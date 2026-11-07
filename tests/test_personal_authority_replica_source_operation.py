"""Original-entry source-unit lifetime faults, authored but not yet run.

These tests use isolated initialized profiles and a real G1.  Early callback
faults deliberately stop before census; they do not claim a complete source,
full preview, native cleanup after acquired resources, or performance proof.
The native author's separate cases exercise successful/faulted actual I/O.
"""
import os
import codecs
import json
import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from reno import personal_authority_replica_operation as operation
from reno import personal_authority_replica_selector as selector
from reno import personal_authority_replica_group as group
from reno import personal_authority_raw_preservation2_contract as raw2_contract
from reno import storage_lifecycle as lifecycle
from tests.test_personal_authority_replica_source import prepare_source


OPERATION_ID = "12345678-1234-4234-8234-123456789abc"
SOURCE_SHA = "0123456789abcdef" * 4
ERROR = operation.AuthorityReplicaOperationError
pytestmark = pytest.mark.skipif(os.name != "nt", reason="new source kernel is Windows-only")


@pytest.fixture
def home(tmp_path):
    profile = lifecycle.StorageProfile(tmp_path / "home/data/reno.db",
        tmp_path / "home/media", tmp_path / "home/reports", tmp_path / "home/checkpoints")
    lifecycle.initialize(profile)
    return profile


def source_value(tmp_path):
    # The early-cancel tests must stop before this intentionally missing root
    # is reached. A missing package is never used as positive source evidence.
    return selector.DetachedRaw2Source(package_root=tmp_path / "uncreated-source",
        generation=1, expected_request_sha256=SOURCE_SHA)


def live_original():
    records = list(operation._LIVE.values())
    assert len(records) == 1
    registration = records[0]
    assert registration.kind == "source_census_unit"
    context = registration.source_context
    assert context is registration.record.source_context
    assert context.registration is registration and context.record is registration.record
    assert context.token is registration.token
    assert context.budget is registration.budget
    assert context.budget_dict is registration.budget_dict
    assert context.limits is registration.budget_dict["limits"]
    assert context.started is registration.budget_dict["_started"]
    assert context.guard is registration.guard
    assert registration.native_scope is registration.record.native_scope is None
    assert context.inputs[3] == "preview" and context.inputs[7] == "readonly"
    return registration, context


def assert_retired(registration):
    assert id(registration.token) not in operation._LIVE
    assert registration.owner_key not in operation._OWNERS
    assert registration.phase == registration.record.phase == "retired_failed"
    assert registration.budget._current_resources == registration.budget._pending_resources == 0
    assert not registration.budget._resource_pending and not registration.budget._resource_leases
    assert registration.budget._active is None
    assert registration.budget._confirmed == registration.budget._write_calls == 0
    assert registration.budget._uncertain_writes == 0


@pytest.mark.parametrize("error_type", [RuntimeError, StopIteration, KeyboardInterrupt, SystemExit])
def test_source_fixed_driver_delivers_exact_original_control_first(home, tmp_path, error_type):
    original = error_type("original source callback first")
    captured = []
    def callback():
        registration, context = live_original()
        assert registration.first is None
        assert context.unit_key is not None
        captured.append(registration)
        raise original
    with group.checked_exclusive_many((home,)) as group_token:
        with pytest.raises(error_type) as caught:
            operation.check_source_census_locked(home, group_token,
                source=source_value(tmp_path), operation_id=OPERATION_ID, check_cancelled=callback)
        assert caught.value is original
        assert len(captured) == 1
        assert_retired(captured[0])
        assert lifecycle.current_guard() is group.guard_original(group_token)
        assert not operation._NATIVE_DEBTS


