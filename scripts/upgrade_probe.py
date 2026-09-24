#!/usr/bin/env python3
"""Verify a real prior-release skill upgrade in a temporary project using the install CLI."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/story-codex"
INSTALLER = ROOT / "scripts/install.py"
PYTHON = [sys.executable, "-B", "-X", "utf8"]


def load_script(name):
    spec = importlib.util.spec_from_file_location(f"upgrade_probe_{name}", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def read_tree(directory, installer):
    """Include the generated manifest when comparing the installed version and its backup."""
    files = {}
    for path in sorted(directory.rglob("*")):
        if installer.linked(path):
            raise ValueError(f"Linked file in temporary installation: {path}")
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = path.read_bytes()
    return files


def hashes(files):
    return {name: sha256(raw) for name, raw in files.items()}


def unpack_old_archive(archive_path, skill_parent):
    """Validate legacy or version-bound suite roots before writing any members."""
    installer = load_script("install")
    allowed = set(installer.SKILL_NAMES)
    entries, canonical, extracted, roots = [], {}, {}, set()
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise ValueError("Old archive failed its CRC check")
        seen = set()
        for entry in archive.infolist():
            # ZipInfo may normalize Windows separators or truncate NULs while
            # reading. Validate the original spelling before using its path.
            if entry.orig_filename != entry.filename or "\\" in entry.orig_filename:
                raise ValueError(f"Non-portable archive path: {entry.filename}")
            parts = entry.filename.split("/")
            if entry.is_dir():
                parts = parts[:-1]
            if not parts or parts[0] not in allowed or any(
                    part in ("", ".", "..") or any(char in '<>:"|?*' for char in part) or part.rstrip(" .") != part or
                    any(ord(char) < 32 for char in part) or
                    part.split(".", 1)[0].upper() in {"CON", "PRN", "AUX", "NUL", *(
                        f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10))}
                    for part in parts):
                raise ValueError(f"Archive path is outside its skill roots or non-portable: {entry.filename}")
            for i in range(1, len(parts) + 1):
                prefix = "/".join(parts[:i])
                key = unicodedata.normalize("NFC", prefix).casefold()
                if key in canonical and canonical[key] != prefix:
                    raise ValueError(f"Aliased archive path: {entry.filename}")
                canonical[key] = prefix
            normalized = unicodedata.normalize("NFC", "/".join(parts)).casefold()
            if normalized in seen:
                raise ValueError(f"Duplicate archive path: {entry.filename}")
            seen.add(normalized)
            kind = stat.S_IFMT(entry.external_attr >> 16)
            if (kind not in (0, stat.S_IFREG, stat.S_IFDIR) or
                    (kind == stat.S_IFDIR and not entry.is_dir()) or (kind == stat.S_IFREG and entry.is_dir())):
                raise ValueError(f"Linked or special archive member: {entry.filename}")
            if not entry.is_dir() and len(parts) < 2:
                raise ValueError("An archive skill root must be a directory")
            roots.add(parts[0])
            entries.append((entry, parts))
        names = {"/".join(parts) for entry, parts in entries if not entry.is_dir()}
        required = {root + "/SKILL.md" for root in roots} | {"story-codex/scripts/story.py"}
        if not required <= names:
            raise ValueError("Old archive is missing skill entries or the runtime")
        version = installer.runtime_version(archive.read("story-codex/scripts/story.py"))
        expected_roots = ({"story-codex"} if tuple(map(int, version.split("."))) < (0, 4, 0)
                          else set(installer.skill_names(version)))
        if roots != expected_roots:
            raise ValueError(f"Archive must contain all skill roots reviewed for {version}")
        if any("/".join(parts[:i]) in names for _, parts in entries for i in range(1, len(parts))):
            raise ValueError("Archive file is also used as a parent directory")
        skill_parent.mkdir(parents=True, exist_ok=True)
        boundary = skill_parent.resolve()
        for entry, parts in entries:
            destination = skill_parent.joinpath(*parts)
            destination.resolve().relative_to(boundary)
            if entry.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            raw = archive.read(entry)
            with destination.open("xb") as stream:
                stream.write(raw)
            extracted["/".join(parts)] = sha256(raw)
    if roots == {"story-codex"}:
        return skill_parent / "story-codex", {name.split("/", 1)[1]: value for name, value in extracted.items()}
    return skill_parent, extracted


def probe(old_archive, timeout):
    installer, package, verification = (load_script(name) for name in ("install", "package", "verify"))
    report = {"schema": 1, "date": datetime.now(timezone.utc).isoformat(), "ok": False,
              "method": "Prior release ZIP installed and upgraded through real install.py CLI subprocesses",
              "scope": "Managed skill files, backup bytes, update idempotency, installed version and prepare CLI help",
              "environment": {"platform": platform.platform(), "python": platform.python_version(),
                              "executable": sys.executable}, "commands": [], "checks": {}}

    def check(name, passed, **details):
        report["checks"][name] = {"status": "passed" if passed else "failed", **details}
        if not passed:
            raise ValueError(f"Upgrade verification failed: {name}")

    def run(name, arguments):
        command = PYTHON + list(map(str, arguments))
        started = time.monotonic()
        result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=timeout)
        recorded = {"name": name, "command": command, "exit_code": result.returncode,
                    "stdout": result.stdout.decode("utf-8", errors="replace"),
                    "stderr": result.stderr.decode("utf-8", errors="replace"),
                    "duration_seconds": round(time.monotonic() - started, 3)}
        report["commands"].append(recorded)
        if result.returncode:
            raise ValueError(f"{name} exited with {result.returncode}: {recorded['stderr'] or recorded['stdout']}")
        return recorded["stdout"]

    try:
        old_archive = Path(old_archive).expanduser().resolve()
        archive_sha = sha256(old_archive.read_bytes())
        installer_bytes = INSTALLER.read_bytes()
        current_files = installer.inventory(SKILL)
        current_suite = installer.suite_inventory(ROOT / "skills")
        current_version = package.current_version(SKILL / "scripts/story.py")
        report["initial_archive"] = {"path": str(old_archive), "sha256": archive_sha,
                                     "bytes": old_archive.stat().st_size}
        report["current_source"] = {"version": current_version, "files": current_files, "suite": current_suite}
        report["installer"] = {"path": str(INSTALLER), "sha256": sha256(installer_bytes)}
        current_archive = ROOT / f"dist/story-codex-{current_version}.zip"
        if current_archive.is_file():
            archive_check = verification.check_archive(current_archive)
            report["current_archive"] = archive_check
            check("current_archive_matches_canonical", archive_check["status"] == "passed")
        else:
            report["current_archive"] = {"status": "not_available", "path": str(current_archive),
                                         "scope": "This run verifies canonical source; no current release ZIP was provided"}

        with tempfile.TemporaryDirectory(prefix="story-upgrade-probe-") as directory:
            temporary = Path(directory).resolve()
            old_repository = temporary / "旧版安装源"
            old_source, archive_files = unpack_old_archive(old_archive, old_repository / "unpacked")
            # The unchanged installer derives SOURCE from this temporary repository.
            # Preserve the version's legacy flat source or complete sibling roots.
            old_source.rename(old_repository / "skills")
            old_source = old_repository / "skills"
            legacy = (old_source / "SKILL.md").is_file()
            old_core = old_source if legacy else old_source / "story-codex"
            old_version = package.current_version(old_core / "scripts/story.py")
            package.validate_archive_name(old_archive, old_version)
            report["initial_archive"].update({"version": old_version, "files": archive_files})
            check("version_advances", tuple(map(int, old_version.split("."))) < tuple(map(int, current_version.split("."))),
                  old_version=old_version, new_version=current_version)
            old_suite = {"story-codex": installer.inventory(old_core)} if legacy else installer.suite_inventory(old_source)
            old_files = old_suite["story-codex"]
            extracted_files = old_files if legacy else {
                name + "/" + relative: value for name, files in old_suite.items() for relative, value in files.items()}
            check("archive_extraction_exact", extracted_files == archive_files, skill_count=len(old_suite))

            # install.py has --project and --update, but no --source option. Its
            # unchanged bytes derive SOURCE from this temporary repository layout.
            old_installer = old_repository / "scripts/install.py"
            old_installer.parent.mkdir(parents=True)
            old_installer.write_bytes(installer_bytes)
            copied_hash = sha256(old_installer.read_bytes())
            check("installer_copy_exact", copied_hash == report["installer"]["sha256"], copied_sha256=copied_hash)
            project = temporary / "升级验证项目"
            target = project / ".agents/skills/story-codex"
            skills_target = target.parent
            book = project / "books/原有小说/正文/第1章.md"
            book.parent.mkdir(parents=True)
            book.write_text("真实升级不能触碰的正文", encoding="utf-8")
            other = skills_target / "other-skill/SKILL.md"
            other.parent.mkdir(parents=True)
            other.write_text("其他技能保持原样", encoding="utf-8")
            initial = json.loads(run("install_old_release", [old_installer, "--project", project]))
            report["initial_install"] = initial
            check("old_release_managed_install", initial.get("status") == "installed" and
                  Path(initial["path"]).resolve() == (target if legacy else skills_target).resolve() and
                  (legacy or initial.get("skills") == list(old_suite)))
            originals = {name: read_tree(skills_target / name, installer) for name in old_suite}
            check("all_prior_skills_managed", all(installer.managed_snapshot(skills_target / name)["files"] == files
                                                  for name, files in old_suite.items()), skill_count=len(old_suite))
            original = originals["story-codex"]
            original_hashes = hashes(original)
            manifest = json.loads(original[installer.MARKER].decode("utf-8"))
            check("old_manifest_matches_archive", manifest.get("schema") == 1 and manifest.get("files") == old_files and
                  {name: value for name, value in original_hashes.items() if name != installer.MARKER} == old_files)
            old_runtime_version = run("old_installed_version", [target / "scripts/story.py", "--version"]).strip()
            check("old_installed_version", old_runtime_version == old_version, actual=old_runtime_version)

            updated = json.loads(run("update_to_canonical", [INSTALLER, "--project", project, "--update"]))
            report["update"] = updated
            check("update_status", updated.get("status") == "updated" and bool(updated.get("backup")) and
                  Path(updated["path"]).resolve() == skills_target.resolve() and
                  updated.get("skills") == list(current_suite))
            backup = Path(updated["backup"]).resolve()
            backup.relative_to((project / ".agents/.story-codex-backups").resolve())
            changed = {name for name in old_suite if old_suite[name] != current_suite[name]}
            backup_originals = {name: read_tree(backup / name, installer) for name in changed}
            check("previous_release_backup_exact", all(backup_originals[name] == originals[name] for name in changed) and
                  all(read_tree(skills_target / name, installer) == originals[name] for name in old_suite if name not in changed),
                  changed_skills=sorted(changed), prior_skill_count=len(originals),
                  original_files_sha256={name: hashes(files) for name, files in originals.items()},
                  backup_files_sha256={name: hashes(files) for name, files in backup_originals.items()})
            installed = read_tree(target, installer)
            installed_hashes = hashes(installed)
            manifest = json.loads(installed[installer.MARKER].decode("utf-8"))
            check("updated_manifest_matches_canonical", manifest.get("schema") == 1 and
                  manifest.get("files") == current_files and
                  {name: value for name, value in installed_hashes.items() if name != installer.MARKER} == current_files,
                  installed_files_sha256=installed_hashes)
            installed_suite = {name: read_tree(skills_target / name, installer) for name in current_suite}
            check("all_skills_match_canonical", all(
                installer.inventory(skills_target / name) == current_suite[name] and
                installer.managed_snapshot(skills_target / name) is not None for name in current_suite),
                skill_count=len(installed_suite), files=sum(len(files) for files in current_suite.values()))
            installed_version = run("updated_installed_version", [target / "scripts/story.py", "--version"]).strip()
            check("updated_installed_version", installed_version == current_version, actual=installed_version)
            help_text = run("updated_prepare_help", [target / "scripts/story.py", "prepare", "--help"])
            check("prepare_cli_available", "prepare" in help_text and all(
                option in help_text for option in ("--book", "--chapter", "--draft", "--reconcile")))

            before_backups = sorted(path.name for path in backup.parent.iterdir())
            repeated = json.loads(run("repeat_update", [INSTALLER, "--project", project, "--update"]))
            report["repeat_update"] = repeated
            check("repeat_update_idempotent", repeated.get("status") == "unchanged" and
                  Path(repeated["path"]).resolve() == skills_target.resolve() and read_tree(target, installer) == installed and
                  all(read_tree(skills_target / name, installer) == files for name, files in installed_suite.items()) and
                  all(read_tree(backup / name, installer) == files for name, files in backup_originals.items()) and
                  sorted(path.name for path in backup.parent.iterdir()) == before_backups,
                  backup_count=len(before_backups))
            check("input_files_unchanged", installer.inventory(SKILL) == current_files and
                  installer.suite_inventory(ROOT / "skills") == current_suite and
                  INSTALLER.read_bytes() == installer_bytes and sha256(old_archive.read_bytes()) == archive_sha)
            check("book_and_other_skill_unchanged", book.read_text(encoding="utf-8") == "真实升级不能触碰的正文" and
                  other.read_text(encoding="utf-8") == "其他技能保持原样")
            if report["current_archive"]["status"] == "passed":
                check("current_archive_unchanged", sha256(current_archive.read_bytes()) == report["current_archive"]["sha256"])
        report["temporary_project_removed"] = True
        report["ok"] = True
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
    return report


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-archive", default=str(ROOT / "dist/story-codex-0.4.0.zip"),
                        help="Trusted local prior release ZIP; defaults to version 0.4.0")
    parser.add_argument("--output", help="Defaults to benchmarks/results/v<runtime VERSION>/upgrade.json; "
                        "failed reruns preserve an existing report using a .failed sibling")
    parser.add_argument("--timeout", type=int, default=60, help="Maximum seconds for each CLI command")
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    try:
        verification = load_script("verify")
        output = verification.report_path(args.output if args.output is not None else
                                          verification.current_evidence_directory() / "upgrade.json")
        result = probe(args.from_archive, args.timeout)
        output = verification.write_report(result, output)
        print(json.dumps({"ok": result["ok"], "report": str(output),
                          "old_version": result.get("initial_archive", {}).get("version"),
                          "new_version": result.get("current_source", {}).get("version"),
                          "error": result.get("error")}, ensure_ascii=False))
        return 0 if result["ok"] else 1
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
