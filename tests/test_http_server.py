"""The HTTP transport: settings, the authentication policy, and the token gate.

The middleware is exercised through the ASGI interface directly, with a recording
`send`. No socket is opened and no server is started. These stay as fast as the
rest of the suite.

The authentication policy section is the one that matters. An earlier version chose
whether to authenticate by looking at the client store, and an empty or unreadable
store therefore served every caller. Those tests hold one application instance
across a change to the store, which is what a test calling `build_app()` twice
cannot see.
"""

import asyncio

import pytest

from altiplano import clients, config, http_server

DIGEST = "a" * 64

# The Vikunja API token a registered client acts with. Every record needs one, and a
# client whose record has none is refused.
VIKUNJA = "tk_" + "1" * 32


@pytest.fixture(autouse=True)
def _forget_module_state(monkeypatch):
    clients._file_cache = None
    config._warned_about.clear()
    monkeypatch.delenv("ALTIPLANO_HTTP_ALLOW_UNAUTHENTICATED", raising=False)


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


def bearer(token):
    return [(b"authorization", f"Bearer {token}".encode())]


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


def drive(app, request_scope, recorder=None):
    recorder = recorder or Recorder()
    asyncio.run(app(request_scope, None, recorder.send))
    return recorder


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
     ("0", False), ("false", False), ("", False), ("maybe", False)],
)
def test_the_opt_out_reads_only_explicit_affirmatives(monkeypatch, value, expected):
    monkeypatch.setenv("ALTIPLANO_HTTP_ALLOW_UNAUTHENTICATED", value)
    assert http_server._unauthenticated_requested() is expected


# --- the authentication policy ----------------------------------------------
# An empty store means "nobody is authorised". It never means "authorise everybody".
def test_an_empty_store_still_authenticates_on_loopback(store, monkeypatch, caplog):
    monkeypatch.setenv("ALTIPLANO_HTTP_HOST", "127.0.0.1")
    with caplog.at_level("WARNING", logger="altiplano.http"):
        assert http_server._check_policy() is True
    assert "Every request will be refused" in caplog.text


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.50"])
def test_an_empty_store_refuses_to_start_off_loopback(store, monkeypatch, host):
    """The gate denies every request either way. Refusing here catches the operator
    who forgot to mint a key, while they can still act on it."""
    monkeypatch.setenv("ALTIPLANO_HTTP_HOST", host)
    with pytest.raises(RuntimeError, match="no client tokens"):
        http_server._check_policy()


def test_an_empty_store_refuses_every_request(store):
    """The gate goes on with no clients registered, and denies."""
    app = http_server.build_app(gated=True)
    gate = http_server._RequireClientToken(Recorder().app)
    assert isinstance(app, http_server._RequireClientToken)

    recorder = drive(gate, scope(bearer("altp_anything")))
    assert recorder.reached is False
    assert recorder.status == 401


def test_adding_the_first_key_takes_effect_on_the_running_app(store):
    """The regression test for the policy defect.

    One application instance, built while the store was empty, has to start
    accepting the first key without a rebuild. Calling `build_app()` again would
    hide exactly the bug this covers.
    """
    app = http_server.build_app(gated=True)

    before = drive(app, scope(bearer("altp_notyetminted")))
    assert before.status == 401

    token = clients._add("laptop", VIKUNJA)
    clients._file_cache = None

    after = Recorder()
    app._app = after.app  # stand in for the MCP app behind the gate
    drive(app, scope(bearer(token)), after)
    assert after.reached is True
    assert after.messages == []


def test_revoking_the_last_key_leaves_the_app_closed(store):
    """Revoking every client must deny, never fall open."""
    token = clients._add("laptop", VIKUNJA)
    app = http_server.build_app(gated=True)

    recorder = Recorder()
    app._app = recorder.app
    drive(app, scope(bearer(token)), recorder)
    assert recorder.reached is True

    clients._remove("laptop")
    after = Recorder()
    app._app = after.app
    drive(app, scope(bearer(token)), after)
    assert after.reached is False
    assert after.status == 401


