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
    r"^ {0,3}(?:#{1,6}\s*|[-*+]\s*)?(?:\*\*)?(?:当前)?(?:状态|status)(?:\*\*)?\s*[:：]\s*(.*?)\s*$",
    re.IGNORECASE,
)
_STATUS_TABLE = re.compile(r"^ {0,3}\|\s*(?:当前)?(?:状态|status)\s*\|\s*([^|]*?)\s*\|", re.IGNORECASE)
_FENCE_LINE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_COMMENT_OR_CODE = re.compile(r"<!--|`+")
_INLINE_BLOCK_BREAK = re.compile(r"^ {0,3}(?:>|[-+*][ \t]|[0-9]+[.)][ \t]|(?:[-*_][ \t]*){3,}$|=+[ \t]*$)")
_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+|$)")
_OUTLINE_TITLE = re.compile(
    r"(?:章细纲|章节细纲|全书规划|全书总纲|全书大纲|全本大纲|本卷大纲|分卷大纲|卷纲|近期细纲|短篇规划"
    r"|第[0-9０-９零〇一二三四五六七八九十百千万两]+章[^\r\n]*细纲)"
    r"(?:[ \t]*[（(].*[）)])?\Z"
)
_BODY_TITLE = re.compile(r"(?:第[0-9０-９零〇一二三四五六七八九十百千万两]+[章节卷回]|本章目标|场景)")
_ROLE_SEPARATOR = re.compile(r"[\s_.\-()[\]{}（）【】]+")
_DRAFT_ROLES = {"候选", "候选稿", "候选版", "候选细纲", "草稿", "草稿版", "草稿细纲",
                "draft", "drafts", "candidate", "candidates"}
_ADOPTED_STATUSES = {"已采用", "adopted"}
MAX_OUTLINE_BYTES = 4 * 1024 * 1024

_POSITION_TITLE = re.compile(r"作品定位与平台分类(?:\s*[（(].*[）)])?\Z")
_FIELD = re.compile(r"(?:^|[；;])\s*(?:[-*+]\s*)?(?:\*\*)?([^：:；;]+?)(?:\*\*)?\s*[：:]\s*([^；;]*)")
_DATE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")
_POSITION_FIELDS = ("篇幅类型", "目标平台", "作品阶段", "来源与核对日期", "选择依据", "待核对")
_CLASSIFICATION_FIELDS = {"平台分类", "分类频道", "一级分类", "二级分类", "主分类",
                          "阅读标签", "内容标签", "风格", "角色", "情节", "情绪", "背景"}
_TAG_GROUP_FIELDS = {"阅读标签": {"主分类", "主题", "角色", "情节"},
                     "内容标签": {"情节", "情感", "人设", "世界观"}}
_PLATFORM_ALIASES = {
    "fanqie": re.compile(r"番茄(?:免费小说|小说)?|(?<![a-z])fanqie(?![a-z])", re.IGNORECASE),
    "qimao": re.compile(r"七猫(?:免费小说|中文网|小说)?|(?<![a-z])qimao(?![a-z])", re.IGNORECASE),
}
_PLATFORM_SEPARATOR = re.compile(
    r"[／/、，,|｜;&；+＋]|以及|(?<=\S)[及或与和](?=\S)|\s+(?:and|or)\s+", re.IGNORECASE)
_PLATFORM_STATUS_NOTES = {
    "拟投", "主投", "辅投", "备选", "候选", "待核对", "待确认", "尚未创建", "未创建", "草稿",
    "审核中", "退回", "已发布", "已投稿", "后台已确认",
    "planned", "proposed", "primary", "secondary", "candidate", "draft", "pending", "submitted",
    "under review", "published", "rejected", "not created", "to be confirmed", "pending verification",
}
_PLATFORM_TARGET_DECLARATION = re.compile(
    r"^(?:(?:兼投|另投|也投|拟投|主投|辅投|同时投稿(?:至|到)?|同时投(?:向|往)?)\s*[:：]?\s*"
    r"|(?:also|additionally)\s+(?:submit(?:ting)?|publish(?:ing)?|target(?:ing)?)(?:\s+(?:to|on))?\s*[:：]?\s*)",
    re.IGNORECASE)


