import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer_races", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallationRaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-install-race-test-")
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "skills"
        for relative in installer.SUITE_FILES:
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("reviewed file: " + relative + "\n").encode())
        (self.source / "story-skill/SKILL.md").write_bytes(b"original skill\n")
        self.runtime = self.source / "story-skill/scripts/story.py"
        self.runtime.write_bytes(b'VERSION = "0.6.0"\n# v1\n')
        self.project = self.root / "project"
        installer.install(self.project, source=self.source)
        self.target = self.project / ".agents/skills/story-skill"
        self.runtime.write_bytes(b'VERSION = "0.6.0"\n# v2\n')

    def tearDown(self):
        self.temp.cleanup()

    def update(self):
        return installer.install(self.project, True, self.source)

    def during_staging(self, action):
        paused, resume = threading.Event(), threading.Event()
        result = {}
        real_copy = installer.shutil.copyfile

        def copy(source, destination):
            if not paused.is_set():
                paused.set()
                if not resume.wait(10):
                    raise RuntimeError("Test synchronization timeout")
            return real_copy(source, destination)

        def update():
            try:
                result["receipt"] = self.update()
            except BaseException as error:
                result["error"] = error

        with patch.object(installer.shutil, "copyfile", side_effect=copy):
            worker = threading.Thread(target=update)
            worker.start()
            try:
                self.assertTrue(paused.wait(10), "Installer never reached staging")
                action()
            finally:
                resume.set()
                worker.join(10)
            self.assertFalse(worker.is_alive(), "Installer did not finish")
        return result

    def backups(self):
        return list((self.project / ".agents/.story-skill-backups").glob("*"))

    def assert_original_runtime(self):
        self.assertEqual((self.target / "scripts/story.py").read_bytes(), b'VERSION = "0.6.0"\n# v1\n')

    def test_source_save_during_copy_is_rejected_without_bad_manifest(self):
        result = self.during_staging(lambda: self.runtime.write_bytes(b'VERSION = "0.6.0"\n# v3 concurrent save\n'))
        self.assertIsInstance(result.get("error"), ValueError)
        self.assertIn("Source", str(result["error"]))
        self.assert_original_runtime()
        marker = json.loads((self.target / installer.MARKER).read_text(encoding="utf-8"))
        self.assertEqual(installer.inventory(self.target), marker["files"])
        self.assertEqual(self.update()["status"], "updated")

    def test_source_new_file_after_copy_is_detected_before_publication(self):
        real_copy = installer.shutil.copyfile

        def copy(source, destination):
            result = real_copy(source, destination)
            (self.source / "story-skill/new-reference.md").write_bytes(b"new source content")
            return result

        with patch.object(installer.shutil, "copyfile", side_effect=copy):
            with self.assertRaisesRegex(ValueError, "unreviewed files"):
                self.update()
        self.assert_original_runtime()
        self.assertEqual(self.backups(), [])

    def test_target_save_during_staging_remains_active(self):
        user_bytes = b"user edit saved during update\r\n"
        result = self.during_staging(lambda: (self.target / "SKILL.md").write_bytes(user_bytes))
        self.assertIsInstance(result.get("error"), ValueError)
        self.assertEqual((self.target / "SKILL.md").read_bytes(), user_bytes)
        self.assert_original_runtime()
        self.assertEqual(self.backups(), [])

    def test_staged_bytes_changed_after_initial_validation_restore_original(self):
        real_validate = installer.validate_stage
        validations = 0

        def validate(source, stage, files, manifest):
            nonlocal validations
            real_validate(source, stage, files, manifest)
            validations += 1
            if validations == 1:
                (stage / "SKILL.md").write_bytes(b"changed after the initial stage check")

        with patch.object(installer, "validate_stage", side_effect=validate):
            with self.assertRaisesRegex(ValueError, "Staged package differs"):
                self.update()
        self.assertEqual((self.target / "SKILL.md").read_bytes(), b"original skill\n")
        self.assert_original_runtime()
        self.assertEqual(self.backups(), [])

    def test_target_edit_at_backup_move_is_verified_and_restored(self):
        user_bytes = b"user edit at backup move\r\n"
        real_move = installer.move_directory

        def move(source, destination):
            if Path(source) == self.target:
                (self.target / "SKILL.md").write_bytes(user_bytes)
            return real_move(source, destination)

        with patch.object(installer, "move_directory", side_effect=move):
            with self.assertRaisesRegex(ValueError, "local edits"):
                self.update()
        self.assertEqual((self.target / "SKILL.md").read_bytes(), user_bytes)
        self.assert_original_runtime()
        self.assertEqual(self.backups(), [])

    def test_edit_to_moved_backup_is_verified_and_restored(self):
        user_bytes = b"user edit visible only after rename\r\n"
        real_move = installer.move_directory

        def move(source, destination):
            real_move(source, destination)
            if Path(source) == self.target:
                (Path(destination) / "SKILL.md").write_bytes(user_bytes)

        with patch.object(installer, "move_directory", side_effect=move):
            with self.assertRaisesRegex(ValueError, "local edits"):
                self.update()
        self.assertEqual((self.target / "SKILL.md").read_bytes(), user_bytes)
        self.assert_original_runtime()

    def test_new_target_before_publication_is_preserved_with_old_backup(self):
        real_move = installer.move_directory

        def move(source, destination):
            if Path(source).parent.name.startswith(".story-skill-stage-"):
                self.target.mkdir()
                (self.target / "new-owner.txt").write_bytes(b"new target must survive")
            return real_move(source, destination)

        with patch.object(installer, "move_directory", side_effect=move):
            with self.assertRaisesRegex(ValueError, "preserved at"):
                self.update()
        self.assertEqual((self.target / "new-owner.txt").read_bytes(), b"new target must survive")
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual((self.backups()[0] / "story-skill/scripts/story.py").read_bytes(), b'VERSION = "0.6.0"\n# v1\n')

    def test_stage_edit_inside_publication_is_detected_and_both_versions_survive(self):
        changed_bytes = b"stage changed just before its real rename\r\n"
        real_move = installer.move_directory

        def move(source, destination):
            if Path(source).parent.name.startswith(".story-skill-stage-"):
                (Path(source) / "SKILL.md").write_bytes(changed_bytes)
            return real_move(source, destination)

        with patch.object(installer, "move_directory", side_effect=move):
            with self.assertRaisesRegex(ValueError, "Recovery did not replace"):
                self.update()
        self.assertEqual((self.target / "SKILL.md").read_bytes(), changed_bytes)
        self.assertEqual((self.target / "scripts/story.py").read_bytes(), b'VERSION = "0.6.0"\n# v2\n')
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual((self.backups()[0] / "story-skill/SKILL.md").read_bytes(), b"original skill\n")
        self.assertEqual((self.backups()[0] / "story-skill/scripts/story.py").read_bytes(), b'VERSION = "0.6.0"\n# v1\n')

    def test_target_recreated_during_recovery_is_not_replaced(self):
        real_move = installer.move_directory

        def move(source, destination):
            if Path(source).parent.name.startswith(".story-skill-stage-"):
                raise OSError("simulated publication failure")
            if Path(source).parent.parent.name == ".story-skill-backups":
                self.target.mkdir()
                (self.target / "new-owner.txt").write_bytes(b"created during rollback")
            return real_move(source, destination)

        with patch.object(installer, "move_directory", side_effect=move):
            with self.assertRaisesRegex(ValueError, "Recovery did not replace"):
                self.update()
        self.assertEqual((self.target / "new-owner.txt").read_bytes(), b"created during rollback")
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual((self.backups()[0] / "story-skill/SKILL.md").read_bytes(), b"original skill\n")

    @unittest.skipUnless(os.name == "nt", "Windows rename provides the no-replace guarantee tested here")
    def test_windows_publication_refuses_an_empty_target_created_inside_rename(self):
        real_rename = installer.os.rename

        def rename(source, destination):
            if Path(source).parent.name.startswith(".story-skill-stage-"):
                self.target.mkdir()
            return real_rename(source, destination)

        with patch.object(installer.os, "rename", side_effect=rename):
            with self.assertRaisesRegex(ValueError, "preserved at"):
                self.update()
        self.assertTrue(self.target.is_dir())
        self.assertEqual(list(self.target.iterdir()), [])
        self.assertEqual(len(self.backups()), 1)

    def test_concurrent_installer_is_rejected_and_first_completes(self):
        def second_installer():
            with self.assertRaisesRegex(ValueError, "Another installation"):
                self.update()

        result = self.during_staging(second_installer)
        self.assertNotIn("error", result)
        self.assertEqual(result["receipt"]["status"], "updated")

    def test_process_exit_releases_lock_without_deleting_lock_file(self):
        code = ("import importlib.util,os,pathlib,sys; "
                "spec=importlib.util.spec_from_file_location('installer_exit',sys.argv[1]); "
                "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
                "lock=module.installation_lock(pathlib.Path(sys.argv[2])); lock.__enter__(); os._exit(0)")
        child = subprocess.run([sys.executable, "-B", "-c", code, str(ROOT / "scripts/install.py"), str(self.project)],
                               capture_output=True, timeout=10)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertTrue((self.project / ".agents/skills/.story-skill-install.lock").is_file())
        self.assertEqual(self.update()["status"], "updated")


if __name__ == "__main__":
    unittest.main()
