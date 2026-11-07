"""Durable upload intentions, exact publication completion and tombstones.

All mutations require the caller's lifecycle guard and SQL transaction. These
helpers do not authenticate a network peer; callers must bind source_node_id
and source_version to their authenticated source before invoking them. No TTL,
failed task, omitted snapshot entry or expired lease releases a protection.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import uuid

from . import storage_artifacts as artifacts
from .sync_protocol import SyncError, _json_bytes, validate_media_descriptor
from .sync_snapshot import _version

BINDING_FIELDS = frozenset(("publication_id", "source_node_id", "source_version", "recovery_stream", "recovery_generation"))
INTENT_FIELDS = BINDING_FIELDS | {"descriptor"}
_MAX_INTEGER = 2**63 - 1


def _fail(message, code="SYNC_UPLOAD_INTENT_INVALID", status=409):
    raise SyncError(message, code, status)


def _uuid(value):
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError, TypeError):
        _fail("Upload identity must be a canonical UUID", status=400)
    return value


def normalize_binding(value):
    if type(value) is not dict or set(value) != BINDING_FIELDS:
        _fail("Invalid publication intent binding", status=400)
    _uuid(value["publication_id"])
    _uuid(value["source_node_id"])
    _version(value["source_version"])
    stream = value["recovery_stream"]
    if type(stream) is not dict or set(stream) != {"library_id", "library_epoch"}:
        _fail("Upload intent requires an exact receiver stream", status=400)
    _uuid(stream["library_id"])
    if type(stream["library_epoch"]) is not int or not 1 <= stream["library_epoch"] <= _MAX_INTEGER:
        _fail("Invalid receiver epoch", status=400)
    generation = value["recovery_generation"]
    if type(generation) is not int or not 0 <= generation <= _MAX_INTEGER:
        _fail("Invalid receiver recovery generation", status=400)
    return {"publication_id": value["publication_id"], "source_node_id": value["source_node_id"],
            "source_version": dict(value["source_version"]), "recovery_stream": dict(stream),
            "recovery_generation": generation}


def normalize_intent(value):
    """Return a detached, closed canonical descriptor/binding object."""
    if type(value) is not dict or set(value) != INTENT_FIELDS:
        _fail("Invalid upload intent fields", status=400)
    return {**normalize_binding({k: value[k] for k in BINDING_FIELDS}),
            "descriptor": validate_media_descriptor(value["descriptor"])}


def intent_id(value):
    return "pin_" + hashlib.sha256(_json_bytes(normalize_intent(value))).hexdigest()


def _binding(intent):
    return {k: intent[k] for k in BINDING_FIELDS}


def _record(binding, *, descriptor=None):
    value = binding if descriptor is None else {**binding, "descriptor": descriptor}
    digest = hashlib.sha256(_json_bytes(value)).hexdigest()
    source, target = binding["source_version"], binding["recovery_stream"]
    return {"pin_id": ("pubpin_" if descriptor is None else "pin_") + digest,
            "kind": "publication" if descriptor is None else "media",
            "publication_id": binding["publication_id"], "parent_pin_id": None,
            "source_library_id": source["library_id"], "source_node_id": binding["source_node_id"],
            "source_epoch": source["library_epoch"], "source_revision": source["kb_revision"],
            "target_library_id": target["library_id"], "target_library_epoch": target["library_epoch"],
            "recovery_generation": binding["recovery_generation"],
            "descriptor_json": None if descriptor is None else _json_bytes(descriptor).decode(),
            "intent_sha256": digest, "state": "reserved", "artifact_id": None,
            "abandon_operation_id": None, "abandon_request_sha256": None,
            "created_at": artifacts._stamp(), "updated_at": artifacts._stamp()}


def _row(con, where, parameters):
    cursor = con.execute("SELECT * FROM sync_upload_pin WHERE " + where, parameters)
    row = cursor.fetchone()
    return dict(zip((d[0] for d in cursor.description), row)) if row is not None else None


def get(con, pin_id):
    return _row(con, "pin_id=?", (pin_id,))


def _insert(con, record):
    con.execute("INSERT INTO sync_upload_pin(" + ",".join(record) + ") VALUES(" + ",".join("?" for _ in record) + ")",
                tuple(record.values()))
    return record


def _same(existing, expected):
    ignored = {"created_at", "updated_at", "state", "artifact_id", "abandon_operation_id", "abandon_request_sha256"}
    if any(existing[key] != value for key, value in expected.items() if key not in ignored):
        _fail("Publication or pin ID has different intent fields", "SYNC_UPLOAD_INTENT_CONFLICT")


def _assert_fence(con, binding):
    from .sync_recovery import assert_publish_allowed
    assert_publish_allowed(con, binding["recovery_generation"], binding["source_version"], binding["recovery_stream"])


def _assert_abandon_fence(con, binding, *, known):
    """Retiring a proven old intent is valid during/after a recovery hold.

    Unknown intentions must name the current fence, but need not be allowed
    to publish. Proven old bindings may only move backwards within this same
    receiver library, never into a future epoch/generation or another library.
    """
    from .sync_recovery import state
    control = state(con)
    supplied, current = binding["recovery_stream"], control["stream"]
    if (supplied["library_id"] != current["library_id"]
            or supplied["library_epoch"] > current["library_epoch"]
            or (not known and supplied != current)):
        _fail("Abandon request belongs to another receiver stream", "SYNC_RECOVERY_STREAM_MISMATCH")
    if (binding["recovery_generation"] > control["generation"]
            or (not known and binding["recovery_generation"] != control["generation"])):
        _fail("Abandon request belongs to another recovery generation", "SYNC_RECOVERY_GENERATION_MISMATCH")


def _parent(con, binding, *, create=False, completing=False):
    expected = _record(binding)
    existing = _row(con, "kind='publication' AND publication_id=?", (binding["publication_id"],))
    if existing is not None:
        # A publication-wide tombstone also refuses descriptors never reserved.
        if existing["state"] == "abandoned":
            _fail("Publication has been permanently abandoned", "SYNC_UPLOAD_ABANDONED")
        _same(existing, expected)
        if existing["state"] == "published" and not completing:
            _fail("Published upload intents cannot be reopened", "SYNC_UPLOAD_PUBLISHED")
        return existing
    if not completing and con.execute("SELECT 1 FROM sync_publication WHERE publication_id=?", (binding["publication_id"],)).fetchone():
        _fail("Published upload intents cannot be reopened", "SYNC_UPLOAD_PUBLISHED")
    if create:
        return _insert(con, expected)
    _fail("Publication intent has not been reserved", "SYNC_UPLOAD_NOT_RESERVED")


def reserve(con, intent, *, profile):
    value = normalize_intent(intent)
    binding, expected = _binding(value), _record(_binding(value), descriptor=value["descriptor"])
    with artifacts.operation(con, profile):
        parent = _parent(con, binding, create=True)
        _assert_fence(con, binding)
        expected["parent_pin_id"] = parent["pin_id"]
        existing = get(con, expected["pin_id"])
        if existing is not None:
            _same(existing, expected)
            if existing["state"] not in ("reserved", "stored"):
                _fail("Terminal upload intent cannot be reopened", "SYNC_UPLOAD_TERMINAL")
            return existing
        # A frozen publication has exactly one descriptor for each video.
        for row in con.execute("SELECT descriptor_json FROM sync_upload_pin WHERE parent_pin_id=?", (parent["pin_id"],)):
            if json.loads(row[0])["video_id"] == value["descriptor"]["video_id"]:
                _fail("Publication video has another descriptor", "SYNC_UPLOAD_INTENT_CONFLICT")
        return _insert(con, expected)


def prepare_publication(con, *, binding, descriptors, profile):
    """Freeze the complete upload set, including a zero-media publication.

    The protocol permits adding descriptors only during this first atomic
    preparation. Individual uploads must call require_prepared before reserve.
    An old completed receipt has no new pins fabricated for it.
    """
    binding, descriptors = normalize_binding(binding), _descriptors(descriptors)
    with artifacts.operation(con, profile):
        _assert_fence(con, binding)
        parent = _row(con, "kind='publication' AND publication_id=?", (binding["publication_id"],))
        if parent is not None and parent["state"] == "abandoned":
            _fail("Publication has been permanently abandoned", "SYNC_UPLOAD_ABANDONED")
        completed = con.execute("SELECT 1 FROM sync_publication WHERE publication_id=?", (binding["publication_id"],)).fetchone()
        if completed:
            _assert_publication(con, binding, descriptors)
        if parent is not None:
            _same(parent, _record(binding))
            rows = [dict(row) for row in con.execute("SELECT * FROM sync_upload_pin WHERE parent_pin_id=?", (parent["pin_id"],))]
            expected = {_json_bytes(item).decode() for item in descriptors}
            if {row["descriptor_json"] for row in rows} != expected:
                _fail("Frozen publication media set cannot change", "SYNC_UPLOAD_SET_MISMATCH")
            if completed and (parent["state"] != "published" or any(row["state"] != "published" for row in rows)):
                _fail("Publication and upload receipt states disagree", "SYNC_UPLOAD_INTENT_CONFLICT")
        elif not completed:
            parent = _parent(con, binding, create=True)
            rows = [reserve(con, {**binding, "descriptor": item}, profile=profile) for item in descriptors]
        else:
            rows = []
        return {"binding": binding, "state": "published" if completed else "reserved",
                "media": [] if completed else [{"descriptor": json.loads(row["descriptor_json"]),
                    "pin_id": row["pin_id"], "state": row["state"]} for row in rows]}


def require_prepared(con, intent):
    """Read-only admission: no individual request expands a frozen set."""
    value = normalize_intent(intent)
    binding = _binding(value)
    parent = _parent(con, binding)
    _assert_fence(con, binding)
    expected = _record(binding, descriptor=value["descriptor"])
    expected["parent_pin_id"] = parent["pin_id"]
    row = get(con, expected["pin_id"])
    if row is None:
        _fail("Upload descriptor is not in the frozen publication", "SYNC_UPLOAD_NOT_RESERVED")
    _same(row, expected)
    if row["state"] not in ("reserved", "stored"):
        _fail("Terminal upload cannot receive more media", "SYNC_UPLOAD_TERMINAL")
    return row


def require_publication_prepared(con, *, binding, descriptors):
    binding, descriptors = normalize_binding(binding), _descriptors(descriptors)
    parent = _parent(con, binding)
    _assert_fence(con, binding)
    expected = {_json_bytes(item).decode() for item in descriptors}
    actual = {row[0] for row in con.execute("SELECT descriptor_json FROM sync_upload_pin WHERE parent_pin_id=?", (parent["pin_id"],))}
    if actual != expected:
        _fail("Publication differs from its frozen upload set", "SYNC_UPLOAD_SET_MISMATCH")
    for descriptor in descriptors:
        require_prepared(con, {**binding, "descriptor": descriptor})
    return parent


def _verified_artifact(con, descriptor, artifact_id, profile, *, check_cancelled=None):
    record = artifacts.verify(con, artifact_id, profile=profile, root=profile.media, check_cancelled=check_cancelled)
    return _check_descriptor(record, descriptor)


def _check_descriptor(record, descriptor):
    expected_key = f"sync/{descriptor['video_id']}/{descriptor['sha256']}.{descriptor['extension']}"
    if (record["namespace"] != "sync_media" or record["storage_key"] != expected_key
            or record["sha256"] != descriptor["sha256"] or record["byte_size"] != descriptor["byte_size"]):
        _fail("Artifact does not match the exact upload descriptor", "SYNC_UPLOAD_ARTIFACT_MISMATCH")
    return record


def mark_stored(con, intent, artifact_id, *, profile, check_cancelled=None):
    return _mark_stored(con, intent, artifact_id, profile=profile, check_cancelled=check_cancelled)


def _mark_stored_verified(con, intent, proof, *, profile):
    record = artifacts._validate_verified(con, proof, profile=profile)
    return _mark_stored(con, intent, record["artifact_id"], profile=profile, proof=proof)


def _mark_stored(con, intent, artifact_id, *, profile, proof=None, check_cancelled=None):
    value = normalize_intent(intent)
    binding, expected = _binding(value), _record(_binding(value), descriptor=value["descriptor"])
    with artifacts.operation(con, profile):
        parent = _parent(con, binding)
        _assert_fence(con, binding)
        expected["parent_pin_id"] = parent["pin_id"]
        existing = get(con, expected["pin_id"])
        if existing is None:
            _fail("Upload intent has not been reserved", "SYNC_UPLOAD_NOT_RESERVED")
        _same(existing, expected)
        if existing["state"] not in ("reserved", "stored"):
            _fail("Terminal upload intent cannot be reopened", "SYNC_UPLOAD_TERMINAL")
        if proof is None:
            _verified_artifact(con, value["descriptor"], artifact_id, profile, check_cancelled=check_cancelled)
        else:
            _check_descriptor(artifacts._validate_verified(con, proof, profile=profile), value["descriptor"])
        if existing["artifact_id"] is not None and existing["artifact_id"] != artifact_id:
            _fail("Upload intent already stores another artifact", "SYNC_UPLOAD_INTENT_CONFLICT")
        if existing["state"] == "stored":
            return existing
        con.execute("UPDATE sync_upload_pin SET state='stored',artifact_id=?,updated_at=? WHERE pin_id=?",
                    (artifact_id, artifacts._stamp(), expected["pin_id"]))
        return get(con, expected["pin_id"])


def _descriptors(value):
    if type(value) is not list or len(value) > 10_000:
        _fail("Publication media must be a bounded complete descriptor list", status=400)
    result = [validate_media_descriptor(item) for item in value]
    if len({item["video_id"] for item in result}) != len(result):
        _fail("Publication has duplicate video descriptors", status=400)
    return result


def _assert_publication(con, binding, descriptors):
    row = con.execute("SELECT * FROM sync_publication WHERE publication_id=?", (binding["publication_id"],)).fetchone()
    if row is None:
        _fail("Upload completion requires the actual publication transaction", "SYNC_UPLOAD_PUBLICATION_REQUIRED")
    source = binding["source_version"]
    if (row["source_library_id"], row["source_node_id"], row["source_epoch"], row["source_revision"]) != (
            source["library_id"], binding["source_node_id"], source["library_epoch"], source["kb_revision"]):
        _fail("Published source differs from the reserved intent", "SYNC_UPLOAD_INTENT_CONFLICT")
    try:
        local = json.loads(row["local_version_json"])
        _version(local)
        stream = {"library_id": local["library_id"], "library_epoch": local["library_epoch"]}
    except (ValueError, KeyError, TypeError):
        _fail("Publication has an invalid receiver version", "SYNC_UPLOAD_INTENT_CONFLICT")
    # Pre-P03 committed receipts have no fence row and belong to generation 0.
    # Preserve that existing replay contract without creating synthetic pins.
    from .sync_queue import publication_generation
    generation = publication_generation(con, binding["publication_id"])
    if stream != binding["recovery_stream"] or generation != binding["recovery_generation"]:
        _fail("Publication receiver fence differs from the intent", "SYNC_UPLOAD_INTENT_CONFLICT")
    rows = con.execute("SELECT * FROM sync_published_video WHERE publication_id=?", (binding["publication_id"],)).fetchall()
    expected = {item["video_id"]: item["sha256"] for item in descriptors}
    if {row["video_id"]: row["media_sha256"] for row in rows} != expected or any(
            (row["source_library_id"], row["source_node_id"], row["source_epoch"], row["source_revision"]) !=
            (source["library_id"], binding["source_node_id"], source["library_epoch"], source["kb_revision"]) for row in rows):
        _fail("Publication receipts do not cover exactly these media", "SYNC_UPLOAD_SET_MISMATCH")


def require_published_binding(con, *, binding):
    """Prove a committed publication's original binding using only SQL facts.

    An absent fence row has the established pre-recovery generation-zero
    meaning. Its receiver stream must still come from the original committed
    local_version_json. A parent, when present, must independently agree with
    that evidence and be published. Current recovery state only bounds a known
    historical fence; it never supplies missing original metadata.
    """
    binding = normalize_binding(binding)
    row = con.execute("SELECT * FROM sync_publication WHERE publication_id=?", (binding["publication_id"],)).fetchone()
    if row is None:
        _fail("A committed publication receipt was not found", "SYNC_PUBLICATION_REQUIRED", 404)
    source = binding["source_version"]
    if (row["source_library_id"], row["source_node_id"], row["source_epoch"], row["source_revision"]) != (
            source["library_id"], binding["source_node_id"], source["library_epoch"], source["kb_revision"]):
        _fail("Publication receipt belongs to another source", "SYNC_PUBLICATION_CONFLICT")
    from .sync_protocol import _unique_object
    from .sync_queue import publication_generation, _stored_time
    try:
        encoded = row["local_version_json"]
        if type(encoded) is not str or len(encoded) > 1024:
            raise ValueError
        local = json.loads(encoded, object_pairs_hook=_unique_object)
        _version(local)
        generation = publication_generation(con, binding["publication_id"])
        if type(generation) is not int or not 0 <= generation <= _MAX_INTEGER:
            raise ValueError
        if _stored_time(row["committed_at"]) is None:
            raise ValueError
    except (SyncError, ValueError, TypeError, KeyError):
        _fail("Original publication binding cannot be proved", "SYNC_PUBLICATION_BINDING_UNPROVEN")
    stream = {"library_id": local["library_id"], "library_epoch": local["library_epoch"]}
    if stream != binding["recovery_stream"] or generation != binding["recovery_generation"]:
        _fail("Publication receipt has another receiver fence", "SYNC_UPLOAD_INTENT_CONFLICT")
    parent = _row(con, "kind='publication' AND publication_id=?", (binding["publication_id"],))
    if parent is not None:
        _same(parent, _record(binding))
        if parent["state"] != "published":
            _fail("Publication and intent receipts disagree", "SYNC_PUBLICATION_BINDING_UNPROVEN")
    # Known terminal facts can be queried during a hold or at a later fence,
    # using the same backwards-only receiver bounds as exact abandonment.
    _assert_abandon_fence(con, binding, known=True)
    return row


def _assert_current_media(con, descriptors, profile):
    # sync_published_video records content SHA, not its extension/size. Before
    # releasing a new intent prove the exact key installed by this publication
    # too; a matching SHA in an unrelated extension is a different file.
    for descriptor in descriptors:
        row = con.execute("SELECT content_sha256,files_json,sync_state FROM video_asset WHERE video_id=?",
                          (descriptor["video_id"],)).fetchone()
        try:
            files = json.loads(row["files_json"]) if row is not None else None
        except (ValueError, TypeError):
            files = None
        expected = f"{Path(profile.media).name}/sync/{descriptor['video_id']}/{descriptor['sha256']}.{descriptor['extension']}"
        if (row is None or row["sync_state"] != "ready" or row["content_sha256"] != descriptor["sha256"]
                or type(files) is not dict or files.get("video") != expected):
            _fail("Publication has not installed the exact reserved media", "SYNC_UPLOAD_SET_MISMATCH")


def complete_publication(con, *, binding, descriptors, profile, media_hold=None):
    """Complete only a successful exact publication, in its same transaction.

