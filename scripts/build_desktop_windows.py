#!/usr/bin/env python3
"""Build the self-contained WPF/WebView2 Windows x64 distribution on Windows."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import xml.etree.ElementTree as ET

from build_desktop_macos import REPO, RUNTIME_FILES
from check_forbidden_technologies import assert_clean_tree

PROJECT = Path('desktop/windows/StoryWorkbench/StoryWorkbench.csproj')
UI_STACK = 'wpf-webview2'
WEBVIEW_INSTALLER = 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
WEBVIEW_DOWNLOAD = 'https://go.microsoft.com/fwlink/?linkid=2124701'
NOTICE_PACKAGES = ('microsoft.netcore.app.runtime.win-x64',
                   'microsoft.windowsdesktop.app.runtime.win-x64', 'microsoft.web.webview2')


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def project_configuration(source=REPO):
    root = ET.parse(Path(source) / PROJECT).getroot()
    def value(name):
        values = [node.text for node in root.iter(name)]
        if len(values) != 1 or not values[0]:
            raise RuntimeError('Ambiguous desktop project setting: ' + name)
        return values[0].strip()
    version, target = value('Version'), value('TargetFramework')
    if not re.fullmatch(r'\d+\.\d+\.\d+', version) or target != 'net10.0-windows':
        raise RuntimeError('Unexpected desktop version or target framework.')
    packages = {node.attrib.get('Include'): node.attrib.get('Version') for node in root.iter('PackageReference')}
    if (set(packages) != {'Microsoft.Web.WebView2'} or
            not re.fullmatch(r'\d+\.\d+\.\d+\.\d+', packages['Microsoft.Web.WebView2'] or '')):
        raise RuntimeError('Desktop dependencies must have an exact stable WebView2 version.')
    return {'client_version': version, 'target_framework': target, 'packages': packages,
            'runtime_identifier': 'win-x64', 'self_contained': True, 'ui_stack': UI_STACK}


def desktop_client_version():
    return project_configuration()['client_version']


def client_source_hashes(source=REPO):
    directory = Path(source) / PROJECT.parent
    return {path.relative_to(source).as_posix(): digest_file(path)
            for path in sorted(directory.rglob('*')) if path.is_file()
            and not {'bin', 'obj'} & set(path.relative_to(directory).parts)
            and path.suffix.lower() in ('.cs', '.xaml', '.csproj', '.manifest', '.json')}


def verify_book_runtime(python, script, book):
    packets = []
    for arguments in (['init', '--book', str(book), '--title', '桌面验证', '--kind', 'short'],
                      ['status', '--book', str(book)]):
        checked = subprocess.run([str(python), '-I', '-B', str(script), *arguments],
                                 capture_output=True, check=True, timeout=30)
        packets.append(json.loads(checked.stdout))
    created, status = packets
    if (not created.get('id') or created['id'] != status.get('id')
            or status.get('title') != '桌面验证' or status.get('kind') != 'short'
            or status.get('revision') != 0 or status.get('next_chapter') != 1):
        raise RuntimeError('Bundled initialization/status check failed.')


def pe_machine(path):
    with Path(path).open('rb') as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b'MZ':
            raise RuntimeError('Expected a PE executable.')
        offset = struct.unpack_from('<I', header, 60)[0]
        if not 64 <= offset <= 1024 * 1024:
            raise RuntimeError('Invalid PE header offset.')
        stream.seek(offset)
        header = stream.read(6)
        if len(header) != 6 or header[:4] != b'PE\0\0':
            raise RuntimeError('Invalid PE executable signature.')
        return struct.unpack_from('<H', header, 4)[0]


def verify_self_contained_host(app):
    app = Path(app)
    required = ('coreclr.dll', 'hostfxr.dll', 'hostpolicy.dll', 'PresentationFramework.dll',
                'Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.Wpf.dll')
    if (pe_machine(app / 'StoryWorkbench.exe') != 0x8664 or
            any(not (app / name).is_file() for name in required) or
            not any((app / name).is_file() for name in ('WebView2Loader.dll',
                    'runtimes/win-x64/native/WebView2Loader.dll'))):
        raise RuntimeError('Desktop publication is not a complete self-contained Windows x64 host.')
    options = json.loads((app / 'StoryWorkbench.runtimeconfig.json').read_text(encoding='utf-8'))['runtimeOptions']
    frameworks = {item['name']: item['version'] for item in options.get('includedFrameworks', [])}
    if (set(frameworks) != {'Microsoft.NETCore.App', 'Microsoft.WindowsDesktop.App'} or
            any(not re.fullmatch(r'10\.0\.\d+', version) for version in frameworks.values()) or
            'framework' in options or 'frameworks' in options):
        raise RuntimeError('Desktop publication requires an external or unsupported .NET runtime.')
    return frameworks


class SignatureVerificationError(RuntimeError):
    def __init__(self, message, diagnostic):
        self.diagnostic = diagnostic
        super().__init__(message + ': ' + json.dumps(diagnostic, ensure_ascii=False))


def bounded_diagnostic(value):
    if isinstance(value, bytes):
        value = value.decode('utf-8-sig', errors='replace')
    return str(value or '')[:4096]


def verify_microsoft_signature(path):
    path = str(Path(path).resolve()).replace("'", "''")
    script = """$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); $p='%s';
