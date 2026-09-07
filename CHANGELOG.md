# Changelog

All notable changes to this project are documented here.

## [1.3.0]

### Added

- `altiplano-http`, a second entry point serving the existing tools and prompt over
  Streamable HTTP from one always-on host. `altiplano` keeps speaking stdio,
  unchanged.

  Settings come from the environment: `ALTIPLANO_HTTP_HOST` (default `127.0.0.1`),
  `ALTIPLANO_HTTP_PORT` (`8000`), `ALTIPLANO_HTTP_PATH` (`/mcp`),
  `ALTIPLANO_HTTP_ALLOWED_HOSTS`, and `ALTIPLANO_HTTP_ALLOWED_ORIGINS`.
  `VIKUNJA_URL` is server-wide and selects one API version for every client.

- Each HTTP client acts as its own Vikunja user. The host holds one Vikunja API
  token per registered client, and the transport presents that client's token to
  Vikunja for the duration of its request. Two people sharing one service reach
  their own projects and their own tasks, with Vikunja applying its own permissions
  to each.

- `altiplano-clientkey update <label>` replaces the Vikunja API token a client acts
  with and leaves its Altiplano client token alone. The client needs no
  reconfiguring. It is how a client is moved to a different Vikunja token, and how a
  record from a store predating per-client tokens is repaired. `add` continues to
  refuse a label that already exists.

- `altiplano-clientkey add|list|revoke`, which mints the bearer tokens the HTTP
  transport accepts. A token is `altp_` followed by 32 bytes from `secrets`, shown
  once, and only its SHA-256 is stored. `add` also collects the Vikunja API token
  the client acts with, from a hidden prompt or from stdin when the input is piped.
  `list` reports whether each client has one. A revocation applies to the next
  request with no restart.

  The store lives in `ALTIPLANO_CLIENTS` or a `clients` file beside the credentials
  file, and it opens with the line `# altiplano clients v2`.

- `uvicorn` as a declared dependency. It was already in the tree through `mcp`.

- `DEPLOYMENT.md`, covering the host side of a shared deployment: installing with
  `uv` under a service account, every environment variable the transport reads,
  registering clients, a systemd unit, an OpenRC script, firewalling, and a
  Cloudflare tunnel. The README covers connecting a client to a service.

- `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md`.

- `scripts/acceptance.py`, which checks a deployed endpoint from a client machine: the
  401 for an anonymous caller, that no `mcp-session-id` is issued, and the tool set.

  `--write` calls all 35 tools once per account, with a per-run nonce in every payload
  that traces an object back to the account that made it. The tour runs twice, both
  accounts concurrently and then one after the other, and a check at the end names any
  tool no account reached. Between the two runs it confirms that a search for the other
  account's nonce returns nothing, that direct reads of the other account's task,
  comments, and project are all refused, and that `created_by` on a freshly created
  task names the expected Vikunja user. Everything is deleted afterwards. Projects are
  the exception. Altiplano exposes no `delete_project`, and each one is reported by id
  and title for removal in Vikunja.

  Repository only, and it carries its own dependencies for `uv run --script`.

- `tests/test_http_integration.py`, which drives the application `altiplano-http`
  serves: the real ASGI app with its lifespan running, requests over
  `httpx2.ASGITransport`, and the MCP client library itself. Only Vikunja is
  synthetic. Five of its eight tests fail against a stateful transport.

- `altiplano-http --check` prints the resolved settings, the Vikunja URL, both
  allowlists, the client count, how many of those clients carry a Vikunja token, and
  whether authentication is on, then exits without opening a socket. It validates the
  same settings startup validates. A configuration it approves is one the server can
  serve. Both HTTP commands take `--version`.

- `ALTIPLANO_HTTP_ALLOW_UNAUTHENTICATED` serves with no token, for local
  development. It is refused on any bind address other than loopback.

### Fixed

- `duplicate_task` returns the copied task, carrying its `id`. Vikunja answers a
  duplicate with a `duplicated_task` envelope on both API versions, and that envelope
  was passed through whole. A caller had no way to reach the copy it had just made.

### Security

