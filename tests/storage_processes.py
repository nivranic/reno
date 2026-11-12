"""Spawn entry points for actual isolated storage ownership tests."""

def _exclusive(profile, entered, release, requested=None, attempted=None):
    from reno import storage_lifecycle as lifecycle

    if requested is not None:
        requested.wait(15)
    if attempted is not None:
        attempted.set()
    with lifecycle.exclusive(profile, timeout=15):
        entered.set()
        release.wait(15)


def _update(profile, attempted, done):
    from reno.sync_state import StateStore

    value = StateStore(profile.database.parent, profile=profile)
    attempted.set()
    value.update({"seeded": True})
    done.set()
