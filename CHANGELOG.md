# Changelog

All notable changes to this project are documented here.

## [0.14.1]

### Changed

- `server.py` is now a package. It had reached 988 lines and 34 tools, so it is
  split along the same seams the README uses: `app.py` holds the MCP instance,
  `config.py` resolves credentials, `api.py` owns the version differences and the
  request layer, and `tools/` has a module per section. `server.py` keeps only the
  imports that register the tools and `main`.

  `app.py` exists to break a cycle rather than out of taste. Every tool module needs
  the instance for `@mcp.tool()`, and `server` needs to import every tool module so
  that decorator runs. With the instance in `server`, those two facts are a circular
  import; in its own module, importing nothing of ours, there is no cycle to reason
  about.

  Two modules for the support layer rather than four. `_date` and `_task_summary`
  are shaping rather than transport and could argue for a third file, but a module
  holding three helpers earns less than it costs to look in.

  No behaviour changed. The tool bodies were moved by line range rather than retyped,
  so nothing could drift in transcription, and the suite is unchanged at 226 tests
  and 100 percent statement and branch coverage. Two of those tests are what make
  the move safe to believe: one compares the registered tools against a written list
  of all 34 names, so a module that fails to import or an instance that ends up
  duplicated fails loudly, and one loads the console script, keeping
  `altiplano.server:main` honest.

  The tests moved with the code, since `server` no longer owns any of it. Two of
  their patches needed thought rather than renaming, because rebinding a name in one
  module never reaches another: `_CONFIG_FILE` and `_file_cache` are now patched on
  `config`, and the fake transport is installed on `httpx` itself rather than through
  whichever module happens to call it, which is both simpler and indifferent to where
  the client gets built.

## [0.14.0]

### Added

- `delete_label`, closing an asymmetry 0.13.0 opened by adding `create_label` with
  no counterpart. It deletes the label everywhere, taking it off every task that
  carries it, which is a different thing from `remove_label` detaching it from one.

- `create_bucket` and `delete_bucket`, so a kanban board's columns can be managed
  and not merely read. `create_bucket` takes an optional `limit`, the cap on how
  many tasks the column accepts.

  These exist because of a workflow requirement rather than for completeness: a
  steering rule that moves a task into a `Doing` column when work starts has to be
  able to create that column on a board that has none. `delete_bucket` comes along
  so the same asymmetry is not opened twice in a week.

  Deleting a column does not delete its tasks. Vikunja moves them to the default
  bucket, confirmed live: a task in a deleted column reappeared in To-Do. A view
  keeps at least one column, so the last cannot be removed.

  Not included: renaming a bucket or changing its limit or position. Nothing needs
  it, and three tools that answer a real requirement are worth more than a complete
  CRUD set that does not.

## [0.13.0]

### Added

- Five tools, taking the surface to 31: `search_tasks`, `move_task`,
  `duplicate_task`, `bulk_update_tasks` and `create_label`. These were offered in a
  branch referenced from PR #4's comments, approved there, and never submitted as a
  pull request. Each was rebuilt from the spec rather than copied, and two of the
  fork's versions could not have worked:

  `search_tasks` is the first tool here that does not need to be told a project. The
  fork targets `/tasks/all`, which does not exist on either API version of 2.5.0;
  the cross-project route is `GET /tasks`, and it takes the same `s` on v1 and `q`
  on v2 rename that `search_users` already handles. Results carry `project_id`,
  because not knowing which project a task is in is the reason to reach for this.

  `duplicate_task` takes no target project, where the fork offered one. The endpoint
  accepts no body at all: it copies into the same project and records a `copiedfrom`
  relation back to the original, both confirmed live. Duplicating elsewhere is this
  followed by `move_task`, which the docstring says.

  `move_task` exists because Vikunja has no move endpoint. A task's `project_id` is
  writable, and the v2 schema is explicit that setting it to another project is the
  move, so this goes through the same write path `update_task` uses, now factored
  out as `_write_task`. The task keeps its labels, assignees, comments and relations;
  its project-local `identifier` is reassigned on arrival, observed live as `#57`
  becoming `HOME-1`.

  `bulk_update_tasks` sends field names separately from values, which is what the
  endpoint wants and what makes it a genuine partial update even on v1, where a
  single-task update is not.

  `create_label` closes the gap where labels could be listed and attached but never
  created.

### Changed

- `move_task_to_bucket` no longer takes `project_id`. It reads the project from the
  task, which costs a request and removes an argument that could contradict the task
  it was given; a mismatched one produced a 404 from a path that looked correct.
  This was a review point on PR #4 that went unanswered there and applied equally to
  the version shipped in 0.12.0, which has not been released.

