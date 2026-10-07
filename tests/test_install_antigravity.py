import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("antigravity_installer", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

HOST_PATHS = {
    ("antigravity", "project"): ".agents/skills",
    ("antigravity-cli", "project"): ".agents/skills",
    ("antigravity", "user"): ".gemini/config/skills",
    ("antigravity-cli", "user"): ".gemini/antigravity-cli/skills",
}


class AntigravityInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-antigravity-install-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        for relative in installer.SUITE_FILES:
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("fixture: " + relative + "\n").encode("utf-8"))
        (self.source / "story-skill/scripts/story.py").write_bytes(b'VERSION = "0.6.11"\n')
        self.project = self.root / "中文项目"
        self.project.mkdir()
        self.user_home = self.root / "fake-home"
        self.user_home.mkdir()

    def install(self, host="antigravity", scope="project", update=False, root=None):
        target_root = root or (self.user_home if scope == "user" else self.project)
        return installer.install(target_root, update, self.source, host=host, scope=scope)

    def target(self, host="antigravity", scope="project", root=None):
        return (root or (self.user_home if scope == "user" else self.project)) / HOST_PATHS[host, scope]

    def snapshot(self, directory):
        return {path.relative_to(directory).as_posix(): path.read_bytes()
                for path in directory.rglob("*") if path.is_file()}

    def run_cli(self, *arguments):
        original_install = installer.install
        output = io.StringIO()

        def from_fixture(project, update=False, *, host="codex", scope="project"):
            return original_install(project, update, self.source, host=host, scope=scope)

        with patch("sys.argv", ["install.py", *map(str, arguments)]), \
                patch.object(installer, "install", side_effect=from_fixture), \
                patch.object(installer.Path, "home", return_value=self.user_home), \
                contextlib.redirect_stdout(output):
            status = installer.main()
        return status, json.loads(output.getvalue())

    def test_project_hosts_share_complete_codex_suite_and_are_idempotent(self):
        config = self.project / "GEMINI.md"
        config.write_bytes(b"existing instructions\n")
        book = self.project / "books/story/chapter.md"
        book.parent.mkdir(parents=True)
        book.write_bytes(b"existing novel\n")
        first = self.install()
        self.assertEqual(first["path"], str(self.target()))
        self.assertEqual(first["skills"], list(installer.SKILL_NAMES))
        for name in installer.SKILL_NAMES:
            self.assertEqual(installer.inventory(self.target() / name),
                             installer.inventory(self.source / name))
        self.assertEqual(self.install(host="antigravity-cli")["status"], "unchanged")
        self.assertEqual(installer.install(self.project, False, self.source)["status"], "unchanged")
        self.assertFalse((self.project / ".gemini").exists())
        self.assertEqual(config.read_bytes(), b"existing instructions\n")
        self.assertEqual(book.read_bytes(), b"existing novel\n")

    def test_api_defaults_and_scope_validation_preserve_existing_callers(self):
        receipt = installer.install(self.project, False, self.source, host="antigravity")
        self.assertEqual(receipt["path"], str(self.target()))
        claude = installer.install(self.project, False, self.source, host="claude-code")
        self.assertEqual(claude["path"], str(self.project / ".claude/skills"))
        with self.assertRaisesRegex(ValueError, "scope"):
            self.install(scope="unsupported")
        with self.assertRaises(TypeError):
            installer.install(self.project, False, self.source, "antigravity", "user")

    def test_cli_project_hosts_select_shared_project_directory(self):
        for host, expected_status in (("antigravity", "installed"), ("antigravity-cli", "unchanged")):
            with self.subTest(host=host):
                status, receipt = self.run_cli("--host", host, "--project", self.project)
                self.assertEqual(status, 0)
                self.assertEqual(receipt["status"], expected_status)
                self.assertEqual(receipt["path"], str(self.target(host=host)))
        self.assertFalse((self.user_home / ".gemini").exists())

    def test_cli_user_hosts_have_distinct_global_targets(self):
        sentinel = self.user_home / ".claude/skills/other-skill/SKILL.md"
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(b"other host\n")
        for host in ("antigravity", "antigravity-cli"):
            with self.subTest(host=host):
                status, receipt = self.run_cli("--host", host, "--user")
                self.assertEqual(status, 0)
                self.assertEqual(receipt["status"], "installed")
                self.assertEqual(receipt["path"], str(self.target(host=host, scope="user")))
                for name in installer.SKILL_NAMES:
                    self.assertEqual(installer.inventory(self.target(host, "user") / name),
                                     installer.inventory(self.source / name))
        self.assertNotEqual(self.target("antigravity", "user"), self.target("antigravity-cli", "user"))
        self.assertFalse((self.user_home / ".agents").exists())
        self.assertFalse((self.project / ".agents").exists())
        self.assertEqual(sentinel.read_bytes(), b"other host\n")

    def test_project_lock_is_shared_with_codex_and_user_locks_are_distinct(self):
        with installer.installation_lock(self.project, host="codex"):
            for host in ("antigravity", "antigravity-cli"):
                with self.subTest(host=host), self.assertRaisesRegex(ValueError, "Another installation"):
                    self.install(host=host)
        with installer.installation_lock(self.user_home, host="antigravity", scope="user"):
            with self.assertRaisesRegex(ValueError, "Another installation"):
                self.install(scope="user")
            self.assertEqual(self.install(host="antigravity-cli", scope="user")["status"], "installed")

    def test_user_updates_keep_backups_in_each_scope_and_preserve_other_hosts(self):
        for host in ("antigravity", "antigravity-cli"):
            self.install(host=host, scope="user")
        target = self.target(scope="user")
        old_file = (target / "story-skill/SKILL.md").read_bytes()
        other_before = self.snapshot(self.target("antigravity-cli", "user"))
        (self.source / "story-skill/SKILL.md").write_bytes(b"reviewed update\n")
        with self.assertRaisesRegex(ValueError, "--update"):
            self.install(scope="user")
        status, receipt = self.run_cli("--host", "antigravity", "--user", "--update")
        self.assertEqual(status, 0)
        self.assertEqual(receipt["status"], "updated")
        backup = Path(receipt["backup"])
        self.assertEqual(backup.parent, self.user_home / ".gemini/config/.story-skill-backups")
        self.assertEqual((backup / "story-skill/SKILL.md").read_bytes(), old_file)
        self.assertEqual(self.snapshot(self.target("antigravity-cli", "user")), other_before)
        updated_before = self.snapshot(self.user_home / ".gemini/config")
        receipt = self.install(host="antigravity-cli", scope="user", update=True)
        self.assertEqual(Path(receipt["backup"]).parent,
                         self.user_home / ".gemini/antigravity-cli/.story-skill-backups")
        self.assertEqual(self.snapshot(self.user_home / ".gemini/config"), updated_before)
        edited = target / "story-skill-plan/SKILL.md"
        edited.write_bytes(b"my local edits\n")
        before = self.snapshot(target)
        with self.assertRaisesRegex(ValueError, "local edits"):
            self.install(scope="user", update=True)
        self.assertEqual(self.snapshot(target), before)
        self.assertEqual(self.install(host="antigravity-cli", scope="user")["status"], "unchanged")

    def test_linked_paths_in_each_scope_are_rejected(self):
        cases = (("antigravity", "project", ".agents/skills"),
                 ("antigravity-cli", "project", ".agents/skills/story-skill"),
                 ("antigravity", "user", ".gemini/config"),
                 ("antigravity", "user", ".gemini/config/skills"),
                 ("antigravity-cli", "user", ".gemini/antigravity-cli"),
                 ("antigravity-cli", "user", ".gemini/antigravity-cli/skills"))
        for index, (host, scope, relative) in enumerate(cases):
            with self.subTest(host=host, scope=scope, relative=relative):
                target_root = self.root / f"linked-root-{index}"
                target_root.mkdir()
                outside = self.root / f"outside-{index}"
                outside.mkdir()
                (outside / "sentinel").write_bytes(b"untouched\n")
                link = target_root / relative
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(outside, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "linked installation path"):
                    self.install(host=host, scope=scope, root=target_root)
                self.assertEqual(self.snapshot(outside), {"sentinel": b"untouched\n"})


if __name__ == "__main__":
    unittest.main()
