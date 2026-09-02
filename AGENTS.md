# Working on Altiplano

Altiplano is an MCP server exposing Vikunja as tools. Python, `uv`, no runtime
dependencies beyond `mcp` and `httpx`.

This file is for an agent working inside a checkout. The wheel contains
`src/altiplano` alone, so an agent that only calls the tools gets its guidance
from the handshake instructions and the `altiplano_guide` prompt.

## Installing it for someone

If the job is to get Altiplano running for a user, follow `## Install` in
`README.md`: `uv`, a Vikunja API token, a credentials file, the client's MCP
entry, then one `list_projects()` call to confirm.

Three things go wrong there. `VIKUNJA_URL` has to end in `/api/v1` or `/api/v2`,
because that suffix alone selects the version. The token belongs in the
credentials file, which leaves the MCP entry free of secrets. And a terminal run
of `uvx altiplano` prints nothing and waits, since it speaks MCP over stdio.

## Commands

```bash
uv sync --locked                    # install, exactly as the lock file says
uv run pytest -q                    # tests, with the 90 percent coverage gate
uvx ruff@0.16.4 check src tests     # lint, the pinned version CI uses
uv run altiplano                    # run the server from a checkout
```

The coverage floor lives in `pyproject.toml` as `--cov-fail-under`, so a local
run and CI enforce the same number. Enable the pre-commit hook once per clone
with `git config core.hooksPath hooks`; it runs the lint and the tests above.

## Layout

```text
src/altiplano/
  app.py       MCP instance imported by the tool and prompt modules
  config.py    Credential resolution and credential-file parsing
  api.py       API-version handling, requests, and response shaping
  prompts.py   The usage guidance served as an MCP prompt
  tools/       One module for each tool group
  server.py    Registration and the main entry point
```

Adding a tool means three edits beyond the tool itself: import its module from
`server.py`, add a routing case in `tests/test_tools.py`, and add the name to the
exact set in `tests/test_smoke.py`. Both tests fail on an unregistered or
undocumented tool.

## Conventions

- Tool docstrings are shipped text. MCP clients read them as tool descriptions,
  so a wrong one misleads every caller, silently.
- One concern per commit, and no task tracker references in commit messages.
- Every change bumps the version across `pyproject.toml`,
  `src/altiplano/__init__.py` and `uv.lock`, with a `CHANGELOG.md` entry under the
  matching heading. A new tool is a minor bump.
- Australian spelling in prose. No em dashes.
- Never describe something by what it is not. Write the mechanism instead.

## Two API versions

`VIKUNJA_URL` decides everything. A URL ending in `/api/v2` selects v2; anything
else uses v1 verbs. There is no probing and no separate setting.

The differences are absorbed in `api.py`: create and update verbs, the collection
envelope v2 wraps results in, the search parameter name, and Markdown conversion.
A tool module should not branch on the version unless the endpoint itself differs.

## Driving the tools

The server ships the full runtime guidance as an MCP prompt named
`altiplano_guide`. Load it before making calls: clients list it in a prompt picker
as `Using Altiplano`, and an agent can fetch it directly with a `prompts/get` call
for that name. It covers id resolution, cross-tool sequencing, the calls that
cannot be undone, and the v1 and v2 differences.

Three rules matter before anything else, because they are where an agent goes
wrong first:

- Never guess an id. Resolve a project, label, bucket, view or user by name with
  the matching list or search tool, then carry the id.
- `delete_task()`, `delete_label()` and `delete_bucket()` cannot be undone
  through this API. Confirm the target id first.
- Moving a task between kanban buckets changes whether it is done.
