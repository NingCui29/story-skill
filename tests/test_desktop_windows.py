"""The bundled Python backend remains independent from the C# desktop host."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WindowsDesktopTests(unittest.TestCase):
    def test_bundled_cli_check_uses_real_init_and_status_schema(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        try:
            from build_desktop_windows import verify_book_runtime
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as raw:
            verify_book_runtime(sys.executable, ROOT / 'skills/story-skill/scripts/story.py',
                                Path(raw) / '中文 空格目录')


if __name__ == '__main__':
    unittest.main()
