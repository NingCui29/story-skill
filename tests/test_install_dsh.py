"""DeepSeek Harness: one-level discovery, frontmatter and native skill roots."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load("dsh_installer", ROOT / "scripts/install.py")
package = load("dsh_package", ROOT / "scripts/package.py")

# DSH accepts kebab-case names and renders a capped description in its catalog.
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DESCRIPTION_LIMIT = 500


def frontmatter(path):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise AssertionError("skill is missing opening YAML frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise AssertionError("skill is missing closing YAML frontmatter") from None
    fields = {}
    for line in lines[1:end]:
        if not line or line.startswith((" ", "\t", "#")):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise AssertionError(f"unsupported frontmatter line: {line}")
        fields[key.strip()] = value.strip()
    if not fields.get("name") or not fields.get("description"):
        raise AssertionError("DSH requires both name and description")
    return fields, lines[end + 1:]


def discover(root):
    """Mirror DSH: only direct child directories with a SKILL.md are skills."""
    found = {}
    for entry in sorted(Path(root).iterdir()):
        if not entry.is_dir():
            continue
        entry_file = entry / "SKILL.md"
        if not entry_file.is_file():
            continue
        fields, _ = frontmatter(entry_file)
        name = fields["name"]
        if name in found:
            raise AssertionError(f"duplicate discovered skill: {name}")
        found[name] = {"directory": entry.name, "description": fields["description"]}
    return found


class DshInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-dsh-install-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        runtime = ROOT / "skills/story-skill/scripts/story.py"
        self.version = installer.runtime_version(runtime.read_bytes())
        self.names = tuple(installer.skill_names(self.version))
        for relative in installer.suite_files(self.version):
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative.endswith("/SKILL.md"):
                name = relative.split("/", 1)[0]
                path.write_text(
                    f"---\nname: {name}\ndescription: 测试描述 {name}\n---\n\n# {name}\n",
                    encoding="utf-8")
            else:
                path.write_bytes(("中文载荷: " + relative + "\r\n").encode("utf-8"))
        (self.source / "story-skill/scripts/story.py").write_text(
            'VERSION = "' + self.version + '"\n', encoding="utf-8")

    def target(self, root, scope):
        return root / installer.host_directory("dsh", scope) / "skills"

    def run_cli(self, root, scope, update=False):
        original_install = installer.install
        output = io.StringIO()

        def from_fixture(project, update=False, *, host="codex", scope="project"):
            return original_install(project, update, self.source, host=host, scope=scope)

        arguments = ["install.py", "--host", "dsh"]
        arguments.extend(["--user"] if scope == "user" else ["--project", str(root)])
        if update:
            arguments.append("--update")
        with patch("sys.argv", arguments), \
                patch.object(installer.Path, "home", return_value=root), \
                patch.object(installer, "install", side_effect=from_fixture), \
                contextlib.redirect_stdout(output):
            status = installer.main()
        return status, json.loads(output.getvalue())

    def assert_discovery(self, root):
        found = discover(root)
        self.assertEqual(set(found), set(self.names))
        self.assertEqual(len(list(Path(root).rglob("SKILL.md"))), len(self.names))
        for name, entry in found.items():
            with self.subTest(skill=name):
                self.assertEqual(entry["directory"], name)
                self.assertRegex(name, NAME)
                self.assertTrue(entry["description"])
                self.assertLessEqual(len(entry["description"]), DESCRIPTION_LIMIT)
                self.assertNotIn("\n", entry["description"])

    def test_project_and_user_roots_match_dsh_discovery(self):
        for scope in ("project", "user"):
            with self.subTest(scope=scope):
                root = self.root / ("dsh-" + scope)
                root.mkdir()
                status, receipt = self.run_cli(root, scope)
                self.assertEqual(status, 0)
                self.assertEqual(receipt["status"], "installed")
                self.assertEqual(receipt["host"], "dsh")
                self.assertEqual(receipt["scope"], scope)
                target = self.target(root, scope)
                self.assertEqual(receipt["path"], str(target))
                self.assertTrue((target / "story-skill/scripts/story.py").is_file())
                self.assert_discovery(target)
                self.assertFalse((root / f"story-skill-{self.version}").exists())

                checked = installer.check_installation(root, self.source, host="dsh", scope=scope)
                self.assertEqual(checked["status"], "verified")
                self.assertEqual(checked["skills"], list(self.names))

                status, repeated = self.run_cli(root, scope)
                self.assertEqual((status, repeated["status"]), (0, "unchanged"))

    def test_standard_zip_has_the_eight_roots_dsh_scans(self):
        entries = package.source_entries()
        roots = {name.split("/", 1)[0] for name, _ in entries}
        self.assertEqual(roots, set(self.names))
        self.assertNotIn("story-skill-" + self.version, roots)

        archive = self.root / f"story-skill-{self.version}.zip"
        result = package.package(archive)
        self.assertEqual(result["version"], self.version)
        extracted = self.root / "extracted"
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(extracted)
        self.assert_discovery(extracted)

    def test_update_keeps_a_complete_backup_and_is_idempotent(self):
        root = self.root / "dsh-update"
        root.mkdir()
        status, installed = self.run_cli(root, "project")
        self.assertEqual((status, installed["status"]), (0, "installed"))
        target = self.target(root, "project")
        before = {name: installer.inventory(target / name) for name in self.names}

        edited = self.source / "story-skill/SKILL.md"
        edited.write_bytes(edited.read_bytes() + "本地修订\n".encode("utf-8"))
        status, updated = self.run_cli(root, "project", update=True)
        self.assertEqual((status, updated["status"]), (0, "updated"))
        self.assertEqual(updated["changed_skills"], ["story-skill"])
        self.assertEqual(installer.inventory(target / "story-skill"),
                         installer.inventory(self.source / "story-skill"))
        backup = Path(updated["backup"])
        self.assertEqual(set(path.name for path in backup.iterdir()), {"story-skill"})
        self.assertEqual(installer.inventory(backup / "story-skill"), before["story-skill"])
        self.assertEqual(installer.install(root, True, self.source, host="dsh", scope="project")["status"],
                         "unchanged")


if __name__ == "__main__":
    unittest.main()