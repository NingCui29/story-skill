"""Explicit, resumable saves to existing platform chapter drafts.

The immutable offline publishing plan remains the content source. This module
records only hashes and observations in an independent append-only journal;
it neither submits chapters nor writes creative events. Browser access is
deliberately injected so the state machine can be tested without an account.
"""
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
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


COMMANDS = {"publish-draft-run", "publish-draft-status"}
DIRECTORY = ".story/platform-drafts"
MAX_EVENTS = 10000
MAX_EVENT_BYTES = 16384
EVENTS = {"sending", "saved", "reconciled", "unknown", "blocked", "conflict", "not_found"}
SUCCESS = {"saved", "reconciled"}
_EVENT_NAME = re.compile(r"event-([0-9]{8})-([0-9a-f]{32})\.json")
PROFILE_DIRECTORY = ".story/platform-profiles"
MAX_PROFILE_BYTES = 1024 * 1024
MAX_PROFILES = 100
MAX_WORKBENCH_JOBS = 100
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


def _write_record(directory, name, record):
    """Durably publish a new regular JSON file without replacing any history."""
    data = api.dumps(record).encode("utf-8")
    if len(data) > MAX_EVENT_BYTES:
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


class _TargetState:
    """Keep unresolved writes visible across book copies and new plan IDs.

    Only hashed target/content identities and attempt IDs leave the book root.
    These small local anchors supplement remote reconciliation; they are not a
    distributed lock or proof of remote absence after loss of local storage.
    """

    def __init__(self, directory, manifest, guard):
        self.directory, self.guard = directory, guard
        self.target_hash = _hash(_target(manifest))

    def __call__(self):
        self.guard()

    def attempts(self, chapter):
        self.guard()
        prefix = self.target_hash + "-" + str(chapter) + "-"
        names = os.listdir(self.directory.path if os.name == "nt" else self.directory.fd)
        names = [name for name in names if name.startswith(prefix)]
        if len(names) > MAX_EVENTS * 2:
            api.fail("platform_draft_journal_limit", "Too many target-level draft attempts")
        intents, resolved = {}, {}
        for name in names:
            match = re.fullmatch(re.escape(prefix) + r"([0-9a-f]{32})\.(intent|resolved)\.json", name)
            if not match:
                api.fail("platform_draft_journal_invalid", "Invalid target-level draft record name")
            record = _read_record(self.directory, name)
            fields = {"schema", "target_sha256", "chapter", "attempt_id", "content_sha256", "at"}
            if match[2] == "resolved":
                fields.add("remote_id")
            if (not isinstance(record, dict) or set(record) != fields or
                    type(record["schema"]) is not int or record["schema"] != 1 or
                    record["target_sha256"] != self.target_hash or type(record["chapter"]) is not int or
                    record["chapter"] != chapter or record["attempt_id"] != match[1] or
                    not isinstance(record["content_sha256"], str) or
                    not re.fullmatch(r"[0-9a-f]{64}", record["content_sha256"])):
                api.fail("platform_draft_journal_invalid", "Invalid target-level draft record")
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
        return intents, resolved

    def intent(self, chapter, attempt_id, frozen):
        self.guard()
        record = {"schema": 1, "target_sha256": self.target_hash, "chapter": chapter,
                  "attempt_id": attempt_id, "content_sha256": _hash(_content(frozen)), "at": _now()}
        try:
            _write_record(self.directory, f"{self.target_hash}-{chapter}-{attempt_id}.intent.json", record)
        except Exception:
            api.fail("platform_draft_journal_write_failed", "Could not persist the target-level sending intent")

    def resolve(self, chapter, observed):
        intents, resolved = self.attempts(chapter)
        content_hash = _hash(_content(observed))
        # Do not settle a different, potentially still in-flight version merely
        # because this new plan happens to match the currently visible chapter.
        if any(item["content_sha256"] != content_hash for key, item in intents.items() if key not in resolved):
            return False
        for key, intent in intents.items():
            if key in resolved:
                continue
            record = {**intent, "at": _now(), "remote_id": observed["remote_id"]}
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
        if not isinstance(record, dict) or set(record) != fields:
            raise ValueError("Unexpected fields")
        chapter = record["chapter"]
        if (type(record["schema"]) is not int or record["schema"] != 1 or
                record["book_id"] != self.book.meta("id") or record["plan_id"] != self.plan_id or
                record["manifest_sha256"] != self.fingerprint or
                record["target_sha256"] != _hash(_target(self.manifest)) or
                type(record["sequence"]) is not int or record["sequence"] != len(self.events) + 1 or
                record["previous_sha256"] != self.last_hash or type(chapter) is not int or chapter not in self.chapters or
                record["event"] not in EVENTS or
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

    def append(self, chapter, event, attempt_id=None, remote_id=None, observed=None, reason=None):
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

    def may_have_remote(self, chapter):
        return any(item["chapter"] == chapter and item["event"] in ("sending", "unknown", "saved", "reconciled")
                   for item in self.events)

    def recover(self):
        for chapter in self.chapters:
            previous = self.latest(chapter)
            if previous is not None and previous["event"] == "sending":
                self.append(chapter, "unknown", attempt_id=previous["attempt_id"],
                            remote_id=previous["remote_id"], reason="interrupted_sending")


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
    return {name: raw[name] for name in ("remote_id", "title", "body", "state")}


def _packet(plan_id, manifest, plan_status, journal, *, preview=False, remote_accessed=False,
            target_verified=False, source_checked=False, observed_chapters=()):
    results = []
    for item in manifest["chapters"]:
        latest = journal.latest(item["chapter"])
        effective = "pending" if latest is None else latest["event"]
        if effective == "sending":
            effective = "unknown"
        observed_this_run = item["chapter"] in observed_chapters
        results.append({"chapter": item["chapter"], "state": effective,
                        "remote_id": latest["remote_id"] if latest is not None else None,
                        "title_body_verified": observed_this_run and effective in SUCCESS,
                        "historical_title_body_match": effective in SUCCESS,
                        "observed_this_run": observed_this_run,
                        "observation_scope": "this_run" if observed_this_run else "recorded_history",
                        "last_observed_at": latest["at"] if latest is not None else None,
                        "reason": latest["reason"] if latest is not None else None,
                        "recovery_required": latest is not None and latest["event"] == "sending"})
    all_current = all(item["title_body_verified"] for item in results)
    return {"ok": all_current, "id": plan_id,
            "scope": "platform_chapter_drafts_only", "platform": manifest["platform"],
            "account_id": manifest["account_id"], "remote_book_id": manifest["remote_book_id"],
            "book_title": manifest["book_title"], "plan_status": plan_status,
            "preview": preview, "remote_accessed": remote_accessed,
            "target_verified": target_verified, "source_check_performed": source_checked,
            "content_fields": ["title", "body"], "normalization": "crlf_cr_to_lf_v1",
            "author_note_included": False, "submission_performed": False,
            "all_chapters_verified_this_run": all_current,
            "historical_all_title_body_match": all(item["historical_title_body_match"] for item in results),
            "observation_scope": "recorded_history" if not remote_accessed else "this_run_and_recorded_history",
            "results": results, "history_events_total": len(journal.events),
            "recent_events": journal.events[-20:], "history_truncated": len(journal.events) > 20,
            "source_matches_current": True if source_checked else None}


def status(book, plan_id):
    """Report recorded observations, without refreshing source or platform facts."""
    with _checked_plan(book, plan_id, check_source=False) as (manifest, fingerprint, plan_status):
        with _journal(book, plan_id, manifest, fingerprint) as journal:
            result = _packet(plan_id, manifest, plan_status, journal)
            result["ok"] = True  # The read succeeded; remote facts were not refreshed.
            return result


def run_plan(book, plan_id, adapter=None, *, execute=False, reconcile_only=False, progress=None):
    """Execute only explicitly requested saves; injected adapters own browser I/O."""
    with _checked_plan(book, plan_id, check_source=not reconcile_only) as (manifest, fingerprint, plan_status):
        # Keep immutable in-memory content and never hold a creative transaction
        # or lock across target verification, login, save or remote readback.
        if not execute:
            with _journal(book, plan_id, manifest, fingerprint) as journal:
                result = _packet(plan_id, manifest, plan_status, journal, preview=True, source_checked=not reconcile_only)
                result["ok"] = True
                result["chapters"] = [{"chapter": item["chapter"], "title": item["title"],
                                       "body_characters": item["body_characters"]} for item in manifest["chapters"]]
                result["requested_mode"] = "reconcile_only" if reconcile_only else "save_draft"
                return result
    if adapter is None:
        api.fail("platform_draft_adapter_required", "Execution requires a platform draft adapter")
    with _target_lock(manifest) as guard, _journal(book, plan_id, manifest, fingerprint, create=True) as journal:
        journal.recover()
        target_verified = False
        remote_accessed = False
        observed_chapters = set()
        for frozen in manifest["chapters"]:
            chapter = frozen["chapter"]
            if progress is not None:
                progress({"stage": "chapter_started", "chapter": chapter,
                          "verified_chapters": len(observed_chapters), "total_chapters": len(manifest["chapters"])})
            attempt_id = uuid.uuid4().hex
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
                    journal.append(chapter, "conflict", attempt_id=attempt_id, reason="multiple_remote_drafts")
                    break
                if found:
                    listed = _observation(found[0])
                    remote_id = listed["remote_id"]
                    guard()
                    target_verified = False
                    adapter.verify_target(**_target(manifest))
                    target_verified = True
                    observed = _observation(adapter.read_draft(remote_id), remote_id)
                    observed_chapters.add(chapter)
                    if _content(observed) != _content(frozen):
                        journal.append(chapter, "conflict", attempt_id=attempt_id, remote_id=remote_id,
                                       observed=observed, reason="remote_content_differs")
                        break
                    if not guard.resolve(chapter, observed):
                        journal.append(chapter, "conflict", attempt_id=attempt_id, remote_id=remote_id,
                                       observed=observed, reason="unresolved_previous_content")
                        break
                    journal.append(chapter, "reconciled", attempt_id=attempt_id,
                                   remote_id=remote_id, observed=observed)
                    if progress is not None:
                        progress({"stage": "chapter_verified", "chapter": chapter,
                                  "verified_chapters": len(observed_chapters), "total_chapters": len(manifest["chapters"])})
                    continue
                target_attempts, _ = guard.attempts(chapter)
                if journal.may_have_remote(chapter) or target_attempts:
                    journal.append(chapter, "unknown", attempt_id=attempt_id, reason="previous_attempt_not_found")
                    break
                if reconcile_only:
                    journal.append(chapter, "not_found", attempt_id=attempt_id, reason="remote_draft_not_found")
                    break
                guard()
                target_verified = False
                adapter.verify_target(**_target(manifest))
                target_verified = True
                # Final local check and durable intent share the writer lock.
                # No remote operation occurs until both have completed.
                with _checked_plan(book, plan_id) as (_, current_fingerprint, _):
                    if current_fingerprint != fingerprint:
                        api.fail("platform_draft_source_changed", "Publishing plan changed")
                    guard.intent(chapter, attempt_id, frozen)
                    sending = True
                    journal.append(chapter, "sending", attempt_id=attempt_id)
                guard()
                remote_id = _identifier(adapter.create_draft(chapter, frozen["title"], frozen["body"]), "remote_id")
                guard()
                target_verified = False
                adapter.verify_target(**_target(manifest))
                target_verified = True
                observed = _observation(adapter.read_draft(remote_id), remote_id)
                observed_chapters.add(chapter)
                if _content(observed) != _content(frozen):
                    journal.append(chapter, "conflict", attempt_id=attempt_id, remote_id=remote_id,
                                   observed=observed, reason="saved_content_differs")
                    break
                journal.append(chapter, "saved", attempt_id=attempt_id, remote_id=remote_id, observed=observed)
                if not guard.resolve(chapter, observed):
                    api.fail("platform_draft_unresolved_previous_content", "A previous content version remains unresolved")
                if progress is not None:
                    progress({"stage": "chapter_verified", "chapter": chapter,
                              "verified_chapters": len(observed_chapters), "total_chapters": len(manifest["chapters"])})
            except Exception as error:
                if _error_code(error).startswith("platform_draft_journal"):
                    raise
                # A write may have reached the platform even if create() raised.
                # Record only a safe error code; an interrupted sending event is
                # itself sufficient to prohibit blind replay on the next run.
                journal.append(chapter, "unknown" if sending else "blocked", attempt_id=attempt_id,
                               remote_id=remote_id, reason=_error_code(error))
                break
        with _checked_plan(book, plan_id, check_source=False) as (_, _, current_status):
            current_reasons, _ = api.publish._source_comparison(book, manifest)
        result = _packet(plan_id, manifest, current_status, journal, remote_accessed=remote_accessed,
                         target_verified=target_verified, source_checked=True, observed_chapters=observed_chapters)
        result["source_matches_current"] = not current_reasons
        result["local_version_changed"] = bool(current_reasons)
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
            "unknown_chapters": states.count("unknown"),
            "blocked_chapters": sum(state in ("blocked", "conflict", "not_found") for state in states),
            "pending_chapters": states.count("pending"), "history_events_total": result["history_events_total"]}


