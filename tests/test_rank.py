"""Research evidence and filesystem boundary tests; no live-site assertions."""
import argparse
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("rank", ROOT / "skills/story-skill/scripts/story_rank.py")
rank = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rank)


def entry(book_id="a", category="玄幻", value=None):
    return {"book_id": book_id, "book_url": f"https://example.com/books/{book_id}",
            "title": "同名作品", "author": "作者", "rank": 1, "category": category,
            "tags": [], "word_count": None, "status": None, "metric_value": value,
            "metric_raw": None if value is None else str(value)}


def snapshot(entries=None):
    data = rank.template()
    data["platform"] = "platform-a"
    data["source"].update({"url": "https://example.com/rank", "board": "阅读榜",
                           "sample_method": "人工读取前 20 项", "limitations": ["仅采样首屏"],
                           "metric": {"name": "阅读人数", "unit": "人", "window": "24h"}})
    data["entries"] = entries if entries is not None else [entry()]
    return data


class SnapshotValidationTests(unittest.TestCase):
    def test_template_is_valid_without_making_up_metrics(self):
        result = rank.validate_snapshot(rank.template())
        self.assertIsNone(result["source"]["metric"])
        self.assertIsNone(result["entries"][0]["metric_value"])

    def test_requires_identity_explicit_metadata_and_timezone(self):
        missing_id = snapshot()
        missing_id["entries"][0].update(book_id=None, book_url=None)
        missing_field = snapshot()
        del missing_field["entries"][0]["word_count"]
        naive_time = snapshot()
        naive_time["collected_at"] = "2026-10-22T12:00:00"
        unknown_field = snapshot()
        unknown_field["entires"] = []
        for data in (missing_id, missing_field, naive_time, unknown_field):
            with self.subTest(data=data), self.assertRaises(rank.RankError):
                rank.validate_snapshot(data)

    def test_rejects_invalid_numbers_and_rank_only_numeric_values(self):
        for value in (True, -1, float("nan"), float("inf"), "12", 10**400):
            with self.subTest(value=value), self.assertRaises(rank.RankError):
                rank.validate_snapshot(snapshot([entry(value=value)]))
        for field, value in (("rank", 0), ("rank", True), ("word_count", -1), ("word_count", 1.2)):
            data = snapshot()
            data["entries"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(rank.RankError):
                rank.validate_snapshot(data)
        data = snapshot([entry(value=0)])
        data["source"]["metric"] = None
        with self.assertRaisesRegex(rank.RankError, "source.metric"):
            rank.validate_snapshot(data)

    def test_bounds_pages_before_building_coverage_range(self):
        data = snapshot()
        data["source"]["pages"] = [10**12]
        with self.assertRaisesRegex(rank.RankError, "页码"):
            rank.validate_snapshot(data)

    def test_manual_snapshots_also_reject_obfuscated_titles_and_authors(self):
        for field in ("title", "author"):
            data = snapshot()
            data["entries"][0][field] = "样本\ue123"
            with self.subTest(field=field), self.assertRaisesRegex(rank.RankError, "私用区"):
                rank.validate_snapshot(data)
        data = snapshot()
        data["platform"] = "bad\ud800"
        with self.assertRaises(rank.RankError):
            rank.validate_snapshot(data)

    def test_url_normalization_keeps_identity_query_but_removes_tracking(self):
        self.assertEqual(rank.normalize_url("https://EXAMPLE.com:443/books/a/?id=3&utm_source=x#top"),
                         "https://example.com/books/a?id=3")
        self.assertNotEqual(rank.normalize_url("https://example.com/book?id=3"),
                            rank.normalize_url("https://example.com/book?id=4"))
        for url in ("file:///book", "https://user:secret@example.com/book", "//example.com/book", "https://example.com/a b"):
            with self.subTest(url=url), self.assertRaises(rank.RankError):
                rank.normalize_url(url)


class RankAggregateTests(unittest.TestCase):
    def test_identical_input_and_order_are_idempotent(self):
        first = snapshot([entry("a"), entry("b")])
        reordered = deepcopy(first)
        reordered["entries"].reverse()
        expected = rank.aggregate_snapshots([first])
        self.assertEqual(rank.aggregate_snapshots([first, first, reordered]), expected)

    def test_same_day_recapture_has_no_extra_weight(self):
        first = snapshot([entry("a", value=1), entry("b", value=9)])
        second = deepcopy(first)
        second["collected_at"] = "2026-10-22T18:00:00+08:00"
        second["entries"] = [entry("a", value=1)]
        report = rank.aggregate_snapshots([first, second])
        self.assertEqual(report["counts"], {"snapshots": 2, "groups": 1, "books": 2})
        group = report["groups"][0]
        self.assertEqual(group["sample_count"], 2)
        self.assertEqual(group["metric_median"], 5)
        self.assertEqual(group["duplicate_observations"], 1)
        self.assertEqual(report, rank.aggregate_snapshots([second, first]))

    def test_same_title_different_ids_or_platforms_remain_distinct(self):
        first = snapshot([entry("a"), entry("b")])
        second = snapshot()
        second["platform"] = "platform-b"
        report = rank.aggregate_snapshots([first, second])
        self.assertEqual(report["counts"]["books"], 3)
        self.assertEqual(report["counts"]["groups"], 2)

    def test_url_only_identity_can_join_unique_id_but_not_title(self):
        first = snapshot()
        second = deepcopy(first)
        second["collected_at"] = "2026-10-22T18:00:00+08:00"
        second["entries"][0].update(book_id=None, book_url="https://EXAMPLE.com/books/a/?utm_campaign=test#top")
        report = rank.aggregate_snapshots([first, second])
        self.assertEqual(report["counts"]["books"], 1)
        self.assertEqual(report["groups"][0]["sample_count"], 1)
        second["entries"][0]["book_url"] = "https://example.com/other/a"
        self.assertEqual(rank.aggregate_snapshots([first, second])["counts"]["books"], 2)

    def test_shared_url_with_conflicting_ids_does_not_force_merge(self):
        data = snapshot([entry("a"), entry("b")])
        data["entries"][1]["book_url"] = data["entries"][0]["book_url"]
        report = rank.aggregate_snapshots([data])
        self.assertEqual(report["counts"]["books"], 2)
        self.assertIn("identity_conflict", {warning["code"] for warning in report["warnings"]})
        self.assertEqual(report["groups"][0]["sample_count"], 0)
        self.assertEqual(report["groups"][0]["excluded_identity_conflicts"], 2)
        self.assertEqual(len(report["groups"][0]["identity_conflicted_book_keys"]), 2)
        self.assertTrue(all(book["observations"] for book in report["books"]))

    def test_metrics_windows_dates_and_cohorts_are_separate(self):
        samples = [snapshot([entry(value=10)]) for _ in range(5)]
        samples[1]["source"]["metric"]["name"] = "点击量"
        samples[2]["source"]["metric"]["window"] = "7d"
        samples[3]["collected_at"] = "2026-10-23T12:00:00+08:00"
        samples[4]["source"]["cohort"] = "new"
        report = rank.aggregate_snapshots(samples)
        self.assertEqual(report["counts"]["groups"], 5)
        self.assertEqual([group["sample_count"] for group in report["groups"]], [1] * 5)

    def test_sampling_methods_are_separate_including_import_with_unknown_capture_time(self):
        samples = [snapshot() for _ in range(3)]
        for sample, method in zip(samples, ("top_n; public_http_get", "top_n; browser_html_import", "人工等额抽样")):
            sample["source"]["sample_method"] = method
        self.assertEqual(rank.aggregate_snapshots(samples)["counts"]["groups"], 3)

    def test_unknown_is_not_zero_and_missing_category_is_separate(self):
        report = rank.aggregate_snapshots([snapshot([entry("a", value=0), entry("b", value=10),
                                                     entry("c", category=None)])])
        group = report["groups"][0]
        self.assertEqual((group["metric_valid_n"], group["metric_missing_n"], group["metric_median"]), (2, 1, 5))
        missing = next(row for row in group["categories"] if row["category"] is None)
        self.assertEqual(missing["sample_count"], 1)
        self.assertAlmostEqual(missing["sample_share"], 1 / 3)
        self.assertIsNone(missing["metric_median"])
        self.assertIn("分类缺失", rank.render_report(report))

    def test_conflict_retains_both_values_and_excludes_them_from_statistics(self):
        first = snapshot([entry("a", value=1), entry("b", value=9)])
        second = snapshot([entry("a", value=100)])
        second["collected_at"] = "2026-10-22T18:00:00+08:00"
        report = rank.aggregate_snapshots([first, second])
        group = report["groups"][0]
        self.assertEqual((group["sample_count"], group["excluded_conflicts"], group["metric_median"]), (1, 1, 9))
        book = next(book for book in report["books"] if book["identity"]["value"] == "a")
        self.assertEqual(sorted(row["metric_value"] for row in book["observations"]), [1, 100])
        self.assertTrue(all(row["conflict"] and row["source_ids"] for row in book["observations"]))

    def test_different_boards_identical_titles_and_ids_are_warned_not_merged(self):
        first = snapshot()
        second = deepcopy(first)
        second["source"]["board"] = "热门榜"
        report = rank.aggregate_snapshots([first, second])
        self.assertEqual(report["counts"]["groups"], 2)
        self.assertIn("duplicate_content", {warning["code"] for warning in report["warnings"]})

    def test_coverage_checks_page_gaps_and_unverified_total(self):
        data = snapshot([entry("a"), entry("b")])
        data["source"].update(coverage="complete", expected_count=1, pages=[1, 3])
        codes = {warning["code"] for warning in rank.aggregate_snapshots([data])["warnings"]}
        self.assertEqual(codes, {"unverified_complete", "count_mismatch", "page_gap"})
        data["source"].update(expected_count=2, pages=[1])
        self.assertEqual(rank.aggregate_snapshots([data])["warnings"], [])


class RankPatternTests(unittest.TestCase):
    def test_identity_ambiguity_cannot_create_fake_patterns_or_change_statistic_denominators(self):
        books = [entry("a", "玄幻", 100), entry("b", "玄幻", 100), entry("c", "都市", 7)]
        for book, title in zip(books, ("重生归来甲", "重生归来乙", "远山独行")):
            book.update(title=title, tags=["复仇"] if book["book_id"] != "c" else ["成长"],
                        word_count=900000 if book["book_id"] != "c" else 0)
        books[1]["book_url"] = books[0]["book_url"]
        data = snapshot(books)
        report = rank.aggregate_snapshots([data])
        group = report["groups"][0]
        sample = rank.build_sample([data])
        self.assertEqual(report["counts"]["books"], 3)
        self.assertEqual((group["unique_books"], group["sample_count"], group["excluded_identity_conflicts"]), (3, 1, 2))
        self.assertEqual((group["metric_valid_n"], group["metric_median"]), (1, 7))
        self.assertEqual([(row["category"], row["sample_count"], row["sample_share"]) for row in group["categories"]],
                         [("都市", 1, 1.0)])
        self.assertEqual([(row["tag"], row["book_count"], row["sample_share"]) for row in group["tags"]["rows"]],
                         [("成长", 1, 1.0)])
        self.assertEqual(group["title_patterns"]["rows"], [])
        self.assertEqual((group["word_counts"]["valid_n"], group["word_counts"]["zero_n"], group["word_counts"]["median"]),
                         (1, 1, 0))
        self.assertEqual((sample["counts"]["group_accepted"], sample["counts"]["eligible"], sample["counts"]["selected"]),
                         (1, 1, 1))
        self.assertEqual(sample["counts"]["excluded_identity_conflicts"], 2)
        self.assertEqual(sample["books"][0]["identity"]["value"], "c")
        self.assertEqual(rank.validate_sample(sample), sample)
        self.assertTrue(all(book["observations"][0]["source_ids"] for book in report["books"]))
        self.assertIn("另排除身份歧义 2", rank.render_report(report))

    def test_identity_and_observation_exclusion_counts_do_not_overlap(self):
        first = snapshot([entry("a"), entry("b"), entry("c")])
        first["entries"][1]["book_url"] = first["entries"][0]["book_url"]
        second = deepcopy(first)
        second["collected_at"] = "2026-10-22T18:00:00+08:00"
        second["entries"] = [deepcopy(first["entries"][0])]
        second["entries"][0]["title"] = "改名观测"
        group = rank.aggregate_snapshots([first, second])["groups"][0]
        self.assertEqual((group["sample_count"], group["excluded_conflicts"], group["excluded_identity_conflicts"]), (1, 1, 1))
        self.assertFalse(set(group["conflicted_book_keys"]) & set(group["identity_conflicted_book_keys"]))
        sample = rank.build_sample([first, second])
        self.assertEqual((sample["counts"]["group_accepted"], sample["counts"]["excluded_conflicts"],
                          sample["counts"]["excluded_identity_conflicts"]), (1, 1, 1))

    def test_tags_count_books_not_repeated_observations_and_report_missing(self):
        first = snapshot([entry("a"), entry("b"), entry("c")])
        first["entries"][0]["tags"] = ["复仇", "成长", "复仇"]
        first["entries"][1]["tags"] = ["成长"]
        second = deepcopy(first)
        second["collected_at"] = "2026-10-22T18:00:00+08:00"
        second["entries"] = [deepcopy(first["entries"][0])]
        report = rank.aggregate_snapshots([first, second])
        tags = report["groups"][0]["tags"]
        self.assertEqual((tags["tagged_n"], tags["missing_n"], tags["distinct_n"]), (2, 1, 2))
        self.assertEqual([(row["tag"], row["book_count"]) for row in tags["rows"]], [("成长", 2), ("复仇", 1)])
        self.assertAlmostEqual(tags["rows"][0]["sample_share"], 2 / 3)
        self.assertEqual(len(tags["rows"][0]["book_keys"]), 2)

    def test_conflicts_are_excluded_from_tags_titles_and_words(self):
        first = snapshot([entry("a"), entry("b")])
        first["entries"][0].update(title="重生归来甲", tags=["复仇"], word_count=100)
        first["entries"][1].update(title="重生归来乙", tags=["复仇"], word_count=0)
        second = deepcopy(first)
        second["collected_at"] = "2026-10-22T18:00:00+08:00"
        second["entries"] = [deepcopy(first["entries"][0])]
        second["entries"][0]["word_count"] = 200
        group = rank.aggregate_snapshots([first, second])["groups"][0]
        self.assertEqual(group["tags"]["rows"][0]["book_count"], 1)
        self.assertEqual(group["title_patterns"]["rows"], [])
        self.assertEqual((group["word_counts"]["valid_n"], group["word_counts"]["median"]), (1, 0))

    def test_title_candidates_are_literal_normalized_and_evidence_deduplicated(self):
        books = [entry("a"), entry("b"), entry("c")]
        for book, title in zip(books, ("重生归来甲：今日", "重生归来乙:明日", "重生之后丙")):
            book["title"] = title
        report = rank.aggregate_snapshots([snapshot(books + [deepcopy(books[0])])])
        patterns = report["groups"][0]["title_patterns"]
        rows = {(row["kind"], row["pattern"]): row for row in patterns["rows"]}
        self.assertEqual(rows[("prefix", "重生")]["book_count"], 3)
        self.assertEqual(rows[("prefix", "重生归来")]["book_count"], 2)
        self.assertEqual(rows[("structure", "含冒号")]["book_count"], 2)
        self.assertIn("NFKC", patterns["method"])
        self.assertIn("不是中文分词", patterns["method"])
        self.assertEqual(report["books"][0]["observations"][0]["title"],
                         next(book["title"] for book in books if book["book_id"] == report["books"][0]["identity"]["value"]))

    def test_redundant_title_boundaries_collapse_only_identical_book_sets(self):
        books = [entry("a"), entry("b")]
        books[0]["title"], books[1]["title"] = "重生归来甲", "重生归来乙"
        rows = rank.aggregate_snapshots([snapshot(books)])["groups"][0]["title_patterns"]["rows"]
        prefixes = [row["pattern"] for row in rows if row["kind"] == "prefix"]
        self.assertEqual(prefixes, ["重生归来"])

    def test_word_counts_distinguish_unknown_and_true_zero(self):
        books = [entry("a"), entry("b"), entry("c")]
        books[0]["word_count"], books[1]["word_count"] = 0, 200000
        words = rank.aggregate_snapshots([snapshot(books)])["groups"][0]["word_counts"]
        self.assertEqual((words["valid_n"], words["missing_n"], words["zero_n"], words["median"]), (2, 1, 1, 100000))
        self.assertEqual((words["min"], words["max"]), (0, 200000))
        self.assertEqual(sum(row["book_count"] for row in words["bands"]), 3)
        empty = rank.aggregate_snapshots([snapshot([])])["groups"][0]
        self.assertEqual(empty["title_patterns"]["rows"], [])
        self.assertIsNone(empty["word_counts"]["median"])

    def test_tag_and_title_display_limits_are_explicit(self):
        books = [entry(str(i)) for i in range(80)]
        for i, book in enumerate(books):
            book.update(title=f"组{i // 2:02d}标题{i % 2}尾{i // 2:02d}", tags=[f"标签{i:02d}"])
        group = rank.aggregate_snapshots([snapshot(books)])["groups"][0]
        self.assertEqual((len(group["tags"]["rows"]), group["tags"]["truncated_n"]), (50, 30))
        self.assertEqual(len(group["title_patterns"]["rows"]), 30)
        self.assertEqual(group["title_patterns"]["candidate_n"], 30 + group["title_patterns"]["truncated_n"])
        self.assertGreater(group["title_patterns"]["truncated_n"], 0)

    def test_distinct_tag_work_is_bounded_before_building_full_report(self):
        book = entry()
        book["tags"] = ["标签一", "标签二", "标签三"]
        with patch.object(rank, "MAX_DISTINCT_TAGS", 2), self.assertRaisesRegex(rank.RankError, "不同标签超过"):
            rank.aggregate_snapshots([snapshot([book])])


class RankSampleTests(unittest.TestCase):
    def diverse(self):
        books = [entry("a", "玄幻"), entry("b", "都市"), entry("c", "玄幻"), entry("d", "都市")]
        for i, book in enumerate(books):
            book.update(rank=i + 1, title=f"开局{i}新世界")
        books[0].update(tags=["成长", "复仇"], word_count=0)
        books[1].update(tags=["成长"], word_count=200000)
        books[2].update(tags=["复仇"], word_count=500000)
        books[3].update(tags=[], word_count=None)
        return snapshot(books)

    def test_deterministic_diversity_and_recomputable_provenance(self):
        data = self.diverse()
        result = rank.build_sample([data], limit=3)
        reversed_data = deepcopy(data)
        reversed_data["entries"].reverse()
        self.assertEqual(result, rank.build_sample([reversed_data, data], limit=3))
        self.assertEqual([book["identity"]["value"] for book in result["books"]], ["a", "b", "c"])
        self.assertEqual(result["counts"]["eligible"], 4)
        self.assertEqual(result["counts"]["selected"], 3)
        self.assertEqual(len(result["sample_id"]), 64)
        self.assertEqual(rank.validate_sample(json.loads(json.dumps(result))), result)
        source_ids = {source["source_id"] for source in result["sources"]}
        for book in result["books"]:
            self.assertTrue(book["reasons"])
            self.assertTrue(set(book["observation"]["source_ids"]).issubset(source_ids))
            self.assertFalse(book["observation"]["conflict"])
        self.assertIn("不是随机抽样", rank.render_sample(result))

    def test_repeated_tags_beyond_display_limit_affect_sampling_but_display_limit_does_not(self):
        books = [entry(str(i)) for i in range(1, 6)]
        for i, book in enumerate(books):
            book.update(rank=i + 1, word_count=100000,
                        tags=[f"常见标签{j:02d}" for j in range(50)] if i < 3 else ["另一重复标签"])
        data = snapshot(books)
        group = rank.aggregate_snapshots([data])["groups"][0]
        self.assertEqual((len(group["tags"]["rows"]), group["tags"]["truncated_n"]), (50, 1))
        self.assertNotIn("另一重复标签", {row["tag"] for row in group["tags"]["rows"]})
        result = rank.build_sample([data], limit=2)
        self.assertEqual([book["identity"]["value"] for book in result["books"]], ["1", "4"])
        self.assertIn("补充本组重复标签：另一重复标签", result["books"][1]["reasons"])
        for display_limit in (0, 1, 51, 80):
            with self.subTest(display_limit=display_limit), patch.object(rank, "MAX_TAG_ROWS", display_limit):
                self.assertEqual(rank.build_sample([data], limit=2), result)
                self.assertEqual(rank.validate_sample(result), result)

    def test_sampling_tag_repetition_uses_full_selected_group_before_filters(self):
        books = [entry(key) for key in ("a", "b", "c", "d")]
        for i, book in enumerate(books):
            book.update(rank=i + 1, title="命中作品" if i < 3 else "筛除作品",
                        tags=["常见标签"] if i < 2 else ["另一重复标签"])
        result = rank.build_sample([snapshot(books)], title_contains="命中", limit=2)
        self.assertEqual((result["counts"]["group_accepted"], result["counts"]["eligible"]), (4, 3))
        self.assertEqual([book["identity"]["value"] for book in result["books"]], ["a", "c"])
        self.assertIn("补充本组重复标签：另一重复标签", result["books"][1]["reasons"])

    def test_excluded_or_duplicate_observations_do_not_promote_unique_sampling_tags(self):
        books = [entry(key) for key in ("a", "b", "c")]
        for i, book in enumerate(books):
            book.update(rank=i + 1, tags=["共有标签"] if i < 2 else ["孤例标签"])
        first = snapshot(books)
        duplicate = deepcopy(first)
        duplicate["collected_at"] = "2026-10-22T18:00:00+08:00"
        duplicate["entries"] = [deepcopy(books[2]), deepcopy(books[2])]
        conflicted = entry("d")
        conflicted.update(rank=4, tags=["孤例标签"])
        changed = deepcopy(conflicted)
        changed["title"] = "冲突观测"
        ambiguous = deepcopy(conflicted)
        ambiguous["book_id"] = "e"
        another_group = snapshot([conflicted])
        another_group["source"]["board"] = "另一榜单"
        cases = {
            "duplicate": [first, duplicate],
            "observation_conflict": [snapshot(books + [conflicted, changed])],
            "identity_conflict": [snapshot(books + [conflicted, ambiguous])],
            "another_group": [first, another_group],
        }
        group_id = rank.aggregate_snapshots([first])["groups"][0]["group_id"]
        for case, data in cases.items():
            with self.subTest(case=case):
                result = rank.build_sample(data, group_id=group_id, limit=2)
                self.assertEqual([book["identity"]["value"] for book in result["books"]], ["a", "b"])
                self.assertEqual(result["counts"]["group_accepted"], 3)

    def test_filters_are_exact_tags_and_literal_title_with_inclusive_word_bounds(self):
        data = self.diverse()
        result = rank.build_sample([data], category="玄幻", tags=["成长", "复仇"], title_contains="开局0", min_words=0, max_words=0)
        self.assertEqual([book["identity"]["value"] for book in result["books"]], ["a"])
        result = rank.build_sample([data], min_words=200000, max_words=500000)
        self.assertEqual({book["identity"]["value"] for book in result["books"]}, {"b", "c"})
        result = rank.build_sample([data], max_words=0)
        self.assertEqual(result["counts"]["eligible"], 1)
        for filters in ({"category": "玄"}, {"tags": ["成"]}, {"title_contains": "开局.*"}, {"min_words": 600000}):
            with self.subTest(filters=filters), self.assertRaisesRegex(rank.RankError, "没有可抽样"):
                rank.build_sample([data], **filters)

    def test_multiple_groups_need_explicit_selection_and_do_not_mix(self):
        first, second = self.diverse(), self.diverse()
        second["platform"] = "platform-b"
        with self.assertRaisesRegex(rank.RankError, "多个可比分组"):
            rank.build_sample([first, second])
        group = rank.aggregate_snapshots([first, second])["groups"][0]
        result = rank.build_sample([first, second], group_id=group["group_id"])
        self.assertEqual({book["identity"]["platform"] for book in result["books"]}, {group["dimensions"]["platform"]})
        self.assertEqual(len(result["snapshots"]), 2)
        with self.assertRaisesRegex(rank.RankError, "未知分组"):
            rank.build_sample([first], group_id="missing")

    def test_sample_identity_dedup_uses_id_and_url_instead_of_title(self):
        first = snapshot([entry("a"), entry("a"), entry("b")])
        second = snapshot([entry("a")])
        second["collected_at"] = "2026-10-22T18:00:00+08:00"
        second["entries"][0]["book_id"] = None
        sample = rank.build_sample([first, second])
        self.assertEqual(sample["counts"]["selected"], 2)
        self.assertEqual({book["identity"]["value"] for book in sample["books"]}, {"a", "b"})
        self.assertEqual({book["observation"]["title"] for book in sample["books"]}, {"同名作品"})
        merged = next(book for book in sample["books"] if book["identity"]["value"] == "a")
        self.assertEqual(len(merged["observation"]["source_ids"]), 2)

    def test_observation_and_identity_conflicts_are_excluded(self):
        first = self.diverse()
        first["entries"][1]["book_url"] = first["entries"][0]["book_url"]
        second = deepcopy(first)
        second["collected_at"] = "2026-10-22T18:00:00+08:00"
        second["entries"] = [deepcopy(first["entries"][2])]
        second["entries"][0]["title"] = "不同书名待核实"
        result = rank.build_sample([first, second])
        self.assertEqual([book["identity"]["value"] for book in result["books"]], ["d"])
        self.assertEqual(result["counts"]["excluded_conflicts"], 1)
        self.assertEqual(result["counts"]["excluded_identity_conflicts"], 2)

    def test_sample_validation_detects_modified_selection_identity_or_provenance(self):
        sample = rank.build_sample([self.diverse()])
        mutations = [lambda result: result.update(sample_id="a" * 64),
                     lambda result: result["books"][0].update(book_key="fake"),
                     lambda result: result["books"][0]["identity"].update(value="fake"),
                     lambda result: result["books"][0]["observation"].update(source_ids=[]),
                     lambda result: result["books"][0].update(reasons=["爆款推荐"]),
                     lambda result: result["snapshots"][0]["entries"][0].update(title="改造来源"),
                     lambda result: result["counts"].update(selected=99)]
        for mutate in mutations:
            changed = deepcopy(sample)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaisesRegex(rank.RankError, "重算结果不一致"):
                rank.validate_sample(changed)

    def test_invalid_limits_empty_samples_and_reversed_bounds_are_rejected(self):
        for limit in (0, 21, True, "5"):
            with self.subTest(limit=limit), self.assertRaises(rank.RankError):
                rank.build_sample([self.diverse()], limit=limit)
        with self.assertRaisesRegex(rank.RankError, "不能大于"):
            rank.build_sample([self.diverse()], min_words=10, max_words=1)
        with self.assertRaisesRegex(rank.RankError, "没有可抽样"):
            rank.build_sample([snapshot([])])
        with patch.object(rank, "MAX_OUTPUT_BYTES", 1), self.assertRaisesRegex(rank.RankError, "32 MiB"):
            rank.build_sample([self.diverse()])


class RankCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "sample.json"
        self.input.write_text(json.dumps(snapshot()), encoding="utf-8")
        self.parser = argparse.ArgumentParser()
        rank.register_parser(self.parser.add_subparsers(dest="command", required=True))

    def run_rank(self, *args, **kwargs):
        return rank.run(self.parser.parse_args(args), **kwargs)

    def test_standalone_cli_outputs_utf8_help_and_errors_under_non_utf8_environment(self):
        script = ROOT / "skills/story-skill/scripts/story_rank.py"
        environment = os.environ.copy()
        environment.update(PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
        for arguments, expected_status in ((("--help",), 0),
                (("rank-check", "--input", str(self.root / "missing.json")), 2)):
            with self.subTest(arguments=arguments):
                result = subprocess.run([sys.executable, str(script), *arguments], cwd=self.root,
                                        env=environment, capture_output=True, check=False)
                self.assertEqual(result.returncode, expected_status, result.stderr.decode("utf-8", errors="replace"))
                output = (result.stdout if expected_status == 0 else result.stderr).decode("utf-8")
                self.assertNotIn("UnicodeEncodeError", output)
                if expected_status == 0:
                    self.assertIn("rank-collect", output)
                    self.assertIn("扫榜", output)
                else:
                    self.assertFalse(json.loads(output)["ok"])
                    self.assertIn("JSON", json.loads(output)["error"])

    def test_check_is_read_only_and_aggregate_does_not_initialize_book(self):
        before = self.input.read_bytes()
        checked = self.run_rank("rank-check", "--input", str(self.input))
        self.assertTrue(checked["ok"])
        output = self.root / "result"
        result = self.run_rank("rank-aggregate", "--input", str(self.input), "--input", str(self.input),
                               "--output-dir", str(output))
        self.assertEqual(result["counts"]["snapshots"], 1)
        self.assertEqual({file.name for file in output.iterdir()}, {"report.json", "report.md"})
        self.assertEqual(self.input.read_bytes(), before)
        self.assertFalse(list(self.root.rglob(".story")))

    def test_sample_cli_writes_new_reproducible_bundle_without_book_state(self):
        before = self.input.read_bytes()
        output = self.root / "selection"
        result = self.run_rank("rank-sample", "--input", str(self.input), "--category", "玄幻",
                               "--title-contains", "同名", "--output-dir", str(output))
        self.assertEqual({file.name for file in output.iterdir()}, {"sample.json", "sample.md"})
        sample = rank.validate_sample(json.loads((output / "sample.json").read_text(encoding="utf-8")))
        self.assertEqual(result["sample_id"], sample["sample_id"])
        self.assertEqual(self.input.read_bytes(), before)
        self.assertFalse(list(self.root.rglob(".story")))
        with self.assertRaises(rank.RankError):
            self.run_rank("rank-sample", "--input", str(self.input), "--output-dir", str(output))

    def test_sample_no_match_or_report_json_creates_no_output(self):
        output = self.root / "selection"
        with self.assertRaisesRegex(rank.RankError, "没有可抽样"):
            self.run_rank("rank-sample", "--input", str(self.input), "--tag", "不存在",
                          "--output-dir", str(output))
        self.assertFalse(output.exists())
        self.input.write_text(json.dumps(rank.aggregate_snapshots([snapshot()])), encoding="utf-8")
        with self.assertRaises(rank.RankError):
            self.run_rank("rank-sample", "--input", str(self.input), "--output-dir", str(output))
        self.assertFalse(output.exists())

    def test_existing_output_file_and_directory_are_never_overwritten(self):
        directory = self.root / "existing"
        directory.mkdir()
        sentinel = directory / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        for destination in (directory, self.input):
            with self.subTest(destination=destination), self.assertRaises(rank.RankError):
                self.run_rank("rank-aggregate", "--input", str(self.input), "--output-dir", str(destination))
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_existing_and_broken_output_symlinks_are_never_overwritten(self):
        directory = self.root / "existing"
        directory.mkdir()
        alias = self.root / "alias"
        broken = self.root / "broken"
        try:
            alias.symlink_to(directory, target_is_directory=True)
            broken.symlink_to(self.root / "missing", target_is_directory=True)
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                self.skipTest("Windows did not grant symbolic-link creation privilege")
            raise
        for destination in (alias, broken):
            with self.subTest(destination=destination), self.assertRaises(rank.RankError):
                self.run_rank("rank-aggregate", "--input", str(self.input), "--output-dir", str(destination))
        self.assertTrue(alias.is_symlink())
        self.assertTrue(broken.is_symlink())
        self.assertEqual(list(directory.iterdir()), [])
        self.assertFalse((self.root / "missing").exists())

    def test_invalid_or_oversize_json_does_not_create_output(self):
        output = self.root / "result"
        for value in ('{"schema_version":1,"schema_version":2}', '{"value":NaN}', '{bad json}'):
            self.input.write_text(value, encoding="utf-8")
            with self.subTest(value=value), self.assertRaises(rank.RankError):
                self.run_rank("rank-aggregate", "--input", str(self.input), "--output-dir", str(output))
            self.assertFalse(output.exists())
        self.input.write_text(json.dumps(snapshot()), encoding="utf-8")
        with patch.object(rank, "MAX_INPUT_BYTES", 1), self.assertRaises(rank.RankError):
            self.run_rank("rank-check", "--input", str(self.input))

    def test_local_html_collect_passes_real_adapter_and_writes_evidence(self):
        from tests import test_rank_sources
        html = self.root / "page.html"
        html.write_text(test_rank_sources.page(), encoding="utf-8")
        output = self.root / "collection"
        result = self.run_rank("rank-collect", "--source", "qimao-library", "--pages", "1",
                               "--html", str(html), "--html-url", test_rank_sources.sources.QIMAO_URL.format(page=1),
                               "--output-dir", str(output),
                               collect_qimao=test_rank_sources.sources.collect_qimao)
        self.assertTrue(result["ok"])
        self.assertEqual((output / "raw/page-1.html").read_text(encoding="utf-8"), html.read_text(encoding="utf-8"))
        collected = json.loads((output / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(collected["source"]["method"], "browser_html_import")
        self.assertIsNone(collected["source"]["metric"])
        self.assertEqual(json.loads((output / "report.json").read_text(encoding="utf-8"))["counts"]["books"], 1)
        self.assertFalse(list(self.root.rglob(".story")))

    def test_collect_count_or_source_failure_creates_no_output(self):
        output = self.root / "collection"
        with self.assertRaisesRegex(rank.RankError, "文件数"):
            self.run_rank("rank-collect", "--source", "qimao-library", "--pages", "2",
                          "--html", "one.html", "--output-dir", str(output))
        with self.assertRaisesRegex(rank.RankError, "html-url"):
            self.run_rank("rank-collect", "--source", "qimao-library", "--html", "one.html",
                          "--output-dir", str(output))
        with self.assertRaisesRegex(rank.RankError, "html-url"):
            self.run_rank("rank-collect", "--source", "qimao-library", "--html-url", "https://example.com/",
                          "--output-dir", str(output))
        def blocked(pages, html_files=None, html_urls=None):
            raise ValueError("来源返回 WAF 挑战，停止采集")
        with self.assertRaisesRegex(ValueError, "WAF"):
            self.run_rank("rank-collect", "--source", "qimao-library", "--output-dir", str(output),
                          collect_qimao=blocked)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
