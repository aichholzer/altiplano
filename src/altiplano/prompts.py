"""The usage guidance, served as an MCP prompt.

`GUIDE` covers what a single tool description cannot: which tool to reach for, the
order calls happen in, and the calls that do more than they appear to.

Tool references carry parentheses, `list_buckets()`, so `tests/test_guidance.py`
can extract them and check each against the registry. Parameter names never take
parentheses.
"""

from altiplano.app import mcp

GUIDE = """\
# Using Altiplano

Altiplano exposes a Vikunja instance as MCP tools. This covers what the individual
tool descriptions cannot: which tool to reach for, the order calls happen in, and
where a call does more than it appears to.

## Resolve ids before using them

Every id is volatile. Resolve names through the API, and re-resolve anything
remembered from an earlier session:

- Projects: `list_projects()`, matching on title. `parent_project_id` shows
  sub-project nesting.
- Labels: `list_labels()`, matching on title, case-insensitively.
- Kanban views and their columns: `list_kanban_views()`, then `list_buckets()`.
- Users, before assigning one: `search_users()`.
- A task whose project is unknown: `search_tasks()` spans every project you can
  see and returns `project_id` on each result.

When a name matches nothing, say so. When several match, confirm which was meant
before writing anything.

## Finding tasks

`list_tasks()` needs a project. `search_tasks()` does not, so reach for it when
the location is unknown.

Both take `filter` and `sort_by`, which are Vikunja's own server-side syntax, for
example `filter="done = false && priority >= 4"`. Vikunja filters first and
paginates second, so a filtered result is complete at any page size.

`search_tasks()` also takes `query`, a text search over titles and descriptions.
Vikunja documents `query` as incompatible with `filter`, so use one of the two.

## Creating and closing tasks

`create_task()` takes no `done` parameter, so a task cannot arrive completed.
Recording finished work takes two calls: `create_task()`, then `update_task()`
with `done: true` and the dates the work actually happened.

`update_task()` changes only the fields passed to it. Omit the rest. Re-sending a
field you are not changing costs an extra request on v2, where a description
forces a read and a replace.

Dates: `due_date` is the deadline, while `start_date` and `end_date` are the
window you plan to work in. All three take ISO 8601 datetimes, and an empty
string clears one.

`percent_done` is a fraction despite the name, so a quarter done is `0.25`.
Vikunja stores `50` as `50` and never clamps it.

A repeating task, meaning one with `repeat_after` set in seconds, reopens itself
when marked done and moves its dates forward. Give one a `due_date`: with no
dates it still reopens, so it can never be closed.

## Batching

`bulk_create_tasks()` creates many tasks in one project in a single atomic
request, and the tasks keep the order they were given, so a numbered plan comes
back numbered. A loop of `create_task()` calls races and can come back shuffled,
and a failure halfway leaves the rest uncreated. This tool needs the v2 API and
refuses on v1. Vikunja caps a batch at 100.

`bulk_update_tasks()` sets `done` or `priority` across many tasks in one request,
writing only the fields passed. Missing write access on any one project involved
refuses the entire request, and nothing changes.

## Kanban

A task holds a position in every kanban view of its project, so a project with
two boards puts that task in two columns. `list_task_buckets()` reports them all.
Read any other way, a task's `bucket_id` is `0`, because the field only means
something inside a view.

`move_task_to_bucket()` does more than move a card:

- Moving into the done column marks the task done. Moving it out reopens it.
- A repeating task moved into the done column reopens and lands in the default
  column.
- A column at its task limit refuses the move.

Closing a task by moving it therefore skips everything else closing involves.
Call `update_task()` with `done: true` when the intent is to close.

Read `bucket_configuration_mode` from `list_kanban_views()` first. In `filter`
mode Vikunja derives each column from its filters, and moving a task between
columns is unavailable.

`list_bucket_tasks()` reports `task_count` as the column's true size, which can
exceed the tasks returned, because Vikunja caps how many it sends per column.
Pass `filter` to narrow the result.

`delete_bucket()` keeps the tasks it held: Vikunja moves them to the default
column. A view always keeps one column, so the last one cannot be deleted.

## Labels

`remove_label()` takes a label off one task. `delete_label()` destroys the label
everywhere, stripping it from every task that has it. Confirm which of the two is
wanted before calling the second.

## Moving and copying

`move_task()` writes the task's `project_id`. Labels, assignees, comments,
relations and dates travel with it. The project-local `identifier` is reassigned
on arrival, because it derives from the project the task sits in.

`duplicate_task()` copies into the same project and links the copy back with a
`copiedfrom` relation. Vikunja has no cross-project duplicate, so follow it with
`move_task()` to place the copy elsewhere.

## Relations

`add_relation()` reads `task_id` as the base task and `other_task_id` as the one
being related to it. That direction decides the asymmetric kinds: `subtask` makes
the other task a child of the base. `remove_relation()` needs the same
`relation_kind` the relation was created with, and `get_task()` reports the kinds
a task currently has.

## Calls that cannot be undone

`delete_task()` takes the task's comments, labels and assignees with it. Vikunja
soft-deletes and documents a 30 day retention window, while exposing no endpoint
to list or restore anything deleted, so through this API the call is permanent.
Confirm the id with `get_task()` first. `delete_label()` and `delete_comment()`
are equally final.

`set_reminders()` replaces the task's reminders with the list given, so an
existing reminder survives only by being passed again. An empty list clears them.

`update_comment()` replaces the comment text outright.

## Two API versions

`VIKUNJA_URL` decides the version: a URL ending in `/api/v2` selects v2, and
anything else uses v1. Descriptions are written as Markdown on both. What differs:

- `bulk_create_tasks()` needs v2 and refuses on v1.
- `get_task()` returns the description as Markdown on v2.
- A partial `update_task()` on v2 returns the description as stored HTML, because
  v2 does not convert on a PATCH. Call `get_task()` when Markdown is wanted.
"""


@mcp.prompt(title="Using Altiplano")
def altiplano_guide() -> str:
    """How to drive Altiplano's tools: resolving ids, sequencing calls across
    tools, the calls that cannot be undone, and the v1 and v2 differences. Load
    this before making changes through the tools.
    """
    return GUIDE