def _platform_parts(value):
    """Split declared targets, retaining exact-name boundaries for status notes."""
    parts = []
    for part in _PLATFORM_SEPARATOR.split(value):
        part = part.strip(" 。.!！\t").casefold()
        status = re.sub(r"^(?:状态|投稿状态|status)\s*[:：]\s*", "", part)
        if part and status not in _PLATFORM_STATUS_NOTES:
            parts.append(part)
    return parts


def _declared_platforms(values):
    """Identify each target; a combined target must never select generic rules."""
    platforms = set()
    for value in values:
        label = value
        annotations = []
        while True:
            matches = list(re.finditer(r"[（(]([^（）()]*)[）)]", label))
            if not matches:
                break
            annotations.extend(match.group(1) for match in matches)
            label = re.sub(r"[（(][^（）()]*[）)]", " ", label)
        parts = _platform_parts(label)
        for annotation in annotations:
            annotation_parts = _platform_parts(annotation)
            # Free-form explanations are not platform names. An explicit
            # additional-submission declaration makes its targets relevant,
            # including names outside the two platforms with built-in rules.
            if any(_PLATFORM_TARGET_DECLARATION.match(part) for part in annotation_parts):
                parts.extend(annotation_parts)
            else:
                # A bare known platform name is a target; a platform name
                # mentioned inside an explanatory sentence is not.
                parts.extend(part for part in annotation_parts
                             if any(pattern.fullmatch(part) for pattern in _PLATFORM_ALIASES.values()))
        for part in parts:
            target = _PLATFORM_TARGET_DECLARATION.sub("", part)
            known = [name for name, pattern in _PLATFORM_ALIASES.items() if pattern.search(target)]
            if known:
                platforms.update(known)
            elif target:
                platforms.add(target)
    return sorted(platforms)


def _inline_code_end(lines, index, cursor, marker):
    """Find a paired code span without crossing an outline block boundary."""
    closer = re.compile(r"(?<!`)" + re.escape(marker) + r"(?!`)")
    match = closer.search(lines[index], cursor)
    if match:
        return index, match.start(), match.end()
    if _HEADING.match(lines[index]):
        return None
    for following in range(index + 1, len(lines)):
        line = lines[following]
        if not line.strip() or _HEADING.match(line) or _FENCE_LINE.match(line) or _INLINE_BLOCK_BREAK.match(line):
            break
        match = closer.search(line)
        if match:
            return following, match.start(), match.end()
    return None


