# Changelog

All notable changes to this project are documented here.

## [0.5.1]

### Added

- A manually triggered GitHub Actions workflow that publishes to PyPI. It runs
  the tests on Python 3.10 and 3.13, refuses to republish a version that already
  exists on PyPI, builds with `--no-sources`, and imports the built wheel before
  uploading, so a packaging mistake fails the run instead of reaching users.
- A smoke test suite covering module import, tool registration, console script
  resolution, and version agreement between `pyproject.toml` and `__init__.py`.
  0.4.0 shipped an import error that broke every launch; these are the checks
  that would have caught it. `pytest` is now a `dev` dependency group.

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
