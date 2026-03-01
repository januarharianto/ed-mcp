# CLI + Skills Plugin Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Typer CLI (`ed`) alongside the existing MCP server, plus 4 Claude Code skills, packaged as a Claude Code plugin.

**Architecture:** Extract shared helpers (key sets, trimming, PII, JSON output) from `server.py` into `_helpers.py` so both `server.py` and the new `cli.py` can reuse them without importing FastMCP. The CLI wraps `EdClient` methods with the same response trimming as MCP tools, outputting compact JSON by default. Four skills teach Claude how to use the CLI.

**Tech Stack:** Python, Typer, httpx, FastMCP (existing stack + typer as only new dep)

---

### Task 1: Extract shared helpers from `server.py` into `_helpers.py`

**Files:**
- Create: `src/edstem_mcp/_helpers.py`
- Modify: `src/edstem_mcp/server.py`

**Why:** `cli.py` needs the key-set constants, trimming functions, PII helpers, and JSON serialiser from `server.py`. But importing `server.py` triggers FastMCP initialisation. Extract the shared code into `_helpers.py` so both consumers can import it cleanly.

**Step 1: Create `_helpers.py`**

Create `src/edstem_mcp/_helpers.py` containing everything that's NOT FastMCP-specific or tool-function-specific. Move these items from `server.py`:

```python
"""Shared helpers for response trimming, PII stripping, and serialisation."""

from __future__ import annotations

import json
import os
import re


def _json(data: dict) -> str:
    """Compact JSON serialisation for tool responses."""
    return json.dumps(data, separators=(",", ":"), default=str)


def _thread_url(course_id: int, thread_id: int) -> str:
    """Build an Ed Discussion URL for a thread."""
    region = os.environ.get("ED_REGION", "us")
    return f"https://edstem.org/{region}/courses/{course_id}/discussion/{thread_id}"


# ------------------------------------------------------------------
# PII helpers
# ------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


def _pii_enabled() -> bool:
    """Return True unless ED_STRIP_PII is explicitly 'false'."""
    return os.environ.get("ED_STRIP_PII", "").lower() != "false"


def _scrub_emails(text: str) -> str:
    """Replace email addresses in text with [email]."""
    return _EMAIL_RE.sub("[email]", text)


def _strip_user_pii(user: dict) -> dict:
    """Keep only name and course_role from a user dict."""
    return {k: user[k] for k in ("name", "course_role") if k in user}


# ------------------------------------------------------------------
# Key sets for response trimming
# ------------------------------------------------------------------

# Keys kept in thread summaries (list/search). Full content via get_thread.
_THREAD_SUMMARY_KEYS = {
    "id", "number", "type", "title", "category", "subcategory",
    "created_at", "is_pinned", "is_private", "is_endorsed",
    "is_answered", "is_locked", "reply_count", "vote_count",
    "view_count", "unresolved_count",
}

# Keys kept in full thread detail responses (get_thread / get_course_thread).
_THREAD_DETAIL_KEYS = {
    "id", "number", "type", "title", "content", "category", "subcategory",
    "course_id", "accepted_id", "duplicate_id",
    "created_at", "is_pinned", "is_private", "is_endorsed",
    "is_answered", "is_locked", "is_anonymous",
    "reply_count", "vote_count", "unresolved_count",
}

_COMMENT_KEYS = {
    "id", "parent_id", "type", "content",
    "is_endorsed", "is_private", "is_resolved", "is_anonymous",
    "vote_count", "created_at",
}

_USER_KEYS = {"id", "name", "course_role"}

_UPLOAD_KEYS = {"url", "filename"}

# Keys kept in attendance session summaries (list_attendance_sessions).
_EVENT_SUMMARY_KEYS = {
    "id", "title", "is_closed", "is_hidden", "start", "created_at",
}

# Keys kept in full attendance session detail.
_EVENT_DETAIL_KEYS = {
    "id", "course_id", "title", "content", "is_closed", "is_hidden",
    "no_screen", "start", "qr_expiry", "index", "created_at",
    "grade_passback_scoring_mode", "grade_passback_scale_to",
}

# Keys kept in check-in records.
_CHECK_IN_KEYS = {
    "event_id", "user_id", "checked_in_by", "kind", "method", "created_at",
}

_ACTIVITY_THREAD_KEYS = {
    "id", "type", "course_id", "title", "category", "subcategory",
    "document", "created_at",
}
_ACTIVITY_COMMENT_KEYS = {
    "id", "type", "thread_id", "thread_title", "thread_category",
    "document", "created_at",
}


# ------------------------------------------------------------------
# Trimming functions
# ------------------------------------------------------------------


def _summarise_threads(threads: list[dict], course_id: int) -> list[dict]:
    """Extract compact summaries from a list of thread dicts."""
    return [
        {
            **{k: t[k] for k in _THREAD_SUMMARY_KEYS if k in t},
            "user": t.get("user", {}).get("name", ""),
            "url": _thread_url(course_id, t["id"]),
        }
        for t in threads
    ]


def _trim_comment(c: dict) -> dict:
    """Strip a comment/answer to essential fields, recursing into replies."""
    trimmed = {k: c[k] for k in _COMMENT_KEYS if k in c}
    strip_pii = _pii_enabled()
    if c.get("user"):
        trimmed["user"] = (
            _strip_user_pii(c["user"]) if strip_pii
            else {k: c["user"][k] for k in _USER_KEYS if k in c["user"]}
        )
    if strip_pii and "content" in trimmed:
        trimmed["content"] = _scrub_emails(trimmed["content"])
    nested = c.get("comments", [])
    if nested:
        trimmed["comments"] = [_trim_comment(r) for r in nested]
    return trimmed


def _trim_thread_detail(data: dict) -> dict:
    """Trim a full thread API response to essential fields."""
    t = data.get("thread", data)
    trimmed = {k: t[k] for k in _THREAD_DETAIL_KEYS if k in t}
    if "id" in trimmed and "course_id" in trimmed:
        trimmed["url"] = _thread_url(trimmed["course_id"], trimmed["id"])
    strip_pii = _pii_enabled()
    if t.get("user"):
        trimmed["user"] = (
            _strip_user_pii(t["user"]) if strip_pii
            else {k: t["user"][k] for k in _USER_KEYS if k in t["user"]}
        )
    for key in ("answers", "comments"):
        if t.get(key):
            trimmed[key] = [_trim_comment(c) for c in t[key]]
    if strip_pii and "content" in trimmed:
        trimmed["content"] = _scrub_emails(trimmed["content"])
    # Compact users list (participants)
    users = data.get("users", [])
    if users:
        trimmed["users"] = [
            _strip_user_pii(u) if strip_pii
            else {k: u[k] for k in _USER_KEYS if k in u}
            for u in users
        ]
    return trimmed
```