def _unfenced_lines(lines):
    """Yield visible outline lines, keeping comments and code examples inert."""
    fence = None
    comment = False
    code_end = None
    quote_paragraph = False
    quote_fence = None
    quote_comment = False
    for index, line in enumerate(lines):
        continuing_code = code_end is not None
        if fence is None and not comment and not continuing_code:
            quoted = re.match(r"^ {0,3}>[ \t]?(.*)$", line)
            if quoted:
                content = quoted.group(1)
                while (nested := re.match(r"^ {0,3}>[ \t]?(.*)$", content)):
                    content = nested.group(1)
                marker = _FENCE_LINE.match(content)
                if quote_fence is not None:
                    if (marker and marker.group(1)[0] == quote_fence[0]
                            and len(marker.group(1)) >= len(quote_fence)
                            and not marker.group(2).strip()):
                        quote_fence = None
                    quote_paragraph = False
                elif quote_comment or content.lstrip().startswith("<!--"):
                    quote_comment = "-->" not in content
                    quote_paragraph = False
                elif marker and (marker.group(1)[0] == "~" or "`" not in marker.group(2)):
                    quote_fence = marker.group(1)
                    quote_paragraph = False
                else:
                    quote_paragraph = bool(content.strip()) and not (
                        _HEADING.match(content) or content.lstrip().startswith("<!--")
                        or (not quote_paragraph and (content.startswith("    ")
                            or re.match(r"^ {0,3}\t", content)))
                        or re.fullmatch(r" {0,3}(?:=+|-+|(?:[_*][ \t]*){3,})[ \t]*", content))
                # Quoted code/comment markers never enter document parser state.
                continue
            if (quote_paragraph and line.strip() and not _HEADING.match(line)
                    and not _FENCE_LINE.match(line) and not _INLINE_BLOCK_BREAK.match(line)
                    and not line.lstrip().startswith("<!--")):
                # Markdown permits unmarked continuation lines in a quoted paragraph.
                continue
            quote_paragraph = False
            quote_fence = None
            quote_comment = False
        if code_end is not None:
            if index < code_end[0]:
                continue
            # Keep the closing backticks, so a declaration later on this same
            # logical line cannot be mistaken for a new document status.
            visible = [" " * code_end[1] + line[code_end[1]:code_end[2]]]
            cursor = code_end[2]
            code_end = None
        else:
            visible = []
            cursor = 0
        match_fence = _FENCE_LINE.match(line)
        if fence is not None:
            if match_fence:
                marker, suffix = match_fence.groups()
                if (marker[0] == fence[0] and len(marker) >= len(fence)
                        and not suffix.strip(" \t")):
                    fence = None
            continue
        if not comment and not continuing_code and match_fence:
            marker, suffix = match_fence.groups()
            # Backticks cannot occur in a backtick fence's info string.
            if marker[0] == "~" or "`" not in suffix:
                fence = marker
                continue
        # An indented code example cannot open a comment that hides later
        # declarations. A real comment may, however, contain indented lines.
        if not comment and not continuing_code and (line.startswith("    ") or re.match(r"^ {0,3}\t", line)):
            continue
        while cursor < len(line):
            if comment:
                end = line.find("-->", cursor)
                if end < 0:
                    break
                cursor = end + 3
                comment = False
                continue
            match = _COMMENT_OR_CODE.search(line, cursor)
            if match is None:
                visible.append(line[cursor:])
                break
            visible.append(line[cursor:match.start()])
            cursor = match.end()
            marker = match.group()
            escape_start = match.start()
            while escape_start and line[escape_start - 1] == "\\":
                escape_start -= 1
            if (match.start() - escape_start) % 2:
                # Only the first character is escaped; any following
                # backticks remain eligible to open their own code span.
                visible.append(marker[0])
                cursor = match.start() + 1
                continue
            if marker == "<!--":
                comment = True
            else:
                closer = _inline_code_end(lines, index, cursor, marker)
                if closer and closer[0] > index:
                    visible.append(marker)
                    code_end = closer
                    break
                end = closer[2] if closer else cursor
                visible.append(line[match.start():end])
                cursor = end
        yield index, "".join(visible)


def _heading_parts(line):
    """Read one ATX heading, including optional indentation and closing hashes."""
    heading = _HEADING.match(line)
    if heading is None:
        return None
    title = re.sub(r"[ \t]+#+[ \t]*$", "", line[heading.end():]).strip()
    return len(heading.group(1)), title


def _position_sections(text):
    """Find actual Markdown sections, ignoring examples in fenced blocks."""
    sections = []
    start = None
    level = None
    visible = []
    lines = text.splitlines()
    if lines and lines[0].rstrip() == "---":
        end = next((index for index, line in enumerate(lines[1:], 1)
                    if line.rstrip() in {"---", "..."}), len(lines) - 1)
        # Metadata is not a rendered section. Preserve original source positions
        # without imposing adoption's restricted YAML grammar on a read-only audit.
        lines[:end + 1] = [""] * (end + 1)
    for index, line in _unfenced_lines(lines):
        heading = _heading_parts(line)
        if start is not None and heading and heading[0] <= level:
            sections.append((start + 1, visible))
            start = None
            level = None
            visible = []
        if heading and _POSITION_TITLE.fullmatch(heading[1]):
            if start is not None:
                sections.append((start + 1, visible))
            start = index
            level = heading[0]
            visible = [line]
        elif start is not None:
            visible.append(line)
    if start is not None:
        sections.append((start + 1, visible))
    return sections


