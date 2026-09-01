"""Minimal Vikunja MCP server: the entry point.

Filtering and sorting are passed straight to the Vikunja API (server-side), so
there is no client-side filtering engine to get wrong.

There is no logic here. Importing the tool modules is what registers their tools,
since `@mcp.tool()` runs at import time, and `main` serves them. The parts are:

  `app`      the MCP instance the tools decorate
  `config`   where credentials come from
  `api`      the version differences, the request layer, response shaping
  `tools/`   one module per section, as listed in the README

Credentials are resolved without storing secrets in a shared mcp.json; see
`config` for the order they are looked up in.
"""

from altiplano.app import mcp
from altiplano.tools import (
    assignees,
    comments,
    kanban,
    labels,
    projects,
    relations,
    tasks,
)

# Named so the imports above read as deliberate. They are here for the registration
# side effect, and a linter would otherwise call them unused.
__all__ = [
    "assignees",
    "comments",
    "kanban",
    "labels",
    "main",
    "mcp",
    "projects",
    "relations",
    "tasks",
]


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
