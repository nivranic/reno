"""Pure format-2/Q1 declarations; no claim, I/O or completion capability.

The immutable graph binds actual supplied control bytes. A declared inventory
is still only data: native admission, fd observations and A/B/C stay outside
this module. Format-1 validation and its one-attempt meaning are unchanged.
"""
from __future__ import annotations

from functools import wraps
import hashlib
import posixpath
import re

from . import personal_authority_raw_preservation_contract as raw1
from . import personal_authority_preservation_contract as c2
from . import personal_authority_source_projection as projection
from . import personal_authority_resume_policy as policy


FORMAT_VERSION = 2
SCOPE = "raw-byte-preservation"
DIRECTORY = policy.DIRECTORY
_MAX_FACT = 128 * 1024**2
_EMPTY_SHA = hashlib.sha256(b"").hexdigest()
_ROOT = {"request.json": "request", "source-metadata.json": "source_metadata",
         "source-projection.json": "source_projection", "selection.json": "selection"}
_LOCAL = {"run-attempt.json": "run_attempt", "capsule.json": "capsule",
          "source-verified.json": "source_verified", "manifest.json": "manifest",
          "receipt.json": "receipt"}
_KINDS = frozenset({"request", "run_attempt", "selection", "copy_attempt", "copy_record",
                    "source_verified", "manifest", "receipt", "failure"})
_H0 = frozenset({"format_version", "kind", "operation_id"})
_HR = _H0 | {"slot", "run_id"}
_FIXED_DIRS = frozenset({"copy-attempts", "payload", "copy-records", "failures"})
_UUID = c2._UUID_TEXT
_COPY_ATTEMPT = re.compile(rf"runs/(01|02)/copy-attempts/({_UUID})\.json\Z")
_COPY_RECORD = re.compile(r"runs/(01|02)/copy-records/(f[0-9]{8})\.json\Z")
_FAILURE = re.compile(rf"runs/(01|02)/failures/({_UUID})\.json\Z")
_TEMP = re.compile(rf"runs/(01|02)/\.write-({_UUID})\.tmp\Z")
_PAYLOAD = re.compile(rf"runs/(01|02)/payload/({_UUID})/(f[0-9]{{8}})\.raw\Z")
_SELECTED = {"source_entry_id", "origin_slot", "origin_run_id", "copy_record_relative_key",
             "copy_record_sha256", "copy_record_byte_size"}
_FILE_FIELDS = {"relative_key", "parent_identity", "path_identity", "handle_identity", "sha256", "byte_size"}
_DIR_FIELDS = {"relative_key", "identity", "children"}
_PHASES = raw1._PHASES | {"admission", "bootstrap_request", "bootstrap_selection", "prefix", "reuse"}
_FAILURE_SUFFIXES = raw1._FAILURE_SUFFIXES | {"ADMISSION_REQUIRED", "ADMISSION_INVALID", "ATTEMPTS_EXHAUSTED",
    "ANCHOR_MISMATCH", "SELECTION_REQUIRED", "HISTORY_CHANGED", "SOURCE_CHANGED"}


class AuthorityRawPreservation2ContractError(ValueError):
    def __init__(self, suffix):
        self.code = "AUTHORITY_RAW_PRESERVATION2_" + suffix
        super().__init__(self.code)


Error = AuthorityRawPreservation2ContractError


def _fail(suffix):
    raise Error(suffix) from None


def _checked(function):
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Error:
            raise
        except raw1.Error as exc:
            _fail(exc.code.removeprefix("AUTHORITY_RAW_PRESERVATION1_"))
        except policy.Error as exc:
            _fail(exc.code.removeprefix("AUTHORITY_RAW_PRESERVATION2_POLICY_"))
        except (c2.AuthorityPreservationContractError, projection.AuthoritySourceProjectionError):
            _fail("DECLARATION_INVALID")
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, UnicodeError,
                RecursionError, OverflowError, RuntimeError):
            _fail("TYPE_INVALID")
    return checked


_same = raw1._same
_exact = raw1._exact
_u = raw1._u
_sha = raw1._sha
_uuid = raw1._uuid
_entry = raw1._entry
_key = raw1._key
_ordered = raw1._ordered
_canonical = raw1._canonical
_digest = raw1._digest


def _slot(value):
    policy.slot_reservation(value)
    return value


def _run(slot):
    return f"runs/{_slot(slot):02d}"


def _late(slot):
    return {_run(slot) + "/" + name for name in ("source-verified.json", "manifest.json", "receipt.json")}


def _header(value, kind, fields, *, run=True):
    _exact(value, (_HR if run else _H0) | set(fields))
    if type(value["format_version"]) is not int or value["format_version"] != 2:
        _fail("VERSION_INVALID")
    _same(value["kind"], "authority_raw_preservation_" + kind)
    _uuid(value["operation_id"])
    if run:
        _slot(value["slot"])
        _uuid(value["run_id"])


def _fact_path(path):
    _key(path)
    if path in _ROOT:
        return _ROOT[path], None, None
    parts = path.split("/")
    if len(parts) == 3 and parts[0] == "runs" and parts[1] in ("01", "02") and parts[2] in _LOCAL:
        return _LOCAL[parts[2]], int(parts[1]), None
    for pattern, kind in ((_COPY_ATTEMPT, "copy_attempt"), (_COPY_RECORD, "copy_record"), (_FAILURE, "failure")):
        match = pattern.fullmatch(path)
        if match:
            identifier = match[2]
            (_entry if kind == "copy_record" else _uuid)(identifier)
            return kind, int(match[1]), identifier
    _fail("FACT_PATH_INVALID")


