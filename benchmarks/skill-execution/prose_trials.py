#!/usr/bin/env python3
"""Freeze or inspect a paired prose trial; never launch models or grade fiction."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
CASES = Path(__file__).resolve().parent / "prose-cases"
SAMPLE_EVIDENCE = {"launch": "launch.json", "process": "process-result.json", "trace": "trace.jsonl",
                   "stderr": "stderr.log", "final_message": "final.txt"}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evaluation = load_module("prose_execution_eval", ROOT / "scripts" / "execution_eval.py")
fixture = load_module("prose_execution_fixture", Path(__file__).resolve().parent / "prepare.py")
metrics = load_module("prose_execution_metrics", ROOT / "scripts" / "execution_metrics.py")
review = load_module("prose_execution_review", ROOT / "scripts" / "execution_review.py")
runtime = load_module("prose_runtime", ROOT / "skills" / "story-skill" / "scripts" / "story.py")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def file_hash(path):
    return evaluation.sha(evaluation.safe_path(path).read_bytes())


def read_json(path):
    value, path = evaluation.load_json(path)
    return value


def validate_configuration(value):
    if not isinstance(value, dict) or set(value) != {"conditions", "assessment_mode"}:
        raise ValueError("Configuration requires conditions and assessment_mode")
    if value["assessment_mode"] not in ("blind", "independent", "feedback_aware", "self"):
        raise ValueError("Unknown assessment mode")
    conditions = value["conditions"]
    if not isinstance(conditions, dict) or set(conditions) != {"baseline", "candidate"}:
        raise ValueError("Exactly baseline and candidate conditions are required")
    for name, condition in conditions.items():
        if not isinstance(condition, dict) or set(condition) != {"model", "settings", "repetitions"}:
            raise ValueError(f"{name}: model, settings and repetitions must be declared")
        if not isinstance(condition["model"], str) or not condition["model"].strip():
            raise ValueError(f"{name}: supply the intended model ID; preparation does not choose one")
        if not isinstance(condition["settings"], dict):
            raise ValueError(f"{name}: settings must be an object; unknown settings use null")
        if type(condition["repetitions"]) is not int or not 1 <= condition["repetitions"] <= 20:
            raise ValueError(f"{name}: repetitions must be an integer between 1 and 20")
        digest(condition)  # Reject non-JSON values, NaN and infinities before creating files.
    if conditions["baseline"] != conditions["candidate"]:
        raise ValueError("Paired conditions must have the same model, settings and repetition count")
    return value


def load_case(case_id):
    if case_id not in {p.stem for p in CASES.glob("*.json") if not p.name.endswith(".template.json")}:
        raise ValueError(f"Unknown prose case: {case_id}")
    path = evaluation.safe_path(CASES / f"{case_id}.json")
    case = evaluation.validate_case(read_json(path))
    if case["id"] != case_id:
        raise ValueError("Case ID differs from filename")
    provenance = case.get("provenance", {})
    if provenance.get("origin") != "original_synthetic" or provenance.get("classification") != "development":
        raise ValueError("Bundled cases must remain original synthetic development material")
    if provenance.get("actual_author_complaint") is not False or provenance.get("independent_holdout") is not False:
        raise ValueError("Synthetic examples cannot stand in for actual feedback or independent holdouts")
    fixtures = case.get("fixtures")
    if not isinstance(fixtures, dict) or not fixtures:
        raise ValueError("Prose case needs frozen fixtures")
    for name, text in fixtures.items():
        evaluation.relative_path(name)
        if not isinstance(text, str) or not text:
            raise ValueError(f"Fixture must be nonempty text: {name}")
        change = {"path": name, "kind": "file"}
        if evaluation.allowed(change, case["allowed_changes"]):
            raise ValueError(f"Original fixture cannot be writable: {name}")
    length = case.get("output_length", {})
    if (not isinstance(length, dict) or not length or any(
            name not in case["required_outputs"] or not isinstance(rule, dict)
            or set(rule) != {"min", "max", "count_method"}
            or type(rule["min"]) is not int or type(rule["max"]) is not int
            or not 0 < rule["min"] <= rule["max"]
            or rule["count_method"] != "visible_nonspace_v2"
            for name, rule in length.items())):
        raise ValueError("Each declared output length must bind a required output and a valid range")
    invocation = case["invocation"]
    if invocation != "provided_skill_path:story-skill-review":
        raise ValueError("Prose trials use the frozen review skill")
    return case, path


def validate_skill_source(source):
    # Reuse the existing snapshot policy; reject hard links too so later edits do not
    # mutate a source file shared with a copied artifact through a filesystem link.
    source = evaluation.safe_path(source, directory=True)
    evaluation.snapshot(source)
    for entry in source.rglob("*"):
        if entry.is_file() and entry.stat().st_nlink > 1:
            raise ValueError(f"Hard-linked skill source is not supported: {entry}")
    if not (source / "story-skill-review" / "SKILL.md").is_file():
        raise ValueError(f"Review skill not found: {source}")
    return source


def copy_skills(source, destination):
    source = validate_skill_source(source)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))


def render_task(case, workspace, skills, condition):
    controls = json.dumps(condition, ensure_ascii=False, sort_keys=True)
    return (f"工作目录：{workspace}\n\n{case['prompt']}\n\n"
            f"本任务使用这份技能及其同目录套件：{skills / 'story-skill-review' / 'SKILL.md'}\n"
            f"试验预登记生成条件（实际宿主调用另行保存）：{controls}\n"
            "只读取上述任务、工作区输入与本轮冻结技能；不要读取工作区外的验收标准、版本映射或另一臂输出。\n")


def evidence_slots():
    return {"launch": None, "trace": None, "stderr": None, "final_message": None,
            "execution_check": None, "usage_report": None, "provider_usage_or_cost": None,
            "review_record": None, "actual_model_and_settings": None,
            "actual_conditions_status": "unverified", "all_task_usage": None, "cost": None}


def prepare(case_id, baseline_skills, candidate_skills, configuration, requested_output=None):
    configuration = validate_configuration(configuration)
    case, case_path = load_case(case_id)
    sources = {"baseline": validate_skill_source(baseline_skills),
               "candidate": validate_skill_source(candidate_skills)}
    run = fixture.output_path(requested_output, f"prose-{case_id}")
    if any(evaluation.within(run, source) or evaluation.within(source, run) for source in sources.values()):
        raise ValueError("Run directory and skill sources must not overlap")
    run.parent.mkdir(parents=True, exist_ok=True)
    fixture.no_links(run.parent)
    run.mkdir()
    evaluation.save_new(run / "case.json", case)
    evaluation.save_new(run / "configuration.json", configuration)
    manifest = {"schema": 1, "case_id": case_id, "created_at": datetime.now(timezone.utc).isoformat(),
                "scope": "Frozen paired inputs and file integrity only; execution and literary assessment remain unverified.",
                "provenance": case["provenance"], "case_sha256": file_hash(run / "case.json"),
                "case_source_sha256": file_hash(case_path), "configuration_sha256": file_hash(run / "configuration.json"),
                "declared_conditions": configuration["conditions"], "output_length": case["output_length"],
                "authorization": {"allowed_changes": case["allowed_changes"], "required_outputs": case["required_outputs"]},
                "review_plan": {"requested_mode": configuration["assessment_mode"], "performed_mode": None,
                                "blinding_performed": False, "version_mapping_exposure": "unverified",
                                "prior_feedback_exposure": "unverified", "judgments": None},
                "task_execution": "not_started", "quality_assessed": False, "arms": {}}
    condition = configuration["conditions"]["baseline"]
    for arm, source in sources.items():
        arm_root = run / arm
        arm_root.mkdir()
        skills = arm_root / "skills"
        copy_skills(source, skills)
        skills_snapshot = evaluation.snapshot(skills)
        arm_manifest = {"source_skills_root": str(source), "skills_snapshot": skills_snapshot,
                        "skills_snapshot_sha256": digest(skills_snapshot), "samples": []}
        for number in range(1, condition["repetitions"] + 1):
            sample = arm_root / f"sample-{number}"
            workspace = sample / "workspace"
            workspace.mkdir(parents=True)
            for relative, text in case["fixtures"].items():
                target = fixture.fixture_path(workspace, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("x", encoding="utf-8") as stream:
                    stream.write(text)
            task = sample / "task.txt"
            with task.open("x", encoding="utf-8") as stream:
                stream.write(render_task(case, workspace, skills, condition))
            baseline = sample / "baseline.json"
            evaluation.start(argparse.Namespace(case_file=run / "case.json", workspace=workspace,
                                                 skills_root=skills, output=baseline))
            record = {"sample": number, "relative_root": sample.relative_to(run).as_posix(),
                      "task_sha256": file_hash(task), "baseline_sha256": file_hash(baseline),
                      "input_snapshot": evaluation.snapshot(workspace), "evidence": evidence_slots()}
            arm_manifest["samples"].append(record)
        manifest["arms"][arm] = arm_manifest
    evaluation.save_new(run / "trial.json", manifest)
    evaluation.save_new(run / "review-record.template.json", {
        "schema": 1, "case_id": case_id, "classification": "development",
        "trial_sha256": file_hash(run / "trial.json"), "reviewer": {"kind": None, "mode": None},
        "actual_blinding": None, "has_seen_prior_feedback": None, "artifact_refs": [],
        "reading_observations": [], "remaining_issues": [], "scope_following": None,
        "preference": None, "original_feedback_record": None,
        "note": "空模板不是评阅结果。blind、independent、feedback_aware、self 按真实过程记录；不得补造盲评或作者反馈。"})
    return {"prepared": True, "run": str(run), "trial": str(run / "trial.json"),
            "samples_per_arm": condition["repetitions"], "task_execution": "not_started", "quality_assessed": False}


def verify(requested_run):
    run = evaluation.safe_path(requested_run, True)
    manifest = read_json(run / "trial.json")
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise ValueError("Unsupported trial schema")
    issues = []
    for name, key in (("case.json", "case_sha256"), ("configuration.json", "configuration_sha256")):
        if file_hash(run / name) != manifest[key]:
            issues.append(f"Frozen {name} has changed")
    configuration = validate_configuration(read_json(run / "configuration.json"))
    case = evaluation.validate_case(read_json(run / "case.json"))
    if configuration["conditions"] != manifest["declared_conditions"]:
        issues.append("Declared conditions differ from their frozen manifest")
    if case["output_length"] != manifest["output_length"] or {
            "allowed_changes": case["allowed_changes"], "required_outputs": case["required_outputs"]} != manifest["authorization"]:
        issues.append("Length or authorization differs from its frozen manifest")
    input_snapshots = {}
    for arm in ("baseline", "candidate"):
        record = manifest["arms"][arm]
        skills = evaluation.safe_path(run / arm / "skills", True)
        observed = evaluation.snapshot(skills)
        if observed != record["skills_snapshot"] or digest(observed) != record["skills_snapshot_sha256"]:
            issues.append(f"{arm}: frozen skill snapshot has changed")
        if len(record["samples"]) != configuration["conditions"][arm]["repetitions"]:
            issues.append(f"{arm}: repetition count differs from declaration")
        for index, sample in enumerate(record["samples"], 1):
            expected_root = f"{arm}/sample-{index}"
            if sample["relative_root"] != expected_root or sample["sample"] != index:
                raise ValueError("Sample path or order differs from fixed paired layout")
            sample_root = evaluation.safe_path(run / expected_root, True)
            workspace = evaluation.safe_path(sample_root / "workspace", True)
            baseline = evaluation.safe_path(sample_root / "baseline.json")
            task = evaluation.safe_path(sample_root / "task.txt")
            if file_hash(baseline) != sample["baseline_sha256"]:
                issues.append(f"{expected_root}: baseline has changed")
            expected_task = render_task(case, workspace, skills, configuration["conditions"][arm])
            if file_hash(task) != sample["task_sha256"] or task.read_text(encoding="utf-8") != expected_task:
                issues.append(f"{expected_root}: task has changed")
            current = evaluation.snapshot(workspace)
            changes = evaluation.changes(sample["input_snapshot"], current)
            unauthorized = [c for c in changes if not evaluation.allowed(c, case["allowed_changes"])]
            if unauthorized:
                issues.append(f"{expected_root}: protected inputs or unauthorized files changed: {unauthorized}")
            input_snapshots[expected_root] = sample["input_snapshot"]
    if len({digest(value) for value in input_snapshots.values()}) != 1:
        issues.append("Paired input snapshots are not identical")
    return {"frozen_inputs_intact": not issues, "issues": issues,
            "actual_conditions_status": "unverified", "task_execution": "unverified", "quality_assessed": False,
            "note": "Run execution_eval.check and execution_metrics on each real run, then perform source-cited content review. This check does not read or certify evidence slots."}


def sample_key(value):
    """Accept selectors, never arbitrary paths into a trial."""
    parts = value.split("/") if isinstance(value, str) else []
    if (len(parts) != 2 or parts[0] not in ("baseline", "candidate")
            or not parts[1].isascii() or not parts[1].isdigit()
            or str(int(parts[1])) != parts[1] or not 1 <= int(parts[1]) <= 20):
        raise ValueError("Sample must be baseline/N or candidate/N, with N between 1 and 20")
    return parts[0], int(parts[1])


def evidence_file(path):
    path = evaluation.safe_path(path)
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"Regular evidence file required: {path}")
    raw = path.read_bytes()
    return {"path": str(path), "sha256": evaluation.sha(raw), "bytes": len(raw)}


def evidence_json(path):
    ref = evidence_file(path)
    if ref is None:
        return None
    value = json.loads(Path(ref["path"]).read_text(encoding="utf-8"),
                       object_pairs_hook=metrics.unique_keys, parse_constant=metrics.reject_constant)
    if not isinstance(value, dict):
        raise ValueError(f"Evidence must be a JSON object: {path}")
    return value


def launch_conditions(launch, task, workspace, condition):
    """Compare declared settings with a supported *recorded* argv, not model identity."""
    result = {"status": "unverified", "binding_status": "unverified", "issues": [],
              "declared": condition, "fields": {}, "actual_model_and_settings": "unverified",
              "scope": "Recorded Codex CLI launch only; capture origin and actual serving model are not authenticated."}
    if launch is None:
        return result
    argv = launch.get("argv")
    if not isinstance(argv, list) or not argv or any(not isinstance(word, str) for word in argv):
        raise ValueError("Launch requires the original argv array")
    result["recorded_argv"] = argv
    if len(argv) < 3 or Path(argv[0]).stem.lower() != "codex" or argv[1] != "exec" or argv[-1] != "-" or "--" in argv:
        result["issues"].append("Unsupported launch adapter; expected codex exec with the frozen task on stdin")
        return result
    observed, conflicts, unverified = {}, [], []
    result["unverified_overrides"] = unverified

    def remember(key, value):
        if (key in observed and observed[key] != value
                and not isinstance(observed[key], dict) and not isinstance(value, dict)):
            conflicts.append(f"Conflicting launch options for {key}")
        observed[key] = value

    options = {"--model": "model", "-m": "model", "--cd": "cwd", "-C": "cwd",
               "--config": "config", "-c": "config", "--sandbox": "capture:sandbox",
               "--output-last-message": "capture:last_message", "-o": "capture:last_message",
               "--color": "capture:color"}
    flags = {"--json", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules",
             "--full-auto", "--dangerously-bypass-approvals-and-sandbox"}
    known_configs = {"model", "model_reasoning_effort", "seed", "temperature", "model_revision",
                     "approval_policy", "sandbox_mode"}
    index = 2
    while index < len(argv) - 1:
        word = argv[index]
        flag, equals, value = word.partition("=")
        if flag not in options:
            if word not in flags:
                unverified.append(word)
            index += 1
            continue
        if not equals:
            index += 1
            if index >= len(argv) - 1:
                raise ValueError(f"Launch option lacks a value: {flag}")
            value = argv[index]
        key = options[flag]
        if key == "config":
            key, separator, value = value.partition("=")
            key = key.strip()
            if not separator or not key:
                raise ValueError("Launch -c/--config requires key=value")
            try:
                value = json.loads(value, parse_constant=metrics.reject_constant)
            except ValueError:
                # Retain unsupported TOML verbatim; never invent its parsed value.
                value = {"unparsed_config": value}
                unverified.append({"config": key, "value": value})
            if key not in known_configs:
                unverified.append({"config": key, "reason": "Unsupported configuration override"})
            key = "reasoning_effort" if key == "model_reasoning_effort" else key
        remember(key, value)
        index += 1
    issues = result["issues"]
    issues.extend(conflicts)
    task_hash = file_hash(task)
    task_hashes = [launch[key] for key in ("task_sha256", "stdin_task_sha256") if key in launch]
    if not task_hashes:
        result["issues"].append("No captured stdin/task hash")
    elif any(value != task_hash for value in task_hashes):
        issues.append("Launch task hash differs from the frozen task")
    cwd = observed.get("cwd", launch.get("cwd"))
    if isinstance(cwd, str) and cwd:
        if not Path(cwd).is_absolute():
            startup = launch.get("cwd")
            cwd = str(Path(startup) / cwd) if isinstance(startup, str) and Path(startup).is_absolute() else None
        if cwd is not None and evaluation.safe_path(cwd) != workspace:
            issues.append("Launch working directory differs from the selected workspace")
    else:
        cwd = None
    if "model" in condition["settings"] and condition["settings"]["model"] != condition["model"]:
        conflicts.append("Conflicting declared model and settings.model")
        issues.append(conflicts[-1])
    for name, declared in {**condition["settings"], "model": condition["model"]}.items():
        value = observed.get(name)
        comparable = (declared is not None and name in observed
                      and not isinstance(value, dict) and name != "host_version")
        status = "matches_launch" if comparable and value == declared else "mismatch" if comparable else "unverified"
        result["fields"][name] = {"declared": declared, "recorded_launch": value, "status": status}
        if status == "mismatch":
            issues.append(f"Declared {name} differs from the recorded launch")
    for key, requested in (("model", "model_requested"), ("reasoning_effort", "reasoning_effort_requested")):
        if requested in launch and key in observed and not isinstance(observed[key], dict) and launch[requested] != observed[key]:
            issues.append(f"Launch {requested} declaration contradicts its argv")
    mismatch = bool(conflicts or any("differs" in issue or "contradicts" in issue for issue in issues))
    binding = bool(task_hashes and cwd is not None and not mismatch)
    result["binding_status"] = "matches_recorded_launch" if binding else "mismatch" if mismatch else "unverified"
    result["status"] = ("mismatch" if mismatch else "partially_observed" if any(
        field["status"] == "matches_launch" for field in result["fields"].values()) else "unverified")
    required = ["model"] + (["reasoning_effort"] if condition["settings"].get("reasoning_effort") is not None else [])
    result["required_options_observed"] = not unverified and all(
        result["fields"][name]["status"] == "matches_launch" for name in required)
    return result


def inspect_review(record_path, baseline, checked, task, trace, output):
    result = {"status": "unverified", "independent_assignment_status": "unverified",
              "scope": review.REVIEW_LINK_SCOPE, "record": evidence_file(record_path)}
    if result["record"] is None:
        return result
    record = evidence_json(record_path)
    # Bind a supplied existing record to this sample, not merely some valid trial.
    if record.get("task") != {"path": str(task), "sha256": file_hash(task)}:
        raise ValueError("Review task reference does not identify the selected sample")
    invocation = record.get("invocation", {})
    if isinstance(invocation, dict) and invocation.get("launch"):
        ref = invocation["launch"]
        if ref != {"path": str(task.parent / "launch.json"), "sha256": file_hash(task.parent / "launch.json")}:
            raise ValueError("Review launch reference does not identify the selected sample")
    report, _ = review.check(argparse.Namespace(
        baseline=baseline, check_file=checked, record=record_path, output=output, source_skills_root=None))
    result.update(status=report["validation_status"], reviewer=report["reviewer"], claims=report["claims"])
    if trace["status"] == "complete" and any(
            claim["kind"] == "independent_review" and claim["status"] == "observed"
            and claim["evidence_status"] == "traceable" for claim in report["claims"]):
        result["independent_assignment_status"] = "observed"
    return result


def inspect_sample(run, arm, number, condition, case, frozen, review_record=None):
    sample = evaluation.safe_path(run / arm / f"sample-{number}", True)
    workspace = evaluation.safe_path(sample / "workspace", True)
    result = {"sample": f"{arm}/{number}", "relative_root": sample.relative_to(run).as_posix(),
              "status": "incomplete", "issues": [], "quality_assessed": False,
              "actual_conditions_status": "unverified", "all_task_usage": None, "cost": None}
    try:
        refs = {name: evidence_file(sample / filename) for name, filename in SAMPLE_EVIDENCE.items()}
        result["evidence"] = refs
        launch = evidence_json(sample / "launch.json")
        process = evidence_json(sample / "process-result.json")
        result["launch_conditions"] = launch_conditions(launch, sample / "task.txt", workspace, condition)
        result["process"] = {"status": "unverified", "receipt": process}
        if process is not None:
            if (type(process.get("timed_out")) is not bool or "exit_code" not in process
                    or (process["exit_code"] is not None and type(process["exit_code"]) is not int)):
                raise ValueError("Process receipt requires timed_out (boolean) and exit_code (integer or null)")
            if (process.get("case", case["id"]) != case["id"] or process.get("arm", arm) != arm
                    or process.get("sample", number) != number):
                raise ValueError("Process receipt case/arm/sample differs from the selected sample")
            result["process"]["status"] = ("failed" if process["timed_out"] or process["exit_code"] not in (None, 0)
                                           else "recorded_success" if process["exit_code"] == 0 else "incomplete")
        with tempfile.TemporaryDirectory(prefix="story-prose-inspection-") as scratch:
            scratch = Path(scratch).resolve()
            checked_path = scratch / "check.json"
            checked, _ = evaluation.check(argparse.Namespace(
                baseline=sample / "baseline.json", trace=refs["trace"]["path"] if refs["trace"] else None,
                output=checked_path))
            result["execution_check"] = checked
            result["usage"] = metrics.summarize([refs["trace"]["path"]]) if refs["trace"] else None
            result["output_length"] = {}
            for name, rule in case["output_length"].items():
                path = evaluation.safe_path(workspace / evaluation.relative_path(name))
                count = runtime.manuscript_counts(path.read_text(encoding="utf-8"))[rule["count_method"]] if path.is_file() else None
                result["output_length"][name] = {**rule, "actual": count,
                    "status": "missing" if count is None else "within_range" if rule["min"] <= count <= rule["max"] else "out_of_range"}
            blank = [name for name in case["required_outputs"] if (workspace / name).is_file()
                     and not (workspace / name).read_text(encoding="utf-8").strip()]
            result["blank_required_outputs"] = blank
            result["review"] = inspect_review(
                review_record or sample / "review-record.json", sample / "baseline.json", checked_path,
                sample / "task.txt", checked["trace"], scratch / "review-check.json")
        changes = checked["checks"]
        length_failed = any(item["status"] == "out_of_range" for item in result["output_length"].values())
        terminal = result["process"]["status"] in ("recorded_success", "failed") or checked["trace"]["status"] in ("complete", "failed")
        started = bool(any(refs.values()) or changes["workspace_changes"] or result["review"]["record"])
        failed = (not frozen["frozen_inputs_intact"] or changes["unauthorized_changes"] or changes["skill_changes"]
                  or length_failed or blank or result["process"]["status"] == "failed"
                  or checked["trace"]["status"] == "failed" or result["launch_conditions"]["status"] == "mismatch"
                  or (result["usage"] and result["usage"]["status"] == "invalid")
                  or result["review"]["status"] == "failed" or (terminal and not checked["mechanical_ok"]))
        complete = (checked["mechanical_ok"] and checked["trace"]["status"] == "complete"
                    and result["process"]["status"] == "recorded_success"
                    and result["launch_conditions"]["binding_status"] == "matches_recorded_launch"
                    and result["launch_conditions"].get("required_options_observed"))
        result["status"] = "failed" if failed else "not_started" if not started else "needs_content_review" if complete else "incomplete"
    except (ValueError, OSError, KeyError, TypeError, UnicodeError) as error:
        result["status"] = "failed"
        result["issues"].append({"error": type(error).__name__, "message": str(error)})
    return result


def inspect(requested_run, samples=None, review_records=None):
    """Read existing trials; temporary checker reports never alter captured evidence."""
    run = evaluation.safe_path(requested_run, True)
    frozen = verify(run)
    manifest = read_json(run / "trial.json")
    case = evaluation.validate_case(read_json(run / "case.json"))
    available = {f"{arm}/{item['sample']}": (arm, item["sample"])
                 for arm in ("baseline", "candidate") for item in manifest["arms"][arm]["samples"]}
    selected = list(available) if samples is None else list(samples)
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("Select at least one sample, without duplicates")
    for value in selected:
        sample_key(value)
        if value not in available:
            raise ValueError(f"Sample was not prepared: {value}")
    review_records = review_records or {}
    if set(review_records) - set(selected):
        raise ValueError("Review records must identify selected samples")
    reports = [inspect_sample(run, *available[key], manifest["declared_conditions"][available[key][0]],
                              case, frozen, review_records.get(key)) for key in selected]
    counts = {status: sum(item["status"] == status for item in reports)
              for status in ("not_started", "incomplete", "failed", "needs_content_review")}
    status = ("failed" if counts["failed"] else "not_started" if counts["not_started"] == len(reports)
              else "needs_content_review" if counts["needs_content_review"] == len(reports) else "incomplete")
    return {"schema": 1, "run": str(run), "scope": "Read-only execution evidence inspection; no literary score or certification.",
            "status": status, "selected_samples": selected, "sample_counts": counts, "frozen_inputs": frozen,
            "samples": reports, "quality_assessed": False, "actual_conditions_status": "unverified",
            "limits": ["Recorded files/hashes depend on trusted capture; their origin is not authenticated.",
                       "needs_content_review means observable execution completed; literary and semantic review remain required.",
                       "Only fixed per-sample launch.json, process-result.json, trace.jsonl, stderr.log and final.txt are discovered; manifest evidence claims are not certified.",
                       "Unknown model revisions, host versions and settings remain unverified; no provider charges or full-task usage are inferred.",
                       "Independent assignment links establish dispatch/return/version references only, not actual reading or correct judgements.",
                       "Tree snapshots do not detect transient writes restored before inspection or writes outside captured trees."]}


def inspection_output(path, run, protected=()):
    """Allow an exclusive report beside evidence, outside all protected trees."""
    manifest = read_json(run / "trial.json")
    roots = [run / arm / "skills" for arm in ("baseline", "candidate")]
    reserved = [run / name for name in ("trial.json", "case.json", "configuration.json", "review-record.template.json")]
    for arm in ("baseline", "candidate"):
        record = manifest["arms"][arm]
        roots.append(evaluation.safe_path(record["source_skills_root"]))
        roots.extend(run / item["relative_root"] / "workspace" for item in record["samples"])
        for item in record["samples"]:
            sample = run / item["relative_root"]
            reserved.extend(sample / name for name in (*SAMPLE_EVIDENCE.values(), "review-record.json", "task.txt", "baseline.json"))
    return evaluation.output_path(path, roots, (*reserved, *protected))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("prepare")
    start.add_argument("--case", required=True)
    start.add_argument("--baseline-skills", required=True)
    start.add_argument("--candidate-skills", required=True)
    start.add_argument("--configuration", required=True)
    start.add_argument("--output")
    check = commands.add_parser("verify")
    check.add_argument("--run", required=True)
    inspect_parser = commands.add_parser("inspect")
    inspect_parser.add_argument("--run", required=True)
    inspect_parser.add_argument("--sample", action="append", help="baseline/N or candidate/N; default selects all prepared samples")
    inspect_parser.add_argument("--review-record", action="append", default=[], metavar="SAMPLE=PATH",
                                help="Optional existing execution_review record; default sample-N/review-record.json")
    inspect_parser.add_argument("--output", help="Exclusively create a new report; default stdout")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(args.case, args.baseline_skills, args.candidate_skills,
                             read_json(args.configuration), args.output)
            code = 0
        elif args.command == "verify":
            result = verify(args.run)
            code = 0 if result["frozen_inputs_intact"] else 1
        else:
            records = {}
            for value in args.review_record:
                key, separator, path = value.partition("=")
                sample_key(key)
                if not separator or not path or key in records:
                    raise ValueError("Each review record requires a unique SAMPLE=PATH")
                records[key] = evaluation.safe_path(path)
            result = inspect(args.run, args.sample, records)
            code = 1 if result["status"] == "failed" else 0 if result["status"] == "needs_content_review" else 2
            if args.output:
                evaluation.save_new(inspection_output(args.output, evaluation.safe_path(args.run, True), records.values()), result)
    except (ValueError, OSError, KeyError, TypeError) as error:
        result, code = {"error": str(error), "quality_assessed": False}, 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
