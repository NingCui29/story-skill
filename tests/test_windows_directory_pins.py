"""Native directory pins protect ancestors without blocking child publication."""
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

import test_chapter_layout as fixture


EXTERNAL_OPERATION = """
import os
from pathlib import Path
import sys

operation = sys.argv[1]
if operation == "swap":
    victim, moved, replacement = map(Path, sys.argv[2:])
    os.replace(victim, moved)
    os.replace(replacement, victim)
elif operation == "create":
    with Path(sys.argv[2]).open("xb") as handle:
        handle.write(sys.argv[3].encode("utf-8"))
elif operation == "unlink":
    Path(sys.argv[2]).unlink()
elif operation == "junction":
    import ctypes
    from ctypes import wintypes
    import json
    import struct

    # Real mount-point reparse data, including UTF-16 byte offsets for non-BMP names.
    # https://learn.microsoft.com/windows-hardware/drivers/ddi/ntifs/ns-ntifs-_reparse_data_buffer
    directory, destination = map(Path, sys.argv[2:])
    substitute = (chr(92) + "??" + chr(92) + str(destination)).encode("utf-16-le")
    printed = str(destination).encode("utf-16-le")
    data = (struct.pack("<HHHH", 0, len(substitute), len(substitute) + 2, len(printed))
            + substitute + bytes(2) + printed + bytes(2))
    data = struct.pack("<IHH", 0xA0000003, len(data), 0) + data
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID,
                                      wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel.DeviceIoControl.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # GENERIC_WRITE, all shares, OPEN_EXISTING, OPEN_REPARSE_POINT | BACKUP_SEMANTICS.
    handle = kernel.CreateFileW(str(directory), 0x40000000, 0x7, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        result = {"success": False, "stage": "open", "winerror": ctypes.get_last_error()}
    else:
        try:
            buffer = ctypes.create_string_buffer(data)
            returned = wintypes.DWORD()
            success = kernel.DeviceIoControl(handle, 0x000900A4, buffer, len(data),
                                             None, 0, ctypes.byref(returned), None)
            result = {"success": bool(success), "stage": "FSCTL_SET_REPARSE_POINT",
                      "winerror": 0 if success else ctypes.get_last_error()}
        finally:
            kernel.CloseHandle(handle)
    print(json.dumps(result))
else:
    raise ValueError(operation)
"""


