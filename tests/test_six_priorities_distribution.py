"""New runtime dependencies have their own layout; published lists stay fixed."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_package import package, fixture_suite, ROOT
from test_install_suite import installer
from test_package_npm import npm


class SixPrioritiesDistributionTests(unittest.TestCase):
    added = {'story-skill/scripts/story_tasks.py',
             'story-skill/scripts/story_reader_feedback.py',
             'story-skill-research/references/reader-records.md'}

    def test_new_layout_is_consistent_without_changing_published_layout(self):
        for module in (package, installer, npm):
            with self.subTest(module=module.__name__):
                self.assertEqual(len(module.SUITE_FILES_V0627), 69)
                self.assertEqual(set(module.SUITE_FILES_V0628) - set(module.SUITE_FILES_V0627), self.added)
                self.assertEqual(module.SKILL_NAMES_V0628, module.SKILL_NAMES_V0627)
        self.assertEqual(package.SUITE_FILES_V0628, installer.SUITE_FILES_V0628)
        self.assertEqual(package.SUITE_FILES_V0628, npm.SUITE_FILES_V0628)

    def test_missing_new_runtime_or_reference_cannot_produce_a_complete_archive(self):
        for missing in self.added:
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as area:
                source = Path(area) / 'skills'
                fixture_suite(source, '0.6.28')
                (source / missing).unlink()
                destination = Path(area) / 'story-skill-0.6.28.zip'
                with patch.object(package, 'SOURCE', source):
                    with self.assertRaisesRegex(ValueError, 'reviewed file manifest'):
                        package.package(destination)
                self.assertFalse(destination.exists())

    def test_new_files_cannot_be_relabelled_as_old_release(self):
        for added in self.added:
            with self.subTest(added=added), tempfile.TemporaryDirectory() as area:
                source = Path(area) / 'skills'
                fixture_suite(source, '0.6.27')
                (source / added).write_text('new development content', encoding='utf-8')
                destination = Path(area) / 'story-skill-0.6.27.zip'
                destination.write_bytes(b'existing release')
                with patch.object(package, 'SOURCE', source):
                    with self.assertRaisesRegex(ValueError, 'reviewed file manifest'):
                        package.package(destination)
                self.assertEqual(destination.read_bytes(), b'existing release')

    def test_desktop_runtime_includes_the_new_import_dependencies(self):
        spec = importlib.util.spec_from_file_location('six_desktop_builder', ROOT / 'scripts/build_desktop_macos.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        self.assertTrue({'story_tasks.py', 'story_task_worker.py', 'story_reader_feedback.py'} <= set(builder.RUNTIME_FILES))

    def test_development_wrapper_does_not_offer_nonexistent_release_links(self):
        _, files = npm.wrapper_files('0.6.28')
        readme = files['README.md'].decode()
        self.assertIn('Unreleased development snapshot', readme)
        self.assertNotIn('/blob/v0.6.28/', readme)
        self.assertNotIn('/releases/download/v0.6.28/', readme)


if __name__ == '__main__':
    unittest.main()
