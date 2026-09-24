"""Offline, immutable chapter publishing preparation. No platform/network actions.

This ledger is deliberately separate from the creative event stream. A prepared
plan proves only local readiness; it never proves account identity, permission,
an upload, remote review, or publication.
"""
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import threading
import unicodedata
import uuid
import zipfile
import zlib


COMMANDS = {"publish-prepare", "publish-list", "publish-inspect", "publish-check",
            "publish-cancel", "publish-backup", "publish-recover", "publish-export", "publish-verify-export",
            "publish-export-list", "publish-compare", "publish-compare-record",
            "publish-compare-history", "publish-compare-inspect"}
SCHEMA_VERSION = 1
DEFAULT_BUDGET = 16000
LEDGER = ".story/publishing.sqlite3"
COMPARISON_PREFIX = "comparison:"
MAX_COMPARISON_RECORDS = 10000
MAX_COMPARISON_RECORD_BYTES = 16384
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
_CONNECTION_LOCK = threading.RLock()
SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE plans(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,
 manifest TEXT NOT NULL,created TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('prepared','stale','cancelled')),
 reasons TEXT NOT NULL,checked_at TEXT);
CREATE UNIQUE INDEX plans_prepared_fingerprint ON plans(fingerprint) WHERE status='prepared';
CREATE TRIGGER plans_immutable BEFORE UPDATE OF id,fingerprint,manifest,created ON plans BEGIN
 SELECT RAISE(ABORT,'publishing snapshots are immutable');
END;
CREATE TRIGGER plans_no_delete BEFORE DELETE ON plans BEGIN
 SELECT RAISE(ABORT,'publishing snapshots cannot be deleted');
