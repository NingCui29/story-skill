import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("source_boundary_installer", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def fixture_suite(source):
    for relative in installer.SUITE_FILES:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("fixture: " + relative + "\n").encode("utf-8"))
    (source / "story-skill/scripts/story.py").write_bytes(b'VERSION = "0.6.14"\n')


def tree_snapshot(root):
    """Include empty directories and links as well as every file's bytes."""
    result = {".": ("directory",)}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = ("link", os.readlink(path))
        elif path.is_dir():
            result[relative] = ("directory",)
        else:
            result[relative] = ("file", path.read_bytes())
    return result


class InstallSourceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-install-source-boundary-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source_parent = self.root / "source-parent"
        self.source = self.source_parent / "skills"
        fixture_suite(self.source)

    def test_nested_targets_are_rejected_before_source_tree_changes(self):
        before = tree_snapshot(self.source)
        inventory = installer.suite_inventory(self.source)
        for host in installer.HOST_DIRECTORIES:
            for scope in ("project", "user"):
                with self.subTest(host=host, scope=scope):
                    with self.assertRaisesRegex(ValueError, "outside the source directory"):
                        installer.install(self.source / "story-skill", source=self.source,
                                          host=host, scope=scope)
                    self.assertEqual(tree_snapshot(self.source), before)
                    self.assertEqual(installer.suite_inventory(self.source), inventory)

    def test_parent_links_resolve_to_the_same_source_boundary(self):
        alias = self.root / "source-parent-alias"
        try:
            alias.symlink_to(self.source_parent, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Host cannot create directory symlink: {error}")
        before = tree_snapshot(self.source)
        source_alias = alias / "skills"
        cases = ((self.source / "story-skill", source_alias),
                 (source_alias / "story-skill", self.source))
        for project, source in cases:
            with self.subTest(project=project, source=source):
                with self.assertRaisesRegex(ValueError, "outside the source directory"):
                    installer.install(project, source=source, host="claude-code")
                self.assertEqual(tree_snapshot(self.source), before)
                self.assertTrue(alias.is_symlink())

    def test_source_already_at_target_remains_unchanged(self):
        for host in installer.HOST_DIRECTORIES:
            for scope in ("project", "user"):
                with self.subTest(host=host, scope=scope):
                    project = self.root / "in-place" / host / scope
                    source = project / installer.host_directory(host, scope) / "skills"
                    fixture_suite(source)
                    before = tree_snapshot(source)
                    receipt = installer.install(project, source=source, host=host, scope=scope)
                    self.assertEqual(receipt["status"], "already_in_place")
                    self.assertEqual(receipt["path"], str(source))
                    self.assertEqual(tree_snapshot(source), before)

    def test_external_targets_still_install_complete_suite(self):
        before = tree_snapshot(self.source)
        inventory = installer.suite_inventory(self.source)
        for host in installer.HOST_DIRECTORIES:
            for scope in ("project", "user"):
                with self.subTest(host=host, scope=scope):
                    project = self.root / "external" / host / scope
                    receipt = installer.install(project, source=self.source, host=host, scope=scope)
                    target = project / installer.host_directory(host, scope) / "skills"
                    self.assertEqual(receipt["status"], "installed")
                    self.assertEqual(receipt["path"], str(target))
                    for name in installer.SKILL_NAMES:
                        self.assertEqual(installer.inventory(target / name), inventory[name])
                    self.assertEqual(tree_snapshot(self.source), before)


if __name__ == "__main__":
    unittest.main()
