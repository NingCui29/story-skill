"""A saved publishing package is an archive of a frozen local plan, not live platform state."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
import warnings
import zipfile
from uuid import uuid4
from unittest.mock import patch

from tests import test_publish as fixtures


publishing, story = fixtures.publishing, fixtures.story


class PublishExportVerifyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublishTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.book = self.fixture.book
        self.fixture.commit()
        self.prepared = self.fixture.prepare()
        self.exported = publishing.export_material(self.book, self.prepared["id"])
        self.archive = Path(self.exported["export"]["path"])

    def altered_archive(self, replacement=None, extra=None, omit=None):
        """Write another CRC-valid ZIP; the original export stays available for comparison."""
        destination = self.archive.with_name("changed-" + uuid4().hex + ".zip")
        replacement = replacement or {}
        with zipfile.ZipFile(self.archive) as source, zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as target:
            for member in source.infolist():
                if member.filename != omit:
                    target.writestr(member.filename, replacement.get(member.filename, source.read(member)))
            for name, value in extra or []:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    target.writestr(name, value)
        with zipfile.ZipFile(destination) as result:
            self.assertIsNone(result.testzip(), "The test must tamper with content, not CRC bytes")
        return destination

    def changed_json(self, name, **fields):
        with zipfile.ZipFile(self.archive) as source:
            value = json.loads(source.read(name).decode("utf-8"))
        value.update(fields)
        return self.altered_archive({name: story.dumps(value).encode("utf-8")})

    def verify(self, candidate=None, expected_id=None, expected_sha=None):
        candidate = candidate or self.archive
        return publishing.verify_export(self.book, candidate,
                                        expected_sha or hashlib.sha256(candidate.read_bytes()).hexdigest(),
                                        expected_id or self.prepared["id"])

    def assert_rejected_without_writes(self, candidate, code=None):
        before_ledger = self.fixture.ledger.read_bytes()
        before_story = self.fixture.writing_snapshot()
        with self.assertRaises(story.StoryError) as caught:
            self.verify(candidate)
        if code is not None:
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.fixture.ledger.read_bytes(), before_ledger)
        self.assertEqual(self.fixture.writing_snapshot(), before_story)

    def test_valid_export_verifies_saved_artifact_and_current_source_without_writing_ledgers(self):
        before_ledger = self.fixture.ledger.read_bytes()
        before_story = self.fixture.writing_snapshot()
        result = self.verify()
        self.assertTrue(result["artifact_verified"])
        self.assertEqual(result["id"], self.prepared["id"])
        self.assertEqual(result["status"], "prepared")
        self.assertTrue(result["source_check_performed"])
        self.assertTrue(result["source_matches_current"])
        self.assertTrue(result["usable_now"])
        self.assertEqual(result["remote_state"], "unknown")
        self.assertFalse(result["platform_verified"])
        self.assertFalse(result["ready_to_upload"])
        self.assertEqual(self.fixture.ledger.read_bytes(), before_ledger)
        self.assertEqual(self.fixture.writing_snapshot(), before_story)

    def test_crc_valid_title_and_body_rewrites_are_rejected(self):
        for member, replacement in (("章节/第1章/标题.txt", "伪造章名"),
                                    ("章节/第1章/正文.txt", "这段已被替换。")):
            with self.subTest(member=member):
                candidate = self.altered_archive({member: replacement.encode("utf-8")})
                self.assert_rejected_without_writes(candidate)

    def test_crc_valid_guide_and_directory_rewrites_are_rejected(self):
        for member in ("使用说明.txt", "目录.txt"):
            with self.subTest(member=member):
                candidate = self.altered_archive({member: "伪造使用指引。".encode("utf-8")})
                self.assert_rejected_without_writes(candidate)

    def test_required_external_digest_accepts_exact_package_and_rejects_a_mismatch(self):
        expected = self.exported["export"]["sha256"]
        result = self.verify(expected_sha=expected)
        self.assertTrue(result["artifact_verified"])
        self.assertTrue(result["usable_now"])
        before_ledger = self.fixture.ledger.read_bytes()
        with self.assertRaises(story.StoryError):
            self.verify(expected_sha="0" * 64)
        self.assertEqual(self.fixture.ledger.read_bytes(), before_ledger)

        changed_guide = self.altered_archive({"使用说明.txt": "仍可正确解压的伪造说明。".encode("utf-8")})
        self.assert_rejected_without_writes(changed_guide)
        with self.assertRaises(story.StoryError):
            self.verify(changed_guide, expected_sha=expected)
        self.assertEqual(self.fixture.ledger.read_bytes(), before_ledger)

    def test_manifest_and_receipt_cannot_claim_another_book_or_plan(self):
        wrong_book = self.changed_json("manifest.json", book_id="another-book")
        self.assert_rejected_without_writes(wrong_book)

        self.fixture.commit(2)
        other_plan = self.fixture.prepare((2,))
        wrong_known_plan = self.changed_json("receipt.json", id=other_plan["id"])
        self.assert_rejected_without_writes(wrong_known_plan)

        unknown_plan = self.changed_json("receipt.json", id="f" * 32)
        self.assert_rejected_without_writes(unknown_plan)

    def test_another_complete_package_from_same_book_cannot_pass_the_expected_plan_id(self):
        self.fixture.commit(2)
        other = self.fixture.prepare((2,))
        other_export = publishing.export_material(self.book, other["id"])
        other_path = Path(other_export["export"]["path"])
        self.assertNotEqual(other["id"], self.prepared["id"])
        self.assert_rejected_without_writes(other_path, "publish_export_mismatch")

    def test_corrupt_manifest_deflate_stream_returns_a_structured_error(self):
        original = bytearray(self.archive.read_bytes())
        with zipfile.ZipFile(self.archive) as archive:
            info = archive.getinfo("manifest.json")
        self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)
        start = info.header_offset
        self.assertEqual(original[start:start + 4], b"PK\x03\x04")
        filename_size = int.from_bytes(original[start + 26:start + 28], "little")
        extra_size = int.from_bytes(original[start + 28:start + 30], "little")
        compressed_start = start + 30 + filename_size + extra_size
        # In raw DEFLATE, the first block type 0b11 is reserved and must fail decompression.
        original[compressed_start] = (original[compressed_start] & ~0b110) | 0b110
        damaged = self.archive.with_name("damaged-" + uuid4().hex + ".zip")
        damaged.write_bytes(original)
        with zipfile.ZipFile(damaged) as archive:
            self.assertEqual(archive.getinfo("manifest.json").compress_type, zipfile.ZIP_DEFLATED)
        self.assert_rejected_without_writes(damaged, "publish_export_invalid")

    def test_duplicate_member_and_path_traversal_are_rejected(self):
        with zipfile.ZipFile(self.archive) as source:
            duplicate_body = source.read("章节/第1章/正文.txt")
        duplicate = self.altered_archive(extra=[("章节/第1章/正文.txt", duplicate_body)])
        self.assert_rejected_without_writes(duplicate)
        traversal = self.altered_archive(extra=[("../outside.txt", b"unexpected")])
        self.assert_rejected_without_writes(traversal)

    def test_saved_package_stays_verifiable_after_cancel_without_reviving_plan(self):
        publishing.cancel(self.book, self.prepared["id"])
        before_ledger = self.fixture.ledger.read_bytes()
        result = self.verify()
        self.assertTrue(result["artifact_verified"])
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(result["source_check_performed"])
        self.assertTrue(result["source_matches_current"])
        self.assertFalse(result["usable_now"])
        self.assertEqual(self.fixture.ledger.read_bytes(), before_ledger)
        self.assertEqual(self.fixture.inspect(self.prepared["id"])["status"], "cancelled")

    def test_saved_package_stays_verifiable_after_stale_check_without_reviving_plan(self):
        self.fixture.commit(text=self.fixture.texts[1] + "她回头又看了一眼。", replace_last=True)
        self.assertEqual(publishing.check(self.book, self.prepared["id"])["status"], "stale")
        before_ledger = self.fixture.ledger.read_bytes()
        result = self.verify()
        self.assertTrue(result["artifact_verified"])
        self.assertEqual(result["status"], "stale")
        self.assertTrue(result["source_check_performed"])
        self.assertFalse(result["source_matches_current"])
        self.assertFalse(result["usable_now"])
        self.assertEqual(self.fixture.ledger.read_bytes(), before_ledger)
        self.assertEqual(self.fixture.inspect(self.prepared["id"])["status"], "stale")

    def test_changed_formal_body_fails_live_check_without_changing_saved_plan_status(self):
        self.fixture.commit(text=self.fixture.texts[1] + "她回头又看了一眼。", replace_last=True)
        before_ledger = self.fixture.ledger.read_bytes()
        result = self.verify()
        self.assertTrue(result["artifact_verified"])
        self.assertEqual(result["status"], "prepared")  # Stored status until publish-check runs.
        self.assertTrue(result["source_check_performed"])
        self.assertFalse(result["source_matches_current"])
        self.assertFalse(result["usable_now"])
        self.assertFalse(result["ready_to_upload"])
        self.assertEqual(self.fixture.ledger.read_bytes(), before_ledger)

    def test_zip_rewritten_after_snapshot_fails_closed_for_receipt_and_explicit_hash(self):
        original = self.archive.read_bytes()
        damaged = bytearray(original)
        damaged[len(damaged) // 2] ^= 1  # Same size; a size-only final check is insufficient.
        real_check = publishing._source_comparison
        receipt_path = self.exported["export"]["receipt_path"]
        calls = (
            ("receipt", lambda: publishing.verify_export_receipt(self.book, receipt_path)),
            ("explicit_hash", lambda: publishing.verify_export(self.book, self.archive,
                                                                 self.exported["export"]["sha256"], self.prepared["id"])),
        )
        for mode, verify in calls:
            with self.subTest(mode=mode):
                def change_after_snapshot(book, manifest):
                    self.archive.write_bytes(damaged)
                    return real_check(book, manifest)

                with patch.object(publishing, "_source_comparison", side_effect=change_after_snapshot):
                    with self.assertRaises(story.StoryError) as caught:
                        verify()
                self.assertEqual(caught.exception.code, "unsafe_publish_path")
                self.assertEqual(self.archive.read_bytes(), damaged)
                self.archive.write_bytes(original)

    def test_zip_replaced_with_identical_bytes_after_snapshot_fails_closed(self):
        original = self.archive.read_bytes()
        displaced = self.archive.with_name("displaced-" + self.archive.name)
        real_check = publishing._source_comparison

        def replace_after_snapshot(book, manifest):
            self.archive.rename(displaced)
            self.archive.write_bytes(original)
            return real_check(book, manifest)

        with patch.object(publishing, "_source_comparison", side_effect=replace_after_snapshot):
            try:
                with self.assertRaises(story.StoryError) as caught:
                    publishing.verify_export(self.book, self.archive,
                                             self.exported["export"]["sha256"], self.prepared["id"])
            except OSError as error:
                self.skipTest(f"Host cannot replace a pinned export: {error}")
        self.assertEqual(caught.exception.code, "unsafe_publish_path")
        self.assertEqual(self.archive.read_bytes(), original)

    def test_external_edit_of_formal_export_marks_saved_package_unusable_without_repair(self):
        chapter = self.fixture.root / self.book.chapter_path(1)
        changed = "外部直接修改后的正文，尚未经正式提交。".encode("utf-8")
        chapter.write_bytes(changed)
        before_ledger = self.fixture.ledger.read_bytes()
        before_story = self.fixture.writing_snapshot()
        result = self.verify()
        self.assertTrue(result["artifact_verified"])
        self.assertEqual(result["status"], "prepared")
        self.assertTrue(result["source_check_performed"])
        self.assertFalse(result["source_matches_current"])
        self.assertFalse(result["usable_now"])
        self.assertEqual(chapter.read_bytes(), changed)
        self.assertEqual(self.fixture.ledger.read_bytes(), before_ledger)
        self.assertEqual(self.fixture.writing_snapshot(), before_story)

    def test_export_rejects_material_above_member_limit_without_leaving_a_partial_package(self):
        output = self.archive.parent
        before = {path.name: path.read_bytes() for path in output.iterdir() if path.is_file()}
        with patch.object(publishing, "MAX_MEMBER_BYTES", 1):
            with self.assertRaises(story.StoryError):
                publishing.export_material(self.book, self.prepared["id"])
        after = {path.name: path.read_bytes() for path in output.iterdir() if path.is_file()}
        self.assertEqual(after, before)

    def test_cli_verifies_a_saved_zip_and_reports_current_source_scope(self):
        command = [sys.executable, "-B", str(fixtures.TOOL), "publish-verify-export",
                   "--book", str(self.fixture.root), "--file", str(self.archive),
                   "--id", self.prepared["id"], "--sha256", self.exported["export"]["sha256"]]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        packet = json.loads(result.stdout)
        self.assertTrue(packet["artifact_verified"])
        self.assertEqual(packet["status"], "prepared")
        self.assertTrue(packet["source_check_performed"])
        self.assertTrue(packet["source_matches_current"])
        self.assertTrue(packet["usable_now"])

    def test_cli_requires_both_original_plan_id_and_archive_sha(self):
        base = [sys.executable, "-B", str(fixtures.TOOL), "publish-verify-export",
                "--book", str(self.fixture.root), "--file", str(self.archive)]
        for arguments in (["--sha256", self.exported["export"]["sha256"]],
                          ["--id", self.prepared["id"]]):
            with self.subTest(provided=arguments[0]):
                result = subprocess.run(base + arguments, capture_output=True, text=True,
                                        encoding="utf-8", timeout=20)
                self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
