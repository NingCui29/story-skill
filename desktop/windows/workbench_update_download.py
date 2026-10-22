"""Bounded, cancellable official desktop downloads; no Qt or manuscript access."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
from typing import NamedTuple
from urllib.error import URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from workbench_updates import (CLIENT_VERSION, MAX_CHECKSUM_BYTES, MAX_DOWNLOAD_BYTES,
                               official_release_url, version_components)


CHUNK_BYTES = 128 * 1024
SOCKET_TIMEOUT = 10
TOTAL_TIMEOUT = 30 * 60
CDN_HOSTS = frozenset({'release-assets.githubusercontent.com', 'objects.githubusercontent.com',
                       'github-releases.githubusercontent.com'})


class DownloadError(ValueError):
    pass


class DownloadCancelled(DownloadError):
    pass


class DownloadProgress(NamedTuple):
    stage: str
    received: int = 0
    total: int = 0
    speed: float = 0
    remaining: float | None = None


class DownloadedUpdate(NamedTuple):
    path: str
    sha256: str
    size: int
    version: str
    asset_name: str
    identity: tuple = ()


def permitted_transfer_url(value):
    """GitHub's exact repository entry point and explicit HTTPS asset CDN hosts."""
    if (not isinstance(value, str)
            or any(ord(character) < 33 or ord(character) == 127 for character in value)):
        return False
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or parsed.username is not None or parsed.password is not None
                or parsed.fragment or parsed.netloc != parsed.hostname):
            return False
        if parsed.hostname == 'github.com':
            return official_release_url(value, download=True)
        decoded = unquote(parsed.path)
        return (parsed.hostname in CDN_HOSTS
                and parsed.path.startswith(('/github-production-release-asset/',
                                            '/github-production-release-asset-2e65be/'))
                and '%' not in decoded and '\\' not in decoded
                and not any(part in ('.', '..') for part in decoded.split('/'))
                and not any(ord(character) < 33 or ord(character) == 127 for character in decoded)
                and all('/' not in unquote(part) for part in parsed.path.split('/')))
    except ValueError:
        return False


class OfficialRedirects(HTTPRedirectHandler):
    def __init__(self):
        super().__init__()
        self.redirect_count = 0

    def redirect_request(self, request, response, code, message, headers, new_url):
        self.redirect_count += 1
        if self.redirect_count > 5 or not permitted_transfer_url(new_url):
            raise DownloadError('下载重定向不在官方 HTTPS 资源范围内，已停止。')
        return super().redirect_request(request, response, code, message, headers, new_url)


def _check(cancel, clock, deadline):
    if cancel.is_set():
        raise DownloadCancelled('已取消下载，未完成的临时文件已清理。')
    if clock() >= deadline:
        raise DownloadError('下载超时，请检查网络后重试。')


def _validate_metadata(result):
    try:
        version_components(result.version)
        name = result.asset_name
        expected = rf'story-workbench-{re.escape(result.version)}-windows-(?:x86_64|x64)(?:-setup\.exe|\.zip)'
        if (result.state != 'available' or not isinstance(name, str)
                or re.fullmatch(expected, name, re.IGNORECASE) is None
                or not official_release_url(result.download_url, download=True)
                or unquote(urlsplit(result.download_url).path.split('/')[-1]) != name
                or type(result.asset_size) is not int or not 0 < result.asset_size <= MAX_DOWNLOAD_BYTES):
            raise ValueError
        if result.sha256:
            if not isinstance(result.sha256, str) or re.fullmatch(r'[0-9a-f]{64}', result.sha256) is None:
                raise ValueError
        elif (result.checksum_url != result.download_url + '.sha256'
              or not official_release_url(result.checksum_url, download=True)
              or type(result.checksum_size) is not int
              or not 0 < result.checksum_size <= MAX_CHECKSUM_BYTES):
            raise ValueError
    except (AttributeError, TypeError, ValueError) as error:
        raise DownloadError('缺少可验证的官方安装包信息，请重新检查更新。') from error


