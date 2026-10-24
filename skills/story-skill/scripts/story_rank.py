"""Bounded, evidence-preserving rank samples. No book state or third-party packages."""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_ENTRIES = 5000
MAX_SNAPSHOTS = 32
MAX_OBSERVATIONS = 20000
MAX_SAMPLE_BOOKS = 20
MAX_TAG_ROWS = 50
MAX_TITLE_ROWS = 30
MAX_DISTINCT_TAGS = 50000

TITLE_METHOD = ("按 NFKC 规范化后的标题，统计首尾连续文字的 2、4、6 字片段，以及冒号、逗号、问号结构；"
                "只保留至少 2 本书重复的候选，同一边界且命中作品完全相同的片段保留最长项。"
                "每书每项只计一次，最多展示 30 项；不是中文分词、情节识别或流行原因证明。")
SAMPLE_METHOD = ("仅从一个可比分组内去重且无冲突的作品中筛选；优先补充未覆盖分类，再补充本组重复标签，"
                 "再补充字数区间，最后按页面名次与作品键确定次序。重复标签按筛选前本组全部有效作品计票，至少出现于 2 本书，不受报告展示项数限制。"
                 "这是供进一步阅读的覆盖抽样，不是随机抽样、质量排名、市场代表性证明或爆款预测。"
                 "嵌入快照和来源用于重算与核对，不能证明源网页真实或记录完整。")
# These descriptions are part of historical sample identities. Keep them frozen;
# an algorithm change gets a new version, never a rewrite of existing evidence.
LEGACY_SAMPLE_METHOD = ("仅从一个可比分组内去重且无冲突的作品中筛选；优先补充未覆盖分类，再补充本组重复标签，"
                        "再补充字数区间，最后按页面名次与作品键确定次序。重复标签限本组频次前 50 项且至少出现于 2 本书。"
                        "这是供进一步阅读的覆盖抽样，不是随机抽样、质量排名、市场代表性证明或爆款预测。"
                        "嵌入快照和来源用于重算与核对，不能证明源网页真实或记录完整。")
SAMPLE_METHODS = {1: LEGACY_SAMPLE_METHOD, 2: SAMPLE_METHOD}
SAMPLE_SCHEMA_VERSION = 2
SAMPLE_ALGORITHM_VERSION = 2


class RankError(ValueError):
    """Invalid research data or an unsafe output destination."""


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _key(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()[:20]


def _fields(value, required, name, optional=()):
    if not isinstance(value, dict):
        raise RankError(f"{name} 必须是对象")
    missing = set(required) - value.keys()
    extra = value.keys() - set(required) - set(optional)
    if missing or extra:
        raise RankError(f"{name} 字段不匹配：缺少 {sorted(missing)}；未知 {sorted(extra)}")


def _text(value, name, nullable=False, maximum=1000):
    if value is None and nullable:
        return None
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(char) < 32 or unicodedata.category(char) == "Cs" for char in value)):
        raise RankError(f"{name} 必须是非空文本（最多 {maximum} 字符）" + ("或 null" if nullable else ""))
    return value.strip()


