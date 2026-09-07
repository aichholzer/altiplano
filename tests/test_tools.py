"""Every tool's wire contract: verb, path, query, and body.

Vikunja inverts the usual REST convention, using PUT to create and POST to update.
The verb assertions here are load bearing.
"""

import json

import httpx
import pytest

from altiplano.api import _version
from altiplano.server import mcp
from altiplano.tools import (
    assignees,
    comments,
    kanban,
    labels,
    projects,
    relations,
    tasks,
)


def body(request) -> dict:
    return json.loads(request.content) if request.content else {}


# --- routing ----------------------------------------------------------------
def route(name, call, verbs, path, body, response=None):
    """One routing case, named so failures point at the tool.

    `verbs` is {1: verb, 2: verb}. Both are written out as literals, never derived
    from the server's own mapping. The test cannot agree with a wrong one.

    `body` is the expected request body, or a {1: body, 2: body} mapping where the
    two versions differ. `response` overrides what the fake returns. The two tools
    that read the task before writing it need that.
    """
    return pytest.param(call, verbs, path, body, response, id=name)


READ = {1: "GET", 2: "GET"}
CREATE = {1: "PUT", 2: "POST"}
UPDATE = {1: "POST", 2: "PATCH"}
REMOVE = {1: "DELETE", 2: "DELETE"}
# No verb on v1 means the tool refuses that version before sending anything. That is
# a routing fact worth stating in the same table as the verbs.
CREATE_V2_ONLY = {1: None, 2: "POST"}
# v2 honours ?format=markdown on a replace but not on a partial update. Writes
# holding rich text go through PUT there.
REPLACE = {1: "POST", 2: "PUT"}

# What the fake hands back to the two tools that read the task before writing it.
# On v1 the write is that task with the change merged in: v1's update endpoint is
# a replace. On v2 it is the change alone.
READ_BACK = {"id": 7, "title": "Existing"}

# Buckets belong to a view. Every bucket tool resolves one first.
KANBAN_VIEW = [
    {
        "id": 48,
        "title": "Kanban",
        "view_kind": "kanban",
        "default_bucket_id": 41,
        "done_bucket_id": 43,
        "bucket_configuration_mode": "manual",
    }
]


# What the fake hands back to the project and label tools that read before writing.
# Same shape as READ_BACK above, for the resources with their own replace path.
PROJECT_READ_BACK = {"id": 3, "title": "Existing"}
LABEL_READ_BACK = {"id": 1, "title": "Existing"}

# A bucket write makes two reads: the view, then that view's bucket list. One
# response answers both, the way the move_task_to_bucket case below does. `_items`
# unwraps the envelope, `_kanban_view` picks the entry with a kanban `view_kind`, and
# the bucket lookup picks the one whose id it was given.
BUCKET_IN_VIEW = {"id": 41, "title": "Existing", "limit": 0, "position": 100, "count": 7}
VIEW_AND_ITS_BUCKETS = {"items": [*KANBAN_VIEW, BUCKET_IN_VIEW]}