def test_a_restart_with_an_empty_store_still_authenticates(store, monkeypatch):
    """The store is empty because the last key was revoked. A restart on loopback
    must not come up open."""
    monkeypatch.setenv("ALTIPLANO_HTTP_HOST", "127.0.0.1")
    clients._add("laptop", VIKUNJA)
    clients._remove("laptop")
    assert clients._labels() == ()

    assert http_server._check_policy() is True


@pytest.mark.skipif(
    __import__("os").geteuid() == 0, reason="root reads a file whatever its mode says"
)
def test_an_unreadable_store_refuses_to_start(store, monkeypatch):
    """"I cannot tell who is authorised" must never resolve to "serve everyone"."""
    monkeypatch.setenv("ALTIPLANO_HTTP_HOST", "127.0.0.1")
    clients._add("laptop", VIKUNJA)
    store.chmod(0o000)
    clients._file_cache = None
    try:
        with pytest.raises(RuntimeError, match="client store decides who may call"):
            http_server._check_policy()
    finally:
        store.chmod(0o600)


@pytest.mark.skipif(
    __import__("os").geteuid() == 0, reason="root reads a file whatever its mode says"
)
def test_an_unreadable_store_denies_at_request_time(store):
    import warnings

    token = clients._add("laptop", VIKUNJA)
    app = http_server.build_app(gated=True)
    store.chmod(0o000)
    clients._file_cache = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            recorder = drive(app, scope(bearer(token)))
        assert recorder.status == 401
    finally:
        store.chmod(0o600)


def test_the_opt_out_turns_the_gate_off_on_loopback(store, monkeypatch, caplog):
    monkeypatch.setenv("ALTIPLANO_HTTP_HOST", "127.0.0.1")
    monkeypatch.setenv("ALTIPLANO_HTTP_ALLOW_UNAUTHENTICATED", "1")
    with caplog.at_level("WARNING", logger="altiplano.http"):
        assert http_server._check_policy() is False
    assert "served with no token" in caplog.text
    assert not isinstance(http_server.build_app(gated=False), http_server._RequireClientToken)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.50", "altiplano.home.arpa"])
def test_the_opt_out_is_refused_off_loopback(store, monkeypatch, host):
    """The setting exists for a developer on their own machine. Anywhere reachable it
    would publish every write and delete tool."""
    monkeypatch.setenv("ALTIPLANO_HTTP_HOST", host)
    monkeypatch.setenv("ALTIPLANO_HTTP_ALLOW_UNAUTHENTICATED", "1")
    with pytest.raises(RuntimeError, match="ALLOW_UNAUTHENTICATED"):
        http_server._check_policy()


def test_main_refuses_to_serve_when_the_policy_is_refused(store, monkeypatch):
    monkeypatch.setenv("ALTIPLANO_HTTP_HOST", "0.0.0.0")
    monkeypatch.setenv("ALTIPLANO_HTTP_ALLOW_UNAUTHENTICATED", "1")
    started = []
    monkeypatch.setattr(http_server.uvicorn, "run", lambda *a, **k: started.append(True))
    with pytest.raises(RuntimeError):
        http_server.main([])
    assert started == []


def test_main_serves_a_gated_app(store, monkeypatch):
    clients._add("laptop", VIKUNJA)
    monkeypatch.setenv("ALTIPLANO_HTTP_HOST", "0.0.0.0")
    monkeypatch.setenv("ALTIPLANO_HTTP_PORT", "8123")
    observed = {}
    monkeypatch.setattr(
        http_server.uvicorn, "run", lambda app, **kw: observed.update(app=app, **kw)
    )

    assert http_server.main([]) == 0
    assert observed["host"] == "0.0.0.0"
    assert observed["port"] == 8123
    assert isinstance(observed["app"], http_server._RequireClientToken)


def test_the_endpoint_path_reaches_the_built_app(store, monkeypatch):
    monkeypatch.setenv("ALTIPLANO_HTTP_PATH", "/altiplano")
    app = http_server.build_app(gated=False)
    assert any(getattr(route, "path", None) == "/altiplano" for route in app.routes)


