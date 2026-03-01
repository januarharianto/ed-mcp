"""Typer CLI for Ed Discussion — LLM-friendly alternative to the MCP server."""

from __future__ import annotations

import asyncio
import json
import sys
from functools import wraps
from pathlib import Path
from typing import Any

import typer

from edstem_mcp.__about__ import __version__
from edstem_mcp._helpers import _json
from edstem_mcp.client import EdAPIError, EdClient

# ------------------------------------------------------------------
# Async helper
# ------------------------------------------------------------------

def _run_async(f):
    """Decorator: run an async Typer command via asyncio.run()."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper


# ------------------------------------------------------------------
# Config management
# ------------------------------------------------------------------

_CONFIG_DIR = Path.home() / ".config" / "ed"
_CONFIG_FILE = _CONFIG_DIR / "config.json"


def _get_config() -> dict[str, Any]:
    if _CONFIG_FILE.exists():
        return json.loads(_CONFIG_FILE.read_text())
    return {}


def _set_config(key: str, value: Any) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config = _get_config()
    config[key] = value
    _CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")


def _require_course(course: int | None) -> int:
    """Resolve course ID from --course flag or saved config."""
    if course is not None:
        return course
    saved = _get_config().get("course_id")
    if saved is not None:
        return int(saved)
    print('{"error":"No course ID. Use --course or run: ed config set-course <id>"}', file=sys.stderr)
    raise typer.Exit(1)


# ------------------------------------------------------------------
# Client helper
# ------------------------------------------------------------------

def _output(data: Any) -> None:
    """Print compact JSON to stdout."""
    if isinstance(data, str):
        print(data)
    else:
        print(_json(data))


async def _client() -> EdClient:
    """Create a fresh EdClient. Caller must close it."""
    return EdClient()


# ------------------------------------------------------------------
# App structure
# ------------------------------------------------------------------

app = typer.Typer(no_args_is_help=True, add_completion=False)
courses_app = typer.Typer(no_args_is_help=True, help="Course operations.")
threads_app = typer.Typer(no_args_is_help=True, help="Thread operations.")
attendance_app = typer.Typer(no_args_is_help=True, help="Attendance operations.")
comments_app = typer.Typer(no_args_is_help=True, help="Comment operations.")
users_app = typer.Typer(no_args_is_help=True, help="User operations.")
files_app = typer.Typer(no_args_is_help=True, help="File operations.")
config_app = typer.Typer(no_args_is_help=True, help="CLI configuration.")

app.add_typer(courses_app, name="courses")
app.add_typer(threads_app, name="threads")
app.add_typer(attendance_app, name="attendance")
app.add_typer(comments_app, name="comments")
app.add_typer(users_app, name="users")
app.add_typer(files_app, name="files")
app.add_typer(config_app, name="config")


# ------------------------------------------------------------------
# Top-level commands
# ------------------------------------------------------------------


@app.command()
def usage():
    """Print compact CLI reference for LLM consumption."""
    print(f"""Ed Discussion CLI v{__version__}

Config:
  ed config set-course <id>     Save default course

Courses:
  ed courses list [--status S] [--year Y] [--code C]
  ed courses stats [--course ID]
  ed courses users [--course ID] [--role R] [--limit N] [--offset N]
  ed courses categories [--course ID]

Threads:
  ed threads list [--course ID] [--sort S] [--filter F] [--limit N] [--offset N]
  ed threads get <ref>          ref = thread_id, #number, or URL
  ed threads search <query> [--course ID] [--limit N]
  ed threads create --title T --content XML [--course ID] [--type T] [--category C]
  ed threads edit <id> [--title T] [--content XML] [--category C]
  ed threads reply <id> --content XML [--type comment|answer]
  ed threads delete <id>
  ed threads mod <id> [--lock|--unlock] [--pin|--unpin] [--endorse|--unendorse] [--duplicate-of ID] [--unmark-duplicate] [--accept COMMENT_ID]
  ed threads recategorise <ids> --category C [--subcategory S]

