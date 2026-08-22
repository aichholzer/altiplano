![Altiplano](https://github.com/aichholzer/altiplano/blob/a045975ddd6b59f7c690fa5507a4f55a893c5ab8/banner.png)

[![CI](https://github.com/aichholzer/altiplano/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/aichholzer/altiplano/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/aichholzer/altiplano/graph/badge.svg?token=l7Svxa1x0X)](https://codecov.io/gh/aichholzer/altiplano)
[![Python](https://img.shields.io/badge/python-%3E%3D3.10-blue.svg)](https://www.python.org/)
[![PyPI version](https://img.shields.io/pypi/v/altiplano.svg)](https://pypi.org/project/altiplano/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/altiplano.svg)](https://pypi.org/project/altiplano/)
[![License](https://img.shields.io/github/license/aichholzer/altiplano)](LICENSE)

# Altiplano

A small, dependable MCP server for [Vikunja](https://vikunja.io). Named after the Andean altiplano, the high plateau that is the Vicuña's native habitat.

Filtering and sorting are passed straight to the Vikunja API (server-side), so there is no client-side filtering engine and no paginate-then-filter pitfall.

## Tools

Projects:
- `list_projects` (includes `parent_project_id`, shows sub-project nesting)
- `create_project` (title, parent_project_id?, description?): pass `parent_project_id` for a sub-project

Tasks:
- `list_tasks` (project_id, filter, sort_by, page, per_page)
- `search_tasks` (query?, filter?, sort_by?, page, per_page): the same, across every project you can see, and each result carries its `project_id`
- `get_task` (task_id)
- `create_task` (project_id, title, description?, priority?, due_date?, start_date?, end_date?, percent_done?, is_favorite?, repeat_after?, repeat_mode?)
- `update_task` (task_id, title?, description?, done?, priority?, due_date?, start_date?, end_date?, percent_done?, is_favorite?, repeat_after?, repeat_mode?): only the fields you pass change. Pass an empty string to any of the three dates to clear it
- `move_task` (task_id, project_id): moves a task to another project. Its project-local `identifier` is reassigned on arrival
- `duplicate_task` (task_id): copies a task into the same project, with a `copiedfrom` relation back to the original
- `bulk_update_tasks` (task_ids, done?, priority?): sets those fields on many tasks in one request, writing only the fields you pass
- `set_reminders` (task_id, reminders): replaces the task's reminders with the given ISO 8601 datetimes; empty list clears
- `delete_task` (task_id): soft-deletes the task along with its comments, labels and assignees. Vikunja keeps it for 30 days but exposes no way to restore it, so treat this as irreversible

Kanban:
- `list_kanban_views` (project_id): a project's kanban views, with the ids of their default and done buckets
- `list_buckets` (project_id, view_id?): the columns in board order, flagging which is the default and which is done
- `create_bucket` (project_id, title, view_id?, limit?): adds a column on the right-hand end
- `delete_bucket` (project_id, bucket_id, view_id?): removes a column and sends its tasks to the default one
- `list_bucket_tasks` (project_id, view_id?, filter?): the columns with their tasks. See the warning below about v2
- `list_task_buckets` (task_id): which bucket a task sits in, one entry per kanban view
- `move_task_to_bucket` (task_id, bucket_id, view_id?): moving into the done bucket marks the task done, and moving it out un-marks it. The project is read from the task rather than passed in

Buckets belong to a view, not to a project, so each of these resolves one first. `view_id` is optional and the first kanban view is used, which is the only one most projects have.

> `list_bucket_tasks` does not work on `/api/v2` with an API token, on Vikunja 2.5.0. That one route answers 401 while every other route here accepts the same token, and the v2 spec says it should accept one too, so the likely cause is a token created before the route existed and therefore lacking permission for it. A token created with full permissions may fix it; `/api/v1` serves the same data either way. The tool says as much when it hits that 401, rather than repeating Vikunja's claim that your token is invalid.

Relations:
- `add_relation` (task_id, other_task_id, relation_kind?): relates two tasks, defaulting to a plain `related` link
- `remove_relation` (task_id, other_task_id, relation_kind?): the kind must match the one the relation was created with

There is no `list_relations`: `get_task` already returns `related_tasks`, grouped by kind.

Labels:
- `list_labels`
- `create_label` (title, hex_color?, description?): `hex_color` is six hex digits with no leading `#`
- `delete_label` (label_id): deletes the label everywhere, taking it off every task that carries it
- `add_label` (task_id, label_id)
- `remove_label` (task_id, label_id)

Comments:
- `list_comments` (task_id)
- `add_comment` (task_id, comment)
- `update_comment` (task_id, comment_id, comment): replaces the comment text; get `comment_id` from `list_comments`
- `delete_comment` (task_id, comment_id)

Assignees:
- `search_users` (query): find a `user_id` to assign
- `list_assignees` (task_id)
- `add_assignee` (task_id, user_id)
- `remove_assignee` (task_id, user_id)

## Credentials (no secrets in mcp.json)

The server resolves two values, in order:

1. Environment variables `VIKUNJA_URL` and `VIKUNJA_API_TOKEN`.
2. A per-device file of `KEY=VALUE` lines, default `~/.config/altiplano/env`
   (override the path with `ALTIPLANO_CONFIG`).

`VIKUNJA_URL` is the base API URL including the version prefix (e.g. `https://todo.example.com/api/v2`).

Recommended so the your `mcp.json` carries no secrets:

- Drop a per-device file and lock it down:
  ```bash
  mkdir -p ~/.config/altiplano
  printf 'VIKUNJA_URL=https://todo.example.com/api/v2\nVIKUNJA_API_TOKEN=tk_xxx\n' > ~/.config/altiplano/env
  chmod 600 ~/.config/altiplano/env
  ```
- Or inject via the launcher's environment (e.g. a systemd unit `EnvironmentFile=` pointing at a `chmod 600` file), which the server inherits.
- For stronger setups, source the token from a secret manager/keychain at launch and export it into the environment.

Then `mcp.json` only needs the command, no `env` block, no plain-text secrets:

```json
{
  "altiplano": {
    "command": "uvx",
    "args": ["--refresh-package", "altiplano", "altiplano@latest"]
  }
}
```

> `uvx` caches aggressively. It fetches a version on first run and reuses it afterwards, and it separately caches the list of versions it knows about. So `altiplano@latest` on its own can keep launching an older build for a while after a release, and restarting your client does not help.
>
> `--refresh-package altiplano` revalidates against PyPI on each start, which is what makes `@latest` actually mean latest. It costs roughly a tenth of a second.
>
> If you are already stuck on an older build, quit your MCP client first, then run `uv cache clean altiplano`. It has to be quit: while the client is running it holds that cache, and the command will sit and wait for it.

## Choosing the API version

Vikunja 2.4.0 added a v2 API alongside v1, and this MCP deals with both. The version comes from the URL you configure, so there is nothing else to set:

| `VIKUNJA_URL` ends in | you get |
| --- | --- |
| `/api/v2` | the v2 API |
| `/api/v1` | the v1 API |

Prefer `/api/v2` if your server has it. Stay on `/api/v1` only for older servers; every tool behaves the same either way, though v1 pays for it in requests and cannot exchange Markdown.

### On v1, an update costs two requests

Only the fields you pass ever change, on either version. Getting there differs.

v1 has no partial update: `POST /tasks/{id}` is a replace, so a body carrying only the changed fields resets everything else to its zero value. Left alone, `update_task(task_id=42, priority=4)` would blank the description, `update_task(task_id=42, done=True)` would discard the description, priority and dates, and `set_reminders` would do the same through the same endpoint.

So on v1, `update_task` and `set_reminders` read the task first and write it back with your changes merged in. That is one extra request per update, and it is why v2 is worth pointing at.

v2 needs no read. It uses `PATCH`, omitted fields survive, and only a description forces the longer path, for the Markdown reason below.

One thing v1 cannot do is notice a concurrent edit. The read-then-write on v2 sends the ETag back as `If-Match`, so a task that changed in between fails loudly. v1 has no ETag to send, so a simultaneous edit from elsewhere is overwritten.

## Markdown descriptions and comments (v2 only)

Vikunja stores task descriptions and comments as HTML. On v2 this server asks it to convert, so you write and read Markdown:

```
create_task(project_id=12, title="Ship it", description="**bold** and a [link](https://example.com)")
```

Vikunja stores that as `<p><strong>bold</strong> and a <a href="...">link</a></p>`, and `get_task` hands it back as the Markdown you wrote. The same applies to `update_task`, `create_project`, `add_comment` and `update_comment`.

On v1 there is no conversion and the fields are HTML, so Markdown you send is stored verbatim and renders as literal asterisks.

Two things worth mentioning:

- A partial update is the exception in both directions. `update_task` and `set_reminders` return the task with its description as the stored HTML, because v2 will not convert on a `PATCH`. Call `get_task` if you want it back as Markdown.
- v2 only converts on create and on full replace, never on a partial update. So changing a description reads the task first and writes it back whole, which costs one extra request. The ETag from that read goes back as `If-Match`, so a task that something else wrote to in between fails with a message telling you to read it again, rather than having that edit quietly overwritten. Updates that do not touch the description stay a single partial update.
- Vikunja resolves `@mentions` during conversion, so writing `@someone` in a description notifies them.

## Run

```bash
uv run altiplano                        # dev, from this directory
uvx --from /your/local/path altiplano   # local path
uvx altiplano@latest                    # from PyPI, refreshing the cache
```

## Notes

- Vikunja priority scale: 0 Unset, 1 Low, 2 Medium, 3 High, 4 Urgent, 5 DO NOW.
- Dates are ISO 8601 datetimes. `start_date`/`end_date` mark the window you plan to work on a task (start work / finish work); `due_date` is the deadline.
- To clear a date, pass an empty string. Vikunja has no null for one: an unset date is the zero time, `0001-01-01T00:00:00Z`, and that is what gets written.
- When a call is rejected, the error carries Vikunja's own explanation, plus its numeric error code on v2, instead of only the HTTP status.
- Kanban buckets live on a view (`view_kind` of `kanban`), not on the project. A view's `bucket_configuration_mode` is `manual` when you arrange tasks yourself, or `filter` when Vikunja builds a bucket per filter, in which case moving a task between buckets does nothing for you.
- A bucket `limit` of `0` means no limit; a move into a full bucket is refused. A repeating task moved into the done bucket is reopened and sent to the default bucket, since done is not a state it stays in. Marking a task done through `update_task` moves it into the done bucket.
- `percent_done` is a fraction despite the name: a quarter done is `0.25`, not `25`. Vikunja does not validate it, so `50` is stored as `50` rather than read as 50 percent.
- `repeat_after` is a number of seconds, and it changes what marking a task done does: the task reopens itself with its due date and reminders moved forward. `repeat_mode` is `0` to advance by `repeat_after`, `1` to repeat monthly and ignore `repeat_after`, or `2` to count from the day it was completed. Vikunja's own API docs say `3` for that last one, but the enum it generates says `2`.
- Setting `repeat_after` on a task with no dates makes a task that cannot be closed. It reopens whether or not there is a due date to advance, verified against 2.5.0, so give a repeating task a `due_date` or leave `repeat_after` alone.
- Relation kinds: `subtask`, `parenttask`, `related`, `duplicateof`, `duplicates`, `blocking`, `blocked`, `precedes`, `follows`, `copiedfrom`, `copiedto`. Direction matters for the asymmetric ones: `add_relation(task_id, other_task_id, "subtask")` makes the other task a child of `task_id`.
- The UI shows tasks by their project-local `identifier` (e.g. `#50`), which is not the global `id` the API uses.
- Verified end to end against Vikunja v2.5.0 on both `/api/v1` and `/api/v2`.
- `list_assignees` needs a server where `GET /tasks/{id}/assignees` works. It answers 500 on v2.3.0, which was a server-side bug, and works on v2.5.0. Every other tool works on both.

## Layout

```
altiplano/
  app.py       the MCP instance, alone, so tool modules can import it
  config.py    where credentials come from; the only module that reads a file
  api.py       version and verb differences, the request layer, response shaping
  tools/       one module per section of this README
  server.py    imports the tool modules to register them, and main()
```

A tool module is the whole of what a section needs: `@mcp.tool()` functions, and any
helper only they use. Adding a section means adding a file and one import in
`server.py`; the test suite fails until the new tools appear in both the routing
table and the smoke test's list, which is deliberate.

## Contributing

Enable the pre-commit hook once per clone:

```bash
git config core.hooksPath hooks
```

> Git hook configuration lives in `.git/config`, which is not version controlled, so I can't do it for you. The hook itself is committed in `hooks/`, and git will not look there until you point it.
>
> The same checks run on every pull request. The suite must pass and coverage must stay above 90 percent.
>
> Coverage is also reported to Codecov on each pull request, as a comment and as inline annotations. That reporting is purely informational and never fails a build.

## Licence

[MIT](./LICENSE).

## Support

RTFM, then RTFC... If you are still stuck or just need an additional feature, file an [issue](https://github.com/aichholzer/altiplano/issues).

<div align="center">
✌🏼
</div>
