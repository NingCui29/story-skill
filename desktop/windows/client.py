"""Windows desktop host. The bundled backend runs in its own Python process."""
from __future__ import annotations

import json
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import zipfile


THEME_CHOICES = ('system', 'light', 'dark')


def page_key(value):
    """Compare controlled pages without storing or changing their fragments."""
    if not local_url(value):
        return None
    url = urlsplit(value)
    if url.query or not (url.path == '/' or re.fullmatch(r'/[A-Za-z0-9_-]{20,128}/', url.path)):
        return None
    return url.port, url.path


def close_state_script(include_text=False):
    """Read all editor buffers, including files hidden by the active document."""
    return '''(() => {
        if(document.getElementById('library-heading'))return {kind:'shelf',documents:[]};
        if(!document.getElementById('current-book-root') || typeof docs==='undefined' || !(docs instanceof Map))return null;
        if([...docs.values()].some(d=>!d || typeof d!=='object' || typeof d.editable!=='boolean'
            || typeof d.value!=='string' || (d.editable && typeof d.text!=='string')))return null;
        return {kind:'editor',documents:[...docs].map(([id,d])=>{
            const textbox=document.getElementById('text');
            const value=typeof active!=='undefined' && id===active && d.editing && typeof textbox?.value==='string'
                ?textbox.value:d.value;
            const dirty=!!(d?.editable && value!==undefined && value!==d.text);
            const pending=dirty || !!d?.needs_recovery;
            return {id:String(id),title:String(d?.title||'未命名文字'),path:String(d?.path||''),
                dirty,saving:!!d?.saving,needs_recovery:!!d?.needs_recovery,
                recovery_current:dirty && d.recovered===value,
                text:__INCLUDE_TEXT__ && pending && typeof value==='string'?value:null};
        })};
    })()'''.replace('__INCLUDE_TEXT__', 'true' if include_text else 'false')


def checked_close_state(value, include_text=False):
    if (not isinstance(value, dict) or value.get('kind') not in ('shelf', 'editor')
            or not isinstance(value.get('documents'), list) or len(value['documents']) > 10000):
        return None
    documents = value['documents']
    if value['kind'] == 'shelf' and documents:
        return None
    for document in documents:
        if (not isinstance(document, dict)
                or any(not isinstance(document.get(key), str) for key in ('id', 'title', 'path'))
                or any(type(document.get(key)) is not bool for key in (
                    'dirty', 'saving', 'needs_recovery', 'recovery_current'))
                or (include_text and (document['dirty'] or document['needs_recovery'])
                    and not isinstance(document.get('text'), str))):
            return None
    return {'kind': value['kind'], 'documents': documents,
            'pending': [document for document in documents if document['dirty'] or document['needs_recovery']],
            'saving': any(document['saving'] for document in documents)}


