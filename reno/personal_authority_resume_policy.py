"""Pure fixed Q1 declarations for format-2 raw-preservation producers.

Slot occupancy must be established by the native admission factory. These
helpers neither inspect that factory nor authorize I/O. Once occupied, a slot
reserves its full quota: actual counters, receipt status and elapsed offline
time cannot refund or transfer it. Source reads are a subset of total reads.
"""
from types import MappingProxyType

from . import personal_authority_raw_preservation_contract as raw1


DIRECTORY = ".reno-authority-raw-preservation-v2"
PROFILE = "q1"
CLAIM_NAME = "resume-slot-02.claim"
ANCHOR_PREFIX = "admission-q1-sha256-"
MAX_REQUEST_BYTES = 1024**2

# Immutable protocol values; never derive limits from caller counters or from
# the mutable convenience default dictionaries exposed by other codecs.
_Q1 = (
    ("profile", "q1"), ("max_attempts", 2),
    ("max_read_bytes", 512 * 1024**3), ("max_write_bytes", 128 * 1024**3),
    ("source_max_read_bytes", 384 * 1024**3), ("active_ms", 120_000),
    ("slot_read_bytes", 256 * 1024**3), ("slot_write_bytes", 64 * 1024**3),
    ("slot_source_read_bytes", 192 * 1024**3), ("slot_active_ms", 60_000),
    ("max_request_bytes", 1024**2),
)
_SLOT = (
    ("read_bytes", 256 * 1024**3), ("write_bytes", 64 * 1024**3),
    ("source_read_bytes", 192 * 1024**3), ("active_ms", 60_000),
    ("attempts", 1),
)


class AuthorityResumePolicyError(ValueError):
    def __init__(self, suffix):
        self.code = "AUTHORITY_RAW_PRESERVATION2_POLICY_" + suffix
        super().__init__(self.code)


Error = AuthorityResumePolicyError


def _fail(suffix):
    raise Error(suffix) from None


def _slot(value):
    if type(value) is not int:
        _fail("TYPE_INVALID")
    if value not in (1, 2):
        _fail("SLOT_INVALID")
    return value


def _fields(value, expected):
    # Check key types before set comparison, membership, or dict lookup: a str
    # subclass with a colliding hash must not execute its equality hook here.
    if type(value) is not dict:
        _fail("TYPE_INVALID")
    if len(value) != len(expected):
        _fail("FIELDS_INVALID")
    if any(type(key) is not str for key in value):
        _fail("TYPE_INVALID")
    if set(value) != set(expected):
        _fail("FIELDS_INVALID")


def _fixed(value, expected):
    _fields(value, expected)
    for key, required in expected.items():
        actual = value[key]
        if type(actual) is not type(required):
            _fail("TYPE_INVALID")
        if actual != required:
            _fail("Q1_MISMATCH")
    return dict(expected)


def q1_declaration():
    """Return a detached wire declaration, not a budget or occupied slot."""
    return dict(_Q1)


def normalize_q1(value):
    """Accept only the exact fixed profile; custom producer quotas need a version."""
    return _fixed(value, dict(_Q1))


def slot_limits(slot):
    """Adapt one slot to the existing single-run OperationBudget engine.

    The internal max_attempts=1 does not authorize a new attempt. The outer
    format-2 admission separately proves the operation/claim was CREATE_NEW.
    Failure reserve and all original structural ceilings remain inside this
    slot's limits; unused capacity never moves to another slot.
    """
    _slot(slot)
    limits = raw1.default_limits()
    limits.update(source_max_read_bytes=192 * 1024**3,
                  max_read_bytes=256 * 1024**3,
                  max_write_bytes=64 * 1024**3, deadline_ms=60_000)
    return limits


def slot_reservation(slot):
    """Describe the whole quota reserved by an independently established slot."""
    return {"profile": "q1", "slot": _slot(slot),
            **{key: value for key, value in _SLOT if key != "attempts"}}


def normalize_reservation(value):
    """Validate a declaration without claiming that a native slot exists."""
    expected = slot_reservation(1)
    _fields(value, expected)
    expected["slot"] = _slot(value["slot"])
    return _fixed(value, expected)


def allocation(occupied_slots):
    """Return immutable reserved/remaining quotas for an exact ordered prefix.

    This pure arithmetic deliberately accepts no actual usage, failure,
    receipt, timestamp, or refund inputs. It is not an occupancy observation.
    """
    if type(occupied_slots) is not tuple:
        _fail("TYPE_INVALID")
    if len(occupied_slots) > 2:
        _fail("SLOTS_INVALID")
    if any(type(slot) is not int for slot in occupied_slots):
        _fail("TYPE_INVALID")
    if occupied_slots not in ((), (1,), (1, 2)):
        _fail("SLOTS_INVALID")
    count = len(occupied_slots)
    return MappingProxyType({
        "occupied_slots": occupied_slots,
        "reserved": MappingProxyType({key: value * count for key, value in _SLOT}),
        "remaining": MappingProxyType({key: value * (2 - count) for key, value in _SLOT}),
    })


def request_anchor_name(expected_sha256):
    """Build the metadata-only routing name; a matching name proves no bytes."""
    if type(expected_sha256) is not str:
        _fail("TYPE_INVALID")
    if (len(expected_sha256) != 64
            or any(char not in "0123456789abcdef" for char in expected_sha256)):
        _fail("SHA_INVALID")
    return ANCHOR_PREFIX + expected_sha256
