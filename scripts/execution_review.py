#!/usr/bin/env python3
"""Check references in a developer review; do not run a model or grade fiction.

The record cites the hashes of baseline/check/task files, a launch receipt, and
claims with trace or artifact line references. A traceability result is not an
independent semantic judgement, authenticated reader response, or proof that a
host understood a skill. The files still depend on a trusted capture environment.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import ntpath
from pathlib import Path, PurePosixPath, PureWindowsPath
import posixpath
import re
import shlex
import sys

_SPEC = importlib.util.spec_from_file_location("execution_review_eval", Path(__file__).with_name("execution_eval.py"))
evaluation = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(evaluation)

SCOPE = "Reference integrity only; literary quality, skill understanding and human reception remain unverified."
STATES = {"observed", "failed", "unverified"}
KINDS = {"skill_read", "behavior", "artifact", "content", "independent_review", "other_agent_return", "reader_response"}
REVIEW_ASSIGNMENT = "STORY_REVIEW_ASSIGNMENT "
REVIEW_RETURN = "STORY_REVIEW_RETURN "
REVIEW_LINK_SCOPE = ("Captured assignment, returned message and current input version only; "
                     "actual reading, semantic correctness and literary quality remain unverified.")
SKILL_NAME = re.compile(r"(?<![A-Za-z0-9_-])\$?story[-_]skill(?:[-_](?:analyze|cover|plan|publish|research|review|write))?(?![A-Za-z0-9_-])", re.IGNORECASE)


def file_reference(ref, label):
    if not isinstance(ref, dict) or not isinstance(ref.get("sha256"), str):
        raise ValueError(f"{label} requires path and sha256")
    path = evaluation.safe_path(ref.get("path", ""))
    if not path.is_file():
        raise ValueError(f"{label} is not a regular file: {path}")
    raw = path.read_bytes()
    if evaluation.sha(raw) != ref["sha256"]:
        raise ValueError(f"Stale {label} sha256: {path}")
    return path, raw


def text_lines(raw, label):
    try:
        return raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} must be UTF-8") from error


def quoted_line(lines, evidence):
    line, quote = evidence.get("line"), evidence.get("quote")
    if type(line) is not int or not 1 <= line <= len(lines):
        raise ValueError("Evidence line is outside the referenced file")
    if not isinstance(quote, str) or not quote.strip():
        raise ValueError("Evidence requires a nonempty quote")
    return lines[line - 1], quote


def output_text(item):
    """Extract native returned text, excluding the agent's own completion message."""
    if not isinstance(item, dict):
        return ""
    if item.get("type") == "command_execution":
        value = item.get("aggregated_output")
        return value if isinstance(value, str) else ""
    if item.get("type") == "collab_tool_call" and isinstance(item.get("agents_states"), dict):
        return "\n".join(state["message"] for state in item["agents_states"].values()
                         if isinstance(state, dict) and isinstance(state.get("message"), str))
    result = item.get("result")
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        parts = result.get("content", [])
        if isinstance(parts, list):
            return "\n".join(part["text"] for part in parts
                             if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str))
    return ""


def completed_result(event):
    if event.get("type") != "item.completed" or not isinstance(event.get("item"), dict):
        return False
    item = event["item"]
    if item.get("type") == "command_execution":
        return type(item.get("exit_code")) is int and item["exit_code"] == 0 and item.get("status") == "completed"
    if item.get("type") == "mcp_tool_call":
        result = item.get("result")
        return item.get("status") == "completed" and not item.get("error") and not (
            isinstance(result, dict) and result.get("isError"))
    return False


def literal_read_path(name, target, cwd):
    """Compare a literal operand in the captured filesystem's path flavour.

    Drive-relative and rooted-without-drive Windows paths depend on ambient
    process state, so they cannot identify a read from the recorded cwd alone.
    """
    path_type = PureWindowsPath if isinstance(target, PureWindowsPath) else PurePosixPath
    path, directory = path_type(name), path_type(cwd)
    if path.is_absolute():
        return path == target
    return not path.anchor and directory.is_absolute() and directory / path == target


