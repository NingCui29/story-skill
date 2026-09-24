#!/usr/bin/env python3
"""Run local release checks and write an evidence report; no model or API calls."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import unquote, urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/story-skill"
SKILLS = ROOT / "skills"
PYTHON = [sys.executable, "-B", "-X", "utf8"]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(command, timeout):
    started = time.monotonic()
    result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=timeout)
    return {"command": list(map(str, command)), "exit_code": result.returncode,
            "stdout": result.stdout.decode("utf-8", errors="replace"),
            "stderr": result.stderr.decode("utf-8", errors="replace"),
            "duration_seconds": round(time.monotonic() - started, 3),
            "status": "passed" if result.returncode == 0 else "failed"}


class CountedTestResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.successes = 0

    def addSuccess(self, test):
        super().addSuccess(test)
        self.successes += 1


def summarize_unittest(result):
    """Count success callbacks; multiple failed subtests must not create negative counts."""
    return {"status": "passed" if result.wasSuccessful() and result.successes > 0 else "failed",
            "run": result.testsRun, "passed": result.successes, "failed": len(result.failures),
            "errors": len(result.errors), "skipped": len(result.skipped),
            "expected_failures": len(result.expectedFailures),
            "unexpected_successes": len(result.unexpectedSuccesses),
            "counting": "Successful test cases use addSuccess; failure/error/skip counts may include subtests"}


def unittest_child(output):
    # Run the suite from this repository even when an unrelated installed
    # package also uses the top-level name "tests".
    sys.path.insert(0, str(ROOT))
    runner = unittest.TextTestRunner(resultclass=CountedTestResult)
    program = unittest.main(module=None, argv=["unittest", "discover", "-s", "tests"],
                            testRunner=runner, exit=False)
    result = summarize_unittest(program.result)
    write_report({"ok": result["status"] == "passed", **result}, output)
    return 0 if result["status"] == "passed" else 1


def check_unittest(output, timeout):
    result = run(PYTHON + [str(Path(__file__).resolve()), "--unittest-report", str(output)], timeout)
    if not output.is_file():
        return {**result, "status": "failed", "error": "No complete unittest result was produced"}
    evidence = json.loads(output.read_text(encoding="utf-8"))
    status = "passed" if result["exit_code"] == 0 and evidence["status"] == "passed" else "failed"
    return {**result, **evidence, "status": status,
            "discovery_argv": ["unittest", "discover", "-s", "tests"]}


def select_archive(explicit=None):
    if explicit:
        return Path(explicit).expanduser().resolve()
    package = package_module()
    version = package.current_version(SKILL / "scripts/story.py")
    path = ROOT / f"dist/story-skill-{version}.zip"
    if not path.is_file():
        raise ValueError(f"No archive for runtime VERSION {version}; run scripts/package.py or pass --archive")
    return path.resolve()


def package_module():
    spec = importlib.util.spec_from_file_location("story_release_package", ROOT / "scripts/package.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def current_evidence_directory():
    version = package_module().current_version(SKILL / "scripts/story.py")
    return ROOT / "benchmarks/results" / f"v{version}"


def skill_files():
    files = {}
    names = package_module().SKILL_NAMES
    actual_names = {path.name for path in SKILLS.iterdir() if path.is_dir()}
    if actual_names != set(names):
        raise ValueError("Skill suite directories differ from the supported skill list")
    for path in sorted(SKILLS.rglob("*")):
        relative = path.relative_to(SKILLS)
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError(f"Linked skill content: {relative}")
        if path.is_file() and "__pycache__" not in relative.parts and path.suffix != ".pyc":
            files[relative.as_posix()] = digest(path)
    for name in names:
        if not {f"{name}/SKILL.md", f"{name}/agents/openai.yaml", f"{name}/LICENSE"} <= files.keys():
            raise ValueError(f"Skill is incomplete: {name}")
    if "story-skill/scripts/story.py" not in files:
        raise ValueError("Shared runtime is missing")
    return files


def check_archive(path):
    package = package_module()
    version = package.current_version(SKILL / "scripts/story.py")
    package.validate_archive_name(path, version)
    expected = skill_files()
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        names = [member.filename for member in members]
        non_regular = sorted(member.filename for member in members
                             if member.is_dir() or stat.S_IFMT(member.external_attr >> 16)
                             not in (0, stat.S_IFREG))
        actual = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in names}
        corrupt = archive.testzip()
    mismatches = sorted(name for name in actual.keys() & expected.keys() if actual[name] != expected[name])
    missing, extra = sorted(expected.keys() - actual.keys()), sorted(actual.keys() - expected.keys())
    duplicates = sorted(name for name in set(names) if names.count(name) > 1)
    matches = not (missing or extra or mismatches or duplicates or corrupt or non_regular)
    return {"status": "passed" if matches else "failed", "archive": str(path), "version": version,
            "sha256": digest(path), "bytes": path.stat().st_size, "files": len(names),
            "contents_match_current_skill": matches, "missing": missing, "extra": extra,
            "changed": mismatches, "duplicate_entries": duplicates, "corrupt_entry": corrupt,
            "non_regular_entries": non_regular}


def check_benchmark():
    config = json.loads((ROOT / "benchmarks/profiles.json").read_text(encoding="utf-8"))
    path = ROOT / config.get("report_path", "benchmarks/results/tokens.json")
    report = json.loads(path.read_text(encoding="utf-8"))
    profiles = {item["id"]: item for item in report["profiles"]}
    problems, files = [], []
    if type(report.get("schema")) is not int or report["schema"] != 1:
        problems.append("Unsupported benchmark report schema")
    if len(profiles) != len(report["profiles"]) or len({item["id"] for item in config["profiles"]}) != len(config["profiles"]):
        problems.append("Duplicate benchmark profile IDs")
    for recorded_key, configured_key in (("repository", "repository"), ("upstream_revision", "revision"),
                                         ("encoding", "encoding")):
        if report.get(recorded_key) != config[configured_key]:
            problems.append(f"Benchmark {configured_key} differs from profiles.json")
    if [item["id"] for item in report["profiles"]] != [item["id"] for item in config["profiles"]]:
        problems.append("Benchmark profile IDs or order differ from profiles.json")
    for profile in config["profiles"]:
        measured = profiles.get(profile["id"])
        if measured is None:
            continue
        for field in ("label", "scope"):
            if measured.get(field) != profile[field]:
                problems.append(f"Benchmark {field} differs: {profile['id']}")
        lists_match = True
        for side in ("upstream", "candidate"):
            paths = [entry["path"] for entry in measured.get(f"{side}_files", [])]
            if paths != profile[side]:
                problems.append(f"{side.capitalize()} file list differs: {profile['id']}")
                lists_match = False
            if len(set(paths)) != len(paths) or len(set(profile[side])) != len(profile[side]):
                problems.append(f"Duplicate {side} file paths: {profile['id']}")
                lists_match = False
        if not lists_match:
            continue
        totals = {}
        for side in ("upstream", "candidate"):
            tokens = [entry.get("tokens") for entry in measured[f"{side}_files"]]
            if not all(type(value) is int and value >= 0 for value in tokens):
                problems.append(f"Invalid {side} token counts: {profile['id']}")
                continue
            totals[side] = sum(tokens)
            if type(measured.get(f"{side}_tokens")) is not int or measured[f"{side}_tokens"] != totals[side]:
                problems.append(f"{side.capitalize()} token total differs from file counts: {profile['id']}")
        if totals.get("upstream", 0) <= 0:
            problems.append(f"Upstream token total must be positive: {profile['id']}")
        elif "candidate" in totals:
            reduction = round((1 - totals["candidate"] / totals["upstream"]) * 100, 2)
            if measured.get("reduction_percent") != reduction:
                problems.append(f"Reduction percent differs from file counts: {profile['id']}")
        recorded = measured["candidate_files"]
        for entry in recorded:
            relative = Path(entry["path"])
            source = (ROOT / relative).resolve()
            source.relative_to(ROOT)
            actual = digest(source)
            normalized = source.read_bytes().decode("utf-8-sig").replace("\r\n", "\n")
            normalized_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            matches = actual == entry["sha256"] and normalized_hash == entry["normalized_text_sha256"]
            files.append({"profile": profile["id"], "path": entry["path"], "matches": matches,
                          "recorded_sha256": entry["sha256"], "current_sha256": actual})
    passed = not problems and bool(files) and all(item["matches"] for item in files)
    return {"status": "passed" if passed else "failed", "report_sha256": digest(path),
            "scope": "Candidate file hashes, both input lists, profile configuration and arithmetic; "
                     "tokenizer not rerun and upstream file contents not revalidated",
            "files": files, "problems": problems}


def check_markdown_links():
    files = set(ROOT.glob("*.md"))
    for directory in (ROOT / "docs", ROOT / "benchmarks", SKILLS):
        files.update(directory.rglob("*.md"))
    checked, missing = [], []
    link = re.compile(r"!?\[[^\]\n]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+[\"'][^\n]*[\"'])?\s*\)")
    for path in sorted(files):
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"^\s*(`{3,}|~{3,}).*?^\s*\1\s*$", "", text, flags=re.MULTILINE | re.DOTALL)
        text = re.sub(r"`[^`\n]*`", "", text)
        for match in link.finditer(text):
            target = match.group(1) or match.group(2)
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            resolved = (path.parent / unquote(parsed.path)).resolve()
            item = {"source": path.relative_to(ROOT).as_posix(), "target": target,
                    "exists": resolved.exists()}
            checked.append(item)
            if not item["exists"]:
                missing.append(item)
    return {"status": "passed" if not missing else "failed", "files_scanned": len(files),
            "links_checked": len(checked), "missing": missing,
            "scope": "Inline local Markdown file targets; remote URLs and heading anchors are not checked"}


def check_smoke(output, timeout, installed_tool=None):
    if installed_tool is None:
        command = PYTHON + [str(ROOT / "scripts/smoke.py"), "--output", str(output)]
    else:
        code = ("import importlib.util,pathlib,sys; "
                "spec=importlib.util.spec_from_file_location('release_smoke',sys.argv[1]); "
                "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
                "module.TOOL=pathlib.Path(sys.argv[2]); "
                "sys.argv=[sys.argv[1],'--output',sys.argv[3]]; module.main()")
        command = PYTHON + ["-c", code, str(ROOT / "scripts/smoke.py"), str(installed_tool), str(output)]
    result = run(command, timeout)
    if result["exit_code"] == 0:
        evidence = json.loads(output.read_text(encoding="utf-8"))
        result["evidence"] = evidence
        if evidence.get("ok") is not True:
            result["status"] = "failed"
    return result


def check_chinese(output, timeout):
    result = run(PYTHON + [str(ROOT / "scripts/chinese_acceptance.py"), "--output", str(output)], timeout)
    if result["exit_code"] != 0 or not output.is_file():
        return {**result, "status": "failed", "error": "Chinese manuscript CLI replay did not complete"}
    evidence = json.loads(output.read_text(encoding="utf-8"))
    return {**result, "status": "passed" if evidence.get("ok") is True else "failed", "result": evidence}


def check_install(temp, timeout):
    project = temp / "安装验证项目"
    installed = run(PYTHON + [str(ROOT / "scripts/install.py"), "--project", str(project)], timeout)
    if installed["exit_code"] != 0:
        return installed
    receipt = json.loads(installed["stdout"])
    parent = project / ".agents/skills"
    target = parent / "story-skill"
    expected = skill_files()
    actual, managed = {}, {}
    for name in package_module().SKILL_NAMES:
        directory = parent / name
        marker = json.loads((directory / ".story-skill-install.json").read_text(encoding="utf-8"))
        managed.update({f"{name}/{key}": value for key, value in marker["files"].items()})
        actual.update({f"{name}/{path.relative_to(directory).as_posix()}": digest(path)
                       for path in directory.rglob("*") if path.is_file()
                       and path.name != ".story-skill-install.json"})
    matching = expected == actual == managed
    help_result = run(PYTHON + [str(target / "scripts/story.py"), "--help"], timeout)
    smoke_result = check_smoke(temp / "installed-smoke.json", timeout, target / "scripts/story.py")
    passed = matching and receipt.get("status") == "installed" and all(
        result["status"] == "passed" for result in (installed, help_result, smoke_result))
    return {"status": "passed" if passed else "failed", "files": len(actual),
            "skills": len(package_module().SKILL_NAMES),
            "installed_files_match": matching, "installation": installed, "receipt": receipt,
            "cli_help": help_result, "cli_smoke": smoke_result}


def report_path(output):
    """Honor an explicit output location without following any pre-existing links."""
    path = Path(output).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            attributes = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(attributes.st_mode) or (getattr(attributes, "st_file_attributes", 0)
                                               & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise ValueError(f"Refusing linked report output path: {current}")
    return path.resolve()


def write_report(report, output):
    """Retain an existing report when a later check fails, and publish JSON atomically."""
    output = report_path(output)
    if not report["ok"] and output.exists():
        output = output.with_name(output.stem + ".failed" + output.suffix)
        output = report_path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".verification-", suffix=".json", dir=output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(name, output)
    finally:
        stage = Path(name)
        if stage.exists():
            stage.unlink()
    return output


def verify(archive=None, validator=None, validator_python=None, timeout=300):
    report = {"schema": 2, "date": datetime.now(timezone.utc).isoformat(),
              "environment": {"platform": platform.platform(), "python": platform.python_version(),
                              "executable": sys.executable},
              "not_verified": ["Host-app skill discovery and automatic routing in a new conversation",
                               "Live model-generated novel quality compared with upstream",
                               "Actual account usage or total-turn token savings",
                               "Long-run consistency over hundreds of generated chapters"]}
    checks = []

    def check(name, action):
        print(f"verify: {name}", file=sys.stderr, flush=True)
        try:
            result = action()
        except Exception as error:
            result = {"status": "failed", "error": str(error), "error_type": type(error).__name__}
        report[name] = result
        checks.append(result)
        return result

    baseline = check("skill_input_snapshot", lambda: {"status": "passed", "files": skill_files()})
    with tempfile.TemporaryDirectory(prefix="story-release-verify-") as directory:
        temp = Path(directory).resolve()
        check("unit_tests", lambda: check_unittest(temp / "unittest.json", timeout))
        check("cli_smoke", lambda: check_smoke(temp / "smoke.json", timeout))
        check("chinese_manuscript_replay", lambda: check_chinese(temp / "chinese.json", timeout))
        check("long_form_cli_replay", lambda: check_long_form(temp / "long-form.json", timeout))
        if validator:
            def validate_skills():
                results = {name: run(
                    [str(validator_python or sys.executable), "-B", "-X", "utf8",
                     str(Path(validator).expanduser().resolve()), str(SKILLS / name)], timeout)
                    for name in package_module().SKILL_NAMES}
                return {"status": "passed" if all(item["status"] == "passed" for item in results.values())
                        else "failed", "skills": results}
            check("skill_frontmatter", validate_skills)
        else:
            report["skill_frontmatter"] = {"status": "skipped", "reason": "No --skill-validator supplied"}
        check("actual_package_install", lambda: check_install(temp, timeout))
        check("benchmark_candidate_file_hashes", check_benchmark)
        check("scale_and_migration_evidence", check_recorded_probes)
        check("local_markdown_links", check_markdown_links)
        check("package", lambda: check_archive(select_archive(archive)))
        check("skill_input_stability", lambda: {"status": "passed" if (
            baseline.get("files") == skill_files()) else "failed",
            "scope": "Skill files must remain unchanged throughout verification"})
    report["ok"] = all(result["status"] == "passed" for result in checks)
    return report


def check_long_form(output, timeout):
    result = run(PYTHON + [str(ROOT / "scripts/long_acceptance.py"), "--output", str(output)], timeout)
    if output.is_file():
        evidence = json.loads(output.read_text(encoding="utf-8"))
        result["evidence"] = evidence
        if not evidence.get("ok") or not evidence.get("runtime_stable"):
            result["status"] = "failed"
    else:
        result["status"] = "failed"
    return result


def check_recorded_probes():
    current = {p.name: digest(p) for p in (SKILL / "scripts").glob("*.py")}
    directory = current_evidence_directory()
    results = []
    for filename, key in (("scaling.json", "runtime_files"), ("migration.json", "runtime")):
        path = directory / filename
        evidence = json.loads(path.read_text(encoding="utf-8"))
        valid = evidence.get("ok") is True and evidence.get(key) == current
        if filename.startswith("scaling"):
            coverage = {(c["chapters"], c["cards"], c["integrity_mode"]) for c in evidence.get("cases", []) if c.get("ok") is True}
            valid = valid and evidence.get("runtime_stable") is True and {
                (400,2000,"strict"),(400,2000,"local"),(4000,20000,"strict"),(4000,20000,"local")} <= coverage
        else:
            valid = valid and evidence.get("original_tree_unchanged") is True and len(evidence.get("books", [])) == 3
        results.append({"path": str(path), "sha256": digest(path), "matches_current_runtime": evidence.get(key) == current,
                        "status": "passed" if valid else "failed"})
    return {"status": "passed" if all(r["status"] == "passed" for r in results) else "failed", "reports": results,
            "scope": "Stored probe success, required capacity sizes and current runtime hashes; these probes are not rerun by verify.py"}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Report path; defaults to benchmarks/results/v<runtime VERSION>/verification.json. "
                        "A failed rerun uses a .failed sibling if this file exists")
    parser.add_argument("--archive", help="Archive to compare; defaults to the canonical runtime VERSION")
    parser.add_argument("--skill-validator", help="Optional bundled quick_validate.py path")
    parser.add_argument("--validator-python", help="Python executable with the optional validator's dependencies")
    parser.add_argument("--timeout", type=int, default=300, help="Maximum seconds for each child process")
    parser.add_argument("--unittest-report", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.unittest_report:
        return unittest_child(args.unittest_report)
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    if args.validator_python and not args.skill_validator:
        parser.error("--validator-python requires --skill-validator")
    try:
        output = report_path(args.output if args.output is not None else current_evidence_directory() / "verification.json")
        report = verify(args.archive, args.skill_validator, args.validator_python, args.timeout)
        output = write_report(report, output)
        print(json.dumps({"ok": report["ok"], "report": str(output),
                          "failed_checks": [key for key, value in report.items()
                                            if isinstance(value, dict) and value.get("status") == "failed"]},
                         ensure_ascii=False))
        return 0 if report["ok"] else 1
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
