"""Credential resolution: environment first, then the per-device file.

The Vikunja API token takes one source ahead of those two, bound per request by the
HTTP transport. The section at the end covers that.
"""

import asyncio
import os
import warnings

import httpx
import pytest

from altiplano import config, server
from altiplano.api import _request


@pytest.fixture(autouse=True)
def _forget_module_state():
    """Clear the warn-once record and the parse cache. Each test then sees a fresh
    module, free of a neighbour's leftovers."""
    config._warned_about.clear()
    config._file_cache = None


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """Point the module at a throwaway config file and return a writer for it.

    Created at 0600 so it matches the posture the module documents. write_text
    truncates without touching the mode. It stays 0600 for every test that does not
    deliberately loosen it.
    """
    path = tmp_path / "env"
    path.touch(mode=0o600)
    monkeypatch.setattr(config, "_CONFIG_FILE", path)
    return path


def test_reads_a_key_from_file(config_file):
    config_file.write_text("VIKUNJA_URL=https://from.file/api/v1\n")
    assert config._from_file("VIKUNJA_URL") == "https://from.file/api/v1"


def test_ignores_comments_blanks_and_lines_without_an_equals(config_file):
    config_file.write_text(
        "\n"
        "# VIKUNJA_URL=https://commented.out/api/v1\n"
        "   \n"
        "this line has no equals sign\n"
        "VIKUNJA_URL=https://real.test/api/v1\n"
    )
    assert config._from_file("VIKUNJA_URL") == "https://real.test/api/v1"


@pytest.mark.parametrize("raw", ['"quoted"', "'quoted'", "  quoted  "])
def test_strips_quotes_and_surrounding_whitespace(config_file, raw):
    config_file.write_text(f"TOKEN={raw}\n")
    assert config._from_file("TOKEN") == "quoted"


def test_returns_none_for_a_key_that_is_absent(config_file):
    config_file.write_text("OTHER=value\n")
    assert config._from_file("VIKUNJA_URL") is None


def test_returns_none_when_the_file_does_not_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_CONFIG_FILE", tmp_path / "nonexistent")
    assert config._from_file("VIKUNJA_URL") is None


def test_a_duplicated_key_keeps_the_first_occurrence(config_file):
    """The parse replaced a first-match scan. It has to keep preferring the first
    line."""
    config_file.write_text(
        "VIKUNJA_URL=https://first.test/api/v1\nVIKUNJA_URL=https://second.test/api/v1\n"
    )
    assert config._from_file("VIKUNJA_URL") == "https://first.test/api/v1"


def test_the_file_is_read_once_per_change(config_file, monkeypatch):
    """A single tool call resolves config three or four times, by way of _base,
    _headers, and _version. That used to be a read and a parse each time."""
    config_file.write_text("VIKUNJA_URL=https://one.test/api/v1\n")

    reads = []
    real_read = config.Path.read_text

    def counting_read(self, *args, **kwargs):
        if self == config_file:
            reads.append(self)
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(config.Path, "read_text", counting_read)

    assert config._from_file("VIKUNJA_URL") == "https://one.test/api/v1"
    assert config._from_file("VIKUNJA_API_TOKEN") is None
    assert config._from_file("VIKUNJA_URL") == "https://one.test/api/v1"
    assert len(reads) == 1

    # A rotated token still has to be picked up. The cache is keyed on the file and
    # expires with it. The replacement is the same length as the original, and size
    # cannot be what invalidates it. mtime is bumped explicitly: the clock may not
    # have moved.
    config_file.write_text("VIKUNJA_URL=https://two.test/api/v1\n")
    bumped = config_file.stat().st_mtime_ns + 10**9
    os.utime(config_file, ns=(bumped, bumped))

    assert config._from_file("VIKUNJA_URL") == "https://two.test/api/v1"
    assert len(reads) == 2


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a file whatever its mode says")
def test_warns_once_and_carries_on_when_the_file_cannot_be_read(config_file):
    """An unreadable file used to escape as a raw OSError from inside _base.

    It warns and carries on: the environment may already hold the credentials, in
    which case this file does not matter.
    """
    config_file.write_text("VIKUNJA_URL=https://from.file/api/v1\n")
    config_file.chmod(0o000)

    with pytest.warns(UserWarning, match="could not read"):
        assert config._from_file("VIKUNJA_URL") is None

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert config._from_file("VIKUNJA_URL") is None


