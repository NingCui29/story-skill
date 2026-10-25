"""Owned, ephemeral Codex workers for the existing-Chrome draft bridge.

Only a real default-model turn can provide the official browser SDK's host
context. A lease or model message never establishes readiness: the bridge's
nonce health receipt and the owned app-server process must both remain valid.
This controller does not claim or replay queue requests and never launches a
browser. Its only launch is a fixed, resolved Codex app-server executable.
"""
from concurrent.futures import Future, TimeoutError as FutureTimeout
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid


CONNECT_SECONDS = 120
# Kept for older callers importing the constant; tool progress never extends
# the one deadline frozen by start().
CONNECT_PROGRESS_SECONDS = 120
IDLE_SECONDS = 120
STOP_CHECKPOINT_SECONDS = 400
# The awaited worker can finish before the official host emits turn/completed.
# Give that transport handshake a bounded minute; the former 10-second cutoff
# interrupted clean worker exits and reported unconfirmed cleanup.
STOP_TURN_SECONDS = 60
DISCONNECT_WAIT_SECONDS = 1
RESUME_SECONDS = 120
MAX_WORKERS = 4
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
MAX_DIAGNOSTICS = 64
MAX_EARLY_TURN_EVENTS = 16
MAX_EARLY_TURN_BYTES = 65536
CHECKPOINT_PREFIX = "STORY_CHROME_WORKER_CHECKPOINT "
CHECKPOINT_STATES = frozenset({"connecting", "ready", "busy", "stopped", "idle_timeout", "failed"})
TERMINAL_CHECKPOINT_STATES = frozenset({"stopped", "idle_timeout", "failed"})
WORKER_PHASES = frozenset({"config_validated", "sdk_setup", "browser_inventory", "browser_documentation",
    "waiting_for_chrome", "existing_tab_probe", "bridge_attach", "bridge_health", "queue_wait", "queue_operation", "cleanup"})
ERROR_NAMES = frozenset({"Error", "ReferenceError", "TypeError", "RangeError", "SyntaxError", "AggregateError",
    "URIError", "EvalError", "AbortError", "TimeoutError"})
ERROR_MARKERS = frozenset({"none", "browser_unattached", "missing_turn_metadata", "missing_browser_service",
    "missing_runtime_global", "origin_permission_required"})
DRIVER_PHASES = frozenset({"guard", "catalog", "listing", "pages", "all_rows", "read"})
RESULT_CODES = frozenset("platform_chrome_worker_" + value for value in (
    "ambiguous_browser", "ambiguous_tab", "binding_mismatch", "browser_changed", "browser_unattached", "cleanup_failed", "cli_failed", "config_changed",
    "detach_failed", "duplicate_request", "existing_tab_required", "target_tab_changed", "target_tab_closed", "failed", "handle_exists", "invalid_challenge", "connect_timeout", "browser_wait_timeout",
    "invalid_config", "invalid_packet", "invalid_scroll", "invalid_stop", "unavailable", "unsafe_file",
    "unsafe_user_tab", "unsupported_sdk", "turn_metadata_missing", "runtime_unavailable", "origin_permission_required")) | frozenset("platform_browser_" + value for value in (
    "ambiguous_dom", "ambiguous_draft", "chapter_exists_not_draft", "editor_changed", "identity_mismatch",
    "identity_unavailable", "incomplete_dom", "incomplete_list", "incomplete_scope", "invalid_chapter",
    "invalid_identity", "invalid_profile", "invalid_remote_id", "login_required", "navigation_timeout",
    "not_draft", "not_new_draft", "not_open", "operation_failed", "save_unverified", "target_unverified",
    "unnumbered_draft", "unsafe_create", "unsafe_navigation", "unsafe_save", "unsafe_url", "unsupported_editor",
    "bridge_scope", "bridge_expired", "editor_in_use", "readback_required"))
DIAGNOSTIC_CODES = RESULT_CODES | frozenset("platform_chrome_worker_" + value for value in (
    "tool_failed", "unexpected_tool", "host_context_missing", "debugger_unavailable", "module_unavailable",
    "disconnected", "invalid_message", "message_limit", "rpc_error", "rpc_timeout", "connect_timeout",
    "health_expired", "exited", "turn_failed", "not_ready", "unsafe_runtime", "unsafe_stop", "invalid_context",
    "thread_start_failed", "cleanup_unconfirmed"))
DIAGNOSTIC_PHASES = frozenset({"created", "app_server_started", "rpc_sent", "rpc_completed", "rpc_timeout",
    "rpc_failed", "model_turn_started", "model_turn_completed", "model_activity", "tool_started", "tool_completed",
    "tool_failed", "worker_checkpoint", "awaiting_health", "bridge_ready", "connection_failed", "stop_requested",
    "transport_closed", "closed", "connect_budget_extended", "worker_done_confirmed", "cleanup_unconfirmed",
    "resume_wait", "bridge_resumed"})
_CONTROLLER = None
_CONTROLLER_LOCK = threading.RLock()

CLI_ERRNOS = frozenset({"none", "EPERM", "EACCES", "EROFS", "ENOENT", "ENOTDIR", "ENOSPC", "EIO",
    "EMFILE", "ENFILE", "ETIMEDOUT"})
CLI_KNOWN_ERRORS = frozenset({"other", "io_or_input_error", "book_missing", "schema_mismatch", "invalid_input",
    "platform_chrome_invalid_handle", "platform_chrome_invalid_profile", "platform_chrome_unsafe_file",
    "platform_chrome_invalid_message", "platform_chrome_message_limit", "platform_chrome_worker_unavailable",
    "platform_chrome_worker_busy", "platform_chrome_session_limit", "platform_chrome_invalid_health",
    "platform_chrome_health_expired", "platform_chrome_worker_changed", "platform_chrome_already_claimed",
    "platform_chrome_request_expired", "platform_chrome_already_responded", "platform_chrome_request_timeout",
    "platform_draft_plan_unusable", "platform_draft_source_changed", "platform_draft_profile_mismatch",
    "platform_draft_invalid_profile_path", "platform_draft_invalid_profile_file", "platform_draft_profile_changed",
    "platform_draft_book_changed", "platform_draft_busy"})
RESULT_CODES |= frozenset(code for code in CLI_KNOWN_ERRORS if code.startswith("platform_chrome_"))
DIAGNOSTIC_CODES |= RESULT_CODES


class WorkerError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def inject(core):
    global api, _CONTROLLER
    api = core
    with _CONTROLLER_LOCK:
        _CONTROLLER = None


def _code(error):
    code = getattr(error, "code", None)
    return code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,99}", code) else "platform_chrome_worker_failed"


def _regular(path, *, executable=False):
    path = Path(path).expanduser().resolve(strict=True)
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or (os.name != "nt" and info.st_mode & 0o022) or
            (hasattr(os, "getuid") and info.st_uid not in (0, os.getuid())) or
            (executable and not os.access(path, os.X_OK))):
        raise WorkerError("platform_chrome_worker_unsafe_runtime")
    return path


def _codex_executable():
    """Prefer the installed desktop CLI; no executable comes from an HTTP form."""
    candidates = []
    if sys.platform == "darwin":
        for parent in (Path("/Applications"), Path.home() / "Applications"):
            for app in ("ChatGPT.app", "Codex.app"):
                candidates.append(parent / app / "Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex")
    located = shutil.which("codex")
    if located:
        candidates.append(Path(located))
    for candidate in candidates:
        try:
            path = _regular(candidate, executable=True)
            if path.name not in ("codex", "codex.exe", "codex-x86_64-apple-darwin", "codex-aarch64-apple-darwin"):
                continue
            result = subprocess.run([str(path), "--version"], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3, check=False)
            if result.returncode == 0 and re.fullmatch(rb"codex-cli [0-9][0-9A-Za-z.+_-]*\s*", result.stdout):
                return path
        except (OSError, ValueError, subprocess.SubprocessError, WorkerError):
            continue
    raise WorkerError("platform_chrome_worker_codex_unavailable")


