"""A nondefault new-chapter count needs a recorded, reviewable source."""

import importlib.util
from pathlib import Path
import tempfile
import unittest


TOOL = Path(__file__).resolve().parents[1] / "skills/story-skill/scripts/story.py"
spec = importlib.util.spec_from_file_location("story_length_exception", TOOL)
story = importlib.util.module_from_spec(spec)
spec.loader.exec_module(story)


SHORT_BODY = "她推开门。" * 260  # 1300 visible characters.
DEFAULT_BODY = "她推开门。" * 500  # 2500 visible characters.


class LengthExceptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-length-exception-")
        self.root = Path(self.temp.name) / "测试书"
        story.Book.create(self.root, "测试书", "short")
        self.book = story.Book(self.root)
        self.draft = self.root / ".story/drafts/第1章.md"
        self.draft.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def plan(self, **updates):
        value = {"title": "推门", "volume_dir": "第一卷 门内", "goal": "推门进去",
                 "beats": [{"choice": "推开门", "change": "发现门已打开"}],
                 "stop": "停在门槛", "constraints": [], "requires": [], "tags": [],
                 "length": [1200, 1500], "count_method": "visible_nonspace_v1",
                 "count_title": False}
        value.update(updates)
        return value

    def write(self, body=SHORT_BODY):
        text = "第1章 推门\n" + body
        self.draft.write_bytes(text.encode("utf-8"))
        return text

    def delta(self, text):
        return {"book_id": self.book.meta("id"), "base_revision": self.book.meta("revision"),
                "summary": "她推开门。", "changes": [],
                "review": {"draft_sha256": story.digest(text), "checks": {
                    name: {"note": "推门的动作与结果在正文中。", "quote": "她推开门。"}
                    for name in story.CHECKS}, "issues": []}}

    def assert_error(self, code, operation):
        with self.assertRaises(story.StoryError) as result:
            operation()
        self.assertEqual(result.exception.code, code)
        return result.exception

    def install_legacy_plan(self):
        """Model a pre-upgrade plan already present in a book's state database."""
        value = self.plan()
        with self.book.transaction():
            self.book.db.execute("INSERT INTO plans(chapter,data) VALUES (?,?)",
                                 (1, story.dumps(value)))

    def test_new_nondefault_plan_needs_an_explicit_source_at_save(self):
        before = self.book.meta("revision")
        self.assert_error("length_exception_required", lambda: self.book.save_plan(
            1, self.plan(), before))
        self.assertEqual(self.book.meta("revision"), before)
        self.assertIsNone(self.book.db.execute(
            "SELECT data FROM plans WHERE chapter=1").fetchone())

    def test_old_uncommitted_plan_cannot_escape_lint_or_commit(self):
        self.install_legacy_plan()
        text = self.write()
        lint = self.book.lint(1, self.draft)
        self.assertFalse(lint["ok"])
        self.assertIn("length_exception_required", {error["code"] for error in lint["errors"]})
        error = self.assert_error("lint_failed", lambda: self.book.commit(
            1, self.draft, self.delta(text)))
        self.assertIn("length_exception_required", {
            item["code"] for item in error.details["lint"]["errors"]})
        self.assertEqual(self.book.meta("last_chapter"), 0)

    def test_malformed_legacy_exception_fails_closed_without_key_error(self):
        text = self.write()
        for claim in ({"source": "unknown", "quote": "曾有短章约定。"},
                      {"quote": "曾有短章约定。"},
                      {"source": "book_agreement"},
                      {"source": "user_request", "quote": "\u200b"}):
            with self.subTest(claim=claim):
                with self.book.transaction():
                    self.book.db.execute(
                        "INSERT INTO plans(chapter,data) VALUES (?,?) "
                        "ON CONFLICT(chapter) DO UPDATE SET data=excluded.data",
                        (1, story.dumps(self.plan(length_exception=claim))))
                lint = self.book.lint(1, self.draft)
                self.assertFalse(lint["ok"])
                self.assertIn("length_exception_required", {
                    item["code"] for item in lint["errors"]})
                error = self.assert_error("lint_failed", lambda: self.book.commit(
                    1, self.draft, self.delta(text)))
                self.assertIn("length_exception_required", {
                    item["code"] for item in error.details["lint"]["errors"]})

    def test_user_request_exception_allows_short_chapter(self):
        exception = {"source": "user_request", "quote": "请把本章控制在1200到1500字。"}
        self.book.save_plan(1, self.plan(length_exception=exception), self.book.meta("revision"))
        text = self.write()
        lint = self.book.lint(1, self.draft)
        self.assertTrue(lint["ok"], lint["errors"])
        self.assertEqual(lint["length_count"], 1300)
        self.assertTrue(self.book.commit(1, self.draft, self.delta(text))["exports_complete"])
        self.assertEqual(self.book.get_plan(1)["length_exception"], exception)

    def test_invisible_user_quote_is_not_an_exception(self):
        for quote in ("\u200b", "\u2060"):
            with self.subTest(quote=repr(quote)):
                exception = {"source": "user_request", "quote": quote}
                self.assert_error("length_exception_required", lambda: self.book.save_plan(
                    1, self.plan(length_exception=exception), self.book.meta("revision")))

    def test_book_agreement_exception_requires_a_real_quote(self):
        agreement = self.root / "创作约定.md"
        quote = "本书第一章控制在1200到1500字。"
        agreement.write_text("# 章幅\n" + quote + "\n", encoding="utf-8")
        exception = {"source": "book_agreement", "path": "创作约定.md", "quote": quote}
        self.book.save_plan(1, self.plan(length_exception=exception), self.book.meta("revision"))
        text = self.write()
        self.assertTrue(self.book.lint(1, self.draft)["ok"])
        self.assertTrue(self.book.commit(1, self.draft, self.delta(text))["exports_complete"])

    def test_book_agreement_absent_quote_is_rejected(self):
        (self.root / "创作约定.md").write_text("本书仍用默认章幅。\n", encoding="utf-8")
        exception = {"source": "book_agreement", "path": "创作约定.md",
                     "quote": "本书第一章控制在1200到1500字。"}
        self.assert_error("length_exception_source_missing", lambda: self.book.save_plan(
            1, self.plan(length_exception=exception), self.book.meta("revision")))

    def test_draft_file_cannot_claim_to_be_the_book_agreement(self):
        quote = "本书第一章控制在1200到1500字。"
        temporary = self.root / ".story/drafts/临时约定.md"
        temporary.write_text(quote + "\n", encoding="utf-8")
        exception = {"source": "book_agreement", "path": ".story/drafts/临时约定.md",
                     "quote": quote}
        self.assert_error("length_exception_source_missing", lambda: self.book.save_plan(
            1, self.plan(length_exception=exception), self.book.meta("revision")))

    def test_book_agreement_removed_after_save_blocks_lint_and_commit(self):
        agreement = self.root / "创作约定.md"
        quote = "本书第一章控制在1200到1500字。"
        agreement.write_text(quote + "\n", encoding="utf-8")
        exception = {"source": "book_agreement", "path": "创作约定.md", "quote": quote}
        self.book.save_plan(1, self.plan(length_exception=exception), self.book.meta("revision"))
        text = self.write()
        agreement.write_text("本书第一章恢复默认章幅。\n", encoding="utf-8")
        lint = self.book.lint(1, self.draft)
        self.assertFalse(lint["ok"])
        self.assertIn("length_exception_source_missing", {item["code"] for item in lint["errors"]})
        prepared = self.book.prepare(1, self.draft)
        self.assertFalse(prepared["lint"]["ok"])
        error = self.assert_error("lint_failed", lambda: self.book.commit(
            1, self.draft, self.delta(text)))
        self.assertIn("length_exception_source_missing", {
            item["code"] for item in error.details["lint"]["errors"]})
        self.assertEqual(self.book.meta("last_chapter"), 0)

    def test_count_method_and_title_count_are_also_nondefault(self):
        for changed in ({"length": [2400, 2800], "count_method": "han_v1"},
                        {"length": [2400, 2800], "count_title": True}):
            with self.subTest(changed=changed):
                self.assert_error("length_exception_required", lambda: self.book.save_plan(
                    1, self.plan(**changed), self.book.meta("revision")))

    def test_default_count_needs_no_exception(self):
        self.book.save_plan(1, self.plan(length=[2400, 2800]), self.book.meta("revision"))
        text = self.write(DEFAULT_BODY)
        self.assertTrue(self.book.lint(1, self.draft)["ok"])
        self.assertTrue(self.book.commit(1, self.draft, self.delta(text))["exports_complete"])

    def test_old_committed_chapter_is_not_retroactively_blocked(self):
        exception = {"source": "user_request", "quote": "请把本章控制在1200到1500字。"}
        self.book.save_plan(1, self.plan(length_exception=exception), self.book.meta("revision"))
        first = self.write()
        self.book.commit(1, self.draft, self.delta(first))
        legacy = self.book.get_plan(1)
        legacy.pop("length_exception")
        with self.book.transaction():
            self.book.db.execute("UPDATE plans SET data=? WHERE chapter=1", (story.dumps(legacy),))
        revised = self.write(SHORT_BODY + "她停住脚。")
        self.assertTrue(self.book.lint(1, self.draft)["ok"])
        self.assertTrue(self.book.commit(1, self.draft, self.delta(revised),
                                         replace_last=True)["exports_complete"])

    def test_imported_baseline_is_not_retroactively_blocked(self):
        self.write()
        self.book.adopt(1, self.draft, "原章基线。", self.book.meta("revision"),
                        volume_dir="第一卷 门内")
        self.book.save_plan(1, self.plan(), self.book.meta("revision"))
        self.assertTrue(self.book.lint(1, self.draft)["ok"])

    def test_missing_prebaseline_short_chapter_can_keep_its_old_plan(self):
        self.draft.write_bytes(("第2章 旧章\n" + SHORT_BODY).encode("utf-8"))
        self.book.adopt(2, self.draft, "原第二章基线。", self.book.meta("revision"),
                        volume_dir="第一卷 门内")
        # Chapter 1 is historical source material, not the next native chapter.
        self.book.save_plan(1, self.plan(), self.book.meta("revision"))
        first = self.write()
        self.assert_error("chapter_order", lambda: self.book.commit(
            1, self.draft, self.delta(first)))
        result = self.book.adopt_backfill(1, self.draft, "原第一章基线。",
                                          self.book.meta("revision"),
                                          volume_dir="第一卷 门内")
        self.assertTrue(result["exports_complete"])


if __name__ == "__main__":
    unittest.main()
