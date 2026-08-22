"""Every tool's wire contract: verb, path, query and body.

Vikunja inverts the usual REST convention, using PUT to create and POST to
update, so the verb assertions here are load bearing rather than incidental.
"""

import json

import pytest

from altiplano import server


def body(request) -> dict:
    return json.loads(request.content) if request.content else {}


# --- routing ----------------------------------------------------------------
def route(name, call, verbs, path, body, response=None):
    """One routing case, named so failures point at the tool.

    `verbs` is {1: verb, 2: verb}. Both are written out as literals rather than
    derived from the server's own mapping, so the test cannot agree with a wrong
    implementation.

    `body` is the expected request body, or a {1: body, 2: body} mapping where the
    two versions differ. `response` overrides what the fake returns, which the two
    tools that read the task before writing it need.
    """
    return pytest.param(call, verbs, path, body, response, id=name)


READ = {1: "GET", 2: "GET"}
CREATE = {1: "PUT", 2: "POST"}
UPDATE = {1: "POST", 2: "PATCH"}
REMOVE = {1: "DELETE", 2: "DELETE"}
# v2 honours ?format=markdown on a replace but not on a partial update, so writes
# that carry rich text go through PUT there.
REPLACE = {1: "POST", 2: "PUT"}

# What the fake hands back to the two tools that read the task before writing it.
# On v1 the write is that task with the change merged in, because v1's update
# endpoint is a replace; on v2 it is the change alone.
READ_BACK = {"id": 7, "title": "Existing"}


ROUTES = [
    route("list_projects", lambda: server.list_projects(), READ, "/projects", {}),
    route("get_task", lambda: server.get_task(7), READ, "/tasks/7", {}),
    route("list_tasks", lambda: server.list_tasks(3), READ, "/projects/3/tasks", {}),
    route("list_labels", lambda: server.list_labels(), READ, "/labels", {}),
    route("add_label", lambda: server.add_label(7, 1), CREATE, "/tasks/7/labels", {"label_id": 1}),
    route("remove_label", lambda: server.remove_label(7, 1), REMOVE, "/tasks/7/labels/1", {}),
    route("list_comments", lambda: server.list_comments(7), READ, "/tasks/7/comments", {}),
    route(
        "add_comment",
        lambda: server.add_comment(7, "hello"),
        CREATE,
        "/tasks/7/comments",
        {"comment": "hello"},
    ),
    route(
        "update_comment",
        lambda: server.update_comment(7, 21, "edited"),
        REPLACE,
        "/tasks/7/comments/21",
        {"comment": "edited"},
    ),
    route("delete_comment", lambda: server.delete_comment(7, 21), REMOVE, "/tasks/7/comments/21", {}),
    route(
        "add_relation",
        lambda: server.add_relation(7, 9),
        CREATE,
        "/tasks/7/relations",
        {"other_task_id": 9, "relation_kind": "related"},
    ),
    route(
        "remove_relation",
        lambda: server.remove_relation(7, 9),
        REMOVE,
        "/tasks/7/relations/related/9",
        # The path carries all three values and the API documents the body as
        # required anyway, so both go out.
        {"other_task_id": 9, "relation_kind": "related"},
    ),
    route("list_assignees", lambda: server.list_assignees(7), READ, "/tasks/7/assignees", {}),
    route("add_assignee", lambda: server.add_assignee(7, 2), CREATE, "/tasks/7/assignees", {"user_id": 2}),
    route("remove_assignee", lambda: server.remove_assignee(7, 2), REMOVE, "/tasks/7/assignees/2", {}),
    route("create_project", lambda: server.create_project("Board"), CREATE, "/projects", {"title": "Board"}),
    route("create_task", lambda: server.create_task(3, "Task"), CREATE, "/projects/3/tasks", {"title": "Task"}),
    route(
        "update_task",
        lambda: server.update_task(7, done=True),
        UPDATE,
        "/tasks/7",
        {1: {**READ_BACK, "done": True}, 2: {"done": True}},
        response=READ_BACK,
    ),
    route("delete_task", lambda: server.delete_task(7), REMOVE, "/tasks/7", {}),
    route(
        "set_reminders",
        lambda: server.set_reminders(7, ["2026-08-20T09:00:00+10:00"]),
        UPDATE,
        "/tasks/7",
        {
            1: {**READ_BACK, "reminders": [{"reminder": "2026-08-20T09:00:00+10:00"}]},
            2: {"reminders": [{"reminder": "2026-08-20T09:00:00+10:00"}]},
        },
        response=READ_BACK,
    ),
    route("search_users", lambda: server.search_users("stefan"), READ, "/users", {}),
]


