![Altiplano](https://raw.githubusercontent.com/aichholzer/altiplano/a045975ddd6b59f7c690fa5507a4f55a893c5ab8/banner.png)

# Altiplano

[![CI](https://github.com/aichholzer/altiplano/actions/workflows/ci.yml/badge.svg)](https://github.com/aichholzer/altiplano/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/aichholzer/altiplano/graph/badge.svg?token=l7Svxa1x0X)](https://codecov.io/gh/aichholzer/altiplano)
[![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue.svg)](https://www.python.org/)
[![PyPI version](https://img.shields.io/pypi/v/altiplano.svg)](https://pypi.org/project/altiplano/)
[![License](https://img.shields.io/github/license/aichholzer/altiplano)](LICENSE)

A small, dependable MCP server for [Vikunja](https://vikunja.io).<br />
Named after the Andean altiplano, the high plateau that is the Vicuña's native habitat.

Requires Python 3.10 or later.

## Install

### 1. Install uv

`uv` provides `uvx`, which runs Altiplano without a checkout. See [installation guide](https://docs.astral.sh/uv/getting-started/installation/).

### 2. Vikunja API token

In Vikunja, open Settings from the menu under your username, then API Tokens. See [Vikunja's API documentation](https://vikunja.io/docs/api-documentation/).

Give the token scopes covering the tools you intend to call.

### 3. Store the credentials

```bash
mkdir -p ~/.config/altiplano
printf 'VIKUNJA_URL=https://todo.example.com/api/v2\nVIKUNJA_API_TOKEN=tk_xxx\n' > ~/.config/altiplano/env
chmod 600 ~/.config/altiplano/env
```

> `VIKUNJA_URL` must end in `/api/v1` or `/api/v2`. That suffix selects the version, for example `https://todo.example.com/api/v2`.

> Vikunja 2.4.0 introduced `/api/v2`. Altiplano strips trailing slashes and enables v2 only for a URL ending in `/api/v2`; every other URL keeps its configured path and uses v1 request verbs. Use `/api/v2` when the server supports it.

Altiplano checks these sources in order:

1. `VIKUNJA_URL` and `VIKUNJA_API_TOKEN` environment variables.
2. A file containing `KEY=VALUE` pairs, defaulting to `~/.config/altiplano/env`.

Set `ALTIPLANO_CONFIG` before starting Altiplano to use a different file. Use absolute paths; `~` is not expanded in custom paths.

> Permissions broader than `600` produce a warning (on POSIX) but do not prevent startup. An unreadable file is ignored after a warning.

### 4. Add the MCP server entry

In your client's MCP configuration:

```json
{
  "altiplano": {
    "command": "uvx",
    "args": ["--refresh-package", "altiplano", "altiplano@latest"]
  }
}
```

> `--refresh-package altiplano` checks PyPI for a current release; if an older version still starts, close the client and run `uv cache clean altiplano`.

### 5. Verify with one call

Restart the client so it launches the server, then call `list_projects()`. Any list, an empty one included, means the install works.

> Altiplano speaks MCP over stdio. `uvx altiplano` prints nothing and waits for a client.

## Tools

<details>
<summary>Projects</summary>

- `list_projects()`: includes `parent_project_id` for sub-projects.
- `create_project(title, parent_project_id?, description?)`: pass `parent_project_id` to create a sub-project.

</details>

<details>
<summary>Tasks</summary>

- `list_tasks(project_id, filter?, sort_by?, page=1, per_page=50)`: Vikunja applies `filter` and `sort_by` before pagination.
- `search_tasks(query?, filter?, sort_by?, page=1, per_page=50)`: searches all visible projects and includes `project_id` in each result. Vikunja does not combine text search with `filter`.
- `get_task(task_id)`: returns full task detail. On v2, the description is Markdown.
- `create_task(project_id, title, description?, priority?, due_date?, start_date?, end_date?, percent_done?, is_favorite?, repeat_after?, repeat_mode?)`
- `update_task(task_id, title?, description?, done?, priority?, due_date?, start_date?, end_date?, percent_done?, is_favorite?, repeat_after?, repeat_mode?)`: changes only the supplied fields and requires at least one. Pass an empty string for `due_date`, `start_date`, or `end_date` to clear it.
- `move_task(task_id, project_id)`: moves labels, assignees, comments, relations, and dates with the task. The destination project assigns a new local `identifier`.
- `duplicate_task(task_id)`: copies the task, labels, assignees, attachments, and reminders into the same project. The copy receives a `copiedfrom` relation to the original.
- `bulk_create_tasks(project_id, tasks)`: creates a batch of tasks in one request, atomically and in the given order. Requires `/api/v2`. Each entry takes the same fields as `create_task`, `title` included, anything else is refused. Vikunja caps a batch at 100 and names the entry that fails. Returns one summary per created task.
- `bulk_update_tasks(task_ids, done?, priority?)`: requires at least one field. The request fails as a unit if the token lacks write access to any affected project.
- `set_reminders(task_id, reminders)`: replaces all reminders with the supplied ISO 8601 datetimes. Pass an empty list to clear them.
- `delete_task(task_id)`: soft-deletes the task and removes its comments, labels, and assignees. Vikunja retains the task for 30 days and provides no restore endpoint. Treat deletion as irreversible.

</details>

<details>
<summary>Kanban</summary>

- `list_kanban_views(project_id)`: includes the default and done bucket IDs.
- `list_buckets(project_id, view_id?)`: returns columns in board order and marks the default and done columns.
- `create_bucket(project_id, title, view_id?, limit?)`: adds a column at the right. Omit `limit`, or pass `0`, for no limit.
- `delete_bucket(project_id, bucket_id, view_id?)`: moves the column's tasks to the default column. Vikunja will not remove the last column.
- `list_bucket_tasks(project_id, view_id?, filter?)`: returns columns and their tasks. `task_count` remains the full count when Vikunja caps the returned task list.
- `list_task_buckets(task_id)`: returns one bucket for each kanban view.
- `move_task_to_bucket(task_id, bucket_id, view_id?)`: reads the project ID from the task.

Bucket behaviour:

- Bucket operations require a view with `view_kind="kanban"`. Without `view_id`, Altiplano uses the first kanban view in the project's view order.
- `bucket_configuration_mode="manual"` accepts explicit moves. In `filter` mode, filters determine the column.
- A move into a full bucket fails.
- Moving a task into the done column closes it; moving it out reopens it.
- A repeating task moved into the done column reopens in the default column.
- Setting `done` to true through `update_task` moves the task into the done column.

</details>

<details>
<summary>Relations</summary>

- `add_relation(task_id, other_task_id, relation_kind="related")`
- `remove_relation(task_id, other_task_id, relation_kind="related")`: use the same kind that created the relation.

> `get_task` returns `related_tasks`, grouped by kind. Supported kinds are `subtask`, `parenttask`, `related`, `duplicateof`, `duplicates`, `blocking`, `blocked`, `precedes`, `follows`, `copiedfrom`, and `copiedto`.

> `add_relation(task_id, other_task_id, "subtask")` makes `other_task_id` a child of `task_id`.

</details>

<details>
<summary>Labels</summary>

`list_labels()`, `create_label(title, hex_color?, description?)`, `delete_label(label_id)`, `add_label(task_id, label_id)`, `remove_label(task_id, label_id)`.

> `hex_color` is six hexadecimal digits without `#`. Deleting a label removes it from every task.

</details>

<details>
<summary>Comments</summary>

`list_comments(task_id)`, `add_comment(task_id, comment)`, `update_comment(task_id, comment_id, comment)`, `delete_comment(task_id, comment_id)`.

> `update_comment` replaces the complete text. Get `comment_id` from `list_comments`.

</details>

<details>
<summary>Assignees</summary>

`search_users(query)`, `list_assignees(task_id)`, `add_assignee(task_id, user_id)`, `remove_assignee(task_id, user_id)`.

> Use `search_users` to find the `user_id` required by the assignment tools.

</details>

## Shared HTTP server

`altiplano` speaks MCP over stdio, one process per client, credentials on every
machine. `altiplano-http` serves the same tools over Streamable HTTP from one
always-on host, with the Vikunja token held there and nothing else.

Each client presents its own bearer token. Altiplano mints them, stores only their
SHA-256, and revokes them one at a time.

### 1. Register a client

```bash
altiplano-clientkey add stefan-laptop
```

The token is printed once. Altiplano keeps only its hash. A lost token is replaced
by revoking the label and adding it again.

```bash
altiplano-clientkey list
altiplano-clientkey revoke stefan-laptop
```

A revocation takes effect on the next request. There is no restart.

> The store lives beside the credentials file, at `~/.config/altiplano/clients`, or
> wherever `ALTIPLANO_CLIENTS` points. It is written `chmod 600`.

### 2. Start the server

```bash
ALTIPLANO_HTTP_HOST=0.0.0.0 altiplano-http
```

| Variable | Default | Meaning |
|---|---:|---|
| `ALTIPLANO_HTTP_HOST` | `127.0.0.1` | Bind address. `0.0.0.0` listens on every IPv4 interface. |
| `ALTIPLANO_HTTP_PORT` | `8000` | TCP port. |
| `ALTIPLANO_HTTP_PATH` | `/mcp` | MCP endpoint path. |
| `ALTIPLANO_HTTP_ALLOWED_HOSTS` | localhost patterns | Accepted HTTP `Host` values, comma separated. |
| `ALTIPLANO_HTTP_ALLOWED_ORIGINS` | localhost origins | Accepted browser `Origin` values, comma separated. |
| `ALTIPLANO_CLIENTS` | `~/.config/altiplano/clients` | Client token store. |

Binding beyond loopback with an empty client store is refused at startup. Every
caller would otherwise act as the configured Vikunja identity, with every write and
delete tool available.

On loopback with an empty store the server runs open and says so in its log, which
keeps a fresh checkout testable before any key exists.

> `ALLOWED_HOSTS` and `ALLOWED_ORIGINS` prevent DNS rebinding. They are not
> authentication. A device can send any `Host` header it likes. The client tokens
> are the access control.

### 3. Point a client at it

```bash
claude mcp add --transport http altiplano \
  http://altiplano.home.arpa:8000/mcp \
  --header "Authorization: Bearer altp_..."
```

The equivalent in a client's own configuration:

```json
{
  "mcpServers": {
    "altiplano": {
      "type": "http",
      "url": "http://altiplano.home.arpa:8000/mcp",
      "headers": { "Authorization": "Bearer altp_..." }
    }
  }
}
```

Some clients name the transport `http`, others `streamable-http`, and some infer it
from the URL. A client that only launches subprocesses cannot reach an HTTP URL at
all; keep the stdio entry on those machines.

### 4. Verify it

```python
import asyncio
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

URL = "http://127.0.0.1:8000/mcp"
AUTH = {"Authorization": "Bearer altp_..."}


async def main() -> None:
    async with httpx2.AsyncClient(headers=AUTH) as http:
        async with streamable_http_client(URL, http_client=http) as (read, write, *_):
            async with ClientSession(read, write) as session:
                info = await session.initialize()
                print(info.server_info.name, info.server_info.version)
                listed = await session.list_tools()
                print(len(listed.tools), "tools")


asyncio.run(main())
```

Headers set on the `httpx2.AsyncClient` reach every request. Dropping the
`Authorization` header gives a `401`.

### Running it as a service with uv

`uv tool install` puts the commands in a directory of their own. Setting
`UV_TOOL_DIR` and `UV_TOOL_BIN_DIR` makes those paths deterministic, which matters
for a unit file running as a system account with no home directory of its own.

```bash
sudo install -d -o altiplano -g altiplano /opt/altiplano /etc/altiplano
sudo -u altiplano env \
  UV_TOOL_DIR=/opt/altiplano/tools \
  UV_TOOL_BIN_DIR=/opt/altiplano/bin \
  uv tool install "altiplano==1.3.0"
```

Pin the version. An unattended restart should not pick up a release nobody has
looked at.

Put the settings in `/etc/altiplano/service.env`, owned by the service account and
`chmod 600`:

```dotenv
VIKUNJA_URL=https://vikunja.home.arpa/api/v2
VIKUNJA_API_TOKEN=tk_xxxxxxxx
ALTIPLANO_CLIENTS=/etc/altiplano/clients
ALTIPLANO_HTTP_HOST=0.0.0.0
ALTIPLANO_HTTP_PORT=8000
ALTIPLANO_HTTP_ALLOWED_HOSTS=altiplano.home.arpa,altiplano.home.arpa:*
```

Register clients as the service account. The store then belongs to the user that
reads it:

```bash
sudo -u altiplano env ALTIPLANO_CLIENTS=/etc/altiplano/clients \
  /opt/altiplano/bin/altiplano-clientkey add stefan-laptop
```

#### systemd, for Debian and its derivatives

`/etc/systemd/system/altiplano.service`:

```ini
[Unit]
Description=Altiplano MCP server over HTTP
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=altiplano
Group=altiplano
EnvironmentFile=/etc/altiplano/service.env
ExecStart=/opt/altiplano/bin/altiplano-http
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/etc/altiplano

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now altiplano
journalctl -u altiplano -f
```

#### OpenRC, for Alpine

`/etc/init.d/altiplano`, `chmod 755`:

```sh
#!/sbin/openrc-run

name="altiplano"
description="Altiplano MCP server over HTTP"

supervisor="supervise-daemon"
command="/opt/altiplano/bin/altiplano-http"
command_user="altiplano:altiplano"
supervise_daemon_args="--respawn-delay 3"
output_log="/var/log/altiplano/altiplano.log"
error_log="/var/log/altiplano/altiplano.log"

depend() {
    need net
}

start_pre() {
    checkpath --directory --owner altiplano:altiplano --mode 0755 /var/log/altiplano
}
```

OpenRC sources `/etc/conf.d/altiplano` on its own. Environment variables there need
exporting to reach the daemon:

```sh
export VIKUNJA_URL="https://vikunja.home.arpa/api/v2"
export VIKUNJA_API_TOKEN="tk_xxxxxxxx"
export ALTIPLANO_CLIENTS="/etc/altiplano/clients"
export ALTIPLANO_HTTP_HOST="0.0.0.0"
export ALTIPLANO_HTTP_PORT="8000"
export ALTIPLANO_HTTP_ALLOWED_HOSTS="altiplano.home.arpa,altiplano.home.arpa:*"
```

```bash
sudo chmod 600 /etc/conf.d/altiplano
sudo rc-update add altiplano default
sudo rc-service altiplano start
sudo rc-service altiplano status
```

### Behind a Cloudflare tunnel

The tunnel authenticates the connection and Altiplano authenticates the client, and
the two are worth keeping separate: revoking one client's access stays a local
operation, and it survives a change of transport.

Two things change when the tunnel goes up. `ALTIPLANO_HTTP_ALLOWED_HOSTS` needs the
public hostname, which is the `Host` the tunnel presents. And Cloudflare Access
authenticates browsers through SSO, while an MCP client posting a bearer token is
not a browser: non-interactive clients need a Cloudflare service token, sent as
`CF-Access-Client-Id` and `CF-Access-Client-Secret` alongside their Altiplano
bearer.

Bind to loopback once the tunnel reaches the server, and let `cloudflared` be the
only thing that connects.

### What a shared server does not give you

One Vikunja token serves every client. Every client therefore acts as the same
Vikunja identity with the same permissions. Per-client tokens control who may
connect and give each client a name in the log. They do not partition what a client
may do.

Use a dedicated Vikunja service account with only the scopes the tools you expose
need. Per-user Vikunja identity would mean selecting credentials from the request
context, which is a different design.

## Guidance

Altiplano documents its own use in three places.

- The handshake sends usage rules: resolve ids by name, which calls cannot be undone, how to close a task. Clients apply them on connect.
- The `altiplano_guide` prompt holds the full version, with cross-tool sequencing and the v1 and v2 differences. Clients list it as `Using Altiplano`.
- `AGENTS.md` covers working on this repository, and installing Altiplano for someone else. `CLAUDE.md` imports it, for Claude Code.

## Task behaviour

### Task updates

Both API versions preserve fields omitted from an update.

On v1, `POST /tasks/{id}` replaces the complete task. Altiplano reads the current task, merges the changes, and writes it back for `update_task`, `set_reminders`, and `move_task`. Each call costs one extra request.

On v2, those calls use `PATCH`. A description change uses a read and full replacement because `PATCH` does not apply Markdown conversion.

When a v2 read includes an ETag, Altiplano sends it in `If-Match` during the replacement. Vikunja returns HTTP 412 if the task changed between the read and write. Read the task again before retrying. V1 and v2 responses without an ETag have no concurrency guard.

### Dates, priority, and progress

- Priorities use Vikunja's scale: `0` Unset, `1` Low, `2` Medium, `3` High, `4` Urgent, and `5` DO NOW.
- Dates are ISO 8601 datetimes. `start_date` and `end_date` define the work window; `due_date` is the deadline.
- An empty string clears a date by writing Vikunja's zero time, `0001-01-01T00:00:00Z`.
- `percent_done` is a fraction. A quarter complete is `0.25`. Vikunja does not clamp the value, and `50` remains `50`.

### Repeating tasks

`repeat_after` is measured in seconds. Completing a repeating task reopens it and advances its due date and reminders.

`repeat_mode` values:

- `0`: advance the existing dates by `repeat_after`.
- `1`: repeat monthly and ignore `repeat_after`.
- `2`: calculate the next occurrence from the completion date.

Vikunja's [v1 specification](https://try.vikunja.io/api/v1/docs.json) lists `3` in the `repeat_mode` description; its generated enum defines the final mode as `2`.

A repeating task with no dates reopens immediately and cannot remain closed. Set a `due_date` when enabling repetition.

### Identifiers and errors

The Vikunja UI displays a project-local `identifier`, such as `#50`. API calls use the global numeric `id`.

Failed requests include Vikunja's `detail`, `message`, or `title` field when present. Altiplano also includes a non-zero numeric error code when Vikunja provides one. Redirects are errors and include the destination.

Requests time out after 30 seconds and are not retried.

### Markdown

Vikunja stores task descriptions and comments as HTML. Altiplano requests Markdown conversion for full task reads, task creates, project creates, comment reads and writes, and full task replacements.

The v1 API has no Markdown conversion. Markdown sent through v1 is stored literally.

V2 partial updates through `update_task`, `move_task`, and `set_reminders` return the stored HTML description. Call `get_task` to retrieve Markdown. An `update_task` call that changes `description` uses a full replacement.

Vikunja resolves `@mentions` during Markdown conversion and notifies the named user.

## Compatibility

Tested with Vikunja 2.5.0 against `/api/v1` and `/api/v2`.

Identified issues:

- On Vikunja 2.5.0, the v2 grouped-bucket route used by `list_bucket_tasks` may return HTTP 401 when the token works elsewhere. Try a new full-permission token or `/api/v1`. The same diagnosis is returned for every v2 HTTP 401 from that route. Verify the token itself too.
- On Vikunja 2.3.0, `list_assignees` returns HTTP 500. The endpoint worked on 2.5.0.

## Contributing

Enable the pre-commit hook once per clone:

```bash
git config core.hooksPath hooks
```

The hook runs Ruff 0.16.4 over `src` and `tests`, then pytest with a 90 percent coverage minimum. CI runs Ruff in one job and pytest on Python 3.10 and 3.13.

## Run

```bash
uv run altiplano                                      # development checkout
uvx --from /your/local/path altiplano                 # local package path
uvx --refresh-package altiplano altiplano@latest      # current PyPI release

uv run altiplano-http                                 # HTTP transport, loopback
uv run altiplano-clientkey add laptop                 # mint a client token
```

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

Register a tool group by adding its module and importing it from `server.py`. Add its tools to the routing-table test and the smoke test's exact list.

> Pull requests are always welcome.

## Licence

[MIT](./LICENSE).

## Support

[RTFM](https://en.wikipedia.org/wiki/RTFM), then RTFS. If you are still stuck, or just need an additional feature, file an [issue](https://github.com/aichholzer/altiplano/issues).

<div align="center">
✌🏼
</div>