ROUTES = [
    route("list_projects", lambda: projects.list_projects(), READ, "/projects", {}),
    route(
        "update_project",
        lambda: projects.update_project(3, title="Renamed"),
        UPDATE,
        "/projects/3",
        # v1 writes the project back whole. v2 sends the change alone.
        {1: {**PROJECT_READ_BACK, "title": "Renamed"}, 2: {"title": "Renamed"}},
        response=PROJECT_READ_BACK,
    ),
    route("delete_project", lambda: projects.delete_project(3), REMOVE, "/projects/3", {}),
    route("get_task", lambda: tasks.get_task(7), READ, "/tasks/7", {}),
    route("list_tasks", lambda: tasks.list_tasks(3), READ, "/projects/3/tasks", {}),
    route("search_tasks", lambda: tasks.search_tasks(), READ, "/tasks", {}),
    route(
        "move_task",
        lambda: tasks.move_task(7, 5),
        UPDATE,
        "/tasks/7",
        {1: {**READ_BACK, "project_id": 5}, 2: {"project_id": 5}},
        response=READ_BACK,
    ),
    route("duplicate_task", lambda: tasks.duplicate_task(7), CREATE, "/tasks/7/duplicate", {}),
    route(
        "bulk_create_tasks",
        lambda: tasks.bulk_create_tasks(3, [{"title": "First"}, {"title": "Second"}]),
        CREATE_V2_ONLY,
        "/projects/3/tasks/bulk",
        {"tasks": [{"title": "First"}, {"title": "Second"}]},
        response={"tasks": []},
    ),
    route(
        "bulk_update_tasks",
        lambda: tasks.bulk_update_tasks([7, 9], done=True),
        REPLACE,
        "/tasks/bulk",
        {"task_ids": [7, 9], "fields": ["done"], "values": {"done": True}},
    ),
    route("list_labels", lambda: labels.list_labels(), READ, "/labels", {}),
    route(
        "create_label",
        lambda: labels.create_label("Doing"),
        CREATE,
        "/labels",
        {"title": "Doing"},
    ),
    route(
        "update_label",
        lambda: labels.update_label(1, title="Renamed"),
        UPDATE,
        "/labels/1",
        {1: {**LABEL_READ_BACK, "title": "Renamed"}, 2: {"title": "Renamed"}},
        response=LABEL_READ_BACK,
    ),
    route("delete_label", lambda: labels.delete_label(1), REMOVE, "/labels/1", {}),
    route("add_label", lambda: labels.add_label(7, 1), CREATE, "/tasks/7/labels", {"label_id": 1}),
    route("remove_label", lambda: labels.remove_label(7, 1), REMOVE, "/tasks/7/labels/1", {}),
    route("list_comments", lambda: comments.list_comments(7), READ, "/tasks/7/comments", {}),
    route(
        "add_comment",
        lambda: comments.add_comment(7, "hello"),
        CREATE,
        "/tasks/7/comments",
        {"comment": "hello"},
    ),
    route(
        "update_comment",
        lambda: comments.update_comment(7, 21, "edited"),
        REPLACE,
        "/tasks/7/comments/21",
        {"comment": "edited"},
    ),
    route("delete_comment", lambda: comments.delete_comment(7, 21), REMOVE, "/tasks/7/comments/21", {}),
    route(
        "add_relation",
        lambda: relations.add_relation(7, 9),
        CREATE,
        "/tasks/7/relations",
        {"other_task_id": 9, "relation_kind": "related"},
    ),
    route(
        "remove_relation",
        lambda: relations.remove_relation(7, 9),
        REMOVE,
        "/tasks/7/relations/related/9",
        # The path holds all three values and the API documents the body as
        # required anyway. Both go out.
        {"other_task_id": 9, "relation_kind": "related"},
    ),
    # The bucket tools resolve a view first. The fake has to answer that with
    # something kanban-shaped. The same canned response serves the second request,
    # and these cases assert nothing there beyond verb, path, and body.
    route(
        "list_kanban_views",
        lambda: kanban.list_kanban_views(3),
        READ,
        "/projects/3/views",
        {},
    ),
    route(
        "list_buckets",
        lambda: kanban.list_buckets(3),
        READ,
        "/projects/3/views/48/buckets",
        {},
        response=KANBAN_VIEW,
    ),
    route(
        "create_bucket",
        lambda: kanban.create_bucket(3, "Doing"),
        CREATE,
        "/projects/3/views/48/buckets",
        {"title": "Doing"},
        response=KANBAN_VIEW,
    ),
    route(
        "update_bucket",
        lambda: kanban.update_bucket(3, 41, title="Renamed"),
        # No partial update exists for a bucket on either version. Both write the
        # column back whole, and `count` is derived and stays out of the body.
        REPLACE,
        "/projects/3/views/48/buckets/41",
        {"id": 41, "title": "Renamed", "limit": 0, "position": 100},
        response=VIEW_AND_ITS_BUCKETS,
    ),
    route(
        "delete_bucket",
        lambda: kanban.delete_bucket(3, 42),
        REMOVE,
        "/projects/3/views/48/buckets/42",
        {},
        response=KANBAN_VIEW,
    ),
    route(
        "list_board",
        lambda: kanban.list_board(3),
        READ,
        # v1 groups on the view's task endpoint; v2 answers that one flat and has a
        # separate route for the grouped form.
        {1: "/projects/3/views/48/tasks", 2: "/projects/3/views/48/buckets/tasks"},
        {},
        response=KANBAN_VIEW,
    ),
    route(
        "list_task_placements",
        lambda: kanban.list_task_placements(7),
        READ,
        "/tasks/7",
        {},
        response={"id": 7, "buckets": []},
    ),
    route(
        "move_task_to_bucket",
        lambda: kanban.move_task_to_bucket(7, 41),
        REPLACE,
        "/projects/3/views/48/buckets/41/tasks",
        {"task_id": 7},
        # Read first for the task's project, then for the view. One canned response
        # serves both: a dict holding project_id for the task read, wrapped in the
        # pagination envelope so `_items` finds the view in it.
        response={"project_id": 3, "items": KANBAN_VIEW},
    ),
    route("list_assignees", lambda: assignees.list_assignees(7), READ, "/tasks/7/assignees", {}),
    route("add_assignee", lambda: assignees.add_assignee(7, 2), CREATE, "/tasks/7/assignees", {"user_id": 2}),
    route("remove_assignee", lambda: assignees.remove_assignee(7, 2), REMOVE, "/tasks/7/assignees/2", {}),
    route("create_project", lambda: projects.create_project("Board"), CREATE, "/projects", {"title": "Board"}),
    route("create_task", lambda: tasks.create_task(3, "Task"), CREATE, "/projects/3/tasks", {"title": "Task"}),
    route(
        "update_task",
        lambda: tasks.update_task(7, done=True),
        UPDATE,
        "/tasks/7",
        {1: {**READ_BACK, "done": True}, 2: {"done": True}},
        response=READ_BACK,
    ),
    route("delete_task", lambda: tasks.delete_task(7), REMOVE, "/tasks/7", {}),
    route(
        "set_reminders",
        lambda: tasks.set_reminders(7, ["2026-08-20T09:00:00+10:00"]),
        UPDATE,
        "/tasks/7",
        {
            1: {**READ_BACK, "reminders": [{"reminder": "2026-08-20T09:00:00+10:00"}]},
            2: {"reminders": [{"reminder": "2026-08-20T09:00:00+10:00"}]},
        },
        response=READ_BACK,
    ),
    route("search_users", lambda: assignees.search_users("stefan"), READ, "/users", {}),
]


@pytest.mark.parametrize("api_version", [1, 2])
@pytest.mark.parametrize(("call", "verbs", "path", "expected_body", "response"), ROUTES)
def test_tool_uses_the_expected_verb_path_and_body(
    api, run, api_version, call, verbs, path, expected_body, response
):
    if response is not None:
        api.returns(response)
    if verbs[api_version] is None:
        # A tool with no verb for this version must say so before reaching the
        # network. The server has no route for it, and nothing should be sent.
        with pytest.raises(RuntimeError, match="needs the v2 API"):
            run(call())
        assert api.requests == []
        return
    run(call())
    assert api.last.method == verbs[api_version]
    # A path or a body may be given per version, for the handful of tools where the
    # two versions differ. A real path is a string and a real JSON body never has
    # integer keys. Neither check can collide with a genuine value.
    assert api.last.url.path.endswith(path[api_version] if isinstance(path, dict) else path)
    expected = expected_body[api_version] if set(expected_body) == {1, 2} else expected_body
    assert body(api.last) == expected


def test_every_tool_is_covered_by_a_routing_case():
    """Guards against a new tool being added without a wire-contract test."""
    import asyncio

    registered = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert registered == {case.id for case in ROUTES}


# --- api version selection --------------------------------------------------
@pytest.mark.parametrize("api_version", [1, 2])
def test_version_comes_from_the_configured_url(api, api_version):
    assert _version() == api_version


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://vikunja.test/api/v1", 1),
        ("https://vikunja.test/api/v2", 2),
        # A trailing slash is stripped by _base before the check.
        ("https://vikunja.test/api/v2/", 2),
        # Anything unrecognised falls back to v1. There is no guessing.
        ("https://vikunja.test/api", 1),
        ("https://vikunja.test/api/v3", 1),
    ],
)
def test_version_falls_back_to_v1_without_an_explicit_v2_url(monkeypatch, url, expected):
    monkeypatch.setenv("VIKUNJA_URL", url)
    assert _version() == expected


