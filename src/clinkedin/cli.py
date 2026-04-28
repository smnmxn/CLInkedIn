"""CLInkedIn entry point."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from .auth import NotAuthenticatedError, login, login_from_chrome, login_with_cookie
from .client import make_client
from .connections import fetch_connections, format_json, format_table
from .invite import InviteError, parse_profile_url, send_invite
from .search import format_json as search_format_json
from .search import format_table as search_format_table
from .search import search_people


def _cmd_login(args: argparse.Namespace) -> int:
    try:
        if args.from_chrome:
            profile = login_from_chrome()
        elif args.cookie:
            profile = login_with_cookie(args.cookie)
        else:
            email = input("LinkedIn email: ").strip()
            password = getpass.getpass("LinkedIn password: ")
            profile = login(email, password)
    except Exception as e:
        cls = type(e).__name__
        if cls == "ChallengeException":
            print(
                "LinkedIn is requiring a security challenge. Try 'clinkedin login "
                "--from-chrome' instead, or sign in to linkedin.com in a browser and "
                "retry.",
                file=sys.stderr,
            )
            return 2
        print(f"Login failed: {e}", file=sys.stderr)
        return 1

    name = " ".join(
        x for x in (profile.get("firstName"), profile.get("lastName")) if x
    ) or "(unknown)"
    print(f"Logged in as {name}")
    return 0


def _cmd_connections(args: argparse.Namespace) -> int:
    try:
        client = make_client()
    except NotAuthenticatedError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        conns = fetch_connections(client, limit=args.limit)
    except Exception as e:
        print(f"Failed to fetch connections: {e}", file=sys.stderr)
        print("If this looks like a session error, run 'clinkedin login' again.", file=sys.stderr)
        return 1

    rendered = format_json(conns) if args.json else format_table(conns)
    if args.output:
        Path(args.output).write_text(rendered)
    else:
        print(rendered)
    return 0


def _cmd_search_people(args: argparse.Namespace) -> int:
    network: list[str] | None = None
    if args.network:
        network = [n.strip().upper() for n in args.network.split(",") if n.strip()]
        valid = {"F", "S", "O"}
        bad = [n for n in network if n not in valid]
        if bad:
            print(f"Invalid --network values: {bad}. Use F, S, O.", file=sys.stderr)
            return 2

    try:
        client = make_client()
    except NotAuthenticatedError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        results = search_people(client, args.query, network_depths=network, limit=args.limit)
    except Exception as e:
        print(f"Search failed: {e}", file=sys.stderr)
        print("If this looks like a session error, run 'clinkedin login' again.", file=sys.stderr)
        return 1

    rendered = search_format_json(results) if args.json else search_format_table(results)
    if args.output:
        Path(args.output).write_text(rendered)
    else:
        print(rendered)
    return 0


def _cmd_connect(args: argparse.Namespace) -> int:
    try:
        public_id = parse_profile_url(args.url)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    message = args.message or ""
    if len(message) > 300:
        print(f"Message too long ({len(message)}/300).", file=sys.stderr)
        return 2

    preview = f" with note: {message!r}" if message else " (no note)"
    print(f"Invite target: {public_id}{preview}")

    if args.dry_run:
        return 0

    if not args.yes:
        reply = input(f"Send invite to {public_id}? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Aborted.")
            return 0

    try:
        client = make_client()
    except NotAuthenticatedError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        send_invite(client, public_id, message=message)
    except InviteError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Invite failed: {e}", file=sys.stderr)
        return 1

    print(f"Invite sent to {public_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    def _fmt(prog):
        return argparse.RawDescriptionHelpFormatter(prog, max_help_position=28, width=100)

    parser = argparse.ArgumentParser(
        prog="clinkedin",
        formatter_class=_fmt,
        description=(
            "CLInkedIn — a personal CLI for your LinkedIn account.\n"
            "List your connections and send connection requests from the terminal."
        ),
        epilog=(
            "Examples:\n"
            "  clinkedin connections                     list your 1st-degree connections\n"
            "  clinkedin connections --limit 20 --json   first 20 as JSON\n"
            "  clinkedin connect https://www.linkedin.com/in/<slug>/\n"
            "  clinkedin connect https://www.linkedin.com/in/<slug>/ \\\n"
            "      --message 'Hi, met at the conference' --yes\n"
            "  clinkedin search people 'product manager fintech'\n"
            "  clinkedin search people 'designer' --network F,S --limit 50\n"
            "  clinkedin login --cookie AQEDAR...        paste li_at if Chrome isn't available\n"
            "\n"
            "First run: sign in to linkedin.com in Chrome; the CLI will reuse that session.\n"
            "Session cache: ~/.config/clinkedin/session.json (0600).\n"
            "\n"
            "Rate limits (free account): ~5 invites with notes/week, ~100–200 invites/week total.\n"
            "Run 'clinkedin <command> --help' for details on each command.\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", title="commands")

    lg = sub.add_parser("login", help="Authenticate and cache a session cookie")
    auth_group = lg.add_mutually_exclusive_group()
    auth_group.add_argument(
        "--from-chrome",
        action="store_true",
        help="Pull the li_at cookie from Chrome (macOS Keychain access required)",
    )
    auth_group.add_argument(
        "--cookie",
        type=str,
        default=None,
        metavar="LI_AT",
        help="Use a raw li_at cookie value",
    )

    c = sub.add_parser("connections", help="List 1st-degree connections")
    c.add_argument("--json", action="store_true", help="Output raw JSON")
    c.add_argument("--limit", type=int, default=None, help="Max number of results")
    c.add_argument("--output", type=str, default=None, help="Write to FILE instead of stdout")

    co = sub.add_parser("connect", help="Send a connection request")
    co.add_argument("url", help="LinkedIn profile URL (https://www.linkedin.com/in/<slug>/)")
    co.add_argument("--message", type=str, default=None, help="Invite note (max 300 chars)")
    co.add_argument("--dry-run", action="store_true", help="Parse the URL and exit without sending")
    co.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

    s = sub.add_parser("search", help="Search LinkedIn")
    s_sub = s.add_subparsers(dest="search_kind", title="search kinds")

    sp = s_sub.add_parser("people", help="Search for people")
    sp.add_argument("query", help="Keywords to search for")
    sp.add_argument(
        "--network",
        type=str,
        default=None,
        help="Comma-separated network depths: F (1st), S (2nd), O (3rd+). Default: all.",
    )
    sp.add_argument("--limit", type=int, default=25, help="Max number of results (default 25)")
    sp.add_argument("--json", action="store_true", help="Output raw JSON")
    sp.add_argument("--output", type=str, default=None, help="Write to FILE instead of stdout")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "login":
        return _cmd_login(args)
    if args.command == "connections":
        return _cmd_connections(args)
    if args.command == "connect":
        return _cmd_connect(args)
    if args.command == "search":
        if args.search_kind == "people":
            return _cmd_search_people(args)
        s.print_help()
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
