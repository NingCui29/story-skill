#!/usr/bin/env python3
"""Create a self-contained, reproducible skill archive (no novel data or virtualenv)."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
SKILL = SKILLS / "story-codex"
SOURCE = SKILLS
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


def suite_files(version):
    """Keep published layouts fixed while reviewing each supported version family."""
    if re.fullmatch(r"0\.4\.(?:0|[1-9][0-9]*)", version) or version == "0.5.0":
        return LEGACY_SUITE_FILES
    patch = re.fullmatch(r"0\.5\.([1-9][0-9]*)", version)
    if patch:
        return TAGGED_SUITE_FILES if int(patch.group(1)) >= 7 else SUITE_FILES
    raise ValueError(f"Release version has no reviewed suite layout: {version}")


def source_version(raw):
    """Read the packaged runtime's literal VERSION without executing its code."""
    tree = ast.parse(raw.decode("utf-8-sig"))
    values = []
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        if any(isinstance(target, ast.Name) and target.id == "VERSION" for target in targets):
            values.append(ast.literal_eval(node.value))
    if len(values) != 1 or not isinstance(values[0], str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", values[0]):
        raise ValueError("Runtime must define one literal VERSION in major.minor.patch form")
    return values[0]


def current_version(runtime=None):
    return source_version(Path(runtime or SKILL / "scripts/story.py").read_bytes())


def validate_archive_name(output, version):
    match = re.fullmatch(r"story-codex-([0-9]+\.[0-9]+\.[0-9]+)\.zip", Path(output).name)
    if match and match.group(1) != version:
        raise ValueError(f"Archive filename version {match.group(1)} differs from runtime VERSION {version}")


def validate_archive(path, entries):
    with zipfile.ZipFile(path) as archive:
        if archive.namelist() != [name for name, _ in entries]:
            raise RuntimeError("Archive file list differs from the source snapshot")
        if archive.testzip() is not None:
            raise RuntimeError("Archive verification failed")
        for name, raw in entries:
            if archive.read(name) != raw:
                raise RuntimeError(f"Archive content differs from the source snapshot: {name}")


def linked(path):
    attributes = path.lstat()
    return stat.S_ISLNK(attributes.st_mode) or bool(
        getattr(attributes, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def source_entries():
    """Package only the reviewed suite manifest, rejecting new or missing skill files."""
    if linked(SOURCE):
        raise ValueError("Refusing linked source directory")
    files = {}
    for skill in SKILL_NAMES:
        directory = SOURCE / skill
        if not directory.is_dir() or linked(directory):
            raise ValueError(f"Source suite is missing an ordinary skill directory: {skill}")
        for path in sorted(directory.rglob("*")):
            relative = path.relative_to(SOURCE)
            if linked(path):
                raise ValueError(f"Refusing linked package content: {relative}")
            if "__pycache__" in relative.parts or path.suffix == ".pyc":
                continue
            if path.is_file():
                files[relative.as_posix()] = path.read_bytes()
            elif not path.is_dir():
                raise ValueError(f"Refusing special package content: {relative}")
    runtime = files.get("story-codex/scripts/story.py")
    if runtime is None:
        raise ValueError("Source suite differs from its reviewed file manifest; missing scripts/story.py")
    required = set(suite_files(source_version(runtime)))
    if set(files) != required:
        raise ValueError(f"Source suite differs from its reviewed file manifest; "
                         f"missing={sorted(required - files.keys())}, "
                         f"extra={sorted(files.keys() - required)}")
    return sorted(files.items())


def package(output=None):
    entries = source_entries()
    runtime = dict(entries).get("story-codex/scripts/story.py")
    if runtime is None:
        raise ValueError("Source package is missing scripts/story.py")
    version = source_version(runtime)
    output = (Path(output).expanduser() if output is not None else ROOT / f"dist/story-codex-{version}.zip").absolute()
    validate_archive_name(output, version)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".story-codex-package-", suffix=".zip", dir=output.parent)
    stage = Path(name)
    try:
        os.close(fd)
        with zipfile.ZipFile(stage, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for entry_name, raw in entries:
                info = zipfile.ZipInfo(entry_name, date_time=(2026, 9, 8, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, raw)
        validate_archive(stage, entries)
        archive_bytes = stage.read_bytes()
        result = {"archive": str(output), "version": version, "files": len(entries),
                  "sha256": hashlib.sha256(archive_bytes).hexdigest(), "bytes": len(archive_bytes)}
        os.replace(stage, output)
        return result
    finally:
        if stage.exists():
            stage.unlink()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", help="Archive path; defaults to dist/story-codex-<runtime VERSION>.zip")
    args = p.parse_args()
    print(json.dumps(package(args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