def _job_packet(job):
    # Registry entries also hold private binding data. Only this fixed set is
    # returned to HTTP clients; no raw exception/profile/content is retained.
    fields = ("id", "book_id", "plan_id", "platform", "account_id", "remote_book_id", "profile_id", "mode",
              "profile_sha256", "execution_mode", "backend", "state", "stage", "done", "created_at", "updated_at", "finished_at",
              "total_chapters", "verified_chapters", "current_chapter", "error", "result")
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


def _preflight(root, plan_id, profile_id, expected_book_id=None, reconcile_only=False):
    if type(reconcile_only) is not bool:
        api.fail("invalid_input", "reconcile_only must be a boolean")
    with _workbench_book(root, expected_book_id) as book:
        result = run_plan(book, plan_id, reconcile_only=reconcile_only)
        with _checked_plan(book, plan_id, check_source=not reconcile_only) as (manifest, fingerprint, _):
            binding = {"book_id": book.meta("id"), "plan_id": plan_id,
                       "manifest_sha256": fingerprint, "target_sha256": _hash(_target(manifest)),
                       "mode": "reconcile_only" if reconcile_only else "save_draft"}
        root = book.root
    profile = None
    fingerprint = None
    if profile_id is not None:
        profile, fingerprint = _read_profile(root, profile_id)
        if profile["platform"] != result["platform"]:
            api.fail("platform_draft_profile_mismatch", "所选配置的平台与冻结清单不一致。")
    binding.update(profile_id=profile_id, profile_sha256=fingerprint)
    return root, result, profile, binding