def read_command(command, target, cwd):
    """Recognise literal cat/head/sed reads only; never execute a command string."""
    try:
        words = shlex.split(command)
        if len(words) == 3 and Path(words[0]).name in {"sh", "bash", "zsh"} and words[1] in {"-c", "-lc"}:
            words = shlex.split(words[2])
    except (ValueError, TypeError):
        return False
    if not words or any(any(char in word for char in ";&|><$`") for word in words):
        return False
    program, args = Path(words[0]).name, words[1:]
    paths = []
    if program == "cat":
        if any(word.startswith("-") and word not in {"--", "-n", "-b", "-s", "-E", "-T", "-v", "-A"} for word in args):
            return False
        paths = [word for word in args if not word.startswith("-")]
    elif program == "head":
        if len(args) >= 2 and args[0] == "-n" and args[1].isdigit():
            args = args[2:]
        elif args and re.fullmatch(r"-\d+", args[0]):
            args = args[1:]
        paths = [word for word in args if word != "--"]
        if any(word.startswith("-") for word in paths):
            return False
    elif program == "sed":
        if args and args[0] == "-n":
            args = args[1:]
        if args and args[0] == "-e":
            args = args[1:]
        if not args or not re.fullmatch(r"\d+(?:,(?:\d+|\$))?p", args[0]):
            return False
        paths = [word for word in args[1:] if word != "--"]
    else:
        return False
    return any(literal_read_path(name, target, cwd) for name in paths)


def full_cat(command, target, cwd):
    try:
        words = shlex.split(command)
        if len(words) == 3 and Path(words[0]).name in {"sh", "bash", "zsh"} and words[1] in {"-c", "-lc"}:
            words = shlex.split(words[2])
    except (ValueError, TypeError):
        return False
    if words and Path(words[0]).name == "cat" and len(words) in (2, 3):
        args = words[1:]
        if args[0] == "--":
            args = args[1:]
        return len(args) == 1 and read_command(command, target, cwd)
    return False


def read_tool(item, target, cwd):
    if item.get("type") != "mcp_tool_call" or item.get("tool") not in {"read_file", "read_text_file"}:
        return False
    arguments = item.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return False
    if not isinstance(arguments, dict) or not isinstance(arguments.get("path"), str):
        return False
    return literal_read_path(arguments["path"], target, cwd)


def independent_return(event, quote, thread_id):
    """Recognise receipt of another agent's text, without certifying its task."""
    item = event.get("item", {})
    if not isinstance(item, dict):
        return False
    receivers, states = item.get("receiver_thread_ids"), item.get("agents_states")
    if event.get("type") != "item.completed" or item.get("type") != "collab_tool_call" or item.get("tool") != "wait" or item.get("status") != "completed":
        return False
    if not thread_id or item.get("sender_thread_id") != thread_id or not isinstance(receivers, list) or not isinstance(states, dict):
        return False
    return any(isinstance(other, str) and other != thread_id and isinstance(states.get(other), dict)
               and states[other].get("status") == "completed" and isinstance(states[other].get("message"), str)
               and quote in states[other]["message"] for other in receivers)


def review_packet(text, prefix):
    """Read one optional handoff line; prose or repeated packets are ambiguous."""
    if not isinstance(text, str):
        return None
    lines = [line[len(prefix):] for line in text.splitlines() if line.startswith(prefix)]
    if len(lines) != 1:
        return None
    try:
        value = json.loads(lines[0])
    except ValueError:
        return None
    return value if isinstance(value, dict) and type(value.get("schema")) is int and value["schema"] == 1 else None


