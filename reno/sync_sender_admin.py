"""Explicit sender administration over committed, private source images.

Previews never initialize storage, acquire writer locks, read configuration or
construct a network client. Local commits and remote delivery are separate;
delivery needs an explicit credential file and always retains the original peer.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
from pathlib import Path
import sqlite3
import threading
import time

from . import db, storage_home_roots as home, storage_lifecycle as lifecycle
from . import storage_members as members, storage_readonly as readonly
from . import sync_media as safe, sync_no_resend as ledger, sync_protocol as protocol
from . import sync_snapshot, sync_state, tasks
from .sync_protocol import SyncError


def _fail(code):
    raise SyncError("Sender administration could not be verified", code, 409) from None


def _digest(value):
    return hashlib.sha256(protocol._json_bytes(value)).hexdigest()


def _existing_profile(profile):
    # Read-only admission finishes before writer ownership. It cannot create
    # missing DATA/roots through the general worker lock's startup behavior.
    with lifecycle.shared(profile):
        with safe._Directory(profile.database.parent, base=profile.database.parent):
            pass


def _node(profile):
    with safe._Directory(profile.database.parent, base=profile.database.parent) as directory:
        raw, signature = home._read(directory, "node-identity.json", 4096, home._Budget(home.HomeLimits()))
    value = sync_state._decode(raw)
    sync_state._exact(value, {"instance_id"})
    sync_state._uuid(value["instance_id"])
    return value["instance_id"], signature


def _source(connection):
    """Bound the dedicated image's fixed SQL reads and reject view substitutes."""
    started, steps = time.monotonic(), 0
    def progress():
        nonlocal steps
        steps += 1000
        return int(steps > 100_000 or time.monotonic() - started > 10)
    def authorize(action, first, second, schema, source):
        if source is not None:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and schema == "main" and first in {"meta", "sqlite_master", "sqlite_schema"}:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and first in {"table_info", "index_list", "index_info", "foreign_key_list"}:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    connection.set_authorizer(authorize)
    connection.set_progress_handler(progress, 1000)
    previous_length = connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 256 * 1024)
    try:
        required = {"meta", *protocol.TABLES}
        schema = connection.execute("SELECT name,type FROM sqlite_schema WHERE name IN (" +
                                    ",".join("?" for _ in required) + ")", tuple(sorted(required))).fetchall()
        if len(schema) != len(required) or any(row[1] != "table" for row in schema):
            _fail("SYNC_SENDER_SCHEMA_INVALID")
        version = db.knowledge_version(connection)
        sync_snapshot._version(version)
        return version, protocol.schema_handshake(connection)
    finally:
        connection.set_progress_handler(None, 0)
        connection.set_authorizer(readonly._readonly_authorizer)
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, previous_length)


@contextmanager
def _proof(profile, store):
    lifecycle._profile(profile)
    with lifecycle.shared(profile):
        member = members.require_enrolled(profile)
        node_id, node_signature = _node(profile)
        with readonly.committed_image_locked(profile) as image:
            version, handshake = _source(image.connection)
            state = store.read_observation()
            if (version["library_id"] != member["library_id"]
                    or state["binding"]["home_library_id"] != version["library_id"]
                    or state["binding"]["home_node_id"] != node_id
                    or state["local_epoch"] > version["library_epoch"]):
                _fail("SYNC_SENDER_OWNER_MISMATCH")
            proof = {"format_version": 1, "profile_sha256": ledger.profile_sha256(profile),
                     "member_sha256": _digest(member), "version": version,
                     "node_id": node_id, "handshake": handshake}
            source = {"library_id": version["library_id"], "node_id": node_id, "handshake": handshake}
            def verify():
                if _node(profile) != (node_id, node_signature):
                    _fail("SYNC_SENDER_SOURCE_CHANGED")
                image.verify_source()
            verify()
            yield proof, source, verify
            verify()


def _preview(store, publication_id, proof):
    preview = store.preview_abandon(publication_id)
    return {**preview, "digest": _digest({"format_version": 1, "source": proof, "selection": preview})}, preview


def preview_abandon(profile, publication_id):
    store = sync_state.StateStore(profile.database.parent, profile=profile)
    with _proof(profile, store) as (proof, _source_identity, _verify):
        return _preview(store, publication_id, proof)[0]


