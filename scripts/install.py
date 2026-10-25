#!/usr/bin/env python3
"""Install the complete Story Skill suite into a supported host's skills directory."""
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
SUITE_FILES_V065 = tuple(sorted((*SUITE_FILES_V061,
    "story-skill-plan/references/qimao-tags.md",
)))
SUITE_FILES_V069 = tuple(sorted((*SUITE_FILES_V065,
    "story-skill/scripts/story_outline.py",
    "story-skill-review/references/fanqie-content-review.md",
    "story-skill-review/references/qimao-content-review.md",
    "story-skill-write/references/suspense-evidence.md",
)))
SKILL_NAMES_V069 = SKILL_NAMES_V061
SUITE_FILES_V0610 = tuple(sorted((*SUITE_FILES_V069,
    "story-skill-write/references/content-review.md",
)))
SKILL_NAMES_V0610 = SKILL_NAMES_V069
SUITE_FILES_V0611 = tuple(sorted((*SUITE_FILES_V0610,
    "story-skill-plan/references/blurb.md",
    "story-skill-research/references/reader-validation.md",
    "story-skill-write/references/punctuation.md",
    "story-skill/scripts/story_punctuation.py",
)))
SKILL_NAMES_V0611 = SKILL_NAMES_V0610
SUITE_FILES_V0612 = SUITE_FILES_V0611
SKILL_NAMES_V0612 = SKILL_NAMES_V0611
SUITE_FILES_V0613 = tuple(sorted((*SUITE_FILES_V0612,
    "story-skill-plan/references/outline.md",
)))
SKILL_NAMES_V0613 = SKILL_NAMES_V0612
SUITE_FILES_V0614 = tuple(sorted((*SUITE_FILES_V0613,
    "story-skill/references/workbench.md",
)))
SKILL_NAMES_V0614 = SKILL_NAMES_V0613
SUITE_FILES_V0615 = SUITE_FILES_V0614
SKILL_NAMES_V0615 = SKILL_NAMES_V0614
SUITE_FILES_V0616 = SUITE_FILES_V0615
SKILL_NAMES_V0616 = SKILL_NAMES_V0615
SUITE_FILES_V0617 = tuple(sorted((*SUITE_FILES_V0616,
    "story-skill-plan/references/qidian-categories.md",
)))
SKILL_NAMES_V0617 = SKILL_NAMES_V0616
SUITE_FILES_V0618 = tuple(sorted((*SUITE_FILES_V0617,
    "story-skill-write/references/writing-handoff.md",
    "story-skill-write/references/chapter-continuity.md",
    "story-skill-plan/references/short-platform.md",
)))
SKILL_NAMES_V0618 = SKILL_NAMES_V0617
SUITE_FILES_V0619 = tuple(sorted((*SUITE_FILES_V0618,
    "story-skill/scripts/story_platform_drafts.py",
    "story-skill/scripts/story_platform_browser.py",
    "story-skill/scripts/story_platform_chrome_bridge.py",
    "story-skill/scripts/story_chrome_bridge.mjs",
    "story-skill-publish/references/platform-drafts.md",
    "story-skill/scripts/story_workbench_books.py",
    "story-skill/scripts/story_workbench_archives.py",
)))
SKILL_NAMES_V0619 = SKILL_NAMES_V0618
SUITE_FILES_V0620 = SUITE_FILES_V0619
SKILL_NAMES_V0620 = SKILL_NAMES_V0619
SUITE_FILES_V0621 = tuple(sorted((*SUITE_FILES_V0620,
    "story-skill/scripts/story_chrome_worker.py",
    "story-skill/scripts/story_chrome_worker.mjs",
)))
SKILL_NAMES_V0621 = SKILL_NAMES_V0620
SUITE_FILES_V0622 = tuple(sorted((*SUITE_FILES_V0621,
    "story-skill/scripts/story_rank.py",
    "story-skill/scripts/story_rank_sources.py",
    "story-skill/scripts/story_research.py",
    "story-skill-research/references/rank-scan.md",
    "story-skill-research/references/research-handoff.md",
)))
SKILL_NAMES_V0622 = SKILL_NAMES_V0621
SUITE_FILES_V0623 = SUITE_FILES_V0622
SKILL_NAMES_V0623 = SKILL_NAMES_V0622
SUITE_FILES_V0624 = SUITE_FILES_V0623
SKILL_NAMES_V0624 = SKILL_NAMES_V0623
SUITE_FILES_V0625 = SUITE_FILES_V0624
SKILL_NAMES_V0625 = SKILL_NAMES_V0624
SUITE_FILES_V0626 = SUITE_FILES_V0625
SKILL_NAMES_V0626 = SKILL_NAMES_V0625