## [0.12.0]

### Added

- Kanban tools, taking the surface to 26: `list_kanban_views`, `list_buckets`,
  `list_bucket_tasks`, `list_task_buckets` and `move_task_to_bucket`. Boards were
  the largest thing this server could see nothing of.

  Buckets belong to a view rather than to a project, so each tool resolves one
  first. `view_id` is optional and the first kanban view is taken, which is the only
  one most projects have; views arrive ordered by position, so "first" means the
  leftmost tab rather than an arbitrary pick. Naming a view that is not kanban, or
  one that does not exist, fails with a message that says which of the two happened.

  `move_task_to_bucket` carries side effects, all documented on the tool: the done
  bucket marks a task done and moving out of it un-marks it, both confirmed live on
  both API versions; a repeating task moved into the done bucket is reopened and sent
  to the default bucket; and a bucket at its task limit refuses the move.

  This was the second half of PR #4's suggestion, though little of the PR survived
  contact with the spec:

  The PR reads grouped buckets from `GET /views/{view}/tasks`. On v2 that route
  "always returns flat tasks, even for a kanban view", and grouping moved to
  `GET /views/{view}/buckets/tasks`, which v1 does not have at all. The path
  therefore forks by version, and three of the PR's tools would have misread v2.

  The PR hardcodes `POST` for the move, which is v1's verb; v2 wants `PUT`. That is
  the same pair `_replace_task` and `update_comment` already needed, so `_VERBS`
  gained a third action, `replace`, and all three call sites now go through it
  instead of spelling the versions out locally.

  The PR spends an extra request per `list_buckets` call to read bucket counts from
  the tasks endpoint. Counts are genuinely absent from the buckets endpoint,
  confirmed live as `count: 0` on a bucket holding 23 tasks, but the answer is not to
  pay for them: `list_buckets` omits the field rather than reporting a zero that is
  a lie, and `list_bucket_tasks` carries the real numbers.

  The PR's `get_task_bucket` costs three requests. `GET /tasks/{id}?expand=buckets`
  answers directly on both versions, so `list_task_buckets` is one, and it returns a
  list because a task holds a bucket in every kanban view its project has.

### Note

- `list_bucket_tasks` does not work on v2 with an API token, on Vikunja 2.5.0. That
  route answers 401 while every sibling accepts the same token and the v2 spec
  documents it as accepting one, so the likely cause is a token predating the route
  and lacking permission for it. It ships anyway, with that 401 mapped to an
  explanation rather than Vikunja's claim that the token is invalid, on the
  precedent of 0.5.6 keeping `list_assignees` on an endpoint that was broken
  server-side at the time. v1 serves the same data and was confirmed working.

## [0.11.0]

### Added

- `percent_done`, `is_favorite`, `repeat_after` and `repeat_mode` on `create_task`
  and `update_task`, following the existing rule that a field reaches the payload
  only if it was passed. These were the remaining writable scalars on a task that
  this server could not set. What is left is either read-only, owned by a dedicated
  endpoint, or kanban state.

  Lifted from the suggestion in PR #4 rather than the PR itself, and each field
  checked against the spec instead of taken on trust, which was worth doing:

  `repeat_mode` is the enum `[0, 1, 2]`, generated from the Go type as
  `TaskRepeatModeDefault`, `TaskRepeatModeMonth` and `TaskRepeatModeFromCurrentDate`.
  Vikunja's own description of the field disagrees with its own enum, announcing
  "three possible values" and then listing 0, 1 and 3. The enum wins, and both the
  docstrings and the README note the discrepancy so the next reader does not take
  the prose for gospel.

  `percent_done` is a fraction despite its name: a quarter done is 0.25. The spec
  documents no range at all, so this was settled against a live server, which also
  turned up that nothing validates it. Passing 50 stores 50, neither clamped nor
  read as 50 percent. Documented rather than corrected, since silently dividing a
  caller's number by 100 would be a worse surprise than the one it prevents.

  `is_favorite` is per-user state, and writable through the task rather than through
  a separate endpoint. Confirmed both directions live.

  All four are falsy at their "off" value, so a truthiness check in the payload
  builder would drop exactly `percent_done=0`, `is_favorite=False`, `repeat_after=0`
  and `repeat_mode=0`. Tests pin each one, on both tools.

## [0.10.1]

### Fixed

