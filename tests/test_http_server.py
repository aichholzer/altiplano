"""The HTTP transport: settings, the startup refusal, and the token gate.

The middleware is exercised through the ASGI interface directly, with a recording
`send`. No socket is opened and no server is started. These stay as fast as the
rest of the suite.
"""

import asyncio

import pytest

from altiplano import clients, http_server


@pytest.fixture(autouse=True)
def _forget_module_state():
    clients._file_cache = None


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "clients"
    monkeypatch.setattr(clients, "_CLIENTS_FILE", path)
    monkeypatch.setattr(http_server, "_CLIENTS_FILE", path)
    return path


def scope(headers=None, kind="http", method="POST", path="/mcp", client=("10.0.0.9", 51234)):
    return {
        "type": kind,
        "method": method,
        "path": path,
        "client": client,
        "headers": headers if headers is not None else [],
    }


class Recorder:
    """A `send` that keeps what the app wrote, and an app that records a pass."""

    def __init__(self):
        self.messages = []
        self.reached = False

    async def send(self, message):
        self.messages.append(message)

    async def app(self, scope, receive, send):
        self.reached = True

    @property
    def status(self):
        return self.messages[0]["status"] if self.messages else None

    @property
    def headers(self):
        return dict(self.messages[0]["headers"]) if self.messages else {}


def drive(app, request_scope, recorder):
    asyncio.run(app(request_scope, None, recorder.send))


# --- settings ---------------------------------------------------------------
def test_settings_fall_back_to_loopback_defaults(monkeypatch):
    for name in (
        "ALTIPLANO_HTTP_HOST",
        "ALTIPLANO_HTTP_PORT",
        "ALTIPLANO_HTTP_PATH",
        "ALTIPLANO_HTTP_ALLOWED_HOSTS",
        "ALTIPLANO_HTTP_ALLOWED_ORIGINS",
    ):
        monkeypatch.delenv(name, raising=False)

    assert http_server._host() == "127.0.0.1"
    assert http_server._port() == 8000
    assert http_server._path() == "/mcp"
    assert http_server._list_setting("ALTIPLANO_HTTP_ALLOWED_HOSTS", ("a", "b")) == ["a", "b"]


def test_a_list_setting_is_split_and_trimmed(monkeypatch):
    monkeypatch.setenv("ALTIPLANO_HTTP_ALLOWED_HOSTS", " one , two ,, three ")
    assert http_server._list_setting("ALTIPLANO_HTTP_ALLOWED_HOSTS", ()) == [
        "one",
        "two",
        "three",
    ]


def test_an_empty_list_setting_is_refused(monkeypatch):
    """An allowlist set to nothing is a typo, and treating it as the default would
    quietly widen or narrow what the server accepts."""
    monkeypatch.setenv("ALTIPLANO_HTTP_ALLOWED_HOSTS", " , ")
    with pytest.raises(RuntimeError, match="at least one"):
        http_server._list_setting("ALTIPLANO_HTTP_ALLOWED_HOSTS", ())


@pytest.mark.parametrize("value", ["not-a-number", "0", "65536", "-1"])
def test_a_bad_port_is_refused(monkeypatch, value):
    monkeypatch.setenv("ALTIPLANO_HTTP_PORT", value)
    with pytest.raises(RuntimeError, match="ALTIPLANO_HTTP_PORT"):
        http_server._port()


def test_a_path_without_a_leading_slash_is_refused(monkeypatch):
    monkeypatch.setenv("ALTIPLANO_HTTP_PATH", "mcp")
    with pytest.raises(RuntimeError, match="must begin"):
        http_server._path()


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("[::1]", True),
        # The whole of 127.0.0.0/8 is loopback, which a plain string match misses.
        ("127.0.0.53", True),
        ("0.0.0.0", False),
        ("192.168.1.50", False),
        ("altiplano.home.arpa", False),
    ],
)
def test_loopback_detection(host, expected):
    assert http_server._is_loopback(host) is expected


# --- the startup refusal ----------------------------------------------------
def test_binding_beyond_loopback_with_no_clients_refuses_to_start(store, monkeypatch):
    """The whole point of the check. Forgetting to register a client would
    otherwise publish every write and delete tool to anyone who can reach the port.
    """
    monkeypatch.setenv("ALTIPLANO_HTTP_HOST", "0.0.0.0")
    started = []
    monkeypatch.setattr(http_server.uvicorn, "run", lambda *a, **k: started.append(True))

    with pytest.raises(RuntimeError, match="refusing to bind"):
        http_server.main()
    assert started == []


def test_binding_beyond_loopback_with_a_client_starts(store, monkeypatch):
    clients._add("laptop")
    monkeypatch.setenv("ALTIPLANO_HTTP_HOST", "0.0.0.0")
    monkeypatch.setenv("ALTIPLANO_HTTP_PORT", "8123")

    observed = {}

    def fake_run(app, **kwargs):
        observed["app"] = app
        observed.update(kwargs)

    monkeypatch.setattr(http_server.uvicorn, "run", fake_run)
    http_server.main()

    assert observed["host"] == "0.0.0.0"
    assert observed["port"] == 8123
    assert isinstance(observed["app"], http_server._RequireClientToken)


def test_loopback_with_no_clients_starts_open(store, monkeypatch):
    """A fresh checkout has to be smoke-testable before a key exists."""
    monkeypatch.setenv("ALTIPLANO_HTTP_HOST", "127.0.0.1")
    observed = {}
    monkeypatch.setattr(
        http_server.uvicorn, "run", lambda app, **kw: observed.update(app=app, **kw)
    )
    http_server.main()
    assert not isinstance(observed["app"], http_server._RequireClientToken)


