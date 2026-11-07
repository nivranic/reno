"""Independent S0 selector/profile/path projection oracles.

Synthetic absolute paths are never created.  Tests call only constructors and
pure projection APIs, not G1, owners, locks, native source factories or codecs.
Expectations are written from SOURCE-WIRE 2.1, not the new implementation.
"""
import os
from pathlib import Path
from types import MappingProxyType

import pytest

from reno import personal_authority_replica_selector as selector
from reno import storage_lifecycle as lifecycle


UUID = "12345678-1234-4234-8234-123456789abc"
ZERO_UUID = "00000000-0000-0000-0000-000000000000"
SHA = "0123456789abcdef" * 4
BASE = "C:/reno-selector-synthetic" if os.name == "nt" else "/reno-selector-synthetic"
PATH_TYPE = type(Path())
PROFILE_TYPE = lifecycle.StorageProfile
FIELDS = ("database", "media", "reports", "checkpoints")
_UNSET = object()
PREFIX = "AUTHORITY_REPLICA_SELECTOR1_"
ERROR_SUFFIXES = {
    "TYPE_INVALID", "SELECTOR_INVALID", "PROFILE_INVALID", "PATH_INVALID",
    "ORIGIN_INVALID", "REPRESENTATION_UNSUPPORTED",
}


def profile():
    root = Path(BASE)
    return PROFILE_TYPE(database=root / "data" / "reno.db", media=root / "media",
                        reports=root / "reports", checkpoints=root / "checkpoints")


def canonical(path):
    return os.path.normcase(os.path.normpath(str(path)))


def expected_paths(value):
    return tuple(canonical(object.__getattribute__(value, name)) for name in FIELDS)


def profile_source(value=_UNSET, **changes):
    args = dict(profile=profile() if value is _UNSET else value, operation_id=UUID,
                generation=1, expected_request_sha256=SHA)
    args.update(changes)
    return selector.ProfileRaw2Source(**args)


def detached_source(root=_UNSET, **changes):
    args = dict(package_root=Path(BASE) / "detached" if root is _UNSET else root,
                generation=1, expected_request_sha256=SHA)
    args.update(changes)
    return selector.DetachedRaw2Source(**args)


def rejected(call, *args, **kwargs):
    with pytest.raises(selector.AuthorityReplicaSelectorError) as captured:
        call(*args, **kwargs)
    assert type(captured.value.code) is str
    assert captured.value.code.startswith(PREFIX)
    assert captured.value.code[len(PREFIX):] in ERROR_SUFFIXES


@pytest.mark.parametrize("generation", [1, 2])
def test_profile_projection_has_original_order_and_no_native_identity(generation):
    original = profile()
    paths = expected_paths(original)
    value = profile_source(original, generation=generation)
    actual = selector.project_selector(value)
    expected = {
        "source_kind": "profile_raw2",
        "package_root": canonical(original.database.parent / ".reno-authority-raw-preservation-v2" / UUID),
        "generation": generation, "expected_request_sha256": SHA,
        "profile_paths": list(paths), "original_operation_id": UUID,
    }
    assert type(selector.project_profile(original)) is tuple
    assert selector.project_profile(original) == paths
    assert type(actual) is dict and actual == expected
    assert type(actual["profile_paths"]) is list
    assert all(type(item) is str for item in actual["profile_paths"])
    assert value.profile is original


@pytest.mark.parametrize("generation", [1, 2])
def test_detached_projection_has_explicit_null_profile_and_original_operation(generation):
    root = Path(BASE) / "detached"
    value = detached_source(root, generation=generation)
    assert selector.project_selector(value) == {
        "source_kind": "detached_raw2", "package_root": canonical(root),
        "generation": generation, "expected_request_sha256": SHA,
        "profile_paths": None, "original_operation_id": None,
    }


def test_returned_projection_is_a_fresh_detached_value():
    value = profile_source()
    expected = selector.project_selector(value)
    edited = selector.project_selector(value)
    assert edited is not expected and edited["profile_paths"] is not expected["profile_paths"]
    edited["profile_paths"][0] = "caller replacement"
    edited["generation"] = False
    edited["root_identity"] = {"device": "1", "inode": "2"}
    assert selector.project_selector(value) == expected


