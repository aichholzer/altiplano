![Altiplano](https://raw.githubusercontent.com/aichholzer/altiplano/a045975ddd6b59f7c690fa5507a4f55a893c5ab8/banner.png)

# Altiplano

[![CI](https://github.com/aichholzer/altiplano/actions/workflows/ci.yml/badge.svg)](https://github.com/aichholzer/altiplano/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/aichholzer/altiplano/graph/badge.svg?token=l7Svxa1x0X)](https://codecov.io/gh/aichholzer/altiplano)
[![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue.svg)](https://www.python.org/)
[![PyPI version](https://img.shields.io/pypi/v/altiplano.svg)](https://pypi.org/project/altiplano/)
[![License](https://img.shields.io/github/license/aichholzer/altiplano)](LICENSE)
[![Altiplano MCP server](https://glama.ai/mcp/servers/aichholzer/altiplano/badges/score.svg)](https://glama.ai/mcp/servers/aichholzer/altiplano)

A small, dependable MCP server for [Vikunja](https://vikunja.io).<br />
Named after the Andean altiplano, the high plateau that is the Vicuña's native habitat.

Altiplano runs locally through `uvx`, or as a stand-alone HTTP service that several people share on one endpoint, each acting as their own Vikunja user. Both modes expose the same tools and the same guidance.

## Choose how to use Altiplano

|                                    | Local, through `uvx`                                            | Shared, over HTTP                                                                 |
| ---------------------------------- | --------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Where Altiplano runs               | On your computer, launched by your MCP client.                  | On a host running Altiplano as a stand-alone service.                             |
| How your MCP client connects       | Runs `uvx` and communicates over stdio.                         | Connects to the service URL with a valid bearer token.                            |
| Requirements on your computer      | `uv`, Python 3.10 or later, and an MCP client supporting stdio. | An MCP client supporting Streamable HTTP and a configured `Authorization` header. |
| Where the Vikunja credentials live | On each computer running Altiplano.                             | On the service host, one token per client.                                        |
| Setup                              | [Use locally](#use-locally-with-uvx)                            | [Use over HTTP](#use-over-http)                                                   |

Connecting to an existing HTTP service needs its URL and an Altiplano client token. You do not need to install Altiplano, `uv`, or Python on the client.

The configuration examples below use an `mcpServers` block. Adapt the surrounding structure to your MCP client's configuration format.

## Use locally with uvx

Your MCP client launches Altiplano as a local subprocess and communicates with it over stdio. Each client manages its own Altiplano process.

### 1. Install uv

[`uv`](https://docs.astral.sh/uv/getting-started/installation/) provides the `uvx` command, which runs Altiplano from PyPI without a repository checkout.

### 2. Configure Vikunja credentials

Create an API token in Vikunja under Settings → API Tokens, reachable from the menu under your username. Give it the scopes covering the tools you intend to call. See [Vikunja's API documentation](https://vikunja.io/docs/api-documentation/).

```bash
mkdir -p ~/.config/altiplano
```

Create `~/.config/altiplano/env` containing:

```dotenv
VIKUNJA_URL=https://altiplano.example.com/api/v2
VIKUNJA_API_TOKEN=tk_replace_me
```

Restrict the file's permissions:

```bash
chmod 600 ~/.config/altiplano/env
```

Altiplano checks these sources in order:

1. The `VIKUNJA_URL` and `VIKUNJA_API_TOKEN` environment variables.
2. A file containing `KEY=VALUE` pairs, defaulting to `~/.config/altiplano/env`.

Set `ALTIPLANO_CONFIG` before starting Altiplano to read a different file. Use absolute paths; `~` is not expanded in custom paths.

> `VIKUNJA_URL` must end in `/api/v1` or `/api/v2`. That suffix selects the version, for example `https://altiplano.example.com/api/v2`.

> Vikunja 2.4.0 introduced `/api/v2`. Altiplano strips trailing slashes and enables v2 only for a URL ending in `/api/v2`. Every other URL keeps its configured path and uses v1 request verbs. Use `/api/v2` when the server supports it.

> Permissions broader than `600` produce a warning on POSIX systems and startup continues. An unreadable file is ignored after a warning.

### 3. Configure your MCP client

Add a local server entry:

```json
{
  "mcpServers": {
    "altiplano": {
      "command": "uvx",
      "args": ["--refresh-package", "altiplano", "altiplano@latest"]
    }
  }
}
```

> `--refresh-package altiplano` checks PyPI for a current release. If an older version still starts, close the client and run `uv cache clean altiplano`.

### 4. Verify with one call

Restart or reconnect your MCP client, then call `list_projects()`. Any list, an empty one included, confirms that Altiplano reaches Vikunja with the configured credentials.

> Altiplano speaks MCP over stdio. Running `uvx altiplano` in a terminal prints nothing and waits for a client on stdin and stdout.

## Use over HTTP

`altiplano-http` serves the same tools over Streamable HTTP from one always-on host. Each client presents its own bearer token, which Altiplano mints, stores as a SHA-256 hash, and revokes one at a time.

The service must already be running and reachable from the computer running your MCP client. Adding its URL to your client configuration connects to the service. It does not start it. [`DEPLOYMENT.md`](./DEPLOYMENT.md) covers standing one up.

One endpoint serves several people, each as their own Vikunja user. There is no shared Vikunja API token: the host holds one per registered client, and a request is made with the token belonging to the client that sent it. Two people on one service reach their own projects and their own tasks, with Vikunja applying its own permissions to each.

The operator records your Vikunja token when registering your client. Give them one created from your own Vikunja account. A client with no token registered for it is refused.

### Connect to an existing service

Obtain the MCP endpoint URL and a client token from whoever operates the service. Each client should have its own token.

```bash
claude mcp add --transport http altiplano \
  https://altiplano.example.com/mcp \
  --header "Authorization: Bearer altp_replace_me"
```

The equivalent in a client's own configuration:

```json
{
  "mcpServers": {
    "altiplano": {
      "type": "http",
      "url": "https://altiplano.example.com/mcp",
      "headers": {
        "Authorization": "Bearer altp_replace_me"
      }
    }
  }
}
```

Replace the URL with the real endpoint, including its port and path where required. The example above assumes HTTPS is configured for the service.

Some clients name the transport `streamable-http`, others `http`, and some infer it from the URL. Use the form your client supports. A client that only launches subprocesses cannot reach an HTTP URL at all; keep the stdio entry on those machines.

The bearer token here is an **Altiplano client token**, issued by `altiplano-clientkey`, and it says which client is calling. Your **Vikunja API token** is a separate thing: it stays on the service host, registered against your client, and it is the identity your requests act as. Give the operator a token from your own Vikunja account.

Restart or reconnect your MCP client, then call `list_projects()`. A successful response confirms the connection, the client token, and access to Vikunja.

Keep the client token private. It grants access to the service and every tool it exposes. Ask the operator to revoke and replace a lost or exposed token. A revocation applies to the next request.

> The supported path is a client that sends the header you configure. Altiplano answers an unauthenticated request with a bare `WWW-Authenticate: Bearer` challenge and publishes no OAuth metadata. A client may still probe the well-known metadata URLs on its own initiative and will get a `404`. A client that can only obtain credentials through an OAuth flow is not supported here.

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

## Guidance

Altiplano documents its own use in four places.

- The handshake sends usage rules: resolve ids by name, which calls cannot be undone, how to close a task. Clients apply them on connect.
- The `altiplano_guide` prompt holds the full version, with cross-tool sequencing and the v1 and v2 differences. Clients list it as `Using Altiplano`.
- [`AGENTS.md`](./AGENTS.md) covers working on this repository, and installing Altiplano for someone else. [`CLAUDE.md`](./CLAUDE.md) imports it, for Claude Code.
- [`DEPLOYMENT.md`](./DEPLOYMENT.md) covers running the HTTP transport as a service on a host: installing with `uv` under a service account, every environment variable the transport reads, minting client tokens, a systemd unit for Debian and an OpenRC script for Alpine, firewalling the listener, putting it behind a Cloudflare tunnel, and the checks to run before the deployment counts as done.

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

Pull requests are always welcome. [`CONTRIBUTING.md`](./CONTRIBUTING.md) covers the development setup, the commands, the pre-commit hook, the source layout, and what a pull request needs. Taking part means agreeing to the [code of conduct](./CODE_OF_CONDUCT.md).

## Licence

[MIT](./LICENSE).

## Support

[RTFM](https://en.wikipedia.org/wiki/RTFM), then RTFS. If you are still stuck, or just need an additional feature, file an [issue](https://github.com/aichholzer/altiplano/issues).

<div align="center">
✌🏼
</div>
