"""The client token store: parsing, hashing, identification, and revocation.

The store decides who may call the HTTP transport. The cases that matter are the
negative ones: a malformed line, an unreadable file, a revoked label, a token that
was never issued, and a label that tries to rewrite the record it lives in.
"""

import os
import subprocess
import sys
import textwrap
import warnings

import pytest

from altiplano import clients, config

DIGEST = "b" * 64


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


def write(path, text):
    """Write a store fixture at the mode the module would have written."""
    path.write_text(textwrap.dedent(text).lstrip())
    path.chmod(0o600)


# --- tokens and digests -----------------------------------------------------
def test_a_minted_token_carries_the_prefix_and_real_entropy():
    first, second = clients._mint(), clients._mint()
    assert first.startswith("altp_")
    assert first != second
    assert len(first) > 40


def test_the_digest_is_stable_and_is_not_the_token():
    token = clients._mint()
    assert clients._digest(token) == clients._digest(token)
    assert token not in clients._digest(token)
    assert clients._DIGEST.match(clients._digest(token))


# --- the happy path ---------------------------------------------------------
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


# --- label validation -------------------------------------------------------
# A label ends up in a colon-separated, newline-separated record. Anything that can
# introduce either separator can rewrite the file. The accepted set is narrow.
@pytest.mark.parametrize(
    "label",
    [
        "", "has:colon", "#comment", " leading", "trailing ",
        "work\nlaptop", "work\rlaptop", "work\r\nlaptop", "work\x0blaptop",
        "work\u2028laptop", "tab\tlaptop", "null\x00byte", "-leading-dash",
        ".leading-dot", "_leading-underscore", "a" * 65, "spaces here",
        "unicode\u00e9", "semi;colon",
    ],
    ids=[
        "empty", "colon", "hash", "leading space", "trailing space",
        "newline", "carriage return", "crlf", "vertical tab", "line separator",
        "tab", "null byte", "leading dash", "leading dot", "leading underscore",
        "too long", "inner space", "non-ascii", "semicolon",
    ],
)
def test_a_label_that_could_rewrite_the_record_is_refused(store, label):
    with pytest.raises(ValueError, match="client label"):
        clients._add(label)
    assert not store.exists()


def test_an_embedded_newline_cannot_smuggle_a_second_record(store):
    """A label carrying a newline used to store a record that parsed back under a
    different label, leaving a live token nobody could revoke by name."""
    with pytest.raises(ValueError):
        clients._add("work\nlaptop")
    assert clients._labels() == ()


@pytest.mark.parametrize(
    "label", ["laptop", "Laptop", "laptop-2", "laptop_2", "laptop.2", "a", "9lives", "a" * 64]
)
def test_a_reasonable_label_is_accepted(store, label):
    assert clients._add(label).startswith("altp_")
    assert clients._labels() == (label,)


