"""Shared no-follow source readers for SQLite identity and committed images.

Only DB, WAL and rollback-journal metadata are observed; source SHM is never
opened or inspected. Domain callers translate SourceReadError to their own
public errors and own the copy-size, retry and schema-reading budgets.
"""
from __future__ import annotations

from contextlib import contextmanager
import os

from . import sync_media as safe

DEFAULT_COPY_MAX_BYTES = 256 * 1024 * 1024
MAX_CAPTURE_ATTEMPTS = 2


class SourceReadError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def file_signature(info):
    safe._safe_stat(info, directory=False)
    if info.st_nlink != 1:
        raise SourceReadError("SOURCE_UNSAFE")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def source_state(directory, name):
    result = {}
    for suffix in ("", "-wal", "-journal"):
        try:
            result[suffix] = file_signature(directory.entry_stat(name + suffix))
        except FileNotFoundError:
            if not suffix:
                raise
            result[suffix] = None
    directory.verify()
    return result


@contextmanager
def source_reader(directory, name):
    """Read a live SQLite file without denying its existing RW connection.

    This sharing policy is local to source copies; ordinary media verification
    continues to deny writes/deletes. Never create a missing source file.
    """
    before = file_signature(directory.entry_stat(name))
    if os.name == "nt":
        handle = safe._kernel.CreateFileW(safe._native_path(directory.path / name),
            0x80000000, 3, None, 3, 0x00200000, None)  # READ, share READ|WRITE, OPEN_EXISTING, NO_REPARSE
        if handle == safe.ctypes.c_void_p(-1).value:
            raise safe.ctypes.WinError(safe.ctypes.get_last_error())
        try:
            info = safe._HandleInfo()
            if not safe._kernel.GetFileInformationByHandle(handle, safe.ctypes.byref(info)):
                raise safe.ctypes.WinError(safe.ctypes.get_last_error())
            if info.attributes & (safe._REPARSE | 0x10) or info.links != 1:
                raise SourceReadError("SOURCE_UNSAFE")
            fd = safe.msvcrt.open_osfhandle(handle, os.O_BINARY | os.O_RDONLY)
        except BaseException:
            safe._kernel.CloseHandle(handle)
            raise
        stream = os.fdopen(fd, "rb", buffering=0)
    else:
        stream = directory.open_file(name)
    with stream:
        opened = file_signature(os.fstat(stream.fileno()))
        # Windows path stat and fstat expose different ctime semantics. Compare
        # ctime only within the same observation API, never across the two.
        if opened[:4] != before[:4]:
            raise SourceReadError("SOURCE_CHANGED")
        yield stream
        if (file_signature(os.fstat(stream.fileno())) != opened
                or file_signature(directory.entry_stat(name)) != before):
            raise SourceReadError("SOURCE_CHANGED")
        directory.verify()


def copy_file(directory, name, destination, expected, *, reader=None, destination_initializer=None):
    """Copy exactly the observed bytes to a new private destination.

    reader preserves legacy domain instrumentation without duplicating source
    sharing or ctime logic. A private destination owner may establish file
    permissions after O_EXCL, before copying the first original byte.
    Cancellation remains a caller's file-boundary check.
    """
    if destination_initializer is not None and not callable(destination_initializer):
        raise SourceReadError("SOURCE_INVALID_ARGUMENT")
    with (reader or source_reader)(directory, name) as source:
        if file_signature(os.fstat(source.fileno()))[:4] != expected[:4]:
            raise SourceReadError("SOURCE_CHANGED")
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as target:
            if destination_initializer is not None:
                destination_initializer(destination)
            remaining = expected[2]
            while remaining:
                chunk = source.read(min(128 * 1024, remaining))
                if not chunk:
                    raise SourceReadError("SOURCE_CHANGED")
                target.write(chunk)
                remaining -= len(chunk)
            if source.read(1):
                raise SourceReadError("SOURCE_CHANGED")
