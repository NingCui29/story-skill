"""Offline release and async desktop-control regression checks (no Qt required)."""
import ast
import importlib.util
import json
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'workbench_updates', ROOT / 'desktop/windows/workbench_updates.py')
updates = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updates)
CLIENT_SPEC = importlib.util.spec_from_file_location(
    'windows_theme_client', ROOT / 'desktop/windows/client.py')
client = importlib.util.module_from_spec(CLIENT_SPEC)
CLIENT_SPEC.loader.exec_module(client)


def release(version='0.1.2', extension='-setup.exe', **changes):
    name = f'story-workbench-{version}-windows-x86_64{extension}'
    result = {
        'tag_name': '0.6.18', 'draft': False, 'prerelease': False,
        'html_url': 'https://github.com/NingCui29/story-skill/releases/tag/0.6.18',
        'assets': [{'name': name, 'state': 'uploaded', 'browser_download_url':
                    'https://github.com/NingCui29/story-skill/releases/download/0.6.18/' + name}],
    }
    result.update(changes)
    return result


def evaluate(packet, **kwargs):
    return updates.evaluate_release_response(200, json.dumps(packet).encode('utf-8'), **kwargs)


class ReleaseTests(unittest.TestCase):
    def test_client_version_is_independent_of_skill_release_tag(self):
        result = evaluate(release())
        self.assertEqual(result.state, 'available')
        self.assertEqual(result.version, '0.1.2')
        self.assertIn('0.1.1', result.message)
        self.assertNotIn('0.6.18', result.message)

    def test_numeric_version_comparison(self):
        self.assertEqual(evaluate(release('0.1.10'), current_version='0.1.9').state, 'available')
        self.assertEqual(evaluate(release('0.1.1')).state, 'current')
        result = evaluate(release('0.1.0'))
        self.assertEqual(result.state, 'ahead')
        self.assertEqual(result.download_url, '')

    def test_version_validation_rejects_tags_and_prereleases(self):
        for version in ('v0.1.1', '0.1.1-rc1', '0.1', '0.1.1.1', None):
            with self.subTest(version=version), self.assertRaises(ValueError):
                updates.version_components(version)

    def test_highest_platform_version_and_installer_are_preferred(self):
        packet = release('0.1.2', '.zip')
        packet['assets'] += release('0.1.2')['assets'] + release('0.1.1')['assets']
        result = evaluate(packet)
        self.assertTrue(result.download_url.endswith('-setup.exe'))
        self.assertEqual(result.version, '0.1.2')

    def test_portable_zip_is_supported(self):
        self.assertTrue(evaluate(release(extension='.zip')).download_url.endswith('.zip'))

    def test_no_matching_platform_or_version_is_not_reported_as_latest(self):
        for name in ('story-workbench-0.1.7-macos-x86_64.dmg',
                     'story-workbench-windows-x86_64-setup.exe',
                     'story-workbench-0.1.2-windows-arm64-setup.exe',
                     'story-workbench-0.1.2-rc1-windows-x86_64-setup.exe'):
            packet = release()
            packet['assets'][0]['name'] = name
            result = evaluate(packet)
            self.assertEqual(result.state, 'unavailable', name)
            self.assertNotIn('已是最新', result.message)
            self.assertEqual(result.download_url, '')

    def test_unuploaded_or_unmatched_asset_is_rejected(self):
        packet = release()
        packet['assets'][0]['state'] = 'new'
        self.assertEqual(evaluate(packet).state, 'unavailable')
        for replacement in ('wrong.exe', '../wrong.exe'):
            packet = release()
            packet['assets'][0]['browser_download_url'] = (
                'https://github.com/NingCui29/story-skill/releases/download/0.6.18/' + replacement)
            self.assertEqual(evaluate(packet).state, 'error')
        packet = release()
        packet['assets'][0]['browser_download_url'] = packet['assets'][0]['browser_download_url'].replace(
            '/0.6.18/', '/other-tag/')
        self.assertEqual(evaluate(packet).state, 'error')

    def test_invalid_compatible_asset_cannot_hide_behind_an_older_valid_asset(self):
        packet = release('0.1.1')
        invalid_asset = release('0.1.2')['assets'][0]
        invalid_asset['browser_download_url'] = 'https://evil.test/new.exe'
        packet['assets'].append(invalid_asset)
        result = evaluate(packet)
        self.assertEqual(result.state, 'error')
        self.assertEqual(result.download_url, '')

    def test_release_flags_and_structure_fail_closed(self):
        for changes in ({'draft': True}, {'prerelease': True}, {'draft': None},
                        {'assets': {}}, {'html_url': 'https://example.com/releases/tag/0.6.18'}):
            self.assertEqual(evaluate(release(**changes)).state, 'error')
        for data in (b'[]', b'not JSON', b'\xff', b'{}'):
            self.assertEqual(updates.evaluate_release_response(200, data).state, 'error')

    def test_absent_release_rate_limit_and_http_failure_have_distinct_feedback(self):
        self.assertEqual(updates.evaluate_release_response(404, b'').state, 'unavailable')
        for status in (403, 429):
            self.assertIn('请求限额', updates.evaluate_release_response(status, b'').message)
        self.assertIn('HTTP 500', updates.evaluate_release_response(500, b'').message)
        self.assertEqual(updates.evaluate_release_response(
            200, b' ' * (updates.MAX_RESPONSE_BYTES + 1)).state, 'error')

    def test_download_links_are_limited_to_official_https_repository(self):
        valid = release()['assets'][0]['browser_download_url']
        self.assertTrue(updates.official_release_url(valid, download=True))
        for value in (valid.replace('https:', 'http:'), valid.replace('github.com', 'github.com.evil.test'),
                      valid.replace('github.com', 'user@github.com'), valid + '?redirect=evil',
                      valid + '#extra', valid.replace('NingCui29', 'another-user'),
                      valid.replace('/0.6.18/', '/%2e%2e/'), valid.replace('/0.6.18/', '/a%2fb/'),
                      valid.replace('github.com', 'github.com:443'),
                      valid.replace('github.com', 'git\nhub.com'), None):
            self.assertFalse(updates.official_release_url(value, download=True), value)

    def test_client_and_installer_use_the_same_desktop_version(self):
        installer = (ROOT / 'desktop/windows/installer.iss').read_text(encoding='utf-8-sig')
        version = re.search(r'#define ClientVersion "([^"]+)"', installer).group(1)
        self.assertEqual(version, updates.CLIENT_VERSION)
        self.assertIn('AppVersion={#ClientVersion}', installer)
        self.assertIn('OutputBaseFilename=story-workbench-{#ClientVersion}-windows-x86_64-setup', installer)
        spec = importlib.util.spec_from_file_location('windows_builder', ROOT / 'scripts/build_desktop_windows.py')
        import sys
        sys.path.insert(0, str(ROOT / 'scripts'))
        try:
            builder = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(builder)
        finally:
            sys.path.pop(0)
        self.assertEqual(builder.desktop_client_version(), version)