# --- the command line -------------------------------------------------------
def test_version_reports_the_package_version(capsys):
    from altiplano import __version__

    with pytest.raises(SystemExit) as caught:
        http_server.main(["--version"])
    assert caught.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_check_reports_the_settings_without_serving(store, monkeypatch, capsys):
    clients._add("laptop", VIKUNJA)
    monkeypatch.setenv("ALTIPLANO_HTTP_HOST", "127.0.0.1")
    started = []
    monkeypatch.setattr(http_server.uvicorn, "run", lambda *a, **k: started.append(True))

    assert http_server.main(["--check"]) == 0
    printed = capsys.readouterr().out
    assert "clients:       1" in printed
    assert "with a token:  1 of 1" in printed
    assert "authenticated: yes" in printed
    assert VIKUNJA not in printed, "a Vikunja token never reaches the terminal"
    assert started == []


def test_check_reports_a_refused_policy_without_raising(store, monkeypatch, capsys):
    monkeypatch.setenv("ALTIPLANO_HTTP_HOST", "0.0.0.0")
    monkeypatch.setenv("ALTIPLANO_HTTP_ALLOW_UNAUTHENTICATED", "1")
    assert http_server.main(["--check"]) == 1
    assert "ALLOW_UNAUTHENTICATED" in capsys.readouterr().err


def test_check_names_an_unauthenticated_listener_loudly(store, monkeypatch, capsys):
    monkeypatch.setenv("ALTIPLANO_HTTP_HOST", "127.0.0.1")
    monkeypatch.setenv("ALTIPLANO_HTTP_ALLOW_UNAUTHENTICATED", "1")
    assert http_server.main(["--check"]) == 0
    assert "authenticated: NO" in capsys.readouterr().out


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
        "bearer", "lowercase scheme", "uppercase scheme", "extra whitespace",
        "other header", "no headers", "basic auth", "no scheme", "scheme only",
        "empty token",
    ],
)
def test_bearer_extraction(headers, expected):
    assert http_server._bearer(scope(headers)) == expected


# --- the gate ---------------------------------------------------------------
def test_a_valid_token_reaches_the_app(store):
    token = clients._add("laptop", VIKUNJA)
    recorder = Recorder()
    drive(http_server._RequireClientToken(recorder.app), scope(bearer(token)), recorder)

    assert recorder.reached is True
    assert recorder.messages == []


@pytest.mark.parametrize(
    "headers",
    [None, [(b"authorization", b"Bearer altp_neverissued")]],
    ids=["no token", "unissued token"],
)
def test_a_request_without_a_known_token_gets_401(store, headers):
    clients._add("laptop", VIKUNJA)
    recorder = Recorder()
    drive(http_server._RequireClientToken(recorder.app), scope(headers), recorder)

    assert recorder.reached is False
    assert recorder.status == 401


def test_the_401_names_a_realm_and_advertises_no_oauth_metadata(store):
    """A `resource_metadata` parameter here would point a client at metadata this
    server does not serve."""
    clients._add("laptop", VIKUNJA)
    recorder = drive(http_server._RequireClientToken(Recorder().app), scope())

    challenge = recorder.headers[b"www-authenticate"]
    assert challenge == b'Bearer realm="altiplano"'
    assert b"resource_metadata" not in challenge
    assert recorder.headers[b"content-type"] == b"application/json"


def test_the_401_body_matches_its_content_length(store):
    clients._add("laptop", VIKUNJA)
    recorder = drive(http_server._RequireClientToken(Recorder().app), scope())
    assert int(recorder.headers[b"content-length"]) == len(recorder.messages[1]["body"])


@pytest.mark.parametrize("kind", ["lifespan", "websocket"])
def test_a_non_http_scope_passes_straight_through(store, kind):
    """The lifespan scope starts the MCP session manager. Swallowing it would leave
    the server unable to answer anything, and no test on the auth path would see it.
    """
    clients._add("laptop", VIKUNJA)
    recorder = Recorder()
    drive(http_server._RequireClientToken(recorder.app), scope(kind=kind), recorder)

    assert recorder.reached is True
    assert recorder.messages == []