@pytest.mark.parametrize("factory", [profile_source, detached_source])
def test_values_are_frozen_slotted_and_use_identity_equality(factory):
    value = factory()
    other = factory()
    assert value is not other and value != other
    assert not hasattr(value, "__dict__")
    with pytest.raises((AttributeError, TypeError)):
        value.generation = 2
    with pytest.raises((AttributeError, TypeError)):
        value.caller_authority = True


def test_keyword_only_constructors_and_cross_variant_arguments():
    with pytest.raises(TypeError):
        selector.ProfileRaw2Source(profile(), UUID, 1, SHA)
    with pytest.raises(TypeError):
        selector.DetachedRaw2Source(Path(BASE), 1, SHA)
    with pytest.raises(TypeError):
        profile_source(package_root=Path(BASE))
    with pytest.raises(TypeError):
        detached_source(profile=profile())
    with pytest.raises(TypeError):
        detached_source(operation_id=UUID)


@pytest.mark.parametrize("factory", [profile_source, detached_source])
@pytest.mark.parametrize("value", [None, True, False, 0, 3, -1, 1.0, "1", b"1", [], {}])
def test_generations_are_exact_one_or_two(factory, value):
    rejected(factory, generation=value)


@pytest.mark.parametrize("value", [
    None, True, 1, b"uuid", "", ZERO_UUID, UUID.upper(), UUID.replace("-", ""),
    "{" + UUID + "}", UUID + "\n", "12345678-1234-4234-8234-123456789abg",
])
def test_original_operation_id_is_nonzero_canonical_uuid(value):
    rejected(profile_source, operation_id=value)


@pytest.mark.parametrize("factory", [profile_source, detached_source])
@pytest.mark.parametrize("value", [None, True, 1, b"0" * 64, "", "0" * 63, "0" * 65, "g" * 64, SHA.upper()])
def test_request_sha_is_an_exact_lowercase_digest(factory, value):
    rejected(factory, expected_request_sha256=value)


@pytest.mark.parametrize("value", [None, True, 1, {}, [], (), MappingProxyType({})])
def test_profile_requires_the_actual_exact_storageprofile_class(value):
    rejected(selector.project_profile, value)
    rejected(profile_source, value)


def test_storageprofile_subclass_is_rejected():
    class Derived(PROFILE_TYPE):
        pass

    original = profile()
    derived = Derived(**{name: getattr(original, name) for name in FIELDS})
    rejected(selector.project_profile, derived)
    rejected(profile_source, derived)


def test_selector_subclasses_cannot_enter_projection():
    for original, kwargs in (
        (selector.ProfileRaw2Source, dict(profile=profile(), operation_id=UUID, generation=1, expected_request_sha256=SHA)),
        (selector.DetachedRaw2Source, dict(package_root=Path(BASE), generation=1, expected_request_sha256=SHA)),
    ):
        # The closed value type may forbid class creation or construction.
        # If Python can construct it, the public projection must still reject.
        try:
            derived = type("DerivedSelector", (original,), {})
            value = derived(**kwargs)
        except (TypeError, selector.AuthorityReplicaSelectorError):
            continue
        rejected(selector.project_selector, value)


@pytest.mark.parametrize("value", [None, True, 1, {}, [], (), MappingProxyType({}), "profile_raw2"])
def test_projection_never_accepts_a_caller_mapping_or_decoded_source(value):
    rejected(selector.project_selector, value)


@pytest.mark.parametrize("field", FIELDS)
def test_each_profile_path_pointer_is_pinned_even_for_equal_new_path(field):
    original = profile()
    value = profile_source(original)
    current = object.__getattribute__(original, field)
    replacement = Path(str(current))
    assert replacement is not current and str(replacement) == str(current)
    object.__setattr__(original, field, replacement)
    rejected(selector.project_selector, value)


def test_replacing_actual_profile_with_equal_distinct_profile_is_rejected():
    original = profile()
    value = profile_source(original)
    replacement = profile()
    assert replacement is not original
    assert expected_paths(replacement) == expected_paths(original)
    object.__setattr__(value, "profile", replacement)
    rejected(selector.project_selector, value)


