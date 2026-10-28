#!/usr/bin/env python3
"""Install the lean payload in an isolated Windows folder, verify every byte and open its native window."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from check_forbidden_technologies import assert_clean_tree
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop/windows'))
from installation_lifecycle import registry_snapshot


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): value.update(block)
    return value.hexdigest()


def probe(installer, expected, area):
    if sys.platform != 'win32': raise RuntimeError('Native installation probe requires Windows')
    if any(name == 'Inno Setup: App Path' for name, _, _ in registry_snapshot()):
        raise RuntimeError('Use a clean Windows validation environment; an existing registered workbench must not be replaced by this fixture')
    installer, expected, area = Path(installer).resolve(), Path(expected).resolve(), Path(area).resolve()
    area.mkdir(parents=True, exist_ok=False)
    app, state = area / '精简更新验证 空格', area / 'state'
    raw = (expected / 'build-manifest.json').read_bytes()
    manifest = json.loads(raw)
    if manifest.get('distribution') != 'update-existing-webview2': raise ValueError('Not a lean update payload')
    binary_hash = digest(installer)
    process = subprocess.run([str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                              '/DIR=' + str(app), '/LOG=' + str(area / 'install.log')], timeout=180)
    if process.returncode != 0: raise RuntimeError('Lean update installer failed')
    if (app / 'build-manifest.json').read_bytes() != raw: raise ValueError('Installed lean manifest differs')
    if (app / 'Prerequisites/MicrosoftEdgeWebView2RuntimeInstallerX64.exe').exists():
        raise ValueError('Lean installation contains the offline browser installer')
    for name, expected_digest in manifest['files'].items():
        path = app / name
        if path.is_symlink() or not path.is_file() or digest(path) != expected_digest:
            raise ValueError('Installed lean payload checksum mismatch')
    registered = set(manifest['files']) | {'build-manifest.json'}
    extras = {'unins000.exe', 'unins000.dat', 'unins000.msg'}
    for path in app.rglob('*'):
        info = path.lstat()
        if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Installed lean payload contains a link or reparse point')
        if path.is_file() and path.relative_to(app).as_posix() not in registered | extras:
            raise ValueError('Installed lean payload contains an untracked file')
    assert_clean_tree(app)
    native_report = area / 'native.json'
    try:
        native = subprocess.run([str(app / 'StoryWorkbench.exe'), '--self-test-report', str(native_report),
                                 '--state-dir', str(state), '--port', '18766'], timeout=180)
        if native.returncode != 0: raise RuntimeError('Lean native window probe failed')
    finally:
        subprocess.run([str(app / 'runtime/python.exe'), '-I', '-B', str(app / 'Scripts/story.py'),
                        'workbench-library-stop', '--state-dir', str(state), '--saved'], check=True, timeout=60)
    observed = json.loads(native_report.read_text(encoding='utf-8-sig'))
    if not all(observed.get(key) is True for key in ('ok', 'native_window', 'shelf_rendered', 'themes_verified', 'update_progress_verified')):
        raise RuntimeError('Lean installed native acceptance is incomplete')
    if binary_hash != digest(installer): raise ValueError('Installer changed during validation')
    uninstall = subprocess.run([str(app / 'unins000.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'], timeout=180)
    if uninstall.returncode != 0 or (app / 'StoryWorkbench.exe').exists(): raise RuntimeError('Lean fixture uninstall failed')
    return {'ok': True, 'scope': 'isolated-installed-windows-update', 'client_version': manifest['client_version'],
            'installer_sha256': binary_hash, 'installed_manifest_sha256': hashlib.sha256(raw).hexdigest(),
            'manifest_files_verified': len(manifest['files']), 'offline_browser_absent': True,
            'forbidden_technologies_absent': True, 'native_report': observed, 'uninstall_verified': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installer', required=True, type=Path)
    parser.add_argument('--expected-payload', required=True, type=Path)
    parser.add_argument('--area', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    report = probe(args.installer, args.expected_payload, args.area)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