**Step 2: Update `server.py` to import from `_helpers.py`**

Replace the moved code (lines 31–164 and lines 731–738) with imports. Delete all the moved functions and constants. **Keep `import re` in `server.py`** — it's still needed for `_ED_URL_RE` (line 372). Delete `import os` from `server.py` — it's no longer used directly (only in the moved helpers). Add at the top of `server.py` (after the existing imports):

```python
from edstem_mcp._helpers import (
    _json,
    _thread_url,
    _pii_enabled,
    _scrub_emails,
    _strip_user_pii,
    _THREAD_SUMMARY_KEYS,
    _THREAD_DETAIL_KEYS,
    _COMMENT_KEYS,
    _USER_KEYS,
    _UPLOAD_KEYS,
    _EVENT_SUMMARY_KEYS,
    _EVENT_DETAIL_KEYS,
    _CHECK_IN_KEYS,
    _ACTIVITY_THREAD_KEYS,
    _ACTIVITY_COMMENT_KEYS,
    _summarise_threads,
    _trim_comment,
    _trim_thread_detail,
)
```

Keep in `server.py`: the `FastMCP` setup, `_get_client`, `_ED_URL_RE` (needs `import re`), and all `@mcp.tool()` functions.

**Step 3: Verify nothing is broken**

Run: `cd "/Users/jhar8696/Sydney Uni Dropbox/Januar Harianto/projects/automation/edstem" && uv run python -c "from edstem_mcp.server import mcp; print(len(mcp._tool_manager._tools), 'tools')"`
Expected: `38 tools`

Run: `cd "/Users/jhar8696/Sydney Uni Dropbox/Januar Harianto/projects/automation/edstem" && uv run pytest tests/ -q`
Expected: 70 passed, 7 failed (same as before — the 7 failures are pre-existing test bugs unrelated to this refactor: `test_summarise_threads_keeps_only_summary_keys`, `test_get_thread_by_url_valid`, `test_create_thread`, `test_list_users`, `test_get_user_activity`, `test_list_users_pii_maps_user_id`, `test_list_users_pii_disabled`). Verify no NEW failures are introduced.

**Step 4: Commit**

```
refactor: extract shared helpers into _helpers.py for CLI reuse
```

---

### Task 2: Add typer dependency and create CLI scaffold

**Files:**
- Modify: `pyproject.toml` (add typer dep + console script)
- Create: `src/edstem_mcp/cli.py`