@pytest.mark.parametrize("field", FIELDS)
def test_same_profile_path_object_changed_raw_and_caches_cannot_rebind(field):
    original = profile()
    value = profile_source(original)
    old = object.__getattribute__(original, field)
    replacement = Path(BASE) / "changed"
    # Change the existing object, preserving the original profile field pointer.
    object.__setattr__(old, "_raw_paths", list(object.__getattribute__(replacement, "_raw_paths")))
    for name in ("_str", "_drv", "_root", "_tail_cached"):
        try:
            object.__delattr__(old, name)
        except AttributeError:
            pass
    assert object.__getattribute__(original, field) is old
    rejected(selector.project_selector, value)


@pytest.mark.parametrize("factory", [profile_source, detached_source])
@pytest.mark.parametrize("field,bad", [("generation", True), ("expected_request_sha256", SHA.upper())])
def test_projection_rechecks_constructor_fields_after_bypass_mutation(factory, field, bad):
    value = factory()
    object.__setattr__(value, field, bad)
    rejected(selector.project_selector, value)


def test_profile_projection_rejects_nonstring_dictionary_key_without_comparing_it():
    original = profile()
    calls = []

    class Key:
        def __hash__(self):
            return hash("database")

        def __eq__(self, other):
            calls.append("eq")
            return False

    original.__dict__[Key()] = None
    calls.clear()
    rejected(selector.project_profile, original)
    assert calls == []


def test_profile_dictionary_subclass_is_rejected_without_iterating():
    original = profile()
    calls = []

    class HostileDict(dict):
        def __iter__(self):
            calls.append("iter")
            raise AssertionError("dict hook")

        def get(self, *args):
            calls.append("get")
            raise AssertionError("dict hook")

    object.__setattr__(original, "__dict__", HostileDict(original.__dict__))
    rejected(selector.project_profile, original)
    assert calls == []


@pytest.mark.parametrize("field", FIELDS)
def test_profile_field_wrong_type_is_rejected_without_path_coercion(field):
    original = profile()
    calls = []

    class FakePath:
        def __fspath__(self):
            calls.append("fspath")
            raise AssertionError("path coercion")

        def __str__(self):
            calls.append("str")
            raise AssertionError("path coercion")

    object.__setattr__(original, field, FakePath())
    rejected(selector.project_profile, original)
    assert calls == []


@pytest.mark.parametrize("slot,bad", [
    ("_raw_paths", (BASE,)), ("_raw_paths", [1]), ("_raw_paths", [object()]),
    ("_str", 7), ("_drv", object()), ("_root", []),
    ("_tail_cached", ("safe",)), ("_tail_cached", [object()]),
])
def test_path_representation_is_checked_before_semantic_methods(slot, bad):
    path = Path(BASE) / "detached"
    object.__setattr__(path, slot, bad)
    rejected(detached_source, path)


@pytest.mark.parametrize("slot", ["_str", "_drv", "_root", "_tail_cached"])
def test_cache_only_path_change_is_rejected(slot):
    path = Path(BASE) / "detached"
    # Fill normal caches before changing only one semantic representation.
    str(path), path.parts, path.name, path.parent
    value = ["different"] if slot == "_tail_cached" else "different"
    object.__setattr__(path, slot, value)
    rejected(detached_source, path)


def test_hostile_strings_in_path_raw_or_cache_do_not_execute_hooks():
    calls = []

    class Hostile(str):
        def __str__(self):
            calls.append("str")
            raise AssertionError("string hook")

        def __eq__(self, other):
            calls.append("eq")
            raise AssertionError("string hook")

        def __hash__(self):
            calls.append("hash")
            raise AssertionError("string hook")

    for slot, bad in (("_raw_paths", [Hostile(BASE)]), ("_str", Hostile(BASE)),
                      ("_tail_cached", [Hostile("detached")])):
        path = Path(BASE) / "detached"
        object.__setattr__(path, slot, bad)
        rejected(detached_source, path)
    assert calls == []


