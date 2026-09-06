"""`altiplano-clientkey`: the three subcommands, and what they print.

The printed client token is the only copy that will ever exist. The assertions cover
what reaches stdout as much as what reaches the store.

`add` also collects the Vikunja API token the client acts with. Most tests here stub
that prompt; the ones under "reading the Vikunja token" cover the prompt itself.
"""

import io

import pytest

from altiplano import clientkey, clients

VIKUNJA = "tk_" + "1" * 32

# Captured before the autouse fixture below stubs it out. The two tests covering the
# prompt itself call this alias and reach the real function.
read_vikunja_token = clientkey._read_vikunja_token


@pytest.fixture(autouse=True)
def _forget_module_state():
    clients._file_cache = None


@pytest.fixture(autouse=True)
def _stub_prompt(monkeypatch):
    """Answer the Vikunja token prompt. A test can then drive `add` with no terminal."""
    monkeypatch.setattr(clientkey, "_read_vikunja_token", lambda label: VIKUNJA)


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "clients"
    monkeypatch.setattr(clients, "_CLIENTS_FILE", path)
    monkeypatch.setattr(clientkey, "_CLIENTS_FILE", path)
    return path


def label_of(token):
    client = clients._resolve(token)
    return client.label if client else None


def test_add_prints_a_usable_token_once(store, capsys):
    assert clientkey.main(["add", "laptop"]) == 0

    printed = capsys.readouterr().out
    token = next(word for word in printed.split() if word.startswith("altp_"))
    assert label_of(token) == "laptop"
    assert "Shown once" in printed
    assert "Bearer" in printed


def test_add_never_prints_the_vikunja_token_back(store, capsys):
    """It came from a hidden prompt. Echoing it would defeat that."""
    clientkey.main(["add", "laptop"])
    assert VIKUNJA not in capsys.readouterr().out


def test_add_says_whose_vikunja_identity_the_client_gets(store, capsys):
    clientkey.main(["add", "laptop"])
    assert "act as the Vikunja user" in capsys.readouterr().out


def test_add_records_the_client_and_its_vikunja_token(store):
    clientkey.main(["add", "laptop"])
    assert clients._labels() == ("laptop",)
    assert clients._clients()[0].vikunja_token == VIKUNJA


def test_add_refuses_a_duplicate_label_without_touching_the_store(store, capsys):
    clientkey.main(["add", "laptop"])
    before = store.read_text()

    assert clientkey.main(["add", "laptop"]) == 1
    assert "already exists" in capsys.readouterr().err
    assert store.read_text() == before


def test_add_refuses_a_label_that_would_corrupt_the_store(store, capsys):
    assert clientkey.main(["add", "has:colon"]) == 1
    assert "client label" in capsys.readouterr().err


def test_add_refuses_an_unusable_vikunja_token(store, capsys, monkeypatch):
    monkeypatch.setattr(clientkey, "_read_vikunja_token", lambda label: "")
    assert clientkey.main(["add", "laptop"]) == 1
    assert "Vikunja API token" in capsys.readouterr().err
    assert not store.exists()


def test_list_reports_an_empty_store(store, capsys):
    assert clientkey.main(["list"]) == 0
    assert "no clients registered" in capsys.readouterr().out


def test_list_shows_every_label_and_no_digest(store, capsys):
    clientkey.main(["add", "laptop"])
    clientkey.main(["add", "desktop"])
    capsys.readouterr()

    assert clientkey.main(["list"]) == 0
    printed = capsys.readouterr().out
    assert "laptop" in printed
    assert "desktop" in printed
    for client in clients._clients():
        assert client.digest not in printed


def test_list_never_shows_a_vikunja_token(store, capsys):
    """The column reports whether there is one. Printing it would put it on screen."""
    clientkey.main(["add", "laptop"])
    capsys.readouterr()

    clientkey.main(["list"])
    printed = capsys.readouterr().out
    assert VIKUNJA not in printed
    assert "VIKUNJA" in printed
    assert "yes" in printed


def test_list_flags_a_client_with_no_vikunja_token(store, capsys):
    """What a client carried over from before per-client tokens looks like."""
    store.write_text(f"{clients._HEADER}\nlaptop:{'a' * 64}::2026-09-05T00:00:00Z\n")
    store.chmod(0o600)

    assert clientkey.main(["list"]) == 0
    printed = capsys.readouterr().out
    assert "MISSING" in printed
    assert "refused on every request" in printed


def test_revoke_removes_the_client(store, capsys):
    clientkey.main(["add", "laptop"])
    capsys.readouterr()

    assert clientkey.main(["revoke", "laptop"]) == 0
    assert "revoked laptop" in capsys.readouterr().out
    assert clients._labels() == ()


def test_revoking_something_absent_exits_non_zero(store, capsys):
    assert clientkey.main(["revoke", "ghost"]) == 1
    assert "no client named" in capsys.readouterr().err


def test_a_platform_without_locking_is_reported_rather_than_risked(store, capsys, monkeypatch):
    monkeypatch.setattr(clients, "fcntl", None)
    assert clientkey.main(["add", "laptop"]) == 1
    assert "POSIX file locking" in capsys.readouterr().err
    assert not store.exists()


# --- reading the Vikunja token ----------------------------------------------
def test_a_piped_token_is_read_from_stdin(monkeypatch):
    """How a configuration-management run would call this."""
    monkeypatch.setattr(clientkey.sys, "stdin", io.StringIO(f"  {VIKUNJA}  \n"))
    assert read_vikunja_token("laptop") == VIKUNJA


def test_a_terminal_gets_a_hidden_prompt(monkeypatch):
    """`getpass` keeps the token off the screen, and off the shell history."""
    asked = []

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(clientkey.sys, "stdin", Terminal())
    monkeypatch.setattr(
        clientkey.getpass, "getpass", lambda prompt: (asked.append(prompt), f" {VIKUNJA}\n")[1]
    )

    assert read_vikunja_token("laptop") == VIKUNJA
    assert "laptop" in asked[0]


def test_version_reports_the_package_version(capsys):
    from altiplano import __version__

    with pytest.raises(SystemExit) as caught:
        clientkey.main(["--version"])
    assert caught.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_subcommand_is_an_error(store):
    """argparse raises SystemExit here. This pins that path."""
    with pytest.raises(SystemExit) as caught:
        clientkey.main([])
    assert caught.value.code != 0