def _preview_token(binding, expires=None):
    expires = int(time.time()) + 600 if expires is None else expires
    signed = api.dumps({"binding": binding, "expires": expires}).encode("utf-8")
    return str(expires) + "." + hmac.new(_PREVIEW_SECRET, signed, hashlib.sha256).hexdigest()


def _runtime_blockers(profile, runtime):
    blockers = []
    if profile is not None and profile.get("execution_mode") == "host_assisted_cua":
        if profile.get("platform") == "fanqie" and profile.get("readback") is None:
            blockers.append("platform_browser_readback_required")
        if runtime.get("worker", {}).get("attached") is not True:
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


def workbench_preview(root, plan_id, profile_id=None, *, expected_book_id=None, reconcile_only=False):
    """Check frozen local sources and profile eligibility; never opens a browser."""
    root, preview, profile, binding = _preflight(root, plan_id, profile_id, expected_book_id, reconcile_only)
    runtime = _execution_runtime(root, preview, profile, binding)
    blockers = []
    if profile_id is None:
        blockers.append("profile_required")
    blockers.extend(_runtime_blockers(profile, runtime))
    return {"ok": True, **binding, "preview": preview, "browser_runtime": runtime,
            "browser_channel": profile.get("browser_channel", "chromium") if profile is not None else None,
            "execution_mode": profile.get("execution_mode", "playwright") if profile is not None else None,
            "can_start": not blockers, "blockers": blockers,
            "preview_token": _preview_token(binding) if not blockers else None,
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
                     "chapter_started", "chapter_verified"}:
        return
    changes = {"stage": stage, "state": "waiting_login" if stage == "waiting_login" else "running"}
    # Only local journal completion events can advance the verified count.
    if stage in ("chapter_started", "chapter_verified"):
        if type(event.get("chapter")) is int:
            changes["current_chapter"] = event["chapter"]
        if stage == "chapter_verified" and type(event.get("verified_chapters")) is int:
            changes["verified_chapters"] = event["verified_chapters"]
    _update_job(job_id, **changes)


