"""Task tools, and the write paths the API version dictates.

`_replace_task` and `_write_task` live here, because the read-then-merge they
implement is a fact about tasks specifically: no other resource has a field that
only converts on a full replace.
"""

from typing import Any

import httpx

from altiplano.api import (
    _date,
    _decode,
    _items,
    _md_params,
    _request,
    _send,
    _task_summary,
    _verb,
    _version,
)
from altiplano.app import mcp


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


# The optional fields a new task takes. `create_task` accepts them as arguments and
# `bulk_create_tasks` accepts them per entry, so they are named once here: a second
# list would drift, and a field missing from it would be dropped in silence.
_NEW_TASK_FIELDS = (
    "description",
    "priority",
    "due_date",
    "start_date",
    "end_date",
    "percent_done",
    "is_favorite",
    "repeat_after",
    "repeat_mode",
)
_DATE_FIELDS = frozenset({"due_date", "start_date", "end_date"})


def _new_task(title: str, fields: dict[str, Any]) -> dict[str, Any]:
    """The body for one new task: its title, plus whichever fields were given.

    `None` means leave the field out. Any other falsy value is kept, because 0 and
    False are how percent_done, is_favorite and the repeat fields are turned off,
    and a truthiness check here would drop exactly those.
    """
    payload: dict[str, Any] = {"title": title}
    for name in _NEW_TASK_FIELDS:
        value = fields.get(name)
        if value is None:
            continue
        payload[name] = _date(value) if name in _DATE_FIELDS else value
    return payload


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
    `repeat_after`, or 2 to count from the day it was completed. Give a repeating
    task a `due_date`: it reopens whether or not there is a date to advance, so one
    with no dates cannot be closed at all.
    """
    payload = _new_task(
        title,
        {
            "description": description,
            "priority": priority,
            "due_date": due_date,
            "start_date": start_date,
            "end_date": end_date,
            "percent_done": percent_done,
            "is_favorite": is_favorite,
            "repeat_after": repeat_after,
            "repeat_mode": repeat_mode,
        },
    )
    return await _request(
        _verb("create"), f"/projects/{project_id}/tasks", params=_md_params(), json=payload
    )


def _bulk_entry(index: int, entry: Any) -> dict[str, Any]:
    """One entry of a bulk create, checked while its position is still known."""
    if not isinstance(entry, dict):
        raise ValueError(f"tasks[{index}] is not an object")
    unsupported = sorted(set(entry) - {"title", *_NEW_TASK_FIELDS})
    if unsupported:
        raise ValueError(f"tasks[{index}] has unsupported fields: {', '.join(unsupported)}")
    title = entry.get("title")
    if not title:
        raise ValueError(f"tasks[{index}] has no title")
    return _new_task(title, entry)


@mcp.tool()
async def bulk_create_tasks(project_id: int, tasks: list[dict[str, Any]]) -> list[dict]:
    """Create several tasks in one project, in one request. Needs the v2 API.

    Vikunja creates the batch atomically: if one entry is invalid then none are
    created, and the error names the entry that failed. They also keep the order
    they were given, which is the reason to use this over a loop of `create_task`
    calls: those race each other, so a numbered plan can come back shuffled, and a
    failure halfway through leaves the rest uncreated.

    Each entry is an object taking the same fields as `create_task`. `title` is
    required; `description`, `priority`, `due_date`, `start_date`, `end_date`,
    `percent_done`, `is_favorite`, `repeat_after` and `repeat_mode` are optional and
    mean what they do there, including an empty string to clear a date. Any other
    key is refused, because a dropped one reads as a task created with a date or a
    priority it never got. Vikunja caps a batch at 100.

    Returns a summary per created task, in creation order. Call `get_task` for the
    full detail of one.
    """
    if _version() == 1:
        raise RuntimeError(
            "bulk_create_tasks needs the v2 API: Vikunja added this endpoint in 2.5.0 and it "
            "exists on v2 only. Point VIKUNJA_URL at /api/v2, or create the tasks one at a "
            "time with create_task."
        )
    if not tasks:
        raise ValueError("No tasks to create")
    body = [_bulk_entry(i, entry) for i, entry in enumerate(tasks)]
    data = await _request(
        _verb("create"),
        f"/projects/{project_id}/tasks/bulk",
        params=_md_params(),
        json={"tasks": body},
    )
    created = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(created, list):
        # A bodyless response arrives as a status dict. The tasks were most likely
        # created, so reporting none of them would be worse than saying so.
        raise RuntimeError("the API did not return the created tasks, so they cannot be listed")
    return [_task_summary(t) for t in created]


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
    for this task, which will reopen itself with its dates moved forward.
    `repeat_mode` is 0 to advance by `repeat_after`, 1 to repeat monthly and ignore
    `repeat_after`, or 2 to count from the day it was completed.
    A repeating task with no dates cannot be closed: it reopens regardless.

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
    back in the form it came in, with no second conversion. Verified lossless
    across labels, assignees, reminders, dates, colour, priority and percent_done.

    The lost update this opens is caught where the server allows it: v2 returns an
    ETag on a single-resource read and honours If-Match, so a task that changed in
    between fails with 412 and is never silently overwritten. v1 offers no
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
