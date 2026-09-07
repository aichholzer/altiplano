"""The HTTP transport end to end: the real ASGI app, the real SDK, the real store.

`test_http_server.py` drives the gate against a recording stub, which is the right
tool for the gate's own decisions and blind to everything the SDK does behind it. A
stub cannot show that a session id is ignored. A stub has no sessions.

These tests run the application `altiplano-http` serves. `build_app` builds it, the
ASGI lifespan starts the session manager, and requests arrive over
`httpx2.ASGITransport`, in process and with no socket. Only Vikunja is synthetic: the
upstream transport answers with a project named after the token it was called with, so
a response identifies which Vikunja identity served it.

What these hold in place, all of it reproduced against the stateful transport before
the fix:

- No `mcp-session-id` is issued. Stateful mode keyed every request on one, and the
  gate could not say which client a session belonged to.
- A fabricated session id shared by two callers, with an identical JSON-RPC request
  id, leaves each of them with their own data. Under the old transport a borrowed
  session was accepted and the owner's in-flight response was lost.
- Session deletion is not honoured. It used to answer 200 and leave the owner
  receiving 404.

A change back to a stateful transport fails this file.
"""

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import httpx2
import pytest

from altiplano import clients, config, http_server

URL = "http://testserver/mcp"
PROTOCOL = "2025-06-18"
UPSTREAM = "https://vikunja.test/api/v1"

# Long enough for the store's validator, and each one identifies its owner in the
# synthetic upstream's reply.
VIKUNJA = {"alice": "tk_alice_00000000", "bob": "tk_bob_000000000"}


def _upstream(request: httpx.Request) -> httpx.Response:
    """A Vikunja that names the token it was called with."""
    presented = request.headers.get("Authorization", "").removeprefix("Bearer ")
    who = presented.removeprefix("tk_").split("_")[0] or "nobody"
    return httpx.Response(200, json=[{"id": 1, "title": f"PROJECT-OF-{who}"}])


@asynccontextmanager
async def _running(app):
    """Run the ASGI lifespan, which is what starts the MCP session manager.

    `httpx2.ASGITransport` does not run it, and without it the app answers nothing.
    Implemented against the protocol directly. The suite takes no new dependency.
    """
    to_app: asyncio.Queue = asyncio.Queue()
    from_app: asyncio.Queue = asyncio.Queue()

    async def receive():
        return await to_app.get()

    async def send(message):
        await from_app.put(message)

    scope = {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}}
    task = asyncio.create_task(app(scope, receive, send))
    await to_app.put({"type": "lifespan.startup"})
    started = await from_app.get()
    assert started["type"] == "lifespan.startup.complete", started
    try:
        yield
    finally:
        await to_app.put({"type": "lifespan.shutdown"})
        await from_app.get()
        await task


@pytest.fixture
def served(tmp_path, monkeypatch):
    """The real gated application, two registered clients, a synthetic Vikunja.

    Returns the app and the two Altiplano client tokens.
    """
    monkeypatch.setattr(clients, "_CLIENTS_FILE", tmp_path / "clients")
    monkeypatch.setattr(http_server, "_CLIENTS_FILE", tmp_path / "clients")
    clients._file_cache = None
    config._warned_about.clear()

    monkeypatch.setenv("VIKUNJA_URL", UPSTREAM)
    # ASGITransport sends `Host: testserver`, and the default allowlist is localhost.
    monkeypatch.setenv("ALTIPLANO_HTTP_ALLOWED_HOSTS", "testserver,testserver:*")
    monkeypatch.setenv("ALTIPLANO_HTTP_ALLOWED_ORIGINS", "http://testserver")

    tokens = {who: clients._add(who, VIKUNJA[who]) for who in ("alice", "bob")}

    # `api._send` builds an `httpx.AsyncClient`. The client side of these tests uses
    # `httpx2`. Patching this therefore reaches the upstream call alone.
    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(_upstream), **kw),
    )
    return http_server.build_app(gated=True), tokens


def _headers(token, session=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL,
    }
    if session:
        headers["mcp-session-id"] = session
    return headers


def _body(response):
    """The JSON-RPC payload, whether it arrived as JSON or as one SSE event."""
    text = response.text
    if text.startswith(("event:", "data:")) or "\ndata:" in text:
        for line in text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
    return response.json()


def _titles(payload):
    """Every project title in a `tools/call` result."""
    found = []
    for block in payload.get("result", {}).get("content", []):
        text = block.get("text")
        if text is None:
            continue
        try:
            items = json.loads(text)
        except ValueError:
            found.append(text)
            continue
        for item in items if isinstance(items, list) else [items]:
            found.append(item.get("title") if isinstance(item, dict) else str(item))
    return found


def client_for(app):
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://testserver")


async def _initialise(http, token):
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "integration", "version": "1"},
        },
    }
    return await http.post(URL, headers=_headers(token), json=body)


async def _list_projects(http, token, request_id, session=None):
    body = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": "list_projects", "arguments": {}},
    }
    return await http.post(URL, headers=_headers(token, session), json=body)


# --- no session is issued ----------------------------------------------------
def test_no_session_id_is_issued(served):
    app, tokens = served

    async def scenario():
        async with _running(app), client_for(app) as http:
            initialised = await _initialise(http, tokens["alice"])
            called = await _list_projects(http, tokens["alice"], 10)
            return initialised, called

    initialised, called = asyncio.run(scenario())

    assert initialised.status_code == 200
    assert "mcp-session-id" not in initialised.headers, "stateful mode issues one here"
    assert _titles(_body(called)) == ["PROJECT-OF-alice"]


