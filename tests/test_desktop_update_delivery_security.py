"""Exercise production update delivery with hostile synthetic streams only."""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.request import Request


ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / 'desktop/windows'
sys.path.insert(0, str(WINDOWS))
try:
    import workbench_updates as metadata
    spec = importlib.util.spec_from_file_location(
        'security_update_delivery', WINDOWS / 'workbench_update_download.py')
    delivery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(delivery)
finally:
    sys.path.pop(0)


NAME = 'story-workbench-0.2.0-windows-x86_64-setup.exe'
URL = 'https://github.com/NingCui29/story-skill/releases/download/v-test/' + NAME
CDN = 'https://release-assets.githubusercontent.com/github-production-release-asset/1/fixture?sig=test'
PAYLOAD = b'MZ synthetic update fixture\r\n' * 23_000


def package(payload=PAYLOAD, **changes):
    values = dict(state='available', message='synthetic', version='0.2.0',
                  download_url=URL, asset_name=NAME, asset_size=len(payload),
                  sha256=hashlib.sha256(payload).hexdigest())
    values.update(changes)
    return metadata.UpdateResult(**values)


class Response:
    def __init__(self, data, *, url=CDN, length=None, status=200, encoding='identity', fail_after=None):
        self.data, self.url, self.status = data, url, status
        self.headers = {'Content-Encoding': encoding}
        if length is not None:
            self.headers['Content-Length'] = str(length)
        self.fail_after, self.read_count, self.closed = fail_after, 0, False

    def geturl(self):
        return self.url

    def read(self, size):
        self.read_count += 1
        if self.fail_after is not None and self.read_count > self.fail_after:
            raise OSError('synthetic interrupted connection')
        result, self.data = self.data[:size], self.data[size:]
        return result

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        self.close()


class Opener:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def open(self, request, *, timeout):
        self.requests.append((request, timeout))
        return self.responses.pop(0)


