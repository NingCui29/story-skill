import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("claude_installer", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class ClaudeInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-claude-install-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        for relative in installer.SUITE_FILES:
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("fixture: " + relative + "\n").encode("utf-8"))
        (self.source / "story-skill/scripts/story.py").write_bytes(b'VERSION = "0.6.14"\n')
        self.project = self.root / "中文项目"
        self.project.mkdir()

    def install(self, host="claude-code", update=False, project=None):
        return installer.install(project or self.project, update, self.source, host=host)

    def parent(self, host="claude-code", project=None):
        return (project or self.project) / installer.HOST_DIRECTORIES[host] / "skills"

    def snapshot(self, path):
        return {file.relative_to(path).as_posix(): file.read_bytes()
                for file in path.rglob("*") if file.is_file()}

    def change_source(self):
        for name in installer.SKILL_NAMES:
            (self.source / name / "SKILL.md").write_bytes(b"reviewed update\n")

    def run_cli(self, *arguments):
        output = io.StringIO()
        original_install = installer.install

        def from_fixture(project, update=False, *, host="codex", scope="project"):
            return original_install(project, update, self.source, host=host, scope=scope)

        with patch("sys.argv", ["install.py", *map(str, arguments)]), \
                patch.object(installer, "install", side_effect=from_fixture), \
                contextlib.redirect_stdout(output):
            code = installer.main()
        return code, json.loads(output.getvalue())

    def test_all_eight_skills_are_copied_verbatim_and_repeat_is_unchanged(self):
        config = self.project / "CLAUDE.md"
        config.write_bytes(b"existing instructions\n")
        book = self.project / "books/novel/chapter.md"
        book.parent.mkdir(parents=True)
        book.write_bytes(b"existing novel\n")
        receipt = self.install()
        self.assertEqual(receipt["status"], "installed")
        self.assertEqual(receipt["host"], "claude-code")
        self.assertEqual(receipt["path"], str(self.parent()))
        self.assertEqual(receipt["skills"], list(installer.SKILL_NAMES))
        self.assertEqual(receipt["files"], len(installer.SUITE_FILES))
        for name in installer.SKILL_NAMES:
            self.assertEqual(installer.inventory(self.parent() / name),
                             installer.inventory(self.source / name))
            self.assertTrue((self.parent() / name / "agents/openai.yaml").is_file())
        self.assertEqual(self.install()["status"], "unchanged")
        self.assertFalse((self.project / ".agents").exists())
        self.assertEqual(config.read_bytes(), b"existing instructions\n")
        self.assertEqual(book.read_bytes(), b"existing novel\n")

    def test_default_api_keeps_codex_and_host_is_keyword_only(self):
        receipt = installer.install(self.project, False, self.source)
        self.assertEqual(receipt["path"], str(self.parent("codex")))
        self.assertFalse((self.project / ".claude").exists())
        with self.assertRaises(TypeError):
            installer.install(self.project, False, self.source, "claude-code")
        with self.assertRaisesRegex(ValueError, "Unsupported installation host"):
            self.install(host="unsupported")

    def test_hosts_are_isolated_during_install_and_update(self):
        self.install("codex")
        codex_before = self.snapshot(self.project / ".agents")
        self.install()
        self.assertEqual(self.snapshot(self.project / ".agents"), codex_before)
        self.change_source()
        receipt = self.install(update=True)
        self.assertEqual(receipt["status"], "updated")
        self.assertEqual(self.snapshot(self.project / ".agents"), codex_before)
        claude_before = self.snapshot(self.project / ".claude")
        self.install("codex", update=True)
        self.assertEqual(self.snapshot(self.project / ".claude"), claude_before)

    def test_update_requires_flag_and_preserves_host_specific_backup(self):
        self.install()
        before = {name: installer.inventory(self.parent() / name) for name in installer.SKILL_NAMES}
        self.change_source()
        with self.assertRaisesRegex(ValueError, "--update"):
            self.install()
        receipt = self.install(update=True)
        backup = Path(receipt["backup"])
        self.assertEqual(backup.parent, self.project / ".claude/.story-skill-backups")
        for name in installer.SKILL_NAMES:
            self.assertEqual(installer.inventory(backup / name), before[name])
            self.assertEqual(installer.inventory(self.parent() / name),
                             installer.inventory(self.source / name))
        self.assertEqual(list(self.parent().glob(".story-skill-stage-*")), [])
        self.assertTrue((self.parent() / ".story-skill-install.lock").is_file())
        self.assertFalse((self.project / ".agents").exists())

    def test_local_edits_and_unmanaged_skills_are_preserved(self):
        self.install("codex")
        self.install()
        edited = self.parent() / "story-skill-plan/SKILL.md"
        edited.write_bytes(b"my edits\n")
        self.change_source()
        with self.assertRaisesRegex(ValueError, "local edits"):
            self.install(update=True)
        self.assertEqual(edited.read_bytes(), b"my edits\n")
        self.assertEqual(self.install("codex", update=True)["status"], "updated")
        (self.parent() / "story-skill-plan" / installer.MARKER).unlink()
        with self.assertRaisesRegex(ValueError, "no managed manifest"):
            self.install(update=True)
        self.assertEqual(edited.read_bytes(), b"my edits\n")

    def test_old_suite_is_checked_only_in_selected_host(self):
        old = self.parent("codex") / "story-codex-plan/SKILL.md"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"old suite\n")
        self.assertEqual(self.install()["status"], "installed")
        with self.assertRaisesRegex(ValueError, "earlier writing suite"):
            self.install("codex")
        self.assertEqual(old.read_bytes(), b"old suite\n")

    def test_omitted_skill_in_selected_host_is_rejected(self):
        self.install()
        before = self.snapshot(self.project / ".claude")
        files = installer.suite_inventory(self.source)
        files.pop("story-skill-cover")
        with patch.object(installer, "suite_inventory", return_value=files), \
                self.assertRaisesRegex(ValueError, "omit existing suite skill"):
            self.install(update=True)
        self.assertEqual(self.snapshot(self.project / ".claude"), before)

    def test_host_locks_do_not_block_each_other(self):
        with installer.installation_lock(self.project, host="codex"):
            self.assertEqual(self.install()["status"], "installed")
            with self.assertRaisesRegex(ValueError, "Another installation"):
                self.install("codex")

    def test_failed_partial_update_restores_all_claude_skills(self):
        self.install("codex")
        self.install()
        before = self.snapshot(self.project)
        self.change_source()
        original_move = installer.move_directory

        def fail_third_publication(source, destination):
            if Path(source).parent.name.startswith(".story-skill-stage-") and Path(source).name == "story-skill-write":
                raise OSError("simulated third publication failure")
            return original_move(source, destination)

        with patch.object(installer, "move_directory", side_effect=fail_third_publication), \
                self.assertRaisesRegex(OSError, "third publication failure"):
            self.install(update=True)
        self.assertEqual(self.snapshot(self.project), before)
        self.assertEqual(list(self.parent().glob(".story-skill-stage-*")), [])

    def test_linked_host_paths_are_rejected_without_writing_through_links(self):
        for relative in (".claude", ".claude/skills", ".claude/skills/story-skill"):
            with self.subTest(relative=relative):
                project = self.root / relative.replace("/", "-")
                project.mkdir()
                outside = self.root / (project.name + "-outside")
                outside.mkdir()
                sentinel = outside / "sentinel"
                sentinel.write_bytes(b"untouched\n")
                link = project / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(outside, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "linked installation path"):
                    self.install(project=project)
                self.assertEqual(self.snapshot(outside), {"sentinel": b"untouched\n"})

    def test_linked_lock_and_backup_are_rejected(self):
        self.install()
        lock = self.parent() / ".story-skill-install.lock"
        lock.unlink()
        outside = self.root / "outside-lock"
        outside.write_bytes(b"untouched\n")
        lock.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "linked installation path"):
            self.install()
        self.assertEqual(outside.read_bytes(), b"untouched\n")
        lock.unlink()
        backup = self.project / ".claude/.story-skill-backups"
        outside_backup = self.root / "outside-backup"
        outside_backup.mkdir()
        backup.symlink_to(outside_backup, target_is_directory=True)
        self.change_source()
        before = self.snapshot(self.parent())
        with self.assertRaisesRegex(ValueError, "linked installation path"):
            self.install(update=True)
        self.assertEqual(self.snapshot(self.parent()), before | {".story-skill-install.lock": b""})
        self.assertEqual(list(outside_backup.iterdir()), [])
        self.assertEqual(list(self.parent().glob(".story-skill-stage-*")), [])

    def test_cli_project_selection_and_default_host(self):
        code, receipt = self.run_cli("--host", "claude-code", "--project", self.project)
        self.assertEqual(code, 0)
        self.assertEqual(receipt["path"], str(self.parent()))
        code, receipt = self.run_cli("--project", self.project)
        self.assertEqual(code, 0)
        self.assertEqual(receipt["path"], str(self.parent("codex")))

    def test_cli_user_uses_home_and_update_keeps_backups_there(self):
        user_home = self.root / "fake-home"
        user_home.mkdir()
        with patch.object(installer.Path, "home", return_value=user_home):
            code, receipt = self.run_cli("--host", "claude-code", "--user")
            self.assertEqual(code, 0)
            self.assertEqual(receipt["path"], str(self.parent(project=user_home)))
            self.change_source()
            code, receipt = self.run_cli("--host", "claude-code", "--user", "--update")
            self.assertEqual(code, 0)
            self.assertEqual(receipt["status"], "updated")
            self.assertEqual(Path(receipt["backup"]).parent, user_home / ".claude/.story-skill-backups")
        self.assertFalse((self.project / ".claude").exists())

    def test_cli_rejects_missing_or_conflicting_destinations_and_unknown_hosts(self):
        for arguments in ([], ["--project", str(self.project), "--user"],
                          ["--project", str(self.project), "--host", "unknown"]):
            with self.subTest(arguments=arguments), \
                    patch("sys.argv", ["install.py", *arguments]), \
                    patch.object(installer, "install") as install, \
                    contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit) as result:
                installer.main()
            self.assertEqual(result.exception.code, 2)
            install.assert_not_called()


if __name__ == "__main__":
    unittest.main()
