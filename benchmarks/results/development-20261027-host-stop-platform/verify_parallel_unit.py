#!/usr/bin/env python3
"""Run every unittest file once in four isolated processes; no model or platform calls."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def hashes(root, paths):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


class CountedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.successes = 0

    def addSuccess(self, test):
        super().addSuccess(test)
        self.successes += 1


def worker(root, manifest, output):
    sys.path[:0] = [str(root / 'tests'), str(root)]
    modules = json.loads(manifest.read_text())
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in modules)
    started = time.monotonic()
    result = unittest.TextTestRunner(resultclass=CountedResult).run(suite)
    report = {'ok': result.wasSuccessful() and result.successes > 0, 'run': result.testsRun,
              'passed': result.successes, 'failed': len(result.failures), 'errors': len(result.errors),
              'skipped': len(result.skipped), 'expected_failures': len(result.expectedFailures),
              'unexpected_successes': len(result.unexpectedSuccesses), 'modules': modules,
              'duration_seconds': round(time.monotonic() - started, 3)}
    write(output, report)
    return 0 if report['ok'] else 1


def run(root, output):
    output.mkdir(parents=True, exist_ok=False)
    sys.path[:0] = [str(root / 'tests'), str(root)]
    paths = sorted((root / 'tests').glob('test*.py'))
    test_before = hashes(root, paths)
    snapshot = json.loads((Path(__file__).parent / 'final-source-snapshot.json').read_text())
    source_before = hashes(root, [root / name for name in snapshot['files']])
    if source_before != snapshot['files']:
        raise ValueError('Source does not match frozen snapshot')
    groups = [[], [], [], []]
    for path in paths:
        name = path.stem
        if name.startswith(('test_install', 'test_codex', 'test_claude')):
            index = 0
        elif name.startswith(('test_package', 'test_dsh', 'test_workbuddy', 'test_qoder',
                              'test_host_stop_distribution', 'test_six_priorities_distribution')):
            index = 1
        elif name.startswith(('test_workbench', 'test_host_execution')):
            index = 2
        else:
            index = 3
        groups[index].append(name)
    sequential = unittest.defaultTestLoader.discover(str(root / 'tests')).countTestCases()
    grouped = sum(unittest.defaultTestLoader.loadTestsFromName(n).countTestCases() for g in groups for n in g)
    if grouped != sequential:
        raise ValueError(f'File grouping count {grouped} differs from discovery {sequential}')
    started = time.monotonic()
    report = {'schema': 1, 'started': datetime.now(timezone.utc).isoformat(),
              'method': 'Every test file once; four independent Python processes; one workbench/browser shard',
              'python': sys.executable, 'discovered': sequential, 'test_files': test_before,
              'source_files': source_before, 'groups': groups,
              'real_installation_modified': False, 'model_dispatch': False, 'platform_pages_used': False}
    write(output / 'run-manifest.json', report)
    pending = []
    try:
        for index, modules in enumerate(groups):
            manifest, result_path = output / f'group-{index}.json', output / f'unit-{index}.json'
            write(manifest, modules)
            log = (output / f'unit-{index}.log').open('w', encoding='utf-8')
            command = [sys.executable, '-B', str(Path(__file__).resolve()), '--repo', str(root),
                       '--worker', str(manifest), '--output', str(result_path)]
            process = subprocess.Popen(command, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
            pending.append((process, log, result_path, command))
        print(json.dumps({'started': True, 'discovered': sequential,
                          'groups': [len(g) for g in groups], 'output': str(output)}), flush=True)
        children = []
        for process, log, path, command in pending:
            code = process.wait()
            log.close()
            child = json.loads(path.read_text()) if path.is_file() else {'ok': False, 'error': 'No complete result'}
            children.append({'exit_code': code, 'report': str(path), 'command': command, **child})
        report['shards'] = children
        for key in ('run', 'passed', 'failed', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'):
            report[key] = sum(child.get(key, 0) for child in children)
        report['source_stable'] = source_before == hashes(root, [root / n for n in source_before])
        report['tests_stable'] = test_before == hashes(root, paths) and paths == sorted((root / 'tests').glob('test*.py'))
        report['ok'] = (all(child['ok'] and child['exit_code'] == 0 for child in children)
                        and report['run'] == sequential and report['source_stable'] and report['tests_stable'])
    finally:
        for process, log, _, _ in pending:
            if process.poll() is None:
                process.terminate()
                process.wait()
            log.close()
    report['finished'] = datetime.now(timezone.utc).isoformat()
    report['duration_seconds'] = round(time.monotonic() - started, 3)
    write(output / 'unit-tests.json', report)
    print(json.dumps({key: report[key] for key in ('ok', 'run', 'passed', 'failed', 'errors', 'skipped', 'source_stable', 'tests_stable')}), flush=True)
    return 0 if report['ok'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker', type=Path)
    args = parser.parse_args()
    root = args.repo.resolve()
    return worker(root, args.worker, args.output) if args.worker else run(root, args.output)


if __name__ == '__main__':
    raise SystemExit(main())