**Step 1: Add typer dependency and console script entry point**

In `pyproject.toml`, add `typer>=0.9.0` to the dependencies list and add a `[project.scripts]` section:

```toml
[project]
name = "edstem-mcp"
dynamic = ["version"]
description = "MCP server for Ed Discussion (edstem.org)"
requires-python = ">=3.11"
dependencies = [
    "mcp[cli]>=1.0.0",
    "httpx>=0.27.0",
    "typer>=0.9.0",
]

[project.scripts]
ed = "edstem_mcp.cli:app"
```

Run: `cd "/Users/jhar8696/Sydney Uni Dropbox/Januar Harianto/projects/automation/edstem" && uv sync`

**Step 2: Create CLI scaffold**

Create `src/edstem_mcp/cli.py`:

```python
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
```

**Step 3: Verify CLI loads**

Run: `cd "/Users/jhar8696/Sydney Uni Dropbox/Januar Harianto/projects/automation/edstem" && uv run ed usage`
Expected: prints the compact CLI reference

Run: `cd "/Users/jhar8696/Sydney Uni Dropbox/Januar Harianto/projects/automation/edstem" && uv run ed --help`
Expected: shows Typer help with subcommands

**Step 4: Commit**

```
feat: add CLI scaffold with typer, config management, and usage command
```

---

### Task 3: Add course and thread CLI commands

**Files:**
- Modify: `src/edstem_mcp/cli.py` (append after config commands)

**Step 1: Add course commands**

Append to `cli.py`:

```python

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
```

**Step 2: Add thread commands**

Append to `cli.py`:

```python

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
```

**Step 3: Verify thread commands work**

Run: `cd "/Users/jhar8696/Sydney Uni Dropbox/Januar Harianto/projects/automation/edstem" && source .env && uv run ed courses list --code ENVX`
Expected: JSON array of ENVX courses

Run: `cd "/Users/jhar8696/Sydney Uni Dropbox/Januar Harianto/projects/automation/edstem" && source .env && uv run ed config set-course 31545 && uv run ed threads list --limit 2`
Expected: JSON with 2 thread summaries

**Step 4: Commit**

```
feat: add course and thread CLI commands
```

---

### Task 4: Add moderation, attendance, and remaining CLI commands

**Files:**
- Modify: `src/edstem_mcp/cli.py` (append after thread commands)

**Step 1: Add moderation command**

Append to `cli.py`:

```python

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
```

**Step 2: Add attendance commands**

Append to `cli.py`:

```python

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
    user_ids = [int(u.strip()) for u in users.split(",")]
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
    user_ids = [int(u.strip()) for u in users.split(",")]
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
```

**Step 3: Add comment, user, and file commands**

Append to `cli.py`:

```python

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
```

**Step 4: Verify all commands load**

Run: `cd "/Users/jhar8696/Sydney Uni Dropbox/Januar Harianto/projects/automation/edstem" && uv run ed --help`
Expected: shows all subcommand groups

Run: `cd "/Users/jhar8696/Sydney Uni Dropbox/Januar Harianto/projects/automation/edstem" && uv run ed threads --help`
Expected: lists all thread subcommands (list, get, search, create, edit, reply, delete, mod, recategorise)

Run: `cd "/Users/jhar8696/Sydney Uni Dropbox/Januar Harianto/projects/automation/edstem" && uv run ed attendance --help`
Expected: lists all attendance subcommands

**Step 5: Commit**

```
feat: add moderation, attendance, and remaining CLI commands
```

---

### Task 5: Create plugin manifest and MCP config

**Files:**
- Create: `.claude-plugin/plugin.json`
- Create: `.mcp.json`

**Step 1: Create plugin manifest**

Create directory `.claude-plugin/` and file `.claude-plugin/plugin.json`:

```json
{
  "name": "edstem",
  "version": "0.5.0",
  "description": "Ed Discussion tools for Claude Code — browse threads, manage attendance, moderate discussions.",
  "author": {
    "name": "Januar Harianto"
  },
  "repository": "https://github.com/jhar8696/edstem-mcp"
}
```

**Step 2: Create MCP config**

Create `.mcp.json` at the repo root:

```json
{
  "edstem": {
    "command": "uv",
    "args": ["run", "python", "-m", "edstem_mcp.server"],
    "env": {
      "ED_API_TOKEN": "${ED_API_TOKEN}"
    }
  }
}
```

**Step 3: Update `.gitignore` if needed**

Check if `.claude-plugin/` and `.mcp.json` are gitignored. They should NOT be — these are distributable plugin files. If they're caught by existing ignore patterns, add explicit exceptions.

