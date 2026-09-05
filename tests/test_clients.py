"""The client token store: parsing, hashing, identification, and revocation.

The store decides who may call the HTTP transport. The cases that matter are the
negative ones: a malformed line, an unreadable file, a revoked label, and a token
that was never issued.
"""

import os
import warnings

import pytest

from altiplano import clients, config


@pytest.fixture(autouse=True)
def _forget_module_state():
    """Clear the parse cache and the warn-once record between tests.

    The warn-once record lives in `config`, which is where `_warn_once` comes from.
    """
    clients._file_cache = None
    config._warned_about.clear()


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the module at a throwaway store and return its path."""
    path = tmp_path / "clients"
    monkeypatch.setattr(clients, "_CLIENTS_FILE", path)
    return path


def test_a_minted_token_carries_the_prefix_and_real_entropy():
    first, second = clients._mint(), clients._mint()
    assert first.startswith("altp_")
    assert first != second
    # 32 bytes base64url-encoded, plus the prefix.
    assert len(first) > 40


def test_the_digest_is_stable_and_is_not_the_token():
    token = clients._mint()
    assert clients._digest(token) == clients._digest(token)
    assert token not in clients._digest(token)
    assert len(clients._digest(token)) == 64


def test_a_missing_store_has_no_clients(store):
    assert clients._clients() == ()
    assert clients._identify("altp_anything") is None


def test_add_then_identify_round_trips(store):
    token = clients._add("laptop")
    assert clients._identify(token) == "laptop"
    assert clients._labels() == ("laptop",)


def test_the_store_holds_the_digest_and_never_the_token(store):
    token = clients._add("laptop")
    text = store.read_text()
    assert token not in text
    assert clients._digest(token) in text
    assert text.startswith("laptop:")


def test_a_second_client_is_appended(store):
    first = clients._add("laptop")
    second = clients._add("desktop")
    assert clients._identify(first) == "laptop"
    assert clients._identify(second) == "desktop"
    assert clients._labels() == ("laptop", "desktop")


def test_a_duplicate_label_is_refused(store):
    clients._add("laptop")
    with pytest.raises(ValueError, match="already exists"):
        clients._add("laptop")


@pytest.mark.parametrize(
    "label",
    ["", "has:colon", "#comment", " leading", "trailing "],
    ids=["empty", "colon", "hash", "leading space", "trailing space"],
)
def test_a_label_that_would_corrupt_the_store_is_refused(store, label):
    """A colon splits a record and a '#' starts a comment. Neither can be a label,
    and surrounding whitespace would not survive the read."""
    with pytest.raises(ValueError, match="client label"):
        clients._add(label)
    assert not store.exists()


def test_revoking_removes_only_that_client(store):
    laptop = clients._add("laptop")
    desktop = clients._add("desktop")

    assert clients._remove("laptop") is True
    assert clients._identify(laptop) is None
    assert clients._identify(desktop) == "desktop"


def test_revoking_something_absent_reports_it(store):
    clients._add("laptop")
    assert clients._remove("desktop") is False
    assert clients._labels() == ("laptop",)


def test_an_unissued_token_identifies_nobody(store):
    clients._add("laptop")
    assert clients._identify("altp_neverissued") is None


@pytest.mark.parametrize("token", [None, ""], ids=["none", "empty"])
def test_no_token_identifies_nobody(store, token):
    clients._add("laptop")
    assert clients._identify(token) is None


def test_blanks_comments_and_malformed_lines_are_skipped(store):
    """One corrupt line must not take every working client down with it."""
    store.write_text(
        "\n"
        "# a comment\n"
        "   \n"
        "no-colon-at-all\n"
        "laptop:aaaa:2026-09-05T00:00:00Z\n"
        ":missing-label\n"
        "nodigest:\n"
        "desktop:bbbb\n"
    )
    store.chmod(0o600)
    assert clients._labels() == ("laptop", "desktop")


def test_a_record_with_no_created_field_still_loads(store):
    store.write_text("laptop:aaaa\n")
    store.chmod(0o600)
    assert clients._clients()[0].created == ""


def test_the_store_is_read_once_per_change(store, monkeypatch):
    clients._add("laptop")
    clients._file_cache = None

    reads = []
    real_read = clients.Path.read_text

    def counting_read(self, *args, **kwargs):
        if self == store:
            reads.append(self)
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(clients.Path, "read_text", counting_read)

    clients._labels()
    clients._labels()
    assert len(reads) == 1

    # A revoked client has to be picked up without a restart. The write clears the
    # cache itself; this proves a change made behind its back is caught too.
    store.write_text("desktop:cccc:2026-09-05T00:00:00Z\n")
    bumped = store.stat().st_mtime_ns + 10**9
    os.utime(store, ns=(bumped, bumped))

    assert clients._labels() == ("desktop",)
    assert len(reads) == 2


def test_the_written_store_is_only_readable_by_its_owner(store):
    clients._add("laptop")
    assert store.stat().st_mode & 0o777 == 0o600


def test_no_temporary_file_is_left_behind(store, tmp_path):
    clients._add("laptop")
    assert [p.name for p in tmp_path.iterdir()] == ["clients"]


def test_the_parent_directory_is_created_when_absent(tmp_path, monkeypatch):
    nested = tmp_path / "config" / "altiplano" / "clients"
    monkeypatch.setattr(clients, "_CLIENTS_FILE", nested)
    clients._add("laptop")
    assert nested.exists()


def test_warns_when_others_can_read_the_store_but_still_reads_it(store):
    clients._add("laptop")
    store.chmod(0o644)
    clients._file_cache = None

    with pytest.warns(UserWarning, match="chmod 600") as caught:
        assert clients._labels() == ("laptop",)

    message = str(caught[0].message)
    assert "client tokens" in message
    assert "Vikunja API token" not in message


def test_does_not_warn_when_only_the_owner_can_read_the_store(store):
    clients._add("laptop")
    clients._file_cache = None

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert clients._labels() == ("laptop",)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a file whatever its mode says")
def test_an_unreadable_store_warns_and_reports_no_clients(store):
    """Reporting no clients is what makes the HTTP entry point refuse to serve. A
    store that cannot be read must never be waved through."""
    clients._add("laptop")
    store.chmod(0o000)
    clients._file_cache = None

    with pytest.warns(UserWarning, match="could not read"):
        assert clients._clients() == ()


def test_the_store_sits_beside_the_credentials_file():
    assert clients._CLIENTS_FILE.parent == config._CONFIG_FILE.parent
    assert clients._CLIENTS_FILE.name == "clients"
