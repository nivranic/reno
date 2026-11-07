"""Closed public continuity observations; representation never grants effects."""
from __future__ import annotations

from . import sync_source_readmit_contract as wire, sync_snapshot

MAX_BYTES = 8 * 1024
REQUEST_FIELDS = frozenset(("kind", "source", "target"))
RESULT_FIELDS = frozenset(("format_version", "kind", "source_identity", "target_identity",
    "handshake", "receiver_version", "state", "continuity", "head"))


def normalize_request(value):
    value = wire._copy(value, MAX_BYTES)
    wire._exact(value, REQUEST_FIELDS)
    if value["kind"] != "source_continuity":
        wire._fail()
    wire._identity(value["source"], source=True)
    wire._identity(value["target"])
    if (value["source"]["handshake"]["protocol_version"] not in (2, 3)
            or value["source"]["handshake"]["migration_version"] != 17
            or any(value["source"][key] == value["target"][key] for key in ("library_id", "node_id"))):
        wire._fail()
    return value


def normalize_result(request, result):
    request = normalize_request(request)
    result = wire._copy(result, MAX_BYTES)
    wire._exact(result, RESULT_FIELDS)
    wire._version(result["format_version"])
    if result["kind"] != "source_continuity":
        wire._fail()
    for side in ("source", "target"):
        wire._identity(result[side + "_identity"])
        if result[side + "_identity"] != wire._peer(request[side]):
            wire._fail()
    if result["handshake"] != request["source"]["handshake"]:
        wire._fail()
    sync_snapshot._version(result["receiver_version"])
    if result["receiver_version"]["library_id"] != request["target"]["library_id"]:
        wire._fail()
    wire._enum(result["state"], ("absent", "current", "historical", "unknown"))
    if result["state"] in {"absent", "unknown"}:
        if result["continuity"] is not None or result["head"] is not None:
            wire._fail()
    else:
        value = result["continuity"]
        wire._exact(value, ("format_version", "continuity_id", "contract_sha256", "target_epoch"))
        wire._version(value["format_version"])
        wire.queue._uuid(value["continuity_id"], "continuity_id")
        wire._sha(value["contract_sha256"])
        wire.queue._integer(value["target_epoch"], "target_epoch")
        if (value["target_epoch"] == result["receiver_version"]["library_epoch"]) != (result["state"] == "current"):
            wire._fail()
        wire._exact(result["head"], ("ordinal", "sha256"))
        wire.queue._integer(result["head"]["ordinal"], "ordinal", minimum=0)
        wire._sha(result["head"]["sha256"])
    return result