def test_warns_when_others_can_read_the_file_but_still_reads_it(config_file):
    config_file.write_text("VIKUNJA_API_TOKEN=tk_secret\n")
    config_file.chmod(0o644)

    with pytest.warns(UserWarning, match="chmod 600") as caught:
        assert config._from_file("VIKUNJA_API_TOKEN") == "tk_secret"

    assert len(caught) == 1
    message = str(caught[0].message)
    assert str(config_file) in message
    assert "0644" in message
    # The warning exists to protect the token. Leaking it into a log to say so
    # would be its own finding.
    assert "tk_secret" not in message


def test_does_not_warn_when_only_the_owner_can_read_the_file(config_file):
    config_file.write_text("VIKUNJA_API_TOKEN=tk_secret\n")
    config_file.chmod(0o600)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert config._from_file("VIKUNJA_API_TOKEN") == "tk_secret"


def test_warns_once_per_file(config_file):
    config_file.write_text("VIKUNJA_API_TOKEN=tk_secret\n")
    config_file.chmod(0o644)

    # "always" defeats Python's own per-message dedupe. What this counts is the
    # module's own warn-once guard, with the warnings registry out of the way.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        config._from_file("VIKUNJA_API_TOKEN")
        config._from_file("VIKUNJA_API_TOKEN")
        config._from_file("VIKUNJA_URL")

    assert len(caught) == 1


def test_environment_wins_over_the_file(config_file, monkeypatch):
    config_file.write_text("VIKUNJA_URL=https://from.file/api/v1\n")
    monkeypatch.setenv("VIKUNJA_URL", "https://from.env/api/v1")
    assert config._conf("VIKUNJA_URL") == "https://from.env/api/v1"


def test_falls_back_to_the_file_when_the_environment_is_unset(config_file, monkeypatch):
    config_file.write_text("VIKUNJA_URL=https://from.file/api/v1\n")
    monkeypatch.delenv("VIKUNJA_URL", raising=False)
    assert config._conf("VIKUNJA_URL") == "https://from.file/api/v1"


def test_base_strips_a_trailing_slash(monkeypatch):
    monkeypatch.setenv("VIKUNJA_URL", "https://vikunja.test/api/v1/")
    assert config._base() == "https://vikunja.test/api/v1"


def test_base_raises_when_the_url_is_unset(config_file, monkeypatch):
    monkeypatch.delenv("VIKUNJA_URL", raising=False)
    with pytest.raises(RuntimeError, match="VIKUNJA_URL is not set"):
        config._base()


def test_headers_carry_the_bearer_token(monkeypatch):
    monkeypatch.setenv("VIKUNJA_API_TOKEN", "tk_abc")
    assert config._headers() == {
        "Authorization": "Bearer tk_abc",
        "Content-Type": "application/json",
    }


def test_headers_raise_when_the_token_is_unset(config_file, monkeypatch):
    monkeypatch.delenv("VIKUNJA_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="VIKUNJA_API_TOKEN is not set"):
        config._headers()


def test_request_sends_the_token_and_resolves_against_the_base_url(api, run):
    run(_request("GET", "/projects"))
    assert api.last.url == f"{config._base()}/projects"
    assert api.last.headers["Authorization"] == f"Bearer {config._conf('VIKUNJA_API_TOKEN')}"


def test_request_reports_ok_for_no_content(api, run):
    api.returns_raw(204)
    assert run(_request("DELETE", "/tasks/1/labels/2")) == {"ok": True}


def test_request_reports_ok_for_an_empty_body(api, run):
    api.returns_raw(200)
    assert run(_request("GET", "/projects")) == {"ok": True}


def test_request_decodes_a_json_body(api, run):
    api.returns({"id": 7})
    assert run(_request("GET", "/tasks/7")) == {"id": 7}


def test_request_raises_on_an_error_status(api, run):
    api.returns({"message": "not found"}, status=404)
    with pytest.raises(httpx.HTTPStatusError):
        run(_request("GET", "/tasks/999"))


# --- error messages ---------------------------------------------------------
# httpx's own message stops at the status code. Vikunja says why in the body, and
# an agent that cannot see the reason cannot correct the call: a rejected filter
# expression and a missing task are the same "400" without it.
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        # v1 puts the human-readable text in `message`.
        ({"message": "The task does not exist."}, "The task does not exist."),
        # v2 is RFC 9457 problem+json: `detail`, alongside Vikunja's numeric code.
        (
            {"title": "Not Found", "detail": "This task does not exist.", "code": 4002},
            "This task does not exist. (code 4002)",
        ),
        # Nothing but a title still beats a bare status code.
        ({"title": "Not Found"}, ": Not Found"),
    ],
    ids=["v1-message", "v2-problem-json", "title-only"],
)
def test_an_error_reports_what_the_server_objected_to(api, run, payload, expected):
    api.returns(payload, status=404)
    with pytest.raises(httpx.HTTPStatusError) as caught:
        run(_request("GET", "/tasks/999"))
    message = str(caught.value)
    assert expected in message
    # The status line survives alongside the detail, and the type is unchanged. A
    # caller can still branch on response.status_code.
    assert "404" in message
    assert caught.value.response.status_code == 404


