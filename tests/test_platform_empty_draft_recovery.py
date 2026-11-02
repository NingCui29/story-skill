"""An allocated empty draft may be claimed once, only by its original batch."""
from contextlib import contextmanager
import copy
import hashlib
import json
import unittest
from unittest.mock import patch

from tests.test_platform_drafts import drafts, fixtures, story


class EmptyDraftRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublishTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.commit()
        self.book = self.fixture.book
        self.plan = self.fixture.prepare()
        self.root = self.fixture.root.parent / "global-draft-state"
        self.root.mkdir(mode=0o700)
        lock_patch = patch.object(drafts, "_lock_directory", return_value=self.root)
        lock_patch.start()
        self.addCleanup(lock_patch.stop)
        self.original_attempt = "a" * 32
        self.recovery_attempt = "b" * 32
        self.remote_id = "allocated-empty-1"
        self.encoding = drafts.PLATFORM_BODY_ENCODING

    @contextmanager
    def ledger(self, plan=None):
        plan = plan or self.plan
        with drafts._checked_plan(self.book, plan["id"]) as (manifest, fingerprint, _):
            pass
        with drafts._target_lock(manifest) as guard, drafts._journal(
                self.book, plan["id"], manifest, fingerprint, create=True) as journal:
            yield (self.book, plan["id"], manifest, fingerprint, journal, guard)

    def seed(self, args, *, chapter=1, attempt=None, remote_id="default", sending_remote=None,
             scope=None, encoding=None, observed=None):
        _, plan_id, manifest, fingerprint, journal, guard = args
        attempt = attempt or self.original_attempt
        encoding = encoding or self.encoding
        remote_id = self.remote_id if remote_id == "default" else remote_id
        frozen = next(item for item in manifest["chapters"] if item["chapter"] == chapter)
        scope = scope or {"book_id": self.book.meta("id"), "plan_id": plan_id, "manifest_sha256": fingerprint}
        guard.intent(chapter, attempt, frozen, comparison_policy="strict", body_encoding=encoding, scope=scope)
        journal.append(chapter, "sending", attempt_id=attempt, remote_id=sending_remote, body_encoding=encoding)
        journal.append(chapter, "unknown", attempt_id=attempt, remote_id=remote_id,
                       observed=observed, body_encoding=encoding, reason="platform_browser_unsupported_editor")

    def candidate(self, args, chapter=1, **kwargs):
        return drafts._empty_recovery_candidate(*args, chapter, comparison_policy="strict",
                                                body_encoding=kwargs.get("body_encoding", self.encoding))

    def claim(self, args, candidate, chapter=1, recovery_attempt_id=None):
        return drafts._claim_empty_recovery(*args, chapter, candidate, comparison_policy="strict",
            body_encoding=self.encoding, recovery_attempt_id=recovery_attempt_id or self.recovery_attempt)

    def assert_changed(self, function, *args, **kwargs):
        with self.assertRaises(story.StoryError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, "platform_draft_empty_recovery_changed")

    def test_exact_allocated_intent_produces_content_free_stable_candidate(self):
        with self.ledger() as args:
            self.seed(args)
            candidate = self.candidate(args)
            self.assertEqual(candidate, self.candidate(args))
            self.assertEqual(candidate["attempt_id"], self.original_attempt)
            self.assertEqual(candidate["remote_id"], self.remote_id)
            self.assertEqual(candidate["plan_id"], self.plan["id"])
            frozen = args[2]["chapters"][0]
            encoded = drafts._encoded_content(frozen, self.encoding, args[2]["platform"])
            for name in ("title", "body"):
                self.assertEqual(candidate[name + "_sha256"],
                    hashlib.sha256(drafts._normal(encoded[name]).encode("utf-8")).hexdigest())
                self.assertNotIn(name, candidate)
                self.assertNotIn(frozen[name], json.dumps(candidate, ensure_ascii=False))
            self.assertNotEqual(candidate["intent_sha256"], candidate["sending_sha256"])
            self.assertFalse(list(self.root.glob("empty-recovery-*.json")))

    def test_no_allocated_remote_identity_is_ineligible(self):
        with self.ledger() as args:
            self.seed(args, remote_id=None)
            self.assertIsNone(self.candidate(args))

    def test_unknown_update_to_existing_draft_is_not_an_allocation(self):
        with self.ledger() as args:
            self.seed(args, sending_remote=self.remote_id)
            self.assertIsNone(self.candidate(args))

    def test_observed_unknown_even_empty_is_not_unobserved_allocation(self):
        with self.ledger() as args:
            self.seed(args, observed={"title": "", "body": ""})
            self.assertIsNone(self.candidate(args))

    def test_legacy_intent_without_frozen_scope_is_ineligible(self):
        with self.ledger() as args:
            self.seed(args, encoding="verbatim")
            self.assertIsNone(self.candidate(args, body_encoding="verbatim"))

    def test_another_book_plan_or_manifest_cannot_adopt_the_allocation(self):
        for name, value in (("book_id", "another-book"), ("plan_id", "c" * 32),
                            ("manifest_sha256", "d" * 64)):
            with self.subTest(name=name), self.ledger() as args:
                scope = {"book_id": self.book.meta("id"), "plan_id": args[1], "manifest_sha256": args[3]}
                scope[name] = value
                attempt = {"book_id": "1", "plan_id": "2", "manifest_sha256": "3"}[name] * 32
                self.seed(args, attempt=attempt, scope=scope)
                self.assertIsNone(self.candidate(args))
                # Each subcase gets its own unresolved-intent-free target.
                for path in self.root.glob(args[5].target_hash + "-1-*.json"):
                    path.unlink()

    def test_changed_encoding_and_multiple_unresolved_intents_are_ineligible(self):
        with self.ledger() as args:
            self.seed(args)
            self.assertIsNone(self.candidate(args, body_encoding="verbatim"))
            self.seed(args, attempt="c" * 32, remote_id="another-allocation")
            self.assertIsNone(self.candidate(args))

    def test_same_remote_id_for_two_chapters_is_ambiguous(self):
        self.fixture.commit(2)
        plan = self.fixture.prepare((1, 2))
        with self.ledger(plan) as args:
            self.seed(args)
            self.seed(args, chapter=2, attempt="c" * 32)
            self.assertIsNone(self.candidate(args, 1))
            self.assertIsNone(self.candidate(args, 2))

    def test_resolved_attempt_is_not_eligible(self):
        with self.ledger() as args:
            self.seed(args)
            frozen = args[2]["chapters"][0]
            observed = {**drafts._encoded_content(frozen, self.encoding, args[2]["platform"]),
                        "remote_id": self.remote_id}
            scope = {"book_id": self.book.meta("id"), "plan_id": args[1], "manifest_sha256": args[3]}
            self.assertTrue(args[5].resolve(1, observed, frozen=frozen, body_encoding=self.encoding, scope=scope))
            self.assertIsNone(self.candidate(args))

    def test_claim_is_durable_one_use_and_preserves_original_history(self):
        before_writing = self.fixture.writing_snapshot()
        with self.ledger() as args:
            self.seed(args)
            candidate = self.candidate(args)
            before_events = copy.deepcopy(args[4].events)
            proof = self.claim(args, candidate)
            self.assertEqual(set(proof), {"schema", "attempt_id", "remote_id", "chapter", "platform",
                                         "account_id", "remote_book_id", "title_sha256", "body_sha256"})
            self.assertEqual(proof["attempt_id"], self.original_attempt)
            self.assertEqual(proof["remote_id"], self.remote_id)
            self.assertEqual(args[4].events, before_events)
            self.assertIsNone(self.candidate(args))
            self.assert_changed(self.claim, args, candidate)
        with self.ledger() as args:
            self.assertEqual(args[4].events, before_events)
            self.assertIsNone(self.candidate(args))
            self.assert_changed(self.claim, args, candidate)
            intents, resolved = args[5].attempts(1)
            self.assertEqual(set(intents), {self.original_attempt})
            self.assertEqual(resolved, {})
        claims = list(self.root.glob("empty-recovery-*.claim.json"))
        self.assertEqual(len(claims), 1)
        record = json.loads(claims[0].read_text(encoding="utf-8"))
        self.assertEqual(record["recovery_attempt_id"], self.recovery_attempt)
        self.assertEqual(record["candidate"], candidate)
        self.assertEqual(self.fixture.writing_snapshot(), before_writing)

    def test_mutated_candidate_and_reused_attempt_id_are_rejected(self):
        with self.ledger() as args:
            self.seed(args)
            candidate = self.candidate(args)
            for name, value in (("remote_id", "another-id"), ("body_sha256", "f" * 64),
                                ("plan_id", "e" * 32), ("chapter", 2)):
                with self.subTest(name=name):
                    self.assert_changed(self.claim, args, {**candidate, name: value})
            self.assert_changed(self.claim, args, candidate, recovery_attempt_id=self.original_attempt)
            self.assertFalse(list(self.root.glob("empty-recovery-*.json")))

    def test_claim_rereads_disk_journal_and_rejects_stale_in_memory_candidate(self):
        with self.ledger() as args:
            self.seed(args)
            candidate = self.candidate(args)
            journal = args[4]
            later = drafts._Journal(self.book, args[1], args[2], args[3], journal.directory)
            later.append(1, "unknown", attempt_id=self.original_attempt, remote_id=self.remote_id,
                         body_encoding=self.encoding, reason="platform_browser_remote_changed")
            self.assertEqual(len(journal.events), 2)
            self.assert_changed(self.claim, args, candidate)
            self.assertFalse(list(self.root.glob("empty-recovery-*.json")))

    def test_no_write_blocked_attempt_does_not_hide_original_allocation(self):
        with self.ledger() as args:
            self.seed(args)
            candidate = self.candidate(args)
            args[4].append(1, "blocked", attempt_id="c" * 32, body_encoding=self.encoding,
                           reason="platform_browser_read_timeout")
            self.assertEqual(self.candidate(args), candidate)
            proof = self.claim(args, candidate)
            self.assertEqual(proof["attempt_id"], self.original_attempt)
            self.assertEqual(proof["remote_id"], self.remote_id)
            self.assertEqual([event["event"] for event in args[4].events], ["sending", "unknown", "blocked"])
            self.assertIsNone(self.candidate(args))

    def test_readonly_empty_check_failure_can_reinspect_same_allocation(self):
        with self.ledger() as args:
            self.seed(args)
            candidate = self.candidate(args)
            args[4].append(1, "blocked", attempt_id="c" * 32, remote_id=self.remote_id,
                           body_encoding=self.encoding, reason="platform_browser_read_timeout")
            self.assertEqual(self.candidate(args), candidate)
            self.assertEqual(self.claim(args, candidate)["remote_id"], self.remote_id)
            self.assertIsNone(self.candidate(args))

    def test_later_blocked_with_another_remote_identity_cannot_be_ignored(self):
        with self.ledger() as args:
            self.seed(args)
            args[4].append(1, "blocked", attempt_id="c" * 32, remote_id="different-allocation",
                           body_encoding=self.encoding, reason="platform_browser_read_timeout")
            self.assertIsNone(self.candidate(args))

    def test_later_blocked_with_remote_observation_cannot_be_ignored(self):
        with self.ledger() as args:
            self.seed(args)
            args[4].append(1, "blocked", attempt_id="c" * 32, remote_id=self.remote_id,
                           observed={"title": "", "body": ""},
                           body_encoding=self.encoding, reason="platform_browser_read_timeout")
            self.assertIsNone(self.candidate(args))

    def test_later_sending_even_without_target_intent_cannot_be_ignored(self):
        with self.ledger() as args:
            self.seed(args)
            args[4].append(1, "sending", attempt_id="c" * 32, body_encoding=self.encoding)
            self.assertIsNone(self.candidate(args))

    def test_later_blocked_with_target_intent_cannot_be_ignored(self):
        with self.ledger() as args:
            self.seed(args)
            scope = {"book_id": self.book.meta("id"), "plan_id": args[1], "manifest_sha256": args[3]}
            args[5].intent(1, "c" * 32, args[2]["chapters"][0], body_encoding=self.encoding, scope=scope)
            args[4].append(1, "blocked", attempt_id="c" * 32, body_encoding=self.encoding,
                           reason="platform_browser_read_timeout")
            self.assertIsNone(self.candidate(args))

    def test_crash_after_claim_publication_never_allows_replay(self):
        with self.ledger() as args:
            self.seed(args)
            candidate = self.candidate(args)
            original_publish = drafts._atomic_publish

            def fail_after_publish(source, target):
                original_publish(source, target)
                raise OSError("simulated crash after durable claim publication")

            with patch.object(drafts, "_atomic_publish", fail_after_publish):
                with self.assertRaises(story.StoryError) as raised:
                    self.claim(args, candidate)
            self.assertEqual(raised.exception.code, "platform_draft_journal_write_failed")
            self.assertIsNone(self.candidate(args))
            self.assert_changed(self.claim, args, candidate)
            self.assertEqual(len(args[4].events), 2)

    def test_full_readback_resolves_original_and_recovery_intents(self):
        with self.ledger() as args:
            self.seed(args)
            before_events = copy.deepcopy(args[4].events)
            self.claim(args, self.candidate(args))
            frozen = args[2]["chapters"][0]
            scope = {"book_id": self.book.meta("id"), "plan_id": args[1], "manifest_sha256": args[3]}
            args[5].intent(1, self.recovery_attempt, frozen, body_encoding=self.encoding, scope=scope)
            observed = {**drafts._encoded_content(frozen, self.encoding, args[2]["platform"]),
                        "remote_id": self.remote_id}
            self.assertTrue(args[5].resolve(1, observed, frozen=frozen, body_encoding=self.encoding, scope=scope))
            intents, resolved = args[5].attempts(1)
            self.assertEqual(set(intents), {self.original_attempt, self.recovery_attempt})
            self.assertEqual(set(resolved), set(intents))
            self.assertEqual(args[4].events, before_events)


if __name__ == "__main__":
    unittest.main()
