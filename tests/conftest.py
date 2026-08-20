"""Shared fixtures.

Requests are intercepted at the transport boundary rather than by replacing
`_request`, so real httpx machinery still runs: URL joining, header assembly,
status handling and JSON decoding are all genuine, and only the socket is fake.
"""

import asyncio
from typing import Any

import httpx
import pytest

from altiplano import server

HOST = "https://vikunja.test"
TOKEN = "tk_notarealtoken"


class RecordingAPI:
    """Captures outbound requests and serves canned responses."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        # An empty collection, because tests that only assert on the outbound
        # request still need a response the listing tools will accept.
        self._response = httpx.Response(200, json=[])

    def returns(self, payload: Any, status: int = 200) -> None:
        self._response = httpx.Response(status, json=payload)

    def returns_raw(self, status: int, content: bytes = b"") -> None:
        """For responses with no JSON body, such as 204 or an empty 200."""
        self._response = httpx.Response(status, content=content)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._response

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

    monkeypatch.setattr(server.httpx, "AsyncClient", with_mock_transport)
    return recorder


@pytest.fixture
def run():
    """Drive a coroutine to completion, so tests need no async plugin."""
    return lambda coro: asyncio.run(coro)
