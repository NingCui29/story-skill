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


COMMANDS = {"publish-draft-run", "publish-draft-status"}
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


def _read_record(directory, name):
    file = api._BoundFile(directory, name)
    before = api.publish._bound_stat(file)
    if before.st_size > MAX_EVENT_BYTES:
        api.fail("platform_draft_journal_invalid", "Platform draft event exceeds its size limit")
    with api._bound_reader(file) as stream:
        opened = os.fstat(stream.fileno())
        if not os.path.samestat(before, opened) or opened.st_nlink != 1:
            api.fail("unsafe_platform_draft_path", "Platform draft event changed while opening")
        data = stream.read(MAX_EVENT_BYTES + 1)
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
                        if request.get("operation") not in {"create_draft", "update_draft"}:
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

    def resolve(self, chapter, observed, *, frozen=None, comparison_policy="strict", body_encoding="verbatim", scope=None):
        if observed.get("formatting_clean") is False:
            return False
        intents, resolved = self.attempts(chapter)
        frozen = observed if frozen is None else frozen
        encoded = _encoded_content(frozen, body_encoding, self.platform)
        rendered_exact, terminal_equivalent = _comparison(encoded, observed, comparison_policy, self.platform)
        if not rendered_exact and not terminal_equivalent:
            return False
        equivalent = _content(frozen) != _content(observed)
        content_hash = _hash(_content(frozen))
        # Do not settle a different, potentially still in-flight version merely
        # because this new plan happens to match the currently visible chapter.
        if any(item["content_sha256"] != content_hash for key, item in intents.items() if key not in resolved):
            return False
        if any(item["schema"] == 2 and {name: item[name] for name in SCOPE_FIELDS} != scope
               for key, item in intents.items() if key not in resolved):
            return False
        if any(item["schema"] == 2 and not _valid_attempt(item, self.platform, frozen)
               for key, item in intents.items() if key not in resolved):
            return False
        if (equivalent or comparison_policy != "strict" or body_encoding != "verbatim") and not _valid_scope(scope):
            api.fail("platform_draft_journal_invalid", "Platform equivalence requires an authorized frozen batch")
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
            "updated_chapters": sum(item["accepted_this_run"] and item["reason"] == "existing_draft_updated" for item in results),
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


def inspect_plan(book, plan_id, adapter, *, progress=None, stop_requested=None,
                 comparison_policy="strict", body_encoding=PLATFORM_BODY_ENCODING):
    """Inspect the whole batch without journal writes, editor fills or saves."""
    require_platform_comparison_policy(comparison_policy)
    require_platform_body_encoding(body_encoding)
    with _checked_plan(book, plan_id) as (manifest, fingerprint, _):
        frozen_items = manifest["chapters"]
        encoded = {item["chapter"]: _encoded_content(item, body_encoding, manifest["platform"]) for item in frozen_items}
    rows, expected_remote = [], {}
    stopped = False
    with _target_lock(manifest) as guard, _journal(book, plan_id, manifest, fingerprint) as journal:
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
                adapter.verify_target(**_target(manifest))
                found = adapter.find_drafts(chapter)
                if not isinstance(found, list):
                    api.fail("platform_draft_invalid_observation", "需要完整目录。")
                if len(found) > 1:
                    row["reason"] = "multiple_remote_drafts"
                elif found:
                    listed = _observation(found[0])
                    guard()
                    adapter.verify_target(**_target(manifest))
                    observed = _observation(adapter.read_draft(listed["remote_id"]), listed["remote_id"])
                    same = _content(encoded[chapter]) == _content(observed) and observed.get("formatting_clean") is not False
                    intents, resolved = guard.attempts(chapter)
                    unresolved = any(key not in resolved for key in intents)
                    row.update(remote_id=observed["remote_id"], action="unchanged" if same else "update",
                               difference=_draft_difference(encoded[chapter], observed))
                    if unresolved and not same:
                        row.update(action="blocked", reason="unresolved_previous_content")
                    expected_remote[str(chapter)] = _remote_signature(observed)
                else:
                    attempts, _ = guard.attempts(chapter)
                    if attempts or journal.may_have_remote(chapter, guard.retired_events(chapter, book.meta("id"), plan_id)):
                        row["reason"] = "previous_attempt_not_found"
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
    complete = len(rows) == len(frozen_items) and not reasons and not stopped
    return {"id": plan_id, "inspection": True, "remote_accessed": bool(rows), "submission_performed": False,
            "source_matches_current": not reasons, "inspection_complete": complete, "stop_requested": stopped,
            "can_execute": complete and all(row["action"] != "blocked" for row in rows),
            "checked_chapters": len(rows), "total_chapters": len(frozen_items), "results": [],
            "changes": rows, "change_counts": {key: sum(row["action"] == key for row in rows)
                                                for key in ("create", "update", "unchanged", "blocked")},
            "_expected_remote": expected_remote, "_manifest_sha256": fingerprint}