@pytest.mark.parametrize("api_version", [1, 2])
def test_listings_unwrap_whichever_collection_shape_arrives(api, run, api_version):
    """v1 sends a bare array, v2 a pagination envelope. Both must work. The check is
    on shape, and a version mismatch degrades gracefully."""
    api.returns([{"id": 1, "title": "Home"}])
    assert run(projects.list_projects())[0]["title"] == "Home"

    api.returns({"items": [{"id": 1, "title": "Home"}], "total": 1, "page": 1})
    assert run(projects.list_projects())[0]["title"] == "Home"


def test_envelope_with_no_items_returns_empty(api, run):
    api.returns({"items": [], "total": 0, "page": 1, "per_page": 50})
    assert run(projects.list_projects()) == []


def test_a_dict_without_items_is_still_rejected(api, run):
    """The 0.5.4 protection must survive envelope support: a bodyless response
    reported as a status dict has no `items` and is not an empty collection."""
    api.returns_raw(204)
    with pytest.raises(RuntimeError, match="expected a list from the API, got dict"):
        run(projects.list_projects())


# --- optional payload fields ------------------------------------------------
def test_create_project_omits_unset_fields(api, run):
    run(projects.create_project("Board"))
    assert body(api.last) == {"title": "Board"}


def test_create_project_includes_supplied_fields(api, run):
    run(projects.create_project("Sub", parent_project_id=4, description="notes"))
    assert body(api.last) == {"title": "Sub", "parent_project_id": 4, "description": "notes"}


def test_create_task_includes_every_supplied_field(api, run):
    run(
        tasks.create_task(
            3,
            "Task",
            description="why",
            priority=4,
            due_date="2026-08-21T09:00:00+10:00",
            start_date="2026-08-20T09:00:00+10:00",
            end_date="2026-08-20T17:00:00+10:00",
        )
    )
    assert body(api.last) == {
        "title": "Task",
        "description": "why",
        "priority": 4,
        "due_date": "2026-08-21T09:00:00+10:00",
        "start_date": "2026-08-20T09:00:00+10:00",
        "end_date": "2026-08-20T17:00:00+10:00",
    }


def test_update_task_includes_every_supplied_field(api, run):
    # A description routes through the read-then-replace path on both versions.
    # These changes get merged into the canned task.
    api.returns({"id": 7})
    run(
        tasks.update_task(
            7,
            title="New",
            description="why",
            done=False,
            priority=2,
            due_date="2026-08-21T09:00:00+10:00",
            start_date="2026-08-20T09:00:00+10:00",
            end_date="2026-08-20T17:00:00+10:00",
        )
    )
    assert body(api.last) == {
        "id": 7,
        "title": "New",
        "description": "why",
        "done": False,
        "priority": 2,
        "due_date": "2026-08-21T09:00:00+10:00",
        "start_date": "2026-08-20T09:00:00+10:00",
        "end_date": "2026-08-20T17:00:00+10:00",
    }


# The remaining payload tests run on v2, where an update is a genuine partial
# update and the body on the wire is exactly the fields that were passed. v1
# merges into the task it read first. The routing table and the replace tests
# below cover that.
@pytest.mark.parametrize("api_version", [2])
def test_update_task_can_set_a_due_date(api, run, api_version):
    """`create_task` always took a deadline; `update_task` did not. A deadline could
    be given at creation and never changed afterwards."""
    run(tasks.update_task(7, due_date="2026-08-21T09:00:00+10:00"))
    assert body(api.last) == {"due_date": "2026-08-21T09:00:00+10:00"}


# --- clearing dates ---------------------------------------------------------
# Vikunja has no null for a date: an unset one is Go's zero time, on the wire and
# in the database. Clearing means writing that value. An empty string is the
# caller's way of asking: None already means "leave it out of the payload", and ""
# is not a datetime Vikunja parses.
@pytest.mark.parametrize("api_version", [2])
@pytest.mark.parametrize("field", ["due_date", "start_date", "end_date"])
def test_update_task_clears_a_date_given_an_empty_string(api, run, field, api_version):
    run(tasks.update_task(7, **{field: ""}))
    assert body(api.last) == {field: "0001-01-01T00:00:00Z"}


@pytest.mark.parametrize("field", ["due_date", "start_date", "end_date"])
def test_create_task_clears_a_date_given_an_empty_string(api, run, field):
    run(tasks.create_task(3, "Task", **{field: ""}))
    assert body(api.last) == {"title": "Task", field: "0001-01-01T00:00:00Z"}


# --- progress, favourite, and repeating -------------------------------------
PROGRESS_FIELDS = {
    "percent_done": 0.25,
    "is_favorite": True,
    "repeat_after": 86400,
    "repeat_mode": 2,
}


def test_create_task_sends_the_progress_and_repeat_fields(api, run):
    run(tasks.create_task(3, "Task", **PROGRESS_FIELDS))
    assert body(api.last) == {"title": "Task", **PROGRESS_FIELDS}


@pytest.mark.parametrize("api_version", [2])
def test_update_task_sends_the_progress_and_repeat_fields(api, run, api_version):
    run(tasks.update_task(7, **PROGRESS_FIELDS))
    assert body(api.last) == PROGRESS_FIELDS


# Every one of these means something and every one is falsy. A truthiness check in
# the payload builder would drop exactly the values that turn a feature off.
OFF_VALUES = {
    "percent_done": 0.0,
    "is_favorite": False,
    "repeat_after": 0,
    "repeat_mode": 0,
}


@pytest.mark.parametrize(("field", "value"), OFF_VALUES.items())
def test_create_task_keeps_a_falsy_value(api, run, field, value):
    run(tasks.create_task(3, "Task", **{field: value}))
    assert body(api.last) == {"title": "Task", field: value}


@pytest.mark.parametrize("api_version", [2])
@pytest.mark.parametrize(("field", "value"), OFF_VALUES.items())
def test_update_task_keeps_a_falsy_value(api, run, field, value, api_version):
    run(tasks.update_task(7, **{field: value}))
    assert body(api.last) == {field: value}


