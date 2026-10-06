"""Exercise the native lease in isolated processes without launching an app."""
import os
from pathlib import Path
import platform
import select
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'desktop/macos/ClientInstance.swift'


class DesktopSingleInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != 'darwin' or not shutil.which('xcrun'):
            raise unittest.SkipTest('macOS Swift compiler required')
        cls.compilation = tempfile.TemporaryDirectory(prefix='story-client-instance-')
        cls.addClassCleanup(cls.compilation.cleanup)
        folder = Path(cls.compilation.name).resolve()
        harness = folder / 'harness.swift'
        harness.write_text(r'''
import AppKit
import Darwin

@main struct LockHarness {
    @MainActor static func main() {
        let arguments=CommandLine.arguments
        if arguments[1]=="policy" {
            if case .bypassed = ClientInstance.claim(bundleIdentifier:"invalid/identity",selfTest:true) {} else { exit(1) }
            if case .failure = ClientInstance.claim(bundleIdentifier:"invalid/identity") {} else { exit(1) }
            let configuration=ClientInstance.reopenConfiguration()
            guard !configuration.createsNewApplicationInstance, configuration.allowsRunningApplicationSubstitution,
                  configuration.activates, !configuration.promptsUserIfNeeded else { exit(1) }
            guard ClientInstanceLock.validBundleIdentifier("com.storyskill.workbench"),
                  !ClientInstanceLock.validBundleIdentifier("../other"),
                  !ClientInstanceLock.validBundleIdentifier("a/b") else { exit(1) }
            print("policy-passed")
            return
        }
        do {
            switch try ClientInstanceLock.acquire(at:URL(fileURLWithPath:arguments[2],isDirectory:true)) {
            case .occupied:
                print("occupied");fflush(stdout)
            case .acquired(let lease):
                print("primary");fflush(stdout)
                if arguments[1]=="hold" { while readLine() != nil {} }
                lease.release()
                lease.release()
            }
        } catch {
            print("rejected");fflush(stdout)
            exit(2)
        }
    }
}
''')
        cls.executable = folder / 'instance-first'
        built = subprocess.run([
            'xcrun', 'swiftc', '-warnings-as-errors', '-parse-as-library',
            '-target', platform.machine() + '-apple-macosx13.0', '-framework', 'AppKit',
            str(SOURCE), str(harness), '-o', str(cls.executable),
        ], capture_output=True, text=True, timeout=60)
        if built.returncode != 0:
            raise AssertionError(built.stdout + built.stderr)
        cls.second_executable = folder / 'instance-second'
        shutil.copy2(cls.executable, cls.second_executable)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='story-client-lock-')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name).resolve()
        self.directory = self.folder / 'com.storyskill.workbench'
        self.children = []
        self.addCleanup(self.stop_children)

    def stop_children(self):
        for child in self.children:
            if child.stdin and not child.stdin.closed:
                child.stdin.close()
            if child.poll() is None:
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.terminate()  # Only this test's isolated harness process.
                    child.wait(timeout=5)
            for stream in (child.stdout, child.stderr):
                if stream:
                    stream.close()

    def start(self, executable=None):
        child = subprocess.Popen(
            [str(executable or self.executable), 'hold', str(self.directory)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.children.append(child)
        return child

    def first_line(self, child):
        self.assertTrue(select.select([child.stdout], [], [], 10)[0], 'Harness failed to report its claim')
        return child.stdout.readline().strip()

    def probe(self, directory=None):
        return subprocess.run(
            [str(self.executable), 'probe', str(directory or self.directory)],
            capture_output=True, text=True, timeout=10,
        )

    def test_two_binary_copies_have_one_owner_and_normal_release_keeps_inode(self):
        first = self.start()
        second = self.start(self.second_executable)
        claims = [self.first_line(first), self.first_line(second)]
        self.assertCountEqual(claims, ['primary', 'occupied'])
        lock = self.directory / 'client.lock'
        before = lock.stat()
        owner = [first, second][claims.index('primary')]
        owner.stdin.close()
        self.assertEqual(owner.wait(timeout=5), 0)
        again = self.probe()
        self.assertEqual((again.returncode, again.stdout.strip()), (0, 'primary'), again.stderr)
        after = lock.stat()
        self.assertEqual((before.st_ino, before.st_size, before.st_mtime_ns),
                         (after.st_ino, after.st_size, after.st_mtime_ns))

    def test_process_exit_releases_lock_without_deleting_or_rewriting_content(self):
        self.directory.mkdir(mode=0o700)
        lock = self.directory / 'client.lock'
        lock.write_bytes(b'Existing contents are not a process record.\n')
        before = lock.stat()
        owner = self.start()
        self.assertEqual(self.first_line(owner), 'primary')
        blocked = self.probe()
        self.assertEqual((blocked.returncode, blocked.stdout.strip()), (0, 'occupied'))
        owner.terminate()  # Prove kernel release for an isolated test process.
        owner.wait(timeout=5)
        released = self.probe()
        self.assertEqual((released.returncode, released.stdout.strip()), (0, 'primary'), released.stderr)
        after = lock.stat()
        self.assertEqual(lock.read_bytes(), b'Existing contents are not a process record.\n')
        self.assertEqual((before.st_ino, before.st_size, before.st_mtime_ns),
                         (after.st_ino, after.st_size, after.st_mtime_ns))

    def test_symlinked_directory_and_lock_do_not_touch_targets(self):
        target = self.folder / 'other-data'
        target.mkdir(mode=0o700)
        self.directory.symlink_to(target, target_is_directory=True)
        self.assertEqual(self.probe().stdout.strip(), 'rejected')
        self.assertFalse((target / 'client.lock').exists())
        self.directory.unlink()
        self.directory.mkdir(mode=0o700)
        material = target / 'important.txt'
        material.write_bytes(b'Original material')
        before = material.stat()
        (self.directory / 'client.lock').symlink_to(material)
        self.assertEqual(self.probe().stdout.strip(), 'rejected')
        after = material.stat()
        self.assertEqual(material.read_bytes(), b'Original material')
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))

    def test_shared_writable_directories_and_hardlinked_files_are_refused(self):
        self.directory.mkdir(mode=0o700)
        self.directory.chmod(0o777)
        self.assertEqual(self.probe().stdout.strip(), 'rejected')
        self.assertFalse((self.directory / 'client.lock').exists())
        self.directory.chmod(0o700)
        material = self.folder / 'material.txt'
        material.write_bytes(b'Keep me')
        os.link(material, self.directory / 'client.lock')
        self.assertEqual(self.probe().stdout.strip(), 'rejected')
        self.assertEqual(material.read_bytes(), b'Keep me')
        (self.directory / 'client.lock').unlink()
        os.mkfifo(self.directory / 'client.lock', 0o600)
        self.assertEqual(self.probe().stdout.strip(), 'rejected')

    def test_self_test_bypasses_identity_lock_and_reopen_reuses_running_app(self):
        result = subprocess.run([str(self.executable), 'policy'], capture_output=True, text=True, timeout=10)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, 'policy-passed'), result.stderr)
        self.assertFalse(self.directory.exists())


if __name__ == '__main__':
    unittest.main()
