"""User and assignee tools."""

from altiplano.api import _items, _request, _verb, _version
from altiplano.app import mcp


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
