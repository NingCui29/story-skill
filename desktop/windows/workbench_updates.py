"""Parse public desktop releases without Qt or network side effects."""
from __future__ import annotations

import json
import re
from typing import NamedTuple
from urllib.parse import unquote, urlsplit


CLIENT_VERSION = '0.1.8'
REPOSITORY = 'NingCui29/story-skill'
LATEST_RELEASE_API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024
MAX_CHECKSUM_BYTES = 16 * 1024
_VERSION = r'([0-9]+\.[0-9]+\.[0-9]+)'
_WINDOWS_ASSET = re.compile(
    r'^story-workbench-' + _VERSION + r'-windows-(?:x86_64|x64)(-setup\.exe|\.zip)$',
    re.IGNORECASE,
)


class UpdateResult(NamedTuple):
    state: str
    message: str
    version: str = ''
    download_url: str = ''
    asset_name: str = ''
    asset_size: int = 0
    sha256: str = ''
    checksum_url: str = ''
    checksum_size: int = 0


def version_components(value):
    """Compare desktop numeric versions, never a skill/release tag."""
    if not isinstance(value, str) or re.fullmatch(_VERSION, value) is None:
        raise ValueError('Invalid desktop version')
    return tuple(int(part) for part in value.split('.'))


def official_release_url(value, *, download=False):
    """Only open HTTPS release links in the official repository."""
    if (not isinstance(value, str)
            or any(ord(character) < 33 or ord(character) == 127 for character in value)):
        return False
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or parsed.netloc != 'github.com'
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            return False
    except ValueError:
        return False
    prefix = '/' + REPOSITORY + '/releases/'
    if not parsed.path.startswith(prefix):
        return False
    parts = parsed.path[len(prefix):].split('/')
    required = 3 if download else 2
    if len(parts) != required or parts[0] != ('download' if download else 'tag'):
        return False
    return all(part and unquote(part) not in ('.', '..')
               and '/' not in unquote(part) and '\\' not in unquote(part)
               and not any(ord(character) < 32 for character in unquote(part))
               for part in parts[1:])


def evaluate_release_response(status, data, current_version=CLIENT_VERSION):
    """Return a visible result for a GitHub latest-stable-release response."""
    current = version_components(current_version)
    if status == 404:
        return UpdateResult('unavailable', '官方仓库尚未发布正式版本，暂时无法检查 Windows 工作台更新。')
    if status in (403, 429):
        return UpdateResult('error', 'GitHub 暂时拒绝了更新检查，可能达到请求限额。请稍后重试。')
    if status != 200:
        return UpdateResult('error', f'更新检查失败（HTTP {status or "未知"}），请稍后重试。')
    if len(data) > MAX_RESPONSE_BYTES:
        return UpdateResult('error', '更新信息超过大小限制，请稍后重试。')
    try:
        release = json.loads(data)
    except (ValueError, TypeError, UnicodeDecodeError):
        return UpdateResult('error', '更新信息格式无效，请稍后重试。')
    if (not isinstance(release, dict) or release.get('draft') is not False
            or release.get('prerelease') is not False
            or not official_release_url(release.get('html_url'))
            or not isinstance(release.get('assets'), list)):
        return UpdateResult('error', '无法验证正式版本信息，请稍后重试。')
    candidates = []
    for asset in release['assets']:
        if not isinstance(asset, dict) or asset.get('state') != 'uploaded':
            continue
        name = asset.get('name', '')
        match = _WINDOWS_ASSET.fullmatch(name) if isinstance(name, str) else None
        url = asset.get('browser_download_url')
        if not match:
            continue
        if not official_release_url(url, download=True):
            return UpdateResult('error', '无法验证 Windows 工作台安装包下载信息，请稍后重试。')
        # The URL filename and release tag must belong to this exact asset.
        release_tag = unquote(urlsplit(release['html_url']).path.split('/')[-1])
        url_parts = urlsplit(url).path.split('/')
        if unquote(url_parts[-1]) != name or unquote(url_parts[-2]) != release_tag:
            return UpdateResult('error', '无法验证 Windows 工作台安装包下载信息，请稍后重试。')
        version = match.group(1)
        candidates.append((version_components(version), match.group(2).lower() == '-setup.exe',
                           version, url, asset))
    if not candidates:
        return UpdateResult('unavailable',
                            f'最新正式发布中没有可验证版本的 Windows x64 工作台安装包。当前客户端：{current_version}。')
    numeric, _, version, url, asset = max(candidates, key=lambda item: item[:4])
    if numeric > current:
        size = asset.get('size')
        if type(size) is not int or not 0 < size <= MAX_DOWNLOAD_BYTES:
            return UpdateResult('error', '官方安装包大小无效或超出 1 GiB 限制，无法安全下载。')
        digest = asset.get('digest')
        checksum_url, checksum_size, sha256 = '', 0, ''
        if digest is not None:
            if not isinstance(digest, str) or re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest) is None:
                return UpdateResult('error', '官方安装包 SHA256 信息无效，无法安全下载。')
            sha256 = digest.split(':')[1].lower()
        else:
            companions = [item for item in release['assets'] if isinstance(item, dict)
                          and item.get('name') == asset['name'] + '.sha256']
            if len(companions) != 1:
                return UpdateResult('error', '官方发布缺少此安装包的 SHA256 校验信息，无法安全下载。')
            companion = companions[0]
            checksum_url, checksum_size = companion.get('browser_download_url'), companion.get('size')
            if (companion.get('state') != 'uploaded' or type(checksum_size) is not int
                    or not 0 < checksum_size <= MAX_CHECKSUM_BYTES
                    or checksum_url != url + '.sha256'
                    or not official_release_url(checksum_url, download=True)):
                return UpdateResult('error', '无法验证此安装包的 SHA256 校验文件，无法安全下载。')
        return UpdateResult('available',
                            f'发现 Windows 工作台 {version}，当前版本 {current_version}。可在应用内下载并校验。',
                            version, url, asset['name'], size, sha256, checksum_url, checksum_size)
    if numeric == current:
        return UpdateResult('current', f'当前 Windows 工作台 {current_version} 已是最新正式版本。', version)
    return UpdateResult('ahead',
                        f'当前 Windows 工作台 {current_version} 高于最新正式安装包 {version}，无需降级。', version)