def _field_matches(line):
    """Ignore example fields inside code spans, retaining code-formatted values."""
    spans = []
    cursor = 0
    while (marker := re.search(r"`+", line[cursor:])):
        start = cursor + marker.start()
        cursor += marker.end()
        escape_start = start
        while escape_start and line[escape_start - 1] == "\\":
            escape_start -= 1
        if (start - escape_start) % 2:
            cursor = start + 1
            continue
        closer = _inline_code_end([line], 0, cursor, marker.group())
        if closer:
            spans.append((start, closer[2]))
            cursor = closer[2]
    for match in _FIELD.finditer(line):
        colon = match.end(1) + re.search(r"[:：]", line[match.end(1):match.start(2)]).start()
        if not any(start <= colon < end for start, end in spans):
            yield match


def _declared_tag_group_headings(lines):
    """Count a tagged subsection only when it contains actual labelled fields."""
    declared = set()
    for index, line in enumerate(lines):
        heading = _heading_parts(line)
        if not heading or heading[0] < 2:
            continue
        level, title = heading
        for group, expected in _TAG_GROUP_FIELDS.items():
            if not title.endswith(group):
                continue
            child_keys = set()
            for child in lines[index + 1:]:
                next_heading = _heading_parts(child)
                if next_heading and next_heading[0] <= level:
                    break
                for match in _field_matches(child.lstrip(" -*+\t")):
                    child_keys.update(part.strip() for part in re.split(r"[／/、]", match.group(1)))
            if child_keys & expected:
                declared.add(group)
    return declared


def _fanqie_entrance(values):
    """Resolve only explicit, consistent entrance declarations and scoped notes."""
    entrances = set()
    for value in values:
        # The brand contains “小说” without declaring the novel entrance.
        value = re.sub(r"番茄(?:免费小说|小说)?(?:作家平台)?", "", value)
        parts = [part.strip(" \t\"'“”‘’「」")
                 for part in re.split(r"[，,；;。.!！／/、|｜（）()]+", value)]
        found = False
        for part in filter(None, parts):
            match = re.fullmatch(
                r"(?:(?:拟选|已选|拟用|使用|已使用|新建|创建)\s*[:：]?\s*)?"
                r"(短故事|小说)(?:入口|页面|页)?(?:已选|已确认|拟选)?", part)
            if match:
                entrances.add(match.group(1))
                found = True
            elif re.fullmatch(r"(?:入口\s*[:：]?\s*)?(?:已选|已选择|已确认|拟选)", part):
                continue
            elif (re.match(r"^(?:标签|分类|作品分类|阅读标签|内容标签)(?:[：:]|待|已|未|尚)", part)
                  and not re.search(r"入口|短故事|小说", part)):
                # “短故事（入口已选，标签待核对）” has a known entrance.
                continue
            else:
                # Unknown, uncertain or natural-language alternatives need
                # manual clarification, not a substring-selected rule set.
                return None
        if not found:
            return None
    return next(iter(entrances)) if len(entrances) == 1 else None


