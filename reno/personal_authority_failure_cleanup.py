"""Fixed internal cleanup for the three original failure-record writers.

No owner, token, budget, publication or completion capability is created here.
Callers supply only their actual local resources and original stream closer.
"""


def _note_original(primary, code, /):
    try:
        BaseException.add_note(primary, code)
    except BaseException:
        pass


def _close_failure_original(stream, owned, primary, close_stream, /):
    """Keep the caller's first and attempt each original closer in order."""
    if stream is not None:
        try:
            close_stream(stream)
        except BaseException:
            _note_original(primary, "AUTHORITY_RAW_PRESERVATION_FAILURE_CLOSE_FAILED")
    for directory in reversed(owned):
        try:
            directory.__exit__(type(primary), primary,
                BaseException.__dict__["__traceback__"].__get__(primary))
        except BaseException:
            _note_original(primary, "AUTHORITY_RAW_PRESERVATION_FAILURE_CLOSE_FAILED")