Attendance:
  ed attendance list [--course ID]
  ed attendance get <event_id>
  ed attendance create --title T [--course ID] [--start ISO] [--hidden]
  ed attendance update <id> [--title T] [--closed|--no-closed] [--hidden|--no-hidden]
  ed attendance delete <id>
  ed attendance check-in <id> --users U1,U2 [--kind present|late|excused|absent]
  ed attendance undo <id> --users U1,U2
  ed attendance analytics [--course ID]

Other:
  ed comments edit <id> --content XML
  ed comments delete <id>
  ed users activity <user_id> [--course ID] [--filter F] [--limit N]
  ed files upload <path>

Output: JSON (compact). Content format: Ed XML.""")


# ------------------------------------------------------------------
# Config commands
# ------------------------------------------------------------------


@config_app.command("set-course")
def config_set_course(course_id: int = typer.Argument(..., help="Course ID to use as default.")):
    """Save a default course ID so you don't need --course on every command."""
    _set_config("course_id", course_id)
    _output({"course_id": course_id, "saved": True})


# ------------------------------------------------------------------
# Course commands
# ------------------------------------------------------------------


@courses_app.command("list")
@_run_async
async def courses_list(
    status: str | None = typer.Option(None, help="Filter: active, archived, inactive."),
    year: str | None = typer.Option(None, help="Filter by year, e.g. 2026."),
    code: str | None = typer.Option(None, help="Filter by course code substring."),
):
    """List enrolled courses."""
    c = await _client()
    try:
        result = await c.get_user()
        code_upper = code.upper() if code else None
        courses = []
        for entry in result.get("courses", []):
            course = entry["course"]
            if status and course.get("status", "") != status:
                continue
            if year and course.get("year", "") != year:
                continue
            if code_upper and code_upper not in course.get("code", "").upper():
                continue
            courses.append({
                "id": course["id"],
                "code": course.get("code", ""),
                "name": course.get("name", ""),
                "year": course.get("year", ""),
                "session": course.get("session", ""),
                "status": course.get("status", ""),
            })
        _output(courses)
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


@courses_app.command("stats")
@_run_async
async def courses_stats(
    course: int | None = typer.Option(None, "--course", help="Course ID (uses saved default)."),
):
    """Get course overview: enrollment, unanswered Qs, top categories."""
    course_id = _require_course(course)
    c = await _client()
    try:
        stats = await c.get_course_stats(course_id)
        enrollment = stats.get("stats", {}).get("student_enrollment_count", 0)

        # Count unanswered and unresolved via pagination
        async def _count(filt: str) -> int:
            total = 0
            offset = 0
            while True:
                r = await c.list_threads(course_id, limit=100, offset=offset, filter=filt)
                batch = r.get("threads", [])
                total += len(batch)
                if len(batch) < 100:
                    break
                offset += 100
            return total

        recent = await c.list_threads(course_id, limit=100, offset=0)
        unanswered = await _count("unanswered")
        unresolved = await _count("unresolved")

        cat_counts: dict[str, int] = {}
        for t in recent.get("threads", []):
            cat = t.get("category", "") or "Uncategorised"
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
        top_categories = sorted(cat_counts.items(), key=lambda x: -x[1])

        _output({
            "enrollment": enrollment,
            "unanswered": unanswered,
            "unresolved": unresolved,
            "top_categories": [{"name": n, "count": ct} for n, ct in top_categories],
        })
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


