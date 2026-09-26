from contextlib import ExitStack
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location("test_release_" + name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration, upgrade = load("migrate_probe"), load("upgrade_probe")
scaling = load("scale_probe")


class ProbeOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-probe-output-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.skill = self.root / "skills/story-skill"
        (self.skill / "scripts").mkdir(parents=True)
        self.writer = load("verify")
        self.package = self.writer.package_module()
        self.probes = ((scaling, "load_module", "scaling.json"),
                       (upgrade, "load_script", "upgrade.json"),
                       (migration, "load_runtime", "migration.json"))

    def run_main(self, module, loader, version, output=None, ok=True):
        (self.skill / "scripts/story.py").write_text(f'VERSION = "{version}"\n', encoding="utf-8")
        arguments = [module.__file__] + (["--output", str(output)] if output is not None else [])
        result = {"ok": ok, "fixture_version": version, "mode": "fixture", "books": [], "cases": []}
        with ExitStack() as stack:
            for target, attribute, value in ((module, "ROOT", self.root), (self.writer, "ROOT", self.root),
                                              (self.writer, "SKILL", self.skill),
                                              (migration.story, "VERSION", version), (sys, "argv", arguments)):
                stack.enter_context(patch.object(target, attribute, value))
            stack.enter_context(patch.object(self.writer, "package_module", return_value=self.package))
            stack.enter_context(patch.object(module, loader, return_value=self.writer))
            probe = stack.enter_context(patch.object(module, "probe", return_value=result))
            stack.enter_context(patch("sys.stdout", new=io.StringIO()))
            code = module.main()
            probe.assert_called_once()
        self.assertEqual(code, 0 if ok else 1)

    def test_defaults_follow_runtime_version_and_keep_previous_report(self):
        for module, loader, filename in self.probes:
            for version in ("0.6.0", "0.6.1"):
                with self.subTest(probe=filename, version=version):
                    self.run_main(module, loader, version)
            for version in ("0.6.0", "0.6.1"):
                path = self.root / "benchmarks/results" / f"v{version}" / filename
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["fixture_version"], version)

    def test_explicit_output_does_not_create_default_directory(self):
        for module, loader, filename in self.probes:
            with self.subTest(probe=filename):
                path = self.root / "custom" / filename
                self.run_main(module, loader, "0.6.0", path)
                self.assertTrue(path.is_file())
        self.assertFalse((self.root / "benchmarks/results/v0.6.0").exists())

    def test_failed_default_run_preserves_current_success(self):
        for module, loader, filename in self.probes:
            with self.subTest(probe=filename):
                self.run_main(module, loader, "0.6.0")
                path = self.root / "benchmarks/results/v0.6.0" / filename
                original = path.read_bytes()
                self.run_main(module, loader, "0.6.0", ok=False)
                self.assertEqual(path.read_bytes(), original)
                self.assertFalse(json.loads(path.with_suffix(".failed.json").read_text(encoding="utf-8"))["ok"])


class ReleaseProbeTests(unittest.TestCase):
    def test_generated_schema1_books_cover_native_baseline_and_analysis_rollback(self):
        report = migration.probe()
        self.assertTrue(report["ok"], report.get("error"))
        self.assertEqual(report["mode"], "generated-schema1-fixtures")
        self.assertIn("not retained historical", report["scope"])
        self.assertEqual({book["kind"] for book in report["books"]}, {"long", "short", "analysis"})
        self.assertTrue(report["original_tree_unchanged"])
        self.assertTrue(report["runtime_stable"])
        self.assertTrue(all(book["source_and_analysis_preserved"] and
                            book["old_runtime_rollback_copy_verified"] for book in report["books"]))

    def test_missing_retained_database_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory(prefix="story-probe-source-") as directory:
            source = Path(directory).resolve()
            (source / migration.RETAINED_NAMES[0]).mkdir()
            report = migration.probe(source)
        self.assertFalse(report["ok"])
        self.assertEqual(report["mode"], "retained-source")
        self.assertEqual(report["books"], [])
        self.assertIn("Source database unavailable", report["error"]["message"])

    def test_changed_schema1_runtime_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory(prefix="story-probe-runtime-") as directory:
            fixture = Path(directory) / "changed.py"
            fixture.write_bytes(b"untrusted replacement")
            with patch.object(migration, "SCHEMA1_RUNTIME", fixture):
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    migration.legacy_runtime(Path(directory) / "old.py")
            self.assertFalse((Path(directory) / "old.py").exists())

    def test_current_suite_upgrade_probe_preserves_book_and_backup(self):
        report = upgrade.probe(timeout=60)
        self.assertTrue(report["ok"], report.get("error"))
        self.assertEqual(report["version"], load("package").current_version(ROOT / "skills/story-skill/scripts/story.py"))
        self.assertTrue(report["temporary_project_removed"])
        self.assertEqual({command["name"] for command in report["commands"]},
                         {"initial_install", "managed_update", "installed_version", "prepare_help", "repeat_update"})
        for name in ("initial_install", "backup_exact", "updated_payload", "unchanged_siblings",
                     "runtime_version", "repeat_idempotent", "book_and_other_skill_unchanged"):
            self.assertEqual(report["checks"][name]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