- The HTTP transport is stateless and issues no `mcp-session-id`. In the SDK's
  stateful mode every request after `initialize` is keyed on that id alone, and the
  bearer-token gate cannot say which client a session belongs to. A client holding any
  valid token could send requests on another client's session and could delete it,
  after which the owner received `404` and an in-flight response was lost. Stateless
  removes the session. There is no id to borrow or terminate, and no session table to
  grow on a reconnecting client or a rejected request.

  The cost is server-initiated requests: sampling, elicitation, progress over a
  standalone stream, and resumability. Altiplano uses none of them, and a client needs
  no configuration change.

- `VIKUNJA_URL` is checked for shape as well as presence, and parsed with the same
  parser that builds the request. It needs an `http` or `https` scheme, a host, and a
  port inside 1 to 65535. `vikunja.home.arpa/api/v2`, `https:///api/v2`,
  `https://vikunja.test:abc/api/v2` and `https://[::1/api/v2` are all refused at
  startup with a message. Every one of them was accepted before, and the last two
  reached the operator as a failed tool call and a traceback.

- A client token is a bearer credential and needs confidentiality in transit. Serve
  the endpoint behind TLS or an encrypted tunnel on any network, a LAN included, and
  bind Altiplano to loopback when something terminates TLS in front of it.

- Authentication is always on. Every HTTP request needs a registered token. An
  empty store denies every request, and an unreadable store refuses to start. The
  policy is independent of the contents of the store, and
  `ALTIPLANO_HTTP_ALLOW_UNAUTHENTICATED` is the only way to turn it off.
- Binding a non-loopback address with an empty client store is refused at startup.
- A request with no recognised token gets `401` with
  `WWW-Authenticate: Bearer realm="altiplano"`, and no OAuth metadata is
  advertised. Clients configured to send the header directly are the supported
  path.
- Client key changes hold an exclusive lock on a sibling `clients.lock` for the
  whole read-modify-write. An add overlapping a revoke can no longer write back a
  snapshot that resurrects the revoked token. A platform without POSIX `fcntl`
  refuses to change the store, in place of proceeding unlocked. Reading needs no
  lock and is unaffected.
- The client store is opened on every read, and the parse is cached against the
  descriptor's device, inode, size, mtime, and ctime. A cache keyed on `stat` alone
  kept authorising tokens after the server lost read access to the store, since
  removing read permission changes neither mtime nor size. The wider key also
  notices a store replaced by a different file of the same length.
- Label and digest patterns are applied with `fullmatch`. `$` also matches just
  before a final newline, which let a label like `laptop\n` pass validation and
  split its own record across two lines. `add` reported success and handed over a
  token that could never authenticate.
- A client label is limited to 1 to 64 characters of letters, digits, `.`, `_`, and
  `-`, starting alphanumeric. A label carrying a line break could previously store
  a record that read back under a different label, leaving a live token that could
  not be revoked by name.
- A stored digest must be exactly 64 hexadecimal characters. A malformed record is
  skipped with a warning naming the line. One non-ASCII digest previously made
  comparison raise and locked out every client whose record followed it.
- The store is written through `mkstemp`. The temporary file is never readable by
  anyone else.
- Each authenticated request logs the client label that matched. Tokens are never
  logged.
- A registered client with no Vikunja API token is refused with `403`. There is no
  server-wide fallback for an HTTP caller. A forgotten token therefore cannot put a
  client on the operator's Vikunja account. Starting off loopback when no registered
  client has a Vikunja token is refused too.
- The client store holds Vikunja API tokens in plaintext, and it is written
  `chmod 600`. Altiplano presents each one to Vikunja on every request and needs the
  plaintext to do it. Anyone able to read the store can act as every client in it.
  Vikunja does the authorising: narrow each token's scopes there to the tools you
  expose.
- A Vikunja API token is never accepted as a command-line argument, where `ps` would
  show it to every user on the host.
- A Vikunja API token is limited to 8 to 512 printable ASCII characters with no
  space and no `:`. A colon would shift the `created` field along, and a line break
  would split the record.

## [1.2.0]

### Added

- An MCP prompt, `altiplano_guide`, with the guidance that spans several tools:
  resolving ids by name, the order calls happen in, the calls that cannot be undone,
  and the v1 and v2 differences. Clients list it as `Using Altiplano`.

- Server instructions in the handshake: resolve ids by name, which calls cannot be
  undone, how to close a task, and the name of the prompt above. Clients apply them
  on connect.

- `AGENTS.md`, with the commands, layout, and conventions for working on Altiplano.
  `CLAUDE.md` imports it for Claude Code.