$s=Get-AuthenticodeSignature -LiteralPath $p;
$v=(Get-Item -LiteralPath $p).VersionInfo;
@{status=[string]$s.Status;status_message=[string]$s.StatusMessage;subject=[string]$s.SignerCertificate.Subject;thumbprint=[string]$s.SignerCertificate.Thumbprint;file_version=[string]$v.FileVersion;product_name=[string]$v.ProductName} | ConvertTo-Json -Compress
""" % path
    command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand',
               base64.b64encode(script.encode('utf-16le')).decode('ascii')]
    try:
        result = subprocess.run(command, capture_output=True, check=False, timeout=90)
    except subprocess.TimeoutExpired as error:
        raise SignatureVerificationError('Microsoft signature inspection timed out', {
            'timed_out': True, 'stdout': bounded_diagnostic(error.stdout),
            'stderr': bounded_diagnostic(error.stderr)}) from None
    diagnostic = {'returncode': result.returncode, 'stdout': bounded_diagnostic(result.stdout),
                  'stderr': bounded_diagnostic(result.stderr)}
    if result.returncode != 0:
        raise SignatureVerificationError('Microsoft signature inspection failed', diagnostic)
    try:
        signature = json.loads(result.stdout.decode('utf-8-sig'))
    except (ValueError, UnicodeError):
        raise SignatureVerificationError('Microsoft signature inspection returned invalid JSON', diagnostic) from None
    fields = ('status', 'status_message', 'subject', 'thumbprint', 'file_version', 'product_name')
    if not isinstance(signature, dict) or any(not isinstance(signature.get(key), str) for key in fields):
        raise SignatureVerificationError('Microsoft signature inspection returned invalid fields', diagnostic)
    diagnostic['signature'] = {key: bounded_diagnostic(signature[key]) for key in fields}
    if (signature['status'] != 'Valid' or
            not re.search(r'(^|,\s*)O=Microsoft Corporation(,|$)', signature['subject'], re.IGNORECASE)):
        raise SignatureVerificationError('Microsoft signature validation failed', diagnostic)
    return signature


def allowed_microsoft_url(url):
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and not parsed.username and not parsed.password
            and parsed.port in (None, 443) and
            (parsed.hostname == 'go.microsoft.com' or
             (parsed.hostname or '').endswith('.dl.delivery.mp.microsoft.com')))


class MicrosoftRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not allowed_microsoft_url(newurl):
            raise RuntimeError('WebView2 download redirected outside Microsoft HTTPS.')
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download_webview_runtime(app, diagnostic_path=None):
    diagnostic = {'schema': 1, 'phase': 'download', 'source_url': WEBVIEW_DOWNLOAD}
    try:
        return _download_webview_runtime(app, diagnostic)
    except Exception as error:
        diagnostic['error'] = bounded_diagnostic(str(error))
        if isinstance(error, SignatureVerificationError):
            diagnostic['signature_inspection'] = error.diagnostic
        destination = Path(app) / 'Prerequisites' / WEBVIEW_INSTALLER
        if destination.is_file():
            diagnostic.update(bytes=destination.stat().st_size, sha256=digest_file(destination))
        if diagnostic_path is not None:
            diagnostic_path = Path(diagnostic_path)
            diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
            diagnostic_path.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        raise


def _download_webview_runtime(app, diagnostic):
    destination = Path(app) / 'Prerequisites' / WEBVIEW_INSTALLER
    destination.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with build_opener(MicrosoftRedirects()).open(Request(WEBVIEW_DOWNLOAD,
            headers={'User-Agent': 'StoryWorkbench-Build/0.2'}), timeout=60) as response:
        final_url = response.geturl()
        if (not allowed_microsoft_url(final_url) or
                urlsplit(final_url).path.rsplit('/', 1)[-1].casefold() != WEBVIEW_INSTALLER.casefold()):
            raise RuntimeError('Expected the official x64 standalone runtime, not a bootstrapper.')
        diagnostic['resolved_url'] = final_url
        size = int(response.headers.get('Content-Length', '0'))
        diagnostic['expected_bytes'] = size
        if not 50 * 1024 ** 2 <= size <= 600 * 1024 ** 2:
            raise RuntimeError('Unexpected offline WebView2 installer size.')
        copied = 0
        with destination.open('xb') as output:
            for chunk in iter(lambda: response.read(1024 * 1024), b''):
                copied += len(chunk)
                if copied > size or time.monotonic() - started > 600:
                    raise RuntimeError('WebView2 download exceeded its size or time budget.')
                output.write(chunk)
        if copied != size:
            raise RuntimeError('Incomplete WebView2 runtime download.')
    # The signed standalone wrapper may be x86 even when its payload is x64.
    machine = pe_machine(destination)
    if machine not in (0x14c, 0x8664):
        raise RuntimeError('Unexpected WebView2 standalone installer architecture.')
    diagnostic.update(phase='signature', pe_machine=machine)
    signature = verify_microsoft_signature(destination)
    return {'kind': 'evergreen-standalone', 'architecture': 'x64',
            'path': destination.relative_to(app).as_posix(), 'source_url': WEBVIEW_DOWNLOAD,
            'resolved_url': final_url, 'bytes': copied, 'sha256': digest_file(destination),
            'pe_machine': machine, 'signature': signature,
            'distribution_terms': 'https://developer.microsoft.com/en-us/microsoft-edge/webview2/'}


def copy_runtime_notices(app, packages, python_prefix, configuration):
    """Retain licenses from the exact restored runtime/SDK packages and CPython."""
    destination = Path(app) / 'ThirdPartyLicenses'
    destination.mkdir()
    components = {}
    for package_name in NOTICE_PACKAGES:
        roots = [path for path in (Path(packages) / package_name).iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise RuntimeError('Ambiguous or absent runtime license package: ' + package_name)
        package = roots[0]
        if package_name == 'microsoft.web.webview2':
            if package.name != configuration['packages']['Microsoft.Web.WebView2']:
                raise RuntimeError('WebView2 license package version mismatch.')
        elif not package.name.startswith('10.0.'):
            raise RuntimeError('Expected .NET 10 runtime notices.')
        notices = [path for path in package.iterdir() if path.is_file() and
                   any(word in path.name.lower() for word in ('license', 'notice', 'copying'))]
        if not any('license' in path.name.lower() for path in notices):
            raise RuntimeError('Missing runtime license: ' + package_name)
        package_hash = package / (package_name + '.' + package.name + '.nupkg.sha512')
        if not package_hash.is_file():
            raise RuntimeError('Missing restored package integrity receipt.')
        for notice in notices:
            if notice.is_symlink():
                raise RuntimeError('Linked runtime notices are not supported.')
            target = destination / package_name / notice.name
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(notice, target)
        components[package_name] = {'version': package.name,
            'nuget_sha512': package_hash.read_text(encoding='ascii').strip(),
            'source': 'https://api.nuget.org/v3-flatcontainer/' + package_name + '/' + package.name + '/'}
    python_license = Path(python_prefix) / 'LICENSE.txt'
    if not python_license.is_file() or python_license.is_symlink():
        raise RuntimeError('Missing CPython license.')
    (destination / 'Python').mkdir()
    shutil.copy2(python_license, destination / 'Python/LICENSE.txt')
    components['Python'] = {'version': platform.python_version(), 'source': 'https://www.python.org/'}
    files = {path.relative_to(destination).as_posix(): digest_file(path)
             for path in sorted(destination.rglob('*')) if path.is_file()}
    manifest = {'schema': 2, 'ui_stack': UI_STACK, 'components': components, 'files': files}
    raw = json.dumps(manifest, ensure_ascii=False, indent=2) + '\n'
    (destination / 'manifest.json').write_text(raw, encoding='utf-8')
    return {'manifest_sha256': digest_file(destination / 'manifest.json'), 'files': len(files),
            'components': components}


def build(output):
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('Run this builder with Windows x64 Python and .NET 10 SDK.')
    output = Path(output).resolve()
    configuration = project_configuration()
    archive = output.with_suffix('.zip')
    if output.exists() or archive.exists():
        raise RuntimeError('Choose a new output directory; existing builds are preserved.')
    prefix = Path(sys.base_prefix)
    if not (prefix / 'python.exe').is_file() or not (prefix / 'Lib/encodings').is_dir():
        raise RuntimeError('A full CPython installation is required for building.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='story-windows-build-') as raw:
        stage = Path(raw)
        app, packages = stage / 'app', stage / 'packages'
        project = REPO / PROJECT
        subprocess.run(['dotnet', 'restore', str(project), '-r', 'win-x64', '--packages', str(packages),
                        '-p:SelfContained=true'], check=True)
        subprocess.run(['dotnet', 'publish', str(project), '-c', 'Release', '-r', 'win-x64',
                        '--self-contained', 'true', '--no-restore', '-o', str(app),
                        '-p:PublishSingleFile=false', '-p:PublishTrimmed=false'], check=True)
        runtime_versions = verify_self_contained_host(app)
        runtime = app / 'runtime'
        runtime.mkdir()
        for file in prefix.iterdir():
            if file.is_file() and file.suffix.lower() in ('.exe', '.dll', '.txt'):
                shutil.copy2(file, runtime / file.name)
        for name in ('Lib', 'DLLs'):
            shutil.copytree(prefix / name, runtime / name,
                            ignore=shutil.ignore_patterns('site-packages', '__pycache__',
                                                         '*.pyc', 'test', 'tests', 'idlelib', 'tkinter'))
        scripts = app / 'Scripts'
        scripts.mkdir()
        for name in RUNTIME_FILES:
            shutil.copy2(REPO / 'skills/story-skill/scripts' / name, scripts / name)
        for source, target in (('desktop/macos/backend_runner.py', 'backend_runner.py'),
                               ('LICENSE', 'StorySkill-LICENSE.txt'), ('desktop/windows/README.md', 'README.md')):
            shutil.copy2(REPO / source, app / target)
        notice_report = copy_runtime_notices(app, packages, prefix, configuration)
        webview = download_webview_runtime(app, output.parent / 'windows-build-diagnostic.json')
        result = subprocess.run([str(runtime / 'python.exe'), '-I', '-B', str(scripts / 'story.py'), '--version'],
                                capture_output=True, text=True, check=True, timeout=30)
        subprocess.run([str(runtime / 'python.exe'), '-I', '-B', '-c',
                        'import sqlite3, ssl, ctypes, http.server, multiprocessing'], check=True, timeout=30)
        verify_book_runtime(runtime / 'python.exe', scripts / 'story.py', stage / '隔离验证 空格目录')
        assert_clean_tree(app)
        manifest = {path.relative_to(app).as_posix(): digest_file(path)
                    for path in sorted(app.rglob('*')) if path.is_file()}
        (app / 'build-manifest.json').write_text(json.dumps({
            'schema': 2, 'platform': 'windows-x86_64', **configuration,
            'dotnet_runtime_versions': runtime_versions,
            'story_version': result.stdout.strip(), 'client_source_sha256': client_source_hashes(),
            'files': manifest, 'runtime_notices': notice_report, 'webview2_runtime': webview,
            'forbidden_technologies_absent': True, 'ui_verified': False,
        }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        shutil.copytree(app, output)
    shutil.make_archive(str(archive.with_suffix('')), 'zip', output.parent, output.name)
    digest = digest_file(archive)
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    return {'output': str(output), 'archive': str(archive), 'sha256': digest,
            **configuration, 'runtime_notices': notice_report, 'webview2_runtime': webview, 'ui_verified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    print(json.dumps(build(parser.parse_args().output), ensure_ascii=False, indent=2))
