"""Minimal Vikunja MCP server.

Filtering and sorting are passed straight to the Vikunja API (server-side),
so there is no client-side filtering engine to get wrong.

Credentials are resolved without storing secrets in a shared mcp.json:
  1. Environment variables VIKUNJA_URL / VIKUNJA_API_TOKEN (preferred).
  2. A per-device file of KEY=VALUE lines (default ~/.config/altiplano/env,
     override with ALTIPLANO_CONFIG). Keep it chmod 600.
"""

import os
import warnings
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

from altiplano import __version__

# The version is declared so it appears in the MCP handshake. `uvx` can serve a
# cached build for a while after a release, and without this the running version
# is invisible: the only way to tell is to provoke a behaviour that changed.
mcp = MCPServer("altiplano", version=__version__)

_CONFIG_FILE = Path(
    os.environ.get("ALTIPLANO_CONFIG", Path.home() / ".config" / "altiplano" / "env")
)


# Vikunja has no null for a date. An unset one is Go's zero time, both on the
# wire and in the database, so writing this value back is how a date is cleared.
_NO_DATE = "0001-01-01T00:00:00Z"


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


# Vikunja 2.4.0 added a v2 API alongside v1. Paths are identical for everything
# this server does, but the verbs for create and update differ, so the version is
# taken from the URL the user configured rather than probed. Pointing
# VIKUNJA_URL at /api/v2 is the whole opt-in.
_VERBS = {
    1: {"create": "PUT", "update": "POST"},
    2: {"create": "POST", "update": "PATCH"},
}


def _version() -> int:
    """2 when VIKUNJA_URL ends in /api/v2, otherwise 1."""
    return 2 if _base().endswith("/api/v2") else 1


def _verb(action: str) -> str:
    """The verb this API version uses for `create` or `update`."""
    return _VERBS[_version()][action]


def _md_params() -> dict[str, str]:
    """Ask v2 to exchange rich-text fields as Markdown. v1 has no such option.

    Descriptions and comments are stored as HTML. v2 will convert in both
    directions, which is what lets callers write Markdown instead of hand-rolling
    HTML. On v1 this is empty and the fields stay HTML.
    """
    return {"format": "markdown"} if _version() == 2 else {}


def _date(value: str) -> str:
    """Translate an empty string into the value Vikunja uses for no date.

    Dates can otherwise only be overwritten, never cleared: `None` means "leave
    this out of the payload", and an empty string is not a datetime Vikunja will
    parse. There is no null to send, so clearing one means writing the zero time.
    """
    return _NO_DATE if value == "" else value


def _error_detail(r: httpx.Response) -> str:
    """A message that says what the server actually objected to.

    httpx's own message stops at the status code, which for a rejected filter
    expression or a validation failure leaves an agent nothing to act on. Vikunja
    explains itself in the body: v2 follows RFC 9457 (`detail`, alongside a numeric
    `code`), v1 uses `message`.
    """
    where = f"{r.status_code} {r.reason_phrase} for {r.request.method} {r.request.url}"
    if r.has_redirect_location:
        # Nearly always a VIKUNJA_URL that is wrong rather than a real redirect,
        # so name where it was sent instead.
        return f"{where}: redirected to {r.headers['Location']}"
    try:
        payload = r.json()
    except ValueError:
        return where
    if not isinstance(payload, dict):
        return where
    detail = payload.get("detail") or payload.get("message") or payload.get("title")
    if not detail:
        return where
    code = payload.get("code")
    return f"{where}: {detail}" + (f" (code {code})" if code else "")


async def _send(method: str, path: str, **kwargs: Any) -> httpx.Response:
    """One request. Raises on any non-2xx, carrying the server's own explanation.

    `is_success` rather than `is_error`, so a redirect is still a failure: it means
    the configured URL is wrong, and decoding its body as a result would hide that.
    """
    async with httpx.AsyncClient(base_url=_base(), headers=_headers(), timeout=30) as client:
        r = await client.request(method, path, **kwargs)
        if not r.is_success:
            raise httpx.HTTPStatusError(_error_detail(r), request=r.request, response=r)
        return r


