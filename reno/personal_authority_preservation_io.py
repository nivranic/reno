"""Fixed private byte/resource primitives shared by preservation versions.

Version-owned targets keep their original live-registry checks, budgets and
layout. This module does not mint tokens, select a version, accept an external
I/O adapter, or produce a completion observation. All operations retain their
per-effect identity/first/deadline gates and exhaustive close behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import os
import posixpath

from . import personal_authority_preservation_source as source
from . import personal_authority_preservation_contract as c2
from . import personal_preservation_files as files
from . import sync_media as safe


@dataclass
class _Directory:
    original: object
    identity: dict
    handles: tuple
    key: str | None
    private: bool

@dataclass
class _File:
    parent: _Directory
    name: str
    key: str
    stream: object
    descriptor: int
    path_identity: dict
    handle_identity: dict
    sha256: str | None = None
    raw: bytes | None = None


class _TargetIO:
    def keep(self, kind, value):
        self.resource_indices[(kind, id(value))] = len(self.resources)
        self.resources.append((kind, value))
        return value

    def released(self, kind, value):
        index = self.resource_indices.pop((kind, id(value)))
        if self.resources[index] != (kind, value):
            self._fail("OWNER_MISMATCH")
        self.resources[index] = None
        if kind == "stream":
            self.reader_records.pop(id(value), None)

    def open_directory(self, path, *, parent=None, key=None, private=False):
        self.check()
        original = source._OwnedDirectory(path, base=path if parent is None else self.operation.profile.database.parent,
                                          parent=None if parent is None else parent.original)
        original.__enter__()
        self.keep("directory", original)
        record = _Directory(original, self._identity(original), tuple(original.handles), key, private)
        self.directory_records[id(original)] = record
        if private:
            files.require_private(original.path, directory=True)
        self.verify_directory(record)
        if key is not None:
            if key in self.directories or len(self.directories) + len(self.files) >= self.limits["max_target_entries"]:
                self._fail("TARGET_UNSAFE")
            self.directories[key] = record
        return record

    def verify_directory(self, record):
        self.check()
        if tuple(record.original.handles) != record.handles or self._identity(record.original) != record.identity:
            self._fail("TARGET_CHANGED")
        if record.private:
            files.require_private(record.original.path, directory=True)

    def mkdir(self, parent, name, *, key=None, allow_existing=False):
        c2._component(name)
        self.verify_directory(parent)
        created = False
        try:
            if os.name == "nt":
                os.mkdir(safe._native_path(parent.original.path / name))
            else:
                os.mkdir(name, mode=0o700, dir_fd=parent.original.fd)
            created = True
            self.new_effects.append((key, "directory"))
        except FileExistsError:
            if not allow_existing:
                self._fail("OPERATION_EXISTS")
            safe._safe_stat(parent.original.entry_stat(name), directory=True)
        self.verify_directory(parent)
        if created and os.name == "nt":
            files._windows_acl(parent.original.path / name, directory=True, initialize=True)
        if created and os.name != "nt":
            os.fsync(parent.original.fd)
        return self.open_directory(parent.original.path / name, parent=parent, key=key, private=True)

    def listing(self, record):
        self.verify_directory(record)
        result = []
        original = record.original
        with os.scandir(safe._native_path(original.path) if os.name == "nt" else original.fd) as entries:
            for entry in entries:
                self.check()
                if len(result) >= self.limits["max_target_entries"]:
                    self._fail("TARGET_UNSAFE")
                c2._component(entry.name)
                result.append(entry.name)
        self.verify_directory(record)
        return tuple(sorted(result, key=lambda name: name.encode("utf-8")))

    def verify_file(self, record):
        self.verify_directory(record.parent)
        if (type(record.stream) is not io.BufferedReader or record.stream.closed
                or record.stream.fileno() != record.descriptor
                or files.identity(record.parent.original.entry_stat(record.name)) != record.path_identity
                or files.identity(os.fstat(record.descriptor)) != record.handle_identity):
            self._fail("TARGET_CHANGED")

    def read_file(self, record, *, collect=False):
        expected = record.path_identity["byte_size"]
        if collect and expected > self.limits["max_fact_bytes"]:
            self._fail("TARGET_UNSAFE")
        self.verify_file(record)
        self.budget.note_seek()
        self._seek_original(record.stream, 0)
        digest, size, chunks = hashlib.sha256(), 0, []
        while True:
            self.verify_file(record)
            count = min(self.limits["chunk_bytes"], expected - size + 1)
            ticket = self.budget.begin_read(count)
            try:
                raw = self._read_bytes_original(record.stream, count)
            except BaseException as exc:
                self.budget.fail_read(ticket, exc)
            self.budget.end_read(ticket, raw)
            self.verify_file(record)
            if not raw:
                break
            size += len(raw)
            if size > expected:
                self._fail("TARGET_CHANGED")
            digest.update(raw)
            if collect:
                chunks.append(raw)
        self.verify_file(record)
        if size != expected or record.sha256 is not None and record.sha256 != digest.hexdigest():
            self._fail("TARGET_CHANGED")
        return digest.hexdigest(), b"".join(chunks) if collect else None

    def open_reader(self, parent, name, key, *, collect=False):
        self.verify_directory(parent)
        before = files.identity(parent.original.entry_stat(name))
        stream = self.keep("stream", parent.original.open_file(name))
        if type(stream) is not io.BufferedReader:
            self._fail("TARGET_UNSAFE")
        files.require_private(parent.original.path / name, directory=False)
        after, held = files.identity(parent.original.entry_stat(name)), files.identity(os.fstat(stream.fileno()))
        if before != after or any(after[field] != held[field] for field in after if field != "ctime_ns"):
            self._fail("TARGET_CHANGED")
        record = _File(parent, name, key, stream, stream.fileno(), after, held)
        self.reader_records[id(stream)] = record
        record.sha256, record.raw = self.read_file(record, collect=collect)
        if key in self.files or len(self.files) + len(self.directories) >= self.limits["max_target_entries"]:
            self._fail("TARGET_UNSAFE")
        self.files[key] = record
        return record

    def writer(self, parent, name, key):
        self.verify_directory(parent)
        stream = self.keep("writer", parent.original.open_file(name, write=True, create=True))
        self.new_effects.append((key, "file"))
        if type(stream) is not io.BufferedRandom:
            self._fail("TARGET_UNSAFE")
        if os.name == "nt":
            files._windows_acl(parent.original.path / name, directory=False, initialize=True)
        files.require_private(parent.original.path / name, directory=False)
        before = files.identity(os.fstat(stream.fileno()))
        if before["byte_size"] != 0:
            self._fail("TARGET_CHANGED")
        return {"parent": parent, "name": name, "key": key, "stream": stream,
                "descriptor": stream.fileno(), "identity": before, "written": 0}

    def verify_writer(self, writer):
        self.verify_directory(writer["parent"])
        stream = writer["stream"]
        if type(stream) is not io.BufferedRandom or stream.closed or stream.fileno() != writer["descriptor"]:
            self._fail("TARGET_CHANGED")
        path = files.identity(writer["parent"].original.entry_stat(writer["name"]))
        held = files.identity(os.fstat(writer["descriptor"]))
        for row in (path, held):
            if (any(row[field] != writer["identity"][field] for field in ("device", "inode", "mode", "nlink"))
                    or row["byte_size"] != writer["written"]):
                self._fail("TARGET_CHANGED")

    def write_chunk(self, writer, raw):
        if type(raw) is not bytes or len(raw) > self.limits["chunk_bytes"]:
            self._fail("TARGET_UNSAFE")
        offset = 0
        while offset < len(raw):
            self.verify_writer(writer)
            remaining = raw[offset:]
            ticket = self.budget.begin_write(len(remaining))
            try:
                count = self._write_bytes_original(writer["stream"], remaining)
            except BaseException as exc:
                self.budget.fail_write(ticket, exc)
            if type(count) is int and 0 <= count <= len(remaining):
                writer["written"] += count
            self.budget.end_write(ticket, count)
            self.verify_writer(writer)
            offset += count

    def close_writer(self, writer):
        self.verify_writer(writer)
        self._flush_original(writer["stream"])
        self.verify_writer(writer)
        self._fsync_original(writer["stream"])
        self.verify_writer(writer)
        try:
            self._close_stream_original(writer["stream"])
        except BaseException as exc:
            raise self._error(exc, "CLOSE_FAILED") from None
        self.released("writer", writer["stream"])
        if os.name != "nt":
            os.fsync(writer["parent"].original.fd)
        self.verify_directory(writer["parent"])

    def inventory(self):
        children = {key: [] for key in self.directories}
        for key in set(self.directories) | set(self.files):
            if key:
                children[posixpath.dirname(key)].append(posixpath.basename(key))
        directories = [{"relative_key": key, "identity": dict(record.identity),
                        "children": sorted(children[key], key=lambda name: name.encode("utf-8"))}
                       for key, record in sorted(self.directories.items())]
        listed = [{"relative_key": key, "parent_identity": dict(record.parent.identity),
                   "path_identity": dict(record.path_identity), "handle_identity": dict(record.handle_identity),
                   "sha256": record.sha256, "byte_size": record.path_identity["byte_size"]}
                  for key, record in sorted(self.files.items())]
        return {"directories": directories, "files": listed}

    def verify_pass(self):
        self.check()
        for root in self.roots.values():
            self.verify_directory(root)
        self.verify_directory(self.namespace)
        expected = self.inventory()
        for row in expected["directories"]:
            if self.listing(self.directories[row["relative_key"]]) != tuple(row["children"]):
                self._fail("TARGET_CHANGED")
        for record in self.files.values():
            self.read_file(record)
        self.check(callback=False)


def _close_scope_original(state, primary):
    """Attempt every original closer; return the first problem, if any."""
    first = primary
    for item in reversed(state.resources):
        if item is None:
            continue
        kind, original = item
        # A failed last identity/deadline gate must not skip this closer.
        if first is None:
            try:
                state.check(callback=False)
                if kind == "stream":
                    record = state.reader_records[id(original)]
                    if record.stream is not original:
                        state._fail("OWNER_MISMATCH")
                    state.verify_file(record)
                elif kind == "directory":
                    record = state.directory_records[id(original)]
                    if record.original is not original:
                        state._fail("OWNER_MISMATCH")
                    state.verify_directory(record)
                else:
                    # Every writer must already have closed before readback.
                    state._fail("INCOMPLETE")
            except BaseException as exc:
                first = state._error(exc, "CLOSE_FAILED")
        try:
            if kind in {"stream", "writer"}:
                state._close_stream_original(original)
            else:
                original.__exit__(None if first is None else type(first), first,
                                  None if first is None else first.__traceback__)
        except BaseException as exc:
            if first is None:
                first = state._error(exc, "CLOSE_FAILED")
            elif exc is not first:
                first.add_note("AUTHORITY_RAW_PRESERVATION_CLOSE_FAILED")
    state.resources.clear()
    state.resource_indices.clear()
    state.reader_records.clear()
    state.directory_records.clear()
    return first
