"""Discover the offline JS public-CUA driver contracts in the Python suite."""
from pathlib import Path
import shutil
import subprocess
import unittest


class ChromeBridgeDriverContracts(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for JS CUA driver contracts")
    def test_public_cua_contracts(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([shutil.which("node"), "--test", "tests/test_chrome_bridge_driver.mjs"],
                                cwd=root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
