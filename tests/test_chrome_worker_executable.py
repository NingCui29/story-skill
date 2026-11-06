"""CLI discovery under the environment inherited by a normal Windows launch."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "skills/story-skill/scripts/story_chrome_worker.py"
SPEC = importlib.util.spec_from_file_location("worker_executable_test", SCRIPT)
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


class ExecutableDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.local = Path(self.temp.name).resolve() / "Local AppData"
        self.local.mkdir()
        patcher = patch.object(worker.sys, "platform", "win32")
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.dict(worker.os.environ, {"LOCALAPPDATA": str(self.local), "CODEX_INSTALL_DIR": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(worker.shutil, "which", return_value=None)
        self.which = patcher.start()
        self.addCleanup(patcher.stop)
        self.probes = []

        def probe(argv, **kwargs):
            self.assertEqual(argv[1:], ["--version"])
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            self.assertEqual(kwargs["timeout"], 3)
            self.assertNotIn("shell", kwargs)
            self.probes.append(Path(argv[0]))
            return subprocess.CompletedProcess(argv, 0, b"codex-cli 0.0.0-dev\n")

        patcher = patch.object(worker.subprocess, "run", side_effect=probe)
        self.run = patcher.start()
        self.addCleanup(patcher.stop)

    def executable(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake CLI; never execute")
        path.chmod(0o700)
        return path

    def cached(self, digest, timestamp=1):
        path = self.executable(self.local / "OpenAI/Codex/bin" / digest / "codex.exe")
        os.utime(path, ns=(timestamp, timestamp))
        return path

    def test_desktop_cached_cli_is_found_when_path_has_no_codex(self):
        cached = self.cached("a" * 16)
        self.assertEqual(worker._codex_executable(), cached)
        self.assertEqual(self.probes, [cached])

    def test_normal_install_precedes_cache_and_does_not_require_desktop_running(self):
        self.cached("a" * 16)
        installed = self.executable(self.local / "Programs/OpenAI/Codex/bin/codex.exe")
        self.assertEqual(worker._codex_executable(), installed)
        self.assertEqual(self.probes, [installed])

    def test_existing_path_selection_is_preserved(self):
        self.cached("a" * 16)
        selected = self.executable(self.local / "Custom CLI/codex.exe")
        self.which.return_value = str(selected)
        self.assertEqual(worker._codex_executable(), selected)
        self.assertEqual(self.probes, [selected])

    def test_documented_custom_install_directory_is_used_without_path(self):
        selected = self.executable(self.local / "Custom CLI/codex.exe")
        with patch.dict(worker.os.environ, {"CODEX_INSTALL_DIR": str(selected.parent)}):
            self.assertEqual(worker._codex_executable(), selected)

    def test_broken_cache_entries_do_not_hide_valid_installation(self):
        cache = self.local / "OpenAI/Codex/bin"
        (cache / ("a" * 16)).mkdir(parents=True)
        self.executable(cache / ("b" * 16))
        self.executable(cache / "unrecognized/codex.exe")
        valid = self.cached("c" * 16)
        self.assertEqual(worker._codex_executable(), valid)
        self.assertEqual(self.probes, [valid])

    def test_newer_cache_is_preferred_and_bad_version_receipt_is_skipped(self):
        older = self.cached("a" * 16, 1_000_000_000)
        newer = self.cached("b" * 16, 2_000_000_000)
        def probe(argv, **kwargs):
            self.probes.append(Path(argv[0]))
            return subprocess.CompletedProcess(argv, 0, b"not Codex\n" if Path(argv[0]) == newer else b"codex-cli 0.1.0\n")
        self.run.side_effect = probe
        self.assertEqual(worker._codex_executable(), older)
        self.assertEqual(self.probes, [newer, older])

    def test_unbounded_cache_and_relative_install_path_are_not_searched(self):
        cache = self.local / "OpenAI/Codex/bin"
        cache.mkdir(parents=True)
        for index in range(101):
            (cache / str(index)).mkdir()
        with patch.dict(worker.os.environ, {"CODEX_INSTALL_DIR": "relative"}):
            with self.assertRaises(worker.WorkerError) as error:
                worker._codex_executable()
        self.assertEqual(error.exception.code, "platform_chrome_worker_codex_unavailable")
        self.run.assert_not_called()

    def test_missing_installation_remains_unavailable_without_launching_a_model(self):
        with self.assertRaises(worker.WorkerError) as error:
            worker._codex_executable()
        self.assertEqual(error.exception.code, "platform_chrome_worker_codex_unavailable")
        self.run.assert_not_called()

    def test_all_version_probes_share_one_bounded_deadline(self):
        self.cached("a" * 16)
        self.cached("b" * 16)
        self.run.side_effect = subprocess.TimeoutExpired("codex", 3)
        with patch.object(worker.time, "monotonic", side_effect=[0, 0, 0, 7]):
            with self.assertRaises(worker.WorkerError) as error:
                worker._codex_executable()
        self.assertEqual(error.exception.code, "platform_chrome_worker_codex_unavailable")
        self.assertEqual(self.run.call_count, 1)

    @unittest.skipIf(os.name == "nt", "Windows symlink creation requires a separate privilege")
    def test_cache_link_cannot_select_an_executable_outside_managed_cache(self):
        outside = self.executable(self.local / "Other/codex.exe")
        cache = self.local / "OpenAI/Codex/bin" / ("a" * 16)
        cache.mkdir(parents=True)
        (cache / "codex.exe").symlink_to(outside)
        with self.assertRaises(worker.WorkerError):
            worker._codex_executable()
        self.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
