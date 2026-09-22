"""An ancestor changed inside a filesystem syscall must not redirect exports."""
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_chapter_layout as fixture


class RetirementPathRaceTests(unittest.TestCase):
    def setUp(self):
        self.case = fixture.ChapterLayoutTests()
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.root, self.book = self.case.root, self.case.book
        self.outside = self.root.parent / "outside"
        self.outside.mkdir()

    @contextmanager
    def swap_inside_replace(self, expected_source, swap_destination=False):
        story = fixture.story
        original_replace, original_bound = story.os.replace, story._bound_replace
        pending = {}
        result = {"raced": False}

        def bound(source, target):
            pending.update(source=source, target=target)
            return original_bound(source, target)

        def replace(source, target, *args, **kwargs):
            if not result["raced"] and pending.get("source") and pending["source"].path == expected_source:
                victim = pending["target"].path.parent if swap_destination else expected_source.parent
                moved = victim.with_name(victim.name + "-original")
                original_replace(victim, moved)
                victim.symlink_to(self.outside, target_is_directory=True)
                result.update(raced=True, moved=moved, backup=pending["target"].path)
            return original_replace(source, target, *args, **kwargs)

        with patch.object(story, "_bound_replace", side_effect=bound), \
                patch.object(story.os, "replace", side_effect=replace):
            yield result

    @unittest.skipUnless(os.name == "posix", "POSIX permits swapping an open parent directory")
    def test_retirement_source_parent_swap_cannot_displace_identical_outside_file(self):
        self.case.save_plan(1)
        self.case.commit(1)
        original = self.root / self.book.chapter_path(1)
        outside_file = self.outside / original.name
        outside_file.write_bytes(fixture.DRAFT.encode("utf-8"))
        self.case.save_plan(1, volume_dir="第二卷 旧城")
        with self.swap_inside_replace(original) as race:
            saved, _ = self.case.commit(1, replace_last=True)
        self.assertTrue(race["raced"])
        self.assertFalse(saved["exports_complete"])
        self.assertEqual(outside_file.read_bytes(), fixture.DRAFT.encode("utf-8"))
        self.assertEqual((race["moved"] / original.name).read_bytes(), fixture.DRAFT.encode("utf-8"))
        self.assertEqual(race["backup"].read_bytes(), fixture.DRAFT.encode("utf-8"))
        self.assertTrue(self.book._retired_chapters())

    @unittest.skipUnless(os.name == "posix", "POSIX permits swapping an open parent directory")
    def test_retirement_backup_parent_swap_cannot_write_into_outside_directory(self):
        self.case.save_plan(1)
        self.case.commit(1)
        original = self.root / self.book.chapter_path(1)
        marker = self.outside / "keep.txt"
        marker.write_bytes(b"outside marker")
        self.case.save_plan(1, volume_dir="第二卷 旧城")
        with self.swap_inside_replace(original, swap_destination=True) as race:
            saved, _ = self.case.commit(1, replace_last=True)
        self.assertTrue(race["raced"])
        self.assertFalse(saved["exports_complete"])
        self.assertEqual({p.name: p.read_bytes() for p in self.outside.iterdir()}, {"keep.txt": b"outside marker"})
        self.assertEqual(original.read_bytes(), fixture.DRAFT.encode("utf-8"))
        self.assertEqual((race["moved"] / original.name).read_bytes(), fixture.DRAFT.encode("utf-8"))

    @unittest.skipUnless(os.name == "posix", "Requires a directory symlink")
    def test_empty_outside_parent_cannot_hide_a_retired_chapter_after_preflight(self):
        self.case.save_plan(1)
        self.case.commit(1)
        original = self.root / self.book.chapter_path(1)
        moved = original.parent.with_name(original.parent.name + "-original")
        self.case.save_plan(1, volume_dir="第二卷 旧城")
        check = self.book._check_artifact
        retiring, raced = [], []

        def before_retirement(row):
            retiring.append(True)
            return original_retirement(row)

        def checked_then_swapped(relative, *args, **kwargs):
            target = check(relative, *args, **kwargs)
            if retiring and target == original and not raced:
                os.replace(original.parent, moved)
                original.parent.symlink_to(self.outside, target_is_directory=True)
                raced.append(True)
            return target

        original_retirement = self.book._retire_chapter
        with patch.object(self.book, "_retire_chapter", side_effect=before_retirement), \
                patch.object(self.book, "_check_artifact", side_effect=checked_then_swapped):
            saved, _ = self.case.commit(1, replace_last=True)
        self.assertTrue(raced)
        self.assertFalse(saved["exports_complete"])
        self.assertTrue(self.book._retired_chapters())
        self.assertEqual((moved / original.name).read_bytes(), fixture.DRAFT.encode("utf-8"))
        self.assertEqual(list(self.outside.iterdir()), [])

    @unittest.skipUnless(os.name == "posix", "POSIX permits swapping an open parent directory")
    def test_atomic_displacement_source_parent_swap_preserves_outside_inode(self):
        self.case.save_plan(1)
        self.case.commit(1)
        original = self.root / self.book.chapter_path(1)
        outside_file = self.outside / original.name
        outside_file.write_bytes(fixture.DRAFT.encode("utf-8"))
        outside_stat = outside_file.stat()
        revised = fixture.DRAFT + "她将空手藏进衣袖。\n"
        with self.swap_inside_replace(original) as race:
            saved, _ = self.case.commit(1, revised, replace_last=True)
        self.assertTrue(race["raced"])
        self.assertFalse(saved["exports_complete"])
        self.assertTrue(os.path.samestat(outside_stat, outside_file.stat()))
        self.assertEqual(outside_file.read_bytes(), fixture.DRAFT.encode("utf-8"))
        self.assertEqual(race["backup"].read_bytes(), fixture.DRAFT.encode("utf-8"))
        self.assertEqual((race["moved"] / original.name).read_bytes(), revised.encode("utf-8"))
        self.assertEqual(list(race["moved"].glob(".story-tmp-*")), [])

    @unittest.skipUnless(os.name == "posix", "POSIX permits swapping an open parent directory")
    def test_first_publication_parent_swap_cannot_create_outside_chapter(self):
        story = fixture.story
        self.case.save_plan(1)
        publish, link, replace = story._publish_no_replace, story.os.link, story.os.replace
        pending, raced = {}, []

        def remember(source, target):
            pending.update(source=source, target=target)
            return publish(source, target)

        def swap_then_link(source, target, *args, **kwargs):
            if not raced:
                # Give the redirected path matching bytes: checking only hashes
                # after a path-based syscall would incorrectly accept the escape.
                (self.outside / pending["source"].name).write_bytes(fixture.DRAFT.encode("utf-8"))
                parent = pending["target"].path.parent
                moved = parent.with_name(parent.name + "-original")
                replace(parent, moved)
                parent.symlink_to(self.outside, target_is_directory=True)
                raced.append(moved)
            return link(source, target, *args, **kwargs)

        with patch.object(story, "_publish_no_replace", side_effect=remember), \
                patch.object(story.os, "link", side_effect=swap_then_link):
            saved, _ = self.case.commit(1)
        self.assertTrue(raced)
        self.assertFalse(saved["exports_complete"])
        self.assertFalse((self.outside / "第1章 门后的雨.md").exists())
        self.assertEqual((raced[0] / "第1章 门后的雨.md").read_bytes(), fixture.DRAFT.encode("utf-8"))
        self.assertEqual(list(raced[0].glob(".story-tmp-*")), [])

    def test_unsupported_directory_binding_keeps_original_untouched(self):
        if os.name != "posix":
            self.skipTest("POSIX capability fallback")
        self.case.save_plan(1)
        self.case.commit(1)
        original = self.root / self.book.chapter_path(1)
        revised = fixture.DRAFT + "她将空手藏进衣袖。\n"
        with patch.object(fixture.story.os, "supports_dir_fd", set()):
            saved, _ = self.case.commit(1, revised, replace_last=True)
        self.assertFalse(saved["exports_complete"])
        self.assertEqual(saved["export_details"]["code"], "safe_export_unavailable")
        self.assertEqual(original.read_bytes(), fixture.DRAFT.encode("utf-8"))

    @unittest.skipUnless(sys.platform == "win32", "Requires native Windows directory share locks")
    def test_windows_parent_pin_blocks_rename_but_allows_child_publication(self):
        parent = self.root / "第一卷 雪夜𠮷📖"
        parent.mkdir()
        moved = parent.with_name("移走的卷目录𠮷")
        target = parent / "第1章 门后的雨📖.md"
        stage = parent / "待发布𠮷.tmp"
        revision = parent / "修订稿📖.tmp"
        with fixture.story._pinned_directory(parent):
            with self.assertRaises(OSError):
                os.replace(parent, moved)
            stage.write_bytes("首次正文。\n".encode("utf-8"))
            os.rename(stage, target)
            self.assertEqual(target.read_bytes(), "首次正文。\n".encode("utf-8"))
            revision.write_bytes("修订后的正文。\n".encode("utf-8"))
            os.replace(revision, target)
            self.assertEqual(target.read_bytes(), "修订后的正文。\n".encode("utf-8"))
            self.assertFalse(stage.exists())
            self.assertFalse(revision.exists())
        os.replace(parent, moved)
        self.assertEqual((moved / target.name).read_bytes(), "修订后的正文。\n".encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
