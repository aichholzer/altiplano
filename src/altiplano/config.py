"""Where the credentials come from, and the only module that reads a file.

`VIKUNJA_URL` is resolved without storing secrets in a shared mcp.json:
  1. The environment variable (preferred).
  2. A per-device file of KEY=VALUE lines (default ~/.config/altiplano/env,
     override with ALTIPLANO_CONFIG). Keep it chmod 600.

The Vikunja API token takes one source ahead of those two: whatever the current
request is bound to, through `_acting_as`. The HTTP transport binds each request to
the token of the client that made it. One server therefore serves several Vikunja
identities from one process. stdio binds nothing and falls through to the environment
and the file.

The URL has no such override. Every client of one server reaches one Vikunja
instance, and `api._version()` reads the v1 or v2 choice off that single URL.

Nothing is resolved at import time. Every value is read when a request needs it.
A rotated token takes effect without a restart.
"""

import os
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_CONFIG_FILE = Path(
    os.environ.get("ALTIPLANO_CONFIG", Path.home() / ".config" / "altiplano" / "env")
)


# A single tool call resolves credentials three or four times, by way of _base,
# _headers, and _version. Both the warnings below and the parse itself happen once
# per file.
_warned_about: set[tuple[Path, str]] = set()
_file_cache: tuple[tuple[Path, int, int], dict[str, str]] | None = None


def _warn_once(key: tuple[Path, str], message: str) -> None:
    """Warn about one file, for one reason, once per process."""
    if key in _warned_about:
        return
    _warned_about.add(key)
    warnings.warn(message, stacklevel=3)


def _mode_warning(path: Path, mode: int, holds: str = "your Vikunja API token") -> str | None:
    """The complaint to make when a secret-bearing file is not chmod 600, if any.

    The module docstring asks for 600; this verifies it. It only warns: the file
    belongs to the user, and refusing to read one that works today would be the
    worse trade. The message names the path and the mode. The contents never appear
    in it.

    `holds` names what is at risk. `clients.py` passes its own: the same check
    guards the client token store.
    """
    if os.name != "posix" or not mode & 0o077:
        return None
    return (
        f"{path} is accessible to group or others (mode {mode:04o}) and holds "
        f"{holds}. Restrict it with: chmod 600 {path}"
    )


def _load_file() -> dict[str, str]:
    """Parse the credentials file, re-reading it only once it changes.

    Keyed on mtime and size. A rotated token is picked up without a restart.
    """
    global _file_cache
    try:
        info = _CONFIG_FILE.stat()
        warning = _mode_warning(_CONFIG_FILE, info.st_mode & 0o777)
        if warning:
            _warn_once((_CONFIG_FILE, "mode"), warning)
        stamp = (_CONFIG_FILE, info.st_mtime_ns, info.st_size)
        if _file_cache is not None and _file_cache[0] == stamp:
            return _file_cache[1]
        text = _CONFIG_FILE.read_text()
    except FileNotFoundError:
        return {}
    except OSError as err:
        # Usually permissions, on the file itself or a directory above it. Warn
        # and carry on: the environment may already hold the credentials, in which
        # case this file is irrelevant and failing here would be wrong.
        _warn_once((_CONFIG_FILE, "unreadable"), f"could not read {_CONFIG_FILE}: {err}")
        return {}

    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        # setdefault: a duplicated key keeps the first occurrence. The earlier
        # line-by-line scan behaved the same way.
        values.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    _file_cache = (stamp, values)
    return values


def _from_file(key: str) -> str | None:
    return _load_file().get(key)


def _conf(key: str) -> str | None:
    return os.environ.get(key) or _from_file(key)


def _base() -> str:
    url = _conf("VIKUNJA_URL")
    if not url:
        raise RuntimeError("VIKUNJA_URL is not set (env or ~/.config/altiplano/env)")
    return url.rstrip("/")


# The Vikunja token the current request acts with. The HTTP gate binds it once the
# caller is known; on stdio it stays unset.
#
# A ContextVar and not an argument because the alternative is threading an identity
# through 35 tool signatures and every helper in `api.py`. A ContextVar set in ASGI
# middleware reaches the tool coroutine and stays isolated per request, including
# across overlapping calls on one long-lived session. `tests/test_clients.py` holds
# the test that says so.
_REQUEST_TOKEN: ContextVar[str | None] = ContextVar("altiplano_vikunja_token", default=None)


@contextmanager
def _acting_as(token: str) -> Iterator[None]:
    """Bind the Vikunja token for everything called inside this block."""
    handle = _REQUEST_TOKEN.set(token)
    try:
        yield
    finally:
        _REQUEST_TOKEN.reset(handle)


def _headers() -> dict[str, str]:
    # The bound token wins. On the HTTP transport the gate refuses a caller it cannot
    # bind one for. Falling through to the environment here therefore means stdio, or
    # the loopback development mode that turns the gate off.
    token = _REQUEST_TOKEN.get() or _conf("VIKUNJA_API_TOKEN")
    if not token:
        raise RuntimeError("VIKUNJA_API_TOKEN is not set (env or ~/.config/altiplano/env)")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
