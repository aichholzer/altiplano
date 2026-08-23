"""Credential resolution: environment first, then the per-device file."""

import os
import warnings

import httpx
import pytest

from altiplano import config, server
from altiplano.api import _request


@pytest.fixture(autouse=True)
def _forget_module_state():
    """Clear the warn-once record and the parse cache, so each test sees a fresh
    module rather than a neighbour's leftovers."""
    config._warned_about.clear()
    config._file_cache = None


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """Point the module at a throwaway config file and return a writer for it.

    Created at 0600 so it matches the posture the module documents. write_text
    truncates without touching the mode, so it stays 0600 for every test that
    does not deliberately loosen it.
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
    """The parse replaced a first-match scan, so it must not start preferring the
    last line instead."""
    config_file.write_text(
        "VIKUNJA_URL=https://first.test/api/v1\nVIKUNJA_URL=https://second.test/api/v1\n"
    )
    assert config._from_file("VIKUNJA_URL") == "https://first.test/api/v1"


def test_the_file_is_read_once_per_change_not_once_per_lookup(config_file, monkeypatch):
    """A single tool call resolves config three or four times, by way of _base,
    _headers and _version. That used to be a read and a parse each time."""
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

    # A rotated token still has to be picked up, which is why the cache is keyed on
    # the file rather than held for the life of the process. The replacement is the
    # same length as the original, so size cannot be what invalidates it, and mtime
    # is bumped explicitly rather than trusting the clock to have moved.
    config_file.write_text("VIKUNJA_URL=https://two.test/api/v1\n")
    bumped = config_file.stat().st_mtime_ns + 10**9
    os.utime(config_file, ns=(bumped, bumped))

    assert config._from_file("VIKUNJA_URL") == "https://two.test/api/v1"
    assert len(reads) == 2


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a file whatever its mode says")
def test_warns_once_and_carries_on_when_the_file_cannot_be_read(config_file):
    """An unreadable file used to escape as a raw OSError from inside _base.

    It warns rather than raises because the environment may already carry the
    credentials, in which case this file does not matter.
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
    # The whole point of the warning is the token. Leaking it into a log to say
    # so would be its own finding.
    assert "tk_secret" not in message


def test_does_not_warn_when_only_the_owner_can_read_the_file(config_file):
    config_file.write_text("VIKUNJA_API_TOKEN=tk_secret\n")
    config_file.chmod(0o600)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert config._from_file("VIKUNJA_API_TOKEN") == "tk_secret"


def test_warns_once_per_file_not_once_per_read(config_file):
    config_file.write_text("VIKUNJA_API_TOKEN=tk_secret\n")
    config_file.chmod(0o644)

    # "always" defeats Python's own per-message dedupe, so what this counts is
    # the module's warn-once guard rather than the warnings registry.
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
def test_an_error_carries_what_the_server_objected_to(api, run, payload, expected):
    api.returns(payload, status=404)
    with pytest.raises(httpx.HTTPStatusError) as caught:
        run(_request("GET", "/tasks/999"))
    message = str(caught.value)
    assert expected in message
    # The status line survives alongside the detail, and the type is unchanged, so
    # a caller can still branch on response.status_code.
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

    Worth pinning: a redirect is not a 4xx or a 5xx, so treating only those as
    failures would decode the redirect body as a result instead.
    """
    api.returns_raw(301, headers={"Location": "https://vikunja.test/api/v1/projects"})
    with pytest.raises(httpx.HTTPStatusError, match="redirected to"):
        run(_request("GET", "/projects"))


def test_main_starts_the_server(monkeypatch):
    started = []
    monkeypatch.setattr(server.mcp, "run", lambda: started.append(True))
    server.main()
    assert started == [True]