An exact already-published retry checks durable facts without reopening media;
the network layer must first validate its original payload/claims/identity.
"""
    binding, descriptors = normalize_binding(binding), _descriptors(descriptors)
    with artifacts.operation(con, profile):
        _assert_fence(con, binding)
        _assert_publication(con, binding, descriptors)
        parent = _parent(con, binding, create=not descriptors, completing=True)
        rows = [dict(row) for row in con.execute("SELECT * FROM sync_upload_pin WHERE parent_pin_id=?", (parent["pin_id"],))]
        expected = {_record(binding, descriptor=item)["pin_id"]: item for item in descriptors}
        if {row["pin_id"] for row in rows} != set(expected):
            _fail("Publication media and reserved intents differ", "SYNC_UPLOAD_SET_MISMATCH")
        if parent["state"] != "published":
            _assert_current_media(con, descriptors, profile)
        for row in rows:
            record = _record(binding, descriptor=expected[row["pin_id"]])
            record["parent_pin_id"] = parent["pin_id"]
            _same(row, record)
            wanted = "published" if parent["state"] == "published" else "stored"
            if row["state"] != wanted:
                _fail("Publication has an uncompleted upload", "SYNC_UPLOAD_NOT_STORED")
            if wanted == "stored":
                descriptor = expected[row["pin_id"]]
                proof = artifacts._from_media_hold(con, media_hold, descriptor, profile=profile)
                record = artifacts._validate_verified(con, proof, profile=profile)
                if record["artifact_id"] != row["artifact_id"]:
                    _fail("Stored upload references another artifact", "SYNC_UPLOAD_ARTIFACT_MISMATCH")
                _check_descriptor(record, descriptor)
        if parent["state"] != "published":
            con.execute("UPDATE sync_upload_pin SET state='published',updated_at=? WHERE pin_id=? OR parent_pin_id=?",
                        (artifacts._stamp(), parent["pin_id"], parent["pin_id"]))
        return get(con, parent["pin_id"])


def abandon(con, *, binding, operation_id, profile):
    """Record an explicit permanent publication-wide abandonment receipt.

