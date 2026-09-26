import errno
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_package", ROOT / "scripts/package.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


def fixture_suite(source, version="0.6.1", files=None):
    for name in (package.suite_files(version) if files is None else files):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture content\n")
    (source / "story-skill/scripts/story.py").write_bytes(f'VERSION = "{version}"\n'.encode())


class PackageVersionTests(unittest.TestCase):
    def test_reads_literal_version_without_executing_runtime(self):
        source = b'VERSION = "2.4.6"\nraise RuntimeError("must not execute")\n'
        self.assertEqual(package.source_version(source), "2.4.6")
        for invalid in [b'VERSION = get_version()\n', b'VERSION = "bad"\n',
                        b'VERSION = "1.2.3"\nVERSION = "2.3.4"\n']:
            with self.subTest(source=invalid), self.assertRaises(ValueError):
                package.source_version(invalid)

    def test_default_archive_name_tracks_the_actual_packaged_runtime(self):
        with tempfile.TemporaryDirectory(prefix="story-package-version-test-") as directory:
            root = Path(directory)
            source = root / "skill"
            fixture_suite(source)
            runtime = b'VERSION = "0.6.1"\n'
            with patch.object(package, "ROOT", root), patch.object(package, "SOURCE", source):
                result = package.package()
            archive_path = root / "dist/story-skill-0.6.1.zip"
            self.assertEqual(Path(result["archive"]), archive_path)
            self.assertEqual(result["version"], "0.6.1")
            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(archive.read("story-skill/scripts/story.py"), runtime)

    def test_mismatched_filename_cannot_replace_an_existing_archive(self):
        with tempfile.TemporaryDirectory(prefix="story-package-mismatch-test-") as directory:
            root = Path(directory)
            source = root / "skill"
            fixture_suite(source)
            output = root / "story-skill-0.6.0.zip"
            output.write_bytes(b"existing reviewed artifact")
            with patch.object(package, "ROOT", root), patch.object(package, "SOURCE", source):
                with self.assertRaisesRegex(ValueError, "Archive filename must be story-skill-0.6.1.zip"):
                    package.package(output)
            self.assertEqual(output.read_bytes(), b"existing reviewed artifact")

    def test_manifest_is_bound_to_version_and_unknown_families_are_rejected(self):
        layouts = {"0.6.0": package.SUITE_FILES_V060, "0.6.1": package.SUITE_FILES_V061}
        skill_layouts = {"0.6.0": package.SKILL_NAMES_V060, "0.6.1": package.SKILL_NAMES_V061}
        for version, expected in layouts.items():
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                self.assertEqual(package.skill_names(version), skill_layouts[version])
                root = Path(directory)
                fixture_suite(root / "skills", version)
                with patch.object(package, "ROOT", root), patch.object(package, "SOURCE", root / "skills"):
                    result = package.package()
                self.assertEqual(result["files"], len(expected))
                with zipfile.ZipFile(result["archive"]) as archive:
                    self.assertEqual(archive.namelist(), list(expected))
        self.assertEqual(len(package.SUITE_FILES_V060), 38)
        self.assertNotIn("story-skill/scripts/story_workbench.py", package.SUITE_FILES_V060)
        self.assertEqual(len(package.SUITE_FILES), 39)
        self.assertEqual(package.SUITE_FILES, package.SUITE_FILES_V061)
        self.assertEqual(package.SKILL_NAMES, package.SKILL_NAMES_V061)
        self.assertIn("story-skill/scripts/story_workbench.py", package.SUITE_FILES)
        for version in ("0.5.11", "0.6.00", "0.6.2", "0.6.99", "1.0.0", "2.4.6"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "no reviewed suite layout"):
                package.suite_files(version)
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "no reviewed suite layout"):
                package.skill_names(version)

    def test_missing_reviewed_file_cannot_replace_existing_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "skills"
            fixture_suite(source)
            (source / "story-skill-publish/SKILL.md").unlink()
            output = root / "story-skill-0.6.1.zip"
            output.write_bytes(b"existing reviewed artifact")
            with patch.object(package, "SOURCE", source):
                with self.assertRaisesRegex(ValueError, "reviewed file manifest"):
                    package.package(output)
            self.assertEqual(output.read_bytes(), b"existing reviewed artifact")


class PackagePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-package-publication-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        fixture_suite(self.source)
        self.runtime = self.source / "story-skill/scripts/story.py"
        for name, value in (("ROOT", self.root), ("SOURCE", self.source)):
            patched = patch.object(package, name, value)
            patched.start()
            self.addCleanup(patched.stop)
        self.first = package.package()
        self.output = Path(self.first["archive"])
        self.original = self.output.read_bytes()

    def prepare_rebuild(self):
        self.runtime.write_bytes(self.runtime.read_bytes() + b"# same-version rebuild\n")

    def assert_old_archive_preserved(self):
        self.assertEqual(self.output.read_bytes(), self.original)
        self.assertEqual(list(self.output.parent.glob(".story-skill-package-*.zip")), [])
        with zipfile.ZipFile(self.output) as archive:
            self.assertEqual(len(archive.namelist()), len(package.SUITE_FILES))
            self.assertIsNone(archive.testzip())

    def test_partial_write_then_disk_full_preserves_previous_archive(self):
        self.prepare_rebuild()
        real_write = zipfile.ZipFile.writestr
        writes = 0

        def write(archive, name, content, *args, **kwargs):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError(errno.ENOSPC, "simulated disk full after a real member write")
            return real_write(archive, name, content, *args, **kwargs)

        with patch.object(zipfile.ZipFile, "writestr", write):
            with self.assertRaises(OSError) as failure:
                package.package()
        self.assertEqual(failure.exception.errno, errno.ENOSPC)
        self.assertEqual(writes, 2)
        self.assert_old_archive_preserved()

    def test_crc_validation_failure_preserves_previous_archive(self):
        self.prepare_rebuild()
        with patch.object(zipfile.ZipFile, "testzip", return_value="story-skill/SKILL.md"):
            with self.assertRaisesRegex(RuntimeError, "Archive verification failed"):
                package.package()
        self.assert_old_archive_preserved()

    def test_valid_crc_but_wrong_member_content_is_rejected(self):
        self.prepare_rebuild()
        real_write = zipfile.ZipFile.writestr

        def write(archive, name, content, *args, **kwargs):
            if name.filename == "story-skill/SKILL.md":
                content = b"different content with a valid ZIP checksum"
            return real_write(archive, name, content, *args, **kwargs)

        with patch.object(zipfile.ZipFile, "writestr", write):
            with self.assertRaisesRegex(RuntimeError, "Archive content differs"):
                package.package()
        self.assert_old_archive_preserved()

    def test_atomic_replace_failure_preserves_previous_archive(self):
        self.prepare_rebuild()
        with patch.object(package.os, "replace", side_effect=PermissionError("simulated locked destination")):
            with self.assertRaises(PermissionError):
                package.package()
        self.assert_old_archive_preserved()

    def test_success_uses_same_directory_stage_and_reproduces_hash(self):
        real_replace = package.os.replace
        publication = []

        def replace(source, destination):
            source, destination = Path(source), Path(destination)
            publication.append((source, destination))
            self.assertEqual(source.parent, self.output.parent)
            self.assertNotEqual(source, self.output)
            self.assertEqual(destination, self.output)
            with zipfile.ZipFile(source) as archive:
                self.assertEqual(len(archive.namelist()), len(package.SUITE_FILES))
                self.assertIsNone(archive.testzip())
            return real_replace(source, destination)

        with patch.object(package.os, "replace", side_effect=replace):
            second = package.package()
        self.assertEqual(len(publication), 1)
        self.assertEqual(second["sha256"], self.first["sha256"])
        self.assertEqual(second["sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())
        self.assertEqual(second["bytes"], self.output.stat().st_size)
        self.assert_old_archive_preserved()

    def test_unknown_payload_file_or_missing_dependency_preserves_previous_archive(self):
        extra = self.source / "story-skill-write/private-draft.md"
        extra.write_bytes(b"must never be shipped")
        with self.assertRaisesRegex(ValueError, "reviewed file manifest"):
            package.package()
        self.assert_old_archive_preserved()
        extra.unlink()
        (self.source / "story-skill-cover/SKILL.md").unlink()
        with self.assertRaisesRegex(ValueError, "reviewed file manifest"):
            package.package()
        self.assert_old_archive_preserved()

    def test_sibling_project_material_is_never_packaged(self):
        draft = self.source / "my-novel/draft.md"
        draft.parent.mkdir()
        draft.write_bytes(b"book manuscript")
        result = package.package()
        self.assertEqual(result["sha256"], self.first["sha256"])


if __name__ == "__main__":
    unittest.main()