def abandon(profile, publication_id, *, expected_digest, operation_id):
    sync_state._uuid(operation_id)
    ledger._sha(expected_digest)
    _existing_profile(profile)
    with tasks.process_lock(profile=profile) as acquired:
        if not acquired:
            _fail("SYNC_BUSY")
        store = sync_state.StateStore(profile.database.parent, profile=profile)
        with store.exclusive(), _proof(profile, store) as (proof, _source_identity, verify):
            public, internal = _preview(store, publication_id, proof)
            if public["digest"] != expected_digest:
                _fail("SYNC_NO_RESEND_STALE_PREVIEW")
            verify()  # Before any capability, marker or state mutation.
            request = store.commit_no_resend(publication_id, expected_digest=internal["digest"], operation_id=operation_id)
            receipt = store.no_resend_receipt(operation_id)
            return {"operation_id": operation_id, "publication_id": request["metadata"]["publication_id"],
                    "state": "remote_" + receipt["state"] if receipt else "no_resend_remote_pending"}


def read_local_selection(path):
    from . import sync_local_retirement as retirement
    with safe._Directory(path.parent, base=path.parent) as directory:
        raw, _signature = home._read(directory, path.name, ledger.MAX_RECORD_BYTES, home._Budget(home.HomeLimits()))
    return retirement.validate_selection(sync_state._decode(raw))


def _retirement_preview(store, selection, proof):
    from . import sync_local_retirement as retirement
    internal = retirement.preview(store, selection)
    public = {**internal, "digest": _digest({"format_version": 1, "source": proof, "selection": internal})}
    return public, internal


def preview_retire_local(profile, selection):
    store = sync_state.StateStore(profile.database.parent, profile=profile)
    with _proof(profile, store) as (proof, _source_identity, _verify):
        return _retirement_preview(store, selection, proof)[0]


def retire_local(profile, selection, *, expected_digest, operation_id):
    from . import sync_local_retirement as retirement
    ledger._sha(expected_digest)
    sync_state._uuid(operation_id)
    _existing_profile(profile)
    with tasks.process_lock(profile=profile) as acquired:
        if not acquired:
            _fail("SYNC_BUSY")
        store = sync_state.StateStore(profile.database.parent, profile=profile)
        with store.exclusive(), _proof(profile, store) as (proof, _source_identity, verify):
            public, internal = _retirement_preview(store, selection, proof)
            if public["digest"] != expected_digest:
                _fail("SYNC_NO_RESEND_STALE_PREVIEW")
            verify()
            request = retirement.apply(store, selection, expected_digest=internal["digest"], operation_id=operation_id)
            return {"operation_id": operation_id, "state": "local_references_retired",
                    "selected_count": len(request["selected"])}


def resume_retire_local(profile, selection, *, operation_id):
    from . import sync_local_retirement as retirement
    selection = retirement.validate_selection(selection)
    sync_state._uuid(operation_id)
    _existing_profile(profile)
    with tasks.process_lock(profile=profile) as acquired:
        if not acquired:
            _fail("SYNC_BUSY")
        store = sync_state.StateStore(profile.database.parent, profile=profile)
        with store.exclusive(), _proof(profile, store) as (_proof_value, _source_identity, verify):
            observed = ledger.observe(profile, state=store.read_observation(), allow_incomplete=True)
            entry = None if observed["catalog"] is None else observed["catalog"]["references"].get(operation_id)
            if entry is None or entry["record"]["selected"] != selection["selected"]:
                _fail("SYNC_NO_RESEND_OPERATION_CONFLICT")
            verify()
            request = retirement.resume(store, operation_id)
            return {"operation_id": operation_id, "state": "local_references_retired",
                    "selected_count": len(request["selected"])}


def _sender_file(path, binding):
    """Fresh peer and token from one bounded, no-follow file observation."""
    from .sync_client import _base_url
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        _fail("SYNC_SENDER_CONFIG_REQUIRED")
    with safe._Directory(path.parent, base=path.parent) as directory:
        raw, _signature = home._read(directory, path.name, 1024 * 1024, home._Budget(home.HomeLimits()))
    value = sync_state._decode(raw)
    if type(value) is not dict:
        _fail("SYNC_SENDER_CONFIG_INVALID")
    if value.get("node_role", "all") not in {"home", "all"}:
        _fail("SYNC_HOME_REQUIRED")
    url, _, _ = _base_url(value.get("sync_cloud_url"))
    if (url != binding["target_url"] or value.get("sync_target_library_id") != binding["target_library_id"]
            or value.get("sync_target_node_id") != binding["target_node_id"]):
        _fail("SYNC_PEER_MISMATCH")
    interval = value.get("sync_interval_seconds", 60)
    if type(interval) is not int or not 15 <= interval <= 3600:
        _fail("SYNC_INTERVAL_INVALID")
    return {"url": url, "interval": interval,
            "sync_target_library_id": binding["target_library_id"],
            "sync_target_node_id": binding["target_node_id"]}, value.get("sync_token")