**Step 4: Verify**

Run: `cd "/Users/jhar8696/Sydney Uni Dropbox/Januar Harianto/projects/automation/edstem" && cat .claude-plugin/plugin.json | python3 -m json.tool`
Expected: valid JSON

Run: `cd "/Users/jhar8696/Sydney Uni Dropbox/Januar Harianto/projects/automation/edstem" && cat .mcp.json | python3 -m json.tool`
Expected: valid JSON

**Step 5: Commit**

```
feat: add Claude Code plugin manifest and MCP config
```

---

### Task 6: Create 4 skills

**Files:**
- Create: `skills/ed-threads/SKILL.md`
- Create: `skills/ed-attendance/SKILL.md`
- Create: `skills/ed-moderation/SKILL.md`
- Create: `skills/ed-admin/SKILL.md`

**Step 1: Create ed-threads skill**

Create `skills/ed-threads/SKILL.md`:

```markdown
---
name: ed-threads
description: >
  Use when the user asks about Ed Discussion threads — browsing, searching,
  creating posts/questions/announcements, replying, or editing thread content.
  Requires the `ed` CLI (install: uv pip install edstem-mcp).
allowed-tools:
  - Bash
---

# Ed Discussion Thread Management

## Setup
Ensure `ED_API_TOKEN` is set. Set a default course: `ed config set-course <id>`

## Commands

**Browse threads:**
```
ed threads list [--course ID] [--sort new|top|trending] [--filter unanswered|unresolved] [--limit N]
```

**Read a thread** (accepts thread ID, #number, or URL):
```
ed threads get <thread_id>
ed threads get '#42' --course 31545
ed threads get 'https://edstem.org/au/courses/31545/discussion/123'
```

**Search:**
```
ed threads search "exam deadline" [--course ID] [--limit N]
```

**Create a thread:**
```
ed threads create --title "Week 5 Reminder" --content '<document version="2.0"><paragraph>Content here</paragraph></document>' [--type post|question|announcement] [--category "General"]
```

**Edit:**
```
ed threads edit <id> [--title "New Title"] [--content '<xml>'] [--category "Labs"]
```

**Reply:**
```
ed threads reply <thread_id> --content '<document version="2.0"><paragraph>Reply text</paragraph></document>' [--type comment|answer]
```

**Delete:**
```
ed threads delete <thread_id>
```

## Content Format
Ed uses XML: `<document version="2.0"><paragraph>Your text</paragraph></document>`

Cross-reference threads: `<link href="https://edstem.org/au/courses/{course_id}/discussion/{number}">#N</link>`

## Output
All commands return compact JSON. Errors: `{"error": "message"}`.
```

**Step 2: Create ed-attendance skill**

Create `skills/ed-attendance/SKILL.md`:

```markdown
---
name: ed-attendance
description: >
  Use when the user asks about Ed Discussion attendance — creating sessions,
  marking students present/late/excused/absent, viewing check-ins, or
  generating attendance reports. Requires the `ed` CLI.
allowed-tools:
  - Bash
---

# Ed Discussion Attendance Management

## Setup
Ensure `ED_API_TOKEN` is set. Set a default course: `ed config set-course <id>`

## Commands

**List sessions:**
```
ed attendance list [--course ID]
```

**Session details (includes check-ins):**
```
ed attendance get <event_id>
```

**Create session:**
```
ed attendance create --title "Week 3 Tutorial" [--course ID] [--start "2026-03-01T09:00:00+11:00"] [--hidden]
```

**Update session (close/reopen, hide/show, rename):**
```
ed attendance update <event_id> [--title "New Name"] [--closed] [--no-closed] [--hidden] [--no-hidden]
```

**Delete session:**
```
ed attendance delete <event_id>
```

**Manual check-in:**
```
ed attendance check-in <event_id> --users 12345,67890 [--kind present|late|excused|absent]
```
Find user IDs first: `ed courses users [--role student]`

**Undo check-in:**
```
ed attendance undo <event_id> --users 12345,67890
```

**Analytics (all sessions + all check-ins):**
```
ed attendance analytics [--course ID]
```

## Check-in Kinds
- `present` — attended (default)
- `late` — arrived late
- `excused` — excused absence
- `absent` — unexcused absence

## Workflow: Mark Attendance
1. `ed attendance list` — find the session ID
2. `ed courses users --role student` — find user IDs
3. `ed attendance check-in <event_id> --users <ids> --kind present`
```

**Step 3: Create ed-moderation skill**

Create `skills/ed-moderation/SKILL.md`:

```markdown
---
name: ed-moderation
description: >
  Use when the user asks to moderate Ed Discussion threads — locking, pinning,
  endorsing, marking duplicates, accepting answers, or recategorising threads.
  Requires the `ed` CLI.