class DeliverySecurityTests(unittest.TestCase):
    def assert_clean_cache(self, cache):
        self.assertEqual(list(cache.iterdir()), [])

    def test_cancel_during_transfer_cleans_only_owned_files_and_allows_retry(self):
        with tempfile.TemporaryDirectory() as raw:
            root, cancel, events = Path(raw), threading.Event(), []
            sentinel = root / 'keep-author-file.txt'
            sentinel.write_bytes(b'outside cache remains')
            cache = root / 'cache'

            def progress(event):
                events.append(event)
                if event.stage == 'downloading' and event.received:
                    cancel.set()

            with self.assertRaises(delivery.DownloadCancelled):
                delivery.download_update(package(), cache, cancel=cancel, progress=progress,
                                         opener=Opener(Response(PAYLOAD)))
            self.assertNotIn('ready', [event.stage for event in events])
            self.assert_clean_cache(cache)
            self.assertEqual(sentinel.read_bytes(), b'outside cache remains')
            retried = delivery.download_update(package(), cache, opener=Opener(Response(PAYLOAD)))
            self.assertEqual(Path(retried.path).read_bytes(), PAYLOAD)

    def test_disconnect_after_writing_a_chunk_cleans_partial_then_retry_succeeds(self):
        with tempfile.TemporaryDirectory() as raw:
            cache = Path(raw)
            broken = Response(PAYLOAD, fail_after=1)
            with self.assertRaises(delivery.DownloadError):
                delivery.download_update(package(), cache, opener=Opener(broken))
            self.assertTrue(broken.closed)
            self.assert_clean_cache(cache)
            result = delivery.download_update(package(), cache, opener=Opener(Response(PAYLOAD)))
            self.assertEqual(delivery.verify_downloaded(result), result)

    def test_no_ready_package_on_truncated_overlong_or_wrong_digest_stream(self):
        for payload, result in ((PAYLOAD[:-1], package()), (PAYLOAD + b'x', package()),
                                (PAYLOAD, package(sha256='0' * 64))):
            with self.subTest(length=len(payload), digest=result.sha256):
                with tempfile.TemporaryDirectory() as raw:
                    cache, events = Path(raw), []
                    with self.assertRaises(delivery.DownloadError):
                        delivery.download_update(result, cache, progress=events.append,
                                                 opener=Opener(Response(payload)))
                    self.assertNotIn('ready', [event.stage for event in events])
                    self.assert_clean_cache(cache)

    def test_final_response_requires_https_and_official_host_before_reading(self):
        for url in ('http://release-assets.githubusercontent.com/file',
                    'https://release-assets.githubusercontent.com.evil.test/file',
                    'https://user:pass@release-assets.githubusercontent.com/file',
                    'https://release-assets.githubusercontent.com:443/file',
                    'https://evil.test/file', 'file:///tmp/installer.exe'):
            with self.subTest(url=url), tempfile.TemporaryDirectory() as raw:
                response, cache = Response(PAYLOAD, url=url), Path(raw)
                with self.assertRaises(delivery.DownloadError):
                    delivery.download_update(package(), cache, opener=Opener(response))
                self.assertEqual(response.read_count, 0)
                self.assertTrue(response.closed)
                self.assert_clean_cache(cache)

    def test_redirect_handler_rejects_untrusted_targets_and_excessive_hops(self):
        original = Request(URL)
        for target in ('http://github.com/NingCui29/story-skill/releases/download/v-test/' + NAME,
                       'https://github.com/another/story-skill/releases/download/v-test/' + NAME,
                       'https://evil.test/installer.exe', CDN.replace('https:', 'http:')):
            with self.subTest(target=target), self.assertRaises(delivery.DownloadError):
                delivery.OfficialRedirects().redirect_request(original, None, 302, 'redirect', {}, target)
        redirects = delivery.OfficialRedirects()
        for _ in range(5):
            self.assertIsNotNone(redirects.redirect_request(original, None, 302, 'redirect', {}, CDN))
        with self.assertRaises(delivery.DownloadError):
            redirects.redirect_request(original, None, 302, 'redirect', {}, CDN)

    def test_response_status_encoding_and_length_fail_before_any_payload_read(self):
        for changes in ({'status': 206}, {'status': 302}, {'encoding': 'gzip'},
                        {'length': len(PAYLOAD) - 1}, {'length': '-1'}, {'length': 'NaN'}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as raw:
                response, cache = Response(PAYLOAD, **changes), Path(raw)
                with self.assertRaises(delivery.DownloadError):
                    delivery.download_update(package(), cache, opener=Opener(response))
                self.assertEqual(response.read_count, 0)
                self.assertTrue(response.closed)
                self.assert_clean_cache(cache)

    def test_checksum_sidecar_is_bound_to_exact_package_and_stays_bounded(self):
        digest = hashlib.sha256(PAYLOAD).hexdigest()
        for sidecar in ((digest + '  other.exe\n').encode(), digest.encode(),
                        (digest + '  ' + NAME + '\n' + digest + '  other.exe\n').encode()):
            result = package(sha256='', checksum_url=URL + '.sha256', checksum_size=len(sidecar))
            with self.subTest(sidecar=sidecar[:80]), tempfile.TemporaryDirectory() as raw:
                opener = Opener(Response(sidecar))
                with self.assertRaises(delivery.DownloadError):
                    delivery.download_update(result, raw, opener=opener)
                self.assertEqual(len(opener.requests), 1)
                self.assert_clean_cache(Path(raw))
        sidecar = (digest + '  ' + NAME + '\n').encode()
        result = package(sha256='', checksum_url=URL + '.sha256', checksum_size=len(sidecar))
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(delivery.DownloadError):
                delivery.download_update(result, raw, opener=Opener(Response(sidecar + b'x')))
            self.assert_clean_cache(Path(raw))
            opener = Opener(Response(sidecar), Response(PAYLOAD))
            installed = delivery.download_update(result, raw, opener=opener)
            self.assertEqual(installed.sha256, digest)
            self.assertEqual([request.full_url for request, _ in opener.requests],
                             [URL + '.sha256', URL])

    def test_metadata_mismatch_never_makes_a_network_request(self):
        for changes in ({'asset_name': '../' + NAME}, {'asset_size': True},
                        {'asset_size': delivery.MAX_DOWNLOAD_BYTES + 1},
                        {'version': '0.3.0'}, {'sha256': ''},
                        {'download_url': URL.replace('https:', 'http:')},
                        {'download_url': URL.replace(NAME, 'other.exe')}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as raw:
                opener = Opener()
                with self.assertRaises(delivery.DownloadError):
                    delivery.download_update(package(**changes), raw, opener=opener)
                self.assertEqual(opener.requests, [])
                self.assert_clean_cache(Path(raw))

    def test_disk_tampering_after_download_is_rejected_before_install_handoff(self):
        with tempfile.TemporaryDirectory() as raw:
            downloaded = delivery.download_update(package(), raw, opener=Opener(Response(PAYLOAD)))
            path = Path(downloaded.path)
            path.write_bytes(b'X' + PAYLOAD[1:])
            with self.assertRaises(delivery.DownloadError):
                delivery.verify_downloaded(downloaded)
            path.write_bytes(PAYLOAD[:-1])
            with self.assertRaises(delivery.DownloadError):
                delivery.verify_downloaded(downloaded)

    def test_save_dialog_delay_cannot_handoff_a_replaced_package_even_with_same_size_and_mtime(self):
        with tempfile.TemporaryDirectory() as raw:
            downloaded = delivery.download_update(package(), raw, opener=Opener(Response(PAYLOAD)))
            delivery.assert_verified_identity(downloaded)
            path, original = Path(downloaded.path), Path(downloaded.path).stat()
            replacement = path.with_name('replacement.fixture')
            replacement.write_bytes(b'X' + PAYLOAD[1:])
            os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
            os.replace(replacement, path)
            with self.assertRaises(delivery.DownloadError):
                delivery.assert_verified_identity(downloaded)

    def test_cancellation_during_disk_verification_is_checked_and_cleans_download(self):
        with tempfile.TemporaryDirectory() as raw:
            cancel, events = threading.Event(), []

            def progress(event):
                events.append(event)
                if event.stage == 'verifying':
                    cancel.set()

            with self.assertRaises(delivery.DownloadCancelled):
                delivery.download_update(package(), raw, cancel=cancel, progress=progress,
                                         opener=Opener(Response(PAYLOAD)))
            self.assertNotIn('ready', [event.stage for event in events])
            self.assert_clean_cache(Path(raw))

    def test_cancellation_before_start_makes_no_request_or_cache_files(self):
        with tempfile.TemporaryDirectory() as raw:
            cancel, opener = threading.Event(), Opener()
            cancel.set()
            with self.assertRaises(delivery.DownloadCancelled):
                delivery.download_update(package(), raw, cancel=cancel, opener=opener)
            self.assertEqual(opener.requests, [])
            self.assert_clean_cache(Path(raw))

    def test_total_deadline_cleans_the_attempt_even_with_a_responsive_connection(self):
        with tempfile.TemporaryDirectory() as raw:
            now, events = [0], []

            def progress(event):
                events.append(event)
                if event.stage == 'downloading':
                    now[0] = delivery.TOTAL_TIMEOUT + 1

            response = Response(PAYLOAD)
            with self.assertRaises(delivery.DownloadError):
                delivery.download_update(package(), raw, progress=progress,
                                         opener=Opener(response), clock=lambda: now[0])
            self.assertTrue(response.closed)
            self.assertNotIn('ready', [event.stage for event in events])
            self.assert_clean_cache(Path(raw))

    def test_success_requests_send_no_authorization_or_cookie_headers(self):
        with tempfile.TemporaryDirectory() as raw:
            opener, events = Opener(Response(PAYLOAD)), []
            result = delivery.download_update(package(), raw, opener=opener, progress=events.append)
            self.assertEqual(events[-1].stage, 'ready')
            self.assertIn('verifying', [event.stage for event in events])
            self.assertEqual(events[-1].received, len(PAYLOAD))
            self.assertEqual(Path(result.path).read_bytes(), PAYLOAD)
            request, timeout = opener.requests[0]
            self.assertIsNone(request.get_header('Authorization'))
            self.assertIsNone(request.get_header('Cookie'))
            self.assertEqual(request.get_header('Accept-encoding'), 'identity')
            self.assertEqual(timeout, delivery.SOCKET_TIMEOUT)


if __name__ == '__main__':
    unittest.main()
