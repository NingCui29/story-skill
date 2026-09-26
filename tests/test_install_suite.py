import importlib.util
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
        for relative in installer.SUITE_FILES:
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("reviewed file: " + relative + "\n").encode("utf-8"))
        (self.source / "story-skill/scripts/story.py").write_text('VERSION = "0.6.1"\n', encoding="utf-8")
        self.project.mkdir()
        self.book = self.project / "books/我的小说/正文/第1章.md"
        self.book.parent.mkdir(parents=True)
        self.book.write_text("不能改动的小说正文", encoding="utf-8")
        self.other = self.project / ".agents/skills/other-skill/SKILL.md"
        self.other.parent.mkdir(parents=True)
        self.other.write_text("其他技能", encoding="utf-8")

    def install(self, update=False, source=None):
        return installer.install(self.project, update, source or self.source)

    def target(self, name):
        return self.project / ".agents/skills" / name

    def snapshot(self):
        return {path.relative_to(self.project).as_posix(): path.read_bytes()
                for path in self.project.rglob("*") if path.is_file()}

    def change_source(self):
        for name in installer.SKILL_NAMES:
            (self.source / name / "SKILL.md").write_text(name + " updated", encoding="utf-8")

    def test_install_all_dependencies_preserves_book_and_other_skills(self):
        result = self.install()
        self.assertEqual(result["skills"], list(installer.SKILL_NAMES))
        self.assertEqual(result["files"], len(installer.SUITE_FILES))
        for name in installer.SKILL_NAMES:
            self.assertEqual(installer.inventory(self.source / name), installer.inventory(self.target(name)))
        self.assertEqual(self.book.read_text(encoding="utf-8"), "不能改动的小说正文")
        self.assertEqual(self.other.read_text(encoding="utf-8"), "其他技能")
        self.assertEqual(self.install()["status"], "unchanged")

    def test_core_path_redirects_to_complete_suite(self):
        result = self.install(source=self.source / "story-skill")
        self.assertEqual(result["skills"], list(installer.SKILL_NAMES))
        (self.source / "story-skill-cover/SKILL.md").unlink()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.install(True, self.source / "story-skill")

    def test_missing_dependency_or_reference_stops_before_install(self):
        for relative in ("story-skill/scripts/story_storage.py",
                         "story-skill/scripts/story_workbench.py",
                         "story-skill-plan/references/fanqie-tags.md",
                         "story-skill-publish/SKILL.md"):
            with self.subTest(relative=relative):
                path = self.source / relative
                raw = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(ValueError, "incomplete"):
                    self.install()
                self.assertFalse(self.target("story-skill").exists())
                path.write_bytes(raw)

    def test_private_draft_cannot_be_installed(self):
        (self.source / "story-skill/private-draft.md").write_bytes(b"private book")
        with self.assertRaisesRegex(ValueError, "unreviewed files"):
            self.install()
        self.assertFalse(self.target("story-skill").exists())

    def test_install_and_archive_share_current_manifest(self):
        spec = importlib.util.spec_from_file_location("suite_package_manifest", ROOT / "scripts/package.py")
        package = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(package)
        self.assertEqual(installer.SUITE_FILES, package.SUITE_FILES)
        for version, size, has_workbench in (("0.6.0", 38, False), ("0.6.1", 39, True), ("0.6.2", 39, True), ("0.6.3", 39, True)):
            with self.subTest(version=version):
                files = installer.suite_files(version)
                self.assertEqual(installer.skill_names(version), package.skill_names(version))
                self.assertEqual(files, package.suite_files(version))
                self.assertEqual(len(files), size)
                self.assertEqual("story-skill/scripts/story_workbench.py" in files, has_workbench)
        self.assertEqual(installer.SUITE_FILES, installer.SUITE_FILES_V061)
        self.assertEqual(installer.SKILL_NAMES, installer.SKILL_NAMES_V061)
        for version in ("0.5.11", "0.6.4", "0.6.99"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "no reviewed suite layout"):
                installer.suite_files(version)
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "no reviewed suite layout"):
                installer.skill_names(version)

    def test_published_v060_installs_legacy_layout_without_workbench(self):
        workbench = self.source / "story-skill/scripts/story_workbench.py"
        workbench.unlink()
        (self.source / "story-skill/scripts/story.py").write_text('VERSION = "0.6.0"\n', encoding="utf-8")
        result = self.install()
        self.assertEqual(result["files"], 38)
        self.assertFalse((self.target("story-skill") / "scripts/story_workbench.py").exists())

    def test_planning_reference_and_publication_runtime_are_installed(self):
        self.install()
        for relative in ("story-skill-plan/references/fanqie-tags.md",
                         "story-skill/scripts/story_publish.py",
                         "story-skill/scripts/story_workbench.py"):
            self.assertEqual((self.project / ".agents/skills" / relative).read_bytes(),
                             (self.source / relative).read_bytes())

    def test_update_keeps_exact_backup_and_all_eight_skills(self):
        self.install()
        before = {name: installer.inventory(self.target(name)) for name in installer.SKILL_NAMES}
        self.change_source()
        upgraded = self.install(True)
        self.assertEqual(upgraded["status"], "updated")
        self.assertEqual(upgraded["skills"], list(installer.SKILL_NAMES))
        self.assertEqual(upgraded["files"], len(installer.SUITE_FILES))
        backup = Path(upgraded["backup"])
        for name in installer.SKILL_NAMES:
            self.assertEqual(installer.inventory(backup / name), before[name])
            self.assertEqual(installer.inventory(self.target(name)), installer.inventory(self.source / name))
        self.assertEqual(self.install(True)["status"], "unchanged")

    def test_old_version_and_single_skill_source_are_rejected(self):
        runtime = self.source / "story-skill/scripts/story.py"
        runtime.write_text('VERSION = "0.5.11"\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no reviewed suite layout"):
            self.install()
        runtime.write_text('VERSION = "0.6.1"\n', encoding="utf-8")
        single = self.root / "single-skill"
        (single / "scripts").mkdir(parents=True)
        (single / "SKILL.md").write_text("partial", encoding="utf-8")
        (single / "scripts/story.py").write_text('VERSION = "0.6.1"\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "full supported suite"):
            self.install(source=single)
        self.assertFalse(self.target("story-skill").exists())

    def test_unknown_suite_version_is_rejected_before_writing_targets(self):
        runtime = self.source / "story-skill/scripts/story.py"
        for version in ("0.6.00", "0.6.4", "0.6.99", "0.7.0", "1.0.0"):
            with self.subTest(version=version):
                runtime.write_text(f'VERSION = "{version}"\n', encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "no reviewed suite layout"):
                    self.install()
        self.assertFalse(self.target("story-skill").exists())

    def test_modified_or_unmanaged_member_prevents_all_changes(self):
        self.install()
        self.change_source()
        user_file = self.target("story-skill-cover") / "SKILL.md"
        user_file.write_bytes(b"user edits")
        with self.assertRaisesRegex(ValueError, "local edits"):
            self.install(True)
        self.assertEqual(user_file.read_bytes(), b"user edits")
        (self.target("story-skill-cover") / installer.MARKER).unlink()
        with self.assertRaisesRegex(ValueError, "no managed manifest"):
            self.install(True)

    def test_partial_publication_rolls_back_whole_suite(self):
        self.install()
        before = self.snapshot()
        self.change_source()
        real_move = installer.move_directory

        def move(source, destination):
            if Path(source).parent.name.startswith(".story-skill-stage-") and Path(source).name == "story-skill-write":
                raise OSError("third publication failed")
            return real_move(source, destination)

        with patch.object(installer, "move_directory", side_effect=move):
            with self.assertRaisesRegex(OSError, "third publication"):
                self.install(True)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(list((self.project / ".agents/skills").glob(".story-skill-stage-*")), [])

    def test_partial_first_install_keeps_other_content(self):
        real_move = installer.move_directory

        def move(source, destination):
            if Path(source).parent.name.startswith(".story-skill-stage-") and Path(source).name == "story-skill-write":
                raise OSError("publication failed")
            return real_move(source, destination)

        with patch.object(installer, "move_directory", side_effect=move), self.assertRaises(OSError):
            self.install()
        self.assertTrue(all(not self.target(name).exists() for name in installer.SKILL_NAMES))
        self.assertEqual(self.other.read_text(encoding="utf-8"), "其他技能")

    def test_published_edit_is_preserved_with_prior_backup(self):
        self.install()
        self.change_source()
        real_move = installer.move_directory

        def move(source, destination):
            result = real_move(source, destination)
            if Path(source).parent.name.startswith(".story-skill-stage-") and Path(source).name == "story-skill":
                (Path(destination) / "SKILL.md").write_bytes(b"concurrent published edit")
            return result

        with patch.object(installer, "move_directory", side_effect=move):
            with self.assertRaisesRegex(ValueError, "preserved at"):
                self.install(True)
        self.assertEqual((self.target("story-skill") / "SKILL.md").read_bytes(), b"concurrent published edit")
        old = list((self.project / ".agents/.story-skill-backups").glob("*/story-skill/SKILL.md"))
        self.assertEqual(len(old), 1)
        self.assertEqual(old[0].read_text(), "reviewed file: story-skill/SKILL.md\n")

    def test_source_save_during_staging_prevents_publication(self):
        real_copy = installer.shutil.copyfile

        def copy(source, destination):
            result = real_copy(source, destination)
            if Path(source).name == "SKILL.md":
                (self.source / "story-skill-cover/SKILL.md").write_bytes(b"concurrent source change")
            return result

        with patch.object(installer.shutil, "copyfile", side_effect=copy), self.assertRaisesRegex(ValueError, "Source"):
            self.install()
        self.assertTrue(all(not self.target(name).exists() for name in installer.SKILL_NAMES))


if __name__ == "__main__":
    unittest.main()
