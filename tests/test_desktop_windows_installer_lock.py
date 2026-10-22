"""Final installer verification follows user interaction and retains a write-deny lock."""
import ctypes
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import patch

from test_desktop_windows_downloads import delivery, NAME, production_class, Widget


class ModelLock:
    """Portable handle-lifetime model; Windows sharing enforcement is tested separately."""
    def __init__(self, path):
        self.stream = open(path, 'rb')
        self.closed = False

    def stat(self):
        if self.closed:
            raise ValueError('closed model lock')
        return os.fstat(self.stream.fileno())

    def close(self):
        if not self.closed:
            self.closed = True
            self.stream.close()


class InstallerLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='story-installer-lock-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / NAME
        self.payload = b'MZ synthetic installer lock test\r\n' * 10000
        self.path.write_bytes(self.payload)
        self.update = delivery.verify_downloaded(delivery.DownloadedUpdate(
            str(self.path), hashlib.sha256(self.payload).hexdigest(), len(self.payload), '0.2.0', NAME))
        self.locks = []

    def lock(self, path):
        lock = ModelLock(path)
        self.locks.append(lock)
        return lock

    def test_same_inode_size_and_restored_mtime_rewrite_is_rejected_by_final_hash(self):
        before = self.path.stat()
        self.path.write_bytes(b'XX' + self.payload[2:])
        os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns))
        current = self.path.stat()
        self.assertEqual((current.st_ino, current.st_size, current.st_mtime_ns),
                         (before.st_ino, before.st_size, before.st_mtime_ns))
        actual_identity = delivery._identity
        # Windows Python 3.12 ctime remains the creation time. Even when that
        # metadata cannot expose the rewrite, the post-dialog full hash must fail.
        with patch.object(delivery, '_identity', side_effect=lambda value: (
                *actual_identity(value)[:-1], before.st_ctime_ns)):
            delivery.assert_verified_identity(self.update)
            with self.assertRaisesRegex(delivery.DownloadError, 'SHA256'):
                delivery.prepare_installer(self.update, lock_factory=self.lock)
        self.assertTrue(self.locks[0].closed)

    def test_lock_stays_open_through_process_creation_and_closes_on_success_and_failure(self):
        calls = []

        def launch(*args, **kwargs):
            self.assertFalse(self.locks[-1].closed)
            calls.append(args[0])

        cls = production_class('Window', {'complete_installer_handoff'},
                               dict(Path=Path, subprocess=SimpleNamespace(Popen=launch)))
        window = cls()
        dialog = SimpleNamespace(message=Widget(), mark_installer_started=lambda: calls.append('started'))
        prepared = delivery.prepare_installer(self.update, lock_factory=self.lock)
        self.assertFalse(self.locks[-1].closed)
        window.complete_installer_handoff(prepared, dialog)
        self.assertEqual(calls, [[self.update.path], 'started'])
        self.assertTrue(self.locks[-1].closed)
        prepared.close()  # Terminal paths may defensively close the same receipt twice.
        prepared = delivery.prepare_installer(self.update, lock_factory=self.lock)
        with patch.object(cls.complete_installer_handoff.__globals__['subprocess'], 'Popen',
                          side_effect=OSError('synthetic launch failure')):
            window.complete_installer_handoff(prepared, dialog)
        self.assertTrue(self.locks[-1].closed)
        self.assertIn('无法启动', dialog.message.content)
        self.assertEqual(calls.count('started'), 1)

    def test_cancel_before_lock_and_during_hash_never_retains_a_handle(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(delivery.DownloadCancelled):
            delivery.prepare_installer(self.update, cancel=cancel, lock_factory=self.lock)
        self.assertEqual(self.locks, [])
        cancel.clear()
        with self.assertRaises(delivery.DownloadCancelled):
            delivery.prepare_installer(self.update, cancel=cancel, lock_factory=self.lock,
                                       progress=lambda event: cancel.set())
        self.assertTrue(self.locks[0].closed)

    def test_lock_failure_and_handle_path_mismatch_cannot_produce_a_handoff(self):
        with self.assertRaises(OSError):
            delivery.prepare_installer(self.update, lock_factory=lambda path: (_ for _ in ()).throw(OSError('busy')))
        other = self.path.with_name('other.fixture')
        other.write_bytes(self.payload)
        with self.assertRaisesRegex(delivery.DownloadError, '不一致'):
            delivery.prepare_installer(self.update, lock_factory=lambda path: self.lock(other))
        self.assertTrue(self.locks[-1].closed)

    def test_save_decision_precedes_background_hash_and_cancelled_decision_starts_nothing(self):
        callbacks, events = [], []
        cls = production_class('Window', {'launch_verified_installer'}, {})
        window = cls()
        window.close_pending = window.close_after_update_cancel = False
        window.tabs = SimpleNamespace(count=lambda: 1, widget=lambda index: object())
        window.request_close = lambda views, action, completion: callbacks.append(completion)
        dialog = SimpleNamespace(busy=lambda: False, install_button=Widget(), message=Widget(),
                                 start_worker=lambda **kwargs: events.append(('background verify', kwargs)))
        window.launch_verified_installer(self.update, dialog)
        self.assertEqual(events, [])
        callbacks.pop()(False)
        self.assertEqual(events, [])
        window.launch_verified_installer(self.update, dialog)
        callbacks.pop()(True)
        self.assertEqual(events, [('background verify', {'verify': True})])
        window.launch_verified_installer(self.update, dialog)
        dialog.install_cancelled = True
        callbacks.pop()(True)
        self.assertEqual(len(events), 1)
        self.assertFalse(dialog.install_checking)

    def test_update_worker_hashes_off_calling_gui_thread_and_transfers_the_lock(self):
        emitted, errors, hashes = [], [], []

        def prepare(*args, **kwargs):
            hashes.append(threading.get_ident())
            return delivery.prepare_installer(*args, lock_factory=self.lock, **kwargs)

        cls = production_class('UpdateWorker', {'run'}, dict(
            prepare_installer=prepare, DownloadCancelled=delivery.DownloadCancelled))
        worker = cls()
        worker.verified, worker.cancel_event = self.update, threading.Event()
        worker.progress = SimpleNamespace(emit=lambda event: None)
        worker.succeeded = SimpleNamespace(emit=emitted.append)
        worker.failed = SimpleNamespace(emit=errors.append)
        worker.cancelled = SimpleNamespace(emit=lambda: errors.append('cancelled'))
        thread = threading.Thread(target=worker.run)
        thread.start()
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertNotEqual(hashes, [threading.get_ident()])
        self.assertEqual(len(emitted), 1)
        self.assertFalse(self.locks[0].closed)
        emitted[0].close()
        self.assertTrue(self.locks[0].closed)

    def test_close_during_save_guard_cancels_install_and_retries_normal_close_after_guard(self):
        queued, callbacks, actions = [], [], []
        window_cls = production_class('Window', {'launch_verified_installer', 'closeEvent'},
                                      dict(QTimer=SimpleNamespace(singleShot=lambda delay, callback: queued.append(callback))))
        dialog_cls = production_class('UpdateDialog', {'busy', 'cancel_download', 'closeEvent'}, {})
        window, dialog = window_cls(), dialog_cls()
        window.close_pending = window.close_after_update_cancel = window.close_approved = False
        window.tabs = SimpleNamespace(count=lambda: 1, widget=lambda index: object())
        window.request_close = lambda views, action, completion: callbacks.append(completion)
        window.close = lambda: actions.append('normal close retried')
        window.update_dialog, dialog.owner = dialog, window
        dialog.worker = None
        dialog.install_checking = dialog.install_cancelled = False
        dialog.install_editing_locked = False
        dialog.install_button, dialog.message = Widget(), Widget()
        dialog.start_worker = lambda **kwargs: actions.append('must not start hashing')
        window.launch_verified_installer(self.update, dialog)
        self.assertTrue(dialog.busy())
        event = SimpleNamespace(ignore=lambda: actions.append('close deferred'), accept=lambda: actions.append('accepted'))
        window.closeEvent(event)
        self.assertTrue(dialog.install_cancelled)
        callbacks.pop()(True)
        self.assertFalse(dialog.busy())
        self.assertFalse(window.close_after_update_cancel)
        self.assertEqual(actions, ['close deferred'])
        self.assertEqual(len(queued), 1)
        queued[0]()
        self.assertEqual(actions[-1], 'normal close retried')

    def test_worker_signal_failure_releases_its_untransferred_lock(self):
        errors = []
        cls = production_class('UpdateWorker', {'run'}, dict(
            prepare_installer=lambda *args, **kwargs: delivery.prepare_installer(*args, lock_factory=self.lock, **kwargs),
            DownloadCancelled=delivery.DownloadCancelled))
        worker = cls()
        worker.verified, worker.cancel_event = self.update, threading.Event()
        worker.progress = SimpleNamespace(emit=lambda event: None)
        worker.succeeded = SimpleNamespace(emit=lambda update: (_ for _ in ()).throw(RuntimeError('signal failure')))
        worker.failed = SimpleNamespace(emit=errors.append)
        worker.cancelled = SimpleNamespace(emit=lambda: errors.append('cancelled'))
        worker.run()
        self.assertEqual(errors, ['signal failure'])
        self.assertTrue(self.locks[0].closed)

    def test_win32_open_uses_read_only_sharing_and_transfers_handle_ownership(self):
        calls = []

        def create(*args):
            calls.append(args)
            return os.open(self.path, os.O_RDONLY)

        kernel = SimpleNamespace(CreateFileW=create, CloseHandle=lambda handle: os.close(handle))
        with patch.object(delivery.os, 'name', 'nt'), patch.object(delivery.os, 'O_BINARY', 0, create=True), \
                patch.object(ctypes, 'WinDLL', return_value=kernel, create=True), \
                patch.dict('sys.modules', msvcrt=SimpleNamespace(open_osfhandle=lambda handle, flags: handle)):
            lock = delivery.WindowsInstallerLock(str(self.path))
            descriptor = lock.stream.fileno()
            self.assertEqual(calls[0][1:6], (0x80000000, 0x1, None, 3, 0x00200080))
            self.assertEqual(lock.stat().st_size, len(self.payload))
            lock.close()
            lock.close()
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    @unittest.skipUnless(os.name == 'nt', 'Native Win32 share-mode enforcement requires Windows')
    def test_native_windows_lock_blocks_inplace_write_and_delete_until_released(self):
        prepared = delivery.prepare_installer(self.update)
        try:
            with self.assertRaises(OSError):
                with self.path.open('r+b') as stream:
                    stream.write(b'XX')
            with self.assertRaises(OSError):
                self.path.unlink()
        finally:
            prepared.close()
        with self.path.open('r+b') as stream:
            stream.write(b'XX')
        self.path.unlink()


if __name__ == '__main__':
    unittest.main()
