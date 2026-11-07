"""Publish a complete knowledge generation after immutable file preparation.

The receiving database is the visibility boundary. Domain methods participate
in the caller's transaction and never commit it. An orphaned immutable file
after a failed transaction cannot expose half a knowledge generation.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import hashlib
import json
from pathlib import Path
import sqlite3

from . import config, db, review, sync_protocol as protocol, sync_queue as queue, sync_reports
from .sync_protocol import SyncError
from . import storage_admin, storage_artifacts, storage_lifecycle, sync_upload_pins as pins


def _receiver():
    if not config.is_cloud():
        raise SyncError("Knowledge publication requires a cloud receiver", "SYNC_ROLE_REQUIRED", 403)


def _admit(con, source, lease, now):
    _receiver()
    # Read-only admission before reading bytes. Final authorization is always
    # repeated after acquiring write ownership and after the expensive work.
    current, _ = queue._clock(now)
    queue._session(con, source, lease, current)


def _profile(con, profile=None, media_store=None, report_root=None):
    value = storage_admin.from_connection(con, profile=profile,
        media=media_store.root if media_store is not None else None, reports=report_root)
    storage_lifecycle.require_guard(value)
    return value


def _intent_source(source, binding):
    value = pins.normalize_binding(binding)
    if value["source_node_id"] != source["node_id"] or value["source_version"]["library_id"] != source["library_id"]:
        raise SyncError("Upload intent belongs to another source", "SYNC_PEER_MISMATCH", 409)
    return value


def prepare_uploads(con, source, lease, *, binding, descriptors, profile=None, now=None):
    profile = _profile(con, profile)
    _admit(con, source, lease, now)
    binding = _intent_source(source, binding)
    with queue._operation(con):
        result = pins.prepare_publication(con, binding=binding, descriptors=descriptors, profile=profile)
        queue.require_session(con, source, lease, now=now)
        return result


def confirm_cached_upload(con, source, lease, intent, *, profile=None, now=None, check_cancelled=None):
    """Only previously registered files can satisfy a no-upload cache hit."""
    profile = _profile(con, profile)
    value = pins.normalize_intent(intent)
    _intent_source(source, {k: value[k] for k in pins.BINDING_FIELDS})
    _admit(con, source, lease, now)
    pins.require_prepared(con, value)
    descriptor = value["descriptor"]
    key = f"sync/{descriptor['video_id']}/{descriptor['sha256']}.{descriptor['extension']}"
    root_id = storage_artifacts.root_identity(profile.media, profile=profile)["root_id"]
    row = con.execute("SELECT artifact_id FROM storage_artifact WHERE namespace='sync_media' AND root_id=? AND storage_key=?", (root_id, key)).fetchone()
    if row is None:
        return False
    try:
        proof = storage_artifacts._verify_registered(con, row[0], profile=profile, root=profile.media,
                                                     check_cancelled=check_cancelled)
        with queue._operation(con):
            _admit(con, source, lease, now)
            pins.require_prepared(con, value)
            pins._mark_stored_verified(con, value, proof, profile=profile)
            queue.require_session(con, source, lease, now=now)
    except SyncError as exc:
        if exc.code in {"STORAGE_ARTIFACT_MISSING", "STORAGE_ARTIFACT_CHANGED"}:
            return False
        raise
    return True


def reserve_upload(con, source, lease, intent, *, profile=None, now=None):
    profile = _profile(con, profile)
    value = pins.normalize_intent(intent)
    _intent_source(source, {k: value[k] for k in pins.BINDING_FIELDS})
    _admit(con, source, lease, now)
    with queue._operation(con):
        pins.require_prepared(con, value)
        result = pins.reserve(con, value, profile=profile)
        queue.require_session(con, source, lease, now=now)
        return result


def abandon_uploads(con, source, lease, *, binding, operation_id, profile=None, now=None):
    profile = _profile(con, profile)
    _admit(con, source, lease, now)
    binding = _intent_source(source, binding)
    with queue._operation(con):
        pins.abandon(con, binding=binding, operation_id=operation_id, profile=profile)
        queue.require_session(con, source, lease, now=now)
        return {"binding": binding, "operation_id": operation_id, "state": "abandoned"}


def publication_receipt(con, source, lease, *, binding, payload_sha256, profile, now=None):
    """Read an exact committed receipt, including a proven historical fence.

    This is a SELECT-only operation: a held receiver or a later recovery fence
    must not prevent proving the original publication. Its current session is
    still authenticated, and no missing original binding is inferred from the
    current fence. The caller must already hold this profile's lifetime guard.
    """
    if profile is None:
        raise storage_lifecycle.StorageLifecycleError("STORAGE_PROFILE_REQUIRED", 400)
    profile = _profile(con, profile)
    if type(payload_sha256) is not str or not protocol._SHA256.fullmatch(payload_sha256):
        raise SyncError("Publication payload hash is invalid", "INVALID_SYNC_REQUEST", 400)
    started = not con.in_transaction
    if started:
        con.execute("BEGIN")
    try:
        _admit(con, source, lease, now)
        binding = _intent_source(source, binding)
        row = pins.require_published_binding(con, binding=binding)
        if row["payload_sha256"] != payload_sha256:
            raise SyncError("Publication has another payload", "SYNC_PUBLICATION_CONFLICT", 409)
        receipt = _receipt(row, replayed=True)
        _admit(con, source, lease, now)
        result = {"binding": binding, "payload_sha256": payload_sha256, "state": "published", "receipt": receipt}
    except BaseException:
        if started:
            try:
                con.rollback()
            except BaseException:
                pass  # Cleanup must not replace the original failed proof.
        raise
    else:
        if started:
            con.rollback()
        return result


def receive_media(con, source, lease, descriptor, chunks, *, intent, profile=None,
                  media_store=None, now=None, check_cancelled=None):
    from .sync_media import MediaStore
    profile = _profile(con, profile, media_store)
    if con.in_transaction:
        raise SyncError("Commit the upload reservation before receiving bytes", "STORAGE_TRANSACTION_ACTIVE", 409)
    value = pins.normalize_intent(intent)
    _intent_source(source, {k: value[k] for k in pins.BINDING_FIELDS})
    if value["descriptor"] != protocol.validate_media_descriptor(descriptor):
        raise SyncError("Upload differs from its reserved descriptor", "SYNC_UPLOAD_INTENT_CONFLICT", 409)
    _admit(con, source, lease, now)
    pins.require_prepared(con, value)
    store = media_store or MediaStore(profile.media)
    key = store.receive(descriptor, chunks, check_cancelled=check_cancelled)
    proof = storage_artifacts._inspect_for_registration(con, profile=profile, namespace="sync_media", root=profile.media,
        storage_key=key, sha256=descriptor["sha256"], byte_size=descriptor["byte_size"], check_cancelled=check_cancelled)
    with queue._operation(con):
        _admit(con, source, lease, now)
        pins.require_prepared(con, value)
        storage_artifacts._register_verified(con, proof, profile=profile)
        pins._mark_stored_verified(con, value, proof, profile=profile)
        queue.require_session(con, source, lease, now=now)
    return {"storage_key": key, "sha256": descriptor["sha256"], "published": False}


def _bindings(value, videos):
    if value is None:
        return []
    if type(value) is not list or len(value) > queue.MAX_CLAIM_BATCH:
        raise SyncError("Invalid publication claims")
    result, seen = [], set()
    for entry in value:
        if type(entry) is not dict or set(entry) not in ({"video_id", "claim"}, {"video_id", "claim", "origin"}):
            raise SyncError("Invalid publication claim fields")
        protocol.validate_video_id(entry["video_id"])
        claim = entry["claim"]
        if (entry["video_id"] not in videos or type(claim) is not dict
                or set(claim) != {"item_id", "lease_token", "lease_generation"}):
            raise SyncError("Publication claim must reference a supplied video")
        queue._integer(claim["item_id"], "item_id")
        queue._integer(claim["lease_generation"], "lease_generation")
        queue._uuid(claim["lease_token"], "claim lease_token")
        if claim["item_id"] in seen:
            raise SyncError("Repeated publication claim")
        seen.add(claim["item_id"])
        normalized = {"video_id": entry["video_id"], "claim": dict(claim)}
        if "origin" in entry:
            from .sync_claims import validate_origin
            normalized["origin"] = validate_origin(entry["origin"])
            if normalized["origin"]["video_id"] != entry["video_id"]:
                raise SyncError("Claim origin belongs to another video", "SYNC_CLAIM_SOURCE_MISMATCH", 409)
        result.append(normalized)
    return sorted(result, key=lambda entry: entry["claim"]["item_id"])


def publication_payload_sha256(snapshot, claims, *, recovery_generation=0, recovery_stream=None):
    """Hash the publication's normalized wire payload without reading storage.

    Generation zero preserves the pre-recovery hash. Its receiver binding is
    proved separately by the publication metadata, never by adding new fields
    to this hash. Later generations explicitly include both fence fields.
    """
    from .sync_snapshot import validate_snapshot
    snapshot = validate_snapshot(snapshot)
    claims = _bindings(claims, {video["video_id"] for video in snapshot["videos"]})
    queue._integer(recovery_generation, "recovery_generation", minimum=0)
    if recovery_stream is not None:
        if type(recovery_stream) is not dict or set(recovery_stream) != {"library_id", "library_epoch"}:
            raise SyncError("Publication requires an exact receiver stream", "INVALID_SYNC_REQUEST", 400)
        queue._uuid(recovery_stream["library_id"], "stream library_id")
        queue._integer(recovery_stream["library_epoch"], "stream library_epoch")
    if recovery_generation and recovery_stream is None:
        raise SyncError("Publication requires an exact receiver stream", "INVALID_SYNC_REQUEST", 400)
    hashed_payload = {"snapshot": snapshot, "claims": claims}
    if recovery_generation != 0:
        hashed_payload["recovery_generation"] = recovery_generation
        hashed_payload["recovery_stream"] = recovery_stream
    return hashlib.sha256(protocol._json_bytes(hashed_payload)).hexdigest()


def _insert(con, table, rows):
    # Table and columns come from the fixed S01 contract, never the payload.
    columns = protocol.TABLES[table]["columns"]
    sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})"
    con.executemany(sql, [tuple(row.get(key) for key in columns) for row in rows])


def _ownership(con, snapshot):
    videos = {video["video_id"] for video in snapshot["videos"]}
    incoming = {atom["id"]: video["video_id"] for video in snapshot["videos"]
                for atom in video["tables"]["knowledge_atom"]}
    for row in con.execute("SELECT id,video_id FROM knowledge_atom"):
        if row["id"] in incoming and incoming[row["id"]] != row["video_id"]:
            raise SyncError("An existing atom belongs to another video", "SYNC_ATOM_OWNER_MISMATCH", 409)
        if row["video_id"] not in videos:
            raise SyncError("Snapshot omits a video with existing knowledge", "SYNC_INCOMPLETE_SNAPSHOT", 409)
    for video in snapshot["videos"]:
        owner = con.execute("SELECT video_id FROM video_asset WHERE content_sha256=?",
                            (video["media"]["sha256"],)).fetchone()
        if owner is not None and owner[0] != video["video_id"]:
            raise SyncError("Media content belongs to another video", "SYNC_VIDEO_OWNER_MISMATCH", 409)


def _replace_knowledge(con, snapshot, store):
    _ownership(con, snapshot)
    old_ids = {row[0] for row in con.execute("SELECT id FROM knowledge_atom")}
    for video in snapshot["videos"]:
        vid, tables = video["video_id"], video["tables"]
        # Match exact keys, not LIKE: video IDs may contain '_' wildcards.
        con.executemany("DELETE FROM meta WHERE key=?",
                        [(f"step:{vid}:{step}",) for step in protocol.STEP_NAMES])
        for table in ("evidence_ref", "knowledge_atom", "visual_observation", "ocr_span",
                      "transcript_segment", "frame"):
            con.execute(f"DELETE FROM {table} WHERE video_id=?", (vid,))
        asset = tables["video_asset"][0]
        columns = protocol.TABLES["video_asset"]["columns"]
        changes = ",".join(f"{name}=excluded.{name}" for name in columns if name != "video_id")
        con.execute(f"INSERT INTO video_asset ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
                    f"ON CONFLICT(video_id) DO UPDATE SET {changes}", tuple(asset.get(key) for key in columns))
        # config.resolve_media resolves relative to MEDIA.parent. Receiver
        # constructs this path; the sender never supplies it.
        key = (Path(store.root).name + "/" + store.storage_key(video["media"]))
        con.execute("UPDATE video_asset SET files_json=?,sync_state='ready' WHERE video_id=?",
                    (db.j({"video": key}), vid))
        for table in ("frame", "transcript_segment", "ocr_span", "visual_observation",
                      "knowledge_atom", "evidence_ref", "meta"):
            _insert(con, table, tables[table])
    for table in ("knowledge_cluster", "conflict_case"):
        con.execute(f"DELETE FROM {table}")
        _insert(con, table, snapshot["globals"][table])
    # Do not use fts_reseed(), which owns a commit and would break publication.
    new_ids = {row[0] for row in con.execute("SELECT id FROM knowledge_atom")}
    db.fts_sync_atoms(con, old_ids | new_ids)
    review.replay(con)
    db.bump_kb_revision(con)


def _receipt(row, *, replayed):
    return {"publication_id": row["publication_id"], "source_version": {
        "library_id": row["source_library_id"], "library_epoch": row["source_epoch"],
        "kb_revision": row["source_revision"]}, "local_version": json.loads(row["local_version_json"]),
        "committed_at": row["committed_at"], "replayed": replayed}


@contextmanager
def _final_authorization(con, source, lease, bindings, now, recovery_generation, source_version, recovery_stream):
    yield
    # This context exits AFTER hold_verified's final SHA pass and handle
    # cleanup, but BEFORE the write savepoint is released, including retries.
    current, _ = queue._clock(now)
    session, _ = queue._session(con, source, lease, current)
    for entry in bindings:
        queue._claim_record(con, session, entry["claim"], current)
    from . import sync_recovery
    sync_recovery.assert_publish_allowed(con, recovery_generation, source_version, recovery_stream)
    # The media hold's final checksum and session checks have completed. This
    # transition shares the publication transaction and rolls back with it.
    sync_recovery.mark_published(con, recovery_generation, source_version, recovery_stream)


def publish_snapshot(con, source, lease, payload, *, claims=None, media_store=None,
                     report_root=None, now=None, recovery_generation=0, recovery_stream=None, profile=None):
    """Stage files, validate closure, then publish atomically without committing.

    HTTP/CLI callers must commit successfully before returning the receipt.
    Retry the same publication UUID and claims after a lost response. A new
    claim requires a new publication UUID, even when the video is unchanged.
    """
    from .sync_media import MediaStore
    from .sync_snapshot import validate_snapshot
    from . import sync_checkpoints, sync_recovery
    profile = _profile(con, profile, media_store, report_root)
    _admit(con, source, lease, now)
    snapshot = validate_snapshot(payload)
    version = snapshot["version"]
    if version["library_id"] != source["library_id"]:
        raise SyncError("Snapshot belongs to another library", "SYNC_PEER_MISMATCH", 409)
    sync_recovery.assert_publish_allowed(con, recovery_generation, version, recovery_stream)
    bindings = _bindings(claims, {video["video_id"] for video in snapshot["videos"]})
    entries = sync_reports.validate_reports(snapshot["reports"], version)
    store = media_store or MediaStore(profile.media)
    root = Path(profile.reports)
    publication_binding = {"publication_id": snapshot["publication_id"], "source_node_id": source["node_id"],
        "source_version": version, "recovery_generation": recovery_generation, "recovery_stream": recovery_stream}
    payload_hash = publication_payload_sha256(snapshot, bindings,
        recovery_generation=recovery_generation, recovery_stream=recovery_stream)
    knowledge_hash = hashlib.sha256(protocol._json_bytes({
        "version": version, "videos": snapshot["videos"], "globals": snapshot["globals"]})).hexdigest()
    try:
        with (queue._operation(con), _final_authorization(con, source, lease, bindings, now, recovery_generation, version, recovery_stream),
              ExitStack() as files):
            current, stamp = queue._clock(now)
            session, _ = queue._session(con, source, lease, current)
            sync_recovery.assert_publish_allowed(con, recovery_generation, version, recovery_stream)
            recovery = sync_recovery.state(con)
            existing = con.execute("SELECT * FROM sync_publication WHERE publication_id=?",
                                   (snapshot["publication_id"],)).fetchone()
            if existing is not None:
                queue.require_publication_generation(con, snapshot["publication_id"], recovery_generation)
                if recovery["state"] == "resume_pending":
                    raise SyncError("Resume requires a fresh publication UUID", "SYNC_RECOVERY_FRESH_PUBLICATION_REQUIRED", 409)
                if (existing["payload_sha256"] != payload_hash
                        or existing["source_library_id"] != source["library_id"]
                        or existing["source_node_id"] != source["node_id"]):
                    raise SyncError("Publication ID has another payload", "SYNC_PUBLICATION_CONFLICT", 409)
                for entry in bindings:
                    queue.complete(con, source, lease, entry["claim"],
                                   publication_id=snapshot["publication_id"], video_id=entry["video_id"], now=now,
                                   recovery_generation=recovery_generation, recovery_stream=recovery_stream)
                return _receipt(existing, replayed=True)
            descriptors = [video["media"] for video in snapshot["videos"]]
            pins.require_publication_prepared(con, binding=publication_binding, descriptors=descriptors)
            media_hold = files.enter_context(store.hold_verified(descriptors))
            entries = sync_reports.prepare_files(root, entries, profile=profile)
            if recovery["state"] == "resume_pending":
                supplied = {video["video_id"] for video in snapshot["videos"]}
                if any(row["video_id"] not in supplied for row in con.execute(
                        "SELECT video_id FROM video_asset WHERE sync_state='ready'")):
                    raise SyncError("Resume publication omits a currently ready video",
                                    "SYNC_INCOMPLETE_SNAPSHOT", 409)
            previous = con.execute("SELECT * FROM sync_publication WHERE source_library_id=? AND source_node_id=? "
                                   "ORDER BY source_epoch DESC,source_revision DESC,rowid DESC LIMIT 1",
                                   (source["library_id"], source["node_id"])).fetchone()
            unchanged = False
            if previous is not None:
                before = (previous["source_epoch"], previous["source_revision"])
                incoming = (version["library_epoch"], version["kb_revision"])
                if incoming < before:
                    raise SyncError("Older knowledge generation cannot be published", "SYNC_STALE_PUBLICATION", 409)
                if incoming == before:
                    if previous["knowledge_sha256"] != knowledge_hash:
                        raise SyncError("Knowledge version has another payload", "SYNC_VERSION_CONFLICT", 409)
                    unchanged = True
            for entry in bindings:
                row = queue._claim_record(con, session, entry["claim"], current)
                if row["status"] != "claimed":
                    raise SyncError("Publication needs an active claim", "SYNC_OUTCOME_MISMATCH", 409)
                from .sync_claims import validate_claim_origin
                asset = next(video["tables"]["video_asset"][0] for video in snapshot["videos"]
                             if video["video_id"] == entry["video_id"])
                validate_claim_origin(row["url"], asset, origin=entry.get("origin"), source=source)
            if not unchanged:
                _ownership(con, snapshot)
            # Capture the receiver's before-image while holding the same write
            # transaction. Lost-response replay returns above without another
            # archive. Report-only publications need their old pointers too.
            checkpoint = sync_checkpoints.stage(con, snapshot["publication_id"],
                                                media_store=store, report_root=root, profile=profile)
            if not unchanged:
                _replace_knowledge(con, snapshot, store)
            else:
                review.replay(con)
            local_version = db.knowledge_version(con)
            record = {"publication_id": snapshot["publication_id"], "source_library_id": source["library_id"],
                      "source_node_id": source["node_id"], "source_epoch": version["library_epoch"],
                      "source_revision": version["kb_revision"], "payload_sha256": payload_hash,
                      "knowledge_sha256": knowledge_hash, "committed_at": stamp,
                      "local_version_json": db.j(local_version)}
            con.execute(f"INSERT INTO sync_publication ({','.join(record)}) VALUES ({','.join('?' for _ in record)})",
                        tuple(record.values()))
            con.execute("INSERT INTO sync_publication_fence(publication_id,generation) VALUES(?,?)",
                        (snapshot["publication_id"], recovery_generation))
            sync_checkpoints.register(con, checkpoint, profile=profile)
            for video in snapshot["videos"]:
                con.execute("INSERT INTO sync_published_video (publication_id,video_id,source_library_id,source_node_id,"
                            "source_epoch,source_revision,media_sha256,committed_at) VALUES (?,?,?,?,?,?,?,?)",
                            (snapshot["publication_id"], video["video_id"], source["library_id"], source["node_id"],
                             version["library_epoch"], version["kb_revision"], video["media"]["sha256"], stamp))
            sync_reports.install_pointers(con, entries, snapshot["publication_id"], local_version, profile=profile, root=root)
            for entry in bindings:
                credential = entry["claim"]
                con.execute("INSERT INTO sync_queue_publication (item_id,lease_generation,lease_token,owner_id,"
                            "owner_generation,publication_id,video_id,created_at) VALUES (?,?,?,?,?,?,?,?)",
                            (credential["item_id"], credential["lease_generation"], credential["lease_token"],
                             session["session_id"], session["generation"], snapshot["publication_id"], entry["video_id"], stamp))
                queue.complete(con, source, lease, credential, publication_id=snapshot["publication_id"],
                               video_id=entry["video_id"], now=now, recovery_generation=recovery_generation,
                               recovery_stream=recovery_stream)
            pins.complete_publication(con, binding=publication_binding, descriptors=descriptors, profile=profile, media_hold=media_hold)
            return _receipt(record, replayed=False)
    except sqlite3.IntegrityError:
        raise SyncError("Knowledge publication violates a storage constraint", "SYNC_CONTENT_CONFLICT", 409) from None
