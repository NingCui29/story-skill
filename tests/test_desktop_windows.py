"""Validate the desktop/backend process boundary without requiring Qt."""
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('windows_client', ROOT / 'desktop/windows/client.py')
client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client)


class WindowsDesktopTests(unittest.TestCase):
    def test_navigation_rejects_remote_and_ambiguous_addresses(self):
        self.assertTrue(client.local_url('http://127.0.0.1:8765/'))
        self.assertTrue(client.local_url('http://127.0.0.1:49152/token/'))
        for value in ('https://127.0.0.1:8765/', 'file:///tmp/book',
                      'http://127.0.0.1/', 'http://localhost:8765/',
                      'http://127.0.0.1:99999/', 'http://127.0.0.1:abc/',
                      'http://user@127.0.0.1:8765/', 'http://example.com:8765/'):
            with self.subTest(value=value):
                self.assertFalse(client.local_url(value))

    def test_backend_uses_bundled_interpreter_without_shell(self):
        packet = {'ok': True, 'url': 'http://127.0.0.1:8765/', 'reused': True}
        with patch.dict(client.os.environ, {'PYTHONHOME': '/bad', 'PYTHONPATH': '/bad'}):
            with patch.object(client.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps(packet))) as run:
                self.assertEqual(client.start_backend(Path('/客户端 空格')), packet)
        args, kwargs = run.call_args
        self.assertEqual(args[0], client.backend_command(Path('/客户端 空格')))
        self.assertNotIn('PYTHONHOME', kwargs['env'])
        self.assertNotIn('PYTHONPATH', kwargs['env'])
        self.assertFalse(kwargs.get('shell', False))
        self.assertEqual(kwargs['timeout'], 25)

    def test_backend_errors_are_visible_and_never_navigated(self):
        for code, stdout in ((2, '{"ok":false,"message":"书架不可达"}'),
                             (0, 'invalid JSON'),
                             (0, '{"ok":true,"url":"https://example.com"}')):
            with self.subTest(stdout=stdout), patch.object(
                    client.subprocess, 'run', return_value=SimpleNamespace(
                        returncode=code, stdout=stdout)):
                with self.assertRaises(RuntimeError):
                    client.start_backend(Path('/app'))

    def test_timeout_propagates_to_startup_error_dialog(self):
        with patch.object(client.subprocess, 'run', side_effect=subprocess.TimeoutExpired('backend', 25)):
            with self.assertRaises(subprocess.TimeoutExpired):
                client.start_backend(Path('/app'))


if __name__ == '__main__':
    unittest.main()