END;
"""


def inject(core):
    global api
    api = core


def template():
    return {"platform": "fanqie", "account_id": "<填写平台稳定账号标识>",
            "remote_book_id": "<填写已创建的平台作品ID>", "chapters": [1], "mode": "draft"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(value):
    return api.digest(api.dumps(value))


def _identity(value, field):
    value = api.text_field(value, field, 200)
    if value != value.strip() or any(unicodedata.category(c).startswith("C") for c in value):
        api.fail("invalid_input", field + " must be a stable identifier without surrounding whitespace or controls")
    return value


def _book_kind(book):
    if book.meta("kind") not in ("long", "short"):
        api.fail("invalid_book_kind", "Publishing preparation requires a novel, not an analysis project")


def _input(raw):
    api.object_value(raw, "publishing input")
    allowed = {"platform", "account_id", "remote_book_id", "chapters", "mode"}
    if set(raw) != allowed:
        api.fail("invalid_input", "Publishing input must contain exactly platform, account_id, remote_book_id, chapters and mode")
    if raw["platform"] not in ("fanqie", "qimao") or raw["mode"] != "draft":
        api.fail("invalid_input", "Offline preparation supports fanqie/qimao and mode=draft only")
    chapters = raw["chapters"]
    if not isinstance(chapters, list) or not chapters or len(chapters) > 1000:
        api.fail("invalid_input", "chapters must contain between 1 and 1000 ascending consecutive chapter numbers")
    for chapter in chapters:
        api.integer(chapter, "chapter", 1)
    if any(right != left + 1 for left, right in zip(chapters, chapters[1:])):
        api.fail("invalid_input", "chapters must be an ascending, consecutive range without duplicates")
    return {"platform": raw["platform"], "account_id": _identity(raw["account_id"], "account_id"),
            "remote_book_id": _identity(raw["remote_book_id"], "remote_book_id"),
            "chapters": chapters, "mode": "draft"}


def _bound_stat(file, missing=False):
    try:
        value = (os.lstat(file.path) if os.name == "nt" else
                 os.stat(file.name, dir_fd=file.directory.fd, follow_symlinks=False))
    except FileNotFoundError:
        if missing:
            return None
        raise
    if (not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or
            getattr(value, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
        api.fail("unsafe_publish_path", "Publishing files must be regular files with no links or aliases", path=str(file))
    return value


def _sides(directory, name):
    for suffix in ("-journal", "-wal", "-shm"):
        yield api._BoundFile(directory, name + suffix)


def _file_identity(value):
    return value.st_dev, value.st_ino


def _process_descriptors():
    """Inspect open descriptors without opening/closing the database itself.

    Closing an additional POSIX descriptor for a SQLite file can release the
    process's other SQLite locks. Only fstat is used here. /dev/fd supports both
    macOS and ordinary Unix hosts; /proc is not required.
    """
    try:
        names = os.listdir("/dev/fd")
    except OSError as error:
        api.fail("publishing_connection_unverifiable", "Cannot verify SQLite's open file descriptors on this host", reason=str(error))
    result = {}
    for name in names:
        if not name.isdecimal():
            continue
        fd = int(name)
        try:
            result[fd] = os.fstat(fd)
        except OSError:
            # The descriptor used to enumerate /dev/fd is already closed.
            continue
    return result


@contextmanager
def _windows_database_pin(file, expected):
    """Allow SQLite read/write while denying rename/delete for the full open."""
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    name = str(file.path)
    if not name.startswith("\\\\?\\"):
        name = "\\\\?\\UNC\\" + name[2:] if name.startswith("\\\\") else "\\\\?\\" + name
    # GENERIC_READ, FILE_SHARE_READ|WRITE (no DELETE), OPEN_EXISTING,
    # OPEN_REPARSE_POINT. The CRT descriptor owns the resulting handle.
    handle = kernel.CreateFileW(name, 0x80000000, 0x3, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    try:
        value = os.fstat(fd)
        if (not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or
                getattr(value, "st_file_attributes", 0) & 0x400 or
                _file_identity(value) != _file_identity(expected)):
            api.fail("unsafe_publish_path", "SQLite database pin does not match the checked regular file", path=str(file))
        yield
    finally:
        os.close(fd)


@contextmanager
def _connection(file, write, timeout=10):
    """Verify the actual open file before any query, recovery, or write.

    Module connections are serialized for their lifetime. On POSIX the freshly
    held SQLite descriptor must prove its inode identity, not merely a pathname
    restored after connect. A cached descriptor from an outside SQLite client
    cannot be attributed safely, so that unusual case requires closing that
    client and retrying. Windows prevents replacement with a native file pin.
    """
    with _CONNECTION_LOCK, ExitStack() as stack:
        expected = _bound_stat(file)
        if os.name == "nt":
            stack.enter_context(_windows_database_pin(file, expected))
            descriptors = None
        else:
            descriptors = _process_descriptors()
        db = None
        try:
            db = sqlite3.connect(file.path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=timeout)
            if descriptors is not None:
                current = _process_descriptors()
                opened = {fd: value for fd, value in current.items() if fd not in descriptors or
                          _file_identity(value) != _file_identity(descriptors[fd])}
                matches = [value for value in opened.values() if stat.S_ISREG(value.st_mode) and
                           _file_identity(value) == _file_identity(expected)]
                if not matches:
                    code = "unsafe_publish_path" if any(stat.S_ISREG(value.st_mode) for value in opened.values()) else "publishing_connection_unverifiable"
                    api.fail(code, "Cannot bind SQLite's actual database descriptor to the checked file; close other SQLite clients and retry", path=str(file))
                if any(value.st_nlink != 1 for value in matches):
                    api.fail("unsafe_publish_path", "SQLite opened a linked database", path=str(file))
            if _file_identity(_bound_stat(file)) != _file_identity(expected):
                api.fail("unsafe_publish_path", "Database path changed while SQLite opened it", path=str(file))
            db.row_factory = sqlite3.Row
            yield db
        finally:
            if db is not None:
                if db.in_transaction:
                    db.rollback()
                db.close()


def _validate_database(db, book_id):
    try:
        # Tools only INSERT bounded local comparison records into meta; this
        # ledger does not protect against a database owner deliberately editing
        # them. Do not load history just to read these fixed identities.
        metadata = dict(db.execute("SELECT key,value FROM meta WHERE key IN ('schema','book_id')"))
        schema = json.loads(metadata.get("schema", "null"))
        if type(schema) is not int or schema != SCHEMA_VERSION:
            api.fail("publishing_schema_mismatch", "Unsupported publishing ledger schema")
        if json.loads(metadata.get("book_id", "null")) != book_id:
            api.fail("wrong_book", "Publishing ledger belongs to a different book")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            api.fail("publishing_corrupt", "Publishing ledger failed SQLite integrity checking")
        # A schema-1 ledger must enforce both snapshot immutability and the
        # prepared-only uniqueness rule. SQLite's integrity check does not
        # report missing indexes or triggers after an otherwise valid edit.
        # Check index semantics rather than relying on its particular name.
        prepared_indexes = 0
        for index in db.execute("PRAGMA index_list(plans)"):
            if not index[2]:
                continue
            name = "'" + index[1].replace("'", "''") + "'"
            columns = [item[2] for item in db.execute("PRAGMA index_info(" + name + ")")]
            if "fingerprint" not in columns:
                continue
            if columns == ["fingerprint"] and not index[4]:
                api.fail("publishing_schema_mismatch", "This older publishing ledger globally restricts fingerprints; preserve it for a separate reviewed migration. No automatic migration was performed.")
            sql_row = db.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (index[1],)).fetchone()
            sql = sql_row[0] if sql_row else None
            predicates = list(re.finditer(r"\bWHERE\b", sql, re.IGNORECASE)) if sql else []
            condition = sql[predicates[-1].end():].strip() if predicates else ""
            while condition.startswith("(") and condition.endswith(")"):
                condition = condition[1:-1].strip()
            expected_predicate = r"(?i:(?:status|\"status\"|`status`))\s*=\s*'prepared'"
            if columns != ["fingerprint"] or not index[4] or not re.fullmatch(expected_predicate, condition):
                api.fail("publishing_schema_mismatch", "Publishing ledger has an unsupported fingerprint uniqueness rule")
            prepared_indexes += 1
        if prepared_indexes != 1:
            api.fail("publishing_schema_mismatch", "Publishing ledger lacks its prepared-plan uniqueness rule")
        expected_triggers = {
            "plans_immutable": "CREATE TRIGGER plans_immutable BEFORE UPDATE OF id,fingerprint,manifest,created ON plans BEGIN SELECT RAISE(ABORT,'publishing snapshots are immutable'); END",
            "plans_no_delete": "CREATE TRIGGER plans_no_delete BEFORE DELETE ON plans BEGIN SELECT RAISE(ABORT,'publishing snapshots cannot be deleted'); END",
        }
        triggers = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name='plans'"))
        compact = lambda sql: re.sub(r"\s+", "", sql).lower() if isinstance(sql, str) else None
        if (set(triggers) != set(expected_triggers) or
                any(compact(triggers[name]) != compact(sql) for name, sql in expected_triggers.items())):
            api.fail("publishing_schema_mismatch", "Publishing ledger lacks its immutable-plan triggers")
    except sqlite3.OperationalError as error:
        if "readonly" in str(error).lower() or "read-only" in str(error).lower():
            api.fail("publishing_recovery_required", "SQLite needs a writable recovery pass; run publish-recover --book with this book root before listing again. No plan ID or upload is involved.")
        api.fail("publishing_corrupt", "Invalid publishing ledger", reason=str(error))
    except (sqlite3.Error, ValueError, TypeError) as error:
        api.fail("publishing_corrupt", "Invalid publishing ledger", reason=str(error))


@contextmanager
def _ledger(book, write=False, create=False):
    """Open only a validated regular database under an already-pinned parent.

    Initialization and the first plan use one staged transaction. A crash before
    publication leaves no half-initialized live ledger. Existing crash journals
    may require a writable check; links and orphan sidecars are never followed.
    """
    path = api.safe_path(book.root, LEDGER)
    with api._pinned_directory(path.parent) as directory:
        file = api._BoundFile(directory, path.name)
        before = _bound_stat(file, missing=True)
        sidecars = list(_sides(directory, file.name))
        existing_sides = [_bound_stat(side, missing=True) for side in sidecars]
        if before is None and any(existing_sides):
            api.fail("publishing_corrupt", "Orphan publishing sidecars require recovery before initialization")
        if before is None and not create:
            yield None
            return
        created = before is None
        target = file
        if created:
            fd, file = api._bound_stage(directory, ".publishing-init-")
            os.close(fd)
            before = _bound_stat(file)
            sidecars = list(_sides(directory, file.name))
            for side in sidecars:
                if _bound_stat(side, missing=True) is not None:
                    api.fail("unsafe_publish_path", "Unexpected staging sidecar")
        connections, db, succeeded = ExitStack(), None, False
        try:
            api._verify_bound_directory(directory)
            db = connections.enter_context(_connection(file, write))
            if not os.path.samestat(before, _bound_stat(file)):
                api.fail("unsafe_publish_path", "Publishing database changed during open")
            for side in sidecars:
                _bound_stat(side, missing=True)
            if write:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("BEGIN IMMEDIATE")
            else:
                db.execute("BEGIN")
            if created:
                api.storage.execute_schema(db, SCHEMA)
                db.executemany("INSERT INTO meta VALUES (?,?)", [("schema", api.dumps(SCHEMA_VERSION)),
                                ("book_id", api.dumps(book.meta("id")))])
            else:
                _validate_database(db, book.meta("id"))
            yield db
            api._verify_bound_directory(directory)
            if not os.path.samestat(before, _bound_stat(file)):
                api.fail("unsafe_publish_path", "Publishing database changed during operation")
            for side in sidecars:
                _bound_stat(side, missing=True)
            if write:
                db.commit()
            else:
                db.rollback()
            if created:
                _validate_database(db, book.meta("id"))
                connections.close()
                db = None
                api._verify_bound_directory(directory)
                for side in _sides(directory, target.name):
                    if _bound_stat(side, missing=True) is not None:
                        api.fail("unsafe_publish_path", "Publishing sidecar appeared before initialization")
                # The standard export primitive refuses an existing destination.
                # A POSIX crash between link/unlink is intentionally rejected as
                # an alias on the next open, never mistaken for a ready ledger.
                api._publish_no_replace(file, target)
                if os.name != "nt":
                    api._bound_unlink(file)
                    os.fsync(directory.fd)
                api._verify_bound_directory(directory)
                if not os.path.samestat(before, _bound_stat(target)):
                    api.fail("unsafe_publish_path", "Initialized publishing database changed during publication")
            succeeded = True
        finally:
            connections.close()
            if created and not succeeded:
                current = _bound_stat(file, missing=True)
                if current is not None and os.path.samestat(before, current):
                    api._bound_unlink(file)


def _health(book):
    prior = book.integrity
    try:
        book.integrity = "strict"
        pending, changed = book._export_health()
    finally:
        book.integrity = prior
    if pending or changed:
        api.fail("exports_unresolved", "Resolve exports and outside edits before preparing publishing material",
                 pending=pending, changed=changed)


def _review(book, chapter, body, receipt):
    if "history_branch" in receipt:
        review = receipt.get("review")
        semantic = receipt.get("semantic_review")
        if (not isinstance(semantic, dict) or semantic.get("issues") != [] or
                chapter not in semantic.get("reviewed_chapters", []) or
                not re.fullmatch(r"[0-9a-f]{64}", str(semantic.get("manifest_sha256", ""))) or
                any(not isinstance(semantic.get(k), str) or not semantic[k].strip()
                    for k in ("note", "state_review", "coverage_review"))):
            api.fail("review_required", "Current historical revision lacks its semantic review", chapter=chapter)
        branch = book.db.execute("SELECT status,data FROM history_branches WHERE id=?", (receipt["history_branch"],)).fetchone()
        if not branch or branch["status"] != "published":
            api.fail("review_required", "Current historical receipt has no published branch", chapter=chapter)
        data = json.loads(branch["data"])
        if (semantic.get("manifest_sha256") != api.history._manifest(data) or
                str(chapter) not in data.get("candidates", {})):
            api.fail("review_required", "Historical review does not match its saved manifest", chapter=chapter)
        api.history._review(body, data["candidates"][str(chapter)], review)
    else:
        raw = receipt.get("input")
        review = raw.get("review") if isinstance(raw, dict) else None
    if not isinstance(review, dict) or review.get("draft_sha256") != api.digest(body):
        api.fail("review_required", "The current formal chapter needs a body-bound review", chapter=chapter)
    checks = review.get("checks")
    if not isinstance(checks, dict):
        api.fail("review_required", "Current chapter review is incomplete", chapter=chapter)
    for field in api.CHECKS:
        value = checks.get(field)
        if (not isinstance(value, dict) or not isinstance(value.get("note"), str) or not value["note"].strip() or
                not isinstance(value.get("quote"), str) or not value["quote"].strip() or value["quote"] not in body):
            api.fail("review_required", "Current chapter review has missing or invalid evidence", chapter=chapter, check=field)
    issues = review.get("issues")
    if not isinstance(issues, list) or any(not isinstance(v, dict) or v.get("severity") not in ("advice", "minor") for v in issues):
        api.fail("review_required", "Current chapter review contains blockers or invalid issues", chapter=chapter)


def _chapter_number(value):
    normalized = unicodedata.normalize("NFKC", value)
    if normalized.isdecimal():
        return int(normalized)
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    digits.update({"〇": 0, "两": 2})
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    if all(c in digits for c in normalized):
        return int("".join(str(digits[c]) for c in normalized))
    total = section = digit = 0
    for c in normalized:
        if c in digits:
            digit = digits[c]
        elif c in units:
            unit = units[c]
            if unit == 10000:
                total += (section + digit) * unit
                section = digit = 0
            else:
                section += (digit or 1) * unit
                digit = 0
        else:
            return None
    return total + section + digit


def _split(chapter, body, plan):
    title = plan.get("title") if isinstance(plan, dict) else None
    if title is not None:
        title = _identity(title, "formal plan title")
    lines = body.splitlines(keepends=True)
    if not lines:
        api.fail("chapter_body_missing", "Formal chapter body is empty", chapter=chapter)
    first, *rest = lines
    line = first.lstrip("\ufeff").rstrip("\r\n")
    markdown = re.match(r"^#{1,6}[^\S\r\n]+(.+)$", line)
    if markdown:
        line = re.sub(r"[ \t]+#+[ \t]*$", "", markdown[1])
    match = re.fullmatch(r"第([0-9０-９零〇一二三四五六七八九十百千万两]+)章[ \t　:：、.．-]+(.+?)\s*", line)
    if match:
        heading_title = match[2].strip()
        if _chapter_number(match[1]) != chapter or (title is not None and title != heading_title):
            api.fail("publish_title_mismatch", "Chapter heading does not match its formal number/title", chapter=chapter)
        title = _identity(heading_title, "chapter title")
        prepared_body = "".join(rest)
        conversion = "confirmed_first_heading_removed_v1"
    elif markdown and api.first_chapter_heading(body) == line.strip():
        # Legacy formal chapters can use a title-only H1. Reuse the core's
        # definition; other Markdown-looking lines remain ordinary body text.
        heading_title = api.first_chapter_heading(body)
        if title is not None and title != heading_title:
            api.fail("publish_title_mismatch", "First chapter heading does not match its formal title", chapter=chapter)
        title = _identity(heading_title, "chapter title")
        prepared_body = "".join(rest)
        conversion = "confirmed_first_heading_removed_v1"
    else:
        if not title:
            api.fail("chapter_title_missing", "Set a reviewed formal plan title or supply a verifiable chapter heading", chapter=chapter)
        prepared_body, conversion = body, "body_unchanged_v1"
    if not prepared_body.strip():
        api.fail("chapter_body_missing", "Chapter body is empty after separating its heading", chapter=chapter)
    return title, prepared_body, conversion


def _source(book, chapter):
    row = book.db.execute("SELECT * FROM chapter_state WHERE chapter=?", (chapter,)).fetchone()
    if not row:
        api.fail("chapter_missing", "Only committed formal chapters can be prepared", chapter=chapter)
    head = api.history._head(book, chapter)
    if head["sha"] != row["sha"] or head["receipt"] != row["receipt"]:
        api.fail("history_unavailable", "Formal chapter and current history head disagree", chapter=chapter)
    body = api.history._body(book, head["sha"])
    if api.digest(body) != head["sha"]:
        api.fail("state_corrupt", "Formal chapter hash mismatch", chapter=chapter)
    receipt = json.loads(head["receipt"])
    _review(book, chapter, body, receipt)
    path = book.chapter_path(chapter)
    api.safe_path(book.root, path)
    artifact = book.db.execute("SELECT sha,written_sha FROM artifact_state WHERE path=?", (path,)).fetchone()
    if not artifact or artifact["sha"] != head["sha"] or artifact["written_sha"] != head["sha"]:
        api.fail("exports_unresolved", "Formal chapter does not have its current registered export", chapter=chapter, path=path)
    plan = json.loads(head["plan"]) if head["plan"] else {}
    title, upload_body, conversion = _split(chapter, body, plan)
    return {"chapter": chapter, "head_id": head["id"], "body_sha": head["sha"],
            "review_receipt_sha": _hash(receipt), "chapter_path": path,
            "source_revision": book.meta("revision"), "title": title, "body": upload_body,
            "author_note": "", "conversion_rule": conversion,
            "upload_sha256": _hash({"title": title, "body": upload_body, "author_note": ""}),
            "body_characters": len(upload_body), "local_visible_nonspace_v1": api.visible_count(upload_body)}


def _validated_manifest(row, book_id=None):
    """Use the same snapshot validation for display, reuse and backup."""
    plan_id = None
    try:
        plan_id = row["id"]
        if not isinstance(plan_id, str) or not plan_id.strip():
            raise ValueError("Invalid publishing plan ID")
        manifest = json.loads(row["manifest"])
        if not isinstance(manifest, dict) or _hash(manifest) != row["fingerprint"]:
            raise ValueError("Publishing snapshot fingerprint mismatch")
        required = {"platform", "account_id", "remote_book_id", "mode", "book_id", "book_title",
                    "book_kind", "source_revision", "chapters", "binding_status", "conversion_version"}
        if not required <= manifest.keys():
            raise ValueError("Missing publishing snapshot fields")
        for key in ("account_id", "remote_book_id"):
            value = manifest[key]
            if (not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 200 or
                    any(unicodedata.category(character).startswith("C") for character in value)):
                raise ValueError("Invalid publishing identity field: " + key)
        if (not isinstance(manifest["book_id"], str) or not manifest["book_id"].strip() or
                (book_id is not None and manifest["book_id"] != book_id)):
            raise ValueError("Publishing snapshot belongs to a different book or has no book identity")
        if not isinstance(manifest["book_title"], str) or not manifest["book_title"].strip() or len(manifest["book_title"]) > 200:
            raise ValueError("Invalid snapshot book title")
        if (manifest["platform"] not in ("fanqie", "qimao") or manifest["mode"] != "draft" or
                manifest["book_kind"] not in ("long", "short") or
                manifest["binding_status"] != "user_declared_unverified" or
                type(manifest["conversion_version"]) is not int or manifest["conversion_version"] != 1):
            raise ValueError("Unsupported publishing snapshot platform, mode, or format")
        revision = manifest["source_revision"]
        if type(revision) is not int or not 0 <= revision <= 2**63 - 1:
            raise ValueError("Invalid snapshot source revision")
        chapters = manifest["chapters"]
        if not isinstance(chapters, list) or not 1 <= len(chapters) <= 1000:
            raise ValueError("Snapshot must contain a nonempty bounded chapter range")
        chapter_fields = {"chapter", "head_id", "body_sha", "review_receipt_sha", "chapter_path", "source_revision",
                          "title", "body", "author_note", "conversion_rule", "upload_sha256", "body_characters",
                          "local_visible_nonspace_v1"}
        previous = None
        for chapter in chapters:
            if not isinstance(chapter, dict) or not chapter_fields <= chapter.keys():
                raise ValueError("Missing required chapter snapshot fields")
            number = chapter["chapter"]
            if (type(number) is not int or not 1 <= number <= 2**63 - 1 or
                    (previous is not None and number != previous + 1)):
                raise ValueError("Snapshot chapters must be ascending consecutive positive integers")
            previous = number
            if type(chapter["source_revision"]) is not int or chapter["source_revision"] != revision:
                raise ValueError("Chapter and manifest source revisions disagree")
            if not isinstance(chapter["head_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", chapter["head_id"]):
                raise ValueError("Invalid formal history head proof")
            for key in ("body_sha", "review_receipt_sha", "upload_sha256"):
                if not isinstance(chapter[key], str) or not re.fullmatch(r"[0-9a-f]{64}", chapter[key]):
                    raise ValueError("Invalid chapter proof digest: " + key)
            path = chapter["chapter_path"]
            if (not isinstance(path, str) or not path.strip() or "\x00" in path or
                    path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", path) or
                    ".." in re.split(r"[/\\]", path)):
                raise ValueError("Chapter export path must stay relative to the book")
            title, body, note = chapter["title"], chapter["body"], chapter["author_note"]
            if (not isinstance(title, str) or not title.strip() or title != title.strip() or len(title) > 200 or
                    any(unicodedata.category(character).startswith("C") for character in title) or
                    not isinstance(body, str) or not body.strip() or not isinstance(note, str)):
                raise ValueError("Invalid frozen chapter title, body, or author note")
            if chapter["conversion_rule"] not in ("confirmed_first_heading_removed_v1", "body_unchanged_v1"):
                raise ValueError("Unknown chapter conversion rule")
            if _hash({"title": title, "body": body, "author_note": note}) != chapter["upload_sha256"]:
                raise ValueError("Frozen upload content does not match its digest")
            if chapter["conversion_rule"] == "body_unchanged_v1" and api.digest(body) != chapter["body_sha"]:
                raise ValueError("Unchanged frozen body does not match its formal body digest")
            for key, actual in (("body_characters", len(body)), ("local_visible_nonspace_v1", api.visible_count(body))):
                if type(chapter[key]) is not int or chapter[key] != actual:
                    raise ValueError("Frozen chapter count is inconsistent: " + key)
        reasons = json.loads(row["reasons"])
        if (not isinstance(reasons, list) or row["status"] not in ("prepared", "stale", "cancelled") or
                any(not isinstance(reason, dict) or not isinstance(reason.get("code"), str) or not reason["code"].strip()
                    for reason in reasons)):
            raise ValueError("Invalid publishing status or reasons")
        if (not isinstance(row["created"], str) or not row["created"].strip() or
                (row["checked_at"] is not None and (not isinstance(row["checked_at"], str) or not row["checked_at"].strip()))):
            raise ValueError("Invalid publishing observation timestamps")
        return manifest
    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as error:
        api.fail("publishing_corrupt", "Invalid publishing snapshot", id=plan_id, reason=str(error))


def _validate_plans(db):
    try:
        metadata = db.execute("SELECT value FROM meta WHERE key='book_id'").fetchone()
        book_id = json.loads(metadata[0]) if metadata is not None else None
        if not isinstance(book_id, str) or not book_id.strip():
            raise ValueError("Publishing ledger has no valid book identity")
        count = 0
        for row in db.execute("SELECT * FROM plans"):
            _validated_manifest(row, book_id)
            count += 1
        return count
    except (sqlite3.Error, ValueError, TypeError, KeyError, IndexError) as error:
        api.fail("publishing_corrupt", "Invalid publishing ledger records", reason=str(error))


def _reuse_key(manifest):
    # Revision is an audit timestamp, not part of chapter/target identity. Keep
    # it in the immutable manifest and full fingerprint, but exclude it only
    # when deciding whether a still-prepared snapshot can be reused.
    value = {key: item for key, item in manifest.items() if key != "source_revision"}
    value["chapters"] = [{key: item for key, item in chapter.items() if key != "source_revision"}
                         for chapter in manifest["chapters"]]
    return _hash(value)


def _packet(row, full=False, book_id=None):
    manifest = _validated_manifest(row, book_id)
    result = {"ok": row["status"] == "prepared", "id": row["id"], "status": row["status"],
              "created": row["created"], "checked_at": row["checked_at"], "reasons": json.loads(row["reasons"]),
              "manifest_sha256": row["fingerprint"], "remote_state": "unknown", "content_verification": "unread",
              "platform_verified": False, "ready_to_upload": False,
              "source_check_performed": False, "source_matches_current": None,
              "scope": "offline_chapter_draft_material", "platform": manifest["platform"],
              "account_id": manifest["account_id"], "remote_book_id": manifest["remote_book_id"],
              "book_title": manifest["book_title"], "mode": manifest["mode"],
              "source_revision": manifest["source_revision"],
              "chapters": [chapter["chapter"] for chapter in manifest["chapters"]],
              "total_chapters": len(manifest["chapters"]),
              "content_scope": "full_manifest" if full else "metadata"}
    if full:
        result["manifest"] = manifest
    return result


def _plan(db, plan_id):
    _identity(plan_id, "plan id")
    if db is None:
        api.fail("publishing_missing", "No publishing ledger exists; prepare a plan first")
    try:
        row = db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            api.fail("publish_plan_missing", "Unknown publishing plan", id=plan_id)
        metadata = db.execute("SELECT value FROM meta WHERE key='book_id'").fetchone()
        book_id = json.loads(metadata[0]) if metadata is not None else None
        if not isinstance(book_id, str) or not book_id.strip():
            raise ValueError("Publishing ledger has no valid book identity")
        _validated_manifest(row, book_id)
        return row
    except (sqlite3.Error, ValueError, TypeError, KeyError, IndexError) as error:
        api.fail("publishing_corrupt", "Invalid publishing ledger record", id=plan_id, reason=str(error))


def prepare(book, raw, expected, summary=False):
    _book_kind(book)
    request = _input(raw)
    api.integer(expected, "expected revision")
    with api.storage.operation_lock(book), book.read_snapshot():
        revision = book.meta("revision")
        if expected != revision:
            api.fail("stale_revision", "Reload formal chapter state before preparing publication", expected=expected, actual=revision)
        _health(book)
        chapters = [_source(book, chapter) for chapter in request["chapters"]]
        manifest = {**request, "chapters": chapters, "book_id": book.meta("id"), "book_title": book.meta("title"),
                    "book_kind": book.meta("kind"), "source_revision": revision,
                    "binding_status": "user_declared_unverified", "conversion_version": 1}
        fingerprint = _hash(manifest)
        with _ledger(book, write=True, create=True) as db:
            old = db.execute("SELECT * FROM plans WHERE fingerprint=? AND status='prepared'", (fingerprint,)).fetchone()
            if old is None:
                key = _reuse_key(manifest)
                for candidate in db.execute("SELECT * FROM plans WHERE status='prepared' ORDER BY created,id"):
                    if _reuse_key(_validated_manifest(candidate, book.meta("id"))) == key:
                        old = candidate
                        break
            if old is not None:
                # Reuse still performs a fresh formal-source check. Keep the
                # immutable snapshot, but record when that check was made.
                db.execute("UPDATE plans SET checked_at=? WHERE id=?", (_now(), old["id"]))
                result = {**_packet(_plan(db, old["id"]), full=not summary, book_id=book.meta("id")), "idempotent": True}
            else:
                plan_id = uuid.uuid4().hex
                db.execute("INSERT INTO plans VALUES (?,?,?,?,?,?,?)", (plan_id, fingerprint, api.dumps(manifest), _now(), "prepared", "[]", _now()))
                result = {**_packet(_plan(db, plan_id), full=not summary, book_id=book.meta("id")), "idempotent": False}
            result.update(source_check_performed=True, source_matches_current=True, checked_revision=revision)
    return result


def list_plans(book, offset=0, limit=10, budget=DEFAULT_BUDGET):
    _book_kind(book)
    api.integer(offset, "offset")
    api.integer(limit, "limit", 1)
    if limit > 100:
        api.fail("invalid_input", "limit must be at most 100")
    with _ledger(book) as db:
        total = db.execute("SELECT count(*) FROM plans").fetchone()[0] if db is not None else 0
        rows = db.execute("SELECT * FROM plans ORDER BY created DESC,id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall() if db is not None else []
        result = {"ok": True, "ledger_exists": db is not None, "book_id": book.meta("id"),
                  "results": [_packet(row, book_id=book.meta("id")) for row in rows],
                  "total": total, "offset": offset, "limit": limit,
                  "next_offset": offset + len(rows) if offset + len(rows) < total else None}
        return api.bounded_packet(result, budget)


def inspect(book, plan_id, budget=DEFAULT_BUDGET, *, summary=False, chapter=None, offset=0, limit=20):
    _book_kind(book)
    if summary and chapter is not None:
        api.fail("invalid_input", "Choose either a chapter summary page or one complete chapter")
    if not summary and (offset != 0 or limit != 20):
        api.fail("invalid_input", "Chapter pagination requires --summary")
    api.integer(offset, "chapter offset")
    api.integer(limit, "chapter limit", 1)
    if limit > 100:
        api.fail("invalid_input", "chapter limit must be at most 100")
    if chapter is not None:
        api.integer(chapter, "chapter", 1)
    with _ledger(book) as db:
        row = _plan(db, plan_id)
        result = _packet(row, full=not summary and chapter is None, book_id=book.meta("id"))
        manifest = _validated_manifest(row, book.meta("id"))
        if summary:
            entries = manifest["chapters"][offset:offset + limit]
            result.update(content_scope="chapter_summaries", chapter_page={
                "results": [{field: item[field] for field in (
                    "chapter", "title", "chapter_path", "body_characters", "local_visible_nonspace_v1", "upload_sha256")}
                    for item in entries],
                "offset": offset, "limit": limit, "total": len(manifest["chapters"]),
                "next_offset": offset + len(entries) if offset + len(entries) < len(manifest["chapters"]) else None})
        elif chapter is not None:
            selected = next((item for item in manifest["chapters"] if item["chapter"] == chapter), None)
            if selected is None:
                api.fail("publish_chapter_missing", "Chapter is not in this frozen publishing plan", id=plan_id, chapter=chapter)
            result.update(content_scope="single_chapter", chapter_snapshot=selected)
        return api.bounded_packet(result, budget)


def _source_comparison(book, manifest):
    """Compare a frozen plan with the current formal book without ledger writes."""
    reasons = []
    chapter_checks = []
    try:
        _health(book)
    except api.StoryError as error:
        reasons.append({"code": error.code, "message": error.message, **error.details})
    for frozen in manifest["chapters"]:
        try:
            current = _source(book, frozen["chapter"])
            fields = ("head_id", "body_sha", "review_receipt_sha", "chapter_path", "title", "body", "upload_sha256", "conversion_rule")
            changed = [field for field in fields if current[field] != frozen[field]]
            if changed:
                reasons.append({"code": "source_changed", "chapter": frozen["chapter"], "fields": changed})
            chapter_checks.append({"chapter": frozen["chapter"], "matches_current": not changed,
                                   "changed_fields": changed})
        except api.StoryError as error:
            reasons.append({"code": error.code, "message": error.message, "chapter": frozen["chapter"], **error.details})
            chapter_checks.append({"chapter": frozen["chapter"], "matches_current": False,
                                   "error": {"code": error.code, "message": error.message, **error.details}})
    return reasons, chapter_checks


def _check_locked(book, db, plan_id):
    """Refresh source state while the caller holds the book and ledger locks."""
    row = _plan(db, plan_id)
    if row["status"] == "cancelled":
        return _packet(row)
    manifest = _validated_manifest(row, book.meta("id"))
    reasons, chapter_checks = _source_comparison(book, manifest)
    status = "stale" if reasons or row["status"] == "stale" else "prepared"
    db.execute("UPDATE plans SET status=?,reasons=?,checked_at=? WHERE id=? AND status IN ('prepared','stale')",
               (status, api.dumps(reasons), _now(), plan_id))
    return {**_packet(_plan(db, plan_id)), "source_check_performed": True,
            "source_matches_current": not reasons, "checked_revision": book.meta("revision"),
            "chapter_checks": chapter_checks}


def check(book, plan_id):
    _book_kind(book)
    with api.storage.operation_lock(book), book.read_snapshot(), _ledger(book, write=True) as db:
        return _check_locked(book, db, plan_id)


def _comparison_input(raw):
    """Copy only accepted fields before acquiring locks or hashing the input."""
    api.object_value(raw, "readback copy")
    fields = set(raw)
    if not {"title", "body"} <= fields or not fields <= {"title", "body", "author_note"}:
        api.fail("invalid_input", "Readback copy must contain title and body, with optional author_note only")
    for field in fields:
        if not isinstance(raw[field], str):
            api.fail("invalid_input", "Readback fields must be strings within the supported size", field=field)
        try:
            size = len(raw[field].encode("utf-8"))
        except UnicodeError:
            api.fail("invalid_input", "Readback fields must contain valid Unicode text", field=field)
        if size > MAX_MEMBER_BYTES:
            api.fail("invalid_input", "Readback fields must be strings within the supported size", field=field)
    return {field: raw[field] for field in ("title", "body", "author_note") if field in raw}


def _comparison_locked(book, db, plan_id, chapter, raw):
    """Evaluate one in-memory copy while the book snapshot and ledger are held."""

    def normalized(value):
        return value.replace("\r\n", "\n").replace("\r", "\n")

    row = _plan(db, plan_id)
    manifest = _validated_manifest(row, book.meta("id"))
    frozen = next((item for item in manifest["chapters"] if item["chapter"] == chapter), None)
    if frozen is None:
        api.fail("publish_chapter_missing", "Chapter is not in this frozen publishing plan", id=plan_id, chapter=chapter)
    comparisons = {}
    for field in ("title", "body", "author_note"):
        if field not in raw:
            comparisons[field] = {"checked": False, "matches_frozen": None,
                                  "readback_sha256": None, "normalized_readback_sha256": None,
                                  "normalized_frozen_sha256": hashlib.sha256(normalized(frozen[field]).encode("utf-8")).hexdigest(),
                                  "newline_normalization_applied": None}
            continue
        copy = raw[field]
        normalized_copy = normalized(copy)
        normalized_frozen = normalized(frozen[field])
        comparisons[field] = {"checked": True, "matches_frozen": normalized_copy == normalized_frozen,
                              "readback_sha256": hashlib.sha256(copy.encode("utf-8")).hexdigest(),
                              "normalized_readback_sha256": hashlib.sha256(normalized_copy.encode("utf-8")).hexdigest(),
                              "normalized_frozen_sha256": hashlib.sha256(normalized_frozen.encode("utf-8")).hexdigest(),
                              "newline_normalization_applied": normalized_copy != copy}
    current_reasons, chapter_checks = _source_comparison(book, manifest)
    source_matches = not current_reasons
    complete = all(item["checked"] for item in comparisons.values())
    fields_match = all(item["matches_frozen"] for item in comparisons.values() if item["checked"])
    copy_matches = complete and fields_match
    usable = row["status"] == "prepared" and source_matches and copy_matches
    changed_chapters = [item["chapter"] for item in chapter_checks if not item["matches_current"]]
    reason_summaries = [{field: reason[field] for field in ("code", "message", "chapter", "fields") if field in reason}
                        for reason in current_reasons[:10]]
    result = {**_packet(row, book_id=book.meta("id")), "ok": usable, "chapter": chapter,
              "copy_source": "user_supplied_copy", "evidence_scope": "local_text_comparison_only",
              "binding_status": "user_declared_unverified", "normalization": "crlf_cr_to_lf_v1",
              "field_comparisons": comparisons,
              "fields_checked": [field for field in ("title", "body", "author_note") if field in raw],
              "unchecked_fields": [field for field in ("title", "body", "author_note") if field not in raw],
              "readback_complete": complete, "supplied_fields_match_frozen": fields_match,
              "copy_matches_frozen": copy_matches, "usable_now": usable, "ready_to_upload": False,
              "source_check_performed": True, "source_matches_current": source_matches,
              "checked_revision": book.meta("revision"),
              "source_checked_chapters": len(chapter_checks),
              "source_changed_chapter_count": len(changed_chapters),
              "source_changed_chapters": changed_chapters[:20],
              "source_changed_chapters_truncated": len(changed_chapters) > 20,
              "current_source_reasons": reason_summaries,
              "current_source_reason_count": len(current_reasons),
              "current_source_reasons_truncated": len(current_reasons) > 10}
    result.pop("chapters")  # This view checks one chapter; keep large plans within a useful budget.
    return result


def compare(book, plan_id, chapter, raw, budget=DEFAULT_BUDGET):
    """Compare a user's copied text with one frozen chapter, without ledger writes."""
    _book_kind(book)
    api.integer(chapter, "chapter", 1)
    raw = _comparison_input(raw)
    with api.storage.operation_lock(book), book.read_snapshot(), _ledger(book) as db:
        return api.bounded_packet(_comparison_locked(book, db, plan_id, chapter, raw), budget)


