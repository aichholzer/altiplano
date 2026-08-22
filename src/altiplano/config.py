"""Where the credentials come from, and the only module that reads a file.

Resolved without storing secrets in a shared mcp.json:
  1. Environment variables VIKUNJA_URL / VIKUNJA_API_TOKEN (preferred).
  2. A per-device file of KEY=VALUE lines (default ~/.config/altiplano/env,
     override with ALTIPLANO_CONFIG). Keep it chmod 600.

Nothing is resolved at import time. Every value is read when a request needs it,
which is what lets a rotated token take effect without a restart.
"""

import os
import warnings
from pathlib import Path

_CONFIG_FILE = Path(
    os.environ.get("ALTIPLANO_CONFIG", Path.home() / ".config" / "altiplano" / "env")
)


# A single tool call resolves credentials three or four times, by way of _base,
# _headers and _version, so both the warnings below and the parse itself are done
# once per file rather than once per lookup.
_warned_about: set[tuple[Path, str]] = set()
_file_cache: tuple[tuple[Path, int, int], dict[str, str]] | None = None


def _warn_once(key: tuple[Path, str], message: str) -> None:
    """Warn about one file, for one reason, once per process."""
    if key in _warned_about:
        return
    _warned_about.add(key)
    warnings.warn(message, stacklevel=3)


def _mode_warning(path: Path, mode: int) -> str | None:
    """The complaint to make when the credentials file is not chmod 600, if any.

    The module docstring asks for 600; this checks it instead of trusting it. It
    only warns, because the file belongs to the user and refusing to read one that
    works today would be the worse trade. The message names the path and the mode,
    never the contents.
    """
    if os.name != "posix" or not mode & 0o077:
        return None
    return (
        f"{path} is accessible to group or others (mode {mode:04o}) and holds your "
        f"Vikunja API token. Restrict it with: chmod 600 {path}"
    )


def _load_file() -> dict[str, str]:
    """Parse the credentials file, re-reading it only once it changes.

    Keyed on mtime and size rather than cached for the life of the process, so a
    rotated token is still picked up without a restart.
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
        # rather than raise: the environment may already carry the credentials, in
        # which case this file is irrelevant and failing here would be wrong.
        _warn_once((_CONFIG_FILE, "unreadable"), f"could not read {_CONFIG_FILE}: {err}")
        return {}

    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        # setdefault, so a duplicated key keeps the first occurrence, which is what
        # the earlier line-by-line scan did.
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


def _headers() -> dict[str, str]:
    token = _conf("VIKUNJA_API_TOKEN")
    if not token:
        raise RuntimeError("VIKUNJA_API_TOKEN is not set (env or ~/.config/altiplano/env)")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
