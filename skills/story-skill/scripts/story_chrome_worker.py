"""Owned, ephemeral Codex workers for the existing-Chrome draft bridge.

Only a verified Terra turn may attempt the automatic Codex SDK connection;
the host must separately authorize it. A lease or model message never establishes readiness: the bridge's
nonce health receipt and the owned app-server process must both remain valid.
This controller does not claim or replay queue requests and never launches a
browser. Its only launch is a fixed, resolved Codex app-server executable.
The separate assistant-session helper only prepares a local queue controller;
the actual assistant supplies its own authorized public browser runtime. Codex
keeps its Terra requirement; native hosts require separate explicit permission.
"""
from concurrent.futures import Future, TimeoutError as FutureTimeout
from contextlib import contextmanager
from datetime import datetime
import hashlib
import hmac
import json
import math
import os
import platform
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
WORKER_MODEL = "gpt-6.1-terra"
CONNECTION_SUSPENDED = "platform_chrome_worker_connection_suspended"
BOOTSTRAP_PREFIX = "STORY_CHROME_WORKER_BOOTSTRAP "
BOOTSTRAP_ERRORS = frozenset({"platform_chrome_worker_tool_discovery_failed",
                             "platform_chrome_worker_origin_permission_required"})
PROVIDER_ERRORS = frozenset({"contextWindowExceeded", "sessionBudgetExceeded", "usageLimitExceeded",
    "rateLimitExceeded", "flexUnavailable", "serverOverloaded", "modelNotFound", "cyberPolicy",
    "misalignmentPolicyViolation", "tooManyDenials", "internalServerError", "unauthorized", "badRequest",
    "threadRollbackFailed", "sandboxError", "other", "httpConnectionFailed", "responseStreamConnectionFailed",
    "responseStreamDisconnected", "responseTooManyFailedAttempts", "activeTurnNotSteerable"})
CHECKPOINT_PREFIX = "STORY_CHROME_WORKER_CHECKPOINT "
CHECKPOINT_STATES = frozenset({"connecting", "ready", "busy", "stopped", "idle_timeout", "failed"})
TERMINAL_CHECKPOINT_STATES = frozenset({"stopped", "idle_timeout", "failed"})
WORKER_PHASES = frozenset({"config_validated", "sdk_setup", "browser_inventory", "browser_documentation",
    "waiting_for_chrome", "existing_tab_probe", "bridge_attach", "bridge_health", "queue_wait", "queue_operation", "cleanup"})
ERROR_NAMES = frozenset({"Error", "ReferenceError", "TypeError", "RangeError", "SyntaxError", "AggregateError",
    "URIError", "EvalError", "AbortError", "TimeoutError"})
ERROR_MARKERS = frozenset({"none", "browser_unattached", "missing_turn_metadata", "missing_browser_service",
    "missing_runtime_global", "origin_permission_required", "native_pipe_closed"})
DRIVER_PHASES = frozenset({"guard", "catalog", "listing", "pages", "all_rows", "read"})
RESULT_CODES = frozenset("platform_chrome_worker_" + value for value in (
    "ambiguous_browser", "ambiguous_tab", "binding_mismatch", "browser_changed", "browser_unattached", "cleanup_failed", "cli_failed", "config_changed",
    "detach_failed", "duplicate_request", "existing_tab_required", "target_tab_changed", "target_tab_closed", "failed", "handle_exists", "invalid_challenge", "connect_timeout", "browser_wait_timeout",
    "invalid_config", "invalid_packet", "invalid_scroll", "invalid_stop", "unavailable", "unsafe_file",
    "native_model_not_authorized", "native_sdk_unavailable", "native_runtime_required",
    "unsafe_user_tab", "unsupported_sdk", "turn_metadata_missing", "runtime_unavailable", "origin_permission_required", "browser_transport_closed")) | frozenset("platform_browser_" + value for value in (
    "ambiguous_dom", "ambiguous_draft", "chapter_exists_not_draft", "editor_changed", "identity_mismatch",
    "identity_unavailable", "incomplete_dom", "incomplete_list", "incomplete_scope", "invalid_chapter",
    "invalid_identity", "invalid_profile", "invalid_remote_id", "login_required", "navigation_timeout", "read_timeout",
    "not_draft", "not_new_draft", "not_open", "operation_failed", "save_unverified", "target_unverified",
    "unnumbered_draft", "unsafe_create", "unsafe_navigation", "unsafe_save", "unsafe_url", "unsupported_editor",
    "bridge_scope", "bridge_expired", "editor_in_use", "readback_required"))
DIAGNOSTIC_CODES = RESULT_CODES | frozenset("platform_chrome_worker_" + value for value in (
    "tool_failed", "unexpected_tool", "host_context_missing", "debugger_unavailable", "module_unavailable",
    "disconnected", "invalid_message", "message_limit", "rpc_error", "rpc_timeout", "connect_timeout",
    "health_expired", "exited", "turn_failed", "not_ready", "unsafe_runtime", "unsafe_stop", "invalid_context",
    "thread_start_failed", "cleanup_unconfirmed", "model_unverified", "model_mismatch", "model_rerouted",
    "model_unavailable", "tool_not_started", "tool_discovery_failed", "connection_suspended"))
DIAGNOSTIC_PHASES = frozenset({"created", "app_server_started", "rpc_sent", "rpc_completed", "rpc_timeout",
    "rpc_failed", "model_turn_started", "model_turn_completed", "model_activity", "tool_started", "tool_completed",
    "tool_failed", "worker_checkpoint", "awaiting_health", "bridge_ready", "connection_failed", "stop_requested",
    "transport_closed", "closed", "connect_budget_extended", "worker_done_confirmed", "cleanup_unconfirmed",
    "resume_wait", "bridge_resumed", "model_verified", "model_rejected", "provider_error", "bootstrap_failed"})
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


def _provider_error(error):
    """Retain public structured categories only, never provider prose or data."""
    if not isinstance(error, dict):
        return {"error_kind": "other"}
    info = error.get("codexErrorInfo")
    if info is None and isinstance(error.get("data"), dict):
        info = error["data"].get("codexErrorInfo")
    kind = info if isinstance(info, str) and info in PROVIDER_ERRORS else "other"
    status = None
    if isinstance(info, dict) and len(info) == 1:
        name = next(iter(info))
        if name in PROVIDER_ERRORS:
            kind = name
            detail = info[name]
            if isinstance(detail, dict):
                status = detail.get("httpStatusCode")
    result = {"error_kind": kind}
    if type(error.get("code")) is int and -(2**31) <= error["code"] < 2**31:
        result["rpc_error_code"] = error["code"]
    if type(status) is int and 100 <= status <= 599:
        result["http_status"] = status
    return result


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


def _native_sdk_roots(execution_host):
    """Only verified public desktop installation paths, never session registries."""
    if execution_host == "qoder-cn" and sys.platform == "darwin":
        return tuple(parent / "Qoder CN.app/Contents/Resources/node-repl/node_modules"
                     for parent in (Path("/Applications"), Path.home() / "Applications"))
    # WorkBuddy's browser runtime has no verified public SDK installation here.
    # Its task-level GUI agent and private native launcher are not substitutes.
    return ()


def _native_sdk_path(execution_host):
    if execution_host not in ("qoder-cn", "workbuddy"):
        raise WorkerError("platform_chrome_worker_invalid_config")
    for root in _native_sdk_roots(execution_host):
        try:
            root = Path(root)
            if not root.is_absolute() or root.is_symlink() or root.resolve(strict=True) != root:
                raise WorkerError("platform_chrome_worker_unsafe_runtime")
            paths = (root / "@qoder-space/browser-use/package.json", root / "@qoder-space/browser-use/index.js",
                     root / "@ali/qoder-browser-use-sdk/package.json", root / "@ali/qoder-browser-use-sdk/index.js")
            for candidate in paths:
                resolved = _regular(candidate)
                if resolved != candidate:
                    raise WorkerError("platform_chrome_worker_unsafe_runtime")
                resolved.relative_to(root)
            alias_manifest, alias_module, sdk_manifest, _ = paths
            if (alias_manifest.stat().st_size > 65536 or sdk_manifest.stat().st_size > 65536 or
                    alias_module.stat().st_size > 4096):
                raise WorkerError("platform_chrome_worker_unsafe_runtime")
            alias = json.loads(alias_manifest.read_text(encoding="utf-8"))
            sdk = json.loads(sdk_manifest.read_text(encoding="utf-8"))
            if (not isinstance(alias, dict) or alias.get("name") != "@qoder-space/browser-use" or
                    alias.get("type") != "module" or alias.get("exports") != "./index.js" or
                    not isinstance(sdk, dict) or sdk.get("name") != "@ali/qoder-browser-use-sdk" or
                    sdk.get("type") != "module" or sdk.get("exports") != "./index.js" or
                    not isinstance(sdk.get("version"), str) or
                    not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", sdk["version"]) or
                    not re.fullmatch(r'\s*export\s+\*\s+from\s+[\'"]@ali/qoder-browser-use-sdk[\'"]\s*;?\s*',
                                     alias_module.read_text(encoding="utf-8"))):
                raise WorkerError("platform_chrome_worker_native_sdk_unavailable")
            return alias_module
        except FileNotFoundError:
            continue
        except (OSError, ValueError, UnicodeError):
            raise WorkerError("platform_chrome_worker_native_sdk_unavailable") from None
    raise WorkerError("platform_chrome_worker_native_sdk_unavailable")


WORKBUDDY_CLI_VERSION = "0.41.0"
WORKBUDDY_CLI_HASHES = {
    ("darwin", "x86_64"): ("agent-browser-darwin-x64", "f108d4aa02f41eee05d6b1b05817475fd5d576d802826783ef80800da16f33e2"),
    ("darwin", "arm64"): ("agent-browser-darwin-arm64", "a402fef2489c221b17522aa21f103ca5dc47f00ff5879d4a72c39881544730ab"),
    ("win32", "AMD64"): ("agent-browser-win32-x64.exe", "dee86b78071cbe7217f71ec1c9df6a50efa73cd44d64a18877bb36556fc8e270"),
}