def _comparison_record(db, key, saved, book_id, plan_cache=None):
    """Validate one book-bound local receipt; never infer a platform observation."""
    try:
        if not isinstance(key, str) or not re.fullmatch(r"comparison:[0-9a-f]{32}", key):
            raise ValueError("Invalid comparison record key")
        if not isinstance(saved, str) or len(saved.encode("utf-8")) > MAX_COMPARISON_RECORD_BYTES:
            raise ValueError("Comparison record exceeds its size limit")
        envelope = json.loads(saved)
        if (not isinstance(envelope, dict) or set(envelope) != {"record", "sha256"} or
                saved != api.dumps(envelope)):
            raise ValueError("Invalid comparison record encoding")
        record = envelope["record"]
        fields = {"schema", "id", "book_id", "plan_id", "manifest_sha256", "chapter", "recorded_at",
                  "copy_source", "evidence_scope", "normalization", "input_sha256", "fields_checked",
                  "unchecked_fields", "field_comparisons", "readback_complete", "supplied_fields_match_frozen",
                  "copy_matches_frozen", "status_at_record", "source_matches_current_at_record",
                  "checked_revision", "source_reason_codes", "source_reason_count", "usable_at_record",
                  "remote_state", "platform_verified", "ready_to_upload"}
        if (not isinstance(record, dict) or set(record) != fields or
                not isinstance(envelope["sha256"], str) or
                not re.fullmatch(r"[0-9a-f]{64}", envelope["sha256"]) or
                _hash(record) != envelope["sha256"]):
            raise ValueError("Comparison record fingerprint or fields are invalid")
        if (type(record["schema"]) is not int or record["schema"] != 1 or
                record["id"] != key[len(COMPARISON_PREFIX):] or record["book_id"] != book_id or
                not isinstance(record["plan_id"], str) or
                not re.fullmatch(r"[0-9a-f]{32}", record["plan_id"]) or
                not isinstance(record["manifest_sha256"], str) or
                not re.fullmatch(r"[0-9a-f]{64}", record["manifest_sha256"]) or
                type(record["chapter"]) is not int or record["chapter"] < 1 or
                record["copy_source"] != "user_supplied_copy" or
                record["evidence_scope"] != "local_text_comparison_only" or
                record["normalization"] != "crlf_cr_to_lf_v1" or
                record["remote_state"] != "unknown" or
                record["platform_verified"] is not False or record["ready_to_upload"] is not False or
                not isinstance(record["input_sha256"], str) or
                not re.fullmatch(r"[0-9a-f]{64}", record["input_sha256"])):
            raise ValueError("Comparison record identity or evidence boundary is invalid")
        timestamp = datetime.fromisoformat(record["recorded_at"])
        if timestamp.utcoffset() is None or timestamp.utcoffset().total_seconds() != 0:
            raise ValueError("Comparison record time must be UTC")
        if (record["status_at_record"] not in ("prepared", "stale", "cancelled") or
                type(record["source_matches_current_at_record"]) is not bool or
                type(record["checked_revision"]) is not int or
                not 0 <= record["checked_revision"] <= 2**63 - 1):
            raise ValueError("Invalid saved source comparison state")
        cached = plan_cache.get(record["plan_id"]) if plan_cache is not None else None
        if cached is None:
            row = db.execute("SELECT * FROM plans WHERE id=?", (record["plan_id"],)).fetchone()
            if row is None or row["fingerprint"] != record["manifest_sha256"]:
                raise ValueError("Comparison record does not belong to a frozen plan")
            manifest = _validated_manifest(row, book_id)
            if plan_cache is not None:
                if len(plan_cache) >= 16:
                    plan_cache.pop(next(iter(plan_cache)))
                plan_cache[record["plan_id"]] = (row["fingerprint"], manifest)
        else:
            fingerprint, manifest = cached
            if fingerprint != record["manifest_sha256"]:
                raise ValueError("Comparison record plan fingerprint changed")
        frozen = next((item for item in manifest["chapters"] if item["chapter"] == record["chapter"]), None)
        if frozen is None:
            raise ValueError("Comparison chapter is absent from its frozen plan")
        checked = record["fields_checked"]
        unchecked = record["unchecked_fields"]
        expected = ["title", "body", "author_note"]
        if (checked not in (["title", "body"], expected) or
                unchecked != [field for field in expected if field not in checked] or
                not isinstance(record["field_comparisons"], dict) or
                set(record["field_comparisons"]) != set(expected)):
            raise ValueError("Invalid checked-field coverage")
        for field in expected:
            item = record["field_comparisons"][field]
            if (not isinstance(item, dict) or set(item) != {"checked", "matches_frozen", "readback_sha256",
                                                          "normalized_readback_sha256", "normalized_frozen_sha256",
                                                          "newline_normalization_applied"} or
                    item["checked"] is not (field in checked)):
                raise ValueError("Invalid saved field comparison")
            frozen_hash = api.digest(frozen[field].replace("\r\n", "\n").replace("\r", "\n"))
            if item["normalized_frozen_sha256"] != frozen_hash:
                raise ValueError("Saved frozen-field digest differs from the plan")
            if field not in checked:
                if any(item[name] is not None for name in ("matches_frozen", "readback_sha256",
                                                          "normalized_readback_sha256", "newline_normalization_applied")):
                    raise ValueError("An unchecked field cannot have a comparison result")
                continue
            raw_sha, normalized_sha = item["readback_sha256"], item["normalized_readback_sha256"]
            if (not isinstance(raw_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", raw_sha) or
                    not isinstance(normalized_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", normalized_sha) or
                    type(item["matches_frozen"]) is not bool or
                    item["matches_frozen"] is not (normalized_sha == frozen_hash) or
                    type(item["newline_normalization_applied"]) is not bool or
                    item["newline_normalization_applied"] is not (raw_sha != normalized_sha)):
                raise ValueError("Saved field hashes and verdict disagree")
        complete = len(checked) == 3
        supplied_match = all(record["field_comparisons"][field]["matches_frozen"] for field in checked)
        copy_match = complete and supplied_match
        if (record["readback_complete"] is not complete or
                record["supplied_fields_match_frozen"] is not supplied_match or
                record["copy_matches_frozen"] is not copy_match or
                record["usable_at_record"] is not (record["status_at_record"] == "prepared" and
                                                    record["source_matches_current_at_record"] and copy_match)):
            raise ValueError("Saved coverage or verdict is inconsistent")
        codes = record["source_reason_codes"]
        count = record["source_reason_count"]
        if (not isinstance(codes, list) or len(codes) > 10 or
                any(not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code) for code in codes) or
                type(count) is not int or not len(codes) <= count <= 1001 or
                (record["source_matches_current_at_record"] is not (count == 0))):
            raise ValueError("Saved source-reason summary is inconsistent")
        return record, envelope["sha256"]
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, sqlite3.Error, UnicodeError) as error:
        api.fail("publishing_corrupt", "Invalid saved local comparison record", id=key, reason=str(error))


