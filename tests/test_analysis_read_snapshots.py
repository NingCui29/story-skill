from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_story as fixtures


story = fixtures.story


class BeforeQuery:
    """Run another connection's write at a deterministic read boundary."""

    def __init__(self, connection, sql_prefix, callback):
        self.connection = connection
        self.sql_prefix = sql_prefix
        self.callback = callback

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def execute(self, sql, *args, **kwargs):
        if self.callback is not None and sql.startswith(self.sql_prefix):
            callback, self.callback = self.callback, None
            callback()
        return self.connection.execute(sql, *args, **kwargs)


class AnalysisReadSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-analysis-reads-")
        self.root = Path(self.temp.name) / "分析"
        story.Book.create(self.root, "来信", "analysis")
        self.book = story.Book(self.root)
        self.book.db.execute("PRAGMA journal_mode=WAL")
        self.other = story.Book(self.root)
        source = self.root / "原文.txt"
        source.write_text("第1章 来信\n她收到一封信。\n", encoding="utf-8")
        self.sid = self.book.ingest(source, "partial")["source"]

    def tearDown(self):
        self.other.close()
        self.book.close()
        self.temp.cleanup()

    def finish_from_other_connection(self):
        chunk = self.other.next_chunks(self.sid)["chunks"][0]
        self.other.record(self.sid, chunk["ordinal"], {
            "chunk_sha256": chunk["sha"], "summary": "人物收到一封信。",
            "findings": [{"kind": "事实", "claim": "收到信是已发生的行动。", "quote": "她收到一封信。"}],
        })
        report = self.root / "待提交报告.md"
        report.write_text(
            "本报告只分析已导入的第一章片段。人物收到一封信是原文直接展示的行动；"
            "信件内容以及后续行动尚未交代，不能据此认定人物的动机或故事结局。", encoding="utf-8")
        fingerprint = self.other.findings(self.sid)["analysis_sha256"]
        self.other.report(self.sid, report, fingerprint)

    def test_coverage_does_not_mix_pending_chunks_with_a_later_final_report(self):
        before = self.book.coverage(self.sid)
        hook = BeforeQuery(self.book.db, "SELECT 1 FROM artifacts WHERE path=?",
                           self.finish_from_other_connection)
        with patch.object(self.book, "db", hook):
            during = self.book.coverage(self.sid)
        self.assertIsNone(hook.callback)
        self.assertEqual(during, before)
        after = self.book.coverage(self.sid)
        self.assertEqual(after["pending"], 0)
        self.assertIsNotNone(after["report_path"])
        self.assertFalse(self.book.db.in_transaction)

    def test_source_list_count_and_rows_use_the_same_snapshot(self):
        before = self.book.list_sources()
        second = self.root / "另一作品.txt"
        second.write_text("序章\n另一个人等来了天亮。\n", encoding="utf-8")
        hook = BeforeQuery(self.book.db, "SELECT id,name FROM sources ORDER BY rowid",
                           lambda: self.other.ingest(second, "complete"))
        with patch.object(self.book, "db", hook):
            during = self.book.list_sources()
        self.assertIsNone(hook.callback)
        self.assertEqual(during, before)
        after = self.book.list_sources()
        self.assertEqual(after["total"], 2)
        self.assertEqual(len(after["results"]), 2)
        self.assertFalse(self.book.db.in_transaction)

    def test_composite_reads_preserve_the_callers_transaction_and_rollback(self):
        for read in (lambda: self.book.coverage(self.sid), self.book.list_sources):
            with self.subTest(read=read):
                with self.assertRaisesRegex(RuntimeError, "caller rollback"):
                    with self.book.transaction():
                        self.book.set_meta("analysis_read_marker", "uncommitted")
                        read()
                        self.assertTrue(self.book.db.in_transaction)
                        self.assertEqual(self.book.meta("analysis_read_marker"), "uncommitted")
                        self.assertIsNone(self.other.db.execute(
                            "SELECT value FROM meta WHERE key='analysis_read_marker'").fetchone())
                        raise RuntimeError("caller rollback")
                self.assertIsNone(self.book.db.execute(
                    "SELECT value FROM meta WHERE key='analysis_read_marker'").fetchone())

    def test_failed_reads_release_their_own_snapshot(self):
        for read in (lambda: self.book.coverage("missing"), lambda: self.book.list_sources(budget=1)):
            with self.subTest(read=read):
                with self.assertRaises(story.StoryError):
                    read()
                self.assertFalse(self.book.db.in_transaction)


if __name__ == "__main__":
    unittest.main()
