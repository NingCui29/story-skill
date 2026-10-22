#!/usr/bin/env python3
"""Verify Windows CI payloads and optionally upload four assets to one existing draft."""
from __future__ import annotations

import argparse
import ast
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
from urllib.parse import quote
import zipfile

REPOSITORY = 'NingCui29/story-skill'
STORY_VERSION = '0.6.22'
CLIENT_VERSION = '0.1.12'
TAG = 'v' + STORY_VERSION
REPO = Path(__file__).resolve().parents[1]
SETUP = f'story-workbench-{CLIENT_VERSION}-windows-x86_64-setup.exe'
PORTABLE = f'story-workbench-{CLIENT_VERSION}-windows-x86_64.zip'
ORIGINAL_PORTABLE = 'story-workbench-windows-x86_64.zip'
ASSET_NAMES = (SETUP, SETUP + '.sha256', PORTABLE, PORTABLE + '.sha256')
NATIVE_FLAGS = ('ok', 'native_window', 'shelf_rendered', 'captured', 'themes_verified',
                'update_check_available', 'update_progress_verified')
SAFETY_FLAGS = ('ok', 'isolated_payload', 'created_and_registered', 'backup_verified',
                'restore_identity_preserved', 'candidate_and_binary_preserved',
                'sqlite_integrity_verified', 'missing_trash_path_refused',
                'native_trash_bridge_checked')


class VerificationError(ValueError):
    """A deliberately sanitized validation failure."""


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def digest_file(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def literal(source, name):
    values = [node.value for node in ast.parse(source).body
              if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and
              t.id == name for t in node.targets)]
    require(len(values) == 1, 'Source version declaration is ambiguous')
    return ast.literal_eval(values[0])


def one(root, name):
    paths = list(root.rglob(name))
    require(len(paths) == 1 and paths[0].is_file() and not paths[0].is_symlink(),
            'Required artifact is absent, linked, or ambiguous: ' + name)
    require(all(not p.is_symlink() for p in paths[0].parents if p != root.parent),
            'Artifact parent is linked')
    return paths[0]


def verify_sidecar(path):
    sidecar = path.with_name(path.name + '.sha256')
    require(sidecar.is_file() and not sidecar.is_symlink(), 'Checksum sidecar is absent or linked')
    fields = sidecar.read_text(encoding='utf-8-sig').split()
    require(len(fields) == 2 and fields[1] == path.name and
            re.fullmatch('[0-9a-f]{64}', fields[0]) is not None and
            fields[0] == digest_file(path), 'Binary checksum sidecar mismatch')


def safe_archive(archive):
    names = set()
    total = 0
    for entry in archive.infolist():
        name = entry.filename
        normalized = name[:-1] if name.endswith('/') else name
        parts = PurePosixPath(name)
        require(name and '\\' not in name and '\x00' not in name and
                not parts.is_absolute() and parts.as_posix() == normalized and
                all(p not in ('.', '..', '') and ':' not in p and p.rstrip(' .') == p
                    for p in parts.parts), 'Unsafe portable ZIP path')
        require(normalized.casefold() not in names, 'Duplicate portable ZIP path')
        names.add(normalized.casefold())
        require(stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG, stat.S_IFDIR)
                and not entry.flag_bits & 1, 'Linked, special, or encrypted ZIP entry')
        total += entry.file_size
        require(entry.file_size <= 1024 ** 3 and total <= 6 * 1024 ** 3,
                'Portable ZIP exceeds validation limits')
    require(archive.testzip() is None, 'Portable ZIP CRC mismatch')


