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

# A stand-in for the Vikunja API token a client acts with. Every record needs one.
VIKUNJA = "tk_" + "1" * 32


def label_of(token):
    """The label holding `token`, or None. `_resolve` returns the whole record."""
    client = clients._resolve(token)
    return client.label if client else None


def record(label, digest, token=VIKUNJA, created="2026-09-05T00:00:00Z"):
    """One store line in the current format."""
    return f"{label}:{digest}:{token}:{created}"


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


def write(path, text, *, header=True):
    """Write a store fixture at the mode the module would have written.

    The version line goes on by default. `header=False` produces a v1 store, which is
    what the migration cases need.
    """
    body = textwrap.dedent(text).lstrip()
    path.write_text(f"{clients._HEADER}\n{body}" if header else body)
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
    assert label_of("altp_anything") is None


def test_add_then_identify_round_trips(store):
    token = clients._add("laptop", VIKUNJA)
    assert label_of(token) == "laptop"
    assert clients._labels() == ("laptop",)


def test_the_store_holds_the_digest_and_never_the_client_token(store):
    token = clients._add("laptop", VIKUNJA)
    text = store.read_text()
    assert token not in text
    assert clients._digest(token) in text
    assert text.splitlines()[0] == clients._HEADER
    assert text.splitlines()[1].startswith("laptop:")


def test_the_store_holds_the_vikunja_token_in_plaintext(store):
    """Deliberate, and the reason the file is written 0600.

    Altiplano presents this token to Vikunja on every request the client makes and
    needs the plaintext to do it. A reader of this file can act as every client in it.
    """
    token = clients._add("laptop", VIKUNJA)
    assert VIKUNJA in store.read_text()
    assert clients._resolve(token).vikunja_token == VIKUNJA


def test_a_second_client_is_appended(store):
    first = clients._add("laptop", VIKUNJA)
    second = clients._add("desktop", VIKUNJA)
    assert label_of(first) == "laptop"
    assert label_of(second) == "desktop"
    assert clients._labels() == ("laptop", "desktop")


def test_a_duplicate_label_is_refused(store):
    clients._add("laptop", VIKUNJA)
    with pytest.raises(ValueError, match="already exists"):
        clients._add("laptop", VIKUNJA)


def test_revoking_removes_only_that_client(store):
    laptop = clients._add("laptop", VIKUNJA)
    desktop = clients._add("desktop", VIKUNJA)

    assert clients._remove("laptop") is True
    assert label_of(laptop) is None
    assert label_of(desktop) == "desktop"


def test_revoking_something_absent_reports_it(store):
    clients._add("laptop", VIKUNJA)
    assert clients._remove("desktop") is False
    assert clients._labels() == ("laptop",)


def test_an_unissued_token_identifies_nobody(store):
    clients._add("laptop", VIKUNJA)
    assert label_of("altp_neverissued") is None


@pytest.mark.parametrize("token", [None, ""], ids=["none", "empty"])
def test_no_token_identifies_nobody(store, token):
    clients._add("laptop", VIKUNJA)
    assert label_of(token) is None


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
        # `$` also matches just before a final newline. `re.match` accepted these,
        # and only `fullmatch` refuses them.
        "laptop\n", "laptop\r\n", "laptop\n\n",
    ],
    ids=[
        "empty", "colon", "hash", "leading space", "trailing space",
        "newline", "carriage return", "crlf", "vertical tab", "line separator",
        "tab", "null byte", "leading dash", "leading dot", "leading underscore",
        "too long", "inner space", "non-ascii", "semicolon",
        "trailing newline", "trailing crlf", "two trailing newlines",
    ],
)
def test_a_label_that_could_rewrite_the_record_is_refused(store, label):
    with pytest.raises(ValueError, match="client label"):
        clients._add(label, VIKUNJA)
    assert not store.exists()


def test_a_trailing_newline_never_mints_an_unusable_token(store):
    """`_LABEL.match("laptop\\n")` succeeded, and the record split across two lines.

    `add` then reported success while handing over a token that could not
    authenticate and a label that could not be revoked.
    """
    with pytest.raises(ValueError, match="client label"):
        clients._add("laptop\n", VIKUNJA)
    assert clients._labels() == ()
    assert not store.exists()


def test_the_patterns_are_anchored_at_both_ends():
    """The guard against `match` creeping back in place of `fullmatch`."""
    assert clients._LABEL.fullmatch("laptop\n") is None
    assert clients._DIGEST.fullmatch("a" * 64 + "\n") is None


