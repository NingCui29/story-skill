"""Identified local book lifecycle operations for the workbench.

The host supplies its existing registry, identity reader and editor lease. This
module never deletes file content, stops a process, or adopts manuscript text.
"""
from contextlib import ExitStack, contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import uuid


MAX_TREE_ENTRIES = 50000


@contextmanager
def lifecycle_guard(api, root):
    """Serialize editor lease acquisition and book moves outside the book.

    Keep this lock file in place: unlinking a lock permits two owners. The
    parent is pinned without keeping any handle inside a directory being moved.
    """
    root = Path(os.path.abspath(root))
    name = '.story-workbench-' + hashlib.sha256(str(root).encode()).hexdigest() + '.lock'
    with api._pinned_directory(root.parent) as parent:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        fd = (os.open(name, flags, 0o600, dir_fd=parent.fd) if parent.fd is not None
              else os.open(parent.path / name, flags, 0o600))
        with os.fdopen(fd, 'a+b') as handle:
            bound = api._BoundFile(parent, name)
            if (not stat.S_ISREG(os.fstat(handle.fileno()).st_mode) or
                    not os.path.samestat(os.fstat(handle.fileno()), api.publish._bound_stat(bound))):
                api.fail('workbench_book_changed', '作品操作锁已改变，请保留目录后核对。')
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                api.fail('workbench_book_busy', '作品正在打开、备份或移动，请稍后重试。')
            try:
                api._verify_bound_directory(parent)
                yield
            finally:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _writer_lease(api, root):
    """Share the core's writer/export lock, without waiting or deleting locks."""
    with api._pinned_directory(root / '.story') as directory:
        name = 'operation.lock'
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        fd = (os.open(name, flags, 0o600, dir_fd=directory.fd) if directory.fd is not None
              else os.open(directory.path / name, flags, 0o600))
        with os.fdopen(fd, 'a+b') as handle:
            if (not stat.S_ISREG(os.fstat(handle.fileno()).st_mode) or
                    not os.path.samestat(os.fstat(handle.fileno()), api.publish._bound_stat(api._BoundFile(directory, name)))):
                api.fail('workbench_book_changed', '作品写入锁已改变，请保留目录后核对。')
            handle.seek(0, 2)
            if not handle.tell():
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                api.fail('book_busy', '本书正在写入或导出，请完成操作后再移入回收站。')
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _macos_trash(path):
    """Use Foundation's trash API; no removeItem or filesystem-delete fallback.

    https://developer.apple.com/documentation/foundation/filemanager/trashitem(at:resultingitemurl:)
    """
    ctypes.CDLL('/System/Library/Frameworks/Foundation.framework/Foundation')
    objc = ctypes.CDLL('/usr/lib/libobjc.A.dylib')
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value

    def send(receiver, selector, result=ctypes.c_void_p, argtypes=(), args=()):
        call = ctypes.CFUNCTYPE(result, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(address)
        return call(receiver, objc.sel_registerName(selector), *args)

    pool = send(objc.objc_getClass(b'NSAutoreleasePool'), b'new')
    try:
        string = send(objc.objc_getClass(b'NSString'), b'stringWithUTF8String:',
                      argtypes=(ctypes.c_char_p,), args=(os.fsencode(path),))
        url = send(objc.objc_getClass(b'NSURL'), b'fileURLWithPath:',
                   argtypes=(ctypes.c_void_p,), args=(string,))
        manager = send(objc.objc_getClass(b'NSFileManager'), b'defaultManager')
        resulting, error = ctypes.c_void_p(), ctypes.c_void_p()
        success = send(manager, b'trashItemAtURL:resultingItemURL:error:', ctypes.c_bool,
                       (ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p)),
                       (url, ctypes.byref(resulting), ctypes.byref(error)))
        if not success:
            description = send(error, b'localizedDescription') if error else None
            raw = send(description, b'UTF8String', ctypes.c_char_p) if description else None
            raise OSError(raw.decode('utf-8', 'replace') if raw else '系统未确认移入废纸篓。')
        target = send(resulting, b'path') if resulting else None
        raw = send(target, b'UTF8String', ctypes.c_char_p) if target else None
        return {'system': 'macos', 'trash_path': raw.decode('utf-8', 'replace') if raw else None}
    finally:
        if pool:
            send(pool, b'drain', None)


