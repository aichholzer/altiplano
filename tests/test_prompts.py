"""Tests for the guidance prompt.

The one that earns its keep is `test_every_tool_reference_resolves`. The guidance
names tools in prose, so renaming or removing a tool leaves the text describing an
API that no longer exists, and nothing else in the suite notices. Writing every
reference as `list_buckets()` makes them extractable, and parameter names never
take parentheses, so there is no allowlist to keep in step.
"""

import re

from altiplano.prompts import GUIDE, altiplano_guide
from altiplano.server import mcp

# A backtick, an identifier, then an opening parenthesis. `GUIDE` writes tool
# references and nothing else that way.
TOOL_REFERENCE = re.compile(r"`([a-z_]+)\(")


def test_the_prompt_registers_with_a_description(run):
    prompts = {p.name: p for p in run(mcp.list_prompts())}
    assert "altiplano_guide" in prompts

    prompt = prompts["altiplano_guide"]
    assert prompt.title == "Using Altiplano"
    assert prompt.description

    # Every `prompts/list` response carries the description, so it stays a summary.
    # Passing `GUIDE` as the description would put the whole document in a listing.
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
    """Covers the body directly, since `get_prompt` reaches it through the SDK."""
    assert altiplano_guide() == GUIDE


def test_every_tool_reference_resolves(run):
    registered = {tool.name for tool in run(mcp.list_tools())}
    referenced = set(TOOL_REFERENCE.findall(GUIDE))

    # Guards the extraction itself: a regex that stopped matching would otherwise
    # make the assertion below pass on an empty set.
    assert len(referenced) >= 20

    assert referenced <= registered, (
        f"the guidance names tools that are not registered: {sorted(referenced - registered)}"
    )
