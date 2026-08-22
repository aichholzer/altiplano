"""Project tools: the boards tasks live on."""

from typing import Any

from altiplano.api import _items, _md_params, _request, _verb
from altiplano.app import mcp


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
