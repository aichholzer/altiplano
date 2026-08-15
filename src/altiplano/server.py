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

mcp = MCPServer("altiplano")

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


async def _request(method: str, path: str, **kwargs: Any) -> Any:
    async with httpx.AsyncClient(base_url=_base(), headers=_headers(), timeout=30) as client:
        r = await client.request(method, path, **kwargs)
        r.raise_for_status()
        if r.status_code == 204 or not r.content:
            return {"ok": True}
        return r.json()


def _task_summary(t: dict) -> dict:
    return {
        "id": t.get("id"),
        "identifier": t.get("identifier"),
        "title": t.get("title"),
        "done": t.get("done"),
        "priority": t.get("priority"),
    }


async def _patch_task(task_id: int, changes: dict[str, Any]) -> dict:
    """Apply `changes` to a task, preserving every other field.

    Vikunja's `POST /tasks/{id}` replaces the whole task rather than merging,
    so sending only the changed keys silently resets every field omitted from
    the payload (e.g. priority drops to 0 if you only pass `description`).
    Fetching the current task first and posting it back with `changes`
    applied avoids that.
    """
    task = await _request("GET", f"/tasks/{task_id}")
    task.update(changes)
    return await _request("POST", f"/tasks/{task_id}", json=task)


async def _kanban_view(project_id: int, view_id: int | None = None) -> dict:
    """Resolve a project's kanban view.

    Defaults to the first kanban view. Pass `view_id` (from
    `list_kanban_views`) to target a specific one when a project has several.
    Raises if the project has no kanban view, or no view matching `view_id`.
    """
    views = await _request("GET", f"/projects/{project_id}/views")
    if view_id is not None:
        for v in views or []:
            if v.get("id") == view_id:
                return v
        raise ValueError(f"Project {project_id} has no view {view_id}")
    for v in views or []:
        if v.get("view_kind") == "kanban":
            return v
    raise ValueError(f"Project {project_id} has no kanban view")


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
        for p in (data or [])
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
    return await _request("PUT", "/projects", json=payload)


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
    return [_task_summary(t) for t in (data or [])]


@mcp.tool()
async def get_task(task_id: int) -> dict:
    """Get a single task with full detail."""
    return await _request("GET", f"/tasks/{task_id}")


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

    `percent_done` is 0.0-1.0. `repeat_after` is a number of seconds; `repeat_mode`
    is 0 (repeat after `repeat_after` from the due date), 1 (monthly), or 2 (repeat
    after `repeat_after` from the current date).
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
    if percent_done is not None:
        payload["percent_done"] = percent_done
    if is_favorite is not None:
        payload["is_favorite"] = is_favorite
    if repeat_after is not None:
        payload["repeat_after"] = repeat_after
    if repeat_mode is not None:
        payload["repeat_mode"] = repeat_mode
    return await _request("PUT", f"/projects/{project_id}/tasks", json=payload)


@mcp.tool()
async def update_task(
    task_id: int,
    title: str | None = None,
    description: str | None = None,
    done: bool | None = None,
    priority: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    percent_done: float | None = None,
    is_favorite: bool | None = None,
    repeat_after: int | None = None,
    repeat_mode: int | None = None,
) -> dict:
    """Update a task. Only the fields you pass are changed. Use `done` to open/close it.

    `start_date` and `end_date` are ISO 8601 datetimes marking the window you
    plan to work on the task (start work / finish work).

    `percent_done` is 0.0-1.0. `repeat_after` is a number of seconds; `repeat_mode`
    is 0 (repeat after `repeat_after` from the due date), 1 (monthly), or 2 (repeat
    after `repeat_after` from the current date).
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
    return await _request("POST", f"/tasks/{task_id}", json=payload)


@mcp.tool()
async def set_reminders(task_id: int, reminders: list[str]) -> dict:
    """Replace a task's reminders with the given ISO 8601 datetimes. Empty list clears them."""
    payload = {"reminders": [{"reminder": r} for r in reminders]}
    return await _request("POST", f"/tasks/{task_id}", json=payload)


# --- kanban -------------------------------------------------------------
##
@mcp.tool()
async def list_kanban_views(project_id: int) -> list[dict]:
    """List a project's kanban views. Most projects have exactly one.

    When there's more than one, pass a view's `id` as `view_id` to
    `list_buckets`, `list_bucket_tasks`, or `move_task_to_bucket` to target it.
    """
    views = await _request("GET", f"/projects/{project_id}/views")
    return [
        {"id": v["id"], "title": v.get("title")}
        for v in (views or [])
        if v.get("view_kind") == "kanban"
    ]


@mcp.tool()
async def list_buckets(project_id: int, view_id: int | None = None) -> list[dict]:
    """List the kanban columns (buckets) of a project's kanban view.

    If the project has multiple kanban views, pass `view_id` (from
    `list_kanban_views`) to target a specific one; defaults to the first.
    """
    view = await _kanban_view(project_id, view_id)
    buckets = await _request("GET", f"/projects/{project_id}/views/{view['id']}/buckets")
    # The plain buckets endpoint never populates `count`; task counts are only
    # filled in by the view tasks endpoint, so fetch that (1 task/bucket is
    # enough) purely to read counts.
    counted = await _request(
        "GET",
        f"/projects/{project_id}/views/{view['id']}/tasks",
        params={"per_page": 1},
    )
    counts = {b["id"]: b.get("count") for b in (counted or [])}
    buckets = buckets or []
    default_bucket_id = view.get("default_bucket_id")
    if not default_bucket_id and buckets:
        # 0/unset means "leftmost bucket" rather than "no default".
        default_bucket_id = min(buckets, key=lambda b: b.get("position", 0))["id"]
    return [
        {
            "id": b["id"],
            "title": b["title"],
            "position": b.get("position"),
            "count": counts.get(b["id"]),
            "is_default_bucket": b["id"] == default_bucket_id,
            "is_done_bucket": b["id"] == view.get("done_bucket_id"),
        }
        for b in buckets
    ]


