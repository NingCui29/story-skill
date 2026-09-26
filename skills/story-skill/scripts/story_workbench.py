"""Read-only local author workbench snapshots and self-contained HTML export."""
from collections import Counter, deque
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import base64
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import uuid
import webbrowser
import secrets
import threading
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer


CONTRACT = "story.workbench.v1"
DEFAULT_BUDGET = 64000
DEFAULT_LIMIT = 10
MAX_LIMIT = 100
MAX_CHAPTERS = 20000
MAX_MANAGED_ARTIFACTS = 20000
MAX_RETIRED_EXPORTS = 20000
MAX_PUBLISH_PLANS = 5000
MAX_PUBLISH_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_PUBLISH_FILES = 20000
MAX_PUBLISH_RECEIPTS = 10000
WORLD_TABLES = (
    "entities", "volumes", "arcs", "lines", "facts", "knowledge", "hooks",
    "rules", "uses", "transfers", "arc_steps",
)


def inject(core):
    global api
    api = core


def register_parser(sub, command):
    snapshot_parser = command("workbench-snapshot", "Read one bounded, non-mutating local workbench snapshot",
                              DEFAULT_BUDGET, False)
    snapshot_parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    snapshot_parser.add_argument("--offset", type=int, default=0)
    snapshot_parser.add_argument("--cursor", help="Opaque cursor returned by an earlier snapshot")

    export_parser = command("workbench-export", "Generate a self-contained, read-only local author workbench",
                            DEFAULT_BUDGET, False)
    export_parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    export_parser.add_argument("--offset", type=int, default=0)
    export_parser.add_argument("--cursor", help="Opaque cursor returned by an earlier snapshot")
    mode = export_parser.add_mutually_exclusive_group()
    mode.add_argument("--include-text", action="store_true", dest="include_text", help="Use the default three-column reader")
    mode.add_argument("--overview-only", action="store_false", dest="include_text", help="Export a summary without embedding chapter text")
    export_parser.set_defaults(include_text=True)
    export_parser.add_argument("--output", help="HTML path inside .story/workbench; defaults to index.html")
    export_parser.add_argument("--open", action="store_true", dest="open_browser",
                               help="Open the generated file with the default browser")

    editor = command("workbench-serve", "Open a loopback editor that saves candidate drafts only", DEFAULT_BUDGET, False)
    editor.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    editor.add_argument("--library-book", action="append", default=[], help="Another explicitly selected book on the local shelf; repeat as needed")
    editor.add_argument("--open", action="store_true", dest="open_browser")
    command("workbench-status", "Check the registered local editor instance", DEFAULT_BUDGET, False)
    stop = command("workbench-stop", "Stop the registered editor after preserving edits in all its pages", DEFAULT_BUDGET, False)
    stop.add_argument("--saved", action="store_true", help="All editor pages have saved or downloaded their pending text")


class _ReadOnlyBook:
    """Minimal book facade backed by a descriptor-bound read-only SQLite connection."""
    fail = None
    safe_path = None

    def __init__(self, root):
        self.fail = api.fail
        self.safe_path = api.safe_path
        self.root = Path(root).expanduser().absolute()
        state = api.safe_path(self.root, ".story/state.sqlite3")
        if not state.is_file():
            api.fail("book_missing", "No Story Skill state here; initialize an explicit book directory",
                     book=str(self.root))
        self.path = state
        self._stack = ExitStack()
        try:
            directory = self._stack.enter_context(api._pinned_directory(state.parent))
            file = api._BoundFile(directory, state.name)
            self._state_file = file
            self._state_stat = api.publish._bound_stat(file)
            self.db = self._stack.enter_context(api.publish._connection(file, False))
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA query_only=ON")
            self.db.execute("PRAGMA foreign_keys=ON")
            schema = self.meta("schema")
            if type(schema) is not int:
                api.fail("state_corrupt", "Invalid book metadata", key="schema")
            if schema != api.SCHEMA_VERSION:
                api.fail("schema_mismatch", "Run migrate on a backed-up book copy before opening the workbench",
                         actual=schema, required=api.SCHEMA_VERSION)
        except sqlite3.Error as error:
            self._stack.close()
            api.fail("state_corrupt", "Invalid or incomplete Story Skill state", reason=str(error))
        except BaseException:
            self._stack.close()
            raise

    def close(self):
        self._stack.close()

    def meta(self, key):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if row is None:
            api.fail("state_corrupt", "Missing required metadata", key=key)
        try:
            return json.loads(row[0])
        except (ValueError, TypeError) as error:
            api.fail("state_corrupt", "Invalid book metadata", key=key, reason=str(error))

    def verify_state_path(self):
        try:
            current = api.publish._bound_stat(self._state_file)
        except OSError as error:
            api.fail("workbench_changed", "The book state path changed while the workbench was open",
                     path=str(self.path), reason=str(error))
        if not os.path.samestat(self._state_stat, current):
            api.fail("workbench_changed", "The book state path changed while the workbench was open",
                     path=str(self.path))

    @contextmanager
    def read_snapshot(self):
        owns = not self.db.in_transaction
        if owns:
            self.db.execute("BEGIN")
        try:
            yield
        finally:
            if owns:
                self.db.rollback()


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash_json(value):
    return api.digest(api.dumps(value))


def _validate_page(limit, offset):
    api.integer(limit, "limit", 1)
    api.integer(offset, "offset")
    if limit > MAX_LIMIT:
        api.fail("invalid_input", f"limit must be at most {MAX_LIMIT}")


def _decode_cursor(cursor):
    if cursor is None:
        return None, None
    if not isinstance(cursor, str):
        api.fail("invalid_input", "cursor must be text")
    match = re.fullmatch(r"([0-9a-f]{64}):([0-9]+)", cursor)
    if match is None:
        api.fail("invalid_input", "cursor is not a Story Skill workbench cursor")
    return match.group(1), int(match.group(2))


def _meta_map(db):
    result = {}
    required = ("id", "title", "kind", "revision", "last_chapter", "imported_through",
                "schema", "short_assembly_path")
    placeholders = ",".join("?" for _ in required)
    rows = db.execute(
        f"SELECT key,value FROM meta WHERE key IN ({placeholders}) "
        "OR key GLOB 'chapter_retired:*' OR key GLOB 'chapter_path:*'",
        required,
    )
    retired = 0
    paths = 0
    for row in rows:
        if row["key"].startswith("chapter_path:"):
            paths += 1
            if paths > MAX_CHAPTERS:
                api.fail("workbench_scan_limit", "Too many chapter paths for a bounded workbench snapshot",
                         maximum=MAX_CHAPTERS)
        if row["key"].startswith("chapter_retired:"):
            retired += 1
            if retired > MAX_RETIRED_EXPORTS:
                api.fail("workbench_scan_limit", "Too many retired exports for a bounded workbench snapshot",
                         maximum=MAX_RETIRED_EXPORTS)
        try:
            result[row["key"]] = json.loads(row["value"])
        except (ValueError, TypeError) as error:
            api.fail("state_corrupt", "Invalid book metadata", key=row["key"], reason=str(error))
    return result


def _state_text(metadata, key, maximum):
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        api.fail("state_corrupt", "Invalid book metadata", key=key)
    return value


def _state_integer(metadata, key, minimum=0, maximum=2**63 - 1):
    value = metadata.get(key)
    if type(value) is not int or not minimum <= value <= maximum:
        api.fail("state_corrupt", "Invalid book metadata", key=key)
    return value


