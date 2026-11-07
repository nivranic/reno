"""Original-shaped SQL facts under one borrowed image and original deadline.

Own one fixed observation scope and its Cursors/reference Connections. Python
work is clipped cooperatively; successful SQL projection is not source_closed,
producer completion, receiver admission, durable publication or authority.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from functools import wraps
import hashlib
import math
import time

from . import personal_authority_namespace3_deadline as _deadline
from . import personal_authority_sql_observation_deadline as _sql
from . import personal_mirror_contract as _facts
from . import personal_mirror_transfer_contract as _wire
from . import personal_mirror_transfer_schema as _receiver
from . import personal_preservation_contract as _contract
from . import personal_preservation_source as _source

_monotonic = time.monotonic


class SqlProjectionDeadlineError(ValueError):
    def __init__(self, suffix):
        self.code = "SQL_PROJECTION_DEADLINE_" + suffix
        super().__init__(self.code)


Error = SqlProjectionDeadlineError


def _fail(suffix):
    raise SqlProjectionDeadlineError(suffix) from None


def _observation(profile, image, *, deadline_token):
    return _sql.SqlObservationDeadlineLease(profile, image, deadline_token=deadline_token)


def _observation_enter(value):
    return value.__enter__()


def _observation_exit(value, kind, primary, traceback):
    return value.__exit__(kind, primary, traceback)


def _observation_token(value):
    return value.deadline_token


def _observation_evidence(value):
    return value.evidence


def _query(value, recipe_id, *, table=None, index_name=None):
    return value.query(recipe_id, table=table, index_name=index_name)


def _personal_reference(value):
    return value.personal_reference()


def _receiver_reference(value):
    return value.receiver_reference()


def _reference_enter(value):
    return value.__enter__()


def _reference_exit(value, kind, primary, traceback):
    return value.__exit__(kind, primary, traceback)


def _reference_query(value, recipe_id, *, table=None, index_name=None):
    return value.query(recipe_id, table=table, index_name=index_name)


def _rows_enter(value):
    return value.__enter__()


def _rows_exit(value, kind, primary, traceback):
    return value.__exit__(kind, primary, traceback)


def _fetchone(value):
    return value.fetchone()


def _fetchmany(value, size):
    return value.fetchmany(size)


def _schema_tokens(sql):
    return _facts._tokens(sql)


def _schema_json(value):
    return _facts._json(value)


def _schema_decode(value, expected=dict):
    return _facts._decode(value, expected)


def _freeze_reference(value):
    return _receiver._freeze_reference(value)


def _snapshot_uuid(value):
    return _facts._uuid(value)


def _contract_text(value, *, maximum=4096, empty=False):
    return _contract._text(value, maximum=maximum, empty=empty)


def _typed_cell(value):
    return _contract.typed_cell(value)


def _canonical_bytes(value):
    return _contract.canonical_bytes(value)


def _decode(raw):
    return _contract.decode(raw)


def _wire_digest(value):
    return _wire._digest(value)


def _sha256(raw):
    return hashlib.sha256(raw)


def _hash_update(value, raw):
    return value.update(raw)


def _hash_hexdigest(value):
    return value.hexdigest()


def _source_fail(code, *, retriable=False):
    return _source._fail(code, retriable=retriable)


def _snapshot_fail(code):
    return _facts._fail(code)


def _receiver_fail(code):
    return _receiver._fail(code)


def _snapshot_document(value, *, state):
    return _cpu(state, _document_property, value)


def _document_property(value):
    return value.document


def _snapshot_counts(value, *, state):
    return _cpu(state, _counts_property, value)


def _counts_property(value):
    return value.counts


class _CpuState:
    __slots__ = ("token", "first", "traceback")

    def __init__(self, token):
        self.token, self.first, self.traceback = token, None, None


def _state(token):
    # A domain failure precedes both the clock and SQL factory.
    try:
        original = _deadline._token(token)
    except _deadline.Namespace3Error:
        _fail("INVALID_ARGUMENT")
    return _CpuState(original)


def _capture(state, failure, traceback=None):
    if state.first is None:
        state.first = failure
        state.traceback = (BaseException.__dict__["__traceback__"].__get__(failure)
                           if traceback is None else traceback)


def _raise_first(state):
    if state.first is not None:
        raise BaseException.with_traceback(state.first, state.traceback) from None


def _check(state):
    _raise_first(state)
    try:
        now = _monotonic()
        if type(now) is not float or not math.isfinite(now) or now < 0.0:
            _fail("INVALID_CLOCK")
        if now >= state.token.absolute_monotonic:
            _fail("DEADLINE")
    except BaseException as failure:
        _capture(state, failure)
        _raise_first(state)


def _tick(state):
    _check(state)
    return True


def _cpu(state, operation, /, *args, **kwargs):
    # A failed opaque call has no later clock that could replace its error.
    _check(state)
    value = operation(*args, **kwargs)
    _check(state)
    return value


def _cleanup_note(first):
    try:
        BaseException.add_note(first, "SQL_PROJECTION_DEADLINE_CLEANUP_FAILED")
    except BaseException:
        pass


class _Scope:
    """Fixed lower public exits retain ownership and the original primary."""
    __slots__ = ("state", "value", "enter", "exit")

    def __init__(self, state, value, enter, exit):
        self.state, self.value, self.enter, self.exit = state, value, enter, exit

    def __enter__(self):
        value = self.enter(self.value)
        # Lower successful entry is now known; a local postcondition failure
        # exits that exact lower owner rather than leaving it ACTIVE.
        try:
            if value is not self.value:
                _fail("INVALID_RESULT")
            _check(self.state)
            return value
        except BaseException as primary:
            traceback = BaseException.__dict__["__traceback__"].__get__(primary)
            self.__exit__(type(primary), primary, traceback)
            raise

    def __exit__(self, kind, primary, traceback):
        if primary is not None:
            _capture(self.state, primary, traceback)
        try:
            result = self.exit(self.value, kind, primary, traceback)
            if result is not None:
                _fail("INVALID_RESULT")
        except BaseException as failure:
            if self.state.first is not None and failure is not self.state.first:
                _cleanup_note(self.state.first)
            _capture(self.state, failure)
            _raise_first(self.state)
        # Returning None lets ordinary context semantics rethrow body primary.
        # No post-exit clock changes a successful lower owner's history.
        return None


def _wire_checked(function):
    @wraps(function)
    def checked(*args, **kwargs):
        state = kwargs["state"]
        try:
            return function(*args, **kwargs)
        except _wire.PersonalMirrorTransferError:
            _raise_first(state)
            raise
        except _facts.PersonalSnapshotError as error:
            _raise_first(state)
            raise _wire.PersonalMirrorTransferError(error.code) from None
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            _raise_first(state)
            _wire._fail()
    return checked


def _preservation_checked(function):
    @wraps(function)
    def checked(*args, **kwargs):
        state = kwargs["state"]
        try:
            return function(*args, **kwargs)
        except _contract.PersonalPreservationError:
            _raise_first(state)
            raise
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError,
                RecursionError, OverflowError):
            _raise_first(state)
            _contract._fail()
    return checked


def _collect(owner, recipe, *, state, reference=False, table=None, index_name=None):
    _check(state)
    factory = _reference_query if reference else _query
    rows = factory(owner, recipe, table=table, index_name=index_name)
    values = []
    with _Scope(state, rows, _rows_enter, _rows_exit) as active:
        while True:
            _check(state)
            value = _fetchone(active)
            _check(state)
            if value is None:
                break
            values.append(value)
    _check(state)
    return values


def _describe(owner, table, *, state, reference=False):
    raw = _collect(owner, "schema_object", state=state, reference=reference, table=table)
    if len(raw) != 1 or raw[0][0] != "table" or raw[0][1] != table or raw[0][2] <= 0:
        _snapshot_fail("SNAPSHOT_SCHEMA_DRIFT")
    columns = []
    for row in _collect(owner, "table_xinfo", state=state, reference=reference, table=table):
        _check(state)
        columns.append({"name": row[1], "type": row[2], "not_null": row[3],
                        "default": row[4], "pk": row[5], "hidden": row[6]})
    if not columns or len(columns) > 64:
        _snapshot_fail("SNAPSHOT_SCHEMA_DRIFT")
    primary = []
    for column in _cpu(state, sorted, columns, key=lambda value: value["pk"]):
        _check(state)
        if column["pk"]:
            primary.append(column["name"])
    unique = []
    for row in _collect(owner, "index_list", state=state, reference=reference, table=table):
        _check(state)
        if row[2]:
            if row[4]:
                _snapshot_fail("SNAPSHOT_SCHEMA_DRIFT")
            names = []
            for part in _collect(owner, "index_info", state=state, reference=reference,
                                 table=table, index_name=row[1]):
                _check(state)
                names.append(part[2])
            unique.append(names)
    foreign = []
    for row in _collect(owner, "foreign_key_list", state=state, reference=reference, table=table):
        _check(state)
        foreign.append([row[2], row[3], row[4], row[5], row[6], row[7]])
    value = {"columns": columns, "primary_key": primary, "order_by": primary,
             "unique_keys": _cpu(state, sorted, unique),
             "foreign_keys": _cpu(state, sorted, foreign),
             "sql_tokens": _cpu(state, _schema_tokens, raw[0][3])}
    _check(state)
    return value


def _storage_contract(owner, table, *, state, reference=False):
    columns, refs, indexes = [], [], []
    for row in _collect(owner, "table_info", state=state, reference=reference, table=table):
        _check(state)
        columns.append(tuple(row)[1:])
    for row in _collect(owner, "foreign_key_list", state=state, reference=reference, table=table):
        _check(state)
        refs.append(tuple(row))
    for row in _collect(owner, "index_list", state=state, reference=reference, table=table):
        _check(state)
        sql = _collect(owner, "index_sql", state=state, reference=reference,
                       table=table, index_name=row[1])
        first = sql[0] if sql else None
        parts = []
        for part in _collect(owner, "index_xinfo", state=state, reference=reference,
                             table=table, index_name=row[1]):
            _check(state)
            parts.append(tuple(part)[2:])
        normalized = _cpu(state, _sql_normalized, first[0] if first and first[0] else "")
        indexes.append((tuple(row)[2:], tuple(parts), normalized))
    sql = _collect(owner, "table_sql", state=state, reference=reference, table=table)
    first = sql[0] if sql else None
    value = (columns, refs, _cpu(state, sorted, indexes),
             _cpu(state, _sql_normalized, first[0] if first else ""))
    _check(state)
    return value


def _sql_normalized(value):
    return " ".join(value.split()).replace('"', '')


def _objects(rows, *, state):
    values = []
    for row in rows:
        _check(state)
        table = _cpu(state, str, row[2])
        matches = _cpu(state, table.lower) in _receiver.TABLES
        if not matches:
            for prefix in _receiver._PREFIXES:
                _check(state)
                original = _cpu(state, str, row[1])
                lowered = _cpu(state, original.lower)
                if _cpu(state, lowered.startswith, prefix):
                    matches = True
                    break
        if matches:
            values.append(tuple(row))
    _check(state)
    return values


def _same(actual, expected, code="SOURCE_CHANGED", *, state):
    _check(state)
    if actual != expected:
        _source_fail(code, retriable=code == "SOURCE_CHANGED")
    _check(state)


def _schema_digest(owner, table, *, state):
    descriptor = _describe(owner, table, state=state)
    raw = _cpu(state, _canonical_bytes, descriptor)
    hasher = _cpu(state, _sha256, raw)
    return _cpu(state, _hash_hexdigest, hasher)


def _personal_schema(owner, *, state):
    _check(state)
    ref = _personal_reference(owner)
    with _Scope(state, ref, _reference_enter, _reference_exit) as active:
        tables = {}
        for table in _facts.TABLE_NAMES:
            _check(state)
            tables[table] = _describe(active, table, state=state, reference=True)
        value = {"personal_schema_version": _facts.FORMAT_VERSION, "tables": tables}
        encoded = _cpu(state, _schema_json, value)
        schema_bytes = _cpu(state, encoded.encode, "utf-8")
        expected = _cpu(state, _schema_decode, schema_bytes)
        _check(state)
    _check(state)
    return expected, schema_bytes


def _validate_receiver(owner, objects, *, state):
    temporary = _objects(_collect(owner, "temp_objects", state=state), state=state)
    if temporary:
        _receiver_fail("TEMP_COLLISION")
    ref = _receiver_reference(owner)
    with _Scope(state, ref, _reference_enter, _reference_exit) as active:
        expected_objects = _cpu(state, _freeze_reference,
            _objects(_collect(active, "main_objects", state=state, reference=True), state=state))
        contracts = []
        for table in _receiver.TABLES:
            _check(state)
            contracts.append((table, _cpu(state, _freeze_reference,
                _storage_contract(active, table, state=state, reference=True))))
        if objects != list(expected_objects):
            _receiver_fail("INCOMPATIBLE")
        for table, expected in contracts:
            _check(state)
            columns, refs, indexes, sql = expected
            if _storage_contract(owner, table, state=state) != (
                    list(columns), list(refs), list(indexes), sql):
                _receiver_fail("INCOMPATIBLE")
        _check(state)
    _check(state)


def _table(owner, name, expected_columns, all_rows, *, state):
    _check(state)
    columns = []
    for column in _describe(owner, name, state=state)["columns"]:
        _check(state)
        columns.append(column["name"])
    _same(columns, list(expected_columns), "SQL_COLUMNS_INVALID", state=state)
    rows = _collect(owner, "raw_table", state=state, table=name)
    detached = []
    for row in rows:
        _check(state)
        values = {}
        for column, value in zip(columns, row):
            _check(state)
            values[column] = value
        detached.append(values)
    all_rows[name] = detached
    summary = _typed_table_digest(columns, rows, state=state)
    summary["schema_sha256"] = _schema_digest(owner, name, state=state)
    _check(state)
    return summary


def _integrity(owner, *, state):
    rows = _query(owner, "integrity")
    with _Scope(state, rows, _rows_enter, _rows_exit) as active:
        value = _fetchmany(active, 2)
        _check(state)
        if value != (("ok",),):
            _source_fail("SQL_INTEGRITY_INVALID")
        if _fetchone(active) is not None:
            _source_fail("SQL_INTEGRITY_INVALID")
        _check(state)
    rows = _query(owner, "foreign_keys_check")
    with _Scope(state, rows, _rows_enter, _rows_exit) as active:
        if _fetchone(active) is not None:
            _source_fail("SQL_FOREIGN_KEYS_INVALID")
        _check(state)


def _projection(owner, *, state):
    _integrity(owner, state=state)
    expected, schema_bytes = _personal_schema(owner, state=state)
    tables = {}
    for name in _facts.TABLE_NAMES:
        _check(state)
        tables[name] = _describe(owner, name, state=state)
    actual = {"personal_schema_version": _facts.FORMAT_VERSION, "tables": tables}
    if actual != expected:
        _snapshot_fail("SNAPSHOT_SCHEMA_DRIFT")
    for row in _collect(owner, "personal_triggers", state=state):
        _check(state)
        if row[0] in _facts.TABLE_NAMES:
            _source_fail("PERSONAL_SCHEMA_INVALID")
    migration = _collect(owner, "migration_version", state=state)
    if not migration or migration[0] != ("17",):
        _source_fail("SQL_SCHEMA_VERSION_UNSUPPORTED")
    meta_rows = _collect(owner, "personal_meta", state=state)
    meta_keys = []
    for row in meta_rows:
        _check(state)
        meta_keys.append(row[0])
    core_keys = []
    for key in _facts.META_KEYS:
        _check(state)
        if key != "default_project_id":
            core_keys.append(key)
    missing_default = meta_keys == core_keys
    if meta_keys != list(_facts.META_KEYS) and not missing_default:
        _source_fail("PERSONAL_META_INVALID")
    meta = _cpu(state, dict, meta_rows)
    _cpu(state, _snapshot_uuid, meta["library_id"])
    if type(meta["personal_primary_id"]) is not str or missing_default and meta["personal_primary_id"] != "":
        _source_fail("PERSONAL_META_INVALID")
    if meta["personal_primary_id"]:
        _cpu(state, _snapshot_uuid, meta["personal_primary_id"])
    try:
        epoch = _cpu(state, int, meta["primary_epoch"])
    except (ValueError, TypeError):
        _raise_first(state)
        _source_fail("PERSONAL_META_INVALID")
    if epoch < 1 or meta["primary_epoch"] != str(epoch):
        _source_fail("PERSONAL_META_INVALID")
    all_rows, summaries = {}, {}
    for name, columns in _contract.PERSONAL_COLUMNS.items():
        _check(state)
        summaries[name] = _table(owner, name, columns, all_rows, state=state)
    if missing_default:
        for summary in summaries.values():
            _check(state)
            if summary["count"]:
                _source_fail("PERSONAL_META_INVALID")
    receiver_objects = _objects(_collect(owner, "main_objects", state=state), state=state)
    receiver_present = bool(receiver_objects)
    receiver_summaries = {}
    if receiver_present:
        _validate_receiver(owner, receiver_objects, state=state)
        for name, columns in _contract.RECEIVER_COLUMNS.items():
            _check(state)
            receiver_summaries[name] = _table(owner, name, columns, all_rows, state=state)
        _receiver_semantics(all_rows, schema_bytes=schema_bytes, state=state)
    meta_summary = _typed_table_digest(["key", "value"], meta_rows, state=state)
    meta_summary["schema_sha256"] = _schema_digest(owner, "meta", state=state)
    fingerprint = _cpu(state, _observation_evidence, owner)["source_fingerprint"]
    result = {"schema_version": 17, "source_fingerprint": fingerprint,
              "meta": meta_summary, "personal_tables": summaries,
              "receiver_facility": "installed" if receiver_present else "absent",
              "receiver_tables": receiver_summaries, "integrity": "ok", "foreign_keys": "ok"}
    _check(state)
    return result, meta, all_rows


def _verify_retired(evidence, *, state):
    _check(state)
    if (type(evidence) is not dict or evidence.get("retired") is not True
            or evidence.get("phase") != "retired" or evidence.get("normal_exit") is not True
            or evidence.get("failed") is not False or evidence.get("source_borrowed") is not True):
        _fail("INVALID_RESULT")
    for name in ("source", "personal_reference", "receiver_reference"):
        _check(state)
        role = evidence.get(name)
        if type(role) is not dict or role.get("fee_complete") is not True:
            _fail("INVALID_RESULT")
        cursors = role.get("cursor_states")
        if type(cursors) is not tuple:
            _fail("INVALID_RESULT")
        for value in cursors:
            _check(state)
            if value != "closed":
                _fail("INVALID_RESULT")
        if name == "source" or role.get("connection_state") != "empty":
            expected = "borrowed" if name == "source" else "closed"
            if (role.get("connection_state") != expected or role.get("entered") is not True
                    or role.get("normal_exit") is not True
                    or role.get("progress_restore_state") != "returned"
                    or role.get("authorizer_restore_state") != "returned"):
                _fail("INVALID_RESULT")
        elif role.get("entered") is not False or role.get("normal_exit") is not False:
            _fail("INVALID_RESULT")
    _check(state)


def project_sql_locked(profile, image, *, deadline_token):
    state = _state(deadline_token)
    _check(state)
    owner = _observation(profile, image, deadline_token=deadline_token)
    if type(owner) is not _sql.SqlObservationDeadlineLease:
        _fail("INVALID_RESULT")
    if _observation_token(owner) is not deadline_token:
        _fail("INVALID_ARGUMENT")
    with _Scope(state, owner, _observation_enter, _observation_exit) as active:
        product = _projection(active, state=state)
        _check(state)
    # This final CPU failure cannot alter the already retired lower history.
    evidence = _observation_evidence(owner)
    _verify_retired(evidence, state=state)
    _check(state)
    return product


# SOURCE-CLONES-01: appended by the source-text-only fixed builder.


def _checked_value(state, value):
    _check(state)
    return value


def _snapshot_validate(snapshot_json, *, schema_bytes, state, expected_sha256=None, expected_counts=None, prior_snapshot_json=None):
    """Validate a received fact document while retaining its exact UTF-8 bytes.

    An optional prior document proves append-only histories/receipts and rejects
    a historical key reused with different content. It does not make either
    document an accepted mirror, a current primary, or a durable receipt.
    """
    _check(state)
    try:
        raw = _cpu(state, snapshot_json.encode, 'utf-8') if _cpu(state, type, snapshot_json) is str else snapshot_json
        if _cpu(state, type, raw) is not bytes or not 0 < _cpu(state, len, raw) <= _facts.MAX_SNAPSHOT_BYTES:
            _snapshot_fail('SNAPSHOT_SIZE_INVALID')
        _cpu(state, raw.decode, 'utf-8')
    except UnicodeError:
        _raise_first(state)
        _snapshot_fail('SNAPSHOT_INVALID_JSON')
    digest = _cpu(state, _hash_hexdigest, _cpu(state, _sha256, raw))
    if expected_sha256 is not None:
        _cpu(state, _facts._sha, expected_sha256)
        if digest != expected_sha256:
            _snapshot_fail('SNAPSHOT_SHA256_MISMATCH')
    document = _cpu(state, _schema_decode, raw)
    required = {'format_version', 'snapshot_id', 'source', 'meta', 'schema', 'tables', 'attachment_manifest', 'counts'}
    if _cpu(state, set, document) != required or _cpu(state, type, document['format_version']) is not int or document['format_version'] != _facts.FORMAT_VERSION:
        _snapshot_fail('SNAPSHOT_FORMAT_MISMATCH')
    _cpu(state, _snapshot_uuid, document['snapshot_id'])
    if _cpu(state, type, document['schema']) is not dict or _cpu(state, _schema_json, document['schema']) != _cpu(state, schema_bytes.decode, 'utf-8'):
        _snapshot_fail('SNAPSHOT_SCHEMA_DRIFT')
    meta_rows = document['meta']
    if _cpu(state, type, meta_rows) is not list or _cpu(state, len, meta_rows) != _cpu(state, len, _facts.META_KEYS):
        _snapshot_fail('SNAPSHOT_META_MISMATCH')
    meta = {}
    for key, row in _cpu(state, zip, _facts.META_KEYS, meta_rows):
        _check(state)
        if _cpu(state, type, row) is not dict or _cpu(state, set, row) != {'key', 'value'} or row['key'] != key:
            _snapshot_fail('SNAPSHOT_META_MISMATCH')
        _cpu(state, _facts._text, row['value'], nonempty=True)
        meta[key] = row['value']
    for key in ('default_project_id', 'library_id', 'personal_primary_id'):
        _check(state)
        _cpu(state, _snapshot_uuid, meta[key])
    if not _cpu(state, _facts._DECIMAL.fullmatch, meta['primary_epoch']) or _cpu(state, int, meta['primary_epoch']) < 1:
        _snapshot_fail('SNAPSHOT_META_MISMATCH')
    _cpu(state, _facts._integer, _cpu(state, int, meta['primary_epoch']), 1)
    source = {'library_id': meta['library_id'], 'personal_primary_id': meta['personal_primary_id'], 'primary_epoch': _cpu(state, int, meta['primary_epoch'])}
    if _cpu(state, type, document['source']) is not dict or document['source'] != source or _cpu(state, type, _cpu(state, document['source'].get, 'primary_epoch')) is not int:
        _snapshot_fail('SNAPSHOT_SOURCE_MISMATCH')
    indexes = _cpu(state, _facts._rows, document)
    _cpu(state, _facts._entity, indexes, 'project', meta['default_project_id'])
    _cpu(state, _facts._content, document, indexes)
    _cpu(state, _facts._manifest, document, indexes)
    counts = {table: _cpu(state, len, document['tables'][table]) for table in _facts.TABLE_NAMES if _tick(state)}
    _cpu(state, counts.update, meta=_cpu(state, len, _facts.META_KEYS), attachment_manifest=_cpu(state, len, document['attachment_manifest']))
    declared = document['counts']
    if _cpu(state, type, declared) is not dict or _cpu(state, set, declared) != _cpu(state, set, counts) or _cpu(state, any, (_cpu(state, type, value) is not int or value < 0 for value in _cpu(state, declared.values) if _tick(state))) or (declared != counts):
        _snapshot_fail('SNAPSHOT_COUNT_MISMATCH')
    if expected_counts is not None and (_cpu(state, type, expected_counts) is not dict or expected_counts != counts or _cpu(state, any, (_cpu(state, type, value) is not int for value in _cpu(state, expected_counts.values) if _tick(state)))):
        _snapshot_fail('SNAPSHOT_COUNT_MISMATCH')
    if prior_snapshot_json is not None:
        prior = _cpu(state, _snapshot_validate, prior_snapshot_json, schema_bytes=schema_bytes, state=state)
        if prior.snapshot_id == document['snapshot_id'] and prior.snapshot_bytes != raw:
            _snapshot_fail('SNAPSHOT_ID_REUSED')
        _cpu(state, _facts._preserved, document, _snapshot_document(prior, state=state))
    return _checked_value(state, _cpu(state, _facts.SnapshotFacts, raw, digest, document['snapshot_id'], _cpu(state, _schema_json, counts)))
    _check(state)


def _normalize_prepare_facts(value, *, schema_bytes, state, prior_snapshot_json=None):
    _check(state)
    _cpu(state, _wire._exact, value, _wire.PREPARE_FIELDS)
    _cpu(state, _wire._version, value['format_version'])
    if value['kind'] != 'personal_mirror_prepare':
        _wire._fail('KIND_INVALID')
    _cpu(state, _facts._uuid, value['operation_id'])
    _cpu(state, _facts._uuid, value['snapshot_id'])
    _cpu(state, _facts._sha, value['snapshot_sha256'])
    if _cpu(state, type, value['snapshot_json']) is not str:
        _wire._fail('SNAPSHOT_TEXT_REQUIRED')
    source = _cpu(state, _wire._source, value['source'])
    receiver = _cpu(state, _wire._receiver, value['receiver'], source)
    previous = _cpu(state, _wire._previous, value['previous'], value['expected_revision'])
    facts = _cpu(state, _snapshot_validate, value['snapshot_json'], expected_sha256=value['snapshot_sha256'], prior_snapshot_json=prior_snapshot_json, schema_bytes=schema_bytes, state=state)
    document = _snapshot_document(facts, state=state)
    if facts.snapshot_id != value['snapshot_id'] or document['source'] != {name: source[name] for name in ('library_id', 'personal_primary_id', 'primary_epoch') if _tick(state)}:
        _wire._fail('SNAPSHOT_BINDING_MISMATCH')
    _cpu(state, _wire._manifest_limit, document['attachment_manifest'])
    if prior_snapshot_json is not None:
        prior = _cpu(state, _snapshot_validate, prior_snapshot_json, schema_bytes=schema_bytes, state=state)
        if previous != {'snapshot_id': prior.snapshot_id, 'snapshot_sha256': prior.snapshot_sha256}:
            _wire._fail('PREVIOUS_SNAPSHOT_MISMATCH')
    normalized = {**_cpu(state, deepcopy, value), 'source': source, 'receiver': receiver, 'previous': previous}
    return _checked_value(state, (normalized, facts))
    _check(state)


@_wire_checked
def _normalize_prepare(value, *, schema_bytes, state, prior_snapshot_json=None):
    """Validate exact binding and original snapshot bytes, without admission.

    A prior snapshot is an explicit fact input, not a receiver CAS proof. The
    runtime must select its actual accepted predecessor and enforce the guard.
    """
    _check(state)
    normalized, _ = _cpu(state, _normalize_prepare_facts, value, prior_snapshot_json=prior_snapshot_json, schema_bytes=schema_bytes, state=state)
    return _checked_value(state, normalized)
    _check(state)


def _prepare_sha256(value, *, schema_bytes, state):
    """Hash canonical envelope while preserving the original snapshot string."""
    _check(state)
    return _checked_value(state, _cpu(state, _wire_digest, _cpu(state, _normalize_prepare, value, schema_bytes=schema_bytes, state=state)))
    _check(state)


def _attachment_manifest(value, *, schema_bytes, state):
    """Return all original ready descriptors; do not invent staging bodies."""
    _check(state)
    prepared = _cpu(state, _normalize_prepare, value, schema_bytes=schema_bytes, state=state)
    return _checked_value(state, _snapshot_document(_cpu(state, _snapshot_validate, prepared['snapshot_json'], schema_bytes=schema_bytes, state=state), state=state)['attachment_manifest'])
    _check(state)


@_wire_checked
def _normalize_receipt(prepare, value, *, schema_bytes, state):
    """Verify a claimed committed result against the exact original request.

    A matching DTO proves shape and binding, never a local COMMIT, remote HTTP
    receipt, complete files, reader capability or authority to change primary.
    """
    _check(state)
    detached = _cpu(state, _wire._plain_prepare_copy, prepare)
    prepared_facts = None
    if detached is None:
        prepared = _cpu(state, _normalize_prepare, prepare, schema_bytes=schema_bytes, state=state)
    else:
        prepared, prepared_facts = _cpu(state, _normalize_prepare_facts, detached, schema_bytes=schema_bytes, state=state)
    _cpu(state, _wire._exact, value, _wire.RECEIPT_FIELDS)
    _cpu(state, _wire._version, value['format_version'])
    if value['kind'] != 'personal_mirror_receipt':
        _wire._fail('KIND_INVALID')
    if _cpu(state, type, value['replayed']) is not bool:
        _wire._fail('REPLAY_INVALID')
    _cpu(state, _facts._integer, value['installed_revision'], 1)
    source = _cpu(state, _wire._source, value['source'])
    receiver = _cpu(state, _wire._receiver, value['receiver'], source)
    facts = prepared_facts
    if facts is None:
        facts = _cpu(state, _snapshot_validate, prepared['snapshot_json'], schema_bytes=schema_bytes, state=state)
    expected = {'operation_id': prepared['operation_id'], 'prepare_sha256': _cpu(state, _wire_digest, prepared), 'snapshot_id': prepared['snapshot_id'], 'snapshot_sha256': prepared['snapshot_sha256'], 'source': prepared['source'], 'receiver': prepared['receiver'], 'installed_revision': prepared['expected_revision'] + 1, 'counts': _cpu(state, dict, _snapshot_counts(facts, state=state)), 'attachment_manifest_sha256': _cpu(state, _wire_digest, _snapshot_document(facts, state=state)['attachment_manifest'] if prepared_facts is not None else _cpu(state, _attachment_manifest, prepared, schema_bytes=schema_bytes, state=state))}
    if _cpu(state, any, (value[key] != item for key, item in _cpu(state, expected.items) if _tick(state))):
        _wire._fail('RECEIPT_BINDING_MISMATCH')
    if _cpu(state, type, value['counts']) is not dict or _cpu(state, set, value['counts']) != _cpu(state, set, expected['counts']) or _cpu(state, any, (_cpu(state, type, count) is not int or count < 0 for count in _cpu(state, value['counts'].values) if _tick(state))):
        _wire._fail('COUNTS_INVALID')
    stamp = value['committed_at']
    if _cpu(state, type, stamp) is not str or not _cpu(state, _wire._STAMP.fullmatch, stamp):
        _wire._fail('COMMIT_TIME_INVALID')
    try:
        parsed = _cpu(state, datetime.fromisoformat, _cpu(state, stamp.replace, 'Z', '+00:00'))
        if parsed.tzinfo != timezone.utc:
            _wire._fail('COMMIT_TIME_INVALID')
    except ValueError:
        _wire._fail('COMMIT_TIME_INVALID')
    _cpu(state, _facts._sha, value['receipt_sha256'])
    if value['receipt_sha256'] != _cpu(state, _wire._receipt_sha256, value):
        _wire._fail('RECEIPT_SHA256_MISMATCH')
    return _checked_value(state, {**_cpu(state, deepcopy, value), 'source': source, 'receiver': receiver})
    _check(state)


@_preservation_checked
def _typed_table_digest(columns, rows, *, state):
    _check(state)
    if _cpu(state, type, columns) not in (list, tuple) or not columns or _cpu(state, len, columns) > 64 or (_cpu(state, len, _cpu(state, set, columns)) != _cpu(state, len, columns)):
        _contract._fail('SQL_COLUMNS_INVALID')
    for column in columns:
        _check(state)
        _cpu(state, _contract_text, column, maximum=128)
    encoded = []
    total = 0
    for row in rows:
        _check(state)
        if _cpu(state, type, row) not in (tuple, list) or _cpu(state, len, row) != _cpu(state, len, columns) or _cpu(state, len, encoded) >= _contract.MAX_ROWS:
            _contract._fail('SQL_ROWS_INVALID')
        raw = _cpu(state, _canonical_bytes, [_cpu(state, _typed_cell, item) for item in row if _tick(state)])
        total += _cpu(state, len, raw)
        if total > _contract.MAX_JSON_BYTES:
            _contract._fail('SQL_ROWS_LIMIT')
        _cpu(state, encoded.append, raw)
    result = _cpu(state, _sha256, _cpu(state, _canonical_bytes, _cpu(state, list, columns)))
    for raw in _cpu(state, sorted, encoded):
        _check(state)
        _cpu(state, _hash_update, result, _cpu(state, _cpu(state, len, raw).to_bytes, 8, 'big'))
        _cpu(state, _hash_update, result, raw)
    return _checked_value(state, {'columns': _cpu(state, list, columns), 'count': _cpu(state, len, encoded), 'sha256': _cpu(state, _hash_hexdigest, result)})
    _check(state)


def _receiver_semantics(rows, *, schema_bytes, state):
    """Original facility rows, without calling receiver runtime write gates."""
    _check(state)
    intents = {r['operation_id']: r for r in rows[_receiver.INTENT] if _tick(state)}
    prepared, receipts = ({}, {})
    for operation, record in _cpu(state, intents.items):
        _check(state)
        _check(state)
        value = _cpu(state, _normalize_prepare, _cpu(state, _decode, record['prepare_json']), schema_bytes=schema_bytes, state=state)
        source_fact, target, previous = (value['source'], value['receiver'], value['previous'])
        expected = {'operation_id': value['operation_id'], 'format_version': 1, 'prepare_sha256': _cpu(state, _prepare_sha256, value, schema_bytes=schema_bytes, state=state), 'snapshot_id': value['snapshot_id'], 'snapshot_sha256': value['snapshot_sha256'], 'source_library_id': source_fact['library_id'], 'source_node_id': source_fact['node_id'], 'source_primary_id': source_fact['personal_primary_id'], 'source_primary_epoch': source_fact['primary_epoch'], 'receiver_library_id': target['library_id'], 'receiver_node_id': target['node_id'], 'previous_snapshot_id': None if previous is None else previous['snapshot_id'], 'previous_snapshot_sha256': None if previous is None else previous['snapshot_sha256'], 'expected_revision': value['expected_revision']}
        _cpu(state, _same, {key: record[key] for key in expected if _tick(state)}, expected, 'RECEIVER_INTENT_INVALID', state=state)
        prepared[operation] = value
    attachment_rows = {}
    for record in rows[_receiver.ATTACHMENT]:
        _check(state)
        _check(state)
        value = _cpu(state, prepared.get, record['operation_id'])
        if value is None:
            _source_fail('RECEIVER_ATTACHMENT_INVALID')
        declared = {d['attachment_id']: d for d in _cpu(state, _attachment_manifest, value, schema_bytes=schema_bytes, state=state) if _tick(state)}
        descriptor = _cpu(state, declared.get, record['attachment_id'])
        if descriptor is None:
            _source_fail('RECEIVER_ATTACHMENT_INVALID')
        _cpu(state, _same, {key: record[key] for key in ('storage_key', 'sha256', 'byte_size') if _tick(state)}, {key: descriptor[key] for key in ('storage_key', 'sha256', 'byte_size') if _tick(state)}, 'RECEIVER_ATTACHMENT_INVALID', state=state)
        _cpu(state, _same, _cpu(state, _decode, record['descriptor_json']), descriptor, 'RECEIVER_ATTACHMENT_INVALID', state=state)
        _cpu(state, _same, record['descriptor_sha256'], _cpu(state, _wire_digest, descriptor), 'RECEIVER_ATTACHMENT_INVALID', state=state)
        if intents[record['operation_id']]['state'] == 'installed' and record['state'] != 'installed':
            _source_fail('RECEIVER_ATTACHMENT_INVALID')
        _cpu(state, _cpu(state, attachment_rows.setdefault, record['operation_id'], []).append, record)
    for record in rows[_receiver.RECEIPT]:
        _check(state)
        _check(state)
        operation = record['operation_id']
        value = _cpu(state, prepared.get, operation)
        if value is None or intents[operation]['state'] != 'installed':
            _source_fail('RECEIVER_RECEIPT_INVALID')
        receipt = _cpu(state, _normalize_receipt, value, _cpu(state, _decode, record['receipt_json']), schema_bytes=schema_bytes, state=state)
        for key in ('operation_id', 'format_version', 'prepare_sha256', 'snapshot_id', 'snapshot_sha256', 'installed_revision', 'receipt_sha256', 'committed_at'):
            _check(state)
            _cpu(state, _same, record[key], receipt[key], 'RECEIVER_RECEIPT_INVALID', state=state)
        if receipt['replayed'] is not False:
            _source_fail('RECEIVER_RECEIPT_INVALID')
        receipts[operation] = receipt
    for operation, intent in _cpu(state, intents.items):
        _check(state)
        if intent['state'] == 'installed':
            if operation not in receipts or {r['attachment_id'] for r in _cpu(state, attachment_rows.get, operation, []) if _tick(state)} != {d['attachment_id'] for d in _cpu(state, _attachment_manifest, prepared[operation], schema_bytes=schema_bytes, state=state) if _tick(state)}:
                _source_fail('RECEIVER_RECEIPT_INVALID')
    heads = rows[_receiver.HEAD]
    if _cpu(state, len, heads) > 1:
        _source_fail('RECEIVER_HEAD_INVALID')
    if heads:
        head = heads[0]
        operation = head['operation_id']
        if operation not in receipts:
            _source_fail('RECEIVER_HEAD_INVALID')
        for key in ('source_library_id', 'source_node_id', 'source_primary_id', 'source_primary_epoch', 'receiver_library_id', 'receiver_node_id'):
            _check(state)
            _cpu(state, _same, head[key], intents[operation][key], 'RECEIVER_HEAD_INVALID', state=state)
        receipt = receipts[operation]
        for left, right in (('revision', 'installed_revision'), ('snapshot_id', 'snapshot_id'), ('snapshot_sha256', 'snapshot_sha256'), ('receipt_sha256', 'receipt_sha256')):
            _check(state)
            _cpu(state, _same, head[left], receipt[right], 'RECEIVER_HEAD_INVALID', state=state)
    elif receipts:
        _source_fail('RECEIVER_HEAD_INVALID')
    _check(state)