@courses_app.command("users")
@_run_async
async def courses_users(
    course: int | None = typer.Option(None, "--course", help="Course ID."),
    role: str | None = typer.Option(None, help="Filter: student, staff, admin."),
    limit: int = typer.Option(50, help="Max users to return."),
    offset: int = typer.Option(0, help="Pagination offset."),
):
    """List enrolled users in a course."""
    from edstem_mcp._helpers import _pii_enabled
    course_id = _require_course(course)
    c = await _client()
    try:
        result = await c.list_users(course_id)
        strip_pii = _pii_enabled()
        users = []
        for u in result.get("users", []):
            u_role = u.get("course_role", u.get("role", ""))
            if role and u_role != role:
                continue
            entry: dict = {
                "id": u.get("user_id", u.get("id")),
                "name": u.get("name"),
                "course_role": u_role,
            }
            if not strip_pii:
                entry["email"] = u.get("email")
            users.append(entry)
        total = len(users)
        page = users[offset:offset + limit]
        _output({"users": page, "total": total, "offset": offset, "limit": limit})
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


@courses_app.command("categories")
@_run_async
async def courses_categories(
    course: int | None = typer.Option(None, "--course", help="Course ID."),
):
    """List thread categories and subcategories."""
    course_id = _require_course(course)
    c = await _client()
    try:
        result = await c.get_course(course_id)
        course_data = result.get("course", result)
        cats = (
            course_data.get("settings", {})
            .get("discussion", {})
            .get("categories", [])
        )
        compact = [
            {
                "name": cat["name"],
                **({"subcategories": [s["name"] for s in cat["subcategories"]]}
                   if cat.get("subcategories") else {}),
            }
            for cat in cats
        ]
        _output(compact)
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


# ------------------------------------------------------------------
# Thread commands
# ------------------------------------------------------------------


@threads_app.command("list")
@_run_async
async def threads_list(
    course: int | None = typer.Option(None, "--course", help="Course ID."),
    limit: int = typer.Option(30, help="Max threads."),
    offset: int = typer.Option(0, help="Pagination offset."),
    sort: str = typer.Option("new", help="Sort: new, top, trending."),
    filter: str | None = typer.Option(None, help="Filter: unanswered, unresolved, mine, following."),
):
    """List threads in a course (compact summaries)."""
    from edstem_mcp._helpers import _summarise_threads
    course_id = _require_course(course)
    c = await _client()
    try:
        result = await c.list_threads(course_id, limit=limit, offset=offset, sort=sort, filter=filter)
        _output({"threads": _summarise_threads(result.get("threads", []), course_id)})
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


@threads_app.command("get")
@_run_async
async def threads_get(
    ref: str = typer.Argument(..., help="Thread ID, #number (needs --course), or Ed URL."),
    course: int | None = typer.Option(None, "--course", help="Course ID (required for #number refs)."),
):
    """Read a thread's full content. Accepts thread_id, #number, or URL."""
    import re
    from edstem_mcp._helpers import _trim_thread_detail

    c = await _client()
    try:
        url_match = re.search(r"https?://edstem\.org/(?:\w+/)?courses/(\d+)/discussion/(\d+)", ref)
        if url_match:
            thread_id = int(url_match.group(2))
            result = await c.get_thread(thread_id)
        elif ref.startswith("#"):
            course_id = _require_course(course)
            number = int(ref[1:])
            result = await c.get_course_thread(course_id, number)
        else:
            result = await c.get_thread(int(ref))
        _output(_trim_thread_detail(result))
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


@threads_app.command("search")
@_run_async
async def threads_search(
    query: str = typer.Argument(..., help="Search keywords."),
    course: int | None = typer.Option(None, "--course", help="Course ID."),
    limit: int = typer.Option(20, help="Max results."),
):
    """Search threads by keyword."""
    from edstem_mcp._helpers import _summarise_threads
    course_id = _require_course(course)
    c = await _client()
    try:
        result = await c.search_threads(course_id, query, limit=limit)
        _output({"threads": _summarise_threads(result.get("threads", []), course_id)})
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


