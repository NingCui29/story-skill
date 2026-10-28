"""Lean update payloads retain the full application and omit one browser installer."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prepare_windows_update', ROOT / 'scripts/prepare_windows_update.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PrepareWindowsUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source, self.output = Path(self.temp.name) / 'full', Path(self.temp.name) / 'update'
        self.source.mkdir()
        self.files = {'StoryWorkbench.exe': b'host', 'coreclr.dll': b'dotnet', 'runtime/python.exe': b'python',
                      'Scripts/story.py': b'print("fixture")', 'ThirdPartyLicenses/Python/LICENSE.txt': b'license',
                      prepare.WEBVIEW: b'offline browser installer'}
        for name, data in self.files.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.manifest = {'ui_stack': 'wpf-webview2', 'client_version': '0.3.0',
                         'files': {name: hashlib.sha256(data).hexdigest() for name, data in self.files.items()}}
        self.write_manifest()

    def write_manifest(self):
        (self.source / 'build-manifest.json').write_text(json.dumps(self.manifest))

    def test_preserves_all_host_runtime_and_license_bytes_with_separate_manifest(self):
        report = prepare.prepare(self.source, self.output)
        self.assertTrue(report['ok'])
        self.assertFalse(report['native_install_verified'])
        self.assertEqual(report['removed_bytes'], len(self.files[prepare.WEBVIEW]))
        self.assertFalse((self.output / prepare.WEBVIEW).exists())
        result = json.loads((self.output / 'build-manifest.json').read_text())
        self.assertEqual(set(result['files']), set(self.files) - {prepare.WEBVIEW})
        for name in result['files']:
            self.assertEqual((self.output / name).read_bytes(), self.files[name])
        self.assertTrue((self.source / prepare.WEBVIEW).is_file())
        self.assertEqual(result['webview2_runtime']['kind'], 'existing')
        self.assertNotIn(b'\r\n', (self.output / 'build-manifest.json').read_bytes())

    def test_changed_missing_extra_and_linked_source_inputs_are_rejected(self):
        target = self.source / 'StoryWorkbench.exe'
        target.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum'): prepare.prepare(self.source, self.output)
        target.write_bytes(self.files['StoryWorkbench.exe'])
        unknown = self.source / 'extra.dll'; unknown.write_bytes(b'extra')
        with self.assertRaisesRegex(ValueError, 'differs'): prepare.prepare(self.source, self.output)
        unknown.unlink()
        target.unlink()
        with self.assertRaisesRegex(ValueError, 'differs'): prepare.prepare(self.source, self.output)
        try: target.symlink_to(self.source / 'coreclr.dll')
        except OSError: return
        with self.assertRaisesRegex(ValueError, 'Linked'): prepare.prepare(self.source, self.output)

    def test_existing_or_overlapping_output_is_never_overwritten(self):
        with self.assertRaises(ValueError): prepare.prepare(self.source, self.source / 'nested')
        self.output.mkdir()
        with self.assertRaises(ValueError): prepare.prepare(self.source, self.output)
