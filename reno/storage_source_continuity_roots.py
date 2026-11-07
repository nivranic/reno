"""Bounded, optimistic Cloud continuity roots; no writer or admission authority.

Callers supply already captured, public SQL projections. No SQLite, StateStore,
writer lock, initialization or repair is performed. Permanent immutable files
are held; mutable heads are read briefly and physically rechecked at exit.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
from copy import deepcopy
import hashlib
import json
import math
import os
import stat
import time
import uuid

from . import storage_lifecycle as lifecycle, storage_source as source, sync_media as safe
from . import sync_source_continuity as external, sync_source_readmit_contract as wire
from . import sync_source_readmit_schema as schema
from . import sync_state

IDENTITIES = ("source_library_id", "source_node_id", "target_library_id", "target_node_id")
SQL_COLUMNS = {
    schema.ACTIVATION: ("continuity_id", "format_version", "owner_profile_sha256", *IDENTITIES,
        "target_epoch", "contract_sha256", "activated_at"),
    schema.REGISTRATION: ("request_id", "format_version", *IDENTITIES, "continuity_id", "core_sha256", "item_id",
        "original_request_id", "original_request_sha256", "original_kind", "request_sha256", "registration_sha256",
        "result_sha256", "committed_at"),
    schema.DISPOSITION: ("request_id", "format_version", *IDENTITIES, "core_sha256", "item_id", "original_request_id",
        "original_request_sha256", "original_kind", "continuity_id", "registration_sha256", "disposition",
        "request_sha256", "result_sha256", "disposition_sha256", "defer_receipt_sha256", "committed_at"),
    schema.ADMISSION: ("admission_id", "format_version", *IDENTITIES, "core_sha256", "item_id", "submitted_url_sha256",
        "current_home_epoch", "receiver_library_epoch", "recovery_generation", "control_revision", "previous_admission_id",
        "lease_generation", "attempts", "attempt_basis", "request_sha256", "admission_sha256", "result_sha256", "committed_at"),
}
_NULLABLE = {schema.DISPOSITION: {"continuity_id", "registration_sha256", "defer_receipt_sha256"},
             schema.ADMISSION: {"previous_admission_id"}}
_INTEGERS = {"format_version", "item_id", "target_epoch", "current_home_epoch", "receiver_library_epoch",
             "recovery_generation", "control_revision", "lease_generation", "attempts"}
_SHARED = ("original_request_id", "original_request_sha256", "original_kind", "core_sha256", "item_id", "continuity_id")


class ContinuityRootError(ValueError):
    def __init__(self, code, *, retriable=False):
        self.code, self.retriable = code, retriable
        super().__init__(code)


class _Cancelled(BaseException):
    def __init__(self, error):
        self.error = error


def _fail(code, *, retriable=False):
    raise ContinuityRootError("CONTINUITY_ROOT_" + code, retriable=retriable) from None


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class CaptureLimits:
    max_records: int = 4096
    max_journal: int = 8192
    max_parts: int = 32
    max_record_bytes: int = 32 * 1024
    max_total_bytes: int = 64 * 1024 * 1024
    max_sql_rows: int = 100_000
    max_sql_bytes: int = 16 * 1024 * 1024
    deadline_seconds: float = 10.0

    def __post_init__(self):
        for name, value in vars(self).items():
            if name == "deadline_seconds":
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 120:
                    raise ValueError("Invalid capture limit")
            elif (type(value) is not int or value < 1
                    or value > type(self).__dataclass_fields__[name].default):
                raise ValueError("Invalid capture limit")


def normalize_sql(projection, *, limits=None):
    """Detached exact public columns; no body, SQL execution or authority."""
    limits = CaptureLimits() if limits is None else limits
    if not isinstance(limits, CaptureLimits):
        raise ValueError("Invalid SQL projection limits")
    try:
        if type(projection) is not dict or set(projection) != set(SQL_COLUMNS):
            _fail("SQL_PROJECTION_INVALID")
        result, count, total = {}, 0, 0
        for table, fields in SQL_COLUMNS.items():
            rows, seen = projection[table], set()
            if type(rows) is not list:
                _fail("SQL_PROJECTION_INVALID")
            copied = []
            for original in rows:
                count += 1
                if count > limits.max_sql_rows:
                    _fail("SQL_LIMIT")
                if type(original) is not dict or set(original) != set(fields):
                    _fail("SQL_PROJECTION_INVALID")
                row = dict(original)
                for key, value in row.items():
                    if value is None and key in _NULLABLE.get(table, ()):
                        continue
                    if key in _INTEGERS:
                        minimum = 0 if key in {"recovery_generation", "control_revision"} else 1
                        if key == "format_version":
                            maximum = 2 if table in {schema.ADMISSION, schema.DISPOSITION} else 1
                        else:
                            maximum = 5 if key == "attempts" else 2**63 - 1
                        if type(value) is not int or not minimum <= value <= maximum:
                            _fail("SQL_PROJECTION_INVALID")
                    elif key.endswith("_id"):
                        if type(value) is not str or str(uuid.UUID(value)) != value:
                            _fail("SQL_PROJECTION_INVALID")
                    elif key.endswith("_sha256"):
                        external._sha(value)
                    elif key in {"committed_at", "activated_at"}:
                        external._time(value)
                    elif key == "original_kind" and value in {"budget_defer", "budget_resume", "source_fail"}:
                        pass
                    elif key == "disposition" and value in {"applied", "not_applied"}:
                        pass
                    elif key == "attempt_basis" and value in {"held_claim", "original_defer_refund"}:
                        pass
                    else:
                        _fail("SQL_PROJECTION_INVALID")
                primary = row[fields[0]]
                if primary in seen:
                    _fail("SQL_PROJECTION_INVALID")
                seen.add(primary)
                total += len(wire.canonical_json(row).encode())
                if total > limits.max_sql_bytes:
                    _fail("SQL_LIMIT")
                copied.append(row)
            result[table] = sorted(copied, key=lambda row: row[fields[0]])
        activations = {row["continuity_id"] for row in result[schema.ACTIVATION]}
        registrations = {row["registration_sha256"]: row for row in result[schema.REGISTRATION]}
        if len(registrations) != len(result[schema.REGISTRATION]):
            _fail("SQL_PROJECTION_INVALID")
        for row in result[schema.REGISTRATION]:
            if row["continuity_id"] not in activations:
                _fail("SQL_RELATION_INVALID")
        for row in result[schema.DISPOSITION]:
            if ((row["continuity_id"] is None) != (row["registration_sha256"] is None)
                    or row["disposition"] == "not_applied" and row["continuity_id"] is None):
                _fail("SQL_RELATION_INVALID")
            if row["continuity_id"] is not None:
                parent = registrations.get(row["registration_sha256"])
                if parent is None or any(parent[key] != row[key] for key in (*_SHARED, *IDENTITIES)):
                    _fail("SQL_RELATION_INVALID")
        admissions = {row["admission_id"]: row for row in result[schema.ADMISSION]}
        branches = set()
        for row in admissions.values():
            branch = (row["core_sha256"], row["item_id"], row["previous_admission_id"])
            if branch in branches:
                _fail("SQL_RELATION_INVALID")
            branches.add(branch)
            parent = admissions.get(row["previous_admission_id"])
            if row["previous_admission_id"] is not None and (parent is None or parent is row
                    or any(parent[key] != row[key] for key in ("core_sha256", "item_id", *IDENTITIES))
                    or row["lease_generation"] <= parent["lease_generation"]):
                _fail("SQL_RELATION_INVALID")
        return result
    except ContinuityRootError:
        raise
    except (ValueError, TypeError, KeyError, OverflowError, safe.SyncError):
        _fail("SQL_PROJECTION_INVALID")


class _Reader:
    def __init__(self, profile, stack, limits, callback):
        self.profile, self.stack, self.limits, self.callback = profile, stack, limits, callback
        self.started, self.total = time.monotonic(), 0
        self.directories, self.files, self.absences, self.names = [], [], [], []
        self.known_refs = {}

    def check(self):
        if self.callback is not None:
            try:
                self.callback()
            except BaseException as exc:
                raise _Cancelled(exc) from None
        if time.monotonic() - self.started > self.limits.deadline_seconds:
            _fail("DEADLINE_LIMIT")

    def directory(self, path, *, base):
        self.check()
        directory = self.stack.enter_context(safe._Directory(path, base=base))
        self.directories.append(directory)
        return directory

    def read(self, directory, name, *, mutable=False, optional=False, decode=True):
        self.check()
        try:
            before_info = directory.entry_stat(name)
        except FileNotFoundError:
            if not optional:
                _fail("PARTIAL")
            self.absences.append((directory, name))
            return None
        before = source.file_signature(before_info)
        if before[2] > self.limits.max_record_bytes:
            _fail("RECORD_LIMIT")
        if os.name != "nt" and (before_info.st_uid != os.geteuid() or stat.S_IMODE(before_info.st_mode) & 0o077):
            _fail("UNSAFE")
        self.total += before[2]
        if self.total > self.limits.max_total_bytes:
            _fail("TOTAL_LIMIT")
        # Heads are mutable. Release their ordinary read handles promptly;
        # immutable records may stay held without blocking a lawful append.
        context = source.source_reader(directory, name) if mutable else directory.open_file(name)
        stream = context.__enter__() if mutable else self.stack.enter_context(context)
        try:
            opened = source.file_signature(os.fstat(stream.fileno()))
            if before[:4] != opened[:4]:
                _fail("SOURCE_CHANGED", retriable=True)
            raw, size = bytearray(), 0
            while True:
                self.check()
                chunk = os.read(stream.fileno(), min(128 * 1024, self.limits.max_record_bytes + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                raw.extend(chunk)
                if size > before[2]:
                    _fail("SOURCE_CHANGED", retriable=True)
            if size != before[2] or source.file_signature(os.fstat(stream.fileno())) != opened:
                _fail("SOURCE_CHANGED", retriable=True)
        finally:
            if mutable:
                context.__exit__(None, None, None)
        if source.file_signature(directory.entry_stat(name)) != before:
            _fail("SOURCE_CHANGED", retriable=True)
        raw = bytes(raw)
        self.files.append((directory, name, None if mutable else stream, before, opened, hashlib.sha256(raw).digest()))
        if not decode:
            return raw
        value = sync_state._decode(raw)
        if external._encoded(value) != raw:
            _fail("RECORD_INVALID")
        return value

    def listing(self, directory, allowed, maximum, *, subdirectories=()):
        self.check()
        names, ordinary, parts = [], 0, 0
        with os.scandir(directory.fd if os.name != "nt" else safe._native_path(directory.path)) as entries:
            for entry in entries:
                self.check()
                names.append(entry.name)
                info = directory.entry_stat(entry.name)
                safe._safe_stat(info, directory=entry.name in subdirectories)
                if external._PART.fullmatch(entry.name):
                    parts += 1
                    if parts > self.limits.max_parts:
                        _fail("PART_LIMIT")
                    self.read(directory, entry.name, decode=False)
                    continue
                ordinary += 1
                if ordinary > maximum:
                    _fail("COUNT_LIMIT")
                if not allowed(entry.name):
                    _fail("UNEXPECTED_FILE")
        names.sort()
        self.names.append((directory, tuple(names)))
        return [name for name in names if not external._PART.fullmatch(name)]

    def _verify_namespace(self):
        self.check()
        for directory in self.directories:
            directory.verify()
        for directory, name in self.absences:
            self.check()
            try:
                directory.entry_stat(name)
            except FileNotFoundError:
                continue
            _fail("SOURCE_CHANGED", retriable=True)
        for directory, names in self.names:
            self.check()
            current = []
            with os.scandir(directory.fd if os.name != "nt" else safe._native_path(directory.path)) as entries:
                for entry in entries:
                    self.check()
                    if len(current) >= len(names):
                        _fail("SOURCE_CHANGED", retriable=True)
                    current.append(entry.name)
            if tuple(sorted(current)) != names:
                _fail("SOURCE_CHANGED", retriable=True)

    def verify(self):
        self._verify_namespace()
        for directory, name, stream, before, opened, digest in self.files:
            self.check()
            if source.file_signature(directory.entry_stat(name)) != before:
                _fail("SOURCE_CHANGED", retriable=True)
            if stream is None:
                with source.source_reader(directory, name) as current:
                    found = hashlib.sha256()
                    while chunk := os.read(current.fileno(), 128 * 1024):
                        self.check()
                        found.update(chunk)
            else:
                if source.file_signature(os.fstat(stream.fileno())) != opened:
                    _fail("SOURCE_CHANGED", retriable=True)
                os.lseek(stream.fileno(), 0, os.SEEK_SET)
                found = hashlib.sha256()
                while chunk := os.read(stream.fileno(), 128 * 1024):
                    self.check()
                    found.update(chunk)
            if found.digest() != digest or source.file_signature(directory.entry_stat(name)) != before:
                _fail("SOURCE_CHANGED", retriable=True)

    def verify_for_commit(self):
        """Use held immutable pins; reread only bounded mutable heads/lock."""
        self._verify_namespace()
        for directory, name, stream, before, opened, digest in self.files:
            self.check()
            if source.file_signature(directory.entry_stat(name)) != before:
                _fail("SOURCE_CHANGED", retriable=True)
            if stream is None:
                with source.source_reader(directory, name) as current:
                    if source.file_signature(os.fstat(current.fileno())) != opened:
                        _fail("SOURCE_CHANGED", retriable=True)
                    found, remaining = hashlib.sha256(), before[2]
                    while remaining:
                        self.check()
                        chunk = os.read(current.fileno(), min(128 * 1024, remaining))
                        if not chunk:
                            _fail("SOURCE_CHANGED", retriable=True)
                        remaining -= len(chunk)
                        found.update(chunk)
                    self.check()
                    if (os.read(current.fileno(), 1) or found.digest() != digest
                            or source.file_signature(os.fstat(current.fileno())) != opened):
                        _fail("SOURCE_CHANGED", retriable=True)
            elif source.file_signature(os.fstat(stream.fileno())) != opened:
                _fail("SOURCE_CHANGED", retriable=True)
            if source.file_signature(directory.entry_stat(name)) != before:
                _fail("SOURCE_CHANGED", retriable=True)
        self.check()


class ContinuityCapture:
    def __init__(self, reader, guard):
        self._reader, self._guard, self._active = reader, guard, True
        self._summary, self._references, self._unknown = {}, (), ()

    @property
    def summary(self):
        return deepcopy(self._summary)

    @property
    def references(self):
        return deepcopy(self._references)

    @property
    def unknown(self):
        return deepcopy(self._unknown)

    def verify_source(self):
        if not self._active:
            _fail("CAPTURE_CLOSED")
        try:
            self._guard.verify()
            self._reader.verify()
        except (source.SourceReadError, safe.SyncError, OSError):
            _fail("SOURCE_CHANGED", retriable=True)

    def verify_for_commit(self):
        """Short physical CAS inside a SQL window; grants no authority.

        Entry and exit still perform the full SHA verification. This method
        relies only on those already held immutable pins and rereads the small
        mutable heads/lock, without hashing the complete ledger in SQL.
        """
        if not self._active:
            _fail("CAPTURE_CLOSED")
        try:
            self._guard.verify()
            self._reader.verify_for_commit()
        except (source.SourceReadError, safe.SyncError, OSError):
            _fail("SOURCE_CHANGED", retriable=True)

    def public_summary(self):
        return deepcopy({"summary": self.summary, "references": list(self.references), "unknown": list(self.unknown)})


def _capture(reader, sql, prefix):
    profile = reader.profile
    data = reader.directory(profile.database.parent, base=profile.database.parent)
    checkpoints = reader.directory(profile.checkpoints, base=profile.checkpoints)
    intent = reader.read(data, external.INTENT_NAME, optional=True)
    seal = reader.read(checkpoints, external.SEAL_NAME, optional=True)
    heads = [reader.read(directory, name, mutable=True, optional=True) for directory, name in
             ((data, external.DB_HEAD_NAME), (checkpoints, external.CHECKPOINT_HEAD_NAME))]
    lock = reader.read(data, external.LOCK_NAME, mutable=True, optional=True, decode=False)
    try:
        ledger = reader.directory(data.path / external.DIRECTORY, base=data.path)
    except FileNotFoundError:
        reader.absences.append((data, external.DIRECTORY))
        ledger = None
    references = [{"record_kind": table, "record_id": row[fields[0]], "projection_sha256": _digest(row)}
                  for table, fields in SQL_COLUMNS.items() for row in sql[table]]
    if intent is None and seal is None and heads == [None, None] and ledger is None and lock is None:
        if sql[schema.ACTIVATION] or prefix is not None:
            _fail("SQL_EXTERNAL_MISSING")
        return {"format_version": 1, "kind": "cloud_continuity", "presence": "absent",
            "absence_sha256": _digest({"format_version": 1, "kind": "cloud_continuity", "presence": "absent"}),
            "owner_profile_sha256": external.profile_sha256(profile), "identity": None,
            "contract_sha256": None, "head_ordinal": 0, "head_sha256": None, "journal_sha256": None,
            "record_set_sha256": None, "reference_set_sha256": _digest(references), "record_count": 0,
            "journal_count": 0}, references
    if intent is None or seal is None or ledger is None or None in heads or lock != external.LOCK_MARKER:
        _fail("PARTIAL")
    active = external.validate_contract(intent, profile=profile)
    if seal != active:
        _fail("CONTRACT_MISMATCH")
    reader.listing(ledger, lambda name: name in {"contract.json", "journal", *external.KINDS}, 5,
                   subdirectories={"journal", *external.KINDS})
    if reader.read(ledger, "contract.json") != active:
        _fail("CONTRACT_MISMATCH")
    for head in heads:
        external._exact(head, external._HEAD_FIELDS)
        if (type(head["format_version"]) is not int or head["format_version"] != 1
                or head["continuity_id"] != active["continuity_id"]):
            _fail("HEAD_INVALID")
        external._integer(head["ordinal"], maximum=reader.limits.max_journal)
        external._sha(head["sha256"])
    journal = reader.directory(ledger.path / "journal", base=data.path)
    names = reader.listing(journal, lambda name: bool(external._ORDINAL.fullmatch(name)), reader.limits.max_journal)
    entries, chain, public = {kind: {} for kind in external.KINDS}, [external._digest(active)], []
    for ordinal, name in enumerate(names, 1):
        reader.check()
        if name != f"{ordinal:08d}.json":
            _fail("JOURNAL_INVALID")
        event = reader.read(journal, name)
        external._exact(event, external._JOURNAL_FIELDS)
        if (type(event["format_version"]) is not int or event["format_version"] != 1
                or type(event["ordinal"]) is not int or event["ordinal"] != ordinal
                or event["continuity_id"] != active["continuity_id"] or event["previous_sha256"] != chain[-1]
                or event["kind"] not in entries or event["state"] not in {"reserved", "committed"}):
            _fail("JOURNAL_INVALID")
        record = external._record(event["kind"], event["record"], active)
        if event["key"] != record["original_request_id"] or event["record_sha256"] != external._digest(record):
            _fail("JOURNAL_INVALID")
        previous = entries[event["kind"]].get(event["key"])
        if (previous is None and event["state"] != "reserved" or previous is not None and
                (previous["state"] != "reserved" or event["state"] != "committed" or previous["record"] != record)):
            _fail("JOURNAL_INVALID")
        entries[event["kind"]][event["key"]] = {"state": event["state"], "record": record}
        reader.known_refs[(event["kind"], event["key"])] = {"record_kind": event["kind"],
            "record_id": event["key"], "record_sha256": event["record_sha256"], "state": event["state"],
            **{field: record[field] for field in _SHARED}}
        if sum(map(len, entries.values())) > reader.limits.max_records:
            _fail("COUNT_LIMIT")
        chain.append(external._digest(event))
    expected_head = {"format_version": 1, "continuity_id": active["continuity_id"], "ordinal": len(names), "sha256": chain[-1]}
    if any(head != expected_head for head in heads):
        _fail("HEAD_INCOMPLETE")
    if prefix is not None:
        if (type(prefix) is not dict or set(prefix) != {"continuity_id", "ordinal", "sha256"}
                or prefix["continuity_id"] != active["continuity_id"] or type(prefix["ordinal"]) is not int
                or not 0 <= prefix["ordinal"] < len(chain) or prefix["sha256"] != chain[prefix["ordinal"]]):
            _fail("PREFIX_INVALID")
    for kind in external.KINDS:
        directory = reader.directory(ledger.path / kind, base=data.path)
        found = reader.listing(directory, lambda name: name in {key + ".json" for key in entries[kind]}, reader.limits.max_records)
        if any(entry["state"] != "committed" for entry in entries[kind].values()):
            _fail("RESERVED")
        if set(found) != {key + ".json" for key in entries[kind]}:
            _fail("RECORD_MISSING")
        for key, entry in entries[kind].items():
            if reader.read(directory, key + ".json") != entry["record"]:
                _fail("RECORD_INVALID")
            record = entry["record"]
            chosen = {field: record[field] for field in _SHARED}
            chosen.update(record_kind=kind, record_id=key, record_sha256=external._digest(record))
            for field in ("request_id", "request_sha256", "registration_sha256", "disposition", "begun_at", "committed_at"):
                if field in record:
                    chosen[field] = record[field]
            public.append(chosen)
    for kind in ("effects", "tombstones"):
        for key, entry in entries[kind].items():
            parent = entries["registrations"].get(key)
            if parent is None:
                _fail("REGISTRATION_MISSING")
            external._same_original(entry["record"], parent["record"])
            if entry["record"]["registration_sha256"] != parent["record"]["registration_sha256"]:
                _fail("REGISTRATION_MISMATCH")
            if kind == "tombstones" and key in entries["effects"]:
                _fail("OUTCOME_MISMATCH")
    # Crosscheck captured SQL scalars only; never read private canonical bodies.
    matching = [row for row in sql[schema.ACTIVATION] if row["continuity_id"] == active["continuity_id"]]
    if len(matching) != 1 or len(sql[schema.ACTIVATION]) != 1:
        _fail("SQL_ACTIVATION_MISMATCH")
    activation = matching[0]
    expected = {"owner_profile_sha256": active["owner_profile_sha256"], "target_epoch": active["target_epoch"],
        "contract_sha256": external._digest(active), "activated_at": active["activated_at"], **{
            side + "_" + key: active[side + "_identity"][key] for side in ("source", "target") for key in ("library_id", "node_id")}}
    if any(activation[key] != value for key, value in expected.items()):
        _fail("SQL_ACTIVATION_MISMATCH")
    for table in (schema.REGISTRATION, schema.DISPOSITION, schema.ADMISSION):
        if any(any(row[key] != activation[key] for key in IDENTITIES) for row in sql[table]):
            _fail("SQL_IDENTITY_MISMATCH")
    registrations = {row["original_request_id"]: row for row in sql[schema.REGISTRATION]}
    if len(registrations) != len(sql[schema.REGISTRATION]) or set(registrations) != set(entries["registrations"]):
        _fail("SQL_REGISTRATION_MISMATCH")
    for key, entry in entries["registrations"].items():
        row, record = registrations[key], entry["record"]
        if any(row[field] != record[field] for field in (*_SHARED, "request_id", "request_sha256", "registration_sha256", "committed_at")) or row["result_sha256"] != wire.digest(record):
            _fail("SQL_REGISTRATION_MISMATCH")
    for row in sql[schema.DISPOSITION]:
        if row["disposition"] != "applied":
            continue
        # A historical positive replay may predate registration. Its nullable
        # continuity pointer does not weaken the permanent original UUID/body
        # binding or allow it to conceal a later negative outcome.
        original_fields = tuple(field for field in _SHARED if field != "continuity_id")
        for kind in external.KINDS:
            entry = entries[kind].get(row["original_request_id"])
            if entry is not None and (kind == "tombstones" or any(
                    row[field] != entry["record"][field] for field in original_fields)):
                _fail("SQL_DISPOSITION_MISMATCH")
    tombstones = {row["original_request_id"]: row for row in sql[schema.DISPOSITION] if row["disposition"] == "not_applied"}
    if set(tombstones) != set(entries["tombstones"]):
        _fail("SQL_DISPOSITION_MISMATCH")
    for key, entry in entries["tombstones"].items():
        row, record = tombstones[key], entry["record"]
        if any(row[field] != record[field] for field in (*_SHARED, "request_id", "request_sha256", "registration_sha256", "disposition", "committed_at")) or row["result_sha256"] != wire.digest(record) or row["disposition_sha256"] != wire.disposition_sha256(record):
            _fail("SQL_DISPOSITION_MISMATCH")
    public.sort(key=lambda row: (row["record_kind"], row["record_id"]))
    references.extend(public)
    return {"format_version": 1, "kind": "cloud_continuity", "presence": "present", "absence_sha256": None,
        "owner_profile_sha256": active["owner_profile_sha256"], "identity": {"continuity_id": active["continuity_id"]},
        "contract_sha256": external._digest(active), "head_ordinal": len(names), "head_sha256": chain[-1],
        "journal_sha256": _digest(chain), "record_set_sha256": _digest(public),
        "reference_set_sha256": _digest(references), "record_count": len(public), "journal_count": len(names)}, references


@contextmanager
def capture_locked(profile, *, sql_projection, historical_prefix=None, limits=None, check_cancelled=None):
    """Yield a detached public summary/refs/unknown and final physical verifier.

    Unknown is bounded to Cloud continuity evidence, with no managed-path GC
    blocker or permission. The caller retains all other roots and determines
    any explicitly linked dependency scope. SQL freshness remains caller-owned.
    """
    limits = CaptureLimits() if limits is None else limits
    if not isinstance(limits, CaptureLimits) or check_cancelled is not None and not callable(check_cancelled):
        raise ValueError("Invalid continuity capture options")
    guard = lifecycle.require_guard(profile)
    with ExitStack() as stack:
        reader = _Reader(profile, stack, limits, check_cancelled)
        capture = ContinuityCapture(reader, guard)
        sql = None
        try:
            reader.check()
            try:
                sql = normalize_sql(sql_projection, limits=limits)
                capture._references = tuple({"record_kind": table, "record_id": row[fields[0]],
                    "projection_sha256": _digest(row)} for table, fields in SQL_COLUMNS.items() for row in sql[table])
                if historical_prefix is not None:
                    if (type(historical_prefix) is not dict or set(historical_prefix) != {"continuity_id", "ordinal", "sha256"}
                            or type(historical_prefix["ordinal"]) is not int or not 0 <= historical_prefix["ordinal"] <= limits.max_journal):
                        _fail("PREFIX_INVALID")
                    external._uuid(historical_prefix["continuity_id"])
                    external._sha(historical_prefix["sha256"])
                capture._summary, refs = _capture(reader, sql, deepcopy(historical_prefix))
                capture._references = tuple(refs)
            except (ContinuityRootError, source.SourceReadError, safe.SyncError, OSError, ValueError, TypeError, KeyError) as exc:
                if getattr(exc, "retriable", False):
                    raise
                if isinstance(exc, source.SourceReadError) and exc.code == "SOURCE_CHANGED":
                    _fail("SOURCE_CHANGED", retriable=True)
                code = exc.code if isinstance(exc, ContinuityRootError) else "CONTINUITY_ROOT_UNPROVEN"
                capture._references += tuple(reader.known_refs[key] for key in sorted(reader.known_refs))
                capture._summary = {"format_version": 1, "kind": "cloud_continuity", "presence": "unknown"}
                capture._unknown = ({"scope": "cloud_continuity", "code": code, "boundscope": {
                    "sql_projection_sha256": None if sql is None else _digest(sql),
                    "sql_rows": 0 if sql is None else sum(map(len, sql.values())),
                    "observed_files": len(reader.files),
                    "observed_file_set_sha256": _digest([[name, digest.hex()] for _directory, name, _stream, _before, _opened, digest in reader.files])}},)
            capture.verify_source()
            yield capture
            capture.verify_source()
        except _Cancelled as exc:
            raise exc.error from None
        finally:
            capture._active = False
