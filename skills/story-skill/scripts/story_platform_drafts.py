"""Explicit, resumable saves to existing platform chapter drafts.

The immutable offline publishing plan remains the content source. This module
records only hashes and observations in an independent append-only journal;
it neither submits chapters nor writes creative events. Browser access is
deliberately injected so the state machine can be tested without an account.
"""
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import difflib
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import shlex
import stat
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit


COMMANDS = {"publish-draft-run", "publish-draft-status", "publish-draft-assist-run"}
DIRECTORY = ".story/platform-drafts"
MAX_EVENTS = 10000
MAX_EVENT_BYTES = 16384
SUCCESS = {"saved", "reconciled"}
EQUIVALENT = {"saved_equivalent", "reconciled_equivalent"}
ACCEPTED = SUCCESS | EQUIVALENT
EVENTS = {"sending", "unknown", "blocked", "conflict", "not_found"} | ACCEPTED
COMPARISON_POLICIES = {"strict", "fanqie_terminal_lf_v1"}
PLATFORM_BODY_ENCODING = "plain_paragraphs_v1"
BODY_ENCODINGS = {"verbatim", "markdown_paragraphs_v1", "markdown_paragraphs_v2", PLATFORM_BODY_ENCODING}
EQUIVALENCE_FIELDS = {"comparison_policy", "canonical_sha256", "observed_canonical_sha256",
                      "expected_terminal_lf", "observed_terminal_lf"}
ENCODING_FIELDS = {"body_encoding", "source_content_sha256", "encoded_content_sha256", "rendered_body_matches"}
ATTEMPT_FIELDS = {"comparison_policy", "body_encoding", "source_content_sha256", "encoded_content_sha256"}
SCOPE_FIELDS = {"book_id", "plan_id", "manifest_sha256"}
_EVENT_NAME = re.compile(r"event-([0-9]{8})-([0-9a-f]{32})\.json")
PROFILE_DIRECTORY = ".story/platform-profiles"
MAX_PROFILE_BYTES = 1024 * 1024
MAX_PROFILES = 100
MAX_WORKBENCH_JOBS = 100
RETIREMENT_PROOFS = frozenset({"authorization", "backup", "deletion_catalog", "current_catalog",
                               "absence_request", "absence_response", "closed_job"})
RETIREMENT_EXTRA_PROOFS = frozenset({"prior_attempt", "manual_cleanup"})
_WORKBENCH_LOCK = threading.RLock()
_WORKBENCH_JOBS = {}
_WORKBENCH_BINDINGS = {}
_PREVIEW_SECRET = secrets.token_bytes(32)
ASSISTANT_DIRECTORY = ".story/platform-assistant-tasks"
ASSISTANT_SECONDS = 600
_ASSISTANT_MODES = {"inspect_drafts", "reconcile_only", "sync_drafts"}
_ASSISTANT_STATES = {"prepared", "running", "inspection_ready", "inspection_blocked", "succeeded",
                     "unknown", "failed", "cancelled", "stopped", "interrupted", "expired", "partial_failure"}
_ASSISTANT_STAGES = {"awaiting_assistant", "connecting_assistant", "waiting_assistant_bridge", "running_assistant",
                     "finished", "cleanup_pending", "waiting_login", "waiting_host", "target_verified", "listing_drafts", "saving_draft",
                     "reading_back", "chapter_started", "chapter_verified", "inspection_started", "inspection_checked"}


def inject(core):
    global api
    api = core


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(value):
    return hashlib.sha256(api.dumps(value).encode("utf-8")).hexdigest()


def _normal(value):
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _content(item):
    return {name: _normal(item[name]) for name in ("title", "body")}


def _comparison_policy(value, platform):
    if not isinstance(value, str) or value not in COMPARISON_POLICIES:
        api.fail("platform_draft_invalid_comparison_policy", "Unsupported platform draft comparison policy")
    if value != "strict" and platform != "fanqie":
        api.fail("platform_draft_invalid_comparison_policy", "Terminal LF equivalence is available only for Fanqie")
    return value


def _canonical(item):
    content = _content(item)
    return {**content, "body": content["body"].rstrip("\n")}


def _body_encoding(value, platform):
    if (not isinstance(value, str) or value not in BODY_ENCODINGS
            or (value not in {"verbatim", PLATFORM_BODY_ENCODING} and platform != "fanqie")):
        api.fail("platform_draft_invalid_body_encoding", "Unsupported platform draft body encoding")
    return value


def require_platform_body_encoding(value):
    """Public write entrypoints use one format; historical evidence keeps its own."""
    if value != PLATFORM_BODY_ENCODING:
        api.fail("platform_draft_body_encoding_required",
                 "平台正文统一去掉空白行、保留段落换行。请刷新工作台并重新预览，无需修改原稿。")
    return value


def _encoded_content(item, encoding, platform):
    _body_encoding(encoding, platform)
    content = _content(item)
    if encoding == "verbatim":
        return {name: item[name] for name in ("title", "body")}
    body = content["body"]
    chapter = f"第{item['chapter']}章" if type(item.get("chapter")) is int else ""
    if encoding == PLATFORM_BODY_ENCODING:
        # This is plain text, not Markdown rendering. Remove only whole blank
        # lines; preserve every character (including indentation) on other lines.
        # Versioned separately so old attempts and receipts retain their hashes.
        lines = [line for line in body.split("\n") if line.strip()]
        if not lines:
            api.fail("platform_draft_empty_body", chapter + "正文去掉空白行后没有文字，请核对正式稿。")
        return {**content, "body": "\n".join(lines)}
    leading, trailing = len(body) - len(body.lstrip("\n")), len(body) - len(body.rstrip("\n"))
    inner = body[leading:len(body) - trailing if trailing else len(body)]
    paragraphs = inner.split("\n\n")
    unsupported = re.compile(r"^ {0,3}(?:#{1,6}[ \t]|>|`{3}|~{3}|[-*+][ \t]|[0-9]+[.)][ \t]|\||<[A-Za-z/!])")
    if (leading > 1 or trailing > 1 or not inner or any("\n" in paragraph or not paragraph
            or unsupported.search(paragraph) for paragraph in paragraphs)):
        api.fail("platform_draft_unsupported_body_encoding",
                 chapter + "正文换行或标记不符合旧版段落转换规则。请刷新工作台，使用去空行排版；无需为此修改原稿。")
    rendered_leading = 0 if encoding == "markdown_paragraphs_v2" else leading
    return {**content, "body": "\n" * rendered_leading + "\n".join(paragraphs) + "\n" * trailing}


def _comparison_canonical(item, policy):
    return _canonical(item) if policy == "fanqie_terminal_lf_v1" else _content(item)


def _terminal_lf(item):
    body = _normal(item["body"])
    return len(body) - len(body.rstrip("\n"))


def _comparison(frozen, observed, policy, platform):
    _comparison_policy(policy, platform)
    exact = _content(frozen) == _content(observed)
    equivalent = not exact and policy == "fanqie_terminal_lf_v1" and _canonical(frozen) == _canonical(observed)
    return exact, equivalent


def _equivalence_evidence(frozen, encoded, observed, policy, encoding):
    return {"comparison_policy": policy, "canonical_sha256": _hash(_comparison_canonical(encoded, policy)),
            "observed_canonical_sha256": _hash(_comparison_canonical(observed, policy)),
            "expected_terminal_lf": _terminal_lf(encoded), "observed_terminal_lf": _terminal_lf(observed),
            "body_encoding": encoding, "source_content_sha256": _hash(_content(frozen)),
            "encoded_content_sha256": _hash(_content(encoded)),
            "rendered_body_matches": _normal(observed["body"]) == _normal(encoded["body"])}


def _attempt_evidence(frozen, policy, encoding, platform):
    _comparison_policy(policy, platform)
    encoded = _encoded_content(frozen, encoding, platform)
    return {"comparison_policy": policy, "body_encoding": encoding,
            "source_content_sha256": _hash(_content(frozen)), "encoded_content_sha256": _hash(_content(encoded))}


def _valid_attempt(record, platform, frozen=None):
    try:
        _comparison_policy(record["comparison_policy"], platform)
        _body_encoding(record["body_encoding"], platform)
        return (record["source_content_sha256"] == record["content_sha256"]
                and _retirement_hex(record["encoded_content_sha256"])
                and (record["body_encoding"] != "verbatim" or record["encoded_content_sha256"] == record["content_sha256"])
                and (frozen is None or _attempt_evidence(frozen, record["comparison_policy"], record["body_encoding"], platform)
                     == {name: record[name] for name in ATTEMPT_FIELDS}))
    except api.StoryError:
        return False


def _valid_scope(scope):
    return (isinstance(scope, dict) and set(scope) == SCOPE_FIELDS
            and isinstance(scope["book_id"], str) and 0 < len(scope["book_id"]) <= 200
            and _retirement_hex(scope["plan_id"], 32) and _retirement_hex(scope["manifest_sha256"]))


def _record_attempt_metadata(record):
    return {"comparison_policy": record.get("comparison_policy", "strict"),
            "body_encoding": record.get("body_encoding", "verbatim"),
            "source_content_sha256": record.get("source_content_sha256", record["content_sha256"]),
            "encoded_content_sha256": record.get("encoded_content_sha256", record["content_sha256"])}


def _valid_equivalence(record, frozen=None):
    """Validate an explicit equivalence assertion without rewriting either hash."""
    extended = "body_encoding" in record
    policy = record["comparison_policy"]
    encoding = record.get("body_encoding", "verbatim")
    if (not isinstance(policy, str) or policy not in COMPARISON_POLICIES
            or not isinstance(encoding, str) or encoding not in BODY_ENCODINGS):
        return False
    try:
        encoded = _encoded_content(frozen, encoding, "fanqie") if frozen is not None else None
    except api.StoryError:
        return False
    if extended and (record["source_content_sha256"] != record["content_sha256"]
            or not _retirement_hex(record["encoded_content_sha256"]) or type(record["rendered_body_matches"]) is not bool
            or record["rendered_body_matches"] != (record["observed_sha256"] == record["encoded_content_sha256"])
            or (encoding == "verbatim" and record["encoded_content_sha256"] != record["content_sha256"])
            or (policy == "strict" and (record["canonical_sha256"] != record["encoded_content_sha256"]
                                        or record["observed_canonical_sha256"] != record["observed_sha256"]))
            or (encoded is not None and record["encoded_content_sha256"] != _hash(_content(encoded)))):
        return False
    return ((policy == "fanqie_terminal_lf_v1" or (extended and encoding != "verbatim"))
            and _retirement_hex(record["canonical_sha256"])
            and record["canonical_sha256"] == record["observed_canonical_sha256"]
            and record["observed_sha256"] != record["content_sha256"]
            and record["title_matches"] is True and record["body_matches"] is False
            and all(type(record[name]) is int and record[name] >= 0
                    for name in ("expected_terminal_lf", "observed_terminal_lf"))
            and (policy != "strict" or (record["expected_terminal_lf"] == record["observed_terminal_lf"]
                                       and record.get("rendered_body_matches") is True))
            and (encoding != "verbatim" or record["expected_terminal_lf"] != record["observed_terminal_lf"])
            and (not extended or record["encoded_content_sha256"] != record["content_sha256"]
                 or record["expected_terminal_lf"] != record["observed_terminal_lf"])
            and (encoded is None or (record["canonical_sha256"] == _hash(_comparison_canonical(encoded, policy))
                                    and record["expected_terminal_lf"] == _terminal_lf(encoded))))


def _target(manifest):
    return {name: manifest[name] for name in ("platform", "account_id", "remote_book_id")}


def _identifier(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9A-Za-z_-]{1,200}", value):
        api.fail("platform_draft_invalid_observation", name + " must be a stable identifier, not a URL")
    return value


def _error_code(error):
    # Exception text, URLs and browser details may contain novel text or secrets.
    code = getattr(error, "code", None)
    return code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,99}", code) else "platform_draft_adapter_error"


def _atomic_publish(source, target):
    """A single no-replace rename avoids a crash leaving two hard-link aliases."""
    if os.name == "nt":
        os.rename(source.path, target.path)
        return
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin" and hasattr(libc, "renameatx_np"):
        rename = libc.renameatx_np
        flags = 0x00000004  # RENAME_EXCL, SDK sys/stdio.h
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        rename = libc.renameat2
        flags = 1  # RENAME_NOREPLACE
    else:
        api.fail("platform_draft_atomic_publish_unavailable", "Atomic no-replace publication is unavailable on this host")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(source.directory.fd, os.fsencode(source.name), target.directory.fd,
              os.fsencode(target.name), flags) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _write_record(directory, name, record, *, max_bytes=MAX_EVENT_BYTES):
    """Durably publish a new regular JSON file without replacing any history."""
    data = api.dumps(record).encode("utf-8")
    if len(data) > max_bytes:
        api.fail("platform_draft_journal_limit", "Platform draft event exceeds its size limit")
    file = api._BoundFile(directory, name)
    fd, staged = api._bound_stage(directory, ".draft-stage-")
    owned = os.fstat(fd)
    try:
        with api._owned_fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if not os.path.samestat(owned, api.publish._bound_stat(staged)):
            api.fail("unsafe_platform_draft_path", "Platform draft event staging file changed")
        api._verify_bound_directory(directory)
        _atomic_publish(staged, file)
        if os.name != "nt":
            os.fsync(directory.fd)
        api._verify_bound_directory(directory)
        if (not os.path.samestat(owned, api.publish._bound_stat(file)) or
                api._bound_hash(file) != _hash_bytes(data)):
            api.fail("unsafe_platform_draft_path", "Platform draft event changed during publication")
    finally:
        try:
            remaining = api.publish._bound_stat(staged, missing=True)
        except api.StoryError:
            remaining = None
        if remaining is not None and os.path.samestat(owned, remaining):
            api._bound_unlink(staged)


def _read_record(directory, name, *, max_bytes=MAX_EVENT_BYTES):
    file = api._BoundFile(directory, name)
    before = api.publish._bound_stat(file)
    if before.st_size > max_bytes:
        api.fail("platform_draft_journal_invalid", "Platform draft event exceeds its size limit")
    with api._bound_reader(file) as stream:
        opened = os.fstat(stream.fileno())
        if not os.path.samestat(before, opened) or opened.st_nlink != 1:
            api.fail("unsafe_platform_draft_path", "Platform draft event changed while opening")
        data = stream.read(max_bytes + 1)
        after_read = os.fstat(stream.fileno())
    after = api.publish._bound_stat(file)
    if (not os.path.samestat(before, after) or before.st_size != after.st_size or
            before.st_mtime_ns != after.st_mtime_ns or opened.st_mtime_ns != after_read.st_mtime_ns):
        api.fail("unsafe_platform_draft_path", "Platform draft event changed while reading")
    try:
        record = json.loads(data.decode("utf-8"))
        if api.dumps(record).encode("utf-8") != data:
            raise ValueError("Noncanonical event")
        return record
    except (UnicodeError, ValueError, TypeError):
        api.fail("platform_draft_journal_invalid", "Platform draft journal event is invalid")


def _retirement_require(condition):
    if not condition:
        api.fail("platform_draft_retirement_invalid", "Deleted-draft retirement evidence is incomplete or changed")


def _retirement_hex(value, length=64):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", value) is not None


def _retirement_time(value):
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.tzinfo is not None and stamp.utcoffset().total_seconds() == 0
    except (TypeError, ValueError):
        return False


def _validate_retirement(record, target_hash, chapter, attempt, intents, resolved):
    fields = {"schema", "kind", "target_sha256", "chapter", "attempt_id", "content_sha256", "at",
              "intent_sha256", "resolved_sha256", "remote_id", "book_id", "plan_id", "manifest_sha256",
              "proofs", "acknowledgements", "backup_content_sha256", "content_difference_acknowledged", "derived_unknowns"}
    _retirement_require(isinstance(record, dict) and set(record) == fields and type(record["schema"]) is int and record["schema"] == 1
        and record["kind"] == "user_authorized_retirement" and record["target_sha256"] == target_hash
        and type(record["chapter"]) is int and record["chapter"] == chapter and record["attempt_id"] == attempt and attempt in intents)
    original = intents[attempt]
    _retirement_require(record["content_sha256"] == original["content_sha256"]
        and record["intent_sha256"] == _hash(original) and _retirement_time(record["at"])
        and record["at"] >= original["at"] and _retirement_hex(record["manifest_sha256"])
        and _retirement_hex(record["plan_id"], 32) and isinstance(record["book_id"], str)
        and 0 < len(record["book_id"]) <= 200 and isinstance(record["remote_id"], str)
        and re.fullmatch(r"[0-9A-Za-z_-]{1,200}", record["remote_id"]) is not None)
    prior = resolved.get(attempt)
    _retirement_require(record["resolved_sha256"] == (_hash(prior) if prior else None)
        and (prior is None or record["remote_id"] == prior["remote_id"]))
    proofs = record["proofs"]
    _retirement_require(isinstance(proofs, dict) and RETIREMENT_PROOFS <= set(proofs)
        and set(proofs) <= RETIREMENT_PROOFS | RETIREMENT_EXTRA_PROOFS
        and all(_retirement_hex(value) for value in proofs.values())
        and record["acknowledgements"] == {"deletion_confirmed": True, "complete_catalog_empty": True,
                                          "all_related_workers_stopped": True}
        and all(type(value) is bool for value in record["acknowledgements"].values())
        and _retirement_hex(record["backup_content_sha256"])
        and type(record["content_difference_acknowledged"]) is bool)
    if record["backup_content_sha256"] != record["content_sha256"]:
        _retirement_require(record["content_difference_acknowledged"] is True
                            and RETIREMENT_EXTRA_PROOFS <= set(proofs))
    events = record["derived_unknowns"]
    _retirement_require(isinstance(events, list) and len(events) <= 100)
    seen = set()
    for event in events:
        _retirement_require(isinstance(event, dict) and set(event) == {"sequence", "attempt_id", "event_sha256"}
            and type(event["sequence"]) is int and event["sequence"] > 0
            and _retirement_hex(event["attempt_id"], 32) and _retirement_hex(event["event_sha256"])
            and event["event_sha256"] not in seen)
        seen.add(event["event_sha256"])


def _retirement_proof(proof):
    _retirement_require(isinstance(proof, dict) and set(proof) == {"path", "sha256"}
                        and _retirement_hex(proof["sha256"]) and isinstance(proof["path"], str))
    path = Path(proof["path"])
    try:
        _retirement_require(path.is_absolute() and path.resolve(strict=True) == path)
        with api._pinned_directory(path.parent) as directory:
            file = api._BoundFile(directory, path.name)
            before = api.publish._bound_stat(file)
            _retirement_require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= 16 * 1024 * 1024)
            with api._bound_reader(file) as stream:
                opened = os.fstat(stream.fileno()); data = stream.read(16 * 1024 * 1024 + 1)
                after_read = os.fstat(stream.fileno())
            after = api.publish._bound_stat(file)
            _retirement_require(os.path.samestat(before, opened) and os.path.samestat(before, after)
                and before.st_size == after.st_size == len(data) and before.st_mtime_ns == after.st_mtime_ns
                and opened.st_mtime_ns == after_read.st_mtime_ns and _hash_bytes(data) == proof["sha256"])
            api._verify_bound_directory(directory)
        return data
    except (OSError, ValueError):
        _retirement_require(False)


def _retirement_no_active_create(book, target):
    """A target lock does not cancel a previously claimed browser operation."""
    bridge = api.platform_chrome_bridge
    path = api.safe_path(book.root, bridge.DIRECTORY)
    with bridge._attachment_lock(book.root):
        if not path.exists():
            return
        with api._pinned_directory(path) as parent:
            names = [name for name in os.listdir(parent.path if os.name == "nt" else parent.fd)
                     if re.fullmatch(r"[0-9a-f]{32}", name)]
            # MAX_SESSIONS bounds live workers, not retained queue history.
            # Match attachment discovery only for never-ready initialization;
            # unknown requests and restored queues still require full checks.
            for name in names:
                with bridge._directory(book.root, name) as directory, bridge._lock(directory):
                    if bridge._uninitialized_session(directory):
                        continue
                    session = bridge._session(directory)
                    if session["binding"].get("target") != target:
                        continue
                    try:
                        bridge._lease(directory, session)
                    except api.StoryError as error:
                        if error.code != "platform_chrome_worker_unavailable":
                            raise
                    else:
                        _retirement_require(False)
                    for filename in os.listdir(directory.path if os.name == "nt" else directory.fd):
                        match = re.fullmatch(r"claimed-([0-9a-f]{32})\.json", filename)
                        if not match:
                            continue
                        request = bridge._read(directory, filename)
                        if request.get("operation") not in {"create_draft", "update_draft", "resume_empty_draft"}:
                            continue
                        response = api.publish._bound_stat(api._BoundFile(directory, "response-" + match[1] + ".json"), missing=True)
                        if response is None:
                            health = bridge._health_record(directory, session)
                            _retirement_require(health is not None and
                                not bridge._owner_alive(book.root, {"owner_pid": health["worker"]["pid"]}))
            api._verify_bound_directory(parent)


def retire_deleted_attempts(book, packet, *, execute=False, acknowledgement=None):
    """Record operator-reviewed deletion evidence, never platform success.

    This local, explicit recovery API performs no browser action. The operator
    must verify the user authorization, deletion/empty-catalog screenshots and
    that every related worker has stopped. Hashes bind that review to exact
    artifacts; only matching old attempts and derived non-write events retire.
    """
    _retirement_require(type(execute) is bool and (not execute or acknowledgement == "user_authorized_deleted_drafts_only"))
    fields = {"schema", "kind", "book_id", "plan_id", "manifest_sha256", "target", "acknowledgements", "proofs", "attempts"}
    _retirement_require(isinstance(packet, dict) and set(packet) == fields and type(packet["schema"]) is int and packet["schema"] == 1
        and packet["kind"] == "user_authorized_deleted_drafts_retirement" and packet["book_id"] == book.meta("id")
        and isinstance(packet["proofs"], dict) and RETIREMENT_PROOFS <= set(packet["proofs"])
        and set(packet["proofs"]) <= RETIREMENT_PROOFS | RETIREMENT_EXTRA_PROOFS
        and isinstance(packet["attempts"], list) and 0 < len(packet["attempts"]) <= 100)
    raw = {name: _retirement_proof(proof) for name, proof in packet["proofs"].items()}
    try:
        proofs = {name: json.loads(raw[name].decode("utf-8")) for name in
                  ("authorization", "backup", "absence_request", "absence_response", "closed_job")}
    except (UnicodeError, ValueError, RecursionError):
        _retirement_require(False)
    with _checked_plan(book, packet["plan_id"]) as (manifest, fingerprint, _):
        _retirement_require(packet["manifest_sha256"] == fingerprint and packet["target"] == _target(manifest))
        frozen_chapters = [item["chapter"] for item in manifest["chapters"]]
    authorization, request, response, job = (proofs[name] for name in
                                            ("authorization", "absence_request", "absence_response", "closed_job"))
    _retirement_require(isinstance(authorization, dict) and type(authorization.get("schema")) is int and authorization.get("schema") == 1
        and all(isinstance(authorization.get(key), str) and authorization[key].strip() for key in ("source", "verbatim"))
        and authorization.get("platform") == packet["target"]["platform"]
        and authorization.get("authorized_formal_chapters") == frozen_chapters
        and all(type(chapter) is int for chapter in authorization["authorized_formal_chapters"])
        and isinstance(authorization.get("deleted_test_drafts"), list))
    _retirement_require(isinstance(request, dict) and isinstance(response, dict) and isinstance(job, dict)
                        and type(request.get("schema")) is int and request.get("schema") == 1
                        and type(response.get("schema")) is int and response.get("schema") == 1)
    binding = request.get("binding", {})
    _retirement_require(isinstance(binding, dict) and binding.get("book_id") == packet["book_id"]
        and binding.get("plan_id") == packet["plan_id"] and binding.get("manifest_sha256") == fingerprint
        and binding.get("target") == packet["target"] and request.get("binding_sha256") == _hash(binding)
        and request.get("operation") == "find_drafts" and response.get("ok") is True and response.get("result") == []
        and all(response.get(key) == request.get(key) and request.get(key) is not None
                for key in ("id", "session_id", "binding_sha256", "nonce"))
        and job.get("done") is True and job.get("stage") == "finished"
        and job.get("book_id") == packet["book_id"] and job.get("plan_id") == packet["plan_id"]
        and all(job.get(key) == value for key, value in packet["target"].items()))
    with _target_lock(manifest) as state, _journal(book, packet["plan_id"], manifest, fingerprint) as journal:
        with _checked_plan(book, packet["plan_id"]) as (_, current_fingerprint, _):
            _retirement_require(current_fingerprint == fingerprint)
        _retirement_no_active_create(book, packet["target"])
        records, seen = [], set()
        for attempt in packet["attempts"]:
            expected = {"chapter", "attempt_id", "content_sha256", "intent_sha256", "resolved_sha256", "remote_id",
                        "backup_index", "backup_content_sha256", "content_difference_acknowledged", "derived_unknowns"}
            _retirement_require(isinstance(attempt, dict) and set(attempt) == expected
                and type(attempt["chapter"]) is int and attempt["chapter"] in frozen_chapters
                and _retirement_hex(attempt["attempt_id"], 32)
                and all(_retirement_hex(attempt[key]) for key in ("content_sha256", "intent_sha256", "backup_content_sha256"))
                and (attempt["resolved_sha256"] is None or _retirement_hex(attempt["resolved_sha256"]))
                and isinstance(attempt["remote_id"], str) and re.fullmatch(r"[0-9A-Za-z_-]{1,200}", attempt["remote_id"]) is not None
                and (attempt["chapter"], attempt["attempt_id"]) not in seen)
            chapter, attempt_id = attempt["chapter"], attempt["attempt_id"]
            seen.add((chapter, attempt_id)); state.attempts(chapter)
            backup = proofs["backup"]
            _retirement_require(isinstance(backup, list) and type(attempt["backup_index"]) is int
                                and 0 <= attempt["backup_index"] < len(backup))
            row = backup[attempt["backup_index"]]
            _retirement_require(isinstance(row, dict) and isinstance(row.get("title"), str))
            title = row["title"]; prefix = f"第{chapter}章 "
            if title.startswith(prefix):
                title = title[len(prefix):]
            body = row.get("body")
            if isinstance(body, dict):
                _retirement_require(set(body) == {"ok", "value"} and body["ok"] is True)
                body = body["value"]
            _retirement_require(isinstance(body, str) and _hash(_content({"title": title, "body": body})) == attempt["backup_content_sha256"])
            remote = row.get("remote_id")
            if remote is None and isinstance(row.get("url"), str):
                remote = attempt["remote_id"] if re.search(r"(?:^|[/&])" + re.escape(attempt["remote_id"]) + r"(?:[/&]|$)", urlsplit(row["url"]).path) else None
            _retirement_require(remote == attempt["remote_id"] and any(isinstance(item, dict)
                and item.get("remote_id") == remote and type(item.get("chapter")) is int and item.get("chapter") == chapter and item.get("title") == title
                for item in authorization["deleted_test_drafts"]))
            record = {"schema": 1, "kind": "user_authorized_retirement", "target_sha256": state.target_hash,
                **{key: attempt[key] for key in expected - {"backup_index"}}, "at": _now(),
                "book_id": packet["book_id"], "plan_id": packet["plan_id"], "manifest_sha256": fingerprint,
                "proofs": {name: proof["sha256"] for name, proof in packet["proofs"].items()},
                "acknowledgements": packet["acknowledgements"]}
            _validate_retirement(record, state.target_hash, chapter, attempt_id, state.all_intents, state.all_resolved)
            for event in record["derived_unknowns"]:
                matching = [value for value in journal.events if value["sequence"] == event["sequence"]]
                _retirement_require(len(matching) == 1)
                value = matching[0]
                _retirement_require(value["chapter"] == chapter and value["attempt_id"] == event["attempt_id"]
                    and _hash(value) == event["event_sha256"] and value["event"] == "unknown"
                    and value["reason"] == "previous_attempt_not_found" and value["remote_id"] is None
                    and not any(other["attempt_id"] == value["attempt_id"] and other["event"] == "sending" for other in journal.events))
            prior = state.retired.get(attempt_id)
            if prior is not None:
                _retirement_require({key: value for key, value in prior.items() if key != "at"} ==
                                    {key: value for key, value in record.items() if key != "at"})
            records.append((record, prior is not None))
        _retirement_require(isinstance(request.get("params"), dict) and type(request["params"].get("chapter")) is int
                            and request["params"] in [{"chapter": record["chapter"]} for record, _ in records])
        if execute:
            for record, exists in records:
                if not exists:
                    _write_record(state.directory, f"{state.target_hash}-{record['chapter']}-{record['attempt_id']}.retired.json", record)
        return {"ok": True, "executed": execute, "kind": "user_authorized_retirement", "attempts": len(records),
                "already_retired": sum(exists for _, exists in records), "remote_accessed": False,
                "platform_success_claimed": False, "submission_performed": False}


