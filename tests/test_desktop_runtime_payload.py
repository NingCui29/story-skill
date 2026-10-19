"""Build sources must carry a complete, independently usable workbench runtime."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DesktopRuntimePayloadTests(unittest.TestCase):
    def test_exact_desktop_payload_supports_synthetic_book_backup_restore_and_safe_trash_failure(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        try:
            from build_desktop_macos import RUNTIME_FILES as mac_files
            from build_desktop_windows import RUNTIME_FILES as windows_files
        finally:
            sys.path.pop(0)
        self.assertEqual(mac_files, windows_files)
        with tempfile.TemporaryDirectory(prefix='story-desktop-payload-') as raw:
            directory = Path(raw)
            scripts = directory / '桌面客户端 中文 空格/Scripts'
            scripts.mkdir(parents=True)
            for filename in windows_files:
                shutil.copy2(ROOT / 'skills/story-skill/scripts' / filename, scripts / filename)
            report = directory / 'runtime-safety.json'
            result = subprocess.run([
                sys.executable, '-I', '-B', str(ROOT / 'tests/desktop_payload_probe.py'),
                '--scripts', str(scripts), '--area', str(directory / '隔离验证 空格'),
                '--report', str(report),
            ], cwd=directory, capture_output=True, timeout=45)
            self.assertEqual(result.returncode, 0,
                             result.stdout.decode('utf-8', 'replace') + result.stderr.decode('utf-8', 'replace'))
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt, json.loads(report.read_text(encoding='utf-8')))
            for field in ('ok', 'isolated_payload', 'created_and_registered', 'backup_verified',
                          'restore_identity_preserved', 'candidate_and_binary_preserved',
                          'sqlite_integrity_verified', 'missing_trash_path_refused'):
                self.assertIs(receipt[field], True, field)
            self.assertIs(receipt['existing_book_files_recycled'], False)
            self.assertEqual(receipt['native_trash_bridge_checked'], sys.platform in ('win32', 'darwin'))


if __name__ == '__main__':
    unittest.main()