# --- relations --------------------------------------------------------------
# The routing cases above cover the default `related` kind. What matters here is
# that a kind reaches a different place in each tool: the body on create, and the
# path on remove, where getting it wrong would silently address another relation.
# --- kanban -----------------------------------------------------------------
TWO_KANBAN_VIEWS = [
    {"id": 45, "title": "List", "view_kind": "list"},
    {"id": 48, "title": "Kanban", "view_kind": "kanban", "done_bucket_id": 43},
    {"id": 49, "title": "Triage", "view_kind": "kanban", "done_bucket_id": 0},
]


def test_the_first_kanban_view_is_used_when_none_is_named(api, run):
    """Views arrive ordered by position. The first kanban one is the leftmost tab,
    whatever order the server happened to send them in."""
    api.returns(TWO_KANBAN_VIEWS)
    run(kanban.list_buckets(3))
    assert api.last.url.path.endswith("/projects/3/views/48/buckets")


def test_a_named_view_is_used_instead(api, run):
    api.returns(TWO_KANBAN_VIEWS)
    run(kanban.list_buckets(3, view_id=49))
    assert api.last.url.path.endswith("/projects/3/views/49/buckets")


def test_a_view_that_is_not_kanban_is_refused(api, run):
    """Only kanban views have buckets. Pointing at a list view is a mistake worth
    naming here, before the buckets endpoint answers with something confusing."""
    api.returns(TWO_KANBAN_VIEWS)
    with pytest.raises(ValueError, match="is a list view"):
        run(kanban.list_buckets(3, view_id=45))


def test_a_view_that_does_not_exist_is_refused(api, run):
    api.returns(TWO_KANBAN_VIEWS)
    with pytest.raises(ValueError, match="has no view 99"):
        run(kanban.list_buckets(3, view_id=99))


def test_a_project_with_no_kanban_view_is_refused(api, run):
    api.returns([{"id": 45, "title": "List", "view_kind": "list"}])
    with pytest.raises(ValueError, match="has no kanban view"):
        run(kanban.list_buckets(3))


def test_list_kanban_views_drops_the_other_kinds(api, run):
    api.returns(TWO_KANBAN_VIEWS)
    assert run(kanban.list_kanban_views(3)) == [
        {
            "id": 48,
            "title": "Kanban",
            "default_bucket_id": None,
            "done_bucket_id": 43,
            "bucket_configuration_mode": None,
        },
        {
            "id": 49,
            "title": "Triage",
            "default_bucket_id": None,
            "done_bucket_id": 0,
            "bucket_configuration_mode": None,
        },
    ]


BUCKETS = [
    {"id": 41, "title": "To-Do", "position": 100, "limit": 0, "count": 0},
    {"id": 42, "title": "Doing", "position": 200, "limit": 3, "count": 0},
    {"id": 43, "title": "Done", "position": 300, "limit": 0, "count": 0},
]


def test_list_buckets_flags_the_default_and_done_columns(api, run):
    api.returns_in_order(
        httpx.Response(200, json=[dict(KANBAN_VIEW[0], default_bucket_id=42)]),
        httpx.Response(200, json=BUCKETS),
    )
    assert run(kanban.list_buckets(3)) == [
        {
            "id": 41,
            "title": "To-Do",
            "position": 100,
            "limit": 0,
            "is_default_bucket": False,
            "is_done_bucket": False,
        },
        {
            "id": 42,
            "title": "Doing",
            "position": 200,
            "limit": 3,
            "is_default_bucket": True,
            "is_done_bucket": False,
        },
        {
            "id": 43,
            "title": "Done",
            "position": 300,
            "limit": 0,
            "is_default_bucket": False,
            "is_done_bucket": True,
        },
    ]


def test_an_unset_default_bucket_means_the_leftmost_one(api, run):
    """Vikunja leaves `default_bucket_id` at 0 to mean "the leftmost bucket", and
    buckets arrive in board order. That is the first."""
    api.returns_in_order(
        httpx.Response(200, json=[dict(KANBAN_VIEW[0], default_bucket_id=0)]),
        httpx.Response(200, json=BUCKETS),
    )
    flags = [(b["id"], b["is_default_bucket"]) for b in run(kanban.list_buckets(3))]
    assert flags == [(41, True), (42, False), (43, False)]


def test_list_buckets_survives_a_view_with_no_buckets(api, run):
    api.returns_in_order(
        httpx.Response(200, json=[dict(KANBAN_VIEW[0], default_bucket_id=0)]),
        httpx.Response(200, json=[]),
    )
    assert run(kanban.list_buckets(3)) == []


def test_list_board_reports_the_true_size_beside_the_tasks(api, run):
    """Vikunja caps the tasks it sends per bucket. `task_count` and the length of
    `tasks` are different questions."""
    api.returns_in_order(
        httpx.Response(200, json=KANBAN_VIEW),
        httpx.Response(
            200,
            json=[
                {
                    "id": 43,
                    "title": "Done",
                    "count": 23,
                    "tasks": [{"id": 374, "identifier": "#1", "title": "T", "done": True}],
                }
            ],
        ),
    )
    assert run(kanban.list_board(3)) == [
        {
            "id": 43,
            "title": "Done",
            "task_count": 23,
            "tasks": [
                {"id": 374, "identifier": "#1", "title": "T", "done": True, "priority": None}
            ],
        }
    ]


def test_list_board_passes_a_filter_to_the_server(api, run):
    api.returns(KANBAN_VIEW)
    run(kanban.list_board(3, filter="done = false"))
    assert dict(api.last.url.params) == {"filter": "done = false"}


@pytest.mark.parametrize("api_version", [2])
def test_a_401_on_the_v2_board_explains_itself(api, run, api_version):
    """Observed on 2.5.0: this one route rejects an API token that works everywhere
    else. The stock "invalid token" would send someone hunting the wrong problem.
    """
    api.returns_in_order(
        httpx.Response(200, json=KANBAN_VIEW),
        httpx.Response(401, json={"detail": "invalid token provided", "code": 11}),
    )
    with pytest.raises(RuntimeError, match="created with full permissions"):
        run(kanban.list_board(3))


@pytest.mark.parametrize("api_version", [1])
def test_a_401_elsewhere_is_left_alone(api, run, api_version):
    """The explanation is specific to the v2 route. v1 must keep the real error."""
    api.returns_in_order(
        httpx.Response(200, json=KANBAN_VIEW),
        httpx.Response(401, json={"message": "invalid token provided"}),
    )
    with pytest.raises(httpx.HTTPStatusError, match="401"):
        run(kanban.list_board(3))