def verify_artifacts(root, source=REPO, source_sha=None, installed_root=None):
    root = Path(root).resolve()
    require(literal((source / 'skills/story-skill/scripts/story.py').read_text(encoding='utf-8'), 'VERSION')
            == STORY_VERSION and literal((source / 'desktop/windows/workbench_updates.py').read_text(encoding='utf-8'),
                                        'CLIENT_VERSION') == CLIENT_VERSION,
            'Source versions do not match this release')
    setup = one(root, SETUP)
    portable = one(root, ORIGINAL_PORTABLE)
    verify_sidecar(setup)
    verify_sidecar(portable)
    native = json.loads(one(root, 'windows-native-smoke.json').read_text(encoding='utf-8-sig'))
    safety = json.loads(one(root, 'windows-runtime-safety.json').read_text(encoding='utf-8-sig'))
    require(all(native.get(key) is True for key in NATIVE_FLAGS) and
            native.get('client_version') == CLIENT_VERSION and
            native.get('theme_captures') == {'light': True, 'dark': True},
            'Native Windows acceptance report is incomplete')
    require(all(safety.get(key) is True for key in SAFETY_FLAGS) and
            safety.get('platform') == 'win32' and safety.get('story_version') == STORY_VERSION
            and safety.get('existing_book_files_recycled') is False,
            'Installed runtime safety report is incomplete')
    with zipfile.ZipFile(portable) as archive:
        safe_archive(archive)
        regular = {e.filename for e in archive.infolist() if not e.is_dir()}
        manifest_name = 'story-workbench-windows-x86_64/build-manifest.json'
        prefix = 'story-workbench-windows-x86_64/'
        raw_manifest = archive.read(manifest_name)
        manifest = json.loads(raw_manifest)
        require(manifest.get('platform') == 'windows-x86_64' and
                manifest.get('client_version') == CLIENT_VERSION and
                manifest.get('story_version') == STORY_VERSION, 'Portable build version mismatch')
        files = manifest.get('files')
        require(isinstance(files, dict) and regular == {prefix + name for name in files} |
                {manifest_name}, 'Portable manifest inventory mismatch')
        for name, expected in files.items():
            require(isinstance(expected, str) and re.fullmatch('[0-9a-f]{64}', expected),
                    'Invalid portable manifest digest')
            h = hashlib.sha256()
            with archive.open(prefix + name) as stream:
                for data in iter(lambda: stream.read(1024 * 1024), b''):
                    h.update(data)
            require(h.hexdigest() == expected, 'Portable manifest file checksum mismatch')
        runtime = literal((source / 'scripts/build_desktop_macos.py').read_text(encoding='utf-8'), 'RUNTIME_FILES')
        require(len(runtime) == 20, 'Unexpected runtime module inventory')
        runtime_hashes = {}
        for name in runtime:
            raw = archive.read(prefix + 'Scripts/' + name)
            require(raw == (source / 'skills/story-skill/scripts' / name).read_bytes(),
                    'Runtime source differs from uploaded build')
            runtime_hashes[name] = hashlib.sha256(raw).hexdigest()
        mapped_hashes = {}
        for member, original in (('backend_runner.py', 'desktop/macos/backend_runner.py'),
                                 ('StorySkill-LICENSE.txt', 'LICENSE'),
                                 ('README.md', 'desktop/windows/README.md')):
            raw = archive.read(prefix + member)
            wanted = (source / original).read_bytes()
            require(raw == wanted, 'Packaged desktop source differs from runner checkout')
            mapped_hashes[member] = hashlib.sha256(raw).hexdigest()
        notice_prefix = prefix + 'ThirdPartyLicenses/Qt/'
        notice_source = source / 'desktop/windows/licenses'
        raw_notice = (notice_source / 'manifest.json').read_bytes()
        require(archive.read(notice_prefix + 'manifest.json') == raw_notice,
                'Supplementary notice manifest differs from source')
        notices = json.loads(raw_notice)
        require(notices.get('schema') == 1 and notices.get('pyside_version') == '6.10.3'
                and notices.get('qt_version') == '6.10.3' and
                {'LGPL-3.0-only.txt', 'GPL-2.0-only.txt', 'GPL-3.0-only.txt',
                 'README.md', 'QtWebEngine/index.html'} <= set(notices.get('files', {})),
                'Supplementary notice inventory is incomplete')
        require({name[len(notice_prefix):] for name in regular if name.startswith(notice_prefix)}
                == set(notices['files']) | {'manifest.json'}, 'Unexpected supplementary notice file')
        for name, expected in notices['files'].items():
            require(isinstance(expected, str) and re.fullmatch('[0-9a-f]{64}', expected),
                    'Invalid supplementary notice digest')
            raw = archive.read(notice_prefix + name)
            require(hashlib.sha256(raw).hexdigest() == expected and
                    raw == (notice_source / name).read_bytes(), 'Supplementary notice checksum mismatch')
        component_pages = [name for name in notices['files'] if name.startswith('QtWebEngine/')
                           and name.endswith('.html') and name != 'QtWebEngine/index.html']
        require(len(component_pages) == 124, 'Expected 124 offline WebEngine component notices')
        installed_verified = 0
        if installed_root is not None:
            installed_root = Path(installed_root).resolve()
            require((installed_root / 'build-manifest.json').read_bytes() == raw_manifest,
                    'Installed build manifest differs from portable ZIP')
            for name, expected in files.items():
                installed = installed_root / name
                require(installed.is_file() and not installed.is_symlink() and
                        installed.resolve().is_relative_to(installed_root) and
                        digest_file(installed) == expected, 'Installed manifest file checksum mismatch')
                installed_verified += 1
        (root / 'windows-build-manifest.json').write_bytes(raw_manifest)
    versioned = root / PORTABLE
    if versioned.exists():
        require(versioned.is_file() and not versioned.is_symlink() and
                digest_file(versioned) == digest_file(portable), 'Versioned portable already differs')
    else:
        shutil.copyfile(portable, versioned)
    checksum = digest_file(versioned)
    sidecar = versioned.with_name(versioned.name + '.sha256')
    require(not sidecar.is_symlink(), 'Versioned checksum path is linked')
    sidecar.write_text(checksum + '  ' + versioned.name + '\n', encoding='ascii')
    assets = {name: setup if name == SETUP else setup.with_name(name) if name == SETUP + '.sha256'
              else versioned if name == PORTABLE else sidecar for name in ASSET_NAMES}
    asset_info = {name: {'bytes': path.stat().st_size, 'sha256': digest_file(path)}
                  for name, path in assets.items()}
    report = {'ok': True, 'repository': REPOSITORY, 'source_commit': source_sha,
              'story_version': STORY_VERSION, 'client_version': CLIENT_VERSION,
              'assets': asset_info, 'portable_crc_and_safe_paths': True,
              'manifest_sha256': hashlib.sha256(raw_manifest).hexdigest(),
              'manifest_files_verified': len(files), 'runtime_sha256': runtime_hashes,
              'installed_manifest_files_verified': installed_verified,
              'installed_tree_verified': installed_root is not None,
              'offline_webengine_component_pages': len(component_pages),
              'mapped_source_sha256': mapped_hashes,
              'supplementary_notice_manifest_sha256': hashlib.sha256(raw_notice).hexdigest(),
              'supplementary_notice_files_verified': len(notices['files']),
              'supplementary_notice_sha256': notices['files'],
              'native_report': native, 'installed_payload_safety_report': safety}
    return assets, report


