#!/usr/bin/env python3
"""Verify a local Codex plugin using a disposable Codex home; never start a model turn."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import queue
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit
from urllib.request import ProxyHandler, build_opener


ROOT = Path(__file__).resolve().parents[1]
SKILL_NAMES = (
    "story-skill", "story-skill-analyze", "story-skill-cover", "story-skill-plan",
    "story-skill-publish", "story-skill-research", "story-skill-review", "story-skill-write",
)
PLUGIN_NAME = "story-skill"
MARKETPLACE_NAME = "story-skill-local"
PYTHON = [sys.executable, "-B", "-X", "utf8"]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inventory(root, source=False):
    """Reject links and special files so hashes describe an ordinary self-contained tree."""
    root = Path(root)
    files = {}
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"Expected an ordinary directory: {root}")
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if source and ("__pycache__" in relative.parts or path.suffix == ".pyc"):
            continue
        attributes = path.lstat()
        if stat.S_ISLNK(attributes.st_mode) or (getattr(attributes, "st_file_attributes", 0) & 0x400):
            raise ValueError(f"Linked plugin content: {relative}")
        if stat.S_ISREG(attributes.st_mode):
            files[relative.as_posix()] = digest(path)
        elif not stat.S_ISDIR(attributes.st_mode):
            raise ValueError(f"Non-regular plugin content: {relative}")
    return files


def compare_files(expected, actual):
    missing = sorted(expected.keys() - actual.keys())
    extra = sorted(actual.keys() - expected.keys())
    changed = sorted(name for name in expected.keys() & actual.keys() if expected[name] != actual[name])
    return {"status": "passed" if not (missing or extra or changed) else "failed",
            "files": len(actual), "missing": missing, "extra": extra, "changed": changed}


def expected_source_payload():
    """Use the builder's bounded UI adaptation instead of assuming raw source UI bytes."""
    spec = importlib.util.spec_from_file_location("verify_codex_package", ROOT / "scripts/package_codex.py")
    package = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package)
    source = package.suite.source_entries()
    adapted = package.plugin_skill_entries(source)
    original = dict(source)
    adapted_files = sorted(name for name, raw in adapted if raw != original[name])
    expected_ui = sorted(f"{name}/agents/openai.yaml" for name in SKILL_NAMES)
    require(adapted_files == expected_ui, "Expected exactly eight namespaced plugin UI adaptations")
    return {name: hashlib.sha256(raw).hexdigest() for name, raw in adapted}, adapted_files


def local_markdown_links(plugin):
    """Check local inline and reference links, including sibling-skill dependencies."""
    plugin = Path(plugin).resolve()
    inline = re.compile(r"!?\[[^\]\n]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+[\"'][^\n]*[\"'])?\s*\)")
    reference = re.compile(r"^\s{0,3}\[[^]\n]+\]:\s*(?:<([^>]+)>|(\S+))", re.MULTILINE)
    checked, missing, escaped = [], [], []
    documents = sorted((plugin / "skills").rglob("*.md"))
    for path in documents:
        content = path.read_text(encoding="utf-8-sig")
        content = re.sub(r"^\s*(`{3,}|~{3,}).*?^\s*\1\s*$", "", content,
                         flags=re.MULTILINE | re.DOTALL)
        content = re.sub(r"`[^`\n]*`", "", content)
        for pattern in (inline, reference):
            for match in pattern.finditer(content):
                target = match.group(1) or match.group(2)
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                resolved = (path.parent / unquote(parsed.path)).resolve()
                item = {"source": path.relative_to(plugin).as_posix(), "target": target}
                checked.append(item)
                try:
                    resolved.relative_to(plugin)
                except ValueError:
                    escaped.append(item)
                if not resolved.exists():
                    missing.append(item)
    return {"status": "passed" if not (missing or escaped) else "failed",
            "files_scanned": len(documents), "links_checked": len(checked),
            "missing": missing, "outside_plugin": escaped,
            "scope": "Local inline/reference Markdown file targets; remote URLs and heading anchors are excluded"}


def run(command, cwd, env, timeout=30):
    started = time.monotonic()
    result = subprocess.run(list(map(str, command)), cwd=cwd, env=env, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=timeout)
    evidence = {"command": list(map(str, command)), "exit_code": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr,
                "duration_seconds": round(time.monotonic() - started, 3),
                "status": "passed" if result.returncode == 0 else "failed"}
    if result.returncode:
        raise VerificationFailure("Command failed", evidence)
    return evidence


class VerificationFailure(Exception):
    def __init__(self, message, evidence=None):
        super().__init__(message)
        self.evidence = evidence


def require(condition, message):
    if not condition:
        raise VerificationFailure(message)