The sender must persist its own no-resend fact before calling this operation.
Replaying an exact tombstone never depends on media existence or elapsed time.
"""
    binding, operation_id = normalize_binding(binding), _uuid(operation_id)
    request_hash = hashlib.sha256(_json_bytes({"binding": binding, "operation_id": operation_id})).hexdigest()
    expected = _record(binding)
    with artifacts.operation(con, profile):
        replay = _row(con, "abandon_operation_id=?", (operation_id,))
        if replay is not None:
            if replay["abandon_request_sha256"] != request_hash:
                _fail("Abandon operation ID has another request", "SYNC_UPLOAD_ABANDON_CONFLICT")
            _same(replay, expected)
            _assert_abandon_fence(con, binding, known=True)
            return replay
        existing = _row(con, "kind='publication' AND publication_id=?", (binding["publication_id"],))
        if existing is not None:
            _same(existing, expected)
            if existing["state"] == "abandoned":
                _fail("Publication was abandoned by another operation", "SYNC_UPLOAD_ABANDON_CONFLICT")
            if existing["state"] == "published":
                _fail("A published intent cannot be abandoned", "SYNC_UPLOAD_PUBLISHED")
        if con.execute("SELECT 1 FROM sync_publication WHERE publication_id=?", (binding["publication_id"],)).fetchone():
            _fail("A published intent cannot be abandoned", "SYNC_UPLOAD_PUBLISHED")
        _assert_abandon_fence(con, binding, known=existing is not None)
        parent = existing or _insert(con, expected)
        stamp = artifacts._stamp()
        con.execute("UPDATE sync_upload_pin SET state='abandoned',updated_at=? WHERE parent_pin_id=?", (stamp, parent["pin_id"]))
        con.execute("UPDATE sync_upload_pin SET state='abandoned',abandon_operation_id=?,abandon_request_sha256=?,updated_at=? WHERE pin_id=?",
                    (operation_id, request_hash, stamp, parent["pin_id"]))
        return get(con, parent["pin_id"])


def legacy_hold(con, *, source_library_id, source_node_id, descriptor, profile, artifact_id=None):
    """Unbound old uploads keep a permanent hold; no completion can release it."""
    value = {"source_library_id": _uuid(source_library_id), "source_node_id": _uuid(source_node_id),
             "descriptor": validate_media_descriptor(descriptor)}
    digest = hashlib.sha256(_json_bytes(value)).hexdigest()
    with artifacts.operation(con, profile):
        if artifact_id is not None:
            _verified_artifact(con, value["descriptor"], artifact_id, profile)
        expected = {"pin_id": "legacy_" + digest, "kind": "legacy", "publication_id": None, "parent_pin_id": None,
                    "source_library_id": value["source_library_id"], "source_node_id": value["source_node_id"],
                    "source_epoch": None, "source_revision": None, "target_library_id": None,
                    "target_library_epoch": None, "recovery_generation": None,
                    "descriptor_json": _json_bytes(value["descriptor"]).decode(), "intent_sha256": digest,
                    "state": "legacy_hold", "artifact_id": artifact_id,
                    "abandon_operation_id": None, "abandon_request_sha256": None,
                    "created_at": artifacts._stamp(), "updated_at": artifacts._stamp()}
        existing = get(con, expected["pin_id"])
        if existing is None:
            return _insert(con, expected)
        _same(existing, expected)
        if existing["state"] != "legacy_hold" or (artifact_id is not None and existing["artifact_id"] not in (None, artifact_id)):
            _fail("Legacy hold has incompatible stored facts", "SYNC_UPLOAD_INTENT_CONFLICT")
        if existing["artifact_id"] is None and artifact_id is not None:
            con.execute("UPDATE sync_upload_pin SET artifact_id=?,updated_at=? WHERE pin_id=?",
                        (artifact_id, artifacts._stamp(), expected["pin_id"]))
        return get(con, expected["pin_id"])


def held_descriptors(con):
    """Durable roots include reserved descriptors even before an artifact row."""
    return [json.loads(row[0]) for row in con.execute("SELECT DISTINCT descriptor_json FROM sync_upload_pin "
            "WHERE kind IN ('media','legacy') AND state IN ('reserved','stored','legacy_hold')")]
