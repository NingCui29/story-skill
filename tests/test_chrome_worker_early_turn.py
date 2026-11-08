"""Early owned-turn completion retains cleanup and unknown-write protection."""
import json
from unittest.mock import patch
import unittest

from tests import test_chrome_worker_controller as protocol
from tests import test_platform_draft_auto_connect as auto
from tests import test_workbench_connection_diagnostics_ui as diagnostics_ui


EARLY = "platform_chrome_worker_turn_ended_early"


class EarlyTurnControllerTests(unittest.TestCase):
    def setUp(self):
        self.f = protocol.ControllerTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.cooperative_stop = False

    def busy(self):
        self.f.start()
        self.f.wait(lambda: self.f.state()["ready"])
        entry = next(iter(self.f.controller._workers.values()))
        process, client = self.f.processes[0], entry["client"]
        process.checkpoint({"done": False, "state": "busy", "phase": "queue_operation", "processed": 1})
        self.f.wait(lambda: any(row.get("phase") == "worker_checkpoint" and
                               row.get("worker_phase") == "queue_operation" for row in entry["diagnostics"]))
        return entry, process, client

    def assert_no_retry(self, process):
        self.assertEqual(sum(row.get("method") == "turn/start" for row in process.messages), 1)
        self.assertEqual(len(self.f.processes), 1)

    def test_owned_busy_normal_turn_end_is_specific_and_cleanup_stays_unconfirmed(self):
        entry, process, client = self.busy()
        process.finish()
        self.f.wait(entry["done"].is_set)
        view = self.f.state()
        self.assertEqual(view["error"], EARLY)
        self.assertTrue(view["cleanup_unconfirmed"])
        self.assertFalse(view["worker_done_confirmed"])
        self.assertTrue(view["turn_completed_normally"])
        self.assertIsNone(client.worker_done)
        self.assertIn(EARLY, [row.get("code") for row in view["diagnostics"]])
        self.assert_no_retry(process)

    def test_polling_before_supervisor_keeps_specific_early_end_instead_of_health_expiry(self):
        entry, process, client = self.busy()
        # Preserve the production diagnostic->controller lock order while
        # injecting a received event before the supervisor can act on it.
        with entry["diagnostic_lock"], self.f.controller._lock:
            with patch.object(client, "_emit"):
                client._observe(protocol.owned_message("turn/completed", turn={"status": "completed"}))
            self.assertEqual(self.f.state()["error"], EARLY)
        self.f.wait(entry["done"].is_set)
        self.assertEqual(self.f.state()["error"], EARLY)
        self.assertTrue(self.f.state()["cleanup_unconfirmed"])
        self.assert_no_retry(process)

    def test_checkpoint_from_another_turn_cannot_establish_an_early_end(self):
        entry, process, client = self.busy()
        client.last_checkpoint_turn = (client.thread_id, "another-turn")
        process.finish()
        self.f.wait(entry["done"].is_set)
        self.assertNotEqual(self.f.state()["error"], EARLY)
        self.assertTrue(self.f.state()["cleanup_unconfirmed"])
        self.assert_no_retry(process)

    def test_ready_queue_wait_completion_is_not_reclassified_as_busy_early_end(self):
        entry, process, client = self.busy()
        process.checkpoint({"done": False, "state": "ready", "phase": "queue_wait", "processed": 1}, item_id="idle-checkpoint")
        self.f.wait(lambda: client.last_checkpoint.get("phase") == "queue_wait")
        process.finish()
        self.f.wait(entry["done"].is_set)
        self.assertNotEqual(self.f.state()["error"], EARLY)
        self.assert_no_retry(process)

    def test_real_health_failure_before_turn_completion_keeps_its_own_reason(self):
        entry, process, _ = self.busy()
        with entry["diagnostic_lock"], self.f.controller._lock:
            self.f.core.health.clear()
            self.assertEqual(self.f.state()["error"], "platform_chrome_worker_health_expired")
            process.finish()
        self.f.wait(entry["done"].is_set)
        self.assertEqual(self.f.state()["error"], "platform_chrome_worker_health_expired")
        self.assert_no_retry(process)

    def test_provider_failure_is_not_relabelled_as_early_completion(self):
        entry, process, _ = self.busy()
        process.stdout.put({"method": "error", "params": {"threadId": "ephemeral-thread", "turnId": "real-fake-wire-turn",
                           "error": {"message": "PRIVATE", "codexErrorInfo": "responseStreamDisconnected"}}})
        self.f.wait(entry["done"].is_set)
        view = self.f.state()
        self.assertEqual(view["error"], "platform_chrome_worker_turn_failed")
        self.assertEqual(view["error_kind"], "responseStreamDisconnected")
        self.assertNotIn("PRIVATE", json.dumps(view))
        self.assert_no_retry(process)

    def test_actual_terminal_worker_and_normal_turn_remain_confirmed(self):
        entry, process, _ = self.busy()
        process.checkpoint({"done": True, "state": "stopped", "phase": "cleanup", "processed": 1}, item_id="terminal-checkpoint")
        process.finish()
        self.f.wait(entry["done"].is_set)
        view = self.f.state()
        self.assertIsNone(view["error"])
        self.assertTrue(view["worker_done_confirmed"])
        self.assertFalse(view["cleanup_unconfirmed"])
        self.assert_no_retry(process)