def _sdk_path():
    codex_root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser().resolve()
    base = codex_root / "plugins/cache/openai-bundled"
    for name in ("chrome",):
        root = base / name
        if not root.is_dir() or root.is_symlink():
            continue
        versions = [entry for entry in root.iterdir() if re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}", entry.name)]
        if len(versions) > 100:
            raise WorkerError("platform_chrome_worker_unsafe_runtime")
        for version in sorted(versions, key=lambda item: tuple(map(int, item.name.split("."))), reverse=True):
            if version.is_symlink():
                continue
            try:
                manifest_path = _regular(version / ".codex-plugin/plugin.json")
                manifest_path.relative_to(version.resolve())
                if manifest_path.stat().st_size > 65536:
                    continue
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest.get("author", {}).get("name") == "OpenAI":
                    candidate = _regular(version / "scripts/browser-client.mjs")
                    candidate.relative_to(version.resolve())
                    return candidate
            except (OSError, ValueError, WorkerError):
                continue
    raise WorkerError("platform_chrome_worker_sdk_unavailable")


def _private_context(core):
    root = core.safe_path(Path.home().resolve(), ".story-skill/chrome-workers")
    with core._pinned_directory(root, create=True) as parent:
        if os.name == "nt":
            os.chmod(parent.path, 0o700)
        else:
            os.fchmod(parent.fd, 0o700)
        name = uuid.uuid4().hex
        if os.name == "nt":
            path = parent.path / name
            path.mkdir(mode=0o700)
        else:
            os.mkdir(name, mode=0o700, dir_fd=parent.fd)
            path = parent.path / name
        core._verify_bound_directory(parent)
    return path


def _write_private(path, value, *, core):
    """Use the bridge's native, parent-pinned 0600 no-replace writer."""
    return core.platform_chrome_bridge._external(path, create_value=value)


def _persist_diagnostics(core, context, snapshot):
    bridge = core.platform_chrome_bridge
    with bridge._pinned(context) as directory:
        bridge._write(directory, "diagnostics.json", snapshot, replace=True)


def _replace_stop(core, context, record):
    bridge = core.platform_chrome_bridge
    with bridge._pinned(context) as directory:
        bridge._write(directory, "stop.json", record, replace=True)


def _sandbox_policy(book_root, storage_root, context):
    """Grant only the validated local bridge storage and this owned context."""
    try:
        roots = tuple(Path(value) for value in (book_root, storage_root, context))
        book_root, storage_root, context = roots
        if (storage_root != book_root / ".story" or context == book_root or context == storage_root or
                context in book_root.parents or context in storage_root.parents or
                not re.fullmatch(r"[0-9a-f]{32}", context.name) or
                context.parent.name != "chrome-workers" or context.parent.parent.name != ".story-skill"):
            raise ValueError("Invalid worker storage scope")
        for index, path in enumerate(roots):
            info = path.lstat()
            if (not path.is_absolute() or path.resolve(strict=True) != path or not stat.S_ISDIR(info.st_mode) or
                    (os.name != "nt" and info.st_mode & (0o077 if index == 2 else 0o022)) or
                    (hasattr(os, "getuid") and info.st_uid not in ((os.getuid(),) if index == 2 else (0, os.getuid())))):
                raise ValueError("Unsafe worker storage directory")
    except (OSError, TypeError, ValueError):
        raise WorkerError("platform_chrome_worker_invalid_context") from None
    return {"type": "workspaceWrite", "writableRoots": [str(storage_root), str(context)],
            "networkAccess": False, "excludeSlashTmp": True, "excludeTmpdirEnvVar": True}


def _worker_prompt(module_path, config_path):
    first = ("var storyChromeWorkerModule = await import(" + json.dumps(Path(module_path).as_uri()) + ");\n"
             "var storyChromeWorkerCheckpoint = await storyChromeWorkerModule.runChromeWorker(" +
             json.dumps(str(config_path), ensure_ascii=False) + ");\nnodeRepl.write(" +
             json.dumps(CHECKPOINT_PREFIX) + " + JSON.stringify(storyChromeWorkerCheckpoint));")
    subsequent = ("var storyChromeWorkerCheckpoint = await storyChromeWorkerModule.runChromeWorker(" +
                  json.dumps(str(config_path), ensure_ascii=False) + ");\nnodeRepl.write(" +
                  json.dumps(CHECKPOINT_PREFIX) + " + JSON.stringify(storyChromeWorkerCheckpoint));")
    return ("Run the explicitly requested local Chrome draft worker using only the public node_repl JavaScript MCP tool. "
        "This consumes the user's configured Codex quota. Use their default model and effort. "
        "The initiating user's requested website is https://fanqienovel.com in their existing, signed-in Google Chrome. "
        "Their request is limited to the single account and work in the validated target binding, and to the selected frozen formal chapter batch. "
        "An authenticated update_draft request authorizes replacing the title and complete plain-text body of that existing unpublished draft after verifying its before-image. Read back independently; never update published chapters. "
        "They requested account/work identity checks and directory/draft reads. A create_draft request from the authenticated local queue "
        "conveys their explicit request to send those frozen chapter titles and bodies to that bound work as unpublished platform drafts, "
        "followed by independent full readback. A read-only queue never authorizes creating a draft. "
        "Never submit for review, publish, schedule, delete platform content, or change a published chapter. "
        "This describes the delegated user task; it does not assert that official browser access has been approved. "
        "Honor every official authorization denial and stop without trying another route. "
        "Do not run shell tools, read other files, edit manuscripts, create another task/chat, install anything, "
        "launch a browser/window, access cookies/profiles/private transports, change permissions, or invent observations. "
        "Only the fixed worker module may service its bound authenticated queue through documented public browser APIs. "
        "Call the JavaScript tool with timeout_ms=420000. A checkpoint awaits the current queue operation, "
        "including its health checks and cleanup, before returning; never leave browser work running after a tool call returns. "
        "The larger tool timeout allows the existing 300-second create deadline to finish without replaying a remote write. "
        "First call exactly:\n" + first +
        "\nIf done is false, continue calling the same already-imported module with exactly this code:\n" + subsequent +
        "\nRepeat checkpoints until done is true, then return only its stable state/error summary. "
        "Do not reimport the module or reset the REPL. Never repeat an individual queue operation or reconstruct a failed write. "
        "If the tool fails, stop and report a stable failure; do not retry or use a fallback. "
        "The module controls the 120-second no-job idle exit and finally cleanup. Never print book content, account IDs, or tokens.")


