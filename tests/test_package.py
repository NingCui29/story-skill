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

    def test_output_inside_source_is_rejected_without_creating_files_or_directories(self):
        with tempfile.TemporaryDirectory(prefix="story-package-source-output-") as directory:
            root = Path(directory)
            source = root / "skills"
            fixture_suite(source)
            before = {path.relative_to(source): path.read_bytes()
                      for path in source.rglob("*") if path.is_file()}
            for parent in (source, source / "story-skill/new-output-directory"):
                with self.subTest(parent=parent), patch.object(package, "SOURCE", source):
                    with self.assertRaisesRegex(ValueError, "outside the skill source"):
                        package.package(parent / "story-skill-0.6.1.zip")
                self.assertEqual({path.relative_to(source): path.read_bytes()
                                  for path in source.rglob("*") if path.is_file()}, before)
            self.assertFalse((source / "story-skill/new-output-directory").exists())

    def test_output_aliases_into_source_are_rejected_and_source_still_packages(self):
        with tempfile.TemporaryDirectory(prefix="story-package-source-alias-") as directory:
            root = Path(directory)
            source = root / "skills"
            fixture_suite(source)
            alias = root / "source-alias"
            output_alias = root / "story-skill-0.6.1.zip"
            runtime = source / "story-skill/scripts/story.py"
            before = runtime.read_bytes()
            try:
                alias.symlink_to(source, target_is_directory=True)
                output_alias.symlink_to(runtime)
            except OSError:
                self.skipTest("Creating links is unavailable")
            with patch.object(package, "SOURCE", source):
                for output in (alias / "story-skill/new-output/story-skill-0.6.1.zip", output_alias):
                    with self.subTest(output=output), self.assertRaisesRegex(ValueError, "outside the skill source"):
                        package.package(output)
                self.assertEqual(runtime.read_bytes(), before)
                self.assertTrue(output_alias.is_symlink())
                self.assertFalse((source / "story-skill/new-output").exists())
                result = package.package(root / "artifacts/story-skill-0.6.1.zip")
            self.assertTrue(Path(result["archive"]).is_file())
            self.assertEqual(result["files"], len(package.suite_files("0.6.1")))

    def test_manifest_is_bound_to_version_and_unknown_families_are_rejected(self):
        layouts = {"0.6.0": package.SUITE_FILES_V060, "0.6.1": package.SUITE_FILES_V061, "0.6.2": package.SUITE_FILES_V061, "0.6.3": package.SUITE_FILES_V061, "0.6.4": package.SUITE_FILES_V061, "0.6.5": package.SUITE_FILES_V065, "0.6.6": package.SUITE_FILES_V065, "0.6.7": package.SUITE_FILES_V065, "0.6.8": package.SUITE_FILES_V065, "0.6.9": package.SUITE_FILES_V069, "0.6.10": package.SUITE_FILES_V0610, "0.6.11": package.SUITE_FILES_V0611, "0.6.12": package.SUITE_FILES_V0612, "0.6.13": package.SUITE_FILES_V0613, "0.6.14": package.SUITE_FILES_V0614, "0.6.15": package.SUITE_FILES_V0615, "0.6.16": package.SUITE_FILES_V0616, "0.6.17": package.SUITE_FILES_V0617, "0.6.18": package.SUITE_FILES_V0618, "0.6.19": package.SUITE_FILES_V0619, "0.6.20": package.SUITE_FILES_V0620, "0.6.21": package.SUITE_FILES_V0621, "0.6.22": package.SUITE_FILES_V0622, "0.6.23": package.SUITE_FILES_V0623, "0.6.24": package.SUITE_FILES_V0624}
        skill_layouts = {"0.6.0": package.SKILL_NAMES_V060, "0.6.1": package.SKILL_NAMES_V061, "0.6.2": package.SKILL_NAMES_V061, "0.6.3": package.SKILL_NAMES_V061, "0.6.4": package.SKILL_NAMES_V061, "0.6.5": package.SKILL_NAMES_V061, "0.6.6": package.SKILL_NAMES_V061, "0.6.7": package.SKILL_NAMES_V061, "0.6.8": package.SKILL_NAMES_V061, "0.6.9": package.SKILL_NAMES_V069, "0.6.10": package.SKILL_NAMES_V0610, "0.6.11": package.SKILL_NAMES_V0611, "0.6.12": package.SKILL_NAMES_V0612, "0.6.13": package.SKILL_NAMES_V0613, "0.6.14": package.SKILL_NAMES_V0614, "0.6.15": package.SKILL_NAMES_V0615, "0.6.16": package.SKILL_NAMES_V0616, "0.6.17": package.SKILL_NAMES_V0617, "0.6.18": package.SKILL_NAMES_V0618, "0.6.19": package.SKILL_NAMES_V0619, "0.6.20": package.SKILL_NAMES_V0620, "0.6.21": package.SKILL_NAMES_V0621, "0.6.22": package.SKILL_NAMES_V0622, "0.6.23": package.SKILL_NAMES_V0623, "0.6.24": package.SKILL_NAMES_V0624}
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
        self.assertEqual(len(package.SUITE_FILES_V065), 40)
        self.assertEqual(package.suite_files("0.6.8"), package.SUITE_FILES_V065)
        self.assertEqual(len(package.SUITE_FILES_V069), 44)
        self.assertEqual(package.suite_files("0.6.9"), package.SUITE_FILES_V069)
        self.assertEqual(len(package.SUITE_FILES), 69)
        self.assertEqual(package.SUITE_FILES, package.SUITE_FILES_V0623)
        self.assertEqual(len(package.SUITE_FILES_V0621), 64)
        self.assertEqual(len(package.SUITE_FILES_V0622), 69)
        self.assertEqual(package.SUITE_FILES_V0623, package.SUITE_FILES_V0622)
        self.assertEqual(set(package.SUITE_FILES_V0622) - set(package.SUITE_FILES_V0621), {
            "story-skill/scripts/story_rank.py",
            "story-skill/scripts/story_rank_sources.py",
            "story-skill/scripts/story_research.py",
            "story-skill-research/references/rank-scan.md",
            "story-skill-research/references/research-handoff.md",
        })
        self.assertEqual(len(package.SUITE_FILES_V0619), 62)
        self.assertEqual(package.SUITE_FILES_V0620, package.SUITE_FILES_V0619)
        self.assertEqual(len(package.SUITE_FILES_V0620), 62)
        self.assertEqual(set(package.SUITE_FILES_V0621) - set(package.SUITE_FILES_V0620), {
            "story-skill/scripts/story_chrome_worker.py",
            "story-skill/scripts/story_chrome_worker.mjs",
        })
        self.assertEqual(len(package.SUITE_FILES_V0618), 55)
        self.assertEqual(set(package.SUITE_FILES_V0619) - set(package.SUITE_FILES_V0618), {
            "story-skill/scripts/story_workbench_books.py",
            "story-skill/scripts/story_workbench_archives.py",
            "story-skill/scripts/story_platform_drafts.py",
            "story-skill/scripts/story_platform_browser.py",
            "story-skill/scripts/story_platform_chrome_bridge.py",
            "story-skill/scripts/story_chrome_bridge.mjs",
            "story-skill-publish/references/platform-drafts.md",
        })
        self.assertEqual(len(package.SUITE_FILES_V0617), 52)
        self.assertEqual(set(package.SUITE_FILES_V0618) - set(package.SUITE_FILES_V0617), {
            "story-skill-write/references/writing-handoff.md",
            "story-skill-write/references/chapter-continuity.md",
            "story-skill-plan/references/short-platform.md",
        })
        self.assertEqual(len(package.SUITE_FILES_V0616), 51)
        self.assertNotIn("story-skill-plan/references/qidian-categories.md", package.SUITE_FILES_V0616)
        self.assertEqual(set(package.SUITE_FILES_V0617) - set(package.SUITE_FILES_V0616), {
            "story-skill-plan/references/qidian-categories.md",
        })
        self.assertEqual(package.SUITE_FILES_V0612, package.SUITE_FILES_V0611)
        self.assertEqual(len(package.SUITE_FILES_V0612), 49)
        self.assertNotIn("story-skill-plan/references/outline.md", package.SUITE_FILES_V0612)
        self.assertEqual(set(package.SUITE_FILES_V0613) - set(package.SUITE_FILES_V0612), {
            "story-skill-plan/references/outline.md",
        })
        self.assertEqual(package.SKILL_NAMES, package.SKILL_NAMES_V0623)
        self.assertEqual(len(package.SUITE_FILES_V0613), 50)
        self.assertNotIn("story-skill/references/workbench.md", package.SUITE_FILES_V0613)
        self.assertEqual(set(package.SUITE_FILES_V0614) - set(package.SUITE_FILES_V0613), {
            "story-skill/references/workbench.md",
        })
        self.assertEqual(package.SKILL_NAMES_V0614, package.SKILL_NAMES_V0613)
        self.assertEqual(package.SUITE_FILES_V0615, package.SUITE_FILES_V0614)
        self.assertEqual(package.SKILL_NAMES_V0615, package.SKILL_NAMES_V0614)
        self.assertEqual(len(package.SKILL_NAMES_V0615), 8)
        self.assertEqual(package.SKILL_NAMES_V0612, package.SKILL_NAMES_V0611)
        self.assertEqual(len(package.SUITE_FILES_V0610), 45)
        self.assertEqual(set(package.SUITE_FILES_V0611) - set(package.SUITE_FILES_V0610), {
            "story-skill-plan/references/blurb.md",
            "story-skill-research/references/reader-validation.md",
            "story-skill-write/references/punctuation.md",
            "story-skill/scripts/story_punctuation.py",
        })
        self.assertEqual(set(package.SUITE_FILES_V0610) - set(package.SUITE_FILES_V069), {
            "story-skill-write/references/content-review.md",
        })
        self.assertEqual(set(package.SUITE_FILES_V069) - set(package.SUITE_FILES_V065), {
            "story-skill/scripts/story_outline.py",
            "story-skill-review/references/fanqie-content-review.md",
            "story-skill-review/references/qimao-content-review.md",
            "story-skill-write/references/suspense-evidence.md",
        })
        self.assertIn("story-skill/scripts/story_workbench.py", package.SUITE_FILES)
        for version in ("0.5.11", "0.6.00", "0.6.25", "0.6.99", "1.0.0", "2.4.6"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "no reviewed suite layout"):
                package.suite_files(version)
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "no reviewed suite layout"):
                package.skill_names(version)

    def test_missing_reviewed_file_cannot_replace_existing_archive(self):
        for version, missing in (("0.6.1", "story-skill-publish/SKILL.md"),
                                 ("0.6.10", "story-skill-write/references/content-review.md"),
                                 ("0.6.11", "story-skill-plan/references/blurb.md"),
                                 ("0.6.11", "story-skill-write/references/punctuation.md"),
                                 ("0.6.11", "story-skill/scripts/story_punctuation.py"),
                                 ("0.6.11", "story-skill-research/references/reader-validation.md"),
                                 ("0.6.13", "story-skill-plan/references/outline.md"),
                                 ("0.6.14", "story-skill/references/workbench.md"),
                                 ("0.6.15", "story-skill/references/workbench.md"),
                                 ("0.6.17", "story-skill-plan/references/qidian-categories.md"),
                                 ("0.6.18", "story-skill-write/references/writing-handoff.md"),
                                 ("0.6.18", "story-skill-write/references/chapter-continuity.md"),
                                 ("0.6.18", "story-skill-plan/references/short-platform.md"),
                                 ("0.6.19", "story-skill/scripts/story_workbench_books.py"),
                                 ("0.6.19", "story-skill/scripts/story_workbench_archives.py"),
                                 ("0.6.20", "story-skill/scripts/story_outline.py"),
                                 ("0.6.21", "story-skill/scripts/story_chrome_worker.py"),
                                 ("0.6.21", "story-skill/scripts/story_chrome_worker.mjs"),
                                 ("0.6.22", "story-skill/scripts/story_rank.py"),
                                 ("0.6.22", "story-skill/scripts/story_rank_sources.py"),
                                 ("0.6.22", "story-skill/scripts/story_research.py"),
                                 ("0.6.22", "story-skill-research/references/rank-scan.md"),
                                 ("0.6.22", "story-skill-research/references/research-handoff.md")):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "skills"
                fixture_suite(source, version)
                (source / missing).unlink()
                output = root / f"story-skill-{version}.zip"
                output.write_bytes(b"existing reviewed artifact")
                with patch.object(package, "SOURCE", source):
                    with self.assertRaisesRegex(ValueError, "reviewed file manifest"):
                        package.package(output)
                self.assertEqual(output.read_bytes(), b"existing reviewed artifact")

    def test_v0610_with_reader_validation_cannot_replace_existing_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "skills"
            fixture_suite(source, "0.6.10")
            reference = source / "story-skill-research/references/reader-validation.md"
            reference.parent.mkdir(parents=True, exist_ok=True)
            reference.write_bytes(b"new reference\n")
            output = root / "story-skill-0.6.10.zip"
            output.write_bytes(b"existing reviewed artifact")
            with patch.object(package, "SOURCE", source):
                with self.assertRaisesRegex(ValueError, "reviewed file manifest"):
                    package.package(output)
            self.assertEqual(output.read_bytes(), b"existing reviewed artifact")

    def test_published_v0620_rejects_new_worker_files_without_replacing_archive(self):
        for filename in ("story_chrome_worker.py", "story_chrome_worker.mjs"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "skills"
                fixture_suite(source, "0.6.20")
                (source / "story-skill/scripts" / filename).write_bytes(b"new worker\n")
                output = root / "story-skill-0.6.20.zip"
                output.write_bytes(b"existing reviewed artifact")
                with patch.object(package, "SOURCE", source):
                    with self.assertRaisesRegex(ValueError, "reviewed file manifest"):
                        package.package(output)
                self.assertEqual(output.read_bytes(), b"existing reviewed artifact")

    def test_published_v0621_rejects_rank_files_without_replacing_archive(self):
        for relative in ("story-skill/scripts/story_rank.py",
                         "story-skill/scripts/story_rank_sources.py",
                         "story-skill/scripts/story_research.py",
                         "story-skill-research/references/rank-scan.md",
                         "story-skill-research/references/research-handoff.md"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "skills"
                fixture_suite(source, "0.6.21")
                output = root / "story-skill-0.6.21.zip"
                with patch.object(package, "SOURCE", source):
                    package.package(output)
                    original = output.read_bytes()
                    (source / relative).write_bytes(b"new rank capability\n")
                    with self.assertRaisesRegex(ValueError, "reviewed file manifest"):
                        package.package(output)
                self.assertEqual(output.read_bytes(), original)
                with zipfile.ZipFile(output) as archive:
                    self.assertEqual(archive.namelist(), list(package.SUITE_FILES_V0621))

    def test_v0622_cannot_use_the_published_v0621_archive_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "skills"
            fixture_suite(source, "0.6.22")
            output = root / "story-skill-0.6.21.zip"
            output.write_bytes(b"published v0.6.21 artifact")
            with patch.object(package, "SOURCE", source):
                with self.assertRaisesRegex(ValueError, "Archive filename must be story-skill-0.6.22.zip"):
                    package.package(output)
            self.assertEqual(output.read_bytes(), b"published v0.6.21 artifact")


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
            self.assertEqual(len(archive.namelist()), len(package.suite_files(self.first["version"])))
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
                self.assertEqual(len(archive.namelist()), len(package.suite_files(self.first["version"])))
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