def _audit_position_section(start, lines):
    fields = {}
    issues = []
    for line in lines:
        field_line = line.lstrip(" -*+\t")
        matches = list(_FIELD.finditer(field_line))
        declarations = {match.start() for match in _field_matches(field_line)}
        for index, match in enumerate(matches):
            if match.start() not in declarations:
                continue
            key = match.group(1).strip(" *_`\t")
            value = match.group(2).strip(" *_`\t")
            if key in {"目标平台", "来源与核对日期", "平台入口", "页面适用性"}:
                # Keep unlabelled semicolon continuations and parenthetical
                # annotations until the next labelled field outside them.
                # A quoted field example also ends this value; its dates must
                # not be borrowed merely because it cannot declare a new field.
                # This includes “（拟投；兼投：起点）” and source-date notes.
                end = len(field_line)
                depth = 0
                cursor = match.start(2)
                for following in matches[index + 1:]:
                    for char in field_line[cursor:following.start()]:
                        if char in "（(":
                            depth += 1
                        elif char in "）)":
                            depth = max(0, depth - 1)
                    cursor = following.start()
                    if depth == 0:
                        end = cursor
                        break
                value = field_line[match.start(2):end].strip(" *_`\t")
            fields.setdefault(key, []).append(value)
    for field in _POSITION_FIELDS:
        if not any(fields.get(field, [])):
            issues.append({"code": "position_field_missing", "field": field})
    if not (any(fields.get("平台入口", [])) or any(fields.get("页面适用性", []))):
        issues.append({"code": "position_field_missing", "field": "平台入口／页面适用性"})
    width = fields.get("篇幅类型", [])
    if width and not any(re.match(r"^(?:长篇|短篇)(?=$|[。.!（(\s])", value) for value in width):
        issues.append({"code": "length_kind_unresolved"})
    source_dates = [candidate for value in fields.get("来源与核对日期", [])
                    for candidate in _DATE.findall(value)]
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

    platforms = _declared_platforms(fields.get("目标平台", []))
    platform = platforms[0] if len(platforms) == 1 else None
    title = _heading_parts(lines[0])[1]
    heading_platforms = sorted(name for name, pattern in _PLATFORM_ALIASES.items() if pattern.search(title))
    if len(platforms) > 1:
        issues.append({"code": "position_platforms_mixed", "platforms": platforms})
    if heading_platforms and heading_platforms != platforms:
        issues.append({"code": "position_platform_heading_mismatch", "heading_platforms": heading_platforms,
                       "declared_platforms": platforms})

    checked_platform_rules = "generic"
    columns = ()
    if len(platforms) > 1:
        checked_platform_rules = "none"
    elif platform == "qimao":
        checked_platform_rules = "qimao"
        columns = ("分类频道", "一级分类", "二级分类", "风格", "角色", "情节", "背景")
    elif platform == "fanqie":
        entrance = _fanqie_entrance(fields.get("平台入口", []) + fields.get("页面适用性", []))
        if entrance == "短故事":
            checked_platform_rules = "fanqie_short"
            columns = ("主分类", "情节", "角色", "情绪", "背景")
        elif entrance == "小说":
            checked_platform_rules = "fanqie_novel"
            columns = ("目标读者", "阅读标签", "内容标签")
        else:
            checked_platform_rules = "none"
            issues.append({"code": "platform_entrance_unresolved"})
    for column in columns:
        if column not in declared:
            issues.append({"code": "classification_column_missing", "field": column})
    return {"ok": not issues, "platform": platform, "platforms_declared": platforms,
            "section_line": start, "section_title": title, "fields_found": sorted(declared),
            "checked_platform_rules": checked_platform_rules, "issues": issues}


def audit_position(book, relative_path, require_platforms=()):
    """Read-only structural check; never decides whether platform choices are valid."""
    if (not isinstance(require_platforms, (tuple, list)) or
            any(not isinstance(platform, str) or platform not in _PLATFORM_ALIASES
                for platform in require_platforms)):
        book.fail("invalid_input", "Required platforms must be fanqie or qimao")
    required_platforms = list(dict.fromkeys(require_platforms))
    relative = _relative_path(book, relative_path, require_adopted=False)
    path = book.safe_path(book.root, relative)
    try:
        if not path.is_file():
            book.fail("outline_missing", "Whole-book outline is missing", path=str(path))
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
    results = [_audit_position_section(start, lines) for start, lines in sections]
    issues = []
    if not sections:
        issues.append({"code": "position_section_missing"})
    by_platform = {}
    for result in results:
        if result["platform"]:
            by_platform.setdefault(result["platform"], []).append(result)
    for platform, matches in by_platform.items():
        if len(matches) > 1:
            for result in matches:
                result["issues"].append({"code": "position_platform_duplicate", "platform": platform,
                                         "section_lines": [match["section_line"] for match in matches]})
                result["ok"] = False
    # Preserve the legacy issue shape for one block. Multiple blocks need
    # their own location and platform, so missing fields cannot be conflated.
    for result in results:
        for issue in result["issues"]:
            issues.append(dict(issue, section_line=result["section_line"], platform=result["platform"])
                          if len(results) > 1 else dict(issue))
    covered_platforms = sorted(platform for platform, matches in by_platform.items()
                               if len(matches) == 1 and not any(
                                   issue["code"] == "position_platform_heading_mismatch"
                                   for issue in matches[0]["issues"]))
    for platform in required_platforms:
        if platform not in covered_platforms:
            issues.append({"code": "required_platform_missing", "platform": platform})
    return {"ok": not issues, "scope": "outline_structure_only", "path": relative,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "section_lines": [start for start, _ in sections],
            "fields_found": results[0]["fields_found"] if len(results) == 1 else [],
            "checked_platform_rules": (results[0]["checked_platform_rules"] if len(results) == 1
                                       else "multiple" if results else "none"),
            "required_platforms": required_platforms, "covered_platforms": covered_platforms,
            "platform_results": results, "issues": issues, "manual_review_required": True,
            "note": "Only required field presence and source-date syntax were checked per platform block; platform options, per-column status, evidence, and adoption still require human review."}


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


