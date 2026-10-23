"""Windows CI-only real installed-service, upgrade, and rollback acceptance probe.

The application directory MUST be in RUNNER_TEMP, never a user's installation.
Run with the runner Python (outside the installation). The final installed tree
is left ready for the publisher's full manifest verification and final uninstall.
"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def snapshot(app):
    return {p.relative_to(app).as_posix(): digest(p) for p in app.rglob('*') if p.is_file()}


def run(app, installer, area, report, failure_installer=None):
    if sys.platform != 'win32' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('This native acceptance probe runs only in Windows GitHub CI.')
    runner_temp = Path(os.environ['RUNNER_TEMP']).resolve()
    app, area = app.resolve(), area.resolve()
    if runner_temp not in app.parents or runner_temp not in area.parents or app in Path(sys.executable).parents:
        raise RuntimeError('Probe requires an isolated RUNNER_TEMP installation and an external interpreter.')
    if area.exists():
        raise RuntimeError('Use a fresh probe directory.')
    area.mkdir(parents=True)
    result = {'ok': False, 'scope': 'isolated-installed-windows-lifecycle'}
    owned = None
    lifecycle = None
    record = None
    state = area / 'state'
    try:
        spec = importlib.util.spec_from_file_location('installed_lifecycle_probe', app / 'installation_lifecycle.py')
        lifecycle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lifecycle)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateMutexW(None, False, r'Local\StoryWorkbench.InstallLifecycle')
        error = ctypes.get_last_error()
        if not handle or error == 183:
            if handle:
                kernel.CloseHandle(handle)
            raise RuntimeError('Native lifecycle mutex fixture could not own a fresh name.')
        try:
            blocked_report = area / 'blocked-host.json'
            blocked_state = area / 'must-not-start-state'
            blocked = subprocess.run([str(app / 'StoryWorkbench.exe'), '--self-test-report', str(blocked_report),
                '--state-dir', str(blocked_state), '--port', '18769'], timeout=30, capture_output=True)
            if (blocked.returncode != 2 or blocked_state.exists() or lifecycle.processes_in(app) or
                    json.loads(blocked_report.read_text(encoding='utf-8')).get('ok') is not False):
                raise RuntimeError('Host did not refuse startup before launching a backend while install mutex exists.')
            result['installation_mutex_verified'] = True
        finally:
            kernel.CloseHandle(handle)
        original_manifest = (app / 'build-manifest.json').read_bytes()
        marker = app / '作者误放在安装目录的文件.txt'
        marker.write_text('Preserve this unknown file during migration.', encoding='utf-8')
        old_files = snapshot(app)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        with (area / 'service.log').open('wb') as log:
            owned = subprocess.Popen([str(app / 'runtime/python.exe'), '-I', '-B',
                str(app / 'Scripts/story.py'), 'workbench-library-serve', '--state-dir', str(state),
                '--port', str(port)], stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 20
        while owned.poll() is None and time.monotonic() < deadline:
            try:
                record = lifecycle._json(state / 'service.json', 8192)
                lifecycle._service_request(record, state, True)
                break
            except (OSError, ValueError, lifecycle.LifecycleError):
                time.sleep(.05)
        if record is None or record.get('pid') != owned.pid:
            raise RuntimeError('Isolated installed bookshelf did not become ready.')
        if owned.pid not in lifecycle.processes_in(app):
            raise RuntimeError('Native process inventory missed the actual installed interpreter.')
        try:
            lifecycle.quiesce(app, state)
        except lifecycle.SaveConfirmationRequired:
            pass
        else:
            raise RuntimeError('A live service was accepted without saved confirmation.')
        if owned.poll() is not None:
            raise RuntimeError('Read-only handoff check stopped the service.')
        result['unsaved_service_preserved'] = True

        # A silent installer/uninstaller must neither auto-confirm external edits
        # nor delete files. The custom state is deliberately undiscoverable from
        # the user's default shelf: unknown live processes must also block.
        commands = [
            ('blocked-upgrade', [str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                                 '/DIR=' + str(app), '/LOG=' + str(area / 'blocked-upgrade.log')]),
            ('blocked-uninstall', [str(app / 'unins000.exe'), '/VERYSILENT', '/SUPPRESSMSGBOXES',
                                   '/NORESTART', '/LOG=' + str(area / 'blocked-uninstall.log')]),
        ]
        for name, command in commands:
            process = subprocess.run(command, timeout=180, capture_output=True)
            if process.returncode == 0 or owned.poll() is not None or snapshot(app) != old_files:
                raise RuntimeError(name + ' did not preserve the running service and every installed file.')
            result[name.replace('-', '_') + '_verified'] = True
        lifecycle.quiesce(app, state, saved=True)
        owned.wait(timeout=15)
        if owned.returncode != 0 or lifecycle.processes_in(app):
            raise RuntimeError('Authenticated graceful stop left a process behind.')
        result['authenticated_saved_stop_verified'] = True

        # Exercise the same rollback commands used by Inno, including an unknown
        # file in the partial new installation. Neither side may be deleted.
        journal = area / 'rollback.json'
        registration = lifecycle.registry_snapshot()
        verified = lifecycle.verify_install(app, registration)
        lifecycle.prepare_upgrade(app, journal, registration, verified)
        app.mkdir()
        (app / 'partial-install.txt').write_text('preserve incomplete tree')
        restored = lifecycle.finish_upgrade(journal)
        if restored['state'] != 'restored' or snapshot(app) != old_files:
            raise RuntimeError('Rollback did not restore exact installed bytes.')
        if lifecycle.registry_snapshot() != registration:
            # Win32 registry enumeration order is not guaranteed.
            if sorted(lifecycle.registry_snapshot()) != sorted(registration):
                raise RuntimeError('Rollback did not restore uninstall registration.')
        if not (Path(restored['incomplete']) / 'partial-install.txt').is_file():
            raise RuntimeError('Rollback discarded the incomplete new tree.')
        result['rollback_restore_verified'] = True

        if failure_installer is not None:
            incomplete_before = set(app.parent.glob(app.name + '.incomplete-*'))
            failed = subprocess.run([str(failure_installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                '/DIR=' + str(app), '/LOG=' + str(area / 'failed-install-rollback.log')], timeout=240, capture_output=True)
            if failed.returncode == 0 or snapshot(app) != old_files:
                raise RuntimeError('Actual failed Inno install did not restore every prior installed byte.')
            if sorted(lifecycle.registry_snapshot()) != sorted(registration):
                raise RuntimeError('Actual failed Inno install did not restore uninstall registration.')
            incomplete_after = set(app.parent.glob(app.name + '.incomplete-*')) - incomplete_before
            if not any((p / 'lifecycle-failure-unowned.txt').is_file() for p in incomplete_after):
                raise RuntimeError('Actual failed Inno install discarded its untracked partial-install file.')
            result['inno_rollback_verified'] = True

        before = set(app.parent.glob(app.name + '.previous-*'))
        upgrade = subprocess.run([str(installer), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
            '/DIR=' + str(app), '/LOG=' + str(area / 'upgrade.log')], timeout=240, capture_output=True)
        if upgrade.returncode != 0:
            raise RuntimeError('Actual Inno upgrade failed: ' + str(upgrade.returncode))
        backups = set(app.parent.glob(app.name + '.previous-*')) - before
        if len(backups) != 1:
            raise RuntimeError('Actual upgrade did not preserve exactly one prior installation.')
        backup = backups.pop()
        if snapshot(backup) != old_files or marker.exists() or (app / 'build-manifest.json').read_bytes() != original_manifest:
            raise RuntimeError('Upgrade lost prior bytes or contaminated the new installed tree.')
        if lifecycle.processes_in(app):
            raise RuntimeError('Silent upgrade left an unexpected app process.')
        result.update(installed_upgrade_verified=True, unknown_file_preserved=True, no_remaining_service=True, ok=True)
        return result
    finally:
        if owned is not None and owned.poll() is None:
            # Only our fixture PID and verified identity can be stopped here.
            if record is not None and record.get('pid') == owned.pid:
                lifecycle._service_request(record, state, True, stop=True)
            else:
                owned.terminate()
            owned.wait(timeout=15)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True, type=Path)
    parser.add_argument('--installer', required=True, type=Path)
    parser.add_argument('--area', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--failure-installer', type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.app, args.installer, args.area, args.report, args.failure_installer), ensure_ascii=False, indent=2))