@pytest.mark.parametrize("bad_call", ["missing", "invalid_selector", "unexpected_mode", "duplicate_target"])
def test_new_source_entry_routes_reentry_before_exact_binding(home, tmp_path, bad_call):
    seen = []
    with group.checked_exclusive_many((home,)) as group_token:
        def callback():
            registration = next(iter(operation._LIVE.values()))
            assert registration.kind == "check_only"
            try:
                if bad_call == "missing":
                    operation.check_source_census_locked()
                elif bad_call == "invalid_selector":
                    operation.check_source_census_locked(home, group_token,
                        source=None, operation_id=OPERATION_ID)
                elif bad_call == "unexpected_mode":
                    operation.check_source_census_locked(home, group_token,
                        source=source_value(tmp_path), operation_id=OPERATION_ID, mode="create")
                else:
                    operation.check_source_census_locked(home, group_token, target_profile=home,
                        source=source_value(tmp_path), operation_id=OPERATION_ID)
            except ERROR as error:
                assert error.code == "AUTHORITY_REPLICA_OPERATION1_REENTRANT"
                seen.append(error)
            assert len(seen) == 1
            assert registration.first is seen[0]
        with pytest.raises(ERROR) as caught:
            operation.check_operation_locked(home, group_token=group_token,
                source=source_value(tmp_path), mode="preview", operation_id=OPERATION_ID,
                check_cancelled=callback)
        assert caught.value is seen[0]
        assert not operation._LIVE and not operation._OWNERS


@pytest.mark.parametrize("entry", ["context", "attach", "checkpoint"])
def test_source_callback_cannot_adopt_or_reenter_original_context(home, tmp_path, entry):
    captured, seen = [], []
    def callback():
        registration, context = live_original()
        captured.append(registration)
        try:
            if entry == "context":
                operation._source_context_original(registration.token)
            elif entry == "attach":
                operation._attach_source_record_original(registration.token, object(), object())
            else:
                operation._source_checkpoint_original(registration.token, context.source_token, "source_begin")
        except ERROR as error:
            assert error.code == "AUTHORITY_REPLICA_OPERATION1_REENTRANT"
            seen.append(error)
    with group.checked_exclusive_many((home,)) as group_token:
        with pytest.raises(ERROR) as caught:
            operation.check_source_census_locked(home, group_token,
                source=source_value(tmp_path), operation_id=OPERATION_ID, check_cancelled=callback)
        assert caught.value is seen[0]
        assert len(captured) == len(seen) == 1
        assert_retired(captured[0])


def test_foreign_thread_context_probe_cannot_poison_original_owner(home, tmp_path):
    original = RuntimeError("owner cancels after foreign probe")
    captured = []
    def callback():
        registration, _context = live_original()
        captured.append(registration)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(operation._source_context_original, registration.token)
            with pytest.raises(ERROR) as caught:
                future.result()
        assert caught.value.code == "AUTHORITY_REPLICA_OPERATION1_OWNER_MISMATCH"
        assert registration.first is registration.record.first is None
        assert registration.budget._first is None
        raise original
    with group.checked_exclusive_many((home,)) as group_token:
        with pytest.raises(RuntimeError) as caught:
            operation.check_source_census_locked(home, group_token,
                source=source_value(tmp_path), operation_id=OPERATION_ID, check_cancelled=callback)
        assert caught.value is original
        assert len(captured) == 1
        assert_retired(captured[0])


@pytest.mark.parametrize("field", ["_read_bytes", "_source_read_bytes", "_read_calls", "_peak_resources"])
def test_source_numeric_counter_mirror_is_checked_against_actual_events(home, tmp_path, field):
    captured = []
    def callback():
        registration, _context = live_original()
        captured.append(registration)
        # The value remains a nonnegative exact int. Only actual event/ticket
        # validation rejects it; a widened type/monotonic validator would pass.
        setattr(registration.budget, field, getattr(registration.budget, field) + 1)
    with group.checked_exclusive_many((home,)) as group_token:
        with pytest.raises(ValueError) as caught:
            operation.check_source_census_locked(home, group_token,
                source=source_value(tmp_path), operation_id=OPERATION_ID, check_cancelled=callback)
        assert len(captured) == 1
        assert caught.value is captured[0].first is captured[0].budget._first
        assert "CHANGED" in caught.value.code or "UNSETTLED" in caught.value.code
        assert_retired(captured[0])


@pytest.mark.parametrize("constant,key", [("_ROOT", "request.json"), ("_LOCAL", "receipt.json")])
def test_original_catalog_rejects_in_place_fixed_codec_schema_change_after_restore(home, tmp_path, constant, key):
    mapping = getattr(raw2_contract, constant)
    original_value, original_mapping = mapping[key], mapping
    seen = []
    with group.checked_exclusive_many((home,)) as group_token:
        def callback():
            registration = next(iter(operation._LIVE.values()))
            assert registration.kind == "check_only"
            try:
                mapping[key] = "unexpected_schema_role"
                assert getattr(raw2_contract, constant) is original_mapping
                try:
                    operation._source_context_original(registration.token)
                except ERROR as error:
                    assert error.code == "AUTHORITY_REPLICA_OPERATION1_RECORD_CHANGED"
                    seen.append(error)
            finally:
                mapping[key] = original_value
            assert registration.first is seen[0]
        with pytest.raises(ERROR) as caught:
            operation.check_operation_locked(home, group_token=group_token,
                source=source_value(tmp_path), mode="preview", operation_id=OPERATION_ID,
                check_cancelled=callback)
        assert len(seen) == 1 and caught.value is seen[0]
        assert not operation._LIVE and not operation._OWNERS
    assert getattr(raw2_contract, constant) is original_mapping and mapping[key] == original_value