def test_an_embedded_newline_cannot_smuggle_a_second_record(store):
    """A label carrying a newline used to store a record that parsed back under a
    different label, leaving a live token nobody could revoke by name."""
    with pytest.raises(ValueError):
        clients._add("work\nlaptop", VIKUNJA)
    assert clients._labels() == ()


@pytest.mark.parametrize(
    "label", ["laptop", "Laptop", "laptop-2", "laptop_2", "laptop.2", "a", "9lives", "a" * 64]
)
def test_a_reasonable_label_is_accepted(store, label):
    assert clients._add(label, VIKUNJA).startswith("altp_")
    assert clients._labels() == (label,)


# --- parsing ----------------------------------------------------------------
def test_blanks_comments_and_malformed_lines_are_skipped(store):
    """One corrupt line must not take every working client down with it."""
    write(
        store,
        f"""
        # a comment

        no-colon-at-all
        {record("laptop", "a" * 64)}
        :missing-label
        nodigest:
        {record("has spaces", "c" * 64)}
        {record("desktop", "d" * 64)}
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
    write(store, f"broken:{digest}\n{record('laptop', 'a' * 64)}\n")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert clients._labels() == ("laptop",)


def test_a_valid_client_authenticates_past_a_corrupt_record(store):
    """The point of skipping a bad line, proven end to end through `_identify`."""
    token = clients._add("laptop", VIKUNJA)
    body = store.read_text()
    write(store, f"broken:\u00e9\n{body}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert label_of(token) == "laptop"


def test_a_skipped_record_is_warned_about_without_naming_its_digest(store):
    write(store, f"broken:{'z' * 64}\n")
    # Line 1 is the version line. The bad record is line 2.
    with pytest.warns(UserWarning, match="was skipped") as caught:
        clients._clients()
    message = str(caught[0].message)
    assert "line 2" in message
    assert "z" * 64 not in message


def test_an_uppercase_digest_still_matches(store):
    """Hex is case-insensitive, and a hand-edited store may use either."""
    token = clients._mint()
    write(store, record("laptop", clients._digest(token).upper()) + "\n")
    assert label_of(token) == "laptop"


def test_a_record_with_no_created_field_still_loads(store):
    write(store, f"laptop:{'a' * 64}:{VIKUNJA}\n")
    assert clients._clients()[0].created == ""
    assert clients._clients()[0].vikunja_token == VIKUNJA


# --- caching ----------------------------------------------------------------
def test_the_parse_is_cached_and_the_file_is_opened_every_time(store, monkeypatch):
    """Two separate guarantees, and they pull in opposite directions.

    The parse is cached, and repeated reads cost nothing. The file is opened on
    every call regardless, which is what notices a store that has stopped being
    readable.
    """
    clients._add("laptop", VIKUNJA)
    clients._file_cache = None

    opens, parses = [], []
    real_fstat, real_parse = clients.os.fstat, clients._parse
    monkeypatch.setattr(clients.os, "fstat", lambda fd: (opens.append(fd), real_fstat(fd))[1])
    monkeypatch.setattr(clients, "_parse", lambda text: (parses.append(text), real_parse(text))[1])

    clients._labels()
    clients._labels()
    clients._labels()
    assert len(opens) == 3, "the file has to be opened on every call"
    assert len(parses) == 1, "the parse is cached"

    write(store, record("desktop", "c" * 64) + "\n")
    bumped = store.stat().st_mtime_ns + 10**9
    os.utime(store, ns=(bumped, bumped))

    assert clients._labels() == ("desktop",)
    assert len(opens) == 4
    assert len(parses) == 2


# --- writing ----------------------------------------------------------------
def test_the_written_store_is_only_readable_by_its_owner(store):
    clients._add("laptop", VIKUNJA)
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
    clients._add("laptop", VIKUNJA)

    assert seen == [0o600]


def test_no_temporary_file_is_left_behind(store, tmp_path):
    clients._add("laptop", VIKUNJA)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["clients", "clients.lock"]


def test_a_failed_write_leaves_no_temporary_file(store, tmp_path, monkeypatch):
    monkeypatch.setattr(clients.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("nope")))
    with pytest.raises(OSError):
        clients._add("laptop", VIKUNJA)
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".clients-")] == []


def test_the_parent_directory_is_created_when_absent(tmp_path, monkeypatch):
    nested = tmp_path / "config" / "altiplano" / "clients"
    monkeypatch.setattr(clients, "_CLIENTS_FILE", nested)
    clients._add("laptop", VIKUNJA)
    assert nested.exists()


# --- permissions and readability --------------------------------------------
def test_warns_when_others_can_read_the_store_but_still_reads_it(store):
    clients._add("laptop", VIKUNJA)
    store.chmod(0o644)
    clients._file_cache = None

    with pytest.warns(UserWarning, match="chmod 600") as caught:
        assert clients._labels() == ("laptop",)

    message = str(caught[0].message)
    assert "client tokens" in message
    assert "Vikunja API token" not in message


def test_does_not_warn_when_only_the_owner_can_read_the_store(store):
    clients._add("laptop", VIKUNJA)
    clients._file_cache = None

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert clients._labels() == ("laptop",)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a file whatever its mode says")
def test_an_unreadable_store_raises(store):
    """An unreadable store is not an empty one. Reporting it as empty is what let the
    HTTP transport come up with no authentication at all."""
    clients._add("laptop", VIKUNJA)
    store.chmod(0o000)
    clients._file_cache = None
    try:
        with pytest.raises(clients._StoreUnreadable, match="could not read"):
            clients._clients()
    finally:
        store.chmod(0o600)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a file whatever its mode says")
def test_an_unreadable_store_denies_even_with_the_cache_populated(store):
    """The cache must not outlive the ability to read the file.

    Removing read permission changes neither mtime nor size. A `stat`-keyed cache
    therefore answered from a file the process could no longer open, and a token
    authorised before the change kept working. No cache clearing here: that is the
    point.
    """
    token = clients._add("laptop", VIKUNJA)
    assert label_of(token) == "laptop"  # populate the cache

    store.chmod(0o000)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            assert label_of(token) is None
            with pytest.raises(clients._StoreUnreadable):
                clients._clients()
    finally:
        store.chmod(0o600)

    # Readable again, and the same token works.
    assert label_of(token) == "laptop"


def test_a_store_replaced_by_a_different_file_of_the_same_size_is_re_read(store):
    """mtime and size alone can miss a swap. The cache identity covers the inode."""
    first = clients._add("laptop", VIKUNJA)
    assert label_of(first) == "laptop"
    stat_before = store.stat()

    replacement = store.with_name("replacement")
    other = clients._digest("altp_other")
    replacement.write_text(f"{clients._HEADER}\n{record('desktop', other)}\n")
    replacement.chmod(0o600)
    os.utime(replacement, ns=(stat_before.st_mtime_ns, stat_before.st_mtime_ns))
    os.replace(replacement, store)

    assert clients._labels() == ("desktop",)
    assert label_of(first) is None


def test_the_store_sits_beside_the_credentials_file():
    assert clients._CLIENTS_FILE.parent == config._CONFIG_FILE.parent
    assert clients._CLIENTS_FILE.name == "clients"


# --- concurrency ------------------------------------------------------------
# --- locking availability ---------------------------------------------------
def test_a_platform_without_fcntl_refuses_to_change_the_store(store, monkeypatch):
    """Proceeding without the lock would drop the guarantee while looking identical.

    A reader needs no lock: `os.replace` is atomic. Only a change is refused.
    """
    clients._add("laptop", VIKUNJA)
    monkeypatch.setattr(clients, "fcntl", None)

    with pytest.raises(clients._LockUnavailable, match="POSIX file locking"):
        clients._add("desktop", VIKUNJA)
    with pytest.raises(clients._LockUnavailable, match="POSIX file locking"):
        clients._remove("laptop")

    assert clients._labels() == ("laptop",)


def test_reading_the_store_needs_no_lock(store, monkeypatch):
    token = clients._add("laptop", VIKUNJA)
    monkeypatch.setattr(clients, "fcntl", None)
    clients._file_cache = None

    assert label_of(token) == "laptop"
    assert clients._labels() == ("laptop",)


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

clients._add("desktop", "tk_" + "2" * 32)
"""


def test_an_overlapping_add_cannot_undo_a_completed_revocation(store, tmp_path):
    """Two processes, one lock.

    Without a lock across the whole read-modify-write, the add reads a snapshot
    containing `laptop`, the revoke completes, and the add writes its stale snapshot
    back. The revoked token returns.
    """
    token = clients._add("laptop", VIKUNJA)
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
    assert label_of(token) is None


# --- the Vikunja token on each record ---------------------------------------
@pytest.mark.parametrize(
    "token",
    ["", "short", "has:colon", "has space", "tab\there", "line\nbreak", "x" * 513],
    ids=["empty", "too short", "colon", "space", "tab", "newline", "too long"],
)
def test_a_vikunja_token_that_could_rewrite_the_record_is_refused(store, token):
    """The token sits between the digest and the timestamp.

    A colon in it would shift `created` along, and a newline would split the record.
    """
    with pytest.raises(ValueError, match="Vikunja API token"):
        clients._add("laptop", token)
    assert not store.exists()


@pytest.mark.parametrize(
    "token",
    ["tk_" + "a" * 32, "x" * 8, "x" * 512, "no-prefix-required", "punct!~$%^&*()"],
    ids=["vikunja shape", "shortest", "longest", "no prefix", "punctuation"],
)
def test_a_usable_vikunja_token_is_accepted(store, token):
    """The `tk_` prefix is not required. Vikunja owns that vocabulary."""
    client_token = clients._add("laptop", token)
    assert clients._resolve(client_token).vikunja_token == token


def test_each_client_carries_its_own_vikunja_token(store):
    mine = clients._add("mine", "tk_" + "1" * 32)
    yours = clients._add("yours", "tk_" + "2" * 32)

    assert clients._resolve(mine).vikunja_token == "tk_" + "1" * 32
    assert clients._resolve(yours).vikunja_token == "tk_" + "2" * 32


def test_a_record_whose_vikunja_token_is_unusable_is_skipped(store):
    write(store, f"broken:{'a' * 64}:has space:2026-09-05T00:00:00Z\n{record('laptop', 'b' * 64)}\n")
    with pytest.warns(UserWarning, match="unusable Vikunja token"):
        assert clients._labels() == ("laptop",)


def test_the_written_store_round_trips_through_the_parser(store):
    """What `_write` produces is what `_parse` reads back, timestamp colons included."""
    token = clients._add("laptop", VIKUNJA)
    clients._file_cache = None

    (parsed,) = clients._clients()
    assert parsed.label == "laptop"
    assert parsed.digest == clients._digest(token)
    assert parsed.vikunja_token == VIKUNJA
    assert parsed.created.endswith("Z")
    assert parsed.created.count(":") == 2


# --- migrating a store that predates per-client Vikunja tokens ---------------
def test_a_v1_store_loads_its_labels_with_no_vikunja_token(store):
    """`altiplano-clientkey list` has to be able to show what needs re-adding."""
    write(store, f"laptop:{'a' * 64}:2026-09-05T00:00:00Z\n", header=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        (parsed,) = clients._clients()
    assert parsed.label == "laptop"
    assert parsed.vikunja_token == ""
    assert parsed.created == "2026-09-05T00:00:00Z", "the timestamp must not be eaten"


def test_a_v1_store_is_warned_about_once_and_names_the_fix(store):
    write(store, f"laptop:{'a' * 64}:2026-09-05T00:00:00Z\n", header=False)
    with pytest.warns(UserWarning, match="predates per-client Vikunja tokens") as caught:
        clients._clients()
    assert "altiplano-clientkey add" in str(caught[0].message)


def test_a_v1_record_still_resolves_so_the_gate_can_name_it(store):
    """`_resolve` answers who, and the HTTP gate decides whether that is enough."""
    token = clients._mint()
    write(store, f"laptop:{clients._digest(token)}:2026-09-05T00:00:00Z\n", header=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        resolved = clients._resolve(token)
    assert resolved.label == "laptop"
    assert resolved.vikunja_token == ""


def test_writing_a_v1_store_upgrades_it(store):
    """One `add` or `revoke` rewrites the whole file, version line included."""
    write(store, f"laptop:{'a' * 64}:2026-09-05T00:00:00Z\n", header=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        clients._add("desktop", VIKUNJA)

    assert store.read_text().startswith(clients._HEADER)
    clients._file_cache = None
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        by_label = {c.label: c for c in clients._clients()}
    assert by_label["desktop"].vikunja_token == VIKUNJA
    assert by_label["laptop"].vikunja_token == "", "still needs re-adding"


def test_an_empty_store_is_not_reported_as_v1(store):
    """An absent or empty file is a fresh install, and there is nothing to migrate."""
    write(store, "")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert clients._clients() == ()


def test_a_v2_record_with_an_empty_token_field_survives(store):
    """The state a v1 record lands in after one rewrite.

    Skipping it would drop the label, and the operator would lose the only sign that
    the client still needs re-adding.
    """
    write(store, f"laptop:{'a' * 64}::2026-09-05T00:00:00Z\n")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        (parsed,) = clients._clients()
    assert parsed.label == "laptop"
    assert parsed.vikunja_token == ""
    assert parsed.created == "2026-09-05T00:00:00Z"
