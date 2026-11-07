"""Fixed RP1 declarations and remaining-copy planning, never I/O authority.

Amounts distinguish known-byte lower bounds from future control capacities and
in-flight read headroom. No declaration occupies a producer slot, accounts an
actual effect, observes a clock, acquires resources, or proves normal closure.
The replica ledger must independently enforce every actual I/O/resource ticket.
"""
from __future__ import annotations


PROFILE = "replica-rp1"

_PRODUCER = (
    ("max_read_bytes", 2 * 1024**4),
    ("source_max_read_bytes", 1024**4),
    ("max_write_bytes", 256 * 1024**3),
    ("deadline_ms", 1_800_000),
    ("chunk_bytes", 131_072),
    ("max_failure_bytes", 16_384),
    ("max_resources", 1_000_000),
)
_READONLY = (
    ("max_read_bytes", 1024**4),
    ("source_max_read_bytes", 1024**4),
    ("max_write_bytes", 0),
    ("deadline_ms", 1_800_000),
    ("chunk_bytes", 131_072),
    ("max_failure_bytes", 0),
    ("max_resources", 1_000_000),
)
_STRUCTURE = (
    ("max_package_bytes", 128 * 1024**3),
    ("max_package_entries", 400_064),
    ("max_request_bytes", 1024**2),
    ("max_fact_bytes", 128 * 1024**2),
    ("max_path_components", 128),
    ("max_node_bytes", 4096),
    ("max_json_depth", 64),
    ("max_json_nodes", 4_000_000),
    ("max_json_integer_digits", 4300),
)
_CONTROL_CAPS = (
    ("request", 1024**2),
    ("attempt", 16_384),
    ("source_selection", 128 * 1024**2),
    ("copy_attempt", 8192),
    ("copy_record", 16_384),
    ("replica", 128 * 1024**2),
    ("source_closed", 16_384),
    ("target_closed", 16_384),
    ("manifest", 128 * 1024**2),
    ("receipt", 16_384),
    ("failure", 16_384),
)
_SELECTION_FIELDS = frozenset({"package_file_count", "package_directory_count", "package_bytes",
    "request_bytes", "attempt_bytes", "source_selection_bytes", "predecessor_attempt_bytes", "observer_node_bytes"})
_DEMAND_FIELDS = frozenset({"format_version", "profile", "stage", "inputs", "known_minimum", "capacity_reserve",
    "read_headroom", "failure_write_reserve_bytes", "peak_additional_resources", "read_bytes", "source_read_bytes", "write_bytes"})


class AuthorityReplicaPolicyError(ValueError):
    def __init__(self, suffix):
        self.code = "AUTHORITY_REPLICA_POLICY1_" + suffix
        super().__init__(self.code)


Error = AuthorityReplicaPolicyError


def _fail(suffix):
    raise Error(suffix) from None


def _exact(value, fields):
    if type(value) is not dict:
        _fail("TYPE_INVALID")
    if len(value) != len(fields):
        _fail("FIELDS_INVALID")
    # Inspect all key types before set construction or dictionary lookup so a
    # colliding key subclass cannot run its comparison/hash hooks here.
    if any(type(key) is not str for key in value):
        _fail("TYPE_INVALID")
    if set(value) != fields:
        _fail("FIELDS_INVALID")


def _same(value, expected, suffix):
    """Compare a bounded, closed schema without coercing caller objects."""
    if type(value) is not type(expected):
        _fail("TYPE_INVALID")
    if type(expected) is dict:
        _exact(value, set(expected))
        for key, wanted in expected.items():
            _same(value[key], wanted, suffix)
    elif value != expected:
        _fail(suffix)


def _slot(value):
    if type(value) is not int:
        _fail("TYPE_INVALID")
    if value not in (1, 2):
        _fail("SLOT_INVALID")
    return value


def _uint(value, maximum, *, minimum=0):
    if type(value) is not int:
        _fail("TYPE_INVALID")
    if value < minimum:
        _fail("SELECTION_INVALID")
    if value > maximum:
        _fail("LIMIT")
    return value


def rp1_declaration() -> dict:
    """Return fresh fixed data; no dictionary is an occupied-slot ticket."""
    return {"format_version": 1, "profile": PROFILE, "max_attempts": 2,
        "producer": dict(_PRODUCER), "readonly": dict(_READONLY),
        "structure": dict(_STRUCTURE), "control_caps": dict(_CONTROL_CAPS)}


def normalize_rp1(value: dict) -> dict:
    expected = rp1_declaration()
    _same(value, expected, "RP1_MISMATCH")
    return expected


def runtime_limits(mode: str) -> dict:
    """Return one invocation's limits, never the two-slot allocation total."""
    if type(mode) is not str:
        _fail("TYPE_INVALID")
    if mode == "producer":
        return dict(_PRODUCER)
    if mode == "readonly":
        return dict(_READONLY)
    _fail("MODE_INVALID")


def slot_reservation(slot: int) -> dict:
    slot = _slot(slot)
    limits = dict(_PRODUCER)
    return {"profile": PROFILE, "slot": slot, "read_bytes": limits["max_read_bytes"],
        "source_read_bytes": limits["source_max_read_bytes"], "write_bytes": limits["max_write_bytes"],
        "active_ms": limits["deadline_ms"], "attempts": 1}


