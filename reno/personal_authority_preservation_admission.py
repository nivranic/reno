"""Fixed native Q1 admission inside the original format-2 target scope.

No caller-owned directory, ledger or decoded request is an admission token.
An operation mkdir consumes slot 1; a CREATE_NEW claim consumes slot 2 even
when later persistence or close fails. Content reads never precede that claim.
"""
from . import personal_authority_resume_policy as policy


def _state(token):
    from . import personal_authority_preservation_target2 as target
    return target._record(token, phases=("opening", "idle"))


def _checkpoint(phase):
    """Fixed internal fault seam; never an argument supplied by a caller."""


def create_original(token):
    state = _state(token)
    operation = state.operation
    if (state.mode != "create" or operation.slot != 1 or operation.readonly
            or state.phase != "opening" or state.directory is not None or operation.occupied_slots):
        state._fail("OWNER_MISMATCH")
    state._open_base_original(create=True, all_roots=True)
    # Native mkdir may succeed before a later gate raises. Its retained name
    # remains the persistent slot even if no in-memory declaration is returned.
    state.directory = state.mkdir(state.namespace, operation.operation_id, key="")
    operation.occupied_slots = (1,)
    _checkpoint("operation_created")
    state._make_run_directories_original(1)
    operation.admission = {"kind": "operation_directory", "relative_key": "",
                           "identity": dict(state.directory.identity)}


def establish_anchor_original(token):
    state = _state(token)
    operation = state.operation
    if (state.mode != "create" or operation.slot != 1 or operation.anchor is not None
            or operation.occupied_slots != (1,)):
        state._fail("OWNER_MISMATCH")
    state._check_request_original()
    name = policy.request_anchor_name(operation.graph.sha("request.json"))
    anchor = state.mkdir(state.directory, name, key=name)
    if state.listing(anchor):
        state._fail("ADMISSION_INVALID")
    operation.anchor = {"relative_key": name, "identity": dict(anchor.identity)}
    _checkpoint("anchor_created")


def resume_original(token):
    state = _state(token)
    operation = state.operation
    if (state.mode != "resume" or operation.slot != 2 or operation.readonly
            or state.phase != "opening" or state.directory is not None or operation.occupied_slots):
        state._fail("OWNER_MISMATCH")
    state._open_base_original(create=False, all_roots=True)
    state.directory = state.open_directory(state.namespace.original.path / operation.operation_id,
                                          parent=state.namespace, key="", private=True)
    names = state.listing(state.directory)
    expected = policy.request_anchor_name(operation.expected_request_sha256)
    anchors = [name for name in names if name.startswith(policy.ANCHOR_PREFIX)]
    if anchors != [expected]:
        state._fail("ANCHOR_MISMATCH")
    allowed = set(state._root_files()) | {expected, "runs", policy.CLAIM_NAME}
    if set(names) - allowed:
        state._fail("ADMISSION_INVALID")
    anchor = state.open_directory(state.directory.original.path / expected, parent=state.directory,
                                  key=expected, private=True)
    if state.listing(anchor):
        state._fail("ADMISSION_INVALID")
    if policy.CLAIM_NAME in names:
        state._fail("ATTEMPTS_EXHAUSTED")
    if "runs" not in names:
        state._fail("ADMISSION_REQUIRED")
    runs = state.open_directory(state.directory.original.path / "runs", parent=state.directory,
                                key="runs", private=True)
    run_names = state.listing(runs)
    if "02" in run_names:
        state._fail("ATTEMPTS_EXHAUSTED")
    if run_names != ("01",):
        state._fail("ADMISSION_INVALID")
    # Nothing above reads file contents. The writer uses CREATE_NEW, and a
    # later failure never removes/reuses this fixed filename or issues a retry.
    operation.phase = "admission"
    writer = state.writer(state.directory, policy.CLAIM_NAME, policy.CLAIM_NAME)
    operation.occupied_slots = (1, 2)
    claim_identity = dict(writer["identity"])
    _checkpoint("claim_created")
    state.close_writer(writer)
    _checkpoint("claim_closed")
    operation.phase = "bootstrap_request"
    state._read_control_original("request.json", request=True)
    state._check_request_original()
    _checkpoint("request_verified")
    operation.phase = "bootstrap_selection"
    for path in ("runs/01/run-attempt.json", "source-metadata.json", "source-projection.json", "selection.json"):
        state._read_control_original(path)
    operation.graph.selection()
    original_anchor = operation.graph.attempt(1)["anchor"]
    if original_anchor != {"relative_key": expected, "identity": dict(anchor.identity)}:
        state._fail("ADMISSION_INVALID")
    # This independent zero-byte read is after the request gate; even its EOF
    # probe is charged. It does not retroactively authorize the preceding claim.
    claimed = state.open_reader(state.directory, policy.CLAIM_NAME, policy.CLAIM_NAME)
    if (claimed.sha256 != state._empty_sha()
            or any(claimed.path_identity[field] != claim_identity[field]
                   for field in ("device", "inode", "mode", "nlink", "byte_size"))):
        state._fail("ADMISSION_INVALID")
    operation.anchor = dict(original_anchor)
    operation.admission = {"kind": "resume_claim", "relative_key": policy.CLAIM_NAME,
        "parent_identity": dict(state.directory.identity), "path_identity": dict(claimed.path_identity),
        "handle_identity": dict(claimed.handle_identity), "sha256": claimed.sha256, "byte_size": 0}
    state._make_run_directories_original(2)
    state.bootstrap_complete = True


def declaration_original(token):
    state = _state(token)
    operation = state.operation
    if operation.admission is None or operation.anchor is None:
        state._fail("ADMISSION_REQUIRED")
    return state._detached({"admission": operation.admission, "anchor": operation.anchor,
                            "occupied_slots": list(operation.occupied_slots)})
