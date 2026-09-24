"""Real SQLite open-time path substitution and descriptor identity regressions."""
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import unittest
from unittest.mock import patch

from tests import test_publish as fixtures

publishing, story = fixtures.publishing, fixtures.story


class PublishingConnectionPathTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublishTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.commit()
        self.prepared = self.fixture.prepare()
        self.book, self.ledger = self.fixture.book, self.fixture.ledger
        self.foreign = self.fixture.root / "unrelated.sqlite3"
        shutil.copyfile(self.ledger, self.foreign)
        with sqlite3.connect(self.foreign) as db:
            db.execute("UPDATE plans SET status='cancelled'")

    def digest(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def assert_code(self, code, action):
        with self.assertRaises(story.StoryError) as error:
            action()
        self.assertEqual(error.exception.code, code)

    def substitute_during_connect(self, predicate):
        real_connect = sqlite3.connect
        substitutions = []

        def connect(database, *args, **kwargs):
            name = str(database).split("?", 1)[0]
            if not predicate(name):
                return real_connect(database, *args, **kwargs)
            # Operate on actual regular databases: restore the original path
            # before returning the connection to reproduce the ABA window.
            from urllib.parse import unquote, urlparse
            path = Path(unquote(urlparse(name).path))
            held = path.with_name(path.name + ".held")
            path.rename(held)
            try:
                path.symlink_to(self.foreign)
                connection = real_connect(database, *args, **kwargs)
            finally:
                path.unlink()
                held.rename(path)
            substitutions.append(path)
            return connection

        return connect, substitutions

    @unittest.skipUnless(os.name == "posix", "POSIX descriptor verification")
    def test_restored_symlink_cannot_redirect_read_check_cancel_or_backup(self):
        actions = [lambda: publishing.list_plans(self.book),
                   lambda: publishing.inspect(self.book, self.prepared["id"]),
                   lambda: publishing.check(self.book, self.prepared["id"]),
                   lambda: publishing.cancel(self.book, self.prepared["id"]),
                   lambda: publishing.recover(self.book),
                   lambda: publishing.backup(self.book)]
        before, foreign_before = self.digest(self.ledger), self.digest(self.foreign)
        for action in actions:
            with self.subTest(action=action):
                connect, substitutions = self.substitute_during_connect(lambda name: name == self.ledger.as_uri())
                with patch.object(publishing.sqlite3, "connect", side_effect=connect):
                    self.assert_code("unsafe_publish_path", action)
                self.assertEqual(len(substitutions), 1)
                self.assertEqual(self.digest(self.ledger), before)
                self.assertEqual(self.digest(self.foreign), foreign_before)
                self.assertFalse(self.ledger.is_symlink())

    @unittest.skipUnless(os.name == "posix", "POSIX descriptor verification")
    def test_restored_symlink_cannot_redirect_initial_creation(self):
        self.ledger.unlink()
        before = self.digest(self.foreign)
        connect, substitutions = self.substitute_during_connect(lambda name: "/.publishing-init-" in name)
        with patch.object(publishing.sqlite3, "connect", side_effect=connect):
            self.assert_code("unsafe_publish_path", self.fixture.prepare)
        self.assertEqual(len(substitutions), 1)
        self.assertFalse(self.ledger.exists())
        self.assertEqual(self.digest(self.foreign), before)
        self.assertEqual(list(self.ledger.parent.glob(".publishing-init-*")), [])
        self.assertTrue(self.fixture.prepare()["ok"])

    @unittest.skipUnless(os.name == "posix", "POSIX descriptor verification")
    def test_restored_symlink_cannot_redirect_backup_destination(self):
        before = self.digest(self.foreign)
        connect, substitutions = self.substitute_during_connect(lambda name: "/publishing-backups/" in name)
        with patch.object(publishing.sqlite3, "connect", side_effect=connect):
            self.assert_code("unsafe_publish_path", lambda: publishing.backup(self.book))
        self.assertEqual(len(substitutions), 1)
        self.assertEqual(self.digest(self.foreign), before)
        self.assertEqual(list((self.ledger.parent / "publishing-backups").glob("*.sqlite3")), [])
        self.assertTrue(publishing.backup(self.book)["ok"])

    @unittest.skipUnless(os.name == "posix", "POSIX descriptor verification")
    def test_fd_number_reuse_for_actual_database_is_accepted(self):
        extra = self.fixture.root / "extra.txt"
        extra.write_bytes(b"unrelated descriptor")
        fd = os.open(extra, os.O_RDONLY)
        real_connect = sqlite3.connect
        closed = []

        def connect(*args, **kwargs):
            os.close(fd)
            closed.append(fd)
            connection = real_connect(*args, **kwargs)
            self.assertEqual(os.fstat(fd).st_ino, self.ledger.stat().st_ino)
            return connection

        try:
            with patch.object(publishing.sqlite3, "connect", side_effect=connect):
                self.assertEqual(publishing.list_plans(self.book)["total"], 1)
        finally:
            if not closed:
                os.close(fd)

    @unittest.skipUnless(os.name == "posix", "POSIX descriptor verification")
    def test_unrelated_new_descriptor_does_not_reject_valid_connection(self):
        extra = self.fixture.root / "extra.txt"
        extra.write_bytes(b"unrelated descriptor")
        real_connect, opened = sqlite3.connect, []

        def connect(*args, **kwargs):
            opened.append(os.open(extra, os.O_RDONLY))
            return real_connect(*args, **kwargs)

        try:
            with patch.object(publishing.sqlite3, "connect", side_effect=connect):
                self.assertEqual(publishing.list_plans(self.book)["total"], 1)
        finally:
            for fd in opened:
                os.close(fd)

    @unittest.skipUnless(os.name == "posix", "POSIX SQLite descriptor cache")
    def test_external_sqlite_cached_descriptor_requires_close_then_retry(self):
        uri = self.ledger.as_uri() + "?mode=ro"
        owner = sqlite3.connect(uri, uri=True)
        owner.execute("BEGIN")
        owner.execute("SELECT count(*) FROM plans").fetchone()
        cached = sqlite3.connect(uri, uri=True)
        cached.close()
        try:
            self.assert_code("publishing_connection_unverifiable", lambda: publishing.list_plans(self.book))
            self.assertEqual(owner.execute("SELECT count(*) FROM plans").fetchone()[0], 1)
        finally:
            owner.rollback()
            owner.close()
        self.assertEqual(publishing.list_plans(self.book)["total"], 1)

    def test_module_queries_from_multiple_book_connections_are_serialized(self):
        barrier = threading.Barrier(3)
        failures = []

        def worker():
            book = story.Book(self.fixture.root)
            try:
                barrier.wait(timeout=5)
                for _ in range(4):
                    self.assertEqual(publishing.list_plans(book)["total"], 1)
            except BaseException as error:
                failures.append(error)
            finally:
                book.close()

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])

    @unittest.skipUnless(os.name == "nt", "Native Windows delete-sharing protection")
    def test_windows_database_pin_blocks_replacement_for_connection_lifetime(self):
        held = self.ledger.with_name("held.sqlite3")
        with publishing._ledger(self.book) as db:
            with self.assertRaises(OSError):
                self.ledger.rename(held)
            self.assertEqual(db.execute("SELECT count(*) FROM plans").fetchone()[0], 1)
        self.ledger.rename(held)
        held.rename(self.ledger)
        self.assertTrue(publishing.backup(self.book)["ok"])


if __name__ == "__main__":
    unittest.main()
