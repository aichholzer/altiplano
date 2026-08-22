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
# `replace` is the third action because three places need a whole-resource write
# rather than a partial one: the task replace, a comment edit, and placing a task in
# a bucket. v1 spells that the same way it spells an update, which is the root of
# the hazard _replace_task exists to work around.
_VERBS = {
    1: {"create": "PUT", "update": "POST", "replace": "POST"},
    2: {"create": "POST", "update": "PATCH", "replace": "PUT"},
}


def _version() -> int:
    """2 when VIKUNJA_URL ends in /api/v2, otherwise 1."""
    return 2 if _base().endswith("/api/v2") else 1


def _verb(action: str) -> str:
    """The verb this API version uses for `create`, `update` or `replace`."""
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
async def search_tasks(
    query: str | None = None,
    filter: str | None = None,
    sort_by: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> list[dict]:
    """Search tasks across every project you can see.

    `list_tasks` needs to be told a project. This does not, which makes it the tool
    for "find this task, I do not remember where it lives". Results carry
    `project_id` for the same reason.

    `query` is a text search over titles and descriptions. `filter` and `sort_by` are
    the same server-side syntax `list_tasks` takes. Vikunja documents the text search
    as not combinable with a filter, so use one or the other.
    """
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if query:
        # Renamed between versions, the same way it is for search_users.
        params["q" if _version() == 2 else "s"] = query
    if filter:
        params["filter"] = filter
    if sort_by:
        params["sort_by"] = sort_by
    data = await _request("GET", "/tasks", params=params)
    return [{**_task_summary(t), "project_id": t.get("project_id")} for t in _items(data)]


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
    percent_done: float | None = None,
    is_favorite: bool | None = None,
    repeat_after: int | None = None,
    repeat_mode: int | None = None,
) -> dict:
    """Create a task in a project.

    `start_date` and `end_date` are ISO 8601 datetimes marking the window you
    plan to work on the task (start work / finish work), distinct from
    `due_date` (the deadline).

    `percent_done` is a fraction despite the name, so a quarter done is 0.25 and not
    25. Vikunja does not validate it: 50 is stored as 50, not read as 50 percent and
    not clamped.

    `repeat_after` is a number of seconds, and repeating happens when the task is
    marked done: it reopens itself and moves its due date and reminders forward.
    `repeat_mode` is 0 to advance by `repeat_after`, 1 to repeat monthly and ignore
    `repeat_after`, or 2 to count from the day it was completed rather than from its
    previous dates.
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
    if percent_done is not None:
        payload["percent_done"] = percent_done
    if is_favorite is not None:
        payload["is_favorite"] = is_favorite
    if repeat_after is not None:
        payload["repeat_after"] = repeat_after
    if repeat_mode is not None:
        payload["repeat_mode"] = repeat_mode
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
    percent_done: float | None = None,
    is_favorite: bool | None = None,
    repeat_after: int | None = None,
    repeat_mode: int | None = None,
) -> dict:
    """Update a task. Only the fields you pass change. Use `done` to open/close it.

    v1 has no partial update, so there this reads the task and writes it back with
    your changes merged in, at the cost of one extra request. v2 is a single PATCH
    unless a description is involved.

    `due_date` is the deadline. `start_date` and `end_date` are ISO 8601 datetimes
    marking the window you plan to work on the task (start work / finish work).
    Pass an empty string to any of the three to clear it.

    `percent_done` is a fraction despite the name, so a quarter done is 0.25 and not
    25. Vikunja does not validate it: 50 is stored as 50, not read as 50 percent.

    `repeat_after` is a number of seconds, and setting it changes what `done` means
    for this task, which will reopen itself with its dates moved forward instead of
    staying closed. `repeat_mode` is 0 to advance by `repeat_after`, 1 to repeat
    monthly and ignore `repeat_after`, or 2 to count from the day it was completed.

    One wrinkle in what comes back: on v2 a partial update returns the description
    as the stored HTML, because v2 will not convert on a PATCH. Call `get_task` if
    you need it as Markdown.
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
    if percent_done is not None:
        payload["percent_done"] = percent_done
    if is_favorite is not None:
        payload["is_favorite"] = is_favorite
    if repeat_after is not None:
        payload["repeat_after"] = repeat_after
    if repeat_mode is not None:
        payload["repeat_mode"] = repeat_mode
    if not payload:
        raise ValueError("No fields to update")
    return await _write_task(task_id, payload)


