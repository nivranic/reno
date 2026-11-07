"""Read-only UI projections; local admission observations are never provider bills."""
from __future__ import annotations

import sqlite3

from . import budget, budget_contract as contract, budget_schema, migrations, storage_lifecycle


WAIT_LIMIT = 50
_GROUP = {"settled": "confirmed", "reserved": "held", "dispatching": "in_flight", "indeterminate": "unknown"}


class BudgetViewError(ValueError):
    code = "BUDGET_VIEW_UNAVAILABLE"
    status = 503

    def __init__(self):
        super().__init__(self.code)


def _attempts(rows):
    counts = dict.fromkeys(("admissions", "confirmed", "held", "in_flight", "unknown"), 0)
    for row in rows:
        counts["admissions"] += 1
        counts[_GROUP[row["state"]]] += 1
    return counts


def _tokens(models):
    dispatched = [row for row in models if row["state"] != "reserved"]
    observations = [row["_usage"] for row in dispatched]
    quality = dict.fromkeys(("reported", "partial", "missing", "invalid", "unobserved"), 0)
    for usage in observations:
        quality["unobserved" if usage is None else usage.quality] += 1
    result = {"quality_counts": quality}
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        known = [getattr(usage, field) for usage in observations
                 if usage is not None and getattr(usage, field) is not None]
        result[field] = {"reported": sum(known) if known or not observations else None,
            "covered_attempts": len(known), "missing_attempts": len(observations) - len(known),
            "dispatched_attempts": len(dispatched)}
    return result


def _output_holds(models):
    counts = dict.fromkeys(("held", "in_flight", "unknown", "confirmed_missing_output"), 0)
    for row in models:
        group = _GROUP[row["state"]]
        if group == "confirmed":
            usage = row["_usage"]
            if usage is not None and usage.output_tokens is not None:
                continue
            group = "confirmed_missing_output"
        counts[group] += budget._snapshot(row)["units"]["reserved_output_tokens"]
    return {**counts, "total": sum(counts.values())}


def _waiting(con):
    total = con.execute("SELECT COUNT(*) FROM budget_operation WHERE state='waiting'").fetchone()[0]
    rows = con.execute("SELECT operation_id,kind,root_job_id,wait_reason,resume_at FROM budget_operation "
        "WHERE state='waiting' ORDER BY updated_at,operation_id LIMIT ?", (WAIT_LIMIT,)).fetchall()
    result = []
    for row in rows:
        budget._uuid(row["operation_id"])
        if row["kind"] not in budget_schema.KINDS or row["wait_reason"] not in contract.DEFER_REASONS:
            raise BudgetViewError()
        if row["root_job_id"] is not None:
            budget._integer(row["root_job_id"], minimum=1)
        if row["resume_at"] is not None:
            budget._datetime(row["resume_at"])
        result.append({"operation_id": row["operation_id"], "kind": row["kind"],
            "root_job_id": row["root_job_id"], "reason": row["wait_reason"], "resume_at": row["resume_at"]})
    return {"scope": "all_history", "total": total, "operations": result, "limit": WAIT_LIMIT}


def inspect(*, profile, policy=None, now=None):
    """One guarded, query-only SQL snapshot using the ledger's original window math."""
    try:
        if type(profile) is not storage_lifecycle.StorageProfile:
            raise BudgetViewError()
        settings = budget._policy(policy)
        window = contract.period_for(settings, budget._now(now))
        with storage_lifecycle.shared(profile):
            # mode=ro cannot create a missing database; no request upgrades it.
            con = migrations._open(profile.database, readonly=True)
            try:
                con.execute("PRAGMA query_only=ON")
                con.execute("BEGIN")
                migrations.validate(con)
                budget_schema.validate(con)
                models, downloads, logical, sources = budget._counts(con, window)
                groups = {"model": models, "download": downloads, **sources}
                return {"scope": "local_ledger_only", "history_before_ledger": "unknown",
                    "window": window.public(), "attempts": {kind: _attempts(rows) for kind, rows in groups.items()},
                    "download_logical_admissions": len(logical), "tokens": _tokens(models),
                    "conservative_output_tokens": _output_holds(models),
                    "all_history": {"unknown_attempts": con.execute(
                        "SELECT COUNT(*) FROM budget_attempt WHERE state='indeterminate'").fetchone()[0],
                        "waiting_operations": con.execute(
                            "SELECT COUNT(*) FROM budget_operation WHERE state='waiting'").fetchone()[0],
                        "stored_results": con.execute(
                            "SELECT COUNT(*) FROM budget_attempt WHERE result_storage_state='stored'").fetchone()[0]},
                    "waiting": _waiting(con)}
            finally:
                con.close()
    except (sqlite3.DatabaseError, OSError, ValueError, TypeError, KeyError, UnicodeError,
            migrations.SchemaVersionError, storage_lifecycle.StorageLifecycleError):
        raise BudgetViewError() from None
