"""Discover the offline public-Chrome worker contracts in the Python suite."""
from pathlib import Path
import shutil
import subprocess
import unittest


class ChromeWorkerContracts(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for JS Chrome worker contracts")
    def test_public_chrome_worker_contracts(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([shutil.which("node"), "--test", "tests/test_chrome_worker.mjs"],
                                cwd=root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
