"""Authenticated local operations for a host controlling an existing Chrome tab.

This transport never starts a browser, connects CDP, or reads browser profiles.
The host explicitly attaches a private handle and claims each operation once.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import errno
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit


DIRECTORY = ".story/platform-chrome-bridge"
RESTORED_EVIDENCE = "restored-evidence.json"
COMMANDS = {"publish-chrome-" + name for name in ("attach", "next", "respond", "heartbeat", "health", "detach")}
MAX_BYTES = 16 * 1024 * 1024
MAX_SESSIONS = 100
TTL_SECONDS = 120
HEALTH_SECONDS = 45
HEALTH_CHALLENGE_SECONDS = 30
OP_SECONDS = {"verify_target": 180, "find_drafts": 180, "read_draft": 180, "create_draft": 300}
ERROR_CODES = {"platform_browser_" + code for code in (
    "ambiguous_dom", "ambiguous_draft", "chapter_exists_not_draft", "editor_changed", "identity_mismatch",
    "identity_unavailable", "incomplete_dom", "incomplete_list", "incomplete_scope", "invalid_chapter",
    "invalid_identity", "invalid_profile", "invalid_remote_id", "login_required", "navigation_timeout",
    "not_draft", "not_new_draft", "not_open", "operation_failed", "save_unverified", "target_unverified",
    "unnumbered_draft", "unsafe_create", "unsafe_navigation", "unsafe_save", "unsafe_url", "unsupported_editor",
    "bridge_scope", "bridge_expired", "editor_in_use", "readback_required")}
_LOCAL_LOCK = threading.RLock()
_LOCK_SECONDS = 10.0
_LOCK_HANDOFF_SECONDS = .01


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
    if mode == "host_assisted_cua":
        # The host driver proves the account on its guard page and both book
        # and remote identity from URLs. Other DOM sources remain standalone
        # capabilities; reject them before attaching or queuing any operation.
        if (profile["identity"]["account"].get("source") != "guard_dom" or
                profile["identity"]["book"].get("source") != "page_url" or
                profile["editor"]["remote_id"].get("source") != "page_url"):
            _fail("invalid_profile", "宿主模式须使用账号核对页DOM、作品URL标识和草稿URL标识。")
        if profile["editor"].get("ready") is None:
            _fail("invalid_profile", "宿主模式须配置编辑器的正向加载就绪标志，不能在创建草稿后才判断配置缺失。")
        for listing in profile["chapter_lists"]:
            if any(listing[name].get("source") not in (None, "dom") for name in ("chapter", "link")):
                _fail("invalid_profile", "宿主模式的目录章号和链接只支持DOM字段。")
        if profile.get("readback") and not profile["readback"]["header"]["title_separator"]:
            _fail("invalid_profile", "宿主模式的预览章号和标题须配置非空分隔符。")
        if profile["platform"] == "fanqie":
            # These exact management routes use the host's observed tab
            # switch, whose labels and scopes are fixed rather than guessed.
            groups = {}
            for url in [profile["urls"]["drafts"]] + [item["url"] for item in profile["chapter_lists"]]:
                parsed = urlsplit(url)
                if parsed.path.startswith("/main/writer/chapter-manage/") and parsed.query in ("type=1", "type=2"):
                    groups.setdefault((parsed.scheme, parsed.netloc, parsed.path), set()).add(url)
            for urls in groups.values():
                if len(urls) != 2:
                    continue
                for url in urls:
                    matches = [item for item in profile["chapter_lists"] if item["url"] == url]
                    if len(matches) != 1:
                        _fail("invalid_profile", "宿主管理页切换须为每个已核实的页签配置唯一目录。")
                    listing = matches[0]
                    scope, label = ("non_drafts", "章节管理") if urlsplit(url).query == "type=1" else ("drafts", "草稿箱")
                    coverage = listing["coverage"]
                    if (listing["scope"] != scope or coverage.get("source", "dom") != "dom" or
                            coverage.get("attribute") is not None or coverage["expected"] != label):
                        _fail("invalid_profile", "宿主管理页切换的目录范围和DOM页签名称须与已核实的映射一致。")
    return mode


@contextmanager
def _pinned(path, *, create=False):
    """Wait briefly for another Windows process to finish entering its pin.

    Failed native enters release their own handles. Only that acquisition is
    retried; the protected operation and context exit are each run once.
    """
    with ExitStack() as stack:
        deadline = time.monotonic() + 1.0
        while True:
            try:
                directory = stack.enter_context(api._pinned_directory(path, create=create))
                break
            except OSError as error:
                if (os.name != "nt" or getattr(error, "winerror", None) not in (32, 33) or
                        time.monotonic() >= deadline):
                    raise
                time.sleep(.01)
        yield directory


@contextmanager
def _directory(root, session_id=None, create=False):
    relative = DIRECTORY + ("/" + _id(session_id) if session_id else "")
    path = api.safe_path(Path(root), relative)
    # Windows traversal briefly denies directory-write sharing before its
    # child guard is installed. Serialize *before* acquiring any such pin so
    # another bridge thread cannot pin a writer's parent while waiting for the
    # session lock. Native path guards and the cross-process lock stay intact.
    with _LOCAL_LOCK, _pinned(path, create=create) as directory:
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


def _acquire_windows_lock(stream, verify, native):
    """Poll only a refused local lock acquisition, never its protected body."""
    deadline = time.monotonic() + _LOCK_SECONDS
    last_error = None

    def check_deadline():
        if time.monotonic() >= deadline:
            if last_error is not None:
                raise last_error
            raise TimeoutError(errno.ETIMEDOUT, "Chrome bridge lock acquisition timed out")

    while True:
        check_deadline()
        verify()
        check_deadline()
        stream.seek(0)
        try:
            native.locking(stream.fileno(), native.LK_NBLCK, 1)
            return deadline
        except OSError as failure:
            winerror = getattr(failure, "winerror", None)
            if not (winerror in (32, 33) or (winerror is None and
                    failure.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK))):
                raise
            last_error = failure
            check_deadline()
            # CRT's blocking mode waits in one-second steps and can repeatedly
            # miss short unlock windows. A small jitter avoids that fixed phase.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise last_error
            time.sleep(min(.005 + secrets.randbelow(6) / 1000, remaining))


@contextmanager
def _lock(directory, name="session.lock"):
    with _LOCAL_LOCK:
        file = api._BoundFile(directory, name)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(file.path, flags, 0o600) if os.name == "nt" else os.open(file.name, flags, 0o600, dir_fd=directory.fd)
        with api._owned_fdopen(fd, "r+b") as stream:
            held = os.fstat(stream.fileno())

            def verify():
                api._verify_bound_directory(directory)
                if (not os.path.samestat(held, os.fstat(stream.fileno())) or
                        not os.path.samestat(held, api.publish._bound_stat(file))):
                    _fail("unsafe_file", "Chrome 桥接锁文件发生变化。")

            verify()
            if held.st_size == 0:
                stream.write(b"0"); stream.flush()
            if os.name == "nt":
                import msvcrt
                deadline = _acquire_windows_lock(stream, verify, msvcrt)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                verify()
                if os.name == "nt" and time.monotonic() >= deadline:
                    raise TimeoutError(errno.ETIMEDOUT, "Chrome bridge lock acquisition timed out")
                yield
            finally:
                if os.name == "nt":
                    stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    # Keep this process's local lock during the handoff so its
                    # polling thread cannot immediately reacquire the byte lock.
                    time.sleep(_LOCK_HANDOFF_SECONDS)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _external(path, *, create_value=None):
    path = Path(path).expanduser().absolute()
    with _LOCAL_LOCK, _pinned(path.parent) as directory:
        if create_value is not None:
            _write(directory, path.name, create_value)
            return None
        return _read(directory, path.name)


def _binding(book, plan_id, profile_id, comparison_policy="strict", body_encoding="verbatim"):
    _, _, profile, binding = api.platform_drafts._preflight(book.root, plan_id, profile_id, book.meta("id"), True,
        comparison_policy=comparison_policy, body_encoding=body_encoding)
    if validate_profile(profile) != "host_assisted_cua":
        _fail("invalid_profile", "此配置未选择已有 Chrome 会话桥接。")
    with api.platform_drafts._checked_plan(book, plan_id, check_source=False) as (manifest, _, _):
        target = api.platform_drafts._target(manifest)
    binding.pop("mode", None)
    binding["target"] = target
    return binding, profile


def _restored_evidence(directory):
    return api.publish._bound_stat(api._BoundFile(directory, RESTORED_EVIDENCE), missing=True) is not None


@contextmanager
def _attachment_lock(root):
    # Serialize discovery and handle publication across processes. A per-session
    # lock alone cannot prevent two attach calls from both observing no worker.
    with _LOCAL_LOCK, _pinned(api.safe_path(Path(root), ".story")) as directory:
        with _lock(directory, ".platform-chrome-attach.lock"):
            yield


def _uninitialized_session(directory):
    # A handle is published only after session.json and lease.json. Preserve
    # incomplete metadata, but never ignore a lease, request or unknown payload.
    if api.publish._bound_stat(api._BoundFile(directory, "lease.json"), missing=True) is not None:
        return False
    names = os.listdir(directory.path if os.name == "nt" else directory.fd)
    for name in names:
        if re.fullmatch(r"\.story-pin-[0-9a-f]{32}", name):
            continue
        if name not in ("session.json", "session.lock") and not re.fullmatch(r"\.chrome-stage-[0-9a-f]{32}", name):
            return False
        api.publish._bound_stat(api._BoundFile(directory, name))
    if "session.json" in names:
        _session(directory)  # Invalid published metadata is never orphan evidence.
    return True


def _lease(directory, session, *, extend=False, until=None):
    if _restored_evidence(directory):
        _fail("worker_unavailable", "备份中的 Chrome 会话仅作为恢复证据保留；请重新连接执行端。")
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
    target = session["binding"].get("target")
    api.platform_drafts._comparison_policy(session["binding"].get("comparison_policy", "strict"),
        target.get("platform") if isinstance(target, dict) else None)
    api.platform_drafts._body_encoding(session["binding"].get("body_encoding", "verbatim"),
        target.get("platform") if isinstance(target, dict) else None)
    return session


@contextmanager
def _authenticated(book, handle_path, *, extend=True):
    handle = _external(handle_path)
    if (not isinstance(handle, dict) or handle.get("book_id") != book.meta("id") or
            handle.get("book_root") != str(book.root) or not isinstance(handle.get("token"), str)):
        _fail("invalid_handle", "Chrome 桥接句柄不属于当前作品。")
    with _directory(book.root, handle.get("session_id")) as directory:
        # Reject imported evidence before initializing an empty/missing lock or
        # parsing session metadata that may never have survived the crash.
        if _restored_evidence(directory):
            _fail("worker_unavailable", "备份中的 Chrome 会话仅作为恢复证据保留；请重新连接执行端。")
        with _lock(directory):
            session = _session(directory)
            actual = hashlib.sha256(handle["token"].encode()).hexdigest()
            if (not hmac.compare_digest(actual, session.get("token_sha256", "")) or
                    handle.get("binding_sha256") != session.get("binding_sha256")):
                _fail("invalid_handle", "Chrome 桥接句柄认证失败。")
            _lease(directory, session, extend=extend)
            yield directory, session


def attach(book, plan_id, profile_id, handle_path, ttl_seconds=TTL_SECONDS, *, comparison_policy="strict", body_encoding="verbatim"):
    api.integer(ttl_seconds, "ttl_seconds", 30)
    if ttl_seconds > 3600:
        _fail("invalid_handle", "执行端心跳期限超过限制。")
    binding, _ = _binding(book, plan_id, profile_id, comparison_policy=comparison_policy, body_encoding=body_encoding)
    with _attachment_lock(book.root):
        return _attach(book, binding, handle_path, ttl_seconds)


def _attach(book, binding, handle_path, ttl_seconds):
    available = _worker_status(book.root, binding)
    if available["attached"]:
        _fail("worker_busy", "同一目标已有有效执行端，请复用原句柄或先分离。")
    if available["active_sessions"] >= MAX_SESSIONS:
        _fail("session_limit", "本书连接中的 Chrome 桥接会话过多；请先分离不用的执行端。")
    session_id, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
    session = {"schema": 1, "session_id": session_id, "binding": binding, "binding_sha256": _digest(binding),
               "token_sha256": hashlib.sha256(token.encode()).hexdigest(), "created_at": _stamp(), "ttl_seconds": ttl_seconds}
    with _directory(book.root, create=True):
        pass
    with _directory(book.root, session_id, create=True) as directory, _lock(directory):
        try:
            _write(directory, "session.json", session)
            _write(directory, "lease.json", {"schema": 1, "session_id": session_id,
                   "expires": time.time() + ttl_seconds, "detached": False})
        except BaseException:
            # A local fsync/verification error can happen after lease publication.
            # No handle has escaped this initialization, so retire only our own
            # published lease; do not leave it blocking the next attachment.
            if api.publish._bound_stat(api._BoundFile(directory, "lease.json"), missing=True) is not None:
                if _session(directory) != session:
                    _fail("invalid_message", "Chrome 初始化元数据发生变化；保留现场待核对。")
                lease = _read(directory, "lease.json", maximum=16384)
                if not isinstance(lease, dict) or lease.get("session_id") != session_id:
                    _fail("invalid_message", "Chrome 初始化租约发生变化；保留现场待核对。")
                _write(directory, "lease.json", {"schema": 1, "session_id": session_id,
                       "expires": time.time(), "detached": True}, replace=True)
            raise
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
            "ttl_seconds": ttl_seconds, "state": "awaiting_health", "ready": False,
            "remote_accessed": False, "browser_started": False}


def _health_record(directory, session):
    if api.publish._bound_stat(api._BoundFile(directory, "health.json"), missing=True) is None:
        return None
    record = _read(directory, "health.json", maximum=16384)
    if (not isinstance(record, dict) or record.get("schema") != 1 or
            record.get("session_id") != session["session_id"] or
            record.get("binding_sha256") != session["binding_sha256"] or
            not isinstance(record.get("browser"), dict) or record["browser"].get("kind") != "chrome" or
            not isinstance(record["browser"].get("id"), str) or not 1 <= len(record["browser"]["id"]) <= 200 or
            not isinstance(record.get("worker"), dict) or type(record["worker"].get("pid")) is not int or
            record["worker"]["pid"] <= 0 or not isinstance(record["worker"].get("instance"), str) or
            not re.fullmatch(r"[0-9a-f]{32}", record["worker"]["instance"]) or
            not isinstance(record.get("capabilities"), dict) or set(record["capabilities"]) != {"list_tabs", "get_tab", "dom_snapshot"} or
            any(value is not True for value in record["capabilities"].values()) or
            any(type(record.get(key)) not in (int, float) or not math.isfinite(record[key])
                for key in ("checked_epoch", "expires_epoch")) or
            record["expires_epoch"] - record["checked_epoch"] != HEALTH_SECONDS):
        _fail("invalid_health", "Chrome 执行端健康回执无效；未启动新操作。")
    return record


def _health_status(root, directory, session):
    record = _health_record(directory, session)
    if record is None:
        return {"ready": False, "state": "awaiting_health", "health_state": "awaiting_health"}
    details = {"browser_id": record["browser"]["id"], "worker_pid": record["worker"]["pid"],
               "worker_instance": record["worker"]["instance"], "health_checked_at": _stamp(record["checked_epoch"]),
               "health_expires_at": _stamp(record["expires_epoch"])}
    if record["expires_epoch"] <= time.time() or record["checked_epoch"] > time.time():
        return {**details, "ready": False, "state": "health_expired", "health_state": "expired"}
    if not _owner_alive(root, {"owner_pid": record["worker"]["pid"]}):
        return {**details, "ready": False, "state": "worker_unavailable", "health_state": "worker_exited"}
    return {**details, "ready": True, "state": "connected", "health_state": "healthy"}


def _require_health(root, directory, session):
    status = _health_status(root, directory, session)
    if not status["ready"]:
        _fail("worker_unavailable", "Chrome 执行端尚未通过近期公开浏览器探测，或执行进程已退出；未启动新操作。")
    return status


def health(book, handle_path, input_path=None):
    """Challenge/ack for an actual public Chrome list/get probe.

    A lease or controller process alone is never browser evidence. The trusted
    runner obtains this nonce before calling the public SDK list/get and reading
    a DOM snapshot of the existing target page, then submits the observed browser
    and its own live supervisor identity. No browser is opened
    here, and a health acknowledgement does not verify platform account/book.
    """
    response = _external(input_path) if input_path is not None else None
    with _authenticated(book, handle_path) as (directory, session):
        previous = _health_record(directory, session)
        if input_path is None:
            deadline = time.time() + HEALTH_CHALLENGE_SECONDS
            challenge = {"schema": 1, "session_id": session["session_id"],
                         "binding_sha256": session["binding_sha256"], "nonce": secrets.token_urlsafe(32),
                         "expires_at": _stamp(deadline), "deadline_epoch": deadline,
                         "browser_id": previous["browser"]["id"] if previous else None,
                         "worker_instance": previous["worker"]["instance"] if previous else None}
            _write(directory, "health-challenge.json", {**challenge, "consumed": False}, replace=True)
            return {"ok": True, "health_required": True, "challenge": challenge,
                    "remote_accessed": False, "browser_started": False}
        challenge = _read(directory, "health-challenge.json", maximum=16384)
        if (not isinstance(challenge, dict) or challenge.get("session_id") != session["session_id"] or
                challenge.get("binding_sha256") != session["binding_sha256"] or challenge.get("consumed") is not False or
                type(challenge.get("deadline_epoch")) not in (int, float) or
                not math.isfinite(challenge["deadline_epoch"]) or challenge["deadline_epoch"] <= time.time()):
            _fail("health_expired", "Chrome 连接探测已失效；请重新进行真实浏览器探测。")
        required = {"schema", "session_id", "binding_sha256", "nonce", "browser", "capabilities", "worker"}
        if (not isinstance(response, dict) or set(response) != required or type(response.get("schema")) is not int or
                response["schema"] != 1 or any(response.get(key) != challenge.get(key)
                    for key in ("session_id", "binding_sha256", "nonce")) or
                not isinstance(response.get("browser"), dict) or set(response["browser"]) != {"kind", "id"} or
                response["browser"].get("kind") != "chrome" or not isinstance(response["browser"].get("id"), str) or
                not 1 <= len(response["browser"]["id"]) <= 200 or
                any(ord(char) < 32 or ord(char) == 127 for char in response["browser"]["id"]) or
                not isinstance(response.get("capabilities"), dict) or set(response["capabilities"]) != {"list_tabs", "get_tab", "dom_snapshot"} or
                any(value is not True for value in response["capabilities"].values()) or
                not isinstance(response.get("worker"), dict) or set(response["worker"]) != {"pid", "instance"} or
                type(response["worker"].get("pid")) is not int or response["worker"]["pid"] <= 0 or
                not isinstance(response["worker"].get("instance"), str) or
                not re.fullmatch(r"[0-9a-f]{32}", response["worker"]["instance"])):
            _fail("invalid_health", "Chrome 连接探测回执与本次挑战不符；未采用。")
        if previous and (response["browser"] != previous["browser"] or response["worker"] != previous["worker"]):
            _fail("worker_changed", "Chrome 或执行进程身份发生变化；请结束旧会话后重新连接。")
        if not _owner_alive(book.root, {"owner_pid": response["worker"]["pid"]}):
            _fail("worker_unavailable", "Chrome 探测回执的执行进程已退出；未建立连接。")
        # Consume first: a crash before publishing health remains disconnected.
        _write(directory, "health-challenge.json", {**challenge, "consumed": True}, replace=True)
        now = time.time()
        _write(directory, "health.json", {**response, "checked_epoch": now,
                                         "expires_epoch": now + HEALTH_SECONDS}, replace=True)
        return {"ok": True, "session_id": session["session_id"], **_health_status(book.root, directory, session),
                "remote_accessed": False, "browser_started": False, "platform_target_verified": False}


def worker_status(root, binding):
    with _attachment_lock(root):
        return _worker_status(root, binding)


def _worker_status(root, binding):
    path = api.safe_path(Path(root), DIRECTORY)
    if not path.exists():
        return {"attached": False, "ready": False, "state": "worker_unavailable", "active_sessions": 0}
    with _directory(root) as parent:
        names = os.listdir(parent.path if os.name == "nt" else parent.fd)
    names = [name for name in names if re.fullmatch(r"[0-9a-f]{32}", name)]
    # Retain expired/detached queues as recovery evidence without making their
    # lifetime count prevent the next attachment. The limit applies to live
    # workers; checking the lease must therefore precede counting or binding.
    candidates, active_sessions = [], 0
    for name in names:
        with _directory(root, name) as directory:
            # Imported queues may retain a crash before session.json existed.
            # They are opaque evidence, so do not parse or lock their payloads.
            if _restored_evidence(directory):
                continue
            with _lock(directory):
                if _uninitialized_session(directory):
                    continue
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
                candidates.append({"attached": True, "session_id": name, "expires_at": _stamp(lease["expires"]),
                                   **_health_status(root, directory, session)})
    if len(candidates) > 1:
        _fail("worker_busy", "目标存在多个执行端；请先分离多余会话。")
    status = candidates[0] if candidates else {"attached": False, "ready": False, "state": "worker_unavailable"}
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


def _macos_process_alive(pid):
    """Query a specific PID without requiring sandbox signal permission."""
    if type(pid) is not int or not 0 < pid <= 0x7fffffff:
        return False
    try:
        group = os.getpgid(pid)
        session = os.getsid(pid)
        return type(group) is int and group > 0 and type(session) is int and session > 0
    except Exception:
        return False


def _owner_alive(root, request):
    pid = request.get("owner_pid")
    if type(pid) is not int or pid <= 0:
        return False
    try:
        if os.name == "nt":
            if not _windows_process_alive(pid):
                return False
        elif sys.platform == "darwin":
            if not _macos_process_alive(pid):
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
        _require_health(book.root, directory, session)
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
        api.platform_drafts._comparison_policy(binding.get("comparison_policy", "strict"), binding.get("target", {}).get("platform"))
        api.platform_drafts._body_encoding(binding.get("body_encoding", "verbatim"), binding.get("target", {}).get("platform"))
        self.root, self.binding, self.profile = Path(root), binding, deepcopy(profile)
        self.progress = progress_callback
        self.session_id = None

    def __enter__(self):
        status = worker_status(self.root, self.binding)
        if not status["ready"]:
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
            _require_health(self.root, directory, session)
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
            parser.add_argument("--comparison-policy", choices=("strict", "fanqie_terminal_lf_v1"), default="strict")
            parser.add_argument("--body-encoding", choices=("verbatim", "markdown_paragraphs_v1", "markdown_paragraphs_v2"), default="verbatim")
        elif name.endswith("respond"):
            parser.add_argument("--request-id", required=True)
            parser.add_argument("--input", required=True, help="Private 0600 response JSON file")
        elif name.endswith("health"):
            parser.add_argument("--input", help="Omit to issue a nonce; provide private 0600 actual Chrome probe acknowledgement")


def run(book, args):
    action = args.command.removeprefix("publish-chrome-")
    if action == "attach":
        return attach(book, args.id, args.profile, args.handle, args.ttl_seconds,
                      comparison_policy=args.comparison_policy, body_encoding=args.body_encoding)
    if action == "next":
        return next_request(book, args.handle)
    if action == "respond":
        return respond(book, args.handle, args.request_id, args.input)
    if action == "health":
        return health(book, args.handle, args.input)
    return heartbeat(book, args.handle, detach=action == "detach")