async def _write_task(task_id: int, payload: dict[str, Any]) -> dict:
    """Send task changes whichever way this API version requires."""
    # Two separate reasons to read the task first, see _replace_task: on v1 because
    # a partial body would wipe the fields it omits, and on v2 because PATCH would
    # store a Markdown description verbatim. Everything else on v2 stays a cheap
    # partial update.
    if _version() == 1 or "description" in payload:
        return await _replace_task(task_id, payload)
    # No ?format=markdown here. v2 ignores it on a PATCH in both directions, not
    # just for the request body, so the task this returns carries its description as
    # the stored HTML. Verified against 2.5.0 by asking and getting HTML back
    # regardless. Sending a parameter the server discards would only suggest a
    # guarantee that does not hold.
    return await _request(_verb("update"), f"/tasks/{task_id}", json=payload)


@mcp.tool()
async def move_task(task_id: int, project_id: int) -> dict:
    """Move a task to another project. Needs write access to the target.

    Vikunja has no endpoint for this. A task's `project_id` is writable and setting
    it is the move, so this costs what an update costs: two requests on v1, one on
    v2.

    Labels, assignees, comments, relations and dates all come along. The task's
    project-local `identifier` does not: that is derived from the project it is in,
    so it is reassigned on arrival.
    """
    return await _write_task(task_id, {"project_id": project_id})


@mcp.tool()
async def duplicate_task(task_id: int) -> dict:
    """Copy a task, with its labels, assignees, attachments and reminders.

    The copy lands in the same project as the original and carries a `copiedfrom`
    relation back to it. Vikunja offers no way to duplicate straight into another
    project; call `move_task` on the copy for that.
    """
    return await _request(_verb("create"), f"/tasks/{task_id}/duplicate")