class Signal:
    def connect(self, callback):
        self.callback = callback


class Timer:
    def __init__(self, parent):
        self.timeout = Signal()
        self.stopped = False

    def setSingleShot(self, value):
        self.single_shot = value

    def start(self, milliseconds):
        self.milliseconds = milliseconds

    def stop(self):
        self.stopped = True


class Request:
    Attribute = SimpleNamespace(RedirectPolicyAttribute=1, HttpStatusCodeAttribute=2)
    RedirectPolicy = SimpleNamespace(ManualRedirectPolicy=0)

    def __init__(self, url):
        self.url = url
        self.headers = {}

    def setRawHeader(self, name, value):
        self.headers[name] = value

    def setAttribute(self, key, value):
        self.redirect = value


class Reply:
    def __init__(self, data=b'', status=200, error=0):
        self.data, self.status, self.network_error = data, status, error
        self.readyRead, self.finished = Signal(), Signal()
        self.aborted, self.deleted = False, False

    def readAll(self):
        data, self.data = self.data, b''
        return data

    def attribute(self, key):
        return self.status

    def error(self):
        return self.network_error

    def abort(self):
        self.aborted = True
        self.finished.callback()

    def deleteLater(self):
        self.deleted = True


class AsyncControlTests(unittest.TestCase):
    def make_window(self, reply):
        # Execute the real transport methods with deterministic event objects.
        tree = ast.parse((ROOT / 'desktop/windows/client.py').read_text())
        window = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == 'Window')
        names = {'check_for_updates', 'update_timeout', 'read_update_response', 'finish_update_check'}
        window.body = [node for node in window.body if getattr(node, 'name', '') in names]
        window.bases = []
        messages = []
        namespace = {name: getattr(updates, name) for name in (
            'CLIENT_VERSION', 'LATEST_RELEASE_API', 'MAX_RESPONSE_BYTES',
            'UpdateResult', 'evaluate_release_response', 'official_release_url')}
        namespace.update(QNetworkRequest=Request, QNetworkReply=SimpleNamespace(
            NetworkError=SimpleNamespace(NoError=0)), QTimer=Timer, QUrl=lambda value: value,
            QMessageBox=SimpleNamespace(information=lambda owner, title, message: messages.append(message)))
        exec(compile(ast.fix_missing_locations(ast.Module(body=[window], type_ignores=[])),
                     '<real Windows update controls>', 'exec'), namespace)
        instance = namespace['Window']()
        calls = []
        instance.update_manager = SimpleNamespace(get=lambda request: (calls.append(request), reply)[1])
        instance.update_reply = None
        action = SimpleNamespace(enabled=True)
        action.setEnabled = lambda enabled: setattr(action, 'enabled', enabled)
        instance.update_action = action
        instance.statusBar = lambda: SimpleNamespace(showMessage=lambda message: None, clearMessage=lambda: None)
        instance._test_namespace = namespace
        return instance, calls, messages

    def test_check_returns_before_reply_and_disables_duplicate_requests(self):
        reply = Reply(json.dumps(release('0.1.1')).encode())
        window, requests, messages = self.make_window(reply)
        window.check_for_updates()
        window.check_for_updates()
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url, updates.LATEST_RELEASE_API)
        self.assertFalse(window.update_action.enabled)
        self.assertEqual(messages, [])
        self.assertEqual(window.update_timer.milliseconds, 15000)
        reply.finished.callback()
        self.assertTrue(window.update_action.enabled)
        self.assertTrue(reply.deleted)
        self.assertIn('已是最新', messages[0])

    def test_timeout_aborts_request_and_allows_retry(self):
        reply = Reply(status=None, error=1)
        window, requests, messages = self.make_window(reply)
        window.check_for_updates()
        window.update_timer.timeout.callback()
        self.assertTrue(reply.aborted)
        self.assertIsNone(window.update_reply)
        self.assertTrue(window.update_action.enabled)
        self.assertIn('超时', messages[0])

    def test_available_update_opens_only_when_download_is_selected(self):
        for selected in (False, True):
            reply = Reply(json.dumps(release()).encode())
            window, _, _ = self.make_window(reply)
            opened = []

            class Dialog:
                ButtonRole = SimpleNamespace(AcceptRole=1)
                StandardButton = SimpleNamespace(Close=2)

                def __init__(self, owner):
                    self.buttons = []

                def setWindowTitle(self, title):
                    pass

                def setText(self, message):
                    pass

                def addButton(self, label, *arguments):
                    button = object()
                    self.buttons.append(button)
                    return button

                def exec(self):
                    pass

                def clickedButton(self):
                    return self.buttons[0 if selected else -1]

            window._test_namespace.update(
                QMessageBox=Dialog, QDesktopServices=SimpleNamespace(openUrl=opened.append))
            window.check_for_updates()
            self.assertEqual(opened, [])
            reply.finished.callback()
            self.assertEqual(opened, [release()['assets'][0]['browser_download_url']] if selected else [])

    def test_network_failure_and_rate_limit_are_reported(self):
        for reply, expected in ((Reply(status=None, error=1), '无法连接'),
                                (Reply(json.dumps(release('0.1.1')).encode(), status=200, error=1), '无法连接'),
                                (Reply(json.dumps(release('0.1.2')).encode(), status=200, error=1), '无法连接'),
                                (Reply(b'{}', status=403, error=1), '请求限额')):
            window, _, messages = self.make_window(reply)
            window.check_for_updates()
            reply.finished.callback()
            self.assertIn(expected, messages[0])

    def test_response_limit_aborts_without_buffering_a_download(self):
        reply = Reply(b' ' * (updates.MAX_RESPONSE_BYTES + 1))
        window, _, messages = self.make_window(reply)
        window.check_for_updates()
        reply.readyRead.callback()
        self.assertTrue(reply.aborted)
        self.assertEqual(window.update_buffer, bytearray())
        self.assertIn('大小限制', messages[0])

    def test_oversized_final_chunk_cannot_reenter_finish_and_clear_the_reply(self):
        reply = Reply(b' ' * (updates.MAX_RESPONSE_BYTES + 1))
        window, _, messages = self.make_window(reply)
        window.check_for_updates()
        reply.finished.callback()
        self.assertTrue(reply.aborted)
        self.assertIsNone(window.update_reply)
        self.assertEqual(len(messages), 1)
        self.assertIn('大小限制', messages[0])


