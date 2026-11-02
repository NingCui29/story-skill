"""Host authorization and CLI handoffs; synthetic books, no real browser."""
from contextlib import contextmanager
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_platform_draft_assistant as base

drafts, story = base.drafts, base.story


class NativeDraftHandoffTests(unittest.TestCase):
    setUp = base.AssistantTaskTests.setUp
    cleanup_jobs = base.AssistantTaskTests.cleanup_jobs
    bridge_status = base.AssistantTaskTests.bridge_status
    host = base.AssistantTaskTests.host
    wait_job = base.AssistantTaskTests.wait_job
    assert_code = base.AssistantTaskTests.assert_code
    prepare = base.AssistantTaskTests.prepare

    def native(self, **kwargs):
        return self.prepare(execution_host="qoder-cn", native_model_authorized=True, **kwargs)

    def test_native_authorization_is_explicit_and_codex_default_stays_terra(self):
        for host in ("qoder-cn", "workbuddy"):
            self.assert_code("platform_draft_native_model_not_authorized", self.prepare, execution_host=host)
        self.assert_code("invalid_input", self.prepare, native_model_authorized=True)
        request = self.prepare()
        self.assertEqual(request["execution_host"], "codex")
        self.assertFalse(request["native_model_authorized"])
        self.assertIn("gpt-6.1-terra", request["handoff_prompt"])
        self.assertEqual(self.operations, [])

    def test_prepare_native_task_does_not_launch_or_claim_browser(self):
        request = self.native()
        self.assertTrue(request["native_model_authorized"])
        self.assertEqual(request["execution_host"], "qoder-cn")
        self.assertEqual(request["model_policy"], "user_authorized_native")
        self.assertIn("Qoder CN 官方当前会话", request["handoff_prompt"])
        self.assertNotIn("model_verified", request)
        self.assertEqual(self.native()["request_id"], request["request_id"])
        self.assertEqual(self.operations, [])
        self.assertEqual(self.launcher.calls, [])

    def test_different_host_cannot_silently_reuse_prepared_task(self):
        self.native()
        self.assert_code("platform_draft_busy", self.prepare)
        self.assert_code("platform_draft_busy", self.prepare, execution_host="workbuddy", native_model_authorized=True)

    def test_missing_native_sdk_preserves_unclaimed_request(self):
        request = self.native()
        def missing(_):
            story.fail("platform_chrome_worker_native_sdk_unavailable", "Synthetic missing SDK")
        with patch.object(self.launcher, "_native_sdk_path", side_effect=missing, create=True):
            self.assert_code("platform_chrome_worker_native_sdk_unavailable", drafts.assistant_run,
                             self.root, request["request_id"], expected_book_id=self.book.meta("id"))
        result = drafts.workbench_assistant_status(self.root, request["request_id"], expected_book_id=self.book.meta("id"))
        self.assertEqual(result["state"], "prepared")
        self.assertIsNone(result["claimed_at"])
        self.assertEqual(self.operations, [])

    def test_native_inspection_then_sync_passes_bound_host_and_does_not_replay(self):
        self.health_ready = True
        original = self.launcher.assistant_session.side_effect
        received = []
        @contextmanager
        def native_session(root, binding, *, execution_host, native_model_authorized, **kwargs):
            received.append((execution_host, native_model_authorized))
            with original(root, binding, **kwargs) as session:
                yield session
        with patch.object(self.launcher, "_native_sdk_path", return_value=Path("/synthetic/public-sdk.js"), create=True), \
                patch.object(self.launcher, "assistant_session", side_effect=native_session):
            request = self.native()
            checked = drafts.assistant_run(self.root, request["request_id"], expected_book_id=self.book.meta("id"))
            self.assertTrue(checked["can_sync"], checked)
            self.assertFalse(any(op[0] in {"create", "update"} for op in self.operations))
            self.assert_code("platform_draft_inspection_required", self.prepare, mode="sync_drafts",
                             inspection_id=request["request_id"], confirmed=True)
            sync = self.native(mode="sync_drafts", inspection_id=request["request_id"], confirmed=True)
            saved = drafts.assistant_run(self.root, sync["request_id"], expected_book_id=self.book.meta("id"))
            self.assertEqual(saved["state"], "succeeded", saved)
            before = list(self.operations)
            self.assert_code("platform_draft_assistant_request_consumed", drafts.assistant_run,
                             self.root, sync["request_id"], expected_book_id=self.book.meta("id"))
            self.assertEqual(self.operations, before)
        self.assertEqual(received, [("qoder-cn", True), ("qoder-cn", True)])

    def test_cli_prepare_status_cancel_are_local_only(self):
        parser = story.parser()
        args = parser.parse_args(["publish-draft-assist-prepare", "--book", str(self.root), "--id", self.plan["id"],
                                  "--profile", self.profile_path.name, "--host", "qoder-cn", "--allow-native-host-model"])
        request = drafts.run(self.book, args)
        for command, expected in (("status", "prepared"), ("cancel", "cancelled")):
            args = parser.parse_args(["publish-draft-assist-" + command, "--book", str(self.root),
                                      "--request", request["request_id"]])
            self.assertEqual(drafts.run(self.book, args)["state"], expected)
        self.assertEqual(self.operations, [])
        self.assertEqual(self.launcher.calls, [])

    def test_host_catalog_reports_only_sdk_presence_without_connecting(self):
        def native_path(host):
            if host == "workbuddy":
                story.fail("platform_chrome_worker_native_sdk_unavailable", "Synthetic missing component")
            return Path("/synthetic/public-qoder-sdk.js")
        with patch.object(story.platform_browser, "browser_runtime_status", return_value={"status": "browser_unverified"}), \
                patch.object(self.launcher, "_sdk_path", return_value=Path("/synthetic/public-codex-sdk.mjs"), create=True), \
                patch.object(self.launcher, "_workbuddy_cli_path", side_effect=lambda: native_path("workbuddy"), create=True), \
                patch.object(self.launcher, "_native_sdk_path", side_effect=native_path, create=True):
            hosts = drafts._browser_runtime()["execution_hosts"]
        self.assertEqual([row["id"] for row in hosts], ["codex", "qoder-cn", "workbuddy"])
        self.assertEqual([row["sdk_available"] for row in hosts], [True, True, False])
        self.assertTrue(all(row["browser_ready"] is False and row["platform_verified"] is False for row in hosts))
        self.assertEqual(hosts[-1]["reason"], "platform_chrome_worker_native_sdk_unavailable")
        self.assertEqual(self.operations, [])
        self.assertEqual(self.launcher.calls, [])


if __name__ == "__main__":
    unittest.main()