def review_assignment_link(binding, event, ref, trace, workspace):
    """Link native Codex CLI dispatch/return events, never a self-reported pass.

    Native fields/tool names follow codex-rs/exec/src/exec_events.rs:
    https://github.com/openai/codex/blob/main/codex-rs/exec/src/exec_events.rs
    Missing prompt/markers remain unverified. The compact markers only state
    the requested file version and response association; they cannot prove the
    receiver read it. Trace/files still require a trusted capture environment.
    """
    proof = {"status": "unverified", "scope": REVIEW_LINK_SCOPE}

    def insufficient(reason):
        return {**proof, "reason": reason}

    if not isinstance(binding, dict) or type(binding.get("schema")) is not int or binding["schema"] != 1:
        return insufficient("No supported review_assignment reference; another-agent return alone does not identify a reviewed draft")
    dispatch = binding.get("dispatch")
    if not isinstance(dispatch, dict) or dispatch.get("source") != "trace":
        return insufficient("Review assignment requires a native dispatch trace reference")
    if dispatch.get("sha256") != trace["sha256"]:
        raise ValueError("Stale review dispatch trace sha256")
    line, quote = quoted_line(trace["lines"], dispatch)
    dispatch_event = json.loads(line)
    item = dispatch_event.get("item", {}) if isinstance(dispatch_event, dict) else {}
    receiver = binding.get("receiver_thread_id")
    if not isinstance(receiver, str) or not receiver or receiver == trace.get("thread_id"):
        return insufficient("Review receiver must identify another agent")
    if (not isinstance(dispatch_event, dict) or not isinstance(item, dict) or dispatch_event.get("type") != "item.completed"
            or item.get("type") != "collab_tool_call" or item.get("tool") not in {"spawn_agent", "send_input"}
            or item.get("status") != "completed" or item.get("sender_thread_id") != trace.get("thread_id")
            or not isinstance(item.get("receiver_thread_ids"), list) or receiver not in item["receiver_thread_ids"]):
        return insufficient("Referenced event is not a completed native dispatch to this review receiver")
    prompt = item.get("prompt")
    if not isinstance(prompt, str) or quote not in prompt:
        return insufficient("Native dispatch lacks the cited prompt; its content cannot be reconstructed from the review record")
    packet = review_packet(prompt, REVIEW_ASSIGNMENT)
    if not packet or packet.get("task") != "review" or not isinstance(packet.get("assignment_id"), str) or not packet["assignment_id"].strip():
        return insufficient("Native dispatch lacks one supported review assignment packet")
    assigned_input = packet.get("input")
    input_ref = binding.get("input")
    if (not isinstance(assigned_input, dict) or not isinstance(input_ref, dict)
            or assigned_input != {"path": input_ref.get("path"), "sha256": input_ref.get("sha256")}):
        return insufficient("Recorded review input does not match the file/version in the native dispatch")
    name = evaluation.relative_path(input_ref.get("path"))
    path, _ = file_reference({**input_ref, "path": str(workspace / name)}, "assigned review input")
    dispatch_line, return_line = dispatch["line"], ref["line"]
    if dispatch_line >= return_line:
        return insufficient("Review return must follow its dispatch")
    same_assignment = 0
    for raw in trace["lines"][:return_line - 1]:
        if not raw.strip():
            continue
        earlier_event = json.loads(raw)
        earlier = earlier_event.get("item", {}) if isinstance(earlier_event, dict) else {}
        if (isinstance(earlier_event, dict) and isinstance(earlier, dict) and earlier_event.get("type") == "item.completed"
                and earlier.get("type") == "collab_tool_call" and earlier.get("tool") in {"spawn_agent", "send_input"}):
            earlier_packet = review_packet(earlier.get("prompt"), REVIEW_ASSIGNMENT)
            if earlier_packet and earlier_packet.get("assignment_id") == packet["assignment_id"]:
                same_assignment += 1
    if same_assignment != 1:
        return insufficient("Review assignment id is reused in the captured dispatches; cached returns cannot identify the requested task")
    for raw in trace["lines"][dispatch_line:return_line - 1]:
        if not raw.strip():
            continue
        later_event = json.loads(raw)
        later = later_event.get("item", {}) if isinstance(later_event, dict) else {}
        if (isinstance(later_event, dict) and isinstance(later, dict) and later_event.get("type") == "item.completed"
                and later.get("type") == "collab_tool_call" and later.get("tool") in {"spawn_agent", "send_input"}
                and isinstance(later.get("receiver_thread_ids"), list) and receiver in later["receiver_thread_ids"]):
            return insufficient("Another dispatch to this receiver intervened; the earlier review assignment is ambiguous")
    returned = event.get("item", {})
    states = returned.get("agents_states", {}) if isinstance(returned, dict) else {}
    state = states.get(receiver) if isinstance(states, dict) else None
    if (not independent_return(event, ref["quote"], trace.get("thread_id"))
            or receiver not in returned.get("receiver_thread_ids", []) or not isinstance(state, dict)
            or state.get("status") != "completed" or not isinstance(state.get("message"), str)
            or ref["quote"] not in state["message"]):
        return insufficient("Cited review text did not complete from the assigned receiver")
    receipt = review_packet(state["message"], REVIEW_RETURN)
    if (not receipt or receipt.get("assignment_id") != packet["assignment_id"]
            or receipt.get("input_sha256") != input_ref["sha256"]):
        return insufficient("Returned message lacks a matching assignment/input-version receipt")
    opinion = "\n".join(line for line in state["message"].splitlines() if not line.startswith(REVIEW_RETURN))
    if ref["quote"] not in opinion:
        return insufficient("Cited review text occurs only in the linkage receipt, not in the returned opinion")
    return {**proof, "status": "observed", "assignment_id": packet["assignment_id"],
            "dispatch_line": dispatch_line, "return_line": return_line, "receiver_thread_id": receiver,
            "input": {"path": name, "sha256": input_ref["sha256"], "resolved_path": str(path)}}


