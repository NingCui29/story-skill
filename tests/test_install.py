import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-install-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "skills"
        for relative in installer.SUITE_FILES:
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("fixture: " + relative + "\n").encode("utf-8"))
        (self.source / "story-skill/scripts/story.py").write_text('VERSION = "0.6.1"\n', encoding="utf-8")
        self.project = self.root / "项目"
        self.project.mkdir()

    def install(self, update=False):
        return installer.install(self.project, update, self.source)

    def target(self, name="story-skill"):
        return self.project / ".agents/skills" / name

    def test_only_adds_suite_and_repeat_is_noop(self):
        (self.project / "AGENTS.md").write_text("用户配置", encoding="utf-8")
        (self.project / ".active-book").write_text("另一本书", encoding="utf-8")
        result = self.install()
        self.assertEqual(result["status"], "installed")
        self.assertEqual(result["skills"], list(installer.SKILL_NAMES))
        self.assertEqual(self.install()["status"], "unchanged")
        self.assertEqual((self.project / "AGENTS.md").read_text(encoding="utf-8"), "用户配置")
        self.assertEqual((self.project / ".active-book").read_text(encoding="utf-8"), "另一本书")

    def test_update_preserves_backup(self):
        self.install()
        (self.source / "story-skill/scripts/story.py").write_text('VERSION = "0.6.1"\n# revised\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "--update"):
            self.install()
        result = self.install(True)
        self.assertEqual(result["status"], "updated")
        self.assertIn("VERSION", (Path(result["backup"]) / "story-skill/scripts/story.py").read_text())
        self.assertIn("revised", (self.target() / "scripts/story.py").read_text())

    def test_local_modifications_are_preserved(self):
        self.install()
        file = self.target() / "SKILL.md"
        file.write_text("我的改动", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "local edits"):
            self.install(True)
        self.assertEqual(file.read_text(encoding="utf-8"), "我的改动")

    def test_unmanaged_existing_skill_not_overwritten(self):
        target = self.target()
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("用户自己的技能", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no managed manifest"):
            self.install(True)
        self.assertEqual((target / "SKILL.md").read_text(encoding="utf-8"), "用户自己的技能")

    def test_failed_swap_restores_previous_installation(self):
        self.install()
        (self.source / "story-skill/scripts/story.py").write_text('VERSION = "0.6.1"\n# revised\n', encoding="utf-8")
        original_move = installer.move_directory

        def move(source, destination):
            if Path(source).parent.name.startswith(".story-skill-stage-") and Path(source).name == "story-skill":
                raise OSError("simulated swap failure")
            return original_move(source, destination)

        with patch.object(installer, "move_directory", side_effect=move), self.assertRaises(OSError):
            self.install(True)
        self.assertNotIn("revised", (self.target() / "scripts/story.py").read_text())

    def test_malicious_manifest_path_is_rejected(self):
        self.install()
        marker = self.target() / installer.MARKER
        marker.write_text(json.dumps({"schema": 1, "files": {"../../outside": "hash"}}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.install(True)


if __name__ == "__main__":
    unittest.main()
