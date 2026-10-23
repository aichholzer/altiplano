"""Relation tools: linking one task to another."""

import re

from altiplano.api import _request, _verb
from altiplano.app import mcp

# `remove_relation` puts the kind in the request path. A kind carrying a slash or
# a dot segment addresses a different endpoint under the API root, and httpx
# resolves the dot segments itself while it merges the path onto the base URL, so
# the request leaves as a clean URL somewhere else entirely. This pattern holds
# the value to one bare word. Which words Vikunja accepts stays Vikunja's to say.
_BARE_WORD = re.compile(r"\A[A-Za-z0-9_-]+\Z")


def _checked_kind(relation_kind: str) -> str:
    if not _BARE_WORD.fullmatch(relation_kind):
        raise ValueError(
            "relation_kind takes one word of letters, digits, hyphens, and "
            f"underscores; got {relation_kind!r}"
        )
    return relation_kind


# There is no list_relations: `get_task` already returns `related_tasks`, grouped
# by kind. The kind is passed straight through, the same way `filter` is: the
# server owns that vocabulary, and it explains itself when given something it does
# not recognise.
@mcp.tool()
async def add_relation(task_id: int, other_task_id: int, relation_kind: str = "related") -> dict:
    """Relate one task to another. Defaults to a plain, symmetric `related` link.

    Kinds: subtask, parenttask, related, duplicateof, duplicates, blocking,
    blocked, precedes, follows, copiedfrom, copiedto.

    `task_id` is the base task and `other_task_id` is the one being related to it.
    That direction decides the asymmetric kinds: `subtask` makes the other task a
    child of this one. Needs write access to the base task and read access to the
    other; they do not have to be in the same project.
    """
    kind = _checked_kind(relation_kind)
    return await _request(
        _verb("create"),
        f"/tasks/{task_id}/relations",
        json={"other_task_id": other_task_id, "relation_kind": kind},
    )


@mcp.tool()
async def remove_relation(task_id: int, other_task_id: int, relation_kind: str = "related") -> dict:
    """Remove a relation between two tasks.

    The kind has to match the one the relation was created with; see
    `add_relation` for the list. `get_task` reports what a task currently has.
    """
    # The path holds all three values, and the API documents a body as required
    # here as well. Both are sent, built from the same arguments so they cannot
    # disagree with each other.
    kind = _checked_kind(relation_kind)
    return await _request(
        "DELETE",
        f"/tasks/{task_id}/relations/{kind}/{other_task_id}",
        json={"other_task_id": other_task_id, "relation_kind": kind},
    )
