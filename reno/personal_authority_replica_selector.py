"""Pure replica source values, not native identities or authority.

Only the inspected CPython 3.12.10 pathlib representation is admitted.  This
module never follows a path, accesses a package, or calls a lifecycle guard.
Its private snapshots enforce input consistency; they are not a registry and
cannot make a caller-created value evidence of ownership or package validity.
"""
from __future__ import annotations

import builtins
from dataclasses import dataclass, field
import ntpath
import os
import pathlib
import posixpath
import sys
from types import CellType, FunctionType, MappingProxyType, ModuleType

from . import storage_lifecycle as lifecycle


_TYPE = type
_OBJECT_NEW = object.__new__
_OBJECT_GET = object.__getattribute__
_OBJECT_SET = object.__setattr__
_TYPE_GET = type.__getattribute__
_DICT_GET = dict.get
_STR = str
_MISSING = object()
_FIELDS = ("database", "media", "reports", "checkpoints")
_NAMESPACE = ".reno-authority-raw-preservation-v2"
_SENTINEL = "reno-selector-check"
_NATIVE_OS = os.name
_PATH_TYPE = pathlib.WindowsPath if _NATIVE_OS == "nt" else pathlib.PosixPath
_PROFILE_TYPE = lifecycle.StorageProfile
_LIFECYCLE_MODULE = lifecycle
_FLAVOUR = ntpath if _NATIVE_OS == "nt" else posixpath
_NORMAL_CASE = _FLAVOUR.normcase
_NORMAL_PATH = _FLAVOUR.normpath
_JOIN = _FLAVOUR.join
_DIRNAME = _FLAVOUR.dirname
_SUPPORTED = sys.implementation.name == "cpython" and sys.version_info[:3] == (3, 12, 10)
_IMPLEMENTATION = sys.implementation
_IMPLEMENTATION_TYPE = _TYPE(_IMPLEMENTATION)
_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {prefix + number for prefix in ("COM", "LPT") for number in "123456789¹²³"})


class AuthorityReplicaSelectorError(ValueError):
    def __init__(self, suffix):
        super().__init__("Replica source selection is not valid")
        self.code = "AUTHORITY_REPLICA_SELECTOR1_" + suffix


def _fail(suffix):
    raise AuthorityReplicaSelectorError(suffix) from None


def _dictionary(value, suffix):
    dictionary = _OBJECT_GET(value, "__dict__")
    if _TYPE(dictionary) is not dict or any(_TYPE(key) is not str for key in dictionary):
        _fail(suffix)
    return dictionary


def _class_dictionary(cls, suffix):
    dictionary = _TYPE_GET(cls, "__dict__")
    if (_TYPE(dictionary) is not MappingProxyType
            or any(_TYPE(key) is not str for key in dictionary)):
        _fail(suffix)
    return dictionary


def _capture_classes(classes, names):
    # Read descriptor objects from the original class dictionaries.  Do not
    # evaluate class attributes: a replacement descriptor could execute there.
    return tuple((cls, _TYPE_GET(cls, "__mro__"), tuple(
        (name, _class_dictionary(cls, "REPRESENTATION_UNSUPPORTED").get(name, _MISSING))
        for name in names)) for cls in classes)


def _check_classes(pins, suffix):
    for cls, original_mro, fields in pins:
        current_mro = _TYPE_GET(cls, "__mro__")
        if (len(current_mro) != len(original_mro)
                or any(a is not b for a, b in zip(current_mro, original_mro))):
            _fail(suffix)
        dictionary = _class_dictionary(cls, suffix)
        for name, expected in fields:
            if dictionary.get(name, _MISSING) is not expected:
                _fail(suffix)


