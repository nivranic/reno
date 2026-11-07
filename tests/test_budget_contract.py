"""B01 pure policy/meter boundaries; synthetic values, no external spending."""
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import traceback
import uuid
from zoneinfo import ZoneInfo

import pytest

from reno import budget_contract as budget, config


UTC = timezone.utc
STAMP = datetime(2026, 10, 1, 12, tzinfo=UTC)


def test_defaults_are_immutable_and_public_projection_is_fresh():
    policy = budget.parse_policy({})
    assert isinstance(policy, budget.Policy)
    assert policy.budget_enabled is False and policy.budget_timezone == "UTC"
    assert policy.download_interval_min_seconds == policy.download_interval_max_seconds == 0
    assert policy.model_operation_max_attempts == 8
    assert policy.model_total_tokens_mode == "reported_only"
    for name in ("download_daily_videos", "download_rate_bytes_per_second", "model_daily_attempts",
                 "model_reported_tokens_stop_at", "model_daily_output_tokens"):
        assert getattr(policy, name) is None
    before = budget.policy_sha256(policy)
    public = policy.public()
    public["budget_enabled"] = True
    assert policy.public()["budget_enabled"] is False and budget.policy_sha256(policy) == before
    with pytest.raises(FrozenInstanceError):
        policy.budget_enabled = True
    assert budget.policy_sha256(budget.parse_policy(dict(reversed(list(policy.public().items()))))) == before
    assert budget.policy_sha256(budget.parse_policy({"model_daily_attempts": 0})) != before


@pytest.mark.parametrize("field", ["download_daily_videos", "model_daily_attempts", "model_reported_tokens_stop_at",
                                  "model_daily_output_tokens", "download_interval_min_seconds",
                                  "download_interval_max_seconds", "model_operation_max_attempts"])
@pytest.mark.parametrize("value", [True, False, 1.0, "1", -1, 2**63])
def test_numbers_reject_coercion_negative_and_overflow(field, value):
    with pytest.raises(budget.BudgetContractError):
        budget.parse_policy({field: value})


@pytest.mark.parametrize("value", [None, 0, 1, "false", [], {}])
def test_enabled_requires_real_bool(value):
    with pytest.raises(budget.BudgetContractError):
        budget.parse_policy({"budget_enabled": value})


def test_zero_null_intervals_rate_and_operation_attempt_boundaries():
    for value in (None, 0, 2**63 - 1):
        assert budget.parse_policy({"model_daily_attempts": value}).model_daily_attempts == value
    assert budget.parse_policy({"model_operation_max_attempts": 128}).model_operation_max_attempts == 128
    assert budget.parse_policy({"download_rate_bytes_per_second": 1}).download_rate_bytes_per_second == 1
    for values in ({"download_rate_bytes_per_second": 0}, {"model_operation_max_attempts": 0},
                   {"model_operation_max_attempts": 129}, {"download_interval_min_seconds": 2},
                   {"download_interval_max_seconds": 86401}, {"download_interval_min_seconds": None}):
        with pytest.raises(budget.BudgetContractError):
            budget.parse_policy(values)
    assert budget.parse_policy({"download_interval_max_seconds": 86400}).download_interval_max_seconds == 86400


@pytest.mark.parametrize("values", [{"private-secret-key": "private-secret-value"},
                                   {"budget_timezone": "private-secret-zone"},
                                   {"budget_timezone": "../private-secret-path"},
                                   {"budget_timezone": " Asia/Shanghai "},
                                   {"model_total_tokens_mode": "strict"},
                                   {"model_total_tokens_mode": None}, {"model_total_tokens_mode": "private-secret-mode"}])
def test_policy_errors_have_only_closed_codes_and_known_fields(values):
    with pytest.raises(budget.BudgetContractError) as caught:
        budget.parse_policy(values)
    diagnostic = str(caught.value) + json.dumps(caught.value.public())
    assert "private-secret" not in diagnostic
    assert caught.value.code in budget.ERROR_CODES
    assert caught.value.field is None or caught.value.field in budget.ERROR_FIELDS


def test_direct_dto_construction_cannot_skip_policy_validation():
    with pytest.raises(budget.BudgetContractError):
        budget.Policy(model_daily_attempts=True)


@pytest.mark.parametrize("zone,day,start,end", [
    ("UTC", "2026-10-01", "2026-10-01T00:00:00+00:00", "2026-10-02T00:00:00+00:00"),
    ("Asia/Shanghai", "2026-10-02", "2026-10-01T16:00:00+00:00", "2026-10-02T16:00:00+00:00"),
])
def test_period_uses_explicit_natural_day(zone, day, start, end):
    window = budget.period_for(budget.parse_policy({"budget_timezone": zone}), datetime(2026, 10, 1, 16, tzinfo=UTC))
    assert window.local_date.isoformat() == day
    assert window.start_utc == datetime.fromisoformat(start) and window.end_utc == datetime.fromisoformat(end)
    assert window.period_key == zone + "/" + day
    assert window.public()["timezone"] == zone
    with pytest.raises(FrozenInstanceError):
        window.end_utc = STAMP