@pytest.mark.parametrize("api_version", [1, 2])
@pytest.mark.parametrize(("call", "verbs", "path", "expected_body", "response"), ROUTES)
def test_tool_uses_the_expected_verb_path_and_body(
    api, run, api_version, call, verbs, path, expected_body, response
):
    if response is not None:
        api.returns(response)
    run(call())
    assert api.last.method == verbs[api_version]
    assert api.last.url.path.endswith(path)
    # A body given per version, for the tools whose v1 write merges into the task
    # they read. A real JSON body never has integer keys, so this cannot collide.
    expected = expected_body[api_version] if set(expected_body) == {1, 2} else expected_body
    assert body(api.last) == expected


def test_every_tool_is_covered_by_a_routing_case():
    """Guards against a new tool being added without a wire-contract test."""
    import asyncio

    registered = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    assert registered == {case.id for case in ROUTES}


# --- api version selection --------------------------------------------------
@pytest.mark.parametrize("api_version", [1, 2])
def test_version_comes_from_the_configured_url(api, api_version):
    assert server._version() == api_version


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://vikunja.test/api/v1", 1),
        ("https://vikunja.test/api/v2", 2),
        # A trailing slash is stripped by _base before the check.
        ("https://vikunja.test/api/v2/", 2),
        # Anything unrecognised falls back to v1 rather than guessing.
        ("https://vikunja.test/api", 1),
        ("https://vikunja.test/api/v3", 1),
    ],
)
def test_version_falls_back_to_v1_for_anything_but_an_explicit_v2_url(monkeypatch, url, expected):
    monkeypatch.setenv("VIKUNJA_URL", url)
    assert server._version() == expected


@pytest.mark.parametrize("api_version", [1, 2])
def test_listings_unwrap_whichever_collection_shape_arrives(api, run, api_version):
    """v1 sends a bare array, v2 a pagination envelope. Both must work, and the
    check is on shape, so a version mismatch degrades gracefully rather than
    breaking."""
    api.returns([{"id": 1, "title": "Home"}])
    assert run(server.list_projects())[0]["title"] == "Home"

    api.returns({"items": [{"id": 1, "title": "Home"}], "total": 1, "page": 1})
    assert run(server.list_projects())[0]["title"] == "Home"


def test_envelope_with_no_items_returns_empty(api, run):
    api.returns({"items": [], "total": 0, "page": 1, "per_page": 50})
    assert run(server.list_projects()) == []


def test_a_dict_without_items_is_still_rejected(api, run):
    """The 0.5.4 protection must survive envelope support: a bodyless response
    reported as a status dict has no `items` and is not an empty collection."""
    api.returns_raw(204)
    with pytest.raises(RuntimeError, match="expected a list from the API, got dict"):
        run(server.list_projects())


# --- optional payload fields ------------------------------------------------
def test_create_project_omits_unset_fields(api, run):
    run(server.create_project("Board"))
    assert body(api.last) == {"title": "Board"}


def test_create_project_includes_supplied_fields(api, run):
    run(server.create_project("Sub", parent_project_id=4, description="notes"))
    assert body(api.last) == {"title": "Sub", "parent_project_id": 4, "description": "notes"}