def _workbuddy_cli_path():
    """Inspect the pinned public CLI only; discovery never launches a browser."""
    entry = WORKBUDDY_CLI_HASHES.get((sys.platform, platform.machine()))
    if entry is None:
        raise WorkerError("platform_chrome_worker_native_sdk_unavailable")
    roots = [Path.home() / ".story-skill/browser-tools/agent-browser" / WORKBUDDY_CLI_VERSION / "package"]
    launcher = shutil.which("agent-browser")
    if launcher:
        located = Path(launcher).resolve()
        if located.name in ("agent-browser.js", entry[0]):
            roots.append(located.parent.parent)
        elif os.name == "nt" and located.suffix.lower() in (".cmd", ".ps1"):
            roots.append(located.parent / "node_modules/agent-browser")
    if os.name == "nt":
        if os.environ.get("APPDATA"):
            roots.append(Path(os.environ["APPDATA"]) / "npm/node_modules/agent-browser")
    else:
        roots.extend((Path("/usr/local/lib/node_modules/agent-browser"),
                      Path("/opt/homebrew/lib/node_modules/agent-browser"),
                      Path.home() / ".npm-global/lib/node_modules/agent-browser"))
    for root in dict.fromkeys(roots):
        candidate = root / "bin" / entry[0]
        try:
            executable = _regular(candidate, executable=True)
            manifest = _regular(root / "package.json")
            if executable != candidate or manifest.stat().st_size > 65536:
                raise WorkerError("platform_chrome_worker_unsafe_runtime")
            package = json.loads(manifest.read_text(encoding="utf-8"))
            if (not isinstance(package, dict) or package.get("name") != "agent-browser" or
                    package.get("version") != WORKBUDDY_CLI_VERSION or
                    hashlib.sha256(executable.read_bytes()).hexdigest() != entry[1]):
                raise WorkerError("platform_chrome_worker_unsafe_runtime")
            return executable
        except FileNotFoundError:
            continue
        except (OSError, ValueError, UnicodeError):
            raise WorkerError("platform_chrome_worker_native_sdk_unavailable") from None
    raise WorkerError("platform_chrome_worker_native_sdk_unavailable")


def _workbuddy_session(binding):
    target = binding["target"]
    identity = [binding["book_id"], target["platform"], target["account_id"], target["remote_book_id"]]
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return "story-wb-" + digest[:24]


def _workbuddy_node_path():
    candidates = [Path.home() / ".story-skill/browser-tools/node/v24.22.2" /
                  ("node.exe" if os.name == "nt" else "bin/node")]
    found = shutil.which("node")
    if found:
        candidates.append(Path(found))
    if os.name == "nt":
        if os.environ.get("ProgramFiles"):
            candidates.append(Path(os.environ["ProgramFiles"]) / "nodejs/node.exe")
    else:
        candidates.extend((Path("/usr/local/bin/node"), Path("/opt/homebrew/bin/node")))
    for candidate in candidates:
        try:
            return _regular(candidate, executable=True)
        except (WorkerError, OSError):
            continue
    raise WorkerError("platform_chrome_worker_native_runtime_required")


def _assistant_host_context_matches(config, context):
    """Bind host permission to this exact private context, not a model claim."""
    host = config.get("execution_host", "codex")
    if host not in ("codex", "qoder-cn", "workbuddy") or context.get("execution_host", "codex") != host:
        return False
    if host == "codex":
        return (config.get("native_model_authorized", False) is False and
                context.get("native_model_authorized", False) is False and
                context.get("model_policy", "terra_required") == "terra_required")
    if host == "workbuddy" and any(context.get(key) != config.get(key) or not isinstance(config.get(key), str)
            for key in ("workbuddy_cli_path", "workbuddy_cli_sha256", "workbuddy_session", "workbuddy_state_directory")):
        return False
    return (config.get("native_model_authorized") is True and context.get("native_model_authorized") is True and
            context.get("model_policy") == "user_authorized_native" and
            isinstance(context.get("sdk_path"), str) and context["sdk_path"] == config.get("sdk_path"))


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
        "This consumes the user's configured Codex quota. The controller has pinned and verified gpt-6.1-terra; "
        "do not change the model or reasoning effort, delegate, or fall back. "
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
        "If node_repl/js is deferred, first use the host's documented tool discovery to find only that exact tool. "
        "When the host exposes functions/exec and ALL_TOOLS, the only permitted discovery code is "
        "text(ALL_TOOLS.filter(t => t.name === 'mcp__node_repl__js')); "
        "The host's official call wrapper may invoke only that discovered node_repl/js tool with the exact JavaScript below "
        "and forward its result. It may not run shell, filesystem, network, browser, or any other tool. "
        "Tool discovery is not browser permission and never permits bypassing a denial. "
        "If the tool cannot be found, return exactly " + BOOTSTRAP_PREFIX +
        '{"code":"platform_chrome_worker_tool_discovery_failed"}. '
        "If official authorization is denied, return exactly " + BOOTSTRAP_PREFIX +
        '{"code":"platform_chrome_worker_origin_permission_required"}. '
        "Call the JavaScript tool with timeout_ms=240000. Checkpoints pause between completed SDK calls, "
        "awaiting in-flight browser calls and health checks before returning. A parked continuation never performs browser work "
        "between tool invocations; resume the same consumed request, never replay a remote write. "
        "First call exactly:\n" + first +
        "\nIf done is false, continue calling the same already-imported module with exactly this code:\n" + subsequent +
        "\nRepeat checkpoints until done is true, then return only its stable state/error summary. "
        "Do not reimport the module or reset the REPL. Never repeat an individual queue operation or reconstruct a failed write. "
        "If the tool fails, stop and report a stable failure; do not retry or use a fallback. "
        "The module controls the 120-second no-job idle exit and finally cleanup. Never print book content, account IDs, or tokens.")


@contextmanager
def _assistant_bound_evidence(root, binding, context):
    """Yield the pinned worker directory evidence, or None when it cannot be verified.

    Every read happens inside the pinned descriptor so another process cannot
    substitute the worker directory after validation.
    """
    core, bridge = api, api.platform_chrome_bridge
    if (not isinstance(context, dict) or context.get("execution_origin") != "assistant_session" or
            context.get("model_verification") != "executor_runtime_required" or
            type(context.get("controller_pid")) is not int or context["controller_pid"] <= 0 or
            not isinstance(context.get("controller_instance"), str) or
            not re.fullmatch(r"[0-9a-f]{32}", context["controller_instance"]) or
            not isinstance(context.get("config_path"), str)):
        yield None
        return
    path = Path(context["config_path"])
    if not path.is_absolute() or path.name != "config.json":
        yield None
        return
    expected_worker = {"pid": context["controller_pid"], "instance": context["controller_instance"]}
    fingerprint = bridge._digest(binding)
    with bridge._pinned(path.parent) as directory:
        attributes = directory.path.stat() if os.name == "nt" else os.fstat(directory.fd)
        if ((os.name != "nt" and attributes.st_mode & 0o077) or
                (hasattr(os, "getuid") and attributes.st_uid != os.getuid())):
            yield None
            return
        config = bridge._read(directory, path.name, maximum=32768)
        config_stat = core.publish._bound_stat(core._BoundFile(directory, path.name))
        if (not isinstance(config, dict) or not _assistant_host_context_matches(config, context) or
                config.get("schema") != 1 or config.get("worker") != expected_worker or
                config.get("book_root") != str(Path(root).resolve()) or
                any(config.get(key) != binding[key] for key in ("book_id", "plan_id", "profile_id", "target")) or
                config.get("binding_sha256") != fingerprint or
                config.get("handle_path") != str(path.parent / "handle.json")):
            yield None
            return
        handle = None
        if core.publish._bound_stat(core._BoundFile(directory, "handle.json"), missing=True) is not None:
            handle = bridge._read(directory, "handle.json", maximum=16384)
        if handle is not None and (not isinstance(handle, dict) or handle.get("book_root") != str(Path(root).resolve()) or
                handle.get("book_id") != binding["book_id"] or handle.get("binding_sha256") != fingerprint or
                not isinstance(handle.get("session_id"), str) or
                not re.fullmatch(r"[0-9a-f]{32}", handle["session_id"]) or
                not isinstance(handle.get("token"), str)):
            yield None
            return
        receipt = None
        if core.publish._bound_stat(core._BoundFile(directory, "worker-final.json"), missing=True) is not None:
            receipt = bridge._read(directory, "worker-final.json", maximum=4096)
        names = sorted(os.listdir(directory.path if os.name == "nt" else directory.fd))
        yield {"directory": directory, "config": config, "config_stat": config_stat, "handle": handle,
               "receipt": receipt, "names": names, "worker": expected_worker, "fingerprint": fingerprint}


_RECONCILED_FIELDS = {"schema", "kind", "request_id", "request_binding_sha256", "binding_sha256",
                      "book_root", "session_id", "worker", "browser_id", "owned_tab_ids", "queue_sha256",
                      "evidence", "evidence_sha256", "observed_at_ms", "reconciled_at", "worker_done",
                      "cleanup_complete", "platform_written"}
_RECONCILED_EVIDENCE_FIELDS = {"schema", "request_id", "config_path", "controller_instance", "session_id",
                               "request_binding_sha256", "browser_id", "source", "observed_at_ms", "checked_tabs"}