def installed_plugin(codex_home):
    """Locate only the cache created inside this verifier's new, isolated home."""
    candidates = []
    for manifest in (codex_home / "plugins/cache").rglob(".codex-plugin/plugin.json"):
        if json.loads(manifest.read_text(encoding="utf-8")).get("name") == PLUGIN_NAME:
            candidates.append(manifest.parent.parent.resolve())
    require(len(candidates) == 1, f"Expected one isolated installed plugin; found {len(candidates)}")
    candidates[0].relative_to(codex_home.resolve())
    return candidates[0]


def validate_ui_prompts(metadata):
    """Match the default prompt returned by Codex to each discovered namespaced skill."""
    expected_names = {f"{PLUGIN_NAME}:{name}" for name in SKILL_NAMES}
    evidence = {}
    for item in metadata:
        prompt = (item.get("interface") or {}).get("defaultPrompt")
        require(isinstance(prompt, str) and bool(prompt.strip()),
                f"Codex did not parse defaultPrompt for {item['name']}")
        references = re.findall(r"\$(story-skill[A-Za-z0-9_:-]*)", prompt)
        require(item["name"] in references and all(name in expected_names for name in references),
                f"Default prompt has a missing or unnamespaced skill reference: {item['name']}")
        evidence[item["name"]] = ["$" + name for name in references]
    return {"status": "passed", "adapted_ui_files": len(metadata), "references": evidence,
            "scope": "Codex-parsed UI defaultPrompt references; no model routing turn was started"}


def skill_discovery(codex, plugin, cwd, env):
    """Ask the local app-server for skill metadata without creating a thread or model turn."""
    messages = queue.Queue()
    notifications = []
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
        process = subprocess.Popen([codex, "app-server", "--stdio"], cwd=cwd, env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                                   text=True, encoding="utf-8", errors="replace", bufsize=1)
        def reader():
            for line in process.stdout:
                messages.put(line)
            messages.put(None)
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()

        def request(number, method, params):
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": number,
                                             "method": method, "params": params}) + "\n")
            process.stdin.flush()
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                try:
                    line = messages.get(timeout=max(0.01, deadline - time.monotonic()))
                except queue.Empty:
                    break
                if line is None:
                    raise VerificationFailure(f"App-server exited before {method}")
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if item.get("id") == number:
                    require("error" not in item, f"App-server {method} error: {item.get('error')}")
                    return item["result"]
                if "method" in item:
                    notifications.append(item["method"])
            raise VerificationFailure(f"Timed out waiting for app-server {method}")

        try:
            initialization = request(1, "initialize", {"clientInfo": {
                "name": "story-skill-local-verifier", "version": "1"}})
            process.stdin.write('{"jsonrpc":"2.0","method":"initialized","params":{}}\n')
            process.stdin.flush()
            result = request(2, "skills/list", {"cwds": [str(cwd)], "forceReload": True})
            entries = result.get("data", [])
            require(len(entries) == 1 and entries[0].get("cwd") == str(cwd),
                    "App-server did not return the isolated working directory")
            metadata = []
            for item in entries[0].get("skills", []):
                try:
                    Path(item["path"]).resolve().relative_to(plugin)
                except (ValueError, KeyError):
                    continue
                metadata.append(item)
            require(not entries[0].get("errors"), f"Skill discovery errors: {entries[0].get('errors')}")
            expected_names = {f"{PLUGIN_NAME}:{name}" for name in SKILL_NAMES}
            expected_paths = {str((plugin / "skills" / name / "SKILL.md").resolve()) for name in SKILL_NAMES}
            if ({item["name"] for item in metadata} != expected_names or
                    {str(Path(item["path"]).resolve()) for item in metadata} != expected_paths):
                raise VerificationFailure("App-server did not discover all eight installed plugin skills",
                                          {"installed_plugin": str(plugin), "skills": entries[0].get("skills", []),
                                           "errors": entries[0].get("errors", [])})
            require(len(metadata) == len(SKILL_NAMES) and all(item.get("enabled") for item in metadata),
                    "Installed plugin skills were duplicated or disabled")
            require(all(item.get("pluginId") == f"{PLUGIN_NAME}@{MARKETPLACE_NAME}" for item in metadata),
                    "Discovered skills did not identify the installed plugin as their owner")
            ui_prompts = validate_ui_prompts(metadata)
            return {"status": "passed", "method": "skills/list", "skills": metadata,
                    "errors": entries[0].get("errors", []), "initialization": initialization,
                    "notification_methods": notifications,
                    "ui_default_prompts": ui_prompts,
                    "model_turn_started": False}
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
            thread.join(timeout=1)


