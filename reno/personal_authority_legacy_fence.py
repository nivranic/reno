"""Reject legacy personal writes after a current authority-name observation.

This read-only fence borrows the caller's actual SH/EX guard. It does not enroll
a namespace, inspect marker contents/ACLs, or issue a writer grant. Returning
None only means this bounded observation did not reject the legacy path; it is
not evidence that the store has never been enrolled or is currently writable.
Cooperating enrollment uses EX and cannot overlap this caller's SH lifetime.
Actors that ignore that protocol are outside this observation's guarantee.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
import time

from . import storage_lifecycle as lifecycle, sync_media as safe

PREFIX = ".reno-personal-authority"
MAX_NAMES = 4096
MAX_NAME_BYTES = 1024 * 1024
MAX_SECONDS = 30.0
REQUIRED = "PERSONAL_AUTHORITY_REQUIRED"
STATE_UNAVAILABLE = "PERSONAL_AUTHORITY_STATE_UNAVAILABLE"


class LegacyAuthorityFenceError(ValueError):
    """A finite failure without source names, paths, or marker contents."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code=STATE_UNAVAILABLE):
    raise LegacyAuthorityFenceError(code) from None


def _note(primary):
    try:
        BaseException.add_note(primary, "PERSONAL_AUTHORITY_FENCE_CLEANUP_FAILED")
    except BaseException:
        pass


@contextmanager
def _preserve_primary(context):
    """Do not suppress a body exception or replace it with an exit failure."""
    resource = context.__enter__()
    try:
        yield resource
    except BaseException as primary:
        original_tb = BaseException.__dict__["__traceback__"].__get__(primary)
        try:
            context.__exit__(type(primary), primary, original_tb)
        except BaseException as cleanup:
            if cleanup is not primary:
                _note(primary)
        raise
    else:
        # A failed ordinary iterator/directory close is never a clean census.
        context.__exit__(None, None, None)


class _Budget:
    def __init__(self, callback):
        # Include argument checking, guard admission, scans and normal close.
        self.deadline = time.monotonic() + MAX_SECONDS
        self.callback = callback
        self.callback_primary = None
        self.names = self.name_bytes = 0
        if callback is not None and not callable(callback):
            _fail()

    def clock_only(self):
        if self.callback_primary is not None:
            raise self.callback_primary
        if time.monotonic() >= self.deadline:
            _fail()

    def poll(self):
        self.clock_only()
        # A callable may be false-valued; do not select it by truthiness.
        if self.callback is not None:
            try:
                self.callback()
            except BaseException as primary:
                self.callback_primary = primary
                raise
        self.clock_only()

    def before_next(self):
        # Refuse before asking for an entry beyond the total allowance. At an
        # exhausted allowance, even an unconfirmed EOF is an unknown census.
        if self.names >= MAX_NAMES:
            _fail()

    def name(self, name):
        self.names += 1
        if type(name) is not str or not name:
            _fail()
        remaining = MAX_NAME_BYTES - self.name_bytes
        # Bound the temporary UTF-8 allocation as well as the charged bytes.
        if len(name) > remaining:
            _fail()
        size = len(name.encode("utf-8"))
        if size > remaining:
            _fail()
        self.name_bytes += size
        if name.rstrip(" .").casefold().startswith(PREFIX):
            _fail(REQUIRED)


def _census(directory, budget, *, terminal):
    check = budget.clock_only if terminal else budget.poll
    check()
    directory.verify()
    check()
    names = set()
    # POSIX enumerates the held FD; Windows keeps the original no-delete-share
    # parent handle and verifies its path identity around each fresh scan.
    target = safe._native_path(directory.path) if os.name == "nt" else directory.fd
    with _preserve_primary(os.scandir(target)) as entries:
        while True:
            check()
            budget.before_next()
            try:
                entry = next(entries)
            except StopIteration:
                break
            name = entry.name
            budget.name(name)
            if name in names:
                _fail()
            names.add(name)
        check()
    check()
    directory.verify()
    check()
    return frozenset(names)


def assert_legacy_write_allowed_locked(profile, check_cancelled=None) -> None:
    """Observe names under an already-held exact profile, without any writes.

    The initial pass polls cancellation. After its last caller poll, two fresh
    terminal passes, original guard/profile verification, normal directory
    close and a final deadline check run without another caller callback. All
    three passes charge the same total entry/name-byte and time allowances.
    """
    budget = None
    try:
        budget = _Budget(check_cancelled)
        budget.poll()
        profile = lifecycle._profile(profile)
        expected_profile = tuple(profile.identity)
        guard = lifecycle.require_guard(profile)
        parent = profile.database.parent
        budget.poll()
        with _preserve_primary(safe._Directory(parent)) as directory:
            initial = _census(directory, budget, terminal=False)
            budget.poll()
            first = _census(directory, budget, terminal=True)
            second = _census(directory, budget, terminal=True)
            if initial != first or first != second:
                _fail()
            budget.clock_only()
            if (tuple(profile.identity) != expected_profile
                    or lifecycle.require_guard(profile) is not guard
                    or directory.path != parent):
                _fail()
            directory.verify()
            budget.clock_only()
        budget.clock_only()
    except BaseException as error:
        if budget is not None and budget.callback_primary is not None:
            # In particular, caller OSError/ValueError must not be mistaken for
            # a source fault, including when cleanup also fails.
            if error is not budget.callback_primary:
                _note(budget.callback_primary)
                raise budget.callback_primary from None
            raise
        if isinstance(error, LegacyAuthorityFenceError):
            raise
        if isinstance(error, Exception):
            _fail()
        # KeyboardInterrupt/SystemExit and other control exceptions keep their
        # original objects; ordinary unknown/IO observations fail closed above.
        raise
