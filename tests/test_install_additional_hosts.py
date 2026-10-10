import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("additional_hosts_installer", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

# Reviewed host locations, independent of the implementation's host mappings.
# Copilot's VS Code Agent and CLI both consume this same copilot installation.
HOST_PATHS = {
    ("cursor", "project"): ".cursor/skills",
    ("cursor", "user"): ".cursor/skills",
    ("trae", "project"): ".trae/skills",
    ("trae", "user"): ".trae/skills",
    ("trae-cn", "project"): ".trae/skills",
    ("trae-cn", "user"): ".trae-cn/skills",
    ("trae-cli", "project"): ".traecli/skills",
    ("trae-cli", "user"): ".traecli/skills",
    ("codebuddy", "project"): ".codebuddy/skills",
    ("codebuddy", "user"): ".codebuddy/skills",
    ("opencode", "project"): ".opencode/skills",
    ("opencode", "user"): ".config/opencode/skills",
    ("copilot", "project"): ".github/skills",
    ("copilot", "user"): ".copilot/skills",
    ("gemini-cli", "project"): ".gemini/skills",
    ("gemini-cli", "user"): ".gemini/skills",
    ("cline", "project"): ".cline/skills",
    ("cline", "user"): ".cline/skills",
    # Retained Windsurf legacy target; current Devin has its own native locations.
    ("windsurf", "project"): ".windsurf/skills",
    ("windsurf", "user"): ".codeium/windsurf/skills",
    ("devin", "project"): ".devin/skills",
    ("devin", "user"): ".config/devin/skills",
}
HOSTS = tuple(dict.fromkeys(host for host, _ in HOST_PATHS))


def snapshot(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}


class AdditionalHostInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-additional-host-install-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        runtime = ROOT / "skills/story-skill/scripts/story.py"
        version = installer.runtime_version(runtime.read_bytes())
        for relative in installer.suite_files(version):
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("中文载荷: " + relative + "\r\n").encode("utf-8"))
        (self.source / "story-skill/scripts/story.py").write_text(
            'VERSION = "' + version + '"\n', encoding="utf-8")
        self.payload = snapshot(self.source)
        self.names = tuple(installer.skill_names(version))

    def target(self, root, host, scope):
        return root / HOST_PATHS[host, scope]

    def run_cli(self, root, host, scope, update=False):
        original_install = installer.install
        output = io.StringIO()

        def from_fixture(project, update=False, *, host="codex", scope="project"):
            return original_install(project, update, self.source, host=host, scope=scope)

        arguments = ["install.py", "--host", host]
        arguments.extend(["--user"] if scope == "user" else ["--project", str(root)])
        if update:
            arguments.append("--update")
        with patch("sys.argv", arguments), \
                patch.object(installer.Path, "home", return_value=root), \
                patch.object(installer, "install", side_effect=from_fixture), \
                contextlib.redirect_stdout(output):
            status = installer.main()
        return status, json.loads(output.getvalue())

    def assert_payload(self, target):
        actual = {}
        for name in self.names:
            files = snapshot(target / name)
            marker = json.loads(files.pop(installer.MARKER).decode("utf-8"))
            self.assertEqual(marker["schema"], 1)
            self.assertEqual(set(marker["files"]), set(files))
            actual.update({name + "/" + relative: raw for relative, raw in files.items()})
        self.assertEqual(actual, self.payload)
        self.assertTrue((target / "story-skill/scripts/story.py").is_file())

    def test_cli_selects_native_paths_and_preserves_complete_payload_and_existing_files(self):
        for (host, scope), relative in HOST_PATHS.items():
            with self.subTest(host=host, scope=scope):
                root = self.root / (host + "-" + scope) / "中文写作目录"
                root.mkdir(parents=True)
                target = root / relative
                other_skill = target / "other-skill/SKILL.md"
                other_skill.parent.mkdir(parents=True)
                other_skill.write_bytes(b"unrelated skill\n")
                for name in ("AGENTS.md", "TRAE.md", "GEMINI.md",
                             ".cursor/rules/author.mdc", ".trae/config.json",
                             ".trae-cn/config.json", ".traecli/config.json",
                             ".codebuddy/settings.json", ".opencode/opencode.json",
                             ".config/opencode/opencode.json",
                             ".github/copilot-instructions.md", ".copilot/config.json",
                             ".gemini/settings.json", ".cline/settings.json",
                             ".windsurf/rules/author.md", ".codeium/windsurf/config.json",
                             ".devin/config.json", ".config/devin/config.json",
                             "books/我的小说/正文/第一章.md"):
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(("preserve: " + name + "\n").encode("utf-8"))
                original = snapshot(root)

                status, receipt = self.run_cli(root, host, scope)
                self.assertEqual(status, 0)
                self.assertEqual(receipt["status"], "installed")
                self.assertEqual(receipt["host"], host)
                self.assertEqual(receipt["scope"], scope)
                self.assertEqual(receipt["path"], str(target))
                self.assertEqual(receipt["skills"], list(self.names))
                self.assertEqual(receipt["files"], len(self.payload))
                self.assert_payload(target)
                installed = snapshot(root)
                for name, raw in original.items():
                    self.assertEqual(installed[name], raw)
                self.assertFalse((root / ".agents").exists())

                status, repeated = self.run_cli(root, host, scope)
                self.assertEqual(status, 0)
                self.assertEqual(repeated["status"], "unchanged")
                self.assertEqual(snapshot(root), installed)
                self.assertEqual(snapshot(self.source), self.payload)

                edited = target / "story-skill-plan/SKILL.md"
                edited.write_bytes(edited.read_bytes() + "作者的本地修改\n".encode("utf-8"))
                with_edits = snapshot(root)
                status, rejected = self.run_cli(root, host, scope, update=True)
                self.assertEqual(status, 2)
                self.assertFalse(rejected["ok"])
                self.assertIn("local edits", rejected["error"])
                self.assertEqual(snapshot(root), with_edits)
                self.assertEqual(snapshot(self.source), self.payload)

    def test_each_native_directory_has_a_lock_independent_of_other_hosts(self):
        for scope in ("project", "user"):
            with self.subTest(scope=scope):
                root = self.root / ("独立安装锁-" + scope)
                root.mkdir()
                first_host = HOSTS[0]
                first_target = self.target(root, first_host, scope)
                installer.install(root, source=self.source, host=first_host, scope=scope)
                first_before = snapshot(first_target)
                installed_targets = {first_target}
                with installer.installation_lock(root, host=first_host, scope=scope):
                    for host in HOSTS[1:]:
                        with self.subTest(scope=scope, host=host):
                            receipt = installer.install(root, source=self.source, host=host, scope=scope)
                            target = self.target(root, host, scope)
                            self.assertNotEqual(target, first_target)
                            expected_status = "unchanged" if target in installed_targets else "installed"
                            self.assertEqual(receipt["status"], expected_status)
                            self.assertEqual(receipt["path"], str(target))
                            self.assert_payload(target)
                            installed_targets.add(target)
                # Windows byte-range locks also prevent reading the lock file.
                self.assertEqual(snapshot(first_target), first_before)
                before = snapshot(root)
                for host in HOSTS:
                    other_host = next(candidate for candidate in HOSTS
                                      if self.target(root, candidate, scope) != self.target(root, host, scope))
                    with self.subTest(scope=scope, locked_host=host):
                        with installer.installation_lock(root, host=host, scope=scope):
                            with self.assertRaisesRegex(ValueError, "Another installation"):
                                installer.install(root, source=self.source, host=host, scope=scope)
                            unchanged = installer.install(root, source=self.source,
                                                          host=other_host, scope=scope)
                            self.assertEqual(unchanged["status"], "unchanged")
                        self.assertEqual(snapshot(root), before)
                self.assertEqual(snapshot(self.source), self.payload)

    def test_trae_and_cn_share_the_project_suite_and_installation_lock(self):
        root = self.root / "TRAE共享项目"
        root.mkdir()
        target = root / ".trae/skills"
        first_status, first = self.run_cli(root, "trae", "project")
        self.assertEqual(first_status, 0)
        self.assertEqual(first["status"], "installed")
        self.assertEqual(first["path"], str(target))
        before = snapshot(root)
        second_status, second = self.run_cli(root, "trae-cn", "project")
        self.assertEqual(second_status, 0)
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(second["path"], str(target))
        self.assertEqual(snapshot(root), before)
        self.assert_payload(target)
        for locked_host, other_host in (("trae", "trae-cn"), ("trae-cn", "trae")):
            with self.subTest(locked_host=locked_host):
                with installer.installation_lock(root, host=locked_host):
                    for host in (locked_host, other_host):
                        with self.assertRaisesRegex(ValueError, "Another installation"):
                            installer.install(root, source=self.source, host=host)
                self.assertEqual(snapshot(root), before)
        self.assertEqual(snapshot(self.source), self.payload)


if __name__ == "__main__":
    unittest.main()
