"""An explicit Fanqie LF policy preserves exact evidence and write fences."""
import copy
import json
import subprocess
import unittest
from unittest.mock import patch

from tests import test_platform_drafts as base
from tests import test_workbench_platform_drafts as workbench


POLICY = "fanqie_terminal_lf_v1"
ENCODING = "markdown_paragraphs_v1"
ENCODING_V2 = "markdown_paragraphs_v2"


class TerminalLFPolicyTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(subprocess, "Popen", side_effect=AssertionError("No process is permitted in policy tests"))
        guard.start(); self.addCleanup(guard.stop)
        self.case = base.PlatformDraftTests(); self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.d, self.frozen = base.drafts, self.case.frozen
        self.assertTrue(self.frozen["body"].endswith("\n"))

    def seed(self, body=None, **kwargs):
        self.case.adapter.seed(self.frozen, body=self.frozen["body"].rstrip("\n") if body is None else body, **kwargs)

    def execute(self, **kwargs):
        return self.case.execute(comparison_policy=POLICY, **kwargs)

    def journal(self):
        with self.d._checked_plan(self.case.book, self.case.plan["id"]) as (manifest, fingerprint, _):
            with self.d._journal(self.case.book, self.case.plan["id"], manifest, fingerprint) as journal:
                return copy.deepcopy(journal.events)

    def test_default_stays_strict_and_explicit_policy_preserves_real_hashes(self):
        self.seed()
        strict = self.case.execute()
        self.assertEqual(strict["results"][0]["state"], "conflict")
        before = self.journal()
        result = self.execute(reconcile_only=True)
        row, event = result["results"][0], self.journal()[-1]
        self.assertTrue(result["ok"])
        self.assertEqual(row["state"], "reconciled_equivalent")
        self.assertFalse(row["title_body_verified"])
        self.assertFalse(row["body_matches"])
        self.assertTrue(row["title_matches"])
        self.assertTrue(row["platform_equivalent"])
        self.assertTrue(row["accepted_this_run"])
        self.assertFalse(result["all_chapters_verified_this_run"])
        self.assertTrue(result["all_chapters_accepted_this_run"])
        self.assertEqual((result["exact_verified_chapters"], result["equivalent_chapters"], result["accepted_chapters"]), (0, 1, 1))
        self.assertEqual(event["schema"], 2)
        self.assertEqual(event["content_sha256"], self.d._hash(self.d._content(self.frozen)))
        self.assertEqual(event["observed_sha256"], self.d._hash(self.d._content(self.case.adapter.remote["existing"])))
        self.assertNotEqual(event["content_sha256"], event["observed_sha256"])
        self.assertEqual(self.journal()[:-1], before)
        self.assertEqual(self.case.adapter.created, 0)
        historical = self.d.status(self.case.book, self.case.plan["id"])
        self.assertFalse(historical["all_chapters_accepted_this_run"])
        self.assertFalse(historical["results"][0]["accepted_this_run"])
        self.assertTrue(historical["results"][0]["platform_equivalent"])

    def test_only_terminal_lf_changes_are_allowed_including_crlf(self):
        for suffix in ("", "\n\n\n", "\r\n\r\n"):
            with self.subTest(suffix=repr(suffix)):
                observed = {**self.frozen, "body": self.frozen["body"].rstrip("\n").replace("\n", "\r\n") + suffix}
                self.assertEqual(self.d._comparison(self.frozen, observed, POLICY, "fanqie"), (False, True))
        body = self.frozen["body"]
        changes = ["\n" + body, body.replace("\n", "\n\n", 1), body.rstrip("\n") + " \n",
                   body.rstrip("\n") + "\u3000\n", body.replace("。", "！", 1)]
        for changed in changes:
            with self.subTest(changed=changed[-12:]):
                self.assertEqual(self.d._comparison(self.frozen, {**self.frozen, "body": changed}, POLICY, "fanqie"), (False, False))
        self.assertEqual(self.d._comparison(self.frozen, {**self.frozen, "title": self.frozen["title"] + " "}, POLICY, "fanqie"), (False, False))

    def test_exact_observation_keeps_schema1_and_exact_success_meaning(self):
        self.seed(body=self.frozen["body"])
        result = self.case.execute()
        self.assertTrue(result["all_chapters_verified_this_run"])
        self.assertTrue(result["all_chapters_accepted_this_run"])
        self.assertEqual(result["equivalent_chapters"], 0)
        self.assertEqual(self.journal()[-1]["schema"], 1)
        self.assertEqual(self.journal()[-1]["event"], "reconciled")
        self.assertNotIn("comparison_policy", self.journal()[-1])
        selected = self.execute()
        self.assertTrue(selected["all_chapters_verified_this_run"])
        self.assertEqual(self.journal()[-1]["schema"], 2)
        self.assertEqual(self.journal()[-1]["event"], "reconciled")
        self.assertTrue(self.journal()[-1]["body_matches"])

    def test_invalid_policy_or_other_platform_fails_before_remote_access(self):
        for value in (None, True, "trim", []):
            with self.subTest(value=value):
                self.case.assert_code("platform_draft_invalid_comparison_policy", self.case.execute, comparison_policy=value)
        other = self.case.fixture.prepare(platform="qimao")
        self.case.assert_code("platform_draft_invalid_comparison_policy", self.case.execute, plan=other, comparison_policy=POLICY)
        self.assertEqual(self.case.adapter.calls, [])

    def test_lost_create_response_reconciles_same_frozen_intent_without_recreate(self):
        self.case.adapter.fail_create = "after"
        first = self.case.execute()
        self.assertEqual(first["results"][0]["state"], "unknown")
        original_intent = next(self.case.global_root.glob("*.intent.json"))
        original_bytes = original_intent.read_bytes()
        for item in self.case.adapter.remote.values():
            item["body"] = item["body"].rstrip("\n")
        result = self.execute(reconcile_only=True)
        self.assertTrue(result["all_chapters_accepted_this_run"])
        self.assertEqual(self.case.adapter.created, 1)
        self.assertEqual(original_intent.read_bytes(), original_bytes)
        settlement = json.loads(next(self.case.global_root.glob("*.resolved.json")).read_text())
        intent = json.loads(original_bytes)
        self.assertEqual(settlement["schema"], 2)
        self.assertEqual(settlement["content_sha256"], intent["content_sha256"])
        self.assertNotEqual(settlement["observed_sha256"], intent["content_sha256"])
        self.assertFalse(settlement["body_matches"])
        self.assertEqual(settlement["book_id"], self.case.book.meta("id"))
        self.assertEqual(settlement["plan_id"], self.case.plan["id"])
        self.assertTrue(self.execute(reconcile_only=True)["all_chapters_accepted_this_run"])

    def test_equivalent_create_keeps_raw_readback_and_missing_remote_never_replays(self):
        self.case.adapter.on_create = lambda: None
        original = self.case.adapter.create_draft
        def create(chapter, title, body):
            remote_id = original(chapter, title, body)
            self.case.adapter.remote[remote_id]["body"] = body.rstrip("\n")
            return remote_id
        with patch.object(self.case.adapter, "create_draft", side_effect=create):
            result = self.execute()
        self.assertEqual(result["results"][0]["state"], "saved_equivalent")
        self.assertFalse(self.journal()[-1]["body_matches"])
        self.case.adapter.hide_all = True
        missing = self.execute()
        self.assertEqual(missing["results"][0]["state"], "unknown")
        self.assertEqual(missing["results"][0]["reason"], "previous_attempt_not_found")
        self.assertEqual(self.case.adapter.created, 1)

    def test_different_raw_intent_is_not_settled_even_if_only_lf_differs(self):
        manifest = self.case.plan["manifest"]
        old = {**self.frozen, "body": self.frozen["body"] + "\n"}
        with self.d._target_lock(manifest) as state:
            state.intent(self.frozen["chapter"], "a" * 32, old)
            state.intent(self.frozen["chapter"], "b" * 32, self.frozen)
        self.seed()
        result = self.execute(reconcile_only=True)
        self.assertEqual(result["results"][0]["reason"], "unresolved_previous_content")
        self.assertEqual(list(self.case.global_root.glob("*.resolved.json")), [])
        self.assertEqual(self.case.adapter.created, 0)

    def test_policy_must_be_selected_again_and_never_rewrites_prior_settlement(self):
        self.case.adapter.fail_create = "after"
        self.case.execute()
        for item in self.case.adapter.remote.values():
            item["body"] = item["body"].rstrip("\n")
        self.execute(reconcile_only=True)
        settlement = next(self.case.global_root.glob("*.resolved.json"))
        prior = settlement.read_bytes()
        strict = self.case.execute(reconcile_only=True)
        self.assertEqual(strict["results"][0]["state"], "conflict")
        self.assertFalse(strict["all_chapters_accepted_this_run"])
        self.assertEqual(settlement.read_bytes(), prior)
        self.assertEqual(self.case.adapter.created, 1)

    def test_invalid_equivalence_settlement_fails_closed(self):
        self.case.adapter.fail_create = "after"
        self.case.execute()
        for item in self.case.adapter.remote.values():
            item["body"] = item["body"].rstrip("\n")
        self.execute(reconcile_only=True)
        path = next(self.case.global_root.glob("*.resolved.json"))
        original = json.loads(path.read_text())
        for changes in ({"content_sha256": "0" * 64}, {"body_matches": True}, {"comparison_policy": "strict"},
                        {"schema": 1}, {"observed_sha256": original["content_sha256"]}, {"plan_id": "invalid"}):
            with self.subTest(changes=changes):
                path.write_text(self.d.api.dumps({**original, **changes}), encoding="utf-8")
                with self.d._target_lock(self.case.plan["manifest"]) as state:
                    self.case.assert_code("platform_draft_journal_invalid", state.attempts, self.frozen["chapter"])
        path.write_text(self.d.api.dumps(original), encoding="utf-8")

    def test_mixed_batch_progress_and_result_keep_exact_count_separate(self):
        self.case.fixture.commit(2)
        self.case.plan = self.case.fixture.prepare((1, 2))
        chapters = self.case.plan["manifest"]["chapters"]
        self.case.adapter.seed(chapters[0], remote_id="one", body=chapters[0]["body"].rstrip("\n"))
        self.case.adapter.seed(chapters[1], remote_id="two")
        progress = []
        before = self.case.fixture.writing_snapshot()
        result = self.execute(progress=progress.append)
        self.assertTrue(result["all_chapters_accepted_this_run"])
        self.assertFalse(result["all_chapters_verified_this_run"])
        self.assertEqual((result["exact_verified_chapters"], result["equivalent_chapters"], result["accepted_chapters"]), (1, 1, 2))
        self.assertEqual((progress[-1]["verified_chapters"], progress[-1]["equivalent_chapters"], progress[-1]["accepted_chapters"]), (1, 1, 2))
        self.assertEqual(self.case.fixture.writing_snapshot(), before)

    def test_tampered_equivalence_event_cannot_claim_exact_or_expand_policy(self):
        self.seed(); self.execute()
        event_path = next((self.case.fixture.root / self.d.DIRECTORY / self.case.plan["id"]).glob("event-*.json"))
        original = json.loads(event_path.read_text())
        for changes in ({"body_matches": True}, {"comparison_policy": "trim"}, {"canonical_sha256": "0" * 64},
                        {"schema": 1}, {"event": "saved"}, {"observed_terminal_lf": original["expected_terminal_lf"]}):
            with self.subTest(changes=changes):
                event_path.write_text(self.d.api.dumps({**original, **changes}), encoding="utf-8")
                self.case.assert_code("platform_draft_journal_invalid", self.d.status, self.case.book, self.case.plan["id"])
        event_path.write_text(self.d.api.dumps(original), encoding="utf-8")


class WorkbenchPolicyTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(subprocess, "Popen", side_effect=AssertionError("No process is permitted in policy tests"))
        guard.start(); self.addCleanup(guard.stop)
        self.case = workbench.WorkbenchPlatformDraftTests(); self.case.setUp(); self.addCleanup(self.case.doCleanups)

    def test_preview_token_binds_policy_and_host_binding_preserves_it(self):
        strict, equivalent = self.case.preview(), self.case.preview(comparison_policy=POLICY)
        self.assertNotEqual(strict["preview_token"], equivalent["preview_token"])
        self.case.assert_code("platform_draft_preview_required", self.case.start, strict, comparison_policy=POLICY)
        binding = {key: equivalent[key] for key in ("book_id", "plan_id", "manifest_sha256", "target_sha256",
                                                   "mode", "profile_id", "profile_sha256", "comparison_policy")}
        self.assertEqual(workbench.drafts._host_binding(binding, equivalent["preview"])["comparison_policy"], POLICY)
        self.assertEqual(self.case.calls, [])

    def test_equivalent_job_reports_accepted_separately_from_exact(self):
        frozen = self.case.plan["manifest"]["chapters"][0]
        self.case.remote["existing"] = {**frozen, "remote_id": "existing", "state": "draft", "body": frozen["body"].rstrip("\n")}
        preview = self.case.preview(comparison_policy=POLICY)
        job = self.case.start(preview, comparison_policy=POLICY)
        completed = self.case.wait_job(job["id"])
        self.assertEqual(completed["state"], "succeeded_equivalent")
        self.assertEqual((completed["verified_chapters"], completed["equivalent_chapters"], completed["accepted_chapters"]), (0, 1, 1))
        self.assertFalse(completed["result"]["all_chapters_verified_this_run"])
        self.assertTrue(completed["result"]["all_chapters_accepted_this_run"])
        self.assertNotIn(("create", frozen["chapter"]), self.case.calls)

    def test_active_job_with_another_policy_is_not_reused(self):
        strict, equivalent = self.case.preview(), self.case.preview(comparison_policy=POLICY)
        self.case.release.clear()
        job = self.case.start(strict)
        self.assertTrue(self.case.entered.wait(2))
        self.case.assert_code("platform_draft_busy", self.case.start, equivalent, comparison_policy=POLICY)
        self.case.release.set()
        self.case.wait_job(job["id"])

    def test_encoding_is_bound_to_preview_and_cannot_be_added_after_authorization(self):
        self.case.fixture.commit(text=self.case.fixture.texts[1].replace("\n守门人", "\n\n守门人"), replace_last=True)
        self.case.plan = self.case.fixture.prepare()
        verbatim, rendered = self.case.preview(), self.case.preview(body_encoding=ENCODING)
        self.assertNotEqual(verbatim["preview_token"], rendered["preview_token"])
        self.case.assert_code("platform_draft_preview_required", self.case.start, verbatim, body_encoding=ENCODING)
        self.case.assert_code("platform_draft_preview_required", self.case.start, rendered)
        self.assertEqual(self.case.calls, [])

    def test_rendered_job_preserves_exact_counts_and_safe_receipt_fields(self):
        self.case.fixture.commit(text=self.case.fixture.texts[1].replace("\n守门人", "\n\n守门人"), replace_last=True)
        self.case.plan = self.case.fixture.prepare()
        frozen = self.case.plan["manifest"]["chapters"][0]
        encoded = workbench.drafts._encoded_content(frozen, ENCODING, "fanqie")
        self.case.remote["existing"] = {**encoded, "chapter": frozen["chapter"], "remote_id": "existing", "state": "draft"}
        job = self.case.start(self.case.preview(body_encoding=ENCODING), body_encoding=ENCODING)
        completed = self.case.wait_job(job["id"])
        self.assertEqual(completed["state"], "succeeded_equivalent")
        self.assertEqual((completed["verified_chapters"], completed["accepted_chapters"], completed["rendered_chapters"]), (0, 1, 1))
        self.assertEqual(completed["body_encoding"], ENCODING)
        row = completed["result"]["results"][0]
        self.assertTrue(row["rendered_body_matches"])
        self.assertFalse(row["body_matches"])
        self.assertEqual(row["encoded_content_sha256"], row["observed_sha256"])
        self.assertNotEqual(row["content_sha256"], row["observed_sha256"])
        self.assertNotIn(frozen["body"], json.dumps(completed, ensure_ascii=False))