def _assistant_ownership_snapshot(directory, session_id, fingerprint, worker):
    """Latest tab ownership written by this exact worker session."""
    core, bridge = api, api.platform_chrome_bridge
    names = os.listdir(directory.path if os.name == "nt" else directory.fd)
    records = []
    for name in names:
        match = re.fullmatch(r"worker-ownership-([0-9]+)\.json", name)
        if not match:
            continue
        value = bridge._read(directory, name, maximum=16384)
        if (not isinstance(value, dict) or
                set(value) != {"schema", "worker", "binding_sha256", "session_id", "browser_id",
                               "owned_tab_ids", "observed_at_ms"} or
                value.get("schema") != 1 or value.get("worker") != worker or
                value.get("binding_sha256") != fingerprint or
                value.get("session_id") not in (None, session_id) or
                type(value.get("observed_at_ms")) is not int or
                not isinstance(value.get("owned_tab_ids"), list) or len(value["owned_tab_ids"]) > 10000 or
                any(not isinstance(item, str) or not item or len(item) > 200 for item in value["owned_tab_ids"]) or
                len(set(value["owned_tab_ids"])) != len(value["owned_tab_ids"])):
            raise WorkerError("platform_draft_assistant_reconcile_invalid")
        records.append((int(match.group(1)), value))
    if not records or len(records) > 10000:
        raise WorkerError("platform_draft_assistant_reconcile_invalid")
    records.sort(key=lambda item: item[0])
    latest = records[-1][1]
    if (latest.get("session_id") != session_id or
            not isinstance(latest.get("browser_id"), str) or not latest["browser_id"]):
        raise WorkerError("platform_draft_assistant_reconcile_invalid")
    return latest


def _assistant_health_browser(directory, session_id, fingerprint, worker):
    """Browser id from a health receipt bound to the same session and worker."""
    core, bridge = api, api.platform_chrome_bridge
    names = os.listdir(directory.path if os.name == "nt" else directory.fd)
    found = set()
    for name in names:
        if not re.fullmatch(r"worker-health-[A-Za-z0-9_-]{32,200}\.json", name):
            continue
        value = bridge._read(directory, name, maximum=8192)
        if (not isinstance(value, dict) or value.get("schema") != 1 or
                value.get("session_id") != session_id or value.get("binding_sha256") != fingerprint or
                value.get("worker") != worker or not isinstance(value.get("browser"), dict) or
                value["browser"].get("kind") != "chrome" or
                not isinstance(value["browser"].get("id"), str) or not value["browser"]["id"]):
            continue
        found.add(value["browser"]["id"])
    if len(found) > 1:
        raise WorkerError("platform_draft_assistant_reconcile_invalid")
    return next(iter(found), None)


def _assistant_reconciled_valid(receipt, *, root, binding, context, config_dir, session_dir, handle,
                                expected, fingerprint, config_stat, request_id, request_binding_sha256,
                                mode, queue):
    """Accept only a machine-verified absent session for a finished read-only task."""
    core, bridge = api, api.platform_chrome_bridge
    if mode not in {"inspect_drafts", "reconcile_only"}:
        return False
    if (not isinstance(receipt, dict) or set(receipt) != _RECONCILED_FIELDS or
            type(receipt.get("schema")) is not int or receipt["schema"] != 1 or
            receipt.get("kind") != "machine_verified_absent_session" or
            receipt.get("request_id") != request_id or
            receipt.get("request_binding_sha256") != request_binding_sha256 or
            receipt.get("binding_sha256") != fingerprint or
            receipt.get("book_root") != str(Path(root).resolve()) or
            receipt.get("session_id") != handle["session_id"] or receipt.get("worker") != expected or
            receipt.get("worker_done") is not False or receipt.get("cleanup_complete") is not True or
            receipt.get("platform_written") is not False or
            not isinstance(receipt.get("browser_id"), str) or not receipt["browser_id"] or
            type(receipt.get("observed_at_ms")) is not int or
            type(receipt.get("reconciled_at")) not in (int, float) or
            not math.isfinite(receipt["reconciled_at"]) or
            not isinstance(receipt.get("queue_sha256"), str) or
            not re.fullmatch(r"[0-9a-f]{64}", receipt["queue_sha256"]) or
            not isinstance(receipt.get("evidence_sha256"), str) or
            not re.fullmatch(r"[0-9a-f]{64}", receipt["evidence_sha256"]) or
            receipt["queue_sha256"] != bridge._digest(queue) or
            receipt["evidence_sha256"] != bridge._digest(receipt.get("evidence"))):
        return False
    evidence = receipt["evidence"]
    if (not isinstance(evidence, dict) or set(evidence) != _RECONCILED_EVIDENCE_FIELDS or
            type(evidence.get("schema")) is not int or evidence["schema"] != 1 or
            evidence.get("request_id") != request_id or
            evidence.get("config_path") != context["config_path"] or
            evidence.get("controller_instance") != context["controller_instance"] or
            evidence.get("session_id") != handle["session_id"] or
            evidence.get("request_binding_sha256") != request_binding_sha256 or
            evidence.get("browser_id") != receipt["browser_id"] or
            evidence.get("source") != "official_node_repl_browser_use" or
            evidence.get("observed_at_ms") != receipt["observed_at_ms"] or
            not isinstance(evidence.get("checked_tabs"), list)):
        return False
    latest = _assistant_ownership_snapshot(config_dir, handle["session_id"], fingerprint, expected)
    health_browser = _assistant_health_browser(config_dir, handle["session_id"], fingerprint, expected)
    if (receipt["browser_id"] != latest["browser_id"] or health_browser != receipt["browser_id"] or
            not isinstance(receipt.get("owned_tab_ids"), list) or
            receipt["owned_tab_ids"] != latest["owned_tab_ids"]):
        return False
    checked = evidence["checked_tabs"]
    if (len(checked) != len(latest["owned_tab_ids"]) or
            any(not isinstance(row, dict) or set(row) != {"id", "present"} or row.get("present") is not False
                for row in checked) or
            sorted(row["id"] for row in checked) != sorted(latest["owned_tab_ids"])):
        return False
    formed_at_ms = config_stat.st_mtime_ns // 1000000
    now_ms = int(time.time() * 1000)
    if (not formed_at_ms <= receipt["observed_at_ms"] <= now_ms + 60000 or
            receipt["observed_at_ms"] < latest["observed_at_ms"] or
            receipt["observed_at_ms"] > receipt["reconciled_at"] * 1000 + 1000 or
            receipt["reconciled_at"] > time.time() + 60 or
            bridge._owner_alive(root, {"owner_pid": expected["pid"]})):
        return False
    lease = bridge._read(session_dir, "lease.json", maximum=16384)
    if (not isinstance(lease, dict) or lease.get("session_id") != handle["session_id"] or
            type(lease.get("detached")) is not bool or type(lease.get("expires")) not in (int, float) or
            not math.isfinite(lease["expires"]) or
            (lease["expires"] > time.time() and lease.get("detached") is not True)):
        return False
    return True


