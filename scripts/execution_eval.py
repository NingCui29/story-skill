#!/usr/bin/env python3
"""Freeze and inspect a local skill trial; never run a model or certify its review."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys

SCOPE = "File integrity and execution evidence only; semantic review remains required."
CASE_FIELDS = ("id", "prompt", "invocation", "allowed_changes", "required_outputs", "review_required")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_path(value, directory=False):
    """Reject links in the supplied path, including an existing parent directory."""
    path = Path(value).expanduser().absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise ValueError(f"Symbolic links are not supported: {part}")
    if directory and not path.is_dir():
        raise ValueError(f"Directory not found: {path}")
    return path.resolve()


def within(path, root):
    return path == root or root in path.parents


def relative_path(value, pattern=False):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\x00" in value:
        raise ValueError(f"Invalid relative path: {value!r}")
    name = value[:-3] if pattern and value.endswith("/**") else value
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts) or any("*" in p or "?" in p for p in parts):
        raise ValueError(f"Invalid relative path: {value!r}")
    if PurePosixPath(name).is_absolute():
        raise ValueError(f"Absolute paths are not allowed: {value!r}")
    return value


def validate_case(case):
    if not isinstance(case, dict) or any(key not in case for key in CASE_FIELDS):
        raise ValueError(f"Case requires: {', '.join(CASE_FIELDS)}")
    for field in ("id", "prompt"):
        if not isinstance(case[field], str) or not case[field].strip():
            raise ValueError(f"Case {field} must be a nonempty string")
    invocation = case["invocation"]
    invocation_mode = invocation.get("mode") if isinstance(invocation, dict) else invocation
    if not isinstance(invocation_mode, str) or not invocation_mode.strip():
        raise ValueError("Case invocation must be a nonempty string or an object with a nonempty mode")
    for field in ("allowed_changes", "required_outputs", "review_required"):
        values = case[field]
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
            raise ValueError(f"Case {field} must be an array of nonempty strings")
        if len(values) != len(set(values)):
            raise ValueError(f"Duplicate case {field}")
    for value in case["allowed_changes"]:
        relative_path(value, pattern=True)
    for value in case["required_outputs"]:
        relative_path(value)
    return case


def load_json(path):
    path = safe_path(path)
    if not path.is_file():
        raise ValueError(f"Regular input file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8")), path


def snapshot(root):
    """Inspect only the requested tree. File contents are represented by hashes."""
    files, directories = {}, []
    def visit(folder):
        with os.scandir(folder) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                mode = entry.stat(follow_symlinks=False).st_mode
                attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
                reparse = attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                if stat.S_ISLNK(mode) or reparse or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise ValueError(f"Linked or special tree entry: {path}")
                if (stat.S_ISDIR(mode) and entry.name == "__pycache__") or (stat.S_ISREG(mode) and path.suffix == ".pyc"):
                    continue
                if stat.S_ISDIR(mode):
                    directories.append(relative)
                    visit(path)
                else:
                    files[relative] = sha(path.read_bytes())
    visit(root)
    return {"files": files, "directories": sorted(directories)}


def validate_snapshot(value):
    if not isinstance(value, dict) or not isinstance(value.get("files"), dict) or not isinstance(value.get("directories"), list):
        raise ValueError("Invalid baseline snapshot")
    for name, digest in value["files"].items():
        relative_path(name)
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"Invalid baseline file hash: {name}")
    for name in value["directories"]:
        relative_path(name)
    return value


def roots_for(workspace, skills):
    workspace, skills = safe_path(workspace, True), safe_path(skills, True)
    if within(workspace, skills) or within(skills, workspace):
        raise ValueError("Workspace and skills root must not overlap")
    return workspace, skills


def output_path(output, roots, protected=()):
    output = safe_path(output)
    if any(within(output, root) for root in roots):
        raise ValueError("Evidence output must be outside workspace and skills trees")
    if output in protected or output.exists():
        raise ValueError(f"Refusing to overwrite evidence or input: {output}")
    if not output.parent.is_dir():
        raise ValueError(f"Evidence output parent must already exist: {output.parent}")
    return output


def save_new(path, value):
    # Exclusive creation also protects an existing file if it appears after validation.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def changes(before, after):
    result = []
    for name in sorted(before["files"].keys() | after["files"].keys()):
        old, new = before["files"].get(name), after["files"].get(name)
        if old != new:
            result.append({"path": name, "kind": "file", "change": "added" if old is None else "deleted" if new is None else "modified"})
    old_dirs, new_dirs = set(before["directories"]), set(after["directories"])
    for name in sorted(old_dirs ^ new_dirs):
        result.append({"path": name, "kind": "directory", "change": "added" if name in new_dirs else "deleted"})
    return result


def allowed(change, patterns):
    name = change["path"]
    for pattern in patterns:
        base = pattern[:-3] if pattern.endswith("/**") else pattern
        if name == base or (pattern.endswith("/**") and name.startswith(base + "/")):
            return True
        # Creating/removing a parent directory of an explicitly permitted file is incidental.
        if change["kind"] == "directory" and base.startswith(name + "/"):
            return True
    return False


def inspect_trace(path):
    if path is None:
        return {"status": "unverified", "reason": "No host execution trace supplied"}
    path = safe_path(path)
    if not path.is_file():
        raise ValueError(f"Regular trace file not found: {path}")
    raw = path.read_bytes()
    result = {"path": str(path), "sha256": sha(raw), "event_count": 0,
              "completed_turns": [], "failed_turns": [], "error_events": [], "failed_commands": [],
              "completed_commands": 0, "pending_items": [], "pending_commands": [], "issues": []}
    thread_started, active_turn, pending_items = False, False, {}
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        result.update(status="incomplete", issues=[{"reason": "Trace is not UTF-8"}])
        return result
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise ValueError("Event must be an object with a type")
        except (ValueError, json.JSONDecodeError) as error:
            result["issues"].append({"line": number, "reason": str(error)})
            continue
        result["event_count"] += 1
        if event["type"] == "thread.started":
            if thread_started or not isinstance(event.get("thread_id"), str) or not event["thread_id"].strip():
                result["issues"].append({"line": number, "reason": "Invalid or duplicate thread.started"})
            else:
                thread_started = True
        elif event["type"] == "turn.started":
            if not thread_started or active_turn:
                result["issues"].append({"line": number, "reason": "Turn started without a thread or before previous turn ended"})
            active_turn = True
        elif event["type"] in ("turn.completed", "turn.failed"):
            if not active_turn:
                result["issues"].append({"line": number, "reason": "Turn ended without turn.started"})
            if pending_items:
                result["issues"].append({"line": number, "reason": "Turn ended with unfinished items"})
            active_turn = False
            if event["type"] == "turn.completed":
                result["completed_turns"].append(number)
            else:
                result["failed_turns"].append({"line": number, "error": event.get("error")})
        elif event["type"] == "error":
            result["error_events"].append({"line": number, "message": event.get("message")})
        elif event["type"] in ("item.started", "item.completed"):
            item = event.get("item")
            if not isinstance(item, dict) or any(not isinstance(item.get(key), str) or not item[key].strip() for key in ("id", "type")):
                result["issues"].append({"line": number, "reason": "Item must be an object with nonempty id and type"})
                continue
            identifier, item_type = item["id"], item["type"]
            if event["type"] == "item.started":
                if not active_turn:
                    result["issues"].append({"line": number, "reason": "Item started outside an active turn"})
                if identifier in pending_items:
                    result["issues"].append({"line": number, "reason": "Started item has duplicate id"})
                else:
                    pending_items[identifier] = {"line": number, "id": identifier, "type": item_type}
                    if item_type == "command_execution":
                        pending_items[identifier]["command"] = item.get("command")
                continue
            started = pending_items.pop(identifier, None)
            if started and started["type"] != item_type:
                result["issues"].append({"line": number, "reason": "Completed item type differs from its start"})
            # Native error/message items can complete without a start or outside a turn.
            if not active_turn and item_type in ("command_execution", "mcp_tool_call", "collab_tool_call", "web_search", "todo_list"):
                result["issues"].append({"line": number, "reason": "Execution item completed outside an active turn"})
            if item_type == "command_execution":
                code, command, status = item.get("exit_code"), item.get("command"), item.get("status")
                declined_or_failed = code is None and status in ("declined", "failed")
                if not isinstance(command, str) or (type(code) is not int and not declined_or_failed):
                    result["issues"].append({"line": number, "reason": "Completed command lacks command or a valid terminal result"})
                    continue
                result["completed_commands"] += 1
                if code != 0 or status in ("declined", "failed"):
                    result["failed_commands"].append({"line": number, "command": command, "exit_code": code,
                                                      "status": status, "outcome": "not_executed" if status == "declined" else "failed"})
    if not result["completed_turns"]:
        result["issues"].append({"reason": "No turn.completed event"})
    if not thread_started:
        result["issues"].append({"reason": "No valid thread.started event"})
    if active_turn:
        result["issues"].append({"reason": "Started turn has no ending event"})
    result["pending_items"] = list(pending_items.values())
    result["pending_commands"] = [item for item in result["pending_items"] if item["type"] == "command_execution"]
    if pending_items:
        result["issues"].append({"reason": "Started item has no completion event"})
    result["status"] = "failed" if result["failed_turns"] or result["error_events"] else "incomplete" if result["issues"] else "complete"
    return result


def start(args):
    case, case_path = load_json(args.case_file)
    validate_case(case)
    workspace, skills = roots_for(args.workspace, args.skills_root)
    output = output_path(args.output, (workspace, skills), (case_path,))
    baseline = {"schema": 1, "scope": SCOPE, "case": case,
                "case_source": {"path": str(case_path), "sha256": sha(case_path.read_bytes())},
                "workspace": str(workspace), "skills_root": str(skills),
                "before": {"workspace": snapshot(workspace), "skills": snapshot(skills)}}
    save_new(output, baseline)
    return {"mechanical_ok": True, "scope": SCOPE, "baseline": str(output)}, 0


def check(args):
    baseline, baseline_path = load_json(args.baseline)
    if not isinstance(baseline, dict) or type(baseline.get("schema")) is not int or baseline["schema"] != 1:
        raise ValueError("Unsupported baseline schema")
    case = validate_case(baseline["case"])
    workspace, skills = roots_for(baseline["workspace"], baseline["skills_root"])
    for name in ("workspace", "skills"):
        validate_snapshot(baseline["before"][name])
    protected = [baseline_path]
    if args.trace is not None:
        protected.append(safe_path(args.trace))
    output = output_path(args.output, (workspace, skills), protected)
    after = {"workspace": snapshot(workspace), "skills": snapshot(skills)}
    workspace_changes = changes(baseline["before"]["workspace"], after["workspace"])
    unauthorized = [item for item in workspace_changes if not allowed(item, case["allowed_changes"])]
    skill_changes = changes(baseline["before"]["skills"], after["skills"])
    missing = [name for name in case["required_outputs"] if name not in after["workspace"]["files"]]
    empty = [name for name in case["required_outputs"] if after["workspace"]["files"].get(name) == sha(b"")]
    checks = {"workspace_changes": workspace_changes, "unauthorized_changes": unauthorized,
              "skill_changes": skill_changes, "missing_outputs": missing, "empty_outputs": empty}
    mechanical_ok = not (unauthorized or skill_changes or missing or empty)
    trace = inspect_trace(args.trace)
    execution_failed = not mechanical_ok or trace["status"] in ("failed", "incomplete")
    report = {"schema": 1, "case_id": case["id"], "scope": SCOPE,
              "baseline": {"path": str(baseline_path), "sha256": sha(baseline_path.read_bytes())},
              "mechanical_ok": mechanical_ok, "checks": checks, "trace": trace,
              "execution_status": "failed" if execution_failed else "needs_review",
              "review_required": case["review_required"], "after": after}
    save_new(output, report)
    code = 1 if execution_failed else 2 if trace["status"] == "unverified" else 0
    return report, code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    begin = commands.add_parser("start")
    for option in ("case-file", "workspace", "skills-root", "output"):
        begin.add_argument("--" + option, required=True)
    inspect = commands.add_parser("check")
    inspect.add_argument("--baseline", required=True)
    inspect.add_argument("--trace")
    inspect.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        result, code = start(args) if args.command == "start" else check(args)
    except (ValueError, OSError, KeyError, TypeError) as error:
        result, code = {"mechanical_ok": False, "scope": SCOPE, "error": str(error)}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
