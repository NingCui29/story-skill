"""Versioned, local reader-study materials; no invitations or novel-state writes."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import unicodedata


_spec = importlib.util.spec_from_file_location("reader_research", Path(__file__).with_name("story_research.py"))
research = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(research)
rank = research.rank
FeedbackError = research.ResearchError
COMMANDS = ("reader-init", "reader-record", "reader-check", "reader-render")
MAX_ROUNDS, MAX_PARTICIPANTS, MAX_RECORDS = 20, 500, 5000
LIMITS = ("仅核对材料关联、文件版本、摘录与所填观察；不证明实际阅读或参与者身份。"
          "不计算留存率、文学分数或因果效果；不同来源、版本、轮次、人群与提醒条件分别列计数。"
          "不邀读、不联网、不调度、不修改小说事实库；原话与修改假设分别保留。")
RECORD_FIELDS = ("participant_id", "round_id", "observed_at", "opportunity", "provided_at",
                 "reminder", "reading", "evidence_kind", "evidence_file", "reader_words",
                 "text_quote", "text_locator", "revision_hypothesis", "limitations", "supersedes")


def _digest(value):
    return hashlib.sha256(rank._json(value).encode("utf-8")).hexdigest()


def _prose(value, name, nullable=False):
    if value is None and nullable:
        return None
    if (not isinstance(value, str) or not value.strip() or len(value) > 8000
            or any((ord(c) < 32 and c not in "\n\t") or unicodedata.category(c) == "Cs" for c in value)):
        raise FeedbackError(f"{name} 必须为最多 8000 字符的非空文本" + ("或 null" if nullable else ""))
    return value  # Preserve the participant's actual wording, including line breaks.


def _id(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value):
        raise FeedbackError(f"{name} 必须为最多 64 字符的匿名英文数字 ID（可含 - 或 _）")
    return value


def _sha(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise FeedbackError(f"{name} 必须是 SHA-256")
    return value


def _time(value, name, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or len(value) > 64 or "T" not in value:
        raise FeedbackError(f"{name} 必须为带时区的 ISO 日期时间")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise FeedbackError(f"{name} 必须为带时区的 ISO 日期时间") from error
    if result.utcoffset() is None:
        raise FeedbackError(f"{name} 必须包含时区")
    try:
        return result.astimezone(timezone.utc)
    except OverflowError as error:
        raise FeedbackError(f"{name} 转换为 UTC 后超出支持的日期范围") from error


def _file(path):
    path = Path(rank._text(path, "文件路径", maximum=4000)).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise FeedbackError("稿件与观察证据必须是存在的绝对本地文件路径")
    with path.open("rb") as handle:
        raw = handle.read(research.MAX_REFERENCE_BYTES + 1)
    if len(raw) > research.MAX_REFERENCE_BYTES:
        raise FeedbackError("引用文件超过 32 MiB")
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}, raw


def _file_ref(value):
    rank._fields(value, ("path", "sha256"), "文件引用")
    path = rank._text(value["path"], "文件路径", maximum=4000)
    if not Path(path).is_absolute():
        raise FeedbackError("文件引用路径必须为绝对路径")
    _sha(value["sha256"], "文件 sha256")


def _plan(value, frozen=False):
    plan = deepcopy(value)
    rank._fields(plan, ("study_id", "question", "rounds", "participants"), "plan")
    _id(plan["study_id"], "study_id")
    _prose(plan["question"], "question")
    research._items(plan["rounds"], "rounds", MAX_ROUNDS, nonempty=True)
    rounds = {}
    for item in plan["rounds"]:
        rank._fields(item, ("round_id", "manuscript", "reading_scope", "available_at", "closes_at",
                           "delivery", "reminder_policy", "follows_round_id"), "round")
        key = _id(item["round_id"], "round_id")
        if key in rounds:
            raise FeedbackError("round_id 重复")
        for field in ("reading_scope", "delivery", "reminder_policy"):
            _prose(item[field], field)
        if _time(item["available_at"], "available_at") >= _time(item["closes_at"], "closes_at"):
            raise FeedbackError("closes_at 必须晚于 available_at")
        if item["follows_round_id"] is not None:
            _id(item["follows_round_id"], "follows_round_id")
        if frozen:
            _file_ref(item["manuscript"])
        else:
            item["manuscript"], raw = _file(item["manuscript"])
            try:
                raw.decode("utf-8-sig")
            except UnicodeError as error:
                raise FeedbackError("稿件必须是 UTF-8 文本") from error
        rounds[key] = item
    for item in rounds.values():
        prior = item["follows_round_id"]
        if prior is not None and (prior not in rounds or
                _time(rounds[prior]["available_at"], "available_at") >= _time(item["available_at"], "available_at")):
            raise FeedbackError("follows_round_id 必须引用更早提供的已登记轮次")
    research._items(plan["participants"], "participants", MAX_PARTICIPANTS)
    seen = set()
    for person in plan["participants"]:
        rank._fields(person, ("participant_id", "source_kind", "cohort"), "participant")
        key = _id(person["participant_id"], "participant_id")
        if key in seen:
            raise FeedbackError("participant_id 重复")
        seen.add(key)
        if person["source_kind"] not in ("human", "model", "unknown"):
            raise FeedbackError("source_kind 必须为 human/model/unknown")
        _prose(person["cohort"], "cohort")
    return plan


def initialize(plan):
    plan = _plan(plan)
    return {"schema_version": 1, "kind": "reader_study", "plan": plan,
            "plan_sha256": _digest(plan), "records": []}


def _record(record, plan, frozen=False):
    rank._fields(record, (*RECORD_FIELDS, "record_id") if frozen else RECORD_FIELDS, "observation")
    _id(record["participant_id"], "participant_id")
    _id(record["round_id"], "round_id")
    people = {p["participant_id"]: p for p in plan["participants"]}
    rounds = {r["round_id"]: r for r in plan["rounds"]}
    if record["participant_id"] not in people or record["round_id"] not in rounds:
        raise FeedbackError("观察引用未知 participant_id 或 round_id")
    person, current = people[record["participant_id"]], rounds[record["round_id"]]
    observed = _time(record["observed_at"], "observed_at")
    provided = _time(record["provided_at"], "provided_at", nullable=True)
    if record["opportunity"] not in ("provided", "not_provided", "unknown"):
        raise FeedbackError("opportunity 必须为 provided/not_provided/unknown")
    if record["reminder"] not in ("none", "sent", "unknown"):
        raise FeedbackError("reminder 必须为 none/sent/unknown")
    if record["reading"] not in ("not_started", "started", "completed", "unknown"):
        raise FeedbackError("reading 必须为 not_started/started/completed/unknown")
    if record["opportunity"] == "provided":
        if provided is None or not _time(current["available_at"], "available_at") <= provided <= observed:
            raise FeedbackError("提供机会须有处于计划开放时间与观察时间之间的 provided_at")
    elif provided is not None or record["reading"] in ("started", "completed") or record["reminder"] == "sent":
        raise FeedbackError("未确认提供机会时不能记录已开始、读完、提供时间或已提醒")
    kinds = {"human": ("participant_report", "local_record", "unknown"),
             "model": ("model_output", "unknown"), "unknown": ("unknown",)}
    if record["evidence_kind"] not in kinds[person["source_kind"]]:
        raise FeedbackError("evidence_kind 与人类/模型/未知来源不一致")
    if record["reading"] != "unknown" and record["evidence_kind"] == "unknown":
        raise FeedbackError("阅读状态缺少观察来源时须保持 unknown")
    for field in ("reader_words", "text_quote", "text_locator", "revision_hypothesis"):
        _prose(record[field], field, nullable=True)
    if record["text_quote"] is not None and record["text_locator"] is None:
        raise FeedbackError("正文摘录须有 text_locator")
    research._items(record["limitations"], "limitations", 50)
    for limitation in record["limitations"]:
        _prose(limitation, "limitations")
    if record["supersedes"] is not None:
        _sha(record["supersedes"], "supersedes")
    if record["evidence_file"] is not None:
        if frozen:
            _file_ref(record["evidence_file"])
        else:
            record["evidence_file"], _ = _file(record["evidence_file"])
    elif record["evidence_kind"] in ("local_record", "model_output"):
        raise FeedbackError("local_record/model_output 须提供本地 evidence_file")
    if record["evidence_kind"] == "participant_report" and record["reader_words"] is None and record["evidence_file"] is None:
        raise FeedbackError("参与者报告须保留原话或本地记录")
    if frozen:
        expected = _digest({key: value for key, value in record.items() if key != "record_id"})
        if record["record_id"] != expected:
            raise FeedbackError("record_id 不匹配；历史观察须保留，修正请追加 supersedes")
    return record


def validate_study(value):
    data = deepcopy(value)
    rank._fields(data, ("schema_version", "kind", "plan", "plan_sha256", "records"), "study")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1 or data["kind"] != "reader_study":
        raise FeedbackError("需要 schema_version=1、kind=reader_study")
    data["plan"] = _plan(data["plan"], frozen=True)
    if data["plan_sha256"] != _digest(data["plan"]):
        raise FeedbackError("计划或稿件版本已改变；保留旧研究，重新 reader-init")
    research._items(data["records"], "records", MAX_RECORDS)
    latest, ids = {}, set()
    for record in data["records"]:
        _record(record, data["plan"], frozen=True)
        key = (record["participant_id"], record["round_id"])
        if record["record_id"] in ids:
            raise FeedbackError("record_id 重复")
        ids.add(record["record_id"])
        prior = latest.get(key)
        if record["supersedes"] != (prior["record_id"] if prior else None):
            raise FeedbackError("重复观察须通过 supersedes 指向该参与者该轮最新记录")
        if prior and _time(record["observed_at"], "observed_at") < _time(prior["observed_at"], "observed_at"):
            raise FeedbackError("修正观察时间不能早于被修正记录")
        latest[key] = record
    return data


def record_observations(study, observations):
    data = validate_study(study)
    observations = deepcopy(observations)
    rank._fields(observations, ("plan_sha256", "items"), "observations")
    if observations["plan_sha256"] != data["plan_sha256"]:
        raise FeedbackError("观察的 plan_sha256 与研究计划不一致")
    research._items(observations["items"], "items", MAX_RECORDS, nonempty=True)
    rounds = {r["round_id"]: r for r in data["plan"]["rounds"]}
    seen = {r["record_id"] for r in data["records"]}
    sources, added = {}, 0
    for record in observations["items"]:
        _record(record, data["plan"])
        round_id = record["round_id"]
        if round_id not in sources:
            expected = rounds[round_id]["manuscript"]
            actual, raw = _file(expected["path"])
            if actual != expected:
                raise FeedbackError("稿件文件已变化；不能把新观察接到旧稿，请保留原稿或新建研究")
            sources[round_id] = raw.decode("utf-8-sig")
        if record["text_quote"] is not None and record["text_quote"] not in sources[round_id]:
            raise FeedbackError("text_quote 未在该轮稿件中精确匹配")
        record["record_id"] = _digest(record)
        if record["record_id"] not in seen:
            data["records"].append(record)
            seen.add(record["record_id"])
            added += 1
    return validate_study(data), added


def source_warnings(study):
    refs = [("round:" + r["round_id"], r["manuscript"]) for r in study["plan"]["rounds"]]
    refs += [("record:" + r["record_id"], r["evidence_file"]) for r in study["records"] if r["evidence_file"]]
    warnings = []
    cache = {}
    for owner, ref in refs:
        if ref["path"] not in cache:
            try:
                cache[ref["path"]] = _file(ref["path"])[0]["sha256"]
            except (OSError, FeedbackError):
                cache[ref["path"]] = None
        actual = cache[ref["path"]]
        if actual != ref["sha256"]:
            warnings.append({"owner": owner, "path": ref["path"], "expected_sha256": ref["sha256"],
                             "status": "unavailable" if actual is None else "changed"})
    return warnings


def summarize(study, as_of=None):
    study = validate_study(study)
    now = _time(as_of, "as_of") if as_of else datetime.now(timezone.utc)
    latest = {}
    for record in study["records"]:
        if _time(record["observed_at"], "observed_at") <= now:
            latest[(record["participant_id"], record["round_id"])] = record
    groups = defaultdict(Counter)
    warnings = source_warnings(study)
    for current in study["plan"]["rounds"]:
        for person in study["plan"]["participants"]:
            record = latest.get((person["participant_id"], current["round_id"]))
            reminder = record["reminder"] if record else "unknown"
            cutoff = _time(current["closes_at"], "closes_at")
            opportunity = record["opportunity"] if record else "unknown"
            timing = ("late" if _time(record["provided_at"], "provided_at") > cutoff else "in_window") if opportunity == "provided" else "not_confirmed"
            key = (current["round_id"], current["manuscript"]["sha256"], person["source_kind"], person["cohort"], reminder, opportunity, timing)
            counts = groups[key]
            counts["planned_pairs"] += 1
            if record is None:
                counts["unobserved"] += 1
                continue
            counts["recorded_pairs"] += 1
            if opportunity != "provided":
                counts["no_opportunity" if opportunity == "not_provided" else "opportunity_unknown"] += 1
                continue
            counts["provided"] += 1
            if timing == "late":
                counts["late_opportunity"] += 1
            reading = record["reading"]
            if reading in ("started", "completed"):
                counts["started"] += 1
                if reading == "completed":
                    counts["completed"] += 1
                prior_id = current["follows_round_id"]
                if prior_id:
                    prior = latest.get((person["participant_id"], prior_id))
                    if prior and prior["reading"] in ("started", "completed") and _time(prior["observed_at"], "observed_at") <= _time(record["observed_at"], "observed_at"):
                        counts["reported_return"] += 1
                    else:
                        counts["prior_reading_unverified"] += 1
            elif timing == "late":
                counts["reading_after_late_opportunity"] += 1
            elif now < cutoff:
                counts["window_open"] += 1
            elif reading == "not_started" and _time(record["observed_at"], "observed_at") >= cutoff:
                counts["not_started_at_observation"] += 1
            else:
                counts["reading_unknown_at_cutoff"] += 1
    rows = [{"round_id": key[0], "manuscript_sha256": key[1], "source_kind": key[2], "cohort": key[3],
             "reminder": key[4], "opportunity": key[5], "opportunity_timing": key[6],
             "counts": dict(sorted(counts.items()))} for key, counts in sorted(groups.items())]
    return {"ok": True, "status": "source_changed" if warnings else "recorded_materials",
            "study_id": study["plan"]["study_id"], "plan_sha256": study["plan_sha256"],
            "as_of": now.isoformat(), "records": len(study["records"]), "groups": rows,
            "source_warnings": warnings, "limits": LIMITS}


def observation_template(study):
    person = study["plan"]["participants"]
    return {"plan_sha256": study["plan_sha256"], "items": [{
        "participant_id": person[0]["participant_id"] if person else None,
        "round_id": study["plan"]["rounds"][0]["round_id"], "observed_at": None,
        "opportunity": "unknown", "provided_at": None, "reminder": "unknown", "reading": "unknown",
        "evidence_kind": "unknown", "evidence_file": None, "reader_words": None, "text_quote": None,
        "text_locator": None, "revision_hypothesis": None, "limitations": [], "supersedes": None}]}


def render(study, summary):
    lines = ["# 分次阅读观察", "", "研究问题：" + study["plan"]["question"],
             "研究 ID：" + study["plan"]["study_id"], "观察截止：" + summary["as_of"], "", LIMITS,
             "计划中的匿名 ID 不等于已参加试读；reported_return 仅为有前次阅读记录的再次阅读报告，不认证读者身份或实际行为。", ""]
    for warning in summary["source_warnings"]:
        lines.append(f"- 来源待核：{warning['owner']}／{warning['status']}／{warning['path']}；历史反馈保留，不套给新稿。")
    for current in study["plan"]["rounds"]:
        lines += ["", "## " + current["round_id"], "", "稿件：" + current["manuscript"]["path"],
                  "版本：" + current["manuscript"]["sha256"], "范围：" + current["reading_scope"],
                  f"提供窗口：{current['available_at']} 至 {current['closes_at']}",
                  "提供方式：" + current["delivery"], "计划提醒：" + current["reminder_policy"]]
        for row in summary["groups"]:
            if row["round_id"] == current["round_id"]:
                lines += [f"- 来源 {row['source_kind']}／人群 {row['cohort']}／实际提醒 {row['reminder']}／机会 {row['opportunity']}（{row['opportunity_timing']}）：" +
                          "；".join(f"{key}={value}" for key, value in row["counts"].items())]
    lines += ["", "## 原始观察与修正历史", "", "以下包括全部历史；超过观察截止的记录不参与上方汇总。"]
    for record in study["records"]:
        lines += ["", f"### {record['participant_id']}／{record['round_id']}／{record['record_id'][:12]}",
                  f"观察时间：{record['observed_at']}；来源：{record['evidence_kind']}；机会：{record['opportunity']}；阅读：{record['reading']}",
                  "修正此前记录：" + (record["supersedes"] or "无"),
                  "参与者或模型原话：" + (record["reader_words"] or "未提供"),
                  "正文定位：" + (record["text_locator"] or "未提供"),
                  "正文摘录：" + (record["text_quote"] or "未提供"),
                  "修改假设（尚未证明）：" + (record["revision_hypothesis"] or "未提出"),
                  "限制：" + ("；".join(record["limitations"]) or "未补充")]
    return "\n".join(lines) + "\n"


def register_parser(sub):
    command = sub.add_parser("reader-init", help="冻结分次试读研究计划与稿件版本；不邀读")
    command.add_argument("--plan", required=True)
    command.add_argument("--output-dir", required=True)
    for name in COMMANDS[1:]:
        command = sub.add_parser(name, help={"reader-record": "追加阅读观察并另存新研究目录", "reader-check": "只读核对来源并按条件汇总", "reader-render": "导出阅读观察与修正历史"}[name])
        command.add_argument("--input", required=True)
        command.add_argument("--as-of", help="带时区的观察截止；不填使用当前 UTC 时间")
        if name == "reader-record":
            command.add_argument("--observations", required=True)
        if name != "reader-check":
            command.add_argument("--output-dir", required=True)


def run(args):
    if args.command not in COMMANDS:
        raise FeedbackError("未知试读研究命令")
    added = 0
    if args.command == "reader-init":
        study = initialize(research.load_json(args.plan))
    else:
        study = validate_study(research.load_json(args.input))
        if args.command == "reader-record":
            study, added = record_observations(study, research.load_json(args.observations))
    summary = summarize(study, getattr(args, "as_of", None))
    if args.command == "reader-check":
        return summary
    destination = rank._new_destination(args.output_dir)
    dump = lambda value: json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    files = {"study.json": dump(study), "observation-template.json": dump(observation_template(study)),
             "summary.json": dump(summary), "summary.md": render(study, summary)}
    rank._write_outputs(destination, files)
    return {"ok": True, "status": summary["status"], "plan_sha256": study["plan_sha256"],
            "output_dir": str(destination), "files": sorted(files), "added_records": added,
            "source_warnings": summary["source_warnings"], "limits": LIMITS}


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser(description=__doc__)
    register_parser(parser.add_subparsers(dest="command", required=True))
    try:
        print(json.dumps(run(parser.parse_args()), ensure_ascii=False, indent=2, allow_nan=False))
    except (FeedbackError, OSError, UnicodeError, RecursionError) as error:
        parser.exit(2, json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False) + "\n")