class AppServerClient:
    """Bounded JSONL transport for one owned app-server, never another chat."""
    METHODS = frozenset({"initialize", "thread/start", "turn/start", "turn/interrupt", "thread/unsubscribe"})

    def __init__(self, process, diagnostic=None):
        self.process = process
        self._lock, self._pending = threading.RLock(), {}
        self._close_lock = threading.Lock()
        self._sequence, self._dead = 0, False
        self.completed = None
        self.completed_event = threading.Event()
        self.worker_done = None
        self.worker_done_event = threading.Event()
        self.failure = None
        self.thread_id = None
        self.turn_id = None
        self.worker_error = None
        self.tool_started_at = None
        self.tool_sequence = 0
        self.last_checkpoint = None
        self.last_checkpoint_at = None
        self.last_checkpoint_epoch = None
        self.diagnostic = diagnostic
        self._model_activity_seen = False
        self._sandbox_roots = None
        self._sandbox_identity = None
        self._fixed_prompt = None
        self._pending_methods = {}
        self._early_turn_events = []
        self._early_turn_bytes = 0
        self._started_tools = set()
        self._worker_entered = False
        self._reader = threading.Thread(target=self._read, name="story-chrome-rpc", daemon=True)
        self._reader.start()

    def _emit(self, phase, **fields):
        if self.diagnostic:
            try:
                self.diagnostic(phase, **fields)
            except Exception:
                # Diagnostic storage never grants access or breaks RPC delivery.
                pass

    def _publish_worker_error(self, phase, **fields):
        # The supervisor may stop as soon as it observes this flag. Publish
        # the specific failure diagnostics before it can freeze a fallback.
        try:
            self._emit(phase, **fields)
        finally:
            self.worker_error = fields['code']

    def _checkpoint(self, result):
        """Only decode our exact tagged, bounded worker result; never DOM/text."""
        if not isinstance(result, dict):
            return None
        content = result.get("content", [])
        if not isinstance(content, list):
            return None
        for item in content[:8]:
            text = item.get("text") if isinstance(item, dict) else None
            if not isinstance(text, str) or len(text) > 8192:
                continue
            if not text.startswith(CHECKPOINT_PREFIX):
                continue
            try:
                value = json.loads(text[len(CHECKPOINT_PREFIX):])
            except (ValueError, TypeError):
                continue
            if (not isinstance(value, dict) or set(value) - {"done", "state", "processed", "code", "phase", "failure_phase", "error_name", "error_marker", "cli_exit_code", "cli_known_error", "cli_errno", "driver_phase", "driver_line", "driver_column"} or
                    type(value.get("done")) is not bool or not isinstance(value.get("state"), str) or value["state"] not in CHECKPOINT_STATES or
                    type(value.get("processed")) is not int or not 0 <= value["processed"] <= 10000 or
                    any(key in value and (not isinstance(value[key], str) or value[key] not in choices)
                        for key, choices in (("phase", WORKER_PHASES), ("failure_phase", WORKER_PHASES),
                                             ("error_name", ERROR_NAMES), ("error_marker", ERROR_MARKERS)))):
                continue
            # A terminal marker is meaningful only after the fixed worker's
            # awaited finish. Configuration rejection before it exists is the
            # sole legitimate terminal result without a cleanup phase.
            if value["done"] != (value["state"] in TERMINAL_CHECKPOINT_STATES):
                continue
            if value["done"] and value.get("phase") != "cleanup" and not (
                    value["state"] == "failed" and value.get("failure_phase") == "config_validated" and
                    value.get("phase") in (None, "config_validated") and value["processed"] == 0):
                continue
            if (any(key in value for key in ("cli_exit_code", "cli_known_error", "cli_errno")) and
                    (value["done"] is not True or value["state"] != "failed" or
                     ("cli_exit_code" in value and value["cli_exit_code"] is not None and
                      (type(value["cli_exit_code"]) is not int or not 0 <= value["cli_exit_code"] <= 255)) or
                     ("cli_known_error" in value and (not isinstance(value["cli_known_error"], str) or value["cli_known_error"] not in CLI_KNOWN_ERRORS)) or
                     ("cli_errno" in value and (not isinstance(value["cli_errno"], str) or value["cli_errno"] not in CLI_ERRNOS)))):
                continue
            if (any(key in value for key in ("driver_phase", "driver_line", "driver_column")) and
                    (value["done"] is not True or value["state"] != "failed" or
                     ("driver_phase" in value and (not isinstance(value["driver_phase"], str) or value["driver_phase"] not in DRIVER_PHASES)) or
                     any(key in value and (type(value[key]) is not int or not 1 <= value[key] <= 100000)
                         for key in ("driver_line", "driver_column")))):
                continue
            if "code" in value and (not isinstance(value["code"], str) or value["code"] not in RESULT_CODES):
                value["code"] = "platform_chrome_worker_tool_failed"
            return value
        return None

    def _tool_error(self, item):
        # Classify only established runtime errors. Raw exception text can
        # contain page content or credentials and is never retained.
        parts = []
        error = item.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            parts.append(error["message"][:8192])
        result = item.get("result")
        if isinstance(result, dict):
            content = result.get("content", [])
            if isinstance(content, list):
                parts.extend(part["text"][:8192] for part in content[:8]
                             if isinstance(part, dict) and isinstance(part.get("text"), str))
        name = "Error"
        for text in parts:
            candidate = text.split(":", 1)[0].strip()
            if candidate in ERROR_NAMES:
                name = candidate
                break
        fixed = (("Missing required Codex turn metadata", "turn_metadata_missing", "missing_turn_metadata"),
                 ("Debugger unattached", "browser_unattached", "browser_unattached"),
                 ("Browser use requires a trusted Node REPL browser service", "runtime_unavailable", "missing_browser_service"),
                 ("process is not defined", "runtime_unavailable", "missing_runtime_global"),
                 ("nodeRepl is not defined", "runtime_unavailable", "missing_runtime_global"),
                 ("Cannot find module", "module_unavailable", "none"),
                 ("ERR_MODULE_NOT_FOUND", "module_unavailable", "none"))
        for text in parts:
            for marker, code, error_marker in fixed:
                if marker in text:
                    return {"code": "platform_chrome_worker_" + code, "error_name": name, "error_marker": error_marker}
        return {"code": "platform_chrome_worker_tool_failed", "error_name": name, "error_marker": "none"}

    def _observe(self, message):
        method, params = message.get("method"), message.get("params", {})
        if not isinstance(params, dict):
            return
        # The transport can deliver notifications about other threads. Neither
        # those, assistant prose, nor a failed tool's tagged text proves finish.
        if (self.thread_id and not self.turn_id and params.get("threadId") == self.thread_id and
                "turn/start" in self._pending_methods.values() and
                method in ("turn/started", "turn/completed", "item/started", "item/completed")):
            size = len(json.dumps(message, ensure_ascii=False).encode("utf-8"))
            if len(self._early_turn_events) < MAX_EARLY_TURN_EVENTS and self._early_turn_bytes + size <= MAX_EARLY_TURN_BYTES:
                # Only keep a bounded in-memory copy until the real RPC reply
                # pins the turn. Every event is rechecked against that id.
                self._early_turn_events.append(message)
                self._early_turn_bytes += size
            else:
                self._publish_worker_error("tool_failed", code="platform_chrome_worker_invalid_message")
            return
        if not self.thread_id or not self.turn_id or params.get("threadId") != self.thread_id:
            return
        turn = params.get("turn", {})
        turn_id = turn.get("id") if method in ("turn/started", "turn/completed") and isinstance(turn, dict) else params.get("turnId")
        if turn_id != self.turn_id:
            return
        if method == "turn/started":
            self._emit("model_turn_started")
        elif method == "turn/completed":
            status = turn.get("status") if isinstance(turn, dict) else None
            self.completed = {"status": status if status in ("completed", "interrupted", "failed") else "failed"}
            self.completed_event.set()
            self._emit("model_turn_completed", turn_status=self.completed["status"])
        elif method in ("item/started", "item/completed"):
            item = params.get("item", {})
            if not isinstance(item, dict):
                return
            if item.get("type") == "agentMessage" and not self._model_activity_seen:
                self._model_activity_seen = True
                self._emit("model_activity")
            elif item.get("type") == "mcpToolCall":
                if (item.get("server"), item.get("tool")) != ("node_repl", "js"):
                    self._publish_worker_error("tool_failed", code="platform_chrome_worker_unexpected_tool")
                elif method == "item/started":
                    item_id = item.get("id")
                    if not isinstance(item_id, str) or not 0 < len(item_id) <= 256 or item.get("status") != "inProgress":
                        return
                    if item_id in self._started_tools:
                        return
                    self._started_tools.add(item_id)
                    self.tool_sequence += 1
                    self.tool_started_at = time.monotonic()
                    self._emit("tool_started")
                else:
                    item_id = item.get("id")
                    if not isinstance(item_id, str) or item_id not in self._started_tools:
                        return
                    self._started_tools.remove(item_id)
                    result = item.get("result")
                    failed = (item.get("error") is not None or item.get("status") != "completed" or
                              (isinstance(result, dict) and result.get("isError") is True))
                    if failed:
                        fields = self._tool_error(item)
                        self._publish_worker_error("tool_failed", **fields)
                        return
                    checkpoint = self._checkpoint(result)
                    if (checkpoint and checkpoint["done"] and checkpoint.get("phase") != "cleanup" and
                            self._worker_entered):
                        checkpoint = None
                    if checkpoint:
                        self.last_checkpoint = dict(checkpoint)
                        self.last_checkpoint_at = time.monotonic()
                        self.last_checkpoint_epoch = time.time()
                        if (checkpoint.get("phase") not in (None, "config_validated") or
                                checkpoint["state"] in ("ready", "busy") or checkpoint["processed"] > 0):
                            self._worker_entered = True
                        fields = {"worker_state": checkpoint["state"], "processed": checkpoint["processed"]}
                        if "phase" in checkpoint:
                            fields["worker_phase"] = checkpoint["phase"]
                        for key in ("failure_phase", "error_name", "error_marker"):
                            if key in checkpoint:
                                fields[key] = checkpoint[key]
                        for key in ("cli_exit_code", "cli_known_error", "cli_errno", "driver_phase", "driver_line", "driver_column"):
                            if key in checkpoint:
                                fields[key] = checkpoint[key]
                        if "code" in checkpoint:
                            fields["code"] = checkpoint["code"]
                        elif checkpoint["done"] and checkpoint["state"] == "failed":
                            fields["code"] = "platform_chrome_worker_failed"
                        if "code" in fields:
                            self._publish_worker_error("worker_checkpoint", **fields)
                        else:
                            self._emit("worker_checkpoint", **fields)
                        if checkpoint["done"] and self.worker_done is None:
                            self.worker_done = dict(checkpoint)
                            self.worker_done_event.set()
                    else:
                        self._publish_worker_error("tool_failed", code="platform_chrome_worker_tool_failed")
            elif item.get("type") in ("commandExecution", "fileChange", "dynamicToolCall"):
                self._publish_worker_error("tool_failed", code="platform_chrome_worker_unexpected_tool")

    def _write(self, message):
        if self._dead:
            raise WorkerError("platform_chrome_worker_disconnected")
        raw = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(raw) > MAX_MESSAGE_BYTES:
            raise WorkerError("platform_chrome_worker_message_limit")
        self.process.stdin.write(raw)
        self.process.stdin.flush()

    def _read(self):
        failure = WorkerError("platform_chrome_worker_disconnected")
        try:
            while True:
                raw = self.process.stdout.readline(MAX_MESSAGE_BYTES + 1)
                if not raw:
                    break
                if len(raw) > MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
                    raise WorkerError("platform_chrome_worker_message_limit")
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise WorkerError("platform_chrome_worker_invalid_message")
                if "method" in message:
                    if "id" in message:
                        with self._lock:
                            self._write({"id": message["id"], "error": {"code": -32601,
                                "message": "Worker does not grant additional actions"}})
                    else:
                        self._observe(message)
                    continue
                with self._lock:
                    future = self._pending.pop(message.get("id"), None)
                    rpc = self._pending_methods.pop(message.get("id"), None)
                if future is not None:
                    if "error" in message:
                        future.set_exception(WorkerError("platform_chrome_worker_rpc_error"))
                    elif "result" not in message:
                        future.set_exception(WorkerError("platform_chrome_worker_invalid_message"))
                    else:
                        # Publish the owned turn id before delivering its RPC
                        # result: a fast tool can emit notifications before the
                        # initialize caller is scheduled again.
                        result = message["result"]
                        if rpc == "turn/start" and isinstance(result, dict):
                            turn = result.get("turn", {})
                            if isinstance(turn, dict) and isinstance(turn.get("id"), str) and turn["id"]:
                                self.turn_id = turn["id"]
                                events, self._early_turn_events = self._early_turn_events, []
                                self._early_turn_bytes = 0
                                for event in events:
                                    self._observe(event)
                        future.set_result(message["result"])
        except Exception as error:
            failure = error if isinstance(error, WorkerError) else WorkerError("platform_chrome_worker_invalid_message")
        finally:
            self.failure = failure.code
            self._emit("transport_closed", code=failure.code)
            with self._lock:
                self._dead = True
                waiting, self._pending = self._pending, {}
                self._pending_methods = {}
                self._early_turn_events = []
                self._early_turn_bytes = 0
            for future in waiting.values():
                if not future.done():
                    future.set_exception(failure)

    def request(self, method, params, *, timeout=10):
        if method not in self.METHODS:
            raise WorkerError("platform_chrome_worker_method_forbidden")
        if method == "thread/start" and params.get("ephemeral") is not True:
            raise WorkerError("platform_chrome_worker_persistent_thread_forbidden")
        if method in ("thread/start", "turn/start") and any(key in params for key in ("model", "effort", "reasoningEffort")):
            raise WorkerError("platform_chrome_worker_model_override_forbidden")
        if ((method == "turn/interrupt" and (set(params) != {"threadId", "turnId"} or
                params.get("threadId") != self.thread_id or params.get("turnId") != self.turn_id or not self.turn_id)) or
                (method == "thread/unsubscribe" and (set(params) != {"threadId"} or
                params.get("threadId") != self.thread_id or not self.thread_id))):
            raise WorkerError("platform_chrome_worker_invalid_context")
        if method in ("thread/start", "turn/start"):
            if self._sandbox_roots is None:
                raise WorkerError("platform_chrome_worker_invalid_context")
            policy = _sandbox_policy(*self._sandbox_roots)
            identity = tuple((path.stat().st_dev, path.stat().st_ino) for path in self._sandbox_roots)
            if identity != self._sandbox_identity:
                raise WorkerError("platform_chrome_worker_invalid_context")
            if method == "thread/start":
                if (set(params) != {"cwd", "ephemeral", "approvalPolicy", "sandbox", "experimentalRawEvents", "developerInstructions"} or
                        params["cwd"] != str(self._sandbox_roots[2]) or params["approvalPolicy"] != "never" or
                        params["sandbox"] != "read-only" or params["experimentalRawEvents"] is not False):
                    raise WorkerError("platform_chrome_worker_invalid_context")
            elif (set(params) != {"threadId", "input", "sandboxPolicy"} or params["threadId"] != self.thread_id or
                    params["input"] != [{"type": "text", "text": self._fixed_prompt}] or params["sandboxPolicy"] != policy):
                raise WorkerError("platform_chrome_worker_invalid_context")
        with self._lock:
            self._sequence += 1
            number, future = self._sequence, Future()
            self._pending[number] = future
            self._pending_methods[number] = method
            try:
                self._emit("rpc_sent", rpc=method)
                self._write({"id": number, "method": method, "params": params})
            except Exception:
                self._pending.pop(number, None)
                self._pending_methods.pop(number, None)
                raise
        try:
            result = future.result(timeout=max(0.01, timeout))
            self._emit("rpc_completed", rpc=method)
            return result
        except FutureTimeout:
            with self._lock:
                self._pending.pop(number, None)
                self._pending_methods.pop(number, None)
            # No RPC, tool call, claimed write, or model turn is retried.
            self._emit("rpc_timeout", rpc=method, code="platform_chrome_worker_rpc_timeout")
            raise WorkerError("platform_chrome_worker_rpc_timeout")
        except WorkerError as error:
            self._emit("rpc_failed", rpc=method, code=error.code)
            raise

    def initialize(self, context, prompt, *, book_root, storage_root, deadline=None):
        def remaining():
            value = 10 if deadline is None else min(10, deadline - time.monotonic())
            if value <= 0:
                raise WorkerError("platform_chrome_worker_connect_timeout")
            return value
        remaining()
        if self._sandbox_roots is not None:
            raise WorkerError("platform_chrome_worker_invalid_context")
        policy = _sandbox_policy(book_root, storage_root, context)
        self._sandbox_roots = tuple(Path(value) for value in (book_root, storage_root, context))
        self._sandbox_identity = tuple((path.stat().st_dev, path.stat().st_ino) for path in self._sandbox_roots)
        self._fixed_prompt = prompt
        self.request("initialize", {"clientInfo": {"name": "story-chrome-worker", "version": "1"},
            "capabilities": {"experimentalApi": True, "explicitGatewayOauth": True}}, timeout=remaining())
        with self._lock:
            self._write({"method": "initialized", "params": {}})
        result = self.request("thread/start", {"cwd": str(context), "ephemeral": True,
            "approvalPolicy": "never", "sandbox": "read-only", "experimentalRawEvents": False,
            "developerInstructions": "Only execute the fixed, user-requested Chrome worker module via public node_repl/js. Do not use shell, other files, other tasks, model overrides, or arbitrary browser actions."}, timeout=remaining())
        thread = result.get("thread", {})
        if thread.get("ephemeral") is not True or thread.get("path") is not None or not isinstance(thread.get("id"), str):
            raise WorkerError("platform_chrome_worker_invalid_context")
        self.thread_id = thread["id"]
        result = self.request("turn/start", {"threadId": self.thread_id,
            "input": [{"type": "text", "text": prompt}], "sandboxPolicy": policy}, timeout=remaining())
        self.turn_id = result.get("turn", {}).get("id")
        if not isinstance(self.turn_id, str):
            raise WorkerError("platform_chrome_worker_invalid_context")

    def close(self):
        with self._close_lock:
            return self._close()

    def _close(self):
        if self.thread_id and self.turn_id and self.completed is None and self.process.poll() is None:
            try:
                self.request("turn/interrupt", {"threadId": self.thread_id, "turnId": self.turn_id}, timeout=2)
            except Exception:
                pass
        if self.thread_id and self.process.poll() is None:
            try:
                self.request("thread/unsubscribe", {"threadId": self.thread_id}, timeout=2)
            except Exception:
                pass
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        self._reader.join(1)