@threads_app.command("create")
@_run_async
async def threads_create(
    title: str = typer.Option(..., "--title", help="Thread title."),
    content: str = typer.Option(..., "--content", help="Body in Ed XML."),
    course: int | None = typer.Option(None, "--course", help="Course ID."),
    type: str = typer.Option("post", help="post, question, or announcement."),
    category: str = typer.Option("", help="Category name."),
    subcategory: str = typer.Option("", help="Subcategory name."),
    private: bool = typer.Option(False, "--private", help="Staff-only."),
    anonymous: bool = typer.Option(False, "--anonymous", help="Hide author."),
):
    """Create a new thread."""
    from edstem_mcp._helpers import _thread_url
    course_id = _require_course(course)
    c = await _client()
    try:
        result = await c.create_thread(
            course_id, title=title, content=content, type=type,
            category=category, subcategory=subcategory,
            is_private=private, is_anonymous=anonymous,
        )
        t = result.get("thread", result)
        _output({"id": t.get("id"), "number": t.get("number"), "title": t.get("title"), "url": _thread_url(course_id, t["id"])})
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


@threads_app.command("edit")
@_run_async
async def threads_edit(
    thread_id: int = typer.Argument(..., help="Thread ID."),
    title: str | None = typer.Option(None, "--title", help="New title."),
    content: str | None = typer.Option(None, "--content", help="New body in Ed XML."),
    category: str | None = typer.Option(None, "--category", help="New category."),
    subcategory: str | None = typer.Option(None, "--subcategory", help="New subcategory."),
):
    """Edit a thread's title, content, or category."""
    from edstem_mcp._helpers import _thread_url
    c = await _client()
    try:
        result = await c.edit_thread(thread_id, title=title, content=content, category=category, subcategory=subcategory)
        t = result.get("thread", result)
        resp: dict = {"id": t.get("id"), "number": t.get("number"), "title": t.get("title")}
        if t.get("course_id") and t.get("id"):
            resp["url"] = _thread_url(t["course_id"], t["id"])
        _output(resp)
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


@threads_app.command("reply")
@_run_async
async def threads_reply(
    thread_id: int = typer.Argument(..., help="Thread ID."),
    content: str = typer.Option(..., "--content", help="Reply body in Ed XML."),
    type: str = typer.Option("comment", help="comment or answer."),
    private: bool = typer.Option(False, "--private", help="Staff-only."),
    anonymous: bool = typer.Option(False, "--anonymous", help="Hide author."),
    parent_id: int | None = typer.Option(None, "--parent", help="Nest under this comment ID."),
):
    """Reply to a thread."""
    c = await _client()
    try:
        result = await c.reply_to_thread(
            thread_id, content=content, type=type,
            is_private=private, is_anonymous=anonymous, parent_id=parent_id,
        )
        cm = result.get("comment", result)
        _output({"id": cm.get("id"), "thread_id": cm.get("thread_id"), "type": cm.get("type")})
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


@threads_app.command("delete")
@_run_async
async def threads_delete(
    thread_id: int = typer.Argument(..., help="Thread ID."),
):
    """Permanently delete a thread."""
    c = await _client()
    try:
        await c.delete_thread(thread_id)
        _output({"deleted": True})
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


# ------------------------------------------------------------------
# Moderation command (consolidated)
# ------------------------------------------------------------------


