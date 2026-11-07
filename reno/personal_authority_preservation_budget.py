"""Internal operation accounting; no filesystem, owner, or completion capability.

Callers own the fixed I/O and identity gates. They reserve immediately before
that I/O, account its actual return before any callback, then run their gates.
Unknown write amounts retain the whole reservation; failed runs never reset.
"""
from contextlib import contextmanager
from types import MappingProxyType
import time

from . import personal_authority_raw_preservation_contract as contract


class AuthorityRawPreservationBudgetError(ValueError):
    def __init__(self, suffix):
        self.code = "AUTHORITY_RAW_PRESERVATION_" + suffix
        super().__init__(self.code)


class OperationBudget:
    """One serial operation's private ledger, starting before its first effect."""

    def __init__(self, limits, check_cancelled=None, *, readonly=False):
        if (type(limits) is not dict or len(limits) > 32
                or any(type(key) is not str for key in limits)
                or type(limits.get("capsule_limits")) is not dict
                or type(readonly) is not bool
                or (check_cancelled is not None and not callable(check_cancelled))):
            raise AuthorityRawPreservationBudgetError("LIMIT_INVALID")
        copied = {**limits, "capsule_limits": dict(limits["capsule_limits"])}
        try:
            contract._limits(copied)
        except (contract.Error, contract.c2.AuthorityPreservationContractError):
            raise AuthorityRawPreservationBudgetError("LIMIT_INVALID") from None
        copied["capsule_limits"] = MappingProxyType(copied["capsule_limits"])
        self.limits = MappingProxyType(copied)
        self._callback = check_cancelled
        self._started = time.monotonic_ns()
        self._deadline_ns = copied["deadline_ms"] * 1_000_000
        self._write_cap = 0 if readonly else copied["max_write_bytes"]
        self._failure_remaining = 0 if readonly else copied["max_failure_bytes"]
        self._readonly = readonly
        self._first = None
        self._terminal = 0
        self._in_callback = False
        self._active = None
        self._failure_started = False
        self._failure_write_started = False
        self._receipt_started = False
        self._read_bytes = self._read_calls = self._seek_calls = 0
        self._confirmed = self._write_calls = self._uncertain_reads = self._uncertain_writes = 0

    @property
    def first(self):
        return self._first

    def remember(self, exc):
        if not isinstance(exc, BaseException):
            exc = AuthorityRawPreservationBudgetError("SOURCE_FAILED")
        if self._first is None:
            self._first = exc
        return self._first

    def _fail(self, suffix):
        raise self.remember(AuthorityRawPreservationBudgetError(suffix))

    def _elapsed(self):
        elapsed = time.monotonic_ns() - self._started
        if type(elapsed) is not int or elapsed < 0:
            self._fail("DEADLINE_LIMIT")
        return elapsed

    @property
    def remaining_seconds(self):
        return max(0, self._deadline_ns - self._elapsed()) / 1_000_000_000

    def check(self, *, callback=True):
        if self._in_callback:
            self._fail("REENTRANT")
        if self._first is not None:
            raise self._first
        if self._elapsed() > self._deadline_ns:
            self._fail("DEADLINE_LIMIT")
        if callback and not self._terminal and self._callback is not None:
            self._in_callback = True
            try:
                self._callback()
            except BaseException as exc:
                raise self.remember(exc)
            finally:
                self._in_callback = False
        if self._first is not None:
            raise self._first
        if self._elapsed() > self._deadline_ns:
            self._fail("DEADLINE_LIMIT")

    @contextmanager
    def terminal_window(self):
        self._terminal += 1
        try:
            yield
        finally:
            self._terminal -= 1

    def _idle(self):
        if self._active is not None:
            self._fail("REENTRANT")

    def _count(self, requested):
        if type(requested) is not int or not 0 < requested <= self.limits["chunk_bytes"]:
            self._fail("LIMIT_INVALID")

    def _ticket(self, kind, count, failure=False):
        ticket = object()
        self._active = (ticket, kind, count, failure)
        return ticket

    def _take(self, ticket, kind):
        active = self._active
        if active is None or active[0] is not ticket or active[1] != kind:
            self._fail("REENTRANT")
        self._active = None
        return active[2], active[3]

    def begin_read(self, requested):
        self._idle()
        self.check()
        self._count(requested)
        if requested > self.limits["max_read_bytes"] - self._read_bytes:
            self._fail("READ_LIMIT")
        self._read_calls += 1
        return self._ticket("read", requested)

    def end_read(self, ticket, raw):
        requested, _ = self._take(ticket, "read")
        if type(raw) is not bytes:
            self._uncertain_reads += 1
            self._fail("SOURCE_FAILED")
        # Account the actual returned bytes even if a faulty reader exceeded
        # its request. Such a reader cannot produce a successful observation.
        self._read_bytes += len(raw)
        if len(raw) > requested or self._read_bytes > self.limits["max_read_bytes"]:
            self._fail("READ_LIMIT")
        return len(raw)

    def fail_read(self, ticket, exc):
        self._take(ticket, "read")
        self._uncertain_reads += 1
        raise self.remember(exc)

    def note_seek(self):
        self._idle()
        self.check()
        self._seek_calls += 1

    def begin_write(self, requested, *, failure=False):
        self._idle()
        if type(failure) is not bool:
            self._fail("LIMIT_INVALID")
        if failure:
            if (not self._failure_started or self._failure_write_started
                    or self._receipt_started or self._readonly):
                self._fail("PUBLICATION_FAILED")
            # This path never invokes callbacks or clears the original first.
            # Callers additionally verify the original guard and directories.
            if self._elapsed() > self._deadline_ns:
                self._fail("DEADLINE_LIMIT")
        else:
            self.check()
        self._count(requested)
        if failure:
            if requested > self._failure_remaining:
                self._fail("WRITE_LIMIT")
            self._failure_remaining -= requested
            self._failure_write_started = True
        else:
            available = self._write_cap - self._confirmed - self._uncertain_writes - self._failure_remaining
            if requested > available or self._readonly:
                self._fail("WRITE_LIMIT")
        self._write_calls += 1
        return self._ticket("write", requested, failure)

    def end_write(self, ticket, count):
        requested, failure = self._take(ticket, "write")
        if type(count) is not int or not 0 <= count <= requested:
            self._uncertain_writes += requested
            self._fail("WRITE_UNCERTAIN")
        self._confirmed += count
        if failure:
            self._failure_remaining += requested - count
        if count == 0:
            self._fail("PUBLICATION_FAILED")
        return count

    def fail_write(self, ticket, exc):
        requested, _ = self._take(ticket, "write")
        self._uncertain_writes += requested
        raise self.remember(exc)

    def begin_failure(self):
        if (self._first is None or self._failure_started or self._receipt_started
                or self._readonly or self._in_callback):
            self._fail("PUBLICATION_FAILED")
        self._idle()
        if self._elapsed() > self._deadline_ns:
            self._fail("DEADLINE_LIMIT")
        self._failure_started = True

    def mark_receipt_started(self):
        self._idle()
        self.check(callback=False)
        self._receipt_started = True

    def snapshot(self):
        """Historical counters; no callback or assertion of successful closure."""
        return {"read_bytes": self._read_bytes, "read_calls": self._read_calls,
                "seek_calls": self._seek_calls, "confirmed_write_bytes": self._confirmed,
                "write_calls": self._write_calls, "uncertain_read_calls": self._uncertain_reads,
                "uncertain_write_bytes": self._uncertain_writes, "elapsed_ns": self._elapsed()}

    def assert_settled(self):
        """Reject pending accounting; callers still must prove actual closes."""
        self._idle()
        self.check(callback=False)
