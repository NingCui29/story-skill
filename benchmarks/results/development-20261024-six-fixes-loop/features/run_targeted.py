"""Bounded feature regressions; no live account, manuscript or installation."""
from pathlib import Path
import hashlib
import json
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))
MODULES = [
    "test_rank_source_template_scope", "test_rank_source_visibility", "test_rank_source_markup",
    "test_rank_sources", "test_rank", "test_rank_compatibility", "test_research", "test_research_long_reasons",
    "test_instruction_budget", "test_skill_audit_fixes", "test_history_state_scope", "test_history_saved_read",
    "test_plan_outline_sync", "test_outline_example_boundaries", "test_short_assembly_history",
    "test_analysis_read_snapshots", "test_world_rules_regressions", "test_long_storage",
]
paths = ["skills/story-skill/scripts/" + name for name in (
    "story.py", "story_rank_sources.py", "story_rank.py", "story_research.py", "story_history.py",
    "story_outline.py", "story_search.py", "story_storage.py", "story_world.py")]
def hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}
before = hashes()
loader = unittest.TestLoader()
suite = loader.loadTestsFromNames(MODULES)
started = time.monotonic()
with (OUT / "targeted-suite.log").open("w") as log:
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
after = hashes()
summary = {
    "successful": result.wasSuccessful() and not loader.errors and before == after,
    "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
    "skipped": len(result.skipped), "modules": MODULES, "loader_errors": loader.errors,
    "duration_seconds": round(time.monotonic() - started, 2), "source_sha256": after,
    "source_unchanged_during_run": before == after,
}
(OUT / "targeted-suite.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(summary, ensure_ascii=False))
raise SystemExit(0 if summary["successful"] else 1)
