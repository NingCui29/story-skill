"""A nondefault new-chapter count needs a recorded, reviewable source."""

import importlib.util
from pathlib import Path
import tempfile
import unittest

from outline_fixture import bind_adopted_outline


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

    def rebind_fixture_outline(self):
        """Explicitly adopt a synthetic count-plan revision before testing it."""
        binding = story.outline.binding_for(self.book, 1)
        self.assertIsNotNone(binding)
        story.outline.bind(self.book, 1, binding["path"], self.book.meta("revision"),
                           binding["sha256"])

    def committed_v1_boundary(self):
        # Build a native chapter through the public API, then restore the
        # historical v1 minimum whose extra mark was counted before v2 existed.
        self.book.save_plan(1, self.plan(length=[2399, 2800], length_exception={
            "source": "user_request", "quote": "测试准备阶段按2399至2800字写。"}),
            self.book.meta("revision"))
        bind_adopted_outline(story, self.book, 1)
        text = self.write(DEFAULT_BODY[:2399] + "\ufe0f")
        self.assertTrue(self.book.commit(1, self.draft, self.delta(text))["exports_complete"])
        legacy = self.book.get_plan(1)
        legacy["length"] = [2400, 2800]
        legacy.pop("length_exception")
        self.book.save_plan(1, legacy, self.book.meta("revision"))
        self.rebind_fixture_outline()
        return text

    def test_saved_v1_boundary_lints_and_replaces_unchanged_but_rejects_changed_padding(self):
        text = self.committed_v1_boundary()
        checked = self.book.lint(1, self.draft)
        self.assertTrue(checked["ok"])
        self.assertEqual(checked["length_count"], 2400)
        self.assertTrue(self.book.commit(1, self.draft, self.delta(text),
                                         replace_last=True)["exports_complete"])
        changed = text.replace("她推开门。", "她敲了门。", 1)
        self.draft.write_bytes(changed.encode("utf-8"))
        rejected = self.book.lint(1, self.draft)
        self.assertIn("invisible_padding", {item["code"] for item in rejected["errors"]})
        self.assert_error("lint_failed", lambda: self.book.commit(
            1, self.draft, self.delta(changed), replace_last=True))
        self.assertEqual((self.root / self.book.chapter_path(1)).read_bytes(), text.encode("utf-8"))

    def test_history_keeps_unchanged_v1_boundary_and_rejects_changed_padding(self):
        text = self.committed_v1_boundary()
        history = story.history
        packet = history.branch_start(self.book, 1, self.book.meta("revision"))
        candidate = {"sha": story.digest(text), "summary": "她推开门。",
                     "dependencies": [], "complete": True}
        entry = {"chapter": 1, "text": text, "summary": candidate["summary"],
                 "dependencies": [], "complete": True,
                 "review": {**self.delta(text)["review"],
                            "candidate_sha256": history.candidate_fingerprint(candidate)}}
        staged = history.branch_update(self.book, packet["branch"], {"chapters": [entry]},
                                       self.book.meta("revision"))
        semantic = {**staged["review_template"], "note": "原正文及前后衔接已完整回读。",
                    "state_review": "旧章无新增状态，已核对当前记录。",
                    "coverage_review": "核对本章行动与事实，原文保持不变。"}
        staged = history.branch_update(self.book, packet["branch"], {"semantic_review": semantic},
                                       self.book.meta("revision"))
        published = history.branch_publish(self.book, staged["branch"], self.book.meta("revision"))
        self.assertTrue(published["exports_complete"])
        packet = history.branch_start(self.book, 1, self.book.meta("revision"))
        changed = text.replace("她推开门。", "她敲了门。", 1)
        error = self.assert_error("lint_failed", lambda: history.branch_update(
            self.book, packet["branch"], {"chapters": [{"chapter": 1, "text": changed}]},
            self.book.meta("revision")))
        self.assertIn("invisible_padding", {item["code"] for item in error.details["lint"]["errors"]})
        self.assertEqual((self.root / self.book.chapter_path(1)).read_bytes(), text.encode("utf-8"))

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
        bind_adopted_outline(story, self.book, 1)
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
        bind_adopted_outline(story, self.book, 1)
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
        self.book.save_plan(1, self.plan(length=[2400, 2800],
                                         count_method="visible_nonspace_v2"),
                            self.book.meta("revision"))
        bind_adopted_outline(story, self.book, 1)
        text = self.write(DEFAULT_BODY)
        self.assertTrue(self.book.lint(1, self.draft)["ok"])
        self.assertTrue(self.book.commit(1, self.draft, self.delta(text))["exports_complete"])

    def test_new_default_v2_requires_no_exception_but_explicit_v1_does(self):
        fresh = self.plan(length=[2400, 2800])
        fresh.pop("count_method")
        self.book.save_plan(1, fresh, self.book.meta("revision"))
        self.assertEqual(self.book.get_plan(1)["count_method"], "visible_nonspace_v2")
        self.assertFalse(story.needs_length_exception(self.book.get_plan(1)))
        self.assertTrue(story.needs_length_exception(self.plan(length=[2400, 2800])))
        self.assert_error("length_exception_required", lambda: self.book.save_plan(
            2, self.plan(length=[2400, 2800]), self.book.meta("revision")))

    def test_preexisting_uncommitted_default_v1_must_be_reconciled_before_commit(self):
        old = self.plan(length=[2400, 2800])
        with self.book.transaction():
            self.book.db.execute("INSERT INTO plans(chapter,data) VALUES (?,?)",
                                 (1, story.dumps(old)))
        self.assertEqual(self.book.get_plan(1)["count_method"], "visible_nonspace_v1")
        text = self.write(DEFAULT_BODY)
        lint = self.book.lint(1, self.draft)
        self.assertIn("length_exception_required", {item["code"] for item in lint["errors"]})
        self.assert_error("lint_failed", lambda: self.book.commit(
            1, self.draft, self.delta(text)))
        self.book.save_plan(1, {**old, "count_method": "visible_nonspace_v2"},
                            self.book.meta("revision"))
        bind_adopted_outline(story, self.book, 1)
        self.assertTrue(self.book.lint(1, self.draft)["ok"])
        self.assertTrue(self.book.commit(1, self.draft, self.delta(text))["exports_complete"])

    def test_committed_default_v1_remains_compatible_without_old_exception(self):
        exception = {"source": "user_request", "quote": "本章沿用旧的可见字符口径。"}
        self.book.save_plan(1, self.plan(length=[2400, 2800],
                                         length_exception=exception),
                            self.book.meta("revision"))
        bind_adopted_outline(story, self.book, 1)
        text = self.write(DEFAULT_BODY)
        self.assertTrue(self.book.commit(1, self.draft, self.delta(text))["exports_complete"])
        legacy = self.book.get_plan(1)
        legacy.pop("length_exception")
        with self.book.transaction():
            self.book.db.execute("UPDATE plans SET data=? WHERE chapter=1", (story.dumps(legacy),))
        self.rebind_fixture_outline()
        self.assertTrue(self.book.lint(1, self.draft)["ok"])
        revised = self.write(DEFAULT_BODY + "她停住脚。")
        self.assertTrue(self.book.commit(1, self.draft, self.delta(revised),
                                         replace_last=True)["exports_complete"])

    def test_old_committed_chapter_is_not_retroactively_blocked(self):
        exception = {"source": "user_request", "quote": "请把本章控制在1200到1500字。"}
        self.book.save_plan(1, self.plan(length_exception=exception), self.book.meta("revision"))
        bind_adopted_outline(story, self.book, 1)
        first = self.write()
        self.book.commit(1, self.draft, self.delta(first))
        legacy = self.book.get_plan(1)
        legacy.pop("length_exception")
        with self.book.transaction():
            self.book.db.execute("UPDATE plans SET data=? WHERE chapter=1", (story.dumps(legacy),))
        self.rebind_fixture_outline()
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
