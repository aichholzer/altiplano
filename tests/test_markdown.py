"""Markdown exchange on v2, and its absence on v1.

Vikunja stores descriptions and comments as HTML. v2 will convert to and from
Markdown when asked, which is what lets callers stop writing HTML by hand. The
awkward part, and the reason for the shape of these tests, is that v2 honours the
parameter on reads, creates and replaces but silently ignores it on a partial
update: PATCH returns 200 and stores the Markdown verbatim into a field that is
rendered as HTML. So a description change has to go through a full replace.
"""

import httpx
import pytest

from altiplano import server

MD = "**bold** and a [link](https://example.com)"


def fmt(request) -> str | None:
    return dict(request.url.params).get("format")


# Tools whose payload or result carries rich text, so they must ask for Markdown.
RICH_TEXT_CALLS = [
    pytest.param(lambda: server.get_task(7), id="get_task"),
    pytest.param(lambda: server.list_comments(7), id="list_comments"),
    pytest.param(lambda: server.create_task(3, "T", description=MD), id="create_task"),
    pytest.param(lambda: server.create_project("P", description=MD), id="create_project"),
    pytest.param(lambda: server.add_comment(7, MD), id="add_comment"),
    pytest.param(lambda: server.update_comment(7, 21, MD), id="update_comment"),
]


@pytest.mark.parametrize("api_version", [2])
@pytest.mark.parametrize("call", RICH_TEXT_CALLS)
def test_v2_asks_for_markdown(api, run, call, api_version):
    run(call())
    assert fmt(api.last) == "markdown"


@pytest.mark.parametrize("api_version", [1])
@pytest.mark.parametrize("call", RICH_TEXT_CALLS)
def test_v1_never_asks_for_markdown(api, run, call, api_version):
    """v1 has no format parameter, so sending one would be noise at best."""
    run(call())
    assert fmt(api.last) is None


@pytest.mark.parametrize("api_version", [2])
def test_v2_partial_updates_do_not_ask_for_markdown(api, run, api_version):
    """A partial update answers with the whole task, description included, and that
    description comes back as the stored HTML.

    0.10.0 asked for Markdown here, on the theory that v2 ignored the parameter only
    for the request body. It ignores it for the response too: asking against 2.5.0
    returned `<p><strong>Bold</strong></p>` regardless. The parameter is therefore
    not sent, because one that the server discards implies a guarantee that does not
    hold. `get_task` is the way to read a description as Markdown.
    """
    run(server.update_task(7, priority=3))
    assert fmt(api.last) is None

    run(server.set_reminders(7, []))
    assert fmt(api.last) is None


@pytest.mark.parametrize("api_version", [2])
def test_v2_description_update_reads_then_replaces(api, run, api_version):
    """PATCH would not convert, so a description change becomes read then replace."""
    api.returns({"id": 7, "title": "Existing", "description": "old", "priority": 4})
    run(server.update_task(7, description=MD))

    read, write = api.requests
    assert read.method == "GET"
    assert fmt(read) == "markdown"
    assert write.method == "PUT"
    assert fmt(write) == "markdown"


@pytest.mark.parametrize("api_version", [2])
def test_v2_replace_preserves_fields_it_was_not_given(api, run, api_version):
    """The whole point of reading first: an unrelated field must survive."""
    api.returns({"id": 7, "title": "Existing", "description": "old", "priority": 4, "done": True})
    run(server.update_task(7, description=MD))

    import json

    body = json.loads(api.requests[-1].content)
    assert body["description"] == MD
    assert body["title"] == "Existing"
    assert body["priority"] == 4
    assert body["done"] is True


@pytest.mark.parametrize("api_version", [2])
def test_v2_replace_drops_the_schema_key_it_read_back(api, run, api_version):
    """`$schema` is v2 response metadata, not a writable field."""
    api.returns({"$schema": "https://example.test/schema.json", "id": 7, "description": "old"})
    run(server.update_task(7, description=MD))

    import json

    assert "$schema" not in json.loads(api.requests[-1].content)


