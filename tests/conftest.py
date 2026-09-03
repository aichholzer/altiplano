"""Shared fixtures.

Requests are intercepted at the transport boundary, leaving `_request` untouched.
Real httpx machinery still runs: URL joining, header assembly, status handling, and
JSON decoding are all genuine, and only the socket is fake.
"""

import asyncio
from typing import Any

import httpx
import pytest

HOST = "https://vikunja.test"
TOKEN = "tk_notarealtoken"


class RecordingAPI:
    """Captures outbound requests and serves canned responses."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        # An empty collection. Tests that only assert on the outbound request
        # still need a response the listing tools will accept.
        self._responses = [httpx.Response(200, json=[])]

    def returns(self, payload: Any, status: int = 200) -> None:
        self._responses = [httpx.Response(status, json=payload)]

    def returns_raw(
        self, status: int, content: bytes = b"", headers: dict[str, str] | None = None
    ) -> None:
        """For responses with no JSON body: a 204, an empty 200, or a redirect."""
        self._responses = [httpx.Response(status, content=content, headers=headers)]

    def returns_in_order(self, *responses: httpx.Response) -> None:
        """For a flow that makes more than one request.

        A v2 description update reads before it writes, and the interesting cases
        need those two answered differently.
        """
        self._responses = list(responses)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        # The last response is reused once the queue reaches it. Tests that only
        # assert on the outbound request need not enumerate every reply.
        return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]

    @property
    def last(self) -> httpx.Request:
        assert self.requests, "no request was made"
        return self.requests[-1]


@pytest.fixture
def api_version() -> int:
    """Which Vikunja API version the fake speaks.

    Defaults to 1. Override it per test with
    `@pytest.mark.parametrize("api_version", [1, 2])` to run against both.
    """
    return 1


@pytest.fixture
def api(api_version: int, monkeypatch: pytest.MonkeyPatch) -> RecordingAPI:
    """A fake Vikunja, with credentials supplied via the environment."""
    monkeypatch.setenv("VIKUNJA_URL", f"{HOST}/api/v{api_version}")
    monkeypatch.setenv("VIKUNJA_API_TOKEN", TOKEN)

    recorder = RecordingAPI()
    real_client = httpx.AsyncClient

    def with_mock_transport(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(recorder._handle), **kwargs)

    # Patched on httpx itself. It holds wherever the client is built, whichever
    # module does the building.
    monkeypatch.setattr(httpx, "AsyncClient", with_mock_transport)
    return recorder


@pytest.fixture
def run():
    """Drive a coroutine to completion. No async plugin needed."""
    return lambda coro: asyncio.run(coro)
