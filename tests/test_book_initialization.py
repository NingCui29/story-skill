"""New books publish schema and identity together without weakening durability."""
from contextlib import closing
import importlib.util
import json
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

    def assert_empty_database(self):
        self.assertTrue(self.database.is_file())
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master").fetchall(), [])
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_late_schema_failure_rolls_back_preceding_tables_and_views(self):
        with patch.object(story.history, "SCHEMA", story.history.SCHEMA + "\nCREATE TABLE meta(duplicate TEXT);\n"):
            with self.assertRaisesRegex(sqlite3.OperationalError, "already exists"):
                story.Book.create(self.root, "未完成的新书", "long")
        self.assert_empty_database()

    def test_initial_metadata_failure_rolls_back_schema_and_partial_identity(self):
        rejection = """
CREATE TRIGGER reject_initial_title BEFORE INSERT ON meta WHEN NEW.key='title' BEGIN
 SELECT RAISE(ABORT,'initial metadata interrupted');
END;
"""
        with patch.object(story.history, "SCHEMA", story.history.SCHEMA + rejection):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "initial metadata interrupted"):
                story.Book.create(self.root, "元数据故障", "short")
        self.assert_empty_database()

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
            with closing(sqlite3.connect(self.database)) as observer:
                observations.append(observer.execute("SELECT name FROM sqlite_master").fetchall())

        with patch.object(story.storage, "execute_schema", side_effect=inspect_before_identity):
            receipt = story.Book.create(self.root, "完整的新书", "long")
        self.assertEqual(observations, [[]])
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


if __name__ == "__main__":
    unittest.main()
