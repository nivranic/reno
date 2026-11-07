"""Synthetic H06 target/runtime seams; no normal Reno imports or native I/O.

The formal target/runtime/pure projection are loaded under a disposable package.
SH, node handles, privacy checks and status are declared fixtures. These cases
prove orchestration/error boundaries, not real locks, sameFD or ACL acceptance.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
NODE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER_NODE = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
INIT_ID = "11111111-1111-4111-8111-111111111111"
BINDING_SHA = "a" * 64


class FixtureStorageError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class FixtureFileError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class FixturePrimitiveError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class FalseCallback:
    def __init__(self, action=None):
        self.calls, self.bool_calls, self.action = 0, 0, action

    def __bool__(self):
        self.bool_calls += 1
        raise AssertionError("The actual cancellation callable was truth-tested")

    def __call__(self):
        self.calls += 1
        if self.action is not None:
            self.action()


class _Shared:
    def __init__(self, fixture, profile, timeout, check_cancelled):
        self.f = fixture
        fixture.shared_arguments.append((profile, timeout, check_cancelled))
        # Match the existing primitive's historical truth-value fallback. A
        # normal wrapper supplied by observe must keep a false callback alive.
        self.check = check_cancelled or (lambda: None)

    def __enter__(self):
        self.f.events.append("shared.enter")
        self.check()
        if self.f.shared_enter_error is not None:
            raise self.f.shared_enter_error
        self.f.shared_active = True
        return self

    def __exit__(self, kind, error, traceback):
        self.f.events.append("shared.exit")
        self.f.shared_exit_primary.append(error)
        self.f.shared_active = False
        if self.f.shared_exit_error is not None:
            raise self.f.shared_exit_error
        return self.f.suppress_shared


class _NodeHold:
    def __init__(self, fixture, path, maximum, check_cancelled, collect):
        self.f = fixture
        fixture.node_arguments.append((path, maximum, check_cancelled, collect))
        self.check = check_cancelled or (lambda: None)
        self.raw = fixture.node_raw
        self.sha256 = hashlib.sha256(self.raw).hexdigest()

    def __enter__(self):
        self.f.events.append("node.enter")
        if not self.f.shared_active:
            raise AssertionError("Node holder entered outside the original SH")
        self.check()
        if self.f.node_enter_error is not None:
            raise self.f.node_enter_error
        self.f.node_active = True
        try:
            self.verify()
        except BaseException:
            self.f.node_active = False
            self.f.events.append("node.enter-cleanup")
            raise
        return self

    def verify(self):
        self.f.verify_calls += 1
        self.f.events.append("node.verify")
        if not self.f.node_active or not self.f.shared_active:
            raise AssertionError("Node verification lost its owned window")
        self.check()
        if (self.f.verify_error is not None
                and self.f.verify_calls == self.f.verify_error_on):
            raise self.f.verify_error

    def __exit__(self, kind, error, traceback):
        self.f.node_exit_primary.append(error)
        try:
            if kind is None:
                self.verify()
        finally:
            self.f.node_active = False
            self.f.events.append("node.exit")
            if self.f.node_exit_error is not None:
                raise self.f.node_exit_error
        return self.f.suppress_node


class _Fixture:
    def __init__(self):
        base = Path(os.path.abspath(os.path.join(os.path.sep, "h06-synthetic")))
        paths = (base / "data/reno.db", base / "media", base / "data/generated",
                 base / "data/sync-checkpoints")
        self.profile = SimpleNamespace(database=paths[0], media=paths[1], reports=paths[2],
                                       checkpoints=paths[3], identity=tuple(
            os.path.normcase(os.path.abspath(os.fspath(path))) for path in paths))
        self.facility = os.path.normcase(os.path.abspath(os.fspath(
            paths[0].parent / ".reno-personal-authority-fixture")))
        self.node_raw = json.dumps({"instance_id": NODE_ID}).encode("utf-8")
        self.events, self.shared_arguments, self.node_arguments, self.status_arguments = [], [], [], []
        self.shared_exit_primary, self.node_exit_primary = [], []
        self.private_arguments = []
        self.shared_active = self.node_active = False
        self.shared_enter_error = self.shared_exit_error = None
        self.node_enter_error = self.node_exit_error = None
        self.verify_error, self.verify_error_on, self.verify_calls = None, 2, 0
        self.suppress_shared = self.suppress_node = False
        self.status_error, self.status_action, self.private_error = None, None, None
        self.report = {"state": "ready", "filesystem_only": True,
                       "init_operation_id": INIT_ID, "declared_binding_sha256": BINDING_SHA,
                       "windows_directory_fsync_claimed": False, "fact_count": 1,
                       "total_bytes": 64, "head_state": "matches"}

    def target(self, **changes):
        value = {"format_version": 1, "facility_root": self.facility,
                 "profile_paths": list(self.profile.identity), "expected_node_id": NODE_ID,
                 "expected_declared_binding_sha256": BINDING_SHA}
        value.update(changes)
        return json.dumps(value, ensure_ascii=True)

    def private(self, path, *, directory):
        self.private_arguments.append((path, directory))
        self.events.append("node.private")
        if not self.shared_active:
            raise AssertionError("Privacy observation occurred without SH")
        if self.private_error is not None:
            raise self.private_error

    def status(self, root, **options):
        self.events.append("status")
        self.status_arguments.append((root, options))
        if not self.shared_active or not self.node_active:
            raise AssertionError("Status lost the same owned node/SH window")
        options["check_cancelled"]()
        if self.status_action is not None:
            self.status_action(options["check_cancelled"])
        if self.status_error is not None:
            raise self.status_error
        return self.report


class TestPersonalAuthorityReadRuntime(unittest.TestCase):
    def setUp(self):
        self.f = _Fixture()
        self.package_name = "h06_read_runtime_synthetic"
        package = ModuleType(self.package_name)
        package.__path__ = []
        self.modules_patch = patch.dict(sys.modules, {self.package_name: package})
        self.modules_patch.start()
        self.addCleanup(self.modules_patch.stop)
        for leaf in ("storage_admin", "storage_lifecycle", "personal_preservation_files",
                     "personal_authority_files"):
            module = ModuleType(self.package_name + "." + leaf)
            sys.modules[module.__name__] = module
            setattr(package, leaf, module)
        admin = sys.modules[self.package_name + ".storage_admin"]
        admin.profile = lambda: self.f.profile
        lifecycle = sys.modules[self.package_name + ".storage_lifecycle"]
        lifecycle.StorageLifecycleError = FixtureStorageError
        lifecycle.shared = lambda profile, *, timeout, check_cancelled: _Shared(
            self.f, profile, timeout, check_cancelled)
        files = sys.modules[self.package_name + ".personal_preservation_files"]
        files.PreservationFileError = FixtureFileError
        files.require_private = self.f.private
        files.FileHold = lambda path, *, maximum, check_cancelled, collect: _NodeHold(
            self.f, path, maximum, check_cancelled, collect)
        authority = sys.modules[self.package_name + ".personal_authority_files"]
        authority.FilePrimitiveError = FixturePrimitiveError
        authority.status = self.f.status
        self.target_module = self._load("personal_authority_read_target")
        self.contract = self._load("personal_authority_read_contract")
        self.runtime = self._load("personal_authority_read")
        # Only this module's declared env seam is populated; no real environment
        # values, config, database, node files or native facilities are read.
        self.runtime.os = SimpleNamespace(environ={self.target_module.ENVIRONMENT_VARIABLE: self.f.target()})

    def _load(self, leaf):
        name = self.package_name + "." + leaf
        spec = importlib.util.spec_from_file_location(name, ROOT / "reno" / (leaf + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        setattr(sys.modules[self.package_name], leaf, module)
        spec.loader.exec_module(module)
        return module

    def assertCode(self, callback, code):
        with self.assertRaises(self.runtime.AuthorityReadError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(str(raised.exception), code)
        self.assertNotIn(NODE_ID, str(raised.exception))
        self.assertNotIn(self.f.facility, str(raised.exception))
        return raised.exception

    def assertClosed(self):
        self.assertFalse(self.f.node_active)
        self.assertFalse(self.f.shared_active)

    def test_target_current_four_paths_array_hash_and_frozen_selection(self):
        value = self.target_module.parse_target(self.f.target(), profile=self.f.profile)
        expected = hashlib.sha256(json.dumps(list(self.f.profile.identity), ensure_ascii=False,
                                            separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(value.profile_paths, self.f.profile.identity)
        self.assertEqual(value.profile_sha256, expected)
        self.assertEqual(os.fspath(value.facility_root), self.f.facility)
        with self.assertRaises(AttributeError):
            value.expected_node_id = OTHER_NODE
        self.assertEqual(self.f.events, [])

    def test_target_missing_and_invalid_json_stop_before_resources(self):
        for raw, code in ((None, "TARGET_NOT_CONFIGURED"), ("", "TARGET_NOT_CONFIGURED"),
                          ("[]", "TARGET_INVALID"), ("{", "TARGET_INVALID"),
                          (self.f.target().encode(), "TARGET_INVALID")):
            with self.subTest(code=code):
                self.assertCode(lambda: self.target_module.parse_target(raw, profile=self.f.profile), code)
        self.assertEqual(self.f.events, [])

    def test_target_duplicate_extra_and_strict_format(self):
        duplicate = self.f.target()[:-1] + ', "format_version":1}'
        for raw in (duplicate, self.f.target(extra=True), self.f.target(format_version=True),
                    self.f.target(format_version=1.0), self.f.target(format_version="1")):
            with self.subTest():
                self.assertCode(lambda: self.target_module.parse_target(raw, profile=self.f.profile), "TARGET_INVALID")

    def test_target_byte_cap_and_lone_surrogate(self):
        for raw in (self.f.target() + " " * 32768,
                    self.f.target(facility_root=self.f.facility + "\ud800"), "\ud800"):
            with self.subTest():
                self.assertCode(lambda: self.target_module.parse_target(raw, profile=self.f.profile), "TARGET_INVALID")

    def test_target_profile_tuple_requires_exact_order_spelling_and_types(self):
        paths = list(self.f.profile.identity)
        reordered = [paths[1], paths[0], *paths[2:]]
        cases = ((self.f.target(profile_paths=reordered), "TARGET_PROFILE_MISMATCH"),
                 (self.f.target(profile_paths=[*paths[:3], paths[3] + "-other"]), "TARGET_PROFILE_MISMATCH"),
                 (self.f.target(profile_paths=paths[:3]), "TARGET_INVALID"),
                 (self.f.target(profile_paths=[None, *paths[1:]]), "TARGET_INVALID"))
        for raw, code in cases:
            with self.subTest(code=code):
                self.assertCode(lambda: self.target_module.parse_target(raw, profile=self.f.profile), code)

    def test_target_uuid_and_sha_have_no_nil_or_noncanonical_aliases(self):
        for changed in ({"expected_node_id": "00000000-0000-0000-0000-000000000000"},
                        {"expected_node_id": NODE_ID.upper()}, {"expected_node_id": NODE_ID.replace("-", "")},
                        {"expected_node_id": True}, {"expected_declared_binding_sha256": "A" * 64},
                        {"expected_declared_binding_sha256": "a" * 63}):
            with self.subTest():
                self.assertCode(lambda: self.target_module.parse_target(self.f.target(**changed),
                                                                        profile=self.f.profile), "TARGET_INVALID")

    def test_target_facility_direct_child_and_original_canonical_spelling(self):
        parent = os.fspath(Path(self.f.facility).parent)
        name = Path(self.f.facility).name
        aliases = [".reno-personal-authority-relative", parent,
                   os.path.join(parent, "unselected"),
                   os.path.join(parent, "child", name),
                   parent + os.sep + "." + os.sep + name,
                   parent + os.sep + os.sep + name, self.f.facility + os.sep]
        if os.name == "nt":
            aliases.extend([self.f.facility.upper(), self.f.facility.replace("\\", "/")])
        for facility in aliases:
            with self.subTest():
                self.assertCode(lambda: self.target_module.parse_target(
                    self.f.target(facility_root=facility), profile=self.f.profile), "TARGET_INVALID")

    def test_ready_status_exact_pins_and_response_after_all_exits(self):
        result = self.runtime.observe()
        root, options = self.f.status_arguments[0]
        expected = self.target_module.parse_target(self.f.target(), profile=self.f.profile)
        self.assertEqual(root, expected.facility_root)
        self.assertEqual(set(options), {"timeout", "check_cancelled", "expected_declared_binding_sha256",
                                       "expected_node_id", "expected_profile_sha256"})
        self.assertEqual(options["timeout"], 0)
        self.assertEqual(options["expected_declared_binding_sha256"], BINDING_SHA)
        self.assertEqual(options["expected_node_id"], NODE_ID)
        self.assertEqual(options["expected_profile_sha256"], expected.profile_sha256)
        profile, timeout, _poll = self.f.shared_arguments[0]
        self.assertIs(profile, self.f.profile)
        self.assertEqual(timeout, 0)
        path, maximum, _poll, collect = self.f.node_arguments[0]
        self.assertEqual(path, self.f.profile.database.parent / "node-identity.json")
        self.assertEqual((maximum, collect), (4096, True))
        self.assertGreaterEqual(self.f.verify_calls, 3)
        self.assertLess(self.f.events.index("status"), self.f.events.index("node.exit"))
        self.assertLess(self.f.events.index("node.exit"), self.f.events.index("shared.exit"))
        self.assertEqual(result["reported_filesystem_state"], "ready")
        self.assertEqual(result["observation_error_code"], None)
        self.assertEqual((result["effective_authority_state"], result["write_authority"],
                          result["handoff_authority"]), ("unknown", "not_established", "not_established"))
        encoded = json.dumps(result)
        for private in (NODE_ID, INIT_ID, BINDING_SHA, self.f.facility, "profile_paths"):
            self.assertNotIn(private, encoded)
        self.assertClosed()

    def test_absent_reserved_and_unavailable_project_actual_report_only(self):
        for report, code in (({"state": "absent", "filesystem_only": True}, None),
                             ({"state": "reserved", "filesystem_only": True, "init_operation_id": INIT_ID}, None),
                             ({"state": "unavailable", "filesystem_only": True, "code": "FILES_BUSY"}, "FILES_BUSY")):
            with self.subTest(state=report["state"]):
                self.f.report = report
                result = self.runtime.observe()
                self.assertEqual(result["reported_filesystem_state"], report["state"])
                self.assertEqual(result["observation_error_code"], code)
                self.assertEqual(result["effective_authority_state"], "unknown")
                self.assertClosed()

    def test_env_is_selected_per_call_without_cached_ready(self):
        self.runtime.observe()
        self.runtime.os.environ = {}
        self.assertCode(self.runtime.observe, "TARGET_NOT_CONFIGURED")
        self.assertEqual(len(self.f.status_arguments), 1)

    def test_false_callback_is_called_without_truth_value_dispatch(self):
        callback = FalseCallback()
        self.runtime.observe(check_cancelled=callback)
        self.assertGreater(callback.calls, 0)
        self.assertEqual(callback.bool_calls, 0)
        self.assertClosed()

    def test_node_duplicate_extra_noncanonical_and_mismatch_stop_status(self):
        cases = ((b'{"instance_id":"' + NODE_ID.encode() + b'","instance_id":"' + NODE_ID.encode() + b'"}', "NODE_INVALID"),
                 (json.dumps({"instance_id": NODE_ID, "grant": True}).encode(), "NODE_INVALID"),
                 (json.dumps({"instance_id": NODE_ID.upper()}).encode(), "NODE_INVALID"),
                 (json.dumps({"instance_id": OTHER_NODE}).encode(), "NODE_MISMATCH"))
        for raw, code in cases:
            with self.subTest(code=code):
                self.f.node_raw = raw
                self.assertCode(self.runtime.observe, code)
                self.assertEqual(self.f.status_arguments, [])
                self.assertClosed()

    def test_actual_profile_change_during_status_discards_ready(self):
        def change(_poll):
            self.f.profile.identity = (*self.f.profile.identity[:3], self.f.profile.identity[3] + "-changed")
        self.f.status_action = change
        self.assertCode(self.runtime.observe, "TARGET_PROFILE_MISMATCH")
        self.assertClosed()

    def test_pending_callback_identity_wins_over_both_cleanup_failures(self):
        original = RuntimeError("synthetic callback primary")
        self.f.node_exit_error = OSError("synthetic node cleanup")
        self.f.shared_exit_error = OSError("synthetic SH cleanup")
        def action():
            if "status" in self.f.events:
                raise original
        callback = FalseCallback(action)
        with self.assertRaises(RuntimeError) as raised:
            self.runtime.observe(check_cancelled=callback)
        self.assertIs(raised.exception, original)
        self.assertIs(self.f.node_exit_primary[-1], original)
        self.assertIs(self.f.shared_exit_primary[-1], original)
        self.assertEqual(callback.bool_calls, 0)
        self.assertIn("AUTHORITY_READ_CLEANUP_FAILED", original.__notes__)
        self.assertClosed()

    def test_baseexception_controls_remain_original_objects(self):
        for original in (KeyboardInterrupt(), SystemExit(7), GeneratorExit()):
            with self.subTest(kind=type(original).__name__):
                def callback():
                    raise original
                with self.assertRaises(type(original)) as raised:
                    self.runtime.observe(check_cancelled=callback)
                self.assertIs(raised.exception, original)
        self.assertEqual(self.f.events, [])

    def test_callback_source_class_failure_is_not_classified_or_swallowed(self):
        original = FixtureStorageError("STORAGE_BUSY")
        def action():
            if "status" in self.f.events:
                raise original
        with self.assertRaises(FixtureStorageError) as raised:
            self.runtime.observe(check_cancelled=action)
        self.assertIs(raised.exception, original)
        self.assertClosed()

    def test_status_swallowing_callback_oserror_cannot_return_unavailable(self):
        original = OSError("synthetic callback OS error")
        def callback():
            if "status" in self.f.events:
                raise original
        def swallowed_status(root, **options):
            self.f.events.append("status")
            try:
                options["check_cancelled"]()
            except OSError:
                return {"state": "unavailable", "filesystem_only": True, "code": "FILES_IO_UNAVAILABLE"}
        self.runtime.authority_files.status = swallowed_status
        with self.assertRaises(OSError) as raised:
            self.runtime.observe(check_cancelled=callback)
        self.assertIs(raised.exception, original)
        self.assertClosed()

    def test_normal_node_or_SH_close_failure_never_delivers_ready(self):
        self.f.node_exit_error = FixtureFileError("PERSONAL_PRESERVATION_FILE_CHANGED")
        self.assertCode(self.runtime.observe, "NODE_CHANGED")
        self.assertClosed()
        self.f.node_exit_error = None
        self.f.shared_exit_error = OSError("synthetic late SH close")
        self.assertCode(self.runtime.observe, "FILESYSTEM_UNAVAILABLE")
        self.assertClosed()

    def test_node_final_same_holder_failure_is_finite_changed_reason(self):
        self.f.verify_error = FixtureFileError("PERSONAL_PRESERVATION_FILE_CHANGED")
        self.assertCode(self.runtime.observe, "NODE_CHANGED")
        self.assertEqual(len(self.f.status_arguments), 1)
        self.assertClosed()

    def test_busy_uninitialized_and_other_storage_errors_are_distinct_finite_reasons(self):
        for original_code, expected in (("STORAGE_BUSY", "STORAGE_BUSY"),
                                        ("STORAGE_NOT_INITIALIZED", "STORAGE_NOT_INITIALIZED"),
                                        ("unknown-synthetic-details", "STORAGE_UNAVAILABLE")):
            with self.subTest(code=expected):
                self.f.shared_enter_error = FixtureStorageError(original_code)
                self.assertCode(self.runtime.observe, expected)
                self.assertClosed()
        self.assertEqual(self.f.status_arguments, [])

    def test_node_privacy_failure_has_finite_unavailable_reason(self):
        self.f.private_error = FixtureFileError("PERSONAL_PRESERVATION_PRIVATE_MODE_UNPROVEN")
        self.assertCode(self.runtime.observe, "NODE_FILE_UNAVAILABLE")
        self.assertEqual(self.f.status_arguments, [])
        self.assertClosed()

    def test_unknown_programmer_error_and_bad_report_are_not_fake_reports(self):
        original = RuntimeError("synthetic programmer error")
        self.f.status_error = original
        with self.assertRaises(RuntimeError) as raised:
            self.runtime.observe()
        self.assertIs(raised.exception, original)
        self.assertClosed()
        self.f.status_error = None
        self.f.report = {"state": "ready", "filesystem_only": True}
        with self.assertRaises(self.contract.AuthorityReadContractError):
            self.runtime.observe()
        self.assertClosed()

    def test_context_suppression_does_not_erase_original_error(self):
        original = RuntimeError("synthetic unsuppressed primary")
        self.f.status_error = original
        self.f.suppress_node = self.f.suppress_shared = True
        with self.assertRaises(RuntimeError) as raised:
            self.runtime.observe()
        self.assertIs(raised.exception, original)
        self.assertClosed()

    def test_API_cancel_error_signature_has_finite_status(self):
        error = self.runtime.AuthorityReadError("OBSERVATION_CANCELLED", 409)
        self.assertEqual((error.code, error.status, str(error)), ("OBSERVATION_CANCELLED", 409, "OBSERVATION_CANCELLED"))
        self.assertEqual(self.runtime.AuthorityReadError("OBSERVATION_CANCELLED", True).status, 503)
        self.assertEqual(self.runtime.AuthorityReadError("untrusted-private-value", 999).code, "TARGET_INVALID")
