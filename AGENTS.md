# Working on Altiplano

Altiplano is an MCP server exposing Vikunja as tools. Python, `uv`, no runtime
dependencies beyond `mcp` and `httpx`.

This file is for an agent working inside a checkout. The wheel contains
`src/altiplano` alone. An agent that only calls the tools takes its guidance from
the handshake instructions and the `altiplano_guide` prompt.

[`CONTRIBUTING.md`](./CONTRIBUTING.md) covers the same ground for a human
contributor. A change here to the commands, the layout, or the conventions belongs in
that file too.

## Installing it for someone

Ask which shape they want first. `README.md` opens with the choice under
`## Choose how to use Altiplano`.

For a local install, follow `## Use locally with uvx`: `uv`, a Vikunja API token, a
credentials file, the client's MCP entry, then one `list_projects()` call to confirm.

For a client connecting to an HTTP service, follow `## Use over HTTP`, under
`### Connect to an existing service`. That needs the endpoint URL and a client token
from whoever operates it, and installs nothing.

For a shared HTTP deployment on a host, `DEPLOYMENT.md` has the service account,
the systemd unit, the OpenRC script, and the client token commands.

Three things go wrong there. `VIKUNJA_URL` has to end in `/api/v1` or `/api/v2`,
and that suffix alone selects the version. The token belongs in the credentials
file. The MCP entry then holds no secrets. And a terminal run of `uvx altiplano`
prints nothing and waits. It speaks MCP over stdio.

## Commands

```bash
uv sync --locked                    # install, exactly as the lock file says
uv run pytest -q                    # tests, with the 90 percent coverage gate
uvx ruff@0.16.4 check src tests     # lint, the pinned version CI uses
uv run altiplano                    # stdio server, from a checkout
uv run altiplano-http               # HTTP server, loopback, authentication on
uv run altiplano-clientkey list     # the clients the HTTP server accepts
```

The coverage floor lives in `pyproject.toml` as `--cov-fail-under`. A local run
and CI enforce the same number. Enable the pre-commit hook once per clone with
`git config core.hooksPath hooks`; it runs the lint and the tests above.

## Layout

```text
src/altiplano/
  app.py           MCP instance imported by the tool and prompt modules
  config.py        Credential resolution and credential-file parsing
  api.py           API-version handling, requests, and response shaping
  prompts.py       The usage guidance served as an MCP prompt
  tools/           One module for each tool group
  server.py        Registration and the stdio entry point
  clients.py       The per-client token store for the HTTP transport
  http_server.py   The HTTP entry point and its bearer-token gate
  clientkey.py     The altiplano-clientkey command
```

Adding a tool means three edits beyond the tool itself: import its module from
`server.py`, add a routing case in `tests/test_tools.py`, and add the name to the
exact set in `tests/test_smoke.py`. Both tests fail on an unregistered or
undocumented tool. A new tool reaches both transports with no further work.

## Conventions

- Tool docstrings are shipped text. MCP clients read them as tool descriptions. A
  wrong one misleads every caller, silently.
- One concern per commit, and no task tracker references in commit messages.
- Every change bumps the version across `pyproject.toml`,
  `src/altiplano/__init__.py`, and `uv.lock`, with a `CHANGELOG.md` entry under the
  matching heading. A new tool is a minor bump.
- Australian spelling in prose. No em dashes, and no emoji.
- Oxford comma in every list.
- Never describe something by what it is not. Write the mechanism.
- Nothing hangs off the end of a finished sentence. A trailing clause opening with
  `so`, `because`, `since`, or `which means` becomes its own sentence, or goes.

## Two transports

`server.py` runs stdio, one process per client. `http_server.py` serves the same
`MCPServer` over Streamable HTTP to many clients, gated on per-client bearer tokens
that `clients.py` stores as SHA-256 digests.

Each record in that store also holds the Vikunja API token its client acts with. The
gate resolves the bearer token to a record and binds that Vikunja token with
`config._acting_as` for the rest of the call, and `config._headers()` reads it back.
That is a `ContextVar`, and it stays isolated per request across overlapping calls on
one session. A record with no Vikunja token is refused with a 403; the server's own
`VIKUNJA_API_TOKEN` is not a fallback for an HTTP caller. `VIKUNJA_URL` has no
per-client override, which keeps `api._version()` reading one API version.

The store carries a version line, `# altiplano clients v2`. A file without one is v1,
its records load with an empty Vikunja token so their labels stay visible, and the gate
refuses each of them. `created` is a timestamp full of colons and stays the last field.
Appending a fourth field with no version line would have parsed that timestamp as a
token.

The gate is ASGI middleware wrapping `mcp.streamable_http_app()`. The SDK's
`token_verifier` is
refused without `AuthSettings`; `AuthSettings` requires `issuer_url` and
`resource_server_url`; and setting it makes the SDK publish
`/.well-known/oauth-protected-resource` and wrap the endpoint in
`RequireAuthMiddleware`. A compliant client then follows that metadata to an OAuth
authorisation server that does not exist. `ServerMiddleware` is protocol-tier and
never sees an HTTP header.

Two rules for anything touching `http_server.py`:

- The wrapper passes every non-`http` scope straight through. The `lifespan` scope
  starts the MCP session manager, and no unit test on the auth path would notice it
  going missing.
- The middleware is written against the ASGI interface with no Starlette import.
  `uvicorn` is the only dependency this transport adds.

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

Three rules matter before anything else. They are where an agent goes wrong first:

- Never guess an id. Resolve a project, label, bucket, view, or user by name with
  the matching list or search tool, then carry the id.
- `delete_task()`, `delete_label()`, and `delete_bucket()` cannot be undone
  through this API. Confirm the target id first.
- Moving a task between kanban buckets changes whether it is done.
