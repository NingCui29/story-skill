#!/usr/bin/env python3
"""Build or verify the npm content wrapper around an authenticated Release ZIP."""
import argparse
import ast
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile

NAME = "@ningcui29/story-skill"
REGISTRY = "https://npm.pkg.github.com"
REPOSITORY = "https://github.com/NingCui29/story-skill.git"
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
MAX_BYTES = 256 * 1024 * 1024


def payload_files(version):
    if version == "0.6.0":
        return SUITE_FILES_V060
    if version == "0.6.1":
        return SUITE_FILES_V061
    raise ValueError(f"Release version has no reviewed payload layout: {version}")


def skill_names(version):
    """Return the immutable skill-root list reviewed for one exact version."""
    if version == "0.6.0":
        return SKILL_NAMES_V060
    if version == "0.6.1":
        return SKILL_NAMES_V061
    raise ValueError(f"Release version has no reviewed payload layout: {version}")


def package_identity(version):
    payload_files(version)
    return NAME, REPOSITORY


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def integrity(raw):
    return "sha512-" + base64.b64encode(hashlib.sha512(raw).digest()).decode("ascii")


def version_from_source(raw):
    versions = []
    for node in ast.parse(raw.decode("utf-8-sig")).body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        if any(isinstance(target, ast.Name) and target.id == "VERSION" for target in targets):
            versions.append(ast.literal_eval(node.value))
    if len(versions) != 1 or not isinstance(versions[0], str) or not re.fullmatch(
            r"[0-9]+\.[0-9]+\.[0-9]+", versions[0]):
        raise ValueError("ZIP runtime must declare exactly one literal major.minor.patch VERSION")
    return versions[0]