def injected_skill_inputs(task, launch, workspace, skills, host):
    """Separate skill instructions from a repository name in legitimate paths."""
    windows = isinstance(workspace, PureWindowsPath)
    path_type, path_module = (PureWindowsPath, ntpath) if windows else (PurePosixPath, posixpath)

    def path_text(value):
        # Backslashes are separators and path case is insensitive on Windows;
        # on POSIX a backslash is a literal filename character.
        text = str(value)
        return text.replace("\\", "/").casefold() if windows else text

    roots = []
    for root in ([skills, host["root"]] if host else [skills]):
        roots.append(path_text(root))
        try:
            roots.append(path_text(path_module.relpath(str(root), str(workspace))))
        except ValueError:
            pass  # Different Windows drives have no relative path.

    def root_mentioned(text):
        text = path_text(text)
        return any(re.search(re.escape(root) + r"(?=$|[/\s`\"'，。;:])", text) for root in roots)

    def names(text):
        text = path_text(text)
        explicit, ambiguous = False, False
        # An actual SKILL.md path supplies a skill, regardless of its parent name.
        if re.search(r"/SKILL\.md(?=$|[\s`\"'，。;:])", text, re.IGNORECASE):
            explicit = True
        for match in SKILL_NAME.finditer(text):
            before = text[match.start() - 1:match.start()] if match.start() else ""
            after = text[match.end():match.end() + 1]
            if match.group().startswith("$") or (before != "/" and after != "/"):
                explicit = True
            else:
                # An unfamiliar filesystem path may only contain the repo name.
                ambiguous = True
        return explicit, ambiguous

    explicit = root_mentioned(task) or bool(launch and launch.get("provided_skill_paths"))
    # Only the exact known working-directory reference is incidental. A child
    # SKILL.md path or a workspace prefix with a different suffix stays visible.
    directory = re.escape(path_text(workspace))
    natural_task = re.sub(directory + r"(?=$|[\s`\"'，。;:])", "<workspace>", path_text(task))
    named, ambiguous = names(natural_task)
    explicit |= named
    if launch:
        argv = launch["argv"]
        index = 0
        while index < len(argv):
            token = argv[index]
            option, separator, value = token.partition("=")
            # A config value is instruction text even when it begins with a
            # string that resembles another CLI option.
            configuration = token in {"--config", "-c"}
            if configuration and index + 1 < len(argv):
                token = argv[index + 1]
                option, separator, value = "<config-value>", "", ""
                index += 1
            positional_path = option in {"--cd", "-C", "--output-last-message", "-o"}
            if positional_path and not separator and index + 1 < len(argv):
                value = argv[index + 1]
                index += 1
            else:
                value = value if separator else ""
            # An identified skill root/SKILL.md remains explicit in a path slot.
            argument = value if positional_path and value else token
            skill_path = bool(re.search(r"/SKILL\.md(?=$|[\s`\"'，。;:])", path_text(argument), re.IGNORECASE))
            explicit |= skill_path
            ignore = False
            if positional_path and value and option in {"--cd", "-C"}:
                path, resolved = path_type(value), None
                startup_cwd = launch.get("cwd")
                try:
                    if path.is_absolute():
                        resolved = path
                    elif not path.anchor and isinstance(startup_cwd, str) and path_type(startup_cwd).is_absolute():
                        resolved = path_type(startup_cwd) / path
                    if resolved is not None:
                        resolved = (Path(resolved).resolve() if isinstance(workspace, Path)
                                    else path_type(path_module.normpath(str(resolved))))
                except (OSError, RuntimeError):
                    resolved = None
                if resolved is not None:
                    explicit |= root_mentioned(str(resolved))
                # The option denotes a directory, not a skill invocation. An
                # unresolved relative directory or a scope mismatch is uncertain.
                ambiguous |= resolved != workspace
                ignore = True
            else:
                explicit |= root_mentioned(argument)
                if positional_path and value and not root_mentioned(value) and not skill_path:
                    ignore = not value.startswith("-") and not any(char in value for char in "\n\r\x00")
            if not ignore:
                named, uncertain = names(argument)
                explicit |= named
                ambiguous |= uncertain
            index += 1
    return bool(explicit), bool(ambiguous)