def _integer(value, name, minimum=0, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not int or not minimum <= value <= 10**15:
        raise RankError(f"{name} 必须是 {minimum} 至 10^15 的整数" + ("或 null" if nullable else ""))
    return value


def normalize_url(value):
    value = _text(value, "URL", maximum=2000)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise RankError("URL 格式无效") from exc
    if (parsed.scheme.lower() not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password or any(char.isspace() for char in value)):
        raise RankError("URL 必须是无登录凭据的绝对 HTTP(S) 地址")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if port is not None and (parsed.scheme.lower(), port) not in (("http", 80), ("https", 443)):
        host += f":{port}"
    query = [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in ("gclid", "fbclid")]
    return urlunsplit((parsed.scheme.lower(), host, parsed.path.rstrip("/") or "/",
                       urlencode(sorted(query)), ""))


def _texts(value, name, maximum=100):
    if not isinstance(value, list) or len(value) > maximum:
        raise RankError(f"{name} 必须是最多 {maximum} 项的文本数组")
    return sorted(set(_text(item, name) for item in value))


def validate_snapshot(snapshot):
    """Return a normalized copy; missing and unknown values are never zero-filled."""
    try:
        data = deepcopy(snapshot)
    except RecursionError as exc:
        raise RankError("快照嵌套层级过深") from exc
    _fields(data, ("schema_version", "platform", "collected_at", "source", "entries"), "snapshot")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise RankError("schema_version 必须为整数 1")
    data["platform"] = _text(data["platform"], "platform", maximum=80)
    collected = _text(data["collected_at"], "collected_at", maximum=64)
    try:
        date = datetime.fromisoformat(collected.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RankError("collected_at 必须是带时区的 ISO 日期时间") from exc
    if date.utcoffset() is None or "T" not in collected:
        raise RankError("collected_at 必须是带时区的 ISO 日期时间")
    data["collected_at"] = date.isoformat()
    source = data["source"]
    _fields(source, ("url", "board", "channel", "category", "period", "metric", "sample_method",
                     "pages", "coverage", "expected_count", "limitations"), "source", ("cohort", "method"))
    source["url"] = normalize_url(source["url"])
    for field in ("board", "sample_method"):
        source[field] = _text(source[field], "source." + field)
    for field in ("channel", "category", "period"):
        source[field] = _text(source[field], "source." + field, nullable=True)
    source["cohort"] = source.get("cohort", "unknown")
    if source["cohort"] not in ("new", "mature", "mixed", "unknown"):
        raise RankError("source.cohort 必须为 new/mature/mixed/unknown")
    if "method" in source and source["method"] not in ("public_http_get", "browser_html_import", "manual"):
        raise RankError("source.method 必须为 public_http_get/browser_html_import/manual")
    if source["coverage"] not in ("partial", "complete", "unknown"):
        raise RankError("source.coverage 必须为 partial/complete/unknown")
    source["expected_count"] = _integer(source["expected_count"], "source.expected_count", nullable=True)
    source["limitations"] = _texts(source["limitations"], "source.limitations")
    pages = source["pages"]
    if not isinstance(pages, list) or len(pages) > 1000:
        raise RankError("source.pages 必须是最多 1000 项的页码数组")
    source["pages"] = sorted(set(_integer(page, "source.pages", minimum=1) for page in pages))
    if source["pages"] and source["pages"][-1] > 1000:
        raise RankError("source.pages 页码不能超过 1000")
    metric = source["metric"]
    if metric is not None:
        _fields(metric, ("name", "unit", "window"), "source.metric")
        metric["name"] = _text(metric["name"], "source.metric.name")
        metric["unit"] = _text(metric["unit"], "source.metric.unit")
        metric["window"] = _text(metric["window"], "source.metric.window", nullable=True)
    entries = data["entries"]
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise RankError(f"entries 必须是最多 {MAX_ENTRIES} 项的数组")
    for index, entry in enumerate(entries):
        name = f"entries[{index}]"
        _fields(entry, ("title", "author", "rank", "category", "tags", "word_count", "status",
                        "metric_value", "metric_raw"), name, ("book_id", "book_url"))
        entry["book_id"] = _text(entry.get("book_id"), name + ".book_id", nullable=True, maximum=200)
        entry["book_url"] = normalize_url(entry["book_url"]) if entry.get("book_url") is not None else None
        if entry["book_id"] is None and entry["book_url"] is None:
            raise RankError(f"{name} 至少提供 book_id 或 book_url，不能仅按书名识别")
        entry["title"] = _text(entry["title"], name + ".title")
        for field in ("author", "category", "status", "metric_raw"):
            entry[field] = _text(entry[field], name + "." + field, nullable=True)
        for field in ("title", "author"):
            if entry[field] is not None and any(unicodedata.category(char) == "Co" for char in entry[field]):
                raise RankError(f"{name}.{field} 包含私用区字符，疑似字体混淆；请核实可见原文")
        entry["rank"] = _integer(entry["rank"], name + ".rank", minimum=1)
        entry["word_count"] = _integer(entry["word_count"], name + ".word_count", nullable=True)
        entry["tags"] = _texts(entry["tags"], name + ".tags")
        number = entry["metric_value"]
        if number is not None and (type(number) not in (int, float) or not 0 <= number <= 10**18
                                   or not math.isfinite(number)):
            raise RankError(f"{name}.metric_value 必须是有限非负数或 null")
        if metric is None and number is not None:
            raise RankError(f"{name} 有指标值但 source.metric 为 null")
    # Sorting preserves duplicate observations for the aggregation audit, without depending on file order.
    data["entries"] = sorted(entries, key=_json)
    return data


def template():
    return {"schema_version": 1, "platform": "example", "collected_at": "2026-10-22T12:00:00+08:00",
            "source": {"url": "https://example.invalid/ranking", "board": "示例榜（请替换）",
                       "channel": None, "category": None, "period": None, "metric": None,
                       "cohort": "unknown", "sample_method": "示例数据，请替换为真实页面采样",
                       "pages": [1], "coverage": "partial", "expected_count": None,
                       "limitations": ["仅演示格式，不可用于市场判断；未展示指标时保留 null"]},
            "entries": [{"book_id": "example-1", "book_url": "https://example.invalid/book/example-1",
                         "title": "示例作品（请替换）", "author": None, "rank": 1, "category": None,
                         "tags": [], "word_count": None, "status": None,
                         "metric_value": None, "metric_raw": None}]}


def _group_dimensions(snapshot):
    source = snapshot["source"]
    return {"platform": snapshot["platform"], "date": snapshot["collected_at"][:10],
            **{key: source[key] for key in ("url", "board", "channel", "category", "period", "metric", "cohort", "sample_method")}}


def _word_band(value):
    if value is None:
        return "字数未知"
    if value < 100000:
        return "不足 10 万字"
    if value < 300000:
        return "10 万至不足 30 万字"
    if value < 1000000:
        return "30 万至不足 100 万字"
    return "100 万字及以上"


def _title_candidates(title):
    title = unicodedata.normalize("NFKC", title)
    candidates = set()
    start, end = re.match(r"[^\W_]+", title), re.search(r"[^\W_]+$", title)
    for kind, match in (("prefix", start), ("suffix", end)):
        if match:
            text = match.group()
            for width in (2, 4, 6):
                if len(text) >= width:
                    candidates.add((kind, text[:width] if kind == "prefix" else text[-width:]))
    for marker, label in ((":", "含冒号"), (",", "含逗号"), ("?", "含问号")):
        if marker in title:
            candidates.add(("structure", label))
    return candidates


def _pattern_analysis(accepted):
    """Count one vote per accepted identity; bounded literal title features avoid n-gram growth."""
    tags, titles, bands = defaultdict(list), defaultdict(list), defaultdict(list)
    tagged_n = 0
    words = []
    for book_key, entry in accepted:
        if entry["tags"]:
            tagged_n += 1
        for tag in entry["tags"]:
            if tag not in tags and len(tags) >= MAX_DISTINCT_TAGS:
                raise RankError(f"单组不同标签超过 {MAX_DISTINCT_TAGS} 项，请缩小采样范围或核实标签字段")
            tags[tag].append(book_key)
        for candidate in _title_candidates(entry["title"]):
            titles[candidate].append(book_key)
        word_count = entry["word_count"]
        if word_count is not None:
            words.append(word_count)
        bands[_word_band(word_count)].append(book_key)
    count = len(accepted)
    tag_rows = [{"tag": tag, "book_count": len(keys), "sample_share": len(keys) / count,
                 "book_keys": sorted(keys)} for tag, keys in tags.items()]
    tag_rows.sort(key=lambda row: (-row["book_count"], row["tag"]))
    # Suppress shorter boundary fragments only when their complete evidence sets coincide.
    equivalent = {}
    for (kind, pattern), keys in sorted(titles.items()):
        if len(keys) < 2:
            continue
        key = (kind, tuple(sorted(keys))) if kind != "structure" else (kind, pattern)
        previous = equivalent.get(key)
        if previous is None or len(pattern) > len(previous["pattern"]):
            equivalent[key] = {"kind": kind, "pattern": pattern, "book_count": len(keys),
                               "sample_share": len(keys) / count, "book_keys": sorted(keys)}
    title_rows = sorted(equivalent.values(), key=lambda row: (-row["book_count"], row["kind"], row["pattern"]))
    return {
        "tags": {"method": "按去重且无冲突作品计票；占比分母为全部有效样本。空标签数组表示未获得标签，不推断为作品没有题材标签。最多展示频次前 50 项。",
                 "tagged_n": tagged_n, "missing_n": count - tagged_n,
                 "distinct_n": len(tag_rows), "truncated_n": max(0, len(tag_rows) - MAX_TAG_ROWS),
                 "rows": tag_rows[:MAX_TAG_ROWS]},
        "title_patterns": {"method": TITLE_METHOD, "candidate_n": len(title_rows),
                           "truncated_n": max(0, len(title_rows) - MAX_TITLE_ROWS),
                           "rows": title_rows[:MAX_TITLE_ROWS]},
        "word_counts": {"valid_n": len(words), "missing_n": count - len(words),
                        "zero_n": words.count(0), "min": min(words) if words else None,
                        "max": max(words) if words else None,
                        "median": statistics.median(words) if words else None,
                        "bands": [{"band": band, "book_count": len(keys), "book_keys": sorted(keys)}
                                  for band, keys in sorted(bands.items())]},
    }


def aggregate_snapshots(snapshots):
    """Deduplicate within comparable source/day groups, preserving every conflicting variant."""
    if not isinstance(snapshots, (list, tuple)) or not 1 <= len(snapshots) <= MAX_SNAPSHOTS:
        raise RankError(f"需要 1 至 {MAX_SNAPSHOTS} 个快照")
    unique = {}
    for snapshot in snapshots:
        normalized = validate_snapshot(snapshot)
        unique[_key(normalized)] = normalized
    if sum(len(snapshot["entries"]) for snapshot in unique.values()) > MAX_OBSERVATIONS:
        raise RankError(f"聚合最多处理 {MAX_OBSERVATIONS} 条原始观测")
    sources, groups, books, warnings = [], {}, {}, []
    aliases = defaultdict(set)
    for snapshot in unique.values():
        for entry in snapshot["entries"]:
            if entry["book_url"] and entry["book_id"]:
                aliases[(snapshot["platform"], entry["book_url"])].add(entry["book_id"])
    for (platform, url), ids in sorted(aliases.items()):
        if len(ids) > 1:
            warnings.append({"code": "identity_conflict", "platform": platform, "url": url,
                             "book_ids": sorted(ids), "message": "同一作品 URL 对应多个 ID，保留独立身份与原始观测待核实；相关身份排除出统计与抽样"})
    source_contents = defaultdict(list)
    for snapshot_id, snapshot in sorted(unique.items()):
        dimensions = _group_dimensions(snapshot)
        group_id = _key(dimensions)
        group = groups.setdefault(group_id, {"group_id": group_id, "dimensions": dimensions,
                                            "source_ids": [], "observations": defaultdict(dict)})
        group["source_ids"].append(snapshot_id)
        identities = set()
        for entry in snapshot["entries"]:
            book_id = entry["book_id"]
            linked_ids = aliases[(snapshot["platform"], entry["book_url"])] if entry["book_url"] else set()
            if book_id is None and len(linked_ids) == 1:
                book_id = next(iter(linked_ids))
            identity = {"platform": snapshot["platform"], "kind": "id" if book_id else "url",
                        "value": book_id or entry["book_url"]}
            book_key = _key(identity)
            identities.add(book_key)
            book = books.setdefault(book_key, {"book_key": book_key, "identity": identity,
                                              "titles": set(), "urls": set(), "observations": []})
            book["titles"].add(entry["title"])
            if entry["book_url"]:
                book["urls"].add(entry["book_url"])
            # The observed ID/URL is evidence, not an extra vote when identity is already resolved.
            values = {key: value for key, value in entry.items() if key not in ("book_id", "book_url")}
            variant = group["observations"][book_key].setdefault(
                _key(values), {"values": values, "source_ids": set(), "occurrences": 0})
            variant["source_ids"].add(snapshot_id)
            variant["occurrences"] += 1
        source = {"source_id": snapshot_id, "group_id": group_id, "platform": snapshot["platform"],
                  "collected_at": snapshot["collected_at"], "source": snapshot["source"],
                  "raw_entries": len(snapshot["entries"]), "unique_books": len(identities)}
        sources.append(source)
        if identities:
            source_contents[(snapshot["platform"], snapshot["collected_at"][:10], tuple(sorted(identities)))].append(source)
        declared = snapshot["source"]
        expected = declared["expected_count"]
        if declared["coverage"] == "complete" and (expected is None or expected != len(identities)):
            warnings.append({"code": "unverified_complete", "source_id": snapshot_id,
                             "message": "声明完整，但总数未知或与唯一作品数不符；不能视为已证实全量"})
        if expected is not None and expected < len(identities):
            warnings.append({"code": "count_mismatch", "source_id": snapshot_id,
                             "message": "唯一作品数超过声明总数，请核实分页、总数和采集范围"})
        pages = declared["pages"]
        if pages and pages != list(range(1, max(pages) + 1)):
            warnings.append({"code": "page_gap", "source_id": snapshot_id,
                             "message": "页码未从第 1 页连续覆盖；仅能解释已采样页面"})
    for matching_sources in source_contents.values():
        if len({source["group_id"] for source in matching_sources}) > 1:
            warnings.append({"code": "duplicate_content", "source_ids": sorted(source["source_id"] for source in matching_sources),
                             "message": "不同分组的作品样本相同，可能存在榜单入口或缓存问题；仍分别保留，不认定为同一榜"})
    ambiguous_book_keys = {
        book_key for book_key, book in books.items()
        if any(len(aliases[(book["identity"]["platform"], url)]) > 1 for url in book["urls"])
    }
    result_groups = []
    for group_id, group in sorted(groups.items()):
        observations = group.pop("observations")
        accepted, conflicts, identity_conflicts, duplicate_count = [], [], [], 0
        for book_key, variants in sorted(observations.items()):
            conflict = len(variants) > 1
            duplicate_count += sum(item["occurrences"] - 1 for item in variants.values())
            for variant_id, variant in sorted(variants.items()):
                books[book_key]["observations"].append({"group_id": group_id, "variant_id": variant_id,
                                                       "source_ids": sorted(variant["source_ids"]),
                                                       "conflict": conflict, **variant["values"]})
            if conflict:
                conflicts.append(book_key)
                warnings.append({"code": "observation_conflict", "group_id": group_id, "book_key": book_key,
                                 "message": "同源同日同书观测不一致，保留全部版本并从本组统计排除"})
            elif book_key in ambiguous_book_keys:
                # Exclusion buckets are disjoint: observation conflicts take precedence.
                identity_conflicts.append(book_key)
            else:
                accepted.append((book_key, next(iter(variants.values()))["values"]))
        values = [entry["metric_value"] for _, entry in accepted if entry["metric_value"] is not None]
        category_rows = []
        by_category = defaultdict(list)
        for _, entry in accepted:
            by_category[entry["category"]].append(entry)
        for category, entries in sorted(by_category.items(), key=lambda item: (item[0] is None, item[0] or "")):
            numbers = [entry["metric_value"] for entry in entries if entry["metric_value"] is not None]
            category_rows.append({"category": category, "sample_count": len(entries),
                                  "sample_share": len(entries) / len(accepted), "metric_valid_n": len(numbers),
                                  "metric_missing_n": len(entries) - len(numbers),
                                  "metric_median": statistics.median(numbers) if numbers else None})
        group.update({"unique_books": len(observations), "sample_count": len(accepted),
                      "excluded_conflicts": len(conflicts), "conflicted_book_keys": conflicts,
                      "excluded_identity_conflicts": len(identity_conflicts),
                      "identity_conflicted_book_keys": identity_conflicts,
                      "duplicate_observations": duplicate_count, "metric_valid_n": len(values),
                      "metric_missing_n": len(accepted) - len(values),
                      "metric_median": statistics.median(values) if values else None,
                      "categories": category_rows, "book_keys": sorted(observations),
                      **_pattern_analysis(accepted)})
        result_groups.append(group)
    for book in books.values():
        book["titles"] = sorted(book["titles"])
        book["urls"] = sorted(book["urls"])
    return {"schema_version": 1, "counts": {"snapshots": len(unique), "groups": len(groups), "books": len(books)},
            "method": "按平台、来源、榜名、频道、分类、周期、记录日期、采样方式、指标及新老书群体分组；同书去重，先排除观测冲突，再排除同 URL 多 ID 的身份歧义，两个排除计数不重叠；占比只描述有效样本。日期仅描述记录时点，HTML 导入时间不证明页面当日状态。同日不同时点也可能因正常变化产生冲突；合并后的统计不代表某一时点的完整榜单，排除冲突也会改变分类分布。",
            "sources": sources, "groups": result_groups, "books": [books[key] for key in sorted(books)],
            "warnings": sorted(warnings, key=_json)}


def build_sample(snapshots, group_id=None, category=None, tags=None, title_contains=None,
                 min_words=None, max_words=None, limit=5):
    """Reaggregate evidence and choose reproducible reading candidates from one group."""
    return _build_sample(snapshots, group_id, category, tags, title_contains, min_words, max_words, limit,
                         algorithm_version=SAMPLE_ALGORITHM_VERSION, legacy=False)


def _build_sample(snapshots, group_id=None, category=None, tags=None, title_contains=None,
                  min_words=None, max_words=None, limit=5, *, algorithm_version, legacy):
    """Replay a supported selection algorithm without changing its saved identity."""
    report = aggregate_snapshots(snapshots)
    if group_id is None:
        if len(report["groups"]) != 1:
            raise RankError("多个可比分组，必须用 --group 指定一个分组 ID；不能跨口径抽样")
        group = report["groups"][0]
    else:
        group_id = _text(group_id, "group_id", maximum=64)
        group = next((group for group in report["groups"] if group["group_id"] == group_id), None)
        if group is None:
            raise RankError("未知分组 ID；请先运行 rank-aggregate 查看分组")
    group_id = group["group_id"]
    category = _text(category, "category", nullable=True)
    title_contains = _text(title_contains, "title_contains", nullable=True)
    tags = _texts([] if tags is None else tags, "tags")
    min_words = _integer(min_words, "min_words", nullable=True)
    max_words = _integer(max_words, "max_words", nullable=True)
    if min_words is not None and max_words is not None and min_words > max_words:
        raise RankError("min_words 不能大于 max_words")
    limit = _integer(limit, "limit", minimum=1)
    if limit > MAX_SAMPLE_BOOKS:
        raise RankError(f"limit 不能超过 {MAX_SAMPLE_BOOKS}")
    filters = {"category": category, "tags": tags, "title_contains": title_contains,
               "min_words": min_words, "max_words": max_words}
    identity_conflicts = set(group["identity_conflicted_book_keys"])
    tag_counts = defaultdict(int)
    candidates = []
    for book in report["books"]:
        observation = next((row for row in book["observations"]
                            if row["group_id"] == group_id and not row["conflict"]), None)
        if observation is None:
            continue
        if book["book_key"] in identity_conflicts:
            continue
        # Count each accepted identity once, before user filters; report rows are display-only.
        for tag in observation["tags"]:
            tag_counts[tag] += 1
        word_count = observation["word_count"]
        if (category is not None and observation["category"] != category
                or not set(tags).issubset(observation["tags"])
                or title_contains is not None and title_contains not in observation["title"]
                or min_words is not None and (word_count is None or word_count < min_words)
                or max_words is not None and (word_count is None or word_count > max_words)):
            continue
        candidates.append({"book_key": book["book_key"], "identity": book["identity"],
                           "observation": observation})
    if not candidates:
        raise RankError("筛选后没有可抽样作品；请检查筛选值、字数缺失及身份/观测冲突")
    eligible_n = len(candidates)
    counted_tags = sorted(tag_counts.items(), key=lambda item: (-item[1], item[0]))
    if algorithm_version == 1:
        # Freeze the released top-50 selection rule independently of display limits.
        counted_tags = counted_tags[:50]
    repeated_tags = {tag for tag, count in counted_tags if count >= 2}
    seen_categories, seen_tags, seen_bands, selected = set(), set(), set(), []
    while candidates and len(selected) < limit:
        def choice_key(book):
            observation = book["observation"]
            return (-(observation["category"] not in seen_categories),
                    -len(set(observation["tags"]) & repeated_tags - seen_tags),
                    -(_word_band(observation["word_count"]) not in seen_bands),
                    observation["rank"], book["book_key"])
        chosen = min(candidates, key=choice_key)
        candidates.remove(chosen)
        observation = chosen["observation"]
        new_tags = sorted(set(observation["tags"]) & repeated_tags - seen_tags)
        band = _word_band(observation["word_count"])
        reasons = []
        if observation["category"] not in seen_categories:
            reasons.append("补充尚未覆盖的分类：" + (observation["category"] or "分类未知"))
        if new_tags:
            reasons.append("补充本组重复标签：" + "、".join(new_tags))
        if band not in seen_bands:
            reasons.append("补充尚未覆盖的字数区间：" + band)
        reasons.append(f"覆盖条件相同时按页面名次 {observation['rank']}、作品键稳定排序；名次不代表文学质量")
        chosen["reasons"] = reasons
        selected.append(chosen)
        seen_categories.add(observation["category"])
        seen_tags.update(observation["tags"])
        seen_bands.add(band)
    normalized = {_key(data): data for data in map(validate_snapshot, snapshots)}
    result = {"schema_version": 1 if legacy else SAMPLE_SCHEMA_VERSION, "kind": "rank_sample", "group_id": group_id,
              "dimensions": group["dimensions"], "filters": filters, "limit": limit,
              "counts": {"group_accepted": group["sample_count"], "eligible": eligible_n,
                         "selected": len(selected), "excluded_conflicts": group["excluded_conflicts"],
                         "excluded_identity_conflicts": group["excluded_identity_conflicts"]},
              "method": SAMPLE_METHODS[algorithm_version], "snapshots": [normalized[key] for key in sorted(normalized)],
              "sources": [source for source in report["sources"] if source["group_id"] == group_id],
              "warnings": report["warnings"], "books": selected}
    if not legacy:
        result["algorithm_version"] = algorithm_version
    result["sample_id"] = hashlib.sha256(_json(result).encode("utf-8")).hexdigest()
    if len(_json(result).encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise RankError("抽样证据超过 32 MiB 上限，请缩小输入快照范围")
    return result


def validate_sample(sample):
    """Verify reproducibility against embedded snapshots, not external source authenticity."""
    _fields(sample, ("schema_version", "kind", "sample_id", "group_id", "dimensions", "filters", "limit",
                     "counts", "method", "snapshots", "sources", "warnings", "books"), "sample",
            optional=("algorithm_version",))
    schema = sample["schema_version"]
    if type(schema) is not int or schema not in (1, SAMPLE_SCHEMA_VERSION) or sample["kind"] != "rank_sample":
        raise RankError("sample 必须为支持的 rank_sample schema_version（1 或 2）")
    if schema == 1:
        # Both pre-versioning formats were emitted locally. Exact frozen text
        # selects their algorithm; the complete result and hash still must match.
        if "algorithm_version" in sample:
            raise RankError("旧版 sample 不应带 algorithm_version，不能直接修改历史格式")
        algorithm = next((version for version, method in SAMPLE_METHODS.items() if sample["method"] == method), None)
        if algorithm is None:
            raise RankError("未知的旧版抽样方法；请保留原文件并核对生成版本")
    else:
        algorithm = sample.get("algorithm_version")
        if type(algorithm) is not int or algorithm not in SAMPLE_METHODS:
            raise RankError("不支持的 sample.algorithm_version；请保留原文件并核对生成版本")
    _fields(sample["filters"], ("category", "tags", "title_contains", "min_words", "max_words"), "sample.filters")
    rebuilt = _build_sample(sample["snapshots"], group_id=sample["group_id"], limit=sample["limit"],
                            algorithm_version=algorithm, legacy=schema == 1, **sample["filters"])
    try:
        consistent = _json(sample) == _json(rebuilt)
    except (TypeError, ValueError, RecursionError) as exc:
        raise RankError("sample 不是有效 JSON 对象") from exc
    if not consistent:
        raise RankError("sample 与嵌入快照重算结果不一致；请用 rank-sample 重新生成，不要手改证据或选择结果")
    return rebuilt


def render_sample(sample):
    lines = ["# 代表作阅读候选", "", f"样本 ID：{sample['sample_id']}",
             f"分组 ID：{sample['group_id']}；符合条件 {sample['counts']['eligible']} 本，选取 {sample['counts']['selected']} 本。",
             "", sample["method"], "", "筛选条件：" + _json(sample["filters"]),
             "", "## 来源与限制", ""]
    for source in sample["sources"]:
        declared = source["source"]
        lines.append(f"- {source['source_id']}：{declared['url']}；记录时间 {source['collected_at']}；覆盖 {declared['coverage']}；限制：{'；'.join(declared['limitations']) or '未填写'}。")
    for warning in sample["warnings"]:
        lines.append(f"- {warning['code']}：{warning['message']}。")
    for index, book in enumerate(sample["books"], 1):
        observation = book["observation"]
        lines += ["", f"## {index}. {_md(observation['title'])}", "",
                  f"作者：{_md(observation['author'])}；分类：{_md(observation['category'])}；字数：{_md(observation['word_count'])}。",
                  f"作品键：{book['book_key']}；身份：{_json(book['identity'])}。",
                  f"观测：{observation['variant_id']}；来源：{', '.join(observation['source_ids'])}。"]
        lines.extend("- " + reason for reason in book["reasons"])
    lines += ["", "下一步：阅读获授权的简介、开篇或全文，记录范围与原文定位；榜单和书名本身不能证明情节、读者动机或写法有效。",
              "sample.json 嵌入源快照，可由 validate_sample 重算核对后交给 research-init；不自动采用选题或写入正式书稿。", ""]
    return "\n".join(lines)


def _md(value):
    if value is None:
        return "未知"
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render_report(report):
    counts = report["counts"]
    lines = ["# 结构化扫榜样本报告", "", f"独立快照 {counts['snapshots']} 个；可比分组 {counts['groups']} 个；唯一作品 {counts['books']} 本。",
             "", report["method"], "", "名次是页面位置；指标缺失保留未知，不补零。不同分组不合成全网热度或爆款分。",
             "完整作品观测与来源 ID 见同目录 report.json。", "", "## 来源与覆盖", "",
             "| 来源 ID | 平台 / 榜单 | 记录时间 | 页面 | 覆盖声明 | 唯一作品 / 声明总数 |", "|---|---|---|---|---|---|"]
    for item in report["sources"]:
        source = item["source"]
        lines.append(f"| {item['source_id']} | {_md(item['platform'])} / {_md(source['board'])} | {item['collected_at']} | {','.join(map(str, source['pages'])) or '未知'} | {source['coverage']} | {item['unique_books']} / {_md(source['expected_count'])} |")
    for item in report["sources"]:
        source = item["source"]
        lines += ["", f"来源 {item['source_id']}：{source['url']}；采样方式：{_md(source['sample_method'])}；限制：{'；'.join(source['limitations']) or '未填写'}。"]
    lines += ["", "## 分组统计", "", "分类占比的分母为本组去重且无冲突的样本数，不能外推为平台市场份额。"]
    for group in report["groups"]:
        dim = group["dimensions"]
        metric = dim["metric"]
        metric_label = f"{metric['name']}（{metric['unit']}；窗口 {_md(metric['window'])}）" if metric else "仅名次，未采集数值指标"
        lines += ["", f"### {_md(dim['platform'])} / {_md(dim['board'])} / {dim['date']}", "",
                  f"分组 ID：{group['group_id']}；频道：{_md(dim['channel'])}；分类范围：{_md(dim['category'])}；周期：{_md(dim['period'])}；群体：{dim['cohort']}；采样：{_md(dim['sample_method'])}。",
                  f"口径：{_md(metric_label)}。有效样本 {group['sample_count']}；排除观测冲突 {group['excluded_conflicts']}；另排除身份歧义 {group['excluded_identity_conflicts']}；折叠重复观测 {group['duplicate_observations']}。",
                  "", "| 分类 | 样本数 | 样本内占比 | 指标有效 n | 指标缺失 n | 指标中位数 |", "|---|---:|---:|---:|---:|---:|"]
        for row in group["categories"]:
            lines.append(f"| {_md(row['category']) if row['category'] else '分类缺失'} | {row['sample_count']} | {row['sample_share']:.1%} | {row['metric_valid_n']} | {row['metric_missing_n']} | {_md(row['metric_median'])} |")
        if not group["categories"]:
            lines.append("| 无可统计样本 | 0 | — | 0 | 0 | 未知 |")
        words, tags, patterns = group["word_counts"], group["tags"], group["title_patterns"]
        lines += ["", f"字数：有效 {words['valid_n']}，缺失 {words['missing_n']}，真实零值 {words['zero_n']}；中位数 {_md(words['median'])}，范围 {_md(words['min'])} 至 {_md(words['max'])}。",
                  "", "| 字数区间 | 作品数 |", "|---|---:|"]
        lines.extend(f"| {row['band']} | {row['book_count']} |" for row in words["bands"])
        if not words["bands"]:
            lines.append("| 无可统计样本 | 0 |")
        lines += ["", f"标签：已获标签 {tags['tagged_n']} 本，未获标签 {tags['missing_n']} 本；不同标签 {tags['distinct_n']} 项，截断 {tags['truncated_n']} 项。", tags["method"],
                  "", "| 标签 | 作品数 | 有效样本内占比 |", "|---|---:|---:|"]
        lines.extend(f"| {_md(row['tag'])} | {row['book_count']} | {row['sample_share']:.1%} |" for row in tags["rows"])
        if not tags["rows"]:
            lines.append("| 无已采集标签 | 0 | — |")
        lines += ["", f"标题模式：候选 {patterns['candidate_n']} 项，截断 {patterns['truncated_n']} 项。", patterns["method"],
                  "", "| 类型 | 字面模式 | 作品数 | 有效样本内占比 |", "|---|---|---:|---:|"]
        kind_labels = {"prefix": "标题前缀", "suffix": "标题后缀", "structure": "标点结构"}
        lines.extend(f"| {kind_labels[row['kind']]} | {_md(row['pattern'])} | {row['book_count']} | {row['sample_share']:.1%} |" for row in patterns["rows"])
        if not patterns["rows"]:
            lines.append("| 无重复候选 | — | 0 | — |")
        lines += ["", "标签、标题模式与字数区间的对应作品键见 report.json；这些统计只描述已采集字段，不推断情节或读者偏好。"]
    lines += ["", "## 重复、冲突与限制", ""]
    if not report["warnings"]:
        lines.append("未检出覆盖声明矛盾或观测冲突；这不代表样本完整或来源数据真实无误。")
    for warning in report["warnings"]:
        pointer = warning.get("source_id") or warning.get("group_id") or ", ".join(warning.get("source_ids", []))
        lines.append(f"- {warning['code']}：{warning['message']}（{pointer or warning.get('url', '参见 JSON')}）。")
    lines += ["", "## 选题交接（待人工判断）", "",
              "- 先核实来源、分页覆盖、新老书群体与指标窗口；书库排序不能自行改称人气榜。",
              "- 在同一分组内观察样本较多的分类，再阅读具体作品的简介、开篇和读者反馈；不要从书名直接推断套路、质量或需求。",
              "- 对拟选题记录目标读者、可观察的阅读回报、创作优势、已有样本证据与反例；样本稀少或指标缺失时标为待验证。",
              "- 沿用用户已明确的采用授权；仅有研究材料时仍交候选，不自动改书库或写正文。", ""]
    return "\n".join(lines)


def _reject_constant(value):
    raise RankError(f"JSON 不允许 {value}")


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RankError(f"JSON 存在重复字段：{key}")
        result[key] = value
    return result


def load_snapshot(path):
    path = Path(path).expanduser()
    if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise RankError(f"输入必须是最多 {MAX_INPUT_BYTES} 字节的 JSON 文件：{path}")
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise RankError("JSON 输入超过大小上限")
        data = json.loads(raw.decode("utf-8-sig"), parse_constant=_reject_constant, object_pairs_hook=_unique_keys)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise RankError(f"无法读取 JSON：{path}：{exc}") from exc
    return validate_snapshot(data)


def _new_destination(path):
    destination = Path(path).expanduser()
    if destination.exists() or destination.is_symlink():
        raise RankError(f"输出目录必须是尚不存在的新目录：{destination}")
    if not destination.parent.is_dir():
        raise RankError("输出目录的父目录必须已经存在")
    return destination.resolve()


def _write_outputs(destination, files):
    encoded = {name: value.encode("utf-8") for name, value in files.items()}
    if sum(map(len, encoded.values())) > MAX_OUTPUT_BYTES:
        raise RankError("输出超过 32 MiB 上限，请缩小采样范围")
    try:
        destination.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise RankError("输出目录已存在，拒绝覆盖") from exc
    for name, content in encoded.items():
        path = destination / name
        if path.parent != destination:
            path.parent.mkdir(exist_ok=True)
        with path.open("xb") as output:
            output.write(content)


def register_parser(sub):
    sub.add_parser("rank-template", help="输出结构化扫榜快照示例")
    check = sub.add_parser("rank-check", help="校验离线扫榜快照与覆盖声明")
    check.add_argument("--input", required=True)
    aggregate = sub.add_parser("rank-aggregate", help="去重聚合结构化扫榜快照")
    aggregate.add_argument("--input", action="append", required=True)
    aggregate.add_argument("--output-dir", required=True)
    sample = sub.add_parser("rank-sample", help="按同一可比分组抽取代表作阅读候选与来源证据")
    sample.add_argument("--input", action="append", required=True, help="原始快照 JSON，可重复；不接受 report.json")
    sample.add_argument("--group", help="分组 ID；多个分组时必填")
    sample.add_argument("--category", help="精确匹配分类")
    sample.add_argument("--tag", action="append", help="精确匹配标签；重复使用表示同时具备全部标签")
    sample.add_argument("--title-contains", help="书名包含的原文字面片段，不是正则表达式")
    sample.add_argument("--min-words", type=int)
    sample.add_argument("--max-words", type=int)
    sample.add_argument("--limit", type=int, choices=range(1, MAX_SAMPLE_BOOKS + 1), default=5)
    sample.add_argument("--output-dir", required=True)
    collect = sub.add_parser("rank-collect", help="采集七猫公开书库样本（含本地 HTML 导入）")
    collect.add_argument("--source", choices=("qimao-library",), required=True)
    collect.add_argument("--pages", type=int, choices=range(1, 11), default=1)
    collect.add_argument("--html", action="append", help="已保存的本地 HTML，按页序重复，数量须等于 --pages")
    collect.add_argument("--html-url", action="append", help="每份 HTML 的实际源 URL，按相同页序重复；导入时必填")
    collect.add_argument("--output-dir", required=True)


def run(args, collect_qimao=None):
    command = args.command
    if command == "rank-template":
        return template()
    if command == "rank-check":
        snapshot = load_snapshot(args.input)
        report = aggregate_snapshots([snapshot])
        return {"ok": True, "platform": snapshot["platform"], "entries": len(snapshot["entries"]),
                "counts": report["counts"], "warnings": report["warnings"]}
    if command not in ("rank-aggregate", "rank-collect", "rank-sample"):
        raise RankError(f"未知扫榜命令：{command}")
    destination = _new_destination(args.output_dir)
    if command in ("rank-aggregate", "rank-sample"):
        if len(args.input) > MAX_SNAPSHOTS:
            raise RankError(f"最多输入 {MAX_SNAPSHOTS} 个快照")
        snapshots = [load_snapshot(path) for path in args.input]
        if command == "rank-sample":
            sample = build_sample(snapshots, group_id=args.group, category=args.category, tags=args.tag,
                                  title_contains=args.title_contains, min_words=args.min_words,
                                  max_words=args.max_words, limit=args.limit)
            files = {"sample.json": json.dumps(sample, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                     "sample.md": render_sample(sample)}
            _write_outputs(destination, files)
            return {"ok": True, "output_dir": str(destination), "sample_id": sample["sample_id"],
                    "group_id": sample["group_id"], "counts": sample["counts"],
                    "warnings": sample["warnings"], "files": sorted(files)}
        else:
            report = aggregate_snapshots(snapshots)
            files = {"report.json": json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                     "report.md": render_report(report)}
    else:
        html_files = getattr(args, "html", None)
        html_urls = getattr(args, "html_url", None)
        if html_files is not None and len(html_files) != args.pages:
            raise RankError("--html 文件数必须与 --pages 一致，按第 1 页起顺序提供")
        if html_files is not None and (html_urls is None or len(html_urls) != args.pages):
            raise RankError("HTML 导入须按页序提供 --html-url，数量必须与 --pages 一致")
        if html_files is None and html_urls is not None:
            raise RankError("--html-url 只能与 --html 一起使用")
        if collect_qimao is None:
            raise RankError("七猫采集器不可用")
        snapshot, raw_pages = collect_qimao(args.pages, html_files=html_files, html_urls=html_urls)
        snapshot = validate_snapshot(snapshot)
        if (not isinstance(raw_pages, dict) or set(raw_pages) != set(range(1, args.pages + 1))
                or any(type(page) is not int or not isinstance(html, str) for page, html in raw_pages.items())):
            raise RankError("采集器返回的原始页面与请求页码不一致")
        report = aggregate_snapshots([snapshot])
        files = {f"raw/page-{page}.html": html for page, html in raw_pages.items()}
        files.update({"snapshot.json": json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                      "report.json": json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                      "report.md": render_report(report)})
    _write_outputs(destination, files)
    return {"ok": True, "output_dir": str(destination), "counts": report["counts"],
            "warnings": report["warnings"], "files": sorted(files)}


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser(description=__doc__)
    register_parser(parser.add_subparsers(dest="command", required=True))
    try:
        print(json.dumps(run(parser.parse_args()), ensure_ascii=False, indent=2, allow_nan=False))
    except (RankError, OSError) as error:
        parser.exit(2, json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False) + "\n")
