"""Pure source-only projection of the declared authority raw census.

Equality concerns supplied metadata, not live files, complete capture, an
owner, or write permission. Operation/copy/history-link evidence remains in
the original capsule and is validated before it may be projected away.
"""
from __future__ import annotations

from functools import wraps

from . import personal_authority_preservation3_contract as capsule3


FORMAT_VERSION = 2
KIND = "personal_authority_source_projection"
SCOPE = "personal-authority-raw-source-comparison-v2"
CAPSULE_FORMAT_VERSION = 3
CAPSULE_SCOPE = "personal-authority-raw-census-v3"
LAYOUT_PROFILE = capsule3.LAYOUT_PROFILE
LAYOUT_SOURCE_SHA256 = capsule3.LAYOUT_SOURCE_SHA256

# Public convenience only; the old immutable ceilings remain authoritative.
DEFAULT_LIMITS = dict(capsule3._DEFAULT_ITEMS)
_TOP = frozenset({"format_version", "kind", "scope", "capsule_format_version",
    "capsule_scope", "layout_profile", "layout_source_sha256", "source_context",
    "source_coverage", "directories", "files", "unresolved"})
_SOURCE_FIELDS = ("source_context", "directories", "files", "unresolved")
_FIXED = (("kind", KIND), ("scope", SCOPE), ("capsule_scope", CAPSULE_SCOPE),
          ("layout_profile", LAYOUT_PROFILE), ("layout_source_sha256", LAYOUT_SOURCE_SHA256))


class AuthoritySourceProjectionError(ValueError):
    def __init__(self, suffix):
        self.code = "AUTHORITY_SOURCE_PROJECTION2_" + suffix
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
        except (capsule3.AuthorityPreservationContractError, capsule3.prior.AuthorityPreservationContractError) as exc:
            prefix = "AUTHORITY_PRESERVATION3_" if isinstance(exc, capsule3.AuthorityPreservationContractError) else "AUTHORITY_PRESERVATION2_"
            _fail(exc.code[len(prefix):] if type(exc.code) is str and exc.code.startswith(prefix)
                  else "TYPE_INVALID")
        except (ValueError, TypeError, KeyError, IndexError, AttributeError,
                UnicodeError, RecursionError, OverflowError, RuntimeError):
            _fail("TYPE_INVALID")
    return checked


def _normalize(value, limits):
    copied = capsule3._snapshot(value, limits)
    capsule3._exact(copied, _TOP)
    if (type(copied["format_version"]) is not int or copied["format_version"] != FORMAT_VERSION
            or type(copied["capsule_format_version"]) is not int
            or copied["capsule_format_version"] != CAPSULE_FORMAT_VERSION
            or any(copied[key] != expected for key, expected in _FIXED)):
        _fail("VERSION_INVALID")
    coverage = copied["source_coverage"]
    capsule3._exact(coverage, {"declared_tree", "reason"})
    # Only the original source validators consume this local view. It is not
    # a capsule, persisted evidence, or a source with a fabricated operation.
    # The empty copy portion lets the original coverage rules validate the
    # source declarations without adding any copy claim to the projection.
    source = {key: copied[key] for key in _SOURCE_FIELDS}
    source.update(coverage={"declared_tree": coverage["declared_tree"],
                            "reason": coverage["reason"], "declared_copies": "none"},
                  copies=[])
    capsule3._tree(source, limits)
    capsule3._coverage(source)
    return copied


def _from_capsule(value, limits):
    # Validate the ENTIRE original declaration first, including the fields
    # excluded from stable source comparison. Invalid evidence cannot vanish.
    original = capsule3.normalize(value, limits=limits)
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
    return _from_capsule(value, capsule3._limits(limits))


@_checked
def normalize(value, *, limits=None):
    """Validate all projection fields without filesystem or owner claims."""
    return _normalize(value, capsule3._limits(limits))


@_checked
def encode(value, *, limits=None):
    """Return canonical new projection bytes; never rewrite a raw artifact."""
    return capsule3._canonical(_normalize(value, capsule3._limits(limits)))


@_checked
def decode(raw: bytes, *, limits=None):
    """Accept only canonical immutable bytes, with bounded strict parsing."""
    budget = capsule3._limits(limits)
    normalized = _normalize(capsule3._json(raw, budget), budget)
    if capsule3._canonical(normalized) != raw:
        _fail("JSON_NONCANONICAL")
    return normalized


@_checked
def cross_validate_capsule(projection, capsule, *, limits=None):
    """Compare two validated declarations; return no completion capability."""
    budget = capsule3._limits(limits)
    selected = _normalize(projection, budget)
    expected = _from_capsule(capsule, budget)
    if capsule3._canonical(selected) != capsule3._canonical(expected):
        _fail("CAPSULE_MISMATCH")