def host_snapshot(ref):
    path, raw = file_reference(ref, "host skill snapshot")
    value = json.loads(raw)
    root = evaluation.safe_path(value["root"], directory=True)
    paired = "before" in value and "after" in value
    before = value["before"] if paired else {"files": value.get("files"), "directories": []}
    after = value["after"] if paired else before
    for item in (before, after):
        if not isinstance(item, dict):
            raise ValueError("Host skill snapshots must be objects with files")
        evaluation.validate_snapshot({"files": item.get("files"), "directories": item.get("directories", [])})
    if not before["files"]:
        raise ValueError("Host skill snapshot must include files")
    drift, current_drift = [], []
    for name in sorted(before["files"].keys() | after["files"].keys()):
        target = evaluation.safe_path(root / name)
        current = evaluation.sha(target.read_bytes()) if target.is_file() else None
        if before["files"].get(name) != after["files"].get(name):
            drift.append(name)
        if current != after["files"].get(name):
            current_drift.append(name)
    return {"path": str(path), "root": str(root), "files": after["files"], "paired": paired,
            "status": "drift" if drift else "stable" if paired else "current_only", "execution_drift": drift,
            "current_status": "drift" if current_drift else "same", "current_vs_after": current_drift,
            "scope": "A later installation change does not rewrite the captured execution version"}