class _TargetState:
    """Keep unresolved writes visible across book copies and new plan IDs.

    Only hashed target/content identities and attempt IDs leave the book root.
    These small local anchors supplement remote reconciliation; they are not a
    distributed lock or proof of remote absence after loss of local storage.
    """

    def __init__(self, directory, manifest, guard):
        self.directory, self.guard = directory, guard
        self.target_hash = _hash(_target(manifest))
        self.platform = manifest["platform"]

    def __call__(self):
        self.guard()

    def attempts(self, chapter):
        self.guard()
        prefix = self.target_hash + "-" + str(chapter) + "-"
        names = os.listdir(self.directory.path if os.name == "nt" else self.directory.fd)
        names = [name for name in names if name.startswith(prefix)]
        if len(names) > MAX_EVENTS * 3:
            api.fail("platform_draft_journal_limit", "Too many target-level draft attempts")
        intents, resolved, retired = {}, {}, {}
        for name in names:
            match = re.fullmatch(re.escape(prefix) + r"([0-9a-f]{32})\.(intent|resolved|retired)\.json", name)
            if not match:
                api.fail("platform_draft_journal_invalid", "Invalid target-level draft record name")
            record = _read_record(self.directory, name)
            if match[2] == "retired":
                retired[match[1]] = record
                continue
            fields = {"schema", "target_sha256", "chapter", "attempt_id", "content_sha256", "at"}
            if match[2] == "resolved":
                fields.add("remote_id")
            extended = isinstance(record, dict) and record.get("schema") == 2
            equivalent = extended and match[2] == "resolved" and "canonical_sha256" in record
            if extended:
                fields |= ATTEMPT_FIELDS | SCOPE_FIELDS
                if match[2] == "resolved":
                    fields |= {"observed_sha256", "title_matches", "body_matches", "rendered_body_matches"}
            if equivalent:
                fields |= EQUIVALENCE_FIELDS | {"observed_sha256", "title_matches", "body_matches",
                                                "book_id", "plan_id", "manifest_sha256"}
                if "body_encoding" in record:
                    fields |= ENCODING_FIELDS
            if (not isinstance(record, dict) or set(record) != fields or
                    type(record["schema"]) is not int or record["schema"] != (2 if extended else 1) or
                    record["target_sha256"] != self.target_hash or type(record["chapter"]) is not int or
                    record["chapter"] != chapter or record["attempt_id"] != match[1] or
                    not isinstance(record["content_sha256"], str) or
                    not re.fullmatch(r"[0-9a-f]{64}", record["content_sha256"])):
                api.fail("platform_draft_journal_invalid", "Invalid target-level draft record")
            if extended and (not _valid_attempt(record, self.platform)
                    or not _valid_scope({name: record[name] for name in SCOPE_FIELDS})):
                api.fail("platform_draft_journal_invalid", "Invalid target-level attempt metadata")
            if equivalent and ((self.platform != "fanqie" and not
                    (record.get("body_encoding") == PLATFORM_BODY_ENCODING and record["comparison_policy"] == "strict"))
                    or not _valid_equivalence(record)
                    or not _retirement_hex(record["observed_sha256"])
                    or not _retirement_hex(record["manifest_sha256"]) or not _retirement_hex(record["plan_id"], 32)
                    or not isinstance(record["book_id"], str) or not 0 < len(record["book_id"]) <= 200):
                api.fail("platform_draft_journal_invalid", "Invalid target-level platform equivalence")
            if extended and match[2] == "resolved" and not equivalent and (
                    record["observed_sha256"] != record["content_sha256"] or record["title_matches"] is not True
                    or record["body_matches"] is not True or record["rendered_body_matches"] is not True
                    or record["encoded_content_sha256"] != record["content_sha256"]):
                api.fail("platform_draft_journal_invalid", "Invalid exact target-level resolution")
            try:
                stamp = datetime.fromisoformat(record["at"])
                if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
                    raise ValueError("Not UTC")
            except (ValueError, TypeError):
                api.fail("platform_draft_journal_invalid", "Invalid target-level draft timestamp")
            if match[2] == "resolved":
                _identifier(record["remote_id"], "remote_id")
                resolved[match[1]] = record
            else:
                intents[match[1]] = record
        if any(key not in intents or intents[key]["content_sha256"] != value["content_sha256"]
               for key, value in resolved.items()):
            api.fail("platform_draft_journal_invalid", "Resolved target attempt has no matching intent")
        for key, record in retired.items():
            _validate_retirement(record, self.target_hash, chapter, key, intents, resolved)
        self.all_intents, self.all_resolved, self.retired = intents, resolved, retired
        return ({key: value for key, value in intents.items() if key not in retired},
                {key: value for key, value in resolved.items() if key not in retired})

    def retired_events(self, chapter, book_id, plan_id):
        self.attempts(chapter)
        return {event["event_sha256"] for record in self.retired.values()
                if record["book_id"] == book_id and record["plan_id"] == plan_id
                for event in record["derived_unknowns"]}

    def intent(self, chapter, attempt_id, frozen, *, comparison_policy="strict", body_encoding="verbatim", scope=None):
        self.guard()
        record = {"schema": 1, "target_sha256": self.target_hash, "chapter": chapter,
                  "attempt_id": attempt_id, "content_sha256": _hash(_content(frozen)), "at": _now()}
        evidence = _attempt_evidence(frozen, comparison_policy, body_encoding, self.platform)
        if comparison_policy != "strict" or body_encoding != "verbatim":
            if not _valid_scope(scope):
                api.fail("platform_draft_journal_invalid", "Attempt metadata requires an authorized frozen batch")
            record.update(schema=2, **scope, **evidence)
        try:
            _write_record(self.directory, f"{self.target_hash}-{chapter}-{attempt_id}.intent.json", record)
        except Exception:
            api.fail("platform_draft_journal_write_failed", "Could not persist the target-level sending intent")

    def _resolution(self, chapter, observed, *, frozen=None, comparison_policy="strict", body_encoding="verbatim", scope=None):
        """Read compatible resolution evidence without changing any intent."""
        if observed.get("formatting_clean") is False:
            return None
        intents, resolved = self.attempts(chapter)
        frozen = observed if frozen is None else frozen
        encoded = _encoded_content(frozen, body_encoding, self.platform)
        rendered_exact, terminal_equivalent = _comparison(encoded, observed, comparison_policy, self.platform)
        if not rendered_exact and not terminal_equivalent:
            return None
        equivalent = _content(frozen) != _content(observed)
        content_hash = _hash(_content(frozen))
        # Do not settle a different, potentially still in-flight version merely
        # because this new plan happens to match the currently visible chapter.
        if any(item["content_sha256"] != content_hash for key, item in intents.items() if key not in resolved):
            return None
        if any(item["schema"] == 2 and {name: item[name] for name in SCOPE_FIELDS} != scope
               for key, item in intents.items() if key not in resolved):
            return None
        if any(item["schema"] == 2 and not _valid_attempt(item, self.platform, frozen)
               for key, item in intents.items() if key not in resolved):
            return None
        if (equivalent or comparison_policy != "strict" or body_encoding != "verbatim") and not _valid_scope(scope):
            api.fail("platform_draft_journal_invalid", "Platform equivalence requires an authorized frozen batch")
        return intents, resolved, frozen, encoded, rendered_exact, equivalent

    def can_resolve(self, chapter, observed, *, frozen=None, comparison_policy="strict", body_encoding="verbatim", scope=None):
        return self._resolution(chapter, observed, frozen=frozen, comparison_policy=comparison_policy,
                                body_encoding=body_encoding, scope=scope) is not None

    def resolve(self, chapter, observed, *, frozen=None, comparison_policy="strict", body_encoding="verbatim", scope=None):
        evidence = self._resolution(chapter, observed, frozen=frozen, comparison_policy=comparison_policy,
                                    body_encoding=body_encoding, scope=scope)
        if evidence is None:
            return False
        intents, resolved, frozen, encoded, rendered_exact, equivalent = evidence
        for key, intent in intents.items():
            if key in resolved:
                continue
            record = {**intent, "at": _now(), "remote_id": observed["remote_id"]}
            if intent["schema"] == 2 or comparison_policy != "strict" or body_encoding != "verbatim":
                if not _valid_scope(scope):
                    api.fail("platform_draft_journal_invalid", "Resolution metadata requires an authorized frozen batch")
                record.update(schema=2, **scope, **_attempt_evidence(frozen, comparison_policy, body_encoding, self.platform),
                              observed_sha256=_hash(_content(observed)), title_matches=True,
                              body_matches=not equivalent, rendered_body_matches=rendered_exact)
            if equivalent:
                record.update(schema=2, observed_sha256=_hash(_content(observed)), title_matches=True,
                              body_matches=False, **scope,
                              **_equivalence_evidence(frozen, encoded, observed, comparison_policy, body_encoding))
            try:
                _write_record(self.directory, f"{self.target_hash}-{chapter}-{key}.resolved.json", record)
            except Exception:
                api.fail("platform_draft_journal_write_failed", "Could not persist target-level reconciliation")
        return True


@contextmanager
def _checked_plan(book, plan_id, check_source=True):
    api.publish._book_kind(book)
    api.publish._identity(plan_id, "plan id")
    if not re.fullmatch(r"[0-9a-f]{32}", plan_id):
        api.fail("invalid_input", "Expected a publishing plan ID")
    with api.storage.operation_lock(book), book.read_snapshot(), api.publish._ledger(book) as db:
        row = api.publish._plan(db, plan_id)
        manifest = api.publish._validated_manifest(row, book.meta("id"))
        if check_source:
            if row["status"] != "prepared":
                api.fail("platform_draft_plan_unusable", "Only prepared publishing plans can save platform drafts",
                         id=plan_id, status=row["status"])
            reasons, chapter_checks = api.publish._source_comparison(book, manifest)
            if reasons:
                api.fail("platform_draft_source_changed", "Formal chapters no longer match the frozen publishing plan",
                         id=plan_id, chapters=[item["chapter"] for item in chapter_checks if not item["matches_current"]])
        yield manifest, row["fingerprint"], row["status"]


def _lock_directory():
    # Fences must survive temporary-directory cleanup and book-root restoration.
    # This per-user location contains hashed identities, never account secrets.
    path = api.safe_path(Path.home().resolve(), ".story-skill/platform-drafts")
    with api._pinned_directory(path.parent, create=True) as parent:
        try:
            if os.name == "nt":
                path.mkdir(mode=0o700)
            else:
                os.mkdir(path.name, mode=0o700, dir_fd=parent.fd)
        except FileExistsError:
            pass
        api._verify_bound_directory(parent)
    value = path.lstat()
    if (not stat.S_ISDIR(value.st_mode) or path.is_symlink() or
            getattr(path, "is_junction", lambda: False)() or
            (hasattr(os, "getuid") and (value.st_uid != os.getuid() or value.st_mode & 0o022))):
        api.fail("unsafe_platform_draft_path", "Platform execution lock directory is unsafe", path=str(path))
    return path


