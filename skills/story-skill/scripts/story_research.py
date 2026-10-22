"""Offline scan → text evidence → planning candidates; never changes a novel."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import unicodedata
from urllib.parse import urlsplit


_spec = importlib.util.spec_from_file_location("story_research_rank", Path(__file__).with_name("story_rank.py"))
rank = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rank)
ResearchError = rank.RankError
COMMANDS = ("research-init", "research-check", "research-verify", "research-render")
MAX_INPUT_BYTES = rank.MAX_OUTPUT_BYTES
MAX_REFERENCE_BYTES = 32 * 1024 * 1024
MAX_TOPICS = 30
MAX_HYPOTHESES = 30
MAX_VERIFICATIONS = 100
VERDICTS = ("supports", "contradicts", "mixed", "insufficient")
VERDICT_LABELS = dict(zip(VERDICTS, ("支持", "反证", "混合", "证据不足")))
TOPIC_TEXT_FIELDS = ("title", "target_reader", "payoff", "author_strength", "differentiation", "risk", "next_check")
EVIDENCE_FIELDS = ("topic_id", "hypothesis_id", "book_key", "hypothesis_claim", "text_ref", "report_ref",
                   "read_scope", "locators", "quote", "conclusion", "finding", "limitations")


def load_json(path):
    """Bounded JSON with duplicate keys and non-finite numbers rejected."""
    path = Path(path).expanduser()
    if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise ResearchError(f"输入必须是最多 {MAX_INPUT_BYTES} 字节的 JSON 文件：{path}")
    with path.open("rb") as handle:
        raw = handle.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ResearchError("JSON 输入超过大小上限")
    try:
        return json.loads(raw.decode("utf-8-sig"), parse_constant=rank._reject_constant,
                          object_pairs_hook=rank._unique_keys)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ResearchError(f"无法读取 JSON：{path}：{exc}") from exc


def _items(value, name, maximum, nonempty=False):
    if not isinstance(value, list) or len(value) > maximum or (nonempty and not value):
        raise ResearchError(f"{name} 必须是{'非空且' if nonempty else ''}最多 {maximum} 项的数组")
    return value


def _id(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", value):
        raise ResearchError(f"{name} 必须是最多 80 字符的稳定英文/数字 ID（可含 - 或 _）")
    return value


def _prose(value, name, nullable=False):
    return rank._text(value, name, nullable=nullable, maximum=8000)


def _quote(value, nullable):
    if value is None and nullable:
        return None
    if (not isinstance(value, str) or not value.strip() or len(value) > 800
            or any((ord(char) < 32 and char not in "\n\r\t") or unicodedata.category(char) == "Cs" for char in value)):
        raise ResearchError("quote 必须为最多 800 字符的非空原文摘录；仅 insufficient 可填 null")
    return value.strip()


def _digest(value):
    return hashlib.sha256(rank._json(value).encode("utf-8")).hexdigest()


def _reference(ref, name, cache):
    """Hash local files. URL references are declarations and are never fetched."""
    ref = rank._text(ref, name, maximum=4000)
    if ref in cache:
        return cache[ref]
    try:
        parsed = urlsplit(ref)
    except ValueError as exc:
        raise ResearchError(f"{name} URL 格式无效") from exc
    if parsed.scheme in ("http", "https"):
        rank.normalize_url(ref)  # Validate without discarding chapter/paragraph fragments.
        cache[ref] = None
        return None
    path = Path(ref)
    if not path.is_absolute() or not path.is_file():
        raise ResearchError(f"{name} 必须为存在的绝对本地文件路径或 HTTP(S) URL：{ref}")
    if path.stat().st_size > MAX_REFERENCE_BYTES:
        raise ResearchError(f"{name} 超过 32 MiB，请提供与阅读范围对应的原文或报告文件")
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            total += len(chunk)
            if total > MAX_REFERENCE_BYTES:
                raise ResearchError(f"{name} 超过 32 MiB")
            digest.update(chunk)
    cache[ref] = digest.hexdigest()
    return cache[ref]


def _quote_checked(record, reference_hash, cache):
    """Only literal local text membership is verified, never the interpretation."""
    if record["quote"] is None or reference_hash is None:
        return False
    key = (record["text_ref"], record["quote"])
    if key in cache:
        return cache[key]
    with Path(record["text_ref"]).open("rb") as handle:
        raw = handle.read(MAX_REFERENCE_BYTES + 1)
    if len(raw) > MAX_REFERENCE_BYTES or hashlib.sha256(raw).hexdigest() != reference_hash:
        raise ResearchError("text_ref 在校验期间变化，请保留稳定版本后重试")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ResearchError("本地 text_ref 必须是 UTF-8 原文文件，才能核验摘录") from exc
    if record["quote"] not in text:
        raise ResearchError("quote 未在 text_ref 原文中精确匹配；请核对摘录与原文版本")
    cache[key] = True
    return True


def _evidence_fields(record):
    """Validate user-authored evidence, without interpreting it as proof of reading."""
    for field in ("topic_id", "hypothesis_id", "book_key"):
        record[field] = _id(record[field], field)
    for field in ("hypothesis_claim", "finding"):
        record[field] = _prose(record[field], field)
    for field in ("text_ref", "report_ref"):
        record[field] = rank._text(record[field], field, maximum=4000)
    scope = record["read_scope"]
    rank._fields(scope, ("coverage", "description"), "read_scope")
    if scope["coverage"] not in ("partial", "full"):
        raise ResearchError("read_scope.coverage 必须为 partial/full，局部阅读不得填 full")
    scope["description"] = _prose(scope["description"], "read_scope.description")
    locators = record["locators"]
    rank._fields(locators, ("text", "report"), "locators")
    for field in ("text", "report"):
        _items(locators[field], "locators." + field, 100, nonempty=True)
        locators[field] = [_prose(item, "locators." + field) for item in locators[field]]
    if record["conclusion"] not in VERDICTS:
        raise ResearchError("conclusion 必须为 supports/contradicts/mixed/insufficient")
    record["quote"] = _quote(record["quote"], nullable=record["conclusion"] == "insufficient")
    _items(record["limitations"], "limitations", 100)
    record["limitations"] = [_prose(item, "limitations") for item in record["limitations"]]


def _check_binding(record, decision, topic, hypothesis):
    if record["topic_id"] != topic["topic_id"] or record["hypothesis_id"] != hypothesis["hypothesis_id"]:
        raise ResearchError("拆文证据的 topic_id/hypothesis_id 与目标不一致")
    if record["book_key"] not in {item["book_key"] for item in topic["books"]}:
        raise ResearchError("拆文证据 book_key 未被该选题引用")
    if record["hypothesis_claim"] != hypothesis["claim"]:
        raise ResearchError("假设已变化或证据绑定不一致；请保留旧假设及证据，另建 hypothesis_id")
    if record.get("sample_id", decision["sample"]["sample_id"]) != decision["sample"]["sample_id"]:
        raise ResearchError("sample_id 已变化；旧验证不能转移到新来源")


def validate_decision(value):
    """Check provenance and retained evidence; return a normalized independent copy."""
    try:
        data = deepcopy(value)
    except RecursionError as exc:
        raise ResearchError("decision 嵌套层级过深") from exc
    rank._fields(data, ("schema_version", "kind", "sample", "topics"), "decision")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1 or data["kind"] != "research_decision":
        raise ResearchError("decision 必须是 schema_version=1、kind=research_decision")
    data["sample"] = rank.validate_sample(data["sample"])
    sample_keys = {book["book_key"] for book in data["sample"]["books"]}
    _items(data["topics"], "topics", MAX_TOPICS, nonempty=True)
    topic_ids = set()
    evidence_ids = set()
    reference_cache, quote_cache = {}, {}
    for topic in data["topics"]:
        rank._fields(topic, ("topic_id", *TOPIC_TEXT_FIELDS, "books", "hypotheses"), "topic")
        _id(topic["topic_id"], "topic_id")
        if topic["topic_id"] in topic_ids:
            raise ResearchError("topic_id 重复")
        topic_ids.add(topic["topic_id"])
        for field in TOPIC_TEXT_FIELDS:
            topic[field] = _prose(topic[field], field, nullable=True)
        _items(topic["books"], "books", 100)
        selected = set()
        for book in topic["books"]:
            rank._fields(book, ("book_key", "reason"), "topic.books")
            _id(book["book_key"], "book_key")
            if book["book_key"] not in sample_keys or book["book_key"] in selected:
                raise ResearchError("topic.books book_key 未在样本中或重复")
            selected.add(book["book_key"])
            book["reason"] = _prose(book["reason"], "reason", nullable=True)
        _items(topic["hypotheses"], "hypotheses", MAX_HYPOTHESES, nonempty=True)
        hypothesis_ids = set()
        for hypothesis in topic["hypotheses"]:
            rank._fields(hypothesis, ("hypothesis_id", "claim", "verifications"), "hypothesis")
            _id(hypothesis["hypothesis_id"], "hypothesis_id")
            if hypothesis["hypothesis_id"] in hypothesis_ids:
                raise ResearchError("同一 topic 的 hypothesis_id 重复")
            hypothesis_ids.add(hypothesis["hypothesis_id"])
            hypothesis["claim"] = _prose(hypothesis["claim"], "claim", nullable=True)
            _items(hypothesis["verifications"], "verifications", MAX_VERIFICATIONS)
            for record in hypothesis["verifications"]:
                rank._fields(record, (*EVIDENCE_FIELDS, "sample_id", "reference_hashes", "quote_checked", "verification_id"), "verification")
                _evidence_fields(record)
                _check_binding(record, data, topic, hypothesis)
                rank._fields(record["reference_hashes"], ("text_ref", "report_ref"), "reference_hashes")
                for field in ("text_ref", "report_ref"):
                    current = _reference(record[field], field, reference_cache)
                    if record["reference_hashes"][field] != current:
                        raise ResearchError(f"{field} 文件已变化，历史验证引用过期；保留原文件并针对新版本重新验证")
                checked = _quote_checked(record, record["reference_hashes"]["text_ref"], quote_cache)
                if type(record["quote_checked"]) is not bool or record["quote_checked"] != checked:
                    raise ResearchError("quote_checked 与原文摘录校验结果不一致")
                expected_id = _digest({key: val for key, val in record.items() if key != "verification_id"})
                if record["verification_id"] != expected_id or expected_id in evidence_ids:
                    raise ResearchError("verification_id 不匹配或重复，历史验证不可直接覆盖")
                evidence_ids.add(expected_id)
    return data


def initialize(sample):
    sample = rank.validate_sample(sample)
    if not sample["books"]:
        raise ResearchError("代表作样本为空；请调整筛选后再建立交接")
    topic = {"topic_id": "topic-1", **{field: None for field in TOPIC_TEXT_FIELDS},
             "books": [{"book_key": book["book_key"], "reason": "；".join(book["reasons"])} for book in sample["books"]],
             "hypotheses": [{"hypothesis_id": "hypothesis-1", "claim": None, "verifications": []}]}
    return validate_decision({"schema_version": 1, "kind": "research_decision", "sample": sample, "topics": [topic]})


def evidence_template(decision):
    """Automatic identity fields plus deliberately incomplete evidence fields."""
    items = []
    for topic in decision["topics"]:
        for hypothesis in topic["hypotheses"]:
            for book in topic["books"]:
                items.append({"topic_id": topic["topic_id"], "hypothesis_id": hypothesis["hypothesis_id"],
                              "book_key": book["book_key"], "hypothesis_claim": hypothesis["claim"],
                              "text_ref": None, "report_ref": None,
                              "read_scope": {"coverage": "partial", "description": None},
                              "locators": {"text": [], "report": []}, "quote": None, "conclusion": None,
                              "finding": None, "limitations": []})
    return {"schema_version": 1, "kind": "research_evidence", "sample_id": decision["sample"]["sample_id"], "items": items}


def verify(decision, evidence):
    data = validate_decision(decision)
    evidence = deepcopy(evidence)
    rank._fields(evidence, ("schema_version", "kind", "sample_id", "items"), "evidence")
    if (type(evidence["schema_version"]) is not int or evidence["schema_version"] != 1
            or evidence["kind"] != "research_evidence"):
        raise ResearchError("evidence 必须是 schema_version=1、kind=research_evidence")
    if evidence["sample_id"] != data["sample"]["sample_id"]:
        raise ResearchError("sample_id 与当前样本不一致，不能回填旧来源的验证")
    _items(evidence["items"], "items", 1000, nonempty=True)
    topics = {topic["topic_id"]: topic for topic in data["topics"]}
    added = 0
    reference_cache, quote_cache = {}, {}
    for record in evidence["items"]:
        rank._fields(record, EVIDENCE_FIELDS, "evidence.items")
        _evidence_fields(record)
        topic = topics.get(record["topic_id"])
        if topic is None:
            raise ResearchError("证据引用未知 topic_id")
        hypothesis = next((item for item in topic["hypotheses"] if item["hypothesis_id"] == record["hypothesis_id"]), None)
        if hypothesis is None:
            raise ResearchError("证据引用未知 hypothesis_id")
        _check_binding(record, data, topic, hypothesis)
        record["sample_id"] = evidence["sample_id"]
        record["reference_hashes"] = {field: _reference(record[field], field, reference_cache) for field in ("text_ref", "report_ref")}
        record["quote_checked"] = _quote_checked(record, record["reference_hashes"]["text_ref"], quote_cache)
        record["verification_id"] = _digest(record)
        if record["verification_id"] not in {item["verification_id"] for item in hypothesis["verifications"]}:
            hypothesis["verifications"].append(record)
            added += 1
    return validate_decision(data), added


def _display(value):
    return "待填写" if value is None else str(value)


def _source_lines(sample):
    lines = [f"样本 ID：{sample['sample_id']}；分组 ID：{sample['group_id']}。",
             "仅针对本次样本；抽样理由不等于作品质量、市场需求或成功概率。", ""]
    for item in sample["sources"]:
        source = item["source"]
        lines += [f"- {item['platform']} / {source['board']}：{source['url']}",
                  f"  记录：{item['collected_at']}；页码：{source['pages']}；覆盖：{source['coverage']}；限制：{'；'.join(source['limitations']) or '未填写'}。"]
    for warning in sample["warnings"]:
        lines.append(f"- 样本警示：{warning['message']}")
    return lines


def render(decision, purpose="planning"):
    """Readable handoff preserves partial/contradictory findings, without auto-adoption."""
    sample = decision["sample"]
    books = {book["book_key"]: book for book in sample["books"]}
    heading = "选题规划候选交接" if purpose == "planning" else "代表作拆文验证交接"
    lines = [f"# {heading}", "", "状态：候选，尚未采用。不会创建小说、正式稿或平台材料。",
             "支持/反证仅描述已读文本中的机制；榜单和拆文均不能单独证明读者需求或商业结果。",
             "脚本核验样本绑定、本地引用文件哈希及摘录字面匹配，不证明实际阅读完成、引用语义正确或作品质量；URL 引用仅为声明。",
             "局部阅读只支持该阅读范围，不升级为全书结论。", "", "## 样本来源与限制", "", *_source_lines(sample)]
    for topic in decision["topics"]:
        lines += ["", f"## {topic['topic_id']}：{_display(topic['title'])}", ""]
        labels = {"target_reader": "目标读者", "payoff": "预期阅读回报", "author_strength": "创作优势",
                  "differentiation": "差异化", "risk": "风险", "next_check": "下一步验证"}
        for field, label in labels.items():
            lines.append(f"- {label}：{_display(topic[field])}")
        lines += ["", "### 对标作品与选择理由", ""]
        for selected in topic["books"]:
            book = books[selected["book_key"]]
            obs = book["observation"]
            lines.append(f"- 《{obs['title']}》／{obs['author'] or '作者未知'}（{book['book_key']}）：{_display(selected['reason'])}")
        for hypothesis in topic["hypotheses"]:
            lines += ["", f"### {hypothesis['hypothesis_id']}：{_display(hypothesis['claim'])}", ""]
            records = hypothesis["verifications"]
            if not records:
                lines.append("待拆文验证；尚无回填证据。请阅读原文并形成可定位的分析报告，填写 evidence-template.json。")
            verdicts = {record["conclusion"] for record in records}
            if "mixed" in verdicts or {"supports", "contradicts"} <= verdicts:
                lines.append("存在混合或相互冲突的证据；保留全部记录，选题取舍仍需人工判断。")
            for record in records:
                scope = record["read_scope"]
                lines += [f"- {VERDICT_LABELS[record['conclusion']]}（{record['verification_id'][:12]}；作品 {record['book_key']}）：{record['finding']}",
                          f"  阅读范围：{'局部' if scope['coverage'] == 'partial' else '全文（声明）'}；{scope['description']}。",
                          f"  原文：{record['text_ref']}；定位：{'；'.join(record['locators']['text'])}。",
                          f"  摘录：{record['quote'] or '未提供（证据不足）'}；字面校验：{'本地原文匹配' if record['quote_checked'] else '未核验'}。",
                          f"  报告：{record['report_ref']}；定位：{'；'.join(record['locators']['report'])}。",
                          f"  限制：{'；'.join(record['limitations']) or '未填写额外限制'}。"]
                if any(value is None for value in record["reference_hashes"].values()):
                    lines.append("  含 URL 引用：未联网核验内容或版本。")
    lines += ["", "## 继续交接", "",
              "在 decision.json 中填写或修订候选信息；已有证据的假设发生实质变化时另建 hypothesis_id，保留原假设和历史。",
              "拆文流程依据原文和分析报告填写证据，用 research-verify 回填；research-render 生成当前候选交接。",
              "规划流程读取本文件及 decision.json，区分样本观察、文本机制和原创设想；沿用用户已明确的采用授权，仅有研究材料时仍交候选。", ""]
    return "\n".join(lines)


def _files(decision):
    dump = lambda value: json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    return {"decision.json": dump(decision), "analysis-brief.md": render(decision, "analysis"),
            "planning-candidate.md": render(decision), "evidence-template.json": dump(evidence_template(decision))}


def register_parser(sub):
    init = sub.add_parser("research-init", help="从代表作样本生成待填写的拆文与选题交接")
    init.add_argument("--sample", required=True)
    init.add_argument("--output-dir", required=True)
    for name, help_text in (("research-check", "只读核验研究交接与本地引用版本"),
                            ("research-verify", "按显式假设与作品身份追加拆文证据"),
                            ("research-render", "导出当前拆文任务与规划候选")):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--input", required=True)
        if name == "research-verify":
            command.add_argument("--evidence", required=True)
        if name != "research-check":
            command.add_argument("--output-dir", required=True)


def run(args):
    if args.command not in COMMANDS:
        raise ResearchError(f"未知研究交接命令：{args.command}")
    if args.command == "research-check":
        decision = validate_decision(load_json(args.input))
        return {"ok": True, "sample_id": decision["sample"]["sample_id"], "topics": len(decision["topics"]),
                "verifications": sum(len(hypothesis["verifications"]) for topic in decision["topics"] for hypothesis in topic["hypotheses"]),
                "status": "candidate"}
    destination = rank._new_destination(args.output_dir)
    added = 0
    if args.command == "research-init":
        decision = initialize(load_json(args.sample))
    else:
        decision = validate_decision(load_json(args.input))
        if args.command == "research-verify":
            decision, added = verify(decision, load_json(args.evidence))
    files = _files(decision)
    rank._write_outputs(destination, files)
    return {"ok": True, "output_dir": str(destination), "files": sorted(files), "added_verifications": added,
            "sample_id": decision["sample"]["sample_id"], "status": "candidate"}


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser(description=__doc__)
    register_parser(parser.add_subparsers(dest="command", required=True))
    try:
        print(json.dumps(run(parser.parse_args()), ensure_ascii=False, indent=2, allow_nan=False))
    except (ResearchError, OSError) as error:
        parser.exit(2, json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False) + "\n")