# Complete semantic call graph used below: constructor -> init/with_segments;
# str/parts/name/parent -> drive/root/_tail -> _load_parts/_parse_path;
# parent -> _from_parsed_parts/_format_parsed_parts; joinpath -> with_segments.
# Pins cover inherited lookup and absence of shadow attributes at every MRO
# level, including original slot descriptors and object access methods.  I/O
# methods such as stat/open/resolve are deliberately neither used nor pinned.
_PATH_NAMES = (
    "__new__", "__init__", "__getattribute__", "__getattr__", "__setattr__",
    "__class__", "__str__", "_flavour", "_raw_paths", "_str", "_drv", "_root",
    "_tail_cached", "_tail", "drive", "root", "parts", "name", "parent",
    "with_segments", "_load_parts", "_parse_path", "_format_parsed_parts",
    "_from_parsed_parts", "joinpath",
)
_PATH_CLASS_PINS = _capture_classes(_TYPE_GET(_PATH_TYPE, "__mro__"), _PATH_NAMES)
_PROFILE_CLASS_PINS = _capture_classes(_TYPE_GET(_PROFILE_TYPE, "__mro__"), (
    "identity", "roots", "__dict__", "__getattribute__", "__getattr__", *_FIELDS))


def _capture_module(module, names):
    dictionary = _dictionary(module, "REPRESENTATION_UNSUPPORTED")
    return module, tuple((name, _DICT_GET(dictionary, name, _MISSING)) for name in names)


# These are the actual pure dependencies of the pinned pathlib functions and
# selected ntpath/posixpath functions on the inspected interpreter.  Windows
# normcase uses LCMapStringEx for text case mapping, not a filesystem call.
_MODULE_PINS = (
    _capture_module(pathlib, ("PurePath", "Path", "PureWindowsPath", "PurePosixPath",
        "WindowsPath", "PosixPath", "os", "sys", "ntpath", "posixpath")),
    _capture_module(os, ("name", "path", "fspath")),
    _capture_module(sys, ("implementation", "version_info", "intern")),
    _capture_module(ntpath, ("normcase", "normpath", "join", "splitroot", "splitdrive",
        "dirname", "split", "_get_bothseps", "sep", "altsep", "os", "sys",
        "_LCMapStringEx", "_LOCALE_NAME_INVARIANT", "_LCMAP_LOWERCASE")),
    _capture_module(posixpath, ("normcase", "normpath", "join", "splitroot", "splitdrive",
        "dirname", "_get_sep", "sep", "altsep", "os")),
    _capture_module(builtins, ("type", "object", "str", "list", "tuple", "len",
        "isinstance", "map", "super", "AttributeError", "TypeError")),
)


def _function_pins(class_pins, module_pins=()):
    functions = []
    for _, _, fields in class_pins:
        for _, value in fields:
            if _TYPE(value) is property:
                functions.extend(f for f in (value.fget, value.fset, value.fdel) if f is not None)
            elif _TYPE(value) in (classmethod, staticmethod):
                functions.append(value.__func__)
            elif _TYPE(value) is FunctionType:
                functions.append(value)
    for _, fields in module_pins:
        functions.extend(value for _, value in fields if _TYPE(value) is FunctionType)
    return tuple((f, _OBJECT_GET(f, "__code__"), _OBJECT_GET(f, "__defaults__"),
                  _OBJECT_GET(f, "__kwdefaults__")) for f in functions)


# Unsupported pathlib versions may use native property accessors. Keep
# ordinary imports available; _path_runtime rejects their projections.
_FUNCTION_PINS = _function_pins(_PATH_CLASS_PINS, _MODULE_PINS) if _SUPPORTED else ()