### Changed

- The README opens with an ordered `Install` section: `uv`, an API token,
  credentials, the MCP entry, then one call to confirm. The `Credentials` section is
  folded into the credentials step.

## [1.1.0]

### Added

- `bulk_create_tasks` (project_id, tasks), which creates a batch of tasks in one
  atomic request that keeps the order it was given. Each entry takes the same fields
  as `create_task`, and an unrecognised key is refused.
  Vikunja caps a batch at 100.

  Vikunja added the endpoint in 2.5.0 on v2 only. The tool fails on an `/api/v1`
  URL, and there is no fallback to one request per task.

- The publish workflow now creates the GitHub release once the upload to PyPI
  succeeds. It tags the published commit and takes the body from this file's section
  for that version. A release therefore means the version is on PyPI.

  It skips a version already released, and it cannot revise the notes on one that
  exists. A failure to create the release leaves the run green: the package has
  already shipped by that point. The run summary says what happened.

### Changed

- CI runs on a push to `main` as well as on a pull request. Every commit landing on
  `main` uploads coverage, and Codecov has a current base for the next comparison.

- Tool descriptions, comments, and this change log reworded throughout. MCP clients
  read the tool descriptions, and the text an agent sees when it calls Altiplano has
  changed with them. Every tool, argument, default, and return shape is unchanged.

## [1.0.0]

Stable. The tool surface is settled, and SemVer applies from here: tool names, their
arguments and defaults, the shape of what they return, and how credentials are
resolved. Adding a tool or an optional argument is a minor bump; removing or renaming
any of the above is a major one. Names prefixed with an underscore are internal.

Released together with 0.10.1 through 0.14.1 below. PyPI goes from 0.10.0 to 1.0.0,
and `Development Status` moves to `5 - Production/Stable`.

Every tool is verified against Vikunja 2.5.0 on both `/api/v1` and `/api/v2`. 226
tests, 100 percent statement and branch coverage, on Python 3.10 and 3.13.

Known issue: `list_bucket_tasks` fails on v2 with an API token; see 0.12.0.

## [0.14.1]

### Changed

- `server.py` is split into a package. `app.py` holds the MCP instance, `config.py`
  resolves credentials, `api.py` owns the version differences and the request layer,
  and `tools/` has one module per section of the README. `server.py` keeps the imports
  that register the tools, and `main`.

  The instance needs a module of its own. The tool modules import it and `server`
  imports them, and in one file that is a circular import.

  No behaviour changed.

## [0.14.0]

### Added

- `delete_label` (label_id), which deletes a label everywhere. `remove_label`
  detaches one from a single task.
- `create_bucket` (project_id, title, view_id?, limit?) and `delete_bucket`
  (project_id, bucket_id, view_id?). Deleting a column moves its tasks to the default
  column, leaving them intact, and a view keeps at least one column.

## [0.13.0]

### Added

- `search_tasks` (query?, filter?, sort_by?, page, per_page), the first tool that does
  not need to be told a project. `GET /tasks` on both versions, with the same `s` on
  v1 and `q` on v2 rename `search_users` uses. Each result reports `project_id`.
- `move_task` (task_id, project_id). Vikunja has no move endpoint: `project_id` is
  writable and setting it is the move. The task's project-local `identifier` is
  reassigned on arrival.
- `duplicate_task` (task_id). Copies into the same project with a `copiedfrom`
  relation back to the original. The endpoint takes no target project. Duplicating
  elsewhere is this followed by `move_task`.
- `bulk_update_tasks` (task_ids, done?, priority?). Field names are sent separately
  from values, and only the named fields are written, on either version.
- `create_label` (title, hex_color?, description?).

### Changed

- `move_task_to_bucket` no longer takes `project_id`. It reads the project from the
  task: one more request, one fewer argument that can contradict the task it is given.

## [0.12.0]

### Added