class GitHub:
    """HTTPS only, fixed GitHub hosts, no redirects, sanitized failures."""
    def __init__(self, token):
        require(bool(token), 'GITHUB_TOKEN is required')
        self.token = token

    def request(self, method, path, *, upload=None):
        host = 'uploads.github.com' if upload is not None else 'api.github.com'
        require(path.startswith('/repos/' + REPOSITORY + '/releases/'), 'API path outside release scope')
        headers = {'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2026-03-10', 'User-Agent': 'story-skill-draft-assets'}
        connection = http.client.HTTPSConnection(host, timeout=90)
        try:
            if upload is None:
                connection.request(method, path, headers=headers)
            else:
                require(method == 'POST', 'Invalid asset upload method')
                expected = digest_file(upload)
                with upload.open('rb') as stream:
                    size = os.fstat(stream.fileno()).st_size
                    headers.update({'Content-Type': 'application/octet-stream', 'Content-Length': str(size)})
                    connection.putrequest(method, path)
                    for key, value in headers.items():
                        connection.putheader(key, value)
                    connection.endheaders()
                    h = hashlib.sha256()
                    for data in iter(lambda: stream.read(1024 * 1024), b''):
                        h.update(data)
                        connection.send(data)
                    require(h.hexdigest() == expected, 'Asset changed during upload')
            response = connection.getresponse()
            require(response.status in (200, 201, 204), 'GitHub request failed with HTTP ' + str(response.status))
            body = response.read(8 * 1024 * 1024 + 1)
            require(len(body) <= 8 * 1024 * 1024, 'GitHub response exceeds limit')
            return json.loads(body) if body else None
        finally:
            connection.close()


def release_check(api, release_id, source_sha):
    require(type(release_id) is int and release_id > 0, 'A positive draft release ID is required')
    release = api.request('GET', f'/repos/{REPOSITORY}/releases/{release_id}')
    require(release.get('id') == release_id and release.get('draft') is True and
            release.get('tag_name') == TAG and release.get('target_commitish') == source_sha,
            'Release must be the specified draft, tag, and exact source commit')
    return release


