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

    def attach(self, *, healthy=True, **kwargs):
        result = bridge.attach(self.book, self.plan["id"], self.path.name, self.handle, **kwargs)
        self.handles.append(json.loads(self.handle.read_text()))
        if healthy:
            self.health()
        return result

    def health(self, **changes):
        challenge = bridge.health(self.book, self.handle)["challenge"]
        packet = {key: challenge[key] for key in ("schema", "session_id", "binding_sha256", "nonce")}
        packet.update(browser={"kind": "chrome", "id": "fixture-connected-chrome"},
                      capabilities={"list_tabs": True, "get_tab": True, "dom_snapshot": True},
                      worker={"pid": os.getpid(), "instance": "a" * 32})
        packet.update(changes)
        self.response.write_text(json.dumps(packet), encoding="utf-8")
        self.response.chmod(0o600)
        return bridge.health(self.book, self.handle, self.response)

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

    def test_allocated_id_is_only_accepted_on_failed_creation(self):
        request = {"schema": 1, "id": "r", "nonce": "n", "session_id": "s", "binding_sha256": "b", "operation": "create_draft"}
        response = {key: request[key] for key in ("schema", "id", "nonce", "session_id", "binding_sha256")}
        response.update(ok=False, error="platform_browser_incomplete_list", allocated_remote_id="7700440421876367934")
        bridge._validate_response(response, request)
        for operation in ("find_drafts", "read_draft", "update_draft"):
            self.assert_code("platform_chrome_invalid_message", bridge._validate_response, response, {**request, "operation": operation})
        success = {key: value for key, value in response.items() if key != "error"}
        success.update(ok=True, result="7700440421876367934")
        self.assert_code("platform_chrome_invalid_message", bridge._validate_response, success, request)

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

    def test_comparison_policy_is_bound_to_authenticated_session_and_health(self):
        strict = self.binding()
        equivalent, _ = bridge._binding(self.book, self.plan["id"], self.path.name,
                                       comparison_policy="fanqie_terminal_lf_v1")
        self.assertEqual(strict["comparison_policy"], "strict")
        self.assertEqual(equivalent["comparison_policy"], "fanqie_terminal_lf_v1")
        self.assertNotEqual(bridge._digest(strict), bridge._digest(equivalent))
        result = self.attach(comparison_policy="fanqie_terminal_lf_v1")
        session = json.loads((self.root / bridge.DIRECTORY / result["session_id"] / "session.json").read_text())
        self.assertEqual(session["binding"], equivalent)
        self.assertTrue(bridge.worker_status(self.root, equivalent)["ready"])
        self.assertFalse(bridge.worker_status(self.root, strict)["ready"])
        self.assertTrue(self.preview(comparison_policy="fanqie_terminal_lf_v1")["can_start"])
        self.assertFalse(self.preview()["can_start"])

    def test_invalid_or_foreign_comparison_policy_never_attaches(self):
        for policy in (None, True, "trim", "fanqie_terminal_lf_v2"):
            self.assert_code("platform_draft_invalid_comparison_policy", bridge.attach,
                self.book, self.plan["id"], self.path.name, self.handle, comparison_policy=policy)
        self.assertFalse(self.handle.exists())
        binding = self.binding()
        binding["comparison_policy"] = "fanqie_terminal_lf_v1"
        binding["target"]["platform"] = "qimao"
        self.assert_code("platform_draft_invalid_comparison_policy", bridge.HostAdapter,
                         self.root, binding, self.config)

    def test_attach_cli_defaults_strict_and_passes_explicit_policy(self):
        import argparse
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        bridge.register_parser(sub, lambda name, *args, **kwargs: sub.add_parser(name))
        argv = ["publish-chrome-attach", "--id", "plan", "--profile", "profile.json", "--handle", "private.json"]
        for tail, policy, encoding in (([], "strict", "verbatim"),
                (["--comparison-policy", "fanqie_terminal_lf_v1"], "fanqie_terminal_lf_v1", "verbatim"),
                (["--body-encoding", "markdown_paragraphs_v1"], "strict", "markdown_paragraphs_v1"),
                (["--body-encoding", "markdown_paragraphs_v2"], "strict", "markdown_paragraphs_v2")):
            args = parser.parse_args(argv + tail)
            with patch.object(bridge, "attach", return_value={"ok": True}) as attach:
                bridge.run(self.book, args)
                attach.assert_called_once_with(self.book, "plan", "profile.json", "private.json", bridge.TTL_SECONDS,
                                               comparison_policy=policy, body_encoding=encoding)

    def test_body_encoding_is_bound_independently_of_comparison_and_authenticated_health(self):
        self.fixture.commit(text=self.fixture.texts[1].replace("\n守门人", "\n\n守门人"), replace_last=True)
        self.plan = self.fixture.prepare()
        verbatim = self.binding()
        encoded, _ = bridge._binding(self.book, self.plan["id"], self.path.name,
                                    body_encoding="markdown_paragraphs_v1")
        upgraded, _ = bridge._binding(self.book, self.plan["id"], self.path.name,
                                     body_encoding="markdown_paragraphs_v2")
        self.assertEqual(verbatim["body_encoding"], "verbatim")
        self.assertEqual(encoded["body_encoding"], "markdown_paragraphs_v1")
        self.assertEqual(encoded["comparison_policy"], "strict")
        self.assertNotEqual(bridge._digest(verbatim), bridge._digest(encoded))
        self.assertEqual(upgraded["body_encoding"], "markdown_paragraphs_v2")
        self.assertNotEqual(bridge._digest(encoded), bridge._digest(upgraded))
        result = self.attach(body_encoding="markdown_paragraphs_v2")
        session = json.loads((self.root / bridge.DIRECTORY / result["session_id"] / "session.json").read_text())
        self.assertEqual(session["binding"], upgraded)
        self.assertTrue(bridge.worker_status(self.root, upgraded)["ready"])
        self.assertFalse(bridge.worker_status(self.root, encoded)["ready"])
        self.assertFalse(bridge.worker_status(self.root, verbatim)["ready"])
        self.assertTrue(self.preview(body_encoding="markdown_paragraphs_v2")["can_start"])

    def test_invalid_or_foreign_body_encoding_never_attaches(self):
        for encoding in (None, True, "trim", "markdown_paragraphs_v3"):
            self.assert_code("platform_draft_invalid_body_encoding", bridge.attach,
                self.book, self.plan["id"], self.path.name, self.handle, body_encoding=encoding)
        self.assertFalse(self.handle.exists())
        binding = self.binding()
        binding["body_encoding"] = "markdown_paragraphs_v2"
        binding["target"]["platform"] = "qimao"
        self.assert_code("platform_draft_invalid_body_encoding", bridge.HostAdapter,
                         self.root, binding, self.config)

    def test_obsolete_fanqie_host_profile_cannot_open_editor_for_reconciliation(self):
        obsolete = {**profile("fanqie"), "execution_mode": "host_assisted_cua"}
        self.path.write_text(json.dumps(obsolete))
        self.assert_code("platform_browser_readback_required", self.preview, reconcile_only=True)
        self.assert_code("platform_browser_readback_required", bridge.HostAdapter,
                         self.root, {}, obsolete)

    def test_attach_requires_real_probe_acknowledgement_before_local_preview_can_start(self):
        result = self.attach(healthy=False)
        self.assertNotIn("token", result)
        self.assertFalse(result["browser_started"])
        self.assertEqual((result["state"], result["ready"]), ("awaiting_health", False))
        self.assertFalse(bridge.worker_status(self.root, self.binding())["ready"])
        self.assertFalse(self.preview()["can_start"])
        self.assert_code("platform_chrome_worker_unavailable", bridge.next_request, self.book, self.handle)
        self.assert_code("platform_chrome_worker_unavailable",
                         bridge.HostAdapter(self.root, self.binding(), self.config).__enter__)
        self.assertTrue(self.health()["ready"])
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

    def test_probe_nonce_is_single_use_and_does_not_claim_platform_identity(self):
        self.attach(healthy=False)
        result = self.health()
        self.assertTrue(result["ready"])
        self.assertFalse(result["remote_accessed"])
        self.assertFalse(result["platform_target_verified"])
        self.assert_code("platform_chrome_health_expired", bridge.health, self.book, self.handle, self.response)
        status = bridge.worker_status(self.root, self.binding())
        self.assertEqual((status["browser_id"], status["worker_pid"], status["worker_instance"]),
                         ("fixture-connected-chrome", os.getpid(), "a" * 32))

    def test_new_challenge_supersedes_old_probe_and_expired_challenge_cannot_connect(self):
        self.attach(healthy=False)
        first = bridge.health(self.book, self.handle)["challenge"]
        bridge.health(self.book, self.handle)
        packet = {key: first[key] for key in ("schema", "session_id", "binding_sha256", "nonce")}
        packet.update(browser={"kind": "chrome", "id": "fixture-connected-chrome"},
                      capabilities={"list_tabs": True, "get_tab": True, "dom_snapshot": True},
                      worker={"pid": os.getpid(), "instance": "a" * 32})
        self.response.write_text(json.dumps(packet), encoding="utf-8"); self.response.chmod(0o600)
        self.assert_code("platform_chrome_invalid_health", bridge.health, self.book, self.handle, self.response)
        now = time.time()
        with patch.object(bridge.time, "time", return_value=now + 31):
            self.assert_code("platform_chrome_health_expired", bridge.health, self.book, self.handle, self.response)
        self.assertFalse(bridge.worker_status(self.root, self.binding())["ready"])

    def test_wrong_binding_nonce_schema_and_capability_fail_without_health(self):
        self.attach(healthy=False)
        for changes in ({"nonce": "wrong"}, {"binding_sha256": "0" * 64}, {"schema": True},
                        {"capabilities": {"list_tabs": 1, "get_tab": True, "dom_snapshot": True}},
                        {"capabilities": {"list_tabs": True, "get_tab": True}},
                        {"capabilities": {"list_tabs": True, "get_tab": True, "dom_snapshot": False}},
                        {"browser": {"kind": "iab", "id": "fixture"}},
                        {"worker": {"pid": True, "instance": "a" * 32}}):
            self.assert_code("platform_chrome_invalid_health", self.health, **changes)
            self.assertFalse(bridge.worker_status(self.root, self.binding())["ready"])

    def test_browser_and_worker_are_pinned_until_new_attachment(self):
        self.attach()
        for changes in ({"browser": {"kind": "chrome", "id": "another-browser"}},
                        {"worker": {"pid": os.getpid(), "instance": "b" * 32}},
                        {"worker": {"pid": os.getpid() + 1, "instance": "a" * 32}}):
            self.assert_code("platform_chrome_worker_changed", self.health, **changes)
        self.assertTrue(bridge.worker_status(self.root, self.binding())["ready"])

    def test_dead_process_cannot_connect_or_remain_ready(self):
        self.attach(healthy=False)
        with patch.object(bridge, "_owner_alive", return_value=False):
            self.assert_code("platform_chrome_worker_unavailable", self.health)
        self.health()
        with patch.object(bridge, "_owner_alive", return_value=False):
            status = bridge.worker_status(self.root, self.binding())
            self.assertEqual((status["ready"], status["health_state"]), (False, "worker_exited"))

    def test_lease_heartbeat_and_queue_poll_cannot_extend_browser_health(self):
        self.attach()
        now = time.time()
        with patch.object(bridge.time, "time", return_value=now + 46):
            bridge.heartbeat(self.book, self.handle)
            status = bridge.worker_status(self.root, self.binding())
            self.assertTrue(status["attached"])
            self.assertFalse(status["ready"])
            self.assertEqual(status["health_state"], "expired")
            self.assert_code("platform_chrome_worker_unavailable", bridge.next_request, self.book, self.handle)
            self.assertFalse(self.preview()["can_start"])

    def test_health_expiry_keeps_claimed_response_and_blocks_the_next_operation(self):
        self.attach()
        thread, result, errors, claim, request = self.enqueue()
        with patch.object(bridge.time, "time", return_value=time.time() + 46):
            self.write_response(request, request["binding"]["target"])
            bridge.respond(self.book, self.handle, claim["request_id"], self.response)
            thread.join(2)
            self.assertEqual((result, errors), ([request["binding"]["target"]], []))
            with self.assertRaises(story.StoryError) as caught:
                adapter = bridge.HostAdapter(self.root, self.binding(), self.config)
                adapter.session_id = request["session_id"]
                adapter.verify_target(**request["binding"]["target"])
            self.assertEqual(caught.exception.code, "platform_chrome_worker_unavailable")

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

    def test_restored_incomplete_or_invalid_sessions_are_opaque_and_unmodified(self):
        preserved = {}
        for name, content in (("c" * 32, None), ("d" * 32, {"invalid": "restored metadata"})):
            with bridge._directory(self.root, name, create=True) as directory:
                bridge._write(directory, bridge.RESTORED_EVIDENCE, {"retained": True})
                if content is not None:
                    bridge._write(directory, "session.json", content)
            queue = self.root / bridge.DIRECTORY / name
            preserved.update({path: path.read_bytes() for path in queue.iterdir() if path.is_file()})
        status = bridge.worker_status(self.root, self.binding())
        self.assertFalse(status["attached"])
        self.assertEqual(status["active_sessions"], 0)
        self.assertEqual({path: path.read_bytes() for path in preserved}, preserved)
        self.assertEqual({path for path in (self.root / bridge.DIRECTORY).rglob('*') if path.is_file()}, set(preserved))
        self.attach()
        self.assertTrue(bridge.worker_status(self.root, self.binding())["ready"])

    def test_invalid_session_without_restore_marker_is_still_rejected(self):
        with bridge._directory(self.root, "c" * 32, create=True) as directory:
            bridge._write(directory, "session.json", {"invalid": "active metadata"})
        self.assert_code("platform_chrome_invalid_message", bridge.worker_status, self.root, self.binding())

    def test_linked_restore_marker_cannot_hide_invalid_session(self):
        with bridge._directory(self.root, "c" * 32, create=True) as directory:
            bridge._write(directory, "session.json", {"invalid": "active metadata"})
            marker = directory.path / bridge.RESTORED_EVIDENCE
            os.link(directory.path / "session.json", marker)
        self.assert_code("unsafe_publish_path", bridge.worker_status, self.root, self.binding())

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

    def test_host_profile_rejects_identity_sources_only_supported_by_standalone_before_attach(self):
        from tests.test_platform_browser import BrowserTests, field
        for location in ("account", "book", "remote_id"):
            with self.subTest(location=location):
                config = BrowserTests().url_guard_profile("qimao")
                config["editor"]["ready"] = field("#save", expected="保存草稿", enabled=True)
                if location == "remote_id":
                    config["editor"][location] = field("#remote-id", attribute="data-id")
                else:
                    config["identity"][location] = field("#" + location)
                self.assertEqual(bridge.validate_profile(config), "playwright")
                config["execution_mode"] = "host_assisted_cua"
                self.assert_code("platform_chrome_invalid_profile", bridge.validate_profile, config)
        self.assertEqual(bridge.validate_profile(self.config), "host_assisted_cua")
        unsupported = copy.deepcopy(self.config)
        unsupported["identity"]["account"] = field("#account", attribute="data-id")
        self.path.write_text(json.dumps(unsupported), encoding="utf-8")
        self.assert_code("platform_chrome_invalid_profile", bridge.attach,
                         self.book, self.plan["id"], self.path.name, self.handle)
        self.assertFalse(self.handle.exists())
        self.assertFalse((self.root / bridge.DIRECTORY).exists())

    def test_host_required_editor_and_row_contracts_reject_unsupported_profiles_statically(self):
        from tests.test_platform_browser import field
        base = copy.deepcopy(self.config)
        base["editor"]["ready"] = field("#save", expected="保存草稿", enabled=True)
        for index, change in enumerate((
            lambda config: config["editor"].pop("ready"),
            lambda config: config["editor"].update(ready=None),
            lambda config: config["chapter_lists"][0]["chapter"].update(source="page_url"),
            lambda config: config["chapter_lists"][0]["link"].update(source="prosemirror_paragraphs"),
            lambda config: config["readback"]["header"].update(title_separator=""),
        )):
            with self.subTest(index=index):
                config = copy.deepcopy(base)
                change(config)
                standalone = {**config, "execution_mode": "playwright"}
                self.assertEqual(bridge.validate_profile(standalone), "playwright")
                self.assert_code("platform_chrome_invalid_profile", bridge.validate_profile, config)

    def test_host_management_tab_mapping_is_checked_before_connecting(self):
        base = story.platform_browser.fanqie_profile_from_book_url(
            "https://fanqienovel.com/main/writer/chapter-manage/100&test?type=2")["profile"]
        self.assertEqual(bridge.validate_profile(base), "host_assisted_cua")
        for index, change in enumerate((
            lambda config: config["chapter_lists"][0]["coverage"].update(expected="其他页面"),
            lambda config: config["chapter_lists"][0]["coverage"].update(attribute="title"),
            lambda config: config["chapter_lists"][0]["coverage"].update(source=None),
            lambda config: config["chapter_lists"].append(copy.deepcopy(config["chapter_lists"][0])),
        )):
            with self.subTest(index=index):
                config = copy.deepcopy(base)
                change(config)
                self.assertEqual(bridge.validate_profile({**config, "execution_mode": "playwright"}), "playwright")
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
        challenge = command("health")[0]["challenge"]
        packet = {key: challenge[key] for key in ("schema", "session_id", "binding_sha256", "nonce")}
        packet.update(browser={"kind": "chrome", "id": "fixture-cli-chrome"},
                      capabilities={"list_tabs": True, "get_tab": True, "dom_snapshot": True},
                      worker={"pid": os.getpid(), "instance": "c" * 32})
        self.response.write_text(json.dumps(packet), encoding="utf-8")
        self.response.chmod(0o600)
        self.assertTrue(command("health", "--input", str(self.response))[0]["ready"])
        self.assertIsNone(command("next")[0]["request_id"])
        self.assertFalse(command("heartbeat")[0]["detached"])
        self.assertTrue(command("detach")[0]["detached"])
        self.assertFalse(self.preview()["can_start"])

    def test_cross_process_sender_and_host_exchange_five_authenticated_requests(self):
        self.attach()
        ready = self.root / "sender-ready"
        request_count, operation_budget, startup_and_cleanup_budget = 5, 15, 20
        sender = """
import importlib.util,json,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('bridge_sender_story',sys.argv[1])
story=importlib.util.module_from_spec(spec);spec.loader.exec_module(story)
book=story.Book(sys.argv[2])
try:
    bridge=story.platform_chrome_bridge
    bridge.OP_SECONDS['verify_target']=float(sys.argv[6])
    binding,profile=bridge._binding(book,sys.argv[3],sys.argv[4])
    with bridge.HostAdapter(book.root,binding,profile) as adapter:
        Path(sys.argv[5]).write_text('ready',encoding='utf-8')
        results=[adapter._request('verify_target',{}) for _ in range(int(sys.argv[7]))]
    assert all(result==binding['target'] for result in results)
    print(json.dumps({'ok':True,'requests':len(results)}),flush=True)
finally:
    book.close()
"""
        failure, cleanup_failures, request_ids = None, [], []
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output, \
                tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
            process = subprocess.Popen([sys.executable, "-I", "-B", "-c", sender,
                str(fixtures.TOOL), str(self.root), self.plan["id"], self.path.name, str(ready),
                str(operation_budget), str(request_count)],
                stdout=output, stderr=errors)
            deadline = time.monotonic() + request_count * operation_budget + startup_and_cleanup_budget
            try:
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(ready.is_file(), "Sender did not initialize its independent Book and HostAdapter")
                while len(request_ids) < request_count and process.poll() is None and time.monotonic() < deadline:
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
                self.assertEqual(len(request_ids), request_count, "Cross-process request exchange did not finish")
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
        self.assertEqual(json.loads(stdout), {"ok": True, "requests": request_count}, diagnostic)
        self.assertEqual(len(set(request_ids)), request_count)

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


