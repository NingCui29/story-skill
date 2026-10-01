"""Explicit, versioned links between adopted readable outlines and chapter plans.

Markdown prose cannot be compared with a structured plan for semantic equality.
Binding records the caller's explicit adoption declaration for one file version;
it does not prove that semantic review occurred. Later changes to either side
must be reviewed and bound again before writing.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
import re
import unicodedata


_HASH = re.compile(r"[0-9a-f]{64}\Z")
_KEY_PREFIX = "outline_binding:"
_STATUS_LINE = re.compile(
    r"^\s*(?:#{1,6}\s*|[-*+]\s*)?(?:\*\*)?(?:当前)?(?:状态|status)(?:\*\*)?\s*[:：]\s*(.*?)\s*$",
    re.IGNORECASE,
)
_STATUS_TABLE = re.compile(r"^\s*\|\s*(?:当前)?(?:状态|status)\s*\|\s*([^|]*?)\s*\|", re.IGNORECASE)
_ADOPTED_STATUSES = {"已采用", "adopted"}
MAX_OUTLINE_BYTES = 4 * 1024 * 1024


def _chapter(book, chapter):
    if type(chapter) is not int or chapter < 1 or chapter > 2**63 - 1:
        book.fail("invalid_input", "outline chapter must be a positive integer")
    return chapter


def _relative_path(book, value):
    if not isinstance(value, str) or not value or value != value.strip() or "\\" in value:
        book.fail("invalid_input", "outline file must be a book-relative Markdown path")
    if any(unicodedata.category(char).startswith("C") for char in value):
        book.fail("invalid_input", "outline file path contains a control character")
    path = PurePosixPath(value)
    if (path.is_absolute() or not path.parts or value.startswith("//") or
            any(part in ("", ".", "..") for part in value.split("/")) or
            re.match(r"^[a-zA-Z]:", value) or path.suffix.lower() != ".md"):
        book.fail("invalid_input", "outline file must be a book-relative Markdown path")
    for part in path.parts:
        name = unicodedata.normalize("NFKC", part).casefold()
        if (name in {".story", "chapters", "正文", "02_正文", "archive", "archives", "history",
                     "99_历史版本", "历史版本"} or
                "候选" in name or "草稿" in name or "draft" in name or "candidate" in name):
            book.fail("invalid_input", "Adopted outline cannot use a draft, candidate, history, or manuscript path",
                      path=value)
    return path.as_posix()


def _sha256(book, value, name):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        book.fail("invalid_input", f"{name} must be a lowercase SHA-256 hex digest")
    return value


def _plan_sha256(plan):
    canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _declared_statuses(prefix):
    """Read explicit statuses in the Markdown header, not words in its prose."""
    statuses = []
    for line in prefix.decode("utf-8-sig", errors="replace").splitlines()[:40]:
        match = _STATUS_LINE.match(line) or _STATUS_TABLE.match(line)
        if match:
            statuses.append(match.group(1).strip().strip("*_` ").casefold())
    return statuses


def _file_sha256(book, relative, missing_code, *, require_adopted=False):
    path = book.safe_path(book.root, relative)
    try:
        if not path.is_file():
            book.fail(missing_code, "Adopted outline file is missing or is not a regular file",
                      path=str(path))
        short_copy = False
        if book.meta("kind") == "short":
            reading_path = book.short_assembly_path()
            managed = book.safe_path(book.root, reading_path)
            short_copy = relative == reading_path or (managed.is_file() and path.samefile(managed))
        if (book.db.execute("SELECT 1 FROM artifact_state WHERE path=?", (relative,)).fetchone() or short_copy):
            book.fail("invalid_input" if missing_code == "outline_missing" else "outline_plan_drift",
                      "Adopted outline path is a managed manuscript or export", path=str(path))
        resolved = path.resolve(strict=True)
        try:
            resolved.relative_to(book.root)
        except ValueError:
            book.fail("path_escape", "Adopted outline resolves outside the book", path=str(path))
        stat = path.stat()
        if stat.st_nlink > 1:
            book.fail("invalid_input" if missing_code == "outline_missing" else "outline_plan_drift",
                      "Adopted outline file cannot share an inode with another file", path=str(path))
        if stat.st_size > MAX_OUTLINE_BYTES:
            book.fail("outline_too_large", "Adopted outline exceeds the file size limit",
                      path=str(path), max_bytes=MAX_OUTLINE_BYTES)
        sha = hashlib.sha256()
        header = bytearray()
        with path.open("rb") as source:
            total = 0
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_OUTLINE_BYTES:
                    book.fail("outline_too_large", "Adopted outline exceeds the file size limit",
                              path=str(path), max_bytes=MAX_OUTLINE_BYTES)
                sha.update(chunk)
                if len(header) < 65536:
                    header.extend(chunk[:65536 - len(header)])
        # Detect a path replaced while reading, including an ancestor changed to
        # a link. The reviewed SHA still protects the exact bytes at bind time.
        if book.safe_path(book.root, relative).resolve(strict=True) != resolved:
            book.fail(missing_code, "Adopted outline path changed while it was read", path=str(path))
        statuses = _declared_statuses(header)
        if any(status not in _ADOPTED_STATUSES for status in statuses):
            book.fail("invalid_input" if require_adopted else "outline_plan_drift",
                      "Outline document header declares a status other than adopted",
                      path=str(path), declared_statuses=statuses)
        if require_adopted and not statuses:
            book.fail("invalid_input", "Outline document header must declare 状态：已采用",
                      path=str(path))
        return sha.hexdigest()
    except OSError as error:
        book.fail(missing_code, "Cannot read the adopted outline file", path=str(path), error=str(error))


def _key(chapter):
    return f"{_KEY_PREFIX}{chapter}"


def binding_for(book, chapter):
    """Return an explicit binding, or None for an untouched legacy chapter."""
    _chapter(book, chapter)
    row = book.db.execute("SELECT value FROM meta WHERE key=?", (_key(chapter),)).fetchone()
    if row is None:
        return None
    try:
        binding = json.loads(row[0])
    except (TypeError, ValueError):
        book.fail("outline_binding_corrupt", "Stored outline binding is not valid JSON", chapter=chapter)
    if (not isinstance(binding, dict) or binding.get("status") != "adopted" or
            binding.get("chapter") != chapter):
        book.fail("outline_binding_corrupt", "Stored outline binding has invalid identity", chapter=chapter)
    try:
        relative = _relative_path(book, binding.get("path"))
        file_sha = _sha256(book, binding.get("sha256"), "outline sha256")
        plan_sha = _sha256(book, binding.get("plan_sha256"), "plan sha256")
    except Exception as error:
        # A malformed stored record is state corruption, not a new bad user input.
        if getattr(error, "code", None) == "invalid_input":
            book.fail("outline_binding_corrupt", "Stored outline binding has invalid fields", chapter=chapter)
        raise
    return {"status": "adopted", "chapter": chapter, "path": relative,
            "sha256": file_sha, "plan_sha256": plan_sha}


def verify(book, chapter, plan):
    """Read-only gate for context and commit; legacy chapters remain unbound."""
    binding = binding_for(book, chapter)
    if binding is None:
        return {"status": "unbound"}
    plan_sha = _plan_sha256(plan)
    current_file_sha = _file_sha256(book, binding["path"], "outline_plan_drift")
    changed = []
    if current_file_sha != binding["sha256"]:
        changed.append("outline")
    if plan_sha != binding["plan_sha256"]:
        changed.append("plan")
    if changed:
        book.fail("outline_plan_drift", "Adopted outline and chapter plan must be reviewed and bound again",
                  chapter=chapter, path=binding["path"], changed=changed,
                  recorded_outline_sha256=binding["sha256"], current_outline_sha256=current_file_sha,
                  recorded_plan_sha256=binding["plan_sha256"], current_plan_sha256=plan_sha)
    return {"status": "adopted", "path": binding["path"],
            "sha256": binding["sha256"], "plan_sha256": binding["plan_sha256"]}


def bind(book, chapter, relative_path, expected_revision, expected_sha256):
    """Record an explicit adoption declaration for one Markdown and plan version.

    The caller must review both sides. The SHA guards against selecting a
    different file version, but cannot prove that review actually occurred.
    """
    _chapter(book, chapter)
    if type(expected_revision) is not int or expected_revision < 0 or expected_revision > 2**63 - 1:
        book.fail("invalid_input", "outline binding requires the current revision")
    relative = _relative_path(book, relative_path)
    expected_file_sha = _sha256(book, expected_sha256, "outline sha256")
    with book.transaction(expected_revision):
        plan = book.get_plan(chapter)
        current_file_sha = _file_sha256(book, relative, "outline_missing", require_adopted=True)
        if current_file_sha != expected_file_sha:
            book.fail("stale_outline", "Outline changed since its reviewed SHA-256 was recorded",
                      chapter=chapter, path=relative, expected=expected_file_sha, actual=current_file_sha)
        record = {"status": "adopted", "chapter": chapter, "path": relative,
                  "sha256": current_file_sha, "plan_sha256": _plan_sha256(plan)}
        previous = binding_for(book, chapter)
        if previous != record:
            book.set_meta(_key(chapter), record)
            book.event("outline_bind", {"chapter": chapter, "before": previous, "after": record})
        revision = book.meta("revision")
    return {"chapter": chapter, "revision": revision, "outline": record}