@pytest.mark.parametrize(
    "body",
    [b"<html>502 Bad Gateway</html>", b'"a bare string"', b'{"unrecognised": true}', b""],
    ids=["html", "json-but-not-an-object", "object-explaining-nothing", "empty"],
)
def test_an_error_falls_back_to_the_status_line_when_the_body_explains_nothing(api, run, body):
    api.returns_raw(502, body)
    with pytest.raises(httpx.HTTPStatusError, match="502 Bad Gateway for GET"):
        run(_request("GET", "/projects"))


def test_a_redirect_is_an_error_and_names_where_it_was_sent(api, run):
    """Nearly always a VIKUNJA_URL missing its https or its /api/vN prefix.

    Worth pinning: a redirect is not a 4xx or a 5xx. Treating only those as failures
    would decode the redirect body as a result.
    """
    api.returns_raw(301, headers={"Location": "https://vikunja.test/api/v1/projects"})
    with pytest.raises(httpx.HTTPStatusError, match="redirected to"):
        run(_request("GET", "/projects"))


def test_main_starts_the_server(monkeypatch):
    started = []
    monkeypatch.setattr(server.mcp, "run", lambda: started.append(True))
    server.main()
    assert started == [True]


# --- the per-request Vikunja token ------------------------------------------
# The HTTP transport binds the calling client's token; stdio binds nothing. These
# cover `config`'s half of that. `tests/test_http_server.py` covers the gate.
def test_nothing_is_bound_by_default():
    assert config._REQUEST_TOKEN.get() is None


def test_the_bound_token_wins_over_the_environment(monkeypatch):
    """The environment holds the server's own token. A bound one is the caller's.

    Losing this order would put every HTTP client on the operator's Vikunja account.
    """
    monkeypatch.setenv("VIKUNJA_API_TOKEN", "tk_server")
    with config._acting_as("tk_caller"):
        assert config._headers()["Authorization"] == "Bearer tk_caller"


def test_the_environment_is_used_when_nothing_is_bound(monkeypatch):
    """stdio has no caller to resolve, and reads the environment as it always did."""
    monkeypatch.setenv("VIKUNJA_API_TOKEN", "tk_server")
    assert config._headers()["Authorization"] == "Bearer tk_server"


def test_the_binding_is_released_on_the_way_out(monkeypatch):
    monkeypatch.setenv("VIKUNJA_API_TOKEN", "tk_server")
    with config._acting_as("tk_caller"):
        pass
    assert config._REQUEST_TOKEN.get() is None
    assert config._headers()["Authorization"] == "Bearer tk_server"


def test_the_binding_is_released_when_the_body_raises(monkeypatch):
    """A failed request must not leave the next one on someone else's account."""
    monkeypatch.setenv("VIKUNJA_API_TOKEN", "tk_server")
    with pytest.raises(RuntimeError, match="boom"), config._acting_as("tk_caller"):
        raise RuntimeError("boom")
    assert config._REQUEST_TOKEN.get() is None


def test_bindings_nest_and_unwind_in_order(monkeypatch):
    monkeypatch.setenv("VIKUNJA_API_TOKEN", "tk_server")
    with config._acting_as("tk_outer"):
        with config._acting_as("tk_inner"):
            assert config._REQUEST_TOKEN.get() == "tk_inner"
        assert config._REQUEST_TOKEN.get() == "tk_outer"
    assert config._REQUEST_TOKEN.get() is None


