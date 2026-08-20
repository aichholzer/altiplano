# Changelog

All notable changes to this project are documented here.

## [0.5.0]

### Added

- `update_comment` (task_id, comment_id, comment) and `delete_comment`
  (task_id, comment_id). Comments could be listed and created but never
  corrected or removed, so a typo in a comment was permanent from the client
  side. Both wrap endpoints Vikunja already exposed; pass the `id` returned by
  `list_comments` as `comment_id`.

## [0.4.0]

### Breaking

- Requires the MCP Python SDK 2.x (`mcp>=2.0.0,<3`). The SDK 1.x line is no
  longer supported.

### Fixed

- The server no longer fails to start with
  `ModuleNotFoundError: No module named 'mcp.server.fastmcp'`. SDK 2.0 removed
  `mcp.server.fastmcp` and renamed `FastMCP` to `MCPServer` under
  `mcp.server.mcpserver`; the import and construction now use the new name. The
  open-ended `mcp>=1.2.0` requirement meant a fresh `uvx altiplano` resolved
  SDK 2.x against code written for 1.x, so every launch broke.
