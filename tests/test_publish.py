"""Offline publishing prepares reviewed snapshots without changing writing history."""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


TOOL = Path(__file__).resolve().parents[1] / "skills/story-skill/scripts/story.py"
SPEC = importlib.util.spec_from_file_location("offline_publishing_story", TOOL)
story = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(story)
PUBLISH_SPEC = importlib.util.spec_from_file_location("offline_publishing", TOOL.with_name("story_publish.py"))
publishing = importlib.util.module_from_spec(PUBLISH_SPEC)
PUBLISH_SPEC.loader.exec_module(publishing)
publishing.inject(story)

QUOTE = "沈禾交出钥匙，留下一张收据。"
BODY = QUOTE + "\n守门人让开门口，她终于看见了那本账。\n"


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-publish-")
        self.root = Path(self.temp.name).resolve() / "中文书根"
        story.Book.create(self.root, "门后的账本", "long")
        self.book = story.Book(self.root)
        self.draft = self.root / "draft.md"
        self.ledger = self.root / ".story/publishing.sqlite3"
        self.texts = {}

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def rev(self):
        return self.book.meta("revision")

    def writing_snapshot(self):
        return self.rev(), tuple(self.book.db.iterdump())

    def assert_code(self, code, call, *args, **kwargs):
        with self.assertRaises(story.StoryError) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception.details)
        return caught.exception

    def review(self, text):
        return {"draft_sha256": story.digest(text), "issues": [], "checks": {
            name: {"note": "核对钥匙交接、人物选择及本章停笔位置。", "quote": QUOTE}
            for name in story.CHECKS}}

    def commit(self, chapter=1, text=None, title=None, replace_last=False):
        title = title or f"门后的账本{chapter}"
        text = text if text is not None else f"第{chapter}章 {title}\n" + BODY
        plan = {"title": title, "volume_dir": "第一卷 雨夜", "goal": "完成钥匙交接",
                "stop": "看到账本，不翻开账本", "requires": [], "length": [10, 500],
                "beats": [{"choice": "交出钥匙", "change": "获准入内并保留收据"}]}
        self.book.save_plan(chapter, plan, self.rev())
        self.draft.write_bytes(text.encode("utf-8"))
        delta = {"book_id": self.book.meta("id"), "base_revision": self.rev(),
                 "summary": "沈禾交出钥匙，保留收据并进入门内。", "changes": [],
                 "review": self.review(text), "dependencies": [],
                 "dependency_review": {"complete": True, "note": "本章独立交接，已逐项检查相关事实。"}}
        result = self.book.commit(chapter, self.draft, delta, replace_last=replace_last)
        self.assertTrue(result["exports_complete"], result)
        self.texts[chapter] = text
        return result

    def payload(self, chapters=(1,), **fields):
        return {"platform": "fanqie", "account_id": "author-one", "remote_book_id": "book-one",
                "chapters": list(chapters), "mode": "draft", **fields}

    def prepare(self, chapters=(1,), **fields):
        return publishing.prepare(self.book, self.payload(chapters, **fields), self.rev())

    def inspect(self, plan_id, **fields):
        return publishing.inspect(self.book, plan_id, **fields)

    def publish_reviewed_history(self, chapter=1, text=None):
        """Promote an actual reviewed branch, including an imported baseline."""
        text = text if text is not None else self.texts[chapter]
        if self.book.db.execute("SELECT 1 FROM plans WHERE chapter=?", (chapter,)).fetchone() is None:
            self.book.save_plan(chapter, {
                "volume_dir": "第一卷 雨夜", "goal": "完成钥匙交接", "stop": "看到门内账本",
                "requires": [], "length": [10, 500],
                "beats": [{"choice": "交出钥匙", "change": "保留收据并入内"}]}, self.rev())
        packet = story.history.branch_start(self.book, chapter, self.rev())
        candidates = []
        for affected in packet["affected"]:
            number = affected["chapter"]
            content = text if number == chapter else self.texts[number]
            candidate = {"chapter": number, "text": content, "sha": story.digest(content),
                         "summary": "交接已经完成，沈禾保留收据。", "dependencies": [], "complete": True}
            candidate["review"] = {**self.review(content),
                                   "candidate_sha256": story.history.candidate_fingerprint(candidate)}
            candidates.append(candidate)
        staged = story.history.branch_update(self.book, packet["branch"], {"chapters": candidates}, self.rev())
        semantic = {**staged["review_template"], "note": "已逐段审查所有受影响章节。",
                    "state_review": "本次交接没有遗漏需要回写的人物状态。",
                    "coverage_review": "核对本章与后续依赖，受影响的正文已全部纳入。"}
        story.history.branch_update(self.book, packet["branch"], {"semantic_review": semantic}, self.rev())
        result = story.history.branch_publish(self.book, packet["branch"], self.rev())
        self.assertTrue(result["exports_complete"], result)
        self.texts[chapter] = text
        return result

    def test_empty_list_does_not_create_ledger_or_modify_writing_history(self):
        before = self.writing_snapshot()
        paths = {p.relative_to(self.root).as_posix() for p in self.root.rglob("*")}
        result = publishing.list_plans(self.book)
        self.assertEqual(result["results"], [])
        self.assertEqual(result["total"], 0)
        self.assertIsNone(result["next_offset"])
        self.assertFalse(self.ledger.exists())
        after_paths = {p.relative_to(self.root).as_posix() for p in self.root.rglob("*")}
        self.assertEqual(paths, after_paths)
        self.assertEqual(self.writing_snapshot(), before)

    def test_prepare_freezes_exact_source_and_is_idempotent_without_story_events(self):
        self.commit()
        before = self.writing_snapshot()
        first = self.prepare()
        second = self.prepare()
        self.assertEqual(first["id"], second["id"])
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["status"], "prepared")
        self.assertEqual(first["remote_state"], "unknown")
        self.assertFalse(first["platform_verified"])
        self.assertFalse(first["ready_to_upload"])
        for result in (first, second):
            self.assertTrue(result["source_check_performed"])
            self.assertTrue(result["source_matches_current"])
        manifest = first["manifest"]
        self.assertEqual(manifest["book_id"], self.book.meta("id"))
        self.assertEqual(manifest["source_revision"], self.rev())
        item = manifest["chapters"][0]
        head = story.history._head(self.book, 1)
        self.assertEqual(item["chapter"], 1)
        self.assertEqual(item["head_id"], head["id"])
        self.assertEqual(item["body_sha"], story.digest(self.texts[1]))
        self.assertEqual(item["review_receipt_sha"], story.digest(story.dumps(json.loads(head["receipt"]))))
        self.assertEqual(item["chapter_path"], self.book.chapter_path(1))
        self.assertEqual(item["title"], "门后的账本1")
        self.assertEqual(item["body"], BODY)
        self.assertRegex(item["upload_sha256"], r"^[0-9a-f]{64}$")
        self.assertIsInstance(item["source_revision"], int)
        self.assertEqual(self.inspect(first["id"])["manifest"], manifest)
        self.assertEqual(publishing.list_plans(self.book)["total"], 1)
        self.assertEqual(self.writing_snapshot(), before)

    def test_changed_text_invalidates_plan_without_replacing_its_frozen_body(self):
        self.commit()
        first = self.prepare()
        original = copy.deepcopy(first["manifest"])
        self.commit(text=self.texts[1] + "她把收据仔细收进衣袋。\n", replace_last=True)
        before = self.writing_snapshot()
        checked = publishing.check(self.book, first["id"])
        self.assertFalse(checked["ok"])
        self.assertEqual(checked["status"], "stale")
        self.assertTrue(checked["source_check_performed"])
        self.assertFalse(checked["source_matches_current"])
        self.assertTrue(checked["reasons"])
        self.assertEqual(self.inspect(first["id"])["manifest"], original)
        self.assertEqual(self.writing_snapshot(), before)
        refreshed = self.prepare()
        self.assertNotEqual(refreshed["id"], first["id"])
        self.assertIn("仔细收进衣袋", refreshed["manifest"]["chapters"][0]["body"])

    def test_reused_plan_records_the_new_source_check_time(self):
        self.commit()
        with patch.object(publishing, "_now", return_value="2026-09-23T01:00:00+00:00"):
            first = self.prepare()
        with patch.object(publishing, "_now", return_value="2026-09-23T02:00:00+00:00"):
            second = self.prepare()
        self.assertTrue(second["idempotent"])
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["created"], first["created"])
        self.assertEqual(second["manifest_sha256"], first["manifest_sha256"])
        self.assertEqual(first["checked_at"], "2026-09-23T01:00:00+00:00")
        self.assertEqual(second["checked_at"], "2026-09-23T02:00:00+00:00")
        self.assertEqual(self.inspect(first["id"])["checked_at"], second["checked_at"])

    def test_same_body_new_history_head_also_invalidates_plan(self):
        self.commit()
        first = self.prepare()
        story.history.save_dependencies(self.book, {"chapter": 1,
            "chapter_sha": story.digest(self.texts[1]), "dependencies": [], "complete": True,
            "note": "再次核对依赖，正文维持原样。"}, self.rev())
        self.assertEqual(first["manifest"]["chapters"][0]["body_sha"], story.history._head(self.book, 1)["sha"])
        self.assertNotEqual(first["manifest"]["chapters"][0]["head_id"], story.history._head(self.book, 1)["id"])
        checked = publishing.check(self.book, first["id"])
        self.assertFalse(checked["ok"])
        self.assertEqual(checked["status"], "stale")

    def test_corrupted_review_cannot_reuse_a_valid_body_hash(self):
        self.commit()
        first = self.prepare()
        head = story.history._head(self.book, 1)
        receipt = json.loads(head["receipt"])
        receipt["input"]["review"]["draft_sha256"] = "0" * 64
        # Deliberate corruption: keep body hashes valid while invalidating review evidence.
        self.book.db.execute("UPDATE chapter_state SET receipt=? WHERE chapter=1", (story.dumps(receipt),))
        self.book.db.execute("INSERT INTO history_versions SELECT ?,chapter,sha,summary,?,plan,complete,revision,publication_revision "
                             "FROM history_versions WHERE id=?", ("corrupt-receipt-head", story.dumps(receipt), head["id"]))
        self.book.db.execute("UPDATE history_heads SET version='corrupt-receipt-head' WHERE chapter=1")
        self.book.db.commit()
        before = self.writing_snapshot()
        self.assert_code("review_required", self.prepare)
        checked = publishing.check(self.book, first["id"])
        self.assertFalse(checked["ok"])
        self.assertEqual(checked["status"], "stale")
        self.assertIn("review_required", {reason["code"] for reason in checked["reasons"]})
        self.assertEqual(checked["chapter_checks"][0]["chapter"], 1)
        self.assertFalse(checked["chapter_checks"][0]["matches_current"])
        self.assertEqual(checked["chapter_checks"][0]["error"]["code"], "review_required")
        self.assertEqual(self.writing_snapshot(), before)

    def test_unrelated_notes_do_not_change_selected_head_eligibility(self):
        self.commit()
        first = self.prepare()
        self.book.save_notes([{"id": "elsewhere", "text": "远城有一处渡口。", "source": "作者补充"}], self.rev())
        checked = publishing.check(self.book, first["id"])
        self.assertTrue(checked["ok"])
        self.assertEqual(checked["status"], "prepared")
        self.assertFalse(checked["ready_to_upload"])
        before = self.writing_snapshot()
        repeated = self.prepare()
        self.assertTrue(repeated["idempotent"])
        self.assertTrue(repeated["source_check_performed"])
        self.assertTrue(repeated["source_matches_current"])
        self.assertEqual(repeated["id"], first["id"])
        self.assertEqual(repeated["manifest"], first["manifest"])
        self.assertEqual(repeated["manifest_sha256"], first["manifest_sha256"])
        self.assertEqual(repeated["manifest_sha256"], story.digest(story.dumps(first["manifest"])))
        self.assertLess(repeated["source_revision"], self.rev())
        self.assertEqual(publishing.list_plans(self.book)["total"], 1)
        self.assertEqual(self.writing_snapshot(), before)

    def test_saved_prepared_state_does_not_claim_a_fresh_check_after_revision(self):
        self.commit()
        original = self.prepare()
        self.commit(text=self.texts[1] + "她把账页上那处墨痕记了下来。\n", replace_last=True)
        before = self.writing_snapshot()
        with closing(sqlite3.connect(self.ledger)) as ledger:
            saved = tuple(ledger.iterdump())
        listed = publishing.list_plans(self.book)["results"][0]
        inspected = self.inspect(original["id"])
        recovered = publishing.recover(self.book)
        for result in (listed, inspected, recovered):
            self.assertFalse(result["source_check_performed"])
            self.assertIsNone(result["source_matches_current"])
        for result in (listed, inspected):
            self.assertEqual(result["status"], "prepared")
            self.assertEqual(result["checked_at"], original["checked_at"])
        self.assertEqual(inspected["manifest"], original["manifest"])
        with closing(sqlite3.connect(self.ledger)) as ledger:
            self.assertEqual(tuple(ledger.iterdump()), saved)
        self.assertEqual(self.writing_snapshot(), before)

    def test_stale_batch_recheck_reports_new_changes_to_other_chapters(self):
        self.commit(1)
        self.commit(2)
        original = self.prepare((1, 2))
        self.commit(2, text=self.texts[2] + "她把收据放进衣袋。\n", replace_last=True)
        first = publishing.check(self.book, original["id"])
        self.assertEqual(first["status"], "stale")
        self.assertTrue(first["source_check_performed"])
        self.assertFalse(first["source_matches_current"])
        first_chapters = {row["chapter"]: row for row in first["chapter_checks"]}
        self.assertEqual(set(first_chapters), {1, 2})
        self.assertTrue(first_chapters[1]["matches_current"])
        self.assertEqual(first_chapters[1]["changed_fields"], [])
        self.assertFalse(first_chapters[2]["matches_current"])
        self.assertIn("body_sha", first_chapters[2]["changed_fields"])

        self.publish_reviewed_history(1, self.texts[1] + "她把灯拨亮，认出了账上的旧字。\n")
        before = self.writing_snapshot()
        second = publishing.check(self.book, original["id"])
        self.assertEqual(second["status"], "stale")
        self.assertTrue(second["source_check_performed"])
        self.assertFalse(second["source_matches_current"])
        self.assertNotEqual(second["checked_at"], first["checked_at"])
        self.assertEqual({reason["chapter"] for reason in second["reasons"]
                          if reason["code"] == "source_changed"}, {1, 2})
        self.assertEqual({row["chapter"] for row in second["chapter_checks"]}, {1, 2})
        for row in second["chapter_checks"]:
            self.assertFalse(row["matches_current"])
            self.assertIn("body_sha", row["changed_fields"])
        saved = self.inspect(original["id"])
        self.assertEqual(saved["manifest"], original["manifest"])
        self.assertEqual(saved["checked_at"], second["checked_at"])
        self.assertEqual(saved["reasons"], second["reasons"])
        self.assertFalse(saved["source_check_performed"])
        self.assertIsNone(saved["source_matches_current"])
        self.assertEqual(self.writing_snapshot(), before)

    def test_new_head_and_changed_account_or_target_do_not_reuse_previous_plan(self):
        self.commit()
        original = self.prepare()
        original_hash = original["manifest_sha256"]
        self.book.save_notes([{"id": "elsewhere", "text": "远城仍然下雨。", "source": "作者补充"}], self.rev())
        other_account = self.prepare(account_id="author-two")
        other_target = self.prepare(remote_book_id="book-two")
        self.assertEqual(len({original["id"], other_account["id"], other_target["id"]}), 3)
        story.history.save_dependencies(self.book, {"chapter": 1,
            "chapter_sha": story.digest(self.texts[1]), "dependencies": [], "complete": True,
            "note": "同正文的新一轮依赖审查。"}, self.rev())
        new_head = self.prepare()
        self.assertNotEqual(new_head["id"], original["id"])
        self.assertNotEqual(new_head["manifest"]["chapters"][0]["head_id"],
                            original["manifest"]["chapters"][0]["head_id"])
        self.assertEqual(self.inspect(original["id"])["manifest_sha256"], original_hash)
        self.assertEqual(self.inspect(original["id"])["manifest"], original["manifest"])

    def test_external_edit_and_missing_export_block_prepare_and_check(self):
        self.commit()
        first = self.prepare()
        export = self.root / self.book.chapter_path(1)
        original = export.read_bytes()
        for changed in (b"external changes", None):
            with self.subTest(changed=changed):
                if changed is None:
                    export.unlink()
                else:
                    export.write_bytes(changed)
                before = self.writing_snapshot()
                self.assert_code("exports_unresolved", self.prepare)
                checked = publishing.check(self.book, first["id"])
                self.assertFalse(checked["ok"])
                self.assertEqual(checked["status"], "stale")
                self.assertEqual(self.writing_snapshot(), before)
                if changed is None:
                    self.assertFalse(export.exists())
                else:
                    self.assertEqual(export.read_bytes(), changed)
                export.write_bytes(original)

    def test_imported_baseline_requires_real_history_review_then_becomes_eligible(self):
        text = "第1章 旧账本\n" + BODY
        self.draft.write_bytes(text.encode("utf-8"))
        self.book.adopt(1, self.draft, "导入既有章节，需要重新审稿。", self.rev(), volume_dir="第一卷 雨夜")
        self.texts[1] = text
        self.assert_code("review_required", self.prepare)
        self.publish_reviewed_history()
        self.assertEqual(self.book.db.execute("SELECT imported FROM chapter_state WHERE chapter=1").fetchone()[0], 1)
        result = self.prepare()
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["manifest"]["chapters"][0]["title"], "旧账本")

    def test_plain_first_body_line_is_never_mistaken_for_a_heading(self):
        self.commit(text=BODY, title="不带章头的原稿")
        result = self.prepare()
        self.assertEqual(result["manifest"]["chapters"][0]["body"], BODY)
        self.assertEqual(result["manifest"]["chapters"][0]["title"], "不带章头的原稿")

    def test_legacy_markdown_title_matching_formal_plan_is_separated(self):
        self.commit(text="# 雨夜\n" + BODY, title="雨夜")
        item = self.prepare()["manifest"]["chapters"][0]
        self.assertEqual(item["title"], "雨夜")
        self.assertEqual(item["body"], BODY)
        self.assertEqual(item["body_sha"], story.digest("# 雨夜\n" + BODY))

    def test_legacy_markdown_title_without_plan_title_remains_publishable(self):
        text = "# 雨夜\n" + BODY
        self.book.save_plan(1, {
            "volume_dir": "第一卷 雨夜", "goal": "完成钥匙交接", "stop": "看到账本，不翻开",
            "requires": [], "length": [10, 500],
            "beats": [{"choice": "交出钥匙", "change": "保留收据并入内"}]}, self.rev())
        self.draft.write_bytes(text.encode("utf-8"))
        result = self.book.commit(1, self.draft, {
            "book_id": self.book.meta("id"), "base_revision": self.rev(),
            "summary": "沈禾交出钥匙后入内，保留交接收据。", "changes": [], "review": self.review(text)})
        self.assertTrue(result["exports_complete"], result)
        self.assertEqual(self.book.chapter_path(1), "chapters/第一卷 雨夜/第1章 雨夜.md")
        item = self.prepare()["manifest"]["chapters"][0]
        self.assertEqual(item["title"], "雨夜")
        self.assertEqual(item["body"], BODY)

    def test_legacy_markdown_title_conflicting_with_formal_plan_is_blocked(self):
        self.commit(text="# 雨夜\n" + BODY, title="晴天")
        self.assert_code("publish_title_mismatch", self.prepare)
        self.assertFalse(self.ledger.exists())

    def test_legacy_h1_unicode_whitespace_with_and_without_formal_title(self):
        for chapter, (space, with_title) in enumerate(
                (("\u3000", True), ("\u3000", False), ("\u00a0", True), ("\u00a0", False)), 1):
            with self.subTest(space=repr(space), with_title=with_title):
                text = "#" + space + "雨夜\n" + BODY
                if with_title:
                    self.commit(chapter, text=text, title="雨夜")
                else:
                    self.book.save_plan(chapter, {
                        "volume_dir": "第一卷 雨夜", "goal": "完成钥匙交接", "stop": "看到账本，不翻开",
                        "requires": [], "length": [10, 500],
                        "beats": [{"choice": "交出钥匙", "change": "保留收据并入内"}]}, self.rev())
                    self.draft.write_bytes(text.encode("utf-8"))
                    result = self.book.commit(chapter, self.draft, {
                        "book_id": self.book.meta("id"), "base_revision": self.rev(),
                        "summary": "沈禾交出钥匙后入内，保留收据。", "changes": [], "review": self.review(text)})
                    self.assertTrue(result["exports_complete"], result)
                item = self.prepare((chapter,))["manifest"]["chapters"][0]
                self.assertEqual(item["title"], "雨夜")
                self.assertEqual(item["body"], BODY)
                self.assertEqual(item["body_sha"], story.digest(text))

    def test_unicode_indentation_in_an_ordinary_first_paragraph_is_preserved(self):
        for chapter, space in enumerate(("\u3000", "\u00a0"), 1):
            text = space + BODY + "\n正文引文中的 # 雨夜保持原样。\n"
            self.commit(chapter, text=text, title="雨夜")
            self.assertEqual(self.prepare((chapter,))["manifest"]["chapters"][0]["body"], text)

    def test_hashes_inside_prose_and_unrecognized_opening_are_preserved(self):
        for chapter, first in ((1, ""), (2, "## 雨夜\n")):
            with self.subTest(chapter=chapter):
                text = first + BODY + "\n# 账房旧规\n纸条写着：收据编号 #7，切勿遗失。\n"
                self.commit(chapter, text=text, title="正文中的纸条")
                item = self.prepare((chapter,))["manifest"]["chapters"][0]
                self.assertEqual(item["title"], "正文中的纸条")
                self.assertEqual(item["body"], text)

    def test_chinese_crlf_and_non_bmp_survive_title_body_conversion(self):
        body = BODY.replace("账。", "账：𠮷字旁还有一枚🌧️印记。")
        text = ("# 第一章 雨夜𠮷字\n" + body).replace("\n", "\r\n")
        self.commit(text=text, title="雨夜𠮷字")
        result = self.prepare()["manifest"]["chapters"][0]
        self.assertEqual(result["body"], body.replace("\n", "\r\n"))
        self.assertEqual(result["title"], "雨夜𠮷字")
        self.assertEqual(result["body_sha"], story.digest(text))
        self.assertEqual(result["body"].count("\r\n"), body.count("\n"))

    def test_reviewed_import_without_a_title_is_not_given_a_fabricated_title(self):
        self.draft.write_bytes(BODY.encode("utf-8"))
        self.book.adopt(1, self.draft, "导入无章头的既有正文。", self.rev(), volume_dir="第一卷 雨夜")
        self.texts[1] = BODY
        self.publish_reviewed_history()
        self.assert_code("chapter_title_missing", self.prepare)

    def test_invalid_platform_mode_chapter_ranges_and_unknown_fields_are_rejected(self):
        self.commit()
        self.commit(2)
        bad = [{"platform": "unknown"}, {"mode": "publish"}, {"chapters": []},
               {"chapters": [1, 3]}, {"chapters": [2, 1]}, {"chapters": [1, 1]},
               {"chapters": [True]}, {"chapters": ["1"]}, {"account_id": ""},
               {"remote_book_id": ""}, {"access_token": "not-a-real-token"}]
        before = self.writing_snapshot()
        for fields in bad:
            with self.subTest(fields=fields):
                payload = self.payload(**fields)
                self.assert_code("invalid_input", publishing.prepare, self.book, payload, self.rev())
        self.assertEqual(self.writing_snapshot(), before)

    def test_stale_revision_and_ambiguous_title_cannot_create_a_plan(self):
        self.commit(text="第1章 原稿标题\n" + BODY, title="正式大纲标题")
        self.assert_code("stale_revision", publishing.prepare, self.book, self.payload(), self.rev() - 1)
        self.assert_code("publish_title_mismatch", self.prepare)
        self.assertFalse(self.ledger.exists())

    def test_qimao_and_fanqie_bindings_get_distinct_plans_in_numeric_chapter_order(self):
        for chapter in range(1, 11):
            self.commit(chapter)
        first = self.prepare((9, 10))
        second = self.prepare((9, 10), platform="qimao")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(second["manifest"]["platform"], "qimao")
        self.assertEqual([c["chapter"] for c in first["manifest"]["chapters"]], [9, 10])
        self.assertEqual([c["title"] for c in first["manifest"]["chapters"]], ["门后的账本9", "门后的账本10"])

    def test_missing_chapter_and_analysis_book_cannot_prepare(self):
        self.commit()
        with self.assertRaises(story.StoryError):
            self.prepare((1, 2))
        other_root = self.root.parent / "拆书项目"
        story.Book.create(other_root, "拆书分析", "analysis")
        other = story.Book(other_root)
        try:
            self.assert_code("invalid_book_kind", publishing.prepare, other, self.payload(), other.meta("revision"))
            self.assertFalse((other_root / ".story/publishing.sqlite3").exists())
        finally:
            other.close()

    def test_cancel_is_idempotent_and_reprepare_keeps_old_plan_cancelled(self):
        self.commit()
        plan = self.prepare()
        before = self.writing_snapshot()
        first = publishing.cancel(self.book, plan["id"])
        second = publishing.cancel(self.book, plan["id"])
        self.assertEqual(first["status"], "cancelled")
        self.assertTrue(second["idempotent"])
        self.assertEqual(self.inspect(plan["id"])["status"], "cancelled")
        replacement = self.prepare()
        self.assertNotEqual(replacement["id"], plan["id"])
        self.assertFalse(replacement["idempotent"])
        self.assertEqual(replacement["manifest"], plan["manifest"])
        self.assertEqual(self.inspect(plan["id"])["status"], "cancelled")
        self.assertEqual(self.inspect(plan["id"])["manifest"], plan["manifest"])
        self.assertEqual(self.prepare()["id"], replacement["id"])
        self.assertEqual(self.writing_snapshot(), before)

    def test_cancelled_check_does_not_recheck_or_change_its_saved_observation(self):
        self.commit()
        original = self.prepare()
        cancelled = publishing.cancel(self.book, original["id"])
        self.commit(text=self.texts[1] + "她记下了门后的脚步声。\n", replace_last=True)
        before = self.writing_snapshot()
        with closing(sqlite3.connect(self.ledger)) as ledger:
            saved = tuple(ledger.iterdump())
        result = publishing.check(self.book, original["id"])
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(result["source_check_performed"])
        self.assertIsNone(result["source_matches_current"])
        self.assertEqual(result["checked_at"], cancelled["checked_at"])
        self.assertEqual(result["reasons"], cancelled["reasons"])
        with closing(sqlite3.connect(self.ledger)) as ledger:
            self.assertEqual(tuple(ledger.iterdump()), saved)
        self.assertEqual(self.writing_snapshot(), before)

    def test_repaired_export_allows_a_new_plan_without_reactivating_stale_one(self):
        self.commit()
        original = self.prepare()
        export = self.root / self.book.chapter_path(1)
        export.unlink()
        first_check = publishing.check(self.book, original["id"])
        self.assertEqual(first_check["status"], "stale")
        self.assertFalse(first_check["source_matches_current"])
        self.assertTrue(self.book.export()["exports_complete"])
        before = self.writing_snapshot()
        rechecked = publishing.check(self.book, original["id"])
        self.assertEqual(rechecked["status"], "stale")
        self.assertFalse(rechecked["ok"])
        self.assertTrue(rechecked["source_check_performed"])
        self.assertTrue(rechecked["source_matches_current"])
        self.assertNotEqual(rechecked["checked_at"], first_check["checked_at"])
        self.assertEqual(rechecked["reasons"], [])
        self.assertEqual(rechecked["chapter_checks"], [{"chapter": 1, "matches_current": True, "changed_fields": []}])
        replacement = self.prepare()
        self.assertNotEqual(replacement["id"], original["id"])
        self.assertEqual(replacement["status"], "prepared")
        self.assertEqual(replacement["manifest"], original["manifest"])
        self.assertEqual(self.inspect(original["id"])["status"], "stale")
        self.assertEqual(self.prepare()["id"], replacement["id"])
        self.assertEqual(self.writing_snapshot(), before)

    def test_failed_initialization_and_insert_leave_no_half_created_ledger(self):
        self.commit()
        before = self.writing_snapshot()
        original_schema = story.storage.execute_schema

        def interrupted_schema(connection, script):
            original_schema(connection, script)
            raise OSError("simulated interruption after schema creation")

        for target, attribute, effect in ((story.storage, "execute_schema", interrupted_schema),
                                           (publishing, "_packet", OSError("simulated interruption after plan insert"))):
            with self.subTest(stage=attribute):
                with patch.object(target, attribute, side_effect=effect):
                    with self.assertRaises(OSError):
                        self.prepare()
                self.assertFalse(self.ledger.exists())
                self.assertEqual(list(self.ledger.parent.glob("publishing.sqlite3-*")), [])
                self.assertEqual(self.writing_snapshot(), before)
        retried = self.prepare()
        self.assertFalse(retried["idempotent"])
        self.assertEqual(publishing.list_plans(self.book)["total"], 1)

    def test_failed_new_plan_insert_preserves_existing_ledger_then_retries(self):
        self.commit()
        first = self.prepare()
        before = self.writing_snapshot()
        with patch.object(publishing, "_packet", side_effect=OSError("simulated disk interruption")):
            with self.assertRaises(OSError):
                self.prepare(remote_book_id="second-book")
        self.assertEqual(publishing.list_plans(self.book)["total"], 1)
        self.assertEqual(self.inspect(first["id"])["manifest"], first["manifest"])
        retried = self.prepare(remote_book_id="second-book")
        self.assertFalse(retried["idempotent"])
        self.assertEqual(publishing.list_plans(self.book)["total"], 2)
        self.assertEqual(self.writing_snapshot(), before)

    def test_concurrent_identical_preparations_produce_one_immutable_plan(self):
        self.commit()
        expected = self.rev()
        before = self.writing_snapshot()
        gate = threading.Barrier(2)

        def attempt():
            book = story.Book(self.root)
            try:
                gate.wait(timeout=10)
                return publishing.prepare(book, self.payload(), expected)
            finally:
                book.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(attempt) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(results[0]["id"], results[1]["id"])
        self.assertEqual(results[0]["manifest"], results[1]["manifest"])
        self.assertEqual(sum(result["idempotent"] for result in results), 1)
        self.assertEqual(publishing.list_plans(self.book)["total"], 1)
        self.assertEqual(self.writing_snapshot(), before)

    def first_prepare_after_process_crash(self, stage):
        self.commit()
        before = self.writing_snapshot()
        payload = self.root / "发布输入.json"
        payload.write_bytes(story.dumps(self.payload()).encode("utf-8"))
        script = """
import importlib.util
import os
import sys
spec = importlib.util.spec_from_file_location('crash_story', sys.argv[1])
story = importlib.util.module_from_spec(spec)
spec.loader.exec_module(story)
book = story.Book(sys.argv[2])
if sys.argv[4] == 'schema':
    original = story.storage.execute_schema
    def crash_after_schema(connection, schema):
        original(connection, schema)
        os._exit(77)
    story.storage.execute_schema = crash_after_schema
else:
    def crash_after_insert(*args, **kwargs):
        os._exit(77)
    story.publish._packet = crash_after_insert
story.publish.prepare(book, story.read_json(sys.argv[3]), book.meta('revision'))
raise AssertionError('Crash barrier was not reached')
"""
        crashed = subprocess.run([sys.executable, "-B", "-c", script, str(TOOL), str(self.root), str(payload), stage],
                                 capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(crashed.returncode, 77, crashed.stderr)
        self.assertFalse(self.ledger.exists(), "A killed first transaction must not expose an incomplete live ledger")
        self.assertTrue(list(self.ledger.parent.glob(".publishing-init-*")), "The crash must bypass staging cleanup")
        self.assertEqual(self.writing_snapshot(), before)

        command = [sys.executable, "-B", str(TOOL), "publish-prepare", "--book", str(self.root),
                   "--input", str(payload), "--expect", str(self.rev())]
        retried = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(retried.returncode, 0, retried.stderr)
        prepared = json.loads(retried.stdout)
        self.assertFalse(prepared["idempotent"])
        self.assertEqual(prepared["status"], "prepared")
        self.assertEqual(publishing.list_plans(self.book)["total"], 1)
        self.assertEqual(self.inspect(prepared["id"])["manifest"]["chapters"][0]["body"], BODY)
        with closing(sqlite3.connect(self.ledger)) as ledger:
            self.assertEqual(ledger.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        again = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(json.loads(again.stdout)["id"], prepared["id"])
        self.assertTrue(json.loads(again.stdout)["idempotent"])
        self.assertEqual(self.writing_snapshot(), before)

    def test_first_schema_process_crash_retries_in_new_process_without_half_ledger(self):
        self.first_prepare_after_process_crash("schema")

    def test_first_insert_process_crash_retries_in_new_process_without_duplicate_plan(self):
        self.first_prepare_after_process_crash("insert")

    def test_hot_journal_requires_writable_check_and_recovers_without_story_mutation(self):
        self.commit()
        plan = self.prepare()
        before = self.writing_snapshot()
        script = """
import os
import sqlite3
import sys
db = sqlite3.connect(sys.argv[1])
db.execute('PRAGMA cache_size=1')
db.execute('PRAGMA cache_spill=ON')
db.execute('PRAGMA synchronous=FULL')
db.execute('BEGIN IMMEDIATE')
db.execute('UPDATE plans SET reasons=?', ('x' * 2000000,))
os._exit(77)
"""
        crashed = subprocess.run([sys.executable, "-B", "-c", script, str(self.ledger)],
                                 capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(crashed.returncode, 77, crashed.stderr)
        journal = self.ledger.with_name(self.ledger.name + "-journal")
        self.assertTrue(journal.exists())
        unchanged = self.ledger.read_bytes(), journal.read_bytes()
        self.assert_code("publishing_recovery_required", self.inspect, plan["id"])
        self.assertEqual((self.ledger.read_bytes(), journal.read_bytes()), unchanged)

        recovered = subprocess.run([sys.executable, "-B", str(TOOL), "publish-check", "--book", str(self.root),
                                    "--id", plan["id"]], capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        result = json.loads(recovered.stdout)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["reasons"], [])
        self.assertEqual(self.inspect(plan["id"])["manifest"], plan["manifest"])
        self.assertEqual(publishing.list_plans(self.book)["total"], 1)
        with closing(sqlite3.connect(self.ledger)) as ledger:
            self.assertEqual(ledger.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.writing_snapshot(), before)

    def test_recover_without_id_restores_hot_journal_and_preserves_every_plan(self):
        self.commit()
        ready = self.prepare(remote_book_id="ready-book")
        cancelled = self.prepare(remote_book_id="cancelled-book")
        publishing.cancel(self.book, cancelled["id"])
        stale = self.prepare(remote_book_id="stale-book")
        (self.root / self.book.chapter_path(1)).unlink()
        self.assertEqual(publishing.check(self.book, stale["id"])["status"], "stale")
        self.assertTrue(self.book.export()["exports_complete"])
        ids = [ready["id"], cancelled["id"], stale["id"]]
        before_plans = {plan_id: self.inspect(plan_id) for plan_id in ids}
        before = self.writing_snapshot()
        with closing(sqlite3.connect(self.ledger)) as ledger:
            before_ledger = tuple(ledger.iterdump())
        script = """
import os
import sqlite3
import sys
db = sqlite3.connect(sys.argv[1])
db.execute('PRAGMA cache_size=1')
db.execute('PRAGMA cache_spill=ON')
db.execute('PRAGMA synchronous=FULL')
db.execute('BEGIN IMMEDIATE')
db.execute('UPDATE plans SET reasons=?', ('x' * 2000000,))
os._exit(77)
"""
        crashed = subprocess.run([sys.executable, "-B", "-c", script, str(self.ledger)],
                                 capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(crashed.returncode, 77, crashed.stderr)
        self.assertTrue(self.ledger.with_name(self.ledger.name + "-journal").exists())
        self.assert_code("publishing_recovery_required", publishing.list_plans, self.book)

        recovered = subprocess.run([sys.executable, "-B", str(TOOL), "publish-recover", "--book", str(self.root)],
                                   capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        result = json.loads(recovered.stdout)
        self.assertTrue(result["ok"])
        self.assertTrue(result["ledger_exists"])
        self.assertEqual(result["book_id"], self.book.meta("id"))
        self.assertEqual(result["plans"], 3)
        self.assertEqual(result["remote_state"], "unknown")
        self.assertFalse(result["platform_verified"])
        self.assertFalse(result["ready_to_upload"])
        self.assertFalse(result["source_check_performed"])
        self.assertIsNone(result["source_matches_current"])
        self.assertEqual(publishing.list_plans(self.book)["total"], 3)
        self.assertEqual({plan_id: self.inspect(plan_id) for plan_id in ids}, before_plans)
        with closing(sqlite3.connect(self.ledger)) as ledger:
            self.assertEqual(ledger.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(tuple(ledger.iterdump()), before_ledger)
        self.assertEqual(self.writing_snapshot(), before)

    def test_recover_without_an_existing_ledger_never_initializes_publishing(self):
        before = self.writing_snapshot()
        result = subprocess.run([sys.executable, "-B", str(TOOL), "publish-recover", "--book", str(self.root)],
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        packet = json.loads(result.stdout)
        self.assertTrue(packet["ok"])
        self.assertFalse(packet["ledger_exists"])
        self.assertEqual(packet["book_id"], self.book.meta("id"))
        self.assertEqual(packet["plans"], 0)
        self.assertEqual(packet["remote_state"], "unknown")
        self.assertFalse(packet["platform_verified"])
        self.assertFalse(packet["ready_to_upload"])
        self.assertFalse(packet["source_check_performed"])
        self.assertIsNone(packet["source_matches_current"])
        self.assertFalse(self.ledger.exists())
        self.assertFalse(list(self.ledger.parent.glob(".publishing-init-*")))
        self.assertEqual(self.writing_snapshot(), before)

    def test_recover_rejects_damaged_manifest_instead_of_claiming_repair(self):
        self.commit()
        self.prepare()
        raw = self.ledger.read_bytes()
        token = b'"remote_book_id":"book-one"'
        self.assertEqual(raw.count(token), 1)
        self.ledger.write_bytes(raw.replace(token, b'"remote_book_id":"book-ond"'))
        corrupted = self.ledger.read_bytes()
        before = self.writing_snapshot()
        result = subprocess.run([sys.executable, "-B", str(TOOL), "publish-recover", "--book", str(self.root)],
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual(json.loads(result.stderr)["error"], "publishing_corrupt")
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.ledger.read_bytes(), corrupted)
        self.assertEqual(self.writing_snapshot(), before)

    def test_recover_rejects_a_ledger_copied_from_another_book(self):
        self.commit()
        self.prepare()
        other_root = self.root.parent / "错误账本所属书"
        story.Book.create(other_root, "另一部作品", "long")
        other_ledger = other_root / ".story/publishing.sqlite3"
        shutil.copy2(self.ledger, other_ledger)
        original = other_ledger.read_bytes()
        other = story.Book(other_root)
        try:
            before = tuple(other.db.iterdump())
            result = subprocess.run([sys.executable, "-B", str(TOOL), "publish-recover", "--book", str(other_root)],
                                    capture_output=True, text=True, encoding="utf-8", timeout=20)
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertEqual(json.loads(result.stderr)["error"], "wrong_book")
            self.assertEqual(result.stdout, "")
            self.assertEqual(other_ledger.read_bytes(), original)
            self.assertEqual(self.ledger.read_bytes(), original)
            self.assertEqual(tuple(other.db.iterdump()), before)
        finally:
            other.close()

    def test_pagination_accounts_for_every_plan_without_silent_budget_truncation(self):
        self.commit()
        ids = {self.prepare(remote_book_id=f"remote-{n}")["id"] for n in range(3)}
        before = self.writing_snapshot()
        first = publishing.list_plans(self.book, limit=2)
        second = publishing.list_plans(self.book, offset=first["next_offset"], limit=2)
        self.assertEqual(first["total"], 3)
        self.assertEqual(len(first["results"]), 2)
        self.assertEqual(len(second["results"]), 1)
        self.assertIsNone(second["next_offset"])
        self.assertEqual({r["id"] for r in first["results"] + second["results"]}, ids)
        error = self.assert_code("budget_exceeded", publishing.list_plans, self.book, limit=3, budget=256)
        self.assertGreater(error.details["minimum_bytes"], 256)
        plan_id = next(iter(ids))
        error = self.assert_code("budget_exceeded", self.inspect, plan_id, budget=256)
        complete = self.inspect(plan_id, budget=error.details["minimum_bytes"] + 64)
        self.assertEqual(complete["manifest"]["chapters"][0]["body"], BODY)
        self.assertEqual(self.writing_snapshot(), before)

    def test_backup_is_unique_complete_and_bound_to_book_identity(self):
        self.commit()
        plan = self.prepare()
        before = self.writing_snapshot()
        first = publishing.backup(self.book)
        second = publishing.backup(self.book)
        self.assertNotEqual(first["backup"], second["backup"])
        self.assertEqual(first["book_id"], self.book.meta("id"))
        for result in (first, second):
            path = Path(result["backup"])
            self.assertTrue(path.is_absolute())
            self.assertEqual(path.parent, self.root / ".story/publishing-backups")
            with closing(sqlite3.connect(path)) as saved, closing(sqlite3.connect(self.ledger)) as live:
                self.assertEqual(saved.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(tuple(saved.iterdump()), tuple(live.iterdump()))
        self.assertEqual(self.inspect(plan["id"])["status"], "prepared")
        self.assertEqual(self.writing_snapshot(), before)

        other_root = self.root.parent / "另一部书"
        story.Book.create(other_root, "另一部书", "long")
        shutil.copy2(first["backup"], other_root / ".story/publishing.sqlite3")
        other = story.Book(other_root)
        try:
            self.assert_code("wrong_book", publishing.list_plans, other)
        finally:
            other.close()

    def test_manifest_damage_is_rejected_by_list_inspect_and_backup(self):
        self.commit()
        plan = self.prepare()
        raw = self.ledger.read_bytes()
        original = b'"remote_book_id":"book-one"'
        damaged = b'"remote_book_id":"book-ond"'
        self.assertEqual(raw.count(original), 1)
        self.ledger.write_bytes(raw.replace(original, damaged))
        with closing(sqlite3.connect(self.ledger)) as ledger:
            self.assertEqual(ledger.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(ledger.execute("SELECT fingerprint FROM plans WHERE id=?", (plan["id"],)).fetchone()[0],
                             plan["manifest_sha256"])
        corrupted_bytes = self.ledger.read_bytes()
        before = self.writing_snapshot()
        for name, operation in (("list", lambda: publishing.list_plans(self.book)),
                                ("inspect", lambda: self.inspect(plan["id"])),
                                ("backup", lambda: publishing.backup(self.book))):
            with self.subTest(command=name):
                self.assert_code("publishing_corrupt", operation)
                self.assertEqual(self.ledger.read_bytes(), corrupted_bytes)
        backup_dir = self.root / ".story/publishing-backups"
        self.assertEqual([p for p in backup_dir.rglob("*") if p.is_file()], [])
        self.assertEqual(self.writing_snapshot(), before)

    def test_ledger_rejects_missing_or_weakened_freeze_constraints(self):
        self.commit()
        self.prepare()
        pristine = self.ledger.read_bytes()
        mutations = {
            "missing index": ["DROP INDEX plans_prepared_fingerprint"],
            "wrong index predicate": ["DROP INDEX plans_prepared_fingerprint",
                                      "CREATE UNIQUE INDEX plans_prepared_fingerprint ON plans(fingerprint) WHERE status='stale'"],
            "quoted wrong index predicate": ["DROP INDEX plans_prepared_fingerprint",
                                             "CREATE UNIQUE INDEX plans_prepared_fingerprint ON plans(fingerprint) "
                                             "WHERE status='\"prepared\"'"],
            "spaced wrong index predicate": ["DROP INDEX plans_prepared_fingerprint",
                                             "CREATE UNIQUE INDEX plans_prepared_fingerprint ON plans(fingerprint) "
                                             "WHERE status='pre pared'"],
            "missing update trigger": ["DROP TRIGGER plans_immutable"],
            "weakened update trigger": ["DROP TRIGGER plans_immutable",
                                        "CREATE TRIGGER plans_immutable BEFORE UPDATE OF id ON plans BEGIN "
                                        "SELECT RAISE(ABORT,'publishing snapshots are immutable'); END"],
            "missing delete trigger": ["DROP TRIGGER plans_no_delete"],
            "weakened delete trigger": ["DROP TRIGGER plans_no_delete",
                                        "CREATE TRIGGER plans_no_delete BEFORE DELETE ON plans WHEN 0 BEGIN "
                                        "SELECT RAISE(ABORT,'publishing snapshots cannot be deleted'); END"],
        }
        try:
            for description, statements in mutations.items():
                with self.subTest(description=description):
                    self.ledger.write_bytes(pristine)
                    with closing(sqlite3.connect(self.ledger)) as db, db:
                        for statement in statements:
                            db.execute(statement)
                    changed = self.ledger.read_bytes()
                    self.assert_code("publishing_schema_mismatch", publishing.list_plans, self.book)
                    self.assert_code("publishing_schema_mismatch", publishing.recover, self.book)
                    self.assertEqual(self.ledger.read_bytes(), changed)
        finally:
            self.ledger.write_bytes(pristine)
        self.assertTrue(publishing.recover(self.book)["ok"])

    def test_symlink_and_hardlink_ledgers_are_rejected_without_touching_target(self):
        self.commit()
        self.prepare()
        outside = self.root.parent / "outside-publishing.sqlite3"
        self.ledger.rename(outside)
        original = outside.read_bytes()
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind):
                try:
                    if kind == "symlink":
                        self.ledger.symlink_to(outside)
                    else:
                        os.link(outside, self.ledger)
                except (OSError, NotImplementedError) as error:
                    self.skipTest(f"Host cannot create {kind}: {error}")
                try:
                    code = "linked_path" if kind == "symlink" else "unsafe_publish_path"
                    self.assert_code(code, publishing.list_plans, self.book)
                    self.assert_code(code, publishing.backup, self.book)
                    self.assertEqual(outside.read_bytes(), original)
                finally:
                    self.ledger.unlink()

    def test_linked_sidecar_and_backup_directory_never_write_outside_book(self):
        self.commit()
        self.prepare()
        outside = self.root.parent / "outside"
        outside.mkdir()
        marker = outside / "preserve.txt"
        marker.write_bytes(b"unchanged")
        sidecar = self.ledger.with_name(self.ledger.name + "-journal")
        backup_dir = self.root / ".story/publishing-backups"
        try:
            sidecar.symlink_to(marker)
            backup_dir.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Host cannot create symlink: {error}")
        try:
            self.assert_code("unsafe_publish_path", publishing.list_plans, self.book)
            sidecar.unlink()
            self.assert_code("linked_path", publishing.backup, self.book)
            self.assertEqual(list(outside.iterdir()), [marker])
            self.assertEqual(marker.read_bytes(), b"unchanged")
        finally:
            sidecar.unlink(missing_ok=True)
            backup_dir.unlink()

    def test_cli_prepare_inspect_list_and_template_preserve_unicode(self):
        self.commit()
        payload = self.root / "发布输入.json"
        payload.write_bytes(story.dumps(self.payload()).encode("utf-8"))
        command = [sys.executable, "-B", str(TOOL)]
        prepared = subprocess.run(command + ["publish-prepare", "--book", str(self.root), "--input", str(payload),
                                            "--expect", str(self.rev())], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        plan = json.loads(prepared.stdout)
        inspected = subprocess.run(command + ["publish-inspect", "--book", str(self.root), "--id", plan["id"]],
                                   capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertEqual(json.loads(inspected.stdout)["manifest"]["chapters"][0]["body"], BODY)
        template = subprocess.run(command + ["template", "publish"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(template.returncode, 0, template.stderr)
        self.assertEqual(json.loads(template.stdout)["mode"], "draft")


if __name__ == "__main__":
    unittest.main()
