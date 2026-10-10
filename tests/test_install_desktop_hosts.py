import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("desktop_hosts_installer", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

# Keep these expectations independent of the installer's directory mappings.
HOST_PATHS = {
    ("qoder", "project"): ".qoder/skills",
    ("qoder", "user"): ".qoder/skills",
    ("qoder-cn", "project"): ".qoder/skills",
    ("qoder-cn", "user"): ".qoder-cn/skills",
    ("zcode", "project"): ".zcode/skills",
    ("zcode", "user"): ".zcode/skills",
    ("workbuddy", "project"): ".workbuddy/skills",
    ("workbuddy", "user"): ".workbuddy/skills",
}


def snapshot(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}


class DesktopHostInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-desktop-host-install-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        runtime = ROOT / "skills/story-skill/scripts/story.py"
        version = installer.runtime_version(runtime.read_bytes())
        for relative in installer.suite_files(version):
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("fixture: " + relative + "\n").encode("utf-8"))
        (self.source / "story-skill/scripts/story.py").write_text(
            'VERSION = "' + version + '"\n', encoding="utf-8")
        self.expected = installer.suite_inventory(self.source)

    def target(self, root, host, scope):
        return root / HOST_PATHS[host, scope]

    def run_cli(self, root, host, scope):
        original_install = installer.install
        output = io.StringIO()

        def from_fixture(project, update=False, *, host="codex", scope="project"):
            return original_install(project, update, self.source, host=host, scope=scope)

        arguments = ["install.py", "--host", host]
        arguments.extend(["--user"] if scope == "user" else ["--project", str(root)])
        with patch("sys.argv", arguments), \
                patch.object(installer.Path, "home", return_value=root), \
                patch.object(installer, "install", side_effect=from_fixture), \
                contextlib.redirect_stdout(output):
            status = installer.main()
        return status, json.loads(output.getvalue())

    def test_cli_selects_expected_paths_and_copies_complete_suite_verbatim(self):
        source_before = snapshot(self.source)
        for (host, scope), relative in HOST_PATHS.items():
            with self.subTest(host=host, scope=scope):
                root = self.root / (host + "-" + scope) / "中文写作目录"
                root.mkdir(parents=True)
                target = root / relative
                other_skill = target / "other-skill/SKILL.md"
                other_skill.parent.mkdir(parents=True)
                other_skill.write_bytes(b"unrelated skill\n")
                for name in ("AGENTS.md", "QODER.md", "WORKBUDDY.md",
                             "books/我的小说/正文/第一章.md", ".claude/settings.json"):
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
                self.assertEqual(receipt["skills"], list(self.expected))
                self.assertEqual(receipt["files"], sum(map(len, self.expected.values())))
                for name, files in self.expected.items():
                    self.assertEqual(installer.inventory(target / name), files)
                    marker = json.loads((target / name / installer.MARKER).read_text(encoding="utf-8"))
                    self.assertEqual(marker["files"], files)
                installed = snapshot(root)
                for name, raw in original.items():
                    self.assertEqual(installed[name], raw)
                self.assertFalse((root / ".agents").exists())

                status, repeated = self.run_cli(root, host, scope)
                self.assertEqual(status, 0)
                self.assertEqual(repeated["status"], "unchanged")
                self.assertEqual(snapshot(root), installed)
                self.assertEqual(snapshot(self.source), source_before)

    def test_qoder_variants_share_the_project_suite_and_installation_lock(self):
        project = self.root / "共享项目"
        project.mkdir()
        target = self.target(project, "qoder", "project")
        self.assertEqual(target, self.target(project, "qoder-cn", "project"))
        first = installer.install(project, source=self.source, host="qoder")
        before = snapshot(project)
        second = installer.install(project, source=self.source, host="qoder-cn")
        self.assertEqual(first["status"], "installed")
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(second["path"], str(target))
        self.assertEqual(snapshot(project), before)
        with installer.installation_lock(project, host="qoder"):
            for host in ("qoder", "qoder-cn"):
                with self.subTest(host=host), self.assertRaisesRegex(ValueError, "Another installation"):
                    installer.install(project, source=self.source, host=host)
        self.assertEqual(snapshot(project), before)

    def test_distinct_host_directories_have_independent_locks_and_installations(self):
        for scope in ("project", "user"):
            with self.subTest(scope=scope):
                root = self.root / ("独立目录-" + scope)
                root.mkdir()
                installer.install(root, source=self.source, host="qoder", scope=scope)
                locked_target = self.target(root, "qoder", scope)
                before = snapshot(locked_target)
                with installer.installation_lock(root, host="qoder", scope=scope):
                    for host in ("qoder-cn", "zcode", "workbuddy"):
                        target = self.target(root, host, scope)
                        if target == locked_target:
                            continue
                        with self.subTest(scope=scope, host=host):
                            receipt = installer.install(root, source=self.source, host=host, scope=scope)
                            self.assertEqual(receipt["status"], "installed")
                            self.assertEqual(receipt["path"], str(target))
                            for name, files in self.expected.items():
                                self.assertEqual(installer.inventory(target / name), files)
                            self.assertEqual(snapshot(locked_target), before)


if __name__ == "__main__":
    unittest.main()