@unittest.skipUnless(sys.platform == "win32", "Requires native Windows directory share locks")
class WindowsDirectoryPinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-windows-pins-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "书库𠮷📖"
        self.root.mkdir()
        self.story = fixture.story

    def external(self, operation, *arguments):
        return subprocess.run(
            [sys.executable, "-I", "-X", "utf8", "-c", EXTERNAL_OPERATION,
             operation, *(str(argument) for argument in arguments)],
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )

    def assert_no_stages(self, directory):
        self.assertEqual(list(directory.glob(".story-tmp-*")), [])
        self.assertEqual(list(directory.glob(".story-restore-*")), [])

    def set_external_junction(self, directory, destination):
        result = self.external("junction", directory, destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_external_process_cannot_swap_pinned_leaf_or_ancestor(self):
        for level in ("leaf", "ancestor"):
            with self.subTest(level=level):
                book_root = self.root / level / "小说𠮷"
                parent = book_root / "正文" / "第一卷 风雪📖"
                parent.mkdir(parents=True)
                victim = parent if level == "leaf" else book_root
                marker = victim / "原目录.txt"
                marker.write_bytes(b"original directory")
                moved = victim.with_name("移走𠮷")
                replacement = victim.with_name("外部替换📖")
                replacement.mkdir()
                (replacement / "外部目录.txt").write_bytes(b"replacement directory")

                with self.story._pinned_directory(parent):
                    blocked = self.external("swap", victim, moved, replacement)
                    self.assertNotEqual(blocked.returncode, 0, blocked.stdout)
                    self.assertEqual(marker.read_bytes(), b"original directory")
                    self.assertFalse(moved.exists())
                    self.assertEqual((replacement / "外部目录.txt").read_bytes(), b"replacement directory")

                released = self.external("swap", victim, moved, replacement)
                self.assertEqual(released.returncode, 0, released.stderr)
                self.assertEqual((moved / marker.name).read_bytes(), b"original directory")
                self.assertEqual((victim / "外部目录.txt").read_bytes(), b"replacement directory")

    def test_atomic_first_publication_and_revision_preserve_unicode_bytes(self):
        target = self.root / "正文📖" / "第一卷 风雪𠮷" / "第1章 木牌📖.md"
        first_backup = self.root / "备份" / "首次𠮷" / target.name
        revision_backup = self.root / "备份" / "修订📖" / target.name
        initial = "第1章 木牌📖\n她捡起写有𠮷字的木牌。\n"
        revised = initial + "她把木牌交还给主人。\n"

        self.assertIsNone(self.story.atomic_write(target, initial, set(), first_backup))
        self.assertEqual(target.read_bytes(), initial.encode("utf-8"))
        backup = self.story.atomic_write(target, revised, {self.story.digest(initial)}, revision_backup)
        self.assertEqual(backup, str(revision_backup))
        self.assertEqual(target.read_bytes(), revised.encode("utf-8"))
        self.assertEqual(revision_backup.read_bytes(), initial.encode("utf-8"))
        self.assert_no_stages(target.parent)

    def test_root_report_update_and_nested_backup_restore_share_ancestors(self):
        target = self.root / "report.md"
        backup = self.root / ".story" / "export-backups" / "修订𠮷📖" / "report.md"
        initial = "报告原稿𠮷。\n"
        revised = "报告修订稿📖。\n"
        target.write_bytes(initial.encode("utf-8"))

        saved_path = self.story.atomic_write(target, revised, {self.story.digest(initial)}, backup)
        self.assertEqual(saved_path, str(backup))
        self.assertEqual(target.read_bytes(), revised.encode("utf-8"))
        self.assertEqual(backup.read_bytes(), initial.encode("utf-8"))

        # The first pin's leaf becomes an ancestor traversed by the second pin.
        with self.story._pinned_directory(self.root) as pinned_root, \
                self.story._pinned_directory(backup.parent) as pinned_backup:
            target.unlink()
            self.story._restore_displaced_file(
                self.story._BoundFile(pinned_backup, backup.name),
                self.story._BoundFile(pinned_root, target.name),
            )
            self.assertEqual(target.read_bytes(), initial.encode("utf-8"))
            self.assertEqual(backup.read_bytes(), initial.encode("utf-8"))
        self.assertEqual({p.relative_to(self.root) for p in self.root.rglob("*") if p.is_file()},
                         {target.relative_to(self.root), backup.relative_to(self.root)})

    def test_nested_directory_guards_leave_no_files_on_normal_or_exception_exit(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                parent = self.root / ("异常退出𠮷" if interrupted else "正常退出📖")
                parent.mkdir()
                target = parent / "保留正文.md"

                def write_with_nested_pins():
                    with self.story._pinned_directory(parent), self.story._pinned_directory(parent):
                        target.write_bytes(b"preserved manuscript")
                        if interrupted:
                            raise RuntimeError("stop while pins are held")

                if interrupted:
                    with self.assertRaisesRegex(RuntimeError, "stop while pins are held"):
                        write_with_nested_pins()
                else:
                    write_with_nested_pins()
                self.assertEqual(list(parent.iterdir()), [target])
                self.assertEqual(target.read_bytes(), b"preserved manuscript")
                target.unlink()
                parent.rmdir()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_empty_pinned_directory_rejects_junction_conversion_until_released(self):
        destination = self.root / "外部目的地𠮷📖"
        destination.mkdir()
        marker = destination / "外部内容📖.txt"
        marker.write_bytes(b"outside marker")
        control = self.root / "无锁对照𠮷"
        control.mkdir()
        available = self.set_external_junction(control, destination)
        if not available["success"]:
            if available["winerror"] in {1, 5, 50, 1314}:
                self.skipTest(f"Native junction conversion unavailable: {available}")
            self.fail(f"Unpinned junction control failed unexpectedly: {available}")
        try:
            self.assertEqual((control / marker.name).read_bytes(), b"outside marker")
        finally:
            control.rmdir()

        victim = self.root / "待保护空目录📖"
        victim.mkdir()
        try:
            with self.story._pinned_directory(victim):
                for guard in victim.iterdir():
                    removed = self.external("unlink", guard)
                    self.assertNotEqual(removed.returncode, 0, removed.stdout)
                blocked = self.set_external_junction(victim, destination)
                self.assertFalse(blocked["success"], blocked)
                self.assertFalse(victim.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
                self.assertFalse((victim / marker.name).exists())
            self.assertEqual(list(victim.iterdir()), [])
            released = self.set_external_junction(victim, destination)
            self.assertTrue(released["success"], released)
            self.assertTrue(victim.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
            self.assertEqual((victim / marker.name).read_bytes(), b"outside marker")
        finally:
            if victim.exists() and victim.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                victim.rmdir()
        self.assertEqual(marker.read_bytes(), b"outside marker")

    def test_pinned_directories_allow_cross_directory_backup_and_copy_restore(self):
        parent = self.root / "正文📖"
        backup_parent = self.root / "备份𠮷" / "第一次"
        parent.mkdir()
        backup_parent.mkdir(parents=True)
        target = parent / "第1章 木牌𠮷.md"
        backup = backup_parent / "旧稿📖.md"
        content = "正文与中文、𠮷、📖保持原样。\n".encode("utf-8")
        target.write_bytes(content)

        with self.story._pinned_directory(parent) as pinned_parent, \
                self.story._pinned_directory(backup_parent) as pinned_backup:
            source = self.story._BoundFile(pinned_parent, target.name)
            saved = self.story._BoundFile(pinned_backup, backup.name)
            self.story._bound_replace(source, saved)
            self.assertFalse(target.exists())
            self.assertEqual(backup.read_bytes(), content)
            self.story._restore_displaced_file(saved, source)
            self.assertEqual(target.read_bytes(), content)
            self.assertEqual(backup.read_bytes(), content)
            self.assert_no_stages(parent)

    def test_changed_target_is_restored_and_retained_in_backup(self):
        target = self.root / "正文𠮷" / "第1章 外部修改📖.md"
        target.parent.mkdir()
        external = "编辑器改过的版本，不能被未审查的新稿替换。\n"
        target.write_bytes(external.encode("utf-8"))
        backup = self.root / "备份📖" / "冲突𠮷" / target.name

        with self.assertRaises(self.story.StoryError) as caught:
            self.story.atomic_write(target, "准备发布的新稿。\n",
                                    {self.story.digest("此前读取的旧稿。\n")}, backup)
        self.assertEqual(caught.exception.code, "export_conflict")
        self.assertEqual(target.read_bytes(), external.encode("utf-8"))
        self.assertEqual(backup.read_bytes(), external.encode("utf-8"))
        self.assert_no_stages(target.parent)

    def test_external_target_wins_over_first_publication(self):
        parent = self.root / "正文𠮷"
        parent.mkdir()
        target = parent / "第1章 外部稿📖.md"
        stage = parent / "完整待发布稿𠮷.tmp"
        content = "已准备完整的新稿。\n".encode("utf-8")
        stage.write_bytes(content)
        external = "另一个进程先保存了正文📖。\n"

        with self.story._pinned_directory(parent) as pinned:
            self.assertFalse(target.exists())
            created = self.external("create", target, external)
            self.assertEqual(created.returncode, 0, created.stderr)
            with self.assertRaises(FileExistsError):
                self.story._publish_no_replace(self.story._BoundFile(pinned, stage.name),
                                               self.story._BoundFile(pinned, target.name))
            self.assertEqual(target.read_bytes(), external.encode("utf-8"))
            self.assertEqual(stage.read_bytes(), content)

    def test_external_recreated_target_wins_over_backup_restore(self):
        parent = self.root / "正文𠮷"
        backup_parent = self.root / "备份📖"
        parent.mkdir()
        backup_parent.mkdir()
        target = parent / "第1章 被重新保存的稿件📖.md"
        backup = backup_parent / "原稿𠮷.md"
        original = "需要保留的完整备份。\n".encode("utf-8")
        backup.write_bytes(original)
        external = "另一个进程在恢复之前重新保存了正文𠮷。\n"

        with self.story._pinned_directory(parent) as pinned_parent, \
                self.story._pinned_directory(backup_parent) as pinned_backup:
            self.assertFalse(target.exists())
            created = self.external("create", target, external)
            self.assertEqual(created.returncode, 0, created.stderr)
            with self.assertRaises(FileExistsError):
                self.story._restore_displaced_file(
                    self.story._BoundFile(pinned_backup, backup.name),
                    self.story._BoundFile(pinned_parent, target.name),
                )
            self.assertEqual(target.read_bytes(), external.encode("utf-8"))
            self.assertEqual(backup.read_bytes(), original)
            self.assert_no_stages(parent)


if __name__ == "__main__":
    unittest.main()
