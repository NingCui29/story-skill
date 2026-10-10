import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_installer", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def snapshot(root):
    return {p.relative_to(root).as_posix(): ("dir",) if p.is_dir() else ("file", p.read_bytes())
            for p in root.rglob("*")}


class ReadOnlyInstallationCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-install-check-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        version = installer.runtime_version((ROOT / "skills/story-skill/scripts/story.py").read_bytes())
        for relative in installer.suite_files(version):
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((relative + "\n").encode())
        (self.source / "story-skill/scripts/story.py").write_text(
            'VERSION = "' + version + '"\n', encoding="utf-8")
        self.project = self.root / "中文项目"
        self.target = self.project / ".cursor/skills"

    def check(self):
        return installer.check_installation(self.project, self.source, host="cursor")

    def test_absent_installation_fails_without_creating_any_directory(self):
        before = snapshot(self.root)
        with self.assertRaisesRegex(ValueError, "missing a skill"):
            self.check()
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(self.project.exists())

    def test_managed_and_manual_suites_verify_without_creating_a_lock(self):
        installer.install(self.project, source=self.source, host="cursor")
        (self.target / ".story-skill-install.lock").unlink()
        before = snapshot(self.root)
        receipt = self.check()
        self.assertEqual(receipt["status"], "verified")
        self.assertEqual(set(receipt["managed_skills"]), set(installer.SKILL_NAMES))
        self.assertEqual(snapshot(self.root), before)
        for name in installer.SKILL_NAMES:
            (self.target / name / installer.MARKER).unlink()
        before = snapshot(self.root)
        receipt = self.check()
        self.assertEqual(receipt["managed_skills"], [])
        self.assertEqual(receipt["files"], len(installer.SUITE_FILES))
        self.assertEqual(snapshot(self.root), before)

    def test_modified_extra_missing_files_and_invalid_manifest_are_rejected(self):
        for change in ("modified", "extra", "missing", "manifest"):
            with self.subTest(change=change):
                if self.project.exists():
                    shutil.rmtree(self.project)
                installer.install(self.project, source=self.source, host="cursor")
                skill = self.target / "story-skill"
                if change == "modified":
                    (skill / "SKILL.md").write_text("local edit", encoding="utf-8")
                elif change == "extra":
                    (skill / "extra.txt").write_text("local file", encoding="utf-8")
                elif change == "missing":
                    (skill / "scripts/story.py").unlink()
                else:
                    (skill / installer.MARKER).write_text('{"schema": 2}', encoding="utf-8")
                before = snapshot(self.root)
                with self.assertRaises(ValueError):
                    self.check()
                self.assertEqual(snapshot(self.root), before)

    def test_concurrent_source_change_is_detected_without_writing(self):
        installer.install(self.project, source=self.source, host="cursor")
        source_inventory = installer.suite_inventory
        calls = 0

        def changing_source(source):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.source / "story-skill/SKILL.md").write_text("changed by external writer", encoding="utf-8")
            return source_inventory(source)

        before = snapshot(self.project)
        with patch.object(installer, "suite_inventory", side_effect=changing_source), \
                self.assertRaisesRegex(ValueError, "Source suite changed"):
            self.check()
        self.assertEqual(snapshot(self.project), before)

    def test_concurrent_manifest_changes_are_detected_without_overwriting_them(self):
        for change in ("invalid", "removed", "reformatted", "added"):
            with self.subTest(change=change):
                if self.project.exists():
                    shutil.rmtree(self.project)
                installer.install(self.project, source=self.source, host="cursor")
                marker = self.target / "story-skill" / installer.MARKER
                raw = marker.read_bytes()
                if change == "added":
                    marker.unlink()
                source_inventory = installer.suite_inventory
                calls = 0
                mutated = None

                def change_manifest(source):
                    nonlocal calls, mutated
                    calls += 1
                    if calls == 2:
                        if change == "invalid":
                            marker.write_text('{"schema": 2}', encoding="utf-8")
                        elif change == "removed":
                            marker.unlink()
                        elif change == "reformatted":
                            marker.write_text(json.dumps(json.loads(raw), indent=4), encoding="utf-8")
                        else:
                            marker.write_bytes(raw)
                        mutated = snapshot(self.project)
                    return source_inventory(source)

                with patch.object(installer, "suite_inventory", side_effect=change_manifest), \
                        self.assertRaisesRegex(ValueError, "[Mm]anifest"):
                    self.check()
                self.assertEqual(snapshot(self.project), mutated)

    def test_cli_check_routes_both_scopes_without_invoking_install(self):
        for scope in ("project", "user"):
            root = self.root / scope
            installer.install(root, source=self.source, host="opencode", scope=scope)
            before = snapshot(self.root)
            output = io.StringIO()
            args = ["install.py", "--host", "opencode", "--check"]
            args += ["--user"] if scope == "user" else ["--project", str(root)]
            check_installation = installer.check_installation
            with patch("sys.argv", args), patch.object(installer.Path, "home", return_value=root), \
                    patch.object(installer, "install", side_effect=AssertionError("check must not install")), \
                    patch.object(installer, "check_installation", side_effect=lambda project, **kw:
                                 check_installation(project, self.source, **kw)), \
                    contextlib.redirect_stdout(output):
                status = installer.main()
            self.assertEqual(status, 0)
            receipt = json.loads(output.getvalue())
            self.assertEqual(receipt["status"], "verified")
            self.assertEqual(receipt["scope"], scope)
            self.assertEqual(snapshot(self.root), before)


if __name__ == "__main__":
    unittest.main()
