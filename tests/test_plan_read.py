"""Recovery can inspect saved plans without accepting drift or changing state."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from outline_fixture import bind_adopted_outline
import test_analysis_read_snapshots as snapshots
import test_story as base


story = base.story


class PlanReadTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.StoryTests("runTest")
        self.fixture.setUp()
        self.root, self.book = self.fixture.root, self.fixture.book

    def tearDown(self):
        self.fixture.tearDown()

    def command(self, chapter=1, budget=16000, root=None):
        result = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", str(base.TOOL), "plan-read",
             "--book", str(root or self.root), "--chapter", str(chapter),
             "--budget-bytes", str(budget)],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        return result.returncode, json.loads(result.stdout or result.stderr)

    def state(self):
        binding = self.book.db.execute(
            "SELECT value FROM meta WHERE key=?", ("outline_binding:1",),
        ).fetchone()
        return {
            "database": hashlib.sha256((self.root / ".story/state.sqlite3").read_bytes()).hexdigest(),
            "revision": self.book.meta("revision"),
            "events": self.book.db.execute("SELECT count(*) FROM events").fetchone()[0],
            "binding": binding[0] if binding else None,
        }

    def bind(self):
        relative = "01_大纲细纲/第1章 门后的雨.md"
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes("# 章细纲\n状态：已采用\n沈禾交出钥匙。\n".encode("utf-8"))
        story.outline.bind(self.book, 1, relative, self.book.meta("revision"),
                           hashlib.sha256(target.read_bytes()).hexdigest())
        return target

    def assert_error(self, code, action, *args):
        with self.assertRaises(story.StoryError) as caught:
            action(*args)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_cli_reads_complete_saved_plan_during_file_and_plan_drift(self):
        target = self.bind()
        self.book.save_plan(1, base.plan(title="门后的雨", goal="保留钥匙并重新争取入口",
                                       length=[30, 140], count_title=True), self.book.meta("revision"))
        target.write_bytes("# 章细纲\n状态：已采用\n沈禾保留钥匙。\n".encode("utf-8"))
        expected = self.book.get_plan(1)
        before = self.state()
        code, result = self.command()
        self.assertEqual(code, 0, result)
        self.assertTrue(result["read_only"])
        self.assertEqual(result["read_only_scope"], "saved_chapter_plan")
        self.assertEqual(result["book_id"], self.book.meta("id"))
        self.assertEqual(result["revision"], before["revision"])
        self.assertEqual(result["chapter"], 1)
        self.assertEqual(result["plan"], expected)
        self.assertEqual(result["plan_sha256"], story.outline._plan_sha256(expected))
        self.assertEqual(result["budget"]["used"], len(story.dumps(result).encode("utf-8")))
        self.assertNotIn("outline", result)
        self.assertEqual(self.state(), before)
        self.assert_error("outline_plan_drift", self.book.context, 1)
        self.assert_error("outline_plan_drift", self.book.prepare, 1, self.fixture.draft)
        self.assert_error("outline_plan_drift", self.book.commit, 1,
                          self.fixture.draft, self.fixture.delta())
        self.assertEqual(self.state(), before)

    def test_cli_read_does_not_repair_pending_or_changed_exports(self):
        for pending in (True, False):
            with self.subTest(pending=pending):
                fixture = base.StoryTests("runTest")
                fixture.setUp()
                try:
                    bind_adopted_outline(story, fixture.book, 1)
                    if pending:
                        with patch.object(story, "atomic_write", side_effect=OSError("delayed export")):
                            receipt = fixture.book.commit(1, fixture.draft, fixture.delta())
                        self.assertFalse(receipt["exports_complete"])
                    else:
                        receipt = fixture.book.commit(1, fixture.draft, fixture.delta())
                        self.assertTrue(receipt["exports_complete"])
                        Path(receipt["path"]).write_bytes("外部候选，尚未审查。\n".encode("utf-8"))
                    target = Path(receipt["path"])
                    original = target.read_bytes() if target.exists() else None
                    database = fixture.root / ".story/state.sqlite3"
                    before = database.read_bytes()
                    code, result = self.command(root=fixture.root)
                    self.assertEqual(code, 0, result)
                    self.assertEqual(result["plan"], fixture.book.get_plan(1))
                    self.assertEqual(database.read_bytes(), before)
                    self.assertEqual(target.read_bytes() if target.exists() else None, original)
                    self.assert_error("exports_unresolved", fixture.book.context, 1)
                finally:
                    fixture.tearDown()

    def test_read_is_still_available_when_bound_file_is_missing(self):
        target = self.bind()
        target.unlink()
        before = self.state()
        code, result = self.command()
        self.assertEqual(code, 0, result)
        self.assertEqual(result["plan"], self.book.get_plan(1))
        self.assertFalse(target.exists())
        self.assertEqual(self.state(), before)
        self.assert_error("outline_plan_drift", self.book.context, 1)

    def test_budget_failure_does_not_truncate_plan_or_leave_a_transaction(self):
        self.book.save_plan(1, base.plan(goal="雨" * 1000), self.book.meta("revision"))
        before = self.state()
        error = self.assert_error("budget_exceeded", self.book.plan_read, 1, 256)
        self.assertGreater(error.details["minimum_bytes"], 256)
        self.assertFalse(self.book.db.in_transaction)
        code, result = self.command(budget=256)
        self.assertEqual(code, 2)
        self.assertEqual(result["error"], "budget_exceeded")
        self.assertNotIn("plan", result)
        code, result = self.command(budget=error.details["minimum_bytes"])
        self.assertEqual(code, 0, result)
        self.assertEqual(result["plan"], self.book.get_plan(1))
        self.assertEqual(self.state(), before)

    def test_missing_plan_or_book_does_not_initialize_or_write(self):
        before = self.state()
        for chapter, error in ((0, "invalid_input"), (2, "plan_missing")):
            with self.subTest(chapter=chapter):
                code, result = self.command(chapter=chapter)
                self.assertEqual(code, 2)
                self.assertEqual(result["error"], error)
                self.assertEqual(self.state(), before)
        missing = self.root / "尚未初始化"
        code, result = self.command(root=missing)
        self.assertEqual(code, 2)
        self.assertEqual(result["error"], "book_missing")
        self.assertFalse(missing.exists())

    def test_plan_and_revision_are_from_the_same_read_snapshot(self):
        self.book.db.execute("PRAGMA journal_mode=WAL")
        writer = story.Book(self.root)
        try:
            before = self.book.get_plan(1)
            revision = self.book.meta("revision")
            hook = snapshots.BeforeQuery(
                self.book.db, "SELECT value FROM meta",
                lambda: writer.save_plan(1, base.plan(goal="另一个写入者的新目标"), revision),
            )
            with patch.object(self.book, "db", hook):
                during = self.book.plan_read(1)
            self.assertIsNone(hook.callback)
            self.assertEqual(during["plan"], before)
            self.assertEqual(during["revision"], revision)
            self.assertEqual(during["plan_sha256"], story.outline._plan_sha256(before))
            after = self.book.plan_read(1)
            self.assertEqual(after["revision"], revision + 1)
            self.assertEqual(after["plan"]["goal"], "另一个写入者的新目标")
            self.assertFalse(self.book.db.in_transaction)
        finally:
            writer.close()


if __name__ == "__main__":
    unittest.main()
