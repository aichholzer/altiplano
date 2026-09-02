"""Tests for the guidance the server ships.

`INSTRUCTIONS` travels in the handshake. `GUIDE` is fetched as the
`altiplano_guide` prompt.

Both name tools in prose. Writing every reference as `list_buckets()` makes them
extractable, and `test_every_tool_reference_resolves` checks them against the
registry.
"""

import re

from altiplano.app import INSTRUCTIONS
from altiplano.prompts import GUIDE, altiplano_guide
from altiplano.server import mcp

# A backtick, an identifier, an opening parenthesis. Only tool references match.
TOOL_REFERENCE = re.compile(r"`([a-z_]+)\(")


def references_in(text: str, floor: int) -> set[str]:
    """Tool names referenced in `text`.

    `floor` guards the extraction: a regex that stopped matching would turn every
    assertion built on this into a pass over an empty set.
    """
    found = set(TOOL_REFERENCE.findall(text))
    assert len(found) >= floor, f"expected at least {floor} tool references, found {len(found)}"
    return found


def test_the_prompt_registers_with_a_description(run):
    prompts = {p.name: p for p in run(mcp.list_prompts())}
    assert "altiplano_guide" in prompts

    prompt = prompts["altiplano_guide"]
    assert prompt.title == "Using Altiplano"
    assert prompt.description

    # Every `prompts/list` response includes the description.
    assert len(prompt.description) < 500


def test_the_prompt_takes_no_required_arguments(run):
    """A client with no way to collect arguments still has to be able to call it."""
    prompt = next(p for p in run(mcp.list_prompts()) if p.name == "altiplano_guide")
    required = [a.name for a in (prompt.arguments or []) if a.required]
    assert required == []


def test_getting_the_prompt_returns_the_guidance(run):
    result = run(mcp.get_prompt("altiplano_guide", {}))
    assert len(result.messages) == 1

    message = result.messages[0]
    assert message.role == "user"
    assert message.content.text == GUIDE


def test_the_function_returns_the_guidance():
    assert altiplano_guide() == GUIDE


def test_the_handshake_includes_the_instructions():
    options = mcp._lowlevel_server.create_initialization_options()
    assert options.instructions == INSTRUCTIONS


def test_the_instructions_stay_short():
    assert len(INSTRUCTIONS) < 1500
    assert len(INSTRUCTIONS) < len(GUIDE) / 4


def test_the_instructions_point_at_the_prompt():
    """Without the name, an agent cannot reach the rest of the guidance."""
    assert "altiplano_guide" in INSTRUCTIONS


def test_every_tool_reference_resolves(run):
    registered = {tool.name for tool in run(mcp.list_tools())}
    referenced = references_in(GUIDE, 20) | references_in(INSTRUCTIONS, 6)

    assert referenced <= registered, (
        f"the guidance names tools that are not registered: {sorted(referenced - registered)}"
    )
