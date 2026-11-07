"""One durable home synchronization cycle, serialized by the existing worker.

The OS worker lock owns knowledge writes. Only the lease heartbeat runs in a
second thread; it can update the state file but cannot write knowledge. Network
receipts, never local processing success, advance the publication checkpoint.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import threading
import uuid

from . import config, db, pipeline, report, sync_access, sync_events, sync_protocol
from . import sync_queue, sync_reports, sync_snapshot, tasks, worker
from . import storage_admin, storage_lifecycle
from .budget_contract import BudgetDeferred
from .sync_client import SyncClient
from .sync_protocol import SyncError
from .sync_state import StateStore
from .sync_source_opportunity17 import ReadmissionRequired17, consume_required17

MAX_EVENT_PAGES = 20
HEARTBEAT_SECONDS = 30
_EMPTY_CURSOR = {"stream": None, "after": 0, "through": None}
_RECOVERY_ERRORS = {"SYNC_RECOVERY_HELD", "SYNC_RECOVERY_GENERATION_MISMATCH", "SYNC_RECOVERY_STREAM_MISMATCH",
                    "SYNC_RECOVERY_FRESH_PUBLICATION_REQUIRED"}
_RECLAIM = {"SYNC_CLAIM_EXPIRED", "SYNC_CLAIM_MISMATCH", "SYNC_ITEM_NOT_FOUND",
            "SYNC_OUTCOME_MISMATCH", "SYNC_STALE_PUBLICATION"}
_REJECTED_PUBLICATION = {"INVALID_SYNC_SNAPSHOT", "INVALID_SYNC_REPORT", "SYNC_CONTENT_CONFLICT",
    "SYNC_VERSION_CONFLICT", "SYNC_CLAIM_SOURCE_MISMATCH", "SYNC_ATOM_OWNER_MISMATCH",
    "SYNC_VIDEO_OWNER_MISMATCH", "SYNC_INCOMPLETE_SNAPSHOT", "SYNC_PUBLICATION_CONFLICT"}


def _error(code):
    raise SyncError("Home synchronization cannot continue; check its status", code, 409)


def _sender_config_snapshot():
    """Read peer and credentials from one atomic file/config observation.

    The general application cache may lag an external credential rotation.
    Reuse its parser and overlay rules, but never mix a cached peer with the
    separate fresh credential-only accessor. Release the lock before network.
    """
    try:
        with config._config_lock:
            values = dict(config.DEFAULTS)
            values.update(config._read_local(config.local_path()))
            environment = dict(os.environ)
            for key in tuple(values):
                override = environment.get(f"RENO_{key.upper()}")
                if override is not None:
                    values[key] = override
            return values
    except (OSError, ValueError, TypeError, AttributeError, KeyError, UnicodeError):
        _error("SYNC_ACCESS_UNAVAILABLE")


def settings(*, delivery_only=False, _snapshot=None):
    """Validate an explicit configuration without connecting or creating state."""
    values = _sender_config_snapshot() if _snapshot is None else _snapshot
    if not delivery_only and values["sync_enabled"] is not True:
        _error("SYNC_DISABLED")
    if str(values.get("node_role") or "all").lower() == "cloud":
        _error("SYNC_HOME_REQUIRED")
    from .sync_client import _base_url
    url, _, _ = _base_url(values["sync_cloud_url"])
    identifiers = {}
    for key in ("sync_target_library_id", "sync_target_node_id"):
        value = values[key]
        try:
            if type(value) is not str or str(uuid.UUID(value)) != value:
                raise ValueError
        except (ValueError, AttributeError):
            _error("SYNC_TARGET_NOT_CONFIGURED")
        identifiers[key] = value
    interval = values["sync_interval_seconds"]
    if type(interval) is not int or not 15 <= interval <= 3600:
        _error("SYNC_INTERVAL_INVALID")
    return {"url": url, "interval": interval, **identifiers}


def _assert_peer_configuration(options, *, snapshot=None):
    current = settings(delivery_only=True, _snapshot=snapshot)
    if any(current[key] != options[key] for key in ("url", "sync_target_library_id", "sync_target_node_id")):
        _error("SYNC_PEER_MISMATCH")


def _sender_token(options):
    snapshot = _sender_config_snapshot()
    _assert_peer_configuration(options, snapshot=snapshot)
    # SyncClient performs the existing closed credential syntax validation.
    # This value belongs to precisely the peer observation just checked.
    return snapshot["sync_token"]


def _now():
    return datetime.now(timezone.utc)


def _stamp(value):
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _status(store, state, *, error=None, retry_at=None, success=False):
    status = store.read()["status"]
    status.update(state=state, error_code=error, updated_at=_stamp(_now()), retry_at=retry_at)
    if success:
        status["last_success_at"] = status["updated_at"]
    store.update({"status": status})


def _local():
    con = db.connect()
    try:
        return sync_access.identity(con), db.knowledge_version(con)
    finally:
        con.close()


def _content_digest(snapshot):
    return hashlib.sha256(sync_protocol._json_bytes({"videos": snapshot["videos"],
                                                   "globals": snapshot["globals"]})).hexdigest()


def _reports_digest(reports):
    return hashlib.sha256(sync_protocol._json_bytes(sorted(reports, key=lambda value: value["name"]))).hexdigest()


def _upload_cache_reset(store, state):
    # An activated sender contract makes the legacy dictionary an independent
    # audit reference, including after restoring an old V1 state image.
    # Neither a recovery fence nor one publication's error proves it dead.
    return {} if store.has_sender_contract(state=state) else {"uploaded_media": {}}


def _mapped_work(work):
    return work is not None and work.get("source_mapping_generation") is not None


class _SourceFailed(Exception):
    """A permanent original failure has been saved and its core CAS-closed."""


class _SourcePublished(Exception):
    """An original source publication and its local ack are permanently proved."""


class _Cycle:
    def __init__(self, store, client, source, options, stop_event):
        self.store, self.client, self.source = store, client, source
        self.options, self.stop_event = options, stop_event
        self.database = config.db_path().resolve()
        self.home_data = config.DATA.resolve()
        self.storage_profile = storage_admin.profile()
        self.lost = threading.Event()
        self.pulse_error = None
        self.recovering = False
        self.knowledge_generation = None
        self.knowledge_stream = None
        self._fresh_source_claim = None
        self._opportunity_actions17 = set()

    @contextmanager
    def storage_connection(self):
        """Own the file lifetime before opening SQLite or reading pointers."""
        with storage_lifecycle.shared(self.storage_profile, check_cancelled=self.check):
            con = db.connect(self.storage_profile.database)
            try:
                yield con
            except BaseException:
                try:
                    con.close()
                except BaseException:
                    pass
                raise
            else:
                con.close()

    def check(self):
        if (self.stop_event.is_set() or config.db_path().resolve() != self.database
                or config.DATA.resolve() != self.home_data):
            _error("SYNC_CANCELLED")
        _assert_peer_configuration(self.options)
        if self.lost.is_set():
            _error(self.pulse_error or "SYNC_HEARTBEAT_LOST")

    def lease(self):
        self.check()
        value = self.store.read_observation()["lease"]
        if value is None or sync_queue._stored_time(value["lease_until"]) <= _now():
            _error("SYNC_LEASE_EXPIRED")
        return value

    def recovery(self):
        value = self._state_observation()["recovery"]
        if value is None:
            _error("SYNC_STATE_RECOVERY_CONFLICT")
        return value

    def _state_observation(self):
        # File-protected hashing/upload callbacks never acquire the writer's
        # RLock after SH. They observe a complete atomically published image.
        if storage_lifecycle.current_guard() is not None:
            return self.store.read_observation()
        return self.store.read()

    def check_knowledge(self):
        self.check()
        recovery = self.recovery()
        if recovery["state"] == "held":
            _error("SYNC_RECOVERY_HELD")
        if self.knowledge_stream is not None and recovery["stream"] != self.knowledge_stream:
            _error("SYNC_RECOVERY_STREAM_MISMATCH")
        if self.knowledge_generation is not None and recovery["generation"] != self.knowledge_generation:
            _error("SYNC_RECOVERY_GENERATION_MISMATCH")
        if recovery["state"] == "resume_pending" and not self.recovering:
            _error("SYNC_RECOVERY_FRESH_PUBLICATION_REQUIRED")

    def acquire(self, *, delivery_only=False):
        self.check()
        state = self.store.read()
        lease = state["lease"]
        try:
            try:
                # A previous renewal reply may have been lost: the server can
                # still accept the original token after our recorded expiry.
                result = self.client.heartbeat(self.source, state["session_id"], lease=lease)
            except SyncError as exc:
                if exc.code != "SYNC_LEASE_EXPIRED" or lease is None:
                    raise
                # Recovery metadata arrives only with the NEW heartbeat. Never
                # discard downloaded work before discovering a receiver hold.
                if not delivery_only:
                    self.store.archive_progress("claim_changed")
                self.store.update({"lease": None})
                result = self.client.heartbeat(self.source, state["session_id"], lease=None)
        except SyncError as exc:
            # A lost FIRST heartbeat response has no token to renew. Persist a
            # wait state and retry acquisition later; never bypass server fencing.
            if exc.code in {"SYNC_SESSION_BUSY", "SYNC_LEASE_MISMATCH"}:
                _status(self.store, "waiting_lease", error=exc.code,
                        retry_at=_stamp(_now() + timedelta(seconds=self.options["interval"])))
            raise
        if delivery_only:
            # Historical terminal delivery does not install a new recovery
            # control or archive ordinary claims/work on an epoch transition.
            self.store.update({"lease": result["lease"]})
        else:
            self.store.observe_recovery(result["recovery"], lease=result["lease"])

    @contextmanager
    def heartbeat(self, *, delivery_only=False):
        stopped = threading.Event()

        def pulse():
            while not stopped.wait(HEARTBEAT_SECONDS):
                try:
                    lease = self.lease()
                    result = self.client.heartbeat(self.source, lease["session_id"], lease=lease)
                    # Only this field: never write an old copy of cursors/work.
                    if delivery_only:
                        self.store.update({"lease": result["lease"]})
                    else:
                        self.store.observe_recovery(result["recovery"], lease=result["lease"])
                except Exception as exc:
                    self.pulse_error = exc.code if isinstance(exc, SyncError) else "SYNC_HEARTBEAT_LOST"
                    self.lost.set()
                    return

        thread = threading.Thread(target=pulse, name="reno-sync-heartbeat", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            # The HTTP client has bounded timeouts. Do not release state/config
            # ownership while a late heartbeat is still capable of writing.
            thread.join()

    def deliver_no_resend(self, *, operation_id=None):
        """Retry only committed authorizations; never choose a new batch."""
        requests = self.store.pending_no_resend_requests(allow_incomplete=True)
        if operation_id is not None:
            requests = [request for request in requests if request["operation_id"] == operation_id]
            if not requests:
                response = self.store.no_resend_receipt(operation_id)
                if response is None:
                    _error("SYNC_NO_RESEND_REQUEST_NOT_PENDING")
                return [{"operation_id": operation_id, "state": response["state"]}]
        results = []
        for request in requests:
            self._assert_delivery_peer(request)
            if request["reason"] == "published_receipt":
                response = self.store.no_resend_receipt(request["operation_id"], allow_incomplete=True)
                if response is None:
                    response = self.client.publication_receipt(self.source, self.lease(), binding=request["binding"],
                                                              payload_sha256=request["payload_sha256"])
                self._assert_delivery_peer(request)
                from .sync_no_resend import normalize_response
                response = normalize_response(request, response)
                self.store.record_published_terminal(request["metadata"], response["receipt"],
                    recovery_generation=request["binding"]["recovery_generation"], recovery_stream=request["binding"]["recovery_stream"],
                    recovery_bootstrap=self._is_recovery_publication(request["metadata"]))
                self._complete_published(request)
                results.append({"operation_id": request["operation_id"], "state": "published"})
                continue
            # Repair marker -> detach crashes before going onto the network.
            # This short state mutation holds SH, but neither network call does.
            self.store.resume_no_resend_commit(request["operation_id"])
            self._assert_delivery_peer(request)
            response = self.store.no_resend_receipt(request["operation_id"])
            if response is not None:
                results.append({"operation_id": request["operation_id"], "state": response["state"]})
                continue
            try:
                response = self.client.abandon_uploads(self.source, self.lease(), binding=request["binding"],
                                                      operation_id=request["operation_id"])
            except SyncError as exc:
                if exc.code != "SYNC_UPLOAD_PUBLISHED":
                    raise
                self._assert_delivery_peer(request)
                response = self.client.publication_receipt(self.source, self.lease(), binding=request["binding"],
                                                          payload_sha256=request["payload_sha256"])
            self._assert_delivery_peer(request)
            self.store.record_no_resend_receipt(request["operation_id"], response)
            results.append({"operation_id": request["operation_id"], "state": response["state"]})
        return results

    def _complete_published(self, request, *, current_version=None):
        """Finish the original local ack; a historical receipt sends no content."""
        with self.store.reference_lifetime():
            return self._complete_published_locked(request, current_version=current_version)

    def _is_recovery_publication(self, metadata):
        return self.store.is_recovery_publication(metadata)

    def _complete_published_locked(self, request, *, current_version=None):
        self.check()
        state = self.store.read()
        if self._is_recovery_publication(request["metadata"]):
            if current_version is None:
                with self.storage_connection() as con:
                    current_version = db.knowledge_version(con)
            latest = self.store.acknowledge_recovery_publication(request, current_version=current_version)
            return latest["recovery_attempt"] is None
        if state["pending_publication"] != request["metadata"]:
            return False
        binding = request["binding"]
        envelope = self.store.read_publication(request["metadata"], proved_stream=binding["recovery_stream"])
        snapshot = envelope["snapshot"]
        core = state.get("source_execution")
        if (self.source["handshake"]["migration_version"] == 17 and core is not None
                and any(entry["claim"]["item_id"] == core["item_id"] for entry in envelope["claims"])):
            # The permanent P05 request+receipt closes only this original
            # source before any old-pointer ack or new-stream progress choice.
            from . import sync_source_lifecycle17 as original17
            original17.complete_terminal17_locked(profile=self.storage_profile, store=self.store,
                kind="source_published")
            state = self.store.read()
        if current_version is None:
            with self.storage_connection() as con:
                current_version = db.knowledge_version(con)
        sync_snapshot._version(current_version)
        control = state["recovery"]
        if (control is None or control["stream"] != binding["recovery_stream"]
                or control["generation"] < binding["recovery_generation"]
                or state["local_epoch"] != snapshot["version"]["library_epoch"]
                or current_version["library_id"] != snapshot["version"]["library_id"]
                or current_version["library_epoch"] != snapshot["version"]["library_epoch"]):
            # This original receipt proves no progress in an unknown/new stream.
            # Close only its exact old pointer; leave all work and counters.
            self.store.detach_no_resend(request["operation_id"])
            return False
        core = state.get("source_execution")
        if core is not None and self.source["handshake"]["migration_version"] != 17 and any(
                entry["claim"]["item_id"] == core["item_id"] for entry in envelope["claims"]):
            self.store.complete_published_source(core, request)
        self.store.acknowledge_publication(request["metadata"],
            recovery_generation=binding["recovery_generation"], recovery_stream=binding["recovery_stream"],
            version=snapshot["version"], content_sha256=_content_digest(snapshot),
            reports_sha256=_reports_digest(snapshot["reports"]),
            preserve_work=getattr(self, "recovering", False) or not envelope["claims"])
        latest = self.store.read()
        if latest["pending_publication"] == request["metadata"] and (
                latest["recovery"] is None or latest["recovery"]["stream"] != binding["recovery_stream"]):
            self.store.detach_no_resend(request["operation_id"])
        return self.store.read()["pending_publication"] != request["metadata"]

    def _assert_delivery_peer(self, request):
        self.check()
        state_binding = self.store.read_observation()["binding"]
        expected = {"home_library_id": self.source["library_id"], "home_node_id": self.source["node_id"],
                    "target_library_id": self.options["sync_target_library_id"],
                    "target_node_id": self.options["sync_target_node_id"]}
        if (any(request[key] != value or state_binding[key] != value for key, value in expected.items())
                or state_binding["target_url"] != self.options["url"]):
            _error("SYNC_PEER_MISMATCH")

    def _merge_page(self, page, claims=None):
        self.check()
        con = db.connect()
        try:
            sync_events.merge_events(con, page["events"])
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()
        # Crash between commit and cursor advancement only replays original UUIDs.
        updates = {"inbound": {"stream": page["stream"], "after": page["next_cursor"],
                                "through": page["high_watermark"] if page["has_more"] else None}}
        if not page["has_more"]:
            updates["seeded"] = True
        state = self.store.read()
        claim_changes, archive_work = None, False
        if claims is not None and state["recovery"]["state"] == "running":
            core = state.get("source_execution")
            if core is not None and self.source["handshake"]["migration_version"] == 17:
                # Targeted readmit owns this exact original claim. Ordinary
                # event pages must never replace it or advance its work.
                from .sync_source_cycle17 import apply_review_page17_locked
                apply_review_page17_locked(store=self.store, updates=updates, recovery=state["recovery"])
                return
            if core is not None:
                claims = [entry for entry in claims if entry["item_id"] == core["item_id"] and hashlib.sha256(
                    entry["url"].encode("utf-8")).hexdigest() == core["submitted_url_sha256"]]
                self._fresh_source_claim = bool(claims)
                if not claims:
                    self.store.apply_review_page(updates, recovery=state["recovery"])
                    return
            claim_changes = {"claims": claims}
            work = state["work"]
            if work:
                self.store.assert_work_reusable(work)
                original = next((c for c in state["claims"] if c["item_id"] == work["item_id"]), None)
                matched = next((c for c in claims if c["item_id"] == work["item_id"]
                                and original is not None and c["url"] == original["url"]), None)
                if matched and matched["claim"]["lease_generation"] != work["lease_generation"]:
                    with self.store.reference_lifetime():
                        self.ensure_work_mapping(work, original)
                    claim_changes["work"] = {**work, "lease_generation": matched["claim"]["lease_generation"]}
                elif matched is None:
                    if work.get("source_mapping_generation") is not None:
                        # Lack of a current cloud claim cannot choose another
                        # local work instance or erase its persisted mapping
                        # inputs. A matching renewal will reuse this generation.
                        claim_changes = None
                    else:
                        archive_work = True
                        claim_changes["work"] = None
        self.store.apply_review_page(updates, claim_changes=claim_changes,
                                     recovery=state["recovery"], archive_work=archive_work)

    def pull_events(self, *, claim_limit=0):
        if self.source["handshake"]["migration_version"] == 17 and claim_limit:
            from .sync_source_cycle17 import ordinary_claim_limit17_locked
            claim_limit = min(claim_limit, ordinary_claim_limit17_locked(store=self.store))
        _status(self.store, "pulling")
        for _ in range(MAX_EVENT_PAGES):
            self.check()
            cursor = self.store.read()["inbound"]
            try:
                result = self.client.pull(self.source, self.lease(), **cursor, claim_limit=claim_limit)
            except SyncError as exc:
                if exc.code not in {"SYNC_EVENT_STREAM_CHANGED", "SYNC_CURSOR_OUT_OF_RANGE"} or cursor == _EMPTY_CURSOR:
                    raise
                # Receiver restoration can lose our acknowledged events/media.
                # Rescan BOTH event directions; original UUIDs make it idempotent.
                self.store.archive_progress("epoch_changed")
                self.store.update({"inbound": dict(_EMPTY_CURSOR), "outbound": dict(_EMPTY_CURSOR),
                                   "seeded": False, **_upload_cache_reset(self.store, self.store.read()), "published_version": None,
                                   "published_content_sha256": None,
                                   "published_reports_sha256": None})
                continue
            self.store.observe_recovery(result["recovery"])
            page = result["events"]
            if page["stream"]["library_id"] != self.options["sync_target_library_id"]:
                _error("SYNC_PEER_MISMATCH")
            self._merge_page(page, result["claims"] if claim_limit and self.recovery()["state"] == "running" else None)
            if (self.source["handshake"]["migration_version"] == 17 and claim_limit
                    and self.recovery()["state"] == "running" and self.store.read()["claims"]):
                # The real Client parser retains its authenticated observation;
                # this ordinary returned DTO never supplies a producer seal.
                self.client.source_continuity(self.source, {
                    "library_id": self.options["sync_target_library_id"],
                    "node_id": self.options["sync_target_node_id"]})
            if not page["has_more"]:
                return True
        return False

    def push_events(self):
        _status(self.store, "pushing_reviews")
        for _ in range(MAX_EVENT_PAGES):
            self.check()
            recovery_stream = self.recovery()["stream"]
            cursor = self.store.read()["outbound"]
            con = db.connect()
            try:
                version = db.knowledge_version(con)
                stream = {k: version[k] for k in ("library_id", "library_epoch")}
                if cursor["stream"] != stream:
                    cursor = {"stream": stream, "after": 0, "through": None}
                page = sync_events.export_events(con, after=cursor["after"], through=cursor["through"])
            finally:
                con.close()
            if page["events"]:
                self.client.push_events(self.source, self.lease(), page["events"])
            self.check()
            self.store.advance_outbound({"stream": page["stream"], "after": page["next_cursor"],
                                          "through": page["high_watermark"] if page["has_more"] else None},
                                         recovery_stream=recovery_stream)
            if self.recovery()["stream"] != recovery_stream:
                return False
            if not page["has_more"]:
                return True
        return False

    def _path(self, asset):
        files = db.uj(asset.get("files_json"), {})
        submitted = files.get("video")
        if not isinstance(submitted, str) or not submitted:
            _error("SYNC_LOCAL_MEDIA_MISSING")
        try:
            path = Path(submitted.replace("\\", "/"))
            if not path.is_absolute():
                path = self.storage_profile.media.parent / path
            # The sender never treats remote storage_key as a local pathname.
            if ".." in path.parts or any(":" in part for part in path.parts[1:]):
                raise ValueError
            path = Path(os.path.abspath(path))
            path.relative_to(self.storage_profile.media)
        except ValueError:
            _error("SYNC_LOCAL_MEDIA_UNSAFE")
        return path

    def media_descriptor(self, asset, *, verify=True):
        from .sync_media import _Directory
        storage_lifecycle.require_guard(self.storage_profile)
        path = self._path(asset)
        with _Directory(path.parent, base=self.storage_profile.media) as directory:
            with directory.open_file(path.name) as handle:
                if not verify:
                    return sync_protocol.validate_media_descriptor({"video_id": asset["video_id"],
                        "sha256": asset["content_sha256"], "byte_size": os.fstat(handle.fileno()).st_size,
                        "extension": path.suffix[1:].lower()})
                size, digest = 0, hashlib.sha256()
                while chunk := handle.read(128 * 1024):
                    self.check_knowledge()
                    size += len(chunk)
                    if size > sync_protocol.MAX_MEDIA_BYTES:
                        _error("SYNC_MEDIA_TOO_LARGE")
                    digest.update(chunk)
            directory.verify()
        if digest.hexdigest() != asset["content_sha256"]:
            _error("SYNC_MEDIA_HASH_MISMATCH")
        return sync_protocol.validate_media_descriptor({"video_id": asset["video_id"],
                   "sha256": digest.hexdigest(), "byte_size": size, "extension": path.suffix[1:].lower()})

    def snapshot(self, con, *, verify, recovery_projection=False):
        storage_lifecycle.require_guard(self.storage_profile)
        assets = [dict(row) for row in con.execute("SELECT * FROM video_asset WHERE status='processed'")]
        media = {asset["video_id"]: self.media_descriptor(asset, verify=verify) for asset in assets}
        return sync_snapshot.export_snapshot(con, publication_id=str(uuid.uuid4()), media=media,
            reports=sync_reports.export_reports(con, profile=self.storage_profile,
                recovery_projection=recovery_projection) if verify else [], recovery_projection=recovery_projection)

    def needs_publication(self):
        """Cover every wire field, including zero-atom videos and ASR repairs.

        Existing revision triggers serve product caches, not the entire sync
        closure. Detect closure changes here; hash media bytes only when actually
        freezing/uploading, so idle polls do not reread the whole video library.
        """
        self.check_knowledge()
        state = self.store.read()
        with self.storage_connection() as con:
            con.execute("BEGIN")
            probe = self.snapshot(con, verify=False)
            changed = _content_digest(probe) != state["published_content_sha256"]
            version_changed = probe["version"] != state["published_version"]
            reports_changed = _reports_digest(sync_reports.export_reports(con, profile=self.storage_profile)) != state["published_reports_sha256"]
            con.rollback()
            if state["published_version"] is None or changed and not version_changed:
                self.check_knowledge()
                con.execute("BEGIN IMMEDIATE")
                current = self.snapshot(con, verify=False)
                if state["published_version"] is None or (current["version"] == state["published_version"]
                        and _content_digest(current) != state["published_content_sha256"]):
                    # Missing/cleared state is not proof the receiver has no
                    # history. Reserve a fresh revision for first/recovery sync.
                    db.bump_kb_revision(con)
                con.commit()
            return changed or version_changed or reports_changed

    def _resume_frozen_publication17(self):
        """Complete an original selection before reading new publication inputs."""
        if (self.source["handshake"]["migration_version"] != 17
                or self.store.read().get("source_execution") is None):
            return None
        from .sync_source_publication_local2 import resume_publication2_locked
        return resume_publication2_locked(profile=self.storage_profile, store=self.store)

    def freeze(self, claims, *, publication_id=None, recovery_bootstrap=False):
        # State ownership -> SH, continuous from input proof until both durable
        # outbox and current pending reference exist. No network runs here.
        with self.store.reference_lifetime():
            if not recovery_bootstrap and self._resume_frozen_publication17() is not None:
                return
            self.check_knowledge()
            if publication_id is not None:
                self.store.assert_sendable(publication_id)
            with self.storage_connection() as con:
                con.execute("BEGIN")
                if recovery_bootstrap:
                    attempt = self.store.read_observation()["recovery_attempt"]
                    current = db.knowledge_version(con)
                    if attempt is None or attempt["publication_id"] != publication_id:
                        _error("SYNC_STATE_RECOVERY_CONFLICT")
                    if current != attempt["version"]:
                        # Regenerate static reports for the newly observed view
                        # on the next cycle; never freeze incompatible bytes.
                        _error("SYNC_STATE_RECOVERY_CONFLICT")
                snapshot = self.snapshot(con, verify=True, recovery_projection=recovery_bootstrap)
                if recovery_bootstrap and {entry["name"] for entry in snapshot["reports"]} != set(report.REPORTS) - {"summary"}:
                    _error("SYNC_STATE_RECOVERY_CONFLICT")
                if publication_id is not None:
                    snapshot["publication_id"] = publication_id
            self.check_knowledge()
            if (not recovery_bootstrap and self.source["handshake"]["migration_version"] == 17
                    and self.store.read().get("source_execution") is not None):
                from . import sync_source_lifecycle17 as original17
                # Admission may request lock-free observation/readmission. The
                # actual17 factory resolves that before any immutable bytes and
                # retains the same view through descriptor creation.
                original17.freeze_publication17_locked(profile=self.storage_profile,
                    store=self.store, client=self.client, snapshot=snapshot, claims=claims,
                    recovery_generation=self.knowledge_generation, recovery_stream=self.knowledge_stream)
                return
            metadata = self.store.write_publication(snapshot, claims, recovery_generation=self.knowledge_generation,
                                                    recovery_stream=self.knowledge_stream)
            installed = (self.store.install_recovery_publication(metadata, attempt=self.store.read()["recovery_attempt"])
                if recovery_bootstrap else self.store.install_publication(metadata,
                    recovery_generation=self.knowledge_generation, recovery_stream=self.knowledge_stream))
            if not installed:
                self.check_knowledge()
                _error("SYNC_STATE_RECOVERY_CONFLICT")

    def publish(self, *, recovery_bootstrap=False):
        state = self.store.read()
        metadata = self.store.recovery_publication() if recovery_bootstrap else state["pending_publication"]
        if metadata is None:
            return
        publication_id = metadata["publication_id"]
        request = self.store.publication_no_resend_request(publication_id, allow_incomplete=True)
        if request is not None and request["reason"] == "published_receipt":
            if request["metadata"] != metadata:
                _error("SYNC_NO_RESEND_BINDING_MISMATCH")
            # Receipt/ack interruption resumes its exact terminal proof. The
            # uniform content-sending gate remains closed for this UUID.
            self.deliver_no_resend(operation_id=request["operation_id"])
            return
        self.store.assert_sendable(publication_id)
        envelope = self.store.read_publication(metadata)
        snapshot, claims = envelope["snapshot"], envelope["claims"]
        generation = envelope["recovery_generation"]
        recovery_stream = envelope["recovery_stream"]
        self.check_knowledge()
        if recovery_stream is None:
            # A legacy orphan without its original inbound cursor has no proof
            # of a receiver epoch. Preserve it and build a new explicit batch.
            self.store.archive_progress("recovery")
            self.store.update({"pending_publication": None, "published_version": None,
                               "published_content_sha256": None, "published_reports_sha256": None})
            return
        if recovery_stream != self.recovery()["stream"]:
            _error("SYNC_RECOVERY_STREAM_MISMATCH")
        if generation != self.recovery()["generation"]:
            _error("SYNC_RECOVERY_GENERATION_MISMATCH")
        # A database restored to a new epoch must not republish the old outbox.
        if snapshot["version"]["library_epoch"] != state["local_epoch"]:
            if recovery_bootstrap:
                _error("SYNC_RECOVERY_SOURCE_EPOCH_BEHIND")
            self.store.update({"pending_publication": None, "published_version": None,
                               "published_content_sha256": None, "published_reports_sha256": None})
            return
        _status(self.store, "uploading")
        binding = {"publication_id": snapshot["publication_id"], "source_node_id": self.source["node_id"],
            "source_version": snapshot["version"], "recovery_generation": generation, "recovery_stream": recovery_stream}

        def check_publication():
            self.check_knowledge()
            # read_observation + permanent ledger only: callbacks may already
            # hold media SH and must not take StateStore's mutation RLock.
            self.store.assert_sendable(publication_id)

        check_publication()
        prepared = self.client.prepare_uploads(self.source, self.lease(), binding=binding,
                                              descriptors=[video["media"] for video in snapshot["videos"]])
        for video in snapshot["videos"]:
            check_publication()
            descriptor = video["media"]
            if prepared["state"] == "published":
                continue
            intent = {**binding, "descriptor": descriptor}
            verified = self.client.verify_upload(self.source, self.lease(), intent=intent, check_cancelled=check_publication)
            if verified["stored"]:
                continue
            with self.storage_connection() as con:
                asset = con.execute("SELECT * FROM video_asset WHERE video_id=?", (video["video_id"],)).fetchone()
                if asset is None:
                    _error("SYNC_LOCAL_MEDIA_MISSING")
                path = self._path(dict(asset))
                from .sync_media import _Directory
                with _Directory(path.parent, base=self.storage_profile.media) as directory:
                    check_publication()
                    self.client.upload_media(self.source, self.lease(), descriptor, path,
                        intent=intent, check_cancelled=check_publication)
                    directory.verify()
            self.store.record_uploaded(binding, descriptor)
        _status(self.store, "publishing")
        try:
            check_publication()
            receipt = self.client.push_snapshot(self.source, self.lease(), snapshot, claims,
                                               recovery_generation=generation, recovery_stream=recovery_stream)
        except SyncError as exc:
            if exc.code in {"SYNC_MEDIA_NOT_FOUND", "SYNC_MEDIA_CORRUPT", "SYNC_MEDIA_HASH_MISMATCH"}:
                self.store.update(_upload_cache_reset(self.store, self.store.read()))
            elif self.store.read().get("source_execution") is not None:
                # A tracked execution needs original terminal evidence before
                # failure/reclaim can retire its claims or frozen publication.
                # Preserve the paid facts and let the same job remain blocked.
                raise
            elif exc.code in _RECLAIM and not self.recovering:
                # Retain the immutable file as recovery evidence. A later pull
                # obtains a legitimate new claim and creates a NEW publication.
                self.store.retire_progress(recovery_generation=generation, recovery_stream=recovery_stream, archive=True,
                    preserve_work=_mapped_work(self.store.read()["work"]))
            elif exc.code in _REJECTED_PUBLICATION and not self.recovering:
                # A definitive rejection cannot be fixed by sending the same
                # frozen invalid bytes indefinitely while renewing its claim.
                for entry in claims:
                    try:
                        self.client.fail(self.source, self.lease(), entry["claim"], "content_invalid",
                                         recovery_generation=generation, recovery_stream=recovery_stream)
                    except SyncError as failure:
                        if failure.code not in _RECLAIM:
                            raise
                self.store.retire_progress(recovery_generation=generation, recovery_stream=recovery_stream,
                    preserve_work=_mapped_work(self.store.read()["work"]))
            raise
        if receipt["local_version"]["library_id"] != self.options["sync_target_library_id"]:
            _error("SYNC_PEER_MISMATCH")
        self.check()
        request = self.store.record_published_terminal(metadata, receipt,
            recovery_generation=generation, recovery_stream=recovery_stream, recovery_bootstrap=recovery_bootstrap)
        self._complete_published(request)
        observed = self.recovery()
        status = "recovery_held" if observed["state"] == "held" else (
            "recovery_resuming" if observed["state"] == "resume_pending" or observed["generation"] > generation else "published")
        _status(self.store, status, success=True)

    def saved_work_origin(self, work, entry):
        """Prove existing downloaded work before reusing an expired claim's output."""
        from . import sync_sources
        self.store.assert_work_reusable(work)
        if entry is None or work.get("source_mapping_id") is None:
            _error("SYNC_RECOVERY_LOCAL_WORK_INVALID")
        source_map = sync_sources.SourceMap(self.home_data, profile=self.storage_profile,
                                           source_library_id=self.source["library_id"],
                                           source_node_id=self.source["node_id"])
        origin = source_map.get(work["source_mapping_id"], mapping_generation=work.get("source_mapping_generation"))
        with self.storage_connection() as con:
            row = con.execute("SELECT * FROM video_asset WHERE video_id=?", (work["video_id"],)).fetchone()
            if row is None:
                _error("SYNC_RECOVERY_LOCAL_WORK_INVALID")
            descriptor = self.media_descriptor(dict(row))
        if (origin["submitted_url"] != entry["url"] or origin["video_id"] != work["video_id"]
                or origin["media_sha256"] != descriptor["sha256"]):
            _error("SYNC_RECOVERY_LOCAL_WORK_INVALID")
        return origin

    def advance_work(self, work):
        """Finish only saved local stages; callers own claim policy and failures."""
        self.store.assert_work_reusable(work)
        video_id = work["video_id"]
        if work["phase"] == "downloaded":
            self.check_knowledge()
            result = pipeline.run_video(video_id, check_cancelled=self.check_knowledge)
            self.check_knowledge()
            if any(isinstance(v, str) and v.startswith("ERROR") for v in result.values()):
                _error("SYNC_PROCESSING_FAILED")
            work["phase"] = "processed"
            self.store.update({"work": work})
        if work["phase"] == "processed":
            self.check_knowledge()
            pipeline.judge_all()
            work["phase"] = "judged"
            self.store.update({"work": work})
        self.check_knowledge()
        return work

    def ensure_work_mapping(self, work, entry):
        """Resume the saved instance; never resolve or choose a replacement UUID."""
        from . import sync_sources
        self.store.assert_work_reusable(work)
        if work["item_id"] != entry["item_id"]:
            _error("SYNC_RECOVERY_LOCAL_WORK_INVALID")
        generation = work.get("source_mapping_generation")
        inputs = work.get("source_mapping_inputs")
        if generation is None or inputs is None:
            # A legacy linked map remains an explicit legacy instance. It is
            # never silently upgraded into a fresh UUID when proof is missing.
            return self.saved_work_origin(work, entry)
        if inputs["resolution"]["submitted_url"] != entry["url"]:
            _error("SYNC_RECOVERY_LOCAL_WORK_INVALID")
        with self.storage_connection() as con:
            row = con.execute("SELECT * FROM video_asset WHERE video_id=?", (work["video_id"],)).fetchone()
            if row is None or row["content_sha256"] != inputs["media_sha256"]:
                _error("SYNC_SOURCE_MEDIA_MISMATCH")
            asset = dict(row)
        source_map = sync_sources.SourceMap(self.home_data, profile=self.storage_profile,
                    source_library_id=self.source["library_id"], source_node_id=self.source["node_id"])
        origin = source_map.record(inputs["resolution"], extracted_video_id=inputs["extracted_video_id"],
                   asset=asset, mapping_generation=generation, recorded_at=inputs["recorded_at"],
                   check_cancelled=self.check_knowledge)
        if work.get("source_mapping_id") is not None and work["source_mapping_id"] != origin["mapping_id"]:
            _error("SYNC_SOURCE_MAP_CORRUPT")
        if work.get("source_mapping_id") is None:
            work["source_mapping_id"] = origin["mapping_id"]
            self.store.update({"work": work})
        return origin

    def resume_publication(self):
        recovery = self.recovery()
        self.knowledge_generation = recovery["generation"]
        self.knowledge_stream = recovery["stream"]
        self.recovering = True
        _status(self.store, "recovery_resuming")
        state = self.store.read()
        self.check_knowledge()
        state = self.store.read()
        attempt = state["recovery_attempt"]
        if (attempt is not None and attempt["generation"] == recovery["generation"]
                and attempt["stream"] == recovery["stream"]):
            saved = self.store.recovery_publication(allow_claimed=True)
            if saved is not None:
                envelope = self.store.read_publication(saved)
                if envelope["claims"]:
                    # A legacy marker may point at an original claimed batch.
                    # Preserve its exact file/pointer/history, then reserve an
                    # independent UUID; it cannot become a claims=[] bootstrap.
                    self.store.archive_progress("recovery")
                    self.store.update({"recovery_attempt": None})
                    attempt = None
                else:
                    self.publish(recovery_bootstrap=True)
                    return
        if (attempt is None or attempt["generation"] != recovery["generation"]
                or attempt["stream"] != recovery["stream"]):
            _, version = _local()
            floor = recovery["source_floor"]
            if floor is not None and version["library_epoch"] < floor["library_epoch"]:
                _error("SYNC_RECOVERY_SOURCE_EPOCH_BEHIND")
            minimum = floor["kb_revision"] if floor and version["library_epoch"] == floor["library_epoch"] else 0
            attempt = {"generation": recovery["generation"], "stream": recovery["stream"], "publication_id": str(uuid.uuid4()),
                       "version": {**version, "kb_revision": max(version["kb_revision"], minimum) + 1}}
            self.store.archive_progress("recovery")
            self.store.update({"recovery_attempt": attempt})
        with self.store.reference_operation():
            with self.storage_connection() as con:
                con.execute("BEGIN IMMEDIATE")
                current = db.knowledge_version(con)
                if current["library_epoch"] != attempt["version"]["library_epoch"]:
                    _error("SYNC_RECOVERY_SOURCE_EPOCH_BEHIND")
                if current["kb_revision"] < attempt["version"]["kb_revision"]:
                    con.execute("UPDATE meta SET value=? WHERE key='kb_revision'", (str(attempt["version"]["kb_revision"]),))
                con.commit()
                con.execute("BEGIN")
                attempt = self.store.refresh_unfrozen_recovery_attempt(db.knowledge_version(con), attempt=attempt)
        self.check_knowledge()
        report.run_all(profile=self.storage_profile, recovery_projection=True)
        self.freeze([], publication_id=attempt["publication_id"], recovery_bootstrap=True)
        self.publish(recovery_bootstrap=True)

    def _budget_owner(self, core):
        """Waiting-control delivery may close work after pause, but needs owner."""
        self.check()
        current, profile = worker.execution_context(), worker._execution_profile()
        if (current is None or profile is None or profile.identity != self.storage_profile.identity
                or not tasks.holds_process_lock() or current["kind"] != "sync"
                or (current["job_id"], current["continuation_id"], current["execution_generation"]) != (
                    core["root_job_id"], core["continuation_id"], core["execution_generation"])):
            _error("SYNC_SOURCE_EXECUTION_BLOCKED")
        with self.storage_connection() as con:
            row = con.execute("SELECT * FROM job WHERE job_id=?", (current["job_id"],)).fetchone()
            if (row is None or row["status"] != "running" or row["owner_id"] != current["owner_id"]
                    or (row["continuation_id"], row["execution_generation"]) != (
                        core["continuation_id"], core["execution_generation"])):
                _error("SYNC_SOURCE_EXECUTION_BLOCKED")
            tasks._require_sync_job(row)

    def _budget_delivery_wait(self, pending):
        retry = _now() + timedelta(seconds=self.options["interval"])
        _status(self.store, "pending_delivery", error="SYNC_BUDGET_DELIVERY_PENDING", retry_at=_stamp(retry))
        return BudgetDeferred("indeterminate", resume_at=retry,
                              operation_id=pending["defer_request"]["budget_operation_id"])

    def _deliver_budget_pending(self, pending):
        """Replay frozen UUID/body/lease; a newer lease never edits old intent."""
        try:
            if self.source["handshake"]["migration_version"] == 17:
                from . import sync_source_lifecycle17 as original17
                for name in ("defer", "resume"):
                    request = pending[name + "_request"]
                    if request is not None:
                        original17.deliver_registered_control17(profile=self.storage_profile, store=self.store,
                            client=self.client, request_id=request["request_id"])
                        original17.record_control_receipt17_locked(profile=self.storage_profile, store=self.store,
                            request_id=request["request_id"])
                        pending = self.store.read()["budget_pending"]
                return pending
            if pending["defer_receipt"] is None:
                original = pending["defer_request"]
                receipt = self.client.budget_request(original)
                pending = self.store.record_budget_defer_receipt(original["request_id"], receipt)
            if pending["resume_request"] is not None and pending["resume_receipt"] is None:
                original = pending["resume_request"]
                receipt = self.client.budget_request(original, defer_receipt=pending["defer_receipt"])
                pending = self.store.record_budget_resume_receipt(original["request_id"], receipt)
        except BudgetDeferred:
            raise
        except Exception as exc:
            if isinstance(exc, SyncError) and exc.code in {"SYNC_CANCELLED", "SYNC_CLIENT_CANCELLED"}:
                raise
            raise self._budget_delivery_wait(pending) from None
        return pending

    def defer_budget(self, deferred):
        """Persist a real wait before releasing cloud claim, then retain job."""
        state = self.store.read()
        core = state.get("source_execution")
        if core is None:
            raise deferred
        self._budget_owner(core)
        if deferred.operation_id is None:
            # A pre-dispatch block has no paid operation to invent. Keep core
            # and original job; the dedicated unblock contract owns recovery.
            _status(self.store, "source_blocked", error="SYNC_SOURCE_EXECUTION_BLOCKED",
                    retry_at=None if deferred.resume_at is None else _stamp(deferred.resume_at))
            raise deferred
        pending = state["budget_pending"]
        if self.source["handshake"]["migration_version"] == 17 and pending is None:
            from . import sync_source_lifecycle17 as original17
            ref = original17.freeze_budget_defer17_locked(profile=self.storage_profile, store=self.store,
                client=self.client, operation_id=deferred.operation_id)
            original17.install_control17_locked(profile=self.storage_profile, store=self.store,
                request_id=ref["record_id"])
            pending = self.store.read()["budget_pending"]
        if pending is None:
            self.check_knowledge()
            entry = next((entry for entry in state["claims"] if entry["item_id"] == core["item_id"]), None)
            if entry is None:
                _error("SYNC_SOURCE_EXECUTION_BLOCKED")
            original = {"kind": "budget_defer", "request_id": str(uuid.uuid4()), "source": self.source,
                "target": {"library_id": core["binding"]["target_library_id"], "node_id": core["binding"]["target_node_id"]},
                "lease": self.lease(), "claim": entry["claim"], "recovery_generation": core["recovery_generation"],
                "recovery_stream": core["recovery_stream"], "reason": deferred.reason,
                "retry_at": None if deferred.resume_at is None else _stamp(deferred.resume_at),
                "budget_operation_id": deferred.operation_id}
            pending = self.store.install_budget_defer_request(original)
        pending = self._deliver_budget_pending(pending)
        original = pending["defer_request"]
        _status(self.store, "budget_wait", retry_at=original["retry_at"])
        raise deferred

    def resume_budget(self):
        """Deliver prior intent before deciding whether this original wait can end."""
        state = self.store.read()
        pending = state.get("budget_pending")
        if pending is None:
            return False
        core = state["source_execution"]
        self._budget_owner(core)
        pending = self._deliver_budget_pending(pending)
        original = pending["defer_request"]
        retry = None if original["retry_at"] is None else sync_queue._stored_time(original["retry_at"])
        due = retry is not None and _now() >= retry
        approved = tasks.sync_resume_authorized(core["root_job_id"], original["request_id"], profile=self.storage_profile)
        if self.source["handshake"]["migration_version"] == 17:
            from . import sync_source_lifecycle17 as original17
            if pending["resume_request"] is None:
                if not due and not approved:
                    _status(self.store, "budget_wait", retry_at=original["retry_at"])
                    raise BudgetDeferred(original["reason"], resume_at=retry, operation_id=original["budget_operation_id"])
                ref = original17.freeze_budget_resume17_locked(profile=self.storage_profile, store=self.store,
                    client=self.client)
                original17.install_control17_locked(profile=self.storage_profile, store=self.store,
                    request_id=ref["record_id"])
                pending = self._deliver_budget_pending(self.store.read()["budget_pending"])
            original17.complete_terminal17_locked(profile=self.storage_profile, store=self.store, kind="budget_resumed")
            return True
        if pending["resume_request"] is None:
            if not due and not approved:
                _status(self.store, "budget_wait", retry_at=original["retry_at"])
                raise BudgetDeferred(original["reason"], resume_at=retry, operation_id=original["budget_operation_id"])
            self.check_knowledge()
            StateStore._assert_source_fence(self.store.read(), core)
            followup = {"kind": "budget_resume", "request_id": str(uuid.uuid4()), "source": self.source,
                "target": original["target"], "lease": self.lease(), "recovery_generation": core["recovery_generation"],
                "recovery_stream": core["recovery_stream"], "defer_request_id": original["request_id"]}
            pending = self.store.install_budget_resume_request(followup)
            pending = self._deliver_budget_pending(pending)
        self.store.complete_budget_resume(core, pending)
        return True

    def _source_control_blocked(self):
        _status(self.store, "source_blocked", error="SYNC_SOURCE_EXECUTION_BLOCKED")
        return BudgetDeferred("indeterminate")

    def _source_control_delivery_wait(self, pending):
        retry = _now() + timedelta(seconds=self.options["interval"])
        _status(self.store, "pending_delivery", error="SYNC_SOURCE_CONTROL_DELIVERY_PENDING", retry_at=_stamp(retry))
        return BudgetDeferred("indeterminate", resume_at=retry,
                              operation_id=pending["request"]["failure"]["operation_id"])

    def _deliver_source_control(self, binding, pending):
        """Replay only original failure intent; a stored receipt needs no RPC."""
        from . import source_terminal
        core = binding.core
        if self.source["handshake"]["migration_version"] == 17:
            from . import sync_source_lifecycle17 as original17
            self._budget_owner(core)
            original17.deliver_registered_control17(profile=self.storage_profile, store=self.store,
                client=self.client, request_id=pending["request"]["request_id"])
            original17.record_control_receipt17_locked(profile=self.storage_profile, store=self.store,
                request_id=pending["request"]["request_id"])
            closed = original17.complete_terminal17_locked(profile=self.storage_profile, store=self.store,
                kind="source_failed")
            if closed.get("source_execution") is not None:
                raise self._source_control_blocked()
            _status(self.store, "source_failed", error="SYNC_SOURCE_FAILED")
            raise _SourceFailed()
        try:
            self._budget_owner(core)
            proved = source_terminal.revalidate_failure(binding, pending["request"]["failure"])
            if proved.core_sha256 != pending["core_sha256"]:
                _error("SYNC_SOURCE_FAILURE_UNPROVED")
        except BudgetDeferred:
            _status(self.store, "source_blocked", error="SYNC_SOURCE_EXECUTION_BLOCKED")
            raise
        except Exception as exc:
            if isinstance(exc, SyncError) and exc.code in {"SYNC_CANCELLED", "SYNC_CLIENT_CANCELLED"}:
                raise
            raise self._source_control_blocked() from None
        if pending["receipt"] is None:
            original = pending["request"]
            try:
                # No fresh source admission or mutable lease replacement is
                # allowed between the persisted original body and its replay.
                self._budget_owner(core)
                receipt = self.client.source_control(original)
                self._budget_owner(core)
                pending = self.store.record_source_control_receipt(original["request_id"], receipt)
            except Exception as exc:
                if isinstance(exc, SyncError) and exc.code in {"SYNC_CANCELLED", "SYNC_CLIENT_CANCELLED"}:
                    raise
                raise self._source_control_delivery_wait(pending) from None
        try:
            # A public typed receipt alone cannot close a changed local paid
            # fact, worker owner, restore fence or independent pending root.
            self._budget_owner(core)
            source_terminal.revalidate_failure(binding, pending["request"]["failure"])
            closed = self.store.complete_source_failure(core, pending,
                effect_guard=lambda: source_terminal.failure_clear_guard(binding, pending["request"]["failure"]))
            if closed.get("source_execution") is not None or closed.get("source_control_pending") is not None:
                raise self._source_control_blocked()
        except BudgetDeferred:
            raise
        except Exception as exc:
            if isinstance(exc, SyncError) and exc.code in {"SYNC_CANCELLED", "SYNC_CLIENT_CANCELLED"}:
                raise
            raise self._source_control_blocked() from None
        _status(self.store, "source_failed", error="SYNC_SOURCE_FAILED")
        raise _SourceFailed()

    def fail_source(self, binding, proof):
        """Freeze one real settled source failure before releasing its claim."""
        from . import source_terminal
        if self.source["handshake"]["migration_version"] == 17:
            from . import sync_source_lifecycle17 as original17
            self._budget_owner(binding.core)
            proved = source_terminal.revalidate_failure(binding, proof)
            ref = original17.freeze_source_failure17_locked(profile=self.storage_profile, store=self.store,
                client=self.client, attempt_id=proved.public()["attempt_id"])
            original17.install_control17_locked(profile=self.storage_profile, store=self.store,
                request_id=ref["record_id"])
            self._deliver_source_control(binding, self.store.read()["source_control_pending"])
        try:
            core = binding.core
            self._budget_owner(core)
            self.store.assert_source_execution(core)
            proved = source_terminal.revalidate_failure(binding, proof)
            state = self.store.read()
            if proved.core_sha256 != hashlib.sha256(binding.core_json.encode("utf-8")).hexdigest():
                _error("SYNC_SOURCE_FAILURE_UNPROVED")
            pending = state.get("source_control_pending")
            if pending is None:
                self.check_knowledge()
                StateStore._assert_source_fence(state, core)
                entry = next((entry for entry in state["claims"] if entry["item_id"] == core["item_id"]), None)
                if entry is None:
                    _error("SYNC_SOURCE_FAILURE_UNPROVED")
                original = {"kind": "source_fail", "request_id": str(uuid.uuid4()), "source": self.source,
                    "target": {"library_id": core["binding"]["target_library_id"], "node_id": core["binding"]["target_node_id"]},
                    "lease": self.lease(), "claim": entry["claim"], "recovery_generation": core["recovery_generation"],
                    "recovery_stream": core["recovery_stream"], "core_sha256": proved.core_sha256,
                    "submitted_url_sha256": core["submitted_url_sha256"], "failure": proved.public()}
                self.store.enable_source_control()
                pending = self.store.install_source_control_request(original)
            elif pending["request"]["failure"] != proved.public():
                _error("SYNC_SOURCE_FAILURE_UNPROVED")
        except BudgetDeferred:
            _status(self.store, "source_blocked", error="SYNC_SOURCE_EXECUTION_BLOCKED")
            raise
        except Exception as exc:
            if isinstance(exc, SyncError) and exc.code in {"SYNC_CANCELLED", "SYNC_CLIENT_CANCELLED"}:
                raise
            raise self._source_control_blocked() from None
        self._deliver_source_control(binding, pending)

    def resume_source_control(self):
        """Restore only original paid authority before any new source work."""
        pending = self.store.read().get("source_control_pending")
        if pending is None:
            return False
        if self.source["handshake"]["migration_version"] == 17:
            from .sync_source_binding17 import capture_home_binding_locked
            core = self.store.read()["source_execution"]
            binding = capture_home_binding_locked(profile=self.storage_profile, store=self.store,
                item_id=core["item_id"])
            self._deliver_source_control(binding, pending)
        from .sync_source_execution import historical_binding
        try:
            binding = historical_binding(self.store, self.storage_profile)
        except BudgetDeferred:
            _status(self.store, "source_blocked", error="SYNC_SOURCE_EXECUTION_BLOCKED")
            raise
        except Exception as exc:
            if isinstance(exc, SyncError) and exc.code in {"SYNC_CANCELLED", "SYNC_CLIENT_CANCELLED"}:
                raise
            raise self._source_control_blocked() from None
        self._deliver_source_control(binding, pending)

    def resume_completed_source_failure(self):
        """Recover original job completion after state clear and before finish."""
        current, profile = worker.execution_context(), worker._execution_profile()
        if current is None or current["kind"] != "sync" or profile is None:
            return False
        state = self.store.read()
        if state.get("source_execution") is not None:
            return False
        from . import source_terminal, sync_source_execution
        try:
            if profile.identity != self.storage_profile.identity or not tasks.holds_process_lock():
                _error("SYNC_SOURCE_EXECUTION_BLOCKED")
            history = self.store.completed_source_failure(root_job_id=current["job_id"],
                continuation_id=current["continuation_id"], execution_generation=current["execution_generation"])
            if history is None:
                published = self.store.completed_source_publication(root_job_id=current["job_id"],
                    continuation_id=current["continuation_id"], execution_generation=current["execution_generation"])
                if published is not None:
                    self.resume_completed_source_publication(published)
                # Lost terminal history cannot silently permit another source
                # under this same paid continuation. A genuinely new job has
                # no old Home operation in its own original namespace.
                with self.storage_connection() as con:
                    paid = con.execute("SELECT 1 FROM budget_operation WHERE library_id=? AND root_job_id=? "
                        "AND continuation_id=? AND execution_generation=? " + (
                        "LIMIT 1" if self.source["handshake"]["migration_version"] == 17 else
                        "AND substr(suboperation_key,1,5)='home:' LIMIT 1"),
                        (self.source["library_id"], current["job_id"], current["continuation_id"],
                         current["execution_generation"])).fetchone()
                if paid is not None:
                    raise self._source_control_blocked()
                return False
            if self.source["handshake"]["migration_version"] == 17:
                from .sync_source_binding17 import capture_completed_home_binding_locked
                binding = capture_completed_home_binding_locked(profile=self.storage_profile, store=self.store,
                    root_job_id=current["job_id"], continuation_id=current["continuation_id"],
                    execution_generation=current["execution_generation"], terminal_kind="source_failed")
            else:
                binding = sync_source_execution.completed_binding(self.store, self.storage_profile, history)
            self._budget_owner(binding.core)
            source_terminal.revalidate_failure(binding, history["source_control_pending"]["request"]["failure"])
        except BudgetDeferred:
            _status(self.store, "source_blocked", error="SYNC_SOURCE_EXECUTION_BLOCKED")
            raise
        except _SourcePublished:
            raise
        except Exception as exc:
            if isinstance(exc, SyncError) and exc.code in {"SYNC_CANCELLED", "SYNC_CLIENT_CANCELLED"}:
                raise
            raise self._source_control_blocked() from None
        _status(self.store, "source_failed", error="SYNC_SOURCE_FAILED")
        raise _SourceFailed()

    def resume_completed_source_publication(self, history):
        """Consume only the original permanent publication and exact local ack."""
        from . import sync_source_execution
        from .sync_transfer import publication_payload_sha256
        if self.source["handshake"]["migration_version"] == 17:
            from .sync_source_binding17 import capture_completed_home_binding_locked
            selectors = history["source_execution"]
            binding = capture_completed_home_binding_locked(profile=self.storage_profile, store=self.store,
                root_job_id=selectors["root_job_id"], continuation_id=selectors["continuation_id"],
                execution_generation=selectors["execution_generation"], terminal_kind="source_published")
        else:
            binding = sync_source_execution.completed_binding(self.store, self.storage_profile, history)
        self._budget_owner(binding.core)
        proof = history["source_terminal"]["proof"]
        with self.store.reference_lifetime():
            request = self.store.no_resend_request(proof["request"]["operation_id"])
            response = self.store.no_resend_receipt(request["operation_id"])
            if request != proof["request"] or response != proof["response"]:
                _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
            envelope = self.store.read_publication(request["metadata"], proved_stream=request["binding"]["recovery_stream"])
            if (envelope["claims"] != proof["claims"]
                    or publication_payload_sha256(envelope["snapshot"], envelope["claims"],
                        recovery_generation=envelope["recovery_generation"], recovery_stream=envelope["recovery_stream"])
                        != request["payload_sha256"]):
                _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
            current = self.store.read()
            if current["pending_publication"] == request["metadata"]:
                if (not self._complete_published(request)
                        and self.source["handshake"]["migration_version"] != 17):
                    _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
            if self.source["handshake"]["migration_version"] == 17:
                # A stored original terminal closes this job even after a new
                # epoch/stream. Only its exact old pointer may be detached;
                # unrelated pending roots and new counters keep their values.
                if self.store.read()["pending_publication"] == request["metadata"]:
                    _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
                raise _SourcePublished()
            elif current["pending_publication"] is not None:
                _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
            current = self.store.read()
            if (current.get("source_execution") is not None or current.get("source_control_pending") is not None
                    or current["budget_pending"] is not None or current["pending_publication"] is not None
                    or current["published_version"] != envelope["snapshot"]["version"]
                    or current["published_content_sha256"] != _content_digest(envelope["snapshot"])
                    or current["published_reports_sha256"] != _reports_digest(envelope["snapshot"]["reports"])):
                _error("SYNC_STATE_SOURCE_TERMINAL_CONFLICT")
        _status(self.store, "published", success=True)
        raise _SourcePublished()

    def _resume_recovery17(self):
        """Existing original recovery delivery precedes any current readmit."""
        from . import sync_source_lifecycle17 as original17
        pending = self.store.read().get("source_recovery_pending")
        if pending is None:
            return False
        original17.deliver_recovery17(profile=self.storage_profile, store=self.store, client=self.client,
            request_id=pending["request_id"])
        original17.reconcile_recovery17_locked(profile=self.storage_profile, store=self.store,
            request_id=pending["request_id"])
        return True

    def _readmit_source17(self):
        """The same active core receives a targeted claim; ordinary pull is0."""
        from . import sync_source_lifecycle17 as original17
        self._resume_recovery17()
        state = self.store.read()
        if state.get("source_execution") is None:
            return False
        if any(state.get(name) is not None for name in
                ("source_control_pending", "budget_pending", "pending_publication")):
            raise self._source_control_blocked()
        self.client.source_continuity(self.source, {
            "library_id": self.options["sync_target_library_id"], "node_id": self.options["sync_target_node_id"]})
        intent = original17.create_current_readmit17_locked(profile=self.storage_profile,
            store=self.store, client=self.client)
        original17.install_recovery_pending17_locked(profile=self.storage_profile, store=self.store,
            request_id=intent["record"]["record_id"])
        self._resume_recovery17()
        self._fresh_source_claim = True
        return True

    def _observe_source17(self):
        """Acquire missing real observations without replacing an existing pull."""
        broker = self.client._source_claim_broker
        with broker.lock:
            missing_heartbeat = broker.latest.get("heartbeat") is None
            missing_pull = broker.latest.get("pull") is None
        if missing_heartbeat:
            self.acquire()
        if missing_pull and not self.pull_events(claim_limit=0):
            _error("SYNC_SOURCE_CURRENT_OBSERVATION_REQUIRED")
        self.client.source_continuity(self.source, {
            "library_id": self.options["sync_target_library_id"],
            "node_id": self.options["sync_target_node_id"]})

    def _handle_opportunity17(self, required):
        """Consume the original sealed signal after every proof owner exits."""
        state = self.store.read()
        core = state.get("source_execution")
        if self.source["handshake"]["migration_version"] != 17 or core is None:
            _error("SYNC_SOURCE_CURRENT_OBSERVATION_REQUIRED")
        action = consume_required17(required, profile=self.storage_profile, store=self.store,
            client=self.client, item_id=core["item_id"])
        if action in self._opportunity_actions17:
            _error("SYNC_SOURCE_CURRENT_OBSERVATION_REQUIRED")
        self._opportunity_actions17.add(action)
        self.check_knowledge()
        if action == "observe":
            self._observe_source17()
        elif action == "readmit":
            self._readmit_source17()
        else:
            _error("SYNC_SOURCE_CURRENT_OBSERVATION_REQUIRED")
        return action

    def _process_current_claim17(self):
        """Retry the same business through paid lookup before new admission."""
        while True:
            try:
                return self.process_claim()
            except ReadmissionRequired17 as required:
                self._handle_opportunity17(required)

    def _publish_current_claim17(self, bindings):
        """Keep frozen work while a genuinely new publication needs admission."""
        while True:
            try:
                if self._resume_frozen_publication17() is not None:
                    self.publish()
                    return
                state = self.store.read()
                # A claim forces publication, but cannot skip version detection.
                changed = self.needs_publication()
                self.check_knowledge()
                if bindings or changed:
                    report.run_all()  # Static only; AI summary remains explicit.
                    if state["work"] is not None:
                        work = state["work"]
                        work["phase"] = "reported"
                        self.store.update({"work": work})
                    self.freeze(bindings)
                    self.publish()
                return
            except ReadmissionRequired17 as required:
                action = self._handle_opportunity17(required)
                if action == "readmit":
                    # The new claim is current; source/model paid namespaces
                    # and the saved mapping instance still belong to this core.
                    bindings = self._process_current_claim17()

    def process_claim(self):
        state = self.store.read()
        if not state["claims"]:
            return []
        entry = state["claims"][0]
        claim = entry["claim"]
        work = state["work"]
        _status(self.store, "processing")
        self.check_knowledge()
        if self.source["handshake"]["migration_version"] > 17:
            _status(self.store, "source_blocked", error="SYNC_SOURCE_EXECUTION_BLOCKED")
            raise BudgetDeferred("unsupported_capability")
        if self.source["handshake"]["migration_version"] == 17 and type(self.client) is not SyncClient:
            raise BudgetDeferred("unsupported_capability")
        proof = None
        source_phase = None
        try:
            from . import ingest, source_budget, source_resolution_budget, sync_source_execution, sync_sources
            prospective17 = self.source["handshake"]["migration_version"] == 17
            strict = prospective17 or config.budget_settings()["budget_enabled"] or state.get("source_execution") is not None
            if prospective17:
                from . import sync_source_genesis17, sync_source_binding17
                if state.get("source_execution") is None:
                    # Before an original identity exists, genuine claim origin
                    # creation needs this real current continuity observation.
                    self.client.source_continuity(self.source, {
                        "library_id": self.options["sync_target_library_id"],
                        "node_id": self.options["sync_target_node_id"]})
                    sync_source_genesis17.create_initial_core(profile=self.storage_profile,
                        store=self.store, client=self.client, item_id=entry["item_id"])
                proof = sync_source_binding17.capture_home_binding_locked(profile=self.storage_profile,
                    store=self.store, item_id=entry["item_id"])
            elif strict:
                if self._fresh_source_claim is False:
                    waiting = BudgetDeferred("download_pacing", resume_at=_now() + timedelta(seconds=self.options["interval"]))
                    _status(self.store, "source_blocked", error="SYNC_SOURCE_EXECUTION_BLOCKED", retry_at=_stamp(waiting.resume_at))
                    raise waiting
                proof = sync_source_execution.bind_execution(self.store, self.storage_profile, entry,
                    recovery_generation=self.knowledge_generation, recovery_stream=self.knowledge_stream)
            if work is None:
                if proof is None:
                    resolution = sync_sources.resolve_url(entry["url"], check_cancelled=self.check_knowledge)
                    result = ingest.ingest_url(resolution["resolved_url"], enqueue_process=False,
                        metadata_validator=lambda info: sync_sources.validate_metadata(resolution, info))
                else:
                    source_phase = "resolution"
                    resolution = source_resolution_budget.resolve_url(proof, client=self.client,
                        check_cancelled=self.check_knowledge)
                    source_phase = None
                    # Source resolution keeps its own dispatcher; metadata and
                    # media retain the original logical source inside this scope.
                    with source_budget.home_source_scope(proof, client=self.client, check_cancelled=self.check_knowledge):
                        source_phase = "ingest"
                        result = ingest.ingest_url(resolution["resolved_url"], enqueue_process=False,
                            metadata_validator=lambda info: sync_sources.validate_metadata(resolution, info))
                        source_phase = None
                    refreshed = source_resolution_budget.completed_resolution(proof, generation=1)
                    if refreshed is not None:
                        resolution = refreshed.resolution
                video_id = result["video_id"]
                work = {"item_id": entry["item_id"], "lease_generation": claim["lease_generation"],
                        "video_id": video_id, "phase": "downloaded", "source_mapping_generation": str(uuid.uuid4()),
                        "source_mapping_inputs": {"resolution": resolution, "extracted_video_id": result["extracted_video_id"],
                            "media_sha256": result["extracted_media_sha256"], "recorded_at": sync_sources._now()}}
                # Persist the generation and all immutable reconstruction inputs
                # before map SHA/publication. The whole installation stays SH.
                with self.store.reference_lifetime():
                    self.store.enable_no_resend()
                    self.store.update({"work": work})
                    self.ensure_work_mapping(work, entry)
            else:
                with self.store.reference_lifetime():
                    self.ensure_work_mapping(work, entry)
            if prospective17:
                # ModelScope consumes this exact Home binding/client while the
                # original model key, request, result and paid identity remain.
                with source_budget.home_source_scope(proof, client=self.client, check_cancelled=self.check_knowledge):
                    self.advance_work(work)
            else:
                self.advance_work(work)
            video_id = work["video_id"]
            # Contract extension supplies an immutable resolution statement; the
            # paired home, not the cloud, is the authority that observed redirects.
            source_map = sync_sources.SourceMap(self.home_data, profile=self.storage_profile,
                                               source_library_id=self.source["library_id"],
                                               source_node_id=self.source["node_id"])
            origin = (source_map.get(work["source_mapping_id"], mapping_generation=work.get("source_mapping_generation"))
                      if work.get("source_mapping_id") else None)
            if origin is None:
                _error("SYNC_SOURCE_MAPPING_MISSING")
            return [{"video_id": video_id, "claim": claim, "origin": origin}]
        except ReadmissionRequired17:
            raise
        except BudgetDeferred as deferred:
            # The original worker job owns this wait; it is not a cloud failure
            # or permission to retire this claim's saved local progress.
            self.defer_budget(deferred)
        except Exception as exc:
            code = exc.code if isinstance(exc, SyncError) else "SYNC_PROCESSING_FAILED"
            normal_source_failure = (proof is not None and (
                source_phase == "ingest" and isinstance(exc, source_budget.SourceProcessError)
                or source_phase == "resolution" and isinstance(exc, SyncError)
                and code in {"SYNC_SOURCE_INVALID", "SYNC_SOURCE_UNAVAILABLE", "SYNC_SOURCE_RATE_LIMITED", "SYNC_SOURCE_NETWORK"}))
            if normal_source_failure:
                from . import source_terminal
                try:
                    failure = source_terminal.known_failure(proof)
                except BudgetDeferred:
                    _status(self.store, "source_blocked", error="SYNC_SOURCE_EXECUTION_BLOCKED")
                    raise
                except Exception:
                    raise self._source_control_blocked() from None
                if failure is not None:
                    self.fail_source(proof, failure)
            # A local proof/pipeline failure is not a decision to discard this
            # work. Losing its generation here would let an ordinary retry
            # silently invent a new mapping instance and bypass retirement.
            current_work = self.store.read()["work"]
            if self.store.read().get("source_execution") is not None:
                _status(self.store, "source_blocked", error="SYNC_SOURCE_EXECUTION_BLOCKED")
                raise BudgetDeferred("indeterminate") from None
            if (code == "SYNC_NO_RESEND_REFERENCE_RETIRED"
                    or current_work is not None and current_work.get("source_mapping_generation") is not None):
                raise
            if (code in _RECOVERY_ERRORS | {"SYNC_CANCELLED", "SYNC_CLIENT_CANCELLED", "SYNC_HEARTBEAT_LOST", "SYNC_LEASE_EXPIRED"}
                    or self.lost.is_set() or self.stop_event.is_set()):
                raise
            # A hold observed while a lower-level operation failed still fences
            # knowledge work. Do not turn that operation into a queue failure.
            self.check_knowledge()
            error_class = "processing_failed"
            if code in {"SYNC_SOURCE_NETWORK", "SYNC_SOURCE_TIMEOUT"}:
                error_class = "network"
            elif code == "SYNC_SOURCE_RATE_LIMITED":
                error_class = "rate_limited"
            elif code == "SYNC_SOURCE_UNAVAILABLE":
                error_class = "source_unavailable"
            elif code == "SYNC_SOURCE_MEDIA_MISMATCH":
                error_class = "media_invalid"
            elif code == "SYNC_SOURCE_MAP_STORAGE":
                error_class = "processing_failed"
            elif code == "INVALID_SYNC_REQUEST" or code.startswith("SYNC_SOURCE_"):
                error_class = "invalid_url"
            self.client.fail(self.source, self.lease(), claim, error_class,
                             recovery_generation=self.knowledge_generation, recovery_stream=self.knowledge_stream)
            self.store.retire_progress(recovery_generation=self.knowledge_generation, recovery_stream=self.knowledge_stream)
            raise

    def execute(self):
        self.resume_completed_source_failure()
        self.acquire()
        with self.heartbeat():
            pulled = self.pull_events(claim_limit=0)
            recovery = self.recovery()
            self.knowledge_generation = recovery["generation"]
            self.knowledge_stream = recovery["stream"]
            if recovery["state"] == "held":
                self.push_events()
                # P05 explicit no-resend authorizations remain deliverable on
                # a held receiver. Reviews precede these original controls;
                # source/budget dispatch and content publication stay closed.
                self.deliver_no_resend()
                _status(self.store, "recovery_held")
                return {"state": "recovery_held"}
            if not pulled:
                _status(self.store, "review_backlog")
                return {"state": "review_backlog"}
            state = self.store.read()
            if (recovery["generation"] > state["applied_recovery_generation"]
                    or state["recovery_attempt"] is not None
                    or recovery["state"] == "resume_pending" and state["published_version"] is None):
                if not self.push_events():
                    _status(self.store, "recovery_resuming")
                    return {"state": "recovery_resuming"}
                self.resume_publication()
                # A lost acknowledgement retries the frozen new generation.
                # A later control generation must not be cleared by this ack.
                self.acquire()
                observed = self.recovery()
                result = "recovery_held" if observed["state"] == "held" else (
                    "recovery_resuming" if observed["generation"] > self.store.read()["applied_recovery_generation"]
                    or observed["state"] == "resume_pending" else "idle")
                _status(self.store, result, success=True)
                return {"state": result, "recovery_bootstrapped": True}
            if recovery["state"] == "resume_pending":
                # The exact bootstrap was already acknowledged. A delayed
                # control observation cannot invent another publication UUID.
                self.push_events()
                _status(self.store, "recovery_resuming")
                return {"state": "recovery_resuming"}
            if (self.source["handshake"]["migration_version"] == 17
                    and self.store.read().get("source_execution") is not None):
                self._resume_recovery17()
            self.resume_source_control()
            self.resume_budget()
            self.deliver_no_resend()
            # Retry frozen payloads before doing any new processing. Lost replies
            # cannot invent a fresh UUID or change the original task claims.
            self.publish()
            if not self.push_events() or not self.pull_events(claim_limit=1):
                _status(self.store, "review_backlog")
                return {"state": "review_backlog"}
            self.check_knowledge()
            bindings = self._process_current_claim17()
            if not self.push_events():
                _status(self.store, "review_backlog")
                return {"state": "review_backlog"}
            try:
                self._publish_current_claim17(bindings)
            except (_SourceFailed, _SourcePublished):
                raise
            except BudgetDeferred as deferred:
                self.defer_budget(deferred)
            except Exception:
                # Local report/export failures cannot hold a claim forever.
                # Frozen network retries retain their original payload/claims.
                self.check_knowledge()
                current = self.store.read()
                if current.get("source_execution") is not None:
                    _status(self.store, "source_blocked", error="SYNC_SOURCE_EXECUTION_BLOCKED")
                    raise BudgetDeferred("indeterminate") from None
                if (current["pending_publication"] is None and current["claims"]
                        and not (current["work"] is not None and current["work"].get("source_mapping_generation") is not None)):
                    self.client.fail(self.source, self.lease(), current["claims"][0]["claim"], "processing_failed",
                                     recovery_generation=self.knowledge_generation, recovery_stream=self.knowledge_stream)
                    self.store.retire_progress(recovery_generation=self.knowledge_generation, recovery_stream=self.knowledge_stream)
                raise
            self.check_knowledge()
            _status(self.store, "idle", success=True)
            return {"state": "idle", "published": self.store.read()["published_version"] is not None}

    def execute_reviews(self):
        """Exchange reviews/static bootstrap without admitting the waiting job."""
        original = self.store.read()["status"]
        self.acquire()
        with self.heartbeat():
            pulled = self.pull_events(claim_limit=0)
            pushed = self.push_events()
            recovery = self.recovery()
            state = self.store.read()
            bootstrapped = False
            if (pulled and pushed and recovery["state"] != "held" and (
                    recovery["generation"] > state["applied_recovery_generation"] or state["recovery_attempt"] is not None
                    or recovery["state"] == "resume_pending" and state["published_version"] is None)):
                self.resume_publication()
                self.acquire()
                bootstrapped = True
        _status(self.store, original["state"], error=original["error_code"], retry_at=original["retry_at"])
        result = {"state": original["state"], "reviews_synced": bool(pulled and pushed)}
        if bootstrapped:
            result["recovery_bootstrapped"] = True
        return result


