"""One owned read window for personal HTTP projections, never transport authority."""
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass

from fastapi.responses import JSONResponse

from reno import config, personal, personal_mirror_read, storage_admin, storage_lifecycle

VERSION_HEADER = "X-Reno-Personal-Read-Version"


def private_headers(version=None):
    headers = {"Cache-Control": "private, no-store"}
    if version is not None:
        headers[VERSION_HEADER] = version
    return headers


def error_response(error):
    return JSONResponse({**error.body(), "personal_read_version": None},
        status_code=error.status, headers=private_headers())


def _check_cancelled():
    import anyio
    try:
        anyio.from_thread.check_cancelled()
    except RuntimeError:
        # Direct synchronous callers are also supported; they have no AnyIO
        # worker cancellation scope to inspect.
        pass


@contextmanager
def _ordinary_read(profile, check):
    check()
    with storage_admin.connection(profile) as con:
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        yield con
        check()


@contextmanager
def read_connection(*, request=None, allow_disabled=False, check_cancelled=None):
    """Only factory ENTER can produce an explicit disabled projection.

    Failure in callbacks or final source/proof checks never falls back to the
    ordinary connection. The latter grants no cloud personal capability.
    """
    check = check_cancelled or _check_cancelled
    expected = request.headers.get(VERSION_HEADER) if request is not None else None
    try:
        profile = storage_admin.profile()
        with ExitStack() as stack:
            if not config.is_cloud():
                con = stack.enter_context(_ordinary_read(profile, check))
            elif not personal._enabled("personal_cloud_read_enabled"):
                if not allow_disabled:
                    raise personal.PersonalError("云端个人镜像读取尚未启用", "PERSONAL_MIRROR_READ_DISABLED", 403)
                con = stack.enter_context(_ordinary_read(profile, check))
            else:
                try:
                    con = stack.enter_context(personal_mirror_read.open_installed_read(profile,
                        expected_read_version=expected, check_cancelled=check))
                except personal_mirror_read.PersonalMirrorReadError as error:
                    if not allow_disabled or error.code not in {
                        "PERSONAL_MIRROR_READ_DISABLED", "PERSONAL_MIRROR_READ_NOT_INSTALLED"}:
                        raise
                    con = stack.enter_context(_ordinary_read(profile, check))
            yield con
    except personal_mirror_read.PersonalMirrorReadError as error:
        raise personal.PersonalError(str(error), error.code, error.status) from None
    except storage_lifecycle.StorageLifecycleError as error:
        raise personal.PersonalError("个人数据存储保护暂不可用，请稍后重试", error.code, error.status) from None


@dataclass(frozen=True)
class ReadResult:
    data: object
    version: str | None


def read_result(callback, *, request=None, gate=True, field=False, allow_disabled=False,
                check_cancelled=None):
    with read_connection(request=request, allow_disabled=allow_disabled, check_cancelled=check_cancelled) as con:
        cap = personal.require_read(con, field=field) if gate else personal.capabilities(con)
        result = ReadResult(callback(con), cap["personal_read_version"])
    # Serialize only after the original transaction, source/identity check and
    # original factory exit all succeeded.
    return result


def json_response(result, *, status_code=200):
    return JSONResponse({**result.data, "personal_read_version": result.version},
        status_code=status_code, headers=private_headers(result.version))


def read(callback, *, request=None, gate=True, field=False, allow_disabled=False):
    result = read_result(callback, request=request, gate=gate, field=field, allow_disabled=allow_disabled)
    return json_response(result) if request is not None else result.data
