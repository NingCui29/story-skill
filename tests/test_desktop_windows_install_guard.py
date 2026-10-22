"""Use the production guard, worker setup, hash and handoff as one lifecycle."""
import hashlib
from pathlib import Path
from types import MethodType, SimpleNamespace
import tempfile
import threading
import unittest

from test_desktop_windows_downloads import delivery, NAME, production_class
import test_desktop_windows_downloads as downloads
from test_desktop_windows_installer_lock import ModelLock
import test_desktop_windows_safety as safety


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in self.callbacks:
            callback(*args)


class WindowsInstallGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='story-install-guard-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / NAME
        self.data = b'MZ synthetic update, never executed\r\n' * 10000
        self.path.write_bytes(self.data)
        self.update = delivery.verify_downloaded(delivery.DownloadedUpdate(
            str(self.path), hashlib.sha256(self.data).hexdigest(), len(self.data), '0.2.0', NAME))
        self.view = safety.View(safety.EDITOR)
        self.window, self.warnings = safety.CloseControlsTests().make_window([self.view])
        self.window.close_after_update_cancel = False
        self.launches, self.locks, self.typing, self.hash_threads = [], [], [], []
        self.fail_start = False
        self.dialog, self.queued, self.actions = downloads.ControlTests().dialog()
        self.dialog.owner, self.dialog.worker = self.window, None
        self.window.update_dialog = self.dialog
        self.window.close = lambda: self.actions.append('normal close retried')
        self.dialog.verified = self.update
        fixture = self

        def lock(path):
            value = ModelLock(path)
            self.locks.append(value)
            return value

        def prepare(*args, **kwargs):
            self.hash_threads.append(threading.get_ident())
            progress = kwargs['progress']

            def trying_to_type(event):
                # A real disabled QTabWidget prevents its editor descendants
                # receiving user input. Model that condition at every hash chunk.
                if event.stage == 'verifying':
                    self.typing.append(self.window.tabs.enabled)
                progress(event)

            return delivery.prepare_installer(*args, **{**kwargs, 'progress': trying_to_type}, lock_factory=lock)

        worker_class = production_class('UpdateWorker', {'run'}, dict(
            prepare_installer=prepare, DownloadCancelled=delivery.DownloadCancelled))

        class Worker(worker_class):
            def __init__(self, result, directory, *, verified=None, parent=None):
                self.verified, self.cancel_event = verified, threading.Event()
                self.progress, self.succeeded, self.failed, self.cancelled, self.finished = [Signal() for _ in range(5)]
                self.started = self.deleted = False

            def start(self):
                if fixture.fail_start:
                    raise RuntimeError('synthetic QThread start failure')
                self.started = True

            def deleteLater(self):
                self.deleted = True

        namespace = dict(Path=Path, UpdateWorker=Worker, QStandardPaths=SimpleNamespace(
            writableLocation=lambda *args: self.temp.name, StandardLocation=SimpleNamespace(CacheLocation=1)))
        dialog_class = production_class('UpdateDialog', {'start_worker'}, namespace)
        self.dialog.start_worker = MethodType(dialog_class.start_worker, self.dialog)

        def launch(*args, **kwargs):
            self.assertFalse(self.window.tabs.enabled, 'input must stay locked until process creation returns')
            self.assertFalse(self.locks[-1].closed, 'the installer file lock must outlive hashing')
            self.launches.append(args[0])

        window_class = production_class('Window', {'launch_verified_installer', 'complete_installer_handoff'},
                                       dict(Path=Path, subprocess=SimpleNamespace(Popen=launch)))
        self.window.launch_verified_installer = MethodType(window_class.launch_verified_installer, self.window)
        self.window.complete_installer_handoff = MethodType(window_class.complete_installer_handoff, self.window)

    def start_install(self):
        self.window.launch_verified_installer(self.update, self.dialog)
        self.assertFalse(self.window.tabs.enabled, 'the initial save guard holds input')
        self.view.reply(safety.state(safety.document()))

    def finish_hash(self):
        worker = self.dialog.worker
        thread = threading.Thread(target=worker.run)
        thread.start()
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        worker.finished.emit()

    def test_input_stays_locked_from_save_guard_through_real_hash_and_process_creation(self):
        self.start_install()
        self.assertFalse(self.window.tabs.enabled)
        self.assertTrue(self.dialog.cancel_button.enabled, 'cancel remains responsive outside the disabled tabs')
        blocked = []
        self.window.request_close([self.view], '重新载入', blocked.append)
        self.assertEqual(blocked, [False])
        self.assertEqual(len(self.view.scripts), 1, 'nested probes cannot re-enable the editor')
        self.assertFalse(self.window.tabs.enabled)
        self.finish_hash()
        self.assertTrue(self.typing)
        self.assertFalse(any(self.typing), 'no modeled input is accepted during final hashing')
        self.assertNotEqual(self.hash_threads, [threading.get_ident()])
        self.assertEqual(self.launches, [[self.update.path]])
        self.assertTrue(self.locks[0].closed)
        self.assertTrue(self.window.tabs.enabled)
        self.assertFalse(self.dialog.install_editing_locked)

    def test_cancel_restores_editing_without_starting_installer(self):
        self.start_install()
        self.dialog.cancel_download()
        self.assertFalse(self.window.tabs.enabled, 'do not unlock before cancellation is complete')
        self.finish_hash()
        self.assertEqual(self.launches, [])
        self.assertTrue(self.window.tabs.enabled)
        self.assertFalse(self.dialog.install_editing_locked)
        self.assertEqual(self.dialog.message.content, '更新已取消。')

    def test_final_hash_failure_releases_both_editing_and_file_locks(self):
        self.start_install()
        self.path.write_bytes(b'XX' + self.data[2:])
        self.finish_hash()
        self.assertEqual(self.launches, [])
        self.assertTrue(self.locks[0].closed)
        self.assertTrue(self.window.tabs.enabled)
        self.assertFalse(self.dialog.install_editing_locked)
        self.assertIn('SHA256', self.dialog.message.content)

    def test_qthread_start_failure_restores_editing_and_retry_controls(self):
        self.fail_start = True
        self.start_install()
        self.assertIsNone(self.dialog.worker)
        self.assertEqual(self.launches, [])
        self.assertTrue(self.window.tabs.enabled)
        self.assertFalse(self.dialog.install_editing_locked)
        self.assertTrue(self.dialog.install_button.enabled)
        self.assertTrue(self.dialog.close_button.enabled)
        self.assertIn('无法启动更新任务', self.dialog.message.content)

    def test_window_close_cancels_hash_then_retries_normal_close_after_unlock(self):
        self.start_install()
        event = SimpleNamespace(ignore=lambda: self.actions.append('close deferred'),
                                accept=lambda: self.actions.append('must not close directly'))
        self.window.closeEvent(event)
        self.assertTrue(self.window.close_after_update_cancel)
        self.assertFalse(self.window.tabs.enabled)
        self.finish_hash()
        self.assertEqual(self.launches, [])
        self.assertTrue(self.window.tabs.enabled)
        self.assertEqual(len(self.queued), 1)
        self.queued[0]()
        self.assertEqual(self.actions[-1], 'normal close retried')


if __name__ == '__main__':
    unittest.main()