def _record_text(value, field, maximum=2000, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        api.fail("state_corrupt", "Invalid stored workbench field", field=field)
    return value


def _record_integer(value, field, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        api.fail("state_corrupt", "Invalid stored workbench field", field=field)
    return value


def _record_sha(value, field, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        api.fail("state_corrupt", "Invalid stored workbench hash", field=field)
    return value


def _chapter_path(metadata, chapter):
    value = metadata.get(f"chapter_path:{chapter}", f"chapters/{chapter:04d}.md")
    if not isinstance(value, str) or not value or len(value) > 512:
        api.fail("state_corrupt", "Invalid stored chapter path", chapter=chapter)
    api.safe_path(Path(metadata["__root"]), value)
    return value


def _chapter_state_digest(db, metadata, total):
    if total > MAX_CHAPTERS:
        api.fail("workbench_scan_limit", "Too many chapters for a bounded workbench snapshot",
                 maximum=MAX_CHAPTERS, actual=total)
    digest = hashlib.sha256()
    seen = 0
    for row in db.execute(
            "SELECT c.chapter,c.sha,c.summary,c.imported,"
            "EXISTS(SELECT 1 FROM plans p WHERE p.chapter=c.chapter) AS has_plan "
            "FROM chapter_state c ORDER BY c.chapter"):
        chapter = _record_integer(row["chapter"], "chapter_state.chapter", 1)
        record = {
            "chapter": chapter,
            "path": _chapter_path(metadata, chapter),
            "summary": _record_text(row["summary"], "chapter_state.summary", empty=True),
            "imported": _record_integer(row["imported"], "chapter_state.imported", 0, 1),
            "has_plan": _record_integer(row["has_plan"], "chapter_state.has_plan", 0, 1),
            "sha256": _record_sha(row["sha"], "chapter_state.sha"),
        }
        raw = api.dumps(record).encode("utf-8")
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
        seen += 1
    if seen != total:
        api.fail("workbench_changed", "Chapter state changed while the workbench was captured")
    return digest.hexdigest()


def _core_capture(book, limit, offset):
    db = book.db
    metadata = _meta_map(db)
    for key in ("id", "title", "kind", "revision", "last_chapter", "imported_through", "schema"):
        if key not in metadata:
            api.fail("state_corrupt", "Missing required metadata", key=key)
    _state_text(metadata, "id", 200)
    _state_text(metadata, "title", 200)
    if metadata["kind"] not in ("long", "short", "analysis"):
        api.fail("state_corrupt", "Invalid book metadata", key="kind")
    for key in ("revision", "imported_through", "schema"):
        _state_integer(metadata, key)
    _state_integer(metadata, "last_chapter", maximum=2**63 - 2)
    if metadata["schema"] != api.SCHEMA_VERSION:
        api.fail("schema_mismatch", "Run migrate on a backed-up book copy before opening the workbench",
                 actual=metadata["schema"], required=api.SCHEMA_VERSION)
    if metadata["imported_through"] > metadata["last_chapter"]:
        api.fail("state_corrupt", "Imported chapter progress exceeds the saved chapter range")
    assembly = metadata.get("short_assembly_path")
    if assembly is not None:
        if not isinstance(assembly, str) or not assembly or len(assembly) > 512:
            api.fail("state_corrupt", "Invalid short-story assembly path")
        api.safe_path(book.root, assembly)
    metadata["__root"] = str(book.root)
    total = db.execute("SELECT count(*) FROM chapter_state").fetchone()[0]
    planned_total = db.execute("SELECT count(*) FROM plans").fetchone()[0]
    chapter_state_sha256 = _chapter_state_digest(db, metadata, total)
    rows = db.execute(
        "SELECT c.chapter,c.sha,c.summary,c.imported,"
        "EXISTS(SELECT 1 FROM plans p WHERE p.chapter=c.chapter) AS has_plan "
        "FROM chapter_state c ORDER BY c.chapter DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    chapters = []
    for row in rows:
        chapter = _record_integer(row["chapter"], "chapter_state.chapter", 1)
        imported = _record_integer(row["imported"], "chapter_state.imported", 0, 1)
        has_plan = _record_integer(row["has_plan"], "chapter_state.has_plan", 0, 1)
        chapters.append({
            "chapter": chapter, "path": _chapter_path(metadata, chapter),
            "summary": _record_text(row["summary"], "chapter_state.summary", empty=True),
            "imported": bool(imported), "has_plan": bool(has_plan),
            "sha256": _record_sha(row["sha"], "chapter_state.sha"),
        })

    card_total = db.execute("SELECT count(*) FROM card_index").fetchone()[0]
    cards_by_kind = {_record_text(row[0], "card_index.kind", 80): row[1] for row in
                     db.execute("SELECT kind,count(*) FROM card_index GROUP BY kind ORDER BY kind")}
    cards_by_status = {_record_text(row[0], "card_index.status", 80): row[1] for row in db.execute(
        "SELECT status,count(*) FROM card_index GROUP BY status ORDER BY status")}
    active_critical = db.execute(
        "SELECT count(*) FROM card_index WHERE status='active' AND critical=1").fetchone()[0]
    due_total = db.execute(
        "SELECT count(*) FROM card_index WHERE status='active' AND due IS NOT NULL AND due<=?",
        (metadata["last_chapter"] + 1,)).fetchone()[0]
    due_rows = db.execute(
        "SELECT id,kind,due,critical FROM card_index WHERE status='active' AND due IS NOT NULL AND due<=? "
        "ORDER BY due,id LIMIT 10", (metadata["last_chapter"] + 1,)).fetchall()
    due_cards = [{"id": _record_text(row["id"], "card_index.id", 80),
                  "kind": _record_text(row["kind"], "card_index.kind", 80),
                  "due": _record_integer(row["due"], "card_index.due", 1),
                  "critical": bool(_record_integer(
                      row["critical"], "card_index.critical", 0, 1))} for row in due_rows]

    world_counts = {}
    for name in WORLD_TABLES:
        world_counts[name] = db.execute(f"SELECT count(*) FROM world_{name}").fetchone()[0]
    retired = db.execute("SELECT count(*) FROM world_retirements").fetchone()[0]

    branch_counts = dict(db.execute(
        "SELECT status,count(*) FROM history_branches GROUP BY status ORDER BY status"))
    recent_branches = [{
        "id": _record_text(row["id"], "history_branches.id", 100),
        "chapter": _record_integer(row["target"], "history_branches.target", 1),
        "status": _record_text(row["status"], "history_branches.status", 80),
        "revision": _record_integer(row["revision"], "history_branches.revision"),
    } for row in db.execute(
        "SELECT id,target,status,revision FROM history_branches ORDER BY rowid DESC LIMIT 10")]
    unseeded = db.execute(
        "SELECT count(*) FROM chapter_state c LEFT JOIN history_heads h ON h.chapter=c.chapter "
        "WHERE h.chapter IS NULL").fetchone()[0]
    snapshots_total = db.execute("SELECT count(*) FROM history_snapshots").fetchone()[0]

    sources_total = db.execute("SELECT count(*) FROM sources").fetchone()[0]
    pending_sources = db.execute(
        "SELECT count(DISTINCT source) FROM chunks WHERE analysis IS NULL").fetchone()[0]
    recent_sources = []
    for row in db.execute(
            "SELECT s.id,s.name,s.coverage,count(c.ordinal) AS chunks_total,"
            "sum(CASE WHEN c.analysis IS NOT NULL THEN 1 ELSE 0 END) AS analyzed "
            "FROM sources s LEFT JOIN chunks c ON c.source=s.id GROUP BY s.id "
            "ORDER BY s.rowid DESC LIMIT 10"):
        recent_sources.append({"id": _record_text(row["id"], "sources.id", 1000),
                               "name": _record_text(row["name"], "sources.name", 1000),
                               "coverage": _record_text(row["coverage"], "sources.coverage", 1000),
                               "chunks_total": row["chunks_total"], "analyzed": row["analyzed"] or 0})

    managed_total = db.execute("SELECT count(*) FROM artifact_state").fetchone()[0]
    if managed_total > MAX_MANAGED_ARTIFACTS:
        api.fail("workbench_scan_limit", "Too many managed artifacts for a bounded workbench snapshot",
                 maximum=MAX_MANAGED_ARTIFACTS, actual=managed_total)
    managed = []
    for row in db.execute("SELECT path,sha,written_sha FROM artifact_state ORDER BY path"):
        managed.append({
            "path": _record_text(row["path"], "artifact_state.path", 512),
            "sha": _record_sha(row["sha"], "artifact_state.sha"),
            "written_sha": _record_sha(row["written_sha"], "artifact_state.written_sha", optional=True),
        })
    retired_exports = []
    for key, value in metadata.items():
        if not key.startswith("chapter_retired:"):
            continue
        if (not isinstance(value, dict) or not isinstance(value.get("path"), str) or
                not value["path"] or len(value["path"]) > 512):
            api.fail("state_corrupt", "Invalid retired chapter record", key=key)
        retired_exports.append({
            "path": value["path"],
            "sha": _record_sha(value.get("sha"), f"{key}.sha"),
            "written_sha": _record_sha(value.get("written_sha"), f"{key}.written_sha", optional=True),
            "retired": True,
        })

    core = {
        "book": {"id": metadata["id"], "root": str(book.root), "title": metadata["title"],
                 "kind": metadata["kind"], "revision": metadata["revision"]},
        "progress": {"last_chapter": metadata["last_chapter"],
                     "next_chapter": metadata["last_chapter"] + 1,
                     "imported_through": metadata["imported_through"]},
        "chapters": {"total": total, "planned_total": planned_total, "offset": offset,
                     "limit": limit, "returned": len(chapters), "results": chapters,
                     "next_cursor": None, "has_more": offset + len(chapters) < total,
                     "complete": offset == 0 and len(chapters) >= total},
        "cards": {"total": card_total, "by_kind": cards_by_kind, "by_status": cards_by_status,
                  "active_critical": active_critical, "due_or_overdue": due_cards,
                  "due_or_overdue_total": due_total,
                  "due_or_overdue_returned": len(due_cards),
                  "due_or_overdue_omitted": due_total - len(due_cards),
                  "next_cursor": None},
        "world": {"counts_by_kind": world_counts, "retired_count": retired,
                  "recent_refs": [], "contextual_current_state": "summary_only"},
        "history": {"unseeded_chapters": unseeded, "branch_counts_by_status": branch_counts,
                    "recent_branches": recent_branches, "snapshots_total": snapshots_total,
                    "next_cursor": None},
        "analysis": {"sources_total": sources_total, "pending_sources": pending_sources,
                     "recent_sources": recent_sources, "next_cursor": None},
        "managed": managed,
        "retired_exports": retired_exports,
        "next_plan_exists": db.execute("SELECT 1 FROM plans WHERE chapter=?",
                                       (metadata["last_chapter"] + 1,)).fetchone() is not None,
        "short_assembly_path": metadata.get("short_assembly_path"),
    }
    # The snapshot identity must remain stable while the caller follows a
    # pagination cursor.  Page rows, offsets and limits are response details;
    # the authoritative revision and page-independent summaries identify the
    # core state.
    identity_view = {key: value for key, value in core.items()
                     if key not in ("managed", "retired_exports", "fingerprint")}
    identity_view["chapters"] = {
        "total": core["chapters"]["total"],
        "planned_total": core["chapters"]["planned_total"],
        "state_sha256": chapter_state_sha256,
    }
    core["fingerprint"] = _hash_json(identity_view)
    return core


def _bound_file_fact(root, relative, expected_sha=None, written_sha=None):
    path = api.safe_path(root, relative)
    try:
        with api._pinned_directory(path.parent) as directory:
            file = api._BoundFile(directory, path.name)
            before = api.publish._bound_stat(file, missing=True)
            if before is None:
                return {"path": relative, "exists": False, "sha256": None, "bytes": None,
                        "expected_sha256": expected_sha, "written_sha256": written_sha}
            actual = api._bound_hash(file)
            after = api.publish._bound_stat(file)
            if (not os.path.samestat(before, after) or before.st_size != after.st_size or
                    before.st_mtime_ns != after.st_mtime_ns or before.st_ctime_ns != after.st_ctime_ns):
                api.fail("workbench_changed", "A displayed file changed while the workbench was captured",
                         path=str(path))
            return {"path": relative, "exists": True, "sha256": actual, "bytes": after.st_size,
                    "expected_sha256": expected_sha, "written_sha256": written_sha}
    except FileNotFoundError:
        return {"path": relative, "exists": False, "sha256": None, "bytes": None,
                "expected_sha256": expected_sha, "written_sha256": written_sha}


def _managed_file_capture(book, managed, retired):
    facts, pending, changed = [], [], []
    for record in managed:
        fact = _bound_file_fact(book.root, record["path"], record["sha"], record["written_sha"])
        facts.append(fact)
        actual = fact["sha256"]
        if actual not in {record["sha"], record["written_sha"]}:
            changed.append(record["path"])
        elif actual != record["sha"] or record["written_sha"] != record["sha"]:
            pending.append(record["path"])
    for record in retired:
        fact = _bound_file_fact(book.root, record["path"], record.get("sha"), record.get("written_sha"))
        fact["retired"] = True
        facts.append(fact)
        if fact["sha256"] not in {record.get("sha"), record.get("written_sha")}:
            changed.append(record["path"])
        else:
            pending.append(record["path"])
    return {"facts": facts, "pending": sorted(set(pending)), "changed": sorted(set(changed))}


def _material_names(directory, pattern, maximum):
    names = []
    location = directory.fd if os.name != "nt" else directory.path
    with os.scandir(location) as entries:
        for entry in entries:
            if not pattern.fullmatch(entry.name):
                continue
            names.append(entry.name)
            if len(names) > maximum:
                api.fail("workbench_scan_limit", "Too many publishing files for a bounded workbench snapshot",
                         maximum=maximum)
    return sorted(names)


def _publishing_directory_capture(book):
    path = api.safe_path(book.root, ".story/publishing-exports")
    if not path.exists():
        empty = {"exists": False, "entries": []}
        empty["sha256"] = _hash_json(empty)
        return empty
    try:
        with api._pinned_directory(path) as directory:
            names = _material_names(
                directory, re.compile(r"material-[0-9a-f]{32}\.(?:zip|receipt\.json)"),
                MAX_PUBLISH_FILES)
            entries = []
            for name in names:
                file = api._BoundFile(directory, name)
                value = api.publish._bound_stat(file, missing=True)
                if value is None:
                    entries.append({"name": name, "exists": False})
                    continue
                entries.append({
                    "name": name, "exists": True, "bytes": value.st_size,
                    "mtime_ns": value.st_mtime_ns, "ctime_ns": value.st_ctime_ns,
                    "device": value.st_dev, "inode": value.st_ino,
                })
            api._verify_bound_directory(directory)
    except FileNotFoundError:
        entries = []
        exists = False
    else:
        exists = True
    result = {"exists": exists, "entries": entries}
    result["sha256"] = _hash_json(result)
    return result


def _publishing_plan_capture(db, book_id, limit):
    if db is None:
        return {"plans_total": 0, "status_counts": {}, "recent_plans": [],
                "fingerprints": {}, "sha256": _hash_json([])}
    total = db.execute("SELECT count(*) FROM plans").fetchone()[0]
    if total > MAX_PUBLISH_PLANS:
        api.fail("workbench_scan_limit", "Too many publishing plans for a bounded workbench snapshot",
                 maximum=MAX_PUBLISH_PLANS, actual=total)
    manifest_bytes = db.execute(
        "SELECT COALESCE(sum(length(CAST(manifest AS BLOB))),0) FROM plans"
    ).fetchone()[0]
    manifest_bytes = _record_integer(
        manifest_bytes, "publishing.plans.manifest_bytes", 0)
    if manifest_bytes > MAX_PUBLISH_MANIFEST_BYTES:
        api.fail("workbench_scan_limit",
                 "Publishing plan manifests are too large for a bounded workbench snapshot",
                 maximum_bytes=MAX_PUBLISH_MANIFEST_BYTES, actual_bytes=manifest_bytes)
    status_counts = Counter()
    recent = deque(maxlen=limit)
    fingerprints = {}
    digest = hashlib.sha256()
    seen = 0
    for row in db.execute(
            "SELECT * FROM plans ORDER BY created,id"):
        plan_id = _record_text(row["id"], "publishing.plans.id", 32)
        if re.fullmatch(r"[0-9a-f]{32}", plan_id) is None:
            api.fail("publishing_corrupt", "Invalid publishing plan identity", id=plan_id)
        manifest = api.publish._validated_manifest(row, book_id)
        manifest_raw = row["manifest"]
        if not isinstance(manifest_raw, str) or manifest_raw != api.dumps(manifest):
            api.fail("publishing_corrupt", "Publishing plan manifest is not canonical", id=plan_id)
        fingerprint = _record_sha(row["fingerprint"], "publishing.plans.fingerprint")
        created = _record_text(row["created"], "publishing.plans.created", 100)
        checked_at = row["checked_at"]
        if checked_at is not None:
            checked_at = _record_text(checked_at, "publishing.plans.checked_at", 100)
        status = _record_text(row["status"], "publishing.plans.status", 20)
        if status not in ("prepared", "stale", "cancelled"):
            api.fail("publishing_corrupt", "Invalid publishing plan status", id=plan_id)
        reasons_raw = _record_text(row["reasons"], "publishing.plans.reasons", 65536)
        try:
            reasons = json.loads(reasons_raw)
        except (ValueError, TypeError) as error:
            api.fail("publishing_corrupt", "Invalid publishing plan reasons", id=plan_id,
                     reason=str(error))
        if (not isinstance(reasons, list) or
                any(not isinstance(item, dict) or not isinstance(item.get("code"), str) or
                    not item["code"].strip() for item in reasons)):
            api.fail("publishing_corrupt", "Invalid publishing plan reasons", id=plan_id)
        if plan_id in fingerprints:
            api.fail("publishing_corrupt", "Duplicate publishing plan identity", id=plan_id)
        fingerprints[plan_id] = fingerprint
        summary = {"id": plan_id, "fingerprint": fingerprint, "created": created,
                   "status": status, "reasons": reasons, "checked_at": checked_at}
        raw = api.dumps(summary).encode("utf-8")
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
        manifest_raw = manifest_raw.encode("utf-8")
        digest.update(len(manifest_raw).to_bytes(8, "big"))
        digest.update(manifest_raw)
        status_counts[status] += 1
        recent.append(summary)
        seen += 1
    if seen != total:
        api.fail("workbench_changed", "Publishing plans changed while the workbench was captured")
    return {
        "plans_total": total,
        "status_counts": dict(status_counts),
        "recent_plans": [{"id": row["id"], "status": row["status"],
                          "created": row["created"], "checked_at": row["checked_at"],
                          "manifest_sha256": row["fingerprint"]}
                         for row in reversed(recent)],
        "fingerprints": fingerprints,
        "sha256": digest.hexdigest(),
    }


def _publishing_export_capture(book, db, fingerprints, limit):
    path = api.safe_path(book.root, ".story/publishing-exports")
    if not path.exists():
        return {"export_receipts_total": 0, "recent_exports": [], "invalid_exports": 0,
                "archives_missing": 0, "archives_size_mismatch": 0,
                "archive_hash_checked": False}
    if db is None:
        api.fail("publishing_corrupt", "Export receipts exist without a publishing ledger")
    try:
        with api._pinned_directory(path) as directory:
            names = _material_names(
                directory, re.compile(r"material-[0-9a-f]{32}\.receipt\.json"),
                MAX_PUBLISH_RECEIPTS)
            records, invalid = [], 0
            missing, mismatched = 0, 0
            for name in names:
                file = api._BoundFile(directory, name)
                try:
                    record = api.publish._saved_export_receipt(file, book)
                except api.StoryError as error:
                    if error.code not in ("publish_export_invalid", "publish_export_mismatch",
                                          "publish_plan_missing"):
                        raise
                    invalid += 1
                    continue
                expected = fingerprints.get(record["plan_id"])
                if expected is None or expected != record["manifest_sha256"]:
                    invalid += 1
                    continue
                archive = api._BoundFile(directory, record["archive"])
                stat = api.publish._bound_stat(archive, missing=True)
                exists = stat is not None
                size_matches = None if stat is None else stat.st_size == record["bytes"]
                missing += not exists
                mismatched += exists and not size_matches
                records.append({"plan_id": record["plan_id"], "archive": record["archive"],
                                "exported_at": record["exported_at"],
                                "archive_exists": exists,
                                "archive_size_matches_receipt": size_matches})
            api._verify_bound_directory(directory)
    except FileNotFoundError:
        return {"export_receipts_total": 0, "recent_exports": [], "invalid_exports": 0,
                "archives_missing": 0, "archives_size_mismatch": 0,
                "archive_hash_checked": False}
    records.sort(key=lambda item: (item["exported_at"], item["archive"]), reverse=True)
    return {"export_receipts_total": len(records), "recent_exports": records[:limit],
            "invalid_exports": invalid, "archives_missing": missing,
            "archives_size_mismatch": mismatched, "archive_hash_checked": False}


def _publishing_capture(book, limit):
    if book.meta("kind") == "analysis":
        empty = {"ledger_exists": False, "plans_total": 0,
                 "status_counts": {}, "recent_plans": [], "export_receipts_total": 0,
                 "recent_exports": [], "invalid_exports": 0, "archives_missing": 0,
                 "archives_size_mismatch": 0, "archive_hash_checked": False,
                 "directory_manifest_sha256": _hash_json({"exists": False, "entries": []})}
        empty["fingerprint"] = _hash_json({
            "ledger_exists": False,
            "directory_manifest_sha256": empty["directory_manifest_sha256"],
        })
        return empty
    with api.publish._ledger(book) as db:
        ledger_exists = db is not None
        plans = _publishing_plan_capture(db, book.meta("id"), limit)
        exports = _publishing_export_capture(book, db, plans["fingerprints"], limit)
    directory = _publishing_directory_capture(book)
    result = {"ledger_exists": ledger_exists,
            "plans_total": plans["plans_total"], "status_counts": plans["status_counts"],
            "recent_plans": plans["recent_plans"], **exports,
            "directory_manifest_sha256": directory["sha256"]}
    result["fingerprint"] = _hash_json({
        "ledger_exists": ledger_exists, "plans_sha256": plans["sha256"],
        "export_receipts_total": result["export_receipts_total"],
        "invalid_exports": result["invalid_exports"],
        "archives_missing": result["archives_missing"],
        "archives_size_mismatch": result["archives_size_mismatch"],
        "directory_manifest_sha256": directory["sha256"],
    })
    return result


def _short_assembly(core, managed_capture):
    path = core.get("short_assembly_path")
    if core["book"]["kind"] != "short" or not path:
        return None
    fact = next((item for item in managed_capture["facts"] if item["path"] == path), None)
    if fact is None:
        return {"path": path, "state": "unregistered"}
    if not fact["exists"]:
        state = "missing"
    elif fact["sha256"] != fact["expected_sha256"]:
        state = "changed"
    elif fact["written_sha256"] != fact["expected_sha256"]:
        state = "pending"
    else:
        state = "current"
    return {"path": path, "state": state}


def _attention(core, managed_capture, publishing):
    items = []
    if managed_capture["changed"]:
        items.append({"code": "external_changes", "level": "required",
                      "message": f"有 {len(managed_capture['changed'])} 个正式文件与保存版本不同。",
                      "action": "先查看差异并决定采用哪一版。"})
    if managed_capture["pending"]:
        items.append({"code": "exports_pending", "level": "required",
                      "message": f"有 {len(managed_capture['pending'])} 个正式文件等待同步。",
                      "action": "运行安全导出恢复。"})
    if core["cards"]["due_or_overdue_total"]:
        items.append({"code": "due_story_items", "level": "review",
                      "message": f"有 {core['cards']['due_or_overdue_total']} 项故事事项已到处理章节。",
                      "action": "写下一章前逐项核对。"})
    if publishing["invalid_exports"]:
        items.append({"code": "invalid_publish_receipts", "level": "required",
                      "message": f"有 {publishing['invalid_exports']} 份本地材料回执无法读取。",
                      "action": "从仍有效的清单重新导出。"})
    if publishing["archives_missing"]:
        items.append({"code": "publish_archives_missing", "level": "required",
                      "message": f"有 {publishing['archives_missing']} 份导出回执找不到对应 ZIP。",
                      "action": "不要据此投稿；重新核验并导出材料包。"})
    if publishing["archives_size_mismatch"]:
        items.append({"code": "publish_archives_size_mismatch", "level": "required",
                      "message": f"有 {publishing['archives_size_mismatch']} 份 ZIP 大小与导出回执不符。",
                      "action": "不要使用这些 ZIP；重新核验并导出材料包。"})
    items.append({"code": "author_artifacts_unregistered", "level": "info",
                  "message": "草稿、候选稿、可读大纲和封面尚未登记。",
                  "action": "工作台不会根据文件名或修改时间猜测当前版本。"})
    return items


def _capture_once(book, limit, offset):
    verify_state_path = getattr(book, "verify_state_path", None)
    if verify_state_path is not None:
        verify_state_path()
    data_version_before = book.db.execute("PRAGMA data_version").fetchone()[0]
    publishing_before = _publishing_capture(book, limit)
    with book.read_snapshot():
        core = _core_capture(book, limit, offset)
        managed_first = _managed_file_capture(book, core["managed"], core["retired_exports"])
        managed_second = _managed_file_capture(book, core["managed"], core["retired_exports"])
    publishing_after = _publishing_capture(book, limit)
    data_version_after = book.db.execute("PRAGMA data_version").fetchone()[0]
    if verify_state_path is not None:
        verify_state_path()
    if (data_version_before != data_version_after or
            managed_first["facts"] != managed_second["facts"] or
            publishing_before["fingerprint"] != publishing_after["fingerprint"] or
            publishing_before["directory_manifest_sha256"] !=
            publishing_after["directory_manifest_sha256"]):
        return None
    filesystem_manifest = _hash_json({"managed": managed_second["facts"],
                                      "publishing_exports":
                                      publishing_after["directory_manifest_sha256"]})
    identity = {"core_revision": core["book"]["revision"],
                "core_fingerprint": core["fingerprint"],
                "publishing_fingerprint": publishing_after["fingerprint"],
                "filesystem_manifest_sha256": filesystem_manifest}
    identity["id"] = _hash_json({"book_id": core["book"]["id"], **identity})
    return core, managed_second, publishing_after, identity


def snapshot(book, limit=DEFAULT_LIMIT, budget=DEFAULT_BUDGET, offset=0, cursor=None):
    expected_snapshot, cursor_offset = _decode_cursor(cursor)
    if cursor_offset is not None:
        if offset not in (0, cursor_offset):
            api.fail("invalid_input", "Do not combine cursor with a different offset")
        offset = cursor_offset
    _validate_page(limit, offset)
    captured = None
    try:
        for _ in range(3):
            captured = _capture_once(book, limit, offset)
            if captured is not None:
                break
    except (sqlite3.Error, ValueError, TypeError) as error:
        api.fail("state_corrupt", "Invalid or incomplete Story Skill state", reason=str(error))
    if captured is None:
        api.fail("workbench_changed", "Book files or state kept changing; retry the workbench snapshot")
    core, managed, publishing, identity = captured
    if expected_snapshot is not None and expected_snapshot != identity["id"]:
        api.fail("stale_snapshot", "The book changed after the previous workbench page; refresh from the first page",
                 expected=expected_snapshot, actual=identity["id"])
    next_offset = offset + len(core["chapters"]["results"])
    core["chapters"]["next_cursor"] = (f"{identity['id']}:{next_offset}"
                                                  if core["chapters"]["has_more"] else None)
    exports = {"pending_count": len(managed["pending"]),
               "changed_count": len(managed["changed"]),
               "examples": ([{"path": path, "state": "changed"} for path in managed["changed"][:5]] +
                            [{"path": path, "state": "pending"} for path in managed["pending"][:5]])[:10]}
    packet = {
        "contract": CONTRACT, "captured_at": _utc_now(), "snapshot": identity,
        "book": core["book"],
        "progress": {**core["progress"], "short_assembly": _short_assembly(core, managed),
                     "exports": exports, "next_plan_exists": core["next_plan_exists"]},
        "chapters": core["chapters"],
        "artifacts": {"registry_exists": False, "recent": [], "next_cursor": None},
        "cards": core["cards"], "world": core["world"], "history": core["history"],
        "analysis": core["analysis"],
        "publish": {"record_exists": publishing["ledger_exists"],
                    "plans_total": publishing["plans_total"],
                    "status_counts": publishing["status_counts"],
                    "recent_plans": publishing["recent_plans"],
                    "export_receipts_total": publishing["export_receipts_total"],
                    "recent_exports": publishing["recent_exports"],
                    "invalid_exports": publishing["invalid_exports"],
                    "archives_missing": publishing["archives_missing"],
                    "archives_size_mismatch": publishing["archives_size_mismatch"],
                    "archive_hash_checked": publishing["archive_hash_checked"],
                    "source_check_performed": False, "source_matches_current": None,
                    "next_cursor": None},
        "attention": _attention(core, managed, publishing),
        "completeness": {"chapters": "complete" if core["chapters"]["complete"] else "paged",
                         "cards": "summary", "world": "summary", "history": "summary",
                         "analysis": "summary", "publish": "saved_state_only",
                         "drafts": "unregistered", "candidates": "unregistered",
                         "readable_outlines": "unregistered",
                         "covers_and_other_artifacts": "unregistered"},
    }
    return api.bounded_packet(packet, budget)


def _escape(value):
    return html.escape(str(value), quote=True)


def _author_next(packet):
    exports = packet["progress"]["exports"]
    if exports["changed_count"]:
        return "先处理正式文件与保存版本的差异", "查看“需要你处理”中的文件，再决定保留哪一版。"
    if exports["pending_count"]:
        return "恢复待同步的正式文件", "完成安全导出后再继续写作。"
    chapter = packet["progress"]["next_chapter"]
    planned = packet["progress"]["next_plan_exists"]
    if planned:
        return f"继续写第{chapter}章", "下一章已有工具计划；写前仍需核对已采用细纲。"
    if packet["book"]["kind"] == "analysis":
        return "继续作品分析", "按尚未分析的来源片段继续，并保留原文证据。"
    return f"先补第{chapter}章计划", "工作台尚未找到下一章的工具计划。"


def _reading_text(book, packet):
    """Read only registered, unchanged chapters with a bounded total payload."""
    result = {}
    remaining = 8 * 1024 * 1024
    for row in packet["chapters"]["results"]:
        path = api.safe_path(book.root, row["path"])
        try:
            with api._pinned_directory(path.parent) as directory:
                with api._bound_reader(api._BoundFile(directory, path.name)) as stream:
                    raw = stream.read(min(remaining, 1024 * 1024) + 1)
            if len(raw) > min(remaining, 1024 * 1024):
                api.fail("workbench_read_limit", "Chapter reading payload exceeds the bounded limit")
            remaining -= len(raw)
            if hashlib.sha256(raw).hexdigest() != row["sha256"]:
                api.fail("workbench_changed", "Chapter differs from its registered text", path=row["path"])
            result[row["chapter"]] = raw.decode("utf-8")
        except (OSError, UnicodeError) as error:
            api.fail("workbench_read_failed", str(error), path=row["path"])
    return result


def _body_without_heading(text, chapter, title):
    lines = text.splitlines(keepends=True)
    if not lines:
        return "", text
    first = lines[0].lstrip('\ufeff').strip()
    # Only remove an exact opening heading, never mentions within the story.
    first = re.sub(r"^#{1,6}\s+", "", first)
    if first != title or not re.match(rf"^第0*{chapter}章(?:\s|$)", first):
        return "", text
    end = 1
    while end < len(lines) and not lines[end].strip():
        end += 1
    return "".join(lines[:end]), "".join(lines[end:])


def _render_desk(packet, reading):
    book = packet["book"]
    rows = sorted(packet["chapters"]["results"], key=lambda row: row["chapter"])
    groups, articles, notes = {}, [], []
    for row in rows:
        number, path = row["chapter"], Path(row["path"])
        groups.setdefault(str(path.parent), []).append(
            f'<a class="chapter-link" href="#chapter-{number}">{_escape(path.stem)}</a>')
        articles.append(f'<article id="chapter-{number}" hidden><div class="eyebrow">正式正文 · 只读</div>'
                        f'<h1>{_escape(path.stem)}</h1><pre>{_escape(_body_without_heading(reading[number], number, path.stem)[1])}</pre></article>')
        notes.append(f'<section data-note="chapter-{number}" hidden><h3>本章摘要</h3>'
                     f'<p>{_escape(row["summary"]) or "尚无摘要"}</p>'
                     f'<h3>原稿</h3><a href="{_escape((Path(book["root"]) / path).as_uri())}">打开章节文件</a></section>')
    navigation = "".join(f'<details open><summary>{_escape(folder)}</summary>{"".join(items)}</details>'
                         for folder, items in groups.items())
    title, note = _author_next(packet)
    attention = "".join(f'<p>{_escape(item["message"])}<br>{_escape(item["action"])}</p>' for item in packet["attention"])
    total = packet["chapters"]["total"]
    coverage = (f"已载入全部 {total} 章" if packet["chapters"]["complete"] else
                f"已载入 {len(rows)} / {total} 章，本页并非全书")
    if rows:
        coverage += f" · 第{rows[0]['chapter']}—{rows[-1]['chapter']}章"
    publishing = packet["publish"]
    delivery = (f"本地准备记录 {publishing['plans_total']} 份；导出回执 {publishing['export_receipts_total']} 份。"
                if publishing["record_exists"] else "尚无本地投稿材料记录。")
    export_state = packet["progress"]["exports"]
    delivery += f" 正式文件待同步 {export_state['pending_count']} 项，外部修改 {export_state['changed_count']} 项。"
    script = """const links=[...document.querySelectorAll('.chapter-link')];
const positions=new Map();let current=null;const pane=document.querySelector('.manuscript');
const previous=document.querySelector('#previous'),next=document.querySelector('#next');
function select(){let id=location.hash.slice(1);const missing=!!id&&id!=='overview'&&!links.some(a=>a.hash==='#'+id);document.querySelector('#missing-chapter').hidden=!missing;if(missing)id='overview';if(!id)id=links[0]?.hash.slice(1)||'overview';
if(current)positions.set(current,pane.scrollTop);current=id;
document.querySelectorAll('article').forEach(e=>e.hidden=e.id!==id);
document.querySelectorAll('[data-note]').forEach(e=>e.hidden=e.dataset.note!==id);
links.forEach(a=>{const on=a.hash==='#'+id;a.classList.toggle('selected',on);if(on)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
const overview=document.querySelector('.overview-link');if(id==='overview')overview.setAttribute('aria-current','page');else overview.removeAttribute('aria-current');
const index=links.findIndex(a=>a.hash==='#'+id);previous.disabled=index<=0;next.disabled=index<0||index>=links.length-1;
document.querySelector('#reading-position').textContent=index<0?'作品概览':`本页第 ${index+1} / ${links.length} 章`;
document.querySelector('#overview').hidden=id!=='overview';pane.scrollTop=positions.get(id)||0;}
window.addEventListener('hashchange',select);
previous.addEventListener('click',()=>{const i=links.findIndex(a=>a.hash==='#'+current);if(i>0)location.hash=links[i-1].hash;});
next.addEventListener('click',()=>{const i=links.findIndex(a=>a.hash==='#'+current);if(i>=0&&i<links.length-1)location.hash=links[i+1].hash;});
document.querySelector('#chapter-search').addEventListener('input',event=>{const query=event.target.value.trim().toLocaleLowerCase();let count=0;
links.forEach(a=>{a.hidden=!a.textContent.toLocaleLowerCase().includes(query);if(!a.hidden)count++;});
document.querySelectorAll('nav details').forEach(group=>{group.hidden=![...group.querySelectorAll('.chapter-link')].some(a=>!a.hidden);if(query)group.open=true;});
document.querySelector('#search-empty').hidden=count>0;});
select();
document.querySelector('#toggle-context').addEventListener('click',()=>{const closed=document.body.classList.toggle('context-closed');document.querySelector('#toggle-context').setAttribute('aria-expanded',String(!closed));});"""
    script_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-{script_hash}'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{_escape(book["title"])} · Story Skill</title><style>
.reading-column{{min-width:0;min-height:0;display:flex;flex-direction:column}}.reading-column main{{flex:1;min-height:0}}.reader-controls{{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:10px 20px;border-bottom:1px solid #e3e7ee;background:white;font-size:12px;color:#7b8799}}button:disabled{{opacity:.4;cursor:default}}#chapter-search{{width:100%;padding:10px;border:1px solid #dce2eb;border-radius:7px;margin:8px 0 18px;font:inherit}}a:focus-visible,button:focus-visible,input:focus-visible{{outline:2px solid #476aab;outline-offset:2px}}*{{box-sizing:border-box}}[hidden]{{display:none!important}}body{{margin:0;color:#243047;background:#fafbfc;font:14px/1.7 system-ui,-apple-system,"PingFang SC",sans-serif}}a{{color:#476aab;text-decoration:none}}button{{font:inherit;cursor:pointer;background:white;border:1px solid #dce2eb;border-radius:7px;padding:6px 12px;color:#46556e}}header{{height:66px;border-bottom:1px solid #e3e7ee;display:flex;align-items:center;justify-content:space-between;padding:0 24px;background:#fff;gap:16px}}header strong{{font-size:16px}}.brand{{color:#70819d;font-size:12px;margin-right:24px}}.desk{{display:grid;grid-template-columns:260px minmax(0,1fr) 280px;height:calc(100vh - 66px)}}nav,aside{{padding:24px 18px;overflow:auto;background:#f6f8fb}}nav{{border-right:1px solid #e3e7ee}}aside{{border-left:1px solid #e3e7ee}}.manuscript{{overflow:auto;padding:48px clamp(24px,5vw,88px);background:white}}h1{{font-size:25px;line-height:1.5;margin:12px 0 32px}}h2{{font-size:19px}}h3{{font-size:13px;color:#748096;margin:24px 0 8px}}p{{overflow-wrap:anywhere}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:19px/2.05 "Songti SC","SimSun",serif;color:#303743;margin:0;max-width:800px}}summary{{cursor:pointer;color:#7b8799;font-size:12px;padding:16px 8px 8px;overflow-wrap:anywhere}}.chapter-link{{display:block;padding:10px 12px;border-radius:7px;color:#526078;overflow-wrap:anywhere}}.chapter-link:hover{{background:#edf1f7}}.selected{{background:#e7eefb!important;color:#315fa5;font-weight:600}}.eyebrow,.muted{{font-size:12px;color:#8894a6}}.overview-link{{display:block;padding:10px 12px;border-bottom:1px solid #e1e6ef;margin-bottom:8px}}.context-closed .desk{{grid-template-columns:260px minmax(0,1fr)}}.context-closed aside{{display:none}}article{{max-width:850px;margin:auto}}.status{{color:#5f8974;font-size:12px}}@media(max-width:1000px){{.desk{{grid-template-columns:210px minmax(0,1fr) 220px}}.manuscript{{padding:30px 24px}}}}@media(max-width:760px){{#toggle-context{{display:none}}.reader-controls{{padding:8px;flex-wrap:wrap}}.desk,.context-closed .desk{{grid-template-columns:160px minmax(0,1fr)}}aside{{display:none}}header{{padding:0 12px}}.brand{{display:none}}nav{{padding:14px 8px}}pre{{font-size:17px}}}}
</style></head><body><header><div><span class="brand">STORY SKILL / 写作工作台</span><strong>{_escape(book["title"])}</strong></div><div><span class="status">本地只读</span> <button id="toggle-context" aria-expanded="true">章节信息</button></div></header>
<noscript><p>正文切换需要启用 JavaScript；可直接打开书目录中的章节原稿。</p></noscript><div class="desk"><nav aria-label="作品目录"><a class="overview-link" href="#overview">作品概览</a><label class="eyebrow" for="chapter-search">搜索本页章节</label><input id="chapter-search" type="search" placeholder="输入章名或编号"><p id="search-empty" hidden role="status">未找到匹配章节</p><div class="eyebrow">正文目录 · 本页 {len(rows)} 章</div>{navigation or '<p>尚无正式章节</p>'}<p class="muted">{_escape(coverage)}</p></nav>
<div class="reading-column"><div class="reader-controls"><button id="previous" disabled>上一章</button><span id="reading-position" aria-live="polite"></span><button id="next" disabled>下一章</button></div><main class="manuscript"><p id="missing-chapter" hidden role="status">该章节未包含在此页面中，请从左侧选择已载入章节。</p>{"".join(articles)}<section id="overview" hidden><h1>作品概览</h1><h2>{_escape(title)}</h2><p>{_escape(note)}</p><p>已正式保存 {total} 章</p><p>{_escape(coverage)}</p><h2>需要处理</h2>{attention or "<p>当前没有待处理提示。</p>"}<h2>交付准备</h2><p>{_escape(delivery)}</p><p class="muted">这里只显示本地记录；未核验 ZIP 内容，也不代表平台已保存、审核或上线。</p><p class="muted">生成于 {_escape(packet["captured_at"])}。原稿修改后需重新导出。</p></section></main></div>
<aside aria-label="章节信息"><div class="eyebrow">章节信息</div>{"".join(notes)}<h3>阅读说明</h3><p class="muted">正文为生成时核对的正式稿副本。本页不提供编辑保存。</p><p class="muted">细纲与人物资料尚未接入。</p></aside></div><script>{script}</script></body></html>'''


def render_html(packet, reading=None):
    if reading is not None:
        return _render_desk(packet, reading)
    title, note = _author_next(packet)
    book = packet["book"]
    progress = packet["progress"]
    attention = "".join(
        f'<li><strong>{_escape(item["message"])}</strong><span>{_escape(item["action"])}</span></li>'
        for item in packet["attention"])
    chapters = "".join(
        '<li><div><strong>第{chapter}章</strong><span>{summary}</span></div>'
        '<a href="{uri}">打开正文</a></li>'.format(
            chapter=row["chapter"], summary=_escape(row["summary"]),
            uri=_escape((Path(book["root"]) / row["path"]).absolute().as_uri()))
        for row in packet["chapters"]["results"])
    reader = ""
    if not chapters:
        chapters = '<li><div><strong>尚无正式章节</strong><span>完成规划和审查后，正式章会出现在这里。</span></div></li>'
    publish_text = (f'本地已有 {packet["publish"]["plans_total"]} 份准备记录、'
                    f'{packet["publish"]["export_receipts_total"]} 份导出回执。'
                    if packet["publish"]["record_exists"] else "尚无本地投稿材料记录。")
    kind = {"long": "长篇", "short": "短篇", "analysis": "作品分析"}.get(book["kind"], book["kind"])
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'">
<title>{_escape(book["title"])} · 本地作者工作台</title>
<style>
:root{{--ink:#201f1d;--muted:#68645e;--paper:#f5f1e9;--card:#fffdf8;--accent:#b84e2b;--line:#ded5c8}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.65 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
main{{max-width:1040px;margin:auto;padding:42px 24px 72px}} header{{margin-bottom:28px}} h1{{font-size:32px;margin:0 0 6px}} h2{{font-size:18px;margin:0 0 14px}}
.meta,.small{{color:var(--muted);font-size:14px}} .grid{{display:grid;grid-template-columns:1.25fr .75fr;gap:20px}} .card{{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px;box-shadow:0 8px 30px #493c2b0b;margin-bottom:20px}}
.next{{border-top:5px solid var(--accent)}} .next strong{{display:block;font-size:24px;margin-bottom:5px}} .stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
.stat{{background:#f6efe4;border-radius:12px;padding:14px}} .stat b{{display:block;font-size:22px}} ul{{list-style:none;margin:0;padding:0}} li{{display:flex;justify-content:space-between;gap:18px;border-top:1px solid var(--line);padding:13px 0}} li:first-child{{border-top:0}} li span{{display:block;color:var(--muted);font-size:14px}} a{{color:var(--accent);white-space:nowrap}}
.badge{{display:inline-block;background:#efe2d4;border-radius:999px;padding:3px 10px;margin-right:7px;font-size:13px}} details summary{{cursor:pointer;font-weight:650}} code{{word-break:break-all}}
.reading-layout{{display:grid;grid-template-columns:240px minmax(0,1fr);gap:20px}} .reading-layout nav{{align-self:start;position:sticky;top:16px}} .reading-layout a{{white-space:normal}} .prose{{white-space:pre-wrap;overflow-wrap:anywhere;font:18px/2 "Songti SC","SimSun",serif;margin:0 0 20px}} article{{scroll-margin-top:20px}}
@media(max-width:760px){{.grid,.reading-layout{{grid-template-columns:1fr}}.reading-layout nav{{position:static}}.stats{{grid-template-columns:1fr}}li{{display:block}}li a{{display:inline-block;margin-top:6px}}}}
</style></head><body><main>
<header><span class="badge">{_escape(kind)}</span><span class="badge">本地只读快照</span><h1>{_escape(book["title"])}</h1><div class="meta">生成于 {_escape(packet["captured_at"])}</div></header>
<section class="card next"><h2>下一步</h2><strong>{_escape(title)}</strong><div>{_escape(note)}</div></section>
<div class="grid"><div>
<section class="card"><h2>创作进度</h2><div class="stats"><div class="stat"><b>{progress["last_chapter"]}</b>已正式保存章节</div><div class="stat"><b>{progress["next_chapter"]}</b>下一章</div><div class="stat"><b>{packet["chapters"]["planned_total"]}</b>已有工具计划</div></div><p class="small">草稿、候选稿、可读大纲和封面尚未登记，工作台不会根据文件名猜测当前版本。</p></section>
<section class="card"><h2>最近正式章节</h2><ul>{chapters}</ul></section>
</div><div>
<section class="card"><h2>需要你处理</h2><ul>{attention}</ul></section>
<section class="card"><h2>交付准备</h2><p>{_escape(publish_text)}</p><p class="small">这里只显示本地保存状态，尚未逐字节核验 ZIP。本工具没有执行平台上传，也没有核验平台后台是否保存。</p></section>
</div></div>
{reader}
<details class="card"><summary>技术详情</summary><p class="small">供排障使用，不代表文学质量或平台审核结果。</p><p>作品版本：<code>{_escape(book["revision"])}</code></p><p>快照标识：<code>{_escape(packet["snapshot"]["id"])}</code></p><p>文件清单摘要：<code>{_escape(packet["snapshot"]["filesystem_manifest_sha256"])}</code></p></details>
</main></body></html>'''


def _output_relative(book, output):
    if output is None:
        relative = Path(".story/workbench/index.html")
    else:
        candidate = Path(output).expanduser()
        if candidate.is_absolute():
            try:
                relative = Path(os.path.abspath(candidate)).relative_to(Path(os.path.abspath(book.root)))
            except ValueError:
                api.fail("path_escape", "Workbench output must stay inside the book", path=str(candidate))
        else:
            relative = candidate
    if len(relative.parts) < 3 or relative.parts[:2] != (".story", "workbench"):
        api.fail("path_escape", "Workbench output must stay inside .story/workbench", path=str(relative))
    if relative.suffix.lower() != ".html":
        api.fail("invalid_input", "Workbench output must be an HTML file")
    return relative.as_posix()


def _has_git_marker(root):
    current = Path(os.path.abspath(root))
    for directory in (current, *current.parents):
        if os.path.lexists(directory / ".git"):
            return True
    return False


def _git_ignore_status(book, target, backup):
    try:
        root_check = subprocess.run(["git", "-C", str(book.root), "rev-parse", "--show-toplevel"],
                                    capture_output=True, text=True, encoding="utf-8", timeout=5)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        if not _has_git_marker(book.root):
            return "not_a_repository"
        api.fail("workbench_git_check_failed", "Could not determine the book repository",
                 path=str(target), reason=str(error))
    if root_check.returncode:
        if not _has_git_marker(book.root):
            return "not_a_repository"
        api.fail("workbench_git_check_failed", "Could not determine the book repository",
                 path=str(target), reason=root_check.stderr.strip())
    try:
        repository = Path(root_check.stdout.strip()).resolve(strict=True)
    except (OSError, ValueError) as error:
        api.fail("workbench_git_check_failed", "Git returned an invalid repository root",
                 path=str(target), reason=str(error))
    try:
        relative_target = target.relative_to(repository)
        relative_backup = backup.relative_to(repository)
        workbench = api.safe_path(book.root, ".story/workbench").relative_to(repository)
    except ValueError:
        api.fail("workbench_git_check_failed", "Workbench paths do not belong to the detected repository",
                 path=str(target))
    try:
        tracked = subprocess.run(["git", "-C", str(repository), "ls-files", "-z", "--",
                                  workbench.as_posix()], capture_output=True, timeout=5)
        ignored = [subprocess.run(
            ["git", "-C", str(repository), "check-ignore", "--no-index", "-q", "--", path.as_posix()],
            capture_output=True, timeout=5) for path in (relative_target, relative_backup)]
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as error:
        api.fail("workbench_git_check_failed", "Could not verify the book repository ignore rules",
                 path=str(target), reason=str(error))
    if tracked.returncode:
        api.fail("workbench_git_check_failed", "Could not inspect tracked workbench files",
                 path=str(target))
    if tracked.stdout:
        api.fail("workbench_tracked_output", "Remove the derived workbench page from Git tracking before exporting",
                 path=str(target))
    if all(result.returncode == 0 for result in ignored):
        return "ignored"
    if all(result.returncode in (0, 1) for result in ignored):
        api.fail("workbench_not_ignored", "Add .story/workbench/ to this book repository's local ignore rules before exporting",
                 path=str(target))
    api.fail("workbench_git_check_failed", "Could not verify the book repository's ignore rules",
             path=str(target))


def export(book, output=None, open_browser=False, limit=DEFAULT_LIMIT, budget=DEFAULT_BUDGET,
           offset=0, cursor=None, include_text=True, **legacy):
    if "open" in legacy:
        open_browser = bool(legacy.pop("open"))
    if legacy:
        api.fail("invalid_input", "Unknown workbench export option", fields=sorted(legacy))
    packet = snapshot(book, limit=limit, budget=budget, offset=offset, cursor=cursor)
    reading = _reading_text(book, packet) if include_text else None
    if include_text and snapshot(book, limit=limit, budget=budget, offset=offset)["snapshot"]["id"] != packet["snapshot"]["id"]:
        api.fail("workbench_changed", "Book changed while reading chapters")
    content = render_html(packet, reading)
    relative = _output_relative(book, output)
    target = api.safe_path(book.root, relative)
    backup = api.safe_path(book.root, f".story/workbench/.backups/{uuid.uuid4().hex}/{target.name}")
    ignore_status = _git_ignore_status(book, target, backup)
    existing = _bound_file_fact(book.root, relative)
    old_hash = existing["sha256"] if existing["exists"] else None
    try:
        saved = api.atomic_write(target, content, {old_hash} if old_hash else set(), backup)
    except api.StoryError:
        raise
    except OSError as error:
        api.fail("export_io", str(error), path=str(target))
    raw = content.encode("utf-8")
    opened, open_error = False, None
    if open_browser:
        try:
            opened = bool(webbrowser.open(target.absolute().as_uri()))
        except (OSError, webbrowser.Error) as error:
            open_error = str(error)
    return {"ok": True, "path": str(target), "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw), "snapshot_id": packet["snapshot"]["id"],
            "captured_at": packet["captured_at"], "backup": saved,
            "git_ignore_status": ignore_status, "open_requested": bool(open_browser),
            "opened": opened, "open_error": open_error}


def _save_candidate(root, packet, chapter, text):
    if type(chapter) is not int or not isinstance(text, str) or not text.strip():
        api.fail("invalid_input", "Chapter and non-empty text are required")
    if len(text.encode("utf-8")) > 1024 * 1024:
        api.fail("workbench_read_limit", "Candidate is larger than 1 MiB")
    row = next((row for row in packet["chapters"]["results"] if row["chapter"] == chapter), None)
    if row is None:
        api.fail("invalid_input", "Chapter is outside this editing session")
    book = _ReadOnlyBook(root)
    try:
        current = snapshot(book, limit=packet["chapters"]["limit"])
        if current["snapshot"]["id"] != packet["snapshot"]["id"]:
            api.fail("stale_snapshot", "作品已变化，请另开工作台核对。当前编辑内容仍保留在页面中。")
        source = _editor_document(root, f'formal:{chapter}')
        if source['sha256'] != row['sha256']:
            api.fail('stale_snapshot', '正式章节已变化，请重新载入。')
        saved = _editor_save(root, {**source, 'text': text}, force_candidate=True)
        saved.update(base_sha256=row['sha256'], sha256=hashlib.sha256(_author_read(Path(root), saved['path'])).hexdigest())
        return saved
    finally:
        book.close()


# Author files are discoverable without treating their names as adoption evidence.
AUTHOR_SUFFIXES = {'.md', '.txt', '.png', '.jpg', '.jpeg', '.webp'}
AUTHOR_LIMIT = 5000
AUTHOR_TEXT_LIMIT = 1024 * 1024
MAX_EDITOR_CHAPTER = (1 << 63) - 1
PENDING_SAVE_LIMIT = 7 * 1024 * 1024


def _start_pending_save(root, name, text, content, meta):
    # One flushed record contains both the user's text and its provenance before
    # either destination is touched. A competing writer cannot replace it.
    _validate_candidate_metadata(meta, name + '.meta.json')
    if len(json.dumps(meta, ensure_ascii=False).encode('utf-8')) > 16384:
        api.fail('workbench_write_limit', '来源记录超过保存上限，请下载文字后整理章名信息。')
    record = {'version': 1, 'target': name, 'text': text, 'meta': meta,
              'content_sha256': hashlib.sha256(content.encode('utf-8')).hexdigest()}
    content = json.dumps(record, ensure_ascii=False)
    pending_name = name + '.pending.json'
    api.atomic_write(api.safe_path(root, pending_name), content, set(),
                     api.safe_path(root, '.story/drafts/workbench/.backups/' + uuid.uuid4().hex + '/pending'))
    return pending_name, hashlib.sha256(content.encode('utf-8')).hexdigest()


def _finish_pending_save(root, pending):
    name, digest = pending
    api._retire_bound_file(api.safe_path(root, name),
                          api.safe_path(root, '.story/drafts/workbench/.backups/' + uuid.uuid4().hex + '/completed.json'),
                          {digest})


def _pending_document(root, document_id):
    row = next((r for r in _author_files(Path(root)) if r['id'] == document_id), None)
    if row is None or not row['path'].endswith('.pending.json'):
        api.fail('invalid_input', '待核对记录未列入本书目录。')
    raw = _author_read(Path(root), row['path'], PENDING_SAVE_LIMIT)
    try:
        record = json.loads(raw)
        if not isinstance(record, dict) or record.get('version') != 1 or record.get('target') != row['path'][:-13]:
            raise ValueError('record target or version')
        meta, text = record['meta'], record['text']
        _validate_candidate_metadata(meta, row['path'])
        if not isinstance(text, str) or not text.strip():
            raise ValueError('record text')
        content = text if meta.get('recovery') else meta.get('prefix', '') + text
        if len(content.encode('utf-8')) > AUTHOR_TEXT_LIMIT or hashlib.sha256(content.encode('utf-8')).hexdigest() != record.get('content_sha256'):
            raise ValueError('record content')
    except (KeyError, ValueError, UnicodeError) as error:
        api.fail('workbench_pending_invalid', '待核对记录损坏，请保留原文件并在外部核对。', path=row['path'], cause=str(error))
    packet = _editor_packet(root)
    result = {'ok': True, **row, 'text': text, 'prefix': meta.get('prefix', ''),
              'sha256': hashlib.sha256(raw).hexdigest(), 'snapshot': packet['snapshot']['id'],
              'metadata_sha256': hashlib.sha256(raw).hexdigest(), 'kind': 'candidate',
              'source': meta.get('source'), 'chapter': meta.get('chapter'), 'base_sha256': meta.get('base_sha256'),
              'editable': True, 'needs_recovery': True,
              'status': '保存尚未确认完成；文字及来源已保留。可另存新候选，原文件留待核对。'}
    result['content_kind'] = _candidate_content_kind(meta)
    if result['chapter'] and result['content_kind'] != 'material':
        try:
            formal = _editor_document(root, f"formal:{result['chapter']}")
            result['comparison'] = {'title': formal['title'], 'text': formal['text'], 'sha256': formal['sha256']}
        except api.StoryError:
            pass
    return result


def _author_files(root, warnings=None):
    def report(path):
        if warnings is not None:
            warnings.append('已跳过无法安全读取的文件或目录：' + str(path))

    roots = []
    for relative, category in [('.story/drafts', '候选与草稿'), ('.story/analysis', '拆书分析')]:
        try:
            path = api.safe_path(root, relative)
            if path.is_dir():
                roots.append((path, category))
        except api.StoryError as error:
            if error.code != 'linked_path':
                raise
            report(relative)
        except OSError:
            report(relative)
    try:
        children = list(root.iterdir())
    except OSError:
        report('书根')
        children = []
    for path in children:
        if path.name.startswith('.') or path.name == 'chapters':
            continue
        try:
            if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                report(path.name)
                continue
            if path.is_dir() and (re.match(r'^\d{2}_', path.name) or
                                  any(word in path.name for word in ('大纲', '细纲', '封面', '策划', '分析'))):
                roots.append((path, '创作材料'))
            elif path.is_file() and path.suffix.lower() in AUTHOR_SUFFIXES:
                roots.append((path, '创作材料'))
        except OSError:
            report(path.name)
    result, inspected = [], 0
    for start, category in sorted(roots):
        pending = [start]
        while pending:
            path = pending.pop()
            inspected += 1
            if inspected > AUTHOR_LIMIT:
                api.fail('workbench_scan_limit', '创作材料超过扫描上限，请先整理归档。', maximum=AUTHOR_LIMIT)
            relative = path.relative_to(root).as_posix()
            try:
                api.safe_path(root, relative)
                if path.is_dir():
                    pending.extend(sorted((p for p in path.iterdir()
                                           if not p.name.startswith('.') and p.name != '__pycache__'), reverse=True))
                elif path.is_file() and (path.suffix.lower() in AUTHOR_SUFFIXES or
                                        (relative.startswith('.story/drafts/workbench/') and relative.endswith('.pending.json'))):
                    facts = path.stat()
                    pending_save = relative.endswith('.pending.json')
                    result.append({'id': ('pending:' if pending_save else 'file:') + relative, 'path': relative,
                                   'title': Path(relative[:-13]).stem + ' · 保存待核对' if pending_save else path.stem,
                                   'category': '自动恢复' if pending_save or '/自动恢复/' in relative else category,
                                   'modified': datetime.fromtimestamp(facts.st_mtime, timezone.utc).isoformat(),
                                   'bytes': facts.st_size})
            except api.StoryError as error:
                if error.code != 'linked_path':
                    raise
                report(relative)
            except OSError:
                report(relative)
    return sorted(result, key=lambda row: (row['category'], row['path']))


def _author_read(root, relative, maximum=AUTHOR_TEXT_LIMIT):
    path = api.safe_path(root, relative)
    with api._pinned_directory(path.parent) as directory:
        with api._bound_reader(api._BoundFile(directory, path.name)) as stream:
            raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        api.fail('workbench_read_limit', '文件超过工作台预览上限，请在外部编辑器打开。')
    return raw


def _editor_packet(root, chapter=None, offset=0, limit=DEFAULT_LIMIT):
    book = _ReadOnlyBook(root)
    try:
        if chapter is not None:
            _editor_chapter(chapter)
            offset = book.db.execute('SELECT count(*) FROM chapter_state WHERE chapter > ?', (chapter,)).fetchone()[0]
            limit = 1
        return snapshot(book, limit=limit, offset=offset)
    finally:
        book.close()


def _editor_catalog(root, offset=0, limit=DEFAULT_LIMIT, query=''):
    if type(offset) is not int or not isinstance(query, str) or len(query) > 200:
        api.fail('invalid_input', '目录参数无效。')
    packet = _editor_packet(root, offset=offset, limit=limit)
    # Search the whole formal directory, not only the currently loaded page.
    rows = packet['chapters']['results']
    if query:
        book = _ReadOnlyBook(root)
        try:
            metadata = {row['key']: json.loads(row['value']) for row in book.db.execute('SELECT key,value FROM meta')}
            metadata['__root'] = str(book.root)
            matches = []
            for row in book.db.execute('SELECT chapter FROM chapter_state ORDER BY chapter DESC'):
                path = _chapter_path(metadata, row['chapter'])
                if query.casefold() in Path(path).stem.casefold():
                    matches.append({'chapter': row['chapter'], 'path': path})
            rows = matches[offset:offset + limit]
            total = len(matches)
        finally:
            book.close()
    else:
        total = packet['chapters']['total']
    warnings = []
    all_files = _author_files(Path(root), warnings)
    files = [row for row in all_files if not query or query.casefold() in row['path'].casefold()]
    return {'ok': True, 'chapters': [{'id': f"formal:{r['chapter']}", 'title': Path(r['path']).stem,
                                    'path': r['path'], 'category': '正式正文'} for r in rows],
            'files': files, 'related_files': all_files, 'warnings': warnings, 'total': total, 'offset': offset, 'limit': limit,
            'has_more': offset + len(rows) < total, 'snapshot': packet['snapshot']['id'],
            'workspace': _workspace_index(root, all_files, offset, limit, query)}


def _candidate_metadata(root, relative):
    if not relative.startswith('.story/drafts/workbench/'):
        return {}
    try:
        _author_read(root, relative + '.pending.json', PENDING_SAVE_LIMIT)
    except FileNotFoundError:
        pass
    except (api.StoryError, OSError) as error:
        api.fail('workbench_metadata_pending', '保存记录待核对，暂不可编辑；请从自动恢复区打开待核对记录。',
                 path=relative + '.pending.json', cause=str(error))
    else:
        api.fail('workbench_metadata_pending', '上次保存尚未确认完成；请从自动恢复区找回文字并另存候选。',
                 path=relative + '.pending.json')
    name = relative + '.meta.json'
    try:
        raw = _author_read(root, name, 16384)
    except FileNotFoundError:
        return {}
    except (api.StoryError, OSError) as error:
        api.fail('workbench_metadata_unreadable', '来源记录无法读取，请检查文件或权限。', path=name, cause=str(error))
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        api.fail('workbench_metadata_corrupt', '来源记录损坏，无法解析；请恢复来源记录后再编辑。', path=name)
    _validate_candidate_metadata(value, name)
    value['_metadata_sha256'] = hashlib.sha256(raw).hexdigest()
    return value


def _validate_candidate_metadata(value, name):
    if not isinstance(value, dict):
        api.fail('workbench_metadata_corrupt', '来源记录格式错误，应为对象。', path=name)
    if 'content_kind' in value and value['content_kind'] not in ('material', 'prose'):
        api.fail('workbench_metadata_invalid', '来源记录内容类型无效。', path=name)
    chapter = value.get('chapter')
    if chapter is not None and (type(chapter) is not int or not 1 <= chapter <= MAX_EDITOR_CHAPTER):
        api.fail('workbench_metadata_invalid', '来源记录章号无效或超出范围。', path=name)
    for field in ('source_sha256', 'base_sha256', 'content_sha256'):
        item = value.get(field)
        if item is not None and (not isinstance(item, str) or not re.fullmatch(r'[0-9a-f]{64}', item)):
            api.fail('workbench_metadata_invalid', '来源记录校验值格式错误。', path=name, field=field)
    for field, maximum in (('source', 2048), ('prefix', 8192)):
        item = value.get(field)
        if (field == 'prefix' and field in value and item is None) or (item is not None and (not isinstance(item, str) or len(item) > maximum)):
            api.fail('workbench_metadata_invalid', '来源记录字段格式错误。', path=name, field=field)


def _candidate_content_kind(meta):
    if meta.get('content_kind'):
        return meta['content_kind']
    # Legacy source paths can identify planning materials without opening them.
    # This is a display hint, never permission to read or adopt a source file.
    source = meta.get('source') or ''
    if source.startswith('plan:') or (source.startswith('file:') and
            not source[5:].startswith(('.story/drafts/', 'chapters/'))):
        return 'material'
    return 'prose'


def _editor_chapter(value):
    if type(value) is not int or not 1 <= value <= MAX_EDITOR_CHAPTER:
        api.fail('invalid_input', '章号必须为有效范围内的正整数。')
    return value


def _editor_document_raw(root, document_id):
    if not isinstance(document_id, str) or len(document_id) > 2048:
        api.fail('invalid_input', '文件标识无效。')
    if re.fullmatch(r'plan:[1-9]\d*', document_id):
        number = _editor_chapter(int(document_id.split(':')[1]))
        context = _chapter_context(root, number)
        plan = context['plan']
        if not plan:
            api.fail('invalid_input', '此章尚未保存工具计划。')
        text = '# 第' + str(number) + '章 ' + plan['title'] + '\n\n## 本章目标\n\n' + plan['goal']
        for beat in plan.get('beats', []):
            text += '\n\n### 场景\n\n' + beat['choice'] + '\n\n' + beat['change']
        text += '\n\n## 停笔点\n\n' + plan['stop']
        return {'ok': True, 'id': document_id, 'path': '工具章计划 · 第' + str(number) + '章',
                'title': '第' + str(number) + '章 ' + plan['title'], 'text': text, 'kind': 'plan',
                'chapter': number, 'sha256': api.digest(text), 'editable': False, 'status': '已保存计划，尚非正文'}
    if document_id.startswith('pending:'):
        return _pending_document(root, document_id)
    if re.fullmatch(r'formal:[1-9]\d*', document_id):
        chapter = _editor_chapter(int(document_id.split(':')[1]))
        packet = _editor_packet(root, chapter=chapter)
        row = next((r for r in packet['chapters']['results'] if r['chapter'] == chapter), None)
        if row is None:
            api.fail('invalid_input', '正式章节不存在。')
        raw = _author_read(Path(root), row['path'])
        if hashlib.sha256(raw).hexdigest() != row['sha256']:
            api.fail('workbench_changed', '正式文件有外部改动，请先核对；不会把外改当作正式稿。')
        prefix, text = _body_without_heading(raw.decode('utf-8'), chapter, Path(row['path']).stem)
        return {'ok': True, 'id': document_id, 'path': row['path'], 'title': Path(row['path']).stem,
                'text': text, 'prefix': prefix, 'sha256': row['sha256'], 'snapshot': packet['snapshot']['id'],
                'kind': 'formal', 'chapter': chapter, 'summary': row['summary'], 'base_sha256': row['sha256'],
                'status': '正式稿', 'editable': True}
    row = next((r for r in _author_files(Path(root)) if r['id'] == document_id), None)
    if row is None:
        api.fail('invalid_input', '文件未列入本书材料目录。')
    image_type = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}.get(Path(row['path']).suffix.lower())
    raw = _author_read(Path(root), row['path'], 8 * 1024 * 1024 if image_type else AUTHOR_TEXT_LIMIT)
    packet = _editor_packet(root)
    result = {'ok': True, **row, 'sha256': hashlib.sha256(raw).hexdigest(), 'snapshot': packet['snapshot']['id'],
              'kind': 'material', 'status': '材料文件，采用状态未登记', 'editable': not image_type}
    if image_type:
        result.update(image='data:' + image_type + ';base64,' + base64.b64encode(raw).decode())
        return result
    result.update(text=raw.decode('utf-8'), prefix='')
    try:
        meta = _candidate_metadata(Path(root), row['path'])
    except api.StoryError as error:
        if not error.code.startswith('workbench_metadata_'):
            raise
        result.update(editable=False, status=str(error) + ' 当前只读，正文保留。',
                      metadata_error={'code': error.code, 'path': error.details.get('path')}, source=None)
        return result
    result['metadata_sha256'] = meta.get('_metadata_sha256')
    if row['category'] in ('候选与草稿', '自动恢复'):
        result.update(kind='candidate', status='候选稿，未采用；未记录正式基线')
    result['source'] = meta.get('source') if isinstance(meta.get('source'), str) else None
    result['content_kind'] = _candidate_content_kind(meta) if result['kind'] == 'candidate' else 'material'
    if result['content_kind'] == 'material':
        if meta.get('chapter'):
            result['chapter'] = meta['chapter']
        if result['kind'] == 'candidate':
            result['status'] = '材料候选，未采用；不计作小说正文'
        return result
    chapter = meta.get('chapter')
    base_sha = meta.get('base_sha256')
    # Unregistered old drafts get a visible suggestion, never an asserted lineage.
    hint = re.match(r'^第(\d+)章', Path(row['path']).stem) if result['kind'] == 'candidate' else None
    if type(chapter) is not int or chapter < 1:
        chapter = _editor_chapter(int(hint.group(1))) if hint else None
        base_sha = None
    if chapter:
        result['chapter'] = chapter
        result['base_sha256'] = base_sha
        prefix, body = _body_without_heading(result['text'], chapter, Path(row['path']).stem)
        if meta.get('prefix') and isinstance(meta['prefix'], str) and result['text'].startswith(meta['prefix']):
            prefix, body = meta['prefix'], result['text'][len(meta['prefix']):]
        result.update(prefix=meta.get('prefix', '') if meta.get('recovery') else prefix, text=body)
        try:
            formal = _editor_document(root, f'formal:{chapter}')
            if not prefix and not meta and re.fullmatch(rf'第0*{chapter}章_基线[0-9a-f]{{64}}_修订[0-9a-f]{{32}}', Path(row['path']).stem):
                prefix, body = _body_without_heading(result['text'], chapter, formal['title'])
                result.update(prefix=prefix, text=body)
            result['comparison'] = {'title': formal['title'], 'text': formal['text'], 'sha256': formal['sha256']}
            result['status'] = ('候选稿，未采用；正式基线已变化' if base_sha and base_sha != formal['sha256'] else
                                '候选稿，未采用；正式基线一致' if base_sha else
                                '未登记版本关系；按文件名建议与此章对照')
        except api.StoryError:
            result['status'] = '草稿；对应正式章节不存在或暂不可读'
    return result


def _editor_save(root, payload, recovery=False, force_candidate=False):
    text = payload.get('text')
    if not isinstance(text, str) or not text.strip() or len(text.encode('utf-8')) > AUTHOR_TEXT_LIMIT:
        api.fail('invalid_input', '请输入不超过 1 MiB 的非空正文。')
    source_id = payload.get('id')
    if recovery:
        # Recovery can rescue text even when the source has changed or disappeared.
        if not isinstance(source_id, str) or not source_id.startswith(('formal:', 'file:', 'pending:')) or len(source_id) > 2048:
            api.fail('invalid_input', '恢复来源无效。')
        key = payload.get('recovery_key')
        if not isinstance(key, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', key):
            api.fail('invalid_input', '恢复标识无效。')
        title = payload.get('title', '未保存编辑')
        if not isinstance(title, str):
            api.fail('invalid_input', '恢复稿名称无效。')
        label = re.sub(r'[^\w\u4e00-\u9fff -]', '_', title)[:60]
        name = '.story/drafts/workbench/自动恢复/' + label + '_' + key + '.txt'
        meta = {'source': source_id, 'recovery': True, 'source_sha256': payload.get('sha256'),
                'chapter': payload.get('chapter'), 'base_sha256': payload.get('base_sha256'),
                'prefix': payload.get('prefix', ''), 'created': _utc_now(), 'adopted': False}
        meta['content_kind'] = payload.get('content_kind', _candidate_content_kind(meta))
        if meta['chapter'] is not None:
            _editor_chapter(meta['chapter'])
        if not isinstance(meta['prefix'], str) or len(meta['prefix']) > 8192:
            api.fail('invalid_input', '恢复稿章名信息无效。')
        for field in ('source_sha256', 'base_sha256'):
            if meta[field] is not None and (not isinstance(meta[field], str) or not re.fullmatch(r'[0-9a-f]{64}', meta[field])):
                api.fail('invalid_input', '恢复稿来源校验值无效。')
        conflict, conflict_reason = False, None
        allowed, meta_allowed = set(), set()
        try:
            # Check the marker before reading the body: interruption may have
            # happened before any body existed. Never reuse that destination.
            existing_meta = _candidate_metadata(Path(root), name)
            try:
                old_hash = hashlib.sha256(_author_read(Path(root), name)).hexdigest()
            except FileNotFoundError:
                old_hash = None
            meta_hash = existing_meta.get('_metadata_sha256')
            if old_hash is not None or meta_hash is not None:
                if (not meta_hash or payload.get('recovery_metadata_sha256') != meta_hash or
                        (old_hash is not None and payload.get('recovery_sha256') != old_hash)):
                    conflict, conflict_reason = True, 'changed'
                else:
                    allowed = {old_hash} if old_hash else set()
                    meta_allowed = {meta_hash}
        except api.StoryError as error:
            if not error.code.startswith('workbench_metadata_'):
                raise
            conflict = True
            conflict_reason = 'pending' if error.code == 'workbench_metadata_pending' else 'metadata'
        if conflict:
            key = uuid.uuid4().hex
            name = '.story/drafts/workbench/自动恢复/' + label + '_' + key + '.txt'
            allowed, meta_allowed = set(), set()
        meta['content_sha256'] = hashlib.sha256(text.encode('utf-8')).hexdigest()
        content = text
    else:
        source = _editor_document(root, source_id)
        if not source['editable']:
            api.fail('invalid_input', '当前文件只读；图片仅供预览，来源记录异常须先修复。')
        if (payload.get('sha256') != source['sha256'] or payload.get('snapshot') != source['snapshot'] or
                payload.get('metadata_sha256') != source.get('metadata_sha256')):
            api.fail('stale_snapshot', '来源或正式状态已变化。请保留恢复稿，刷新后重新对照。')
        if text == source['text'] and not source.get('needs_recovery') and not force_candidate:
            return {'ok': True, 'unchanged': True, 'id': source_id, 'path': source['path']}
        name = '.story/drafts/workbench/' + re.sub(r'[^\w\u4e00-\u9fff -]', '_', source['title'])[:70] + '_候选_' + uuid.uuid4().hex[:12] + '.md'
        meta = {'source': source_id, 'source_sha256': source['sha256'], 'chapter': source.get('chapter'),
                'base_sha256': source.get('base_sha256'), 'prefix': source.get('prefix', ''),
                'created': _utc_now(), 'adopted': False,
                'content_kind': 'prose' if source['is_prose'] else 'material'}
        content = source.get('prefix', '') + text
        allowed, meta_allowed = set(), set()
    if len(content.encode('utf-8')) > AUTHOR_TEXT_LIMIT:
        api.fail('workbench_write_limit', '完整候选文件（含恢复的章名）不得超过 1 MiB，请缩减后保存。',
                 maximum_bytes=AUTHOR_TEXT_LIMIT, actual_bytes=len(content.encode('utf-8')))
    target = api.safe_path(Path(root), name)
    pending = _start_pending_save(Path(root), name, text, content, meta)
    try:
        previous_body = api.atomic_write(target, content, allowed, api.safe_path(Path(root), '.story/drafts/workbench/.backups/' + uuid.uuid4().hex + '/previous'))
    except (OSError, api.StoryError, ValueError) as error:
        api.fail('workbench_partial_save', '候选正文写入未完成；完整编辑已保留在自动恢复区的待核对记录中。',
                 path=name, incomplete_path=pending[0], cause=str(error))
    try:
        meta_path = name + '.meta.json'
        api.atomic_write(api.safe_path(Path(root), meta_path), json.dumps(meta, ensure_ascii=False), meta_allowed,
                         api.safe_path(Path(root), '.story/drafts/workbench/.backups/' + uuid.uuid4().hex + '/previous'))
    except (OSError, api.StoryError, ValueError) as error:
        incomplete = api.safe_path(Path(root), '.story/drafts/workbench/.backups/' + uuid.uuid4().hex + '/incomplete.md')
        try:
            api._retire_bound_file(target, incomplete, {hashlib.sha256(content.encode('utf-8')).hexdigest()})
            if previous_body:
                prior = _author_read(Path(root), Path(previous_body).relative_to(root).as_posix()).decode('utf-8')
                api.atomic_write(target, prior, set(), api.safe_path(Path(root),
                                 '.story/drafts/workbench/.backups/' + uuid.uuid4().hex + '/rollback'))
            _finish_pending_save(Path(root), pending)
        except (OSError, api.StoryError, ValueError) as rollback_error:
            api.fail('workbench_partial_save', '来源记录保存失败，回退未完成；请保留编辑并核对文件，勿直接重复保存。',
                     path=name, incomplete_path=str(incomplete), previous_body=previous_body,
                     cause=str(error), rollback_error=str(rollback_error))
        api.fail('workbench_save_rolled_back', '来源记录保存失败，不完整候选已移出目录并保留为暂存文件。'
                 + ('上一份恢复稿已还原。' if previous_body else '') + '请保留编辑后重试。',
                 path=name, incomplete_path=str(incomplete), cause=str(error))
    metadata_hash = hashlib.sha256(json.dumps(meta, ensure_ascii=False).encode('utf-8')).hexdigest()
    if recovery and (hashlib.sha256(_author_read(Path(root), name)).hexdigest() != meta['content_sha256'] or
                     hashlib.sha256(_author_read(Path(root), name + '.meta.json', 16384)).hexdigest() != metadata_hash):
        api.fail('recovery_changed', '恢复稿或来源在写入后发生变化；待核对记录已保留，请保留编辑后重试。', path=pending[0])
    try:
        _finish_pending_save(Path(root), pending)
    except (OSError, api.StoryError, ValueError) as error:
        api.fail('workbench_partial_save', '正文与来源已写入，保存收尾未确认；请从自动恢复区核对后另存候选。',
                 path=name, incomplete_path=pending[0], cause=str(error))
    result = {'ok': True, 'id': 'file:' + name, 'path': name, 'formal_changed': False}
    if recovery:
        result.update(recovery_key=key, recovery_sha256=meta['content_sha256'],
                      recovery_metadata_sha256=metadata_hash, conflict=conflict, conflict_reason=conflict_reason)
    return result


def _chapter_hint(path):
    match = re.match(r'^第([0-9]+)章(?:\s|_|$)', Path(path).stem)
    if not match or len(match[1]) > 18:
        return None
    number = int(match[1])
    return number if number > 0 else None


def _workspace_index(root, files, offset=0, limit=DEFAULT_LIMIT, query=''):
    """Read chapter relationships; names are navigation hints, never adoption evidence."""
    book = _ReadOnlyBook(root)
    try:
        with book.read_snapshot():
            metadata = _meta_map(book.db)
            metadata['__root'] = str(book.root)
            plans = {r['chapter']: json.loads(r['data']) for r in book.db.execute('SELECT chapter,data FROM plans')}
            formal = {r['chapter']: _chapter_path(metadata, r['chapter'])
                      for r in book.db.execute('SELECT chapter FROM chapter_state')}
            revision = book.meta('revision')
            next_chapter = book.meta('last_chapter') + 1
            title = book.meta('title')
        grouped = {}
        for row in files:
            chapter = _chapter_hint(row['path'])
            if chapter:
                grouped.setdefault(chapter, []).append({**row, 'relation': '同章号文件，采用关系须另核对'})
        numbers = sorted(set(plans) | set(formal) | set(grouped))
        groups = []
        for n in numbers:
            plan = plans.get(n, {})
            label = f'第{n}章 ' + plan['title'] if plan.get('title') else Path(formal[n]).stem if n in formal else f'第{n}章'
            items = []
            if n in formal:
                items.append({'id': f'formal:{n}', 'title': '正式正文', 'path': formal[n], 'category': '正式正文'})
            if n in plans:
                items.append({'id': f'plan:{n}', 'title': '已保存章计划', 'path': f'第{n}章 工具计划', 'category': '计划'})
            items.extend(grouped.get(n, []))
            if query and not (query.casefold() in label.casefold() or any(query.casefold() in x['path'].casefold() for x in items)):
                continue
            groups.append({'chapter': n, 'title': label, 'volume': plan.get('volume_dir', ''),
                           'status': '已有正式稿' if n in formal else '已规划，未提交' if n in plans else '仅有同章号材料',
                           'items': items, 'has_plan': n in plans})
        return {'title': title, 'revision': revision, 'captured_at': _utc_now(), 'formal_total': len(formal),
                'planned_total': len(plans), 'next_chapter': next_chapter, 'total': len(groups),
                'groups': groups[offset:offset + limit], 'has_more': offset + limit < len(groups),
                'offset': offset, 'limit': limit}
    finally:
        book.close()


def _chapter_context(root, chapter):
    if not chapter:
        return None
    book = _ReadOnlyBook(root)
    try:
        with book.read_snapshot():
            row = book.db.execute('SELECT data FROM plans WHERE chapter=?', (chapter,)).fetchone()
            plan = json.loads(row['data']) if row else None
            row = book.db.execute('SELECT summary,receipt,sha FROM chapter_state WHERE chapter=?', (chapter,)).fetchone()
            prior = book.db.execute('SELECT summary FROM chapter_state WHERE chapter=?', (chapter - 1,)).fetchone()
            receipt = json.loads(row['receipt']) if row else {}
            return {'chapter': chapter, 'plan': plan, 'previous_summary': prior['summary'] if prior else None,
                    'formal_sha256': row['sha'] if row else None,
                    'review': receipt.get('input', {}).get('review'), 'imported': receipt.get('quality') == 'imported_unverified'}
    finally:
        book.close()


def _editor_document(root, document_id):
    result = _editor_document_raw(root, document_id)
    number = result.get('chapter') or _chapter_hint(result.get('path', ''))
    result['context'] = _chapter_context(root, number)
    path = result.get('path', '')
    result['historical_note'] = ('记录内容反映编写时状态；当前进度请看顶部。'
                                 if any(w in path for w in ('记录', '历史', '报告', '测试结果')) else '规划材料保留编写时内容；实际正文进度以顶部为准。' if any(w in path for w in ('大纲', '细纲', '策划')) else '')
    result['is_prose'] = result.get('kind') in ('formal', 'candidate') and result.get('content_kind') != 'material' and bool(number)
    result['content_kind'] = 'prose' if result['is_prose'] else 'material'
    result['render_markdown'] = result.get('kind') == 'plan' or (not result['is_prose'] and
        (path.lower().endswith('.md') or result.get('kind') == 'candidate'))
    if result.get('kind') == 'candidate' and result['is_prose'] and result.get('context'):
        if result['sha256'] == result['context']['formal_sha256']:
            result['status'] = '内容与当前正式稿逐字一致；不据此推断采用历史'
    return result


def _editor_metrics(root, payload):
    doc = _editor_document(root, payload.get('id'))
    text = payload.get('text')
    selected = payload.get('selection', '')
    if not isinstance(text, str) or not isinstance(selected, str) or len(text.encode('utf-8')) + len(selected.encode('utf-8')) > 2 * AUTHOR_TEXT_LIMIT:
        api.fail('invalid_input', '字数核对文本过大或无效。')
    plan = (doc.get('context') or {}).get('plan') or {}
    method = plan.get('count_method', 'visible_nonspace_v1') if doc['is_prose'] else 'visible_nonspace_v1'
    counted = doc.get('prefix', '') + text if doc['is_prose'] else '\n' + text
    counts = api.manuscript_counts(counted, plan.get('count_title', False) if doc['is_prose'] else True)
    value = counts[method]
    target = plan.get('length') if doc['is_prose'] else None
    return {'ok': True, 'count': value, 'method': method, 'target': target,
            'include_title': bool(plan.get('count_title')) if doc['is_prose'] else None,
            'selection': api.manuscript_counts('\n' + selected, True)[method],
            'in_range': target[0] <= value <= target[1] if target else None,
            'is_prose': doc['is_prose'], 'text_sha256': api.digest(text)}


def _editor_review_task(root, document_id, expected_sha=None):
    doc = _editor_document(root, document_id)
    if expected_sha is not None and expected_sha != doc['sha256']:
        api.fail('stale_snapshot', '所选文件已变化，请刷新后重新生成审稿任务。')
    if not doc.get('editable') or doc.get('needs_recovery'):
        api.fail('invalid_input', '请先将可编辑内容保存为完整候选稿。')
    context = doc.get('context') or {}
    lint = None
    if doc['is_prose'] and context.get('plan'):
        lint = api.lint_text(doc.get('prefix', '') + doc['text'], context['plan'])
    prompt = ('请审查以下已保存文件，先核对文件仍与本次校验值一致，再读取本书约定及相关细纲、前文。\n'
              f'书目录：{Path(root).absolute()}\n文件：{doc["path"]}\n校验值：{doc["sha256"]}\n'
              f'文件状态：{doc["status"]}\n'
              '先检查叙事、人物行动、连续性、阅读期待、字数与中文格式，指出具体依据。\n'
              '本次只审查，不自动修改或正式采用；需要修改时另存候选。不要将页面格式检查当成人工审稿。')
    return {'ok': True, 'prompt': prompt, 'lint': lint, 'status': '审稿任务已生成，尚未交给助手执行'}


def _editor_search(root, query, offset=0, limit=30):
    if not isinstance(query, str) or not query.strip() or len(query) > 200 or type(offset) is not int or offset < 0:
        api.fail('invalid_input', '请输入1至200字搜索词。')
    query = query.strip()
    book = _ReadOnlyBook(root)
    try:
        with book.read_snapshot():
            meta = _meta_map(book.db); meta['__root'] = str(book.root)
            rows = [{'id': f'formal:{r["chapter"]}', 'path': _chapter_path(meta, r['chapter']),
                     'title': Path(_chapter_path(meta, r['chapter'])).stem, 'sha256': r['sha']}
                    for r in book.db.execute('SELECT chapter,sha FROM chapter_state ORDER BY chapter')]
    finally:
        book.close()
    warnings = []
    rows += [r for r in _author_files(Path(root), warnings) if Path(r['path']).suffix.lower() in ('.md', '.txt')]
    hits, read_bytes, checked = [], 0, 0
    # Bound each request and explicitly report incomplete coverage; never silently call a partial scan complete.
    for row in rows:
        if checked >= 3000 or read_bytes >= 32 * 1024 * 1024:
            warnings.append('达到本轮搜索上限，尚未覆盖全部文件。可缩小作品材料范围后重试。')
            break
        checked += 1
        try:
            raw = _author_read(Path(root), row['path'])
            read_bytes += len(raw)
            if row.get('sha256') and hashlib.sha256(raw).hexdigest() != row['sha256']:
                warnings.append('正式稿外改，未纳入搜索：' + row['path']); continue
            text = raw.decode('utf-8')
            match = re.search(re.escape(query), text, re.IGNORECASE)
            if match:
                found = match.start()
                hits.append({'id': row['id'], 'path': row['path'], 'title': row['title'],
                             'excerpt': text[max(0, found - 45):found + len(query) + 110].replace('\n', ' ')})
        except (OSError, UnicodeError, api.StoryError):
            warnings.append('无法读取，未纳入搜索：' + row['path'])
    return {'ok': True, 'results': hits[offset:offset + limit], 'total': len(hits), 'offset': offset,
            'has_more': offset + limit < len(hits), 'checked': checked, 'warnings': warnings,
            'complete': checked == len(rows) and not warnings}


def _library_roots(root, extra=None):
    paths = [Path(root).absolute()] + [Path(p).expanduser().absolute() for p in (extra or [])]
    if len(paths) > 30:
        api.fail('invalid_input', '工作台书架最多登记30本作品。')
    result = {}
    for path in paths:
        book = _ReadOnlyBook(path)
        try:
            key = hashlib.sha256(str(book.root).encode()).hexdigest()[:20]
            result[key] = {'root': str(book.root), 'title': book.meta('title'), 'key': key}
        finally:
            book.close()
    return result


def _library_open(library, key, current_root=None, current_url=None):
    if not isinstance(key, str) or key not in library:
        api.fail('invalid_input', '作品未登记在本次书架中。')
    root = library[key]['root']
    if current_root is not None and root == str(current_root):
        return {'ok': True, 'url': current_url, 'outdated': False}
    state = _service_request(root)
    if state.get('running'):
        return {'ok': True, 'url': state['url'], 'outdated': state.get('outdated', False)}
    # Unreachable instances are not assumed dead; only an unregistered/stopped service can start here.
    record_path = api.safe_path(Path(root), '.story/workbench-service.json')
    if record_path.exists():
        record = json.loads(_author_read(Path(root), '.story/workbench-service.json', 8192))
        if not record.get('stopped'):
            api.fail('workbench_unreachable', '此作品的服务无法确认已退出，请先核对原工作台。')
    import sys
    import time
    command = [sys.executable, str(Path(__file__).with_name('story.py')), 'workbench-serve', '--book', root]
    for entry in library.values():
        if entry['root'] != root:
            command += ['--library-book', entry['root']]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(30):
        time.sleep(0.1)
        state = _service_request(root)
        if state.get('running'):
            return {'ok': True, 'url': state['url'], 'outdated': state.get('outdated', False)}
        if process.poll() is not None:
            break
    api.fail('workbench_start_pending', '启动尚未确认，请稍后再次打开或查看该作品服务状态。')


def _editor_page(packet, reading, token):
    # The editor loads one document at a time; author files are never executable HTML.
    script = r"""const TOKEN=__TOKEN__, LIMIT=__LIMIT__;
const $=id=>document.getElementById(id), docs=new Map();let active=null,offset=0,loading=false,reloadPending=false,openSequence=0,reloading=false,queryTimer;
const note=text=>$('message').textContent=text;
async function call(action,data={}){const response=await fetch(location.pathname+'api/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Story-Token':TOKEN},body:JSON.stringify(data)});const value=await response.json();if(!response.ok)throw Error((value.message||'请求失败')+(value.details?.path?' 文件：'+value.details.path:'')+(value.details?.incomplete_path?' 暂存：'+value.details.incomplete_path:''));return value;}
let currentCatalog=null,viewMode='chapters',metricTimer,metricSequence=0,searchSequence=0,fullSearchOffset=0;
const readableMethod={visible_nonspace_v1:'非空白可见字符（含标点）',letters_numbers_v1:'汉字、字母与数字',han_v1:'汉字'};
function chapterNumber(d){return d.context?.chapter||d.chapter||null;}
function simpleCount(text){return [...text].filter(c=>!/[\s\p{Cc}\p{Cf}]/u.test(c)).length;}
function inlineText(parent,text,path){
 const regex=/(\[([^\]\n]+)\]\(([^)\n]+)\)|\*\*([^*\n]+)\*\*|`([^`\n]+)`)/g;let last=0,m;
 while((m=regex.exec(text))){parent.append(document.createTextNode(text.slice(last,m.index)));let el;
  if(m[2]){let url;try{url=new URL(m[3],'https://story.local/'+path);}catch{}if(url&&/^https?:$/.test(url.protocol)){
   el=document.createElement('a');el.textContent=m[2];
   if(url.hostname==='story.local'){el.href='#';el.onclick=e=>{e.preventDefault();try{openDoc('file:'+decodeURIComponent(url.pathname.slice(1)));}catch{note('链接路径无法识别。');}};}
   else{el.href=url.href;el.target='_blank';el.rel='noreferrer noopener';}
  }else{el=document.createElement('span');el.textContent=m[2];}}
  else{el=document.createElement(m[4]?'strong':'code');el.textContent=m[4]||m[5];}
  parent.append(el);last=regex.lastIndex;
 }parent.append(document.createTextNode(text.slice(last)));
}
function renderMarkdown(container,text,path){
 container.replaceChildren();const lines=text.replace(/\r\n/g,'\n').split('\n');let i=0;
 const cells=line=>line.trim().replace(/^\||\|$/g,'').split('|').map(x=>x.trim());
 while(i<lines.length){const line=lines[i];if(!line.trim()){i++;continue;}
  if(line.startsWith('```')){const pre=document.createElement('pre');const block=[];i++;while(i<lines.length&&!lines[i].startsWith('```'))block.push(lines[i++]);i++;pre.textContent=block.join('\n');container.append(pre);continue;}
  if(i+1<lines.length&&line.includes('|')&&/^\s*\|?\s*:?-{3,}/.test(lines[i+1])){
   const table=document.createElement('table');const tr=document.createElement('tr');for(const x of cells(line)){const th=document.createElement('th');inlineText(th,x,path);tr.append(th);}table.append(tr);i+=2;
   while(i<lines.length&&lines[i].includes('|')&&lines[i].trim()){const row=document.createElement('tr');for(const x of cells(lines[i++])){const td=document.createElement('td');inlineText(td,x,path);row.append(td);}table.append(row);}const wrap=document.createElement('div');wrap.className='table-scroll';wrap.append(table);container.append(wrap);continue;
  }
  const heading=line.match(/^(#{1,6})\s+(.+)/);if(heading){const h=document.createElement('h'+heading[1].length);inlineText(h,heading[2],path);container.append(h);i++;continue;}
  const list=line.match(/^\s*(?:[-*+]\s+|\d+[.)]\s+)(.*)/);if(list){const ul=document.createElement(/^\s*\d/.test(line)?'ol':'ul');while(i<lines.length){const m=lines[i].match(/^\s*(?:[-*+]\s+|\d+[.)]\s+)(.*)/);if(!m)break;const li=document.createElement('li');inlineText(li,m[1],path);ul.append(li);i++;}container.append(ul);continue;}
  const p=document.createElement(line.startsWith('> ')?'blockquote':'p');inlineText(p,line.replace(/^> /,''),path);container.append(p);i++;
 }
}
function readingView(d){const formatted=d.render_markdown&&!d.editing&&!d.rawPreview&&!d.image;
 $('formatted').hidden=!formatted;$('source-view').hidden=!d.render_markdown;$('source-view').textContent=d.rawPreview?'排版预览':'查看源码';
 if(formatted){$('prose').hidden=true;renderMarkdown($('formatted'),d.value,d.path);}
 $('history-note').textContent=d.historical_note||'';
 $('review-task-box').hidden=true;
 showChapterInfo(d);scheduleMetrics();
}
function showChapterInfo(d){const box=$('chapter-info');box.replaceChildren();const ctx=d.context,p=ctx?.plan;
 if(!ctx){box.textContent='这是全书材料。可从左栏按章查看细纲、正文及候选。';return;}
 const add=(title,text,target=box)=>{if(!text)return;const h=document.createElement('h4');h.textContent=title;const v=document.createElement('p');v.textContent=text;target.append(h,v);};
 add('第'+ctx.chapter+'章 · 目标',p?.goal||'尚未保存工具章计划');add('前章衔接',ctx.previous_summary||'没有已提交的前章摘要');
 add('停笔点',p?.stop);add('本章约束',(p?.constraints||[]).join('\n'));
 const related=(currentCatalog?.related_files||currentCatalog?.files||[]).filter(r=>Number(r.path.split('/').pop().match(/^第(\d+)章(?:\s|_|\.)/)?.[1])===ctx.chapter);
 if(related.length){add('相关材料','同章号匹配仅用于导航，不代表已经采用。');related.slice(0,30).forEach(r=>box.append(item(r)));}
 if(ctx.review){const reviewBox=document.createElement('details');const summary=document.createElement('summary');summary.textContent='正式稿审查记录';reviewBox.append(summary);box.append(reviewBox);add('记录范围','以下记录只对应已提交正式稿，不能代替对当前候选的审查。',reviewBox);for(const [key,label]of Object.entries({causality:'因果',continuity:'连续性',constraints:'约束',style:'文风'})){add(label,ctx.review.checks?.[key]?.note,reviewBox);}for(const issue of ctx.review.issues||[])add('审查问题',issue.issue,reviewBox);}
 else add('审查状态',ctx.formal_sha256?'暂无可展示的审查记录。':'尚无正式提交的审查记录。');
}
function scheduleMetrics(){clearTimeout(metricTimer);++metricSequence;const d=docs.get(active);if(!d||!d.editable){$('word-count').textContent='';return;}
 $('word-count').textContent='正在核对字数…';const seq=metricSequence;metricTimer=setTimeout(async()=>{try{
  const selected=d.editing?$('text').value.slice($('text').selectionStart,$('text').selectionEnd):'';
  const m=await call('metrics',{id:d.id,text:d.value,selection:selected});if(seq!==metricSequence||active!==d.id)return;
  $('word-count').textContent=(m.is_prose?'正文':'材料')+' '+m.count+' 字符 · '+readableMethod[m.method]+(m.is_prose?(m.include_title?' · 含章名':' · 不含章名'):'')+(m.target?' · 目标 '+m.target.join('～')+' · '+(m.in_range?'范围内':'范围外'):'')+(m.selection?' · 已选 '+m.selection:'');
 }catch(e){if(seq===metricSequence)$('word-count').textContent='字数核对失败：'+e.message;}},350);
}
function drawCatalog(c,expanded){currentCatalog=c;const w=c.workspace;
 $('book-progress').textContent=`当前进度：正式 ${w.formal_total} 章 · 已规划 ${w.planned_total} 章 · 下一章 第${w.next_chapter}章 · 刷新于 ${new Date(w.captured_at).toLocaleTimeString()}`;
 if(viewMode!=='chapters')return;
 $('list').replaceChildren();
 function group(title,rows,expandedDefault=false){const d=document.createElement('details');d.dataset.group=title;d.open=$('search').value?true:(expanded.get(title)??expandedDefault);const h=document.createElement('summary');h.textContent=title;d.append(h);rows.forEach(r=>d.append(item(r)));$('list').append(d);}
 group('全书材料',c.files.filter(r=>!/^第\d+章(?:\s|_|$)/.test(r.title)&&r.category!=='自动恢复'),false);
 for(const g of w.groups){group((g.volume?g.volume+' / ':'')+g.title+' · '+g.status,g.items,!!$('search').value||g.chapter===w.next_chapter||g.chapter===(chapterNumber(docs.get(active)||{})||1));}
 const recoveries=c.files.filter(r=>r.category==='自动恢复');if(recoveries.length)group('自动恢复',recoveries,true);
 $('range').textContent=w.total?`${w.offset+1}—${Math.min(w.offset+w.limit,w.total)} / ${w.total} 个章节（含规划）`:'尚无章节规划';
 $('older').disabled=!w.has_more;$('newer').disabled=w.offset===0;
 if(active)showChapterInfo(docs.get(active));
}
// Paragraph LCS with a bounded fallback. Preserve every line, including blank lines, on both sides.
function diffLines(before,after){const a=before.split('\n'),b=after.split('\n');if(before===after)return {left:a.map(text=>({text,change:''})),right:b.map(text=>({text,change:''})),coarse:false};if(a.length*b.length>350000){return {left:a.map(t=>({text:t,change:'removed'})),right:b.map(t=>({text:t,change:'added'})),coarse:true};}
 const dp=Array.from({length:a.length+1},()=>new Uint32Array(b.length+1));for(let i=a.length-1;i>=0;i--)for(let j=b.length-1;j>=0;j--)dp[i][j]=a[i]===b[j]?1+dp[i+1][j+1]:Math.max(dp[i+1][j],dp[i][j+1]);
 const left=[],right=[];let i=0,j=0;while(i<a.length||j<b.length){if(i<a.length&&j<b.length&&a[i]===b[j]){left.push({text:a[i++],change:''});right.push({text:b[j++],change:''});}else if(i<a.length&&(j===b.length||dp[i+1][j]>=dp[i][j+1]))left.push({text:a[i++],change:'removed'});else right.push({text:b[j++],change:'added'});}return {left,right,coarse:false};}
function drawDiff(before,after){const diff=diffLines(before,after);for(const [id,rows]of [['before',diff.left],['after',diff.right]]){const box=$(id);box.replaceChildren();for(const r of rows){const span=document.createElement('span');span.className='diff-line '+r.change;span.textContent=r.text;box.append(span);}}
 $('compare-label').textContent+=' · 红色为删除，绿色为新增'+(diff.coarse?'（长文本使用整块高亮）':'');}
async function fullSearch(){const seq=++searchSequence,q=$('full-query').value.trim();if(!q){fullSearchOffset=0;$('search-results').replaceChildren();$('search-status').textContent='';$('search-prev').disabled=true;$('search-next').disabled=true;return;}$('search-status').textContent='搜索中…';try{const r=await call('search',{query:q,offset:fullSearchOffset});if(seq!==searchSequence)return;$('search-results').replaceChildren();for(const row of r.results){const b=item(row);const small=document.createElement('small');small.textContent=row.excerpt;b.append(small);$('search-results').append(b);}$('search-status').textContent=`找到 ${r.total} 份文件 · 已检查 ${r.checked} 份`+(r.complete?' · 本轮覆盖完整':' · 存在未覆盖文件')+(r.warnings.length?'\n'+r.warnings.join('\n'):'');$('search-prev').disabled=!fullSearchOffset;$('search-next').disabled=!r.has_more;}catch(e){if(seq===searchSequence)$('search-status').textContent=e.message;}}
function applyReading(){const size=$('font-size').value,line=$('line-height').value,font=$('font-family').value;document.documentElement.style.setProperty('--reader-size',size+'px');document.documentElement.style.setProperty('--reader-line',line);document.documentElement.style.setProperty('--reader-font',font==='sans'?'system-ui':"'Songti SC','SimSun',serif");document.body.classList.toggle('focus-reading',$('focus-reading').checked);try{localStorage.setItem('story-reading',JSON.stringify({size,line,font,focus:$('focus-reading').checked}));}catch{}}
async function loadBooks(){try{const r=await call('books');$('book-select').replaceChildren();for(const b of r.books){const o=document.createElement('option');o.value=b.key;o.textContent=b.title;o.selected=b.root===r.current;$('book-select').append(o);}$('book-open').disabled=r.books.length<2;$('book-hint').textContent=r.books.length<2?'目前只登记本书；启动服务时可添加其他作品。':'作品会在新标签页打开，当前编辑保留。';}catch(e){$('book-hint').textContent=e.message;}}
function setupWorkspace(){
 $('view-chapters').onclick=()=>{viewMode='chapters';offset=0;catalog();};$('view-files').onclick=()=>{viewMode='files';offset=0;catalog();};
 $('source-view').onclick=()=>{const d=docs.get(active);d.rawPreview=!d.rawPreview;show(d);};
 $('text').addEventListener('select',scheduleMetrics);$('text').addEventListener('keyup',scheduleMetrics);
 $('review-task').onclick=async()=>{const d=docs.get(active);if(!d?.editable)return;if(dirty(d)||d.needs_recovery){note('请先保存当前候选稿，再生成绑定该文件的审稿任务。');return;}try{const r=await call('review-task',{id:d.id,sha256:d.sha256});if(active!==d.id)return;$('review-task-box').hidden=false;$('review-prompt').value=r.prompt;$('review-task-status').textContent=r.status+(r.lint?'；基础检查'+(r.lint.ok?'未发现阻断项':'发现 '+r.lint.errors.length+' 项问题')+'，不代表语义审查通过。':'。');$('review-findings').textContent=r.lint?[...r.lint.errors,...r.lint.warnings].map(x=>x.code+(x.expected?'：目标 '+x.expected.join('～')+'，实际 '+x.actual:'')).join('\n'):'';}catch(e){note(e.message);}};
 $('copy-review').onclick=async()=>{try{await navigator.clipboard.writeText($('review-prompt').value);$('review-task-status').textContent='已复制，请粘贴给助手执行审查；当前尚未正式采用。';}catch{$('review-prompt').select();note('请复制已选中的审稿任务。');}};
 $('full-go').onclick=()=>{fullSearchOffset=0;fullSearch();};$('full-query').onkeydown=e=>{if(e.key==='Enter'){fullSearchOffset=0;fullSearch();}};$('search-prev').onclick=()=>{fullSearchOffset=Math.max(0,fullSearchOffset-30);fullSearch();};$('search-next').onclick=()=>{fullSearchOffset+=30;fullSearch();};
 try{const p=JSON.parse(localStorage.getItem('story-reading')||'{}');if(['16','19','22','25'].includes(p.size))$('font-size').value=p.size;if(['1.6','1.9','2.2'].includes(p.line))$('line-height').value=p.line;if(['serif','sans'].includes(p.font))$('font-family').value=p.font;$('focus-reading').checked=p.focus===true;}catch{}
 for(const id of ['font-size','line-height','font-family','focus-reading'])$(id).onchange=applyReading;applyReading();
 $('book-open').onclick=async()=>{try{const r=await call('open-book',{key:$('book-select').value});const a=document.createElement('a');a.href=r.url;a.target='_blank';a.rel='noopener';a.textContent='打开所选作品';$('book-hint').replaceChildren(a);a.click();if(r.outdated)note('所选作品服务仍使用较早代码；原编辑保留，可另行升级。');}catch(e){$('book-hint').textContent=e.message;}};
 loadBooks();
}

function dirty(d){return d&&d.editable&&d.value!==d.text;}
function badge(){pendingEdits();if(!active)return;const d=docs.get(active);$('state').textContent=dirty(d)?'未保存的候选编辑':d.status;$('save').disabled=(!dirty(d)&&!d.needs_recovery)||d.saving;$('save').textContent=d.needs_recovery?'恢复为新候选':'保存候选稿';$('edit').disabled=!d.editable;$('review-task').disabled=!d.editable;$('compare').disabled=!d.comparison&&d.kind!=='formal';$('download').disabled=!d.editable;$('edit').textContent=d.editing?'阅读预览':'编辑';document.querySelectorAll('[data-doc]').forEach(e=>{const item=docs.get(e.dataset.doc);e.classList.toggle('selected',e.dataset.doc===active);e.classList.toggle('dirty',!!dirty(item));});}
function show(d){active=d.id;$('title').textContent=d.title;$('path').textContent=d.path;$('text').value=d.value;$('text').hidden=!d.editable||!d.editing;$('prose').hidden=!!d.image||!!d.editing;$('prose').textContent=d.value||'';$('cover').hidden=!d.image;if(d.image)$('cover').src=d.image;$('difference').hidden=true;$('detail').textContent=[d.status,d.metadata_error?'来源记录文件：'+d.metadata_error.path:'',d.modified?'文件修改时间：'+new Date(d.modified).toLocaleString():'',d.source?'来源：'+d.source:'',d.summary?'正式稿摘要：'+d.summary:'',d.comparison?'对照对象：'+d.comparison.title:''].filter(Boolean).join('\n\n');readingView(d);badge();}
async function openDoc(id){const sequence=++openSequence;try{if(!docs.has(id)){const d=await call('open',{id});if(sequence!==openSequence)return;d.value=d.text||'';d.editing=false;d.recoveryKey=crypto.randomUUID();if(!docs.has(id))docs.set(id,d);}if(sequence!==openSequence)return;show(docs.get(id));location.hash=encodeURIComponent(id);}catch(e){if(sequence===openSequence)note(e.message);}}
function pendingEdits(){const pending=[...docs.values()].filter(dirty);$('pending-box').hidden=!pending.length;$('pending-count').textContent='待保存文件 · '+pending.length;$('pending-list').replaceChildren();for(const d of pending){const b=document.createElement('button');b.className='document';b.title=d.path||d.id;b.textContent=d.title+' · '+(d.recovered===d.value?'已写入恢复稿':'尚未写入恢复稿');b.onclick=()=>openDoc(d.id);$('pending-list').append(b);}}
function item(row){const b=document.createElement('button');b.className='document';b.dataset.doc=row.id;b.title=row.path;b.textContent=row.category==='正式正文'||row.category==='计划'?row.title:row.title+' · '+(row.path.split('/').slice(-2,-1)[0]||'书根');b.onclick=()=>openDoc(row.id);return b;}
async function catalog(){if(loading){reloadPending=true;return;}loading=true;try{const c=await call('catalog',{offset,query:$('search').value});$('directory-warning').textContent=(c.warnings||[]).join('\n');const expandedGroups=new Map([...$('list').querySelectorAll('details')].map(g=>[g.dataset.group,g.open]));const listScroll=$('list').parentElement.scrollTop;if(viewMode!=='chapters'){$('list').replaceChildren();const section=(title,rows,expanded=true)=>{const group=document.createElement('details');group.dataset.group=title;group.open=$('search').value?true:(expandedGroups.get(title)??expanded);const summary=document.createElement('summary');summary.textContent=title+' · '+rows.length;group.append(summary);rows.forEach(r=>group.append(item(r)));$('list').append(group);};section('正式正文',c.chapters);for(const category of ['候选与草稿','自动恢复','创作材料','拆书分析']){const rows=c.files.filter(r=>r.category===category);if(rows.length){const folders=new Map();for(const row of rows){const folder=row.path.includes('/')?row.path.slice(0,row.path.lastIndexOf('/')):'书根';if(!folders.has(folder))folders.set(folder,[]);folders.get(folder).push(row);}for(const [folder,entries] of folders)section(category+' / '+folder,entries,!!$('search').value||category==='自动恢复');}}$('range').textContent=c.total?`${offset+1}—${Math.min(offset+LIMIT,c.total)} / ${c.total} 章`:'没有匹配的正式章节';$('older').disabled=!c.has_more;$('newer').disabled=offset===0;}drawCatalog(c,expandedGroups);$('list').parentElement.scrollTop=listScroll;badge();if(!active){let id;try{id=decodeURIComponent(location.hash.slice(1));}catch{}if(/^chapter-\d+$/.test(id||''))id='formal:'+id.slice(8);id=id||c.chapters[0]?.id||c.files[0]?.id||c.workspace?.groups[0]?.items[0]?.id;if(id)await openDoc(id);}}catch(e){note(e.message);}finally{loading=false;if(reloadPending){reloadPending=false;catalog();}}}
async function recover(d,force=false){if(d.recovering){await d.recovering;if(force||(dirty(d)&&d.value!==d.recovered))return recover(d,force);return;}if(!dirty(d)||(!force&&d.value===d.recovered))return;if(force)d.recovered=undefined;const value=d.value;d.recovering=(async()=>{try{const r=await call('recover',{id:d.id,text:value,title:d.title,recovery_key:d.recoveryKey,sha256:d.sha256,chapter:d.chapter,content_kind:d.content_kind,base_sha256:d.base_sha256,prefix:d.prefix,recovery_sha256:d.recoverySha,recovery_metadata_sha256:d.recoveryMetadataSha});d.recovered=value;d.recoveryPath=r.path;d.recoverySha=r.recovery_sha256;d.recoveryMetadataSha=r.recovery_metadata_sha256;if(r.recovery_key)d.recoveryKey=r.recovery_key;pendingEdits();await catalog();if(active===d.id)note(r.conflict?'原恢复稿有变化或保存待核对，本次编辑已另存恢复稿，原文件保留。':'编辑已写入自动恢复稿；正式采用前仍须审查。');}catch(e){if(active===d.id)note('自动恢复保存失败：'+e.message+'，请下载当前文字。');}})();try{await d.recovering;}finally{d.recovering=null;}}
$('text').addEventListener('input',()=>{const d=docs.get(active);d.value=$('text').value;clearTimeout(d.timer);d.timer=setTimeout(()=>recover(d),1200);badge();scheduleMetrics();});
$('edit').onclick=()=>{const d=docs.get(active);d.editing=!d.editing;show(d);if(d.editing)$('text').focus();};
$('save').onclick=async()=>{const d=docs.get(active);if((!dirty(d)&&!d.needs_recovery)||d.saving||reloading)return;const text=d.value;d.saving=true;badge();try{await recover(d);const r=await call('save',{id:d.id,sha256:d.sha256,snapshot:d.snapshot,metadata_sha256:d.metadata_sha256,text});const fresh=await call('open',{id:r.id});fresh.value=d.value;fresh.editing=d.editing;fresh.recoveryKey=crypto.randomUUID();docs.set(r.id,fresh);d.value=d.text;clearTimeout(d.timer);if(active===d.id){show(fresh);location.hash=encodeURIComponent(fresh.id);}if(dirty(fresh))fresh.timer=setTimeout(()=>recover(fresh),1200);note('已保存候选稿：'+r.path+'；正式稿未改变。');await catalog();}catch(e){note(e.message+' 当前文字仍在编辑器中，可下载或从自动恢复稿找回。');}finally{d.saving=false;badge();}};
$('compare').onclick=async()=>{const d=docs.get(active),id=active;$('difference').hidden=true;try{const fresh=await call('open',{id:d.kind==='formal'?id:'formal:'+d.chapter});if(active!==id)return;$('before').textContent=fresh.text;$('after').textContent=d.value;$('compare-label').textContent=d.base_sha256?(d.base_sha256===fresh.sha256?'最新正式稿与当前候选：基线一致':'最新正式稿与当前候选：正式基线已变化'):'最新正式稿对照：未确认版本关系';drawDiff(fresh.text,d.value);$('difference').hidden=false;}catch(e){if(active===id)note('无法读取最新正式稿：'+e.message);}};
$('download').onclick=()=>{const d=docs.get(active),a=document.createElement('a');const url=URL.createObjectURL(new Blob([d.value],{type:'text/plain;charset=utf-8'}));a.href=url;a.download=d.title+'-候选.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
async function reloadDocuments(preserve){if(reloading)return;if([...docs.values()].some(d=>d.saving)){note('候选稿正在保存，请完成后再重新载入。');return;}const changes=[...docs.values()].filter(dirty);if(changes.length&&!preserve){note('有未保存编辑，请使用“保留恢复稿并重新载入”。');return;}reloading=true;const id=active;try{for(const d of changes){await recover(d,true);if(d.value!==d.recovered){note('恢复尚未成功，已停止重新载入，请下载当前文字。');return;}}const fresh=id?await call('open',{id}):null;if(active!==id){note('已切换文件，本次重新载入取消；原编辑仍保留。');return;}for(const d of docs.values()){if(dirty(d)&&d.value!==d.recovered){note('恢复期间又有输入，已保留编辑；请再次操作。');return;}}++openSequence;for(const d of docs.values())clearTimeout(d.timer);docs.clear();active=null;if(fresh){fresh.value=fresh.text||'';fresh.editing=false;fresh.recoveryKey=crypto.randomUUID();docs.set(fresh.id,fresh);show(fresh);}note(changes.length?'恢复稿已保留，来源已重新载入；可从左栏打开恢复稿。':'来源已重新读取。');await catalog();}catch(e){note('重新载入失败：'+e.message+'；原编辑仍保留。');}finally{reloading=false;}}
$('refresh').onclick=()=>reloadDocuments(false);
$('recover-reload').onclick=()=>reloadDocuments(true);
$('search').oninput=()=>{clearTimeout(queryTimer);queryTimer=setTimeout(()=>{offset=0;catalog();},300);};$('older').onclick=()=>{offset+=LIMIT;catalog();};$('newer').onclick=()=>{offset=Math.max(0,offset-LIMIT);catalog();};
window.addEventListener('beforeunload',event=>{if([...docs.values()].some(dirty)){event.preventDefault();event.returnValue='';}});
window.addEventListener('hashchange',()=>{let id;try{id=decodeURIComponent(location.hash.slice(1));}catch{return;}if(/^chapter-\d+$/.test(id))id='formal:'+id.slice(8);if(id&&id!==active)openDoc(id);});
setupWorkspace();catalog();""".replace('__TOKEN__', json.dumps(token)).replace('__LIMIT__', str(packet['chapters']['limit']))
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    page = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; connect-src 'self'; img-src data:; script-src 'sha256-__HASH__'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>__TITLE__ · 写作工作台</title><style>
#build-label{display:block;color:#718096;font-size:11px}#pending-box{border:1px solid #e5c694;border-radius:8px;padding:10px;margin-bottom:14px;background:#fffaf0}#pending-list{max-height:220px;overflow:auto}*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;color:#293446;background:#f7f8fa;font:14px/1.7 system-ui,-apple-system,'PingFang SC',sans-serif}header{height:66px;display:flex;align-items:center;justify-content:space-between;padding:0 24px;border-bottom:1px solid #dde3ea;background:white}button,input{font:inherit}button{cursor:pointer;border:1px solid #dce2e9;background:white;border-radius:7px;padding:7px 12px;color:#34445b}button:disabled{opacity:.45;cursor:default}.desk{display:grid;grid-template-columns:280px minmax(0,1fr) 280px;height:calc(100vh - 66px)}nav,aside{overflow:auto;padding:20px;background:#f5f7fa}nav{border-right:1px solid #e1e5eb}aside{border-left:1px solid #e1e5eb}main{overflow:auto;padding:28px 40px;background:white;min-width:0}#search{width:100%;padding:10px;border:1px solid #dce2e9;border-radius:6px}.document{display:block;width:100%;text-align:left;margin:5px 0;border:0;background:transparent;overflow-wrap:anywhere}.selected{background:#e7eefb;color:#285c9e}.dirty:after{content:' · 未保存';color:#b65226}summary{cursor:pointer;margin:16px 0 8px;color:#6d7b8d}.tools{display:flex;gap:8px;flex-wrap:wrap;position:sticky;top:-28px;background:white;padding:8px 0;z-index:1}h1{font-size:25px;font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:19px/2 'Songti SC','SimSun',serif}textarea{width:100%;min-height:65vh;resize:vertical;border:1px solid #cad5e1;border-radius:6px;padding:18px;font:19px/1.9 'Songti SC','SimSun',serif;color:#293446}#detail{font:13px/1.8 system-ui;white-space:pre-wrap}#path,#message,#state{overflow-wrap:anywhere;color:#718096;font-size:12px}#state{color:#966127}#cover{max-width:100%;max-height:75vh}.comparison{display:grid;grid-template-columns:1fr 1fr;gap:20px}.comparison pre{font-size:15px;background:#f7f8fa;padding:12px}.pager{display:flex;gap:8px;margin:12px 0}#range{font-size:12px;color:#718096}button:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid #527bb5}@media(max-width:1000px){.desk{grid-template-columns:220px minmax(0,1fr) 230px}main{padding:24px}}@media(max-width:760px){.desk{grid-template-columns:150px minmax(0,1fr)}aside{display:none}nav{padding:10px}main{padding:15px}.comparison{grid-template-columns:1fr}}

:root{--reader-size:19px;--reader-line:1.9;--reader-font:'Songti SC','SimSun',serif}#prose,#text,#formatted{font-size:var(--reader-size);line-height:var(--reader-line);font-family:var(--reader-font)}#formatted{overflow-wrap:anywhere}#formatted h1{font-size:1.35em}#formatted h2{font-size:1.18em}#formatted h3{font-size:1.05em}#formatted pre{font:14px/1.6 monospace;background:#f5f7fa;padding:12px}#formatted code{font-size:.85em;background:#f1f4f8}#formatted table{border-collapse:collapse;font:14px/1.7 system-ui;width:100%}#formatted td,#formatted th{border:1px solid #dce2e9;padding:8px;text-align:left}.table-scroll{overflow:auto}#book-progress{font-size:12px;color:#526777;margin:4px 0}header{height:auto;min-height:78px;gap:14px}.desk{height:calc(100vh - 88px)}#history-note{font:13px/1.6 system-ui;color:#946425;background:#fff6df}#history-note:empty{display:none}#word-count{font:13px/1.8 system-ui;color:#456875}#chapter-info{font:13px/1.8 system-ui;white-space:pre-wrap}#chapter-info h4{margin-bottom:5px}#chapter-info p{margin-top:5px}#chapter-info .document{font-size:12px}#review-prompt{min-height:220px;font:13px/1.8 system-ui}#review-task-box{padding:14px;background:#eef4fa;margin:20px 0}#review-findings{font:13px/1.6 system-ui;color:#8a392b}.diff-line{display:block;white-space:pre-wrap;min-height:1em}.diff-line.removed{background:#ffe3df;color:#882f26}.diff-line.added{background:#ddf4e4;color:#215b36}.navigation-mode{display:flex;gap:4px;margin:10px 0}.navigation-mode button{font-size:12px}#search-results small{display:block;font-size:12px;color:#677488;margin-top:6px}#search-status,#book-hint{white-space:pre-wrap;font:12px/1.7 system-ui}#book-select,#full-query{width:100%;max-width:100%;padding:6px}#reading-options{font:13px/1.8 system-ui;padding:8px 0}#reading-options label{display:inline-block;margin-right:12px}#reading-options select{font:inherit}.focus-reading .desk{grid-template-columns:240px minmax(0,1fr)}.focus-reading aside{display:none}.focus-reading main{padding-left:max(24px,calc((100vw - 1080px)/2));padding-right:max(24px,calc((100vw - 1080px)/2))}@media(max-width:760px){header{padding:10px;flex-wrap:wrap}.desk{height:auto;min-height:80vh;grid-template-columns:135px minmax(0,1fr)}.focus-reading .desk{grid-template-columns:1fr}.focus-reading nav{display:none}main{max-height:85vh}.tools button{font-size:12px}#book-progress{font-size:11px}}
</style></head><body><header><div><strong>__TITLE__</strong><p id="book-progress" role="status">正在读取当前进度…</p><small id="build-label" title="用于区分工作台页面更新，不是技能发布版本">界面标识：__BUILD__</small></div><div><button id="recover-reload">保留恢复稿并重新载入</button> <button id="refresh">刷新目录与状态</button></div></header><div class="desk"><nav aria-label="作品目录"><details><summary>切换作品</summary><select id="book-select" aria-label="选择作品"></select><button id="book-open">打开作品</button><p id="book-hint"></p></details><div class="navigation-mode"><button id="view-chapters">按章查看</button><button id="view-files">按文件查看</button></div><section id="pending-box" hidden><strong id="pending-count"></strong><div id="pending-list"></div></section><input id="search" type="search" placeholder="搜索全书章名或材料路径" aria-label="搜索作品文件"><div class="pager"><button id="newer">较新章节</button><button id="older">更早章节</button></div><p id="range"></p><p id="directory-warning" role="status"></p><div id="list"></div><details><summary>全文搜索</summary><input id="full-query" aria-label="搜索正文与材料内容" placeholder="搜索正文与材料内容"><button id="full-go">搜索内容</button><p id="search-status" role="status"></p><div id="search-results"></div><button id="search-prev" disabled>上一页结果</button><button id="search-next" disabled>下一页结果</button></details></nav><main><div class="tools"><button id="edit" disabled>编辑</button><button id="save" disabled>保存候选稿</button><button id="compare" disabled>对照正式稿</button><button id="review-task" disabled>审稿</button><button id="source-view" hidden>查看源码</button><button id="download" disabled>下载当前文字</button></div><p id="message" role="status">编辑会写入本书自动恢复稿；关闭前仍请保存候选。文件名称不代表已采用。</p><details id="reading-options"><summary>阅读设置</summary><label>字号 <select id="font-size"><option>16</option><option selected>19</option><option>22</option><option>25</option></select></label><label>行距 <select id="line-height"><option>1.6</option><option selected>1.9</option><option>2.2</option></select></label><label>字体 <select id="font-family"><option value="serif">宋体</option><option value="sans">黑体</option></select></label><label><input id="focus-reading" type="checkbox">专注阅读</label></details><p id="word-count" role="status"></p><h1 id="title">选择章节或材料</h1><p id="state"></p><p id="path"></p><p id="history-note"></p><div id="formatted" hidden></div><pre id="prose"></pre><textarea id="text" aria-label="候选正文编辑" hidden></textarea><img id="cover" alt="封面预览" hidden><section id="review-task-box" hidden><h3>交给助手审稿</h3><p id="review-task-status"></p><pre id="review-findings"></pre><textarea id="review-prompt" aria-label="审稿任务" readonly></textarea><button id="copy-review">复制审稿任务</button></section><section id="difference" hidden><p id="compare-label"></p><div class="comparison"><section><h3>当前正式稿</h3><pre id="before"></pre></section><section><h3>当前候选</h3><pre id="after"></pre></section></div></section></main><aside><h3>本章写作</h3><div id="chapter-info"></div><h3>文件与版本</h3><pre id="detail"></pre><h3>采用候选</h3><p>保存不会覆盖正式稿。请将候选路径交给助手，按现有审稿和历史修订流程采用。</p><p>旧稿未登记的版本关系会明确标注；自动恢复稿须自行核对后再采用。</p><p>自动恢复在停止输入后写入本书文件；失败会提示。关闭或崩溃前尚未写入的文字仍可能丢失。</p></aside></div><script>__SCRIPT__</script></body></html>'''
    return page.replace('__TITLE__', _escape(packet['book']['title'])).replace('__HASH__', digest).replace('__BUILD__', hashlib.sha256((page + script.partition('\n')[2]).encode()).hexdigest()[:10]).replace('__SCRIPT__', script).encode('utf-8')


@contextmanager
def _editor_lease(root):
    # Keep the lock inode in place. The OS releases ownership on normal exit or
    # process death; never infer ownership from a stale PID or delete the lock.
    path = api.safe_path(root, '.story/workbench-server.lock')
    with api._pinned_directory(path.parent) as directory:
        with path.open('a+b') as handle:
            api._verify_bound_directory(directory)
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                api.fail('workbench_running', '本书已有工作台运行。请用 workbench-status 查看；升级前保留所有页面编辑，再用 workbench-stop --saved 停止。')
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _service_request(root, action='session', saved=False):
    root = Path(root).expanduser().resolve()
    try:
        record = json.loads(_author_read(root, '.story/workbench-service.json', 8192))
    except FileNotFoundError:
        return {'ok': True, 'running': False, 'status': '未登记服务；较早版本的服务须另行核对。'}
    except (ValueError, UnicodeError):
        api.fail('workbench_service_invalid', '工作台服务记录损坏，请保留记录并核对实际进程。')
    if (not isinstance(record, dict) or record.get('root') != str(root) or
            type(record.get('port')) is not int or not 1 <= record['port'] <= 65535 or
            not isinstance(record.get('token'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', record['token']) or
            not isinstance(record.get('instance'), str) or not re.fullmatch(r'[0-9a-f]{32}', record['instance'])):
        api.fail('workbench_service_invalid', '工作台服务记录无效；不会向未确认的进程发送停止请求。')
    if action == 'stop' and not saved:
        api.fail('workbench_unsaved', '请先保留所有工作台页面的编辑，再使用 --saved 停止服务。')
    if record.get('stopped') is True:
        return {'ok': True, 'running': False, 'status': '登记服务已正常停止。'}
    connection = http.client.HTTPConnection('127.0.0.1', record['port'], timeout=3)
    try:
        origin = f"http://127.0.0.1:{record['port']}"
        connection.request('POST', '/' + record['token'] + '/api/' + action,
                           json.dumps({'instance': record['instance'], 'saved': saved}),
                           {'Content-Type': 'application/json', 'Origin': origin, 'X-Story-Token': record['token']})
        response = connection.getresponse()
        raw = response.read(8193)
        if response.status != 200 or len(raw) > 8192:
            api.fail('workbench_service_mismatch', '服务未确认此实例；不会按记录中的 PID 结束进程。')
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get('instance') != record['instance'] or result.get('root') != str(root):
            api.fail('workbench_service_mismatch', '服务身份不一致，未确认停止。')
        result['outdated'] = result.get('code_sha256') != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        result['url'] = origin + '/' + record['token'] + '/'
        return result
    except (OSError, http.client.HTTPException, ValueError) as error:
        return {'ok': True, 'running': None, 'status': '登记服务暂不可达，不能据此判定进程已退出。', 'reason': str(error)}
    finally:
        connection.close()


def editor_server(root, limit=DEFAULT_LIMIT, library_books=None):
    root = Path(root).expanduser().resolve()
    packet = _editor_packet(root, limit=limit)
    token = secrets.token_urlsafe(32)
    route = '/' + token + '/'
    library = _library_roots(root, library_books)
    identity = {'instance': uuid.uuid4().hex, 'root': str(root), 'pid': os.getpid(),
                'code_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'started': _utc_now()}

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass

        def reply(self, status, data, content_type='application/json; charset=utf-8'):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('X-Frame-Options', 'DENY')
            self.end_headers()
            self.wfile.write(data)

        def allowed(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def do_GET(self):
            if not self.allowed() or self.path != route:
                return self.reply(404, b'{}')
            try:
                fresh = _editor_packet(root, limit=limit)
                self.reply(200, _editor_page(fresh, {}, token), 'text/html; charset=utf-8')
            except (api.StoryError, OSError, ValueError) as error:
                self.reply(409, json.dumps({'message': str(error)}, ensure_ascii=False).encode())

        def do_POST(self):
            origin = f'http://127.0.0.1:{self.server.server_port}'
            if (not self.allowed() or not self.path.startswith(route) or
                    self.headers.get('Origin') != origin or self.headers.get('X-Story-Token') != token):
                return self.reply(403, b'{}')
            if self.headers.get('Content-Type') != 'application/json' or self.headers.get('Transfer-Encoding'):
                return self.reply(400, b'{}')
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 7 * 1024 * 1024:
                    return self.reply(413, b'{}')
                raw = self.rfile.read(length)
                if len(raw) != length:
                    return self.reply(400, b'{}')
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    return self.reply(400, b'{}')
                action = self.path[len(route):]
                if action in ('api/session', 'api/stop'):
                    if payload.get('instance') != identity['instance']:
                        return self.reply(409, b'{}')
                    if action == 'api/stop' and payload.get('saved') is not True:
                        return self.reply(409, b'{}')
                    result = {'ok': True, **identity, 'running': True, 'stop_requested': action == 'api/stop'}
                    self.reply(200, json.dumps(result, ensure_ascii=False).encode())
                    if action == 'api/stop':
                        threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                elif action == 'api/catalog':
                    result = _editor_catalog(root, payload.get('offset', 0), limit, payload.get('query', ''))
                elif action == 'api/metrics':
                    result = _editor_metrics(root, payload)
                elif action == 'api/review-task':
                    result = _editor_review_task(root, payload.get('id'), payload.get('sha256'))
                elif action == 'api/search':
                    result = _editor_search(root, payload.get('query'), payload.get('offset', 0))
                elif action == 'api/books':
                    result = {'ok': True, 'books': list(library.values()), 'current': str(root)}
                elif action == 'api/open-book':
                    result = _library_open(library, payload.get('key'), root, self.server.editor_url)
                elif action == 'api/open':
                    result = _editor_document(root, payload.get('id'))
                elif action in ('api/save', 'api/recover'):
                    result = _editor_save(root, payload, recovery=action.endswith('recover'))
                elif action == 'candidate':
                    result = _save_candidate(root, packet, payload.get('chapter'), payload.get('text'))
                else:
                    return self.reply(404, b'{}')
                self.reply(200, json.dumps(result, ensure_ascii=False).encode())
            except api.StoryError as error:
                self.reply(409, json.dumps({'message': str(error), 'error': error.code, 'details': error.details}, ensure_ascii=False).encode())
            except (ValueError, UnicodeError, OSError) as error:
                self.reply(400, json.dumps({'message': str(error)}, ensure_ascii=False).encode())

    class EditorServer(HTTPServer):
        def server_close(self):
            try:
                super().server_close()
                if getattr(self, 'lease', None) is not None:
                    try:
                        path = '.story/workbench-service.json'
                        raw = _author_read(root, path, 8192)
                        record = json.loads(raw)
                        if record.get('instance') == identity['instance'] and not record.get('stopped'):
                            record['stopped'] = True
                            api.atomic_write(api.safe_path(root, path), json.dumps(record, ensure_ascii=False),
                                             {hashlib.sha256(raw).hexdigest()},
                                             api.safe_path(root, '.story/.workbench-backups/' + uuid.uuid4().hex + '/service.json'))
                    except (OSError, api.StoryError, ValueError, AttributeError):
                        # Failure to update a status hint must not hold the lease.
                        pass
            finally:
                self.lease.close()

    lease = ExitStack()
    lease.enter_context(_editor_lease(root))
    server = None
    try:
        server = EditorServer(('127.0.0.1', 0), Handler, bind_and_activate=False)
        server.lease = lease
        server.server_bind()
        server.server_activate()
        server.editor_url = f'http://127.0.0.1:{server.server_port}' + route
        path = '.story/workbench-service.json'
        try:
            allowed = {hashlib.sha256(_author_read(root, path, 8192)).hexdigest()}
        except FileNotFoundError:
            allowed = set()
        api.atomic_write(api.safe_path(root, path), json.dumps({**identity, 'port': server.server_port, 'token': token}, ensure_ascii=False),
                         allowed, api.safe_path(root, '.story/.workbench-backups/' + uuid.uuid4().hex + '/service.json'))
        return server
    except BaseException:
        if server is not None:
            server.server_close()
        else:
            lease.close()
        raise

def run(args):
    if args.command in ('workbench-status', 'workbench-stop'):
        return _service_request(args.book, 'stop' if args.command == 'workbench-stop' else 'session', getattr(args, 'saved', False))
    if args.command == "workbench-serve":
        server = editor_server(args.book, args.limit, args.library_book)
        print(json.dumps({"url": server.editor_url, "mode": "candidate-editing"}), flush=True)
        if args.open_browser:
            webbrowser.open(server.editor_url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return {"ok": True, "stopped": True}
    book = _ReadOnlyBook(args.book)
    try:
        if args.command == "workbench-snapshot":
            return snapshot(book, args.limit, args.budget_bytes, args.offset, args.cursor)
        if args.command == "workbench-export":
            return export(book, args.output, args.open_browser, args.limit, args.budget_bytes,
                          args.offset, args.cursor, include_text=args.include_text)
        raise AssertionError(args.command)
    finally:
        book.close()
