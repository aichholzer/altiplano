#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=2.0.0,<3", "httpx2"]
# ///
"""Check a deployed Altiplano HTTP endpoint from a client machine.

Two people sharing one endpoint have to reach their own Vikunja data and nobody else's.
The test suite covers the token store, the gate and the transport, and it cannot cover a
hostname, a tunnel, or two real Vikunja accounts. This does.

Run it from a machine that is not the host, against the endpoint clients actually use.
Pointing it at `127.0.0.1` on the host exercises a `Host` value the allowlist accepts by
default, and a misconfigured allowlist would go unnoticed.

    export ALTIPLANO_TOKEN_A=altp_...
    export ALTIPLANO_TOKEN_B=altp_...
    ./scripts/acceptance.py https://altiplano.example.com/mcp

The tokens are read from the environment, or prompted for. Neither is ever an argument:
`ps` shows a command line to every user on the machine.

### The default run

Transport and session checks only. Nothing is created.

### --write

Every tool the server exposes, once per account, with a nonce in every payload so any
object can be traced back to the account that made it. The tour runs twice: both
accounts concurrently, then one after the other. Concurrency is the point, since
sequential calls cannot show that overlapping traffic keeps its identity apart.

After each run, three cross-contamination checks. `search_tasks` for the other account's
nonce sweeps every project that token can see and must return nothing. Direct reads of
the other account's objects by id must be refused. And every object an account created
must carry its own nonce and no other.

Everything is deleted afterwards, in reverse order, whatever happened. One exception is
unavoidable and reported. Altiplano exposes no `delete_project`, and the project each
tour creates is left behind by name.

Use test accounts for `--write`.

    ./scripts/acceptance.py https://altiplano.example.com/mcp --write

Exits non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import secrets
import sys
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

PROTOCOL = "2025-06-18"

# Every tool the server exposes. The tour asserts it called all of them. A tool added to
# Altiplano with no step here is named in the coverage check at the end.
EXPECTED_TOOLS = {
    "list_projects", "create_project",
    "list_tasks", "get_task", "create_task", "update_task", "set_reminders", "delete_task",
    "search_tasks", "move_task", "duplicate_task", "bulk_create_tasks", "bulk_update_tasks",
    "list_labels", "create_label", "delete_label", "add_label", "remove_label",
    "list_comments", "add_comment", "update_comment", "delete_comment",
    "list_kanban_views", "list_buckets", "create_bucket", "delete_bucket",
    "list_bucket_tasks", "list_task_buckets", "move_task_to_bucket",
    "add_relation", "remove_relation",
    "search_users", "list_assignees", "add_assignee", "remove_assignee",
}  # fmt: skip


# --- reporting ---------------------------------------------------------------
@dataclass
class Report:
    checks: list[tuple[bool, str, str]] = field(default_factory=list)

    def record(self, passed: bool, name: str, detail: str = "") -> bool:
        self.checks.append((passed, name, detail))
        print(f"{'PASS' if passed else 'FAIL'}  {name}", flush=True)
        if detail:
            for line in detail.splitlines():
                print(f"      {line}", flush=True)
        return passed

    def note(self, text: str) -> None:
        print(f"      {text}", flush=True)

    def heading(self, text: str) -> None:
        print()
        print(text, flush=True)

    @property
    def failures(self) -> int:
        return sum(1 for passed, _, _ in self.checks if not passed)

    def summarise(self) -> int:
        print()
        total = len(self.checks)
        if self.failures:
            print(f"{self.failures} of {total} checks failed.")
            for passed, name, _ in self.checks:
                if not passed:
                    print(f"  FAIL  {name}")
            return 1
        print(f"All {total} checks passed.")
        return 0


# --- talking to the endpoint --------------------------------------------------
def _headers(token: str, session: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL,
    }
    if session:
        headers["mcp-session-id"] = session
    return headers


async def _raw_initialise(http: httpx2.AsyncClient, url: str, token: str, session=None):
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "altiplano-acceptance", "version": "1"},
        },
    }
    return await http.post(url, headers=_headers(token, session), json=body)


class ToolFailed(RuntimeError):
    """The server reported an error for a tool call."""


class Client:
    """One MCP session, plus a record of which tools it has called."""

    def __init__(self, name: str, url: str, token: str) -> None:
        self.name, self.url, self.token = name, url, token
        self._stack: list = []
        self.session: ClientSession | None = None
        self.tools: list = []
        self.called: set[str] = set()
        self.acted_as: str | None = None

    async def open(self) -> None:
        http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {self.token}"}, timeout=60)
        await http.__aenter__()
        transport = streamable_http_client(self.url, http_client=http)
        read, write, *_ = await transport.__aenter__()
        session = ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()
        self.tools = (await session.list_tools()).tools
        self.session = session
        self._stack = [session, transport, http]

    async def close(self) -> None:
        # Teardown is deliberately quiet. A failure closing the session would mask the
        # check result, which is the only thing the operator ran this for.
        for entered in self._stack:
            with suppress(Exception):
                await entered.__aexit__(None, None, None)
        self._stack = []

    async def call(self, tool: str, arguments: dict):
        """A tool result as Python, or a raised ToolFailed carrying the server's text.

        The SDK returns one content block per item in a collection. A decoded result is
        therefore a list when there was more than one block, and `call_list` normalises
        that for the callers which always want a collection.
        """
        self.called.add(tool)
        result = await self.session.call_tool(tool, arguments)
        texts = [block.text for block in result.content if hasattr(block, "text")]
        # The attribute is spelled both ways across SDK versions.
        if getattr(result, "isError", None) or getattr(result, "is_error", None):
            raise ToolFailed("\n".join(texts) or f"{tool} failed with no message")

        decoded = []
        for text in texts:
            try:
                decoded.append(json.loads(text))
            except ValueError:
                decoded.append(text)
        if not decoded:
            return None
        return decoded[0] if len(decoded) == 1 else decoded

    async def call_list(self, tool: str, arguments: dict) -> list:
        value = await self.call(tool, arguments)
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    async def refused(self, tool: str, arguments: dict) -> bool:
        """Whether the server refuses this call. Used for the cross-account reads."""
        try:
            await self.call(tool, arguments)
        except ToolFailed:
            return True
        return False


# --- the transport, before any session ----------------------------------------
async def check_transport(report: Report, url: str, token_a: str) -> None:
    report.heading("Transport")
    async with httpx2.AsyncClient(timeout=30) as http:
        anonymous = await http.post(
            url,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
        report.record(
            anonymous.status_code == 401,
            "a request with no token is refused with 401",
            f"got {anonymous.status_code}",
        )

        first = await _raw_initialise(http, url, token_a)
        report.record(
            first.status_code == 200, "the endpoint answers initialize", f"got {first.status_code}"
        )
        report.record(
            "mcp-session-id" not in first.headers,
            "no mcp-session-id is issued. The build is stateless",
            "a session id here means the host is running a build from before the fix",
        )

        borrowed = await _raw_initialise(http, url, token_a, session="acceptance-fabricated")
        report.record(
            borrowed.status_code == 200,
            "a fabricated session id changes nothing",
            f"got {borrowed.status_code}",
        )


# --- one account's tour through every tool ------------------------------------
@dataclass
class Made:
    """What one tour created, in creation order. Cleanup walks it backwards."""

    nonce: str
    project_id: int | None = None
    project_title: str | None = None
    inbox_id: int | None = None
    tasks: list[int] = field(default_factory=list)
    labels: list[int] = field(default_factory=list)
    buckets: list[int] = field(default_factory=list)
    comment: tuple[int, int] | None = None


def _writable(projects: list) -> dict | None:
    """The first project that can hold a task.

    Vikunja reports saved filters alongside real projects and gives them negative ids.
    `My Open Tasks` is one, and creating a task in it fails. Position in the list is no
    guide either, which leaves the id as the only thing to go on.
    """
    for project in projects:
        if isinstance(project, dict) and isinstance(project.get("id"), int) and project["id"] > 0:
            return project
    return None


async def tour(report: Report, client: Client, nonce: str) -> Made:
    """Call every tool once, tagging everything with `nonce`.

    Raises nothing. Each step records its own result. One failure leaves the rest of the
    tour to run, and cleanup keeps a full record of what exists.
    """
    made = Made(nonce=nonce)
    tag = f"[{nonce}]"

    def step(passed: bool, what: str, detail: str = "") -> bool:
        return report.record(passed, f"{client.name} {what}", detail)

    # --- projects
    try:
        existing = await client.call_list("list_projects", {})
        inbox = _writable(existing)
        made.inbox_id = inbox["id"] if inbox else None
        step(bool(inbox), "lists projects and has one that can hold tasks",
             f"{len(existing)} visible, writable {made.inbox_id}")
        project = await client.call(
            "create_project", {"title": f"{tag} acceptance", "description": f"{tag} throwaway"}
        )
        made.project_id, made.project_title = project["id"], project.get("title")
        step(True, "creates a project", f"project {made.project_id}")
    except ToolFailed as err:
        step(False, "sets up its projects", str(err))
        return made

    target = made.project_id

    # --- tasks
    try:
        task = await client.call("create_task", {"project_id": target, "title": f"{tag} first"})
        made.tasks.append(task["id"])
        step(True, "creates a task", f"task {task['id']}")

        full = await client.call("get_task", {"task_id": task["id"]})
        client.acted_as = (full.get("created_by") or {}).get("username")
        step(
            bool(client.acted_as) and tag in (full.get("title") or ""),
            "reads its task back, and it names the acting account",
            f"created_by {client.acted_as}, title {full.get('title')!r}",
        )

        await client.call(
            "update_task", {"task_id": task["id"], "description": f"{tag} described", "priority": 3}
        )
        updated = await client.call("get_task", {"task_id": task["id"]})
        step(updated.get("priority") == 3, "updates a task", "priority 3")

        soon = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        await client.call("set_reminders", {"task_id": task["id"], "reminders": [soon]})
        step(True, "sets a reminder", soon)

        listed = await client.call_list("list_tasks", {"project_id": target})
        step(any(t.get("id") == task["id"] for t in listed), "lists tasks in its project")

        found = await client.call_list("search_tasks", {"query": nonce})
        step(bool(found), "finds its own task by nonce", f"{len(found)} match")

        second = await client.call("create_task", {"project_id": target, "title": f"{tag} second"})
        made.tasks.append(second["id"])
        batch = await client.call_list(
            "bulk_create_tasks",
            {"project_id": target, "tasks": [{"title": f"{tag} bulk 1"}, {"title": f"{tag} bulk 2"}]},
        )
        made.tasks.extend(b["id"] for b in batch if isinstance(b, dict) and "id" in b)
        step(len(batch) == 2, "creates a batch of tasks", f"{len(batch)} created")

        await client.call("bulk_update_tasks", {"task_ids": [t["id"] for t in [task, second]],
                                               "priority": 2})
        step(True, "updates tasks in bulk")

        # The id has to come back, or the copy cannot be cleaned up and the tour
        # leaves a task behind. Vikunja wraps this response and the tool unwraps it.
        copy = await client.call("duplicate_task", {"task_id": task["id"]})
        copy_id = copy.get("id") if isinstance(copy, dict) else None
        if copy_id:
            made.tasks.append(copy_id)
        step(bool(copy_id), "duplicates a task and names the copy", f"copy {copy_id}")

        if made.inbox_id and made.inbox_id != target:
            await client.call("move_task", {"task_id": second["id"], "project_id": made.inbox_id})
            await client.call("move_task", {"task_id": second["id"], "project_id": target})
            step(True, "moves a task between projects and back")
        else:
            step(False, "has a second project to move a task to")
    except ToolFailed as err:
        step(False, "completes the task tour", str(err))
        return made

    # --- relations, labels, comments, assignees
    try:
        await client.call(
            "add_relation",
            {"task_id": made.tasks[0], "other_task_id": made.tasks[1], "relation_kind": "related"},
        )
        related = await client.call("get_task", {"task_id": made.tasks[0]})
        step(bool(related.get("related_tasks")), "relates two tasks")
        await client.call(
            "remove_relation",
            {"task_id": made.tasks[0], "other_task_id": made.tasks[1], "relation_kind": "related"},
        )
        step(True, "removes the relation")

        label = await client.call("create_label", {"title": f"{tag}-label", "hex_color": "4287f5"})
        made.labels.append(label["id"])
        await client.call("add_label", {"task_id": made.tasks[0], "label_id": label["id"]})
        labelled = await client.call("get_task", {"task_id": made.tasks[0]})
        step(bool(labelled.get("labels")), "creates a label and attaches it", f"label {label['id']}")
        all_labels = await client.call_list("list_labels", {})
        step(any(item.get("id") == label["id"] for item in all_labels), "lists its labels")
        await client.call("remove_label", {"task_id": made.tasks[0], "label_id": label["id"]})
        step(True, "detaches the label")

        comment = await client.call(
            "add_comment", {"task_id": made.tasks[0], "comment": f"{tag} first comment"}
        )
        made.comment = (made.tasks[0], comment["id"])
        await client.call(
            "update_comment",
            {"task_id": made.tasks[0], "comment_id": comment["id"], "comment": f"{tag} edited"},
        )
        comments = await client.call_list("list_comments", {"task_id": made.tasks[0]})
        step(
            any(tag in str(c.get("comment", "")) for c in comments),
            "comments on its task, edits it, and reads it back",
            f"{len(comments)} comment(s)",
        )

        users = await client.call_list("search_users", {"query": client.acted_as or "a"})
        mine = next((u for u in users if u.get("username") == client.acted_as), None)
        step(bool(mine), "finds itself with search_users", f"{len(users)} match")
        if mine:
            await client.call("add_assignee", {"task_id": made.tasks[0], "user_id": mine["id"]})
            assignees = await client.call_list("list_assignees", {"task_id": made.tasks[0]})
            step(
                any(u.get("username") == client.acted_as for u in assignees),
                "assigns itself and lists assignees",
            )
            await client.call("remove_assignee", {"task_id": made.tasks[0], "user_id": mine["id"]})
            step(True, "unassigns itself")
    except ToolFailed as err:
        step(False, "completes the relations, labels and comments tour", str(err))

    # --- kanban
    try:
        views = await client.call_list("list_kanban_views", {"project_id": target})
        step(bool(views), "lists kanban views", f"{len(views)} view(s)")
        buckets = await client.call_list("list_buckets", {"project_id": target})
        step(bool(buckets), "lists buckets", f"{len(buckets)} bucket(s)")
        bucket = await client.call("create_bucket", {"project_id": target, "title": f"{tag} column"})
        made.buckets.append(bucket["id"])
        step(True, "creates a bucket", f"bucket {bucket['id']}")
        await client.call("move_task_to_bucket", {"task_id": made.tasks[0],
                                                 "bucket_id": bucket["id"]})
        placed = await client.call_list("list_task_buckets", {"task_id": made.tasks[0]})
        step(
            any(entry.get("bucket_id") == bucket["id"] for entry in placed),
            "moves a task into its bucket and reads its placement back",
        )
        with_tasks = await client.call_list("list_bucket_tasks", {"project_id": target})
        step(bool(with_tasks), "lists buckets with their tasks", f"{len(with_tasks)} bucket(s)")
    except ToolFailed as err:
        step(False, "completes the kanban tour", str(err))

    return made


# --- cross-contamination ------------------------------------------------------
async def cross_checks(report: Report, a: Client, b: Client, made_a: Made, made_b: Made) -> None:
    report.heading("Isolation between the two accounts")

    report.record(
        a.acted_as is not None and a.acted_as != b.acted_as,
        "the two clients act as two different Vikunja accounts",
        f"{a.name} as {a.acted_as}, {b.name} as {b.acted_as}",
    )

    # The sweep. `search_tasks` covers every project the token can see. A foreign nonce
    # anywhere in the account surfaces here.
    for reader, other in ((a, made_b), (b, made_a)):
        try:
            hits = await reader.call_list("search_tasks", {"query": other.nonce})
        except ToolFailed:
            hits = []
        report.record(
            not hits,
            f"{reader.name} finds nothing anywhere carrying the other account's nonce",
            f"searched {other.nonce}, {len(hits)} hit(s)",
        )

    # Direct reads of the other account's objects, by id.
    for reader, other, owner in ((a, made_b, b), (b, made_a, a)):
        if other.tasks:
            report.record(
                await reader.refused("get_task", {"task_id": other.tasks[0]}),
                f"{reader.name} cannot read {owner.name}'s task",
                f"task {other.tasks[0]}",
            )
            report.record(
                await reader.refused("list_comments", {"task_id": other.tasks[0]}),
                f"{reader.name} cannot read comments on {owner.name}'s task",
            )
        if other.project_id:
            report.record(
                await reader.refused("list_tasks", {"project_id": other.project_id}),
                f"{reader.name} cannot list tasks in {owner.name}'s project",
                f"project {other.project_id}",
            )

    # Every task an account can see must carry its own nonce and no other.
    for reader, own, other in ((a, made_a, made_b), (b, made_b, made_a)):
        try:
            visible = await reader.call_list("search_tasks", {"query": own.nonce})
        except ToolFailed:
            visible = []
        foreign = [t for t in visible if other.nonce in json.dumps(t)]
        report.record(
            bool(visible) and not foreign,
            f"everything {reader.name} sees under its own nonce is its own",
            f"{len(visible)} task(s), {len(foreign)} carrying the other nonce",
        )


def check_coverage(report: Report, clients: list[Client]) -> None:
    report.heading("Tool coverage")
    for client in clients:
        missed = EXPECTED_TOOLS - client.called
        report.record(
            not missed,
            f"{client.name} called every tool",
            f"{len(client.called)} of {len(EXPECTED_TOOLS)}"
            + (f", missed: {', '.join(sorted(missed))}" if missed else ""),
        )
    for client in clients:
        unknown = {tool.name for tool in client.tools} - EXPECTED_TOOLS
        if unknown:
            report.record(
                False,
                f"{client.name} sees tools this script does not exercise",
                ", ".join(sorted(unknown)),
            )


# --- cleanup ------------------------------------------------------------------
async def clean_up(report: Report, client: Client, made: Made) -> None:
    """Reverse everything the tour created, in reverse order. Reports what it cannot."""
    if made.comment:
        task_id, comment_id = made.comment
        with suppress(Exception):
            await client.call("delete_comment", {"task_id": task_id, "comment_id": comment_id})
    for bucket_id in reversed(made.buckets):
        try:
            await client.call(
                "delete_bucket", {"project_id": made.project_id, "bucket_id": bucket_id}
            )
        except Exception as err:  # noqa: BLE001 - a cleanup failure is a result, not a crash
            report.record(False, f"{client.name} deletes bucket {bucket_id}", str(err))
    for label_id in reversed(made.labels):
        try:
            await client.call("delete_label", {"label_id": label_id})
        except Exception as err:  # noqa: BLE001
            report.record(False, f"{client.name} deletes label {label_id}", str(err))
    for task_id in reversed(made.tasks):
        try:
            await client.call("delete_task", {"task_id": task_id})
        except Exception as err:  # noqa: BLE001
            report.record(False, f"{client.name} deletes task {task_id}", str(err))


async def check_nothing_remains(report: Report, client: Client, made: Made) -> None:
    try:
        left = await client.call_list("search_tasks", {"query": made.nonce})
    except ToolFailed:
        left = []
    report.record(
        not left,
        f"{client.name} leaves no task behind",
        f"searched {made.nonce}, {len(left)} remaining",
    )
    if made.project_id:
        report.note(
            f"project {made.project_id} ({made.project_title!r}) is left behind: "
            "Altiplano exposes no delete_project. Remove it in Vikunja."
        )


# --- the tours ----------------------------------------------------------------
async def run_pass(report: Report, a: Client, b: Client, label: str, concurrent: bool) -> None:
    nonce_a = f"acc-{label}-a-{secrets.token_hex(4)}"
    nonce_b = f"acc-{label}-b-{secrets.token_hex(4)}"
    report.heading(f"=== {label} pass: every tool, {'concurrently' if concurrent else 'serially'}")
    report.note(f"nonces: {nonce_a} and {nonce_b}")

    if concurrent:
        made_a, made_b = await asyncio.gather(
            tour(report, a, nonce_a), tour(report, b, nonce_b)
        )
    else:
        made_a = await tour(report, a, nonce_a)
        made_b = await tour(report, b, nonce_b)

    try:
        await cross_checks(report, a, b, made_a, made_b)
    finally:
        report.heading("Cleanup")
        if concurrent:
            await asyncio.gather(clean_up(report, a, made_a), clean_up(report, b, made_b))
        else:
            await clean_up(report, a, made_a)
            await clean_up(report, b, made_b)
        await check_nothing_remains(report, a, made_a)
        await check_nothing_remains(report, b, made_b)


async def run(url: str, token_a: str, token_b: str, write: bool) -> int:
    report = Report()
    print(f"Endpoint: {url}")

    await check_transport(report, url, token_a)

    a, b = Client("client A", url, token_a), Client("client B", url, token_b)
    try:
        report.heading("Sessions")
        await asyncio.gather(a.open(), b.open())
        report.record(True, "both clients complete an MCP session")
        names_a = {tool.name for tool in a.tools}
        names_b = {tool.name for tool in b.tools}
        report.record(bool(names_a) and names_a == names_b, "both clients see the same tool set",
                      f"{len(names_a)} tools each")

        if not write:
            report.heading("Identity, read only")
            ids_a = {p["id"] for p in await a.call_list("list_projects", {})}
            ids_b = {p["id"] for p in await b.call_list("list_projects", {})}
            report.record(
                ids_a != ids_b,
                "the two clients see different projects",
                f"{a.name} ids {sorted(ids_a)}\n{b.name} ids {sorted(ids_b)}\n"
                "identical sets would mean both tokens reach one Vikunja account",
            )
            print()
            print("Skipping the tour. Add --write to exercise every tool.")
            return report.summarise()

        # Concurrency first. A defect in request-scoped credentials shows up there and
        # not in the serial pass.
        await run_pass(report, a, b, "concurrent", concurrent=True)
        await run_pass(report, a, b, "serial", concurrent=False)
        check_coverage(report, [a, b])
    finally:
        await a.close()
        await b.close()

    return report.summarise()


def _token(name: str, label: str) -> str:
    token = os.environ.get(name)
    if token:
        return token.strip()
    return getpass.getpass(f"Altiplano client token for {label}: ").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check a deployed Altiplano HTTP endpoint. Tokens come from "
            "ALTIPLANO_TOKEN_A and ALTIPLANO_TOKEN_B, or a prompt."
        ),
        epilog="Run this from a client machine, against the hostname clients use.",
    )
    parser.add_argument("url", help="the MCP endpoint, for example https://host.example.com/mcp")
    parser.add_argument(
        "--write",
        action="store_true",
        help="exercise every tool for both accounts, concurrently then serially",
    )
    args = parser.parse_args(argv)

    token_a = _token("ALTIPLANO_TOKEN_A", "client A")
    token_b = _token("ALTIPLANO_TOKEN_B", "client B")
    if not token_a or not token_b:
        print("acceptance: two client tokens are required", file=sys.stderr)
        return 2
    if token_a == token_b:
        print(
            "acceptance: both tokens are the same. This check needs two clients "
            "registered against two Vikunja accounts.",
            file=sys.stderr,
        )
        return 2

    return asyncio.run(run(args.url, token_a, token_b, args.write))


if __name__ == "__main__":
    raise SystemExit(main())