def test_moving_to_a_bucket_takes_the_project_from_the_task(api, run):
    """The project is derived, and it cannot contradict the task. A caller-supplied
    one that disagreed would build a path that looks valid and 404s."""
    api.returns_in_order(
        httpx.Response(200, json={"id": 7, "project_id": 3}),
        httpx.Response(200, json=KANBAN_VIEW),
        httpx.Response(200, json={"task_id": 7}),
    )
    run(kanban.move_task_to_bucket(7, 41))

    task_read, views, move = api.requests
    assert task_read.url.path.endswith("/tasks/7")
    assert views.url.path.endswith("/projects/3/views")
    assert move.url.path.endswith("/projects/3/views/48/buckets/41/tasks")


def test_moving_to_a_bucket_refuses_when_the_project_cannot_be_read(api, run):
    api.returns_raw(204)
    with pytest.raises(RuntimeError, match="could not read which project task 7"):
        run(kanban.move_task_to_bucket(7, 41))
    assert [r.url.path.split("/")[-1] for r in api.requests] == ["7"]


# --- searching, bulk, and labels ---------------------------------------------
@pytest.mark.parametrize(("api_version", "param"), [(1, "s"), (2, "q")])
def test_search_tasks_uses_the_search_param_the_version_expects(api, run, param):
    """Same rename as search_users: sending the wrong one is not an error, it just
    silently ignores the search."""
    run(tasks.search_tasks(query="kanban"))
    assert dict(api.last.url.params) == {"page": "1", "per_page": "50", param: "kanban"}


def test_search_tasks_reports_which_project_each_task_is_in(api, run):
    """The reason to search across projects is not knowing which one it is in."""
    api.returns([{"id": 374, "identifier": "#1", "title": "T", "done": False, "project_id": 12}])
    assert run(tasks.search_tasks(query="T")) == [
        {
            "id": 374,
            "identifier": "#1",
            "title": "T",
            "done": False,
            "priority": None,
            "project_id": 12,
        }
    ]


def test_search_tasks_passes_a_filter_and_sort_through(api, run):
    run(tasks.search_tasks(filter="done = false", sort_by="priority", page=2, per_page=10))
    assert dict(api.last.url.params) == {
        "page": "2",
        "per_page": "10",
        "filter": "done = false",
        "sort_by": "priority",
    }


def test_bulk_update_names_the_fields_separately_from_the_values(api, run):
    """This endpoint writes only the fields it is told to. That makes it a real
    partial update on v1 too."""
    run(tasks.bulk_update_tasks([7, 9], done=True, priority=4))
    assert body(api.last) == {
        "task_ids": [7, 9],
        "fields": ["done", "priority"],
        "values": {"done": True, "priority": 4},
    }


def test_bulk_update_keeps_done_false(api, run):
    run(tasks.bulk_update_tasks([7], done=False))
    assert body(api.last) == {
        "task_ids": [7],
        "fields": ["done"],
        "values": {"done": False},
    }


def test_bulk_update_rejects_an_empty_payload(api, run):
    with pytest.raises(ValueError, match="No fields to update"):
        run(tasks.bulk_update_tasks([7]))
    assert api.requests == []


# --- bulk create ------------------------------------------------------------
# v2 only. Every case here runs there, and the routing table covers the v1 refusal.
@pytest.mark.parametrize("api_version", [2])
def test_bulk_create_gives_each_entry_the_same_fields_as_a_single_create(api, run, api_version):
    api.returns({"tasks": []})
    run(
        tasks.bulk_create_tasks(
            3,
            [
                {"title": "First", "description": "why", "priority": 4, "due_date": ""},
                {"title": "Second", **OFF_VALUES},
            ],
        )
    )
    assert body(api.last) == {
        "tasks": [
            {
                "title": "First",
                "description": "why",
                "priority": 4,
                "due_date": "0001-01-01T00:00:00Z",
            },
            {"title": "Second", **OFF_VALUES},
        ]
    }


@pytest.mark.parametrize("api_version", [2])
def test_bulk_create_asks_for_markdown_like_a_single_create_does(api, run, api_version):
    api.returns({"tasks": []})
    run(tasks.bulk_create_tasks(3, [{"title": "First"}]))
    assert dict(api.last.url.params) == {"format": "markdown"}


@pytest.mark.parametrize("api_version", [2])
def test_bulk_create_returns_a_summary_per_task_in_creation_order(api, run, api_version):
    api.returns(
        {
            "tasks": [
                {"id": 8, "identifier": "#1", "title": "First", "done": False, "priority": 0},
                {"id": 9, "identifier": "#2", "title": "Second", "description": "dropped"},
            ]
        }
    )
    assert run(tasks.bulk_create_tasks(3, [{"title": "First"}, {"title": "Second"}])) == [
        {"id": 8, "identifier": "#1", "title": "First", "done": False, "priority": 0},
        {"id": 9, "identifier": "#2", "title": "Second", "done": None, "priority": None},
    ]


@pytest.mark.parametrize("api_version", [2])
@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ([], "No tasks to create"),
        (["First"], r"tasks\[0\] is not an object"),
        ([{"title": "First"}, {}], r"tasks\[1\] has no title"),
        ([{"title": "First", "name": "x", "done": True}], r"tasks\[0\].*done, name"),
    ],
    ids=["empty", "not an object", "no title", "unsupported fields"],
)
def test_bulk_create_refuses_a_batch_it_cannot_send_faithfully(api, run, entries, message, api_version):
    """An unsupported key is refused: a caller told the task was created would
    otherwise never learn the date or priority went nowhere."""
    with pytest.raises(ValueError, match=message):
        run(tasks.bulk_create_tasks(3, entries))
    assert api.requests == []


@pytest.mark.parametrize("api_version", [2])
@pytest.mark.parametrize("reply", [{"ok": True}, []], ids=["no tasks key", "not an object"])
def test_bulk_create_rejects_a_response_without_the_created_tasks(api, run, reply, api_version):
    """The tasks were most likely created. Reporting none of them would be a worse
    answer than saying the reply could not be read."""
    api.returns(reply)
    with pytest.raises(RuntimeError, match="did not return the created tasks"):
        run(tasks.bulk_create_tasks(3, [{"title": "First"}]))