def assistant_cleanup_status(root, binding, context, *, request_id=None, request_binding_sha256=None,
                             mode=None):
    """Read bound terminal evidence; lease expiry or controller exit is not cleanup.

    This does not contact the host, extend a lease, create locks, or accept a
    caller-supplied success flag. A missing worker receipt stays pending unless
    a separately machine-verified reconciliation proves this exact session has
    no live controller, no undrained request and no remaining owned tab.
    """
    core, bridge = api, api.platform_chrome_bridge
    result = {"worker_done": False, "bridge_detached": False,
              "cleanup_complete": False, "cleanup_status": "unverified"}
    try:
        with _assistant_bound_evidence(root, binding, context) as evidence:
            if evidence is None:
                return result
            handle, receipt, config_stat = evidence["handle"], evidence["receipt"], evidence["config_stat"]
            expected_worker, fingerprint = evidence["worker"], evidence["fingerprint"]
            if handle is not None:
                with bridge._directory(Path(root).resolve(), handle["session_id"]) as directory:
                    if bridge._restored_evidence(directory):
                        return result
                    session = bridge._session(directory)
                    if (session["binding"] != binding or not hmac.compare_digest(session["token_sha256"],
                            hashlib.sha256(handle["token"].encode()).hexdigest())):
                        return result
                    health = bridge._health_record(directory, session)
                    if health is not None and health["worker"] != expected_worker:
                        return result
                    lease = bridge._read(directory, "lease.json", maximum=16384)
                    if not isinstance(lease, dict) or lease.get("session_id") != handle["session_id"]:
                        return result
                    result["bridge_detached"] = lease.get("detached") is True
                    reconciled = None
                    if core.publish._bound_stat(core._BoundFile(directory, "worker-reconciled.json"),
                                                missing=True) is not None:
                        reconciled = bridge._read(directory, "worker-reconciled.json", maximum=65536)
                    if (receipt is None and reconciled is not None and request_id is not None and
                            request_binding_sha256 is not None):
                        try:
                            queue = _assistant_readonly_queue(directory, binding, expected_worker["pid"])
                            if _assistant_reconciled_valid(
                                    reconciled, root=root, binding=binding, context=context,
                                    config_dir=evidence["directory"], session_dir=directory,
                                    handle=handle, expected=expected_worker, fingerprint=fingerprint,
                                    config_stat=config_stat, request_id=request_id,
                                    request_binding_sha256=request_binding_sha256, mode=mode,
                                    queue=queue):
                                return {"worker_done": False,
                                        "bridge_detached": result["bridge_detached"],
                                        "cleanup_complete": True,
                                        "cleanup_status": "reconciled_absent"}
                        except (core.StoryError, WorkerError, OSError, ValueError, KeyError, TypeError):
                            pass
        result["cleanup_status"] = "pending"
        if receipt is None:
            return result
        fields = {"schema", "worker", "binding_sha256", "session_id", "attached", "done", "bridge_detached",
                  "cleanup_complete", "state", "owned_tabs_remaining", "completed_at_ms"}
        if (not isinstance(receipt, dict) or set(receipt) != fields or type(receipt.get("schema")) is not int or
                receipt["schema"] != 1 or receipt.get("worker") != expected_worker or
                receipt.get("binding_sha256") != fingerprint or
                receipt.get("session_id") != (handle["session_id"] if handle else None) or
                receipt.get("attached") is not (handle is not None) or
                any(type(receipt.get(key)) is not bool for key in ("attached", "done", "bridge_detached", "cleanup_complete")) or
                (handle is None and receipt.get("bridge_detached") is not False) or
                type(receipt.get("owned_tabs_remaining")) is not int or not 0 <= receipt["owned_tabs_remaining"] <= 10000 or
                not isinstance(receipt.get("state"), str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,95}", receipt["state"]) or
                type(receipt.get("completed_at_ms")) is not int or
                not config_stat.st_mtime_ns // 1000000 <= receipt["completed_at_ms"] <= int(time.time() * 1000) + 1000):
            result["cleanup_status"] = "unverified"
            return result
        result["worker_done"] = receipt["done"]
        result["cleanup_complete"] = (receipt["done"] and receipt["cleanup_complete"] and
            receipt["owned_tabs_remaining"] == 0 and
            (handle is None or (receipt["bridge_detached"] and result["bridge_detached"])))
        result["cleanup_status"] = "complete" if result["cleanup_complete"] else "pending"
        return result
    except (core.StoryError, WorkerError, OSError, ValueError, KeyError, TypeError):
        return {**result, "cleanup_complete": False, "cleanup_status": "unverified"}


def assistant_session_never_started(root, binding, context):
    """Prove locally that no host ever began the claimed queue session.

    A queue controller killed before its host ran leaves a stopped record that no
    worker can ever drain. handle.json, an operation packet or any diagnostic is
    the host's first session artifact and the bridge holds no lease for a session
    that never attached, so a bound directory holding only the queue controller's
    own files, a dead controller and no attached bridge proves nothing started.
    Any other artifact keeps this false.
    """
    core, bridge = api, api.platform_chrome_bridge
    try:
        with _assistant_bound_evidence(root, binding, context) as evidence:
            if evidence is None or evidence["handle"] is not None:
                return False
            if set(evidence["names"]) - {"config.json", "stop.json"}:
                return False
        book = Path(root).resolve()
        return (not bridge._owner_alive(book, {"owner_pid": context["controller_pid"]}) and
                bridge.worker_status(book, binding).get("attached") is not True)
    except (core.StoryError, WorkerError, OSError, ValueError, KeyError, TypeError):
        return False


def _assistant_reset_metadata(root, binding, context):
    """Authenticate the exact local queue without renewing its expired lease."""
    core, bridge = api, api.platform_chrome_bridge
    if not isinstance(context, dict) or not isinstance(context.get("config_path"), str):
        raise WorkerError("platform_draft_assistant_reset_invalid")
    path = Path(context["config_path"])
    if not path.is_absolute() or path.name != "config.json":
        raise WorkerError("platform_draft_assistant_reset_invalid")
    with bridge._pinned(path.parent) as directory:
        attributes = directory.path.stat() if os.name == "nt" else os.fstat(directory.fd)
        if (os.name != "nt" and attributes.st_mode & 0o077):
            raise WorkerError("platform_draft_assistant_reset_invalid")
        config = bridge._read(directory, "config.json", maximum=32768)
        handle = bridge._read(directory, "handle.json", maximum=16384)
        formed_at = max(core.publish._bound_stat(core._BoundFile(directory, name)).st_mtime_ns
                        for name in ("config.json", "handle.json")) / 1000000000
    expected = {"pid": context.get("controller_pid"), "instance": context.get("controller_instance")}
    fingerprint = bridge._digest(binding)
    if (type(expected["pid"]) is not int or expected["pid"] <= 0 or
            not isinstance(expected["instance"], str) or not re.fullmatch(r"[0-9a-f]{32}", expected["instance"]) or
            context.get("execution_origin") != "assistant_session" or
            context.get("model_verification") != "executor_runtime_required" or
            not isinstance(config, dict) or not _assistant_host_context_matches(config, context) or config.get("worker") != expected or
            config.get("book_root") != str(Path(root).resolve()) or config.get("binding_sha256") != fingerprint or
            config.get("handle_path") != str(path.parent / "handle.json") or
            any(config.get(key) != binding[key] for key in ("book_id", "plan_id", "profile_id", "target")) or
            not isinstance(handle, dict) or handle.get("book_root") != str(Path(root).resolve()) or
            handle.get("book_id") != binding["book_id"] or handle.get("binding_sha256") != fingerprint or
            not isinstance(handle.get("token"), str) or not isinstance(handle.get("session_id"), str) or
            not re.fullmatch(r"[0-9a-f]{32}", handle["session_id"])):
        raise WorkerError("platform_draft_assistant_reset_invalid")
    return path, handle, expected, formed_at


def _assistant_chrome_processes():
    """Process metadata only. Recovery is intentionally limited to this Mac path."""
    if sys.platform != "darwin":
        raise WorkerError("platform_draft_assistant_reset_unsupported")
    completed = subprocess.run(["/bin/ps", "-wwaxo", "pid=,comm="], capture_output=True,
                               text=True, timeout=5, check=True)
    if len(completed.stdout) > 8 * 1024 * 1024:
        raise WorkerError("platform_draft_assistant_reset_invalid")
    return [int(match.group(1)) for line in completed.stdout.splitlines()
            if (match := re.match(r"\s*(\d+)\s+(.+)", line)) and
            re.search(r"(?:Google Chrome(?: Canary| Beta| Dev)?|Chromium)(?: Helper[^/]*)?\.app/", match.group(2))]


def _assistant_readonly_queue(directory, binding, worker_pid, *, reset_after=None):
    core, bridge, rows = api, api.platform_chrome_bridge, {}
    names = os.listdir(directory.path if os.name == "nt" else directory.fd)
    if len(names) > 10000:
        raise WorkerError("platform_draft_assistant_reset_invalid")
    for name in sorted(names):
        if not name.startswith(("outbox-", "claimed-")):
            continue
        if not re.fullmatch(r"(?:outbox|claimed)-[0-9a-f]{32}\.json", name):
            raise WorkerError("platform_draft_assistant_reset_invalid")
        request = bridge._read(directory, name)
        formed_at = core.publish._bound_stat(core._BoundFile(directory, name)).st_mtime_ns / 1000000000
        if (not isinstance(request, dict) or request.get("schema") != 1 or
                (reset_after is not None and formed_at > reset_after) or
                request.get("operation") not in {"verify_target", "inspect_catalog", "inspect_empty_draft", "find_drafts", "read_draft"} or
                request.get("binding") != binding or request.get("binding_sha256") != bridge._digest(binding) or
                request.get("session_id") != directory.path.name or request.get("owner_pid") != worker_pid or
                name.split("-", 1)[1] != str(request.get("id")) + ".json" or
                type(request.get("deadline_epoch")) not in (int, float) or
                not math.isfinite(request["deadline_epoch"]) or request["deadline_epoch"] >= time.time()):
            raise WorkerError("platform_draft_assistant_reset_not_readonly")
        rows[name] = bridge._digest(request)
    return rows


def assistant_retire_readonly_session(root, binding, context, *, request_id, request_binding_sha256, evidence_path,
                                      expected_snapshot=None):
    """Explicit operator isolation, never a claim that a worker promise finished.

    The operator attests the official kernel reset and the user's save/quit
    confirmation. This code independently checks process absence, expired
    read-only queues and handle authentication, then revokes that exact session.
    """
    core, bridge = api, api.platform_chrome_bridge
    path, handle, expected, formed_at = _assistant_reset_metadata(root, binding, context)
    evidence_path = Path(evidence_path)
    if not evidence_path.is_absolute():
        raise WorkerError("platform_draft_assistant_reset_invalid")
    with bridge._pinned(evidence_path.parent) as directory:
        attributes = directory.path.stat() if os.name == "nt" else os.fstat(directory.fd)
        if os.name != "nt" and attributes.st_mode & 0o077:
            raise WorkerError("platform_draft_assistant_reset_invalid")
        evidence = bridge._read(directory, evidence_path.name, maximum=32768)
    fields = {"schema", "request_id", "config_path", "controller_instance", "session_id", "binding_sha256",
              "operator", "chrome_quit", "official_kernel_reset", "authorized_by_user"}
    now = time.time()
    if (not isinstance(evidence, dict) or set(evidence) != fields or type(evidence.get("schema")) is not int or evidence["schema"] != 1 or
            evidence.get("request_id") != request_id or evidence.get("config_path") != str(path) or
            evidence.get("controller_instance") != expected["instance"] or evidence.get("session_id") != handle["session_id"] or
            evidence.get("binding_sha256") != request_binding_sha256 or evidence.get("operator") != "confirmed_terra"):
        raise WorkerError("platform_draft_assistant_reset_invalid")
    quit_info, reset, authorization = (evidence[key] for key in ("chrome_quit", "official_kernel_reset", "authorized_by_user"))
    if (not isinstance(quit_info, dict) or set(quit_info) != {"old_processes", "observed_none_at"} or
            not isinstance(reset, dict) or set(reset) != {"tool", "completed_at"} or
            reset["tool"] not in {"mcp__node_repl__js_reset", "mcp__node-repl__node_repl_reset"} or
            not isinstance(authorization, dict) or set(authorization) != {"text", "confirmed_at"} or
            not isinstance(authorization["text"], str) or not 1 <= len(authorization["text"].strip()) <= 2000 or
            type(authorization["confirmed_at"]) not in (int, float) or
            not math.isfinite(authorization["confirmed_at"]) or not 0 < authorization["confirmed_at"] <= now or
            any(type(value) not in (int, float) or not math.isfinite(value) or not now - 900 <= value <= now
                for value in (quit_info["observed_none_at"], reset["completed_at"])) or
            not authorization["confirmed_at"] <= quit_info["observed_none_at"] <= reset["completed_at"] or
            reset["completed_at"] < formed_at or
            not isinstance(quit_info["old_processes"], list) or not 1 <= len(quit_info["old_processes"]) <= 512):
        raise WorkerError("platform_draft_assistant_reset_invalid")
    processes = quit_info["old_processes"]
    if any(not isinstance(row, dict) or set(row) != {"pid", "start_identity"} or type(row["pid"]) is not int or row["pid"] <= 0 or
           not isinstance(row["start_identity"], str) or not 1 <= len(row["start_identity"]) <= 1000 for row in processes):
        raise WorkerError("platform_draft_assistant_reset_invalid")
    if len({row["pid"] for row in processes}) != len(processes):
        raise WorkerError("platform_draft_assistant_reset_invalid")
    if (_assistant_chrome_processes() or bridge._owner_alive(root, {"owner_pid": expected["pid"]}) or
            any(bridge._owner_alive(root, {"owner_pid": row["pid"]}) for row in processes)):
        raise WorkerError("platform_draft_assistant_reset_process_alive")
    with bridge._directory(Path(root).resolve(), handle["session_id"]) as directory, bridge._lock(directory):
        if expected_snapshot is not None:
            with _assistant_bound_evidence(root, binding, context) as bound:
                current = _assistant_reconcile_snapshot(root, binding, context, bound, directory, handle, expected)
            if any(current.get(key) != value for key, value in expected_snapshot.items() if key != "lease_sha256"):
                raise WorkerError("platform_draft_assistant_reconcile_changed")
        session = bridge._session(directory)
        if (bridge._restored_evidence(directory) or session["binding"] != binding or
                not hmac.compare_digest(session["token_sha256"], hashlib.sha256(handle["token"].encode()).hexdigest())):
            raise WorkerError("platform_draft_assistant_reset_invalid")
        health = bridge._health_record(directory, session)
        if health is not None and health["worker"] != expected:
            raise WorkerError("platform_draft_assistant_reset_invalid")
        queue = _assistant_readonly_queue(directory, binding, expected["pid"], reset_after=reset["completed_at"])
        # Check again immediately before revocation, after all bounded reads.
        if _assistant_chrome_processes() or bridge._owner_alive(root, {"owner_pid": expected["pid"]}):
            raise WorkerError("platform_draft_assistant_reset_process_alive")
        receipt = {"schema":1, "kind":"operator_attested_external_session_reset", "request_id":request_id,
            "request_binding_sha256":request_binding_sha256, "binding_sha256":bridge._digest(binding),
            "book_root":str(Path(root).resolve()), "session_id":handle["session_id"], "worker":expected,
            "evidence":evidence, "evidence_sha256":bridge._digest(evidence), "queue_sha256":bridge._digest(queue),
            "retired_at":time.time(), "execution_retired_by_operator":True,
            "worker_done":False, "cleanup_complete":False, "platform_written":False}
        if core.publish._bound_stat(core._BoundFile(directory, "operator-retired.json"), missing=True) is not None:
            previous = bridge._read(directory, "operator-retired.json", maximum=65536)
            if (not isinstance(previous, dict) or set(previous) != set(receipt) or
                    any(previous.get(key) != value for key, value in receipt.items() if key != "retired_at")):
                raise WorkerError("platform_draft_assistant_reset_invalid")
            receipt = previous  # Finish a prior revocation that stopped before lease persistence.
        else:
            bridge._write(directory, "operator-retired.json", receipt, replace=False)
        bridge._write(directory, "lease.json", {"schema":1, "session_id":handle["session_id"],
                      "expires":time.time(), "detached":True}, replace=True)
    return receipt


def _assistant_reconcile_snapshot(root, binding, context, bound, session_dir, handle, expected):
    """Read the existing reconciliation gates under the caller's pinned locks."""
    core, bridge = api, api.platform_chrome_bridge
    if bound is None or bound["receipt"] is not None or bridge._restored_evidence(session_dir):
        raise WorkerError("platform_draft_assistant_reconcile_invalid")
    if bridge._owner_alive(root, {"owner_pid": expected["pid"]}):
        raise WorkerError("platform_draft_assistant_reconcile_process_alive")
    session = bridge._session(session_dir)
    if (session["binding"] != binding or not hmac.compare_digest(
            session["token_sha256"], hashlib.sha256(handle["token"].encode()).hexdigest())):
        raise WorkerError("platform_draft_assistant_reconcile_invalid")
    health = bridge._health_record(session_dir, session)
    if health is not None and health["worker"] != expected:
        raise WorkerError("platform_draft_assistant_reconcile_invalid")
    lease = bridge._read(session_dir, "lease.json", maximum=16384)
    if (not isinstance(lease, dict) or lease.get("session_id") != handle["session_id"] or
            type(lease.get("detached")) is not bool or type(lease.get("expires")) not in (int, float) or
            not math.isfinite(lease["expires"]) or
            (lease["expires"] > time.time() and lease["detached"] is not True)):
        raise WorkerError("platform_draft_assistant_reconcile_invalid")
    queue = _assistant_readonly_queue(session_dir, binding, expected["pid"])
    latest = _assistant_ownership_snapshot(bound["directory"], handle["session_id"], bound["fingerprint"], expected)
    if _assistant_health_browser(bound["directory"], handle["session_id"], bound["fingerprint"], expected) != latest["browser_id"]:
        raise WorkerError("platform_draft_assistant_reconcile_invalid")
    return {"config_path": context["config_path"], "controller_instance": context["controller_instance"],
            "session_id": handle["session_id"], "browser_id": latest["browser_id"],
            "owned_tab_ids": list(latest["owned_tab_ids"]), "ownership_observed_at_ms": latest["observed_at_ms"],
            "binding_sha256": bound["fingerprint"], "config_sha256": bridge._digest(bound["config"]),
            "handle_sha256": bridge._digest(handle), "session_sha256": bridge._digest(session),
            "lease_sha256": bridge._digest(lease), "queue_sha256": bridge._digest(queue),
            "ownership_sha256": bridge._digest(latest)}


def assistant_reconcile_preflight(root, binding, context):
    """Read-only eligibility and immutable identities; never renew or detach a lease."""
    bridge = api.platform_chrome_bridge
    _, handle, expected, _ = _assistant_reset_metadata(root, binding, context)
    with _assistant_bound_evidence(root, binding, context) as bound:
        if bound is None:
            raise WorkerError("platform_draft_assistant_reconcile_invalid")
        with bridge._directory(Path(root).resolve(), handle["session_id"]) as session_dir, bridge._lock(session_dir):
            return _assistant_reconcile_snapshot(root, binding, context, bound, session_dir, handle, expected)


def assistant_reconcile_session(root, binding, context, *, request_id, request_binding_sha256, evidence_path,
                                expected_snapshot=None):
    """Record a machine-verified absent read-only session; never a worker success.

    The host supplies only the observed tab inventory. This function
    independently authenticates the queue, proves its controller is dead and
    every dispatched request expired, requires a non-live lease, and binds the
    observation to the session's own health and ownership receipts.
    """
    core, bridge = api, api.platform_chrome_bridge
    if (not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id) or
            not isinstance(request_binding_sha256, str) or
            not re.fullmatch(r"[0-9a-f]{64}", request_binding_sha256)):
        raise WorkerError("platform_draft_assistant_reconcile_invalid")
    path, handle, expected, formed_at = _assistant_reset_metadata(root, binding, context)
    evidence_path = Path(evidence_path)
    if not evidence_path.is_absolute():
        raise WorkerError("platform_draft_assistant_reconcile_invalid")
    with bridge._pinned(evidence_path.parent) as directory:
        attributes = directory.path.stat() if os.name == "nt" else os.fstat(directory.fd)
        if os.name != "nt" and attributes.st_mode & 0o077:
            raise WorkerError("platform_draft_assistant_reconcile_invalid")
        evidence = bridge._read(directory, evidence_path.name, maximum=32768)
    with _assistant_bound_evidence(root, binding, context) as bound:
        if bound is None:
            raise WorkerError("platform_draft_assistant_reconcile_invalid")
        if bound["receipt"] is not None:
            raise WorkerError("platform_draft_assistant_reconcile_conflict")
        if bridge._owner_alive(root, {"owner_pid": expected["pid"]}):
            raise WorkerError("platform_draft_assistant_reconcile_process_alive")
        with bridge._directory(Path(root).resolve(), handle["session_id"]) as session_dir, \
                bridge._lock(session_dir):
            if expected_snapshot is not None and _assistant_reconcile_snapshot(
                    root, binding, context, bound, session_dir, handle, expected) != expected_snapshot:
                raise WorkerError("platform_draft_assistant_reconcile_changed")
            if bridge._restored_evidence(session_dir):
                raise WorkerError("platform_draft_assistant_reconcile_invalid")
            session = bridge._session(session_dir)
            if (session["binding"] != binding or not hmac.compare_digest(
                    session["token_sha256"], hashlib.sha256(handle["token"].encode()).hexdigest())):
                raise WorkerError("platform_draft_assistant_reconcile_invalid")
            health = bridge._health_record(session_dir, session)
            if health is not None and health["worker"] != expected:
                raise WorkerError("platform_draft_assistant_reconcile_invalid")
            queue = _assistant_readonly_queue(session_dir, binding, expected["pid"])
            latest = _assistant_ownership_snapshot(bound["directory"], handle["session_id"],
                                                   bound["fingerprint"], expected)
            receipt = {"schema": 1, "kind": "machine_verified_absent_session",
                       "request_id": request_id, "request_binding_sha256": request_binding_sha256,
                       "binding_sha256": bound["fingerprint"],
                       "book_root": str(Path(root).resolve()), "session_id": handle["session_id"],
                       "worker": expected,
                       "browser_id": evidence.get("browser_id") if isinstance(evidence, dict) else None,
                       "owned_tab_ids": list(latest["owned_tab_ids"]),
                       "queue_sha256": bridge._digest(queue), "evidence": evidence,
                       "evidence_sha256": bridge._digest(evidence),
                       "observed_at_ms": evidence.get("observed_at_ms") if isinstance(evidence, dict) else None,
                       "reconciled_at": time.time(), "worker_done": False, "cleanup_complete": True,
                       "platform_written": False}
            if not _assistant_reconciled_valid(
                    receipt, root=root, binding=binding, context=context,
                    config_dir=bound["directory"], session_dir=session_dir, handle=handle,
                    expected=expected, fingerprint=bound["fingerprint"], config_stat=bound["config_stat"],
                    request_id=request_id, request_binding_sha256=request_binding_sha256,
                    mode="inspect_drafts", queue=queue):
                raise WorkerError("platform_draft_assistant_reconcile_invalid")
            if core.publish._bound_stat(core._BoundFile(session_dir, "worker-reconciled.json"),
                                        missing=True) is not None:
                previous = bridge._read(session_dir, "worker-reconciled.json", maximum=65536)
                if (not isinstance(previous, dict) or set(previous) != _RECONCILED_FIELDS or
                        any(previous.get(key) != value for key, value in receipt.items()
                            if key != "reconciled_at")):
                    raise WorkerError("platform_draft_assistant_reconcile_conflict")
                return previous
            bridge._write(session_dir, "worker-reconciled.json", receipt, replace=False)
    return receipt


