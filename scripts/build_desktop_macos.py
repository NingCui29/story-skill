#!/usr/bin/env python3
"""Build a relocatable local macOS application without downloading dependencies."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile


REPO = Path(__file__).resolve().parents[1]
RUNTIME_FILES = (
    'story.py', 'story_world.py', 'story_punctuation.py', 'story_search.py',
    'story_history.py', 'story_publish.py', 'story_workbench.py',
    'story_outline.py', 'story_storage.py',
)
NATIVE_SOURCES = ('StoryWorkbench.swift', 'ClientInstance.swift')


def run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or
                           f'Command failed: {command[0]}')
    return result.stdout


def python_environment(runtime):
    environment = os.environ.copy()
    for key in ('PYTHONPATH', 'PYTHONEXECUTABLE', '__PYVENV_LAUNCHER__'):
        environment.pop(key, None)
    environment.update(PYTHONHOME=str(runtime), PYTHONNOUSERSITE='1',
                       PYTHONDONTWRITEBYTECODE='1')
    return environment


def python_prefix(executable):
    environment = os.environ.copy()
    for key in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONEXECUTABLE', '__PYVENV_LAUNCHER__'):
        environment.pop(key, None)
    probe = json.loads(run([str(executable), '-s', '-B', '-c',
        'import json,sys,platform;print(json.dumps(dict(prefix=sys.base_prefix,'
        'version=list(sys.version_info[:2]),arch=platform.machine())))'], env=environment))
    if probe['version'] != [3, 12] or probe['arch'] != platform.machine():
        raise RuntimeError('Use a Python 3.12 runtime for the current Mac architecture.')
    prefix = Path(probe['prefix']).resolve()
    if not (prefix / 'bin/python3').is_file():
        raise RuntimeError('This Python installation does not have a complete runtime prefix.')
    for path in prefix.rglob('*'):
        if path.is_symlink() and not path.resolve().is_relative_to(prefix):
            raise RuntimeError(f'Python runtime contains an external link: {path}')
    return prefix


def build(output, python):
    if sys.platform != 'darwin':
        raise RuntimeError('This builder runs on macOS with the Apple command line tools.')
    output = Path(output).expanduser().absolute()
    if output.suffix != '.app' or output.exists() or output.is_symlink():
        raise RuntimeError('Choose a new .app destination; existing applications are preserved.')
    prefix = python_prefix(Path(python).expanduser())
    source = REPO / 'desktop/macos'
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.story-desktop-build-', dir=output.parent) as raw:
        stage = Path(raw) / output.name
        contents = stage / 'Contents'
        resources = contents / 'Resources'
        executable = contents / 'MacOS/StoryWorkbench'
        executable.parent.mkdir(parents=True)
        resources.mkdir()
        shutil.copy2(source / 'Info.plist', contents / 'Info.plist')
        shutil.copy2(source / 'backend_runner.py', resources / 'backend_runner.py')
        shutil.copy2(source / 'editor-appearance.js', resources / 'editor-appearance.js')
        shutil.copy2(REPO / 'LICENSE', resources / 'StorySkill-LICENSE')
        scripts = resources / 'Scripts'
        scripts.mkdir()
        for name in RUNTIME_FILES:
            shutil.copy2(REPO / 'skills/story-skill/scripts' / name, scripts / name)
        runtime = resources / 'runtime'
        shutil.copytree(prefix, runtime, symlinks=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
        run(['xcrun', 'swiftc', '-O', '-parse-as-library', '-whole-module-optimization',
             '-target', platform.machine() + '-apple-macosx13.0',
             '-framework', 'Cocoa', '-framework', 'WebKit',
             *(str(source / name) for name in NATIVE_SOURCES), '-o', str(executable)], timeout=180)
        iconset = Path(raw) / 'AppIcon.iconset'
        iconset.mkdir()
        icon_tool = Path(raw) / 'make-icon'
        run(['xcrun', 'swiftc', '-O', '-framework', 'Cocoa',
             str(source / 'AppIcon.swift'), '-o', str(icon_tool)], timeout=120)
        run([str(icon_tool), str(iconset)], timeout=20)
        run(['iconutil', '-c', 'icns', str(iconset), '-o', str(resources / 'AppIcon.icns')], timeout=20)
        bundled_python = runtime / 'bin/python3'
        environment = python_environment(runtime)
        version = run([str(bundled_python), '-s', '-B', str(scripts / 'story.py'),
                       '--version'], env=environment, timeout=20).strip()
        if not version or '\n' in version:
            raise RuntimeError('The bundled Story Skill version could not be confirmed.')
        probe = json.loads(run([str(bundled_python), '-s', '-B', '-c',
            'import json,sys,sqlite3,ssl;print(json.dumps(dict(prefix=sys.prefix,'
            'executable=sys.executable,sqlite=sqlite3.sqlite_version,ssl=ssl.OPENSSL_VERSION)))'],
            env=environment, timeout=20))
        if Path(probe['prefix']).resolve() != runtime.resolve():
            raise RuntimeError('Bundled Python did not load its relocated standard library.')
        manifest = {'app_version': '0.1.1', 'story_version': version,
                    'architecture': platform.machine(), 'minimum_macos': '13.0',
                    'runtime_version': run([str(bundled_python), '--version'],
                                           env=environment, timeout=20).strip(),
                    'runtime_files': {name: hashlib.sha256((scripts / name).read_bytes()).hexdigest()
                                      for name in RUNTIME_FILES},
                    'native_source_sha256': hashlib.sha256((source / 'StoryWorkbench.swift').read_bytes()).hexdigest(),
                    'native_sources': {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                                       for name in NATIVE_SOURCES},
                    'launcher_sha256': hashlib.sha256((resources / 'backend_runner.py').read_bytes()).hexdigest(),
                    'editor_appearance_sha256': hashlib.sha256((resources / 'editor-appearance.js').read_bytes()).hexdigest(),
                    'local_build': True}
        (resources / 'build-manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        run(['codesign', '--force', '--deep', '--sign', '-', str(stage)], timeout=120)
        run(['codesign', '--verify', '--deep', '--strict', str(stage)], timeout=30)
        if output.exists() or output.is_symlink():
            raise RuntimeError('Application destination changed during the build; preserve it.')
        os.rename(stage, output)
    return {'ok': True, 'application': str(output), **manifest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=REPO / 'dist/写作工作台.app', type=Path)
    parser.add_argument('--python', default=sys.executable, type=Path,
                        help='Complete, relocatable Python 3.12 installation to bundle.')
    args = parser.parse_args()
    try:
        result = build(args.output, args.python)
    except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
        print(json.dumps({'ok': False, 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
