"""The per-client token store, and the only place a caller is identified.

The HTTP transport hands every request's bearer token to `_identify`, which returns
the label of the client that holds it. `altiplano-clientkey` mints tokens and
writes the store; nothing else does.

One record per line, beside the credentials file:

    label:sha256hex:created

Only the SHA-256 of a token is kept. A leaked store yields no working credential,
and a lost token is replaced by revoking the label and adding it again.

Tokens carry 32 bytes from `secrets`. The digest is therefore a bare SHA-256, with
no salt and no password KDF: there is no dictionary to attack, and a KDF would add
a third runtime dependency to a package that has two.

Two rules keep the file trustworthy. A label is drawn from a narrow character set,
so no label can smuggle a line break and split itself into a second record. A digest
is exactly 64 hexadecimal characters. One corrupt line therefore cannot make
`hmac.compare_digest` raise and lock out every working client.

Writes take an exclusive lock on a sibling `clients.lock` for the whole
read-modify-write. Without it, an add overlapping a revoke would write back a
snapshot taken before the revoke and bring the revoked token back.

The stdio transport never consults this. A local subprocess speaking over a pipe
has no network to authenticate.
"""

import hashlib
import hmac
import os
import re
import secrets
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from altiplano.config import _CONFIG_FILE, _mode_warning, _warn_once

try:  # pragma: no cover
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

_CLIENTS_FILE = Path(os.environ.get("ALTIPLANO_CLIENTS", _CONFIG_FILE.parent / "clients"))

# Prefixed so the value is recognisable in a client config, and so a secret scanner
# has something to match on.
_TOKEN_PREFIX = "altp_"

# Letters, digits, and the three separators that survive a round trip through the
# file. A label starts alphanumeric, which keeps '#' and '-' out of the first
# position. Anything wider risks a line break, a colon, or a control character
# rewriting the record.
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")

# Keyed on mtime and size, the same way config.py keys the credentials file. Adding
# or revoking a client takes effect on the next request.
_file_cache: tuple[tuple[Path, int, int], tuple["_Client", ...]] | None = None


class _StoreUnreadable(RuntimeError):
    """The store exists and cannot be read.

    Distinct from an absent store. "I cannot tell who is authorised" and "nobody is
    authorised" are different answers, and only one of them may ever be mistaken for
    "everybody is authorised".
    """


class _Client(NamedTuple):
    label: str
    digest: str
    created: str


def _digest(token: str) -> str:
    """The stored form of a token."""
    return hashlib.sha256(token.encode()).hexdigest()


def _mint() -> str:
    """A fresh client token. Shown to its owner once and never stored."""
    return _TOKEN_PREFIX + secrets.token_urlsafe(32)


def _lock_path() -> Path:
    return _CLIENTS_FILE.with_name(f"{_CLIENTS_FILE.name}.lock")


@contextmanager
def _locked() -> Iterator[None]:
    """Hold an exclusive lock for one whole read-modify-write.

    The lock is a file of its own. Locking the store itself would not work: `_write`
    replaces that path, and the lock would follow the old inode while the next
    writer took a lock on the new one.
    """
    if fcntl is None:  # pragma: no cover
        yield
        return
    _CLIENTS_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = os.open(_lock_path(), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        os.close(handle)


def _parse(text: str) -> tuple[_Client, ...]:
    """Read the store, skipping blanks, comments, and anything malformed.

    A record with a label or a digest that fails its pattern is skipped with a
    warning. One corrupt line leaves every other client working.
    """
    found: list[_Client] = []
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":", 2)
        if len(parts) < 2:
            _skipped(number, "no digest")
            continue
        label, digest = parts[0].strip(), parts[1].strip().lower()
        created = parts[2].strip() if len(parts) > 2 else ""
        if not _LABEL.match(label):
            _skipped(number, "unusable label")
            continue
        if not _DIGEST.match(digest):
            _skipped(number, "digest is not 64 hex characters")
            continue
        found.append(_Client(label, digest, created))
    return tuple(found)


def _skipped(number: int, why: str) -> None:
    """Warn about one unusable record, naming neither the label nor the digest."""
    _warn_once(
        (_CLIENTS_FILE, f"line {number}"),
        f"{_CLIENTS_FILE} line {number} was skipped: {why}",
    )


def _clients() -> tuple[_Client, ...]:
    """Every registered client, re-reading the store only once it changes.

    Raises `_StoreUnreadable` when the file is present and unreadable. An absent
    store is an empty one.
    """
    global _file_cache
    try:
        info = _CLIENTS_FILE.stat()
        warning = _mode_warning(
            _CLIENTS_FILE, info.st_mode & 0o777, "the Altiplano client tokens"
        )
        if warning:
            _warn_once((_CLIENTS_FILE, "mode"), warning)
        stamp = (_CLIENTS_FILE, info.st_mtime_ns, info.st_size)
        if _file_cache is not None and _file_cache[0] == stamp:
            return _file_cache[1]
        text = _CLIENTS_FILE.read_text()
    except FileNotFoundError:
        return ()
    except OSError as err:
        raise _StoreUnreadable(f"could not read {_CLIENTS_FILE}: {err}") from err

    parsed = _parse(text)
    _file_cache = (stamp, parsed)
    return parsed


def _identify(token: str | None) -> str | None:
    """The label holding this token, or `None`.

    Compared with `hmac.compare_digest` over every record. A handful of clients
    makes the cost irrelevant, and it removes the question entirely.

    An unreadable store denies. The caller cannot tell that apart from a token
    nobody holds, which is the safe way round.
    """
    if not token:
        return None
    presented = _digest(token)
    try:
        registered = _clients()
    except _StoreUnreadable as err:
        _warn_once((_CLIENTS_FILE, "denying"), f"denying every request: {err}")
        return None
    for client in registered:
        if hmac.compare_digest(presented, client.digest):
            return client.label
    return None


def _labels() -> tuple[str, ...]:
    return tuple(client.label for client in _clients())


def _write(records: tuple[_Client, ...]) -> None:
    """Replace the store, through a temporary file in the same directory.

    `mkstemp` creates at 0600. The file is never readable by anyone else, not even
    for the moment between creation and the rename. `os.replace` is atomic within
    one filesystem: a reader sees the old store or the new one.

    Call this inside `_locked`.
    """
    global _file_cache
    _CLIENTS_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    body = "".join(f"{c.label}:{c.digest}:{c.created}\n" for c in records)
    handle, temporary = tempfile.mkstemp(dir=_CLIENTS_FILE.parent, prefix=".clients-")
    try:
        with os.fdopen(handle, "w") as out:
            out.write(body)
        os.replace(temporary, _CLIENTS_FILE)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    _file_cache = None


def _add(label: str) -> str:
    """Register `label` and return its token. The token is not recoverable later."""
    if not _LABEL.match(label):
        raise ValueError(
            "a client label must be 1 to 64 characters of letters, digits, '.', "
            "'_' or '-', starting with a letter or a digit"
        )
    with _locked():
        # Read inside the lock. The cache is keyed on mtime and size, and a write by
        # another process between our last read and this one invalidates it.
        existing = _clients()
        if label in {client.label for client in existing}:
            raise ValueError(f"client {label!r} already exists. Revoke it first to re-issue.")
        token = _mint()
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _write((*existing, _Client(label, _digest(token), created)))
    return token


def _remove(label: str) -> bool:
    """Revoke `label`. False when there was nothing to revoke."""
    with _locked():
        existing = _clients()
        kept = tuple(client for client in existing if client.label != label)
        if len(kept) == len(existing):
            return False
        _write(kept)
    return True
