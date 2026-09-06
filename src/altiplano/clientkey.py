"""`altiplano-clientkey`: mint, list, and revoke the tokens HTTP clients present.

Three subcommands, run on the machine hosting the server:

    altiplano-clientkey add <label>
    altiplano-clientkey list
    altiplano-clientkey revoke <label>

Each client needs a Vikunja API token of its own, and `add` asks for it. That token is
what the client's requests are made with. The client therefore acts as the Vikunja user
who created it. Give one person's token to one client label.

`add` prints the client token once. Altiplano keeps only its SHA-256. A client token
that is lost is replaced by revoking the label and adding it again.

The Vikunja token is read from a hidden prompt, or from stdin when the input is
piped. It never appears as an argument, where `ps` would show it to every user on the
host.

Three reasons this is a console script and not a shell script. The config directory
resolution lives in `config.py`, and a shell copy would drift from it. `sha256sum`
and `shasum` take different arguments on Linux and macOS. And a Python entry point
arrives with the package, on whichever host runs the server.
"""

import argparse
import getpass
import sys

from altiplano import __version__
from altiplano.clients import (
    _CLIENTS_FILE,
    _add,
    _clients,
    _LockUnavailable,
    _remove,
    _StoreUnreadable,
)


def _read_vikunja_token(label: str) -> str:
    """The Vikunja API token this client acts with, off the terminal or off stdin.

    `getpass` keeps it off the screen on a terminal. A piped stdin makes the command
    scriptable, which is how a configuration-management run would call it.
    """
    if sys.stdin.isatty():
        return getpass.getpass(f"Vikunja API token for {label}: ").strip()
    return sys.stdin.readline().strip()


def _add_command(label: str) -> int:
    vikunja_token = _read_vikunja_token(label)
    token = _add(label, vikunja_token)
    print(f"client:  {label}")
    print(f"token:   {token}")
    print(f"stored:  {_CLIENTS_FILE}")
    print()
    print("Shown once. Altiplano stores only the SHA-256 of this token.")
    print("Give it to the client as: Authorization: Bearer <token>")
    print()
    print(f"Requests from {label} will act as the Vikunja user whose token you gave.")
    return 0


def _list_command() -> int:
    registered = _clients()
    if not registered:
        print(f"no clients registered in {_CLIENTS_FILE}")
        return 0
    width = max(len(client.label) for client in registered)
    print(f"{'LABEL'.ljust(width)}  VIKUNJA  CREATED")
    for client in registered:
        vikunja = "yes    " if client.vikunja_token else "MISSING"
        print(f"{client.label.ljust(width)}  {vikunja}  {client.created or 'unknown'}")
    if any(not client.vikunja_token for client in registered):
        print()
        print("A client marked MISSING is refused on every request. Re-add it.")
    return 0


def _revoke_command(label: str) -> int:
    if not _remove(label):
        print(f"no client named {label!r} in {_CLIENTS_FILE}", file=sys.stderr)
        return 1
    print(f"revoked {label}. It takes effect on the next request, with no restart.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="altiplano-clientkey",
        description="Manage the tokens Altiplano's HTTP transport accepts.",
    )
    parser.add_argument("--version", action="version", version=f"altiplano {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser(
        "add",
        help="mint a token for a new client",
        description=(
            "Mint a client token and record the Vikunja API token the client acts "
            "with. The Vikunja token is read from a hidden prompt, or from stdin "
            "when the input is piped."
        ),
    )
    add.add_argument("label", help="a name for the client, for example stefan-laptop")

    commands.add_parser("list", help="list registered clients")

    revoke = commands.add_parser("revoke", help="revoke a client's token")
    revoke.add_argument("label", help="the client to revoke")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        match args.command:
            case "add":
                return _add_command(args.label)
            case "revoke":
                return _revoke_command(args.label)
            case _:
                return _list_command()
    except (ValueError, OSError, _StoreUnreadable, _LockUnavailable) as err:
        print(f"altiplano-clientkey: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