def _relative_path(book, value, *, require_adopted=True):
    """Validate the path; only adoption also restricts its document role."""
    if not isinstance(value, str) or not value or value != value.strip() or "\\" in value:
        book.fail("invalid_input", "outline file must be a book-relative Markdown path")
    if any(unicodedata.category(char).startswith("C") for char in value):
        book.fail("invalid_input", "outline file path contains a control character")
    path = PurePosixPath(value)
    if (path.is_absolute() or not path.parts or value.startswith("//") or
            any(part in ("", ".", "..") for part in value.split("/")) or
            re.match(r"^[a-zA-Z]:", value) or path.suffix.lower() != ".md"):
        book.fail("invalid_input", "outline file must be a book-relative Markdown path")
    for part in path.parts if require_adopted else ():
        name = unicodedata.normalize("NFKC", part).casefold()
        if (name in {".story", "chapters", "正文", "02_正文", "archive", "archives", "history",
                     "99_历史版本", "历史版本"} or
                _DRAFT_ROLES.intersection(_ROLE_SEPARATOR.split(name))):
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


_PLAN_SOURCE = "<!-- story-plan-source/v1 "
_PLAN_BEGIN = "<!-- story-plan-fields:start -->"
_PLAN_END = "<!-- story-plan-fields:end -->"


def _readable_plan_fields(plan):
    """Render every stored field, without inventing scenes or adoption status."""
    lines = []

    def field(label, value):
        lines.append(f"### {label}")
        # Quote each line so field contents cannot forge our standalone markers.
        lines.extend("> " + line for line in str(value).splitlines())
        lines.append("")

    labels = {"title": "章名", "volume_dir": "卷目录", "volume": "卷引用",
              "arc": "阶段引用", "line": "故事线引用", "goal": "本章目标",
              "stop": "停笔点"}
    for key, label in labels.items():
        if key in plan:
            field(label, plan[key])
    for number, beat in enumerate(plan["beats"], 1):
        field(f"情节点 {number}：人物尝试或选择", beat["choice"])
        field(f"情节点 {number}：变化与后果", beat["change"])
    for key, label in (("constraints", "约束"), ("requires", "所需状态引用"),
                       ("tags", "标签"), ("entities", "相关实体引用")):
        if key in plan:
            field(label, "\n".join(plan[key]) if plan[key] else "无")
    field("字数范围", f"{plan['length'][0]}—{plan['length'][1]}")
    field("计数口径", plan.get("count_method", "visible_nonspace_v1"))
    field("章名计数", "计入" if plan.get("count_title", False) else "不计入")
    if "length_exception" in plan:
        exception = plan["length_exception"]
        field("篇幅例外来源", exception["source"])
        if "path" in exception:
            field("篇幅例外文件", exception["path"])
        field("篇幅例外原句", exception["quote"])
    if "time" in plan:
        for key, label in (("clock", "故事时间口径"), ("start", "起始时间"),
                           ("end", "结束时间")):
            value = plan["time"].get(key)
            field(label, "未指定" if value is None else value)
    return "\n".join(lines) + "\n"


