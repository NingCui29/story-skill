"""Exercise real Windows close controls without requiring a graphical Qt host."""
import ast
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'desktop/windows/client.py'
SPEC = importlib.util.spec_from_file_location('windows_safety_client', SOURCE)
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)
EDITOR = 'http://127.0.0.1:49152/' + 'a' * 32 + '/'
OTHER = 'http://127.0.0.1:49153/' + 'b' * 32 + '/'
SHELF = 'http://127.0.0.1:8765/'


def document(**changes):
    value = dict(id='file:原稿.md', title='待保存文字', path='原稿.md', dirty=False,
                 saving=False, needs_recovery=False, recovery_current=False, text=None)
    value.update(changes)
    return value


def state(*documents, kind='editor', busy=False, shelf_pending=False):
    return dict(kind=kind, busy=busy, shelf_pending=shelf_pending, documents=list(documents))


class CloseStateTests(unittest.TestCase):
    def test_page_identity_reuses_only_exact_token_routes_and_port(self):
        self.assertEqual(client.page_key(EDITOR + '#file%3Ahello.md'), client.page_key(EDITOR))
        self.assertNotEqual(client.page_key(OTHER), client.page_key(EDITOR))
        self.assertEqual(client.page_key(SHELF), (8765, '/'))
        for value in (EDITOR + '?query=bad', EDITOR.replace('a' * 32, '%61' * 32),
                      EDITOR.replace('a' * 32, 'short'), 'http://localhost:8765/',
                      'https://127.0.0.1:8765/', 'about:blank', 'file:///book'):
            self.assertIsNone(client.page_key(value), value)

    def test_automatic_recovery_and_pending_save_are_never_treated_as_clean(self):
        value = client.checked_close_state(state(
            document(dirty=True, recovery_current=True), document(saving=True),
            document(needs_recovery=True)))
        self.assertEqual(len(value['pending']), 2)
        self.assertTrue(value['saving'])
        self.assertIsNone(client.checked_close_state(state(document(dirty=True)), include_text=True))
        for invalid in (None, {}, state(kind='remote'), state(document(dirty=1)),
                        state(document(), kind='shelf'), state({'dirty': False})):
            self.assertIsNone(client.checked_close_state(invalid))

    def test_real_script_reads_hidden_buffers_and_preserves_recovery_boundary(self):
        node = shutil.which('node')
        if not node:
            bundled = (Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')
            node = str(bundled) if bundled.is_file() else None
        if not node:
            self.skipTest('Node is unavailable')
        program = '''
const document={getElementById:id=>id==='current-book-root'?{}:null};
const docs=new Map([
 ['active',{title:'当前',editable:true,text:'已保存',value:'已保存'}],
 ['hidden',{title:'隐藏',path:'隐藏.md',editable:true,text:'原稿',value:'未保存文字',recovered:'未保存文字'}],
 ['pending',{title:'待核对',editable:true,text:'恢复文字',value:'恢复文字',needs_recovery:true}],
 ['saving',{title:'保存中',editable:true,text:'原稿',value:'原稿',saving:true}]
]);
console.log(JSON.stringify(eval(__SCRIPT__)));
'''.replace('__SCRIPT__', json.dumps(client.close_state_script(True)))
        result = subprocess.run([node, '-e', program], capture_output=True, text=True, check=True)
        checked = client.checked_close_state(json.loads(result.stdout), include_text=True)
        self.assertEqual([item['id'] for item in checked['pending']], ['hidden', 'pending'])
        self.assertEqual([item['text'] for item in checked['pending']], ['未保存文字', '恢复文字'])
        self.assertTrue(checked['pending'][0]['recovery_current'])
        self.assertTrue(checked['saving'])

        # A final IME/textarea change may precede the editor's input handler.
        # The close probe must export the visible characters as well.
        composed = program.replace(
            "id==='current-book-root'?{}:null", "id==='current-book-root'?{}:id==='text'?{value:'刚输入的文字'}:null"
        ).replace("const docs=new Map([", "const active='active';const docs=new Map([").replace(
            "title:'当前',editable:true", "title:'当前',editing:true,editable:true")
        result = subprocess.run([node, '-e', composed], capture_output=True, text=True, check=True)
        checked = client.checked_close_state(json.loads(result.stdout), include_text=True)
        self.assertEqual(checked['pending'][0]['text'], '刚输入的文字')

    def test_export_preserves_all_text_with_safe_unique_names_and_status(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            original = folder / '原稿.md'
            original.write_text('原稿仍在', encoding='utf-8')
            target = folder / '全部待保存文字.zip'
            target.write_bytes(b'old export')
            first = state(document(title='../../同名', path=str(original), dirty=True,
                                   recovery_current=True, text='第一份\n隐藏文字'))
            second = state(document(title='../../同名', needs_recovery=True, text='第二份恢复文字'))
            client.export_pending_buffers(target, [first, second])
            with zipfile.ZipFile(target) as archive:
                manifest = json.loads(archive.read('导出说明.json'))
                names = [item['export_file'] for item in manifest['documents']]
                self.assertEqual(len(set(names)), 2)
                self.assertTrue(all('/' not in name and '\\' not in name for name in names))
                self.assertEqual([archive.read(name).decode('utf-8') for name in names],
                                 ['第一份\n隐藏文字', '第二份恢复文字'])
                self.assertEqual(manifest['status'], 'unsaved_buffer_export')
            self.assertEqual(original.read_text(encoding='utf-8'), '原稿仍在')
            self.assertEqual(sorted(path.name for path in folder.iterdir()), ['全部待保存文字.zip', '原稿.md'])

    def test_failed_export_preserves_existing_destination_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            target = folder / '待保存.zip'
            target.write_bytes(b'original destination')
            with patch.object(client.os, 'replace', side_effect=PermissionError('destination locked')):
                with self.assertRaises(PermissionError):
                    client.export_pending_buffers(target, [state(document(dirty=True, text='文字'))])
            self.assertEqual(target.read_bytes(), b'original destination')
            self.assertEqual(list(folder.iterdir()), [target])
            for invalid in (state(document(saving=True, dirty=True, text='文字')),
                            state(document(dirty=True)), state(document())):
                with self.assertRaises(ValueError):
                    client.export_pending_buffers(target, [invalid])


class Timer:
    instances = []
    queued = []

    def __init__(self, owner):
        self.timeout = SimpleNamespace(connect=lambda callback: setattr(self, 'callback', callback))
        self.stopped = False
        self.deleted = False
        Timer.instances.append(self)

    def setSingleShot(self, value):
        pass

    def start(self, milliseconds):
        self.milliseconds = milliseconds

    def stop(self):
        self.stopped = True

    def deleteLater(self):
        self.deleted = True

    @staticmethod
    def singleShot(milliseconds, callback):
        Timer.queued.append(callback)


class View:
    def __init__(self, address):
        self.address = address
        self.entry_key = client.page_key(address)
        self.navigation_approved = None
        self.load_generation = 0
        self.load_complete = True
        self.scripts = []
        self.callbacks = []
        self.deleted = False

    def url(self):
        return SimpleNamespace(toString=lambda: self.address)

    def page(self):
        return self

    def runJavaScript(self, script, callback=None):
        self.scripts.append(script)
        if callback is not None:
            self.callbacks.append(callback)

    def reply(self, value):
        self.callbacks.pop(0)(value)

    def deleteLater(self):
        self.deleted = True

    def setUrl(self, url):
        self.address = url.toString()


class Tabs:
    def __init__(self, views):
        self.views = list(views)
        self.enabled = True
        self.selected = self.views[0]

    def setEnabled(self, value):
        self.enabled = value

    def setCurrentWidget(self, view):
        self.selected = view

    def count(self):
        return len(self.views)

    def widget(self, index):
        return self.views[index]

    def indexOf(self, view):
        return self.views.index(view) if view in self.views else -1

    def removeTab(self, index):
        self.views.pop(index)


class CloseControlsTests(unittest.TestCase):
    def make_window(self, views, *, export=False, path=''):
        tree = ast.parse(SOURCE.read_text())
        window = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == 'Window')
        names = {'find_page', 'open_document_target', 'remove_page', 'probe_close_state', 'request_close', 'close_tab', 'closeEvent'}
        window.body = [node for node in window.body if getattr(node, 'name', '') in names]
        window.bases = []
        warnings = []

        class Dialog:
            ButtonRole = SimpleNamespace(RejectRole=1, AcceptRole=2)

            def __init__(self, owner):
                self.buttons = []

            def setWindowTitle(self, value):
                pass

            def setText(self, value):
                pass

            def setInformativeText(self, value):
                self.detail = value

            def addButton(self, title, role):
                button = object()
                self.buttons.append(button)
                return button

            def setDefaultButton(self, value):
                self.default = value

            def setEscapeButton(self, value):
                self.escape = value

            def exec(self):
                assert self.default == self.buttons[0] == self.escape

            def clickedButton(self):
                return self.buttons[1 if export else 0]

            @staticmethod
            def warning(owner, title, message):
                warnings.append((title, message))

        namespace = {name: getattr(client, name) for name in (
            'page_key', 'editor_target_script', 'checked_close_state', 'close_state_script', 'export_pending_buffers', 'zipfile')}
        namespace.update(QTimer=Timer, QMessageBox=Dialog, QFileDialog=SimpleNamespace(
            getSaveFileName=lambda *args: (path, '')))
        exec(compile(ast.fix_missing_locations(ast.Module(body=[window], type_ignores=[])),
                     '<real Windows close controls>', 'exec'), namespace)
        instance = namespace['Window']()
        instance.close_pending = False
        instance.close_approved = False
        instance.tabs = Tabs(views)
        instance._test_namespace = namespace
        return instance, warnings

    def test_close_waits_for_all_tabs_with_input_locked_and_closes_clean_pages_silently(self):
        shelf, first, second = View(SHELF), View(EDITOR), View(OTHER)
        window, warnings = self.make_window([shelf, first, second])
        allowed = []
        window.request_close([shelf, first, second], '退出工作台', allowed.append)
        self.assertTrue(window.close_pending)
        self.assertFalse(window.tabs.enabled)
        self.assertEqual(allowed, [])
        shelf.reply(state(kind='shelf'))
        first.reply(state(document()))
        self.assertEqual(allowed, [])
        second.reply(state(document()))
        self.assertEqual(allowed, [True])
        self.assertTrue(window.tabs.enabled)
        self.assertFalse(window.close_pending)
        self.assertEqual(warnings, [])

    def test_hidden_dirty_or_recovery_buffer_returns_to_its_page(self):
        for changes in ({'dirty': True, 'recovery_current': True}, {'needs_recovery': True}):
            with self.subTest(changes=changes):
                shelf, first, second = View(SHELF), View(EDITOR), View(OTHER)
                window, warnings = self.make_window([shelf, first, second])
                allowed = []
                window.request_close([first, second], '退出工作台', allowed.append)
                first.reply(state(document()))
                second.reply(state(document(id='inactive', **changes)))
                self.assertEqual(allowed, [False])
                self.assertEqual(window.tabs.selected, second)
                self.assertFalse(second.deleted)
                self.assertEqual(warnings, [])

    def test_inflight_save_or_unavailable_state_always_cancels_close(self):
        for value, message in ((state(document(saving=True)), '正在保存'), (None, '无法核对')):
            with self.subTest(value=value):
                view = View(EDITOR)
                window, warnings = self.make_window([View(SHELF), view], export=True)
                allowed = []
                window.request_close([view], '关闭页面', allowed.append)
                view.reply(value)
                self.assertEqual(allowed, [False])
                self.assertIn(message, warnings[0][0])
                self.assertTrue(window.tabs.enabled)

    def test_rejected_blank_popup_has_no_buffers_and_can_close_without_javascript(self):
        blank = View('about:blank')
        blank.load_complete = False
        window, warnings = self.make_window([View(SHELF), blank])
        window.close_tab(1)
        self.assertTrue(blank.deleted)
        self.assertEqual(blank.scripts, [])
        self.assertEqual(warnings, [])

    def test_probe_timeout_and_late_javascript_reply_cannot_reauthorize_close(self):
        view = View(EDITOR)
        window, warnings = self.make_window([View(SHELF), view])
        allowed = []
        window.request_close([view], '关闭页面', allowed.append)
        timer = Timer.instances[-1]
        self.assertEqual(timer.milliseconds, 8000)
        timer.callback()
        view.reply(state(document()))
        self.assertEqual(allowed, [False])
        self.assertEqual(len(warnings), 1)
        self.assertTrue(timer.stopped and timer.deleted)

    def test_navigation_during_probe_is_unknown_and_never_clean(self):
        view = View(EDITOR)
        window, warnings = self.make_window([View(SHELF), view])
        allowed = []
        window.request_close([view], '关闭页面', allowed.append)
        view.load_generation += 1
        view.reply(state(document()))
        self.assertEqual(allowed, [False])
        self.assertIn('无法核对', warnings[0][0])

    def test_export_rereads_every_buffer_and_only_closes_after_atomic_write(self):
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / '待保存.zip'
            first, second = View(EDITOR), View(OTHER)
            window, warnings = self.make_window([View(SHELF), first, second], export=True, path=str(target))
            allowed = []
            window.request_close([first, second], '退出工作台', allowed.append)
            first.reply(state(document(dirty=True)))
            second.reply(state(document(needs_recovery=True)))
            self.assertFalse(target.exists())
            self.assertEqual(allowed, [])
            self.assertIn('text:false', first.scripts[0])
            self.assertIn('text:true', first.scripts[1])
            first.reply(state(document(dirty=True, text='再次读取的第一份')))
            second.reply(state(document(needs_recovery=True, text='再次读取的第二份')))
            self.assertEqual(allowed, [True])
            self.assertTrue(target.is_file())
            with zipfile.ZipFile(target) as archive:
                self.assertEqual(archive.read('001-待保存文字.txt').decode('utf-8'), '再次读取的第一份')
                self.assertEqual(archive.read('002-待保存文字.txt').decode('utf-8'), '再次读取的第二份')
            self.assertEqual(warnings, [])

    def test_cancelled_export_and_write_failure_keep_all_pages(self):
        for export_path in ('', '/unused.zip'):
            with self.subTest(path=export_path):
                view = View(EDITOR)
                window, warnings = self.make_window([View(SHELF), view], export=True, path=export_path)
                allowed = []
                window._test_namespace['export_pending_buffers'] = lambda *args: (_ for _ in ()).throw(PermissionError('不可写'))
                window.request_close([view], '关闭页面', allowed.append)
                view.reply(state(document(dirty=True)))
                if export_path:
                    view.reply(state(document(dirty=True, text='保留文字')))
                    self.assertIn('尚未导出', warnings[0][0])
                self.assertEqual(allowed, [False])
                self.assertFalse(view.deleted)
                self.assertIn(view, window.tabs.views)

    def test_tab_removal_uses_view_identity_after_async_check(self):
        shelf, other, selected = View(SHELF), View(OTHER), View(EDITOR)
        window, _ = self.make_window([shelf, other, selected])
        window.close_tab(2)
        window.tabs.removeTab(1)
        selected.reply(state(document()))
        self.assertEqual(window.tabs.views, [shelf])
        self.assertTrue(selected.deleted)
        window.close_tab(0)
        self.assertEqual(window.tabs.views, [shelf])

    def test_close_event_is_accepted_only_after_all_pages_are_checked(self):
        shelf, editor = View(SHELF), View(EDITOR)
        window, _ = self.make_window([shelf, editor])
        events = []

        def event():
            return SimpleNamespace(accept=lambda: events.append('accepted'), ignore=lambda: events.append('ignored'))

        window.close = lambda: window.closeEvent(event())
        window.closeEvent(event())
        self.assertEqual(events, ['ignored'])
        shelf.reply(state(kind='shelf'))
        editor.reply(state(document()))
        self.assertEqual(events, ['ignored', 'accepted'])
        self.assertFalse(window.close_approved)

    def make_page(self, window, view):
        tree = ast.parse(SOURCE.read_text())
        page = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == 'Page')
        page.body = [node for node in page.body if getattr(node, 'name', '') == 'acceptNavigationRequest']
        page.bases = []
        opened = []
        namespace = {'window': window, 'page_key': client.page_key, 'local_url': client.local_url,
                     'QTimer': Timer, 'QDesktopServices': SimpleNamespace(openUrl=opened.append)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[page], type_ignores=[])),
                     '<real Windows navigation policy>', 'exec'), namespace)
        instance = namespace['Page']()
        instance.view = view
        return instance, opened

    def url(self, address):
        return SimpleNamespace(toString=lambda: address, scheme=lambda: address.split(':')[0])

    def test_duplicate_popup_reuses_existing_editor_without_reloading_its_buffers(self):
        shelf, existing, placeholder = View(SHELF), View(EDITOR), View('about:blank')
        window, _ = self.make_window([shelf, existing, placeholder])
        page, _ = self.make_page(window, placeholder)
        self.assertFalse(page.acceptNavigationRequest(self.url(EDITOR + '#other-document'), None, True))
        self.assertEqual(window.tabs.selected, existing)
        self.assertEqual(existing.address, EDITOR)
        self.assertEqual(existing.scripts, [client.editor_target_script(EDITOR + '#other-document')])
        Timer.queued.pop(0)()
        self.assertTrue(placeholder.deleted)
        self.assertEqual(window.tabs.views, [shelf, existing])

    def test_editor_reload_uses_close_guard_and_navigation_cannot_replace_book(self):
        shelf, existing = View(SHELF), View(EDITOR)
        window, _ = self.make_window([shelf, existing])
        added = []
        window.add_page = added.append
        page, opened = self.make_page(window, existing)
        self.assertFalse(page.acceptNavigationRequest(self.url(OTHER), None, True))
        self.assertEqual(added, [OTHER])
        self.assertEqual(existing.address, EDITOR)
        self.assertFalse(page.acceptNavigationRequest(self.url(EDITOR), None, True))
        existing.reply(state(document(dirty=True)))
        self.assertIsNone(existing.navigation_approved)
        self.assertFalse(page.acceptNavigationRequest(self.url('about:blank'), None, True))
        self.assertFalse(page.acceptNavigationRequest(self.url('http://127.0.0.1:49152/api/stop'), None, True))
        self.assertEqual(opened, [])


if __name__ == '__main__':
    unittest.main()
