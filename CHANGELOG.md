# Changelog

All notable changes to this project are documented here.

## [0.8.5]

### Changed

- Upgraded the lock file, clearing seven advisories in `mcp`'s transitive tree:
  four in `cryptography`, now 50.0.0, three of them high severity and the worst
  a Bleichenbacher oracle in PKCS#7 decryption; two in `starlette`, now 1.6.0,
  the higher one a denial of service from `request.form()` ignoring its own
  limits; and one in `python-multipart`, now 0.0.32, where a negative
  `Content-Length` buffers the whole body in memory.

  None were reachable from this server. It speaks stdio, so `starlette` and
  `python-multipart` are there for an HTTP transport it never starts, and
  `cryptography` arrives through MCP auth code it does not use. Upgrading anyway,
  because the fixes were already published and a scan that reports the same seven
  every time teaches you to stop reading it.

  Also moved, all incidental to `--upgrade`: `anyio`, `annotated-types`,
  `certifi`, `cffi`, `click`, `httpcore2`, `httpx2`, `idna`, `pywin32`, `rpds-py`,
  `sse-starlette`, `typing-extensions`, `typing-inspection` and `uvicorn`.
  `pytest` and `pytest-cov` are pinned exactly and did not.

  `httpx2 2.12.0` introduces one new name to the tree, `httpx2-jsfetch`. It is
  gated behind `sys_platform == 'emscripten'`, a Pyodide fetch backend, so it is
  recorded in the lock and installed on no platform this project runs on.
- The publish workflow now passes the reused CI workflow only `CODECOV_TOKEN`
  instead of `secrets: inherit`. Inherit handed the test job every repository
  secret, `PYPI_API_TOKEN` among them, when the one it declares is the coverage
  token, and only the publish job itself needs the publishing credential.
- Reading the credentials file now warns once when its mode lets group or others
  read or write it, naming the path and the mode but never the contents. The
  module has always asked for `chmod 600`; asking without checking meant a loose
  file stayed quietly loose. It warns rather than refuses, because the file
  belongs to whoever set it up.

## [0.8.3]

### Added

- A committed pre-commit hook in `hooks/`, running `ruff` and then the test suite
  with its coverage floor, in roughly two seconds. Enable it per clone with
  `git config core.hooksPath hooks`; that setting lives in `.git/config` and
  cannot be committed, so it is opt-in by nature and the README says so under
  Contributing.
- A `lint` job in CI running the same `ruff` check. The hook is opt-in, so the
  gate cannot depend on it: this catches anyone who has not enabled it. It is a
  separate job because linting does not vary by Python version.
- Ruff configuration. `TRY004` is ignored: it wants `_items` to raise
  `TypeError`, but that guard validates an API response rather than a caller's
  argument, and the exception type is part of a contract the tests assert.

### Fixed

- Removed the executable bit from nine tracked files that are not scripts,
  including `LICENSE`, `banner.png` and `uv.lock`. Ruff surfaced two of them as
  executable files without a shebang; the rest were the same defect.

## [0.8.2]

### Added

- Coverage reporting to Codecov from CI, uploaded from the Python 3.13 leg only
  since both legs produce identical figures.
- `codecov.yml`, configuring Codecov to report rather than gate. Both statuses are
  marked informational, so they show what happened to coverage without ever
  turning a pull request red. Coverage arrives as a comment and as inline
  annotations on uncovered lines in the diff.

  The targets are still strict, because they decide what gets reported: overall
  coverage may not drop at all, and new lines are held to the same 90 percent
  floor the workflow enforces. Strictness is free when it cannot fail anything.

### Changed

- The publish workflow passes secrets to the reusable CI workflow. Reusable
  workflows do not inherit them, so without this the release run would have
  uploaded coverage tokenless.

### Note

- Nothing about this can fail a build. The only coverage gate remains
  `--cov-fail-under` in `pyproject.toml`, which fails the test run itself. The
  Codecov action reports and does not act on a decline, its statuses are
  informational, and `fail_ci_if_error` is off, which also keeps fork pull
  requests green when they cannot read repository secrets.

## [0.8.1]

### Changed

- `update_task` and `set_reminders` now document that v1's update endpoint is a
  replace: every field you omit is reset to its zero value, so passing only
  `priority` blanks the description and closing a task with `done` discards its
  description, priority and dates. v2 is unaffected, using `PATCH`.

  `update_task` previously claimed "Only the fields you pass are changed", which
  was true on v2 and false on v1. Since the docstring is what an agent reads
  before calling, that made it the most load-bearing place to correct.

  `set_reminders` carries the same hazard and was not previously known to: it
  sends a partial body to the same endpoint, and was confirmed to reset description
  and priority on v1.

  The behaviour is deliberately unchanged. Fixing it would mean reading each task
  before every update, spending a request on every call to protect a path that v2
  users never take. The README now explains it, and this is the strongest practical
  argument for pointing at `/api/v2`.

## [0.8.0]

### Added

- `delete_task` (task_id), taking the tool surface to 19. Removing a task
  previously meant calling the API by hand, which is exactly the gap an MCP server
  exists to close.

  Vikunja soft-deletes and documents deleted tasks as retained for 30 days before
  permanent removal, but exposes no endpoint to list or restore them. The row
  therefore outlives the task while being unreachable through the API, so the
  docstring presents this as irreversible and notes that a task takes its
  comments, labels and assignees with it.

  The path and verb are the same on both API versions, so this needed no version
  branching. As with the other deletes, v1 answers with a message body and v2 with
  no content.

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
