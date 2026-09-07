"""Label tools."""

from typing import Any

import httpx

from altiplano.api import _decode, _items, _md_params, _request, _send, _verb, _version
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

    `description` is rich text, written as Markdown. Vikunja stores it as HTML and
    v2 converts on the way in.
    """
    payload: dict[str, Any] = {"title": title}
    if hex_color is not None:
        payload["hex_color"] = hex_color
    if description is not None:
        payload["description"] = description
    return await _request(_verb("create"), "/labels", params=_md_params(), json=payload)


@mcp.tool()
async def update_label(
    label_id: int,
    title: str | None = None,
    hex_color: str | None = None,
    description: str | None = None,
) -> dict:
    """Update a label. Only the fields you pass change. Every task carrying it sees
    the change.

    `hex_color` is six hex digits with no leading `#`, and an empty string clears it.
    `description` is Markdown.

    v1 has no partial update, and neither does a description change on v2. Both read
    the label and write it back with your changes merged in, at the cost of one extra
    request. Everything else on v2 is a single PATCH.
    """
    payload: dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if hex_color is not None:
        payload["hex_color"] = hex_color
    if description is not None:
        payload["description"] = description
    if not payload:
        raise ValueError("No fields to update")

    if _version() == 1 or "description" in payload:
        return await _replace_label(label_id, payload)
    return await _request(_verb("update"), f"/labels/{label_id}", json=payload)


async def _replace_label(label_id: int, changes: dict[str, Any]) -> dict:
    """Apply `changes` by reading the label and writing it back whole.

    On v1, `POST /labels/{id}` is a replace. A title-only body clears `hex_color` and
    `description`, and renaming a label would strip its colour.

    On v2 there is `PATCH`, which silently ignores ?format=markdown and stores the
    Markdown verbatim into a field rendered as HTML. A description therefore goes
    through `PUT` there, which converts.

    v2 returns an ETag on a single-label read and honours If-Match. A label that changed
    in between fails with 412 and is never silently overwritten. v1 offers no ETag, no
    precondition is sent, and that window stays open there.
    """
    read = await _send("GET", f"/labels/{label_id}", params=_md_params())
    current = _decode(read)
    if not isinstance(current, dict) or "id" not in current:
        # A bodyless response arrives as a status dict. A replace built from that
        # would wipe the label.
        raise RuntimeError(f"the API did not return label {label_id}. It was not updated.")
    body = {k: v for k, v in current.items() if k != "$schema"}
    body.update(changes)

    headers = {}
    etag = read.headers.get("ETag")
    if etag:
        headers["If-Match"] = etag
    try:
        return await _request(
            _verb("replace"),
            f"/labels/{label_id}",
            params=_md_params(),
            headers=headers,
            json=body,
        )
    except httpx.HTTPStatusError as err:
        if err.response.status_code == 412:
            raise RuntimeError(
                f"label {label_id} changed while this update was being prepared. Nothing was "
                "written. Read it again and retry."
            ) from err
        raise


@mcp.tool()
async def delete_label(label_id: int) -> dict:
    """Delete a label everywhere. It comes off every task that has it.

    `remove_label` takes a label off one task and leaves the label itself alone.
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
