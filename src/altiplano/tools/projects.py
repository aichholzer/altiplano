"""Project tools: the boards tasks live on."""

from typing import Any

from altiplano.api import _items, _md_params, _request, _verb, _version
from altiplano.app import mcp


@mcp.tool()
async def list_projects(include_archived: bool = False) -> list[dict]:
    """List all projects (boards). `parent_project_id` shows sub-project nesting.

    Vikunja leaves archived projects out of this endpoint. `include_archived` adds
    them back alongside the active ones, and `is_archived` on each result says which
    is which. An archived project is otherwise unreachable through this tool, and its
    id is what `update_project` needs to bring it back.
    """
    params = {"is_archived": "true"} if include_archived else {}
    data = await _request("GET", "/projects", params=params)
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


@mcp.tool()
async def update_project(
    project_id: int,
    title: str | None = None,
    description: str | None = None,
    parent_project_id: int | None = None,
    is_archived: bool | None = None,
    hex_color: str | None = None,
) -> dict:
    """Update a project. Only the fields you pass change.

    `is_archived` archives and unarchives, and Vikunja has no separate archive
    endpoint. Archiving has two consequences worth knowing before using it. The
    project drops out of `list_projects` unless that call is given
    `include_archived: true`. And Vikunja then refuses every other edit to it, and to
    the tasks in it, with a 412 naming the archive. Unarchive it before changing
    anything else on it.

    `parent_project_id` re-parents the project, making it a sub-project of the id
    given. `hex_color` is six hex digits with no leading `#`, and an empty string
    clears it. `description` is Markdown.

    v1 has no partial update, and neither does a description change on v2. Both read
    the project and write it back with your changes merged in, at the cost of one
    extra request. Everything else on v2 is a single PATCH.
    """
    payload: dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if description is not None:
        payload["description"] = description
    if parent_project_id is not None:
        payload["parent_project_id"] = parent_project_id
    if is_archived is not None:
        payload["is_archived"] = is_archived
    if hex_color is not None:
        payload["hex_color"] = hex_color
    if not payload:
        raise ValueError("No fields to update")

    # Two separate reasons to read the project first, see _replace_project: on v1
    # because a partial body resets fields it omits, and on v2 because PATCH would
    # store a Markdown description verbatim.
    if _version() == 1 or "description" in payload:
        return await _replace_project(project_id, payload)
    return await _request(_verb("update"), f"/projects/{project_id}", json=payload)


async def _replace_project(project_id: int, changes: dict[str, Any]) -> dict:
    """Apply `changes` by reading the project and writing it back whole.

    There is a reason per API version, mirroring `_replace_task`.

    On v1, `POST /projects/{id}` resets some of the fields a body omits, and one of
    them is `is_archived`: a title-only write to an archived project un-archives it.
    Go's zero value for a bool is false, and Vikunja cannot tell an omitted flag from
    one set to false. `hex_color` clears the same way. That endpoint also rejects a
    body with no title at all, answering 412 with code 2002.

    On v2 there is `PATCH`, which silently ignores ?format=markdown and stores the
    Markdown verbatim into a field rendered as HTML. A description therefore goes
    through `PUT` there, which converts. That verb wants a title too, answering 422
    without one.

    The read supplies the title in both cases, and merging keeps every field the
    caller did not name.

    Neither version returns an ETag on a project read. No precondition is sent, and the
    lost-update window stays open here. A v2 task write can close it; a project write
    cannot.
    """
    current = await _request("GET", f"/projects/{project_id}", params=_md_params())
    if not isinstance(current, dict) or "id" not in current:
        # A bodyless response arrives as a status dict. A replace built from that
        # would wipe the project.
        raise RuntimeError(f"the API did not return project {project_id}. It was not updated.")
    body = {k: v for k, v in current.items() if k != "$schema"}
    body.update(changes)
    return await _request(
        _verb("replace"), f"/projects/{project_id}", params=_md_params(), json=body
    )


@mcp.tool()
async def delete_project(project_id: int) -> dict:
    """Delete a project, everything in it, and every project under it.

    This cascades. Deleting a parent takes its sub-projects, every task in all of
    them, and each task's comments, labels, and assignees. Checked against Vikunja
    2.5.0 with a parent, one sub-project, and a task: all three ids read 404
    afterwards.

    Vikunja soft-deletes and documents a 30 day retention window, while exposing no
    endpoint to list or restore anything deleted. Through this API the call is
    permanent. Confirm the id with `list_projects` first, and look there for a
    `parent_project_id` matching this one: any project that names it goes too.

    To put a project out of the way and keep it, call `update_project` with
    `is_archived: true`.
    """
    return await _request("DELETE", f"/projects/{project_id}")
