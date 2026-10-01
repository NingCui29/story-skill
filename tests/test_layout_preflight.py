from pathlib import Path
import os
import unittest
from unittest.mock import patch

import test_chapter_layout as layout


class LayoutPreflightTests(unittest.TestCase):
    def setUp(self):
        self.fixture = layout.ChapterLayoutTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def assert_rejected_without_changes(self, chapter, expected_code):
        fixture = self.fixture
        for operation in (fixture.book.lint, fixture.book.prepare):
            with self.subTest(operation=operation.__name__):
                before = fixture.state_snapshot()
                with self.assertRaises(layout.story.StoryError) as result:
                    operation(chapter, fixture.draft)
                self.assertEqual(result.exception.code, expected_code)
                self.assertEqual(fixture.state_snapshot(), before)

    def test_lint_and_prepare_reject_missing_volume_before_review_scaffold(self):
        fixture = self.fixture
        fixture.save_plan(1, volume_dir=None)
        fixture.draft.write_bytes(layout.DRAFT.encode("utf-8"))
        self.assert_rejected_without_changes(1, "volume_title_missing")
        self.assertFalse((fixture.root / "chapters").exists())

    def test_lint_and_prepare_reject_missing_chapter_title_before_review_scaffold(self):
        fixture = self.fixture
        fixture.save_plan(1)
        fixture.draft.write_bytes(layout.BODY.encode("utf-8"))
        self.assert_rejected_without_changes(1, "chapter_title_missing")
        self.assertFalse((fixture.root / "chapters").exists())

    def test_world_volume_path_preview_is_pure_and_matches_later_commit(self):
        fixture = self.fixture
        volume = {"id": "rain", "title": "第一卷 雨夜", "goal": "查清账本去向",
                  "entry_condition": "唯一的钥匙仍在手中", "exit_condition": "带回账本",
                  "cost": "失去退路", "evidence": {"kind": "author_plan", "note": "作者确定的卷纲"}}
        layout.story.world.save(fixture.book, {"volumes": [volume]}, fixture.book.meta("revision"))
        fixture.save_plan(1, volume="rain", volume_dir=None)
        fixture.draft.write_bytes(layout.DRAFT.encode("utf-8"))
        before = fixture.state_snapshot()
        expected = fixture.root / "chapters/第一卷 雨夜/第1章 门后的雨.md"

        lint = fixture.book.lint(1, fixture.draft)
        prepared = fixture.book.prepare(1, fixture.draft)
        self.assertTrue(lint["ok"])
        self.assertFalse(prepared["ready_to_commit"])
        self.assertEqual(Path(lint["path"]), expected)
        self.assertEqual(Path(prepared["lint"]["path"]), expected)
        self.assertEqual(fixture.state_snapshot(), before)
        self.assertFalse((fixture.root / "chapters").exists())
        self.assertIsNone(fixture.book.db.execute(
            "SELECT value FROM meta WHERE key='volume_dir:rain'").fetchone())

        committed, _ = fixture.commit(1)
        self.assertTrue(committed["exports_complete"])
        self.assertEqual(committed["path"], lint["path"])
        self.assertEqual(committed["path"], prepared["lint"]["path"])
        self.assertEqual(expected.read_bytes(), layout.DRAFT.encode("utf-8"))

    def test_lint_and_prepare_reject_conflicting_directory_for_established_volume(self):
        fixture = self.fixture
        volume = {"id": "rain", "title": "第一卷 雨夜", "goal": "查清账本去向",
                  "entry_condition": "唯一的钥匙仍在手中", "exit_condition": "带回账本",
                  "cost": "失去退路", "evidence": {"kind": "author_plan", "note": "作者确定的卷纲"}}
        layout.story.world.save(fixture.book, {"volumes": [volume]}, fixture.book.meta("revision"))
        fixture.save_plan(1, volume="rain")
        fixture.commit(1)
        # Older runtimes could save this conflicting plan; read paths still reject it.
        with patch.object(fixture.book, "_check_plan_volume_binding"), patch.object(fixture.book, "_check_unique_names"):
            fixture.save_plan(2, volume="rain", volume_dir="第一卷 改名")
        fixture.draft.write_text("第2章 第二夜\n" + layout.BODY, encoding="utf-8")
        self.assert_rejected_without_changes(2, "volume_directory_conflict")
        self.assertFalse((fixture.root / "chapters/第一卷 改名").exists())
        self.assertEqual(fixture.book.chapter_path(1), "chapters/第一卷 雨夜/第1章 门后的雨.md")

    def check_invalid_target(self, kind, expected_code):
        fixture = self.fixture
        fixture.save_plan(1)
        fixture.draft.write_bytes(layout.DRAFT.encode("utf-8"))
        target = fixture.root / "chapters/第一卷 雨夜/第1章 门后的雨.md"
        target.parent.mkdir(parents=True)
        outside = Path(fixture.temp.name) / "outside.md"
        outside.write_bytes(layout.DRAFT.encode("utf-8"))
        if kind == "occupied":
            target.write_bytes("用户另存的正文，必须保留。\n".encode("utf-8"))
        elif kind == "symlink":
            try:
                target.symlink_to(outside)
            except OSError as error:
                self.skipTest(f"Creating symbolic links is unavailable: {error}")
        else:
            try:
                os.link(outside, target)
            except OSError as error:
                self.skipTest(f"Creating hard links is unavailable: {error}")
        target_before = target.read_bytes()
        outside_before = outside.read_bytes()
        self.assert_rejected_without_changes(1, expected_code)
        before = fixture.state_snapshot()
        with self.assertRaises(layout.story.StoryError) as result:
            fixture.commit(1)
        self.assertEqual(result.exception.code, expected_code)
        self.assertEqual(fixture.state_snapshot(), before)
        self.assertEqual(target.read_bytes(), target_before)
        self.assertEqual(outside.read_bytes(), outside_before)

    def test_occupied_destination_is_rejected_before_review(self):
        self.check_invalid_target("occupied", "export_conflict")

    def test_symbolic_link_destination_is_rejected_before_review(self):
        self.check_invalid_target("symlink", "linked_path")

    def test_hard_link_destination_is_rejected_before_review(self):
        self.check_invalid_target("hardlink", "export_path_alias")

    def test_case_alias_rename_is_rejected_before_review(self):
        fixture = self.fixture
        fixture.save_plan(1, title="AI来客")
        original = "第1章 AI来客\n" + layout.BODY
        fixture.commit(1, original)
        old_relative = fixture.book.chapter_path(1)
        old = fixture.root / old_relative
        alias = old.with_name("第1章 Ai来客.md")
        if not alias.exists() or not alias.samefile(old):
            self.skipTest("Filesystem distinguishes names that differ only in letter case")
        fixture.save_plan(1, title="Ai来客")
        self.assert_rejected_without_changes(1, "export_path_alias")
        before = fixture.state_snapshot()
        with self.assertRaises(layout.story.StoryError) as result:
            fixture.commit(1, original, replace_last=True)
        self.assertEqual(result.exception.code, "export_path_alias")
        self.assertEqual(fixture.state_snapshot(), before)
        self.assertEqual(fixture.book.chapter_path(1), old_relative)
        self.assertEqual(old.read_bytes(), original.encode("utf-8"))
        self.assertEqual([path.name for path in old.parent.iterdir()], [old.name])

    def check_revised_reconcile_preparation(self, original, external, reviewed):
        fixture = self.fixture
        fixture.draft.write_bytes(reviewed.encode("utf-8"))
        before = fixture.state_snapshot()
        lint = fixture.book.lint(1, fixture.draft)
        self.assertTrue(lint["ok"])
        self.assertEqual(Path(lint["path"]), original)
        self.assertEqual(lint["external_edit"],
                         {"path": str(original), "sha256": layout.story.digest(external)})
        self.assertEqual(fixture.state_snapshot(), before)
        self.assertEqual(original.read_bytes(), external.encode("utf-8"))
        with self.assertRaises(layout.story.StoryError) as result:
            fixture.book.commit(1, fixture.draft, fixture.delta(reviewed), replace_last=True)
        self.assertEqual(result.exception.code, "exports_unresolved")
        self.assertEqual(fixture.state_snapshot(), before)
        self.assertEqual(original.read_bytes(), external.encode("utf-8"))
        prepared = fixture.book.prepare(1, fixture.draft, reconcile=True)
        self.assertEqual(prepared["delta"]["external_sha256"], layout.story.digest(external))
        self.assertEqual(prepared["delta"]["review"]["draft_sha256"], layout.story.digest(reviewed))
        self.assertEqual(Path(prepared["lint"]["path"]), original)
        self.assertEqual(fixture.state_snapshot(), before)
        self.assertEqual(original.read_bytes(), external.encode("utf-8"))
        delta = fixture.delta(reviewed)
        delta["external_sha256"] = prepared["delta"]["external_sha256"]
        committed = fixture.book.reconcile(1, fixture.draft, delta)
        self.assertTrue(committed["exports_complete"])
        self.assertEqual(committed["path"], prepared["lint"]["path"])
        self.assertEqual(original.read_bytes(), reviewed.encode("utf-8"))

    def test_reconcile_preparation_accepts_further_revised_current_export(self):
        fixture = self.fixture
        fixture.save_plan(1)
        fixture.commit(1)
        original = fixture.root / fixture.book.chapter_path(1)
        external = layout.DRAFT + "她停下脚步。\n"
        original.write_bytes(external.encode("utf-8"))
        reviewed = external + "她决定回去。\n"
        self.check_revised_reconcile_preparation(original, external, reviewed)

    def test_reconcile_preparation_accepts_further_revised_retired_name(self):
        fixture = self.fixture
        fixture.save_plan(1)
        fixture.commit(1)
        original = fixture.root / fixture.book.chapter_path(1)
        intermediate_text = "第1章 新的入口\n" + layout.BODY + "她没有回头。\n"
        with patch.object(layout.story, "atomic_write", side_effect=OSError("publication interrupted")):
            fixture.commit(1, intermediate_text, replace_last=True)
        intermediate = fixture.root / fixture.book.chapter_path(1)
        external = layout.DRAFT + "她停下脚步。\n"
        original.write_bytes(external.encode("utf-8"))
        fixture.book.export(safe_only=True)
        reviewed = external + "她决定回去。\n"
        self.check_revised_reconcile_preparation(original, external, reviewed)
        self.assertFalse(intermediate.exists())


if __name__ == "__main__":
    unittest.main()
