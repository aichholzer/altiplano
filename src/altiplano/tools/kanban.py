"""Kanban tools: views, their buckets, and which bucket a task sits in.

Buckets belong to a view, which is why every tool here resolves one first.
"""

from typing import Any

import httpx

from altiplano.api import _items, _request, _task_summary, _verb, _version
from altiplano.app import mcp


async def _kanban_view(project_id: int, view_id: int | None = None) -> dict:
    """Resolve which kanban view to act on.

    Buckets belong to a view, so every bucket tool needs one. Most projects have
    exactly one kanban view, so `view_id` is optional and the first kanban view is
    used. Views arrive ordered by position, which makes "first" the leftmost tab.
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
    Vikunja caps how many it sends per bucket. To reach the rest, narrow with
    `filter`, the same server-side syntax `list_tasks` takes.
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
      bucket, since being done is not a state it stays in.
    - A bucket at its task limit refuses the move.

    Only meaningful when the view's `bucket_configuration_mode` is `manual`. In
    `filter` mode the filters decide which bucket a task sits in.

    The project is read from the task, which costs a request and removes an
    argument that could contradict the task it was given.
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
