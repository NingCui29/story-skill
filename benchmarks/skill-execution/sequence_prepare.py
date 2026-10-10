#!/usr/bin/env python3
"""Prepare a bounded, multi-session fiction trial without running a model.

This is a transaction/continuity exercise, not a full-length novel assessment.
Evidence is exclusive-created; traces describe observed execution, not authorship
authentication or a security boundary. Independent content review remains needed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SEQUENCES = HERE / "sequences"


def module_at(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preparer = module_at("execution_sequence_fixture", HERE / "prepare.py")
evaluation = module_at("execution_sequence_evidence", ROOT / "scripts/execution_eval.py")


def load(path):
    value, resolved = evaluation.load_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object: {resolved}")
    return value


def configuration(sequence_id):
    if not re.fullmatch(r"[a-z][a-z0-9-]*", sequence_id):
        raise ValueError("Invalid sequence ID")
    path = preparer.no_links(SEQUENCES / f"{sequence_id}.json")
    value = load(path)
    if value.get("id") != sequence_id or value.get("schema") != 1:
        raise ValueError("Unsupported sequence configuration")
    rounds = value.get("rounds")
    if (not isinstance(rounds, list) or len(rounds) != 3
            or [r.get("through_chapter") for r in rounds] != [2, 4, 6]):
        raise ValueError("This bounded sequence requires rounds ending at chapters 2, 4, 6")
    if value.get("kind") != "long" or not isinstance(value.get("title"), str):
        raise ValueError("A long-fiction transaction profile with a title is required")
    if (not isinstance(value.get("volumes"), list) or len(value["volumes"]) != 2
            or any(not isinstance(v, str) or not v.strip() for v in value["volumes"])):
        raise ValueError("Two volume names are required")
    if value.get("chapter_length") != [800, 1000]:
        raise ValueError("The explicit bounded exercise length must be 800–1000")
    if (not isinstance(value.get("fixtures"), dict) or set(value["fixtures"]) != {"创作输入.md"}
            or not isinstance(value["fixtures"]["创作输入.md"], str) or not value["fixtures"]["创作输入.md"].strip()):
        raise ValueError("Only the initial user input is permitted; no prewritten formal chapter fixtures")
    for index, step in enumerate(rounds, 1):
        evaluation.validate_case({"id": f"{sequence_id}-round-{index}",
                                  "invocation": "provided_skill_path:story-skill",
                                  **step})
    return value, path


def manifest_for(run):
    run = preparer.no_links(run)
    if not run.is_dir():
        raise ValueError("Sequence run not found")
    manifest_path = run / "sequence.json"
    manifest = load(manifest_path)
    if manifest.get("schema") != 1 or manifest.get("run") != str(run):
        raise ValueError("Invalid sequence binding")
    if manifest.get("workspace") != str(run / "workspace") or manifest.get("skills_root") != str(run / "skills"):
        raise ValueError("Sequence roots cannot be rebound")
    evaluation.roots_for(run / "workspace", run / "skills")
    frozen = evaluation.validate_snapshot(manifest.get("skills_snapshot"))
    if evaluation.snapshot(run / "skills") != frozen:
        raise ValueError("Frozen skill snapshot has drifted")
    first = load(run / "round-1" / "phase.json")
    if first.get("manifest_sha256") != preparer.sha256(manifest_path):
        raise ValueError("Sequence manifest has changed")
    return run, manifest


def prepare_round(run, manifest, number, previous=None):
    step = manifest["configuration"]["rounds"][number - 1]
    folder = preparer.no_links(run / f"round-{number}")
    if folder.exists():
        raise ValueError(f"Refusing to overwrite round evidence: {folder}")
    folder.mkdir()
    case = {"id": f"{manifest['configuration']['id']}-round-{number}",
            "invocation": "provided_skill_path:story-skill", **step}
    preparer.write_json(folder / "case.json", case)
    task = (f"工作目录及最终书根：{run / 'workspace'}\n\n{step['prompt']}\n\n"
            f"本任务使用这份冻结技能及其同目录套件：{run / 'skills/story-skill/SKILL.md'}\n")
    with (folder / "task.txt").open("x", encoding="utf-8") as stream:
        stream.write(task)
    evaluation.start(argparse.Namespace(case_file=folder / "case.json", workspace=run / "workspace",
                                        skills_root=run / "skills", output=folder / "baseline.json"))
    phase = {"schema": 1, "round": number, "manifest_sha256": preparer.sha256(run / "sequence.json"),
             "case_sha256": preparer.sha256(folder / "case.json"),
             "task_sha256": preparer.sha256(folder / "task.txt"),
             "baseline_sha256": preparer.sha256(folder / "baseline.json"),
             "previous": previous, "task_execution": "not_started", "quality_assessed": False}
    preparer.write_json(folder / "phase.json", phase)
    return {"prepared": True, "run": str(run), "round": number, "workspace": manifest["workspace"],
            "skills_root": manifest["skills_root"], "task": str(folder / "task.txt"),
            "baseline": str(folder / "baseline.json"), "trace": str(folder / "trace.jsonl"),
            "check_output": str(folder / "result.json"),
            "task_execution": "not_started", "quality_assessed": False}


def start(sequence_id, requested_output=None):
    config, source = configuration(sequence_id)
    run = preparer.output_path(requested_output, f"sequence-{sequence_id}")
    run.parent.mkdir(parents=True, exist_ok=True)
    preparer.no_links(run.parent)
    run.mkdir()
    workspace, skills = run / "workspace", run / "skills"
    workspace.mkdir()
    preparer.copy_skills(skills)
    # Initial user inputs only: no initialized book, adopted plans or chapters.
    for relative, text in config.get("fixtures", {}).items():
        target = preparer.fixture_path(workspace, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as stream:
            stream.write(text)
    manifest = {"schema": 1, "run": str(run), "workspace": str(workspace), "skills_root": str(skills),
                "prepared_at": datetime.now(timezone.utc).isoformat(), "configuration": config,
                "configuration_source": {"path": str(source), "sha256": preparer.sha256(source)},
                "skills_snapshot": evaluation.snapshot(skills), "synthetic": True,
                "scope": "Six-chapter transaction and continuity exercise; not full-length literary validation",
                "native_skill_discovery_tested": False, "quality_assessed": False}
    preparer.write_json(run / "sequence.json", manifest)
    return prepare_round(run, manifest, 1)


def verify_round_files(run, number):
    folder = run / f"round-{number}"
    phase = load(folder / "phase.json")
    if phase.get("round") != number:
        raise ValueError("Invalid round binding")
    for filename, field in (("case.json", "case_sha256"), ("task.txt", "task_sha256"),
                            ("baseline.json", "baseline_sha256")):
        if preparer.sha256(preparer.no_links(folder / filename)) != phase.get(field):
            raise ValueError(f"Prepared round input changed: {filename}")
    return folder, phase


def verify_evidence(run, manifest, number):
    folder, phase = verify_round_files(run, number)
    baseline_path, trace_path = folder / "baseline.json", folder / "trace.jsonl"
    baseline, report = load(baseline_path), load(folder / "result.json")
    if (baseline.get("workspace") != manifest["workspace"]
            or baseline.get("skills_root") != manifest["skills_root"]
            or baseline.get("before", {}).get("skills") != manifest["skills_snapshot"]):
        raise ValueError("Round baseline is not bound to this workspace and frozen skill")
    if report.get("case_id") != baseline["case"]["id"] or report.get("baseline") != {
            "path": str(baseline_path), "sha256": preparer.sha256(baseline_path)}:
        raise ValueError("Report belongs to another round or baseline")
    trace = evaluation.inspect_trace(trace_path)
    if trace["status"] != "complete" or report.get("trace") != trace:
        raise ValueError("A complete, unchanged native execution trace is required")
    if report.get("mechanical_ok") is not True or report.get("execution_status") != "needs_review":
        raise ValueError("Previous mechanical check is unresolved")
    current = {"workspace": evaluation.snapshot(run / "workspace"), "skills": evaluation.snapshot(run / "skills")}
    if report.get("after") != current:
        raise ValueError("Workspace or skill changed after the round check")
    changes = evaluation.changes(baseline["before"]["workspace"], current["workspace"])
    case = baseline["case"]
    if any(not evaluation.allowed(item, case["allowed_changes"]) for item in changes):
        raise ValueError("Round has unauthorized file changes")
    if any(name not in current["workspace"]["files"] or not (run / "workspace" / name).read_text(encoding="utf-8").strip()
           for name in case["required_outputs"]):
        raise ValueError("Required round outputs are missing or empty")
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    thread_id = next(event["thread_id"] for event in events if event["type"] == "thread.started")
    for earlier in range(1, number):
        previous = load(run / f"round-{earlier}" / "verified.json")
        if previous["thread_id"] == thread_id:
            raise ValueError("Each round must use a real new session")
    return {"round": number, "thread_id": thread_id,
            "baseline_sha256": phase["baseline_sha256"], "trace_sha256": trace["sha256"],
            "report_sha256": preparer.sha256(folder / "result.json"),
            "mechanical_ok": True, "execution_status": "needs_review", "quality_assessed": False}


def verify_reading_copy(assembled, title, chapter_texts):
    """Allow one exact book-title heading, then the actual chapters once each.

    Only whitespace may separate chapters. A substring match would miss repeated
    chapters, inserted candidates, or commentary accidentally merged into prose.
    """
    remaining = assembled.strip()
    first, separator, tail = remaining.partition("\n")
    if re.sub(r"^#{1,6}\s+", "", first).strip() == title:
        remaining = tail.lstrip() if separator else ""
    for text in chapter_texts:
        actual = text.strip()
        if not remaining.startswith(actual):
            raise ValueError("Final reading copy must contain each actual chapter once, separated only by whitespace")
        remaining = remaining[len(actual):].lstrip()
    if remaining:
        raise ValueError("Final reading copy contains repeated chapters, candidates or extra text")


def inspect_book(run, manifest, number):
    workspace, skills = run / "workspace", run / "skills"
    runtime = skills / "story-skill/scripts/story.py"
    command = [sys.executable, str(runtime), "status", "--book", str(workspace)]
    result = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
    status = json.loads(result.stdout)
    config = manifest["configuration"]
    expected = config["rounds"][number - 1]["through_chapter"]
    if (status.get("title") != config["title"] or status.get("kind") != config["kind"]
            or status.get("last_chapter") != expected or status.get("next_chapter") != expected + 1
            or status.get("imported_through") != 0):
        raise ValueError(f"Actual book status does not match round {number}: {status}")
    story, _ = preparer.load_runtime(skills)
    book = story.Book(workspace, read_only=True)
    chapters = []
    try:
        with book.read_snapshot():
            audit = book.audit()
            if audit.get("exports_complete") is not True:
                raise ValueError("Actual managed exports are incomplete or changed")
            rows = book.db.execute("SELECT chapter,text,sha,imported FROM chapters ORDER BY chapter").fetchall()
            if [row["chapter"] for row in rows] != list(range(1, expected + 1)):
                raise ValueError("Registered formal chapter range is incomplete")
            for row in rows:
                chapter = row["chapter"]
                relative = book.chapter_path(chapter)
                volume = config["volumes"][0 if chapter <= 3 else 1]
                if Path(relative).parent.as_posix() != f"chapters/{volume}" or row["imported"]:
                    raise ValueError(f"Wrong volume or imported chapter: {chapter}")
                target = preparer.fixture_path(workspace, relative)
                if (not target.is_file() or target.read_text(encoding="utf-8") != row["text"]
                        or preparer.sha256(target) != row["sha"]):
                    raise ValueError(f"Formal export differs from committed chapter {chapter}")
                count = story.manuscript_counts(row["text"])["visible_nonspace_v2"]
                if not config["chapter_length"][0] <= count <= config["chapter_length"][1]:
                    raise ValueError(f"Chapter {chapter} does not meet the explicit exercise length")
                chapters.append({"chapter": chapter, "path": relative, "sha256": row["sha"], "count": count})
    finally:
        book.close()
    actual_files = evaluation.snapshot(workspace / "chapters")["files"]
    if {f"chapters/{name}" for name in actual_files} != {chapter["path"] for chapter in chapters}:
        raise ValueError("Formal export directory contains unregistered or missing chapters")
    if number > 1:
        previous = load(run / f"round-{number - 1}" / "verified.json")["book"]
        if previous["status"]["id"] != status["id"] or chapters[:len(previous["chapters"])] != previous["chapters"]:
            raise ValueError("Book identity or earlier committed chapters changed")
    if number == 3:
        assembled = (workspace / "全书正文.md").read_text(encoding="utf-8")
        verify_reading_copy(assembled, config["title"], [
            (workspace / chapter["path"]).read_text(encoding="utf-8") for chapter in chapters])
    return {"status_command": command, "status": status, "audit": audit, "chapters": chapters,
            "semantic_review": "required; status, hashes, counts and file presence do not assess prose"}


def complete_round(run, after_round, final=False):
    if type(after_round) is not int or not 1 <= after_round <= 3:
        raise ValueError("Invalid round number")
    run, manifest = manifest_for(run)
    if (final and after_round != 3) or (not final and after_round == 3):
        raise ValueError("Use finish only after round 3; use next after rounds 1 and 2")
    expected_folders = {f"round-{n}" for n in range(1, after_round + 1)}
    if {p.name for p in run.glob("round-*")} != expected_folders:
        raise ValueError("Refusing skipped, repeated or out-of-order rounds")
    if (run / f"round-{after_round}" / "verified.json").exists():
        raise ValueError("Refusing to overwrite completed round evidence")
    for earlier in range(1, after_round):
        folder, _ = verify_round_files(run, earlier)
        if not (folder / "verified.json").is_file():
            raise ValueError("Previous round has not been verified")
        previous = load(folder / "verified.json")
        for filename, field in (("trace.jsonl", "trace_sha256"), ("result.json", "report_sha256")):
            path = preparer.no_links(folder / filename)
            if not path.is_file() or preparer.sha256(path) != previous.get(field):
                raise ValueError(f"Earlier round {earlier} execution evidence changed or missing: {filename}")
        _, next_phase = verify_round_files(run, earlier + 1)
        if next_phase.get("previous") != previous:
            raise ValueError("Earlier verified evidence changed after the next baseline was prepared")
    verified = verify_evidence(run, manifest, after_round)
    verified["book"] = inspect_book(run, manifest, after_round)
    preparer.write_json(run / f"round-{after_round}" / "verified.json", verified)
    if not final:
        return prepare_round(run, manifest, after_round + 1, verified)
    result = {"schema": 1, "run": str(run), "mechanical_sequence_complete": True,
              "rounds": 3, "formal_chapters": 6, "volumes": manifest["configuration"]["volumes"],
              "native_skill_discovery_tested": False, "quality_assessed": False,
              "execution_status": "needs_review", "review_required": [
                  "独立通读六章、规划与完本回读，核对前次结果是否实际改变后续选择及跨卷条件。",
                  "独立核对六章的阅读体验、人物知情与物件归属；回执与完整导出不能作文学证明。",
                  "此处仅为缩小规模的连续性与事务验收，常规章幅长小说、原生自动选技能与真实读者反馈仍未验证。"]}
    preparer.write_json(run / "sequence-result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    begin = commands.add_parser("start")
    begin.add_argument("--sequence", required=True, choices=sorted(p.stem for p in SEQUENCES.glob("*.json")))
    begin.add_argument("--output")
    advance = commands.add_parser("next")
    advance.add_argument("--run", required=True)
    advance.add_argument("--after-round", required=True, type=int)
    finish = commands.add_parser("finish")
    finish.add_argument("--run", required=True)
    args = parser.parse_args(argv)
    try:
        result = start(args.sequence, args.output) if args.command == "start" else complete_round(
            args.run, 3 if args.command == "finish" else args.after_round, final=args.command == "finish")
    except Exception as error:
        print(json.dumps({"prepared": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
