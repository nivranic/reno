"""Conservative applicability hints, not a rule engine for free-form prose."""
from .dict import condition_keys, taxonomy


def assess(atom, context):
    conditions = atom.get("conditions") or {}
    if not isinstance(conditions, dict):
        conditions = {}
    supported = set(condition_keys())
    spaces = set(taxonomy()["spaces"]) - {"全屋"}
    details = []
    for key, requested in (context or {}).items():
        source = conditions.get(key)
        if key == "空间" and source in (None, ""):
            source = atom.get("space")
        if source is None or source == "":
            status, reason = "unknown", "来源未明确该条件"
        elif key not in supported or isinstance(source, (dict, list)) or isinstance(requested, (dict, list)):
            status, reason = "unknown", "条件尚未规范，不能自动比较"
        elif type(source) is type(requested) and str(source).strip() == str(requested).strip():
            status, reason = "match", "已提供条件的值相同"
        elif key == "空间" and isinstance(source, str) and isinstance(requested, str) and source in spaces and requested in spaces:
            status, reason = "mismatch", "明确的受控空间不同"
        else:
            status, reason = "unknown", "文字不同不足以判定不适用，请核对原文与例外"
        details.append({"key": key, "requested": requested, "source": source, "status": status, "reason": reason})
    statuses = {d["status"] for d in details}
    status = "mismatch" if "mismatch" in statuses else "unknown" if not details or "unknown" in statuses else "match"
    reason = {"mismatch": "部分已知条件不同，保留证据供比较",
              "unknown": "条件不全或含自由文本，需要进一步核对",
              "match": "已提供条件一致；例外与实际适用性仍须核对"}[status]
    return {"status": status, "reason": reason, "details": details, "exceptions_evaluated": False}