def _safe_job_result(result):
    fields = ("id", "platform", "account_id", "remote_book_id", "plan_status", "remote_accessed",
              "target_verified", "source_matches_current", "local_version_changed", "all_chapters_verified_this_run",
              "historical_all_title_body_match", "observation_scope", "results", "history_events_total",
              "execution_mode", "backend", "remote_evidence_source")
    return {key: result[key] for key in fields if key in result}


def _finish_job(job_id, result=None, error=None):
    states = [item["state"] for item in result["results"]] if result else []
    verified = sum(item.get("title_body_verified") is True for item in result["results"]) if result else 0
    if error is not None:
        with _WORKBENCH_LOCK:
            verified = max(verified, _WORKBENCH_JOBS[job_id]["verified_chapters"])
    if result and result.get("all_chapters_verified_this_run"):
        state = "succeeded"
    elif "unknown" in states:
        state = "unknown"
    elif verified:
        state = "partial_failure"
    else:
        state = "failed"
    changes = {"state": state, "stage": "finished", "done": True, "finished_at": _now(), "error": error}
    if result is not None:
        changes["result"] = _safe_job_result(result)
    changes["verified_chapters"] = verified
    _update_job(job_id, **changes)


def _workbench_worker(job_id, binding, timeout_ms, login_timeout_ms):
    with _WORKBENCH_LOCK:
        root = _WORKBENCH_JOBS[job_id]["_root"]
    _update_job(job_id, state="running", stage="local_preflight")
    try:
        # Recheck after thread dispatch, before constructing/opening a browser.
        reconcile_only = binding["mode"] == "reconcile_only"
        _, preview, profile, current = _preflight(root, binding["plan_id"], binding["profile_id"], binding["book_id"], reconcile_only)
        if current != binding:
            api.fail("platform_draft_preview_changed", "冻结清单或平台配置已变化；请重新预览。")
        callback = lambda event: _job_progress(job_id, event)
        with _workbench_book(root, binding["book_id"]) as book:
            if profile.get("execution_mode") == "host_assisted_cua":
                context = api.platform_chrome_bridge.HostAdapter(root, _host_binding(binding, preview), profile, callback)
            else:
                native_profile = {key: value for key, value in profile.items() if key != "execution_mode"}
                context = api.platform_browser.BrowserAdapter(native_profile, timeout_ms=timeout_ms,
                    login_timeout_ms=login_timeout_ms, progress_callback=callback)
            with context as adapter:
                result = run_plan(book, binding["plan_id"], adapter, execute=True,
                                  reconcile_only=reconcile_only, progress=callback)
        _finish_job(job_id, result)
    except BaseException as error:
        # Durable intents remain the source of truth even if the worker was
        # interrupted after sending. Status is local-only and never retries.
        result = None
        try:
            with _workbench_book(root, binding["book_id"]) as book:
                result = status(book, binding["plan_id"])
        except BaseException:
            pass
        _finish_job(job_id, result, _error_code(error))


