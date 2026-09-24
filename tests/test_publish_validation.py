"""Schema-1 snapshots remain readable only with complete, book-bound proofs."""
import copy
import json
import sqlite3
import unittest
import uuid

from tests import test_publish as fixtures


story, publishing = fixtures.story, fixtures.publishing


class PublishValidationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublishTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.commit()
        self.prepared = self.fixture.prepare()
        self.book, self.ledger = self.fixture.book, self.fixture.ledger

    def assert_code(self, code, call, *args, **kwargs):
        with self.assertRaises(story.StoryError) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception.details)
        return caught.exception

    def row(self, manifest=None, **fields):
        manifest = copy.deepcopy(self.prepared["manifest"] if manifest is None else manifest)
        return {"id": uuid.uuid4().hex, "fingerprint": publishing._hash(manifest),
                "manifest": story.dumps(manifest), "created": publishing._now(),
                "status": "prepared", "reasons": "[]", "checked_at": None, **fields}

    def insert(self, manifest, **fields):
        row = self.row(manifest, **fields)
        db = sqlite3.connect(self.ledger)
        try:
            with db:
                db.execute("INSERT INTO plans VALUES (?,?,?,?,?,?,?)", tuple(row[field] for field in
                           ("id", "fingerprint", "manifest", "created", "status", "reasons", "checked_at")))
        finally:
            db.close()
        return row["id"]

    def assert_manifest_rejected(self, manifest):
        self.assert_code("publishing_corrupt", publishing._validated_manifest,
                         self.row(manifest), self.book.meta("id"))

    def test_required_manifest_fields_and_types_are_checked_beyond_outer_digest(self):
        original = self.prepared["manifest"]
        for field in original:
            with self.subTest(missing=field):
                candidate = copy.deepcopy(original)
                del candidate[field]
                self.assert_manifest_rejected(candidate)
        invalid = {"platform": ("other", 1, None), "mode": ("publish", True),
                   "account_id": ("", " author", "author\n", 3),
                   "remote_book_id": ("", "book ", [], None),
                   "book_id": ("", None, "another-book"), "book_title": ("", {}, 3),
                   "book_kind": ("analysis", [], None), "source_revision": (True, -1, 1.0, 2**63),
                   "chapters": ([], {}, None), "binding_status": ("verified", False),
                   "conversion_version": (True, 2, "1")}
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    candidate = copy.deepcopy(original)
                    candidate[field] = value
                    self.assert_manifest_rejected(candidate)

    def test_required_chapter_proofs_cannot_be_omitted_or_mistyped(self):
        original = self.prepared["manifest"]
        for field in original["chapters"][0]:
            with self.subTest(missing=field):
                candidate = copy.deepcopy(original)
                del candidate["chapters"][0][field]
                self.assert_manifest_rejected(candidate)
        invalid = {"chapter": (True, 0, -1, "1", 2**63), "head_id": ("", None, "bad-head"),
                   "body_sha": ("", None, "0" * 63), "review_receipt_sha": ("", [], "G" * 64),
                   "chapter_path": (None, "", "../chapter.md", "/tmp/chapter.md", "C:\\chapter.md"),
                   "source_revision": (True, "1", original["source_revision"] + 1),
                   "title": ("", " title", "title\n", {}), "body": ("", " \n", []),
                   "author_note": (None, [], 1), "conversion_rule": (None, "unverified"),
                   "upload_sha256": (None, "", "0" * 64), "body_characters": (True, 0, "1"),
                   "local_visible_nonspace_v1": (True, 0, None)}
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    candidate = copy.deepcopy(original)
                    candidate["chapters"][0][field] = value
                    self.assert_manifest_rejected(candidate)

    def test_chapter_range_must_be_nonempty_ordered_and_consecutive(self):
        for numbers in ([1, 1], [2, 1], [1, 3]):
            with self.subTest(numbers=numbers):
                candidate = copy.deepcopy(self.prepared["manifest"])
                item = candidate["chapters"][0]
                candidate["chapters"] = [{**item, "chapter": number} for number in numbers]
                self.assert_manifest_rejected(candidate)

    def test_upload_content_must_match_its_own_digest(self):
        for field, value in (("body", fixtures.BODY + "账页最后多了一笔。\n"),
                             ("title", "另一个章名"), ("author_note", "新增作者的话")):
            with self.subTest(field=field):
                candidate = copy.deepcopy(self.prepared["manifest"])
                item = candidate["chapters"][0]
                item[field] = value
                item["body_characters"] = len(item["body"])
                item["local_visible_nonspace_v1"] = story.visible_count(item["body"])
                # Rehashing the outer manifest cannot excuse a mismatched inner upload proof.
                self.assert_manifest_rejected(candidate)

    def test_unchanged_body_conversion_checks_original_body_proof(self):
        candidate = copy.deepcopy(self.prepared["manifest"])
        candidate["chapters"][0]["conversion_rule"] = "body_unchanged_v1"
        self.assert_manifest_rejected(candidate)
        item = candidate["chapters"][0]
        item["body_sha"] = story.digest(item["body"])
        self.assertEqual(publishing._validated_manifest(self.row(candidate), self.book.meta("id")), candidate)

    def test_wrong_book_manifest_is_rejected_by_every_existing_ledger_reader(self):
        manifest = copy.deepcopy(self.prepared["manifest"])
        manifest["book_id"] = str(uuid.uuid4())
        plan_id = self.insert(manifest)
        before = self.ledger.read_bytes()
        for reader, args in ((publishing.recover, (self.book,)),
                             (publishing.inspect, (self.book, plan_id)),
                             (publishing.list_plans, (self.book,)),
                             (publishing.check, (self.book, plan_id)),
                             (publishing.backup, (self.book,))):
            with self.subTest(reader=reader.__name__):
                self.assert_code("publishing_corrupt", reader, *args)
        self.assertEqual(self.ledger.read_bytes(), before)
        self.assertFalse((self.fixture.root / ".story/publishing-backups").exists())

    def test_empty_and_incomplete_chapters_fail_with_story_error_before_use(self):
        for chapters in ([], [{"chapter": 1}]):
            with self.subTest(chapters=chapters):
                manifest = copy.deepcopy(self.prepared["manifest"])
                manifest["chapters"] = chapters
                plan_id = self.insert(manifest)
                self.assert_code("publishing_corrupt", publishing.inspect, self.book, plan_id)
                self.assert_code("publishing_corrupt", publishing.check, self.book, plan_id)
                self.assert_code("publishing_corrupt", publishing.recover, self.book)

    def test_status_timestamps_and_reason_shapes_do_not_raise_raw_errors(self):
        for field, values in {"id": (None, ""), "status": (None, "uploaded"),
                              "created": (None, ""), "checked_at": (False, ""),
                              "reasons": ("{}", "[null]", '[{"note":"missing code"}]', "not json")}.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assert_code("publishing_corrupt", publishing._validated_manifest,
                                     self.row(**{field: value}), self.book.meta("id"))

    def test_valid_schema_one_supports_crlf_non_bmp_and_optional_metadata(self):
        text = "第1章 门后的账本1\r\n" + fixtures.BODY.replace("\n", "\r\n") + "门上刻着𠮷字。\r\n"
        self.fixture.commit(text=text, replace_last=True)
        prepared = self.fixture.prepare()
        self.assertIn("𠮷", prepared["manifest"]["chapters"][0]["body"])
        self.assertTrue(publishing.recover(self.book)["ok"])
        self.assertEqual(publishing.inspect(self.book, prepared["id"])["manifest"], prepared["manifest"])
        manifest = copy.deepcopy(prepared["manifest"])
        manifest["archival_note"] = "Older schema-1 producers may preserve extra audit metadata."
        manifest["chapters"][0]["archival_note"] = {"source": "author"}
        self.assertEqual(publishing._validated_manifest(self.row(manifest), self.book.meta("id")), manifest)

    def test_historical_frozen_proofs_remain_readable_after_live_revision(self):
        self.fixture.commit(text=self.fixture.texts[1] + "她将门轻轻掩上。\n", replace_last=True)
        self.assertFalse(publishing.check(self.book, self.prepared["id"])["ok"])
        self.assertTrue(publishing.recover(self.book)["ok"])
        stale = publishing.inspect(self.book, self.prepared["id"])
        self.assertEqual(stale["manifest"], self.prepared["manifest"])
        self.assertEqual(stale["status"], "stale")
        publishing.cancel(self.book, self.prepared["id"])
        self.assertEqual(publishing.inspect(self.book, self.prepared["id"])["manifest"], self.prepared["manifest"])

    def replace_schema(self, schema):
        db = sqlite3.connect(self.ledger)
        try:
            metadata = db.execute("SELECT * FROM meta").fetchall()
            plans = db.execute("SELECT * FROM plans").fetchall()
        finally:
            db.close()
        self.ledger.unlink()
        db = sqlite3.connect(self.ledger)
        try:
            with db:
                db.executescript(schema)
                db.executemany("INSERT INTO meta VALUES (?,?)", metadata)
                db.executemany("INSERT INTO plans VALUES (?,?,?,?,?,?,?)", plans)
        finally:
            db.close()

    def test_early_global_unique_schema_is_precisely_rejected_without_migration(self):
        old = publishing.SCHEMA.replace("fingerprint TEXT NOT NULL,", "fingerprint TEXT NOT NULL UNIQUE,")
        old = old.replace("CREATE UNIQUE INDEX plans_prepared_fingerprint ON plans(fingerprint) WHERE status='prepared';", "")
        self.replace_schema(old)
        before = self.ledger.read_bytes()
        for reader, args in ((publishing.recover, (self.book,)),
                             (publishing.inspect, (self.book, self.prepared["id"])),
                             (publishing.list_plans, (self.book,)),
                             (self.fixture.prepare, ())):
            with self.subTest(reader=reader.__name__):
                self.assert_code("publishing_schema_mismatch", reader, *args)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_named_global_unique_index_also_reports_schema_mismatch(self):
        schema = publishing.SCHEMA.replace("plans_prepared_fingerprint ON plans(fingerprint) WHERE status='prepared'",
                                          '"old\'fingerprint" ON plans(fingerprint)')
        self.replace_schema(schema)
        self.assert_code("publishing_schema_mismatch", publishing.recover, self.book)

    def test_equivalent_partial_index_schema_remains_readable_and_reusable(self):
        schema = publishing.SCHEMA.replace("plans_prepared_fingerprint ON plans(fingerprint) WHERE status='prepared'",
                                          '"differently_named" ON plans("fingerprint") WHERE ("status" = \'prepared\')')
        self.replace_schema(schema)
        self.assertTrue(publishing.recover(self.book)["ok"])
        publishing.cancel(self.book, self.prepared["id"])
        replacement = self.fixture.prepare()
        self.assertNotEqual(replacement["id"], self.prepared["id"])
        self.assertEqual(replacement["manifest"], self.prepared["manifest"])

    def test_boolean_schema_version_is_not_an_integer_version(self):
        db = sqlite3.connect(self.ledger)
        try:
            with db:
                db.execute("UPDATE meta SET value=? WHERE key='schema'", (json.dumps(True),))
        finally:
            db.close()
        self.assert_code("publishing_schema_mismatch", publishing.recover, self.book)


if __name__ == "__main__":
    unittest.main()
