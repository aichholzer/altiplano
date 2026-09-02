"""The MCP server instance, alone in a module of its own.

Every tool module needs `@mcp.tool()`, and `server` needs to import every tool
module so that decorator runs. Were the instance defined in `server`, those two
facts would be a circular import. Nothing of ours is imported here, so there is no
cycle to reason about.
"""

from mcp.server.mcpserver import MCPServer

from altiplano import __version__

# Sent in the handshake, so a client can inject it on connect.
# `tests/test_guidance.py` caps its length.
INSTRUCTIONS = """\
Altiplano exposes a Vikunja instance as MCP tools.

Before writing anything:

- Ids are volatile. Resolve a project, label, kanban view, column or user by name
  with the matching list or search tool, then carry the id through the session.
  Never guess an id, and re-resolve one remembered from an earlier session.
- `search_tasks()` finds a task when its project is unknown. `list_tasks()` needs
  one.
- `delete_task()`, `delete_label()` and `delete_comment()` cannot be undone
  through this API. Confirm the target with `get_task()` first.
- `move_task_to_bucket()` writes state: the done column marks a task done, and
  moving it out reopens it. To close a task, call `update_task()` with `done: true`.
- `create_task()` takes no `done` parameter, so recording finished work means a
  `create_task()` call followed by an `update_task()` call.
- `filter` and `sort_by` take Vikunja's server-side syntax, for example
  `done = false && priority >= 4`.

Fetch the `altiplano_guide` prompt for the rest: cross-tool sequencing, batching,
kanban behaviour, relations, and the differences between the v1 and v2 APIs.
"""

# The version is declared so it appears in the MCP handshake. `uvx` can serve a
# cached build for a while after a release, and without this the running version
# is invisible: the only way to tell is to provoke a behaviour that changed.
mcp = MCPServer("altiplano", version=__version__, instructions=INSTRUCTIONS)
