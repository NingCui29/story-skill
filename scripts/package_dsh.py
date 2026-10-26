#!/usr/bin/env python3
"""Build the DeepSeek Harness bundle from the reviewed Story Skill suite.

The bundle lives outside the skill source: it contains a copy of every skill
file so Harness can install it as a normal local package. Rebuild after any
change under skills/; do not install the source dsh/ directory directly.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "dsh"
SKILLS = ROOT / "skills"
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PACKAGE_NAME = "story-skill-dsh"
BUNDLE_FILES = (
    "package.json",
    "cordis.patch.yml",
    "index.js",
    "icon.svg",
    "README.md",
    "LICENSE",
    "locale/en.json",
    "locale/zh.json",
)


def load_package_module():
    spec = importlib.util.spec_from_file_location("story_skill_package", ROOT / "scripts/package.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest_files(files):
    digest = hashlib.sha256()
    for relative in sorted(files):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(files[relative]).digest())
    return digest.hexdigest()


def read_frontmatter(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"Skill is missing YAML frontmatter: {path}")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise ValueError(f"Skill is missing closing YAML frontmatter: {path}") from None
    fields = {}
    for line in lines[1:end]:
        if not line or line.startswith((" ", "\t", "#")):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise ValueError(f"Unsupported frontmatter line in {path}: {line}")
        fields[key.strip()] = value.strip()
    if not fields.get("name") or not fields.get("description"):
        raise ValueError(f"Skill requires name and description: {path}")
    return fields


def validate(stage, version, entries, names):
    manifest_path = stage / "package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("name") != PACKAGE_NAME:
        raise ValueError("Bundle package name differs from the reviewed name")
    if manifest.get("version") != version:
        raise ValueError("Bundle version differs from the runtime VERSION")
    patch = manifest.get("dsh", {}).get("bundle", {}).get("patch")
    if patch != "./cordis.patch.yml" or not (stage / patch).is_file():
        raise ValueError("Bundle manifest does not declare its cordis.patch.yml")
    if "skills/**" not in manifest.get("files", []):
        raise ValueError("Bundle files must include skills/**")

    patch_text = (stage / "cordis.patch.yml").read_text(encoding="utf-8")
    for required in (PACKAGE_NAME, "@deepseek-ai/dsh-skill-filesystem",
                     "providerName: story-skill", "includeDefaultRoots: false",
                     "bundledSkillDir"):
        if required not in patch_text:
            raise ValueError(f"Bundle patch is missing {required!r}")

    for relative in ("locale/en.json", "locale/zh.json"):
        locale = json.loads((stage / relative).read_text(encoding="utf-8"))
        meta = locale.get("meta") if isinstance(locale, dict) else None
        if not isinstance(meta, dict) or not meta.get("title") or not meta.get("description"):
            raise ValueError(f"Bundle locale is missing meta.title/meta.description: {relative}")

    icon = stage / "icon.svg"
    if not icon.read_bytes().startswith(b"<svg") or icon.stat().st_size > 256 * 1024:
        raise ValueError("Bundle icon must be an SVG no larger than 256 KiB")

    skills_root = stage / "skills"
    actual = sorted(path.name for path in skills_root.iterdir() if path.is_dir())
    if actual != sorted(names):
        raise ValueError(f"Bundle skill roots differ from the reviewed suite: {actual}")
    found = {}
    for path in sorted(skills_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(stage).as_posix()
        if relative == "skills":  # pragma: no cover - defensive
            continue
        found[relative] = path.read_bytes()
        if path.name == "SKILL.md" and path.parent.parent == skills_root:
            fields = read_frontmatter(path)
            if fields["name"] != path.parent.name or not NAME.fullmatch(fields["name"]):
                raise ValueError(f"Invalid DSH skill frontmatter: {path}")
            if len(fields["description"]) > 500:
                raise ValueError(f"Skill description exceeds the DSH catalog cap: {path}")
    expected = {f"skills/{relative}": raw for relative, raw in entries}
    if found != expected:
        raise ValueError("Bundle skill bytes differ from the reviewed suite")
    if sum(path.name == "SKILL.md" for path in skills_root.rglob("SKILL.md")) != len(names):
        raise ValueError("Bundle must contain exactly one SKILL.md per skill root")
    return found


def copy_bundle_sources(stage):
    for relative in BUNDLE_FILES:
        source = BUNDLE / relative
        if not source.is_file():
            raise ValueError(f"Bundle source is missing {relative}")
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def write_archive(directory, archive, force=False):
    archive = Path(archive).expanduser().absolute()
    if archive.suffix.lower() != ".zip":
        raise ValueError(f"Bundle archive must be a ZIP file: {archive}")
    resolved = archive.resolve()
    if resolved.is_relative_to(BUNDLE.resolve()) or resolved.is_relative_to(SKILLS.resolve()):
        raise ValueError("Bundle archive must stay outside the source dsh/ and skills/ directories")
    dsh_home = Path(os.environ.get("DSH_HOME", Path.home() / ".dsh")).expanduser().resolve()
    if resolved.is_relative_to(dsh_home):
        raise ValueError("Bundle archive must stay outside DSH_HOME")
    if archive.exists():
        if not force:
            raise ValueError(f"Bundle archive already exists: {archive}; pass --force to rebuild")
        if archive.is_symlink() or not archive.is_file():
            raise ValueError(f"Refusing to replace a linked or non-file archive: {archive}")
    files = {path.relative_to(directory).as_posix(): path.read_bytes()
             for path in sorted(directory.rglob("*")) if path.is_file()}
    archive.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".story-skill-dsh-", suffix=".zip", dir=archive.parent)
    stage = Path(temporary)
    try:
        os.close(fd)
        with zipfile.ZipFile(stage, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for relative, raw in sorted(files.items()):
                info = zipfile.ZipInfo(relative, date_time=(2026, 9, 8, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                bundle.writestr(info, raw)
        with zipfile.ZipFile(stage) as bundle:
            if bundle.namelist() != sorted(files) or bundle.testzip() is not None:
                raise RuntimeError("Bundle archive differs from the built package")
            for relative, raw in files.items():
                if bundle.read(relative) != raw:
                    raise RuntimeError(f"Bundle archive content differs from the built package: {relative}")
        os.replace(stage, archive)
    finally:
        if stage.exists():
            stage.unlink()
    raw = archive.read_bytes()
    return {"archive": str(archive), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
            "files": len(files)}


def build(output=None, force=False, archive=None):
    package = load_package_module()
    entries = package.source_entries()
    runtime = dict(entries).get("story-skill/scripts/story.py")
    if runtime is None:
        raise ValueError("Source suite is missing story-skill/scripts/story.py")
    version = package.source_version(runtime)
    names = tuple(package.skill_names(version))
    output = (Path(output).expanduser() if output is not None else ROOT / "dist/dsh-plugin").absolute()
    resolved = output.resolve()
    if resolved.is_relative_to(BUNDLE.resolve()) or resolved.is_relative_to(SKILLS.resolve()):
        raise ValueError("Bundle output must stay outside the source dsh/ and skills/ directories")
    for protected in (ROOT.resolve(), BUNDLE.resolve(), SKILLS.resolve()):
        if resolved == protected or protected.is_relative_to(resolved):
            raise ValueError("Bundle output must not contain the repository or its skill sources")
    dsh_home = Path(os.environ.get("DSH_HOME", Path.home() / ".dsh")).expanduser().resolve()
    if resolved.is_relative_to(dsh_home):
        raise ValueError("Bundle output must stay outside DSH_HOME; install the built bundle through Harness")
    if output.exists():
        if not force:
            raise ValueError(f"Bundle output already exists: {output}; pass --force to rebuild")
        if output.is_symlink() or not output.is_dir():
            raise ValueError(f"Refusing to replace a linked or non-directory output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".story-skill-dsh-stage-", dir=output.parent))
    try:
        copy_bundle_sources(stage)
        for relative, raw in entries:
            destination = stage / "skills" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        files = validate(stage, version, entries, names)
        if output.exists():
            shutil.rmtree(output)
        os.replace(stage, output)
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
    package_files = {path.relative_to(output).as_posix(): path.read_bytes()
                     for path in output.rglob("*") if path.is_file()}
    archive_result = write_archive(output, archive, force) if archive else None
    receipt = output.with_name(output.name + ".json")
    receipt_data = {
        "schema": 1,
        "package": PACKAGE_NAME,
        "version": version,
        "path": str(output),
        "skills": list(names),
        "files": len(package_files),
        "skill_files": len(entries),
        "sha256": digest_files(package_files),
        "manifest": {relative: hashlib.sha256(raw).hexdigest()
                     for relative, raw in sorted(package_files.items())},
    }
    if archive_result is not None:
        receipt_data["archive"] = archive_result["archive"]
        receipt_data["archive_sha256"] = archive_result["sha256"]
        receipt_data["archive_bytes"] = archive_result["bytes"]
    receipt.write_text(json.dumps(receipt_data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
    result = {"ok": True, "package": PACKAGE_NAME, "version": version, "path": str(output),
              "skills": list(names), "files": len(package_files), "skill_files": len(entries),
              "sha256": digest_files(package_files), "receipt": str(receipt)}
    if archive_result is not None:
        result.update(archive=archive_result["archive"], archive_sha256=archive_result["sha256"],
                      archive_bytes=archive_result["bytes"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="Bundle directory; defaults to dist/dsh-plugin")
    parser.add_argument("--archive", help="Optional ZIP path for the release asset")
    parser.add_argument("--force", action="store_true", help="Replace an existing bundle directory or archive")
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.output, args.force, args.archive), ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())