def test_create_bucket_sends_an_optional_limit(api, run):
    api.returns(KANBAN_VIEW)
    run(kanban.create_bucket(3, "Doing", limit=3))
    assert body(api.last) == {"title": "Doing", "limit": 3}


def test_create_label_includes_the_optional_fields(api, run):
    run(labels.create_label("Doing", hex_color="f59e0b", description="in flight"))
    assert body(api.last) == {
        "title": "Doing",
        "hex_color": "f59e0b",
        "description": "in flight",
    }


def test_list_task_placements_returns_one_entry_per_kanban_view(api, run):
    api.returns(
        {
            "id": 7,
            "buckets": [
                {"id": 43, "title": "Done", "project_view_id": 48},
                {"id": 51, "title": "Later", "project_view_id": 49},
            ],
        }
    )
    assert run(kanban.list_task_placements(7)) == [
        {"bucket_id": 43, "bucket_title": "Done", "project_view_id": 48},
        {"bucket_id": 51, "bucket_title": "Later", "project_view_id": 49},
    ]


def test_list_task_placements_asks_for_the_buckets_to_be_expanded(api, run):
    """Without `expand`, a task's `bucket_id` is 0 and the buckets are absent."""
    api.returns({"id": 7, "buckets": []})
    assert run(kanban.list_task_placements(7)) == []
    assert dict(api.last.url.params) == {"expand": "buckets"}


def test_add_relation_puts_a_non_default_kind_in_the_body(api, run):
    run(relations.add_relation(7, 9, "blocking"))
    assert body(api.last) == {"other_task_id": 9, "relation_kind": "blocking"}


def test_remove_relation_puts_the_kind_in_the_path(api, run):
    run(relations.remove_relation(7, 9, "subtask"))
    assert api.last.url.path.endswith("/tasks/7/relations/subtask/9")


@pytest.mark.parametrize("api_version", [2])
def test_update_task_keeps_done_false(api, run, api_version):
    """`done=False` is falsy. A truthiness check here would silently lose it."""
    run(tasks.update_task(7, done=False))
    assert body(api.last) == {"done": False}


def test_update_task_rejects_an_empty_payload(api, run):
    with pytest.raises(ValueError, match="No fields to update"):
        run(tasks.update_task(7))
    assert api.requests == []


@pytest.mark.parametrize("api_version", [2])
def test_set_reminders_accepts_an_empty_list_to_clear(api, run, api_version):
    run(tasks.set_reminders(7, []))
    assert body(api.last) == {"reminders": []}


# --- v1 has no partial update -----------------------------------------------
# POST /tasks/{id} replaces the task on v1, and a body carrying only the changed
# fields resets everything else. That was documented and left armed in 0.8.1,
# having already destroyed one task's description by then. These are the regression
# tests for both tools that send through that endpoint.
V1_TASK = {
    "id": 7,
    "title": "Existing",
    "description": "<p>keep me</p>",
    "priority": 4,
    "due_date": "2026-08-21T09:00:00+10:00",
}


def test_v1_update_merges_into_the_task_it_read_first(api, run):
    api.returns(V1_TASK)
    run(tasks.update_task(7, done=True))

    read, write = api.requests
    assert read.method == "GET"
    assert write.method == "POST"
    assert body(write) == {**V1_TASK, "done": True}


def test_v1_set_reminders_merges_into_the_task_it_read_first(api, run):
    """Same endpoint, same hazard. This one went unnoticed until 0.8.1: a reminders
    payload looks self-contained."""
    api.returns(V1_TASK)
    run(tasks.set_reminders(7, ["2026-08-21T09:00:00+10:00"]))

    read, write = api.requests
    assert read.method == "GET"
    assert write.method == "POST"
    assert body(write) == {
        **V1_TASK,
        "reminders": [{"reminder": "2026-08-21T09:00:00+10:00"}],
    }


def test_move_task_goes_through_the_same_write_path_as_an_update(api, run):
    """Moving is an update that sets project_id. On v1 it must merge like one: a
    single-field body there replaces the whole task."""
    api.returns(V1_TASK)
    run(tasks.move_task(7, 5))

    read, write = api.requests
    assert read.method == "GET"
    assert body(write) == {**V1_TASK, "project_id": 5}


def test_v1_refuses_to_replace_from_a_read_that_is_not_a_task(api, run):
    """The read makes the write safe. A read that returned no task must never become
    a replace: that would wipe the task."""
    api.returns_raw(204)
    with pytest.raises(RuntimeError, match="did not return task 7"):
        run(tasks.update_task(7, done=True))
    assert [r.method for r in api.requests] == ["GET"]


# --- projects, labels, and buckets have the same hazard ----------------------
# Each of these three endpoints resets fields a body omits, and each does it
# differently. The write path per resource reads first for that reason, and these
# are the tests that hold it there.
V1_PROJECT = {
    "id": 3,
    "title": "Existing",
    "description": "<p>keep me</p>",
    "hex_color": "e11d48",
    "parent_project_id": 9,
    "is_archived": True,
}


def test_v1_update_project_merges_into_the_project_it_read_first(api, run):
    """The field that matters here is `is_archived`.

    On v1 a title-only body un-archives an archived project: Go's zero value for a
    bool is false, and Vikunja cannot tell an omitted flag from one set to false.
    `hex_color` clears the same way. Verified against 2.5.0 before this was written.
    """
    api.returns(V1_PROJECT)
    run(projects.update_project(3, title="Renamed"))

    read, write = api.requests
    assert read.method == "GET"
    assert write.method == "POST"
    assert body(write) == {**V1_PROJECT, "title": "Renamed"}


def test_v1_update_project_refuses_to_replace_from_a_read_that_is_not_a_project(api, run):
    api.returns_raw(204)
    with pytest.raises(RuntimeError, match="did not return project 3"):
        run(projects.update_project(3, title="Renamed"))


def test_v1_update_label_merges_into_the_label_it_read_first(api, run):
    """A title-only body on v1 clears `hex_color` and `description`. Renaming a label
    would strip its colour."""
    api.returns({"id": 1, "title": "Existing", "hex_color": "4287f5", "description": "keep me"})
    run(labels.update_label(1, title="Renamed"))

    read, write = api.requests
    assert read.method == "GET"
    assert write.method == "POST"
    assert body(write) == {
        "id": 1,
        "title": "Renamed",
        "hex_color": "4287f5",
        "description": "keep me",
    }


