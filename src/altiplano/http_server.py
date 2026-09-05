"""The Streamable HTTP entry point, with a bearer-token gate in front of it.

`altiplano` keeps speaking stdio. This serves the same `MCPServer`, the same tools,
and the same prompt over HTTP, to any number of clients, with one Vikunja token
held here on the server.

### The authentication policy is explicit

The gate is always installed. An empty store denies every request, and an
unreadable store refuses to start. The policy never follows from the contents of
the store: "nobody is authorised" and "authorise everybody" are different answers,
and reading one as the other is how a listener ends up open.

`ALTIPLANO_HTTP_ALLOW_UNAUTHENTICATED=1` turns the gate off for development. It is
refused on any bind address other than loopback. Even on loopback it is the wrong
setting behind a proxy or a tunnel, where the bind address describes this machine
and says nothing about who is calling.

### Why the gate is ASGI middleware

The SDK offers `token_verifier`, and it refuses to accept one without
`AuthSettings`. `AuthSettings` in turn requires `issuer_url` and
`resource_server_url`, and switching it on makes the SDK publish
`/.well-known/oauth-protected-resource` and wrap the endpoint in
`RequireAuthMiddleware`, advertising an OAuth authorisation server that does not
exist.

`ServerMiddleware` is the other hook the SDK provides. It sits inside the MCP
dispatcher with `ctx.method` and `ctx.params`, sees no HTTP headers, and rejects
with an MCP error where a 401 belongs.

So the check wraps the ASGI app instead. The `WWW-Authenticate` header below names
a realm and nothing else. A client may still probe the well-known metadata URLs on
its own initiative, and it will get a 404; what the bare challenge avoids is
Altiplano pointing it at an issuer nobody runs. Clients configured to send the
header directly are the supported path.

The wrapper is written against the ASGI interface directly, with no Starlette
import, which keeps `uvicorn` the only dependency this transport adds.
"""

import argparse
import ipaddress
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from typing import Any

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings

from altiplano import __version__
from altiplano.clients import _CLIENTS_FILE, _clients, _identify, _labels
from altiplano.server import mcp

_LOG = logging.getLogger("altiplano.http")

_LOCAL_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")
_LOCAL_ORIGINS = ("http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*")
_TRUE = frozenset({"1", "true", "yes", "on"})

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


def _unauthenticated_requested() -> bool:
    return os.environ.get("ALTIPLANO_HTTP_ALLOW_UNAUTHENTICATED", "").lower() in _TRUE


def _is_loopback(host: str) -> bool:
    """Whether binding to `host` keeps the listener on this machine.

    `ipaddress` covers the whole of `127.0.0.0/8`. A hostname other than `localhost`
    reads as remote, and an unrecognised bind address therefore requires client
    tokens. Failing that way round is the safe one.
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
                # A realm and nothing more. A `resource_metadata` parameter here
                # would point a client at OAuth metadata this server does not serve.
                (b"www-authenticate", b'Bearer realm="altiplano"'),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class _RequireClientToken:
    """Reject any HTTP request whose bearer token matches no registered client.

    The store is consulted per request. A token added to a running server works
    immediately, and a revoked one stops working immediately.

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


def _check_policy() -> bool:
    """Settle the authentication policy before a socket is opened.

    Returns True when the gate goes on. Raises when the configuration asks for
    something that would publish every write and delete tool.
    """
    host = _host()
    # An unreadable store is fatal at startup whatever else is configured. Coming up
    # while unable to read who is authorised is the failure worth being loud about.
    try:
        registered = _labels()
    except Exception as err:
        raise RuntimeError(
            f"{err}. The client store decides who may call, and starting without it "
            "would serve every caller alike. Fix the file's permissions, or move it "
            "aside to start with no clients registered."
        ) from err

    if _unauthenticated_requested():
        if not _is_loopback(host):
            raise RuntimeError(
                f"ALTIPLANO_HTTP_ALLOW_UNAUTHENTICATED is set and the bind address is "
                f"{host}. That combination serves every caller with no token, on a "
                "reachable interface. Unset it, or bind 127.0.0.1."
            )
        _LOG.warning(
            "ALTIPLANO_HTTP_ALLOW_UNAUTHENTICATED is set. Every request on %s is "
            "served with no token. Do not use this behind a proxy or a tunnel, where "
            "the bind address says nothing about who is calling.",
            host,
        )
        return False

    if not registered:
        # The gate is on either way. An empty store denies every request and exposes
        # nothing. Refusing here catches the operator who forgot to mint a key, at
        # the moment they can still act on it. On loopback a warning is enough: that
        # is where a fresh checkout gets tried out.
        if not _is_loopback(host):
            raise RuntimeError(
                f"no client tokens in {_CLIENTS_FILE} and the bind address is {host}. "
                "Every request would be refused. Register a client first with: "
                "altiplano-clientkey add <label>"
            )
        _LOG.warning(
            "no client tokens in %s. Every request will be refused. Register one "
            "with: altiplano-clientkey add <label>",
            _CLIENTS_FILE,
        )
    return True


def build_app(*, gated: bool = True) -> Any:
    """The ASGI application, wrapped in the token gate unless told otherwise.

    `gated` comes from `_check_policy`. It is a parameter so the policy is decided
    once, in one place, and cannot be re-derived from the store by accident.
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
    return _RequireClientToken(app) if gated else app


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="altiplano-http",
        description=(
            "Serve Altiplano's MCP tools over Streamable HTTP. Configured through "
            "the environment: ALTIPLANO_HTTP_HOST, ALTIPLANO_HTTP_PORT, "
            "ALTIPLANO_HTTP_PATH, ALTIPLANO_HTTP_ALLOWED_HOSTS, "
            "ALTIPLANO_HTTP_ALLOWED_ORIGINS, ALTIPLANO_CLIENTS, and "
            "ALTIPLANO_HTTP_ALLOW_UNAUTHENTICATED."
        ),
    )
    parser.add_argument("--version", action="version", version=f"altiplano {__version__}")
    parser.add_argument(
        "--check",
        action="store_true",
        help="report the resolved settings and the client count, then exit",
    )
    return parser


def _check_report() -> int:
    """Print what the server would do, without opening a socket."""
    try:
        gated = _check_policy()
        registered = _clients()
    except RuntimeError as err:
        print(f"altiplano-http: {err}", file=sys.stderr)
        return 1
    print(f"version:       {__version__}")
    print(f"bind:          {_host()}:{_port()}{_path()}")
    print(f"allowed hosts: {', '.join(_list_setting('ALTIPLANO_HTTP_ALLOWED_HOSTS', _LOCAL_HOSTS))}")
    print(f"client store:  {_CLIENTS_FILE}")
    print(f"clients:       {len(registered)}")
    print(f"authenticated: {'yes' if gated else 'NO'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    args = _parser().parse_args(argv)
    if args.check:
        return _check_report()
    gated = _check_policy()
    host, port = _host(), _port()
    _LOG.info(
        "serving %d client(s) on %s:%d%s, authentication %s",
        len(_clients()),
        host,
        port,
        _path(),
        "on" if gated else "OFF",
    )
    uvicorn.run(build_app(gated=gated), host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
