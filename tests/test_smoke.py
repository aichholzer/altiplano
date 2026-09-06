"""Smoke tests guarding the failure modes that have actually shipped.

0.4.0 reached PyPI with an import error that broke every launch. The checks here
are deliberately shallow and cheap: the module imports, every tool registers, the
console script resolves, and the two places the version lives agree with each
other.

Two constraints shape these tests. They never call `main()`, which starts the
server and never returns. And they need no Vikunja credentials, because
`server.py` reads config inside the request helpers, well after import time.
"""

import asyncio
import json
from importlib.metadata import entry_points, version
from pathlib import Path

import altiplano
from altiplano.server import mcp

REPO_ROOT = Path(__file__).resolve().parent.parent

# The full public surface. Update this when adding or removing a tool; the
# exact-match assertion below stops a tool silently disappearing.
EXPECTED_TOOLS = {
    "list_projects",
    "create_project",
    "list_tasks",
    "get_task",
    "create_task",
    "update_task",
    "set_reminders",
    "delete_task",
    "list_labels",
    "add_label",
    "remove_label",
    "list_comments",
    "add_comment",
    "update_comment",
    "delete_comment",
    "search_tasks",
    "move_task",
    "duplicate_task",
    "bulk_create_tasks",
    "bulk_update_tasks",
    "create_label",
    "delete_label",
    "list_kanban_views",
    "list_buckets",
    "create_bucket",
    "delete_bucket",
    "list_bucket_tasks",
    "list_task_buckets",
    "move_task_to_bucket",
    "add_relation",
    "remove_relation",
    "search_users",
    "list_assignees",
    "add_assignee",
    "remove_assignee",
}


def test_every_tool_registers():
    names = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert names == EXPECTED_TOOLS


def test_console_script_resolves():
    scripts = [e for e in entry_points(group="console_scripts") if e.name == "altiplano"]
    assert len(scripts) == 1, "the altiplano console script is not installed"
    assert callable(scripts[0].load())


def test_version_is_consistent():
    # `version()` reads the built distribution metadata, which hatchling takes
    # from pyproject.toml. This catches drift between pyproject and __init__.
    assert altiplano.__version__ == version("altiplano")


def test_the_handshake_reports_the_running_version():
    """Without this, a stale `uvx` cache is invisible.

    The only other way to tell which version a client actually launched is to
    provoke a behaviour that changed between releases. That is a poor diagnostic,
    and it cost several restart cycles before this was declared.
    """
    options = mcp._lowlevel_server.create_initialization_options()
    assert options.server_name == "altiplano"
    assert options.server_version == altiplano.__version__


def test_the_glama_manifest_is_parseable_and_names_a_maintainer():
    """Glama reads `glama.json` to let a maintainer claim the server listing.

    A syntax error there is the documented reason a claim silently fails, and
    nothing else in this project ever parses the file. The schema requires
    `maintainers` to be a unique array of GitHub usernames.
    """
    manifest = json.loads((REPO_ROOT / "glama.json").read_text())
    assert manifest["$schema"] == "https://glama.ai/mcp/schemas/server.json"

    maintainers = manifest["maintainers"]
    assert isinstance(maintainers, list)
    assert maintainers, "at least one GitHub username is required"
    assert all(isinstance(name, str) and name.strip() for name in maintainers)
    assert len(set(maintainers)) == len(maintainers), "the schema requires unique entries"