def workbench_smoke(tool, book, cwd, env):
    """Start a foreground service, request its loopback HTML, then stop that exact service."""
    command = PYTHON + [str(tool), "workbench-serve", "--book", str(book)]
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as logs:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=logs, stderr=logs,
                                   text=True, encoding="utf-8")
        status, stopped, final = None, None, None
        try:
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                status = run(PYTHON + [str(tool), "workbench-status", "--book", str(book)], cwd, env)
                state = json.loads(status["stdout"])
                if state.get("running") is True:
                    break
                require(process.poll() is None, "Workbench exited before its service became ready")
                time.sleep(0.1)
            require(state.get("running") is True, "Workbench failed to report a running service")
            require(Path(state["root"]).resolve() == book.resolve(), "Workbench reported a different book root")
            require(not state.get("outdated"), "Workbench did not load the installed runtime bytes")
            url = state["url"]
            parsed = urlsplit(url)
            require(parsed.scheme == "http" and parsed.hostname == "127.0.0.1",
                    "Workbench did not provide a loopback HTTP URL")
            with build_opener(ProxyHandler({})).open(url, timeout=5) as response:
                html = response.read(2_000_001)
                http_status, content_type = response.status, response.headers.get("Content-Type")
            require(http_status == 200 and b"<html" in html.lower() and len(html) <= 2_000_000,
                    "Workbench did not serve its HTML document")
            stopped = run(PYTHON + [str(tool), "workbench-stop", "--book", str(book), "--saved"], cwd, env)
            process.wait(timeout=8)
            final = run(PYTHON + [str(tool), "workbench-status", "--book", str(book)], cwd, env)
            require(json.loads(final["stdout"]).get("running") is False,
                    "Workbench did not confirm service shutdown")
            logs.seek(0)
            return {"status": "passed", "start_command": command, "running": status,
                    "http": {"status": http_status, "content_type": content_type,
                             "bytes": len(html), "sha256": hashlib.sha256(html).hexdigest()},
                    "stop": stopped, "final_status": final, "process_exit_code": process.returncode,
                    "log": logs.read(), "scope": "Loopback HTTP only; no browser or GUI interaction"}
        finally:
            if process.poll() is None:
                try:
                    run(PYTHON + [str(tool), "workbench-stop", "--book", str(book), "--saved"], cwd, env)
                    process.wait(timeout=5)
                except (VerificationFailure, subprocess.TimeoutExpired, OSError):
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