class ThemePersistenceTests(unittest.TestCase):
    def cookie(self, value=b'dark', name=b'story_workbench_theme', domain='127.0.0.1', path='/'):
        return SimpleNamespace(name=lambda: name, value=lambda: value,
                               domain=lambda: domain, path=lambda: path)

    def test_only_local_namespaced_whitelisted_preferences_are_persisted(self):
        for value in client.THEME_CHOICES:
            self.assertEqual(client.theme_from_cookie(self.cookie(value.encode())), value)
        for changes in ({'value': b'javascript:bad'}, {'value': b'\xff'}, {'name': b'capability'},
                        {'domain': 'example.com'}, {'domain': 'localhost'}, {'path': '/book/'}):
            self.assertIsNone(client.theme_from_cookie(self.cookie(**changes)))

    def test_cookie_changes_update_only_theme_settings_and_next_navigation_script(self):
        tree = ast.parse((ROOT / 'desktop/windows/client.py').read_text())
        window = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == 'Window')
        window.body = [node for node in window.body if getattr(node, 'name', '') in
                       {'set_theme_seed', 'remember_theme_cookie'}]
        window.bases = []

        class Script:
            InjectionPoint = SimpleNamespace(DocumentCreation=1)
            ScriptWorldId = SimpleNamespace(MainWorld=0)

            def setName(self, value):
                self.name = value

            def setInjectionPoint(self, value):
                self.point = value

            def setWorldId(self, value):
                self.world = value

            def setRunsOnSubFrames(self, value):
                self.frames = value

            def setSourceCode(self, value):
                self.source = value

        namespace = {'QWebEngineScript': Script, 'THEME_CHOICES': client.THEME_CHOICES,
                     'theme_from_cookie': client.theme_from_cookie,
                     'theme_seed_script': client.theme_seed_script}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[window], type_ignores=[])),
                     '<real Windows theme persistence>', 'exec'), namespace)
        saved = {}

        def instance(initial):
            current = namespace['Window']()
            current.theme_script = None
            scripts = []
            current.profile = SimpleNamespace(scripts=lambda: SimpleNamespace(
                insert=scripts.append, remove=scripts.remove))
            current.theme_settings = SimpleNamespace(
                setValue=lambda key, value: saved.__setitem__(key, value), sync=lambda: None)
            current.set_theme_seed(initial)
            return current, scripts

        first, scripts = instance('system')
        first.remember_theme_cookie(self.cookie())
        self.assertEqual(saved, {'appearance/theme': 'dark'})
        self.assertEqual(len(scripts), 1)
        self.assertIn('const value="dark"', scripts[0].source)
        self.assertEqual(scripts[0].point, Script.InjectionPoint.DocumentCreation)
        self.assertFalse(scripts[0].frames)
        first.remember_theme_cookie(self.cookie(b'not-a-theme'))
        self.assertEqual(saved, {'appearance/theme': 'dark'})
        reopened, _ = instance(saved['appearance/theme'])
        self.assertEqual(reopened.theme_preference, 'dark')
        reopened.set_theme_seed('bad value')
        self.assertEqual(reopened.theme_preference, 'system')