def _open(opener, url, expected_size):
    if not official_release_url(url, download=True):
        raise DownloadError('下载必须从官方发布链接开始。')
    # Each asset/sidecar has an independent, bounded redirect chain. No auth or
    # cookie headers are sent to either GitHub or its signed CDN URL.
    opener = opener or build_opener(OfficialRedirects())
    request = Request(url, headers={'User-Agent': 'StoryWorkbench/' + CLIENT_VERSION,
                                    'Accept': 'application/octet-stream', 'Accept-Encoding': 'identity'})
    response = opener.open(request, timeout=SOCKET_TIMEOUT)
    try:
        if not permitted_transfer_url(response.geturl()) or response.status != 200:
            raise DownloadError('官方资源未返回有效下载内容，请稍后重试。')
        encoding = response.headers.get('Content-Encoding', 'identity').lower()
        length = response.headers.get('Content-Length')
        if (encoding != 'identity' or (length is not None and
                (re.fullmatch(r'[0-9]+', str(length)) is None or int(length) != expected_size))):
            raise DownloadError('下载大小或编码与官方发布信息不符，已停止。')
    except BaseException:
        response.close()
        raise
    return response


def parse_checksum(data, asset_name):
    if not 0 < len(data) <= MAX_CHECKSUM_BYTES:
        raise DownloadError('SHA256 校验文件大小无效。')
    try:
        text = data.decode('ascii')
    except UnicodeDecodeError as error:
        raise DownloadError('SHA256 校验文件格式无效。') from error
    # Bind the checksum to this filename, rather than accepting a digest for
    # some other release artifact in a multi-file list.
    match = re.fullmatch(r'([0-9a-fA-F]{64}) [ *]' + re.escape(asset_name) + r'\r?\n?', text)
    if match is None:
        raise DownloadError('SHA256 校验文件与此安装包名称不符。')
    return match.group(1).lower()