class RenderedBodyTests(unittest.TestCase):
    def setUp(self):
        TerminalLFPolicyTests.setUp(self)
        body = self.frozen["body"].rstrip("\n").replace("\n", "\n\n") + "\n"
        self.case.fixture.commit(text=self.case.fixture.texts[1].replace(self.frozen["body"], body), replace_last=True)
        self.case.plan = self.case.fixture.prepare()
        self.frozen = self.case.frozen = self.case.plan["manifest"]["chapters"][0]

    def execute(self, **kwargs):
        return self.case.execute(body_encoding=ENCODING, **kwargs)

    def journal(self):
        return TerminalLFPolicyTests.journal(self)

    def test_rendering_creates_single_paragraph_boundaries_and_preserves_all_prose(self):
        before = self.case.fixture.writing_snapshot()
        preview = self.d.run_plan(self.case.book, self.case.plan["id"], body_encoding=ENCODING)
        self.assertEqual(self.case.adapter.calls, [])
        result = self.execute()
        remote = self.case.adapter.remote["1"]
        expected = self.frozen["body"].replace("\n\n", "\n")
        self.assertEqual(remote["body"], expected)
        row, event = result["results"][0], self.journal()[-1]
        self.assertEqual(row["state"], "saved_equivalent")
        self.assertTrue(row["rendered_body_matches"])
        self.assertFalse(row["body_matches"])
        self.assertTrue(result["all_chapters_accepted_this_run"])
        self.assertFalse(result["all_chapters_verified_this_run"])
        self.assertEqual(result["rendered_chapters"], 1)
        self.assertEqual(event["source_content_sha256"], event["content_sha256"])
        self.assertEqual(event["encoded_content_sha256"], event["observed_sha256"])
        self.assertNotEqual(event["encoded_content_sha256"], event["content_sha256"])
        self.assertEqual(preview["chapters"][0]["encoded_content_sha256"], event["encoded_content_sha256"])
        self.assertEqual(self.case.fixture.writing_snapshot(), before)
        self.assertEqual(self.case.plan["manifest"]["chapters"][0]["body"], self.frozen["body"])

    def test_strict_rendering_rejects_terminal_lf_loss_then_explicit_lf_policy_reconciles(self):
        encoded = self.d._encoded_content(self.frozen, ENCODING, "fanqie")
        self.case.adapter.seed(self.frozen, body=encoded["body"].rstrip("\n"))
        self.assertEqual(self.execute(reconcile_only=True)["results"][0]["state"], "conflict")
        result = self.execute(reconcile_only=True, comparison_policy=POLICY)
        self.assertTrue(result["all_chapters_accepted_this_run"])
        self.assertFalse(result["results"][0]["rendered_body_matches"])
        self.assertFalse(result["results"][0]["body_matches"])
        self.assertEqual((result["rendered_chapters"], result["equivalent_chapters"]), (1, 1))
        self.assertEqual(self.case.adapter.created, 0)

    def test_original_double_spaced_remote_is_not_accepted_as_encoded_target(self):
        self.case.adapter.seed(self.frozen)
        result = self.execute(reconcile_only=True)
        row = result["results"][0]
        self.assertEqual(row["state"], "conflict")
        self.assertTrue(row["body_matches"])
        self.assertFalse(row["rendered_body_matches"])
        self.assertFalse(row["accepted_this_run"])
        self.assertEqual(self.case.adapter.created, 0)

    def test_encoding_is_not_an_internal_lf_comparison_relaxation(self):
        encoded = self.d._encoded_content(self.frozen, ENCODING, "fanqie")
        for body in (encoded["body"].replace("\n", "\n\n", 1), "\n" + encoded["body"],
                     encoded["body"].replace("\n", " \n", 1), encoded["body"].replace("。", "！", 1)):
            with self.subTest(body=body[-12:]):
                self.case.adapter.seed(self.frozen, body=body)
                result = self.execute(reconcile_only=True, comparison_policy=POLICY)
                self.assertFalse(result["all_chapters_accepted_this_run"])
                self.assertEqual(result["results"][0]["state"], "conflict")

    def test_same_source_unknown_reconciles_encoded_and_other_source_intent_stays_unresolved(self):
        with self.d._target_lock(self.case.plan["manifest"]) as state:
            state.intent(self.frozen["chapter"], "a" * 32, self.frozen)
        encoded = self.d._encoded_content(self.frozen, ENCODING, "fanqie")
        self.case.adapter.seed(self.frozen, body=encoded["body"])
        self.assertTrue(self.execute(reconcile_only=True)["all_chapters_accepted_this_run"])
        settlement = json.loads(next(self.case.global_root.glob("*.resolved.json")).read_text())
        self.assertEqual(settlement["body_encoding"], ENCODING)
        self.assertEqual(settlement["content_sha256"], self.d._hash(self.d._content(self.frozen)))
        self.assertEqual(settlement["encoded_content_sha256"], self.d._hash(self.d._content(encoded)))
        with self.d._target_lock(self.case.plan["manifest"]) as state:
            state.intent(self.frozen["chapter"], "b" * 32, {**self.frozen, "body": self.frozen["body"] + "\n"})
        result = self.execute(reconcile_only=True)
        self.assertEqual(result["results"][0]["reason"], "unresolved_previous_content")
        self.assertEqual(len(list(self.case.global_root.glob("*.resolved.json"))), 1)
        self.assertEqual(self.case.adapter.created, 0)

    def test_shape_rejects_ambiguous_or_structured_markdown_and_preserves_plain_spaces(self):
        encode = lambda body: self.d._encoded_content({"title": "标题", "body": body}, ENCODING, "fanqie")["body"]
        for body in ("甲\n乙\n", "甲\n\n\n乙\n", "\n\n甲\n", "甲\n\n", "\n", "甲\n \n乙\n",
                     "# 标题\n", "- 列表\n", "1. 列表\n", "> 引用\n", "```代码\n", "~~~代码\n", "|表格|\n", "<div>正文</div>\n"):
            with self.subTest(body=body):
                self.case.assert_code("platform_draft_unsupported_body_encoding", encode, body)
        for body, expected in (("  中文对话——保留空格  \r\n\r\n“可以-继续。”\r\n", "  中文对话——保留空格  \n“可以-继续。”\n"),
                               ("\n甲\n\n乙\n", "\n甲\n乙\n"), ("甲", "甲")):
            self.assertEqual(encode(body), expected)

    def test_unsupported_source_and_nonfanqie_encoding_fail_before_remote_or_intent(self):
        bad_plan = self.case.fixture.prepare(platform="qimao")
        self.case.assert_code("platform_draft_invalid_body_encoding", self.case.execute, plan=bad_plan, body_encoding=ENCODING)
        for value in (None, [], "collapse", True):
            with self.subTest(value=value):
                self.case.assert_code("platform_draft_invalid_body_encoding", self.case.execute, body_encoding=value)
        self.case.fixture.commit(text=self.case.fixture.texts[1].replace("\n\n", "\n"), replace_last=True)
        self.case.plan = self.case.fixture.prepare()
        self.case.assert_code("platform_draft_unsupported_body_encoding", self.execute)
        self.assertEqual(self.case.adapter.calls, [])
        self.assertEqual(list(self.case.global_root.glob("*.intent.json")), [])

    def test_encoding_schema_cannot_be_tampered_or_relabelled_verbatim(self):
        self.execute()
        event_path = next((self.case.fixture.root / self.d.DIRECTORY / self.case.plan["id"]).glob("event-00000002-*.json"))
        original = json.loads(event_path.read_text())
        for changes in ({"body_encoding": "verbatim"}, {"encoded_content_sha256": "0" * 64},
                        {"source_content_sha256": "0" * 64}, {"rendered_body_matches": False}, {"comparison_policy": "invalid"}):
            with self.subTest(changes=changes):
                event_path.write_text(self.d.api.dumps({**original, **changes}), encoding="utf-8")
                self.case.assert_code("platform_draft_journal_invalid", self.d.status, self.case.book, self.case.plan["id"])
        event_path.write_text(self.d.api.dumps(original), encoding="utf-8")

    def test_lost_encoded_create_receipt_keeps_attempt_metadata_and_readonly_reconciles_once(self):
        self.case.adapter.fail_create = "after"
        result = self.execute(comparison_policy=POLICY)
        self.assertEqual(result["results"][0]["state"], "unknown")
        events = self.journal()
        self.assertEqual([item["event"] for item in events], ["sending", "unknown"])
        intent_path = next(self.case.global_root.glob("*.intent.json"))
        original = intent_path.read_bytes()
        intent = json.loads(original)
        for record in [intent, *events]:
            self.assertEqual(record["schema"], 2)
            self.assertEqual(record["body_encoding"], ENCODING)
            self.assertEqual(record["comparison_policy"], POLICY)
            self.assertEqual(record["source_content_sha256"], record["content_sha256"])
            self.assertEqual(record["encoded_content_sha256"], events[0]["encoded_content_sha256"])
            self.assertEqual(record["book_id"], self.case.book.meta("id"))
            self.assertEqual(record["plan_id"], self.case.plan["id"])
            self.assertEqual(record["manifest_sha256"], events[0]["manifest_sha256"])
        self.assertIsNone(events[-1]["observed_sha256"])
        self.assertIsNone(events[-1]["rendered_body_matches"])
        recorded = self.d.status(self.case.book, self.case.plan["id"])
        self.assertEqual(recorded["body_encoding"], ENCODING)
        self.assertEqual(recorded["comparison_policy"], POLICY)
        self.assertEqual(recorded["results"][0]["body_encoding"], ENCODING)
        self.assertEqual(recorded["results"][0]["encoded_content_sha256"], intent["encoded_content_sha256"])
        self.assertFalse(recorded["all_chapters_accepted_this_run"])
        reconciled = self.execute(reconcile_only=True, comparison_policy=POLICY)
        self.assertTrue(reconciled["all_chapters_accepted_this_run"])
        self.assertEqual(self.case.adapter.created, 1)
        self.assertEqual(intent_path.read_bytes(), original)

    def test_interrupted_sending_recovery_inherits_the_original_rendering_metadata(self):
        self.case.adapter.fail_create = "interrupt"
        with self.assertRaises(KeyboardInterrupt):
            self.execute(comparison_policy=POLICY)
        sending = self.journal()[0]
        self.assertEqual(sending["event"], "sending")
        status = self.d.status(self.case.book, self.case.plan["id"])
        self.assertTrue(status["results"][0]["recovery_required"])
        self.assertEqual(status["body_encoding"], ENCODING)
        self.assertTrue(self.execute(reconcile_only=True, comparison_policy=POLICY)["all_chapters_accepted_this_run"])
        recovered = self.journal()[1]
        self.assertEqual(recovered["reason"], "interrupted_sending")
        self.assertEqual(recovered["attempt_id"], sending["attempt_id"])
        for name in self.d.ATTEMPT_FIELDS:
            self.assertEqual(recovered[name], sending[name])
        self.assertEqual(self.case.adapter.created, 1)

    def test_verbatim_preview_preserves_historical_rendered_evidence(self):
        self.execute()
        recorded = self.d.status(self.case.book, self.case.plan["id"])
        preview = self.d.run_plan(self.case.book, self.case.plan["id"])
        old, row = recorded["results"][0], preview["results"][0]
        for name in ("body_encoding", "encoded_content_sha256", "content_sha256", "observed_sha256",
                     "rendered_body_matches", "body_matches", "state"):
            self.assertEqual(row[name], old[name])
        self.assertTrue(row["rendered_body_matches"])
        self.assertFalse(row["accepted_this_run"])
        self.assertEqual(preview["body_encoding"], "verbatim")
        self.assertEqual(preview["chapters"][0]["encoded_content_sha256"], self.d._hash(self.d._content(self.frozen)))
        self.assertNotEqual(preview["chapters"][0]["encoded_content_sha256"], row["encoded_content_sha256"])

    def test_pending_attempt_tampering_is_rejected_and_scope_change_cannot_settle(self):
        self.case.adapter.fail_create = "after"
        self.execute(comparison_policy=POLICY)
        directory = self.case.fixture.root / self.d.DIRECTORY / self.case.plan["id"]
        event_path = next(directory.glob("event-00000002-*.json"))
        original = json.loads(event_path.read_text())
        for changes in ({"source_content_sha256": "0" * 64}, {"encoded_content_sha256": "0" * 64},
                        {"comparison_policy": "strict"}, {"book_id": "other"}, {"plan_id": "0" * 32}):
            with self.subTest(changes=changes):
                event_path.write_text(self.d.api.dumps({**original, **changes}), encoding="utf-8")
                self.case.assert_code("platform_draft_journal_invalid", self.d.status, self.case.book, self.case.plan["id"])
        event_path.write_text(self.d.api.dumps(original), encoding="utf-8")
        intent_path = next(self.case.global_root.glob("*.intent.json"))
        intent = json.loads(intent_path.read_text())
        intent_path.write_text(self.d.api.dumps({**intent, "plan_id": "0" * 32}), encoding="utf-8")
        result = self.execute(reconcile_only=True, comparison_policy=POLICY)
        self.assertEqual(result["results"][0]["reason"], "unresolved_previous_content")
        self.assertEqual(list(self.case.global_root.glob("*.resolved.json")), [])
        self.assertEqual(self.case.adapter.created, 1)

    def test_global_intent_encoded_hash_is_recomputed_using_its_original_encoding(self):
        self.case.adapter.fail_create = "after"
        self.execute(comparison_policy=POLICY)
        intent_path = next(self.case.global_root.glob("*.intent.json"))
        original_bytes = intent_path.read_bytes()
        intent = json.loads(original_bytes)
        intent_path.write_text(self.d.api.dumps({**intent, "encoded_content_sha256": "0" * 64}), encoding="utf-8")
        blocked = self.execute(reconcile_only=True, comparison_policy=POLICY)
        self.assertEqual(blocked["results"][0]["reason"], "unresolved_previous_content")
        self.assertFalse(blocked["all_chapters_accepted_this_run"])
        self.assertEqual(list(self.case.global_root.glob("*.resolved.json")), [])
        self.assertEqual(self.case.adapter.created, 1)
        intent_path.write_bytes(original_bytes)
        recovered = self.execute(reconcile_only=True, comparison_policy=POLICY)
        self.assertTrue(recovered["all_chapters_accepted_this_run"])
        self.assertEqual(self.case.adapter.created, 1)
        self.assertEqual(intent_path.read_bytes(), original_bytes)


