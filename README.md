![Altiplano](https://github.com/aichholzer/altiplano/blob/a045975ddd6b59f7c690fa5507a4f55a893c5ab8/banner.png)

# Altiplano

A small, dependable MCP server for [Vikunja](https://vikunja.io). Named after the Andean altiplano, the high plateau that is the Vicuña's native habitat.

Filtering and sorting are passed straight to the Vikunja API (server-side), so there is no client-side filtering engine and no paginate-then-filter pitfall.

## Tools

Projects:
- `list_projects` (includes `parent_project_id`, shows sub-project nesting)
- `create_project` (title, parent_project_id?, description?) — pass `parent_project_id` for a sub-project

Tasks:
- `list_tasks` (project_id, filter, sort_by, page, per_page)
- `get_task` (task_id)
- `create_task` (project_id, title, description?, priority?, due_date?, start_date?, end_date?)
- `update_task` (task_id, title?, description?, done?, priority?, start_date?, end_date?) — on v1, see the warning below about omitted fields
- `set_reminders` (task_id, reminders) — replaces the task's reminders with the given ISO 8601 datetimes; empty list clears
- `delete_task` (task_id) — soft-deletes the task along with its comments, labels and assignees. Vikunja keeps it for 30 days but exposes no way to restore it, so treat this as irreversible

Labels:
- `list_labels`
- `add_label` (task_id, label_id)
- `remove_label` (task_id, label_id)

Comments:
- `list_comments` (task_id)
- `add_comment` (task_id, comment)
- `update_comment` (task_id, comment_id, comment) — replaces the comment text; get `comment_id` from `list_comments`
- `delete_comment` (task_id, comment_id)

Assignees:
- `search_users` (query) — find a `user_id` to assign
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
| `/api/v1`, or anything else | the v1 API |

Prefer `/api/v2` if your server has it. Stay on `/api/v1` only for older servers; every tool works the same either way, with some exceptions.

### On v1, updating a task discards the fields you omit

v1's update endpoint is a replace, not a partial update. Whatever you leave out is reset to its zero value:

```
update_task(task_id=42, priority=4)     # on v1, this also blanks the description
update_task(task_id=42, done=True)      # and this discards description, priority and dates
set_reminders(task_id=42, reminders=[]) # same, via the same endpoint
```

This is Vikunja's behaviour. On v1, you must pass every field you want to keep.

v2 is unaffected. It uses `PATCH` and omitted fields survive.

## Markdown descriptions and comments (v2 only)

Vikunja stores task descriptions and comments as HTML. On v2 this server asks it to convert, so you write and read Markdown:

```
create_task(project_id=12, title="Ship it", description="**bold** and a [link](https://example.com)")
```

Vikunja stores that as `<p><strong>bold</strong> and a <a href="...">link</a></p>`, and `get_task` hands it back as the Markdown you wrote. The same applies to `update_task`, `create_project`, `add_comment` and `update_comment`.

On v1 there is no conversion and the fields are HTML, so Markdown you send is stored verbatim and renders as literal asterisks.

Two things worth mentioning:

- v2 only converts on create and on full replace, never on a partial update. So changing a description reads the task first and writes it back whole, which costs one extra request and could lose a concurrent edit by something else. Updates that do not touch the description stay a single partial update.
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
- The UI shows tasks by their project-local `identifier` (e.g. `#50`), which is not the global `id` the API uses.
- Verified end to end against Vikunja v2.5.0 on both `/api/v1` and `/api/v2`.
- `list_assignees` needs a server where `GET /tasks/{id}/assignees` works. It answers 500 on v2.3.0, which was a server-side bug, and works on v2.5.0. Every other tool works on both.

## Licence

[MIT](./LICENSE).

## Support

RTFM, then RTFC... If you are still stuck or just need an additional feature, file an [issue](https://github.com/aichholzer/altiplano/issues).

<div align="center">
✌🏼
</div>
