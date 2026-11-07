"""A real manual SQL proof cannot acquire a different core through callbacks."""
from reno import budget_writer_witness as sql, storage_root_inputs as inputs, storage_roots
from reno import sync_source_authority as permanent
from tests.test_source_manual_resume_dual_sha17 import actual_dual_sha, env, _projection


def test_gc_callback_cannot_rebind_actual_manual_row_to_another_core(actual_dual_sha):
    value = actual_dual_sha
    projection = _projection(value)
    row = next(row for row in projection["references"] if row["kind"] == "requests"
        and row["record_id"] == value.intent["record_id"])
    original_core_sha = row["core_sha256"]
    replacement_core_sha = "e" * 64 if original_core_sha != "e" * 64 else "f" * 64
    with sql.connection(value.profile) as con:
        provenance = inputs.read_provenance_projection17(con)
    observed = {"job_read": False, "changed": False}

    def trace(statement):
        if "FROM main.job WHERE job_id=" in statement:
            observed["job_read"] = True

    def check():
        if observed["job_read"] and not observed["changed"]:
            row["core_sha256"] = replacement_core_sha
            observed["changed"] = True

    with value.store.reference_operation(), sql.connection(value.profile) as con:
        con.set_trace_callback(trace)
        try:
            result = storage_roots._manual_proofs(con, projection, provenance,
                value.core["binding"]["home_library_id"], storage_roots._Budget(storage_roots.RootLimits(), check))
        finally:
            con.set_trace_callback(None)
    assert observed["changed"]
    assert result == {(original_core_sha, value.manual_row["request_id"]): permanent.digest(value.manual_row)}
    assert (replacement_core_sha, value.manual_row["request_id"]) not in result
