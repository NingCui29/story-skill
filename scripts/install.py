#!/usr/bin/env python3
"""Install the complete Story Codex suite into one project's .agents/skills directory."""
from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "skills"
SKILL_NAMES = ("story-codex", "story-codex-plan", "story-codex-write", "story-codex-analyze",
               "story-codex-review", "story-codex-research", "story-codex-cover")
LEGACY_SUITE_FILES = tuple(sorted(
    [f"{name}/{relative}" for name in SKILL_NAMES for relative in ("LICENSE", "SKILL.md", "agents/openai.yaml")]
    + ["story-codex/scripts/" + name for name in (
        "story.py", "story_history.py", "story_search.py", "story_storage.py", "story_world.py")]
    + ["story-codex/references/project-state.md", "story-codex-write/references/chapter.md",
       "story-codex-write/references/long-form.md", "story-codex-write/references/drama.md",
       "story-codex-review/references/history.md"]))
SUITE_FILES = tuple(sorted(LEGACY_SUITE_FILES + (
    "story-codex-analyze/references/deep-reading.md",
    "story-codex-analyze/references/examples.md")))
TAGGED_SUITE_FILES = tuple(sorted(SUITE_FILES + (
    "story-codex-plan/references/fanqie-tags.md",)))
MARKER = ".story-codex-install.json"


def suite_files(version):
    if re.fullmatch(r"0\.4\.(?:0|[1-9][0-9]*)", version) or version == "0.5.0":
        return LEGACY_SUITE_FILES
    patch = re.fullmatch(r"0\.5\.([1-9][0-9]*)", version)
    if patch:
        return TAGGED_SUITE_FILES if int(patch.group(1)) >= 7 else SUITE_FILES
    raise ValueError(f"Source runtime version has no reviewed suite layout: {version}")