def _comparison_records(db, book_id):
    """Iterate and validate bounded comparison history without loading prose."""
    count = 0
    plan_cache = {}
    for key, saved in db.execute("SELECT key,value FROM meta WHERE key GLOB 'comparison:*' ORDER BY key"):
        count += 1
        if count > MAX_COMPARISON_RECORDS:
            api.fail("publish_compare_limit", "Too many local comparison records to inspect safely")
        yield _comparison_record(db, key, saved, book_id, plan_cache)


def record_compare(book, plan_id, chapter, raw, budget=DEFAULT_BUDGET):
    """Explicitly save one local comparison; no claim about a platform draft."""
    _book_kind(book)
    api.integer(chapter, "chapter", 1)
    raw = _comparison_input(raw)
    with api.storage.operation_lock(book), book.read_snapshot(), _ledger(book, write=True) as db:
        result = _comparison_locked(book, db, plan_id, chapter, raw)
        count = sum(1 for _ in _comparison_records(db, book.meta("id")))
        if count >= MAX_COMPARISON_RECORDS:
            api.fail("publish_compare_limit", "This book has reached its local comparison record limit")
        record_id = uuid.uuid4().hex
        record = {"schema": 1, "id": record_id, "book_id": book.meta("id"), "plan_id": plan_id,
                  "manifest_sha256": result["manifest_sha256"], "chapter": chapter,
                  "recorded_at": _now(), "copy_source": "user_supplied_copy",
                  "evidence_scope": "local_text_comparison_only", "normalization": result["normalization"],
                  "input_sha256": _hash(raw), "fields_checked": result["fields_checked"],
                  "unchecked_fields": result["unchecked_fields"],
                  "field_comparisons": result["field_comparisons"],
                  "readback_complete": result["readback_complete"],
                  "supplied_fields_match_frozen": result["supplied_fields_match_frozen"],
                  "copy_matches_frozen": result["copy_matches_frozen"],
                  "status_at_record": result["status"],
                  "source_matches_current_at_record": result["source_matches_current"],
                  "checked_revision": result["checked_revision"],
                  "source_reason_codes": [item["code"] for item in result["current_source_reasons"]],
                  "source_reason_count": result["current_source_reason_count"],
                  "usable_at_record": result["usable_now"], "remote_state": "unknown",
                  "platform_verified": False, "ready_to_upload": False}
        envelope = {"record": record, "sha256": _hash(record)}
        key = COMPARISON_PREFIX + record_id
        saved = api.dumps(envelope)
        _comparison_record(db, key, saved, book.meta("id"))
        response = api.bounded_packet({**result, "record_saved": True, "record_id": record_id,
                                       "record_sha256": envelope["sha256"],
                                       "recorded_at": record["recorded_at"]}, budget)
        db.execute("INSERT INTO meta(key,value) VALUES (?,?)", (key, saved))
        return response


