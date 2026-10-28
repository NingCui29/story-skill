"""Run the complete local suite and bind the result to unchanged source files."""
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


def hashes():
    suffixes = {".py", ".mjs", ".js", ".md", ".json", ".swift", ".plist"}
    paths = [path for folder in ("skills", "scripts", "tests", "desktop", "docs")
             for path in (ROOT / folder).rglob("*")
             if path.is_file() and path.suffix in suffixes and "__pycache__" not in path.parts]
    paths += [ROOT / "README.md", ROOT / "benchmarks/instruction-loads.json", Path(__file__).resolve()]
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}


def main():
    os.chdir(ROOT)
    # Script execution puts this report directory on sys.path, unlike the
    # repository's `python -m unittest` command. Keep package fixture imports
    # (`tests.test_publish`, including lazy imports in worker threads) valid.
    sys.path.insert(0, str(ROOT))
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node must be on PATH so JS contract tests actually run")
    before = hashes()
    started = datetime.now(timezone.utc).isoformat()
    timer = time.monotonic()
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"))
    if loader.errors:
        (OUTPUT / "discovery-errors.json").write_text(json.dumps(loader.errors, indent=2) + "\n")
        raise RuntimeError("Test discovery failed; see discovery-errors.json")
    discovered = suite.countTestCases()
    with (OUTPUT / "full-suite.log").open("w", encoding="utf-8", buffering=1) as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2, failfast=True).run(suite)
    after = hashes()
    drift = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    report = {"started_at_utc": started, "python": sys.version, "platform": platform.platform(),
              "node": subprocess.run([node, "--version"], check=True, capture_output=True, text=True).stdout.strip(),
              "tests_discovered": discovered, "tests_run": result.testsRun,
              "passed": result.testsRun - len(result.skipped) - len(result.failures) - len(result.errors),
              "skipped": [{"test": test.id(), "reason": reason} for test, reason in result.skipped],
              "failures": [{"test": test.id(), "traceback": details} for test, details in result.failures],
              "errors": [{"test": test.id(), "traceback": details} for test, details in result.errors],
              "elapsed_seconds": round(time.monotonic() - timer, 2), "source_changed_during_run": drift,
              "successful": result.wasSuccessful() and result.testsRun == discovered and not drift, "failfast": True}
    for name, value in (("full-suite.json", report), ("validated-source-hashes.json", after)):
        (OUTPUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**{key: report[key] for key in ("tests_run", "passed", "elapsed_seconds", "successful")},
                      "skipped": len(result.skipped), "failures": len(result.failures), "errors": len(result.errors),
                      "source_changed_during_run": drift}, ensure_ascii=False), flush=True)
    return 0 if report["successful"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
