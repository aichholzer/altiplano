"""The MCP server instance, alone in a module of its own.

Every tool module needs `@mcp.tool()`, and `server` needs to import every tool
module so that decorator runs. Were the instance defined in `server`, those two
facts would be a circular import. Nothing of ours is imported here, so there is no
cycle to reason about.
"""

from mcp.server.mcpserver import MCPServer

from altiplano import __version__

# The version is declared so it appears in the MCP handshake. `uvx` can serve a
# cached build for a while after a release, and without this the running version
# is invisible: the only way to tell is to provoke a behaviour that changed.
mcp = MCPServer("altiplano", version=__version__)