def verify_downloaded(update, *, cancel=None, progress=None, clock=time.monotonic):
    """Re-read disk bytes before exposing or launching an installer."""
    cancel, progress = cancel or threading.Event(), progress or (lambda event: None)
    path = Path(update.path)
    if (path.name != update.asset_name or re.fullmatch(r'[0-9a-f]{64}', update.sha256) is None
            or type(update.size) is not int or not 0 < update.size <= MAX_DOWNLOAD_BYTES
            or not re.fullmatch(r'story-workbench-[0-9]+\.[0-9]+\.[0-9]+-windows-(?:x86_64|x64)(?:-setup\.exe|\.zip)',
                                update.asset_name, re.IGNORECASE)):
        raise DownloadError('安装包验证信息无效，请重新下载。')
    deadline = clock() + TOTAL_TIMEOUT
    _check(cancel, clock, deadline)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size != update.size:
        raise DownloadError('下载文件已改变，请重新下载。')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    digest, received = hashlib.sha256(), 0
    progress(DownloadProgress('verifying', 0, update.size))
    try:
        with os.fdopen(descriptor, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_size != before.st_size
                    or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
                raise DownloadError('下载文件已被替换，请重新下载。')
            while chunk := stream.read(CHUNK_BYTES):
                _check(cancel, clock, deadline)
                received += len(chunk)
                if received > update.size:
                    raise DownloadError('下载文件大小改变，请重新下载。')
                digest.update(chunk)
                progress(DownloadProgress('verifying', received, update.size))
            after_handle = os.fstat(stream.fileno())
        after_path = path.lstat()
        # CPython Windows lstat and fstat can use different ctime semantics;
        # compare each API's complete baseline separately, never cross-ctime.
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if (any(getattr(before, field) != getattr(after_path, field) for field in fields)
                or any(getattr(opened, field) != getattr(after_handle, field) for field in fields)
                or not stat.S_ISREG(after_path.st_mode)):
            raise DownloadError('下载文件在校验期间改变，请重新下载。')
        if received != update.size or digest.hexdigest() != update.sha256:
            raise DownloadError('SHA256 校验失败，文件不可安装，请重新下载。')
        _check(cancel, clock, deadline)
        return update._replace(identity=_identity(after_path))
    except BaseException:
        raise


def download_update(result, cache_directory, *, progress=None, cancel=None,
                    opener=None, clock=time.monotonic):
    """Download into a new private cache folder and delete partial files on failure."""
    _validate_metadata(result)
    cancel, progress = cancel or threading.Event(), progress or (lambda event: None)
    deadline = clock() + TOTAL_TIMEOUT
    directory = temporary = target = None
    succeeded = False
    try:
        _check(cancel, clock, deadline)
        digest = result.sha256
        if not digest:
            progress(DownloadProgress('checksum'))
            checksum = bytearray()
            with _open(opener, result.checksum_url, result.checksum_size) as response:
                while chunk := response.read(min(CHUNK_BYTES, MAX_CHECKSUM_BYTES + 1)):
                    _check(cancel, clock, deadline)
                    checksum.extend(chunk)
                    if len(checksum) > result.checksum_size or len(checksum) > MAX_CHECKSUM_BYTES:
                        raise DownloadError('SHA256 校验文件超出官方大小，已停止。')
            if len(checksum) != result.checksum_size:
                raise DownloadError('SHA256 校验文件下载不完整，请重试。')
            digest = parse_checksum(bytes(checksum), result.asset_name)
        _check(cancel, clock, deadline)
        cache = Path(cache_directory)
        cache.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix='verified-update-', dir=cache))
        temporary, target = directory / (result.asset_name + '.part'), directory / result.asset_name
        received, started, last_report = 0, clock(), 0
        progress(DownloadProgress('downloading', 0, result.asset_size))
        with _open(opener, result.download_url, result.asset_size) as response:
            with temporary.open('xb') as stream:
                while chunk := response.read(CHUNK_BYTES):
                    _check(cancel, clock, deadline)
                    received += len(chunk)
                    if received > result.asset_size:
                        raise DownloadError('下载内容超出官方安装包大小，已停止。')
                    stream.write(chunk)
                    now = clock()
                    if now - last_report >= 0.1 or received == result.asset_size:
                        speed = received / max(now - started, 0.001)
                        progress(DownloadProgress('downloading', received, result.asset_size,
                                                  speed, (result.asset_size - received) / speed))
                        last_report = now
                _check(cancel, clock, deadline)
                if received != result.asset_size:
                    raise DownloadError('安装包下载不完整，请重试。')
                stream.flush()
                os.fsync(stream.fileno())
        os.replace(temporary, target)
        update = DownloadedUpdate(str(target), digest, result.asset_size, result.version, result.asset_name)
        update = verify_downloaded(update, cancel=cancel, progress=progress, clock=clock)
        _check(cancel, clock, deadline)
        progress(DownloadProgress('ready', result.asset_size, result.asset_size))
        succeeded = True
        return update
    except (OSError, URLError) as error:
        if cancel.is_set():
            raise DownloadCancelled('已取消下载，未完成的临时文件已清理。') from error
        raise DownloadError('下载或保存失败，请检查网络、磁盘空间和权限后重试。') from error
    finally:
        # Only unlink these exact files we own. Never traverse or recursively
        # delete a user-selected directory, even if cancellation is interrupted.
        if directory is not None and not succeeded:
            for path in (temporary, target):
                if path is not None:
                    path.unlink(missing_ok=True)
            try:
                directory.rmdir()
            except OSError:
                pass


