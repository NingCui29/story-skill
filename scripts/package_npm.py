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

NAME = "@ningcui29/story-codex"
REGISTRY = "https://npm.pkg.github.com"
REPOSITORY = "https://github.com/NingCui29/story-skill.git"
LEGACY_NAME = "@cuinings/story-codex"
LEGACY_REPOSITORY = "https://github.com/Cuinings/story-skill.git"
PAYLOAD_FILES = tuple("story-codex/" + name for name in (
    "LICENSE", "SKILL.md", "agents/openai.yaml", "references/analyze.md",
    "references/long-form.md", "references/research.md", "references/revise.md",
    "references/write.md", "scripts/story.py", "scripts/story_history.py",
    "scripts/story_search.py", "scripts/story_storage.py", "scripts/story_world.py",
))
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
MAX_BYTES = 256 * 1024 * 1024


def payload_files(version):
    if version == "0.3.0":
        return PAYLOAD_FILES
    if re.fullmatch(r"0\.4\.(?:0|[1-9][0-9]*)", version) or version == "0.5.0":
        return LEGACY_SUITE_FILES
    patch = re.fullmatch(r"0\.5\.([1-9][0-9]*)", version)
    if patch:
        return TAGGED_SUITE_FILES if int(patch.group(1)) >= 7 else SUITE_FILES
    raise ValueError(f"Release version has no reviewed payload layout: {version}")


def package_identity(version):
    payload_files(version)
    return (LEGACY_NAME, LEGACY_REPOSITORY) if version == "0.3.0" else (NAME, REPOSITORY)


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
        layouts = (set(PAYLOAD_FILES), set(LEGACY_SUITE_FILES), set(SUITE_FILES),
                   set(TAGGED_SUITE_FILES))
        if len(names) != len(set(names)) or set(names) not in layouts:
            raise ValueError("Release ZIP must contain exactly a reviewed skill file list, without duplicates")
        if sum(member.file_size for member in members) > MAX_BYTES:
            raise ValueError("Release ZIP payload is too large")
        for member in members:
            kind = stat.S_IFMT(member.external_attr >> 16)
            if member.is_dir() or kind not in (0, stat.S_IFREG):
                raise ValueError(f"Release ZIP contains a linked or special member: {member.filename}")
        payload = {name: bundle.read(name) for name in sorted(names)}
    version = version_from_source(payload["story-codex/scripts/story.py"])
    if set(payload) != set(payload_files(version)):
        raise ValueError("Release ZIP file list does not match its version's reviewed layout")
    if archive.name != f"story-codex-{version}.zip":
        raise ValueError("Release ZIP filename differs from its runtime VERSION")
    return payload, version, sha256(raw)


def wrapper_files(version):
    files = payload_files(version)
    name, repository = package_identity(version)
    manifest = {
        "name": name, "version": version,
        "description": "Complete Story Codex skill content for Chinese novel writing and review",
        "license": "MIT",
        "repository": {"type": "git", "url": repository},
        "homepage": repository.removesuffix(".git") + "#readme",
        "publishConfig": {"registry": REGISTRY},
        "files": list(files),
    }
    readme = (
        f"# Story Codex {version}\n\n"
        "This npm package contains the complete skill in `story-codex/`. "
        "Its 13 skill files preserve the exact bytes of the matching GitHub Release ZIP.\n\n"
        "npm is a content distribution channel; installing this package does not register "
        "the skill with Codex. The original Git-based skill installation remains recommended. "
        "In Codex, ask:\n\n"
        "```text\n"
        "使用 skill-installer 安装 https://github.com/Cuinings/story-skill/tree/"
        f"v{version}/.agents/skills/story-codex\n"
        "```\n\n"
        "Alternatively, copy the complete `story-codex/` directory to your project's "
        "`.agents/skills/` using the repository's documented installation procedure. "
        "The Python runtime is `story-codex/scripts/story.py`; npm does not install Python.\n\n"
        "GitHub Packages npm downloads require authentication. "
        "See https://github.com/Cuinings/story-skill for setup and update instructions.\n\n"
        "License: MIT; the complete license is in `story-codex/LICENSE`.\n"
    )
    if version != "0.3.0":
        manifest["description"] = "Complete seven-skill Story Codex suite for Chinese novel writing and review"
        readme = (
            f"# Story Codex {version}\n\n"
            "This npm package contains seven sibling skills: "
            + ", ".join(f"`{name}/`" for name in SKILL_NAMES) + ". "
            f"Its {len(files)} skill files preserve the exact bytes of the matching GitHub Release ZIP.\n\n"
            "npm distributes content; it does not register skills with Codex. "
            "Copy all seven complete skill directories into your project's `.agents/skills/`, "
            "or follow the repository's managed suite installation instructions. "
            "Do not copy only an individual task skill: its shared runtime is required.\n\n"
            "In Codex, ask:\n\n```text\n"
            f"使用 skill-installer 从 https://github.com/NingCui29/story-skill/tree/v{version}/skills "
            "安装全部七个技能目录：" + "、".join(SKILL_NAMES) + "。\n```\n\n"
            "The shared Python runtime is `story-codex/scripts/story.py`; npm does not install Python. "
            "Project data and novels belong outside the skill installation directory.\n\n"
            "GitHub Packages npm downloads require authentication. "
            "See https://github.com/NingCui29/story-skill for setup and update instructions.\n\n"
            "License: MIT; each skill contains its complete `LICENSE`.\n"
        )
    if version.startswith("0.5."):
        previous_request = (f"使用 skill-installer 从 https://github.com/NingCui29/story-skill/tree/v{version}/skills "
                            "安装全部七个技能目录：" + "、".join(SKILL_NAMES) + "。")
        readme = readme.replace(previous_request,
            "$skill-installer 按 https://github.com/NingCui29/story-skill/blob/main/INSTALL.md "
            f"安装或升级 Story Codex，固定使用 v{version}。")
    patch = re.fullmatch(r"0\.5\.([1-9][0-9]*)", version)
    if patch and int(patch.group(1)) < 10:
        readme = readme.replace("In Codex, ask:", "For macOS/Linux, in Codex, ask:")
        readme = readme.replace("The shared Python runtime is", (
            f"Platform scope: v{version} is released for macOS/Linux. Windows manuscript and report "
            "export still has the known WinError 32 limitation; Windows users should retain "
            "the verified v0.4.0 suite. Do not automatically downgrade a book already processed "
            "by v0.5.x; preserve the complete book and skill backup first.\n\n"
            "The shared Python runtime is"))
    elif patch:
        readme = readme.replace("In Codex, ask:", "For macOS, Linux and Windows, in Codex, ask:")
        readme = readme.replace("The shared Python runtime is", (
            "Platform scope: macOS, Linux and Windows. Consult the matching release verification "
            "for the tested environments and remaining limitations. Preserve a complete book "
            "and skill backup before upgrading.\n\n"
            "The shared Python runtime is"))
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
        command.add_argument("--archive", required=True, help="Published story-codex-<version>.zip")
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