def resume_no_resend(operation_id, *, stop_event=None, client=None):
    """Deliver one previously committed request, even with sync disabled.

    This entry owns the existing worker and StateStore process locks. It never
    initializes state, resets epochs, pulls claims/events or publishes content.
    """
    if not tasks.holds_process_lock():
        _error("SYNC_WORKER_LOCK_REQUIRED")
    try:
        sync_queue._uuid(operation_id, "operation_id")
    except (ValueError, TypeError, AttributeError):
        _error("SYNC_NO_RESEND_REQUEST_NOT_PENDING")
    options = settings(delivery_only=True)
    source, _ = _local()
    requested_stop = stop_event or threading.Event()
    database = config.db_path().resolve()
    home_data = config.DATA.resolve()

    def check_cancelled():
        if (requested_stop.is_set() or config.db_path().resolve() != database
                or config.DATA.resolve() != home_data):
            _error("SYNC_CANCELLED")
        _assert_peer_configuration(options)

    client = client or SyncClient(options["url"], token_provider=lambda: _sender_token(options),
                                  check_cancelled=check_cancelled)
    store = StateStore(home_data, profile=storage_admin.profile(), check_cancelled=check_cancelled)
    with store.exclusive():
        state_binding = store.read()["binding"]
        expected = {"home_library_id": source["library_id"], "home_node_id": source["node_id"],
                    "target_library_id": options["sync_target_library_id"],
                    "target_node_id": options["sync_target_node_id"], "target_url": options["url"]}
        if state_binding != expected:
            _error("SYNC_PEER_MISMATCH")
        # A reserved catalog slot is already a committed authorization to stop
        # sending. Repair only its original immutable evidence before delivery.
        store.resume_no_resend_commit(operation_id)
        request = store.no_resend_request(operation_id)
        if any(request[key] != expected[key] for key in expected if key != "target_url"):
            _error("SYNC_PEER_MISMATCH")
        response = store.no_resend_receipt(operation_id)
        if response is not None:
            if request["reason"] == "published_receipt":
                _Cycle(store, client, source, options, requested_stop)._complete_published(request)
            return {"operation_id": operation_id, "state": response["state"]}
        cycle = _Cycle(store, client, source, options, requested_stop)
        cycle.check()
        target = client.handshake()
        sync_protocol.validate_handshake(target["handshake"], source["handshake"])
        if (target["library_id"] != options["sync_target_library_id"]
                or target["node_id"] != options["sync_target_node_id"]
                or target["library_id"] == source["library_id"] or target["node_id"] == source["node_id"]):
            _error("SYNC_PEER_MISMATCH")
        cycle.acquire(delivery_only=True)
        with cycle.heartbeat(delivery_only=True):
            results = cycle.deliver_no_resend(operation_id=operation_id)
        return results[0]


