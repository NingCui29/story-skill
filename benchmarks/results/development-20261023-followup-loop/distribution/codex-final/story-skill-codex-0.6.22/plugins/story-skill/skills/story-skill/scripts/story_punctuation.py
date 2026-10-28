"""Bounded, non-blocking punctuation observations for Chinese prose.

These are character-form candidates, not grammatical findings. In particular,
this module neither identifies dialogue nor enforces quotation punctuation.
"""
from __future__ import annotations

import re
import unicodedata


MAX_EXAMPLES = 20
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_BACKTICKS = re.compile(r"`+")
_URL = re.compile(r"(?i)(?<![a-z0-9_])(?:[a-z][a-z0-9+.-]*://|www\.)[^\s<>\"“”‘’（）【】《》，。！？；：]+")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.!#$%&'*+/=?^_`{|}~-]+@[\w.-]+\.[a-zA-Z]{2,}(?!\w)")
_TOKEN = re.compile(r"[,:;!?]|\.{2,}|…+|—+|-{2,}")
_EMPHASIS = frozenset("!?？！")
_NOTE = ("仅为标点形态疑点，须结合语境复核；不是确定错误，不影响 lint 的 ok。"
         "不自动替换，也不检查对白句号规则、引号配对或语义。"
         "line、column 从 1 开始，column 按 Unicode 码点计；最多显示前 20 处。")


def _is_han(char):
    return bool(char) and (char == "〇" or unicodedata.name(char, "").startswith(
        ("CJK UNIFIED IDEOGRAPH-", "CJK COMPATIBILITY IDEOGRAPH-")))


def _neighbor(text, index, step):
    while 0 <= index < len(text):
        char = text[index]
        if not char.isspace():
            return char
        index += step
    return ""


def _escaped(text, index):
    slashes = 0
    while index > 0 and text[index - 1] == "\\":
        slashes += 1
        index -= 1
    return slashes % 2 == 1


def _mask_special_text(line):
    """Preserve offsets, and prevent nearby prose from crossing masked spans."""
    chars = list(line)
    runs = list(_BACKTICKS.finditer(line))
    next_same, latest = {}, {}
    for index in range(len(runs) - 1, -1, -1):
        size = len(runs[index][0])
        if size in latest:
            next_same[index] = latest[size]
        latest[size] = index
    index = 0
    while index < len(runs):
        opening = runs[index]
        closing_index = next_same.get(index)
        if closing_index is not None and not _escaped(line, opening.start()):
            end = runs[closing_index].end()
            chars[opening.start():end] = "\0" * (end - opening.start())
            index = closing_index + 1
        else:
            index += 1
    masked = "".join(chars)
    for pattern in (_URL, _EMAIL):
        for match in pattern.finditer(masked):
            chars[match.start():match.end()] = "\0" * (match.end() - match.start())
        masked = "".join(chars)
    return masked


def _candidate_kind(line, match):
    token = match[0]
    left = _neighbor(line, match.start() - 1, -1)
    right = _neighbor(line, match.end(), 1)
    if not (_is_han(left) or _is_han(right)):
        return None
    if token in (",", ";", ":", "?", "!"):
        # Chinese text can contain numeric punctuation and combined emphasis.
        # Do not infer their meaning or normalize legitimate ?! / ？！ runs.
        if token in (",", ":") and left.isdigit() and right.isdigit():
            return None
        if token in _EMPHASIS and (
                match.start() > 0 and line[match.start() - 1] in _EMPHASIS
                or match.end() < len(line) and line[match.end()] in _EMPHASIS):
            return None
        return "halfwidth_punctuation"
    if token.startswith(".") or token.startswith("…") and len(token) % 2:
        return "ellipsis_form"
    if token == "—" or token.startswith("--") and _is_han(left) and _is_han(right):
        return "dash_form"
    return None


def review_warnings(text):
    """Return one bounded warning, or none; never edit the source or reject it."""
    count, examples, fence = 0, [], None
    for line_number, raw in enumerate(text.splitlines(), 1):
        marker = _FENCE.match(raw)
        if fence is not None:
            if (marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1]
                    and not marker[2].strip()):
                fence = None
            continue
        if marker and (marker[1][0] == "~" or "`" not in marker[2]):
            fence = (marker[1][0], len(marker[1]))
            continue
        line = _mask_special_text(raw)
        for match in _TOKEN.finditer(line):
            kind = _candidate_kind(line, match)
            if kind is None:
                continue
            count += 1
            if len(examples) >= MAX_EXAMPLES:
                continue
            excerpt_start = max(0, match.start() - 24)
            example = {"kind": kind, "line": line_number, "column": match.start() + 1,
                       "text": match[0][:24], "length": len(match[0]),
                       "excerpt": raw[excerpt_start:excerpt_start + 96]}
            if kind == "dash_form":
                example["note"] = "若表示破折号，核对是否应为‘——’；连接号、范围及专用符号按语境保留。"
            examples.append(example)
    if not count:
        return []
    return [{"code": "punctuation_review", "count": count,
             "truncated": count > len(examples), "examples": examples, "note": _NOTE}]