def read_release(archive, sha256_file):
    """Read a checksum-bound, explicit skill file list without extracting or executing it."""
    archive = Path(archive).expanduser().resolve()
    lines = [line for line in Path(sha256_file).read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    checksum = re.fullmatch(r"([0-9a-fA-F]{64})[ \t]+\*?([^\r\n]+)", lines[0]) if len(lines) == 1 else None
    if checksum is None or checksum.group(2) != archive.name:
        raise ValueError("Checksum file must contain exactly one SHA256 entry naming this archive")
    raw = archive.read_bytes()
    if sha256(raw) != checksum.group(1).lower():
        raise ValueError("Release ZIP SHA256 does not match its checksum file")
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        members = bundle.infolist()
        names = [member.filename for member in members]
        if len(names) != len(set(names)):
            raise ValueError("Release ZIP must not contain duplicate members")
        if sum(member.file_size for member in members) > MAX_BYTES:
            raise ValueError("Release ZIP payload is too large")
        for member in members:
            kind = stat.S_IFMT(member.external_attr >> 16)
            if member.is_dir() or kind not in (0, stat.S_IFREG):
                raise ValueError(f"Release ZIP contains a linked or special member: {member.filename}")
        if "story-skill/scripts/story.py" not in names:
            raise ValueError("Release ZIP is missing its shared runtime")
        payload = {name: bundle.read(name) for name in sorted(names)}
    version = version_from_source(payload["story-skill/scripts/story.py"])
    if set(payload) != set(payload_files(version)):
        raise ValueError("Release ZIP file list does not match its version's reviewed layout")
    if archive.name != f"story-skill-{version}.zip":
        raise ValueError("Release ZIP filename differs from its runtime VERSION")
    return payload, version, sha256(raw)


def wrapper_files(version):
    files = payload_files(version)
    names = skill_names(version)
    name, repository = package_identity(version)
    manifest = {
        "name": name, "version": version,
        "description": "Eight Story Skill skills for Chinese novel writing, review and publication preparation",
        "license": "MIT",
        "repository": {"type": "git", "url": repository},
        "homepage": repository.removesuffix(".git") + "#readme",
        "publishConfig": {"registry": REGISTRY},
        "files": list(files),
    }
    readme = (
        f"# Story Skill {version}\n\n"
        "This npm package contains eight sibling skills: "
        + ", ".join(f"`{skill}/`" for skill in names) + ". "
        + f"Its {len(files)} skill files preserve the exact bytes of the matching GitHub Release ZIP.\n\n"
        "npm distributes content; installing this package does not register skills with the host app. "
        "Copy all eight complete skill directories into your project's `.agents/skills/`, "
        "or follow the repository's managed installation instructions. "
        "Do not copy only an individual task skill: its shared runtime is required.\n\n"
        "For macOS, Linux and Windows, ask:\n\n```text\n"
        f"$skill-installer 按 https://github.com/NingCui29/story-skill/blob/v{version}/INSTALL.md "
        f"安装或升级 Story Skill，固定使用 v{version}。\n```\n\n"
        "The shared Python runtime is `story-skill/scripts/story.py`; npm does not install Python. "
        "Project data and novels belong outside the skill installation directory.\n\n"
        "The publication skill prepares local snapshots and records only; it does not log in to "
        "author platforms, upload chapters or publish them remotely.\n\n"
        "GitHub Packages npm downloads require authentication. "
        "See https://github.com/NingCui29/story-skill for setup and update instructions.\n\n"
        "License: MIT; each skill contains its complete `LICENSE`.\n"
    )
    return manifest, {
        "package.json": (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        "README.md": readme.encode("utf-8"),
    }


def verify_tarball(archive, sha256_file, tarball, expected_manifest=None):
    """Bind files to an earlier build; report actual container hashes for registry SRI checks."""
    payload, version, archive_sha = read_release(archive, sha256_file)
    manifest, wrapper = wrapper_files(version)
    expected_files = {"package/" + name: raw for name, raw in {**payload, **wrapper}.items()}
    tarball = Path(tarball).expanduser().resolve()
    if tarball.stat().st_size > MAX_BYTES:
        raise ValueError("npm tarball is too large")
    raw = tarball.read_bytes()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as bundle:
        seen = set()
        for member in bundle:
            if member.name in seen:
                raise ValueError(f"Duplicate npm tarball member: {member.name}")
            seen.add(member.name)
            if not member.isfile() or member.name not in expected_files:
                raise ValueError(f"Unexpected, linked or special npm tarball member: {member.name}")
            expected = expected_files[member.name]
            if member.size != len(expected):
                raise ValueError(f"npm tarball member bytes differ: {member.name}")
            stream = bundle.extractfile(member)
            if stream is None or stream.read() != expected:
                raise ValueError(f"npm tarball member bytes differ: {member.name}")
        if seen != set(expected_files):
            raise ValueError("npm tarball is missing expected files")
    result = {
        "ok": True, "name": manifest["name"], "version": version, "tarball": str(tarball),
        "sha256": sha256(raw), "integrity": integrity(raw), "bytes": len(raw),
        "registry": REGISTRY, "archive_sha256": archive_sha,
        "payload_manifest": {name: sha256(content) for name, content in payload.items()},
        "wrapper_manifest": {name: sha256(content) for name, content in wrapper.items()},
        "package_manifest": manifest,
    }
    if expected_manifest is not None:
        if isinstance(expected_manifest, (str, os.PathLike)):
            expected_manifest = json.loads(Path(expected_manifest).read_text(encoding="utf-8-sig"))
        if not isinstance(expected_manifest, dict) or expected_manifest.get("ok") is not True:
            raise ValueError("Expected manifest must be a successful build or verification JSON")
        # Registries may encode the same tar differently. Bind all file bytes here;
        # the caller must separately compare this result's actual SRI with the registry.
        for key in ("name", "version", "registry", "archive_sha256",
                    "payload_manifest", "wrapper_manifest", "package_manifest"):
            if expected_manifest.get(key) != result[key]:
                raise ValueError(f"npm tarball differs from the expected build manifest: {key}")
    return result


def npm_command():
    """Use npm's JS entry on Windows instead of passing arbitrary paths through cmd.exe."""
    npm = shutil.which("npm")
    if npm is None:
        raise ValueError("npm is required to build this content package")
    npm_path = Path(npm)
    if os.name == "nt":
        cli = npm_path.parent / "node_modules/npm/bin/npm-cli.js"
        node = shutil.which("node")
        if node is None or not cli.is_file():
            raise ValueError("Cannot locate node and npm-cli.js next to the Windows npm launcher")
        return [node, str(cli)]
    return [npm]


def npm_pack(stage, destination):
    command = npm_command() + ["pack", "--json", "--ignore-scripts", "--pack-destination", str(destination)]
    process = subprocess.run(command, cwd=stage, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=180)
    if process.returncode:
        raise RuntimeError(f"npm pack failed (exit {process.returncode}): {process.stderr.strip()}")
    rows = json.loads(process.stdout)
    # npm 12 keys JSON pack receipts by package name; older versions use a list.
    if isinstance(rows, dict) and len(rows) == 1:
        name, receipt = next(iter(rows.items()))
        if isinstance(receipt, dict) and receipt.get("name") == name:
            rows = [receipt]
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("npm pack did not return exactly one package result")
    return rows[0]


def build(archive, sha256_file, output_dir):
    payload, version, _ = read_release(archive, sha256_file)
    manifest, wrapper = wrapper_files(version)
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = manifest["name"].removeprefix("@").replace("/", "-") + f"-{version}.tgz"
    with tempfile.TemporaryDirectory(prefix=".story-npm-stage-", dir=output_dir) as directory:
        stage = Path(directory)
        content, packed = stage / "content", stage / "packed"
        content.mkdir()
        packed.mkdir()
        for name, raw in {**payload, **wrapper}.items():
            path = content / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        receipt = npm_pack(content, packed)
        if receipt.get("filename") != filename or receipt.get("name") != manifest["name"] or receipt.get("version") != version:
            raise ValueError("npm pack returned an unexpected filename or package identity")
        candidate = packed / filename
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError("npm pack did not create an ordinary tarball file")
        result = verify_tarball(archive, sha256_file, candidate)
        if receipt.get("integrity") != result["integrity"]:
            raise ValueError("npm pack integrity differs from the actual tarball")
        destination = output_dir / filename
        os.replace(candidate, destination)
        result["tarball"] = str(destination)
        return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("build", "verify"):
        argv.insert(0, "build")
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "verify"):
        command = subparsers.add_parser(name)
        command.add_argument("--archive", required=True, help="Published story-skill-<version>.zip")
        command.add_argument("--sha256-file", required=True, help="Published SHA256 file naming the ZIP")
        if name == "build":
            command.add_argument("--output-dir", required=True)
        else:
            command.add_argument("--tarball", required=True)
            command.add_argument("--expected-manifest", help="JSON output from the original successful build")
    args = parser.parse_args(argv)
    try:
        result = (build(args.archive, args.sha256_file, args.output_dir) if args.command == "build" else
                  verify_tarball(args.archive, args.sha256_file, args.tarball, args.expected_manifest))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, RuntimeError, SyntaxError, zipfile.BadZipFile,
            tarfile.TarError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "error_type": type(exc).__name__}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