@mcp.tool()
async def list_bucket_tasks(
    project_id: int,
    view_id: int | None = None,
    filter: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> list[dict]:
    """List tasks grouped by kanban bucket for a project's kanban view.

    Vikunja caps this endpoint at 50 tasks per bucket per page; a bucket whose
    `task_count` exceeds `len(tasks)` has more tasks on later pages. Use
    `page`/`per_page` to page through a large bucket, and `filter` (same
    syntax as `list_tasks`) to narrow results instead.

    If the project has multiple kanban views, pass `view_id` (from
    `list_kanban_views`) to target a specific one; defaults to the first.
    """
    view = await _kanban_view(project_id, view_id)
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if filter:
        params["filter"] = filter
    data = await _request(
        "GET", f"/projects/{project_id}/views/{view['id']}/tasks", params=params
    )
    return [
        {
            "id": b["id"],
            "title": b["title"],
            "task_count": b.get("count"),
            "tasks": [_task_summary(t) for t in (b.get("tasks") or [])],
        }
        for b in (data or [])
    ]


@mcp.tool()
async def get_task_bucket(task_id: int) -> dict:
    """Report the kanban bucket a task currently sits in."""
    task = await _request("GET", f"/tasks/{task_id}")
    project_id = task["project_id"]
    view = await _kanban_view(project_id)
    data = await _request(
        "GET",
        f"/projects/{project_id}/views/{view['id']}/tasks",
        params={"filter": f"id = {task_id}"},
    )
    for b in data or []:
        for t in b.get("tasks") or []:
            if t.get("id") == task_id:
                return {
                    "bucket_id": b["id"],
                    "bucket_title": b["title"],
                    "project_view_id": view["id"],
                }
    raise ValueError(f"Task {task_id} was not found on project {project_id}'s kanban board")


@mcp.tool()
async def move_task_to_bucket(
    project_id: int, task_id: int, bucket_id: int, view_id: int | None = None
) -> dict:
    """Move a task into a kanban bucket.

    Moving a task into the view's done bucket marks it `done=true`; moving it
    out of the done bucket clears `done`. If the project has multiple kanban
    views, pass `view_id` (from `list_kanban_views`) to match the view
    `bucket_id` belongs to; defaults to the first.
    """
    view = await _kanban_view(project_id, view_id)
    return await _request(
        "POST",
        f"/projects/{project_id}/views/{view['id']}/buckets/{bucket_id}/tasks",
        json={"task_id": task_id},
    )


# --- labels -----------------------------------------------------------------
##
@mcp.tool()
async def list_labels() -> list[dict]:
    """List all labels."""
    data = await _request("GET", "/labels")
    return [{"id": x["id"], "title": x["title"]} for x in (data or [])]


@mcp.tool()
async def add_label(task_id: int, label_id: int) -> dict:
    """Attach a label to a task."""
    return await _request("PUT", f"/tasks/{task_id}/labels", json={"label_id": label_id})


@mcp.tool()
async def remove_label(task_id: int, label_id: int) -> dict:
    """Remove a label from a task."""
    return await _request("DELETE", f"/tasks/{task_id}/labels/{label_id}")


# --- comments ---------------------------------------------------------------
##
@mcp.tool()
async def list_comments(task_id: int) -> list[dict]:
    """List comments on a task."""
    data = await _request("GET", f"/tasks/{task_id}/comments")
    return [
        {"id": c.get("id"), "comment": c.get("comment"), "author": (c.get("author") or {}).get("username")}
        for c in (data or [])
    ]


@mcp.tool()
async def add_comment(task_id: int, comment: str) -> dict:
    """Add a comment to a task."""
    return await _request("PUT", f"/tasks/{task_id}/comments", json={"comment": comment})


# --- users / assignees ------------------------------------------------------
##
@mcp.tool()
async def search_users(query: str) -> list[dict]:
    """Search users by name or username. Use this to find a user_id for assignees."""
    data = await _request("GET", "/users", params={"s": query})
    return [{"id": u.get("id"), "username": u.get("username"), "name": u.get("name")} for u in (data or [])]


@mcp.tool()
async def list_assignees(task_id: int) -> list[dict]:
    """List the users assigned to a task."""
    data = await _request("GET", f"/tasks/{task_id}/assignees")
    return [{"id": u.get("id"), "username": u.get("username")} for u in (data or [])]


@mcp.tool()
async def add_assignee(task_id: int, user_id: int) -> dict:
    """Assign a user to a task."""
    return await _request("PUT", f"/tasks/{task_id}/assignees", json={"user_id": user_id})


@mcp.tool()
async def remove_assignee(task_id: int, user_id: int) -> dict:
    """Unassign a user from a task."""
    return await _request("DELETE", f"/tasks/{task_id}/assignees/{user_id}")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
