"""Explicit local reference retirement; pure proofs and permanent reuse gates.

Nothing here removes an evidence file or makes a network request. Publication
abandonment and local selectors are independent authorizations. A reservation
already forbids reuse, but only complete immutable evidence can release roots.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import os
import re
import time
from contextlib import contextmanager

from . import storage_lifecycle as lifecycle, sync_media as safe, sync_protocol as protocol
from . import sync_no_resend as ledger

MAX_SELECTED = 128
_HISTORY = re.compile(r"[0-9a-f]{32}\.json\Z")
_SELECTORS = {
    "history_work": {"kind", "history_name", "raw_sha256", "work_key"},
    "source_map": {"kind", "mapping_generation", "origin_mapping_id", "raw_sha256"},
    "legacy_cache": {"kind", "holder_kind", "holder_name", "holder_raw_sha256", "video_id", "descriptor"},
    "publication_cache": {"kind", "holder_kind", "holder_name", "holder_raw_sha256", "video_id", "descriptor", "publication_id", "binding"},
}
_WORK = {"item_id", "video_id", "source_mapping_id", "source_mapping_generation"}


def _fail(code="SYNC_NO_RESEND_REFERENCE_INVALID"):
    ledger._fail(code)


@contextmanager
def _errors(store):
    """Keep caller cancellation identity while sanitizing proof failures."""
    previous = store.check_cancelled
    cancelled = []
    def check():
        if previous is not None:
            try:
                previous()
            except BaseException as exc:
                cancelled.append(exc)
                raise
    # This helper does not mutate the callback (heartbeat shares the store).
    try:
        yield check
    except BaseException as exc:
        if cancelled:
            # Filesystem sanitizers inside capture may have translated an
            # OSError-shaped cancellation. It is still the caller's primary.
            raise cancelled[0]
        if isinstance(exc, (protocol.SyncError, OSError)):
            # Domain helpers already use closed, credential-free errors. This
            # also preserves cancellation raised by reference_lifetime itself.
            raise
        with ledger._errors():
            raise


def work_key(work):
    from .sync_state import _integer
    if type(work) is not dict:
        _fail()
    value = {key: work.get(key) for key in _WORK}
    _integer(value["item_id"])
    protocol.validate_video_id(value["video_id"])
    if value["source_mapping_id"] is not None:
        ledger._sha(value["source_mapping_id"])
    if value["source_mapping_generation"] is not None:
        ledger._uuid(value["source_mapping_generation"])
    return value


def validate_selector(value):
    if type(value) is not dict or value.get("kind") not in _SELECTORS:
        _fail()
    ledger._exact(value, _SELECTORS[value["kind"]])
    kind = value["kind"]
    if kind == "history_work":
        if type(value["history_name"]) is not str or not _HISTORY.fullmatch(value["history_name"]):
            _fail()
        ledger._sha(value["raw_sha256"])
        ledger._exact(value["work_key"], _WORK)
        work_key(value["work_key"])
    elif kind == "source_map":
        ledger._sha(value["origin_mapping_id"])
        ledger._sha(value["raw_sha256"])
        if value["mapping_generation"] is not None:
            ledger._uuid(value["mapping_generation"])
    else:
        if value["holder_kind"] not in {"state", "history"}:
            _fail()
        name = value["holder_name"]
        if (type(name) is not str or (name != "sync-state.json" if value["holder_kind"] == "state"
                else not _HISTORY.fullmatch(name))):
            _fail()
        ledger._sha(value["holder_raw_sha256"])
        protocol.validate_video_id(value["video_id"])
        if protocol.validate_media_descriptor(value["descriptor"])["video_id"] != value["video_id"]:
            _fail()
        if kind == "publication_cache":
            from .sync_upload_pins import normalize_binding
            ledger._uuid(value["publication_id"])
            if normalize_binding(value["binding"])["publication_id"] != value["publication_id"]:
                _fail()
    ledger._encoded(value)
    return deepcopy(value)


def validate_selection(value):
    ledger._exact(value, {"format_version", "selected"})
    if type(value["format_version"]) is not int or value["format_version"] != 1:
        _fail()
    selected = value["selected"]
    if type(selected) is not list or not 1 <= len(selected) <= MAX_SELECTED:
        _fail()
    result, seen = [], set()
    for item in selected:
        item = validate_selector(item)
        identity = selector_key(item)
        if identity in seen:
            _fail("SYNC_NO_RESEND_REFERENCE_DUPLICATE")
        seen.add(identity)
        result.append(item)
    result.sort(key=lambda item: ledger._encoded(item))
    ledger._encoded(result)
    return {"format_version": 1, "selected": result}


def selector_key(item):
    kind = item["kind"]
    if kind == "history_work":
        identity = {"kind": kind, "history_name": item["history_name"]}
    elif kind == "source_map":
        identity = {"kind": kind, "mapping_generation": item["mapping_generation"],
                    "origin_mapping_id": item["origin_mapping_id"] if item["mapping_generation"] is None else None}
    else:
        identity = {key: item[key] for key in ("kind", "holder_kind", "holder_name", "video_id")}
        if kind == "publication_cache":
            identity["publication_id"] = item["publication_id"]
    return ledger._digest(identity)


def validate_request(value, contract):
    from .sync_state import _timestamp
    ledger._exact(value, ledger._CONTRACT | {"operation_id", "created_at", "policy_version", "selected", "preview_sha256"})
    if any(type(value[key]) is not type(contract[key]) or value[key] != contract[key] for key in ledger._CONTRACT):
        _fail("SYNC_NO_RESEND_OWNER_MISMATCH")
    ledger._uuid(value["operation_id"])
    _timestamp(value["created_at"])
    ledger._sha(value["preview_sha256"])
    if type(value["policy_version"]) is not int or value["policy_version"] != 1:
        _fail()
    normalized = validate_selection({"format_version": 1, "selected": value["selected"]})
    if value["selected"] != normalized["selected"]:
        _fail()
    for item in value["selected"]:
        if item["kind"] == "publication_cache":
            binding = item["binding"]
            if (binding["source_node_id"] != contract["home_node_id"]
                    or binding["source_version"]["library_id"] != contract["home_library_id"]
                    or binding["recovery_stream"]["library_id"] != contract["target_library_id"]):
                _fail("SYNC_NO_RESEND_PEER_MISMATCH")
    ledger._encoded(value)
    return deepcopy(value)


def selections(catalog, *, committed_only=False):
    if catalog is None:
        return []
    return [item for entry in catalog["references"].values()
            if not committed_only or entry["state"] == "committed"
            for item in entry["record"]["selected"]]


def is_mapping_retired(catalog, mapping_id, mapping_generation=None):
    return any(item["kind"] == "source_map" and (
        item["mapping_generation"] == mapping_generation if mapping_generation is not None
        else item["mapping_generation"] is None and item["origin_mapping_id"] == mapping_id)
        for item in selections(catalog))


def is_work_retired(catalog, work):
    if work is None:
        return False
    key = work_key(work)
    if is_mapping_retired(catalog, key["source_mapping_id"], key["source_mapping_generation"]):
        return True
    # A work instance can be archived before its mapping ID is linked. Linking
    # that same instance, changing phase or renewing its claim cannot revoke a
    # permanent decision. A different explicit generation/item remains new.
    return any(item["kind"] == "history_work" and all(item["work_key"][field] == key[field]
        for field in ("item_id", "video_id", "source_mapping_generation")) for item in selections(catalog))


def same_work_instance(first, second):
    return all(work_key(first)[key] == work_key(second)[key] for key in ("item_id", "video_id", "source_mapping_generation"))


def assert_mapping_reusable(profile, mapping_id, *, mapping_generation=None, state=None):
    if mapping_id is not None:
        ledger._sha(mapping_id)
    if mapping_generation is not None:
        ledger._uuid(mapping_generation)
    if is_mapping_retired(ledger.observe(profile, state=state)["catalog"], mapping_id, mapping_generation):
        _fail("SYNC_NO_RESEND_REFERENCE_RETIRED")


def assert_work_reusable(profile, work, *, state=None):
    if is_work_retired(ledger.observe(profile, state=state)["catalog"], work):
        _fail("SYNC_NO_RESEND_REFERENCE_RETIRED")


def cache_retired(catalog, *, kind, holder_kind, holder_name, video_id, descriptor, publication_id=None, binding=None, committed_only=False):
    return any(item["kind"] == kind and item["holder_kind"] == holder_kind and item["holder_name"] == holder_name
        and item["video_id"] == video_id and item["descriptor"] == descriptor
        and (kind == "legacy_cache" or item["publication_id"] == publication_id and item["binding"] == binding)
        for item in selections(catalog, committed_only=committed_only))


def detach_state_caches(state, catalog):
    """Apply only already-authorized selectors for this live state holder."""
    result = deepcopy(state)
    for video_id, descriptor in list(result["uploaded_media"].items()):
        if cache_retired(catalog, kind="legacy_cache", holder_kind="state", holder_name="sync-state.json",
                         video_id=video_id, descriptor=descriptor, committed_only=True):
            del result["uploaded_media"][video_id]
    for publication_id, record in list(result.get("publication_media", {}).items()):
        for video_id, descriptor in list(record["media"].items()):
            if cache_retired(catalog, kind="publication_cache", holder_kind="state", holder_name="sync-state.json",
                    video_id=video_id, descriptor=descriptor, publication_id=publication_id, binding=record["binding"], committed_only=True):
                del record["media"][video_id]
        if not record["media"]:
            del result["publication_media"][publication_id]
    return result


def _capture(profile, check=None):
    """A bounded no-follow immutable reading round, with no constructors that write."""
    from . import storage_home_roots as home
    limits, check = home.HomeLimits(), check or (lambda: None)
    budget = home._Budget(limits, check)
    layout = {"state": None, "history": "sync-work-history", "outbox": "sync-outbox",
              "maps_v1": "sync-sources/v1", "maps_v2": "sync-sources/v2"}
    records = {kind: {} for kind in layout}
    identities = []
    data = profile.database.parent
    try:
        for kind, relative in layout.items():
            budget.check()
            path = data if relative is None else data / relative
            try:
                directory = safe._Directory(path, base=data).__enter__()
            except FileNotFoundError:
                identities.append((kind, "absent"))
                continue
            try:
                names = ["sync-state.json"] if kind == "state" else []
                if kind != "state":
                    with os.scandir(directory.fd if os.name != "nt" else safe._native_path(path)) as entries:
                        for entry in entries:
                            budget.file()
                            if ledger._PART.fullmatch(entry.name) or re.fullmatch(r"[0-9a-f]{32}\.part", entry.name):
                                safe._safe_stat(directory.entry_stat(entry.name), directory=False)
                                continue
                            names.append(entry.name)
                names.sort()
                identities.append((kind, tuple(names), tuple((str(p), dev, ino) for p, dev, ino in directory.identities)))
                for name in names:
                    budget.check()
                    if kind == "state":
                        budget.file()
                    valid = name == "sync-state.json" if kind == "state" else (
                        bool(_HISTORY.fullmatch(name)) if kind == "history" else (
                        bool(ledger._SHA.fullmatch(name[:-5])) and name.endswith(".json") if kind == "maps_v1"
                        else name.endswith(".json") and _is_uuid(name[:-5])))
                    if not valid:
                        _fail("SYNC_NO_RESEND_REFERENCE_CAPTURE_INVALID")
                    maximum = limits.max_outbox_bytes if kind == "outbox" else (
                        limits.max_source_bytes if kind.startswith("maps") else limits.max_state_bytes)
                    raw, identity = home._read(directory, name, maximum, budget)
                    records[kind][name] = raw
                    identities.append((kind, name, identity))
                directory.verify()
            finally:
                directory.__exit__(None, None, None)
    except (OSError, home.HomeRootError, home._Invalid):
        _fail("SYNC_NO_RESEND_REFERENCE_CAPTURE_INVALID")
    return records, identities


def _is_uuid(value):
    try:
        ledger._uuid(value)
        return True
    except (ValueError, protocol.SyncError):
        return False


def _parsed(records, identity):
    from . import storage_home_roots as home
    from .sync_state import _decode
    contexts, maps, outboxes = {}, {}, {}
    for kind in ("state", "history"):
        for name, raw in records[kind].items():
            contexts[kind, name] = home._state(_decode(raw), identity, history=kind == "history")
    for kind in ("maps_v1", "maps_v2"):
        for name, raw in records[kind].items():
            value = _decode(raw)
            generation = None
            if kind == "maps_v2":
                ledger._exact(value, {"format_version", "mapping_generation", "origin", "resolution"})
                if type(value["format_version"]) is not int or value["format_version"] != 2 or value["mapping_generation"] != name[:-5]:
                    _fail()
                ledger._uuid(value["mapping_generation"])
                generation = value["mapping_generation"]
            else:
                ledger._exact(value, {"origin", "resolution"})
            origin_name = name if kind == "maps_v1" else value["origin"]["mapping_id"] + ".json"
            origin = home._mapping({"origin": value["origin"], "resolution": value["resolution"]}, origin_name, identity)
            maps[origin["mapping_id"], generation] = (kind, name, origin)
    for name, raw in records["outbox"].items():
        outboxes[name[:-5]] = home._outbox(_decode(raw), name, identity)
    return contexts, maps, outboxes


def _prove(selection, records, contexts, maps, outboxes, observed):
    """Validate exact selected bytes and deny any surviving mapping dependency."""
    catalog = observed["catalog"]
    selected = selection["selected"]
    work_choices = {(item["history_name"], item["raw_sha256"]): item for item in selected if item["kind"] == "history_work"}
    for item in selected:
        kind = item["kind"]
        selected_holder = (("history", item["history_name"]) if kind == "history_work" else
                           (item["holder_kind"], item["holder_name"]) if kind in {"legacy_cache", "publication_cache"} else None)
        if selected_holder is not None:
            context = contexts.get(selected_holder)
            current_binding = contexts["state", "sync-state.json"]["binding"]
            if context is None or any(context["binding"][key] != current_binding[key] for key in ledger._PEER):
                _fail("SYNC_NO_RESEND_PEER_MISMATCH")
        if kind == "history_work":
            raw = records["history"].get(item["history_name"])
            context = contexts.get(("history", item["history_name"]))
            if raw is None or hashlib.sha256(raw).hexdigest() != item["raw_sha256"] or context["work"] is None or work_key(context["work"]) != item["work_key"]:
                _fail("SYNC_NO_RESEND_REFERENCE_PROOF_MISMATCH")
            current = contexts["state", "sync-state.json"]["work"]
            if current is not None and same_work_instance(current, item["work_key"]):
                _fail("SYNC_NO_RESEND_REFERENCE_ACTIVE")
        elif kind == "source_map":
            matched = maps.get((item["origin_mapping_id"], item["mapping_generation"]))
            if matched is None or hashlib.sha256(records[matched[0]][matched[1]]).hexdigest() != item["raw_sha256"]:
                _fail("SYNC_NO_RESEND_REFERENCE_PROOF_MISMATCH")
            for (holder_kind, name), context in contexts.items():
                work = context["work"]
                if work is None:
                    continue
                generation = work.get("source_mapping_generation")
                mapping_id = work.get("source_mapping_id")
                if generation is not None:
                    same = generation == item["mapping_generation"]
                elif mapping_id is not None:
                    same = item["mapping_generation"] is None and mapping_id == item["origin_mapping_id"]
                else:
                    # Old work may not have linked its map yet. Its video is
                    # the only surviving proof, so retain every related map
                    # until that independent work reference is also retired.
                    same = work["video_id"] == matched[2]["video_id"]
                if not same or is_work_retired(catalog, work):
                    continue
                raw_sha = hashlib.sha256(records[holder_kind][name]).hexdigest()
                if holder_kind == "history" and (name, raw_sha) in work_choices:
                    continue
                _fail("SYNC_NO_RESEND_REFERENCE_ACTIVE")
            for publication_id, envelope in outboxes.items():
                if catalog is not None and publication_id in catalog["publications"]:
                    continue
                if any(video["video_id"] == matched[2]["video_id"] and video["media"]["sha256"] == matched[2]["media_sha256"]
                       for video in envelope["snapshot"]["videos"]) or any(claim.get("origin", {}).get("mapping_id") == item["origin_mapping_id"] or (
                        "origin" not in claim and claim["video_id"] == matched[2]["video_id"]
                        and any(video["video_id"] == matched[2]["video_id"] and video["media"]["sha256"] == matched[2]["media_sha256"]
                                for video in envelope["snapshot"]["videos"])) for claim in envelope["claims"]):
                    # Old wire envelopes do not bind a local mapping generation.
                    _fail("SYNC_NO_RESEND_REFERENCE_ACTIVE")
        else:
            holder = item["holder_kind"], item["holder_name"]
            raw = records[holder[0]].get(holder[1])
            context = contexts.get(holder)
            if raw is None or hashlib.sha256(raw).hexdigest() != item["holder_raw_sha256"]:
                _fail("SYNC_NO_RESEND_REFERENCE_PROOF_MISMATCH")
            if kind == "legacy_cache":
                actual = context["uploaded_media"].get(item["video_id"])
            else:
                record = context.get("publication_media", {}).get(item["publication_id"])
                actual = None if record is None or record["binding"] != item["binding"] else record["media"].get(item["video_id"])
                original = None if catalog is None else catalog["publications"].get(item["publication_id"])
                if original is None or original["state"] != "committed" or original["record"]["binding"] != item["binding"]:
                    _fail("SYNC_NO_RESEND_REFERENCE_ACTIVE")
                outbox = outboxes.get(item["publication_id"])
                raw_outbox = records["outbox"].get(item["publication_id"] + ".json")
                metadata = original["record"]["metadata"]
                if (raw_outbox is None or len(raw_outbox) != metadata["byte_size"] or hashlib.sha256(raw_outbox).hexdigest() != metadata["sha256"]
                        or item["descriptor"] not in [video["media"] for video in outbox["snapshot"]["videos"]]):
                    _fail("SYNC_NO_RESEND_REFERENCE_PROOF_MISMATCH")
            if actual != item["descriptor"]:
                _fail("SYNC_NO_RESEND_REFERENCE_PROOF_MISMATCH")


def preview(store, selection):
    selection = validate_selection(selection)
    with _errors(store) as check, lifecycle.shared(store.profile, check_cancelled=check):
        state = store.read_observation()
        observed = ledger.observe(store.profile, state=state)
        records, image = _capture(store.profile, check)
        identity = {"library_id": state["binding"]["home_library_id"], "node_id": state["binding"]["home_node_id"]}
        contexts, maps, outboxes = _parsed(records, identity)
        state = contexts["state", "sync-state.json"]
        _prove(selection, records, contexts, maps, outboxes, observed)
        again, next_image = _capture(store.profile, check)
        next_contexts, _, _ = _parsed(again, identity)
        relevant = lambda value: {key: value.get(key) for key in ("binding", "local_epoch", "work", "pending_publication",
            "recovery_attempt", "legacy_pending_stream", "uploaded_media", "publication_media", "sender_contract")}
        stable_image = lambda items: [item for item in items if item[0] != "state" or len(item) == 3 and item[1] != "sync-state.json"]
        if (stable_image(image) != stable_image(next_image)
                or any(records[kind] != again[kind] for kind in records if kind != "state")
                or relevant(state) != relevant(next_contexts["state", "sync-state.json"])
                or ledger.observe(store.profile, state=store.read_observation()) != observed):
            _fail("SYNC_NO_RESEND_REFERENCE_SOURCE_CHANGED")
        # Raw state bytes identify a selected container. For unrelated state,
        # semantic projections omit lease/display changes from the CAS.
        semantics = {"selection": selection, "binding": state["binding"], "ledger": observed,
            "images": {kind: {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
                       for kind, files in records.items() if kind != "state"},
            "current_work": state["work"], "legacy_cache": state["uploaded_media"],
            "publication_cache": state.get("publication_media", {})}
        digest = hashlib.sha256(ledger._encoded(semantics, ledger.MAX_CATALOG_BYTES)).hexdigest()
        return {"digest": digest, "selected_count": len(selection["selected"]), "stage": "preview"}


def apply(store, selection, *, expected_digest, operation_id):
    from contextlib import nullcontext
    from .sync_state import _write, _validate
    selection = validate_selection(selection)
    ledger._sha(expected_digest)
    ledger._uuid(operation_id)
    with (store.exclusive() if store._directory is None else nullcontext()), _errors(store) as check, store.reference_lifetime():
        actual = preview(store, selection)
        if actual["digest"] != expected_digest:
            _fail("SYNC_NO_RESEND_STALE_PREVIEW")
        state = store.enable_no_resend()
        observed = ledger.observe(store.profile, state=state)
        request = {**observed["contract"], "operation_id": operation_id,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "policy_version": 1, "selected": selection["selected"], "preview_sha256": expected_digest}
        validate_request(request, observed["contract"])
        ledger._publish(store.profile, "references", operation_id, request)
        with store._mutating() as directory:
            state = store._read(directory)
            catalog = ledger.observe(store.profile, state=state)["catalog"]
            _write(directory, "sync-state.json", _validate(detach_state_caches(state, catalog)))
        return deepcopy(request)


def resume(store, operation_id):
    from contextlib import nullcontext
    from .sync_state import _write, _validate
    ledger._uuid(operation_id)
    with (store.exclusive() if store._directory is None else nullcontext()), _errors(store) as check, store.reference_lifetime():
        state = store.read_observation()
        observed = ledger.observe(store.profile, state=state, allow_incomplete=True)
        entry = None if observed["catalog"] is None else observed["catalog"]["references"].get(operation_id)
        if entry is None:
            _fail("SYNC_NO_RESEND_OPERATION_MISSING")
        if any(item != ("references", operation_id) for item in observed["incomplete"]):
            _fail("SYNC_NO_RESEND_RECORD_MISSING")
        request = entry["record"]
        if entry["state"] != "committed" or observed["incomplete"]:
            # Reserved catalog stores the precise authorization. Re-prove all
            # immutable selections; a state cache may have been detached only
            # by this same authorization and can never expand its selection.
            records, _ = _capture(store.profile, check)
            identity = {"library_id": state["binding"]["home_library_id"], "node_id": state["binding"]["home_node_id"]}
            contexts, maps, outboxes = _parsed(records, identity)
            stable = [item for item in request["selected"] if item["kind"] in {"history_work", "source_map"} or item["holder_kind"] == "history"]
            if stable:
                _prove({"selected": stable}, records, contexts, maps, outboxes, observed)
            ledger._publish(store.profile, "references", operation_id, request)
        with store._mutating() as directory:
            current = store._read(directory)
            catalog = ledger.observe(store.profile, state=current)["catalog"]
            _write(directory, "sync-state.json", _validate(detach_state_caches(current, catalog)))
        return deepcopy(request)
