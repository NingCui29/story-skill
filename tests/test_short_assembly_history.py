"""The complete short-story copy follows reviewed historical and outside edits."""
from pathlib import Path
import tempfile
import unittest

import test_long_history as fixtures


story = fixtures.story
history = fixtures.history


class ShortAssemblyHistoryTests(unittest.TestCase):
    # Reuse evidence-bound chapter/branch fixtures without inheriting their tests.
    rev = fixtures.LongHistoryTests.rev
    add = fixtures.LongHistoryTests.add
    dep = fixtures.LongHistoryTests.dep
    start = fixtures.LongHistoryTests.start
    candidate = fixtures.LongHistoryTests.candidate
    stage = fixtures.LongHistoryTests.stage

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-short-assembly-history-")
        self.root = Path(self.temp.name) / "书库"
        story.Book.create(self.root, "远处的账本", "short")
        self.book = story.Book(self.root)
        self.draft = self.root / "draft.md"
        self.texts = {}
        self.book.save_notes([{"id": "key", "kind": "fact", "text": "钥匙留在库房。",
                               "source": "作者设定"}], self.rev())
        for chapter in (1, 2):
            self.add(chapter)
            self.dep(chapter)
        assembled = self.book.assemble_short(2)
        self.assertTrue(assembled["exports_complete"], assembled)
        self.output = Path(assembled["path"])

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def assert_current(self):
        status = self.book.status()
        self.assertEqual(status["short_assembly"]["state"], "current", status)
        self.assertTrue(status["short_assembly"]["source_current"])
        self.assertEqual(status["pending_exports"], [])
        self.assertEqual(status["changed_exports"], [])
        self.assertTrue(status["integrity"]["full_book_verified"])

    def test_earlier_chapter_history_publish_updates_text_and_renamed_heading(self):
        old_path = self.root / self.book.chapter_path(1)
        old_prose = old_path.read_text(encoding="utf-8")
        old_full = self.output.read_bytes()
        second_path = self.root / self.book.chapter_path(2)
        second_prose = second_path.read_bytes()
        plan = self.book.get_plan(1)
        self.book.save_plan(1, {**plan, "title": "更正交接"}, self.rev())
        self.assert_current()  # A planning revision alone has not changed the source chapter.
        revised = self.texts[1].replace("一张收据", "两张收据")
        staged = self.stage(self.start(1), {1: revised})
        self.assertEqual(self.output.read_bytes(), old_full)

        result = history.branch_publish(self.book, staged["branch"], self.rev())

        self.assertTrue(result["committed"], result)
        self.assertTrue(result["exports_complete"], result)
        new_path = self.root / self.book.chapter_path(1)
        self.assertEqual(new_path.name, "第1章 更正交接.md")
        self.assertEqual(new_path.read_text(encoding="utf-8"), revised)
        self.assertFalse(old_path.exists())
        self.assertEqual(self.book._retired_chapters(), [])
        self.assertEqual(second_path.read_bytes(), second_prose)
        combined = self.output.read_text(encoding="utf-8")
        self.assertIn("第1章 更正交接\n\n" + revised.splitlines()[1], combined)
        self.assertNotIn("第1章 核对交接", combined)
        self.assertIn("第2章 核对交接\n\n" + self.texts[2].splitlines()[1], combined)
        self.assertTrue(any(Path(path).read_text(encoding="utf-8") == old_prose
                            for path in result["backups"]))
        self.assertTrue(any(Path(path).read_bytes() == old_full for path in result["backups"]))
        self.assert_current()

    def test_latest_chapter_reconcile_refreshes_combined_copy_after_review(self):
        first_path = self.root / self.book.chapter_path(1)
        first_prose = first_path.read_bytes()
        latest = self.root / self.book.chapter_path(2)
        old_full = self.output.read_bytes()
        outside = self.texts[2].replace("一张收据", "三张收据")
        latest.write_text(outside, encoding="utf-8")
        packet = self.book.reconcile(2)
        self.assertEqual(self.output.read_bytes(), old_full)
        self.assertEqual(packet["external_edit"]["sha256"], story.digest(outside))
        reviewed = outside + "她把收据分别封好。\n"
        self.draft.write_text(reviewed, encoding="utf-8")
        delta = {"book_id": self.book.meta("id"), "base_revision": packet["revision"],
                 "external_sha256": packet["external_edit"]["sha256"],
                 "summary": "她交出钥匙，将三张收据分别封存。", "changes": [],
                 "review": {"draft_sha256": story.digest(reviewed), "checks": {
                     name: {"note": "外部修改及最终封存动作已经核对。", "quote": "她把收据分别封好。"}
                     for name in story.CHECKS}, "issues": []}}

        result = self.book.reconcile(2, self.draft, delta)

        self.assertTrue(result["committed"], result)
        self.assertTrue(result["exports_complete"], result)
        self.assertEqual(first_path.read_bytes(), first_prose)
        self.assertEqual(latest.read_text(encoding="utf-8"), reviewed)
        combined = self.output.read_text(encoding="utf-8")
        self.assertIn("第2章 核对交接\n\n" + "\n".join(reviewed.splitlines()[1:]), combined)
        self.assertTrue(any(Path(path).read_bytes() == old_full for path in result["backups"]))
        self.assert_current()


if __name__ == "__main__":
    unittest.main()