def _windows_trash(path):
    """Use IFileOperation's recycle-only mode (Windows 8+), never FO_DELETE.

    https://learn.microsoft.com/windows/win32/api/shobjidl_core/nf-shobjidl_core-ifileoperation-setoperationflags
    """
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [('data1', wintypes.DWORD), ('data2', wintypes.WORD),
                    ('data3', wintypes.WORD), ('data4', ctypes.c_ubyte * 8)]

        def __init__(self, value):
            super().__init__()
            ctypes.memmove(ctypes.byref(self), uuid.UUID(value).bytes_le, 16)

    ole = ctypes.OleDLL('ole32')
    shell = ctypes.OleDLL('shell32')
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD,
                                    ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole.CoCreateInstance.restype = ctypes.c_long
    shell.SHCreateItemFromParsingName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p,
                                                ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    shell.SHCreateItemFromParsingName.restype = ctypes.c_long

    def checked(hr):
        if hr < 0:
            raise OSError('系统回收站操作失败 (HRESULT 0x%08x)。' % (hr & 0xffffffff))

    def method(pointer, index, *types):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(table[index])

    initialized = ole.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    checked(initialized)
    operation, item = ctypes.c_void_p(), ctypes.c_void_p()
    try:
        checked(ole.CoCreateInstance(ctypes.byref(GUID('3ad05575-8857-4850-9277-11b85bdb8e09')),
                                    None, 1, ctypes.byref(GUID('947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8')),
                                    ctypes.byref(operation)))
        # RECYCLEONDELETE guarantees recycling rather than permanent deletion.
        # EARLYFAILURE turns failures into errors instead of implicit skip/continue.
        flags = 0x00080000 | 0x00100000 | 0x00000400 | 0x00000004 | 0x00000200
        checked(method(operation, 5, wintypes.DWORD)(operation, flags))
        checked(shell.SHCreateItemFromParsingName(str(path), None,
                    ctypes.byref(GUID('43826d1e-e718-42ee-bc55-a1e261c37bfe')), ctypes.byref(item)))
        checked(method(operation, 18, ctypes.c_void_p, ctypes.c_void_p)(operation, item, None))
        checked(method(operation, 21)(operation))
        aborted = wintypes.BOOL()
        checked(method(operation, 22, ctypes.POINTER(wintypes.BOOL))(operation, ctypes.byref(aborted)))
        if aborted.value:
            raise OSError('系统取消了回收站操作；请核对原目录。')
        return {'system': 'windows', 'trash_path': None}
    finally:
        for pointer in (item, operation):
            if pointer:
                method(pointer, 2)(pointer)
        ole.CoUninitialize()


def move_to_trash(path):
    """Move one fully qualified directory to the supported OS trash."""
    path = Path(path)
    if not path.is_absolute() or '\x00' in str(path):
        raise ValueError('回收站操作需要完整的本地目录。')
    if sys.platform == 'darwin':
        return _macos_trash(path)
    if sys.platform == 'win32':
        if sys.getwindowsversion().major < 6 or sys.getwindowsversion().build < 9200:
            raise OSError('系统回收站操作需要 Windows 8 或更新版本。')
        return _windows_trash(path)
    raise OSError('当前系统不支持工作台移入系统回收站；作品目录保持不变。')


