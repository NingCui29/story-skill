#!/usr/bin/env python3
"""Inspect selected native read results and task links, without running a model.

Phases are explicit developer annotations, not inferred from model prose.
An observed excerpt is not proof of a whole-file read, comprehension, task
coverage, independent review, token savings or a complete main/child total.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


def _module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


review = _module("activity_review", "execution_review.py")
metrics = _module("activity_metrics", "execution_metrics.py")
PHASES = ("preparation", "drafting", "review", "commit", "delivery", "other")
MAX_TRACE_BYTES = 128 * 1024 * 1024
MAX_MAP_BYTES = 4 * 1024 * 1024
MAX_SOURCE_BYTES = 32 * 1024 * 1024
LIMITS = [
    "Selected cited excerpts only, not all reads, full-file coverage or model understanding.",
    "Phases are developer annotations, not native host phases or automatically inferred behavior.",
    "Paths must be literal absolute operands, or resolve from a captured native item cwd; caller-supplied cwd is not proof.",
    "Repeated excerpt counts describe repeated evidence only, not redundant reasoning or wasted tokens.",
    "Native task links bind captured sender/receiver IDs to supplied session IDs; they do not prove task scope or independent review.",
    "Usage inclusion and completeness are unknown even when a task link is observed; full_task_total stays null.",
    "Hashes check supplied bytes, not the authenticity of their capture. No cost or literary-quality certification.",
]


def _raw(path, maximum):
    path = metrics.checked_path(path)
    if path.stat().st_size > maximum:
        raise ValueError(f"Input exceeds {maximum} bytes: {path}")
    with path.open("rb") as handle:
        raw = handle.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError(f"Input exceeds {maximum} bytes: {path}")
    return path, raw


def _json(raw):
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=metrics.unique_keys,
                      parse_constant=metrics.reject_constant)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _reference(value, label):
    if (not isinstance(value, dict) or set(value) != {"path", "sha256"}
            or not isinstance(value["path"], str) or not Path(value["path"]).is_absolute()
            or not isinstance(value["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"])):
        raise ValueError(f"{label} requires an absolute path and SHA-256")


def load_mapping(path):
    _, raw = _raw(path, MAX_MAP_BYTES)
    value = _json(raw)
    if (not isinstance(value, dict) or set(value) != {"schema", "annotations"}
            or type(value["schema"]) is not int or value["schema"] != 1
            or not isinstance(value["annotations"], list) or len(value["annotations"]) > 10000):
        raise ValueError("Activity map requires schema=1 and at most 10000 annotations")
    return value


def _inspect_annotation(annotation, traces):
    if not isinstance(annotation, dict) or set(annotation) != {"trace", "line", "phase", "source", "quote"}:
        raise ValueError("Annotation requires trace, line, phase, source and quote")
    _reference(annotation["trace"], "trace")
    _reference(annotation["source"], "source")
    if annotation["phase"] not in PHASES:
        raise ValueError("Unsupported phase")
    if type(annotation["line"]) is not int or annotation["line"] < 1:
        raise ValueError("line must be a positive integer")
    quote = annotation["quote"]
    if not isinstance(quote, str) or not quote.strip() or len(quote) > 8000:
        raise ValueError("quote must be a nonempty excerpt of at most 8000 characters")
    trace_path = str(metrics.checked_path(annotation["trace"]["path"]))
    captured = traces.get(trace_path)
    if captured is None or captured["sha256"] != annotation["trace"]["sha256"]:
        raise ValueError("Read annotation references an absent or changed trace")
    source = captured["metrics"]
    if source["adapter"] != "codex-cli" or source["status"] in ("invalid", "unsupported"):
        raise ValueError("Native CLI events cannot be verified from this trace")
    number = annotation["line"]
    event = captured["events"].get(number)
    if not event or not review.completed_result(event):
        raise ValueError("Cited event is not a successful native returned result")
    item = event["item"]
    output = review.output_text(item)
    if quote not in output:
        raise ValueError("Excerpt is absent from the native returned output")
    target, raw = _raw(annotation["source"]["path"], MAX_SOURCE_BYTES)
    if _sha(raw) != annotation["source"]["sha256"]:
        raise ValueError("Read source version has changed; cite a preserved execution snapshot")
    if quote not in raw.decode("utf-8-sig"):
        raise ValueError("Excerpt is absent from the cited source version")
    native_cwd = item.get("cwd")
    cwd = native_cwd if isinstance(native_cwd, str) and Path(native_cwd).is_absolute() else ""
    known = (review.read_command(item.get("command"), target, cwd) if item.get("type") == "command_execution"
             else review.read_tool(item, target, cwd))
    if not known:
        raise ValueError("No supported literal read of this source; relative operands require captured native cwd")
    original_line = captured["duplicates"].get(number, number)
    return {**annotation, "status": "excerpt_observed", "trace_line": original_line,
            "source": {"path": str(target), "sha256": annotation["source"]["sha256"]},
            "quote_sha256": _sha(quote.encode("utf-8")), "quoted_characters": len(quote),
            "phase_origin": "developer_annotation", "coverage": "cited_excerpt_only"}


def _task_links(traces):
    sessions = defaultdict(list)
    for captured in traces.values():
        source = captured["metrics"]
        if source["session_id"] and source["status"] not in ("invalid", "unsupported", "duplicate"):
            sessions[source["session_id"]].append({"path": source["path"], "sha256": source["sha256"],
                                                   "caller_role": source["role"]})
    links, seen = [], set()
    for captured in traces.values():
        source = captured["metrics"]
        if source["adapter"] != "codex-cli" or source["status"] in ("invalid", "unsupported", "duplicate"):
            continue
        for number, event in captured["events"].items():
            item = event.get("item", {})
            if (event.get("type") != "item.completed" or not isinstance(item, dict)
                    or item.get("type") != "collab_tool_call" or item.get("tool") not in ("spawn_agent", "send_input")
                    or item.get("status") != "completed" or item.get("sender_thread_id") != source["session_id"]
                    or not isinstance(item.get("receiver_thread_ids"), list)):
                continue
            for receiver in item["receiver_thread_ids"]:
                if not isinstance(receiver, str) or not receiver or receiver == source["session_id"]:
                    continue
                key = (source["sha256"], captured["duplicates"].get(number, number), receiver)
                if key in seen:
                    continue
                seen.add(key)
                matched = sessions.get(receiver, [])
                links.append({"status": "native_session_link" if matched else "child_trace_missing",
                              "sender_session_id": source["session_id"], "receiver_session_id": receiver,
                              "tool": item["tool"], "dispatch": {"path": source["path"], "sha256": source["sha256"], "line": number},
                              "child_traces": matched, "usage_inclusion": "unknown", "task_scope": "unverified"})
    return links


def analyze(trace_paths, child_paths=(), mapping=None):
    all_paths = list(trace_paths) + list(child_paths)
    if not trace_paths or len(all_paths) > 100:
        raise ValueError("Provide one main trace and at most 100 total trace files")
    for path in all_paths:
        _raw(path, MAX_TRACE_BYTES)
    usage = metrics.summarize(trace_paths, child_paths)
    traces = {}
    for source in usage["sources"]:
        path, raw = _raw(source["path"], MAX_TRACE_BYTES)
        if _sha(raw) != source["sha256"]:
            raise ValueError("Trace changed during inspection")
        events = {}
        if source["status"] not in ("invalid", "unsupported"):
            for number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
                if line.strip():
                    events[number] = _json(line.encode("utf-8"))
        # A repeated supplied file is the same capture, never a second execution.
        if str(path) in traces:
            continue
        traces[str(path)] = {"sha256": source["sha256"], "events": events, "metrics": source,
                            "duplicates": {r["line"]: r["original_line"] for r in source["duplicate_events"]}}
    if mapping is None:
        mapping = {"schema": 1, "annotations": []}
    if (not isinstance(mapping, dict) or set(mapping) != {"schema", "annotations"}
            or type(mapping["schema"]) is not int or mapping["schema"] != 1
            or not isinstance(mapping["annotations"], list) or len(mapping["annotations"]) > 10000):
        raise ValueError("Invalid activity mapping")
    activities, mapped, excerpts, event_phases, phase_counts = [], {}, {}, {}, defaultdict(Counter)
    for index, annotation in enumerate(mapping["annotations"], 1):
        try:
            item = _inspect_annotation(annotation, traces)
            event_key = (item["trace"]["sha256"], item["trace_line"])
            if event_key in event_phases and event_phases[event_key] != item["phase"]:
                raise ValueError("Same native read event has conflicting phase annotations")
            event_phases[event_key] = item["phase"]
            key = (item["trace"]["sha256"], item["trace_line"], item["source"]["path"], item["source"]["sha256"], item["quote_sha256"])
            if key in mapped:
                if mapped[key]["phase"] != item["phase"]:
                    raise ValueError("Same read excerpt has conflicting phase annotations")
                item.update(status="duplicate_annotation", duplicate_of=mapped[key]["annotation"])
            else:
                item["annotation"] = index
                mapped[key] = item
                excerpt = (item["source"]["path"], item["source"]["sha256"], item["quote_sha256"])
                if excerpt in excerpts:
                    item["repeated_excerpt_of"] = excerpts[excerpt]
                    phase_counts[item["phase"]]["repeated_excerpts"] += 1
                else:
                    excerpts[excerpt] = index
                phase_counts[item["phase"]]["observed_excerpts"] += 1
                phase_counts[item["phase"]]["quoted_characters"] += item["quoted_characters"]
            activities.append({"annotation": index, **item})
        except (ValueError, OSError, UnicodeError, TypeError) as error:
            activities.append({"annotation": index, "status": "unverified", "reason": str(error)})
    links = _task_links(traces)
    partial = any(a["status"] == "unverified" for a in activities) or usage["status"] != "observed"
    return {"schema": 1, "status": "partial" if partial else "selected_evidence_checked",
            "activities": activities, "phases": {key: dict(value) for key, value in sorted(phase_counts.items())},
            "native_task_links": links, "usage": usage, "full_task_total": None, "limits": LIMITS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", action="append", required=True)
    parser.add_argument("--child-trace", action="append", default=[])
    parser.add_argument("--map", dest="mapping", help="Optional version-bound developer annotations")
    parser.add_argument("--output", help="Exclusively create a JSON report; existing files are never replaced")
    args = parser.parse_args(argv)
    try:
        result = analyze(args.trace, args.child_trace, load_mapping(args.mapping) if args.mapping else None)
        content = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            with Path(args.output).open("x", encoding="utf-8", newline="\n") as output:
                output.write(content)
        else:
            print(content, end="")
        return 1 if result["status"] == "partial" else 0
    except (ValueError, OSError, UnicodeError, RecursionError) as error:
        parser.exit(2, json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    sys.exit(main())
