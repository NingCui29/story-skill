"""Authenticated local operations for a host controlling an existing Chrome tab.

This transport never starts a browser, connects CDP, or reads browser profiles.
The host explicitly attaches a private handle and claims each operation once.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
import uuid


DIRECTORY = ".story/platform-chrome-bridge"
COMMANDS = {"publish-chrome-" + name for name in ("attach", "next", "respond", "heartbeat", "detach")}
MAX_BYTES = 16 * 1024 * 1024
MAX_SESSIONS = 100
TTL_SECONDS = 120
OP_SECONDS = {"verify_target": 180, "find_drafts": 180, "read_draft": 180, "create_draft": 300}
ERROR_CODES = {"platform_browser_" + code for code in (
    "ambiguous_dom", "ambiguous_draft", "chapter_exists_not_draft", "editor_changed", "identity_mismatch",
    "identity_unavailable", "incomplete_dom", "incomplete_list", "incomplete_scope", "invalid_chapter",
    "invalid_identity", "invalid_profile", "invalid_remote_id", "login_required", "navigation_timeout",
    "not_draft", "not_new_draft", "not_open", "operation_failed", "save_unverified", "target_unverified",
    "unnumbered_draft", "unsafe_create", "unsafe_navigation", "unsafe_save", "unsafe_url", "unsupported_editor",
    "bridge_scope", "bridge_expired", "editor_in_use", "readback_required")}
_LOCAL_LOCK = threading.RLock()


def inject(core):
    global api
    api = core


def _fail(code, message):
    api.fail("platform_chrome_" + code, message)


def _stamp(value=None):
    return datetime.fromtimestamp(time.time() if value is None else value, timezone.utc).isoformat()


def _digest(value):
    return hashlib.sha256(api.dumps(value).encode("utf-8")).hexdigest()


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        _fail("invalid_handle", "本机 Chrome 桥接句柄或请求标识无效。")
    return value


def validate_profile(profile):
    """Retain the existing static profile rules, without entering an adapter."""
    profile = deepcopy(profile)
    mode = profile.pop("execution_mode", "playwright")
    if mode not in ("playwright", "host_assisted_cua"):
        _fail("invalid_profile", "不支持该浏览器执行模式。")
    if mode == "host_assisted_cua":
        allowed = {"schema_version", "platform", "origin", "login_origins", "browser_channel", "urls",
                   "create_action", "identity", "chapter_lists", "editor", "readback"}
        if set(profile) - allowed:
            _fail("invalid_profile", "桥接配置包含未支持的字段。")
        def inspect(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if str(key).lower() in {"script", "javascript", "js", "code", "evaluate", "exec", "expression", "html", "callback", "function"}:
                        _fail("invalid_profile", "页面配置不能包含可执行代码。")
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)
        inspect(profile)
    api.platform_browser.BrowserAdapter(profile)
    return mode


@contextmanager
def _directory(root, session_id=None, create=False):
    relative = DIRECTORY + ("/" + _id(session_id) if session_id else "")
    path = api.safe_path(Path(root), relative)
    # Windows traversal briefly denies directory-write sharing before its
    # child guard is installed. Serialize *before* acquiring any such pin so
    # another bridge thread cannot pin a writer's parent while waiting for the
    # session lock. Native path guards and the cross-process lock stay intact.
    with _LOCAL_LOCK, api._pinned_directory(path, create=create) as directory:
        if create:
            if os.name == "nt":
                os.chmod(directory.path, 0o700)
            else:
                os.fchmod(directory.fd, 0o700)
        api._verify_bound_directory(directory)
        yield directory


def _read(directory, name, *, maximum=MAX_BYTES, private=True):
    file = api._BoundFile(directory, name)
    before = api.publish._bound_stat(file)
    if (before.st_size > maximum or (private and os.name != "nt" and before.st_mode & 0o077) or
            (hasattr(os, "getuid") and before.st_uid != os.getuid())):
        _fail("unsafe_file", "Chrome 桥接文件权限或大小不安全。")
    with api._bound_reader(file) as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(maximum + 1)
        after_read = os.fstat(stream.fileno())
    after = api.publish._bound_stat(file)
    if (len(raw) > maximum or not os.path.samestat(before, opened) or not os.path.samestat(before, after) or
            before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns or
            opened.st_mtime_ns != after_read.st_mtime_ns):
        _fail("unsafe_file", "Chrome 桥接文件读取期间发生变化。")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError):
        _fail("invalid_message", "Chrome 桥接消息不是有效 JSON。")
    api._verify_bound_directory(directory)
    return value


def _rename(source, target, *, replace=False):
    """Retry only a Windows rename refused before its local state transition.

    Another process can briefly deny parent-write sharing while establishing
    its native directory pin. Keep the caller's pins and session lock, and
    recheck both file identities before retrying; never replay a host request.
    """
    if os.name != "nt":
        if replace:
            os.replace(source.name, target.name, src_dir_fd=source.directory.fd,
                       dst_dir_fd=target.directory.fd)
        else:
            api.platform_drafts._atomic_publish(source, target)
        return

    original = api.publish._bound_stat(source)
    destination = api.publish._bound_stat(target, missing=True)
    if not replace and destination is not None:
        _fail("unsafe_file", "Chrome 桥接目标文件已存在；不会覆盖。")

    def unchanged(before, after):
        if before is None or after is None:
            return before is after
        return (os.path.samestat(before, after) and before.st_size == after.st_size and
                before.st_mtime_ns == after.st_mtime_ns)

    deadline = time.monotonic() + 1.0
    while True:
        api._verify_bound_directory(source.directory)
        if target.directory is not source.directory:
            api._verify_bound_directory(target.directory)
        if (not unchanged(original, api.publish._bound_stat(source, missing=True)) or
                not unchanged(destination, api.publish._bound_stat(target, missing=True))):
            _fail("unsafe_file", "Chrome 桥接文件在本机写入期间发生变化；未重试。")
        try:
            if replace:
                os.replace(source.path, target.path)
            else:
                api.platform_drafts._atomic_publish(source, target)
            return
        except OSError as error:
            if getattr(error, "winerror", None) not in (32, 33) or time.monotonic() >= deadline:
                raise
            time.sleep(.01)


def _write(directory, name, value, *, replace=False):
    raw = api.dumps(value).encode("utf-8")
    if len(raw) > MAX_BYTES:
        _fail("message_limit", "Chrome 桥接消息超过大小限制。")
    destination = api._BoundFile(directory, name)
    if replace:
        api.publish._bound_stat(destination, missing=True)
    fd, staged = api._bound_stage(directory, ".chrome-stage-")
    try:
        if os.name == "nt":
            os.chmod(staged.path, 0o600)
        else:
            os.fchmod(fd, 0o600)
        with api._owned_fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        api._verify_bound_directory(directory)
        _rename(staged, destination, replace=replace)
        if os.name != "nt":
            os.fsync(directory.fd)
        api._verify_bound_directory(directory)
    finally:
        if api.publish._bound_stat(staged, missing=True) is not None:
            api._bound_unlink(staged)


@contextmanager
def _lock(directory):
    with _LOCAL_LOCK:
        file = api._BoundFile(directory, "session.lock")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(file.path, flags, 0o600) if os.name == "nt" else os.open(file.name, flags, 0o600, dir_fd=directory.fd)
        with api._owned_fdopen(fd, "r+b") as stream:
            held = os.fstat(stream.fileno())
            if not os.path.samestat(held, api.publish._bound_stat(file)):
                _fail("unsafe_file", "Chrome 桥接锁文件发生变化。")
            if held.st_size == 0:
                stream.write(b"0"); stream.flush()
            if os.name == "nt":
                import msvcrt
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _external(path, *, create_value=None):
    path = Path(path).expanduser().absolute()
    with _LOCAL_LOCK, api._pinned_directory(path.parent) as directory:
        if create_value is not None:
            _write(directory, path.name, create_value)
            return None
        return _read(directory, path.name)


def _binding(book, plan_id, profile_id):
    _, _, profile, binding = api.platform_drafts._preflight(book.root, plan_id, profile_id, book.meta("id"), True)
    if validate_profile(profile) != "host_assisted_cua":
        _fail("invalid_profile", "此配置未选择已有 Chrome 会话桥接。")
    with api.platform_drafts._checked_plan(book, plan_id, check_source=False) as (manifest, _, _):
        target = api.platform_drafts._target(manifest)
    binding.pop("mode", None)
    binding["target"] = target
    return binding, profile


def _lease(directory, session, *, extend=False, until=None):
    lease = _read(directory, "lease.json", maximum=16384)
    if (not isinstance(lease, dict) or lease.get("session_id") != session["session_id"] or lease.get("detached") is not False or
            type(lease.get("expires")) not in (int, float) or not math.isfinite(lease["expires"]) or
            lease["expires"] <= time.time()):
        _fail("worker_unavailable", "没有仍在心跳有效期内的已连接 Chrome 执行端；未启动操作。")
    if extend:
        lease["expires"] = max(time.time() + session["ttl_seconds"], until or 0)
        _write(directory, "lease.json", lease, replace=True)
    return lease


def _session(directory):
    session = _read(directory, "session.json", maximum=32768)
    if (not isinstance(session, dict) or session.get("schema") != 1 or
            session.get("session_id") != directory.path.name or not isinstance(session.get("binding"), dict) or
            session.get("binding_sha256") != _digest(session["binding"]) or
            not isinstance(session.get("token_sha256"), str) or
            not re.fullmatch(r"[0-9a-f]{64}", session["token_sha256"]) or
            type(session.get("ttl_seconds")) is not int or not 30 <= session["ttl_seconds"] <= 3600):
        _fail("invalid_message", "Chrome 执行端会话元数据无效。")
    return session


@contextmanager
def _authenticated(book, handle_path, *, extend=True):
    handle = _external(handle_path)
    if (not isinstance(handle, dict) or handle.get("book_id") != book.meta("id") or
            handle.get("book_root") != str(book.root) or not isinstance(handle.get("token"), str)):
        _fail("invalid_handle", "Chrome 桥接句柄不属于当前作品。")
    with _directory(book.root, handle.get("session_id")) as directory, _lock(directory):
        session = _session(directory)
        actual = hashlib.sha256(handle["token"].encode()).hexdigest()
        if (not hmac.compare_digest(actual, session.get("token_sha256", "")) or
                handle.get("binding_sha256") != session.get("binding_sha256")):
            _fail("invalid_handle", "Chrome 桥接句柄认证失败。")
        _lease(directory, session, extend=extend)
        yield directory, session


def attach(book, plan_id, profile_id, handle_path, ttl_seconds=TTL_SECONDS):
    api.integer(ttl_seconds, "ttl_seconds", 30)
    if ttl_seconds > 3600:
        _fail("invalid_handle", "执行端心跳期限超过限制。")
    binding, _ = _binding(book, plan_id, profile_id)
    available = worker_status(book.root, binding)
    if available["attached"]:
        _fail("worker_busy", "同一目标已有有效执行端，请复用原句柄或先分离。")
    if available["active_sessions"] >= MAX_SESSIONS:
        _fail("session_limit", "本书连接中的 Chrome 桥接会话过多；请先分离不用的执行端。")
    session_id, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
    session = {"schema": 1, "session_id": session_id, "binding": binding, "binding_sha256": _digest(binding),
               "token_sha256": hashlib.sha256(token.encode()).hexdigest(), "created_at": _stamp(), "ttl_seconds": ttl_seconds}
    with _directory(book.root, create=True):
        pass
    with _directory(book.root, session_id, create=True) as directory:
        _write(directory, "session.json", session)
        _write(directory, "lease.json", {"schema": 1, "session_id": session_id,
               "expires": time.time() + ttl_seconds, "detached": False})
    try:
        _external(handle_path, create_value={"schema": 1, "session_id": session_id, "book_root": str(book.root),
            "book_id": book.meta("id"), "binding_sha256": session["binding_sha256"], "token": token})
    except Exception:
        # An unusable handle must never leave an apparently attached worker.
        with _directory(book.root, session_id) as directory, _lock(directory):
            _write(directory, "lease.json", {"schema": 1, "session_id": session_id,
                "expires": time.time(), "detached": True}, replace=True)
        _fail("unsafe_file", "无法安全创建私有执行端句柄；会话未连接。")
    return {"ok": True, "session_id": session_id, "handle_path": str(Path(handle_path).absolute()),
            "ttl_seconds": ttl_seconds, "remote_accessed": False, "browser_started": False}


def worker_status(root, binding):
    path = api.safe_path(Path(root), DIRECTORY)
    if not path.exists():
        return {"attached": False, "state": "worker_unavailable", "active_sessions": 0}
    with _directory(root) as parent:
        names = os.listdir(parent.path if os.name == "nt" else parent.fd)
    names = [name for name in names if re.fullmatch(r"[0-9a-f]{32}", name)]
    # Retain expired/detached queues as recovery evidence without making their
    # lifetime count prevent the next attachment. The limit applies to live
    # workers; checking the lease must therefore precede counting or binding.
    candidates, active_sessions = [], 0
    for name in names:
        with _directory(root, name) as directory, _lock(directory):
            session = _session(directory)
            try:
                lease = _lease(directory, session)
            except api.StoryError as error:
                if error.code == "platform_chrome_worker_unavailable":
                    continue
                raise
            active_sessions += 1
            if active_sessions > MAX_SESSIONS:
                _fail("session_limit", "本书连接中的 Chrome 桥接会话过多。")
            if session.get("binding") != binding or session.get("binding_sha256") != _digest(binding):
                continue
            candidates.append({"attached": True, "state": "attached", "session_id": name,
                               "expires_at": _stamp(lease["expires"])})
    if len(candidates) > 1:
        _fail("worker_busy", "目标存在多个执行端；请先分离多余会话。")
    status = candidates[0] if candidates else {"attached": False, "state": "worker_unavailable"}
    return {**status, "active_sessions": active_sessions}


def _windows_process_alive(pid):
    """Observe a Windows process without sending a console control event."""
    if type(pid) is not int or not 0 < pid <= 0xffffffff:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        # SYNCHRONIZE grants zero-time waiting only; no termination or signal
        # rights are requested. WAIT_TIMEOUT means the process is still alive.
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            alive = kernel.WaitForSingleObject(handle, 0) == 0x00000102
        finally:
            closed = kernel.CloseHandle(handle)
        return bool(alive and closed)
    except (OSError, AttributeError, TypeError, ValueError, OverflowError):
        return False


def _owner_alive(root, request):
    pid = request.get("owner_pid")
    if type(pid) is not int or pid <= 0:
        return False
    try:
        if os.name == "nt":
            if not _windows_process_alive(pid):
                return False
        else:
            os.kill(pid, 0)
    except (OSError, TypeError, OverflowError):
        return False
    instance = request.get("owner_service_instance")
    if instance is not None:
        try:
            service = api.read_json(api.safe_path(Path(root), ".story/workbench-service.json"))
            return (service.get("instance") == instance and service.get("pid") == request["owner_pid"] and
                    service.get("stopped") is not True)
        except (OSError, ValueError, api.StoryError):
            return False
    return True


def next_request(book, handle_path):
    with _authenticated(book, handle_path) as (directory, session):
        names = sorted(os.listdir(directory.path if os.name == "nt" else directory.fd))
        for name in names:
            if not re.fullmatch(r"outbox-[0-9a-f]{32}\.json", name):
                continue
            request = _read(directory, name)
            if (not isinstance(request, dict) or request.get("schema") != 1 or
                    request.get("binding_sha256") != session["binding_sha256"] or request.get("binding") != session["binding"] or
                    request.get("session_id") != session["session_id"] or request.get("operation") not in OP_SECONDS or
                    type(request.get("deadline_epoch")) not in (int, float) or
                    not math.isfinite(request["deadline_epoch"]) or
                    type(request.get("owner_pid")) is not int or request["owner_pid"] <= 0 or
                    not isinstance(request.get("nonce"), str) or len(request["nonce"]) < 32):
                _fail("invalid_message", "队列请求不属于当前会话。")
            if request["deadline_epoch"] <= time.time() or not _owner_alive(book.root, request):
                continue
            destination = "claimed-" + _id(request["id"]) + ".json"
            source, target = api._BoundFile(directory, name), api._BoundFile(directory, destination)
            api.publish._bound_stat(source)
            if api.publish._bound_stat(target, missing=True) is not None:
                _fail("already_claimed", "请求已领取；不会重新派发。")
            _rename(source, target)
            _lease(directory, session, extend=True, until=request["deadline_epoch"] + 30)
            return {"ok": True, "request_id": request["id"], "request_path": str(target.path), "claimed_once": True}
        return {"ok": True, "request_id": None, "request_path": None, "claimed_once": False}


def respond(book, handle_path, request_id, input_path):
    response = _external(input_path)
    with _authenticated(book, handle_path) as (directory, session):
        request = _read(directory, "claimed-" + _id(request_id) + ".json")
        _validate_response(response, request)
        if request["deadline_epoch"] <= time.time() or not _owner_alive(book.root, request):
            _fail("request_expired", "请求已超时或调用进程已退出；保留未知结果，不再发送。")
        if response["ok"]:
            _validate_result(request["operation"], response["result"], request["binding"]["target"], request["params"])
        response_name = "response-" + request_id + ".json"
        if api.publish._bound_stat(api._BoundFile(directory, response_name), missing=True) is not None:
            _fail("already_responded", "请求已有回执；不会覆盖或再次执行。")
        _write(directory, response_name, response)
        response_path = str(directory.path / response_name)
    return {"ok": True, "request_id": request_id, "response_recorded": True,
            "response_path": response_path, "remote_accessed": False}


def _validate_response(response, request):
    expected = {"schema", "id", "nonce", "session_id", "binding_sha256", "ok"}
    if not isinstance(response, dict) or type(response.get("ok")) is not bool:
        _fail("invalid_message", "执行端回执字段无效。")
    expected.add("result" if response["ok"] else "error")
    if (set(response) != expected or type(response["schema"]) is not int or response["schema"] != 1 or any(response.get(key) != request.get(key)
            for key in ("id", "nonce", "session_id", "binding_sha256"))):
        _fail("invalid_message", "执行端回执与本次随机请求绑定不符。")
    if response["ok"] is False and (not isinstance(response["error"], str) or response["error"] not in ERROR_CODES):
        _fail("invalid_message", "执行端错误代码不在允许范围。")


def heartbeat(book, handle_path, detach=False):
    with _authenticated(book, handle_path) as (directory, session):
        if detach:
            _write(directory, "lease.json", {"schema": 1, "session_id": session["session_id"],
                "expires": time.time(), "detached": True}, replace=True)
        return {"ok": True, "session_id": session["session_id"], "detached": detach, "remote_accessed": False}


def _validate_result(operation, result, target, params):
    if operation == "verify_target":
        if result != target:
            api.fail("platform_browser_identity_mismatch", "宿主回读的账号或作品与冻结目标不一致。")
    elif operation == "find_drafts":
        if not isinstance(result, list) or len(result) > 10000:
            _fail("invalid_message", "完整草稿观察列表无效。")
        for item in result:
            api.platform_drafts._observation(item)
    elif operation == "read_draft":
        api.platform_drafts._observation(result, params["remote_id"])
    elif operation == "create_draft":
        api.platform_drafts._identifier(result, "remote_id")
    else:
        _fail("invalid_message", "不支持该宿主操作。")
    return result


class HostAdapter:
    execution_mode = "host_assisted_cua"

    def __init__(self, root, binding, profile, progress_callback=None):
        if validate_profile(profile) != self.execution_mode:
            _fail("invalid_profile", "此配置未选择已有 Chrome 会话桥接。")
        if profile.get("platform") == "fanqie" and profile.get("readback") is None:
            api.fail("platform_browser_readback_required", "番茄已有Chrome会话必须配置独立只读预览回读；不会打开编辑器核对旧稿。")
        self.root, self.binding, self.profile = Path(root), binding, deepcopy(profile)
        self.progress = progress_callback
        self.session_id = None

    def __enter__(self):
        status = worker_status(self.root, self.binding)
        if not status["attached"]:
            _fail("worker_unavailable", "未连接持有既有 Chrome 的宿主执行端；未启动操作。")
        self.session_id = status["session_id"]
        return self

    def __exit__(self, *args):
        return False

    def _request(self, operation, params):
        if self.session_id is None:
            _fail("worker_unavailable", "宿主执行端尚未连接。")
        request_id, nonce = uuid.uuid4().hex, secrets.token_urlsafe(32)
        deadline = time.time() + OP_SECONDS[operation]
        service_instance = None
        service_path = api.safe_path(self.root, ".story/workbench-service.json")
        if service_path.exists():
            service = api.read_json(service_path)
            if service.get("pid") == os.getpid():
                service_instance = service.get("instance")
        request = {"schema": 1, "id": request_id, "nonce": nonce, "session_id": self.session_id,
                   "binding": self.binding, "binding_sha256": _digest(self.binding), "profile": self.profile,
                   "operation": operation, "params": params, "deadline": _stamp(deadline), "deadline_epoch": deadline,
                   "owner_pid": os.getpid(), "owner_service_instance": service_instance}
        with _directory(self.root, self.session_id) as directory, _lock(directory):
            session = _session(directory)
            _lease(directory, session)
            if session["binding"] != self.binding:
                _fail("invalid_message", "宿主会话目标发生变化。")
            _write(directory, "outbox-" + request_id + ".json", request)
        if self.progress:
            self.progress({"stage": "waiting_host"})
        while time.time() < deadline:
            with _directory(self.root, self.session_id) as directory, _lock(directory):
                response_name = "response-" + request_id + ".json"
                response_file = api._BoundFile(directory, response_name)
                if api.publish._bound_stat(response_file, missing=True) is not None:
                    response = _read(directory, response_name)
                    _validate_response(response, request)
                    if response["ok"] is False:
                        api.fail(response["error"], "既有 Chrome 中的操作未能完成；请核对安全错误代码。")
                    return _validate_result(operation, response["result"], self.binding["target"], params)
                session = _session(directory)
                _lease(directory, session)
            time.sleep(.1)
        _fail("request_timeout", "宿主操作未在期限内返回；写入结果按未知处理，不重新派发。")

    def verify_target(self, **target):
        if target != self.binding["target"]:
            api.fail("platform_browser_identity_mismatch", "调用目标与已连接宿主不符。")
        return self._request("verify_target", {})

    def find_drafts(self, chapter):
        return self._request("find_drafts", {"chapter": chapter})

    def create_draft(self, chapter, title, body):
        return self._request("create_draft", {"chapter": chapter, "title": title, "body": body})

    def read_draft(self, remote_id):
        return self._request("read_draft", {"remote_id": remote_id})


def register_parser(sub, command):
    for name in sorted(COMMANDS):
        parser = command(name, "Authenticate an existing-Chrome host bridge; never starts a browser", integrity=False)
        parser.add_argument("--handle", required=True, help="Private 0600 session handle; tokens never appear in argv")
        if name.endswith("attach"):
            parser.add_argument("--id", required=True)
            parser.add_argument("--profile", required=True, help="Saved profile filename inside this book")
            parser.add_argument("--ttl-seconds", type=int, default=TTL_SECONDS)
        elif name.endswith("respond"):
            parser.add_argument("--request-id", required=True)
            parser.add_argument("--input", required=True, help="Private 0600 response JSON file")


def run(book, args):
    action = args.command.removeprefix("publish-chrome-")
    if action == "attach":
        return attach(book, args.id, args.profile, args.handle, args.ttl_seconds)
    if action == "next":
        return next_request(book, args.handle)
    if action == "respond":
        return respond(book, args.handle, args.request_id, args.input)
    return heartbeat(book, args.handle, detach=action == "detach")
