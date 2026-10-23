"""Reject prohibited implementations while allowing documentation and source URLs."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('technology_policy', ROOT / 'scripts/check_forbidden_technologies.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class TechnologyPolicyTests(unittest.TestCase):
    def test_vendor_payload_names_and_php_sources_are_rejected(self):
        for name in ('Qt6Core.dll', 'Qt5WebEngineCore.dll', '_internal/PySide6/thing.pyd',
                     'libQt6Widgets.so.6', 'QtWebEngineProcess.exe', 'php.exe', 'php8ts.dll',
                     'server/index.php', 'vendor/shiboken6/file', 'tools/php-cgi.exe',
                     'Qt6Charts.dll', 'Qt6Bluetooth.dll', 'QtCore.framework/QtCore',
                     'QtCore4.dll', 'libphp.so', 'libphp8.so', 'qwindows.dll', 'libqcocoa.dylib'):
            with self.subTest(name=name):
                self.assertTrue(policy.forbidden_path(name))

    def test_safe_names_and_external_php_links_are_not_implementations(self):
        for name in ('Microsoft.Web.WebView2.Core.dll', 'PresentationFramework.dll',
                     'runtime/python.exe', 'Scripts/story.py', 'ui/Quarter.cs'):
            self.assertFalse(policy.forbidden_path(name), name)
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'source.py'
            path.write_text('source = "https://example.org/showmsg.php?id=1"\n')
            self.assertEqual(policy.source_violations(path, 'source.py'), [])

    def test_imports_and_dependencies_are_rejected(self):
        fixtures = [('app.py', 'from PySide6.QtWidgets import QApplication'),
                    ('app.py', 'import PyQt6 as ui'),
                    ('app.py', '__import__("shiboken6")'),
                    ('app.py', 'importlib.import_module("PySide6.QtCore")'),
                    ('requirements.txt', 'PySide6==6.10.3'),
                    ('build.yml', 'run: pip install PyQt6'),
                    ('app.cpp', '#include <QtWidgets/QApplication>'),
                    ('build.txt', 'find_package(Qt6 REQUIRED COMPONENTS Widgets)'),
                    ('server.js', '<?php echo 1; ?>')]
        with tempfile.TemporaryDirectory() as raw:
            for name, content in fixtures:
                with self.subTest(content=content):
                    path = Path(raw) / name
                    path.write_text(content)
                    self.assertTrue(policy.source_violations(path, name))

    def test_distribution_has_no_excluded_vendor_directories(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / 'vendor').mkdir()
            (root / 'vendor/Qt6Core.dll').write_bytes(b'MZ')
            with self.assertRaises(RuntimeError):
                policy.assert_clean_tree(root)


if __name__ == '__main__':
    unittest.main()