def _path_runtime():
    if not _SUPPORTED:
        _fail("REPRESENTATION_UNSUPPORTED")
    for module, fields in _MODULE_PINS:
        if _TYPE(module) is not ModuleType:
            _fail("REPRESENTATION_UNSUPPORTED")
        dictionary = _dictionary(module, "REPRESENTATION_UNSUPPORTED")
        for name, expected in fields:
            if _DICT_GET(dictionary, name, _MISSING) is not expected:
                _fail("REPRESENTATION_UNSUPPORTED")
    if _TYPE(_IMPLEMENTATION) is not _IMPLEMENTATION_TYPE:
        _fail("REPRESENTATION_UNSUPPORTED")
    implementation = _dictionary(_IMPLEMENTATION, "REPRESENTATION_UNSUPPORTED")
    implementation_name = _DICT_GET(implementation, "name", _MISSING)
    if _TYPE(implementation_name) is not str or implementation_name != "cpython":
        _fail("REPRESENTATION_UNSUPPORTED")
    _check_classes(_PATH_CLASS_PINS, "REPRESENTATION_UNSUPPORTED")
    for function, code, defaults, kwdefaults in _FUNCTION_PINS:
        if (_OBJECT_GET(function, "__code__") is not code
                or _OBJECT_GET(function, "__defaults__") is not defaults
                or _OBJECT_GET(function, "__kwdefaults__") is not kwdefaults):
            _fail("REPRESENTATION_UNSUPPORTED")


def _utf8(value):
    if len(value) > 32768:
        _fail("PATH_INVALID")
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError:
        _fail("PATH_INVALID")
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
        _fail("PATH_INVALID")
    return raw


def _component(value):
    if (not value or value in (".", "..") or value[-1:] in (".", " ")
            or any(char in '<>:"/\\|?*' for char in value)
            or len(value) > 255 or len(_utf8(value)) > 255
            or value.split(".", 1)[0].rstrip(" ").upper() in _RESERVED):
        _fail("PATH_INVALID")


def _text_parts(value, *, absolute):
    if _TYPE(value) is not str:
        _fail("TYPE_INVALID")
    if not value or len(_utf8(value)) > 32768:
        _fail("PATH_INVALID")
    if _NATIVE_OS == "nt":
        text = value.replace("/", "\\")
        if absolute:
            if (len(text) < 3 or text[0] not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    or text[1:3] != ":\\"):
                _fail("PATH_INVALID")
            tail = text[3:]
        else:
            tail = text
        parts = [] if absolute and not tail else tail.split("\\")
    else:
        text = value
        if absolute:
            if not text.startswith("/") or text.startswith("//"):
                _fail("PATH_INVALID")
            tail = text[1:]
        else:
            tail = text
        parts = [] if absolute and not tail else tail.split("/")
    if len(parts) > 128:
        _fail("PATH_INVALID")
    for part in parts:
        _component(part)
    return text


def _canonical(value):
    normalized_separators = _text_parts(value, absolute=True)
    canonical = _NORMAL_CASE(_NORMAL_PATH(normalized_separators))
    if (_TYPE(canonical) is not str or _NORMAL_CASE(normalized_separators) != canonical
            or len(_utf8(canonical)) > 32768):
        _fail("PATH_INVALID")
    _text_parts(canonical, absolute=True)
    return canonical


def _raw_shape(path):
    try:
        raw = _OBJECT_GET(path, "_raw_paths")
    except AttributeError:
        _fail("PATH_INVALID")
    if (_TYPE(raw) is not list or not raw or len(raw) > 129
            or any(_TYPE(piece) is not str for piece in raw)):
        _fail("PATH_INVALID")
    for name in ("_str", "_drv", "_root", "_tail_cached"):
        try:
            value = _OBJECT_GET(path, name)
        except AttributeError:
            continue
        if name == "_tail_cached":
            if (_TYPE(value) is not list or len(value) > 128
                    or any(_TYPE(piece) is not str for piece in value)):
                _fail("PATH_INVALID")
            for piece in value:
                _component(piece)
        elif _TYPE(value) is not str:
            _fail("PATH_INVALID")
        else:
            _utf8(value)
    copied = tuple(raw)
    for index, piece in enumerate(copied):
        _text_parts(piece, absolute=index == 0)
    return copied


