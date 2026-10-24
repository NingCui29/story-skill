"""Explicit deleted-test recovery preserves every original fence and journal."""
import copy
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import subprocess
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests import test_platform_drafts as base


class DeletedDraftRetirementTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(subprocess, "Popen", side_effect=AssertionError("retirement tests cannot start processes"))
        guard.start(); self.addCleanup(guard.stop)
        self.case = base.PlatformDraftTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.drafts, self.book = base.drafts, self.case.book
        self.manifest = self.case.plan["manifest"]
        self.plan_id = self.case.plan["id"]
        self.chapter = self.case.frozen["chapter"]
        self.proof_dir = self.case.fixture.root.parent / "retirement-proofs"
        self.proof_dir.mkdir(mode=0o700)

    def proof(self, name, value):
        path = self.proof_dir / (name + ".json")
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        path.chmod(0o600)
        return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def packet(self, resolved=False):
        d = self.drafts
        old = {"title": "已删除测试草稿", "body": "旧测试正文\n保留结尾空行\n"}
        attempt_id, remote_id = "a" * 32, "deleted-test-remote"
        with d._target_lock(self.manifest) as state:
            state.intent(self.chapter, attempt_id, old)
            if resolved:
                self.assertTrue(state.resolve(self.chapter, {**old, "remote_id": remote_id}))
            state.attempts(self.chapter)
            original = copy.deepcopy(state.all_intents[attempt_id])
            old_resolved = copy.deepcopy(state.all_resolved.get(attempt_id))
        result = self.case.execute()
        self.assertEqual(result["results"][0]["reason"], "previous_attempt_not_found")
        with d._checked_plan(self.book, self.plan_id) as (manifest, fingerprint, _):
            with d._journal(self.book, self.plan_id, manifest, fingerprint) as journal:
                event = copy.deepcopy(journal.events[-1])
        target = d._target(manifest)
        binding = {"book_id": self.book.meta("id"), "plan_id": self.plan_id,
                   "manifest_sha256": fingerprint, "target": target}
        request = {"schema": 1, "id": "b" * 32, "session_id": "c" * 32, "nonce": "nonce",
                   "binding": binding, "binding_sha256": d._hash(binding), "operation": "find_drafts",
                   "params": {"chapter": self.chapter}}
        response = {key: request[key] for key in ("id", "session_id", "nonce", "binding_sha256")}
        response.update(schema=1, ok=True, result=[])
        authorization = {"schema": 1, "source": "direct user request", "verbatim": "删除测试草稿，继续保存正式稿",
            "platform": target["platform"], "authorized_formal_chapters": [self.chapter],
            "deleted_test_drafts": [{"chapter": self.chapter, "remote_id": remote_id, "title": old["title"]}]}
        proofs = {"authorization": self.proof("authorization", authorization),
            "backup": self.proof("backup", [{**old, "remote_id": remote_id}]),
            "deletion_catalog": self.proof("deletion-catalog", {"operator_reviewed_empty": True}),
            "current_catalog": self.proof("current-catalog", {"operator_reviewed_empty": True}),
            "absence_request": self.proof("absence-request", request),
            "absence_response": self.proof("absence-response", response),
            "closed_job": self.proof("closed-job", {**target, "book_id": self.book.meta("id"),
                "plan_id": self.plan_id, "done": True, "stage": "finished", "state": "unknown"})}
        return {"schema": 1, "kind": "user_authorized_deleted_drafts_retirement", "book_id": self.book.meta("id"),
            "plan_id": self.plan_id, "manifest_sha256": fingerprint, "target": target,
            "acknowledgements": {"deletion_confirmed": True, "complete_catalog_empty": True,
                                 "all_related_workers_stopped": True}, "proofs": proofs,
            "attempts": [{"chapter": self.chapter, "attempt_id": attempt_id,
                "content_sha256": original["content_sha256"], "intent_sha256": d._hash(original),
                "resolved_sha256": d._hash(old_resolved) if old_resolved else None, "remote_id": remote_id,
                "backup_index": 0, "backup_content_sha256": d._hash(d._content(old)),
                "content_difference_acknowledged": False,
                "derived_unknowns": [{"sequence": event["sequence"], "attempt_id": event["attempt_id"],
                                      "event_sha256": d._hash(event)}]}]}

    def retire(self, packet, execute=True):
        return self.drafts.retire_deleted_attempts(self.book, packet, execute=execute,
            acknowledgement="user_authorized_deleted_drafts_only" if execute else None)

    def assert_invalid(self, packet):
        self.case.assert_code("platform_draft_retirement_invalid", self.retire, packet)

    def originals(self):
        paths = list(self.case.global_root.glob("*.json"))
        journal = self.case.fixture.root / self.drafts.DIRECTORY / self.plan_id
        if journal.exists():
            paths.extend(journal.glob("event-*.json"))
        return {path: path.read_bytes() for path in paths if not path.name.endswith(".retired.json")}

    def bridge_session(self, session_id, *, lease=True, active=False):
        bridge = base.story.platform_chrome_bridge
        binding = {"target": self.drafts._target(self.manifest)}
        session = {"schema": 1, "session_id": session_id, "binding": binding,
                   "binding_sha256": bridge._digest(binding), "token_sha256": "a" * 64,
                   "ttl_seconds": 120}
        with bridge._directory(self.book.root, session_id, create=True) as directory, bridge._lock(directory):
            bridge._write(directory, "session.json", session)
            if lease:
                bridge._write(directory, "lease.json", {"schema": 1, "session_id": session_id,
                    "expires": time.time() + (120 if active else -120), "detached": not active})
        return directory.path

    def test_initialization_residue_does_not_block_precise_retirement_or_change_evidence(self):
        packet = self.packet()
        bridge = base.story.platform_chrome_bridge
        self.bridge_session("1" * 32, lease=False)
        with bridge._directory(self.book.root, "2" * 32, create=True) as directory, bridge._lock(directory):
            bridge._write(directory, ".chrome-stage-" + "3" * 32, {"unpublished": True})
        evidence = {path: path.read_bytes() for path in (self.book.root / bridge.DIRECTORY).rglob("*") if path.is_file()}
        before = self.originals()
        self.assertTrue(self.retire(packet)["executed"])
        self.assertEqual(self.originals(), before)
        self.assertEqual({path: path.read_bytes() for path in evidence}, evidence)
        self.assertEqual(self.case.adapter.created, 0)
        self.assertTrue(self.case.execute()["ok"])
        self.assertEqual(self.case.adapter.created, 1)

    def test_inactive_session_history_is_not_a_live_session_limit_for_retirement(self):
        packet = self.packet()
        bridge = base.story.platform_chrome_bridge
        for session_id in ("1" * 32, "2" * 32, "3" * 32):
            self.bridge_session(session_id)
        with patch.object(bridge, "MAX_SESSIONS", 2):
            self.assertTrue(self.retire(packet)["executed"])
        self.assertTrue(self.case.execute()["ok"])
        self.assertEqual(self.case.adapter.created, 1)

    def test_real_live_lease_and_unknown_create_still_block_retirement_among_residue(self):
        packet = self.packet()
        bridge = base.story.platform_chrome_bridge
        self.bridge_session("1" * 32, lease=False)
        self.bridge_session("2" * 32, active=True)
        self.assert_invalid(packet)
        with bridge._directory(self.book.root, "2" * 32) as directory, bridge._lock(directory):
            bridge._write(directory, "lease.json", {"schema": 1, "session_id": "2" * 32,
                "expires": time.time() - 1, "detached": True}, replace=True)
            bridge._write(directory, "claimed-" + "4" * 32 + ".json", {"operation": "create_draft"})
        before = self.originals()
        self.assert_invalid(packet)
        self.assertEqual(self.originals(), before)
        self.assertEqual(self.case.adapter.created, 0)

    def test_dry_run_and_explicit_acknowledgement_do_not_change_any_history(self):
        packet = self.packet(); before = self.originals()
        result = self.retire(packet, execute=False)
        self.assertFalse(result["executed"])
        self.case.assert_code("platform_draft_retirement_invalid", self.drafts.retire_deleted_attempts,
                              self.book, packet, execute=True)
        self.assertEqual(list(self.case.global_root.glob("*.retired.json")), [])
        self.assertEqual(self.originals(), before)

    def test_precise_retirement_allows_current_formal_content_and_preserves_old_bytes(self):
        packet = self.packet(); before = self.originals()
        retired = self.retire(packet)
        self.assertFalse(retired["platform_success_claimed"])
        self.assertFalse(retired["remote_accessed"])
        self.assertEqual(self.originals(), before)
        self.assertEqual(self.case.adapter.created, 0)
        self.assertTrue(self.case.execute()["ok"])
        self.assertEqual(self.case.adapter.created, 1)
        for path, value in before.items():
            self.assertEqual(path.read_bytes(), value)

    def test_wrong_target_content_remote_resolved_proof_or_derived_event_is_rejected(self):
        packet = self.packet(resolved=True)
        changes = [lambda value: value["target"].update(remote_book_id="other"),
            lambda value: value["attempts"][0].update(content_sha256="0" * 64),
            lambda value: value["attempts"][0].update(intent_sha256="0" * 64),
            lambda value: value["attempts"][0].update(remote_id="other-remote"),
            lambda value: value["attempts"][0].update(resolved_sha256=None),
            lambda value: value["proofs"]["current_catalog"].update(sha256="0" * 64),
            lambda value: value["attempts"][0]["derived_unknowns"][0].update(event_sha256="0" * 64),
            lambda value: value["acknowledgements"].update(all_related_workers_stopped=False)]
        for change in changes:
            bad = copy.deepcopy(packet); change(bad)
            with self.subTest(change=change):
                self.assert_invalid(bad)
        self.assertEqual(list(self.case.global_root.glob("*.retired.json")), [])

    def test_resolved_remote_is_retired_without_becoming_a_platform_success(self):
        packet = self.packet(resolved=True); before = self.originals()
        self.retire(packet)
        self.assertEqual(self.originals(), before)
        self.assertTrue(self.case.execute()["ok"])
        self.assertEqual(self.case.adapter.created, 1)

    def test_other_unknown_or_sending_and_new_intents_still_block_creation(self):
        packet = self.packet(); self.retire(packet)
        d = self.drafts
        with d._checked_plan(self.book, self.plan_id) as (manifest, fingerprint, _):
            with d._journal(self.book, self.plan_id, manifest, fingerprint) as journal:
                journal.append(self.chapter, "unknown", reason="platform_chrome_request_timeout")
        self.assertFalse(self.case.execute()["ok"])
        self.assertEqual(self.case.adapter.created, 0)
        with d._target_lock(self.manifest) as state:
            state.intent(self.chapter, "d" * 32, self.case.frozen)
            active, _ = state.attempts(self.chapter)
            self.assertIn("d" * 32, active)
            self.assertNotIn("a" * 32, active)

    def test_a_real_sending_event_cannot_be_listed_as_a_derived_unknown(self):
        packet = self.packet(); d = self.drafts
        with d._checked_plan(self.book, self.plan_id) as (manifest, fingerprint, _):
            with d._journal(self.book, self.plan_id, manifest, fingerprint) as journal:
                journal.append(self.chapter, "sending", attempt_id=packet["attempts"][0]["derived_unknowns"][0]["attempt_id"])
        self.assert_invalid(packet)

    def test_changed_backup_body_requires_separate_hash_acknowledgement_and_evidence(self):
        packet = self.packet(); attempt = packet["attempts"][0]
        backup = json.loads(Path(packet["proofs"]["backup"]["path"]).read_text())
        backup[0]["body"] += "\n人工测试清理"
        packet["proofs"]["backup"] = self.proof("backup", backup)
        attempt["backup_content_sha256"] = self.drafts._hash(self.drafts._content(backup[0]))
        self.assert_invalid(packet)
        attempt["content_difference_acknowledged"] = True
        self.assert_invalid(packet)
        packet["proofs"]["prior_attempt"] = self.proof("prior-attempt", {"original_content_sha256": attempt["content_sha256"]})
        packet["proofs"]["manual_cleanup"] = self.proof("manual-cleanup", {"scope": "manual test cleanup only"})
        self.retire(packet)
        record = json.loads(next(self.case.global_root.glob("*.retired.json")).read_text())
        self.assertNotEqual(record["content_sha256"], record["backup_content_sha256"])
        self.assertFalse(self.drafts.status(self.book, self.plan_id)["all_chapters_verified_this_run"])

    def test_raw_preview_backup_parses_only_the_exact_chapter_prefix_and_keeps_body_newlines(self):
        packet = self.packet()
        backup = json.loads(Path(packet["proofs"]["backup"]["path"]).read_text())
        original_body = backup[0]["body"]
        backup[0]["title"] = f"第{self.chapter}章 " + backup[0]["title"]
        backup[0]["body"] = {"ok": True, "value": original_body}
        packet["proofs"]["backup"] = self.proof("backup", backup)
        self.assertTrue(self.retire(packet, execute=False)["ok"])
        backup[0]["body"]["value"] = original_body.rstrip("\n")
        packet["proofs"]["backup"] = self.proof("backup", backup)
        self.assert_invalid(packet)

    def test_repeating_the_identical_explicit_recovery_is_idempotent(self):
        packet = self.packet(); self.retire(packet)
        path = next(self.case.global_root.glob("*.retired.json")); before = path.read_bytes()
        self.assertEqual(self.retire(packet)["already_retired"], 1)
        self.assertEqual(path.read_bytes(), before)

    def test_all_records_validate_before_the_first_append(self):
        packet = self.packet()
        packet["attempts"].append(copy.deepcopy(packet["attempts"][0]))
        self.assert_invalid(packet)
        self.assertEqual(list(self.case.global_root.glob("*.retired.json")), [])

    def test_live_connection_or_unanswered_create_cannot_retire_even_with_operator_ack(self):
        packet = self.packet(); sid = "f" * 32
        folder = self.case.fixture.root / ".story/fake-bridge" / sid
        folder.mkdir(parents=True)
        (folder / ("claimed-" + "e" * 32 + ".json")).write_text("{}")
        unavailable = base.story.StoryError("platform_chrome_worker_unavailable", "closed")
        bridge = SimpleNamespace(DIRECTORY=".story/fake-bridge", MAX_SESSIONS=100,
            _attachment_lock=lambda root: nullcontext(), _uninitialized_session=lambda directory: False,
            _directory=lambda root, name: base.story._pinned_directory(folder),
            _lock=lambda directory: nullcontext(), _session=lambda directory: {"binding": {"target": packet["target"]}},
            _lease=lambda directory, session: {"active": True},
            _health_record=lambda directory, session: {"worker": {"pid": 111}},
            _read=lambda directory, name: {"operation": "create_draft"}, _owner_alive=lambda root, request: True)
        with patch.object(self.drafts.api, "platform_chrome_bridge", bridge):
            self.assert_invalid(packet)
            bridge._lease = lambda directory, session: (_ for _ in ()).throw(unavailable)
            self.assert_invalid(packet)
            self.assertEqual(list(self.case.global_root.glob("*.retired.json")), [])
            bridge._owner_alive = lambda root, request: False
            self.assertTrue(self.retire(packet)["executed"])

    def test_boolean_schema_and_unlisted_schema_fields_are_rejected(self):
        packet = self.packet()
        for change in (lambda value: value.update(schema=True), lambda value: value.update(override_unknown=True)):
            bad = copy.deepcopy(packet); change(bad); self.assert_invalid(bad)

    def test_prewrite_blocked_events_alone_never_require_retirement(self):
        self.case.adapter.fail_find = True
        for _ in range(3):
            self.assertEqual(self.case.execute()["results"][0]["state"], "blocked")
        self.case.adapter.fail_find = False
        self.assertTrue(self.case.execute()["ok"])
        self.assertEqual(self.case.adapter.created, 1)


if __name__ == "__main__":
    unittest.main()
