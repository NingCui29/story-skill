"""New books publish schema and identity together without weakening durability."""
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("story_initialization_test", ROOT / "skills/story-skill/scripts/story.py")
story = importlib.util.module_from_spec(spec)
spec.loader.exec_module(story)


class BookInitializationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-init-transaction-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "新书"
        self.database = self.root / ".story/state.sqlite3"

    def staged_databases(self):
        return list(self.database.parent.glob(".state-init-*.sqlite3"))

    def assert_failed_state_preserved(self):
        self.assertFalse(self.database.exists())
        staged = self.staged_databases()
        self.assertEqual(len(staged), 1)
        with closing(sqlite3.connect(staged[0])) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master").fetchall(), [])
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_late_schema_failure_rolls_back_preceding_tables_and_views(self):
        with patch.object(story.history, "SCHEMA", story.history.SCHEMA + "\nCREATE TABLE meta(duplicate TEXT);\n"):
            with self.assertRaisesRegex(sqlite3.OperationalError, "already exists"):
                story.Book.create(self.root, "未完成的新书", "long")
        self.assert_failed_state_preserved()
        receipt = story.Book.create(self.root, "重试的新书", "long")
        self.assertTrue(self.database.is_file())
        self.assertEqual(receipt["title"], "重试的新书")
        self.assertEqual(len(self.staged_databases()), 1)

    def test_initial_metadata_failure_rolls_back_schema_and_partial_identity(self):
        rejection = """
CREATE TRIGGER reject_initial_title BEFORE INSERT ON meta WHEN NEW.key='title' BEGIN
 SELECT RAISE(ABORT,'initial metadata interrupted');
END;
"""
        with patch.object(story.history, "SCHEMA", story.history.SCHEMA + rejection):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "initial metadata interrupted"):
                story.Book.create(self.root, "元数据故障", "short")
        self.assert_failed_state_preserved()

    def test_new_schema_is_invisible_until_complete_identity_is_committed(self):
        execute_schema = story.storage.execute_schema
        observations = []
        with closing(sqlite3.connect(":memory:")) as default:
            synchronous = default.execute("PRAGMA synchronous").fetchone()[0]

        def inspect_before_identity(db, script):
            execute_schema(db, script)
            self.assertTrue(db.in_transaction)
            self.assertEqual(db.execute("PRAGMA synchronous").fetchone()[0], synchronous)
            self.assertTrue(db.execute("SELECT name FROM sqlite_master WHERE name='meta'").fetchone())
            self.assertFalse(self.database.exists())
            with closing(sqlite3.connect(self.staged_databases()[0])) as observer:
                observations.append(observer.execute("SELECT name FROM sqlite_master").fetchall())

        with patch.object(story.storage, "execute_schema", side_effect=inspect_before_identity):
            receipt = story.Book.create(self.root, "完整的新书", "long")
        self.assertEqual(observations, [[]])
        self.assertEqual(self.staged_databases(), [])
        with closing(sqlite3.connect(self.database)) as db:
            meta = {key: json.loads(value) for key, value in db.execute("SELECT key,value FROM meta")}
            self.assertEqual(meta, {key: value for key, value in receipt.items() if key != "created"})
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        book = story.Book(self.root)
        try:
            self.assertEqual(book.meta("revision"), 0)
            self.assertEqual(book.meta("id"), receipt["id"])
        finally:
            book.close()

    def test_publish_failure_preserves_completed_stage_and_allows_retry(self):
        operation = "rename" if os.name == "nt" else "link"
        with patch.object(story.os, operation, side_effect=OSError("atomic publish unavailable")):
            with self.assertRaises(story.StoryError) as result:
                story.Book.create(self.root, "暂存的新书", "short")
        self.assertEqual(result.exception.code, "init_publish_failed")
        self.assertFalse(self.database.exists())
        staged = self.staged_databases()
        self.assertEqual(len(staged), 1)
        with closing(sqlite3.connect(staged[0])) as db:
            self.assertEqual(json.loads(db.execute("SELECT value FROM meta WHERE key='title'").fetchone()[0]),
                             "暂存的新书")
        story.Book.create(self.root, "重试的新书", "short")
        self.assertTrue(self.database.is_file())
        self.assertEqual(len(self.staged_databases()), 1)

    def test_concurrent_initializer_never_replaces_existing_state(self):
        operation = "rename" if os.name == "nt" else "link"
        real_publish = getattr(story.os, operation)

        def publish_after_other_writer(source, target):
            Path(target).write_bytes(b"other writer's state")
            return real_publish(source, target)

        with patch.object(story.os, operation, side_effect=publish_after_other_writer):
            with self.assertRaises(story.StoryError) as result:
                story.Book.create(self.root, "竞态中的新书", "long")
        self.assertEqual(result.exception.code, "book_exists")
        self.assertEqual(self.database.read_bytes(), b"other writer's state")
        self.assertEqual(len(self.staged_databases()), 1)


if __name__ == "__main__":
    unittest.main()