@pytest.mark.parametrize("at,hours", [(datetime(2026, 3, 8, 12, tzinfo=UTC), 23),
                                    (datetime(2026, 11, 1, 12, tzinfo=UTC), 25)])
def test_dst_day_is_not_fixed_24_hours(at, hours):
    window = budget.period_for(budget.parse_policy({"budget_timezone": "America/New_York"}), at)
    assert window.end_utc - window.start_utc == timedelta(hours=hours)


def test_windows_use_positive_half_open_overlap_and_do_not_chain():
    policy = budget.parse_policy({})
    first = budget.period_for(policy, STAMP)
    next_day = budget.period_for(policy, STAMP + timedelta(days=1))
    shanghai = budget.period_for(budget.parse_policy({"budget_timezone": "Asia/Shanghai"}), STAMP)
    assert budget.windows_overlap(first, first)
    assert not budget.windows_overlap(first, next_day)
    assert budget.windows_overlap(first, shanghai)
    assert not budget.windows_overlap(next_day, shanghai)
    with pytest.raises(budget.BudgetContractError):
        budget.Window("UTC", first.local_date, first.end_utc, first.start_utc)


def test_clock_compares_utc_across_dst_fold_and_rejects_rollback():
    zone = ZoneInfo("America/New_York")
    before = datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=0)
    after = datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=1)
    budget.validate_admission_clock(after, before)
    budget.validate_admission_clock(after, after)
    budget.validate_admission_clock(after, None)
    with pytest.raises(budget.BudgetDeferred) as caught:
        budget.validate_admission_clock(before, after)
    assert caught.value.reason == "clock_rollback" and caught.value.resume_at is None
    with pytest.raises(budget.BudgetContractError):
        budget.period_for(budget.parse_policy({}), datetime(2026, 10, 1))


def test_effective_max_tokens_matches_existing_payload_without_coercion():
    assert budget.effective_max_tokens("glm53_anthropic", 8192) == 128000
    assert budget.effective_max_tokens("glm53_anthropic", 256000) == 256000
    assert budget.effective_max_tokens("paas_v4", 8192) == 8192
    for requested in (0, True, 1.0, "8192", -1, 2**63):
        with pytest.raises(budget.BudgetContractError):
            budget.effective_max_tokens("paas_v4", requested)
    with pytest.raises(budget.BudgetContractError) as caught:
        budget.effective_max_tokens("private-secret-adapter", 1)
    assert "private-secret" not in str(caught.value)


def test_provider_usage_retains_original_counters_and_details_without_summing():
    usage = budget.parse_usage("paas_v4", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 99,
        "prompt_tokens_details": {"cached_tokens": 7}, "completion_tokens_details": {"reasoning_tokens": 3},
        "private-secret-field": "private-secret-body"})
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (10, 5, 99)
    assert usage.quality == "reported"
    public = usage.public()
    assert public["details"] == {"prompt_tokens_details.cached_tokens": 7,
                                  "completion_tokens_details.reasoning_tokens": 3}
    public["details"].clear()
    assert len(usage.details) == 2 and "private-secret" not in json.dumps(usage.public())
    with pytest.raises(FrozenInstanceError):
        usage.input_tokens = 0
    anthropic = budget.parse_usage("glm53_anthropic", {"input_tokens": 10, "output_tokens": 5,
        "cache_read_input_tokens": 7, "cache_creation_input_tokens": 3})
    assert anthropic.total_tokens is None and anthropic.quality == "reported"
    assert anthropic.public()["details"] == {"cache_creation_input_tokens": 3, "cache_read_input_tokens": 7}


@pytest.mark.parametrize("values", [
    {"adapter": "paas_v4", "quality": "reported"},
    {"adapter": "paas_v4", "quality": "missing", "input_tokens": 0},
    {"adapter": "paas_v4", "quality": "invalid"},
    {"adapter": "glm53_anthropic", "quality": "reported", "input_tokens": 1, "output_tokens": 2,
     "details": (("prompt_tokens_details.cached_tokens", 3),)},
    {"adapter": "paas_v4", "quality": "reported", "input_tokens": 1, "output_tokens": 2, "total_tokens": 3,
     "details": (("cache_read_input_tokens", 3),)},
    {"adapter": "glm53_anthropic", "quality": "invalid", "invalid_fields": ("prompt_tokens",)},
])
def test_direct_usage_constructor_cannot_forge_quality_or_adapter_provenance(values):
    with pytest.raises(budget.BudgetContractError):
        budget.Usage(**values)


def test_direct_anthropic_usage_can_report_input_output_without_invented_total():
    usage = budget.Usage("glm53_anthropic", "reported", 1, 2)
    assert usage.total_tokens is None and usage.public()["quality"] == "reported"


@pytest.mark.parametrize("value", [True, -1, "0", 0.0, 2**63, [], {}])
def test_bad_vendor_usage_is_invalid_without_throwing_or_manufacturing_zero(value):
    usage = budget.parse_usage("paas_v4", {"prompt_tokens": value, "completion_tokens": 4})
    assert usage.quality == "invalid" and usage.input_tokens is None and usage.output_tokens == 4
    assert usage.total_tokens is None
    assert usage.public()["invalid_fields"] == ["prompt_tokens"]


