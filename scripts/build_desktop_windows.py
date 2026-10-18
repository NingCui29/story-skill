#!/usr/bin/env python3
"""Build a portable Windows x64 desktop folder and ZIP on Windows."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

from build_desktop_macos import REPO, RUNTIME_FILES


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


def build(output):
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('Run this builder with Windows x64 Python.')
    output = Path(output).resolve()
    archive = output.with_suffix('.zip')
    if output.exists() or archive.exists():
        raise RuntimeError('Choose a new output directory; existing builds are preserved.')
    prefix = Path(sys.base_prefix)
    if not (prefix / 'python.exe').is_file() or not (prefix / 'Lib/encodings').is_dir():
        raise RuntimeError('A full CPython installation is required for building.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='story-windows-build-') as raw:
        stage = Path(raw)
        subprocess.run([
            sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
            '--onedir', '--windowed', '--name', 'StoryWorkbench',
            '--distpath', str(stage / 'dist'), '--workpath', str(stage / 'build'),
            '--specpath', str(stage), str(REPO / 'desktop/windows/client.py'),
        ], check=True)
        app = stage / 'dist/StoryWorkbench'
        runtime = app / 'runtime'
        runtime.mkdir()
        # Backend must be a regular interpreter: it starts independent editor
        # services using sys.executable, even after the desktop window closes.
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
        shutil.copy2(REPO / 'desktop/macos/backend_runner.py', app / 'backend_runner.py')
        shutil.copy2(REPO / 'LICENSE', app / 'StorySkill-LICENSE.txt')
        shutil.copy2(REPO / 'desktop/windows/README.md', app / 'README.md')
        notices = app / 'ThirdPartyLicenses'
        notices.mkdir()
        for distribution in ('PySide6', 'PySide6_Essentials', 'PySide6_Addons', 'shiboken6', 'pyinstaller'):
            package = importlib.metadata.distribution(distribution)
            for file in package.files or ():
                if any('license' in part.lower() or 'copying' in part.lower() for part in file.parts):
                    original = Path(package.locate_file(file))
                    if original.is_file():
                        destination = notices / distribution / Path(*file.parts)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(original, destination)
        result = subprocess.run([str(runtime / 'python.exe'), '-I', '-B',
                                 str(scripts / 'story.py'), '--version'],
                                capture_output=True, text=True, check=True, timeout=30)
        subprocess.run([str(runtime / 'python.exe'), '-I', '-B', '-c',
                        'import sqlite3, ssl, ctypes, http.server, multiprocessing'],
                       check=True, timeout=30)
        verify_book_runtime(runtime / 'python.exe', scripts / 'story.py', stage / '隔离验证 空格目录')
        manifest = {str(path.relative_to(app)).replace('\\', '/'):
                    hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in sorted(app.rglob('*')) if path.is_file()}
        (app / 'build-manifest.json').write_text(json.dumps({
            'platform': 'windows-x86_64', 'story_version': result.stdout.strip(),
            'files': manifest, 'ui_verified': False,
        }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        shutil.copytree(app, output)
    shutil.make_archive(str(archive.with_suffix('')), 'zip', output.parent, output.name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    return {'output': str(output), 'archive': str(archive), 'sha256': digest,
            'ui_verified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output), ensure_ascii=False, indent=2))
