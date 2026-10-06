"""Start or reuse the confirmed local bookshelf for the native macOS client.

The parent app owns only this launcher. Bookshelf and editor processes remain
independent, including when this launcher receives SIGTERM or its app exits.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


DEFAULT_PORT = 8765
START_TIMEOUT = 12.0


class LauncherError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class LauncherArguments(argparse.ArgumentParser):
    def error(self, message):
        raise LauncherError('invalid_input', '客户端启动参数无效：' + message)


def load_story():
    script = Path(__file__).resolve().parent / 'Scripts/story.py'
    if not script.is_file():
        raise LauncherError('client_runtime_missing', '客户端内的工作台程序缺失，请重新安装客户端。')
    spec = importlib.util.spec_from_file_location('story_desktop_client', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, script


def ready_result(state, reused, log=None):
    result = {'ok': True, 'url': state['url'], 'reused': reused,
              'outdated': state.get('outdated') is True}
    if log is not None:
        result['log'] = str(log)
    if result['outdated']:
        result['message'] = '已连接原有书架。它使用较早代码；请先保留各作品页面的编辑，再手动重启服务以启用更新。'
    return result


def _launch_log(story, state_dir):
    directory_path = story.safe_path(state_dir, 'desktop-client')
    with story._pinned_directory(directory_path, create=True) as directory:
        name = 'launch-' + uuid.uuid4().hex + '.log'
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        fd = (os.open(name, flags, 0o600, dir_fd=directory.fd) if directory.fd is not None else
              os.open(directory_path / name, flags, 0o600))
        try:
            story._verify_bound_directory(directory)
            story.publish._bound_stat(story._BoundFile(directory, name))
            stream = os.fdopen(fd, 'wb', buffering=0)
        except BaseException:
            os.close(fd)
            raise
    return stream, directory_path / name


def _process_failure(story, log):
    # CLI errors are JSON on stderr. Logs never provide commands to execute.
    try:
        raw = story.workbench._author_read(log.parent, log.name, 16384)
        for line in reversed(raw.decode('utf-8', errors='replace').splitlines()):
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and item.get('ok') is False and isinstance(item.get('message'), str):
                return LauncherError(item.get('error', 'client_start_failed'), item['message'])
    except (OSError, story.StoryError):
        pass
    return LauncherError('client_start_failed', '书架未能启动，请查看客户端启动日志。')


def ensure_bookshelf(story, script, state_dir=None, port=DEFAULT_PORT,
                     timeout=START_TIMEOUT, poll_interval=0.1):
    """Use only the bookshelf's authenticated instance protocol and fixed CLI."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise LauncherError('invalid_input', '客户端书架端口必须是 1 至 65535 的整数。')
    state_dir = story.workbench._library_state_dir(state_dir)
    state = story.workbench._library_service_request(state_dir)
    if state.get('running') is True:
        return ready_result(state, True)
    if state.get('running') is not False:
        raise LauncherError('client_service_unconfirmed',
                            '原书架服务暂不可达，无法确认已经退出。请核对原服务；客户端不会结束或替换未确认的进程。')

    stream, log = _launch_log(story, state_dir)
    command = [sys.executable, '-B', str(script), 'workbench-library-serve',
               '--state-dir', str(state_dir), '--port', str(port)]
    environment = os.environ.copy()
    environment.pop('PYTHONHOME', None)
    environment.pop('PYTHONPATH', None)
    environment['PYTHONNOUSERSITE'] = '1'
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
                                   close_fds=True, start_new_session=True, env=environment)
    except OSError as error:
        raise LauncherError('client_start_failed', '无法运行客户端内的工作台程序：' + str(error)) from error
    finally:
        stream.close()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = story.workbench._library_service_request(state_dir)
        if state.get('running') is True:
            # A second client may have won the service lease. The authenticated
            # instance is safe to share regardless of which launcher created it.
            return ready_result(state, False, log)
        if process.poll() is not None:
            error = _process_failure(story, log)
            error.log = str(log)
            raise error
        time.sleep(poll_interval)
    error = LauncherError('client_start_pending',
                          '书架启动尚未确认。请稍后重试；客户端会保留可能仍在启动的服务。')
    error.log = str(log)
    raise error


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    try:
        parser = LauncherArguments(description='Launch the bundled Story Workbench bookshelf')
        parser.add_argument('--state-dir', help='Override the user bookshelf directory for isolated runs')
        parser.add_argument('--port', type=int, default=DEFAULT_PORT)
        args = parser.parse_args(argv)
        story, script = load_story()
        result = ensure_bookshelf(story, script, args.state_dir, args.port)
    except Exception as error:
        result = {'ok': False, 'message': str(error),
                  'code': getattr(error, 'code', 'client_start_failed')}
        if getattr(error, 'log', None):
            result['log'] = error.log
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if not result['ok']:
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