def assistant_retirement_status(root, binding, context, *, request_id, request_binding_sha256):
    """Read the specific operator isolation receipt, not automatic cleanup."""
    core, bridge = api, api.platform_chrome_bridge
    try:
        _, handle, expected, formed_at = _assistant_reset_metadata(root, binding, context)
        with bridge._directory(Path(root).resolve(), handle["session_id"]) as directory:
            receipt = bridge._read(directory, "operator-retired.json", maximum=65536)
            session = bridge._session(directory)
            lease = bridge._read(directory, "lease.json", maximum=16384)
            fields = {"schema", "kind", "request_id", "request_binding_sha256", "binding_sha256", "book_root",
                "session_id", "worker", "evidence", "evidence_sha256", "queue_sha256", "retired_at",
                "execution_retired_by_operator", "worker_done", "cleanup_complete", "platform_written"}
            evidence = receipt.get("evidence") if isinstance(receipt, dict) else None
            reset = evidence.get("official_kernel_reset") if isinstance(evidence, dict) else None
            reset_at = reset.get("completed_at") if isinstance(reset, dict) else None
            if (type(reset_at) not in (int, float) or not math.isfinite(reset_at) or reset_at < formed_at or
                    not isinstance(receipt, dict) or set(receipt) != fields or type(receipt.get("schema")) is not int or receipt["schema"] != 1 or
                    receipt.get("kind") != "operator_attested_external_session_reset" or
                    receipt.get("request_id") != request_id or receipt.get("request_binding_sha256") != request_binding_sha256 or
                    receipt.get("binding_sha256") != bridge._digest(binding) or receipt.get("book_root") != str(Path(root).resolve()) or
                    receipt.get("session_id") != handle["session_id"] or receipt.get("worker") != expected or
                    receipt.get("execution_retired_by_operator") is not True or receipt.get("worker_done") is not False or
                    receipt.get("cleanup_complete") is not False or receipt.get("platform_written") is not False or
                    receipt.get("evidence_sha256") != bridge._digest(receipt.get("evidence")) or
                    session["binding"] != binding or bridge._restored_evidence(directory) or
                    not hmac.compare_digest(session["token_sha256"], hashlib.sha256(handle["token"].encode()).hexdigest()) or
                    not isinstance(lease, dict) or lease.get("session_id") != handle["session_id"] or lease.get("detached") is not True or
                    receipt.get("queue_sha256") != bridge._digest(_assistant_readonly_queue(directory, binding, expected["pid"], reset_after=reset_at))):
                return False
        return True
    except (core.StoryError, WorkerError, OSError, ValueError, KeyError, TypeError):
        return False


