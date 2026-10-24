"""Windows status readers must not abort concurrent pinned atomic publication."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_chapter_layout as fixture


story = fixture.story


def sharing_violation(code=32):
    error = PermissionError("synthetic Windows sharing violation")
    error.winerror = code
    return error


class BoundRenameRetryTests(unittest.TestCase):
    def files(self):
        directory = SimpleNamespace(path=Path.cwd(), fd=None)
        return story._BoundFile(directory, "source"), story._BoundFile(directory, "target")

    def test_only_sharing_violation_retries_the_same_bound_no_replace_operation(self):
        source, target = self.files()
        with patch.object(story.os, "name", "nt"), \
                patch.object(story.os, "rename", side_effect=[sharing_violation(), None]) as rename, \
                patch.object(story.os, "replace") as replace, patch("time.sleep"):
            story._publish_no_replace(source, target)
        self.assertEqual(rename.call_args_list, [unittest.mock.call(source.path, target.path)] * 2)
        replace.assert_not_called()

    def test_other_errors_and_an_existing_target_do_not_retry(self):
        source, target = self.files()
        for error in (sharing_violation(5), sharing_violation(33), FileExistsError(), FileNotFoundError()):
            with self.subTest(error=repr(error)), patch.object(story.os, "name", "nt"), \
                    patch.object(story.os, "rename", side_effect=error) as rename, patch("time.sleep") as sleep:
                with self.assertRaises(type(error)) as caught:
                    story._publish_no_replace(source, target)
                self.assertIs(caught.exception, error)
                rename.assert_called_once_with(source.path, target.path)
                sleep.assert_not_called()

    def test_persistent_sharing_violation_is_bounded_and_preserves_original_error(self):
        source, target = self.files()
        now = [0.0]
        error = sharing_violation()
        def advance(seconds):
            now[0] += seconds
        with patch.object(story.os, "name", "nt"), \
                patch.object(story.os, "replace", side_effect=error) as replace, \
                patch("time.monotonic", side_effect=lambda: now[0]), patch("time.sleep", side_effect=advance):
            with self.assertRaises(PermissionError) as caught:
                story._bound_replace(source, target)
        self.assertIs(caught.exception, error)
        self.assertGreater(replace.call_count, 1)
        self.assertLessEqual(replace.call_count, 103)
        self.assertGreaterEqual(now[0], 1.0)
        self.assertLessEqual(now[0], 1.01)

    def test_unbound_windows_publication_does_not_gain_a_retry_without_parent_pins(self):
        error = sharing_violation()
        with patch.object(story.os, "name", "nt"), \
                patch.object(story.os, "rename", side_effect=error) as rename, patch("time.sleep") as sleep:
            with self.assertRaises(PermissionError):
                story._publish_no_replace("source", "target")
        rename.assert_called_once_with("source", "target")
        sleep.assert_not_called()


HOLD_STRICT_DIRECTORY = r'''
import ctypes
import importlib.util
from pathlib import Path
import sys
spec = importlib.util.spec_from_file_location('isolated_reader', sys.argv[1])
story = importlib.util.module_from_spec(spec)
spec.loader.exec_module(story)
directory, ready, held = map(Path, sys.argv[2:])
ready.write_bytes(b'ready')
if sys.stdin.readline().strip() == 'hold':
    with story._windows_path_handle(directory, directory=True):
        held.write_bytes(b'held')
        sys.stdin.readline()
'''


@unittest.skipUnless(sys.platform == "win32", "Requires native Windows cross-process directory sharing")
class NativeAtomicSharingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="story-desktop-atomic-sharing-")
        self.addCleanup(self.temporary.cleanup)
        self.area = Path(self.temporary.name).resolve()
        self.root = self.area / "书架 并发𠮷"
        self.root.mkdir()

    @contextmanager
    def reader(self, directory):
        ready, held = self.area / "reader-ready", self.area / "reader-held"
        for path in (ready, held):
            path.unlink(missing_ok=True)
        process = subprocess.Popen([sys.executable, "-I", "-B", "-X", "utf8", "-c", HOLD_STRICT_DIRECTORY,
                                    str(fixture.TOOL), str(directory), str(ready), str(held)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8")
        released = False
        def wait_for(path):
            deadline = time.monotonic() + 20
            while not path.exists():
                if process.poll() is not None:
                    output, error = process.communicate()
                    self.fail(f"Isolated reader exited before handshake: {output} {error}")
                if time.monotonic() >= deadline:
                    self.fail("Isolated reader did not complete bounded handshake")
                time.sleep(.01)
        def hold():
            process.stdin.write("hold\n")
            process.stdin.flush()
            wait_for(held)
        def release():
            nonlocal released
            if released:
                return
            output, error = process.communicate("release\n", timeout=20)
            released = True
            self.assertEqual(process.returncode, 0, output + error)
        try:
            wait_for(ready)
            yield hold, release
        finally:
            if process.poll() is None:
                try:
                    release()
                finally:
                    if process.poll() is None:
                        process.kill()  # Only this isolated synthetic lock-holder.
                        process.communicate(timeout=10)

    def write_under_reader(self, target, content, allowed, backup, *, operation="rename", release=True, on_block=None):
        original = getattr(story.os, operation)
        violations = []
        entered = False
        # Windows needs write access to the destination directory for a rename.
        # Moving out of a strict source directory can succeed, so block the
        # backup parent for displacement and the target parent for publication.
        # The child opens it only on hold(), after atomic_write created/pinned it.
        directory = backup.parent if operation == "replace" else target.parent
        with self.reader(directory) as (hold, unlock):
            def observed(source, destination):
                nonlocal entered
                if not entered:
                    entered = True
                    hold()
                try:
                    return original(source, destination)
                except OSError as error:
                    if getattr(error, "winerror", None) != 32:
                        raise
                    violations.append(error.winerror)
                    if release:
                        unlock()
                        if on_block is not None:
                            on_block()
                    raise
            with patch.object(story.os, operation, side_effect=observed):
                try:
                    return story.atomic_write(target, content, allowed, backup), violations
                finally:
                    self.assertTrue(violations, "A real cross-process sharing failure must precede completion")

    def test_real_reader_conflict_recovers_first_publication_and_both_revision_renames(self):
        for mode in ("first", "revision-displace", "revision-publish"):
            with self.subTest(mode=mode):
                target = self.root / (mode + "-library.json")
                backup = self.root / (mode + "-backup") / target.name
                before, after = "原书架登记𠮷\n", "新书架登记📖\n"
                if mode != "first":
                    target.write_bytes(before.encode("utf-8"))
                result, violations = self.write_under_reader(
                    target, after, {story.digest(before)} if mode != "first" else set(), backup,
                    operation="replace" if mode == "revision-displace" else "rename")
                self.assertEqual(violations, [32])
                self.assertEqual(target.read_bytes(), after.encode("utf-8"))
                if mode != "first":
                    self.assertEqual(result, str(backup))
                    self.assertEqual(backup.read_bytes(), before.encode("utf-8"))
                else:
                    self.assertIsNone(result)
                self.assertEqual(list(self.root.glob(".story-tmp-*")), [])

    def test_permanent_reader_conflict_fails_bounded_without_displacing_old_registry(self):
        target = self.root / "library.json"
        before = "保留原书架登记\n"
        target.write_bytes(before.encode("utf-8"))
        backup = self.root / "backup" / "library.json"
        with self.assertRaises(OSError) as caught:
            self.write_under_reader(target, "更新\n", {story.digest(before)}, backup, operation="replace", release=False)
        self.assertEqual(caught.exception.winerror, 32)
        self.assertEqual(target.read_bytes(), before.encode("utf-8"))
        self.assertFalse(backup.exists())

    def test_concurrent_target_created_during_retry_is_never_overwritten(self):
        target = self.root / "library.json"
        external = "另一个进程已完成的登记\n".encode("utf-8")
        # Use a separate process for the competing writer as well as the reader.
        def create_target():
            result = subprocess.run([sys.executable, "-I", "-c",
                                     "import sys;f=open(sys.argv[1],'xb');f.write(bytes.fromhex(sys.argv[2]));f.close()",
                                     str(target), external.hex()], capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
        with self.assertRaises(FileExistsError):
            self.write_under_reader(target, "本次新登记\n", set(), self.root / "backup" / target.name,
                                    on_block=create_target)
        self.assertEqual(target.read_bytes(), external)
        self.assertEqual(list(self.root.glob(".story-tmp-*")), [])


if __name__ == "__main__":
    unittest.main()
