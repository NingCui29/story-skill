"""Exercise dual-installer orchestration with a synthetic compiler, never a native build claim."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
try:
    import build_windows_installers as builder
finally:
    sys.path.pop(0)


class DesktopUpdateBuildTests(unittest.TestCase):
    def test_native_probe_refuses_an_existing_registered_installation_before_starting(self):
        spec = importlib.util.spec_from_file_location('update_probe', ROOT / 'tests/desktop_update_package_probe.py')
        probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with patch.object(probe.sys, 'platform', 'win32'), \
                    patch.object(probe, 'registry_snapshot', return_value=[['Inno Setup: App Path', str(root / 'real'), 1]]), \
                    patch.object(probe.subprocess, 'run') as launch:
                with self.assertRaisesRegex(RuntimeError, 'clean Windows'):
                    probe.probe(root / 'setup.exe', root / 'payload', root / 'area')
                launch.assert_not_called()
                self.assertFalse((root / 'area').exists())

    def fixture(self, root):
        source = root / 'portable'
        source.mkdir()
        files = {'StoryWorkbench.exe': b'host', 'runtime/python.exe': b'python',
                 'Prerequisites/MicrosoftEdgeWebView2RuntimeInstallerX64.exe': b'offline browser installer'}
        for name, data in files.items():
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (source / 'build-manifest.json').write_text(json.dumps({'client_version': '0.3.0',
            'ui_stack': 'wpf-webview2', 'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}))
        with zipfile.ZipFile(source.with_suffix('.zip'), 'w') as archive:
            for path in source.rglob('*'):
                if path.is_file(): archive.write(path, 'portable/' + path.relative_to(source).as_posix())
        return source

    def test_complete_and_lean_installers_get_exact_plans_and_keep_original_source(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); source = self.fixture(root); output = root / 'artifacts'
            calls = []
            def compile(arguments, **kwargs):
                calls.append(arguments)
                suffix = '-update-setup.exe' if '/DUpdateOnly=1' in arguments else '-setup.exe'
                (output / ('story-workbench-0.3.0-windows-x86_64' + suffix)).write_bytes(b'MZ fixture ' + suffix.encode())
            with patch.object(builder.sys, 'platform', 'win32'), \
                    patch.object(builder, 'project_configuration', return_value={'client_version': '0.3.0'}), \
                    patch.object(builder.subprocess, 'run', side_effect=compile):
                result = builder.build(source, output, root / 'compiler.exe')
                self.assertEqual(len(calls), 2)
                self.assertNotIn('/DUpdateOnly=1', calls[0])
                self.assertIn('/DUpdateOnly=1', calls[1])
                self.assertFalse(result['native_install_verified'])
                self.assertEqual(len(result['downloads']), 3)
                for record in result['downloads']:
                    plan = Path(record['manifest'])
                    body = json.loads(plan.read_text())
                    self.assertEqual(body['file'] + '.download.json', plan.name)
                    package = output / body['file']
                    self.assertEqual(body['sha256'], hashlib.sha256(package.read_bytes()).hexdigest())
                    self.assertTrue(plan.with_name(plan.name + '.sha256').is_file())
                self.assertFalse((output / 'update-payload/Prerequisites/MicrosoftEdgeWebView2RuntimeInstallerX64.exe').exists())
                self.assertTrue((source / 'Prerequisites/MicrosoftEdgeWebView2RuntimeInstallerX64.exe').exists())
                with self.assertRaises(ValueError): builder.build(source, output, root / 'compiler.exe')

    def test_installer_refuses_a_different_portable_version(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); source = self.fixture(root)
            with patch.object(builder.sys, 'platform', 'win32'), \
                    patch.object(builder, 'project_configuration', return_value={'client_version': '0.3.1'}):
                with self.assertRaisesRegex(ValueError, 'versions differ'):
                    builder.build(source, root / 'out', root / 'compiler.exe')
