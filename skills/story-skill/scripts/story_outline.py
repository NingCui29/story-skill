"""Explicit, versioned links between adopted readable outlines and chapter plans.

Markdown prose cannot be compared with a structured plan for semantic equality.
Binding records the caller's explicit adoption declaration for one file version;
it does not prove that semantic review occurred. Later changes to either side
must be reviewed and bound again before writing.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import PurePosixPath
import os
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

_POSITION_HEADING = re.compile(r"^(#{1,6})\s*作品定位与平台分类(?:\s*[（(].*[）)])?\s*$")
_FIELD = re.compile(r"(?:^|[；;])\s*(?:[-*+]\s*)?(?:\*\*)?([^：:；;]+?)(?:\*\*)?\s*[：:]\s*([^；;]*)")
_DATE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
_POSITION_FIELDS = ("篇幅类型", "目标平台", "作品阶段", "来源与核对日期", "选择依据", "待核对")
_CLASSIFICATION_FIELDS = {"平台分类", "分类频道", "一级分类", "二级分类", "主分类",
                          "阅读标签", "内容标签", "风格", "角色", "情节", "情绪", "背景"}
_TAG_GROUP_FIELDS = {"阅读标签": {"主分类", "主题", "角色", "情节"},
                     "内容标签": {"情节", "情感", "人设", "世界观"}}


def _position_sections(text):
    """Find actual Markdown sections, ignoring examples in fenced blocks."""
    lines = text.splitlines()
    sections = []
    fence = None
    start = None
    level = None
    visible = []
    for index, line in enumerate(lines):
        match_fence = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match_fence:
            marker = match_fence.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        heading = re.match(r"^(#{1,6})\s+", line)
        if start is not None and heading and len(heading.group(1)) <= level:
            sections.append((start + 1, visible))
            start = None
            level = None
            visible = []
        match = _POSITION_HEADING.match(line)
        if match:
            if start is not None:
                sections.append((start + 1, visible))
            start = index
            level = len(match.group(1))
            visible = [line]
        elif start is not None:
            visible.append(line)
    if start is not None:
        sections.append((start + 1, visible))
    return sections


def _declared_tag_group_headings(lines):
    """Count a tagged subsection only when it contains actual labelled fields."""
    declared = set()
    for index, line in enumerate(lines):
        heading = re.match(r"^(#{2,6})\s+(.+?)\s*$", line)
        if not heading:
            continue
        level = len(heading.group(1))
        for group, expected in _TAG_GROUP_FIELDS.items():
            if not heading.group(2).endswith(group):
                continue
            child_keys = set()
            for child in lines[index + 1:]:
                next_heading = re.match(r"^(#{1,6})\s+", child)
                if next_heading and len(next_heading.group(1)) <= level:
                    break
                for match in _FIELD.finditer(child.lstrip(" -*+\t")):
                    child_keys.update(part.strip() for part in re.split(r"[／/、]", match.group(1)))
            if child_keys & expected:
                declared.add(group)
    return declared


def audit_position(book, relative_path):
    """Read-only structural check; never decides whether platform choices are valid."""
    relative = _relative_path(book, relative_path)
    path = book.safe_path(book.root, relative)
    try:
        if not path.is_file():
            book.fail("outline_missing", "Current whole-book outline is missing", path=str(path))
        resolved = path.resolve(strict=True)
        try:
            resolved.relative_to(book.root)
        except ValueError:
            book.fail("path_escape", "Whole-book outline resolves outside the book", path=str(path))
        stat = path.stat()
        if stat.st_nlink > 1:
            book.fail("invalid_input", "Whole-book outline cannot share an inode with another file",
                      path=str(path))
        if stat.st_size > MAX_OUTLINE_BYTES:
            book.fail("outline_too_large", "Whole-book outline exceeds the file size limit",
                      path=str(path), max_bytes=MAX_OUTLINE_BYTES)
        with path.open("rb") as source:
            opened_stat = os.fstat(source.fileno())
            if (opened_stat.st_dev, opened_stat.st_ino) != (stat.st_dev, stat.st_ino):
                book.fail("outline_changed", "Whole-book outline path changed while opening it", path=str(path))
            raw = source.read(MAX_OUTLINE_BYTES + 1)
        if len(raw) > MAX_OUTLINE_BYTES:
            book.fail("outline_too_large", "Whole-book outline exceeds the file size limit",
                      path=str(path), max_bytes=MAX_OUTLINE_BYTES)
        after = book.safe_path(book.root, relative)
        after_stat = after.stat()
        if (after.resolve(strict=True) != resolved or
                (after_stat.st_dev, after_stat.st_ino) != (stat.st_dev, stat.st_ino)):
            book.fail("outline_changed", "Whole-book outline path changed while it was read", path=str(path))
        text = raw.decode("utf-8-sig")
    except OSError as error:
        book.fail("outline_missing", "Cannot read whole-book outline", path=str(path), error=str(error))
    except UnicodeDecodeError:
        book.fail("invalid_encoding", "Whole-book outline must be UTF-8", path=str(path))
    sections = _position_sections(text)
    issues = []
    if not sections:
        issues.append({"code": "position_section_missing"})
    elif len(sections) != 1:
        issues.append({"code": "position_sections_ambiguous", "count": len(sections)})
    fields = {}
    checked_platform_rules = "none"
    if len(sections) == 1:
        _, lines = sections[0]
        for line in lines:
            for match in _FIELD.finditer(line.lstrip(" -*+\t")):
                key = match.group(1).strip(" *_`\t")
                value = match.group(2).strip(" *_`\t")
                fields.setdefault(key, []).append(value)
        for field in _POSITION_FIELDS:
            if not any(fields.get(field, [])):
                issues.append({"code": "position_field_missing", "field": field})
        if not (any(fields.get("平台入口", [])) or any(fields.get("页面适用性", []))):
            issues.append({"code": "position_field_missing", "field": "平台入口／页面适用性"})
        width = fields.get("篇幅类型", [])
        if width and not any(re.match(r"^(?:长篇|短篇)(?=$|[。.!（(\s])", value) for value in width):
            issues.append({"code": "length_kind_unresolved"})
        source_lines = [line for line in lines if "来源与核对日期" in line]
        source_dates = [candidate for line in source_lines for candidate in _DATE.findall(line)]
        if not any(_valid_date(value) for value in source_dates):
            issues.append({"code": "source_date_missing"})
        declared = {part.strip() for key in fields for part in re.split(r"[／/、]", key)}
        declared.update(_declared_tag_group_headings(lines))
        classification_values = [value for key, values in fields.items()
                                 if any(part.strip() in _CLASSIFICATION_FIELDS
                                        for part in re.split(r"[／/、]", key))
                                 for value in values]
        if not any(token in value for value in classification_values
                   for token in ("拟选", "后台已确认", "待核对")):
            issues.append({"code": "classification_status_missing"})
        if not declared.intersection({"主分类", "一级分类", "阅读标签", "平台分类"}):
            issues.append({"code": "classification_column_missing"})
        platform = " ".join(fields.get("目标平台", []))
        entrance = " ".join(fields.get("平台入口", []) + fields.get("页面适用性", []))
        if "七猫" in platform and "番茄" not in platform:
            checked_platform_rules = "qimao"
            columns = ("分类频道", "一级分类", "二级分类", "风格", "角色", "情节", "背景")
        elif "番茄" in platform and "七猫" not in platform:
            if "短故事" in entrance:
                checked_platform_rules = "fanqie_short"
                columns = ("主分类", "情节", "角色", "情绪", "背景")
            elif "小说" in entrance and "待核对" not in entrance:
                checked_platform_rules = "fanqie_novel"
                columns = ("目标读者", "阅读标签", "内容标签")
            else:
                issues.append({"code": "platform_entrance_unresolved"})
                columns = ()
        else:
            checked_platform_rules = "generic"
            columns = ()
        for column in columns:
            if column not in declared:
                issues.append({"code": "classification_column_missing", "field": column})
    return {"ok": not issues, "scope": "outline_structure_only", "path": relative,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "section_lines": [start for start, _ in sections], "fields_found": sorted(declared if len(sections) == 1 else fields),
            "checked_platform_rules": checked_platform_rules,
            "issues": issues, "manual_review_required": True,
            "note": "Only required field presence and source-date syntax were checked; platform options, per-column status, evidence, and adoption still require human review."}


def _valid_date(value):
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


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