def test_v1_update_label_refuses_to_replace_from_a_read_that_is_not_a_label(api, run):
    api.returns_raw(204)
    with pytest.raises(RuntimeError, match="did not return label 1"):
        run(labels.update_label(1, title="Renamed"))


@pytest.mark.parametrize("api_version", [1, 2])
def test_update_bucket_keeps_the_limit_it_was_not_asked_to_change(api, run, api_version):
    """Neither version has a partial update for a bucket, and a title-only body
    resets `limit` to 0 on both. The write is the column read back whole."""
    api.returns(VIEW_AND_ITS_BUCKETS)
    run(kanban.update_bucket(3, 41, title="Renamed"))
    assert body(api.last) == {"id": 41, "title": "Renamed", "limit": 0, "position": 100}


@pytest.mark.parametrize("api_version", [1, 2])
def test_update_bucket_sends_a_new_limit(api, run, api_version):
    api.returns(VIEW_AND_ITS_BUCKETS)
    run(kanban.update_bucket(3, 41, limit=5))
    assert body(api.last) == {"id": 41, "title": "Existing", "limit": 5, "position": 100}


def test_update_bucket_refuses_a_bucket_that_is_not_in_the_view(api, run):
    """Vikunja has no endpoint for reading one bucket. The id is checked against the
    view's list, and without that the write would address a bucket in another view."""
    api.returns(VIEW_AND_ITS_BUCKETS)
    with pytest.raises(ValueError, match="has no bucket 99"):
        run(kanban.update_bucket(3, 99, title="Renamed"))
    assert [r.method for r in api.requests] == ["GET", "GET"]


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: projects.update_project(3), id="update_project"),
        pytest.param(lambda: labels.update_label(1), id="update_label"),
        pytest.param(lambda: kanban.update_bucket(3, 41), id="update_bucket"),
    ],
)
def test_an_update_with_no_fields_is_refused_before_any_request(api, run, call):
    with pytest.raises(ValueError, match="No fields to update"):
        run(call())
    assert api.requests == []


@pytest.mark.parametrize("api_version", [2])
def test_update_project_sends_every_supplied_field(api, run, api_version):
    """v2 without a description is a single partial update, and the body on the wire
    is exactly the fields that were passed."""
    run(
        projects.update_project(
            3, title="New", parent_project_id=9, is_archived=True, hex_color="e11d48"
        )
    )
    assert [r.method for r in api.requests] == ["PATCH"]
    assert body(api.last) == {
        "title": "New",
        "parent_project_id": 9,
        "is_archived": True,
        "hex_color": "e11d48",
    }


@pytest.mark.parametrize("api_version", [2])
def test_update_project_can_unarchive(api, run, api_version):
    """`is_archived: false` has to reach the wire. It is the only way back from an
    archived project, and a falsy value is the easy one to drop while building a
    payload."""
    run(projects.update_project(3, is_archived=False))
    assert body(api.last) == {"is_archived": False}


@pytest.mark.parametrize("api_version", [2])
def test_update_label_sends_every_supplied_field(api, run, api_version):
    run(labels.update_label(1, title="New", hex_color="4287f5"))
    assert [r.method for r in api.requests] == ["PATCH"]
    assert body(api.last) == {"title": "New", "hex_color": "4287f5"}


@pytest.mark.parametrize("api_version", [2])
def test_a_project_description_change_reads_then_replaces_on_v2(api, run, api_version):
    """Two things make PATCH unusable here. It stores the Markdown verbatim into a
    field rendered as HTML, and the replace verb that does convert rejects a body
    with no title. The read supplies the title and the conversion happens."""
    api.returns({"id": 3, "title": "Existing", "hex_color": "e11d48"})
    run(projects.update_project(3, description="**bold**"))

    read, write = api.requests
    assert read.method == "GET"
    assert write.method == "PUT"
    assert dict(read.url.params) == {"format": "markdown"}
    assert dict(write.url.params) == {"format": "markdown"}
    assert body(write) == {
        "id": 3,
        "title": "Existing",
        "hex_color": "e11d48",
        "description": "**bold**",
    }


@pytest.mark.parametrize("api_version", [2])
def test_a_label_description_change_reads_then_replaces_on_v2(api, run, api_version):
    """PATCH would not convert the Markdown. Same asymmetry as a task description."""
    api.returns({"id": 1, "title": "Existing", "hex_color": "4287f5"})
    run(labels.update_label(1, description="**bold**"))

    read, write = api.requests
    assert read.method == "GET"
    assert write.method == "PUT"
    assert body(write) == {"id": 1, "title": "Existing", "hex_color": "4287f5",
                           "description": "**bold**"}


@pytest.mark.parametrize("api_version", [2])
def test_a_label_replace_sends_if_match_when_the_read_offered_an_etag(api, run, api_version):
    api.returns_in_order(
        httpx.Response(200, json={"id": 1, "title": "Existing"}, headers={"ETag": '"abc"'}),
        httpx.Response(200, json={"id": 1, "title": "Existing", "description": "x"}),
    )
    run(labels.update_label(1, description="x"))
    assert api.last.headers.get("If-Match") == '"abc"'


@pytest.mark.parametrize("api_version", [2])
def test_a_label_that_changed_underneath_reports_the_conflict(api, run, api_version):
    """412 means the label moved between the read and the write. Nothing was written,
    and the message says that in words the caller can act on."""
    api.returns_in_order(
        httpx.Response(200, json={"id": 1, "title": "Existing"}, headers={"ETag": '"abc"'}),
        httpx.Response(412, json={"detail": "precondition failed"}),
    )
    with pytest.raises(RuntimeError, match="label 1 changed while this update"):
        run(labels.update_label(1, description="x"))


@pytest.mark.parametrize("api_version", [2])
def test_a_label_replace_that_fails_for_another_reason_keeps_the_real_error(api, run, api_version):
    api.returns_in_order(
        httpx.Response(200, json={"id": 1, "title": "Existing"}),
        httpx.Response(500, json={"detail": "database is on fire"}),
    )
    with pytest.raises(httpx.HTTPStatusError, match="database is on fire"):
        run(labels.update_label(1, description="x"))


