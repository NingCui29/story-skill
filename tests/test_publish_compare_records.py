"""Explicit, book-bound local comparison history never becomes platform proof."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests import test_publish as fixtures


story, publishing = fixtures.story, fixtures.publishing


class PublishComparisonRecordTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublishTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.commit()
        self.plan = self.fixture.prepare()
        self.book = self.fixture.book
        self.frozen = self.plan["manifest"]["chapters"][0]

    def copied(self, **changes):
        return {"title": self.frozen["title"], "body": self.frozen["body"],
                "author_note": self.frozen["author_note"], **changes}

    def count(self):
        with closing(sqlite3.connect(self.fixture.ledger)) as db:
            return db.execute("SELECT count(*) FROM meta WHERE key GLOB 'comparison:*'").fetchone()[0]

    def assert_code(self, code, call, *args, **kwargs):
        with self.assertRaises(story.StoryError) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception.details)

    def test_record_is_explicit_hash_only_and_history_does_not_recheck_current_source(self):
        copy = self.copied(body=self.frozen["body"].replace("\n", "\r\n"))
        before_writing = self.fixture.writing_snapshot()
        before_ledger = self.fixture.ledger.read_bytes()
        plain = publishing.compare(self.book, self.plan["id"], 1, copy)
        self.assertEqual(self.fixture.ledger.read_bytes(), before_ledger)
        self.assertTrue(plain["copy_matches_frozen"])
        recorded = publishing.record_compare(self.book, self.plan["id"], 1, copy)
        self.assertTrue(recorded["record_saved"])
        self.assertTrue(recorded["copy_matches_frozen"])
        self.assertTrue(recorded["usable_now"])
        self.assertFalse(recorded["platform_verified"])
        self.assertEqual(recorded["remote_state"], "unknown")
        self.assertEqual(self.count(), 1)
        history = publishing.compare_history(self.book, self.plan["id"])
        self.assertEqual(history["total"], 1)
        self.assertFalse(history["source_check_performed"])
        self.assertEqual(history["results"][0]["id"], recorded["record_id"])
        inspection = publishing.inspect_comparison(self.book, recorded["record_id"])
        self.assertTrue(inspection["record_valid"])
        self.assertFalse(inspection["source_check_performed"])
        self.assertFalse(inspection["platform_verified"])
        saved = inspection["local_comparison_record"]
        self.assertEqual(saved["copy_source"], "user_supplied_copy")
        self.assertEqual(saved["input_sha256"], publishing._hash(copy))
        self.assertEqual(saved["status_at_record"], "prepared")
        self.assertTrue(saved["usable_at_record"])
        self.assertNotIn(self.frozen["body"], json.dumps(inspection, ensure_ascii=False))
        self.assertEqual(self.fixture.writing_snapshot(), before_writing)

    def test_missing_field_and_mismatch_are_retained_without_fabricating_success(self):
        incomplete = {"title": self.frozen["title"], "body": self.frozen["body"]}
        first = publishing.record_compare(self.book, self.plan["id"], 1, incomplete)
        self.assertFalse(first["ok"])
        self.assertFalse(first["copy_matches_frozen"])
        self.assertEqual(first["unchecked_fields"], ["author_note"])
        mismatch = publishing.record_compare(self.book, self.plan["id"], 1,
                                             self.copied(body=self.frozen["body"] + "错字"))
        self.assertFalse(mismatch["copy_matches_frozen"])
        self.assertFalse(mismatch["field_comparisons"]["body"]["matches_frozen"])
        self.assertEqual(self.count(), 2)
        for item in (first, mismatch):
            saved = publishing.inspect_comparison(self.book, item["record_id"])["local_comparison_record"]
            self.assertFalse(saved["usable_at_record"])
            self.assertEqual(saved["remote_state"], "unknown")

    def test_old_record_remains_historical_after_revision_and_cancellation(self):
        first = publishing.record_compare(self.book, self.plan["id"], 1, self.copied())
        self.fixture.commit(text=self.fixture.texts[1] + "她又记下一笔。\n", replace_last=True)
        before_ledger = self.fixture.ledger.read_bytes()
        old = publishing.inspect_comparison(self.book, first["record_id"])
        self.assertTrue(old["local_comparison_record"]["usable_at_record"])
        self.assertFalse(old["source_check_performed"])
        self.assertEqual(self.fixture.ledger.read_bytes(), before_ledger)
        second = publishing.record_compare(self.book, self.plan["id"], 1, self.copied())
        self.assertTrue(second["copy_matches_frozen"])
        self.assertFalse(second["source_matches_current"])
        self.assertFalse(second["usable_now"])
        publishing.cancel(self.book, self.plan["id"])
        third = publishing.record_compare(self.book, self.plan["id"], 1, self.copied())
        self.assertEqual(third["status"], "cancelled")
        self.assertFalse(third["usable_now"])
        self.assertEqual(publishing.compare_history(self.book, self.plan["id"])["total"], 3)
        self.assertTrue(publishing.inspect_comparison(self.book, first["record_id"])["local_comparison_record"]["usable_at_record"])

    def test_budget_or_invalid_input_never_writes_half_a_record(self):
        before = self.fixture.ledger.read_bytes()
        self.assert_code("budget_exceeded", publishing.record_compare,
                         self.book, self.plan["id"], 1, self.copied(), 256)
        self.assertEqual(before, self.fixture.ledger.read_bytes())
        self.assert_code("invalid_input", publishing.record_compare,
                         self.book, self.plan["id"], 1, {"title": self.frozen["title"]})
        self.assertEqual(self.count(), 0)
        with patch.object(publishing, "MAX_COMPARISON_RECORDS", 0):
            self.assert_code("publish_compare_limit", publishing.record_compare,
                             self.book, self.plan["id"], 1, self.copied())
        self.assertEqual(self.count(), 0)

    def test_history_paginates_and_backup_recover_include_comparison_records(self):
        ids = [publishing.record_compare(self.book, self.plan["id"], 1, self.copied())["record_id"]
               for _ in range(3)]
        first = publishing.compare_history(self.book, self.plan["id"], 0, 2)
        second = publishing.compare_history(self.book, self.plan["id"], 2, 2)
        self.assertEqual(first["total"], 3)
        self.assertEqual(first["next_offset"], 2)
        self.assertIsNone(second["next_offset"])
        self.assertEqual({item["id"] for item in first["results"] + second["results"]}, set(ids))
        self.assertEqual(publishing.recover(self.book)["local_comparison_records"], 3)
        backup = publishing.backup(self.book)
        self.assertEqual(backup["local_comparison_records"], 3)
        with closing(sqlite3.connect(backup["backup"])) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM meta WHERE key GLOB 'comparison:*'").fetchone()[0], 3)
        self.assertEqual(self.fixture.writing_snapshot()[0], self.fixture.rev())

    def test_corrupt_or_cross_plan_record_is_not_silently_listed_or_backed_up(self):
        record = publishing.record_compare(self.book, self.plan["id"], 1, self.copied())
        with closing(sqlite3.connect(self.fixture.ledger)) as db, db:
            key = "comparison:" + record["record_id"]
            data = json.loads(db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0])
            data["record"]["remote_state"] = "live"
            data["sha256"] = publishing._hash(data["record"])
            db.execute("UPDATE meta SET value=? WHERE key=?", (story.dumps(data), key))
        self.assert_code("publishing_corrupt", publishing.inspect_comparison, self.book, record["record_id"])
        self.assert_code("publishing_corrupt", publishing.compare_history, self.book, self.plan["id"])
        self.assert_code("publishing_corrupt", publishing.recover, self.book)
        self.assert_code("publishing_corrupt", publishing.backup, self.book)

    def test_schema_one_without_records_and_cli_record_history_inspect(self):
        self.assertEqual(publishing.compare_history(self.book, self.plan["id"])["total"], 0)
        self.assertEqual(publishing.recover(self.book)["local_comparison_records"], 0)
        input_path = self.fixture.root / ".story" / "author-copy.json"
        input_path.write_text(json.dumps(self.copied(), ensure_ascii=False), encoding="utf-8")
        base = [sys.executable, "-B", str(fixtures.TOOL)]
        record_cmd = base + ["publish-compare-record", "--book", str(self.fixture.root),
                             "--id", self.plan["id"], "--chapter", "1", "--input", str(input_path)]
        process = subprocess.run(record_cmd, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        record = json.loads(process.stdout)
        self.assertTrue(record["record_saved"])
        history_cmd = base + ["publish-compare-history", "--book", str(self.fixture.root),
                              "--id", self.plan["id"], "--offset", "0", "--limit", "10"]
        history = subprocess.run(history_cmd, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(history.returncode, 0, history.stderr)
        self.assertEqual(json.loads(history.stdout)["total"], 1)
        inspect_cmd = base + ["publish-compare-inspect", "--book", str(self.fixture.root),
                              "--record-id", record["record_id"]]
        inspection = subprocess.run(inspect_cmd, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(inspection.returncode, 0, inspection.stderr)
        self.assertTrue(json.loads(inspection.stdout)["record_valid"])
        self.assertNotIn(self.frozen["body"], inspection.stdout)


if __name__ == "__main__":
    unittest.main()
