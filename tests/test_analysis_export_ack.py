from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_story as fixtures


story = fixtures.story


class AnalysisExportAcknowledgementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-analysis-export-ack-")
        self.root = (Path(self.temp.name) / "报告导出确认").resolve()
        story.Book.create(self.root, "门后的信", "analysis")
        self.book = story.Book(self.root)
        source = self.root / "原文.txt"
        source.write_text("第1章 来信\n她拆开了一封信。\n", encoding="utf-8")
        self.sid = self.book.ingest(source, "partial")["source"]
        chunk = self.book.next_chunks(self.sid)["chunks"][0]
        self.book.record(self.sid, chunk["ordinal"], {
            "chunk_sha256": chunk["sha"], "summary": "人物拆开了来信。",
            "findings": [{"kind": "事实", "claim": "人物拆开了一封信。",
                          "quote": "她拆开了一封信。"}],
        })
        self.baseline = self.book.findings(self.sid)["analysis_sha256"]
        self.draft = self.root / "报告草稿.md"
        self.draft.write_text(
            "本报告仅覆盖实际导入的文字。人物拆开信件是可定位的行动，"
            "但文本没有展示来信内容，不能据此判断来信者身份或人物接下来的选择。\n",
            encoding="utf-8",
        )
        self.relative = f".story/analysis/{self.sid}/report.md"
        self.target = self.root / self.relative

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def state(self):
        return dict(self.book.db.execute(
            "SELECT * FROM artifact_state WHERE path=?", (self.relative,)).fetchone())

    def events(self):
        return [tuple(row) for row in self.book.db.execute("SELECT * FROM events ORDER BY seq")]

    def audits(self):
        return [tuple(row) for row in self.book.db.execute("SELECT * FROM integrity_audits")]

    def one_postpublication_failure(self):
        verify = story._verify_bound_directory
        failed = False

        def fail_once(directory):
            nonlocal failed
            if not failed:
                failed = True
                self.assertTrue(self.target.is_file(), "The failure must follow publication")
                self.assertEqual(hashlib.sha256(self.target.read_bytes()).hexdigest(), self.state()["sha"])
                raise OSError("temporary directory verification failure after publication")
            return verify(directory)

        return patch.object(story, "_verify_bound_directory", side_effect=fail_once)

    def assert_incomplete(self, result):
        self.assertFalse(result["exports_complete"], result)
        self.assertFalse(result["scope_exports_complete"], result)
        self.assertFalse(result["integrity"]["full_book_verified"], result)
        self.assertIn(self.relative, result["pending_exports"])
        self.assertEqual(result["pending_export_count"], 1)
        self.assertEqual(result["changed_exports"], [])
        self.assertEqual(result["export_error_count"], 1)
        self.assertEqual(result["export_errors"][0]["code"], "io_error")

    def assert_retry_recovers(self, published, events):
        result = self.book.export(safe_only=True)
        self.assertTrue(result["exports_complete"], result)
        self.assertTrue(result["scope_exports_complete"], result)
        self.assertTrue(result["integrity"]["full_book_verified"], result)
        self.assertEqual(result["pending_exports"], [])
        self.assertEqual(result["export_error_count"], 0)
        self.assertEqual(self.state()["written_sha"], self.state()["sha"])
        self.assertEqual(self.target.read_bytes(), published)
        self.assertEqual(self.events(), events)
        self.assertEqual(self.book.status()["pending_exports"], [])

    def test_failed_report_export_does_not_finalize_audit_after_publication_error(self):
        with patch.object(story, "atomic_write", side_effect=OSError("initial export unavailable")):
            accepted = self.book.report(self.sid, self.draft, self.baseline)
        self.assertTrue(accepted["finalized"])
        self.assertFalse(accepted["exports_complete"])
        self.assertFalse(self.target.exists())
        self.assertIsNone(self.state()["written_sha"])
        self.assertEqual(self.audits(), [])
        events = self.events()

        with self.one_postpublication_failure() as injected:
            failed = self.book.export(safe_only=True)
        self.assertTrue(injected.called)
        published = self.target.read_bytes()
        self.assert_incomplete(failed)
        self.assertIsNone(self.state()["written_sha"])
        self.assertEqual(self.audits(), [], "A failed export must not record a full audit")
        self.assertEqual(self.events(), events)
        self.assertIn(self.relative, self.book.status()["pending_exports"])
        self.assert_retry_recovers(published, events)

    def test_cli_rebuild_of_acknowledged_report_still_reports_postpublication_failure(self):
        accepted = self.book.report(self.sid, self.draft, self.baseline)
        self.assertTrue(accepted["exports_complete"])
        published, events = self.target.read_bytes(), self.events()
        self.assertEqual(self.state()["written_sha"], self.state()["sha"])
        self.target.unlink()

        output, errors = io.StringIO(), io.StringIO()
        argv = [str(fixtures.TOOL), "export", "--book", str(self.root), "--safe-only"]
        with self.one_postpublication_failure() as injected, \
                patch.object(sys, "argv", argv), redirect_stdout(output), redirect_stderr(errors):
            exit_code = story.main()
        self.assertTrue(injected.called)
        self.assertEqual(errors.getvalue(), "")
        failed = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2, failed)
        self.assert_incomplete(failed)
        self.assertEqual(self.target.read_bytes(), published)
        self.assertEqual(self.events(), events)
        self.assert_retry_recovers(published, events)


if __name__ == "__main__":
    unittest.main()