def _path_signature(path):
    text = _STR(path)
    parts = _OBJECT_GET(path, "parts")
    name = _OBJECT_GET(path, "name")
    parent = _OBJECT_GET(path, "parent")
    child = path.joinpath(_SENTINEL)
    if (_TYPE(text) is not str or _TYPE(parts) is not tuple
            or any(_TYPE(part) is not str for part in parts) or _TYPE(name) is not str
            or _TYPE(parent) is not _PATH_TYPE or _TYPE(child) is not _PATH_TYPE):
        _fail("PATH_INVALID")
    # The sentinel child may have 129 components when the actual path has 128.
    # Compare its pure representation, without treating it as an admitted root.
    return (_NORMAL_CASE(text), tuple(_NORMAL_CASE(part) for part in parts),
            _NORMAL_CASE(name), _NORMAL_CASE(_STR(parent)), _NORMAL_CASE(_STR(child)))


def _path_projection(path):
    _path_runtime()
    if _TYPE(path) is not _PATH_TYPE:
        _fail("TYPE_INVALID")
    raw = _raw_shape(path)
    rebuilt = _PATH_TYPE(*raw)
    expected = _canonical(_JOIN(*raw))
    if (_canonical(_STR(path)) != expected or _canonical(_STR(rebuilt)) != expected
            or _path_signature(path) != _path_signature(rebuilt)):
        _fail("PATH_INVALID")
    return expected


def _profile_fields(profile):
    if lifecycle is not _LIFECYCLE_MODULE or _TYPE(lifecycle) is not ModuleType:
        _fail("PROFILE_INVALID")
    dictionary = _dictionary(lifecycle, "PROFILE_INVALID")
    if _DICT_GET(dictionary, "StorageProfile", _MISSING) is not _PROFILE_TYPE:
        _fail("PROFILE_INVALID")
    _check_classes(_PROFILE_CLASS_PINS, "PROFILE_INVALID")
    if _TYPE(profile) is not _PROFILE_TYPE:
        _fail("TYPE_INVALID")
    dictionary = _dictionary(profile, "PROFILE_INVALID")
    values = tuple(_DICT_GET(dictionary, field, _MISSING) for field in _FIELDS)
    if any(value is _MISSING for value in values):
        _fail("PROFILE_INVALID")
    if any(_TYPE(value) is not _PATH_TYPE for value in values):
        _fail("TYPE_INVALID")
    return values


def project_profile(profile):
    """Return fresh canonical path strings, without evaluating profile properties."""
    return tuple(_path_projection(value) for value in _profile_fields(profile))


def _origin(generation, request_sha, operation=None):
    if _TYPE(generation) is not int or _TYPE(request_sha) is not str:
        _fail("TYPE_INVALID")
    if generation not in (1, 2) or len(request_sha) != 64 or any(c not in "0123456789abcdef" for c in request_sha):
        _fail("ORIGIN_INVALID")
    if operation is not None:
        if _TYPE(operation) is not str:
            _fail("TYPE_INVALID")
        if (len(operation) != 36 or any(operation[index] != "-" for index in (8, 13, 18, 23))
                or any(c not in "0123456789abcdef" for index, c in enumerate(operation) if index not in (8, 13, 18, 23))
                or not any(c != "0" for c in operation if c != "-")):
            _fail("ORIGIN_INVALID")


