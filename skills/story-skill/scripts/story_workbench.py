"""Read-only local author workbench snapshots and self-contained HTML export."""
from collections import Counter, deque
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import base64
import html
import json
import os
import errno
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
    global api, _runtime_paths, _loaded_runtime_sha256, _loaded_workbench_sha256, _loaded_runtime_error
    api = core
    # Capture the source set when the modules are loaded, rather than stamping
    # a running process with newer files when it later opens a workbench.
    paths = {Path(core.__file__)}
    for value in vars(core).values():
        source = getattr(value, '__file__', None)
        if source and Path(source).name.startswith('story_') and Path(source).suffix == '.py':
            paths.add(Path(source))
    _runtime_paths = tuple(sorted(paths, key=lambda path: path.name))
    try:
        sources = _runtime_sources()
        _loaded_runtime_sha256 = _runtime_digest(sources)
        _loaded_workbench_sha256 = sources[Path(__file__).name]
        _loaded_runtime_error = None
    except OSError as error:
        _loaded_runtime_sha256 = _loaded_workbench_sha256 = None
        _loaded_runtime_error = str(error)


def _runtime_sources():
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in _runtime_paths}


def _runtime_digest(sources):
    content = json.dumps(sources, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(content).hexdigest()


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
    editor.add_argument("--expected-book-id", help="Require the book identity selected by the bookshelf")
    editor.add_argument("--library-book", action="append", default=[], help="Another explicitly selected book on the local shelf; repeat as needed")
    editor.add_argument("--material-root", action="append", default=[], help="Explicit external directory of read-only author materials; repeat as needed")
    editor.add_argument("--open", action="store_true", dest="open_browser")
    command("workbench-status", "Check the registered local editor instance", DEFAULT_BUDGET, False)
    stop = command("workbench-stop", "Stop the registered editor after preserving edits in all its pages", DEFAULT_BUDGET, False)
    stop.add_argument("--saved", action="store_true", help="All editor pages have saved or downloaded their pending text")
    for name, help_text in (
        ('workbench-library-serve', 'Open the independent local bookshelf at a fixed address'),
        ('workbench-library-status', 'Check the independent bookshelf service'),
        ('workbench-library-stop', 'Stop the bookshelf without stopping book editors'),
    ):
        shelf = sub.add_parser(name, help=help_text)
        shelf.add_argument('--state-dir', help='Bookshelf state directory; defaults to ~/.codex/story-workbench')
        if name.endswith('-serve'):
            shelf.add_argument('--port', type=int, default=8765, help='Fixed loopback port; 0 is available for tests')
            shelf.add_argument('--library-book', action='append', default=[], help='Explicit book to register; repeat as needed')
            shelf.add_argument('--open', action='store_true', dest='open_browser')
        elif name.endswith('-stop'):
            shelf.add_argument('--saved', action='store_true', help='Confirm bookshelf closure; existing book editors stay running')


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
    if re.sub(r"\s+", " ", first) != re.sub(r"\s+", " ", title.strip()) or not re.match(rf"^第0*{chapter}章(?:\s|$)", first):
        return "", text
    end = 1
    while end < len(lines) and not lines[end].strip():
        end += 1
    return "".join(lines[:end]), "".join(lines[end:])


def _workbench_theme_control():
    return ('<label class="workbench-theme-control" for="workbench-theme">外观 '
            '<select id="workbench-theme" aria-label="工作台外观">'
            '<option value="system">跟随系统</option><option value="light">浅色</option>'
            '<option value="dark">深色</option></select></label>')


def _workbench_theme_script():
    # Only presentation preferences are shared across loopback ports. Never put
    # editor URLs, capability tokens, or manuscript state in browser storage.
    return r"""(() => {
 const root=document.documentElement;if(!root.hasAttribute('data-story-workbench'))return;
 const key='story-workbench-theme',cookieKey='story_workbench_theme',choices=['system','light','dark'];
 const control=document.getElementById('workbench-theme');
 const media=typeof window.matchMedia==='function'?window.matchMedia('(prefers-color-scheme: dark)'):null;
 const shared=location.protocol==='http:'&&location.hostname==='127.0.0.1';
 function stored(){
  if(shared)try{const value=document.cookie.split(';').map(part=>part.trim()).find(part=>part.startsWith(cookieKey+'='))?.slice(cookieKey.length+1);if(choices.includes(value))return value;}catch{}
  try{const value=localStorage.getItem(key);if(choices.includes(value))return value;}catch{}
  return null;
 }
 let preference=stored()||'system';
 function apply(){root.setAttribute('data-theme',preference==='system'?(media?.matches?'dark':'light'):preference);root.setAttribute('data-theme-preference',preference);if(control)control.value=preference;}
 function sync(){const value=stored();if(value!==null)preference=value;apply();}
 if(control)control.addEventListener('change',()=>{if(!choices.includes(control.value)){apply();return;}preference=control.value;try{localStorage.setItem(key,preference);}catch{}if(shared)try{document.cookie=cookieKey+'='+preference+'; Max-Age=31536000; Path=/; SameSite=Strict';}catch{}apply();});
 if(media){if(typeof media.addEventListener==='function')media.addEventListener('change',()=>{if(preference==='system')apply();});else if(typeof media.addListener==='function')media.addListener(()=>{if(preference==='system')apply();});}
 window.addEventListener('storage',event=>{if(event.key===key||event.key===null)sync();});
 window.addEventListener('focus',sync);window.addEventListener('pageshow',sync);apply();
})();
"""


def _workbench_theme_css():
    dark = ('color-scheme:dark;--ink:#e0ebf3;--muted:#9eb2c2;--line:#334858;'
            '--paper:#141c24;--card:#1b2732;--surface:#1b2732;--accent:#79cbf2;--green:#79cbf2;'
            '--wb-panel:#17222c;--wb-tint:#253746;--wb-hover:#2b4052;--wb-selected:#23485f;'
            '--wb-selected-ink:#c1eafe;--wb-link:#93d7f7;--wb-button:#38bdf8;--wb-button-ink:#082f49;'
            '--wb-warning:#e6c48b;--wb-warning-bg:#3c3323;--wb-warning-line:#75623d;'
            '--wb-success:#b6dfc3;--wb-success-bg:#243c30;--wb-success-line:#486d55;'
            '--wb-danger:#f2b8ae;--wb-danger-bg:#4a2d2d;--wb-danger-line:#88544d;'
            '--wb-mark:#665126;--wb-mark-current:#805b26;--wb-mark-ink:#fff1bd')
    return r"""
html[data-story-workbench]{color-scheme:light;--paper:#f5f8fb;--surface:#fff;--card:#fff;--ink:#253b4d;--muted:#5b7082;--line:#dde7ef;--accent:#147dad;--green:#147dad;--wb-panel:#f2f6fa;--wb-tint:#edf4f9;--wb-hover:#eaf4fb;--wb-selected:#e2f2fc;--wb-selected-ink:#125a7e;--wb-link:#146e99;--wb-button:#38bdf8;--wb-button-ink:#082f49;--wb-warning:#7b5a25;--wb-warning-bg:#faf1df;--wb-warning-line:#e9d7ad;--wb-success:#315f48;--wb-success-bg:#eaf3ee;--wb-success-line:#cbded1;--wb-danger:#882f26;--wb-danger-bg:#ffe3df;--wb-danger-line:#e6bbb5;--wb-mark:#fff1a6;--wb-mark-current:#ffc66b;--wb-mark-ink:#382600}
html[data-story-workbench][data-theme="dark"]{__DARK__}
@media(prefers-color-scheme:dark){html[data-story-workbench]:not([data-theme]){__DARK__}}
html[data-story-workbench]{background:var(--paper,#f6f7f9)}
html[data-story-workbench] body{color:var(--ink);background:var(--paper,#f6f7f9)}
.workbench-theme-control{display:inline-flex;align-items:center;gap:7px;font:12px/1.6 system-ui;white-space:nowrap;color:var(--muted)}
.workbench-theme-control select{font:inherit;padding:5px 7px;min-height:30px;max-width:125px;border:1px solid var(--line);border-radius:7px;color:var(--ink);background:var(--surface,#fff)}
.workbench-theme-control:focus-within{color:var(--ink)}
.menu-panel>.workbench-theme-control{display:flex;margin:2px 0 12px;justify-content:space-between}
.shelf-header-actions,.reader-header-actions{display:flex;align-items:center;gap:20px;flex-wrap:wrap;justify-content:flex-end}
html[data-story-workbench][data-workbench-view="overview"] header>.workbench-theme-control{float:right}
html[data-story-workbench][data-workbench-view="reader"] header{height:auto;min-height:66px;padding-top:8px;padding-bottom:8px}
html[data-story-workbench][data-workbench-view="reader"] .desk{height:calc(100vh - 66px)}
html[data-story-workbench] body>header:not(.shelf-header),html[data-story-workbench] .manuscript,html[data-story-workbench] .desk>main,html[data-story-workbench] .tools,html[data-story-workbench] .menu-panel,html[data-story-workbench] .diff-tools,html[data-story-workbench] #match-tools,html[data-story-workbench] .reader-controls{background:var(--surface,#fff)}
html[data-story-workbench] nav,html[data-story-workbench] aside{background:var(--wb-panel);border-color:var(--line)}
html[data-story-workbench] button,html[data-story-workbench] input,html[data-story-workbench] select,html[data-story-workbench] textarea{color:var(--ink);border-color:var(--line);background:var(--surface,#fff)}
html[data-story-workbench] #search,html[data-story-workbench] #full-query,html[data-story-workbench] #book-select,html[data-story-workbench] #chapter-search,html[data-story-workbench] #chapter-version{color:var(--ink);border-color:var(--line);background:var(--surface,#fff)}
html[data-story-workbench] textarea{caret-color:var(--ink)}
html[data-story-workbench] a{color:var(--wb-link)}
html[data-story-workbench] pre{color:inherit}
html[data-story-workbench] button:hover:not(:disabled),html[data-story-workbench] .chapter-link:hover,html[data-story-workbench] .menu[open]>summary{background:var(--wb-hover)}
html[data-story-workbench] .document,html[data-story-workbench] #locate,html[data-story-workbench] #refresh,html[data-story-workbench] .type-filters button,html[data-story-workbench] .search-clear{background:transparent}
html[data-story-workbench] .selected,html[data-story-workbench] .type-filters [aria-pressed="true"],html[data-story-workbench] .navigation-mode [aria-pressed="true"],html[data-story-workbench] #edit[aria-pressed="true"]{color:var(--wb-selected-ink);background:var(--wb-selected)!important;border-color:var(--line)}
html[data-story-workbench] button.primary,html[data-story-workbench] .book>button{background:var(--wb-button);color:var(--wb-button-ink);border-color:var(--wb-button)}
html[data-story-workbench] button.primary:hover:not(:disabled),html[data-story-workbench] .book>button:hover:not(:disabled){background:var(--wb-selected);color:var(--wb-selected-ink);border-color:var(--wb-selected)}
html[data-story-workbench] button.primary:disabled{background:var(--wb-tint);color:var(--muted);border-color:var(--line);opacity:1}
html[data-story-workbench] .book>button:disabled{background:var(--wb-tint);color:var(--muted);border-color:var(--line)}
html[data-story-workbench] .shelf-add,html[data-story-workbench] #books:not(:has(.book)),html[data-story-workbench] .stat,html[data-story-workbench] .badge,html[data-story-workbench] .book-kind,html[data-story-workbench] .identity-badge,html[data-story-workbench] #formatted code,html[data-story-workbench] #formatted pre,html[data-story-workbench] .comparison pre,html[data-story-workbench] #review-task-box{background:var(--wb-tint);border-color:var(--line);color:var(--ink)}
html[data-story-workbench] #list>details,html[data-story-workbench] .book-status-editor,html[data-story-workbench] .overview-link,html[data-story-workbench] .reader-controls,html[data-story-workbench] .book:hover{border-color:var(--line)}
html[data-story-workbench] .genre-tag,html[data-story-workbench] .book-classification .genre-tag,html[data-story-workbench] .identity-badge[data-kind="formal"],html[data-story-workbench] .book-status[data-status="serializing"],html[data-story-workbench] .diff-line.added{color:var(--wb-success);background:var(--wb-success-bg);border-color:var(--wb-success-line)}
html[data-story-workbench] .genre-tag.pending,html[data-story-workbench] .book-classification .genre-tag.pending,html[data-story-workbench] .identity-badge[data-kind="recovery"],html[data-story-workbench] .identity-badge[data-kind="pending"],html[data-story-workbench] .identity-badge[data-kind="uncertain"],html[data-story-workbench] #document-unsaved,html[data-story-workbench] .book-status,html[data-story-workbench] #history-note,html[data-story-workbench] #pending-box{color:var(--wb-warning);background:var(--wb-warning-bg);border-color:var(--wb-warning-line)}
html[data-story-workbench] .identity-badge[data-kind="candidate"],html[data-story-workbench] .book-status[data-status="completed"]{background:var(--wb-selected);color:var(--wb-selected-ink);border-color:var(--line)}
html[data-story-workbench] .book-status[data-status="not_applicable"]{background:var(--wb-tint);color:var(--muted)}
html[data-story-workbench] #state,html[data-story-workbench] .dirty:after,html[data-story-workbench] #directory-warning,html[data-story-workbench] .error{color:var(--wb-warning)}
html[data-story-workbench] #review-findings,html[data-story-workbench] .diff-line.removed{color:var(--wb-danger)}
html[data-story-workbench] .diff-line.removed{background:var(--wb-danger-bg)}
html[data-story-workbench] #match-text mark{color:var(--wb-mark-ink);background:var(--wb-mark)}
html[data-story-workbench] #match-text mark.current-match{background:var(--wb-mark-current);outline-color:var(--wb-warning)}
html[data-story-workbench] #list>details>summary,html[data-story-workbench] .chapter-link{color:var(--ink)}
html[data-story-workbench] .eyebrow,html[data-story-workbench] .muted,html[data-story-workbench] .brand,html[data-story-workbench] .reader-controls,html[data-story-workbench] h3{color:var(--muted)}
html[data-story-workbench][data-workbench-view="reader"] summary{color:var(--muted)}
html[data-story-workbench] .status{color:var(--green)}
html[data-story-workbench][data-workbench-view="shelf"] #message{background:var(--wb-tint);border-color:var(--line)}
@media(max-width:680px){.shelf-header-actions{gap:12px;align-items:flex-end;flex-direction:column}.reader-header-actions{gap:7px}.workbench-theme-control{font-size:11px}.workbench-theme-control select{max-width:115px}}
""".replace('__DARK__', dark)


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
    script = _workbench_theme_script() + script
    script_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    return f'''<!doctype html><html lang="zh-CN" data-story-workbench data-workbench-view="reader"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-{script_hash}'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{_escape(book["title"])} · Story Skill</title><style>
.reading-column{{min-width:0;min-height:0;display:flex;flex-direction:column}}.reading-column main{{flex:1;min-height:0}}.reader-controls{{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:10px 20px;border-bottom:1px solid #e3e7ee;background:white;font-size:12px;color:#7b8799}}button:disabled{{opacity:.4;cursor:default}}#chapter-search{{width:100%;padding:10px;border:1px solid #dce2eb;border-radius:7px;margin:8px 0 18px;font:inherit}}a:focus-visible,button:focus-visible,input:focus-visible{{outline:2px solid #476aab;outline-offset:2px}}*{{box-sizing:border-box}}[hidden]{{display:none!important}}body{{margin:0;color:#243047;background:#fafbfc;font:14px/1.7 system-ui,-apple-system,"PingFang SC",sans-serif}}a{{color:#476aab;text-decoration:none}}button{{font:inherit;cursor:pointer;background:white;border:1px solid #dce2eb;border-radius:7px;padding:6px 12px;color:#46556e}}header{{height:66px;border-bottom:1px solid #e3e7ee;display:flex;align-items:center;justify-content:space-between;padding:0 24px;background:#fff;gap:16px}}header strong{{font-size:16px}}.brand{{color:#70819d;font-size:12px;margin-right:24px}}.desk{{display:grid;grid-template-columns:260px minmax(0,1fr) 280px;height:calc(100vh - 66px)}}nav,aside{{padding:24px 18px;overflow:auto;background:#f6f8fb}}nav{{border-right:1px solid #e3e7ee}}aside{{border-left:1px solid #e3e7ee}}.manuscript{{overflow:auto;padding:48px clamp(24px,5vw,88px);background:white}}h1{{font-size:25px;line-height:1.5;margin:12px 0 32px}}h2{{font-size:19px}}h3{{font-size:13px;color:#748096;margin:24px 0 8px}}p{{overflow-wrap:anywhere}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:19px/2.05 "Songti SC","SimSun",serif;color:#303743;margin:0;max-width:800px}}summary{{cursor:pointer;color:#7b8799;font-size:12px;padding:16px 8px 8px;overflow-wrap:anywhere}}.chapter-link{{display:block;padding:10px 12px;border-radius:7px;color:#526078;overflow-wrap:anywhere}}.chapter-link:hover{{background:#edf1f7}}.selected{{background:#e7eefb!important;color:#315fa5;font-weight:600}}.eyebrow,.muted{{font-size:12px;color:#8894a6}}.overview-link{{display:block;padding:10px 12px;border-bottom:1px solid #e1e6ef;margin-bottom:8px}}.context-closed .desk{{grid-template-columns:260px minmax(0,1fr)}}.context-closed aside{{display:none}}article{{max-width:850px;margin:auto}}.status{{color:#5f8974;font-size:12px}}@media(max-width:1000px){{.desk{{grid-template-columns:210px minmax(0,1fr) 220px}}.manuscript{{padding:30px 24px}}}}@media(max-width:760px){{#toggle-context{{display:none}}.reader-controls{{padding:8px;flex-wrap:wrap}}.desk,.context-closed .desk{{grid-template-columns:160px minmax(0,1fr)}}aside{{display:none}}header{{padding:0 12px}}.brand{{display:none}}nav{{padding:14px 8px}}pre{{font-size:17px}}}}
</style><style>{_workbench_theme_css()}</style></head><body><header><div><span class="brand">STORY SKILL / 写作工作台</span><strong>{_escape(book["title"])}</strong></div><div class="reader-header-actions">{_workbench_theme_control()}<span class="status">本地只读</span> <button id="toggle-context" aria-expanded="true">章节信息</button></div></header>
<noscript><p>正文切换需要启用 JavaScript；可直接打开书目录中的章节原稿。</p></noscript><div class="desk"><nav id="book-nav" aria-label="作品目录"><a class="overview-link" href="#overview">作品概览</a><label class="eyebrow" for="chapter-search">搜索本页章节</label><input id="chapter-search" type="search" placeholder="输入章名或编号"><p id="search-empty" hidden role="status">未找到匹配章节</p><div class="eyebrow">正文目录 · 本页 {len(rows)} 章</div>{navigation or '<p>尚无正式章节</p>'}<p class="muted">{_escape(coverage)}</p></nav>
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
    theme_script = _workbench_theme_script()
    theme_hash = base64.b64encode(hashlib.sha256(theme_script.encode()).digest()).decode()
    return f'''<!doctype html>
<html lang="zh-CN" data-story-workbench data-workbench-view="overview"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-{theme_hash}'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'">
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
</style><style>{_workbench_theme_css()}</style></head><body><main>
<header>{_workbench_theme_control()}<span class="badge">{_escape(kind)}</span><span class="badge">本地只读快照</span><h1>{_escape(book["title"])}</h1><div class="meta">生成于 {_escape(packet["captured_at"])}</div></header>
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
</main><script>{theme_script}</script></body></html>'''


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
        # Specific output rules do not cover future workbench files. Probe
        # absent leaf paths rather than relying on a trailing-slash query.
        probe = uuid.uuid4().hex
        scope_paths = (relative_target, relative_backup, workbench / probe,
                       workbench / uuid.uuid4().hex / probe)
        ignored = [subprocess.run(
            ["git", "-C", str(repository), "check-ignore", "--no-index", "-q", "--", path.as_posix()],
            capture_output=True, timeout=5) for path in scope_paths]
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
    except (KeyError, ValueError, UnicodeError, RecursionError) as error:
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
            result['comparison'] = {'title': formal['title'], 'text': formal['text'],
                                    'prefix': formal.get('prefix', ''), 'sha256': formal['sha256']}
        except api.StoryError:
            pass
    return result


def _author_files(root, warnings=None, exclude=None):
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
        if exclude is not None and exclude(path.relative_to(root).as_posix()):
            continue
        try:
            if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                report(path.name)
                continue
            if path.is_dir() and (path.name == '作者有话说' or re.match(r'^\d{2}_', path.name) or
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
            relative = path.relative_to(root).as_posix()
            if exclude is not None and exclude(relative):
                continue
            inspected += 1
            if inspected > AUTHOR_LIMIT:
                api.fail('workbench_scan_limit', '创作材料超过扫描上限，请先整理归档。', maximum=AUTHOR_LIMIT)
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


def _material_roots(paths):
    if len(paths or []) > 10:
        api.fail('invalid_input', '最多关联10个只读材料目录。')
    roots = {}
    for value in paths or []:
        path = Path(value).expanduser().absolute()
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            api.fail('linked_path', '关联材料目录不能是链接。', path=str(path))
        path = path.resolve()
        if not path.is_dir():
            api.fail('invalid_input', '关联材料目录不存在。', path=str(path))
        key = hashlib.sha256(str(path).encode()).hexdigest()[:20]
        roots[key] = path
    return roots


def _linked_material_files(roots, warnings=None):
    rows, inspected = [], 0
    for key, root in roots.items():
        root_rows = []
        try:
            # The explicitly selected directory may disappear or be replaced
            # after startup. Bind it before scanning and discard partial rows
            # if its path changes; book documents must remain available.
            with api._pinned_directory(root) as directory:
                pending = list(root.iterdir())
                while pending:
                    api._verify_bound_directory(directory)
                    path = pending.pop()
                    inspected += 1
                    if inspected > AUTHOR_LIMIT:
                        api.fail('workbench_scan_limit', '关联材料超过扫描上限，请缩小指定目录。')
                    if path.name.startswith('.') or path.name == '__pycache__':
                        continue
                    relative = path.relative_to(root).as_posix()
                    try:
                        path = api.safe_path(root, relative)
                        if path.is_dir():
                            pending.extend(path.iterdir())
                        elif path.is_file() and path.suffix.lower() in AUTHOR_SUFFIXES:
                            root_rows.append({'id': 'linked:' + key + ':' + relative, 'path': str(path),
                                              'relative': relative, 'source_root': str(root), 'external': True,
                                              'title': path.stem, 'category': '关联材料（只读）'})
                    except (api.StoryError, OSError):
                        if warnings is not None:
                            warnings.append('已跳过无法安全读取的关联材料：' + str(path))
                api._verify_bound_directory(directory)
        except (api.StoryError, OSError) as error:
            if isinstance(error, api.StoryError) and error.code == 'workbench_scan_limit':
                raise
            if warnings is not None:
                warnings.append('已跳过无法安全读取的关联材料目录：' + str(root))
            continue
        rows.extend(root_rows)
    return sorted(rows, key=lambda r: r['path'])


def _linked_material_document(roots, document_id):
    row = next((r for r in _linked_material_files(roots) if r['id'] == document_id), None)
    if row is None:
        api.fail('invalid_input', '材料未列入本次明确关联的目录。')
    mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}.get(Path(row['path']).suffix.lower())
    raw = _author_read(Path(row['source_root']), row['relative'], 8 * 1024 * 1024 if mime else AUTHOR_TEXT_LIMIT)
    result = {'ok': True, **row, 'text': '' if mime else raw.decode('utf-8'), 'sha256': hashlib.sha256(raw).hexdigest(),
              'kind': 'material', 'editable': False, 'is_prose': False, 'context': None,
              'source': row['source_root'], 'status': '关联材料，只读；未与本书正式状态合并',
              'render_markdown': Path(row['path']).suffix.lower() == '.md',
              'historical_note': '来自明确关联的外部目录；修改与采用请交给助手核对原始书目录。'}
    if mime:
        result['image'] = 'data:' + mime + ';base64,' + base64.b64encode(raw).decode()
    return result


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


def _editor_catalog(root, offset=0, limit=DEFAULT_LIMIT, query='', focus=None, material_roots=None,
                    context_chapter=None):
    if type(offset) is not int or not isinstance(query, str) or len(query) > 200:
        api.fail('invalid_input', '目录参数无效。')
    if context_chapter is not None:
        _editor_chapter(context_chapter)
    if focus is not None:
        _editor_chapter(focus)
        book = _ReadOnlyBook(root)
        try:
            numbers = [r[0] for r in book.db.execute('SELECT chapter FROM chapter_state ORDER BY chapter DESC')]
            if focus in numbers and not query:
                offset = numbers.index(focus) // limit * limit
        finally:
            book.close()
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
    all_files += _linked_material_files(material_roots or {}, warnings)
    all_files = [_navigation_file(Path(root), row) for row in all_files]
    files = [row for row in all_files if not query or query.casefold() in row['path'].casefold()]
    return {'ok': True, 'chapters': [{'id': f"formal:{r['chapter']}", 'title': Path(r['path']).stem,
                                    'path': r['path'], 'category': '正式正文'} for r in rows],
            'files': files, 'related_files': all_files, 'warnings': warnings, 'total': total, 'offset': offset, 'limit': limit,
            'has_more': offset + len(rows) < total, 'snapshot': packet['snapshot']['id'],
            'workspace': _workspace_index(root, all_files, offset, limit, query, focus, context_chapter),
            'book_root': str(root), 'material_roots': [str(p) for p in (material_roots or {}).values()]}


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
    except (ValueError, UnicodeError, RecursionError):
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


def _navigation_file(root, row):
    """Use validated candidate chapter records as navigation, never adoption."""
    if row.get('external'):
        return row
    chapter = _chapter_hint(row['path'])
    relation = '同章号文件，采用关系须另核对'
    if row['id'].startswith('file:') and row['path'].startswith('.story/drafts/workbench/'):
        try:
            meta = _candidate_metadata(root, row['path'])
        except api.StoryError as error:
            if not error.code.startswith('workbench_metadata_'):
                raise
            # Keep damaged or incomplete candidates accessible by filename;
            # opening them still reports the existing metadata/recovery state.
        else:
            row = {**row, 'content_kind': _candidate_content_kind(meta)}
            if meta.get('chapter'):
                chapter = meta['chapter']
                relation = '来源记录章号，仅用于导航，采用关系须另核对'
    return {**row, 'chapter': chapter, 'relation': relation} if chapter else row


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
        label = f'第{number}章' + (' ' + plan['title'] if plan.get('title') else '（未登记章名）')
        text = '# ' + label + '\n\n## 本章目标\n\n' + plan.get('goal', '未登记目标')
        for beat in plan.get('beats', []):
            text += '\n\n### 变化节点\n\n' + beat['choice'] + '\n\n' + beat['change']
        text += '\n\n## 停笔点\n\n' + plan['stop']
        return {'ok': True, 'id': document_id, 'path': '工具章计划 · 第' + str(number) + '章',
                'title': label, 'text': text, 'kind': 'plan',
                'chapter': number, 'context': context, 'sha256': api.digest(text),
                'editable': False, 'status': '已保存计划，尚非正文'}
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
        result.update(kind='candidate', status='候选稿，采用关系待核对；未记录正式基线')
    result['source'] = meta.get('source') if isinstance(meta.get('source'), str) else None
    result['content_kind'] = _candidate_content_kind(meta) if result['kind'] == 'candidate' else 'material'
    if result['content_kind'] == 'material':
        if meta.get('chapter'):
            result['chapter'] = meta['chapter']
        if result['kind'] == 'candidate':
            result['status'] = '材料候选，采用关系待核对；不计作小说正文'
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
            result['comparison'] = {'title': formal['title'], 'text': formal['text'],
                                    'prefix': formal.get('prefix', ''), 'sha256': formal['sha256']}
            result['status'] = ('候选稿，采用关系待核对；正式基线已变化' if base_sha and base_sha != formal['sha256'] else
                                '候选稿，采用关系待核对；正式基线一致' if base_sha else
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


def _workspace_index(root, files, offset=0, limit=DEFAULT_LIMIT, query='', focus=None, context_chapter=None):
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
            book_id = _state_text(metadata, 'id', 200)
            kind = _state_text(metadata, 'kind', 40)
            if kind not in ('long', 'short', 'analysis'):
                api.fail('state_corrupt', '作品类型无效。', path=str(book.root))
            context = _chapter_context_data(book, context_chapter) if context_chapter else None
            context_snapshot = ({key: context[key] for key in ('chapter', 'revision', 'context_sha256')}
                                if context else None)
        grouped = {}
        for row in files:
            if row.get('external'):
                continue
            chapter = row.get('chapter') or _chapter_hint(row['path'])
            if chapter:
                grouped.setdefault(chapter, []).append({**row, 'relation': row.get('relation', '同章号文件，采用关系须另核对')})
        numbers = sorted(set(plans) | set(formal) | set(grouped))
        groups = []
        for n in numbers:
            plan = plans.get(n, {})
            label = f'第{n}章 ' + plan['title'] if plan.get('title') else Path(formal[n]).stem if n in formal else f'第{n}章'
            if re.fullmatch(r'[0-9]+', label):
                label = f'第{n}章（未登记章名）'
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
        if focus is not None:
            _editor_chapter(focus)
            for index, group in enumerate(groups):
                if group['chapter'] == focus:
                    offset = index // limit * limit
                    break
        classification = _book_classification(book.root)
        try:
            book.verify_state_path()
            # The open database is bound to its original directory. Also check
            # the current pathname before attaching source files to that ID.
            with api._pinned_directory(book.path.parent) as directory:
                current = api.publish._bound_stat(api._BoundFile(directory, book.path.name))
            if not os.path.samestat(book._state_stat, current):
                raise ValueError('book path changed')
            if book.meta('id') != book_id:
                raise ValueError('book identity changed')
        except (api.StoryError, OSError, ValueError, sqlite3.Error):
            classification = _classification_unavailable('作品身份在读取题材时变化，分类待核对。')
        return {'title': title, 'book_id': book_id, 'kind': kind, 'kind_verified': True,
                'classification': classification,
                'revision': revision, 'context_snapshot': context_snapshot,
                'captured_at': _utc_now(), 'formal_total': len(formal),
                'planned_total': len(plans), 'next_chapter': next_chapter, 'total': len(groups),
                'groups': groups[offset:offset + limit], 'has_more': offset + limit < len(groups),
                'offset': offset, 'limit': limit}
    finally:
        book.close()


def _chapter_context_data(book, chapter):
    """Fingerprint relevant auxiliary data, excluding unrelated book revisions."""
    row = book.db.execute('SELECT data FROM plans WHERE chapter=?', (chapter,)).fetchone()
    plan = json.loads(row['data']) if row else None
    row = book.db.execute('SELECT summary,receipt,sha FROM chapter_state WHERE chapter=?', (chapter,)).fetchone()
    prior = book.db.execute('SELECT summary FROM chapter_state WHERE chapter=?', (chapter - 1,)).fetchone()
    receipt = json.loads(row['receipt']) if row else {}
    data = {'chapter': chapter, 'plan': plan, 'previous_summary': prior['summary'] if prior else None,
            'formal_sha256': row['sha'] if row else None,
            'review': receipt.get('input', {}).get('review'), 'imported': receipt.get('quality') == 'imported_unverified'}
    return {**data, 'revision': book.meta('revision'), 'context_sha256': _hash_json(data)}


def _chapter_context(root, chapter):
    if not chapter:
        return None
    book = _ReadOnlyBook(root)
    try:
        with book.read_snapshot():
            return _chapter_context_data(book, chapter)
    finally:
        book.close()


def _editor_document(root, document_id):
    result = _editor_document_raw(root, document_id)
    number = result.get('chapter') or _chapter_hint(result.get('path', ''))
    if 'context' not in result:
        result['context'] = _chapter_context(root, number)
    if result.get('kind') == 'formal' and result['context']['formal_sha256'] != result['sha256']:
        api.fail('workbench_changed', '正式稿在读取期间已变化，请重新读取；不会把新审查记录配给旧正文。')
    path = result.get('path', '')
    result['historical_note'] = ('记录内容反映编写时状态；当前进度请看顶部。'
                                 if any(w in path for w in ('记录', '历史', '报告', '测试结果')) else '规划材料保留编写时内容；实际正文进度以顶部为准。' if any(w in path for w in ('大纲', '细纲', '策划')) else '')
    result['is_prose'] = result.get('kind') in ('formal', 'candidate') and result.get('content_kind') != 'material' and bool(number)
    result['content_kind'] = 'prose' if result['is_prose'] else 'material'
    result['render_markdown'] = result.get('kind') == 'plan' or (not result['is_prose'] and
        (path.lower().endswith('.md') or result.get('kind') == 'candidate'))
    if result['render_markdown']:
        result['markdown_path'] = _markdown_resource_path(Path(root), result)
    if result.get('kind') == 'candidate' and result['is_prose'] and result.get('context'):
        if result['sha256'] == result['context']['formal_sha256']:
            result['status'] = '内容与当前正式稿逐字一致；不据此推断采用历史'
    return result


def _markdown_resource_path(root, document):
    """Locate a material candidate's original directory without changing its text."""
    if document.get('external') or document.get('metadata_error') or document.get('kind') == 'plan':
        return None
    if document.get('kind') != 'candidate':
        return document.get('path')
    try:
        files = {row['id']: row['path'] for row in _author_files(root)}
        source, seen = document.get('source'), {document['id']}
        # Each step consumes one discovered source ID; cycles and the finite
        # material inventory bound traversal without limiting valid save history.
        while isinstance(source, str) and source in files and source not in seen:
            seen.add(source)
            relative = files[source]
            api.safe_path(root, relative)
            if source.startswith('pending:'):
                pending = _pending_document(root, source)
                if pending['content_kind'] != 'material':
                    return None
                source = pending.get('source')
                continue
            if not source.startswith('file:'):
                return None
            if not relative.startswith('.story/drafts/workbench/'):
                return relative
            meta = _candidate_metadata(root, relative)
            if _candidate_content_kind(meta) != 'material':
                return None
            source = meta.get('source')
    except (api.StoryError, OSError, ValueError):
        pass
    return None


def _editor_metrics(root, payload):
    doc = _editor_document(root, payload.get('id'))
    text = payload.get('text')
    selected = payload.get('selection', '')
    if not isinstance(text, str) or not isinstance(selected, str) or len(text.encode('utf-8')) + len(selected.encode('utf-8')) > 2 * AUTHOR_TEXT_LIMIT:
        api.fail('invalid_input', '字数核对文本过大或无效。')
    plan = (doc.get('context') or {}).get('plan') or {}
    method = plan.get('count_method', 'visible_nonspace_v1') if doc['is_prose'] else 'visible_nonspace_v1'
    counted = doc.get('prefix', '') + text if doc['is_prose'] else '\n' + text
    target = plan.get('length') if doc['is_prose'] else None
    # The range alone is not a valid chapter-length result: legacy methods
    # also reject attached marks and blank filler glyphs that v2 does not count.
    if target:
        baseline = doc if doc.get('kind') == 'formal' else doc.get('comparison')
        previous_text = (baseline.get('prefix', '') + baseline['text']).removeprefix('\ufeff') if baseline else None
        checked = api.lint_text(counted.removeprefix('\ufeff'), plan, previous_text=previous_text)
        counts, value = checked['counts'], checked['length_count']
        length_issues = [issue['code'] for issue in checked['errors']
                         if issue['code'] in ('length', 'invisible_padding')]
    else:
        counts = api.manuscript_counts(counted, plan.get('count_title', False) if doc['is_prose'] else True)
        value, length_issues = counts[method], []
    within_target = target[0] <= value <= target[1] if target else None
    return {'ok': True, 'count': value, 'method': method, 'target': target,
            'include_title': bool(plan.get('count_title')) if doc['is_prose'] else None,
            'selection': api.manuscript_counts('\n' + selected, True)[method],
            'within_target': within_target, 'in_range': not length_issues if target else None,
            'length_issues': length_issues,
            'is_prose': doc['is_prose'], 'text_sha256': api.digest(text)}


def _editor_review_task(root, document_id, expected_sha=None):
    doc = _editor_document(root, document_id)
    if expected_sha is not None and expected_sha != doc['sha256']:
        api.fail('stale_snapshot', '所选文件已变化，请刷新后重新生成审稿任务。')
    if not doc.get('editable') or doc.get('needs_recovery'):
        api.fail('invalid_input', '请先将可编辑内容保存为完整候选稿。')
    context = doc.get('context') or {}
    lint = None
    check_scope = 'material'
    check_note = '这是材料文件，未运行完整章节检查。'
    if doc['is_prose'] and not context.get('plan'):
        check_scope = 'no_plan'
        check_note = '尚无已保存章计划，未运行完整章节检查；请人工核对章号、章名和篇幅。'
    elif doc['is_prose']:
        # Book.lint is the same read-only path used by the CLI, including
        # heading, length exception, outline and destination checks. Read the
        # saved candidate, never an unsaved editor buffer or a temporary file.
        book = api.Book(root, read_only=True)
        try:
            try:
                lint = book.lint(context['chapter'], Path(root) / doc['path'])
            except api.StoryError as error:
                check_scope = 'blocked'
                check_note = f'完整章节工具检查未完成（{error.code}）：{error}。请在审稿时核对原因。'
            else:
                # The page binds raw file bytes; lint binds UTF-8 text after
                # stripping an optional BOM. Check both without conflating them.
                raw = _author_read(Path(root), doc['path'])
                if (hashlib.sha256(raw).hexdigest() != doc['sha256'] or
                        api.digest(raw.decode('utf-8-sig')) != lint['draft_sha256']):
                    api.fail('stale_snapshot', '所选文件在检查期间变化，请刷新后重试。')
                check_scope = 'complete_chapter'
                check_note = ('已对保存文件运行只读完整章节工具检查；若本文件只是章中片段，'
                              '章头和整章字数提示仅供参考。工具结果不代表语义审查通过。')
        finally:
            book.close()
    prompt = ('请审查以下已保存文件，先核对文件仍与本次校验值一致，再读取本书约定及相关细纲、前文。\n'
              f'书目录：{Path(root).absolute()}\n文件：{doc["path"]}\n校验值：{doc["sha256"]}\n'
              f'文件状态：{doc["status"]}\n'
              f'本地工具检查范围：{check_note}\n'
              '先检查叙事、人物行动、连续性、阅读期待、字数与中文格式，指出具体依据。\n'
              '本次只审查，不自动修改或正式采用；需要修改时另存候选。不要将页面格式检查当成人工审稿。')
    return {'ok': True, 'prompt': prompt, 'lint': lint, 'check_scope': check_scope,
            'check_note': check_note, 'status': '审稿任务已生成，尚未交给助手执行'}


def _editor_writing_task(root):
    """Build a handoff using the existing bound-outline writing-brief contract."""
    selected = _library_entry(root)
    book = api.Book(root, read_only=True)
    try:
        if book.meta('id') != selected['book_id']:
            api.fail('workbench_book_changed', '作品身份已变化，请重新打开作品。')
        chapter = book.meta('last_chapter') + 1
        brief = book.writing_brief(chapter, budget=48000)
    finally:
        book.close()
    _library_entry(root, selected)
    current = _ReadOnlyBook(root)
    try:
        if current.meta('id') != brief['book_id'] or current.meta('revision') != brief['revision']:
            api.fail('stale_snapshot', '写前简报生成期间作品状态已变化，请刷新后重新生成。')
        source = brief['adopted_outline']
        if hashlib.sha256(_author_read(Path(root), source['path'], api.outline.MAX_OUTLINE_BYTES)).hexdigest() != source['sha256']:
            api.fail('stale_snapshot', '写前简报生成期间已采用细纲发生变化，请重新核对绑定。')
        current.verify_state_path()
    finally:
        current.close()
    prompt = (f'请继续创作本书第{chapter}章，先核对作品、revision 和已采用细纲仍与以下写前简报一致。\n'
              '读取本书约定、相邻正式正文和本章细纲，按现有写作流程生成候选、审稿并核对。\n'
              '不要将此任务已生成视为已执行、已保存、已正式提交或已发布；依据实际结果报告状态。\n'
              '本章写前简报：\n' + json.dumps(brief, ensure_ascii=False, indent=2))
    return {'ok': True, 'chapter': chapter, 'book_id': selected['book_id'], 'brief': brief,
            'status': '写作任务已生成，等待交给助手执行', 'prompt': prompt}


def _editor_search(root, query, offset=0, limit=30, material_roots=None):
    if not isinstance(query, str) or not query.strip() or len(query) > 200 or type(offset) is not int or offset < 0:
        api.fail('invalid_input', '请输入1至200字搜索词。')
    query = query.strip()
    book = _ReadOnlyBook(root)
    try:
        with book.read_snapshot():
            meta = _meta_map(book.db); meta['__root'] = str(book.root)
            rows = [{'id': f'formal:{r["chapter"]}', 'path': _chapter_path(meta, r['chapter']),
                     'title': Path(_chapter_path(meta, r['chapter'])).stem, 'sha256': r['sha'],
                     'category': '正式正文', 'kind': 'formal'}
                    for r in book.db.execute('SELECT chapter,sha FROM chapter_state ORDER BY chapter')]
    finally:
        book.close()
    warnings = []
    rows += [r for r in _author_files(Path(root), warnings) if Path(r['path']).suffix.lower() in ('.md', '.txt')]
    rows += [r for r in _linked_material_files(material_roots or {}, warnings) if Path(r['path']).suffix.lower() in ('.md', '.txt')]
    hits, read_bytes, checked = [], 0, 0
    # Bound each request and explicitly report incomplete coverage; never silently call a partial scan complete.
    for row in rows:
        if checked >= 3000 or read_bytes >= 32 * 1024 * 1024:
            warnings.append('达到本轮搜索上限，尚未覆盖全部文件。可缩小作品材料范围后重试。')
            break
        checked += 1
        try:
            raw = _author_read(Path(row['source_root']), row['relative']) if row.get('external') else _author_read(Path(root), row['path'])
            read_bytes += len(raw)
            if row.get('sha256') and hashlib.sha256(raw).hexdigest() != row['sha256']:
                warnings.append('正式稿外改，未纳入搜索：' + row['path']); continue
            text = raw.decode('utf-8')
            match = re.search(re.escape(query), text, re.IGNORECASE)
            if match:
                found = match.start()
                hits.append({**{key: row[key] for key in ('id', 'path', 'title', 'category', 'kind', 'external', 'modified') if key in row},
                             'excerpt': text[max(0, found - 45):found + len(query) + 110].replace('\n', ' ')})
        except (OSError, UnicodeError, api.StoryError):
            warnings.append('无法读取，未纳入搜索：' + row['path'])
    return {'ok': True, 'results': hits[offset:offset + limit], 'total': len(hits), 'offset': offset,
            'has_more': offset + limit < len(hits), 'checked': checked, 'warnings': warnings,
            'complete': checked == len(rows) and not warnings}


CLASSIFICATION_PATHS = ('创作约定.md', '01_大纲细纲/全书总纲.md', '00_项目策划/开书策划.md')
CLASSIFICATION_TEXT_LIMIT = 256 * 1024
CLASSIFICATION_FIELDS = ('题材', '故事题材', '作品题材', '题材定位', '题材方向', '题材与期待',
                         '题材与读者期待', '类型', '类型定位', '作品类型', '类型与期待',
                         '类型与阅读期待', '类型与读者期待')
CLASSIFICATION_FIELD_LIMIT = 12
CLASSIFICATION_TAG_LIMIT = 12
_CLASSIFICATION_FIELD = re.compile(r'^(' + '|'.join(sorted(CLASSIFICATION_FIELDS, key=len, reverse=True)) + r')\s*[:：]\s*(.*)$')
_CLASSIFICATION_SKIPPED_SECTION = re.compile(r'历史|旧版|旧规划|候选|草稿|示例|例子|参考|备选|未采用|平台分类|平台定位|平台标签|番茄|七猫|晋江|起点|盐选')
_CLASSIFICATION_NON_SUBJECT = re.compile(r'平台|番茄|七猫|晋江|起点|盐选|待定|待核对|待确认|未确定|未登记|暂无|不选')
_CLASSIFICATION_UNCONFIRMED = re.compile(r'^状态\s*[:：].*(?:候选|草稿|未采用|待定|待核对|待确认|未确定)')


def _classification_unavailable(note):
    return {'status': 'unavailable', 'tags': [], 'sources': [], 'note': note}


def _classification_fields(text):
    fence, skipped_level, fields = None, None, []
    for number, line in enumerate(text.splitlines(), 1):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if marker:
            current = marker.group(1)
            if fence is None:
                fence = current
            elif current[0] == fence[0] and len(current) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        heading = re.match(r'^ {0,3}(#{1,6})\s+(.+)', line)
        if heading:
            level, title = len(heading.group(1)), heading.group(2)
            if skipped_level is not None and level <= skipped_level:
                skipped_level = None
            if _CLASSIFICATION_SKIPPED_SECTION.search(title):
                skipped_level = min(skipped_level, level) if skipped_level is not None else level
            continue
        if skipped_level is not None or line.startswith(('    ', '\t')) or line.lstrip().startswith('>'):
            continue
        plain = re.sub(r'^ {0,3}(?:[-*+]\s+)?', '', line).replace('**', '')
        if _CLASSIFICATION_UNCONFIRMED.match(plain):
            # A document status applies to the whole document, including fields
            # above it; never retain a declaration from an unconfirmed copy.
            return []
        match = _CLASSIFICATION_FIELD.fullmatch(plain)
        if match:
            fields.append((number, match.group(1), match.group(2).strip()))
    return fields


def _classification_tags(value):
    # A short explicit declaration is displayable. Narrative positioning,
    # platform categories and manuscript length never become guessed subjects.
    first = re.split(r'[；;。]', value, maxsplit=1)[0].strip()
    tags = []
    for item in re.split(r'[＋+、，,/／|｜]', first):
        item = item.strip().strip('`').strip()
        if (not item or _CLASSIFICATION_NON_SUBJECT.search(item) or
                item.startswith(('兼有', '兼具', '读者', '期待', '依据', '主投', '拟投'))):
            continue
        if len(item) > 40 or re.search(r'[。；;:：＝=<>\n]', item):
            continue
        if item not in ('小说', '分析', '同上', '无', '长篇', '短篇', '长篇小说', '短篇小说',
                        '短故事', '作品分析', '男频', '女频', '男频短故事', '女频短故事') and item not in tags:
            tags.append(item)
    return tags


def _book_classification(root):
    sources, declarations, issues = [], [], []
    for relative in CLASSIFICATION_PATHS:
        try:
            path = api.safe_path(Path(root), relative)
            if not path.exists():
                continue
            text = _author_read(Path(root), relative, CLASSIFICATION_TEXT_LIMIT).decode('utf-8-sig')
            fields = list(_classification_fields(text))
        except (api.StoryError, OSError, UnicodeError, ValueError) as error:
            issues.append(relative + '：' + str(error))
            continue
        if len(fields) > CLASSIFICATION_FIELD_LIMIT:
            issues.append(relative + '：明确类型字段超过核对上限')
        for line, field, value in fields[:CLASSIFICATION_FIELD_LIMIT]:
            if len(value) > 1000:
                issues.append(relative + '：类型字段过长，待人工核对')
                continue
            tags = _classification_tags(value)
            if not tags:
                continue
            sources.append({'path': relative, 'field': field, 'value': value, 'line': line})
            if len(tags) > CLASSIFICATION_TAG_LIMIT:
                issues.append(relative + '：题材标签超过核对上限')
            else:
                declarations.append(tags)
    if declarations and any(set(tags) != set(declarations[0]) for tags in declarations[1:]):
        issues.append('现行材料中的明确题材字段不一致，未自动选择其中一份')
    if issues:
        return {'status': 'needs_review', 'tags': [], 'sources': sources, 'note': '；'.join(issues)}
    if not declarations:
        return {'status': 'missing', 'tags': [], 'sources': [], 'note': '尚无可直接显示的明确题材字段，待分类。'}
    return {'status': 'recorded', 'tags': declarations[0], 'sources': sources,
            'note': '来自本书现行材料的明确题材记录；不表示平台分类已确认。'}


_WRITING_STATUS_FIELD = re.compile(r'^(作品状态|写作状态|创作状态|连载状态|完本状态|完结状态|当前状态|当前有效状态|状态)\s*[:：]\s*(.*)$')
_WRITING_STATUS_UNCERTAIN = re.compile(r'未完|未完成|未完本|未完结|尚未|没有|还没|未宣布|未确认|未证实|不是|并非|不算|如果|假如|若|是否|历史|旧版|参考|曾经|此前|待定|待核对|待确认|待确定|未确定|候选|草稿|未采用|预计|预期|计划|目标|拟|授权|将|准备|希望')
_WRITING_STATUS_PLATFORM = re.compile(r'平台|番茄|七猫|晋江|起点|盐选|后台|发布|上架')


def _unknown_writing_status(note):
    return {'value': 'unknown', 'verified': False, 'source': 'unknown', 'note': note, 'sources': []}


def _writing_status_fields(text):
    skipped_level, fields = None, []
    for index, line in api.outline._unfenced_lines(text.splitlines()):
        number = index + 1
        heading = api.outline._heading_parts(line)
        if heading:
            level, title = heading
            if skipped_level is not None and level <= skipped_level:
                skipped_level = None
            if (_CLASSIFICATION_SKIPPED_SECTION.search(title) or _WRITING_STATUS_PLATFORM.search(title)
                    or re.fullmatch(r'(?:完本|完结)计划|目标状态', title)):
                skipped_level = min(skipped_level, level) if skipped_level is not None else level
            elif skipped_level is None and re.fullmatch(r'当前有效状态[（(](?:已)?(?:完本|完结)[）)]', title):
                fields.append((number, '当前有效状态', title, 'completed'))
            continue
        if skipped_level is not None:
            continue
        plain = re.sub(r'^ {0,3}(?:[-*+]\s+)?', '', line).replace('**', '')
        match = _WRITING_STATUS_FIELD.fullmatch(plain)
        if not match:
            continue
        field, value = match.group(1), match.group(2).strip()
        first = re.split(r'[；;。]', value, maxsplit=1)[0].strip()
        clauses = [clause.strip() for clause in re.split(r'[，,；;。]', value)]
        # An unconfirmed document is not a current declaration, even when
        # its qualification follows a positive clause. Cover/classification
        # work can still be pending after the manuscript is complete.
        if any(re.search(r'候选|草稿|未采用|待定|待核对|待确认|待确定|未确定', clause)
               and (clause_index == 0 or not re.match(r'^(?:封面|(?:平台)?分类|(?:平台)?标签)', clause))
               for clause_index, clause in enumerate(clauses)):
            return []
        negative = re.fullmatch(r'(?:本书)?(?:正文|故事)?(?:尚未|未|(?:不是|并非|不算)(?:已)?)'
                                r'(完本|完结|连载(?:中)?)', first)
        if len(value) <= 1000 and negative:
            status = 'not_serializing' if negative.group(1).startswith('连载') else 'not_completed'
            fields.append((number, field, value, status))
            continue
        if len(value) > 1000 or _WRITING_STATUS_UNCERTAIN.search(first) or _WRITING_STATUS_PLATFORM.search(first):
            continue
        completed = (re.fullmatch(r'(?:已)?(?:完本|完结)', first) or
                     re.search(r'已完本|已完结|正文完结|故事完结|本书(?:正文)?(?:已)?完本', first))
        serializing = re.fullmatch(r'(?:正在)?连载(?:中)?', first)
        if completed or serializing:
            fields.append((number, field, value, 'completed' if completed else 'serializing'))
    return fields


def _book_writing_status(root, entry):
    if entry['kind'] == 'analysis':
        return {'value': 'not_applicable', 'verified': True, 'source': 'unknown',
                'note': '作品分析不适用连载或完本状态。', 'sources': []}
    if entry.get('shelf_status') in ('serializing', 'completed'):
        return {'value': entry['shelf_status'], 'verified': True, 'source': 'author_mark',
                'note': '作者手动标记的本地作品状态；不代表平台状态或全书审查通过。', 'sources': []}
    sources, values, issues = [], set(), []
    for relative in CLASSIFICATION_PATHS:
        try:
            path = api.safe_path(Path(root), relative)
            if not path.exists():
                continue
            text = _author_read(Path(root), relative, CLASSIFICATION_TEXT_LIMIT).decode('utf-8-sig')
            fields = _writing_status_fields(text)
            if len(fields) > CLASSIFICATION_FIELD_LIMIT:
                issues.append('明确作品状态字段超过核对上限')
            for line, field, value, status in fields[:CLASSIFICATION_FIELD_LIMIT]:
                sources.append({'path': relative, 'field': field, 'value': value, 'line': line})
                values.add(status)
        except (api.StoryError, OSError, UnicodeError, ValueError):
            issues.append('无法安全读取现行状态材料：' + relative)
    negative_states = {'not_completed': 'completed', 'not_serializing': 'serializing'}
    denied = {status for negative, status in negative_states.items() if negative in values}
    values.difference_update(negative_states)
    if issues or len(values) > 1 or denied.intersection(values):
        result = _unknown_writing_status('；'.join(issues) if issues else '现行材料中的作品状态不一致，请核对或手动标记。')
        result['sources'] = sources
        return result
    if not values:
        result = _unknown_writing_status('现行材料只有明确的否定状态，不能据此确认连载或完本。' if denied else
                                         '尚无明确的本地连载或完本记录；可手动标记，不按章数或篇幅推断。')
        result['sources'] = sources
        return result
    return {'value': next(iter(values)), 'verified': True, 'source': 'recorded', 'sources': sources,
            'note': '来自现行材料的明确本地作品状态；不代表平台状态或全书审查通过。'}


def _unknown_book_update(note):
    return {'updated_at': None, 'updated_at_verified': False, 'updated_at_source': None,
            'updated_at_note': note}


def _update_time(value):
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?', value):
        raise ValueError('invalid recorded update time')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed).astimezone(timezone.utc)


def _book_update(root, expected):
    """Newest recorded activity or current author-file mtime; never service time."""
    book, newest, source, warnings = None, None, None, []
    try:
        root = Path(root)
        book = _ReadOnlyBook(root)
        with book.read_snapshot():
            metadata = _meta_map(book.db)
            if metadata.get('id') != expected['book_id']:
                return _unknown_book_update('作品身份已变化，更新时间待核对。')
            metadata['__root'] = str(root)
            event = book.db.execute('SELECT seq,kind,created FROM events '
                                    'ORDER BY julianday(created) DESC,seq DESC LIMIT 1').fetchone()
            last_event = book.db.execute('SELECT created FROM events ORDER BY seq DESC LIMIT 1').fetchone()
            invalid_event = book.db.execute(
                'SELECT 1 FROM events WHERE julianday(created) IS NULL LIMIT 1').fetchone()
            if invalid_event is not None:
                warnings.append('状态事件中存在无法核对的时间')
            if last_event is not None:
                try:
                    _update_time(last_event['created'])
                except (ValueError, TypeError):
                    warnings.append('最近记录的状态事件时间格式无法核对')
            if event is not None:
                try:
                    newest = _update_time(event['created'])
                    source = {'kind': 'state_event', 'event': event['kind'], 'seq': event['seq']}
                except (ValueError, TypeError):
                    warnings.append('最近状态事件的时间格式无法核对')
            chapters = [row['chapter'] for row in book.db.execute(
                'SELECT chapter FROM chapter_state ORDER BY chapter LIMIT ?', (MAX_CHAPTERS + 1,))]
            if len(chapters) > MAX_CHAPTERS:
                return _unknown_book_update('正式章数超过核对上限，更新时间待核对。')
            formal_paths = {_chapter_path(metadata, chapter) for chapter in chapters}

        assembly = metadata.get('short_assembly_path')

        def excluded(relative):
            if relative == assembly or relative.endswith('.pending.json'):
                return True
            path = Path(relative)
            # Author leaf titles may contain words such as “备份证据”. Only
            # explicit directory labels identify generated or archived copies.
            directories = path.parts[:-1] if path.suffix else path.parts
            return any(re.match(r'^(?:\d{2}_)?(?:历史版本|历史归档|作品归档|归档|备份|导出|自动恢复)(?:_|$)', part) or
                       re.match(r'^(?:\d{2}_)?(?:archives?|exports?|backups?|recovery)(?:[_-]|$)',
                                part.casefold()) for part in directories)

        files = _author_files(root, warnings, exclude=excluded)
        paths = {row['path'] for row in files if row.get('category') != '自动恢复'} | formal_paths
        for relative in sorted(paths):
            if excluded(relative) and relative not in formal_paths:
                continue
            try:
                path = api.safe_path(root, relative)
                with api._pinned_directory(path.parent) as directory:
                    facts = api.publish._bound_stat(api._BoundFile(directory, path.name))
                modified = datetime.fromtimestamp(facts.st_mtime, timezone.utc)
                if newest is None or modified > newest:
                    newest, source = modified, {'kind': 'author_file', 'path': relative}
            except (api.StoryError, OSError, ValueError, OverflowError):
                warnings.append('无法安全核对文件时间：' + relative)

        book.verify_state_path()
        with api._pinned_directory(book.path.parent) as directory:
            current = api.publish._bound_stat(api._BoundFile(directory, book.path.name))
        if not os.path.samestat(book._state_stat, current) or book.meta('id') != expected['book_id']:
            return _unknown_book_update('作品身份在读取时间时变化，更新时间待核对。')
        if newest is None:
            return _unknown_book_update('尚无可靠的作品状态事件或当前作者文件时间。')
        updated_at = newest.isoformat().replace('+00:00', 'Z')
        source['at'] = updated_at
        note = '最近一次作品状态事件或当前作者文件修改时间；不含服务、导出、归档和自动恢复。'
        if warnings:
            note = '更新时间尚未完整核对：' + '；'.join(warnings[:3])
        return {'updated_at': updated_at, 'updated_at_verified': not warnings,
                'updated_at_source': source, 'updated_at_note': note}
    except (api.StoryError, OSError, ValueError, TypeError, sqlite3.Error):
        return _unknown_book_update('无法完整核对原作品的更新时间。')
    finally:
        if book is not None:
            book.close()


def _library_roots(root, extra=None):
    paths = [Path(root).absolute()] + [Path(p).expanduser().absolute() for p in (extra or [])]
    if len(paths) > 30:
        api.fail('invalid_input', '工作台书架最多登记30本作品。')
    result = {}
    for path in paths:
        entry = _library_entry(path)
        entry['classification'] = _book_classification(entry['root'])
        entry['writing_status'] = _book_writing_status(entry['root'], entry)
        entry.update(_book_update(entry['root'], entry))
        _library_entry(entry['root'], entry)
        result[entry['key']] = entry
    return result


def _library_open(library, key, current_root=None, current_url=None):
    if not isinstance(key, str) or key not in library:
        api.fail('invalid_input', '作品未登记在本次书架中。')
    entry = library[key]
    root = entry['root']
    if entry.get('book_id'):
        _library_entry(root, entry)
    if current_root is not None and root == str(current_root):
        return {'ok': True, 'url': current_url, 'outdated': False}

    def service_result(state):
        if entry.get('book_id'):
            _library_entry(root, entry)
            if state.get('book_id') != entry['book_id']:
                api.fail('workbench_book_changed', '服务未确认所选作品身份；请保留当前编辑并核对原工作台。', path=root)
        return {'ok': True, 'url': state['url'], 'outdated': state.get('outdated', False)}

    state = _service_request(root)
    if state.get('running'):
        return service_result(state)
    # Only a stopped record or a stable, independently verified exit permits
    # recovery. The new editor still acquires its own OS lease before starting.
    record_path = api.safe_path(Path(root), '.story/workbench-service.json')
    if record_path.exists():
        raw = _author_read(Path(root), '.story/workbench-service.json', 8192)
        record = json.loads(raw)
        verified_exit = state.get('exit_verified') is True and state.get('record_sha256') == hashlib.sha256(raw).hexdigest()
        if record.get('stopped') is not True and not verified_exit:
            api.fail('workbench_unreachable', '此作品的服务无法确认已退出，请先核对原工作台。')
    import sys
    import time
    command = [sys.executable, '-B', str(Path(__file__).with_name('story.py')), 'workbench-serve', '--book', root]
    if entry.get('book_id'):
        command += ['--expected-book-id', entry['book_id']]
    for neighbour in library.values():
        if neighbour['root'] != root:
            command += ['--library-book', neighbour['root']]
    if entry.get('book_id'):
        _library_entry(root, entry)
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(30):
        time.sleep(0.1)
        state = _service_request(root)
        if state.get('running'):
            return service_result(state)
        if process.poll() is not None:
            break
    api.fail('workbench_start_pending', '启动尚未确认，请稍后再次打开或查看该作品服务状态。')


def _book_display_script():
    """Share readable work types and directory disambiguation across both pages."""
    return r"""function bookName(book){return typeof book.title==='string'&&book.title.trim()?book.title:'未命名作品';}
function bookKindLabel(book){if(book.kind_verified===false||book.available===false)return '类型未核对';const labels={long:'长篇',short:'短篇',analysis:'作品分析'};return Object.hasOwn(labels,book.kind)?labels[book.kind]:'类型未确认';}
function bookDisplayRows(books){
 const path=book=>typeof book.root==='string'?book.root.replace(/\\/g,'/').replace(/\/+$/,'')||'/':'';
 const parts=book=>path(book).split('/').filter(Boolean),suffix=(book,depth)=>parts(book).slice(-depth).join('/');
 return books.map(book=>{const title=bookName(book),same=books.filter(other=>bookName(other)===title);let directory='';
  if(same.length>1){let depth=1;const maximum=parts(book).length;while(depth<maximum&&same.some(other=>path(other)!==path(book)&&suffix(other,depth)===suffix(book,depth)))depth++;
   directory=path(book)?(depth>=maximum?path(book):'…/'+suffix(book,depth)):'目录未提供';}
  return {...book,displayTitle:title,kindLabel:bookKindLabel(book),label:title+' · '+bookKindLabel(book)+(directory?' · '+directory:'')};
 });
}
function classificationNode(book){
 const box=document.createElement('div');box.className='classification';const label=document.createElement('p');label.className='genre-label';label.textContent=book.kind==='analysis'?'分析对象题材':'题材';box.append(label);
 const classification=book.classification,status=classification?.status||'missing',tags=[...new Set((Array.isArray(classification?.tags)?classification.tags:[]).filter(tag=>typeof tag==='string').map(tag=>tag.trim()).filter(Boolean))];const row=document.createElement('div');row.className='genre-tags';
 if(status==='recorded'&&tags.length){for(const text of tags){const tag=document.createElement('span');tag.className='genre-tag';tag.textContent=text;row.append(tag);}}
 else{const tag=document.createElement('span');tag.className='genre-tag pending';tag.textContent=status==='missing'||status==='recorded'?'题材待分类':'题材待核对';row.append(tag);}box.append(row);
 const sources=(Array.isArray(classification?.sources)?classification.sources:[]).filter(source=>source&&typeof source==='object'),note=typeof classification?.note==='string'?classification.note:'';
 if(sources.length||note){const details=document.createElement('details');details.className='classification-sources';const summary=document.createElement('summary');summary.textContent='分类依据';details.append(summary);for(const source of sources){const p=document.createElement('p');p.textContent=(typeof source.path==='string'?source.path:'来源未标注')+(Number.isInteger(source.line)&&source.line>0?' · 第'+source.line+'行':'')+'\n'+(typeof source.field==='string'?source.field:'字段未标注')+'：'+(typeof source.value==='string'?source.value:'原值未提供');details.append(p);}if(note){const p=document.createElement('p');p.textContent=note;details.append(p);}box.append(details);}
 return box;
}
"""


def _editor_tools_script():
    return r'''
function editorDialog(title){const dialog=document.createElement('dialog');dialog.className='editor-dialog';const heading=document.createElement('h2');heading.textContent=title;const content=document.createElement('div'),close=document.createElement('button');close.textContent='返回稿件';close.onclick=()=>dialog.close();dialog.append(heading,content,close);dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);dialog.showModal();return {dialog,content};}
$('recoveries-open').onclick=async()=>{const {dialog,content}=editorDialog('恢复中心');const help=document.createElement('p');help.textContent='打开恢复副本核对后，保存为新候选。自动恢复不会替换正式稿。';content.append(help);try{const catalog=await call('catalog',{offset:0,query:''});const rows=catalog.files.filter(row=>row.category==='自动恢复');if(!rows.length){help.textContent='没有可列出的磁盘恢复记录。当前页面待保存文字仍请保存候选。';return;}for(const row of rows){const button=document.createElement('button');button.textContent=displayTitle(row)+' · '+(row.modified?new Date(row.modified).toLocaleString():'待核对')+'\n'+row.path;button.onclick=()=>{dialog.close();openDoc(row.id);};content.append(button);}}catch(error){help.textContent=error.message;}};
$('writing-task').onclick=async()=>{if([...docs.values()].some(d=>dirty(d)||d.saving||d.needs_recovery)){note('请先保存全部候选，再生成基于正式断点和已采用细纲的下一章任务。');return;}try{const result=await call('writing-task');leaveComparison();$('assistant-task-title').textContent='继续第'+result.chapter+'章';$('review-task-box').hidden=false;$('review-task-status').textContent=result.status+'；请核对简报再执行。';$('review-findings').textContent='来源：现有正式断点、已绑定细纲和写前简报。任务生成后尚未开始写作。';$('review-prompt').value=result.prompt;$('review-task-box').scrollIntoView({block:'start'});}catch(error){note('下一章任务尚未生成：'+error.message+'；请先核对章计划、细纲采用与绑定。');}};
$('compare-choose').onclick=()=>{const rows=[...new Map([...(currentCatalog?.chapters||[]),...(currentCatalog?.files||[]),...[...docs.values()]].filter(row=>row.id&&!row.image).map(row=>[row.id,row])).values()];if(rows.length<2){note('至少需要两份可读取的文字稿件或材料。');return;}const {dialog,content}=editorDialog('选择两稿对照');const selects=[];for(const labelText of ['左侧稿件','右侧稿件']){const label=document.createElement('label');label.textContent=labelText;const select=document.createElement('select');for(const row of rows){const option=document.createElement('option');option.value=row.id;option.textContent=displayTitle(row)+' · '+versionLabel(row)+(dirty(docs.get(row.id))?' · 含未保存编辑':'');option.title=row.path||row.id;select.append(option);}label.append(select);content.append(label);selects.push(select);}selects[0].value=rows.find(row=>row.kind==='formal'&&chapterNumber(row)===chapterNumber(docs.get(active)||{}))?.id||rows[0].id;selects[1].value=active||rows[1].id;const run=document.createElement('button');run.textContent='对照两稿';content.append(run);const error=document.createElement('p');error.setAttribute('role','status');content.append(error);run.onclick=async()=>{if(selects[0].value===selects[1].value){error.textContent='请选择两份不同稿件。';return;}const id=active,seq=++compareSequence;run.disabled=true;try{const documents=[];for(const select of selects){const cached=docs.get(select.value);documents.push(cached||await call('open',{id:select.value}));}if(active!==id||seq!==compareSequence){error.textContent='当前文件已切换，请重新选择对照。';return;}const texts=documents.map(d=>(d.prefix||'')+(d.value??d.text??''));const d=docs.get(active);if(d&&!d.comparePosition)d.comparePosition={main:document.querySelector('main').scrollTop,editor:$('text').scrollTop,start:$('text').selectionStart,end:$('text').selectionEnd};$('before-label').textContent=displayTitle(documents[0]);$('after-label').textContent=displayTitle(documents[1]);$('compare-label').textContent='两稿文字对照 · 不改变版本或采用关系';drawDiff(...texts);$('review-task-box').hidden=true;$('document-body').hidden=true;$('difference').hidden=false;document.body.classList.add('comparing');$('diff-prev').disabled=true;$('diff-next').disabled=!diffTargets.length;$('compare-back').textContent='返回当前稿件';document.querySelector('main').scrollTop=0;dialog.close();}catch(failure){error.textContent=failure.message;}finally{run.disabled=false;}};};
'''


def _editor_page(packet, reading, token):
    # The editor loads one document at a time; author files are never executable HTML.
    script = r"""const TOKEN=__TOKEN__, LIMIT=__LIMIT__;
const $=id=>document.getElementById(id), docs=new Map();let active=null,offset=0,loading=false,reloadPending=false,openSequence=0,reloading=false,queryTimer,pendingFocus=false;
const note=text=>{$('message').textContent=text;$('message').hidden=!text||text.startsWith('已打开：');};
async function call(action,data={}){const response=await fetch(location.pathname+'api/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Story-Token':TOKEN},body:JSON.stringify(data)});const value=await response.json();if(!response.ok)throw Error((value.message||'请求失败')+(value.details?.path?' 文件：'+value.details.path:'')+(value.details?.incomplete_path?' 暂存：'+value.details.incomplete_path:''));return value;}
let currentCatalog=null,viewMode='chapters',metricTimer,metricSequence=0,searchSequence=0,fullSearchOffset=0;
const readableMethod={visible_nonspace_v1:'非空白可见字符（旧口径）',visible_nonspace_v2:'独立可见字符（含标点）',letters_numbers_v1:'汉字、字母与数字',han_v1:'汉字'};
__BOOK_DISPLAY__
let bookChoices=[],currentBookInfo={title:$('book-title').textContent,kind:$('book-kind').dataset.kind,root:$('current-book-root').textContent,kind_verified:true};
function renderBookChoices(){const selected=$('book-select').value;const rows=bookDisplayRows(bookChoices.map(book=>book.root===currentBookInfo.root&&!Object.hasOwn(book,'kind')?{...book,kind:currentBookInfo.kind,kind_verified:currentBookInfo.kind_verified}:book));$('book-select').replaceChildren();const selection=rows.some(book=>book.key===selected)?selected:rows.find(book=>book.root===currentBookInfo.root)?.key;for(const book of rows){const option=document.createElement('option');option.value=book.key;option.textContent=book.label;option.title=book.root||'目录未提供';option.selected=book.key===selection;$('book-select').append(option);}}
function showBookIdentity(book,root){currentBookInfo={...currentBookInfo,...book,root:root||currentBookInfo.root};const title=bookName(currentBookInfo);$('book-title').textContent=title;$('book-title').title=title;$('book-info-title').textContent=title;$('book-kind').textContent=bookKindLabel(currentBookInfo);$('current-book-root').textContent=currentBookInfo.root||'目录未提供';document.title=title+' · 写作工作台';bookChoices=bookChoices.map(entry=>entry.root===currentBookInfo.root?{...entry,title,kind:currentBookInfo.kind,kind_verified:currentBookInfo.kind_verified}:entry);renderBookChoices();$('book-classification').replaceChildren(classificationNode(currentBookInfo));$('book-classification').hidden=false;}
function chapterNumber(d){return d.context?.chapter||d.chapter||null;}
function simpleCount(text){return [...text].filter(c=>!/[\s\p{Cc}\p{Cf}]/u.test(c)).length;}
function displayTitle(d){
 const title=d.title||'未命名材料';
 if(!(d.path||'').startsWith('.story/drafts/workbench/'))return title;
 return title.replace(/_候选_[0-9a-f]{12}(?:_\d+)?$/i,'').replace(/_[0-9a-f]{8}-[0-9a-f-]{27,}$/i,'');
}
function versionLabel(d){return d.category==='自动恢复'||(d.path||'').includes('/自动恢复/')?'恢复副本':d.kind==='candidate'||d.category==='候选与草稿'?'候选':d.kind==='formal'||d.category==='正式正文'?'正式稿':d.kind==='plan'||d.category==='计划'?'章计划':d.external?'只读材料':'材料';}
function documentIdentity(d){
 const changed=!!(d.editable&&d.value!==undefined&&d.value!==d.text),path=(d.path||'').replace(/\\/g,'/');
 if(d.needs_recovery||(d.id||'').startsWith('pending:'))return {label:'待核对恢复记录',kind:'pending',note:'保存尚未确认完成；核对文字后可恢复为新候选。',changed};
 if(d.metadata_error)return {label:'来源待核对 · 只读',kind:'uncertain',note:'来源记录无法核对；正文保留，采用关系待核对。',changed};
 if(d.external)return {label:'关联材料 · 只读',kind:'material',note:'来自关联材料目录，不计作本书正式正文。',changed};
 if(d.kind==='formal'||/^formal:[1-9]\d*$/.test(d.id||''))return {label:changed?'来源：正式正文':'正式正文',kind:'formal',note:changed?'当前文字尚未保存；保存会生成候选稿。':'已登记的正式正文。',changed};
 if(d.kind==='plan'||/^plan:[1-9]\d*$/.test(d.id||''))return {label:'章计划',kind:'plan',note:'已保存的章计划，尚非正文。',changed};
 if(d.category==='自动恢复'||path.includes('/自动恢复/'))return {label:d.content_kind==='material'?'材料恢复稿':'自动恢复稿',kind:'recovery',note:'供找回文字；正式采用前仍须核对和审查。',changed};
 if(d.kind==='candidate'||d.category==='候选与草稿')return {label:d.content_kind==='material'?'材料候选稿':'候选稿',kind:'candidate',note:'候选文件；采用关系须另核对。',changed};
 return {label:d.image?'图片材料':'创作材料',kind:'material',note:'材料文件，不据文件名或目录认定为正式正文。',changed};
}
function documentLocation(d){return (d.path||'').replace(/\\/g,'/').replace(/^\.story\/drafts\/workbench\//,'候选保存/').replace(/^\.story\/drafts\//,'草稿/').replace(/^\.story\/analysis\//,'作品分析/');}
function filePurpose(d){
 if(d.kind==='formal'||d.category==='正式正文')return '正式正文';
 if(d.kind==='plan'||d.category==='计划')return '章计划';
 const path=(d.path||'').replace(/\\/g,'/');
 if(/(?:^|\/)\d+_大纲细纲\//.test(path))return '细纲材料';
 if(/(?:^|\/)\d+_正文\//.test(path))return '正文目录文件';
 return versionLabel(d)==='候选'?'候选稿':versionLabel(d)==='恢复副本'?'恢复副本':d.external?'关联材料':'创作材料';
}
function downloadName(d){return displayTitle(d)+(d.editable&&d.value!==undefined&&d.value!==d.text?'-未保存候选':versionLabel(d)==='候选'?'-候选':versionLabel(d)==='恢复副本'?'-恢复副本':'')+'.txt';}
function chapterChoices(c,d){
 const n=chapterNumber(d);if(!n)return [];
 const group=c?.workspace?.groups.find(g=>g.chapter===n),rows=[...(group?.items||[])];
 for(const r of c?.related_files||c?.files||[])if(!r.external&&Number(r.chapter||r.path?.split('/').pop().match(/^第(\d+)章(?:\s|_|\.)/)?.[1])===n)rows.push(r);
 rows.push(d);const unique=[...new Map(rows.map(r=>[r.id,r])).values()];
 return unique.map(r=>{const identity=documentIdentity(r),purpose=identity.kind==='material'?filePurpose(r):identity.label;return {...r,label:purpose+' · '+displayTitle({...r,title:r.title==='正式正文'?(group?.title||r.path?.split('/').pop()?.replace(/\.(md|txt)$/i,'')||r.title):r.title})+(r.modified?' · '+new Date(r.modified).toLocaleString():''),manuscript:r.content_kind!=='material'&&['formal','candidate','recovery','pending'].includes(identity.kind)};});
}
function updateChapterPicker(){
 const d=docs.get(active),box=$('chapter-picker'),select=$('chapter-version');if(!d){box.hidden=true;return;}
 const rows=chapterChoices(currentCatalog,d);box.hidden=!rows.length;select.replaceChildren();
 for(const [label,match]of [['稿件',true],['计划与材料',false]]){const group=document.createElement('optgroup');group.label=label;
  for(const r of rows.filter(r=>r.manuscript===match)){const o=document.createElement('option');o.value=r.id;o.textContent=r.label+(dirty(docs.get(r.id))?' · 未保存':'');if(rows.filter(x=>x.label===r.label).length>1)o.textContent+=' · '+r.path;o.title=r.path;o.selected=r.id===active;group.append(o);}if(group.children.length)select.append(group);
 }
 $('chapter-picker-help').textContent='同章号文件供切换查看；候选和材料不代表已采用。';
}
function primaryChapterDocument(group){return group.items.find(r=>r.id==='formal:'+group.chapter)||group.items.find(r=>r.id==='plan:'+group.chapter)||group.items[0];}
function locateActive(){
 const row=[...$('list').querySelectorAll('[data-doc]')].find(e=>e.dataset.doc===active||Number(e.dataset.chapter)===chapterNumber(docs.get(active)||{}));
 if(!row){if(active)$('directory-warning').textContent+=' 当前文件不在筛选结果中，可清空搜索后定位。';return;}
 for(let p=row.parentElement;p&&p!==$('list');p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;
 row.scrollIntoView({block:'nearest'});
}
// Keep each document's edit and reading positions separate; search is a temporary view.
let searchPreview=false,searchMarks=[],searchIndex=-1;
function rememberView(d=docs.get(active)){
 if(!d||searchPreview)return;
 const mode=$('text').hidden?'reading':'editing';d.positions=d.positions||{};
 d.positions[mode]={main:document.querySelector('main').scrollTop,editor:$('text').scrollTop,start:$('text').selectionStart,end:$('text').selectionEnd,direction:$('text').selectionDirection};
}
function restoreView(d){
 const p=d.positions?.[d.editing?'editing':'reading'];
 if(d.editing){$('text').setSelectionRange(p?.start||0,p?.end||0,p?.direction||'none');$('text').scrollTop=p?.editor||0;}
 document.querySelector('main').scrollTop=p?.main||0;
}
function searchRanges(text,query){
 if(!query)return [];const escaped=query.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),rx=new RegExp(escaped,'giu'),ranges=[];let m;
 while((m=rx.exec(text)))ranges.push({start:m.index,end:m.index+m[0].length});return ranges;
}
function stepSearch(delta){
 searchIndex=Math.max(0,Math.min(searchMarks.length-1,searchIndex+delta));
 searchMarks.forEach((el,i)=>el.classList.toggle('current-match',i===searchIndex));
 if(searchMarks[searchIndex])searchMarks[searchIndex].scrollIntoView({block:'center'});
 $('match-position').textContent=searchMarks.length?'第 '+(searchIndex+1)+' / '+searchMarks.length+' 处':'当前文字中未找到；文件可能已修改，请重新搜索。';
 $('match-prev').disabled=searchIndex<=0;$('match-next').disabled=searchIndex>=searchMarks.length-1;
}
function previewSearch(query){
 const d=docs.get(active);rememberView(d);searchPreview=true;searchMarks=[];searchIndex=-1;
 // Recompute against the current buffer, never substitute disk text for unsaved edits.
 const text=(d.prefix||'')+(d.value||''),box=$('match-text');box.replaceChildren();let last=0;
 for(const r of searchRanges(text,query)){box.append(document.createTextNode(text.slice(last,r.start)));const mark=document.createElement('mark');mark.textContent=text.slice(r.start,r.end);box.append(mark);searchMarks.push(mark);last=r.end;}
 box.append(document.createTextNode(text.slice(last)));
 for(const id of ['formatted','prose','text','cover'])$(id).hidden=true;
 $('match-view').hidden=false;$('match-query').textContent='搜索“'+query+'” · 当前文字定位预览（含原始标题和格式）'+(dirty(d)?' · 含未保存编辑':'');
 $('match-back').textContent=d.editing?'返回编辑位置':'返回阅读位置';stepSearch(1);
 if(!searchMarks.length)$('match-view').scrollIntoView({block:'start'});
}
let closePanels=()=>{};
function panelFocusables(panel){return [...panel.querySelectorAll('button,input,select,textarea,a[href],summary,[tabindex]')].filter(el=>{
 if(el.disabled||el.tabIndex<0||!el.getClientRects().length)return false;
 for(let p=el.parentElement;p&&p!==panel;p=p.parentElement)if(p.tagName==='DETAILS'&&!p.open&&!p.querySelector('summary')?.contains(el))return false;
 return true;
});}
function trapPanelFocus(e){
 const id=document.body.classList.contains('nav-open')?'book-nav':document.body.classList.contains('context-open')?'book-context':null;
 if(!id||e.key!=='Tab')return false;
 const panel=$(id),items=panelFocusables(panel),index=items.indexOf(document.activeElement);
 e.preventDefault();const next=index<0?(e.shiftKey?items.length-1:0):(index+(e.shiftKey?-1:1)+items.length)%items.length;(items[next]||panel).focus();return true;
}
let compareSequence=0,diffTargets=[],diffIndex=-1;
function leaveComparison(){
 ++compareSequence;const d=docs.get(active);document.body.classList.remove('comparing');$('document-body').hidden=false;$('difference').hidden=true;
 if(d?.comparePosition){const p=d.comparePosition;document.querySelector('main').scrollTop=p.main;$('text').scrollTop=p.editor;if(d.editing){$('text').focus({preventScroll:true});$('text').setSelectionRange(p.start,p.end);}delete d.comparePosition;}
}
function stepDiff(delta){diffIndex=Math.max(0,Math.min(diffTargets.length-1,diffIndex+delta));if(diffTargets[diffIndex])diffTargets[diffIndex].scrollIntoView({block:'center'});$('diff-position').textContent=diffTargets.length?'标记片段 '+(diffIndex+1)+' / '+diffTargets.length:'两版文字一致';$('diff-prev').disabled=diffIndex<=0;$('diff-next').disabled=diffIndex>=diffTargets.length-1;}
function inlineText(parent,text,path){
 const regex=/!?\[|\*\*([^*\n]+)\*\*|`([^`\n]+)`/g;let last=0,m;
 while((m=regex.exec(text))){parent.append(document.createTextNode(text.slice(last,m.index)));let el;
  if(m[0]==='['||m[0]==='!['){const ref=markdownReference(text,m.index);
   if(!ref||ref.target===null){const end=ref?.end||regex.lastIndex;parent.append(document.createTextNode(text.slice(m.index,end)));last=regex.lastIndex=end;continue;}
   regex.lastIndex=ref.end;
   if(m[0]==='!['){el=markdownImage(ref.label,ref.target,path);}
   else{let url;try{url=new URL(ref.target,'https://story.local/'+path);}catch{}if(url&&/^https?:$/.test(url.protocol)&&!(path===null&&url.hostname==='story.local')){
   el=document.createElement('a');el.textContent=ref.label;
   if(url.hostname==='story.local'){el.href='#';el.onclick=e=>{e.preventDefault();try{openDoc('file:'+decodeURIComponent(url.pathname.slice(1)));}catch{note('链接路径无法识别。');}};}
   else{el.href=url.href;el.target='_blank';el.rel='noreferrer noopener';}
   }else{el=document.createElement('span');el.textContent=ref.label;}}
  }else{el=document.createElement(m[1]?'strong':'code');el.textContent=m[1]||m[2];}
  parent.append(el);last=regex.lastIndex;
 }parent.append(document.createTextNode(text.slice(last)));
}
function markdownReference(text,start){
 const labelStart=start+(text[start]==='!'?2:1);let i=labelStart,depth=1,complex=false;
 for(;i<text.length;i++){
  if(text[i]==='\\'){complex=true;i++;continue;}
  if(text[i]==='['){depth++;complex=true;}
  else if(text[i]===']'&&!--depth)break;
 }
 if(depth||text[i+1]!=='(')return null;
 const label=text.slice(labelStart,i),bodyStart=i+2;let angle=false,quote='',first=true;depth=1;
 for(i=bodyStart;i<text.length;i++){
  const c=text[i];if(c==='\\'){i++;continue;}
  if(angle){if(c==='>')angle=false;continue;}
  if(quote){if(c===quote)quote='';continue;}
  if(first&&/\s/.test(c))continue;
  if(first&&c==='<'){angle=true;first=false;continue;}first=false;
  if((c==='"'||c==="'")&&/\s/.test(text[i-1])){quote=c;continue;}
  if(c==='(')depth++;
  else if(c===')'&&!--depth)break;
 }
 const end=i<text.length?i+1:text.length;
 const result={end,label,target:null};if(depth||angle||quote||complex)return result;
 const body=text.slice(bodyStart,i).trim();let target,tail='';
 const title=/^(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\((?:\\.|[^()\\])*\))$/;
 if(body.startsWith('<')){
  const close=body.indexOf('>');if(close<0)return result;
  target=body.slice(1,close);tail=body.slice(close+1);
  if(tail&&(!/^\s/.test(tail)||!title.test(tail.trim())))return result;
 }else{
  const titled=body.match(/^(.*?)\s+("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\((?:\\.|[^()\\])*\))$/);
  target=titled?titled[1]:body;
  // Keep space-containing filenames, but do not guess malformed or nested titles.
  if(/\s+["']|[<>]/.test(target)||(!titled&&/\s+\(.*\)$/.test(body)))return result;
 }
 if(!target)return result;
 result.target=target.replace(/\\([()[\]<>])/g,'$1');return result;
}
function markdownImage(alt,target,path){
 const holder=document.createElement('span');holder.className='markdown-image';
 const status=document.createElement('span');status.setAttribute('role','status');status.textContent='配图加载中…';holder.append(status);
 const fail=message=>{status.textContent='配图无法预览：'+message;holder.replaceChildren(status);};let relative;
 try{
  if(typeof path!=='string'||!path)throw Error('无法定位本书配图来源。');
  target=target.trim();if(target.startsWith('<')&&target.endsWith('>'))target=target.slice(1,-1);
  if(!target||/^(?:[a-z][a-z0-9+.-]*:|[/\\])/i.test(target))throw Error('仅支持本书相对路径图片。');
  const parts=path.split('/').slice(0,-1);
  for(const encoded of target.split(/[?#]/,1)[0].split('/')){
   let part;try{part=decodeURIComponent(encoded);}catch{throw Error('图片路径无法识别。');}
   if(/[\\/\0]/.test(part))throw Error('图片路径无法识别。');
   if(part==='..'){if(!parts.length)throw Error('图片路径超出本书目录。');parts.pop();}
   else if(part&&part!=='.')parts.push(part);
  }
  if(!parts.length)throw Error('图片路径无法识别。');relative=parts.join('/');
 }catch(e){fail(e.message);return holder;}
 call('open',{id:'file:'+relative}).then(d=>{
  if(typeof d?.image!=='string'||!/^data:image\/(?:png|jpeg|webp);base64,/.test(d.image)){fail('该文件不是支持的图片。');return;}
  const image=document.createElement('img');image.alt=alt||'配图';image.onerror=()=>fail('图片数据加载失败。');
  holder.replaceChildren(image);image.src=d.image;
 }).catch(e=>fail(e.message||'读取图片失败。'));
 return holder;
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
function renderProse(container,text){
 container.replaceChildren();
 // Preserve exact characters and meaningful extra blank lines; only change their visual spacing.
 for(const part of text.split(/(\r?\n[ \t]*\r?\n(?:[ \t]*\r?\n)*)/)){
  if(!part)continue;const span=document.createElement('span');span.textContent=part;
  const gap=/^\r?\n[ \t]*\r?\n/.test(part);span.className=gap?'prose-gap':'prose-paragraph';
  if(gap)span.style.height='calc(var(--reader-size) * '+((part.match(/\n/g)||[]).length-1)*0.8+')';
  container.append(span);
 }
}
function readingView(d){const formatted=d.render_markdown&&!d.editing&&!d.rawPreview&&!d.image;
 $('formatted').hidden=!formatted;$('source-view').hidden=!d.render_markdown;$('source-view').textContent=d.rawPreview?'排版预览':'查看源码';
 if(formatted){$('prose').hidden=true;renderMarkdown($('formatted'),d.value,d.external?null:d.markdown_path===undefined?d.path:d.markdown_path);}else if(d.is_prose&&!d.editing&&!d.image)renderProse($('prose'),d.value||'');
 $('history-note').textContent=d.historical_note||'';
 $('review-task-box').hidden=true;
 showChapterInfo(d);scheduleMetrics();
}
function showChapterInfo(d){$('context-title').textContent=d.image?'封面信息':d.external?'材料来源':d.kind==='plan'?'章计划':d.is_prose?'本章写作':'材料信息';$('adoption-help').hidden=!d.is_prose;const box=$('chapter-info'),expanded=new Set(box.dataset.contextDoc===d.id?[...box.querySelectorAll('details[open]')].map(e=>e.dataset.section):[]);box.dataset.contextDoc=d.id;box.replaceChildren();const ctx=d.context,p=ctx?.plan;
 if(!ctx){box.textContent=d.external?'关联材料只读展示，不推断与本书章节或采用状态的关系。':'这是全书材料。可从左栏按章查看细纲、正文及候选。';return;}
 const add=(title,text,target=box)=>{if(!text)return;const h=document.createElement('h4');h.textContent=title;const v=document.createElement('p');v.textContent=text;target.append(h,v);};
 const checked=d.context_check,stale=checked?.chapter===ctx.chapter&&checked.revision>=ctx.revision&&checked.context_sha256!==ctx.context_sha256;
 if(stale)add('辅助信息已过期',d.kind==='plan'?'已保存章计划或相关正式状态已变化；中栏计划和以下辅助信息仍为载入时版本。请用“重新读取文件”核对。':'本章计划、前章衔接或正式稿记录已变化；以下辅助信息仍为载入时版本。当前文字和未保存编辑已保留，请用“重新读取文件”或“保留恢复稿并重新载入”核对。');
 const fold=(label)=>{const section=document.createElement('details');section.dataset.section=label;section.open=expanded.has(label);const summary=document.createElement('summary');summary.textContent=label;section.append(summary);box.append(section);return section;};
 add('第'+ctx.chapter+'章 · 目标',p?.goal||'尚未保存工具章计划');add('停笔点',p?.stop);
 add('前章摘要',ctx.previous_summary||'没有已提交的前章摘要',fold('前章衔接'));add('完整约束',(p?.constraints||[]).join('\n'),fold('本章约束'));
 const related=(currentCatalog?.related_files||currentCatalog?.files||[]).filter(r=>!r.external&&Number(r.chapter||r.path.split('/').pop().match(/^第(\d+)章(?:\s|_|\.)/)?.[1])===ctx.chapter);
 if(related.length){const relatedBox=fold('相关材料 · '+related.length);add('关联说明','同章号匹配仅用于导航，不代表已经采用。',relatedBox);related.slice(0,30).forEach(r=>relatedBox.append(item(r)));}
 if(ctx.review){const reviewBox=document.createElement('details');const summary=document.createElement('summary');summary.textContent='正式稿审查记录';reviewBox.append(summary);box.append(reviewBox);add('记录范围','以下记录只对应本次载入的已提交正式稿，不能代替对当前候选的审查。',reviewBox);for(const [key,label]of Object.entries({causality:'因果',continuity:'连续性',constraints:'约束',style:'文风'})){add(label,ctx.review.checks?.[key]?.note,reviewBox);}for(const issue of ctx.review.issues||[])add('审查问题',issue.issue,reviewBox);}
 else add('审查状态',ctx.formal_sha256?'暂无可展示的审查记录。':'尚无正式提交的审查记录。');
}
function scheduleMetrics(){clearTimeout(metricTimer);++metricSequence;const d=docs.get(active);if(!d||!d.editable){$('word-count').textContent='暂无字数统计';$('count-method').textContent='当前文件不提供字数统计。';return;}
 $('word-count').textContent='正在核对字数…';$('count-method').textContent='正在读取本文件计数口径…';const seq=metricSequence;metricTimer=setTimeout(async()=>{try{
  const selected=d.editing?$('text').value.slice($('text').selectionStart,$('text').selectionEnd):'';
  const m=await call('metrics',{id:d.id,text:d.value,selection:selected});if(seq!==metricSequence||active!==d.id)return;
  const lengthState=!m.target?'':m.in_range?'章幅计数通过':!m.within_target?'范围外':m.length_issues?.includes('invisible_padding')?'章幅检查未通过：附着标记和空白填充符不计下限':'章幅检查未通过';
  $('word-count').textContent=m.count+' 字符'+(lengthState?' · '+lengthState:'')+(m.selection?' · 已选 '+m.selection:'');
  $('count-method').textContent=(m.is_prose?'正文':'材料')+' '+m.count+' 字符 · '+readableMethod[m.method]+(m.is_prose?(m.include_title?' · 含章名':' · 不含章名'):'')+(m.target?' · 目标 '+m.target.join('～')+' · '+lengthState+'（仅核对章幅，章头等另查）':'')+(m.selection?' · 已选 '+m.selection:'');
 }catch(e){if(seq===metricSequence)$('word-count').textContent='字数核对失败：'+e.message;}},350);
}
function drawCatalog(c,expanded){currentCatalog=c;const w=c.workspace;
 showBookIdentity(w,c.book_root);
 const d=docs.get(active),checked=w.context_snapshot;
 if(d&&checked?.chapter===d.context?.chapter){d.context_check=checked;showChapterInfo(d);}
 $('view-chapters').setAttribute('aria-pressed',String(viewMode==='chapters'));$('view-files').setAttribute('aria-pressed',String(viewMode==='files'));
 $('root-hint').textContent='当前书库：'+(c.book_root||'')+(c.material_roots?.length?' · 只读材料目录：'+c.material_roots.join('；'):'');
 $('newer').textContent='上一页';$('older').textContent='下一页';
 $('book-progress').textContent=`当前进度：正式 ${w.formal_total} 章 · 已规划 ${w.planned_total} 章 · 下一章 第${w.next_chapter}章 · 刷新于 ${new Date(w.captured_at).toLocaleTimeString()}`;
 $('book-progress').title=$('book-progress').textContent;$('book-progress-detail').textContent=$('book-progress').textContent;
 updateChapterPicker();if(viewMode!=='chapters')return;
 offset=w.offset;
 $('list').replaceChildren();
 function group(title,rows,expandedDefault=false,label=title){const d=document.createElement('details');d.dataset.group=title;d.open=$('search').value?true:(expanded.get(title)??expandedDefault);const h=document.createElement('summary');h.textContent=label;d.append(h);rows.forEach(r=>d.append(item(r)));$('list').append(d);}
 if(c.files.some(r=>r.external))group('关联材料（只读）',c.files.filter(r=>r.external),false);
 group('全书材料',c.files.filter(r=>!r.external&&!/^第\d+章(?:\s|_|$)/.test(r.title)&&r.category!=='自动恢复'),false);
 let volume=null;for(const g of w.groups){if(g.volume&&g.volume!==volume){const h=document.createElement('h3');h.className='volume-heading';h.textContent=g.volume;$('list').append(h);}volume=g.volume;
 const selected=primaryChapterDocument(g);if(!selected)continue;const button=document.createElement('button');button.className='document chapter-entry';button.dataset.doc=selected.id;button.dataset.chapter=String(g.chapter);button.title=g.status;const name=document.createElement('span');name.textContent=g.title;const status=document.createElement('small');status.className='document-type';status.textContent=g.status==='已有正式稿'?'正式':g.status==='已规划，未提交'?'规划':g.status;button.append(name,status);button.onclick=()=>openDoc(selected.id);$('list').append(button);
 }
 const recoveries=c.files.filter(r=>r.category==='自动恢复');if(recoveries.length)group('自动恢复',recoveries,true);
 $('range').textContent=w.total?`${w.offset+1}—${Math.min(w.offset+w.limit,w.total)} / ${w.total} 个章节（含规划）`:'尚无章节规划';
 $('older').disabled=!w.has_more;$('newer').disabled=w.offset===0;
 if(active)showChapterInfo(docs.get(active));
}
// Paragraph LCS with a bounded fallback. Preserve every line, including blank lines, on both sides.
function diffLines(before,after){const a=before.split('\n'),b=after.split('\n');if(before===after)return {left:a.map(text=>({text,change:''})),right:b.map(text=>({text,change:''})),coarse:false};if(a.length*b.length>350000){return {left:a.map(t=>({text:t,change:'removed'})),right:b.map(t=>({text:t,change:'added'})),coarse:true};}
 const dp=Array.from({length:a.length+1},()=>new Uint32Array(b.length+1));for(let i=a.length-1;i>=0;i--)for(let j=b.length-1;j>=0;j--)dp[i][j]=a[i]===b[j]?1+dp[i+1][j+1]:Math.max(dp[i+1][j],dp[i][j+1]);
 const left=[],right=[];let i=0,j=0;while(i<a.length||j<b.length){if(i<a.length&&j<b.length&&a[i]===b[j]){left.push({text:a[i++],change:''});right.push({text:b[j++],change:''});}else if(i<a.length&&(j===b.length||dp[i+1][j]>=dp[i][j+1]))left.push({text:a[i++],change:'removed'});else right.push({text:b[j++],change:'added'});}return {left,right,coarse:false};}
function drawDiff(before,after){diffTargets=[];diffIndex=-1;const diff=diffLines(before,after);for(const [id,rows]of [['before',diff.left],['after',diff.right]]){const box=$(id);box.replaceChildren();let lastChange='';for(const r of rows){const span=document.createElement('span');span.className='diff-line '+r.change;span.textContent=r.text;box.append(span);if(r.change&&r.change!==lastChange)diffTargets.push(span);lastChange=r.change;}}
 $('compare-label').textContent+=' · 红色为删除，绿色为新增'+(diff.coarse?'（长文本使用整块高亮）':'');}
async function fullSearch(){const seq=++searchSequence,q=$('full-query').value.trim();if(!q){fullSearchOffset=0;$('search-results').replaceChildren();$('search-status').textContent='';$('search-prev').disabled=true;$('search-next').disabled=true;return;}$('search-status').textContent='搜索中…';try{const r=await call('search',{query:q,offset:fullSearchOffset});if(seq!==searchSequence)return;$('search-results').replaceChildren();for(const row of r.results){const b=item(row);b.onclick=()=>openDoc(row.id,q);const small=document.createElement('small');small.textContent=row.excerpt;b.append(small);$('search-results').append(b);}$('search-status').textContent=`找到 ${r.total} 份文件 · 已检查 ${r.checked} 份`+(r.complete?' · 本轮覆盖完整':' · 存在未覆盖文件')+(r.warnings.length?'\n'+r.warnings.join('\n'):'');$('search-prev').disabled=!fullSearchOffset;$('search-next').disabled=!r.has_more;}catch(e){if(seq===searchSequence)$('search-status').textContent=e.message;}}
function applyReading(){const size=$('font-size').value,line=$('line-height').value,font=$('font-family').value;document.documentElement.style.setProperty('--reader-size',size+'px');document.documentElement.style.setProperty('--reader-line',line);document.documentElement.style.setProperty('--reader-font',font==='sans'?'system-ui':"'Songti SC','SimSun',serif");document.body.classList.toggle('focus-reading',$('focus-reading').checked);try{localStorage.setItem('story-reading',JSON.stringify({size,line,font,focus:$('focus-reading').checked}));}catch{}}
async function loadBooks(){try{const r=await call('books');bookChoices=r.books;if(r.current)currentBookInfo.root=r.current;renderBookChoices();$('book-open').disabled=r.books.length<2;$('book-hint').textContent=r.books.length<2?'目前只登记本书；启动服务时可添加其他作品。':'作品会在新标签页打开，当前编辑保留。';}catch(e){$('book-hint').textContent=e.message;}}
function fitColumns(left,right,width){
 left=Number.isFinite(left)?Math.max(180,Math.min(360,left)):236;right=Number.isFinite(right)?Math.max(200,Math.min(360,right)):248;
 if(width>820){let excess=Math.max(0,left+right-(width-360)),cut=Math.min(left-180,excess);left-=cut;excess-=cut;right-=Math.min(right-200,excess);}
 return {left:Math.round(left),right:Math.round(right)};
}
function setupColumns(){
 let sizes={left:236,right:248};try{const p=JSON.parse(localStorage.getItem('story-columns')||'{}');sizes=fitColumns(p.left,p.right,innerWidth);document.body.classList.toggle('nav-closed',p.navClosed===true);document.body.classList.toggle('context-closed',p.contextClosed===true);}catch{}
 const apply=()=>{sizes=fitColumns(sizes.left,sizes.right,innerWidth);document.documentElement.style.setProperty('--nav-width',sizes.left+'px');document.documentElement.style.setProperty('--context-width',sizes.right+'px');for(const side of ['left','right'])$('resize-'+side).setAttribute('aria-valuenow',String(sizes[side]));};
 const persist=()=>{try{localStorage.setItem('story-columns',JSON.stringify({...sizes,navClosed:document.body.classList.contains('nav-closed'),contextClosed:document.body.classList.contains('context-closed')}));}catch{}};
 for(const side of ['left','right']){const handle=$('resize-'+side);let drag=null;
  handle.onpointerdown=e=>{if(e.button!==0||innerWidth<=820)return;drag={x:e.clientX,size:sizes[side]};handle.setPointerCapture(e.pointerId);document.body.classList.add('resizing');e.preventDefault();};
  handle.onpointermove=e=>{if(!drag)return;sizes[side]=drag.size+(side==='left'?1:-1)*(e.clientX-drag.x);apply();};
  const finish=()=>{if(!drag)return;drag=null;document.body.classList.remove('resizing');persist();};handle.onpointerup=finish;handle.onpointercancel=finish;handle.onlostpointercapture=finish;
  handle.onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home'].includes(e.key))return;e.preventDefault();sizes[side]=e.key==='Home'?(side==='left'?236:248):sizes[side]+(e.key==='ArrowRight'?1:-1)*(side==='left'?1:-1)*16;apply();persist();};
 }
 $('layout-reset').onclick=()=>{sizes={left:236,right:248};document.body.classList.remove('nav-closed','context-closed','focus-reading');$('focus-reading').checked=false;apply();persist();$('nav-toggle').textContent='收起目录';$('context-toggle').textContent='收起辅助栏';$('nav-toggle').setAttribute('aria-expanded','true');$('context-toggle').setAttribute('aria-expanded','true');};
 window.addEventListener('resize',apply);apply();return persist;
}
function setupWorkspace(){
 const persistColumns=setupColumns();$('chapter-version').onchange=()=>openDoc($('chapter-version').value);

 $('view-chapters').onclick=()=>{viewMode='chapters';offset=0;catalog(true,true);};$('view-files').onclick=()=>{viewMode='files';offset=0;catalog(true,true);};
 $('locate').onclick=()=>{$('search').value='';catalog(true,true);};
 $('compare-back').onclick=leaveComparison;$('diff-prev').onclick=()=>stepDiff(-1);$('diff-next').onclick=()=>stepDiff(1);
 $('refresh-compact').onclick=()=>catalog();
 const narrow=matchMedia('(max-width:820px)');let panelOpener=null;
 const syncPanels=()=>{const opened=narrow.matches?document.body.classList.contains('context-open'):!document.body.classList.contains('context-closed')&&!document.body.classList.contains('focus-reading');$('context-toggle').textContent=opened?'收起辅助栏':'辅助信息';$('context-toggle').setAttribute('aria-expanded',String(opened));const navOpened=narrow.matches?document.body.classList.contains('nav-open'):!document.body.classList.contains('nav-closed');$('nav-toggle').textContent=navOpened?'收起目录':'作品目录';$('nav-toggle').setAttribute('aria-expanded',String(navOpened));};
 closePanels=(restore=true)=>{document.body.classList.remove('nav-open','context-open');document.querySelector('main').inert=false;document.querySelector('header').inert=false;$('book-nav').inert=false;$('book-context').inert=false;syncPanels();const opener=panelOpener;panelOpener=null;if(restore&&opener)opener.focus({preventScroll:true});};
 const openPanel=(kind,opener)=>{closePanels(false);panelOpener=opener;document.body.classList.add(kind+'-open');const panel=$(kind==='nav'?'book-nav':'book-context');document.querySelector('main').inert=true;document.querySelector('header').inert=true;$(kind==='nav'?'book-context':'book-nav').inert=true;syncPanels();(panelFocusables(panel)[0]||panel).focus({preventScroll:true});};
 $('context-toggle').onclick=()=>{if(narrow.matches)openPanel('context',$('context-toggle'));else{const opened=!document.body.classList.contains('context-closed')&&!document.body.classList.contains('focus-reading');document.body.classList.toggle('context-closed',opened);$('focus-reading').checked=false;applyReading();syncPanels();persistColumns();}};
 $('nav-toggle').onclick=()=>{if(narrow.matches)openPanel('nav',$('nav-toggle'));else{document.body.classList.toggle('nav-closed');syncPanels();persistColumns();}};
 const resetColumns=$('layout-reset').onclick;$('layout-reset').onclick=()=>{resetColumns();applyReading();syncPanels();};
 $('nav-close').onclick=()=>closePanels();$('context-close').onclick=()=>closePanels();$('panel-dismiss').onclick=()=>closePanels();narrow.addEventListener('change',()=>closePanels());syncPanels();
 $('match-back').onclick=()=>{const d=docs.get(active);show(d);if(d.editing)$('text').focus({preventScroll:true});};$('match-prev').onclick=()=>stepSearch(-1);$('match-next').onclick=()=>stepSearch(1);
 document.addEventListener('keydown',e=>{if(e.key==='Escape'){closePanels();for(const id of ['book-info','maintenance','more-actions','reading-options'])$(id).open=false;}if(trapPanelFocus(e))return;if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='s'&&!e.isComposing&&!document.querySelector('main').inert){e.preventDefault();if(!$('save').disabled)$('save').click();}});
 document.addEventListener('pointerdown',e=>{for(const id of ['book-info','maintenance','more-actions','reading-options'])if(!$(id).contains(e.target))$(id).open=false;});
 for(const id of ['book-info','maintenance','more-actions','reading-options'])$(id).addEventListener('click',e=>{if(e.target.closest('button'))$(id).open=false;});
 $('source-view').onclick=()=>{const d=docs.get(active);d.rawPreview=!d.rawPreview;show(d);};
 $('text').addEventListener('select',scheduleMetrics);$('text').addEventListener('keyup',scheduleMetrics);
 $('review-task').onclick=async()=>{const d=docs.get(active);if(!d?.editable)return;if(dirty(d)||d.needs_recovery){note('请先保存当前候选稿，再生成绑定该文件的审稿任务。');return;}try{const r=await call('review-task',{id:d.id,sha256:d.sha256});if(active!==d.id)return;$('assistant-task-title').textContent='交给助手审稿';leaveComparison();$('review-task-box').hidden=false;$('review-task-box').scrollIntoView({block:'start'});$('review-prompt').value=r.prompt;$('review-task-status').textContent=r.status+'；'+r.check_note+(r.lint?(r.lint.ok?' 未发现工具阻断项。':' 发现 '+r.lint.errors.length+' 项工具问题。'):'');$('review-findings').textContent=r.lint?[...r.lint.errors,...r.lint.warnings].map(x=>x.code+(x.expected?'：目标 '+(Array.isArray(x.expected)?x.expected.join('～'):x.expected):'')+(x.actual!==undefined?'，实际 '+x.actual:'')+(x.message?'：'+x.message:'')).join('\n'):'';}catch(e){note(e.message);}};
 $('copy-review').onclick=async()=>{try{await navigator.clipboard.writeText($('review-prompt').value);$('review-task-status').textContent='已复制，请粘贴给助手执行；任务尚未自动执行，稿件尚未正式采用。';}catch{$('review-prompt').select();note('请复制已选中的审稿任务。');}};
 $('full-go').onclick=()=>{fullSearchOffset=0;fullSearch();};$('full-query').onkeydown=e=>{if(e.key==='Enter'){fullSearchOffset=0;fullSearch();}};$('search-prev').onclick=()=>{fullSearchOffset=Math.max(0,fullSearchOffset-30);fullSearch();};$('search-next').onclick=()=>{fullSearchOffset+=30;fullSearch();};
 try{const p=JSON.parse(localStorage.getItem('story-reading')||'{}');if(['16','19','22','25'].includes(p.size))$('font-size').value=p.size;if(['1.6','1.8','1.9','2.2'].includes(p.line))$('line-height').value=p.line;if(['serif','sans'].includes(p.font))$('font-family').value=p.font;$('focus-reading').checked=p.focus===true;}catch{}
 for(const id of ['font-size','line-height','font-family','focus-reading'])$(id).onchange=()=>{applyReading();syncPanels();};applyReading();syncPanels();
 $('book-open').onclick=async()=>{const key=$('book-select').value,selected=bookChoices.find(book=>book.key===key),title=selected?bookName(selected):'所选作品';try{const r=await call('open-book',{key});const a=document.createElement('a');a.href=r.url;a.target='_blank';a.rel='noopener';a.textContent='打开所选作品';$('book-hint').replaceChildren(a);a.click();if(r.outdated)note('所选作品服务仍使用较早代码；原编辑保留，可另行升级。');}catch(e){$('book-hint').textContent='打开失败（'+title+'）：'+e.message;}};
 loadBooks();
}

function dirty(d){return d&&d.editable&&d.value!==d.text;}
function badge(){pendingEdits();if(!active)return;const d=docs.get(active),identity=documentIdentity(d);$('document-identity').hidden=false;$('document-role').textContent=identity.label;$('document-role').dataset.kind=identity.kind;$('document-role').title=identity.note;$('document-unsaved').hidden=!identity.changed;$('state').textContent=d.saving?'正在保存候选…':dirty(d)?(d.recovered===d.value?'待保存 · 恢复副本已写入':'待保存 · 恢复副本尚未写入'):d.status;$('save').disabled=(!dirty(d)&&!d.needs_recovery)||d.saving;$('save').textContent=d.needs_recovery?'恢复为新候选':'保存候选稿';$('edit').disabled=!d.editable;$('review-task').disabled=!d.editable;$('compare').disabled=!d.comparison&&d.kind!=='formal';$('download').disabled=!d.editable&&!d.text;$('edit').textContent=d.editing?'返回阅读':'编辑';$('edit').setAttribute('aria-pressed',String(!!d.editing));document.querySelectorAll('[data-doc]').forEach(e=>{const item=docs.get(e.dataset.doc);e.classList.toggle('selected',e.dataset.chapter?Number(e.dataset.chapter)===chapterNumber(d):e.dataset.doc===active);e.classList.toggle('dirty',!!dirty(item));});}
function show(d){leaveComparison();rememberView();searchPreview=false;$('match-view').hidden=true;active=d.id;$('title').textContent=displayTitle(d);$('path').textContent=d.path;$('text').value=d.value;$('text').hidden=!d.editable||!d.editing;$('prose').hidden=!!d.image||!!d.editing;$('prose').textContent=d.value||'';$('cover').hidden=!d.image;if(d.image)$('cover').src=d.image;$('difference').hidden=true;$('detail').textContent=[d.status,d.metadata_error?'来源记录文件：'+d.metadata_error.path:'',d.modified?'文件修改时间：'+new Date(d.modified).toLocaleString():'',d.source?'来源：'+d.source:'',d.summary?'正式稿摘要：'+d.summary:'',d.comparison?'对照对象：'+d.comparison.title:''].filter(Boolean).join('\n\n');readingView(d);badge();updateChapterPicker();restoreView(d);}
async function openDoc(id,query=''){const sequence=++openSequence;try{if(!docs.has(id)){const d=await call('open',{id});if(sequence!==openSequence)return;d.value=d.text||'';d.editing=false;d.recoveryKey=crypto.randomUUID();if(!docs.has(id))docs.set(id,d);}if(sequence!==openSequence)return;show(docs.get(id));closePanels();location.hash=encodeURIComponent(id);note('已打开：'+displayTitle(docs.get(id))+' · '+versionLabel(docs.get(id)));if(query)previewSearch(query);await catalog(true);}catch(e){if(sequence===openSequence)note('打开失败（'+id+'）：'+e.message);}}
function pendingEdits(){const pending=[...docs.values()].filter(dirty);$('pending-box').hidden=!pending.length;$('pending-count').textContent='待保存文件 · '+pending.length;$('pending-list').replaceChildren();for(const d of pending){const b=document.createElement('button');b.className='document';b.title=d.path||d.id;b.textContent=displayTitle(d)+' · '+(d.recovered===d.value?'已写入恢复稿':'尚未写入恢复稿');if(d.path&&pending.filter(x=>displayTitle(x)===displayTitle(d)).length>1){const source=document.createElement('small');source.className='document-location';source.textContent=documentLocation(d);b.append(source);}b.onclick=()=>openDoc(d.id);$('pending-list').append(b);}}
function item(row){const b=document.createElement('button');b.className='document';b.dataset.doc=row.id;b.title=row.path+(row.modified?'\n修改时间：'+new Date(row.modified).toLocaleString():'');const title=document.createElement('span');title.className='document-name';title.textContent=row.category==='正式正文'||row.category==='计划'?row.title:displayTitle(row);const type=document.createElement('small');type.className='document-type';const identity=documentIdentity(row),purpose=identity.kind==='material'?filePurpose(row):identity.label;type.textContent=purpose;b.append(title);if(title.textContent!==purpose&&title.textContent!=='已保存章计划')b.append(type);if(row.path&&['candidate','recovery','pending','uncertain'].includes(identity.kind)){const source=document.createElement('small');source.className='document-location';source.textContent=documentLocation(row);b.append(source);}b.onclick=()=>openDoc(row.id);return b;}
async function catalog(focusCurrent=false,navigation=false){
 if(navigation||focusCurrent)clearTimeout(queryTimer);
 // Explicit navigation replaces an earlier request to locate the active chapter.
 // Background refreshes retain that request until its response is displayed.
 if(navigation)pendingFocus=false;
 if(loading){reloadPending=true;pendingFocus=pendingFocus||focusCurrent;return;}
 loading=true;pendingFocus=focusCurrent;
 const query=$('search').value,mode=viewMode;
 try{
  const c=await call('catalog',{offset,query,focus:focusCurrent?chapterNumber(docs.get(active)||{}):null,context_chapter:chapterNumber(docs.get(active)||{})||null});
  // A queued operation owns the current offset. Never replace it with an old response.
  if(reloadPending||query!==$('search').value||mode!==viewMode)return;
  $('directory-warning').textContent=(c.warnings||[]).join('\n');
  const expandedGroups=new Map([...$('list').querySelectorAll('details')].map(g=>[g.dataset.group,g.open]));
  const listScroll=$('list').parentElement.scrollTop;
  if(mode!=='chapters'){
   offset=c.offset;$('list').replaceChildren();
   const section=(title,rows,expanded=true)=>{
    const group=document.createElement('details');group.dataset.group=title;group.open=query?true:(expandedGroups.get(title)??expanded);
    const summary=document.createElement('summary');summary.textContent=title+' · '+rows.length;group.append(summary);rows.forEach(r=>group.append(item(r)));$('list').append(group);
   };
   section('正式正文',c.chapters);
   for(const category of ['候选与草稿','自动恢复','创作材料','拆书分析','关联材料（只读）']){
    const rows=c.files.filter(r=>r.category===category);
    if(rows.length){
     const folders=new Map();
     for(const row of rows){const folder=row.path.includes('/')?row.path.slice(0,row.path.lastIndexOf('/')):'书根';if(!folders.has(folder))folders.set(folder,[]);folders.get(folder).push(row);}
     for(const [folder,entries] of folders)section(category+' / '+folder,entries,!!query||category==='自动恢复');
    }
   }
   $('range').textContent=c.total?`正式章节 ${offset+1}—${Math.min(offset+LIMIT,c.total)} / ${c.total} · 材料另列`:'没有匹配的正式章节';
   $('older').disabled=!c.has_more;$('newer').disabled=offset===0;
  }
  drawCatalog(c,expandedGroups);$('list').parentElement.scrollTop=listScroll;badge();
  if(focusCurrent&&active)locateActive();
  if(!active){
   let id;try{id=decodeURIComponent(location.hash.slice(1));}catch{}
   if(/^chapter-\d+$/.test(id||''))id='formal:'+id.slice(8);
   id=id||c.chapters[0]?.id||c.files[0]?.id||c.workspace?.groups[0]?.items[0]?.id;
   if(id)await openDoc(id);
  }
 }catch(e){if(!reloadPending&&query===$('search').value&&mode===viewMode)note(e.message);}
 finally{loading=false;if(reloadPending){reloadPending=false;const focus=pendingFocus;pendingFocus=false;catalog(focus);}}
}
async function recover(d,force=false){if(d.recovering){await d.recovering;if(force||(dirty(d)&&d.value!==d.recovered))return recover(d,force);return;}if(!dirty(d)||(!force&&d.value===d.recovered))return;if(force)d.recovered=undefined;const value=d.value;d.recovering=(async()=>{try{const r=await call('recover',{id:d.id,text:value,title:d.title,recovery_key:d.recoveryKey,sha256:d.sha256,chapter:d.chapter,content_kind:d.content_kind,base_sha256:d.base_sha256,prefix:d.prefix,recovery_sha256:d.recoverySha,recovery_metadata_sha256:d.recoveryMetadataSha});d.recovered=value;d.recoveryPath=r.path;d.recoverySha=r.recovery_sha256;d.recoveryMetadataSha=r.recovery_metadata_sha256;if(r.recovery_key)d.recoveryKey=r.recovery_key;pendingEdits();await catalog();if(active===d.id)note(r.conflict?'原恢复稿有变化或保存待核对，本次编辑已另存恢复稿，原文件保留。':'编辑已写入自动恢复稿；正式采用前仍须审查。');}catch(e){if(active===d.id)note('自动恢复保存失败：'+e.message+'，请下载当前文字。');}})();try{await d.recovering;}finally{d.recovering=null;}}
$('text').addEventListener('input',()=>{const d=docs.get(active);d.value=$('text').value;clearTimeout(d.timer);d.timer=setTimeout(()=>recover(d),1200);badge();scheduleMetrics();});
$('edit').onclick=()=>{const d=docs.get(active);d.editing=!d.editing;show(d);if(d.editing)$('text').focus();};
async function saveDocument(d){if((!dirty(d)&&!d.needs_recovery)||d.saving||reloading)return false;const text=d.value;d.saving=true;badge();try{await recover(d);const r=await call('save',{id:d.id,sha256:d.sha256,snapshot:d.snapshot,metadata_sha256:d.metadata_sha256,text});const fresh=await call('open',{id:r.id});fresh.value=d.value;fresh.editing=d.editing;fresh.recoveryKey=crypto.randomUUID();if(active===d.id)rememberView(d);fresh.positions={...d.positions};docs.set(r.id,fresh);d.value=d.text;d.needs_recovery=false;d.savedCandidate=r.id;clearTimeout(d.timer);if(active===d.id){show(fresh);location.hash=encodeURIComponent(fresh.id);}if(dirty(fresh))fresh.timer=setTimeout(()=>recover(fresh),1200);if(active===fresh.id)note('候选已保存 · '+new Date().toLocaleTimeString()+' · 尚未正式采用');await catalog(active===fresh.id);return true;}catch(e){note(e.message+' 当前文字仍在编辑器中，可下载或从自动恢复稿找回。');return false;}finally{d.saving=false;badge();}}
$('save').onclick=()=>saveDocument(docs.get(active));
$('save-all').onclick=async()=>{const pending=[...docs.values()].filter(d=>dirty(d)||d.needs_recovery);if([...docs.values()].some(d=>d.saving)){note('候选正在保存，请完成后再保存全部。');return;}let saved=0;for(const d of pending){if(!await saveDocument(d)){note('保存全部已停止；'+saved+' 份已保存，其余文字保留在页面中。请核对失败提示并下载未保存文字。');return;}saved++;}const remaining=[...docs.values()].filter(d=>dirty(d)||d.saving||d.needs_recovery).length;note('已保存 '+saved+' 份候选；尚未正式采用。'+(remaining?' 另有 '+remaining+' 份新增或未完成编辑，请继续保存。':''));};
$('compare').onclick=async()=>{$('before-label').textContent='当前正式稿';$('after-label').textContent='当前文字';const d=docs.get(active),id=active,seq=++compareSequence;try{const fresh=await call('open',{id:d.kind==='formal'?id:'formal:'+d.chapter});if(active!==id||seq!==compareSequence)return;
 if(!d.comparePosition)d.comparePosition={main:document.querySelector('main').scrollTop,editor:$('text').scrollTop,start:$('text').selectionStart,end:$('text').selectionEnd};
 $('compare-label').textContent=d.base_sha256?(d.base_sha256===fresh.sha256?'最新正式稿与当前文字：基线一致':'最新正式稿与当前文字：正式基线已变化'):'最新正式稿对照：未确认版本关系';drawDiff(fresh.text,d.value);
 $('review-task-box').hidden=true;$('document-body').hidden=true;$('difference').hidden=false;document.body.classList.add('comparing');document.querySelector('main').scrollTop=0;
 $('compare-back').textContent=d.editing?'返回编辑':'返回阅读';$('diff-position').textContent=diffTargets.length?'共 '+diffTargets.length+' 个标记片段':'两版文字一致';$('diff-prev').disabled=true;$('diff-next').disabled=!diffTargets.length;
 }catch(e){if(active===id&&seq===compareSequence)note('无法读取最新正式稿：'+e.message);}};
$('download').onclick=()=>{const d=docs.get(active),a=document.createElement('a');const url=URL.createObjectURL(new Blob([(d.prefix||'')+d.value],{type:'text/plain;charset=utf-8'}));a.href=url;a.download=downloadName(d);a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
async function reloadDocuments(preserve){if(reloading)return;if([...docs.values()].some(d=>d.saving)){note('候选稿正在保存，请完成后再重新载入。');return;}const changes=[...docs.values()].filter(dirty);if(changes.length&&!preserve){note('有未保存编辑，请使用“保留恢复稿并重新载入”。');return;}reloading=true;const id=active;try{for(const d of changes){await recover(d,true);if(d.value!==d.recovered){note('恢复尚未成功，已停止重新载入，请下载当前文字。');return;}}const fresh=id?await call('open',{id}):null;if(active!==id){note('已切换文件，本次重新载入取消；原编辑仍保留。');return;}for(const d of docs.values()){if(dirty(d)&&d.value!==d.recovered){note('恢复期间又有输入，已保留编辑；请再次操作。');return;}}++openSequence;for(const d of docs.values())clearTimeout(d.timer);docs.clear();active=null;if(fresh){fresh.value=fresh.text||'';fresh.editing=false;fresh.recoveryKey=crypto.randomUUID();docs.set(fresh.id,fresh);show(fresh);}note(changes.length?'恢复稿已保留，来源已重新载入；可从左栏打开恢复稿。':'来源已重新读取。');await catalog();}catch(e){note('重新载入失败：'+e.message+'；原编辑仍保留。');}finally{reloading=false;}}
$('refresh').onclick=()=>catalog();
$('reload-source').onclick=()=>reloadDocuments(false);
$('recover-reload').onclick=()=>reloadDocuments(true);
$('search').oninput=()=>{clearTimeout(queryTimer);offset=0;pendingFocus=false;queryTimer=setTimeout(()=>catalog(false,true),300);};$('older').onclick=()=>{offset+=LIMIT;catalog(false,true);};$('newer').onclick=()=>{offset=Math.max(0,offset-LIMIT);catalog(false,true);};
window.addEventListener('beforeunload',event=>{if([...docs.values()].some(d=>dirty(d)||d.saving||d.needs_recovery)){event.preventDefault();event.returnValue='';}});
window.addEventListener('hashchange',()=>{let id;try{id=decodeURIComponent(location.hash.slice(1));}catch{return;}if(/^chapter-\d+$/.test(id))id='formal:'+id.slice(8);if(id&&id!==active)openDoc(id);});
__EDITOR_TOOLS__setupWorkspace();catalog();""".replace('__EDITOR_TOOLS__', _editor_tools_script()).replace('__TOKEN__', json.dumps(token)).replace('__LIMIT__', str(packet['chapters']['limit'])).replace('__BOOK_DISPLAY__', _book_display_script())
    # Keep the session-token declaration on its own first line so the interface
    # identifier below remains independent of the current editing session.
    first_line, _, remainder = script.partition('\n')
    script = first_line + '\n' + _workbench_theme_script() + remainder
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    page = '''<!doctype html><html lang="zh-CN" data-story-workbench data-workbench-view="editor"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; connect-src 'self'; img-src data:; script-src 'sha256-__HASH__'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>__TITLE__ · 写作工作台</title><style>
:root{--reader-size:19px;--reader-line:1.8;--reader-font:'Songti SC','SimSun',serif;--ink:#273447;--muted:#64748b;--line:#e2e8ef;--accent:#335f86}
*{box-sizing:border-box}[hidden]{display:none!important}html,body{height:100%;margin:0}body{display:grid;grid-template-rows:auto minmax(0,1fr);overflow:hidden;color:var(--ink);background:#f6f7f9;font:14px/1.65 system-ui,-apple-system,'PingFang SC',sans-serif}
header{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:10px 20px;border-bottom:1px solid var(--line);background:#fff;z-index:5}.book-heading{flex:1;min-width:0}.book-identity{display:flex;align-items:center;gap:9px;min-width:0;font-size:12px;color:var(--muted)}.book-kind{flex-shrink:0;background:#edf2f7;padding:1px 7px;border-radius:4px}#book-title{display:block;min-width:0;font-size:17px;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}#book-progress{color:var(--muted);font-size:11px;line-height:1.6;margin:3px 0 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.header-actions,.mode-actions,.save-actions{display:flex;align-items:center;gap:6px}.header-actions{flex-shrink:0}.header-actions>button,.header-actions>details>summary{font-size:12px;padding:6px 9px;white-space:nowrap}.menu-panel.book-info-panel{width:380px}.menu-panel.book-info-panel>strong{display:block;font-size:14px;color:var(--ink);overflow-wrap:anywhere}.menu-panel.book-info-panel>p{font-size:12px;color:var(--muted);margin:7px 0 12px;overflow-wrap:anywhere}.menu-panel.book-info-panel>details{border-top:1px solid var(--line);margin-top:12px;padding-top:10px}.menu-panel.book-info-panel>details>summary{font-size:12px;margin:0}#current-book-root{font-size:12px;margin:8px 0 0;white-space:pre-wrap;overflow-wrap:anywhere}
button,input,select{font:inherit}button,summary{touch-action:manipulation}button{cursor:pointer;border:1px solid var(--line);background:#fff;border-radius:8px;padding:7px 11px;color:var(--ink);transition:background .12s}button:hover:not(:disabled){background:#edf2f7}button:disabled{opacity:.42;cursor:default}button.primary{background:var(--accent);color:white;border-color:var(--accent)}button.primary:hover:not(:disabled){background:#284d70}button:focus-visible,input:focus-visible,textarea:focus-visible,summary:focus-visible,select:focus-visible{outline:2px solid #467aa9;outline-offset:3px}
summary{cursor:pointer;color:var(--muted);margin:14px 0 8px;overflow-wrap:anywhere}.menu{position:relative}.menu>summary{list-style:none;margin:0;border:1px solid var(--line);border-radius:8px;padding:7px 11px;color:var(--ink)}.menu>summary::-webkit-details-marker{display:none}.menu[open]>summary{background:#edf2f7}.menu-panel{position:absolute;right:0;top:calc(100% + 8px);width:280px;max-width:calc(100vw - 32px);max-height:65vh;overflow:auto;padding:14px;background:white;box-shadow:0 10px 32px #27344722;border:1px solid var(--line);border-radius:12px;z-index:30}.menu-panel>button{display:block;text-align:left;border:0;width:100%;margin:4px 0}.menu-panel>strong{font-size:12px;color:var(--muted)}#root-hint{font-size:12px;overflow-wrap:anywhere}#build-label{display:block;font-size:11px;color:var(--muted);margin-top:12px}
.desk{display:grid;grid-template-columns:var(--nav-width,236px) minmax(0,1fr) var(--context-width,248px);min-height:0;position:relative}nav,aside{overflow:auto;overscroll-behavior:contain;padding:18px 16px;background:#f6f8fa;min-width:0}nav{border-right:1px solid var(--line)}aside{border-left:1px solid var(--line)}main{overflow:auto;overscroll-behavior:contain;padding:14px clamp(22px,3vw,48px) 60px;background:#fff;min-width:0;scroll-padding-top:72px}.tools{display:flex;flex-wrap:wrap;justify-content:space-between;gap:6px;position:sticky;top:-14px;background:#fff;padding:7px 0;border-bottom:1px solid var(--line);z-index:4}.tools button,.tools summary{white-space:nowrap;font-size:12px;padding:6px 9px}
h1{font-size:24px;line-height:1.45;font-weight:650;margin:14px 0 7px;overflow-wrap:anywhere}.document-meta{display:flex;gap:4px 12px;flex-wrap:wrap;align-items:center;margin-bottom:14px}.document-meta p{margin:0}#state{font-size:12px;color:#6e5a38}#word-count{font-size:12px;color:var(--muted)}#message[hidden]{display:none}#message{font-size:12px;color:var(--muted);overflow-wrap:anywhere;min-height:1.6em;margin:8px 0}#history-note{font:12px/1.7 system-ui;color:#866536;background:#faf5e9;padding:8px 12px;border-radius:6px}#history-note:empty{display:none}#document-body{max-width:780px;margin:0 auto}#prose,#text,#formatted{font-size:var(--reader-size);line-height:var(--reader-line);font-family:var(--reader-font)}pre{white-space:pre-wrap;overflow-wrap:anywhere}#prose{margin:0}.prose-paragraph{display:block;white-space:pre-wrap}.prose-gap{display:block;white-space:pre;line-height:0;font-size:0;overflow:hidden}.document-name{display:block}.document-type{display:block;font-size:11px;color:var(--muted);margin-top:2px}.volume-heading{font-size:12px;font-weight:650;color:var(--muted);margin:20px 8px 4px}#count-details{font-size:12px;color:var(--muted)}#count-details>summary{padding:0;margin:0}#count-method{max-width:550px;padding:8px 0;line-height:1.7}#chapter-info>details{border-top:1px solid var(--line);margin-top:12px}#reading-options .menu-panel{left:auto;right:0;width:240px}#reading-options label{display:block}#locate,#refresh{border-color:transparent;background:transparent}#edit[aria-pressed="true"]{background:#e8eef5;border-color:#c4d2e2;color:#28547a}textarea{width:100%;min-height:62vh;resize:vertical;border:1px solid #c6d3e1;border-radius:8px;padding:20px;color:var(--ink);background:#fff;font:19px/1.9 'Songti SC','SimSun',serif}#text{padding:0;border:0;border-radius:0;outline-offset:5px}#text:focus{box-shadow:0 0 0 3px #335f8610}#cover{display:block;max-width:100%;max-height:75vh;margin:auto}
.navigation-mode{display:flex;gap:4px;flex-wrap:wrap;margin:14px 0}.navigation-mode button{font-size:12px}.navigation-mode [aria-pressed="true"]{background:#e8eef5;border-color:#c4d2e2;color:#28547a}#locate{border-color:transparent;color:var(--muted)}#search,#full-query,#book-select{width:100%;max-width:100%;padding:9px;border:1px solid var(--line);border-radius:7px;background:white}.pager{display:flex;gap:8px;margin:12px 0 4px}.pager button{font-size:12px}#range{font-size:11px;color:var(--muted);margin:6px 0 18px}.document{display:block;width:100%;text-align:left;margin:4px 0;padding:9px 10px;border:1px solid transparent;border-radius:7px;background:transparent;overflow-wrap:anywhere;font-size:12px}.selected{background:#e6eef7!important;border-color:#d1dfef;color:#244f77;font-weight:600}.dirty:after{content:' · 未保存';color:#9b582e}#list>details{padding-bottom:8px;border-bottom:1px solid #e8edf2}#list>details>summary{font-size:12px;line-height:1.8;color:#43546a}#directory-warning{font-size:12px;color:#936333;overflow-wrap:anywhere}#pending-box{border:1px solid #e8d6ad;border-radius:8px;padding:10px;margin-bottom:14px;background:#fffaf0}#pending-count{font-size:12px}#pending-list{max-height:180px;overflow:auto}#search-results small{display:block;font-size:12px;color:var(--muted);margin-top:6px}#search-status,#book-hint{white-space:pre-wrap;font:12px/1.7 system-ui}
aside h3{margin:0 0 20px;font-size:13px;font-weight:650}#chapter-info{font:13px/1.9 system-ui;white-space:pre-wrap}#chapter-info h4{font-size:11px;color:var(--muted);font-weight:500;margin:22px 0 5px}#chapter-info h4:first-child{margin-top:0}#chapter-info p{margin:0 0 12px}#version-details,#adoption-help{border-top:1px solid var(--line);margin-top:22px;font-size:12px}#detail,#path{font:12px/1.8 system-ui;overflow-wrap:anywhere;white-space:pre-wrap}#reading-options{font-size:12px;color:var(--muted)}#reading-options>summary{margin:0}#reading-options label{display:inline-block;margin:8px 12px 8px 0}#reading-options select{font:inherit}
#formatted{overflow-wrap:anywhere}#formatted h1{font-size:1.35em}#formatted h2{font-size:1.18em}#formatted h3{font-size:1.05em}#formatted pre{font:14px/1.6 monospace;background:#f5f7fa;padding:12px}#formatted code{font-size:.85em;background:#f1f4f8}#formatted table{border-collapse:collapse;font:14px/1.7 system-ui;width:100%}#formatted td,#formatted th{border:1px solid var(--line);padding:8px;text-align:left}.table-scroll{overflow:auto}.markdown-image{display:block;margin:12px 0}.markdown-image img{display:block;max-width:100%;max-height:75vh;height:auto}.markdown-image>[role="status"]{font:13px/1.6 system-ui;color:var(--muted)}
#review-task-box{padding:18px;background:#eef4fa;border-radius:10px;margin:20px 0;scroll-margin-top:85px}#review-prompt{min-height:220px;font:13px/1.8 system-ui}#review-findings{font:13px/1.6 system-ui;color:#8a392b}.comparison{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}.comparison section{min-width:0}.comparison h3{font-size:12px;color:var(--muted)}.comparison pre{font:15px/1.9 var(--reader-font);background:#f7f8fa;padding:12px;border-radius:8px}.diff-line{display:block;white-space:pre-wrap;min-height:1em;scroll-margin-top:135px}.diff-line.removed{background:#ffe3df;color:#882f26}.diff-line.added{background:#ddf4e4;color:#215b36}.diff-tools{display:flex;flex-wrap:wrap;gap:8px;align-items:center;position:sticky;top:30px;background:white;z-index:3;padding:10px 0;font-size:12px}.diff-tools button{font-size:12px}#compare-label{font-size:12px;color:var(--muted)}.comparing #reading-options,.comparing #word-count,.comparing #history-note,.comparing #edit,.comparing #source-view{display:none}
.context-closed .desk,.focus-reading .desk{grid-template-columns:var(--nav-width,236px) minmax(0,1fr)}.context-closed aside,.focus-reading aside{display:none}.focus-reading #document-body{max-width:760px}#panel-dismiss,#refresh-compact,.panel-close{display:none}
#match-tools{position:sticky;top:40px;background:white;padding:10px 0;z-index:3;display:flex;flex-wrap:wrap;gap:8px;align-items:center}#match-query{font:12px/1.7 system-ui}#match-text mark{background:#fff1a6;scroll-margin-top:150px}#match-text mark.current-match{background:#ffc66b;outline:2px solid #a85813}
@media(max-width:1100px){header{padding:10px 16px;gap:10px}.header-actions{gap:4px}.header-actions>button,.header-actions>details>summary{font-size:12px;padding:6px 8px}main{padding:14px 22px 60px}nav,aside{padding:16px 14px}.tools{gap:4px}.mode-actions,.save-actions{gap:4px}.tools button,.tools summary{padding:6px 8px;font-size:12px}}
.book-classification{max-width:620px}.book-classification .genre-label{font-size:11px;color:var(--muted);margin:5px 0 2px}.book-classification .genre-tags{display:flex;flex-wrap:wrap;gap:5px;margin:2px 0 4px}.book-classification .genre-tag{font-size:11px;padding:1px 6px;border-radius:4px;background:#edf3ed;color:#41644c}.book-classification .genre-tag.pending{background:#f5f1e7;color:#8a6a3e}.book-classification .classification-sources{font-size:11px;color:var(--muted);margin:4px 0}.book-classification .classification-sources>summary{padding:0;font-size:11px}.book-classification .classification-sources p{font-size:11px;white-space:pre-wrap;overflow-wrap:anywhere;margin:6px 0;max-width:620px}
.identity-badges{display:flex;gap:6px;flex-wrap:wrap;margin:0}.identity-badge{font:11px/1.6 system-ui;padding:1px 7px;border:1px solid #d5dde4;border-radius:5px;background:#f3f6f8;color:#496072}.identity-badge[data-kind="formal"]{background:#eaf3ee;color:#315f48;border-color:#cbded1}.identity-badge[data-kind="candidate"]{background:#edf2fa;color:#365b87;border-color:#d1dced}.identity-badge[data-kind="recovery"],.identity-badge[data-kind="pending"],.identity-badge[data-kind="uncertain"],#document-unsaved{background:#faf1df;color:#7b5a25;border-color:#e9d7ad}.identity-badges[hidden],.identity-badge[hidden]{display:none}.document-location{display:block;font-size:11px;line-height:1.5;margin-top:4px;color:var(--muted);overflow-wrap:anywhere}
#chapter-picker{display:flex;align-items:center;gap:8px;max-width:100%;flex-basis:100%;font-size:12px;color:var(--muted)}#chapter-version{flex:1;min-width:0;max-width:100%;padding:6px 8px;background:#f8fafb;color:var(--ink);border:1px solid var(--line);border-radius:6px}.chapter-entry{margin:2px 0;padding:10px}.chapter-entry span{display:block}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.column-resizer{position:absolute;top:0;bottom:0;width:8px;z-index:6;cursor:col-resize;touch-action:none}#resize-left{left:calc(var(--nav-width,236px) - 4px)}#resize-right{right:calc(var(--context-width,248px) - 4px)}.column-resizer:hover,.column-resizer:focus-visible{background:#335f8630;outline:1px solid var(--accent)}.resizing{cursor:col-resize;user-select:none}.nav-closed nav,.nav-closed #resize-left,.context-closed #resize-right,.focus-reading #resize-right{display:none}.nav-closed .desk{grid-template-columns:minmax(0,1fr) var(--context-width,248px)}.nav-closed.context-closed .desk,.nav-closed.focus-reading .desk{grid-template-columns:minmax(0,1fr)}
@media(max-width:820px){.column-resizer{display:none!important}.nav-closed .desk,.nav-closed.context-closed .desk,.nav-closed.focus-reading .desk{grid-template-columns:minmax(0,1fr)}#reading-options .menu-panel{width:200px}header{padding:9px 14px;gap:8px;flex-wrap:wrap}.book-heading{flex-basis:100%}#book-title{font-size:15px}.header-actions{width:100%;justify-content:flex-start;gap:5px}.header-actions>#refresh{display:none}.menu-panel.book-info-panel{position:fixed;top:100px;right:14px;left:14px;width:auto;max-width:none;max-height:calc(100vh - 124px)}#nav-toggle,.panel-close{display:inline-block}.panel-close{margin-bottom:12px}#refresh-compact{display:block}.desk,.context-closed .desk,.focus-reading .desk{grid-template-columns:minmax(0,1fr)}main{padding:12px 18px 60px}.tools{top:-12px}nav,aside{display:none!important;position:absolute;top:0;bottom:0;width:min(320px,85vw);z-index:12;box-shadow:0 8px 35px #24344730}.nav-open nav{display:block!important;left:0}.context-open aside{display:block!important;right:0}.nav-open #panel-dismiss,.context-open #panel-dismiss{display:block;position:absolute;inset:0;border:0;border-radius:0;background:#26344833;z-index:10;width:100%;height:100%}.comparison{grid-template-columns:minmax(0,1fr)}.diff-tools{top:28px}h1{font-size:22px}.document-meta{margin-bottom:14px}#document-body{max-width:700px}}
.editor-dialog{color:var(--ink);background:var(--surface,#fff);border:1px solid var(--line);border-radius:12px;width:min(620px,92vw);max-height:80vh;overflow:auto;padding:24px}.editor-dialog::backdrop{background:#15253266}.editor-dialog label{display:grid;gap:6px;margin:12px 0}.editor-dialog select{width:100%;padding:8px}.editor-dialog button{display:block;max-width:100%;white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0}.editor-dialog p{color:var(--muted)}
@media(prefers-reduced-motion:reduce){button{transition:none}}
.native-compact-editor{--paper:#f5f8fb;--surface:#fff;--ink:#253b4d;--muted:#5b7082;--line:#dde7ef;--accent:#147dad;--wb-panel:#f2f6fa;--wb-tint:#edf4f9;--wb-hover:#eaf4fb;--wb-selected:#e2f2fc;--wb-selected-ink:#125a7e;--wb-button:#38bdf8;--wb-button-ink:#082f49;color-scheme:light;color:var(--ink);background:var(--paper)}
html[data-theme="dark"] .native-compact-editor{--paper:#141c24;--surface:#1b2732;--ink:#e0ebf3;--muted:#9eb2c2;--line:#334858;--accent:#79cbf2;--wb-panel:#17222c;--wb-tint:#253746;--wb-hover:#2b4052;--wb-selected:#23485f;--wb-selected-ink:#c1eafe;--wb-button:#38bdf8;--wb-button-ink:#082f49;color-scheme:dark}
@media(prefers-color-scheme:dark){html:not([data-theme]) .native-compact-editor{--paper:#141c24;--surface:#1b2732;--ink:#e0ebf3;--muted:#9eb2c2;--line:#334858;--accent:#79cbf2;--wb-panel:#17222c;--wb-tint:#253746;--wb-hover:#2b4052;--wb-selected:#23485f;--wb-selected-ink:#c1eafe;--wb-button:#38bdf8;--wb-button-ink:#082f49;color-scheme:dark}}
body.native-compact-editor>header{align-items:center;gap:20px;padding:12px 22px;background:var(--surface);border-color:var(--line)}
.native-compact-editor .book-heading{flex:1;min-width:0}.native-compact-editor .book-identity{display:flex;align-items:center;gap:9px;min-width:0;font-size:12px}
.native-compact-editor #book-title{display:block;min-width:0;font-size:16px;font-weight:650;letter-spacing:.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.native-compact-editor #book-kind{flex-shrink:0;font-size:10px;border-radius:5px;padding:2px 7px;color:var(--muted);background:var(--wb-tint)}
.native-compact-editor #book-progress{font-size:11px;line-height:1.6;margin:4px 0 0;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.native-compact-editor .header-actions{flex-shrink:0;gap:5px;max-width:none}.native-compact-editor .header-actions>button,.native-compact-editor .header-actions>details>summary{font-size:12px;padding:7px 9px;white-space:nowrap;border-color:transparent;background:transparent}
.native-compact-editor .header-actions>.workbench-theme-control{gap:6px;margin-left:5px;padding-left:12px;border-left:1px solid var(--line);font-size:11px;color:var(--muted)}
.native-compact-editor .workbench-theme-control select{min-height:31px;max-width:108px;font-size:12px;border-radius:7px;background:var(--wb-tint);border-color:transparent;padding:5px 7px}
.native-compact-editor button,.native-compact-editor select,.native-compact-editor input,.native-compact-editor .menu>summary{color:var(--ink);border-color:var(--line);background:var(--surface)}
.native-compact-editor button:hover:not(:disabled),.native-compact-editor .menu[open]>summary{background:var(--wb-hover)}
.native-compact-editor button:focus-visible,.native-compact-editor input:focus-visible,.native-compact-editor textarea:focus-visible,.native-compact-editor select:focus-visible,.native-compact-editor summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.native-compact-editor .menu-panel{padding:10px;border-radius:12px;border-color:var(--line);background:var(--surface);box-shadow:0 12px 30px #102a3b14}.native-compact-editor .menu-panel>button{padding:8px 10px;margin:2px 0;border-radius:7px;font-size:12px}.native-compact-editor .menu-panel>strong{display:block;padding:12px 10px 4px;font-size:10px;letter-spacing:.06em}
.native-compact-editor .menu-panel.book-info-panel{width:360px;padding:18px}.native-compact-editor .book-info-panel>strong{display:block;padding:0;font-size:14px;letter-spacing:0;color:var(--ink);overflow-wrap:anywhere}.native-compact-editor .book-info-panel>p{font-size:12px;color:var(--muted);margin:7px 0 12px;overflow-wrap:anywhere}.native-compact-editor .book-info-panel>details{border-top:1px solid var(--line);margin-top:12px;padding-top:10px}.native-compact-editor .book-info-panel>details>summary{font-size:12px;margin:0}
.native-compact-editor #current-book-location{position:static}.native-compact-editor #current-book-root{position:static;width:auto;max-width:100%;font-size:12px;margin:8px 0 0;white-space:pre-wrap;overflow-wrap:anywhere;padding:0;border:0;box-shadow:none;background:transparent}
.native-compact-editor nav,.native-compact-editor aside{padding:22px 16px;background:var(--wb-panel);border-color:var(--line)}
.native-compact-editor nav>details>summary{font-size:12px;color:var(--muted)}.native-compact-editor nav>details:last-child{margin-top:20px;padding-top:4px;border-top:1px solid var(--line)}
.native-compact-editor .navigation-mode{display:grid;grid-template-columns:1fr 1fr;gap:3px;padding:4px;background:var(--wb-tint);border-radius:9px;margin:18px 0 14px}.native-compact-editor .navigation-mode button{font-size:11px;padding:6px 4px;border-color:transparent;background:transparent;border-radius:6px}.native-compact-editor .navigation-mode [aria-pressed="true"]{background:var(--surface)!important;color:var(--wb-selected-ink);font-weight:600;box-shadow:0 1px 3px #102a3b10}.native-compact-editor #locate{grid-column:1/-1;padding:5px;font-size:11px;color:var(--muted)}
.native-compact-editor #search,.native-compact-editor #full-query,.native-compact-editor #book-select{font-size:12px;border-radius:8px;background:var(--surface);color:var(--ink);border-color:var(--line);padding:9px 10px}.native-compact-editor .pager{gap:5px}.native-compact-editor .pager button{flex:1;font-size:11px;padding:5px;background:transparent}.native-compact-editor #range{font-size:10px;margin-bottom:14px}
.native-compact-editor .document{padding:10px 11px;margin:3px 0;border-radius:8px;line-height:1.6;border-color:transparent;background:transparent}.native-compact-editor .document:hover{background:var(--wb-hover)}.native-compact-editor .document.selected{background:var(--wb-selected)!important;color:var(--wb-selected-ink);box-shadow:inset 3px 0 var(--accent);font-weight:600}.native-compact-editor .document-type,.native-compact-editor .document-location{font-size:10px;color:var(--muted)}.native-compact-editor #list>details{border-color:var(--line);padding-bottom:10px}.native-compact-editor #list>details>summary{font-size:12px;font-weight:600;color:var(--ink)}
.native-compact-editor main{padding:16px clamp(24px,3.5vw,54px) 80px;scroll-padding-top:80px;background:var(--surface)}
.native-compact-editor .tools{gap:8px;top:-16px;padding:10px 0 12px;background:var(--surface);border-color:var(--line)}.native-compact-editor .mode-actions,.native-compact-editor .save-actions{gap:5px}.native-compact-editor .tools button,.native-compact-editor .tools summary{font-size:12px;padding:7px 10px;border-radius:7px}.native-compact-editor .tools #edit,.native-compact-editor .tools #compare,.native-compact-editor .tools .menu>summary{border-color:transparent;background:transparent}.native-compact-editor .tools #edit[aria-pressed="true"]{background:var(--wb-selected);color:var(--wb-selected-ink)}
.native-compact-editor #save{min-height:34px;padding:7px 15px;font-weight:600;letter-spacing:.01em;background:var(--wb-button);border-color:var(--wb-button);color:var(--wb-button-ink)}.native-compact-editor #save:hover:not(:disabled){filter:brightness(.94)}.native-compact-editor #save:disabled{background:var(--wb-tint);border-color:transparent;color:var(--muted);opacity:1}
.native-compact-editor #title{max-width:780px;margin:26px auto 10px;font-size:27px;font-weight:600;line-height:1.45;letter-spacing:.01em}.native-compact-editor .document-meta{max-width:780px;gap:6px 12px;margin:0 auto 26px;padding-bottom:18px;border-bottom:1px solid var(--line)}.native-compact-editor #message{max-width:780px;margin:12px auto 0;font-size:11px;line-height:1.7;color:var(--muted)}.native-compact-editor #chapter-picker{font-size:11px;gap:10px;margin-top:4px}.native-compact-editor #chapter-version{font-size:11px;padding:6px 8px;background:var(--wb-panel);border-color:transparent;border-radius:6px}.native-compact-editor #count-details,.native-compact-editor #word-count,.native-compact-editor #state{font-size:11px}
.native-compact-editor #message:empty{display:none}.native-compact-editor #document-options{min-width:0;max-width:100%;font-size:11px;color:var(--muted)}.native-compact-editor #document-options>summary{font-size:11px;margin:0;padding:0;color:var(--muted)}.native-compact-editor #document-options[open]{flex-basis:100%}.native-compact-editor #document-options[open]>summary{margin-bottom:8px}.native-compact-editor #document-options:has(>#chapter-picker[hidden]){display:none}.native-compact-editor #chapter-picker-help{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.native-compact-editor .identity-badges{flex-basis:auto;margin:0;gap:5px}.native-compact-editor .identity-badge{font-size:10px;padding:2px 7px;border-radius:5px}.native-compact-editor #history-note{max-width:780px;margin:0 auto 22px}.native-compact-editor #document-body{max-width:780px;margin:0 auto}.native-compact-editor #prose,.native-compact-editor #text{color:var(--ink);background:transparent}.native-compact-editor #text:focus{box-shadow:none}.native-compact-editor #text{outline-offset:9px}
.native-compact-editor aside h3{margin:0 0 18px;font-size:11px;letter-spacing:.08em;color:var(--muted)}.native-compact-editor #chapter-info{font-size:12px;line-height:1.9}.native-compact-editor #chapter-info h4{font-size:10px;margin:20px 0 7px;color:var(--muted)}.native-compact-editor #chapter-info p{margin-bottom:14px}.native-compact-editor #version-details,.native-compact-editor #adoption-help{margin-top:20px;font-size:11px}.native-compact-editor #version-details>summary,.native-compact-editor #adoption-help>summary{padding-top:2px}.native-compact-editor #detail,.native-compact-editor #path{font-size:11px;color:var(--muted)}
@media(max-width:1100px){body.native-compact-editor>header{padding:11px 16px;gap:12px}.native-compact-editor .header-actions{gap:2px}.native-compact-editor .header-actions>button,.native-compact-editor .header-actions>details>summary{padding:7px}.native-compact-editor .header-actions>.workbench-theme-control{padding-left:8px;margin-left:2px}.native-compact-editor main{padding:16px 26px 70px}.native-compact-editor .tools button,.native-compact-editor .tools summary{padding:7px 8px}.native-compact-editor #title{font-size:25px}}
@media(max-width:820px){body.native-compact-editor>header{padding:11px 14px;gap:9px;flex-wrap:wrap}.native-compact-editor .book-heading{flex-basis:100%}.native-compact-editor #book-title{font-size:15px}.native-compact-editor .header-actions{width:100%;max-width:none;justify-content:flex-start;flex-wrap:wrap;gap:4px}.native-compact-editor .header-actions>.workbench-theme-control{margin-left:auto;border-left:0;padding-left:0}.native-compact-editor .header-actions>#refresh{display:none}.native-compact-editor .workbench-theme-control select{max-width:100px}.native-compact-editor .menu-panel.book-info-panel{position:fixed;top:112px;right:14px;left:14px;width:auto;max-width:none;max-height:calc(100vh - 138px)}.native-compact-editor main{padding:12px 20px 64px}.native-compact-editor .tools{top:-12px;padding:8px 0 10px;gap:6px}.native-compact-editor .tools button,.native-compact-editor .tools summary{font-size:11px;padding:7px}.native-compact-editor #save{padding:7px 12px}.native-compact-editor #title{font-size:23px;margin-top:22px}.native-compact-editor .document-meta{margin-bottom:22px}.native-compact-editor nav,.native-compact-editor aside{width:min(340px,88vw);padding:20px 18px}}
@media(max-width:480px){.native-compact-editor .header-actions>button,.native-compact-editor .header-actions>details>summary{font-size:11px;padding:6px}.native-compact-editor .header-actions>.workbench-theme-control{font-size:10px;gap:4px}.native-compact-editor .workbench-theme-control select{font-size:11px;max-width:91px;min-height:29px}.native-compact-editor main{padding:10px 17px 56px}.native-compact-editor .tools{top:-10px;row-gap:9px}.native-compact-editor .mode-actions{gap:1px}.native-compact-editor .save-actions{margin-left:auto}.native-compact-editor .tools button,.native-compact-editor .tools summary{padding:6px}.native-compact-editor #title{font-size:21px}.native-compact-editor #chapter-picker{flex-wrap:wrap}.native-compact-editor #chapter-picker label{flex-basis:100%}}
@media(prefers-reduced-motion:reduce){.native-compact-editor button{transition:none}}
</style><style>__THEME_CSS__</style></head><body class="native-compact-editor"><header><div class="book-heading"><div class="book-identity"><strong id="book-title" title="__TITLE__">__TITLE__</strong><span id="book-kind" class="book-kind" data-kind="__KIND_CODE__">__KIND__</span></div><p id="book-progress" role="status">正在读取当前进度…</p></div><div class="header-actions"><button id="nav-toggle" aria-expanded="false" aria-controls="book-nav">作品目录</button><button id="context-toggle" aria-expanded="true" aria-controls="book-context">收起辅助栏</button><button id="refresh" title="刷新目录与状态，不丢弃未保存文字">刷新</button>__THEME_CONTROL__<details id="book-info" class="menu"><summary>作品信息</summary><div id="book-info-panel" class="menu-panel book-info-panel"><strong id="book-info-title">__TITLE__</strong><p id="book-progress-detail">正在读取当前进度…</p><div id="book-classification" class="book-classification" hidden></div><details id="current-book-location"><summary>目录位置</summary><p id="current-book-root">__ROOT__</p></details><details id="root-location"><summary>书库与材料位置</summary><span id="root-hint"></span></details></div></details><details id="maintenance" class="menu"><summary>设置</summary><div class="menu-panel"><button id="refresh-compact">刷新目录</button><button id="layout-reset">恢复默认布局</button><strong>文件与恢复</strong><button id="reload-source">重新读取文件</button><button id="recover-reload">保留恢复稿并重新载入</button><button id="recoveries-open">恢复中心</button><button id="save-all">保存全部候选</button><small id="build-label" title="用于区分工作台页面更新，不是技能发布版本">界面标识：__BUILD__</small></div></details></div></header><div class="desk"><nav id="book-nav" aria-label="作品目录" tabindex="-1"><button id="nav-close" class="panel-close">关闭目录</button><details><summary>切换作品</summary><select id="book-select" aria-label="选择作品"></select><button id="book-open">打开作品</button><p id="book-hint"></p></details><div class="navigation-mode"><button id="view-chapters">按章查看</button><button id="view-files">按文件查看</button><button id="locate">定位当前文件</button></div><section id="pending-box" hidden><strong id="pending-count"></strong><div id="pending-list"></div></section><input id="search" type="search" placeholder="搜索全书章名或材料路径" aria-label="搜索作品文件"><div class="pager"><button id="newer">上一页</button><button id="older">下一页</button></div><p id="range"></p><p id="directory-warning" role="status"></p><div id="list"></div><details><summary>全文搜索</summary><input id="full-query" aria-label="搜索正文与材料内容" placeholder="搜索正文与材料内容"><button id="full-go">搜索内容</button><p id="search-status" role="status"></p><div id="search-results"></div><button id="search-prev" disabled>上一页结果</button><button id="search-next" disabled>下一页结果</button></details></nav><div id="resize-left" class="column-resizer" role="separator" aria-label="调整目录宽度" aria-orientation="vertical" aria-valuemin="180" aria-valuemax="360" tabindex="0"></div><main><div class="tools"><div class="mode-actions"><button id="edit" disabled>编辑</button><button id="compare" disabled>对照正式稿</button><details id="reading-options" class="menu"><summary>阅读设置</summary><div class="menu-panel"><label>字号 <select id="font-size"><option>16</option><option selected>19</option><option>22</option><option>25</option></select></label><label>行距 <select id="line-height"><option>1.6</option><option selected>1.8</option><option>1.9</option><option>2.2</option></select></label><label>字体 <select id="font-family"><option value="serif">宋体</option><option value="sans">黑体</option></select></label><label><input id="focus-reading" type="checkbox">专注阅读</label></div></details></div><div class="save-actions"><button id="save" class="primary" disabled title="保存候选稿（⌘S / Ctrl+S）">保存候选稿</button><details id="more-actions" class="menu"><summary>更多</summary><div class="menu-panel"><button id="writing-task">继续下一章</button><button id="review-task" disabled>生成审稿任务</button><button id="compare-choose">选择两稿对照</button><button id="source-view" hidden>查看源码</button><button id="download" disabled>下载当前文字</button></div></details></div></div><p id="message" role="status"></p><h1 id="title">选择章节或材料</h1><div class="document-meta"><div id="document-identity" class="identity-badges" role="status" hidden><span id="document-role" class="identity-badge"></span><span id="document-unsaved" class="identity-badge" hidden>当前文字未保存</span></div><details id="document-options"><summary>稿件与材料</summary><div id="chapter-picker" hidden><label for="chapter-version">本章稿件与材料</label><select id="chapter-version" aria-describedby="chapter-picker-help"></select><span id="chapter-picker-help" class="sr-only"></span></div></details><p id="state"></p><details id="count-details"><summary id="word-count" aria-label="字数与统计口径"></summary><p id="count-method"></p></details></div><p id="history-note"></p><div id="document-body"><section id="match-view" hidden><div id="match-tools"><button id="match-back">返回阅读位置</button><button id="match-prev">上一处命中</button><span id="match-position" role="status"></span><button id="match-next">下一处命中</button></div><p id="match-query"></p><pre id="match-text"></pre></section><div id="formatted" hidden></div><pre id="prose"></pre><textarea id="text" aria-label="候选正文编辑" hidden></textarea><img id="cover" alt="封面预览" hidden></div><section id="review-task-box" hidden><h3 id="assistant-task-title">交给助手审稿</h3><p id="review-task-status"></p><pre id="review-findings"></pre><textarea id="review-prompt" aria-label="审稿任务" readonly></textarea><button id="copy-review">复制审稿任务</button></section><section id="difference" hidden><div class="diff-tools"><button id="compare-back">返回阅读</button><button id="diff-prev">上一处差异</button><span id="diff-position" role="status"></span><button id="diff-next">下一处差异</button></div><p id="compare-label"></p><div class="comparison"><section><h3 id="before-label">当前正式稿</h3><pre id="before"></pre></section><section><h3 id="after-label">当前文字</h3><pre id="after"></pre></section></div></section></main><div id="resize-right" class="column-resizer" role="separator" aria-label="调整辅助栏宽度" aria-orientation="vertical" aria-valuemin="200" aria-valuemax="360" tabindex="0"></div><button id="panel-dismiss" aria-label="关闭侧栏" tabindex="-1"></button><aside id="book-context" aria-label="写作辅助" tabindex="-1"><button id="context-close" class="panel-close">关闭辅助栏</button><h3 id="context-title">本章写作</h3><div id="chapter-info"></div><details id="version-details"><summary>文件与版本</summary><pre id="detail"></pre><details id="file-location"><summary>文件位置</summary><p id="path"></p></details></details><details id="adoption-help"><summary>保存与采用说明</summary><p>保存不会覆盖正式稿。请将候选路径交给助手，按现有审稿和历史修订流程采用。</p><p>旧稿未登记的版本关系会明确标注；自动恢复稿须自行核对后再采用。</p><p>自动恢复在停止输入后写入本书文件；失败会提示。关闭或崩溃前尚未写入的文字仍可能丢失。</p></details></aside></div><script>__SCRIPT__</script></body></html>'''
    return page.replace('__THEME_CSS__', _workbench_theme_css()).replace('__THEME_CONTROL__', _workbench_theme_control()).replace('__KIND__', _escape({'long': '长篇', 'short': '短篇', 'analysis': '作品分析'}.get(packet['book'].get('kind'), '类型未确认'))).replace('__KIND_CODE__', _escape(packet['book'].get('kind') or '')).replace('__ROOT__', _escape(packet['book'].get('root') or '目录未提供')).replace('__TITLE__', _escape(packet['book']['title'])).replace('__HASH__', digest).replace('__BUILD__', hashlib.sha256((page + script.partition('\n')[2]).encode()).hexdigest()[:10]).replace('__SCRIPT__', script).encode('utf-8')


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


def _editor_exit_verified(root, record, raw):
    """Prove a recorded POSIX process exited, without changing its lease file."""
    # On Windows os.kill(pid, 0) can terminate a process. Keep automatic
    # recovery unavailable there until a truly read-only process probe exists.
    if os.name != 'posix' or type(record.get('pid')) is not int or not 1 <= record['pid'] <= 2**31 - 1:
        return False

    def pid_missing():
        try:
            os.kill(record['pid'], 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        return False

    if not pid_missing():
        return False
    try:
        import fcntl
        parent = api.safe_path(root, '.story')
        with api._pinned_directory(parent) as directory:
            lock = api._BoundFile(directory, 'workbench-server.lock')
            before = api.publish._bound_stat(lock)
            if before.st_size < 1:
                return False
            with api._bound_reader(lock) as stream:
                if not os.path.samestat(before, os.fstat(stream.fileno())):
                    return False
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    return False
                try:
                    api._verify_bound_directory(directory)
                    if _author_read(root, '.story/workbench-service.json', 8192) != raw:
                        return False
                    if not pid_missing():
                        return False
                    after = api.publish._bound_stat(lock)
                    if (not os.path.samestat(before, after) or before.st_size != after.st_size or
                            before.st_mtime_ns != after.st_mtime_ns):
                        return False
                    api._verify_bound_directory(directory)
                    if _author_read(root, '.story/workbench-service.json', 8192) != raw:
                        return False
                    final = api.publish._bound_stat(lock)
                    return (os.path.samestat(before, final) and before.st_size == final.st_size and
                            before.st_mtime_ns == final.st_mtime_ns)
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    except (api.StoryError, OSError, ValueError):
        return False


def _service_request(root, action='session', saved=False):
    root = Path(root).expanduser().resolve()
    try:
        record_raw = _author_read(root, '.story/workbench-service.json', 8192)
        record = json.loads(record_raw)
    except FileNotFoundError:
        return {'ok': True, 'running': False, 'status': '未登记服务；较早版本的服务须另行核对。'}
    except (ValueError, UnicodeError, RecursionError):
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
        try:
            current_runtime = _runtime_digest(_runtime_sources())
        except OSError as error:
            current_runtime = None
            result['runtime_check_error'] = str(error)
        # Older services only registered the workbench file. They cannot prove
        # which core/extension code is loaded, even if that one file still matches.
        result['outdated'] = current_runtime is None or result.get('runtime_sha256') != current_runtime
        result['url'] = origin + '/' + record['token'] + '/'
        return result
    except (OSError, http.client.HTTPException) as error:
        if _editor_exit_verified(root, record, record_raw):
            return {'ok': True, 'running': False, 'exit_verified': True,
                    'record_sha256': hashlib.sha256(record_raw).hexdigest(),
                    'status': '已确认原登记进程退出且作品锁空闲；旧服务记录保留，打开作品时可重新启动。'}
        return {'ok': True, 'running': None, 'status': '登记服务暂不可达，不能据此判定进程已退出。', 'reason': str(error)}
    except (ValueError, UnicodeError, RecursionError):
        api.fail('workbench_service_mismatch', '服务未返回可核对的实例信息；不会替换未确认的服务。')
    finally:
        connection.close()


def editor_server(root, limit=DEFAULT_LIMIT, library_books=None, material_roots=None, expected_book_id=None):
    if _loaded_runtime_sha256 is None:
        api.fail('workbench_runtime_unreadable', '无法核对本地工作台代码，请检查安装文件后重新启动。',
                 cause=_loaded_runtime_error)
    root = Path(root).expanduser().resolve()
    if expected_book_id is not None:
        _library_entry(root, {'book_id': expected_book_id})
    packet = _editor_packet(root, limit=limit)
    if expected_book_id is not None and packet['book']['id'] != expected_book_id:
        api.fail('workbench_book_changed', '启动时作品身份已改变；请核对书架所选作品目录。', path=str(root))
    token = secrets.token_urlsafe(32)
    route = '/' + token + '/'
    library = _library_roots(root, library_books)
    materials = _material_roots(material_roots)
    identity = {'instance': uuid.uuid4().hex, 'root': str(root), 'book_id': packet['book']['id'], 'pid': os.getpid(),
                'code_sha256': _loaded_workbench_sha256, 'runtime_sha256': _loaded_runtime_sha256,
                'started': _utc_now()}

    def checked_book():
        # An editor belongs to the book it opened, not to a reusable directory
        # name. Recovery must not follow a replacement book into that path.
        _library_entry(root, identity)

    checked_book()

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
                checked_book()
                fresh = _editor_packet(root, limit=limit)
                self.reply(200, _editor_page(fresh, {}, token), 'text/html; charset=utf-8')
            except api.StoryError as error:
                self.reply(409, json.dumps({'message': str(error), 'error': error.code, 'details': error.details}, ensure_ascii=False).encode())
            except (OSError, ValueError) as error:
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
                if action not in ('api/session', 'api/stop'):
                    checked_book()
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
                    result = _editor_catalog(root, payload.get('offset', 0), limit, payload.get('query', ''),
                                             payload.get('focus'), materials, payload.get('context_chapter'))
                elif action == 'api/metrics':
                    result = _editor_metrics(root, payload)
                elif action == 'api/review-task':
                    result = _editor_review_task(root, payload.get('id'), payload.get('sha256'))
                elif action == 'api/writing-task':
                    result = _editor_writing_task(root)
                elif action == 'api/search':
                    result = _editor_search(root, payload.get('query'), payload.get('offset', 0), material_roots=materials)
                elif action == 'api/books':
                    result = {'ok': True, 'books': list(library.values()), 'current': str(root)}
                elif action == 'api/open-book':
                    result = _library_open(library, payload.get('key'), root, self.server.editor_url)
                elif action == 'api/open':
                    document_id = payload.get('id')
                    result = (_linked_material_document(materials, document_id) if isinstance(document_id, str) and document_id.startswith('linked:')
                              else _editor_document(root, document_id))
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
            except (KeyError, TypeError) as error:
                self.reply(409, json.dumps({'message': '此材料含旧版或不完整字段，请核对章计划；原文未修改。',
                                           'error': 'workbench_legacy_fields'}, ensure_ascii=False).encode())

    class EditorServer(HTTPServer):
        def server_close(self):
            try:
                super().server_close()
                if getattr(self, 'lease', None) is not None:
                    try:
                        checked_book()
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
    # Trash and startup share an outside lock so Windows may release handles
    # inside the book immediately before the native recycle operation.
    with api.workbench_books.lifecycle_guard(api, root):
        checked_book()
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
        checked_book()
        api.atomic_write(api.safe_path(root, path), json.dumps({**identity, 'port': server.server_port, 'token': token}, ensure_ascii=False),
                         allowed, api.safe_path(root, '.story/.workbench-backups/' + uuid.uuid4().hex + '/service.json'))
        return server
    except BaseException:
        if server is not None:
            server.server_close()
        else:
            lease.close()
        raise

def _library_state_dir(state_dir=None):
    value = Path(state_dir).expanduser() if state_dir is not None else Path.home() / '.codex/story-workbench'
    # Do not resolve links before the no-follow directory traversal below.
    return Path(os.path.abspath(value))


def _unknown_shelf_progress(note='正式章登记尚未完整核对。'):
    return {'verified': False, 'formal_chapters': None, 'last_chapter': None,
            'next_chapter': None, 'note': note}


def _shelf_progress(book):
    """Read chapter registration numbers in the caller's identity snapshot."""
    try:
        last = book.meta('last_chapter')
        if type(last) is not int or not 0 <= last <= 2**63 - 2:
            return _unknown_shelf_progress('正式章断点格式尚未核对。')
        row = book.db.execute(
            "SELECT count(*) AS total, coalesce(max(chapter),0) AS maximum, "
            "coalesce(sum(CASE WHEN typeof(chapter)!='integer' OR chapter<1 "
            "THEN 1 ELSE 0 END),0) AS invalid FROM chapter_state").fetchone()
        if (row['invalid'] != 0 or type(row['total']) is not int or
                not 0 <= row['total'] <= MAX_CHAPTERS or
                type(row['maximum']) is not int or row['maximum'] != last):
            return _unknown_shelf_progress('正式章登记与本书断点尚未一致核对。')
        return {'verified': True, 'formal_chapters': row['total'], 'last_chapter': last,
                'next_chapter': last + 1,
                'note': '本地正式章登记；候选与计划不计入，不代表完结、平台发布或正文完整性验收。'}
    except (api.StoryError, sqlite3.Error, ValueError, TypeError, OverflowError):
        return _unknown_shelf_progress('旧版或不完整的正式章登记尚未核对。')


def _library_entry(value, expected=None):
    if not isinstance(value, (str, Path)) or not str(value).strip() or len(str(value)) > 4096:
        api.fail('invalid_input', '请填写已初始化作品的本地目录。')
    root = Path(os.path.abspath(Path(value).expanduser()))
    if (root / '.story-restore-incomplete.json').exists():
        api.fail('workbench_restore_incomplete', '此目录的备份恢复尚未完成，请保留文件并核对恢复标记；请改用完整作品或恢复到新目录。', path=str(root))
    try:
        book = _ReadOnlyBook(root)
    except (api.StoryError, OSError) as error:
        if expected is not None:
            api.fail('workbench_book_changed', '无法确认原登记作品的目录或身份，请保留原登记并核对。',
                     path=str(root), cause=str(error))
        raise
    try:
        with book.read_snapshot():
            book_id, title, kind = book.meta('id'), book.meta('title'), book.meta('kind')
            progress = _shelf_progress(book)
        if (not isinstance(book_id, str) or not book_id or len(book_id) > 200 or
                not isinstance(title, str) or kind not in ('long', 'short', 'analysis')):
            api.fail('state_corrupt', '作品身份、书名或类型无效。', path=str(root))
        if expected is not None and expected['book_id'] != book_id:
            api.fail('workbench_book_changed', '此目录的作品身份已改变；保留原登记，请核对原作品目录。', path=str(root))
        book.verify_state_path()
        return {'key': hashlib.sha256(str(root).encode()).hexdigest()[:20],
                'root': str(root), 'book_id': book_id, 'title': title,
                'kind': kind, 'kind_verified': True, 'progress': progress}
    finally:
        book.close()


def _library_load(state_dir):
    try:
        raw = _author_read(state_dir, 'library.json', 256 * 1024)
    except FileNotFoundError:
        return {}, None
    try:
        record = json.loads(raw)
        if not isinstance(record, dict) or record.get('version') != 1 or not isinstance(record.get('books'), list):
            raise ValueError('invalid registry')
        if len(record['books']) > 30:
            raise ValueError('too many books')
        entries = {}
        for entry in record['books']:
            if (not isinstance(entry, dict) or any(not isinstance(entry.get(k), str) for k in ('key', 'root', 'book_id', 'title'))
                    or not entry['book_id'] or len(entry['book_id']) > 200 or len(entry['root']) > 4096
                    or not Path(entry['root']).is_absolute() or str(_library_state_dir(entry['root'])) != entry['root']
                    or entry.get('kind') not in (None, 'long', 'short', 'analysis')
                    or ('shelf_status' in entry and entry['shelf_status'] not in ('serializing', 'completed', 'unknown'))
                    or entry['key'] != hashlib.sha256(entry['root'].encode()).hexdigest()[:20]
                    or entry['key'] in entries):
                raise ValueError('invalid book entry')
            saved = {k: entry[k] for k in ('key', 'root', 'book_id', 'title')}
            saved.update(kind=entry.get('kind'), kind_verified=False)
            if 'shelf_status' in entry:
                saved['shelf_status'] = entry['shelf_status']
            if saved['kind'] is None:
                # Version 1 registries omitted the type. Consult the identified
                # book without rewriting its state or the saved registry.
                try:
                    saved['kind'] = _library_entry(saved['root'], saved)['kind']
                except (api.StoryError, OSError, ValueError):
                    pass
            entries[entry['key']] = saved
        return entries, hashlib.sha256(raw).hexdigest()
    except (ValueError, TypeError, RecursionError, UnicodeError):
        api.fail('workbench_library_invalid', '书架登记文件损坏，请保留原文件后核对。', path=str(state_dir / 'library.json'))


def _library_store(state_dir, entries, expected_hash):
    _, current_hash = _library_load(state_dir)
    if current_hash != expected_hash:
        api.fail('workbench_library_changed', '书架登记已被其他程序修改，请保留文件并重新打开书架。')
    saved = [{k: value for k, value in entry.items() if k not in ('progress', 'writing_status')}
             for entry in entries.values()]
    content = json.dumps({'version': 1, 'books': saved}, ensure_ascii=False, indent=2) + '\n'
    api.atomic_write(api.safe_path(state_dir, 'library.json'), content,
                     {expected_hash} if expected_hash is not None else set(),
                     api.safe_path(state_dir, '.backups/' + uuid.uuid4().hex + '/library.json'))
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def _set_shelf_status(state_dir, entries, key, value, expected_hash):
    if not isinstance(key, str) or key not in entries or value not in ('serializing', 'completed', 'unknown'):
        api.fail('invalid_input', '请选择已登记作品及有效的本地作品状态。')
    original = entries[key]
    current = _library_entry(original['root'], original)
    if current['kind'] == 'analysis':
        api.fail('invalid_input', '作品分析不适用连载或完本标记。')
    entry = dict(original)
    if value == 'unknown':
        entry.pop('shelf_status', None)
    else:
        entry['shelf_status'] = value
    updated = {**entries, key: entry}
    # Confirm the same book immediately before the registry CAS write. No
    # chapter, author material or book state is changed by this operation.
    _library_entry(original['root'], original)
    if updated != entries:
        expected_hash = _library_store(state_dir, updated, expected_hash)
    return updated, expected_hash


def _library_catalog(entries):
    books = []
    for entry in entries.values():
        try:
            current = _library_entry(entry['root'], entry)
            current['classification'] = _book_classification(current['root'])
            status_entry = dict(current)
            if 'shelf_status' in entry:
                status_entry['shelf_status'] = entry['shelf_status']
            current['writing_status'] = _book_writing_status(current['root'], status_entry)
            current.update(_book_update(current['root'], current))
            _library_entry(current['root'], current)
            books.append({**current, 'available': True})
        except (api.StoryError, OSError, ValueError) as error:
            books.append({**entry, 'kind': entry.get('kind'), 'kind_verified': False,
                          'progress': _unknown_shelf_progress('无法核对原作品，正式章登记暂不可用。'),
                          'classification': _classification_unavailable('无法核对原作品，题材暂不可用。'),
                          'writing_status': _unknown_writing_status('无法核对原作品，本地作品状态暂不可用。'),
                          **_unknown_book_update('无法核对原作品，更新时间暂不可用。'),
                          'available': False, 'error': str(error)})
    return {'ok': True, 'books': books, 'current': None}


@contextmanager
def _library_lease(state_dir):
    with api._pinned_directory(state_dir, create=True) as directory:
        path = api.safe_path(state_dir, 'server.lock')
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        fd = (os.open(path.name, flags, 0o600, dir_fd=directory.fd) if directory.fd is not None
              else os.open(path, flags, 0o600))
        with os.fdopen(fd, 'a+b') as handle:
            api._verify_bound_directory(directory)
            bound = api._BoundFile(directory, path.name)
            if not os.path.samestat(os.fstat(handle.fileno()), api.publish._bound_stat(bound)):
                api.fail('workbench_library_changed', '书架锁文件已改变。')
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                api.fail('workbench_running', '此书架已有服务运行，请用 workbench-library-status 查看。')
            try:
                yield directory
            finally:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _library_service_request(state_dir=None, action='session', saved=False):
    root = _library_state_dir(state_dir)
    if action not in ('session', 'stop'):
        api.fail('invalid_input', '无效的书架服务操作。')
    try:
        record = json.loads(_author_read(root, 'service.json', 8192))
    except FileNotFoundError:
        return {'ok': True, 'running': False, 'status': '书架服务尚未启动。'}
    except (ValueError, UnicodeError, RecursionError):
        api.fail('workbench_service_invalid', '书架服务记录损坏，请保留原文件并核对。')
    if (not isinstance(record, dict) or record.get('state_dir') != str(root) or record.get('mode') != 'library'
            or type(record.get('port')) is not int or not 1 <= record['port'] <= 65535
            or not isinstance(record.get('token'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', record['token'])
            or not isinstance(record.get('instance'), str) or not re.fullmatch(r'[0-9a-f]{32}', record['instance'])):
        api.fail('workbench_service_invalid', '书架服务记录无效，不会向未确认的进程发送停止请求。')
    if action == 'stop' and not saved:
        api.fail('workbench_unsaved', '请使用 --saved 确认关闭书架；已打开的作品编辑服务仍会保留。')
    if record.get('stopped') is True:
        return {'ok': True, 'running': False, 'status': '书架服务已停止；各作品编辑服务另行管理。'}
    connection = http.client.HTTPConnection('127.0.0.1', record['port'], timeout=3)
    origin = f"http://127.0.0.1:{record['port']}"
    try:
        connection.request('POST', '/api/' + action, json.dumps({'instance': record['instance'], 'saved': saved}),
                           {'Content-Type': 'application/json', 'Origin': origin, 'X-Story-Token': record['token']})
        response = connection.getresponse()
        raw = response.read(8193)
        if response.status != 200 or len(raw) > 8192:
            api.fail('workbench_service_mismatch', '服务未确认此书架实例；不会按 PID 结束进程。')
        result = json.loads(raw)
        if (not isinstance(result, dict) or result.get('instance') != record['instance']
                or result.get('state_dir') != str(root) or result.get('mode') != 'library'):
            api.fail('workbench_service_mismatch', '书架服务身份不一致，未确认停止。')
        try:
            runtime = _runtime_digest(_runtime_sources())
        except OSError as error:
            runtime = None
            result['runtime_check_error'] = str(error)
        result['outdated'] = runtime is None or result.get('runtime_sha256') != runtime
        result['url'] = origin + '/'
        return result
    except (OSError, http.client.HTTPException, ValueError) as error:
        return {'ok': True, 'running': None, 'status': '书架服务暂不可达，不能据此判断进程已经退出。', 'reason': str(error)}
    finally:
        connection.close()


def _library_management_html():
    return '''<style>
.library-actions{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin:22px 0 24px}
.library-actions>button,.library-actions>details>summary{font-size:13px;min-height:40px}
.library-actions #new-book{padding:8px 20px;font-weight:600}.library-actions #new-book::before{content:"+";font-size:18px;font-weight:400;line-height:1;margin-right:8px}
.library-actions>details{position:relative;margin:0;border:0;border-radius:9px}
html[data-story-workbench] .library-actions>details{background:transparent;border:0;margin:0}
.library-actions>details>summary{padding:8px 13px;border:1px solid var(--line);border-radius:9px;cursor:pointer;list-style:none;width:auto;margin:0;font-weight:400;color:var(--ink)}
.library-actions>details>summary::-webkit-details-marker{display:none}.library-actions>details>summary::after{content:"⌄";margin-left:9px;color:var(--muted)}
.library-actions>details[open]>summary{background:var(--wb-tint);margin:0}
.library-actions .add-body,.shelf-tools-panel{position:absolute;left:0;top:calc(100% + 8px);z-index:25;border:1px solid var(--line);border-radius:12px;background:var(--surface);box-shadow:0 12px 36px #12263b1a;padding:17px}
.library-actions .add-body{width:min(520px,calc(100vw - 72px))}.shelf-tools-panel{min-width:188px;padding:7px;display:grid;gap:3px}
.shelf-tools-panel button{font-size:12px;text-align:left;min-height:36px;border-color:transparent;padding:7px 11px}
.book-management-panel{position:absolute;right:0;top:calc(100% + 7px);z-index:20;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;width:min(310px,calc(100vw - 76px));padding:13px;border:1px solid var(--line);border-radius:12px;background:var(--surface);box-shadow:0 12px 36px #12263b20;text-align:left}
.book:has(.book-management[open]){z-index:10}.book-management-panel>details{grid-column:1 / -1;font-size:12px;color:var(--muted);min-width:0;border-bottom:1px solid var(--line);padding-bottom:8px;margin-bottom:2px}
.book-management-panel>details>summary{cursor:pointer;padding:3px 0;color:var(--ink)}
.book-management-panel .classification-sources{margin-top:0}.book-management-panel .classification-sources p{padding:8px 10px;font-size:11px;line-height:1.75;background:var(--wb-panel);border-radius:6px}
.book-management-panel .book-status-editor button{margin-top:8px}.book-management-panel>button:last-child{color:var(--wb-danger)}
html[data-story-workbench] .book-management-panel>button{margin:0;min-height:34px;padding:6px 5px;font-size:11px;text-align:center;width:100%}
.book-management-panel .path{font-size:11px;line-height:1.8}
.book-management-panel .book-status-editor{padding:0 0 8px;border-top:0}
.book-management-panel .status-note,.book-management-panel .status-source{font-size:11px;line-height:1.8}
.book-management-panel .book-status-editor select{font-size:12px}
dialog{color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:18px;width:min(620px,calc(100vw - 32px));max-height:85vh;padding:28px;overflow:auto;box-shadow:0 24px 80px #10271e33}
dialog::backdrop{background:#101d2299;backdrop-filter:blur(3px)}dialog h2{font-size:21px;line-height:1.45;overflow-wrap:anywhere}
dialog label{display:grid;gap:7px;margin:18px 0;font-size:13px}dialog label.check{display:flex;align-items:flex-start;gap:9px;line-height:1.65}dialog input[type=checkbox]{width:auto;flex-shrink:0;margin-top:4px;accent-color:var(--wb-button)}
dialog select{padding:9px 12px}dialog p{white-space:pre-wrap;overflow-wrap:anywhere;margin:14px 0;font-size:13px;color:var(--muted)}
dialog .dialog-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:24px;padding-top:18px;border-top:1px solid var(--line)}
html[data-story-workbench] dialog .danger{border-color:var(--wb-danger-line);color:var(--wb-danger);background:var(--wb-danger-bg)}
html[data-story-workbench] #manager-error{color:var(--wb-danger);background:var(--wb-danger-bg);border:1px solid var(--wb-danger-line);border-radius:8px;padding:10px 12px}#manager-error:empty{display:none}
.recovery-row{border-top:1px solid var(--line);padding:14px 0}.recovery-row button{max-width:100%;text-align:left;overflow-wrap:anywhere}.recovery-row small{display:block;color:var(--muted);overflow-wrap:anywhere;margin-top:5px}
@media(max-width:680px){.library-actions{gap:7px;margin-bottom:22px}.library-actions #new-book{width:100%;margin:0 0 3px}.library-actions>details{flex:1}.library-actions>details>summary{text-align:center}.library-actions #undo-remove{flex-basis:100%}.library-actions .add-body{width:calc(100vw - 36px);padding:15px}.shelf-tools-panel{left:auto;right:0;min-width:180px}dialog{padding:22px 18px;border-radius:14px}.dialog-actions button{flex:1 1 auto}}
</style><div class="library-actions" role="group" aria-label="作品与恢复"><button id="new-book" class="primary">新建作品</button><details id="add-book-panel" class="shelf-add"><summary>添加已有作品</summary><div class="add-body"><label for="book-path">已有作品的本地目录</label><div class="add-fields"><input id="book-path" placeholder="粘贴作品文件夹的完整路径" autocomplete="off"><button id="add">添加作品</button></div><p class="add-help">只登记所填目录，稿件保留在原位置；书架最多登记 30 部作品。</p></div></details><details class="shelf-tools"><summary>书架工具</summary><div class="shelf-tools-panel"><button id="restore-backup">从备份恢复</button><button id="recovery-center">恢复中心</button><button id="last-books">重新打开上次作品</button></div></details><button id="undo-remove" hidden>撤销移出书架</button></div>
<dialog id="book-manager" aria-labelledby="manager-title"><h2 id="manager-title"></h2><div id="manager-body"></div><p id="manager-error" role="alert"></p><div id="manager-actions" class="dialog-actions"></div></dialog>'''


def _library_management_script():
    return r'''
let undoRemoval=null,managerBusy=false,managerGeneration=0;
const manager=$('book-manager'),managerBody=$('manager-body'),managerActions=$('manager-actions');
function managementText(text){const p=document.createElement('p');p.textContent=text;managerBody.append(p);return p;}
function managementField(label,type='text',options=[]){const row=document.createElement('label');row.textContent=label;const input=document.createElement(type==='select'?'select':'input');if(type==='select'){for(const [value,text]of options){const option=document.createElement('option');option.value=value;option.textContent=text;input.append(option);}}else input.type=type;if(type==='checkbox')row.className='check';input.autocomplete='off';row.append(input);managerBody.append(row);return input;}
function managementButton(label,action,danger=false){const b=document.createElement('button');b.textContent=label;if(danger)b.className='danger';b.onclick=async()=>{if(managerBusy)return;managerBusy=true;$('manager-error').textContent='';for(const button of managerActions.querySelectorAll('button'))button.disabled=true;try{await action();}catch(error){$('manager-error').textContent=error.message;}finally{managerBusy=false;for(const button of managerActions.querySelectorAll('button'))button.disabled=false;}};managerActions.append(b);return b;}
function openManager(title){const generation=++managerGeneration;$('manager-title').textContent=title;managerBody.replaceChildren();managerActions.replaceChildren();$('manager-error').textContent='';if(!manager.open)manager.showModal();managementButton('返回书架',()=>manager.close());return generation;}
manager.addEventListener('cancel',event=>{if(managerBusy)event.preventDefault();});
function managementReceipt(result,message){if(result.books)render(result);manager.close();$('message').textContent=message+(result.warning?'\n'+result.warning:'');if(result.undo_id){undoRemoval=result.undo_id;$('undo-remove').hidden=false;}}
function rememberShelfBook(key){try{const previous=JSON.parse(localStorage.getItem('story-last-books')||'[]');const keys=Array.isArray(previous)?previous.filter(value=>typeof value==='string'&&value!==key):[];localStorage.setItem('story-last-books',JSON.stringify([key,...keys].slice(0,8)));}catch{}}
async function openRecoveryBook(key,id){const r=await call('open-book',{key});rememberShelfBook(key);const a=document.createElement('a');a.href=r.url+(id?'#'+encodeURIComponent(id):'');a.target='_blank';a.rel='noopener';a.textContent='打开作品';a.click();}
async function showRecoveries(book){const generation=openManager(book?'找回文字 · '+bookName(book):'启动恢复中心');managementText('这里列出磁盘上已写入的恢复副本和待核对保存记录。打开后核对文字，再保存为新候选；不会自动替换正式稿。');const result=await call('recoveries',book?{key:book.key}:{});if(!manager.open||generation!==managerGeneration)return;let count=0;for(const group of result.books){if(group.error){managementText(bookName(group)+'：'+group.error);continue;}for(const recovery of group.recoveries||[]){count++;const row=document.createElement('div');row.className='recovery-row';const button=document.createElement('button');button.textContent=bookName(group)+' · '+(recovery.title||recovery.path);button.onclick=async()=>{try{await openRecoveryBook(group.key,recovery.id);}catch(error){$('manager-error').textContent=error.message;}};const detail=document.createElement('small');detail.textContent=recovery.path+(recovery.modified?' · '+recovery.modified:'');row.append(button,detail);managerBody.append(row);}if(group.warnings?.length)managementText(group.warnings.join('\n'));if(group.truncated)managementText('该作品恢复记录较多，列表有截断；请打开作品查看其余记录。');}if(!count)managementText('未发现可列出的恢复副本。尚未写入磁盘的崩溃前输入无法在这里找回。');}
function showCreateBook(){openManager('新建作品');managementText('填写一个尚不存在的新目录。已有目录请用“添加已有作品”。');const path=managementField('作品完整目录'),title=managementField('书名'),kind=managementField('作品类型','select',[['long','长篇'],['short','短篇'],['analysis','作品分析']]);managementButton('创建并加入书架',async()=>{const r=await call('create-book',{path:path.value,title:title.value,kind:kind.value});managementReceipt(r,'作品已创建。'+(r.registered===false?'书架登记未完成，请按提示添加目录。':'可打开作品继续规划。'));});}
function showRestoreBackup(){openManager('从整书备份恢复');managementText('备份先核对文件清单与校验值，再恢复到全新的目录。原作品和原备份都会保留。恢复副本沿用原作品身份，请核对后决定使用哪一份。');const archive=managementField('备份 ZIP 完整路径'),destination=managementField('恢复到的新目录');managementButton('核验并恢复副本',async()=>{const r=await call('restore-backup',{archive:archive.value,destination:destination.value});managementReceipt(r,'备份已核验并恢复：'+r.root+'\n'+(r.warning||r.note||'请打开副本核对稿件。'));});}
async function showBookService(book){const generation=openManager('保存与停止 · '+bookName(book));const state=await call('book-service',{key:book.key});if(!manager.open||generation!==managerGeneration)return;managementText(state.running===true?'本书编辑服务正在运行。请在所有窗口、浏览器标签页中保存候选或下载文字，再停止服务。停止不会替页面保存。':state.running===false?'本书编辑服务已停止或未登记。':'服务暂不可达，不能确认已退出。请核对原工作台。');if(state.running===true){managementText(state.outdated?'运行服务使用较早代码。保存并停止后，再打开作品才能加载当前代码。':'运行服务与当前源码一致。');const saved=managementField('我已核对本书所有页面，编辑均已保存或下载','checkbox');managementButton('停止本书编辑服务',async()=>{if(!saved.checked)throw Error('请先保存并核对所有页面。');const r=await call('stop-book',{key:book.key,saved:true});managementText(r.stop_requested?'已请求正常停止。请稍后刷新书架核对；原页面可保留查看。':'停止未确认，请保留当前页面。');});}}
function showBookBackup(book){openManager('备份作品 · '+bookName(book));managementText('先保存所有页面。备份包含本书已保存的正文、候选、恢复记录及状态，排除运行服务记录、锁和外部关联材料；不会包含尚未保存的输入。检测到文件变化会停止本次备份。');managementText('作品目录：'+book.root);const path=managementField('新备份 ZIP 完整路径（放在作品目录之外）');managementButton('创建并核验备份',async()=>{const r=await call('backup-book',{key:book.key,path:path.value});managementText('备份已生成并核验：'+r.path+'\n文件数：'+r.files+'\nSHA-256：'+r.sha256);});}
function showRelinkBook(book){openManager('重新定位 · '+bookName(book));managementText('选择这部作品迁移后的目录。系统会核对作品身份；不会合并同名作品或搬动文件。');const path=managementField('迁移后的作品完整目录');managementButton('核对并更新位置',async()=>{managementReceipt(await call('relink-book',{key:book.key,path:path.value}),'作品位置已核对并更新。');});}
function showRemoveBook(book){openManager('移出书架 · '+bookName(book));managementText('只移除本书架的登记，作品文件和已打开的编辑页面都会保留。\n目录：'+book.root);managementButton('移出书架',async()=>managementReceipt(await call('remove-book',{key:book.key}),'已移出书架，作品文件保留。可撤销或重新添加。'));}
async function showTrashBook(book){const generation=openManager('删除作品至回收站 · '+bookName(book));const preview=await call('trash-preview',{key:book.key});if(!manager.open||generation!==managerGeneration)return;managementText('将以下作品的整个本地目录移入系统废纸篓／回收站，成功后移出书架。外部关联材料不会删除。可通过系统回收站恢复目录，再添加回书架。');managementText('书名：'+preview.title+'\n目录：'+preview.root);managementText('请先完成备份并停止本书的编辑服务。任何身份、目录、服务或回收站核对失败都会停止操作。');const title=managementField('输入完整书名以确认'),saved=managementField('本书所有页面均已保存或下载，编辑服务已停止','checkbox');managementButton('移入系统回收站',async()=>{if(title.value!==preview.title||!saved.checked)throw Error('请输入完整书名并核对保存状态。');const r=await call('trash-book',{key:book.key,confirmation:preview.confirmation,saved:true});managementReceipt(r,'作品已移入系统回收站。'+(r.shelf_removed===false?'书架登记未移除，请刷新核对。':''));},true);}
function decorateBookManagement(){
 for(const row of $('books').querySelectorAll('.book')){
  if(row.querySelector('.book-management'))continue;
  const key=row.dataset.bookKey,book=shelfBooks.find(book=>book.key===key);if(!book)continue;
  const details=document.createElement('details');details.className='book-management';
  const summary=document.createElement('summary');summary.textContent='管理作品';
  const panel=document.createElement('div');panel.className='book-management-panel';
  const location=Array.from(row.children).find(node=>node.tagName==='DETAILS'&&!node.classList.contains('book-status-editor'));
  for(const detail of [row.querySelector('.classification-sources'),location,row.querySelector('.book-status-editor')])if(detail)panel.append(detail);
  details.append(summary,panel);
  for(const [label,action,requiresBook]of [['备份',showBookBackup,true],['找回文字',showRecoveries,true],['保存与停止',showBookService,true],['重新定位',showRelinkBook,false],['移出书架',showRemoveBook,false],['删除至回收站',showTrashBook,true]]){
   const button=document.createElement('button');button.textContent=label;button.disabled=requiresBook&&!book.available;
   button.onclick=async()=>{const generation=managerGeneration+1;try{await action(book);}catch(error){if(manager.open&&generation===managerGeneration)$('manager-error').textContent=error.message;}};panel.append(button);
  }
  row.append(details);
 }
}
$('new-book').onclick=showCreateBook;$('restore-backup').onclick=showRestoreBackup;$('recovery-center').onclick=async()=>{const generation=managerGeneration+1;try{await showRecoveries();}catch(error){if(manager.open&&generation===managerGeneration)$('manager-error').textContent=error.message;}};
$('undo-remove').onclick=async()=>{try{const r=await call('undo-remove',{receipt_id:undoRemoval});render(r);undoRemoval=null;$('undo-remove').hidden=true;$('message').textContent='已恢复书架登记。';}catch(error){$('message').textContent=error.message;}};
$('last-books').onclick=async()=>{try{const keys=JSON.parse(localStorage.getItem('story-last-books')||'[]');if(!Array.isArray(keys))throw Error('没有上次打开记录。');const books=keys.map(key=>shelfBooks.find(book=>book.key===key&&book.available)).filter(Boolean);if(!books.length)throw Error('没有可重新打开的上次作品。');for(const book of books)await openRecoveryBook(book.key);$('message').textContent='已重新打开 '+books.length+' 部作品。页面恢复时会重新核对作品身份。';}catch(error){$('message').textContent=error.message;}};
'''


def _library_page(token):
    page = r'''<!doctype html><html lang="zh-CN" data-story-workbench data-workbench-view="shelf"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>写作书架</title>
<style>
*{box-sizing:border-box}[hidden]{display:none!important}
body{font:14px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC",system-ui,sans-serif;color:var(--ink);background:var(--paper);margin:0 auto}
button,input,select,summary{font:inherit}button,input,select{border:1px solid var(--line);color:var(--ink)}
button,select{background:var(--surface)}button{padding:7px 13px;cursor:pointer;transition:background .15s,border-color .15s,box-shadow .15s}button:disabled{cursor:default;opacity:.5}
button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible,a:focus-visible{outline:3px solid var(--accent);outline-offset:3px}
input{min-width:0;padding:8px 12px;background:var(--surface);width:100%}a{text-underline-offset:3px}h1,h2,p{margin:0}
.shelf-header,.library-top{display:flex;justify-content:space-between;gap:20px}.shelf-overview{display:flex;align-items:baseline;flex-shrink:0;color:var(--muted)}#total-count{line-height:1;color:var(--ink);font-variant-numeric:tabular-nums}
.shelf-intro,.add-help,.shelf-footer{color:var(--muted)}.shelf-add>summary{cursor:pointer}.add-body label{display:block;font-size:13px;margin-bottom:7px}.add-fields{display:flex;align-items:center}.add-fields input{flex:1}
#message{font-size:13px;line-height:1.8;white-space:pre-wrap;overflow-wrap:anywhere;padding:12px 15px;border:1px solid var(--line);border-radius:10px;margin:0 0 22px}#message:empty{display:none}
.type-filters{display:flex;flex-wrap:wrap}.type-filters button{border-color:transparent;background:transparent}.type-filters [aria-pressed="true"]{font-weight:600}
.shelf-state,.shelf-sort{white-space:nowrap}.shelf-search{position:relative;display:flex;align-items:center;gap:8px}.shelf-search input{padding-right:82px}.search-clear{position:absolute;right:5px;border:0;background:transparent}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}#filter-summary{color:var(--muted);overflow-wrap:anywhere}
#books{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));align-items:stretch}
.book{position:relative;display:grid;background:var(--surface);border:1px solid var(--line);transition:border-color .15s,box-shadow .15s}
.book::before{content:"";position:absolute;left:0}.book strong{grid-row:1;display:-webkit-box;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere;align-self:start}
.book .classification{grid-row:2;min-width:0}.genre-label{color:var(--muted)}.genre-tags{display:flex;flex-wrap:wrap}.genre-tag{overflow-wrap:anywhere}.classification-sources{color:var(--muted)}.classification-sources summary{cursor:pointer}.classification-sources p{white-space:pre-wrap;overflow-wrap:anywhere;margin:7px 0}
.book-progress,.book-status{grid-row:3}.book-status{justify-self:end}.book-updated{grid-row:4;display:block;color:var(--muted);font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.book>details{grid-row:5;color:var(--muted);min-width:0}.book>details>summary{cursor:pointer}.path{overflow-wrap:anywhere;color:var(--muted)}.book>button{grid-row:6}.book>details.book-status-editor{grid-row:7}
.book-status-editor label{display:grid;gap:5px;font-size:12px;margin:8px 0}.book-status-editor select{width:100%;padding:6px 8px}.book-status-editor button{font-size:12px;margin-top:9px;min-height:32px;padding:5px 10px}.status-note,.status-source{color:var(--muted);overflow-wrap:anywhere;white-space:pre-wrap;margin:7px 0}.status-source p{margin-top:5px}
.error{overflow-wrap:anywhere}.search-empty{color:var(--muted)}.search-empty button{margin-top:12px}.shelf-footer{border-top:1px solid var(--line)}
@media(max-width:1100px){#books{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:680px){#books{grid-template-columns:minmax(0,1fr);gap:14px}.add-fields{flex-wrap:wrap}.add-fields input{flex-basis:100%}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
</style><style>__THEME_CSS__</style><style>
/* Shelf structure uses the shared palette in both appearances. */
body{max-width:1336px;padding:36px 40px 24px;font-size:14px;line-height:1.65}
button,input,select{border-radius:9px}button{min-height:38px;letter-spacing:.01em}
input::placeholder{color:var(--muted);opacity:.85}
.shelf-header{padding-bottom:20px;border-bottom:1px solid var(--line);align-items:flex-start}
.shelf-eyebrow{font-size:10px;letter-spacing:.18em;text-transform:uppercase;font-weight:600;margin-bottom:9px;color:var(--accent)}
h1{font-size:32px;font-weight:650;letter-spacing:.035em;line-height:1.25}
.shelf-header-actions{gap:24px;align-items:center;padding-top:5px}
.shelf-overview{border-left:1px solid var(--line);padding-left:24px;gap:7px;font-size:12px}
#total-count{font-size:27px;font-weight:550}
.add-body{padding:0 16px 16px}.add-fields{gap:8px}.add-fields input{min-height:40px}
.add-help{font-size:12px;margin-top:8px;line-height:1.7}
.shelf-library{padding-top:2px}.library-top{margin-bottom:18px;align-items:center}
h2{font-size:18px;letter-spacing:.02em;font-weight:600}
.refresh-button{font-size:12px;min-height:32px;padding:5px 10px;color:var(--muted)}
.shelf-toolbar{display:grid;grid-template-columns:minmax(240px,1fr) auto;align-items:center;gap:16px;padding:16px;border:1px solid var(--line);border-radius:12px;background:var(--surface);margin-bottom:4px}
.shelf-search{grid-column:1;grid-row:1;max-width:none;margin:0;min-width:0}
.shelf-search input{background:var(--wb-panel);min-height:41px;padding-left:14px;border-color:transparent}
html[data-story-workbench] .shelf-search input{background:var(--wb-panel)}
.shelf-controls{display:contents}.type-filters{grid-column:1;grid-row:2;gap:4px}
.type-filters button{font-size:13px;min-height:34px;padding:5px 11px;border-radius:7px}
.shelf-options{grid-column:2;grid-row:1 / span 2;display:flex;align-items:flex-end;gap:12px}
.shelf-state,.shelf-sort{display:grid;align-items:start;gap:5px;font-size:11px;line-height:1.5;color:var(--muted)}
.shelf-state select,.shelf-sort select{font-size:12px;padding:8px 10px;min-height:38px;max-width:180px}
.search-clear{min-height:31px;padding:4px 9px;color:var(--muted);font-size:11px}
#filter-summary{font-size:12px;min-height:20px;margin:13px 2px 14px}
#books{gap:18px}
html[data-story-workbench] #books:not(:has(.book)){min-height:210px;display:flex;align-items:center;justify-content:center;text-align:center;padding:40px 28px;border:1px dashed var(--line);background:var(--surface);line-height:1.9;border-radius:14px;overflow-wrap:anywhere}
.book{min-height:246px;grid-template-columns:minmax(0,1fr) 104px;grid-template-rows:auto auto minmax(24px,1fr) auto auto;gap:9px 10px;padding:19px 20px 18px 23px;border-radius:13px;box-shadow:0 2px 6px #12263b08;overflow:visible}
.book>*{grid-column:1 / -1}
.book::before{top:22px;bottom:auto;height:32px;width:3px;border-radius:0 3px 3px 0}
.book[data-kind="long"]::before{background:var(--accent)}
.book[data-kind="short"]::before{background:var(--wb-selected-ink)}
.book[data-kind="analysis"]::before{background:var(--accent)}
.book strong{font-size:18px;font-weight:600;line-height:1.55;letter-spacing:.01em;-webkit-line-clamp:3;min-width:0;min-height:28px}
html[data-story-workbench] .book:hover{border-color:var(--accent);box-shadow:0 5px 18px #12263b0c}
.book:focus-within{border-color:var(--accent)}
.genre-label{font-size:10px;margin-bottom:5px;letter-spacing:.035em}.genre-tags{gap:5px}
.book>.classification>.genre-label{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.genre-tag{font-size:11px;border-radius:5px;padding:2px 7px;line-height:1.6}
.classification-sources{font-size:11px;margin-top:7px}
.classification-sources summary{width:fit-content;color:var(--muted)}
.classification-sources p{padding:8px 10px;border-radius:6px;background:var(--wb-panel);font-size:11px;line-height:1.75}
.book-progress{grid-column:1;font-size:12px;line-height:1.7;padding-right:0;align-self:center;color:var(--muted)}
.book-status{grid-column:2;font-size:10px;border-radius:5px;padding:3px 8px;align-self:center;line-height:1.6;max-width:104px;overflow-wrap:anywhere}
.book-updated{font-size:11px;line-height:1.65;margin-top:1px}
.book>details{font-size:11px;line-height:1.65}.book>details>summary{padding:2px 0;width:fit-content}
.path{padding:8px 10px;background:var(--wb-panel);border-radius:6px;font-size:11px;line-height:1.8;margin-top:5px}
.book>button{grid-column:1;grid-row:5;align-self:start;justify-self:start;font-size:13px;min-height:38px;margin-top:5px;padding:7px 17px;border-radius:8px;font-weight:500}
html[data-story-workbench] .book>details.book-management{position:relative;grid-column:2;grid-row:5;align-self:start;min-width:0;border:0;padding-top:5px}
html[data-story-workbench] .book-management>summary{width:100%;margin:0;padding:8px 10px;min-height:38px;border:1px solid var(--line);border-radius:8px;font-size:12px;text-align:center;list-style:none;color:var(--ink)}
.book-management>summary::-webkit-details-marker{display:none}.book-management>summary::after{content:"⌄";margin-left:7px;color:var(--muted)}
html[data-story-workbench] .book-management[open]>summary{background:var(--wb-tint);margin-bottom:7px}
html[data-story-workbench] .book-management button{display:block;width:100%;margin:0 0 5px;padding:6px 5px;font-size:11px;min-height:32px;text-align:center}
.book>details.book-status-editor{padding-top:5px;border:0}
.book-status-editor>summary{font-size:11px;color:var(--muted)}
.book-status-editor[open]{padding:8px 0;border-top:1px solid var(--line)}
.book-status-editor select{font-size:12px}.status-note,.status-source{font-size:11px;line-height:1.8}
.error{grid-row:6;padding:9px 11px;background:var(--wb-warning-bg);border-radius:7px;font-size:12px;line-height:1.75}
.search-empty{padding:8px 2px 22px;font-size:12px}.search-empty button{min-height:34px}
.shelf-footer{font-size:11px;line-height:1.9;padding-top:16px;margin-top:30px;max-width:920px}
@media(max-width:1100px){body{padding:30px 28px 22px}.shelf-toolbar{grid-template-columns:minmax(0,1fr);gap:12px}.shelf-search{grid-column:1;grid-row:1}.type-filters{grid-row:2}.shelf-options{grid-column:1;grid-row:3;justify-content:flex-end;align-items:center;gap:16px}.shelf-state,.shelf-sort{display:flex;align-items:center;gap:8px}.shelf-state select,.shelf-sort select{min-height:34px;padding:6px 9px}}
@media(max-width:680px){body{padding:24px 18px 20px}.shelf-header{gap:12px;padding-bottom:18px}h1{font-size:27px}.shelf-eyebrow{font-size:9px;margin-bottom:7px;letter-spacing:.13em}.shelf-header-actions{padding-top:0;gap:12px;align-items:flex-end}.shelf-overview{border:0;padding:0;font-size:11px;gap:5px}#total-count{font-size:23px}.shelf-toolbar{padding:12px;gap:11px}.type-filters{gap:2px}.type-filters button{padding:6px 8px;font-size:12px;min-height:34px}.shelf-options{width:100%;justify-content:space-between;gap:8px}.shelf-state,.shelf-options .shelf-sort{display:grid;gap:5px;min-width:0;flex:1}.shelf-state select,.shelf-options .shelf-sort select{width:100%;max-width:none;font-size:12px;min-width:0}.book{padding:19px 18px 17px 22px;min-height:240px;grid-template-columns:minmax(0,1fr) 104px}.book strong{font-size:18px}.book-progress{padding-right:0}.book>button{min-height:40px;padding:8px 14px}.shelf-footer{margin-top:24px;font-size:11px}.add-fields button{width:100%;min-height:40px}}
</style></head>
<body><header class="shelf-header"><div><p class="shelf-eyebrow">STORY SKILL · 本地创作空间</p><h1>写作书架</h1></div><div class="shelf-header-actions">__THEME_CONTROL__<div class="shelf-overview" aria-label="书架作品总数"><strong id="total-count">0</strong><span>部作品</span></div></div></header><p id="shelf-intro" class="sr-only">管理本地作品，打开后继续写作。</p>
__MANAGEMENT_HTML__<p id="message" role="status"></p>
<main class="shelf-library" aria-labelledby="library-heading"><div class="library-top"><h2 id="library-heading">我的作品</h2><button id="refresh" class="refresh-button">刷新书架</button></div><div class="shelf-toolbar" role="search" aria-label="查找书架作品"><div class="shelf-search"><label class="sr-only" for="shelf-search">搜索当前类型的书名、题材或目录</label><input id="shelf-search" type="search" placeholder="搜索书名、题材或目录" aria-describedby="filter-summary" autocomplete="off"><button id="clear-search" class="search-clear" hidden>清空搜索</button></div><div class="shelf-controls"><div class="type-filters" role="group" aria-label="按作品类型筛选"><button id="filter-all" aria-pressed="true">全部 0</button><button id="filter-long" aria-pressed="false">长篇 0</button><button id="filter-short" aria-pressed="false">短篇 0</button><button id="filter-analysis" aria-pressed="false">作品分析 0</button></div><div class="shelf-options"><label class="shelf-state" for="shelf-status">本地状态<select id="shelf-status" aria-label="按本地作品状态筛选"><option value="all">全部状态 0</option><option value="serializing">连载中 0</option><option value="completed">已完本 0</option><option value="unknown">待确认 0</option></select></label><label class="shelf-sort" for="shelf-order">更新时间<select id="shelf-order" aria-label="按更新时间排序"><option value="newest">最新在前</option><option value="oldest">最早在前</option></select></label></div></div></div><p id="filter-summary" role="status"></p><div id="books"></div><div id="search-empty" class="search-empty" hidden><p>可以清空搜索，继续查看当前类型的作品。</p><button id="empty-clear">清空搜索</button></div></main>
<p id="shelf-footer" class="shelf-footer">打开作品会保留书架和已有编辑页面。关闭书架后，作品编辑服务仍会运行。</p>
<script>__THEME_SCRIPT__const TOKEN=__TOKEN__;const $=id=>document.getElementById(id);let busy=false;
async function call(action,data={}){const r=await fetch('/api/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Story-Token':TOKEN},body:JSON.stringify(data)});const v=await r.json();if(!r.ok)throw Error(v.message||'请求失败');return v;}
__BOOK_DISPLAY__
const shelfKinds=[['all','全部'],['long','长篇'],['short','短篇'],['analysis','作品分析']],shelfStatuses=[['all','全部状态'],['serializing','连载中'],['completed','已完本'],['unknown','待确认']];let shelfBooks=[],shelfFilter='all',shelfSort='newest',shelfQuery='',shelfStatusFilter='all';const statusSaving=new Set();
function shelfBookKind(book){return book.kind_verified!==false&&book.available!==false&&['long','short','analysis'].includes(book.kind)?book.kind:null;}
function shelfWritingState(book){if(book.kind==='analysis')return 'not_applicable';const status=book.writing_status;return book.available!==false&&status?.verified===true&&['serializing','completed'].includes(status.value)?status.value:'unknown';}
function shelfWritingLabel(book){const value=shelfWritingState(book);return value==='not_applicable'?'分析项目':value==='serializing'?'连载中':value==='completed'?'已完本':'状态待确认';}
function shelfStatusNode(book){const node=document.createElement('span');node.className='book-status';node.setAttribute('data-status',shelfWritingState(book));node.textContent=shelfWritingLabel(book);node.title='本地作品状态。'+(typeof book.writing_status?.note==='string'?book.writing_status.note:'不代表平台发布情况。');return node;}
function shelfStatusEditor(book){
 if(book.available===false||book.kind==='analysis'||!['long','short'].includes(shelfBookKind(book)))return null;
 const details=document.createElement('details');details.className='book-status-editor';const summary=document.createElement('summary');summary.textContent='作品状态';const label=document.createElement('label');label.textContent='本地作品标记';const select=document.createElement('select');select.setAttribute('aria-label','设置本地作品状态：'+bookName(book));for(const [value,text]of [['serializing','连载中'],['completed','已完本'],['unknown','按作品记录显示']]){const option=document.createElement('option');option.value=value;option.textContent=text;select.append(option);}const state=shelfWritingState(book),original=book.writing_status?.source==='author_mark'&&['serializing','completed'].includes(state)?state:'unknown';select.value=original;label.append(select);
 const note=document.createElement('p');note.className='status-note';note.textContent='这里只标记本地作品，不代表平台已发布或完本。清除手动标记后，显示作品记录；无明确记录则待确认。';const source=document.createElement('div');source.className='status-source';const origin=document.createElement('p');origin.textContent=book.writing_status?.source==='author_mark'?'书架手动标记':book.writing_status?.source==='recorded'?'作品记录':'尚无明确记录';source.append(origin);if(typeof book.writing_status?.note==='string'){const detail=document.createElement('p');detail.textContent=book.writing_status.note;source.append(detail);}for(const evidence of Array.isArray(book.writing_status?.sources)?book.writing_status.sources:[]){if(!evidence||typeof evidence!=='object')continue;const detail=document.createElement('p');detail.textContent=(typeof evidence.path==='string'?evidence.path:'来源未标注')+(Number.isInteger(evidence.line)&&evidence.line>0?' · 第'+evidence.line+'行':'')+'\n'+(typeof evidence.field==='string'?evidence.field+'：':'')+(typeof evidence.value==='string'?evidence.value:'');source.append(detail);}const save=document.createElement('button');save.textContent='保存状态';select.disabled=save.disabled=statusSaving.has(book.key);
 save.onclick=async()=>{if(statusSaving.has(book.key))return;const value=select.value;if(!['serializing','completed','unknown'].includes(value))return;statusSaving.add(book.key);select.disabled=save.disabled=true;save.textContent='正在保存…';try{const catalog=await call('set-book-status',{key:book.key,value});statusSaving.delete(book.key);render(catalog);const updated=shelfBooks.find(item=>item.key===book.key);$('message').textContent='作品状态已更新（'+bookName(book)+'）：'+(updated?shelfWritingLabel(updated):'请刷新核对')+'。仅表示本地作品状态。';}catch(error){statusSaving.delete(book.key);select.value=original;renderShelf();$('message').textContent='作品状态更新未确认（'+bookName(book)+'）：'+error.message;}finally{statusSaving.delete(book.key);select.disabled=save.disabled=false;save.textContent='保存状态';}};
 details.append(summary,label,note,source,save);return details;
}
function shelfUpdatedTime(book){
 if(book.available===false||book.updated_at_verified!==true||typeof book.updated_at!=='string')return null;
 const match=book.updated_at.match(/^([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:[.]([0-9]{1,9}))?(?:Z|[+]00:00)$/);if(!match)return null;
 const value=Date.parse(book.updated_at);if(!Number.isFinite(value)||new Date(value).toISOString().slice(0,19)!==match[1])return null;
 return {value,key:match[1]+'.'+(match[2]||'').padEnd(9,'0')};
}
function sortShelfBooks(books,order='newest'){
 return books.map((book,index)=>({book,index,time:shelfUpdatedTime(book)})).sort((a,b)=>{
  if(!a.time||!b.time)return a.time?-1:b.time?1:a.index-b.index;
  const comparison=a.time.key<b.time.key?-1:a.time.key>b.time.key?1:0;return (order==='oldest'?comparison:-comparison)||a.index-b.index;
 }).map(entry=>entry.book);
}
function shelfSearchMatch(book,query){
 const terms=query.trim().normalize('NFKC').toLocaleLowerCase().split(/\s+/).filter(Boolean);if(!terms.length)return true;
 const tags=book.classification?.status==='recorded'&&Array.isArray(book.classification.tags)?book.classification.tags.filter(tag=>typeof tag==='string'):[];
 const text=[bookName(book),typeof book.root==='string'?book.root:'',...tags].join(' ').normalize('NFKC').toLocaleLowerCase();return terms.every(term=>text.includes(term));
}
function shelfProgressNode(book){
 const progress=book.progress;if(book.kind==='analysis'||book.available===false||progress?.verified!==true||!Number.isInteger(progress.formal_chapters)||progress.formal_chapters<0)return null;
 const node=document.createElement('p');node.className='book-progress';node.textContent='已登记正式 '+progress.formal_chapters+' 章';return node;
}
function restoreShelfView(){try{const saved=JSON.parse(localStorage.getItem('story-shelf-view')||'null');if(saved&&typeof saved==='object'){if(shelfKinds.some(([kind])=>kind===saved.filter))shelfFilter=saved.filter;if(['newest','oldest'].includes(saved.sort))shelfSort=saved.sort;if(shelfStatuses.some(([value])=>value===saved.status))shelfStatusFilter=saved.status;}}catch(e){}}
function persistShelfView(){try{localStorage.setItem('story-shelf-view',JSON.stringify({filter:shelfFilter,sort:shelfSort,status:shelfStatusFilter}));}catch(e){}}
restoreShelfView();
function renderShelf(){
 const category=shelfFilter==='all'?shelfBooks:shelfBooks.filter(book=>shelfBookKind(book)===shelfFilter),visible=sortShelfBooks(category.filter(book=>(shelfStatusFilter==='all'||shelfWritingState(book)===shelfStatusFilter)&&shelfSearchMatch(book,shelfQuery)),shelfSort);$('shelf-order').value=shelfSort;$('shelf-search').value=shelfQuery;$('clear-search').hidden=!shelfQuery.length;$('search-empty').hidden=true;$('total-count').textContent=String(shelfBooks.length);
 $('shelf-status').replaceChildren();for(const [value,label]of shelfStatuses){const option=document.createElement('option');option.value=value;option.textContent=label+' '+(value==='all'?category.length:category.filter(book=>shelfWritingState(book)===value).length);$('shelf-status').append(option);}$('shelf-status').value=shelfStatusFilter;
 for(const [kind,label]of shelfKinds){const count=kind==='all'?shelfBooks.length:shelfBooks.filter(book=>shelfBookKind(book)===kind).length;const button=$('filter-'+kind);button.textContent=label+' '+count;button.setAttribute('aria-pressed',String(shelfFilter===kind));}
 const label=shelfKinds.find(([kind])=>kind===shelfFilter)[1],pending=shelfBooks.filter(book=>!shelfBookKind(book)).length;
 $('filter-summary').textContent=(shelfFilter==='all'?'全部作品':label)+' · '+visible.length+' 部'+(shelfStatusFilter!=='all'?' · '+shelfStatuses.find(([value])=>value===shelfStatusFilter)[1]:'')+(shelfQuery.trim()?' · 搜索“'+shelfQuery.trim()+'”':'')+(shelfFilter==='all'&&pending?' · '+pending+' 部类型尚未确认':'');
 $('books').replaceChildren();if(!visible.length){$('books').textContent=!shelfBooks.length?'书架还是空的。新建作品，或添加已有作品，开始写作。':shelfQuery.trim()?'当前分类中没有与“'+shelfQuery.trim()+'”匹配的作品。':'当前分类暂无作品；可在“全部”查看其他作品。';$('search-empty').hidden=!shelfQuery.trim();return;}
 for(const b of visible){const row=document.createElement('div');row.className='book';row.dataset.bookKey=b.key;row.setAttribute('data-kind',shelfBookKind(b)||'unknown');const title=document.createElement('strong');title.textContent=b.label;title.title=b.label;const updated=shelfUpdatedTime(b),time=document.createElement(updated?'time':'p');time.className='book-updated';time.textContent=updated?'更新时间：'+new Date(updated.value).toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}):'更新时间未确认';if(updated)time.dateTime=b.updated_at;if(typeof b.updated_at_note==='string')time.title=b.updated_at_note;const genres=classificationNode(b);const path=document.createElement('details');const summary=document.createElement('summary');summary.textContent='目录位置';const directory=document.createElement('div');directory.className='path';directory.textContent=b.root||'目录未提供';path.append(summary,directory);const open=document.createElement('button');open.textContent='打开作品';open.setAttribute('aria-label','打开作品：'+b.label);open.disabled=!b.available;open.onclick=async()=>{open.disabled=true;try{const r=await call('open-book',{key:b.key});rememberShelfBook(b.key);const a=document.createElement('a');a.href=r.url;a.target='_blank';a.rel='noopener';a.textContent='点击打开：'+b.label;$('message').replaceChildren(a);a.click();if(r.outdated)$('message').append(document.createTextNode('（此作品服务使用较早代码，升级前请保留原页面编辑。）'));}catch(e){$('message').textContent='打开失败（'+bookName(b)+'）：'+e.message;}finally{open.disabled=!b.available;}};row.append(title,time,genres,path,open);const progress=shelfProgressNode(b);if(progress)row.append(progress);row.append(shelfStatusNode(b));const statusEditor=shelfStatusEditor(b);if(statusEditor)row.append(statusEditor);if(!b.available){const err=document.createElement('p');err.className='error';err.textContent=b.error||'作品暂不可用，请核对目录。';row.append(err);}$('books').append(row);}
 decorateBookManagement();
}
function render(v){shelfBooks=bookDisplayRows(v.books);renderShelf();}
for(const [kind]of shelfKinds)$('filter-'+kind).onclick=()=>{shelfFilter=kind;persistShelfView();renderShelf();};
$('shelf-order').onchange=()=>{shelfSort=$('shelf-order').value==='oldest'?'oldest':'newest';persistShelfView();renderShelf();};
$('shelf-status').onchange=()=>{shelfStatusFilter=shelfStatuses.some(([value])=>value===$('shelf-status').value)?$('shelf-status').value:'all';persistShelfView();renderShelf();};
$('shelf-search').oninput=()=>{shelfQuery=$('shelf-search').value;renderShelf();};
function clearShelfSearch(){shelfQuery='';renderShelf();$('shelf-search').focus();}
$('clear-search').onclick=clearShelfSearch;$('empty-clear').onclick=clearShelfSearch;
async function refresh(){try{render(await call('books'));}catch(e){$('message').textContent=e.message;}}
$('refresh').onclick=refresh;$('add').onclick=async()=>{if(busy)return;busy=true;$('add').disabled=true;try{const value=await call('add-book',{path:$('book-path').value});render(value);$('book-path').value='';$('message').textContent='作品已登记，重新打开书架仍会保留。';}catch(e){$('message').textContent=e.message;}finally{busy=false;$('add').disabled=false;}};__MANAGEMENT_SCRIPT__refresh();</script></body></html>'''
    return page.replace('__MANAGEMENT_HTML__', _library_management_html()).replace('__MANAGEMENT_SCRIPT__', _library_management_script()).replace('__THEME_CSS__', _workbench_theme_css()).replace('__THEME_CONTROL__', _workbench_theme_control()).replace('__THEME_SCRIPT__', _workbench_theme_script()).replace('__BOOK_DISPLAY__', _book_display_script()).replace('__TOKEN__', json.dumps(token)).encode('utf-8')


def library_server(state_dir=None, port=8765, library_books=None):
    if _loaded_runtime_sha256 is None:
        api.fail('workbench_runtime_unreadable', '无法核对本地工作台代码，请检查安装后重新启动。')
    if type(port) is not int or not 0 <= port <= 65535:
        api.fail('invalid_input', '书架端口必须是 0 至 65535 的整数。')
    root = _library_state_dir(state_dir)
    lease = ExitStack()
    directory = lease.enter_context(_library_lease(root))
    server = None
    try:
        registry, registry_hash = _library_load(root)
        pending = dict(registry)
        for value in library_books or []:
            entry = _library_entry(value)
            if entry['key'] in pending and pending[entry['key']]['book_id'] != entry['book_id']:
                api.fail('workbench_book_changed', '登记目录的作品身份已改变，请先核对原作品。', path=entry['root'])
            if 'shelf_status' in pending.get(entry['key'], {}):
                entry['shelf_status'] = pending[entry['key']]['shelf_status']
            pending[entry['key']] = entry
        if len(pending) > 30:
            api.fail('invalid_input', '工作台书架最多登记30本作品。')
        token = secrets.token_urlsafe(32)
        identity = {'instance': uuid.uuid4().hex, 'state_dir': str(root), 'mode': 'library', 'pid': os.getpid(),
                    'code_sha256': _loaded_workbench_sha256, 'runtime_sha256': _loaded_runtime_sha256, 'started': _utc_now()}

        def checked_registry():
            api._verify_bound_directory(directory)
            _, current = _library_load(root)
            if current != registry_hash:
                api.fail('workbench_library_changed', '书架登记已被其他程序修改，请保留文件并重新打开书架。')

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
                origin = f'http://127.0.0.1:{self.server.server_port}'
                top_navigation = (self.headers.get('Sec-Fetch-Mode') == 'navigate'
                                  and self.headers.get('Sec-Fetch-Dest') == 'document')
                if (not self.allowed() or self.path != '/' or self.headers.get('Origin') not in (None, origin)
                        or (self.headers.get('Sec-Fetch-Site') == 'cross-site' and not top_navigation)):
                    return self.reply(404, b'{}')
                self.reply(200, _library_page(token), 'text/html; charset=utf-8')

            def do_POST(self):
                nonlocal registry, registry_hash
                origin = f'http://127.0.0.1:{self.server.server_port}'
                if (not self.allowed() or self.headers.get('Origin') != origin
                        or self.headers.get('X-Story-Token') != token):
                    return self.reply(403, b'{}')
                if self.headers.get('Content-Type') != 'application/json' or self.headers.get('Transfer-Encoding'):
                    return self.reply(400, b'{}')
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 16384:
                        return self.reply(413, b'{}')
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        return self.reply(400, b'{}')
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        return self.reply(400, b'{}')
                    if self.path in ('/api/session', '/api/stop'):
                        if payload.get('instance') != identity['instance'] or (self.path.endswith('/stop') and payload.get('saved') is not True):
                            return self.reply(409, b'{}')
                        result = {'ok': True, **identity, 'running': True, 'stop_requested': self.path.endswith('/stop')}
                        self.reply(200, json.dumps(result, ensure_ascii=False).encode())
                        if result['stop_requested']:
                            threading.Thread(target=self.server.shutdown, daemon=True).start()
                        return
                    checked_registry()
                    if self.path == '/api/books':
                        result = _library_catalog(registry)
                    elif self.path == '/api/add-book':
                        entry = _library_entry(payload.get('path'))
                        if entry['key'] in registry and registry[entry['key']]['book_id'] != entry['book_id']:
                            api.fail('workbench_book_changed', '此目录已登记另一作品，请核对原作品目录。')
                        if 'shelf_status' in registry.get(entry['key'], {}):
                            entry['shelf_status'] = registry[entry['key']]['shelf_status']
                        updated = {**registry, entry['key']: entry}
                        if len(updated) > 30:
                            api.fail('invalid_input', '工作台书架最多登记30本作品。')
                        if updated != registry:
                            registry_hash = _library_store(root, updated, registry_hash)
                            registry = updated
                        result = _library_catalog(registry)
                    elif self.path == '/api/set-book-status':
                        registry, registry_hash = _set_shelf_status(
                            root, registry, payload.get('key'), payload.get('value'), registry_hash)
                        result = _library_catalog(registry)
                    elif self.path in ('/api/create-book', '/api/remove-book', '/api/undo-remove', '/api/relink-book', '/api/trash-book'):
                        manager = api.workbench_books.BookManager(api.workbench)
                        if self.path == '/api/create-book':
                            values = manager.create(root, registry, registry_hash, payload.get('path'), payload.get('title'), payload.get('kind'))
                        elif self.path == '/api/remove-book':
                            values = manager.remove(root, registry, registry_hash, payload.get('key'))
                        elif self.path == '/api/undo-remove':
                            values = manager.undo_remove(root, registry, registry_hash, payload.get('receipt_id'))
                        elif self.path == '/api/relink-book':
                            values = manager.relink(root, registry, registry_hash, payload.get('key'), payload.get('path'))
                        else:
                            values = manager.trash(root, registry, registry_hash, payload.get('key'), payload.get('confirmation'), payload.get('saved') is True)
                        registry, registry_hash, receipt = values
                        result = {**_library_catalog(registry), **receipt}
                    elif self.path in ('/api/trash-preview', '/api/backup-book', '/api/book-service', '/api/stop-book'):
                        key = payload.get('key')
                        if not isinstance(key, str) or key not in registry:
                            api.fail('invalid_input', '请选择已登记作品。')
                        entry = _library_entry(registry[key]['root'], registry[key])
                        if self.path == '/api/trash-preview':
                            preview = api.workbench_books.BookManager(api.workbench).trash_preview(root, registry, key)
                            result = {**preview, **preview['confirmation']}
                        elif self.path == '/api/backup-book':
                            result = api.workbench_archives.create_archive(entry['root'], payload.get('path'), entry['book_id'])
                        else:
                            action = 'stop' if self.path == '/api/stop-book' else 'session'
                            result = _service_request(entry['root'], action, payload.get('saved') is True)
                            _library_entry(entry['root'], entry)
                            # Service capabilities stay in their on-disk record.
                            result = {k: value for k, value in result.items() if k not in ('url', 'instance', 'pid', 'root')}
                    elif self.path == '/api/restore-backup':
                        if len(registry) >= 30:
                            api.fail('invalid_input', '书架已满，请先移出一部作品。')
                        result = api.workbench_archives.restore_archive(payload.get('archive'), payload.get('destination'))
                        entry = _library_entry(result['root'], {'book_id': result['book_id']})
                        try:
                            updated = {**registry, entry['key']: entry}
                            registry_hash = _library_store(root, updated, registry_hash)
                            registry = updated
                        except (api.StoryError, OSError) as error:
                            result.update(registered=False, warning='副本已恢复，书架登记未完成；请添加已有作品：' + str(error))
                        result = {**_library_catalog(registry), **result}
                    elif self.path == '/api/recoveries':
                        key = payload.get('key')
                        if key is not None and (not isinstance(key, str) or key not in registry):
                            api.fail('invalid_input', '请选择已登记作品。')
                        groups = []
                        for entry in ([registry[key]] if key is not None else registry.values()):
                            group = {k: entry[k] for k in ('key', 'title', 'kind')}
                            try:
                                group.update(api.workbench_archives.recovery_catalog(entry['root'], entry['book_id']))
                            except (api.StoryError, OSError, ValueError) as error:
                                group['error'] = str(error)
                            groups.append(group)
                        result = {'ok': True, 'books': groups}
                    elif self.path == '/api/open-book':
                        key = payload.get('key')
                        if not isinstance(key, str) or key not in registry:
                            api.fail('invalid_input', '作品未登记在本书架中。')
                        entry = _library_entry(registry[key]['root'], registry[key])
                        # Each editor retains its own immutable book root and token.
                        # An unavailable shelf neighbour must not prevent this book opening.
                        result = _library_open({key: entry}, key)
                        _library_entry(entry['root'], entry)
                    else:
                        return self.reply(404, b'{}')
                    self.reply(200, json.dumps(result, ensure_ascii=False).encode())
                except api.StoryError as error:
                    self.reply(409, json.dumps({'message': str(error), 'error': error.code, 'details': error.details}, ensure_ascii=False).encode())
                except (OSError, ValueError, UnicodeError, TypeError, RecursionError) as error:
                    self.reply(400, json.dumps({'message': str(error)}, ensure_ascii=False).encode())

        class LibraryServer(HTTPServer):
            def server_close(self):
                try:
                    super().server_close()
                    if getattr(self, 'registered', False):
                        try:
                            api._verify_bound_directory(directory)
                            raw = _author_read(root, 'service.json', 8192)
                            record = json.loads(raw)
                            if record.get('instance') == identity['instance'] and not record.get('stopped'):
                                record['stopped'] = True
                                api.atomic_write(api.safe_path(root, 'service.json'), json.dumps(record, ensure_ascii=False),
                                                 {hashlib.sha256(raw).hexdigest()},
                                                 api.safe_path(root, '.backups/' + uuid.uuid4().hex + '/service.json'))
                        except (OSError, api.StoryError, ValueError, AttributeError, RecursionError):
                            pass
                        self.registered = False
                finally:
                    lease.close()

        server = LibraryServer(('127.0.0.1', port), Handler, bind_and_activate=False)
        try:
            server.server_bind()
            server.server_activate()
        except OSError as error:
            if error.errno == errno.EADDRINUSE:
                api.fail('workbench_port_in_use', '书架端口已被占用，请核对已有服务；不会自动更换固定地址。', port=port)
            if getattr(error, 'winerror', None) == 10013:
                api.fail('workbench_port_unavailable', '书架端口已被占用或受系统限制，请核对已有服务与端口设置；不会自动更换固定地址。', port=port)
            raise
        server.editor_url = f'http://127.0.0.1:{server.server_port}/'
        server.library_token = token
        if pending != registry or registry_hash is None:
            registry_hash = _library_store(root, pending, registry_hash)
            registry = pending
        try:
            record_hash = {hashlib.sha256(_author_read(root, 'service.json', 8192)).hexdigest()}
        except FileNotFoundError:
            record_hash = set()
        api._verify_bound_directory(directory)
        api.atomic_write(api.safe_path(root, 'service.json'), json.dumps({**identity, 'port': server.server_port, 'token': token}, ensure_ascii=False),
                         record_hash, api.safe_path(root, '.backups/' + uuid.uuid4().hex + '/service.json'))
        server.registered = True
        return server
    except BaseException:
        if server is not None:
            server.server_close()
        else:
            lease.close()
        raise


def run(args):
    if args.command in ('workbench-library-status', 'workbench-library-stop'):
        return _library_service_request(args.state_dir, 'stop' if args.command.endswith('-stop') else 'session',
                                        getattr(args, 'saved', False))
    if args.command == 'workbench-library-serve':
        server = library_server(args.state_dir, args.port, args.library_book)
        print(json.dumps({'url': server.editor_url, 'mode': 'library'}), flush=True)
        if args.open_browser:
            webbrowser.open(server.editor_url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return {'ok': True, 'stopped': True, 'editors_stopped': False}
    if args.command in ('workbench-status', 'workbench-stop'):
        return _service_request(args.book, 'stop' if args.command == 'workbench-stop' else 'session', getattr(args, 'saved', False))
    if args.command == "workbench-serve":
        server = editor_server(args.book, args.limit, args.library_book, args.material_root,
                               getattr(args, 'expected_book_id', None))
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
