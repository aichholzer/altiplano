"""The Streamable HTTP entry point, with a bearer-token gate in front of it.

`altiplano` keeps speaking stdio. This serves the same `MCPServer`, the same tools,
and the same prompt over HTTP, to any number of clients, with one Vikunja token
held here on the server.

### Why the gate is ASGI middleware

The SDK offers `token_verifier`, and it refuses to accept one without
`AuthSettings`. `AuthSettings` in turn requires `issuer_url` and
`resource_server_url`, and switching it on makes the SDK publish
`/.well-known/oauth-protected-resource` and wrap the endpoint in
`RequireAuthMiddleware`. A compliant client then reads the 401, follows the
metadata, and goes looking for an OAuth authorisation server that does not exist.

`ServerMiddleware` is the other hook the SDK provides. It sits inside the MCP
dispatcher with `ctx.method` and `ctx.params`, sees no HTTP headers, and rejects
with an MCP error where a 401 belongs.

So the check wraps the ASGI app instead. The `WWW-Authenticate` header below names
a realm and nothing else, which sends no client hunting for metadata.

The wrapper is written against the ASGI interface directly, with no Starlette
import, which keeps `uvicorn` the only dependency this transport adds.
"""

import ipaddress
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings

from altiplano.clients import _CLIENTS_FILE, _identify, _labels
from altiplano.server import mcp

_LOG = logging.getLogger("altiplano.http")

_LOCAL_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")
_LOCAL_ORIGINS = ("http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*")

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


def _list_setting(name: str, default: tuple[str, ...]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None:
        return list(default)
    values = [value.strip() for value in raw.split(",") if value.strip()]
    if not values:
        raise RuntimeError(f"{name} must contain at least one comma-separated value")
    return values


def _port() -> int:
    raw = os.environ.get("ALTIPLANO_HTTP_PORT", "8000")
    try:
        port = int(raw)
    except ValueError as err:
        raise RuntimeError("ALTIPLANO_HTTP_PORT must be an integer") from err
    if not 1 <= port <= 65535:
        raise RuntimeError("ALTIPLANO_HTTP_PORT must be between 1 and 65535")
    return port


def _path() -> str:
    path = os.environ.get("ALTIPLANO_HTTP_PATH", "/mcp")
    if not path.startswith("/"):
        raise RuntimeError("ALTIPLANO_HTTP_PATH must begin with '/'")
    return path


def _host() -> str:
    return os.environ.get("ALTIPLANO_HTTP_HOST", "127.0.0.1")


def _is_loopback(host: str) -> bool:
    """Whether binding to `host` keeps the listener on this machine.

    `127.0.0.0/8` is loopback in full, which is why the check goes through
    `ipaddress` and not a string match. A hostname other than `localhost` reads as
    remote, which is the safe direction: an unrecognised bind address then requires
    client tokens.
    """
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _bearer(scope: Scope) -> str | None:
    """The bearer token on this request, when there is one.

    ASGI header names arrive lower-cased as bytes. Anything other than a `Bearer`
    scheme with a non-empty value reads as absent.
    """
    for name, value in scope.get("headers") or ():
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
            return None
    return None


async def _unauthorised(send: Send) -> None:
    body = b'{"error":"unauthorised"}'
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                # A realm and nothing more. A `resource_metadata` parameter here is
                # what would start an OAuth discovery attempt on the client.
                (b"www-authenticate", b'Bearer realm="altiplano"'),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class _RequireClientToken:
    """Reject any HTTP request whose bearer token matches no registered client.

    Only `http` scopes are inspected. Every other scope passes straight through,
    the `lifespan` one included: that scope starts the MCP session manager, and a
    wrapper that swallowed it would leave the server unable to answer anything.
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        label = _identify(_bearer(scope))
        if label is None:
            _LOG.warning(
                "rejected %s %s from %s",
                scope.get("method", "?"),
                scope.get("path", "?"),
                _peer(scope),
            )
            await _unauthorised(send)
            return
        _LOG.info("%s %s as %s", scope.get("method", "?"), scope.get("path", "?"), label)
        await self._app(scope, receive, send)


def _peer(scope: Scope) -> str:
    client = scope.get("client")
    return client[0] if client else "unknown"


def build_app() -> Any:
    """The ASGI application, gated when any client is registered.

    Three cases, and the middle one is the reason this is a function:

    - Clients registered: every request needs a token.
    - No clients, bound to loopback: served open, with a warning. A fresh checkout
      can be smoke-tested without minting a key first.
    - No clients, bound anywhere else: `main` refuses to start.
    """
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_list_setting("ALTIPLANO_HTTP_ALLOWED_HOSTS", _LOCAL_HOSTS),
        allowed_origins=_list_setting("ALTIPLANO_HTTP_ALLOWED_ORIGINS", _LOCAL_ORIGINS),
    )
    app = mcp.streamable_http_app(
        streamable_http_path=_path(),
        transport_security=security,
        host=_host(),
    )
    if not _labels():
        _LOG.warning(
            "no client tokens in %s. This listener is open to anyone who can reach "
            "it. Register a client with: altiplano-clientkey add <label>",
            _CLIENTS_FILE,
        )
        return app
    return _RequireClientToken(app)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    host = _host()
    if not _is_loopback(host) and not _labels():
        raise RuntimeError(
            f"refusing to bind {host} with no client tokens in {_CLIENTS_FILE}. "
            "Every caller would act as the configured Vikunja identity, with every "
            "write and delete tool available. Register a client first with: "
            "altiplano-clientkey add <label>"
        )
    _LOG.info("serving %d client(s) on %s:%d%s", len(_labels()), host, _port(), _path())
    uvicorn.run(build_app(), host=host, port=_port(), log_level="info")


if __name__ == "__main__":
    main()