- Stopped sending `?format=markdown` on a v2 partial update, which 0.9.0 added on a
  premise that turned out to be wrong. The theory was that v2 ignored the parameter
  only for the request body, so asking for the response in Markdown would cost
  nothing and make `update_task` answer in the same format as `get_task`. Tested
  against 2.5.0 as soon as a released build could reach a live server, it ignores
  the parameter for the response as well: a description stored as
  `<p><strong>Bold</strong> and <code>code</code></p>` came back exactly that way
  with the parameter set.

  So the inconsistency it was meant to remove is still there, and cannot be removed
  by asking. `update_task` and `set_reminders` return the description as stored HTML
  on v2, and `get_task` returns Markdown. The parameter is gone rather than left in
  place, because one the server discards suggests a guarantee that does not hold,
  and the next person to read that line would believe it.

  The docstring and the README now say so, which is the only fix available short of
  spending a second request on a follow-up read after every partial update. That is
  not worth it for a difference in the shape of a return value that callers can
  resolve with `get_task`.

  The 0.9.0 and 0.10.0 entries are left as written. They record what was believed at
  the time, and this one records what testing showed.

## [0.10.0]

### Added

- `add_relation` (task_id, other_task_id, relation_kind) and `remove_relation`,
  taking the tool surface to 21. Relations could be read, through the
  `related_tasks` field `get_task` returns, but not created or removed, so linking
  two tasks meant leaving the MCP and doing it in the web UI. That is exactly the
  gap an MCP server exists to close, and it came up filing one task that revisited
  the decision recorded in another.

  `relation_kind` defaults to `related`, the symmetric case, so the common call is
  `add_relation(415, 397)` with nothing else to decide. The other kinds are
  `subtask`, `parenttask`, `duplicateof`, `duplicates`, `blocking`, `blocked`,
  `precedes`, `follows`, `copiedfrom` and `copiedto`. Direction matters for the
  asymmetric ones: the base task is the one in the path, and `subtask` makes the
  other task a child of it.

  The kind is passed straight through rather than validated against a local copy of
  the enum, the same way `filter` is. The server owns that vocabulary, a local copy
  would be one more thing to keep in sync, and since 0.9.0 a rejected value comes
  back with Vikunja's own explanation attached. The docstrings list the kinds,
  which is where an agent will actually read them.

  No `list_relations`, because `get_task` already returns them grouped by kind.

  Endpoints came from the v1 OpenAPI spec: `PUT /tasks/{id}/relations` on v1 and
  `POST` on v2, through the existing verb table, and
  `DELETE /tasks/{id}/relations/{kind}/{otherID}`. The spec marks a request body as
  required on the delete too, even though the path carries the same three values,
  so both are sent, built from the same arguments and unable to disagree.

  Two things this does not establish. Whether Vikunja creates the inverse relation
  by itself, which matters most for `subtask` and `parenttask`. And whether the v2
  paths match v1, assumed here as they are for every other tool, with the v1 spec's
  reference to "the v2 delete route param" as corroboration rather than proof.

## [0.9.0]

### Added

- `update_task` takes `due_date`. `create_task` always did, so a deadline could be
  set when a task was created and never changed afterwards; the field went to the
  same endpoint as `start_date` and `end_date`, which were already there, so this
  was an omission rather than a limitation.

  Checked against the API before implementing rather than assumed: the v1 OpenAPI
  model documents `due_date` as writable, unlike `done_at`, which says it is
  system-controlled, and `created`, which says it cannot be changed. The v2 docs
  state the JSON models are identical across versions.

- Dates can be cleared, by passing an empty string to `due_date`, `start_date` or
  `end_date` on either `create_task` or `update_task`. They could previously only
  be overwritten with another date.

  Vikunja has no null for a date. An unset one is Go's zero time,
  `0001-01-01T00:00:00Z`, on the wire and in the database, and an empty string is
  not a datetime it will parse, so clearing means writing that value. `None`
  already means "leave this field out of the payload", which is why the empty
  string carries the meaning instead of a second argument saying the same thing.

  This is the value `_replace_task` has been round-tripping for unset dates since
  0.7.0, verified lossless at the time, so it was already known to work.

### Changed

- Errors now carry what the server objected to. `raise_for_status()` reported the
  status code and nothing else, so a rejected filter expression and a task that
  does not exist both arrived as a bare `400` or `404`, leaving an agent nothing to
  correct. Vikunja explains itself in the body: v1 in `message`, v2 as RFC 9457
  problem+json in `detail`, alongside its own numeric error code. All of it is now
  in the raised message.

  Still an `httpx.HTTPStatusError`, so anything branching on
  `response.status_code` is unaffected. Only the message changed.

  It also raises on any non-2xx rather than only on 4xx and 5xx, which is what
  `raise_for_status` did. A redirect is a failure here: it means `VIKUNJA_URL` is
  wrong, and decoding the redirect body as a result would hide that. The message
  names the `Location` it was sent to, because that is usually the whole diagnosis.