# Compatibility aliases mean "current source candidate", not every future 0.6.x release.
SKILL_NAMES = SKILL_NAMES_V0626
SUITE_FILES = SUITE_FILES_V0626
MARKER = ".story-skill-install.json"
HOST_DIRECTORIES = {"codex": ".agents", "claude-code": ".claude",
                    "antigravity": ".agents", "antigravity-cli": ".agents",
                    "qoder": ".qoder", "qoder-cn": ".qoder",
                    "zcode": ".zcode", "workbuddy": ".workbuddy-ai",
                    "trae": ".trae", "trae-cn": ".trae", "trae-cli": ".traecli",
                    "cursor": ".cursor", "codebuddy": ".codebuddy",
                    "opencode": ".opencode", "copilot": ".github",
                    "gemini-cli": ".gemini", "cline": ".cline",
                    "windsurf": ".windsurf", "devin": ".devin"}
USER_HOST_DIRECTORIES = {"codex": ".agents", "claude-code": ".claude",
                        "antigravity": ".gemini/config", "antigravity-cli": ".gemini/antigravity-cli",
                        "qoder": ".qoder", "qoder-cn": ".qoder-cn",
                        "zcode": ".zcode", "workbuddy": ".workbuddy-ai",
                        "trae": ".trae", "trae-cn": ".trae-cn", "trae-cli": ".traecli",
                        "cursor": ".cursor", "codebuddy": ".codebuddy",
                        "opencode": ".config/opencode", "copilot": ".copilot",
                        "gemini-cli": ".gemini", "cline": ".cline",
                        "windsurf": ".codeium/windsurf", "devin": ".config/devin"}


def host_directory(host, scope="project"):
    if scope not in ("project", "user"):
        raise ValueError(f"Unsupported installation scope: {scope}")
    directories = USER_HOST_DIRECTORIES if scope == "user" else HOST_DIRECTORIES
    try:
        return directories[host]
    except (KeyError, TypeError) as error:
        raise ValueError(f"Unsupported installation host: {host}") from error


def suite_files(version):
    if version == "0.6.0":
        return SUITE_FILES_V060
    if version in ("0.6.1", "0.6.2", "0.6.3", "0.6.4"):
        return SUITE_FILES_V061
    if version in ("0.6.5", "0.6.6", "0.6.7", "0.6.8"):
        return SUITE_FILES_V065
    if version == "0.6.9":
        return SUITE_FILES_V069
    if version == "0.6.10":
        return SUITE_FILES_V0610
    if version == "0.6.11":
        return SUITE_FILES_V0611
    if version == "0.6.12":
        return SUITE_FILES_V0612
    if version == "0.6.13":
        return SUITE_FILES_V0613
    if version == "0.6.14":
        return SUITE_FILES_V0614
    if version == "0.6.15":
        return SUITE_FILES_V0615
    if version == "0.6.16":
        return SUITE_FILES_V0616
    if version == "0.6.17":
        return SUITE_FILES_V0617
    if version == "0.6.18":
        return SUITE_FILES_V0618
    if version == "0.6.19":
        return SUITE_FILES_V0619
    if version == "0.6.20":
        return SUITE_FILES_V0620
    if version == "0.6.22":
        return SUITE_FILES_V0622
    if version == "0.6.23":
        return SUITE_FILES_V0623
    if version == "0.6.24":
        return SUITE_FILES_V0624
    if version == "0.6.25":
        return SUITE_FILES_V0625
    if version == "0.6.26":
        return SUITE_FILES_V0626
    if version == "0.6.21":
        return SUITE_FILES_V0621
    raise ValueError(f"Source runtime version has no reviewed suite layout: {version}")