@threads_app.command("mod")
@_run_async
async def threads_mod(
    thread_id: int = typer.Argument(..., help="Thread ID."),
    lock: bool = typer.Option(False, "--lock", help="Lock thread."),
    unlock: bool = typer.Option(False, "--unlock", help="Unlock thread."),
    pin: bool = typer.Option(False, "--pin", help="Pin thread."),
    unpin: bool = typer.Option(False, "--unpin", help="Unpin thread."),
    endorse: bool = typer.Option(False, "--endorse", help="Endorse thread."),
    unendorse: bool = typer.Option(False, "--unendorse", help="Remove endorsement."),
    duplicate_of: int | None = typer.Option(None, "--duplicate-of", help="Mark as duplicate of this thread ID."),
    unmark_duplicate: bool = typer.Option(False, "--unmark-duplicate", help="Remove duplicate mark."),
    accept: int | None = typer.Option(None, "--accept", help="Accept this comment ID as answer."),
):
    """Moderate a thread: lock, pin, endorse, mark duplicate, accept answer."""
    for a, b, label in [
        (lock, unlock, "--lock/--unlock"),
        (pin, unpin, "--pin/--unpin"),
        (endorse, unendorse, "--endorse/--unendorse"),
    ]:
        if a and b:
            _output({"error": f"{label} are mutually exclusive."})
            raise typer.Exit(1)
    if duplicate_of is not None and unmark_duplicate:
        _output({"error": "--duplicate-of and --unmark-duplicate are mutually exclusive."})
        raise typer.Exit(1)
    c = await _client()
    actions: list[str] = []
    try:
        if lock:
            await c.lock_thread(thread_id)
            actions.append("locked")
        if unlock:
            await c.unlock_thread(thread_id)
            actions.append("unlocked")
        if pin:
            await c.pin_thread(thread_id)
            actions.append("pinned")
        if unpin:
            await c.unpin_thread(thread_id)
            actions.append("unpinned")
        if endorse:
            await c.endorse_thread(thread_id)
            actions.append("endorsed")
        if unendorse:
            await c.unendorse_thread(thread_id)
            actions.append("unendorsed")
        if duplicate_of is not None:
            await c.mark_duplicate(thread_id, duplicate_of)
            actions.append(f"marked_duplicate_of_{duplicate_of}")
        if unmark_duplicate:
            await c.unmark_duplicate(thread_id)
            actions.append("unmarked_duplicate")
        if accept is not None:
            await c.accept_answer(thread_id, accept)
            actions.append(f"accepted_{accept}")
        if not actions:
            _output({"error": "No moderation flags provided."})
            raise typer.Exit(1)
        _output({"thread_id": thread_id, "actions": actions})
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


@threads_app.command("recategorise")
@_run_async
async def threads_recategorise(
    thread_ids: list[int] = typer.Argument(..., help="Thread IDs to move."),
    category: str = typer.Option(..., "--category", help="Target category."),
    subcategory: str = typer.Option("", "--subcategory", help="Target subcategory."),
):
    """Move threads to a new category."""
    c = await _client()
    try:
        results = []
        for tid in thread_ids:
            try:
                await c.edit_thread(tid, category=category, subcategory=subcategory)
                results.append({"id": tid, "ok": True})
            except EdAPIError as e:
                results.append({"id": tid, "ok": False, "error": e.message})
        succeeded = sum(1 for r in results if r["ok"])
        failed = [r for r in results if not r["ok"]]
        summary: dict = {"updated": succeeded, "total": len(thread_ids)}
        if failed:
            summary["failed"] = failed
        _output(summary)
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


# ------------------------------------------------------------------
# Attendance commands
# ------------------------------------------------------------------


@attendance_app.command("list")
@_run_async
async def attendance_list(
    course: int | None = typer.Option(None, "--course", help="Course ID."),
):
    """List attendance sessions."""
    from edstem_mcp._helpers import _EVENT_SUMMARY_KEYS
    course_id = _require_course(course)
    c = await _client()
    try:
        result = await c.list_attendance_sessions(course_id)
        sessions = [
            {k: e[k] for k in _EVENT_SUMMARY_KEYS if k in e}
            for e in result.get("events", [])
        ]
        _output({"sessions": sessions})
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


