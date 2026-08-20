"""Minimal Vikunja MCP server.

Filtering and sorting are passed straight to the Vikunja API (server-side),
so there is no client-side filtering engine to get wrong.

Credentials are resolved without storing secrets in a shared mcp.json:
  1. Environment variables VIKUNJA_URL / VIKUNJA_API_TOKEN (preferred).
  2. A per-device file of KEY=VALUE lines (default ~/.config/altiplano/env,
     override with ALTIPLANO_CONFIG). Keep it chmod 600.
"""

import os
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


def _from_file(key: str) -> str | None:
    try:
        for line in _CONFIG_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    except FileNotFoundError:
        return None
    return None


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


async def _request(method: str, path: str, **kwargs: Any) -> Any:
    async with httpx.AsyncClient(base_url=_base(), headers=_headers(), timeout=30) as client:
        r = await client.request(method, path, **kwargs)
        r.raise_for_status()
        if r.status_code == 204 or not r.content:
            return {"ok": True}
        return r.json()


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
##
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
##
@mcp.tool()
async def list_tasks(
    project_id: int,
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
        payload["due_date"] = due_date
    if start_date is not None:
        payload["start_date"] = start_date
    if end_date is not None:
        payload["end_date"] = end_date
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
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Update a task. Only the fields you pass are changed. Use `done` to open/close it.

    `start_date` and `end_date` are ISO 8601 datetimes marking the window you
    plan to work on the task (start work / finish work).
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
    if start_date is not None:
        payload["start_date"] = start_date
    if end_date is not None:
        payload["end_date"] = end_date
    if not payload:
        raise ValueError("No fields to update")
    # A description has to go through a full replace on v2, because PATCH ignores
    # ?format=markdown and would store the Markdown verbatim. Everything else
    # stays a cheap partial update.
    if "description" in payload and _version() == 2:
        return await _replace_task(task_id, payload)
    return await _request(_verb("update"), f"/tasks/{task_id}", json=payload)


async def _replace_task(task_id: int, changes: dict[str, Any]) -> dict:
    """Apply `changes` through a full replace, so v2 converts Markdown for us.

    v2 only honours the Markdown parameter on create and replace, never on a
    partial update. Replacing resets any field it is not given, so the current
    task is read first and the changes layered on top. The read asks for Markdown
    as well, so a description we are not touching is written back in the same form
    it came in rather than being double-converted.

    Verified lossless across labels, assignees, reminders, dates, colour,
    priority and percent_done. The cost is an extra request, and a lost update if
    something else writes to the task in between.
    """
    current = await _request("GET", f"/tasks/{task_id}", params=_md_params())
    body = {k: v for k, v in current.items() if k != "$schema"}
    body.update(changes)
    return await _request("PUT", f"/tasks/{task_id}", params=_md_params(), json=body)


@mcp.tool()
async def set_reminders(task_id: int, reminders: list[str]) -> dict:
    """Replace a task's reminders with the given ISO 8601 datetimes. Empty list clears them."""
    payload = {"reminders": [{"reminder": r} for r in reminders]}
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


# --- labels -----------------------------------------------------------------
##
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
##
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
##
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
