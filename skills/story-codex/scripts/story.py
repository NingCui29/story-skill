#!/usr/bin/env python3
"""Story Codex: local, bounded context and transactional writing state. No API calls."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import unicodedata
import uuid
import importlib.util
from types import SimpleNamespace

VERSION = "0.5.10"
SCHEMA_VERSION = 2
CHECKS = ("causality", "continuity", "constraints", "style")
KINDS = ("fact", "character", "world", "hook", "preference", "contract")
COUNT_METHODS = ("visible_nonspace_v1", "letters_numbers_v1", "han_v1")


class StoryError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


def fail(code, message, **details):
    raise StoryError(code, message, **details)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def text_field(value, name, maximum=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        fail("invalid_input", f"{name} must be nonempty text, at most {maximum} characters")
    if "<填写" in value or value.strip() in ("TODO", "待补充", "REPLACE_ME"):
        fail("placeholder", f"Fill {name} before saving")
    return value


def filename_component(value, name):
    value = text_field(value, name, 100).strip()
    if (value in (".", "..") or value.endswith(".") or
            re.search(r'[<>:"/\\|?*]', value) or
            any(unicodedata.category(char).startswith("C") for char in value) or
            re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", value) or
            len(value.encode("utf-8")) > 180):
        fail("invalid_input", f"{name} must be a portable filename component")
    return value


def book_text_filename(title):
    """Keep the full title as metadata; choose a stable portable reading-copy name."""
    title = text_field(title, "title", 200)
    replacements = str.maketrans('<>:"/\\|?*', '＜＞：＂／＼｜？＊')
    component = title.translate(replacements)
    component = "".join("_" if unicodedata.category(char).startswith("C") or char in "\u2028\u2029"
                        else char for char in component).strip().rstrip(". ")
    if not component:
        component = "作品-" + digest(title)[:8]
    if re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", component):
        component = "_" + component
    if len(component) > 100 or len(component.encode("utf-8")) > 180:
        suffix = "-" + digest(title)[:8]
        while len(component) + len(suffix) > 100 or len((component + suffix).encode("utf-8")) > 180:
            component = component[:-1]
        component = component.rstrip(". ") + suffix
    return filename_component(component, "book output filename") + ".txt"


VOLUME_DIRECTORY = re.compile(r"(第[0-9０-９零〇一二三四五六七八九十百千万两]+卷)\s+(\S.*)")


def volume_directory(value):
    value = filename_component(value, "plan.volume_dir")
    match = VOLUME_DIRECTORY.fullmatch(value)
    if not match:
        fail("invalid_input", "Volume directory must include its number and title, for example 第一卷 雨夜")
    title = text_field(match[2], "volume title", 100)
    return f"{match[1]} {title}"


def first_chapter_heading(text):
    lines = text.lstrip("\ufeff").splitlines()
    if not lines:
        return None
    first = lines[0]
    markdown = re.match(r"^#\s+(\S.*)$", first)
    if markdown:
        return re.sub(r"(?:^|[ \t]+)#+[ \t]*$", "", markdown[1]).strip()
    if re.match(r"^第[0-9０-９零〇一二三四五六七八九十百千万两]+章[ \t　:：、.．-]+\S", first):
        return first.strip()
    return None


def chapter_filename(chapter, text, plan, imported=False):
    title = plan.get("title")
    if title is None:
        heading = first_chapter_heading(text)
        title = re.sub(r"^第[0-9０-９零〇一二三四五六七八九十百千万两]+章[\s　:：、.．-]*", "", heading or "").strip()
    if not title:
        if not imported:
            fail("chapter_title_missing", "Set plan.title or give the draft a plain 第N章 章节名称 heading")
        title = "正文"
    return f"第{chapter}章 {filename_component(title, 'chapter title')}.md"


def integer(value, name, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        fail("invalid_input", f"{name} must be an integer from {minimum} to {2**63 - 1}")
    return value


def string_list(value, name, maximum=50):
    if not isinstance(value, list) or len(value) > maximum:
        fail("invalid_input", f"{name} must be an array, at most {maximum} entries")
    return list(dict.fromkeys(text_field(v, name, 1200) for v in value))


def object_value(value, name):
    if not isinstance(value, dict):
        fail("invalid_input", f"{name} must be an object")
    return value


def read_text(path, encoding="utf-8-sig"):
    return Path(path).read_bytes().decode(encoding)


def read_json(path):
    return json.loads(read_text(path))


def safe_path(root, relative):
    """Only internally chosen relative paths; reject symlinks and Windows junctions."""
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        fail("path_escape", "Expected a relative path inside the book")
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            fail("linked_path", "Managed paths cannot contain links or junctions", path=str(current))
    try:
        current.resolve().relative_to(root)
    except ValueError:
        fail("path_escape", "Managed path leaves the book", path=str(current))
    return current


@contextmanager
def _windows_path_handle(path, directory=False, write_shared=False):
    """Pin Windows parents against rename/reparse replacement without hard links.

    CreateFileW opens reparse points themselves so they can be rejected. Strict
    directory opens initially deny write/delete sharing. A caller may allow
    write sharing only while a pinned child keeps that directory nonempty;
    delete sharing remains disabled to prevent directory replacement.
    https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-createfilew
    """
    import ctypes
    from ctypes import wintypes

    class FileInformation(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("created", wintypes.FILETIME),
                    ("accessed", wintypes.FILETIME), ("written", wintypes.FILETIME),
                    ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                    ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                    ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    name = str(path)
    if not name.startswith("\\\\?\\"):
        name = "\\\\?\\UNC\\" + name[2:] if name.startswith("\\\\") else "\\\\?\\" + name
    # Metadata-only opens do not participate in share-access checks. Request
    # FILE_TRAVERSE as well as FILE_READ_ATTRIBUTES so withholding share-delete
    # actually pins each directory; no listing, write or delete access is needed.
    handle = kernel.CreateFileW(name, 0xA0 if directory else 0x80000000,
                                (0x3 if write_shared else 0x1) if directory else 0x7, None, 3,
                                0x00200000 | (0x02000000 if directory else 0), None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = FileInformation()
        if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & 0x400:
            fail("linked_path", "Managed paths cannot contain reparse points", path=str(path))
        if bool(info.attributes & 0x10) != directory:
            fail("export_conflict", "Managed path has the wrong file type", path=str(path))
        yield handle
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def _windows_directory_guard(path):
    """Keep the leaf nonempty; close deletes the guard without a path-based race.

    NTFS rejects reparse-point conversion of a nonempty directory. Denying
    delete/write sharing on this child keeps it present and unchanged until
    the enclosing directory pin is released. No hard-link support is needed.
    https://learn.microsoft.com/windows/win32/fileio/reparse-points
    """
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    name = str(path / (".story-pin-" + uuid.uuid4().hex))
    if not name.startswith("\\\\?\\"):
        name = "\\\\?\\UNC\\" + name[2:] if name.startswith("\\\\") else "\\\\?\\" + name
    # DELETE | FILE_READ_ATTRIBUTES, FILE_SHARE_READ, CREATE_NEW;
    # DELETE_ON_CLOSE | OPEN_REPARSE_POINT | TEMPORARY.
    handle = kernel.CreateFileW(name, 0x10080, 0x1, None, 1, 0x04200100, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def _pinned_directory(path, create=False, exclusive=False):
    """Traverse once without following links; keep every parent bound during I/O."""
    from contextlib import ExitStack
    path = Path(os.path.abspath(path))
    if os.name == "nt":
        with ExitStack() as stack:
            current = Path(path.anchor)
            strict = stack.enter_context(ExitStack())
            strict.enter_context(_windows_path_handle(current, directory=True))
            for index, part in enumerate(path.parts[1:]):
                child = current / part
                if create:
                    try:
                        child.mkdir()
                    except FileExistsError:
                        if exclusive and index == len(path.parts) - 2:
                            raise
                child_pin = stack.enter_context(ExitStack())
                child_pin.enter_context(_windows_path_handle(child, directory=True))
                # The child cannot be removed, so its parent cannot become an
                # empty reparse point. Allow rename's internal write access to
                # the parent while continuing to deny deletion of that parent.
                stack.enter_context(_windows_path_handle(current, directory=True, write_shared=True))
                strict.close()
                current, strict = child, child_pin
            stack.enter_context(_windows_directory_guard(current))
            stack.enter_context(_windows_path_handle(current, directory=True, write_shared=True))
            strict.close()
            yield SimpleNamespace(path=path, fd=None)
        return
    if not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW")) or os.open not in os.supports_dir_fd:
        fail("safe_export_unavailable", "This platform cannot bind export directories safely; exports remain pending")
    handles = []
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        handles.append(os.open(path.anchor, flags))
        for index, part in enumerate(path.parts[1:]):
            if create:
                try:
                    os.mkdir(part, dir_fd=handles[-1])
                except FileExistsError:
                    if exclusive and index == len(path.parts) - 2:
                        raise
            handles.append(os.open(part, flags, dir_fd=handles[-1]))
        yield SimpleNamespace(path=path, fd=handles[-1])
    finally:
        for handle in reversed(handles):
            os.close(handle)


class _BoundFile:
    """A displayable path whose operations use its already-pinned parent."""
    def __init__(self, directory, name):
        self.directory, self.name = directory, name
        self.path = directory.path / name

    def __fspath__(self):
        return str(self.path)

    def __str__(self):
        return str(self.path)


def _owned_fdopen(fd, mode):
    try:
        return os.fdopen(fd, mode)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


@contextmanager
def _bound_reader(file):
    import stat
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes
        # Duplicate the verified non-reparse handle before giving ownership to
        # Python's file object; the original remains owned by its context.
        with _windows_path_handle(file.path) as handle:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                              ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                              wintypes.BOOL, wintypes.DWORD]
            kernel.DuplicateHandle.restype = wintypes.BOOL
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.restype = wintypes.BOOL
            process, duplicate = kernel.GetCurrentProcess(), wintypes.HANDLE()
            if not kernel.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                fd = msvcrt.open_osfhandle(duplicate.value, os.O_RDONLY | os.O_BINARY)
            except BaseException:
                kernel.CloseHandle(duplicate)
                raise
            with _owned_fdopen(fd, "rb") as stream:
                yield stream
        return
    fd = os.open(file.name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
                 dir_fd=file.directory.fd)
    with _owned_fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            fail("export_conflict", "Export target is not a regular file", path=str(file))
        yield stream


def _bound_hash(file):
    with _bound_reader(file) as stream:
        value = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
        return value.hexdigest()


def _bound_exists(file):
    try:
        if os.name == "nt":
            file.path.lstat()
        else:
            os.stat(file.name, dir_fd=file.directory.fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _bound_unlink(file):
    if os.name == "nt":
        os.unlink(file.path)
    else:
        os.unlink(file.name, dir_fd=file.directory.fd)


def _bound_stage(directory, prefix):
    for _ in range(10):
        file = _BoundFile(directory, prefix + uuid.uuid4().hex)
        try:
            fd = (os.open(file.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
                  if os.name == "nt" else os.open(file.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                                  0o600, dir_fd=directory.fd))
            return fd, file
        except FileExistsError:
            pass
    fail("export_conflict", "Could not reserve an export staging file", path=str(directory.path))


def _bound_replace(source, target):
    if os.name == "nt":
        os.replace(source.path, target.path)
    else:
        os.replace(source.name, target.name, src_dir_fd=source.directory.fd, dst_dir_fd=target.directory.fd)


def _verify_bound_directory(directory):
    if os.name != "nt":
        with _pinned_directory(directory.path) as current:
            if not os.path.samestat(os.fstat(current.fd), os.fstat(directory.fd)):
                fail("export_conflict", "Managed directory moved during export; preserved versions remain pending",
                     path=str(directory.path))


def _publish_no_replace(source, target):
    """Publish a complete stage without replacing a concurrent editor's path."""
    if isinstance(source, _BoundFile) and isinstance(target, _BoundFile):
        if os.name == "nt":
            os.rename(source.path, target.path)
        else:
            os.link(source.name, target.name, src_dir_fd=source.directory.fd,
                    dst_dir_fd=target.directory.fd, follow_symlinks=False)
    elif os.name == "nt":
        os.rename(source, target)
    else:
        os.link(source, target)


