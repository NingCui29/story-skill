"""Volume identities and managed paths must keep one unambiguous destination."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

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

    def test_same_ordinal_without_volume_id_cannot_name_two_planned_directories(self):
        self.case.save_plan(1, title="初雨")
        self.assert_rejected("volume_directory_conflict", lambda: self.case.save_plan(
            2, title="第二夜", volume_dir="第一卷 改名"))
        self.assert_rejected("volume_directory_conflict", lambda: self.case.save_plan(
            2, title="第二夜", volume_dir="第1卷 雨夜"))
        self.case.save_plan(2, title="第二夜", volume_dir="第一卷 雨夜")

    def test_committed_directory_keeps_ordinal_when_its_plan_changes(self):
        self.case.save_plan(1, title="初雨")
        self.case.commit(1, "第1章 初雨\n" + fixture.BODY)
        self.case.save_plan(1, title="初雨", volume_dir="第二卷 旧城")
        self.assert_rejected("volume_directory_conflict", lambda: self.case.save_plan(
            2, title="第二夜", volume_dir="第一卷 改名"))

    def test_same_volume_id_cannot_split_new_chapters_across_directories(self):
        self.seed_volume()
        self.assert_rejected("volume_directory_conflict", lambda: self.case.save_plan(
            2, volume="rain", volume_dir="第一卷 改名"))
        self.assertEqual(self.book.meta("volume_dir:rain"), "第一卷 雨夜")

        self.case.save_plan(2, volume="rain", volume_dir=None, title="第二夜")
        self.assertTrue(self.case.commit(2, "第2章 第二夜\n" + fixture.BODY)[0]["exports_complete"])
        self.assertEqual(Path(self.book.chapter_path(1)).parent, Path(self.book.chapter_path(2)).parent)

    def test_latest_revision_cannot_rebind_existing_volume_identity(self):
        self.seed_volume()
        self.assert_rejected("volume_directory_conflict", lambda: self.case.save_plan(
            1, volume="rain", volume_dir="第一卷 改名"))

    def test_volume_id_does_not_defer_ordinal_conflict_at_plan_save(self):
        self.case.save_plan(1, title="初雨", volume_dir="第一卷 雨夜")
        self.assert_rejected("volume_directory_conflict", lambda: self.case.save_plan(
            2, volume="unregistered", volume_dir="第1卷 改名", title="第二夜"))

    def test_uncommitted_volume_id_keeps_one_directory_across_plans(self):
        self.case.save_plan(1, volume="rain", volume_dir="第一卷 雨夜", title="初雨")
        self.assert_rejected("volume_directory_conflict", lambda: self.case.save_plan(
            2, volume="rain", volume_dir="第二卷 旧城", title="第二夜"))

    def test_implicit_world_directory_cannot_split_an_uncommitted_volume_id(self):
        volume = {"id": "rain", "title": "第二卷 旧城", "goal": "查清账本去向",
                  "entry_condition": "钥匙在手", "exit_condition": "带回账本", "cost": "失去退路",
                  "evidence": {"kind": "author_plan", "note": "作者确定的卷纲"}}
        fixture.story.world.save(self.book, {"volumes": [volume]}, self.book.meta("revision"))
        self.case.save_plan(1, volume="rain", volume_dir="第一卷 雨夜", title="初雨")
        self.assert_rejected("volume_directory_conflict", lambda: self.case.save_plan(
            2, volume="rain", volume_dir=None, title="第二夜"))

    def test_distinct_volume_ids_cannot_claim_one_numbered_directory(self):
        record = {"title": "第一卷 雨夜", "goal": "查清账本去向",
                  "entry_condition": "钥匙在手", "exit_condition": "带回账本", "cost": "失去退路",
                  "evidence": {"kind": "author_plan", "note": "作者确定的卷纲"}}
        fixture.story.world.save(self.book, {"volumes": [{"id": "rain", **record},
                                                        {"id": "storm", **record}]}, self.book.meta("revision"))
        self.case.save_plan(1, volume="rain", title="初雨")
        self.assert_rejected("volume_directory_conflict", lambda: self.case.save_plan(
            2, volume="storm", title="第二夜"))
        self.case.commit(1, "第1章 初雨\n" + fixture.BODY)
        self.book.save_notes([{"id": "rain-rule", "kind": "fact", "text": "本卷钥匙须归档。",
                              "source": "作者设定", "scope": "volume:rain", "critical": True}],
                             self.book.meta("revision"))
        self.assert_rejected("volume_directory_conflict", lambda: self.case.save_plan(
            2, volume="storm", title="第二夜"))
        self.assertIsNone(self.book.db.execute("SELECT data FROM plans WHERE chapter=2").fetchone())
        self.assertEqual(self.book.meta("volume_dir:rain"), "第一卷 雨夜")

    def test_registered_volume_owner_survives_a_later_plan_without_id(self):
        self.seed_volume()
        self.case.save_plan(1, title="门后的雨")
        self.assert_rejected("volume_directory_conflict", lambda: self.case.save_plan(
            2, volume="other", title="第二夜"))

    def test_context_rejects_legacy_duplicate_registered_volume_owners(self):
        self.seed_volume()
        self.case.save_plan(2, volume="rain", title="第二夜")
        with self.book.transaction():
            self.book.set_meta("volume_dir:other", "第一卷 雨夜")
        self.assert_rejected("volume_directory_conflict", lambda: self.book.context(2))

    def test_legacy_historical_plan_cannot_reset_current_volume_binding(self):
        self.seed_volume()
        # Older runtimes allowed a later chapter to overwrite this binding while
        # leaving an earlier chapter and its saved plan in the original directory.
        with self.book.transaction():
            self.book.set_meta("volume_dir:rain", "第一卷 改名")
            self.book.db.execute("INSERT INTO plans(chapter,data) VALUES (?,?)", (
                2, fixture.story.dumps(fixture.story.valid_plan(self.case.plan(
                    volume_dir=None, volume="rain", title="第二夜")))))
        second = "第2章 第二夜\n" + fixture.BODY
        with patch.object(self.book, "_check_unique_names"), patch.object(self.book, "_check_plan_volume_binding"):
            self.case.commit(2, second)
        for chapter in (1, 2):
            fixture.story.history.save_dependencies(self.book, {
                "chapter": chapter, "chapter_sha": fixture.story.digest(fixture.DRAFT if chapter == 1 else second),
                "dependencies": [], "complete": True, "note": "两章独立内容无已知历史依赖。"},
                self.book.meta("revision"))
        packet = fixture.story.history.branch_start(self.book, 1, self.book.meta("revision"))
        self.case.stage_history(packet, {1: fixture.DRAFT + "她将空手藏进衣袖。\n"})
        self.assert_rejected("volume_directory_conflict", lambda: fixture.story.history.branch_publish(
            self.book, packet["branch"], self.book.meta("revision")))
        self.assertEqual(self.book.meta("volume_dir:rain"), "第一卷 改名")
        self.assert_rejected("volume_directory_conflict", lambda: self.case.save_plan(
            3, volume="rain", volume_dir=None, title="第三夜"))

    def assert_directory_alias_rejected(self, original, alias):
        self.case.save_plan(1, volume="original", volume_dir=original)
        self.case.commit(1)
        original_path = self.root / "chapters" / original
        alias_path = self.root / "chapters" / alias
        if not alias_path.exists() or not original_path.samefile(alias_path):
            self.skipTest("Filesystem keeps these directory names distinct")
        # Simulate a plan saved by an older runtime to exercise the filesystem fence.
        with patch.object(self.book, "_check_unique_names"), patch.object(self.book, "_check_plan_volume_binding"):
            self.case.save_plan(2, volume="alias", volume_dir=alias)
            self.assert_rejected("export_path_alias", lambda: self.case.commit(
                2, "第2章 第二夜\n" + fixture.BODY))
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
        with patch.object(self.book, "_check_unique_names"), patch.object(self.book, "_check_plan_volume_binding"):
            self.case.save_plan(2, volume="alias", volume_dir=alias)
            self.assert_rejected("export_path_alias", lambda: self.case.commit(
                2, "第2章 第二夜\n" + fixture.BODY))
        self.assertEqual(self.book.meta("volume_dir:original"), original)
        self.assertIsNone(self.book.db.execute("SELECT value FROM meta WHERE key='volume_dir:alias'").fetchone())

    def test_new_chapter_cannot_adopt_a_hard_link_to_another_chapter(self):
        self.case.save_plan(1, title="初雨")
        first_text = "第1章 初雨\n" + fixture.BODY
        self.case.commit(1, first_text)
        first = self.root / self.book.chapter_path(1)
        second = first.parent / "第2章 门后的雨.md"
        try:
            os.link(first, second)
        except OSError:
            self.skipTest("Filesystem does not support hard links")
        self.case.save_plan(2)
        second_text = "第2章 门后的雨\n" + fixture.BODY
        rejected = self.assert_rejected("exports_unresolved", lambda: self.case.commit(2, second_text))
        self.assertIn(self.book.chapter_path(1), rejected.details["pending"])
        self.assertTrue(first.samefile(second))
        self.assertEqual(first.read_bytes(), first_text.encode("utf-8"))
        self.assertEqual(second.read_bytes(), first_text.encode("utf-8"))
        self.assert_rejected("export_path_alias", lambda: self.book.lint(2, self.case.draft))

        recovered = self.book.export(safe_only=True)
        self.assertTrue(recovered["exports_complete"])
        self.assertFalse(first.samefile(second))
        self.assertEqual(first.read_bytes(), first_text.encode("utf-8"))
        self.assertEqual(second.read_bytes(), first_text.encode("utf-8"))
        self.assertTrue(any(Path(path).samefile(second) for path in recovered["backups"]))
        self.assert_rejected("export_path_alias", lambda: self.case.commit(2, second_text))


if __name__ == "__main__":
    unittest.main()
