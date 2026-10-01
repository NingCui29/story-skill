from contextlib import contextmanager
from pathlib import Path
import unittest
from unittest.mock import patch

import test_chapter_layout as layout


class LayoutRecoveryEdgeTests(unittest.TestCase):
    @contextmanager
    def fixture(self, integrity):
        fixture = layout.ChapterLayoutTests()
        fixture.setUp()
        fixture.book.integrity = integrity
        try:
            yield fixture
        finally:
            fixture.tearDown()

    def interrupted_rename_with_old_path_edit(self, fixture):
        fixture.save_plan(1)
        fixture.commit(1)
        old_relative = fixture.book.chapter_path(1)
        old = fixture.root / old_relative
        intermediate_text = "第1章 新的入口\n" + layout.BODY + "她没有回头。\n"
        with patch.object(layout.story, "atomic_write", side_effect=OSError("publication interrupted")):
            failed, _ = fixture.commit(1, intermediate_text, replace_last=True)
        self.assertFalse(failed["exports_complete"])
        intermediate = fixture.root / fixture.book.chapter_path(1)
        self.assertFalse(intermediate.exists())
        external = layout.DRAFT + "她停下脚步。\n"
        old.write_bytes(external.encode("utf-8"))
        recovery = fixture.book.export(safe_only=True)
        self.assertFalse(recovery["exports_complete"])
        self.assertIn(old_relative, recovery["changed_exports"])
        self.assertEqual(intermediate.read_bytes(), intermediate_text.encode("utf-8"))
        return old_relative, old, intermediate, external, intermediate_text

    def verify_rename_back(self, integrity, revise_reviewed_body):
        with self.fixture(integrity) as fixture:
            relative, old, intermediate, external, intermediate_text = \
                self.interrupted_rename_with_old_path_edit(fixture)
            packet = fixture.book.reconcile(1)
            self.assertEqual(Path(packet["external_edit"]["path"]), old)
            reviewed = external + "她决定回去。\n" if revise_reviewed_body else external
            fixture.draft.write_bytes(reviewed.encode("utf-8"))
            delta = fixture.delta(reviewed)
            delta["external_sha256"] = packet["external_edit"]["sha256"]

            result = fixture.book.reconcile(1, fixture.draft, delta)
            self.assertTrue(result["committed"])
            self.assertTrue(result["scope_exports_complete"])
            self.assertEqual(fixture.book.chapter_path(1), relative)
            self.assertEqual(old.read_bytes(), reviewed.encode("utf-8"))
            self.assertFalse(intermediate.exists())
            backups = {Path(path).read_bytes() for path in result["backups"]}
            self.assertIn(intermediate_text.encode("utf-8"), backups)
            if revise_reviewed_body:
                self.assertIn(external.encode("utf-8"), backups)
            self.assertEqual(fixture.book._retired_chapters(), [])
            revision = fixture.book.meta("revision")
            fixture.book.close()
            fixture.book = layout.story.Book(fixture.root, integrity=integrity)
            retry = fixture.book.reconcile(1, fixture.draft, delta)
            self.assertTrue(retry["idempotent"])
            self.assertTrue(retry["scope_exports_complete"])
            self.assertEqual(fixture.book.meta("revision"), revision)
            self.assertEqual(old.read_bytes(), reviewed.encode("utf-8"))
            self.assertEqual(fixture.book.export(safe_only=True)["exported"], [])

    def test_reusing_retired_name_keeps_current_export_present_after_reconcile(self):
        for integrity in ("strict", "local"):
            with self.subTest(integrity=integrity):
                self.verify_rename_back(integrity, revise_reviewed_body=False)

    def test_reusing_retired_name_accepts_reviewed_old_path_hash_for_revised_body(self):
        for integrity in ("strict", "local"):
            with self.subTest(integrity=integrity):
                self.verify_rename_back(integrity, revise_reviewed_body=True)

    def test_export_clears_legacy_self_retirement_without_moving_current_body(self):
        for integrity in ("strict", "local"):
            for recorded_body in (layout.DRAFT, layout.BODY):
                with self.subTest(integrity=integrity, old_body_matches=recorded_body == layout.DRAFT):
                    with self.fixture(integrity) as fixture:
                        fixture.save_plan(1)
                        fixture.commit(1)
                        relative = fixture.book.chapter_path(1)
                        current = fixture.root / relative
                        inode = current.stat().st_ino
                        sha = layout.story.digest(recorded_body)
                        with fixture.book.transaction():
                            fixture.book.set_meta("chapter_retired:" + relative,
                                                  {"path": relative, "destination": relative,
                                                   "sha": sha, "written_sha": sha})
                        revision = fixture.book.meta("revision")
                        recovered = fixture.book.export(safe_only=True)
                        self.assertTrue(recovered["scope_exports_complete"])
                        self.assertEqual(recovered["exported"], [])
                        self.assertEqual(recovered["backups"], [])
                        self.assertEqual(current.read_bytes(), layout.DRAFT.encode("utf-8"))
                        self.assertEqual(current.stat().st_ino, inode)
                        self.assertEqual(fixture.book._retired_chapters(), [])
                        self.assertEqual(fixture.book.meta("revision"), revision)


if __name__ == "__main__":
    unittest.main()
