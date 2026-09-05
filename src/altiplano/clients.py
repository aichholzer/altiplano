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

The stdio transport never consults this. A local subprocess speaking over a pipe
has no network to authenticate.
"""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from altiplano.config import _CONFIG_FILE, _mode_warning, _warn_once

_CLIENTS_FILE = Path(os.environ.get("ALTIPLANO_CLIENTS", _CONFIG_FILE.parent / "clients"))

# Prefixed so the value is recognisable in a client config, and so a secret scanner
# has something to match on.
_TOKEN_PREFIX = "altp_"

# Keyed on mtime and size, the same way config.py keys the credentials file. Adding
# or revoking a client takes effect on the next request.
_file_cache: tuple[tuple[Path, int, int], tuple["_Client", ...]] | None = None


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


def _parse(text: str) -> tuple[_Client, ...]:
    """Read the store, skipping blanks, comments, and anything malformed.

    A line missing its digest is dropped in silence. Refusing to start over one
    corrupt line would take out every working client with it.
    """
    found: list[_Client] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":", 2)
        if len(parts) < 2:
            continue
        label, digest = parts[0].strip(), parts[1].strip()
        created = parts[2].strip() if len(parts) > 2 else ""
        if label and digest:
            found.append(_Client(label, digest, created))
    return tuple(found)


def _clients() -> tuple[_Client, ...]:
    """Every registered client, re-reading the store only once it changes."""
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
        # The store decides who may call. An unreadable one cannot be waved
        # through the way a missing credentials file can. Report it as empty and
        # let the caller refuse to serve.
        _warn_once((_CLIENTS_FILE, "unreadable"), f"could not read {_CLIENTS_FILE}: {err}")
        return ()

    parsed = _parse(text)
    _file_cache = (stamp, parsed)
    return parsed


def _identify(token: str | None) -> str | None:
    """The label holding this token, or `None`.

    Compared with `hmac.compare_digest` over every record. A handful of clients
    makes the cost irrelevant, and it removes the question entirely.
    """
    if not token:
        return None
    presented = _digest(token)
    for client in _clients():
        if hmac.compare_digest(presented, client.digest):
            return client.label
    return None


def _labels() -> tuple[str, ...]:
    return tuple(client.label for client in _clients())


def _write(records: tuple[_Client, ...]) -> None:
    """Replace the store, through a temporary file in the same directory.

    The mode is set before the rename. The file is never briefly readable by anyone
    else. `os.replace` is atomic within one filesystem: a reader sees the old store
    or the new one.
    """
    global _file_cache
    _CLIENTS_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lines = [f"{c.label}:{c.digest}:{c.created}" for c in records]
    temporary = _CLIENTS_FILE.with_name(f"{_CLIENTS_FILE.name}.{os.getpid()}.tmp")
    temporary.write_text("".join(f"{line}\n" for line in lines))
    temporary.chmod(0o600)
    os.replace(temporary, _CLIENTS_FILE)
    _file_cache = None


def _add(label: str) -> str:
    """Register `label` and return its token. The token is not recoverable later."""
    if not label or ":" in label or label.startswith("#") or label.strip() != label:
        raise ValueError(
            "a client label must be non-empty, free of ':' and surrounding "
            "whitespace, and must not start with '#'"
        )
    existing = _clients()
    if label in {client.label for client in existing}:
        raise ValueError(f"client {label!r} already exists. Revoke it first to re-issue.")
    token = _mint()
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write((*existing, _Client(label, _digest(token), created)))
    return token


def _remove(label: str) -> bool:
    """Revoke `label`. False when there was nothing to revoke."""
    existing = _clients()
    kept = tuple(client for client in existing if client.label != label)
    if len(kept) == len(existing):
        return False
    _write(kept)
    return True