allowed-tools:
  - Bash
---

# Ed Discussion Thread Moderation

## Setup
Ensure `ED_API_TOKEN` is set.

## Moderate a Thread

All moderation actions use one command with flags:

```
ed threads mod <thread_id> [FLAGS]
```

**Flags:**
- `--lock` / `--unlock` — prevent/allow new comments
- `--pin` / `--unpin` — stick to top of feed
- `--endorse` / `--unendorse` — instructor badge
- `--duplicate-of <original_id>` — mark as duplicate
- `--unmark-duplicate` — remove duplicate mark
- `--accept <comment_id>` — accept answer on question thread

**Combine flags:**
```
ed threads mod 12345 --lock --pin
ed threads mod 12345 --duplicate-of 11111
ed threads mod 12345 --accept 67890
```

## Recategorise Threads

Move multiple threads to a new category:
```
ed threads recategorise 111 222 333 --category "Labs" [--subcategory "Lab 1"]
```

## Edit/Delete Comments
```
ed comments edit <comment_id> --content '<document version="2.0"><paragraph>Updated</paragraph></document>'
ed comments delete <comment_id>
```

## Find Valid Categories
```
ed courses categories [--course ID]
```
```

**Step 4: Create ed-admin skill**

Create `skills/ed-admin/SKILL.md`:

```markdown
---
name: ed-admin
description: >
  Use when the user asks about Ed Discussion course details — listing courses,
  checking enrollment, viewing student activity, reviewing course statistics,
  or managing categories. Requires the `ed` CLI.
allowed-tools:
  - Bash
---

# Ed Discussion Course Administration

## Setup
```
export ED_API_TOKEN=your_token
ed config set-course <course_id>    # save default course
```

## Commands

**List courses:**
```
ed courses list [--status active|archived] [--year 2026] [--code ENVX]
```

**Course overview:**
```
ed courses stats [--course ID]
```
Returns: enrollment count, unanswered questions, unresolved threads, top categories.

**List enrolled users:**
```
ed courses users [--course ID] [--role student|staff|admin] [--limit N] [--offset N]
```

**View user activity:**
```
ed users activity <user_id> [--course ID] [--filter thread|answer|comment|all] [--limit N]
```

**List categories:**
```
ed courses categories [--course ID]
```

**Upload a file:**
```
ed files upload /path/to/file.pdf
```
Returns: `{"url": "...", "filename": "..."}` — use the URL in thread content.

## Quick Reference
```
ed usage    # print compact CLI reference
```
```

**Step 5: Verify skill files exist**

Run: `cd "/Users/jhar8696/Sydney Uni Dropbox/Januar Harianto/projects/automation/edstem" && find skills -name "SKILL.md" | sort`
Expected:
```
skills/ed-admin/SKILL.md
skills/ed-attendance/SKILL.md
skills/ed-moderation/SKILL.md
skills/ed-threads/SKILL.md
```

**Step 6: Commit**

```
feat: add 4 Claude Code skills for CLI mode
```

---

### Task 7: Smoke test CLI against live course

**Step 1: Verify tool counts**

Run: `cd "/Users/jhar8696/Sydney Uni Dropbox/Januar Harianto/projects/automation/edstem" && uv run python -c "from edstem_mcp.server import mcp; print(len(mcp._tool_manager._tools), 'tools')"`
Expected: `38 tools` (unchanged)

Run: `cd "/Users/jhar8696/Sydney Uni Dropbox/Januar Harianto/projects/automation/edstem" && uv run pytest tests/ -q`
Expected: 70 passed, 7 failed (same pre-existing failures — no new regressions)

**Step 2: Smoke test CLI commands against course 31545**

Run each command sequence using `source .env` first:

```bash
cd "/Users/jhar8696/Sydney Uni Dropbox/Januar Harianto/projects/automation/edstem"
source .env

# 1. Config
uv run ed config set-course 31545

# 2. Courses
uv run ed courses list --code ENVX
uv run ed courses stats
uv run ed courses users --limit 3
uv run ed courses categories

# 3. Threads
uv run ed threads list --limit 2
uv run ed threads search "attendance" --limit 2

# 4. Attendance
uv run ed attendance list
uv run ed attendance analytics

# 5. Usage
uv run ed usage
```

All should return valid JSON (no Python tracebacks, no empty output).

**Step 3: If all pass, no additional commits needed. If failures, fix and re-verify.**

---