def _identity(value):
    return tuple(getattr(value, field) for field in (
        'st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def assert_verified_identity(update):
    """Check path metadata; executable handoff also requires a held write-deny lock."""
    value = Path(update.path).lstat()
    if (not update.identity or not stat.S_ISREG(value.st_mode)
            or _identity(value) != update.identity or Path(update.path).name != update.asset_name
            or not update.asset_name.lower().endswith('-setup.exe')):
        raise DownloadError('安装包在校验后发生改变，请重新下载并校验。')


class WindowsInstallerLock:
    """Hold a readable Win32 handle that denies writes, removal and replacement."""
    def __init__(self, path):
        if os.name != 'nt':
            raise DownloadError('启动安装程序需要 Windows 文件保护。')
        import ctypes
        from ctypes import wintypes
        import msvcrt

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        # GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING, OPEN_REPARSE_POINT.
        # Omitting FILE_SHARE_WRITE and FILE_SHARE_DELETE also rejects a writer
        # that already holds the file; no hash is trusted without this protection.
        handle = create(str(path), 0x80000000, 0x1, None, 3, 0x00200080, None)
        if handle == ctypes.c_void_p(-1).value:
            raise DownloadError('无法保护安装包，文件可能正被其他程序写入或移动；请关闭相关程序后重试。')
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            close(handle)
            raise
        try:
            self.stream = os.fdopen(descriptor, 'rb')
        except BaseException:
            os.close(descriptor)
            raise

    def stat(self):
        if self.stream is None:
            raise DownloadError('安装包保护已结束，请重新校验。')
        return os.fstat(self.stream.fileno())

    def close(self):
        stream, self.stream = self.stream, None
        if stream is not None:
            stream.close()


class PreparedInstaller:
    """An SHA256 receipt whose exact file remains locked through process creation."""
    def __init__(self, update, lock):
        self.update, self.lock = update, lock

    def assert_current(self):
        if self.lock is None:
            raise DownloadError('安装包保护已结束，请重新校验。')
        assert_verified_identity(self.update)
        locked = self.lock.stat()
        # fstat and lstat on Windows can report different ctime semantics. Bind
        # the handle using file identity and size; SHA256 was read while locked.
        if (not stat.S_ISREG(locked.st_mode)
                or (locked.st_dev, locked.st_ino, locked.st_size) != self.update.identity[:3]):
            raise DownloadError('安装包路径与受保护文件不一致，请重新下载。')

    def close(self):
        lock, self.lock = self.lock, None
        if lock is not None:
            lock.close()


def prepare_installer(update, *, cancel=None, progress=None, clock=time.monotonic, lock_factory=None):
    """Run after the save/export decision, off the GUI thread, and retain the lock."""
    cancel = cancel or threading.Event()
    if not update.asset_name.lower().endswith('-setup.exe'):
        raise DownloadError('便携包不能启动安装程序。')
    deadline = clock() + TOTAL_TIMEOUT
    _check(cancel, clock, deadline)
    lock = (lock_factory or WindowsInstallerLock)(update.path)
    prepared = None
    try:
        verified = verify_downloaded(update, cancel=cancel, progress=progress, clock=clock)
        prepared = PreparedInstaller(verified, lock)
        prepared.assert_current()
        _check(cancel, clock, deadline)
        return prepared
    except BaseException:
        if prepared is not None:
            prepared.close()
        else:
            lock.close()
        raise


def format_bytes(value):
    number = float(value)
    for unit in ('B', 'KiB', 'MiB', 'GiB'):
        if number < 1024 or unit == 'GiB':
            return f'{number:.1f} {unit}' if unit != 'B' else f'{int(number)} B'
        number /= 1024


def progress_text(event):
    detail = f'{format_bytes(event.received)} / {format_bytes(event.total)}'
    if event.stage == 'downloading' and event.speed > 0:
        seconds = max(0, int(event.remaining or 0))
        remaining = f'{seconds // 60} 分 {seconds % 60} 秒' if seconds >= 60 else f'{seconds} 秒'
        detail += f' · {format_bytes(event.speed)}/秒 · 预计剩余 {remaining}'
    return detail
