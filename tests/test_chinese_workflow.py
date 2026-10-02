import json
import subprocess
import sys
import unittest

import test_story as base

DRAFT, TOOL, plan, story = base.DRAFT, base.TOOL, base.plan, base.story


class ChineseCountTests(unittest.TestCase):
    def test_counts_are_characters_with_explicit_scope(self):
        text = "\ufeff# 标题\r\n导语：甲，乙！A12🙂𠀀〇。\u200b\n"
        self.assertEqual(story.manuscript_counts(text), {
            "visible_nonspace_v1": 14, "visible_nonspace_v2": 14,
            "letters_numbers_v1": 9, "han_v1": 6})
        self.assertEqual(story.manuscript_counts(text, True), {
            "visible_nonspace_v1": 16, "visible_nonspace_v2": 16,
            "letters_numbers_v1": 11, "han_v1": 8})

    def test_attached_marks_and_variation_selectors_do_not_create_extra_visible_characters(self):
        text = "第1章 雨夜\n甲\ufe0f\u0301。\u20dd，乙"
        self.assertEqual(story.manuscript_counts(text), {
            "visible_nonspace_v1": 7, "visible_nonspace_v2": 4,
            "letters_numbers_v1": 2, "han_v1": 2})
        self.assertEqual(story.manuscript_counts(text, True)["visible_nonspace_v2"], 9)
        self.assertEqual(story.visible_count("甲\ufe0f乙"), 3)  # Frozen v1 publishing manifests.
        forged = "第1章 雨夜\n" + "雨" * 2399 + "\ufe0f"
        result = story.lint_text(forged, story.TEMPLATES["plan"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["length_count"], 2399)
        self.assertIn("length", {item["code"] for item in result["errors"]})
        legacy = story.lint_text(forged, {**story.TEMPLATES["plan"],
                                          "count_method": "visible_nonspace_v1"})
        self.assertEqual(legacy["length_count"], 2400)
        self.assertIn("invisible_padding", {item["code"] for item in legacy["errors"]})

    def test_named_blank_glyphs_do_not_pad_v2_or_legacy_chapter_minimum(self):
        # These are specific Unicode blank/filler glyphs, not all symbols or
        # non-Chinese text. Historical v1 counts must remain byte-compatible.
        for char in "\u115f\u1160\u2800\u3164\uffa0":
            with self.subTest(codepoint=f"U+{ord(char):04X}"):
                text = "第1章 雨夜\n" + "雨" * 2399 + char
                counts = story.manuscript_counts(text)
                self.assertEqual(counts["visible_nonspace_v1"], 2400)
                self.assertEqual(counts["visible_nonspace_v2"], 2399)
                checked = story.lint_text(text, story.TEMPLATES["plan"])
                self.assertEqual(checked["length_count"], 2399)
                self.assertIn("length", {issue["code"] for issue in checked["errors"]})
                legacy = story.lint_text(text, {**story.TEMPLATES["plan"],
                                                "count_method": "visible_nonspace_v1"})
                self.assertEqual(legacy["length_count"], 2400)
                self.assertIn("invisible_padding", {issue["code"] for issue in legacy["errors"]})
        meaningful = "甲한가⠁🙂。"
        self.assertEqual(story.manuscript_counts(meaningful)["visible_nonspace_v2"], len(meaningful))
        # A standalone Hangul vowel can use a filler for layout; the vowel
        # remains counted even though its non-rendering leading filler does not.
        self.assertEqual(story.manuscript_counts("\u115f\u1161")["visible_nonspace_v2"], 1)

    def test_legacy_letters_numbers_cannot_use_hangul_filler_to_pad_minimum(self):
        text = "第1章 雨夜\n" + "雨" * 2399 + "\u3164"
        checked = story.lint_text(text, {**story.TEMPLATES["plan"],
                                         "count_method": "letters_numbers_v1"})
        self.assertEqual(checked["length_count"], 2400)
        self.assertIn("invisible_padding", {issue["code"] for issue in checked["errors"]})

    def test_only_unchanged_saved_v1_body_keeps_its_historical_minimum(self):
        legacy = {**story.TEMPLATES["plan"], "title": "雨夜",
                  "count_method": "visible_nonspace_v1"}
        for suffix in ("\ufe0f", "\u3164"):
            text = "第1章 雨夜\n" + "雨" * 2399 + suffix
            with self.subTest(suffix=suffix):
                unchanged = story.lint_text(text, legacy, 1, previous_text=text)
                self.assertTrue(unchanged["ok"])
                self.assertEqual(unchanged["length_count"], 2400)
                self.assertEqual(unchanged["counts"]["visible_nonspace_v2"], 2399)
                new = story.lint_text(text, legacy, 1)
                changed = story.lint_text(text.replace("雨雨", "雪雨", 1), legacy,
                                          previous_text=text)
                for checked in (new, changed):
                    self.assertIn("invisible_padding", {item["code"] for item in checked["errors"]})
                selected_v2 = story.lint_text(text, {**legacy, "count_method": "visible_nonspace_v2"},
                                              previous_text=text)
                self.assertIn("length", {item["code"] for item in selected_v2["errors"]})

    def test_selected_count_controls_gate_and_legacy_plan_is_compatible(self):
        text = "# 标题\n甲，乙！A12🙂𠀀〇。"
        legacy = plan(length=[11, 11])
        self.assertTrue(story.lint_text(text, legacy)["ok"])
        selected = plan(length=[4, 4], count_method="han_v1")
        checked = story.lint_text(text, selected)
        self.assertTrue(checked["ok"])
        self.assertEqual(checked["length_count"], 4)
        self.assertEqual(checked["visible_chars"], 11)
        self.assertFalse(story.lint_text(text, {**selected, "count_title": True})["ok"])

    def test_ambiguous_or_invalid_count_configuration_is_rejected(self):
        for fields in ({"count_method": "platform_words"}, {"count_title": "false"}):
            with self.assertRaises(story.StoryError):
                story.valid_plan(plan(**fields))


class ChineseWorkflowTests(unittest.TestCase):
    setUp = base.StoryTests.setUp
    tearDown = base.StoryTests.tearDown
    delta = base.StoryTests.delta
    assert_error = base.StoryTests.assert_error

    def test_blank_filler_padding_cannot_be_committed_as_new_default_chapter(self):
        current = plan(length=[2400, 2800], count_method="visible_nonspace_v2")
        current.pop("length_exception")
        self.book.save_plan(1, current, self.book.meta("revision"))
        quote = "沈禾把唯一的钥匙交给守门人。"
        text = "第1章 门后的雨\n" + quote + "\u3164" * (2400 - len(quote))
        self.draft.write_bytes(text.encode("utf-8"))
        before_revision = self.book.meta("revision")
        result = self.book.lint(1, self.draft)
        self.assertEqual(result["length_count"], len(quote))
        self.assertIn("length", {issue["code"] for issue in result["errors"]})
        error = self.assert_error("lint_failed", self.book.commit, 1, self.draft, self.delta(text))
        self.assertIn("length", {issue["code"] for issue in error.details["lint"]["errors"]})
        self.assertEqual(self.book.meta("revision"), before_revision)
        self.assertEqual(self.book.meta("last_chapter"), 0)
        self.assertFalse((self.root / self.book.chapter_path(1)).exists())

        # A legitimately excepted v1 plan retains its old published count,
        # but that count alone cannot authorize a padded new chapter.
        self.book.save_plan(1, plan(length=[2400, 2800], count_method="visible_nonspace_v1"),
                            self.book.meta("revision"))
        legacy_check = self.book.lint(1, self.draft)
        self.assertEqual(legacy_check["length_count"], 2400)
        self.assertIn("invisible_padding", {issue["code"] for issue in legacy_check["errors"]})
        self.assert_error("lint_failed", self.book.commit, 1, self.draft, self.delta(text))
        self.assertEqual(self.book.meta("last_chapter"), 0)

    def test_prepare_is_read_only_and_cannot_be_mistaken_for_completed_review(self):
        before = self.book.status()
        result = self.book.prepare(1, self.draft)
        self.assertEqual(result["delta"]["book_id"], before["id"])
        self.assertEqual(result["delta"]["base_revision"], before["revision"])
        self.assertEqual(result["delta"]["review"]["draft_sha256"], story.digest(DRAFT))
        self.assertFalse(result["ready_to_commit"])
        self.assert_error("placeholder", self.book.commit, 1, self.draft, result["delta"])
        self.assertEqual(self.book.status(), before)

    def test_prepare_cli_and_stale_draft_review(self):
        result = subprocess.run([sys.executable, "-B", str(TOOL), "prepare", "--book", str(self.root),
                                 "--chapter", "1", "--draft", str(self.draft)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        prepared = json.loads(result.stdout)["delta"]
        reviewed = self.delta()
        reviewed.update(book_id=prepared["book_id"], base_revision=prepared["base_revision"])
        reviewed["review"]["draft_sha256"] = prepared["review"]["draft_sha256"]
        self.draft.write_bytes((DRAFT + "她停住。").encode("utf-8"))
        self.assert_error("stale_review", self.book.commit, 1, self.draft, reviewed)

    def test_prepare_applies_budget_to_its_own_returned_packet(self):
        self.book.save_plan(1, plan(goal="进门", stop="门内", constraints=[], requires=[], tags=[]), 2)
        self.book.save_notes([{**base.card(), "status": "resolved"}], 3)
        self.assertLess(self.book.context(1, 850)["budget"]["used"], 850)
        self.assert_error("budget_exceeded", self.book.prepare, 1, self.draft, budget=850)
        result = self.book.prepare(1, self.draft, budget=3000)
        self.assertEqual(result["budget"]["used"], len(story.dumps(result).encode("utf-8")))

    def test_prepare_reconciliation_binds_external_bytes(self):
        self.book.commit(1, self.draft, self.delta())
        outside = self.root / self.book.chapter_path(1)
        outside.write_bytes((DRAFT + "她停住。\r\n").encode("utf-8"))
        prepared = self.book.prepare(1, outside, reconcile=True)
        self.assertEqual(prepared["mode"], "reconcile_last")
        self.assertEqual(prepared["delta"]["external_sha256"], story.hashlib.sha256(outside.read_bytes()).hexdigest())
        self.assert_error("exports_unresolved", self.book.prepare, 1, outside)

    def test_maximum_valid_quote_commits_without_late_source_limit_failure(self):
        quote = "甲" * 1200
        text = "第1章 字段上限\n" + quote
        self.draft.write_bytes(text.encode("utf-8"))
        self.book.save_plan(1, plan(title="字段上限", length=[1200, 1200], count_method="han_v1"), 2)
        delta = self.delta(text)
        delta["review"]["checks"] = {key: {"note": "上限字段的事务回归夹具。", "quote": quote} for key in story.CHECKS}
        delta["changes"] = [{"id": "hero", "text": "字段上限夹具", "quote": quote}]
        self.assertTrue(self.book.commit(1, self.draft, delta)["exports_complete"])
        self.assertEqual(self.book.cards()["hero"]["source"], "chapter:1 quote:" + quote)


if __name__ == "__main__":
    unittest.main()