def export_pending_buffers(destination, states):
    """Save explicit close-time exports atomically, never into a source file."""
    documents = []
    for state in states:
        state = checked_close_state(state, include_text=True)
        if state is None or state['saving']:
            raise ValueError('无法确认全部文字已读取；请返回作品保存。')
        documents.extend(state['pending'])
    if not documents:
        raise ValueError('没有读取到待保存文字；请返回作品核对。')
    destination = Path(destination)
    if destination.suffix.lower() != '.zip':
        raise ValueError('请选择 ZIP 文件作为未保存文字导出位置。')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix='.story-export-', suffix='.zip',
                                         dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                manifest = []
                for index, document in enumerate(documents, 1):
                    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', document['title']).strip(' .')[:80]
                    name = f'{index:03d}-{title or "未命名文字"}.txt'
                    archive.writestr(name, document['text'].encode('utf-8'))
                    manifest.append({key: document[key] for key in (
                        'id', 'title', 'path', 'dirty', 'needs_recovery', 'recovery_current')})
                    manifest[-1]['export_file'] = name
                archive.writestr('导出说明.json', json.dumps({
                    'version': 1, 'status': 'unsaved_buffer_export',
                    'note': '这是关闭前导出的待保存文字，尚未保存为候选或正式采用。原作品文件未改动。',
                    'documents': manifest,
                }, ensure_ascii=False, indent=2).encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def theme_from_cookie(cookie):
    """Persist only a harmless preference from the local workbench cookie."""
    if (bytes(cookie.name()) != b'story_workbench_theme'
            or cookie.domain() not in ('127.0.0.1', '.127.0.0.1') or cookie.path() != '/'):
        return None
    try:
        value = bytes(cookie.value()).decode('ascii')
    except UnicodeDecodeError:
        return None
    return value if value in THEME_CHOICES else None


def theme_seed_script(value):
    value = value if value in THEME_CHOICES else 'system'
    return '''(() => {
        if(location.protocol!=='http:' || location.hostname!=='127.0.0.1')return;
        const value=__THEME__;
        document.cookie='story_workbench_theme='+value+'; Path=/; SameSite=Strict';
        try{localStorage.setItem('story-workbench-theme',value);}catch{}
    })();'''.replace('__THEME__', json.dumps(value))


def resources():
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent


def local_url(value):
    try:
        url = urlsplit(value)
        return (url.scheme == 'http' and url.hostname == '127.0.0.1'
                and url.port is not None and 1 <= url.port <= 65535
                and url.username is None and url.password is None)
    except ValueError:
        return False


def backend_command(root):
    return [str(root / 'runtime/python.exe'), '-s', '-B',
            str(root / 'backend_runner.py')]


def start_backend(root, arguments=()):
    environment = os.environ.copy()
    for name in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONEXECUTABLE'):
        environment.pop(name, None)
    environment.update(PYTHONNOUSERSITE='1', PYTHONUTF8='1')
    result = subprocess.run([*backend_command(root), *arguments], capture_output=True, text=True,
                            encoding='utf-8', env=environment, timeout=25,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        packet = json.loads(result.stdout)
    except ValueError as error:
        raise RuntimeError('工作台启动失败，请检查安装目录是否完整。') from error
    if result.returncode or packet.get('ok') is not True:
        raise RuntimeError(packet.get('message', '工作台启动失败。'))
    if not local_url(packet.get('url', '')):
        raise RuntimeError('工作台返回的本机地址无效。')
    return packet


def main():
    from PySide6.QtCore import QSettings, QUrl, QTimer
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
    from PySide6.QtWidgets import (QApplication, QMainWindow, QTabWidget,
                                   QMessageBox, QFileDialog, QToolBar)
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineScript
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from workbench_updates import (CLIENT_VERSION, LATEST_RELEASE_API, MAX_RESPONSE_BYTES,
                                   UpdateResult, evaluate_release_response, official_release_url)

    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test-report', type=Path)
    parser.add_argument('--state-dir', type=Path)
    parser.add_argument('--port', type=int, default=8765)
    options = parser.parse_args()
    if options.self_test_report and not options.state_dir:
        parser.error('Self-test requires an isolated --state-dir.')
    app = QApplication([sys.argv[0]])
    app.setApplicationName('写作工作台')
    app.setApplicationVersion(CLIENT_VERSION)
    if options.self_test_report:
        options.state_dir.mkdir(parents=True, exist_ok=True)
        appearance_settings = QSettings(str(options.state_dir / 'desktop-preferences.ini'),
                                        QSettings.Format.IniFormat)
    else:
        appearance_settings = QSettings('StorySkill', 'StoryWorkbench')
    try:
        arguments = ['--port', str(options.port)]
        if options.state_dir:
            arguments.extend(['--state-dir', str(options.state_dir)])
        packet = start_backend(resources(), arguments)
    except Exception as error:
        if options.self_test_report:
            options.self_test_report.write_text(json.dumps({'ok': False, 'error': str(error)}), encoding='utf-8')
            return 2
        QMessageBox.critical(None, '写作工作台', str(error))
        return 2

    class Page(QWebEnginePage):
        def __init__(self, profile, view):
            super().__init__(profile, view)
            self.view = view

        def acceptNavigationRequest(self, url, kind, main_frame):
            address = url.toString()
            if address == 'about:blank':
                return self.view.entry_key is None
            key = page_key(address)
            if key is not None and main_frame:
                if self.view.navigation_approved == address:
                    self.view.navigation_approved = None
                    return True
                if window.close_pending:
                    return False
                existing = window.find_page(address, exclude=self.view)
                if existing is not None:
                    window.tabs.setCurrentWidget(existing)
                    if self.view.entry_key is None:
                        QTimer.singleShot(0, lambda: window.remove_page(self.view))
                    return False
                if self.view.entry_key is not None and self.view.entry_key != key:
                    # A main-frame link cannot replace an editor's hidden buffers.
                    window.add_page(address)
                    return False
                if self.view.entry_key == key and self.view.load_complete:
                    def checked(allowed):
                        if allowed:
                            self.view.navigation_approved = address
                            self.view.setUrl(url)
                    window.request_close([self.view], '重新载入', checked)
                    return False
                self.view.entry_key = key
                return True
            if main_frame and url.scheme() in ('http', 'https'):
                if not local_url(address):
                    QDesktopServices.openUrl(url)
            if main_frame and self.view.entry_key is None:
                QTimer.singleShot(0, lambda: window.remove_page(self.view))
            return False

    class View(QWebEngineView):
        def createWindow(self, kind):
            return None if window.close_pending else window.add_page()

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle('写作工作台')
            self.resize(1280, 860)
            # An off-the-record shared profile keeps capability URLs off disk.
            self.profile = QWebEngineProfile(self)
            self.profile.downloadRequested.connect(self.download)
            self.theme_settings = appearance_settings
            self.theme_script = None
            self.set_theme_seed(self.theme_settings.value('appearance/theme', 'system'))
            self.profile.cookieStore().cookieAdded.connect(self.remember_theme_cookie)
            self.tabs = QTabWidget()
            self.tabs.setTabsClosable(True)
            self.tabs.tabCloseRequested.connect(self.close_tab)
            self.setCentralWidget(self.tabs)
            self.close_pending = False
            self.close_approved = False
            toolbar = QToolBar('工作台', self)
            self.addToolBar(toolbar)
            toolbar.addAction('书架', lambda: self.tabs.setCurrentIndex(0))
            toolbar.addAction('复制作品目录', self.copy_directory)
            self.update_manager = QNetworkAccessManager(self)
            self.update_reply = None
            self.update_action = toolbar.addAction('检查更新', self.check_for_updates)

        def set_theme_seed(self, value):
            value = value if value in THEME_CHOICES else 'system'
            if self.theme_script is not None:
                self.profile.scripts().remove(self.theme_script)
            script = QWebEngineScript()
            script.setName('StoryWorkbench.ThemePreference')
            script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            script.setRunsOnSubFrames(False)
            script.setSourceCode(theme_seed_script(value))
            self.profile.scripts().insert(script)
            self.theme_script = script
            self.theme_preference = value

        def remember_theme_cookie(self, cookie):
            value = theme_from_cookie(cookie)
            if value is None or value == self.theme_preference:
                return
            self.theme_settings.setValue('appearance/theme', value)
            self.theme_settings.sync()
            self.set_theme_seed(value)

        def check_for_updates(self):
            if self.update_reply is not None:
                return
            self.update_action.setEnabled(False)
            self.statusBar().showMessage('正在检查 Windows 工作台更新…')
            request = QNetworkRequest(QUrl(LATEST_RELEASE_API))
            request.setRawHeader(b'Accept', b'application/vnd.github+json')
            request.setRawHeader(b'X-GitHub-Api-Version', b'2022-11-28')
            request.setRawHeader(b'User-Agent', ('StoryWorkbench/' + CLIENT_VERSION).encode('ascii'))
            request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                                 QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
            self.update_buffer = bytearray()
            self.update_finishing = False
            self.update_timed_out = False
            self.update_too_large = False
            self.update_reply = self.update_manager.get(request)
            self.update_timer = QTimer(self.update_reply)
            self.update_timer.setSingleShot(True)
            self.update_timer.timeout.connect(self.update_timeout)
            self.update_reply.readyRead.connect(self.read_update_response)
            self.update_reply.finished.connect(self.finish_update_check)
            self.update_timer.start(15000)

        def update_timeout(self):
            if self.update_reply is not None:
                self.update_timed_out = True
                self.update_reply.abort()

        def read_update_response(self):
            if self.update_reply is None or self.update_too_large:
                return
            self.update_buffer.extend(bytes(self.update_reply.readAll()))
            if len(self.update_buffer) > MAX_RESPONSE_BYTES:
                self.update_too_large = True
                self.update_buffer.clear()
                self.update_reply.abort()

        def finish_update_check(self):
            if self.update_reply is None or self.update_finishing:
                return
            self.update_finishing = True
            self.update_timer.stop()
            self.read_update_response()
            reply = self.update_reply
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if self.update_timed_out:
                result = UpdateResult('error', '更新检查超时，请检查网络后重试。')
            elif self.update_too_large:
                result = UpdateResult('error', '更新信息超过大小限制，请稍后重试。')
            elif (reply.error() != QNetworkReply.NetworkError.NoError
                  and status not in (403, 404, 429)):
                result = UpdateResult('error', '无法连接 GitHub，请检查网络后重试。')
            else:
                result = evaluate_release_response(status, bytes(self.update_buffer))
            self.update_reply = None
            self.update_finishing = False
            reply.deleteLater()
            self.update_action.setEnabled(True)
            self.statusBar().clearMessage()
            if result.state != 'available':
                QMessageBox.information(self, '检查更新', result.message)
                return
            dialog = QMessageBox(self)
            dialog.setWindowTitle('发现工作台更新')
            dialog.setText(result.message)
            download_button = dialog.addButton('打开下载链接', QMessageBox.ButtonRole.AcceptRole)
            dialog.addButton(QMessageBox.StandardButton.Close)
            dialog.exec()
            if (dialog.clickedButton() == download_button
                    and official_release_url(result.download_url, download=True)):
                QDesktopServices.openUrl(QUrl(result.download_url))

        def add_page(self, address=None):
            existing = self.find_page(address) if address else None
            if existing is not None:
                self.tabs.setCurrentWidget(existing)
                return existing
            view = View(self.tabs)
            view.entry_key = None
            view.navigation_approved = None
            view.load_generation = 0
            view.load_complete = False
            view.setPage(Page(self.profile, view))
            index = self.tabs.addTab(view, '工作台')
            self.tabs.setCurrentIndex(index)
            view.titleChanged.connect(lambda title: self.tabs.setTabText(
                self.tabs.indexOf(view), title[:32] or '工作台'))
            def started():
                view.load_generation += 1
                view.load_complete = False
            view.loadStarted.connect(started)
            view.loadFinished.connect(lambda ok: setattr(view, 'load_complete', ok is True))
            if address:
                view.setUrl(QUrl(address))
            return view

        def find_page(self, address, exclude=None):
            key = page_key(address)
            if key is None:
                return None
            return next((self.tabs.widget(index) for index in range(self.tabs.count())
                         if self.tabs.widget(index) is not exclude
                         and self.tabs.widget(index).entry_key == key), None)

        def remove_page(self, view):
            index = self.tabs.indexOf(view)
            if index <= 0:
                return
            self.tabs.removeTab(index)
            view.deleteLater()

        def probe_close_state(self, view, completion, include_text=False):
            # A rejected popup has never loaded a book and owns no editor buffer.
            if view.entry_key is None and view.url().toString() in ('', 'about:blank'):
                completion(checked_close_state({'kind': 'shelf', 'documents': []}))
                return
            if not view.load_complete or page_key(view.url().toString()) != view.entry_key:
                completion(None)
                return
            generation = view.load_generation
            timer = QTimer(self)
            timer.setSingleShot(True)
            finished = False

            def finish(value):
                nonlocal finished
                if finished:
                    return
                finished = True
                timer.stop()
                timer.deleteLater()
                if (self.tabs.indexOf(view) < 0 or not view.load_complete
                        or generation != view.load_generation):
                    value = None
                completion(checked_close_state(value, include_text=include_text))

            timer.timeout.connect(lambda: finish(None))
            timer.start(8000)
            view.page().runJavaScript(close_state_script(include_text), finish)

        def request_close(self, views, action, completion):
            if self.close_pending:
                return
            self.close_pending = True
            # Prevent new input between an asynchronous probe and the decision.
            self.tabs.setEnabled(False)
            states = []

            def finish(allowed, selected=None):
                self.tabs.setEnabled(True)
                self.close_pending = False
                if selected is not None and self.tabs.indexOf(selected) >= 0:
                    self.tabs.setCurrentWidget(selected)
                completion(allowed)

            def blocked(view, title, message):
                self.tabs.setCurrentWidget(view)
                QMessageBox.warning(self, title, message)
                finish(False, view)

            def check(index, include_text=False):
                if index == len(views):
                    if include_text:
                        write_export()
                    else:
                        decide()
                    return
                view = views[index]

                def checked(state):
                    if state is None:
                        blocked(view, '暂时无法核对编辑状态',
                                '页面尚未载入或未能返回完整编辑状态。为保留文字，本次' + action + '已取消。请返回作品检查。')
                        return
                    if state['saving']:
                        blocked(view, '候选稿正在保存', '请等待保存完成后再' + action + '，当前页面和文字仍保留。')
                        return
                    states.append(state)
                    check(index + 1, include_text)
                self.probe_close_state(view, checked, include_text)

            def decide():
                pending = [(view, state) for view, state in zip(views, states) if state['pending']]
                if not pending:
                    finish(True)
                    return
                selected = pending[0][0]
                self.tabs.setCurrentWidget(selected)
                count = sum(len(state['pending']) for _, state in pending)
                dialog = QMessageBox(self)
                dialog.setWindowTitle('还有待保存文字')
                dialog.setText(f'{len(pending)} 个作品页面中有 {count} 份待保存文字。')
                dialog.setInformativeText('请返回保存候选稿，或先导出这些文字再' + action
                                          + '。自动恢复副本和导出文件都不等于已保存候选，也未正式采用。')
                back = dialog.addButton('返回作品保存', QMessageBox.ButtonRole.RejectRole)
                export = dialog.addButton('导出文字后' + action, QMessageBox.ButtonRole.AcceptRole)
                dialog.setDefaultButton(back)
                dialog.setEscapeButton(back)
                dialog.exec()
                if dialog.clickedButton() != export:
                    finish(False, selected)
                    return
                path, _ = QFileDialog.getSaveFileName(self, '导出全部待保存文字', '工作台待保存文字.zip', 'ZIP 文件 (*.zip)')
                if not path:
                    finish(False, selected)
                    return
                self.close_export_path = path
                states.clear()
                # Read the text after the user's decision; never export an old probe.
                check(0, True)

            def write_export():
                try:
                    export_pending_buffers(self.close_export_path, states)
                except (OSError, ValueError, zipfile.BadZipFile) as error:
                    blocked(views[0], '待保存文字尚未导出', str(error) + '；当前页面仍保留。')
                    return
                finish(True)

            check(0)

        def close_tab(self, index):
            if index <= 0:
                return
            view = self.tabs.widget(index)
            self.request_close([view], '关闭页面', lambda allowed: self.remove_page(view) if allowed else None)

        def closeEvent(self, event):
            if self.close_approved:
                self.close_approved = False
                event.accept()
                return
            event.ignore()
            views = [self.tabs.widget(index) for index in range(self.tabs.count())]

            def checked(allowed):
                if allowed:
                    self.close_approved = True
                    self.close()
            self.request_close(views, '退出工作台', checked)

        def copy_directory(self):
            folder = QFileDialog.getExistingDirectory(self, '选择作品目录')
            if folder:
                app.clipboard().setText(folder)
                self.statusBar().showMessage('目录已复制，可粘贴到书架的添加目录栏。', 8000)

        def download(self, request):
            path, _ = QFileDialog.getSaveFileName(self, '保存文件', request.downloadFileName())
            if not path:
                request.cancel()
                return
            destination = Path(path)
            request.setDownloadDirectory(str(destination.parent))
            request.setDownloadFileName(destination.name)
            request.accept()

    window = Window()
    first = window.add_page()
    if options.self_test_report:
        self_test_started = False

        def completed(ok):
            nonlocal self_test_started
            if self_test_started:
                return
            self_test_started = True

            def checked(visible):
                rendered = ok and visible is True
                captures = {}
                verified = {}
                base_capture = False

                def finish(restored=True):
                    themes_verified = (set(verified) == {'light', 'dark'}
                                       and all(verified.values()) and restored is True)
                    captured = base_capture and all(captures.values())
                    passed = rendered and captured and themes_verified
                    options.self_test_report.write_text(json.dumps({
                        'ok': passed, 'native_window': window.isVisible(),
                        'shelf_rendered': visible is True, 'title': first.title(),
                        'captured': captured, 'client_version': CLIENT_VERSION,
                        'update_check_available': window.update_action.isEnabled(),
                        'themes_verified': themes_verified, 'theme_captures': captures,
                    }, ensure_ascii=False), encoding='utf-8')
                    app.exit(0 if passed else 2)

                def select_script(theme):
                    return '''(() => {
                        const control=document.getElementById('workbench-theme');
                        if(!control || !Array.from(control.options).some(option=>option.value===__THEME__))return false;
                        control.value=__THEME__;
                        control.dispatchEvent(new Event('change',{bubbles:true}));
                        return control.value===__THEME__ && document.documentElement.getAttribute('data-theme-preference')===__THEME__
                            && (__THEME__==='system' || document.documentElement.getAttribute('data-theme')===__THEME__);
                    })()'''.replace('__THEME__', json.dumps(theme))

                def initial_preference(preference):
                    if not rendered or preference not in ('system', 'light', 'dark'):
                        finish(False)
                        return

                    def theme_step(index):
                        if index == 2:
                            first.page().runJavaScript(select_script(preference), finish)
                            return
                        theme = ('light', 'dark')[index]

                        def selected(applied):
                            verified[theme] = applied is True

                            def capture():
                                nonlocal base_capture
                                pixmap = first.grab()
                                target = options.self_test_report.with_name(
                                    options.self_test_report.stem + '-' + theme + '.png')
                                captures[theme] = bool(pixmap.save(str(target)))
                                if index == 0:
                                    base_capture = bool(pixmap.save(str(
                                        options.self_test_report.with_suffix('.png'))))
                                theme_step(index + 1)
                            # Theme mutation and loadFinished precede the
                            # composited WebEngine frame used by the screenshot.
                            QTimer.singleShot(1000, capture)
                        first.page().runJavaScript(select_script(theme), selected)
                    theme_step(0)

                first.page().runJavaScript("document.getElementById('workbench-theme')?.value || ''",
                                           initial_preference)
            first.page().runJavaScript("!!document.getElementById('library-heading')", checked)
        first.loadFinished.connect(completed)
        QTimer.singleShot(45000, lambda: app.exit(3))
    first.setUrl(QUrl(packet['url']))
    window.show()
    if packet.get('outdated'):
        QMessageBox.information(window, '书架版本提示', packet['message'])
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