def _decode(r: httpx.Response) -> Any:
    if r.status_code == 204 or not r.content:
        return {"ok": True}
    return r.json()


async def _request(method: str, path: str, **kwargs: Any) -> Any:
    return _decode(await _send(method, path, **kwargs))


def _items(data: Any) -> list:
    """Normalise a collection response across both API versions.

    v1 returns a bare array, or a literal `null` for some empty collections. v2
    wraps every collection in a pagination envelope. The check is on shape rather
    than the configured version, so a mismatch between the two cannot break it.

    Anything else means the response was not a collection at all: most likely a
    bodyless response, which `_request` reports as a status dict. That is an error
    rather than an empty result, because reporting it as empty is
    indistinguishable from genuinely having no items.
    """
    if data is None:
        return []
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    if not isinstance(data, list):
        raise RuntimeError(f"expected a list from the API, got {type(data).__name__}")
    return data


def _task_summary(t: dict) -> dict:
    return {
        "id": t.get("id"),
        "identifier": t.get("identifier"),
        "title": t.get("title"),
        "done": t.get("done"),
        "priority": t.get("priority"),
    }


# --- projects ---------------------------------------------------------------
@mcp.tool()
async def list_projects() -> list[dict]:
    """List all projects (boards). `parent_project_id` shows sub-project nesting."""
    data = await _request("GET", "/projects")
    return [
        {
            "id": p["id"],
            "title": p["title"],
            "parent_project_id": p.get("parent_project_id", 0),
            "is_archived": p.get("is_archived", False),
        }
        for p in _items(data)
    ]


@mcp.tool()
async def create_project(
    title: str,
    parent_project_id: int | None = None,
    description: str | None = None,
) -> dict:
    """Create a project. Pass `parent_project_id` to create it as a sub-project."""
    payload: dict[str, Any] = {"title": title}
    if parent_project_id is not None:
        payload["parent_project_id"] = parent_project_id
    if description is not None:
        payload["description"] = description
    return await _request(_verb("create"), "/projects", params=_md_params(), json=payload)


