"""Owned subprocess observer for the real ingest lock; not a pytest entry."""
import errno
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import sys
import urllib.request

role, root, config_file, data_dir = sys.argv[1:]
root, config_file, data_dir = map(Path, (root, config_file, data_dir))
repository = Path(__file__).absolute().parents[1]
assert role in {"holder", "contender"} and root.is_absolute()
assert not root.is_relative_to(repository) and not repository.is_relative_to(root)
assert config_file.is_relative_to(root) and data_dir.is_relative_to(root)
assert json.loads(config_file.read_text())["zhipu_api_key"] == ""
for key in tuple(os.environ):
    if key.startswith("RENO_"):
        del os.environ[key]
os.environ.update(RENO_QA_ROOT=str(root), RENO_CONFIG_FILE=str(config_file),
                  RENO_DATA_DIR=str(data_dir), RENO_LLM_TESTS="0")
sys.path.insert(0, str(repository))
network_attempts = sql_attempts = 0

def refuse(*args, **kwargs):
    global network_attempts
    network_attempts += 1
    raise RuntimeError("Lock observer refuses all network access")

socket.socket.connect = socket.socket.connect_ex = socket.getaddrinfo = refuse
urllib.request.urlopen = urllib.request.OpenerDirector.open = refuse
http.client.HTTPConnection.request = http.client.HTTPSConnection.request = refuse

def audit(event, arguments):
    global sql_attempts
    if event == "sqlite3.connect":
        sql_attempts += 1
        raise RuntimeError("Lock observer does not open any database")
    if event in {"socket.connect", "socket.bind", "socket.getaddrinfo"}:
        refuse()

sys.addaudithook(audit)
from reno import ingest

source_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
observed_fd = None
contended_reported = False

def emit(event, fd=None):
    identity = None
    if fd is not None:
        info = os.fstat(fd)
        identity = [str(info.st_dev), str(info.st_ino)]
    print(json.dumps({"event": event, "pid": os.getpid(), "parent_pid": os.getppid(), "identity": identity,
        "source_sha256": source_sha256, "network_attempts": network_attempts,
        "sql_attempts": sql_attempts, "descriptor_closed": event == "closed"}), flush=True)

if os.name == "nt":
    class NativeAPI:
        LK_NBLCK = ingest.msvcrt.LK_NBLCK
        def locking(self, fd, operation, length):
            global observed_fd, contended_reported
            observed_fd = fd
            try:
                return ingest.msvcrt.locking(fd, operation, length)
            except OSError as error:
                if role == "contender" and not contended_reported and (error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
                                             or getattr(error, "winerror", None) == 33):
                    emit("contended", fd)
                    contended_reported = True
                raise
    original_wait = ingest._wait_windows_lock
    ingest._wait_windows_lock = lambda stream, api: original_wait(stream, NativeAPI())
else:
    native_flock = ingest.fcntl.flock
    def observed_flock(fd, operation):
        global observed_fd
        observed_fd = fd
        if role == "contender" and operation == ingest.fcntl.LOCK_EX:
            try:
                native_flock(fd, operation | ingest.fcntl.LOCK_NB)
            except BlockingIOError:
                emit("contended", fd)
        return native_flock(fd, operation)
    ingest.fcntl.flock = observed_flock

with ingest._vid_lock("owned-contention", originals=root / "lock-fixture"):
    emit("held", observed_fd)
    assert sys.stdin.readline().strip() == "release"
try:
    os.fstat(observed_fd)
except OSError:
    pass
else:
    raise AssertionError("The original ingest context did not close its actual descriptor")
emit("closed")
