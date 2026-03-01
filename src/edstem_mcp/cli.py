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