def test_the_matched_label_is_logged(store, caplog):
    """Per-client identity in the log is the reason these tokens are per client."""
    token = clients._add("laptop", VIKUNJA)
    recorder = Recorder()

    with caplog.at_level("INFO", logger="altiplano.http"):
        drive(http_server._RequireClientToken(recorder.app), scope(bearer(token)), recorder)

    assert "as laptop" in caplog.text
    assert token not in caplog.text


def test_a_rejection_logs_the_peer_and_never_the_token(store, caplog):
    clients._add("laptop", VIKUNJA)
    with caplog.at_level("WARNING", logger="altiplano.http"):
        drive(
            http_server._RequireClientToken(Recorder().app),
            scope(bearer("altp_secretguess")),
        )

    assert "10.0.0.9" in caplog.text
    assert "altp_secretguess" not in caplog.text


def test_a_peer_the_server_cannot_see_is_reported_as_unknown():
    assert http_server._peer(scope(client=None)) == "unknown"


# --- binding the caller's Vikunja identity ----------------------------------
def v1_record(label, digest, created="2026-09-05T00:00:00Z"):
    """A record with no Vikunja token, as one carried over from an older store."""
    return f"{clients._HEADER}\n{label}:{digest}::{created}\n"


def test_the_callers_vikunja_token_is_bound_for_the_downstream_call(store):
    """The whole point. `config._headers()` picks this up with no argument passed."""
    token = clients._add("laptop", VIKUNJA)
    seen = {}

    async def app(scope, receive, send):
        seen["bound"] = config._REQUEST_TOKEN.get()
        seen["headers"] = config._headers()

    drive(http_server._RequireClientToken(app), scope(bearer(token)))

    assert seen["bound"] == VIKUNJA
    assert seen["headers"]["Authorization"] == f"Bearer {VIKUNJA}"


def test_the_binding_is_released_after_the_call(store):
    """A leaked binding would put the next caller on the previous caller's account."""
    token = clients._add("laptop", VIKUNJA)

    async def app(scope, receive, send):
        assert config._REQUEST_TOKEN.get() == VIKUNJA

    drive(http_server._RequireClientToken(app), scope(bearer(token)))
    assert config._REQUEST_TOKEN.get() is None


def test_two_callers_are_bound_to_their_own_tokens(store):
    """Two clients, one process, two Vikunja identities."""
    mine = clients._add("mine", "tk_" + "1" * 32)
    yours = clients._add("yours", "tk_" + "2" * 32)
    seen = []

    async def app(scope, receive, send):
        seen.append(config._REQUEST_TOKEN.get())

    gate = http_server._RequireClientToken(app)
    drive(gate, scope(bearer(mine)))
    drive(gate, scope(bearer(yours)))
    drive(gate, scope(bearer(mine)))

    assert seen == ["tk_" + "1" * 32, "tk_" + "2" * 32, "tk_" + "1" * 32]


def test_overlapping_calls_do_not_cross_identities(store):
    """The isolation guarantee, with the two requests genuinely interleaved.

    A `ContextVar` set in ASGI middleware is what carries the identity, and this is
    the test that says two concurrent callers each keep their own.
    """
    mine = clients._add("mine", "tk_" + "1" * 32)
    yours = clients._add("yours", "tk_" + "2" * 32)
    observed = {}

    async def app(scope, receive, send):
        who = scope["headers"][0][1]
        # Yield twice. The other request then runs between the read and the check.
        first = config._REQUEST_TOKEN.get()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        observed[who] = (first, config._REQUEST_TOKEN.get())

    gate = http_server._RequireClientToken(app)

    async def both():
        await asyncio.gather(
            gate(scope(bearer(mine)), None, None),
            gate(scope(bearer(yours)), None, None),
        )

    asyncio.run(both())

    assert observed[f"Bearer {mine}".encode()] == ("tk_" + "1" * 32, "tk_" + "1" * 32)
    assert observed[f"Bearer {yours}".encode()] == ("tk_" + "2" * 32, "tk_" + "2" * 32)


