#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=2.0.0,<3", "httpx2"]
# ///
"""Check a deployed Altiplano HTTP endpoint from a client machine.

Two people sharing one endpoint have to reach their own Vikunja data. The test suite
covers the token store, the gate and the transport, and it cannot cover a hostname, a
tunnel, or two real Vikunja accounts. This does.

Run it from a machine that is not the host, against the endpoint clients actually use.
Pointing it at `127.0.0.1` on the host exercises a `Host` value the allowlist accepts by
default, and a misconfigured allowlist would go unnoticed.

    export ALTIPLANO_TOKEN_A=altp_...
    export ALTIPLANO_TOKEN_B=altp_...
    ./scripts/acceptance.py https://altiplano.example.com/mcp

The tokens are read from the environment, or prompted for. Neither is ever an argument:
`ps` shows a command line to every user on the machine.

The default run reads and writes nothing. `--write` adds the conclusive check, which
creates one task per user and deletes both afterwards. Use test accounts for it.

    ./scripts/acceptance.py https://altiplano.example.com/mcp --write

Exits non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from contextlib import suppress
from dataclasses import dataclass, field

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

PROTOCOL = "2025-06-18"
MARKER = "altiplano acceptance check, safe to delete"


# --- reporting ---------------------------------------------------------------
@dataclass
class Report:
    checks: list[tuple[bool, str, str]] = field(default_factory=list)

    def record(self, passed: bool, name: str, detail: str = "") -> bool:
        self.checks.append((passed, name, detail))
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
        if detail:
            for line in detail.splitlines():
                print(f"      {line}")
        return passed

    def note(self, text: str) -> None:
        print(f"      {text}")

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


def _payload(response: httpx2.Response):
    """The JSON-RPC body, whether it arrived as JSON or as one SSE event."""
    text = response.text
    if text.startswith(("event:", "data:")) or "\ndata:" in text:
        for line in text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
    return response.json()


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


class Client:
    """One MCP session, held open for the length of a phase."""

    def __init__(self, name: str, url: str, token: str) -> None:
        self.name, self.url, self.token = name, url, token
        self._stack: list = []
        self.session: ClientSession | None = None
        self.tools: list = []

    async def open(self) -> None:
        http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {self.token}"}, timeout=30)
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
        """A tool result as Python, or a raised RuntimeError carrying the server's text.

        The SDK returns one content block per item in a collection. A decoded result is
        therefore a list when there was more than one block, and `call_list` normalises
        that for the callers which always want a collection.
        """
        result = await self.session.call_tool(tool, arguments)
        texts = [block.text for block in result.content if hasattr(block, "text")]
        # The attribute is spelled both ways across SDK versions.
        failed = getattr(result, "isError", None) or getattr(result, "is_error", None)
        if failed:
            raise RuntimeError("\n".join(texts) or f"{tool} failed with no message")

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
        """A tool result as a list, whichever shape the content blocks arrived in."""
        value = await self.call(tool, arguments)
        if value is None:
            return []
        return value if isinstance(value, list) else [value]


# --- the read-only phase ------------------------------------------------------
async def check_transport(report: Report, url: str, token_a: str) -> None:
    """The endpoint answers, refuses an anonymous caller, and issues no session."""
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
            first.status_code == 200,
            "the endpoint answers initialize",
            f"got {first.status_code}",
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


def check_tools(report: Report, a: Client, b: Client) -> None:
    names_a = {tool.name for tool in a.tools}
    names_b = {tool.name for tool in b.tools}
    report.record(bool(names_a), f"{a.name} discovers tools", f"{len(names_a)} tools")
    report.record(
        names_a == names_b,
        "both clients see the same tool set",
        f"{a.name} {len(names_a)}, {b.name} {len(names_b)}",
    )


async def check_projects_differ(report: Report, a: Client, b: Client) -> list[dict]:
    """Different project ids is the strongest signal available without writing."""
    projects_a = await a.call_list("list_projects", {})
    projects_b = await b.call_list("list_projects", {})
    ids_a = {p["id"] for p in projects_a}
    ids_b = {p["id"] for p in projects_b}

    report.record(
        bool(projects_a) and bool(projects_b),
        "both clients can list projects",
        f"{a.name} {len(ids_a)}, {b.name} {len(ids_b)}",
    )
    report.record(
        ids_a != ids_b,
        "the two clients see different projects",
        f"{a.name} ids {sorted(ids_a)}\n{b.name} ids {sorted(ids_b)}\n"
        "identical sets would mean both tokens reach one Vikunja account",
    )
    if ids_a & ids_b:
        report.note(f"shared with both: {sorted(ids_a & ids_b)}. Expected only if shared in Vikunja.")
    return [projects_a, projects_b]


# --- the write phase ----------------------------------------------------------
async def check_identity_by_writing(report: Report, a: Client, b: Client, projects) -> None:
    """The conclusive check. A task each client creates names the identity that made it.

    `get_task` returns the untrimmed response, and `created_by` on a task this script
    has just created through the endpoint is the account the endpoint acted as.
    """
    projects_a, projects_b = projects
    created: list[tuple[Client, int]] = []
    try:
        for client, own in ((a, projects_a), (b, projects_b)):
            if not own:
                report.record(False, f"{client.name} has a project to write to")
                continue
            task = await client.call(
                "create_task",
                {"project_id": own[0]["id"], "title": MARKER},
            )
            created.append((client, task["id"]))
            report.record(
                True,
                f"{client.name} creates a task in its own project",
                f"task {task['id']} in project {own[0]['id']}",
            )

        for client, task_id in created:
            full = await client.call("get_task", {"task_id": task_id})
            who = (full.get("created_by") or {}).get("username", "unknown")
            report.record(
                who != "unknown",
                f"{client.name} acts as a named Vikunja account",
                f"created_by: {who}",
            )
            client.acted_as = who

        if len(created) == 2:
            (client_a, id_a), (client_b, id_b) = created
            report.record(
                getattr(client_a, "acted_as", None) != getattr(client_b, "acted_as", None),
                "the two clients act as two different Vikunja accounts",
                f"{client_a.name} as {getattr(client_a, 'acted_as', '?')}, "
                f"{client_b.name} as {getattr(client_b, 'acted_as', '?')}",
            )
            for reader, other_id, owner in ((client_a, id_b, client_b), (client_b, id_a, client_a)):
                try:
                    await reader.call("get_task", {"task_id": other_id})
                    refused = False
                except RuntimeError:
                    refused = True
                report.record(
                    refused,
                    f"{reader.name} cannot read {owner.name}'s task",
                    f"task {other_id}",
                )
    finally:
        for client, task_id in created:
            try:
                await client.call("delete_task", {"task_id": task_id})
                report.note(f"cleaned up task {task_id}")
            # Broad on purpose. Cleanup reports its own failure and leaves the check
            # results intact. The operator then sees both.
            except Exception as err:  # noqa: BLE001
                report.record(False, f"cleaning up task {task_id}", str(err))


# --- wiring -------------------------------------------------------------------
def _token(name: str, label: str) -> str:
    token = os.environ.get(name)
    if token:
        return token.strip()
    return getpass.getpass(f"Altiplano client token for {label}: ").strip()


async def run(url: str, token_a: str, token_b: str, write: bool) -> int:
    report = Report()
    print(f"Endpoint: {url}")
    print()

    print("Transport")
    await check_transport(report, url, token_a)

    a, b = Client("client A", url, token_a), Client("client B", url, token_b)
    try:
        print()
        print("Sessions")
        await a.open()
        await b.open()
        report.record(True, "both clients complete an MCP session")
        check_tools(report, a, b)

        print()
        print("Identity, read only")
        projects = await check_projects_differ(report, a, b)

        if write:
            print()
            print("Identity, by writing")
            await check_identity_by_writing(report, a, b, projects)
        else:
            print()
            print("Skipping the write phase. Add --write for the conclusive check.")
    finally:
        await a.close()
        await b.close()

    return report.summarise()


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
        help="create and delete one task per client, which is the conclusive check",
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