class _SourceView:
    """Fixed pure reuse of the unchanged S1 metadata validator, not an owner."""
    def __init__(self, graph):
        self.graph = graph

    def request(self):
        request = self.graph.request()
        return {"operation_id": request["operation_id"], "profile": request["profile"],
                "limits": request["slot_limits"]}


class _Graph:
    def __init__(self, fact_bytes=None, maximum=None):
        self.maximum = raw1._maximum(maximum)
        self.raw, self.cache, self.loading = {}, {}, set()
        self.total_bytes = 0
        self.source_entries = None
        self.source_identities = None
        self.copy_ids, self.copy_entries = {}, {}
        if fact_bytes is not None:
            if type(fact_bytes) is not dict or len(fact_bytes) > 300_032:
                _fail("TYPE_INVALID")
            if any(type(path) is not str for path in fact_bytes):
                _fail("TYPE_INVALID")
            for path, raw in fact_bytes.items():
                self.add(path, raw)

    @_checked
    def add(self, path, raw):
        kind, _, _ = _fact_path(path)
        if type(raw) is not bytes:
            _fail("TYPE_INVALID")
        cap = min(self.maximum, policy.MAX_REQUEST_BYTES) if kind == "request" else self.maximum
        if len(raw) > cap:
            _fail("LIMIT")
        if path in self.raw:
            _same(raw, self.raw[path], "FACT_CHANGED")
            return
        if len(self.raw) >= 300_032 or self.total_bytes + len(raw) > 128 * 1024**3:
            _fail("LIMIT")
        match = _COPY_ATTEMPT.fullmatch(path)
        if match:
            previous = self.copy_ids.get(match[2])
            if previous is not None and previous != path:
                _fail("DUPLICATE_COPY")
            self.copy_ids[match[2]] = path
        self.raw[path] = raw
        self.total_bytes += len(raw)

    def bytes(self, path):
        if path not in self.raw:
            _fail("FACT_REQUIRED")
        return self.raw[path]

    def sha(self, path):
        return _digest(self.bytes(path))

    def cap(self, kind):
        return min(self.maximum, policy.MAX_REQUEST_BYTES if kind == "request" else 16384 if kind == "failure" else _MAX_FACT)

    @_checked
    def load(self, path, kind=None):
        actual, _, _ = _fact_path(path)
        if kind is not None:
            _same(kind, actual, "FACT_PATH_INVALID")
        if path in self.cache:
            return self.cache[path]
        if path in self.loading:
            _fail("FACT_CYCLE")
        self.loading.add(path)
        try:
            value = self.normalize_at(path, actual, raw1._parse(self.bytes(path), self.cap(actual)))
            self.cache[path] = value
            return value
        finally:
            self.loading.remove(path)

    @_checked
    def normalize_at(self, path, kind, value):
        actual, slot, identifier = _fact_path(path)
        _same(kind, actual, "FACT_PATH_INVALID")
        copied = raw1._snapshot(value, self.cap(kind))
        getattr(self, "validate_" + kind)(copied, slot=slot)
        if kind not in {"capsule", "source_metadata", "source_projection"}:
            if slot is not None:
                _same(copied["slot"], slot)
            if kind == "copy_attempt":
                _same(copied["copy_id"], identifier)
            elif kind == "copy_record":
                _same(copied["copy"]["source_entry_id"], identifier)
            elif kind == "failure":
                _same(copied["run_id"], identifier)
        return copied

    def request(self):
        return self.load("request.json")

    def limits(self):
        return self.request()["slot_limits"]

    def source(self):
        return self.load("source-metadata.json")

    def selection(self):
        return self.load("selection.json")

    def attempt(self, slot):
        return self.load(_run(slot) + "/run-attempt.json")

    def bind(self, value, *, run=True):
        request = self.request()
        _same(value["operation_id"], request["operation_id"])
        if "request_sha256" in value:
            _same(value["request_sha256"], self.sha("request.json"))
        if run:
            attempt = self.attempt(value["slot"])
            _same(value["run_id"], attempt["run_id"])
        return request

    def selected_binding(self, value):
        self.bind(value)
        self.selection()
        _same(value["selection_sha256"], self.sha("selection.json"))

    def source_entry(self, entry_id):
        self.selection()
        if self.source_entries is None:
            self.source_entries = {row["entry_id"]: row for row in self.source()["capsule"]["files"]}
        found = self.source_entries.get(_entry(entry_id))
        if found is None:
            _fail("ENTRY_INVALID")
        return found

    def forbidden(self, *, include_operation=True):
        if self.source_identities is None:
            request = self.request()
            source = self.source()["capsule"]
            identities = {c2._directory_identity(row["source_identity"]) for row in source["directories"]}
            identities.update(c2._directory_identity(row["identity"]) for row in request["profile"]["roots"].values())
            identities.update((row["source_path_identity"]["device"], row["source_path_identity"]["inode"]) for row in source["files"])
            identities.add(c2._directory_identity(request["target"]["namespace_identity"]))
            self.source_identities = frozenset(identities)
        result = set(self.source_identities)
        if include_operation:
            result.add(c2._directory_identity(self.request()["target"]["operation_identity"]))
        return result

    def validate_request(self, value, *, slot=None):
        _header(value, "request", {"scope", "layout_profile", "layout_source_sha256", "creation_run_id",
            "profile", "target", "budget_policy", "slot_limits"}, run=False)
        _same(value["scope"], SCOPE)
        _same(value["layout_profile"], c2.LAYOUT_PROFILE)
        _same(value["layout_source_sha256"], c2.LAYOUT_SOURCE_SHA256)
        _uuid(value["creation_run_id"])
        policy.normalize_q1(value["budget_policy"])
        _same(value["slot_limits"], policy.slot_limits(1), "Q1_MISMATCH")
        c2._context({"profile": value["profile"], "node": None}, {}, value["slot_limits"]["capsule_limits"])
        target = value["target"]
        _exact(target, {"namespace", "namespace_identity", "operation_relative_key", "operation_identity"})
        _same(target["namespace"], DIRECTORY)
        _same(target["operation_relative_key"], value["operation_id"])
        identities = [c2._directory_identity(target[key]) for key in ("namespace_identity", "operation_identity")]
        roots = {c2._directory_identity(row["identity"]) for row in value["profile"]["roots"].values()}
        if len(set(identities)) != 2 or set(identities) & roots:
            _fail("IDENTITY_ALIAS")

    def validate_run_attempt(self, value, *, slot):
        _header(value, "run_attempt", {"request_sha256", "frozen_selection_sha256", "reservation", "admission", "anchor", "predecessor"})
        request = self.bind(value, run=False)
        _same(value["slot"], slot)
        policy.normalize_reservation(value["reservation"])
        _same(value["reservation"], policy.slot_reservation(slot))
        anchor = value["anchor"]
        _exact(anchor, {"relative_key", "identity"})
        _same(anchor["relative_key"], policy.request_anchor_name(self.sha("request.json")))
        identity = c2._directory_identity(anchor["identity"])
        forbidden = {c2._directory_identity(row["identity"]) for row in request["profile"]["roots"].values()}
        forbidden.update(c2._directory_identity(request["target"][key]) for key in ("namespace_identity", "operation_identity"))
        if identity in forbidden:
            _fail("IDENTITY_ALIAS")
        admission = value["admission"]
        if slot == 1:
            _same(value["run_id"], request["creation_run_id"])
            _same(value["frozen_selection_sha256"], None)
            _same(value["predecessor"], None)
            _same(admission, {"kind": "operation_directory", "relative_key": "", "identity": request["target"]["operation_identity"]})
        else:
            first = self.attempt(1)
            if value["run_id"] == first["run_id"]:
                _fail("BINDING_MISMATCH")
            self.selection()
            _same(value["frozen_selection_sha256"], self.sha("selection.json"))
            _same(anchor, first["anchor"])
            path = _run(1) + "/run-attempt.json"
            _same(value["predecessor"], {"slot": 1, "run_id": first["run_id"],
                "run_attempt_sha256": self.sha(path), "run_attempt_byte_size": len(self.bytes(path))})
            _exact(admission, {"kind", "relative_key", "parent_identity", "path_identity", "handle_identity", "sha256", "byte_size"})
            _same(admission["kind"], "resume_claim")
            _same(admission["relative_key"], policy.CLAIM_NAME)
            _same(admission["parent_identity"], request["target"]["operation_identity"])
            _same(admission["sha256"], _EMPTY_SHA)
            _same(admission["byte_size"], 0)
            claim_id = c2._file_pair(admission["path_identity"], admission["handle_identity"], 0, self.limits()["capsule_limits"])
            if claim_id in self.forbidden() or claim_id == identity:
                _fail("IDENTITY_ALIAS")

    def validate_source_metadata(self, value, *, slot=None):
        raw1._Graph.validate_source_metadata(_SourceView(self), value)

    def validate_source_projection(self, value, *, slot=None):
        projection.normalize(value, limits=self.limits()["capsule_limits"])
        _same(value, self.source()["source_projection"])

    def validate_selection(self, value, *, slot=None):
        _header(value, "selection", {"request_sha256", "initial_run_attempt_sha256", "source_metadata_sha256",
            "source_projection_sha256", "source_file_ids"}, run=False)
        self.bind(value, run=False)
        self.attempt(1)
        self.load("source-projection.json")
        _same(value["initial_run_attempt_sha256"], self.sha(_run(1) + "/run-attempt.json"))
        _same(value["source_metadata_sha256"], self.sha("source-metadata.json"))
        _same(value["source_projection_sha256"], self.sha("source-projection.json"))
        _same(value["source_file_ids"], [row["entry_id"] for row in self.source()["capsule"]["files"]])

    def validate_copy_attempt(self, value, *, slot):
        _header(value, "copy_attempt", {"request_sha256", "selection_sha256", "run_attempt_sha256", "copy_id",
            "source_entry_id", "target_relative_key", "target_parent_identity", "sha256", "byte_size"})
        self.selected_binding(value)
        _same(value["run_attempt_sha256"], self.sha(_run(slot) + "/run-attempt.json"))
        _uuid(value["copy_id"])
        entry = self.source_entry(value["source_entry_id"])
        _same(value["target_relative_key"], _run(slot) + f"/payload/{value['copy_id']}/{entry['entry_id']}.raw")
        if c2._directory_identity(value["target_parent_identity"]) in self.forbidden():
            _fail("IDENTITY_ALIAS")
        _same(value["sha256"], entry["sha256"])
        _same(value["byte_size"], entry["byte_size"])
        if slot == 2 and _run(1) + f"/copy-records/{entry['entry_id']}.json" in self.raw:
            _fail("DUPLICATE_COPY")
        previous = self.copy_entries.get((slot, entry["entry_id"]))
        if previous is not None and previous != value["copy_id"]:
            _fail("DUPLICATE_COPY")
        self.copy_entries[(slot, entry["entry_id"])] = value["copy_id"]

    def validate_copy_record(self, value, *, slot):
        _header(value, "copy_record", {"request_sha256", "selection_sha256", "copy_attempt_sha256", "copy"})
        self.selected_binding(value)
        copy = value["copy"]
        _exact(copy, c2._COPY)
        entry = self.source_entry(copy["source_entry_id"])
        _key(copy["target_relative_key"])
        match = _PAYLOAD.fullmatch(copy["target_relative_key"])
        if match is None or int(match[1]) != slot or match[3] != entry["entry_id"]:
            _fail("COPY_INVALID")
        path = _run(slot) + "/copy-attempts/" + match[2] + ".json"
        attempt = self.load(path)
        _same(value["copy_attempt_sha256"], self.sha(path))
        for key in ("source_entry_id", "target_relative_key", "target_parent_identity", "sha256", "byte_size"):
            _same(copy[key], attempt[key])
        identity = c2._file_pair(copy["copy_path_identity"], copy["copy_handle_identity"], entry["byte_size"], self.limits()["capsule_limits"])
        if identity in self.forbidden() or identity == c2._directory_identity(copy["target_parent_identity"]):
            _fail("IDENTITY_ALIAS")

    def expected_selected(self, generation):
        generation = _slot(generation)
        result = []
        for entry_id in self.selection()["source_file_ids"]:
            paths = [_run(slot) + f"/copy-records/{entry_id}.json" for slot in range(1, generation + 1)]
            present = [path for path in paths if path in self.raw]
            if not present:
                _fail("INCOMPLETE")
            if len(present) != 1:
                _fail("DUPLICATE_COPY")
            path = present[0]
            record = self.load(path)
            result.append({"source_entry_id": entry_id, "origin_slot": record["slot"], "origin_run_id": record["run_id"],
                "copy_record_relative_key": path, "copy_record_sha256": self.sha(path), "copy_record_byte_size": len(self.bytes(path))})
        return result

    def records(self, generation):
        return [self.load(row["copy_record_relative_key"]) for row in self.expected_selected(generation)]

    def validate_capsule(self, value, *, slot):
        c2.normalize(value, limits=self.limits()["capsule_limits"])
        source = self.source()["capsule"]
        for key in source.keys() - {"copies", "coverage"}:
            _same(value[key], source[key])
        _same(value["copies"], [row["copy"] for row in self.records(slot)])
        _same(value["coverage"], dict(source["coverage"], declared_copies="complete" if source["files"] else "none"))
        projection.cross_validate_capsule(self.source()["source_projection"], value, limits=self.limits()["capsule_limits"])

    def counters(self, value, *, success):
        _exact(value, raw1._COUNTERS)
        for count in value.values():
            _u(count)
        limits = policy.slot_limits(1)
        if (value["read_bytes"] > limits["max_read_bytes"]
                or value["confirmed_write_bytes"] + value["uncertain_write_bytes"] > limits["max_write_bytes"]):
            _fail("LIMIT")
        if success and (value["uncertain_read_calls"] or value["uncertain_write_bytes"]
                or value["elapsed_ns"] > limits["deadline_ms"] * 1_000_000
                or value["confirmed_write_bytes"] + limits["max_failure_bytes"] > limits["max_write_bytes"]):
            _fail("LIMIT")

    def _file_class(self, key):
        if key == policy.CLAIM_NAME:
            return "marker"
        if _TEMP.fullmatch(key) or _FAILURE.fullmatch(key):
            match = _TEMP.fullmatch(key) or _FAILURE.fullmatch(key)
            _uuid(match[2])
            return "retained"
        if _PAYLOAD.fullmatch(key):
            match = _PAYLOAD.fullmatch(key)
            _uuid(match[2])
            _entry(match[3])
            return "payload"
        _fact_path(key)
        return "fact"

    def _directory_key(self, key):
        anchor = policy.request_anchor_name(self.sha("request.json"))
        if key in {"", "runs", anchor}:
            return
        parts = key.split("/")
        if len(parts) >= 2 and parts[0] == "runs" and parts[1] in {"01", "02"}:
            if len(parts) == 2 or len(parts) == 3 and parts[2] in _FIXED_DIRS:
                return
            if len(parts) == 4 and parts[2] == "payload":
                _uuid(parts[3])
                return
        _fail("INVENTORY_MISMATCH")

    @_checked
    def inventory(self, value, *, generation=None, before_publication=False):
        value = raw1._snapshot(value, self.maximum)
        _exact(value, {"directories", "files"})
        request, limits = self.request(), self.limits()
        dirs = _ordered(value["directories"], _DIR_FIELDS, "relative_key", limits["max_target_entries"])
        files = _ordered(value["files"], _FILE_FIELDS, "relative_key", limits["max_target_entries"])
        if len(dirs) + len(files) > limits["max_target_entries"]:
            _fail("LIMIT")
        by_dir = {row["relative_key"]: row for row in dirs}
        by_file = {row["relative_key"]: row for row in files}
        if "" not in by_dir or set(by_dir) & set(by_file):
            _fail("INVENTORY_MISMATCH")
        _same(by_dir[""]["identity"], request["target"]["operation_identity"])
        children = {key: [] for key in by_dir}
        for key in set(by_dir) | set(by_file):
            _key(key, root=True, depth=limits["max_target_depth"])
            if key:
                parent = posixpath.dirname(key)
                if parent not in children:
                    _fail("INVENTORY_MISMATCH")
                children[parent].append(posixpath.basename(key))
        forbidden = {c2._directory_identity(row["identity"]) for row in request["profile"]["roots"].values()}
        forbidden.add(c2._directory_identity(request["target"]["namespace_identity"]))
        if "source-metadata.json" in self.raw:
            forbidden.update(self.forbidden(include_operation=False))
        identities = set()
        for key, row in by_dir.items():
            self._directory_key(key)
            identity = c2._directory_identity(row["identity"])
            if identity in forbidden or identity in identities:
                _fail("IDENTITY_ALIAS")
            identities.add(identity)
            _same(row["children"], sorted(children[key], key=lambda item: item.encode("utf-8")), "INVENTORY_MISMATCH")
        controls = 0
        physical_limits = dict(limits["capsule_limits"], max_total_bytes=limits["max_write_bytes"])
        for key, row in by_file.items():
            category = self._file_class(key)
            if category != "payload":
                controls += 1
            _same(row["parent_identity"], by_dir[posixpath.dirname(key)]["identity"])
            _u(row["byte_size"])
            _sha(row["sha256"])
            identity = c2._file_pair(row["path_identity"], row["handle_identity"], row["byte_size"], physical_limits)
            if identity in identities or identity in forbidden:
                _fail("IDENTITY_ALIAS")
            identities.add(identity)
            if category == "fact":
                self.load(key)
                _same(row["sha256"], self.sha(key))
                _same(row["byte_size"], len(self.bytes(key)))
            elif category == "marker":
                _same(row["sha256"], _EMPTY_SHA)
                _same(row["byte_size"], 0)
            elif category == "payload":
                match = _PAYLOAD.fullmatch(key)
                attempt_path = f"runs/{match[1]}/copy-attempts/{match[2]}.json"
                attempt = self.load(attempt_path)
                _same(attempt["target_relative_key"], key)
                _same(attempt["source_entry_id"], match[3])
                _same(attempt["target_parent_identity"], row["parent_identity"])
                record_path = f"runs/{match[1]}/copy-records/{match[3]}.json"
                if record_path in self.raw:
                    copy = self.load(record_path)["copy"]
                    for local, original in (("relative_key", "target_relative_key"), ("parent_identity", "target_parent_identity"),
                            ("path_identity", "copy_path_identity"), ("handle_identity", "copy_handle_identity"),
                            ("sha256", "sha256"), ("byte_size", "byte_size")):
                        _same(row[local], copy[original])
            else:
                match = _FAILURE.fullmatch(key)
                if match:
                    _same(match[2], self.attempt(int(match[1]))["run_id"])
                    if row["byte_size"] > limits["max_failure_bytes"]:
                        _fail("LIMIT")
                    if key in self.raw:
                        _same(row["sha256"], self.sha(key))
                        _same(row["byte_size"], len(self.bytes(key)))
        if controls > limits["max_control_files"]:
            _fail("LIMIT")
        domain_bytes = {1: 0, 2: 0}
        for key, row in by_file.items():
            domain_bytes[2 if key == policy.CLAIM_NAME or key.startswith("runs/02/") else 1] += row["byte_size"]
        if any(amount > limits["max_write_bytes"] for amount in domain_bytes.values()):
            _fail("LIMIT")
        anchor = policy.request_anchor_name(self.sha("request.json"))
        for slot in (1, 2):
            run = _run(slot)
            attempt_path = run + "/run-attempt.json"
            if slot == 2 and any(key == run or key.startswith(run + "/") for key in set(by_dir) | set(by_file)):
                if policy.CLAIM_NAME not in by_file:
                    _fail("ADMISSION_REQUIRED")
            if attempt_path in by_file:
                attempt = self.attempt(slot)
                if anchor not in by_dir:
                    _fail("ADMISSION_REQUIRED")
                _same(by_dir[anchor]["identity"], attempt["anchor"]["identity"])
                _same(by_dir[anchor]["children"], [])
                for name in _FIXED_DIRS:
                    if run + "/" + name not in by_dir:
                        _fail("INVENTORY_MISMATCH")
                if slot == 2:
                    claim = dict(attempt["admission"])
                    del claim["kind"]
                    _same(by_file[policy.CLAIM_NAME], claim)
            else:
                for key in by_file:
                    if key.startswith(run + "/") and not _TEMP.fullmatch(key):
                        _fail("FACT_REQUIRED")
                for key in by_dir:
                    if key.startswith(run + "/payload/"):
                        _fail("FACT_REQUIRED")
        for key in ("source-metadata.json", "source-projection.json", "selection.json"):
            if key in by_file and _run(1) + "/run-attempt.json" not in by_file:
                _fail("FACT_REQUIRED")
        if "request.json" in by_file:
            initial_dirs = {"runs", _run(1)} | {_run(1) + "/" + name for name in _FIXED_DIRS}
            if not initial_dirs <= set(by_dir):
                _fail("INVENTORY_MISMATCH")
        for key in by_file:
            if _COPY_ATTEMPT.fullmatch(key):
                attempt = self.load(key)
                parent_key = posixpath.dirname(attempt["target_relative_key"])
                if parent_key not in by_dir:
                    _fail("INVENTORY_MISMATCH")
                _same(by_dir[parent_key]["identity"], attempt["target_parent_identity"])
            elif _COPY_RECORD.fullmatch(key):
                if self.load(key)["copy"]["target_relative_key"] not in by_file:
                    _fail("HISTORY_CHANGED")
        if generation is not None:
            generation = _slot(generation)
            if generation == 1 and (policy.CLAIM_NAME in by_file or any(key.startswith("runs/02") for key in set(by_dir) | set(by_file))):
                _fail("INVENTORY_MISMATCH")
            if before_publication and _late(generation) & set(by_file):
                _fail("INVENTORY_MISMATCH")
        return by_dir, by_file

    def target_closed(self, value):
        _header(value, "target_closed", {"request_sha256", "selection_sha256", "run_attempt_sha256", "operation_identity",
            "reserved_slots", "directories", "files", "selected_copies", "counters"})
        self.selected_binding(value)
        slot = value["slot"]
        _same(value["run_attempt_sha256"], self.sha(_run(slot) + "/run-attempt.json"))
        _same(value["operation_identity"], self.request()["target"]["operation_identity"])
        _same(value["reserved_slots"], list(range(1, slot + 1)))
        policy.allocation(tuple(value["reserved_slots"]))
        selected = self.expected_selected(slot)
        _same(value["selected_copies"], selected)
        dirs, files = self.inventory({"directories": value["directories"], "files": value["files"]},
                                    generation=slot, before_publication=True)
        expected_facts = {path for path in self.raw if _fact_path(path)[0] != "failure"
                          and (_fact_path(path)[1] is None or _fact_path(path)[1] <= slot)} - _late(slot)
        actual_facts = {key for key in files if self._file_class(key) == "fact"}
        _same(actual_facts, expected_facts, "INVENTORY_MISMATCH")
        required = set(_ROOT) | {_run(slot) + "/run-attempt.json", _run(slot) + "/capsule.json"}
        if not required <= set(files):
            _fail("INCOMPLETE")
        copies = {record["copy"]["target_relative_key"] for record in self.records(slot)}
        if not copies <= set(files):
            _fail("INCOMPLETE")
        self.counters(value["counters"], success=True)
        new_bytes = sum(row["byte_size"] for key, row in files.items() if slot == 1 or key.startswith(_run(2) + "/"))
        old_bytes = sum(row["byte_size"] for key, row in files.items()
                        if slot == 2 and not key.startswith(_run(2) + "/") and key != policy.CLAIM_NAME)
        if (old_bytes > (slot - 1) * self.limits()["max_write_bytes"]
                or sum(row["byte_size"] for row in files.values()) > policy.allocation(tuple(value["reserved_slots"]))["reserved"]["write_bytes"]):
            _fail("LIMIT")
        if (value["counters"]["confirmed_write_bytes"] < new_bytes
                or new_bytes and value["counters"]["write_calls"] == 0):
            _fail("BINDING_MISMATCH")
        return dirs, files

    def validate_source_verified(self, value, *, slot):
        _header(value, "source_verified", {"request_sha256", "selection_sha256", "run_attempt_sha256", "capsule_sha256",
            "source_closed", "target_closed", "selected_copies"})
        self.selected_binding(value)
        _same(value["run_attempt_sha256"], self.sha(_run(slot) + "/run-attempt.json"))
        self.load(_run(slot) + "/capsule.json")
        _same(value["capsule_sha256"], self.sha(_run(slot) + "/capsule.json"))
        closed = value["source_closed"]
        _exact(closed, raw1._SOURCE_CLOSED)
        _same(closed["format_version"], 1)
        _same(closed["kind"], "personal_authority_raw_census_closed")
        _same(closed["operation_id"], self.request()["operation_id"])
        _same(closed["source_projection_sha256"], self.sha("source-projection.json"))
        _same(closed["source_metadata_sha256"], self.sha("source-metadata.json"))
        for key in ("read_bytes", "read_calls", "seek_calls", "source_file_count", "source_byte_size"):
            _u(closed[key])
        source_files = self.source()["capsule"]["files"]
        _same(closed["source_file_count"], len(source_files))
        _same(closed["source_byte_size"], sum(row["byte_size"] for row in source_files))
        if (closed["read_bytes"] < closed["source_byte_size"]
                or source_files and (closed["read_calls"] == 0 or closed["seek_calls"] == 0)):
            _fail("BINDING_MISMATCH")
        if closed["read_bytes"] > self.limits()["source_max_read_bytes"]:
            _fail("LIMIT")
        target = value["target_closed"]
        _same(target["slot"], slot)
        _same(target["run_id"], value["run_id"])
        _, files = self.target_closed(target)
        for key in ("read_bytes", "read_calls", "seek_calls"):
            if target["counters"][key] < closed[key]:
                _fail("BINDING_MISMATCH")
        if target["counters"]["read_bytes"] < closed["read_bytes"] + sum(row["byte_size"] for row in files.values()):
            _fail("BINDING_MISMATCH")
        _same(value["selected_copies"], self.expected_selected(slot))

    def validate_manifest(self, value, *, slot):
        _header(value, "manifest", {"scope", "request_sha256", "selection_sha256", "source_verified_sha256",
            "outcome", "facts", "payloads", "markers", "retained"})
        self.selected_binding(value)
        _same(value["scope"], SCOPE)
        verified_path = _run(slot) + "/source-verified.json"
        verified = self.load(verified_path)
        _same(value["source_verified_sha256"], self.sha(verified_path))
        _same(value["outcome"], "copied" if self.selection()["source_file_ids"] else "empty_source")
        files = {row["relative_key"]: row for row in verified["target_closed"]["files"]}
        copies = [record["copy"] for record in self.records(slot)]
        payload_keys = {copy["target_relative_key"] for copy in copies}
        facts = {key for key in files if self._file_class(key) == "fact"} | {verified_path}
        markers = {key for key in files if self._file_class(key) == "marker"}
        retained = set(files) - facts - markers - payload_keys
        def rows(keys):
            return [{"relative_key": key, "sha256": self.sha(key) if key == verified_path else files[key]["sha256"],
                     "byte_size": len(self.bytes(key)) if key == verified_path else files[key]["byte_size"]}
                    for key in sorted(keys, key=lambda item: item.encode("utf-8"))]
        _same(value["facts"], rows(facts))
        _same(value["markers"], rows(markers))
        _same(value["retained"], rows(retained))
        _same(value["payloads"], [{"source_entry_id": copy["source_entry_id"], "relative_key": copy["target_relative_key"],
            "sha256": copy["sha256"], "byte_size": copy["byte_size"]} for copy in copies])

    def validate_receipt(self, value, *, slot):
        _header(value, "receipt", {"scope", "request_sha256", "source_verified_sha256", "manifest_sha256", "outcome", "publication"})
        self.bind(value)
        manifest_path = _run(slot) + "/manifest.json"
        manifest = self.load(manifest_path)
        _same(value["scope"], SCOPE)
        _same(value["source_verified_sha256"], self.sha(_run(slot) + "/source-verified.json"))
        _same(value["manifest_sha256"], self.sha(manifest_path))
        _same(value["outcome"], manifest["outcome"])
        _same(value["publication"], "published_observed")
        # Every complete receipt, including a later generation encountered by
        # verify(g1), must close its own unavoidable publication budget edges.
        # During prospective publication this receipt is not in the raw table
        # yet; its canonical value has exactly the bytes the fixed writer uses.
        before = self.load(_run(slot) + "/source-verified.json")["target_closed"]
        after = (len(self.bytes(_run(slot) + "/source-verified.json"))
                 + len(self.bytes(manifest_path)) + len(_canonical(value, self.cap("receipt"))))
        counters, limits = before["counters"], self.limits()
        if (counters["confirmed_write_bytes"] + after + limits["max_failure_bytes"] > limits["max_write_bytes"]
                or counters["read_bytes"] + sum(row["byte_size"] for row in before["files"]) + after > limits["max_read_bytes"]):
            _fail("LIMIT")

    def validate_failure(self, value, *, slot):
        _header(value, "failure", {"request_sha256", "selection_sha256", "phase", "code", "counters", "effects", "effects_complete"})
        if value["phase"] not in _PHASES or value["code"] not in {"AUTHORITY_RAW_PRESERVATION_" + suffix for suffix in _FAILURE_SUFFIXES}:
            _fail("FAILURE_INVALID")
        if type(value["effects_complete"]) is not bool:
            _fail("TYPE_INVALID")
        request = self.request()
        _same(value["operation_id"], request["operation_id"])
        _same(value["run_id"], self.attempt(slot)["run_id"])
        if value["request_sha256"] is not None:
            _same(value["request_sha256"], self.sha("request.json"))
        elif value["selection_sha256"] is not None:
            _fail("BINDING_MISMATCH")
        if value["selection_sha256"] is not None:
            self.selection()
            _same(value["selection_sha256"], self.sha("selection.json"))
        self.counters(value["counters"], success=False)
        for row in _ordered(value["effects"], {"relative_key", "kind", "identity", "sha256", "byte_size"}, "relative_key", 16):
            _key(row["relative_key"], root=True)
            if row["kind"] not in {"file", "directory"}:
                _fail("FAILURE_INVALID")
            if row["identity"] is not None:
                c2._directory_identity(row["identity"])
            if row["kind"] == "directory":
                _same(row["sha256"], None)
                _same(row["byte_size"], None)
            else:
                if row["sha256"] is not None:
                    _sha(row["sha256"])
                if row["byte_size"] is not None:
                    _u(row["byte_size"])

    def prefix(self, inventory=None):
        self.request()
        for path in sorted(self.raw, key=lambda item: item.encode("utf-8")):
            self.load(path)
        entries = {}
        for path in self.raw:
            match = _COPY_ATTEMPT.fullmatch(path)
            if match:
                attempt = self.load(path)
                key = (attempt["slot"], attempt["source_entry_id"])
                if key in entries:
                    _fail("DUPLICATE_COPY")
                entries[key] = path
                if attempt["slot"] == 2 and _run(1) + f"/copy-records/{attempt['source_entry_id']}.json" in self.raw:
                    _fail("DUPLICATE_COPY")
        if inventory is not None:
            _, files = self.inventory(inventory)
            if not set(self.raw) <= set(files):
                _fail("INVENTORY_MISMATCH")
            _same({path for path in self.raw if _fact_path(path)[0] != "failure"},
                  {key for key in files if self._file_class(key) == "fact"}, "INVENTORY_MISMATCH")

    def package(self, generation, inventory):
        generation = _slot(generation)
        if inventory is None:
            _fail("INVENTORY_REQUIRED")
        self.prefix(inventory)
        self.load(_run(generation) + "/receipt.json")
        verified = self.load(_run(generation) + "/source-verified.json")
        before = verified["target_closed"]
        current_dirs, current_files = self.inventory(inventory)
        old_dirs = {row["relative_key"]: row for row in before["directories"]}
        old_files = {row["relative_key"]: row for row in before["files"]}
        late = _late(generation)
        allowed_files = set(old_files) | late
        allowed_dirs = set(old_dirs)
        if generation == 1:
            allowed_files.update(key for key in current_files if key == policy.CLAIM_NAME or key.startswith("runs/02/"))
            allowed_dirs.update(key for key in current_dirs if key == "runs/02" or key.startswith("runs/02/"))
        _same(set(current_files), allowed_files, "INVENTORY_MISMATCH")
        _same(set(current_dirs), allowed_dirs, "INVENTORY_MISMATCH")
        for key, row in old_files.items():
            _same(current_files[key], row, "HISTORY_CHANGED")
        for key, row in old_dirs.items():
            _same(current_dirs[key]["identity"], row["identity"], "HISTORY_CHANGED")
            children = list(row["children"])
            if key == _run(generation):
                children.extend(posixpath.basename(path) for path in late)
            if generation == 1 and key == "" and policy.CLAIM_NAME in current_files:
                children.append(policy.CLAIM_NAME)
            if generation == 1 and key == "runs" and "runs/02" in current_dirs:
                children.append("02")
            _same(current_dirs[key]["children"], sorted(children, key=lambda item: item.encode("utf-8")), "HISTORY_CHANGED")
        occupied = (1, 2) if policy.CLAIM_NAME in current_files else (1,)
        if sum(row["byte_size"] for row in current_files.values()) > policy.allocation(occupied)["reserved"]["write_bytes"]:
            _fail("LIMIT")


