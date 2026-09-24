from contextlib import ExitStack
import importlib.util
import io
import json
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
import warnings
from unittest.mock import patch
import zipfile

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
        self.skill = self.root / "skills/story-codex"
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

    def test_defaults_follow_current_version_and_preserve_previous_release(self):
        for module, loader, filename in self.probes:
            for version in ("0.4.0", "0.5.0", "0.5.1"):
                with self.subTest(probe=filename, version=version):
                    self.run_main(module, loader, version)
            for version in ("0.4.0", "0.5.0", "0.5.1"):
                path = self.root / "benchmarks/results" / f"v{version}" / filename
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["fixture_version"], version)

    def test_explicit_output_is_honored_without_creating_version_directory(self):
        for module, loader, filename in self.probes:
            with self.subTest(probe=filename):
                path = self.root / "custom" / filename
                self.run_main(module, loader, "0.5.1", path)
                self.assertTrue(path.is_file())
        self.assertFalse((self.root / "benchmarks/results/v0.5.1").exists())

    def test_failed_default_run_preserves_current_success(self):
        for module, loader, filename in self.probes:
            with self.subTest(probe=filename):
                self.run_main(module, loader, "0.5.1")
                path = self.root / "benchmarks/results/v0.5.1" / filename
                original = path.read_bytes()
                self.run_main(module, loader, "0.5.1", ok=False)
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

    def test_missing_retained_database_is_not_reported_as_synthetic_or_success(self):
        with tempfile.TemporaryDirectory(prefix="story-probe-source-") as directory:
            source = Path(directory).resolve()
            (source / migration.RETAINED_NAMES[0]).mkdir()
            report = migration.probe(source)
        self.assertFalse(report["ok"])
        self.assertEqual(report["mode"], "retained-source")
        self.assertEqual(report["books"], [])
        self.assertIn("Source database unavailable", report["error"]["message"])

    def test_changed_legacy_runtime_archive_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory(prefix="story-probe-runtime-") as directory:
            archive = Path(directory) / "changed.zip"
            archive.write_bytes(b"untrusted replacement")
            with patch.object(migration, "LEGACY_ARCHIVE", archive):
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    migration.legacy_runtime(Path(directory) / "old.py")
            self.assertFalse((Path(directory) / "old.py").exists())

    def test_seven_skill_archive_keeps_all_sibling_roots(self):
        names = upgrade.load_script("install").LEGACY_SKILL_NAMES
        with tempfile.TemporaryDirectory(prefix="story-upgrade-suite-") as directory:
            root = Path(directory).resolve()
            archive = root / "suite.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                for name in names:
                    bundle.writestr(name + "/SKILL.md", "# " + name)
                bundle.writestr("story-codex/scripts/story.py", 'VERSION = "0.4.0"\n')
            source, files = upgrade.unpack_old_archive(archive, root / "unpacked")
            self.assertEqual(source, root / "unpacked")
            self.assertEqual({name.split("/")[0] for name in files}, set(names))
            self.assertTrue((source / "story-codex-write/SKILL.md").is_file())

    def test_eight_skill_archive_keeps_publisher_and_rejects_mislabeled_roots(self):
        installer = upgrade.load_script("install")
        for version, names, valid in (("0.5.11", installer.SKILL_NAMES, True),
                                      ("0.5.10", installer.SKILL_NAMES, False),
                                      ("0.5.11", installer.LEGACY_SKILL_NAMES, False),
                                      ("0.6.0", installer.SKILL_NAMES, False)):
            with self.subTest(version=version, count=len(names)), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                archive = root / "suite.zip"
                with zipfile.ZipFile(archive, "w") as bundle:
                    for name in names:
                        bundle.writestr(name + "/SKILL.md", "# " + name)
                    bundle.writestr("story-codex/scripts/story.py", f'VERSION = "{version}"\n')
                if valid:
                    source, files = upgrade.unpack_old_archive(archive, root / "unpacked")
                    self.assertEqual(source, root / "unpacked")
                    self.assertEqual({name.split("/")[0] for name in files}, set(names))
                else:
                    with self.assertRaises(ValueError):
                        upgrade.unpack_old_archive(archive, root / "unpacked")
                    self.assertFalse((root / "unpacked").exists())

    def test_invalid_archive_paths_are_rejected_before_extraction(self):
        invalid_members = [
            ["story-codex/../../escaped"], ["story-codex\\escaped"], ["other-skill/SKILL.md"],
            ["story-codex/CON.txt"], ["story-codex/a./file"], ["story-codex/a:stream"], ["story-codex/a?/file"], ["story-codex/empty//"],
            ["story-codex/A/x", "story-codex/a/y"],
            ["story-codex/caf\u00e9/x", "story-codex/cafe\u0301/y"],
            ["story-codex/parent", "story-codex/parent/file"],
            ["story-codex/SKILL.md"],
        ]
        for members in invalid_members:
            with self.subTest(members=members), tempfile.TemporaryDirectory(prefix="story-upgrade-bad-") as directory:
                root = Path(directory).resolve()
                archive = root / "bad.zip"
                with zipfile.ZipFile(archive, "w") as bundle:
                    bundle.writestr("story-codex/SKILL.md", "# Core")
                    bundle.writestr("story-codex/scripts/story.py", 'VERSION = "0.3.0"\n')
                    with warnings.catch_warnings():
                        warnings.filterwarnings("ignore", message="Duplicate name:", category=UserWarning)
                        for name in members:
                            # Preserve the raw member spelling on Windows too;
                            # ZipInfo(name) otherwise rewrites backslashes.
                            member = zipfile.ZipInfo()
                            member.filename = member.orig_filename = name
                            bundle.writestr(member, "bad")
                with self.assertRaises(ValueError):
                    upgrade.unpack_old_archive(archive, root / "unpacked")
                self.assertFalse((root / "unpacked").exists())
                self.assertFalse((root / "escaped").exists())

    def test_reader_normalized_archive_path_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory(prefix="story-upgrade-normalized-") as directory:
            root = Path(directory).resolve()
            archive = root / "normalized.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("story-codex/SKILL.md", "# Core")
                bundle.writestr("story-codex/scripts/story.py", 'VERSION = "0.3.0"\n')
                member = zipfile.ZipInfo()
                member.filename = member.orig_filename = "story-codex\\extra.txt"
                bundle.writestr(member, "extra")
            # Reproduce Windows ZipInfo normalization on every test platform.
            with patch.object(zipfile.os, "sep", "\\"):
                with zipfile.ZipFile(archive) as bundle:
                    member = bundle.infolist()[-1]
                    self.assertNotEqual(member.orig_filename, member.filename)
                with self.assertRaisesRegex(ValueError, "Non-portable archive path"):
                    upgrade.unpack_old_archive(archive, root / "unpacked")
            self.assertFalse((root / "unpacked").exists())

    def test_archive_link_or_partial_suite_is_rejected(self):
        for special in (True, False):
            with self.subTest(special=special), tempfile.TemporaryDirectory(prefix="story-upgrade-kind-") as directory:
                root = Path(directory).resolve()
                archive = root / "bad.zip"
                with zipfile.ZipFile(archive, "w") as bundle:
                    bundle.writestr("story-codex/SKILL.md", "# Core")
                    bundle.writestr("story-codex/scripts/story.py", 'VERSION = "0.3.0"\n')
                    if special:
                        member = zipfile.ZipInfo("story-codex/link")
                        member.create_system = 3
                        member.external_attr = (stat.S_IFLNK | 0o777) << 16
                        bundle.writestr(member, "../../outside")
                    else:
                        bundle.writestr("story-codex-plan/SKILL.md", "# Plan")
                with self.assertRaises(ValueError):
                    upgrade.unpack_old_archive(archive, root / "unpacked")
                self.assertFalse((root / "unpacked").exists())

    def test_legacy_single_skill_upgrade_still_preserves_backup(self):
        # A retained release ZIP may legitimately differ from this working tree.
        # Build and verify the current source in an isolated repository instead
        # of depending on, replacing, or bypassing the real dist archive.
        with tempfile.TemporaryDirectory(prefix="story-upgrade-current-") as directory:
            repository = Path(directory).resolve()
            shutil.copytree(ROOT / "skills", repository / "skills", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            (repository / "scripts").mkdir()
            for name in ("install", "package", "verify"):
                shutil.copyfile(ROOT / "scripts" / f"{name}.py", repository / "scripts" / f"{name}.py")
            with patch.object(upgrade, "ROOT", repository), patch.object(upgrade, "SKILL", repository / "skills/story-codex"), \
                    patch.object(upgrade, "INSTALLER", repository / "scripts/install.py"):
                packaged = upgrade.load_script("package").package()
                report = upgrade.probe(ROOT / "tests/fixtures/story-codex-0.2.0.zip", 60)
        self.assertTrue(report["ok"], report.get("error"))
        self.assertEqual(report["checks"]["current_archive_matches_canonical"]["status"], "passed")
        self.assertEqual(report["current_archive"]["archive"], packaged["archive"])
        self.assertEqual(report["current_archive"]["sha256"], packaged["sha256"])
        self.assertEqual(report["initial_archive"]["version"], "0.2.0")
        self.assertEqual(report["checks"]["archive_extraction_exact"]["skill_count"], 1)
        self.assertEqual(report["checks"]["previous_release_backup_exact"]["status"], "passed")
        self.assertEqual(report["checks"]["all_skills_match_canonical"]["skill_count"], 8)


if __name__ == "__main__":
    unittest.main()