def test_actual_cross_file_source_unit_closes_under_one_original_ledger(home, tmp_path):
    actual_source, bundle, _root = prepare_source(home, tmp_path, kind="g1_with_g2")
    captures = []
    def callback():
        registration, context = live_original()
        if context.source_record is not None and context.source_record.phase == "selection" and not captures:
            captures.append((registration, context.source_record))
            assert context.budget_dict["_source_read_bytes"] == sum(len(raw) for raw in bundle["raw_files"].values())
    with group.checked_exclusive_many((home,)) as group_token:
        result = operation.check_source_census_locked(home, group_token, source=actual_source,
            operation_id=OPERATION_ID, check_cancelled=callback)
        assert result["unit_native_normal_closed"] is result["global_settled"] is True
        assert result["complete_preview"] is result["source_closed"] is result["producer"] is False
        assert result["copy_performed"] is result["data_restored"] is result["write_authority"] is result["sender_authority"] is False
        native_result = result["native"]
        scopes = native_result["scope_reports"]
        assert scopes["observer"]["begin"]["elapsed_ns"] <= result["source"]["source_begin"]["elapsed_ns"]
        actual_bytes = sum(len(raw) for raw in bundle["raw_files"].values())
        assert scopes["source"]["Q"]["package_read_bytes"] == scopes["source"]["Q"]["source_read_bytes"] == actual_bytes
        assert scopes["source"]["resource_acquires"] == scopes["source"]["resource_normal_closes"] > 0
        assert all(scope["normal_closed"] is True for scope in scopes.values())
        assert native_result["global_resources"]["resource_acquires"] == native_result["global_resources"]["resource_normal_closes"] > 0
        assert native_result["global_resources"]["current_resources"] == 0
        assert len(captures) == 1
        registration, source_record = captures[0]
        assert registration.phase == registration.record.phase == "closed"
        assert source_record.cleanup_attempted is source_record.cleanup_normal is True
        assert id(registration.token) not in operation._LIVE
        assert registration.owner_key not in operation._OWNERS
        assert lifecycle.current_guard() is group.guard_original(group_token)


def test_actual_captured_source_checkpoint_reentry_keeps_first_and_exhaustive_cleanup(home, tmp_path):
    actual_source, bundle, _root = prepare_source(home, tmp_path, kind="g1_with_g2")
    captured, seen = [], []
    def callback():
        registration, context = live_original()
        source_record = context.source_record
        if source_record is None or source_record.phase != "selection":
            return
        assert registration.budget._source_read_bytes == sum(len(raw) for raw in bundle["raw_files"].values())
        assert registration.budget._current_resources > 0
        captured.append((registration, source_record))
        try:
            operation._source_checkpoint_original(registration.token, context.source_token, "invalid-stage")
        except ERROR as error:
            assert error.code == "AUTHORITY_REPLICA_OPERATION1_REENTRANT"
            seen.append(error)
        assert registration.first is seen[0]
    with group.checked_exclusive_many((home,)) as group_token:
        with pytest.raises(ERROR) as caught:
            operation.check_source_census_locked(home, group_token, source=actual_source,
                operation_id=OPERATION_ID, check_cancelled=callback)
        assert len(captured) == len(seen) == 1
        assert caught.value is seen[0]
        registration, source_record = captured[0]
        assert_retired(registration)
        assert source_record.cleanup_attempted is source_record.cleanup_normal is True
        assert not operation._NATIVE_DEBTS
        assert lifecycle.current_guard() is group.guard_original(group_token)


def _forbidden_dispatch(*args, **kwargs):
    raise AssertionError("Caller dispatcher must be rejected before invocation")


