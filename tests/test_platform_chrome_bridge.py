"""Local bridge contract tests. Fake observations never contact a platform."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from tests import test_publish as fixtures
from tests.test_platform_browser import profile
from tests.test_platform_browser_preview import configuration as preview_profile


story = fixtures.story
bridge = story.platform_chrome_bridge
drafts = story.platform_drafts


class ChromeBridgeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PublishTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture.commit()
        self.book, self.root = self.fixture.book, self.fixture.root
        self.plan = self.fixture.prepare()
        self.directory = self.root / drafts.PROFILE_DIRECTORY
        self.directory.mkdir()
        self.path = self.directory / "host.json"
        self.config = {**preview_profile(), "execution_mode": "host_assisted_cua"}
        self.path.write_text(json.dumps(self.config), encoding="utf-8")
        self.handle = self.root / "private-handle.json"
        self.response = self.root / "private-response.json"
        self.target_root = self.root.parent / "private-global-state"
        self.target_root.mkdir(mode=0o700)
        self.lock_patch = patch.object(drafts, "_lock_directory", return_value=self.target_root)
        self.lock_patch.start()
        self.addCleanup(self.lock_patch.stop)
        self.native_patch = patch.object(story.platform_browser.BrowserAdapter, "__enter__",
                                         side_effect=AssertionError("native browser must never start"))
        self.native_patch.start()
        self.addCleanup(self.native_patch.stop)
        self.jobs = []
        self.addCleanup(self.cleanup_jobs)

    def cleanup_jobs(self):
        for job_id in self.jobs:
            deadline = time.time() + 3
            while time.time() < deadline and not drafts.workbench_job(self.root, job_id)["done"]:
                time.sleep(.01)
        with drafts._WORKBENCH_LOCK:
            for key, job in list(drafts._WORKBENCH_JOBS.items()):
                if job["_root"] == str(self.root) and job["done"]:
                    del drafts._WORKBENCH_JOBS[key]

    def assert_code(self, code, action, *args, **kwargs):
        return self.fixture.assert_code(code, action, *args, **kwargs)

    def attach(self, **kwargs):
        return bridge.attach(self.book, self.plan["id"], self.path.name, self.handle, **kwargs)

    def binding(self):
        return bridge._binding(self.book, self.plan["id"], self.path.name)[0]

    def preview(self, **kwargs):
        return drafts.workbench_preview(self.root, self.plan["id"], self.path.name,
                                       expected_book_id=self.book.meta("id"), **kwargs)

    def start(self, preview=None, **kwargs):
        preview = preview or self.preview(**kwargs)
        result = drafts.workbench_start(self.root, self.plan["id"], self.path.name,
            expected_book_id=self.book.meta("id"), expected_profile_sha256=preview["profile_sha256"],
            preview_token=preview["preview_token"], execute=True, **kwargs)
        self.jobs.append(result["id"])
        return result

    def write_response(self, request, result=None, error=None, **changes):
        message = {name: request[name] for name in ("schema", "id", "nonce", "session_id", "binding_sha256")}
        message.update(ok=error is None, **({"result": result} if error is None else {"error": error}))
        message.update(changes)
        self.response.write_text(json.dumps(message, ensure_ascii=False), encoding="utf-8")
        self.response.chmod(0o600)
        return message

    def enqueue(self, operation="verify_target", params=None):
        """Queue a production request on a thread; no fake browser adapter."""
        result, errors = [], []
        adapter = bridge.HostAdapter(self.root, self.binding(), self.config)
        adapter.__enter__()
        def call():
            try:
                result.append(adapter._request(operation, params or {}))
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=call)
        thread.start()
        self.addCleanup(thread.join, 2)
        deadline = time.time() + 2
        while time.time() < deadline:
            claim = bridge.next_request(self.book, self.handle)
            if claim["request_id"]:
                return thread, result, errors, claim, json.loads(Path(claim["request_path"]).read_text())
            time.sleep(.01)
        self.fail("request was not queued")

    def driver(self, remote, calls, *, lose_create=False):
        stop, failures = threading.Event(), []
        def work():
            book = story.Book(self.root)
            try:
                while not stop.is_set():
                    claim = bridge.next_request(book, self.handle)
                    if not claim["request_id"]:
                        stop.wait(.01)
                        continue
                    request = json.loads(Path(claim["request_path"]).read_text())
                    op, params = request["operation"], request["params"]
                    calls.append(op)
                    if op == "verify_target":
                        result = request["binding"]["target"]
                    elif op == "find_drafts":
                        result = [copy.deepcopy(row) for row in remote.values() if row["chapter"] == params["chapter"]]
                    elif op == "create_draft":
                        result = "remote-" + str(params["chapter"])
                        remote[result] = {"remote_id": result, "state": "draft", **params}
                        if lose_create:
                            continue
                    else:
                        result = copy.deepcopy(remote[params["remote_id"]])
                    self.write_response(request, result)
                    bridge.respond(book, self.handle, claim["request_id"], self.response)
            except BaseException as error:
                failures.append(error)
            finally:
                book.close()
        thread = threading.Thread(target=work)
        thread.start()
        def cleanup():
            stop.set(); thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
        self.addCleanup(cleanup)
        return stop

    def test_no_worker_blocks_without_browser_dependency(self):
        with patch.object(drafts, "_browser_runtime", side_effect=AssertionError("not required")):
            preview = self.preview()
        self.assertFalse(preview["can_start"])
        self.assertEqual(preview["blockers"], ["platform_chrome_worker_unavailable"])
        self.assertFalse(preview["browser_runtime"]["playwright_required"])
        self.assert_code("platform_chrome_worker_unavailable",
                         bridge.HostAdapter(self.root, self.binding(), self.config).__enter__)

    def test_obsolete_fanqie_host_profile_cannot_open_editor_for_reconciliation(self):
        obsolete = {**profile(), "execution_mode": "host_assisted_cua"}
        self.path.write_text(json.dumps(obsolete))
        preview = self.preview(reconcile_only=True)
        self.assertFalse(preview["can_start"])
        self.assertIn("platform_browser_readback_required", preview["blockers"])
        self.assertIsNone(preview["preview_token"])
        self.assert_code("platform_browser_readback_required", bridge.HostAdapter,
                         self.root, self.binding(), obsolete)

    def test_attach_has_private_handle_and_allows_local_preview(self):
        result = self.attach()
        self.assertNotIn("token", result)
        self.assertFalse(result["browser_started"])
        self.assertTrue(self.preview()["can_start"])
        self.assertEqual(os.stat(self.handle).st_mode & 0o777, 0o600)
        directory = self.root / bridge.DIRECTORY / result["session_id"]
        self.assertEqual(os.stat(directory).st_mode & 0o777, 0o700)
        for name in ("session.json", "lease.json"):
            self.assertEqual(os.stat(directory / name).st_mode & 0o777, 0o600)

    def test_duplicate_attach_and_existing_handle_do_not_leave_active_orphan(self):
        self.handle.write_text("existing", encoding="utf-8")
        self.handle.chmod(0o600)
        self.assert_code("platform_chrome_unsafe_file", self.attach)
        self.assertFalse(bridge.worker_status(self.root, self.binding())["attached"])
        self.assertEqual(self.handle.read_text(), "existing")
        self.handle.unlink()
        self.attach()
        self.assert_code("platform_chrome_worker_busy", self.attach)

    def test_handle_rejects_wrong_token_and_symlinks(self):
        self.attach()
        data = json.loads(self.handle.read_text())
        data["token"] = "wrong"
        self.handle.write_text(json.dumps(data))
        self.assert_code("platform_chrome_invalid_handle", bridge.heartbeat, self.book, self.handle)
        link = self.root / "handle-link.json"
        link.symlink_to(self.handle)
        with self.assertRaises(story.StoryError):
            bridge.heartbeat(self.book, link)

    def test_single_claim_bound_response_and_no_duplicate_ack(self):
        self.attach()
        thread, result, errors, claim, request = self.enqueue()
        self.assertEqual(bridge.next_request(self.book, self.handle)["request_id"], None)
        self.assertEqual(os.stat(claim["request_path"]).st_mode & 0o777, 0o600)
        self.write_response(request, request["binding"]["target"], nonce="wrong")
        self.assert_code("platform_chrome_invalid_message", bridge.respond, self.book, self.handle,
                         claim["request_id"], self.response)
        self.write_response(request, error="password secret must not escape")
        self.assert_code("platform_chrome_invalid_message", bridge.respond, self.book, self.handle,
                         claim["request_id"], self.response)
        self.write_response(request, request["binding"]["target"])
        response = bridge.respond(self.book, self.handle, claim["request_id"], self.response)
        self.assertTrue(Path(response["response_path"]).is_file())
        self.assert_code("platform_chrome_already_responded", bridge.respond, self.book, self.handle,
                         claim["request_id"], self.response)
        thread.join(2)
        self.assertEqual(errors, [])
        self.assertEqual(result, [request["binding"]["target"]])

    def test_detach_blocks_and_late_response_is_rejected(self):
        self.attach()
        with patch.dict(bridge.OP_SECONDS, verify_target=.25):
            thread, _, errors, claim, request = self.enqueue()
            bridge.heartbeat(self.book, self.handle, detach=True)
            thread.join(2)
        self.assertEqual(errors[0].code, "platform_chrome_worker_unavailable")
        self.write_response(request, request["binding"]["target"])
        self.assert_code("platform_chrome_worker_unavailable", bridge.respond, self.book, self.handle,
                         claim["request_id"], self.response)

    def test_expired_lease_blocks_even_if_handle_remains(self):
        self.attach()
        with patch.object(bridge.time, "time", return_value=time.time() + 121):
            self.assertFalse(bridge.worker_status(self.root, self.binding())["attached"])
            self.assert_code("platform_chrome_worker_unavailable", bridge.heartbeat, self.book, self.handle)

    def test_detached_session_history_does_not_exhaust_future_attachments(self):
        preserved = {}
        with patch.object(bridge, "MAX_SESSIONS", 2):
            for index in range(3):
                self.handle = self.root / ("handle-" + str(index) + ".json")
                attached = self.attach()
                bridge.heartbeat(self.book, self.handle, detach=True)
                directory = self.root / bridge.DIRECTORY / attached["session_id"]
                preserved.update({directory / name: (directory / name).read_bytes()
                                  for name in ("session.json", "lease.json")})
            self.handle = self.root / "handle-current.json"
            self.attach()
            self.assertTrue(self.preview()["can_start"])
            self.assertEqual(bridge.worker_status(self.root, self.binding())["active_sessions"], 1)
        self.assertEqual({path: path.read_bytes() for path in preserved}, preserved)

    def test_expired_session_history_does_not_exhaust_future_attachments(self):
        now = time.time()
        with patch.object(bridge, "MAX_SESSIONS", 2):
            for index in range(4):
                self.handle = self.root / ("expired-handle-" + str(index) + ".json")
                with patch.object(bridge.time, "time", return_value=now + index * 121):
                    self.attach()
                    status = bridge.worker_status(self.root, self.binding())
                    self.assertTrue(status["attached"])
                    self.assertEqual(status["active_sessions"], 1)
        self.assertEqual(len(list((self.root / bridge.DIRECTORY).iterdir())), 4)

    def test_live_session_limit_blocks_new_binding_without_breaking_existing_worker(self):
        self.attach()
        binding, profile = bridge._binding(self.book, self.plan["id"], self.path.name)
        other_binding = copy.deepcopy(binding)
        other_binding["target"]["account_id"] = "another-account"
        self.handle = self.root / "other-handle.json"
        with patch.object(bridge, "MAX_SESSIONS", 1):
            with patch.object(bridge, "_binding", return_value=(other_binding, profile)):
                self.assert_code("platform_chrome_session_limit", self.attach)
            self.assertTrue(bridge.worker_status(self.root, binding)["attached"])
        self.assertFalse(self.handle.exists())

    def test_expired_request_is_not_redispatched_or_acked(self):
        self.attach()
        with patch.dict(bridge.OP_SECONDS, verify_target=.1):
            thread, _, errors, claim, request = self.enqueue()
            thread.join(2)
        self.assertEqual(errors[0].code, "platform_chrome_request_timeout")
        self.assertEqual(bridge.next_request(self.book, self.handle)["request_id"], None)
        self.write_response(request, request["binding"]["target"])
        self.assert_code("platform_chrome_request_expired", bridge.respond, self.book, self.handle,
                         claim["request_id"], self.response)

    def test_host_workbench_job_uses_real_transport_and_compares_complete_body(self):
        self.attach()
        remote, calls = {}, []
        self.driver(remote, calls)
        job = self.start()
        deadline = time.time() + 5
        while time.time() < deadline:
            job = drafts.workbench_job(self.root, job["id"])
            if job["done"]:
                break
            time.sleep(.01)
        self.assertTrue(job["done"], job)
        self.assertEqual(job["state"], "succeeded", job)
        self.assertEqual(job["backend"], "host_assisted_cua")
        self.assertEqual(job["result"]["remote_evidence_source"], "host_worker_response")
        self.assertEqual(calls.count("create_draft"), 1)
        self.assertEqual(remote["remote-1"]["body"], self.fixture.texts[1].split("\n", 1)[1])

    def test_lost_create_ack_is_unknown_and_never_creates_again(self):
        self.attach()
        remote, calls = {}, []
        self.driver(remote, calls, lose_create=True)
        with patch.dict(bridge.OP_SECONDS, create_draft=.2):
            with bridge.HostAdapter(self.root, self.binding(), self.config) as adapter:
                result = drafts.run_plan(self.book, self.plan["id"], adapter, execute=True)
        self.assertEqual(result["results"][0]["state"], "unknown", result)
        self.assertEqual(calls.count("create_draft"), 1)
        with bridge.HostAdapter(self.root, self.binding(), self.config) as adapter:
            result = drafts.run_plan(self.book, self.plan["id"], adapter, execute=True, reconcile_only=True)
        self.assertEqual(result["results"][0]["state"], "reconciled", result)
        self.assertEqual(calls.count("create_draft"), 1)

    def test_read_only_reconcile_does_not_create(self):
        self.attach()
        remote, calls = {}, []
        self.driver(remote, calls)
        with bridge.HostAdapter(self.root, self.binding(), self.config) as adapter:
            result = drafts.run_plan(self.book, self.plan["id"], adapter, execute=True, reconcile_only=True)
        self.assertEqual(result["results"][0]["state"], "not_found", result)
        self.assertNotIn("create_draft", calls)

    def test_saved_profile_change_invalidates_worker_and_preview(self):
        self.attach()
        preview = self.preview()
        changed = copy.deepcopy(self.config)
        changed["browser_channel"] = "chrome"
        self.path.write_text(json.dumps(changed))
        self.assert_code("platform_draft_profile_changed", self.start, preview)
        self.assertFalse(self.preview()["can_start"])

    def test_profile_code_is_never_a_transport_payload(self):
        config = copy.deepcopy(self.config)
        config["editor"]["body"]["javascript"] = "steal()"
        self.assert_code("platform_chrome_invalid_profile", bridge.validate_profile, config)

    def test_cli_attaches_claims_heartbeats_and_detaches_without_printing_token(self):
        def command(action, *args):
            result = subprocess.run([sys.executable, "-I", "-B", str(fixtures.TOOL),
                "publish-chrome-" + action, "--book", str(self.root), "--handle", str(self.handle), *args],
                capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout), result.stdout
        attached, output = command("attach", "--id", self.plan["id"], "--profile", self.path.name)
        token = json.loads(self.handle.read_text())["token"]
        self.assertNotIn(token.encode(), output)
        self.assertFalse(attached["browser_started"])
        self.assertIsNone(command("next")[0]["request_id"])
        self.assertFalse(command("heartbeat")[0]["detached"])
        self.assertTrue(command("detach")[0]["detached"])
        self.assertFalse(self.preview()["can_start"])

    def test_dead_owner_is_not_dispatched(self):
        self.attach()
        with patch.dict(bridge.OP_SECONDS, verify_target=.2):
            thread, _, _, claim, request = self.enqueue()
            # A separate expired outbox simulates a worker killed before claim.
            directory_path = Path(claim["request_path"]).parent
            request["id"] = "a" * 32
            request["owner_pid"] = 2147483646
            with bridge._directory(self.root, request["session_id"]) as directory:
                bridge._write(directory, "outbox-" + request["id"] + ".json", request)
            self.assertIsNone(bridge.next_request(self.book, self.handle)["request_id"])
            self.assertTrue((directory_path / ("outbox-" + request["id"] + ".json")).exists())
            thread.join(2)


if __name__ == "__main__":
    unittest.main()
