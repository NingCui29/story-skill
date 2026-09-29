"""Volume identities and managed paths must keep one unambiguous destination."""
import os
from pathlib import Path
import unittest

import test_chapter_layout as fixture


class VolumeConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.case = fixture.ChapterLayoutTests()
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.book = self.case.book
        self.root = self.case.root

    def snapshot(self):
        tables = [row[0] for row in self.book.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        state = {table: [tuple(row) for row in self.book.db.execute(f'SELECT * FROM "{table}" ORDER BY 1')]
                 for table in tables}
        files = {str(path.relative_to(self.root)): path.read_bytes()
                 for path in (self.root / "chapters").rglob("*.md")}
        return state, files

    def assert_rejected(self, code, operation):
        before = self.snapshot()
        with self.assertRaises(fixture.story.StoryError) as raised:
            operation()
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(self.snapshot(), before)
        return raised.exception

    def seed_volume(self):
        volume = {"id": "rain", "title": "第一卷 雨夜", "goal": "查清账本去向",
                  "entry_condition": "钥匙在手", "exit_condition": "带回账本", "cost": "失去退路",
                  "evidence": {"kind": "author_plan", "note": "作者确定的卷纲"}}
        fixture.story.world.save(self.book, {"volumes": [volume]}, self.book.meta("revision"))
        self.case.save_plan(1, volume="rain")
        self.case.commit(1)

    def test_same_volume_id_cannot_split_new_chapters_across_directories(self):
        self.seed_volume()
        self.case.save_plan(2, volume="rain", volume_dir="第一卷 改名")
        self.assert_rejected("volume_directory_conflict", lambda: self.case.commit(2))
        self.assertEqual(self.book.meta("volume_dir:rain"), "第一卷 雨夜")

        self.case.save_plan(2, volume="rain", volume_dir=None, title="第二夜")
        self.assertTrue(self.case.commit(2)[0]["exports_complete"])
        self.assertEqual(Path(self.book.chapter_path(1)).parent, Path(self.book.chapter_path(2)).parent)

    def test_latest_revision_cannot_rebind_existing_volume_identity(self):
        self.seed_volume()
        self.case.save_plan(1, volume="rain", volume_dir="第一卷 改名")
        revised = fixture.DRAFT + "她将空手藏进衣袖。\n"
        self.assert_rejected("volume_directory_conflict", lambda: self.case.commit(1, revised, replace_last=True))

    def test_legacy_historical_plan_cannot_reset_current_volume_binding(self):
        self.seed_volume()
        # Older runtimes allowed a later chapter to overwrite this binding while
        # leaving an earlier chapter and its saved plan in the original directory.
        with self.book.transaction():
            self.book.set_meta("volume_dir:rain", "第一卷 改名")
        self.case.save_plan(2, volume="rain", volume_dir=None, title="第二夜")
        self.case.commit(2)
        for chapter in (1, 2):
            fixture.story.history.save_dependencies(self.book, {
                "chapter": chapter, "chapter_sha": fixture.story.digest(fixture.DRAFT),
                "dependencies": [], "complete": True, "note": "两章独立内容无已知历史依赖。"},
                self.book.meta("revision"))
        packet = fixture.story.history.branch_start(self.book, 1, self.book.meta("revision"))
        self.case.stage_history(packet, {1: fixture.DRAFT + "她将空手藏进衣袖。\n"})
        self.assert_rejected("volume_directory_conflict", lambda: fixture.story.history.branch_publish(
            self.book, packet["branch"], self.book.meta("revision")))
        self.assertEqual(self.book.meta("volume_dir:rain"), "第一卷 改名")
        self.case.save_plan(3, volume="rain", volume_dir=None, title="第三夜")
        self.assertTrue(self.case.commit(3)[0]["exports_complete"])
        self.assertEqual(Path(self.book.chapter_path(3)).parent.as_posix(), "chapters/第一卷 改名")

    def assert_directory_alias_rejected(self, original, alias):
        self.case.save_plan(1, volume="original", volume_dir=original)
        self.case.commit(1)
        original_path = self.root / "chapters" / original
        alias_path = self.root / "chapters" / alias
        if not alias_path.exists() or not original_path.samefile(alias_path):
            self.skipTest("Filesystem keeps these directory names distinct")
        self.case.save_plan(2, volume="alias", volume_dir=alias)
        self.assert_rejected("export_path_alias", lambda: self.case.commit(2))
        self.assertIsNone(self.book.db.execute("SELECT value FROM meta WHERE key='volume_dir:alias'").fetchone())

    def test_new_chapter_rejects_case_alias_of_registered_volume_directory(self):
        self.assert_directory_alias_rejected("第一卷 AI来客", "第一卷 Ai来客")

    def test_new_chapter_rejects_unicode_alias_of_registered_volume_directory(self):
        self.assert_directory_alias_rejected("第一卷 Café", "第一卷 Cafe\u0301")

    def test_external_case_only_folder_rename_cannot_register_a_second_volume_spelling(self):
        original, alias = "第一卷 AI来客", "第一卷 Ai来客"
        self.case.save_plan(1, volume="original", volume_dir=original)
        self.case.commit(1)
        original_path = self.root / "chapters" / original
        alias_path = self.root / "chapters" / alias
        if not alias_path.exists() or not original_path.samefile(alias_path):
            self.skipTest("Filesystem keeps these directory names distinct")
        os.rename(original_path, alias_path)
        self.case.save_plan(2, volume="alias", volume_dir=alias)
        self.assert_rejected("export_path_alias", lambda: self.case.commit(2))
        self.assertEqual(self.book.meta("volume_dir:original"), original)
        self.assertIsNone(self.book.db.execute("SELECT value FROM meta WHERE key='volume_dir:alias'").fetchone())

    def test_new_chapter_cannot_adopt_a_hard_link_to_another_chapter(self):
        self.case.save_plan(1, title="初雨")
        self.case.commit(1)
        first = self.root / self.book.chapter_path(1)
        second = first.parent / "第2章 门后的雨.md"
        try:
            os.link(first, second)
        except OSError:
            self.skipTest("Filesystem does not support hard links")
        self.case.save_plan(2)
        rejected = self.assert_rejected("exports_unresolved", lambda: self.case.commit(2))
        self.assertIn(self.book.chapter_path(1), rejected.details["pending"])
        self.assertTrue(first.samefile(second))
        self.assertEqual(first.read_bytes(), fixture.DRAFT.encode("utf-8"))
        self.assertEqual(second.read_bytes(), fixture.DRAFT.encode("utf-8"))
        self.assert_rejected("export_path_alias", lambda: self.book.lint(2, self.case.draft))

        recovered = self.book.export(safe_only=True)
        self.assertTrue(recovered["exports_complete"])
        self.assertFalse(first.samefile(second))
        self.assertEqual(first.read_bytes(), fixture.DRAFT.encode("utf-8"))
        self.assertEqual(second.read_bytes(), fixture.DRAFT.encode("utf-8"))
        self.assertTrue(any(Path(path).samefile(second) for path in recovered["backups"]))
        self.assert_rejected("export_path_alias", lambda: self.case.commit(2))


if __name__ == "__main__":
    unittest.main()
