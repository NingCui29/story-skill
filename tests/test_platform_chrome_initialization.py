"""Local bridge initialization recovery; no browser or platform operations."""
import json
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from tests import test_platform_chrome_bridge as fixture

bridge = fixture.bridge


class ChromeInitializationTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixture.ChromeBridgeTests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)

    def test_failed_first_metadata_publication_preserves_evidence_and_can_reconnect(self):
        for failed_name in ("session.json", "lease.json"):
            with self.subTest(failed_name=failed_name):
                helper = self.helper
                original = bridge._write
                def write(directory, name, value, **kwargs):
                    if name == failed_name:
                        raise OSError("fixture interrupted initial publication")
                    return original(directory, name, value, **kwargs)
                with patch.object(bridge, "_write", write):
                    with self.assertRaises(OSError):
                        bridge.attach(helper.book, helper.plan["id"], helper.path.name, helper.handle)
                self.assertFalse(helper.handle.exists())
                queue_root = helper.root / bridge.DIRECTORY
                old_ids = {path.name for path in queue_root.iterdir() if path.is_dir()}
                evidence = {path: path.read_bytes() for path in queue_root.rglob("*") if path.is_file()}
                state = bridge.worker_status(helper.root, helper.binding())
                self.assertFalse(state["attached"])
                self.assertEqual(state["active_sessions"], 0)
                created = bridge.attach(helper.book, helper.plan["id"], helper.path.name, helper.handle)
                self.assertNotIn(created["session_id"], old_ids)
                self.assertFalse(created["ready"])
                self.assertEqual({path: path.read_bytes() for path in evidence}, evidence)
                bridge.heartbeat(helper.book, helper.handle, detach=True)
                helper.handle.unlink()

    def test_unpublished_stage_is_preserved_but_not_counted_as_an_active_session(self):
        helper = self.helper
        with bridge._directory(helper.root, "a" * 32, create=True) as directory:
            raw = directory.path / (".chrome-stage-" + "b" * 32)
            raw.write_bytes(b'{"unpublished": "metadata"}')
            raw.chmod(0o600)
        before = raw.read_bytes()
        self.assertFalse(bridge.worker_status(helper.root, helper.binding())["attached"])
        self.assertEqual(raw.read_bytes(), before)
        self.assertFalse((raw.parent / "session.json").exists())
        created = bridge.attach(helper.book, helper.plan["id"], helper.path.name, helper.handle)
        self.assertNotEqual(created["session_id"], "a" * 32)
        self.assertEqual(raw.read_bytes(), before)

    def test_error_after_initial_metadata_publication_does_not_leave_a_live_orphan(self):
        helper = self.helper
        for failed_name in ("session.json", "lease.json"):
            with self.subTest(failed_name=failed_name):
                original, failed = bridge._write, []
                def write(directory, name, value, **kwargs):
                    result = original(directory, name, value, **kwargs)
                    if name == failed_name and not failed:
                        failed.append(True)
                        raise OSError("fixture post-publication fsync failure")
                    return result
                with patch.object(bridge, "_write", write):
                    with self.assertRaises(OSError):
                        bridge.attach(helper.book, helper.plan["id"], helper.path.name, helper.handle)
                self.assertFalse(helper.handle.exists())
                self.assertFalse(bridge.worker_status(helper.root, helper.binding())["attached"])
                created = bridge.attach(helper.book, helper.plan["id"], helper.path.name, helper.handle)
                self.assertTrue(created["ok"])
                bridge.heartbeat(helper.book, helper.handle, detach=True)
                helper.handle.unlink()

    def test_catchable_handle_interrupt_retires_lease_and_preserves_published_evidence(self):
        for interrupted in (KeyboardInterrupt, SystemExit):
            for published in (False, True):
                with self.subTest(interrupted=interrupted.__name__, published=published):
                    helper = fixture.ChromeBridgeTests()
                    helper.setUp()
                    self.addCleanup(helper.doCleanups)
                    original = bridge._external
                    def external(path, *, create_value=None):
                        if create_value is not None and Path(path) == helper.handle:
                            if published:
                                original(path, create_value=create_value)
                            raise interrupted("fixture catchable handle interruption")
                        return original(path, create_value=create_value)
                    with patch.object(bridge, "_external", external):
                        with self.assertRaises(interrupted):
                            bridge.attach(helper.book, helper.plan["id"], helper.path.name, helper.handle)
                    self.assertEqual(helper.handle.exists(), published)
                    evidence = helper.handle.read_bytes() if published else None
                    state = bridge.worker_status(helper.root, helper.binding())
                    self.assertFalse(state["attached"])
                    self.assertEqual(state["active_sessions"], 0)
                    if published:
                        helper.assert_code("platform_chrome_worker_unavailable", bridge.heartbeat,
                                           helper.book, helper.handle)
                        self.assertEqual(helper.handle.read_bytes(), evidence)
                    replacement = helper.root / "replacement-handle.json"
                    created = bridge.attach(helper.book, helper.plan["id"], helper.path.name, replacement)
                    self.assertTrue(created["ok"])
                    bridge.heartbeat(helper.book, replacement, detach=True)
                    replacement.unlink()
                    helper.handle.unlink(missing_ok=True)

    def test_catchable_metadata_lock_exit_interrupt_retires_unpublished_handle_session(self):
        helper = self.helper
        original, interrupted = bridge._lock, []
        @contextmanager
        def lock(directory, name="session.lock"):
            with original(directory, name):
                yield
            if name == "session.lock" and not interrupted and (directory.path / "lease.json").exists():
                interrupted.append(True)
                raise KeyboardInterrupt("fixture interrupted metadata lock handoff")
        with patch.object(bridge, "_lock", lock):
            with self.assertRaises(KeyboardInterrupt):
                bridge.attach(helper.book, helper.plan["id"], helper.path.name, helper.handle)
        self.assertFalse(helper.handle.exists())
        self.assertFalse(bridge.worker_status(helper.root, helper.binding())["attached"])
        self.assertTrue(bridge.attach(helper.book, helper.plan["id"], helper.path.name, helper.handle)["ok"])

    def test_hard_exit_before_handle_keeps_live_lease_until_normal_expiry(self):
        helper = self.helper
        script = r'''
import os, sys
from tests import test_publish as fixture
bridge = fixture.story.platform_chrome_bridge
external = bridge._external
def exit_before_handle(path, *, create_value=None):
    if create_value is not None:
        os._exit(73)
    return external(path)
bridge._external = exit_before_handle
root, plan, profile, handle = sys.argv[1:]
book = fixture.story.Book(root)
bridge.attach(book, plan, profile, handle)
'''
        child = subprocess.run([sys.executable, "-B", "-c", script, str(helper.root),
            helper.plan["id"], helper.path.name, str(helper.handle)],
            cwd=str(Path(__file__).resolve().parents[1]), capture_output=True, text=True, timeout=15,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(child.returncode, 73, child.stderr)
        self.assertFalse(helper.handle.exists())
        binding = helper.binding()
        state = bridge.worker_status(helper.root, binding)
        self.assertTrue(state["attached"])
        self.assertFalse(state["ready"])
        folder = helper.root / bridge.DIRECTORY / state["session_id"]
        before = {path: path.read_bytes() for path in folder.iterdir() if path.is_file()}
        helper.assert_code("platform_chrome_worker_busy", bridge.attach,
                           helper.book, helper.plan["id"], helper.path.name, helper.handle)
        expiry = json.loads((folder / "lease.json").read_text())["expires"]
        with patch.object(bridge.time, "time", return_value=expiry + 1):
            self.assertFalse(bridge.worker_status(helper.root, binding)["attached"])
            created = bridge.attach(helper.book, helper.plan["id"], helper.path.name, helper.handle)
        self.assertNotEqual(created["session_id"], state["session_id"])
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_unknown_requests_health_or_lease_never_become_initialization_residue(self):
        helper = self.helper
        for name in ("outbox-" + "c" * 32 + ".json", "claimed-" + "c" * 32 + ".json",
                     "response-" + "c" * 32 + ".json", "health.json", "lease.json", "unknown.json"):
            with self.subTest(name=name):
                session_id = "a" * 32
                with bridge._directory(helper.root, session_id, create=True) as directory:
                    bridge._write(directory, name, {"session_id": session_id, "expires": time.time() + 120, "detached": False})
                file = directory.path / name
                before = file.read_bytes()
                with self.assertRaises((fixture.story.StoryError, FileNotFoundError)):
                    bridge.worker_status(helper.root, helper.binding())
                with self.assertRaises((fixture.story.StoryError, FileNotFoundError)):
                    bridge.attach(helper.book, helper.plan["id"], helper.path.name, helper.handle)
                self.assertFalse(helper.handle.exists())
                self.assertEqual(file.read_bytes(), before)
                file.unlink()

    def test_invalid_published_metadata_is_not_silently_skipped(self):
        helper = self.helper
        with bridge._directory(helper.root, "a" * 32, create=True) as directory:
            bridge._write(directory, "session.json", {"invalid": True})
        with self.assertRaises(fixture.story.StoryError) as error:
            bridge.worker_status(helper.root, helper.binding())
        self.assertEqual(error.exception.code, "platform_chrome_invalid_message")
        self.assertFalse(helper.handle.exists())

    def test_active_initialization_serializes_other_process_status_and_attach(self):
        helper = self.helper
        entered = helper.root / "initialization-entered"
        release = helper.root / "initialization-release"
        script = r'''
import json, sys, time
from contextlib import closing
from pathlib import Path
from tests import test_publish as fixture
bridge = fixture.story.platform_chrome_bridge
root, plan, profile, handle, entered, release, mode = sys.argv[1:]
if mode == 'first':
    write = bridge._write
    def blocked(directory, name, value, **kwargs):
        if name == 'session.json':
            Path(entered).write_text('entered')
            deadline = time.monotonic() + 15
            while not Path(release).exists():
                if time.monotonic() > deadline:
                    raise RuntimeError('fixture initialization wait timed out')
                time.sleep(.01)
        return write(directory, name, value, **kwargs)
    bridge._write = blocked
with closing(fixture.story.Book(root)) as book:
    results = []
    if mode == 'second':
        try:
            binding = bridge._binding(book, plan, profile)[0]
            Path(entered + '.second').write_text('before status')
            results.append({'status': bridge.worker_status(root, binding)})
        except Exception as error:
            results.append({'error': getattr(error, 'code', type(error).__name__)})
    try:
        results.append({'attach': bridge.attach(book, plan, profile, handle)})
    except Exception as error:
        results.append({'error': getattr(error, 'code', type(error).__name__)})
    print(json.dumps(results))
'''
        base = [sys.executable, "-B", "-c", script, str(helper.root), helper.plan["id"], helper.path.name]
        options = {"cwd": str(Path(__file__).resolve().parents[1]), "text": True,
                   "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                   "env": {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}}
        first = subprocess.Popen(base + [str(helper.handle), str(entered), str(release), "first"], **options)
        second = None
        try:
            deadline = time.monotonic() + 10
            while not entered.exists() and time.monotonic() < deadline and first.poll() is None:
                time.sleep(.01)
            self.assertTrue(entered.exists(), "first process never reached metadata publication")
            second = subprocess.Popen(base + [str(helper.root / "second-handle.json"), str(entered), str(release), "second"], **options)
            second_entered = Path(str(entered) + ".second")
            deadline = time.monotonic() + 10
            while not second_entered.exists() and time.monotonic() < deadline and second.poll() is None:
                time.sleep(.01)
            self.assertTrue(second_entered.exists(), "second process never reached status")
            time.sleep(.2)
            self.assertIsNone(second.poll(), "status observed an in-progress session as missing instead of waiting")
            release.write_text("release")
            first_out, first_err = first.communicate(timeout=15)
            second_out, second_err = second.communicate(timeout=15)
            self.assertEqual(first.returncode, 0, first_err)
            self.assertEqual(second.returncode, 0, second_err)
            self.assertTrue(json.loads(first_out)[0]["attach"]["ok"])
            packet = json.loads(second_out)
            self.assertTrue(packet[0]["status"]["attached"])
            self.assertEqual(packet[0]["status"]["active_sessions"], 1)
            self.assertEqual(packet[1]["error"], "platform_chrome_worker_busy")
            self.assertFalse((helper.root / "second-handle.json").exists())
        finally:
            release.write_text("release")
            for child in (first, second):
                if child is not None:
                    try:
                        child.communicate(timeout=15)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
