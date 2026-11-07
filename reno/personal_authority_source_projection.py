"""Pure source-only projection of the declared authority raw census.

Equality concerns supplied metadata, not live files, complete capture, an
owner, or write permission. Operation/copy/history-link evidence remains in
the original capsule and is validated before it may be projected away.
"""
from __future__ import annotations

from functools import wraps

from . import personal_authority_preservation_contract as capsule2


FORMAT_VERSION = 1
KIND = "personal_authority_source_projection"
SCOPE = "personal-authority-raw-source-comparison-v1"
CAPSULE_FORMAT_VERSION = 2
CAPSULE_SCOPE = "personal-authority-raw-census-v2"
LAYOUT_PROFILE = "namespace2-store2-extension3-enrollment1-r729"
LAYOUT_SOURCE_SHA256 = "7965e1b9d8e4b3a3336e88ec6545f4448ad6cb208f16e653cb81310b01f71aeb"

# Public convenience only; the old immutable ceilings remain authoritative.
DEFAULT_LIMITS = dict(capsule2._DEFAULT_ITEMS)
_TOP = frozenset({"format_version", "kind", "scope", "capsule_format_version",
    "capsule_scope", "layout_profile", "layout_source_sha256", "source_context",
    "source_coverage", "directories", "files", "unresolved"})
_SOURCE_FIELDS = ("source_context", "directories", "files", "unresolved")
_FIXED = (("kind", KIND), ("scope", SCOPE), ("capsule_scope", CAPSULE_SCOPE),
          ("layout_profile", LAYOUT_PROFILE), ("layout_source_sha256", LAYOUT_SOURCE_SHA256))


class AuthoritySourceProjectionError(ValueError):
    def __init__(self, suffix):
        self.code = "AUTHORITY_SOURCE_PROJECTION1_" + suffix
        super().__init__(self.code)


Error = AuthoritySourceProjectionError


def _fail(suffix):
    raise AuthoritySourceProjectionError(suffix) from None


def _checked(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except AuthoritySourceProjectionError:
            raise
        except capsule2.AuthorityPreservationContractError as exc:
            prefix = "AUTHORITY_PRESERVATION2_"
            _fail(exc.code[len(prefix):] if type(exc.code) is str and exc.code.startswith(prefix)
                  else "TYPE_INVALID")
        except (ValueError, TypeError, KeyError, IndexError, AttributeError,
                UnicodeError, RecursionError, OverflowError, RuntimeError):
            _fail("TYPE_INVALID")
    return checked


def _normalize(value, limits):
    copied = capsule2._snapshot(value, limits)
    capsule2._exact(copied, _TOP)
    if (type(copied["format_version"]) is not int or copied["format_version"] != FORMAT_VERSION
            or type(copied["capsule_format_version"]) is not int
            or copied["capsule_format_version"] != CAPSULE_FORMAT_VERSION
            or any(copied[key] != expected for key, expected in _FIXED)):
        _fail("VERSION_INVALID")
    coverage = copied["source_coverage"]
    capsule2._exact(coverage, {"declared_tree", "reason"})
    # Only the original source validators consume this local view. It is not
    # a capsule, persisted evidence, or a source with a fabricated operation.
    # The empty copy portion lets the original coverage rules validate the
    # source declarations without adding any copy claim to the projection.
    source = {key: copied[key] for key in _SOURCE_FIELDS}
    source.update(coverage={"declared_tree": coverage["declared_tree"],
                            "reason": coverage["reason"], "declared_copies": "none"},
                  copies=[])
    capsule2._tree(source, limits)
    capsule2._coverage(source)
    return copied


def _from_capsule(value, limits):
    # Validate the ENTIRE original declaration first, including the fields
    # excluded from stable source comparison. Invalid evidence cannot vanish.
    original = capsule2.normalize(value, limits=limits)
    projection = {"format_version": FORMAT_VERSION, "kind": KIND, "scope": SCOPE,
        "capsule_format_version": CAPSULE_FORMAT_VERSION, "capsule_scope": CAPSULE_SCOPE,
        "layout_profile": original["layout_profile"],
        "layout_source_sha256": original["layout_source_sha256"],
        "source_coverage": {"declared_tree": original["coverage"]["declared_tree"],
                            "reason": original["coverage"]["reason"]},
        **{key: original[key] for key in _SOURCE_FIELDS}}
    return _normalize(projection, limits)


@_checked
def from_capsule(value, *, limits=None):
    """Validate the full capsule, then return detached source declarations."""
    return _from_capsule(value, capsule2._limits(limits))


@_checked
def normalize(value, *, limits=None):
    """Validate all projection fields without filesystem or owner claims."""
    return _normalize(value, capsule2._limits(limits))


@_checked
def encode(value, *, limits=None):
    """Return canonical new projection bytes; never rewrite a raw artifact."""
    return capsule2._canonical(_normalize(value, capsule2._limits(limits)))


@_checked
def decode(raw: bytes, *, limits=None):
    """Accept only canonical immutable bytes, with bounded strict parsing."""
    budget = capsule2._limits(limits)
    normalized = _normalize(capsule2._json(raw, budget), budget)
    if capsule2._canonical(normalized) != raw:
        _fail("JSON_NONCANONICAL")
    return normalized


@_checked
def cross_validate_capsule(projection, capsule, *, limits=None):
    """Compare two validated declarations; return no completion capability."""
    budget = capsule2._limits(limits)
    selected = _normalize(projection, budget)
    expected = _from_capsule(capsule, budget)
    if capsule2._canonical(selected) != capsule2._canonical(expected):
        _fail("CAPSULE_MISMATCH")
