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
- `create_task` (project_id, title, description?, priority?, due_date?, start_date?, end_date?, percent_done?, is_favorite?, repeat_after?, repeat_mode?)
- `update_task` (task_id, title?, description?, done?, priority?, start_date?, end_date?, percent_done?, is_favorite?, repeat_after?, repeat_mode?)
- `set_reminders` (task_id, reminders) — replaces the task's reminders with the given ISO 8601 datetimes; empty list clears

Kanban:
- `list_buckets` (project_id) — lists the columns of a project's kanban view, flagging the default and done buckets
- `list_bucket_tasks` (project_id) — tasks grouped by kanban bucket
- `get_task_bucket` (task_id) — the bucket a task currently sits in
- `move_task_to_bucket` (project_id, task_id, bucket_id) — moves a task into a bucket; moving into the done bucket sets `done=true`, moving out clears it

Labels:
- `list_labels`
- `add_label` (task_id, label_id)
- `remove_label` (task_id, label_id)

Comments:
- `list_comments` (task_id)
- `add_comment` (task_id, comment)

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

`VIKUNJA_URL` is the base API URL including `/api/v1` (e.g. `https://todo.example.com/api/v1`).

Recommended so the your `mcp.json` carries no secrets:

- Drop a per-device file and lock it down:
  ```bash
  mkdir -p ~/.config/altiplano
  printf 'VIKUNJA_URL=https://todo.example.com/api/v1\nVIKUNJA_API_TOKEN=tk_xxx\n' > ~/.config/altiplano/env
  chmod 600 ~/.config/altiplano/env
  ```
- Or inject via the launcher's environment (e.g. a systemd unit `EnvironmentFile=` pointing at a `chmod 600` file), which the server inherits.
- For stronger setups, source the token from a secret manager/keychain at launch and export it into the environment.

Then `mcp.json` only needs the command, no `env` block, no plain-text secrets:

```json
{
  "altiplano": {
    "command": "uvx",
    "args": ["altiplano"]
  }
}
```

## Run

```bash
uv run altiplano                        # dev, from this directory
uvx --from /your/local/path altiplano   # local path
uvx altiplano                           # from PyPI
```

## Notes

- Vikunja priority scale: 0 Unset, 1 Low, 2 Medium, 3 High, 4 Urgent, 5 DO NOW.
- Dates are ISO 8601 datetimes. `start_date`/`end_date` mark the window you plan to work on a task (start work / finish work); `due_date` is the deadline.
- The UI shows tasks by their project-local `identifier` (e.g. `#50`), which is not the global `id` the API uses.
- Endpoint shapes (create via `PUT /projects/{id}/tasks`, update via `POST /tasks/{id}`) follow current Vikunja; adjust if your instance differs.
- `percent_done` is 0.0–1.0. `repeat_mode` is 0 (repeat `repeat_after` seconds from the due date), 1 (monthly), or 2 (repeat `repeat_after` seconds from the current date).
- Kanban buckets belong to a project's *view* (`view_kind == "kanban"`), not the project directly; kanban tools resolve that view automatically from `project_id`. A task's bucket is not present on `get_task` — use `get_task_bucket`. Moving a task into the view's done bucket sets `done=true`; moving it out clears `done`.

## Licence

[MIT](./LICENSE).

## Support

RTFM, then RTFC... If you are still stuck or just need an additional feature, file an [issue](https://github.com/aichholzer/altiplano/issues).

<div align="center">
✌🏼
</div>