def verify(marketplace_root):
    report = {"schema": 1, "date": datetime.now(timezone.utc).isoformat(), "ok": False,
              "environment": {"platform": platform.platform(), "python": platform.python_version()},
              "isolation": {"temporary_codex_home": True, "temporary_working_directory": True,
                            "real_user_config_modified": False, "model_turn_started": False},
              "not_verified": ["Codex desktop GUI loading and interaction",
                               "Model skill selection, prose quality, or platform acceptance"]}
    checks = report["checks"] = {}
    try:
        marketplace_root = Path(marketplace_root).expanduser().resolve()
        marketplace_before = inventory(marketplace_root)
        plugin_source = marketplace_root / "plugins" / PLUGIN_NAME
        expected = inventory(plugin_source)
        source, adapted_ui_files = expected_source_payload()
        packaged = {name[len("skills/"):]: value for name, value in expected.items() if name.startswith("skills/")}
        checks["current_source_payload"] = compare_files(source, packaged)
        checks["current_source_payload"]["adapted_ui_files"] = adapted_ui_files
        checks["current_source_payload"]["adapted_ui_file_count"] = len(adapted_ui_files)
        require(checks["current_source_payload"]["status"] == "passed", "Packaged skills differ from current source")
        checks["source_payload_files"] = len(source)
        codex = shutil.which("codex")
        require(codex is not None, "Codex CLI is unavailable; install it before running this verifier")
        with tempfile.TemporaryDirectory(prefix="story-codex-plugin-verify-") as temporary:
            temporary = Path(temporary).resolve()
            codex_home, cwd = temporary / "codex-home", temporary / "workspace"
            codex_home.mkdir()
            cwd.mkdir()
            env = dict(os.environ, CODEX_HOME=str(codex_home), PYTHONDONTWRITEBYTECODE="1")
            checks["codex_version"] = run([codex, "--version"], cwd, env)
            for label, arguments in (
                ("marketplace_add", ["plugin", "marketplace", "add", str(marketplace_root), "--json"]),
                ("available_plugins", ["plugin", "list", "--available", "--json"]),
                ("plugin_add", ["plugin", "add", f"{PLUGIN_NAME}@{MARKETPLACE_NAME}", "--json"]),
                ("installed_plugins", ["plugin", "list", "--json"]),
            ):
                checks[label] = run([codex] + arguments, cwd, env)
                checks[label]["json"] = json.loads(checks[label]["stdout"])
            require(checks["marketplace_add"]["json"].get("marketplaceName") == MARKETPLACE_NAME,
                    "Codex registered an unexpected marketplace name")
            selector = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
            available = [item for item in checks["available_plugins"]["json"].get("available", [])
                         if item.get("pluginId") == selector]
            require(len(available) == 1 and available[0].get("installed") is False,
                    "Codex did not list the expected uninstalled marketplace plugin")
            installed = [item for item in checks["installed_plugins"]["json"].get("installed", [])
                         if item.get("pluginId") == selector]
            require(len(installed) == 1 and installed[0].get("installed") is True and
                    installed[0].get("enabled") is True,
                    "Codex did not report the installed plugin as enabled")
            plugin = installed_plugin(codex_home)
            receipt = checks["plugin_add"]["json"]
            require(receipt.get("pluginId") == selector and
                    Path(receipt["installedPath"]).resolve() == plugin,
                    "Codex installation receipt does not match its actual plugin cache")
            actual = inventory(plugin)
            checks["installed_bytes"] = compare_files(expected, actual)
            require(checks["installed_bytes"]["status"] == "passed", "Installed plugin bytes differ from the bundle")
            names = sorted(path.parent.name for path in (plugin / "skills").glob("*/SKILL.md"))
            require(names == sorted(SKILL_NAMES), "Installed plugin does not contain exactly eight skill entries")
            checks["skill_entries"] = {"status": "passed", "names": names, "count": len(names)}
            checks["markdown_dependencies"] = local_markdown_links(plugin)
            require(checks["markdown_dependencies"]["status"] == "passed", "Installed Markdown dependencies are missing or escape the plugin")
            checks["app_server_skill_discovery"] = skill_discovery(codex, plugin, cwd, env)
            tool = plugin / "skills/story-skill/scripts/story.py"
            require(tool.is_file(), "Installed shared runtime is missing")
            book = temporary / "book"
            runtime = checks["runtime"] = {}
            for label, arguments in (("version", ["--version"]), ("help", ["--help"]),
                                     ("init", ["init", "--book", str(book), "--title", "Codex 插件隔离验收"]),
                                     ("status", ["status", "--book", str(book)]),
                                     ("audit", ["audit", "--book", str(book)])):
                runtime[label] = run(PYTHON + [str(tool)] + arguments, cwd, env)
            version = json.loads((plugin / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
            require(version in runtime["version"]["stdout"] and receipt.get("version") == version,
                    "Runtime, plugin metadata, and Codex installation versions differ")
            state = json.loads(runtime["status"]["stdout"])
            require(Path(state["book"]).resolve() == book.resolve() and state["last_chapter"] == 0,
                    "Runtime smoke did not operate on the isolated empty book")
            runtime["health_check_command"] = "audit (this Story Skill version has no doctor subcommand)"
            checks["workbench"] = workbench_smoke(tool, book, cwd, env)
            checks["plugin_unchanged_after_runtime"] = compare_files(actual, inventory(plugin))
            require(checks["plugin_unchanged_after_runtime"]["status"] == "passed", "Runtime smoke modified the installed plugin")
            require(not list(cwd.rglob(".story")) and not list(cwd.rglob(".active-book")),
                    "Runtime wrote book state in the working directory")
            require((book / ".story/state.sqlite3").is_file(), "Runtime did not create its database in the explicit book directory")
            checks["book_state_location"] = {"status": "passed", "explicit_book_argument": True,
                                             "database_exists": True,
                                             "cwd_book_state_created": False}
        checks["marketplace_unchanged"] = compare_files(marketplace_before, inventory(marketplace_root))
        require(checks["marketplace_unchanged"]["status"] == "passed", "Verification modified the input marketplace")
        report["ok"] = True
    except (VerificationFailure, ValueError, OSError, subprocess.TimeoutExpired, KeyError) as error:
        report["error"] = str(error)
        if isinstance(error, VerificationFailure) and error.evidence is not None:
            report["failed_command"] = error.evidence
    return report


def report_path(output):
    output = Path(output).expanduser().absolute()
    current = Path(output.anchor)
    for part in output.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError(f"Refusing linked report output path: {current}")
        try:
            attributes = current.lstat()
        except FileNotFoundError:
            continue
        if getattr(attributes, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Refusing linked report output path: {current}")
    return output


def write_report(report, output):
    output = report_path(output)
    if not report["ok"] and output.exists():
        output = report_path(output.with_name(output.stem + ".failed" + output.suffix))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                     prefix=".codex-plugin-verification-", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    try:
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--marketplace-root", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if sys.version_info < (3, 10):
        parser.error("Python 3.10 or newer is required for Story Skill plugin verification")
    report = verify(args.marketplace_root)
    if args.report:
        path = write_report(report, args.report)
        print(json.dumps({"ok": report["ok"], "report": str(path), "error": report.get("error")}, ensure_ascii=False))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
