"""Label tools."""

from typing import Any

from altiplano.api import _items, _request, _verb
from altiplano.app import mcp


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
