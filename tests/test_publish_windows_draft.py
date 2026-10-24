import hashlib
import base64
import struct
import contextlib
import io
from types import SimpleNamespace
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('publish_windows_draft', ROOT / 'scripts/publish_windows_draft.py')
publish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish)
SHA = 'a' * 40


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def fixture(area):
    source = area / 'source'
    artifacts = area / 'artifacts'
    runtime = tuple('runtime%d.py' % n for n in range(20))
    write(source / 'scripts/build_desktop_macos.py', ('RUNTIME_FILES = ' + repr(runtime)).encode())
    write(source / 'skills/story-skill/scripts/story.py', b'VERSION = "0.6.24"\n')
    write(source / publish.PROJECT, b'<Project><PropertyGroup><Version>0.2.3</Version><TargetFramework>net10.0-windows</TargetFramework></PropertyGroup><ItemGroup><PackageReference Include="Microsoft.Web.WebView2" Version="1.0.4258.31" /></ItemGroup></Project>')
    files = {}
    for name in runtime:
        files['Scripts/' + name] = ('# runtime fixture ' + name + '\n').encode()
        write(source / 'skills/story-skill/scripts' / name, files['Scripts/' + name])
    for packaged, original in (('backend_runner.py', 'desktop/macos/backend_runner.py'),
                               ('installation_lifecycle.py', 'desktop/windows/installation_lifecycle.py'),
                               ('StorySkill-LICENSE.txt', 'LICENSE'),
                               ('README.md', 'desktop/windows/README.md')):
        files[packaged] = original.encode()
        write(source / original, files[packaged])
    notices = {}
    components = {}
    for name in publish.NOTICE_PACKAGES:
        version = '1.0.4258.31' if name == 'microsoft.web.webview2' else '10.0.1'
        components[name] = {'version': version, 'nuget_sha512': base64.b64encode(b'x' * 64).decode(),
                            'source': 'https://api.nuget.org/v3-flatcontainer/' + name + '/' + version + '/'}
        for leaf in ('LICENSE.txt', 'NOTICE.txt'):
            notices[name + '/' + leaf] = (name + ' ' + leaf).encode()
    components['Python'] = {'version': '3.12.1', 'source': 'https://www.python.org/'}
    notices['Python/LICENSE.txt'] = b'Python fixture license'
    notice_manifest = {'schema': 2, 'ui_stack': 'wpf-webview2', 'components': components,
                       'files': {name: hashlib.sha256(data).hexdigest() for name, data in notices.items()}}
    notices['manifest.json'] = json.dumps(notice_manifest).encode()
    for name, data in notices.items():
        files['ThirdPartyLicenses/' + name] = data
    pe = bytearray(256)
    pe[:2] = b'MZ'; struct.pack_into('<I', pe, 60, 128)
    pe[128:132] = b'PE\0\0'; struct.pack_into('<H', pe, 132, 0x8664)
    files['StoryWorkbench.exe'] = bytes(pe)
    for name in ('coreclr.dll', 'hostfxr.dll', 'hostpolicy.dll', 'PresentationFramework.dll',
                 'Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.Wpf.dll', 'WebView2Loader.dll'):
        files[name] = b'synthetic self-contained runtime'
    frameworks = {'Microsoft.NETCore.App': '10.0.1', 'Microsoft.WindowsDesktop.App': '10.0.1'}
    files['StoryWorkbench.runtimeconfig.json'] = json.dumps({'runtimeOptions': {'includedFrameworks':
        [{'name': name, 'version': version} for name, version in frameworks.items()]}}).encode()
    runtime_path = 'Prerequisites/' + publish.WEBVIEW_INSTALLER
    files[runtime_path] = bytes(pe)
    webview = {'kind': 'evergreen-standalone', 'architecture': 'x64', 'path': runtime_path,
               'source_url': publish.WEBVIEW_DOWNLOAD,
               'resolved_url': 'https://msedge.sf.dl.delivery.mp.microsoft.com/files/' + publish.WEBVIEW_INSTALLER,
               'bytes': len(pe), 'sha256': hashlib.sha256(pe).hexdigest(),
               'signature': {'status': 'Valid', 'subject': 'CN=Microsoft Corporation, O=Microsoft Corporation',
                             'thumbprint': 'synthetic', 'file_version': '1.0.0.0', 'product_name': 'WebView2'}}
    artifacts.mkdir()
    setup = artifacts / publish.SETUP
    setup.write_bytes(b'MZ synthetic installer')
    manifest = {'schema': 2, 'platform': 'windows-x86_64',
                **publish.project_configuration(source), 'story_version': '0.6.24',
                'client_source_sha256': publish.client_source_hashes(source),
                'runtime_notices': {'manifest_sha256': hashlib.sha256(notices['manifest.json']).hexdigest(),
                                    'files': len(notice_manifest['files']), 'components': components},
                'webview2_runtime': webview, 'dotnet_runtime_versions': frameworks,
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    files['build-manifest.json'] = json.dumps(manifest).encode()
    portable = artifacts / publish.ORIGINAL_PORTABLE
    with zipfile.ZipFile(portable, 'w') as archive:
        for name, data in files.items():
            archive.writestr('story-workbench-windows-x86_64/' + name, data)
    for path in (setup, portable):
        path.with_name(path.name + '.sha256').write_text(publish.digest_file(path) + '  ' + path.name + '\n')
    native = dict.fromkeys(publish.NATIVE_FLAGS, True)
    native.update(client_version='0.2.3', ui_stack='wpf-webview2', self_contained=True, theme_captures={'light': True, 'dark': True})
    safety = dict.fromkeys(publish.SAFETY_FLAGS, True)
    safety.update(platform='win32', story_version='0.6.24', existing_book_files_recycled=False)
    write(artifacts / 'windows-native-smoke.json', json.dumps(native).encode())
    write(artifacts / 'windows-runtime-safety.json', json.dumps(safety).encode())
    lifecycle = dict.fromkeys(publish.LIFECYCLE_FLAGS, True)
    lifecycle['scope'] = 'isolated-installed-windows-lifecycle'
    write(artifacts / 'windows-installation-lifecycle.json', json.dumps(lifecycle).encode())
    write(artifacts / 'windows-previous-upgrade.json', json.dumps({'ok': True, 'previous_version': '0.2.2',
        'installed_version': '0.2.3', 'old_host_preserved': True}).encode())
    write(artifacts / 'windows-csharp-tests.json', json.dumps(
        {'ok': True, 'total': 39, 'passed': 39, 'skipped': 0, 'failed': []}).encode())
    installed = area / 'installed'
    for name, data in files.items():
        write(installed / name, data)
    write(installed / 'unins000.exe', b'allowed installer extra')
    return source, artifacts, installed


class FakeAPI:
    def __init__(self, assets=None):
        self.release = {'id': 42, 'draft': True, 'tag_name': 'windows-v0.2.3', 'target_commitish': SHA}
        self.assets = list(assets or [])
        self.calls = []
        self.next_id = 100
        self.corrupt_digest = False
        self.publish_after_first_check = False
        self.checks = 0

    def request(self, method, path, *, upload=None):
        self.calls.append((method, path))
        if method == 'GET' and path.endswith('/releases/42'):
            self.checks += 1
            result = dict(self.release)
            if self.publish_after_first_check and self.checks > 1:
                result['draft'] = False
            return result
        if method == 'GET' and '/assets?per_page=' in path:
            return self.assets
        if method == 'DELETE':
            return None
        if method == 'POST':
            self.next_id += 1
            row = {'id': self.next_id, 'name': upload.name, 'size': upload.stat().st_size,
                   'digest': 'sha256:' + ('0' * 64 if self.corrupt_digest else publish.digest_file(upload))}
            self.assets.append(row)
            return row
        if method == 'GET' and '/releases/assets/' in path:
            return next(row for row in self.assets if row['id'] == int(path.rsplit('/', 1)[1]))
        raise AssertionError('Unexpected API request')

    def writes(self):
        return [row for row in self.calls if row[0] in ('POST', 'DELETE')]


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source, self.area, self.installed = fixture(Path(self.temp.name))
        signature = json.loads((self.installed / 'build-manifest.json').read_text())['webview2_runtime']['signature']
        patcher = patch.object(publish, 'verify_microsoft_signature', return_value=signature)
        patcher.start(); self.addCleanup(patcher.stop)

    def verified(self):
        return publish.verify_artifacts(self.area, self.source, SHA, self.installed)

    def rewrite_payload(self, edit):
        portable = self.area / publish.ORIGINAL_PORTABLE
        prefix = 'story-workbench-windows-x86_64/'
        with zipfile.ZipFile(portable) as archive:
            files = {name[len(prefix):]: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(files.pop('build-manifest.json'))
        edit(files, manifest)
        manifest['files'] = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        files['build-manifest.json'] = json.dumps(manifest).encode()
        with zipfile.ZipFile(portable, 'w') as archive:
            for name, data in files.items():
                archive.writestr(prefix + name, data)
                write(self.installed / name, data)
        portable.with_name(portable.name + '.sha256').write_text(
            publish.digest_file(portable) + '  ' + portable.name + '\n')

    def test_forbidden_payload_is_rejected_even_with_consistent_manifests_and_checksums(self):
        self.rewrite_payload(lambda files, manifest: files.update({'Qt6Core.dll': b'forbidden fixture'}))
        with self.assertRaisesRegex(RuntimeError, 'Forbidden technology'):
            self.verified()

    def test_untracked_installed_file_is_rejected(self):
        write(self.installed / 'unregistered.dll', b'extra payload')
        with self.assertRaisesRegex(publish.VerificationError, 'untracked payload'):
            self.verified()

    def test_compiled_source_receipt_must_match_source_checkout(self):
        self.rewrite_payload(lambda files, manifest: manifest.update(client_source_sha256={}))
        with self.assertRaisesRegex(publish.VerificationError, 'Compiled client source'):
            self.verified()

    def test_offline_runtime_cannot_be_substituted_with_an_online_bootstrapper(self):
        def change(files, manifest):
            manifest['webview2_runtime']['resolved_url'] = 'https://msedge.sf.dl.delivery.mp.microsoft.com/MicrosoftEdgeWebview2Setup.exe'
        self.rewrite_payload(change)
        with self.assertRaisesRegex(publish.VerificationError, 'runtime provenance'):
            self.verified()

    def test_framework_dependent_host_is_rejected_even_when_metadata_claims_self_contained(self):
        def change(files, manifest):
            files['StoryWorkbench.runtimeconfig.json'] = json.dumps({'runtimeOptions': {
                'framework': {'name': 'Microsoft.NETCore.App', 'version': '10.0.1'}}}).encode()
        self.rewrite_payload(change)
        with self.assertRaisesRegex(RuntimeError, 'external or unsupported'):
            self.verified()

    def test_windows_rechecks_runtime_signature_instead_of_trusting_manifest(self):
        with patch.object(publish.sys, 'platform', 'win32'), \
                patch.object(publish, 'verify_microsoft_signature', return_value={'status': 'Invalid'}), \
                self.assertRaisesRegex(publish.VerificationError, 'signature differs'):
            self.verified()

    def test_contract_report_must_cover_all_groups_without_skips(self):
        path = self.area / 'windows-csharp-tests.json'
        value = json.loads(path.read_text())
        value['passed'], value['skipped'] = 28, 1
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(publish.VerificationError, 'C# contract'):
            self.verified()

    def test_verifies_full_manifest_source_notices_and_installed_tree(self):
        assets, report = self.verified()
        self.assertEqual(set(assets), set(publish.ASSET_NAMES))
        self.assertEqual(len(report['runtime_sha256']), 20)
        self.assertEqual(report['ui_stack'], 'wpf-webview2')
        self.assertTrue(report['self_contained'])
        self.assertEqual(report['runtime_notice_files_verified'], 7)
        self.assertEqual(report['manifest_files_verified'], report['installed_manifest_files_verified'])
        self.assertTrue(report['installed_tree_verified'])
        self.assertEqual(assets[publish.PORTABLE].read_bytes(), (self.area / publish.ORIGINAL_PORTABLE).read_bytes())
        self.assertEqual(assets[publish.PORTABLE + '.sha256'].read_text().split()[1], publish.PORTABLE)

    def test_rejects_wrong_binary_sidecar_without_publishing(self):
        (self.area / publish.SETUP).write_bytes(b'changed')
        with self.assertRaisesRegex(publish.VerificationError, 'checksum'):
            self.verified()

    def test_previous_release_source_versions_are_rejected(self):
        for path, old in ((self.source / 'skills/story-skill/scripts/story.py', b'VERSION = "0.6.20"\n'),
                          (self.source / publish.PROJECT, (self.source / publish.PROJECT).read_bytes().replace(b'0.2.3', b'0.1.10'))):
            original = path.read_bytes()
            path.write_bytes(old)
            with self.subTest(path=path), self.assertRaisesRegex(publish.VerificationError, 'Source versions'):
                self.verified()
            path.write_bytes(original)

    def test_rejects_wrong_source_runtime_installed_and_notice_bytes(self):
        for path in (self.source / 'skills/story-skill/scripts/runtime0.py',
                     self.installed / 'Scripts/runtime0.py',
                     self.installed / 'ThirdPartyLicenses/Python/LICENSE.txt'):
            original = path.read_bytes()
            path.write_bytes(b'changed')
            with self.subTest(path=path), self.assertRaises(publish.VerificationError):
                self.verified()
            path.write_bytes(original)

    def test_rejects_failed_native_and_unsafe_recycle_reports(self):
        for filename, key, value in (('windows-native-smoke.json', 'themes_verified', False),
                                      ('windows-runtime-safety.json', 'existing_book_files_recycled', True)):
            path = self.area / filename
            original = path.read_bytes()
            report = json.loads(original)
            report[key] = value
            path.write_text(json.dumps(report))
            with self.subTest(filename=filename), self.assertRaises(publish.VerificationError):
                self.verified()
            path.write_bytes(original)

    def test_rejects_unsafe_duplicate_and_symlink_zip_entries(self):
        for names in (['../escape'], ['same', 'SAME']):
            path = self.area / 'unsafe.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                for name in names:
                    archive.writestr(name, b'bad')
            with zipfile.ZipFile(path) as archive, self.assertRaises(publish.VerificationError):
                publish.safe_archive(archive)
        with zipfile.ZipFile(path, 'w') as archive:
            entry = zipfile.ZipInfo('link')
            entry.external_attr = 0o120777 << 16
            archive.writestr(entry, b'target')
        with zipfile.ZipFile(path) as archive, self.assertRaises(publish.VerificationError):
            publish.safe_archive(archive)

    def test_published_wrong_target_or_wrong_tag_never_mutates(self):
        assets, report = self.verified()
        for key, value in (('draft', False), ('target_commitish', 'b' * 40), ('tag_name', 'v0.6.20')):
            api = FakeAPI()
            api.release[key] = value
            with self.subTest(key=key), self.assertRaises(publish.VerificationError):
                publish.publish(api, 42, SHA, assets, report)
            self.assertEqual(api.writes(), [])

    def test_only_whitelisted_collision_is_deleted_and_server_digest_is_read_back(self):
        assets, report = self.verified()
        api = FakeAPI([{'id': 5, 'name': publish.SETUP, 'size': 0, 'digest': 'sha256:bad'},
                       {'id': 998, 'name': 'story-workbench-0.1.10-windows-x86_64-setup.exe', 'size': 0},
                       {'id': 999, 'name': 'unrelated-macos.zip', 'size': 0}])
        result = publish.publish(api, 42, SHA, assets, report)
        self.assertTrue(result['ok'])
        self.assertEqual([path for method, path in api.writes() if method == 'DELETE'],
                         ['/repos/NingCui29/story-skill/releases/assets/5'])
        self.assertEqual(len([row for row in api.writes() if row[0] == 'POST']), 4)
        self.assertGreaterEqual(api.checks, 7)

    def test_matching_server_digests_skip_all_uploads(self):
        assets, report = self.verified()
        existing = [{'id': i + 1, 'name': name, 'size': info['bytes'], 'digest': 'sha256:' + info['sha256']}
                    for i, (name, info) in enumerate(report['assets'].items())]
        api = FakeAPI(existing)
        result = publish.publish(api, 42, SHA, assets, report)
        self.assertEqual(api.writes(), [])
        self.assertTrue(all(row['action'] == 'unchanged' for row in result['assets']))

    def test_publish_race_stops_before_deleting_existing_asset(self):
        assets, report = self.verified()
        api = FakeAPI([{'id': 5, 'name': publish.SETUP, 'size': 0}])
        api.publish_after_first_check = True
        with self.assertRaises(publish.VerificationError):
            publish.publish(api, 42, SHA, assets, report)
        self.assertEqual(api.writes(), [])

    def test_wrong_server_digest_stops_after_first_upload(self):
        assets, report = self.verified()
        api = FakeAPI()
        api.corrupt_digest = True
        with self.assertRaisesRegex(publish.VerificationError, 'server digest'):
            publish.publish(api, 42, SHA, assets, report)
        self.assertEqual(len(api.writes()), 1)

    def test_changed_local_asset_and_non_windows_asset_are_rejected(self):
        assets, report = self.verified()
        assets['macos.zip'] = self.area / 'other'
        api = FakeAPI()
        with self.assertRaises(publish.VerificationError):
            publish.publish(api, 42, SHA, assets, report)
        self.assertEqual(api.calls, [])
        del assets['macos.zip']
        assets[publish.SETUP].write_bytes(b'changed after validation')
        with self.assertRaises(publish.VerificationError):
            publish.publish(api, 42, SHA, assets, report)
        self.assertEqual(api.writes(), [])

    def test_zero_release_id_and_wrong_repository_never_construct_api_client(self):
        for repository, release in ((publish.REPOSITORY, '0'), ('fork/story-skill', '42')):
            def run(command, **kwargs):
                if command[1:3] == ['rev-parse', 'HEAD']:
                    return SimpleNamespace(stdout=SHA)
                if command[1:3] == ['ls-files', '-z']:
                    return SimpleNamespace(stdout=b'')
                raise AssertionError('Unexpected subprocess')
            errors = io.StringIO()
            with patch.dict(publish.os.environ, {'GITHUB_REPOSITORY': repository, 'GITHUB_SHA': SHA}), \
                 patch.object(publish.subprocess, 'run', side_effect=run), \
                 patch.object(publish, 'verify_artifacts', return_value=self.verified()), \
                 patch.object(publish, 'GitHub') as api, contextlib.redirect_stderr(errors):
                self.assertEqual(publish.main(['--artifact-root', str(self.area), '--receipt',
                                 str(self.area / 'receipt.json'), '--release-id', release]), 1)
                api.assert_not_called()
            expected = 'Release ID zero disables uploads' if release == '0' else 'Repository is not authorized'
            self.assertEqual(json.loads(errors.getvalue())['error'], expected)

    def test_verify_only_allows_fork_without_constructing_api(self):
        def run(command, **kwargs):
            if command[1:3] == ['rev-parse', 'HEAD']:
                return SimpleNamespace(stdout=SHA)
            if command[1:3] == ['ls-files', '-z']:
                return SimpleNamespace(stdout=b'')
            raise AssertionError('Unexpected subprocess')
        receipt = self.area / 'fork-verification.json'
        with patch.dict(publish.os.environ, {'GITHUB_REPOSITORY': 'fork/story-skill', 'GITHUB_SHA': SHA}), \
             patch.object(publish.subprocess, 'run', side_effect=run), \
             patch.object(publish, 'verify_artifacts', return_value=self.verified()), \
             patch.object(publish, 'GitHub') as api, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(publish.main(['--artifact-root', str(self.area), '--receipt',
                             str(receipt), '--verify-only']), 0)
            api.assert_not_called()
        self.assertEqual(json.loads(receipt.read_text())['repository'], 'fork/story-skill')

    def test_manifest_detects_changed_zip_member_even_with_new_outer_sidecar(self):
        path = self.area / publish.ORIGINAL_PORTABLE
        with zipfile.ZipFile(path) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        members['story-workbench-windows-x86_64/Scripts/runtime0.py'] = b'changed'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, data in members.items():
                archive.writestr(name, data)
        path.with_name(path.name + '.sha256').write_text(publish.digest_file(path) + '  ' + path.name)
        with self.assertRaisesRegex(publish.VerificationError, 'manifest file checksum'):
            self.verified()

    def test_renamed_existing_asset_is_not_deleted(self):
        assets, report = self.verified()
        api = FakeAPI([{'id': 5, 'name': publish.SETUP, 'size': 0}])
        original = api.request
        def request(method, path, **kwargs):
            result = original(method, path, **kwargs)
            if method == 'GET' and path.endswith('/assets/5'):
                return {**result, 'name': 'unrelated-macos.zip'}
            return result
        api.request = request
        with self.assertRaisesRegex(publish.VerificationError, 'identity or name'):
            publish.publish(api, 42, SHA, assets, report)
        self.assertEqual(api.writes(), [])

    def test_upload_uses_only_github_upload_host_and_bounded_file_stream(self):
        path = self.area / publish.SETUP
        body = b'{"id": 10}'
        response = unittest.mock.Mock(status=201)
        response.read.return_value = body
        connection = unittest.mock.Mock()
        connection.getresponse.return_value = response
        with patch.object(publish.http.client, 'HTTPSConnection', return_value=connection) as connect:
            self.assertEqual(publish.GitHub('private-token').request(
                'POST', '/repos/NingCui29/story-skill/releases/42/assets?name=' + publish.SETUP,
                upload=path), {'id': 10})
            connect.assert_called_once_with('uploads.github.com', timeout=90)
            self.assertEqual(b''.join(call.args[0] for call in connection.send.call_args_list), path.read_bytes())
            connection.close.assert_called_once()

    def test_redirect_is_refused_without_following_or_disclosing_location(self):
        response = unittest.mock.Mock(status=302)
        connection = unittest.mock.Mock()
        connection.getresponse.return_value = response
        with patch.object(publish.http.client, 'HTTPSConnection', return_value=connection) as connect:
            with self.assertRaisesRegex(publish.VerificationError, '^GitHub request failed with HTTP 302$'):
                publish.GitHub('private-token').request('GET', '/repos/NingCui29/story-skill/releases/42')
            connect.assert_called_once_with('api.github.com', timeout=90)
            connection.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