@pytest.mark.parametrize("cache_name", ["_cache", "_cache2"])
@pytest.mark.parametrize("poison", ["caller_object", "wrong_pattern", "wrong_flags", "compiled_key", "hostile_key",
    "bytes_wrong_pattern", "bytes_wrong_flags"])
def test_actual_regex_cache_dispatch_poison_is_rejected_without_caller_hooks_and_stays_first(home, tmp_path, cache_name, poison):
    sha_pattern = r"[0-9a-f]{64}"
    original_pattern = re.compile(sha_pattern)
    wrong_pattern = re.compile(r"[0-9]{64}")
    wrong_flags = re.compile(sha_pattern, re.IGNORECASE)
    wrong_bytes_pattern = re.compile(b"[0-9]{64}")
    wrong_bytes_flags = re.compile(sha_pattern.encode("ascii"), re.IGNORECASE)
    cache = getattr(re, cache_name)
    original_items = tuple(dict.items(cache))
    original_cache = cache
    hooks, seen = [], []
    class CallerMatcher:
        def fullmatch(self, *args, **kwargs):
            hooks.append("fullmatch")
            raise AssertionError("Caller matcher invoked")
        def match(self, *args, **kwargs):
            hooks.append("match")
            raise AssertionError("Caller matcher invoked")
    class HostileText(str):
        def __hash__(self):
            hooks.append("hash")
            return str.__hash__(self)
        def __eq__(self, other):
            hooks.append("eq")
            return str.__eq__(self, other)
    with group.checked_exclusive_many((home,)) as group_token:
        def callback():
            registration = next(iter(operation._LIVE.values()))
            key = (str, sha_pattern, 0)
            replacement = original_pattern
            if poison == "caller_object":
                replacement = CallerMatcher()
            elif poison == "wrong_pattern":
                replacement = wrong_pattern
            elif poison == "wrong_flags":
                replacement = wrong_flags
            elif poison == "compiled_key":
                key, replacement = (re.Pattern, original_pattern, 0), wrong_pattern
            elif poison == "bytes_wrong_pattern":
                key, replacement = (bytes, sha_pattern.encode("ascii"), 0), wrong_bytes_pattern
            elif poison == "bytes_wrong_flags":
                key, replacement = (bytes, sha_pattern.encode("ascii"), 0), wrong_bytes_flags
            else:
                key = (str, HostileText("r777-unique-hostile-key"), 0)
            try:
                # Injection intentionally owns any hashing performed here.
                # The protected original entry must execute no caller hooks.
                # An existing equal plain key would retain that original key;
                # this one-entry actual cache also isolates the key/value gate
                # from the independent cache-capacity gate.
                dict.clear(cache)
                dict.__setitem__(cache, key, replacement)
                hooks.clear()
                try:
                    operation._source_context_original(registration.token)
                except ERROR as error:
                    assert error.code == "AUTHORITY_REPLICA_OPERATION1_RECORD_CHANGED"
                    seen.append(error)
                assert not hooks
            finally:
                # Clear drops even hostile keys without asking their hash/eq.
                dict.clear(cache)
                dict.update(cache, original_items)
            assert getattr(re, cache_name) is original_cache
            assert registration.first is seen[0]
        with pytest.raises(ERROR) as caught:
            operation.check_operation_locked(home, group_token=group_token,
                source=source_value(tmp_path), mode="preview", operation_id=OPERATION_ID,
                check_cancelled=callback)
        assert len(seen) == 1 and caught.value is seen[0]
        assert not operation._LIVE and not operation._OWNERS
    assert getattr(re, cache_name) is original_cache


@pytest.mark.parametrize("poison", ["detect_encoding_code", "re_match_binding", "re_match_code"])
def test_actual_json_re_helper_binding_or_code_poison_stays_first_after_restore(home, tmp_path, poison):
    module, name = (json, "detect_encoding") if poison == "detect_encoding_code" else (re, "match")
    function = getattr(module, name)
    original_code = function.__code__
    seen = []
    with group.checked_exclusive_many((home,)) as group_token:
        def callback():
            registration = next(iter(operation._LIVE.values()))
            try:
                if poison == "re_match_binding":
                    setattr(module, name, _forbidden_dispatch)
                else:
                    function.__code__ = _forbidden_dispatch.__code__
                try:
                    operation._source_context_original(registration.token)
                except ERROR as error:
                    assert error.code == "AUTHORITY_REPLICA_OPERATION1_RECORD_CHANGED"
                    seen.append(error)
            finally:
                setattr(module, name, function)
                function.__code__ = original_code
            assert registration.first is seen[0]
        with pytest.raises(ERROR) as caught:
            operation.check_operation_locked(home, group_token=group_token,
                source=source_value(tmp_path), mode="preview", operation_id=OPERATION_ID,
                check_cancelled=callback)
        assert len(seen) == 1 and caught.value is seen[0]
        assert not operation._LIVE and not operation._OWNERS
    assert getattr(module, name) is function and function.__code__ is original_code


