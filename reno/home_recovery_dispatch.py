"""Route explicit local recovery using the protected actual image version.

Routing grants no recovery authority. Each implementation rechecks its own
actual schema, identity, State, source and original-operation contract.
"""
from __future__ import annotations

from . import db, home_recovery as legacy, storage_lifecycle as lifecycle
from . import storage_readonly as readonly


def _actual_version(profile):
    profile = lifecycle._profile(profile)
    with lifecycle.shared(profile), readonly.committed_image_locked(profile) as image:
        rows = image.connection.execute(
            "SELECT value FROM main.meta WHERE key='migration_version' LIMIT 2").fetchall()
        if (len(rows) != 1 or type(rows[0][0]) is not str
                or rows[0][0] not in {str(value) for value in range(1, 18)}):
            raise db.SchemaVersionError("Unsupported actual Home recovery schema")
        version = int(rows[0][0])
        image.verify_source()
        return version


def _implementation(profile):
    profile = legacy._profile(profile)
    if _actual_version(profile) == 17:
        from . import home_recovery17
        return home_recovery17
    return legacy


def preview(*, profile, source_id):
    return _implementation(profile).preview(profile=profile, source_id=source_id)


def apply(*, profile, source_id, expected_digest, operation_id):
    return _implementation(profile).apply(profile=profile, source_id=source_id,
        expected_digest=expected_digest, operation_id=operation_id)


def resume(*, profile, operation_id):
    return _implementation(profile).resume(profile=profile, operation_id=operation_id)