def test_create_task_includes_every_supplied_field(api, run):
    run(
        server.create_task(
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
    # A description routes through the read-then-replace path on both versions, so
    # the canned task is what these changes get merged into.
    api.returns({"id": 7})
    run(
        server.update_task(
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
# merges into the task it read first, which the routing table and the replace
# tests below cover instead.
@pytest.mark.parametrize("api_version", [2])
def test_update_task_can_set_a_due_date(api, run, api_version):
    """`create_task` always took a deadline; `update_task` did not, so a deadline
    could be given at creation and never changed afterwards."""
    run(server.update_task(7, due_date="2026-08-21T09:00:00+10:00"))
    assert body(api.last) == {"due_date": "2026-08-21T09:00:00+10:00"}


# --- clearing dates ---------------------------------------------------------
# Vikunja has no null for a date: an unset one is Go's zero time, on the wire and
# in the database, so clearing means writing that value. An empty string is the
# caller's way of asking, since None already means "leave it out of the payload"
# and "" is not a datetime Vikunja parses.
@pytest.mark.parametrize("api_version", [2])
@pytest.mark.parametrize("field", ["due_date", "start_date", "end_date"])
def test_update_task_clears_a_date_given_an_empty_string(api, run, field, api_version):
    run(server.update_task(7, **{field: ""}))
    assert body(api.last) == {field: "0001-01-01T00:00:00Z"}


@pytest.mark.parametrize("field", ["due_date", "start_date", "end_date"])
def test_create_task_clears_a_date_given_an_empty_string(api, run, field):
    run(server.create_task(3, "Task", **{field: ""}))
    assert body(api.last) == {"title": "Task", field: "0001-01-01T00:00:00Z"}


# --- progress, favourite and repeating --------------------------------------
PROGRESS_FIELDS = {
    "percent_done": 0.25,
    "is_favorite": True,
    "repeat_after": 86400,
    "repeat_mode": 2,
}


def test_create_task_carries_the_progress_and_repeat_fields(api, run):
    run(server.create_task(3, "Task", **PROGRESS_FIELDS))
    assert body(api.last) == {"title": "Task", **PROGRESS_FIELDS}


@pytest.mark.parametrize("api_version", [2])
def test_update_task_carries_the_progress_and_repeat_fields(api, run, api_version):
    run(server.update_task(7, **PROGRESS_FIELDS))
    assert body(api.last) == PROGRESS_FIELDS


# Every one of these means something and every one is falsy, so a truthiness check
# in the payload builder would drop exactly the values that turn a feature off.
OFF_VALUES = {
    "percent_done": 0.0,
    "is_favorite": False,
    "repeat_after": 0,
    "repeat_mode": 0,
}


@pytest.mark.parametrize(("field", "value"), OFF_VALUES.items())
def test_create_task_sends_a_falsy_value_rather_than_dropping_it(api, run, field, value):
    run(server.create_task(3, "Task", **{field: value}))
    assert body(api.last) == {"title": "Task", field: value}


@pytest.mark.parametrize("api_version", [2])
@pytest.mark.parametrize(("field", "value"), OFF_VALUES.items())
def test_update_task_sends_a_falsy_value_rather_than_dropping_it(
    api, run, field, value, api_version
):
    run(server.update_task(7, **{field: value}))
    assert body(api.last) == {field: value}


# --- relations --------------------------------------------------------------
# The routing cases above cover the default `related` kind. What matters here is
# that a kind reaches a different place in each tool: the body on create, and the
# path on remove, where getting it wrong would silently address another relation.
def test_add_relation_carries_a_non_default_kind_in_the_body(api, run):
    run(server.add_relation(7, 9, "blocking"))
    assert body(api.last) == {"other_task_id": 9, "relation_kind": "blocking"}


def test_remove_relation_puts_the_kind_in_the_path(api, run):
    run(server.remove_relation(7, 9, "subtask"))
    assert api.last.url.path.endswith("/tasks/7/relations/subtask/9")


@pytest.mark.parametrize("api_version", [2])
def test_update_task_sends_done_false_rather_than_dropping_it(api, run, api_version):
    """`done=False` is falsy, so a truthiness check here would silently lose it."""
    run(server.update_task(7, done=False))
    assert body(api.last) == {"done": False}


def test_update_task_rejects_an_empty_payload(api, run):
    with pytest.raises(ValueError, match="No fields to update"):
        run(server.update_task(7))
    assert api.requests == []


@pytest.mark.parametrize("api_version", [2])
def test_set_reminders_accepts_an_empty_list_to_clear(api, run, api_version):
    run(server.set_reminders(7, []))
    assert body(api.last) == {"reminders": []}


# --- v1 has no partial update -----------------------------------------------
# POST /tasks/{id} replaces the task on v1, so a body carrying only the changed
# fields resets everything else. That was documented and left armed in 0.8.1, and
# it had already destroyed one task's description by then, so these are the
# regression tests for both tools that send through that endpoint.
V1_TASK = {
    "id": 7,
    "title": "Existing",
    "description": "<p>keep me</p>",
    "priority": 4,
    "due_date": "2026-08-21T09:00:00+10:00",
}


def test_v1_update_merges_into_the_task_it_read_first(api, run):
    api.returns(V1_TASK)
    run(server.update_task(7, done=True))

    read, write = api.requests
    assert read.method == "GET"
    assert write.method == "POST"
    assert body(write) == {**V1_TASK, "done": True}


def test_v1_set_reminders_merges_into_the_task_it_read_first(api, run):
    """Same endpoint, same hazard. This one went unnoticed until 0.8.1, because a
    reminders payload looks self-contained."""
    api.returns(V1_TASK)
    run(server.set_reminders(7, ["2026-08-21T09:00:00+10:00"]))

    read, write = api.requests
    assert read.method == "GET"
    assert write.method == "POST"
    assert body(write) == {
        **V1_TASK,
        "reminders": [{"reminder": "2026-08-21T09:00:00+10:00"}],
    }


def test_v1_refuses_to_replace_from_a_read_that_is_not_a_task(api, run):
    """The read is what makes the write safe, so a read that returned no task must
    not be turned into a replace: that would wipe the task instead of updating it."""
    api.returns_raw(204)
    with pytest.raises(RuntimeError, match="did not return task 7"):
        run(server.update_task(7, done=True))
    assert [r.method for r in api.requests] == ["GET"]


# --- query parameters -------------------------------------------------------
def test_list_tasks_always_paginates(api, run):
    run(server.list_tasks(3))
    assert dict(api.last.url.params) == {"page": "1", "per_page": "50"}


def test_list_tasks_passes_filter_and_sort_through_to_the_server(api, run):
    run(server.list_tasks(3, filter="done = false", sort_by="priority", page=2, per_page=10))
    assert dict(api.last.url.params) == {
        "page": "2",
        "per_page": "10",
        "filter": "done = false",
        "sort_by": "priority",
    }


@pytest.mark.parametrize(("api_version", "param"), [(1, "s"), (2, "q")])
def test_search_users_uses_the_search_param_the_version_expects(api, run, param):
    """v1 names it `s`, v2 renamed it to `q`. Sending the wrong one is not an
    error, it silently returns nothing, which is why this is pinned."""
    run(server.search_users("stefan"))
    assert dict(api.last.url.params) == {param: "stefan"}


# --- response shaping -------------------------------------------------------
def test_list_projects_exposes_nesting_and_defaults_missing_fields(api, run):
    api.returns(
        [
            {"id": 1, "title": "Home", "parent_project_id": 0, "is_archived": False},
            {"id": 11, "title": "Fitness"},
        ]
    )
    assert run(server.list_projects()) == [
        {"id": 1, "title": "Home", "parent_project_id": 0, "is_archived": False},
        {"id": 11, "title": "Fitness", "parent_project_id": 0, "is_archived": False},
    ]


def test_list_tasks_returns_a_summary_not_the_full_task(api, run):
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
    assert run(server.list_tasks(12)) == [
        {"id": 374, "identifier": "#1", "title": "Task", "done": False, "priority": 3}
    ]


def test_list_labels_returns_id_and_title(api, run):
    api.returns([{"id": 1, "title": "Doing", "hex_color": "f59e0b"}])
    assert run(server.list_labels()) == [{"id": 1, "title": "Doing"}]


def test_list_comments_flattens_the_author_to_a_username(api, run):
    api.returns([{"id": 21, "comment": "hello", "author": {"username": "stefan"}}])
    assert run(server.list_comments(7)) == [
        {"id": 21, "comment": "hello", "author": "stefan"}
    ]


def test_list_comments_tolerates_a_missing_author(api, run):
    api.returns([{"id": 21, "comment": "hello"}])
    assert run(server.list_comments(7)) == [{"id": 21, "comment": "hello", "author": None}]


def test_search_users_returns_id_username_and_name(api, run):
    api.returns([{"id": 1, "username": "stefan", "name": "Stefan", "email": "dropped"}])
    assert run(server.search_users("stefan")) == [
        {"id": 1, "username": "stefan", "name": "Stefan"}
    ]


def test_list_assignees_returns_id_and_username(api, run):
    api.returns([{"id": 1, "username": "stefan", "name": "dropped", "email": "dropped"}])
    assert run(server.list_assignees(7)) == [{"id": 1, "username": "stefan"}]


# Applied to each collection-shape test below, so all six listings are held to
# the same contract.
every_listing = pytest.mark.parametrize(
    "listing",
    [
        lambda: server.list_projects(),
        lambda: server.list_tasks(3),
        lambda: server.list_labels(),
        lambda: server.list_comments(7),
        lambda: server.search_users("x"),
        lambda: server.list_assignees(7),
    ],
    ids=["projects", "tasks", "labels", "comments", "users", "assignees"],
)


@every_listing
def test_listings_return_empty_when_the_api_sends_null(api, run, listing):
    # Vikunja sends a literal `null` rather than `[]` for some empty collections.
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
    delete but is not a listing. Returning [] here would be indistinguishable
    from genuinely having no items, so an agent would confidently report that
    nothing exists when the response was actually swallowed upstream.
    """
    api.returns_raw(204)
    with pytest.raises(RuntimeError, match="expected a list from the API, got dict"):
        run(listing())