- Kanban tools: `list_kanban_views`, `list_buckets`, `list_bucket_tasks`,
  `list_task_buckets`, and `move_task_to_bucket`.

  Buckets belong to a view, and each tool resolves one first. `view_id` is optional,
  and the first kanban view is used. Most projects have exactly one.

  `move_task_to_bucket` has side effects, listed on the tool: the done bucket marks a
  task done and moving out of it un-marks it, a repeating task moved into the done
  bucket is reopened and sent to the default bucket, and a bucket at its task limit
  refuses the move.

  Buckets with their tasks come from `GET /views/{view}/tasks` on v1 and
  `GET /views/{view}/buckets/tasks` on v2, which v1 does not have at all; on v2 the view
  tasks route returns flat tasks even for a kanban view. `list_buckets` omits task
  counts because that endpoint does not populate them, and `list_bucket_tasks`
  reports them. `list_task_buckets` reads `GET /tasks/{id}?expand=buckets` and returns
  one entry per kanban view. A task holds a bucket in each.

### Note

- `list_bucket_tasks` does not work on v2 with an API token on Vikunja 2.5.0. That
  route answers 401 while every sibling accepts the same token. That points at a
  token predating the route, with no permission for it. The reported message gives
  that explanation, where Vikunja's own text says only that the token is invalid.
  `/api/v1` serves the same data.

## [0.11.0]

### Added

- `percent_done`, `is_favorite`, `repeat_after`, and `repeat_mode` on `create_task`
  and `update_task`.

  `percent_done` is a fraction despite the name: a quarter done is 0.25. Nothing
  validates it, and 50 is stored as 50.

  `repeat_after` is a number of seconds, and a repeating task reopens itself when
  marked done. One with no dates can never be closed. `repeat_mode` is 0 to advance
  by `repeat_after`, 1 to repeat monthly, 2 to count from the day it was completed.
  Vikunja's description of that field says 3 for the last one; its generated enum
  says 2.

## [0.10.1]

### Fixed

- Stopped sending `?format=markdown` on a v2 partial update, added in 0.9.0 on the
  assumption that v2 ignored it only for the request body. It ignores it for the
  response as well, and the parameter did nothing. `update_task` and `set_reminders`
  return the description as stored HTML on v2, where `get_task` returns Markdown.

## [0.10.0]

### Added

- `add_relation` (task_id, other_task_id, relation_kind) and `remove_relation`.
  `get_task`'s `related_tasks` could already read relations. Changing them had no
  tool.

  `relation_kind` defaults to `related`. The others are `subtask`, `parenttask`,
  `duplicateof`, `duplicates`, `blocking`, `blocked`, `precedes`, `follows`,
  `copiedfrom`, and `copiedto`. Direction matters for the asymmetric ones: the base
  task is the one in the path, and `subtask` makes the other task its child. Vikunja
  maintains the inverse side itself.

  No `list_relations`: `get_task` already returns them grouped by kind.

## [0.9.0]

### Added

- `update_task` takes `due_date`, which `create_task` already had.
- Dates can be cleared, by passing an empty string to `due_date`, `start_date`, or
  `end_date` on either tool. Vikunja has no null for a date: an unset one is the zero
  time, `0001-01-01T00:00:00Z`, and Altiplano writes exactly that.

### Changed

- Errors report what the server objected to alongside the status code: v1's
  `message`, or v2's RFC 9457 `detail` and numeric code. Still an
  `httpx.HTTPStatusError`. Branching on `response.status_code` is unaffected.

  Any non-2xx now raises. A redirect fails, and the message names the `Location` it
  was sent to.

- A description change on v2 sends the ETag from its read back as `If-Match`. A task
  modified in between fails with a message to read it again, and is never silently
  overwritten. Sent only when the read supplied an ETag.

- The credentials file is parsed once per change to it, keyed on its mtime and size.
  A rotated token is still picked up without a restart.

### Fixed

- `update_task` and `set_reminders` no longer discard fields on v1. That API has no
  partial update: `POST /tasks/{id}` is a replace, and a body carrying only the
  changed fields reset every other field to its zero value. Both tools now read the
  task and merge the changes into it.

  v1 updates cost two requests. v2 is untouched and stays a single `PATCH` unless a
  description is involved. v1 cannot detect a concurrent edit. It has no ETag to send.

- A credentials file that cannot be read warns once, naming the path and the error.
  The contents never appear in the warning. It used to raise an `OSError` from inside
  `_base`.

- `_replace_task` refuses to build a replace out of a response that is not a task. A
  bodyless response arrives as a status dict, and replacing a task with that would
  have wiped it.

### Note

- `list_tasks(filter=...)` shadows the builtin deliberately: `filter` is the name
  Vikunja gives the query parameter and the name callers write. Commented in the
  source so it does not get "fixed" later.

