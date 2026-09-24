#!/usr/bin/env python3
"""Mirror a published Release ZIP to GitHub npm; verify the downloaded package."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import package_npm

REPOSITORY = "NingCui29/story-skill"
API = "https://api.github.com/repos/" + REPOSITORY
REGISTRY = "https://npm.pkg.github.com"
NAME = package_npm.NAME
MAX_BYTES = 20 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_url(url, token=None, auth_host=None):
    """Keep credentials on their original registry/API host during redirects."""
    opener = urllib.request.build_opener(NoRedirect)
    for _ in range(6):
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or parsed.username or parsed.password or
                not (host in {"github.com", "api.github.com", "npm.pkg.github.com"}
                     or host.endswith(".githubusercontent.com"))):
            raise ValueError("Unexpected download host")
        headers = {"User-Agent": "story-skill-packages-sync"}
        if token and host == auth_host:
            headers["Authorization"] = "Bearer " + token
        if host == "api.github.com":
            headers.update(Accept="application/vnd.github+json",
                           **{"X-GitHub-Api-Version": "2026-03-10"})
        try:
            with opener.open(urllib.request.Request(url, headers=headers), timeout=45) as response:
                data = response.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise ValueError("Download exceeds the package size budget")
                return data
        except urllib.error.HTTPError as error:
            if error.code not in (301, 302, 303, 307, 308):
                raise
            url = urllib.parse.urljoin(url, error.headers["Location"])
            error.close()
    raise ValueError("Too many download redirects")


def read_json(url, token=None, auth_host=None):
    return json.loads(read_url(url, token, auth_host))


def version_from_tag(tag):
    if not re.fullmatch(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", tag):
        raise ValueError("tag must be a stable version such as v0.6.0")
    return tag[1:]


def release_files(tag, output, token=None):
    version = version_from_tag(tag)
    release = read_json(API + "/releases/tags/" + tag, token, "api.github.com")
    if release["draft"] or release["prerelease"] or release["tag_name"] != tag:
        raise ValueError("Only a published stable release can be mirrored")
    names = [f"story-skill-{version}.zip", f"story-skill-{version}.zip.sha256"]
    paths = []
    output.mkdir(parents=True, exist_ok=True)
    for name in names:
        assets = [asset for asset in release["assets"] if asset["name"] == name]
        if len(assets) != 1 or assets[0]["state"] != "uploaded":
            raise ValueError("Release needs one uploaded ZIP and its checksum file")
        asset = assets[0]
        expected_url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"
        if asset["browser_download_url"] != expected_url:
            raise ValueError("Release asset URL does not match its repository and tag")
        raw = read_url(expected_url)
        if len(raw) != asset["size"]:
            raise ValueError("Release asset size changed")
        if asset.get("digest") and asset["digest"] != "sha256:" + hashlib.sha256(raw).hexdigest():
            raise ValueError("Release asset does not match its server digest")
        path = output / name
        path.write_bytes(raw)
        paths.append(path)
    return release, paths[0], paths[1]


def npm_command(arguments, env):
    # Use the same cross-platform, shell-free npm discovery as the builder.
    return subprocess.run(package_npm.npm_command() + list(arguments), env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=180)


def registry_version(version, token):
    try:
        metadata = read_json(REGISTRY + "/" + NAME.replace("/", "%2f"), token, "npm.pkg.github.com")
    except urllib.error.HTTPError as error:
        if error.code == 404:
            error.close()
            return None
        raise
    return metadata.get("versions", {}).get(version)


def verify_download(metadata, built, archive, checksum, output, token):
    expected_name, expected_repo = package_npm.package_identity(built["version"])
    if metadata.get("name") != expected_name or metadata.get("version") != built["version"]:
        raise ValueError("Registry returned another package or version")
    repo = metadata.get("repository", {})
    repo_url = repo.get("url") if isinstance(repo, dict) else repo
    if repo_url not in (expected_repo, "git+" + expected_repo):
        raise ValueError("Registry package is associated with another repository")
    dist = metadata["dist"]
    if urllib.parse.urlsplit(dist["tarball"]).hostname != "npm.pkg.github.com":
        raise ValueError("Package tarball is outside the GitHub npm registry")
    raw = read_url(dist["tarball"], token, "npm.pkg.github.com")
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(raw).digest()).decode("ascii")
    if integrity not in dist.get("integrity", "").split():
        raise ValueError("Registry tarball failed its SHA-512 integrity check")
    target = output / "downloaded-package.tgz"
    target.write_bytes(raw)
    verified = package_npm.verify_tarball(archive, checksum, target, built)
    return target, verified, integrity


def runtime_smoke(tarball, version):
    """Execute only after verify_tarball has matched all files to the Release."""
    with tempfile.TemporaryDirectory(prefix="story-npm-runtime-") as folder:
        root = Path(folder)
        expected = set(package_npm.payload_files(version))
        seen = set()
        with tarfile.open(tarball, "r:gz") as bundle:
            for member in bundle.getmembers():
                if member.name in {"package/package.json", "package/README.md"}:
                    continue
                if member.name.startswith("package/"):
                    name = member.name[len("package/"):]
                    if name not in expected or name in seen or not member.isfile():
                        raise ValueError("Runtime smoke received an unverified skill member")
                    seen.add(name)
                    relative = Path(name)
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(bundle.extractfile(member).read())
                else:
                    raise ValueError("Runtime smoke received an unexpected package root")
        if seen != expected:
            raise ValueError("Runtime smoke is missing required skill dependencies")
        skills = sorted({name.split("/", 1)[0] for name in expected})
        # Local Markdown links and task runtime paths must survive sibling installation.
        for name in skills:
            entry = root / name / "SKILL.md"
            content = entry.read_text(encoding="utf-8-sig")
            for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", content):
                link = link.split("#", 1)[0]
                if not link or "://" in link:
                    continue
                destination = (entry.parent / link).resolve()
                destination.relative_to(root.resolve())
                if not destination.is_file():
                    raise ValueError(f"Installed skill has a broken local dependency: {name}: {link}")
            if name != "story-skill" and not (entry.parent / "../story-skill/scripts/story.py").is_file():
                raise ValueError(f"Installed task skill has no shared runtime: {name}")
        tool = root / "story-skill/scripts/story.py"
        prefix = [sys.executable, "-B", "-X", "utf8", str(tool)]
        book = root / "book"
        commands = [["--version"], ["--help"],
                    ["init", "--book", str(book), "--title", "Package verification", "--kind", "long"],
                    ["status", "--book", str(book)]]
        for index, arguments in enumerate(commands):
            process = subprocess.run(prefix + arguments, capture_output=True, text=True,
                                     encoding="utf-8", timeout=60)
            if process.returncode:
                raise ValueError("Downloaded package runtime verification failed")
            if index == 0 and process.stdout.strip() != version:
                raise ValueError("Installed runtime version differs from its npm version")
            if index == 3 and json.loads(process.stdout)["last_chapter"] != 0:
                raise ValueError("New package smoke book has unexpected state")
    return {"ok": True, "commands": 4, "skills": skills, "skill_files": len(seen),
            "temporary_book_removed": not root.exists()}


def sync(tag, output, prepare_only=False):
    version = version_from_tag(tag)
    expected_name, _ = package_npm.package_identity(version)
    if os.environ.get("GITHUB_REPOSITORY", REPOSITORY).lower() != REPOSITORY.lower():
        raise ValueError("Publishing is restricted to the linked repository")
    token = os.environ.get("NODE_AUTH_TOKEN")
    if not prepare_only and not token:
        raise ValueError("NODE_AUTH_TOKEN with GitHub Packages access is required")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    release, archive, checksum = release_files(tag, output / "release", token)
    built = package_npm.build(archive, checksum, output / "build")
    if built["name"] != expected_name or built["version"] != version:
        raise ValueError("Release payload and requested package version disagree")
    report = {"ok": False, "repository": REPOSITORY, "tag": tag, "release_url": release["html_url"],
              "package": built, "prepare_only": prepare_only}
    if prepare_only:
        report.update(ok=True, runtime=runtime_smoke(Path(built["tarball"]), version))
        return report
    with tempfile.TemporaryDirectory(prefix="story-npm-auth-") as folder:
        config = Path(folder) / ".npmrc"
        config.write_text(NAME.split("/", 1)[0] + ":registry=https://npm.pkg.github.com\n"
                          "//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}\n", encoding="utf-8")
        env = {**os.environ, "NPM_CONFIG_USERCONFIG": str(config),
               "NPM_CONFIG_CACHE": str(Path(folder) / "cache")}
        who = npm_command(["whoami", "--json", "--registry", REGISTRY], env)
        if who.returncode:
            raise ValueError("GitHub npm authentication failed before publication")
        report["registry_identity"] = json.loads(who.stdout)
        existing = registry_version(version, token)
        report["already_published"] = existing is not None
        if existing is None:
            try:
                published = npm_command(["publish", built["tarball"], "--registry", REGISTRY,
                                         "--ignore-scripts", "--access", "public"], env)
            except subprocess.TimeoutExpired:
                published = subprocess.CompletedProcess([], 124, "", "npm publish timed out; registry rechecked")
            # Read back after either success or an uncertain publish result; never blindly republish.
            for pause in (0, 2, 5):
                if pause:
                    time.sleep(pause)
                existing = registry_version(version, token)
                if existing is not None:
                    break
            if existing is None:
                detail = (published.stderr or published.stdout).replace(token, "[redacted]")[-1800:]
                raise ValueError("Package publication did not become visible: " + detail)
        target, verified, integrity = verify_download(existing, built, archive, checksum, output, token)
        report.update(ok=True, downloaded_integrity=integrity, downloaded_package=verified,
                      runtime=runtime_smoke(target, version))
        info = read_json("https://api.github.com/users/" + REPOSITORY.split("/", 1)[0] +
                         "/packages/npm/story-skill", token, "api.github.com")
        linked = info.get("repository", {}).get("full_name", "")
        if linked.lower() != REPOSITORY.lower():
            raise ValueError("Published package is not linked to the expected repository")
        report.update(package_url=info["html_url"], visibility=info["visibility"],
                      linked_repository=linked)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output-dir", default="dist/packages-sync")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    try:
        report = sync(args.tag, args.output_dir, args.prepare_only)
    except Exception as error:
        token = os.environ.get("NODE_AUTH_TOKEN")
        message = str(error).replace(token, "[redacted]") if token else str(error)
        report = {"ok": False, "tag": args.tag, "error_type": type(error).__name__, "error": message[:2000]}
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    path = output / ("prepared.json" if args.prepare_only else "receipt.json")
    if not report["ok"] and path.exists():
        path = path.with_name(path.stem + ".failed.json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "report": str(path), "package_url": report.get("package_url"),
                      "error": report.get("error")}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
