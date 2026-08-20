# Changelog

All notable changes to this project are documented here.

## [0.7.1]

### Added

- The server declares its version, so it appears as `serverInfo.version` in the
  MCP handshake and clients can show which build they launched.

  This exists because `uvx` can serve a cached build for some time after a
  release, and until now the running version was invisible: the only way to tell
  was to provoke a behaviour that had changed between releases. That is a poor
  diagnostic, and it cost several restart cycles to work out that a client was
  still on the previous version.

## [0.7.0]

### Added

- Descriptions and comments are exchanged as Markdown on v2, so callers no longer
  write HTML by hand. `create_task`, `update_task`, `create_project`,
  `add_comment`, `update_comment`, `get_task` and `list_comments` all speak
  Markdown; Vikunja converts to the HTML it stores and back again, and resolves
  `@mentions` while doing so. v1 has no such facility and is unchanged.

### Changed

- A description change on v2 now reads the task and writes it back whole, rather
  than sending a partial update. This is not gratuitous: v2 honours the Markdown
  parameter on create and on replace but silently ignores it on `PATCH`, returning
  200 while storing the Markdown verbatim into a field rendered as HTML. A partial
  update would therefore have corrupted the field rather than failing. Updates
  that carry no description are untouched and remain a single request.

  The cost is one extra request when a description changes, and a lost update if
  something else writes to the same task in between. Reading first was verified
  lossless across labels, assignees, reminders, dates, colour, priority and
  percent done.

- `update_comment` uses `PUT` on v2 instead of `PATCH`, for the same reason. A
  comment has a single writable field, so replacing and updating it are the same
  operation, and only the replace converts Markdown.

## [0.6.0]

### Added

- Support for the Vikunja v2 API alongside v1. The version is taken from the URL
  you configure: point `VIKUNJA_URL` at `/api/v2` and you get v2, anything else
  gets v1. No new setting, no probing, and no extra request at startup. Older
  servers keep working unchanged, which matters because v2 only exists from
  Vikunja 2.4.0.

  Every tool takes the same arguments and returns the same shapes on both. The
  differences absorbed internally are the create verb (`PUT` on v1, `POST` on v2),
  the update verb (`POST` on v1, `PATCH` on v2), the collection envelope that v2
  wraps results in, and the user search parameter, renamed from `s` to `q`.

  Paths are identical across the two versions for everything this server does,
  which is what keeps the change small.

### Changed

- `_items` unwraps a v2 pagination envelope as well as a v1 bare array. It
  branches on the shape of the response rather than the configured version, so a
  mismatch between the two degrades gracefully instead of breaking, and the
  protection added in 0.5.4 against treating a bodyless response as an empty
  collection still holds.

## [0.5.6]

### Changed

- `list_assignees` reads `GET /tasks/{id}/assignees` again, reverting the 0.5.5
  workaround. That endpoint was broken server-side on Vikunja v2.3.0 and works on
  v2.5.0, so the workaround now costs more than it saves: fetching the whole task
  to read a short user list transferred 3604 bytes where the dedicated endpoint
  returns 157. It also returns `[]` rather than omitting the field when a task has
  no assignees, so the empty case needs no special handling.

  The tradeoff is that `list_assignees` fails again on the affected Vikunja
  versions. That failure is server-side and the fix is to upgrade; carrying a
  workaround for it indefinitely would mean every call paying for a bug nobody
  running a current server has.

## [0.5.5]

### Fixed

- `list_assignees` works again. It previously always failed, because
  `GET /tasks/{id}/assignees` answers 500 on Vikunja 2.3.0 regardless of whether
  the task has any assignees. Verified that the path and verb match what that
  version documents, that query parameters make no difference, and that `PUT` and
  `DELETE` on the same route are unaffected, so only the read is broken. The tool
  now reads the `assignees` field from the task, which carries the same user
  objects and is omitted entirely when nobody is assigned. `add_assignee` and
  `remove_assignee` continue to use the dedicated route.

  This is a client-side workaround, not a root-cause fix. The server-side reason
  for the 500 is still unknown and would need the Vikunja logs.

## [0.5.4]

### Fixed

- The six listing tools no longer fail with an opaque `AttributeError` raised from
  inside a list comprehension when a response carries no body. `_request` reports
  a bodyless response as a status dict, which is correct for a delete but is not a
  collection, and each listing was iterating that dict's keys. Collection
  responses now go through one helper: a literal `null` still means genuinely
  empty, while anything that is not a list raises a `RuntimeError` naming the
  unexpected type. Returning an empty list instead would have been the worse
  outcome, being indistinguishable from having no items and inviting a caller to
  report that nothing exists when the response was swallowed upstream.

## [0.5.3]

### Added

- Real test coverage of the request layer, taking the suite from 3 tests to 64.
  Statement and branch coverage are both at 100 percent, up from 37 percent
  statement coverage in which no function body ran at all. Credential resolution,
  the HTTP helper's no-content and error-status paths, every tool's verb, path
  and body, and the response-shaping helpers are now exercised. Requests are
  intercepted at the httpx transport boundary, so URL joining, header assembly,
  status handling and JSON decoding remain genuine.
- A coverage floor of 90 percent, enforced by `pytest` configuration rather than
  a workflow flag so a local run and CI apply the identical gate. Dropping below
  it fails the run, and therefore fails the pull request.
- `test_every_tool_is_covered_by_a_routing_case` fails if a tool is added without
  a corresponding wire-contract test, so coverage cannot quietly regress as the
  tool surface grows.

## [0.5.2]

### Added

- A CI workflow that runs the tests on every pull request against `main`, on
  Python 3.10 and 3.13. GitHub reports each matrix leg as a status check, so a
  failing test shows on the pull request. Making those checks *block* a merge is
  a branch protection setting on the repository, not something the workflow can
  assert for itself.

### Changed

- The publish workflow now calls the CI workflow instead of carrying its own copy
  of the test job, so the release gate and the pull request gate cannot drift
  apart.
- The publish step names both artefacts explicitly rather than relying on
  `uv publish`'s default of uploading everything in `dist/`. The upload is now
  bounded to the version being released, and fails loudly if either file is
  missing or misnamed.

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
