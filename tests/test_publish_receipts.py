"""Recover and verify offline publishing exports using their adjacent receipts."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests import test_publish as fixtures


publishing, story = fixtures.publishing, fixtures.story


class PublishReceiptTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublishTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.book = self.fixture.book
        self.fixture.commit()
        self.plan = self.fixture.prepare()
        self.directory = self.fixture.root / ".story/publishing-exports"

    def export(self, plan=None):
        result = publishing.export_material(self.book, (plan or self.plan)["id"])
        self.assertTrue(result["export_created"])
        return result, Path(result["export"]["receipt_path"]), Path(result["export"]["path"])

    def receipt(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def test_export_saves_a_distinct_bound_receipt_next_to_its_zip(self):
        result, receipt_path, archive = self.export()
        record = self.receipt(receipt_path)
        self.assertEqual(receipt_path.parent, self.directory)
        self.assertEqual(receipt_path.name, archive.stem + ".receipt.json")
        self.assertEqual(record, {
            "schema": 1,
            "book_id": self.book.meta("id"),
            "plan_id": self.plan["id"],
            "manifest_sha256": self.plan["manifest_sha256"],
            "archive": archive.name,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "bytes": archive.stat().st_size,
            "exported_at": result["exported_at"],
        })
        self.assertEqual(record["sha256"], result["export"]["sha256"])

    def test_list_recovers_both_exports_without_selecting_a_latest_one(self):
        first, first_receipt, first_archive = self.export()
        first_bytes = first_receipt.read_bytes(), first_archive.read_bytes()
        second, second_receipt, second_archive = self.export()
        self.assertNotEqual(first_receipt, second_receipt)
        self.assertNotEqual(first_archive, second_archive)
        self.assertEqual((first_receipt.read_bytes(), first_archive.read_bytes()), first_bytes)

        result = publishing.list_exports(self.book)
        self.assertEqual(result["total"], 2)
        self.assertEqual({Path(item["receipt_path"]) for item in result["results"]},
                         {first_receipt, second_receipt})
        self.assertTrue(all(item["archive_exists"] for item in result["results"]))
        self.assertTrue(all(item["archive_size_matches_receipt"] for item in result["results"]))
        one = publishing.list_exports(self.book, offset=0, limit=1)
        two = publishing.list_exports(self.book, offset=1, limit=1)
        self.assertEqual(one["total"], two["total"])
        self.assertEqual(one["total"], 2)
        self.assertNotEqual(one["results"][0]["receipt_path"], two["results"][0]["receipt_path"])
        self.assertFalse(one.get("source_check_performed", False))
        self.assertFalse(two.get("source_check_performed", False))

    def test_list_filters_exact_plan_and_does_not_treat_another_export_as_a_replacement(self):
        _, first_receipt, _ = self.export()
        self.fixture.commit(2)
        second_plan = self.fixture.prepare((2,))
        _, second_receipt, _ = self.export(second_plan)
        first = publishing.list_exports(self.book, id=self.plan["id"])
        second = publishing.list_exports(self.book, id=second_plan["id"])
        self.assertEqual(first["total"], 1)
        self.assertEqual(second["total"], 1)
        self.assertEqual(Path(first["results"][0]["receipt_path"]), first_receipt)
        self.assertEqual(Path(second["results"][0]["receipt_path"]), second_receipt)
        self.assertEqual(publishing.list_exports(self.book, id="f" * 32)["total"], 0)

    def test_lost_terminal_output_can_be_recovered_and_verified_without_retyping_a_hash(self):
        _, receipt_path, archive = self.export()
        del archive  # The list, not a remembered ZIP path, is the recovery entry point.
        before_story = self.fixture.writing_snapshot()
        before_ledger = self.fixture.ledger.read_bytes()
        found = publishing.list_exports(self.book)["results"]
        self.assertEqual(len(found), 1)
        self.assertEqual(Path(found[0]["receipt_path"]), receipt_path)
        verified = publishing.verify_export_receipt(self.book, found[0]["receipt_path"])
        self.assertTrue(verified["artifact_verified"])
        self.assertTrue(verified["usable_now"])
        self.assertEqual(verified["id"], self.plan["id"])
        self.assertEqual(self.fixture.writing_snapshot(), before_story)
        self.assertEqual(self.fixture.ledger.read_bytes(), before_ledger)

    def test_receipt_does_not_claim_that_a_changed_or_cancelled_plan_is_usable(self):
        _, receipt_path, _ = self.export()
        self.fixture.commit(text=self.fixture.texts[1] + "她又记下一笔。\n", replace_last=True)
        listed = publishing.list_exports(self.book)
        self.assertEqual(listed["total"], 1)
        self.assertFalse(listed["source_check_performed"])
        before_ledger = self.fixture.ledger.read_bytes()
        changed = publishing.verify_export_receipt(self.book, receipt_path)
        self.assertTrue(changed["artifact_verified"])
        self.assertFalse(changed["source_matches_current"])
        self.assertFalse(changed["usable_now"])
        self.assertEqual(self.fixture.ledger.read_bytes(), before_ledger)
        publishing.cancel(self.book, self.plan["id"])
        cancelled = publishing.verify_export_receipt(self.book, receipt_path)
        self.assertTrue(cancelled["artifact_verified"])
        self.assertFalse(cancelled["usable_now"])
        self.assertEqual(cancelled["status"], "cancelled")

    def test_missing_zip_is_visible_in_list_and_rejected_by_verification(self):
        _, receipt_path, archive = self.export()
        archive.unlink()
        found = publishing.list_exports(self.book)["results"]
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]["archive_exists"])
        with self.assertRaises(story.StoryError):
            publishing.verify_export_receipt(self.book, receipt_path)

    def test_listing_reports_a_changed_zip_size_without_claiming_its_hash_was_checked(self):
        _, receipt_path, archive = self.export()
        archive.write_bytes(archive.read_bytes() + b"extra")
        listed = publishing.list_exports(self.book)
        self.assertEqual(listed["total"], 1)
        self.assertEqual(Path(listed["results"][0]["receipt_path"]), receipt_path)
        self.assertTrue(listed["results"][0]["archive_exists"])
        self.assertFalse(listed["results"][0]["archive_size_matches_receipt"])
        self.assertFalse(listed["archive_hash_checked"])
        with self.assertRaises(story.StoryError):
            publishing.verify_export_receipt(self.book, receipt_path)

    def test_missing_or_altered_receipt_and_modified_zip_are_rejected(self):
        for change in ("missing_receipt", "wrong_hash", "wrong_book", "wrong_plan",
                       "unsafe_archive_name", "changed_zip"):
            with self.subTest(change=change):
                _, receipt_path, archive = self.export()
                original_receipt = receipt_path.read_bytes()
                original_zip = archive.read_bytes()
                try:
                    if change == "missing_receipt":
                        receipt_path.unlink()
                    elif change == "changed_zip":
                        archive.write_bytes(original_zip + b"altered")
                    else:
                        record = self.receipt(receipt_path)
                        key, replacement = {
                            "wrong_hash": ("sha256", "0" * 64),
                            "wrong_book": ("book_id", "other-book"),
                            "wrong_plan": ("plan_id", "f" * 32),
                            "unsafe_archive_name": ("archive", "../outside.zip"),
                        }[change]
                        record[key] = replacement
                        receipt_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
                    with self.assertRaises(story.StoryError):
                        publishing.verify_export_receipt(self.book, receipt_path)
                finally:
                    receipt_path.write_bytes(original_receipt)
                    archive.write_bytes(original_zip)

    def test_linked_receipt_and_zip_cannot_redirect_verification(self):
        _, receipt_path, archive = self.export()
        outside = self.fixture.root.parent / "outside-material.zip"
        outside.write_bytes(archive.read_bytes())
        try:
            archive.unlink()
            archive.symlink_to(outside)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Host cannot create file symlink: {error}")
        with self.assertRaises(story.StoryError):
            publishing.verify_export_receipt(self.book, receipt_path)
        archive.unlink()
        outside_receipt = self.fixture.root.parent / "outside-material.receipt.json"
        outside_receipt.write_bytes(receipt_path.read_bytes())
        receipt_path.unlink()
        try:
            receipt_path.symlink_to(outside_receipt)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Host cannot create receipt symlink: {error}")
        with self.assertRaises(story.StoryError):
            publishing.verify_export_receipt(self.book, receipt_path)

    def test_receipt_changed_after_read_cannot_return_a_usable_package(self):
        _, receipt_path, _ = self.export()
        actual_verify = publishing.verify_export

        def change_receipt(*args, **kwargs):
            receipt_path.write_bytes(b'{"damaged":true}')
            return actual_verify(*args, **kwargs)

        with patch.object(publishing, "verify_export", side_effect=change_receipt):
            with self.assertRaises(story.StoryError) as caught:
                publishing.verify_export_receipt(self.book, receipt_path)
        self.assertEqual(caught.exception.code, "unsafe_publish_path")

    def test_same_bytes_replacement_after_read_cannot_reuse_receipt_identity(self):
        _, receipt_path, _ = self.export()
        original = receipt_path.read_bytes()
        displaced = receipt_path.with_name("displaced-receipt.json")
        actual_verify = publishing.verify_export

        def replace_receipt(*args, **kwargs):
            receipt_path.rename(displaced)
            receipt_path.write_bytes(original)
            return actual_verify(*args, **kwargs)

        with patch.object(publishing, "verify_export", side_effect=replace_receipt):
            try:
                with self.assertRaises(story.StoryError) as caught:
                    publishing.verify_export_receipt(self.book, receipt_path)
            except OSError as error:
                self.skipTest(f"Host cannot replace a pinned receipt: {error}")
        self.assertEqual(caught.exception.code, "unsafe_publish_path")

    def test_a_real_receipt_from_another_book_is_rejected(self):
        _, receipt_path, _ = self.export()
        other_root = self.fixture.root.parent / "另一部书"
        story.Book.create(other_root, "另一部书", "long")
        other = story.Book(other_root)
        try:
            with self.assertRaises(story.StoryError):
                publishing.verify_export_receipt(other, receipt_path)
        finally:
            other.close()

    def test_receipt_publication_conflict_preserves_existing_target_and_fails_closed(self):
        previous = set(self.directory.glob("*.receipt.json")) if self.directory.exists() else set()
        old_publish = story._publish_no_replace
        collisions = []

        def conflict(source, target):
            path = Path(target)
            if path.name.endswith(".receipt.json"):
                with path.open("xb") as stream:
                    stream.write(b"someone else's file")
                collisions.append(path)
            return old_publish(source, target)

        with patch.object(story, "_publish_no_replace", side_effect=conflict):
            with self.assertRaises(story.StoryError):
                publishing.export_material(self.book, self.plan["id"])
        self.assertEqual(len(collisions), 1)
        self.assertEqual(collisions[0].read_bytes(), b"someone else's file")
        created = set(self.directory.glob("*.receipt.json")) - previous
        self.assertEqual(created, {collisions[0]})

    def test_zip_rewritten_during_receipt_publication_cannot_report_a_successful_export(self):
        old_publish = story._publish_no_replace
        altered = []

        def rewrite_zip_before_receipt(source, target):
            path = Path(target)
            if path.name.endswith(".receipt.json"):
                archive = path.with_name(path.name.removesuffix(".receipt.json") + ".zip")
                archive.write_bytes(archive.read_bytes() + b"concurrent rewrite")
                altered.append(archive)
            return old_publish(source, target)

        with patch.object(story, "_publish_no_replace", side_effect=rewrite_zip_before_receipt):
            with self.assertRaises(story.StoryError) as caught:
                publishing.export_material(self.book, self.plan["id"])
        self.assertEqual(caught.exception.code, "publish_export_failed")
        self.assertEqual(len(altered), 1)
        self.assertTrue(altered[0].read_bytes().endswith(b"concurrent rewrite"))
        # Keep a concurrently changed file for diagnosis, but never mark it as usable.
        receipt = altered[0].with_name(altered[0].stem + ".receipt.json")
        with self.assertRaises(story.StoryError):
            publishing.verify_export_receipt(self.book, receipt)

    def test_one_corrupt_receipt_does_not_hide_a_separate_valid_export(self):
        _, first_receipt, _ = self.export()
        _, second_receipt, _ = self.export()
        second_receipt.write_bytes(b"{invalid json")
        listed = publishing.list_exports(self.book)
        self.assertEqual(listed["total"], 1)
        self.assertEqual([Path(item["receipt_path"]) for item in listed["results"]], [first_receipt])
        self.assertEqual(listed["invalid_total"], 1)
        self.assertEqual(len(listed["invalid_receipts"]), 1)
        self.assertEqual(Path(listed["invalid_receipts"][0]["receipt_path"]), second_receipt)
        self.assertTrue(listed["invalid_receipts"][0]["code"])
        self.assertTrue(publishing.verify_export_receipt(self.book, first_receipt)["artifact_verified"])
        with self.assertRaises(story.StoryError):
            publishing.verify_export_receipt(self.book, second_receipt)

    def test_cli_lists_and_verifies_a_receipt_and_rejects_mixed_verification_modes(self):
        _, receipt_path, _ = self.export()
        base = [sys.executable, "-B", str(fixtures.TOOL)]
        listed = subprocess.run(base + ["publish-export-list", "--book", str(self.fixture.root)],
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        packet = json.loads(listed.stdout)
        self.assertEqual(packet["total"], 1)
        self.assertEqual(Path(packet["results"][0]["receipt_path"]), receipt_path)

        verified = subprocess.run(base + ["publish-verify-export", "--book", str(self.fixture.root),
                                          "--receipt", str(receipt_path)],
                                  capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertTrue(json.loads(verified.stdout)["artifact_verified"])

        mixed = subprocess.run(base + ["publish-verify-export", "--book", str(self.fixture.root),
                                       "--receipt", str(receipt_path), "--id", self.plan["id"]],
                               capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertNotEqual(mixed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