- A description change on v2 sends the ETag from its read back as `If-Match`. That
  read-then-replace has had a lost-update window since 0.7.0, documented and
  accepted because there was no way to detect it. v2 returns an ETag on
  single-resource reads and honours preconditions, so the window now fails with a
  message saying to read the task again, instead of silently discarding whatever
  was written in between.

  The header goes out only when the read supplied an ETag, so a server that does
  not offer them behaves exactly as before.

- A partial update on v2 now asks for its response in Markdown, like every other
  read. `update_task` and `set_reminders` were answering with raw HTML in the
  description while `get_task` answered with Markdown, so the same field arrived in
  two different formats depending on which tool produced it. v2 ignores that
  parameter for a `PATCH` request body, which is why a description never routes
  through `PATCH` in the first place, but nothing stopped us asking for the response
  in the same currency as everything else.

- The credentials file is parsed once per change to it, rather than once per
  lookup. `_base`, `_headers` and `_version` each resolve config independently, so
  a single tool call read and parsed the file three or four times. The cache is
  keyed on the file's mtime and size, not held for the life of the process, so a
  rotated token is still picked up without a restart.

### Fixed

- `update_task` and `set_reminders` no longer destroy data on v1. That API has no
  partial update: `POST /tasks/{id}` is a replace, so a body carrying only the
  changed fields reset every other field to its zero value. Passing `priority`
  blanked the description; closing a task with `done` discarded its description,
  priority and dates; `set_reminders` did the same through the same endpoint. Both
  tools now read the task first and merge the changes into it.

  0.8.1 found this, documented it, and deliberately left it, on the grounds that
  fixing it would spend an extra request on every v1 call to protect a path v2
  users never take. That weighed one request against silent data loss, which is the
  wrong way round, and it had already cost a real task its description before the
  cause was understood. Anyone still on v1 was one careless call away from the
  same.

  The mechanism is the read-then-merge that `_replace_task` already performed for
  the v2 Markdown path, so this is a routing change and a version-aware verb rather
  than a new mechanism. v1 updates now cost two requests. v2 is untouched, and
  stays a single `PATCH` unless a description is involved.

  What v1 still cannot do is detect a concurrent edit: the v2 path sends the read's
  ETag back as `If-Match`, and v1 has no ETag to send. That window is narrower than
  the certainty of a wipe it replaces.

- A credentials file that cannot be read no longer escapes as a raw `OSError` from
  inside `_base`. Only `FileNotFoundError` was handled, so a permissions problem on
  the file or a directory above it surfaced as an unexplained failure of whichever
  tool happened to be called first. It now warns once, naming the path and the
  error but never the contents, and carries on: the environment may already hold
  the credentials, in which case the file is irrelevant and failing would be wrong.

- `_replace_task` refuses to build a full replace out of a response that is not a
  task. A bodyless response arrives as a status dict, and replacing a task with
  that would have wiped it. Same defect class as the listing bug fixed in 0.5.4,
  in the one place where the consequence is destructive rather than merely
  confusing.

### Note

- `list_tasks(filter=...)` shadows the builtin deliberately. `filter` is the name
  Vikunja gives the query parameter and the name callers already write, the builtin
  is not used anywhere in the module, and renaming it would break the published
  tool contract. Now commented as such, so it does not get "fixed" later.

## [0.8.6]

### Fixed

- CI warned that `actions/github-script` targets Node.js 20 and was being forced
  onto Node.js 24. Nothing here calls that action: the SHA in the warning is the
  pin inside `codecov-action` v5.5.5's own `action.yml`, whose nested
  `github-script` runs on node20. `codecov-action` is now v7.0.0, whose nested
  pin runs on node24.

  A major bump rather than a patch, because the v5 line holds Node 20 on purpose.
  v5.5.3 bumped `github-script` to 8.x, v5.5.4 reverted it and said v6 would
  carry the bump instead, and v6.0.0 shipped it with a warning about requiring
  node24. So v5 is the line for runners without node24, and v5.5.5, newer by date
  than v7.0.0, contains only a signing-key change. v7.0.0 and v6.0.2 are the same
  code; v6.0.2 exists as a copy to ease upgrades.

  Worth fixing now rather than when it breaks: Node 20 is due to leave the
  runners in September 2026, and because `publish.yml` reuses this workflow, an
  unfixed pin would fail in the release path.

  `actions/checkout` v7.0.1 and `astral-sh/setup-uv` v9.0.0 were checked at the
  same time. Both run on node24 and nest no actions, so nothing else here is
  waiting to warn.

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
