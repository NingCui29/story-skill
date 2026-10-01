"""Publication-time chapter titles for old flat-path books."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


TOOL = Path(__file__).resolve().parents[1] / "skills/story-skill/scripts/story.py"
spec = importlib.util.spec_from_file_location("story_legacy_plan_title", TOOL)
story = importlib.util.module_from_spec(spec)
spec.loader.exec_module(story)


OLD_TITLE = "门后的雨"
NEW_TITLE = "新的入口"
ORIGINAL = "第1章 门后的雨\n沈禾把钥匙交给守门人。\n"
REVISED = ORIGINAL + "她没有回头。\n"


class LegacyPlanTitleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-legacy-plan-title-")
        self.root = Path(self.temp.name) / "book"
        story.Book.create(self.root, "雨夜", "long")
        self.book = story.Book(self.root)
        self.draft = self.root / ".story/drafts/chapter.md"
        self.draft.parent.mkdir(parents=True)
        self.book.save_plan(1, {
            "title": OLD_TITLE, "volume_dir": "第一卷 雨夜",
            "goal": "找到入口", "stop": "交出钥匙后停笔",
            "beats": [{"choice": "交出钥匙", "change": "失去退路"}],
            "length": [10, 200],
            "length_exception": {"source": "user_request", "quote": "测试旧章模拟需要10至200字。"},
        }, self.book.meta("revision"))

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def legacy_native(self, *, recorded_commit):
        """Simulate a native pre-history chapter at the old numbered path."""
        base_revision = self.book.meta("revision")
        receipt = {"input": {"base_revision": base_revision},
                   "before": {}, "after": {}, "lint": story.lint_text(
                       ORIGINAL, self.book.get_plan(1), 1)}
        with self.book.transaction():
            self.book.queue_artifact("chapters/0001.md", ORIGINAL)
            self.book.db.execute("INSERT INTO chapters VALUES (?,?,?,?,?,?,0)",
                                 (1, ORIGINAL, story.digest(ORIGINAL),
                                  "沈禾交出钥匙。", story.dumps(receipt), "legacy-input"))
            self.book.set_meta("last_chapter", 1)
            self.book.index_chapter(1, ORIGINAL, "沈禾交出钥匙。")
            if recorded_commit:
                self.book.event("commit_chapter", {"chapter": 1, "text": ORIGINAL,
                                                   "receipt": receipt, "previous": None})
        self.book.export()
        self.assertEqual(self.book.chapter_path(1), "chapters/0001.md")
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM history_heads").fetchone()[0], 0)

    def rename_current_plan(self):
        updated = {**self.book.get_plan(1), "title": NEW_TITLE}
        self.book.save_plan(1, updated, self.book.meta("revision"))

    def test_old_native_plan_event_blocks_unchanged_heading_after_plan_rename(self):
        self.legacy_native(recorded_commit=True)
        self.rename_current_plan()
        self.draft.write_text(REVISED, encoding="utf-8")

        result = self.book.lint(1, self.draft)

        self.assertFalse(result["ok"])
        self.assertIn("chapter_heading_title", {issue["code"] for issue in result["errors"]})

    def test_history_seed_uses_publication_plan_not_later_mutable_plan(self):
        self.legacy_native(recorded_commit=True)
        self.rename_current_plan()

        with self.book.transaction():
            story.history._ensure_history(self.book)

        recorded = self.book.db.execute("SELECT v.plan FROM history_heads h "
                                        "JOIN history_versions v ON v.id=h.version WHERE h.chapter=1").fetchone()[0]
        self.assertEqual(json.loads(recorded)["title"], OLD_TITLE)
        self.assertEqual(self.book.adopted_chapter_title(1), OLD_TITLE)

    def test_missing_publication_event_does_not_invent_an_old_title(self):
        self.legacy_native(recorded_commit=False)
        self.rename_current_plan()
        self.draft.write_text(REVISED, encoding="utf-8")

        self.assertIsNone(self.book.adopted_chapter_title(1))
        before_seed = self.book.lint(1, self.draft)
        self.assertFalse(before_seed["ok"])
        self.assertIn("chapter_heading_title", {issue["code"] for issue in before_seed["errors"]})
        with self.book.transaction():
            story.history._ensure_history(self.book)
        recorded = self.book.db.execute("SELECT v.plan FROM history_heads h "
                                        "JOIN history_versions v ON v.id=h.version WHERE h.chapter=1").fetchone()[0]
        self.assertIsNone(recorded)
        self.assertIsNone(self.book.adopted_chapter_title(1))
        after_seed = self.book.lint(1, self.draft)
        self.assertFalse(after_seed["ok"])
        self.assertIn("chapter_heading_title", {issue["code"] for issue in after_seed["errors"]})

    def test_missing_publication_event_keeps_unchanged_plan_and_heading_compatible(self):
        self.legacy_native(recorded_commit=False)
        self.draft.write_text(REVISED, encoding="utf-8")

        self.assertIsNone(self.book.adopted_chapter_title(1))
        self.assertTrue(self.book.lint(1, self.draft)["ok"])
        with self.book.transaction():
            story.history._ensure_history(self.book)
        recorded = self.book.db.execute("SELECT v.plan FROM history_heads h "
                                        "JOIN history_versions v ON v.id=h.version WHERE h.chapter=1").fetchone()[0]
        self.assertIsNone(recorded)
        self.assertTrue(self.book.lint(1, self.draft)["ok"])


if __name__ == "__main__":
    unittest.main()