@dataclass(frozen=True, slots=True, eq=False, repr=False, kw_only=True)
class ProfileRaw2Source:
    profile: object
    operation_id: str
    generation: int
    expected_request_sha256: str
    _profile_original: object = field(init=False, repr=False)
    _path_originals: tuple = field(init=False, repr=False)
    _projection: tuple = field(init=False, repr=False)
    _origin_original: tuple = field(init=False, repr=False)

    def __new__(cls, *args, **kwargs):
        # The original constructor entry validates downstream generated init
        # and post-init functions before type.__call__ can dispatch to them.
        _selector_classes()
        if cls is not _PROFILE_SELECTOR:
            _fail("TYPE_INVALID")
        return _OBJECT_NEW(cls)

    def __init_subclass__(cls, **kwargs):
        raise TypeError("Replica source classes cannot be subclassed")

    def __post_init__(self):
        _selector_classes()
        _origin(self.generation, self.expected_request_sha256, self.operation_id)
        if _TYPE(self.operation_id) is not str:
            _fail("TYPE_INVALID")
        fields = _profile_fields(self.profile)
        projection = tuple(_path_projection(value) for value in fields)
        _canonical(_JOIN(_DIRNAME(projection[0]), _NAMESPACE, self.operation_id))
        _OBJECT_SET(self, "_profile_original", self.profile)
        _OBJECT_SET(self, "_path_originals", fields)
        _OBJECT_SET(self, "_projection", projection)
        _OBJECT_SET(self, "_origin_original", (self.operation_id, self.generation, self.expected_request_sha256))


@dataclass(frozen=True, slots=True, eq=False, repr=False, kw_only=True)
class DetachedRaw2Source:
    package_root: object
    generation: int
    expected_request_sha256: str
    _root_original: object = field(init=False, repr=False)
    _projection: str = field(init=False, repr=False)
    _origin_original: tuple = field(init=False, repr=False)

    def __new__(cls, *args, **kwargs):
        _selector_classes()
        if cls is not _DETACHED_SELECTOR:
            _fail("TYPE_INVALID")
        return _OBJECT_NEW(cls)

    def __init_subclass__(cls, **kwargs):
        raise TypeError("Replica source classes cannot be subclassed")

    def __post_init__(self):
        _selector_classes()
        _origin(self.generation, self.expected_request_sha256)
        projection = _path_projection(self.package_root)
        _OBJECT_SET(self, "_root_original", self.package_root)
        _OBJECT_SET(self, "_projection", projection)
        _OBJECT_SET(self, "_origin_original", (self.generation, self.expected_request_sha256))


_PROFILE_SELECTOR = ProfileRaw2Source
_DETACHED_SELECTOR = DetachedRaw2Source
_SELECTOR_CLASS_PINS = _capture_classes((_PROFILE_SELECTOR, _DETACHED_SELECTOR), (
    "__new__", "__init__", "__post_init__", "__getattribute__", "__getattr__",
    "__setattr__", "__delattr__", "__init_subclass__", "__eq__", "__hash__", "__dict__",
    "profile", "operation_id", "generation", "expected_request_sha256", "package_root",
    "_profile_original", "_path_originals", "_root_original", "_projection", "_origin_original"))
_SELECTOR_FUNCTION_PINS = _function_pins(_SELECTOR_CLASS_PINS)


def _selector_closure_pins():
    pins = []
    for function, _, _, _ in _SELECTOR_FUNCTION_PINS:
        closure = _OBJECT_GET(function, "__closure__")
        cells = []
        if closure is not None:
            if _TYPE(closure) is not tuple:
                _fail("SELECTOR_INVALID")
            for cell in closure:
                if _TYPE(cell) is not CellType:
                    _fail("SELECTOR_INVALID")
                try:
                    contents = _OBJECT_GET(cell, "cell_contents")
                except ValueError:
                    _fail("SELECTOR_INVALID")
                cells.append((cell, contents))
        pins.append((function, closure, tuple(cells)))
    return tuple(pins)


# Frozen dataclass init calls the object captured in its closure before it
# dispatches post-init.  Function/code/default identity alone cannot pin that
# actual callee.  Capture every downstream selector cell and its original
# contents without equality, repr or caller-supplied attribute dispatch.
_SELECTOR_CLOSURE_PINS = _selector_closure_pins()


