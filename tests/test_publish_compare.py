"""A user-supplied copy can be compared locally without claiming platform proof."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests import test_publish as fixtures


publishing, story = fixtures.publishing, fixtures.story


class PublishCompareTests(unittest.TestCase):
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

    def compare(self, copy=None, budget=publishing.DEFAULT_BUDGET):
        return publishing.compare(self.book, self.plan["id"], 1,
                                  self.copied() if copy is None else copy, budget)

    def assert_code(self, code, *args, **kwargs):
        with self.assertRaises(story.StoryError) as caught:
            publishing.compare(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception.details)

    def test_complete_copy_matches_only_after_documented_newline_conversion(self):
        copy = self.copied(body=self.frozen["body"].replace("\n", "\r\n"))
        before_ledger = self.fixture.ledger.read_bytes()
        before_writing = self.fixture.writing_snapshot()
        result = self.compare(copy)
        self.assertTrue(result["ok"])
        self.assertTrue(result["copy_matches_frozen"])
        self.assertTrue(result["usable_now"])
        self.assertEqual(result["fields_checked"], ["title", "body", "author_note"])
        self.assertEqual(result["unchecked_fields"], [])
        self.assertTrue(result["source_check_performed"])
        self.assertTrue(result["source_matches_current"])
        self.assertEqual(result["source_checked_chapters"], 1)
        self.assertEqual(result["copy_source"], "user_supplied_copy")
        self.assertEqual(result["normalization"], "crlf_cr_to_lf_v1")
        self.assertEqual(result["remote_state"], "unknown")
        self.assertFalse(result["platform_verified"])
        self.assertFalse(result["ready_to_upload"])
        self.assertEqual(result["binding_status"], "user_declared_unverified")
        body = result["field_comparisons"]["body"]
        self.assertTrue(body["newline_normalization_applied"])
        self.assertEqual(body["readback_sha256"], hashlib.sha256(copy["body"].encode("utf-8")).hexdigest())
        self.assertEqual(body["normalized_readback_sha256"], body["normalized_frozen_sha256"])
        self.assertNotIn(self.frozen["body"], json.dumps(result, ensure_ascii=False))
        self.assertEqual(self.fixture.ledger.read_bytes(), before_ledger)
        self.assertEqual(self.fixture.writing_snapshot(), before_writing)

    def test_missing_author_note_is_explicitly_unchecked_not_a_complete_match(self):
        copy = {"title": self.frozen["title"], "body": self.frozen["body"]}
        result = self.compare(copy)
        self.assertEqual(result["fields_checked"], ["title", "body"])
        self.assertEqual(result["unchecked_fields"], ["author_note"])
        self.assertTrue(result["supplied_fields_match_frozen"])
        self.assertFalse(result["readback_complete"])
        self.assertFalse(result["copy_matches_frozen"])
        self.assertFalse(result["usable_now"])
        self.assertFalse(result["ok"])
        self.assertFalse(result["field_comparisons"]["author_note"]["checked"])
        self.assertIsNone(result["field_comparisons"]["author_note"]["matches_frozen"])

    def test_lone_cr_is_the_only_other_accepted_text_change(self):
        copy = self.copied(body=self.frozen["body"].replace("\n", "\r"))
        result = self.compare(copy)
        self.assertTrue(result["copy_matches_frozen"])
        self.assertTrue(result["field_comparisons"]["body"]["newline_normalization_applied"])

    def test_spaces_and_punctuation_are_not_normalized_away(self):
        copy = self.copied(title=self.frozen["title"] + "！", body=self.frozen["body"].replace("，", "， ", 1))
        result = self.compare(copy)
        self.assertFalse(result["copy_matches_frozen"])
        self.assertFalse(result["usable_now"])
        self.assertFalse(result["field_comparisons"]["title"]["matches_frozen"])
        self.assertFalse(result["field_comparisons"]["body"]["matches_frozen"])
        self.assertTrue(result["field_comparisons"]["author_note"]["matches_frozen"])

    def test_old_copy_remains_a_frozen_match_but_is_unusable_after_formal_revision(self):
        before_plan = self.fixture.inspect(self.plan["id"])
        self.fixture.commit(text=self.fixture.texts[1] + "她又记下一笔。\n", replace_last=True)
        before_ledger = self.fixture.ledger.read_bytes()
        before_writing = self.fixture.writing_snapshot()
        result = self.compare()
        self.assertTrue(result["copy_matches_frozen"])
        self.assertFalse(result["source_matches_current"])
        self.assertFalse(result["usable_now"])
        self.assertEqual(result["source_changed_chapters"], [1])
        self.assertEqual(self.fixture.inspect(self.plan["id"])["status"], before_plan["status"])
        self.assertEqual(self.fixture.ledger.read_bytes(), before_ledger)
        self.assertEqual(self.fixture.writing_snapshot(), before_writing)

    def test_cancelled_plan_remains_unusable_and_comparison_is_read_only(self):
        publishing.cancel(self.book, self.plan["id"])
        before_ledger = self.fixture.ledger.read_bytes()
        result = self.compare()
        self.assertTrue(result["copy_matches_frozen"])
        self.assertTrue(result["source_matches_current"])
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(result["usable_now"])
        self.assertEqual(self.fixture.ledger.read_bytes(), before_ledger)

    def test_rejects_unknown_missing_nontext_and_oversized_fields(self):
        bad = ({"title": self.frozen["title"]},
               {"title": self.frozen["title"], "body": self.frozen["body"], "platform": "fanqie"},
               {"title": 1, "body": self.frozen["body"]},
               {"title": self.frozen["title"], "body": None},
               [self.frozen["title"], self.frozen["body"]],
               {"title": "\ud800", "body": self.frozen["body"]})
        for value in bad:
            with self.subTest(value_type=type(value).__name__):
                self.assert_code("invalid_input", self.book, self.plan["id"], 1, value)
        self.assert_code("invalid_input", self.book, self.plan["id"], True, self.copied())
        self.assert_code("publish_chapter_missing", self.book, self.plan["id"], 2, self.copied())
        with patch.object(publishing, "MAX_MEMBER_BYTES", 1):
            self.assert_code("invalid_input", self.book, self.plan["id"], 1, self.copied())

    def test_budget_is_hard_and_cli_reports_only_local_evidence(self):
        before_ledger = self.fixture.ledger.read_bytes()
        self.assert_code("budget_exceeded", self.book, self.plan["id"], 1, self.copied(), 256)
        self.assertEqual(self.fixture.ledger.read_bytes(), before_ledger)
        input_path = Path(self.fixture.root) / ".story" / "supplied-copy.json"
        input_path.write_text(json.dumps(self.copied(), ensure_ascii=False), encoding="utf-8")
        command = [sys.executable, "-B", str(fixtures.TOOL), "publish-compare",
                   "--book", str(self.fixture.root), "--id", self.plan["id"],
                   "--chapter", "1", "--input", str(input_path)]
        process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        packet = json.loads(process.stdout)
        self.assertTrue(packet["copy_matches_frozen"])
        self.assertFalse(packet["platform_verified"])
        self.assertEqual(packet["remote_state"], "unknown")
        self.assertNotIn(self.frozen["body"], process.stdout)
        small = subprocess.run(command + ["--budget-bytes", "256"], capture_output=True,
                               text=True, encoding="utf-8", timeout=20)
        self.assertNotEqual(small.returncode, 0)
        self.assertEqual(json.loads(small.stderr)["error"], "budget_exceeded")
        self.assertEqual(self.fixture.ledger.read_bytes(), before_ledger)


if __name__ == "__main__":
    unittest.main()