@attendance_app.command("get")
@_run_async
async def attendance_get(
    event_id: int = typer.Argument(..., help="Session ID."),
):
    """Get full session detail including check-ins."""
    from edstem_mcp._helpers import _EVENT_DETAIL_KEYS, _CHECK_IN_KEYS
    c = await _client()
    try:
        event_data = await c.get_attendance_session(event_id)
        checkins_data = await c.list_check_ins(event_id=event_id)
        ev = event_data.get("event", event_data)
        session = {k: ev[k] for k in _EVENT_DETAIL_KEYS if k in ev}
        session["check_ins"] = [
            {k: ci[k] for k in _CHECK_IN_KEYS if k in ci}
            for ci in checkins_data.get("check_ins", [])
        ]
        _output(session)
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


@attendance_app.command("create")
@_run_async
async def attendance_create(
    title: str = typer.Option(..., "--title", help="Session title."),
    course: int | None = typer.Option(None, "--course", help="Course ID."),
    start: str | None = typer.Option(None, "--start", help="Start time in ISO 8601."),
    hidden: bool = typer.Option(False, "--hidden", help="Hide from students."),
):
    """Create a new attendance session."""
    course_id = _require_course(course)
    c = await _client()
    try:
        result = await c.create_attendance_session(course_id, title=title, start=start, is_hidden=hidden)
        ev = result.get("event", result)
        _output({"id": ev.get("id"), "title": ev.get("title")})
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


@attendance_app.command("update")
@_run_async
async def attendance_update(
    event_id: int = typer.Argument(..., help="Session ID."),
    title: str | None = typer.Option(None, "--title", help="New title."),
    closed: bool | None = typer.Option(None, "--closed/--no-closed", help="Close or reopen."),
    hidden: bool | None = typer.Option(None, "--hidden/--no-hidden", help="Hide or show."),
):
    """Update an attendance session."""
    c = await _client()
    try:
        fields: dict = {}
        if title is not None:
            fields["title"] = title
        if closed is not None:
            fields["is_closed"] = closed
        if hidden is not None:
            fields["is_hidden"] = hidden
        if not fields:
            _output({"error": "No fields to update."})
            raise typer.Exit(1)
        result = await c.update_attendance_session(event_id, **fields)
        ev = result.get("event", result)
        _output({"id": ev.get("id"), "title": ev.get("title"), "is_closed": ev.get("is_closed")})
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


@attendance_app.command("delete")
@_run_async
async def attendance_delete(
    event_id: int = typer.Argument(..., help="Session ID."),
):
    """Permanently delete an attendance session."""
    c = await _client()
    try:
        await c.delete_attendance_session(event_id)
        _output({"deleted": True})
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


@attendance_app.command("check-in")
@_run_async
async def attendance_check_in(
    event_id: int = typer.Argument(..., help="Session ID."),
    users: str = typer.Option(..., "--users", help="Comma-separated user IDs."),
    kind: str = typer.Option("present", "--kind", help="present, late, excused, absent."),
):
    """Manually check in students."""
    from edstem_mcp._helpers import _CHECK_IN_KEYS
    valid_kinds = {"present", "late", "excused", "absent"}
    if kind not in valid_kinds:
        _output({"error": f"kind must be one of: {', '.join(sorted(valid_kinds))}"})
        raise typer.Exit(1)
    try:
        user_ids = [int(u.strip()) for u in users.split(",")]
    except ValueError:
        _output({"error": "All user IDs must be integers."})
        raise typer.Exit(1)
    c = await _client()
    try:
        result = await c.manual_check_in(event_id, user_ids=user_ids, kind=kind)
        check_ins = [
            {k: ci[k] for k in _CHECK_IN_KEYS if k in ci}
            for ci in result.get("check_ins", [])
        ]
        _output({"checked_in": len(check_ins), "check_ins": check_ins})
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


@attendance_app.command("undo")
@_run_async
async def attendance_undo(
    event_id: int = typer.Argument(..., help="Session ID."),
    users: str = typer.Option(..., "--users", help="Comma-separated user IDs."),
):
    """Remove check-in records for users."""
    try:
        user_ids = [int(u.strip()) for u in users.split(",")]
    except ValueError:
        _output({"error": "All user IDs must be integers."})
        raise typer.Exit(1)
    c = await _client()
    try:
        await c.undo_check_in(event_id, user_ids=user_ids)
        _output({"event_id": event_id, "removed": len(user_ids)})
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


