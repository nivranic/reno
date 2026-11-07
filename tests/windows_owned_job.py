"""Tests-only retained Popen ownership; no PID/port-based termination.

Windows admits each original suspended Popen to its private kill-on-close Job
before running any child code. A root exit never proves its Job is empty.
Unproven teardown retains the owner/resources for diagnostics and fails.
This module is a DATA review draft, not imported by production or live tests.
"""
from __future__ import annotations

import json
import os
import weakref
import subprocess
import threading
import time
import traceback


_RETAINED_UNPROVEN = []


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    CREATE_SUSPENDED = 0x00000004
    CREATE_NO_WINDOW = 0x08000000
    KILL_ON_JOB_CLOSE = 0x00002000
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 258
    WAIT_FAILED = 0xFFFFFFFF

    class _JobBasic(ctypes.Structure):
        _fields_ = [("per_process", ctypes.c_longlong), ("per_job", ctypes.c_longlong),
            ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t), ("max_working", ctypes.c_size_t),
            ("active_limit", wintypes.DWORD), ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
            ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

    class _JobExtended(ctypes.Structure):
        _fields_ = [("basic", _JobBasic), ("io", _IoCounters), ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

    class _JobAccounting(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in
            ("user_time", "kernel_time", "period_user", "period_kernel")] + [
            (name, wintypes.DWORD) for name in
            ("page_faults", "total_processes", "active_processes", "terminated_processes")]

    # Fresh DLL instances own fresh function objects. Never mutate windll's
    # cached functions or another product module's kernel/nt prototypes.
    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _nt = ctypes.WinDLL("ntdll", use_last_error=True)

    def _bind(dll, name, arguments, result):
        function = getattr(dll, name)
        function.argtypes, function.restype = arguments, result
        return function

    _create_job = _bind(_kernel, "CreateJobObjectW", [wintypes.LPVOID, wintypes.LPCWSTR], wintypes.HANDLE)
    _set_job = _bind(_kernel, "SetInformationJobObject",
        [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD], wintypes.BOOL)
    _assign_job = _bind(_kernel, "AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL)
    _terminate_job = _bind(_kernel, "TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL)
    _query_job = _bind(_kernel, "QueryInformationJobObject",
        [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL)
    _process_times = _bind(_kernel, "GetProcessTimes", [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4,
        wintypes.BOOL)
    _wait_native = _bind(_kernel, "WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD)
    _terminate_native = _bind(_kernel, "TerminateProcess", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL)
    _close_native = _bind(_kernel, "CloseHandle", [wintypes.HANDLE], wintypes.BOOL)
    _resume_native = _bind(_nt, "NtResumeProcess", [wintypes.HANDLE], ctypes.c_long)

    def _win_error(action, code=None):
        error = ctypes.WinError(ctypes.get_last_error() if code is None else code)
        error.add_note("Windows owned Job action: " + action)
        return error


class _NativeOwner:
    """Own one original native process and, on Windows, all its descendants."""
    def __init__(self):
        self._process = self._original = None
        self._original_job = self._native_handle = self._creation_filetime = None
        self._retained_native_handle = None
        self._factory_job_handle = None
        self._job_close_started = self._job_close_succeeded = False
        self._assigned = self._drained = self._closed = False
        self._receipt = None
        self.resources = []
        self._lock = threading.RLock()
        if os.name == "nt":
            try:
                self._original_job = _create_job(None, None)  # Default non-inheritable unnamed handle.
                self._factory_job_handle = self._original_job
                if not self._original_job:
                    raise _win_error("CreateJobObjectW")
                limits = _JobExtended()
                limits.basic.flags = KILL_ON_JOB_CLOSE
                if not _set_job(self._job_handle(), 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                    raise _win_error("SetInformationJobObject")
            except BaseException as primary:
                self._cleanup_launch_failure(primary)
                raise

    def _retain_unproven(self):
        if not any(owner is self for owner in _RETAINED_UNPROVEN):
            _RETAINED_UNPROVEN.append(self)

    def _verify_handle(self):
        if (self._process is None or self._process is not self._original
                or self._original._handle is not self._retained_native_handle
                or self._native_handle != int(self._retained_native_handle)):
            raise RuntimeError("Original retained Popen/native handle changed")

    def _creation_time(self):
        self._verify_handle()
        times = [wintypes.FILETIME() for _ in range(4)]
        if not _process_times(self._native_handle, *(ctypes.byref(value) for value in times)):
            raise _win_error("GetProcessTimes(original handle)")
        return (int(times[0].dwHighDateTime) << 32) | int(times[0].dwLowDateTime)

    def _verify_creation(self):
        if self._creation_filetime is None or self._creation_time() != self._creation_filetime:
            raise RuntimeError("Original native process creation FILETIME changed or unavailable")

    def _job_handle(self, *, closing=False):
        # Only the original factory handle may select a native Job operation.
        # This internal owner is never exposed through the public cache object.
        if (self._job_close_started and not closing or not self._original_job
                or self._original_job != self._factory_job_handle):
            raise RuntimeError("Original factory Job handle unavailable or changed")
        return self._factory_job_handle

    def _active(self):
        if not self._original_job:
            raise RuntimeError("Original Job handle unavailable before accounting proof")
        accounting = _JobAccounting()
        returned = wintypes.DWORD()
        if not _query_job(self._job_handle(), 1, ctypes.byref(accounting), ctypes.sizeof(accounting), ctypes.byref(returned)):
            raise _win_error("QueryInformationJobObject(original Job)")
        if returned.value != ctypes.sizeof(accounting):
            raise RuntimeError("Original Job accounting size changed")
        return int(accounting.active_processes)

    def _signaled(self):
        self._verify_handle()
        result = _wait_native(self._native_handle, 0)
        if result == WAIT_FAILED:
            raise _win_error("WaitForSingleObject(original handle)")
        if result not in (WAIT_OBJECT_0, WAIT_TIMEOUT):
            raise RuntimeError("Unexpected original process wait result: " + str(result))
        return result == WAIT_OBJECT_0

    def _cleanup_launch_failure(self, primary):
        try:
            self.stop(timeout=10)
        except BaseException as cleanup:
            self._retain_unproven()
            primary.add_note("Original owned launch cleanup failed:\n" + "".join(traceback.format_exception(cleanup)))

    def launch(self, argv, **kwargs):
        with self._lock:
            if self._process is not None or self._closed:
                raise RuntimeError("An owned process instance can launch only once")
            if "shell" in kwargs or "creationflags" in kwargs:
                raise ValueError("Owned helper fixes shell/Windows creation flags")
            self.resources.extend(value for key, value in kwargs.items()
                if key in {"stdin", "stdout", "stderr"} and hasattr(value, "close"))
            try:
                self._process = self._original = subprocess.Popen(argv, shell=False,
                    creationflags=CREATE_SUSPENDED | CREATE_NO_WINDOW if os.name == "nt" else 0, **kwargs)
                if os.name == "nt":
                    self._retained_native_handle = self._original._handle
                    self._native_handle = int(self._retained_native_handle)
                    self._creation_filetime = self._creation_time()
                    if not _assign_job(self._job_handle(), self._native_handle):
                        # The process has never resumed: only its exact original
                        # retained handle is used for cancellation. No PID scan,
                        # OpenProcess, taskkill or uncontained resumed fallback.
                        raise _win_error("AssignProcessToJobObject(original handle)")
                    self._assigned = True
                    self._verify_creation()
                    status = _resume_native(self._native_handle)
                    if status != 0:
                        raise OSError("NtResumeProcess failed with original NTSTATUS " + str(status))
                return self._original
            except BaseException as primary:
                self._cleanup_launch_failure(primary)
                raise

    def stop(self, *, timeout=10):
        """Return only after original root exit and complete Job accounting0."""
        with self._lock:
            if self._closed and self._receipt is not None and self._original_job is None:
                return self._receipt
            if self._job_close_started:
                raise RuntimeError('Original Job close outcome unknown; native handle cannot be reused')
            try:
                deadline = time.monotonic() + timeout
                if os.name == "nt":
                    if self._process is None:
                        if self._original_job is not None and self._active() != 0:
                            raise RuntimeError("Unlaunched owned Job unexpectedly contains processes")
                    else:
                        self._verify_handle()
                        if self._assigned:
                            # The original redirector may already have exited.
                            # Terminate the original owned Job before optional
                            # observations: a failed times/query API must not
                            # skip the safely owned termination attempt.
                            if not _terminate_job(self._job_handle(), 1):
                                raise _win_error("TerminateJobObject(original Job)")
                            self._verify_creation()
                        else:
                            # Creation-time capture/assignment failed before
                            # resume; the original suspended handle is retained.
                            # Do not let WAIT_FAILED skip exact-handle stop.
                            if not _terminate_native(self._native_handle, 1):
                                raise _win_error("TerminateProcess(original suspended handle)")
                        while True:
                            active, signaled = self._active(), self._signaled()
                            if active == 0 and signaled:
                                break
                            if time.monotonic() >= deadline:
                                raise TimeoutError(f"Owned Job drain unproven: active={active}, root_signaled={signaled}")
                            time.sleep(.01)
                        self._original.wait(timeout=max(.01, deadline - time.monotonic()))
                        if self._creation_filetime is not None:
                            self._verify_creation()
                    self._receipt = {"job_active_processes": 0,
                        "native_original_signaled": self._process is not None,
                        "original_creation_filetime": self._creation_filetime,
                        "original_pid_diagnostic_only": None if self._process is None else self._original.pid,
                        "assigned_before_resume": self._assigned}
                    self._drained = True
                    if self._original_job is not None:
                        self._job_close_started = True
                        if not _close_native(self._job_handle(closing=True)):
                            raise _win_error("CloseHandle(original drained Job)")
                        self._job_close_succeeded = True
                        self._original_job = None
                else:
                    if self._process is not None:
                        if self._process is not self._original:
                            raise RuntimeError("Original POSIX Popen changed")
                        if self._original.poll() is None:
                            self._original.terminate()
                        try:
                            self._original.wait(timeout=min(5, max(.01, deadline - time.monotonic())))
                        except subprocess.TimeoutExpired:
                            self._original.kill()
                            self._original.wait(timeout=max(.01, deadline - time.monotonic()))
                    self._receipt = {"original_wait_completed": self._process is not None,
                        "original_pid_diagnostic_only": None if self._process is None else self._original.pid}
                    self._drained = True
                self._closed = True
                _RETAINED_UNPROVEN[:] = [owner for owner in _RETAINED_UNPROVEN if owner is not self]
                return self._receipt
            except BaseException:
                # Keep original Popen/native Job and passed log/pipe resources
                # alive. A timeout/API failure never becomes an empty-tree proof.
                self._retain_unproven()
                raise



class _OwnerState:
    __slots__ = ("owner_ref", "native", "created_job", "terminal_bytes")

    def __init__(self, owner, native):
        identity = id(owner)
        def retire(reference):
            with _REGISTRY_LOCK:
                current = _OWNER_STATES.get(identity)
                if current is not None and current.owner_ref is reference:
                    if _private_terminal(current):
                        del _OWNER_STATES[identity]
                    else:
                        # An expired wrapper cannot erase a live/unknown original
                        # Job or make its id available for a new factory binding.
                        current.native._retain_unproven()
        self.owner_ref = weakref.ref(owner, retire)
        self.native = native
        self.created_job = native._factory_job_handle
        self.terminal_bytes = None


_OWNER_STATES = {}
_REGISTRY_LOCK = threading.RLock()


def _bound_state(owner):
    with _REGISTRY_LOCK:
        state = _OWNER_STATES.get(id(owner))
        if type(owner) is not OwnedProcess or state is None or state.owner_ref() is not owner:
            raise RuntimeError("Original owned process factory instance required")
        if state.native._factory_job_handle != state.created_job:
            raise RuntimeError("Original factory Job binding changed")
        return state


def _private_terminal(state):
    return (type(state.terminal_bytes) is bytes and state.native._closed
            and state.native._original_job is None
            and (os.name != "nt" or state.native._job_close_started and state.native._job_close_succeeded))


def _install_terminal(state, receipt):
    native = state.native
    if (not native._closed or native._original_job is not None or type(receipt) is not dict):
        raise RuntimeError("Original Job has no completed native closure proof")
    if os.name == "nt":
        if state.created_job is not None and not native._job_close_succeeded:
            raise RuntimeError("Original Job CloseHandle success proof required")
        if receipt.get("job_active_processes") != 0:
            raise RuntimeError("Original Job active0 proof required")
        if native._original is not None and receipt.get("native_original_signaled") is not True:
            raise RuntimeError("Original process-handle exit proof required")
        if receipt.get("original_creation_filetime") != native._creation_filetime:
            raise RuntimeError("Original process creation proof binding changed")
    elif native._original is not None and receipt.get("original_wait_completed") is not True:
        raise RuntimeError("Original POSIX process wait proof required")
    # Immutable private bytes, never the public mutable receipt/cache object.
    state.terminal_bytes = json.dumps({**receipt, "original_job_closed": True},
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


class OwnedProcess:
    """Public hints cannot select Job operations or stand in for native proof."""
    def __init__(self):
        # Hold the private registry lock across check/native-create/register:
        # ctypes may release the GIL, but a second __init__ cannot replace an
        # original state that another thread has accepted and already launched.
        with _REGISTRY_LOCK:
            if type(self) is not OwnedProcess or id(self) in _OWNER_STATES:
                raise RuntimeError("Original owned instance/id cannot be reinitialized or rebound")
            native = _NativeOwner()
            state = _OwnerState(self, native)
            _OWNER_STATES[id(self)] = state
            self._sync_hints(state)

    def _sync_hints(self, state):
        # Diagnostic/UI caches only. Native selection always uses _bound_state.
        self.process = state.native._original
        self.job = state.native._original_job
        self.native_handle = state.native._native_handle
        self.creation_filetime = state.native._creation_filetime
        self.assigned = state.native._assigned
        self.closed = self.drained = _private_terminal(state)
        self.receipt = None if state.terminal_bytes is None else json.loads(state.terminal_bytes)

    def launch(self, argv, **kwargs):
        state = _bound_state(self)
        with state.native._lock:
            try:
                return state.native.launch(argv, **kwargs)
            finally:
                self._sync_hints(state)

    def stop(self, *, timeout=10):
        state = _bound_state(self)
        with state.native._lock:
            # Neither public closed/drained nor a public copied receipt can
            # skip the original Job drain. Replay requires the private terminal
            # bytes AND successful private closure of the original Job.
            if _private_terminal(state):
                return json.loads(state.terminal_bytes)
            try:
                original_receipt = state.native.stop(timeout=timeout)
                _install_terminal(state, original_receipt)
                return json.loads(state.terminal_bytes)
            finally:
                self._sync_hints(state)

    def drain_proved(self):
        state = _bound_state(self)
        with state.native._lock:
            return _private_terminal(state)

    def original_process(self):
        return _bound_state(self).native._original

    def _retain_unproven(self):
        _bound_state(self).native._retain_unproven()


def owned_drain_proved(owner):
    # Resource consumers use this module-level factory lookup, not a mutable
    # public flag, returned dict or instance method cache.
    state = _bound_state(owner)
    with state.native._lock:
        return _private_terminal(state)


def owned_original_process(owner):
    return _bound_state(owner).native._original


def stop_owned(owner, *, timeout=10):
    return OwnedProcess.stop(owner, timeout=timeout)
