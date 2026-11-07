"""Private RP1 accounting, never a resource owner or closure capability.

Fixed factories reserve before their native calls, record the actual return
before any callback or validation, and release leases after actual closes.
Source reads are a subset of all reads. Unknown effects keep their reservation.
"""
from contextlib import contextmanager
from types import MappingProxyType
import time

from . import personal_authority_replica_policy as policy


class AuthorityReplicaBudgetError(ValueError):
    def __init__(self, suffix):
        self.code = "AUTHORITY_REPLICA_BUDGET1_" + suffix
        super().__init__(self.code)


Error = AuthorityReplicaBudgetError


class ReplicaBudget:
    """One serial RP1 execution, starting before the first native guard check."""

    def __init__(self, mode, check_cancelled=None):
        if type(mode) is not str or mode not in ("producer", "readonly"):
            raise Error("MODE_INVALID")
        if check_cancelled is not None and not callable(check_cancelled):
            raise Error("TYPE_INVALID")
        self.limits = MappingProxyType(policy.runtime_limits(mode))
        self._readonly = mode == "readonly"
        self._callback = check_cancelled
        self._first = None
        self._in_callback = False
        self._terminal = 0
        self._active = None
        self._resource_pending = {}
        self._resource_leases = {}
        self._pending_resources = self._current_resources = self._peak_resources = 0
        self._uncertain_resources = 0
        self._read_bytes = self._source_read_bytes = 0
        self._read_calls = self._source_read_calls = self._seek_calls = 0
        self._uncertain_reads = self._uncertain_source_reads = 0
        self._confirmed = self._write_calls = self._uncertain_writes = 0
        self._failure_remaining = self.limits["max_failure_bytes"]
        self._failure_started = self._failure_write_started = self._receipt_started = False
        self._started = time.monotonic_ns()
        if type(self._started) is not int or self._started < 0:
            raise Error("DEADLINE_LIMIT")
        self._last_tick = self._started
        self._deadline_ns = self.limits["deadline_ms"] * 1_000_000

    @property
    def first(self):
        return self._first

    def remember(self, exc):
        if not issubclass(type(exc), BaseException):
            exc = Error("SOURCE_FAILED")
        if self._first is None:
            self._first = exc
        return self._first

    def _fail(self, suffix):
        raise self.remember(Error(suffix))

    def _elapsed(self):
        tick = time.monotonic_ns()
        if type(tick) is not int or tick < self._last_tick:
            self._fail("DEADLINE_LIMIT")
        self._last_tick = tick
        return tick - self._started

    @property
    def remaining_seconds(self):
        return max(0, self._deadline_ns - self._elapsed()) / 1_000_000_000

    def _deadline(self):
        if self._elapsed() >= self._deadline_ns:
            self._fail("DEADLINE_LIMIT")

    def _outside_callback(self):
        # Settlement must survive first/deadline, but a callback cannot consume
        # the factory's outstanding ticket or release its real resource lease.
        if self._in_callback:
            self._fail("REENTRANT")

    def check(self, *, callback=True):
        if type(callback) is not bool:
            self._fail("TYPE_INVALID")
        if self._in_callback:
            self._fail("REENTRANT")
        if self._first is not None:
            raise self._first
        self._deadline()
        if callback and not self._terminal and self._callback is not None:
            # Capture the original storage and primitives before caller code.
            # A rejected callback mutation must not dispatch remember/setattr
            # again while the original check unwinds its callback window.
            dictionary = object.__getattribute__(self, "__dict__")
            kind_of, dictionary_type, text_type = type, dict, str
            every, subclass_of, exception_type = all, issubclass, BaseException
            get_value, set_value = dict.get, dict.__setitem__
            invalid_state = Error("SOURCE_FAILED")
            if kind_of(dictionary) is not dictionary_type or not every(kind_of(key) is text_type for key in dictionary):
                raise invalid_state
            self._in_callback = True
            pending_error = None
            try:
                self._callback()
            except exception_type as exc:
                pending_error = exc
                if kind_of(dictionary) is dictionary_type and every(kind_of(key) is text_type for key in dictionary):
                    first = get_value(dictionary, "_first")
                    if first is None or not subclass_of(kind_of(first), exception_type):
                        first = exc
                        set_value(dictionary, "_first", first)
                    raise first
                # Invalid keys can execute equality during string lookup.
                # Keep the actual exception; do not claim this dict is idle.
                raise
            finally:
                if kind_of(dictionary) is dictionary_type and every(kind_of(key) is text_type for key in dictionary):
                    set_value(dictionary, "_in_callback", False)
                elif pending_error is None:
                    raise invalid_state
        if self._first is not None:
            raise self._first
        self._deadline()

    @contextmanager
    def terminal_window(self):
        self._outside_callback()
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
            self._fail("TYPE_INVALID")

    def _ticket(self, kind, count, flag):
        ticket = object()
        self._active = (ticket, kind, count, flag)
        return ticket

    def _take(self, ticket, kind):
        self._outside_callback()
        active = self._active
        if type(ticket) is not object or active is None or active[0] is not ticket or active[1] != kind:
            self._fail("TICKET_INVALID")
        self._active = None
        return active[2], active[3]

    def begin_read(self, requested, *, source=False):
        self._count(requested)
        if type(source) is not bool:
            self._fail("TYPE_INVALID")
        self._idle()
        self.check()
        if requested > self.limits["max_read_bytes"] - self._read_bytes:
            self._fail("READ_LIMIT")
        if source and requested > self.limits["source_max_read_bytes"] - self._source_read_bytes:
            self._fail("SOURCE_READ_LIMIT")
        self._read_calls += 1
        self._source_read_calls += int(source)
        return self._ticket("read", requested, source)

    def end_read(self, ticket, raw):
        requested, source = self._take(ticket, "read")
        if type(raw) is not bytes:
            self._uncertain_reads += 1
            self._uncertain_source_reads += int(source)
            self._fail("SOURCE_FAILED")
        size = len(raw)
        self._read_bytes += size
        if source:
            self._source_read_bytes += size
        if size > requested or self._read_bytes > self.limits["max_read_bytes"]:
            self._fail("READ_LIMIT")
        if self._source_read_bytes > self.limits["source_max_read_bytes"]:
            self._fail("SOURCE_READ_LIMIT")
        return size

    def fail_read(self, ticket, exc):
        _requested, source = self._take(ticket, "read")
        self._uncertain_reads += 1
        self._uncertain_source_reads += int(source)
        raise self.remember(exc)

    def note_seek(self):
        self._idle()
        self.check()
        self._seek_calls += 1

    def _diagnostic_gate(self):
        if (self._readonly or not self._failure_started or self._first is None
                or self._receipt_started or self._in_callback):
            self._fail("DIAGNOSTIC_UNAVAILABLE")
        self._deadline()

    def begin_write(self, requested, *, failure=False):
        self._count(requested)
        if type(failure) is not bool:
            self._fail("TYPE_INVALID")
        self._idle()
        if failure:
            self._diagnostic_gate()
            if self._failure_write_started:
                self._fail("DIAGNOSTIC_UNAVAILABLE")
            if requested > self._failure_remaining:
                self._fail("WRITE_LIMIT")
        else:
            self.check()
            if self._readonly:
                self._fail("WRITE_LIMIT")
        available = self.limits["max_write_bytes"] - self._confirmed - self._uncertain_writes
        if not failure:
            available -= self._failure_remaining
        if requested > available:
            self._fail("WRITE_LIMIT")
        if failure:
            self._failure_remaining -= requested
            self._failure_write_started = True
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
            self._fail("WRITE_FAILED")
        return count

    def fail_write(self, ticket, exc):
        requested, _failure = self._take(ticket, "write")
        self._uncertain_writes += requested
        raise self.remember(exc)

    def begin_failure(self):
        if (self._readonly or self._first is None or self._failure_started
                or self._receipt_started or self._in_callback):
            self._fail("DIAGNOSTIC_UNAVAILABLE")
        self._idle()
        self._deadline()
        self._failure_started = True

    def mark_receipt_started(self):
        self._idle()
        self.check(callback=False)
        self._receipt_started = True

    def begin_resources(self, maximum, *, failure=False):
        if type(maximum) is not int or not 0 < maximum <= self.limits["max_resources"]:
            self._fail("TYPE_INVALID")
        if type(failure) is not bool:
            self._fail("TYPE_INVALID")
        if failure:
            self._diagnostic_gate()
        else:
            self.check()
        if maximum > self.limits["max_resources"] - self._current_resources - self._pending_resources:
            self._fail("RESOURCE_LIMIT")
        ticket = object()
        self._resource_pending[ticket] = maximum
        self._pending_resources += maximum
        return ticket

    def _take_resources(self, ticket):
        self._outside_callback()
        if type(ticket) is not object or ticket not in self._resource_pending:
            self._fail("TICKET_INVALID")
        maximum = self._resource_pending.pop(ticket)
        self._pending_resources -= maximum
        return maximum

    def _settle_resources(self, maximum, actual):
        error = None
        if type(actual) is not int or actual < 0:
            count = maximum
            self._uncertain_resources += 1
            error = Error("RESOURCE_UNCERTAIN")
        else:
            count = actual
            if count > maximum:
                error = Error("RESOURCE_LIMIT")
        self._current_resources += count
        self._peak_resources = max(self._peak_resources, self._current_resources)
        if self._current_resources + self._pending_resources > self.limits["max_resources"]:
            error = Error("RESOURCE_LIMIT")
        lease = object()
        self._resource_leases[lease] = count
        return lease, None if error is None else self.remember(error)

    def end_resources(self, ticket, actual):
        maximum = self._take_resources(ticket)
        return self._settle_resources(maximum, actual)

    def fail_resources(self, ticket, exc, *, acquired):
        maximum = self._take_resources(ticket)
        self.remember(exc)
        lease, _error = self._settle_resources(maximum, acquired)
        return lease, self._first

    def release_resources(self, lease):
        # A fixed factory calls only after all actual resources in this lease
        # have closed. First/deadline must never prevent this accounting step.
        self._outside_callback()
        if type(lease) is not object or lease not in self._resource_leases:
            self._fail("TICKET_INVALID")
        self._current_resources -= self._resource_leases.pop(lease)

    def require_remaining(self, demand):
        try:
            normalized = policy.normalize_demand(demand)
        except policy.AuthorityReplicaPolicyError:
            self._fail("DEMAND_INVALID")
        self.check()
        pending_read = pending_source = pending_write = 0
        if self._active is not None:
            _ticket, kind, requested, flag = self._active
            if kind == "read":
                pending_read = requested
                pending_source = requested if flag else 0
            else:
                pending_write = requested
        if self._read_bytes + pending_read + normalized["read_bytes"] > self.limits["max_read_bytes"]:
            self._fail("READ_LIMIT")
        if self._source_read_bytes + pending_source + normalized["source_read_bytes"] > self.limits["source_max_read_bytes"]:
            self._fail("SOURCE_READ_LIMIT")
        # Demand.write already includes the one full failure reservation.
        if self._confirmed + self._uncertain_writes + pending_write + normalized["write_bytes"] > self.limits["max_write_bytes"]:
            self._fail("WRITE_LIMIT")
        if self._current_resources + self._pending_resources + normalized["peak_additional_resources"] > self.limits["max_resources"]:
            self._fail("RESOURCE_LIMIT")

    def snapshot(self):
        """Historical family counters; no callback or resource closure claim."""
        return {"read_bytes": self._read_bytes, "source_read_bytes": self._source_read_bytes,
            "read_calls": self._read_calls, "source_read_calls": self._source_read_calls,
            "seek_calls": self._seek_calls, "confirmed_write_bytes": self._confirmed,
            "write_calls": self._write_calls, "uncertain_read_calls": self._uncertain_reads,
            "uncertain_source_read_calls": self._uncertain_source_reads,
            "uncertain_write_bytes": self._uncertain_writes, "elapsed_ns": self._elapsed(),
            "current_resources": self._current_resources, "peak_resources": self._peak_resources,
            "pending_resources": self._pending_resources,
            "uncertain_resource_acquires": self._uncertain_resources}

    def assert_settled(self):
        self._idle()
        if self._resource_pending or self._current_resources:
            self._fail("UNSETTLED")
        self.check(callback=False)
