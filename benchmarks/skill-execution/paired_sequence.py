#!/usr/bin/env python3
"""Freeze and inspect paired candidate-chapter sequences; never launch or grade a model.

Inputs and prose belong in an ignored/local experiment directory. Each phase is a
fresh invocation with its own immutable task and before/after evidence. The two
skill packages may use different entrypoint names; story inputs remain identical.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


trials = module("paired_prose_trials", HERE / "prose_trials.py")
evaluation = trials.evaluation
ARMS = ("baseline", "candidate")


def read(path):
    return evaluation.load_json(path)[0]


def write(path, value):
    evaluation.save_new(path, value)


def validate(protocol):
    if not isinstance(protocol, dict) or protocol.get("schema") != 1:
        raise ValueError("Unsupported paired-sequence protocol")
    trials.validate_configuration(protocol.get("configuration"))
    if any(protocol["configuration"]["conditions"][a]["repetitions"] != 1 for a in ARMS):
        raise ValueError("Each sequence is one preserved attempt; use a new run for a repetition")
    if protocol.get("provenance") != {"origin": "original_synthetic", "classification": "development",
                                      "actual_reader_feedback": False, "independent_holdout": False}:
        raise ValueError("Declare original synthetic development inputs and no actual reader feedback")
    if not isinstance(protocol.get("id"), str) or not protocol["id"].strip():
        raise ValueError("A protocol ID is required")
    fixtures = protocol.get("fixtures")
    if not isinstance(fixtures, dict) or not fixtures:
        raise ValueError("Nonempty frozen fixtures are required")
    fixture_names = set()
    for name, body in fixtures.items():
        evaluation.relative_path(name)
        if any(name.startswith(old + "/") or old.startswith(name + "/") for old in fixture_names):
            raise ValueError("Fixture file paths must not overlap")
        fixture_names.add(name)
        if not isinstance(body, str) or not body.strip():
            raise ValueError("Fixtures must contain text")
    phases = protocol.get("phases")
    if not isinstance(phases, list) or not 2 <= len(phases) <= 6:
        raise ValueError("A bounded sequence contains two to six phases")
    outputs = set(fixtures)
    for index, phase in enumerate(phases, 1):
        if not isinstance(phase, dict) or set(phase) != {"prompt", "output", "length", "review_required"}:
            raise ValueError("Each phase needs prompt, output, length and review_required")
        evaluation.validate_case(case_for(protocol, index))
        name = evaluation.relative_path(phase["output"])
        if name in outputs or any(name.startswith(old + "/") or old.startswith(name + "/") for old in outputs):
            raise ValueError("Phase outputs must be distinct from inputs and prior outputs")
        outputs.add(name)
        length = phase["length"]
        if (not isinstance(length, dict) or set(length) != {"min", "max", "count_method", "count_title"}
                or type(length["min"]) is not int or type(length["max"]) is not int
                or not 2000 <= length["min"] <= length["max"] <= 6000
                or length["count_method"] != "visible_nonspace_v2" or length["count_title"] is not False):
            raise ValueError("Declare normal-length 2000–6000 chapters, excluding their first title line")
    return protocol


def case_for(protocol, number):
    phase = protocol["phases"][number - 1]
    return {"id": f"{protocol['id']}-phase-{number}", "prompt": phase["prompt"],
            "invocation": "provided_skill_path", "allowed_changes": [phase["output"]],
            "required_outputs": [phase["output"]], "review_required": phase["review_required"]}


def source_tree(path, entry):
    root = evaluation.safe_path(path, True)
    evaluation.relative_path(entry)
    target = evaluation.safe_path(root / entry)
    if not target.is_file():
        raise ValueError("Skill entrypoint is missing")
    evaluation.snapshot(root)
    if any(p.is_file() and p.stat().st_nlink > 1 for p in root.rglob("*")):
        raise ValueError("Hard-linked skill sources are unsupported")
    return root


def load_run(path):
    run = evaluation.safe_path(path, True)
    manifest = read(run / "sequence.json")
    if manifest.get("schema") != 1 or manifest.get("run") != str(run):
        raise ValueError("Sequence directory binding changed")
    protocol = validate(read(run / "protocol.json"))
    if trials.file_hash(run / "protocol.json") != manifest["protocol_sha256"]:
        raise ValueError("Frozen protocol changed")
    for arm in ARMS:
        expected = manifest["arms"][arm]
        skills = run / arm / "skills"
        source_tree(skills, expected["entry"])
        if evaluation.snapshot(skills) != expected["snapshot"]:
            raise ValueError(f"{arm}: frozen skill snapshot changed")
    return run, manifest, protocol


def prepare(protocol_path, baseline_skills, baseline_entry, candidate_skills, candidate_entry, output):
    protocol_path = evaluation.safe_path(protocol_path)
    protocol = validate(read(protocol_path))
    sources = {"baseline": (source_tree(baseline_skills, baseline_entry), baseline_entry),
               "candidate": (source_tree(candidate_skills, candidate_entry), candidate_entry)}
    run = evaluation.safe_path(output)
    if run.exists() or any(evaluation.within(run, s[0]) or evaluation.within(s[0], run) for s in sources.values()):
        raise ValueError("Use a new run directory outside both source trees")
    if evaluation.within(protocol_path, run):
        raise ValueError("Protocol must exist outside the new run directory")
    run.mkdir(parents=True)
    write(run / "protocol.json", protocol)
    manifest = {"schema": 1, "run": str(run), "created_at": datetime.now(timezone.utc).isoformat(),
                "protocol_sha256": trials.file_hash(run / "protocol.json"), "arms": {},
                "scope": "Candidate prose sequences; no native discovery, formal adoption, publication or reader certification",
                "quality_assessed": False}
    for arm, (source, entry) in sources.items():
        skills, workspace = run / arm / "skills", run / arm / "workspace"
        before_copy = evaluation.snapshot(source)
        shutil.copytree(source, skills, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
        copied = evaluation.snapshot(skills)
        if evaluation.snapshot(source) != before_copy or copied != before_copy:
            raise ValueError("Source changed during freeze; preserve this partial run and prepare a new one")
        workspace.mkdir()
        for relative, content in protocol["fixtures"].items():
            path = workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        # Output directories are fixed in advance, so directory creation is not
        # mistaken for permission to add arbitrary files during generation.
        for phase in protocol["phases"]:
            (workspace / phase["output"]).parent.mkdir(parents=True, exist_ok=True)
        manifest["arms"][arm] = {"entry": entry, "snapshot": copied,
                                 "source_root": str(source)}
    write(run / "sequence.json", manifest)
    for arm in ARMS:
        begin_phase(run, arm, 1)
    return {"run": str(run), "phases_per_arm": len(protocol["phases"]), "execution": "not_started"}


def phase_files(run, arm, number, count):
    if arm not in ARMS or type(number) is not int or not 1 <= number <= count:
        raise ValueError("Invalid sequence arm or phase")
    folder = run / arm / f"phase-{number}"
    return folder, run / arm / "workspace", run / arm / "skills"


def verify_phase(run, manifest, protocol, arm, number):
    folder, workspace, skills = phase_files(run, arm, number, len(protocol["phases"]))
    frozen = read(folder / "phase.json")
    if (frozen.get("arm"), frozen.get("number"), frozen.get("sequence_sha256")) != (
            arm, number, trials.file_hash(run / "sequence.json")):
        raise ValueError("Phase identity or frozen manifest changed")
    for filename in ("case.json", "task.txt", "baseline.json"):
        if trials.file_hash(folder / filename) != frozen["files"][filename]:
            raise ValueError(f"Frozen phase file changed: {filename}")
    baseline = read(folder / "baseline.json")
    if (baseline["workspace"] != str(workspace) or baseline["skills_root"] != str(skills)
            or baseline["before"]["skills"] != manifest["arms"][arm]["snapshot"]
            or baseline["case"] != case_for(protocol, number)):
        raise ValueError("Phase baseline was rebound")
    return folder, workspace, skills


def begin_phase(path, arm, number):
    run, manifest, protocol = load_run(path)
    folder, workspace, skills = phase_files(run, arm, number, len(protocol["phases"]))
    if folder.exists():
        raise ValueError("Refusing to overwrite a prepared phase")
    previous = None
    if number > 1:
        previous = inspect_phase(run, arm, number - 1)
        if not previous["continuation_ready"]:
            raise ValueError("Prior phase needs complete execution evidence and unchanged inputs/output")
        preserve_output(run / arm / f"phase-{number - 1}", previous)
        # Length deviations remain visible, but do not erase a completed original
        # chapter or replace it with a selected reroll before continuation.
    folder.mkdir()
    case = case_for(protocol, number)
    write(folder / "case.json", case)
    task = (f"工作目录：{workspace}\n本轮使用冻结技能入口：{skills / manifest['arms'][arm]['entry']}\n"
            "使用该入口及其相关参考处理下列写作请求。只读取本工作目录、冻结技能套件；"
            "不要读取评测协议、另一版本输出或评阅材料。不安装、不联网、不调用其他代理。\n\n"
            + case["prompt"] + "\n")
    (folder / "task.txt").write_text(task, encoding="utf-8")
    evaluation.start(argparse.Namespace(case_file=folder / "case.json", workspace=workspace,
                                        skills_root=skills, output=folder / "baseline.json"))
    write(folder / "phase.json", {"arm": arm, "number": number,
        "sequence_sha256": trials.file_hash(run / "sequence.json"),
        "files": {name: trials.file_hash(folder / name) for name in ("case.json", "task.txt", "baseline.json")},
        "previous": previous})
    return {"arm": arm, "phase": number, "task": str(folder / "task.txt"), "workspace": str(workspace)}


def preserve_output(folder, report):
    """Keep the actual observed draft outside the next writer's writable tree."""
    if report["artifact"] is None:
        return None
    source = evaluation.safe_path(report["artifact"]["path"])
    raw = source.read_bytes()
    if evaluation.sha(raw) != report["artifact"]["sha256"]:
        raise ValueError("Output changed between inspection and archival")
    target = evaluation.safe_path(folder / "prose.original.md")
    if target.exists():
        if target.read_bytes() != raw:
            raise ValueError("Archived original differs; refusing to replace it")
    else:
        with target.open("xb") as stream:
            stream.write(raw)
    return trials.evidence_file(target)


