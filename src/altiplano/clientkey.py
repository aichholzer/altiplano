"""`altiplano-clientkey`: mint, list, and revoke the tokens HTTP clients present.

Three subcommands, run on the machine hosting the server:

    altiplano-clientkey add <label>
    altiplano-clientkey list
    altiplano-clientkey revoke <label>

`add` prints the token once. Altiplano keeps only its SHA-256. A token that is lost
is replaced by revoking the label and adding it again.

Three reasons this is a console script and not a shell script. The config directory
resolution lives in `config.py`, and a shell copy would drift from it. `sha256sum`
and `shasum` take different arguments on Linux and macOS. And a Python entry point
arrives with the package, on whichever host runs the server.
"""

import argparse
import sys

from altiplano.clients import _CLIENTS_FILE, _add, _clients, _remove


def _add_command(label: str) -> int:
    token = _add(label)
    print(f"client:  {label}")
    print(f"token:   {token}")
    print(f"stored:  {_CLIENTS_FILE}")
    print()
    print("Shown once. Altiplano stores only the SHA-256 of this token.")
    print("Give it to the client as: Authorization: Bearer <token>")
    return 0


def _list_command() -> int:
    registered = _clients()
    if not registered:
        print(f"no clients registered in {_CLIENTS_FILE}")
        return 0
    width = max(len(client.label) for client in registered)
    print(f"{'LABEL'.ljust(width)}  CREATED")
    for client in registered:
        print(f"{client.label.ljust(width)}  {client.created or 'unknown'}")
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
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="mint a token for a new client")
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
    except (ValueError, OSError) as err:
        print(f"altiplano-clientkey: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