def render_plan(book, chapter, plan, check_file=None):
    """Return a candidate, or check generated fields; never write or adopt it."""
    source = {"book_id": book.meta("id"), "chapter": chapter,
              "plan_sha256": _plan_sha256(plan)}
    fields = _readable_plan_fields(plan)
    text = (f"# 第{chapter}章 可读细纲\n\n状态：候选\n\n"
            "以下内容仅由已保存工具章计划生成；生成操作不登记采用，也不证明正文已发生。\n\n"
            + _PLAN_SOURCE + json.dumps(source, ensure_ascii=False, sort_keys=True) + " -->\n"
            + _PLAN_BEGIN + "\n" + fields + _PLAN_END + "\n\n"
            "## 场景补充\n\n可在此补充具体场景；补充内容不自动写回工具计划。\n")
    result = {"schema_version": 1, "read_only": True, **source,
              "revision": book.meta("revision"), "status": "candidate", "text": text,
              "note": "Generated fields only; adoption, supplementary scenes and semantic quality are not verified."}
    if check_file is None:
        return result
    relative = _relative_path(book, check_file, require_adopted=False)
    path = book.safe_path(book.root, relative)
    if not path.is_file():
        book.fail("outline_missing", "Generated candidate is missing", path=relative)
    if path.stat().st_size > MAX_OUTLINE_BYTES:
        book.fail("outline_too_large", "Generated candidate exceeds the file size limit", path=relative)
    with path.open("rb") as stream:
        raw = stream.read(MAX_OUTLINE_BYTES + 1)
    if len(raw) > MAX_OUTLINE_BYTES:
        book.fail("outline_too_large", "Generated candidate exceeds the file size limit", path=relative)
    try:
        lines = raw.decode("utf-8-sig").splitlines()
    except UnicodeError:
        book.fail("invalid_input", "Generated candidate must be UTF-8", path=relative)
    headers = [line for line in lines if line.startswith(_PLAN_SOURCE) and line.endswith(" -->")]
    starts = [i for i, line in enumerate(lines) if line == _PLAN_BEGIN]
    ends = [i for i, line in enumerate(lines) if line == _PLAN_END]
    issues = []
    if len(headers) != 1 or len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        issues.append({"code": "unrecognized_generated_plan"})
    else:
        try:
            recorded = json.loads(headers[0][len(_PLAN_SOURCE):-4])
        except ValueError:
            recorded = None
        if not isinstance(recorded, dict) or set(recorded) != set(source):
            issues.append({"code": "invalid_generated_source"})
        else:
            for key, value in source.items():
                if type(recorded[key]) is not type(value) or recorded[key] != value:
                    issues.append({"code": "generated_source_changed", "field": key})
        actual_fields = "\n".join(lines[starts[0] + 1:ends[0]]) + "\n"
        if actual_fields != fields:
            issues.append({"code": "generated_fields_changed"})
    result.pop("text")
    result.update(status="matches_saved_plan" if not issues else "needs_review",
                  ok=not issues, path=relative, file_sha256=hashlib.sha256(raw).hexdigest(),
                  issues=issues)
    return result


def _frontmatter_header(lines):
    """Separate a small, explicit metadata grammar from Markdown examples.

    Only flat plain values and non-status block scalars are supported. Reject
    other YAML structures rather than reading text inside them as declarations.
    A malformed header cannot acquire adoption from the following Markdown.
    """
    if not lines or lines[0].rstrip() != "---":
        return [], lines
    end = next((index for index, line in enumerate(lines[1:], 1)
                if line.rstrip() in {"---", "..."}), None)
    if end is None:
        return ["unclosed_frontmatter"], []
    statuses = []
    block_scalar = False
    for line in lines[1:end]:
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")):
            if block_scalar and not line.startswith("\t"):
                continue
            return ["unsupported_frontmatter"], []
        match = re.fullmatch(r"([^\s:#\[\]{},&*!|>'\"%@`?]+)[ \t]*:[ \t]*(.*)", line)
        if match is None:
            return ["unsupported_frontmatter"], []
        key, value = match.groups()
        value = re.split(r"(?:^|[ \t]+)#", value, maxsplit=1)[0].strip()
        is_status = key.casefold() in {"状态", "当前状态", "status", "当前status"}
        block_scalar = bool(re.fullmatch(r"[|>](?:[1-9][+-]?|[+-][1-9]?)?", value))
        if block_scalar and not is_status:
            continue
        if (value.startswith(tuple("'\"[]{}&*!|>%@`")) or
                re.match(r"[-?:](?:[ \t]|$)", value) or re.search(r":[ \t]", value)):
            return ["unsupported_frontmatter"], []
        if is_status:
            statuses.append(value.casefold())
    return statuses, lines[end + 1:]


