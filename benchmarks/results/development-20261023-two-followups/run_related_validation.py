"""Validate backend result handling for the two follow-up fixes, offline."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).resolve().parent
MODULES = (
    "test_platform_drafts", "test_platform_draft_equivalence",
    "test_platform_browser_batch", "test_workbench_platform_drafts",
    "test_platform_draft_auto_connect", "test_workbench_draft_preferences",
    "test_instruction_budget",
)


def hashes():
    suffixes = {".py", ".mjs", ".js", ".md", ".json", ".swift", ".plist"}
    paths = [path for folder in ("skills", "scripts", "tests", "desktop", "docs")
             for path in (ROOT / folder).rglob("*")
             if path.is_file() and path.suffix in suffixes and "__pycache__" not in path.parts]
    paths += [ROOT / "README.md", ROOT / "benchmarks/instruction-loads.json"]
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


def main():
    os.chdir(ROOT)
    sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node must be available for related runtime checks")
    before = hashes()
    started = datetime.now(timezone.utc).isoformat()
    timer = time.monotonic()
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(MODULES)
    if loader.errors:
        raise RuntimeError("Test loading failed: " + "\n".join(loader.errors))
    discovered = suite.countTestCases()
    with (OUTPUT / "backend-related.log").open("w", encoding="utf-8", buffering=1) as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    after = hashes()
    drift = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    report = {
        "scope": "Complete affected backend modules; not the full repository suite",
        "modules": MODULES, "started_at_utc": started, "python": sys.version,
        "platform": platform.platform(),
        "node": subprocess.run([node, "--version"], check=True, capture_output=True, text=True).stdout.strip(),
        "tests_discovered": discovered, "tests_run": result.testsRun,
        "passed": result.testsRun - len(result.skipped) - len(result.failures) - len(result.errors),
        "skipped": [{"test": test.id(), "reason": reason} for test, reason in result.skipped],
        "failures": [{"test": test.id(), "traceback": details} for test, details in result.failures],
        "errors": [{"test": test.id(), "traceback": details} for test, details in result.errors],
        "elapsed_seconds": round(time.monotonic() - timer, 2), "source_changed_during_run": drift,
        "successful": result.wasSuccessful() and result.testsRun == discovered and not drift,
    }
    for name, value in (("backend-related.json", report), ("validated-source-hashes.json", after)):
        (OUTPUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**{key: report[key] for key in ("tests_run", "passed", "elapsed_seconds", "successful")},
                      "skipped": len(result.skipped), "failures": len(result.failures), "errors": len(result.errors),
                      "source_changed_during_run": drift}, ensure_ascii=False), flush=True)
    return 0 if report["successful"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