def inspect_claim(claim, workspace, trace, skill_files, cwd):
    if not isinstance(claim, dict) or not isinstance(claim.get("id"), str) or not claim["id"].strip():
        raise ValueError("Each claim requires a nonempty id")
    if claim.get("status") not in STATES or claim.get("kind") not in KINDS:
        raise ValueError("Claim requires a supported status and kind; passed=true is not a review")
    if not isinstance(claim.get("reason"), str) or not claim["reason"].strip():
        raise ValueError("Claim requires a nonempty reason")
    evidence = claim.get("evidence", [])
    if not isinstance(evidence, list):
        raise ValueError("Claim evidence must be an array")
    result = {"id": claim["id"], "kind": claim["kind"], "requested_status": claim["status"],
              "status": claim["status"], "reason": claim["reason"], "evidence_status": "traceable", "evidence": [], "issues": []}
    supported = False
    for ref in evidence:
        try:
            if not isinstance(ref, dict):
                raise ValueError("Evidence reference must be an object")
            if ref.get("source") == "artifact":
                name = evaluation.relative_path(ref.get("path"))
                path, raw = file_reference({**ref, "path": str(workspace / name)}, "artifact")
                if not raw.strip():
                    raise ValueError("Artifact is empty")
                line, quote = quoted_line(text_lines(raw, "artifact"), ref)
                if quote not in line:
                    raise ValueError("Artifact quote does not occur on the cited line")
                result["evidence"].append({**ref, "resolved_path": str(path)})
                supported |= claim["kind"] in {"artifact", "content"}
            elif ref.get("source") == "trace":
                if trace is None:
                    raise ValueError("No checked native trace available")
                if ref.get("sha256") != trace["sha256"]:
                    raise ValueError("Stale trace sha256")
                line, quote = quoted_line(trace["lines"], ref)
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("Trace reference is not an event object")
                if quote not in line and quote not in json.dumps(event, ensure_ascii=False) and quote not in output_text(event.get("item", {})):
                    raise ValueError("Trace quote does not occur on the cited line")
                item = event.get("item", {})
                returned = output_text(item)
                is_result = completed_result(event) and quote in returned and bool(returned.strip())
                reference = {**ref, "native_result": is_result}
                if claim["kind"] == "skill_read" and is_result:
                    target = evaluation.safe_path(ref.get("target", ""))
                    expected = skill_files.get(str(target))
                    if expected is None:
                        reference["read_status"] = "unverified"
                        result["issues"].append("Read target is not in the identified skill snapshot")
                    else:
                        if ref.get("target_sha256") != expected:
                            raise ValueError("Read target differs from the identified skill snapshot")
                        known_read = read_command(item.get("command"), target, cwd) if item.get("type") == "command_execution" else read_tool(item, target, cwd)
                        target_raw, identity = None, None
                        if target.is_file() and evaluation.sha(target.read_bytes()) == expected:
                            target_raw, identity = target.read_bytes(), "current_file"
                        elif ref.get("target_snapshot"):
                            _, target_raw = file_reference(ref["target_snapshot"], "archived read target")
                            if evaluation.sha(target_raw) != expected:
                                raise ValueError("Archived read target differs from the execution snapshot")
                            identity = "archived_file"
                        elif full_cat(item.get("command"), target, cwd) and evaluation.sha(returned.encode("utf-8")) == expected:
                            target_raw, identity = returned.encode("utf-8"), "complete_native_return"
                        reference["identity_source"] = identity
                        reference["read_status"] = "observed" if known_read and target_raw is not None and quote in target_raw.decode("utf-8") else "unverified"
                        supported |= reference["read_status"] == "observed"
                        if reference["read_status"] != "observed":
                            result["issues"].append("Returned text is insufficient to mechanically identify this file read; inspect the native call manually")
                elif claim["kind"] == "behavior":
                    supported |= is_result
                elif claim["kind"] in {"independent_review", "other_agent_return"}:
                    reference["returned_other_agent"] = independent_return(event, quote, trace.get("thread_id"))
                    if claim["kind"] == "other_agent_return":
                        reference["scope"] = "Receipt of another agent's text only; its task, reviewed draft and judgement remain unverified."
                        supported |= reference["returned_other_agent"]
                    else:
                        link = review_assignment_link(claim.get("review_assignment"), event, ref, trace, workspace)
                        reference["review_assignment"] = link
                        supported |= link["status"] == "observed"
                        if link["status"] != "observed":
                            result["issues"].append(link["reason"])
                result["evidence"].append(reference)
            else:
                raise ValueError("Evidence source must be trace or artifact")
        except (ValueError, OSError, KeyError, TypeError) as error:
            result["evidence_status"] = "failed"
            result["issues"].append(str(error))
    if claim["kind"] == "reader_response":
        result["issues"].append("File references cannot authenticate a human reader or establish actual reception")
        supported = False
    if not supported or result["evidence_status"] == "failed":
        result["status"] = "unverified"
        if result["evidence_status"] != "failed":
            result["evidence_status"] = "unverified"
        if not evidence or not supported:
            result["issues"].append("No sufficient referenced evidence for this claim; an agent completion statement is insufficient")
    # Unverified is an honest scope declaration; it never becomes observed automatically.
    return result


