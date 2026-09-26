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
    editor.add_argument("--open", action="store_true", dest="open_browser")


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
    first = lines[0].strip()
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
        originals = _reading_text(book, packet)
        prefix, _ = _body_without_heading(originals[chapter], chapter, Path(row["path"]).stem)
        text = prefix + text
        name = f".story/drafts/workbench/第{chapter}章_基线{row['sha256']}_修订{uuid.uuid4().hex}.md"
        target = api.safe_path(book.root, name)
        api.atomic_write(target, text, set(), api.safe_path(book.root,
                         f".story/drafts/workbench/.backups/{uuid.uuid4().hex}.md"))
        return {"ok": True, "path": name, "base_sha256": row["sha256"],
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "formal_changed": False}
    finally:
        book.close()


def _editor_page(packet, reading, token):
    page = _render_desk(packet, reading)
    script = """const editors=new Map(),saved=new Map();
document.querySelectorAll('article').forEach(article=>{const area=document.createElement('textarea');area.value=article.querySelector('pre').textContent;area.hidden=true;area.setAttribute('aria-label','候选修订正文');area.style.cssText='width:100%;min-height:65vh;resize:vertical;font:18px/1.8 serif;padding:16px;border:1px solid #ccd5e0';article.append(area);editors.set(article.id,area);saved.set(article.id,area.value);});
const editButton=document.querySelector('#edit-chapter'),saveButton=document.querySelector('#save-candidate'),message=document.querySelector('#save-message');
function dirty(){return [...editors].some(([id,area])=>area.value!==saved.get(id));}
function editingControls(){const area=editors.get(current);editButton.disabled=!area;saveButton.disabled=!area;editButton.textContent=area&&!area.hidden?'阅读预览':'编辑本章';}
window.addEventListener('hashchange',editingControls);editingControls();
editButton.addEventListener('click',()=>{const area=editors.get(current);if(!area)return;area.hidden=!area.hidden;const pre=document.getElementById(current).querySelector('pre');pre.hidden=!area.hidden;if(area.hidden)pre.textContent=area.value;else area.focus();editingControls();});
saveButton.addEventListener('click',async()=>{const id=current,area=editors.get(id);if(!area)return;const text=area.value;saveButton.disabled=true;message.textContent='正在保存候选稿…';try{const response=await fetch(location.pathname+'candidate',{method:'POST',headers:{'Content-Type':'application/json','X-Story-Token':TOKEN},body:JSON.stringify({chapter:Number(id.slice(8)),text})});const result=await response.json();if(!response.ok)throw new Error(result.message||'保存失败');saved.set(id,text);message.textContent='已保存候选稿：'+result.path+'；正式稿未改变。';}catch(error){message.textContent=error.message+' 编辑内容仍保留，请勿关闭页面。';}finally{editingControls();}});
window.addEventListener('beforeunload',event=>{if(dirty()){event.preventDefault();event.returnValue='';}});""".replace("TOKEN", json.dumps(token))
    old_script = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
    combined = old_script + "\n" + script
    old_hash = base64.b64encode(hashlib.sha256(old_script.encode()).digest()).decode()
    new_hash = base64.b64encode(hashlib.sha256(combined.encode()).digest()).decode()
    page = page.replace(old_hash, new_hash).replace("default-src 'none';", "default-src 'none'; connect-src 'self';")
    page = page.replace('<script>'+old_script+'</script>', '<script>'+combined+'</script>')
    page = page.replace('<main class="manuscript">', '<div class="reader-controls"><button id="edit-chapter">编辑本章</button><button id="save-candidate">保存候选稿</button></div><p id="save-message" role="status" style="margin:8px 20px;overflow-wrap:anywhere">编辑仅存为候选，采用前仍需审查提交；切章保留未保存内容，关闭页面会丢失。</p><main class="manuscript">')
    page = page.replace('本地只读</span>', '本地候选编辑</span>').replace('本页不提供编辑保存。', '编辑内容另存为候选稿，正式正文不变。')
    return page.encode("utf-8")


def editor_server(root, limit=DEFAULT_LIMIT):
    book = _ReadOnlyBook(root)
    try:
        packet = snapshot(book, limit=limit)
        reading = _reading_text(book, packet)
    finally:
        book.close()
    token = secrets.token_urlsafe(32)
    route = '/' + token + '/'
    page = _editor_page(packet, reading, token)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass

        def reply(self, status, data, content_type="application/json; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(data)

        def allowed(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.allowed() or self.path != route:
                return self.reply(404, b'{}')
            self.reply(200, page, "text/html; charset=utf-8")

        def do_POST(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if (not self.allowed() or self.path != route+'candidate' or
                    self.headers.get("Origin") != origin or
                    self.headers.get("X-Story-Token") != token):
                return self.reply(403, b'{}')
            if self.headers.get("Content-Type") != "application/json" or self.headers.get("Transfer-Encoding"):
                return self.reply(400, b'{}')
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 7 * 1024 * 1024:
                    return self.reply(413, b'{}')
                raw = self.rfile.read(length)
                if len(raw) != length:
                    return self.reply(400, b'{}')
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    return self.reply(400, b'{}')
                result = _save_candidate(root, packet, payload.get("chapter"), payload.get("text"))
                self.reply(200, json.dumps(result, ensure_ascii=False).encode())
            except api.StoryError as error:
                self.reply(409, json.dumps({"message": str(error), "error": error.code}, ensure_ascii=False).encode())
            except (ValueError, UnicodeError, OSError):
                self.reply(400, b'{"message":"Invalid request or save failed"}')

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.editor_url = f"http://127.0.0.1:{server.server_port}"+route
    return server


def run(args):
    if args.command == "workbench-serve":
        server = editor_server(args.book, args.limit)
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
