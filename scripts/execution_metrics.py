#!/usr/bin/env python3
"""Read reported Codex usage from supplied JSONL files; never run a model.

Supported adapters:
* codex exec --json: turn.completed/turn.failed.usage is per-turn usage.
* Codex session JSONL: event_msg/token_count/info.total_token_usage is a
  cumulative session snapshot. Snapshots are retained, never added together.

Only explicit native fields are read. Missing fields stay None. Input/cache
and output/reasoning can overlap; this tool neither adds them into a synthetic
total nor converts them into money. Caller-labelled child traces are separate
from main traces: their relationship and inclusion in main usage are unknown.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                "output_tokens", "reasoning_output_tokens", "total_tokens")
TOOL_TYPES = {"command_execution", "mcp_tool_call", "collab_tool_call", "web_search"}
OTHER_ITEM_TYPES = {"agent_message", "reasoning", "file_change", "todo_list", "error"}
CLI_TYPES = {"thread.started", "turn.started", "turn.completed", "turn.failed",
             "item.started", "item.updated", "item.completed", "error"}
LIMITS = [
    "Reported fields in supplied files only; hashes do not authenticate their origin.",
    "Missing fields are unknown, not zero. No tokenizer estimates or billed cost.",
    "Input/cache and output/reasoning are separate reported fields; do not add overlapping fields.",
    "No combined main-plus-child total: child relationships and main inclusion are unverified.",
    "No retry count inferred from errors, repeated commands, or failed turns.",
    "Tool counts are observed native item lifecycles, not internal calls or file reads.",
    "Different files with the same or missing session ID may overlap; their sums are withheld.",
    "Without stable event IDs, concatenated replay and legitimate resumed turns may be indistinguishable; inspect partial sums.",
    "Session cumulative snapshots may include work before capture and are not task deltas.",
]


def empty_tokens():
    return dict.fromkeys(TOKEN_FIELDS)


def checked_path(value):
    path = Path(value).expanduser().absolute()
    if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)()
           for p in (path, *path.parents)):
        raise ValueError(f"Linked paths are unsupported: {path}")
    if not path.is_file():
        raise ValueError(f"Regular trace file not found: {path}")
    return path.resolve()


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f"Non-finite JSON constant: {value}")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def issue(source, line, code):
    source["issues"].append({"line": line, "code": code})


def tokens(value, source, line, pointer):
    result = empty_tokens()
    if value is None:
        return result
    if not isinstance(value, dict):
        issue(source, line, "usage_not_object")
        source["invalid"] = True
        return result
    unknown = sorted(set(value) - set(TOKEN_FIELDS))
    if unknown:
        source["unknown_usage_fields"].append({"line": line, "path": pointer, "fields": unknown})
    for field in TOKEN_FIELDS:
        count = value.get(field)
        if count is None:
            continue
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            issue(source, line, f"invalid_usage_field:{field}")
            source["invalid"] = True
        else:
            result[field] = count
    return result


def field_sums(records, complete=False):
    result = empty_tokens()
    for field in TOKEN_FIELDS:
        values = [record["tokens"][field] for record in records]
        known = [value for value in values if value is not None]
        if known and (not complete or len(known) == len(values)):
            result[field] = sum(known)
    return result


def cli_metrics(events, source):
    source["usage"]["basis"] = "per_turn"
    active, turn, last_end = False, 0, None
    tools, ids, lifecycle_events = {}, {}, {}
    counts = Counter()
    for line, event in events:
        kind = event["type"]
        event_id = event.get("event_id")
        if isinstance(event_id, str) and event_id:
            key, encoded = (kind, event_id), canonical(event)
            if key in ids:
                if ids[key][1] != encoded:
                    issue(source, line, "conflicting_event_id")
                    source["invalid"] = True
                else:
                    source["duplicate_events"].append({"line": line, "original_line": ids[key][0]})
                continue
            ids[key] = (line, encoded)
        if kind == "thread.started":
            session_id = event.get("thread_id")
            if not isinstance(session_id, str) or not session_id:
                issue(source, line, "missing_thread_id")
            elif source["session_id"] is None:
                source["session_id"] = session_id
            elif source["session_id"] != session_id:
                issue(source, line, "multiple_sessions_in_file")
                source["invalid"] = True
            else:
                issue(source, line, "repeated_thread_start")
        elif kind == "turn.started":
            if active:
                issue(source, line, "turn_started_before_previous_end")
            active, turn = True, turn + 1
            counts["turns_started"] += 1
        elif kind in ("turn.completed", "turn.failed"):
            encoded = canonical(event)
            if not active and last_end is not None and last_end[1] == encoded:
                source["duplicate_events"].append({"line": line, "original_line": last_end[0]})
                continue
            if not active:
                issue(source, line, "turn_end_without_start")
            counts["turns_completed" if kind == "turn.completed" else "turns_failed"] += 1
            if kind == "turn.failed":
                source["failure_lines"].append(line)
            source["usage"]["records"].append({
                "line": line, "event_type": kind, "turn": turn or None,
                "turn_id": event.get("turn_id") if isinstance(event.get("turn_id"), str) else None,
                "tokens": tokens(event.get("usage"), source, line, "usage"),
            })
            active, last_end = False, (line, encoded)
        elif kind == "error":
            counts["error_events"] += 1
            source["failure_lines"].append(line)
        elif kind in {"item.started", "item.updated", "item.completed"}:
            item = event.get("item")
            if not isinstance(item, dict):
                issue(source, line, "item_not_object")
                source["invalid"] = True
                continue
            item_type, item_id = item.get("type"), item.get("id")
            if not isinstance(item_type, str):
                issue(source, line, "item_type_not_string")
                source["invalid"] = True
                continue
            if isinstance(item_id, str) and item_id:
                duplicate_key, encoded = ((turn, item_id), kind), canonical(event)
                if duplicate_key in lifecycle_events and lifecycle_events[duplicate_key][1] == encoded:
                    source["duplicate_events"].append({"line": line, "original_line": lifecycle_events[duplicate_key][0]})
                    continue
                lifecycle_events[duplicate_key] = (line, encoded)
            if item_type not in TOOL_TYPES:
                if item_type == "error" and kind == "item.completed":
                    counts["error_items"] += 1
                    source["failure_lines"].append(line)
                elif item_type not in OTHER_ITEM_TYPES:
                    source["unknown_events"].append({"line": line, "type": kind, "item_type": item_type})
                continue
            if not active:
                issue(source, line, "tool_event_outside_turn")
            if not isinstance(item_id, str) or not item_id:
                issue(source, line, "tool_item_missing_id")
                continue
            key = (turn, item_id)
            tool = tools.setdefault(key, {"turn": turn or None, "id": item_id, "type": item_type,
                                          "lines": [], "started": False, "completed": False,
                                          "explicit_failure": False, "failure_lines": []})
            tool["lines"].append(line)
            if tool["type"] != item_type or (tool["completed"] and kind == "item.completed"):
                issue(source, line, "conflicting_tool_lifecycle")
                source["invalid"] = True
            tool["started"] |= kind == "item.started"
            tool["completed"] |= kind == "item.completed"
            exit_code, error = item.get("exit_code"), item.get("error")
            failed = (item.get("status") in ("failed", "declined") or
                      (isinstance(error, (dict, str)) and bool(error)) or
                      (isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0))
            tool["explicit_failure"] |= failed
            if failed:
                tool["failure_lines"].append(line)
        else:
            source["unknown_events"].append({"line": line, "type": kind})
            if "usage" in event:
                issue(source, line, "usage_on_unsupported_event")
    if active:
        issue(source, None, "unfinished_turn")
    if not turn:
        issue(source, None, "no_turn_start")
    if source["session_id"] is None:
        issue(source, None, "no_session_identity")
    source["tools"] = list(tools.values())
    for name in ("turns_started", "turns_completed", "turns_failed", "error_events", "error_items"):
        source["counters"][name] = counts[name]
    source["counters"].update(
        tool_lifecycles_observed=len(tools),
        tool_completions_observed=sum(t["completed"] for t in tools.values()),
        tool_failures_explicit=sum(t["explicit_failure"] for t in tools.values()),
        tool_lifecycles_unfinished=sum(not t["completed"] for t in tools.values()),
        tool_completions_without_start=sum(t["completed"] and not t["started"] for t in tools.values()))
    if any(not t["completed"] for t in tools.values()):
        issue(source, None, "unfinished_tool_lifecycle")


def session_metrics(events, source):
    source["usage"]["basis"] = "cumulative_snapshot"
    previous, monotonic = empty_tokens(), True
    seen_ids = {}
    for line, event in events:
        kind, payload = event["type"], event.get("payload")
        if not isinstance(payload, dict):
            issue(source, line, "session_payload_not_object")
            source["invalid"] = True
            continue
        if kind == "session_meta":
            session_id = payload.get("id")
            if not isinstance(session_id, str) or not session_id:
                issue(source, line, "missing_session_id")
            elif source["session_id"] is None:
                source["session_id"] = session_id
            elif source["session_id"] != session_id:
                issue(source, line, "multiple_sessions_in_file")
                source["invalid"] = True
        elif kind == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info")
            if info is None:  # Some native events only report rate limits.
                continue
            if not isinstance(info, dict):
                issue(source, line, "token_info_not_object")
                source["invalid"] = True
                continue
            value = tokens(info.get("total_token_usage"), source, line, "payload.info.total_token_usage")
            event_id = event.get("event_id")
            if isinstance(event_id, str) and event_id:
                if event_id in seen_ids:
                    first_line, first_value = seen_ids[event_id]
                    if first_value != canonical(event):
                        issue(source, line, "conflicting_event_id")
                        source["invalid"] = True
                    else:
                        source["duplicate_events"].append({"line": line, "original_line": first_line})
                    continue
                seen_ids[event_id] = (line, canonical(event))
            for field in TOKEN_FIELDS:
                if value[field] is not None:
                    if previous[field] is not None and value[field] < previous[field]:
                        issue(source, line, f"cumulative_decreased:{field}")
                        monotonic = False
                    previous[field] = value[field]
            source["usage"]["records"].append({"line": line, "event_type": "token_count", "tokens": value})
        elif kind not in ("event_msg", "response_item", "turn_context"):
            source["unknown_events"].append({"line": line, "type": kind})
    source["usage"]["cumulative_monotonic"] = monotonic if source["usage"]["records"] else None
    if source["session_id"] is None:
        issue(source, None, "no_session_identity")


def inspect_file(path, role):
    path = checked_path(path)
    raw = path.read_bytes()
    source = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "role": role,
              "adapter": None, "session_id": None, "status": "observed", "invalid": False,
              "line_count": 0, "event_count": 0, "duplicate_events": [], "issues": [],
              "unknown_events": [], "unknown_usage_fields": [], "failure_lines": [], "tools": [],
              "counters": dict.fromkeys(("turns_started", "turns_completed", "turns_failed", "error_events",
                  "error_items", "tool_lifecycles_observed", "tool_completions_observed",
                  "tool_failures_explicit", "tool_lifecycles_unfinished", "tool_completions_without_start",
                  "retry_events")), "usage": {
                  "basis": None, "status": "unreported", "records": [], "observed_sum": None, "complete_sum": None,
                  "latest_cumulative": None, "cumulative_monotonic": None}}
    events = []
    try:
        for line, text in enumerate(raw.decode("utf-8").splitlines(), 1):
            source["line_count"] = line
            if not text.strip():
                continue
            try:
                event = json.loads(text, object_pairs_hook=unique_keys, parse_constant=reject_constant)
                if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                    raise ValueError("Event must be an object with a type")
                events.append((line, event))
            except (ValueError, json.JSONDecodeError):
                issue(source, line, "invalid_json_event")
                source["invalid"] = True
    except UnicodeDecodeError:
        issue(source, None, "not_utf8")
        source["invalid"] = True
    source["event_count"] = len(events)
    cli = any(event["type"] in CLI_TYPES for _, event in events)
    session = any(event["type"] == "session_meta" or
                  (event["type"] == "event_msg" and isinstance(event.get("payload"), dict)
                   and event["payload"].get("type") == "token_count") for _, event in events)
    if not source["invalid"] and cli != session:
        source["adapter"] = "codex-cli" if cli else "codex-session"
        (cli_metrics if cli else session_metrics)(events, source)
    elif not source["invalid"]:
        source["status"] = "unsupported"
        issue(source, None, "mixed_or_unsupported_format" if events else "empty_trace")
    records = source["usage"]["records"]
    source["usage"]["field_coverage"] = {
        field: {"reported": sum(r["tokens"][field] is not None for r in records), "records": len(records)}
        for field in TOKEN_FIELDS}
    if source["invalid"]:
        source["status"] = "invalid"
        source["usage"]["status"] = "invalid"
    elif source["status"] != "unsupported":
        if source["usage"]["basis"] == "per_turn":
            source["usage"]["observed_sum"] = field_sums(records)
            source["usage"]["complete_sum"] = field_sums(records, complete=True) if not source["issues"] else empty_tokens()
        elif records:
            source["usage"]["latest_cumulative"] = records[-1]["tokens"]
        required = ("input_tokens", "cached_input_tokens", "output_tokens")
        if any(any(r["tokens"][f] is not None for f in TOKEN_FIELDS) for r in records):
            source["usage"]["status"] = ("reported" if all(r["tokens"][f] is not None for r in records for f in required)
                                            else "partial")
        if (source["issues"] or source["unknown_events"] or source["unknown_usage_fields"]
                or source["usage"]["status"] != "reported"):
            source["status"] = "partial"
    else:
        source["usage"]["status"] = "unsupported"
    del source["invalid"]
    return source


def aggregate(sources, role):
    selected = [s for s in sources if s["role"] == role and "duplicate_file_of" not in s]
    sessions = [s["session_id"] for s in selected]
    overlap_unknown = len(selected) > 1 and (None in sessions or len(set(sessions)) != len(sessions))
    usable = all(s["status"] not in ("invalid", "unsupported") for s in selected)
    per_turn = [s for s in selected if s["usage"]["basis"] == "per_turn"]
    result = {"files": len(selected), "overlap_status": "unknown" if overlap_unknown else "no_same_session_files",
              "observed_per_turn_sum": empty_tokens(), "complete_per_turn_sum": empty_tokens(),
              "cumulative_sources": [{"path": s["path"], "session_id": s["session_id"],
                   "latest": s["usage"]["latest_cumulative"], "monotonic": s["usage"]["cumulative_monotonic"]}
                   for s in selected if s["usage"]["basis"] == "cumulative_snapshot"]}
    if usable and not overlap_unknown:
        result["observed_per_turn_sum"] = field_sums(
            [{"tokens": s["usage"]["observed_sum"]} for s in per_turn])
        if len(per_turn) == len(selected):
            result["complete_per_turn_sum"] = field_sums(
                [{"tokens": s["usage"]["complete_sum"]} for s in per_turn], complete=True)
    return result


def summarize(traces, child_traces=()):
    sources, hashes = [], {}
    for role, paths in (("main", traces), ("child", child_traces)):
        for path in paths:
            source = inspect_file(path, role)
            if source["sha256"] in hashes:
                previous = hashes[source["sha256"]]
                source["duplicate_file_of"] = previous["path"]
                if source["role"] != previous["role"]:
                    issue(source, None, "same_file_declared_main_and_child")
                source["status"] = "duplicate"
            else:
                hashes[source["sha256"]] = source
            sources.append(source)
    bad = any(s["status"] in ("invalid", "unsupported") for s in sources)
    partial = any(s["status"] == "partial" or s["issues"] for s in sources)
    return {"schema": 1, "status": "invalid" if bad else "partial" if partial else "observed",
            "scope": "Reported usage in supplied traces; no execution, literary, discovery, or billing certification.",
            "sources": sources, "main": aggregate(sources, "main"), "child": aggregate(sources, "child"),
            "child_coverage": {"role_source": "caller_declared", "status": "unknown",
                 "provided_distinct_files": sum(s["role"] == "child" and "duplicate_file_of" not in s for s in sources),
                 "parent_relationship": "unverified", "main_includes_child_usage": "unknown",
                 "full_task_total": None}, "limits": LIMITS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trace", action="append", required=True, help="Main JSONL file; repeat for additional files")
    parser.add_argument("--child-trace", action="append", default=[], help="Caller-labelled child JSONL; never added to main")
    parser.add_argument("--output", help="Exclusively create a JSON report; default writes to stdout")
    args = parser.parse_args(argv)
    try:
        report = summarize(args.trace, args.child_trace)
        content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            output = Path(args.output).expanduser().absolute()
            if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)()
                   for p in (output, *output.parents)):
                raise ValueError("Linked output paths are unsupported")
            with output.open("x", encoding="utf-8") as stream:
                stream.write(content)
        else:
            sys.stdout.write(content)
        # Zero means parsing completed, including partial/unknown evidence.
        return 1 if report["status"] == "invalid" else 0
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