def test_exact_string_inputs_reject_subclasses_before_hooks():
    calls = []

    class Hostile(str):
        def __str__(self):
            calls.append("str")
            raise AssertionError("string hook")

        def __eq__(self, other):
            calls.append("eq")
            raise AssertionError("string hook")

        def __hash__(self):
            calls.append("hash")
            raise AssertionError("string hook")

    rejected(profile_source, operation_id=Hostile(UUID))
    rejected(profile_source, expected_request_sha256=Hostile(SHA))
    rejected(detached_source, expected_request_sha256=Hostile(SHA))
    assert calls == []


def test_path_subclass_is_rejected_without_conversion():
    calls = []

    class Derived(PATH_TYPE):
        def __fspath__(self):
            calls.append("fspath")
            raise AssertionError("path hook")

        def __str__(self):
            calls.append("str")
            raise AssertionError("path hook")

    rejected(detached_source, Derived(BASE))
    assert calls == []


@pytest.mark.parametrize("raw", [
    "relative", "./relative", "../relative", "https://example.invalid/raw2",
    BASE + "/../other", BASE + "/./other", BASE + "//other",
    BASE + "/CON", BASE + "/nul.txt", BASE + "/COM9", BASE + "/tail.", BASE + "/tail ",
    BASE + "/file:stream", BASE + "/bad\x00", BASE + "/bad\x7f", BASE + "/bad\n", BASE + "/\ud800",
])
def test_unsafe_absolute_or_raw_path_representations_are_rejected(raw):
    rejected(detached_source, Path(raw))


@pytest.mark.skipif(os.name != "nt", reason="Windows local-drive lexical variants")
@pytest.mark.parametrize("raw", ["C:relative", "\\\\server\\share\\root", "\\\\?\\C:\\root", "\\\\.\\C:\\root", "\\root"])
def test_windows_unc_device_and_drive_relative_paths_are_rejected(raw):
    rejected(detached_source, Path(raw))


def test_component_byte_limit_is_utf8_not_character_count():
    accepted = Path(BASE) / ("a" * 255)
    assert selector.project_selector(detached_source(accepted))["package_root"] == canonical(accepted)
    rejected(detached_source, Path(BASE) / ("a" * 256))
    rejected(detached_source, Path(BASE) / ("界" * 86))


def absolute_components(count):
    anchor = Path("C:/") if os.name == "nt" else Path("/")
    return anchor.joinpath(*("x" for _ in range(count)))


def test_detached_component_bound_includes_exact_128_positive():
    accepted = absolute_components(128)
    assert selector.project_selector(detached_source(accepted))["package_root"] == canonical(accepted)
    rejected(detached_source, absolute_components(129))


def test_profile_source_append_namespace_and_operation_is_in_same_128_bound():
    original = profile()
    object.__setattr__(original, "database", absolute_components(126) / "reno.db")
    # 126 parent components plus namespace plus operation exactly reaches128.
    assert selector.project_selector(profile_source(original))["package_root"] == canonical(
        absolute_components(126) / ".reno-authority-raw-preservation-v2" / UUID)
    too_deep = profile()
    object.__setattr__(too_deep, "database", absolute_components(127) / "reno.db")
    assert len(selector.project_profile(too_deep)) == 4
    rejected(profile_source, too_deep)


@pytest.mark.parametrize("attribute", ["identity", "roots"])
def test_profile_class_descriptor_pollution_is_rejected_without_get(attribute, monkeypatch):
    original = profile()
    calls = []

    class HostileDescriptor:
        def __get__(self, instance, owner=None):
            calls.append("descriptor")
            raise AssertionError("descriptor hook")

    with monkeypatch.context() as patch:
        patch.setattr(PROFILE_TYPE, attribute, HostileDescriptor())
        rejected(selector.project_profile, original)
    assert calls == []


def test_storageprofile_module_binding_pin_is_checked(monkeypatch):
    original = profile()
    with monkeypatch.context() as patch:
        patch.setattr(lifecycle, "StorageProfile", object)
        rejected(selector.project_profile, original)


