"""Independent pure raw2 control-cache candidate; no source or authority claims.

The closed path grammar is a local input-shape check.  A later original native
source must establish actual formal kind, identity, census and all its original
budget/first/normal-close gates.  This module performs no IO and accepts neither
a reader, a ledger, a callback nor a caller-supplied digest/completion flag.

Progress means local work reached a boundary, never that a deadline passed.
The fixed later source must check its SAME original ledger before advancing and
after each yield.  Snapshot/sort, json.loads, single encoder-piece production
and tuple finalization are not preemptible; this is not a hard-real-time bound.
Ordinary frozen records avoid mutable JSON leakage.  They are not an authority
kernel resistant to object.__setattr__, class or module modification.
"""

from __future__ import annotations

import bisect
import codecs
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Generator


_CHUNK_BYTES = 131072
_ENCODE_CHARS = 32768
_SHAPE_INTERVAL = 32768
_MAX_REQUEST_BYTES = 1048576
_MAX_CONTROL_BYTES = 134217728
_MAX_FACTS = 300032
_MAX_TOTAL_RAW_BYTES = 137438953472
_MAX_DEPTH = 64
_MAX_NODES = 4000000
_MAX_INTEGER_DIGITS = 4300
_MAX_CONTROL_KEY_CHARS = 80

_ROOT_CONTROLS = frozenset((
    "request.json", "source-metadata.json", "source-projection.json",
    "selection.json",
))
_RUN_CONTROLS = frozenset((
    "run-attempt.json", "capsule.json", "source-verified.json",
    "manifest.json", "receipt.json",
))
_RUN_PATH = re.compile(r"runs/(01|02)/([^/]+)\Z")
_ATTEMPT_PATH = re.compile(
    r"runs/(01|02)/copy-attempts/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.json\Z"
)
_RECORD_PATH = re.compile(r"runs/(01|02)/copy-records/f[0-9]{8}\.json\Z")
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"
_DELIMITERS = frozenset(" \t\r\n,]}:")
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


class PreflightError(ValueError):
    """Bounded local rejection; messages omit the raw JSON contents."""

    def __init__(self, code: str, relative_key: str | None = None) -> None:
        self.code = code
        self.relative_key = relative_key
        super().__init__(code if relative_key is None else f"{code}: {relative_key}")


@dataclass(frozen=True, slots=True)
class Progress:
    phase: str
    relative_key: str | None
    input_offset: int = 0
    local_nodes: int = 0


@dataclass(frozen=True, slots=True)
class JsonObject:
    items: tuple[tuple[str, JsonValue], ...]


@dataclass(frozen=True, slots=True)
class JsonArray:
    values: tuple[JsonValue, ...]


JsonValue = JsonObject | JsonArray | str | int | bool | None


@dataclass(frozen=True, slots=True)
class LocalWork:
    lexical_nodes: int
    shape_nodes: int
    maximum_depth: int
    preflight_input_bytes: int
    decoded_codepoints: int
    lexical_character_visits: int
    parse_calls: int
    parse_input_bytes: int
    canonical_output_bytes: int
    pure_hash_input_bytes: int


@dataclass(frozen=True, slots=True)
class RawFact:
    relative_key: str
    raw: bytes
    length: int
    sha256: str
    document: JsonObject
    work: LocalWork


@dataclass(frozen=True, slots=True)
class _RawFactCache:
    _keys: tuple[str, ...]
    _facts: tuple[RawFact, ...]

    def __post_init__(self) -> None:
        # Direct ordinary construction is possible.  Validate operational
        # fields once so lookup/fact_bytes cannot iterate or compare unvalidated
        # caller objects.  This is not proof that caller-made facts are genuine;
        # only build_cache_steps establishes digest/local shape/work semantics.
        if type(self._keys) is not tuple or type(self._facts) is not tuple:
            raise TypeError("exact cache tuples required")
        if len(self._keys) != len(self._facts) or len(self._keys) > _MAX_FACTS:
            raise ValueError("cache key/fact count mismatch")
        for key in self._keys:
            if type(key) is not str:
                raise TypeError("exact cache string keys required")
        for fact in self._facts:
            if type(fact) is not RawFact:
                raise TypeError("exact RawFact required")
            if type(fact.relative_key) is not str or type(fact.raw) is not bytes:
                raise TypeError("exact fact key/bytes required")
            if type(fact.length) is not int or type(fact.sha256) is not str:
                raise TypeError("exact fact length/digest fields required")
            if type(fact.document) is not JsonObject or type(fact.work) is not LocalWork:
                raise TypeError("exact fact shape/work records required")
            if fact.length != len(fact.raw) or re.fullmatch(r"[0-9a-f]{64}", fact.sha256) is None:
                raise ValueError("fact length/digest representation mismatch")
        for index, key in enumerate(self._keys):
            if self._facts[index].relative_key != key:
                raise ValueError("cache key/fact binding mismatch")
            if index and self._keys[index - 1] >= key:
                raise ValueError("cache keys must be unique and sorted")


