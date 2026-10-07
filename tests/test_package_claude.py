import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("claude_desktop_package", ROOT / "scripts/package_claude.py")
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


def fixture_suite(source):
    for relative in desktop.suite.SUITE_FILES:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("fixture: " + relative + "\n").encode("utf-8"))
    (source / "story-skill/scripts/story.py").write_bytes(b'VERSION = "0.6.12"\n')


class ClaudeDesktopPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-desktop-package-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        fixture_suite(self.source)
        self.output = self.root / "story-skill-claude-desktop-0.6.12.zip"

    def package(self, output=None):
        with patch.object(desktop.suite, "SOURCE", self.source):
            return desktop.package(output or self.output)

    def assert_preserved(self, original):
        self.assertEqual(self.output.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".story-skill-package-*.zip")), [])

    def source_snapshot(self):
        return {path.relative_to(self.source).as_posix(): path.read_bytes() if path.is_file() else None
                for path in self.source.rglob("*")}

    def test_real_source_is_one_importable_skill_with_verbatim_embedded_suite(self):
        source_entries = dict(desktop.suite.source_entries())
        receipt = desktop.package(self.output)
        with zipfile.ZipFile(self.output) as archive:
            members = set(archive.namelist())
            self.assertEqual({PurePosixPath(name).parts[0] for name in members}, {"story-skill"})
            self.assertEqual({name for name in members if len(PurePosixPath(name).parts) == 2
                              and name.endswith("/SKILL.md")}, {"story-skill/SKILL.md"})
            self.assertEqual({PurePosixPath(name).parts[2] for name in members
                              if name.startswith("story-skill/suite/")}, set(desktop.suite.SKILL_NAMES))
            self.assertEqual(members, {"story-skill/SKILL.md", "story-skill/LICENSE"}
                             | {"story-skill/suite/" + name for name in source_entries})
            for name, raw in source_entries.items():
                self.assertEqual(archive.read("story-skill/suite/" + name), raw, name)
            entry = archive.read("story-skill/SKILL.md").decode("utf-8")
            self.assertTrue(entry.startswith("---\nname: story-skill\ndescription: "))
            self.assertIn("suite/story-skill/SKILL.md", entry)
            self.assertIn("suite/story-skill/scripts/story.py", entry)
            checked_links = 0
            for name in members:
                if not name.endswith(".md"):
                    continue
                for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", archive.read(name).decode("utf-8")):
                    target = target.split("#", 1)[0]
                    if not target or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
                        continue
                    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
                    self.assertIn(resolved, members, f"{name}: {target}")
                    checked_links += 1
            self.assertGreater(checked_links, 20)
            self.assertIsNone(archive.testzip())
        self.assertEqual(receipt["source_files"], len(source_entries))
        self.assertEqual(receipt["files"], len(source_entries) + 2)
        self.assertEqual(receipt["skill_name"], "story-skill")
        self.assertEqual(receipt["sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())

    def test_archive_is_deterministic_and_default_name_is_desktop_specific(self):
        with patch.object(desktop, "ROOT", self.root), patch.object(desktop.suite, "SOURCE", self.source):
            first = desktop.package()
            second = desktop.package()
        expected = self.root / "dist/story-skill-claude-desktop-0.6.12.zip"
        self.assertEqual(Path(first["archive"]), expected)
        self.assertEqual(first, second)
        self.assertEqual(first["version"], "0.6.12")
        first_bytes = expected.read_bytes()
        custom = self.root / "custom-desktop.zip"
        self.package(custom)
        self.assertEqual(custom.read_bytes(), first_bytes)

    def test_cli_works_from_an_unrelated_directory(self):
        unrelated = self.root / "unrelated"
        unrelated.mkdir()
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/package_claude.py"),
                                 "--output", str(self.output)], cwd=unrelated,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(Path(receipt["archive"]), self.output)
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(archive.read("story-skill/suite/story-skill/scripts/story.py"),
                             (ROOT / "skills/story-skill/scripts/story.py").read_bytes())
            self.assertIn("story-skill/SKILL.md", archive.namelist())

    def test_invalid_source_preserves_existing_archive(self):
        self.package()
        original = self.output.read_bytes()

        def link_source(link, target, *, directory=False):
            try:
                link.symlink_to(target, target_is_directory=directory)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"Host cannot create symlink: {error}")

        for fault in ("missing", "extra", "linked-file", "linked-skill", "linked-source"):
            with self.subTest(fault=fault):
                source = self.root / fault / "source"
                fixture_suite(source)
                if fault == "missing":
                    (source / "story-skill-plan/SKILL.md").unlink()
                elif fault == "extra":
                    (source / "story-skill/private-draft.md").write_bytes(b"private draft\n")
                elif fault == "linked-file":
                    target = source / "story-skill-plan/SKILL.md"
                    target.unlink()
                    link_source(target, self.source / "story-skill-plan/SKILL.md")
                elif fault == "linked-skill":
                    target = source / "story-skill-plan"
                    moved = source.parent / "outside-skill"
                    target.rename(moved)
                    link_source(target, moved, directory=True)
                else:
                    moved = source.parent / "outside-source"
                    source.rename(moved)
                    link_source(source, moved, directory=True)
                with patch.object(desktop.suite, "SOURCE", source), self.assertRaises(ValueError):
                    desktop.package(self.output)
                self.assert_preserved(original)

    def test_archive_crc_and_content_validation_preserve_existing_archive(self):
        self.package()
        original = self.output.read_bytes()
        with patch.object(zipfile.ZipFile, "testzip", return_value="story-skill/SKILL.md"), \
                self.assertRaisesRegex(RuntimeError, "Archive verification failed"):
            self.package()
        self.assert_preserved(original)
        original_write = zipfile.ZipFile.writestr

        def corrupt_entry(archive, name, raw, *args, **kwargs):
            if name.filename == "story-skill/SKILL.md":
                raw = b"different content with valid ZIP checksum\n"
            return original_write(archive, name, raw, *args, **kwargs)

        with patch.object(zipfile.ZipFile, "writestr", corrupt_entry), \
                self.assertRaisesRegex(RuntimeError, "Archive content differs"):
            self.package()
        self.assert_preserved(original)

    def test_publication_failure_preserves_existing_archive(self):
        self.package()
        original = self.output.read_bytes()
        with patch.object(desktop.os, "replace", side_effect=PermissionError("locked destination")), \
                self.assertRaisesRegex(PermissionError, "locked destination"):
            self.package()
        self.assert_preserved(original)

    def test_canonical_names_and_non_zip_outputs_are_rejected(self):
        for name in ("story-skill-0.6.12.zip", "story-skill-0.6.11.zip", "story-skill-0.6.10.zip", "desktop.tar.gz"):
            with self.subTest(name=name):
                output = self.root / name
                output.write_bytes(b"existing reviewed archive\n")
                with self.assertRaises(ValueError):
                    self.package(output)
                self.assertEqual(output.read_bytes(), b"existing reviewed archive\n")
                self.assertEqual(list(self.root.glob(".story-skill-package-*.zip")), [])

    def test_outputs_inside_source_are_rejected_without_changes(self):
        original = self.source_snapshot()
        outputs = (self.source / "desktop.zip",
                   self.source / "story-skill/desktop.zip",
                   self.source / "story-skill/new/nested/desktop.zip")
        for output in outputs:
            with self.subTest(output=output):
                with self.assertRaisesRegex(ValueError, "outside the source directory"):
                    self.package(output)
                self.assertEqual(self.source_snapshot(), original)
                self.assertEqual(list(self.root.rglob(".story-skill-package-*.zip")), [])

    def test_output_parent_link_to_source_is_rejected_without_changes(self):
        link = self.root / "linked-output"
        try:
            link.symlink_to(self.source / "story-skill", target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Host cannot create directory symlink: {error}")
        original = self.source_snapshot()
        output = link / "new/nested/desktop.zip"
        with self.assertRaisesRegex(ValueError, "outside the source directory"):
            self.package(output)
        self.assertEqual(self.source_snapshot(), original)
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), self.source / "story-skill")
        self.assertFalse((link / "new").exists())
        self.assertEqual(list(self.root.rglob(".story-skill-package-*.zip")), [])

    def test_custom_output_directory_remains_supported_without_source_changes(self):
        original = self.source_snapshot()
        output = self.root / "custom-output/nested/desktop.zip"
        first = self.package(output)
        original_archive = output.read_bytes()
        second = self.package(output)
        self.assertEqual(Path(first["archive"]), output)
        self.assertEqual(first, second)
        self.assertEqual(output.read_bytes(), original_archive)
        self.assertEqual(first["sha256"], hashlib.sha256(original_archive).hexdigest())
        self.assertEqual(self.source_snapshot(), original)

    def test_output_parent_link_outside_source_remains_supported(self):
        outside = self.root / "outside-output"
        outside.mkdir()
        link = self.root / "linked-output"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Host cannot create directory symlink: {error}")
        original = self.source_snapshot()
        output = link / "new/desktop.zip"
        receipt = self.package(output)
        self.assertEqual(Path(receipt["archive"]), output)
        self.assertTrue((outside / "new/desktop.zip").is_file())
        self.assertEqual(receipt["sha256"], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertEqual(self.source_snapshot(), original)


if __name__ == "__main__":
    unittest.main()