class AssistantBridgeSession:
    """A local queue controller, never a model identity or a browser host.

    Codex requires its actual Terra runtime. A separately authorized native host
    must use its own official Node REPL and browser SDK. This helper launches
    neither a model nor a browser, and cannot make an automatic controller ready.
    """

    def __init__(self, core, root, binding, *, connect_seconds=120,
                 execution_host="codex", native_model_authorized=False):
        if execution_host not in ("codex", "qoder-cn", "workbuddy") or type(native_model_authorized) is not bool:
            raise WorkerError("platform_chrome_worker_invalid_config")
        if execution_host != "codex" and native_model_authorized is not True:
            raise WorkerError("platform_chrome_worker_native_model_not_authorized")
        if execution_host == "codex" and native_model_authorized:
            raise WorkerError("platform_chrome_worker_invalid_config")
        self.core = core
        self.execution_host = execution_host
        self.native_model_authorized = native_model_authorized
        self.root = Path(root).resolve()
        self.binding = dict(binding)
        self.instance = uuid.uuid4().hex
        self.pid = os.getpid()
        self.context = None
        self.session_id = None
        self.bridge_detached = False
        self.worker_done = False
        self.cleanup_complete = False
        self.cleanup_status = "not_started"
        self.deadline = time.monotonic() + connect_seconds
        self.bootstrap_deadline_ms = int((time.time() + connect_seconds) * 1000)

    def _check_binding(self):
        controller = ChromeWorkerController(self.core)
        root, binding, fingerprint = controller._binding(self.root, self.binding["plan_id"],
            self.binding["profile_id"], self.binding["book_id"],
            self.binding.get("comparison_policy", "strict"), self.binding.get("body_encoding", "verbatim"))
        if root != self.root or binding != self.binding:
            raise WorkerError("platform_chrome_worker_binding_mismatch")
        return fingerprint

    def prepare(self):
        fingerprint = self._check_binding()
        existing = self.core.platform_chrome_bridge.worker_status(self.root, self.binding)
        if existing.get("attached") is True:
            # Do not adopt, stop, or re-label another session's healthy lease.
            raise WorkerError("platform_chrome_worker_busy")
        if self.execution_host == "workbuddy":
            self.workbuddy_cli = _workbuddy_cli_path()
            self.workbuddy_node = _workbuddy_node_path()
            sdk = _regular(Path(__file__).with_name("story_workbuddy_browser.mjs"))
            self.workbuddy_runner = _regular(Path(__file__).with_name("story_workbuddy_worker.mjs"))
        else:
            sdk = _sdk_path() if self.execution_host == "codex" else _native_sdk_path(self.execution_host)
        self.sdk = sdk
        self.module = _regular(Path(__file__).with_suffix(".mjs"))
        script = _regular(Path(__file__).with_name("story.py"))
        driver = _regular(Path(__file__).with_name("story_chrome_bridge.mjs"))
        python = _regular(sys.executable, executable=True)
        self.context = _private_context(self.core)
        self.config_path = self.context / "config.json"
        self.handle_path = self.context / "handle.json"
        config = {"schema": 1, "book_root": str(self.root), "book_id": self.binding["book_id"],
            "plan_id": self.binding["plan_id"], "profile_id": self.binding["profile_id"],
            "binding_sha256": fingerprint, "target": self.binding["target"],
            "comparison_policy": self.binding.get("comparison_policy", "strict"),
            "body_encoding": self.binding.get("body_encoding", "verbatim"),
            "worker": {"pid": self.pid, "instance": self.instance},
            "handle_path": str(self.handle_path), "stop_path": str(self.context / "stop.json"),
            "python_executable": str(python), "story_cli": str(script), "sdk_path": str(sdk),
            "driver_path": str(driver), "health_interval_seconds": 15, "idle_timeout_seconds": IDLE_SECONDS,
            "checkpoint_ms": 20000, "poll_interval_ms": 250, "browser_id": None,
            "bootstrap_deadline_ms": self.bootstrap_deadline_ms}
        if self.execution_host != "codex":
            config.update(execution_host=self.execution_host, native_model_authorized=True)
        if self.execution_host == "workbuddy":
            session = _workbuddy_session(self.binding)
            config.update(workbuddy_cli_path=str(self.workbuddy_cli),
                workbuddy_cli_sha256=hashlib.sha256(self.workbuddy_cli.read_bytes()).hexdigest(),
                workbuddy_session=session,
                workbuddy_state_directory=str(Path.home() / ".story-skill/workbuddy-browser-sessions" / session))
            self.workbuddy_config = {key: config[key] for key in
                ("workbuddy_cli_path", "workbuddy_cli_sha256", "workbuddy_session", "workbuddy_state_directory")}
        self._check_binding()
        _write_private(self.config_path, config, core=self.core)
        self.cleanup_status = "pending"

    def packet(self):
        if self.execution_host == "workbuddy":
            return {"execution_origin": "assistant_session", "model_verification": "executor_runtime_required",
                "execution_host": "workbuddy", "model_policy": "user_authorized_native", "native_model_authorized": True,
                "controller_pid": self.pid, "controller_instance": self.instance,
                "config_path": str(self.config_path), "module_path": str(self.module), "sdk_path": str(self.sdk),
                "runtime_kind": "agent_browser_cli", **self.workbuddy_config,
                "runner_argv": [str(self.workbuddy_node), str(self.workbuddy_runner), "--config", str(self.config_path)],
                "instructions": "仅在用户已授权的 WorkBuddy AI 原生任务中，以官方 agent-browser 独立会话执行 runner_argv 一次。"
                    "这不是 Node REPL 或 Qoder SDK 入口。CLI 运行期间持续等待同一进程，不重复启动；"
                    "浏览器须已在本任务专属会话登录并打开所选作品管理页，不复制个人浏览器登录资料。"
                    "不得借用 Codex 会话、修改配置、重放未知写入或把调用者模型名当成运行认证。"
                    "本地控制器会等待实际健康回执；完成后只清理本轮自建页，保留登录页。"}
        native = self.execution_host != "codex"
        options = ", {sdkFactory: storyAssistantSDKFactory}" if native else ""
        call = "var storyAssistantCheckpoint = await storyAssistantWorkerModule.runChromeWorker(" + \
            json.dumps(str(self.config_path), ensure_ascii=False) + options + ");\nnodeRepl.write(storyAssistantCheckpoint);"
        first = "var storyAssistantWorkerModule = await import(" + json.dumps(self.module.as_uri(), ensure_ascii=False) + ");\n"
        if native:
            setup = "setupQoderBrowserRuntime" if self.execution_host == "qoder-cn" else "setupWorkBuddyBrowserRuntime"
            first += ("var storyAssistantHostSDK = await import(" + json.dumps(self.sdk.as_uri(), ensure_ascii=False) + ");\n"
                      "var storyAssistantSDKFactory = () => storyAssistantWorkerModule." + setup + "(storyAssistantHostSDK);\n")
        policy = ("本次用户已明确允许 " + self.execution_host + " 使用其原生模型进行电脑操作；"
                  "仅在该宿主自己的官方 node_repl 会话上下文中执行，不得在 Codex 或其他宿主代执行此原生入口。"
                  "不得自造 transport、复制令牌或把调用者填写的模型名称当作宿主证明。"
                  "调用 node_repl 时使用 timeout_ms=tool_timeout_ms、yield_time_ms=tool_yield_time_ms。"
                  "若工具返回 running/cell_id，必须用 node_repl_wait 等待同一 cell_id 至工具终态，"
                  "之后才读取 checkpoint；工具未完成前不得调用 next_call、重复 first_call 或重置 REPL。" if native else
                  "先由运行环境确认实际执行模型为 gpt-6.1-terra；未知或不匹配时停止。")
        packet = {"execution_origin": "assistant_session", "model_verification": "executor_runtime_required",
            "execution_host": self.execution_host, "model_policy": "user_authorized_native" if native else "terra_required",
            "controller_pid": self.pid, "controller_instance": self.instance,
            "tool_timeout_ms": 240000,
            "config_path": str(self.config_path), "module_path": str(self.module),
            "first_call": first + call,
            "next_call": call,
            "instructions": policy +
                "此 PID 仅是本地队列控制器，不是模型身份证明。当前 CLI 正在等待，"
                "请在官方 Node REPL 中按 tool_timeout_ms 执行 first_call，再在同一 REPL 和模块实例连续 await next_call，"
                "每轮在已完成SDK调用之间安全暂停，返回时没有浏览器调用在途；同一已领取操作会在下一轮接续。"
                "直到 done=true。不得重置、重新导入、重发队列操作或绕过官方拒绝；"
                "失败时停止并读取助手任务状态。完成后等待本任务分离；不要退出共享宿主或 Chrome。"}
        if native:
            packet.update(native_model_authorized=True, sdk_path=str(self.sdk), tool_yield_time_ms=60000)
        return packet

    def _handle(self):
        if self.context is None or not self.handle_path.exists():
            return None
        handle = self.core.platform_chrome_bridge._external(self.handle_path)
        if (not isinstance(handle, dict) or handle.get("book_root") != str(self.root) or
                handle.get("book_id") != self.binding["book_id"] or
                handle.get("binding_sha256") != self.core.platform_chrome_bridge._digest(self.binding) or
                not isinstance(handle.get("session_id"), str) or
                not re.fullmatch(r"[0-9a-f]{32}", handle["session_id"])):
            raise WorkerError("platform_chrome_worker_binding_mismatch")
        if self.session_id is not None and handle["session_id"] != self.session_id:
            raise WorkerError("platform_chrome_worker_binding_mismatch")
        self.session_id = handle["session_id"]
        return handle

    def wait_ready(self, stop_requested):
        while time.monotonic() < self.deadline:
            if stop_requested and stop_requested():
                raise WorkerError("platform_draft_stop_requested")
            diagnostic = self.context / "worker-bootstrap-error.private.json"
            if diagnostic.exists():
                bridge = self.core.platform_chrome_bridge
                with bridge._pinned(self.context) as directory:
                    failure = bridge._read(directory, diagnostic.name, maximum=4096)
                if (not isinstance(failure, dict) or set(failure) != {"schema", "phase", "code", "error_name", "error_marker"}
                        or type(failure.get("schema")) is not int or failure["schema"] != 1
                        or failure.get("phase") not in {"sdk_setup", "browser_inventory", "waiting_for_chrome",
                                                       "browser_documentation", "existing_tab_probe"}
                        or failure.get("code") not in RESULT_CODES
                        or not failure["code"].startswith("platform_chrome_worker_")
                        or failure.get("error_name") not in ERROR_NAMES or failure.get("error_marker") not in ERROR_MARKERS):
                    raise WorkerError("platform_chrome_worker_invalid_packet")
                raise WorkerError(failure["code"])
            handle = self._handle()
            status = self.core.platform_chrome_bridge.worker_status(self.root, self.binding)
            # attach publishes the lease before its private handle. A short
            # awaiting-health window is not evidence of a foreign ready worker.
            if status.get("attached") is True and ((handle is not None and status.get("session_id") != self.session_id)
                    or (handle is None and status.get("ready") is True)):
                raise WorkerError("platform_chrome_worker_busy")
            if status.get("ready") is True:
                if (status.get("worker_pid") != self.pid or status.get("worker_instance") != self.instance):
                    raise WorkerError("platform_chrome_worker_binding_mismatch")
                self._check_binding()
                return
            time.sleep(min(.1, max(0, self.deadline - time.monotonic())))
        raise WorkerError("platform_chrome_worker_connect_timeout")

    def close(self, *, finish_seconds=10):
        if self.context is None:
            return
        # The worker checks this before bootstrap and between complete queue
        # operations. Never kill the assistant, browser, or a save in progress.
        record = {"schema": 1, "worker_instance": self.instance, "keep_readback": False}
        _write_private(self.context / "stop.json", record, core=self.core)
        deadline = time.monotonic() + finish_seconds
        while True:
            status = assistant_cleanup_status(self.root, self.binding, self.packet())
            for name in ("worker_done", "bridge_detached", "cleanup_complete", "cleanup_status"):
                setattr(self, name, status[name])
            if self.cleanup_complete:
                return
            if time.monotonic() >= deadline:
                return  # Expiry is not confirmation of worker/tab cleanup.
            time.sleep(min(.1, max(0, deadline - time.monotonic())))