@attendance_app.command("analytics")
@_run_async
async def attendance_analytics(
    course: int | None = typer.Option(None, "--course", help="Course ID."),
):
    """Get combined attendance report for a course."""
    from edstem_mcp._helpers import _EVENT_SUMMARY_KEYS, _CHECK_IN_KEYS
    course_id = _require_course(course)
    c = await _client()
    try:
        result = await c.get_attendance_analytics(course_id)
        sessions = [
            {k: e[k] for k in _EVENT_SUMMARY_KEYS if k in e}
            for e in result.get("events", [])
        ]
        check_ins = [
            {k: ci[k] for k in _CHECK_IN_KEYS if k in ci}
            for ci in result.get("check_ins", [])
        ]
        _output({"sessions": sessions, "check_ins": check_ins})
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


# ------------------------------------------------------------------
# Comment commands
# ------------------------------------------------------------------


@comments_app.command("edit")
@_run_async
async def comments_edit(
    comment_id: int = typer.Argument(..., help="Comment ID."),
    content: str = typer.Option(..., "--content", help="New body in Ed XML."),
):
    """Edit a comment's content."""
    c = await _client()
    try:
        result = await c.edit_comment(comment_id, content=content)
        cm = result.get("comment", result)
        _output({"id": cm.get("id"), "thread_id": cm.get("thread_id")})
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


@comments_app.command("delete")
@_run_async
async def comments_delete(
    comment_id: int = typer.Argument(..., help="Comment ID."),
):
    """Permanently delete a comment."""
    c = await _client()
    try:
        await c.delete_comment(comment_id)
        _output({"deleted": True})
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


# ------------------------------------------------------------------
# User commands
# ------------------------------------------------------------------


@users_app.command("activity")
@_run_async
async def users_activity(
    user_id: int = typer.Argument(..., help="User ID (from courses users)."),
    course: int | None = typer.Option(None, "--course", help="Course ID."),
    filter: str = typer.Option("all", help="thread, answer, comment, or all."),
    limit: int = typer.Option(30, help="Max entries."),
    offset: int = typer.Option(0, help="Pagination offset."),
):
    """View a user's activity in a course."""
    from edstem_mcp._helpers import _ACTIVITY_THREAD_KEYS, _ACTIVITY_COMMENT_KEYS
    course_id = _require_course(course)
    c = await _client()
    try:
        result = await c.get_user_activity(user_id, course_id, limit=limit, offset=offset, filter=filter)
        items = []
        for entry in result.get("items", []):
            kind = entry.get("type", "")
            value = entry.get("value", {})
            if kind == "thread":
                items.append({"kind": "thread", **{k: value[k] for k in _ACTIVITY_THREAD_KEYS if k in value}})
            elif kind in ("comment", "answer"):
                items.append({"kind": kind, **{k: value[k] for k in _ACTIVITY_COMMENT_KEYS if k in value}})
        _output(items)
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()


# ------------------------------------------------------------------
# File commands
# ------------------------------------------------------------------


@files_app.command("upload")
@_run_async
async def files_upload(
    file_path: str = typer.Argument(..., help="Path to file."),
):
    """Upload a file to Ed Discussion."""
    from edstem_mcp._helpers import _UPLOAD_KEYS
    p = Path(file_path)
    if not p.exists():
        _output({"error": f"File not found: {file_path}"})
        raise typer.Exit(1)
    c = await _client()
    try:
        result = await c.upload_file(p)
        _output({k: result[k] for k in _UPLOAD_KEYS if k in result})
    except EdAPIError as e:
        _output({"error": e.message})
        raise typer.Exit(1)
    finally:
        await c.close()
