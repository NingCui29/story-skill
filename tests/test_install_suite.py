import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("suite_installer", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class SuiteInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="story-suite-install-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "skills"
        self.project = self.root / "中文项目"
        for name in installer.SKILL_NAMES:
            folder = self.source / name
            (folder / "agents").mkdir(parents=True)
            for relative, raw in {"SKILL.md": name, "agents/openai.yaml": name,
                                  "LICENSE": "MIT"}.items():
                (folder / relative).write_text(raw, encoding="utf-8")
        for name in ["story.py", "story_storage.py", "story_history.py", "story_search.py", "story_world.py"]:
            path = self.source / "story-codex/scripts" / name
            path.parent.mkdir(exist_ok=True)
            path.write_text('VERSION = "0.5.1"\n', encoding="utf-8")
        for name in installer.SUITE_FILES:
            if "/references/" in name:
                path = self.source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("reference", encoding="utf-8")
        self.project.mkdir()
        self.book = self.project / "books/我的小说/正文/第1章.md"
        self.book.parent.mkdir(parents=True)
        self.book.write_text("不能改动的小说正文", encoding="utf-8")
        self.other = self.project / ".agents/skills/other-skill/SKILL.md"
        self.other.parent.mkdir(parents=True)
        self.other.write_text("其他技能", encoding="utf-8")

    def install(self, update=False):
        return installer.install(self.project, update, self.source)

    def target(self, name):
        return self.project / ".agents/skills" / name

    def change_source(self):
        for name in installer.SKILL_NAMES:
            (self.source / name / "SKILL.md").write_text(name + " updated", encoding="utf-8")

    def test_install_all_dependencies_preserves_book_and_other_skills(self):
        result = self.install()
        self.assertEqual(result["skills"], list(installer.SKILL_NAMES))
        for name in installer.SKILL_NAMES:
            self.assertEqual(installer.inventory(self.source / name), installer.inventory(self.target(name)))
        self.assertEqual(self.book.read_text(encoding="utf-8"), "不能改动的小说正文")
        self.assertEqual(self.other.read_text(encoding="utf-8"), "其他技能")
        self.assertEqual(self.install()["status"], "unchanged")

    def test_single_new_core_redirects_to_complete_suite(self):
        result = installer.install(self.project, source=self.source / "story-codex")
        self.assertEqual(result["skills"], list(installer.SKILL_NAMES))
        (self.source / "story-codex-cover/SKILL.md").unlink()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            installer.install(self.project, True, self.source / "story-codex")

    def test_missing_dependency_stops_before_any_target_is_written(self):
        (self.source / "story-codex/scripts/story_storage.py").unlink()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.install()
        self.assertFalse(self.target("story-codex").exists())

    def test_missing_reference_or_private_draft_cannot_be_installed(self):
        reference = self.source / "story-codex-write/references/drama.md"
        raw = reference.read_bytes()
        reference.unlink()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.install()
        reference.write_bytes(raw)
        (self.source / "story-codex/private-draft.md").write_bytes(b"private book")
        with self.assertRaisesRegex(ValueError, "unreviewed files"):
            self.install()
        self.assertFalse(self.target("story-codex").exists())

    def test_install_and_archive_share_same_reviewed_manifest(self):
        spec = importlib.util.spec_from_file_location("suite_install_package_manifest", ROOT / "scripts/package.py")
        package = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(package)
        self.assertEqual(installer.SUITE_FILES, package.SUITE_FILES)
        self.assertEqual(installer.TAGGED_SUITE_FILES, package.TAGGED_SUITE_FILES)
        self.assertEqual(installer.LEGACY_SUITE_FILES, package.LEGACY_SUITE_FILES)
        for version in ("0.4.0", "0.4.12", "0.5.0", "0.5.1", "0.5.6", "0.5.7", "0.5.12"):
            self.assertEqual(installer.suite_files(version), package.suite_files(version))

    def test_tagged_release_installs_its_planning_reference(self):
        reference = self.source / "story-codex-plan/references/fanqie-tags.md"
        reference.parent.mkdir(parents=True, exist_ok=True)
        reference.write_text("阅读标签与内容标签", encoding="utf-8")
        runtime = self.source / "story-codex/scripts/story.py"
        runtime.write_text('VERSION = "0.5.7"\n', encoding="utf-8")
        installed = self.install()
        self.assertEqual(installed["files"], 34)
        self.assertEqual((self.target("story-codex-plan") / "references/fanqie-tags.md").read_bytes(),
                         reference.read_bytes())

    def test_historical_suite_installs_then_upgrades_with_new_analysis_references(self):
        added = set(installer.SUITE_FILES) - set(installer.LEGACY_SUITE_FILES)
        original = {name: (self.source / name).read_bytes() for name in added}
        runtime = self.source / "story-codex/scripts/story.py"
        for version in ("0.4.0", "0.5.0"):
            with self.subTest(version=version):
                project = self.root / ("upgrade-from-" + version)
                for name in added:
                    (self.source / name).unlink()
                runtime.write_text(f'VERSION = "{version}"\n', encoding="utf-8")
                result = installer.install(project, source=self.source)
                self.assertEqual(result["files"], 31)
                for name, raw in original.items():
                    (self.source / name).write_bytes(raw)
                runtime.write_text('VERSION = "0.5.1"\n', encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "--update"):
                    installer.install(project, source=self.source)
                upgraded = installer.install(project, True, self.source)
                self.assertEqual(upgraded["files"], 33)
                backup = Path(upgraded["backup"])
                self.assertEqual((backup / "story-codex/scripts/story.py").read_text(), f'VERSION = "{version}"\n')
                for name in added:
                    self.assertEqual((project / ".agents/skills" / name).read_bytes(), original[name])
                    self.assertFalse((backup / name).exists())

    def test_analysis_reference_layout_cannot_be_mislabeled_as_an_old_release(self):
        runtime = self.source / "story-codex/scripts/story.py"
        for version in ("0.4.0", "0.5.0"):
            with self.subTest(version=version):
                runtime.write_text(f'VERSION = "{version}"\n', encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "unreviewed files"):
                    self.install()
        runtime.write_text('VERSION = "0.5.1"\n', encoding="utf-8")
        (self.source / "story-codex-analyze/references/deep-reading.md").unlink()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.install()
        self.assertFalse(self.target("story-codex").exists())

    def test_unknown_suite_version_is_rejected_before_writing_targets(self):
        runtime = self.source / "story-codex/scripts/story.py"
        for version in ("0.5.01", "0.6.0", "1.0.0"):
            with self.subTest(version=version):
                runtime.write_text(f'VERSION = "{version}"\n', encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "no reviewed suite layout"):
                    self.install()
        self.assertFalse(self.target("story-codex").exists())

    def test_legacy_managed_core_upgrades_with_backup_and_requires_update(self):
        legacy = self.root / "legacy"
        (legacy / "scripts").mkdir(parents=True)
        (legacy / "SKILL.md").write_bytes(b"legacy skill")
        (legacy / "scripts/story.py").write_bytes(b'VERSION = "0.3.0"\n')
        installer.install(self.project, source=legacy)
        with self.assertRaisesRegex(ValueError, "--update"):
            self.install()
        result = self.install(True)
        self.assertEqual(result["status"], "updated")
        self.assertEqual((Path(result["backup"]) / "story-codex/SKILL.md").read_bytes(), b"legacy skill")
        for name in installer.SKILL_NAMES:
            self.assertTrue((self.target(name) / "SKILL.md").exists())

    def test_pre_03_legacy_sources_remain_installable(self):
        for version in ("0.1.2", "0.2.0"):
            source = self.root / ("legacy-" + version)
            (source / "scripts").mkdir(parents=True)
            (source / "SKILL.md").write_text("legacy", encoding="utf-8")
            (source / "scripts/story.py").write_text(f'VERSION = "{version}"\n', encoding="utf-8")
            result = installer.install(self.root / ("project-" + version), source=source)
            self.assertEqual(result["status"], "installed")

    def test_one_modified_or_unmanaged_member_prevents_all_changes(self):
        self.install()
        self.change_source()
        user_file = self.target("story-codex-cover") / "SKILL.md"
        user_file.write_bytes(b"user edits")
        with self.assertRaisesRegex(ValueError, "local edits"):
            self.install(True)
        self.assertEqual((self.target("story-codex") / "SKILL.md").read_text(), "story-codex")
        self.assertEqual(user_file.read_bytes(), b"user edits")
        (self.target("story-codex-cover") / installer.MARKER).unlink()
        with self.assertRaisesRegex(ValueError, "no managed manifest"):
            self.install(True)

    def test_partial_publication_rolls_back_whole_suite(self):
        self.install()
        self.change_source()
        real_move = installer.move_directory

        def move(source, destination):
            if Path(source).parent.name.startswith(".story-codex-stage-") and Path(source).name == "story-codex-write":
                raise OSError("third publication failed")
            return real_move(source, destination)

        with patch.object(installer, "move_directory", side_effect=move):
            with self.assertRaisesRegex(OSError, "third publication"):
                self.install(True)
        for name in installer.SKILL_NAMES:
            self.assertEqual((self.target(name) / "SKILL.md").read_text(), name)
        self.assertEqual(list((self.project / ".agents/skills").glob(".story-codex-stage-*")), [])

    def test_partial_first_install_rolls_back_only_suite_and_keeps_other_content(self):
        real_move = installer.move_directory

        def move(source, destination):
            if Path(source).parent.name.startswith(".story-codex-stage-") and Path(source).name == "story-codex-write":
                raise OSError("publication failed")
            return real_move(source, destination)

        with patch.object(installer, "move_directory", side_effect=move), self.assertRaises(OSError):
            self.install()
        self.assertTrue(all(not self.target(name).exists() for name in installer.SKILL_NAMES))
        self.assertEqual(self.other.read_text(encoding="utf-8"), "其他技能")

    def test_edit_to_published_member_is_preserved_and_previous_version_kept(self):
        self.install()
        self.change_source()
        real_move = installer.move_directory

        def move(source, destination):
            result = real_move(source, destination)
            if Path(source).parent.name.startswith(".story-codex-stage-") and Path(source).name == "story-codex":
                (Path(destination) / "SKILL.md").write_bytes(b"concurrent published edit")
            return result

        with patch.object(installer, "move_directory", side_effect=move):
            with self.assertRaisesRegex(ValueError, "preserved at"):
                self.install(True)
        self.assertEqual((self.target("story-codex") / "SKILL.md").read_bytes(), b"concurrent published edit")
        old = list((self.project / ".agents/.story-codex-backups").glob("*/story-codex/SKILL.md"))
        self.assertEqual(len(old), 1)
        self.assertEqual(old[0].read_text(), "story-codex")
        self.assertEqual((self.target("story-codex-plan") / "SKILL.md").read_text(), "story-codex-plan")

    def test_modification_at_rollback_move_is_preserved_in_stage(self):
        self.install()
        self.change_source()
        real_move = installer.move_directory

        def move(source, destination):
            source, destination = Path(source), Path(destination)
            if source.parent.name.startswith(".story-codex-stage-") and source.name == "story-codex-plan":
                raise OSError("publication failed")
            result = real_move(source, destination)
            if destination.parent.name.startswith(".story-codex-stage-"):
                (destination / "SKILL.md").write_bytes(b"edit during withdrawal")
            return result

        with patch.object(installer, "move_directory", side_effect=move):
            with self.assertRaisesRegex(ValueError, "preserved at"):
                self.install(True)
        paths = list((self.project / ".agents/skills").glob(".story-codex-stage-*/story-codex/SKILL.md"))
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].read_bytes(), b"edit during withdrawal")
        self.assertEqual((self.target("story-codex") / "SKILL.md").read_text(), "story-codex")

    def test_source_save_during_staging_prevents_suite_publication(self):
        real_copy = installer.shutil.copyfile

        def copy(source, destination):
            result = real_copy(source, destination)
            if Path(source).name == "SKILL.md":
                (self.source / "story-codex-cover/SKILL.md").write_bytes(b"concurrent source change")
            return result

        with patch.object(installer.shutil, "copyfile", side_effect=copy), self.assertRaisesRegex(ValueError, "Source"):
            self.install()
        self.assertTrue(all(not self.target(name).exists() for name in installer.SKILL_NAMES))


if __name__ == "__main__":
    unittest.main()