@pytest.mark.parametrize("value", [None, {}, {"prompt_tokens": None}])
def test_missing_usage_stays_unknown(value):
    usage = budget.parse_usage("paas_v4", value)
    assert usage.quality == "missing" and usage.input_tokens is usage.output_tokens is usage.total_tokens is None


def test_explicit_zero_partial_and_bad_nested_usage_have_distinct_quality():
    zero = budget.parse_usage("paas_v4", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    assert zero.quality == "reported" and zero.total_tokens == 0
    assert budget.parse_usage("paas_v4", {"prompt_tokens": 1}).quality == "partial"
    assert budget.parse_usage("paas_v4", {"prompt_tokens_details": []}).quality == "invalid"
    assert budget.parse_usage("paas_v4", "private-secret-body").quality == "invalid"


def test_deferred_reason_and_error_metadata_are_closed():
    operation = str(uuid.uuid4())
    waiting = budget.BudgetDeferred("daily_limit", resume_at=STAMP, operation_id=operation)
    assert waiting.public()["resume_at"].endswith("Z") and waiting.operation_id == operation
    with pytest.raises(budget.BudgetContractError) as caught:
        budget.BudgetDeferred("private-secret-reason")
    assert "private-secret" not in str(caught.value)
    sanitized = budget.BudgetContractError("private-secret-code", field="private-secret-field")
    assert "private-secret" not in str(sanitized) + json.dumps(sanitized.public())


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    root = tmp_path / "fresh-policy"
    root.mkdir()
    local = root / "config.test.json"
    local.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("RENO_QA_ROOT", str(root))
    monkeypatch.setenv("RENO_CONFIG_FILE", str(local))
    monkeypatch.setenv("RENO_DATA_DIR", str(root / "data"))
    for name, path in {"DATA": root / "data", "MEDIA": root / "media", "FRAMES": root / "media/frames",
                      "AUDIO": root / "media/audio", "ORIG": root / "media/originals",
                      "FRAME_CACHE": root / "frames_cache"}.items():
        monkeypatch.setattr(config, name, path)
    return root, local


def test_fresh_settings_reads_once_under_short_lock_without_cache_or_creation(settings_file, monkeypatch):
    root, local = settings_file
    cached = {"budget_enabled": True, "private-secret-key": "private-secret-cache"}
    monkeypatch.setattr(config, "_cache", cached)
    reads = []
    original_read, original_parse = config._read_local, budget.parse_policy
    def read(path):
        assert config._config_lock.locked()
        reads.append(path)
        return original_read(path)
    def parse(values):
        assert not config._config_lock.locked()
        return original_parse(values)
    monkeypatch.setattr(config, "_read_local", read)
    monkeypatch.setattr(budget, "parse_policy", parse)
    first = config.budget_settings()
    assert first["budget_enabled"] is False and len(reads) == 1
    local.write_text(json.dumps({"budget_enabled": True, "model_daily_attempts": 2,
                               "zhipu_api_key": "private-secret-key", "other_business_field": "keep"}), encoding="utf-8")
    assert config.budget_settings()["model_daily_attempts"] == 2
    monkeypatch.setenv("RENO_BUDGET_ENABLED", "false")
    monkeypatch.setenv("RENO_MODEL_DAILY_ATTEMPTS", "null")
    monkeypatch.setenv("RENO_BUDGET_TIMEZONE", "Asia/Shanghai")
    final = config.budget_settings()
    assert final["budget_enabled"] is False and final["model_daily_attempts"] is None
    assert final["budget_timezone"] == "Asia/Shanghai" and len(reads) == 3
    assert config._cache is cached and cached["budget_enabled"] is True
    assert "private-secret" not in json.dumps(final)
    assert not (root / "data").exists() and not (root / "media").exists()


@pytest.mark.parametrize("key,value", [("RENO_BUDGET_ENABLED", "0"), ("RENO_BUDGET_ENABLED", "False"),
                                      ("RENO_MODEL_DAILY_ATTEMPTS", '"1"'), ("RENO_MODEL_DAILY_ATTEMPTS", "1.0"),
                                      ("RENO_MODEL_DAILY_ATTEMPTS", "true"), ("RENO_MODEL_DAILY_ATTEMPTS", "private-secret-input")])
def test_env_scalar_errors_are_closed(settings_file, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(budget.BudgetContractError) as caught:
        config.budget_settings()
    assert "private-secret" not in "".join(traceback.format_exception(caught.value))


def test_config_read_failure_does_not_leak_path_or_fallback(settings_file):
    root, local = settings_file
    local.unlink()
    with pytest.raises(budget.BudgetContractError) as caught:
        config.budget_settings()
    assert caught.value.code == "BUDGET_CONFIG_INVALID"
    assert str(local) not in "".join(traceback.format_exception(caught.value))
    assert not (root / "data").exists()
