"""Read-only inspection blocks unresolved writes and identifies safe old batches."""
from contextlib import contextmanager
import json
import threading
import unittest
import uuid
from unittest.mock import patch

from tests import test_platform_draft_auto_connect as auto
from tests import test_platform_draft_sync as sync


drafts = sync.drafts
ENCODING = drafts.PLATFORM_BODY_ENCODING


class InspectionRecoveryTests(unittest.TestCase):
    setUp = sync.ExistingDraftSyncTests.setUp
    execute = sync.ExistingDraftSyncTests.execute

    def inspect(self, plan=None):
        return drafts.inspect_plan(self.book, (plan or self.plan)["id"], self.adapter)

    def records(self):
        paths = list(self.global_root.glob("*.json"))
        paths += list((self.book.root / drafts.DIRECTORY).rglob("*.json"))
        return {path: path.read_bytes() for path in paths}

    def pending(self, plan=None, *, chapter=1, scope_changes=None, frozen=None, legacy=False):
        plan = plan or self.plan
        frozen = frozen or next(item for item in plan["manifest"]["chapters"] if item["chapter"] == chapter)
        scope = {"book_id": self.book.meta("id"), "plan_id": plan["id"],
                 "manifest_sha256": drafts._hash(plan["manifest"]), **(scope_changes or {})}
        with drafts._target_lock(self.plan["manifest"]) as guard:
            guard.intent(chapter, uuid.uuid4().hex, frozen,
                         body_encoding="verbatim" if legacy else ENCODING, scope=scope)

    def lost_write(self):
        self.adapter = sync.UpdateAdapter(self.book)
        self.adapter.fail_create = "after"
        result = self.execute(sync_drafts=True, body_encoding=ENCODING)
        self.assertEqual(result["results"][0]["state"], "unknown")
        self.adapter.fail_create = None

    def test_same_scope_matching_unknown_can_be_reconciled_by_confirmed_sync(self):
        self.lost_write()
        before = self.records()
        manuscript = self.fixture.writing_snapshot()
        result = self.inspect()
        self.assertTrue(result["can_execute"])
        self.assertEqual(result["changes"][0]["action"], "unchanged")
        self.assertIsNone(result["changes"][0]["reason"])
        self.assertEqual(result["recovery_plans"], [])
        self.assertEqual(self.records(), before, "Inspection cannot resolve or rewrite historical evidence")
        self.assertTrue(self.execute(sync_drafts=True, body_encoding=ENCODING,
                                    expected_remote=result["_expected_remote"])["all_chapters_accepted_this_run"])
        checked = self.inspect()
        self.assertTrue(checked["can_execute"])
        self.assertEqual(checked["changes"][0]["action"], "unchanged")
        self.assertEqual(checked["recovery_plans"], [])
        self.assertEqual((self.adapter.created, self.adapter.updated), (1, 0))
        self.assertEqual(self.fixture.writing_snapshot(), manuscript)

    def test_missing_remote_keeps_unknown_and_offers_only_its_original_plan(self):
        self.lost_write()
        self.adapter.hide_all = True
        before = self.records()
        result = self.inspect()
        self.assertFalse(result["can_execute"])
        self.assertEqual(result["changes"][0]["reason"], "previous_attempt_not_found")
        self.assertEqual(result["recovery_plans"][0]["plan_id"], self.plan["id"])
        self.assertEqual(self.records(), before)
        self.assertEqual(self.adapter.created, 1)

    def test_original_batches_and_pending_chapters_are_sorted_and_deduplicated(self):
        self.fixture.commit(2)
        old = self.fixture.prepare((1, 2))
        self.pending(old, chapter=2)
        self.pending(old, chapter=1)
        self.pending(old, chapter=2)
        self.pending(self.plan)
        before = self.records()
        result = self.inspect(old)
        routes = {item["plan_id"]: item for item in result["recovery_plans"]}
        self.assertEqual(len(routes), 2)
        self.assertEqual(routes[old["id"]]["chapters"], [1, 2])
        self.assertEqual(routes[old["id"]]["pending_chapters"], [1, 2])
        self.assertEqual(routes[self.plan["id"]]["chapters"], [1])
        self.assertEqual(routes[self.plan["id"]]["pending_chapters"], [1])
        self.assertEqual(result["recovery_plans"], sorted(result["recovery_plans"], key=lambda row: (row["pending_chapters"][0], row["plan_id"])))
        self.assertEqual(self.records(), before)

    def test_foreign_missing_or_wrong_fingerprint_scope_never_produces_a_recovery_link(self):
        for changes in ({"book_id": "another-book"}, {"plan_id": "0" * 32}, {"manifest_sha256": "0" * 64}):
            with self.subTest(changes=changes):
                self.pending(scope_changes=changes)
                result = self.inspect()
                self.assertFalse(result["can_execute"])
                self.assertEqual(result["recovery_plans"], [])

    def test_original_plan_target_and_content_must_match_the_pending_intent(self):
        wrong_target = self.fixture.prepare(account_id="another-account")
        self.pending(wrong_target)
        self.pending(frozen={**self.frozen, "body": "不属于所指清单的正文"})
        result = self.inspect()
        self.assertFalse(result["can_execute"])
        self.assertEqual(result["recovery_plans"], [])

    def test_original_plan_must_actually_contain_the_pending_chapter(self):
        self.fixture.commit(2)
        current = self.fixture.prepare((2,))
        frozen = current["manifest"]["chapters"][0]
        self.pending(chapter=2, frozen=frozen)
        result = self.inspect(current)
        self.assertFalse(result["can_execute"])
        self.assertEqual(result["recovery_plans"], [])

    def test_inconsistent_encoded_evidence_cannot_offer_a_recovery_link(self):
        self.pending()
        path = next(self.global_root.glob("*.intent.json"))
        intent = json.loads(path.read_text())
        intent["encoded_content_sha256"] = "0" * 64
        path.write_text(json.dumps(intent))
        before = self.records()
        result = self.inspect()
        self.assertFalse(result["can_execute"])
        self.assertEqual(result["recovery_plans"], [])
        self.assertEqual(self.records(), before)

    def test_legacy_intent_stays_blocked_without_inventing_an_original_scope(self):
        self.pending(legacy=True)
        self.adapter.seed(self.frozen, **drafts._encoded_content(self.frozen, ENCODING, "fanqie"))
        result = self.inspect()
        self.assertFalse(result["can_execute"])
        self.assertEqual(result["changes"][0]["reason"], "unresolved_previous_content")
        self.assertEqual(result["recovery_plans"], [])

    def test_cancelled_original_plan_is_available_for_read_only_reconciliation(self):
        self.lost_write()
        sync.story.publish.cancel(self.book, self.plan["id"])
        current = self.fixture.prepare()
        result = self.inspect(current)
        self.assertFalse(result["can_execute"])
        self.assertEqual(result["recovery_plans"][0]["plan_id"], self.plan["id"])
        self.assertTrue(self.execute(reconcile_only=True, body_encoding=ENCODING)["all_chapters_accepted_this_run"])
        self.assertTrue(self.inspect(current)["can_execute"])

    def test_multiple_remote_matches_stay_blocked_without_claiming_a_recovery_target(self):
        self.lost_write()
        self.adapter.seed(self.frozen, remote_id="duplicate",
                          **drafts._encoded_content(self.frozen, ENCODING, "fanqie"))
        before = self.records()
        checked = self.inspect()
        self.assertFalse(checked["can_execute"])
        self.assertEqual(checked["changes"][0]["reason"], "multiple_remote_drafts")
        self.assertEqual(checked["recovery_plans"], [])
        self.assertEqual(self.records(), before)
        self.adapter.remote.pop("duplicate")
        recovered = self.inspect()
        self.assertTrue(recovered["can_execute"])
        self.assertEqual(recovered["changes"][0]["action"], "unchanged")
        self.assertEqual(recovered["recovery_plans"], [])
        self.assertEqual(self.records(), before)

    def test_adapter_find_and_read_errors_keep_evidence_and_retry_can_recover_metadata(self):
        self.lost_write()
        before = self.records()
        for method in ("find_drafts", "read_draft"):
            with self.subTest(method=method), patch.object(self.adapter, method, side_effect=RuntimeError("isolated read failure")):
                checked = self.inspect()
                self.assertFalse(checked["can_execute"])
                self.assertEqual(checked["changes"][0]["action"], "blocked")
                self.assertEqual(checked["changes"][0]["reason"], "platform_draft_adapter_error")
                self.assertEqual(checked["recovery_plans"], [])
                self.assertEqual(self.records(), before)
        recovered = self.inspect()
        self.assertTrue(recovered["can_execute"])
        self.assertEqual(recovered["changes"][0]["action"], "unchanged")
        self.assertEqual(recovered["recovery_plans"], [])
        self.assertEqual(self.records(), before)
        self.assertEqual(self.adapter.created, 1)

    def test_real_other_book_with_same_platform_target_cannot_become_local_recovery(self):
        other = sync.base.fixtures.PublishTests()
        other.setUp()
        self.addCleanup(other.tearDown)
        other.commit()
        foreign_plan = other.prepare()
        self.assertNotEqual(other.book.meta("id"), self.book.meta("id"))
        self.assertEqual(drafts._target(foreign_plan["manifest"]), drafts._target(self.plan["manifest"]))
        foreign_adapter = sync.UpdateAdapter(other.book)
        foreign_adapter.fail_create = "after"
        failed = drafts.run_plan(other.book, foreign_plan["id"], foreign_adapter,
                                 execute=True, sync_drafts=True, body_encoding=ENCODING)
        self.assertEqual(failed["results"][0]["state"], "unknown")
        self.adapter.seed(self.frozen, **drafts._encoded_content(self.frozen, ENCODING, "fanqie"))
        before = self.records()
        checked = self.inspect()
        self.assertFalse(checked["can_execute"])
        self.assertEqual(checked["changes"][0]["reason"], "unresolved_previous_content")
        self.assertEqual(checked["recovery_plans"], [])
        self.assertEqual(self.records(), before)
        self.assertEqual(self.adapter.created, 0)

    def test_changed_formal_source_preserves_the_exact_old_frozen_recovery_scope(self):
        self.lost_write()
        self.fixture.commit(text=self.fixture.texts[1] + "她重新核对了门后的收据。\n", replace_last=True)
        current = self.fixture.prepare()
        self.assertNotEqual(current["id"], self.plan["id"])
        checked = self.inspect(current)
        self.assertFalse(checked["can_execute"])
        self.assertEqual(checked["recovery_plans"][0]["manifest_sha256"], drafts._hash(self.plan["manifest"]))
        reconciled = self.execute(reconcile_only=True, body_encoding=ENCODING)
        self.assertTrue(reconciled["all_chapters_accepted_this_run"])
        self.assertFalse(reconciled["source_matches_current"])
        checked = self.inspect(current)
        self.assertTrue(checked["can_execute"])
        self.assertEqual(checked["changes"][0]["action"], "update")
        self.assertEqual((self.adapter.created, self.adapter.updated), (1, 0))

    def test_recovery_lookup_releases_book_locks_and_does_not_hold_them_for_adapter_calls(self):
        self.lost_write()
        self.fixture.commit(2)
        current = self.fixture.prepare((1, 2))
        original_checked = drafts._checked_plan
        lookup_states = []

        @contextmanager
        def checked(book, plan_id, **options):
            with original_checked(book, plan_id, **options) as result:
                if plan_id == self.plan["id"]:
                    lookup_states.append((book.db.in_transaction, getattr(book, "_lock_depth", 0)))
                yield result

        with patch.object(drafts, "_checked_plan", checked):
            result = self.inspect(current)
        self.assertEqual(len(result["recovery_plans"]), 1)
        self.assertEqual(lookup_states, [(True, 1)])
        self.assertFalse(self.book.db.in_transaction)
        self.assertEqual(getattr(self.book, "_lock_depth", 0), 0)
        # FakeAdapter.call also asserts every remote operation is outside both locks.
        self.assertGreater(len(self.adapter.calls), 0)


