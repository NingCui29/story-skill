"""Windows desktop host. The bundled backend runs in its own Python process."""
from __future__ import annotations

import json
import argparse
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit


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
    from PySide6.QtCore import QUrl, QTimer
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import (QApplication, QMainWindow, QTabWidget,
                                   QMessageBox, QFileDialog, QToolBar)
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView

    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test-report', type=Path)
    parser.add_argument('--state-dir', type=Path)
    parser.add_argument('--port', type=int, default=8765)
    options = parser.parse_args()
    if options.self_test_report and not options.state_dir:
        parser.error('Self-test requires an isolated --state-dir.')
    app = QApplication([sys.argv[0]])
    app.setApplicationName('写作工作台')
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
            self.tabs = QTabWidget()
            self.tabs.setTabsClosable(True)
            self.tabs.tabCloseRequested.connect(self.close_tab)
            self.setCentralWidget(self.tabs)
            toolbar = QToolBar('工作台', self)
            self.addToolBar(toolbar)
            toolbar.addAction('书架', lambda: self.tabs.setCurrentIndex(0))
            toolbar.addAction('复制作品目录', self.copy_directory)

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
        def completed(ok):
            def checked(visible):
                passed = ok and visible is True
                options.self_test_report.write_text(json.dumps({
                    'ok': passed, 'native_window': window.isVisible(),
                    'shelf_rendered': visible is True, 'title': first.title(),
                }, ensure_ascii=False), encoding='utf-8')
                if passed:
                    first.grab().save(str(options.self_test_report.with_suffix('.png')))
                app.exit(0 if passed else 2)
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