@contextmanager
def assistant_session(root, binding, *, on_context, stop_requested=None, connect_seconds=120, finish_seconds=10,
                      execution_host="codex", native_model_authorized=False):
    """Prepare an explicit assistant queue; the caller supplies the authorized host.

    on_context persists/prints the fixed public-REPL instructions before waiting,
    allowing the assistant to drive the queue while its CLI controller runs.
    """
    if (type(connect_seconds) not in (int, float) or not 0 < connect_seconds <= 120 or
            type(finish_seconds) not in (int, float) or not 0 <= finish_seconds <= 30 or not callable(on_context)):
        raise WorkerError("platform_chrome_worker_invalid_config")
    session = AssistantBridgeSession(api, root, binding, connect_seconds=connect_seconds,
                                     execution_host=execution_host, native_model_authorized=native_model_authorized)
    try:
        session.prepare()
        on_context(session.packet())
        session.wait_ready(stop_requested)
        yield session
    finally:
        # A cleanup failure must not erase a core write/readback exception.
        failed = sys.exc_info()[0] is not None
        try:
            session.close(finish_seconds=finish_seconds)
        except Exception:
            session.bridge_detached = False
            session.cleanup_complete = False
            session.cleanup_status = "unverified"
            if not failed:
                raise


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
        self.model_verified = False
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

    def _bind_thread(self, result):
        thread = result.get("thread") if isinstance(result, dict) else None
        if (not isinstance(thread, dict) or thread.get("ephemeral") is not True or thread.get("path") is not None or
                not isinstance(thread.get("id"), str) or not thread["id"]):
            raise WorkerError("platform_chrome_worker_invalid_context")
        self.thread_id = thread["id"]
        model = result.get("model")
        code = ("platform_chrome_worker_model_unverified" if not isinstance(model, str) or not model else
                "platform_chrome_worker_model_mismatch" if model != WORKER_MODEL else None)
        if code:
            self._publish_worker_error("model_rejected", code=code, model_verified=False)
            raise WorkerError(code)
        self.model_verified = True
        self._emit("model_verified", model_verified=True)

    def _interrupt_owned_now(self):
        # Notification handlers run on the response reader: never wait for an
        # RPC here. Interrupt only the already pinned turn, without retries.
        if not self.thread_id or not self.turn_id or self.completed is not None:
            return
        with self._lock:
            self._sequence += 1
            try:
                self._write({"id": self._sequence, "method": "turn/interrupt",
                             "params": {"threadId": self.thread_id, "turnId": self.turn_id}})
            except Exception:
                pass

    def _bootstrap_error(self, item):
        text = item.get("text")
        if not isinstance(text, str) or len(text) > 256 or not text.startswith(BOOTSTRAP_PREFIX):
            return None
        try:
            value = json.loads(text[len(BOOTSTRAP_PREFIX):])
        except (TypeError, ValueError):
            return None
        return value["code"] if (isinstance(value, dict) and set(value) == {"code"} and
            isinstance(value["code"], str) and value["code"] in BOOTSTRAP_ERRORS) else None

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
        if self.worker_error in ("platform_chrome_worker_model_unverified", "platform_chrome_worker_model_mismatch",
                                 "platform_chrome_worker_model_rerouted"):
            return
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
                 ("native pipe closed before response", "browser_transport_closed", "native_pipe_closed"),
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
        if method == "model/rerouted" and self.thread_id and params.get("threadId") == self.thread_id:
            if self.turn_id and params.get("turnId") != self.turn_id:
                return
            # Even a route back to Terra revokes this session. No redirect or
            # resumed model is accepted as the originally verified executor.
            self.model_verified = False
            self._publish_worker_error("model_rejected", code="platform_chrome_worker_model_rerouted",
                                       model_verified=False)
            self._interrupt_owned_now()
            return
        # The transport can deliver notifications about other threads. Neither
        # those, assistant prose, nor a failed tool's tagged text proves finish.
        if (((not self.thread_id and "thread/start" in self._pending_methods.values() and method == "model/rerouted") or
                (self.thread_id and not self.turn_id and params.get("threadId") == self.thread_id and
                 "turn/start" in self._pending_methods.values())) and
                method in ("turn/started", "turn/completed", "item/started", "item/completed", "error", "model/rerouted")):
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
        if not self.model_verified and method != "turn/completed":
            return
        if method == "turn/started":
            self._emit("model_turn_started")
        elif method == "error":
            fields = _provider_error(params.get("error"))
            # An explicit provider error stops this owned worker even if the
            # host offers retry; the controller never initiates another turn.
            code = ("platform_chrome_worker_model_unavailable" if fields["error_kind"] == "modelNotFound"
                    else "platform_chrome_worker_turn_failed")
            self._publish_worker_error("provider_error", code=code, **fields)
            self._interrupt_owned_now()
        elif method == "turn/completed":
            status = turn.get("status") if isinstance(turn, dict) else None
            if turn.get("error") is not None:
                fields = _provider_error(turn["error"])
                code = ("platform_chrome_worker_model_unavailable" if fields["error_kind"] == "modelNotFound"
                        else "platform_chrome_worker_turn_failed")
                self._publish_worker_error("provider_error", code=code, **fields)
            self.completed = {"status": status if status in ("completed", "interrupted", "failed") else "failed"}
            self.completed_event.set()
            self._emit("model_turn_completed", turn_status=self.completed["status"])
        elif method in ("item/started", "item/completed"):
            item = params.get("item", {})
            if not isinstance(item, dict):
                return
            if item.get("type") == "agentMessage":
                if not self._model_activity_seen:
                    self._model_activity_seen = True
                    self._emit("model_activity")
                code = self._bootstrap_error(item) if method == "item/completed" else None
                if code and self.tool_sequence == 0:
                    self._publish_worker_error("bootstrap_failed", code=code)
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
            elif item.get("type") in ("commandExecution", "fileChange", "dynamicToolCall", "collabAgentToolCall"):
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
                        fields = _provider_error(message["error"])
                        code = ("platform_chrome_worker_model_unavailable" if fields["error_kind"] == "modelNotFound"
                                else "platform_chrome_worker_rpc_error")
                        self._emit("rpc_failed", rpc=rpc, code=code, **fields)
                        future.set_exception(WorkerError(code))
                    elif "result" not in message:
                        future.set_exception(WorkerError("platform_chrome_worker_invalid_message"))
                    else:
                        # Publish the owned turn id before delivering its RPC
                        # result: a fast tool can emit notifications before the
                        # initialize caller is scheduled again.
                        result = message["result"]
                        if rpc == "thread/start":
                            try:
                                self._bind_thread(result)
                                events, self._early_turn_events = self._early_turn_events, []
                                self._early_turn_bytes = 0
                                for event in events:
                                    self._observe(event)
                            except WorkerError as error:
                                future.set_exception(error)
                                continue
                        if rpc == "turn/start" and isinstance(result, dict):
                            turn = result.get("turn", {})
                            if isinstance(turn, dict) and isinstance(turn.get("id"), str) and turn["id"]:
                                self.turn_id = turn["id"]
                                events, self._early_turn_events = self._early_turn_events, []
                                self._early_turn_bytes = 0
                                for event in events:
                                    self._observe(event)
                                if not self.model_verified:
                                    self._interrupt_owned_now()
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
        if method in ("thread/start", "turn/start") and (any(key in params for key in ("effort", "reasoningEffort")) or
                ("model" in params and (method != "thread/start" or params["model"] != WORKER_MODEL))):
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
                if (set(params) != {"cwd", "ephemeral", "approvalPolicy", "sandbox", "experimentalRawEvents", "developerInstructions", "model", "allowProviderModelFallback"} or
                        params["cwd"] != str(self._sandbox_roots[2]) or params["approvalPolicy"] != "never" or
                        params["sandbox"] != "read-only" or params["experimentalRawEvents"] is not False or
                        params["model"] != WORKER_MODEL or params["allowProviderModelFallback"] is not False):
                    raise WorkerError("platform_chrome_worker_invalid_context")
            elif (set(params) != {"threadId", "input", "sandboxPolicy"} or params["threadId"] != self.thread_id or
                    params["input"] != [{"type": "text", "text": self._fixed_prompt}] or params["sandboxPolicy"] != policy):
                raise WorkerError("platform_chrome_worker_invalid_context")
        with self._lock:
            if method == "turn/start" and (not self.model_verified or self.worker_error):
                raise WorkerError(self.worker_error or "platform_chrome_worker_model_unverified")
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
            "model": WORKER_MODEL, "allowProviderModelFallback": False,
            "developerInstructions": "Use only the verified gpt-6.1-terra executor. Only execute the fixed, user-requested Chrome worker module via public node_repl/js. Allow only official discovery of that exact tool and its documented invocation wrapper. Do not use shell, other files, other tools/tasks, model changes, or arbitrary browser actions. Honor all official authorization denials; never bypass or fall back."}, timeout=remaining())
        # The response reader pins and verifies the exact model before releasing
        # this RPC. A missing identity or redirect can never start the turn.
        if not self.model_verified or self.worker_error:
            raise WorkerError(self.worker_error or "platform_chrome_worker_model_unverified")
        result = self.request("turn/start", {"threadId": self.thread_id,
            "input": [{"type": "text", "text": prompt}], "sandboxPolicy": policy}, timeout=remaining())
        self.turn_id = result.get("turn", {}).get("id")
        if not isinstance(self.turn_id, str):
            raise WorkerError("platform_chrome_worker_invalid_context")
        if not self.model_verified:
            raise WorkerError(self.worker_error or "platform_chrome_worker_model_unverified")

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
        # A confirmed bootstrap transport failure pauses new launches for this
        # service, across plan/range changes. It does not revoke a healthy turn
        # or assert that a previous platform write did not happen. There is no
        # user-supplied reset/model-verified flag; host recovery remains separate.
        self._connection_suspension = None

    def connection_suspension(self):
        with self._lock:
            return dict(self._connection_suspension) if self._connection_suspension else None

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
                if type(fields.get("model_verified")) is bool:
                    record["model_verified"] = fields["model_verified"]
                    entry["model_verified"] = fields["model_verified"]
                if isinstance(fields.get("error_kind"), str) and fields["error_kind"] in PROVIDER_ERRORS:
                    record["error_kind"] = fields["error_kind"]
                    entry.setdefault("error_kind", fields["error_kind"])
                for name, low, high in (("rpc_error_code", -(2**31), 2**31 - 1), ("http_status", 100, 599)):
                    if type(fields.get(name)) is int and low <= fields[name] <= high:
                        record[name] = fields[name]
                        entry.setdefault(name, fields[name])
                if type(fields.get("processed")) is int and 0 <= fields["processed"] <= 10000:
                    record["processed"] = fields["processed"]
                if (phase in ("bridge_ready", "bridge_resumed") or
                        fields.get("worker_state") in ("ready", "busy") or record.get("processed", 0) > 0):
                    entry["execution_seen"] = True
                if (phase == "worker_checkpoint" and fields.get("worker_state") == "failed" and
                        fields.get("code") == "platform_chrome_worker_browser_transport_closed" and
                        fields.get("error_marker") == "native_pipe_closed" and
                        fields.get("failure_phase") in ("sdk_setup", "browser_inventory") and
                        type(fields.get("processed")) is int and fields["processed"] == 0 and
                        not entry.get("execution_seen") and entry["state"] != "ready" and
                        entry.get("browser_id") is None and entry.get("model_verified") is True and
                        getattr(entry.get("client"), "model_verified", False) is True):
                    self._connection_suspension = self._connection_suspension or {
                        "available": False, "uses_codex_quota": True, "retry_blocked": True,
                        "reason": CONNECTION_SUSPENDED, "cause": fields["code"],
                        "failure_phase": fields["failure_phase"]}
                if "code" in fields:
                    record["code"] = fields["code"] if fields["code"] in DIAGNOSTIC_CODES else "platform_chrome_worker_failed"
                    if phase in ("tool_failed", "model_rejected", "provider_error", "bootstrap_failed") or (phase == "worker_checkpoint" and fields.get("worker_state") == "failed"):
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
        verified = entry.get("model_verified") is True and getattr(entry.get("client"), "model_verified", False) is True
        return {"ok": True, "state": state, "ready": entry["state"] == "ready" and not pending and verified,
                "owned": True, "uses_codex_quota": True, "binding_sha256": entry["fingerprint"],
                "owned_worker_id": entry["instance"],
                "requested_model": WORKER_MODEL,
                "resolved_model": WORKER_MODEL if verified else None,
                "model_verified": verified,
                **({"retry_blocked": True} if self._connection_suspension and entry["state"] != "ready" else {}),
                "error_kind": entry.get("error_kind"), "rpc_error_code": entry.get("rpc_error_code"),
                "http_status": entry.get("http_status"),
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
                not client.model_verified or not client.turn_id or (client.thread_id, client.turn_id) != entry.get("owned_turn") or
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
                entry["execution_seen"] = True
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
            if self._connection_suspension:
                raise WorkerError(CONNECTION_SUSPENDED)
            external = self.core.platform_chrome_bridge.worker_status(root, binding)
            if external.get("ready") is True:
                return self._unverified_external()
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
        if external.get("ready") is True:
            return self._unverified_external()
        return {"ok": True, "state": "ready" if external.get("ready") else "closed",
                "ready": external.get("ready") is True, "owned": False, "uses_codex_quota": False,
                "platform_target_verified": False}

    @staticmethod
    def _unverified_external():
        # A bridge health receipt proves connectivity, not its execution model.
        # Do not claim, stop, replace or inherit another session's permission.
        return {"ok": True, "state": "failed", "ready": False, "owned": False,
                "uses_codex_quota": False, "platform_target_verified": False,
                "error": "platform_chrome_worker_model_unverified", "requested_model": WORKER_MODEL,
                "resolved_model": None, "model_verified": False}

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
        while (client.model_verified and client.worker_done is None and client.completed is None and not client.failure and
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
                with self._lock:
                    # Another binding can fail while this entry is still
                    # validating local storage. Serialize the actual launch
                    # with suspension, not just the earlier start() request.
                    if self._connection_suspension:
                        raise WorkerError(CONNECTION_SUSPENDED)
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
                        raise WorkerError("platform_chrome_worker_tool_not_started" if client.tool_sequence == 0
                                          else "platform_chrome_worker_not_ready")
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
                            entry["execution_seen"] = True
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
    """Check admission and dependencies. Never creates a model/tool context."""
    with _CONTROLLER_LOCK:
        suspension = _CONTROLLER.connection_suspension() if _CONTROLLER is not None else None
    if suspension:
        return suspension
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