## [0.8.6]

### Fixed

- CI warned that `actions/github-script` targets Node.js 20 and was being forced
  onto Node.js 24. Nothing here calls that action: the SHA in the warning is the
  pin inside `codecov-action` v5.5.5's own `action.yml`, whose nested
  `github-script` runs on node20. `codecov-action` is now v7.0.0, whose nested
  pin runs on node24.

  A major bump: the v5 line holds Node 20 on purpose.
  v5.5.3 bumped `github-script` to 8.x, v5.5.4 reverted it and said v6 would
  take the bump, and v6.0.0 shipped it with a warning about requiring
  node24. So v5 is the line for runners without node24, and v5.5.5, newer by date
  than v7.0.0, contains only a signing-key change. v7.0.0 and v6.0.2 are the same
  code; v6.0.2 exists as a copy to ease upgrades.

  Worth fixing ahead of the breakage: Node 20 is due to leave the runners in
  September 2026, and because `publish.yml` reuses this workflow, an unfixed pin
  would fail in the release path.

  `actions/checkout` v7.0.1 and `astral-sh/setup-uv` v9.0.0 were checked at the
  same time. Both run on node24 and nest no actions. Nothing else here is waiting
  to warn.

## [0.8.5]

### Changed

- Upgraded the lock file, clearing seven advisories in `mcp`'s transitive tree:
  four in `cryptography`, now 50.0.0, three of them high severity and the worst
  a Bleichenbacher oracle in PKCS#7 decryption; two in `starlette`, now 1.6.0,
  the higher one a denial of service from `request.form()` ignoring its own
  limits; and one in `python-multipart`, now 0.0.32, where a negative
  `Content-Length` buffers the whole body in memory.

  None were reachable from this server. It speaks stdio: `starlette` and
  `python-multipart` are there for an HTTP transport it never starts, and
  `cryptography` arrives through MCP auth code it does not use. Upgraded anyway.
  The fixes were already published, and a scan that reports the same seven every
  time teaches you to stop reading it.

  Also moved, all incidental to `--upgrade`: `anyio`, `annotated-types`,
  `certifi`, `cffi`, `click`, `httpcore2`, `httpx2`, `idna`, `pywin32`, `rpds-py`,
  `sse-starlette`, `typing-extensions`, `typing-inspection`, and `uvicorn`.
  `pytest` and `pytest-cov` are pinned exactly and did not.

  `httpx2 2.12.0` introduces one new name to the tree, `httpx2-jsfetch`. It is
  gated behind `sys_platform == 'emscripten'`, a Pyodide fetch backend. The lock
  records it, and no platform this project runs on installs it.
- The publish workflow now passes the reused CI workflow only `CODECOV_TOKEN`. It
  used `secrets: inherit`, which handed the test job every repository secret,
  `PYPI_API_TOKEN` among them, when the one it declares is the coverage token, and
  only the publish job itself needs the publishing credential.
- Reading the credentials file now warns once when its mode lets group or others
  read or write it, naming the path and the mode. The contents never appear in the
  warning. The module has always asked for `chmod 600`; asking without checking
  meant a loose file stayed quietly loose. It warns and carries on: the file
  belongs to whoever set it up.

## [0.8.3]

### Added

- A committed pre-commit hook in `hooks/`, running `ruff` and then the test suite
  with its coverage floor, in roughly two seconds. Enable it per clone with
  `git config core.hooksPath hooks`; that setting lives in `.git/config` and
  cannot be committed. It is opt-in by nature, and the README says so under
  Contributing.
- A `lint` job in CI running the same `ruff` check. The hook is opt-in, and the
  gate cannot depend on it: this catches anyone who has not enabled it. It is a
  separate job because linting does not vary by Python version.
- Ruff configuration. `TRY004` is ignored: it wants `_items` to raise
  `TypeError`, but that guard validates an API response, and the exception type is
  part of a contract the tests assert.

### Fixed

- Removed the executable bit from nine tracked files that are not scripts,
  including `LICENSE`, `banner.png`, and `uv.lock`. Ruff surfaced two of them as
  executable files without a shebang; the rest were the same defect.

## [0.8.2]

### Added

- Coverage reporting to Codecov from CI, uploaded from the Python 3.13 leg only
  since both legs produce identical figures.
