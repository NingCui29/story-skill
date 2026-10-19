"""Delivery and native updater controls with isolated files and synthetic events."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'desktop/windows'))
try:
    import workbench_updates as metadata
    import workbench_update_download as delivery
finally:
    sys.path.pop(0)

NAME = 'story-workbench-0.2.0-windows-x86_64-setup.exe'
URL = 'https://github.com/NingCui29/story-skill/releases/download/0.9.0/' + NAME
CDN = 'https://release-assets.githubusercontent.com/github-production-release-asset/123/file?sig=fixture'
DATA = b'MZ synthetic updater fixture\r\n' * 20000


def result(**changes):
    fields = dict(state='available', message='synthetic', version='0.2.0', download_url=URL,
                  asset_name=NAME, asset_size=len(DATA), sha256=hashlib.sha256(DATA).hexdigest())
    fields.update(changes)
    return metadata.UpdateResult(**fields)


class Response:
    status = 200

    def __init__(self, data=DATA):
        self.data = data
        self.headers = {'Content-Length': str(len(data))}

    def geturl(self):
        return CDN

    def read(self, size):
        data, self.data = self.data[:size], self.data[size:]
        return data

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Opener:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        return self.responses.pop(0)


class DownloadTests(unittest.TestCase):
    def test_progress_represents_received_and_hashed_bytes_and_verified_artifact(self):
        events, opened = [], Opener(Response())
        with tempfile.TemporaryDirectory() as raw:
            downloaded = delivery.download_update(result(), raw, progress=events.append, opener=opened)
            self.assertEqual(Path(downloaded.path).read_bytes(), DATA)
            self.assertEqual(downloaded.sha256, hashlib.sha256(DATA).hexdigest())
            self.assertEqual(delivery.verify_downloaded(downloaded), downloaded)
            delivery.assert_verified_identity(downloaded)
            transfer = [event for event in events if event.stage == 'downloading']
            self.assertEqual(transfer[0].received, 0)
            self.assertEqual(transfer[-1].received, len(DATA))
            self.assertTrue(all(event.total == len(DATA) for event in transfer))
            self.assertTrue(all(0 <= event.received <= len(DATA) for event in events))
            self.assertTrue(any(event.speed > 0 and event.remaining is not None for event in transfer))
            self.assertEqual(events[-1].stage, 'ready')
            self.assertTrue(any(event.stage == 'verifying' for event in events))
        request, timeout = opened.calls[0]
        self.assertEqual(request.full_url, URL)
        self.assertEqual(timeout, delivery.SOCKET_TIMEOUT)
        self.assertFalse(any(key.lower() in ('authorization', 'cookie') for key in request.headers))

    def test_checksum_sidecar_is_read_and_bound_before_package_download(self):
        checksum = (hashlib.sha256(DATA).hexdigest() + '  ' + NAME + '\n').encode()
        opened = Opener(Response(checksum), Response())
        with tempfile.TemporaryDirectory() as raw:
            downloaded = delivery.download_update(result(sha256='', checksum_url=URL + '.sha256',
                                                        checksum_size=len(checksum)), raw, opener=opened)
            self.assertEqual(Path(downloaded.path).read_bytes(), DATA)
        self.assertEqual([call[0].full_url for call in opened.calls], [URL + '.sha256', URL])

    def test_cancel_before_start_has_no_download_or_cache_side_effect(self):
        cancel, opened = threading.Event(), Opener()
        cancel.set()
        with tempfile.TemporaryDirectory() as raw:
            cache = Path(raw) / 'cache'
            with self.assertRaises(delivery.DownloadCancelled):
                delivery.download_update(result(), cache, cancel=cancel, opener=opened)
            self.assertFalse(cache.exists())
        self.assertEqual(opened.calls, [])

    def test_total_deadline_aborts_and_cleans_written_partial_file(self):
        tick = 0

        def clock():
            nonlocal tick
            tick += 400
            return tick

        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(delivery.DownloadError, '超时'):
                delivery.download_update(result(), raw, opener=Opener(Response()), clock=clock)
            self.assertEqual(list(Path(raw).iterdir()), [])

    def test_changed_after_verification_cannot_launch_even_if_length_is_preserved(self):
        with tempfile.TemporaryDirectory() as raw:
            downloaded = delivery.download_update(result(), raw, opener=Opener(Response()))
            Path(downloaded.path).write_bytes(b'x' + DATA[1:])
            with self.assertRaisesRegex(delivery.DownloadError, '改变'):
                delivery.assert_verified_identity(downloaded)
            with self.assertRaises(delivery.DownloadError):
                delivery.verify_downloaded(downloaded)

    def test_verification_can_cancel_without_deleting_previous_verified_artifact(self):
        with tempfile.TemporaryDirectory() as raw:
            downloaded = delivery.download_update(result(), raw, opener=Opener(Response()))
            cancel = threading.Event()

            def progress(event):
                if event.received:
                    cancel.set()
            with self.assertRaises(delivery.DownloadCancelled):
                delivery.verify_downloaded(downloaded, cancel=cancel, progress=progress)
            self.assertTrue(Path(downloaded.path).is_file())

    def test_cdn_path_aliases_controls_and_unapproved_hosts_are_rejected(self):
        self.assertTrue(delivery.permitted_transfer_url(CDN))
        for path in ('/other/file', '/github-production-release-asset/../file',
                     '/github-production-release-asset/%2e%2e/file',
                     '/github-production-release-asset/a%2fb',
                     '/github-production-release-asset/a%5cb',
                     '/github-production-release-asset/%00bad'):
            self.assertFalse(delivery.permitted_transfer_url('https://release-assets.githubusercontent.com' + path))

    def test_portable_receipt_is_never_accepted_as_executable(self):
        name = NAME.replace('-setup.exe', '.zip')
        with tempfile.TemporaryDirectory() as raw:
            portable = delivery.download_update(result(asset_name=name, download_url=URL.replace(NAME, name)),
                                                raw, opener=Opener(Response()))
            with self.assertRaises(delivery.DownloadError):
                delivery.assert_verified_identity(portable)


class Widget:
    def __init__(self):
        self.visible, self.enabled, self.value = True, True, 0
        self.content = ''

    def setText(self, value):
        self.content = value

    def setEnabled(self, value):
        self.enabled = value

    def setValue(self, value):
        self.value = value

    def setRange(self, *value):
        self.range = value

    def setFormat(self, value):
        self.format = value

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False


def production_class(name, methods, namespace):
    tree = ast.parse((ROOT / 'desktop/windows/client.py').read_text())
    definition = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == name)
    definition.body = [node for node in definition.body if getattr(node, 'name', '') in methods]
    definition.bases = []
    exec(compile(ast.fix_missing_locations(ast.Module(body=[definition], type_ignores=[])),
                 '<production updater controls>', 'exec'), namespace)
    return namespace[name]


class ControlTests(unittest.TestCase):
    def dialog(self):
        queued, launches = [], []
        owner = SimpleNamespace(close_after_update_cancel=False, close=lambda: launches.append('close'),
                                launch_verified_installer=lambda *args: launches.append('launch'))
        namespace = dict(DownloadProgress=delivery.DownloadProgress, progress_text=delivery.progress_text,
                         QTimer=SimpleNamespace(singleShot=lambda delay, callback: queued.append(callback)))
        dialog_class = production_class('UpdateDialog', {
            'busy', 'worker_finished', 'set_progress', 'cancel_download', 'closeEvent', 'mark_installer_started'}, namespace)
        instance = dialog_class()
        for name in ('message', 'details', 'bar', 'close_button', 'cancel_button', 'download_button',
                     'folder_button', 'install_button'):
            setattr(instance, name, Widget())
        instance.owner, instance.result = owner, result()
        instance.launch_requested = False
        instance.worker = SimpleNamespace(cancel_event=threading.Event(), deleteLater=lambda: launches.append('deleted'))
        return instance, queued, launches

    def test_close_waits_for_live_worker_and_cancel_signal_does_not_destroy_it(self):
        dialog, queued, actions = self.dialog()
        event = SimpleNamespace(ignore=lambda: actions.append('ignored'), accept=lambda: actions.append('accepted'))
        worker = dialog.worker
        dialog.closeEvent(event)
        self.assertIs(dialog.worker, worker)
        self.assertTrue(worker.cancel_event.is_set())
        self.assertEqual(actions, ['ignored'])
        self.assertFalse(dialog.cancel_button.enabled)

    def test_cancel_between_success_and_finished_never_launches_installer(self):
        dialog, queued, actions = self.dialog()
        dialog.launch_requested = True
        dialog.outcome = ('ready', SimpleNamespace(size=len(DATA), asset_name=NAME))
        dialog.worker.cancel_event.set()
        dialog.worker_finished()
        self.assertIsNone(dialog.worker)
        self.assertNotIn('launch', actions)
        self.assertEqual(dialog.message.content, '更新已取消。')
        self.assertTrue(dialog.download_button.enabled)

    def test_window_close_waits_for_worker_finished_then_runs_existing_save_guard(self):
        dialog, queued, actions = self.dialog()
        window_class = production_class('Window', {'closeEvent'}, {})
        window = window_class()
        window.update_dialog, window.close_approved = dialog, False
        window.request_close = lambda *args: actions.append('save guard')
        dialog.owner = window
        window.close = lambda: actions.append('retry close')
        event = SimpleNamespace(ignore=lambda: actions.append('ignored'), accept=lambda: actions.append('accepted'))
        window.closeEvent(event)
        self.assertTrue(window.close_after_update_cancel)
        self.assertTrue(dialog.worker.cancel_event.is_set())
        self.assertNotIn('save guard', actions)
        dialog.outcome = ('cancelled', None)
        dialog.worker_finished()
        self.assertFalse(window.close_after_update_cancel)
        self.assertEqual(len(queued), 1)
        queued[0]()
        self.assertEqual(actions[-1], 'retry close')

    def test_installer_handoff_has_no_fake_completion_or_background_service_shutdown(self):
        dialog, queued, actions = self.dialog()
        dialog.mark_installer_started()
        self.assertIn('进度请查看安装向导', dialog.message.content)
        self.assertIn('不能确认安装完成', dialog.details.content)
        self.assertIn('服务不会被结束', dialog.details.content)
        self.assertFalse(dialog.bar.visible)
        self.assertEqual(dialog.close_button.content, '退出客户端，继续安装')
        self.assertNotIn('close', actions)

    def test_launch_requires_all_tabs_save_guard_and_receipt_after_user_decision(self):
        calls, callbacks, messages = [], [], []
        namespace = dict(Path=Path, assert_verified_identity=delivery.assert_verified_identity,
                         subprocess=SimpleNamespace(Popen=lambda *args, **kwargs: calls.append((args, kwargs))))
        window_class = production_class('Window', {'launch_verified_installer'}, namespace)
        window = window_class()
        views = [object(), object(), object()]
        window.tabs = SimpleNamespace(count=lambda: len(views), widget=lambda index: views[index])
        window.request_close = lambda checked_views, action, completion: (
            self.assertEqual(checked_views, views), callbacks.append(completion))
        dialog = SimpleNamespace(message=SimpleNamespace(setText=messages.append),
                                 mark_installer_started=lambda: calls.append('started'))
        with tempfile.TemporaryDirectory() as raw:
            downloaded = delivery.download_update(result(), raw, opener=Opener(Response()))
            window.launch_verified_installer(downloaded, dialog)
            self.assertEqual(calls, [])
            callbacks.pop()(False)
            self.assertEqual(calls, [])
            window.launch_verified_installer(downloaded, dialog)
            callbacks.pop()(True)
            self.assertEqual(calls[0][0], ([downloaded.path],))
            self.assertNotIn('shell', calls[0][1])
            self.assertEqual(calls[-1], 'started')
            calls.clear()
            window.launch_verified_installer(downloaded, dialog)
            Path(downloaded.path).write_bytes(b'x' + DATA[1:])
            callbacks.pop()(True)
            self.assertEqual(calls, [])
            self.assertIn('无法启动', messages[-1])


if __name__ == '__main__':
    unittest.main()
