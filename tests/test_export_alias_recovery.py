import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_chapter_layout as layout


class ExportAliasRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = layout.ChapterLayoutTests()
        self.fixture.setUp()
        self.fixture.save_plan(1)
        self.fixture.commit(1)

    def tearDown(self):
        self.fixture.tearDown()

    def link(self, source, target):
        try:
            os.link(source, target)
        except OSError as error:
            self.skipTest(f"Creating hard links is unavailable: {error}")

    def test_same_body_rename_detaches_hard_link_inserted_before_export(self):
        fixture = self.fixture
        old = fixture.root / fixture.book.chapter_path(1)
        fixture.save_plan(1, volume_dir="第二卷 渡口")
        relative = "chapters/第二卷 渡口/第1章 门后的雨.md"
        target = fixture.root / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(layout.DRAFT.encode("utf-8"))
        outside = Path(fixture.temp.name) / "outside.md"
        outside.write_bytes(layout.DRAFT.encode("utf-8"))
        export = fixture.book.export

        def link_after_queue(*args, **kwargs):
            target.unlink()
            self.link(outside, target)
            return export(*args, **kwargs)

        with patch.object(fixture.book, "export", side_effect=link_after_queue):
            result, _ = fixture.commit(1, replace_last=True)
        self.assertTrue(result["exports_complete"])
        self.assertEqual(target.read_bytes(), layout.DRAFT.encode("utf-8"))
        self.assertFalse(target.samefile(outside))
        self.assertFalse(old.exists())
        self.assertIn(relative, result["exported"])
        self.assertTrue(any(Path(path).samefile(outside) for path in result["backups"]))
        outside.write_bytes("外部文件后来另行修改。\n".encode("utf-8"))
        self.assertEqual(target.read_bytes(), layout.DRAFT.encode("utf-8"))

    def test_known_linked_export_is_pending_until_safely_detached(self):
        fixture = self.fixture
        fixture.book.integrity = "local"
        relative = fixture.book.chapter_path(1)
        target = fixture.root / relative
        outside = Path(fixture.temp.name) / "outside.md"
        self.link(target, outside)
        fixture.save_plan(2)
        before = fixture.book.meta("revision")
        self.assertIn(relative, fixture.book.status()["pending_exports"])
        with self.assertRaises(layout.story.StoryError) as result:
            fixture.book.context(2)
        self.assertEqual(result.exception.code, "exports_unresolved")
        self.assertIn(relative, result.exception.details["pending"])

        recovered = fixture.book.export(safe_only=True)
        self.assertTrue(recovered["scope_exports_complete"])
        self.assertEqual(recovered["exported"], [relative])
        self.assertEqual(fixture.book.meta("revision"), before)
        self.assertFalse(target.samefile(outside))
        self.assertTrue(any(Path(path).samefile(outside) for path in recovered["backups"]))
        outside.write_bytes("外部文件后来另行修改。\n".encode("utf-8"))
        self.assertEqual(target.read_bytes(), layout.DRAFT.encode("utf-8"))
        self.assertEqual(fixture.book.context(2)["chapter"], 2)

    def test_link_recreated_after_publication_is_reported_and_can_be_recovered(self):
        fixture = self.fixture
        relative = fixture.book.chapter_path(1)
        target = fixture.root / relative
        outside = Path(fixture.temp.name) / "outside.md"
        self.link(target, outside)
        write = layout.story.atomic_write

        def link_after_publication(path, *args, **kwargs):
            saved = write(path, *args, **kwargs)
            if Path(path) == target:
                target.unlink()
                self.link(outside, target)
            return saved

        with patch.object(layout.story, "atomic_write", side_effect=link_after_publication):
            result = fixture.book.export(safe_only=True)
        self.assertFalse(result["exports_complete"])
        self.assertFalse(result["scope_exports_complete"])
        self.assertIn(relative, result["pending_exports"] + result["changed_exports"])
        self.assertEqual(outside.read_bytes(), layout.DRAFT.encode("utf-8"))
        retry = fixture.book.export(safe_only=True)
        self.assertTrue(retry["exports_complete"])
        self.assertFalse(target.samefile(outside))
        self.assertEqual(target.read_bytes(), layout.DRAFT.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