def run_cycle(*, stop_event=None, client=None, review_only=False):
    """Called by worker.dispatch under the same lock as all pipeline tasks."""
    if not tasks.holds_process_lock():
        _error("SYNC_WORKER_LOCK_REQUIRED")
    if type(review_only) is not bool:
        _error("SYNC_SOURCE_EXECUTION_BLOCKED")
    options = settings()
    source, version = _local()
    requested_stop = stop_event or threading.Event()
    database = config.db_path().resolve()
    home_data = config.DATA.resolve()

    def check_cancelled():
        if (requested_stop.is_set() or config.db_path().resolve() != database
                or config.DATA.resolve() != home_data):
            _error("SYNC_CANCELLED")
        _assert_peer_configuration(options)

    def check_state_binding():
        # Cancellation still records a terminal status on this same profile.
        # Relocating the configured library must never write its old state.
        if config.db_path().resolve() != database or config.DATA.resolve() != home_data:
            _error("SYNC_CANCELLED")

    client = client or SyncClient(options["url"], token_provider=lambda: _sender_token(options),
                                  check_cancelled=check_cancelled)
    binding = {"home_library_id": source["library_id"], "home_node_id": source["node_id"],
               "target_library_id": options["sync_target_library_id"],
               "target_node_id": options["sync_target_node_id"], "target_url": options["url"]}
    store = StateStore(config.DATA, profile=storage_admin.profile(), check_cancelled=check_state_binding)
    with store.exclusive():
        try:
            before = store.read()
        except SyncError as exc:
            if exc.code != "SYNC_STATE_MISSING":
                raise
            before = None
        if review_only:
            core = None if before is None else before.get("source_execution")
            current, actual_profile = worker.execution_context(), worker._execution_profile()
            if (core is None or before["binding"] != binding or before["local_epoch"] != version["library_epoch"]
                    or current is None or current["kind"] != "sync" or actual_profile is None
                    or actual_profile.identity != store.profile.identity
                    or (current["job_id"], current["continuation_id"], current["execution_generation"]) != (
                        core["root_job_id"], core["continuation_id"], core["execution_generation"])):
                _error("SYNC_SOURCE_EXECUTION_BLOCKED")
            with storage_admin.connection(store.profile) as con:
                row = con.execute("SELECT * FROM job WHERE job_id=?", (core["root_job_id"],)).fetchone()
                if (row is None or row["status"] != "pending" or row["owner_id"] is not None
                        or (row["continuation_id"], row["execution_generation"]) != (
                            core["continuation_id"], core["execution_generation"])):
                    _error("SYNC_SOURCE_EXECUTION_BLOCKED")
                tasks._require_sync_job(row)
        else:
            store.initialize(binding, before["local_epoch"] if before is not None else version["library_epoch"])
        if not review_only and before is not None and before["local_epoch"] != version["library_epoch"]:
            # Epoch and cursor reset are ONE state replacement. A crash cannot
            # persist the new epoch while retaining the pre-restore checkpoints.
            store.archive_progress("epoch_changed")
            work_reset = ({} if before.get("source_execution") is not None
                          or before["work"] is not None and before["work"].get("source_mapping_generation") is not None
                          else {"claims": [], "work": None})
            store.update({"local_epoch": version["library_epoch"], "recovery_attempt": None,
                          "inbound": dict(_EMPTY_CURSOR), "outbound": dict(_EMPTY_CURSOR),
                          "seeded": False, **work_reset,
                          **_upload_cache_reset(store, before), "published_version": None, "published_content_sha256": None,
                          "published_reports_sha256": None})
        cycle = _Cycle(store, client, source, options, requested_stop)
        try:
            cycle.check()
            if not review_only:
                cycle.resume_completed_source_failure()
            target = client.handshake()
            sync_protocol.validate_handshake(target["handshake"], source["handshake"])
            if (target["library_id"] != options["sync_target_library_id"]
                    or target["node_id"] != options["sync_target_node_id"]
                    or target["library_id"] == source["library_id"] or target["node_id"] == source["node_id"]):
                _error("SYNC_PEER_MISMATCH")
            result = cycle.execute_reviews() if review_only else cycle.execute()
            if not review_only:
                current = store.read()
                core = current.get("source_execution")
                if core is not None:
                    pending = current["budget_pending"]
                    if pending is not None:
                        if (result.get("recovery_bootstrapped") and before is not None
                                and before.get("source_execution") == core and before.get("budget_pending") == pending):
                            original_status = before["status"]
                            _status(store, original_status["state"], error=original_status["error_code"],
                                    retry_at=original_status["retry_at"])
                        original = pending["defer_request"]
                        raise BudgetDeferred(original["reason"],
                            resume_at=None if original["retry_at"] is None else sync_queue._stored_time(original["retry_at"]),
                            operation_id=original["budget_operation_id"])
                    control = current["recovery"]
                    same_fence = (current["local_epoch"] == core["local_epoch"] and control is not None
                        and control["state"] == "running"
                        and (control["generation"], control["stream"]) == (core["recovery_generation"], core["recovery_stream"]))
                    retry = _now() + timedelta(seconds=options["interval"]) if same_fence else None
                    _status(store, "source_blocked", error="SYNC_SOURCE_EXECUTION_BLOCKED",
                            retry_at=None if retry is None else _stamp(retry))
                    raise BudgetDeferred("download_pacing" if same_fence else "indeterminate", resume_at=retry)
            return result
        except (_SourceFailed, _SourcePublished) as terminal:
            current = store.read()
            if current.get("source_execution") is not None or current.get("source_control_pending") is not None:
                raise cycle._source_control_blocked()
            if isinstance(terminal, _SourcePublished):
                if current["pending_publication"] is not None:
                    raise cycle._source_control_blocked()
                _status(store, "published", success=True)
                return {"state": "published", "published": True}
            _status(store, "source_failed", error="SYNC_SOURCE_FAILED")
            return {"state": "source_failed", "error_code": "SYNC_SOURCE_FAILED"}
        except BudgetDeferred:
            # Keep the exact exception for worker.tasks.defer, so a later owner
            # resumes this same continuation rather than finishing the cycle.
            raise
        except Exception as exc:
            code = exc.code if isinstance(exc, SyncError) else "SYNC_PROCESSING_FAILED"
            state = "waiting_lease" if code in {"SYNC_SESSION_BUSY", "SYNC_LEASE_MISMATCH"} else "retry_wait"
            current = store.read()
            if review_only:
                original = before["status"]
                _status(store, original["state"], error=original["error_code"], retry_at=original["retry_at"])
                return {"state": original["state"], "reviews_synced": False,
                        "error_code": "SYNC_REVIEW_WAIT_SYNC_FAILED"}
            if current.get("source_execution") is not None:
                _status(store, "source_blocked", error="SYNC_SOURCE_EXECUTION_BLOCKED")
                raise BudgetDeferred("indeterminate") from None
            recovery = current["recovery"]
            if recovery and recovery["state"] == "held" or code == "SYNC_RECOVERY_HELD":
                state = "recovery_held"
            elif (code in _RECOVERY_ERRORS or code.startswith("SYNC_RECOVERY_")
                    or current["recovery_attempt"] is not None or recovery and recovery["state"] == "resume_pending"):
                state = "recovery_resuming" if code in {"SYNC_CLIENT_NETWORK", "SYNC_CLIENT_TIMEOUT"} else "recovery_blocked"
            _status(store, state, error=code,
                    retry_at=_stamp(_now() + timedelta(seconds=options["interval"])))
            # Only closed public codes escape into worker results, no provider
            # errors, paths, download URLs or credential-bearing exception text.
            return {"state": state, "error_code": code}