def _control_cap(relative_key: str) -> int:
    """Only the exact closed path grammar selects kind and the request cap."""
    if len(relative_key) > _MAX_CONTROL_KEY_CHARS:
        raise PreflightError("control_path")
    if relative_key in _ROOT_CONTROLS:
        return _MAX_REQUEST_BYTES if relative_key == "request.json" else _MAX_CONTROL_BYTES
    run = _RUN_PATH.fullmatch(relative_key)
    if run is not None and run.group(2) in _RUN_CONTROLS:
        return _MAX_CONTROL_BYTES
    attempt = _ATTEMPT_PATH.fullmatch(relative_key)
    if attempt is not None and attempt.group(2) != _ZERO_UUID:
        return _MAX_CONTROL_BYTES
    if _RECORD_PATH.fullmatch(relative_key) is not None:
        return _MAX_CONTROL_BYTES
    raise PreflightError("control_path")


def _capture_rows(control_bytes: dict[str, bytes]) -> list[tuple[str, bytes]]:
    if type(control_bytes) is not dict:
        raise PreflightError("exact_dict_required")
    if len(control_bytes) > _MAX_FACTS:
        raise PreflightError("fact_count_limit")
    rows: list[tuple[str, bytes]] = []
    total = 0
    # Never copy or perform key equality before every individual key is exact.
    # A concurrent mutation during enumeration raises normally; no retry/adopt.
    for key, raw in dict.items(control_bytes):
        if type(key) is not str or type(raw) is not bytes:
            raise PreflightError("exact_key_and_bytes_required")
        cap = _control_cap(key)
        if len(raw) > cap:
            raise PreflightError("control_bytes_limit", key)
        total += len(raw)
        if total > _MAX_TOTAL_RAW_BYTES:
            raise PreflightError("cached_raw_bytes_limit")
        rows.append((key, raw))
    rows.sort(key=lambda row: row[0].encode("utf-8", "strict"))
    return rows


class _Lexer:
    """Streaming lexical limits.  Complete JSON grammar stays with json.loads."""

    __slots__ = (
        "key", "state", "stack", "nodes", "maximum_depth", "digits",
        "literal", "literal_at", "unicode_hex", "codepoints", "character_visits",
    )

    def __init__(self, key: str) -> None:
        self.key = key
        self.state = "between"
        self.stack: list[str] = []
        self.nodes = 0
        self.maximum_depth = 0
        self.digits = 0
        self.literal = ""
        self.literal_at = 0
        self.unicode_hex = ""
        self.codepoints = 0
        self.character_visits = 0

    def _node(self) -> None:
        self.nodes += 1
        if self.nodes > _MAX_NODES:
            raise PreflightError("json_nodes_limit", self.key)
        # Match original _snapshot: root depth=0; every child/object key +1.
        # At token start the stack holds the token's ancestors.  Empty containers
        # at depth64 are valid, but their keys or scalar children at65 are not.
        depth = len(self.stack)
        if depth > _MAX_DEPTH:
            raise PreflightError("json_depth_limit", self.key)
        self.maximum_depth = max(self.maximum_depth, depth)

    def feed(self, text: str) -> None:
        self.codepoints += len(text)
        index = 0
        while index < len(text):
            self.character_visits += 1
            char = text[index]
            if self.state == "string":
                if char == '"':
                    self.state = "between"
                elif char == "\\":
                    self.state = "escape"
                elif ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF:
                    raise PreflightError("json_string_character", self.key)
            elif self.state == "escape":
                if char == "u":
                    self.unicode_hex = ""
                    self.state = "unicode"
                elif char in '"\\/bfnrt':
                    self.state = "string"
                else:
                    raise PreflightError("json_escape", self.key)
            elif self.state == "unicode":
                if char not in _HEX_DIGITS:
                    raise PreflightError("json_unicode_escape", self.key)
                self.unicode_hex += char
                if len(self.unicode_hex) == 4:
                    if 0xD800 <= int(self.unicode_hex, 16) <= 0xDFFF:
                        raise PreflightError("json_surrogate", self.key)
                    self.state = "string"
            elif self.state == "literal":
                if self.literal_at == len(self.literal):
                    if char not in _DELIMITERS:
                        raise PreflightError("json_literal", self.key)
                    self.state = "between"
                    continue
                if char != self.literal[self.literal_at]:
                    raise PreflightError("json_literal", self.key)
                self.literal_at += 1
            elif self.state == "number":
                if "0" <= char <= "9":
                    self.digits += 1
                    if self.digits > _MAX_INTEGER_DIGITS:
                        raise PreflightError("integer_digits_limit", self.key)
                else:
                    if self.digits == 0 or char not in _DELIMITERS:
                        raise PreflightError("integer_json_required", self.key)
                    self.state = "between"
                    continue
            else:
                if char in " \t\r\n,:":
                    pass
                elif char in "{[":
                    self._node()
                    self.stack.append(char)
                elif char in "}]":
                    expected = "{" if char == "}" else "["
                    if not self.stack or self.stack.pop() != expected:
                        raise PreflightError("json_container", self.key)
                elif char == '"':
                    self._node()
                    self.state = "string"
                elif char in "tfn":
                    self._node()
                    self.literal = {"t": "true", "f": "false", "n": "null"}[char]
                    self.literal_at = 1
                    self.state = "literal"
                elif char == "-" or "0" <= char <= "9":
                    self._node()
                    self.digits = 0 if char == "-" else 1
                    self.state = "number"
                else:
                    raise PreflightError("integer_json_required", self.key)
            index += 1

    def finish(self) -> None:
        if self.state == "number" and self.digits > 0:
            self.state = "between"
        elif self.state == "literal" and self.literal_at == len(self.literal):
            self.state = "between"
        if self.state != "between" or self.stack:
            raise PreflightError("json_truncated", self.key)


