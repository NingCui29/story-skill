"""Offline fake-wire tests: never start a model, browser, or platform operation."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "skills/story-skill/scripts/story_chrome_worker.py"
spec = importlib.util.spec_from_file_location("chrome_worker_test_module", SCRIPT)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def owned_message(method, *, item=None, turn=None, thread_id="ephemeral-thread", turn_id="real-fake-wire-turn"):
    params = {"threadId": thread_id}
    if item is not None:
        params.update(turnId=turn_id, item=item)
    if turn is not None:
        params["turn"] = {"id": turn_id, **turn}
    return {"method": method, "params": params}


def observe_tool(client, result, *, item_id="fixed-worker-tool", status="completed", error=None):
    base = {"type": "mcpToolCall", "id": item_id, "server": "node_repl", "tool": "js"}
    client._observe(owned_message("item/started", item={**base, "status": "inProgress"}))
    client._observe(owned_message("item/completed", item={**base, "status": status, "result": result, "error": error}))


def checkpoint_result(value):
    return {"content": [{"type": "text", "text": worker.CHECKPOINT_PREFIX + json.dumps(value)}]}


class Input:
    def __init__(self, process):
        self.process = process

    def write(self, data):
        self.process.receive(json.loads(data))

    def flush(self):
        pass


class Output:
    def __init__(self):
        self.lines = queue.Queue()

    def readline(self, limit):
        return self.lines.get()

    def put(self, message):
        self.lines.put(json.dumps(message).encode() + b"\n")


class FakeProcess:
    sequence = 8000

    def __init__(self, on_turn=None, drop=None, before_ack=False):
        FakeProcess.sequence += 1
        self.pid, self.returncode = FakeProcess.sequence, None
        self.stdout, self.stdin = Output(), Input(self)
        self.messages, self.on_turn, self.drop = [], on_turn, drop
        self.before_ack = before_ack

    def receive(self, message):
        self.messages.append(message)
        method = message.get("method")
        if "id" not in message or "method" not in message or method == self.drop:
            return
        result = {}
        if method == "thread/start":
            result = {"thread": {"id": "ephemeral-thread", "ephemeral": True, "path": None}}
        elif method == "turn/start":
            result = {"turn": {"id": "real-fake-wire-turn"}}
        if method == "turn/start" and self.on_turn and self.before_ack:
            self.on_turn(self)
        self.stdout.put({"id": message["id"], "result": result})
        if method == "turn/start" and self.on_turn and not self.before_ack:
            self.on_turn(self)
        if method == "turn/interrupt":
            self.finish("interrupted")

    def finish(self, status="completed"):
        self.stdout.put(owned_message("turn/completed", turn={"status": status}))

    def checkpoint(self, value, item_id="fixed-worker-tool"):
        base = {"type": "mcpToolCall", "id": item_id, "server": "node_repl", "tool": "js"}
        self.stdout.put(owned_message("item/started", item={**base, "status": "inProgress"}))
        self.stdout.put(owned_message("item/completed", item={**base, "status": "completed", "result": checkpoint_result(value)}))

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0
        self.stdout.lines.put(b"")

    kill = terminate

    def wait(self, timeout=None):
        return self.returncode


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.book_root = Path(self.temp.name).resolve()
        self.storage_root = self.book_root / ".story"
        self.storage_root.mkdir(mode=0o700)
        self.context = self.book_root / ".story-skill/chrome-workers" / ("a" * 32)
        self.context.mkdir(mode=0o700, parents=True)

    def initialize(self, client, prompt="fixed prompt", **kwargs):
        return client.initialize(self.context, prompt, book_root=self.book_root, storage_root=self.storage_root, **kwargs)

    def client(self, **kwargs):
        process = FakeProcess(**kwargs)
        client = worker.AppServerClient(process)
        self.addCleanup(client.close)
        return process, client

    def test_real_turn_contract_uses_ephemeral_and_inherits_model(self):
        process, client = self.client()
        self.initialize(client)
        methods = [message.get("method") for message in process.messages]
        self.assertEqual(methods, ["initialize", "initialized", "thread/start", "turn/start"])
        start = process.messages[2]["params"]
        self.assertIs(start["ephemeral"], True)
        self.assertEqual(start["sandbox"], "read-only")
        self.assertFalse(any(key in start for key in ("model", "effort", "reasoningEffort")))
        turn = process.messages[3]["params"]
        self.assertEqual(turn["input"], [{"type": "text", "text": "fixed prompt"}])
        self.assertEqual(set(turn), {"threadId", "input", "sandboxPolicy"})
        self.assertEqual(turn["sandboxPolicy"], {"type": "workspaceWrite",
            "writableRoots": [str(self.storage_root), str(self.context)], "networkAccess": False,
            "excludeSlashTmp": True, "excludeTmpdirEnvVar": True})
        self.assertEqual(start["approvalPolicy"], "never")
        self.assertEqual(client.turn_id, "real-fake-wire-turn")

    def test_persistent_context_model_override_and_arbitrary_rpc_rejected(self):
        _, client = self.client()
        for method, params in (("thread/start", {"ephemeral": False}),
                               ("thread/start", {"ephemeral": True, "model": "other"}),
                               ("turn/start", {"effort": "low"}),
                               ("thread/resume", {"threadId": "a-user-chat"}),
                               ("mcpServer/tool/call", {})):
            with self.subTest(method=method, params=params), self.assertRaises(worker.WorkerError):
                client.request(method, params)

    def test_turn_cannot_broaden_storage_network_temp_or_owned_context(self):
        process, client = self.client()
        self.initialize(client)
        turn = process.messages[3]["params"]
        before = len(process.messages)
        candidates = []
        for field, value in (("writableRoots", [str(self.book_root), str(self.context)]),
                             ("writableRoots", [str(self.storage_root), str(Path.home())]),
                             ("networkAccess", True), ("excludeSlashTmp", False),
                             ("excludeTmpdirEnvVar", False), ("type", "dangerFullAccess")):
            candidate = json.loads(json.dumps(turn))
            candidate["sandboxPolicy"][field] = value
            candidates.append(candidate)
        candidates.extend(({**turn, "permissions": "arbitrary-profile"},
                           {**turn, "threadId": "another-user-chat"},
                           {**turn, "input": [{"type": "text", "text": "arbitrary command"}]}))
        for candidate in candidates:
            with self.subTest(candidate=candidate), self.assertRaises(worker.WorkerError):
                client.request("turn/start", candidate)
        self.assertEqual(len(process.messages), before)

    def test_initialize_rejects_wrong_storage_root_or_nonprivate_context_before_rpc(self):
        process, client = self.client()
        for storage in (self.book_root, self.book_root / "missing", self.book_root.parent):
            with self.subTest(storage=storage), self.assertRaises(worker.WorkerError):
                client.initialize(self.context, "fixed prompt", book_root=self.book_root, storage_root=storage)
        if os.name != "nt":
            self.context.chmod(0o755)
            with self.assertRaises(worker.WorkerError):
                self.initialize(client)
        self.assertEqual(process.messages, [])

    @unittest.skipIf(os.name == "nt", "POSIX symbolic link contract")
    def test_initialize_rejects_symlink_storage_and_context(self):
        process, client = self.client()
        actual = self.book_root / "actual-storage"
        self.storage_root.rename(actual)
        self.storage_root.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(worker.WorkerError):
            self.initialize(client)
        self.storage_root.unlink()
        actual.rename(self.storage_root)
        actual_context = self.context.with_name("b" * 32)
        self.context.rename(actual_context)
        self.context.symlink_to(actual_context, target_is_directory=True)
        with self.assertRaises(worker.WorkerError):
            self.initialize(client)
        self.assertEqual(process.messages, [])

    def test_replaced_owned_directory_revokes_frozen_turn_scope(self):
        process, client = self.client()
        self.initialize(client)
        self.context.rename(self.context.with_name("b" * 32))
        self.context.mkdir(mode=0o700)
        before = len(process.messages)
        with self.assertRaises(worker.WorkerError):
            client.request("turn/start", process.messages[3]["params"])
        self.assertEqual(len(process.messages), before)

    def test_rpc_timeout_is_not_retried(self):
        process, client = self.client(drop="initialize")
        with self.assertRaises(worker.WorkerError) as error:
            client.request("initialize", {}, timeout=.01)
        self.assertEqual(error.exception.code, "platform_chrome_worker_rpc_timeout")
        self.assertEqual(len(process.messages), 1)

    def test_process_exit_fails_waiting_rpc(self):
        process, client = self.client(drop="initialize")
        timer = threading.Timer(.01, process.terminate)
        timer.start()
        self.addCleanup(timer.join)
        with self.assertRaises(worker.WorkerError) as error:
            client.request("initialize", {}, timeout=1)
        self.assertEqual(error.exception.code, "platform_chrome_worker_disconnected")

    def test_server_requests_are_denied_without_granting_actions(self):
        process, client = self.client()
        process.stdout.put({"id": "approval", "method": "item/permissions/requestApproval", "params": {}})
        deadline = time.monotonic()+1
        while len(process.messages) < 1 and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(process.messages[0]["error"]["code"], -32601)
        self.assertNotIn("result", process.messages[0])

    def test_close_interrupts_only_owned_turn_and_unsubscribes(self):
        process, client = self.client()
        self.initialize(client)
        client.close()
        self.assertIsNotNone(process.poll())
        interrupts = [message for message in process.messages if message.get("method") == "turn/interrupt"]
        self.assertEqual([message["params"] for message in interrupts], [{
            "threadId": "ephemeral-thread", "turnId": "real-fake-wire-turn"}])
        self.assertTrue(any(message.get("method") == "thread/unsubscribe" for message in process.messages))
        self.assertFalse(any(message.get("method") in ("thread/archive", "thread/delete") for message in process.messages))

    def test_expired_connect_budget_never_starts_thread(self):
        process, client = self.client()
        with self.assertRaises(worker.WorkerError) as error:
            self.initialize(client, deadline=time.monotonic()-1)
        self.assertEqual(error.exception.code, "platform_chrome_worker_connect_timeout")
        self.assertEqual(process.messages, [])

    def test_fixed_checkpoint_propagates_code_without_raw_tool_content(self):
        process = FakeProcess()
        records = []
        client = worker.AppServerClient(process, diagnostic=lambda phase, **fields: records.append({"phase": phase, **fields}))
        self.addCleanup(client.close)
        self.initialize(client)
        value = {"done": True, "state": "failed", "processed": 0,
                 "code": "platform_chrome_worker_existing_tab_required", "phase": "cleanup", "failure_phase": "existing_tab_probe"}
        observe_tool(client, checkpoint_result(value))
        self.assertEqual(client.worker_error, value["code"])
        self.assertEqual(records[-1]["worker_phase"], "cleanup")
        self.assertNotIn("NEVER_PERSIST_ARGUMENTS", json.dumps(records))
        value["code"] = "platform_chrome_worker_cleanup_failed"
        parsed = client._checkpoint({"content": [{"type": "text", "text": worker.CHECKPOINT_PREFIX + json.dumps(value)}]})
        self.assertEqual(parsed["code"], "platform_chrome_worker_cleanup_failed")

    def test_specific_failure_diagnostic_precedes_error_and_terminal_publication(self):
        _, client = self.client()
        self.initialize(client)
        observed = []
        def diagnose(phase, **fields):
            if phase == 'worker_checkpoint':
                # Assertions belong outside this callback: diagnostic failures
                # are deliberately swallowed by the production transport.
                observed.append((client.worker_error, client.worker_done,
                                 client.worker_done_event.is_set(), fields))
        client.diagnostic = diagnose
        value = {'done': True, 'state': 'failed', 'phase': 'cleanup', 'processed': 0,
                 'code': 'platform_chrome_worker_runtime_unavailable', 'failure_phase': 'sdk_setup',
                 'error_name': 'Error', 'error_marker': 'missing_browser_service'}
        observe_tool(client, checkpoint_result(value))
        self.assertEqual(len(observed), 1)
        error, terminal, terminal_signalled, fields = observed[0]
        self.assertEqual((error, terminal, terminal_signalled), (None, None, False))
        self.assertEqual(fields['failure_phase'], 'sdk_setup')
        self.assertEqual(fields['error_marker'], 'missing_browser_service')
        self.assertEqual(client.worker_error, value['code'])
        self.assertEqual(client.worker_done, value)
        self.assertTrue(client.worker_done_event.is_set())

    def test_diagnostic_storage_failure_still_publishes_tool_error_without_retry(self):
        process, client = self.client()
        self.initialize(client)
        observed = []
        def diagnose(phase, **fields):
            if phase == 'tool_failed':
                observed.append((client.worker_error, fields))
                raise OSError('synthetic diagnostic storage failure')
        client.diagnostic = diagnose
        before = len(process.messages)
        observe_tool(client, {}, status='failed', error={
            'message': 'Error: Browser use requires a trusted Node REPL browser service'})
        self.assertEqual(len(observed), 1)
        self.assertIsNone(observed[0][0])
        self.assertEqual(observed[0][1]['error_marker'], 'missing_browser_service')
        self.assertEqual(client.worker_error, 'platform_chrome_worker_runtime_unavailable')
        self.assertEqual(len(process.messages), before, 'diagnostic failure cannot retry a tool or RPC')

    def test_checkpoint_rejects_extra_content_unbounded_text_and_unknown_code(self):
        _, client = self.client()
        base = {"done": False, "state": "connecting", "processed": 0}
        def result(value):
            return {"content": [{"type": "text", "text": worker.CHECKPOINT_PREFIX + json.dumps(value)}]}
        self.assertIsNone(client._checkpoint(result({**base, "body": "PRIVATE_BODY"})))
        self.assertIsNone(client._checkpoint({"content": [{"type": "text", "text": "prefix " + worker.CHECKPOINT_PREFIX + json.dumps(base)}]}))
        self.assertIsNone(client._checkpoint({"content": [{"type": "text", "text": "x"*8193}]}))
        self.assertIsNone(client._checkpoint(result({**base, "phase": "PRIVATE_PATH"})))
        self.assertIsNone(client._checkpoint(result({**base, "state": {"unsafe": "value"}})))
        self.assertEqual(client._checkpoint(result({**base, "code": "PRIVATE_SECRET"}))["code"], "platform_chrome_worker_tool_failed")

    def test_checkpoint_accepts_only_fixed_failure_metadata(self):
        _, client = self.client()
        base = {"done": True, "state": "failed", "phase": "cleanup", "processed": 0,
            "code": "platform_chrome_worker_turn_metadata_missing", "failure_phase": "sdk_setup",
            "error_name": "TypeError", "error_marker": "missing_turn_metadata"}
        def parse(value):
            return client._checkpoint({"content": [{"type": "text", "text": worker.CHECKPOINT_PREFIX + json.dumps(value)}]})
        self.assertEqual(parse(base), base)
        permission = {**base, "code": "platform_chrome_worker_origin_permission_required",
            "error_name": "Error", "error_marker": "origin_permission_required",
            "failure_phase": "existing_tab_probe"}
        self.assertEqual(parse(permission), permission)
        for key, value in (("failure_phase", "PRIVATE_PHASE"), ("error_name", "PRIVATE_CREDENTIAL"),
                           ("error_marker", "PRIVATE_DOM"), ("error_name", {"bad": "object"}), ("error_kind", "Error")):
            with self.subTest(key=key):
                self.assertIsNone(parse({**base, key: value}))

    def test_waiting_phase_and_distinct_wait_or_total_deadline_codes_are_valid_receipts(self):
        _, client = self.client()
        for code in ("platform_chrome_worker_browser_wait_timeout", "platform_chrome_worker_connect_timeout"):
            value = {"done": True, "state": "failed", "phase": "cleanup", "processed": 0,
                "code": code, "failure_phase": "waiting_for_chrome"}
            self.assertEqual(client._checkpoint(checkpoint_result(value)), value)
        waiting = {"done": False, "state": "connecting", "phase": "waiting_for_chrome", "processed": 0}
        self.assertEqual(client._checkpoint(checkpoint_result(waiting)), waiting)

    def test_cli_failure_diagnostics_accept_only_fixed_bounded_values(self):
        _, client = self.client()
        base = {"done": True, "state": "failed", "processed": 0, "phase": "cleanup",
            "failure_phase": "bridge_attach", "code": "platform_chrome_worker_cli_failed",
            "cli_exit_code": 2, "cli_known_error": "io_or_input_error", "cli_errno": "EPERM"}
        def parse(value):
            return client._checkpoint({"content": [{"type": "text", "text": worker.CHECKPOINT_PREFIX + json.dumps(value)}]})
        self.assertEqual(parse(base), base)
        self.assertIsNone(parse({**base, "done": False, "state": "connecting"}))
        for key, value in (("cli_exit_code", True), ("cli_exit_code", -1), ("cli_exit_code", 256),
                           ("cli_exit_code", "2"), ("cli_errno", "PRIVATE_PATH"),
                           ("cli_known_error", "PRIVATE_BODY"), ("cli_errno", {}),
                           ("cli_known_error", ["other"]), ("stderr", "PRIVATE_CREDENTIAL")):
            with self.subTest(key=key, value=value):
                self.assertIsNone(parse({**base, key: value}))
        self.assertIsNone(parse({**base, "cli_exit_code": 1.0}))
        self.assertIsNone(parse({**base, "cli_errno": None}))
        self.assertIsNone(parse({**base, "cli_known_error": None}))
        self.assertIsNone(parse({**base, "cli_exit_code": None, "state": "ready"}))
        self.assertIsNone(parse({**base, "signal": "SIGTERM"}))
        self.assertIsNone(parse({**base, "cli_message": "PRIVATE_MESSAGE"}))
        self.assertIsNone(parse({**base, "cli_known_error": "platform_chrome_arbitrary"}))
        self.assertIsNotNone(parse({**base, "cli_exit_code": None, "cli_known_error": "other", "cli_errno": "none"}))
        self.assertEqual(parse({**base, "code": "platform_chrome_unsafe_file"})["code"], "platform_chrome_unsafe_file")

    def test_driver_failure_diagnostics_accept_only_fixed_phase_and_bounded_location(self):
        _, client = self.client()
        base = {"done": True, "state": "failed", "processed": 0, "phase": "cleanup",
            "failure_phase": "queue_operation", "code": "platform_chrome_worker_failed",
            "driver_phase": "all_rows", "driver_line": 123, "driver_column": 45}
        def parse(value):
            return client._checkpoint({"content": [{"type": "text", "text": worker.CHECKPOINT_PREFIX + json.dumps(value)}]})
        self.assertEqual(parse(base), base)
        for phase in worker.DRIVER_PHASES:
            self.assertEqual(parse({**base, "driver_phase": phase})["driver_phase"], phase)
        self.assertIsNotNone(parse({**base, "driver_line": 1, "driver_column": 100000}))
        for key, value in (("driver_phase", "PRIVATE_PATH"), ("driver_phase", {}), ("driver_phase", None),
                           ("driver_line", True), ("driver_line", 0), ("driver_line", 100001),
                           ("driver_line", "123"), ("driver_line", None), ("driver_column", -1),
                           ("driver_column", 1.0), ("driver_column", 100001), ("driver_stack", "PRIVATE_STACK"),
                           ("driver_path", "PRIVATE_PATH"), ("driver_message", "PRIVATE_BODY")):
            with self.subTest(key=key, value=value):
                self.assertIsNone(parse({**base, key: value}))
        for done, state in ((False, "connecting"), (False, "busy"), (True, "stopped"), (True, "ready")):
            self.assertIsNone(parse({**base, "done": done, "state": state}))
        for present in ("driver_phase", "driver_line", "driver_column"):
            partial = {key: value for key, value in base.items() if not key.startswith("driver_") or key == present}
            self.assertIsNotNone(parse(partial))

    def test_wrapped_runtime_failures_keep_only_fixed_name_and_marker(self):
        _, client = self.client()
        for text, code, marker in (
                ("ReferenceError: process is not defined PRIVATE_PATH", "runtime_unavailable", "missing_runtime_global"),
                ("Error: Browser use requires a trusted Node REPL browser service PRIVATE_SECRET", "runtime_unavailable", "missing_browser_service"),
                ("Error: Wrapped Missing required Codex turn metadata: session_id, turn_id PRIVATE_IDS", "turn_metadata_missing", "missing_turn_metadata")):
            with self.subTest(marker=marker):
                result = client._tool_error({"error": {"message": text}})
                self.assertEqual(result["code"], "platform_chrome_worker_" + code)
                self.assertEqual(result["error_marker"], marker)
                self.assertNotIn("PRIVATE_", json.dumps(result))

    def test_runtime_error_classification_drops_dom_and_credentials(self):
        process = FakeProcess()
        records = []
        client = worker.AppServerClient(process, diagnostic=lambda phase, **fields: records.append({"phase": phase, **fields}))
        self.addCleanup(client.close)
        self.initialize(client)
        observe_tool(client, {"isError": True, "content": [{"type": "text", "text": "Debugger unattached PRIVATE_CREDENTIAL DOM_BODY"}]})
        self.assertEqual(client.worker_error, "platform_chrome_worker_browser_unattached")
        self.assertNotIn("PRIVATE_CREDENTIAL", json.dumps(records))
        self.assertNotIn("DOM_BODY", json.dumps(records))

    def test_completed_turn_discards_assistant_text_and_unexpected_tools_fail(self):
        _, client = self.client()
        self.initialize(client)
        client._observe(owned_message("turn/completed", turn={"status": "completed", "items": ["PRIVATE_BODY"]}))
        self.assertEqual(client.completed, {"status": "completed"})
        client._observe(owned_message("item/started", item={"type": "commandExecution", "command": "PRIVATE_COMMAND"}))
        self.assertEqual(client.worker_error, "platform_chrome_worker_unexpected_tool")

    def test_model_claims_never_count_as_actual_tool_progress(self):
        _, client = self.client()
        self.initialize(client)
        client._observe(owned_message("item/completed", item={
            "type": "agentMessage", "text": "SDK ready and tool started PRIVATE_BODY"}))
        self.assertIsNone(client.tool_started_at)
        self.assertIsNone(client.worker_error)

    def test_finish_notifications_require_the_owned_thread_and_turn(self):
        _, client = self.client()
        self.initialize(client)
        for message in (
                owned_message("turn/completed", turn={"status": "completed"}, thread_id="another-chat"),
                owned_message("turn/completed", turn={"status": "completed"}, turn_id="another-turn"),
                {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}):
            client._observe(message)
            self.assertIsNone(client.completed)
            self.assertFalse(client.completed_event.is_set())
        before = len(client.process.messages)
        for method, params in (("turn/interrupt", {"threadId": "another-chat", "turnId": client.turn_id}),
                               ("turn/interrupt", {"threadId": client.thread_id, "turnId": "another-turn"}),
                               ("thread/unsubscribe", {"threadId": "another-chat"})):
            with self.assertRaises(worker.WorkerError):
                client.request(method, params)
        self.assertEqual(len(client.process.messages), before)

    def test_done_requires_a_matching_successful_started_tool_item(self):
        _, client = self.client()
        self.initialize(client)
        result = checkpoint_result({"done": True, "state": "stopped", "phase": "cleanup", "processed": 1})
        base = {"id": "owned-tool", "type": "mcpToolCall", "server": "node_repl", "tool": "js"}
        client._observe(owned_message("item/completed", item={**base, "status": "completed", "result": result}))
        self.assertIsNone(client.worker_done, "an unstarted completion cannot prove finish")
        for thread_id, turn_id in (("another-chat", client.turn_id), (client.thread_id, "another-turn")):
            for method, status in (("item/started", "inProgress"), ("item/completed", "completed")):
                client._observe(owned_message(method, thread_id=thread_id, turn_id=turn_id,
                    item={**base, "status": status, "result": result}))
        client._observe(owned_message("item/completed", item={"type": "agentMessage", "text": worker.CHECKPOINT_PREFIX + json.dumps({
            "done": True, "state": "stopped", "phase": "cleanup", "processed": 1})}))
        self.assertIsNone(client.worker_done)
        for status, is_error, error in (("failed", False, None), ("completed", True, None),
                                        ("completed", False, {"message": "PRIVATE_ERROR"})):
            observe_tool(client, {**result, "isError": is_error}, status=status, error=error)
            self.assertIsNone(client.worker_done)
            self.assertFalse(client.worker_done_event.is_set())
        observe_tool(client, result)
        self.assertEqual(client.worker_done["state"], "stopped")
        self.assertTrue(client.worker_done_event.is_set())
        self.assertIsNone(client.completed, "worker finish alone is not model-turn completion")

    def test_terminal_schema_and_config_only_exception_are_narrow(self):
        _, client = self.client()
        self.initialize(client)
        base = {"done": True, "state": "stopped", "phase": "cleanup", "processed": 0}
        for candidate in ({**base, "done": False}, {**base, "state": "ready"}, {**base, "phase": "queue_wait"},
                          {"done": True, "state": "failed", "processed": 0, "phase": "sdk_setup"}):
            self.assertIsNone(client._checkpoint(checkpoint_result(candidate)))
        config_failure = {"done": True, "state": "failed", "processed": 0,
            "code": "platform_chrome_worker_invalid_config", "failure_phase": "config_validated"}
        self.assertEqual(client._checkpoint(checkpoint_result(config_failure)), config_failure)
        observe_tool(client, checkpoint_result({"done": False, "state": "ready", "phase": "queue_wait", "processed": 0}))
        observe_tool(client, checkpoint_result(config_failure))
        self.assertIsNone(client.worker_done, "an existing worker cannot finish via pre-worker validation")
        _, fresh = self.client()
        self.initialize(fresh)
        observe_tool(fresh, checkpoint_result(config_failure))
        self.assertEqual(fresh.worker_done, config_failure)

    def test_fast_tool_notifications_after_turn_ack_are_not_lost(self):
        def turn(process):
            process.checkpoint({"done": True, "state": "stopped", "phase": "cleanup", "processed": 0})
            process.finish()
        for before_ack in (False, True):
            with self.subTest(before_ack=before_ack):
                _, client = self.client(on_turn=turn, before_ack=before_ack)
                self.initialize(client)
                self.assertTrue(client.worker_done_event.wait(1))
                self.assertTrue(client.completed_event.wait(1))
                self.assertEqual(client.completed, {"status": "completed"})
                self.assertEqual(client._early_turn_events, [])
                self.assertEqual(client._early_turn_bytes, 0)

    def test_before_ack_buffer_is_bounded_and_rejects_other_turns_after_pin(self):
        def turn(process):
            base = {"id": "another-tool", "type": "mcpToolCall", "server": "node_repl", "tool": "js"}
            for method, status in (("item/started", "inProgress"), ("item/completed", "completed")):
                process.stdout.put(owned_message(method, turn_id="another-turn", item={**base, "status": status,
                    "result": checkpoint_result({"done": True, "state": "stopped", "phase": "cleanup", "processed": 0})}))
        _, client = self.client(on_turn=turn, before_ack=True)
        self.initialize(client)
        self.assertIsNone(client.worker_done)
        self.assertEqual(client._early_turn_events, [])
        _, bounded = self.client()
        bounded.thread_id = "ephemeral-thread"
        bounded._pending_methods[1] = "turn/start"
        message = owned_message("item/started", item={"type": "agentMessage", "text": "x"})
        for _ in range(worker.MAX_EARLY_TURN_EVENTS + 1):
            bounded._observe(message)
        self.assertEqual(len(bounded._early_turn_events), worker.MAX_EARLY_TURN_EVENTS)
        self.assertLessEqual(bounded._early_turn_bytes, worker.MAX_EARLY_TURN_BYTES)
        self.assertIsNone(bounded.worker_done)
        self.assertEqual(bounded.worker_error, "platform_chrome_worker_invalid_message")


class FakeCore:
    def __init__(self, root):
        self.root, self.health, self.detaches = root, {}, []
        self.external_lock = threading.RLock()
        self.platform_drafts = SimpleNamespace(_workbench_book=self.book)
        self.platform_chrome_bridge = SimpleNamespace(_binding=self.binding, _digest=self.digest,
            worker_status=self.status, heartbeat=self.detach, _external=self.external)

    @staticmethod
    def safe_path(root, relative):
        return root / relative

    @contextmanager
    def _pinned_directory(self, path):
        # Exercise the real native pin rather than POSIX-only open(directory).
        from tests.test_publish import story
        with story._pinned_directory(path) as directory:
            yield SimpleNamespace(path=directory.path, fd=directory.fd,
                                  native=directory, identity=directory.path.stat())

    @staticmethod
    def _verify_bound_directory(directory):
        from tests.test_publish import story
        if not os.path.samestat(directory.identity, directory.path.stat()):
            raise worker.WorkerError("platform_chrome_worker_invalid_context")
        story._verify_bound_directory(directory.native)

    def external(self, path, create_value=None):
        # The production native writer serializes and atomically publishes;
        # the fake must not expose its unfinished JSON to another test thread.
        with self.external_lock:
            if create_value is None:
                return json.loads(Path(path).read_text())
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump(create_value, stream)

    @contextmanager
    def book(self, root, expected_book_id=None):
        if expected_book_id not in (None, "local-book"):
            raise worker.WorkerError("book_identity_mismatch")
        yield SimpleNamespace(root=Path(root), meta=lambda key: "local-book")

    def binding(self, book, plan_id, profile_id, comparison_policy="strict", body_encoding="verbatim"):
        return {"book_id": "local-book", "plan_id": plan_id, "profile_id": profile_id,
            "comparison_policy": comparison_policy,
            "body_encoding": body_encoding,
            "profile_sha256": self.digest(profile_id), "manifest_sha256": self.digest(plan_id),
            "target": {"platform": "fanqie", "account_id": "synthetic-account", "remote_book_id": "synthetic-remote-book"}}, {}

    def digest(self, value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def status(self, root, binding):
        return dict(self.health.get(self.digest(binding), {"attached": False, "ready": False}))

    def detach(self, book, handle, detach=False):
        self.detaches.append((str(handle), detach))
        value = json.loads(Path(handle).read_text())
        self.health[value["binding_sha256"]] = {"attached": False, "ready": False}


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / ".story").mkdir(mode=0o700)
        self.core = FakeCore(self.root)
        self.controller = worker.ChromeWorkerController(self.core)
        self.processes, self.contexts = [], []
        self.health_mode = "healthy"
        self.complete_immediately = False
        self.tool_progress = False
        self.spawn_delay = self.turn_delay = 0
        self.cooperative_stop = True
        original_signal = self.controller._signal_stop
        def signal_stop(entry):
            original_signal(entry)
            process, client = entry.get("process"), entry.get("client")
            if self.cooperative_stop and process and process.poll() is None and client and client.turn_id:
                if client.worker_done is None:
                    process.checkpoint({"done": True, "state": "stopped", "phase": "cleanup", "processed": 0})
                process.finish()
        self.patchers = [patch.object(worker, "_codex_executable", return_value=Path("/official/codex")),
            patch.object(worker, "_sdk_path", return_value=Path("/official/browser-client.mjs")),
            patch.object(worker, "_private_context", side_effect=self.context),
            patch.object(worker, "_persist_diagnostics", side_effect=self.persist),
            patch.object(worker, "_replace_stop", side_effect=self.replace_stop),
            patch.object(worker, "_regular", side_effect=lambda path, **kwargs: Path(path).absolute()),
            patch.object(self.controller, "_signal_stop", side_effect=signal_stop),
            patch.object(worker.subprocess, "Popen", side_effect=self.spawn)]
        for item in self.patchers:
            item.start()
        self.addCleanup(self.cleanup_workers)

    def cleanup_workers(self):
        for entry in self.controller._workers.values():
            entry["stop"].set()
        for entry in self.controller._workers.values():
            if entry["thread"].ident is not None:
                entry["thread"].join(5)
            self.assertFalse(entry["thread"].is_alive())
        for item in reversed(self.patchers):
            item.stop()

    def context(self, core):
        path = self.root / ".story-skill/chrome-workers" / (format(len(self.contexts) + 1, "032x"))
        path.mkdir(mode=0o700, parents=True)
        self.contexts.append(path)
        return path

    def persist(self, core, context, snapshot):
        with self.core.external_lock:
            path = context / "diagnostics.json"
            descriptor = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump(snapshot, stream)

    def replace_stop(self, core, context, record):
        with self.core.external_lock:
            (context / "stop.json").write_text(json.dumps(record))

    def spawn(self, argv, **kwargs):
        self.assertEqual(argv, [str(Path("/official/codex")), "app-server", "--stdio"])
        self.assertNotIn("shell", kwargs)
        if self.spawn_delay:
            time.sleep(self.spawn_delay)
        context = Path(kwargs["cwd"])

        def turn(process):
            if self.turn_delay:
                time.sleep(self.turn_delay)
            config = json.loads((context / "config.json").read_text())
            identity = config["worker"]
            _handle = {"binding_sha256": config["binding_sha256"]}
            worker._write_private(context / "handle.json", _handle, core=self.core)
            state = {"attached": True, "ready": self.health_mode != "lease-only", "browser_id": "observed-chrome",
                "worker_pid": process.pid if self.health_mode != "wrong-pid" else process.pid+100,
                "worker_instance": identity["instance"] if self.health_mode != "wrong-instance" else "b"*32}
            self.core.health[config["binding_sha256"]] = state
            if self.complete_immediately:
                process.finish()
            if self.tool_progress:
                process.stdout.put(owned_message("item/started", item={
                    "id": "worker-progress", "type": "mcpToolCall", "server": "node_repl", "tool": "js", "status": "inProgress"}))
        process = FakeProcess(on_turn=turn)
        self.processes.append(process)
        return process

    def start(self, profile="saved.json"):
        return self.controller.start(self.root, "synthetic-plan", profile, expected_book_id="local-book")

    def state(self, profile="saved.json"):
        return self.controller.status(self.root, "synthetic-plan", profile, expected_book_id="local-book")

    def wait(self, predicate):
        deadline = time.monotonic()+4
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(.01)
        self.fail("fake worker did not reach expected state")

    def test_same_binding_reuses_one_real_worker_context(self):
        self.assertEqual(self.start()["state"], "connecting")
        self.wait(lambda: self.state()["ready"])
        self.assertTrue(self.start()["ready"])
        self.assertEqual(len(self.processes), 1)
        self.assertTrue(self.state()["uses_codex_quota"])

    def test_explicit_comparison_policy_is_bound_and_passed_to_private_config(self):
        self.start()
        self.wait(lambda: self.state()["ready"])
        self.controller.start(self.root, "synthetic-plan", "saved.json", expected_book_id="local-book",
                              comparison_policy="fanqie_terminal_lf_v1")
        self.wait(lambda: self.controller.status(self.root, "synthetic-plan", "saved.json", expected_book_id="local-book",
            comparison_policy="fanqie_terminal_lf_v1")["ready"])
        configs = [json.loads((path / "config.json").read_text()) for path in self.contexts]
        self.assertEqual({row["comparison_policy"] for row in configs}, {"strict", "fanqie_terminal_lf_v1"})
        self.assertEqual(len({row["binding_sha256"] for row in configs}), 2)
        self.controller.disconnect(self.root, "synthetic-plan", "saved.json", expected_book_id="local-book",
                                   comparison_policy="fanqie_terminal_lf_v1")
        self.assertTrue(self.state()["ready"], "policy-specific cleanup cannot stop the strict worker")

    def test_invalid_comparison_policy_is_rejected_before_launch(self):
        for policy in (None, True, "trim", "fanqie_terminal_lf_v2"):
            with self.assertRaisesRegex(worker.WorkerError, "platform_draft_invalid_comparison_policy"):
                self.controller.start(self.root, "synthetic-plan", "saved.json", comparison_policy=policy)
        self.assertEqual(self.processes, [])

    def test_body_encoding_has_its_own_bound_worker_and_private_config(self):
        self.start()
        self.wait(lambda: self.state()["ready"])
        for encoding in ("markdown_paragraphs_v1", "markdown_paragraphs_v2", "plain_paragraphs_v1"):
            self.controller.start(self.root, "synthetic-plan", "saved.json", expected_book_id="local-book",
                                  body_encoding=encoding)
            self.wait(lambda: self.controller.status(self.root, "synthetic-plan", "saved.json", expected_book_id="local-book",
                body_encoding=encoding)["ready"])
        configs = [json.loads((path / "config.json").read_text()) for path in self.contexts]
        self.assertEqual({row["body_encoding"] for row in configs}, {"verbatim", "markdown_paragraphs_v1", "markdown_paragraphs_v2", "plain_paragraphs_v1"})
        self.assertEqual({row["comparison_policy"] for row in configs}, {"strict"})
        self.assertEqual(len({row["binding_sha256"] for row in configs}), 4)
        self.controller.disconnect(self.root, "synthetic-plan", "saved.json", expected_book_id="local-book",
                                   body_encoding="markdown_paragraphs_v2")
        self.assertTrue(self.state()["ready"])
        self.assertTrue(self.controller.status(self.root, "synthetic-plan", "saved.json", expected_book_id="local-book",
                                              body_encoding="markdown_paragraphs_v1")["ready"])

    def test_invalid_body_encoding_is_rejected_before_launch(self):
        for encoding in (None, True, "trim", "markdown_paragraphs_v3"):
            with self.assertRaisesRegex(worker.WorkerError, "platform_draft_invalid_body_encoding"):
                self.controller.start(self.root, "synthetic-plan", "saved.json", body_encoding=encoding)
        self.assertEqual(self.processes, [])

    def test_different_bindings_have_isolated_processes_and_private_configs(self):
        self.start("one.json")
        self.start("two.json")
        self.wait(lambda: self.state("one.json")["ready"] and self.state("two.json")["ready"])
        self.assertEqual(len(self.processes), 2)
        configs = [json.loads((path / "config.json").read_text()) for path in self.contexts]
        self.assertNotEqual(configs[0]["worker"], configs[1]["worker"])
        self.assertNotEqual(configs[0]["handle_path"], configs[1]["handle_path"])
        self.assertNotEqual(configs[0]["binding_sha256"], configs[1]["binding_sha256"])
        for path, config in zip(self.contexts, configs):
            self.assertEqual(config["idle_timeout_seconds"], 120)
            self.assertEqual(config["checkpoint_ms"], 20000)
            self.assertEqual(config["target"]["remote_book_id"], "synthetic-remote-book")
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o700)
                self.assertEqual((path / "config.json").stat().st_mode & 0o777, 0o600)

    def test_lease_without_actual_health_never_ready_or_retried(self):
        self.health_mode = "lease-only"
        clock = SimpleNamespace(monotonic=Mock(return_value=time.monotonic()), time=time.time)
        with patch.object(worker, "time", clock):
            self.start()
            entry = next(iter(self.controller._workers.values()))
            self.wait(lambda: entry.get("connection_phase") == "awaiting_health")
            self.assertEqual(len(self.processes), 1)
            self.assertFalse(self.state()["ready"], "a lease alone must not establish health")
            # Expire only after fake launch/handshake; filesystem speed must not
            # turn this health test into the separate setup-budget test below.
            clock.monotonic.return_value = entry["connect_deadline"]
            self.wait(lambda: entry["done"].is_set())
        self.assertEqual(self.state()["error"], "platform_chrome_worker_connect_timeout")
        self.assertFalse(self.state()["ready"])
        self.assertFalse(self.state()["connect_budget_extended"])
        self.assertEqual(len(self.processes), 1)

    def test_default_base_wait_is_120_seconds_without_treating_turn_ack_as_ready(self):
        self.health_mode = "lease-only"
        receipt = self.start()
        self.assertEqual(receipt["connect_base_timeout_seconds"], 120)
        self.assertEqual(receipt["connect_timeout_seconds"], 120)
        self.wait(lambda: next(iter(self.controller._workers.values())).get("client") and
                  next(iter(self.controller._workers.values()))["client"].turn_id)
        state = self.state()
        self.assertEqual(state["state"], "connecting")
        self.assertFalse(state["ready"])
        self.assertFalse(state["connect_budget_extended"])

    def test_actual_public_tool_progress_cannot_extend_frozen_connection_budget(self):
        self.health_mode, self.tool_progress = "lease-only", True
        with patch.object(worker, "CONNECT_SECONDS", .25), patch.object(worker, "CONNECT_PROGRESS_SECONDS", 1):
            self.start()
            entry = next(iter(self.controller._workers.values()))
            self.wait(lambda: entry.get("client") and entry["client"].tool_started_at)
            self.assertFalse(self.state()["connect_budget_extended"])
            self.assertEqual(self.state()["connect_timeout_seconds"], .25)
            self.wait(lambda: entry["done"].is_set())
            self.assertLess(time.monotonic() - entry["created"], 1)
        self.assertEqual(self.state()["error"], "platform_chrome_worker_connect_timeout")
        self.assertEqual(len(self.processes), 1)

    def test_bootstrap_epoch_deadline_spends_launch_and_model_startup_time_and_is_reused(self):
        self.health_mode = "lease-only"
        self.spawn_delay = self.turn_delay = .15
        with patch.object(worker, "CONNECT_SECONDS", .8):
            self.start()
        entry = next(iter(self.controller._workers.values()))
        self.wait(lambda: entry.get("connection_phase") == "awaiting_health")
        config = json.loads((entry["context"] / "config.json").read_text())
        self.assertIs(type(config["bootstrap_deadline_ms"]), int)
        self.assertEqual(entry["connect_deadline"], entry["created"] + .8)
        remaining_ms = config["bootstrap_deadline_ms"] - int(time.time() * 1000)
        self.assertGreater(remaining_ms, 100)
        self.assertLess(remaining_ms, 650, "launch/model startup time must already be deducted")
        frozen = (entry["connect_deadline"], config["bootstrap_deadline_ms"])
        self.start()
        self.assertEqual((entry["connect_deadline"], json.loads((entry["context"] / "config.json").read_text())["bootstrap_deadline_ms"]), frozen)
        self.assertEqual(len(self.processes), 1)

    def test_expired_local_setup_budget_does_not_launch_app_server(self):
        first = True
        def delayed(path, **kwargs):
            nonlocal first
            if first:
                first = False
                time.sleep(.08)
            return Path(path).absolute()
        with patch.object(worker, "CONNECT_SECONDS", .03), patch.object(worker, "_regular", side_effect=delayed):
            self.start()
            self.wait(lambda: next(iter(self.controller._workers.values()))["done"].is_set())
        self.assertEqual(self.processes, [])
        self.assertEqual(self.state()["error"], "platform_chrome_worker_connect_timeout")

    def test_health_arriving_after_bootstrap_deadline_never_becomes_ready(self):
        self.turn_delay = .12
        with patch.object(worker, "CONNECT_SECONDS", .04):
            self.start()
            self.wait(lambda: next(iter(self.controller._workers.values()))["done"].is_set())
        self.assertEqual(self.state()["error"], "platform_chrome_worker_connect_timeout")
        self.assertFalse(self.state()["ready"])
        self.assertNotIn("bridge_ready", [event["phase"] for event in self.state()["diagnostics"]])

    def waiting_entry(self):
        self.health_mode = "lease-only"
        self.start()
        entry = next(iter(self.controller._workers.values()))
        self.wait(lambda: entry.get("connection_phase") == "awaiting_health")
        return entry

    def write_waiting_hint(self, entry, **overrides):
        value = {"schema": 1, "worker_instance": entry["instance"], "binding_sha256": entry["fingerprint"],
            "phase": "waiting_for_chrome", "observed_at_ms": int(time.time() * 1000),
            "bootstrap_deadline_ms": entry["bootstrap_deadline_ms"], **overrides}
        path = entry["context"] / "bootstrap-state.json"
        if path.exists() or path.is_symlink():
            path.unlink()
        worker._write_private(path, value, core=self.core)
        return path, value

    def test_private_waiting_hint_only_changes_display_until_it_disappears(self):
        entry = self.waiting_entry()
        before = self.state()
        path, _ = self.write_waiting_hint(entry)
        view = self.state()
        self.assertEqual((view["state"], view["worker_phase"], view["waiting_for_chrome"]), ("connecting", "waiting_for_chrome", True))
        self.assertFalse(view["ready"])
        self.assertFalse(view["platform_target_verified"])
        self.assertEqual(entry["connect_deadline"], entry["created"] + 120)
        self.assertNotIn("worker_phase", entry, "a display hint must not replace actual checkpoint evidence")
        path.unlink()
        view = self.state()
        self.assertEqual(view["worker_phase"], before["worker_phase"])
        self.assertFalse(view["waiting_for_chrome"])
        self.assertFalse(view["ready"])

    def test_legacy_entry_without_new_budget_fields_keeps_safe_display_defaults(self):
        entry = self.waiting_entry()
        self.write_waiting_hint(entry)
        with self.controller._lock:
            saved = {key: entry.pop(key) for key in ("connect_deadline", "connect_timeout_seconds", "context_identity",
                "bootstrap_config_created_ms", "bootstrap_deadline_ms")}
            try:
                view = self.controller._view(entry)
                self.assertEqual((view["state"], view["connect_timeout_seconds"], view["connect_base_timeout_seconds"]),
                                 ("connecting", 120, 120))
                self.assertFalse(view["waiting_for_chrome"])
                self.assertFalse(view["ready"])
            finally:
                entry.update(saved)

    def test_waiting_hint_rejects_other_scope_stale_future_and_extra_fields(self):
        entry = self.waiting_entry()
        for overrides in ({"schema": True}, {"worker_instance": "f" * 32}, {"binding_sha256": "e" * 64},
                          {"bootstrap_deadline_ms": entry["bootstrap_deadline_ms"] + 1},
                          {"bootstrap_deadline_ms": True}, {"phase": "ready"}, {"observed_at_ms": True},
                          {"observed_at_ms": entry["bootstrap_config_created_ms"] - 1},
                          {"observed_at_ms": int(time.time() * 1000) + 2000}, {"body": "PRIVATE_BODY"}):
            with self.subTest(overrides=overrides):
                self.write_waiting_hint(entry, **overrides)
                self.assertFalse(self.state()["waiting_for_chrome"])
        _, value = self.write_waiting_hint(entry)
        with patch.object(worker.time, "time", return_value=value["observed_at_ms"] / 1000 + 26):
            self.assertFalse(self.state()["waiting_for_chrome"])
        self.assertNotIn("PRIVATE_BODY", json.dumps(self.state()))

    def test_waiting_hint_cannot_survive_ready_stop_or_original_deadline(self):
        entry = self.waiting_entry()
        self.write_waiting_hint(entry)
        self.assertTrue(self.state()["waiting_for_chrome"])
        with self.controller._lock:
            entry["state"] = "ready"
            self.assertIsNone(self.controller._bootstrap_hint(entry))
            entry["state"] = "connecting"
            entry["stop"].set()
            self.assertIsNone(self.controller._bootstrap_hint(entry))
            entry["stop"].clear()
            old_deadline = entry["connect_deadline"]
            entry["connect_deadline"] = time.monotonic() - 1
            self.assertIsNone(self.controller._bootstrap_hint(entry))
            entry["connect_deadline"] = old_deadline

    @unittest.skipIf(os.name == "nt", "POSIX private-file permission/link contract")
    def test_waiting_hint_rejects_links_unsafe_permissions_and_replaced_context(self):
        entry = self.waiting_entry()
        path, value = self.write_waiting_hint(entry)
        original = entry["context"] / "original-hint.json"
        path.rename(original)
        path.symlink_to(original)
        self.assertFalse(self.state()["waiting_for_chrome"])
        path.unlink()
        os.link(original, path)
        self.assertFalse(self.state()["waiting_for_chrome"])
        path.unlink()
        original.rename(path)
        path.chmod(0o644)
        self.assertFalse(self.state()["waiting_for_chrome"])
        path.chmod(0o600)
        self.assertTrue(self.state()["waiting_for_chrome"])
        old = entry["context"].with_name("f" * 32)
        with self.controller._lock:
            entry["context"].rename(old)
            try:
                entry["context"].mkdir(mode=0o700)
                worker._write_private(entry["context"] / "bootstrap-state.json", value, core=self.core)
                self.assertIsNone(self.controller._bootstrap_hint(entry))
            finally:
                (entry["context"] / "bootstrap-state.json").unlink()
                entry["context"].rmdir()
                old.rename(entry["context"])

    def test_waiting_hint_rejects_oversize_and_concurrent_changes_without_failing_connection(self):
        entry = self.waiting_entry()
        path, _ = self.write_waiting_hint(entry)
        path.write_text("x" * 4097)
        self.assertFalse(self.state()["waiting_for_chrome"])
        path, _ = self.write_waiting_hint(entry)
        fstat, calls = worker.os.fstat, []
        def change_after_read(descriptor):
            result = fstat(descriptor)
            calls.append(True)
            if len(calls) == 2:
                os.utime(path, ns=(result.st_atime_ns, result.st_mtime_ns + 1000000))
            return result
        with patch.object(worker.os, "fstat", side_effect=change_after_read):
            self.assertFalse(self.state()["waiting_for_chrome"])
        self.assertEqual(self.state()["state"], "connecting")
        self.assertIsNone(self.state()["error"])

    def test_failed_connection_forces_strict_cleanup(self):
        self.health_mode = "lease-only"
        with patch.object(worker, "CONNECT_SECONDS", .05):
            self.start()
            entry = next(iter(self.controller._workers.values()))
            entry["keep_readback"] = True
            self.wait(lambda: entry["done"].is_set())
        self.assertIs(json.loads((entry["context"] / "stop.json").read_text())["keep_readback"], False)

    def test_ready_health_must_match_owned_pid_and_instance(self):
        for mode in ("wrong-pid", "wrong-instance"):
            with self.subTest(mode=mode), patch.object(worker, "CONNECT_SECONDS", .05):
                self.health_mode = mode
                profile = mode + ".json"
                self.start(profile)
                entry = list(self.controller._workers.values())[-1]
                self.wait(lambda: entry["done"].is_set())
                self.assertFalse(self.state(profile)["ready"])
                self.assertEqual(self.state(profile)["error"], "platform_chrome_worker_connect_timeout")

    def test_process_exit_revokes_health_without_restarting(self):
        self.start()
        self.wait(lambda: self.state()["ready"])
        self.processes[0].terminate()
        self.wait(lambda: next(iter(self.controller._workers.values()))["done"].is_set())
        self.assertFalse(self.state()["ready"])
        self.assertEqual(len(self.processes), 1)

    def test_completed_turn_is_never_ready_from_an_existing_healthy_lease(self):
        self.complete_immediately = True
        self.start()
        self.wait(lambda: next(iter(self.controller._workers.values()))["done"].is_set())
        self.assertFalse(self.state()["ready"])
        self.assertEqual(self.state()["error"], "platform_chrome_worker_not_ready")

    def test_health_expiry_revokes_readiness_and_stops_owned_turn(self):
        self.start()
        self.wait(lambda: self.state()["ready"])
        self.core.health.clear()
        self.assertEqual(self.state()["error"], "platform_chrome_worker_health_expired")
        self.wait(lambda: next(iter(self.controller._workers.values()))["done"].is_set())
        self.assertFalse(self.state()["ready"])

    def expire_after_ready_checkpoint(self, entry, age=75, start_item_id=None, status_overrides=None):
        observe_tool(entry["client"], checkpoint_result({"done": False, "state": "ready", "phase": "queue_wait", "processed": 1}))
        entry["client"].last_checkpoint_at -= age
        entry["client"].last_checkpoint_epoch -= age
        tool = self.start_resume_tool(entry, start_item_id) if start_item_id else None
        status = dict(self.core.health[entry["fingerprint"]])
        checked = entry["client"].last_checkpoint_epoch - .1
        status.update(ready=False, state="health_expired", health_state="expired",
            health_checked_at=datetime.fromtimestamp(checked, timezone.utc).isoformat(),
            health_expires_at=datetime.fromtimestamp(checked + 45, timezone.utc).isoformat())
        status.update(status_overrides or {})
        self.core.health[entry["fingerprint"]] = status
        return tool

    def start_resume_tool(self, entry, item_id="resume-probe"):
        item = {"id": item_id, "type": "mcpToolCall", "server": "node_repl", "tool": "js", "status": "inProgress"}
        entry["client"]._observe(owned_message("item/started", item=item))
        return item

    def finish_resume_tool(self, entry, item):
        entry["client"]._observe(owned_message("item/completed", item={**item, "status": "completed",
            "result": checkpoint_result({"done": False, "state": "ready", "phase": "queue_wait", "processed": 2})}))

    def fresh_owned_health(self, entry):
        status = dict(self.core.health[entry["fingerprint"]])
        status.update(ready=True, state="connected", health_state="healthy",
            health_checked_at=datetime.now(timezone.utc).isoformat(),
            health_expires_at=datetime.fromtimestamp(time.time() + 45, timezone.utc).isoformat())
        self.core.health[entry["fingerprint"]] = status

    def test_75_second_model_gap_is_not_ready_until_the_same_next_call_has_fresh_health(self):
        self.start()
        self.wait(lambda: self.state()["ready"])
        entry = next(iter(self.controller._workers.values()))
        self.expire_after_ready_checkpoint(entry)
        before = dict(self.core.health[entry["fingerprint"]])
        view = self.state()
        self.assertEqual((view["state"], view["ready"], view["resume_pending"], view["worker_phase"]),
                         ("resuming", False, True, "awaiting_checkpoint"))
        self.assertEqual(entry["resume_deadline"], entry["client"].last_checkpoint_at + 120)
        deadline = entry["resume_deadline"]
        self.assertFalse(entry["stop"].is_set())
        self.assertEqual(self.core.health[entry["fingerprint"]], before, "waiting never renews a health proof or lease")
        for profile, kwargs in (("saved.json", {}), ("other.json", {}), ("saved.json", {"body_encoding": "markdown_paragraphs_v2"})):
            with self.assertRaisesRegex(worker.WorkerError, "platform_chrome_worker_busy"):
                self.controller.start(self.root, "synthetic-plan", profile, expected_book_id="local-book", **kwargs)
        tool = self.start_resume_tool(entry)
        self.assertEqual(self.state()["state"], "resuming", "the approved next call may probe, but is not yet ready")
        self.assertEqual(entry["resume_deadline"], deadline)
        self.fresh_owned_health(entry)
        self.assertTrue(self.state()["ready"])
        self.finish_resume_tool(entry, tool)
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(sum(message.get("method") == "turn/start" for message in self.processes[0].messages), 1)

    def test_resume_deadline_is_fixed_from_checkpoint_and_never_extended_by_poll_or_a_new_checkpoint(self):
        self.start()
        self.wait(lambda: self.state()["ready"])
        entry = next(iter(self.controller._workers.values()))
        self.expire_after_ready_checkpoint(entry)
        self.assertEqual(self.state()["state"], "resuming")
        deadline = entry["resume_deadline"]
        tool = self.start_resume_tool(entry)
        self.finish_resume_tool(entry, tool)
        self.assertEqual(self.state()["state"], "resuming")
        self.assertEqual(entry["resume_deadline"], deadline)
        # Simulate reaching the original checkpoint+120s boundary without
        # waiting two minutes, while preserving all source/owner evidence.
        entry["resume_deadline"] = time.monotonic() - .01
        self.assertFalse(self.state()["ready"])
        self.assertTrue(entry["stop"].is_set())
        self.wait(entry["done"].is_set)
        self.assertEqual(self.state()["error"], "platform_chrome_worker_health_expired")

    def test_checkpoint_older_than_120_seconds_cannot_enter_resume_grace(self):
        self.start()
        self.wait(lambda: self.state()["ready"])
        entry = next(iter(self.controller._workers.values()))
        self.expire_after_ready_checkpoint(entry, age=121)
        self.assertFalse(self.state()["resume_pending"])
        self.assertTrue(entry["stop"].is_set())
        self.wait(entry["done"].is_set)
        self.assertNotIn("resume_wait", [event["phase"] for event in self.state()["diagnostics"]])

    def test_health_loss_during_an_active_operation_does_not_get_model_gap_grace(self):
        self.start()
        self.wait(lambda: self.state()["ready"])
        entry = next(iter(self.controller._workers.values()))
        tool = self.expire_after_ready_checkpoint(entry, start_item_id="active-operation")
        self.assertFalse(self.state()["resume_pending"])
        self.assertTrue(entry["stop"].is_set())
        entry["client"]._observe(owned_message("item/completed", item={**tool, "status": "completed",
            "result": checkpoint_result({"done": True, "state": "failed", "phase": "cleanup", "processed": 1,
                "code": "platform_chrome_worker_unavailable", "failure_phase": "bridge_health"})}))
        self.wait(entry["done"].is_set)
        self.assertNotIn("resume_wait", [event["phase"] for event in self.state()["diagnostics"]])

    def test_ready_checkpoint_received_after_health_was_already_expired_is_not_a_model_gap(self):
        self.start()
        self.wait(lambda: self.state()["ready"])
        entry = next(iter(self.controller._workers.values()))
        self.expire_after_ready_checkpoint(entry)
        # Even a valid ready tag cannot turn a lease that expired during the
        # preceding operation into evidence of a subsequent idle model gap.
        entry["client"].last_checkpoint_at = time.monotonic()
        entry["client"].last_checkpoint_epoch = time.time()
        self.assertFalse(self.state()["resume_pending"])
        self.wait(entry["done"].is_set)
        self.assertEqual(self.state()["error"], "platform_chrome_worker_health_expired")

    def test_resume_rejects_other_worker_browser_binding_or_owned_turn_and_non_expiry(self):
        for index, kind in enumerate(("instance", "pid", "browser", "binding", "turn", "lease", "future")):
            profile = "scope-" + str(index) + ".json"
            self.start(profile)
            self.wait(lambda: self.state(profile)["ready"])
            entry = list(self.controller._workers.values())[-1]
            changes = {}
            if kind == "instance": changes["worker_instance"] = "f" * 32
            elif kind == "pid": changes["worker_pid"] = entry["process"].pid + 1
            elif kind == "browser": changes["browser_id"] = "other-browser"
            elif kind == "binding": entry["binding"]["manifest_sha256"] = "other-manifest"
            elif kind == "turn": entry["owned_turn"] = ("another-thread", "another-turn")
            elif kind == "lease": changes["attached"] = False
            elif kind == "future": changes["health_expires_at"] = datetime.fromtimestamp(time.time() + 45, timezone.utc).isoformat()
            self.expire_after_ready_checkpoint(entry, status_overrides=changes)
            self.assertFalse(self.state(profile)["resume_pending"], kind)
            self.assertTrue(entry["stop"].is_set(), kind)
            self.wait(entry["done"].is_set)

    def test_resume_allows_only_one_next_call_and_stops_immediately_on_tool_or_permission_error(self):
        for index, kind in enumerate(("second_call", "tool_error", "permission")):
            profile = "failure-" + str(index) + ".json"
            self.start(profile)
            self.wait(lambda: self.state(profile)["ready"])
            entry = list(self.controller._workers.values())[-1]
            self.expire_after_ready_checkpoint(entry)
            self.assertEqual(self.state(profile)["state"], "resuming")
            tool = self.start_resume_tool(entry)
            if kind == "second_call":
                self.finish_resume_tool(entry, tool)
                next_tool = self.start_resume_tool(entry, "another-probe")
                self.fresh_owned_health(entry)
                self.assertFalse(self.state(profile)["ready"])
                self.finish_resume_tool(entry, next_tool)
            elif kind == "tool_error":
                entry["client"]._observe(owned_message("item/completed", item={**tool, "status": "failed",
                    "result": checkpoint_result({"done": True, "state": "stopped", "phase": "cleanup", "processed": 1})}))
            else:
                entry["client"]._observe(owned_message("item/completed", item={**tool, "status": "completed",
                    "result": checkpoint_result({"done": True, "state": "failed", "phase": "cleanup", "processed": 1,
                        "code": "platform_chrome_worker_origin_permission_required", "failure_phase": "existing_tab_probe"})}))
            self.assertFalse(self.state(profile)["resume_pending"])
            self.assertTrue(entry["stop"].is_set())
            self.wait(entry["done"].is_set)
            if kind == "permission":
                self.assertEqual(self.state(profile)["error"], "platform_chrome_worker_origin_permission_required")

    def test_status_and_supervisor_use_the_same_resume_and_real_probe_transition(self):
        for profile in ("polled.json", "supervised.json"):
            self.start(profile)
            self.wait(lambda: self.state(profile)["ready"])
        first, second = list(self.controller._workers.values())
        for entry in (first, second): self.expire_after_ready_checkpoint(entry)
        self.assertEqual(self.state("polled.json")["state"], "resuming")
        self.wait(lambda: second["state"] == "resuming")
        tools = [self.start_resume_tool(entry) for entry in (first, second)]
        for entry in (first, second): self.fresh_owned_health(entry)
        self.assertTrue(self.state("polled.json")["ready"])
        self.wait(lambda: second["state"] == "ready")
        for entry, tool in zip((first, second), tools): self.finish_resume_tool(entry, tool)
        self.assertEqual(len(self.processes), 2)

    def test_resuming_owned_disconnect_finishes_closed_and_status_cannot_reclassify_it(self):
        receipt = self.start()
        self.wait(lambda: self.state()["ready"])
        entry = next(iter(self.controller._workers.values()))
        self.expire_after_ready_checkpoint(entry)
        self.assertEqual(self.state()["state"], "resuming")
        result = self.controller.disconnect(self.root, "synthetic-plan", "saved.json", expected_book_id="local-book",
            owned_worker_id=receipt["owned_worker_id"])
        self.wait(entry["done"].is_set)
        self.assertEqual(result["state"], "closed")
        for _ in range(3):
            view = self.state()
            self.assertEqual(view["state"], "closed")
            self.assertFalse(view["ready"])
            self.assertFalse(view["resume_pending"])
            self.assertTrue(view["worker_done_confirmed"])
            self.assertTrue(view["turn_completed_normally"])
            self.assertFalse(view["cleanup_unconfirmed"])
        self.assertEqual(sum(message.get("method") == "turn/start" for message in self.processes[0].messages), 1)

    def stop_clock(self, entry):
        """Advance only worker monotonic time, never real waits or global tests."""
        value = [time.monotonic()]
        waiting = threading.Event()
        original = self.controller._wait_for_finish
        active, calls = [], []
        def monotonic():
            if active and threading.current_thread().name == "story-chrome-worker":
                calls.append(True)
                if len(calls) >= 2:
                    waiting.set()
            return value[0]
        def wait_for_finish(*args):
            active.append(True)
            return original(*args)
        clock = SimpleNamespace(monotonic=monotonic, time=time.time, sleep=time.sleep)
        return value, waiting, patch.object(worker, "time", clock), patch.object(self.controller, "_wait_for_finish", side_effect=wait_for_finish)

    def test_stop_after_31_seconds_still_allows_actual_finish_before_400_second_cap(self):
        self.cooperative_stop = False
        self.start()
        self.wait(lambda: self.state()["ready"])
        entry, process = next(iter(self.controller._workers.values())), self.processes[0]
        value, waiting, clock_patch, wait_patch = self.stop_clock(entry)
        self.assertEqual(worker.STOP_CHECKPOINT_SECONDS, 400)
        with clock_patch, wait_patch, patch.object(worker, "DISCONNECT_WAIT_SECONDS", .01):
            self.controller.disconnect(self.root, "synthetic-plan", "saved.json")
            self.assertTrue(waiting.wait(1))
            value[0] += 31
            time.sleep(.08)
            self.assertIsNone(process.poll())
            self.assertFalse(any(message.get("method") == "turn/interrupt" for message in process.messages))
            self.assertEqual(self.state()["state"], "stopping")
            process.checkpoint({"done": True, "state": "stopped", "phase": "cleanup", "processed": 1})
            process.finish()
            self.wait(entry["done"].is_set)
        self.assertTrue(self.state()["worker_done_confirmed"])
        self.assertFalse(self.state()["cleanup_unconfirmed"])
        self.assertFalse(any(message.get("method") == "turn/interrupt" for message in process.messages))

    def test_stop_400_second_cap_is_bounded_and_never_replays_a_claimed_write(self):
        self.cooperative_stop = False
        self.start()
        self.wait(lambda: self.state()["ready"])
        entry, process = next(iter(self.controller._workers.values())), self.processes[0]
        marker = self.root / "claimed-write.json"
        marker.write_text("unresolved write must remain")
        value, waiting, clock_patch, wait_patch = self.stop_clock(entry)
        with clock_patch, wait_patch, patch.object(worker, "DISCONNECT_WAIT_SECONDS", .01):
            self.controller.disconnect(self.root, "synthetic-plan", "saved.json")
            self.assertTrue(waiting.wait(1))
            value[0] += 401
            self.wait(entry["done"].is_set)
        self.assertTrue(self.state()["cleanup_unconfirmed"])
        self.assertFalse(self.state()["worker_done_confirmed"])
        interrupts = [message for message in process.messages if message.get("method") == "turn/interrupt"]
        self.assertEqual([message["params"] for message in interrupts], [{
            "threadId": "ephemeral-thread", "turnId": "real-fake-wire-turn"}])
        self.assertEqual(marker.read_text(), "unresolved write must remain")
        self.assertEqual(len(self.processes), 1)


    def test_disconnect_preserves_queues_and_propagates_cleanup_choice(self):
        self.start()
        self.wait(lambda: self.state()["ready"])
        marker = self.root / "claimed-write.json"
        marker.write_text("evidence must remain")
        result = self.controller.disconnect(self.root, "synthetic-plan", "saved.json", keep_readback=False)
        self.assertEqual(result["state"], "closed")
        self.assertFalse(result["ready"])
        stop = json.loads((self.contexts[0] / "stop.json").read_text())
        self.assertIs(stop["keep_readback"], False)
        self.assertEqual(marker.read_text(), "evidence must remain")
        self.assertTrue(self.core.detaches)
        self.assertTrue(all(process.poll() is not None for process in self.processes))

    def test_disconnect_waits_past_old_cutoff_for_real_finish_and_normal_turn(self):
        self.cooperative_stop = False
        self.start()
        self.wait(lambda: self.state()["ready"])
        entry, process = next(iter(self.controller._workers.values())), self.processes[0]
        # A natural terminal checkpoint first sets state=stopping, then the
        # supervisor's finally sets the stop event. Neither half is reusable.
        with self.controller._lock:
            entry["state"] = "stopping"
            self.assertFalse(entry["stop"].is_set())
            view = self.controller._view(entry)
            self.assertEqual((view["state"], view["ready"], view["cleanup_pending"]), ("stopping", False, True))
            with self.assertRaisesRegex(worker.WorkerError, "platform_chrome_worker_busy"):
                self.controller.start(self.root, "synthetic-plan", "another-profile.json", expected_book_id="local-book")
            entry["state"] = "ready"
        marker = self.root / "claimed-write.json"
        marker.write_text("unknown write evidence remains")
        with patch.object(worker, "DISCONNECT_WAIT_SECONDS", .01):
            result = self.controller.disconnect(self.root, "synthetic-plan", "saved.json")
        self.assertEqual((result["state"], result["ready"], result["cleanup_pending"]), ("stopping", False, True))
        self.assertFalse(result["worker_done_confirmed"])
        # Reproduce the previous fixed two-second interruption while a valid
        # worker is still awaiting SDK cleanup inside its current tool call.
        time.sleep(2.1)
        self.assertIsNone(process.poll())
        self.assertFalse(any(message.get("method") == "turn/interrupt" for message in process.messages))
        for kwargs in ({}, {"profile_id": "changed-profile.json"}, {"body_encoding": "markdown_paragraphs_v2"}):
            values = {"profile_id": "saved.json", **kwargs}
            with self.assertRaisesRegex(worker.WorkerError, "platform_chrome_worker_busy"):
                self.controller.start(self.root, "synthetic-plan", expected_book_id="local-book", **values)
        self.assertEqual(len(self.processes), 1)
        process.checkpoint({"done": True, "state": "stopped", "phase": "cleanup", "processed": 1})
        self.wait(lambda: self.state()["worker_done_confirmed"])
        self.assertEqual(self.state()["state"], "stopping")
        self.assertFalse(entry["done"].is_set(), "finish still gives the official turn a completion window")
        self.assertIsNone(process.poll())
        process.finish()
        self.wait(entry["done"].is_set)
        result = self.state()
        self.assertEqual(result["state"], "closed")
        self.assertFalse(result["cleanup_pending"])
        self.assertTrue(result["worker_done_confirmed"])
        self.assertTrue(result["turn_completed_normally"])
        self.assertFalse(result["cleanup_unconfirmed"])
        self.assertFalse(any(message.get("method") == "turn/interrupt" for message in process.messages))
        self.assertEqual(marker.read_text(), "unknown write evidence remains")

    def test_missing_finish_is_bounded_and_marked_unconfirmed_without_replay(self):
        self.cooperative_stop = False
        self.start()
        self.wait(lambda: self.state()["ready"])
        entry, process = next(iter(self.controller._workers.values())), self.processes[0]
        marker = self.root / "claimed-write.json"
        marker.write_text("do not retry")
        with patch.object(worker, "STOP_CHECKPOINT_SECONDS", .08), patch.object(worker, "DISCONNECT_WAIT_SECONDS", .01):
            self.controller.disconnect(self.root, "synthetic-plan", "saved.json")
            self.wait(entry["done"].is_set)
        state = self.state()
        self.assertEqual(state["state"], "closed")
        self.assertTrue(state["cleanup_unconfirmed"])
        self.assertFalse(state["worker_done_confirmed"])
        self.assertEqual(state["cleanup_error"], "platform_chrome_worker_cleanup_unconfirmed")
        interrupts = [message for message in process.messages if message.get("method") == "turn/interrupt"]
        self.assertEqual([message["params"] for message in interrupts], [{
            "threadId": "ephemeral-thread", "turnId": "real-fake-wire-turn"}])
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(marker.read_text(), "do not retry")
        self.assertIn("cleanup_unconfirmed", [event["phase"] for event in state["diagnostics"]])

    def test_normal_model_completion_alone_does_not_confirm_worker_finish(self):
        self.cooperative_stop = False
        self.start()
        self.wait(lambda: self.state()["ready"])
        entry, process = next(iter(self.controller._workers.values())), self.processes[0]
        with patch.object(worker, "DISCONNECT_WAIT_SECONDS", .01):
            self.controller.disconnect(self.root, "synthetic-plan", "saved.json")
        process.finish()
        self.wait(entry["done"].is_set)
        self.assertFalse(self.state()["worker_done_confirmed"])
        self.assertTrue(self.state()["turn_completed_normally"])
        self.assertTrue(self.state()["cleanup_unconfirmed"])
        self.assertFalse(any(message.get("method") == "turn/interrupt" for message in process.messages))

    def test_finish_without_normal_turn_completion_has_its_own_bounded_window(self):
        self.cooperative_stop = False
        self.start()
        self.wait(lambda: self.state()["ready"])
        entry, process = next(iter(self.controller._workers.values())), self.processes[0]
        with patch.object(worker, "STOP_TURN_SECONDS", .08), patch.object(worker, "DISCONNECT_WAIT_SECONDS", .01):
            self.controller.disconnect(self.root, "synthetic-plan", "saved.json")
            process.checkpoint({"done": True, "state": "stopped", "phase": "cleanup", "processed": 0})
            self.wait(entry["done"].is_set)
        self.assertTrue(self.state()["worker_done_confirmed"])
        self.assertFalse(self.state()["turn_completed_normally"])
        self.assertTrue(self.state()["cleanup_unconfirmed"])
        self.assertEqual(sum(message.get("method") == "turn/interrupt" for message in process.messages), 1)

    def test_failed_done_checkpoint_still_waits_for_normal_turn_completion(self):
        self.cooperative_stop = False
        self.start()
        self.wait(lambda: self.state()["ready"])
        entry, process = next(iter(self.controller._workers.values())), self.processes[0]
        process.checkpoint({"done": True, "state": "failed", "phase": "cleanup", "processed": 1,
            "code": "platform_chrome_worker_cleanup_failed", "failure_phase": "cleanup"})
        self.wait(lambda: entry.get("worker_done_confirmed"))
        self.assertIsNone(entry["client"].failure, "a valid failed worker receipt is not a broken RPC transport")
        self.assertEqual(self.state()["state"], "stopping")
        self.assertFalse(entry["done"].is_set())
        self.assertIsNone(process.poll())
        self.assertFalse(any(message.get("method") == "turn/interrupt" for message in process.messages))
        process.finish()
        self.wait(entry["done"].is_set)
        view = self.state()
        self.assertEqual(view["error"], "platform_chrome_worker_cleanup_failed")
        self.assertTrue(view["worker_done_confirmed"])
        self.assertTrue(view["turn_completed_normally"])
        self.assertFalse(any(message.get("method") == "turn/interrupt" for message in process.messages))

    def test_unrelated_terminal_tool_receipt_cannot_shorten_cooperative_stop(self):
        self.cooperative_stop = False
        self.start()
        self.wait(lambda: self.state()["ready"])
        entry, process = next(iter(self.controller._workers.values())), self.processes[0]
        with patch.object(worker, "STOP_CHECKPOINT_SECONDS", .2), patch.object(worker, "DISCONNECT_WAIT_SECONDS", .01):
            self.controller.disconnect(self.root, "synthetic-plan", "saved.json")
            item = {"id": "unrelated-tool", "type": "mcpToolCall", "server": "node_repl", "tool": "js",
                "result": checkpoint_result({"done": True, "state": "stopped", "phase": "cleanup", "processed": 0})}
            for method, status in (("item/started", "inProgress"), ("item/completed", "completed")):
                process.stdout.put(owned_message(method, thread_id="another-chat", item={**item, "status": status}))
            time.sleep(.05)
            self.assertEqual(self.state()["state"], "stopping")
            self.assertIsNone(process.poll())
            self.assertFalse(self.state()["worker_done_confirmed"])
            self.wait(entry["done"].is_set)
        self.assertTrue(self.state()["cleanup_unconfirmed"])

    def test_natural_idle_finish_waits_for_turn_without_reusing_detached_health(self):
        self.cooperative_stop = False
        self.start()
        self.wait(lambda: self.state()["ready"])
        entry, process = next(iter(self.controller._workers.values())), self.processes[0]
        process.checkpoint({"done": True, "state": "idle_timeout", "phase": "cleanup", "processed": 0})
        self.wait(lambda: entry["client"].worker_done is not None)
        # The SDK worker removes health when finish runs. A terminal receipt,
        # rather than that now-expired lease, owns the lifecycle transition.
        self.core.health.clear()
        self.assertEqual(self.state()["state"], "stopping")
        process.finish()
        self.wait(entry["done"].is_set)
        self.assertIsNone(self.state()["error"])
        self.assertTrue(self.state()["worker_done_confirmed"])
        self.assertTrue(self.state()["turn_completed_normally"])

    def test_external_healthy_worker_reused_without_starting_model(self):
        binding, _ = self.core.binding(SimpleNamespace(root=self.root), "synthetic-plan", "saved.json")
        self.core.health[self.core.digest(binding)] = {"ready": True, "attached": True}
        result = self.start()
        self.assertTrue(result["ready"])
        self.assertFalse(result["owned"])
        self.assertEqual(self.processes, [])

    def test_external_unhealthy_lease_is_not_hijacked(self):
        binding, _ = self.core.binding(SimpleNamespace(root=self.root), "synthetic-plan", "saved.json")
        self.core.health[self.core.digest(binding)] = {"ready": False, "attached": True}
        with self.assertRaises(worker.WorkerError) as error:
            self.start()
        self.assertEqual(error.exception.code, "platform_chrome_worker_busy")
        self.assertEqual(self.processes, [])

    def test_wrong_book_identity_fails_before_model(self):
        with self.assertRaises(worker.WorkerError):
            self.controller.start(self.root, "plan", "profile", expected_book_id="other-book")
        self.assertEqual(self.processes, [])

    def test_other_platforms_are_rejected_before_runtime_context_or_model_start(self):
        original = self.core.binding
        for platform in ("qimao", "qidian"):
            def other_platform(book, plan, profile, **kwargs):
                binding, config = original(book, plan, profile, **kwargs)
                binding["target"]["platform"] = platform
                return binding, config
            with self.subTest(platform=platform), \
                    patch.object(self.core.platform_chrome_bridge, "_binding", side_effect=other_platform), \
                    self.assertRaises(worker.WorkerError) as caught:
                self.start()
            self.assertEqual(caught.exception.code, "platform_chrome_worker_unsupported_platform")
        self.assertEqual(self.contexts, [])
        self.assertEqual(self.processes, [])
        self.assertEqual(self.controller._workers, {})

    def test_fake_native_directory_pin_checks_identity_and_releases_handles(self):
        storage = self.root / ".story"
        with self.core._pinned_directory(storage) as directory:
            self.assertTrue(os.path.samestat(directory.identity, storage.stat()))
            self.core._verify_bound_directory(directory)
            (storage / "synthetic.txt").write_text("safe fixture", encoding="utf-8")
        moved = self.root / "released-storage"
        storage.rename(moved)
        storage.mkdir()
        # Both native handle cleanup and the frozen identity matter on Windows.
        with self.assertRaises(worker.WorkerError):
            self.core._verify_bound_directory(directory)
        self.assertEqual((moved / "synthetic.txt").read_text(encoding="utf-8"), "safe fixture")

    def test_changed_binding_is_rejected_before_app_server_launch(self):
        original = self.core.binding
        calls = []
        def changed(book, plan, profile, **kwargs):
            binding, config = original(book, plan, profile, **kwargs)
            calls.append(True)
            if len(calls) > 1:
                binding["manifest_sha256"] = "changed-after-start"
            return binding, config
        with patch.object(self.core.platform_chrome_bridge, "_binding", side_effect=changed):
            self.start()
            entry = next(iter(self.controller._workers.values()))
            self.wait(lambda: entry["done"].is_set())
        self.assertEqual(entry["error"], "platform_chrome_worker_binding_mismatch")
        self.assertEqual(self.processes, [])

    def test_binding_is_revalidated_after_both_directory_pins(self):
        original = self.core.binding
        calls = []
        def changed(book, plan, profile, **kwargs):
            binding, config = original(book, plan, profile, **kwargs)
            calls.append(True)
            if len(calls) > 2:
                binding["profile_sha256"] = "changed-while-taking-pins"
            return binding, config
        with patch.object(self.core.platform_chrome_bridge, "_binding", side_effect=changed):
            self.start()
            entry = next(iter(self.controller._workers.values()))
            self.wait(lambda: entry["done"].is_set())
        self.assertEqual(len(calls), 3)
        self.assertEqual(entry["error"], "platform_chrome_worker_binding_mismatch")
        self.assertEqual(self.processes, [])

    @unittest.skipIf(os.name == "nt", "POSIX symbolic link contract")
    def test_symlink_book_storage_is_rejected_before_app_server_launch(self):
        storage = self.root / ".story"
        actual = self.root / "other-storage"
        storage.rename(actual)
        storage.symlink_to(actual, target_is_directory=True)
        self.start()
        entry = next(iter(self.controller._workers.values()))
        self.wait(lambda: entry["done"].is_set())
        self.assertEqual(entry["error"], "platform_chrome_worker_invalid_context")
        self.assertEqual(self.processes, [])

    def test_only_bound_storage_and_owned_context_reach_fake_turn(self):
        self.start()
        self.wait(lambda: self.state()["ready"])
        turn = next(message["params"] for message in self.processes[0].messages if message.get("method") == "turn/start")
        self.assertEqual(turn["sandboxPolicy"]["writableRoots"], [str(self.root / ".story"), str(self.contexts[0])])
        self.assertNotIn(str(self.root), turn["sandboxPolicy"]["writableRoots"])
        self.assertIs(turn["sandboxPolicy"]["networkAccess"], False)

    def test_diagnostics_remain_bounded_and_do_not_store_extra_fields(self):
        self.start()
        self.wait(lambda: self.state()["ready"])
        entry = next(iter(self.controller._workers.values()))
        for index in range(100):
            self.controller._diagnose(entry, "worker_checkpoint", worker_state="ready", worker_phase="queue_wait",
                processed=index, body="PRIVATE_BODY", account_id="PRIVATE_ACCOUNT", path="PRIVATE_PATH")
        snapshot = json.loads((entry["context"] / "diagnostics.json").read_text())
        self.assertLessEqual(len(snapshot["events"]), worker.MAX_DIAGNOSTICS)
        self.assertNotIn("PRIVATE_", json.dumps(snapshot))
        self.assertEqual(self.state()["worker_phase"], "queue_wait")

    def test_cleanup_checkpoint_does_not_erase_first_sdk_failure_phase(self):
        self.health_mode = "lease-only"
        self.start()
        entry = next(iter(self.controller._workers.values()))
        self.wait(lambda: entry.get("client") and entry["client"].turn_id)
        value = {"done": True, "state": "failed", "phase": "cleanup", "processed": 0,
            "code": "platform_chrome_worker_runtime_unavailable", "failure_phase": "sdk_setup",
            "error_name": "Error", "error_marker": "missing_browser_service"}
        observe_tool(entry["client"], checkpoint_result(value))
        self.controller._diagnose(entry, "worker_checkpoint", worker_phase="cleanup")
        self.wait(lambda: entry["done"].is_set())
        view = self.state()
        self.assertEqual(view["failure_phase"], "sdk_setup")
        self.assertEqual(view["worker_phase"], "cleanup")
        self.assertEqual(view["error_marker"], "missing_browser_service")
        self.assertEqual(view["error"], "platform_chrome_worker_runtime_unavailable")

    def test_paused_failure_diagnostic_cannot_expose_error_or_terminal_before_specific_phase(self):
        self.health_mode = 'lease-only'
        self.start()
        entry = next(iter(self.controller._workers.values()))
        self.wait(lambda: entry.get('client') and entry['client'].turn_id)
        entered, release = threading.Event(), threading.Event()
        original = self.controller._diagnose
        value = {'done': True, 'state': 'failed', 'phase': 'cleanup', 'processed': 0,
                 'code': 'platform_chrome_worker_runtime_unavailable', 'failure_phase': 'sdk_setup',
                 'error_name': 'Error', 'error_marker': 'missing_browser_service'}
        failures = []
        def diagnose(current, phase, **fields):
            if phase == 'worker_checkpoint' and fields.get('failure_phase') == 'sdk_setup':
                entered.set()
                if not release.wait(5):
                    failures.append('diagnostic barrier was not released')
            return original(current, phase, **fields)
        def report():
            try:
                observe_tool(entry['client'], checkpoint_result(value))
            except BaseException as error:
                failures.append(error)
        with patch.object(self.controller, '_diagnose', side_effect=diagnose):
            thread = threading.Thread(target=report, daemon=True)
            thread.start()
            try:
                self.assertTrue(entered.wait(4), 'failure checkpoint did not reach diagnostic barrier')
                self.assertIsNone(entry['client'].worker_error)
                self.assertIsNone(entry['client'].worker_done)
                self.assertFalse(entry['client'].worker_done_event.is_set())
            finally:
                release.set()
                thread.join(5)
            self.assertFalse(thread.is_alive(), 'checkpoint reporter did not stop')
        self.assertEqual(failures, [])
        self.wait(lambda: entry['done'].is_set())
        view = self.state()
        self.assertEqual((view['failure_phase'], view['worker_phase']), ('sdk_setup', 'cleanup'))
        self.assertEqual(view['error_marker'], 'missing_browser_service')
        self.assertEqual(view['error'], value['code'])

    def test_cli_failure_diagnostics_reach_view_and_private_record_without_raw_content(self):
        self.health_mode = "lease-only"
        self.start()
        entry = next(iter(self.controller._workers.values()))
        self.wait(lambda: entry.get("client") and entry["client"].turn_id)
        value = {"done": True, "state": "failed", "phase": "cleanup", "processed": 0,
            "code": "platform_chrome_worker_cli_failed", "failure_phase": "bridge_attach",
            "cli_exit_code": 2, "cli_known_error": "io_or_input_error", "cli_errno": "EPERM"}
        observe_tool(entry["client"], checkpoint_result(value))
        self.wait(lambda: entry["done"].is_set())
        view = self.state()
        self.assertEqual((view["cli_exit_code"], view["cli_known_error"], view["cli_errno"]), (2, "io_or_input_error", "EPERM"))
        self.controller._diagnose(entry, "worker_checkpoint", cli_errno="PRIVATE_PATH", cli_known_error="PRIVATE_BODY", cli_exit_code=True,
            stdout="PRIVATE_STDOUT", stderr="PRIVATE_STDERR")
        snapshot = (entry["context"] / "diagnostics.json").read_text()
        self.assertNotIn("PRIVATE_", snapshot)
        self.assertEqual(self.state()["cli_errno"], "EPERM")

    def test_driver_failure_fields_reach_view_and_preserve_first_failure_without_raw_content(self):
        self.health_mode = "lease-only"
        self.start()
        entry = next(iter(self.controller._workers.values()))
        self.wait(lambda: entry.get("client") and entry["client"].turn_id)
        value = {"done": True, "state": "failed", "phase": "cleanup", "processed": 0,
            "code": "platform_chrome_worker_failed", "failure_phase": "queue_operation",
            "driver_phase": "all_rows", "driver_line": 123, "driver_column": 45}
        observe_tool(entry["client"], checkpoint_result(value))
        self.wait(lambda: entry["done"].is_set())
        view = self.state()
        self.assertEqual((view["driver_phase"], view["driver_line"], view["driver_column"]), ("all_rows", 123, 45))
        self.controller._diagnose(entry, "worker_checkpoint", worker_state="failed", driver_phase="read", driver_line=999,
            driver_column=999, stack="PRIVATE_STACK", path="PRIVATE_PATH", message="PRIVATE_BODY")
        self.controller._diagnose(entry, "worker_checkpoint", worker_state="ready", driver_phase="PRIVATE_PHASE",
            driver_line=True, driver_column=100001)
        self.assertEqual((self.state()["driver_phase"], self.state()["driver_line"], self.state()["driver_column"]),
            ("all_rows", 123, 45))
        snapshot = (entry["context"] / "diagnostics.json").read_text()
        self.assertNotIn("PRIVATE_", snapshot)
        self.assertIn('"driver_phase": "all_rows"', snapshot)

    def test_thread_start_failure_releases_slot_without_creating_child(self):
        with patch.object(worker.threading.Thread, "start", side_effect=RuntimeError("PRIVATE_FAILURE")):
            result = self.start()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"], "platform_chrome_worker_thread_start_failed")
        self.assertTrue(next(iter(self.controller._workers.values()))["done"].is_set())
        self.assertEqual(self.processes, [])

    def test_owned_receipt_cleans_original_worker_after_configuration_changes(self):
        receipt = self.start()
        self.wait(lambda: self.state()["ready"])
        with patch.object(self.core.platform_chrome_bridge, "_binding", side_effect=AssertionError("changed profile must not be reread")):
            result = self.controller.disconnect(self.root, "now-different-plan", "removed-profile.json",
                expected_book_id="local-book", owned_worker_id=receipt["owned_worker_id"])
        self.assertEqual(result["state"], "closed")
        self.assertIsNotNone(self.processes[0].poll())

    def test_receipt_cleanup_checks_root_and_book_and_never_stops_external_worker(self):
        receipt = self.start()
        self.wait(lambda: self.state()["ready"])
        for root, book_id in ((self.root / "other-root", "local-book"), (self.root, "other-book")):
            with self.subTest(root=root, book_id=book_id):
                result = self.controller.disconnect(root, "synthetic-plan", "saved.json",
                    expected_book_id=book_id, owned_worker_id=receipt["owned_worker_id"])
                self.assertFalse(result["owned"])
                self.assertIsNone(self.processes[0].poll())
        self.assertFalse(self.controller.disconnect(self.root, "synthetic-plan", "saved.json",
            expected_book_id="local-book", owned_worker_id="f"*32)["owned"])
        self.assertIsNone(self.processes[0].poll())


    def test_specific_checkpoint_cause_wins_before_client_error_flag_is_published(self):
        self.start();self.wait(lambda:self.state()["ready"])
        entry=list(self.controller._workers.values())[-1]
        self.assertIsNone(entry["client"].worker_error)
        self.controller._diagnose(entry,"worker_checkpoint",worker_state="failed",worker_phase="cleanup",
            failure_phase="existing_tab_probe",code="platform_chrome_worker_target_tab_changed")
        state=self.state()
        self.assertFalse(state["ready"])
        self.assertEqual(state["error"],"platform_chrome_worker_target_tab_changed")
        self.assertTrue(entry["stop"].is_set())

    def test_late_specific_checkpoint_refines_generic_health_failure(self):
        self.start();self.wait(lambda:self.state()["ready"])
        entry=list(self.controller._workers.values())[-1]
        with self.controller._lock:
            entry.update(state="failed",error="platform_chrome_worker_health_expired")
            entry["stop"].set()
        self.controller._diagnose(entry,"worker_checkpoint",worker_state="failed",worker_phase="cleanup",
            failure_phase="existing_tab_probe",code="platform_chrome_worker_target_tab_closed")
        self.assertEqual(self.state()["error"],"platform_chrome_worker_target_tab_closed")


class FileAndPromptTests(unittest.TestCase):
    def test_browser_plugin_sdk_is_not_misreported_as_supported_chrome_sdk(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(worker.os.environ, {"CODEX_HOME": temp}):
            version = Path(temp) / "plugins/cache/openai-bundled/browser/1.2.3"
            manifest = version / ".codex-plugin/plugin.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"author": {"name": "OpenAI"}}))
            module = version / "scripts/browser-client.mjs"
            module.parent.mkdir()
            module.write_text("export function setupBrowserRuntime() {}")
            with self.assertRaises(worker.WorkerError) as error:
                worker._sdk_path()
            self.assertEqual(error.exception.code, "platform_chrome_worker_sdk_unavailable")

    def test_stop_cleanup_can_only_become_stricter(self):
        from tests.test_publish import story
        with tempfile.TemporaryDirectory() as temp:
            entry = {"context": Path(temp).resolve(), "instance": "a"*32, "keep_readback": True}
            controller = worker.ChromeWorkerController(story.CORE)
            controller._signal_stop(entry)
            entry["keep_readback"] = False
            controller._signal_stop(entry)
            entry["keep_readback"] = True
            controller._signal_stop(entry)
            self.assertIs(json.loads((entry["context"] / "stop.json").read_text())["keep_readback"], False)
            self.assertIs(entry["keep_readback"], False)

    def test_diagnostic_snapshot_native_writer_is_private_and_bounded(self):
        from tests.test_publish import story
        with tempfile.TemporaryDirectory() as temp:
            context = Path(temp).resolve()
            snapshot = {"schema": 1, "events": [{"phase": "tool_started", "elapsed_ms": 1}]}
            worker._persist_diagnostics(story.CORE, context, snapshot)
            worker._persist_diagnostics(story.CORE, context, snapshot)
            path = context / "diagnostics.json"
            self.assertEqual(json.loads(path.read_text()), snapshot)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
    def test_sdk_is_resolved_from_selected_codex_home_and_cannot_escape_plugin(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(worker.os.environ, {"CODEX_HOME": temp}):
            version = Path(temp) / "plugins/cache/openai-bundled/chrome/1.2.3"
            manifest = version / ".codex-plugin/plugin.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"author": {"name": "OpenAI"}}))
            module = version / "scripts/browser-client.mjs"
            module.parent.mkdir()
            module.write_text("export function setupBrowserRuntime() {}")
            self.assertEqual(worker._sdk_path(), module.resolve())
            if os.name != "nt":
                other = Path(temp) / "other.mjs"
                other.write_text("untrusted module")
                module.unlink()
                module.symlink_to(other)
                with self.assertRaises(worker.WorkerError) as error:
                    worker._sdk_path()
                self.assertEqual(error.exception.code, "platform_chrome_worker_sdk_unavailable")

    def test_stop_receipt_is_idempotent_with_native_private_writer(self):
        from tests.test_publish import story
        with tempfile.TemporaryDirectory() as temp:
            entry = {"context": Path(temp).resolve(), "instance": "a"*32, "keep_readback": False}
            controller = worker.ChromeWorkerController(story.CORE)
            controller._signal_stop(entry)
            controller._signal_stop(entry)
            self.assertIs(json.loads((entry["context"] / "stop.json").read_text())["keep_readback"], False)

    def test_stop_receipt_rejects_other_worker_without_replacing_evidence(self):
        from tests.test_publish import story
        with tempfile.TemporaryDirectory() as temp:
            context = Path(temp).resolve()
            record = {"schema": 1, "worker_instance": "b"*32, "keep_readback": True}
            worker._write_private(context / "stop.json", record, core=story.CORE)
            with self.assertRaises(worker.WorkerError) as error:
                worker.ChromeWorkerController(story.CORE)._signal_stop({"context": context, "instance": "a"*32})
            self.assertEqual(error.exception.code, "platform_chrome_worker_unsafe_stop")
            self.assertEqual(json.loads((context / "stop.json").read_text()), record)

    def test_private_write_does_not_follow_link_or_replace_evidence(self):
        from tests.test_publish import story
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp).resolve() / "private.json"
            worker._write_private(path, {"value": "original"}, core=story.CORE)
            with self.assertRaises((FileExistsError, story.StoryError)):
                worker._write_private(path, {"value": "replacement"}, core=story.CORE)
            self.assertEqual(json.loads(path.read_text()), {"value": "original"})
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                link = Path(temp).resolve() / "link.json"
                link.symlink_to(path)
                with self.assertRaises((FileExistsError, story.StoryError)):
                    worker._write_private(link, {}, core=story.CORE)

    def test_prompt_uses_fixed_module_and_path_without_book_or_profile_content(self):
        text = worker._worker_prompt(Path("/official/story_chrome_worker.mjs").resolve(),
                                     Path("/private").resolve() / 'config-"quoted".json')
        self.assertIn("timeout_ms=420000", text)
        self.assertIn("never leave browser work running after a tool call returns", text)
        self.assertIn("Do not reimport", text)
        self.assertIn("Never repeat an individual queue operation", text)
        self.assertIn("https://fanqienovel.com", text)
        self.assertIn("A read-only queue never authorizes creating a draft", text)
        self.assertIn("does not assert that official browser access has been approved", text)
        self.assertIn("Honor every official authorization denial", text)
        self.assertIn('config-\\"quoted\\".json', text)
        self.assertNotIn("synthetic-account", text)
        self.assertNotIn("synthetic-remote-book", text)

    def test_availability_never_starts_model_or_creates_context(self):
        with patch.object(worker, "_codex_executable", return_value=Path("/official/codex")), \
             patch.object(worker, "_sdk_path", return_value=Path("/official/sdk.mjs")), \
             patch.object(worker, "_regular", side_effect=lambda path, **kwargs: Path(path)), \
             patch.object(worker.subprocess, "Popen", side_effect=AssertionError("no model")):
            self.assertEqual(worker.availability(), {"available": True, "uses_codex_quota": True})

    def test_missing_codex_is_specific_unavailable_reason(self):
        with patch.object(worker, "_codex_executable", side_effect=worker.WorkerError("platform_chrome_worker_codex_unavailable")):
            result = worker.availability()
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "platform_chrome_worker_codex_unavailable")


if __name__ == "__main__":
    unittest.main()