def workbench_start(root, plan_id, profile_id, *, expected_book_id, expected_profile_sha256,
                    preview_token, execute=False, timeout_ms=15000, login_timeout_ms=600000, reconcile_only=False):
    """Start one explicitly authorized frozen batch; repeat clicks reuse jobs."""
    if execute is not True:
        api.fail("platform_draft_execute_required", "保存平台草稿需要明确点击保存整批草稿。")
    if not isinstance(expected_book_id, str) or not isinstance(expected_profile_sha256, str):
        api.fail("platform_draft_preview_required", "请先完成本地预览并核对作品与配置。")
    api.integer(timeout_ms, "timeout_ms", 1)
    api.integer(login_timeout_ms, "login_timeout_ms", 1)
    if timeout_ms > 300000 or login_timeout_ms > 3600000:
        api.fail("invalid_input", "Browser timeout exceeds the supported limit")
    root, preview, profile, binding = _preflight(root, plan_id, profile_id, expected_book_id, reconcile_only)
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
    if blockers:
        api.fail(blockers[0], "所选执行端尚未连接或本地依赖不可用；请完成连接后重新预览。")
    with _WORKBENCH_LOCK:
        if any((other[0] == str(root) and other[1] == binding["book_id"]) or value == binding["target_sha256"]
               for other, value in _WORKBENCH_BINDINGS.items()):
            api.fail("platform_draft_busy", "正在保存本地平台配置，请等待完成后重新预览。")
        for job in _WORKBENCH_JOBS.values():
            if (not job["done"] and job["_target_sha256"] == binding["target_sha256"]):
                if (job["_root"] == str(root) and job["book_id"] == binding["book_id"] and job["plan_id"] == plan_id and
                        job["mode"] == binding["mode"] and job["profile_sha256"] == binding["profile_sha256"]):
                    return {**_job_packet(job), "reused": True}
                api.fail("platform_draft_busy", "该平台作品已有草稿任务正在运行；请等待原任务结束。")
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
               "execution_mode": profile.get("execution_mode", "playwright"), "backend": profile.get("execution_mode", "playwright"),
               "done": False, "created_at": now, "updated_at": now, "finished_at": None,
               "total_chapters": len(preview["results"]), "verified_chapters": 0, "current_chapter": None,
               "error": None, "result": None, "_root": str(root), "_target_sha256": binding["target_sha256"]}
        _WORKBENCH_JOBS[job_id] = job
        worker = threading.Thread(target=_workbench_worker, args=(job_id, binding, timeout_ms, login_timeout_ms),
                                  name="story-platform-draft-" + job_id[:8], daemon=True)
        try:
            worker.start()
        except RuntimeError:
            _finish_job(job_id, error="platform_draft_worker_unavailable")
        return {**_job_packet(job), "reused": False}


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
    parser.add_argument("--timeout-ms", type=int, default=15000)
    parser.add_argument("--login-timeout-ms", type=int, default=180000)
    parser = command("publish-draft-status", "Read recorded platform draft observations without contacting a platform")
    parser.add_argument("--id", required=True)


