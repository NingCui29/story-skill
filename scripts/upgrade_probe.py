#!/usr/bin/env python3
"""Verify managed suite replacement and rollback in an isolated project."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install.py"
PYTHON = [sys.executable, "-B", "-X", "utf8"]


def load_script(name):
    spec = importlib.util.spec_from_file_location(f"upgrade_probe_{name}", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_tree(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}


def probe(timeout=60):
    installer = load_script("install")
    suite = installer.suite_inventory(ROOT / "skills")
    version = installer.runtime_version((ROOT / "skills/story-skill/scripts/story.py").read_bytes())
    report = {"schema": 2, "date": datetime.now(timezone.utc).isoformat(), "ok": False,
              "method": "Current suite installed, synthetically changed, and replaced through real CLI commands",
              "scope": "Managed skill files, backup bytes, update idempotency, unrelated files, and runtime startup",
              "environment": {"platform": platform.platform(), "python": platform.python_version(),
                              "executable": sys.executable},
              "version": version, "commands": [], "checks": {}}

    def check(name, passed, **details):
        report["checks"][name] = {"status": "passed" if passed else "failed", **details}
        if not passed:
            raise ValueError(f"Managed replacement verification failed: {name}")

    def run(name, command):
        command = PYTHON + list(map(str, command))
        started = time.monotonic()
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout)
        report["commands"].append({"name": name, "command": command,
                                   "exit_code": result.returncode,
                                   "duration_seconds": round(time.monotonic() - started, 3)})
        if result.returncode:
            raise ValueError(f"{name} exited with {result.returncode}: {result.stderr or result.stdout}")
        return result.stdout

    try:
        with tempfile.TemporaryDirectory(prefix="story-upgrade-probe-") as directory:
            temp = Path(directory).resolve()
            project = temp / "验证项目"
            skills = project / ".agents/skills"
            book = project / "books/原有小说/正文/第1章.md"
            book.parent.mkdir(parents=True)
            book.write_text("真实升级不能触碰的正文", encoding="utf-8")
            other = skills / "other-skill/SKILL.md"
            other.parent.mkdir(parents=True)
            other.write_text("其他技能保持原样", encoding="utf-8")
            initial = json.loads(run("initial_install", [INSTALLER, "--project", project]))
            check("initial_install", initial.get("status") == "installed" and
                  initial.get("skills") == list(suite))
            before = {name: read_tree(skills / name) for name in suite}
            check("initial_payload", all(installer.inventory(skills / name) == files
                                         for name, files in suite.items()))

            candidate_repo = temp / "candidate"
            shutil.copytree(ROOT / "skills", candidate_repo / "skills",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            candidate = candidate_repo / "skills/story-skill-write/references/drama.md"
            candidate.write_bytes(candidate.read_bytes() + b"\n<!-- isolated replacement probe -->\n")
            candidate_installer = candidate_repo / "scripts/install.py"
            candidate_installer.parent.mkdir()
            candidate_installer.write_bytes(INSTALLER.read_bytes())
            changed_suite = installer.suite_inventory(candidate_repo / "skills")
            changed_names = [name for name in suite if suite[name] != changed_suite[name]]
            check("single_skill_changed", changed_names == ["story-skill-write"])
            updated = json.loads(run("managed_update", [candidate_installer, "--project", project, "--update"]))
            check("update_status", updated.get("status") == "updated" and
                  updated.get("changed_skills") == changed_names and bool(updated.get("backup")))
            backup = Path(updated["backup"])
            backup.relative_to((project / ".agents/.story-skill-backups").resolve())
            check("backup_exact", read_tree(backup / changed_names[0]) == before[changed_names[0]] and
                  sorted(path.name for path in backup.iterdir()) == changed_names)
            check("updated_payload", all(installer.inventory(skills / name) == files and
                                         installer.managed_snapshot(skills / name) is not None
                                         for name, files in changed_suite.items()))
            check("unchanged_siblings", all(read_tree(skills / name) == before[name]
                                            for name in suite if name not in changed_names))
            version_output = run("installed_version", [skills / "story-skill/scripts/story.py", "--version"])
            check("runtime_version", version_output.strip() == version)
            help_output = run("prepare_help", [skills / "story-skill/scripts/story.py", "prepare", "--help"])
            check("prepare_cli", all(option in help_output for option in
                                     ("--book", "--chapter", "--draft", "--reconcile")))
            backup_before = read_tree(backup)
            repeated = json.loads(run("repeat_update", [candidate_installer, "--project", project, "--update"]))
            check("repeat_idempotent", repeated.get("status") == "unchanged" and
                  read_tree(backup) == backup_before)
            check("original_source_unchanged", installer.suite_inventory(ROOT / "skills") == suite and
                  candidate_installer.read_bytes() == INSTALLER.read_bytes())
            check("book_and_other_skill_unchanged", book.read_text(encoding="utf-8") ==
                  "真实升级不能触碰的正文" and other.read_text(encoding="utf-8") == "其他技能保持原样")
        report["temporary_project_removed"] = True
        report["ok"] = True
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Report path; defaults to the current version's verification directory")
    parser.add_argument("--timeout", type=int, default=60, help="Maximum seconds for each CLI command")
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    verification = load_script("verify")
    output = verification.report_path(args.output if args.output is not None else
                                      verification.current_evidence_directory() / "upgrade.json")
    result = probe(args.timeout)
    output = verification.write_report(result, output)
    print(json.dumps({"ok": result["ok"], "report": str(output), "version": result.get("version"),
                      "error": result.get("error")}, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
