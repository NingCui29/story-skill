import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_verify", ROOT / "scripts/verify.py")
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class VerificationEvidenceTests(unittest.TestCase):
    def test_links_are_checked_in_specialized_skills(self):
        with tempfile.TemporaryDirectory(prefix="story-suite-links-") as directory:
            root = Path(directory)
            skills = root / "skills"
            core = skills / "story-skill"
            review = skills / "story-skill-review"
            core.mkdir(parents=True)
            review.mkdir()
            (core / "SKILL.md").write_text("# Core\n", encoding="utf-8")
            (review / "SKILL.md").write_text(
                "[shared](../story-skill/SKILL.md)\n[history](references/history.md)\n", encoding="utf-8")
            with patch.object(verify, "ROOT", root), patch.object(verify, "SKILLS", skills):
                result = verify.check_markdown_links()
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["links_checked"], 2)
            self.assertEqual(result["missing"], [{"source": "skills/story-skill-review/SKILL.md",
                                                 "target": "references/history.md", "exists": False}])

    def test_archive_validation_covers_specialized_skill_bytes(self):
        with tempfile.TemporaryDirectory(prefix="story-suite-archive-") as directory:
            archive = Path(directory) / "story-skill-0.6.0.zip"
            files = {"story-skill/SKILL.md": b"core", "story-skill-write/SKILL.md": b"writing"}
            expected = {key: hashlib.sha256(raw).hexdigest() for key, raw in files.items()}
            with zipfile.ZipFile(archive, "w") as bundle:
                for name, raw in files.items():
                    bundle.writestr(name, raw if name.startswith("story-skill/") else b"changed")
            with patch.object(verify, "skill_files", return_value=expected), patch.object(verify, "package_module") as loader:
                loader.return_value.current_version.return_value = "0.6.0"
                result = verify.check_archive(archive)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["changed"], ["story-skill-write/SKILL.md"])

    def archive_with_member_kind(self, kind):
        with tempfile.TemporaryDirectory(prefix="story-archive-kind-") as directory:
            archive = Path(directory) / "story-skill-0.6.0.zip"
            files = {"story-skill/SKILL.md": b"core", "story-skill-write/SKILL.md": b"writing"}
            expected = {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
            with zipfile.ZipFile(archive, "w") as bundle:
                for name, raw in files.items():
                    member = zipfile.ZipInfo(name)
                    member.create_system = 3
                    member.external_attr = ((kind if name == "story-skill/SKILL.md" else stat.S_IFREG) | 0o644) << 16
                    bundle.writestr(member, raw)
            with patch.object(verify, "skill_files", return_value=expected), patch.object(verify, "package_module") as loader:
                loader.return_value.current_version.return_value = "0.6.0"
                return verify.check_archive(archive)

    def test_archive_rejects_special_members_even_when_names_and_bytes_match(self):
        for kind in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFDIR):
            with self.subTest(kind=kind):
                result = self.archive_with_member_kind(kind)
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["contents_match_current_skill"])
                self.assertEqual(result["non_regular_entries"], ["story-skill/SKILL.md"])
                self.assertEqual(result["changed"], [])
                self.assertEqual(result["missing"], [])
                self.assertEqual(result["extra"], [])

    def test_archive_accepts_regular_and_unspecified_member_types(self):
        for kind in (stat.S_IFREG, 0):
            with self.subTest(kind=kind):
                result = self.archive_with_member_kind(kind)
                self.assertEqual(result["status"], "passed")
                self.assertTrue(result["contents_match_current_skill"])
                self.assertEqual(result["non_regular_entries"], [])

    def test_counts_come_from_real_callbacks_including_failed_subtests(self):
        class Example(unittest.TestCase):
            def test_pass(self):
                pass

            def test_fail(self):
                for i in range(3):
                    with self.subTest(i=i):
                        self.fail("intentional fixture failure")

            @unittest.skip("intentional fixture skip")
            def test_skip(self):
                pass

        runner = unittest.TextTestRunner(stream=io.StringIO(), resultclass=verify.CountedTestResult)
        actual = runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(Example))
        result = verify.summarize_unittest(actual)
        self.assertEqual((result["run"], result["passed"], result["failed"], result["skipped"]), (3, 1, 3, 1))
        self.assertEqual(result["status"], "failed")

    def test_empty_suite_cannot_be_reported_as_passed(self):
        runner = unittest.TextTestRunner(stream=io.StringIO(), resultclass=verify.CountedTestResult)
        result = verify.summarize_unittest(runner.run(unittest.TestSuite()))
        self.assertEqual(result["run"], 0)
        self.assertEqual(result["status"], "failed")

    def test_all_skipped_tests_cannot_be_reported_as_passed(self):
        class Example(unittest.TestCase):
            @unittest.skip("no usable test environment")
            def test_skip(self):
                pass

        runner = unittest.TextTestRunner(stream=io.StringIO(), resultclass=verify.CountedTestResult)
        result = verify.summarize_unittest(runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(Example)))
        self.assertEqual((result["run"], result["passed"], result["skipped"]), (1, 0, 1))
        self.assertEqual(result["status"], "failed")

    def test_reparse_point_output_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory(prefix="story-verify-links-test-") as directory:
            root = Path(directory)
            output = root / "verification.json"
            output.write_text("existing external evidence", encoding="utf-8")
            original = Path.lstat

            class ReparseAttributes:
                st_mode = 0o100644
                st_file_attributes = 0x400

            def attributes(path, *args, **kwargs):
                return ReparseAttributes() if path == output else original(path, *args, **kwargs)

            with patch.object(Path, "lstat", attributes):
                with self.assertRaisesRegex(ValueError, "linked report output"):
                    verify.write_report({"ok": True}, output)
            self.assertEqual(output.read_text(encoding="utf-8"), "existing external evidence")

    def test_linked_parent_cannot_redirect_report_outside_requested_directory(self):
        with tempfile.TemporaryDirectory(prefix="story-verify-native-link-test-") as directory:
            root = Path(directory)
            requested, external = root / "requested", root / "external"
            requested.mkdir()
            external.mkdir()
            link = requested / "reports"
            try:
                link.symlink_to(external, target_is_directory=True)
            except OSError as error:
                if os.name != "nt":
                    self.skipTest(f"Directory links unavailable: {error}")
                import _winapi
                _winapi.CreateJunction(str(external), str(link))
            with self.assertRaisesRegex(ValueError, "linked report output"):
                verify.write_report({"ok": True}, link / "verification.json")
            self.assertEqual(list(external.iterdir()), [])

    def test_default_archive_matches_canonical_version_even_if_other_versions_exist(self):
        with tempfile.TemporaryDirectory(prefix="story-verify-version-test-") as directory:
            root = Path(directory).resolve()
            (root / "dist").mkdir()
            current = root / "dist/story-skill-2.4.6.zip"
            current.write_bytes(b"current")
            (root / "dist/story-skill-9.9.9.zip").write_bytes(b"different release")
            with patch.object(verify, "ROOT", root), patch.object(verify, "package_module") as loader:
                loader.return_value.current_version.return_value = "2.4.6"
                self.assertEqual(verify.select_archive(), current)

    def test_failure_report_preserves_existing_success_evidence(self):
        with tempfile.TemporaryDirectory(prefix="story-verify-report-test-") as directory:
            path = Path(directory).resolve() / "verification.json"
            passed = {"ok": True, "unit_tests": {"run": 57}}
            self.assertEqual(verify.write_report(passed, path), path)
            failure_path = verify.write_report({"ok": False}, path)
            self.assertNotEqual(failure_path, path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), passed)
            self.assertFalse(json.loads(failure_path.read_text(encoding="utf-8"))["ok"])


class VersionedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-versioned-evidence-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.skill = self.root / "skills/story-skill"
        (self.skill / "scripts").mkdir(parents=True)
        self.runtime = self.skill / "scripts/story.py"
        package = verify.package_module()
        self.root_patch = patch.object(verify, "ROOT", self.root)
        self.skill_patch = patch.object(verify, "SKILL", self.skill)
        self.package_patch = patch.object(verify, "package_module", return_value=package)
        for replacement in (self.root_patch, self.skill_patch, self.package_patch):
            replacement.start()
            self.addCleanup(replacement.stop)

    def use_version(self, version):
        self.runtime.write_text(f'VERSION = "{version}"\n', encoding="utf-8")
        return self.root / "benchmarks/results" / f"v{version}"

    def write_probes(self, directory, hashes):
        directory.mkdir(parents=True, exist_ok=True)
        reports = {
            "scaling.json": {"ok": True, "runtime_files": hashes, "runtime_stable": True,
                             "cases": [{"chapters": chapters, "cards": cards,
                                        "integrity_mode": mode, "ok": True}
                                       for chapters, cards in ((400, 2000), (4000, 20000))
                                       for mode in ("strict", "local")]},
            "migration.json": {"ok": True, "runtime": hashes, "original_tree_unchanged": True,
                               "books": [{"fixture": number} for number in range(3)]},
        }
        for filename, report in reports.items():
            (directory / filename).write_text(json.dumps(report), encoding="utf-8")

    def test_default_output_uses_runtime_version_without_overwriting_other_releases(self):
        previous = self.root / "benchmarks/results/v0.6.0/verification.json"
        previous.parent.mkdir(parents=True)
        previous.write_bytes(b"preserved historical evidence\n")
        for version in ("0.6.1", "0.6.2"):
            with self.subTest(version=version):
                directory = self.use_version(version)
                with patch.object(verify.sys, "argv", ["verify.py"]), patch.object(
                        verify, "verify", return_value={"ok": True, "fixture_version": version}), patch(
                        "sys.stdout", new=io.StringIO()):
                    self.assertEqual(verify.main(), 0)
                output = directory / "verification.json"
                self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["fixture_version"], version)
                self.assertEqual(previous.read_bytes(), b"preserved historical evidence\n")

    def test_explicit_output_is_honored_without_creating_default_directory(self):
        directory = self.use_version("0.6.0")
        target = self.root / "custom/evidence.json"
        with patch.object(verify.sys, "argv", ["verify.py", "--output", str(target)]), patch.object(
                verify, "verify", return_value={"ok": True}), patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(verify.main(), 0)
        self.assertTrue(target.is_file())
        self.assertFalse(directory.exists())

    def test_probes_follow_runtime_version_and_keep_exact_hash_binding(self):
        for version in ("0.6.1", "0.6.2"):
            with self.subTest(version=version):
                directory = self.use_version(version)
                hashes = {"story.py": verify.digest(self.runtime)}
                self.write_probes(directory, hashes)
                result = verify.check_recorded_probes()
                self.assertEqual(result["status"], "passed")
                self.assertEqual({Path(item["path"]).parent for item in result["reports"]}, {directory})
                self.runtime.write_text(f'VERSION = "{version}"\nCHANGED = True\n', encoding="utf-8")
                changed = verify.check_recorded_probes()
                self.assertEqual(changed["status"], "failed")
                self.assertTrue(all(not item["matches_current_runtime"] for item in changed["reports"]))

    def test_missing_current_probes_do_not_fall_back_to_a_previous_release(self):
        current = self.use_version("0.6.1")
        older = self.root / "benchmarks/results/v0.6.0"
        self.write_probes(older, {"story.py": verify.digest(self.runtime)})
        with self.assertRaises(FileNotFoundError) as error:
            verify.check_recorded_probes()
        self.assertEqual(Path(error.exception.filename), current / "scaling.json")


class BenchmarkContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-benchmark-contract-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "benchmarks/results").mkdir(parents=True)
        candidate = b"hello\n"
        (self.root / "candidate.md").write_bytes(candidate)
        self.config = {"repository": "https://example.invalid/fixture", "revision": "pinned-fixture",
                       "encoding": "fixture-encoding", "profiles": [
                           {"id": "writing", "label": "写作夹具", "scope": "Static instruction files only",
                            "upstream": ["skills/example/SKILL.md", "skills/example/reference.md"],
                            "candidate": ["candidate.md"]}]}
        candidate_entry = {"path": "candidate.md", "bytes": len(candidate), "tokens": 2,
                           "sha256": hashlib.sha256(candidate).hexdigest(),
                           "normalized_text_sha256": hashlib.sha256(candidate).hexdigest()}
        self.report = {"schema": 1, "repository": self.config["repository"],
                       "upstream_revision": self.config["revision"], "encoding": self.config["encoding"],
                       "profiles": [{"id": "writing", "label": "写作夹具", "scope": "Static instruction files only",
                                     "upstream_files": [{"path": self.config["profiles"][0]["upstream"][0], "tokens": 10},
                                                        {"path": self.config["profiles"][0]["upstream"][1], "tokens": 20}],
                                     "candidate_files": [candidate_entry], "upstream_tokens": 30,
                                     "candidate_tokens": 2, "reduction_percent": 93.33}]}

    def check(self, config=None, report=None):
        (self.root / "benchmarks/profiles.json").write_text(
            json.dumps(self.config if config is None else config), encoding="utf-8")
        (self.root / "benchmarks/results/tokens.json").write_text(
            json.dumps(self.report if report is None else report), encoding="utf-8")
        with patch.object(verify, "ROOT", self.root):
            return verify.check_benchmark()

    def test_matching_lists_metadata_hashes_and_arithmetic_pass(self):
        result = self.check()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["problems"], [])

    def test_changed_upstream_inputs_cannot_validate_an_old_report(self):
        alternatives = [["skills/example/SKILL.md"],
                        ["skills/example/reference.md", "skills/example/SKILL.md"],
                        [*self.config["profiles"][0]["upstream"], "skills/example/extra.md"]]
        for paths in alternatives:
            with self.subTest(paths=paths):
                config = copy.deepcopy(self.config)
                config["profiles"][0]["upstream"] = paths
                result = self.check(config=config)
                self.assertEqual(result["status"], "failed")
                self.assertTrue(any("Upstream file list differs" in problem for problem in result["problems"]))

    def test_changed_candidate_inputs_are_rejected(self):
        config = copy.deepcopy(self.config)
        config["profiles"][0]["candidate"].append("new-candidate.md")
        result = self.check(config=config)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("Candidate file list differs" in problem for problem in result["problems"]))

    def test_changed_configuration_metadata_is_rejected(self):
        for field in ("repository", "revision", "encoding", "id", "label", "scope"):
            with self.subTest(field=field):
                config = copy.deepcopy(self.config)
                target = config if field in ("repository", "revision", "encoding") else config["profiles"][0]
                target[field] = "changed fixture value"
                self.assertEqual(self.check(config=config)["status"], "failed")

    def test_duplicate_input_paths_and_reordered_profiles_are_rejected(self):
        for side in ("upstream", "candidate"):
            with self.subTest(side=side):
                config, report = copy.deepcopy(self.config), copy.deepcopy(self.report)
                config["profiles"][0][side].append(config["profiles"][0][side][0])
                entries = report["profiles"][0][f"{side}_files"]
                entries.append(copy.deepcopy(entries[0]))
                self.assertEqual(self.check(config=config, report=report)["status"], "failed")
        config, report = copy.deepcopy(self.config), copy.deepcopy(self.report)
        config["profiles"].append({**copy.deepcopy(config["profiles"][0]), "id": "second"})
        report["profiles"].append({**copy.deepcopy(report["profiles"][0]), "id": "second"})
        config["profiles"].reverse()
        self.assertEqual(self.check(config=config, report=report)["status"], "failed")

    def test_inconsistent_totals_and_reduction_cannot_be_reported_as_passed(self):
        for field in ("upstream_tokens", "candidate_tokens", "reduction_percent"):
            with self.subTest(field=field):
                report = copy.deepcopy(self.report)
                report["profiles"][0][field] += 1
                self.assertEqual(self.check(report=report)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