def check(args):
    baseline, baseline_path = evaluation.load_json(args.baseline)
    checked, check_path = evaluation.load_json(args.check_file)
    record, record_path = evaluation.load_json(args.record)
    if any(not isinstance(value, dict) or type(value.get("schema")) is not int or value["schema"] != 1 for value in (baseline, checked, record)):
        raise ValueError("Unsupported baseline, check or review schema")
    for key, path in (("baseline_sha256", baseline_path), ("check_sha256", check_path)):
        if record.get(key) != evaluation.sha(path.read_bytes()):
            raise ValueError(f"Stale review {key}")
    if checked.get("baseline", {}).get("sha256") != evaluation.sha(baseline_path.read_bytes()):
        raise ValueError("Check does not cite the supplied baseline")
    case = evaluation.validate_case(baseline["case"])
    if checked.get("case_id") != case["id"] or record.get("case_id", case["id"]) != case["id"]:
        raise ValueError("Case identity differs between evidence files")
    workspace, skills = evaluation.roots_for(baseline["workspace"], baseline["skills_root"])
    for area in ("workspace", "skills"):
        evaluation.validate_snapshot(baseline["before"][area])
        evaluation.validate_snapshot(checked["after"][area])
    task_path, task_raw = file_reference(record.get("task"), "task")
    task = task_raw.decode("utf-8")
    reviewer = record.get("reviewer")
    if not isinstance(reviewer, dict) or reviewer.get("kind") not in {"model", "human", "developer"} or reviewer.get("mode") not in {"independent", "feedback_aware", "self"}:
        raise ValueError("Reviewer requires kind and mode; identity is recorded, not authenticated")
    integrity = {"current_workspace": evaluation.changes(checked["after"]["workspace"], evaluation.snapshot(workspace)),
                 "current_frozen_skills": evaluation.changes(checked["after"]["skills"], evaluation.snapshot(skills)),
                 "execution_skill_drift": evaluation.changes(baseline["before"]["skills"], checked["after"]["skills"]),
                 "blank_required_outputs": [name for name in case["required_outputs"] if (workspace / name).is_file() and not (workspace / name).read_bytes().strip()]}
    expected_changes = evaluation.changes(baseline["before"]["workspace"], checked["after"]["workspace"])
    expected_checks = {"workspace_changes": expected_changes,
                       "unauthorized_changes": [item for item in expected_changes if not evaluation.allowed(item, case["allowed_changes"])],
                       "skill_changes": integrity["execution_skill_drift"],
                       "missing_outputs": [name for name in case["required_outputs"] if name not in checked["after"]["workspace"]["files"]],
                       "empty_outputs": [name for name in case["required_outputs"] if checked["after"]["workspace"]["files"].get(name) == evaluation.sha(b"")]}
    if checked.get("checks") != expected_checks or checked.get("mechanical_ok") != (not any(expected_checks[name] for name in ("unauthorized_changes", "skill_changes", "missing_outputs", "empty_outputs"))):
        raise ValueError("Check fields do not agree with baseline and captured snapshots")
    trace = None
    trace_record = checked.get("trace", {})
    if not isinstance(trace_record, dict):
        raise ValueError("Check trace must be an object")
    if trace_record.get("path"):
        trace_path, trace_raw = file_reference(trace_record, "native trace")
        inspected = evaluation.inspect_trace(trace_path)
        if inspected != trace_record:
            raise ValueError("Check trace summary differs from its native trace")
        lines = text_lines(trace_raw, "trace")
        thread_id = None
        for line in lines:
            try:
                event = json.loads(line)
                if isinstance(event, dict) and event.get("type") == "thread.started":
                    thread_id = event.get("thread_id")
                    break
            except ValueError:
                continue
        trace = {"path": str(trace_path), "sha256": evaluation.sha(trace_raw), "lines": lines, "status": inspected["status"], "thread_id": thread_id}
    invocation = record.get("invocation", {})
    if not isinstance(invocation, dict):
        raise ValueError("Invocation must be an object")
    if invocation.get("mode") not in {"supplied_skill", "native_discovery", "unverified"}:
        raise ValueError("Invocation mode must distinguish supplied_skill and native_discovery")
    discovery = {"mode": invocation["mode"], "host": invocation.get("host"), "status": "unverified", "issues": [], "host_scope": "CLI evidence does not establish desktop discovery"}
    launch, host = None, None
    if invocation.get("launch"):
        _, raw = file_reference(invocation["launch"], "launch receipt")
        launch = json.loads(raw)
        if not isinstance(launch, dict) or not isinstance(launch.get("argv"), list) or not launch["argv"] or any(not isinstance(word, str) for word in launch["argv"]):
            raise ValueError("Launch receipt requires the raw argv array")
        launch_task_hash = launch.get("task_sha256", launch.get("stdin_task_sha256"))
        if launch_task_hash != evaluation.sha(task_raw) or launch.get("host") != invocation.get("host"):
            raise ValueError("Launch host/task identity differs from the review")
    if invocation.get("host_skill_snapshot"):
        host = host_snapshot(invocation["host_skill_snapshot"])
    files = {str(skills / name): digest for name, digest in checked["after"]["skills"]["files"].items()} if invocation["mode"] == "supplied_skill" else {}
    if host:
        files.update({str(Path(host["root"]) / name): digest for name, digest in host["files"].items()})
    claims_input = record.get("claims")
    if not isinstance(claims_input, list) or not claims_input:
        raise ValueError("Review requires nonempty claims; passed=true is not evidence")
    claims = [inspect_claim(claim, workspace, trace, files, workspace) for claim in claims_input]
    if len({claim["id"] for claim in claims}) != len(claims):
        raise ValueError("Duplicate claim ids")
    read_observed = any(claim["kind"] == "skill_read" and claim["status"] == "observed" for claim in claims)
    injected, ambiguous_input = injected_skill_inputs(task, launch, workspace, skills, host)
    if invocation["mode"] == "native_discovery":
        if injected:
            discovery.update(status="failed")
            discovery["issues"].append("Task or launch explicitly supplies a skill name/path; it cannot establish implicit native discovery")
        else:
            if ambiguous_input:
                discovery["issues"].append("A launch directory cannot be matched to the captured workspace, or another path contains a skill/repository name; skill provision is ambiguous")
            if not launch or not isinstance(invocation.get("host"), str) or not invocation["host"].strip():
                discovery["issues"].append("Native discovery requires an identified host and raw launch/task receipt")
            if not host or host["status"] != "stable":
                discovery["issues"].append("Native discovery requires an unchanged before/after snapshot of the host skill files actually read")
            if not trace or trace["status"] != "complete" or not read_observed:
                discovery["issues"].append("Native discovery requires a complete native trace and a referenced successful skill read")
            if not discovery["issues"]:
                discovery["status"] = "observed"
    elif invocation["mode"] == "supplied_skill":
        discovery["status"] = "observed" if injected and launch else "unverified"
        if discovery["status"] == "unverified":
            discovery["issues"].append("Supplied-skill mode recorded, but the launch/task does not identify the supplied skill")
    source = {"status": "unverified", "reason": "No current source skill tree supplied"}
    if getattr(args, "source_skills_root", None):
        source_root = evaluation.safe_path(args.source_skills_root, directory=True)
        drift = evaluation.changes(baseline["before"]["skills"], evaluation.snapshot(source_root))
        source = {"root": str(source_root), "status": "drift" if drift else "same", "changes": drift,
                  "scope": "Source drift does not rewrite the captured frozen version or establish the native host version"}
    evidence_failed = bool(any(integrity.values()) or not checked["mechanical_ok"] or (trace and trace["status"] != "complete") or discovery["status"] == "failed" or (host and host["status"] == "drift") or any(claim["evidence_status"] == "failed" for claim in claims))
    unverified = discovery["status"] == "unverified" or not trace or any(claim["evidence_status"] == "unverified" for claim in claims)
    status = "failed" if evidence_failed else "unverified" if unverified else "traceable"
    result = {"schema": 1, "case_id": case["id"], "scope": SCOPE, "validation_status": status,
              "provenance": {"baseline": {"path": str(baseline_path), "sha256": evaluation.sha(baseline_path.read_bytes())},
                             "check": {"path": str(check_path), "sha256": evaluation.sha(check_path.read_bytes())},
                             "record": {"path": str(record_path), "sha256": evaluation.sha(record_path.read_bytes())},
                             "task": {"path": str(task_path), "sha256": evaluation.sha(task_raw)}},
              "reviewer": reviewer, "integrity": integrity, "invocation": discovery, "host_skills": host,
              "current_source_vs_frozen": source, "claims": claims,
              "unverified_scope": ["Semantic correctness of review judgements", "Skill understanding", "Literary quality and lasting improvement", "Authenticated human reader reception", "Desktop discovery from a CLI trial", "Writes outside the captured trees or transient changes restored before snapshots"]}
    protected = (baseline_path, check_path, record_path, task_path)
    output_roots = (workspace, skills, evaluation.safe_path(host["root"], directory=True)) if host else (workspace, skills)
    output = evaluation.output_path(args.output, output_roots, protected)
    evaluation.save_new(output, result)
    return result, 1 if status == "failed" else 2 if status == "unverified" else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("check")
    for name in ("baseline", "record", "output"):
        inspect.add_argument("--" + name, required=True)
    inspect.add_argument("--check", dest="check_file", required=True)
    inspect.add_argument("--source-skills-root")
    args = parser.parse_args(argv)
    try:
        result, code = check(args)
    except (ValueError, OSError, KeyError, TypeError) as error:
        result, code = {"scope": SCOPE, "validation_status": "failed", "error": str(error)}, 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