def skill_names(version):
    """Return the immutable skill-root list reviewed for one exact version."""
    if version == "0.6.0":
        return SKILL_NAMES_V060
    if version in ("0.6.1", "0.6.2", "0.6.3", "0.6.4"):
        return SKILL_NAMES_V061
    if version in ("0.6.5", "0.6.6", "0.6.7", "0.6.8"):
        return SKILL_NAMES_V061
    if version == "0.6.9":
        return SKILL_NAMES_V069
    if version == "0.6.10":
        return SKILL_NAMES_V0610
    if version == "0.6.11":
        return SKILL_NAMES_V0611
    if version == "0.6.12":
        return SKILL_NAMES_V0612
    if version == "0.6.13":
        return SKILL_NAMES_V0613
    if version == "0.6.14":
        return SKILL_NAMES_V0614
    if version == "0.6.15":
        return SKILL_NAMES_V0615
    if version == "0.6.16":
        return SKILL_NAMES_V0616
    if version == "0.6.17":
        return SKILL_NAMES_V0617
    if version == "0.6.18":
        return SKILL_NAMES_V0618
    if version == "0.6.19":
        return SKILL_NAMES_V0619
    if version == "0.6.20":
        return SKILL_NAMES_V0620
    if version == "0.6.22":
        return SKILL_NAMES_V0622
    if version == "0.6.23":
        return SKILL_NAMES_V0623
    if version == "0.6.24":
        return SKILL_NAMES_V0624
    if version == "0.6.25":
        return SKILL_NAMES_V0625
    if version == "0.6.26":
        return SKILL_NAMES_V0626
    if version == "0.6.21":
        return SKILL_NAMES_V0621
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
def installation_lock(project, *, host="codex", scope="project"):
    """The OS releases this lock on process exit; the empty lock file may remain."""
    lock = checked(project, host_directory(host, scope) + "/skills/.story-skill-install.lock")
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


def validate_omitted_targets(project, names, *, host="codex", scope="project"):
    directory = host_directory(host, scope)
    parent = checked(project, directory + "/skills")
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
        target = checked(project, directory + "/skills/" + name)
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


def install_suite_locked(project, source, files, update, *, host="codex", scope="project"):
    directory = host_directory(host, scope)
    parent = checked(project, directory + "/skills")
    names = tuple(files)
    validate_omitted_targets(project, names, host=host, scope=scope)
    targets = {name: checked(project, directory + "/skills/" + name) for name in names}
    originals = {name: managed_snapshot(path) for name, path in targets.items()}
    changed = [name for name in names if originals[name] is None or files[name] != originals[name]["files"]]
    if not changed:
        return {"status": "unchanged", "host": host, "scope": scope, "path": str(parent), "skills": list(names)}
    if any(originals.values()) and not update:
        raise ValueError("A managed version exists; use --update for a reviewed suite replacement")
    stage = Path(tempfile.mkdtemp(prefix=".story-skill-stage-", dir=parent))
    backup = None
    moved, published, expected = [], [], {}
    manifests = {}
    preserve_stage = False

    def validate_all():
        validate_omitted_targets(project, names, host=host, scope=scope)
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
            backup = checked(project, f"{directory}/.story-skill-backups/{uuid.uuid4().hex}")
            backup.mkdir(parents=True)
        try:
            for name in changed:
                if originals[name] is not None:
                    move_directory(checked(project, directory + "/skills/" + name), backup / name)
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
                move_directory(stage / name, checked(project, directory + "/skills/" + name))
                published.append(name)
                if managed_snapshot(targets[name]) != expected[name]:
                    raise ValueError(f"Published installation differs from verified skill: {name}")
            for name in names:
                if managed_snapshot(targets[name]) != expected.get(name, originals[name]):
                    raise ValueError(f"Installed suite changed before completion: {name}")
            validate_omitted_targets(project, names, host=host, scope=scope)
        except BaseException as error:
            recovery_errors = []
            # Only withdraw our still-unchanged output. External edits are never deleted.
            for name in reversed(published):
                try:
                    if managed_snapshot(targets[name]) != expected[name]:
                        raise ValueError("Published skill now has external edits")
                    move_directory(checked(project, directory + "/skills/" + name), stage / name)
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
                    move_directory(backup / name, checked(project, directory + "/skills/" + name))
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
    return {"status": "updated" if any(originals.values()) else "installed", "host": host,
            "scope": scope, "path": str(parent),
            "skills": list(names), "changed_skills": changed,
            "backup": str(backup) if backup is not None else None,
            "files": sum(len(value) for value in files.values())}