@pytest.mark.parametrize("attribute", ["parts", "name", "parent", "__str__"])
def test_path_descriptor_pollution_is_rejected_before_invocation(attribute, monkeypatch):
    path = Path(BASE) / "detached"
    calls = []

    class HostileDescriptor:
        def __get__(self, instance, owner=None):
            calls.append("descriptor")
            raise AssertionError("descriptor hook")

    with monkeypatch.context() as patch:
        patch.setattr(PATH_TYPE, attribute, HostileDescriptor())
        rejected(detached_source, path)
    assert calls == []


def test_path_module_binding_pin_is_checked(monkeypatch):
    import pathlib

    path = Path(BASE) / "detached"
    with monkeypatch.context() as patch:
        patch.setattr(pathlib, "Path", object)
        rejected(detached_source, path)


def test_constructor_and_projection_never_access_filesystem_or_guard(monkeypatch):
    original = profile()
    root = Path(BASE) / "detached"
    expected = expected_paths(original)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("effect")
        raise AssertionError("S0 performed an effect or guard access")

    with monkeypatch.context() as patch:
        for name in ("stat", "lstat", "open", "read", "write", "scandir", "listdir", "readlink"):
            patch.setattr(os, name, forbidden)
        for name in ("resolve", "exists", "stat", "open"):
            patch.setattr(PATH_TYPE, name, forbidden)
        for name in ("require_guard", "current_guard", "_owner"):
            patch.setattr(lifecycle, name, forbidden)
        first = profile_source(original)
        second = detached_source(root)
        assert selector.project_profile(original) == expected
        assert selector.project_selector(first)["source_kind"] == "profile_raw2"
        assert selector.project_selector(second)["source_kind"] == "detached_raw2"
    assert calls == []


@pytest.mark.parametrize("value", [None, True, 1, BASE, BASE.encode("utf-8"), [], {}, MappingProxyType({})])
def test_detached_root_requires_an_exact_local_path_object(value):
    rejected(detached_source, value)


def test_integer_subclass_generation_is_rejected_without_comparison():
    calls = []

    class HostileInt(int):
        def __eq__(self, other):
            calls.append("eq")
            raise AssertionError("integer hook")

        def __hash__(self):
            calls.append("hash")
            raise AssertionError("integer hook")

        def __int__(self):
            calls.append("int")
            raise AssertionError("integer hook")

    rejected(profile_source, generation=HostileInt(1))
    rejected(detached_source, generation=HostileInt(2))
    assert calls == []


def test_detached_root_does_not_coerce_a_duck_path():
    calls = []

    class FakePath:
        def __fspath__(self):
            calls.append("fspath")
            raise AssertionError("path coercion")

        def __str__(self):
            calls.append("str")
            raise AssertionError("path coercion")

    rejected(detached_source, FakePath())
    assert calls == []


_REPLICA_S0_TEST_HOOK_EVENTS = []


# Same-function tampering uses bounded code templates with matching closure
# counts.  No template is called directly and no test compiles/executes text.
# The fixed global is installed in the original function's globals only for
# the mutation window, and monkeypatch restores it together with the method.
def _same_function_trap_code(freevar_count):
    cell0 = cell1 = cell2 = cell3 = None

    def zero(*args, **kwargs):
        _REPLICA_S0_TEST_HOOK_EVENTS.append("code")
        raise AssertionError("tampered function body executed")

    def one(*args, **kwargs):
        unused = (cell0,)
        _REPLICA_S0_TEST_HOOK_EVENTS.append("code")
        raise AssertionError("tampered function body executed")

    def two(*args, **kwargs):
        unused = (cell0, cell1)
        _REPLICA_S0_TEST_HOOK_EVENTS.append("code")
        raise AssertionError("tampered function body executed")

    def three(*args, **kwargs):
        unused = (cell0, cell1, cell2)
        _REPLICA_S0_TEST_HOOK_EVENTS.append("code")
        raise AssertionError("tampered function body executed")

    def four(*args, **kwargs):
        unused = (cell0, cell1, cell2, cell3)
        _REPLICA_S0_TEST_HOOK_EVENTS.append("code")
        raise AssertionError("tampered function body executed")

    templates = (zero, one, two, three, four)
    assert 0 <= freevar_count < len(templates), "Original callable changed its reviewed closure shape"
    result = templates[freevar_count].__code__
    assert len(result.co_freevars) == freevar_count
    return result