class InspectionRecoveryJobTests(unittest.TestCase):
    def setUp(self):
        self.f = auto.PlatformDraftAutoConnectTests("runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def finish(self, mode, **extra):
        f = self.f
        options = {mode: True, "body_encoding": ENCODING}
        started = f.start(f.preview(**options), **options, **extra)
        return f.wait_job(started["id"])

    def test_mixed_new_batch_blocks_before_confirmation_then_original_reconcile_unblocks_it(self):
        f = self.f
        f.fixture.commit(2)
        original = f.plan
        first = self.finish("inspect_only")
        f.fail_create = True
        saved = self.finish("sync_drafts", inspection_id=first["id"])
        self.assertEqual(saved["result"]["results"][0]["state"], "unknown")
        f.fail_create = False
        f.plan = f.fixture.prepare((1, 2))
        current = f.plan
        second = current["manifest"]["chapters"][1]
        f.remote["existing-2"] = {"remote_id": "existing-2", "chapter": 2, "state": "draft",
                                   "title": second["title"], "body": "平台旧正文"}
        checked = self.finish("inspect_only")
        self.assertEqual(checked["state"], "inspection_blocked")
        self.assertEqual([row["action"] for row in checked["result"]["changes"]], ["blocked", "update"])
        self.assertFalse(checked["result"]["can_execute"])
        route = checked["result"]["recovery_plans"][0]
        self.assertEqual(route["plan_id"], original["id"])
        self.assertEqual(route["chapters"], [1])
        preview = f.preview(sync_drafts=True, body_encoding=ENCODING)
        f.assert_code("platform_draft_inspection_required", f.start, preview, sync_drafts=True,
                      body_encoding=ENCODING, inspection_id=checked["id"])
        self.assertEqual(sum(op[0] == "create" for op in f.operations), 1)
        self.assertFalse(any(op[0] == "update" for op in f.operations))
        f.plan = original
        self.assertTrue(self.finish("reconcile_only")["result"]["all_chapters_accepted_this_run"])
        f.plan = current
        checked = self.finish("inspect_only")
        self.assertEqual(checked["state"], "inspection_ready")
        self.assertEqual(checked["result"]["recovery_plans"], [])
        done = self.finish("sync_drafts", inspection_id=checked["id"])
        self.assertTrue(done["result"]["all_chapters_accepted_this_run"])
        self.assertEqual(sum(op[0] == "create" for op in f.operations), 1)
        self.assertEqual(sum(op[0] == "update" for op in f.operations), 1)

    def test_recovery_metadata_survives_real_worker_thread_finish_and_job_polling(self):
        f = self.f
        first = self.finish("inspect_only")
        f.fail_create = True
        self.assertEqual(self.finish("sync_drafts", inspection_id=first["id"])["state"], "unknown")
        f.fail_create = False
        original_plan = f.plan
        f.fixture.commit(2)
        f.plan = f.fixture.prepare((1, 2))
        self.assertNotEqual(f.plan["id"], original_plan["id"])
        main_thread = threading.get_ident()
        worker_threads = []
        original = auto.drafts._inspection_recovery_plans

        def recovery(*args):
            worker_threads.append(threading.get_ident())
            return original(*args)

        with patch.object(auto.drafts, "_inspection_recovery_plans", side_effect=recovery):
            checked = self.finish("inspect_only")
        self.assertEqual(len(worker_threads), 1)
        self.assertNotEqual(worker_threads[0], main_thread)
        self.assertEqual(checked["state"], "inspection_blocked")
        expected = checked["result"]["recovery_plans"]
        self.assertEqual(expected[0]["plan_id"], original_plan["id"])
        polled = auto.drafts.workbench_job(f.root, checked["id"], expected_book_id=f.book.meta("id"))
        self.assertEqual(polled["result"]["recovery_plans"], expected)
        catalog = auto.drafts.workbench_catalog(f.root, expected_book_id=f.book.meta("id"))
        recovered_job = next(job for job in catalog["jobs"] if job["id"] == checked["id"])
        self.assertEqual(recovered_job["result"]["recovery_plans"], expected)
        self.assertEqual(sum(op[0] == "create" for op in f.operations), 1)
        self.assertFalse(any(op[0] == "update" for op in f.operations))


if __name__ == "__main__":
    unittest.main()