@pytest.mark.parametrize("poison", ["incremental_factory", "encode_attribute", "hostile_own_key"])
def test_actual_selected_utf8_codec_info_poison_is_rejected_before_factory_and_stays_first(home, tmp_path, poison):
    selected = codecs.lookup("utf-8")
    dictionary = object.__getattribute__(selected, "__dict__")
    original_items = tuple(dict.items(dictionary))
    original_selected, original_dictionary = selected, dictionary
    hooks, seen = [], []
    class HostileKey(str):
        def __hash__(self):
            hooks.append("hash")
            return str.__hash__(self)
        def __eq__(self, other):
            hooks.append("eq")
            return str.__eq__(self, other)
    with group.checked_exclusive_many((home,)) as group_token:
        def callback():
            registration = next(iter(operation._LIVE.values()))
            try:
                if poison == "incremental_factory":
                    selected.incrementaldecoder = _forbidden_dispatch
                elif poison == "encode_attribute":
                    selected.encode = _forbidden_dispatch
                else:
                    dict.__setitem__(dictionary, HostileKey("new-selection-field"), _forbidden_dispatch)
                hooks.clear()
                try:
                    operation._source_context_original(registration.token)
                except ERROR as error:
                    assert error.code == "AUTHORITY_REPLICA_OPERATION1_RECORD_CHANGED"
                    seen.append(error)
                assert not hooks
            finally:
                dict.clear(dictionary)
                dict.update(dictionary, original_items)
            assert codecs.lookup("utf-8") is original_selected
            assert object.__getattribute__(selected, "__dict__") is original_dictionary
            assert registration.first is seen[0]
        with pytest.raises(ERROR) as caught:
            operation.check_operation_locked(home, group_token=group_token,
                source=source_value(tmp_path), mode="preview", operation_id=OPERATION_ID,
                check_cancelled=callback)
        assert len(seen) == 1 and caught.value is seen[0]
        assert not operation._LIVE and not operation._OWNERS
    assert codecs.lookup("utf-8") is original_selected
    assert object.__getattribute__(selected, "__dict__") is original_dictionary


@pytest.mark.parametrize("poison", ["caller_zero", "wrong_member_zero", "hostile_key",
    "T_value_hook", "T_value_int", "T_instance_method", "DEBUG_value_hook",
    "NOFLAG_bool_hook", "enum_call_code", "flag_and_code", "enum_sentinel_binding"])