@pytest.mark.parametrize("api_version", [2])
def test_v2_update_without_a_description_stays_a_single_partial_update(api, run, api_version):
    """No rich text, no reason to pay for a read or to widen the write."""
    run(server.update_task(7, priority=3))
    assert [r.method for r in api.requests] == ["PATCH"]


@pytest.mark.parametrize("api_version", [1])
def test_v1_reads_first_too_but_never_for_markdown(api, run, api_version):
    """v1 reads before writing for a different reason than v2 does.

    It cannot convert Markdown at all, so the read is not about that: its update
    endpoint is a replace, and reading first is what stops the write wiping the
    fields it was not given. Neither request asks for a format v1 does not have.
    """
    api.returns({"id": 7, "description": "<p>old</p>"})
    run(server.update_task(7, description="<p>html</p>"))
    assert [r.method for r in api.requests] == ["GET", "POST"]
    assert all(fmt(r) is None for r in api.requests)


@pytest.mark.parametrize("api_version", [2])
def test_set_reminders_stays_a_partial_update(api, run, api_version):
    """Reminders carry no rich text, so they must not trigger a replace."""
    run(server.set_reminders(7, ["2026-08-21T09:00:00+10:00"]))
    assert [r.method for r in api.requests] == ["PATCH"]


# --- the read-then-replace race ---------------------------------------------
# Reading before writing opens a window where something else can write in between,
# and the replace would then silently discard that edit. v2 returns an ETag on a
# single-resource read and honours If-Match, so the window can be made to fail
# loudly instead.
@pytest.mark.parametrize("api_version", [2])
def test_v2_replace_sends_the_etag_back_as_if_match(api, run, api_version):
    api.returns_in_order(
        httpx.Response(200, json={"id": 7, "description": "old"}, headers={"ETag": '"abc123"'}),
        httpx.Response(200, json={"id": 7, "description": MD}),
    )
    run(server.update_task(7, description=MD))
    assert api.requests[-1].headers["If-Match"] == '"abc123"'


@pytest.mark.parametrize("api_version", [2])
def test_v2_replace_omits_if_match_when_the_read_offered_no_etag(api, run, api_version):
    """A server without ETags has to behave exactly as it did before."""
    api.returns({"id": 7, "description": "old"})
    run(server.update_task(7, description=MD))
    assert "if-match" not in api.requests[-1].headers


@pytest.mark.parametrize("api_version", [2])
def test_v2_replace_turns_a_precondition_failure_into_something_actionable(api, run, api_version):
    """412 is the whole point of the header, and "Precondition Failed" on its own
    tells an agent nothing about what to do next."""
    api.returns_in_order(
        httpx.Response(200, json={"id": 7, "description": "old"}, headers={"ETag": '"abc123"'}),
        httpx.Response(412, json={"title": "Precondition Failed"}),
    )
    with pytest.raises(RuntimeError, match="changed while this update was being prepared"):
        run(server.update_task(7, description=MD))


@pytest.mark.parametrize("api_version", [2])
def test_v2_replace_lets_any_other_failure_through_as_it_is(api, run, api_version):
    api.returns_in_order(
        httpx.Response(200, json={"id": 7, "description": "old"}),
        httpx.Response(500, json={"detail": "database is on fire"}),
    )
    with pytest.raises(httpx.HTTPStatusError, match="database is on fire"):
        run(server.update_task(7, description=MD))


@pytest.mark.parametrize("api_version", [2])
def test_v2_replace_refuses_a_read_that_is_not_a_task(api, run, api_version):
    """A bodyless response arrives as a status dict, and a full replace built from
    that would wipe the task rather than update it."""
    api.returns_raw(204)
    with pytest.raises(RuntimeError, match="did not return task 7"):
        run(server.update_task(7, description=MD))
    # The write must not have been attempted.
    assert [r.method for r in api.requests] == ["GET"]
