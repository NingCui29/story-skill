"""The audit command observes export health without publishing or acknowledging it."""

import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

if __package__:
    from . import test_story as fixtures
else:
    import test_story as fixtures


story = fixtures.story


class ReadOnlyAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-readonly-audit-")
        self.root = (Path(self.temp.name) / "待恢复的书").resolve()
        story.Book.create(self.root, "门后的信", "analysis")
        self.book = story.Book(self.root)
        source = self.root / "原文.txt"
        source.write_bytes("第1章 来信\n她拆开了一封信。\n".encode("utf-8"))
        sid = self.book.ingest(source, "partial")["source"]
        chunk = self.book.next_chunks(sid)["chunks"][0]
        self.book.record(sid, chunk["ordinal"], {
            "chunk_sha256": chunk["sha"], "summary": "人物拆开来信。",
            "findings": [{"kind": "事实", "claim": "人物拆开来信。", "quote": "她拆开了一封信。"}],
        })
        draft = self.root / "报告草稿.md"
        draft.write_bytes(
            "本报告只覆盖已经导入的文字。人物拆开了一封信，但正文没有展示信的内容，"
            "因此无法判断寄信者的身份，也无法确认人物下一步将如何行动。\n".encode("utf-8")
        )
        with patch.object(story, "atomic_write", side_effect=OSError("delayed export")):
            receipt = self.book.report(sid, draft, self.book.findings(sid)["analysis_sha256"])
        self.assertTrue(receipt["finalized"])
        self.assertFalse(receipt["exports_complete"])
        self.relative = f".story/analysis/{sid}/report.md"
        self.target = self.root / self.relative
        self.state_path = self.root / ".story/state.sqlite3"
        self.book.close()

    def tearDown(self):
        self.temp.cleanup()

    def command(self, name, *extra):
        process = subprocess.run(
            [sys.executable, str(fixtures.TOOL), name, "--book", str(self.root), *extra],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertFalse(process.stderr, process.stderr)
        return process.returncode, json.loads(process.stdout)

    def state_digest(self):
        return hashlib.sha256(self.state_path.read_bytes()).hexdigest()

    def test_audit_reports_pending_without_publishing_or_recording_and_export_recovers(self):
        before = self.state_digest()
        code, report = self.command("audit", "--integrity", "strict")
        self.assertEqual(code, 2)
        self.assertTrue(report["read_only"])
        self.assertEqual(report["read_only_scope"], "managed_book_records_and_exports")
        self.assertEqual(report["integrity"]["mode"], "strict")
        self.assertFalse(report["exports_complete"])
        self.assertIn(self.relative, report["pending_exports"])
        self.assertEqual(report["changed_exports"], [])
        self.assertFalse(self.target.exists())
        self.assertEqual(self.state_digest(), before)

        code, exported = self.command("export", "--safe-only")
        self.assertEqual(code, 0, exported)
        self.assertTrue(exported["exports_complete"])
        self.assertTrue(self.target.is_file())
        recovered = self.state_digest()
        code, clean = self.command("audit")
        self.assertEqual(code, 0, clean)
        self.assertTrue(clean["exports_complete"])
        self.assertEqual(clean["pending_export_count"], 0)
        self.assertEqual(clean["integrity"]["last_full_audit_revision"], clean["checked_revision"])
        self.assertEqual(self.state_digest(), recovered)

    def test_audit_rejects_local_integrity_in_cli_and_direct_api(self):
        process = subprocess.run(
            [sys.executable, str(fixtures.TOOL), "audit", "--book", str(self.root),
             "--integrity", "local"],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("invalid choice", process.stderr)
        self.assertFalse(self.target.exists())
        local = story.Book(self.root, integrity="local", read_only=True)
        try:
            with self.assertRaises(story.StoryError) as result:
                local.audit()
            self.assertEqual(result.exception.code, "invalid_input")
        finally:
            local.close()

    def test_audit_preserves_external_edit_and_does_not_create_backup(self):
        code, exported = self.command("export", "--safe-only")
        self.assertEqual(code, 0, exported)
        outside = "外部编辑尚未审查。\n".encode("utf-8")
        self.target.write_bytes(outside)
        before = self.state_digest()
        code, report = self.command("audit")
        self.assertEqual(code, 2)
        self.assertIn(self.relative, report["changed_exports"])
        self.assertEqual(self.target.read_bytes(), outside)
        self.assertEqual(self.state_digest(), before)
        self.assertFalse((self.root / ".story/export-backups").exists())

    def test_unreadable_export_is_reported_without_counting_it_as_verified(self):
        code, exported = self.command("export", "--safe-only")
        self.assertEqual(code, 0, exported)
        before_state = self.state_digest()
        before_file = hashlib.sha256(self.target.read_bytes()).hexdigest()
        original_mode = stat.S_IMODE(self.target.stat().st_mode)
        original_read = Path.read_bytes

        def deny_export(path):
            if path == self.target:
                raise PermissionError("isolated export is unreadable")
            return original_read(path)

        mode_changed = False
        try:
            self.target.chmod(0)
            mode_changed = True
        except OSError:
            pass  # The path-specific mock still covers hosts with restricted chmod.
        try:
            # chmod may be ineffective for root or on Windows. The path-specific
            # mock exercises the same read failure on every test host.
            with patch.object(Path, "read_bytes", new=deny_export):
                reader = story.Book(self.root, read_only=True)
                try:
                    report = reader.audit()
                    status = reader.status()
                finally:
                    reader.close()
            self.assertFalse(report["exports_complete"])
            for result in (report, status):
                self.assertIn(self.relative, result["changed_exports"])
                self.assertEqual(result["integrity"]["verified_file_count"], 0)
                self.assertEqual(result["integrity"]["unverified_archive_count"], 1)
                self.assertFalse(result["integrity"]["full_book_verified"])
            self.assertTrue(report["read_only"])

            try:
                original_read(self.target)
            except PermissionError:
                code, cli_report = self.command("audit")
                self.assertEqual(code, 2)
                self.assertIn(self.relative, cli_report["changed_exports"])
                self.assertEqual(cli_report["integrity"]["verified_file_count"], 0)
        finally:
            if mode_changed:
                self.target.chmod(original_mode)
        self.assertEqual(self.state_digest(), before_state)
        self.assertEqual(hashlib.sha256(self.target.read_bytes()).hexdigest(), before_file)
        self.assertFalse((self.root / ".story/export-backups").exists())

    def test_audit_of_active_wal_book_does_not_record_or_export(self):
        writer = story.Book(self.root)
        try:
            self.assertEqual(writer.db.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            writer.save_notes([fixtures.card("wal-audit")], writer.meta("revision"))
            wal = Path(str(self.state_path) + "-wal")
            before_main = self.state_digest()
            before_wal = hashlib.sha256(wal.read_bytes()).hexdigest()
            before_audits = writer.db.execute("SELECT count(*) FROM integrity_audits").fetchone()[0]
            code, report = self.command("audit")
            self.assertEqual(code, 2, report)
            self.assertEqual(report["integrity"]["mode"], "strict")
            self.assertEqual(self.state_digest(), before_main)
            self.assertEqual(hashlib.sha256(wal.read_bytes()).hexdigest(), before_wal)
            self.assertFalse(self.target.exists())
            self.assertEqual(writer.db.execute("SELECT count(*) FROM integrity_audits").fetchone()[0], before_audits)
        finally:
            writer.close()

    def test_audit_reads_wal_without_preexisting_shm(self):
        writer = story.Book(self.root)
        try:
            self.assertEqual(writer.db.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            writer.save_notes([fixtures.card("orphan-wal")], writer.meta("revision"))
            revision = writer.meta("revision")
            copied_root = (Path(self.temp.name) / "wal-copy").resolve()
            copied_state = copied_root / ".story/state.sqlite3"
            copied_state.parent.mkdir(parents=True)
            copied_wal = Path(str(copied_state) + "-wal")
            shutil.copyfile(self.state_path, copied_state)
            shutil.copyfile(Path(str(self.state_path) + "-wal"), copied_wal)
            self.assertFalse(Path(str(copied_state) + "-shm").exists())
            before_main = hashlib.sha256(copied_state.read_bytes()).hexdigest()
            before_wal = hashlib.sha256(copied_wal.read_bytes()).hexdigest()
            process = subprocess.run(
                [sys.executable, str(fixtures.TOOL), "audit", "--book", str(copied_root)],
                capture_output=True, text=True, encoding="utf-8", check=False,
            )
            self.assertFalse(process.stderr, process.stderr)
            self.assertEqual(process.returncode, 2, process.stdout)
            report = json.loads(process.stdout)
            self.assertEqual(report["checked_revision"], revision)
            self.assertEqual(report["integrity"]["mode"], "strict")
            self.assertFalse((copied_root / self.relative).exists())
            self.assertEqual(hashlib.sha256(copied_state.read_bytes()).hexdigest(), before_main)
            self.assertEqual(hashlib.sha256(copied_wal.read_bytes()).hexdigest(), before_wal)
            # SQLite may create or remove a transient -shm file while reading WAL.
        finally:
            writer.close()


if __name__ == "__main__":
    unittest.main()