class MacOSProcessLivenessTests(unittest.TestCase):
    def queries(self, group=4321, session=5678):
        return SimpleNamespace(name="posix", getpgid=Mock(return_value=group),
                               getsid=Mock(return_value=session),
                               kill=Mock(side_effect=AssertionError("must not signal")))

    def test_specific_pid_requires_both_read_only_queries_and_not_matching_group_ids(self):
        platform = self.queries()
        with patch.object(bridge, "os", platform):
            self.assertTrue(bridge._macos_process_alive(1234))
        platform.getpgid.assert_called_once_with(1234)
        platform.getsid.assert_called_once_with(1234)
        platform.kill.assert_not_called()

    def test_invalid_pid_never_queries_the_current_process_or_a_group(self):
        platform = self.queries()
        with patch.object(bridge, "os", platform):
            for pid in (None, True, "1234", 0, -1, 1.0, 0x80000000):
                self.assertFalse(bridge._macos_process_alive(pid))
        platform.getpgid.assert_not_called()
        platform.getsid.assert_not_called()
        platform.kill.assert_not_called()

    def test_nonpositive_or_noninteger_query_results_fail_closed(self):
        for result in (None, True, "4321", 0, -1, 1.0):
            for query in ("getpgid", "getsid"):
                with self.subTest(query=query, result=result):
                    platform = self.queries()
                    getattr(platform, query).return_value = result
                    with patch.object(bridge, "os", platform):
                        self.assertFalse(bridge._macos_process_alive(1234))
                    platform.kill.assert_not_called()

    def test_query_denial_dead_pid_unavailable_api_or_other_exception_never_assumes_alive(self):
        for failure in (PermissionError(1, "denied"), ProcessLookupError(3, "exited"),
                        OverflowError(), ValueError(), TypeError(), RuntimeError()):
            for query in ("getpgid", "getsid"):
                with self.subTest(query=query, failure=type(failure).__name__):
                    platform = self.queries()
                    getattr(platform, query).side_effect = failure
                    with patch.object(bridge, "os", platform):
                        self.assertFalse(bridge._macos_process_alive(1234))
                    platform.kill.assert_not_called()
        for missing in ("getpgid", "getsid"):
            platform = self.queries()
            delattr(platform, missing)
            with patch.object(bridge, "os", platform):
                self.assertFalse(bridge._macos_process_alive(1234))
            platform.kill.assert_not_called()

    def test_owner_dispatch_only_changes_macos_and_preserves_service_instance_guard(self):
        platform = self.queries()
        with patch.object(bridge, "os", platform), patch.object(bridge.sys, "platform", "darwin"):
            self.assertTrue(bridge._owner_alive("unused", {"owner_pid": 1234}))
            for service, expected in (({"pid": 1234, "instance": "expected", "stopped": False}, True),
                                      ({"pid": 1234, "instance": "other", "stopped": False}, False),
                                      ({"pid": 1235, "instance": "expected", "stopped": False}, False),
                                      ({"pid": 1234, "instance": "expected", "stopped": True}, False)):
                with patch.object(bridge.api, "safe_path", return_value=Path("unused-service.json")), \
                        patch.object(bridge.api, "read_json", return_value=service):
                    self.assertEqual(bridge._owner_alive("unused", {"owner_pid": 1234,
                        "owner_service_instance": "expected"}), expected)
            with patch.object(bridge.api, "safe_path", return_value=Path("unused-service.json")), \
                    patch.object(bridge.api, "read_json", side_effect=ValueError("unreadable")):
                self.assertFalse(bridge._owner_alive("unused", {"owner_pid": 1234,
                    "owner_service_instance": "expected"}))
        platform.kill.assert_not_called()
        platform.kill.side_effect = None
        with patch.object(bridge, "os", platform), patch.object(bridge.sys, "platform", "linux"), \
                patch.object(bridge, "_macos_process_alive", side_effect=AssertionError("macOS only")):
            self.assertTrue(bridge._owner_alive("unused", {"owner_pid": 1234}))
        platform.kill.assert_called_once_with(1234, 0)

    @unittest.skipUnless(sys.platform == "darwin", "requires native macOS process queries")
    def test_native_live_and_waited_child_pid_without_signals_even_when_its_group_remains(self):
        child = subprocess.Popen([sys.executable, "-c",
            "import sys; print('ready', flush=True); sys.stdin.buffer.read(1)"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            self.assertEqual(child.stdout.readline().strip(), b"ready")
            self.assertEqual(os.getpgid(child.pid), os.getpgid(os.getpid()))
            with patch.object(os, "kill", side_effect=AssertionError("must not signal")):
                self.assertTrue(bridge._owner_alive("unused", {"owner_pid": os.getpid()}))
                self.assertTrue(bridge._owner_alive("unused", {"owner_pid": child.pid}))
            child.stdin.write(b"x"); child.stdin.flush(); child.stdin.close()
            child.wait(timeout=10)
            with patch.object(os, "kill", side_effect=AssertionError("must not signal")):
                self.assertFalse(bridge._owner_alive("unused", {"owner_pid": child.pid}))
                self.assertTrue(bridge._owner_alive("unused", {"owner_pid": os.getpid()}))
        finally:
            if child.stdin and not child.stdin.closed:
                child.stdin.close()
            child.wait(timeout=10)
            child.stdout.close()


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
                patch.object(bridge, "_require_health", return_value={"ready": True}) as health, \
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
        self.assertEqual(health.call_count, 2)
        lease.assert_called_once()


class BridgePinEnterRetryTests(unittest.TestCase):
    """Acquisition retries never repeat a caller's body or context exit."""

    def setUp(self):
        self.path = Path("bridge-pin-fixture")
        self.directory = SimpleNamespace(path=self.path, fd=None)
        self.elapsed = 0.0
        self.sleeps = []
        self.active = set()
        self.events = []

    @staticmethod
    def sharing_error(code=32):
        error = PermissionError("injected native pin sharing refusal")
        error.winerror = code
        return error

    def sleep(self, seconds):
        self.assertGreater(seconds, 0)
        self.sleeps.append(seconds)
        self.elapsed += seconds

    @contextmanager
    def environment(self, factory, platform="nt"):
        clock = SimpleNamespace(monotonic=lambda: self.elapsed, sleep=self.sleep)
        with patch.object(bridge, "os", SimpleNamespace(name=platform)), \
                patch.object(bridge, "time", clock), \
                patch.object(bridge.api, "_pinned_directory", side_effect=factory) as pin:
            yield pin

    def context_factory(self, errors=(), *, always=None, exit_error=None):
        errors = list(errors)
        @contextmanager
        def acquire(path, *, create=False):
            self.assertEqual(path, self.path)
            self.assertEqual(self.active, set(), "A failed attempt retained its native pin")
            attempt = len([event for event in self.events if event[0] == "enter"])
            self.events.append(("enter", create))
            self.active.add(attempt)
            try:
                error = errors[attempt] if attempt < len(errors) else always
                if error is not None:
                    raise error
                yield self.directory
            finally:
                self.active.remove(attempt)
                self.events.append(("exit", attempt))
                if exit_error is not None:
                    raise exit_error
        return acquire

    def reset_attempts(self):
        self.elapsed, self.sleeps, self.events, self.active = 0.0, [], [], set()

    def test_enter_sharing_conflicts_retry_before_exactly_one_body_and_successful_exit(self):
        for code in (32, 33):
            with self.subTest(winerror=code):
                self.reset_attempts()
                body = Mock()
                factory = self.context_factory([self.sharing_error(code)])
                with self.environment(factory) as pin:
                    with bridge._pinned(self.path) as directory:
                        self.assertIs(directory, self.directory)
                        body()
                        self.assertEqual(len(self.active), 1)
                self.assertEqual(pin.call_count, 2)
                body.assert_called_once_with()
                self.assertEqual([event[0] for event in self.events], ["enter", "exit", "enter", "exit"])
                self.assertEqual(self.active, set())
                self.assertTrue(self.sleeps)

    def test_continuous_enter_sharing_conflicts_stop_at_deadline_without_body(self):
        for code in (32, 33):
            with self.subTest(winerror=code):
                self.reset_attempts()
                error = self.sharing_error(code)
                body = Mock()
                with self.environment(self.context_factory(always=error)) as pin:
                    with self.assertRaises(PermissionError) as raised:
                        with bridge._pinned(self.path):
                            body()
                self.assertIs(raised.exception, error)
                self.assertGreater(pin.call_count, 1)
                self.assertLess(pin.call_count, 200)
                body.assert_not_called()
                self.assertGreaterEqual(self.elapsed, .99)
                self.assertLessEqual(self.elapsed, 1.01)
                self.assertEqual(self.active, set())

    def test_other_errors_and_posix_sharing_errors_do_not_retry(self):
        for platform, code in (("nt", 5), ("nt", None), ("posix", 32), ("posix", 33)):
            with self.subTest(platform=platform, winerror=code):
                self.reset_attempts()
                error = PermissionError("unrelated native refusal")
                if code is not None:
                    error.winerror = code
                body = Mock()
                with self.environment(self.context_factory(always=error), platform) as pin:
                    with self.assertRaises(PermissionError) as raised:
                        with bridge._pinned(self.path):
                            body()
                self.assertIs(raised.exception, error)
                pin.assert_called_once_with(self.path, create=False)
                body.assert_not_called()
                self.assertEqual(self.sleeps, [])
                self.assertEqual(self.active, set())

    def test_body_sharing_error_is_propagated_without_reentering_or_replaying(self):
        for code in (32, 33):
            with self.subTest(winerror=code):
                self.reset_attempts()
                error = self.sharing_error(code)
                body = Mock(side_effect=error)
                with self.environment(self.context_factory()) as pin:
                    with self.assertRaises(PermissionError) as raised:
                        with bridge._pinned(self.path):
                            body()
                self.assertIs(raised.exception, error)
                pin.assert_called_once_with(self.path, create=False)
                body.assert_called_once_with()
                self.assertEqual([event[0] for event in self.events], ["enter", "exit"])
                self.assertEqual(self.sleeps, [])
                self.assertEqual(self.active, set())

    def test_exit_sharing_error_is_propagated_without_reentering_or_replaying(self):
        for code in (32, 33):
            with self.subTest(winerror=code):
                self.reset_attempts()
                error = self.sharing_error(code)
                body = Mock()
                with self.environment(self.context_factory(exit_error=error)) as pin:
                    with self.assertRaises(PermissionError) as raised:
                        with bridge._pinned(self.path):
                            body()
                self.assertIs(raised.exception, error)
                pin.assert_called_once_with(self.path, create=False)
                body.assert_called_once_with()
                self.assertEqual([event[0] for event in self.events], ["enter", "exit"])
                self.assertEqual(self.sleeps, [])
                self.assertEqual(self.active, set())

    def test_partial_create_enter_cleans_resources_before_idempotent_retry(self):
        with tempfile.TemporaryDirectory(prefix="bridge-pin-create-") as area:
            self.path = Path(area) / "new-session"
            handles, creations = [], []
            @contextmanager
            def acquire(path, *, create=False):
                self.assertTrue(create)
                self.assertTrue(all(handle.closed for handle in handles))
                path.mkdir(exist_ok=True)
                marker = path / "authorized-before-conflict"
                if not marker.exists():
                    marker.write_bytes(b"must survive retry")
                handle = (path / "native-handle-fixture").open("ab")
                handles.append(handle)
                creations.append(path)
                try:
                    if len(handles) == 1:
                        raise self.sharing_error()
                    self.assertEqual(marker.read_bytes(), b"must survive retry")
                    yield SimpleNamespace(path=path, fd=None)
                finally:
                    handle.close()
            body = Mock()
            with self.environment(acquire) as pin:
                with bridge._pinned(self.path, create=True) as directory:
                    self.assertEqual(directory.path, self.path)
                    body()
            self.assertEqual(pin.call_count, 2)
            self.assertEqual(creations, [self.path, self.path])
            self.assertTrue(all(handle.closed for handle in handles))
            self.assertEqual((self.path / "authorized-before-conflict").read_bytes(), b"must survive retry")
            body.assert_called_once_with()


class BridgeWindowsLockContentionTests(unittest.TestCase):
    """A contended byte lock never replays an operation or sheds its guards."""

    def setUp(self):
        self.directory = SimpleNamespace(path=Path("bridge-lock-fixture"), fd=71)
        self.file = SimpleNamespace(directory=self.directory, name="session.lock",
                                    path=self.directory.path / "session.lock")
        self.fd_stat = self.file_stat = SimpleNamespace(st_dev=7, st_ino=11, st_size=1)
        self.elapsed, self.sleeps, self.attempts, self.unlocks, self.closed = 0.0, [], [], 0, 0
        self.failures, self.always_failure, self.exit_error = [], None, None
        self.on_acquire = self.on_sleep = self.on_guard = None
        self.locked = False
        self.stream = Mock()
        self.stream.fileno.return_value = 77
        self.native = SimpleNamespace(LK_LOCK=1, LK_NBLCK=2, LK_UNLCK=0,
                                      locking=Mock(side_effect=self.locking))

    @staticmethod
    def refusal(number=36, winerror=None):
        failure = OSError(number, "injected Windows lock refusal")
        if winerror is not None:
            failure.winerror = winerror
        return failure

    def locking(self, fd, mode, size):
        self.assertEqual((fd, size), (77, 1))
        if mode == self.native.LK_UNLCK:
            self.assertTrue(self.locked, "An unowned lock must never be unlocked")
            self.unlocks += 1
            if self.exit_error:
                raise self.exit_error
            self.locked = False
            return
        self.assertEqual(mode, self.native.LK_NBLCK, "CRT blocking polling must not be used")
        self.attempts.append(self.elapsed)
        failure = self.failures.pop(0) if self.failures else self.always_failure
        if failure is not None:
            raise failure
        self.locked = True
        if self.on_acquire:
            self.on_acquire()

    def sleep(self, seconds):
        self.assertGreater(seconds, 0)
        self.sleeps.append(seconds)
        if not self.locked and self.unlocks:
            self.assertTrue(bridge._LOCAL_LOCK._is_owned(), "The same process must not immediately reacquire during handoff")
        self.elapsed += seconds
        if self.on_sleep:
            self.on_sleep(seconds)

    def guard(self, directory):
        self.assertIs(directory, self.directory)
        if self.on_guard:
            self.on_guard()

    @contextmanager
    def owned(self, fd, mode):
        self.assertEqual((fd, mode), (77, "r+b"))
        try:
            yield self.stream
        finally:
            self.closed += 1

    @contextmanager
    def environment(self, platform="nt"):
        native_os = SimpleNamespace(name=platform, path=os.path, O_CREAT=os.O_CREAT,
            O_RDWR=os.O_RDWR, O_NOFOLLOW=getattr(os, "O_NOFOLLOW", 0),
            open=Mock(return_value=77), fstat=lambda _: self.fd_stat)
        # Use Windows' actual CRT errno values on every test host, including
        # errno 36 from the real sender failure, rather than macOS aliases.
        native_errno = SimpleNamespace(EACCES=13, EAGAIN=11, EDEADLK=36, ETIMEDOUT=110)
        clock = SimpleNamespace(monotonic=lambda: self.elapsed, sleep=self.sleep)
        self.flock = SimpleNamespace(LOCK_EX=2, LOCK_UN=8, flock=Mock())
        with patch.object(bridge, "os", native_os), patch.object(bridge, "errno", native_errno), \
                patch.object(bridge, "time", clock), patch.object(bridge.secrets, "randbelow", return_value=0), \
                patch.object(bridge.api, "_BoundFile", return_value=self.file), \
                patch.object(bridge.api, "_owned_fdopen", side_effect=self.owned), \
                patch.object(bridge.api.publish, "_bound_stat", side_effect=lambda _: self.file_stat) as bound_stat, \
                patch.object(bridge.api, "_verify_bound_directory", side_effect=self.guard) as guard, \
                patch.dict(sys.modules, {"msvcrt": self.native, "fcntl": self.flock}):
            yield SimpleNamespace(guard=guard, bound_stat=bound_stat, open=native_os.open)

    def test_contended_nonblocking_acquisition_rechecks_identity_and_hands_off(self):
        for failure in (self.refusal(13), self.refusal(11), self.refusal(36),
                        self.refusal(13, 32), self.refusal(13, 33)):
            with self.subTest(errno=failure.errno, winerror=getattr(failure, "winerror", None)):
                self.setUp()
                self.failures = [failure, failure]
                body = Mock()
                with self.environment() as checks:
                    with bridge._lock(self.directory):
                        self.assertTrue(self.locked)
                        body()
                body.assert_called_once_with()
                self.assertEqual(len(self.attempts), 3)
                self.assertEqual((self.unlocks, self.closed), (1, 1))
                self.assertEqual(checks.bound_stat.call_count, 5)
                self.assertEqual(checks.guard.call_count, 5)
                self.assertEqual(self.sleeps, [.005, .005, .01])
                self.assertEqual(self.stream.seek.call_args_list, [((0,),)] * 4)

    def test_contention_deadline_never_enters_body_or_unlocks_unowned_lock(self):
        self.always_failure = failure = self.refusal(36)
        body = Mock()
        with self.environment(), self.assertRaises(OSError) as raised:
            with bridge._lock(self.directory):
                body()
        self.assertIs(raised.exception, failure)
        body.assert_not_called()
        self.assertEqual((self.unlocks, self.closed), (0, 1))
        self.assertAlmostEqual(self.elapsed, 10.0)
        self.assertTrue(all(start < 10 for start in self.attempts))
        self.assertGreater(len(self.attempts), 1000)
        self.assertLessEqual(len(self.attempts), 2001)
        self.assertTrue(all(0 < delay <= .005 for delay in self.sleeps))

    def test_noncontention_and_explicit_access_denial_never_retry(self):
        for failure in (self.refusal(9), self.refusal(22), self.refusal(5), self.refusal(13, 5)):
            with self.subTest(errno=failure.errno, winerror=getattr(failure, "winerror", None)):
                self.setUp()
                self.failures = [failure]
                body = Mock()
                with self.environment(), self.assertRaises(OSError) as raised:
                    with bridge._lock(self.directory):
                        body()
                self.assertIs(raised.exception, failure)
                body.assert_not_called()
                self.assertEqual((len(self.attempts), self.unlocks, self.closed), (1, 0, 1))
                self.assertEqual(self.sleeps, [])

    def test_changed_lock_identity_or_directory_stops_waiting(self):
        for changed in ("path", "fd", "directory"):
            with self.subTest(changed=changed):
                self.setUp()
                self.failures = [self.refusal(36)]
                failure = story.StoryError("platform_chrome_unsafe_file", "injected replaced directory")
                def replace(_):
                    replacement = SimpleNamespace(st_dev=7, st_ino=12, st_size=1)
                    if changed == "path":
                        self.file_stat = replacement
                    elif changed == "fd":
                        self.fd_stat = replacement
                    else:
                        self.on_guard = lambda: (_ for _ in ()).throw(failure)
                self.on_sleep = replace
                body = Mock()
                with self.environment(), self.assertRaises(story.StoryError) as raised:
                    with bridge._lock(self.directory):
                        body()
                self.assertEqual(raised.exception.code, "platform_chrome_unsafe_file")
                body.assert_not_called()
                self.assertEqual((len(self.attempts), self.unlocks, self.closed), (1, 0, 1))

    def test_identity_change_or_deadline_after_acquisition_unlocks_before_refusing(self):
        for changed in ("path", "deadline"):
            with self.subTest(changed=changed):
                self.setUp()
                def acquired():
                    if changed == "path":
                        self.file_stat = SimpleNamespace(st_dev=7, st_ino=12, st_size=1)
                    else:
                        self.elapsed = 10
                self.on_acquire = acquired
                body = Mock()
                expected = story.StoryError if changed == "path" else TimeoutError
                with self.environment(), self.assertRaises(expected):
                    with bridge._lock(self.directory):
                        body()
                body.assert_not_called()
                self.assertEqual((len(self.attempts), self.unlocks, self.closed), (1, 1, 1))
                self.assertFalse(self.locked)
                self.assertEqual(self.sleeps, [.01])

    def test_body_unlock_and_handoff_errors_are_not_replayed(self):
        for stage in ("body", "unlock", "handoff"):
            with self.subTest(stage=stage):
                self.setUp()
                failure = self.refusal(36, 32)
                if stage == "unlock":
                    self.exit_error = failure
                if stage == "handoff":
                    self.on_sleep = lambda _: (_ for _ in ()).throw(failure)
                body = Mock(side_effect=failure if stage == "body" else None)
                with self.environment(), self.assertRaises(OSError) as raised:
                    with bridge._lock(self.directory):
                        body()
                self.assertIs(raised.exception, failure)
                body.assert_called_once_with()
                self.assertEqual((len(self.attempts), self.unlocks, self.closed), (1, 1, 1))

    def test_posix_flock_is_unchanged(self):
        with self.environment(platform="posix"):
            with bridge._lock(self.directory):
                pass
        self.assertEqual(self.flock.flock.call_args_list, [((77, self.flock.LOCK_EX),), ((77, self.flock.LOCK_UN),)])
        self.native.locking.assert_not_called()
        self.assertEqual((self.closed, self.sleeps), (1, []))

    def test_empty_lock_initialization_is_not_repeated(self):
        self.fd_stat.st_size = 0
        self.failures = [self.refusal(36), self.refusal(36)]
        with self.environment():
            with bridge._lock(self.directory):
                pass
        self.stream.write.assert_called_once_with(b"0")
        self.stream.flush.assert_called_once_with()
        self.assertEqual((len(self.attempts), self.unlocks, self.closed), (3, 1, 1))


if __name__ == "__main__":
    unittest.main()