def _pairs_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if type(key) is not str:
            raise PreflightError("json_object_key")
        if key in result:
            raise PreflightError("json_duplicate_key")
        result[key] = value
    return result


def _parse_integer(text: str) -> int:
    digits = len(text) - (1 if text.startswith("-") else 0)
    if digits > _MAX_INTEGER_DIGITS:
        raise PreflightError("integer_digits_limit")
    return int(text)


def _reject_noninteger(text: str) -> None:
    raise PreflightError("integer_json_required")


def _preflight_steps(raw: bytes, key: str) -> Generator[Progress, None, _Lexer]:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    lexer = _Lexer(key)
    for start in range(0, len(raw), _CHUNK_BYTES):
        stop = min(start + _CHUNK_BYTES, len(raw))
        try:
            decoded = decoder.decode(raw[start:stop], final=False)
        except UnicodeDecodeError as error:
            raise PreflightError("strict_utf8", key) from error
        lexer.feed(decoded)
        yield Progress("preflight_chunk", key, stop, lexer.nodes)
    try:
        tail = decoder.decode(b"", final=True)
    except UnicodeDecodeError as error:
        raise PreflightError("strict_utf8", key) from error
    lexer.feed(tail)
    lexer.finish()
    return lexer


@dataclass(slots=True)
class _ShapeCounter:
    nodes: int = 0


def _shape_tick(counter: _ShapeCounter, key: str) -> Generator[Progress, None, None]:
    counter.nodes += 1
    if counter.nodes > _MAX_NODES:
        raise PreflightError("json_nodes_limit", key)
    if counter.nodes % _SHAPE_INTERVAL == 0:
        yield Progress("shape_chunk", key, 0, counter.nodes)


def _freeze_steps(
    value: object, key: str, counter: _ShapeCounter, depth: int = 0,
) -> Generator[Progress, None, JsonValue]:
    yield from _shape_tick(counter, key)
    if depth > _MAX_DEPTH:
        raise PreflightError("json_depth_limit", key)
    kind = type(value)
    if kind is dict:
        items: list[tuple[str, JsonValue]] = []
        for name, child in dict.items(value):
            if type(name) is not str:
                raise PreflightError("json_object_key", key)
            if depth + 1 > _MAX_DEPTH:
                raise PreflightError("json_depth_limit", key)
            yield from _shape_tick(counter, key)
            frozen = yield from _freeze_steps(child, key, counter, depth + 1)
            items.append((name, frozen))
        return JsonObject(tuple(items))
    if kind is list:
        values: list[JsonValue] = []
        for child in value:
            values.append((yield from _freeze_steps(child, key, counter, depth + 1)))
        return JsonArray(tuple(values))
    if kind is str or kind is int or kind is bool or value is None:
        return value
    raise PreflightError("integer_json_required", key)