class NativeSmokeTests(unittest.TestCase):
    def run_smoke(self, folder, *, rejected_theme=None, save_succeeds=True):
        tree = ast.parse((ROOT / 'desktop/windows/client.py').read_text())
        completed = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                         and node.name == 'completed')
        wrapper = ast.FunctionDef(
            name='make_callback', args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[],
                                                     kw_defaults=[], defaults=[]),
            body=[ast.Assign(targets=[ast.Name(id='self_test_started', ctx=ast.Store())],
                             value=ast.Constant(False)), completed,
                  ast.Return(value=ast.Name(id='completed', ctx=ast.Load()))], decorator_list=[])
        selections, saved, queued, exits = [], [], [], []

        def javascript(source, callback):
            if 'library-heading' in source:
                callback(True)
            elif '?.value' in source:
                callback('system')
            else:
                value = json.loads(re.search(r'control.value=("[^"]+");', source).group(1))
                selections.append(value)
                callback(value != rejected_theme)

        report = folder / 'windows-native-smoke.json'
        page = SimpleNamespace(runJavaScript=javascript)
        namespace = {
            'json': json, 'CLIENT_VERSION': updates.CLIENT_VERSION,
            'options': SimpleNamespace(self_test_report=report),
            'first': SimpleNamespace(page=lambda: page, title=lambda: '写作工作台', grab=lambda:
                                     SimpleNamespace(save=lambda target: (saved.append(target), save_succeeds)[1])),
            'window': SimpleNamespace(isVisible=lambda: True,
                                      update_action=SimpleNamespace(isEnabled=lambda: True)),
            'QTimer': SimpleNamespace(singleShot=lambda milliseconds, callback: queued.append((milliseconds, callback))),
            'app': SimpleNamespace(exit=exits.append),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])),
                     '<real Windows native smoke>', 'exec'), namespace)
        callback = namespace['make_callback']()
        callback(True)
        self.assertEqual(saved, [])
        while queued:
            milliseconds, capture = queued.pop(0)
            self.assertEqual(milliseconds, 1000)
            capture()
        callback(True)  # Repeated load notifications cannot overwrite the report.
        return json.loads(report.read_text()), selections, saved, exits

    def test_native_smoke_captures_both_themes_and_restores_initial_selection(self):
        with tempfile.TemporaryDirectory() as raw:
            packet, selections, saved, exits = self.run_smoke(Path(raw))
        self.assertTrue(packet['ok'])
        self.assertTrue(packet['themes_verified'])
        self.assertEqual(packet['theme_captures'], {'light': True, 'dark': True})
        self.assertEqual(selections, ['light', 'dark', 'system'])
        self.assertEqual({Path(path).name for path in saved},
                         {'windows-native-smoke.png', 'windows-native-smoke-light.png',
                          'windows-native-smoke-dark.png'})
        self.assertEqual(exits, [0])

    def test_failed_theme_selection_or_screenshot_fails_the_smoke(self):
        for changes in ({'rejected_theme': 'dark'}, {'save_succeeds': False}):
            with tempfile.TemporaryDirectory() as raw:
                packet, selections, _, exits = self.run_smoke(Path(raw), **changes)
            self.assertFalse(packet['ok'])
            self.assertEqual(exits, [2])
            self.assertEqual(selections[-1], 'system')


if __name__ == '__main__':
    unittest.main()