- `codecov.yml`, configuring Codecov to report without gating. Both statuses are
  marked informational. They show what happened to coverage without ever turning a
  pull request red. Coverage arrives as a comment and as inline annotations on
  uncovered lines in the diff.

  The targets are still strict, and they decide what gets reported: overall
  coverage may not drop at all, and new lines are held to the same 90 percent
  floor the workflow enforces. Strictness is free when it cannot fail anything.

### Changed

- The publish workflow passes secrets to the reusable CI workflow. Reusable
  workflows do not inherit them. Without this the release run would have uploaded
  coverage tokenless.

### Note

- Nothing about this can fail a build. The only coverage gate remains
  `--cov-fail-under` in `pyproject.toml`, which fails the test run itself. The
  Codecov action reports and does not act on a decline, its statuses are
  informational, and `fail_ci_if_error` is off, which also keeps fork pull
  requests green when they cannot read repository secrets.

## [0.8.1]

### Changed

- `update_task` and `set_reminders` now document that v1's update endpoint is a
  replace: every field you omit is reset to its zero value. Passing only `priority`
  blanks the description, and closing a task with `done` discards its description,
  priority, and dates. v2 uses `PATCH` and is unaffected.

  `update_task` previously claimed "Only the fields you pass are changed", which
  was true on v2 and false on v1. An agent reads the docstring before calling. That
  made it the most load-bearing place to correct.

  `set_reminders` has the same hazard, which was not previously known: it sends a
  partial body to the same endpoint, and was confirmed to reset description and
  priority on v1.

  The behaviour is deliberately unchanged. Fixing it would mean reading each task
  before every update, spending a request on every call to protect a path that v2
  users never take. The README now explains it, and this is the strongest practical
  argument for pointing at `/api/v2`.

## [0.8.0]

### Added

- `delete_task` (task_id), taking the tool surface to 19. Removing a task
  previously meant calling the API by hand. That is exactly the gap an MCP server
  exists to close.

  Vikunja soft-deletes and documents deleted tasks as retained for 30 days before
  permanent removal, but exposes no endpoint to list or restore them. The row
  therefore outlives the task while staying unreachable through the API. The
  docstring presents this as irreversible, and notes that a task takes its
  comments, labels, and assignees with it.

  The path and verb are the same on both API versions. No version branching was
  needed. As with the other deletes, v1 answers with a message body and v2 with
  no content.

## [0.7.1]

### Added

- The server declares its version. It appears as `serverInfo.version` in the MCP
  handshake, and clients can show which build they launched.

  This exists because `uvx` can serve a cached build for some time after a
  release, and until now the running version was invisible: the only way to tell
  was to provoke a behaviour that had changed between releases. That is a poor
  diagnostic, and it cost several restart cycles to work out that a client was
  still on the previous version.

## [0.7.0]

### Added

- Descriptions and comments are exchanged as Markdown on v2. Callers no longer
  write HTML by hand. `create_task`, `update_task`, `create_project`,
  `add_comment`, `update_comment`, `get_task`, and `list_comments` all speak
  Markdown; Vikunja converts to the HTML it stores and back again, and resolves
  `@mentions` while doing so. v1 has no such facility and is unchanged.

### Changed

- A description change on v2 now reads the task and writes it back whole. The
  reason: v2 honours the Markdown parameter on create and on replace but silently
  ignores it on `PATCH`, returning 200 while storing the Markdown verbatim into a
  field rendered as HTML. A partial update would therefore have corrupted the field
  and reported success. Updates with no description are untouched and remain a
  single request.

  The cost is one extra request when a description changes, and a lost update if
  something else writes to the same task in between. Reading first was verified
  lossless across labels, assignees, reminders, dates, colour, priority, and
  percent done.

- `update_comment` uses `PUT` on v2, for the same reason. A comment has a single
  writable field. Replacing and updating it are the same operation, and only the
  replace converts Markdown.

## [0.6.0]

### Added

- Support for the Vikunja v2 API alongside v1. The version comes from the URL you
  configure: point `VIKUNJA_URL` at `/api/v2` and you get v2, anything else
  gets v1. No new setting, no probing, and no extra request at startup. Older
  servers keep working unchanged. v2 only exists from Vikunja 2.4.0.

  Every tool takes the same arguments and returns the same shapes on both. The
  differences absorbed internally are the create verb (`PUT` on v1, `POST` on v2),
  the update verb (`POST` on v1, `PATCH` on v2), the collection envelope that v2
  wraps results in, and the user search parameter, renamed from `s` to `q`.

  Paths are identical across the two versions for everything this server does. No
  request path needed a version branch.

