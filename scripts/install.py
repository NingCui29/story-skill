#!/usr/bin/env python3
"""Install the complete Story Skill suite into one project's .agents/skills directory."""
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
SKILL_NAMES_V060 = (
    "story-skill", "story-skill-plan", "story-skill-write", "story-skill-analyze",
    "story-skill-review", "story-skill-research", "story-skill-cover", "story-skill-publish",
)
SKILL_NAMES_V061 = (
    "story-skill", "story-skill-plan", "story-skill-write", "story-skill-analyze",
    "story-skill-review", "story-skill-research", "story-skill-cover", "story-skill-publish",
)
SUITE_FILES_V060 = (
    "story-skill-analyze/LICENSE",
    "story-skill-analyze/SKILL.md",
    "story-skill-analyze/agents/openai.yaml",
    "story-skill-analyze/references/deep-reading.md",
    "story-skill-analyze/references/examples.md",
    "story-skill-cover/LICENSE",
    "story-skill-cover/SKILL.md",
    "story-skill-cover/agents/openai.yaml",
    "story-skill-plan/LICENSE",
    "story-skill-plan/SKILL.md",
    "story-skill-plan/agents/openai.yaml",
    "story-skill-plan/references/fanqie-tags.md",
    "story-skill-publish/LICENSE",
    "story-skill-publish/SKILL.md",
    "story-skill-publish/agents/openai.yaml",
    "story-skill-research/LICENSE",
    "story-skill-research/SKILL.md",
    "story-skill-research/agents/openai.yaml",
    "story-skill-review/LICENSE",
    "story-skill-review/SKILL.md",
    "story-skill-review/agents/openai.yaml",
    "story-skill-review/references/history.md",
    "story-skill-write/LICENSE",
    "story-skill-write/SKILL.md",
    "story-skill-write/agents/openai.yaml",
    "story-skill-write/references/chapter.md",
    "story-skill-write/references/drama.md",
    "story-skill-write/references/long-form.md",
    "story-skill/LICENSE",
    "story-skill/SKILL.md",
    "story-skill/agents/openai.yaml",
    "story-skill/references/project-state.md",
    "story-skill/scripts/story.py",
    "story-skill/scripts/story_history.py",
    "story-skill/scripts/story_publish.py",
    "story-skill/scripts/story_search.py",
    "story-skill/scripts/story_storage.py",
    "story-skill/scripts/story_world.py",
)
SUITE_FILES_V061 = (
    "story-skill-analyze/LICENSE",
    "story-skill-analyze/SKILL.md",
    "story-skill-analyze/agents/openai.yaml",
    "story-skill-analyze/references/deep-reading.md",
    "story-skill-analyze/references/examples.md",
    "story-skill-cover/LICENSE",
    "story-skill-cover/SKILL.md",
    "story-skill-cover/agents/openai.yaml",
    "story-skill-plan/LICENSE",
    "story-skill-plan/SKILL.md",
    "story-skill-plan/agents/openai.yaml",
    "story-skill-plan/references/fanqie-tags.md",
    "story-skill-publish/LICENSE",
    "story-skill-publish/SKILL.md",
    "story-skill-publish/agents/openai.yaml",
    "story-skill-research/LICENSE",
    "story-skill-research/SKILL.md",
    "story-skill-research/agents/openai.yaml",
    "story-skill-review/LICENSE",
    "story-skill-review/SKILL.md",
    "story-skill-review/agents/openai.yaml",
    "story-skill-review/references/history.md",
    "story-skill-write/LICENSE",
    "story-skill-write/SKILL.md",
    "story-skill-write/agents/openai.yaml",
    "story-skill-write/references/chapter.md",
    "story-skill-write/references/drama.md",
    "story-skill-write/references/long-form.md",
    "story-skill/LICENSE",
    "story-skill/SKILL.md",
    "story-skill/agents/openai.yaml",
    "story-skill/references/project-state.md",
    "story-skill/scripts/story.py",
    "story-skill/scripts/story_history.py",
    "story-skill/scripts/story_publish.py",
    "story-skill/scripts/story_search.py",
    "story-skill/scripts/story_storage.py",
    "story-skill/scripts/story_workbench.py",
    "story-skill/scripts/story_world.py",
)
# Compatibility aliases mean "current source candidate", not every future 0.6.x release.
SKILL_NAMES = SKILL_NAMES_V061
SUITE_FILES = SUITE_FILES_V061
MARKER = ".story-skill-install.json"


def suite_files(version):
    if version == "0.6.0":
        return SUITE_FILES_V060
    if version == "0.6.1":
        return SUITE_FILES_V061
    raise ValueError(f"Source runtime version has no reviewed suite layout: {version}")


def skill_names(version):
    """Return the immutable skill-root list reviewed for one exact version."""
    if version == "0.6.0":
        return SKILL_NAMES_V060
    if version == "0.6.1":
        return SKILL_NAMES_V061
    raise ValueError(f"Source runtime version has no reviewed suite layout: {version}")


def runtime_version(raw):
    versions = []
    for node in ast.parse(raw.decode("utf-8-sig")).body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        if any(isinstance(target, ast.Name) and target.id == "VERSION" for target in targets):
            versions.append(ast.literal_eval(node.value))
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
    lock = checked(project, ".agents/skills/.story-skill-install.lock")
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