def _selector_classes():
    if ProfileRaw2Source is not _PROFILE_SELECTOR or DetachedRaw2Source is not _DETACHED_SELECTOR:
        _fail("SELECTOR_INVALID")
    _check_classes(_SELECTOR_CLASS_PINS, "SELECTOR_INVALID")
    for function, code, defaults, kwdefaults in _SELECTOR_FUNCTION_PINS:
        if (_OBJECT_GET(function, "__code__") is not code
                or _OBJECT_GET(function, "__defaults__") is not defaults
                or _OBJECT_GET(function, "__kwdefaults__") is not kwdefaults):
            _fail("SELECTOR_INVALID")
    for function, original_closure, original_cells in _SELECTOR_CLOSURE_PINS:
        closure = _OBJECT_GET(function, "__closure__")
        if closure is not original_closure:
            _fail("SELECTOR_INVALID")
        if closure is not None:
            if _TYPE(closure) is not tuple or len(closure) != len(original_cells):
                _fail("SELECTOR_INVALID")
            for cell, (original_cell, contents) in zip(closure, original_cells):
                if cell is not original_cell or _TYPE(cell) is not CellType:
                    _fail("SELECTOR_INVALID")
                try:
                    current = _OBJECT_GET(cell, "cell_contents")
                except ValueError:
                    _fail("SELECTOR_INVALID")
                if current is not contents:
                    _fail("SELECTOR_INVALID")


def _snapshot_tuple(value, kinds):
    if (_TYPE(value) is not tuple or len(value) != len(kinds)
            or any(_TYPE(item) is not kind for item, kind in zip(value, kinds))):
        _fail("SELECTOR_INVALID")


def project_selector(selector):
    """Return fresh six-field source parameters; never a PURE Source/owner DTO."""
    _selector_classes()
    kind = _TYPE(selector)
    if kind is not _PROFILE_SELECTOR and kind is not _DETACHED_SELECTOR:
        _fail("TYPE_INVALID")
    try:
        generation = _OBJECT_GET(selector, "generation")
        request_sha = _OBJECT_GET(selector, "expected_request_sha256")
        origin = _OBJECT_GET(selector, "_origin_original")
        projection = _OBJECT_GET(selector, "_projection")
        if kind is _PROFILE_SELECTOR:
            operation = _OBJECT_GET(selector, "operation_id")
            if _TYPE(operation) is not str:
                _fail("TYPE_INVALID")
            _origin(generation, request_sha, operation)
            _snapshot_tuple(origin, (str, int, str))
            if origin != (operation, generation, request_sha):
                _fail("SELECTOR_INVALID")
            profile = _OBJECT_GET(selector, "profile")
            if profile is not _OBJECT_GET(selector, "_profile_original"):
                _fail("PROFILE_INVALID")
            fields = _profile_fields(profile)
            originals = _OBJECT_GET(selector, "_path_originals")
            if (_TYPE(originals) is not tuple or len(originals) != 4
                    or any(a is not b for a, b in zip(fields, originals))):
                _fail("PROFILE_INVALID")
            current = tuple(_path_projection(value) for value in fields)
            _snapshot_tuple(projection, (str, str, str, str))
            if current != projection:
                _fail("PROFILE_INVALID")
            package_root = _canonical(_JOIN(_DIRNAME(current[0]), _NAMESPACE, operation))
            source_kind, profile_paths = "profile_raw2", list(current)
        else:
            _origin(generation, request_sha)
            _snapshot_tuple(origin, (int, str))
            if origin != (generation, request_sha):
                _fail("SELECTOR_INVALID")
            path = _OBJECT_GET(selector, "package_root")
            if path is not _OBJECT_GET(selector, "_root_original"):
                _fail("SELECTOR_INVALID")
            package_root = _path_projection(path)
            if _TYPE(projection) is not str or package_root != projection:
                _fail("SELECTOR_INVALID")
            source_kind, profile_paths, operation = "detached_raw2", None, None
    except AttributeError:
        _fail("SELECTOR_INVALID")
    return {"source_kind": source_kind, "package_root": package_root,
            "generation": generation, "expected_request_sha256": request_sha,
            "profile_paths": profile_paths, "original_operation_id": operation}