def runtime_version(raw, allow_missing=False):
    versions = []
    for node in ast.parse(raw.decode("utf-8-sig")).body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        if any(isinstance(target, ast.Name) and target.id == "VERSION" for target in targets):
            versions.append(ast.literal_eval(node.value))
    if not versions and allow_missing:
        return None
    if (len(versions) != 1 or not isinstance(versions[0], str) or
            not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", versions[0])):
        raise ValueError("Source runtime must define one literal semantic VERSION")
    return versions[0]


def linked(path):
    try:
        attributes = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(attributes.st_mode) or bool(
        getattr(attributes, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def checked(root, relative):
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("Path is outside the requested project")
    current = root
    for part in rel.parts:
        current /= part
        if linked(current):
            raise ValueError(f"Refusing linked installation path: {current}")
    current.resolve().relative_to(root)
    return current


def inventory(directory):
    if linked(directory):
        raise ValueError(f"Refusing linked package content: {directory}")
    files = {}
    for path in sorted(directory.rglob("*")):
        if linked(path):
            raise ValueError(f"Refusing linked package content: {path}")
        rel = path.relative_to(directory)
        if "__pycache__" in rel.parts or path.suffix == ".pyc" or rel.as_posix() == MARKER:
            continue
        if path.is_file():
            files[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


@contextmanager
def installation_lock(project):
    """The OS releases this lock on process exit; the empty lock file may remain."""
    lock = checked(project, ".agents/skills/.story-codex-install.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock, flags, 0o600)
    try:
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError(f"Another installation may be running for this project; retry after it exits: {lock}") from error
        yield
    finally:
        os.close(fd)


def managed_snapshot(directory):
    if linked(directory):
        raise ValueError(f"Refusing linked installation path: {directory}")
    if not directory.exists():
        return None
    marker = checked(directory, MARKER)
    if not marker.is_file():
        raise ValueError("Existing skill has no managed manifest; it will not be replaced")
    raw = marker.read_bytes()
    managed = json.loads(raw.decode("utf-8"))
    if not isinstance(managed, dict) or managed.get("schema") != 1 or not isinstance(managed.get("files"), dict):
        raise ValueError("Invalid installation manifest")
    for relative in managed["files"]:
        checked(directory, relative)
    if inventory(directory) != managed["files"]:
        raise ValueError("Installed skill has local edits or extra files; preserve and reconcile them first")
    return {"files": managed["files"], "manifest_sha256": hashlib.sha256(raw).hexdigest()}


def move_directory(source, destination):
    # Windows rename refuses an existing destination, including an empty directory.
    # On POSIX the preflight guard is not a strong CAS against external writers;
    # the OS lock coordinates other instances of this installer on both platforms.
    if os.path.lexists(destination):
        raise FileExistsError(f"Refusing to replace an existing installation path: {destination}")
    os.rename(source, destination)


def validate_stage(source, stage, files, manifest):
    if inventory(source) != files:
        raise ValueError("Source package changed during installation; retry with a stable source")
    if inventory(stage) != files or (stage / MARKER).read_bytes() != manifest:
        raise ValueError("Staged package differs from its manifest; installation was not published")


def install_locked(project, target, source, files, update):
    original = managed_snapshot(target)
    if original is not None:
        if files == original["files"]:
            return {"status": "unchanged", "path": str(target)}
        if not update:
            raise ValueError("A managed version exists; use --update for a reviewed replacement")
    stage = Path(tempfile.mkdtemp(prefix=".story-codex-stage-", dir=target.parent))
    backup = None
    moved_original = False
    try:
        for relative in files:
            destination = checked(stage.resolve(), relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, destination)
            if hashlib.sha256(destination.read_bytes()).hexdigest() != files[relative]:
                raise ValueError(f"Source file changed while copying; installation was not published: {relative}")
        manifest = (json.dumps({"schema": 1, "files": files}, ensure_ascii=False,
                               sort_keys=True, indent=2) + "\n").encode("utf-8")
        (stage / MARKER).write_bytes(manifest)
        validate_stage(source, stage, files, manifest)
        if managed_snapshot(checked(project, ".agents/skills/story-codex")) != original:
            raise ValueError("Installed skill changed during staging; it will not be replaced")
        if original is not None:
            backup = checked(project, f".agents/.story-codex-backups/{uuid.uuid4().hex}")
            backup.parent.mkdir(parents=True, exist_ok=True)
            move_directory(target, backup)
            moved_original = True
        try:
            if moved_original and managed_snapshot(backup) != original:
                raise ValueError("Installed skill changed while being moved; restoring the moved version")
            validate_stage(source, stage, files, manifest)
            move_directory(stage, checked(project, ".agents/skills/story-codex"))
            expected = {"files": files, "manifest_sha256": hashlib.sha256(manifest).hexdigest()}
            if managed_snapshot(checked(project, ".agents/skills/story-codex")) != expected:
                raise ValueError("Published installation differs from the verified package and manifest")
        except BaseException as error:
            if moved_original:
                try:
                    move_directory(backup, checked(project, ".agents/skills/story-codex"))
                except (OSError, ValueError) as recovery_error:
                    raise ValueError(
                        f"Installation failed ({error}); the moved skill is preserved at {backup}. "
                        f"Recovery did not replace the current target: {recovery_error}") from error
            raise
    finally:
        if stage.exists():
            # This exact temporary child was allocated above, never a caller-supplied deletion target.
            if stage.resolve().parent != target.parent.resolve() or not stage.name.startswith(".story-codex-stage-"):
                raise ValueError("Refusing to clean a stage outside the installation directory")
            shutil.rmtree(stage)
    return {"status": "updated" if backup else "installed", "path": str(target),
            "backup": str(backup) if backup else None, "files": len(files)}


def install_legacy(project, update=False, source=SOURCE):
    project, source = Path(project).expanduser().resolve(), Path(source).resolve()
    target = checked(project, ".agents/skills/story-codex")
    if target == source:
        return {"status": "already_in_place", "path": str(target)}
    files = inventory(source)
    if "SKILL.md" not in files or "scripts/story.py" not in files:
        raise ValueError("Source package is incomplete")
    with installation_lock(project):
        return install_locked(project, checked(project, ".agents/skills/story-codex"), source, files, update)


def suite_inventory(source):
    """Only the seven named skills belong to this installer; never copy sibling data."""
    result = {}
    if linked(source):
        raise ValueError(f"Refusing linked source directory: {source}")
    runtime = checked(source, "story-codex/scripts/story.py")
    if not runtime.is_file():
        raise ValueError("Source suite is incomplete: missing scripts/story.py")
    reviewed = suite_files(runtime_version(runtime.read_bytes()))
    for name in SKILL_NAMES:
        directory = checked(source, name)
        files = inventory(directory)
        required = {path.split("/", 1)[1] for path in reviewed if path.startswith(name + "/")}
        if not required <= files.keys():
            raise ValueError(f"Source suite is incomplete: {name} is missing {sorted(required - files.keys())}")
        if files.keys() != required:
            raise ValueError(f"Source skill contains unreviewed files: {name}: {sorted(files.keys() - required)}")
        result[name] = files
    return result


def clean_stage(stage, parent):
    if not os.path.lexists(stage):
        return
    if (linked(stage) or stage.resolve().parent != parent.resolve()
            or not stage.name.startswith(".story-codex-stage-")):
        raise ValueError(f"Refusing to clean an altered installation stage: {stage}")
    shutil.rmtree(stage)


def install_suite_locked(project, source, files, update):
    parent = checked(project, ".agents/skills")
    targets = {name: checked(project, ".agents/skills/" + name) for name in SKILL_NAMES}
    originals = {name: managed_snapshot(path) for name, path in targets.items()}
    changed = [name for name in SKILL_NAMES if originals[name] is None or files[name] != originals[name]["files"]]
    if not changed:
        return {"status": "unchanged", "path": str(parent), "skills": list(SKILL_NAMES)}
    if any(originals.values()) and not update:
        raise ValueError("A managed version exists; use --update for a reviewed suite replacement")
    stage = Path(tempfile.mkdtemp(prefix=".story-codex-stage-", dir=parent))
    backup = None
    moved, published, expected = [], [], {}
    manifests = {}
    preserve_stage = False

    def validate_all():
        if suite_inventory(source) != files:
            raise ValueError("Source suite changed during installation; retry with a stable source")
        for skill in changed:
            if skill not in published:
                validate_stage(source / skill, stage / skill, files[skill], manifests[skill])

    try:
        for name in changed:
            child = stage / name
            child.mkdir()
            for relative, digest in files[name].items():
                destination = checked(child.resolve(), relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / name / relative, destination)
                if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                    raise ValueError(f"Source file changed while copying: {name}/{relative}")
            manifest = (json.dumps({"schema": 1, "files": files[name]}, ensure_ascii=False,
                                   sort_keys=True, indent=2) + "\n").encode("utf-8")
            (child / MARKER).write_bytes(manifest)
            manifests[name] = manifest
            expected[name] = {"files": files[name], "manifest_sha256": hashlib.sha256(manifest).hexdigest()}
        validate_all()
        for name in SKILL_NAMES:
            if managed_snapshot(targets[name]) != originals[name]:
                raise ValueError(f"Installed skill changed during staging: {name}")
        if any(originals[name] for name in changed):
            backup = checked(project, f".agents/.story-codex-backups/{uuid.uuid4().hex}")
            backup.mkdir(parents=True)
        try:
            for name in changed:
                if originals[name] is not None:
                    move_directory(checked(project, ".agents/skills/" + name), backup / name)
                    moved.append(name)
                    if managed_snapshot(backup / name) != originals[name]:
                        raise ValueError(f"Installed skill changed while being moved: {name}")
            validate_all()
            for name in changed:
                # Recheck the remaining stage and already published directories each step.
                validate_all()
                for prior in published:
                    if managed_snapshot(targets[prior]) != expected[prior]:
                        raise ValueError(f"Published skill changed during suite installation: {prior}")
                move_directory(stage / name, checked(project, ".agents/skills/" + name))
                published.append(name)
                if managed_snapshot(targets[name]) != expected[name]:
                    raise ValueError(f"Published installation differs from verified skill: {name}")
            for name in SKILL_NAMES:
                if managed_snapshot(targets[name]) != expected.get(name, originals[name]):
                    raise ValueError(f"Installed suite changed before completion: {name}")
        except BaseException as error:
            recovery_errors = []
            # Only withdraw our still-unchanged output. External edits are never deleted.
            for name in reversed(published):
                try:
                    if managed_snapshot(targets[name]) != expected[name]:
                        raise ValueError("Published skill now has external edits")
                    move_directory(checked(project, ".agents/skills/" + name), stage / name)
                    try:
                        if managed_snapshot(stage / name) != expected[name]:
                            raise ValueError("Withdrawn skill changed while being moved")
                    except (OSError, ValueError):
                        preserve_stage = True
                        raise
                except (OSError, ValueError) as recovery_error:
                    recovery_errors.append(f"{name}: {recovery_error}")
            for name in reversed(moved):
                try:
                    move_directory(backup / name, checked(project, ".agents/skills/" + name))
                except (OSError, ValueError) as recovery_error:
                    recovery_errors.append(f"{name}: {recovery_error}")
            if backup is not None and not any(backup.iterdir()):
                backup.rmdir()
            if recovery_errors:
                raise ValueError(f"Installation failed ({error}); previous skills are preserved at {backup}; "
                                 f"withdrawn modified files, if any, are preserved at {stage}. "
                                 "Recovery did not replace modified targets: " + "; ".join(recovery_errors)) from error
            raise
    finally:
        if not preserve_stage:
            clean_stage(stage, parent)
    return {"status": "updated" if any(originals.values()) else "installed", "path": str(parent),
            "skills": list(SKILL_NAMES), "changed_skills": changed,
            "backup": str(backup) if backup is not None else None,
            "files": sum(len(value) for value in files.values())}


def install(project, update=False, source=SOURCE):
    project = Path(project).expanduser().resolve()
    source = Path(source).expanduser().absolute()
    if linked(source):
        raise ValueError(f"Refusing linked source directory: {source}")
    source = source.resolve()
    if (source / "SKILL.md").is_file():
        # Preserve the old explicit source API for managed v0.3 installations.
        # A v0.4 core directory is never allowed to omit its sibling dependencies.
        runtime = source / "scripts/story.py"
        if runtime.is_file():
            version = runtime_version(runtime.read_bytes(), allow_missing=True)
            if version and tuple(map(int, version.split("."))) >= (0, 4, 0):
                source = source.parent
            else:
                return install_legacy(project, update, source)
        else:
            raise ValueError("Source package is incomplete")
    files = suite_inventory(source)
    target = checked(project, ".agents/skills")
    if target == source:
        return {"status": "already_in_place", "path": str(target), "skills": list(SKILL_NAMES)}
    with installation_lock(project):
        return install_suite_locked(project, source, files, update)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project", required=True, help="Target Codex project; no global configuration is changed")
    p.add_argument("--update", action="store_true", help="Replace an unchanged managed installation; retain backup")
    args = p.parse_args()
    try:
        print(json.dumps(install(args.project, args.update), ensure_ascii=False))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
