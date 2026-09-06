# Contributing to Altiplano

Pull requests are always welcome. Taking part means agreeing to the
[code of conduct](./CODE_OF_CONDUCT.md).

Requires Python 3.10 or later and
[`uv`](https://docs.astral.sh/uv/getting-started/installation/).

## Setup

```bash
git clone https://github.com/aichholzer/altiplano.git
cd altiplano
uv sync --locked
git config core.hooksPath hooks
```

That last line enables the pre-commit hook, once per clone. It runs Ruff 0.16.4 over
`src` and `tests`, then pytest with a 90 percent coverage minimum. CI runs Ruff in one
job and pytest on Python 3.10 and 3.13.

## Commands

```bash
uv sync --locked                                      # install, exactly as the lock file says
uv run pytest -q                                      # tests, with the coverage gate
uvx ruff@0.16.4 check src tests                       # lint, the pinned version CI uses

uv run altiplano                                      # development checkout
uvx --from /your/local/path altiplano                 # local package path
uvx --refresh-package altiplano altiplano@latest      # current PyPI release

uv run altiplano-http                                 # HTTP transport, loopback
uv run altiplano-clientkey add laptop                 # mint a client token
```

The coverage floor lives in `pyproject.toml` as `--cov-fail-under`. A local run and CI
enforce the same number.

`altiplano-http` binds `127.0.0.1:8000` and keeps authentication on.
`ALTIPLANO_HTTP_ALLOW_UNAUTHENTICATED=1` turns it off for development. Any bind
address other than loopback refuses that variable.

## Layout

```text
src/altiplano/
  app.py           MCP instance imported by the tool and prompt modules
  config.py        Credential resolution and credential-file parsing
  api.py           API-version handling, requests, and response shaping
  prompts.py       The usage guidance, served as a prompt
  tools/           One module for each tool group
  server.py        Registration and the stdio entry point
  clients.py       The per-client token store for the HTTP transport
  http_server.py   The HTTP entry point and its bearer-token gate
  clientkey.py     The altiplano-clientkey command
```

## Adding a tool

Register a tool group by adding its module and importing it from `server.py`. Add its
tools to the routing-table test in `tests/test_tools.py` and to the exact set in
`tests/test_smoke.py`. Both tests fail on an unregistered or undocumented tool.

Tool docstrings are shipped text. MCP clients read them as tool descriptions. A wrong
one misleads every caller, silently.

## Two API versions

`VIKUNJA_URL` decides everything. A URL ending in `/api/v2` selects v2, and anything
else uses v1 verbs. There is no probing and no separate setting.

`api.py` absorbs the differences: the create and update verbs, the collection envelope
v2 wraps results in, the search parameter name, and Markdown conversion. A tool module
should not branch on the version unless the endpoint itself differs.

## What a pull request needs

- One concern per commit.
- A version bump across `pyproject.toml`, `src/altiplano/__init__.py`, and `uv.lock`,
  with a `CHANGELOG.md` entry under the matching heading. A new tool is a minor bump.
- Ruff clean and the test suite passing.
- Australian spelling in prose, and no em dashes.

Documentation-only changes skip the version bump and the change log entry.