# --- a client with no Vikunja identity --------------------------------------
def test_a_client_with_no_vikunja_token_is_refused(store):
    """Strict. The server's own token is not a fallback for an HTTP caller."""
    token = clients._mint()
    store.write_text(v1_record("laptop", clients._digest(token)))
    store.chmod(0o600)

    recorder = Recorder()
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        drive(http_server._RequireClientToken(recorder.app), scope(bearer(token)), recorder)

    assert recorder.reached is False
    assert recorder.status == 403


def test_the_403_says_what_is_missing_and_matches_its_content_length(store):
    token = clients._mint()
    store.write_text(v1_record("laptop", clients._digest(token)))
    store.chmod(0o600)

    recorder = drive(http_server._RequireClientToken(Recorder().app), scope(bearer(token)))

    assert b"Vikunja identity" in recorder.messages[1]["body"]
    assert int(recorder.headers[b"content-length"]) == len(recorder.messages[1]["body"])
    assert b"www-authenticate" not in recorder.headers, "retrying the same token cannot help"


def test_the_403_is_logged_with_the_label_and_the_fix(store, caplog):
    token = clients._mint()
    store.write_text(v1_record("laptop", clients._digest(token)))
    store.chmod(0o600)

    with caplog.at_level("ERROR", logger="altiplano.http"):
        drive(http_server._RequireClientToken(Recorder().app), scope(bearer(token)))

    assert "laptop" in caplog.text
    assert "altiplano-clientkey add laptop" in caplog.text
    assert token not in caplog.text


def test_nothing_is_bound_when_the_caller_is_refused(store):
    """A refused request must not leave the server's own token bound behind it."""
    clients._add("laptop", VIKUNJA)
    drive(http_server._RequireClientToken(Recorder().app), scope(bearer("altp_unissued")))
    assert config._REQUEST_TOKEN.get() is None


def test_a_store_where_no_client_has_a_token_refuses_to_start_off_loopback(store, monkeypatch):
    """Every request would be refused, which looks like a server that answers nothing."""
    monkeypatch.setenv("ALTIPLANO_HTTP_HOST", "0.0.0.0")
    store.write_text(v1_record("laptop", DIGEST))
    store.chmod(0o600)

    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(RuntimeError, match="has a Vikunja token"):
            http_server._check_policy()


def test_a_store_where_no_client_has_a_token_only_warns_on_loopback(store, monkeypatch, caplog):
    monkeypatch.setenv("ALTIPLANO_HTTP_HOST", "127.0.0.1")
    store.write_text(v1_record("laptop", DIGEST))
    store.chmod(0o600)

    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with caplog.at_level("WARNING", logger="altiplano.http"):
            assert http_server._check_policy() is True

    assert "has a Vikunja token" in caplog.text


def test_one_client_with_a_token_is_enough_to_start(store, monkeypatch):
    """A part-migrated store serves the clients that are ready."""
    monkeypatch.setenv("ALTIPLANO_HTTP_HOST", "0.0.0.0")
    clients._add("ready", VIKUNJA)
    body = store.read_text()
    store.write_text(f"{body}stale:{DIGEST}::2026-09-05T00:00:00Z\n")
    store.chmod(0o600)
    clients._file_cache = None

    assert http_server._check_policy() is True


def test_check_counts_only_the_clients_with_a_token(store, monkeypatch, capsys):
    monkeypatch.setenv("ALTIPLANO_HTTP_HOST", "127.0.0.1")
    clients._add("ready", VIKUNJA)
    body = store.read_text()
    store.write_text(f"{body}stale:{DIGEST}::2026-09-05T00:00:00Z\n")
    store.chmod(0o600)
    clients._file_cache = None

    assert http_server.main(["--check"]) == 0
    assert "with a token:  1 of 2" in capsys.readouterr().out
