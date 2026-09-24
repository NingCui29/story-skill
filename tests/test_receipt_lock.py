from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch


TOOL = Path(__file__).resolve().parents[1] / "skills/story-skill/scripts/story.py"
spec = importlib.util.spec_from_file_location("story_receipt_lock", TOOL)
story = importlib.util.module_from_spec(spec)
spec.loader.exec_module(story)

DRAFT = "# 第1章 门后的雨\n沈禾把唯一的钥匙交给守门人。\n她答应在天亮之前带回账本。\n"
REVISED = "# 第1章 门后的雨\n沈禾收回了唯一的钥匙。\n她决定另找入口，守门人退回雨里。\n"
QUOTE = "沈禾把唯一的钥匙交给守门人。"


class ReceiptLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-receipt-lock-")
        self.root = Path(self.temp.name) / "book"
        story.Book.create(self.root, "门后的雨", "long")
        self.book = story.Book(self.root)
        self.book.db.execute("PRAGMA busy_timeout=50")
        self.book.save_notes([{"id": "hero", "kind": "character", "text": "沈禾持有钥匙。",
                               "source": "用户设定"}], 0)
        self.plan = {"volume_dir": "第一卷 雨夜", "goal": "决定钥匙的去向", "stop": "选择入口后停笔", "constraints": [],
                     "requires": ["hero"], "tags": [], "length": [20, 120],
                     "beats": [{"choice": "沈禾决定是否交出钥匙", "change": "失去或保留退路"}]}
        self.book.save_plan(1, self.plan, self.book.meta("revision"))
        self.draft = self.root / ".story/drafts/chapter.md"
        self.draft.parent.mkdir(parents=True)
        self.draft.write_bytes(DRAFT.encode("utf-8"))

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def delta(self, text=DRAFT, quote=QUOTE):
        return {"book_id": self.book.meta("id"), "base_revision": self.book.meta("revision"),
                "summary": "沈禾选择钥匙的去向。", "changes": [],
                "review": {"draft_sha256": story.digest(text), "checks": {
                    check: {"note": "正文体现人物选择与后果。", "quote": quote}
                    for check in story.CHECKS}, "issues": []}}

    def after_commit_exclusive(self, operation):
        """Hold a real SQLite exclusive lock from transaction exit until API return."""
        original_transaction = self.book.transaction
        acquired, release = threading.Event(), threading.Event()
        lock_errors = []
        contender = None
        armed = True

        def exclusive_writer():
            connection = None
            try:
                connection = sqlite3.connect(self.book.path, timeout=1)
                connection.execute("BEGIN EXCLUSIVE")
                acquired.set()
                release.wait(timeout=5)
            except BaseException as error:
                lock_errors.append(error)
                acquired.set()
            finally:
                if connection is not None:
                    connection.rollback()
                    connection.close()

        @contextmanager
        def transaction_then_lock(*args, **kwargs):
            nonlocal armed, contender
            with original_transaction(*args, **kwargs):
                yield
            if armed:
                armed = False
                self.assertFalse(self.book.db.in_transaction)
                contender = threading.Thread(target=exclusive_writer, daemon=True)
                contender.start()
                self.assertTrue(acquired.wait(timeout=3), "Exclusive writer did not reach the lock barrier")
                self.assertEqual(lock_errors, [])

        try:
            with patch.object(self.book, "transaction", side_effect=transaction_then_lock):
                result = operation()
            self.assertFalse(armed, "Operation did not exit a successful transaction")
            self.assertTrue(contender.is_alive(), "Exclusive lock was released before the API returned")
            return result
        finally:
            release.set()
            if contender is not None:
                contender.join(timeout=3)
                self.assertFalse(contender.is_alive(), "Exclusive writer did not release its lock")

    def assert_durable_export_failure(self, result, revision):
        self.assertEqual(result["revision"], revision)
        self.assertFalse(result["exports_complete"])
        self.assertIn("locked", result["export_error"].lower())
        self.assertTrue(result["recovery"])
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertEqual(self.book.status()["pending_export_count"], 1)

    def test_commit_returns_durable_receipt_when_locked_immediately_after_commit(self):
        delta = self.delta()
        delta["changes"] = [{"id": "hero", "text": "沈禾已交出钥匙。", "quote": QUOTE}]
        revision = self.book.meta("revision") + 1
        result = self.after_commit_exclusive(lambda: self.book.commit(1, self.draft, delta))

        self.assertTrue(result["committed"])
        self.assertFalse(result["idempotent"])
        self.assertEqual(result["chapter"], 1)
        self.assert_durable_export_failure(result, revision)
        cards = self.book.cards()
        self.assertEqual(cards["hero"]["text"], "沈禾已交出钥匙。")
        self.assertEqual(self.book.meta("last_chapter"), 1)
        retried = self.book.commit(1, self.draft, delta)
        self.assertTrue(retried["idempotent"])
        self.assertTrue(retried["exports_complete"])
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertEqual(self.book.cards(), cards)
        self.assertEqual((self.root / self.book.chapter_path(1)).read_bytes(), DRAFT.encode("utf-8"))

    def test_reconcile_keeps_hash_and_cards_when_locked_after_commit(self):
        first = self.delta()
        first["changes"] = [{"id": "hero", "text": "沈禾已交出钥匙。", "quote": QUOTE},
                            {"id": "debt", "kind": "hook", "text": "天亮前带回账本。", "due": 1,
                             "quote": "她答应在天亮之前带回账本。"}]
        self.book.commit(1, self.draft, first)
        target = self.root / self.book.chapter_path(1)
        target.write_bytes(REVISED.encode("utf-8"))
        packet = self.book.reconcile(1)
        reviewed = REVISED + "她把钥匙藏进衣襟。\n"
        self.draft.write_bytes(reviewed.encode("utf-8"))
        delta = self.delta(reviewed, "沈禾收回了唯一的钥匙。")
        delta["external_sha256"] = packet["external_edit"]["sha256"]
        delta["changes"] = [{"id": "hero", "text": "沈禾收回钥匙并另找入口。", "quote": "沈禾收回了唯一的钥匙。"}]
        revision = packet["revision"] + 1
        result = self.after_commit_exclusive(lambda: self.book.reconcile(1, self.draft, delta))

        self.assertTrue(result["committed"])
        self.assert_durable_export_failure(result, revision)
        cards = self.book.cards()
        self.assertEqual(cards["hero"]["text"], "沈禾收回钥匙并另找入口。")
        self.assertNotIn("debt", cards)
        receipt = json.loads(self.book.db.execute("SELECT receipt FROM chapters WHERE chapter=1").fetchone()[0])
        self.assertEqual(receipt["input"]["external_sha256"], delta["external_sha256"])
        self.assertEqual(target.read_bytes(), REVISED.encode("utf-8"))
        retried = self.book.reconcile(1, self.draft, delta)
        self.assertTrue(retried["idempotent"])
        self.assertTrue(retried["exports_complete"])
        self.assertEqual(self.book.meta("revision"), revision)
        self.assertEqual(self.book.cards(), cards)
        self.assertEqual(target.read_bytes(), reviewed.encode("utf-8"))

    def test_adopt_returns_baseline_receipt_when_locked_after_commit(self):
        before = self.book.meta("revision")
        cards = self.book.cards()
        result = self.after_commit_exclusive(
            lambda: self.book.adopt(108, self.draft, "最后完整章的导入基线。", before, volume_dir="第一卷 雨夜"))

        self.assertEqual(result["adopted_through"], 108)
        self.assertEqual(result["quality"], "imported_unverified")
        self.assert_durable_export_failure(result, before + 1)
        self.assertEqual(self.book.meta("last_chapter"), 108)
        self.assertEqual(self.book.meta("imported_through"), 108)
        self.assertEqual(self.book.cards(), cards)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM chapters").fetchone()[0], 1)
        self.assertTrue(self.book.export()["exports_complete"])
        self.assertEqual(self.book.meta("revision"), before + 1)
        self.assertEqual((self.root / self.book.chapter_path(108)).read_bytes(), DRAFT.encode("utf-8"))

    def test_other_write_receipts_do_not_read_sql_after_commit(self):
        source = self.root / "source.txt"
        source.write_text("第1章 入口\n沈禾在雨中等候守门人。", encoding="utf-8")
        with self.subTest(command="notes"):
            before = self.book.meta("revision")
            result = self.after_commit_exclusive(lambda: self.book.save_notes(
                [{"id": "rule", "text": "雨夜不能点灯。", "source": "用户设定"}], before))
            self.assertEqual(result, {"updated": 1, "revision": before + 1})
        with self.subTest(command="plan"):
            before = self.book.meta("revision")
            result = self.after_commit_exclusive(lambda: self.book.save_plan(2, self.plan, before))
            self.assertEqual(result, {"chapter": 2, "revision": before + 1})
        with self.subTest(command="ingest"):
            result = self.after_commit_exclusive(lambda: self.book.ingest(source, "partial"))
            self.assertFalse(result["idempotent"])
            self.assertEqual(result["pending"], 1)
            sid = result["source"]
        chunk = self.book.next_chunks(sid)["chunks"][0]
        analysis = {"chunk_sha256": chunk["sha"], "summary": "沈禾等候守门人。",
                    "findings": [{"kind": "行动", "claim": "沈禾仍在等候。", "quote": "沈禾在雨中等候守门人。"}]}
        with self.subTest(command="record"):
            result = self.after_commit_exclusive(lambda: self.book.record(sid, chunk["ordinal"], analysis))
            self.assertFalse(result["idempotent"])
            self.assertEqual(result["recorded"], 1)
            self.assertEqual(result["pending"], 0)
        for name, operation in (("ingest retry", lambda: self.book.ingest(source, "partial")),
                                ("record retry", lambda: self.book.record(sid, chunk["ordinal"], analysis))):
            with self.subTest(command=name):
                before = self.book.meta("revision")
                result = self.after_commit_exclusive(operation)
                self.assertTrue(result["idempotent"])
                self.assertEqual(result["pending"], 0)
                self.assertEqual(self.book.meta("revision"), before)

    def test_sql_failure_rolls_back_without_returning_a_durable_receipt(self):
        before_revision = self.book.meta("revision")
        before_cards = self.book.cards()
        before_events = self.book.db.execute("SELECT count(*) FROM events").fetchone()[0]
        self.book.db.execute("CREATE TRIGGER fail_chapter BEFORE INSERT ON chapter_state "
                             "BEGIN SELECT RAISE(ABORT, 'injected chapter SQL failure'); END")
        delta = self.delta()
        delta["changes"] = [{"id": "hero", "text": "沈禾已交出钥匙。", "quote": QUOTE}]
        with self.assertRaises(sqlite3.IntegrityError):
            self.book.commit(1, self.draft, delta)
        self.assertEqual(self.book.meta("revision"), before_revision)
        self.assertEqual(self.book.meta("last_chapter"), 0)
        self.assertEqual(self.book.cards(), before_cards)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM events").fetchone()[0], before_events)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM chapters").fetchone()[0], 0)
        self.assertEqual(self.book.db.execute("SELECT count(*) FROM artifacts").fetchone()[0], 0)
        self.assertFalse((self.root / "chapters/第一卷 雨夜/第1章 门后的雨.md").exists())
        self.book.db.execute("DROP TRIGGER fail_chapter")
        result = self.book.commit(1, self.draft, delta)
        self.assertTrue(result["committed"])
        self.assertTrue(result["exports_complete"])
        self.assertEqual(result["revision"], before_revision + 1)


if __name__ == "__main__":
    unittest.main()