def publish(api, release_id, source_sha, assets, verification):
    require(re.fullmatch('[0-9a-f]{40}', source_sha) is not None, 'Invalid source commit')
    require(set(assets) == set(ASSET_NAMES) and verification.get('ok') is True,
            'Only the four verified Windows assets may be uploaded')
    release_check(api, release_id, source_sha)
    existing = []
    for page in range(1, 101):
        rows = api.request('GET', f'/repos/{REPOSITORY}/releases/{release_id}/assets?per_page=100&page={page}')
        require(isinstance(rows, list), 'Invalid release asset listing')
        existing.extend(rows)
        if len(rows) < 100:
            break
    else:
        raise VerificationError('Release asset listing exceeds limit')
    outcomes = []
    for name in ASSET_NAMES:
        path = assets[name]
        expected = verification['assets'][name]
        require(path.name == name and path.is_file() and not path.is_symlink() and
                path.stat().st_size == expected['bytes'] and digest_file(path) == expected['sha256'],
                'Verified upload asset changed')
        matches = [row for row in existing if row.get('name') == name]
        require(len(matches) <= 1, 'Duplicate release asset name')
        if matches:
            current = matches[0]
            if current.get('digest') == 'sha256:' + expected['sha256'] and current.get('size') == expected['bytes']:
                release_check(api, release_id, source_sha)
                outcomes.append({'id': current['id'], 'name': name, 'sha256': expected['sha256'],
                                 'bytes': expected['bytes'], 'action': 'unchanged'})
                continue
            require(type(current.get('id')) is int and current['id'] > 0, 'Invalid existing asset ID')
            live = api.request('GET', f'/repos/{REPOSITORY}/releases/assets/{current["id"]}')
            require(live.get('id') == current['id'] and live.get('name') == name,
                    'Existing asset identity or name changed before deletion')
            release_check(api, release_id, source_sha)
            api.request('DELETE', f'/repos/{REPOSITORY}/releases/assets/{current["id"]}')
        release_check(api, release_id, source_sha)
        uploaded = api.request('POST', f'/repos/{REPOSITORY}/releases/{release_id}/assets?name={quote(name, safe="")}', upload=path)
        require(uploaded.get('name') == name and uploaded.get('size') == expected['bytes'] and
                uploaded.get('digest') == 'sha256:' + expected['sha256'] and
                type(uploaded.get('id')) is int and uploaded['id'] > 0,
                'Uploaded asset server digest or size mismatch')
        confirmed = api.request('GET', f'/repos/{REPOSITORY}/releases/assets/{uploaded["id"]}')
        require(confirmed.get('id') == uploaded['id'] and confirmed.get('name') == name
                and confirmed.get('size') == expected['bytes'] and
                confirmed.get('digest') == 'sha256:' + expected['sha256'],
                'Uploaded asset readback mismatch')
        outcomes.append({'id': uploaded['id'], 'name': name, 'sha256': expected['sha256'],
                         'bytes': expected['bytes'], 'action': 'uploaded'})
    release_check(api, release_id, source_sha)
    return {'ok': True, 'repository': REPOSITORY, 'release_id': release_id,
            'tag': TAG, 'source_commit': source_sha, 'draft': True, 'assets': outcomes,
            'verification': verification}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-root', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--release-id', type=int, default=0)
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--installed-root', type=Path)
    args = parser.parse_args(argv)
    try:
        if not args.verify_only:
            require(os.environ.get('GITHUB_REPOSITORY') == REPOSITORY, 'Repository is not authorized')
            require(args.release_id > 0, 'Release ID zero disables uploads')
        source_sha = os.environ.get('GITHUB_SHA', '')
        require(re.fullmatch('[0-9a-f]{40}', source_sha) is not None, 'Invalid source commit')
        actual_sha = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=REPO,
                                    capture_output=True, text=True, check=True).stdout.strip()
        require(actual_sha == source_sha, 'Runner checkout differs from GITHUB_SHA')
        assets, verification = verify_artifacts(args.artifact_root, source_sha=source_sha,
                                               installed_root=args.installed_root)
        source_paths = subprocess.run(
            ['git', 'ls-files', '-z', 'desktop/windows', 'desktop/macos/backend_runner.py',
             'scripts/build_desktop_macos.py', 'scripts/build_desktop_windows.py',
             'scripts/publish_windows_draft.py', '.github/workflows/desktop-windows.yml',
             '.gitattributes', 'LICENSE', 'tests/desktop_payload_probe.py',
             'skills/story-skill/scripts'], cwd=REPO, capture_output=True, check=True).stdout
        source_paths = [name.decode('utf-8') for name in source_paths.split(b'\0') if name]
        verification['source_inputs_sha256'] = {name: digest_file(REPO / name) for name in source_paths}
        verification['repository'] = os.environ.get('GITHUB_REPOSITORY', '')
        if args.verify_only:
            result = verification
        else:
            require(args.release_id > 0, 'Release ID zero disables uploads')
            result = publish(GitHub(os.environ.get('GITHUB_TOKEN')), args.release_id,
                             source_sha, assets, verification)
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'ok': True, 'receipt': str(args.receipt), 'uploaded': not args.verify_only}))
        return 0
    except Exception as error:
        # Never display API bodies, credential-bearing headers, or exception URLs.
        message = str(error) if isinstance(error, VerificationError) else type(error).__name__
        failure = {'ok': False, 'error': message}
        try:
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(json.dumps(failure) + '\n', encoding='utf-8')
        except OSError:
            pass
        print(json.dumps(failure), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
