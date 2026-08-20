"""Markdown exchange on v2, and its absence on v1.

Vikunja stores descriptions and comments as HTML. v2 will convert to and from
Markdown when asked, which is what lets callers stop writing HTML by hand. The
awkward part, and the reason for the shape of these tests, is that v2 honours the
parameter on reads, creates and replaces but silently ignores it on a partial
update: PATCH returns 200 and stores the Markdown verbatim into a field that is
rendered as HTML. So a description change has to go through a full replace.
"""

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
def test_v1_description_update_is_a_single_request(api, run, api_version):
    """v1 cannot convert Markdown, so there is nothing to read first."""
    run(server.update_task(7, description="<p>html</p>"))
    assert [r.method for r in api.requests] == ["POST"]
    assert fmt(api.requests[0]) is None


@pytest.mark.parametrize("api_version", [2])
def test_set_reminders_stays_a_partial_update(api, run, api_version):
    """Reminders carry no rich text, so they must not trigger a replace."""
    run(server.set_reminders(7, ["2026-08-21T09:00:00+10:00"]))
    assert [r.method for r in api.requests] == ["PATCH"]