def test_each_caller_reaches_its_own_vikunja_identity(served):
    app, tokens = served

    async def scenario():
        async with _running(app), client_for(app) as http:
            await _initialise(http, tokens["alice"])
            await _initialise(http, tokens["bob"])
            alice = await _list_projects(http, tokens["alice"], 10)
            bob = await _list_projects(http, tokens["bob"], 10)
            return alice, bob

    alice, bob = asyncio.run(scenario())

    assert _titles(_body(alice)) == ["PROJECT-OF-alice"]
    assert _titles(_body(bob)) == ["PROJECT-OF-bob"]


# --- a fabricated session id changes nothing ---------------------------------
def test_a_fabricated_session_id_is_ignored(served):
    """Under the stateful transport this is where a borrowed session was accepted."""
    app, tokens = served

    async def scenario():
        async with _running(app), client_for(app) as http:
            await _initialise(http, tokens["alice"])
            return await _list_projects(http, tokens["alice"], 10, session="not-a-session")

    called = asyncio.run(scenario())
    assert called.status_code == 200
    assert _titles(_body(called)) == ["PROJECT-OF-alice"]


def test_two_callers_sharing_a_session_id_and_a_request_id_stay_separate(served):
    """The exact shape of the original defect, concurrently.

    One fabricated session id, one JSON-RPC request id, two clients in flight at once.
    Statefully, a borrowed session was accepted and the owner's response was lost.
    """
    app, tokens = served
    shared_session, shared_id = "shared-fabricated-session", 77

    async def scenario():
        async with _running(app), client_for(app) as http:
            return await asyncio.gather(
                _list_projects(http, tokens["alice"], shared_id, session=shared_session),
                _list_projects(http, tokens["bob"], shared_id, session=shared_session),
            )

    alice, bob = asyncio.run(scenario())

    assert _titles(_body(alice)) == ["PROJECT-OF-alice"]
    assert _titles(_body(bob)) == ["PROJECT-OF-bob"]


def test_deleting_a_session_is_not_honoured_and_calls_continue(served):
    """Statefully this answered 200, and the owner then received 404 on every call."""
    app, tokens = served

    async def scenario():
        async with _running(app), client_for(app) as http:
            initialised = await _initialise(http, tokens["alice"])
            deleted = await http.delete(
                URL, headers=_headers(tokens["bob"], session="any-session-at-all")
            )
            after = await _list_projects(http, tokens["alice"], 12)
            return initialised, deleted, after

    _, deleted, after = asyncio.run(scenario())

    assert deleted.status_code != 200, "there is no session to delete"
    assert after.status_code == 200
    assert _titles(_body(after)) == ["PROJECT-OF-alice"]


# --- the gate, through the real application ---------------------------------
def test_an_unregistered_token_is_refused_end_to_end(served):
    app, _ = served

    async def scenario():
        async with _running(app), client_for(app) as http:
            return await _initialise(http, "altp_neverissued")

    response = asyncio.run(scenario())
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Bearer realm="altiplano"'


def test_a_client_with_no_vikunja_token_is_refused_end_to_end(served):
    app, _ = served
    token = clients._mint()
    clients._CLIENTS_FILE.write_text(
        f"{clients._HEADER}\nstale:{clients._digest(token)}::2026-09-05T00:00:00Z\n"
    )
    clients._CLIENTS_FILE.chmod(0o600)
    clients._file_cache = None

    async def scenario():
        async with _running(app), client_for(app) as http:
            return await _initialise(http, token)

    response = asyncio.run(scenario())
    assert response.status_code == 403
    assert b"Vikunja identity" in response.content


# --- the real client library --------------------------------------------------
def test_the_real_client_library_serves_two_users_concurrently(served):
    """What a client such as Claude Code actually does, against the shipped app.

    The raw checks above control headers a library manages for you. This one gives
    that control up in exchange for exercising the negotiation an MCP client performs.
    """
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    app, tokens = served

    async def as_client(who):
        auth = {"Authorization": f"Bearer {tokens[who]}"}
        transport = httpx2.ASGITransport(app=app)
        async with (
            httpx2.AsyncClient(transport=transport, headers=auth) as http,
            streamable_http_client(URL, http_client=http) as (read, write, *_),
            ClientSession(read, write) as session,
        ):
            info = await session.initialize()
            listed = await session.list_tools()
            first = await session.call_tool("list_projects", {})
            second = await session.call_tool("list_projects", {})
            return {
                "server": info.server_info.name,
                "tools": len(listed.tools),
                "titles": _from_result(first) + _from_result(second),
            }

    async def scenario():
        async with _running(app):
            return await asyncio.gather(as_client("alice"), as_client("bob"))

    alice, bob = asyncio.run(scenario())

    assert alice["server"] == bob["server"] == "altiplano"
    assert alice["tools"] == bob["tools"] == 35
    assert alice["titles"] == ["PROJECT-OF-alice", "PROJECT-OF-alice"]
    assert bob["titles"] == ["PROJECT-OF-bob", "PROJECT-OF-bob"]


def _from_result(result):
    """Project titles out of an SDK `CallToolResult`."""
    found = []
    for block in result.content:
        text = getattr(block, "text", None)
        if text is None:
            continue
        try:
            items = json.loads(text)
        except ValueError:
            found.append(text)
            continue
        for item in items if isinstance(items, list) else [items]:
            found.append(item.get("title") if isinstance(item, dict) else str(item))
    return found