def test_actual_regex_enum_dispatch_poison_rejects_caller_hooks_and_keeps_original_first(home, tmp_path, poison):
    value_map = type.__getattribute__(re.RegexFlag, "__dict__")["_value2member_map_"]
    original_map, original_items = value_map, tuple(dict.items(value_map))
    members = (re.T, re.DEBUG, re.NOFLAG)
    own_pins = tuple((member, object.__getattribute__(member, "__dict__"),
        tuple(dict.items(object.__getattribute__(member, "__dict__")))) for member in members)
    enum_call, flag_and = type(re.RegexFlag).__call__, re.RegexFlag.__and__
    original_sentinel = re.enum._not_given
    codes = tuple((function, function.__code__) for function in (enum_call, flag_and))
    hooks, seen = [], []
    class CallerValue:
        def __bool__(self):
            hooks.append("bool")
            raise AssertionError("Caller truthiness dispatched")
        def __and__(self, other):
            hooks.append("and")
            raise AssertionError("Caller arithmetic dispatched")
        def __rand__(self, other):
            hooks.append("rand")
            raise AssertionError("Caller arithmetic dispatched")
    class HostileInt(int):
        def __hash__(self):
            hooks.append("hash")
            return int.__hash__(self)
        def __eq__(self, other):
            hooks.append("eq")
            return int.__eq__(self, other)
    with group.checked_exclusive_many((home,)) as group_token:
        def callback():
            registration = next(iter(operation._LIVE.values()))
            try:
                if poison == "caller_zero":
                    dict.__setitem__(value_map, 0, CallerValue())
                elif poison == "wrong_member_zero":
                    dict.__setitem__(value_map, 0, re.T)
                elif poison == "hostile_key":
                    # Injection owns hashing. Clear avoids retaining an equal
                    # old plain int key; entry admission must perform no hooks.
                    dict.clear(value_map)
                    dict.__setitem__(value_map, HostileInt(510), re.NOFLAG)
                elif poison == "T_value_hook":
                    re.T._value_ = CallerValue()
                elif poison == "T_value_int":
                    re.T._value_ = 2
                elif poison == "T_instance_method":
                    dict.__setitem__(own_pins[0][1], "_get_value", _forbidden_dispatch)
                elif poison == "DEBUG_value_hook":
                    re.DEBUG._value_ = CallerValue()
                elif poison == "NOFLAG_bool_hook":
                    re.NOFLAG._value_ = CallerValue()
                elif poison == "enum_call_code":
                    enum_call.__code__ = _forbidden_dispatch.__code__
                elif poison == "enum_sentinel_binding":
                    re.enum._not_given = CallerValue()
                else:
                    flag_and.__code__ = _forbidden_dispatch.__code__
                hooks.clear()
                try:
                    operation._source_context_original(registration.token)
                except ERROR as error:
                    assert error.code == "AUTHORITY_REPLICA_OPERATION1_RECORD_CHANGED"
                    seen.append(error)
                assert not hooks
            finally:
                re.enum._not_given = original_sentinel
                for function, code in codes:
                    function.__code__ = code
                for _member, own, items in own_pins:
                    dict.clear(own)
                    dict.update(own, items)
                dict.clear(value_map)
                dict.update(value_map, original_items)
            assert type.__getattribute__(re.RegexFlag, "__dict__")["_value2member_map_"] is original_map
            assert registration.first is seen[0]
        with pytest.raises(ERROR) as caught:
            operation.check_operation_locked(home, group_token=group_token,
                source=source_value(tmp_path), mode="preview", operation_id=OPERATION_ID,
                check_cancelled=callback)
        assert len(seen) == 1 and caught.value is seen[0]
        assert not operation._LIVE and not operation._OWNERS
    assert type.__getattribute__(re.RegexFlag, "__dict__")["_value2member_map_"] is original_map
    assert all(function.__code__ is code for function, code in codes)
    assert re.enum._not_given is original_sentinel
    assert all(object.__getattribute__(member, "__dict__") is own for member, own, _items in own_pins)


@pytest.mark.parametrize("poison", ["repeat_index", "repeat_opcode", "repeat_key",
    "escape_bool", "escape_literal", "escape_key", "category_bool",
    "category_nested_index", "category_nested_opcode", "category_list_growth",
    "compiler_opcode_binding", "parser_opcode_binding"])
