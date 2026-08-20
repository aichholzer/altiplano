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
def route(name, call, verb, path, body):
    """One routing case, named so failures point at the tool."""
    return pytest.param(call, verb, path, body, id=name)


ROUTES = [
    route("list_projects", lambda: server.list_projects(), "GET", "/projects", {}),
    route("get_task", lambda: server.get_task(7), "GET", "/tasks/7", {}),
    route("list_tasks", lambda: server.list_tasks(3), "GET", "/projects/3/tasks", {}),
    route("list_labels", lambda: server.list_labels(), "GET", "/labels", {}),
    route("add_label", lambda: server.add_label(7, 1), "PUT", "/tasks/7/labels", {"label_id": 1}),
    route("remove_label", lambda: server.remove_label(7, 1), "DELETE", "/tasks/7/labels/1", {}),
    route("list_comments", lambda: server.list_comments(7), "GET", "/tasks/7/comments", {}),
    route(
        "add_comment",
        lambda: server.add_comment(7, "hello"),
        "PUT",
        "/tasks/7/comments",
        {"comment": "hello"},
    ),
    route(
        "update_comment",
        lambda: server.update_comment(7, 21, "edited"),
        "POST",
        "/tasks/7/comments/21",
        {"comment": "edited"},
    ),
    route("delete_comment", lambda: server.delete_comment(7, 21), "DELETE", "/tasks/7/comments/21", {}),
    route("list_assignees", lambda: server.list_assignees(7), "GET", "/tasks/7/assignees", {}),
    route("add_assignee", lambda: server.add_assignee(7, 2), "PUT", "/tasks/7/assignees", {"user_id": 2}),
    route("remove_assignee", lambda: server.remove_assignee(7, 2), "DELETE", "/tasks/7/assignees/2", {}),
    route("create_project", lambda: server.create_project("Board"), "PUT", "/projects", {"title": "Board"}),
    route("create_task", lambda: server.create_task(3, "Task"), "PUT", "/projects/3/tasks", {"title": "Task"}),
    route("update_task", lambda: server.update_task(7, done=True), "POST", "/tasks/7", {"done": True}),
    route(
        "set_reminders",
        lambda: server.set_reminders(7, ["2026-08-20T09:00:00+10:00"]),
        "POST",
        "/tasks/7",
        {"reminders": [{"reminder": "2026-08-20T09:00:00+10:00"}]},
    ),
    route("search_users", lambda: server.search_users("stefan"), "GET", "/users", {}),
]


@pytest.mark.parametrize(("call", "verb", "path", "expected_body"), ROUTES)
def test_tool_uses_the_expected_verb_path_and_body(api, run, call, verb, path, expected_body):
    run(call())
    assert api.last.method == verb
    assert api.last.url.path.endswith(path)
    assert body(api.last) == expected_body


def test_every_tool_is_covered_by_a_routing_case():
    """Guards against a new tool being added without a wire-contract test."""
    import asyncio

    registered = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    assert registered == {case.id for case in ROUTES}


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
    run(
        server.update_task(
            7,
            title="New",
            description="why",
            done=False,
            priority=2,
            start_date="2026-08-20T09:00:00+10:00",
            end_date="2026-08-20T17:00:00+10:00",
        )
    )
    assert body(api.last) == {
        "title": "New",
        "description": "why",
        "done": False,
        "priority": 2,
        "start_date": "2026-08-20T09:00:00+10:00",
        "end_date": "2026-08-20T17:00:00+10:00",
    }


def test_update_task_sends_done_false_rather_than_dropping_it(api, run):
    """`done=False` is falsy, so a truthiness check here would silently lose it."""
    run(server.update_task(7, done=False))
    assert body(api.last) == {"done": False}


def test_update_task_rejects_an_empty_payload(api, run):
    with pytest.raises(ValueError, match="No fields to update"):
        run(server.update_task(7))
    assert api.requests == []


def test_set_reminders_accepts_an_empty_list_to_clear(api, run):
    run(server.set_reminders(7, []))
    assert body(api.last) == {"reminders": []}


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


def test_search_users_sends_the_query_as_s(api, run):
    run(server.search_users("stefan"))
    assert dict(api.last.url.params) == {"s": "stefan"}


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