def compare_history(book, plan_id, offset=0, limit=10, budget=DEFAULT_BUDGET):
    """List past local checks without looking at today's manuscript or platform."""
    _book_kind(book)
    api.integer(offset, "offset")
    api.integer(limit, "limit", 1)
    if limit > 100:
        api.fail("invalid_input", "limit must be at most 100")
    with _ledger(book) as db:
        _plan(db, plan_id)
        summary_fields = ("id", "plan_id", "chapter", "recorded_at", "fields_checked", "unchecked_fields",
                          "copy_matches_frozen", "status_at_record", "source_matches_current_at_record",
                          "usable_at_record")
        summaries = [{field: record[field] for field in summary_fields}
                     for record, _ in _comparison_records(db, book.meta("id"))
                     if record["plan_id"] == plan_id]
        summaries.sort(key=lambda record: (record["recorded_at"], record["id"]), reverse=True)
        page = summaries[offset:offset + limit]
        return api.bounded_packet({"ok": True, "book_id": book.meta("id"), "plan_id": plan_id,
                                   "results": page, "total": len(summaries), "offset": offset,
                                   "limit": limit,
                                   "next_offset": offset + len(page) if offset + len(page) < len(summaries) else None,
                                   "copy_source": "user_supplied_copy", "evidence_scope": "local_text_comparison_only",
                                   "source_check_performed": False, "source_matches_current": None,
                                   "remote_state": "unknown", "platform_verified": False}, budget)


def inspect_comparison(book, record_id, budget=DEFAULT_BUDGET):
    """Read a saved local comparison only; historical usability is not current."""
    _book_kind(book)
    if not isinstance(record_id, str) or not re.fullmatch(r"[0-9a-f]{32}", record_id):
        api.fail("invalid_input", "--record-id must be a local comparison record ID")
    with _ledger(book) as db:
        if db is None:
            api.fail("publishing_missing", "No publishing ledger exists")
        key = COMPARISON_PREFIX + record_id
        row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if row is None:
            api.fail("publish_comparison_missing", "Local comparison record not found", record_id=record_id)
        record, record_sha = _comparison_record(db, key, row[0], book.meta("id"))
        return api.bounded_packet({"ok": True, "record_valid": True, "record_sha256": record_sha,
                                   "local_comparison_record": record, "source_check_performed": False,
                                   "source_matches_current": None, "remote_state": "unknown",
                                   "platform_verified": False, "ready_to_upload": False}, budget)


