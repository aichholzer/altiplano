"""Comment tools."""

from altiplano.api import _items, _md_params, _request, _verb
from altiplano.app import mcp


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
    # one writable field. Replacing it and updating it are the same operation, and
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