def _mutate_same_function(patch, function, part, calls):
    class HostileDefault:
        def __eq__(self, other):
            calls.append("default-eq")
            raise AssertionError("default hook executed")

        def __hash__(self):
            calls.append("default-hash")
            raise AssertionError("default hook executed")

        def __repr__(self):
            calls.append("default-repr")
            raise AssertionError("default hook executed")

    if part == "__code__":
        patch.setitem(function.__globals__, "_REPLICA_S0_TEST_HOOK_EVENTS", calls)
        patch.setattr(function, part, _same_function_trap_code(len(function.__code__.co_freevars)))
    elif part == "__defaults__":
        patch.setattr(function, part, (HostileDefault(),))
    else:
        assert part == "__kwdefaults__"
        patch.setattr(function, part, {"_s0_unused_default": HostileDefault()})


@pytest.mark.parametrize("kind", ["profile", "detached"])
@pytest.mark.parametrize("method", ["__init__", "__post_init__"])
@pytest.mark.parametrize("part", ["__code__", "__defaults__", "__kwdefaults__"])
@pytest.mark.parametrize("entry", ["constructor", "project"])
def test_selector_same_method_function_tampering_rejected_before_dynamic_call(
        kind, method, part, entry, monkeypatch):
    if kind == "profile":
        value_type = selector.ProfileRaw2Source
        original = profile()
        kwargs = dict(profile=original, operation_id=UUID, generation=1, expected_request_sha256=SHA)
    else:
        value_type = selector.DetachedRaw2Source
        kwargs = dict(package_root=Path(BASE) / "detached", generation=1, expected_request_sha256=SHA)
    existing = value_type(**kwargs)
    method_function = type.__getattribute__(value_type, "__dict__")[method]
    calls = []
    with monkeypatch.context() as patch:
        _mutate_same_function(patch, method_function, part, calls)
        assert type.__getattribute__(value_type, "__dict__")[method] is method_function
        if entry == "constructor":
            rejected(value_type, **kwargs)
        else:
            rejected(selector.project_selector, existing)
    assert calls == []


@pytest.mark.parametrize("entry", ["constructor", "project"])
def test_path_str_same_function_code_tampering_is_rejected_before_call(entry, monkeypatch):
    path = Path(BASE) / "detached"
    existing = detached_source(path)
    original_function = PATH_TYPE.__str__
    calls = []
    with monkeypatch.context() as patch:
        _mutate_same_function(patch, original_function, "__code__", calls)
        assert PATH_TYPE.__str__ is original_function
        if entry == "constructor":
            rejected(detached_source, path)
        else:
            rejected(selector.project_selector, existing)
    assert calls == []


@pytest.mark.parametrize("codepoint", [31, 127, 128, 159])
def test_control_character_components_cannot_enter_either_constructor_or_profile_projection(codepoint):
    invalid = Path(BASE) / ("normal" + chr(codepoint) + "component")
    rejected(detached_source, invalid)
    original = profile()
    object.__setattr__(original, "media", invalid)
    rejected(selector.project_profile, original)
    rejected(profile_source, original)


@pytest.mark.parametrize("kind", ["profile", "detached"])
@pytest.mark.parametrize("codepoint", [127, 128, 159])
def test_existing_selector_cannot_project_an_injected_c1_path(kind, codepoint):
    invalid = Path(BASE) / ("normal" + chr(codepoint) + "component")
    if kind == "profile":
        original = profile()
        value = profile_source(original)
        object.__setattr__(original, "reports", invalid)
    else:
        value = detached_source()
        object.__setattr__(value, "package_root", invalid)
    rejected(selector.project_selector, value)


@pytest.mark.parametrize("codepoint", [126, 160, 161])
def test_portable_characters_adjacent_to_c1_range_remain_valid(codepoint):
    accepted = Path(BASE) / ("normal" + chr(codepoint) + "component")
    assert selector.project_selector(detached_source(accepted))["package_root"] == canonical(accepted)
    original = profile()
    object.__setattr__(original, "media", accepted)
    assert selector.project_profile(original)[1] == canonical(accepted)
    assert selector.project_selector(profile_source(original))["profile_paths"][1] == canonical(accepted)