# --- tasks ------------------------------------------------------------------
@mcp.tool()
async def list_tasks(
    project_id: int,
    # `filter` shadows the builtin on purpose: it is the name Vikunja gives the
    # query parameter and the name callers already write, and the builtin is not
    # used in this module. Renaming it would break the published tool contract.
    filter: str | None = None,
    sort_by: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> list[dict]:
    """List tasks in a project.

    `filter` and `sort_by` are passed to Vikunja and applied server-side, e.g.
    filter="done = false && priority >= 4", sort_by="priority". Vikunja filters
    then paginates, so results are complete regardless of page size.
    """
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if filter:
        params["filter"] = filter
    if sort_by:
        params["sort_by"] = sort_by
    data = await _request("GET", f"/projects/{project_id}/tasks", params=params)
    return [_task_summary(t) for t in _items(data)]


@mcp.tool()
async def get_task(task_id: int) -> dict:
    """Get a single task with full detail. On v2 the description is Markdown."""
    return await _request("GET", f"/tasks/{task_id}", params=_md_params())


@mcp.tool()
async def create_task(
    project_id: int,
    title: str,
    description: str | None = None,
    priority: int | None = None,
    due_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Create a task in a project.

    `start_date` and `end_date` are ISO 8601 datetimes marking the window you
    plan to work on the task (start work / finish work), distinct from
    `due_date` (the deadline).
    """
    payload: dict[str, Any] = {"title": title}
    if description is not None:
        payload["description"] = description
    if priority is not None:
        payload["priority"] = priority
    if due_date is not None:
        payload["due_date"] = _date(due_date)
    if start_date is not None:
        payload["start_date"] = _date(start_date)
    if end_date is not None:
        payload["end_date"] = _date(end_date)
    return await _request(
        _verb("create"), f"/projects/{project_id}/tasks", params=_md_params(), json=payload
    )


@mcp.tool()
async def update_task(
    task_id: int,
    title: str | None = None,
    description: str | None = None,
    done: bool | None = None,
    priority: int | None = None,
    due_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Update a task. Only the fields you pass change. Use `done` to open/close it.

    v1 has no partial update, so there this reads the task and writes it back with
    your changes merged in, at the cost of one extra request. v2 is a single PATCH
    unless a description is involved.

    `due_date` is the deadline. `start_date` and `end_date` are ISO 8601 datetimes
    marking the window you plan to work on the task (start work / finish work).
    Pass an empty string to any of the three to clear it.
    """
    payload: dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if description is not None:
        payload["description"] = description
    if done is not None:
        payload["done"] = done
    if priority is not None:
        payload["priority"] = priority
    if due_date is not None:
        payload["due_date"] = _date(due_date)
    if start_date is not None:
        payload["start_date"] = _date(start_date)
    if end_date is not None:
        payload["end_date"] = _date(end_date)
    if not payload:
        raise ValueError("No fields to update")
    # Two separate reasons to read the task first, see _replace_task: on v1 because
    # a partial body would wipe the fields it omits, and on v2 because PATCH would
    # store a Markdown description verbatim. Everything else on v2 stays a cheap
    # partial update.
    if _version() == 1 or "description" in payload:
        return await _replace_task(task_id, payload)
    # Markdown is asked for on the way back, so this returns a task shaped like the
    # one get_task returns rather than one carrying raw HTML. v2 ignores the
    # parameter for a PATCH request body, which is exactly why a description never
    # reaches this line, but asking costs nothing on the way out.
    return await _request(_verb("update"), f"/tasks/{task_id}", params=_md_params(), json=payload)


async def _replace_task(task_id: int, changes: dict[str, Any]) -> dict:
    """Apply `changes` by reading the task and writing it back whole.

    There is a reason per API version to go the long way round.

    On v1 there is no partial update at all. `POST /tasks/{id}` is a replace, so a
    body carrying only the changed fields resets every other field to its zero
    value: passing `priority` blanks the description, and closing a task with
    `done` discards its description, priority and dates. Reading first and merging
    is the only way to change one field without destroying the rest.

    On v2 there is `PATCH`, but it silently ignores ?format=markdown, returning 200
    while storing the Markdown verbatim into a field rendered as HTML. So a
    description still has to go through a replace there, and everything else stays
    a cheap partial update.

    The read asks for Markdown too, so a description we are not touching is written
    back in the form it came in rather than being double-converted. Verified
    lossless across labels, assignees, reminders, dates, colour, priority and
    percent_done.

    The lost update this opens is caught where the server allows it: v2 returns an
    ETag on a single-resource read and honours If-Match, so a task that changed in
    between fails with 412 instead of being silently overwritten. v1 offers no
    ETag, so no precondition is sent and that window stays open there.
    """
    read = await _send("GET", f"/tasks/{task_id}", params=_md_params())
    current = _decode(read)
    if not isinstance(current, dict) or "id" not in current:
        # A bodyless response arrives as a status dict. Replacing a task with that
        # would wipe it, which is worse than refusing.
        raise RuntimeError(f"the API did not return task {task_id}, so it was not updated")
    body = {k: v for k, v in current.items() if k != "$schema"}
    body.update(changes)

    headers = {}
    etag = read.headers.get("ETag")
    if etag:
        headers["If-Match"] = etag
    # On v1 the replace is the same POST a partial update would have used, which is
    # exactly why the partial update was unsafe.
    verb = "PUT" if _version() == 2 else "POST"
    try:
        return await _request(
            verb, f"/tasks/{task_id}", params=_md_params(), headers=headers, json=body
        )
    except httpx.HTTPStatusError as err:
        if err.response.status_code == 412:
            raise RuntimeError(
                f"task {task_id} changed while this update was being prepared, so nothing was "
                "written. Read it again and retry."
            ) from err
        raise


@mcp.tool()
async def set_reminders(task_id: int, reminders: list[str]) -> dict:
    """Replace a task's reminders with the given ISO 8601 datetimes. Empty list clears them.

    Nothing else about the task changes. On v1 that costs an extra request, because
    its update endpoint is a replace and the task has to be read and written back
    whole; on v2 it is a single partial update.
    """
    payload: dict[str, Any] = {"reminders": [{"reminder": r} for r in reminders]}
    # Same replace hazard as update_task, through the same endpoint. This one went
    # unnoticed until 0.8.1 because the payload looks self-contained.
    if _version() == 1:
        return await _replace_task(task_id, payload)
    return await _request(_verb("update"), f"/tasks/{task_id}", params=_md_params(), json=payload)


@mcp.tool()
async def delete_task(task_id: int) -> dict:
    """Delete a task. There is no way to undo this through the API.

    Vikunja soft-deletes, and documents deleted tasks as retained for 30 days
    before permanent removal, but it exposes no endpoint to list or restore them.
    So the row outlives the task while being unreachable from here. Treat this as
    irreversible and confirm the id first, because deleting a task also takes its
    comments, labels and assignees with it.
    """
    return await _request("DELETE", f"/tasks/{task_id}")


# --- labels -----------------------------------------------------------------
@mcp.tool()
async def list_labels() -> list[dict]:
    """List all labels."""
    data = await _request("GET", "/labels")
    return [{"id": x["id"], "title": x["title"]} for x in _items(data)]


@mcp.tool()
async def add_label(task_id: int, label_id: int) -> dict:
    """Attach a label to a task."""
    return await _request(_verb("create"), f"/tasks/{task_id}/labels", json={"label_id": label_id})


@mcp.tool()
async def remove_label(task_id: int, label_id: int) -> dict:
    """Remove a label from a task."""
    return await _request("DELETE", f"/tasks/{task_id}/labels/{label_id}")


# --- comments ---------------------------------------------------------------
@mcp.tool()
async def list_comments(task_id: int) -> list[dict]:
    """List comments on a task."""
    data = await _request("GET", f"/tasks/{task_id}/comments", params=_md_params())
    return [
        {"id": c.get("id"), "comment": c.get("comment"), "author": (c.get("author") or {}).get("username")}
        for c in _items(data)
    ]


@mcp.tool()
async def add_comment(task_id: int, comment: str) -> dict:
    """Add a comment to a task."""
    return await _request(
        _verb("create"), f"/tasks/{task_id}/comments", params=_md_params(), json={"comment": comment}
    )


@mcp.tool()
async def update_comment(task_id: int, comment_id: int, comment: str) -> dict:
    """Replace the text of an existing comment. Get `comment_id` from `list_comments`."""
    # v2 honours ?format=markdown on PUT but silently ignores it on PATCH, storing
    # the Markdown verbatim in a field the UI renders as HTML. A comment has one
    # writable field, so replacing it and updating it are the same thing, and PUT
    # is the variant that converts. v1 keeps its own update verb.
    verb = "PUT" if _version() == 2 else _verb("update")
    return await _request(
        verb, f"/tasks/{task_id}/comments/{comment_id}", params=_md_params(), json={"comment": comment}
    )


@mcp.tool()
async def delete_comment(task_id: int, comment_id: int) -> dict:
    """Delete a comment from a task. Get `comment_id` from `list_comments`."""
    return await _request("DELETE", f"/tasks/{task_id}/comments/{comment_id}")


# --- users / assignees ------------------------------------------------------
@mcp.tool()
async def search_users(query: str) -> list[dict]:
    """Search users by name or username. Use this to find a user_id for assignees."""
    data = await _request("GET", "/users", params={"q" if _version() == 2 else "s": query})
    return [{"id": u.get("id"), "username": u.get("username"), "name": u.get("name")} for u in _items(data)]


@mcp.tool()
async def list_assignees(task_id: int) -> list[dict]:
    """List the users assigned to a task."""
    data = await _request("GET", f"/tasks/{task_id}/assignees")
    return [{"id": u.get("id"), "username": u.get("username")} for u in _items(data)]


@mcp.tool()
async def add_assignee(task_id: int, user_id: int) -> dict:
    """Assign a user to a task."""
    return await _request(_verb("create"), f"/tasks/{task_id}/assignees", json={"user_id": user_id})


@mcp.tool()
async def remove_assignee(task_id: int, user_id: int) -> dict:
    """Unassign a user from a task."""
    return await _request("DELETE", f"/tasks/{task_id}/assignees/{user_id}")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