def _document_header_lines(lines):
    """Recognize ATX and Setext headings after removing code and comments."""
    paragraph = []
    previous = -2
    for index, line in _unfenced_lines(lines):
        if index != previous + 1:
            for text in paragraph:
                yield text, None
            paragraph = []
        previous = index
        underline = re.fullmatch(r" {0,3}(=+|-+)[ \t]*", line)
        if underline and paragraph:
            title = " ".join(text.strip() for text in paragraph)
            yield title, (1 if underline.group(1).startswith("=") else 2, title)
            paragraph = []
            continue
        heading = _heading_parts(line)
        if heading or not line.strip() or _INLINE_BLOCK_BREAK.match(line):
            for text in paragraph:
                yield text, None
            paragraph = []
            yield line, heading
        else:
            paragraph.append(line)
    for text in paragraph:
        yield text, None


def _declared_statuses(raw):
    """Read document declarations before body sections, excluding code examples.

    Leading declarations and the first document title share one header. Plain
    chapter/scene headings end a titleless header, so chapter progress cannot
    override document adoption.
    Indented code cannot declare status.
    """
    title_seen = False
    lines = raw.decode("utf-8-sig", errors="replace").splitlines()
    statuses, lines = _frontmatter_header(lines)
    for line, heading in _document_header_lines(lines):
        match = _STATUS_LINE.match(line) or _STATUS_TABLE.match(line)
        if match:
            statuses.append(match.group(1).strip().strip("*_` ").casefold())
            continue
        if heading:
            level, title = heading
            if (not title_seen and (_OUTLINE_TITLE.fullmatch(title) or
                    (level == 1 and not _BODY_TITLE.match(title)))):
                title_seen = True
            else:
                break
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
        raw = bytearray()
        with path.open("rb") as source:
            total = 0
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_OUTLINE_BYTES:
                    book.fail("outline_too_large", "Adopted outline exceeds the file size limit",
                              path=str(path), max_bytes=MAX_OUTLINE_BYTES)
                sha.update(chunk)
                raw.extend(chunk)
        # Detect a path replaced while reading, including an ancestor changed to
        # a link. The reviewed SHA still protects the exact bytes at bind time.
        if book.safe_path(book.root, relative).resolve(strict=True) != resolved:
            book.fail(missing_code, "Adopted outline path changed while it was read", path=str(path))
        statuses = _declared_statuses(raw)
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


def _related_chapters(book, relative):
    """List recorded associations without rewriting or validating other bindings."""
    chapters = []
    for key, value in book.db.execute("SELECT key,value FROM meta WHERE key LIKE ?",
                                      (_KEY_PREFIX + "%",)):
        try:
            record = json.loads(value)
        except (TypeError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        chapter = record.get("chapter")
        if (type(chapter) is int and 1 <= chapter <= 2**63 - 1
                and key == _key(chapter) and record.get("status") == "adopted"
                and record.get("path") == relative):
            chapters.append(chapter)
    return sorted(chapters)


def verify(book, chapter, plan):
    """Read-only gate for context and commit; legacy chapters remain unbound."""
    binding = binding_for(book, chapter)
    if binding is None:
        return {"status": "unbound"}
    plan_sha = _plan_sha256(plan)
    try:
        current_file_sha = _file_sha256(book, binding["path"], "outline_plan_drift")
    except Exception as error:
        if getattr(error, "code", None) == "outline_plan_drift":
            error.details["related_chapters"] = _related_chapters(book, binding["path"])
        raise
    changed = []
    if current_file_sha != binding["sha256"]:
        changed.append("outline")
    if plan_sha != binding["plan_sha256"]:
        changed.append("plan")
    if changed:
        book.fail("outline_plan_drift", "Adopted outline and chapter plan must be reviewed and bound again",
                  chapter=chapter, path=binding["path"], changed=changed,
                  recorded_outline_sha256=binding["sha256"], current_outline_sha256=current_file_sha,
                  recorded_plan_sha256=binding["plan_sha256"], current_plan_sha256=plan_sha,
                  related_chapters=_related_chapters(book, binding["path"]))
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
        related_chapters = _related_chapters(book, relative)
    return {"chapter": chapter, "revision": revision, "outline": record,
            "related_chapters": related_chapters}