def validate_omitted_targets(project, names):
    parent = checked(project, ".agents/skills")
    if parent.is_dir():
        groups = {}
        suffixes = ("plan", "write", "analyze", "review", "research", "cover", "publish")
        for child in parent.iterdir():
            if child.name in SKILL_NAMES or not child.name.startswith("story-"):
                continue
            if linked(child):
                raise ValueError("An unverified linked writing skill remains in the skills directory")
            if not child.is_dir():
                continue
            if linked(child / "scripts"):
                raise ValueError("An unverified linked writing runtime remains in the skills directory")
            runtime = child / "scripts/story.py"
            if linked(runtime):
                raise ValueError("An unverified linked writing runtime remains in the skills directory")
            if runtime.is_file():
                try:
                    version = runtime_version(runtime.read_bytes())
                except (SyntaxError, ValueError):
                    version = None
                if version is not None and tuple(map(int, version.split("."))) < (0, 6, 0):
                    raise ValueError("An earlier writing suite remains in the skills directory; "
                                     "move its complete installation outside skill discovery before installing this suite")
            entry = child / "SKILL.md"
            if linked(entry):
                raise ValueError("An unverified linked writing skill remains in the skills directory")
            for suffix in suffixes:
                if child.name.endswith("-" + suffix) and entry.is_file():
                    prefix = child.name[:-(len(suffix) + 1)]
                    groups.setdefault(prefix, set()).add(suffix)
                    break
        if groups:
            raise ValueError("An earlier writing suite remains in the skills directory; "
                             "move its complete installation outside skill discovery before installing this suite")
    for name in SKILL_NAMES:
        if name in names:
            continue
        target = checked(project, ".agents/skills/" + name)
        if os.path.lexists(target):
            raise ValueError(f"Source version would omit existing suite skill: {name}; "
                             "refusing a mixed-version installation; keep the complete installed suite")


def suite_inventory(source):
    """Copy only the version's reviewed skill roots, never sibling project data."""
    result = {}
    if linked(source):
        raise ValueError(f"Refusing linked source directory: {source}")
    runtime = checked(source, "story-skill/scripts/story.py")
    if not runtime.is_file():
        raise ValueError("Source suite is incomplete: missing scripts/story.py")
    version = runtime_version(runtime.read_bytes())
    reviewed = suite_files(version)
    names = skill_names(version)
    for name in set(SKILL_NAMES) - set(names):
        if os.path.lexists(source / name):
            raise ValueError(f"Source suite contains unreviewed files for {version}: {name}")
    for name in names:
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
            or not stage.name.startswith(".story-skill-stage-")):
        raise ValueError(f"Refusing to clean an altered installation stage: {stage}")
    shutil.rmtree(stage)


def install_suite_locked(project, source, files, update):
    parent = checked(project, ".agents/skills")
    names = tuple(files)
    validate_omitted_targets(project, names)
    targets = {name: checked(project, ".agents/skills/" + name) for name in names}
    originals = {name: managed_snapshot(path) for name, path in targets.items()}
    changed = [name for name in names if originals[name] is None or files[name] != originals[name]["files"]]
    if not changed:
        return {"status": "unchanged", "path": str(parent), "skills": list(names)}
    if any(originals.values()) and not update:
        raise ValueError("A managed version exists; use --update for a reviewed suite replacement")
    stage = Path(tempfile.mkdtemp(prefix=".story-skill-stage-", dir=parent))
    backup = None
    moved, published, expected = [], [], {}
    manifests = {}
    preserve_stage = False

    def validate_all():
        validate_omitted_targets(project, names)
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
        for name in names:
            if managed_snapshot(targets[name]) != originals[name]:
                raise ValueError(f"Installed skill changed during staging: {name}")
        if any(originals[name] for name in changed):
            backup = checked(project, f".agents/.story-skill-backups/{uuid.uuid4().hex}")
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
            for name in names:
                if managed_snapshot(targets[name]) != expected.get(name, originals[name]):
                    raise ValueError(f"Installed suite changed before completion: {name}")
            validate_omitted_targets(project, names)
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
            "skills": list(names), "changed_skills": changed,
            "backup": str(backup) if backup is not None else None,
            "files": sum(len(value) for value in files.values())}


def install(project, update=False, source=SOURCE):
    project = Path(project).expanduser().resolve()
    source = Path(source).expanduser().absolute()
    if linked(source):
        raise ValueError(f"Refusing linked source directory: {source}")
    source = source.resolve()
    if (source / "SKILL.md").is_file():
        if source.name != SKILL_NAMES[0] or not (source / "scripts/story.py").is_file():
            raise ValueError("A full supported suite is required")
        source = source.parent
    files = suite_inventory(source)
    target = checked(project, ".agents/skills")
    if target == source:
        validate_omitted_targets(project, files)
        return {"status": "already_in_place", "path": str(target), "skills": list(files)}
    with installation_lock(project):
        return install_suite_locked(project, source, files, update)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project", required=True, help="Target writing project; no global configuration is changed")
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