def _material_entries(manifest, receipt):
    """Use generated member paths; novel titles and user IDs are content only."""
    platform = {"fanqie": "番茄", "qimao": "七猫"}[manifest["platform"]]
    guide = (
        f"《{manifest['book_title']}》章节材料\n\n"
        f"目标平台：{platform}\n账号标识：{manifest['account_id']}\n"
        f"平台作品 ID：{manifest['remote_book_id']}\n清单 ID：{receipt['id']}\n"
        f"导出时间（UTC）：{receipt['exported_at']}\n"
        f"本次核对创作版本：{receipt['checked_revision']}\n\n"
        "本地材料已准备，尚未上传。账号与作品 ID 仅为用户声明，尚未在平台核验。\n"
        "解压后按目录中的章号顺序打开各章文件。标题.txt 仅含章名，正文.txt 仅含正文，"
        "作者的话.txt 仅含该字段；三个文件均为 UTF-8 文本，不额外添加章头、标点或换行。\n"
        "手动填写时核对平台账号、作品、章节分组及章序，再将各文件内容放入对应字段。"
        "空的作者的话文件表示该字段为空。不要把目录、使用说明或 JSON 清单贴入正文。\n"
        "目录中的字数是本地口径，平台统计可能不同。manifest.json 保存完整冻结内容与来源证明；"
        "receipt.json 保存本次本地核对结果，均不是平台回执。\n"
        "此包只在导出时核对过正式稿。后续改稿、清单过期或取消不会修改或撤回旧包；"
        "使用前应重新核对，必要时重新准备并导出。包内含完整稿件及目标标识，请按需要自行保管。\n"
        "逐章材料不代表短故事整篇投稿或完本验收。平台草稿、审核、排期和上线状态均未知。\n"
    )
    entries = {
        "使用说明.txt": guide.encode("utf-8"),
        "manifest.json": api.dumps(manifest).encode("utf-8"),
        "receipt.json": api.dumps(receipt).encode("utf-8"),
    }
    lines = [f"《{manifest['book_title']}》章节目录", "按章号顺序填写，字数为本地统计，平台可能不同。", ""]
    for item in manifest["chapters"]:
        prefix = f"章节/第{item['chapter']}章/"
        lines.append(f"第{item['chapter']}章 {item['title']}｜{item['local_visible_nonspace_v1']} 字｜{prefix}")
        for label, field in (("标题", "title"), ("正文", "body"), ("作者的话", "author_note")):
            entries[prefix + label + ".txt"] = item[field].encode("utf-8")
    entries["目录.txt"] = "\n".join(lines).encode("utf-8")
    return entries


def _write_material(book, entries):
    if not 4 <= len(entries) <= 3004 or any(len(content) > MAX_MEMBER_BYTES for content in entries.values()) or sum(map(len, entries.values())) > MAX_EXPANDED_BYTES:
        api.fail("publish_export_failed", "Chapter material exceeds the supported export limits")
    target = api.safe_path(book.root, ".story/publishing-exports/material-" + uuid.uuid4().hex + ".zip")
    try:
        with api._pinned_directory(target.parent, create=True) as directory:
            file = api._BoundFile(directory, target.name)
            fd, staged = api._bound_stage(directory, ".publishing-export-")
            owned = os.fstat(fd)
            try:
                with api._owned_fdopen(fd, "wb") as stream:
                    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                        for name, content in entries.items():
                            archive.writestr(name, content)
                    stream.flush()
                    os.fsync(stream.fileno())
                if not os.path.samestat(owned, _bound_stat(staged)):
                    api.fail("unsafe_publish_path", "Publishing export staging file changed")
                if _bound_stat(staged).st_size > MAX_ARCHIVE_BYTES:
                    api.fail("publish_export_failed", "Chapter material archive exceeds the supported size")
                with api._bound_reader(staged) as stream, zipfile.ZipFile(stream) as archive:
                    names = archive.namelist()
                    if len(names) != len(entries) or set(names) != set(entries):
                        api.fail("publish_export_failed", "Export archive has unexpected or missing members")
                    for name, content in entries.items():
                        if archive.read(name) != content:
                            api.fail("publish_export_failed", "Export archive content failed verification", member=name)
                fingerprint = api._bound_hash(staged)
                api._verify_bound_directory(directory)
                api._publish_no_replace(staged, file)
                if os.name != "nt":
                    api._bound_unlink(staged)
                    os.fsync(directory.fd)
                api._verify_bound_directory(directory)
                current = _bound_stat(file)
                if not os.path.samestat(owned, current) or api._bound_hash(file) != fingerprint:
                    api.fail("publish_export_failed", "Export archive changed during publication", path=str(target))
                return {"path": str(target), "sha256": fingerprint, "bytes": current.st_size,
                        "format": "zip", "encoding": "utf-8", "files": len(entries)}
            finally:
                # Delete only our own staging name, never an existing destination
                # or a different file substituted by a concurrent editor.
                try:
                    remaining = (os.lstat(staged.path) if os.name == "nt" else
                                 os.stat(staged.name, dir_fd=directory.fd, follow_symlinks=False))
                except FileNotFoundError:
                    pass
                else:
                    if os.path.samestat(owned, remaining):
                        api._bound_unlink(staged)
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        api.fail("publish_export_failed", "Could not create a verified chapter material archive",
                 path=str(target), reason=str(error))