@pytest.mark.parametrize("callee", ["normcase", "join", "dirname"])
@pytest.mark.parametrize("entry", ["constructor", "project"])
def test_used_platform_path_callee_same_function_code_tampering_is_rejected(callee, entry, monkeypatch):
    path = Path(BASE) / "detached"
    existing = detached_source(path)
    path_module = os.path
    original_function = getattr(path_module, callee)
    calls = []
    assert hasattr(original_function, "__code__"), "Reviewed pure path callee must remain a Python function"
    with monkeypatch.context() as patch:
        _mutate_same_function(patch, original_function, "__code__", calls)
        assert getattr(path_module, callee) is original_function
        if entry == "constructor":
            rejected(detached_source, path)
        else:
            rejected(selector.project_selector, existing)
    assert calls == []


@pytest.mark.parametrize("entry", ["constructor", "project"])
def test_used_normpath_module_pointer_tampering_is_rejected_before_call(entry, monkeypatch):
    path = Path(BASE) / "detached"
    existing = detached_source(path)
    calls = []

    def hostile(*args, **kwargs):
        calls.append("normpath")
        raise AssertionError("replaced normpath executed")

    with monkeypatch.context() as patch:
        patch.setattr(os.path, "normpath", hostile)
        if entry == "constructor":
            rejected(detached_source, path)
        else:
            rejected(selector.project_selector, existing)
    assert calls == []


@pytest.mark.parametrize("kind", ["profile", "detached"])
@pytest.mark.parametrize("entry", ["constructor", "project"])
@pytest.mark.parametrize("cell_state", ["replaced", "empty"])
def test_generated_init_object_closure_cell_tampering_is_rejected_before_setattr(kind, entry, cell_state):
    if kind == "profile":
        value_type = selector.ProfileRaw2Source
        kwargs = dict(profile=profile(), operation_id=UUID, generation=1, expected_request_sha256=SHA)
    else:
        value_type = selector.DetachedRaw2Source
        kwargs = dict(package_root=Path(BASE) / "detached", generation=1, expected_request_sha256=SHA)
    existing = value_type(**kwargs)
    original_function = type.__getattribute__(value_type, "__dict__")["__init__"]
    original_code = original_function.__code__
    original_defaults = original_function.__defaults__
    original_kwdefaults = original_function.__kwdefaults__
    names = original_code.co_freevars
    assert names.count("__dataclass_builtins_object__") == 1
    index = names.index("__dataclass_builtins_object__")
    closure = original_function.__closure__
    assert type(closure) is tuple and len(closure) == len(names)
    original_cell = closure[index]
    original_value = original_cell.cell_contents
    assert original_value is object
    calls = []

    class HostileMeta(type):
        def __eq__(cls, other):
            calls.append("cell-eq")
            raise AssertionError("cell equality hook executed")

        def __hash__(cls):
            calls.append("cell-hash")
            raise AssertionError("cell hash hook executed")

        def __repr__(cls):
            calls.append("cell-repr")
            raise AssertionError("cell repr hook executed")

    class HostileObject(metaclass=HostileMeta):
        @staticmethod
        def __setattr__(instance, name, value):
            calls.append("setattr")
            raise AssertionError("generated init closure object executed")

    try:
        if cell_state == "empty":
            del original_cell.cell_contents
        else:
            original_cell.cell_contents = HostileObject
        assert type.__getattribute__(value_type, "__dict__")["__init__"] is original_function
        assert original_function.__code__ is original_code
        assert original_function.__defaults__ is original_defaults
        assert original_function.__kwdefaults__ is original_kwdefaults
        assert original_function.__closure__ is closure and closure[index] is original_cell
        if entry == "constructor":
            rejected(value_type, **kwargs)
        else:
            rejected(selector.project_selector, existing)
    finally:
        original_cell.cell_contents = original_value
    assert original_cell.cell_contents is object
    assert calls == []
