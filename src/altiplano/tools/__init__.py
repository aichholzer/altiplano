"""One module per section of tools, matching the sections the README lists.

Importing a module here registers its tools on the shared instance: that is what
`@mcp.tool()` does at import time. `altiplano.server` imports all of them for
exactly that reason.
"""
