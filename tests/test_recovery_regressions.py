import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch


TOOL = Path(__file__).resolve().parents[1] / "skills/story-skill/scripts/story.py"
spec = importlib.util.spec_from_file_location("story_recovery", TOOL)
story = importlib.util.module_from_spec(spec)
spec.loader.exec_module(story)

DRAFT = "# 第1章 门后的雨\n沈禾把唯一的钥匙交给守门人。\n她答应在天亮之前带回账本。\n"
QUOTE = "沈禾把唯一的钥匙交给守门人。"


class RecoveryRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-recovery-test-")
        self.root = Path(self.temp.name).resolve() / "book"
        story.Book.create(self.root, "门后的雨", "long")
        self.book = story.Book(self.root)
        self.draft = self.root / ".story/drafts/first.md"
        self.draft.parent.mkdir(parents=True)
        self.draft.write_bytes(DRAFT.encode("utf-8"))
        self.save_plan(1)

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def save_plan(self, chapter):
        plan = {"volume_dir": "第一卷 雨夜", "goal": "用钥匙换取入口", "stop": "交出钥匙后停笔",
                "beats": [{"choice": "沈禾交出钥匙", "change": "失去退路"}],
                "constraints": [], "requires": [], "tags": [], "length": [20, 120]}
        self.book.save_plan(chapter, plan, self.book.meta("revision"))

    def delta(self, text=DRAFT):
        return {"book_id": self.book.meta("id"), "base_revision": self.book.meta("revision"),
                "summary": "沈禾交出钥匙，承诺天亮前返回。", "changes": [],
                "review": {"draft_sha256": story.digest(text), "checks": {
                    check: {"note": "选择与代价在正文中明确出现。", "quote": QUOTE}
                    for check in story.CHECKS}, "issues": []}}

    def test_edit_after_export_preflight_is_preserved_and_reported(self):
        self.book.commit(1, self.draft, self.delta())
        self.save_plan(2)
        second_delta = self.delta()
        self.book.commit(2, self.draft, second_delta)
        revision = self.book.meta("revision")
        first_path = self.root / self.book.chapter_path(1)
        user_edit = "用户刚刚保存的新稿，必须保留。\n".encode("utf-8")
        original_check = self.book._check_artifact
        edited = False

        def check_then_editor_saves(relative, *args, **kwargs):
            nonlocal edited
            target = original_check(relative, *args, **kwargs)
            # The second file's preflight occurs after the first file was checked.
            if relative == self.book.chapter_path(2) and not edited:
                first_path.write_bytes(user_edit)
                edited = True
            return target

        with patch.object(self.book, "_check_artifact", side_effect=check_then_editor_saves):
            result = self.book.commit(2, self.draft, second_delta)

        self.assertTrue(edited)
        self.assertTrue(result["committed"])
        self.assertTrue(result["idempotent"])
        self.assertFalse(result["exports_complete"])
        self.assertTrue(result["export_error"])
        self.assertTrue(result["recovery"])
        self.assertEqual(first_path.read_bytes(), user_edit)
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertEqual(self.book.status()["changed_export_count"], 1)
        stored = self.book.db.execute("SELECT text FROM chapters WHERE chapter=1").fetchone()[0]
        self.assertEqual(stored, DRAFT)

    def test_edit_of_previous_chapter_during_later_export_is_reported(self):
        self.book.commit(1, self.draft, self.delta())
        self.save_plan(2)
        second_delta = self.delta()
        self.book.commit(2, self.draft, second_delta)
        revision = self.book.meta("revision")
        first_path = self.root / self.book.chapter_path(1)
        second_path = self.root / self.book.chapter_path(2)
        second_path.unlink()
        user_edit = "导出第二章期间，用户保存了第一章修订。\n".encode("utf-8")
        original_write = story.atomic_write
        edited = False

        def editor_saves_previous_chapter(path, *args, **kwargs):
            nonlocal edited
            if path == second_path:
                first_path.write_bytes(user_edit)
                edited = True
            return original_write(path, *args, **kwargs)

        with patch.object(story, "atomic_write", side_effect=editor_saves_previous_chapter):
            result = self.book.commit(2, self.draft, second_delta)

        self.assertTrue(edited)
        self.assertTrue(result["committed"])
        self.assertTrue(result["idempotent"])
        self.assertFalse(result["exports_complete"])
        self.assertTrue(result["export_error"])
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertEqual(self.book.meta("last_chapter"), 2)
        self.assertEqual(first_path.read_bytes(), user_edit)
        self.assertEqual(second_path.read_bytes(), DRAFT.encode("utf-8"))
        self.assertEqual(self.book.status()["changed_export_count"], 1)
        stored = self.book.db.execute("SELECT text FROM chapters ORDER BY chapter").fetchall()
        self.assertEqual([row[0] for row in stored], [DRAFT, DRAFT])

    def test_existing_backup_is_never_overwritten_or_displaces_target(self):
        target = self.root / "direct-export.md"
        backup = self.root / ".story/export-backups/retained/direct-export.md"
        before = b"the existing export must stay at its original path\n"
        retained = b"an earlier backup must remain untouched\n"
        target.write_bytes(before)
        backup.parent.mkdir(parents=True)
        backup.write_bytes(retained)

        with self.assertRaises((story.StoryError, OSError)):
            story.atomic_write(target, "the new export", {story.digest(before.decode("utf-8"))}, backup)

        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(backup.read_bytes(), retained)
        self.assertEqual(list(self.root.glob(".story-tmp-*")), [])

    def test_sqlite_lock_after_commit_returns_durable_receipt_and_recovers(self):
        self.book.db.execute("PRAGMA busy_timeout=50")
        original_delivery = self.book.delivery
        locked, release = threading.Event(), threading.Event()
        lock_errors = []
        revision = self.book.meta("revision")
        delta = self.delta()

        def other_writer():
            connection = None
            try:
                connection = sqlite3.connect(self.book.path, timeout=1)
                connection.execute("BEGIN IMMEDIATE")
                locked.set()
                release.wait(timeout=5)
            except BaseException as error:
                lock_errors.append(error)
                locked.set()
            finally:
                if connection is not None:
                    connection.rollback()
                    connection.close()

        def deliver_after_other_writer_locks(receipt):
            self.assertFalse(self.book.db.in_transaction)
            contender = threading.Thread(target=other_writer, daemon=True)
            contender.start()
            try:
                self.assertTrue(locked.wait(timeout=3), "Other writer did not reach the lock barrier")
                self.assertEqual(lock_errors, [])
                return original_delivery(receipt)
            finally:
                release.set()
                contender.join(timeout=3)
                self.assertFalse(contender.is_alive(), "Other writer did not release its lock")

        with patch.object(self.book, "delivery", side_effect=deliver_after_other_writer_locks):
            result = self.book.commit(1, self.draft, delta)

        self.assertTrue(result["committed"])
        self.assertFalse(result["exports_complete"])
        self.assertIn("locked", result["export_error"].lower())
        self.assertTrue(result["recovery"])
        self.assertEqual(self.book.meta("last_chapter"), 1)
        self.assertEqual(self.book.meta("revision"), revision + 1)
        self.assertEqual(self.book.status()["pending_export_count"], 1)
        recovered = self.book.commit(1, self.draft, delta)
        self.assertTrue(recovered["idempotent"])
        self.assertTrue(recovered["exports_complete"])
        self.assertEqual(self.book.meta("revision"), revision + 1)
        self.assertEqual((self.root / self.book.chapter_path(1)).read_bytes(), DRAFT.encode("utf-8"))

    def test_edit_at_displacement_is_restored_and_backed_up(self):
        self.book.commit(1, self.draft, self.delta())
        target = self.root / self.book.chapter_path(1)
        revised = DRAFT + "她没有回头。\n"
        self.draft.write_bytes(revised.encode("utf-8"))
        external = b"editor saved at the final write boundary\n"
        original_replace = story.os.replace

        def editor_save_then_replace(source, destination, *args, **kwargs):
            source_matches = Path(source) == target or (
                Path(source) == Path(target.name) and kwargs.get("src_dir_fd") is not None and
                story.os.path.samestat(target.parent.stat(), story.os.fstat(kwargs["src_dir_fd"])))
            if source_matches:
                target.write_bytes(external)
            return original_replace(source, destination, *args, **kwargs)

        with patch.object(story.os, "replace", side_effect=editor_save_then_replace):
            result = self.book.commit(1, self.draft, self.delta(revised), replace_last=True)
        self.assertTrue(result["committed"])
        self.assertFalse(result["exports_complete"])
        self.assertEqual(target.read_bytes(), external)
        self.assertEqual(Path(result["export_details"]["backup"]).read_bytes(), external)
        self.assertEqual(self.book.status()["changed_export_count"], 1)

    def test_editor_recreates_path_during_publication_is_not_overwritten(self):
        self.book.commit(1, self.draft, self.delta())
        target = self.root / self.book.chapter_path(1)
        revised = DRAFT + "她没有回头。\n"
        self.draft.write_bytes(revised.encode("utf-8"))
        external = b"a later editor save must win\n"
        original_publish = story._publish_no_replace

        def editor_save_then_link(source, destination, *args, **kwargs):
            if Path(source).name.startswith(".story-tmp-"):
                self.assertFalse(target.exists())
                target.write_bytes(external)
            return original_publish(source, destination, *args, **kwargs)

        with patch.object(story, "_publish_no_replace", side_effect=editor_save_then_link):
            result = self.book.commit(1, self.draft, self.delta(revised), replace_last=True)
        self.assertTrue(result["committed"])
        self.assertFalse(result["exports_complete"])
        self.assertEqual(target.read_bytes(), external)
        self.assertEqual(Path(result["export_details"]["backup"]).read_bytes(), DRAFT.encode("utf-8"))

    def test_publication_failure_restores_previous_file_and_allows_retry(self):
        self.book.commit(1, self.draft, self.delta())
        target = self.root / self.book.chapter_path(1)
        revised = DRAFT + "她没有回头。\n"
        self.draft.write_bytes(revised.encode("utf-8"))
        delta = self.delta(revised)
        original_publish = story._publish_no_replace

        def publication_fails(source, destination, *args, **kwargs):
            if Path(source).name.startswith(".story-tmp-"):
                raise OSError("publication interrupted")
            return original_publish(source, destination, *args, **kwargs)

        with patch.object(story, "_publish_no_replace", side_effect=publication_fails):
            result = self.book.commit(1, self.draft, delta, replace_last=True)
        self.assertTrue(result["committed"])
        self.assertFalse(result["exports_complete"])
        self.assertEqual(target.read_bytes(), DRAFT.encode("utf-8"))
        self.assertEqual(Path(result["export_details"]["backup"]).read_bytes(), DRAFT.encode("utf-8"))
        retry = self.book.commit(1, self.draft, delta, replace_last=True)
        self.assertTrue(retry["idempotent"])
        self.assertTrue(retry["exports_complete"])
        self.assertEqual(target.read_bytes(), revised.encode("utf-8"))

    def test_atomic_replace_failure_keeps_committed_revision_recoverable(self):
        self.book.commit(1, self.draft, self.delta())
        target = self.root / self.book.chapter_path(1)
        original_bytes = target.read_bytes()
        revised = DRAFT + "她没有回头。\n"
        self.draft.write_bytes(revised.encode("utf-8"))
        delta = self.delta(revised)
        revision = self.book.meta("revision")

        with patch.object(story.os, "replace", side_effect=OSError("replace temporarily unavailable")):
            result = self.book.commit(1, self.draft, delta, replace_last=True)

        self.assertTrue(result["committed"])
        self.assertFalse(result["exports_complete"])
        self.assertTrue(result["export_error"])
        self.assertTrue(result["recovery"])
        self.assertEqual(target.read_bytes(), original_bytes)
        self.assertEqual(self.book.meta("revision"), revision + 1)
        stored = self.book.db.execute("SELECT text FROM chapters WHERE chapter=1").fetchone()[0]
        self.assertEqual(stored, revised)
        self.assertEqual(self.book.status()["pending_export_count"], 1)

        recovered = self.book.commit(1, self.draft, delta, replace_last=True)
        self.assertTrue(recovered["idempotent"])
        self.assertTrue(recovered["exports_complete"])
        self.assertEqual(self.book.meta("revision"), revision + 1)
        self.assertEqual(target.read_bytes(), revised.encode("utf-8"))
        self.assertEqual(self.book.status()["pending_export_count"], 0)


if __name__ == "__main__":
    unittest.main()
