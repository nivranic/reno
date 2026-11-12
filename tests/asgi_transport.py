"""Preserve real TestClient response bytes and close across HTTP client versions."""

import httpx


class _TestClientResponseStream(httpx.SyncByteStream):
    def __init__(self, response):
        self.response = response

    def __iter__(self):
        yield from self.response.stream

    def close(self):
        self.response.close()


def as_httpx_response(response):
    """Transfer a TestClient response to the SDK's actual httpx stream domain."""
    if isinstance(response, httpx.Response):
        return response
    return httpx.Response(
        response.status_code,
        headers=response.headers.raw,
        stream=_TestClientResponseStream(response),
        extensions=response.extensions,
    )