def run_plan(book, plan_id, adapter=None, *, execute=False, reconcile_only=False, progress=None,
             comparison_policy="strict", body_encoding="verbatim", sync_drafts=False,
             expected_remote=None, stop_requested=None):
    """Execute only explicitly requested saves; injected adapters own browser I/O."""
    if type(sync_drafts) is not bool or type(reconcile_only) is not bool or (sync_drafts and reconcile_only):
        api.fail("invalid_input", "Choose one draft execution mode")
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
        target_verified = False
        remote_accessed = False
        observed_chapters = set()
        attempt_ids = set()
        exact_chapters, equivalent_chapters = set(), set()
        stopped = False
        scope = {"book_id": book.meta("id"), "plan_id": plan_id, "manifest_sha256": fingerprint}

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
                found = adapter.find_drafts(chapter)
                if not isinstance(found, list):
                    api.fail("platform_draft_invalid_observation", "The complete remote draft list is required")
                if len(found) > 1:
                    append(chapter, "conflict", attempt_id=attempt_id, reason="multiple_remote_drafts")
                    break
                if found:
                    listed = _observation(found[0])
                    remote_id = listed["remote_id"]
                    guard()
                    target_verified = False
                    adapter.verify_target(**_target(manifest))
                    target_verified = True
                    observed = _observation(adapter.read_draft(remote_id), remote_id)
                    if expected_remote is not None and expected_remote.get(str(chapter)) != _remote_signature(observed):
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
                            "browser_channel": profile.get("browser_channel", "chromium"),
                            "execution_mode": profile.get("execution_mode", "playwright")})
        except api.StoryError as error:
            results.append({"id": name, "label": name[:-5], "valid": False,
                            "platform": None, "error": _error_code(error)})
    return results


def _browser_runtime():
    helper = getattr(api.platform_browser, "browser_runtime_status", None)
    return helper() if helper is not None else {"status": "browser_unverified",
                                                "playwright_installed": None, "chromium_installed": None}


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
              "body_encoding", "rendered_chapters", "current_chapter", "error", "result", "checked_chapters", "stop_requested")
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
                    api.publish._source(book, chapter)
                with _binding_profile(root, profile) as (profile_id, profile_sha256):
                    plan = api.publish.prepare(book, request, expected_revision, summary=True)
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
                    api.fail("platform_draft_profile_mismatch", "所选页面配置属于另一作品，未更改原批次的账号或目标。")
                request = {**target, "chapters": list(range(first, last + 1)), "mode": "draft"}
                # prepare checks current reviewed heads and exports under this
                # same operation lock/snapshot; it never mutates the old plan.
                plan = api.publish.prepare(book, request, expected_revision, summary=True)
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
        return {"execution_mode": "host_assisted_cua", "playwright_required": False,
                "worker": api.platform_chrome_bridge.worker_status(root, _host_binding(binding, preview))}
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
              "can_execute", "stop_requested")
    return {key: result[key] for key in fields if key in result}


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
    _update_job(job_id, **changes)


_CONNECTION_FALLBACKS = frozenset({"platform_chrome_worker_unavailable", "platform_chrome_worker_health_expired",
    "platform_chrome_health_expired", "platform_chrome_request_timeout", "platform_chrome_connection_failed"})
_SPECIFIC_WORKER_STOPS = frozenset({"platform_chrome_worker_target_tab_changed", "platform_chrome_worker_target_tab_closed",
    "platform_chrome_worker_existing_tab_required", "platform_chrome_worker_origin_permission_required",
    "platform_chrome_worker_browser_unattached", "platform_chrome_worker_browser_changed",
    "platform_chrome_worker_turn_failed", "platform_chrome_worker_runtime_unavailable"})


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
            if not allow_auto_connect or not _automatic_connection(profile, blockers)["available"]:
                api.fail(blockers[0], "Chrome 执行连接不可用，请核对连接状态。")
            _update_job(job_id, stage="connecting_chrome")
            connected_automatically = True
            connection = api.chrome_worker.start(root, binding["plan_id"], binding["profile_id"],
                                                 expected_book_id=binding["book_id"], comparison_policy=policy, body_encoding=encoding)
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
                if state.get("ready") is True:
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
    # The draft result can finish while owned-worker cleanup remains pending.
    # The controller keeps that connection unavailable until its stop completes.
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
    if blockers and (not auto_connect or not _automatic_connection(profile, blockers)["available"]):
        api.fail(blockers[0], "所选执行端尚未连接或本地依赖不可用；请完成连接后重新预览。")
    with _WORKBENCH_LOCK:
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


def register_parser(sub, command):
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
