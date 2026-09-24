#!/usr/bin/env python3
"""Read-only v0.5.3 Release download and official isolated-install verification."""
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
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[4]
OUTPUT = Path(__file__).resolve().parent
REPO = "NingCui29/story-skill"
TAG = "v0.5.3"
VERSION = "0.5.3"
PYTHON = "/Users/cuining/.cache/story-skill-runtimes/story-skill-primary-runtime/dependencies/python/bin/python3"
GH = "/private/tmp/story-github-cli-anbp8wqh/gh"
INSTALLER = Path("/Users/cuining/.story-skill/skills/.system/skill-installer/scripts/install-skill-from-github.py")
GLOBAL_SKILLS = Path("/Users/cuining/.story-skill/skills")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load(name):
    spec = importlib.util.spec_from_file_location("remote_verify_" + name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inventory(parent, names):
    files = {}
    for name in names:
        directory = parent / name
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError(f"Missing or linked skill directory: {directory}")
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"Linked installed skill content: {path}")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                files[path.relative_to(parent).as_posix()] = digest(path.read_bytes())
    return files


def write_new(path, report):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-commit", required=True, help="Reviewed full commit SHA expected behind the fixed tag")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_commit):
        parser.error("--expected-commit must be a full lowercase commit SHA")
    targets = {key: OUTPUT / (key + ".json") for key in ("release", "remote-install")}
    if any(path.exists() for path in targets.values()):
        raise ValueError("Refusing to overwrite an existing download or installation receipt")
    package, npm = load("package"), load("package_npm")
    expected_files = dict(package.source_entries())
    expected = {name: digest(raw) for name, raw in expected_files.items()}
    if len(expected) != 33 or package.current_version() != VERSION:
        raise ValueError("Current source is not the expected 33-file v0.5.3 suite")
    archive_name = f"story-skill-{VERSION}.zip"
    local_archive = ROOT / "dist" / archive_name
    local_raw = local_archive.read_bytes()
    global_before = inventory(GLOBAL_SKILLS, package.SKILL_NAMES)
    common = {"date": datetime.now(timezone.utc).isoformat(), "repo": REPO, "tag": TAG,
              "expected_commit": args.expected_commit, "environment": {
                  "platform": platform.platform(), "python": platform.python_version(), "executable": sys.executable},
              "verifier_sha256": digest(Path(__file__).read_bytes())}
    release = {**common, "ok": False, "commands": [], "scope":
               "Point-in-time tag, public Release asset bytes, exact payload and current local source binding; no GitHub mutation"}
    installed = {**common, "ok": False, "commands": [], "installer": {
        "path": str(INSTALLER), "sha256": digest(INSTALLER.read_bytes())}, "scope":
        "Official public fixed-tag download into a unique temporary directory; four real CLI checks after exact file verification. "
        "No global skill installation, user novel, 助手 UI discovery, Windows execution or literary quality validation."}

    def run(report, command, env=None, timeout=180):
        started = time.monotonic()
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, timeout=timeout)
        item = {"command": list(map(str, command)), "exit_code": result.returncode,
                "stdout": result.stdout.decode("utf-8", errors="replace"),
                "stderr": result.stderr.decode("utf-8", errors="replace"),
                "duration_seconds": round(time.monotonic() - started, 3)}
        report["commands"].append(item)
        if result.returncode:
            raise ValueError(f"Read-only verification command failed: {command[0]}")
        return item["stdout"]

    def api(endpoint):
        return json.loads(run(release, [GH, "api", endpoint]))

    temporary = None
    try:
        tag_ref = api(f"repos/{REPO}/git/ref/tags/{TAG}")
        obj = tag_ref["object"]
        for _ in range(5):
            if obj["type"] == "commit":
                break
            if obj["type"] != "tag":
                raise ValueError("Remote tag does not resolve to a commit")
            obj = api(f"repos/{REPO}/git/tags/{obj['sha']}")["object"]
        if obj["type"] != "commit" or obj["sha"] != args.expected_commit:
            raise ValueError("Remote tag differs from the reviewed release commit")
        local_commit = run(release, ["git", "rev-parse", TAG + "^{commit}"]).strip()
        if local_commit != obj["sha"]:
            raise ValueError("Local and remote tags resolve to different commits")
        metadata = api(f"repos/{REPO}/releases/tags/{TAG}")
        if metadata["draft"] or metadata["prerelease"] or metadata["tag_name"] != TAG:
            raise ValueError("Expected a published stable Release")
        release.update(resolved_commit=obj["sha"], local_tag_commit=local_commit, release=metadata,
                       html_url=metadata["html_url"], published_at=metadata["published_at"])
        with tempfile.TemporaryDirectory(prefix="story-v053-remote-install-") as folder:
            temporary = Path(folder).resolve()
            downloads = temporary / "downloads"
            downloads.mkdir()
            run(release, [GH, "release", "download", TAG, "--repo", REPO, "--pattern", archive_name,
                          "--pattern", archive_name + ".sha256", "--dir", str(downloads)])
            archive, checksum = downloads / archive_name, downloads / (archive_name + ".sha256")
            payload, version, archive_sha = npm.read_release(archive, checksum)
            if version != VERSION or archive.read_bytes() != local_raw or payload != expected_files:
                raise ValueError("Downloaded Release differs from local ZIP or current skill source")
            for path in (archive, checksum):
                assets = [asset for asset in metadata["assets"] if asset["name"] == path.name]
                if len(assets) != 1 or assets[0]["state"] != "uploaded" or assets[0]["size"] != path.stat().st_size:
                    raise ValueError("Downloaded asset differs from Release metadata")
                if assets[0].get("digest") and assets[0]["digest"] != "sha256:" + digest(path.read_bytes()):
                    raise ValueError("Downloaded asset failed its server digest")
            release.update(ok=True, sha256=archive_sha, bytes=archive.stat().st_size, files=len(payload),
                           checksum_text=checksum.read_text(encoding="utf-8"), payload_manifest=expected,
                           release_zip_matches_local=True, release_files_match_current_source=True)
            destination = temporary / "skills"
            cache = temporary / "installer-temp"
            cache.mkdir()
            env = {**os.environ, "TMPDIR": str(cache), "PYTHONDONTWRITEBYTECODE": "1"}
            run(installed, [PYTHON, "-B", "-X", "utf8", str(INSTALLER), "--repo", REPO, "--ref", TAG,
                            "--method", "download", "--path", *["skills/" + name for name in package.SKILL_NAMES],
                            "--dest", str(destination)], env=env)
            actual = inventory(destination, package.SKILL_NAMES)
            if actual != expected or {path.name for path in destination.iterdir()} != set(package.SKILL_NAMES):
                raise ValueError("Official fixed-tag installation differs from Release and source")
            tool = destination / "story-skill/scripts/story.py"
            prefix = [PYTHON, "-B", "-X", "utf8", str(tool)]
            if run(installed, prefix + ["--version"], env=env).strip() != VERSION:
                raise ValueError("Installed runtime version differs")
            run(installed, prefix + ["--help"], env=env)
            book = temporary / "固定标签验证书"
            run(installed, prefix + ["init", "--book", str(book), "--title", "固定标签验证", "--kind", "long"], env=env)
            status = json.loads(run(installed, prefix + ["status", "--book", str(book)], env=env))
            if status["last_chapter"] != 0 or status["pending_export_count"] != 0 or status["sources"] != 0:
                raise ValueError("Fresh installed runtime produced unexpected book state")
            installed.update(ok=True, method="official installer --method download --ref v0.5.3",
                             release_url=metadata["html_url"], resolved_commit=obj["sha"], files=len(actual),
                             skill_count=len(package.SKILL_NAMES), payload_manifest=actual, runtime_checks=4,
                             release_zip_sha256=archive_sha, release_zip_matches_local=True,
                             installed_files_match_release_and_source=True)
        if dict(package.source_entries()) != expected_files or local_archive.read_bytes() != local_raw:
            raise ValueError("Local source or ZIP changed during remote verification")
        if inventory(GLOBAL_SKILLS, package.SKILL_NAMES) != global_before:
            raise ValueError("Global skill bytes changed during isolated verification")
        installed.update(global_skill_files_unchanged=True, source_and_local_zip_unchanged=True)
    except Exception as error:
        installed.update(ok=False, error={"type": type(error).__name__, "message": str(error)})
        if not release.get("release_files_match_current_source"):
            release.update(ok=False, error=installed["error"])
    finally:
        removed = temporary is not None and not temporary.exists()
        release["temporary_downloads_removed"] = removed
        installed["temporary_data_removed"] = removed
        if not removed:
            installed["ok"] = False
        for name, report in (("release", release), ("remote-install", installed)):
            path = targets[name] if report["ok"] else targets[name].with_name(targets[name].stem + ".failed.json")
            write_new(path, report)
    print(json.dumps({"ok": release["ok"] and installed["ok"], "release_download": release["ok"],
                      "remote_install": installed["ok"], "temporary_data_removed": removed}, ensure_ascii=False))
    return 0 if release["ok"] and installed["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
