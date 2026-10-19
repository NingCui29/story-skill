"""Local bridge contract tests. Fake observations never contact a platform."""
import copy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

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
        self.handles = []
        self.request_threads = []
        self.driver_threads = []
        self.driver_failures = []
        self.async_tracebacks = []
        self.addCleanup(self.cleanup_jobs)

    def cleanup_jobs(self):
        # Detach this fixture's sessions so production waiters finish through
        # their real lease checks. Never delete or mark a running job complete.
        for stop, _ in self.driver_threads:
            stop.set()
        cleanup_failures = []
        for index, handle_data in enumerate(self.handles):
            # Tests may deliberately damage the original handle. Use the
            # private receipt captured at attach without repairing that input.
            handle = self.root / ("cleanup-handle-" + str(index) + ".json")
            try:
                bridge._external(handle, create_value=handle_data)
                bridge.heartbeat(self.book, handle, detach=True)
            except story.StoryError as error:
                if error.code != "platform_chrome_worker_unavailable":
                    cleanup_failures.append(traceback.format_exc())
            except BaseException:
                cleanup_failures.append(traceback.format_exc())
            finally:
                handle.unlink(missing_ok=True)
        for _, thread in self.driver_threads:
            thread.join(10)
        for thread in self.request_threads:
            thread.join(10)
        remaining = []
        for job_id in self.jobs:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not drafts.workbench_job(self.root, job_id)["done"]:
                time.sleep(.025)
            if not drafts.workbench_job(self.root, job_id)["done"]:
                remaining.append(job_id)
        with drafts._WORKBENCH_LOCK:
            for key, job in list(drafts._WORKBENCH_JOBS.items()):
                if job["_root"] == str(self.root) and job["done"]:
                    del drafts._WORKBENCH_JOBS[key]
        alive = [thread.name for thread in self.request_threads + [item[1] for item in self.driver_threads]
                 if thread.is_alive()]
        self.assertEqual((alive, remaining, self.driver_failures, cleanup_failures), ([], [], [], []),
                         "Background work did not finish cleanly:\n" + "\n".join(self.async_tracebacks + cleanup_failures))

    def assert_code(self, code, action, *args, **kwargs):
        return self.fixture.assert_code(code, action, *args, **kwargs)

    def attach(self, **kwargs):
        result = bridge.attach(self.book, self.plan["id"], self.path.name, self.handle, **kwargs)
        self.handles.append(json.loads(self.handle.read_text()))
        return result

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
                self.async_tracebacks.append(traceback.format_exc())
        thread = threading.Thread(target=call)
        self.request_threads.append(thread)
        thread.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if errors:
                self.fail("Request worker failed before claim:\n" + "\n".join(self.async_tracebacks))
            claim = bridge.next_request(self.book, self.handle)
            if claim["request_id"]:
                return thread, result, errors, claim, json.loads(Path(claim["request_path"]).read_text())
            time.sleep(.01)
        self.fail("Request was not queued:\n" + "\n".join(self.async_tracebacks))

    def driver(self, remote, calls, *, lose_create=False):
        stop, ready = threading.Event(), threading.Event()
        def work():
            book = None
            try:
                book = story.Book(self.root)
                ready.set()
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
                if (stop.is_set() and isinstance(error, story.StoryError) and
                        error.code == "platform_chrome_worker_unavailable"):
                    return
                evidence = traceback.format_exc()
                self.driver_failures.append(evidence)
                self.async_tracebacks.append(evidence)
            finally:
                ready.set()
                if book is not None:
                    book.close()
        thread = threading.Thread(target=work)
        self.driver_threads.append((stop, thread))
        thread.start()
        self.assertTrue(ready.wait(10), "Host driver did not initialize")
        self.assertEqual(self.driver_failures, [], "\n".join(self.driver_failures))
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
        self.assertTrue(self.handle.is_file())
        directory = self.root / bridge.DIRECTORY / result["session_id"]
        self.assertTrue(directory.is_dir())
        if os.name != "nt":
            self.assertEqual(os.stat(self.handle).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(directory).st_mode & 0o777, 0o700)
        for name in ("session.json", "lease.json"):
            self.assertTrue((directory / name).is_file())
            self.assertEqual(json.loads((directory / name).read_text())["schema"], 1)
            if os.name != "nt":
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
        self.assertTrue(Path(claim["request_path"]).is_file())
        if os.name != "nt":
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
        with patch.dict(bridge.OP_SECONDS, verify_target=3):
            thread, _, errors, claim, request = self.enqueue()
            bridge.heartbeat(self.book, self.handle, detach=True)
            thread.join(5)
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
        with patch.dict(bridge.OP_SECONDS, verify_target=3):
            thread, _, errors, claim, request = self.enqueue()
            thread.join(5)
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
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.driver_failures:
                self.fail("Host driver failed:\n" + "\n".join(self.driver_failures))
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
        with patch.dict(bridge.OP_SECONDS, create_draft=3):
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

    def test_cross_process_sender_and_host_exchange_five_authenticated_requests(self):
        self.attach()
        ready = self.root / "sender-ready"
        sender = """
import importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('bridge_sender_story',sys.argv[1])
story=importlib.util.module_from_spec(spec);spec.loader.exec_module(story)
book=story.Book(sys.argv[2])
try:
    bridge=story.platform_chrome_bridge
    bridge.OP_SECONDS['verify_target']=5
    binding,profile=bridge._binding(book,sys.argv[3],sys.argv[4])
    with bridge.HostAdapter(book.root,binding,profile) as adapter:
        Path(sys.argv[5]).write_text('ready',encoding='utf-8')
        results=[adapter._request('verify_target',{}) for _ in range(5)]
    assert all(result==binding['target'] for result in results)
    print(json.dumps({'ok':True,'requests':len(results)}),flush=True)
finally:
    book.close()
"""
        failure, cleanup_failures, request_ids = None, [], []
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output, \
                tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
            process = subprocess.Popen([sys.executable, "-I", "-B", "-c", sender,
                str(fixtures.TOOL), str(self.root), self.plan["id"], self.path.name, str(ready)],
                stdout=output, stderr=errors)
            deadline = time.monotonic() + 20
            try:
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(ready.is_file(), "Sender did not initialize its independent Book and HostAdapter")
                while len(request_ids) < 5 and process.poll() is None and time.monotonic() < deadline:
                    claim = bridge.next_request(self.book, self.handle)
                    if claim["request_id"] is None:
                        time.sleep(.01)
                        continue
                    request = json.loads(Path(claim["request_path"]).read_text())
                    self.assertTrue(claim["claimed_once"])
                    self.assertEqual(request["operation"], "verify_target")
                    self.assertEqual(request["params"], {})
                    self.assertNotIn(claim["request_id"], request_ids)
                    request_ids.append(claim["request_id"])
                    self.write_response(request, request["binding"]["target"])
                    bridge.respond(self.book, self.handle, claim["request_id"], self.response)
                self.assertEqual(len(request_ids), 5, "Cross-process request exchange did not finish")
                process.wait(timeout=max(.01, deadline - time.monotonic()))
            except BaseException:
                failure = traceback.format_exc()
            finally:
                try:
                    bridge.heartbeat(self.book, self.handle, detach=True)
                except story.StoryError as error:
                    if error.code != "platform_chrome_worker_unavailable":
                        cleanup_failures.append(traceback.format_exc())
                except BaseException:
                    cleanup_failures.append(traceback.format_exc())
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=3)
                output.seek(0); stdout = output.read()
                errors.seek(0); stderr = errors.read()
        diagnostic = "\n".join([failure or "", *cleanup_failures,
                                "sender stdout:\n" + stdout, "sender stderr:\n" + stderr])
        self.assertIsNone(failure, diagnostic)
        self.assertEqual(cleanup_failures, [], diagnostic)
        self.assertEqual(process.returncode, 0, diagnostic)
        self.assertEqual(json.loads(stdout), {"ok": True, "requests": 5}, diagnostic)
        self.assertEqual(len(set(request_ids)), 5)

    def test_dead_owner_is_not_dispatched(self):
        self.attach()
        with patch.dict(bridge.OP_SECONDS, verify_target=3):
            thread, _, _, claim, request = self.enqueue()
            # A separate expired outbox simulates a worker killed before claim.
            directory_path = Path(claim["request_path"]).parent
            request["id"] = "a" * 32
            request["owner_pid"] = 2147483646
            with bridge._directory(self.root, request["session_id"]) as directory:
                bridge._write(directory, "outbox-" + request["id"] + ".json", request)
            self.assertIsNone(bridge.next_request(self.book, self.handle)["request_id"])
            self.assertTrue((directory_path / ("outbox-" + request["id"] + ".json")).exists())
            thread.join(5)

    def test_external_and_session_directory_pins_never_overlap_between_threads(self):
        attached = self.attach()
        original = bridge.api._pinned_directory
        ownership = threading.Lock()
        active = {}
        failures = []
        start = threading.Barrier(2)

        @contextmanager
        def exclusive_pin(path, **kwargs):
            owner = threading.get_ident()
            with ownership:
                if any(other != owner for other in active):
                    raise PermissionError("simulated Windows strict-pin sharing violation")
                active[owner] = active.get(owner, 0) + 1
            try:
                with original(path, **kwargs) as directory:
                    # Widen the real traversal/write overlap deterministically.
                    time.sleep(.005)
                    yield directory
            finally:
                with ownership:
                    active[owner] -= 1
                    if not active[owner]:
                        del active[owner]

        def read_external():
            try:
                start.wait(timeout=5)
                for _ in range(10):
                    self.assertEqual(bridge._external(self.handle)["session_id"], attached["session_id"])
            except BaseException:
                failures.append(traceback.format_exc())

        def read_session():
            try:
                start.wait(timeout=5)
                for _ in range(10):
                    with bridge._directory(self.root, attached["session_id"]) as directory, bridge._lock(directory):
                        self.assertEqual(bridge._session(directory)["session_id"], attached["session_id"])
                        bridge._lease(directory, bridge._session(directory), extend=True)
            except BaseException:
                failures.append(traceback.format_exc())

        with patch.object(bridge.api, "_pinned_directory", side_effect=exclusive_pin):
            threads = [threading.Thread(target=read_external), threading.Thread(target=read_session)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
            self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(failures, [], "\n".join(failures))


class WindowsProcessLivenessTests(unittest.TestCase):
    def kernel(self, wait=0x102, handle=123):
        return SimpleNamespace(OpenProcess=Mock(return_value=handle),
                               WaitForSingleObject=Mock(return_value=wait),
                               CloseHandle=Mock(return_value=1))

    def test_live_and_exited_processes_use_zero_wait_and_close_handles(self):
        import ctypes
        from ctypes import wintypes
        for wait, expected in ((0x102, True), (0, False), (0xffffffff, False), (7, False)):
            with self.subTest(wait=wait):
                kernel = self.kernel(wait=wait)
                with patch.object(ctypes, "WinDLL", return_value=kernel, create=True):
                    self.assertEqual(bridge._windows_process_alive(1234), expected)
                kernel.OpenProcess.assert_called_once_with(0x00100000, False, 1234)
                kernel.WaitForSingleObject.assert_called_once_with(123, 0)
                kernel.CloseHandle.assert_called_once_with(123)
                self.assertEqual(kernel.OpenProcess.argtypes, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD])
                self.assertIs(kernel.OpenProcess.restype, wintypes.HANDLE)
                self.assertEqual(kernel.WaitForSingleObject.argtypes, [wintypes.HANDLE, wintypes.DWORD])
                self.assertIs(kernel.WaitForSingleObject.restype, wintypes.DWORD)
                self.assertEqual(kernel.CloseHandle.argtypes, [wintypes.HANDLE])
                self.assertIs(kernel.CloseHandle.restype, wintypes.BOOL)

    def test_invalid_pid_and_access_denied_fail_closed_without_waiting(self):
        import ctypes
        with patch.object(ctypes, "WinDLL", create=True) as load:
            for pid in (None, True, "123", 0, -1, 0x100000000):
                self.assertFalse(bridge._windows_process_alive(pid))
            load.assert_not_called()
        kernel = self.kernel(handle=0)
        with patch.object(ctypes, "WinDLL", return_value=kernel, create=True):
            self.assertFalse(bridge._windows_process_alive(1234))
        kernel.WaitForSingleObject.assert_not_called()
        kernel.CloseHandle.assert_not_called()

    def test_api_failure_closes_open_handle_and_never_assumes_liveness(self):
        import ctypes
        kernel = self.kernel()
        kernel.WaitForSingleObject.side_effect = OSError("query denied")
        with patch.object(ctypes, "WinDLL", return_value=kernel, create=True):
            self.assertFalse(bridge._windows_process_alive(1234))
        kernel.CloseHandle.assert_called_once_with(123)
        with patch.object(ctypes, "WinDLL", side_effect=OSError("unavailable"), create=True):
            self.assertFalse(bridge._windows_process_alive(1234))

    def test_windows_owner_check_never_uses_os_kill(self):
        platform = SimpleNamespace(name="nt", kill=Mock(side_effect=AssertionError("must not signal")))
        for alive in (True, False):
            with patch.object(bridge, "os", platform), patch.object(bridge, "_windows_process_alive", return_value=alive) as query:
                self.assertEqual(bridge._owner_alive("unused", {"owner_pid": 1234}), alive)
                query.assert_called_once_with(1234)
        platform.kill.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "requires a real Windows process handle")
    def test_native_windows_current_and_exited_child_without_signals(self):
        child = subprocess.Popen([sys.executable, "-c",
            "import sys; print('ready', flush=True); sys.stdin.buffer.read(1)"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            self.assertEqual(child.stdout.readline().strip(), b"ready")
            with patch.object(os, "kill", side_effect=AssertionError("must not signal")):
                self.assertTrue(bridge._owner_alive("unused", {"owner_pid": os.getpid()}))
                self.assertTrue(bridge._owner_alive("unused", {"owner_pid": child.pid}))
                self.assertFalse(bridge._owner_alive("unused", {"owner_pid": 2147483646}))
            child.stdin.write(b"x"); child.stdin.flush(); child.stdin.close()
            child.wait(timeout=10)
            with patch.object(os, "kill", side_effect=AssertionError("must not signal")):
                self.assertFalse(bridge._owner_alive("unused", {"owner_pid": child.pid}))
        finally:
            if child.stdin and not child.stdin.closed:
                child.stdin.close()
            child.wait(timeout=10)
            child.stdout.close()


class BridgeRenameSharingRetryTests(unittest.TestCase):
    """Only local, OS-rejected renames may retry; queue identities stay fixed."""

    def setUp(self):
        self.directory = SimpleNamespace(path=Path("bridge-rename-fixture"), fd=71)
        self.source = SimpleNamespace(directory=self.directory, name="source.json",
                                      path=self.directory.path / "source.json")
        self.target = SimpleNamespace(directory=self.directory, name="target.json",
                                      path=self.directory.path / "target.json")
        self.source_stat = self.stat(11)
        self.target_stat = None
        self.elapsed = 0.0
        self.sleeps = []
        self.publish = Mock()
        self.replace = Mock()
        self.guard = Mock()

    @staticmethod
    def stat(inode, *, size=23, mtime=31):
        return SimpleNamespace(st_dev=7, st_ino=inode, st_size=size,
                               st_mtime_ns=mtime, st_mode=0o100600, st_nlink=1)

    @staticmethod
    def sharing_error(code=32):
        error = PermissionError("injected native sharing refusal")
        error.winerror = code
        return error

    def stat_file(self, file, *, missing=False):
        if file is self.source:
            if self.source_stat is None and not missing:
                raise FileNotFoundError("source disappeared")
            return self.source_stat
        if file is self.target:
            if self.target_stat is None and not missing:
                raise FileNotFoundError("target absent")
            return self.target_stat
        self.fail("Unexpected bound file")

    def sleep(self, seconds):
        self.assertGreater(seconds, 0)
        self.sleeps.append(seconds)
        self.elapsed += seconds

    @contextmanager
    def environment(self, platform="nt"):
        native = SimpleNamespace(name=platform, path=os.path, replace=self.replace,
                                 rename=Mock(side_effect=AssertionError("must retain atomic publication")))
        clock = SimpleNamespace(monotonic=lambda: self.elapsed, sleep=self.sleep,
                                time=lambda: 1000)
        with patch.object(bridge, "os", native), patch.object(bridge, "time", clock), \
                patch.object(bridge.api.publish, "_bound_stat", side_effect=self.stat_file), \
                patch.object(bridge.api, "_verify_bound_directory", self.guard), \
                patch.object(bridge.api.platform_drafts, "_atomic_publish", self.publish):
            yield

    def test_local_sharing_refusals_retry_both_publish_and_replace(self):
        for replace in (False, True):
            for code in (32, 33):
                with self.subTest(replace=replace, winerror=code):
                    operation = self.replace if replace else self.publish
                    operation.reset_mock()
                    operation.side_effect = [self.sharing_error(code), None]
                    self.elapsed, self.sleeps = 0.0, []
                    with self.environment():
                        bridge._rename(self.source, self.target, replace=replace)
                    self.assertEqual(operation.call_count, 2)
                    self.assertTrue(self.sleeps)
                    self.assertLessEqual(self.elapsed, 1.0)
                    self.assertGreaterEqual(self.guard.call_count, 2)

    def test_sharing_refusal_has_bounded_budget_and_preserves_last_error(self):
        error = self.sharing_error()
        self.publish.side_effect = error
        with self.environment(), self.assertRaises(PermissionError) as raised:
            bridge._rename(self.source, self.target)
        self.assertIs(raised.exception, error)
        self.assertGreater(self.publish.call_count, 1)
        self.assertGreaterEqual(self.elapsed, .99)
        self.assertLessEqual(self.elapsed, 1.01)

    def test_nonsharing_and_posix_errors_never_retry(self):
        for platform, code in (("nt", 5), ("nt", None), ("posix", 32)):
            with self.subTest(platform=platform, winerror=code):
                error = PermissionError("not retryable")
                if code is not None:
                    error.winerror = code
                self.publish.reset_mock()
                self.publish.side_effect = error
                self.elapsed, self.sleeps = 0.0, []
                with self.environment(platform), self.assertRaises(PermissionError) as raised:
                    bridge._rename(self.source, self.target)
                self.assertIs(raised.exception, error)
                self.publish.assert_called_once_with(self.source, self.target)
                self.assertEqual(self.sleeps, [])

    def test_source_identity_size_mtime_or_disappearance_stops_retry(self):
        for changed in (self.stat(12), self.stat(11, size=24), self.stat(11, mtime=32), None):
            with self.subTest(changed=changed):
                self.source_stat = self.stat(11)
                self.publish.reset_mock()
                def refuse_then_change(*args):
                    self.source_stat = changed
                    raise self.sharing_error()
                self.publish.side_effect = refuse_then_change
                with self.environment(), self.assertRaises((story.StoryError, OSError)):
                    bridge._rename(self.source, self.target)
                self.publish.assert_called_once_with(self.source, self.target)

    def test_new_destination_never_gets_overwritten_after_sharing_refusal(self):
        for replace in (False, True):
            with self.subTest(replace=replace):
                self.target_stat = None
                operation = self.replace if replace else self.publish
                operation.reset_mock()
                def refuse_then_create(*args):
                    self.target_stat = self.stat(22)
                    raise self.sharing_error()
                operation.side_effect = refuse_then_create
                with self.environment(), self.assertRaises((story.StoryError, OSError)):
                    bridge._rename(self.source, self.target, replace=replace)
                self.assertEqual(operation.call_count, 1)

    def test_replacement_destination_identity_or_contents_change_stops_retry(self):
        for changed in (self.stat(23), self.stat(22, size=24), self.stat(22, mtime=32), None):
            with self.subTest(changed=changed):
                self.target_stat = self.stat(22)
                self.replace.reset_mock()
                def refuse_then_change(*args):
                    self.target_stat = changed
                    raise self.sharing_error()
                self.replace.side_effect = refuse_then_change
                with self.environment(), self.assertRaises((story.StoryError, OSError)):
                    bridge._rename(self.source, self.target, replace=True)
                self.assertEqual(self.replace.call_count, 1)

    def test_native_directory_guard_failure_stops_retry(self):
        def verify(directory):
            if self.publish.call_count:
                raise OSError("directory binding changed")
        self.guard.side_effect = verify
        self.publish.side_effect = self.sharing_error()
        with self.environment(), self.assertRaises((story.StoryError, OSError)):
            bridge._rename(self.source, self.target)
        self.publish.assert_called_once_with(self.source, self.target)

    def test_successful_retried_claim_is_never_dispatched_twice(self):
        request_id = "a" * 32
        self.source.name = "outbox-" + request_id + ".json"
        self.source.path = self.directory.path / self.source.name
        self.target.name = "claimed-" + request_id + ".json"
        self.target.path = self.directory.path / self.target.name
        names = [self.source.name]
        session = {"binding_sha256": "bound", "binding": {"target": "fixture"}, "session_id": "b" * 32}
        request = {"schema": 1, "id": request_id, "nonce": "n" * 32,
                   "binding_sha256": session["binding_sha256"], "binding": session["binding"],
                   "session_id": session["session_id"], "operation": "verify_target",
                   "deadline_epoch": 1100, "owner_pid": 123}
        @contextmanager
        def authenticated(*args):
            yield self.directory, session
        def publish(source, target):
            if self.publish.call_count == 1:
                raise self.sharing_error()
            names[:] = [target.name]
            self.target_stat, self.source_stat = self.source_stat, None
        self.publish.side_effect = publish
        def bound_file(directory, name):
            return self.source if name == self.source.name else self.target
        with self.environment(), patch.object(bridge.os, "listdir", side_effect=lambda path: list(names), create=True), \
                patch.object(bridge, "_authenticated", authenticated), \
                patch.object(bridge, "_read", return_value=request), \
                patch.object(bridge, "_owner_alive", return_value=True), \
                patch.object(bridge, "_lease") as lease, \
                patch.object(bridge.api, "_BoundFile", side_effect=bound_file):
            book = SimpleNamespace(root=self.directory.path)
            first = bridge.next_request(book, "private-handle")
            second = bridge.next_request(book, "private-handle")
        self.assertEqual(first["request_id"], request_id)
        self.assertTrue(first["claimed_once"])
        self.assertIsNone(second["request_id"])
        self.assertFalse(second["claimed_once"])
        self.assertEqual(self.publish.call_count, 2)
        lease.assert_called_once()


if __name__ == "__main__":
    unittest.main()
