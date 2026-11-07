"""Ordinary HTTP heartbeat must renew the independent Cloud SQL session."""
from datetime import timedelta

import pytest

from reno import storage_admin, sync_source_lifecycle17 as effects
from reno.sync_client import SyncClientError
from tests.test_sync_source_legacy_pending_intake17 import real_cycle, original_intent, sql_rows
from tests.test_sync_source_restore_cycle17 import actual_call


def test_actual_heartbeat_after_readmit_renews_cloud_without_mutating_home_sql(real_cycle):
    value = real_cycle
    intent = original_intent(value)
    selected = {"profile": value.profile, "store": value.store,
        "request_id": intent["record"]["record_id"]}
    actual_call(effects.install_recovery_pending17_locked, **selected)
    actual_call(effects.deliver_recovery17, client=value.client, **selected)
    state = actual_call(effects.reconcile_recovery17_locked, **selected)
    before = sql_rows(value.profile)
    value.transport.now += timedelta(seconds=60)
    try:
        observed = value.client.heartbeat(value.source, state["session_id"], lease=state["lease"])
    except SyncClientError as error:
        # Preserve the actual closed server code without exposing its body,
        # URL, authentication headers or arbitrary remote error detail.
        pytest.fail(f"Actual heartbeat rejected: {error.code} (status={error.status})", pytrace=False)
    with storage_admin.connection(value.cloud) as connection:
        current = dict(connection.execute("SELECT session_id,lease_token,generation,lease_until "
            "FROM sync_session WHERE slot=1").fetchone())
    assert current == observed["lease"]
    assert current["lease_until"] > state["lease"]["lease_until"]
    assert sql_rows(value.profile) == before
    value.store.observe_recovery(observed["recovery"], lease=observed["lease"])
    assert value.store.read()["lease"] == current
