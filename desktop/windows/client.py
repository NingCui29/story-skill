"""Windows desktop host. The bundled backend runs in its own Python process."""
from __future__ import annotations

import json
import argparse
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit


THEME_CHOICES = ('system', 'light', 'dark')


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
        def acceptNavigationRequest(self, url, kind, main_frame):
            if local_url(url.toString()) or url.toString() == 'about:blank':
                return True
            if main_frame and url.scheme() in ('http', 'https'):
                QDesktopServices.openUrl(url)
            return False

    class View(QWebEngineView):
        def createWindow(self, kind):
            return window.add_page()

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
            view = View(self.tabs)
            view.setPage(Page(self.profile, view))
            index = self.tabs.addTab(view, '工作台')
            self.tabs.setCurrentIndex(index)
            view.titleChanged.connect(lambda title: self.tabs.setTabText(
                self.tabs.indexOf(view), title[:32] or '工作台'))
            if address:
                view.setUrl(QUrl(address))
            return view

        def confirm_close(self):
            return QMessageBox.question(
                self, '关闭页面', '请先保存页面中的修改。确认关闭？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

        def close_tab(self, index):
            if index == 0 or not self.confirm_close():
                return
            view = self.tabs.widget(index)
            self.tabs.removeTab(index)
            view.deleteLater()

        def closeEvent(self, event):
            if self.confirm_close():
                event.accept()
            else:
                event.ignore()

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