# --- parsing ----------------------------------------------------------------
def test_blanks_comments_and_malformed_lines_are_skipped(store):
    """One corrupt line must not take every working client down with it."""
    write(
        store,
        f"""
        # a comment

        no-colon-at-all
        laptop:{"a" * 64}:2026-09-05T00:00:00Z
        :missing-label
        nodigest:
        has spaces:{"c" * 64}
        desktop:{"d" * 64}
        """,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert clients._labels() == ("laptop", "desktop")


@pytest.mark.parametrize(
    "digest",
    ["short", "b" * 63, "b" * 65, "g" * 64, "\u00e9" * 64, ""],
    ids=["short", "63 chars", "65 chars", "not hex", "non-ascii", "empty"],
)
def test_a_record_whose_digest_is_not_64_hex_characters_is_skipped(store, digest):
    """A non-ASCII digest used to make `hmac.compare_digest` raise, which locked out
    every client whose record came after it."""
    write(store, f"broken:{digest}\nlaptop:{'a' * 64}:2026-09-05T00:00:00Z\n")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert clients._labels() == ("laptop",)


def test_a_valid_client_authenticates_past_a_corrupt_record(store):
    """The point of skipping a bad line, proven end to end through `_identify`."""
    token = clients._add("laptop")
    body = store.read_text()
    write(store, f"broken:\u00e9\n{body}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert clients._identify(token) == "laptop"


def test_a_skipped_record_is_warned_about_without_naming_its_digest(store):
    write(store, f"broken:{'z' * 64}\n")
    with pytest.warns(UserWarning, match="was skipped") as caught:
        clients._clients()
    message = str(caught[0].message)
    assert "line 1" in message
    assert "z" * 64 not in message


def test_an_uppercase_digest_still_matches(store):
    """Hex is case-insensitive, and a hand-edited store may use either."""
    token = clients._mint()
    write(store, f"laptop:{clients._digest(token).upper()}:2026-09-05T00:00:00Z\n")
    assert clients._identify(token) == "laptop"


def test_a_record_with_no_created_field_still_loads(store):
    write(store, f"laptop:{'a' * 64}\n")
    assert clients._clients()[0].created == ""


# --- caching ----------------------------------------------------------------
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

    write(store, f"desktop:{'c' * 64}:2026-09-05T00:00:00Z\n")
    bumped = store.stat().st_mtime_ns + 10**9
    os.utime(store, ns=(bumped, bumped))

    assert clients._labels() == ("desktop",)
    assert len(reads) == 2


# --- writing ----------------------------------------------------------------
def test_the_written_store_is_only_readable_by_its_owner(store):
    clients._add("laptop")
    assert store.stat().st_mode & 0o777 == 0o600


def test_the_temporary_file_is_never_readable_by_others(store, monkeypatch):
    """`write_text` followed by `chmod` left the file at 0644 for an instant.
    `mkstemp` creates it at 0600."""
    seen = []
    real_replace = clients.os.replace

    def spy_replace(src, dst):
        seen.append(os.stat(src).st_mode & 0o777)
        return real_replace(src, dst)

    monkeypatch.setattr(clients.os, "replace", spy_replace)
    os.umask(0o022)
    clients._add("laptop")

    assert seen == [0o600]


def test_no_temporary_file_is_left_behind(store, tmp_path):
    clients._add("laptop")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["clients", "clients.lock"]


def test_a_failed_write_leaves_no_temporary_file(store, tmp_path, monkeypatch):
    monkeypatch.setattr(clients.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("nope")))
    with pytest.raises(OSError):
        clients._add("laptop")
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".clients-")] == []


def test_the_parent_directory_is_created_when_absent(tmp_path, monkeypatch):
    nested = tmp_path / "config" / "altiplano" / "clients"
    monkeypatch.setattr(clients, "_CLIENTS_FILE", nested)
    clients._add("laptop")
    assert nested.exists()


# --- permissions and readability --------------------------------------------
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
def test_an_unreadable_store_raises(store):
    """An unreadable store is not an empty one. Reporting it as empty is what let the
    HTTP transport come up with no authentication at all."""
    clients._add("laptop")
    store.chmod(0o000)
    clients._file_cache = None
    try:
        with pytest.raises(clients._StoreUnreadable, match="could not read"):
            clients._clients()
    finally:
        store.chmod(0o600)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a file whatever its mode says")
def test_an_unreadable_store_denies_rather_than_raising_at_identify(store):
    token = clients._add("laptop")
    store.chmod(0o000)
    clients._file_cache = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert clients._identify(token) is None
    finally:
        store.chmod(0o600)


def test_the_store_sits_beside_the_credentials_file():
    assert clients._CLIENTS_FILE.parent == config._CONFIG_FILE.parent
    assert clients._CLIENTS_FILE.name == "clients"


# --- concurrency ------------------------------------------------------------
CONCURRENT_ADD = """
import os, sys, time
from pathlib import Path
sys.path.insert(0, {src!r})
from altiplano import clients
clients._CLIENTS_FILE = Path({store!r})

real_write = clients._write
def slow_write(records):
    # Widen the read-to-write window the lock has to cover.
    time.sleep(1.5)
    return real_write(records)
clients._write = slow_write

clients._add("desktop")
"""


def test_an_overlapping_add_cannot_undo_a_completed_revocation(store, tmp_path):
    """Two processes, one lock.

    Without a lock across the whole read-modify-write, the add reads a snapshot
    containing `laptop`, the revoke completes, and the add writes its stale snapshot
    back. The revoked token returns.
    """
    token = clients._add("laptop")
    src = str(pytest.importorskip("altiplano").__file__).rsplit("/altiplano/", 1)[0]

    script = tmp_path / "concurrent_add.py"
    script.write_text(CONCURRENT_ADD.format(src=src, store=str(store)))

    adder = subprocess.Popen([sys.executable, str(script)])
    try:
        # Let the add get past its read and into the sleep, then revoke.
        import time

        time.sleep(0.4)
        assert clients._remove("laptop") is True
    finally:
        assert adder.wait(timeout=30) == 0

    clients._file_cache = None
    labels = clients._labels()
    assert "desktop" in labels, f"the add was lost: {labels}"
    assert "laptop" not in labels, f"the revocation was undone: {labels}"
    assert clients._identify(token) is None