def run(book, args):
    if args.command == "publish-draft-status":
        return status(book, args.id)
    if args.command != "publish-draft-run":
        raise AssertionError(args.command)
    if not args.execute:
        return run_plan(book, args.id, execute=False, reconcile_only=args.reconcile_only)
    if not args.profile:
        api.fail("invalid_input", "--execute requires --profile with a local browser profile JSON")
    # Preflight must complete before BrowserAdapter.__enter__ can launch/login.
    run_plan(book, args.id, execute=False, reconcile_only=args.reconcile_only)
    api.integer(args.timeout_ms, "timeout_ms", 1)
    api.integer(args.login_timeout_ms, "login_timeout_ms", 1)
    profile = api.read_json(args.profile)
    if api.platform_chrome_bridge.validate_profile(profile) == "host_assisted_cua":
        saved = api.safe_path(book.root, PROFILE_DIRECTORY + "/" + Path(args.profile).name)
        if Path(args.profile).expanduser().absolute() != saved:
            api.fail("platform_draft_invalid_profile_path", "宿主模式只使用本书已保存的平台配置。")
        binding, actual_profile = api.platform_chrome_bridge._binding(book, args.id, saved.name)
        with api.platform_chrome_bridge.HostAdapter(book.root, binding, actual_profile) as adapter:
            return run_plan(book, args.id, adapter, execute=True, reconcile_only=args.reconcile_only)
    profile = {key: value for key, value in profile.items() if key != "execution_mode"}
    with api.platform_browser.BrowserAdapter(profile, timeout_ms=args.timeout_ms,
                                             login_timeout_ms=args.login_timeout_ms) as adapter:
        return run_plan(book, args.id, adapter, execute=True, reconcile_only=args.reconcile_only)
