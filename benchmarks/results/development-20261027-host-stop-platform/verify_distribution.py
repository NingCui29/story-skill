#!/usr/bin/env python3
"""Build five development packages and probe their isolated CLI runtimes.

Run only after freezing the intended source snapshot. No release upload, real
installation, model turn, author book, or platform page is used. --codex-host
adds the existing disposable-CODEX_HOME plugin discovery and runtime verifier.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import traceback
import zipfile


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def load(repo, name):
    spec = importlib.util.spec_from_file_location('host_stop_distribution_' + name, repo / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def execute(command, cwd, env, logs, name, timeout=120):
    process = subprocess.run(list(map(str, command)), cwd=cwd, env=env, capture_output=True,
                             text=True, encoding='utf-8', errors='replace', timeout=timeout)
    evidence = {'command': list(map(str, command)), 'exit_code': process.returncode,
                'stdout': process.stdout, 'stderr': process.stderr}
    write_json(logs / (name + '.json'), evidence)
    if process.returncode:
        raise RuntimeError(f'{name} failed; see {logs / (name + ".json")}')
    return process.stdout


def unpack(archive, destination):
    destination.mkdir()
    members = {}
    if archive.suffix == '.tgz':
        with tarfile.open(archive, 'r:gz') as bundle:
            for member in bundle:
                if not member.isfile():
                    raise ValueError('Non-file tar member: ' + member.name)
                if member.name in members:
                    raise ValueError('Duplicate tar member: ' + member.name)
                members[member.name] = bundle.extractfile(member).read()
    else:
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                if member.is_dir() or member.filename in members:
                    raise ValueError('Duplicate or directory ZIP member: ' + member.filename)
                members[member.filename] = bundle.read(member)
    for name, raw in members.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or any(part in ('', '.', '..') for part in relative.parts) or '\\' in name:
            raise ValueError('Unsafe package member: ' + name)
        target = destination.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    return {name: sha(raw) for name, raw in sorted(members.items())}


def probe(tool, label, output, env, logs, version):
    workspace = output / 'cli-workspaces' / label
    workspace.mkdir(parents=True)
    prefix = [sys.executable, '-B', '-X', 'utf8', str(tool)]
    observed_version = execute(prefix + ['--version'], workspace, env, logs, label + '-version').strip()
    if observed_version != version:
        raise ValueError(label + ' runtime version differs')
    for command in ('task-execute', 'task-execution', 'task-cancel', 'task-reconcile'):
        help_text = execute(prefix + [command, '--help'], workspace, env, logs, label + '-' + command)
        if '--book' not in help_text or '--task-id' not in help_text:
            raise ValueError(label + ' help is missing task scope')
    book = workspace / 'book'
    execute(prefix + ['init', '--book', str(book), '--title', '分发隔离空书'], workspace, env, logs, label + '-init')
    empty = json.loads(execute(prefix + ['task-list', '--book', str(book)], workspace, env, logs, label + '-task-list'))
    if empty.get('tasks') != [] or (book / '.story/assistant-tasks').exists():
        raise ValueError(label + ' empty task-list created workflow files or returned tasks')
    return {'version': observed_version, 'four_command_help': True, 'empty_task_list': True,
            'workflow_directory_created': False, 'model_turn_started': False}


def verify(repo, output, codex_host):
    report = {'schema': 1, 'ok': False, 'started': datetime.now(timezone.utc).isoformat(),
              'repo': str(repo), 'output': str(output), 'development_only': True,
              'published': False, 'real_installation_modified': False, 'platform_pages_used': False,
              'model_turn_started': False}
    output.mkdir(parents=True, exist_ok=False)
    logs = output / 'commands'
    logs.mkdir()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    try:
        suite = load(repo, 'package')
        entries = suite.source_entries()
        source = dict(entries)
        version = suite.source_version(source['story-skill/scripts/story.py'])
        if version != '0.6.29' or len(source) != 73:
            raise ValueError('Expected the frozen 0.6.29 development layout with 73 payload files')
        report['version'] = version
        report['source_manifest'] = {name: sha(raw) for name, raw in entries}
        report['source_git'] = execute(['git', 'rev-parse', 'HEAD'], repo, env, logs, 'source-head').strip()
        execute(['git', 'status', '--short'], repo, env, logs, 'source-status')
        builds = report['builds'] = {}
        builds['standard'] = suite.package(output / f'story-skill-{version}.zip')
        checksum = output / f'story-skill-{version}.zip.sha256'
        checksum.write_text(builds['standard']['sha256'] + '  ' + Path(builds['standard']['archive']).name + '\n', encoding='ascii')
        builds['codex'] = load(repo, 'package_codex').package(output / 'codex')
        builds['claude'] = load(repo, 'package_claude').package(output / f'story-skill-claude-desktop-{version}-r2.zip')
        builds['dsh'] = load(repo, 'package_dsh').build(output / 'dsh', archive=output / f'story-skill-dsh-{version}.zip')
        npm = load(repo, 'package_npm')
        builds['npm'] = npm.build(builds['standard']['archive'], checksum, output / 'npm')
        write_json(output / 'npm-build.json', builds['npm'])
        report['npm_reverification'] = npm.verify_tarball(builds['standard']['archive'], checksum,
                                                         builds['npm']['tarball'], output / 'npm-build.json')
        extractions = output / 'extracted'
        extractions.mkdir()
        tools = {
            'standard': 'story-skill/scripts/story.py',
            'codex': 'plugins/story-skill/skills/story-skill/scripts/story.py',
            'claude': 'story-skill/suite/story-skill/scripts/story.py',
            'dsh': 'skills/story-skill/scripts/story.py',
            'npm': 'package/story-skill/scripts/story.py',
        }
        probes = report['isolated_cli'] = {}
        manifests = report['package_manifests'] = {}
        for label, relative in tools.items():
            archive = Path(builds[label]['tarball' if label == 'npm' else 'archive'])
            folder = extractions / label
            manifests[label] = unpack(archive, folder)
            probes[label] = probe(folder / relative, label, output, env, logs, version)
        if codex_host:
            verifier = load(repo, 'verify_codex_plugin')
            host = verifier.verify(builds['codex']['marketplace_root'])
            write_json(output / 'codex-temporary-host.json', host)
            report['codex_temporary_host'] = {'ok': host['ok'], 'report': str(output / 'codex-temporary-host.json')}
            if not host['ok']:
                raise ValueError('Disposable Codex host verification failed')
        report['source_unchanged'] = entries == suite.source_entries()
        if not report['source_unchanged']:
            raise ValueError('Source changed during verification; rebuild from the final frozen snapshot')
        report['ok'] = True
    except Exception as error:
        report['error'] = str(error)
        report['traceback'] = traceback.format_exc()
    finally:
        report['finished'] = datetime.now(timezone.utc).isoformat()
        write_json(output / 'distribution.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--output-dir', required=True, type=Path, help='New isolated directory; refuses an existing path')
    parser.add_argument('--codex-host', action='store_true', help='Also load the plugin in a disposable Codex home; no model turn')
    args = parser.parse_args()
    report = verify(args.repo.resolve(), args.output_dir.expanduser().resolve(), args.codex_host)
    print(json.dumps({'ok': report['ok'], 'report': str(args.output_dir / 'distribution.json'),
                      'error': report.get('error')}, ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