@mcp.tool()
async def bulk_update_tasks(
    task_ids: list[int], done: bool | None = None, priority: int | None = None
) -> dict:
    """Set `done` or `priority` on many tasks in one request.

    Only the fields you pass are written, on either API version. This endpoint takes
    the field names separately from the values, which makes it a genuine partial
    update even on v1, where updating a single task is not.

    You need write access to every project involved. If it is missing on even one,
    the whole request is refused and nothing changes.
    """
    values: dict[str, Any] = {}
    if done is not None:
        values["done"] = done
    if priority is not None:
        values["priority"] = priority
    if not values:
        raise ValueError("No fields to update")
    return await _request(
        _verb("replace"),
        "/tasks/bulk",
        json={"task_ids": task_ids, "fields": sorted(values), "values": values},
    )


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
    try:
        return await _request(
            _verb("replace"),
            f"/tasks/{task_id}",
            params=_md_params(),
            headers=headers,
            json=body,
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
    return await _request(_verb("update"), f"/tasks/{task_id}", json=payload)


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


# --- kanban -----------------------------------------------------------------
async def _kanban_view(project_id: int, view_id: int | None = None) -> dict:
    """Resolve which kanban view to act on.

    Buckets belong to a view rather than to the project, so every bucket tool needs
    one. Most projects have exactly one kanban view, so `view_id` is optional and
    the first kanban view is used. Views arrive ordered by position, which makes
    "first" the leftmost tab rather than an arbitrary pick.
    """
    views = _items(await _request("GET", f"/projects/{project_id}/views"))
    if view_id is None:
        for view in views:
            if view.get("view_kind") == "kanban":
                return view
        raise ValueError(f"project {project_id} has no kanban view")

    for view in views:
        if view.get("id") == view_id:
            if view.get("view_kind") != "kanban":
                raise ValueError(
                    f"view {view_id} of project {project_id} is a "
                    f"{view.get('view_kind')} view, and only a kanban view has buckets"
                )
            return view
    raise ValueError(f"project {project_id} has no view {view_id}")


def _board_path(project_id: int, view_id: int) -> str:
    """Where buckets come back with their tasks inside them.

    v1 groups by bucket on the view's own task endpoint. v2 answers that one flat
    even for a kanban view, and moved the grouped form to its own route, which v1
    does not have at all.
    """
    base = f"/projects/{project_id}/views/{view_id}"
    return f"{base}/buckets/tasks" if _version() == 2 else f"{base}/tasks"


@mcp.tool()
async def list_kanban_views(project_id: int) -> list[dict]:
    """List a project's kanban views, with the bucket ids that give them meaning.

    Most projects have one. Pass an `id` from here as `view_id` to the bucket tools
    to target a specific one.

    `bucket_configuration_mode` is `manual` when you arrange tasks yourself, or
    `filter` when Vikunja builds a bucket per filter, in which case moving a task
    between buckets is not a thing you can do.
    """
    data = await _request("GET", f"/projects/{project_id}/views")
    return [
        {
            "id": v["id"],
            "title": v.get("title"),
            "default_bucket_id": v.get("default_bucket_id"),
            "done_bucket_id": v.get("done_bucket_id"),
            "bucket_configuration_mode": v.get("bucket_configuration_mode"),
        }
        for v in _items(data)
        if v.get("view_kind") == "kanban"
    ]


@mcp.tool()
async def list_buckets(project_id: int, view_id: int | None = None) -> list[dict]:
    """List the columns of a project's kanban view, in board order.

    `limit` is the most tasks the bucket accepts, where 0 means no limit; a move into
    a full bucket is refused. Task counts are not here, because Vikunja does not
    populate them on this endpoint: `list_bucket_tasks` reports them.
    """
    view = await _kanban_view(project_id, view_id)
    buckets = _items(await _request("GET", f"/projects/{project_id}/views/{view['id']}/buckets"))
    # An unset default means the leftmost bucket, and these arrive ordered by
    # position, so that is the first one.
    default_id = view.get("default_bucket_id") or (buckets[0]["id"] if buckets else None)
    return [
        {
            "id": b["id"],
            "title": b.get("title"),
            "position": b.get("position"),
            "limit": b.get("limit"),
            "is_default_bucket": b["id"] == default_id,
            "is_done_bucket": b["id"] == view.get("done_bucket_id"),
        }
        for b in buckets
    ]


@mcp.tool()
async def create_bucket(
    project_id: int, title: str, view_id: int | None = None, limit: int | None = None
) -> dict:
    """Add a column to a project's kanban view. It goes on the right-hand end.

    `limit` caps how many tasks the column accepts, and moves into a full one are
    refused; leave it out, or pass 0, for no limit.
    """
    view = await _kanban_view(project_id, view_id)
    payload: dict[str, Any] = {"title": title}
    if limit is not None:
        payload["limit"] = limit
    return await _request(
        _verb("create"), f"/projects/{project_id}/views/{view['id']}/buckets", json=payload
    )


@mcp.tool()
async def delete_bucket(project_id: int, bucket_id: int, view_id: int | None = None) -> dict:
    """Delete a column from a project's kanban view.

    The tasks in it are not deleted: Vikunja moves them to the default bucket. A view
    keeps at least one column, so the last one cannot be removed.
    """
    view = await _kanban_view(project_id, view_id)
    return await _request(
        "DELETE", f"/projects/{project_id}/views/{view['id']}/buckets/{bucket_id}"
    )


@mcp.tool()
async def list_bucket_tasks(
    project_id: int, view_id: int | None = None, filter: str | None = None
) -> list[dict]:
    """List a kanban view's buckets with the tasks in them.

    `task_count` is the bucket's true size, which can exceed the tasks returned:
    Vikunja caps how many it sends per bucket. Narrow with `filter`, the same
    server-side syntax `list_tasks` takes, rather than paging.
    """
    view = await _kanban_view(project_id, view_id)
    params = {"filter": filter} if filter else {}
    try:
        data = await _request("GET", _board_path(project_id, view["id"]), params=params)
    except httpx.HTTPStatusError as err:
        if err.response.status_code == 401 and _version() == 2:
            raise RuntimeError(
                "Vikunja rejected the API token for the v2 buckets-with-tasks route, "
                "though it accepts the same token everywhere else and the v2 spec says "
                "this route takes one too. A token created before the route existed "
                "will not carry permission for it, so try a token created with full "
                "permissions. Failing that, /api/v1 serves the same data. Observed on "
                "Vikunja 2.5.0."
            ) from err
        raise
    return [
        {
            "id": b["id"],
            "title": b.get("title"),
            "task_count": b.get("count"),
            "tasks": [_task_summary(t) for t in (b.get("tasks") or [])],
        }
        for b in _items(data)
    ]


@mcp.tool()
async def list_task_buckets(task_id: int) -> list[dict]:
    """Report which bucket a task sits in, one entry per kanban view.

    A task holds a position in every kanban view of its project, so a project with
    two boards puts the task in two buckets. Usually there is one.

    The `bucket_id` on a task read any other way is 0, because that field is only
    meaningful inside a view, which is why this exists.
    """
    task = await _request("GET", f"/tasks/{task_id}", params={"expand": "buckets"})
    return [
        {
            "bucket_id": b.get("id"),
            "bucket_title": b.get("title"),
            "project_view_id": b.get("project_view_id"),
        }
        for b in (task.get("buckets") or [])
    ]


@mcp.tool()
async def move_task_to_bucket(task_id: int, bucket_id: int, view_id: int | None = None) -> dict:
    """Move a task into a kanban bucket. Re-sending the same bucket does nothing.

    This changes more than the column, and `list_kanban_views` tells you which
    bucket is which:

    - Moving into the done bucket marks the task done, and moving it out un-marks it.
    - A repeating task moved into the done bucket is reopened and sent to the default
      bucket instead, since being done is not a state it stays in.
    - A bucket at its task limit refuses the move.

    Only meaningful when the view's `bucket_configuration_mode` is `manual`. In
    `filter` mode a task's bucket follows the filters, not you.

    The project is read from the task rather than passed in, which costs a request
    and removes an argument that could contradict the task it was given.
    """
    task = await _request("GET", f"/tasks/{task_id}")
    project_id = task.get("project_id") if isinstance(task, dict) else None
    if not project_id:
        raise RuntimeError(
            f"could not read which project task {task_id} is in, so it was not moved"
        )
    view = await _kanban_view(project_id, view_id)
    return await _request(
        _verb("replace"),
        f"/projects/{project_id}/views/{view['id']}/buckets/{bucket_id}/tasks",
        json={"task_id": task_id},
    )


# --- relations --------------------------------------------------------------
# There is no list_relations: `get_task` already returns `related_tasks`, grouped
# by kind. The kind is passed through rather than checked against a local copy of
# the enum, the same way `filter` is: the server owns that vocabulary, and it
# explains itself when given something it does not recognise.
@mcp.tool()
async def add_relation(task_id: int, other_task_id: int, relation_kind: str = "related") -> dict:
    """Relate one task to another. Defaults to a plain, symmetric `related` link.

    Kinds: subtask, parenttask, related, duplicateof, duplicates, blocking,
    blocked, precedes, follows, copiedfrom, copiedto.

    `task_id` is the base task and `other_task_id` is the one being related to it,
    which is the direction that matters for the asymmetric kinds: `subtask` makes
    the other task a child of this one. Needs write access to the base task and
    read access to the other; they do not have to be in the same project.
    """
    return await _request(
        _verb("create"),
        f"/tasks/{task_id}/relations",
        json={"other_task_id": other_task_id, "relation_kind": relation_kind},
    )


@mcp.tool()
async def remove_relation(task_id: int, other_task_id: int, relation_kind: str = "related") -> dict:
    """Remove a relation between two tasks.

    The kind has to match the one the relation was created with; see
    `add_relation` for the list. `get_task` reports what a task currently has.
    """
    # The path carries all three values, and the API documents a body as required
    # here as well. Both are sent, built from the same arguments so they cannot
    # disagree with each other.
    return await _request(
        "DELETE",
        f"/tasks/{task_id}/relations/{relation_kind}/{other_task_id}",
        json={"other_task_id": other_task_id, "relation_kind": relation_kind},
    )


# --- labels -----------------------------------------------------------------
@mcp.tool()
async def list_labels() -> list[dict]:
    """List all labels."""
    data = await _request("GET", "/labels")
    return [{"id": x["id"], "title": x["title"]} for x in _items(data)]


@mcp.tool()
async def create_label(
    title: str, hex_color: str | None = None, description: str | None = None
) -> dict:
    """Create a label, which `add_label` can then attach to tasks.

    `hex_color` is six hex digits with no leading `#`, as `list_labels` reports them.
    """
    payload: dict[str, Any] = {"title": title}
    if hex_color is not None:
        payload["hex_color"] = hex_color
    if description is not None:
        payload["description"] = description
    return await _request(_verb("create"), "/labels", json=payload)


@mcp.tool()
async def delete_label(label_id: int) -> dict:
    """Delete a label everywhere. It comes off every task that carries it.

    To take a label off one task without destroying it, use `remove_label`.
    """
    return await _request("DELETE", f"/labels/{label_id}")


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
    # v2 honours ?format=markdown on a replace but silently ignores it on PATCH,
    # storing the Markdown verbatim in a field the UI renders as HTML. A comment has
    # one writable field, so replacing it and updating it are the same thing, and
    # replace is the variant that converts.
    return await _request(
        _verb("replace"),
        f"/tasks/{task_id}/comments/{comment_id}",
        params=_md_params(),
        json={"comment": comment},
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