### Changed

- `_items` unwraps a v2 pagination envelope as well as a v1 bare array. It
  branches on the shape of the response, leaving the configured version out of it.
  A mismatch between the two degrades gracefully, and the protection added in
  0.5.4 against treating a bodyless response as an empty collection still holds.

## [0.5.6]

### Changed

- `list_assignees` reads `GET /tasks/{id}/assignees` again, reverting the 0.5.5
  workaround. That endpoint was broken server-side on Vikunja v2.3.0 and works on
  v2.5.0. The workaround now costs more than it saves: fetching the whole task
  to read a short user list transferred 3604 bytes where the dedicated endpoint
  returns 157. It also always returns `[]` for a task with no assignees, and the
  empty case needs no special handling.

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
  `DELETE` on the same route are unaffected. Only the read is broken. The tool
  now reads the `assignees` field from the task, which holds the same user
  objects and is omitted entirely when nobody is assigned. `add_assignee` and
  `remove_assignee` continue to use the dedicated route.

  This is a client-side workaround. The server-side reason for the 500 is still
  unknown and would need the Vikunja logs.

## [0.5.4]

### Fixed

- The six listing tools no longer fail with an opaque `AttributeError` raised from
  inside a list comprehension when a response has no body. `_request` reports
  a bodyless response as a status dict, which is correct for a delete and is not a
  collection, and each listing was iterating that dict's keys. Collection
  responses now go through one helper: a literal `null` still means genuinely
  empty, while anything that is not a list raises a `RuntimeError` naming the
  unexpected type. Returning an empty list would have been the worse outcome:
  indistinguishable from having no items, and an invitation for a caller to report
  that nothing exists when the response was swallowed upstream.

## [0.5.3]

### Added

- Real test coverage of the request layer, taking the suite from 3 tests to 64.
  Statement and branch coverage are both at 100 percent, up from 37 percent
  statement coverage in which no function body ran at all. Credential resolution,
  the HTTP helper's no-content and error-status paths, every tool's verb, path,
  and body, and the response-shaping helpers are now exercised. Requests are
  intercepted at the httpx transport boundary. URL joining, header assembly,
  status handling, and JSON decoding remain genuine.
- A coverage floor of 90 percent, enforced by `pytest` configuration. A local run
  and CI apply the identical gate. Dropping below it fails the run, and therefore
  fails the pull request.
- `test_every_tool_is_covered_by_a_routing_case` fails if a tool is added without
  a corresponding wire-contract test. Coverage cannot quietly regress as the tool
  surface grows.

## [0.5.2]

### Added

- A CI workflow that runs the tests on every pull request against `main`, on
  Python 3.10 and 3.13. GitHub reports each matrix leg as a status check, and a
  failing test shows on the pull request. Making those checks block a merge is a
  branch protection setting on the repository. The workflow cannot assert it.

### Changed

- The publish workflow now calls the CI workflow, having dropped its own copy of
  the test job. The release gate and the pull request gate cannot drift apart.
- The publish step names both artefacts explicitly, leaving `uv publish`'s default
  of uploading everything in `dist/` unused. The upload is now bounded to the
  version being released, and fails loudly if either file is missing or misnamed.

## [0.5.1]

### Added

- A manually triggered GitHub Actions workflow that publishes to PyPI. It runs
  the tests on Python 3.10 and 3.13, refuses to republish a version that already
  exists on PyPI, builds with `--no-sources`, and imports the built wheel before
  uploading. A packaging mistake fails the run and never reaches users.
- A smoke test suite covering module import, tool registration, console script
  resolution, and version agreement between `pyproject.toml` and `__init__.py`.
  0.4.0 shipped an import error that broke every launch; these are the checks
  that would have caught it. `pytest` is now a `dev` dependency group.

## [0.5.0]

### Added

- `update_comment` (task_id, comment_id, comment) and `delete_comment`
  (task_id, comment_id). Comments could be listed and created, and never
  corrected or removed. A typo in a comment was permanent from the client side.
  Both wrap endpoints Vikunja already exposed; pass the `id` returned by
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
  SDK 2.x against code written for 1.x. Every launch broke.