def allocation(occupied_slots: tuple) -> dict:
    if type(occupied_slots) is not tuple:
        _fail("TYPE_INVALID")
    if len(occupied_slots) > 2:
        _fail("OCCUPANCY_INVALID")
    if any(type(slot) is not int for slot in occupied_slots):
        _fail("TYPE_INVALID")
    if occupied_slots not in ((), (1,), (1, 2)):
        _fail("OCCUPANCY_INVALID")
    count, limits = len(occupied_slots), dict(_PRODUCER)
    return {"profile": PROFILE, "occupied_slots": list(occupied_slots),
        "reserved": {"read_bytes": count * limits["max_read_bytes"],
            "source_read_bytes": count * limits["source_max_read_bytes"],
            "write_bytes": count * limits["max_write_bytes"],
            "active_ms": count * limits["deadline_ms"], "attempts": count},
        # Attempts run serially; this is not multiplied by the occupied slots.
        "max_resources": limits["max_resources"]}


def _demand(*, stage, inputs, known, reserved, source_headroom, resources):
    limits = dict(_PRODUCER)
    headroom = {"read_bytes": limits["chunk_bytes"], "source_read_bytes": source_headroom}
    failure = limits["max_failure_bytes"]
    return {"format_version": 1, "profile": PROFILE, "stage": stage, "inputs": inputs,
        "known_minimum": known, "capacity_reserve": reserved, "read_headroom": headroom,
        "failure_write_reserve_bytes": failure, "peak_additional_resources": resources,
        "read_bytes": known["read_bytes"] + reserved["read_bytes"] + headroom["read_bytes"],
        "source_read_bytes": known["source_read_bytes"] + reserved["source_read_bytes"] + headroom["source_read_bytes"],
        "write_bytes": known["write_bytes"] + reserved["write_bytes"] + failure}


def bootstrap_demand(slot: int) -> dict:
    """Plan bounded admission before source content is known or copied."""
    slot = _slot(slot)
    caps, structure = dict(_CONTROL_CAPS), dict(_STRUCTURE)
    request, attempt, node = caps["request"], caps["attempt"], structure["max_node_bytes"]
    if slot == 1:
        reads, writes = 2 * request + 2 * attempt + node, request + attempt
    else:
        # Old request + predecessor attempt + old receipt probe, followed by
        # the new attempt's temp/formal readbacks. No old request rewrite.
        reads, writes = request + 4 * attempt + node, attempt
    return _demand(stage="bootstrap", inputs={"slot": slot},
        known={"read_bytes": 0, "source_read_bytes": 0, "write_bytes": 0},
        reserved={"read_bytes": reads, "source_read_bytes": 0, "write_bytes": writes},
        source_headroom=0, resources=4096)


def _selection(value):
    _exact(value, _SELECTION_FIELDS)
    structure, caps = dict(_STRUCTURE), dict(_CONTROL_CAPS)
    files = _uint(value["package_file_count"], structure["max_package_entries"])
    directories = _uint(value["package_directory_count"], structure["max_package_entries"], minimum=1)
    if files + directories > structure["max_package_entries"]:
        _fail("LIMIT")
    size = _uint(value["package_bytes"], structure["max_package_bytes"])
    if files == 0 and size != 0:
        _fail("SELECTION_INVALID")
    copied = {"package_file_count": files, "package_directory_count": directories, "package_bytes": size}
    for field, maximum, minimum in (
            ("request_bytes", caps["request"], 1),
            ("attempt_bytes", caps["attempt"], 1),
            ("source_selection_bytes", caps["source_selection"], 1),
            ("predecessor_attempt_bytes", caps["attempt"], 0),
            ("observer_node_bytes", structure["max_node_bytes"], 1)):
        copied[field] = _uint(value[field], maximum, minimum=minimum)
    return copied


def payload_demand(selection: dict) -> dict:
    """Plan remaining copy effects; the completed source scan stays in spent.

    The producer must obtain these sizes from its actual completed source
    selection. Calling this pure function does not establish that provenance.
    Future control maxima are capacities, not observed or minimum byte counts.
    """
    selected = _selection(selection)
    caps, limits = dict(_CONTROL_CAPS), dict(_PRODUCER)
    count, directories, size = (selected[key] for key in
        ("package_file_count", "package_directory_count", "package_bytes"))
    prior = sum(selected[key] for key in ("request_bytes", "attempt_bytes",
        "source_selection_bytes", "predecessor_attempt_bytes"))
    node = selected["observer_node_bytes"]
    copies = count * (caps["copy_attempt"] + caps["copy_record"])
    late = sum(caps[key] for key in ("replica", "source_closed", "target_closed", "manifest", "receipt"))
    # Source copy + two terminal passes: 3S. Target readback + whole-tree
    # readback + two terminal passes + publisher open + two terminal passes:
    # 7S. The source's first S was already charged before this frontier.
    known = {"read_bytes": 10 * size + 5 * prior + 5 * node,
        "source_read_bytes": 3 * size, "write_bytes": size}
    reserved = {"read_bytes": 7 * copies + 4 * late,
        "source_read_bytes": 0, "write_bytes": copies + late}
    return _demand(stage="before_payload", inputs=selected, known=known, reserved=reserved,
        source_headroom=limits["chunk_bytes"], resources=3 * count + directories + 64)


def normalize_demand(value: dict) -> dict:
    """Recompute every field; never clamp a demand to the runtime quota."""
    _exact(value, _DEMAND_FIELDS)
    _same(value["format_version"], 1, "DEMAND_MISMATCH")
    _same(value["profile"], PROFILE, "DEMAND_MISMATCH")
    if type(value["stage"]) is not str:
        _fail("TYPE_INVALID")
    if value["stage"] == "bootstrap":
        _exact(value["inputs"], {"slot"})
        expected = bootstrap_demand(value["inputs"]["slot"])
    elif value["stage"] == "before_payload":
        expected = payload_demand(value["inputs"])
    else:
        _fail("DEMAND_MISMATCH")
    _same(value, expected, "DEMAND_MISMATCH")
    return expected