def _write_export_receipt(book, artifact, manifest, receipt):
    """Publish a separate, immutable lookup record after the ZIP is complete."""
    archive = api.Path(artifact["path"])
    target = archive.with_name(archive.stem + ".receipt.json")
    record = {"schema": 1, "book_id": manifest["book_id"], "plan_id": receipt["id"],
              "manifest_sha256": receipt["manifest_sha256"], "archive": archive.name,
              "sha256": artifact["sha256"], "bytes": artifact["bytes"],
              "exported_at": receipt["exported_at"]}
    payload = api.dumps(record).encode("utf-8")
    try:
        with api._pinned_directory(target.parent) as directory:
            zip_file = api._BoundFile(directory, archive.name)
            zip_before = _bound_stat(zip_file)
            if zip_before.st_size != artifact["bytes"] or api._bound_hash(zip_file) != artifact["sha256"]:
                api.fail("publish_export_failed", "Export archive changed before saving its receipt", path=str(archive))
            file = api._BoundFile(directory, target.name)
            fd, staged = api._bound_stage(directory, ".publishing-receipt-")
            owned = os.fstat(fd)
            try:
                with api._owned_fdopen(fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                if not os.path.samestat(owned, _bound_stat(staged)):
                    api.fail("unsafe_publish_path", "Export receipt staging file changed", path=str(staged))
                api._verify_bound_directory(directory)
                api._publish_no_replace(staged, file)
                if os.name != "nt":
                    api._bound_unlink(staged)
                    os.fsync(directory.fd)
                api._verify_bound_directory(directory)
                if not os.path.samestat(owned, _bound_stat(file)) or api._bound_hash(file) != hashlib.sha256(payload).hexdigest():
                    api.fail("publish_export_failed", "Export receipt changed during publication", path=str(target))
                zip_after = _bound_stat(zip_file)
                if (not os.path.samestat(zip_before, zip_after) or
                        zip_after.st_size != artifact["bytes"] or
                        zip_after.st_mtime_ns != zip_before.st_mtime_ns or
                        api._bound_hash(zip_file) != artifact["sha256"]):
                    api.fail("publish_export_failed", "Export archive changed while saving its receipt",
                             path=str(archive), receipt_path=str(target))
                return str(target)
            finally:
                try:
                    remaining = (os.lstat(staged.path) if os.name == "nt" else
                                 os.stat(staged.name, dir_fd=directory.fd, follow_symlinks=False))
                except FileNotFoundError:
                    pass
                else:
                    if os.path.samestat(owned, remaining):
                        api._bound_unlink(staged)
    except OSError as error:
        api.fail("publish_export_failed", "Could not save the chapter material receipt",
                 path=str(target), orphan_archive=str(archive), reason=str(error))


def export_material(book, plan_id):
    """Export only freshly checked prepared material, without any platform action."""
    _book_kind(book)
    with api.storage.operation_lock(book), book.read_snapshot(), _ledger(book, write=True) as db:
        result = _check_locked(book, db, plan_id)
        if not result["ok"]:
            # Return normally so newly detected stale state is committed.
            return {**result, "export_created": False}
        manifest = _validated_manifest(_plan(db, plan_id), book.meta("id"))
        receipt = {**result, "exported_at": _now()}
        artifact = _write_material(book, _material_entries(manifest, receipt))
        artifact["chapters"] = len(manifest["chapters"])
        try:
            artifact["receipt_path"] = _write_export_receipt(book, artifact, manifest, receipt)
        except api.StoryError as error:
            error.details.setdefault("orphan_archive", artifact["path"])
            raise
        return {**receipt, "export_created": True, "export": artifact}


def verify_export(book, path, expected_sha, expected_id):
    """Read one saved package and the current book; change neither ledger nor prose."""
    _book_kind(book)
    if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        api.fail("invalid_input", "--sha256 must be a lowercase SHA-256 hex digest")
    if not isinstance(expected_id, str) or not re.fullmatch(r"[0-9a-f]{32}", expected_id):
        api.fail("invalid_input", "--id must be the original 32-character publishing plan ID")
    try:
        path = api.Path(path).expanduser()
    except (TypeError, ValueError, OSError) as error:
        api.fail("invalid_input", "Export archive path is invalid", reason=str(error))
    if not path.is_absolute() or "\x00" in str(path):
        api.fail("invalid_input", "Export archive path must be absolute")
    try:
        with api.storage.operation_lock(book), book.read_snapshot(), _ledger(book) as db:
            if db is None:
                api.fail("publishing_missing", "No publishing ledger exists for this book")
            with api._pinned_directory(path.parent) as directory:
                file = api._BoundFile(directory, path.name)
                before = _bound_stat(file)
                if before.st_size > MAX_ARCHIVE_BYTES:
                    api.fail("publish_export_invalid", "Export archive exceeds the supported size", path=str(path))
                with api._bound_reader(file) as stream, tempfile.TemporaryFile(mode="w+b") as copy:
                    opened = os.fstat(stream.fileno())
                    if not os.path.samestat(before, opened):
                        api.fail("unsafe_publish_path", "Export archive changed while opening", path=str(path))
                    digest = hashlib.sha256()
                    size = 0
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        size += len(chunk)
                        if size > MAX_ARCHIVE_BYTES:
                            api.fail("publish_export_invalid", "Export archive exceeds the supported size", path=str(path))
                        digest.update(chunk)
                        copy.write(chunk)
                    archive_sha = digest.hexdigest()
                    after_open = os.fstat(stream.fileno())
                    after_path = _bound_stat(file)
                    api._verify_bound_directory(directory)
                    def identity(value):
                        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns
                    # On Windows, path stat and handle fstat can report different
                    # creation/change times for the same file. Compare those
                    # timestamps within each observation method, and bind the
                    # two views by file identity, size, and modification time.
                    # The final path check and hash below
                    # still catch rewrites after the copy was made.
                    if (not os.path.samestat(before, opened) or before.st_size != opened.st_size or
                            before.st_mtime_ns != opened.st_mtime_ns or
                            identity(before) != identity(after_path) or
                            identity(opened) != identity(after_open)):
                        api.fail("unsafe_publish_path", "Export archive changed during verification", path=str(path))
                    if archive_sha != expected_sha:
                        api.fail("publish_export_mismatch", "Archive SHA-256 differs from the original export receipt",
                                 path=str(path), expected=expected_sha, actual=archive_sha)
                    copy.seek(0)
                    with zipfile.ZipFile(copy) as archive:
                        members = archive.infolist()
                        names = [item.filename for item in members]
                        if (not 4 <= len(names) <= 3004 or len(set(names)) != len(names) or
                                "manifest.json" not in names or "receipt.json" not in names):
                            api.fail("publish_export_invalid", "Archive has missing or duplicate members", path=str(path))
                        total = 0
                        for item in members:
                            total += item.file_size
                            mode = (item.external_attr >> 16) & 0o170000
                            if (item.file_size > MAX_MEMBER_BYTES or total > MAX_EXPANDED_BYTES or
                                    item.flag_bits & 1 or mode == stat.S_IFLNK or
                                    item.filename.startswith(("/", "\\")) or
                                    ".." in re.split(r"[/\\]", item.filename)):
                                api.fail("publish_export_invalid", "Archive has an unsafe or oversized member",
                                         path=str(path), member=item.filename)
                        try:
                            embedded_manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
                            receipt = json.loads(archive.read("receipt.json").decode("utf-8"))
                        except (ValueError, UnicodeError, TypeError) as error:
                            api.fail("publish_export_invalid", "Archive manifest or receipt cannot be read", reason=str(error))
                        if not isinstance(receipt, dict) or not isinstance(embedded_manifest, dict):
                            api.fail("publish_export_invalid", "Archive manifest and receipt must be objects")
                        plan_id = receipt.get("id")
                        if not isinstance(plan_id, str) or not re.fullmatch(r"[0-9a-f]{32}", plan_id):
                            api.fail("publish_export_invalid", "Archive has no valid publishing plan ID")
                        if plan_id != expected_id:
                            api.fail("publish_export_mismatch", "Archive belongs to a different publishing plan",
                                     expected=expected_id, actual=plan_id)
                        row = _plan(db, plan_id)
                        manifest = _validated_manifest(row, book.meta("id"))
                        if embedded_manifest != manifest or archive.read("manifest.json") != api.dumps(manifest).encode("utf-8"):
                            api.fail("publish_export_mismatch", "Archive manifest does not match this book's frozen plan", id=plan_id)
                        checked_revision = receipt.get("checked_revision")
                        if (type(checked_revision) is not int or checked_revision < manifest["source_revision"] or
                                checked_revision > 2**63 - 1):
                            api.fail("publish_export_invalid", "Archive has an invalid checked revision")
                        try:
                            for field in ("checked_at", "exported_at"):
                                value = datetime.fromisoformat(receipt[field])
                                if value.utcoffset() is None:
                                    raise ValueError("Timestamp must contain a timezone")
                        except (KeyError, TypeError, ValueError) as error:
                            api.fail("publish_export_invalid", "Archive has an invalid local check time", reason=str(error))
                        expected_receipt = {**_packet(row, book_id=book.meta("id")),
                                            "ok": True, "status": "prepared", "reasons": [],
                                            "checked_at": receipt["checked_at"],
                                            "source_check_performed": True, "source_matches_current": True,
                                            "checked_revision": checked_revision,
                                            "chapter_checks": [{"chapter": item["chapter"], "matches_current": True,
                                                                "changed_fields": []} for item in manifest["chapters"]],
                                            "exported_at": receipt["exported_at"]}
                        if receipt != expected_receipt:
                            api.fail("publish_export_mismatch", "Archive receipt does not match its frozen plan", id=plan_id)
                        expected = _material_entries(manifest, receipt)
                        if set(names) != set(expected):
                            api.fail("publish_export_invalid", "Archive has unexpected or missing members", id=plan_id)
                        for name, content in expected.items():
                            if archive.read(name) != content:
                                api.fail("publish_export_mismatch", "Archive member differs from its frozen plan",
                                         id=plan_id, member=name)
                current_reasons, chapter_checks = _source_comparison(book, manifest)
                # The ZIP is copied for safe parsing above, but the original path
                # must still name those exact bytes when this check returns.
                final_before = _bound_stat(file)
                if identity(final_before) != identity(before) or api._bound_hash(file) != archive_sha:
                    api.fail("unsafe_publish_path", "Export archive changed during verification", path=str(path))
                final_after = _bound_stat(file)
                api._verify_bound_directory(directory)
                if identity(final_after) != identity(before):
                    api.fail("unsafe_publish_path", "Export archive changed during verification", path=str(path))
                usable = row["status"] == "prepared" and not current_reasons
                return {**_packet(row, book_id=book.meta("id")), "ok": usable,
                        "artifact_verified": True, "archive": str(path), "archive_sha256": archive_sha,
                        "archive_exported_at": receipt["exported_at"],
                        "archive_bytes": size, "sha_check_performed": True,
                        "source_check_performed": True, "source_matches_current": not current_reasons,
                        "checked_revision": book.meta("revision"), "current_reasons": current_reasons,
                        "chapter_checks": chapter_checks, "usable_now": usable}
    except (OSError, zipfile.BadZipFile, RuntimeError, UnicodeError, zlib.error, EOFError, NotImplementedError) as error:
        api.fail("publish_export_invalid", "Could not read a verified chapter material archive",
                 path=str(path), reason=str(error))


def _saved_export_receipt(file, book, db=None):
    """Read one trusted-path local receipt; it is still not a platform receipt."""
    if not re.fullmatch(r"material-[0-9a-f]{32}\.receipt\.json", file.name):
        api.fail("publish_export_invalid", "Export receipt filename is not recognized", path=str(file))
    before = _bound_stat(file)
    if before.st_size > 8192:
        api.fail("publish_export_invalid", "Export receipt exceeds the supported size", path=str(file))
    with api._bound_reader(file) as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(8193)
        closed = os.fstat(stream.fileno())
    after = _bound_stat(file)
    if (len(raw) > 8192 or not os.path.samestat(before, opened) or
            not os.path.samestat(opened, closed) or not os.path.samestat(opened, after) or
            any((before.st_size != value.st_size or before.st_mtime_ns != value.st_mtime_ns)
                for value in (opened, closed, after))):
        api.fail("unsafe_publish_path", "Export receipt changed during read", path=str(file))
    try:
        record = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        api.fail("publish_export_invalid", "Export receipt is not valid UTF-8 JSON", path=str(file), reason=str(error))
    fields = {"schema", "book_id", "plan_id", "manifest_sha256", "archive", "sha256", "bytes", "exported_at"}
    archive = file.name.replace(".receipt.json", ".zip")
    try:
        stamp = datetime.fromisoformat(record["exported_at"])
    except (TypeError, ValueError, KeyError, AttributeError) as error:
        api.fail("publish_export_invalid", "Export receipt has an invalid time", path=str(file), reason=str(error))
    if (not isinstance(record, dict) or set(record) != fields or
            type(record["schema"]) is not int or record["schema"] != 1 or
            record["book_id"] != book.meta("id") or
            not isinstance(record["plan_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", record["plan_id"]) or
            not isinstance(record["manifest_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", record["manifest_sha256"]) or
            record["archive"] != archive or
            not isinstance(record["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) or
            type(record["bytes"]) is not int or not 0 < record["bytes"] <= MAX_ARCHIVE_BYTES or
            stamp.utcoffset() is None or raw != api.dumps(record).encode("utf-8")):
        api.fail("publish_export_mismatch", "Export receipt is not the expected record for this book and archive", path=str(file))
    if db is not None:
        row = _plan(db, record["plan_id"])
        if row["fingerprint"] != record["manifest_sha256"]:
            api.fail("publish_export_mismatch", "Export receipt plan fingerprint differs from the book ledger", path=str(file))
    return record


def list_exports(book, offset=0, limit=10, id=None, budget=DEFAULT_BUDGET):
    """Find saved receipts; listing does not authenticate ZIP bytes or recheck prose."""
    _book_kind(book)
    api.integer(offset, "offset")
    api.integer(limit, "limit", 1)
    if limit > 100:
        api.fail("invalid_input", "limit must be at most 100")
    if id is not None and (not isinstance(id, str) or not re.fullmatch(r"[0-9a-f]{32}", id)):
        api.fail("invalid_input", "--id must be a publishing plan ID")
    directory_path = api.safe_path(book.root, ".story/publishing-exports")
    with _ledger(book) as db:
        if not directory_path.exists():
            return api.bounded_packet({"ok": True, "book_id": book.meta("id"), "results": [],
                                       "total": 0, "offset": offset, "limit": limit, "next_offset": None,
                                       "invalid_receipts": [], "invalid_total": 0,
                                       "invalid_receipts_truncated": False,
                                       "archive_hash_checked": False, "source_check_performed": False}, budget)
        if db is None:
            api.fail("publishing_missing", "Export receipts exist without a publishing ledger")
        try:
            with api._pinned_directory(directory_path) as directory:
                names = sorted(name for name in os.listdir(directory.fd if os.name != "nt" else directory.path)
                               if re.fullmatch(r"material-[0-9a-f]{32}\.receipt\.json", name))
                if len(names) > 10000:
                    api.fail("publish_export_limit", "Too many local export receipts to list safely")
                records, invalid, invalid_total = [], [], 0
                for name in names:
                    file = api._BoundFile(directory, name)
                    try:
                        record = _saved_export_receipt(file, book, db)
                    except api.StoryError as error:
                        if error.code not in ("publish_export_invalid", "publish_export_mismatch", "publish_plan_missing"):
                            raise
                        invalid_total += 1
                        if len(invalid) < 10:
                            invalid.append({"receipt_path": str(file), "code": error.code})
                        continue
                    if id is not None and record["plan_id"] != id:
                        continue
                    zip_file = api._BoundFile(directory, record["archive"])
                    exists = _bound_stat(zip_file, missing=True)
                    records.append({"plan_id": record["plan_id"], "receipt_path": str(file),
                                    "archive_path": str(zip_file), "sha256": record["sha256"],
                                    "exported_at": record["exported_at"], "archive_exists": exists is not None,
                                    "archive_size_matches_receipt": None if exists is None else exists.st_size == record["bytes"]})
                api._verify_bound_directory(directory)
                records.sort(key=lambda item: (item["exported_at"], item["receipt_path"]), reverse=True)
                page = records[offset:offset + limit]
                return api.bounded_packet({"ok": True, "book_id": book.meta("id"), "results": page,
                                           "total": len(records), "offset": offset, "limit": limit,
                                           "next_offset": offset + len(page) if offset + len(page) < len(records) else None,
                                           "invalid_receipts": invalid, "invalid_total": invalid_total,
                                           "invalid_receipts_truncated": invalid_total > len(invalid),
                                           "archive_hash_checked": False, "source_check_performed": False}, budget)
        except OSError as error:
            api.fail("publish_export_invalid", "Could not list saved export receipts",
                     path=str(directory_path), reason=str(error))


def verify_export_receipt(book, receipt_path):
    """Resolve a saved local receipt, then run the full read-only ZIP check."""
    _book_kind(book)
    try:
        path = api.Path(receipt_path).expanduser()
    except (TypeError, ValueError, OSError) as error:
        api.fail("invalid_input", "Export receipt path is invalid", reason=str(error))
    if not path.is_absolute() or "\x00" in str(path):
        api.fail("invalid_input", "Export receipt path must be absolute")
    try:
        with api._pinned_directory(path.parent) as directory:
            file = api._BoundFile(directory, path.name)
            before = _bound_stat(file)
            record = _saved_export_receipt(file, book)
            expected_hash = hashlib.sha256(api.dumps(record).encode("utf-8")).hexdigest()

            def unchanged():
                current = _bound_stat(file)
                if (not os.path.samestat(before, current) or
                        current.st_size != before.st_size or
                        current.st_mtime_ns != before.st_mtime_ns or
                        current.st_ctime_ns != before.st_ctime_ns or
                        api._bound_hash(file) != expected_hash):
                    api.fail("unsafe_publish_path", "Export receipt changed during verification", path=str(path))
                after_hash = _bound_stat(file)
                if (not os.path.samestat(current, after_hash) or
                        after_hash.st_size != current.st_size or
                        after_hash.st_mtime_ns != current.st_mtime_ns or
                        after_hash.st_ctime_ns != current.st_ctime_ns):
                    api.fail("unsafe_publish_path", "Export receipt changed during verification", path=str(path))

            unchanged()
            api._verify_bound_directory(directory)
            archive = path.with_name(record["archive"])
            result = verify_export(book, archive, record["sha256"], record["plan_id"])
            if (result["manifest_sha256"] != record["manifest_sha256"] or
                    result["archive_bytes"] != record["bytes"] or
                    result["archive_exported_at"] != record["exported_at"]):
                api.fail("publish_export_mismatch", "Export receipt and archive do not describe the same saved material", path=str(path))
            unchanged()
            api._verify_bound_directory(directory)
            return {**result, "receipt_path": str(path)}
    except OSError as error:
        api.fail("publish_export_invalid", "Could not read the saved export receipt", path=str(path), reason=str(error))


def cancel(book, plan_id):
    _book_kind(book)
    with api.storage.operation_lock(book), _ledger(book, write=True) as db:
        row = _plan(db, plan_id)
        already = row["status"] == "cancelled"
        if not already:
            db.execute("UPDATE plans SET status='cancelled',checked_at=? WHERE id=? AND status IN ('prepared','stale')", (_now(), plan_id))
        return {**_packet(_plan(db, plan_id)), "ok": True, "idempotent": already}


def recover(book):
    """Recover SQLite transactions without needing a plan ID or changing plans."""
    _book_kind(book)
    with api.storage.operation_lock(book), _ledger(book, write=True) as db:
        return {"ok": True, "ledger_exists": db is not None, "book_id": book.meta("id"),
                "plans": _validate_plans(db) if db is not None else 0,
                "local_comparison_records": sum(1 for _ in _comparison_records(db, book.meta("id"))) if db is not None else 0,
                "remote_state": "unknown", "platform_verified": False, "ready_to_upload": False,
                "source_check_performed": False, "source_matches_current": None,
                "note": "Local ledger checked; no plan status or platform state was changed."}


def backup(book):
    _book_kind(book)
    with api.storage.operation_lock(book), _ledger(book) as db:
        if db is None:
            api.fail("publishing_missing", "There is no publishing ledger to back up")
        _validate_plans(db)
        comparison_count = sum(1 for _ in _comparison_records(db, book.meta("id")))
        target = api.safe_path(book.root, ".story/publishing-backups/" + uuid.uuid4().hex + ".sqlite3")
        with api._pinned_directory(target.parent, create=True) as directory:
            file = api._BoundFile(directory, target.name)
            _bound_stat(file, missing=True)
            for side in _sides(directory, file.name):
                if _bound_stat(side, missing=True) is not None:
                    api.fail("unsafe_publish_path", "Backup sidecars already exist")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = (os.open(file.path, flags, 0o600) if os.name == "nt" else os.open(file.name, flags, 0o600, dir_fd=directory.fd))
            os.close(fd)
            before = _bound_stat(file)
            connections, destination, done = ExitStack(), None, False
            try:
                api._verify_bound_directory(directory)
                destination = connections.enter_context(_connection(file, True))
                destination.execute("PRAGMA synchronous=FULL")
                if not os.path.samestat(before, _bound_stat(file)):
                    api.fail("unsafe_publish_path", "Backup destination changed during open")
                db.backup(destination)
                destination.row_factory = sqlite3.Row
                _validate_database(destination, book.meta("id"))
                _validate_plans(destination)
                if sum(1 for _ in _comparison_records(destination, book.meta("id"))) != comparison_count:
                    api.fail("backup_failed", "Publishing backup omitted local comparison records")
                if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    api.fail("backup_failed", "Publishing backup failed integrity validation")
                connections.close()
                destination = None
                api._verify_bound_directory(directory)
                if not os.path.samestat(before, _bound_stat(file)):
                    api.fail("unsafe_publish_path", "Backup destination changed during save")
                for side in _sides(directory, file.name):
                    _bound_stat(side, missing=True)
                done = True
                return {"ok": True, "backup": str(target), "schema": SCHEMA_VERSION, "book_id": book.meta("id"),
                        "sha256": api._bound_hash(file), "local_comparison_records": comparison_count,
                        "restore_supported": False,
                        "note": "Verified offline-ledger backup. It is not a full book backup or proof of remote state."}
            finally:
                connections.close()
                if not done:
                    current = _bound_stat(file, missing=True)
                    if current is not None and os.path.samestat(before, current):
                        api._bound_unlink(file)


def register_parser(sub, command):
    for name in sorted(COMMANDS):
        parser = command(name, "Prepare and inspect offline chapter publishing material; never uploads",
                         DEFAULT_BUDGET if name in ("publish-list", "publish-inspect", "publish-export-list",
                                                    "publish-compare", "publish-compare-record",
                                                    "publish-compare-history", "publish-compare-inspect") else None)
        if name == "publish-prepare":
            parser.add_argument("--input", required=True)
            parser.add_argument("--expect", required=True, type=int)
            parser.add_argument("--summary", action="store_true", help="Return compact metadata; full frozen content remains in the ledger")
        if name == "publish-inspect":
            view = parser.add_mutually_exclusive_group()
            view.add_argument("--summary", action="store_true", help="Read a page of chapter titles and counts without prose")
            view.add_argument("--chapter", type=int, help="Read exactly one complete frozen chapter")
            parser.add_argument("--offset", type=int, default=0, help="Chapter offset for --summary")
            parser.add_argument("--limit", type=int, default=20, help="Chapter page size for --summary, at most 100")
        if name in ("publish-compare", "publish-compare-record"):
            parser.add_argument("--id", required=True, help="Frozen publishing plan ID")
            parser.add_argument("--chapter", required=True, type=int, help="Chapter in the frozen plan")
            parser.add_argument("--input", required=True, help="JSON file containing copied title/body and optional author_note")
        if name == "publish-compare-inspect":
            parser.add_argument("--record-id", required=True, help="Saved local comparison record ID")
        if name == "publish-verify-export":
            parser.add_argument("--receipt", help="Absolute path to a saved local export receipt")
            parser.add_argument("--file", help="Absolute path to a saved chapter material ZIP")
            parser.add_argument("--sha256", help="Original export receipt SHA-256")
            parser.add_argument("--id", help="Expected publishing plan ID for --file")
        if name in ("publish-inspect", "publish-check", "publish-cancel", "publish-export"):
            parser.add_argument("--id", required=True)
        if name in ("publish-list", "publish-export-list", "publish-compare-history"):
            if name == "publish-export-list":
                parser.add_argument("--id", help="Filter saved export receipts by publishing plan ID")
            if name == "publish-compare-history":
                parser.add_argument("--id", required=True, help="Frozen publishing plan ID")
            parser.add_argument("--offset", type=int, default=0)
            parser.add_argument("--limit", type=int, default=10)


def run(book, args):
    if args.command == "publish-prepare":
        return prepare(book, api.read_json(args.input), args.expect, args.summary)
    if args.command == "publish-list":
        return list_plans(book, args.offset, args.limit, args.budget_bytes)
    if args.command == "publish-compare":
        return compare(book, args.id, args.chapter, api.read_json(args.input), args.budget_bytes)
    if args.command == "publish-compare-record":
        return record_compare(book, args.id, args.chapter, api.read_json(args.input), args.budget_bytes)
    if args.command == "publish-compare-history":
        return compare_history(book, args.id, args.offset, args.limit, args.budget_bytes)
    if args.command == "publish-compare-inspect":
        return inspect_comparison(book, args.record_id, args.budget_bytes)
    if args.command == "publish-export-list":
        return list_exports(book, args.offset, args.limit, args.id, args.budget_bytes)
    if args.command == "publish-inspect":
        return inspect(book, args.id, args.budget_bytes, summary=args.summary, chapter=args.chapter,
                       offset=args.offset, limit=args.limit)
    if args.command == "publish-check":
        return check(book, args.id)
    if args.command == "publish-export":
        return export_material(book, args.id)
    if args.command == "publish-verify-export":
        if args.receipt is not None:
            if any(value is not None for value in (args.file, args.id, args.sha256)):
                api.fail("invalid_input", "Choose --receipt or --file with --id and --sha256")
            return verify_export_receipt(book, args.receipt)
        if any(value is None for value in (args.file, args.id, args.sha256)):
            api.fail("invalid_input", "--file requires both the expected --id and original --sha256")
        return verify_export(book, args.file, args.sha256, args.id)
    if args.command == "publish-cancel":
        return cancel(book, args.id)
    if args.command == "publish-backup":
        return backup(book)
    if args.command == "publish-recover":
        return recover(book)
    raise AssertionError(args.command)