def _canonical_steps(
    value: dict[str, object], raw: bytes, key: str,
) -> Generator[Progress, None, int]:
    encoder = json.JSONEncoder(
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    offset = 0
    since_progress = 0
    for piece in encoder.iterencode(value):
        for start in range(0, len(piece), _ENCODE_CHARS):
            encoded = piece[start:start + _ENCODE_CHARS].encode("utf-8", "strict")
            piece_at = 0
            while piece_at < len(encoded):
                count = min(len(encoded) - piece_at, _CHUNK_BYTES - since_progress)
                stop = offset + count
                if stop > len(raw) or raw[offset:stop] != encoded[piece_at:piece_at + count]:
                    raise PreflightError("raw2_canonical_no_lf", key)
                offset = stop
                piece_at += count
                since_progress += count
                if since_progress == _CHUNK_BYTES:
                    yield Progress("canonical_chunk", key, offset)
                    since_progress = 0
    if offset != len(raw):
        raise PreflightError("raw2_canonical_no_lf", key)
    if since_progress:
        yield Progress("canonical_chunk", key, offset)
    return offset


def _hash_steps(raw: bytes, key: str) -> Generator[Progress, None, str]:
    digest = hashlib.sha256()
    for start in range(0, len(raw), _CHUNK_BYTES):
        stop = min(start + _CHUNK_BYTES, len(raw))
        digest.update(raw[start:stop])
        yield Progress("hash_chunk", key, stop)
    return digest.hexdigest()


def build_cache_steps(
    control_bytes: dict[str, bytes],
) -> Generator[Progress, None, _RawFactCache]:
    """Capture a pure subset snapshot, validate raw bytes, return frozen facts.

    Type annotations are descriptive: all ingress types are checked for exact
    builtins.  Initial validation/capture finishes before the first Progress.
    Empty/partial maps are allowed and confer no complete-package conclusion.
    """
    rows = _capture_rows(control_bytes)
    yield Progress("snapshot_complete", None)
    facts: list[RawFact] = []
    for key, raw in rows:
        yield Progress("before_preflight", key)
        lexer = yield from _preflight_steps(raw, key)
        yield Progress("before_parse", key, len(raw), lexer.nodes)
        try:
            parsed = json.loads(
                raw, object_pairs_hook=_pairs_object, parse_int=_parse_integer,
                parse_float=_reject_noninteger, parse_constant=_reject_noninteger,
            )
        except json.JSONDecodeError as error:
            raise PreflightError("json_syntax", key) from error
        if type(parsed) is not dict:
            raise PreflightError("json_root_object", key)
        yield Progress("after_parse", key, len(raw), lexer.nodes)
        counter = _ShapeCounter()
        frozen = yield from _freeze_steps(parsed, key, counter)
        if type(frozen) is not JsonObject or counter.nodes != lexer.nodes:
            raise PreflightError("local_shape_consistency", key)
        yield Progress("before_canonical", key, len(raw), counter.nodes)
        canonical_bytes = yield from _canonical_steps(parsed, raw, key)
        yield Progress("before_hash", key, len(raw), counter.nodes)
        digest = yield from _hash_steps(raw, key)
        facts.append(RawFact(
            key, raw, len(raw), digest, frozen,
            LocalWork(
                lexer.nodes, counter.nodes, lexer.maximum_depth, len(raw),
                lexer.codepoints, lexer.character_visits, 1, len(raw),
                canonical_bytes, len(raw),
            ),
        ))
        # Only the immutable local shape and raw bytes survive into the cache.
        del parsed
        yield Progress("fact_complete", key, len(raw), counter.nodes)
    cache = _RawFactCache(tuple(row[0] for row in rows), tuple(facts))
    yield Progress("cache_complete", None)
    return cache


def lookup(cache: _RawFactCache, relative_key: str) -> RawFact:
    if type(cache) is not _RawFactCache:
        raise TypeError("exact cache required")
    if type(relative_key) is not str:
        raise TypeError("exact string key required")
    index = bisect.bisect_left(cache._keys, relative_key)
    if index == len(cache._keys) or cache._keys[index] != relative_key:
        raise KeyError(relative_key)
    return cache._facts[index]


def fact_bytes(cache: _RawFactCache) -> dict[str, bytes]:
    if type(cache) is not _RawFactCache:
        raise TypeError("exact cache required")
    return {fact.relative_key: fact.raw for fact in cache._facts}
