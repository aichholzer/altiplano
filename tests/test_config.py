"""Credential resolution: environment first, then the per-device file."""

import warnings

import httpx
import pytest

from altiplano import server


@pytest.fixture(autouse=True)
def _forget_mode_warnings():
    """Clear the warn-once record so each test sees a fresh module."""
    server._warned_about_mode.clear()


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """Point the module at a throwaway config file and return a writer for it.

    Created at 0600 so it matches the posture the module documents. write_text
    truncates without touching the mode, so it stays 0600 for every test that
    does not deliberately loosen it.
    """
    path = tmp_path / "env"
    path.touch(mode=0o600)
    monkeypatch.setattr(server, "_CONFIG_FILE", path)
    return path


def test_reads_a_key_from_file(config_file):
    config_file.write_text("VIKUNJA_URL=https://from.file/api/v1\n")
    assert server._from_file("VIKUNJA_URL") == "https://from.file/api/v1"


def test_ignores_comments_blanks_and_lines_without_an_equals(config_file):
    config_file.write_text(
        "\n"
        "# VIKUNJA_URL=https://commented.out/api/v1\n"
        "   \n"
        "this line has no equals sign\n"
        "VIKUNJA_URL=https://real.test/api/v1\n"
    )
    assert server._from_file("VIKUNJA_URL") == "https://real.test/api/v1"


@pytest.mark.parametrize("raw", ['"quoted"', "'quoted'", "  quoted  "])
def test_strips_quotes_and_surrounding_whitespace(config_file, raw):
    config_file.write_text(f"TOKEN={raw}\n")
    assert server._from_file("TOKEN") == "quoted"


def test_returns_none_for_a_key_that_is_absent(config_file):
    config_file.write_text("OTHER=value\n")
    assert server._from_file("VIKUNJA_URL") is None


def test_returns_none_when_the_file_does_not_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_CONFIG_FILE", tmp_path / "nonexistent")
    assert server._from_file("VIKUNJA_URL") is None


def test_warns_when_others_can_read_the_file_but_still_reads_it(config_file):
    config_file.write_text("VIKUNJA_API_TOKEN=tk_secret\n")
    config_file.chmod(0o644)

    with pytest.warns(UserWarning, match="chmod 600") as caught:
        assert server._from_file("VIKUNJA_API_TOKEN") == "tk_secret"

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
        assert server._from_file("VIKUNJA_API_TOKEN") == "tk_secret"


def test_warns_once_per_file_not_once_per_read(config_file):
    config_file.write_text("VIKUNJA_API_TOKEN=tk_secret\n")
    config_file.chmod(0o644)

    # "always" defeats Python's own per-message dedupe, so what this counts is
    # the module's warn-once guard rather than the warnings registry.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        server._from_file("VIKUNJA_API_TOKEN")
        server._from_file("VIKUNJA_API_TOKEN")
        server._from_file("VIKUNJA_URL")

    assert len(caught) == 1


def test_environment_wins_over_the_file(config_file, monkeypatch):
    config_file.write_text("VIKUNJA_URL=https://from.file/api/v1\n")
    monkeypatch.setenv("VIKUNJA_URL", "https://from.env/api/v1")
    assert server._conf("VIKUNJA_URL") == "https://from.env/api/v1"


def test_falls_back_to_the_file_when_the_environment_is_unset(config_file, monkeypatch):
    config_file.write_text("VIKUNJA_URL=https://from.file/api/v1\n")
    monkeypatch.delenv("VIKUNJA_URL", raising=False)
    assert server._conf("VIKUNJA_URL") == "https://from.file/api/v1"


def test_base_strips_a_trailing_slash(monkeypatch):
    monkeypatch.setenv("VIKUNJA_URL", "https://vikunja.test/api/v1/")
    assert server._base() == "https://vikunja.test/api/v1"


def test_base_raises_when_the_url_is_unset(config_file, monkeypatch):
    monkeypatch.delenv("VIKUNJA_URL", raising=False)
    with pytest.raises(RuntimeError, match="VIKUNJA_URL is not set"):
        server._base()


def test_headers_carry_the_bearer_token(monkeypatch):
    monkeypatch.setenv("VIKUNJA_API_TOKEN", "tk_abc")
    assert server._headers() == {
        "Authorization": "Bearer tk_abc",
        "Content-Type": "application/json",
    }


def test_headers_raise_when_the_token_is_unset(config_file, monkeypatch):
    monkeypatch.delenv("VIKUNJA_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="VIKUNJA_API_TOKEN is not set"):
        server._headers()


def test_request_sends_the_token_and_resolves_against_the_base_url(api, run):
    run(server._request("GET", "/projects"))
    assert api.last.url == f"{server._base()}/projects"
    assert api.last.headers["Authorization"] == f"Bearer {server._conf('VIKUNJA_API_TOKEN')}"


def test_request_reports_ok_for_no_content(api, run):
    api.returns_raw(204)
    assert run(server._request("DELETE", "/tasks/1/labels/2")) == {"ok": True}


def test_request_reports_ok_for_an_empty_body(api, run):
    api.returns_raw(200)
    assert run(server._request("GET", "/projects")) == {"ok": True}


def test_request_decodes_a_json_body(api, run):
    api.returns({"id": 7})
    assert run(server._request("GET", "/tasks/7")) == {"id": 7}


def test_request_raises_on_an_error_status(api, run):
    api.returns({"message": "not found"}, status=404)
    with pytest.raises(httpx.HTTPStatusError):
        run(server._request("GET", "/tasks/999"))


def test_main_starts_the_server(monkeypatch):
    started = []
    monkeypatch.setattr(server.mcp, "run", lambda: started.append(True))
    server.main()
    assert started == [True]