def source_suite(source):
    """Resolve a core entry or complete suite without modifying its source."""
    source = Path(source).expanduser().absolute()
    if linked(source):
        raise ValueError(f"Refusing linked source directory: {source}")
    source = source.resolve()
    if (source / "SKILL.md").is_file():
        if source.name != SKILL_NAMES[0] or not (source / "scripts/story.py").is_file():
            raise ValueError("A full supported suite is required")
        source = source.parent
    files = suite_inventory(source)
    return source, files


def check_installation(project, source=SOURCE, *, host="codex", scope="project"):
    """Compare a complete installed suite to the source without writes or lock creation."""
    directory = host_directory(host, scope)
    project = Path(project).expanduser().resolve()
    source, files = source_suite(source)
    parent = checked(project, directory + "/skills")
    validate_omitted_targets(project, files, host=host, scope=scope)
    manifests = {}
    for name, expected in files.items():
        target = checked(project, directory + "/skills/" + name)
        if not target.is_dir():
            raise ValueError(f"Installed suite is missing a skill: {target}")
        if inventory(target) != expected:
            raise ValueError(f"Installed skill differs from the current source: {target}")
        manifests[name] = managed_snapshot(target) if checked(target, MARKER).exists() else None
    if suite_inventory(source) != files:
        raise ValueError("Source suite changed during verification; retry with a stable source")
    for name, expected in files.items():
        target = checked(parent, name)
        if inventory(target) != expected:
            raise ValueError(f"Installed skill changed during verification: {name}")
        manifest = managed_snapshot(target) if checked(target, MARKER).exists() else None
        if manifest != manifests[name]:
            raise ValueError(f"Installation manifest changed during verification: {name}")
    validate_omitted_targets(project, files, host=host, scope=scope)
    return {"status": "verified", "host": host, "scope": scope, "path": str(parent),
            "skills": list(files), "managed_skills": [name for name, value in manifests.items() if value is not None],
            "files": sum(map(len, files.values()))}


def install(project, update=False, source=SOURCE, *, host="codex", scope="project"):
    directory = host_directory(host, scope)
    project = Path(project).expanduser().resolve()
    source, files = source_suite(source)
    target = checked(project, directory + "/skills")
    if target == source:
        validate_omitted_targets(project, files, host=host, scope=scope)
        return {"status": "already_in_place", "host": host, "scope": scope,
                "path": str(target), "skills": list(files)}
    if target.resolve().is_relative_to(source):
        raise ValueError("Installation target must be outside the source directory")
    with installation_lock(project, host=host, scope=scope):
        return install_suite_locked(project, source, files, update, host=host, scope=scope)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", choices=tuple(HOST_DIRECTORIES), default="codex", help="Skill host (default: codex)")
    destination = p.add_mutually_exclusive_group(required=True)
    destination.add_argument("--project", help="Install into this project's host-specific skills directory")
    destination.add_argument("--user", action="store_true", help="Install into the current user's host-specific skills directory")
    action = p.add_mutually_exclusive_group()
    action.add_argument("--update", action="store_true", help="Replace an unchanged managed installation; retain backup")
    action.add_argument("--check", action="store_true", help="Verify installed files against this source without writing")
    args = p.parse_args()
    try:
        project = Path.home() if args.user else args.project
        scope = "user" if args.user else "project"
        result = (check_installation(project, host=args.host, scope=scope) if args.check else
                  install(project, args.update, host=args.host, scope=scope))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
