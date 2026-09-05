"""`altiplano-clientkey`: the three subcommands, and what they print.

The printed token is the only copy that will ever exist. The assertions cover what
reaches stdout as much as what reaches the store.
"""

import pytest

from altiplano import clientkey, clients


@pytest.fixture(autouse=True)
def _forget_module_state():
    clients._file_cache = None


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "clients"
    monkeypatch.setattr(clients, "_CLIENTS_FILE", path)
    monkeypatch.setattr(clientkey, "_CLIENTS_FILE", path)
    return path


def test_add_prints_a_usable_token_once(store, capsys):
    assert clientkey.main(["add", "laptop"]) == 0

    printed = capsys.readouterr().out
    token = next(word for word in printed.split() if word.startswith("altp_"))
    assert clients._identify(token) == "laptop"
    assert "Shown once" in printed
    assert "Bearer" in printed


def test_add_records_the_client_in_the_store(store):
    clientkey.main(["add", "laptop"])
    assert clients._labels() == ("laptop",)


def test_add_refuses_a_duplicate_label_without_touching_the_store(store, capsys):
    clientkey.main(["add", "laptop"])
    before = store.read_text()

    assert clientkey.main(["add", "laptop"]) == 1
    assert "already exists" in capsys.readouterr().err
    assert store.read_text() == before


def test_add_refuses_a_label_that_would_corrupt_the_store(store, capsys):
    assert clientkey.main(["add", "has:colon"]) == 1
    assert "client label" in capsys.readouterr().err


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
