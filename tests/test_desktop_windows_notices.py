"""Release notices must match the dependency version and their frozen bytes."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
try:
    from build_desktop_windows import copy_supplementary_notices
finally:
    sys.path.pop(0)


class WindowsNoticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.area = Path(self.temp.name)
        self.source, self.app = self.area / '声明 来源', self.area / 'app'
        self.source.mkdir()
        self.files = {}
        for name in ('LGPL-3.0-only.txt', 'GPL-2.0-only.txt', 'GPL-3.0-only.txt',
                     'README.md', 'QtWebEngine/index.html', 'QtWebEngine/component.html'):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = ('frozen test notice: ' + name + '\n').encode()
            path.write_bytes(raw)
            self.files[name] = hashlib.sha256(raw).hexdigest()
        self.manifest = {'schema': 1, 'pyside_version': '6.10.3', 'qt_version': '6.10.3',
                         'files': self.files}
        self.save()

    def save(self):
        (self.source / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def copy(self, **kwargs):
        return copy_supplementary_notices(self.app, source=self.source,
            pyside_version=kwargs.get('pyside_version', '6.10.3'),
            qt_version=kwargs.get('qt_version', '6.10.3'))

    def test_complete_versioned_notice_tree_is_copied_byte_for_byte(self):
        result = self.copy()
        self.assertEqual(result['files'], len(self.files) + 1)
        target = self.app / 'ThirdPartyLicenses/Qt'
        self.assertEqual({p.relative_to(target).as_posix(): p.read_bytes()
                          for p in target.rglob('*') if p.is_file()},
                         {p.relative_to(self.source).as_posix(): p.read_bytes()
                          for p in self.source.rglob('*') if p.is_file()})

    def test_dependency_version_changes_require_new_notices(self):
        for field in ('pyside_version', 'qt_version'):
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.copy(**{field: '6.11.0'})
        self.assertFalse(self.app.exists())

    def test_changed_notice_is_rejected_before_copying(self):
        (self.source / 'LGPL-3.0-only.txt').write_text('changed', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            self.copy()
        self.assertFalse(self.app.exists())

    def test_missing_full_license_or_unlisted_file_is_rejected(self):
        del self.files['GPL-3.0-only.txt']
        self.save()
        with self.assertRaises(RuntimeError):
            self.copy()
        self.files['GPL-3.0-only.txt'] = hashlib.sha256(
            (self.source / 'GPL-3.0-only.txt').read_bytes()).hexdigest()
        self.save()
        (self.source / 'extra.txt').write_text('untracked', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            self.copy()
        self.assertFalse(self.app.exists())

    def test_notice_manifest_cannot_escape_its_source(self):
        self.files['../outside.txt'] = '0' * 64
        self.save()
        with self.assertRaises(RuntimeError):
            self.copy()
        self.assertFalse(self.app.exists())

    def test_linked_notice_is_rejected(self):
        path = self.source / 'README.md'
        path.unlink()
        outside = self.area / 'outside.txt'
        outside.write_text('outside', encoding='utf-8')
        try:
            path.symlink_to(outside)
        except OSError as error:
            self.skipTest('Test environment cannot create a symlink: ' + str(error))
        with self.assertRaises(RuntimeError):
            self.copy()
        self.assertFalse(self.app.exists())