def test_the_app_is_gated_once_a_client_exists(store, monkeypatch):
    monkeypatch.setenv("ALTIPLANO_HTTP_HOST", "127.0.0.1")
    assert not isinstance(http_server.build_app(), http_server._RequireClientToken)
    clients._add("laptop")
    assert isinstance(http_server.build_app(), http_server._RequireClientToken)


def test_the_endpoint_path_reaches_the_built_app(store, monkeypatch):
    monkeypatch.setenv("ALTIPLANO_HTTP_PATH", "/altiplano")
    app = http_server.build_app()
    assert any(getattr(route, "path", None) == "/altiplano" for route in app.routes)


# --- extracting the bearer token --------------------------------------------
@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ([(b"authorization", b"Bearer altp_abc")], "altp_abc"),
        ([(b"authorization", b"bearer altp_abc")], "altp_abc"),
        ([(b"authorization", b"BEARER altp_abc")], "altp_abc"),
        ([(b"authorization", b"Bearer   altp_abc  ")], "altp_abc"),
        ([(b"content-type", b"application/json")], None),
        ([], None),
        ([(b"authorization", b"Basic dXNlcjpwYXNz")], None),
        ([(b"authorization", b"altp_abc")], None),
        ([(b"authorization", b"Bearer")], None),
        ([(b"authorization", b"Bearer   ")], None),
    ],
    ids=[
        "bearer",
        "lowercase scheme",
        "uppercase scheme",
        "extra whitespace",
        "other header",
        "no headers",
        "basic auth",
        "no scheme",
        "scheme only",
        "empty token",
    ],
)
def test_bearer_extraction(headers, expected):
    assert http_server._bearer(scope(headers)) == expected


# --- the gate ---------------------------------------------------------------
def test_a_valid_token_reaches_the_app(store):
    token = clients._add("laptop")
    recorder = Recorder()
    gate = http_server._RequireClientToken(recorder.app)

    drive(gate, scope([(b"authorization", f"Bearer {token}".encode())]), recorder)

    assert recorder.reached is True
    assert recorder.messages == []


def test_a_request_with_no_token_gets_401(store):
    clients._add("laptop")
    recorder = Recorder()
    gate = http_server._RequireClientToken(recorder.app)

    drive(gate, scope(), recorder)

    assert recorder.reached is False
    assert recorder.status == 401


def test_an_unissued_token_gets_401(store):
    clients._add("laptop")
    recorder = Recorder()
    gate = http_server._RequireClientToken(recorder.app)

    drive(gate, scope([(b"authorization", b"Bearer altp_neverissued")]), recorder)

    assert recorder.reached is False
    assert recorder.status == 401


def test_a_revoked_token_gets_401(store):
    """Revocation has to bite without a restart. The store expires on its own
    mtime, which is what makes that possible."""
    token = clients._add("laptop")
    recorder = Recorder()
    gate = http_server._RequireClientToken(recorder.app)
    request = scope([(b"authorization", f"Bearer {token}".encode())])

    drive(gate, request, recorder)
    assert recorder.reached is True

    clients._remove("laptop")
    after = Recorder()
    drive(http_server._RequireClientToken(after.app), request, after)

    assert after.reached is False
    assert after.status == 401


def test_the_401_names_a_realm_and_advertises_no_oauth_metadata(store):
    """A `resource_metadata` parameter here is what would send a compliant client
    hunting for an OAuth authorisation server that does not exist."""
    clients._add("laptop")
    recorder = Recorder()

    drive(http_server._RequireClientToken(recorder.app), scope(), recorder)

    challenge = recorder.headers[b"www-authenticate"]
    assert challenge == b'Bearer realm="altiplano"'
    assert b"resource_metadata" not in challenge
    assert recorder.headers[b"content-type"] == b"application/json"


def test_the_401_body_matches_its_content_length(store):
    clients._add("laptop")
    recorder = Recorder()

    drive(http_server._RequireClientToken(recorder.app), scope(), recorder)

    body = recorder.messages[1]["body"]
    assert int(recorder.headers[b"content-length"]) == len(body)


def test_a_non_http_scope_passes_straight_through(store):
    """The lifespan scope starts the MCP session manager. Swallowing it would leave
    the server unable to answer anything, and no test on the auth path would see it.
    """
    clients._add("laptop")
    recorder = Recorder()
    gate = http_server._RequireClientToken(recorder.app)

    drive(gate, scope(kind="lifespan", headers=[]), recorder)

    assert recorder.reached is True
    assert recorder.messages == []


def test_a_websocket_scope_passes_straight_through(store):
    clients._add("laptop")
    recorder = Recorder()
    gate = http_server._RequireClientToken(recorder.app)

    drive(gate, scope(kind="websocket"), recorder)

    assert recorder.reached is True


def test_the_matched_label_is_logged(store, caplog):
    """Per-client identity in the log is the reason these tokens are per client."""
    token = clients._add("laptop")
    recorder = Recorder()

    with caplog.at_level("INFO", logger="altiplano.http"):
        drive(
            http_server._RequireClientToken(recorder.app),
            scope([(b"authorization", f"Bearer {token}".encode())]),
            recorder,
        )

    assert "as laptop" in caplog.text
    assert token not in caplog.text


def test_a_rejection_logs_the_peer_and_never_the_token(store, caplog):
    clients._add("laptop")
    recorder = Recorder()

    with caplog.at_level("WARNING", logger="altiplano.http"):
        drive(
            http_server._RequireClientToken(recorder.app),
            scope([(b"authorization", b"Bearer altp_secretguess")]),
            recorder,
        )

    assert "10.0.0.9" in caplog.text
    assert "altp_secretguess" not in caplog.text


def test_a_peer_the_server_cannot_see_is_reported_as_unknown():
    assert http_server._peer(scope(client=None)) == "unknown"