def _restore_displaced_file(backup, target):
    if isinstance(backup, _BoundFile) and isinstance(target, _BoundFile):
        if os.name != "nt":
            _publish_no_replace(backup, target)
            return
        fd, staged = _bound_stage(target.directory, ".story-restore-")
        try:
            with _owned_fdopen(fd, "wb") as handle, _bound_reader(backup) as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            _publish_no_replace(staged, target)
        finally:
            if _bound_exists(staged):
                _bound_unlink(staged)
        return
    if os.name != "nt":
        _publish_no_replace(backup, target)
        return
    fd, staged = tempfile.mkstemp(prefix=".story-restore-", dir=target.parent)
    try:
        with _owned_fdopen(fd, "wb") as handle:
            handle.write(backup.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        _publish_no_replace(staged, target)
    finally:
        if os.path.exists(staged):
            os.unlink(staged)


def atomic_write(path, content, allowed_hashes, backup):
    """Publish through pinned parents; retain displaced versions for recovery.

    POSIX operations use no-follow directory handles. Windows prevents parent
    deletion and reparse conversion while permitting child-file rename; initial
    publication never replaces an existing target or requires hard links.
    If a parent cannot be pinned, nothing is displaced.
    """
    from contextlib import ExitStack
    path, backup = Path(path), Path(backup)
    with ExitStack() as stack:
        parent = stack.enter_context(_pinned_directory(path.parent, create=True))
        target = _BoundFile(parent, path.name)
        fd, tmp = _bound_stage(parent, ".story-tmp-")
        displaced, saved = False, None
        try:
            with _owned_fdopen(fd, "wb") as handle:
                handle.write(content.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            if _bound_exists(target):
                backup_parent = stack.enter_context(_pinned_directory(backup.parent, create=True, exclusive=True))
                saved = _BoundFile(backup_parent, backup.name)
                _bound_replace(target, saved)
                displaced = True
                if _bound_hash(saved) not in allowed_hashes:
                    fail("export_conflict", "File changed during export; displaced version preserved",
                         path=str(path), backup=str(backup))
            _publish_no_replace(tmp, target)
            if _bound_hash(target) != digest(content):
                fail("export_conflict", "File changed during publication; preserve it and reconcile", path=str(path))
            _verify_bound_directory(parent)
            if saved is not None:
                _verify_bound_directory(saved.directory)
            return str(backup) if displaced else None
        except (OSError, StoryError) as error:
            if displaced:
                try:
                    _restore_displaced_file(saved, target)
                except (OSError, StoryError):
                    pass
                if isinstance(error, StoryError):
                    error.details.setdefault("backup", str(backup))
                else:
                    fail("export_io", str(error), path=str(path), backup=str(backup))
            raise
        finally:
            if _bound_exists(tmp):
                _bound_unlink(tmp)


def _retire_bound_file(target, backup, allowed_hashes):
    """Move a retired chapter without following a swapped source/backup parent."""
    from contextlib import ExitStack
    with ExitStack() as stack:
        try:
            source_parent = stack.enter_context(_pinned_directory(target.parent))
        except FileNotFoundError:
            # A genuinely absent parent contains no remaining export. A link
            # substituted at that path fails no-follow traversal instead.
            return None
        source = _BoundFile(source_parent, target.name)
        if not _bound_exists(source):
            _verify_bound_directory(source_parent)
            return None
        with _pinned_directory(backup.parent, create=True, exclusive=True) as backup_parent:
            saved = _BoundFile(backup_parent, backup.name)
            _bound_replace(source, saved)
            try:
                if _bound_hash(saved) not in allowed_hashes:
                    fail("export_conflict", "Old chapter changed during rename; preserve it and reconcile",
                         path=str(target), backup=str(backup))
                if _bound_exists(source):
                    fail("export_conflict", "Old chapter path was recreated during rename", path=str(target), backup=str(backup))
                _verify_bound_directory(source_parent)
                _verify_bound_directory(backup_parent)
            except (OSError, StoryError) as error:
                try:
                    _restore_displaced_file(saved, source)
                except (OSError, StoryError):
                    pass
                if isinstance(error, StoryError):
                    error.details.setdefault("backup", str(backup))
                    raise
                fail("export_io", str(error), path=str(target), backup=str(backup))
            return str(backup)


def bounded_packet(packet, limit):
    integer(limit, "budget_bytes", 256)
    packet["budget"] = {"unit": "utf8_bytes", "limit": limit, "used": 0}
    for _ in range(8):
        used = len(dumps(packet).encode("utf-8"))
        if packet["budget"]["used"] == used:
            break
        packet["budget"]["used"] = used
    if used > limit:
        fail("budget_exceeded", "Required content cannot fit; increase the budget or narrow the range",
             minimum_bytes=used, budget_bytes=limit)
    return packet


def valid_card(raw):
    raw = object_value(raw, "card")
    cid = text_field(raw.get("id"), "card.id", 80)
    if not re.fullmatch(r"[\w.-]+", cid):
        fail("invalid_input", "card.id accepts letters, digits, underscore, dot and hyphen")
    kind = raw.get("kind", "fact")
    if kind not in KINDS:
        fail("invalid_input", f"card.kind must be one of {KINDS}")
    if type(raw.get("critical", False)) is not bool:
        fail("invalid_input", "card.critical must be boolean")
    status = raw.get("status", "active")
    if status not in ("active", "resolved"):
        fail("invalid_input", "card.status must be active or resolved")
    due = raw.get("due")
    if due is not None:
        integer(due, "card.due", 1)
    result = {"id": cid, "kind": kind, "text": text_field(raw.get("text"), "card.text"),
            "source": text_field(raw.get("source"), "card.source", 1400),
            "tags": string_list(raw.get("tags", []), "card.tags", 24),
            "critical": raw.get("critical", False), "status": status, "due": due}
    if "scope" in raw:
        scope = text_field(raw["scope"], "card.scope", 180)
        if scope != "global" and not re.fullmatch(r"(volume|arc|line|entity):[\w.-]+", scope):
            fail("invalid_input", "scope must be global or volume/arc/line/entity:ID")
        result["scope"] = scope
    return result


def valid_plan(raw):
    raw = object_value(raw, "plan")
    result = {"goal": text_field(raw.get("goal"), "plan.goal"),
              "stop": text_field(raw.get("stop"), "plan.stop"),
              "constraints": string_list(raw.get("constraints", []), "plan.constraints"),
              "requires": string_list(raw.get("requires", []), "plan.requires"),
              "tags": string_list(raw.get("tags", []), "plan.tags", 24)}
    beats = raw.get("beats")
    if not isinstance(beats, list) or not 1 <= len(beats) <= 40:
        fail("invalid_input", "plan.beats needs 1 to 40 meaningful scenes")
    result["beats"] = []
    for beat in beats:
        object_value(beat, "beat")
        result["beats"].append({key: text_field(beat.get(key), f"beat.{key}")
                                for key in ("choice", "change")})
    length = raw.get("length")
    if not isinstance(length, list) or len(length) != 2:
        fail("invalid_input", "plan.length needs [minimum_visible_chars, maximum_visible_chars]")
    lo, hi = (integer(x, "plan.length", 1) for x in length)
    if lo > hi or hi > 200000:
        fail("invalid_input", "Invalid plan length range")
    result["length"] = [lo, hi]
    method = raw.get("count_method", "visible_nonspace_v1")
    if method not in COUNT_METHODS:
        fail("invalid_input", f"plan.count_method must be one of {COUNT_METHODS}")
    include_title = raw.get("count_title", False)
    if type(include_title) is not bool:
        fail("invalid_input", "plan.count_title must be boolean")
    result.update(count_method=method, count_title=include_title)
    if "title" in raw:
        result["title"] = filename_component(raw["title"], "plan.title")
    if "volume_dir" in raw:
        result["volume_dir"] = volume_directory(raw["volume_dir"])
    for key in ("volume", "arc", "line"):
        if key in raw:
            result[key] = text_field(raw[key], "plan." + key, 80)
    if "entities" in raw:
        result["entities"] = string_list(raw["entities"], "plan.entities", 40)
    if "time" in raw:
        stamp = object_value(raw["time"], "plan.time")
        result["time"] = {"clock": text_field(stamp.get("clock"), "plan.time.clock", 80)}
        for key in ("start", "end"):
            value = stamp.get(key)
            if value is not None and type(value) is not int:
                fail("invalid_input", "Story time must be integer ticks or null")
            result["time"][key] = value
        if stamp.get("start") is not None and stamp.get("end") is not None and stamp["end"] < stamp["start"]:
            fail("invalid_input", "Story time end precedes start")
    return result


def manuscript_counts(text, include_title=False):
    """Declared character counts, never platform word counts or model tokens."""
    lines = text.splitlines()
    title_text = first_chapter_heading(text)
    if title_text is not None:
        lines = ([title_text] if include_title else []) + lines[1:]
    chars = [c for c in "\n".join(lines)
             if not c.isspace() and unicodedata.category(c) not in ("Cc", "Cf")]
    return {"visible_nonspace_v1": len(chars),
            "letters_numbers_v1": sum(unicodedata.category(c)[0] in "LN" for c in chars),
            "han_v1": sum(c == "〇" or unicodedata.name(c, "").startswith(
                ("CJK UNIFIED IDEOGRAPH-", "CJK COMPATIBILITY IDEOGRAPH-")) for c in chars)}


def visible_count(text):
    return manuscript_counts(text)["visible_nonspace_v1"]


def lint_text(text, plan):
    method = plan.get("count_method", "visible_nonspace_v1")
    if method not in COUNT_METHODS:
        fail("invalid_input", f"Unknown count method: {method}")
    counts = manuscript_counts(text, plan.get("count_title", False))
    count = counts[method]
    errors, warnings = [], []
    if not plan["length"][0] <= count <= plan["length"][1]:
        errors.append({"code": "length", "actual": count, "expected": plan["length"]})
    if "\ufffd" in text or "\x00" in text:
        errors.append({"code": "corrupt_text"})
    if re.search(r"<填写[^>]*>|(?m:^\s*REPLACE_ME\s*$)", text):
        errors.append({"code": "placeholder"})
    paragraphs = [p.strip() for p in text.splitlines() if len(p.strip()) >= 20]
    repeated = [{"text": p[:120], "count": n} for p, n in Counter(paragraphs).items() if n > 1]
    if repeated:
        warnings.append({"code": "repeated_paragraph", "examples": repeated[:6],
                         "note": "Editorial signal only; repetition can be intentional"})
    return {"ok": not errors, "visible_chars": counts["visible_nonspace_v1"],
            "length_count": count, "count_method": method, "counts": counts,
            "count_scope": "body_and_lead_with_title_text" if plan.get("count_title", False) else "body_and_lead_without_first_title",
            "unicode_version": unicodedata.unidata_version,
            "draft_sha256": digest(text), "errors": errors, "warnings": warnings}


SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE cards(id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE plans(chapter INTEGER PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT, revision INTEGER NOT NULL,
 kind TEXT NOT NULL, data TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE sources(id TEXT PRIMARY KEY, name TEXT NOT NULL, text TEXT NOT NULL,
 coverage TEXT NOT NULL, encoding TEXT NOT NULL);
CREATE TABLE chunks(source TEXT NOT NULL REFERENCES sources(id), ordinal INTEGER NOT NULL,
 start INTEGER NOT NULL, end INTEGER NOT NULL, title TEXT NOT NULL, sha TEXT NOT NULL, analysis TEXT,
 PRIMARY KEY(source,ordinal));
"""


def index_card(db, card):
    cid = card["id"]
    db.execute("INSERT INTO card_index VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
               "kind=excluded.kind,status=excluded.status,critical=excluded.critical,due=excluded.due,scope=excluded.scope",
               (cid, card["kind"], card["status"], int(card["critical"]), card["due"], card.get("scope", "global")))
    db.execute("DELETE FROM card_tags WHERE id=?", (cid,))
    db.executemany("INSERT INTO card_tags VALUES (?,?)", ((tag, cid) for tag in card["tags"]))
    search.upsert(db, "card", cid, dumps(card), {"id": cid})


def compact_event(db, kind, payload):
    if kind not in ("commit_chapter", "replace_chapter"):
        return payload
    payload = dict(payload)
    def body_ref(value):
        value = dict(value)
        if "text" in value:
            body = value.pop("text")
            sha = digest(body)
            db.execute("INSERT OR IGNORE INTO core_objects VALUES (?,?)", (sha, body))
            value["body_sha256"] = sha
        return value
    payload = body_ref(payload)
    if payload.get("previous"):
        payload["previous"] = body_ref(payload["previous"])
    return payload


class Book:
    fail = staticmethod(fail)
    safe_path = staticmethod(safe_path)

    def __init__(self, root, integrity="strict"):
        if integrity not in ("strict", "local"):
            fail("invalid_input", "integrity must be strict or local")
        self.integrity = integrity
        self._verified_paths = 0
        self.root = Path(root).expanduser().resolve()
        self.path = safe_path(self.root, ".story/state.sqlite3")
        if not self.path.is_file():
            fail("book_missing", "No Story Codex state here; initialize an explicit book directory",
                 book=str(self.root))
        self.db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA synchronous=FULL")
        try:
            schema = self.meta("schema")
        except BaseException:
            self.db.close()
            raise
        if schema != SCHEMA_VERSION:
            self.db.close()
            fail("schema_mismatch", "Run migrate on a backed-up book copy to upgrade; do not overwrite the database", actual=schema, required=SCHEMA_VERSION)

    def close(self):
        self.db.close()

    @contextmanager
    def read_snapshot(self):
        """Keep composite reads consistent without ending a caller's transaction."""
        owns_read = not self.db.in_transaction
        if owns_read:
            self.db.execute("BEGIN")
        try:
            yield
        finally:
            if owns_read:
                self.db.rollback()

    @classmethod
    def create(cls, root, title, kind):
        title = text_field(title, "title", 200)
        if kind not in ("long", "short", "analysis"):
            fail("invalid_input", "Unsupported book kind")
        root = Path(root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        path = safe_path(root, ".story/state.sqlite3")
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb"):
                pass
        except FileExistsError:
            fail("book_exists", "State already exists; use status, never reinitialize", book=str(root))
        db = sqlite3.connect(path)
        try:
            with db:
                # Keep all schema and identity writes in one durable transaction.
                db.execute("BEGIN IMMEDIATE")
                storage.execute_schema(db, SCHEMA + storage.SCHEMA + search.SCHEMA + world.SCHEMA + history.SCHEMA)
                values = {"schema": SCHEMA_VERSION, "revision": 0, "last_chapter": 0,
                          "imported_through": 0, "title": title, "kind": kind, "id": str(uuid.uuid4())}
                if kind == "short":
                    values["short_assembly_path"] = book_text_filename(title)
                db.executemany("INSERT INTO meta VALUES (?,?)", [(k, dumps(v)) for k, v in values.items()])
        finally:
            db.close()
        return {"created": str(root), **values}

    def meta(self, key):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if not row:
            fail("state_corrupt", "Missing required metadata", key=key)
        return json.loads(row[0])

    def set_meta(self, key, value):
        self.db.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, dumps(value)))

    @contextmanager
    def transaction(self, expected=None):
        with storage.operation_lock(self):
            self.db.execute("BEGIN IMMEDIATE")
            try:
                if expected is not None and integer(expected, "expected revision") != self.meta("revision"):
                    fail("stale_revision", "State changed; reload context and recheck the draft",
                         expected=expected, actual=self.meta("revision"))
                yield
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def event(self, kind, payload):
        rev = self.meta("revision") + 1
        self.set_meta("revision", rev)
        self.db.execute("INSERT INTO events(revision,kind,data) VALUES (?,?,?)", (rev, kind, dumps(compact_event(self.db, kind, payload))))
        return rev

    def cards(self, ids=None):
        if ids is None:
            rows = self.db.execute("SELECT id,data FROM cards ORDER BY id")
        else:
            ids = list(set(ids))
            if not ids:
                return {}
            rows = self.db.execute("SELECT id,data FROM cards WHERE id IN (" + ",".join("?" for _ in ids) + ")", ids)
        return {row[0]: json.loads(row[1]) for row in rows}

    def intern_body(self, text):
        sha = digest(text)
        self.db.execute("INSERT OR IGNORE INTO core_objects VALUES (?,?)", (sha, text))
        return sha

    def index_chapter(self, chapter, text, summary):
        search.upsert(self.db, "chapter", str(chapter), text, {"chapter": chapter, "summary": summary})
        search.upsert(self.db, "chapter_summary", str(chapter), summary, {"chapter": chapter})

    def put_card(self, card):
        self.db.execute("INSERT INTO cards VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                        (card["id"], dumps(card)))
        index_card(self.db, card)

    def delete_card(self, cid):
        search.remove(self.db, "card", cid)
        self.db.execute("DELETE FROM cards WHERE id=?", (cid,))

    def get_plan(self, chapter):
        row = self.db.execute("SELECT data FROM plans WHERE chapter=?", (chapter,)).fetchone()
        if not row:
            fail("plan_missing", "Save a concrete chapter plan before drafting", chapter=chapter)
        return json.loads(row[0])

    def save_notes(self, payload, expected):
        if not isinstance(payload, list) or len(payload) > 200:
            fail("invalid_input", "notes input must be an array of at most 200 cards")
        raw_cards = [object_value(card, "card") for card in payload]
        ids = [text_field(card.get("id"), "card.id", 80) for card in raw_cards]
        if len(set(ids)) != len(ids):
            fail("duplicate_id", "Duplicate card ids in the same batch")
        with self.transaction(expected):
            old = self.cards(ids)
            cards = [valid_card({**old.get(cid, {}), **raw}) for cid, raw in zip(ids, raw_cards)]
            changes = [card for card in cards if old.get(card["id"]) != card]
            if changes:
                for card in changes:
                    self.put_card(card)
                self.event("notes", {"before": {c["id"]: old.get(c["id"]) for c in changes}, "after": changes})
            revision = self.meta("revision")
        return {"updated": len(changes), "revision": revision}

    def save_plan(self, chapter, payload, expected):
        integer(chapter, "chapter", 1)
        plan = valid_plan(payload)
        with self.transaction(expected):
            old = self.db.execute("SELECT data FROM plans WHERE chapter=?", (chapter,)).fetchone()
            if not old or old[0] != dumps(plan):
                self.db.execute("INSERT INTO plans VALUES (?,?) ON CONFLICT(chapter) DO UPDATE SET data=excluded.data",
                                (chapter, dumps(plan)))
                self.event("plan", {"chapter": chapter, "before": json.loads(old[0]) if old else None, "after": plan})
            revision = self.meta("revision")
        return {"chapter": chapter, "revision": revision}

    def _check_artifact(self, relative, new_sha, old_sha):
        target = safe_path(self.root, relative)
        current = None
        if target.exists():
            if not target.is_file():
                fail("export_conflict", "Export target is not a regular file", path=str(target))
            current = hashlib.sha256(target.read_bytes()).hexdigest()
            if current not in {new_sha, old_sha}:
                fail("export_conflict", "Export file was edited outside the state tool; preserve it and reconcile",
                     path=str(target))
        self._last_artifact_check = (relative, current)
        return target

    def queue_artifact(self, relative, content, accepted_sha=None):
        row = self.db.execute("SELECT written_sha FROM artifacts WHERE path=?", (relative,)).fetchone()
        self._check_artifact(relative, digest(content), accepted_sha or (row[0] if row else None))
        self.db.execute("INSERT INTO artifacts(path,content,sha) VALUES (?,?,?)",
                        (relative, content, digest(content)))
        if accepted_sha:
            # Persist the reviewed disk version for post-commit export retries.
            self.db.execute("UPDATE artifacts SET written_sha=? WHERE path=?", (accepted_sha, relative))

    def chapter_path(self, chapter):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (f"chapter_path:{chapter}",)).fetchone()
        return json.loads(row[0]) if row else f"chapters/{chapter:04d}.md"

    def chapter_volume(self, plan, current_directory=None):
        volume = plan.get("volume_dir")
        vid = plan.get("volume")
        stored = self.db.execute("SELECT value FROM meta WHERE key=?", ("volume_dir:" + vid,)).fetchone() if vid else None
        assigned = json.loads(stored[0]) if stored else None
        if volume is None and vid:
            record = self.db.execute("SELECT title FROM world_volumes WHERE id=?", (vid,)).fetchone()
            volume = assigned if assigned is not None else (record[0] if record else None)
        if volume is None and not vid:
            volume = current_directory
        if volume is None or ("volume_dir" not in plan and not VOLUME_DIRECTORY.fullmatch(volume)):
            fail("volume_title_missing", "Set a named volume directory before saving prose, for example plan.volume_dir = 第一卷 雨夜")
        volume = volume_directory(volume)
        if assigned and VOLUME_DIRECTORY.fullmatch(assigned) and volume != assigned:
            fail("volume_directory_conflict", "This volume already has a directory; keep its assigned name",
                 volume=vid, expected=assigned, requested=volume)
        parent = safe_path(self.root, "chapters")
        target = safe_path(self.root, Path("chapters") / volume)
        if parent.exists() and not parent.is_dir():
            fail("export_conflict", "The prose directory is occupied by a file", path=str(parent))
        if target.exists():
            if not target.is_dir():
                fail("export_conflict", "The volume directory is occupied by a file", path=str(target))
            # Case/Unicode aliases on an insensitive filesystem must not give
            # different chapter records different names for the same directory.
            known = self.db.execute("SELECT 1 FROM meta WHERE key=?", ("volume_path:" + volume,)).fetchone()
            if not known:
                for registered in self.db.execute("SELECT value FROM meta WHERE key GLOB 'volume_path:*'"):
                    name = json.loads(registered[0])
                    existing = safe_path(self.root, Path("chapters") / name)
                    if name != volume and existing.exists() and existing.samefile(target):
                        fail("export_path_alias", "This directory already belongs to a registered volume path",
                             expected=name, requested=volume)
                if not any(entry.name == volume for entry in parent.iterdir()):
                    fail("export_path_alias", "Use the existing spelling of this volume directory", requested=volume)
        return volume

    def chapter_destination(self, chapter, text, plan, imported=False):
        previous = self.chapter_path(chapter)
        old = self.db.execute("SELECT 1 FROM artifact_state WHERE path=?", (previous,)).fetchone()
        # Read-only resolution is shared by review preparation and publication.
        if old and previous == f"chapters/{chapter:04d}.md":
            return previous
        volume = self.chapter_volume(plan, Path(previous).parent.name if old else None)
        return f"chapters/{volume}/{chapter_filename(chapter, text, plan, imported)}"

    def _chapter_target(self, chapter, text, plan=None, accepted_sha=None, imported=False, external=None):
        plan = plan or {}
        previous = self.chapter_path(chapter)
        old = self.db.execute("SELECT sha,written_sha FROM artifact_state WHERE path=?", (previous,)).fetchone()
        external_relative = Path(external["path"]).relative_to(self.root).as_posix() if external else None
        if external_relative == previous:
            accepted_sha = external["sha256"]
        relative = self.chapter_destination(chapter, text, plan, imported)
        if old and previous != relative:
            source, target = Path(), Path()
            for old_part, new_part in zip(Path(previous).parts, Path(relative).parts):
                source, target = source / old_part, target / new_part
                old_path, new_path = safe_path(self.root, source), safe_path(self.root, target)
                # WindowsPath equality folds case; compare the stored spelling first.
                if source.parts != target.parts and old_path.exists() and new_path.exists() and old_path.samefile(new_path):
                    fail("export_path_alias", "These names refer to the same file or directory; rename through a distinct intermediate name",
                         previous=previous, requested=relative)
        reused_row = self.db.execute("SELECT value FROM meta WHERE key=?", ("chapter_retired:" + relative,)).fetchone()
        reused = json.loads(reused_row[0]) if reused_row else None
        target = safe_path(self.root, relative)
        if relative != previous and not reused and target.is_file() and target.stat().st_nlink > 1:
            fail("export_path_alias", "A new chapter target must not share an inode with another file", path=str(target))
        target_sha = accepted_sha if relative == previous else None
        if reused and reused["destination"] == previous:
            target_sha = reused["written_sha"] or reused["sha"]
        if reused and reused["destination"] == previous and external_relative == relative:
            target_sha = external["sha256"]
        return previous, old, relative, reused, target_sha, accepted_sha

    def queue_chapter(self, chapter, text, plan=None, accepted_sha=None, imported=False):
        plan = plan or {}
        previous, old, relative, reused, target_sha, accepted_sha = self._chapter_target(
            chapter, text, plan, accepted_sha, imported)
        self.queue_artifact(relative, text, accepted_sha=target_sha)
        if reused and reused["destination"] == previous:
            # A failed rename can be reconciled back to its old name. It is now
            # the live export again, so its pending retirement must be cancelled.
            self.db.execute("DELETE FROM meta WHERE key=?", ("chapter_retired:" + relative,))
        if old and relative != previous:
            self._check_artifact(previous, old["sha"], accepted_sha or old["written_sha"])
            self.set_meta("chapter_retired:" + previous,
                          {"path": previous, "sha": old["sha"], "written_sha": accepted_sha or old["written_sha"],
                           "destination": relative})
            self.db.execute("DELETE FROM artifact_state WHERE path=?", (previous,))
        for retired in self._retired_chapters():
            if retired["destination"] == previous and previous != relative:
                retired["destination"] = relative
                self.set_meta("chapter_retired:" + retired["path"], retired)
        self.set_meta(f"chapter_path:{chapter}", relative)
        if len(Path(relative).parts) == 3:
            volume = Path(relative).parent.name
            self.set_meta("volume_path:" + volume, volume)
            if plan.get("volume"):
                self.set_meta("volume_dir:" + plan["volume"], volume)
        return relative

    def _retired_chapters(self):
        return [json.loads(row[0]) for row in self.db.execute(
            "SELECT value FROM meta WHERE key GLOB 'chapter_retired:*' ORDER BY key")]

    def _retire_chapter(self, row):
        # Publish the new name before moving the old inode into retained backups.
        destination = self.db.execute("SELECT sha FROM artifact_state WHERE path=?", (row["destination"],)).fetchone()
        if not destination:
            fail("state_corrupt", "Missing renamed chapter destination", path=row["destination"])
        self._check_artifact(row["destination"], destination["sha"], None)
        if self._last_artifact_check[1] != destination["sha"]:
            fail("export_pending", "Recover the renamed chapter before retiring its old path", path=row["destination"])
        if row["path"] == row["destination"]:
            # Recover self-retirement records left by an interrupted older runtime.
            with self.transaction():
                self.db.execute("DELETE FROM meta WHERE key=?", ("chapter_retired:" + row["path"],))
            return None
        target = self._check_artifact(row["path"], row["sha"], row["written_sha"])
        backup = safe_path(self.root, f".story/export-backups/{uuid.uuid4().hex}/{row['path']}")
        saved = _retire_bound_file(target, backup, {row["sha"], row["written_sha"]})
        with self.transaction():
            self.db.execute("DELETE FROM meta WHERE key=?", ("chapter_retired:" + row["path"],))
        return saved

    def _artifact_has_alias(self, relative):
        target = safe_path(self.root, relative)
        try:
            return target.stat().st_nlink > 1
        except FileNotFoundError:
            return False

    def _artifact_rows(self):
        if self.integrity == "strict":
            return self.db.execute("SELECT path,sha,written_sha FROM artifact_state ORDER BY path").fetchall()
        paths = set(getattr(self, "_local_artifacts", []))
        assembly = self._short_assembly_record()
        if assembly:
            paths.add(assembly["path"])
        paths.add(self.chapter_path(self.meta("last_chapter")))
        marks = ",".join("?" for _ in paths)
        return self.db.execute("SELECT path,sha,written_sha FROM artifact_state WHERE "
            f"path IN ({marks}) UNION SELECT path,sha,written_sha FROM artifact_state "
            "WHERE written_sha IS NULL OR written_sha<>sha ORDER BY path", tuple(paths)).fetchall()

    def _integrity_report(self):
        total = self.db.execute("SELECT count(*) FROM artifact_state").fetchone()[0]
        audit = self.db.execute("SELECT revision FROM integrity_audits WHERE id=1").fetchone()
        return {"mode": self.integrity, "verified_file_count": self._verified_paths,
                "unverified_archive_count": max(0, total-self._verified_paths),
                "last_full_audit_revision": audit[0] if audit else None,
                "full_book_verified": self.integrity == "strict" and getattr(self, "_health_clean", False),
                "note": "Hash verification is a point-in-time observation; outside editors can save again."}

    def export(self, safe_only=False):
        written, backups, errors = [], [], {}
        def remember(relative, error):
            item = errors.setdefault(relative, {"path": relative, "message": str(error),
                "code": error.code if isinstance(error, StoryError) else
                ("sqlite_error" if isinstance(error, sqlite3.Error) else "io_error")})
            if isinstance(error, StoryError):
                item.setdefault("details", {}).update(error.details)
        # The OS lock serializes compliant state writers. File I/O does not hold a
        # SQLite write transaction; the expected SHA is rechecked before acknowledgement.
        with storage.operation_lock(self):
            try:
                self._refresh_short_assembly()
            except (OSError, StoryError, sqlite3.Error) as error:
                if not safe_only:
                    raise
                remember(self._short_assembly_record()["path"], error)
            rows = self._artifact_rows()
            snapshots = {}
            retired = self._retired_chapters()
            for row in retired:
                if row["path"] == row["destination"]:
                    continue
                try:
                    self._check_artifact(row["path"], row["sha"], row["written_sha"])
                except (OSError, StoryError, sqlite3.Error) as error:
                    if not safe_only:
                        raise
                    remember(row["path"], error)
            for row in rows:
                try:
                    self._check_artifact(row["path"], row["sha"], row["written_sha"])
                    snapshots[row["path"]] = self._last_artifact_check[1]
                except (OSError, StoryError, sqlite3.Error) as error:
                    if not safe_only:
                        raise
                    remember(row["path"], error)
            for row in rows:
                relative = row["path"]
                if relative in errors:
                    continue
                try:
                    if snapshots[relative] != row["sha"] or self._artifact_has_alias(relative):
                        target = self._check_artifact(relative, row["sha"], row["written_sha"])
                        content = self.db.execute("SELECT text FROM core_objects WHERE sha=?", (row["sha"],)).fetchone()[0]
                        backup = safe_path(self.root, f".story/export-backups/{uuid.uuid4().hex}/{relative}")
                        saved = atomic_write(target, content, {row["sha"], row["written_sha"]}, backup)
                        if saved:
                            backups.append(saved)
                        written.append(relative)
                    if row["written_sha"] != row["sha"]:
                        with self.transaction():
                            live = self.db.execute("SELECT sha FROM artifact_state WHERE path=?", (relative,)).fetchone()
                            if not live or live[0] != row["sha"]:
                                fail("stale_export", "Queued content changed during export; retry", path=relative)
                            self.db.execute("UPDATE artifact_state SET written_sha=? WHERE path=?", (row["sha"], relative))
                except (OSError, StoryError, sqlite3.Error) as error:
                    if not safe_only:
                        raise
                    remember(relative, error)
            for row in retired:
                if row["path"] in errors or row["destination"] in errors:
                    continue
                try:
                    saved = self._retire_chapter(row)
                    if saved:
                        backups.append(saved)
                except (OSError, StoryError, sqlite3.Error) as error:
                    if not safe_only:
                        raise
                    remember(row["path"], error)
            # A second pass catches edits of an early file while later files were processed.
            pending, changed = [], []
            for row in rows:
                try:
                    self._check_artifact(row["path"], row["sha"], row["written_sha"])
                    # A matching file does not clear a failed publication or acknowledgement.
                    if (row["path"] in errors or self._last_artifact_check[1] != row["sha"] or
                            self._artifact_has_alias(row["path"])):
                        pending.append(row["path"])
                except (OSError, StoryError, sqlite3.Error) as error:
                    changed.append(row["path"])
                    remember(row["path"], error)
                    if not safe_only:
                        raise
            for row in self._retired_chapters():
                if row["path"] in errors and errors[row["path"]]["code"] != "export_pending":
                    changed.append(row["path"])
                else:
                    pending.append(row["path"])
            assembly = self._short_assembly_status()
            if assembly and assembly["state"] != "current":
                unresolved = changed if assembly["state"] == "changed" else pending
                if assembly["path"] not in unresolved:
                    unresolved.append(assembly["path"])
            self._verified_paths = len(rows)
            clean = not pending and not changed
            self._health_clean = clean
            if not clean and not safe_only:
                fail("export_conflict", "Files changed or disappeared during export", pending=pending[:20], changed=changed[:20])
            if clean and self.integrity == "strict":
                with self.transaction():
                    self.db.execute("INSERT OR REPLACE INTO integrity_audits(id,revision,checked) VALUES(1,?,?)",
                                    (self.meta("revision"), len(rows)))
            result = {"exported": written, "backups": backups,
                      "exports_complete": clean if self.integrity == "strict" else (False if not clean else None),
                      "scope_exports_complete": clean, "integrity": self._integrity_report()}
            if assembly:
                result["short_assembly"] = assembly
            if safe_only:
                result.update(safe_only=True, pending_exports=pending[:20], pending_export_count=len(pending),
                    changed_exports=changed[:20], changed_export_count=len(changed),
                    export_errors=list(errors.values())[:20], export_error_count=len(errors))
                if not clean:
                    result["recovery"] = "Resolve remaining paths; use reconcile for an outside edit of the latest chapter. For an edited book-title copy, preserve it outside its managed path, then retry export; adopt its changes through chapter review."
            return result

    def _short_assembly_record(self):
        row = self.db.execute("SELECT value FROM meta WHERE key='short_assembly'").fetchone()
        return json.loads(row[0]) if row else None

    def short_assembly_path(self):
        record = self._short_assembly_record()
        if record:
            return record["path"]
        row = self.db.execute("SELECT value FROM meta WHERE key='short_assembly_path'").fetchone()
        return json.loads(row[0]) if row else book_text_filename(self.meta("title"))

    def _short_assembly_sources(self):
        rows = self.db.execute("SELECT chapter,sha FROM chapter_state ORDER BY chapter").fetchall()
        return {"numbers": [row["chapter"] for row in rows],
                "chapter_shas": [row["sha"] for row in rows],
                "chapter_paths": [self.chapter_path(row["chapter"]) for row in rows]}

    def _short_assembly_status(self):
        record = self._short_assembly_record()
        if not record:
            return None
        sources = self._short_assembly_sources()
        last = self.meta("last_chapter")
        source_current = (record.get("format") == 1 and last > 0 and
            sources["numbers"] == list(range(1, last + 1)) and
            all(record.get(key) == sources[key] for key in ("chapter_shas", "chapter_paths")))
        row = self.db.execute("SELECT sha,written_sha FROM artifact_state WHERE path=?",
                              (record["path"],)).fetchone()
        registered = bool(row and row["sha"] == record["sha256"])
        state = "current"
        try:
            self._check_artifact(record["path"], record["sha256"], row["written_sha"] if row else None)
            if self._last_artifact_check[1] is None:
                state = "missing"
            elif not source_current or not registered:
                state = "stale"
            elif (self._last_artifact_check[1] != row["sha"] or row["written_sha"] != row["sha"] or
                    self._artifact_has_alias(record["path"])):
                state = "pending"
        except (OSError, StoryError):
            state = "changed"
        return {"path": record["path"], "state": state, "source_current": source_current,
                "chapters": len(record["chapter_shas"]), "source_chapters": last,
                "sha256": record["sha256"]}

    def _queue_short_assembly(self):
        """Record the desired reading copy durably before any filesystem publication."""
        title = self.meta("title")
        relative = self.short_assembly_path()
        last = self.meta("last_chapter")
        rows = self.db.execute("SELECT chapter,text,sha FROM chapters ORDER BY chapter").fetchall()
        if not rows or [row["chapter"] for row in rows] != list(range(1, last + 1)):
            fail("chapters_incomplete", "Every chapter from 1 through the final chapter must be committed")
        sections, chapter_shas, chapter_paths = [], [], []
        for row in rows:
            number, prose, sha = row["chapter"], row["text"], row["sha"]
            registered = self.chapter_path(number)
            artifact = self.db.execute("SELECT sha FROM artifact_state WHERE path=?", (registered,)).fetchone()
            if digest(prose) != sha or not artifact or artifact["sha"] != sha:
                fail("state_corrupt", "Chapter and registered export disagree", chapter=number)
            heading = Path(registered).stem
            if not re.fullmatch(rf"第{number}章 .+", heading):
                source_heading = first_chapter_heading(prose)
                source_title = re.sub(r"^第[0-9０-９零〇一二三四五六七八九十百千万两]+章[\s　:：、.．-]*",
                                      "", source_heading or "").strip()
                heading = f"第{number}章 {source_title or '正文'}"
            # Use the same line boundaries as heading detection, including Unicode separators.
            lines = prose.lstrip("\ufeff").splitlines()
            body = "\n".join(lines[1:] if first_chapter_heading(prose) else lines)
            sections.append(heading + "\n\n" + body.strip("\n"))
            chapter_shas.append(sha)
            chapter_paths.append(registered)
        content = title + "\n\n" + "\n\n".join(sections) + "\n"
        previous = self._short_assembly_record()
        row = self.db.execute("SELECT sha,written_sha FROM artifact_state WHERE path=?", (relative,)).fetchone()
        accepted = previous.get("sha256") if previous and previous["path"] == relative else None
        try:
            if row:
                # An earlier publication may have succeeded before its acknowledgement failed.
                self._check_artifact(relative, row["sha"], row["written_sha"])
                accepted = self._last_artifact_check[1] or row["written_sha"]
            self.queue_artifact(relative, content, accepted_sha=accepted)
        except StoryError as error:
            if error.code == "export_conflict":
                fail("assembly_conflict", "Book-title file was edited outside Story Codex; preserve it before recovery",
                     path=str(safe_path(self.root, relative)))
            raise
        record = {"format": 1, "path": relative, "sha256": digest(content),
                  "chapter_shas": chapter_shas, "chapter_paths": chapter_paths, "revision": self.meta("revision")}
        self.set_meta("short_assembly_path", relative)
        self.set_meta("short_assembly", record)
        return record

    def _refresh_short_assembly(self):
        assembly = self._short_assembly_status()
        if assembly and (not assembly["source_current"] or not self.db.execute(
                "SELECT 1 FROM artifact_state WHERE path=? AND sha=?",
                (assembly["path"], assembly["sha256"])).fetchone()):
            with self.transaction():
                self._queue_short_assembly()

    def assemble_short(self, final_chapter):
        """Enable a protected reading copy after every short-story chapter is exported."""
        integer(final_chapter, "final chapter", 1)
        if self.meta("kind") != "short":
            fail("short_only", "Only a short-story book can be assembled")
        self.integrity = "strict"
        with storage.operation_lock(self):
            with self.transaction():
                last = self.meta("last_chapter")
                if last != final_chapter:
                    fail("chapters_incomplete", "Final chapter must match the committed last chapter",
                         expected=final_chapter, actual=last)
                pending, changed = self._export_health(include_assembly=False)
                if pending or changed:
                    fail("exports_unresolved", "Resolve chapter exports before assembling the short story",
                         pending=pending[:20], changed=changed[:20])
                record = self._queue_short_assembly()
                target = safe_path(self.root, record["path"])
                existed = target.exists()
                old_sha = hashlib.sha256(target.read_bytes()).hexdigest() if existed else None
            # Both the content and its accepted predecessor are committed before publishing.
            result = self.delivery({"path": str(target), "chapters": final_chapter, "sha256": record["sha256"],
                                    "source_revision": record["revision"]})
            complete = result.get("exports_complete") is True
            result.update(created=complete and not existed, updated=complete and existed and old_sha != record["sha256"],
                          backup=next((path for path in result.get("backups", []) if Path(path).name == target.name), None))
            return result

    def delivery(self, result):
        try:
            return {**result, **self.export()}
        except (OSError, StoryError, sqlite3.Error) as error:
            # The SQLite transaction is already durable; never misreport this as a rolled-back commit.
            return {**result, "exports_complete": False,
                    "recovery": "Retry export after IO/lock recovery; use reconcile for an externally edited latest chapter. Preserved versions: .story/export-backups/",
                    "export_error": str(error),
                    "export_details": {"code": error.code, **error.details} if isinstance(error, StoryError) else
                                      {"code": "sqlite_error" if isinstance(error, sqlite3.Error) else "io_error"}}

    def _export_health(self, include_assembly=True):
        pending, drift = [], []
        assembly = self._short_assembly_status() if include_assembly else self._short_assembly_record()
        rows = self._artifact_rows()
        if not include_assembly and assembly:
            rows = [row for row in rows if row["path"] != assembly["path"]]
        for row in rows:
            try:
                self._check_artifact(row["path"], row["sha"], row["written_sha"])
                if (self._last_artifact_check[1] != row["sha"] or row["written_sha"] != row["sha"] or
                        self._artifact_has_alias(row["path"])):
                    pending.append(row["path"])
            except StoryError:
                drift.append(row["path"])
        for row in self._retired_chapters():
            try:
                self._check_artifact(row["path"], row["sha"], row["written_sha"])
                pending.append(row["path"])
            except StoryError:
                drift.append(row["path"])
        if include_assembly and assembly and assembly["state"] != "current":
            unresolved = drift if assembly["state"] == "changed" else pending
            if assembly["path"] not in unresolved:
                unresolved.append(assembly["path"])
        self._verified_paths = len(rows)
        self._health_clean = not pending and not drift
        return pending, drift

    def status(self):
        # Bind export health, progress and counts to the same database version.
        with self.read_snapshot():
            pending, drift = self._export_health()
            sources = self.list_sources(0, 3, 6000)
            return {"book": str(self.root), "id": self.meta("id"), "title": self.meta("title"),
                    "kind": self.meta("kind"), "revision": self.meta("revision"),
                    "short_assembly": self._short_assembly_status(),
                    "short_assembly_path": self.short_assembly_path() if self.meta("kind") == "short" else None,
                    "last_chapter": self.meta("last_chapter"), "next_chapter": self.meta("last_chapter") + 1,
                    "imported_through": self.meta("imported_through"),
                    "cards": self.db.execute("SELECT count(*) FROM cards").fetchone()[0],
                    "pending_exports": pending[:20], "pending_export_count": len(pending),
                    "changed_exports": drift[:20], "changed_export_count": len(drift),
                    "sources": sources["total"], "recent_sources": sources["results"],
                    "more_sources": sources["next_offset"] < sources["total"], "integrity": self._integrity_report()}

    def chapter_external_path(self, chapter):
        relative = self.chapter_path(chapter)
        for retired in self._retired_chapters():
            if retired["destination"] != relative:
                continue
            old_target = safe_path(self.root, retired["path"])
            if old_target.is_file() and hashlib.sha256(old_target.read_bytes()).hexdigest() not in {
                    retired["sha"], retired["written_sha"]}:
                relative = retired["path"]
                break
        return relative

    def accept_chapter_external(self, chapter, relative, sha):
        if relative != self.chapter_path(chapter):
            retired = self.meta("chapter_retired:" + relative)
            retired["written_sha"] = sha
            self.set_meta("chapter_retired:" + relative, retired)
            return None
        return sha

    def _external_snapshot(self, chapter):
        existing = self.db.execute("SELECT imported FROM chapters WHERE chapter=?", (chapter,)).fetchone()
        if chapter != self.meta("last_chapter") or not existing or existing["imported"]:
            fail("chapter_order", "Only the latest native chapter can be reconciled")
        target = safe_path(self.root, self.chapter_external_path(chapter))
        if not target.is_file():
            fail("external_missing", "Recover the missing export before reconciling", path=str(target))
        return {"path": str(target), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}

    def reconcile(self, chapter, draft=None, raw=None, budget=16000):
        integer(chapter, "chapter", 1)
        if (draft is None) != (raw is None):
            fail("invalid_input", "Supply both draft and input to apply a reconciliation, or neither to inspect")
        if draft is None:
            return self.context(chapter, budget, _reconcile=True)
        return self.commit(chapter, draft, raw, replace_last=True, accept_external=True)

    def context(self, chapter, budget=16000, _reconcile=False):
        integer(chapter, "chapter", 1)
        # Read one SQLite snapshot. Concurrent writers produce either the old or new packet, never a mix.
        self.db.execute("BEGIN")
        try:
            external = self._external_snapshot(chapter) if _reconcile else None
            pending, drift = self._export_health()
            if external:
                external_relative = Path(external["path"]).relative_to(self.root).as_posix()
                drift = [p for p in drift if p != external_relative]
                if external_relative != self.chapter_path(chapter):
                    pending = [p for p in pending if p != external_relative]
            if pending or drift:
                fail("exports_unresolved", "Recover exports or reconcile outside edits before writing",
                     pending=pending[:10], changed=drift[:10])
            plan = self.get_plan(chapter)
            scopes = {"global"}
            scopes.update(f"{key}:{plan[key]}" for key in ("volume", "arc", "line") if key in plan)
            scopes.update("entity:" + eid for eid in world.resolve(self, plan.get("entities", []), plan.get("line")))
            marks = ",".join("?" for _ in scopes)
            pinned = {r[0] for r in self.db.execute(
                f"SELECT id FROM card_index WHERE scope IN ({marks}) AND status='active' "
                "AND (critical=1 OR (kind='hook' AND due<=?))", (*sorted(scopes), chapter))}
            selected = pinned | set(plan["requires"])
            tags = plan["tags"]
            if tags:
                selected.update(r[0] for r in self.db.execute(
                    "SELECT DISTINCT i.id FROM card_tags t JOIN card_index i ON i.id=t.id "
                    f"WHERE t.tag IN ({','.join('?' for _ in tags)}) AND i.scope IN ({marks}) "
                    "AND i.status='active' ORDER BY i.id LIMIT 256", (*tags, *sorted(scopes))))
            replacing = self.db.execute("SELECT receipt FROM chapters WHERE chapter=?", (chapter,)).fetchone()
            if replacing:
                receipt = json.loads(replacing[0])
                if (receipt.get("history_branch") or receipt.get("world_changes") or self.db.execute(
                        "SELECT 1 FROM world_evidence WHERE mode='chapter' AND chapter=? AND retired=0 LIMIT 1", (chapter,)).fetchone()):
                    fail("history_revision_required", "Use a history branch to revise a chapter with published world evidence or a global state correction",
                         chapter=chapter, recovery_command="history-start")
                selected.update(receipt.get("before", {}))
            cards = self.cards(selected)
            last = self.meta("last_chapter")
            if chapter not in (last, last + 1):
                fail("chapter_order", "Context supports the next chapter or revision of the latest chapter")
            previous = self.db.execute("SELECT chapter,summary,substr(text,-600) FROM chapters WHERE chapter<? ORDER BY chapter DESC LIMIT 1",
                                       (chapter,)).fetchone()
            if replacing:
                receipt = json.loads(replacing[0])
                for cid, value in receipt.get("before", {}).items():
                    if cards.get(cid) != receipt.get("after", {}).get(cid):
                        fail("revised_state_conflict", "A card changed after this chapter; use history-start to review subsequent state",
                             card=cid, chapter=chapter, recovery_command="history-start")
                    if value is None:
                        cards.pop(cid, None)
                    else:
                        cards[cid] = value
            required = set(plan["requires"])
            missing = sorted(required - cards.keys())
            if missing:
                fail("missing_required_cards", "Plan references unknown cards", ids=missing)
            required.update(c["id"] for c in cards.values()
                            if c.get("scope", "global") in scopes and c["status"] == "active" and (c["critical"] or
                               (c["kind"] == "hook" and c["due"] is not None and c["due"] <= chapter)))
            packet = {"book_id": self.meta("id"), "revision": self.meta("revision"), "chapter": chapter,
                      "mode": "replace_last" if replacing else "next", "plan": plan,
                      "required_cards": [cards[cid] for cid in sorted(required)], "optional_cards": [],
                      "previous": {"chapter": previous[0], "summary": previous[1], "tail": previous[2][-600:]} if previous else None,
                      "omitted_optional_count": 0}
            if external:
                packet.update(mode="reconcile_last", external_edit=external)
            # Global rules apply even when the plan has no optional world selectors.
            world_packet = world.context(self, plan, chapter)
            if (any(key in plan for key in ("volume", "arc", "line", "entities", "time")) or
                    world_packet["rules"] or world_packet["planned"]["rules"]):
                packet["world"] = world_packet
            if self.integrity != "strict":
                packet["integrity"] = self._integrity_report()
            tags = set(plan["tags"])
            search = dumps(plan).casefold()
            optional = [c for c in cards.values() if c["id"] not in required and c["status"] == "active"]
            def score(card):
                return 5 * len(tags.intersection(card["tags"])) + sum(tag.casefold() in search for tag in card["tags"]) + 2 * (card["id"].casefold() in search)
            candidates = sorted((c for c in optional if score(c) > 0), key=lambda c: (-score(c), c["id"]))
            active_total = self.db.execute("SELECT count(*) FROM card_index WHERE status='active'").fetchone()[0]
            packet["omitted_optional_count"] = max(0, active_total - sum(c["status"] == "active" for c in packet["required_cards"]))
            bounded_packet(packet, budget)
            for card in candidates:
                packet["optional_cards"].append(card)
                packet["omitted_optional_count"] -= 1
                try:
                    bounded_packet(packet, budget)
                except StoryError as error:
                    if error.code != "budget_exceeded":
                        raise
                    packet["optional_cards"].pop()
                    packet["omitted_optional_count"] += 1
            return bounded_packet(packet, budget)
        finally:
            self.db.rollback()

    def recall(self, query, budget=8000):
        text_field(query, "query", 200)
        with self.read_snapshot():
            result = search.query(self.db, query, limit=128)
            cards = self.cards(item["key"] for item in result["matches"] if item["kind"] == "card")
            found = []
            for item in result["matches"]:
                if item["kind"] == "card" and item["key"] in cards:
                    found.append({"type": "card", **cards[item["key"]]})
                else:
                    found.append({"type": item["kind"], "chapter": item["metadata"].get("chapter"),
                                  "snippet": item["snippet"], "source_sha256": item["source_sha256"],
                                  "path": self.chapter_path(int(item["key"])) if item["kind"] in ("chapter", "chapter_summary") else None})
        packet = {"query": query, "matches": [], "total": len(found), "omitted": len(found),
                  "complete": False if found else result["complete"], "no_match_confirmed": result["no_match_confirmed"],
                  # Reserve truncation metadata before packing, so marking an
                  # omitted result cannot itself overflow an otherwise full packet.
                  "truncated_reasons": result["truncated_reasons"] + (["budget_limit"] if found else []),
                  "total_semantics": "bounded verified matches; use a narrower query if incomplete"}
        for item in found:
            packet["matches"].append(item)
            packet["omitted"] -= 1
            try:
                bounded_packet(packet, budget)
            except StoryError as error:
                if error.code != "budget_exceeded":
                    raise
                packet["matches"].pop()
                packet["omitted"] += 1
        if not packet["omitted"]:
            packet["complete"] = result["complete"]
            packet["truncated_reasons"] = result["truncated_reasons"]
        return bounded_packet(packet, budget)

    def _chapter_lint(self, chapter, text, plan, external=None):
        result = lint_text(text, plan)
        row = self.db.execute("SELECT imported FROM chapter_state WHERE chapter=?", (chapter,)).fetchone()
        previous, old, relative, reused, target_sha, source_sha = self._chapter_target(
            chapter, text, plan, imported=bool(row and row[0]), external=external)
        queued = self.db.execute("SELECT written_sha FROM artifact_state WHERE path=?", (relative,)).fetchone()
        self._check_artifact(relative, digest(text), target_sha or (queued[0] if queued else None))
        if old and relative != previous:
            self._check_artifact(previous, old["sha"], source_sha or old["written_sha"])
        result["path"] = str(self.root / relative)
        return result

    def lint(self, chapter, draft):
        integer(chapter, "chapter", 1)
        text = read_text(draft)
        with self.read_snapshot():
            # Lint remains usable while reviewing an existing outside edit. This
            # observation is read-only; commit/reconcile still enforce their own
            # external SHA and revision fences before accepting any replacement.
            external = history._external(self, chapter)
            if external:
                external = {**external, "path": str(self.root / external["path"])}
            result = self._chapter_lint(chapter, text, self.get_plan(chapter), external)
            if external:
                result["external_edit"] = external
            return result

    def chapter_read(self, chapter, sha=None, start=0, end=None, budget=12000):
        integer(chapter, "chapter", 1)
        integer(start, "start")
        if sha is None:
            row = self.db.execute("SELECT sha FROM chapter_state WHERE chapter=?", (chapter,)).fetchone()
        else:
            if not re.fullmatch(r"[0-9a-f]{64}", sha):
                fail("invalid_input", "sha256 must be a SHA-256 digest")
            candidate_path = f'$.candidates."{chapter}".sha'
            row = self.db.execute("SELECT sha FROM chapter_state WHERE chapter=? AND sha=? UNION "
                "SELECT sha FROM history_versions WHERE chapter=? AND sha=? UNION "
                "SELECT json_extract(data,?) FROM history_branches WHERE json_extract(data,?)=? LIMIT 1",
                (chapter, sha, chapter, sha, candidate_path, candidate_path, sha)).fetchone()
        if not row:
            fail("chapter_missing", "No matching immutable chapter version in this book", chapter=chapter)
        sha = row[0]
        size = self.db.execute("SELECT length(text) FROM core_objects WHERE sha=?", (sha,)).fetchone()[0]
        end = min(start + 2000, size) if end is None else integer(end, "end", 1)
        if not start < end <= size:
            fail("invalid_range", "Use Unicode character offsets within this chapter", characters=size)
        text = self.db.execute("SELECT substr(text,?,?) FROM core_objects WHERE sha=?", (start+1, end-start, sha)).fetchone()[0]
        return bounded_packet({"chapter": chapter, "source_sha256": sha, "start": start, "end": end,
                               "characters": size, "next_start": end if end < size else None, "text": text}, budget)

    def dependency_candidates(self, chapter, budget=16000):
        packet = self.context(chapter, budget)
        found = {("card", c["id"]): {"kind": "card", "ref": c["id"], "sha": digest(dumps(c))}
                 for c in packet["required_cards"] + packet["optional_cards"]}
        world_packet = packet.get("world", {})
        for field, kind in (("entities", "entities"), ("facts", "facts"), ("propositions", "facts"),
                            ("knowledge", "knowledge"), ("hooks", "hooks"), ("rules", "rules"),
                            ("uses", "uses"), ("arc_steps", "arc_steps"),
                            ("volume", "volumes"), ("arc", "arcs"), ("line", "lines"), ("line_candidates", "lines")):
            values = world_packet.get(field, [])
            if values is None:
                continue
            if isinstance(values, dict):
                values = [values]
            for record in values:
                sha = world.resolve_dependency(self, kind, record["id"])
                if sha:
                    key = ("world." + kind, record["id"])
                    found[key] = {"kind": key[0], "ref": key[1], "sha": sha}
        for key in ("previous",):
            if packet.get(key):
                ref = packet[key]["chapter"]
                row = self.db.execute("SELECT sha FROM chapter_state WHERE chapter=?", (ref,)).fetchone()
                found[("chapter", str(ref))] = {"kind": "chapter", "ref": str(ref), "sha": row[0]}
        if packet["revision"] != self.meta("revision"):
            fail("stale_revision", "State changed while resolving dependency candidates; retry")
        return bounded_packet({"book_id": packet["book_id"], "revision": packet["revision"], "chapter": chapter,
            "candidates": [found[k] for k in sorted(found)], "world_warnings": world_packet.get("warnings", []),
            "review_required": "Select actual dependencies, add missing sources, then declare completeness with a concrete review note. "
                               "Candidate hashes do not resolve the accompanying world-state warnings."}, budget)

    def world_check(self, chapter, budget=16000):
        integer(chapter, "chapter", 1)
        with self.read_snapshot():
            result = world.check(self, {**self.get_plan(chapter), "chapter": chapter})
            return bounded_packet({**result, "book_id": self.meta("id"),
                                   "revision": self.meta("revision"), "chapter": chapter}, budget)

    def world_read(self, kind, rid, budget=12000):
        with self.read_snapshot():
            return self._world_read(kind, rid, budget)

    def _world_read(self, kind, rid, budget):
        if kind not in world.FIELDS or kind == "aliases":
            fail("invalid_input", "Choose an individual world record kind; resolve aliases through entities")
        value = world._stored(self, kind, text_field(rid, "record id", 80))
        if value is None:
            fail("world_reference", "Unknown world record", kind=kind, id=rid)
        row, evidence, entities, requires = value
        record = dict(row)
        for field, typ in world.FIELDS[kind].items():
            if typ == "bool":
                record[field] = bool(record[field])
        if evidence:
            record["evidence"] = ({"kind": "chapter", "chapter": evidence["chapter"], "sha256": evidence["sha"], "quote": evidence["quote"]}
                                  if evidence["mode"] == "chapter" else {"kind": "author_plan", "note": evidence["note"]})
        if entities:
            record["entities"] = entities
        if kind == "rules":
            record["requires"] = requires
        sha = world.resolve_dependency(self, kind, rid)
        retired = self.db.execute("SELECT retired FROM world_evidence WHERE kind=? AND record_id=?", (kind,rid)).fetchone()
        return bounded_packet({"kind": kind, "id": rid, "record_sha256": sha, "evidence_current": sha is not None,
                               "retired": bool(retired[0]) if retired else False, "payload": {kind: [record]}}, budget)

    def prepare(self, chapter, draft, reconcile=False, budget=16000):
        # Context captures a consistent plan/revision and checks recovery/replace
        # boundaries. Later changes are rejected by commit's existing fences.
        packet = self.context(chapter, budget, _reconcile=reconcile)
        text = read_text(draft)
        with self.read_snapshot():
            if self.meta("revision") != packet["revision"]:
                fail("stale_revision", "State changed during preparation; reload context and review again")
            lint = self._chapter_lint(chapter, text, packet["plan"], packet.get("external_edit"))
            delta = {"book_id": packet["book_id"], "base_revision": packet["revision"],
                     "summary": "<填写本章实际结果与下一章衔接>", "changes": [],
                     "review": {"draft_sha256": lint["draft_sha256"],
                                "checks": {key: {"note": "<填写审查观察>", "quote": "<填写正文原句>"}
                                           for key in CHECKS},
                                "issues": [{"severity": "blocker", "issue": "尚未完成语义审查；填写观察与引文并处理实际问题后移除此占位项。"}]}}
            if reconcile:
                delta["external_sha256"] = packet["external_edit"]["sha256"]
            result = {"mode": packet["mode"], "lint": lint, "delta": delta,
                    "ready_to_commit": False,
                    "next": "Complete the summary, evidence-based review and state changes; do not change identity or hash fields."}
            if "world" in packet:
                result["world_check"] = world.check(self, {**packet["plan"], "chapter": chapter})
            return bounded_packet(result, budget)

    def validate_delta(self, text, raw):
        raw = object_value(raw, "delta")
        if raw.get("book_id") != self.meta("id"):
            fail("wrong_book", "Delta belongs to a different book or has no book_id")
        integer(raw.get("base_revision"), "delta.base_revision")
        text_field(raw.get("summary"), "delta.summary", 800)
        review = object_value(raw.get("review"), "delta.review")
        if review.get("draft_sha256") != digest(text):
            fail("stale_review", "Review must refer to the exact current draft SHA-256")
        checks = object_value(review.get("checks"), "review.checks")
        for key in CHECKS:
            check = object_value(checks.get(key), f"review.checks.{key}")
            text_field(check.get("note"), f"review.{key}.note", 1200)
            if text_field(check.get("quote"), f"review.{key}.quote", 1200) not in text:
                fail("invalid_evidence", "Review quote is absent from the draft", check=key)
        issues = review.get("issues")
        if not isinstance(issues, list) or len(issues) > 100:
            fail("invalid_input", "review.issues must be an array, at most 100 items")
        for issue in issues:
            object_value(issue, "review.issue")
            text_field(issue.get("issue"), "review.issue.issue", 1200)
            if issue.get("severity") not in ("blocker", "advice"):
                fail("invalid_input", "Issue severity must be blocker or advice")
            if issue["severity"] == "blocker":
                fail("review_blocker", "Resolve blocking findings before committing", issue=issue["issue"])
        changes = raw.get("changes")
        if not isinstance(changes, list) or len(changes) > 100:
            fail("invalid_input", "delta.changes must be an array, at most 100 items")
        ids = []
        for change in changes:
            object_value(change, "change")
            ids.append(text_field(change.get("id"), "change.id", 80))
            if text_field(change.get("quote"), "change.quote", 1200) not in text:
                fail("invalid_evidence", "State change quote is absent from the draft", card=change["id"])
        if len(set(ids)) != len(ids):
            fail("duplicate_id", "Each card can change once per transaction")
        if "world_changes" in raw:
            object_value(raw["world_changes"], "delta.world_changes")
        return raw

    def commit(self, chapter, draft, raw, replace_last=False, accept_external=False):
        integer(chapter, "chapter", 1)
        text = read_text(draft)
        raw = self.validate_delta(text, raw)
        inputs = {"delta": raw, "sha": digest(text), "replace_last": replace_last}
        if accept_external:
            inputs["accept_external"] = True
            if not replace_last or not re.fullmatch(r"[0-9a-f]{64}", str(raw.get("external_sha256", ""))):
                fail("invalid_input", "Reconciliation requires the inspected external_sha256 and a latest-chapter replacement")
        input_hash = digest(dumps(inputs))
        idempotent = False
        with self.transaction():
            existing = self.db.execute("SELECT * FROM chapters WHERE chapter=?", (chapter,)).fetchone()
            if existing and existing["input_hash"] == input_hash:
                idempotent = True
            else:
                if accept_external:
                    external = self._external_snapshot(chapter)
                    if external["sha256"] != raw["external_sha256"]:
                        fail("stale_external", "External draft changed; inspect and review the new version",
                             path=external["path"], expected=raw["external_sha256"], actual=external["sha256"])
                pending, drift = self._export_health()
                if accept_external:
                    external_relative = Path(external["path"]).relative_to(self.root).as_posix()
                    drift = [p for p in drift if p != external_relative]
                    if external_relative != self.chapter_path(chapter):
                        pending = [p for p in pending if p != external_relative]
                if pending or drift:
                    fail("exports_unresolved", "Resolve previous exports before committing another change",
                         pending=pending[:10], changed=drift[:10])
                if raw["base_revision"] != self.meta("revision"):
                    fail("stale_revision", "State changed; reload and review before retrying",
                         expected=raw["base_revision"], actual=self.meta("revision"))
                last = self.meta("last_chapter")
                if replace_last:
                    if chapter != last or not existing or existing["imported"]:
                        fail("chapter_order", "Only the latest native chapter can be replaced")
                elif existing or chapter != last + 1:
                    fail("chapter_order", "Commit exactly the next chapter; existing chapters are never overwritten")
                plan = self.get_plan(chapter)
                check = lint_text(text, plan)
                if not check["ok"]:
                    fail("lint_failed", "Draft fails deterministic checks", lint=check)
                previous = json.loads(existing["receipt"]) if replace_last else {}
                if (previous.get("history_branch") or previous.get("world_changes") or (replace_last and self.db.execute(
                        "SELECT 1 FROM world_evidence WHERE mode='chapter' AND chapter=? AND retired=0 LIMIT 1", (chapter,)).fetchone())):
                    fail("history_revision_required", "Use a history branch to review this chapter and its published world evidence",
                         chapter=chapter, recovery_command="history-start")
                touched = set(plan["requires"]) | {c["id"] for c in raw["changes"]} | set(previous.get("before", {}))
                before_state = self.cards(touched)
                if replace_last:
                    for cid, value in previous["before"].items():
                        if before_state.get(cid) != previous["after"].get(cid):
                            fail("revised_state_conflict", "A card changed after this chapter; use history-start to review subsequent state",
                                 card=cid, chapter=chapter, recovery_command="history-start")
                        if value is None:
                            self.delete_card(cid)
                        else:
                            self.put_card(value)
                state = self.cards(touched)
                missing = sorted(set(plan["requires"]) - state.keys())
                if missing:
                    fail("missing_required_cards", "Plan references unknown cards", ids=missing)
                dependency_metadata = history.validate_commit_dependencies(self, raw, chapter)
                before, after = {}, {}
                for change in raw["changes"]:
                    cid = change["id"]
                    before[cid] = state.get(cid)
                    merged = {**state.get(cid, {}), **{k: v for k, v in change.items() if k != "quote"}}
                    merged["source"] = f"chapter:{chapter} quote:{change['quote']}"
                    card = valid_card(merged)
                    self.put_card(card)
                    after[cid] = card
                receipt = {"input": raw, "before": before, "after": after, "lint": check}
                receipt.update(dependency_metadata)
                accepted_sha = raw["external_sha256"] if accept_external else None
                if accept_external:
                    accepted_sha = self.accept_chapter_external(chapter, external_relative, accepted_sha)
                self.queue_chapter(chapter, text, plan, accepted_sha=accepted_sha)
                self.db.execute("INSERT INTO chapters VALUES (?,?,?,?,?,?,0)",
                                (chapter, text, digest(text), raw["summary"], dumps(receipt), input_hash))
                if raw.get("world_changes"):
                    changes = world.apply_in_transaction(self, raw["world_changes"])
                    receipt["world_changes"] = changes
                    checked_world = world.check_transition(self, plan, chapter, raw["world_changes"])
                    if not checked_world["ok"]:
                        fail("world_constraint", "Resolve recorded rule/resource conflicts in the actual chapter changes", checks=checked_world)
                    receipt["world_checks"] = checked_world
                    self.db.execute("UPDATE chapters SET receipt=? WHERE chapter=?", (dumps(receipt), chapter))
                self.index_chapter(chapter, text, raw["summary"])
                self.set_meta("last_chapter", chapter)
                self.event("replace_chapter" if replace_last else "commit_chapter",
                           {"chapter": chapter, "text": text, "receipt": receipt,
                            "previous": dict(existing) if existing else None})
                history.on_commit(self, chapter, plan, receipt, text, digest(text))
            # Construct the durable receipt from this transaction, before another
            # writer can acquire an exclusive lock and block post-commit reads.
            revision = self.meta("revision")
            relative = self.chapter_path(chapter)
        return self.delivery({"committed": True, "idempotent": idempotent, "chapter": chapter,
                              "revision": revision, "path": str(self.root / relative)})

    def adopt(self, chapter, draft, summary, expected, volume_dir=None):
        integer(chapter, "chapter", 1)
        summary = text_field(summary, "summary", 800)
        text = read_text(draft)
        if "\x00" in text:
            fail("corrupt_text", "Remove NUL characters before adopting; SQLite text offsets stop at NUL")
        if not visible_count(text):
            fail("empty_source", "Cannot adopt an empty chapter")
        with self.transaction(expected):
            if self.meta("last_chapter") != 0:
                fail("adopt_nonempty", "Adoption only initializes a fresh book baseline")
            receipt = {"source_path": str(Path(draft).resolve()), "quality": "imported_unverified"}
            plan_row = self.db.execute("SELECT data FROM plans WHERE chapter=?", (chapter,)).fetchone()
            plan = json.loads(plan_row[0]) if plan_row else {}
            if volume_dir is not None:
                plan["volume_dir"] = volume_directory(volume_dir)
            relative = self.queue_chapter(chapter, text, plan, imported=True)
            self.db.execute("INSERT INTO chapters VALUES (?,?,?,?,?,?,1)",
                            (chapter, text, digest(text), summary, dumps(receipt), digest(dumps(receipt))))
            self.index_chapter(chapter, text, summary)
            self.set_meta("last_chapter", chapter)
            self.set_meta("imported_through", chapter)
            self.event("adopt", {"chapter": chapter, "receipt": receipt, "summary": summary})
            history.on_commit(self, chapter, {}, receipt, text, digest(text))
            revision = self.meta("revision")
        return self.delivery({"adopted_through": chapter, "revision": revision, "path": str(self.root / relative),
                              "quality": "imported_unverified"})

    def adopt_backfill(self, chapter, draft, summary, expected, volume_dir=None):
        """Fill a missing old short-story chapter without replaying historical state."""
        integer(chapter, "chapter", 1)
        integer(expected, "expected revision")
        summary = text_field(summary, "summary", 800)
        text = read_text(draft)
        if "\x00" in text:
            fail("corrupt_text", "Remove NUL characters before importing historical prose")
        if not visible_count(text):
            fail("empty_source", "Cannot import an empty historical chapter")
        volume = volume_directory(volume_dir) if volume_dir is not None else None
        source_path = str(Path(draft).resolve())
        fingerprint = digest(dumps({"chapter": chapter, "source_path": source_path, "sha256": digest(text),
                                    "summary": summary, "volume_dir": volume}))
        idempotent = False
        with self.transaction():
            if self.meta("kind") != "short":
                fail("short_only", "Historical backfill is for an adopted short story")
            through = self.meta("imported_through")
            if not 1 <= chapter < through:
                fail("backfill_range", "Fill only missing chapters before the adopted baseline", imported_through=through)
            existing = self.db.execute("SELECT receipt,sha,summary,imported FROM chapter_state WHERE chapter=?", (chapter,)).fetchone()
            if existing:
                receipt = json.loads(existing["receipt"])
                if (receipt.get("backfill_input_sha256") != fingerprint or not existing["imported"] or
                        existing["sha"] != digest(text) or existing["summary"] != summary):
                    fail("chapter_exists", "Historical backfill never replaces an existing chapter; use reviewed history revision",
                         chapter=chapter)
                idempotent = True
            else:
                if expected != self.meta("revision"):
                    fail("stale_revision", "State changed; reload status before importing historical prose",
                         expected=expected, actual=self.meta("revision"))
                # The chapter's original state changes are unknown. Preserve the current
                # cards/world/progress and record only its authentic text and provenance.
                pending, changed = self._export_health()
                if pending or changed:
                    fail("exports_unresolved", "Resolve previous exports before importing another historical chapter",
                         pending=pending[:10], changed=changed[:10])
                plan_row = self.db.execute("SELECT data FROM plans WHERE chapter=?", (chapter,)).fetchone()
                plan = json.loads(plan_row[0]) if plan_row else {}
                if volume is not None:
                    plan["volume_dir"] = volume
                relative = self.queue_chapter(chapter, text, plan, imported=True)
                receipt = {"source_path": source_path, "source_sha256": digest(text), "quality": "imported_unverified",
                           "backfill_input_sha256": fingerprint, "imported_through": through}
                self.db.execute("INSERT INTO chapters VALUES (?,?,?,?,?,?,1)",
                                (chapter, text, digest(text), summary, dumps(receipt), fingerprint))
                self.index_chapter(chapter, text, summary)
                self.event("adopt_backfill", {"chapter": chapter, "receipt": receipt, "summary": summary})
                history.on_commit(self, chapter, {}, receipt, text, digest(text))
            revision = self.meta("revision")
            relative = self.chapter_path(chapter)
        return self.delivery({"backfilled": chapter, "idempotent": idempotent, "revision": revision,
                              "path": str(self.root / relative), "quality": "imported_unverified"})

    def source(self, sid):
        row = self.db.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
        if not row:
            fail("source_missing", "Unknown source id", source=sid)
        return row

    def source_info(self, sid):
        row = self.db.execute("SELECT s.id,s.name,s.coverage,s.encoding,t.characters FROM sources s "
                              "JOIN source_stats t ON t.source=s.id WHERE s.id=?", (sid,)).fetchone()
        if not row:
            fail("source_missing", "Unknown source id", source=sid)
        return row

    def _source_slice(self, sid, start, end):
        return self.db.execute("SELECT substr(text,?,?) FROM sources WHERE id=?", (start+1, end-start, sid)).fetchone()[0]

    def _complete_noncontent_chunks(self, sid, text=None):
        # Classify new/legacy chunks once. Later calls inspect only known whitespace
        # candidates, not every pending narrative chunk or the whole source in Python.
        unclassified = self.db.execute("SELECT c.ordinal,c.start,c.end FROM chunks c LEFT JOIN chunk_classification k "
            "ON k.source=c.source AND k.ordinal=c.ordinal WHERE c.source=? AND k.ordinal IS NULL", (sid,)).fetchall()
        if unclassified:
            if text is None:
                text = self.source(sid)["text"]
            self.db.executemany("INSERT INTO chunk_classification VALUES (?,?,?)",
                ((sid, r["ordinal"], int(not text[r["start"]:r["end"]].strip())) for r in unclassified))
        rows = self.db.execute("SELECT c.ordinal,c.start,c.end,c.sha FROM chunk_classification k JOIN chunks c "
            "ON c.source=k.source AND c.ordinal=k.ordinal WHERE k.source=? AND k.noncontent=1 "
            "AND c.analysis IS NULL ORDER BY c.ordinal", (sid,)).fetchall()
        for row in rows:
            chunk = text[row["start"]:row["end"]] if text is not None else self._source_slice(sid,row["start"],row["end"])
            if not chunk or chunk.strip():
                continue
            if digest(chunk) != row["sha"]:
                fail("source_hash_mismatch", "Saved whitespace chunk does not match its source hash", source=sid, chunk=row["ordinal"])
            analysis = {"kind": "non_content", "chunk_sha256": row["sha"],
                        "summary": "此块仅含空白字符；保留原文范围，不生成剧情或文学结论。", "findings": []}
            self.db.execute("UPDATE chunks SET analysis=? WHERE source=? AND ordinal=? AND analysis IS NULL",
                            (dumps(analysis), sid, row["ordinal"]))
            self.event("analysis", {"source": sid, "chunk": row["ordinal"], "before": None, "after": analysis})

    def ingest(self, file, coverage="unknown", encoding="utf-8-sig", chunk_chars=2400):
        integer(chunk_chars, "chunk_chars", 256)
        if chunk_chars > 10000 or coverage not in ("unknown", "partial", "complete"):
            fail("invalid_input", "Invalid chunk size or source coverage")
        raw_bytes = Path(file).read_bytes()
        text = raw_bytes.decode(encoding)
        if "\x00" in text:
            fail("corrupt_text", "Remove NUL characters before ingesting; SQLite text offsets stop at NUL")
        if not text.strip():
            fail("empty_source", "Source contains no text")
        sid = hashlib.sha256(raw_bytes).hexdigest()
        with self.transaction():
            existing = self.db.execute("SELECT * FROM sources WHERE id=?", (sid,)).fetchone()
            if existing:
                if existing["text"] != text or existing["coverage"] != coverage:
                    fail("source_conflict", "Same source bytes already registered with different metadata")
                self._complete_noncontent_chunks(sid, text)
                return {"source": sid, "idempotent": True, **self.coverage(sid)}
            self.db.execute("INSERT INTO sources VALUES (?,?,?,?,?)",
                            (sid, str(Path(file).resolve()), text, coverage, encoding))
            self.db.execute("UPDATE source_stats SET characters=? WHERE source=?", (len(text), sid))
            chunks = split_source(text, chunk_chars)
            self.db.executemany("INSERT INTO chunks(source,ordinal,start,end,title,sha) VALUES (?,?,?,?,?,?)",
                                [(sid, i, start, end, title, digest(text[start:end]))
                                 for i, (start, end, title) in enumerate(chunks, 1)])
            self.event("ingest", {"source": sid, "chunks": len(chunks), "coverage": coverage})
            self._complete_noncontent_chunks(sid, text)
            status = self.coverage(sid)
        return {"source": sid, "idempotent": False, **status}

    def coverage(self, sid):
        with self.read_snapshot():
            return self._coverage(sid)

    def _coverage(self, sid):
        source = self.source_info(sid)
        rows = self.db.execute("SELECT ordinal,start,end,CASE WHEN analysis IS NULL THEN NULL ELSE 1 END FROM chunks WHERE source=? ORDER BY ordinal", (sid,)).fetchall()
        missing = [r[0] for r in rows if r[3] is None]
        contiguous = bool(rows) and rows[0][1] == 0 and rows[-1][2] == source["characters"]
        contiguous = contiguous and all(a[2] == b[1] for a, b in zip(rows, rows[1:]))
        report_path = f".story/analysis/{sid}/report.md"
        finalized = self.db.execute("SELECT 1 FROM artifacts WHERE path=?", (report_path,)).fetchone() is not None
        return {"source": sid, "source_coverage": source["coverage"], "chunks_total": len(rows),
                "analyzed": len(rows) - len(missing), "pending": len(missing), "next_chunk": missing[0] if missing else None,
                "text_coverage_contiguous": contiguous, "complete_for_imported_text": not missing and contiguous,
                "report_path": report_path if finalized else None}

    def list_sources(self, offset=0, limit=10, budget=12000):
        with self.read_snapshot():
            return self._list_sources(offset, limit, budget)

    def _list_sources(self, offset=0, limit=10, budget=12000):
        integer(offset, "offset")
        integer(limit, "limit", 1)
        total = self.db.execute("SELECT count(*) FROM sources").fetchone()[0]
        rows = self.db.execute("SELECT id,name FROM sources ORDER BY rowid DESC LIMIT ? OFFSET ?",
                               (min(limit, 100), offset)).fetchall()
        packet = {"offset": offset, "next_offset": offset, "total": total, "results": []}
        for row in rows:
            packet["results"].append({"name": Path(row["name"]).name, **self.coverage(row["id"])})
            packet["next_offset"] += 1
            try:
                bounded_packet(packet, budget)
            except StoryError as error:
                if error.code != "budget_exceeded" or len(packet["results"]) == 1:
                    raise
                packet["results"].pop()
                packet["next_offset"] -= 1
                break
        return bounded_packet(packet, budget)

    def next_chunks(self, sid, limit=2, budget=22000):
        integer(limit, "limit", 1)
        with self.transaction():
            source = self.source_info(sid)
            self._complete_noncontent_chunks(sid)
            rows = self.db.execute("SELECT * FROM chunks WHERE source=? AND analysis IS NULL ORDER BY ordinal LIMIT ?", (sid, min(limit, 50))).fetchall()
            packet = {"source": sid, "source_coverage": source["coverage"], "chunks": [], "pending": self.coverage(sid)["pending"]}
            for row in rows:
                item = {k: row[k] for k in ("ordinal", "start", "end", "title", "sha")}
                item["text"] = self._source_slice(sid, row["start"], row["end"])
                packet["chunks"].append(item)
                try:
                    bounded_packet(packet, budget)
                except StoryError as error:
                    if error.code != "budget_exceeded" or len(packet["chunks"]) == 1:
                        raise
                    packet["chunks"].pop()
                    break
            return bounded_packet(packet, budget)

    def source_read(self, sid, start, end, budget):
        source = self.source_info(sid)
        integer(start, "start")
        integer(end, "end", 1)
        if not start < end <= source["characters"]:
            fail("invalid_range", "Use Unicode character offsets: 0 <= start < end <= source length")
        return bounded_packet({"source": sid, "start": start, "end": end, "text": self._source_slice(sid, start, end)}, budget)

    @staticmethod
    def _analysis_content(payload):
        return {k: v for k, v in payload.items() if k not in ("chunk", "start", "end", "analysis_sha256")}

    def _analysis_record_sha(self, sid, row):
        content = self._analysis_content(json.loads(row["analysis"])) if row["analysis"] is not None else None
        return digest(dumps({"source": sid, "chunk": row["ordinal"], "start": row["start"], "end": row["end"],
                             "chunk_sha256": row["sha"], "analysis": content}))

    def _analysis_snapshot_sha(self, sid):
        # Call within the same transaction as the read packet or finalization.
        # Stream all chunks, including pending ones, without returning their text.
        source = self.source_info(sid)
        value = hashlib.sha256(dumps({k: source[k] for k in ("id", "coverage", "encoding", "characters")}).encode("utf-8"))
        for row in self.db.execute("SELECT ordinal,start,end,sha,analysis FROM chunks WHERE source=? ORDER BY ordinal", (sid,)):
            value.update(("\n" + self._analysis_record_sha(sid, row)).encode("ascii"))
        return value.hexdigest()

    def record(self, sid, ordinal, payload, replace=False):
        integer(ordinal, "chunk", 1)
        payload = dict(object_value(payload, "analysis"))
        # Query ranges are read-only metadata, not user-authored analysis.
        payload.pop("start", None)
        payload.pop("end", None)
        expected_analysis = payload.pop("analysis_sha256", None)
        if "chunk" in payload:
            embedded = integer(payload.pop("chunk"), "analysis.chunk", 1)
            if embedded != ordinal:
                fail("chunk_mismatch", "Analysis chunk must match the requested chunk",
                     expected=ordinal, actual=embedded)
        with self.transaction():
            self.source_info(sid)
            row = self.db.execute("SELECT * FROM chunks WHERE source=? AND ordinal=?", (sid, ordinal)).fetchone()
            if not row:
                fail("chunk_missing", "Unknown chunk number")
            if payload.get("chunk_sha256") != row["sha"]:
                fail("source_hash_mismatch", "Analysis must reference the exact chunk hash")
            text_field(payload.get("summary"), "analysis.summary", 1000)
            findings = payload.get("findings")
            if not isinstance(findings, list) or not 1 <= len(findings) <= 30:
                fail("invalid_input", "Analysis requires 1 to 30 evidence-backed findings")
            text = self._source_slice(sid, row["start"], row["end"])
            for finding in findings:
                object_value(finding, "finding")
                text_field(finding.get("claim"), "finding.claim", 1200)
                text_field(finding.get("kind"), "finding.kind", 80)
                if text_field(finding.get("quote"), "finding.quote", 1200) not in text:
                    fail("invalid_evidence", "Finding quote is absent from this chunk")
            encoded = dumps(payload)
            previous = json.loads(row["analysis"]) if row["analysis"] else None
            # Older records may contain redundant or incorrect query metadata.
            # Identity and ranges belong to the database, not the analysis.
            if previous is not None and dumps(self._analysis_content(previous)) == encoded:
                return {"recorded": ordinal, "idempotent": True, **self.coverage(sid)}
            if row["analysis"] and not replace:
                fail("analysis_exists", "Completed chunks are preserved; explicit --replace revises the analysis")
            report_path = f".story/analysis/{sid}/report.md"
            if replace and self.db.execute("SELECT 1 FROM artifacts WHERE path=?", (report_path,)).fetchone():
                fail("report_already_final", "This analysis has a final report; create a separately reviewed revision")
            if row["analysis"] is not None and replace:
                actual_analysis = self._analysis_record_sha(sid, row)
                if expected_analysis is None:
                    fail("analysis_baseline_required", "Read findings and retain the record analysis_sha256 before replacing",
                         source=sid, chunk=ordinal)
                if expected_analysis != actual_analysis:
                    fail("stale_analysis", "Analysis changed; reread and review the current record before replacing",
                         source=sid, chunk=ordinal, expected=expected_analysis, actual=actual_analysis)
            self.db.execute("UPDATE chunks SET analysis=? WHERE source=? AND ordinal=?", (encoded, sid, ordinal))
            self.event("analysis", {"source": sid, "chunk": ordinal,
                                    "before": json.loads(row["analysis"]) if row["analysis"] else None, "after": payload})
            result = {"recorded": ordinal, "idempotent": False, **self.coverage(sid)}
        return result

    def findings(self, sid, offset=0, limit=10, budget=20000):
        # The page and the full-analysis fingerprint must describe one snapshot.
        with self.read_snapshot():
            return self._findings(sid, offset, limit, budget)

    def _findings(self, sid, offset, limit, budget):
        self.source_info(sid)
        integer(offset, "offset")
        integer(limit, "limit", 1)
        rows = self.db.execute("SELECT ordinal,analysis,start,end,sha FROM chunks WHERE source=? AND analysis IS NOT NULL ORDER BY ordinal LIMIT ? OFFSET ?",
                               (sid, min(limit, 100), offset)).fetchall()
        total = self.coverage(sid)["analyzed"]
        packet = {"source": sid, "offset": offset, "next_offset": offset, "total": total,
                  "analysis_sha256": self._analysis_snapshot_sha(sid), "results": []}
        for row in rows:
            packet["results"].append({**json.loads(row[1]), "chunk": row[0], "start": row[2], "end": row[3],
                                      "analysis_sha256": self._analysis_record_sha(sid, row)})
            packet["next_offset"] += 1
            try:
                bounded_packet(packet, budget)
            except StoryError as error:
                if error.code != "budget_exceeded" or len(packet["results"]) == 1:
                    raise
                packet["results"].pop()
                packet["next_offset"] -= 1
                break
        return bounded_packet(packet, budget)

    def report(self, sid, file, expected_analysis=None):
        report = read_text(file)
        if visible_count(report) < 40:
            fail("empty_report", "Final report needs substantive content")
        with self.transaction():
            status = self.coverage(sid)
            if not status["complete_for_imported_text"]:
                fail("analysis_incomplete", "Finish all imported chunks before finalizing", coverage=status)
            content = (f"> source_sha256: {sid}\n> source_coverage: {status['source_coverage']}\n"
                       f"> analyzed_chunks: {status['analyzed']}/{status['chunks_total']}\n"
                       "> Coverage refers to imported text, not independently verified whole-book completeness.\n\n" + report)
            path = f".story/analysis/{sid}/report.md"
            old = self.db.execute("SELECT sha FROM artifacts WHERE path=?", (path,)).fetchone()
            if old and old[0] != digest(content):
                fail("report_exists", "Final report is preserved; save a separately reviewed revision")
            if not old:
                if expected_analysis is None:
                    fail("analysis_baseline_required", "Retain findings.analysis_sha256 while aggregating and pass --expect-analysis",
                         source=sid)
                actual_analysis = self._analysis_snapshot_sha(sid)
                if expected_analysis != actual_analysis:
                    fail("stale_analysis", "Analysis changed after aggregation; reread changed records and review the report",
                         source=sid, expected=expected_analysis, actual=actual_analysis)
                self.queue_artifact(path, content)
                self.event("report", {"source": sid, "path": path, "sha": digest(content),
                                      "analysis_sha256": actual_analysis})
            status["report_path"] = path
        return self.delivery({"finalized": True, "report": str(self.root / path), **status})


def split_source(text, maximum):
    pattern = re.compile(r"(?:\A|(?<=[\r\n]))[ \t\u3000]*(?:#{1,6}[ \t]*)?(?:第[0-9０-９一二三四五六七八九十百千万零〇两]+[章节回卷][^\r\n]*|(?:番外|序章|序言|楔子|尾声|终章|后记)[^\r\n]*)(?=[\r\n]|\Z)")
    heads = [(m.start(), m.group().strip()[:200]) for m in pattern.finditer(text)]
    if not heads or heads[0][0] != 0:
        heads.insert(0, (0, "未命名文本 / 前言"))
    chunks = []
    for index, (start, title) in enumerate(heads):
        boundary = heads[index + 1][0] if index + 1 < len(heads) else len(text)
        while start < boundary:
            end = min(start + maximum, boundary)
            if end < boundary:
                newline = text.rfind("\n", start + maximum // 2, end)
                if newline >= 0:
                    end = newline + 1
            chunks.append((start, end, title))
            start = end
    return chunks


TEMPLATES = {
    "notes": [{"id": "hero", "kind": "character", "text": "<填写人物当前状态>",
               "tags": ["主角"], "source": "<填写用户要求或原文位置>", "critical": False,
               "status": "active", "due": None}],
    "plan": {"title": "<填写章节名称，不含章号>", "volume_dir": "第一卷 <填写卷名>",
             "goal": "<填写本章推进目标>", "beats": [{"choice": "<填写人物选择>", "change": "<填写后果与变化>"}],
             "stop": "<填写停笔点>", "constraints": ["<填写用户原始硬要求>"], "length": [2200, 2800],
             "requires": ["hero"], "tags": ["主角"], "count_method": "visible_nonspace_v1", "count_title": False},
    "delta": {"book_id": "<填写context返回的book_id>", "base_revision": 0, "summary": "<填写本章结果与下章衔接>", "changes": [],
              "review": {"draft_sha256": "<填写lint返回的SHA256>", "checks": {
                  key: {"note": "<填写核对结论及理由>", "quote": "<填写正文精确摘录>"} for key in CHECKS}, "issues": []}},
    "analysis": {"chunk_sha256": "<填写next返回的sha>", "summary": "<填写本块事件与选择>",
                 "findings": [{"kind": "因果", "claim": "<填写可证实的结论>", "quote": "<填写本块精确摘录>"}]},
}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="command", required=True)
    t = sub.add_parser("template", help="Print just one JSON input example")
    t.add_argument("kind", choices=TEMPLATES)
    def command(name, help_text, budget=None):
        s = sub.add_parser(name, help=help_text)
        s.add_argument("--book", required=True, help="Explicit, isolated book directory")
        s.add_argument("--integrity", choices=("strict", "local"), default="strict", help="strict hashes all exports; local verifies recent/queued files and reports unverified archives")
        if budget:
            s.add_argument("--budget-bytes", type=int, default=budget, help="Hard limit on compact JSON UTF-8 bytes")
        return s
    s = command("init", "Create state once; never overwrite an existing book")
    s.add_argument("--title", required=True)
    s.add_argument("--kind", choices=("long", "short", "analysis"), default="long")
    command("status", "Compact checkpoint and export health")
    command("migrate", "Explicit schema upgrade with a consistent rollback backup")
    command("audit", "Hash every managed export and report conflicts")
    s = command("world-save", "Save structured story state with cited evidence")
    s.add_argument("--input", required=True)
    s.add_argument("--expect", type=int, required=True)
    s = command("world-check", "Check recorded rules, knowledge and continuity", 16000)
    s.add_argument("--chapter", type=int, required=True)
    s = command("world-read", "Inspect one stored narrative record, including stale evidence for reviewed repair", 12000)
    s.add_argument("--kind", choices=sorted(set(world.FIELDS)-{"aliases"}), required=True)
    s.add_argument("--id", required=True)
    history.register_parser(sub, command)
    s = command("export", "Repair exports without replacing outside edits")
    s.add_argument("--safe-only", action="store_true",
                   help="Recover known versions and missing files while leaving conflicting paths untouched")
    s = command("assemble-short", "Assemble all committed short-story chapters into one book-title text file")
    s.add_argument("--final-chapter", type=int, required=True,
                   help="Expected last committed chapter; narrative completion requires editorial review")
    for name in ("notes", "plan"):
        s = command(name, "Save cards or a chapter plan with optimistic concurrency")
        s.add_argument("--input", required=True)
        s.add_argument("--expect", type=int, required=True)
        if name == "plan":
            s.add_argument("--chapter", type=int, required=True)
    s = command("context", "Build a bounded chapter context packet", 16000)
    s.add_argument("--chapter", type=int, required=True)
    s = command("prepare", "Read-only review scaffold bound to the current book, state and draft", 16000)
    s.add_argument("--chapter", type=int, required=True)
    s.add_argument("--draft", required=True)
    s.add_argument("--reconcile", action="store_true", help="Bind the inspected outside edit for a reviewed reconciliation")
    s = command("reconcile", "Inspect an outside edit of the latest native chapter, or apply its reviewed draft", 16000)
    s.add_argument("--chapter", type=int, required=True)
    s.add_argument("--draft")
    s.add_argument("--input")
    s = command("recall", "Bounded literal substring search of cards, summaries and chapter prose", 8000)
    s.add_argument("--query", required=True)
    s = command("chapter-read", "Read a bounded excerpt of a current or immutable historical chapter", 12000)
    s.add_argument("--chapter", type=int, required=True)
    s.add_argument("--sha256")
    s.add_argument("--start", type=int, default=0)
    s.add_argument("--end", type=int)
    s = command("dependencies", "Resolve candidate evidence hashes for a reviewed chapter dependency declaration", 16000)
    s.add_argument("--chapter", type=int, required=True)
    s = command("sources", "Find saved source IDs and analysis checkpoints after a new session", 12000)
    s.add_argument("--offset", type=int, default=0)
    s.add_argument("--limit", type=int, default=10)
    for name in ("lint", "commit", "adopt", "adopt-backfill"):
        s = command(name, "Check, commit, or import one last complete chapter")
        s.add_argument("--chapter", type=int, required=True)
        s.add_argument("--draft", required=True)
        if name == "commit":
            s.add_argument("--input", required=True)
            s.add_argument("--replace-last", action="store_true")
        if name in ("adopt", "adopt-backfill"):
            s.add_argument("--volume-dir", help="Named volume directory, for example 第一卷 雨夜; otherwise use the saved chapter plan")
            s.add_argument("--summary", required=True)
            s.add_argument("--expect", type=int, required=True)
    s = command("ingest", "Snapshot source text and split without losing bonus chapters")
    s.add_argument("--file", required=True)
    s.add_argument("--coverage", choices=("complete", "partial", "unknown"), default="unknown")
    s.add_argument("--encoding", default="utf-8-sig")
    s.add_argument("--chunk-chars", type=int, default=2400)
    for name, budget in (("coverage", None), ("next", 22000), ("record", None),
                         ("findings", 20000), ("source-read", 12000), ("report", None)):
        s = command(name, "Incremental source analysis and evidence-backed checkpoints", budget)
        s.add_argument("--source", required=True)
        if name in ("next", "findings"):
            s.add_argument("--limit", type=int, default=2 if name == "next" else 10)
        if name == "findings":
            s.add_argument("--offset", type=int, default=0)
        if name == "source-read":
            s.add_argument("--start", type=int, required=True)
            s.add_argument("--end", type=int, required=True)
        if name == "record":
            s.add_argument("--chunk", type=int, required=True)
            s.add_argument("--input", required=True)
            s.add_argument("--replace", action="store_true")
        if name == "report":
            s.add_argument("--file", required=True)
            s.add_argument("--expect-analysis", help="findings.analysis_sha256 captured while aggregating; required for a new final report")
    return p


def run(args):
    cmd = args.command
    if cmd == "template":
        return TEMPLATES[args.kind]
    if cmd == "init":
        return Book.create(args.book, args.title, args.kind)
    if cmd == "migrate":
        return storage.migrate(CORE, args.book)
    book = Book(args.book, integrity=getattr(args, "integrity", "strict"))
    try:
        if cmd.startswith("history-") or cmd.startswith("cache-"):
            return history.run(book, args)
        if cmd == "world-save":
            return world.save(book, read_json(args.input), args.expect)
        if cmd == "world-check":
            return book.world_check(args.chapter, args.budget_bytes)
        if cmd == "world-read":
            return book.world_read(args.kind, args.id, args.budget_bytes)
        if cmd == "audit":
            book.integrity = "strict"
            return book.export(safe_only=True)
        if cmd == "status":
            return book.status()
        if cmd == "export":
            return book.export(safe_only=args.safe_only)
        if cmd == "assemble-short":
            return book.assemble_short(args.final_chapter)
        if cmd == "notes":
            return book.save_notes(read_json(args.input), args.expect)
        if cmd == "plan":
            return book.save_plan(args.chapter, read_json(args.input), args.expect)
        if cmd == "context":
            return book.context(args.chapter, args.budget_bytes)
        if cmd == "prepare":
            return book.prepare(args.chapter, args.draft, args.reconcile, args.budget_bytes)
        if cmd == "reconcile":
            return book.reconcile(args.chapter, args.draft, read_json(args.input) if args.input else None,
                                  args.budget_bytes)
        if cmd == "recall":
            return book.recall(args.query, args.budget_bytes)
        if cmd == "chapter-read":
            return book.chapter_read(args.chapter, args.sha256, args.start, args.end, args.budget_bytes)
        if cmd == "dependencies":
            return book.dependency_candidates(args.chapter, args.budget_bytes)
        if cmd == "sources":
            return book.list_sources(args.offset, args.limit, args.budget_bytes)
        if cmd == "lint":
            return book.lint(args.chapter, args.draft)
        if cmd == "commit":
            return book.commit(args.chapter, args.draft, read_json(args.input), args.replace_last)
        if cmd == "adopt":
            return book.adopt(args.chapter, args.draft, args.summary, args.expect, args.volume_dir)
        if cmd == "adopt-backfill":
            return book.adopt_backfill(args.chapter, args.draft, args.summary, args.expect, args.volume_dir)
        if cmd == "ingest":
            return book.ingest(args.file, args.coverage, args.encoding, args.chunk_chars)
        if cmd == "coverage":
            return book.coverage(args.source)
        if cmd == "next":
            return book.next_chunks(args.source, args.limit, args.budget_bytes)
        if cmd == "record":
            return book.record(args.source, args.chunk, read_json(args.input), args.replace)
        if cmd == "findings":
            return book.findings(args.source, args.offset, args.limit, args.budget_bytes)
        if cmd == "source-read":
            return book.source_read(args.source, args.start, args.end, args.budget_bytes)
        if cmd == "report":
            return book.report(args.source, args.file, args.expect_analysis)
        raise AssertionError(cmd)
    finally:
        book.close()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        sys.stderr.reconfigure(encoding="utf-8", newline="\n")
    args = parser().parse_args()
    try:
        result = run(args)
        print(dumps(result))
        if isinstance(result, dict) and (result.get("ok") is False or result.get("exports_complete") is False):
            return 2
        return 0
    except StoryError as error:
        print(dumps({"ok": False, "error": error.code, "message": error.message, **error.details}), file=sys.stderr)
        return 2
    except (OSError, ValueError, sqlite3.Error, LookupError) as error:
        print(dumps({"ok": False, "error": "io_or_input_error", "message": str(error)}), file=sys.stderr)
        return 2


def _load_extension(name):
    spec = importlib.util.spec_from_file_location("story_codex_" + name, Path(__file__).with_name("story_" + name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


storage, search, world, history = (_load_extension(name) for name in ("storage", "search", "world", "history"))
CORE = SimpleNamespace(**globals())
for extension in (search, world, history):
    extension.inject(CORE)
TEMPLATES["world"] = world.template()
for world_kind in world.FIELDS:
    TEMPLATES["world-" + world_kind] = world.template(world_kind)


if __name__ == "__main__":
    raise SystemExit(main())