def _path_for(kind, value):
    if type(kind) is not str or kind not in _KINDS:
        _fail("KIND_INVALID")
    if kind in {"request", "selection"}:
        return kind + ".json"
    if type(value) is not dict:
        _fail("TYPE_INVALID")
    run = _run(value["slot"])
    if kind == "copy_attempt":
        return run + "/copy-attempts/" + _uuid(value["copy_id"]) + ".json"
    if kind == "copy_record":
        return run + "/copy-records/" + _entry(value["copy"]["source_entry_id"]) + ".json"
    if kind == "failure":
        return run + "/failures/" + _uuid(value["run_id"]) + ".json"
    return run + "/" + kind.replace("_", "-") + ".json"


def _normalize(kind, value, fact_bytes, max_fact_bytes):
    graph = _Graph(fact_bytes, max_fact_bytes)
    copied = raw1._snapshot(value, graph.cap(kind))
    return graph.normalize_at(_path_for(kind, copied), kind, copied)


@_checked
def normalize_request(value, *, fact_bytes=None, max_fact_bytes=None):
    return _normalize("request", value, fact_bytes, max_fact_bytes)


@_checked
def normalize_run_attempt(value, *, fact_bytes=None, max_fact_bytes=None):
    return _normalize("run_attempt", value, fact_bytes, max_fact_bytes)