# --- query parameters -------------------------------------------------------
@pytest.mark.parametrize("api_version", [1, 2])
def test_list_projects_omits_archived_projects_by_default(api, run, api_version):
    """Vikunja leaves archived projects out of this endpoint, and asks for them by
    query parameter. Sending it unconditionally would change the default listing."""
    run(projects.list_projects())
    assert dict(api.last.url.params) == {}

    run(projects.list_projects(include_archived=True))
    assert dict(api.last.url.params) == {"is_archived": "true"}


def test_list_tasks_always_paginates(api, run):
    run(tasks.list_tasks(3))
    assert dict(api.last.url.params) == {"page": "1", "per_page": "50"}


def test_list_tasks_passes_filter_and_sort_through_to_the_server(api, run):
    run(tasks.list_tasks(3, filter="done = false", sort_by="priority", page=2, per_page=10))
    assert dict(api.last.url.params) == {
        "page": "2",
        "per_page": "10",
        "filter": "done = false",
        "sort_by": "priority",
    }


@pytest.mark.parametrize(("api_version", "param"), [(1, "s"), (2, "q")])
def test_search_users_uses_the_search_param_the_version_expects(api, run, param):
    """v1 names it `s`, v2 renamed it to `q`. Sending the wrong one is not an error:
    it silently returns nothing. That is what this pins."""
    run(assignees.search_users("stefan"))
    assert dict(api.last.url.params) == {param: "stefan"}


# --- response shaping -------------------------------------------------------
def test_list_projects_exposes_nesting_and_defaults_missing_fields(api, run):
    api.returns(
        [
            {"id": 1, "title": "Home", "parent_project_id": 0, "is_archived": False},
            {"id": 11, "title": "Fitness"},
        ]
    )
    assert run(projects.list_projects()) == [
        {"id": 1, "title": "Home", "parent_project_id": 0, "is_archived": False},
        {"id": 11, "title": "Fitness", "parent_project_id": 0, "is_archived": False},
    ]


def test_list_tasks_returns_a_summary(api, run):
    api.returns(
        [
            {
                "id": 374,
                "identifier": "#1",
                "title": "Task",
                "done": False,
                "priority": 3,
                "description": "dropped",
                "hex_color": "dropped",
            }
        ]
    )
    assert run(tasks.list_tasks(12)) == [
        {"id": 374, "identifier": "#1", "title": "Task", "done": False, "priority": 3}
    ]


def test_list_labels_returns_id_and_title(api, run):
    api.returns([{"id": 1, "title": "Doing", "hex_color": "f59e0b"}])
    assert run(labels.list_labels()) == [{"id": 1, "title": "Doing"}]


def test_list_comments_flattens_the_author_to_a_username(api, run):
    api.returns([{"id": 21, "comment": "hello", "author": {"username": "stefan"}}])
    assert run(comments.list_comments(7)) == [
        {"id": 21, "comment": "hello", "author": "stefan"}
    ]


def test_list_comments_tolerates_a_missing_author(api, run):
    api.returns([{"id": 21, "comment": "hello"}])
    assert run(comments.list_comments(7)) == [{"id": 21, "comment": "hello", "author": None}]


def test_search_users_returns_id_username_and_name(api, run):
    api.returns([{"id": 1, "username": "stefan", "name": "Stefan", "email": "dropped"}])
    assert run(assignees.search_users("stefan")) == [
        {"id": 1, "username": "stefan", "name": "Stefan"}
    ]


def test_list_assignees_returns_id_and_username(api, run):
    api.returns([{"id": 1, "username": "stefan", "name": "dropped", "email": "dropped"}])
    assert run(assignees.list_assignees(7)) == [{"id": 1, "username": "stefan"}]


@pytest.mark.parametrize("api_version", [1, 2])
def test_duplicate_task_returns_the_copy_out_of_its_envelope(api, run, api_version):
    """Vikunja answers a duplicate with `{"duplicated_task": {...}}` on both versions,
    and v2 adds a `$schema` sibling. A caller reading `id` off that envelope gets
    None, and the copy it has just made becomes unreachable."""
    api.returns(
        {
            "$schema": "https://vikunja.test/api/v2/schemas/TaskDuplicate.json",
            "duplicated_task": {"id": 556, "title": "Task", "project_id": 19},
        }
    )
    assert run(tasks.duplicate_task(7)) == {"id": 556, "title": "Task", "project_id": 19}


def test_duplicate_task_passes_through_a_body_without_the_envelope(api, run):
    """The key belongs to Vikunja. A rename upstream would otherwise turn a
    successful duplicate into a KeyError. The copy exists by then, and its id is the
    only way back to it."""
    api.returns({"id": 556, "title": "Task"})
    assert run(tasks.duplicate_task(7)) == {"id": 556, "title": "Task"}


# Applied to each collection-shape test below. All six listings are held to the
# same contract.
every_listing = pytest.mark.parametrize(
    "listing",
    [
        lambda: projects.list_projects(),
        lambda: tasks.list_tasks(3),
        lambda: labels.list_labels(),
        lambda: comments.list_comments(7),
        lambda: assignees.search_users("x"),
        lambda: assignees.list_assignees(7),
    ],
    ids=["projects", "tasks", "labels", "comments", "users", "assignees"],
)


@every_listing
def test_listings_return_empty_when_the_api_sends_null(api, run, listing):
    # Vikunja sends a literal `null` for some empty collections, never `[]`.
    # Passed as raw bytes because httpx cannot distinguish `json=None` from no
    # body at all.
    api.returns_raw(200, b"null")
    assert run(listing()) == []


@every_listing
def test_listings_return_empty_for_an_empty_array(api, run, listing):
    api.returns([])
    assert run(listing()) == []


@every_listing
def test_listings_reject_a_response_that_is_not_a_collection(api, run, listing):
    """An empty body is not an empty collection.

    `_request` reports a bodyless response as a status dict, which is right for a
    delete and is not a listing. Returning [] here would be indistinguishable from
    genuinely having no items. An agent would confidently report that nothing
    exists when the response was actually swallowed upstream.
    """
    api.returns_raw(204)
    with pytest.raises(RuntimeError, match="expected a list from the API, got dict"):
        run(listing())
