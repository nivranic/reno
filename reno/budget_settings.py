"""Non-secret budget settings and conditional saves, separate from paid policy bytes."""
from __future__ import annotations

import hashlib
import json
import os

from . import budget_contract as contract, config


RESOLVER_FIELD = "source_resolution_max_attempts"
FIELDS = frozenset(contract.POLICY_DEFAULTS) | {RESOLVER_FIELD}
READ_ONLY = frozenset({"model_total_tokens_mode"})
CLOUD_FIELDS = frozenset({"budget_enabled", "budget_timezone", "model_daily_attempts",
    "model_reported_tokens_stop_at", "model_daily_output_tokens", "model_operation_max_attempts"})
_STATUSES = {"BUDGET_SETTINGS_INVALID": 400, "BUDGET_SETTINGS_CHANGED": 409,
    "BUDGET_SETTINGS_ENV_CONTROLLED": 409, "BUDGET_SETTINGS_ROLE_FORBIDDEN": 403,
    "BUDGET_SETTINGS_UNAVAILABLE": 503, "BUDGET_SETTINGS_WRITE_BUSY": 503}


class BudgetSettingsError(ValueError):
    def __init__(self, code="BUDGET_SETTINGS_UNAVAILABLE", *, field=None):
        self.code = code if code in _STATUSES else "BUDGET_SETTINGS_UNAVAILABLE"
        self.status = _STATUSES[self.code]
        self.field = field if field in FIELDS else None
        super().__init__(self.code)

    def public(self):
        return {"code": self.code, **({"field": self.field} if self.field is not None else {})}


def _digest(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _snapshot(local, environment):
    """Pure parsing; caller owns the short config read/write lock."""
    values, sources = {}, {}
    for name in sorted(FIELDS):
        values[name] = local.get(name, config.DEFAULTS[name])
        sources[name] = "local" if name in local else "default"
        override = environment.get("RENO_" + name.upper())
        if override is not None:
            sources[name] = "environment"
            values[name] = (override if name in {"budget_timezone", "model_total_tokens_mode"}
                            else json.loads(override))
    policy = contract.parse_policy({name: values[name] for name in contract.POLICY_DEFAULTS})
    resolver = values[RESOLVER_FIELD]
    if type(resolver) is not int or not 1 <= resolver <= 128:
        raise BudgetSettingsError()
    raw_role = environment.get("RENO_NODE_ROLE", local.get("node_role", config.DEFAULTS["node_role"]))
    if type(raw_role) is not str or raw_role.lower() not in {"all", "home", "cloud"}:
        raise BudgetSettingsError()
    role = raw_role.lower()
    fields = {name: {"value": values[name], "source": sources[name],
        "editable": name not in READ_ONLY and sources[name] != "environment"
            and (role != "cloud" or name in CLOUD_FIELDS)} for name in sorted(FIELDS)}
    # A secret rotation need not invalidate a non-secret budget form. The next
    # save still merges the fresh whole local object while holding the writer.
    revision = _digest({"format_version": 1, "node_role": role, "fields": fields})
    return {"effective_policy": policy.public(), RESOLVER_FIELD: resolver,
            "fields": fields, "settings_revision": revision}, role


def read_with_role():
    """Fresh read creates no directories/locks and never returns config secrets."""
    try:
        with config._config_lock:
            local = config._read_local(config.local_path())
            environment = dict(os.environ)
        return _snapshot(local, environment)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise BudgetSettingsError() from None


def read():
    return read_with_role()[0]


def save(updates, expected_settings_revision):
    """Compare and merge under the same OS writer lock as every set_local call."""
    if (type(updates) is not dict or any(type(key) is not str or key not in FIELDS - READ_ONLY for key in updates)
            or type(expected_settings_revision) is not str or len(expected_settings_revision) != 64
            or any(char not in "0123456789abcdef" for char in expected_settings_revision)):
        raise BudgetSettingsError("BUDGET_SETTINGS_INVALID")
    try:
        with config.local_write_lock() as path:
            # Deliberately no set_local fallback: malformed config is preserved.
            local = config._read_local_locked(path)
            environment = dict(os.environ)
            current, role = _snapshot(local, environment)
            if current["settings_revision"] != expected_settings_revision:
                raise BudgetSettingsError("BUDGET_SETTINGS_CHANGED")
            for field in updates:
                if current["fields"][field]["source"] == "environment":
                    raise BudgetSettingsError("BUDGET_SETTINGS_ENV_CONTROLLED", field=field)
                if role == "cloud" and field not in CLOUD_FIELDS:
                    raise BudgetSettingsError("BUDGET_SETTINGS_ROLE_FORBIDDEN", field=field)
            merged = {**local, **updates}
            try:
                result, _ = _snapshot(merged, environment)
            except (ValueError, TypeError, KeyError, UnicodeError):
                raise BudgetSettingsError("BUDGET_SETTINGS_INVALID") from None
            config._write_local_locked(path, merged)
            return result
    except BudgetSettingsError:
        raise
    except config.ConfigWriteBusy:
        raise BudgetSettingsError("BUDGET_SETTINGS_WRITE_BUSY") from None
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise BudgetSettingsError() from None