@_checked
def normalize_selection(value, *, fact_bytes=None, max_fact_bytes=None):
    return _normalize("selection", value, fact_bytes, max_fact_bytes)


@_checked
def normalize_copy_attempt(value, *, fact_bytes=None, max_fact_bytes=None):
    return _normalize("copy_attempt", value, fact_bytes, max_fact_bytes)


@_checked
def normalize_copy_record(value, *, fact_bytes=None, max_fact_bytes=None):
    return _normalize("copy_record", value, fact_bytes, max_fact_bytes)


@_checked
def normalize_source_verified(value, *, fact_bytes=None, max_fact_bytes=None):
    return _normalize("source_verified", value, fact_bytes, max_fact_bytes)


@_checked
def normalize_manifest(value, *, fact_bytes=None, max_fact_bytes=None):
    return _normalize("manifest", value, fact_bytes, max_fact_bytes)


@_checked
def normalize_receipt(value, *, fact_bytes=None, max_fact_bytes=None):
    return _normalize("receipt", value, fact_bytes, max_fact_bytes)


@_checked
def normalize_failure(value, *, fact_bytes=None, max_fact_bytes=None):
    return _normalize("failure", value, fact_bytes, max_fact_bytes)


@_checked
def encode(kind, value, *, fact_bytes=None, max_fact_bytes=None):
    if type(kind) is not str or kind not in _KINDS:
        _fail("KIND_INVALID")
    normalized = _normalize(kind, value, fact_bytes, max_fact_bytes)
    return _canonical(normalized, raw1._maximum(max_fact_bytes))


@_checked
def decode(kind, raw, *, fact_bytes=None, max_fact_bytes=None):
    if type(kind) is not str or kind not in _KINDS:
        _fail("KIND_INVALID")
    graph = _Graph(fact_bytes, max_fact_bytes)
    value = raw1._parse(raw, graph.cap(kind))
    return graph.normalize_at(_path_for(kind, value), kind, value)


@_checked
def cross_validate_prefix(*, fact_bytes, inventory=None, max_fact_bytes=None):
    _Graph(fact_bytes, max_fact_bytes).prefix(inventory)
    return None


@_checked
def cross_validate_package(*, fact_bytes, generation, inventory=None, max_fact_bytes=None):
    _Graph(fact_bytes, max_fact_bytes).package(generation, inventory)
    return None
