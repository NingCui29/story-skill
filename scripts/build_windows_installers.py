#!/usr/bin/env python3
"""Build complete/offline and lean/update installers plus immutable download plans."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from build_desktop_windows import REPO, digest_file, project_configuration
from package_update_manifest import write_manifest
from prepare_windows_update import prepare


def build(source, output, compiler):
    if sys.platform != 'win32':
        raise RuntimeError('Inno Setup installer builds require Windows')
    source, output, compiler = Path(source).resolve(), Path(output).resolve(), Path(compiler).resolve()
    version = project_configuration()['client_version']
    if json.loads((source / 'build-manifest.json').read_text())['client_version'] != version:
        raise ValueError('Portable build and installer source versions differ')
    output.mkdir(parents=True, exist_ok=True)
    full = output / f'story-workbench-{version}-windows-x86_64-setup.exe'
    light = output / f'story-workbench-{version}-windows-x86_64-update-setup.exe'
    portable = output / f'story-workbench-{version}-windows-x86_64.zip'
    for target in (full, light, portable):
        if target.exists() or target.is_symlink(): raise ValueError('Choose a new installer output directory')
    lean = output / 'update-payload'
    prepared = prepare(source, lean)
    (output / 'windows-update-payload.json').write_text(json.dumps(prepared, indent=2) + '\n')
    for app, target, extra in ((source, full, []), (lean, light, ['/DUpdateOnly=1'])):
        subprocess.run([str(compiler), '/Qp', *extra, '/DAppSource=' + str(app),
                        '/DInstallerOutput=' + str(output), str(REPO / 'desktop/windows/installer.iss')], check=True)
        if not target.is_file(): raise RuntimeError('Installer compiler did not produce the expected file')
    original = source.with_suffix('.zip')
    if not original.is_file(): raise ValueError('Verified complete portable archive is missing')
    shutil.copy2(original, portable)
    records = []
    for target in (full, light, portable):
        with target.with_name(target.name + '.sha256').open('x', encoding='ascii') as stream:
            stream.write(digest_file(target) + '  ' + target.name + '\n')
        records.append(write_manifest(target))
    result = {'client_version': version, 'prepared_update': prepared, 'downloads': records,
              'native_install_verified': False}
    (output / 'windows-installer-build.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--compiler', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output, args.compiler), ensure_ascii=False, indent=2))
