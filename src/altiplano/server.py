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
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("altiplano")

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


def _project_view_summary(v: dict) -> dict:
    return {
        "id": v.get("id"),
        "title": v.get("title"),
        "view_kind": v.get("view_kind"),
        "project_id": v.get("project_id"),
        "bucket_configuration_mode": v.get("bucket_configuration_mode"),
        "default_bucket_id": v.get("default_bucket_id"),
        "done_bucket_id": v.get("done_bucket_id"),
        "position": v.get("position"),
    }


def _label_titles(t: dict) -> list[str]:
    return [label.get("title") for label in (t.get("labels") or []) if label.get("title")]


def _kanban_task_summary(t: dict) -> dict:
    summary = _task_summary(t)
    summary.update(
        {
            "bucket_id": t.get("bucket_id"),
            "position": t.get("position"),
            "labels": _label_titles(t),
        }
    )
    return summary


def _bucket_summary(b: dict, include_tasks: bool = True) -> dict:
    summary = {
        "id": b.get("id"),
        "title": b.get("title"),
        "position": b.get("position"),
        "count": b.get("count"),
        "limit": b.get("limit"),
    }
    if include_tasks:
        summary["tasks"] = [_kanban_task_summary(t) for t in (b.get("tasks") or [])]
    return summary


async def _kanban_view_id(project_id: int, view_id: int | None = None) -> int:
    if view_id is not None:
        return view_id
    views = await _request("GET", f"/projects/{project_id}/views")
    for view in views or []:
        if view.get("view_kind") == "kanban":
            return view["id"]
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


@mcp.tool()
async def list_project_views(project_id: int) -> list[dict]:
    """List the views for a project, including the Kanban view id and bucket settings."""
    data = await _request("GET", f"/projects/{project_id}/views")
    return [_project_view_summary(v) for v in (data or [])]


@mcp.tool()
async def list_kanban_buckets(project_id: int, view_id: int | None = None) -> list[dict]:
    """List the Kanban buckets/stages for a project.

    If `view_id` is omitted, the first Kanban view for the project is used.
    """
    resolved_view_id = await _kanban_view_id(project_id, view_id)
    data = await _request("GET", f"/projects/{project_id}/views/{resolved_view_id}/buckets")
    return [_bucket_summary(b, include_tasks=False) for b in (data or [])]


@mcp.tool()
async def list_kanban(
    project_id: int,
    view_id: int | None = None,
    filter: str | None = "done = false",
    page: int = 1,
    per_page: int = 100,
) -> list[dict]:
    """List a project's Kanban stages with their tasks.

    If `view_id` is omitted, the first Kanban view for the project is used.
    By default only open tasks are returned. Pass `filter=None` to include completed tasks.
    """
    resolved_view_id = await _kanban_view_id(project_id, view_id)
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if filter:
        params["filter"] = filter
    data = await _request("GET", f"/projects/{project_id}/views/{resolved_view_id}/tasks", params=params)
    return [_bucket_summary(b) for b in (data or [])]


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
) -> dict:
    """Create a task in a project."""
    payload: dict[str, Any] = {"title": title}
    if description is not None:
        payload["description"] = description
    if priority is not None:
        payload["priority"] = priority
    if due_date is not None:
        payload["due_date"] = due_date
    return await _request("PUT", f"/projects/{project_id}/tasks", json=payload)


@mcp.tool()
async def update_task(
    task_id: int,
    title: str | None = None,
    description: str | None = None,
    done: bool | None = None,
    priority: int | None = None,
) -> dict:
    """Update a task. Only the fields you pass are changed. Use `done` to open/close it."""
    payload: dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if description is not None:
        payload["description"] = description
    if done is not None:
        payload["done"] = done
    if priority is not None:
        payload["priority"] = priority
    if not payload:
        raise ValueError("No fields to update")
    return await _request("POST", f"/tasks/{task_id}", json=payload)


@mcp.tool()
async def set_reminders(task_id: int, reminders: list[str]) -> dict:
    """Replace a task's reminders with the given ISO 8601 datetimes. Empty list clears them."""
    payload = {"reminders": [{"reminder": r} for r in reminders]}
    return await _request("POST", f"/tasks/{task_id}", json=payload)


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