@contextmanager
def _target_lock(manifest, timeout=10):
    path = _lock_directory()
    with api._pinned_directory(path) as directory, ExitStack() as stack:
        file = api._BoundFile(directory, _hash(_target(manifest)) + ".lock")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = (os.open(file.path, flags, 0o600) if os.name == "nt" else
                  os.open(file.name, flags, 0o600, dir_fd=directory.fd))
        except OSError:
            api.fail("unsafe_platform_draft_path", "Could not safely open the platform execution lock")
        handle = stack.enter_context(api._owned_fdopen(fd, "r+b"))
        held = os.fstat(handle.fileno())
        current = api.publish._bound_stat(file)
        if not os.path.samestat(held, current) or held.st_nlink != 1:
            api.fail("unsafe_platform_draft_path", "Execution lock changed while opening")
        if os.name == "nt":
            stack.enter_context(api.publish._windows_database_pin(file, current))
        if held.st_size == 0:
            handle.write(b"0")
            handle.flush()
            os.fsync(handle.fileno())
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    api.fail("platform_draft_busy", "Another local process is operating on this platform work")
                time.sleep(.025)

        def guard():
            api._verify_bound_directory(directory)
            value = api.publish._bound_stat(file)
            if not os.path.samestat(held, value) or value.st_nlink != 1:
                api.fail("unsafe_platform_draft_path", "Platform execution lock changed")

        try:
            guard()
            yield _TargetState(directory, manifest, guard)
            guard()
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class _Journal:
    """Immutable numbered events; no cached mutable status file to tear."""

    def __init__(self, book, plan_id, manifest, fingerprint, directory):
        self.book, self.plan_id, self.manifest = book, plan_id, manifest
        self.fingerprint, self.directory = fingerprint, directory
        self.events = []
        self.last_hash = None
        self.chapters = {item["chapter"]: item for item in manifest["chapters"]}
        if directory is not None:
            self._load()

    def _load(self):
        directory = self.directory
        names = os.listdir(directory.path if os.name == "nt" else directory.fd)
        # Interrupted unpublished stages contain no committed event. Reject all
        # other unexpected files instead of guessing whether they are history.
        entries = []
        for name in names:
            match = _EVENT_NAME.fullmatch(name)
            if match:
                entries.append((int(match[1]), name))
            elif re.fullmatch(r"\.draft-stage-[0-9a-f]{32}", name):
                # A concurrent local status read can list a stage immediately
                # before the writer publishes it under its immutable name.
                api.publish._bound_stat(api._BoundFile(directory, name), missing=True)
            elif re.fullmatch(r"\.story-pin-[0-9a-f]{32}", name):
                # _pinned_directory owns a delete-on-close leaf guard on Windows.
                # Its native handle intentionally denies other readers.
                continue
            else:
                api.fail("platform_draft_journal_invalid", "Unrecognized platform draft journal entry")
        if len(entries) > MAX_EVENTS:
            api.fail("platform_draft_journal_limit", "Too many platform draft events")
        for sequence, name in sorted(entries):
            if sequence != len(self.events) + 1:
                api.fail("platform_draft_journal_invalid", "Platform draft journal is not consecutive")
            record = _read_record(directory, name)
            try:
                self._validate(record)
            except (ValueError, TypeError, KeyError):
                api.fail("platform_draft_journal_invalid", "Platform draft journal event is invalid",
                         sequence=sequence)
            self.events.append(record)
            self.last_hash = _hash(record)
        api._verify_bound_directory(directory)

    def _validate(self, record):
        fields = {"schema", "book_id", "plan_id", "manifest_sha256", "target_sha256", "sequence",
                  "previous_sha256", "at", "event", "chapter", "attempt_id", "remote_id",
                  "content_sha256", "observed_sha256", "title_matches", "body_matches", "reason"}
        extended = isinstance(record, dict) and record.get("schema") == 2
        equivalent = extended and record.get("event") in EQUIVALENT
        if equivalent:
            fields |= EQUIVALENCE_FIELDS
            if "body_encoding" in record:
                fields |= ENCODING_FIELDS
        elif extended:
            fields |= ATTEMPT_FIELDS | {"rendered_body_matches"}
        if not isinstance(record, dict) or set(record) != fields:
            raise ValueError("Unexpected fields")
        chapter = record["chapter"]
        if (type(record["schema"]) is not int or record["schema"] != (2 if extended else 1) or
                record["book_id"] != self.book.meta("id") or record["plan_id"] != self.plan_id or
                record["manifest_sha256"] != self.fingerprint or
                record["target_sha256"] != _hash(_target(self.manifest)) or
                type(record["sequence"]) is not int or record["sequence"] != len(self.events) + 1 or
                record["previous_sha256"] != self.last_hash or type(chapter) is not int or chapter not in self.chapters or
                record["event"] not in EVENTS or (record["event"] in EQUIVALENT and not equivalent) or
                not isinstance(record["attempt_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", record["attempt_id"]) or
                record["content_sha256"] != _hash(_content(self.chapters[chapter]))):
            raise ValueError("Invalid event identity")
        stamp = datetime.fromisoformat(record["at"])
        if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
            raise ValueError("Timestamp must be UTC")
        if record["remote_id"] is not None:
            _identifier(record["remote_id"], "remote_id")
        if record["observed_sha256"] is not None and not re.fullmatch(r"[0-9a-f]{64}", record["observed_sha256"]):
            raise ValueError("Invalid observed hash")
        if any(record[name] is not None and type(record[name]) is not bool for name in ("title_matches", "body_matches")):
            raise ValueError("Invalid comparison")
        if record["reason"] is not None and not re.fullmatch(r"[a-z][a-z0-9_]{0,99}", record["reason"]):
            raise ValueError("Invalid reason")
        if record["event"] in SUCCESS and (record["remote_id"] is None or
                record["observed_sha256"] != record["content_sha256"] or
                record["title_matches"] is not True or record["body_matches"] is not True):
            raise ValueError("A successful observation requires matching content")
        if extended and "body_encoding" in record and (not _valid_attempt(record, self.manifest["platform"], self.chapters[chapter])
                or (record["observed_sha256"] is None and record["rendered_body_matches"] is not None)
                or (record["observed_sha256"] is not None and type(record["rendered_body_matches"]) is not bool)
                or (record["observed_sha256"] is not None and record["title_matches"] is True
                    and record["rendered_body_matches"] != (record["observed_sha256"] == record["encoded_content_sha256"]))):
            raise ValueError("Invalid attempt rendering metadata")
        for previous in reversed(self.events):
            if previous["attempt_id"] == record["attempt_id"] and previous["chapter"] == chapter:
                if _record_attempt_metadata(previous) != _record_attempt_metadata(record):
                    raise ValueError("Attempt metadata changed during one attempt")
                break
        if equivalent and ((self.manifest["platform"] != "fanqie" and not
                (record.get("body_encoding") == PLATFORM_BODY_ENCODING and record["comparison_policy"] == "strict"))
                or record["remote_id"] is None
                or record["observed_sha256"] is None or not _valid_equivalence(record, self.chapters[chapter])):
            raise ValueError("Platform equivalence requires explicit rendering or terminal LF evidence")

    def append(self, chapter, event, attempt_id=None, remote_id=None, observed=None, reason=None,
               comparison_policy="strict", body_encoding="verbatim"):
        if self.directory is None:
            api.fail("platform_draft_journal_invalid", "No writable platform draft journal is open")
        if len(self.events) >= MAX_EVENTS:
            api.fail("platform_draft_journal_limit", "Too many platform draft events")
        frozen = self.chapters[chapter]
        record = {"schema": 1, "book_id": self.book.meta("id"), "plan_id": self.plan_id,
                  "manifest_sha256": self.fingerprint, "target_sha256": _hash(_target(self.manifest)),
                  "sequence": len(self.events) + 1, "previous_sha256": self.last_hash,
                  "at": _now(), "event": event, "chapter": chapter,
                  "attempt_id": attempt_id or uuid.uuid4().hex, "remote_id": remote_id,
                  "content_sha256": _hash(_content(frozen)),
                  "observed_sha256": _hash(_content(observed)) if observed is not None else None,
                  "title_matches": _normal(observed["title"]) == _normal(frozen["title"]) if observed is not None else None,
                  "body_matches": _normal(observed["body"]) == _normal(frozen["body"]) if observed is not None else None,
                  "reason": reason}
        if comparison_policy != "strict" or body_encoding != "verbatim":
            encoded = _encoded_content(frozen, body_encoding, self.manifest["platform"])
            record.update(schema=2, **_attempt_evidence(frozen, comparison_policy, body_encoding, self.manifest["platform"]),
                          rendered_body_matches=_normal(observed["body"]) == _normal(encoded["body"]) if observed is not None else None)
        if event in EQUIVALENT:
            encoded = _encoded_content(frozen, body_encoding, self.manifest["platform"])
            accepted = observed is not None and any(_comparison(encoded, observed, comparison_policy, self.manifest["platform"]))
            if not accepted or _content(frozen) == _content(observed):
                api.fail("platform_draft_journal_invalid", "Platform equivalence was not authorized or observed")
            record.update(schema=2, **_equivalence_evidence(frozen, encoded, observed, comparison_policy, body_encoding))
        self._validate(record)
        try:
            self._write(record)
        except Exception:
            api.fail("platform_draft_journal_write_failed", "Could not durably record the platform draft attempt")
        self.events.append(record)
        self.last_hash = _hash(record)
        return record

    def _write(self, record):
        _write_record(self.directory, f"event-{record['sequence']:08d}-{uuid.uuid4().hex}.json", record)

    def latest(self, chapter):
        return next((item for item in reversed(self.events) if item["chapter"] == chapter), None)

    def may_have_remote(self, chapter, retired_events=()):
        return any(item["chapter"] == chapter and item["event"] in ({"sending", "unknown"} | ACCEPTED)
                   and not (item["event"] == "unknown" and item["reason"] == "previous_attempt_not_found"
                            and _hash(item) in retired_events)
                   for item in self.events)

    def recover(self):
        for chapter in self.chapters:
            previous = self.latest(chapter)
            if previous is not None and previous["event"] == "sending":
                self.append(chapter, "unknown", attempt_id=previous["attempt_id"],
                            remote_id=previous["remote_id"], reason="interrupted_sending",
                            comparison_policy=previous.get("comparison_policy", "strict"),
                            body_encoding=previous.get("body_encoding", "verbatim"))


def _hash_bytes(data):
    return hashlib.sha256(data).hexdigest()


@contextmanager
def _journal(book, plan_id, manifest, fingerprint, create=False):
    path = api.safe_path(book.root, DIRECTORY + "/" + plan_id)
    if not create and not path.exists():
        yield _Journal(book, plan_id, manifest, fingerprint, None)
        return
    with api._pinned_directory(path, create=create) as directory:
        yield _Journal(book, plan_id, manifest, fingerprint, directory)
        api._verify_bound_directory(directory)


def _empty_recovery_claim_name(target_hash, chapter, attempt_id):
    # Deliberately outside the <target>-<chapter>- attempt namespace: ordinary
    # intent/resolution readers must continue to reject unrecognized records.
    return f"empty-recovery-{target_hash}-{chapter}-{attempt_id}.claim.json"


_ATTRIBUTION_FIELDS = {"schema", "kind", "book_id", "plan_id", "manifest_sha256", "target_sha256",
                       "chapter", "attempt_id", "remote_id", "sending_sha256", "unknown_sha256",
                       "authorized_by_user"}


def _event_epoch(value):
    return datetime.fromisoformat(value).timestamp()


def _attribution_used(attributed, expected_remote):
    return (isinstance(attributed, dict) and isinstance(expected_remote, dict) and any(
        isinstance(value, dict) and value.get("kind") == "empty_draft_recovery" and
        value.get("remote_id") == attributed.get("remote_id") and isinstance(value.get("candidate"), dict) and
        value["candidate"].get("attribution_sha256") == attributed.get("sha256")
        for value in expected_remote.values()))


def _attributed_empty_draft(book, plan_id, manifest, fingerprint, journal, evidence_path):
    """Read one private operator attestation of a failed allocation's remote ID.

    The attestation never proves the editor is blank; a live read-only
    inspection of this exact ID is still required before any claim. It names
    the one unresolved attempt it attributes and both its sending and unknown
    event, so it cannot be replayed against another attempt or journal state.
    """
    if evidence_path is None:
        return None
    path = Path(evidence_path)
    if not path.is_absolute():
        api.fail("platform_draft_attribution_invalid", "归属授权必须是绝对路径的私有证据文件。")
    try:
        with api.platform_chrome_bridge._pinned(path.parent) as directory:
            raw = api.platform_chrome_bridge._read(directory, path.name, maximum=16384)
    except (api.StoryError, OSError, ValueError):
        api.fail("platform_draft_attribution_invalid", "归属授权证据不可读取或权限不安全。")
    authorization = raw.get("authorized_by_user") if isinstance(raw, dict) else None
    now = time.time()
    if (not isinstance(raw, dict) or set(raw) != _ATTRIBUTION_FIELDS or type(raw.get("schema")) is not int or
            raw["schema"] != 1 or raw.get("kind") != "operator_attributed_empty_draft" or
            raw.get("book_id") != book.meta("id") or raw.get("plan_id") != plan_id or
            raw.get("manifest_sha256") != fingerprint or raw.get("target_sha256") != _hash(_target(manifest)) or
            type(raw.get("chapter")) is not int or raw["chapter"] < 1 or
            not _retirement_hex(raw.get("attempt_id"), 32) or
            not isinstance(raw.get("remote_id"), str) or not re.fullmatch(r"[0-9A-Za-z_-]{1,200}", raw["remote_id"]) or
            not _retirement_hex(raw.get("sending_sha256")) or not _retirement_hex(raw.get("unknown_sha256")) or
            not isinstance(authorization, dict) or set(authorization) != {"text", "confirmed_at"} or
            not isinstance(authorization.get("text"), str) or not 1 <= len(authorization["text"].strip()) <= 2000 or
            type(authorization.get("confirmed_at")) not in (int, float) or
            not math.isfinite(authorization["confirmed_at"]) or not 0 < authorization["confirmed_at"] <= now):
        api.fail("platform_draft_attribution_invalid", "归属授权证据不完整、不安全或不属于本批次。")
    events = [item for item in journal.events
              if item["chapter"] == raw["chapter"] and item["attempt_id"] == raw["attempt_id"]]
    if (len(events) < 2 or events[0]["event"] != "sending" or events[0]["remote_id"] is not None or
            events[-1]["event"] != "unknown" or events[-1]["remote_id"] is not None or
            any(item["event"] != "unknown" or item["remote_id"] is not None for item in events[1:]) or
            _hash(events[0]) != raw["sending_sha256"] or _hash(events[-1]) != raw["unknown_sha256"] or
            authorization["confirmed_at"] < _event_epoch(events[-1]["at"]) - 1):
        api.fail("platform_draft_attribution_stale", "归属授权的原始未完成记录已变化或早于授权时间。")
    return {"chapter": raw["chapter"], "attempt_id": raw["attempt_id"], "remote_id": raw["remote_id"],
            "sending_sha256": raw["sending_sha256"], "unknown_sha256": raw["unknown_sha256"],
            "sha256": _hash(raw)}


def _empty_recovery_candidate(book, plan_id, manifest, fingerprint, journal, guard, chapter, *,
                              comparison_policy, body_encoding, attributed=None):
    """Prove ownership of one failed allocation, never that its editor is empty.

    A caller must independently inspect the exact remote ID, bind that empty
    observation to a fresh inspection, and recheck it before claiming a write.
    An unknown write to an existing draft is intentionally ineligible. An
    allocation that recorded no remote ID is eligible only through the
    operator's journal-bound attribution, which still requires that live check.
    """
    guard()
    scope = {"book_id": book.meta("id"), "plan_id": plan_id, "manifest_sha256": fingerprint}
    if (not _valid_scope(scope) or type(chapter) is not int or chapter < 1
            or journal.directory is None or journal.plan_id != plan_id or journal.fingerprint != fingerprint
            or journal.book.meta("id") != scope["book_id"] or journal.manifest != manifest
            or guard.target_hash != _hash(_target(manifest)) or guard.platform != manifest["platform"]):
        return None
    frozen = next((item for item in manifest["chapters"] if item["chapter"] == chapter), None)
    if frozen is None or journal.chapters.get(chapter) != frozen:
        return None
    evidence = _attempt_evidence(frozen, comparison_policy, body_encoding, manifest["platform"])
    intents, resolved = guard.attempts(chapter)
    unresolved = [item for key, item in intents.items() if key not in resolved]
    if len(unresolved) != 1:
        return None
    intent = unresolved[0]
    attempt_id = intent["attempt_id"]
    if (intent["schema"] != 2
            or {name: intent[name] for name in SCOPE_FIELDS} != scope
            or {name: intent[name] for name in ATTEMPT_FIELDS} != evidence
            or not _valid_attempt(intent, manifest["platform"], frozen)):
        return None
    events = [item for item in journal.events if item["chapter"] == chapter and item["attempt_id"] == attempt_id]
    latest = events[-1] if events else None
    if (len(events) < 2 or latest["event"] != "unknown" or
            events[0]["event"] != "sending" or events[0]["remote_id"] is not None
            or any(item["event"] != "unknown" or item["remote_id"] != latest["remote_id"] for item in events[1:])
            or any(item["schema"] != 2 or _record_attempt_metadata(item) != evidence
                   or any(item[name] is not None for name in
                          ("observed_sha256", "title_matches", "body_matches", "rendered_body_matches"))
                   for item in events)):
        return None
    if latest["remote_id"] is None:
        if (not isinstance(attributed, dict) or attributed.get("chapter") != chapter
                or attributed.get("attempt_id") != attempt_id
                or attributed.get("sending_sha256") != _hash(events[0])
                or attributed.get("unknown_sha256") != _hash(latest)
                or not _retirement_hex(attributed.get("sha256"))):
            return None
        remote_id, attribution_sha256 = attributed["remote_id"], attributed["sha256"]
    else:
        # An operator attribution never renames a draft that already identified
        # itself; that would be a new, unreviewed write target.
        if attributed is not None:
            return None
        remote_id, attribution_sha256 = latest["remote_id"], None
    # A later connection or read-only empty-state failure may record a blocked
    # attempt before any write. Its only permitted identity is the original
    # allocation; it cannot erase that ownership. Other events remain a stop.
    later = [item for item in journal.events if item["chapter"] == chapter
             and item["sequence"] > latest["sequence"]]
    if any(item["event"] != "blocked" or item["attempt_id"] in guard.all_intents
           or item["schema"] != 2 or _record_attempt_metadata(item) != evidence
           or item["remote_id"] not in (None, remote_id)
           or any(item[name] is not None for name in
                  ("observed_sha256", "title_matches", "body_matches", "rendered_body_matches"))
           for item in later):
        return None
    # A source allocation has exactly one destination. A second intent/event
    # claiming the same ID is ambiguity, even when the desired content matches.
    no_write_sequences = {item["sequence"] for item in later}
    if any(item["remote_id"] == remote_id and item["sequence"] not in no_write_sequences and
           (item["chapter"] != chapter or item["attempt_id"] != attempt_id) for item in journal.events):
        return None
    claim_name = _empty_recovery_claim_name(guard.target_hash, chapter, attempt_id)
    if api.publish._bound_stat(api._BoundFile(guard.directory, claim_name), missing=True) is not None:
        return None
    encoded = _encoded_content(frozen, body_encoding, manifest["platform"])
    api._verify_bound_directory(journal.directory)
    guard()
    return {"schema": 1, "kind": "empty_draft_recovery", **scope, **_target(manifest),
            "target_sha256": guard.target_hash, "chapter": chapter, "attempt_id": attempt_id,
            "remote_id": remote_id, "content_sha256": intent["content_sha256"], **evidence,
            "intent_sha256": _hash(intent), "sending_sha256": _hash(events[0]), "unknown_sha256": _hash(latest),
            "attribution_sha256": attribution_sha256,
            "title_sha256": _hash_bytes(_normal(encoded["title"]).encode("utf-8")),
            "body_sha256": _hash_bytes(_normal(encoded["body"]).encode("utf-8"))}


def _claim_empty_recovery(book, plan_id, manifest, fingerprint, journal, guard, chapter, candidate, *,
                          comparison_policy, body_encoding, recovery_attempt_id, attributed=None):
    """Consume a fresh, caller-verified empty observation before any remote write.

    Keep the original sending/unknown evidence intact. The immutable claim is
    never reusable, including after a crash before the subsequent sending event.
    The target lock must remain held through claim, intent, and browser operation.
    """
    guard()
    if (not _retirement_hex(recovery_attempt_id, 32) or not isinstance(candidate, dict)
            or recovery_attempt_id == candidate.get("attempt_id") or journal.directory is None):
        api.fail("platform_draft_empty_recovery_changed", "Empty-draft recovery evidence is no longer usable")
    # Reopen the committed journal instead of trusting an inspection's in-memory
    # event snapshot. Target intents and the one-use marker are reread as well.
    current_journal = _Journal(book, plan_id, manifest, fingerprint, journal.directory)
    current = _empty_recovery_candidate(book, plan_id, manifest, fingerprint, current_journal, guard, chapter,
                                       comparison_policy=comparison_policy, body_encoding=body_encoding,
                                       attributed=attributed)
    if current is None or current != candidate or recovery_attempt_id in guard.all_intents:
        api.fail("platform_draft_empty_recovery_changed", "Empty-draft recovery evidence is no longer usable")
    record = {"schema": 1, "kind": "empty_draft_recovery_claim", "at": _now(),
              "recovery_attempt_id": recovery_attempt_id, "candidate": current}
    try:
        _write_record(guard.directory,
                      _empty_recovery_claim_name(guard.target_hash, chapter, current["attempt_id"]), record)
    except Exception:
        api.fail("platform_draft_journal_write_failed", "Could not persist the one-use empty-draft recovery claim")
    guard()
    return {name: current[name] for name in ("schema", "attempt_id", "remote_id", "chapter", "platform",
                                             "account_id", "remote_book_id", "title_sha256", "body_sha256")}


def _observation(raw, remote_id=None):
    if not isinstance(raw, dict) or raw.get("state") != "draft":
        api.fail("platform_draft_invalid_observation", "A complete draft observation is required")
    actual_id = _identifier(raw.get("remote_id"), "remote_id")
    if remote_id is not None and actual_id != remote_id:
        api.fail("platform_draft_invalid_observation", "Readback belongs to a different remote chapter")
    if any(not isinstance(raw.get(name), str) for name in ("title", "body")):
        api.fail("platform_draft_invalid_observation", "Full draft title and body are required")
    if "formatting_clean" in raw and type(raw["formatting_clean"]) is not bool:
        api.fail("platform_draft_invalid_observation", "Formatting evidence must be boolean")
    return {name: raw[name] for name in ("remote_id", "title", "body", "state", "formatting_clean") if name in raw}


def _packet(plan_id, manifest, plan_status, journal, *, preview=False, remote_accessed=False,
            target_verified=False, source_checked=False, observed_chapters=(), comparison_policy="strict",
            body_encoding="verbatim", attempt_ids=()):
    results = []
    for item in manifest["chapters"]:
        latest = journal.latest(item["chapter"])
        effective = "pending" if latest is None else latest["event"]
        if effective == "sending":
            effective = "unknown"
        observed_this_run = item["chapter"] in observed_chapters
        recorded_this_run = latest is not None and latest["attempt_id"] in attempt_ids
        equivalent = effective in EQUIVALENT
        encoding = latest.get("body_encoding", "verbatim") if latest else body_encoding
        encoded_hash = _hash(_content(_encoded_content(item, encoding, manifest["platform"])))
        rendered_match = None
        if latest is not None and latest["observed_sha256"] is not None:
            if encoding == "verbatim":
                rendered_match = latest["body_matches"]
            elif latest["title_matches"] is True:
                rendered_match = latest["observed_sha256"] == encoded_hash
        accepted = observed_this_run and (effective in SUCCESS or (equivalent and
            comparison_policy == latest["comparison_policy"] and body_encoding == latest.get("body_encoding", "verbatim")))
        results.append({"chapter": item["chapter"], "state": effective,
                        "remote_id": latest["remote_id"] if latest is not None else None,
                        "title_body_verified": observed_this_run and effective in SUCCESS,
                        "historical_title_body_match": effective in SUCCESS,
                        "platform_equivalent": equivalent,
                        "accepted_this_run": accepted,
                        "comparison_policy": latest.get("comparison_policy", "strict") if latest else "strict",
                        "body_encoding": encoding,
                        "content_sha256": latest["content_sha256"] if latest else _hash(_content(item)),
                        "source_content_sha256": latest["content_sha256"] if latest else _hash(_content(item)),
                        "encoded_content_sha256": encoded_hash,
                        "observed_sha256": latest["observed_sha256"] if latest else None,
                        "rendered_body_matches": rendered_match,
                        "rendered": accepted and encoding != "verbatim" and encoded_hash != _hash(_content(item)),
                        "title_matches": latest["title_matches"] if latest else None,
                        "body_matches": latest["body_matches"] if latest else None,
                        "observed_this_run": observed_this_run,
                        "recorded_this_run": recorded_this_run,
                        "observation_scope": "this_run" if observed_this_run else "this_run_attempt" if recorded_this_run else "recorded_history",
                        "last_observed_at": latest["at"] if latest is not None else None,
                        "reason": latest["reason"] if latest is not None else None,
                        "recovery_required": latest is not None and latest["event"] == "sending"})
    all_current = all(item["title_body_verified"] for item in results)
    all_accepted = all(item["accepted_this_run"] for item in results)
    return {"ok": all_accepted, "id": plan_id,
            "scope": "platform_chapter_drafts_only", "platform": manifest["platform"],
            "account_id": manifest["account_id"], "remote_book_id": manifest["remote_book_id"],
            "book_title": manifest["book_title"], "plan_status": plan_status,
            "preview": preview, "remote_accessed": remote_accessed,
            "target_verified": target_verified, "source_check_performed": source_checked,
            "content_fields": ["title", "body"], "normalization": "crlf_cr_to_lf_v1",
            "comparison_policy": comparison_policy,
            "body_encoding": body_encoding,
            "author_note_included": False, "submission_performed": False,
            "all_chapters_verified_this_run": all_current,
            "all_chapters_accepted_this_run": all_accepted,
            "exact_verified_chapters": sum(item["title_body_verified"] for item in results),
            "equivalent_chapters": sum(item["accepted_this_run"] and item["platform_equivalent"] for item in results),
            "rendered_chapters": sum(item["rendered"] for item in results),
            "accepted_chapters": sum(item["accepted_this_run"] for item in results),
            "created_chapters": sum(item["accepted_this_run"] and item["reason"] == "new_draft_created" for item in results),
            "updated_chapters": sum(item["accepted_this_run"] and item["reason"] in {"existing_draft_updated", "empty_draft_recovered"} for item in results),
            "unchanged_chapters": sum(item["accepted_this_run"] and item["state"] in {"reconciled", "reconciled_equivalent"} for item in results),
            "historical_all_title_body_match": all(item["historical_title_body_match"] for item in results),
            "observation_scope": "recorded_history" if not remote_accessed else "this_run_and_recorded_history",
            "results": results, "history_events_total": len(journal.events),
            "recent_events": journal.events[-20:], "history_truncated": len(journal.events) > 20,
            "source_matches_current": True if source_checked else None}


def status(book, plan_id):
    """Report recorded observations, without refreshing source or platform facts."""
    with _checked_plan(book, plan_id, check_source=False) as (manifest, fingerprint, plan_status):
        with _journal(book, plan_id, manifest, fingerprint) as journal:
            latest = [journal.latest(item["chapter"]) for item in manifest["chapters"]]
            policies = sorted({item.get("comparison_policy", "strict") for item in latest if item is not None})
            encodings = sorted({item.get("body_encoding", "verbatim") for item in latest if item is not None})
            result = _packet(plan_id, manifest, plan_status, journal,
                             comparison_policy=policies[0] if len(policies) == 1 else "strict",
                             body_encoding=encodings[0] if len(encodings) == 1 else "verbatim")
            result.update(recorded_comparison_policies=policies, recorded_body_encodings=encodings)
            result["ok"] = True  # The read succeeded; remote facts were not refreshed.
            return result


def require_platform_comparison_policy(value):
    if value != "strict":
        api.fail("platform_draft_comparison_policy_required", "平台正文须严格匹配去空白行后的本地正文；远端空行及末尾换行不能忽略。请重新预览。")
    return value


def _remote_signature(observed):
    return _hash({key: observed.get(key) for key in ("remote_id", "title", "body", "state", "formatting_clean")})


def _inspection_recovery_plans(book, manifest, pending):
    """Link pending writes only to locally validated original frozen batches."""
    grouped = {}
    for chapter, intent in pending:
        if intent["schema"] == 2 and intent["book_id"] == book.meta("id"):
            grouped.setdefault((intent["plan_id"], intent["manifest_sha256"]), []).append((chapter, intent))
    plans = []
    for (plan_id, fingerprint), attempts in grouped.items():
        try:
            with _checked_plan(book, plan_id, check_source=False) as (original, actual, _):
                if actual != fingerprint or _target(original) != _target(manifest):
                    continue
                chapters = {item["chapter"]: item for item in original["chapters"]}
                recoverable = sorted({chapter for chapter, intent in attempts if chapter in chapters
                    and intent["content_sha256"] == _hash(_content(chapters[chapter]))
                    and _valid_attempt(intent, original["platform"], chapters[chapter])})
                if recoverable:
                    plans.append({"book_id": book.meta("id"), "plan_id": plan_id,
                        "manifest_sha256": actual, "target": _target(original),
                        "chapters": sorted(chapters), "pending_chapters": recoverable})
        except (api.StoryError, OSError):
            # Missing, restored or damaged history is not a trustworthy route.
            # The inspection remains blocked; no historical intent is changed.
            continue
    return sorted(plans, key=lambda item: (item["pending_chapters"][0], item["plan_id"]))


def _draft_difference(expected, observed):
    """Bounded, plain-text display only. Equality always uses the entire body."""
    before = observed["body"] if observed else ""
    after = expected["body"]
    lines = difflib.unified_diff(before.splitlines(), after.splitlines(),
                                fromfile="平台草稿", tofile="本地正式稿（去空白行）", n=2, lineterm="")
    text = "\n".join(lines)
    if observed and before.endswith("\n") != after.endswith("\n"):
        text += "\n末尾换行：平台" + ("有" if before.endswith("\n") else "无") + "；本地转换正文" + ("有" if after.endswith("\n") else "无")
    return {"remote_title": observed["title"] if observed else None, "local_title": expected["title"],
            "title_changed": observed is None or observed["title"] != expected["title"],
            "body_changed": observed is None or before != after,
            "remote_characters": len(before), "local_characters": len(after),
            "body_diff": text[:6000], "diff_truncated": len(text) > 6000}


def _inspection_catalog(raw, target):
    """Validate one complete, target-bound observation; never a persisted cache."""
    if (not isinstance(raw, dict) or set(raw) != {"target", "complete", "entries", "proof_id"} or
            raw.get("target") != target or raw.get("complete") is not True or
            not isinstance(raw.get("entries"), list) or len(raw["entries"]) > 100000):
        api.fail("platform_draft_invalid_observation", "需要当前目标的完整平台目录。")
    _identifier(raw["proof_id"], "proof_id")
    remotes = set()
    for entry in raw["entries"]:
        if (not isinstance(entry, dict) or set(entry) != {"chapter", "remote_id", "state"} or
                not (type(entry.get("chapter")) is int and 1 <= entry["chapter"] <= 1000000 or
                     entry.get("chapter") is None and entry.get("state") == "draft") or
                entry.get("state") not in {"draft", "non_draft"}):
            api.fail("platform_draft_invalid_observation", "平台目录章节信息不完整。")
        if entry["state"] == "draft":
            remote = _identifier(entry["remote_id"], "remote_id")
            if remote in remotes:
                api.fail("platform_draft_invalid_observation", "平台目录重复列出同一草稿。")
            remotes.add(remote)
        elif entry["remote_id"] is not None:
            api.fail("platform_draft_invalid_observation", "非草稿条目不能作为草稿写入目标。")
    return raw["entries"]


def _catalog_chapter(entries, chapter):
    found = [entry for entry in entries if entry["chapter"] == chapter]
    if any(entry["state"] != "draft" for entry in found):
        api.fail("platform_browser_chapter_exists_not_draft", "该章已在非草稿目录中，不能创建或覆盖。")
    return found


def inspect_plan(book, plan_id, adapter, *, progress=None, stop_requested=None,
                 comparison_policy="strict", body_encoding=PLATFORM_BODY_ENCODING, attribution_path=None):
    """Inspect the whole batch without journal writes, editor fills or saves."""
    require_platform_comparison_policy(comparison_policy)
    require_platform_body_encoding(body_encoding)
    with _checked_plan(book, plan_id) as (manifest, fingerprint, _):
        frozen_items = manifest["chapters"]
        encoded = {item["chapter"]: _encoded_content(item, body_encoding, manifest["platform"]) for item in frozen_items}
    rows, expected_remote, pending_recovery = [], {}, []
    stopped = False
    catalog = None
    empty_recoveries = {}
    inspect_catalog = getattr(adapter, "inspect_catalog", None)
    with _target_lock(manifest) as guard, _journal(book, plan_id, manifest, fingerprint) as journal:
        attributed = _attributed_empty_draft(book, plan_id, manifest, fingerprint, journal, attribution_path)
        for item in frozen_items:
            if stop_requested and stop_requested():
                stopped = True
                break
            chapter = item["chapter"]
            row = {"chapter": chapter, "title": item["title"], "action": "blocked", "reason": None}
            if progress:
                progress({"stage": "inspection_started", "chapter": chapter, "checked_chapters": len(rows)})
            try:
                with _checked_plan(book, plan_id) as (_, current, _):
                    if current != fingerprint:
                        api.fail("platform_draft_source_changed", "正式稿在检查期间变化，请重新准备。")
                guard()
                if callable(inspect_catalog):
                    if catalog is None:
                        adapter.verify_target(**_target(manifest))
                        catalog = _inspection_catalog(inspect_catalog(), _target(manifest))
                        unnumbered = [entry for entry in catalog if entry["chapter"] is None]
                        if unnumbered:
                            candidates = [_empty_recovery_candidate(book, plan_id, manifest, fingerprint, journal, guard,
                                item["chapter"], comparison_policy=comparison_policy, body_encoding=body_encoding,
                                attributed=attributed)
                                for item in frozen_items]
                            for entry in unnumbered:
                                matches = [candidate for candidate in candidates if candidate and candidate["remote_id"] == entry["remote_id"]]
                                if len(matches) != 1 or not callable(getattr(adapter, "inspect_empty_draft", None)):
                                    api.fail("platform_browser_unnumbered_draft", "无法确认无章号草稿属于本次未完成的新建操作。")
                                candidate = matches[0]
                                if candidate["chapter"] in empty_recoveries or _catalog_chapter(catalog, candidate["chapter"]):
                                    api.fail("platform_browser_ambiguous_draft", "恢复目标与目录中已有章节冲突。")
                                empty = adapter.inspect_empty_draft(entry["remote_id"], candidate["chapter"])
                                api.platform_chrome_bridge._validate_result("inspect_empty_draft", empty, _target(manifest),
                                                                           {"remote_id": entry["remote_id"]})
                                empty_recoveries[candidate["chapter"]] = candidate
                    found = _catalog_chapter(catalog, chapter)
                else:
                    adapter.verify_target(**_target(manifest))
                    found = adapter.find_drafts(chapter)
                if not isinstance(found, list):
                    api.fail("platform_draft_invalid_observation", "需要完整目录。")
                if chapter in empty_recoveries:
                    candidate = empty_recoveries[chapter]
                    row.update(action="update", reason="resume_empty_draft", remote_id=candidate["remote_id"],
                               difference=_draft_difference(encoded[chapter], {"title": "", "body": ""}))
                    expected_remote[str(chapter)] = {"kind": "empty_draft_recovery", "remote_id": candidate["remote_id"],
                                                     "candidate": candidate}
                elif len(found) > 1:
                    row["reason"] = "multiple_remote_drafts"
                elif found:
                    listed = found[0] if catalog is not None else _observation(found[0])
                    guard()
                    adapter.verify_target(**_target(manifest))
                    observed = _observation(adapter.read_draft(listed["remote_id"]), listed["remote_id"])
                    same = _content(encoded[chapter]) == _content(observed) and observed.get("formatting_clean") is not False
                    intents, resolved = guard.attempts(chapter)
                    unresolved = [intent for key, intent in intents.items() if key not in resolved]
                    row.update(remote_id=observed["remote_id"], action="unchanged" if same else "update",
                               difference=_draft_difference(encoded[chapter], observed))
                    # A same-batch lost receipt does not require a separate
                    # reconciliation pass after this independent full read.
                    # Inspection remains read-only; sync re-reads this exact ID
                    # and only then publishes the original resolution records.
                    current_attempt = _attempt_evidence(item, comparison_policy, body_encoding, manifest["platform"])
                    unresolved_ids = {intent["attempt_id"] for intent in unresolved}
                    known_remotes = {event["remote_id"] for event in journal.events
                                     if event["chapter"] == chapter and event["attempt_id"] in unresolved_ids
                                     and event["remote_id"] is not None}
                    compatible = same and all(_record_attempt_metadata(intent) == current_attempt for intent in unresolved)
                    compatible = compatible and known_remotes <= {observed["remote_id"]} and guard.can_resolve(
                        chapter, observed, frozen=item, comparison_policy=comparison_policy, body_encoding=body_encoding,
                        scope={"book_id": book.meta("id"), "plan_id": plan_id, "manifest_sha256": fingerprint})
                    if unresolved and not compatible:
                        row.update(action="blocked", reason="unresolved_previous_content")
                        pending_recovery.extend((chapter, intent) for intent in unresolved)
                    expected_remote[str(chapter)] = {"remote_id": observed["remote_id"],
                                                     "signature": _remote_signature(observed)}
                else:
                    attempts, resolved = guard.attempts(chapter)
                    if attempts or journal.may_have_remote(chapter, guard.retired_events(chapter, book.meta("id"), plan_id)):
                        row["reason"] = "previous_attempt_not_found"
                        pending_recovery.extend((chapter, intent) for key, intent in attempts.items() if key not in resolved)
                    else:
                        row.update(action="create", difference=_draft_difference(encoded[chapter], None))
                        expected_remote[str(chapter)] = None
            except Exception as error:
                row["reason"] = _error_code(error)
                rows.append(row)
                break
            rows.append(row)
            if progress:
                progress({"stage": "inspection_checked", "chapter": chapter, "checked_chapters": len(rows)})
        with _checked_plan(book, plan_id, check_source=False):
            reasons, _ = api.publish._source_comparison(book, manifest)
        recovery_plans = _inspection_recovery_plans(book, manifest, pending_recovery)
    complete = len(rows) == len(frozen_items) and not reasons and not stopped
    can_execute = complete and all(row["action"] != "blocked" for row in rows)
    used = attributed is not None and _attribution_used(attributed, expected_remote)
    if attributed is not None and can_execute and not used:
        # A supplied authorization that the live catalog did not need must never
        # authorize a write on the strength of an untouched attribution.
        api.fail("platform_draft_attribution_unused", "归属授权未能唯一对上目录中的无章号空草稿。")
    return {"id": plan_id, "inspection": True, "remote_accessed": bool(rows), "submission_performed": False,
            "source_matches_current": not reasons, "inspection_complete": complete, "stop_requested": stopped,
            "can_execute": can_execute,
            "checked_chapters": len(rows), "total_chapters": len(frozen_items), "results": [],
            "changes": rows, "change_counts": {key: sum(row["action"] == key for row in rows)
                                                for key in ("create", "update", "unchanged", "blocked")},
            "recovery_plans": recovery_plans,
            "attribution_sha256": attributed["sha256"] if used else None,
            "_expected_remote": expected_remote, "_manifest_sha256": fingerprint}


def run_plan(book, plan_id, adapter=None, *, execute=False, reconcile_only=False, progress=None,
             comparison_policy="strict", body_encoding="verbatim", sync_drafts=False,
             expected_remote=None, stop_requested=None, attribution_path=None):
    """Execute only explicitly requested saves; injected adapters own browser I/O."""
    if type(sync_drafts) is not bool or type(reconcile_only) is not bool or (sync_drafts and reconcile_only):
        api.fail("invalid_input", "Choose one draft execution mode")
    if attribution_path is not None and not sync_drafts:
        api.fail("platform_draft_attribution_invalid", "归属授权只用于核对后的同步。")
    with _checked_plan(book, plan_id, check_source=not reconcile_only) as (manifest, fingerprint, plan_status):
        _comparison_policy(comparison_policy, manifest["platform"])
        if body_encoding == PLATFORM_BODY_ENCODING:
            require_platform_comparison_policy(comparison_policy)
        if sync_drafts and manifest["platform"] != "fanqie":
            api.fail("platform_draft_update_unsupported", "当前仅支持核对后更新番茄已有草稿。")
        encoded_chapters = {item["chapter"]: _encoded_content(item, body_encoding, manifest["platform"])
                            for item in manifest["chapters"]}
        # Keep immutable in-memory content and never hold a creative transaction
        # or lock across target verification, login, save or remote readback.
        if not execute:
            with _journal(book, plan_id, manifest, fingerprint) as journal:
                result = _packet(plan_id, manifest, plan_status, journal, preview=True, source_checked=not reconcile_only,
                                 comparison_policy=comparison_policy, body_encoding=body_encoding)
                result["ok"] = True
                result["chapters"] = [{"chapter": item["chapter"], "title": item["title"],
                                       "body_characters": item["body_characters"],
                                       "encoded_body_characters": len(encoded_chapters[item["chapter"]]["body"]),
                                       "source_content_sha256": _hash(_content(item)),
                                       "encoded_content_sha256": _hash(_content(encoded_chapters[item["chapter"]]))}
                                      for item in manifest["chapters"]]
                result["requested_mode"] = "sync_drafts" if sync_drafts else ("reconcile_only" if reconcile_only else "save_draft")
                return result
    if adapter is None:
        api.fail("platform_draft_adapter_required", "Execution requires a platform draft adapter")
    with _target_lock(manifest) as guard, _journal(book, plan_id, manifest, fingerprint, create=True) as journal:
        journal.recover()
        attributed = _attributed_empty_draft(book, plan_id, manifest, fingerprint, journal, attribution_path)
        if attributed is not None and not _attribution_used(attributed, expected_remote):
            api.fail("platform_draft_inspection_changed", "本次同步未绑定这份归属授权的检查结果，请重新检查差异。")
        target_verified = False
        remote_accessed = False
        observed_chapters = set()
        attempt_ids = set()
        exact_chapters, equivalent_chapters = set(), set()
        stopped = False
        scope = {"book_id": book.meta("id"), "plan_id": plan_id, "manifest_sha256": fingerprint}
        # A sync consumes an unexpired inspection selected by its caller. Read
        # the whole directory once at batch entry, then address known IDs and
        # create only chapters that this fresh directory proves are absent.
        # Keep reconciliation and adapters without a catalog protocol on their
        # existing path; an unknown write is never a missing-chapter shortcut.
        catalog = None
        inspect_catalog = getattr(adapter, "inspect_catalog", None)
        batch_catalog = sync_drafts and expected_remote is not None and callable(inspect_catalog)

        def append(chapter, event, **kwargs):
            return journal.append(chapter, event, comparison_policy=comparison_policy, body_encoding=body_encoding, **kwargs)

        def report(stage, chapter):
            if progress is not None:
                progress({"stage": stage, "chapter": chapter, "verified_chapters": len(exact_chapters),
                          "equivalent_chapters": len(equivalent_chapters),
                          "rendered_chapters": sum(_content(journal.chapters[number]) != _content(encoded_chapters[number])
                                                   for number in exact_chapters | equivalent_chapters),
                          "accepted_chapters": len(exact_chapters) + len(equivalent_chapters),
                          "total_chapters": len(manifest["chapters"]),
                          "chapter_result": _packet(plan_id, {**manifest, "chapters": [frozen]},
                              plan_status, journal, observed_chapters=observed_chapters,
                              comparison_policy=comparison_policy, body_encoding=body_encoding,
                              attempt_ids=attempt_ids)["results"][0]})

        for frozen in manifest["chapters"]:
            if stop_requested and stop_requested():
                stopped = True
                break
            chapter = frozen["chapter"]
            encoded = encoded_chapters[chapter]
            report("chapter_started", chapter)
            attempt_id = uuid.uuid4().hex
            attempt_ids.add(attempt_id)
            sending = False
            remote_id = None
            try:
                # Fail before contacting a platform if the local plan was changed
                # while another process held the platform work lock.
                with _checked_plan(book, plan_id, check_source=not reconcile_only) as (_, current_fingerprint, _):
                    if current_fingerprint != fingerprint:
                        api.fail("platform_draft_source_changed", "Publishing plan changed")
                guard()
                remote_accessed = True
                target_verified = False
                adapter.verify_target(**_target(manifest))
                target_verified = True
                if batch_catalog:
                    if catalog is None:
                        catalog = _inspection_catalog(inspect_catalog(), _target(manifest))
                        if set(expected_remote) != {str(item["chapter"]) for item in manifest["chapters"]}:
                            api.fail("platform_draft_inspection_changed", "检查范围与本次清单不一致，请重新检查差异。")
                        recovery_ids = {value["remote_id"] for value in expected_remote.values()
                            if isinstance(value, dict) and value.get("kind") == "empty_draft_recovery"}
                        if any(entry["chapter"] is None and entry["remote_id"] not in recovery_ids for entry in catalog):
                            api.fail("platform_browser_unnumbered_draft", "目录出现未确认归属的无章号草稿。")
                        for item in manifest["chapters"]:
                            listed = _catalog_chapter(catalog, item["chapter"])
                            previous = expected_remote[str(item["chapter"])]
                            if isinstance(previous, dict) and previous.get("kind") == "empty_draft_recovery":
                                empty = [entry for entry in catalog if entry["chapter"] is None and
                                         entry["remote_id"] == previous.get("remote_id")]
                                if listed or len(empty) != 1:
                                    api.fail("platform_draft_inspection_changed", "原空草稿在检查后发生变化，请重新检查差异。")
                                continue
                            if (len(listed) > 1 or (not listed) != (previous is None) or
                                    (listed and (not isinstance(previous, dict) or
                                     listed[0]["remote_id"] != previous.get("remote_id")))):
                                api.fail("platform_draft_inspection_changed", "平台目录在检查后发生变化，请重新检查差异。")
                        # Recovery targets must be re-verified empty before their
                        # own turn so earlier chapter reads and updates in this
                        # same batch are not blocked by the declared blank row.
                        for number, previous in expected_remote.items():
                            if isinstance(previous, dict) and previous.get("kind") == "empty_draft_recovery":
                                empty = adapter.inspect_empty_draft(previous["remote_id"], int(number))
                                api.platform_chrome_bridge._validate_result(
                                    "inspect_empty_draft", empty, _target(manifest),
                                    {"remote_id": previous["remote_id"]})
                    found = _catalog_chapter(catalog, chapter)
                else:
                    found = adapter.find_drafts(chapter)
                if not isinstance(found, list):
                    api.fail("platform_draft_invalid_observation", "The complete remote draft list is required")
                previous = expected_remote.get(str(chapter)) if batch_catalog else None
                if isinstance(previous, dict) and previous.get("kind") == "empty_draft_recovery":
                    candidate = _empty_recovery_candidate(book, plan_id, manifest, fingerprint, journal, guard, chapter,
                        comparison_policy=comparison_policy, body_encoding=body_encoding, attributed=attributed)
                    if not candidate or candidate != previous.get("candidate") or candidate["remote_id"] != previous.get("remote_id"):
                        api.fail("platform_draft_inspection_changed", "空草稿恢复资格已改变，请重新检查差异。")
                    remote_id = candidate["remote_id"]
                    empty = adapter.inspect_empty_draft(remote_id, chapter)
                    api.platform_chrome_bridge._validate_result("inspect_empty_draft", empty, _target(manifest), {"remote_id": remote_id})
                    if stop_requested and stop_requested():
                        api.fail("platform_draft_stop_requested", "已在恢复空草稿前安全停止。")
                    with _checked_plan(book, plan_id) as (_, current_fingerprint, _):
                        if current_fingerprint != fingerprint:
                            api.fail("platform_draft_source_changed", "Publishing plan changed")
                        recovery = _claim_empty_recovery(book, plan_id, manifest, fingerprint, journal, guard, chapter,
                            candidate, comparison_policy=comparison_policy, body_encoding=body_encoding,
                            recovery_attempt_id=attempt_id, attributed=attributed)
                        guard.intent(chapter, attempt_id, frozen, comparison_policy=comparison_policy,
                                     body_encoding=body_encoding, scope=scope)
                        sending = True
                        append(chapter, "sending", attempt_id=attempt_id, remote_id=remote_id, reason="recovering_empty_draft")
                    guard()
                    actual_id = adapter.resume_empty_draft(remote_id, chapter, encoded["title"], encoded["body"], recovery=recovery)
                    if _identifier(actual_id, "remote_id") != remote_id:
                        api.fail("platform_draft_invalid_observation", "恢复回执的远端章节身份已改变。")
                    guard()
                    target_verified = False
                    adapter.verify_target(**_target(manifest))
                    target_verified = True
                    observed = _observation(adapter.read_draft(remote_id), remote_id)
                    observed_chapters.add(chapter)
                    rendered_exact, terminal_equivalent = _comparison(encoded, observed, comparison_policy, manifest["platform"])
                    exact = _content(frozen) == _content(observed)
                    if (not rendered_exact and not terminal_equivalent) or observed.get("formatting_clean") is False:
                        append(chapter, "conflict", attempt_id=attempt_id, remote_id=remote_id,
                               observed=observed, reason="saved_content_differs")
                        break
                    if not guard.resolve(chapter, observed, frozen=frozen, comparison_policy=comparison_policy,
                                         body_encoding=body_encoding, scope=scope):
                        append(chapter, "conflict", attempt_id=attempt_id, remote_id=remote_id,
                               observed=observed, reason="unresolved_previous_content")
                        break
                    append(chapter, "saved" if exact else "saved_equivalent", attempt_id=attempt_id,
                           remote_id=remote_id, observed=observed, reason="empty_draft_recovered")
                    (exact_chapters if exact else equivalent_chapters).add(chapter)
                    report("chapter_verified", chapter)
                    continue
                if len(found) > 1:
                    append(chapter, "conflict", attempt_id=attempt_id, reason="multiple_remote_drafts")
                    break
                if found:
                    listed = found[0] if batch_catalog else _observation(found[0])
                    remote_id = listed["remote_id"]
                    guard()
                    target_verified = False
                    adapter.verify_target(**_target(manifest))
                    target_verified = True
                    observed = _observation(adapter.read_draft(remote_id), remote_id)
                    expected = expected_remote.get(str(chapter)) if expected_remote is not None else None
                    if expected_remote is not None and (not isinstance(expected, dict) or
                            expected.get("remote_id") != remote_id or expected.get("signature") != _remote_signature(observed)):
                        api.fail("platform_draft_inspection_changed", "草稿在检查后发生变化，请重新检查差异。")
                    observed_chapters.add(chapter)
                    rendered_exact, terminal_equivalent = _comparison(encoded, observed, comparison_policy, manifest["platform"])
                    exact = _content(frozen) == _content(observed)
                    needs_update = (not rendered_exact and not terminal_equivalent) or observed.get("formatting_clean") is False
                    if needs_update and not sync_drafts:
                        append(chapter, "conflict", attempt_id=attempt_id, remote_id=remote_id,
                               observed=observed, reason="remote_formatting_metadata" if observed.get("formatting_clean") is False else "remote_content_differs")
                        break
                    if needs_update:
                        if stop_requested and stop_requested():
                            api.fail("platform_draft_stop_requested", "已在写入前安全停止。")
                        intents, resolved = guard.attempts(chapter)
                        if any(key not in resolved for key in intents):
                            append(chapter, "unknown", attempt_id=attempt_id, remote_id=remote_id,
                                   observed=observed, reason="unresolved_previous_content")
                            break
                        if not callable(getattr(adapter, "update_draft", None)):
                            api.fail("platform_draft_update_unsupported", "当前执行端不支持更新已有草稿。")
                        # Full private before-image must be durable before any editor opens.
                        backup = api.safe_path(book.root, ".story/platform-draft-update-backups/" + plan_id)
                        with api._pinned_directory(backup, create=True) as directory:
                            _write_record(directory, attempt_id + ".json", {
                                "schema": 1, "at": _now(), **scope, "target": _target(manifest),
                                "chapter": chapter, "attempt_id": attempt_id, "before": observed,
                                "desired_content_sha256": _hash(_content(encoded))}, max_bytes=16 * 1024 * 1024)
                        with _checked_plan(book, plan_id) as (_, current_fingerprint, _):
                            if current_fingerprint != fingerprint:
                                api.fail("platform_draft_source_changed", "Publishing plan changed")
                            guard.intent(chapter, attempt_id, frozen, comparison_policy=comparison_policy,
                                         body_encoding=body_encoding, scope=scope)
                            sending = True
                            append(chapter, "sending", attempt_id=attempt_id, remote_id=remote_id,
                                   reason="updating_existing_draft")
                        guard()
                        updated_id = adapter.update_draft(remote_id, chapter, encoded["title"], encoded["body"],
                            expected_title=observed["title"], expected_body=observed["body"])
                        if _identifier(updated_id, "remote_id") != remote_id:
                            api.fail("platform_draft_invalid_observation", "Update changed the remote chapter identity")
                        guard()
                        target_verified = False
                        adapter.verify_target(**_target(manifest))
                        target_verified = True
                        observed = _observation(adapter.read_draft(remote_id), remote_id)
                        rendered_exact, terminal_equivalent = _comparison(encoded, observed, comparison_policy, manifest["platform"])
                        exact = _content(frozen) == _content(observed)
                        if (not rendered_exact and not terminal_equivalent) or observed.get("formatting_clean") is False:
                            append(chapter, "conflict", attempt_id=attempt_id, remote_id=remote_id,
                                   observed=observed, reason="saved_formatting_metadata" if observed.get("formatting_clean") is False else "saved_content_differs")
                            break
                    if not guard.resolve(chapter, observed, frozen=frozen, comparison_policy=comparison_policy,
                                         body_encoding=body_encoding, scope=scope):
                        append(chapter, "conflict", attempt_id=attempt_id, remote_id=remote_id,
                                       observed=observed, reason="unresolved_previous_content")
                        break
                    event = ("saved" if exact else "saved_equivalent") if needs_update else ("reconciled" if exact else "reconciled_equivalent")
                    append(chapter, event, attempt_id=attempt_id, remote_id=remote_id, observed=observed,
                           **({"reason": "existing_draft_updated"} if needs_update else {}))
                    # The original-source match remains distinct from a rendered match.
                    (exact_chapters if exact else equivalent_chapters).add(chapter)
                    report("chapter_verified", chapter)
                    continue
                target_attempts, _ = guard.attempts(chapter)
                if expected_remote is not None and (str(chapter) not in expected_remote or expected_remote[str(chapter)] is not None):
                    api.fail("platform_draft_inspection_changed", "草稿目录在检查后发生变化，请重新检查差异。")
                if journal.may_have_remote(chapter, guard.retired_events(chapter, book.meta("id"), plan_id)) or target_attempts:
                    append(chapter, "unknown", attempt_id=attempt_id, reason="previous_attempt_not_found")
                    break
                if reconcile_only:
                    append(chapter, "not_found", attempt_id=attempt_id, reason="remote_draft_not_found")
                    break
                if stop_requested and stop_requested():
                    api.fail("platform_draft_stop_requested", "已在新建前安全停止。")
                guard()
                target_verified = False
                adapter.verify_target(**_target(manifest))
                target_verified = True
                # Final local check and durable intent share the writer lock.
                # No remote operation occurs until both have completed.
                with _checked_plan(book, plan_id) as (_, current_fingerprint, _):
                    if current_fingerprint != fingerprint:
                        api.fail("platform_draft_source_changed", "Publishing plan changed")
                    guard.intent(chapter, attempt_id, frozen, comparison_policy=comparison_policy,
                                 body_encoding=body_encoding, scope=scope)
                    sending = True
                    append(chapter, "sending", attempt_id=attempt_id)
                guard()
                remote_id = _identifier(adapter.create_draft(chapter, encoded["title"], encoded["body"]), "remote_id")
                guard()
                target_verified = False
                adapter.verify_target(**_target(manifest))
                target_verified = True
                observed = _observation(adapter.read_draft(remote_id), remote_id)
                observed_chapters.add(chapter)
                rendered_exact, terminal_equivalent = _comparison(encoded, observed, comparison_policy, manifest["platform"])
                exact = _content(frozen) == _content(observed)
                if (not rendered_exact and not terminal_equivalent) or observed.get("formatting_clean") is False:
                    append(chapter, "conflict", attempt_id=attempt_id, remote_id=remote_id,
                           observed=observed, reason="saved_formatting_metadata" if observed.get("formatting_clean") is False else "saved_content_differs")
                    break
                append(chapter, "saved" if exact else "saved_equivalent", attempt_id=attempt_id,
                               remote_id=remote_id, observed=observed, reason="new_draft_created")
                if not guard.resolve(chapter, observed, frozen=frozen, comparison_policy=comparison_policy,
                                     body_encoding=body_encoding, scope=scope):
                    api.fail("platform_draft_unresolved_previous_content", "A previous content version remains unresolved")
                (exact_chapters if exact else equivalent_chapters).add(chapter)
                report("chapter_verified", chapter)
            except Exception as error:
                if _error_code(error) == "platform_draft_stop_requested" and not sending:
                    stopped = True
                    break
                if _error_code(error).startswith("platform_draft_journal"):
                    raise
                if sending and remote_id is None and isinstance(error, api.StoryError):
                    allocated = error.details.get("allocated_remote_id")
                    if allocated is not None:
                        try:
                            remote_id = _identifier(allocated, "remote_id")
                        except api.StoryError:
                            pass
                # A write may have reached the platform even if create() raised.
                # Record only a safe error code; an interrupted sending event is
                # itself sufficient to prohibit blind replay on the next run.
                append(chapter, "unknown" if sending else "blocked", attempt_id=attempt_id,
                               remote_id=remote_id, reason=_error_code(error))
                break
        with _checked_plan(book, plan_id, check_source=False) as (_, _, current_status):
            current_reasons, _ = api.publish._source_comparison(book, manifest)
        result = _packet(plan_id, manifest, current_status, journal, remote_accessed=remote_accessed,
                         target_verified=target_verified, source_checked=True, observed_chapters=observed_chapters,
                         comparison_policy=comparison_policy, body_encoding=body_encoding, attempt_ids=attempt_ids)
        result["source_matches_current"] = not current_reasons
        result["local_version_changed"] = bool(current_reasons)
        result["stop_requested"] = stopped
        result["requested_mode"] = "sync_drafts" if sync_drafts else ("reconcile_only" if reconcile_only else "save_draft")
        result["execution_mode"] = getattr(adapter, "execution_mode", "playwright")
        result["backend"] = result["execution_mode"]
        result["remote_evidence_source"] = "host_worker_response" if result["execution_mode"] == "host_assisted_cua" else "browser_adapter"
        return result


@contextmanager
def _workbench_book(root, expected_book_id=None):
    # SQLite connections and Playwright contexts must belong to their own
    # thread. Never pass the HTTP handler's Book connection into the worker.
    book = api.Book(root, read_only=True)
    try:
        if expected_book_id is not None and book.meta("id") != expected_book_id:
            api.fail("platform_draft_book_changed", "作品身份已改变；请重新打开工作台后核对。")
        yield book
    finally:
        book.close()


def _profile_name(value):
    if (not isinstance(value, str) or not value.endswith(".json") or len(value) > 200 or
            value in (".json", "..json") or any(char in value for char in ("/", "\\", ":")) or
            any(ord(char) < 32 for char in value)):
        api.fail("platform_draft_invalid_profile_path", "请选择本书平台配置目录内的 JSON 配置。")
    return value


def _read_bound_profile(directory, name):
    """Read through an existing pin, including during binding on Windows."""
    name = _profile_name(name)
    try:
        file = api._BoundFile(directory, name)
        before = api.publish._bound_stat(file)
        if before.st_nlink != 1 or not 0 < before.st_size <= MAX_PROFILE_BYTES:
            api.fail("platform_draft_invalid_profile_file", "平台配置必须是大小受限、未链接的普通 JSON 文件。")
        with api._bound_reader(file) as stream:
            opened = os.fstat(stream.fileno())
            data = stream.read(MAX_PROFILE_BYTES + 1)
            after_read = os.fstat(stream.fileno())
        after = api.publish._bound_stat(file)
        if (len(data) > MAX_PROFILE_BYTES or not os.path.samestat(before, opened) or
                not os.path.samestat(before, after) or opened.st_nlink != 1 or
                before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns or
                opened.st_mtime_ns != after_read.st_mtime_ns):
            api.fail("platform_draft_profile_changed", "读取时平台配置发生改变；请重新核对。")
        api._verify_bound_directory(directory)
        profile = json.loads(data.decode("utf-8"))
        if not isinstance(profile, dict):
            api.fail("platform_draft_invalid_profile_file", "平台配置必须是 JSON 对象。")
        # Construction validates routes/coverage/save controls without opening
        # a browser or contacting a platform. Placeholder templates fail here.
        api.platform_chrome_bridge.validate_profile(profile)
        return profile, _hash_bytes(data)
    except (UnicodeError, ValueError, TypeError, RecursionError, OSError):
        api.fail("platform_draft_invalid_profile_file", "无法安全读取已保存的平台 JSON 配置。")


def _read_profile(root, name):
    name = _profile_name(name)
    try:
        path = api.safe_path(root, PROFILE_DIRECTORY + "/" + name)
        with api._pinned_directory(path.parent) as directory:
            return _read_bound_profile(directory, name)
    except OSError:
        api.fail("platform_draft_invalid_profile_file", "无法安全读取已保存的平台 JSON 配置。")


def _profile_target(profile):
    """Describe an explicit local route; never infer identity from a book title."""
    target = {"remote_book_id": None, "remote_book_title": None}
    if profile["platform"] == "fanqie":
        try:
            configured = api.platform_browser.fanqie_profile_from_book_url(profile["urls"]["drafts"])
        except api.StoryError:
            return target
        target.update({key: configured[key] for key in target})
    return target


def _profile_catalog(root):
    path = api.safe_path(root, PROFILE_DIRECTORY)
    if not path.exists():
        return []
    try:
        with api._pinned_directory(path) as directory:
            names = os.listdir(directory.path if os.name == "nt" else directory.fd)
            names = sorted(name for name in names if name.endswith(".json"))
            if len(names) > MAX_PROFILES:
                api.fail("platform_draft_profile_limit", "本书平台配置过多；请整理配置目录后重试。")
            api._verify_bound_directory(directory)
    except OSError:
        api.fail("platform_draft_invalid_profile_path", "本书平台配置目录无法安全读取。")
    results = []
    for name in names:
        try:
            profile, fingerprint = _read_profile(root, name)
            results.append({"id": name, "label": name[:-5], "valid": True,
                            "platform": profile["platform"], "sha256": fingerprint,
                            **_profile_target(profile),
                            "browser_channel": profile.get("browser_channel", "chromium"),
                            "execution_mode": profile.get("execution_mode", "playwright")})
        except api.StoryError as error:
            results.append({"id": name, "label": name[:-5], "valid": False,
                            "platform": None, "error": _error_code(error)})
    return results


def _profile_selection_scope(book, target):
    # Page configuration belongs to an exact account/remote target, not a
    # chapter range. New immutable plans for that target inherit this choice.
    return {"book_id": book.meta("id"),
            **{key: target[key] for key in ("platform", "account_id", "remote_book_id")}}


def _profile_selection_read(directory, name):
    """Keep exact displaced bytes even when an explicit rebind repairs bad JSON."""
    file = api._BoundFile(directory, name)
    before = api.publish._bound_stat(file, missing=True)
    if before is None:
        return None, None, True
    if (before.st_nlink != 1 or before.st_size > MAX_EVENT_BYTES
            or (os.name != "nt" and before.st_uid != os.getuid())):
        api.fail("platform_draft_preferences_unsafe", "本地配置选择记录不安全，请保留后核对。")
    with api._bound_reader(file) as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(MAX_EVENT_BYTES + 1)
        after_read = os.fstat(stream.fileno())
    after = api.publish._bound_stat(file)
    if (not os.path.samestat(before, opened) or not os.path.samestat(before, after)
            or opened.st_nlink != 1 or before.st_size != len(raw)
            or before.st_mtime_ns != after.st_mtime_ns or opened.st_mtime_ns != after_read.st_mtime_ns):
        api.fail("platform_draft_preferences_unsafe", "本地配置选择记录在读取时变化，请重新核对。")
    api._verify_bound_directory(directory)
    fingerprint = _hash_bytes(raw)
    try:
        record = json.loads(raw.decode("utf-8"))
        canonical = api.dumps(record).encode("utf-8") == raw
    except (UnicodeError, ValueError, TypeError, RecursionError):
        record, canonical = None, False
    return record, fingerprint, canonical


def _profile_selection(book, target, profiles=None, *, profile_id=None, profile_sha256=None):
    """Remember only a successful local binding; never mutate remote evidence."""
    scope = _profile_selection_scope(book, target)
    path = api.safe_path(book.root, ".story/platform-draft-preferences")
    name = "profile-" + _hash(scope) + ".json"
    saving = profile_id is not None
    try:
        with api._pinned_directory(path, create=saving) as directory:
            if os.name != "nt":
                if os.fstat(directory.fd).st_uid != os.getuid():
                    api.fail("platform_draft_preferences_unsafe", "本地配置选择目录身份不安全。")
                if saving:
                    os.fchmod(directory.fd, 0o700)
            record, previous_hash, canonical = _profile_selection_read(directory, name)
            api._verify_bound_directory(directory)
            if saving:
                # The caller holds the book operation lock and has finished
                # preparing the plan and validating the immutable profile.
                record = {"schema": 1, **scope, "source_plan_id": target["id"],
                          "profile_id": profile_id, "profile_sha256": profile_sha256}
                api.atomic_write(path / name, api.dumps(record),
                                 {previous_hash} if previous_hash else set(),
                                 path / ".backups" / uuid.uuid4().hex / name)
                api._verify_bound_directory(directory)
                return
            if previous_hash is None:
                return None
            fields = {"schema", "source_plan_id", "profile_id", "profile_sha256"} | set(scope)
            if (not canonical or not isinstance(record, dict) or set(record) != fields or type(record["schema"]) is not int
                    or record["schema"] != 1 or any(record[key] != value for key, value in scope.items())
                    or not isinstance(record["source_plan_id"], str)
                    or not re.fullmatch(r"[0-9a-f]{32}", record["source_plan_id"])
                    or not isinstance(record["profile_id"], str)
                    or not isinstance(record["profile_sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", record["profile_sha256"])):
                return {"status": "invalid"}
            try:
                _profile_name(record["profile_id"])
            except api.StoryError:
                return {"status": "invalid"}
            selection = {"profile_id": record["profile_id"], "profile_sha256": record["profile_sha256"]}
            profile = next((row for row in profiles or [] if row["id"] == record["profile_id"]), None)
            if profile is None:
                return {"status": "missing", **selection}
            if (not profile.get("valid") or profile.get("platform") != scope["platform"]
                    or (scope["platform"] == "fanqie" and profile.get("remote_book_id") != scope["remote_book_id"])):
                return {"status": "invalid", **selection}
            if profile.get("sha256") != record["profile_sha256"]:
                return {"status": "changed", **selection}
            return {"status": "selected", **selection}
    except FileNotFoundError:
        if saving:
            raise
        return None


def _browser_runtime():
    helper = getattr(api.platform_browser, "browser_runtime_status", None)
    packet = helper() if helper is not None else {"status": "browser_unverified",
                                                "playwright_installed": None, "chromium_installed": None}
    hosts = []
    for host, label in (("codex", "Codex"), ("qoder-cn", "Qoder CN"), ("workbuddy", "WorkBuddy AI")):
        available, reason = False, None
        try:
            if host == "codex":
                api.chrome_worker._sdk_path()
            elif host == "workbuddy":
                api.chrome_worker._workbuddy_cli_path()
                api.chrome_worker._workbuddy_node_path()
            else:
                api.chrome_worker._native_sdk_path(host)
            available = True
        except Exception as error:
            reason = _error_code(error)
        hosts.append({"id": host, "label": label, "sdk_available": available, "reason": reason,
                      "model_policy": "terra_required" if host == "codex" else "explicit_native_authorization",
                      "browser_ready": False, "platform_verified": False})
    return {**packet, "execution_hosts": hosts}


def _history_summary(result):
    states = [item["state"] for item in result["results"]]
    return {"observation_scope": "recorded_history", "remote_accessed": False,
            "total_chapters": len(states), "matched_chapters": sum(state in SUCCESS for state in states),
            "equivalent_chapters": sum(state in EQUIVALENT for state in states),
            "unknown_chapters": states.count("unknown"),
            "blocked_chapters": sum(state in ("blocked", "conflict", "not_found") for state in states),
            "pending_chapters": states.count("pending"), "history_events_total": result["history_events_total"]}


def _job_packet(job):
    # Registry entries also hold private binding data. Only this fixed set is
    # returned to HTTP clients; no raw exception/profile/content is retained.
    fields = ("id", "book_id", "plan_id", "platform", "account_id", "remote_book_id", "profile_id", "mode",
              "profile_sha256", "execution_mode", "backend", "state", "stage", "done", "created_at", "updated_at", "finished_at",
              "total_chapters", "verified_chapters", "accepted_chapters", "equivalent_chapters", "comparison_policy",
              "body_encoding", "rendered_chapters", "current_chapter", "error", "result", "checked_chapters", "stop_requested",
              "connection_diagnostics", "audit_saved", "audit_started_saved")
    return {"ok": True, **{key: json.loads(json.dumps(job[key])) for key in fields if key in job},
            "scope": "platform_chapter_drafts_only", "submission_performed": False,
            "job_survives_process_exit": False, "durable_attempt_journal": True}


def workbench_activity(root, *, expected_book_id):
    """Minimal in-process activity for the owning editor's shutdown check."""
    root = str(Path(root).expanduser().absolute())
    with _WORKBENCH_LOCK:
        jobs = [{key: job.get(key) for key in ('id', 'stage', 'mode', 'current_chapter')}
                for job in _WORKBENCH_JOBS.values()
                if job['_root'] == root and job['book_id'] == expected_book_id and not job['done']]
        binding = (root, expected_book_id) in _WORKBENCH_BINDINGS
    if api.safe_path(Path(root), ASSISTANT_DIRECTORY).exists():
        with _assistant_directory(root) as directory:
            for record in _assistant_records(directory):
                effective = _assistant_effective(record, root)
                if record["binding"]["book_id"] == expected_book_id and effective.get("execution_active") is True:
                    jobs.append({"id": record["request_id"], "stage": effective["stage"],
                                 "mode": record["binding"]["mode"], "current_chapter": record.get("current_chapter"),
                                 "execution_origin": "assistant_session"})
    return {'supported': True, 'jobs': jobs, 'binding': binding, 'busy': bool(jobs or binding)}


def workbench_catalog(root, offset=0, limit=50, *, expected_book_id=None):
    """Local-only paged plan/profile inventory, including reconnectable jobs."""
    api.integer(offset, "offset")
    api.integer(limit, "limit", 1)
    if limit > 100:
        api.fail("invalid_input", "limit must be at most 100")
    with _workbench_book(root, expected_book_id) as book:
        root = book.root
        book_id = book.meta("id")
        with book.read_snapshot():
            revision = book.meta("revision")
            formal = book.db.execute("SELECT min(chapter),max(chapter),count(*) FROM chapter_state").fetchone()
        packet = api.publish.list_plans(book, offset, limit, budget=4 * 1024 * 1024)
        for plan in packet["results"]:
            plan["draft_history"] = _history_summary(status(book, plan["id"]))
        profiles = _profile_catalog(root)
        for plan in packet["results"]:
            plan["profile_selection"] = _profile_selection(book, plan, profiles)
    with _WORKBENCH_LOCK:
        jobs = [_job_packet(job) for job in _WORKBENCH_JOBS.values()
                if job["_root"] == str(root) and job["book_id"] == book_id]
    return {"ok": True, "book_id": book_id, "plans": packet["results"], "total": packet["total"],
            "revision": revision, "formal_first": formal[0], "formal_last": formal[1], "formal_count": formal[2], "max_batch": 1000,
            "offset": offset, "limit": limit, "next_offset": packet["next_offset"],
            "profiles": profiles, "profile_directory": PROFILE_DIRECTORY, "jobs": jobs,
            "browser_runtime": _browser_runtime(), "remote_accessed": False,
            "platform_acceptance_verified": False, "submission_performed": False}


@contextmanager
def _binding_profile(root, profile):
    """Reuse identical safe configuration, or publish one new private profile.

    A failed preparation removes only a new file whose inode/content we still
    own. A process interruption may leave a harmless reusable profile, but can
    never start a browser, mutate an old binding, or send a chapter.
    """
    raw = (api.dumps(profile) + "\n").encode("utf-8")
    if len(raw) > MAX_PROFILE_BYTES:
        api.fail("platform_draft_profile_limit", "生成的平台配置超过大小限制。")
    fingerprint = _hash_bytes(raw)
    content_hash = _hash(profile)
    path = api.safe_path(root, PROFILE_DIRECTORY)
    with api._pinned_directory(path, create=True) as directory:
        if os.name != "nt":
            os.fchmod(directory.fd, 0o700)
        names = sorted(value for value in os.listdir(directory.path if os.name == "nt" else directory.fd)
                       if value.endswith(".json"))
        api._verify_bound_directory(directory)
        if len(names) > MAX_PROFILES:
            api.fail("platform_draft_profile_limit", "本书平台配置过多，请先整理已有配置。")
        for name in names:
            try:
                saved, saved_fingerprint = _read_bound_profile(directory, name)
            except api.StoryError:
                api._verify_bound_directory(directory)
                continue
            if _hash(saved) != content_hash:
                continue
            # This branch never owns the existing file, even when preparation
            # fails. Keep its actual byte hash for later preview/start checks.
            yield name, saved_fingerprint
            _, current_fingerprint = _read_bound_profile(directory, name)
            if current_fingerprint != saved_fingerprint:
                api.fail("platform_draft_profile_changed", "复用的平台配置发生变化，请刷新后重新核对。")
            return
        if len(names) >= MAX_PROFILES:
            api.fail("platform_draft_profile_limit", "本书平台配置过多，请先整理已有配置。")
        name = "fanqie-" + uuid.uuid4().hex + ".json"
        target = api._BoundFile(directory, name)
        before = api.publish._bound_stat(target, missing=True)
        owned, staged, created = None, None, False
        try:
            if before is not None:
                api.fail("platform_draft_profile_changed", "新配置文件名已存在，未覆盖原配置；请重试。")
            else:
                fd, staged = api._bound_stage(directory, ".binding-profile-")
                owned = os.fstat(fd)
                with api._owned_fdopen(fd, "wb") as stream:
                    stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                if not os.path.samestat(owned, api.publish._bound_stat(staged)):
                    api.fail("platform_draft_profile_changed", "新平台配置暂存文件发生变化，未采用。")
                api._verify_bound_directory(directory)
                _atomic_publish(staged, target)
                created = True
                if os.name != "nt":
                    os.fsync(directory.fd)
                if not os.path.samestat(owned, api.publish._bound_stat(target)) or api._bound_hash(target) != fingerprint:
                    api.fail("platform_draft_profile_changed", "新平台配置在保存期间发生变化，未采用。")
                _read_bound_profile(directory, name)
            yield name, fingerprint
        except BaseException:
            if created:
                current = api.publish._bound_stat(target, missing=True)
                if current is not None and os.path.samestat(owned, current) and api._bound_hash(target) == fingerprint:
                    api._bound_unlink(target)
                    if os.name != "nt":
                        os.fsync(directory.fd)
            raise
        finally:
            if staged is not None:
                current = api.publish._bound_stat(staged, missing=True)
                if current is not None and os.path.samestat(owned, current):
                    api._bound_unlink(staged)


def workbench_bind(root, account_id, book_management_url, start, end, *, expected_book_id, expected_revision):
    """Save user-declared Fanqie target and prepare qualified formal snapshots.

    Configuration is local only. Browser identity checks and the explicit
    frozen-batch authorization remain necessary before any remote operation.
    """
    if not isinstance(account_id, str) or len(account_id) > 200 or not re.fullmatch(r"[0-9]{1,30}", account_id.strip()):
        api.fail("platform_draft_invalid_account", "请填写番茄账号页显示的数字账号ID，不能填写昵称或登录凭据。")
    account_id = account_id.strip()
    api.integer(start, "start", 1); api.integer(end, "end", 1)
    api.integer(expected_revision, "expected_revision")
    if start > end or end > 1000000 or end - start + 1 > 1000:
        api.fail("platform_draft_invalid_range", "请选择连续的正式章节范围，每批最多1000章。")
    generated = api.platform_browser.fanqie_profile_from_book_url(book_management_url)
    profile = generated["profile"]
    api.platform_chrome_bridge.validate_profile(profile)
    request = {"platform": "fanqie", "account_id": account_id, "remote_book_id": generated["remote_book_id"],
               "chapters": list(range(start, end + 1)), "mode": "draft"}
    target_hash = _hash(_target(request))
    with _workbench_book(root, expected_book_id) as book:
        root, book_id = book.root, book.meta("id")
        key = (str(root), book_id)
        with _WORKBENCH_LOCK:
            if (any(not job["done"] and (job["_root"] == str(root) or job["_target_sha256"] == target_hash)
                    for job in _WORKBENCH_JOBS.values()) or
                    any(other == key or value == target_hash for other, value in _WORKBENCH_BINDINGS.items())):
                api.fail("platform_draft_busy", "当前作品或目标已有任务进行中，请等待任务结束后再配置。")
            _WORKBENCH_BINDINGS[key] = target_hash
        try:
            with api.storage.operation_lock(book), book.read_snapshot():
                api.publish._book_kind(book)
                if book.meta("revision") != expected_revision:
                    api.fail("stale_revision", "本地正式稿状态已变化，请刷新后重新选择章节范围。")
                # Use the exact existing preparation gates before publishing a
                # profile, then let prepare recheck them under the same lock.
                api.publish._health(book)
                for chapter in request["chapters"]:
                    api.publish._source(book, chapter, require_review=False)
                with _binding_profile(root, profile) as (profile_id, profile_sha256):
                    plan = api.publish.prepare(book, request, expected_revision, summary=True, workbench_formal_draft=True)
                _profile_selection(book, plan, profile_id=profile_id, profile_sha256=profile_sha256)
                return {"ok": True, "book_id": book_id, "revision": expected_revision,
                        "platform": "fanqie", "account_id": account_id,
                        "remote_book_id": generated["remote_book_id"], "remote_book_title": generated["remote_book_title"],
                        "profile_id": profile_id, "profile_sha256": profile_sha256,
                        "plan_id": plan["id"], "plan": plan, "plan_reused": plan.get("idempotent", False),
                        "scope": "local_binding_and_frozen_plan_only", "execution_mode": "host_assisted_cua",
                        "remote_accessed": False, "platform_acceptance_verified": False, "submission_performed": False}
        except OSError:
            api.fail("platform_draft_binding_failed", "无法安全保存本地平台配置或清单；请刷新核对，未访问平台。")
        finally:
            with _WORKBENCH_LOCK:
                _WORKBENCH_BINDINGS.pop(key, None)


def workbench_formal_state(root, *, expected_book_id):
    """Observe current formal chapter bounds without preparing a publishing plan."""
    with _workbench_book(root, expected_book_id) as book, book.read_snapshot():
        api.publish._book_kind(book)
        formal = book.db.execute("SELECT min(chapter),max(chapter),count(*) FROM chapter_state").fetchone()
        return {"ok": True, "book_id": book.meta("id"), "revision": book.meta("revision"),
                "formal_first": formal[0], "formal_last": formal[1], "formal_count": formal[2],
                "max_batch": 1000, "scope": "local_formal_state_only",
                "remote_accessed": False, "submission_performed": False}


def workbench_refresh_plan(root, plan_id, profile_id, *, expected_book_id, expected_revision,
                           expected_profile_sha256, start=None, end=None):
    """Prepare current formal sources for the selected immutable plan's target.

    Old plans and journals remain available for reconciliation. Reusing their
    target is not an assertion that their old sources or remote drafts match.
    """
    api.integer(expected_revision, "expected_revision")
    api.publish._identity(plan_id, "plan id")
    if not re.fullmatch(r"[0-9a-f]{32}", plan_id):
        api.fail("invalid_input", "请选择有效的历史正式稿批次。")
    profile_id = _profile_name(profile_id)
    if (not isinstance(expected_profile_sha256, str) or
            not re.fullmatch(r"[0-9a-f]{64}", expected_profile_sha256)):
        api.fail("platform_draft_profile_changed", "页面配置摘要无效，请刷新后重新核对。")
    if start is not None:
        api.integer(start, "start", 1)
    if end is not None:
        api.integer(end, "end", 1)
    with _workbench_book(root, expected_book_id) as book:
        root, book_id = book.root, book.meta("id")
        # Manifest contents are immutable. Read the selected target before
        # taking the shared job/binding gate, without refreshing its status.
        with api.publish._ledger(book) as db:
            manifest = api.publish._validated_manifest(api.publish._plan(db, plan_id), book_id)
        if manifest["platform"] != "fanqie":
            api.fail("platform_draft_refresh_unsupported", "当前仅支持更新番茄正式稿批次；其他平台可继续核对原历史清单。")
        target = _target(manifest)
        target_hash, key = _hash(target), (str(root), book_id)
        with _WORKBENCH_LOCK:
            if (any(not job["done"] and (job["_root"] == str(root) or job["_target_sha256"] == target_hash)
                    for job in _WORKBENCH_JOBS.values()) or
                    any(other == key or value == target_hash for other, value in _WORKBENCH_BINDINGS.items())):
                api.fail("platform_draft_busy", "当前作品或目标已有任务进行中，请等待任务结束后再更新批次。")
            _WORKBENCH_BINDINGS[key] = target_hash
        try:
            with api.storage.operation_lock(book), book.read_snapshot():
                api.publish._book_kind(book)
                if book.meta("revision") != expected_revision:
                    api.fail("stale_revision", "本地正式稿已变化，请刷新章节范围后重新生成批次。")
                last = book.db.execute("SELECT max(chapter) FROM chapter_state").fetchone()[0]
                first = min(item["chapter"] for item in manifest["chapters"]) if start is None else start
                last = last if end is None else end
                if last is None or first > last or last > 1000000 or last - first + 1 > 1000:
                    api.fail("platform_draft_invalid_range", "请选择连续的正式章节范围，每批最多1000章。")
                profile, profile_sha256 = _read_profile(root, profile_id)
                if profile_sha256 != expected_profile_sha256:
                    api.fail("platform_draft_profile_changed", "页面配置已经变化，请刷新后重新核对。")
                if profile["platform"] != target["platform"]:
                    api.fail("platform_draft_profile_mismatch", "所选页面配置与原批次的平台不一致。")
                configured = api.platform_browser.fanqie_profile_from_book_url(profile["urls"]["drafts"])
                if configured["remote_book_id"] != target["remote_book_id"]:
                    api.fail("platform_draft_profile_mismatch",
                             "页面配置的番茄作品 ID（" + configured["remote_book_id"] +
                             "）与本批次目标（" + target["remote_book_id"] +
                             "）不一致；原批次账号和目标未更改。")
                request = {**target, "chapters": list(range(first, last + 1)), "mode": "draft"}
                # prepare checks current reviewed heads and exports under this
                # same operation lock/snapshot; it never mutates the old plan.
                plan = api.publish.prepare(book, request, expected_revision, summary=True, workbench_formal_draft=True)
                _, after_sha256 = _read_profile(root, profile_id)
                if after_sha256 != expected_profile_sha256:
                    api.fail("platform_draft_profile_changed", "生成期间页面配置发生变化；请刷新核对本地批次，尚未访问平台。")
                return {"ok": True, "book_id": book_id, "revision": expected_revision,
                        **target, "remote_book_title": configured["remote_book_title"],
                        "base_plan_id": plan_id, "plan_id": plan["id"], "plan": plan,
                        "profile_id": profile_id, "profile_sha256": profile_sha256,
                        "plan_reused": plan.get("idempotent", False), "chapters": plan["chapters"],
                        "start": first, "end": last, "scope": "local_formal_refresh_only",
                        "remote_accessed": False, "platform_acceptance_verified": False,
                        "submission_performed": False}
        except OSError:
            api.fail("platform_draft_refresh_failed", "无法安全生成本地正式稿批次；请刷新核对，尚未访问平台。")
        finally:
            with _WORKBENCH_LOCK:
                _WORKBENCH_BINDINGS.pop(key, None)


def _preflight(root, plan_id, profile_id, expected_book_id=None, reconcile_only=False, comparison_policy="strict",
               body_encoding="verbatim", sync_drafts=False, inspect_only=False):
    if type(inspect_only) is not bool or (inspect_only and (reconcile_only or sync_drafts)):
        api.fail("invalid_input", "请选择检查差异、同步或只读核对中的一种操作。")
    if type(reconcile_only) is not bool:
        api.fail("invalid_input", "reconcile_only must be a boolean")
    with _workbench_book(root, expected_book_id) as book:
        result = run_plan(book, plan_id, reconcile_only=reconcile_only, comparison_policy=comparison_policy,
                          body_encoding=body_encoding, sync_drafts=sync_drafts)
        with _checked_plan(book, plan_id, check_source=not reconcile_only) as (manifest, fingerprint, _):
            binding = {"book_id": book.meta("id"), "plan_id": plan_id,
                       "manifest_sha256": fingerprint, "target_sha256": _hash(_target(manifest)),
                       "mode": "inspect_drafts" if inspect_only else "sync_drafts" if sync_drafts else ("reconcile_only" if reconcile_only else "save_draft"), "comparison_policy": comparison_policy,
                       "body_encoding": body_encoding}
        root = book.root
    profile = None
    fingerprint = None
    if profile_id is not None:
        profile, fingerprint = _read_profile(root, profile_id)
        if profile["platform"] != result["platform"]:
            api.fail("platform_draft_profile_mismatch", "所选配置的平台与冻结清单不一致。")
        configured = _profile_target(profile)
        if configured["remote_book_id"] is not None and configured["remote_book_id"] != result["remote_book_id"]:
            api.fail("platform_draft_profile_mismatch",
                     "页面配置的番茄作品 ID（" + configured["remote_book_id"] +
                     "）与本批次目标（" + result["remote_book_id"] +
                     "）不一致；请选择与本批次目标匹配的配置。")
        if sync_drafts and (profile.get("execution_mode") != "host_assisted_cua" or not profile.get("readback")):
            api.fail("platform_draft_update_unsupported", "更新已有草稿需要番茄 Chrome 会话和独立预览回读配置。")
    binding.update(profile_id=profile_id, profile_sha256=fingerprint)
    return root, result, profile, binding


def _preview_token(binding, expires=None):
    expires = int(time.time()) + 600 if expires is None else expires
    signed = api.dumps({"binding": binding, "expires": expires}).encode("utf-8")
    return str(expires) + "." + hmac.new(_PREVIEW_SECRET, signed, hashlib.sha256).hexdigest()


def _runtime_blockers(profile, runtime):
    blockers = []
    if profile is not None and profile.get("execution_mode") == "host_assisted_cua":
        if runtime.get("worker", {}).get("ready") is not True:
            blockers.append("platform_chrome_worker_unavailable")
        return blockers
    if runtime.get("playwright_installed") is False:
        blockers.append("platform_browser_dependency_missing")
    if (profile is not None and profile.get("browser_channel", "chromium") == "chrome" and
            runtime.get("chrome_executable_present") is False):
        blockers.append("platform_browser_chrome_unavailable")
    return blockers


def _host_binding(binding, preview):
    return {**{key: value for key, value in binding.items() if key != "mode"},
            "target": {key: preview[key] for key in ("platform", "account_id", "remote_book_id")}}


def _execution_runtime(root, preview, profile, binding):
    if profile is not None and profile.get("execution_mode") == "host_assisted_cua":
        worker = api.platform_chrome_bridge.worker_status(root, _host_binding(binding, preview))
        if worker.get("ready") is True:
            # A bridge lease proves browser health, not the executing model.
            # Only the owning controller can attest its verified Terra session;
            # old/manual leases cannot bypass the automatic launcher's check.
            launcher = getattr(api, "chrome_worker", None)
            controlled = launcher.status(root, binding["plan_id"], binding["profile_id"],
                expected_book_id=binding["book_id"], comparison_policy=binding["comparison_policy"],
                body_encoding=binding["body_encoding"]) if launcher is not None else {}
            verified = (controlled.get("ready") is True and controlled.get("owned") is True and
                        controlled.get("model_verified") is True and
                        controlled.get("requested_model") == controlled.get("resolved_model") == "gpt-6.1-terra")
            worker = {**worker, "ready": verified, "model_verified": verified,
                      "error": None if verified else "platform_chrome_worker_model_unverified"}
        return {"execution_mode": "host_assisted_cua", "playwright_required": False,
                "worker": worker}
    return _browser_runtime()


def _automatic_connection(profile, blockers):
    """Advertise a local launcher separately from a healthy browser worker."""
    if (profile is None or profile.get("execution_mode") != "host_assisted_cua" or
            blockers != ["platform_chrome_worker_unavailable"]):
        return {"available": False, "uses_codex_quota": True}
    if profile.get("platform") != "fanqie":
        return {"available": False, "uses_codex_quota": True,
                "reason": "platform_chrome_worker_unsupported_platform"}
    launcher = getattr(api, "chrome_worker", None)
    if launcher is None:
        return {"available": False, "uses_codex_quota": True,
                "reason": "platform_chrome_connector_unavailable"}
    result = launcher.availability()
    return {"available": result.get("available") is True, "uses_codex_quota": True,
            **({"retry_blocked": True, "cause": result["cause"],
                "failure_phase": result["failure_phase"]}
               if result.get("retry_blocked") is True and result.get("available") is False and
                  result.get("reason") == "platform_chrome_worker_connection_suspended" and
                  result.get("cause") in ("platform_chrome_worker_browser_transport_closed",
                                          "platform_chrome_worker_host_access_denied") and
                  result.get("failure_phase") in ("sdk_setup", "browser_inventory") else {}),
            **({"reason": result["reason"]} if isinstance(result.get("reason"), str) else {})}


def workbench_preview(root, plan_id, profile_id=None, *, expected_book_id=None, reconcile_only=False,
                      comparison_policy="strict", body_encoding="verbatim", sync_drafts=False, inspect_only=False):
    """Check frozen local sources and profile eligibility; never opens a browser."""
    root, preview, profile, binding = _preflight(root, plan_id, profile_id, expected_book_id, reconcile_only,
                                               comparison_policy, body_encoding, sync_drafts, inspect_only)
    runtime = _execution_runtime(root, preview, profile, binding)
    blockers = []
    if profile_id is None:
        blockers.append("profile_required")
    blockers.extend(_runtime_blockers(profile, runtime))
    connection = _automatic_connection(profile, blockers)
    return {"ok": True, **binding, "preview": preview, "browser_runtime": runtime,
            "browser_channel": profile.get("browser_channel", "chromium") if profile is not None else None,
            "execution_mode": profile.get("execution_mode", "playwright") if profile is not None else None,
            "can_start": not blockers, "blockers": blockers,
            "preview_token": _preview_token(binding) if not blockers else None,
            "connection_ready": not blockers,
            "can_connect": connection["available"], "automatic_connection": connection,
            "connect_token": _preview_token(binding) if connection["available"] else None,
            "authorization_scope": "one_frozen_batch_drafts_only", "remote_accessed": False,
            "platform_acceptance_verified": False, "submission_performed": False}


def _update_job(job_id, **changes):
    with _WORKBENCH_LOCK:
        job = _WORKBENCH_JOBS[job_id]
        job.update(changes, updated_at=_now())


def _job_progress(job_id, event):
    if not isinstance(event, dict):
        return
    stage = event.get("stage")
    if stage not in {"waiting_login", "waiting_host", "target_verified", "listing_drafts", "saving_draft", "reading_back",
                     "chapter_started", "chapter_verified", "inspection_started", "inspection_checked"}:
        return
    changes = {"stage": stage, "state": "waiting_login" if stage == "waiting_login" else "running"}
    if stage in ("inspection_started", "inspection_checked"):
        changes.update(current_chapter=event.get("chapter"), checked_chapters=event.get("checked_chapters", 0))
    # Only local journal completion events can advance the verified count.
    if stage in ("chapter_started", "chapter_verified"):
        if type(event.get("chapter")) is int:
            changes["current_chapter"] = event["chapter"]
        if stage == "chapter_verified" and type(event.get("verified_chapters")) is int:
            changes["verified_chapters"] = event["verified_chapters"]
        for name in ("accepted_chapters", "equivalent_chapters", "rendered_chapters"):
            if type(event.get(name)) is int:
                changes[name] = event[name]
        row = event.get("chapter_result")
        if isinstance(row, dict) and row.get("chapter") == event.get("chapter"):
            with _WORKBENCH_LOCK:
                result = _WORKBENCH_JOBS[job_id].get("result")
                if result and any(item["chapter"] == row["chapter"] for item in result["results"]):
                    changes["result"] = {**result, "results": [dict(row) if item["chapter"] == row["chapter"] else item
                        for item in result["results"]]}
    _update_job(job_id, **changes)


def _safe_job_result(result):
    fields = ("id", "platform", "account_id", "remote_book_id", "plan_status", "remote_accessed",
              "target_verified", "source_matches_current", "local_version_changed", "all_chapters_verified_this_run",
              "historical_all_title_body_match", "observation_scope", "results", "history_events_total",
              "execution_mode", "backend", "remote_evidence_source", "comparison_policy",
              "all_chapters_accepted_this_run", "exact_verified_chapters", "equivalent_chapters", "accepted_chapters",
              "body_encoding", "rendered_chapters", "created_chapters", "updated_chapters", "unchanged_chapters",
              "inspection", "inspection_complete", "checked_chapters", "total_chapters", "changes", "change_counts",
              "can_execute", "stop_requested", "recovery_plans")
    return {key: result[key] for key in fields if key in result}


def _write_job_audit(job, suffix, record):
    job_id = job.get("id")
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
        return False
    runtime_sha = getattr(getattr(api, "workbench", None), "_loaded_runtime_sha256", None)
    if isinstance(runtime_sha, str) and re.fullmatch(r"[0-9a-f]{64}", runtime_sha):
        record["runtime_sha256"] = runtime_sha
    try:
        path = api.safe_path(Path(job["_root"]), ".story/platform-draft-audit")
        with api._pinned_directory(path, create=True) as directory:
            _write_record(directory, job_id + suffix + ".json", record, max_bytes=256 * 1024)
        return True
    except (OSError, api.StoryError, ValueError, TypeError):
        return False


def _save_job_start_audit(job):
    record = {"schema": 1, "id": job.get("id"), "created_at": job.get("created_at"),
              "mode": job.get("mode"), "state": "started", "runtime_version": getattr(api, "VERSION", None),
              "total_chapters": job.get("total_chapters", 0)}
    return _write_job_audit(job, ".started", record)


def _audit_chapter(row):
    if not isinstance(row, dict) or type(row.get("chapter")) is not int or not 1 <= row["chapter"] <= 1000000:
        return None
    result = {"chapter": row["chapter"]}
    if row.get("action") in ("create", "update", "unchanged", "blocked"):
        result["action"] = row["action"]
    if isinstance(row.get("state"), str) and row["state"] in EVENTS | {"pending"}:
        result["state"] = row["state"]
    reason = row.get("reason")
    if isinstance(reason, str) and re.fullmatch(r"[a-z][a-z0-9_]{1,100}", reason):
        result["reason"] = reason
    return result


def workbench_audit_lookup(root, record_id, *, expected_book_id):
    """Read a single bounded incident record without trusting local file text."""
    if not isinstance(record_id, str) or not re.fullmatch(r"[0-9a-f]{32}", record_id):
        api.fail("platform_draft_audit_invalid_id", "请输入 32 位记录编号。")
    with _workbench_book(root, expected_book_id) as book:
        path = api.safe_path(book.root, ".story/platform-draft-audit")
        try:
            with api._pinned_directory(path) as directory:
                records = []
                for suffix, kind in ((".started", "started"), ("", "finished"), (".api", "api_error")):
                    name = record_id + suffix + ".json"
                    if api.publish._bound_stat(api._BoundFile(directory, name), missing=True) is None:
                        continue
                    raw = _read_record(directory, name, max_bytes=256 * 1024)
                    if not isinstance(raw, dict) or raw.get("schema") != 1 or raw.get("id") != record_id:
                        api.fail("platform_draft_audit_invalid", "本地任务记录格式无效，请保留原记录核对。")
                    value = {"kind": kind}
                    for field in ("created_at", "finished_at", "at"):
                        item = raw.get(field)
                        if isinstance(item, str) and re.fullmatch(r"[0-9TZ:+.\-]{10,40}", item):
                            value[field] = item
                    runtime_sha = raw.get("runtime_sha256")
                    if isinstance(runtime_sha, str) and re.fullmatch(r"[0-9a-f]{64}", runtime_sha):
                        value["runtime_sha256"] = runtime_sha
                    for field in ("mode", "state", "error"):
                        item = raw.get(field)
                        if isinstance(item, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,100}", item):
                            value[field] = item
                    operation = raw.get("operation")
                    if isinstance(operation, str) and re.fullmatch(r"[a-z][a-z-]{0,60}", operation):
                        value["operation"] = operation
                    error_name = raw.get("error_name")
                    if error_name in ("StoryError", "ValueError", "UnicodeError", "OSError", "KeyError", "TypeError", "Exception"):
                        value["error_name"] = error_name
                    for field in ("total_chapters", "checked_chapters", "verified_chapters"):
                        item = raw.get(field)
                        if type(item) is int and 0 <= item <= 1000000:
                            value[field] = item
                    counts = raw.get("change_counts")
                    if isinstance(counts, dict):
                        value["change_counts"] = {key: counts[key] for key in ("create", "update", "unchanged", "blocked")
                                                  if type(counts.get(key)) is int and 0 <= counts[key] <= 1000000}
                    chapters = raw.get("chapters")
                    if isinstance(chapters, list) and len(chapters) <= 1000:
                        value["chapters"] = [_audit_chapter(row) for row in chapters if _audit_chapter(row) is not None]
                    if type(raw.get("remote_accessed")) is bool:
                        value["remote_accessed"] = raw["remote_accessed"]
                    diag = raw.get("connection_diagnostics")
                    if isinstance(diag, dict):
                        value["connection_diagnostics"] = _connection_diagnostics(
                            {**diag, "owned": diag.get("ready") is True, "error": diag.get("reason")})
                    records.append(value)
                api._verify_bound_directory(directory)
        except FileNotFoundError:
            records = []
    if not records:
        api.fail("platform_draft_audit_missing", "本书没有找到该记录编号。")
    return {"ok": True, "id": record_id, "records": records, "remote_accessed": False}


def _save_job_audit(job, changes, result):
    """Write one immutable, content-free outcome per finished workbench job."""
    job_id = job.get("id")
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
        return False
    error = changes.get("error")
    if error is not None and (not isinstance(error, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,100}", error)):
        error = "platform_draft_unclassified_error"
    diagnostic = job.get("connection_diagnostics")
    diagnostic = diagnostic if isinstance(diagnostic, dict) else {}
    diagnostic_source = {**diagnostic, "owned": diagnostic.get("ready") is True,
                         "error": diagnostic.get("reason")}
    record = {"schema": 1, "id": job_id, "created_at": job.get("created_at"),
              "finished_at": changes["finished_at"], "mode": job.get("mode"), "state": changes["state"],
              "error": error, "runtime_version": getattr(api, "VERSION", None),
              "total_chapters": job.get("total_chapters", 0),
              "checked_chapters": changes.get("checked_chapters", job.get("checked_chapters", 0)),
              "verified_chapters": changes.get("verified_chapters", 0),
              "remote_accessed": result.get("remote_accessed") if isinstance(result, dict) and
                                 type(result.get("remote_accessed")) is bool else None}
    if diagnostic:
        record["connection_diagnostics"] = _connection_diagnostics(diagnostic_source)
    counts = result.get("change_counts") if isinstance(result, dict) else None
    if isinstance(counts, dict):
        record["change_counts"] = {key: counts[key] for key in ("create", "update", "unchanged", "blocked")
                                   if type(counts.get(key)) is int and 0 <= counts[key] <= 1000000}
    if isinstance(result, dict):
        entries = result.get("changes") if result.get("inspection") else result.get("results")
        if isinstance(entries, list) and len(entries) <= 1000:
            record["chapters"] = [_audit_chapter(row) for row in entries if _audit_chapter(row) is not None]
    return _write_job_audit(job, "", record)


def _finish_job(job_id, result=None, error=None):
    states = [item["state"] for item in result["results"] if item.get("recorded_this_run") is True or item.get("observed_this_run") is True] if result else []
    verified = sum(item.get("title_body_verified") is True for item in result["results"]) if result else 0
    if error is not None:
        with _WORKBENCH_LOCK:
            verified = max(verified, _WORKBENCH_JOBS[job_id]["verified_chapters"])
    if result and result.get("inspection"):
        state = "inspection_ready" if result.get("can_execute") and error is None else "inspection_blocked"
    elif result and result.get("all_chapters_verified_this_run"):
        state = "succeeded"
    elif result and result.get("all_chapters_accepted_this_run"):
        state = "succeeded_equivalent"
    elif "unknown" in states:
        state = "unknown"
    elif verified or (result and result.get("accepted_chapters", 0)):
        state = "partial_failure"
    else:
        state = "failed"
    if (result and result.get("stop_requested")) or error == "platform_draft_stop_requested":
        state = "stopped"
    changes = {"state": state, "stage": "finished", "done": True, "finished_at": _now(), "error": error}
    if result is not None:
        changes["result"] = _safe_job_result(result)
        if result.get("inspection"):
            changes.update(checked_chapters=result.get("checked_chapters", 0),
                           _expected_remote=result.get("_expected_remote"), _manifest_sha256=result.get("_manifest_sha256"),
                           _inspection_expires=time.time() + 600)
    changes["verified_chapters"] = verified
    if result is not None:
        changes["accepted_chapters"] = result.get("accepted_chapters", verified)
        changes["equivalent_chapters"] = result.get("equivalent_chapters", 0)
        changes["rendered_chapters"] = result.get("rendered_chapters", 0)
    with _WORKBENCH_LOCK:
        job = dict(_WORKBENCH_JOBS[job_id])
    try:
        changes["audit_saved"] = _save_job_audit(job, changes, result)
    except Exception:
        # Audit failures must never hide the platform outcome or leave a job
        # indefinitely running after a completed or uncertain operation.
        changes["audit_saved"] = False
    _update_job(job_id, **changes)


_CONNECTION_FALLBACKS = frozenset({"platform_chrome_worker_unavailable", "platform_chrome_worker_health_expired",
    "platform_chrome_health_expired", "platform_chrome_request_timeout", "platform_chrome_connection_failed"})
_SPECIFIC_WORKER_STOPS = frozenset({"platform_chrome_worker_target_tab_changed", "platform_chrome_worker_target_tab_closed",
    "platform_chrome_worker_existing_tab_required", "platform_chrome_worker_origin_permission_required",
    "platform_chrome_worker_host_access_denied",
    "platform_chrome_worker_browser_unattached", "platform_chrome_worker_browser_changed",
    "platform_chrome_worker_turn_failed", "platform_chrome_worker_runtime_unavailable",
    "platform_chrome_worker_model_unverified", "platform_chrome_worker_model_mismatch",
    "platform_chrome_worker_model_rerouted", "platform_chrome_worker_model_unavailable",
    "platform_chrome_worker_browser_transport_closed"})


def _record_worker_stop(root, binding, result, cause):
    """Refine only this run's unchanged connection failure after owned cleanup.

    No platform request or retry occurs. Keep unknown as unknown, and never
    replace a conflict, another attempt, or evidence written by a later run.
    """
    if not result or cause not in _SPECIFIC_WORKER_STOPS:
        return result
    rows = [row for row in result["results"] if (row.get("recorded_this_run") is True or row.get("observed_this_run") is True) and
            row["state"] in {"blocked", "unknown"} and row.get("reason") in _CONNECTION_FALLBACKS]
    if not rows:
        return result
    with _workbench_book(root, binding["book_id"]) as book:
        with _checked_plan(book, binding["plan_id"], check_source=False) as (manifest, fingerprint, _):
            with _target_lock(manifest), _journal(book, binding["plan_id"], manifest, fingerprint) as journal:
                refined = {}
                for row in rows:
                    latest = journal.latest(row["chapter"])
                    if (latest and latest["at"] == row.get("last_observed_at") and latest["event"] == row["state"] and
                            latest["reason"] == row["reason"] and latest["observed_sha256"] is None):
                        record = journal.append(row["chapter"], latest["event"], attempt_id=latest["attempt_id"],
                            remote_id=latest["remote_id"], reason=cause,
                            comparison_policy=latest.get("comparison_policy", "strict"),
                            body_encoding=latest.get("body_encoding", "verbatim"))
                        refined[row["chapter"]] = {**row, "reason": cause, "last_observed_at": record["at"]}
                return {**result, "results": [refined.get(row["chapter"], row) for row in result["results"]],
                        "history_events_total": len(journal.events)}


_CONNECTION_ERROR_KINDS = frozenset({
    "contextWindowExceeded", "sessionBudgetExceeded", "usageLimitExceeded", "rateLimitExceeded",
    "flexUnavailable", "serverOverloaded", "modelNotFound", "cyberPolicy", "misalignmentPolicyViolation",
    "tooManyDenials", "internalServerError", "unauthorized", "badRequest", "threadRollbackFailed",
    "sandboxError", "other", "httpConnectionFailed", "responseStreamConnectionFailed",
    "responseStreamDisconnected", "responseTooManyFailedAttempts", "activeTurnNotSteerable"})
_CONNECTION_DIAGNOSTIC_REASONS = _SPECIFIC_WORKER_STOPS | _CONNECTION_FALLBACKS | frozenset(
    "platform_chrome_worker_" + suffix for suffix in (
        "model_unverified", "model_mismatch", "model_rerouted", "model_unavailable",
        "tool_not_started", "tool_discovery_failed", "not_ready", "rpc_error", "rpc_timeout",
        "connect_timeout", "browser_wait_timeout", "tool_failed", "tool_approval_required", "unexpected_tool", "connection_suspended"))
_CONNECTION_WORKER_PHASES = frozenset({"config_validated", "sdk_setup", "browser_inventory", "browser_documentation",
    "waiting_for_chrome", "existing_tab_probe", "bridge_attach", "bridge_health", "queue_wait", "queue_operation", "cleanup"})
_CONNECTION_ERROR_NAMES = frozenset({"Error", "ReferenceError", "TypeError", "RangeError", "SyntaxError", "AggregateError",
    "URIError", "EvalError", "AbortError", "TimeoutError"})
_CONNECTION_ERROR_MARKERS = frozenset({"none", "browser_unattached", "missing_turn_metadata", "missing_browser_service",
    "missing_runtime_global", "origin_permission_required", "native_pipe_closed", "local_browser_access_denied",
    "tool_approval_unavailable"})


def _connection_diagnostics(state):
    """Expose bounded connection facts, never provider prose or credentials.

    These are startup observations, not evidence of platform identity or saved
    chapters. In particular model verification never establishes browser health.
    """
    packet = {"stage": "connecting_chrome"}
    model = "gpt-6.1-terra"
    packet["requested_model"] = model if state.get("requested_model") == model else None
    packet["resolved_model"] = model if state.get("resolved_model") == model else None
    packet["model_verified"] = (state.get("model_verified") is True and
                                packet["requested_model"] == packet["resolved_model"] == model)
    packet["ready"] = state.get("ready") is True and state.get("owned") is True and packet["model_verified"]
    if state.get("retry_blocked") is True and not packet["ready"]:
        packet["retry_blocked"] = True
    for key, allowed in (("reason", _CONNECTION_DIAGNOSTIC_REASONS), ("error_kind", _CONNECTION_ERROR_KINDS)):
        value = state.get("error" if key == "reason" else key)
        if isinstance(value, str) and value in allowed:
            packet[key] = value
    for key, allowed in (("failure_phase", _CONNECTION_WORKER_PHASES),
                         ("worker_phase", _CONNECTION_WORKER_PHASES),
                         ("error_name", _CONNECTION_ERROR_NAMES),
                         ("error_marker", _CONNECTION_ERROR_MARKERS)):
        value = state.get(key)
        if isinstance(value, str) and value in allowed:
            packet[key] = value
    for key, lower, upper in (("rpc_error_code", -(2 ** 31), 2 ** 31 - 1), ("http_status", 100, 599)):
        value = state.get(key)
        if type(value) is int and lower <= value <= upper:
            packet[key] = value
    fingerprint = state.get("tool_error_fingerprint")
    if isinstance(fingerprint, str) and re.fullmatch(r"[0-9a-f]{16}", fingerprint):
        packet["tool_error_fingerprint"] = fingerprint
    diagnostic_id = state.get("diagnostic_id")
    if isinstance(diagnostic_id, str) and re.fullmatch(r"[0-9a-f]{32}", diagnostic_id):
        packet["diagnostic_id"] = diagnostic_id
    for key, allowed in (("tool_error_channel", {"error_object", "error_result", "tool_status"}),
                         ("tool_error_status", {"failed", "completed", "inProgress", "other"})):
        if isinstance(state.get(key), str) and state[key] in allowed:
            packet[key] = state[key]
    error_code = state.get("tool_error_rpc_code")
    if type(error_code) is int and -(2**31) <= error_code < 2**31:
        packet["tool_error_rpc_code"] = error_code
    hints = state.get("tool_error_hints")
    allowed_hints = {"timeout", "permission", "missing_file", "missing_tool", "connection", "session", "sandbox", "process", "invalid_request", "resource_limit"}
    if isinstance(hints, list) and len(hints) <= len(allowed_hints) and all(isinstance(hint, str) and hint in allowed_hints for hint in hints):
        packet["tool_error_hints"] = list(hints)
    allowed_terms = set((
        "access permission authorization authentication disabled denied invalid missing required unavailable "
        "failed failure error internal unexpected unknown timeout timed closed shutdown connection transport broken "
        "socket pipe process spawn terminated exited file path module sandbox work settings limit blocked cancelled "
        "refused reset configured support cannot could not bad rejected output input request response empty read "
        "write resource retry javascript script syntax exception typeerror referenceerror node repl node_repl "
        "tool function server mcp call execution execute running start initialized initialization thread turn session "
        "context metadata no such directory enoent eacces eperm network host use operation invoking reported message"
    ).split())
    terms = state.get("tool_error_terms")
    if isinstance(terms, list) and len(terms) <= len(allowed_terms) and all(isinstance(term, str) and term in allowed_terms for term in terms):
        packet["tool_error_terms"] = list(terms)
    return packet


def _workbench_worker(job_id, binding, timeout_ms, login_timeout_ms):
    with _WORKBENCH_LOCK:
        root = _WORKBENCH_JOBS[job_id]["_root"]
        allow_auto_connect = _WORKBENCH_JOBS[job_id].get("_auto_connect") is True
        expected_remote = _WORKBENCH_JOBS[job_id].get("_expected_remote")
    def stopped():
        with _WORKBENCH_LOCK:
            return _WORKBENCH_JOBS[job_id].get("stop_requested") is True
    def check_stop():
        if stopped():
            api.fail("platform_draft_stop_requested", "任务已在安全位置停止。")
    _update_job(job_id, state="running", stage="local_preflight")
    connected_automatically = False
    owned_worker_id = None
    result = None
    failure = None
    try:
        # Recheck after thread dispatch, before constructing/opening a browser.
        reconcile_only = binding["mode"] == "reconcile_only"
        sync_drafts = binding["mode"] == "sync_drafts"
        inspect_only = binding["mode"] == "inspect_drafts"
        check_stop()
        policy = binding["comparison_policy"]
        encoding = binding["body_encoding"]
        _, preview, profile, current = _preflight(root, binding["plan_id"], binding["profile_id"], binding["book_id"],
                                                  reconcile_only, policy, encoding, sync_drafts, inspect_only)
        if current != binding:
            api.fail("platform_draft_preview_changed", "冻结清单或平台配置已变化；请重新预览。")
        blockers = _runtime_blockers(profile, _execution_runtime(root, preview, profile, current))
        if blockers:
            connection = _automatic_connection(profile, blockers)
            if not allow_auto_connect or not connection["available"]:
                api.fail(connection["reason"] if connection.get("retry_blocked") else blockers[0],
                         "Chrome 执行连接不可用，请核对连接状态。")
            _update_job(job_id, stage="connecting_chrome")
            connected_automatically = True
            connection = api.chrome_worker.start(root, binding["plan_id"], binding["profile_id"],
                                                 expected_book_id=binding["book_id"], comparison_policy=policy, body_encoding=encoding)
            _update_job(job_id, connection_diagnostics=_connection_diagnostics(connection))
            owned_worker_id = connection.get("owned_worker_id")
            connect_seconds = connection.get("connect_timeout_seconds", 60)
            if type(connect_seconds) is not int or not 1 <= connect_seconds <= 120:
                api.fail("platform_chrome_connection_failed", "自动连接期限无效，未启动平台保存。")
            deadline = time.monotonic() + connect_seconds + 15
            connection_stage = "connecting_chrome"
            while True:
                check_stop()
                state = api.chrome_worker.status(root, binding["plan_id"], binding["profile_id"],
                                                  expected_book_id=binding["book_id"], comparison_policy=policy, body_encoding=encoding)
                _update_job(job_id, connection_diagnostics=_connection_diagnostics(state))
                if state.get("ready") is True:
                    if (state.get("owned") is not True or state.get("model_verified") is not True or
                            state.get("requested_model") != "gpt-6.1-terra" or
                            state.get("resolved_model") != "gpt-6.1-terra"):
                        api.fail("platform_chrome_worker_model_unverified", "未核验浏览器执行模型，未开始平台操作。")
                    break
                if state.get("state") in ("failed", "closed", "stopped", "disconnected", "stopping"):
                    api.fail(state.get("error") or "platform_chrome_connection_failed", "自动连接 Chrome 未完成。")
                if time.monotonic() >= deadline:
                    api.fail("platform_chrome_connection_timeout", "自动连接 Chrome 超时，未启动平台保存。")
                next_stage = ("waiting_for_chrome" if state.get("worker_phase") == "waiting_for_chrome"
                              else "connecting_chrome")
                if next_stage != connection_stage:
                    _update_job(job_id, stage=next_stage)
                    connection_stage = next_stage
                time.sleep(0.2)
            # Connecting runs outside book transactions. Never use the earlier
            # preview if the author edited the source or binding meanwhile.
            _, preview, profile, current = _preflight(root, binding["plan_id"], binding["profile_id"],
                                                       binding["book_id"], reconcile_only, policy, encoding, sync_drafts, inspect_only)
            if current != binding:
                api.fail("platform_draft_preview_changed", "连接期间正式稿或配置已变化，请重新预览。")
        callback = lambda event: _job_progress(job_id, event)
        with _workbench_book(root, binding["book_id"]) as book:
            if profile.get("execution_mode") == "host_assisted_cua":
                context = api.platform_chrome_bridge.HostAdapter(root, _host_binding(binding, preview), profile, callback,
                    **({"update_existing": True} if sync_drafts else {}))
            else:
                native_profile = {key: value for key, value in profile.items() if key != "execution_mode"}
                context = api.platform_browser.BrowserAdapter(native_profile, timeout_ms=timeout_ms,
                    login_timeout_ms=login_timeout_ms, progress_callback=callback, comparison_policy=policy)
            with context as adapter:
                check_stop()
                result = inspect_plan(book, binding["plan_id"], adapter, progress=callback, stop_requested=stopped,
                                      comparison_policy=policy, body_encoding=encoding) if inspect_only else run_plan(book, binding["plan_id"], adapter, execute=True,
                                  reconcile_only=reconcile_only, progress=callback, comparison_policy=policy, body_encoding=encoding,
                                  sync_drafts=sync_drafts, expected_remote=expected_remote, stop_requested=stopped)
    except BaseException as error:
        # Durable intents remain the source of truth even if the worker was
        # interrupted after sending. Status is local-only and never retries.
        result = None
        try:
            with _workbench_book(root, binding["book_id"]) as book:
                result = status(book, binding["plan_id"])
        except BaseException:
            pass
        failure = _error_code(error)
    finally:
        if connected_automatically and owned_worker_id is not None:
            try:
                _update_job(job_id, stage="closing_chrome")
                stopped = api.chrome_worker.disconnect(root, binding["plan_id"], binding["profile_id"],
                    expected_book_id=binding["book_id"],
                    owned_worker_id=owned_worker_id,
                    comparison_policy=binding["comparison_policy"],
                    body_encoding=binding["body_encoding"],
                    # The result is recorded locally. Leave the author's
                    # original Chrome pages, not an extra preview tab.
                    keep_readback=False)
                if stopped.get("cleanup_pending") is True:
                    stopped = api.chrome_worker.wait_owned_cleanup(root, owned_worker_id,
                        expected_book_id=binding["book_id"])
                    if stopped.get("owned") is not True or stopped.get("owned_worker_id") != owned_worker_id:
                        api.fail("platform_chrome_worker_cleanup_unconfirmed", "本次连接仍在清理或未确认退出，暂不能开始下一任务。")
                if stopped.get("cleanup_pending") is True or stopped.get("cleanup_unconfirmed") is True:
                    api.fail("platform_chrome_worker_cleanup_unconfirmed", "本次连接仍在清理或未确认退出，暂不能开始下一任务。")
                if (stopped.get("owned") is True and stopped.get("owned_worker_id") == owned_worker_id and
                        stopped.get("error") in _SPECIFIC_WORKER_STOPS):
                    cause = stopped["error"]
                    if failure in _CONNECTION_FALLBACKS:
                        failure = cause
                    result = _record_worker_stop(root, binding, result, cause)
            except Exception as error:
                # Preserve the durable result, but expose cleanup failures and
                # always let polling reach a terminal state. Never retry writes.
                if failure is None:
                    failure = _error_code(error)
    # A completed inspection becomes actionable only after its owned worker exits.
    _finish_job(job_id, result, failure)


def workbench_start(root, plan_id, profile_id, *, expected_book_id, expected_profile_sha256,
                    preview_token, execute=False, timeout_ms=15000, login_timeout_ms=600000, reconcile_only=False,
                    auto_connect=False, comparison_policy="strict", body_encoding="verbatim", sync_drafts=False,
                    inspect_only=False, inspection_id=None):
    """Start one explicitly authorized frozen batch; repeat clicks reuse jobs."""
    if execute is not True:
        api.fail("platform_draft_execute_required", "保存平台草稿需要明确点击保存整批草稿。")
    if type(auto_connect) is not bool:
        api.fail("invalid_input", "自动连接参数必须是布尔值。")
    if not isinstance(expected_book_id, str) or not isinstance(expected_profile_sha256, str):
        api.fail("platform_draft_preview_required", "请先完成本地预览并核对作品与配置。")
    api.integer(timeout_ms, "timeout_ms", 1)
    api.integer(login_timeout_ms, "login_timeout_ms", 1)
    if timeout_ms > 300000 or login_timeout_ms > 3600000:
        api.fail("invalid_input", "Browser timeout exceeds the supported limit")
    root, preview, profile, binding = _preflight(root, plan_id, profile_id, expected_book_id, reconcile_only,
                                               comparison_policy, body_encoding, sync_drafts, inspect_only)
    if binding["profile_sha256"] != expected_profile_sha256:
        api.fail("platform_draft_profile_changed", "平台配置已变化；请重新预览。")
    try:
        expires_text, _ = preview_token.split(".", 1)
        expires = int(expires_text)
        valid = int(time.time()) <= expires <= int(time.time()) + 600 and hmac.compare_digest(
            preview_token, _preview_token(binding, expires))
    except (AttributeError, ValueError, TypeError):
        valid = False
    if not valid:
        api.fail("platform_draft_preview_required", "预览已失效或目标发生改变；请重新预览并核对。")
    blockers = _runtime_blockers(profile, _execution_runtime(root, preview, profile, binding))
    connection = _automatic_connection(profile, blockers)
    if blockers and (not auto_connect or not connection["available"]):
        api.fail(connection["reason"] if connection.get("retry_blocked") else blockers[0],
                 "所选执行端尚未连接或本地依赖不可用；请完成连接后重新预览。")
    with _WORKBENCH_LOCK:
        if _assistant_active(root, binding["book_id"]):
            api.fail("platform_draft_busy", "当前作品已有助手协助任务运行，请等待完成。")
        if any((other[0] == str(root) and other[1] == binding["book_id"]) or value == binding["target_sha256"]
               for other, value in _WORKBENCH_BINDINGS.items()):
            api.fail("platform_draft_busy", "正在保存本地平台配置，请等待完成后重新预览。")
        for job in _WORKBENCH_JOBS.values():
            if (not job["done"] and job["_target_sha256"] == binding["target_sha256"]):
                if (job["_root"] == str(root) and job["book_id"] == binding["book_id"] and job["plan_id"] == plan_id and
                        job["mode"] == binding["mode"] and job["profile_sha256"] == binding["profile_sha256"]
                        and job["comparison_policy"] == binding["comparison_policy"]
                        and job["body_encoding"] == binding["body_encoding"]):
                    return {**_job_packet(job), "reused": True}
                api.fail("platform_draft_busy", "该平台作品已有草稿任务正在运行；请等待原任务结束。")
        inspection = None
        if sync_drafts:
            inspection = _WORKBENCH_JOBS.get(inspection_id) if isinstance(inspection_id, str) else None
            if (not inspection or inspection.get("state") != "inspection_ready" or not inspection.get("done") or
                    inspection.get("_inspection_expires", 0) <= time.time() or inspection.get("_inspection_used") or
                    inspection.get("_root") != str(root) or inspection.get("_manifest_sha256") != binding["manifest_sha256"] or
                    inspection.get("_target_sha256") != binding["target_sha256"] or
                    any(inspection.get(key) != binding[key] for key in
                        ("book_id", "plan_id", "profile_id", "profile_sha256", "comparison_policy", "body_encoding"))):
                api.fail("platform_draft_inspection_required", "请先完成本次整批差异检查；旧检查、不同目标或已执行检查不能复用。")
        while len(_WORKBENCH_JOBS) >= MAX_WORKBENCH_JOBS:
            retired = next((key for key, job in _WORKBENCH_JOBS.items() if job["done"]), None)
            if retired is None:
                api.fail("platform_draft_busy", "运行中的草稿任务过多；请稍后重试。")
            del _WORKBENCH_JOBS[retired]
        job_id, now = uuid.uuid4().hex, _now()
        job = {"id": job_id, "book_id": binding["book_id"], "plan_id": plan_id,
               "platform": preview["platform"], "account_id": preview["account_id"],
               "remote_book_id": preview["remote_book_id"], "profile_id": profile_id,
               "profile_sha256": binding["profile_sha256"], "mode": binding["mode"], "state": "running", "stage": "queued",
               "comparison_policy": binding["comparison_policy"],
               "body_encoding": binding["body_encoding"],
               "execution_mode": profile.get("execution_mode", "playwright"), "backend": profile.get("execution_mode", "playwright"),
               "done": False, "created_at": now, "updated_at": now, "finished_at": None,
               "total_chapters": len(preview["results"]), "verified_chapters": 0, "accepted_chapters": 0,
               "equivalent_chapters": 0, "rendered_chapters": 0, "current_chapter": None,
               "error": None, "result": _safe_job_result(preview), "_root": str(root), "_target_sha256": binding["target_sha256"],
               "_auto_connect": auto_connect, "stop_requested": False, "checked_chapters": 0,
               "_expected_remote": json.loads(json.dumps(inspection["_expected_remote"])) if inspection else None}
        if inspection:
            inspection["_inspection_used"] = True
        try:
            job["audit_started_saved"] = _save_job_start_audit(job)
        except Exception:
            job["audit_started_saved"] = False
        _WORKBENCH_JOBS[job_id] = job
        worker = threading.Thread(target=_workbench_worker, args=(job_id, binding, timeout_ms, login_timeout_ms),
                                  name="story-platform-draft-" + job_id[:8], daemon=True)
        try:
            worker.start()
        except RuntimeError:
            _finish_job(job_id, error="platform_draft_worker_unavailable")
        return {**_job_packet(job), "reused": False}


def workbench_stop(root, job_id, *, expected_book_id):
    """Cooperative cancellation; never terminate a save or its readback."""
    workbench_job(root, job_id, expected_book_id=expected_book_id)
    with _WORKBENCH_LOCK:
        job = _WORKBENCH_JOBS[job_id]
        if not job["done"]:
            job.update(stop_requested=True, updated_at=_now())
        return _job_packet(job)


def workbench_history(root, plan_id, *, expected_book_id=None):
    """Read latest durable observations, never contacts the platform."""
    with _workbench_book(root, expected_book_id) as book:
        packet = status(book, plan_id)
        with _checked_plan(book, plan_id, check_source=False) as (manifest, _, _):
            reasons, _ = api.publish._source_comparison(book, manifest)
        packet["source_matches_current"] = not reasons
        packet["book_id"] = book.meta("id")
        return packet


def workbench_job(root, job_id, *, expected_book_id=None):
    """Read in-process observations only; polling cannot send or refresh drafts."""
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
        api.fail("invalid_input", "Expected a platform draft job ID")
    with _workbench_book(root, expected_book_id) as book:
        root, book_id = book.root, book.meta("id")
    with _WORKBENCH_LOCK:
        job = _WORKBENCH_JOBS.get(job_id)
        if job is None or job["_root"] != str(root) or job["book_id"] != book_id:
            api.fail("platform_draft_job_missing", "此进程没有该草稿任务；请刷新清单查看磁盘记录，未知结果不会自动重发。")
        return _job_packet(job)


@contextmanager
def _assistant_directory(root, *, create=False):
    """Private, pinned task records; never a model or browser credential."""
    bridge = api.platform_chrome_bridge
    path = api.safe_path(Path(root), ASSISTANT_DIRECTORY)
    with _WORKBENCH_LOCK, bridge._LOCAL_LOCK, bridge._pinned(path, create=create) as directory:
        if create:
            if os.name == "nt":
                os.chmod(directory.path, 0o700)
            else:
                os.fchmod(directory.fd, 0o700)
        with bridge._lock(directory, "assistant.lock"):
            yield directory


def _assistant_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        api.fail("platform_draft_assistant_request_invalid", "助手任务编号无效。")
    return value


def _assistant_host(execution_host="codex", native_model_authorized=False):
    if (execution_host not in ("codex", "qoder-cn", "workbuddy") or
            type(native_model_authorized) is not bool or
            (execution_host == "codex" and native_model_authorized)):
        api.fail("invalid_input", "请选择 Codex、Qoder CN 或 WorkBuddy AI 的实际执行宿主。")
    if execution_host != "codex" and not native_model_authorized:
        api.fail("platform_draft_native_model_not_authorized", "原生宿主执行须有本次明确授权；默认仍要求 Terra。")
    return execution_host


def _assistant_read(directory, request_id):
    request_id = _assistant_id(request_id)
    try:
        record = api.platform_chrome_bridge._read(directory, request_id + ".json", maximum=4 * 1024 * 1024)
    except FileNotFoundError:
        api.fail("platform_draft_assistant_request_missing", "未找到本书的助手协助任务。")
    binding = record.get("binding") if isinstance(record, dict) else None
    binding_keys = {"book_id", "plan_id", "manifest_sha256", "target_sha256", "mode", "comparison_policy",
                    "body_encoding", "profile_id", "profile_sha256"}
    if (not isinstance(record, dict) or type(record.get("schema")) is not int or record.get("schema") != 1 or record.get("request_id") != request_id or
            record.get("execution_origin") != "assistant_session" or
            record.get("model_verification") != "executor_runtime_required" or
            not isinstance(record.get("book_root"), str) or not Path(record["book_root"]).is_absolute() or
            not isinstance(record.get("state"), str) or record["state"] not in _ASSISTANT_STATES or
            not isinstance(record.get("stage"), str) or record["stage"] not in _ASSISTANT_STAGES or
            not isinstance(binding, dict) or set(binding) != binding_keys or
            any(not isinstance(value, str) for value in binding.values()) or binding["mode"] not in _ASSISTANT_MODES or
            any(not re.fullmatch(r"[0-9a-f]{64}", binding[key]) for key in ("manifest_sha256", "target_sha256", "profile_sha256")) or
            record.get("binding_sha256") != _hash(binding) or
            not isinstance(record.get("target"), dict) or _hash(record["target"]) != binding["target_sha256"] or
            type(record.get("expires_epoch")) not in (float, int) or
            not math.isfinite(record["expires_epoch"]) or
            type(record.get("stop_requested")) is not bool or type(record.get("done")) is not bool or
            record["done"] != (record["state"] not in {"prepared", "running"}) or
            any(type(record.get(key)) is not int or not 0 <= record[key] <= 1000
                for key in ("checked_chapters", "verified_chapters", "total_chapters")) or
            record["checked_chapters"] > record["total_chapters"] or record["verified_chapters"] > record["total_chapters"] or
            ("inspection_expires_epoch" in record and
             (type(record["inspection_expires_epoch"]) not in (int, float) or not math.isfinite(record["inspection_expires_epoch"])))):
        api.fail("platform_draft_assistant_request_invalid", "助手任务记录不完整，已停止。")
    _assistant_host(record.get("execution_host", "codex"), record.get("native_model_authorized", False))
    return record


def _assistant_write(directory, record, *, new=False):
    if len(api.dumps(record).encode("utf-8")) > 4 * 1024 * 1024:
        api.fail("platform_draft_assistant_result_limit", "助手记录超过本地大小限制，保留生产日志供核对。")
    api.platform_chrome_bridge._write(directory, _assistant_id(record["request_id"]) + ".json", record, replace=not new)


def _assistant_records(directory):
    names = [name for name in os.listdir(directory.path if os.name == "nt" else directory.fd)
             if re.fullmatch(r"[0-9a-f]{32}\.json", name)]
    if len(names) > 1000:
        api.fail("platform_draft_assistant_request_limit", "助手任务记录超过限制，请先归档历史记录。")
    return [_assistant_read(directory, name[:-5]) for name in names]


def _assistant_global_record(kind, request_id, binding_sha256, *, claim=False):
    """Small local anchors survive restoring a book's prepared request files."""
    name = "assistant-" + kind + "-" + _assistant_id(request_id) + ".json"
    expected = {"schema": 1, "request_id": request_id, "binding_sha256": binding_sha256}
    with api._pinned_directory(_lock_directory()) as directory:
        file = api._BoundFile(directory, name)
        if claim:
            try:
                _write_record(directory, name, expected)
            except FileExistsError:
                api.fail("platform_draft_assistant_request_consumed", "本机已消费该助手请求或检查；恢复旧文件不能重新执行。")
            return True
        if api.publish._bound_stat(file, missing=True) is None:
            return False
        if _read_record(directory, name) != expected:
            api.fail("platform_draft_assistant_request_invalid", "助手任务全局消费记录不一致，已停止。")
        return True


def _assistant_cleanup(record, root):
    """Only the bound local worker protocol can prove execution has drained.

    A business result, expired lease or dead queue controller is insufficient:
    the existing host may still own an awaited browser call.
    """
    pending = {"worker_done": False, "bridge_detached": record.get("bridge_detached") is True,
               "cleanup_complete": False, "cleanup_pending": True, "cleanup_status": "unverified"}
    if record.get("_claimed_record_restored"):
        return pending  # A rolled-back pre-claim file cannot prove no handoff.
    context = record.get("assistant_context")
    if context is None and record.get("remote_accessed") is not True:
        return {"worker_done": None, "bridge_detached": None, "cleanup_complete": True,
                "cleanup_pending": False, "cleanup_status": "not_started"}
    if not isinstance(context, dict):
        return pending
    never = getattr(api.chrome_worker, "assistant_session_never_started", None)
    if record.get("remote_accessed") is not True and callable(never) and never(
            root, _host_binding(record["binding"], record["target"]), context):
        return {"worker_done": None, "bridge_detached": None, "cleanup_complete": True,
                "cleanup_pending": False, "cleanup_status": "not_started"}
    retired = getattr(api.chrome_worker, "assistant_retirement_status", None)
    if callable(retired) and retired(root, _host_binding(record["binding"], record["target"]), context,
            request_id=record["request_id"], request_binding_sha256=record["binding_sha256"]):
        return {"worker_done": False, "bridge_detached": False, "cleanup_complete": False,
                "cleanup_pending": False, "cleanup_status": "external_session_reset",
                "execution_retired_by_operator": True, "bridge_revoked": True}
    inspect = getattr(api.chrome_worker, "assistant_cleanup_status", None)
    if not callable(inspect):
        return pending
    try:
        value = inspect(root, _host_binding(record["binding"], record["target"]), context,
                        request_id=record["request_id"],
                        request_binding_sha256=record["binding_sha256"],
                        mode=record["binding"].get("mode"))
    except (api.StoryError, OSError, ValueError, TypeError):
        return pending
    if not isinstance(value, dict):
        return pending
    complete = (value.get("worker_done") is True and
                value.get("cleanup_complete") is True and value.get("cleanup_status") == "complete")
    reconciled = (value.get("worker_done") is False and
                  value.get("cleanup_complete") is True and
                  value.get("cleanup_status") == "reconciled_absent")
    return {"worker_done": value.get("worker_done") is True,
            "bridge_detached": value.get("bridge_detached") is True,
            "cleanup_complete": complete or reconciled, "cleanup_pending": not (complete or reconciled),
            "cleanup_status": "complete" if complete else "reconciled_absent" if reconciled else
            "pending" if value.get("cleanup_status") == "pending" else "unverified"}


def _assistant_blocks_execution(record):
    return record["state"] in {"prepared", "running"} or record.get("execution_active") is True


def _assistant_effective(record, root):
    record = dict(record)
    if record["book_root"] != str(Path(root).resolve()):
        record["origin_mismatch"] = True
        if record["state"] in {"prepared", "running"}:
            record.update(state="interrupted", stage="finished", done=True, error="platform_draft_book_changed")
    elif record["state"] == "prepared" and _assistant_global_record("claim", record["request_id"], record["binding_sha256"]):
        record.update(state="interrupted", stage="finished", done=True,
                      error="platform_draft_assistant_request_consumed", _claimed_record_restored=True)
    elif record["state"] == "prepared" and record["expires_epoch"] <= time.time():
        record.update(state="expired", stage="finished", done=True, error="platform_draft_assistant_request_expired")
    elif record["state"] == "running":
        alive = api.platform_chrome_bridge._owner_alive(root, record)
        identity = api.task_worker._process_identity(record.get("owner_pid")) if alive else None
        if not alive or (identity is not None and identity != record.get("owner_identity")):
            record.update(state="interrupted", stage="finished", done=True,
                          error="platform_draft_assistant_interrupted")
    if record.get("origin_mismatch"):
        record.update(execution_active=False, cleanup_pending=False, cleanup_complete=False,
                      cleanup_status="historical_other_root", worker_done=False)
    else:
        cleanup = _assistant_cleanup(record, root)
        record.update(cleanup)
        record["execution_active"] = record["state"] == "running" or cleanup["cleanup_pending"]
        if record["done"] and cleanup["cleanup_pending"]:
            record["stage"] = "cleanup_pending"
        elif record["done"] and record["stage"] == "cleanup_pending":
            record["stage"] = "finished"
    return record


def _assistant_active(root, book_id):
    if not api.safe_path(Path(root), ASSISTANT_DIRECTORY).exists():
        return False
    with _assistant_directory(root) as directory:
        return any(r["binding"]["book_id"] == book_id and _assistant_effective(r, root).get("execution_active") is True
                   for r in _assistant_records(directory))


def _assistant_preflight(root, binding):
    return _preflight(root, binding["plan_id"], binding["profile_id"], binding["book_id"],
        binding["mode"] == "reconcile_only", binding["comparison_policy"], binding["body_encoding"],
        binding["mode"] == "sync_drafts", binding["mode"] == "inspect_drafts")


def _assistant_command(argv, *, windows=False):
    # list2cmdline quotes CreateProcess argv, not a shell command. Explicit
    # PowerShell literal arguments also protect &, $, backticks and percent.
    return ("& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in argv)) if windows else shlex.join(argv)


def _assistant_prompt(root, request_id, mode, execution_host="codex", *, binding=None):
    cli = str(Path(__file__).resolve().with_name("story.py"))
    argv = [str(Path(sys.executable).absolute()), cli, "publish-draft-assist-run", "--book", str(root), "--request", request_id]
    command = _assistant_command(argv, windows=os.name == "nt")
    if execution_host == "workbuddy":
        setup = "本机需要已安装工作台支持的官方 agent-browser 与 Node 运行时；缺失时停止并显示具体原因。\n"
        try:
            browser_cli = api.chrome_worker._workbuddy_cli_path()
            node = api.chrome_worker._workbuddy_node_path()
            if binding is not None:
                profile, _ = _read_profile(root, binding["profile_id"])
                session = api.chrome_worker._workbuddy_session(binding)
                state = Path.home() / ".story-skill/workbuddy-browser-sessions" / session
                runner = str(Path(__file__).resolve().with_name("story_workbuddy_worker.mjs"))
                setup_argv = [str(node), runner, "--prepare-login", "--cli", str(browser_cli), "--session", session,
                              "--state", str(state), "--url", profile["urls"]["drafts"]]
                setup = ("先检查本任务专属 agent-browser 会话；仅在未打开所选作品管理页时运行以下准备命令。"
                    "首次使用请让用户在该独立窗口登录；无需退出个人 Chrome。等待用户完成登录后再启动下面的队列。\n"
                    + _assistant_command(setup_argv, windows=os.name == "nt") + "\n")
        except Exception:
            pass  # Handoff remains local and unconsumed when runtime is absent.
        return ("这是工作台准备的单次 WorkBuddy AI 原生任务；复制说明不会执行。\n"
            "用户已允许本次使用 WorkBuddy 原生模型；只能在 WorkBuddy 自己的任务中执行官方 agent-browser CLI，"
            "不借用 Codex/Qoder SDK，不复制个人 profile、cookie 或认证文件。\n" + setup +
            ("本请求只检查差异或只读对账，不新建或修改草稿。\n" if mode != "sync_drafts" else
             "本请求仅按同工具已确认的检查同步草稿，保持已有 ID，并独立全文回读；不送审、不发布。\n") +
            "登录和目标页面就绪后异步启动以下本地控制器，读取 assistant_context；"
            "在同一 WorkBuddy 原生任务的 shell 中按 runner_argv 启动一次，持续等待该进程完成。"
            "不要使用 Node REPL 的 first_call/next_call。未知写入不重发；失败保留回执，不切换工具。"
            "完成后只清理任务自建页，保留登录页。\n" + command)
    if execution_host != "codex":
        label = {"qoder-cn": "Qoder CN", "workbuddy": "WorkBuddy AI"}[execution_host]
        return ("这是工作台准备的单次助手协助任务；复制这段文字不会运行任务。\n"
            f"本次任务已明确允许 {label} 原生模型执行电脑操控，其他任务的 Terra 约定不变。\n"
            f"只能在 {label} 官方当前会话的 Node REPL 和公开 Browser Use SDK 中执行；"
            "不能借用其他宿主、读取认证文件、自填 transport/context 或将配置中的模型名称当成运行证明。\n"
            "启动控制器前，先确认官方 Chrome 中已打开本请求配置 urls.drafts 的目标作品管理页并完成登录。"
            "缺少页面时可在同一浏览器按该配置打开本书草稿箱；不得换书、伪造 attach handle，或先启动控制器等待登录。\n"
            + ("本请求仅检查差异或只读对账，不得新建或修改平台草稿。\n" if mode != "sync_drafts" else
               "仅按本次已确认检查同步草稿；保留已有 ID、修改前备份与独立全文回读，不送审、不发布。\n")
            + "未知写入先只读核对，已消费或中断请求不得重放。异步启动以下 CLI，读取 assistant_context，"
            "然后在同一官方 Node REPL 中连续执行 first_call/next_call，复用同一 SDK、模块与 factory。"
            "SDK、扩展或宿主能力不可用时停止，不改用私有接口；结束后清理自建标签并保留用户页面。\n" + command)
    return ("这是工作台准备的单次助手协助任务；复制这段文字不会运行任务。\n"
        "必须先依据当前运行环境确认实际 computer use 执行者为 gpt-6.1-terra；非 Terra 或模型无法确认时停止，不得回退。\n"
        "先读取本任务及本书的冻结清单、配置和范围。不得自填模型认证、借用旧健康记录或启动自动执行器。\n"
        + ("本请求仅检查差异或只读对账，不得新建或修改平台草稿。检查完成后须由作者另行明确确认同步。\n"
           if mode != "sync_drafts" else
           "本请求已绑定作者明确确认的本次检查；仅同步该范围的草稿，保留已有 ID、修改前备份和独立全文回读，不送审、不发布。\n")
        + "未知写入先只读对账，已消费或中断请求不得重放。先异步启动下面的显式 CLI（Windows 使用 PowerShell），读取它返回的 assistant_context。"
        "再由已确认 Terra 助手在官方 node_repl 中按 first_call／next_call 驱动同一运行时的 Chrome 工作者；"
        "CLI 会等待本任务的真实健康回执并处理认证队列。复制代码不等于执行，不得通过其他脚本或浏览器绕过模型限制。\n" + command)


def _assistant_packet(record, root, *, check_source=True):
    record = _assistant_effective(record, root)
    binding = record["binding"]
    current = False
    if check_source and not record.get("origin_mismatch"):
        try:
            # Polling must not acquire the operation journal lock held by the
            # running core; otherwise progress and status can deadlock.
            with _workbench_book(root, binding["book_id"]) as book:
                with _checked_plan(book, binding["plan_id"], check_source=False) as (manifest, fingerprint, _):
                    reasons, _ = api.publish._source_comparison(book, manifest)
                _, profile_hash = _read_profile(root, binding["profile_id"])
                current = (not reasons and fingerprint == binding["manifest_sha256"] and
                           _hash(_target(manifest)) == binding["target_sha256"] and profile_hash == binding["profile_sha256"])
        except (api.StoryError, OSError, ValueError):
            pass
    result = record.get("result")
    can_sync = (current and record["state"] == "inspection_ready" and record.get("cleanup_complete") is True and
                record.get("execution_active") is not True and
                record.get("inspection_expires_epoch", 0) > time.time() and
                not record.get("inspection_used") and isinstance(result, dict) and result.get("can_execute") is True and
                not _assistant_global_record("inspection", record["request_id"], record["binding_sha256"]))
    fields = ("request_id", "execution_origin", "model_verification", "state", "stage", "done", "created_at",
              "expires_at", "claimed_at", "finished_at", "error", "checked_chapters", "verified_chapters",
              "total_chapters", "result", "remote_accessed", "stop_requested", "target", "binding_sha256",
              "inspection_id", "inspection_expires_at", "current_chapter", "assistant_context", "bridge_detached",
              "book_root", "origin_mismatch", "execution_active", "cleanup_pending", "cleanup_complete",
              "cleanup_status", "worker_done", "execution_retired_by_operator", "bridge_revoked",
              "attribution_path")
    return {"ok": True, **{key: json.loads(json.dumps(record.get(key))) for key in fields}, **binding,
            "execution_host": record.get("execution_host", "codex"),
            "native_model_authorized": record.get("native_model_authorized", False),
            "model_policy": "terra_required" if record.get("execution_host", "codex") == "codex" else "user_authorized_native",
            "execution_mode": "host_assisted_cua", "source_matches_current": current, "can_sync": can_sync,
            "submission_performed": False,
            "handoff_prompt": _assistant_prompt(root, record["request_id"], binding["mode"], record.get("execution_host", "codex"), binding=binding) if record["state"] == "prepared" else None}


def workbench_assistant_prepare(root, plan_id, profile_id, *, expected_book_id, expected_profile_sha256,
                                mode="inspect_drafts", comparison_policy="strict", body_encoding=PLATFORM_BODY_ENCODING,
                                inspection_id=None, confirmed=False, execution_host="codex", native_model_authorized=False,
                                attribution_path=None):
    """Prepare a bounded local handoff, without connecting or running any host."""
    _assistant_host(execution_host, native_model_authorized)
    if (not isinstance(mode, str) or mode not in _ASSISTANT_MODES or type(confirmed) is not bool or
            not isinstance(expected_book_id, str) or not expected_book_id or
            (mode != "sync_drafts" and inspection_id is not None)):
        api.fail("invalid_input", "助手协助只支持检查差异、只读对账或明确确认后的同步。")
    if attribution_path is not None and (
            not isinstance(attribution_path, str) or not attribution_path or mode == "reconcile_only" or
            not Path(attribution_path).is_absolute()):
        api.fail("platform_draft_attribution_invalid", "归属授权只接受检查差异或已确认同步的私有绝对路径证据。")
    if mode == "sync_drafts" and confirmed is not True:
        api.fail("platform_draft_assistant_confirmation_required", "请先查看本次检查结果，再明确确认同步。")
    if mode != "reconcile_only":
        require_platform_comparison_policy(comparison_policy)
        require_platform_body_encoding(body_encoding)
    root, preview, profile, binding = _preflight(root, plan_id, profile_id, expected_book_id,
        mode == "reconcile_only", comparison_policy, body_encoding, mode == "sync_drafts", mode == "inspect_drafts")
    if not profile or profile.get("execution_mode") != "host_assisted_cua" or profile.get("platform") != "fanqie":
        api.fail("platform_draft_assistant_unsupported", "助手协助目前只支持番茄 Chrome 草稿配置。")
    if binding["profile_sha256"] != expected_profile_sha256:
        api.fail("platform_draft_profile_changed", "页面配置已变化，请重新核对。")
    with _assistant_directory(root, create=True) as directory:
        if any(not job["done"] and (job["_root"] == str(root) or job["_target_sha256"] == binding["target_sha256"])
               for job in _WORKBENCH_JOBS.values()) or any(
                   key[0] == str(root) or target == binding["target_sha256"] for key, target in _WORKBENCH_BINDINGS.items()):
            api.fail("platform_draft_busy", "当前作品或平台目标已有运行任务。")
        records = _assistant_records(directory)
        for other in records:
            active = _assistant_effective(other, root)
            if (active["state"] == "prepared" and active["binding"] == binding and active.get("inspection_id") == inspection_id
                    and active.get("execution_host", "codex") == execution_host
                    and active.get("native_model_authorized", False) == native_model_authorized
                    and active.get("attribution_path") == attribution_path):
                return {**_assistant_packet(active, root), "reused": True}
            if _assistant_blocks_execution(active):
                api.fail("platform_draft_busy", "本书已有助手任务或浏览器收尾尚未确认，请等待完成后再执行。")
        if len(records) >= 1000:
            api.fail("platform_draft_assistant_request_limit", "助手任务记录已达限制，请先归档。")
        inspection = None
        if mode == "sync_drafts":
            inspection = _assistant_read(directory, inspection_id)
            expected = {**binding, "mode": "inspect_drafts"}
            if (inspection["binding"] != expected or inspection["state"] != "inspection_ready" or
                    inspection.get("execution_host", "codex") != execution_host or
                    inspection.get("native_model_authorized", False) != native_model_authorized or
                    _assistant_effective(inspection, root).get("cleanup_complete") is not True or
                    inspection["book_root"] != str(root) or
                    inspection.get("inspection_used") or inspection.get("inspection_expires_epoch", 0) <= time.time() or
                    inspection.get("result", {}).get("can_execute") is not True or
                    not isinstance(inspection.get("expected_remote"), dict) or
                    _assistant_global_record("inspection", inspection["request_id"], inspection["binding_sha256"])):
                api.fail("platform_draft_inspection_required", "需要本助手流程刚完成、目标一致且未使用的整批检查。")
            if inspection.get("attribution_path") != attribution_path:
                api.fail("platform_draft_attribution_changed", "本次同步必须沿用检查时使用的同一份归属授权。")
        now, request_id = time.time(), uuid.uuid4().hex
        stamp = api.platform_chrome_bridge._stamp
        record = {"schema": 1, "request_id": request_id, "book_root": str(root), "binding": binding, "binding_sha256": _hash(binding),
                  "target": {key: preview[key] for key in ("platform", "account_id", "remote_book_id")},
                  "execution_origin": "assistant_session", "model_verification": "executor_runtime_required",
                  "execution_host": execution_host, "native_model_authorized": native_model_authorized,
                  "state": "prepared", "stage": "awaiting_assistant", "done": False,
                  "created_at": stamp(now), "expires_at": stamp(now + ASSISTANT_SECONDS), "expires_epoch": now + ASSISTANT_SECONDS,
                  "claimed_at": None, "finished_at": None, "error": None, "checked_chapters": 0,
                  "verified_chapters": 0, "total_chapters": len(preview["results"]), "result": None,
                  "remote_accessed": False, "stop_requested": False, "inspection_id": inspection_id,
                  "attribution_path": attribution_path, "expected_remote": inspection.get("expected_remote") if inspection else None}
        if inspection:
            # Consume the inspection before publishing a write request. A crash
            # may require a fresh read-only check but cannot authorize two writes.
            inspection["inspection_used"] = request_id
            _assistant_write(directory, inspection)
        _assistant_write(directory, record, new=True)
    return {**_assistant_packet(record, root), "reused": False}


def workbench_assistant_status(root, request_id, *, expected_book_id):
    with _workbench_book(root, expected_book_id) as book:
        root = book.root
    try:
        with _assistant_directory(root) as directory:
            record = _assistant_read(directory, request_id)
    except FileNotFoundError:
        api.fail("platform_draft_assistant_request_missing", "未找到本书的助手协助任务。")
    if record["binding"]["book_id"] != expected_book_id:
        api.fail("platform_draft_book_changed", "助手任务不属于当前作品。")
    return _assistant_packet(record, root)


def workbench_assistant_latest(root, plan_id, profile_id, *, expected_book_id):
    with _workbench_book(root, expected_book_id) as book:
        root = book.root
    if not api.safe_path(root, ASSISTANT_DIRECTORY).exists():
        return {"ok": True, "request": None, "active_request": None}
    with _assistant_directory(root) as directory:
        all_rows = _assistant_records(directory)
        rows = [r for r in all_rows if r["binding"]["book_id"] == expected_book_id and
                r["binding"]["plan_id"] == plan_id and r["binding"]["profile_id"] == profile_id]
        active = next((r for r in all_rows if r["binding"]["book_id"] == expected_book_id and
                       _assistant_blocks_execution(_assistant_effective(r, root))), None)
    # A previous version could create a newer result before an older worker
    # drained. Surface the unresolved task for this scope instead of hiding it
    # behind a newer terminal result.
    latest = None
    if rows:
        latest = active if active is not None and active in rows else max(
            rows, key=lambda r: (r["created_at"], r["request_id"]))
    return {"ok": True, "request": _assistant_packet(latest, root) if latest else None,
            "active_request": _assistant_packet(active, root) if active else None}


def workbench_assistant_cancel(root, request_id, *, expected_book_id):
    with _workbench_book(root, expected_book_id) as book:
        root = book.root
    with _assistant_directory(root) as directory:
        record = _assistant_read(directory, request_id)
        if record["binding"]["book_id"] != expected_book_id or record["book_root"] != str(root):
            api.fail("platform_draft_book_changed", "助手任务不属于当前作品。")
        effective = _assistant_effective(record, root)
        if effective["state"] != record["state"]:
            return _assistant_packet(effective, root)
        if record["state"] == "prepared":
            record.update(state="cancelled", stage="finished", done=True, stop_requested=True, finished_at=_now())
        elif record["state"] == "running":
            record["stop_requested"] = True
        else:
            # Completion may win the race with this request. Preserve its exact
            # result and do not re-label or rewrite a finished record as cancel.
            return _assistant_packet(effective, root)
        _assistant_write(directory, record)
    return _assistant_packet(record, root)


def assistant_reset(root, request_id, evidence_path, *, expected_book_id=None, confirmed=False):
    """Explicit local operator recovery for an expired read-only host session."""
    if confirmed is not True:
        api.fail("platform_draft_assistant_reset_confirmation_required", "需确认已保存、正常退出 Chrome 并完成官方内核重置。")
    with _workbench_book(root, expected_book_id) as book:
        root, book_id = book.root, book.meta("id")
    with _assistant_directory(root) as directory:
        record = _assistant_read(directory, request_id)
        if (record["book_root"] != str(root) or record["binding"]["book_id"] != book_id or
                record["binding"]["mode"] not in {"inspect_drafts", "reconcile_only"} or
                record["state"] == "prepared" or not isinstance(record.get("assistant_context"), dict)):
            api.fail("platform_draft_assistant_reset_not_readonly", "只允许隔离已停止的只读助手任务；保存任务必须保留未知状态。")
        if api.platform_chrome_bridge._owner_alive(root, record):
            api.fail("platform_draft_assistant_reset_process_alive", "原队列控制器仍在运行，不能隔离恢复。")
        effective = _assistant_effective(record, root)
        if effective.get("execution_retired_by_operator") is not True:
            try:
                api.chrome_worker.assistant_retire_readonly_session(root, _host_binding(record["binding"], record["target"]),
                    record["assistant_context"], request_id=request_id, request_binding_sha256=record["binding_sha256"],
                    evidence_path=evidence_path)
            except Exception as error:
                api.fail(_error_code(error), "外部会话隔离证据未通过；保留原任务，不启动新操作。")
        # Preserve the original failure/result and request bytes. Retirement is
        # separate protocol evidence, never a successful task or retry grant.
    return _assistant_packet(record, root)


def assistant_reconcile(root, request_id, evidence_path, *, expected_book_id=None, confirmed=False):
    """Explicit local recovery for a finished read-only session with no final receipt.

    The host inventory is accepted only after the worker binding proves the
    queue controller is dead, the lease is not live, every dispatched request
    is read-only and expired, and the session's own ownership record lists the
    exact tabs observed absent. This never marks the worker as completed.
    """
    if confirmed is not True:
        api.fail("platform_draft_assistant_reconcile_confirmation_required",
                 "需确认只核验终止会话的标签缺失，不声称工作器正常完成。")
    with _workbench_book(root, expected_book_id) as book:
        root, book_id = book.root, book.meta("id")
    with _assistant_directory(root) as directory:
        record = _assistant_read(directory, request_id)
        if (record["book_root"] != str(root) or record["binding"]["book_id"] != book_id or
                record["binding"]["mode"] not in {"inspect_drafts", "reconcile_only"} or
                record["state"] == "prepared" or not isinstance(record.get("assistant_context"), dict)):
            api.fail("platform_draft_assistant_reconcile_not_readonly", "只允许核验已停止的只读助手任务。")
        if api.platform_chrome_bridge._owner_alive(root, record):
            api.fail("platform_draft_assistant_reconcile_process_alive", "原队列控制器仍在运行，不能核验收尾。")
        effective = _assistant_effective(record, root)
        if effective.get("cleanup_complete") is not True:
            try:
                api.chrome_worker.assistant_reconcile_session(
                    root, _host_binding(record["binding"], record["target"]), record["assistant_context"],
                    request_id=request_id, request_binding_sha256=record["binding_sha256"],
                    evidence_path=evidence_path)
            except Exception as error:
                api.fail(_error_code(error), "标签缺失核验证据未通过；保留原任务，不启动新操作。")
        # Preserve the original result and request bytes. Reconciliation is
        # separate terminal evidence, never a successful task or replay grant.
    return _assistant_packet(record, root)


def assistant_run(root, request_id, *, expected_book_id=None, on_context=None):
    """Explicit host-assisted execution. Model compliance belongs to the caller's
    verified runtime, never to a caller-supplied model field or a health lease.
    No automatic model process, browser launch or shared-host cleanup occurs.
    """
    with _workbench_book(root, expected_book_id) as book:
        root, book_id = book.root, book.meta("id")
    with _assistant_directory(root) as directory:
        record = _assistant_read(directory, request_id)
        if record["binding"]["book_id"] != book_id or record["book_root"] != str(root):
            api.fail("platform_draft_book_changed", "助手任务不属于当前作品。")
        current = _assistant_effective(record, root)
        if current["state"] != "prepared":
            api.fail("platform_draft_assistant_request_consumed", "请求已执行、取消、中断或过期，不能再次执行；未知结果请只读核对。")
        execution_host = _assistant_host(record.get("execution_host", "codex"), record.get("native_model_authorized", False))
        if execution_host != "codex":
            # Missing native runtime is a local preparation failure, not a
            # consumed browser request. Never launch a substitute host.
            if execution_host == "workbuddy":
                api.chrome_worker._workbuddy_cli_path()
                api.chrome_worker._workbuddy_node_path()
            else:
                api.chrome_worker._native_sdk_path(execution_host)
        if any(_assistant_effective(r, root).get("execution_active") is True for r in _assistant_records(directory)):
            api.fail("platform_draft_busy", "本书已有助手任务运行。")
        _, preview, profile, binding = _assistant_preflight(root, record["binding"])
        if binding != record["binding"]:
            api.fail("platform_draft_preview_changed", "请求的正式稿、配置或目标已变化。")
        if binding["mode"] == "sync_drafts":
            inspection = _assistant_read(directory, record.get("inspection_id"))
            if (inspection["binding"] != {**binding, "mode": "inspect_drafts"} or
                    inspection.get("execution_host", "codex") != execution_host or
                    inspection.get("native_model_authorized", False) != record.get("native_model_authorized", False) or
                    inspection["book_root"] != str(root) or
                    inspection["state"] != "inspection_ready" or
                    inspection.get("attribution_path") != record.get("attribution_path") or
                    _assistant_effective(inspection, root).get("cleanup_complete") is not True or
                    inspection.get("inspection_used") != request_id or
                    inspection.get("inspection_expires_epoch", 0) <= time.time() or
                    inspection.get("expected_remote") != record.get("expected_remote")):
                api.fail("platform_draft_inspection_required", "本次检查已失效，请重新只读检查后确认同步。")
        if any(not job["done"] and (job["_root"] == str(root) or job["_target_sha256"] == binding["target_sha256"])
               for job in _WORKBENCH_JOBS.values()):
            api.fail("platform_draft_busy", "当前作品或目标已有自动任务运行。")
        # Persist the single claim before probing a browser bridge. Even a
        # failed or crashed invocation cannot replay this request.
        owner_identity = api.task_worker._process_identity(os.getpid())
        if not owner_identity:
            api.fail("platform_draft_assistant_process_unverified", "无法核对本次本地任务进程，未执行。")
        _assistant_global_record("claim", request_id, record["binding_sha256"], claim=True)
        if binding["mode"] == "sync_drafts":
            _assistant_global_record("inspection", inspection["request_id"], inspection["binding_sha256"], claim=True)
        record.update(state="running", stage="connecting_assistant", claimed_at=_now(),
                      owner_pid=os.getpid(), owner_identity=owner_identity)
        _assistant_write(directory, record)

    def update(**changes):
        with _assistant_directory(root) as directory:
            latest = _assistant_read(directory, request_id)
            latest.update(changes)
            _assistant_write(directory, latest)
            return latest

    def stopped():
        with _assistant_directory(root) as directory:
            return _assistant_read(directory, request_id)["stop_requested"]

    def progress(event):
        allowed = {key: event[key] for key in ("stage", "checked_chapters", "verified_chapters") if key in event}
        if allowed.get("stage") not in _ASSISTANT_STAGES:
            allowed.pop("stage", None)
        if type(event.get("chapter")) is int:
            allowed["current_chapter"] = event["chapter"]
        update(**allowed)

    def context_ready(packet):
        update(stage="waiting_assistant_bridge", assistant_context=packet)
        if on_context is not None:
            on_context({"request_id": request_id, "assistant_context": packet})

    result, error, session = None, None, None
    try:
        _, preview, profile, current = _assistant_preflight(root, binding)
        if current != binding:
            api.fail("platform_draft_preview_changed", "任务认领后本地材料发生变化，未执行。")
        if stopped():
            api.fail("platform_draft_stop_requested", "助手任务已取消。")
        host_binding = _host_binding(binding, preview)
        session_options = ({"execution_host": execution_host, "native_model_authorized": True}
                           if execution_host != "codex" else {})
        with api.chrome_worker.assistant_session(root, host_binding, on_context=context_ready, stop_requested=stopped,
                                                **session_options) as session:
            update(stage="running_assistant")
            with _workbench_book(root, book_id) as book:
                with api.platform_chrome_bridge.HostAdapter(root, host_binding, profile, progress,
                        update_existing=binding["mode"] == "sync_drafts") as adapter:
                    if adapter.session_id != session.session_id:
                        api.fail("platform_draft_assistant_bridge_changed", "等待期间桥接会话改变，未发送平台操作。")
                    update(remote_accessed=True)
                    if binding["mode"] == "inspect_drafts":
                        result = inspect_plan(book, binding["plan_id"], adapter, progress=progress, stop_requested=stopped,
                            comparison_policy=binding["comparison_policy"], body_encoding=binding["body_encoding"],
                            attribution_path=record.get("attribution_path"))
                    else:
                        result = run_plan(book, binding["plan_id"], adapter, execute=True,
                            reconcile_only=binding["mode"] == "reconcile_only", sync_drafts=binding["mode"] == "sync_drafts",
                            progress=progress, stop_requested=stopped, expected_remote=record.get("expected_remote"),
                            comparison_policy=binding["comparison_policy"], body_encoding=binding["body_encoding"],
                            attribution_path=record.get("attribution_path"))
            update(stage="cleanup_pending")
    except Exception as failure:
        error = _error_code(failure)
    if result and result.get("inspection"):
        state = "inspection_ready" if result.get("can_execute") and error is None else "inspection_blocked"
    elif result and result.get("all_chapters_accepted_this_run"):
        state = "succeeded"
    elif result and any(row.get("state") == "unknown" and row.get("recorded_this_run") for row in result.get("results", [])):
        state = "unknown"
    elif result and result.get("accepted_chapters", 0):
        state = "partial_failure"
    else:
        state = "failed"
    if error is not None and result is None and binding["mode"] == "sync_drafts":
        with _assistant_directory(root) as directory:
            observed = _assistant_read(directory, request_id)
        if observed.get("remote_accessed") is True:
            # In particular a journal write failure can follow a remote save.
            # Without the core's completed result do not claim no write occurred.
            state = "unknown"
    if state != "unknown" and (error == "platform_draft_stop_requested" or (result and result.get("stop_requested"))):
        state = "stopped"
    changes = {"state": state, "stage": "finished", "done": True, "finished_at": _now(), "error": error,
               "result": _safe_job_result(result) if result else None,
               "bridge_detached": getattr(session, "bridge_detached", False) is True}
    if result:
        changes.update(checked_chapters=result.get("checked_chapters", 0),
                       verified_chapters=result.get("accepted_chapters", 0))
        if result.get("inspection"):
            expiry = time.time() + ASSISTANT_SECONDS
            changes.update(expected_remote=result.get("_expected_remote"), inspection_expires_epoch=expiry,
                           inspection_expires_at=api.platform_chrome_bridge._stamp(expiry))
    record = update(**changes)
    return _assistant_packet(record, root)


def register_parser(sub, command):
    parser = command("publish-draft-assist-prepare", "Prepare a local, single-use host task; no browser access")
    parser.add_argument("--id", required=True)
    parser.add_argument("--profile", required=True, help="Saved profile filename within this book")
    parser.add_argument("--mode", choices=sorted(_ASSISTANT_MODES), default="inspect_drafts")
    parser.add_argument("--host", choices=("codex", "qoder-cn", "workbuddy"), default="codex")
    parser.add_argument("--allow-native-host-model", action="store_true", help="Only with explicit user authorization for this native-host task")
    parser.add_argument("--inspection", help="Completed, unused inspection from the same host and target")
    parser.add_argument("--attribution", help="Private operator attribution JSON for one interrupted unnumbered empty draft")
    parser.add_argument("--confirm-sync", action="store_true", help="Explicitly confirm syncing the inspected draft changes")
    for name, description in (("status", "Read local task state; never browser access"),
                              ("cancel", "Request a stop at the existing safe boundary; never replay")):
        parser = command("publish-draft-assist-" + name, description)
        parser.add_argument("--request", required=True)
    parser = command("publish-draft-assist-reset", "Explicit operator isolation of an expired read-only assistant session")
    parser.add_argument("--request", required=True)
    parser.add_argument("--evidence", required=True, help="Private operator evidence JSON; never a worker completion proof")
    parser.add_argument("--confirm-external-session-reset", action="store_true")
    parser = command("publish-draft-assist-reconcile", "Machine-verified terminal recovery of a read-only session without a worker receipt")
    parser.add_argument("--request", required=True)
    parser.add_argument("--evidence", required=True, help="Private host-observed tab inventory JSON; never a worker completion proof")
    parser.add_argument("--confirm-reconcile", action="store_true")
    parser = command("publish-draft-assist-run", "Execute one explicitly prepared assistant task; requires a verified Terra host")
    parser.add_argument("--request", required=True, help="Single-use local assistant request ID; never a model proof")
    parser = command("publish-draft-run", "Preview or explicitly save existing-work chapter drafts; never submits")
    parser.add_argument("--id", required=True, help="Frozen publishing plan ID")
    parser.add_argument("--profile", help="Required with --execute; local browser profile JSON never copied into the journal")
    parser.add_argument("--execute", action="store_true", help="Allow browser access; omission is a local preview")
    parser.add_argument("--reconcile-only", action="store_true", help="With --execute, read and reconcile only; never creates")
    parser.add_argument("--sync-drafts", action="store_true", help="Synchronize Fanqie drafts: update changed drafts and create missing chapters; never submits")
    parser.add_argument("--comparison-policy", choices=sorted(COMPARISON_POLICIES), default="strict",
                        help="Explicit policy for this frozen batch; strict is the default")
    parser.add_argument("--body-encoding", choices=sorted(BODY_ENCODINGS), default=PLATFORM_BODY_ENCODING,
                        help="Platform text removes blank lines; legacy encodings are for read-only reconciliation")
    parser.add_argument("--timeout-ms", type=int, default=15000)
    parser.add_argument("--login-timeout-ms", type=int, default=180000)
    parser = command("publish-draft-status", "Read recorded platform draft observations without contacting a platform")
    parser.add_argument("--id", required=True)


def run(book, args):
    if args.command == "publish-draft-assist-prepare":
        _assistant_host(args.host, args.allow_native_host_model)
        _, profile_sha256 = _read_profile(book.root, args.profile)
        return workbench_assistant_prepare(book.root, args.id, args.profile,
            expected_book_id=book.meta("id"), expected_profile_sha256=profile_sha256,
            mode=args.mode, inspection_id=args.inspection, confirmed=args.confirm_sync,
            execution_host=args.host, native_model_authorized=args.allow_native_host_model,
            attribution_path=args.attribution)
    if args.command in ("publish-draft-assist-status", "publish-draft-assist-cancel"):
        operation = workbench_assistant_status if args.command.endswith("-status") else workbench_assistant_cancel
        return operation(book.root, args.request, expected_book_id=book.meta("id"))
    if args.command == "publish-draft-assist-reset":
        return assistant_reset(book.root, args.request, args.evidence, expected_book_id=book.meta("id"),
                               confirmed=args.confirm_external_session_reset)
    if args.command == "publish-draft-assist-reconcile":
        return assistant_reconcile(book.root, args.request, args.evidence, expected_book_id=book.meta("id"),
                                   confirmed=args.confirm_reconcile)
    if args.command == "publish-draft-assist-run":
        return assistant_run(book.root, args.request, expected_book_id=book.meta("id"),
                             on_context=lambda packet: print(api.dumps(packet), flush=True))
    if args.command == "publish-draft-status":
        return status(book, args.id)
    if args.command != "publish-draft-run":
        raise AssertionError(args.command)
    policy = getattr(args, "comparison_policy", "strict")
    encoding = getattr(args, "body_encoding", PLATFORM_BODY_ENCODING)
    if not args.reconcile_only:
        require_platform_body_encoding(encoding)
    sync_drafts = getattr(args, "sync_drafts", False)
    if not args.execute:
        return run_plan(book, args.id, execute=False, reconcile_only=args.reconcile_only,
                        comparison_policy=policy, body_encoding=encoding, sync_drafts=sync_drafts)
    if not args.profile:
        api.fail("invalid_input", "--execute requires --profile with a local browser profile JSON")
    # Preflight must complete before BrowserAdapter.__enter__ can launch/login.
    run_plan(book, args.id, execute=False, reconcile_only=args.reconcile_only, comparison_policy=policy, body_encoding=encoding, sync_drafts=sync_drafts)
    api.integer(args.timeout_ms, "timeout_ms", 1)
    api.integer(args.login_timeout_ms, "login_timeout_ms", 1)
    profile = api.read_json(args.profile)
    if api.platform_chrome_bridge.validate_profile(profile) == "host_assisted_cua":
        saved = api.safe_path(book.root, PROFILE_DIRECTORY + "/" + Path(args.profile).name)
        if Path(args.profile).expanduser().absolute() != saved:
            api.fail("platform_draft_invalid_profile_path", "宿主模式只使用本书已保存的平台配置。")
        binding, actual_profile = api.platform_chrome_bridge._binding(book, args.id, saved.name,
                                                                    comparison_policy=policy, body_encoding=encoding)
        with api.platform_chrome_bridge.HostAdapter(book.root, binding, actual_profile, update_existing=sync_drafts) as adapter:
            return run_plan(book, args.id, adapter, execute=True, reconcile_only=args.reconcile_only,
                            comparison_policy=policy, body_encoding=encoding, sync_drafts=sync_drafts)
    if sync_drafts:
        api.fail("platform_draft_update_unsupported", "更新已有草稿需要 Chrome 会话执行端。")
    profile = {key: value for key, value in profile.items() if key != "execution_mode"}
    with api.platform_browser.BrowserAdapter(profile, timeout_ms=args.timeout_ms,
                                             login_timeout_ms=args.login_timeout_ms, comparison_policy=policy) as adapter:
        return run_plan(book, args.id, adapter, execute=True, reconcile_only=args.reconcile_only,
                        comparison_policy=policy, body_encoding=encoding, sync_drafts=sync_drafts)