class RenderedBodyV2Tests(unittest.TestCase):
    def setUp(self):
        RenderedBodyTests.setUp(self)
        old = self.frozen["body"]
        self.case.fixture.commit(text=self.case.fixture.texts[1].replace(old, "\n" + old), replace_last=True)
        self.case.plan = self.case.fixture.prepare()
        self.frozen = self.case.frozen = self.case.plan["manifest"]["chapters"][0]
        self.assertTrue(self.frozen["body"].startswith("\n"))

    def execute(self, encoding=ENCODING_V2, **kwargs):
        return self.case.execute(body_encoding=encoding, **kwargs)

    def journal(self):
        return TerminalLFPolicyTests.journal(self)

    def test_v2_removes_only_one_boundary_lf_and_preserves_v1_output(self):
        source = {"title": "标题", "body": "\n  首段正文  \n\n“第二段-不变。”  \n"}
        original = copy.deepcopy(source)
        v1 = self.d._encoded_content(source, ENCODING, "fanqie")
        v2 = self.d._encoded_content(source, ENCODING_V2, "fanqie")
        self.assertEqual(v1["body"], "\n  首段正文  \n“第二段-不变。”  \n")
        self.assertEqual(v2["body"], "  首段正文  \n“第二段-不变。”  \n")
        self.assertEqual(source, original)
        no_prefix = {**source, "body": source["body"][1:]}
        self.assertEqual(self.d._encoded_content(no_prefix, ENCODING, "fanqie"),
                         self.d._encoded_content(no_prefix, ENCODING_V2, "fanqie"))

    def test_v1_receipts_and_hashes_remain_readable_and_reconcilable(self):
        expected_v1 = self.d._hash(self.d._content(self.d._encoded_content(self.frozen, ENCODING, "fanqie")))
        self.assertTrue(self.execute(encoding=ENCODING)["all_chapters_accepted_this_run"])
        before = copy.deepcopy(self.journal())
        recorded = self.d.status(self.case.book, self.case.plan["id"])
        self.assertEqual(recorded["results"][0]["body_encoding"], ENCODING)
        self.assertEqual(recorded["results"][0]["encoded_content_sha256"], expected_v1)
        preview = self.d.run_plan(self.case.book, self.case.plan["id"], body_encoding=ENCODING_V2)
        self.assertEqual(preview["results"][0]["encoded_content_sha256"], expected_v1)
        self.assertEqual(preview["results"][0]["body_encoding"], ENCODING)
        self.assertNotEqual(preview["chapters"][0]["encoded_content_sha256"], expected_v1)
        self.assertTrue(self.execute(encoding=ENCODING, reconcile_only=True)["all_chapters_accepted_this_run"])
        self.assertEqual(self.journal()[:len(before)], before)
        self.assertEqual(self.case.adapter.created, 1)

    def test_v1_unknown_requires_remote_prefix_repair_before_v2_readonly_settlement(self):
        self.case.adapter.fail_create = "after"
        self.execute(encoding=ENCODING, comparison_policy=POLICY)
        path = next(self.case.global_root.glob("*.intent.json"))
        intent_bytes = path.read_bytes()
        original = json.loads(intent_bytes)
        blocked = self.execute(reconcile_only=True, comparison_policy=POLICY)
        self.assertEqual(blocked["results"][0]["state"], "conflict")
        self.assertEqual(list(self.case.global_root.glob("*.resolved.json")), [])
        self.assertEqual(self.case.adapter.created, 1)
        remote = next(iter(self.case.adapter.remote.values()))
        remote["body"] = self.d._encoded_content(self.frozen, ENCODING_V2, "fanqie")["body"]
        reconciled = self.execute(reconcile_only=True, comparison_policy=POLICY)
        self.assertTrue(reconciled["all_chapters_accepted_this_run"])
        self.assertTrue(reconciled["results"][0]["rendered_body_matches"])
        self.assertFalse(reconciled["results"][0]["body_matches"])
        settled = json.loads(next(self.case.global_root.glob("*.resolved.json")).read_text())
        self.assertEqual(settled["body_encoding"], ENCODING_V2)
        self.assertNotEqual(settled["encoded_content_sha256"], original["encoded_content_sha256"])
        self.assertEqual(settled["content_sha256"], original["content_sha256"])
        self.assertEqual(path.read_bytes(), intent_bytes)
        self.assertEqual(self.case.adapter.created, 1)

    def test_v2_lost_receipt_and_interrupted_recovery_keep_v2_metadata(self):
        self.case.adapter.fail_create = "interrupt"
        with self.assertRaises(KeyboardInterrupt):
            self.execute(comparison_policy=POLICY)
        intent = json.loads(next(self.case.global_root.glob("*.intent.json")).read_text())
        sending = self.journal()[0]
        self.assertEqual(intent["body_encoding"], ENCODING_V2)
        self.assertEqual(sending["body_encoding"], ENCODING_V2)
        self.assertFalse(self.case.adapter.remote["1"]["body"].startswith("\n"))
        self.assertTrue(self.execute(reconcile_only=True, comparison_policy=POLICY)["all_chapters_accepted_this_run"])
        recovered = self.journal()[1]
        self.assertEqual(recovered["reason"], "interrupted_sending")
        for name in self.d.ATTEMPT_FIELDS:
            self.assertEqual(recovered[name], sending[name])
        self.assertEqual(self.case.adapter.created, 1)

    def test_v2_lost_create_response_records_v2_unknown_then_reads_without_recreate(self):
        self.case.adapter.fail_create = "after"
        result = self.execute(comparison_policy=POLICY)
        self.assertEqual(result["results"][0]["state"], "unknown")
        records = [json.loads(next(self.case.global_root.glob("*.intent.json")).read_text()), *self.journal()]
        for record in records:
            self.assertEqual(record["body_encoding"], ENCODING_V2)
            self.assertEqual(record["comparison_policy"], POLICY)
            self.assertEqual(record["encoded_content_sha256"], records[0]["encoded_content_sha256"])
        recorded = self.d.status(self.case.book, self.case.plan["id"])
        self.assertEqual(recorded["body_encoding"], ENCODING_V2)
        self.assertEqual(recorded["results"][0]["body_encoding"], ENCODING_V2)
        self.assertTrue(self.execute(reconcile_only=True, comparison_policy=POLICY)["all_chapters_accepted_this_run"])
        self.assertEqual(self.case.adapter.created, 1)

    def test_v2_rejects_unsupported_sources_and_other_platforms(self):
        for body in ("\n\n甲\n", "甲\n乙\n", "甲\n\n\n乙\n", "甲\n\n", "# 标题\n", "> 引用\n"):
            with self.subTest(body=body):
                self.case.assert_code("platform_draft_unsupported_body_encoding", self.d._encoded_content,
                                      {"title": "标题", "body": body}, ENCODING_V2, "fanqie")
        for platform in ("qimao", "qidian"):
            self.case.assert_code("platform_draft_invalid_body_encoding", self.d._encoded_content, self.frozen, ENCODING_V2, platform)