def complete_phase(path, arm, number):
    report = inspect_phase(path, arm, number)
    folder = evaluation.safe_path(path, True) / arm / f"phase-{number}"
    if (folder / "inspection.json").exists():
        raise ValueError("Refusing to replace an existing phase report")
    preserve_output(folder, report)
    write(folder / "inspection.json", report)
    return {"status": report["status"], "continuation_ready": report["continuation_ready"],
            "report": str(folder / "inspection.json"), "quality_assessed": False}


def inspect_phase(path, arm, number):
    run, manifest, protocol = load_run(path)
    folder, workspace, skills = verify_phase(run, manifest, protocol, arm, number)
    trace_path = folder / "trace.jsonl"
    with tempfile.TemporaryDirectory() as tmp:
        checked, _ = evaluation.check(argparse.Namespace(baseline=folder / "baseline.json",
            trace=trace_path if trace_path.exists() else None, output=Path(tmp).resolve() / "check.json"))
    condition = protocol["configuration"]["conditions"][arm]
    launch = trials.evidence_json(folder / "launch.json")
    launch_check = trials.launch_conditions(launch, folder / "task.txt", workspace, condition)
    process = trials.evidence_json(folder / "process-result.json")
    expected_process = {"arm": arm, "phase": number, "number": number,
                        "case_id": case_for(protocol, number)["id"],
                        "task_sha256": trials.file_hash(folder / "task.txt"),
                        "stdin_task_sha256": trials.file_hash(folder / "task.txt")}
    binding_fields = [key for key in expected_process if process and key in process]
    binding_errors = [key for key in binding_fields if process[key] != expected_process[key]
                      or (key in ("phase", "number") and type(process[key]) is not int)]
    process_binding = {"status": "mismatch" if binding_errors else "matches_recorded_fields" if binding_fields else "unverified",
                       "provided_fields": binding_fields, "mismatched_fields": binding_errors,
                       "scope": "Internal receipt consistency; missing legacy bindings and capture origin remain unverified."}
    process_ok = bool(process and type(process.get("exit_code")) is int
                      and process["exit_code"] == 0 and process.get("timed_out") is False and not binding_errors)
    phase = protocol["phases"][number - 1]
    artifact = workspace / phase["output"]
    count = None
    if artifact.is_file():
        count = trials.runtime.manuscript_counts(artifact.read_text(encoding="utf-8"), False)["visible_nonspace_v2"]
    within = count is not None and phase["length"]["min"] <= count <= phase["length"]["max"]
    complete = (checked["mechanical_ok"] and checked["trace"]["status"] == "complete" and process_ok
                and launch_check["binding_status"] == "matches_recorded_launch"
                and launch_check["required_options_observed"])
    return {"arm": arm, "phase": number, "continuation_ready": bool(complete),
        "status": "needs_content_review" if complete and within else "incomplete_or_out_of_range",
        "quality_assessed": False, "mechanical": checked, "launch_conditions": launch_check,
        "process": process, "process_binding": process_binding, "count": count, "within_length": within,
        "artifact": trials.evidence_file(artifact),
        "evidence": {name: trials.evidence_file(folder / name) for name in
                     ("launch.json", "process-result.json", "trace.jsonl", "stderr.log", "final.txt")},
        "usage": trials.metrics.summarize([trace_path]) if trace_path.exists() else None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("prepare")
    for name in ("protocol", "baseline-skills", "baseline-entry", "candidate-skills", "candidate-entry", "output"):
        start.add_argument("--" + name, required=True)
    for name in ("phase", "inspect", "complete"):
        sub = commands.add_parser(name)
        sub.add_argument("--run", required=True)
        sub.add_argument("--arm", choices=ARMS, required=True)
        sub.add_argument("--number", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(args.protocol, args.baseline_skills, args.baseline_entry,
                             args.candidate_skills, args.candidate_entry, args.output)
        elif args.command == "phase":
            result = begin_phase(args.run, args.arm, args.number)
        elif args.command == "complete":
            result = complete_phase(args.run, args.arm, args.number)
        else:
            result = inspect_phase(args.run, args.arm, args.number)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        if args.command in ("inspect", "complete") and result["status"] != "needs_content_review":
            return 1
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