class BookManager:
    def __init__(self, workbench):
        self.w, self.api = workbench, workbench.api

    def _entry(self, entries, key):
        if not isinstance(key, str) or key not in entries:
            self.api.fail('invalid_input', '作品未登记在本书架中。')
        return entries[key]

    def _current_registry(self, state_dir, expected_hash):
        if self.w._library_load(state_dir)[1] != expected_hash:
            self.api.fail('workbench_library_changed', '书架已被其他程序修改，请刷新后重试。')

    def _receipt(self, state_dir, receipt):
        identifier = uuid.uuid4().hex
        receipt = {'version': 1, 'id': identifier, **receipt}
        self.api.atomic_write(self.api.safe_path(state_dir, 'operations/' + identifier + '.json'),
                              json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', set(),
                              self.api.safe_path(state_dir, '.backups/' + uuid.uuid4().hex + '/operation.json'))
        return identifier

    def _safe_root(self, root, state_dir=None, entries=None, exclude=None):
        root = Path(os.path.abspath(root))
        protected = {Path(root.anchor), Path.home(), Path.cwd(), Path(__file__).resolve().parent}
        if state_dir is not None:
            protected.add(Path(state_dir))
        # Reject containers of protected locations, not arbitrary books beneath
        # a home folder. A folder containing an unrelated book is handled below.
        if any(root == value or root in value.parents for value in protected):
            self.api.fail('workbench_unsafe_root', '此目录包含应用、书架或用户主目录，不能作为作品移入回收站。', path=str(root))
        for key, entry in (entries or {}).items():
            if key == exclude:
                continue
            other = Path(entry['root'])
            if root == other or root in other.parents or other in root.parents:
                self.api.fail('workbench_nested_book', '作品目录与另一登记作品重叠，请先核对目录范围。', path=str(root), other=str(other))
        for parent in root.parents:
            if (parent / '.story/state.sqlite3').exists():
                self.api.fail('workbench_nested_book', '此目录位于另一作品中，不能独立创建或删除。', path=str(root))
        return root

    def _inspect_tree(self, root):
        count, total = 0, 0
        def cannot_read(error):
            raise error
        for directory, folders, files in os.walk(root, followlinks=False, onerror=cannot_read):
            for name in folders + files:
                path = Path(directory) / name
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    self.api.fail('workbench_linked_path', '作品目录中含链接或连接点，请先核对其范围后再删除。', path=str(path))
                count += 1
                if count > MAX_TREE_ENTRIES:
                    self.api.fail('workbench_tree_limit', '作品目录内容过多，暂不执行自动移入回收站。', path=str(root))
                if name == 'state.sqlite3' and Path(directory).name == '.story' and Path(directory).parent != root:
                    self.api.fail('workbench_nested_book', '作品目录中另含一部作品，请先分离作品目录。', path=str(path.parent.parent))
                if stat.S_ISREG(info.st_mode):
                    total += info.st_size
        return {'entries': count, 'bytes': total}

    def _identity(self, root):
        with self.api._pinned_directory(root) as directory:
            info = os.fstat(directory.fd) if directory.fd is not None else root.stat()
            self.api._verify_bound_directory(directory)
            return '%s:%s' % (info.st_dev, info.st_ino)

    def create(self, state_dir, entries, expected_hash, path, title, kind):
        title = self.api.text_field(title, 'title', 200)
        if kind not in ('long', 'short', 'analysis') or not isinstance(path, str) or not path.strip() or len(path) > 4096:
            self.api.fail('invalid_input', '请填写新作品目录、书名和类型。')
        if len(entries) >= 30:
            self.api.fail('invalid_input', '工作台书架最多登记30本作品。')
        root = self._safe_root(Path(path).expanduser(), state_dir, entries)
        with lifecycle_guard(self.api, root):
            self._current_registry(state_dir, expected_hash)
            with self.api._pinned_directory(root.parent) as parent:
                self.api._verify_bound_directory(parent)
                if root.exists() or root.is_symlink():
                    self.api.fail('workbench_create_exists', '新建作品需要一个尚不存在的目录；已有作品请添加到书架。', path=str(root))
                if parent.fd is not None:
                    os.mkdir(root.name, mode=0o700, dir_fd=parent.fd)
                else:
                    root.mkdir(mode=0o700)
                with self.api._pinned_directory(root) as created:
                    self.api.Book.create(root, title, kind)
                    self.api._verify_bound_directory(created)
                self.api._verify_bound_directory(parent)
            entry = self.w._library_entry(root)
            updated = {**entries, entry['key']: entry}
            try:
                new_hash = self.w._library_store(state_dir, updated, expected_hash)
            except (self.api.StoryError, OSError) as error:
                return entries, expected_hash, {'ok': True, 'action': 'create', 'created': True, 'registered': False,
                    'root': str(root), 'book_id': entry['book_id'], 'warning': '作品已创建，请用“添加已有作品”登记：' + str(error)}
            return updated, new_hash, {'ok': True, 'action': 'create', 'created': True, 'registered': True, 'root': str(root), 'book_id': entry['book_id']}

    def remove(self, state_dir, entries, expected_hash, key):
        entry = self._entry(entries, key)
        self._current_registry(state_dir, expected_hash)
        # An absent or replaced root must remain removable from the shelf. No
        # request is sent to any editor and no file under the root is changed.
        updated = {k: v for k, v in entries.items() if k != key}
        new_hash = self.w._library_store(state_dir, updated, expected_hash)
        result = {'ok': True, 'action': 'remove', 'removed': True, 'root': entry['root'], 'files_changed': False}
        try:
            result['undo_id'] = self._receipt(state_dir, {'action': 'remove', 'entry': entry})
        except (OSError, self.api.StoryError) as error:
            result['warning'] = '已移出书架；撤销记录未写入，仍可重新添加原作品目录：' + str(error)
        return updated, new_hash, result

    def undo_remove(self, state_dir, entries, expected_hash, receipt_id):
        if not isinstance(receipt_id, str) or len(receipt_id) != 32 or any(c not in '0123456789abcdef' for c in receipt_id):
            self.api.fail('invalid_input', '无效的移出记录。')
        try:
            receipt = json.loads(self.w._author_read(state_dir, 'operations/' + receipt_id + '.json', 16384))
            entry = receipt['entry']
            if receipt.get('version') != 1 or receipt.get('id') != receipt_id or receipt.get('action') != 'remove':
                raise ValueError('invalid remove receipt')
            current = self.w._library_entry(entry['root'], entry)
        except (KeyError, TypeError, ValueError, FileNotFoundError):
            self.api.fail('workbench_undo_unavailable', '无法核对此移出记录，请重新添加原作品目录。')
        self._current_registry(state_dir, expected_hash)
        if current['key'] in entries:
            if entries[current['key']]['book_id'] != current['book_id']:
                self.api.fail('workbench_book_changed', '原位置已经登记另一作品，请保留现有登记。')
            return entries, expected_hash, {'ok': True, 'action': 'undo-remove', 'restored': True, 'root': current['root']}
        if len(entries) >= 30:
            self.api.fail('invalid_input', '工作台书架最多登记30本作品。')
        if 'shelf_status' in entry:
            current['shelf_status'] = entry['shelf_status']
        updated = {**entries, current['key']: current}
        new_hash = self.w._library_store(state_dir, updated, expected_hash)
        return updated, new_hash, {'ok': True, 'action': 'undo-remove', 'restored': True, 'root': current['root']}

    def relink(self, state_dir, entries, expected_hash, key, path):
        original = self._entry(entries, key)
        current = self.w._library_entry(path, original)
        if current['key'] == key:
            return entries, expected_hash, {'ok': True, 'action': 'relink', 'root': current['root']}
        if current['key'] in entries:
            self.api.fail('workbench_book_changed', '新目录已登记在书架，请保留现有登记并核对。')
        try:
            self.w._library_entry(original['root'], original)
        except (self.api.StoryError, OSError):
            pass
        else:
            self.api.fail('workbench_original_available', '原目录仍可确认是本书；若是复制，请先保留原登记并核对。')
        self._current_registry(state_dir, expected_hash)
        if 'shelf_status' in original:
            current['shelf_status'] = original['shelf_status']
        updated = {k: v for k, v in entries.items() if k != key}
        updated[current['key']] = current
        self.w._library_entry(current['root'], original)
        new_hash = self.w._library_store(state_dir, updated, expected_hash)
        return updated, new_hash, {'ok': True, 'action': 'relink', 'root': current['root'], 'book_id': current['book_id']}

    def trash_preview(self, state_dir, entries, key):
        original = self._entry(entries, key)
        current = self.w._library_entry(original['root'], original)
        root = self._safe_root(current['root'], state_dir, entries, key)
        counts = self._inspect_tree(root)
        directory_id = self._identity(root)
        self.w._library_entry(root, original)
        return {'ok': True, 'action': 'trash-preview', 'confirmation': {
            'root': str(root), 'title': current['title'], 'book_id': current['book_id'], 'directory_id': directory_id},
            **counts, 'scope': '整个作品目录及其中正文、候选、恢复稿、规划、状态和本地备份；外部材料原目录不会移动。',
            'recovery': '移入系统废纸篓或回收站后，可在系统中恢复，再重新添加作品目录。'}

    def trash(self, state_dir, entries, expected_hash, key, confirmation, saved=False):
        original = self._entry(entries, key)
        if saved is not True:
            self.api.fail('workbench_unsaved', '请先保留全部编辑页面的文字，并确认已保存后再删除作品。')
        preview = self.trash_preview(state_dir, entries, key)
        if confirmation != preview['confirmation']:
            self.api.fail('workbench_delete_confirmation', '作品身份或目录已变化，请重新查看删除范围并确认书名。')
        root = Path(original['root'])
        with lifecycle_guard(self.api, root):
            self._current_registry(state_dir, expected_hash)
            status = self.w._service_request(root)
            if status.get('running') is not False:
                self.api.fail('workbench_running', '本书编辑服务仍在运行或尚未确认停止。请保存所有页面后停止本书服务，再移入回收站。', running=status.get('running'))
            # On Windows an open file inside the directory prevents recycling.
            # The outside guard prevents a new server acquiring the book lease
            # after this check and before the native move.
            with ExitStack() as held:
                windows_handles = held.enter_context(ExitStack())
                windows_handles.enter_context(self.w._editor_lease(root))
                windows_handles.enter_context(_writer_lease(self.api, root))
                if self.trash_preview(state_dir, entries, key)['confirmation'] != confirmation:
                    self.api.fail('workbench_book_changed', '删除前作品目录已改变，未移动任何文件。')
                intent_id = self._receipt(state_dir, {'action': 'trash-intent', 'entry': original, 'confirmation': confirmation})
                if os.name == 'nt':
                    windows_handles.close()
                try:
                    native = move_to_trash(root)
                except OSError as error:
                    self.api.fail('workbench_trash_failed', '系统未确认移入回收站，请核对原目录；工作台不会改为永久删除。', cause=str(error), operation_id=intent_id)
            if root.exists() or root.is_symlink():
                self.api.fail('workbench_trash_unconfirmed', '系统返回后原目录仍存在，请核对原目录与系统回收站；书架登记保留。', operation_id=intent_id)
            result = {'ok': True, 'action': 'trash', 'trashed': True, 'root': str(root),
                      'book_id': original['book_id'], 'operation_id': intent_id, **native,
                      'recovery': preview['recovery']}
            updated = {k: v for k, v in entries.items() if k != key}
            try:
                new_hash = self.w._library_store(state_dir, updated, expected_hash)
            except (self.api.StoryError, OSError) as error:
                result.update(shelf_removed=False, warning='已移入系统回收站，书架登记未更新；请刷新后移出失效登记：' + str(error))
                return entries, expected_hash, result
            result['shelf_removed'] = True
            return updated, new_hash, result
