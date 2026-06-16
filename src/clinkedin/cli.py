"""CLInkedIn entry point."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from .auth import NotAuthenticatedError, login, login_from_chrome, login_with_cookie
from .client import make_client
from .comment import CommentError, parse_post_url, post_comment
from .connections import fetch_connections, format_json, format_table
from .disconnect import DisconnectError, remove_connection
from .industries import IndustryError, resolve_industry
from . import jobqueue
from .follow import (
    FollowError,
    debug_following,
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
from .message import MAX_MESSAGE_LEN, MessageError, parse_thread_target, send_message
from .post import MAX_BODY_LEN as _POST_MAX_BODY_LEN
from .post import PostError, create_post
from .posts import AuthExpiredError, PageShapeError
from .reactions import ReactionsError, fetch_comments, fetch_reactions
from .reactions import format_json as reactions_format_json
from .reactions import format_text as reactions_format_text
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

    industries: list[str] | None = None
    if args.industry:
        try:
            industries = [resolve_industry(i) for i in args.industry]
        except IndustryError as e:
            print(str(e), file=sys.stderr)
            return 2

    try:
        client = make_client()
    except NotAuthenticatedError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        results = search_people(
            client,
            args.query,
            network_depths=network,
            industries=industries,
            limit=args.limit,
            offset=args.offset,
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


def _maybe_enqueue(args: argparse.Namespace, command: str, fields: dict) -> int | None:
    """If --queue is set, record the (already-validated) job and return 0.

    Returns None when the command should run immediately. `fields` are the
    resolved argparse fields the runner needs to replay the command (e.g. a
    --message-file is read and inlined as `message` before it reaches here).
    """
    if not getattr(args, "queue", False):
        return None
    job_id = jobqueue.enqueue(command, fields)
    print(f"Queued {command} (id {job_id}).")
    print("Run 'clinkedin queue run' or wait for the scheduler to drain it.")
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

    rc = _maybe_enqueue(args, "connect", {"url": args.url, "message": message})
    if rc is not None:
        return rc

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

    rc = _maybe_enqueue(args, "disconnect", {"url": args.url})
    if rc is not None:
        return rc

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


_COMMENT_MAX_LEN = 1250


def _cmd_comment(args: argparse.Namespace) -> int:
    # Parse just to validate the URL up front; the IDs are extracted again
    # inside post_comment (which may also fetch the post page for missing IDs).
    try:
        ids = parse_post_url(args.url)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    has_msg = args.message is not None
    has_file = args.message_file is not None
    if has_msg == has_file:  # both or neither
        print(
            "Provide exactly one of --message or --message-file.", file=sys.stderr
        )
        return 2

    if has_file:
        try:
            message = Path(args.message_file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"Could not read --message-file {args.message_file!r}: {e}", file=sys.stderr)
            return 2
    else:
        message = args.message or ""

    message = message.rstrip()
    if not message:
        print("Comment message is empty.", file=sys.stderr)
        return 2
    if len(message) > _COMMENT_MAX_LEN:
        print(
            f"Comment too long ({len(message)}/{_COMMENT_MAX_LEN}).",
            file=sys.stderr,
        )
        return 2

    target_label = (
        f"urn:li:activity:{ids['activity_id']}"
        if ids.get("activity_id")
        else f"urn:li:share:{ids['share_id']}"
        if ids.get("share_id")
        else args.url
    )
    print(f"Comment target: {target_label}", flush=True)
    print(f"Message ({len(message)} chars):", flush=True)
    for line in message.split("\n"):
        print(f"  ┃ {line}", flush=True)

    if args.dry_run:
        return 0

    rc = _maybe_enqueue(args, "comment", {"url": args.url, "message": message})
    if rc is not None:
        return rc

    if not args.yes:
        sys.stdout.flush()
        try:
            reply = input("\nPost this comment? [y/N] ").strip().lower()
        except EOFError:
            print("Aborted (no input).", file=sys.stderr)
            return 0
        if reply not in ("y", "yes"):
            print("Aborted.")
            return 0

    try:
        client = make_client()
    except NotAuthenticatedError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        result = post_comment(client, args.url, message)
    except CommentError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Comment failed: {e}", file=sys.stderr)
        print("If this looks like a session error, run 'clinkedin login' again.", file=sys.stderr)
        return 1

    print(f"Comment posted on {target_label}")
    comment_urn = result.get("urn") if isinstance(result, dict) else None
    if comment_urn:
        print(f"Comment URN: {comment_urn}")
    return 0


def _cmd_post(args: argparse.Namespace) -> int:
    has_msg = args.message is not None
    has_file = args.message_file is not None
    if has_msg == has_file:
        print("Provide exactly one of --message or --message-file.", file=sys.stderr)
        return 2

    if has_file:
        try:
            body = Path(args.message_file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"Could not read --message-file {args.message_file!r}: {e}", file=sys.stderr)
            return 2
    else:
        body = args.message or ""

    try:
        # Validate inputs up front so we never reach the session for bad args.
        preview = create_post(
            None, body, image_path=args.image, dry_run=True
        )
    except PostError as e:
        print(str(e), file=sys.stderr)
        return 2

    print(f"Visibility: {preview['visibility']}", flush=True)
    if preview["image"]:
        print(f"Image: {preview['image']}", flush=True)
    print(f"Body ({preview['body_chars']} chars):", flush=True)
    for line in preview["body"].split("\n"):
        print(f"  ┃ {line}", flush=True)

    if args.dry_run:
        return 0

    rc = _maybe_enqueue(args, "post", {"message": body, "image": args.image})
    if rc is not None:
        return rc

    if not args.yes:
        sys.stdout.flush()
        try:
            reply = input("\nPublish this post? [y/N] ").strip().lower()
        except EOFError:
            print("Aborted (no input).", file=sys.stderr)
            return 0
        if reply not in ("y", "yes"):
            print("Aborted.")
            return 0

    try:
        client = make_client()
    except NotAuthenticatedError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        result = create_post(client, body, image_path=args.image, dry_run=False)
    except PostError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Post failed: {e}", file=sys.stderr)
        print("If this looks like a session error, run 'clinkedin login' again.", file=sys.stderr)
        return 1

    print("Post published.")
    if isinstance(result, dict):
        activity_urn = result.get("activity_urn")
        share_url = result.get("share_url")
        if activity_urn:
            print(f"Activity: {activity_urn}")
        if share_url:
            print(f"URL: {share_url}")
    return 0


def _cmd_message(args: argparse.Namespace) -> int:
    has_msg = args.message is not None
    has_file = args.message_file is not None
    if has_msg == has_file:
        print("Provide exactly one of --message or --message-file.", file=sys.stderr)
        return 2

    if has_file:
        try:
            text = Path(args.message_file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"Could not read --message-file {args.message_file!r}: {e}", file=sys.stderr)
            return 2
    else:
        text = args.message or ""

    text = text.rstrip()
    if not text:
        print("Message text is empty.", file=sys.stderr)
        return 2
    if len(text) > MAX_MESSAGE_LEN:
        print(f"Message too long ({len(text)}/{MAX_MESSAGE_LEN}).", file=sys.stderr)
        return 2

    # Enqueue before touching the session: the target is resolved against the
    # client (a bare thread id needs the viewer URN), so it is validated when
    # the runner replays the job rather than here.
    rc = _maybe_enqueue(args, "message", {"target": args.target, "message": text})
    if rc is not None:
        return rc

    try:
        client = make_client()
    except NotAuthenticatedError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        mailbox_urn, thread_id = parse_thread_target(client, args.target)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    except InviteError as e:
        # resolve_self_urn raises InviteError for auth/lookup failures.
        print(str(e), file=sys.stderr)
        return 1

    print(f"Conversation: {thread_id}", flush=True)
    print(f"Message ({len(text)} chars):", flush=True)
    for line in text.split("\n"):
        print(f"  ┃ {line}", flush=True)

    if args.dry_run:
        return 0

    if not args.yes:
        sys.stdout.flush()
        try:
            reply = input("\nSend this message? [y/N] ").strip().lower()
        except EOFError:
            print("Aborted (no input).", file=sys.stderr)
            return 0
        if reply not in ("y", "yes"):
            print("Aborted.")
            return 0

    try:
        send_message(client, mailbox_urn, thread_id, text)
    except MessageError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Send failed: {e}", file=sys.stderr)
        print("If this looks like a session error, run 'clinkedin login' again.", file=sys.stderr)
        return 1

    print(f"Message sent to {thread_id}")
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

    rc = _maybe_enqueue(args, action, {"url": args.url})
    if rc is not None:
        return rc

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

    if args.debug:
        try:
            info = debug_following(client, count=args.limit or 5)
        except Exception as e:
            import traceback
            print(f"Debug probe failed: {type(e).__name__}: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return 1
        import json as _json
        print(_json.dumps(info, indent=2, default=str))
        return 0

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


def _cmd_reactions(args: argparse.Namespace) -> int:
    if args.likers_only and args.commenters_only:
        print("Pass at most one of --likers-only / --commenters-only.", file=sys.stderr)
        return 2
    if args.offset < 0:
        print(f"--offset must be >= 0 (got {args.offset}).", file=sys.stderr)
        return 2

    try:
        parse_post_url(args.url)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"[dry-run] Would list engagement for: {args.url}")
        return 0

    try:
        client = make_client()
    except NotAuthenticatedError as e:
        print(str(e), file=sys.stderr)
        return 1

    reactors: list[dict] = []
    commenters: list[dict] = []
    try:
        if not args.commenters_only:
            reactors = fetch_reactions(client, args.url, limit=args.limit, offset=args.offset)
        if not args.likers_only:
            commenters = fetch_comments(client, args.url, limit=args.limit, offset=args.offset)
    except ReactionsError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Failed to fetch engagement: {e}", file=sys.stderr)
        print("If this looks like a session error, run 'clinkedin login' again.", file=sys.stderr)
        return 1

    rendered = (
        reactions_format_json(reactors, commenters)
        if args.json
        else reactions_format_text(reactors, commenters)
    )
    if args.output:
        Path(args.output).write_text(rendered)
    else:
        print(rendered)
    return 0


# Single source of truth for command -> handler, shared by main()'s dispatch and
# by jobqueue.run_once() when it replays a queued job.
_DISPATCH = {
    "login": _cmd_login,
    "connections": _cmd_connections,
    "connect": _cmd_connect,
    "disconnect": _cmd_disconnect,
    "comment": _cmd_comment,
    "post": _cmd_post,
    "message": _cmd_message,
    "follow": _cmd_follow,
    "unfollow": _cmd_unfollow,
    "following": _cmd_following,
    "view": _cmd_view,
    "reactions": _cmd_reactions,
}


def _cmd_queue(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    action = args.queue_action
    if action == "run":
        try:
            hours = jobqueue.parse_active_hours(args.active_hours) if args.active_hours else None
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        rc = jobqueue.run_once(
            min_interval=args.min_interval,
            max_interval=args.max_interval,
            active_hours=hours,
            daily_cap=args.daily_cap,
        )
        if rc == jobqueue.EMPTY:
            print("Nothing to run (queue empty, or a spacing/active-hours/daily-cap gate held it back).")
            return 0
        return 0 if rc == 0 else 1

    if action == "list":
        pending = jobqueue.list_jobs("pending")
        failed = jobqueue.list_jobs("failed")
        if not pending and not failed:
            print("Queue is empty.")
            return 0
        if pending:
            print(f"Pending ({len(pending)}):")
            for job in pending:
                print(f"  {_fmt_job(job)}")
        if failed:
            print(f"Failed ({len(failed)}):")
            for job in failed:
                err = job.get("last_error")
                print(f"  {_fmt_job(job)}" + (f" — {err}" if err else ""))
        return 0

    if action == "remove":
        if jobqueue.remove(args.id):
            print(f"Removed {args.id}.")
            return 0
        print(f"No job with id {args.id!r}.", file=sys.stderr)
        return 1

    if action == "clear":
        n = jobqueue.clear()
        print(f"Cleared {n} pending job(s).")
        return 0

    if action == "retry":
        if jobqueue.retry(args.id):
            print(f"Requeued {args.id}.")
            return 0
        print(f"No failed job with id {args.id!r}.", file=sys.stderr)
        return 1

    if action == "install":
        if args.active_hours and args.active_hours.lower() != "none":
            try:
                jobqueue.parse_active_hours(args.active_hours)
            except ValueError as e:
                print(str(e), file=sys.stderr)
                return 2
        path = jobqueue.install_scheduler(
            interval=args.interval,
            min_interval=args.min_interval,
            max_interval=args.max_interval,
            active_hours=args.active_hours,
            daily_cap=args.daily_cap,
        )
        gap = f"{args.min_interval}-{args.max_interval}s (randomized)"
        window = args.active_hours if (args.active_hours and args.active_hours.lower() != "none") else "24h"
        cap = args.daily_cap or "none"
        print(f"Wrote launchd agent: {path}")
        print(f"Load it with:\n  launchctl load -w {path}")
        print(
            f"Wakes every {args.interval}s; sends spaced {gap}, active hours {window}, "
            f"daily cap {cap}. Logs to {jobqueue.runner_log()}."
        )
        print("Non-macOS: add a crontab line instead, e.g.")
        print(f"  * * * * * {' '.join(jobqueue.runner_args(args.min_interval, args.max_interval, args.active_hours, args.daily_cap))}")
        return 0

    if action == "uninstall":
        path = jobqueue.plist_path()
        if jobqueue.uninstall_scheduler():
            print(f"Removed {path}.")
            print(f"Unload it from launchd with:\n  launchctl unload {path}")
            return 0
        print(f"No launchd agent at {path}.", file=sys.stderr)
        return 1

    parser.print_help()
    return 0


def _fmt_job(job: dict) -> str:
    args = job.get("args", {})
    target = args.get("url") or args.get("target") or ""
    return f"{job['id']}  {job['command']}  {target}".rstrip()


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
            "  clinkedin comment https://www.linkedin.com/feed/update/urn:li:activity:7…/ \\\n"
            "      --message 'Great point — agreed'\n"
            "  clinkedin post --message 'Shipping v2 today' --image ~/Pictures/launch.png\n"
            "  clinkedin message https://www.linkedin.com/messaging/thread/2-…/ \\\n"
            "      --message 'On my way!' --yes\n"
            "  clinkedin follow https://www.linkedin.com/in/<slug>/ --yes\n"
            "  clinkedin follow https://www.linkedin.com/company/<slug>/ --yes\n"
            "  clinkedin unfollow https://www.linkedin.com/company/<slug>/ --yes\n"
            "  clinkedin following --limit 50\n"
            "  clinkedin view https://www.linkedin.com/in/<slug>/\n"
            "  clinkedin view https://www.linkedin.com/in/<slug>/ --posts --limit 25\n"
            "  clinkedin search people 'product manager fintech'\n"
            "  clinkedin search people 'designer' --network F,S --limit 50\n"
            "  clinkedin search people '' --network F --industry 'Financial Services'\n"
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
    co.add_argument("--queue", action="store_true", help="Add to the queue instead of sending now")

    dc = sub.add_parser("disconnect", help="Remove an existing connection")
    dc.add_argument("url", help="LinkedIn profile URL (https://www.linkedin.com/in/<slug>/)")
    dc.add_argument("--dry-run", action="store_true", help="Parse the URL and exit without disconnecting")
    dc.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    dc.add_argument("--queue", action="store_true", help="Add to the queue instead of disconnecting now")

    cm = sub.add_parser("comment", help="Post a public comment on a LinkedIn post")
    cm.add_argument("url", help="LinkedIn post URL or urn:li:activity:<id>")
    cm.add_argument("--message", type=str, default=None, help=f"Comment text (max {_COMMENT_MAX_LEN} chars)")
    cm.add_argument("--message-file", type=str, default=None, help="Read comment text from FILE (UTF-8)")
    cm.add_argument("--dry-run", action="store_true", help="Parse the URL and exit without posting")
    cm.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    cm.add_argument("--queue", action="store_true", help="Add to the queue instead of posting now")

    po = sub.add_parser("post", help="Publish a new feed post (text + optional image)")
    po.add_argument("--message", type=str, default=None, help=f"Post body (max {_POST_MAX_BODY_LEN} chars)")
    po.add_argument("--message-file", type=str, default=None, help="Read post body from FILE (UTF-8)")
    po.add_argument("--image", type=str, default=None, metavar="PATH", help="Attach a single image (PNG/JPG, <=10MB)")
    po.add_argument("--dry-run", action="store_true", help="Validate inputs and exit without publishing")
    po.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    po.add_argument("--queue", action="store_true", help="Add to the queue instead of publishing now")

    mg = sub.add_parser("message", help="Reply in an existing LinkedIn conversation")
    mg.add_argument(
        "target",
        help=(
            "Conversation target: thread URL (https://www.linkedin.com/messaging/thread/2-…/), "
            "full urn:li:msg_conversation:(…) URN, or bare 2-… thread id"
        ),
    )
    mg.add_argument("--message", type=str, default=None, help=f"Message text (max {MAX_MESSAGE_LEN} chars)")
    mg.add_argument("--message-file", type=str, default=None, help="Read message text from FILE (UTF-8)")
    mg.add_argument("--dry-run", action="store_true", help="Parse the target and exit without sending")
    mg.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    mg.add_argument("--queue", action="store_true", help="Add to the queue instead of sending now")

    fl = sub.add_parser("follow", help="Follow a person or company")
    fl.add_argument("url", help="LinkedIn member (/in/<slug>) or company (/company/<slug>) URL")
    fl.add_argument("--dry-run", action="store_true", help="Parse the URL and exit without following")
    fl.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    fl.add_argument("--queue", action="store_true", help="Add to the queue instead of following now")

    ufl = sub.add_parser("unfollow", help="Unfollow a person or company")
    ufl.add_argument("url", help="LinkedIn member (/in/<slug>) or company (/company/<slug>) URL")
    ufl.add_argument("--dry-run", action="store_true", help="Parse the URL and exit without unfollowing")
    ufl.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    ufl.add_argument("--queue", action="store_true", help="Add to the queue instead of unfollowing now")

    fg = sub.add_parser("following", help="List members and companies you follow")
    fg.add_argument("--json", action="store_true", help="Output raw JSON")
    fg.add_argument("--limit", type=int, default=None, help="Max number of results")
    fg.add_argument("--offset", type=int, default=0, help="Skip the first N results (for pagination)")
    fg.add_argument("--output", type=str, default=None, help="Write to FILE instead of stdout")
    fg.add_argument("--debug", action="store_true", help="Probe candidate Voyager endpoints and dump raw responses")

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

    rx = sub.add_parser("reactions", help="List people who liked/commented on a post (with profile URLs)")
    rx.add_argument("url", help="LinkedIn post URL or urn:li:activity:<id>")
    rx.add_argument("--likers-only", action="store_true", help="Only list reactors (skip commenters)")
    rx.add_argument("--commenters-only", action="store_true", help="Only list commenters (skip reactors)")
    rx.add_argument("--limit", type=int, default=100, help="Max entries per category (default 100, hard cap 1000)")
    rx.add_argument("--offset", type=int, default=0, help="Skip the first N entries per category")
    rx.add_argument("--json", action="store_true", help="Output JSON ({reactions, comments})")
    rx.add_argument("--dry-run", action="store_true", help="Parse the URL and exit without fetching")
    rx.add_argument("--output", type=str, default=None, help="Write to FILE instead of stdout")

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
    sp.add_argument(
        "--industry",
        action="append",
        default=None,
        metavar="NAME|CODE",
        help=(
            "Filter by industry; name (e.g. 'Financial Services') or numeric code. "
            "Repeat for multiple (matches any)."
        ),
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

    q = sub.add_parser(
        "queue",
        help="Manage the deferred write queue (run/list/remove/clear/retry/install)",
    )
    q_sub = q.add_subparsers(dest="queue_action", title="queue actions")
    q_run = q_sub.add_parser("run", help="Execute the oldest queued job and exit")
    q_run.add_argument(
        "--min-interval",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Minimum gap since the last send before running again",
    )
    q_run.add_argument(
        "--max-interval",
        type=int,
        default=0,
        metavar="SECONDS",
        help="With --min-interval, randomize the gap in [min, max] so sends land irregularly",
    )
    q_run.add_argument(
        "--active-hours",
        type=str,
        default=None,
        metavar="START-END",
        help="Only run during local hours START-END (e.g. 8-20); skip otherwise",
    )
    q_run.add_argument(
        "--daily-cap",
        type=int,
        default=0,
        metavar="N",
        help="Do nothing once N jobs have run today (0 = no cap)",
    )
    q_sub.add_parser("list", help="Show pending and dead-lettered jobs")
    q_rm = q_sub.add_parser("remove", help="Delete a job by id (any state)")
    q_rm.add_argument("id", help="Job id (see 'queue list')")
    q_sub.add_parser("clear", help="Delete all pending jobs")
    q_retry = q_sub.add_parser("retry", help="Move a failed job back to pending")
    q_retry.add_argument("id", help="Job id (see 'queue list')")
    q_install = q_sub.add_parser("install", help="Install a launchd agent to run the queue")
    q_install.add_argument(
        "--interval", type=int, default=60, metavar="SECONDS",
        help="How often the agent wakes to check the queue (default 60)",
    )
    q_install.add_argument(
        "--min-interval", type=int, default=300, metavar="SECONDS",
        help="Minimum gap between sends (default 300)",
    )
    q_install.add_argument(
        "--max-interval", type=int, default=1200, metavar="SECONDS",
        help="Upper bound for the randomized gap between sends (default 1200)",
    )
    q_install.add_argument(
        "--active-hours", type=str, default="8-20", metavar="START-END",
        help="Only send during local hours START-END (default 8-20; 'none' to disable)",
    )
    q_install.add_argument(
        "--daily-cap", type=int, default=0, metavar="N",
        help="Cap sends per day (0 = no cap)",
    )
    q_sub.add_parser("uninstall", help="Remove the launchd agent")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "search":
        if args.search_kind == "people":
            return _cmd_search_people(args)
        if args.search_kind == "posts":
            return _cmd_search_posts(args)
        s.print_help()
        return 0
    if args.command == "queue":
        return _cmd_queue(args, q)

    handler = _DISPATCH.get(args.command)
    if handler is None:
        parser.error(f"unknown command: {args.command}")
        return 2
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
