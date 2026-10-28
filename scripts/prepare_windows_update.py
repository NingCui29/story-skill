#!/usr/bin/env python3
"""Prepare a verified Windows update payload without the offline WebView2 installer."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_forbidden_technologies import assert_clean_tree

WEBVIEW = 'Prerequisites/MicrosoftEdgeWebView2RuntimeInstallerX64.exe'


def linked(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(data)
    return value.hexdigest()


def prepare(source, output):
    source, output = Path(source).absolute(), Path(output).absolute()
    if linked(source) or not source.is_dir() or output.exists() or linked(output):
        raise ValueError('Choose an existing complete source and a new update directory')
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('Update output must be separate from the complete payload')
    manifest_path = source / 'build-manifest.json'
    if linked(manifest_path) or not manifest_path.is_file(): raise ValueError('Build manifest must be a regular file')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    files = manifest['files']
    if manifest.get('ui_stack') != 'wpf-webview2' or WEBVIEW not in files:
        raise ValueError('A complete WPF/WebView2 build manifest is required')
    actual = set()
    for path in source.rglob('*'):
        if linked(path):
            raise ValueError('Linked update payload inputs are prohibited')
        if path.is_file(): actual.add(path.relative_to(source).as_posix())
        elif not path.is_dir(): raise ValueError('Special update payload input')
    if actual != set(files) | {'build-manifest.json'}:
        raise ValueError('Complete payload differs from its file manifest')
    for name, expected in files.items():
        path = PurePosixPath(name)
        if path.is_absolute() or path.as_posix() != name or '\\' in name or '\x00' in name or any(
                part in ('.', '..') or ':' in part or part.rstrip(' .') != part for part in path.parts):
            raise ValueError('Invalid source manifest path')
        if digest(source / name) != expected:
            raise ValueError('Complete payload checksum mismatch')
    # Keep all .NET, Python, native host and license files; remove only the
    # standalone browser installer. Never copy from an author's installation.
    output.mkdir(parents=True)
    for name in files:
        if name == WEBVIEW: continue
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)
    manifest['files'] = {name: value for name, value in files.items() if name != WEBVIEW}
    manifest['distribution'] = 'update-existing-webview2'
    manifest['webview2_runtime'] = {'kind': 'existing', 'architecture': 'x64', 'required': True}
    (output / 'build-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    assert_clean_tree(output)
    report = {'ok': True, 'scope': 'prepared-update-payload', 'native_install_verified': False,
              'source_manifest_sha256': digest(source / 'build-manifest.json'),
              'update_manifest_sha256': digest(output / 'build-manifest.json'),
              'removed_bytes': (source / WEBVIEW).stat().st_size,
              'preserved_files': len(manifest['files']), 'requires_existing_webview2': True}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    result = prepare(args.source, args.output)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