class EarlyTurnDraftTests(unittest.TestCase):
    def setUp(self):
        self.f = auto.PlatformDraftAutoConnectTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def interrupted_host(self, operation):
        original = self.f.host
        def host(*args, **kwargs):
            value = original(*args, **kwargs)
            def fail(*args, **kwargs):
                auto.story.fail(EARLY, "Synthetic early model completion")
            setattr(value, operation, fail)
            return value
        return host

    def test_inspection_exposes_early_end_and_never_becomes_actionable(self):
        original = self.f.launcher.disconnect
        def disconnect(*args, **kwargs):
            return {**original(*args, **kwargs), "error": EARLY, "cleanup_unconfirmed": True,
                    "worker_done_confirmed": False}
        with patch.object(self.f.host_factory, "side_effect", self.interrupted_host("find_drafts")), \
             patch.object(self.f.launcher, "disconnect", side_effect=disconnect):
            job = self.f.wait_job(self.f.start(self.f.preview(inspect_only=True, body_encoding="plain_paragraphs_v1"),
                                              inspect_only=True, body_encoding="plain_paragraphs_v1")["id"])
        self.assertEqual(job["state"], "inspection_blocked")
        self.assertFalse(job["result"]["can_execute"])
        self.assertEqual(job["connection_diagnostics"]["reason"], EARLY)
        self.assertTrue(job["connection_diagnostics"]["cleanup_unconfirmed"])
        self.assertEqual(job["error"], "platform_chrome_worker_cleanup_unconfirmed")
        self.assertEqual(len(self.f.starts()), 1)
        self.assertFalse(any(row[0] in {"create", "update"} for row in self.f.operations))

    def test_claimed_write_remains_unknown_and_not_replayed(self):
        with patch.object(self.f.host_factory, "side_effect", self.interrupted_host("create_draft")):
            job = self.f.wait_job(self.f.start()["id"])
        self.assertEqual(job["state"], "unknown")
        self.assertEqual(job["result"]["results"][0]["reason"], EARLY)
        self.assertEqual(auto.drafts.status(self.f.book, self.f.plan["id"])["results"][0]["state"], "unknown")
        self.assertEqual(len(self.f.starts()), 1)


class EarlyTurnUITests(unittest.TestCase):
    js = diagnostics_ui.ConnectionDiagnosticsUITests.js

    def test_readonly_inspection_shows_early_end_and_unconfirmed_cleanup_together(self):
        self.js(diagnostics_ui.ConnectionDiagnosticsUITests.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;
platformDraftDrawJob(p,failedInspection('platform_chrome_worker_cleanup_unconfirmed',{
 state:'inspection_blocked',current_chapter:1,checked_chapters:1,
 connection_diagnostics:connection('platform_chrome_worker_turn_ended_early',{
  model_verified:true,resolved_model:'gpt-6.1-terra',cleanup_unconfirmed:true,worker_done_confirmed:false}),
 result:{inspection:true,can_execute:false,source_matches_current:true,checked_chapters:1,total_chapters:30,
  changes:[{chapter:1,action:'blocked',reason:'platform_chrome_worker_unavailable'}],results:[]}}));
assert.ok(p.status.textContent.includes('尚未确认退出'));
assert.ok(p.jobNote.textContent.includes('提前结束'));
assert.ok(p.nextHint.textContent.includes('提前结束'));
assert.ok(p.nextHint.textContent.includes('尚未确认'));
assert.ok(p.progressLabel.textContent.includes('未写入平台'));
assert.ok(!p.status.textContent.includes('远端结果未知'));
assert.ok(!p.nextHint.textContent.includes('远端可能已发生保存'));
assert.equal(p.run.disabled,true);assert.equal(starts().length,0);
''')

    def test_early_model_end_is_distinct_from_provider_disconnect_and_keeps_unknown(self):
        self.js(diagnostics_ui.ConnectionDiagnosticsUITests.fixture + r'''
await platformDraftOpen();const p=platformDraftPanel;
const reason='platform_chrome_worker_turn_ended_early';
platformDraftDrawJob(p,job({state:'unknown',stage:'finished',done:true,error:reason,
 connection_diagnostics:connection(reason,{model_verified:true,resolved_model:'gpt-6.1-terra',cleanup_unconfirmed:true}),
 result:{results:[{chapter:1,state:'unknown',recorded_this_run:true,reason}]}}));
assert.ok(p.status.textContent.includes('远端结果未知'));
assert.ok(p.status.textContent.includes('提前结束'));
assert.ok(!p.status.textContent.includes('模型响应通道中断'));
assert.ok(!p.status.textContent.includes('未写入平台'));
assert.equal(p.recoveryAction.textContent,'先只读核对结果');
assert.equal(starts().length,0);
''')


if __name__ == "__main__":
    unittest.main()