def test_actual_fixed_regex_table_poison_with_legal_cache_clear_keeps_original_first(home, tmp_path, poison):
    compiler, parser = re._compiler, re._parser
    original_repeat, original_literal = compiler.REPEAT, parser.LITERAL
    repeating, escapes, categories = compiler._REPEATING_CODES, parser.ESCAPES, parser.CATEGORIES
    table_pins = tuple((table, tuple(dict.items(table))) for table in (repeating, escapes, categories))
    nested = categories[r"\d"][1]
    original_nested = tuple(nested)
    cache_pins = tuple((cache, tuple(dict.items(cache))) for cache in (re._cache, re._cache2))
    hooks, seen = [], []
    class CallerCode:
        def __bool__(self):
            hooks.append("parser-bool")
            raise AssertionError("Caller parser truthiness executed")
        def __getitem__(self, key):
            hooks.append("compiler-index")
            raise AssertionError("Caller compiler/table indexing executed")
    class HostileInt(int):
        def __hash__(self):
            hooks.append("hash")
            return int.__hash__(self)
        def __eq__(self, other):
            hooks.append("eq")
            return int.__eq__(self, other)
    class HostileText(str):
        def __hash__(self):
            hooks.append("hash")
            return str.__hash__(self)
        def __eq__(self, other):
            hooks.append("eq")
            return str.__eq__(self, other)
    with group.checked_exclusive_many((home,)) as group_token:
        def callback():
            registration = next(iter(operation._LIVE.values()))
            try:
                if poison == "repeat_index":
                    dict.__setitem__(repeating, compiler.MAX_REPEAT, CallerCode())
                elif poison == "repeat_opcode":
                    dict.__setitem__(repeating, compiler.MAX_REPEAT,
                        (compiler.REPEAT, compiler.MAX_UNTIL, compiler.SUCCESS))
                elif poison == "repeat_key":
                    original = dict.pop(repeating, compiler.MAX_REPEAT)
                    dict.__setitem__(repeating, HostileInt(int.__int__(compiler.MAX_REPEAT)), original)
                elif poison == "escape_bool":
                    dict.__setitem__(escapes, r"\\", CallerCode())
                elif poison == "escape_literal":
                    dict.__setitem__(escapes, r"\\", (parser.LITERAL, 47))
                elif poison == "escape_key":
                    original = dict.pop(escapes, r"\\")
                    dict.__setitem__(escapes, HostileText(r"\\"), original)
                elif poison == "category_bool":
                    dict.__setitem__(categories, r"\d", CallerCode())
                elif poison == "category_nested_index":
                    nested[0] = CallerCode()
                elif poison == "category_nested_opcode":
                    nested[0] = (parser.CATEGORY, parser.CATEGORY_WORD)
                elif poison == "compiler_opcode_binding":
                    compiler.REPEAT = CallerCode()
                elif poison == "parser_opcode_binding":
                    parser.LITERAL = CallerCode()
                else:
                    nested.append((parser.CATEGORY, parser.CATEGORY_DIGIT))
                # Emptying these two actual caches is lawful evolution. The
                # selected compiler/parser tables must still be admitted before
                # their original miss path can index/test caller table values.
                for cache, _items in cache_pins:
                    dict.clear(cache)
                hooks.clear()
                try:
                    operation._source_context_original(registration.token)
                except ERROR as error:
                    assert error.code == "AUTHORITY_REPLICA_OPERATION1_RECORD_CHANGED"
                    seen.append(error)
                assert not hooks
            finally:
                compiler.REPEAT, parser.LITERAL = original_repeat, original_literal
                nested[:] = original_nested
                for table, items in table_pins:
                    dict.clear(table)
                    dict.update(table, items)
                for cache, items in cache_pins:
                    dict.clear(cache)
                    dict.update(cache, items)
            assert compiler._REPEATING_CODES is repeating
            assert parser.ESCAPES is escapes and parser.CATEGORIES is categories
            assert categories[r"\d"][1] is nested
            assert registration.first is seen[0]
        with pytest.raises(ERROR) as caught:
            operation.check_operation_locked(home, group_token=group_token,
                source=source_value(tmp_path), mode="preview", operation_id=OPERATION_ID,
                check_cancelled=callback)
        assert len(seen) == 1 and caught.value is seen[0]
        assert not operation._LIVE and not operation._OWNERS
    assert re._cache is cache_pins[0][0] and re._cache2 is cache_pins[1][0]


def test_healthy_fixed_regex_tables_allow_actual_empty_cache_compilation_and_original_check(home, tmp_path):
    cache_pins = tuple((cache, tuple(dict.items(cache))) for cache in (re._cache, re._cache2))
    seen = []
    try:
        with group.checked_exclusive_many((home,)) as group_token:
            def callback():
                registration = next(iter(operation._LIVE.values()))
                for cache, _items in cache_pins:
                    dict.clear(cache)
                # These are the actual two fixed compiler/parser intentions.
                # This callback checks lawful empty caches/normal refill; it
                # does not claim source census or native resource acceptance.
                assert re.fullmatch(r"[0-9a-f]{64}", SOURCE_SHA) is not None
                assert re.match(r"^[A-Za-z]:[\\/]", "C:\\source") is not None
                assert registration.first is None
                seen.append(registration)
            result = operation.check_operation_locked(home, group_token=group_token,
                source=source_value(tmp_path), mode="preview", operation_id=OPERATION_ID,
                check_cancelled=callback)
            assert len(seen) == 1 and seen[0].first is None
            assert result["kind"] == "replica_operation_check"
            assert result["original_registry_retired"] is True
            assert result["source_observed"] is False
            assert not operation._LIVE and not operation._OWNERS
    finally:
        for cache, items in cache_pins:
            dict.clear(cache)
            dict.update(cache, items)
    assert re._cache is cache_pins[0][0] and re._cache2 is cache_pins[1][0]
