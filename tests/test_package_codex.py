import copy
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("codex_plugin_package", ROOT / "scripts/package_codex.py")
codex = importlib.util.module_from_spec(spec)
spec.loader.exec_module(codex)


def directory_files(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}


class CodexPluginPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-codex-package-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "skills"
        self.source_entries = dict(codex.suite.source_entries())
        self.version = codex.suite.source_version(
            self.source_entries["story-skill/scripts/story.py"])
        for relative, raw in self.source_entries.items():
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        for relative in (".codex-plugin/plugin.json", "assets/story-skill.svg", "LICENSE"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, path)
        self.manifest_path = self.root / ".codex-plugin/plugin.json"
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.output = self.root / "out"

    def package(self, output=None, source=None):
        with patch.object(codex, "ROOT", self.root), \
                patch.object(codex.suite, "SOURCE", source or self.source):
            return codex.package(output_dir=output or self.output)

    def write_manifest(self, manifest):
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def assert_installable_bundle(self, receipt, source_root, source_entries):
        marketplace_root = Path(receipt["marketplace_root"])
        plugin_root = Path(receipt["plugin_root"])
        self.assertEqual(plugin_root, marketplace_root / "plugins/story-skill")
        marketplace = json.loads((marketplace_root / ".agents/plugins/marketplace.json")
                                 .read_text(encoding="utf-8"))
        self.assertEqual(marketplace["name"], "story-skill-local")
        self.assertEqual(len(marketplace["plugins"]), 1)
        entry = marketplace["plugins"][0]
        self.assertEqual(entry["name"], "story-skill")
        self.assertEqual(entry["source"], {"source": "local", "path": "./plugins/story-skill"})
        self.assertTrue((marketplace_root / entry["source"]["path"]).samefile(plugin_root))
        plugin = json.loads((plugin_root / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin["version"], receipt["version"])
        self.assertEqual(plugin["skills"], "./skills/")
        expected = {}
        for name, raw in source_entries.items():
            skill = PurePosixPath(name).parts[0]
            if name == skill + "/agents/openai.yaml":
                raw = raw.replace(("$" + skill + " ").encode("utf-8"),
                                  ("$story-skill:" + skill + " ").encode("utf-8"))
            expected["skills/" + name] = raw
        expected.update({name: (source_root / name).read_bytes() for name in (
            ".codex-plugin/plugin.json", "assets/story-skill.svg", "LICENSE")})
        self.assertEqual(directory_files(plugin_root), expected)
        skill_entries = sorted(path.parent.name for path in (plugin_root / "skills").glob("*/SKILL.md"))
        self.assertEqual(skill_entries, sorted(codex.suite.skill_names(receipt["version"])))
        self.assertEqual(receipt["skill_count"], 8)
        self.assertEqual(receipt["payload_files"], len(expected))
        self.assertEqual(receipt["payload_files"], 55)

    def test_real_source_packages_all_reviewed_skills_and_preserves_the_runtime(self):
        receipt = codex.package(output_dir=self.output)
        self.assert_installable_bundle(receipt, ROOT, self.source_entries)
        plugin_root = Path(receipt["plugin_root"])
        self.assertEqual((plugin_root / "LICENSE").read_bytes(), (ROOT / "LICENSE").read_bytes())
        self.assertEqual((plugin_root / "skills/story-skill/scripts/story.py").read_bytes(),
                         self.source_entries["story-skill/scripts/story.py"])

    def test_only_ui_prompts_get_exact_namespace_mapping_without_mutating_sources(self):
        before = directory_files(self.source)
        receipt = self.package()
        actual = directory_files(Path(receipt["plugin_root"]) / "skills")
        skills = codex.suite.skill_names(self.version)
        ui_paths = {skill + "/agents/openai.yaml" for skill in skills}
        for name, raw in before.items():
            with self.subTest(name=name):
                if name in ui_paths:
                    skill = PurePosixPath(name).parts[0]
                    expected = raw.replace(("$" + skill + " ").encode("utf-8"),
                                           ("$story-skill:" + skill + " ").encode("utf-8"))
                    self.assertNotEqual(expected, raw)
                    self.assertEqual(actual[name], expected)
                else:
                    self.assertEqual(actual[name], raw)
        self.assertEqual(directory_files(self.source), before)
        self.assertEqual(receipt["source_payload_files"], 52)
        self.assertEqual(receipt["adapted_ui_files"], 8)

        # A prompt can name several skills. Each complete reference must map once;
        # similar names, existing namespaces and unrelated YAML fields stay intact.
        unchanged = b"$story-skillish $story-skill-plan-extra $story-skill_plan $story-skill:story-skill-plan"
        references = " ".join("$" + skill for skill in skills).encode("utf-8")
        namespaced = " ".join("$story-skill:" + skill for skill in skills).encode("utf-8")
        prefix = b'interface:\n  display_name: "$story-skill-plan"\n  default_prompt: "'
        original = prefix + references + b" " + unchanged + b'"\n'
        expected = prefix + namespaced + b" " + unchanged + b'"\n'
        synthetic = [(name, original if name == "story-skill-plan/agents/openai.yaml" else raw)
                     for name, raw in before.items()]
        snapshot = list(synthetic)
        mapped = dict(codex.plugin_skill_entries(synthetic))
        self.assertEqual(mapped["story-skill-plan/agents/openai.yaml"], expected)
        self.assertEqual(synthetic, snapshot)
        for name, raw in before.items():
            if name not in ui_paths:
                self.assertEqual(mapped[name], raw)

    def test_sibling_novels_secrets_and_development_files_are_excluded(self):
        private_files = ("novels/正文/第一章.md", ".env", "tests/private-fixture.txt",
                         "skills/my-novel/draft.md", "benchmarks/results/private.json")
        marker = "PRIVATE MANUSCRIPT 不得打包".encode("utf-8")
        for relative in private_files:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(marker)
        receipt = self.package()
        self.assert_installable_bundle(receipt, self.root, self.source_entries)
        with zipfile.ZipFile(receipt["archive"]) as archive:
            self.assertTrue(all(marker not in archive.read(name) for name in archive.namelist()))
            self.assertFalse(any("my-novel" in name or ".env" in PurePosixPath(name).parts
                                 for name in archive.namelist()))

    def test_archive_matches_installable_directory_and_published_sha256(self):
        receipt = self.package()
        archive_path = Path(receipt["archive"])
        marketplace_root = Path(receipt["marketplace_root"])
        expected = directory_files(marketplace_root)
        with zipfile.ZipFile(archive_path) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(len(archive.namelist()), len(set(archive.namelist())))
            members = {name: archive.read(name) for name in archive.namelist()}
            self.assertEqual(members, expected)
            self.assertTrue(all(not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts
                                for name in archive.namelist()))
        digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        self.assertEqual(receipt["sha256"], digest)
        self.assertEqual(Path(receipt["checksum"]).read_text(encoding="utf-8"),
                         f"{digest}  {archive_path.name}\n")

    def test_clean_builds_in_different_parent_directories_are_identical(self):
        first = self.package(self.root / "first/out")
        second = self.package(self.root / "second/out")
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual(Path(first["archive"]).read_bytes(), Path(second["archive"]).read_bytes())
        self.assertEqual(directory_files(Path(first["marketplace_root"])),
                         directory_files(Path(second["marketplace_root"])))

    def test_plugin_runtime_version_mismatch_is_rejected(self):
        self.write_manifest({**self.manifest, "version": "0.0.0"})
        with self.assertRaises(ValueError):
            self.package()

    def test_manifest_paths_cannot_escape_the_plugin(self):
        for field, value in (("skills", "../skills/"),
                             ("composerIcon", "../../outside.svg"),
                             ("logo", str(self.root / "assets/story-skill.svg"))):
            with self.subTest(field=field):
                manifest = copy.deepcopy(self.manifest)
                if field == "skills":
                    manifest[field] = value
                else:
                    manifest["interface"][field] = value
                self.write_manifest(manifest)
                with self.assertRaises(ValueError):
                    self.package(self.root / ("invalid-" + field))

    def test_missing_declared_asset_is_rejected(self):
        (self.root / "assets/story-skill.svg").unlink()
        with self.assertRaises(ValueError):
            self.package()

    def test_linked_source_directory_and_payload_are_rejected(self):
        linked_source = self.root / "linked-skills"
        linked_source.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.package(source=linked_source)
        runtime = self.source / "story-skill/scripts/story.py"
        target = self.root / "outside-story.py"
        runtime.replace(target)
        runtime.symlink_to(target)
        with self.assertRaises(ValueError):
            self.package()

    def test_linked_wrapper_directory_and_output_parent_are_rejected(self):
        assets = self.root / "assets"
        real_assets = self.root / "real-assets"
        assets.rename(real_assets)
        assets.symlink_to(real_assets, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.package()
        assets.unlink()
        real_assets.rename(assets)
        real_output = self.root / "real-output"
        real_output.mkdir()
        linked_output = self.root / "linked-output"
        linked_output.symlink_to(real_output, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.package(linked_output)
        self.assertEqual(directory_files(real_output), {})

    def test_output_inside_plugin_sources_is_rejected_without_creating_files(self):
        for directory in (self.source, self.root / ".codex-plugin", self.root / "assets"):
            with self.subTest(directory=directory):
                output = directory / "accidental-build"
                with self.assertRaises(ValueError):
                    self.package(output)
                self.assertFalse(output.exists())

    def test_extra_file_inside_a_skill_is_rejected_instead_of_leaking_it(self):
        draft = self.source / "story-skill-write/private-draft.md"
        draft.write_text("不得随技能分发的小说正文", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.package()

    def test_missing_shared_runtime_dependency_is_rejected(self):
        (self.source / "story-skill/scripts/story_storage.py").unlink()
        with self.assertRaises(ValueError):
            self.package()

    def test_existing_archive_checksum_or_marketplace_is_preserved_on_collision(self):
        reference = self.package(self.root / "reference")
        for field in ("archive", "checksum", "marketplace_root"):
            with self.subTest(field=field):
                output = self.root / ("existing-" + field)
                target = output / Path(reference[field]).relative_to(self.root / "reference")
                target.parent.mkdir(parents=True, exist_ok=True)
                if field == "marketplace_root":
                    target.mkdir()
                    (target / "user-notes.txt").write_bytes(b"existing user data")
                else:
                    target.write_bytes(b"existing reviewed artifact")
                before = directory_files(output)
                with self.assertRaises(FileExistsError):
                    self.package(output)
                self.assertEqual(directory_files(output), before)

    def test_default_output_is_the_selected_source_root_dist_directory(self):
        with patch.object(codex, "ROOT", self.root), patch.object(codex.suite, "SOURCE", self.source):
            receipt = codex.package()
        self.assertEqual(Path(receipt["archive"]).parent, self.root / "dist")
        self.assertEqual(Path(receipt["marketplace_root"]).parent, self.root / "dist")

    def test_cli_builds_a_local_marketplace_and_prints_a_machine_readable_receipt(self):
        completed = subprocess.run([sys.executable, str(ROOT / "scripts/package_codex.py"),
                                    "--output-dir", str(self.output)], cwd=self.root,
                                   capture_output=True, text=True, check=True)
        receipt = json.loads(completed.stdout)
        self.assertEqual(Path(receipt["archive"]).parent, self.output)
        self.assert_installable_bundle(receipt, ROOT, self.source_entries)


if __name__ == "__main__":
    unittest.main()
