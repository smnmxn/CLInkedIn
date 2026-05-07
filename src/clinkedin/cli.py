"""CLInkedIn entry point."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from .auth import NotAuthenticatedError, login, login_from_chrome, login_with_cookie
from .client import make_client
from .connections import fetch_connections, format_json, format_table
from .disconnect import DisconnectError, remove_connection
from .follow import (
    FollowError,
    follow_company,
    follow_member,
    list_following,
    parse_follow_url,
    unfollow_company,
    unfollow_member,
)
from .follow import format_json as following_format_json
from .follow import format_table as following_format_table
from .invite import InviteError, parse_profile_url, send_invite
from .posts import AuthExpiredError, PageShapeError
from .posts import format_json as posts_format_json
from .posts import format_table as posts_format_table
from .posts import search_posts
from .profile import (
    ProfileError,
    debug_profile_posts,
    fetch_profile,
    fetch_profile_posts,
    filter_posts_by_age,
    parse_age,
)
from .profile import format_json as profile_format_json
from .profile import format_posts_json as profile_format_posts_json
from .profile import format_posts_text as profile_format_posts_text
from .profile import format_text as profile_format_text
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
    if args.offset < 0:
        print(f"--offset must be >= 0 (got {args.offset}).", file=sys.stderr)
        return 2

    try:
        client = make_client()
    except NotAuthenticatedError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        conns = fetch_connections(client, limit=args.limit, offset=args.offset)
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

    if args.offset < 0:
        print(f"--offset must be >= 0 (got {args.offset}).", file=sys.stderr)
        return 2

    try:
        client = make_client()
    except NotAuthenticatedError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        results = search_people(
            client, args.query, network_depths=network, limit=args.limit, offset=args.offset
        )
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


def _cmd_search_posts(args: argparse.Namespace) -> int:
    try:
        client = make_client()
    except NotAuthenticatedError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        results = search_posts(client, args.query, limit=args.limit)
    except AuthExpiredError as e:
        print(str(e), file=sys.stderr)
        return 1
    except PageShapeError as e:
        print(str(e), file=sys.stderr)
        print(
            "Post search relies on scraping LinkedIn's web UI; this likely "
            "means the page structure changed. Please report the dump path "
            "above to the maintainer.",
            file=sys.stderr,
        )
        return 1
    except Exception as e:
        print(f"Search failed: {e}", file=sys.stderr)
        print("If this looks like a session error, run 'clinkedin login' again.", file=sys.stderr)
        return 1

    rendered = posts_format_json(results) if args.json else posts_format_table(results)
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


def _cmd_disconnect(args: argparse.Namespace) -> int:
    try:
        public_id = parse_profile_url(args.url)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    print(f"Disconnect target: {public_id}")

    if args.dry_run:
        return 0

    if not args.yes:
        reply = input(f"Disconnect from {public_id}? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Aborted.")
            return 0

    try:
        client = make_client()
    except NotAuthenticatedError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        remove_connection(client, public_id)
    except DisconnectError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Disconnect failed: {e}", file=sys.stderr)
        return 1

    print(f"Disconnected from {public_id}")
    return 0


def _cmd_follow(args: argparse.Namespace) -> int:
    return _follow_or_unfollow(args, action="follow")


def _cmd_unfollow(args: argparse.Namespace) -> int:
    return _follow_or_unfollow(args, action="unfollow")


def _follow_or_unfollow(args: argparse.Namespace, *, action: str) -> int:
    try:
        kind, slug = parse_follow_url(args.url)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    verb = "Follow" if action == "follow" else "Unfollow"
    print(f"{verb} target: {kind} {slug}")

    if args.dry_run:
        return 0

    if not args.yes:
        reply = input(f"{verb} {slug}? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("Aborted.")
            return 0

    try:
        client = make_client()
    except NotAuthenticatedError as e:
        print(str(e), file=sys.stderr)
        return 1

    fn = {
        ("follow", "member"): follow_member,
        ("follow", "company"): follow_company,
        ("unfollow", "member"): unfollow_member,
        ("unfollow", "company"): unfollow_company,
    }[(action, kind)]

    try:
        fn(client, slug)
    except FollowError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"{verb} failed: {e}", file=sys.stderr)
        print("If this looks like a session error, run 'clinkedin login' again.", file=sys.stderr)
        return 1

    past = "Followed" if action == "follow" else "Unfollowed"
    print(f"{past} {kind} {slug}")
    return 0


def _cmd_following(args: argparse.Namespace) -> int:
    if args.offset < 0:
        print(f"--offset must be >= 0 (got {args.offset}).", file=sys.stderr)
        return 2

    try:
        client = make_client()
    except NotAuthenticatedError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        items = list_following(client, limit=args.limit, offset=args.offset)
    except FollowError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Failed to list following: {e}", file=sys.stderr)
        print("If this looks like a session error, run 'clinkedin login' again.", file=sys.stderr)
        return 1

    rendered = following_format_json(items) if args.json else following_format_table(items)
    if args.output:
        Path(args.output).write_text(rendered)
    else:
        print(rendered)
    return 0


def _cmd_view(args: argparse.Namespace) -> int:
    try:
        public_id = parse_profile_url(args.url)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    try:
        client = make_client()
    except NotAuthenticatedError as e:
        print(str(e), file=sys.stderr)
        return 1

    if args.posts and args.debug:
        print(f"[debug] probing /identity/profiles/{public_id}/profileView ...", file=sys.stderr, flush=True)
        try:
            info = debug_profile_posts(client, public_id, limit=args.limit)
        except ProfileError as e:
            print(str(e), file=sys.stderr)
            return 1
        except Exception as e:
            import traceback
            print(f"Debug probe failed: {type(e).__name__}: {e}", file=sys.stderr)
            print("--- traceback ---", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return 1
        print("[debug] probe complete, dumping result:", file=sys.stderr, flush=True)
        import json as _json
        print(_json.dumps(info, indent=2, default=str))
        return 0

    max_age = None
    if args.posts and args.since:
        max_age = parse_age(args.since)
        if max_age is None:
            print(
                f"Could not parse --since {args.since!r}. Try formats like "
                "'1d', '7d', '2w', '1mo', '1y'.",
                file=sys.stderr,
            )
            return 2

    try:
        if args.posts:
            # When filtering by age we may discard most results, so fetch a
            # larger window to keep --limit meaningful as a "show me up to N
            # recent posts within the last X" cap.
            fetch_limit = args.limit * 5 if max_age is not None else args.limit
            posts = fetch_profile_posts(client, public_id, limit=fetch_limit)
            if max_age is not None:
                posts = filter_posts_by_age(posts, max_age)
            posts = posts[: args.limit]
        else:
            profile = fetch_profile(client, public_id)
    except ProfileError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Profile fetch failed: {e}", file=sys.stderr)
        print("If this looks like a session error, run 'clinkedin login' again.", file=sys.stderr)
        return 1

    if args.posts:
        if args.json:
            rendered = profile_format_posts_json(posts)
        else:
            rendered = profile_format_posts_text(posts, full=args.full)
    else:
        rendered = profile_format_json(profile) if args.json else profile_format_text(profile)
    if args.output:
        Path(args.output).write_text(rendered)
    else:
        print(rendered)
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
            "  clinkedin disconnect https://www.linkedin.com/in/<slug>/ --yes\n"
            "  clinkedin follow https://www.linkedin.com/in/<slug>/ --yes\n"
            "  clinkedin follow https://www.linkedin.com/company/<slug>/ --yes\n"
            "  clinkedin unfollow https://www.linkedin.com/company/<slug>/ --yes\n"
            "  clinkedin following --limit 50\n"
            "  clinkedin view https://www.linkedin.com/in/<slug>/\n"
            "  clinkedin view https://www.linkedin.com/in/<slug>/ --posts --limit 25\n"
            "  clinkedin search people 'product manager fintech'\n"
            "  clinkedin search people 'designer' --network F,S --limit 50\n"
            "  clinkedin search posts 'AI agents'\n"
            "  clinkedin search posts 'RAG' --limit 5 --json\n"
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
    c.add_argument("--offset", type=int, default=0, help="Skip the first N results (for pagination)")
    c.add_argument("--output", type=str, default=None, help="Write to FILE instead of stdout")

    co = sub.add_parser("connect", help="Send a connection request")
    co.add_argument("url", help="LinkedIn profile URL (https://www.linkedin.com/in/<slug>/)")
    co.add_argument("--message", type=str, default=None, help="Invite note (max 300 chars)")
    co.add_argument("--dry-run", action="store_true", help="Parse the URL and exit without sending")
    co.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

    dc = sub.add_parser("disconnect", help="Remove an existing connection")
    dc.add_argument("url", help="LinkedIn profile URL (https://www.linkedin.com/in/<slug>/)")
    dc.add_argument("--dry-run", action="store_true", help="Parse the URL and exit without disconnecting")
    dc.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

    fl = sub.add_parser("follow", help="Follow a person or company")
    fl.add_argument("url", help="LinkedIn member (/in/<slug>) or company (/company/<slug>) URL")
    fl.add_argument("--dry-run", action="store_true", help="Parse the URL and exit without following")
    fl.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

    ufl = sub.add_parser("unfollow", help="Unfollow a person or company")
    ufl.add_argument("url", help="LinkedIn member (/in/<slug>) or company (/company/<slug>) URL")
    ufl.add_argument("--dry-run", action="store_true", help="Parse the URL and exit without unfollowing")
    ufl.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")

    fg = sub.add_parser("following", help="List members and companies you follow")
    fg.add_argument("--json", action="store_true", help="Output raw JSON")
    fg.add_argument("--limit", type=int, default=None, help="Max number of results")
    fg.add_argument("--offset", type=int, default=0, help="Skip the first N results (for pagination)")
    fg.add_argument("--output", type=str, default=None, help="Write to FILE instead of stdout")

    v = sub.add_parser("view", help="View a LinkedIn profile (name, headline, experience, education)")
    v.add_argument("url", help="LinkedIn profile URL (https://www.linkedin.com/in/<slug>/)")
    v.add_argument("--json", action="store_true", help="Output raw JSON")
    v.add_argument("--posts", action="store_true", help="Show the profile's recent posts instead of bio/experience")
    v.add_argument("--limit", type=int, default=10, help="Max posts to fetch with --posts (default 10)")
    v.add_argument("--full", action="store_true", help="With --posts: print full untruncated text (multi-line per post)")
    v.add_argument(
        "--since",
        type=str,
        default=None,
        metavar="AGE",
        help="With --posts: keep only posts at most AGE old (e.g. 1d, 7d, 2w, 1mo, 1y)",
    )
    v.add_argument("--debug", action="store_true", help="With --posts: print the raw /identity/profileUpdatesV2 response")
    v.add_argument("--output", type=str, default=None, help="Write to FILE instead of stdout")

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
    sp.add_argument("--offset", type=int, default=0, help="Skip the first N results (for pagination)")
    sp.add_argument("--json", action="store_true", help="Output raw JSON")
    sp.add_argument("--output", type=str, default=None, help="Write to FILE instead of stdout")

    sp_posts = s_sub.add_parser("posts", help="Search for posts (web-page scrape; comments not supported)")
    sp_posts.add_argument("query", help="Keywords to search for")
    sp_posts.add_argument("--limit", type=int, default=10, help="Max number of results (default 10)")
    sp_posts.add_argument("--json", action="store_true", help="Output raw JSON")
    sp_posts.add_argument("--output", type=str, default=None, help="Write to FILE instead of stdout")

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
    if args.command == "disconnect":
        return _cmd_disconnect(args)
    if args.command == "follow":
        return _cmd_follow(args)
    if args.command == "unfollow":
        return _cmd_unfollow(args)
    if args.command == "following":
        return _cmd_following(args)
    if args.command == "view":
        return _cmd_view(args)
    if args.command == "search":
        if args.search_kind == "people":
            return _cmd_search_people(args)
        if args.search_kind == "posts":
            return _cmd_search_posts(args)
        s.print_help()
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