def _complete_local_published(store, request, source, binding, check, *, current_version):
    from . import sync_agent
    class LocalCompletion(sync_agent._Cycle):
        def __init__(self):
            self.store, self.source = store, source
            self.options = {"url": binding["target_url"], "sync_target_library_id": binding["target_library_id"],
                            "sync_target_node_id": binding["target_node_id"]}
            attempt = store.read_observation()["recovery_attempt"]
            self.recovering = attempt is not None and attempt["publication_id"] == request["metadata"]["publication_id"]
        def check(self):
            check()
            if store.read_observation()["binding"] != binding:
                _fail("SYNC_PEER_MISMATCH")
    cycle = LocalCompletion()
    cycle._assert_delivery_peer(request)
    return cycle._complete_published(request, current_version=current_version)


def resume_abandon(profile, operation_id, *, sender_config=None, stop_event=None, client=None):
    """One original authorization only; completed receipts need no credentials."""
    from . import sync_agent
    from .sync_client import SyncClient
    sync_state._uuid(operation_id)
    requested_stop = stop_event or threading.Event()
    _existing_profile(profile)
    with tasks.process_lock(profile=profile) as acquired:
        if not acquired:
            _fail("SYNC_BUSY")
        store = sync_state.StateStore(profile.database.parent, profile=profile)
        with store.exclusive():
            with _proof(profile, store) as (source_proof, source, verify):
                verify()
                store.resume_no_resend_commit(operation_id)
                request = store.no_resend_request(operation_id)
                response = store.no_resend_receipt(operation_id)
                binding = store.read_observation()["binding"]
                if response is not None:
                    if request["reason"] == "published_receipt":
                        def local_check():
                            if requested_stop.is_set():
                                _fail("SYNC_CANCELLED")
                            verify()
                        _complete_local_published(store, request, source, binding, local_check,
                                                  current_version=source_proof["version"])
                    return {"operation_id": operation_id, "state": response["state"]}
                original_node, original_signature = _node(profile)
            if sender_config is None:
                _fail("SYNC_SENDER_CONFIG_REQUIRED")
            options, _token = _sender_file(sender_config, binding)
            def check():
                if requested_stop.is_set():
                    _fail("SYNC_CANCELLED")
                if store.read_observation()["binding"] != binding:
                    _fail("SYNC_PEER_MISMATCH")
                if _node(profile) != (original_node, original_signature):
                    _fail("SYNC_SENDER_SOURCE_CHANGED")
                current, _current_token = _sender_file(sender_config, binding)
                if any(current[key] != options[key] for key in ("url", "sync_target_library_id", "sync_target_node_id")):
                    _fail("SYNC_PEER_MISMATCH")
            def token_provider():
                check()
                current, token = _sender_file(sender_config, binding)
                if any(current[key] != options[key] for key in ("url", "sync_target_library_id", "sync_target_node_id")):
                    _fail("SYNC_PEER_MISMATCH")
                return token
            class Delivery(sync_agent._Cycle):
                def __init__(self):
                    self.store, self.client, self.source = store, remote, source
                    self.options, self.stop_event = options, requested_stop
                    self.database, self.home_data = profile.database, profile.database.parent
                    self.storage_profile = profile
                    self.lost, self.pulse_error = threading.Event(), None
                    attempt = store.read_observation()["recovery_attempt"]
                    self.recovering = attempt is not None and attempt["publication_id"] == request["metadata"]["publication_id"]
                def check(self):
                    check()
                    if self.lost.is_set():
                        _fail(self.pulse_error or "SYNC_HEARTBEAT_LOST")
                def _complete_published(self, request, *, current_version=None):
                    # Network delivery ended; re-prove actual committed source
                    # identity/epoch in private TEMP before the local ack.
                    with _proof(profile, store) as (fresh, fresh_source, verify):
                        if fresh_source != source:
                            _fail("SYNC_SENDER_SOURCE_CHANGED")
                        verify()
                        return super()._complete_published(request, current_version=fresh["version"])
            remote = client or SyncClient(options["url"], token_provider=token_provider, check_cancelled=check)
            cycle = Delivery()
            cycle._assert_delivery_peer(request)
            target = remote.handshake()
            cycle.check()
            protocol.validate_handshake(target["handshake"], source["handshake"])
            if (target["library_id"] != options["sync_target_library_id"]
                    or target["node_id"] != options["sync_target_node_id"]
                    or target["library_id"] == source["library_id"] or target["node_id"] == source["node_id"]):
                _fail("SYNC_PEER_MISMATCH")
            cycle.acquire(delivery_only=True)
            with cycle.heartbeat(delivery_only=True):
                return cycle.deliver_no_resend(operation_id=operation_id)[0]