def test_a_bound_token_needs_no_environment_or_file(monkeypatch, tmp_path):
    """An HTTP-only host can hold no server-wide Vikunja token at all."""
    monkeypatch.delenv("VIKUNJA_API_TOKEN", raising=False)
    monkeypatch.setattr(config, "_CONFIG_FILE", tmp_path / "absent")
    with config._acting_as("tk_caller"):
        assert config._headers()["Authorization"] == "Bearer tk_caller"


def test_the_url_has_no_per_request_override(monkeypatch):
    """One server, one Vikunja instance. `api._version()` reads the version off it."""
    monkeypatch.setenv("VIKUNJA_URL", "https://vikunja.example.com/api/v2")
    with config._acting_as("tk_caller"):
        assert config._base() == "https://vikunja.example.com/api/v2"


def test_concurrent_tasks_keep_their_own_bound_token(monkeypatch):
    """Two coroutines interleaving inside the binding, each seeing only its own."""
    monkeypatch.setenv("VIKUNJA_API_TOKEN", "tk_server")
    seen = {}

    async def call(name, token):
        with config._acting_as(token):
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            seen[name] = config._headers()["Authorization"]

    async def both():
        await asyncio.gather(call("mine", "tk_mine"), call("yours", "tk_yours"))

    asyncio.run(both())

    assert seen == {"mine": "Bearer tk_mine", "yours": "Bearer tk_yours"}


# --- the shape of VIKUNJA_URL -----------------------------------------------
# Presence alone let two unusable values reach the first request. Both passed
# `altiplano-http --check`, and the failure surfaced on the first tool call.
@pytest.mark.parametrize(
    "url",
    ["vikunja.home.arpa/api/v2", "//vikunja.home.arpa/api/v2", "/api/v2", "todo.example.com"],
    ids=["bare host", "protocol relative", "path only", "host only"],
)
def test_a_url_with_no_scheme_is_refused(monkeypatch, url):
    """httpx reads these as a relative path and joins them onto nothing."""
    monkeypatch.setenv("VIKUNJA_URL", url)
    with pytest.raises(RuntimeError, match="http:// or https://"):
        config._base()


@pytest.mark.parametrize(
    "url",
    ["https:///api/v2", "http:///api/v1"],
    ids=["https", "http"],
)
def test_a_url_with_no_host_is_refused(monkeypatch, url):
    monkeypatch.setenv("VIKUNJA_URL", url)
    with pytest.raises(RuntimeError, match="names no host"):
        config._base()


@pytest.mark.parametrize(
    "url",
    ["ftp://vikunja.example.com/api/v2", "file:///api/v2", "ws://vikunja.example.com/api/v2"],
    ids=["ftp", "file", "websocket"],
)
def test_a_scheme_other_than_http_is_refused(monkeypatch, url):
    monkeypatch.setenv("VIKUNJA_URL", url)
    with pytest.raises(RuntimeError, match="http:// or https://"):
        config._base()


@pytest.mark.parametrize(
    "url",
    [
        "https://vikunja.example.com/api/v2",
        "http://127.0.0.1:3456/api/v1",
        "https://vikunja.home.arpa/api/v2",
        "http://localhost/api/v1",
        "https://user:pass@vikunja.example.com/api/v2",
    ],
    ids=["https", "port", "lan name", "localhost", "userinfo"],
)
def test_a_usable_url_is_accepted(monkeypatch, url):
    monkeypatch.setenv("VIKUNJA_URL", url)
    assert config._base() == url


def test_a_trailing_slash_is_still_stripped(monkeypatch):
    """The version suffix is matched exactly, and a stray slash would defeat it."""
    monkeypatch.setenv("VIKUNJA_URL", "https://vikunja.example.com/api/v2/")
    assert config._base() == "https://vikunja.example.com/api/v2"


def test_the_error_says_where_the_version_suffix_goes(monkeypatch):
    """The likeliest reason somebody typed a bare hostname is not knowing about it."""
    monkeypatch.setenv("VIKUNJA_URL", "vikunja.home.arpa")
    with pytest.raises(RuntimeError, match="/api/v1 or /api/v2"):
        config._base()
