"""Runtime license provenance and self-contained Windows build contracts."""
import base64
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
try:
    import build_desktop_windows as build
finally:
    sys.path.pop(0)


def project(version='0.2.0', sdk='1.0.4258.31'):
    return ('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><Version>' + version +
            '</Version><TargetFramework>net10.0-windows</TargetFramework></PropertyGroup>'
            '<ItemGroup><PackageReference Include="Microsoft.Web.WebView2" Version="' + sdk +
            '" /></ItemGroup></Project>')


class WindowsNoticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.area = Path(self.temp.name)
        self.app, self.packages, self.python = (self.area / name for name in ('app', 'packages', 'python'))
        self.app.mkdir(); self.python.mkdir()
        (self.python / 'LICENSE.txt').write_text('Python fixture license')
        self.config = {'packages': {'Microsoft.Web.WebView2': '1.0.4258.31'}}
        for name in build.NOTICE_PACKAGES:
            version = '1.0.4258.31' if name == 'microsoft.web.webview2' else '10.0.1'
            directory = self.packages / name / version
            directory.mkdir(parents=True)
            for leaf in ('LICENSE.txt', 'NOTICE.txt'):
                (directory / leaf).write_text(name + ' ' + leaf)
            (directory / (name + '.' + version + '.nupkg.sha512')).write_text(base64.b64encode(b'x' * 64).decode())

    def copy(self):
        return build.copy_runtime_notices(self.app, self.packages, self.python, self.config)

    def test_copies_exact_runtime_sdk_and_python_notices_with_provenance(self):
        report = self.copy()
        target = self.app / 'ThirdPartyLicenses'
        manifest = json.loads((target / 'manifest.json').read_text())
        self.assertEqual(manifest['schema'], 2)
        self.assertEqual(manifest['ui_stack'], 'wpf-webview2')
        self.assertEqual(set(manifest['components']), set(build.NOTICE_PACKAGES) | {'Python'})
        self.assertEqual(report['files'], 7)
        self.assertEqual(report['manifest_sha256'], build.digest_file(target / 'manifest.json'))
        for name, digest in manifest['files'].items():
            self.assertEqual(build.digest_file(target / name), digest)
        self.assertEqual((target / 'Python/LICENSE.txt').read_bytes(), (self.python / 'LICENSE.txt').read_bytes())

    def test_missing_license_or_package_integrity_receipt_is_rejected(self):
        directory = self.packages / build.NOTICE_PACKAGES[0] / '10.0.1'
        (directory / 'LICENSE.txt').unlink()
        with self.assertRaisesRegex(RuntimeError, 'Missing runtime license'):
            self.copy()

    def test_fixed_sdk_version_must_match_licenses(self):
        self.config['packages']['Microsoft.Web.WebView2'] = '1.0.1.1'
        with self.assertRaisesRegex(RuntimeError, 'version mismatch'):
            self.copy()

    def test_linked_runtime_notice_is_rejected(self):
        directory = self.packages / build.NOTICE_PACKAGES[0] / '10.0.1'
        license = directory / 'LICENSE.txt'
        license.unlink()
        try:
            license.symlink_to(self.python / 'LICENSE.txt')
        except OSError as error:
            self.skipTest('Symlink permission unavailable: ' + str(error))
        with self.assertRaisesRegex(RuntimeError, 'Linked runtime notices'):
            self.copy()

    def test_project_requires_exact_sdk_and_net10(self):
        path = self.area / build.PROJECT
        path.parent.mkdir(parents=True)
        path.write_text(project())
        config = build.project_configuration(self.area)
        self.assertEqual(config['client_version'], '0.2.0')
        self.assertTrue(config['self_contained'])
        self.assertEqual(config['runtime_identifier'], 'win-x64')
        path.write_text(project(sdk='1.*'))
        with self.assertRaises(RuntimeError):
            build.project_configuration(self.area)
        path.write_text(project().replace('net10.0-windows', 'net9.0-windows'))
        with self.assertRaises(RuntimeError):
            build.project_configuration(self.area)

    def test_source_receipt_excludes_build_outputs_but_covers_cs_and_xaml(self):
        directory = self.area / build.PROJECT.parent
        directory.mkdir(parents=True)
        for name in ('App.cs', 'MainWindow.xaml', 'StoryWorkbench.csproj', 'app.manifest', 'bin/Generated.cs', 'obj/Generated.json'):
            path = directory / name
            path.parent.mkdir(exist_ok=True)
            path.write_text(name)
        files = build.client_source_hashes(self.area)
        self.assertEqual(len(files), 4)
        self.assertTrue(all('/bin/' not in name and '/obj/' not in name for name in files))

    def test_download_redirects_are_limited_to_microsoft_https(self):
        for url in (build.WEBVIEW_DOWNLOAD, 'https://msedge.sf.dl.delivery.mp.microsoft.com/files/runtime.exe'):
            self.assertTrue(build.allowed_microsoft_url(url))
        for url in ('http://go.microsoft.com/x', 'https://go.microsoft.com.attacker.example/x',
                    'https://user@go.microsoft.com/x', 'https://go.microsoft.com:8080/x'):
            self.assertFalse(build.allowed_microsoft_url(url))

    def test_pe_header_requires_valid_signature_and_reports_architecture(self):
        path = self.area / 'host.exe'
        raw = bytearray(256)
        raw[:2] = b'MZ'; struct.pack_into('<I', raw, 60, 128)
        raw[128:132] = b'PE\0\0'; struct.pack_into('<H', raw, 132, 0x8664)
        path.write_bytes(raw)
        self.assertEqual(build.pe_machine(path), 0x8664)
        raw[128:132] = b'NOPE'; path.write_bytes(raw)
        with self.assertRaises(RuntimeError):
            build.pe_machine(path)


if __name__ == '__main__':
    unittest.main()