class ChromeWorkerController:
    def __init__(self, core):
        self.core = core
        self._lock = threading.RLock()
        self._workers = {}

    def _binding(self, root, plan_id, profile_id, expected_book_id=None, comparison_policy="strict", body_encoding="verbatim"):
        if not isinstance(comparison_policy, str) or comparison_policy not in ("strict", "fanqie_terminal_lf_v1"):
            raise WorkerError("platform_draft_invalid_comparison_policy")
        if not isinstance(body_encoding, str) or body_encoding not in ("verbatim", "markdown_paragraphs_v1", "markdown_paragraphs_v2", "plain_paragraphs_v1"):
            raise WorkerError("platform_draft_invalid_body_encoding")
        with self.core.platform_drafts._workbench_book(root, expected_book_id) as book:
            binding, _ = self.core.platform_chrome_bridge._binding(book, plan_id, profile_id,
                comparison_policy=comparison_policy, body_encoding=body_encoding)
            if (binding.get("comparison_policy", "strict") != comparison_policy or
                    comparison_policy != "strict" and binding.get("target", {}).get("platform") != "fanqie"):
                raise WorkerError("platform_chrome_worker_binding_mismatch")
            if (binding.get("body_encoding", "verbatim") != body_encoding or
                    body_encoding not in {"verbatim", "plain_paragraphs_v1"} and binding.get("target", {}).get("platform") != "fanqie"):
                raise WorkerError("platform_chrome_worker_binding_mismatch")
            return book.root, binding, self.core.platform_chrome_bridge._digest(binding)

    def _diagnose(self, entry, phase, **fields):
        if phase not in DIAGNOSTIC_PHASES:
            return
        with entry["diagnostic_lock"]:
            with self._lock:
                record = {"phase": phase, "elapsed_ms": max(0, round((time.monotonic()-entry["created"])*1000))}
                if fields.get("rpc") in AppServerClient.METHODS:
                    record["rpc"] = fields["rpc"]
                if fields.get("worker_state") in CHECKPOINT_STATES:
                    record["worker_state"] = fields["worker_state"]
                if fields.get("worker_phase") in WORKER_PHASES:
                    record["worker_phase"] = fields["worker_phase"]
                    entry["worker_phase"] = fields["worker_phase"]
                    if fields["worker_phase"] != "cleanup":
                        entry["last_active_worker_phase"] = fields["worker_phase"]
                for key, choices in (("failure_phase", WORKER_PHASES), ("error_name", ERROR_NAMES), ("error_marker", ERROR_MARKERS)):
                    if isinstance(fields.get(key), str) and fields[key] in choices:
                        record[key] = fields[key]
                        if not entry.get(key):
                            entry[key] = fields[key]
                if ("cli_exit_code" in fields and (fields["cli_exit_code"] is None or
                        (type(fields["cli_exit_code"]) is int and 0 <= fields["cli_exit_code"] <= 255))):
                    record["cli_exit_code"] = fields["cli_exit_code"]
                    entry.setdefault("cli_exit_code", fields["cli_exit_code"])
                for key, choices in (("cli_known_error", CLI_KNOWN_ERRORS), ("cli_errno", CLI_ERRNOS)):
                    if isinstance(fields.get(key), str) and fields[key] in choices:
                        record[key] = fields[key]
                        entry.setdefault(key, fields[key])
                if phase == "worker_checkpoint" and fields.get("worker_state") == "failed":
                    if isinstance(fields.get("driver_phase"), str) and fields["driver_phase"] in DRIVER_PHASES:
                        record["driver_phase"] = fields["driver_phase"]
                        entry.setdefault("driver_phase", fields["driver_phase"])
                    for key in ("driver_line", "driver_column"):
                        if type(fields.get(key)) is int and 1 <= fields[key] <= 100000:
                            record[key] = fields[key]
                            entry.setdefault(key, fields[key])
                if fields.get("turn_status") in ("completed", "interrupted", "failed"):
                    record["turn_status"] = fields["turn_status"]
                if type(fields.get("processed")) is int and 0 <= fields["processed"] <= 10000:
                    record["processed"] = fields["processed"]
                if "code" in fields:
                    record["code"] = fields["code"] if fields["code"] in DIAGNOSTIC_CODES else "platform_chrome_worker_failed"
                    if phase == "tool_failed" or (phase == "worker_checkpoint" and fields.get("worker_state") == "failed"):
                        # Publish the bounded cause under the same lock as the
                        # checkpoint. Health polling may race the client's flag.
                        entry.setdefault("worker_failure", record["code"])
                        if entry.get("error") in (None, "platform_chrome_worker_health_expired", "platform_chrome_worker_unavailable"):
                            entry["error"] = entry["worker_failure"]
                entry["diagnostics"].append(record)
                del entry["diagnostics"][:-MAX_DIAGNOSTICS]
                if phase not in ("stop_requested", "transport_closed", "closed", "connection_failed", "worker_done_confirmed", "cleanup_unconfirmed") and \
                        fields.get("rpc") not in ("turn/interrupt", "thread/unsubscribe"):
                    entry["connection_phase"] = phase
                snapshot = {"schema": 1, "events": [dict(item) for item in entry["diagnostics"]]}
            try:
                _persist_diagnostics(self.core, entry["context"], snapshot)
            except Exception:
                with self._lock:
                    entry["diagnostic_storage_failed"] = True

    def _view(self, entry):
        pending = (entry["stop"].is_set() or entry["state"] == "stopping") and not entry["done"].is_set()
        state = "stopping" if pending else ("closed" if entry["state"] == "disconnected" else entry["state"])
        waiting = self._bootstrap_hint(entry)
        resuming = entry["state"] == "resuming" and not pending
        return {"ok": True, "state": state, "ready": entry["state"] == "ready" and not pending,
                "owned": True, "uses_codex_quota": True, "binding_sha256": entry["fingerprint"],
                "owned_worker_id": entry["instance"],
                "connection_phase": entry.get("connection_phase", "created"),
                "worker_phase": "awaiting_checkpoint" if resuming else (waiting or entry.get("worker_phase")),
                "failure_phase": entry.get("failure_phase"), "resume_pending": resuming,
                "resume_remaining_seconds": max(0, round(entry.get("resume_deadline", 0) - time.monotonic(), 1)) if resuming else 0,
                "waiting_for_chrome": waiting is not None,
                "error_name": entry.get("error_name"), "error_marker": entry.get("error_marker"),
                "cli_exit_code": entry.get("cli_exit_code"), "cli_known_error": entry.get("cli_known_error"),
                "cli_errno": entry.get("cli_errno"),
                "driver_phase": entry.get("driver_phase"), "driver_line": entry.get("driver_line"),
                "driver_column": entry.get("driver_column"),
                "diagnostics": [dict(item) for item in entry.get("diagnostics", [])],
                "diagnostic_storage_failed": entry.get("diagnostic_storage_failed", False),
                "cleanup_pending": pending, "worker_done_confirmed": entry.get("worker_done_confirmed", False),
                "cleanup_unconfirmed": entry.get("cleanup_unconfirmed", False),
                "turn_completed_normally": entry.get("turn_completed_normally", False),
                "cleanup_error": entry.get("cleanup_error"),
                "error": entry.get("error"), "connect_timeout_seconds": entry.get("connect_timeout_seconds", CONNECT_SECONDS),
                "connect_base_timeout_seconds": entry.get("connect_timeout_seconds", CONNECT_SECONDS),
                "connect_budget_extended": False,
                "idle_timeout_seconds": IDLE_SECONDS, "platform_target_verified": False}

    def _bootstrap_hint(self, entry):
        """Read a bounded display hint; it never establishes connection health."""
        client, process = entry.get("client"), entry.get("process")
        deadline = entry.get("connect_deadline")
        if (type(deadline) not in (int, float) or not deadline > 0 or
                entry["state"] != "connecting" or entry["stop"].is_set() or entry["done"].is_set() or
                not client or not client.turn_id or client.failure or client.completed or client.worker_done or
                not process or process.poll() is not None or time.monotonic() >= deadline):
            return None
        try:
            context = entry["context"]
            parent = context.lstat()
            if (context.resolve(strict=True) != context or not stat.S_ISDIR(parent.st_mode) or
                    (parent.st_dev, parent.st_ino) != entry["context_identity"] or
                    (os.name != "nt" and parent.st_mode & 0o077) or
                    (hasattr(os, "getuid") and parent.st_uid != os.getuid())):
                return None
            path = context / "bootstrap-state.json"
            before = path.lstat()
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 4096 or
                    getattr(before, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400) or
                    (os.name != "nt" and before.st_mode & 0o077) or
                    (hasattr(os, "getuid") and before.st_uid != os.getuid())):
                return None
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(descriptor, "rb") as stream:
                opened = os.fstat(stream.fileno())
                raw = stream.read(4097)
                after_read = os.fstat(stream.fileno())
            after, final_parent = path.lstat(), context.lstat()
            if (len(raw) > 4096 or not os.path.samestat(before, opened) or not os.path.samestat(before, after) or
                    any(info.st_nlink != 1 or info.st_mode != before.st_mode or info.st_uid != before.st_uid
                        for info in (opened, after_read, after)) or
                    before.st_size != opened.st_size or before.st_size != after.st_size or
                    before.st_mtime_ns != opened.st_mtime_ns or before.st_mtime_ns != after.st_mtime_ns or
                    opened.st_size != after_read.st_size or opened.st_mtime_ns != after_read.st_mtime_ns or
                    not os.path.samestat(parent, final_parent) or parent.st_mode != final_parent.st_mode or
                    parent.st_uid != final_parent.st_uid):
                return None
            value = json.loads(raw.decode("utf-8"))
            now = int(time.time() * 1000)
            if (not isinstance(value, dict) or set(value) != {"schema", "worker_instance", "binding_sha256", "phase", "observed_at_ms", "bootstrap_deadline_ms"} or
                    type(value.get("schema")) is not int or value["schema"] != 1 or
                    value.get("worker_instance") != entry["instance"] or value.get("binding_sha256") != entry["fingerprint"] or
                    value.get("phase") != "waiting_for_chrome" or type(value.get("observed_at_ms")) is not int or
                    type(value.get("bootstrap_deadline_ms")) is not int or value["bootstrap_deadline_ms"] != entry.get("bootstrap_deadline_ms") or
                    not entry["bootstrap_config_created_ms"] <= value["observed_at_ms"] < value["bootstrap_deadline_ms"] or
                    not -1000 <= now - value["observed_at_ms"] <= 25000):
                return None
            return "waiting_for_chrome"
        except (OSError, ValueError, UnicodeError, TypeError, KeyError, RecursionError):
            # Missing/stale/malformed hints only remove the UI hint. The nonce
            # health proof and the frozen connection deadline stay authoritative.
            return None

    def _owned_health_status(self, entry):
        process = entry.get("process")
        client = entry.get("client")
        if (not process or process.poll() is not None or not client or client.process is not process or
                not client.turn_id or (client.thread_id, client.turn_id) != entry.get("owned_turn") or
                client.completed is not None or client.failure or client.worker_error or client.worker_done is not None or
                self.core.platform_chrome_bridge._digest(entry["binding"]) != entry["fingerprint"]):
            return None
        status = self.core.platform_chrome_bridge.worker_status(entry["root"], entry["binding"])
        browser = status.get("browser_id")
        if (status.get("attached") is not True or status.get("worker_pid") != process.pid or status.get("worker_instance") != entry["instance"] or
                not isinstance(browser, str) or not browser or
                (entry.get("browser_id") is not None and browser != entry["browser_id"])):
            return None
        if status.get("ready") is True and entry.get("browser_id") is None:
            entry["browser_id"] = browser
        return status

    def _health(self, entry):
        status = self._owned_health_status(entry)
        return bool(status and status.get("ready") is True)

    @staticmethod
    def _health_times(status):
        try:
            values = []
            for key in ("health_checked_at", "health_expires_at"):
                text = status[key]
                if not isinstance(text, str) or len(text) > 64:
                    return False
                value = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if value.tzinfo is None:
                    return False
                values.append(value.timestamp())
            return tuple(values)
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    @classmethod
    def _expired_health(cls, status, checkpoint_epoch=None):
        if (not isinstance(status, dict) or status.get("attached") is not True or
                status.get("state") != "health_expired" or status.get("health_state") != "expired"):
            return False
        values = cls._health_times(status)
        return bool(values and values[0] < values[1] <= time.time() and
                    (checkpoint_epoch is None or values[0] <= checkpoint_epoch < values[1]))

    def _refresh_connection(self, entry):
        """One transition shared by polling and the supervisor; caller holds _lock.

        An expired proof stays expired. Only the owned turn's single next tool
        call may obtain a fresh nonce probe during an already approved gap.
        No lease, request deadline, or browser permission is changed here.
        """
        client = entry.get("client")
        worker_failure = entry.get("worker_failure") or (client.worker_error if client else None)
        if worker_failure:
            entry.update(state="failed", error=worker_failure, keep_readback=False)
            entry["stop"].set()
            return ("connection_failed", {"code": worker_failure})
        if client and client.worker_done is not None:
            entry["state"] = "stopping"
            entry["stop"].set()
            return None
        status = self._owned_health_status(entry)
        now = time.monotonic()
        healthy = bool(status and status.get("ready") is True)
        if entry["state"] == "resuming":
            allowed = (client is not None and now < entry["resume_deadline"] and
                       client.tool_sequence <= entry["resume_next_tool_sequence"])
            times = self._health_times(status) if status else None
            if (allowed and healthy and client.tool_sequence == entry["resume_next_tool_sequence"] and
                    times and times[0] > entry["resume_health_times"][0]):
                entry["state"] = "ready"
                return ("bridge_resumed", {})
            if allowed and self._expired_health(status) and times == entry["resume_health_times"]:
                return None
        elif healthy:
            return None
        elif (client and entry.get("browser_id") is not None and client.last_checkpoint_epoch is not None and
                self._expired_health(status, client.last_checkpoint_epoch) and
                not client._started_tools and isinstance(client.last_checkpoint, dict) and
                client.last_checkpoint.get("done") is False and client.last_checkpoint.get("state") == "ready" and
                client.last_checkpoint.get("phase") == "queue_wait" and client.last_checkpoint_at is not None and
                0 <= now - client.last_checkpoint_at < RESUME_SECONDS):
            entry.update(state="resuming", resume_deadline=client.last_checkpoint_at + RESUME_SECONDS,
                         resume_next_tool_sequence=client.tool_sequence + 1, resume_health_times=self._health_times(status))
            return ("resume_wait", {})
        code = (client.worker_error if client and client.worker_error else
                client.failure if client and client.failure else "platform_chrome_worker_health_expired")
        entry.update(state="failed", error=code, keep_readback=False)
        entry["stop"].set()
        return ("connection_failed", {"code": code})

    def start(self, root, plan_id, profile_id, *, expected_book_id=None, comparison_policy="strict", body_encoding="verbatim"):
        created = time.monotonic()
        root, binding, fingerprint = self._binding(root, plan_id, profile_id, expected_book_id, comparison_policy, body_encoding)
        # The owned worker and its fixed prompt only implement Fanqie. Reject
        # other host profiles before creating a context or consuming quota.
        if binding["target"]["platform"] != "fanqie":
            raise WorkerError("platform_chrome_worker_unsupported_platform")
        key = (str(root), fingerprint)
        with self._lock:
            if any(not entry["done"].is_set() and (entry["stop"].is_set() or entry["state"] in ("stopping", "resuming")) and
                    entry["binding"]["target"] == binding["target"] for entry in self._workers.values()):
                raise WorkerError("platform_chrome_worker_busy")
            old = self._workers.get(key)
            if old and not old["done"].is_set():
                return self._view(old)
            external = self.core.platform_chrome_bridge.worker_status(root, binding)
            if external.get("ready") is True:
                return {"ok": True, "state": "ready", "ready": True, "owned": False,
                        "uses_codex_quota": False, "platform_target_verified": False}
            if external.get("attached") is True:
                raise WorkerError("platform_chrome_worker_busy")
            if sum(entry["state"] in ("connecting", "ready", "stopping", "resuming") for entry in self._workers.values()) >= MAX_WORKERS:
                raise WorkerError("platform_chrome_worker_limit")
            cli, sdk = _codex_executable(), _sdk_path()
            context = _private_context(self.core)
            context_info = context.lstat()
            entry = {"root": root, "binding": binding, "fingerprint": fingerprint,
                     "instance": uuid.uuid4().hex, "context": context, "state": "connecting",
                     "stop": threading.Event(), "done": threading.Event(), "created": created,
                     "connect_deadline": created + CONNECT_SECONDS, "connect_timeout_seconds": CONNECT_SECONDS,
                     "context_identity": (context_info.st_dev, context_info.st_ino),
                     "process": None, "client": None, "diagnostics": [], "diagnostic_lock": threading.RLock(),
                     "keep_readback": False}
            self._workers[key] = entry
            self._diagnose(entry, "created")
            worker = threading.Thread(target=self._supervise, args=(entry, cli, sdk), name="story-chrome-worker", daemon=True)
            entry["thread"] = worker
            try:
                worker.start()
            except Exception:
                entry.update(state="failed", error="platform_chrome_worker_thread_start_failed")
                entry["stop"].set()
                entry["done"].set()
                self._diagnose(entry, "connection_failed", code="platform_chrome_worker_thread_start_failed")
            return self._view(entry)

    connect = start

    def status(self, root, plan_id, profile_id, *, expected_book_id=None, comparison_policy="strict", body_encoding="verbatim"):
        root, binding, fingerprint = self._binding(root, plan_id, profile_id, expected_book_id, comparison_policy, body_encoding)
        transition = None
        view = None
        with self._lock:
            entry = self._workers.get((str(root), fingerprint))
            if entry:
                if entry["state"] in ("ready", "resuming"):
                    transition = self._refresh_connection(entry)
                view = self._view(entry)
        if view is not None:
            if transition:
                self._diagnose(entry, transition[0], **transition[1])
            return view
        external = self.core.platform_chrome_bridge.worker_status(root, binding)
        return {"ok": True, "state": "ready" if external.get("ready") else "closed",
                "ready": external.get("ready") is True, "owned": False, "uses_codex_quota": False,
                "platform_target_verified": False}

    def disconnect(self, root, plan_id, profile_id, *, expected_book_id=None, keep_readback=False,
                   binding_sha256=None, owned_worker_id=None, comparison_policy="strict", body_encoding="verbatim"):
        if not isinstance(comparison_policy, str) or comparison_policy not in ("strict", "fanqie_terminal_lf_v1"):
            raise WorkerError("platform_draft_invalid_comparison_policy")
        if not isinstance(body_encoding, str) or body_encoding not in ("verbatim", "markdown_paragraphs_v1", "markdown_paragraphs_v2", "plain_paragraphs_v1"):
            raise WorkerError("platform_draft_invalid_body_encoding")
        if type(keep_readback) is not bool:
            raise WorkerError("platform_chrome_worker_invalid_input")
        if owned_worker_id is not None and (not isinstance(owned_worker_id, str) or
                not re.fullmatch(r"[0-9a-f]{32}", owned_worker_id) or not isinstance(expected_book_id, str)):
            raise WorkerError("platform_chrome_worker_invalid_input")
        # A profile can be replaced or removed while a worker is connecting.
        # Cleanup uses only our immutable start receipt, never a recomputed
        # binding which could strand the original provider or stop an outsider.
        root = Path(os.path.abspath(Path(root).expanduser()))
        with self._lock:
            entries = [entry for entry in self._workers.values() if str(entry["root"]) == str(root) and
                ((owned_worker_id is not None and entry["instance"] == owned_worker_id) or
                 (owned_worker_id is None and entry["binding"]["plan_id"] == plan_id and entry["binding"]["profile_id"] == profile_id)) and
                (owned_worker_id is not None or entry["binding"].get("comparison_policy", "strict") == comparison_policy) and
                (owned_worker_id is not None or entry["binding"].get("body_encoding", "verbatim") == body_encoding) and
                (expected_book_id is None or entry["binding"]["book_id"] == expected_book_id) and
                (binding_sha256 is None or entry["fingerprint"] == binding_sha256)]
            if not entries:
                return {"ok": True, "state": "closed", "ready": False, "owned": False}
            for entry in entries:
                entry["stop"].set()
                entry["keep_readback"] = keep_readback
                if entry["state"] in ("connecting", "ready", "resuming"):
                    entry["state"] = "stopping"
                try:
                    self._signal_stop(entry)
                except OSError:
                    pass
        # The supervisor owns the bounded cooperative stop and its fallback.
        # A UI request must not interrupt an SDK cleanup after a fixed 2–3s.
        deadline = time.monotonic() + DISCONNECT_WAIT_SECONDS
        for entry in entries:
            entry["done"].wait(max(0, deadline - time.monotonic()))
        with self._lock:
            pending = any(not entry["done"].is_set() for entry in entries)
            result = {**self._view(entries[-1]), "stopped_workers": len(entries), "cleanup_pending": pending}
            if pending:
                result.update(state="stopping", ready=False)
            return result

    def _signal_stop(self, entry):
        path = entry["context"] / "stop.json"
        with entry.setdefault("stop_lock", threading.RLock()):
            desired = entry.get("keep_readback", False) is True
            record = {"schema": 1, "worker_instance": entry["instance"], "keep_readback": desired}
            try:
                _write_private(path, record, core=self.core)
            except Exception:
                # False is sticky. A later stricter cleanup replaces only this
                # worker's authenticated private receipt; it never upgrades it.
                try:
                    existing = self.core.platform_chrome_bridge._external(path)
                except Exception:
                    raise WorkerError("platform_chrome_worker_unsafe_stop")
                if (not isinstance(existing, dict) or set(existing) != {"schema", "worker_instance", "keep_readback"} or
                        existing.get("schema") != 1 or existing.get("worker_instance") != entry["instance"] or
                        type(existing.get("keep_readback")) is not bool):
                    raise WorkerError("platform_chrome_worker_unsafe_stop")
                desired = desired and existing["keep_readback"]
                if existing["keep_readback"] and not desired:
                    _replace_stop(self.core, entry["context"], {**record, "keep_readback": False})
            entry["keep_readback"] = desired

    def _detach(self, entry):
        handle = entry["context"] / "handle.json"
        if not handle.exists():
            return
        # Detach only this authenticated handle; never delete queue/history or
        # reinterpret a claimed write as safe to resend.
        try:
            with self.core.platform_drafts._workbench_book(entry["root"], entry["binding"]["book_id"]) as book:
                self.core.platform_chrome_bridge.heartbeat(book, handle, detach=True)
        except Exception:
            pass

    def _wait_for_finish(self, entry, client, process):
        """Await actual worker finish, then normal owned-turn termination.

        This runs only in the supervisor thread. A successful terminal tool
        receipt proves that the worker's awaited finish returned, not that all
        browser tabs disappeared (the SDK may report close failures separately).
        A missing receipt never authorizes replay of a claimed operation.
        """
        if not client or not client.turn_id:
            return
        deadline = time.monotonic() + STOP_CHECKPOINT_SECONDS
        while (client.worker_done is None and client.completed is None and not client.failure and
                process and process.poll() is None and time.monotonic() < deadline):
            client.worker_done_event.wait(min(.05, max(0, deadline - time.monotonic())))
        confirmed = client.worker_done is not None
        with self._lock:
            entry["worker_done_confirmed"] = confirmed
        if confirmed:
            self._diagnose(entry, "worker_done_confirmed")
            # Allow the official session to terminate normally, which a tool's
            # own finish marker cannot establish. Do not wait for model prose.
            deadline = time.monotonic() + STOP_TURN_SECONDS
            while (client.completed is None and not client.failure and process and process.poll() is None and
                    time.monotonic() < deadline):
                client.completed_event.wait(min(.05, max(0, deadline - time.monotonic())))
        normal = bool(client.completed and client.completed.get("status") == "completed")
        with self._lock:
            entry["turn_completed_normally"] = normal
            if not confirmed or not normal:
                entry.update(cleanup_unconfirmed=True, cleanup_error="platform_chrome_worker_cleanup_unconfirmed")
        if not confirmed or not normal:
            self._diagnose(entry, "cleanup_unconfirmed", code="platform_chrome_worker_cleanup_unconfirmed")

    @contextmanager
    def _validated_storage(self, entry):
        """Recheck the frozen book/plan binding before granting local writes."""
        root, _, fingerprint = self._binding(entry["root"], entry["binding"]["plan_id"],
            entry["binding"]["profile_id"], entry["binding"]["book_id"], entry["binding"].get("comparison_policy", "strict"),
            entry["binding"].get("body_encoding", "verbatim"))
        if root != entry["root"] or fingerprint != entry["fingerprint"]:
            raise WorkerError("platform_chrome_worker_binding_mismatch")
        storage_root = self.core.safe_path(root, ".story")
        _sandbox_policy(root, storage_root, entry["context"])
        with self.core._pinned_directory(storage_root) as storage, self.core._pinned_directory(entry["context"]) as context:
            _sandbox_policy(root, storage.path, context.path)
            checked_root, _, checked_fingerprint = self._binding(root, entry["binding"]["plan_id"],
                entry["binding"]["profile_id"], entry["binding"]["book_id"], entry["binding"].get("comparison_policy", "strict"),
                entry["binding"].get("body_encoding", "verbatim"))
            if checked_root != root or checked_fingerprint != entry["fingerprint"]:
                raise WorkerError("platform_chrome_worker_binding_mismatch")
            self.core._verify_bound_directory(storage)
            self.core._verify_bound_directory(context)
            yield storage.path
            self.core._verify_bound_directory(storage)
            self.core._verify_bound_directory(context)

    def _supervise(self, entry, cli, sdk):
        process, client = None, None
        try:
            if entry["stop"].is_set():
                return
            script = _regular(Path(__file__).with_name("story.py"))
            module = _regular(Path(__file__).with_suffix(".mjs"))
            driver = _regular(Path(__file__).with_name("story_chrome_bridge.mjs"))
            python = _regular(sys.executable, executable=True)
            with self._validated_storage(entry) as storage_root:
                remaining = entry["connect_deadline"] - time.monotonic()
                if remaining <= 0:
                    raise WorkerError("platform_chrome_worker_connect_timeout")
                # The epoch deadline crosses into the SDK's Date.now clock;
                # derive it from the remaining original monotonic budget once.
                # Neither model startup nor a later checkpoint can reset it.
                entry["bootstrap_config_created_ms"] = int(time.time() * 1000)
                entry["bootstrap_deadline_ms"] = entry["bootstrap_config_created_ms"] + int(remaining * 1000)
                process = subprocess.Popen([str(cli), "app-server", "--stdio"], cwd=entry["context"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True)
                entry["process"] = process
                self._diagnose(entry, "app_server_started")
                config = {"schema": 1, "book_root": str(entry["root"]), "book_id": entry["binding"]["book_id"],
                    "plan_id": entry["binding"]["plan_id"], "profile_id": entry["binding"]["profile_id"],
                    "binding_sha256": entry["fingerprint"], "target": entry["binding"]["target"],
                    "comparison_policy": entry["binding"].get("comparison_policy", "strict"),
                    "body_encoding": entry["binding"].get("body_encoding", "verbatim"),
                    "worker": {"pid": process.pid, "instance": entry["instance"]},
                    "handle_path": str(entry["context"] / "handle.json"), "stop_path": str(entry["context"] / "stop.json"),
                    "python_executable": str(python), "story_cli": str(script), "sdk_path": str(sdk), "driver_path": str(driver),
                    "health_interval_seconds": 15, "idle_timeout_seconds": IDLE_SECONDS, "checkpoint_ms": 20000,
                    "poll_interval_ms": 250, "browser_id": None,
                    "bootstrap_deadline_ms": entry["bootstrap_deadline_ms"]}
                config_path = entry["context"] / "config.json"
                _write_private(config_path, config, core=self.core)
                client = AppServerClient(process, diagnostic=lambda phase, **fields: self._diagnose(entry, phase, **fields))
                entry["client"] = client
                client.initialize(entry["context"], _worker_prompt(module, config_path), book_root=entry["root"],
                    storage_root=storage_root, deadline=entry["connect_deadline"])
                entry["owned_turn"] = (client.thread_id, client.turn_id)
            self._diagnose(entry, "awaiting_health")
            while not entry["stop"].is_set():
                if client.worker_error:
                    raise WorkerError(client.worker_error)
                if process.poll() is not None or client.failure:
                    raise WorkerError(client.failure or "platform_chrome_worker_exited")
                if client.worker_done is not None:
                    # A terminal failure may arrive after the error-flag check
                    # above; it must never become a successful stop.
                    if client.worker_done.get('state') == 'failed':
                        raise WorkerError(client.worker_error or client.worker_done.get('code') or
                                          "platform_chrome_worker_failed")
                    # An idle/stopped worker has already detached its health
                    # lease; wait for its normal turn end rather than treating
                    # that expected detach as an expired ready connection.
                    with self._lock:
                        entry["state"] = "stopping"
                    break
                if client.completed is not None:
                    # Final assistant text cannot establish successful work or
                    # reconnect an exhausted/failed provider automatically.
                    if client.completed.get("status") != "completed":
                        raise WorkerError("platform_chrome_worker_turn_failed")
                    if entry["state"] != "ready":
                        raise WorkerError("platform_chrome_worker_not_ready")
                    with self._lock:
                        entry["state"] = "disconnected"
                    break
                transition = None
                with self._lock:
                    if entry["state"] in ("ready", "resuming"):
                        transition = self._refresh_connection(entry)
                        if entry["state"] == "failed":
                            raise WorkerError(entry["error"])
                    else:
                        healthy = self._health(entry)
                        if time.monotonic() >= entry["connect_deadline"]:
                            raise WorkerError("platform_chrome_worker_connect_timeout")
                        if healthy:
                            entry["state"] = "ready"
                            transition = ("bridge_ready", {})
                if transition:
                    self._diagnose(entry, transition[0], **transition[1])
                entry["stop"].wait(0.2)
        except Exception as error:
            with self._lock:
                entry.update(state="failed", error=_code(error),
                    failure_phase=entry.get("failure_phase") or entry.get("last_active_worker_phase") or
                        entry.get("worker_phase") or entry.get("connection_phase", "created"), keep_readback=False)
            self._diagnose(entry, "connection_failed", code=_code(error))
        finally:
            entry["stop"].set()
            self._diagnose(entry, "stop_requested")
            if entry["state"] == "failed":
                entry["keep_readback"] = False
            try:
                self._signal_stop(entry)
            except Exception as error:
                with self._lock:
                    entry.update(state="failed", error=_code(error))
            try:
                try:
                    self._wait_for_finish(entry, client, process)
                except Exception:
                    with self._lock:
                        entry.update(cleanup_unconfirmed=True, cleanup_error="platform_chrome_worker_cleanup_unconfirmed")
                    self._diagnose(entry, "cleanup_unconfirmed", code="platform_chrome_worker_cleanup_unconfirmed")
                self._detach(entry)
                if client:
                    client.close()
                elif process and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
            finally:
                with self._lock:
                    if entry["state"] in ("connecting", "ready", "stopping", "resuming"):
                        entry["state"] = "disconnected"
                self._diagnose(entry, "closed")
                entry["done"].set()


def _controller():
    global _CONTROLLER
    with _CONTROLLER_LOCK:
        if _CONTROLLER is None:
            _CONTROLLER = ChromeWorkerController(api)
        return _CONTROLLER


def availability():
    """Check installed dependencies only. Never creates a model/tool context."""
    try:
        _codex_executable()
        _sdk_path()
        for name in ("story.py", "story_chrome_worker.mjs", "story_chrome_bridge.mjs"):
            _regular(Path(__file__).with_name(name))
        _regular(sys.executable, executable=True)
        return {"available": True, "uses_codex_quota": True}
    except (OSError, ValueError, WorkerError) as error:
        return {"available": False, "reason": _code(error), "uses_codex_quota": True}


def start(root, plan_id, profile_id, *, expected_book_id=None, comparison_policy="strict", body_encoding="verbatim"):
    return _controller().start(root, plan_id, profile_id, expected_book_id=expected_book_id,
                               comparison_policy=comparison_policy, body_encoding=body_encoding)


connect = start


def status(root, plan_id, profile_id, *, expected_book_id=None, comparison_policy="strict", body_encoding="verbatim"):
    return _controller().status(root, plan_id, profile_id, expected_book_id=expected_book_id,
                                comparison_policy=comparison_policy, body_encoding=body_encoding)


def disconnect(root, plan_id, profile_id, *, expected_book_id=None, keep_readback=False, binding_sha256=None,
               owned_worker_id=None, comparison_policy="strict", body_encoding="verbatim"):
    return _controller().disconnect(root, plan_id, profile_id, expected_book_id=expected_book_id,
                                    keep_readback=keep_readback, binding_sha256=binding_sha256,
                                    owned_worker_id=owned_worker_id, comparison_policy=comparison_policy, body_encoding=body_encoding)
