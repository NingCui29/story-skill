"""One operator attribution may name the remote ID of one interrupted allocation.

The attestation never proves the editor is blank: a live read-only inspection
of that exact ID and a one-use claim remain mandatory before any save.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from tests import test_platform_drafts as base
from tests import test_platform_draft_catalog_batch as batch

story, drafts = base.story, base.drafts
PLATFORM = drafts.PLATFORM_BODY_ENCODING


class AttributionEvidence:
    """Build, sign-by-binding and publish one private attestation file."""

    def make_evidence_dir(self):
        # Resolve macOS /var symlinks: the pinned walk refuses symlinked parents.
        directory = Path(tempfile.mkdtemp(prefix="operator-attribution-")).resolve()
        os.chmod(directory, 0o700)
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        return directory

    def evidence_value(self, journal, manifest, fingerprint, *, chapter=1, attempt=None,
                       remote_id=None, **overrides):
        attempt = attempt or self.attempt
        remote_id = self.remote if remote_id is None else remote_id
        events = [item for item in journal.events
                  if item["chapter"] == chapter and item["attempt_id"] == attempt]
        value = {"schema": 1, "kind": "operator_attributed_empty_draft",
                 "book_id": self.book.meta("id"), "plan_id": self.plan["id"],
                 "manifest_sha256": fingerprint,
                 "target_sha256": drafts._hash(drafts._target(manifest)),
                 "chapter": chapter, "attempt_id": attempt, "remote_id": remote_id,
                 "sending_sha256": drafts._hash(events[0]) if events else "0" * 64,
                 "unknown_sha256": drafts._hash(events[-1]) if events else "1" * 64,
                 "authorized_by_user": {
                     "text": "Synthetic operator confirms this blank draft is the interrupted allocation",
                     "confirmed_at": time.time()}}
        value.update(overrides)
        return value

    def write_value(self, value, name="attribution.json", mode=0o600):
        path = self.evidence_dir / name
        path.write_text(json.dumps(value), encoding="utf-8")
        os.chmod(path, mode)
        return path


class AttributedCandidateTests(AttributionEvidence, unittest.TestCase):
    def setUp(self):
        self.fixture = base.fixtures.PublishTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.commit()
        self.book = self.fixture.book
        self.plan = self.fixture.prepare()
        self.lock_root = self.fixture.root.parent / "global-draft-state"
        self.lock_root.mkdir(mode=0o700)
        patcher = patch.object(drafts, "_lock_directory", return_value=self.lock_root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.evidence_dir = self.make_evidence_dir()
        self.attempt, self.remote = "a" * 32, "leftover-draft"
        self.recovery_attempt = "b" * 32

    @contextmanager
    def ledger(self):
        with drafts._checked_plan(self.book, self.plan["id"]) as (manifest, fingerprint, _):
            pass
        with drafts._target_lock(manifest) as guard, drafts._journal(
                self.book, self.plan["id"], manifest, fingerprint, create=True) as journal:
            yield manifest, fingerprint, journal, guard

    def seed(self, *, sending_remote=None, unknown_remote=None):
        with self.ledger() as (manifest, fingerprint, journal, guard):
            frozen = manifest["chapters"][0]
            scope = {"book_id": self.book.meta("id"), "plan_id": self.plan["id"],
                     "manifest_sha256": fingerprint}
            guard.intent(1, self.attempt, frozen, body_encoding=PLATFORM, scope=scope)
            journal.append(1, "sending", attempt_id=self.attempt, remote_id=sending_remote, body_encoding=PLATFORM)
            journal.append(1, "unknown", attempt_id=self.attempt, remote_id=unknown_remote,
                           body_encoding=PLATFORM, reason="platform_browser_operation_failed")

    def attributed(self, **kwargs):
        with self.ledger() as (manifest, fingerprint, journal, _):
            path = self.write_value(self.evidence_value(journal, manifest, fingerprint, **kwargs))
            return drafts._attributed_empty_draft(self.book, self.plan["id"], manifest, fingerprint,
                                                  journal, str(path))

    def candidate(self, attributed, **kwargs):
        with self.ledger() as (manifest, fingerprint, journal, guard):
            return drafts._empty_recovery_candidate(self.book, self.plan["id"], manifest, fingerprint,
                journal, guard, 1, comparison_policy="strict", body_encoding=PLATFORM,
                attributed=attributed, **kwargs)

    def claim(self, candidate, attributed):
        with self.ledger() as (manifest, fingerprint, journal, guard):
            return drafts._claim_empty_recovery(self.book, self.plan["id"], manifest, fingerprint, journal, guard,
                1, candidate, comparison_policy="strict", body_encoding=PLATFORM,
                recovery_attempt_id=self.recovery_attempt, attributed=attributed)

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(story.StoryError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)

    def test_unattributed_allocation_without_remote_stays_ineligible(self):
        self.seed()
        self.assertIsNone(self.candidate(None))
        value = self.attributed()
        candidate = self.candidate(value)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate, self.candidate(value))
        self.assertEqual((candidate["remote_id"], candidate["attempt_id"]),
                         (self.remote, self.attempt))
        self.assertEqual(candidate["attribution_sha256"], value["sha256"])
        self.assertFalse(list(self.lock_root.glob("empty-recovery-*.json")))

    def test_attribution_is_bound_to_the_exact_sending_and_unknown_events(self):
        self.seed()
        for change in ({"sending_sha256": "f" * 64}, {"unknown_sha256": "f" * 64},
                       {"attempt_id": "c" * 32}, {"chapter": 2}):
            with self.subTest(change=change):
                self.assert_code("platform_draft_attribution_stale",
                                 self.attributed, **change)

    def test_an_identified_unknown_draft_is_never_renamed_by_attribution(self):
        self.seed(unknown_remote="identified")
        self.assert_code("platform_draft_attribution_stale", self.attributed, remote_id="another")
        self.assertEqual(self.candidate(None)["remote_id"], "identified")

    def test_evidence_must_be_private_complete_and_confirmed_after_the_failure(self):
        self.seed()
        with self.ledger() as (manifest, fingerprint, journal, _):
            original = self.evidence_value(journal, manifest, fingerprint)
            unknown_at = drafts._event_epoch(
                [item for item in journal.events if item["chapter"] == 1][-1]["at"])
        bad = [("book_id", "another-book"), ("plan_id", "c" * 32),
               ("manifest_sha256", "d" * 64), ("target_sha256", "e" * 64),
               ("attempt_id", "not-hex"), ("remote_id", "https://example.invalid/x"),
               ("sending_sha256", "f" * 63),
               ("authorized_by_user", {"text": "", "confirmed_at": time.time()}),
               ("authorized_by_user", {"text": "future", "confirmed_at": time.time() + 60})]
        for key, value in bad:
            with self.subTest(key=key, value=value):
                self.assert_code("platform_draft_attribution_invalid", self.attributed,
                                 **{key: value})
        self.assert_code("platform_draft_attribution_invalid", self.attributed, extra="field")
        self.assert_code("platform_draft_attribution_stale", self.attributed,
                         authorized_by_user={"text": "confirmed before the failure",
                                             "confirmed_at": unknown_at - 10})
        path = self.write_value(original, name="world-readable.json", mode=0o644)
        with self.ledger() as (manifest, fingerprint, journal, _):
            self.assert_code("platform_draft_attribution_invalid", drafts._attributed_empty_draft,
                             self.book, self.plan["id"], manifest, fingerprint, journal, str(path))
        with self.ledger() as (manifest, fingerprint, journal, _):
            self.assert_code("platform_draft_attribution_invalid", drafts._attributed_empty_draft,
                             self.book, self.plan["id"], manifest, fingerprint, journal, "relative.json")

    def test_claim_consumes_the_attributed_identity_once(self):
        self.seed()
        value = self.attributed()
        candidate = self.candidate(value)
        proof = self.claim(candidate, value)
        self.assertEqual(proof["remote_id"], self.remote)
        self.assertEqual(proof["attempt_id"], self.attempt)
        self.assertIsNone(self.candidate(value))
        self.assert_code("platform_draft_empty_recovery_changed", self.claim, candidate, value)
        claims = list(self.lock_root.glob("empty-recovery-*.claim.json"))
        self.assertEqual(len(claims), 1)
        record = json.loads(claims[0].read_text(encoding="utf-8"))
        self.assertEqual(record["candidate"], candidate)
        self.assertEqual(record["candidate"]["attribution_sha256"], value["sha256"])


class AttributedCatalogRecoveryTests(AttributionEvidence, unittest.TestCase):
    execute = batch.CatalogBatchTests.execute

    def setUp(self):
        batch.CatalogBatchTests.setUp(self)
        self.evidence_dir = self.make_evidence_dir()
        self.attempt, self.remote = "a" * 32, "leftover"

    def inspect(self, attribution_path=None):
        return drafts.inspect_plan(self.book, self.plan["id"], self.adapter,
                                   attribution_path=attribution_path)

    def seed_interrupted(self):
        self.adapter = batch.AllocatedCatalogAdapter(self.book)
        self.adapter.remote[self.remote] = {"chapter": None, "remote_id": self.remote,
                                            "state": "draft", "title": "", "body": ""}
        with drafts._checked_plan(self.book, self.plan["id"]) as (manifest, fingerprint, _):
            frozen = manifest["chapters"][0]
            scope = {"book_id": self.book.meta("id"), "plan_id": self.plan["id"],
                     "manifest_sha256": fingerprint}
        with drafts._target_lock(manifest) as guard, drafts._journal(
                self.book, self.plan["id"], manifest, fingerprint, create=True) as journal:
            guard.intent(1, self.attempt, frozen, body_encoding=PLATFORM, scope=scope)
            journal.append(1, "sending", attempt_id=self.attempt, remote_id=None, body_encoding=PLATFORM)
            journal.append(1, "unknown", attempt_id=self.attempt, remote_id=None,
                           body_encoding=PLATFORM, reason="platform_browser_operation_failed")
            value = self.evidence_value(journal, manifest, fingerprint)
        return self.write_value(value)

    def test_attributed_leftover_is_recovered_then_read_back_independently(self):
        evidence = self.seed_interrupted()
        checked = self.inspect(str(evidence))
        self.assertTrue(checked["can_execute"], checked)
        self.assertEqual([row["action"] for row in checked["changes"]], ["update"])
        self.assertEqual(checked["changes"][0]["reason"], "resume_empty_draft")
        self.assertEqual(checked["changes"][0]["remote_id"], self.remote)
        self.assertEqual(checked["attribution_sha256"],
                         drafts._hash(json.loads(evidence.read_text(encoding="utf-8"))))
        self.assertEqual([call for call in self.adapter.calls if call[0] == "inspect_empty"],
                         [("inspect_empty", self.remote, 1)])
        result = self.execute(sync_drafts=True, body_encoding=PLATFORM,
                              expected_remote=checked["_expected_remote"],
                              attribution_path=str(evidence))
        self.assertTrue(result["all_chapters_accepted_this_run"], result)
        self.assertEqual(result["results"][0]["remote_id"], self.remote)
        self.assertEqual(result["results"][0]["reason"], "empty_draft_recovered")
        self.assertEqual((self.adapter.resumed, self.adapter.created), (1, 0))
        self.assertEqual([call for call in self.adapter.calls if call[0] == "read"],
                         [("read", self.remote)])
        again = self.inspect()
        self.assertEqual(again["change_counts"]["unchanged"], 1)

    def test_without_attribution_the_leftover_remains_blocked(self):
        self.seed_interrupted()
        checked = self.inspect()
        self.assertFalse(checked["can_execute"])
        self.assertEqual(checked["changes"][0]["reason"], "platform_browser_unnumbered_draft")
        self.assertFalse(any(call[0] == "inspect_empty" for call in self.adapter.calls))
        self.assertEqual((self.adapter.resumed, self.adapter.created), (0, 0))

    def test_another_unowned_unnumbered_draft_blocks_even_with_attribution(self):
        evidence = self.seed_interrupted()
        self.adapter.remote["unowned"] = {"chapter": None, "remote_id": "unowned",
                                          "state": "draft", "title": "", "body": ""}
        checked = self.inspect(str(evidence))
        self.assertFalse(checked["can_execute"])
        self.assertEqual(checked["changes"][0]["reason"], "platform_browser_unnumbered_draft")
        self.assertEqual(self.adapter.resumed, 0)

    def test_attribution_never_targets_a_chapter_that_already_has_a_draft(self):
        evidence = self.seed_interrupted()
        self.adapter.remote["numbered"] = {"chapter": 1, "remote_id": "numbered",
                                           "state": "draft", "title": "旧标题", "body": "旧正文"}
        checked = self.inspect(str(evidence))
        self.assertFalse(checked["can_execute"])
        self.assertEqual(checked["changes"][0]["reason"], "platform_browser_ambiguous_draft")
        self.assertEqual(self.adapter.resumed, 0)

    def test_attribution_without_a_matching_unnumbered_draft_never_unlocks_a_write(self):
        evidence = self.seed_interrupted()
        self.adapter.remote.clear()
        checked = self.inspect(str(evidence))
        self.assertFalse(checked["can_execute"])
        self.assertEqual(checked["changes"][0]["reason"], "previous_attempt_not_found")
        self.assertIsNone(checked["attribution_sha256"])
        self.assertEqual(self.adapter.created, 0)

    def test_changed_or_missing_evidence_stops_before_any_write(self):
        evidence = self.seed_interrupted()
        checked = self.inspect(str(evidence))
        self.assertTrue(checked["can_execute"], checked)
        value = json.loads(evidence.read_text(encoding="utf-8"))
        value["authorized_by_user"]["text"] = "A later, different confirmation"
        self.write_value(value)
        with self.assertRaises(story.StoryError) as raised:
            self.execute(sync_drafts=True, body_encoding=PLATFORM,
                         expected_remote=checked["_expected_remote"], attribution_path=str(evidence))
        self.assertEqual(raised.exception.code, "platform_draft_inspection_changed")
        self.assertEqual((self.adapter.resumed, self.adapter.created), (0, 0))
        evidence.unlink()
        with self.assertRaises(story.StoryError) as raised:
            self.execute(sync_drafts=True, body_encoding=PLATFORM,
                         expected_remote=checked["_expected_remote"], attribution_path=str(evidence))
        self.assertEqual(raised.exception.code, "platform_draft_attribution_invalid")
        self.assertEqual((self.adapter.resumed, self.adapter.created), (0, 0))

    def test_sync_cannot_apply_attribution_that_its_inspection_did_not_use(self):
        evidence = self.seed_interrupted()
        checked = self.inspect(str(evidence))
        result = self.execute(sync_drafts=True, body_encoding=PLATFORM,
                              expected_remote=checked["_expected_remote"])
        self.assertEqual(result["results"][0]["reason"], "platform_draft_inspection_changed")
        self.assertEqual((self.adapter.resumed, self.adapter.created), (0, 0))


if __name__ == "__main__":
    unittest.main()
