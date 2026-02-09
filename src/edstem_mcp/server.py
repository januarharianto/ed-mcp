"""MCP server for Ed Discussion (edstem.org)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from edstem_mcp.client import EdAPIError, EdClient

logger = logging.getLogger(__name__)

mcp = FastMCP("edstem")

# Lazy-initialised client (created on first tool call)
_client: EdClient | None = None


def _get_client() -> EdClient:
    global _client
    if _client is None:
        _client = EdClient()
    return _client


def _json(data: dict) -> str:
    """Compact JSON serialisation for tool responses."""
    return json.dumps(data, separators=(",", ":"), default=str)


# Keys kept in thread summaries (list/search). Full content via get_thread.
_THREAD_SUMMARY_KEYS = {
    "id", "number", "type", "title", "category", "subcategory",
    "created_at", "is_pinned", "is_private", "is_endorsed",
    "is_answered", "is_locked", "reply_count", "vote_count",
    "view_count", "unresolved_count",
}


def _summarise_threads(threads: list[dict]) -> list[dict]:
    """Extract compact summaries from a list of thread dicts."""
    return [
        {
            **{k: t[k] for k in _THREAD_SUMMARY_KEYS if k in t},
            "user": t.get("user", {}).get("name", ""),
        }
        for t in threads
    ]


# Keys kept in full thread detail responses (get_thread / get_course_thread).
_THREAD_DETAIL_KEYS = {
    "id", "number", "type", "title", "content", "category", "subcategory",
    "course_id", "user_id", "accepted_id", "duplicate_id",
    "created_at", "is_pinned", "is_private", "is_endorsed",
    "is_answered", "is_locked", "is_anonymous",
    "reply_count", "vote_count", "unresolved_count",
}

_COMMENT_KEYS = {
    "id", "user_id", "parent_id", "type", "content",
    "is_endorsed", "is_private", "is_resolved", "is_anonymous",
    "vote_count", "created_at",
}

_USER_KEYS = {"id", "name", "course_role"}


def _trim_comment(c: dict) -> dict:
    """Strip a comment/answer to essential fields, recursing into replies."""
    trimmed = {k: c[k] for k in _COMMENT_KEYS if k in c}
    if c.get("user"):
        trimmed["user"] = {k: c["user"][k] for k in _USER_KEYS if k in c["user"]}
    nested = c.get("comments", [])
    if nested:
        trimmed["comments"] = [_trim_comment(r) for r in nested]
    return trimmed


def _trim_thread_detail(data: dict) -> dict:
    """Trim a full thread API response to essential fields."""
    t = data.get("thread", data)
    trimmed = {k: t[k] for k in _THREAD_DETAIL_KEYS if k in t}
    if t.get("user"):
        trimmed["user"] = {k: t["user"][k] for k in _USER_KEYS if k in t["user"]}
    for key in ("answers", "comments"):
        if t.get(key):
            trimmed[key] = [_trim_comment(c) for c in t[key]]
    # Compact users list (participants)
    users = data.get("users", [])
    if users:
        trimmed["users"] = [
            {k: u[k] for k in _USER_KEYS if k in u} for u in users
        ]
    return trimmed


# ======================================================================
# User & Courses
# ======================================================================


@mcp.tool()
async def get_user() -> str:
    """Get the authenticated user's profile. Use list_courses for enrolled courses."""
    try:
        result = await _get_client().get_user()
        u = result.get("user", result)
        profile = {
            "id": u.get("id"),
            "name": u.get("name"),
            "email": u.get("email"),
            "role": u.get("role"),
            "course_count": len(result.get("courses", [])),
        }
        return _json(profile)
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def list_courses() -> str:
    """List enrolled courses (compact). Returns id, code, name, year, session, and status only."""
    try:
        result = await _get_client().get_user()
        courses = [
            {
                "id": c["course"]["id"],
                "code": c["course"].get("code", ""),
                "name": c["course"].get("name", ""),
                "year": c["course"].get("year", ""),
                "session": c["course"].get("session", ""),
                "status": c["course"].get("status", ""),
            }
            for c in result.get("courses", [])
        ]
        return _json(courses)
    except EdAPIError as e:
        return f"Error: {e.message}"


# ======================================================================
# Threads
# ======================================================================


@mcp.tool()
async def list_categories(course_id: int) -> str:
    """List all thread categories and subcategories in a course.

    Args:
        course_id: The course ID.
    """
    try:
        result = await _get_client().get_course(course_id)
        course = result.get("course", result)
        cats = (
            course.get("settings", {})
            .get("discussion", {})
            .get("categories", [])
        )
        compact = [
            {
                "name": c["name"],
                **({"subcategories": [s["name"] for s in c["subcategories"]]}
                   if c.get("subcategories") else {}),
            }
            for c in cats
        ]
        return _json(compact)
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def list_threads(
    course_id: int,
    limit: int = 30,
    offset: int = 0,
    sort: str = "new",
    filter: str | None = None,
) -> str:
    """List threads in a course.

    Args:
        course_id: The course ID.
        limit: Max threads to return (default 30).
        offset: Pagination offset.
        sort: Sort order — "new", "top", or "trending".
        filter: Optional filter — "unresolved", "unanswered", "mine", "following".
    """
    try:
        result = await _get_client().list_threads(
            course_id, limit=limit, offset=offset, sort=sort, filter=filter
        )
        return _json({"threads": _summarise_threads(result.get("threads", []))})
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def get_thread(thread_id: int) -> str:
    """Get a thread by its global ID, including all comments and answers.

    Args:
        thread_id: The global thread ID.
    """
    try:
        result = await _get_client().get_thread(thread_id)
        return _json(_trim_thread_detail(result))
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def get_course_thread(course_id: int, thread_number: int) -> str:
    """Get a thread by its course-relative number (e.g. #42 as shown in the UI).

    Args:
        course_id: The course ID.
        thread_number: The course-relative thread number.
    """
    try:
        result = await _get_client().get_course_thread(course_id, thread_number)
        return _json(_trim_thread_detail(result))
    except EdAPIError as e:
        return f"Error: {e.message}"


_ED_URL_RE = re.compile(
    r"https?://edstem\.org/(?:\w+/)?courses/(\d+)/discussion/(\d+)"
)


@mcp.tool()
async def get_thread_by_url(url: str) -> str:
    """Get a thread by its Ed Discussion URL.

    Accepts URLs like https://edstem.org/au/courses/12345/discussion/220

    Args:
        url: Full Ed Discussion thread URL.
    """
    m = _ED_URL_RE.search(url)
    if not m:
        return "Error: Could not parse Ed thread URL. Expected format: https://edstem.org/.../courses/{id}/discussion/{number}"
    course_id, thread_number = int(m.group(1)), int(m.group(2))
    try:
        result = await _get_client().get_course_thread(course_id, thread_number)
        return _json(_trim_thread_detail(result))
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def accept_answer(thread_id: int, comment_id: int) -> str:
    """Mark a comment as the accepted answer on a question thread.

    Args:
        thread_id: The global thread ID.
        comment_id: The ID of the comment to accept as the answer.
    """
    try:
        result = await _get_client().accept_answer(thread_id, comment_id)
        t = result.get("thread", result)
        return _json({"id": t.get("id"), "accepted_id": t.get("accepted_id")})
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def mark_duplicate(thread_id: int, original_thread_id: int) -> str:
    """Mark a thread as a duplicate of another thread.

    Args:
        thread_id: The global ID of the thread to mark as duplicate.
        original_thread_id: The global ID of the original thread.
    """
    try:
        await _get_client().mark_duplicate(thread_id, original_thread_id)
        return _json({"id": thread_id, "duplicate_id": original_thread_id})
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def unmark_duplicate(thread_id: int) -> str:
    """Remove the duplicate mark from a thread.

    Args:
        thread_id: The global ID of the thread to unmark.
    """
    try:
        await _get_client().unmark_duplicate(thread_id)
        return _json({"id": thread_id, "duplicate_id": None})
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def search_threads(course_id: int, query: str, limit: int = 20) -> str:
    """Search threads in a course by keyword.

    Args:
        course_id: The course ID.
        query: Search keywords.
        limit: Max results (default 20).
    """
    try:
        result = await _get_client().search_threads(course_id, query, limit=limit)
        return _json({"threads": _summarise_threads(result.get("threads", []))})
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def create_thread(
    course_id: int,
    title: str,
    content: str,
    type: str = "post",
    category: str = "",
    subcategory: str = "",
    is_private: bool = False,
    is_anonymous: bool = False,
) -> str:
    """Create a new thread in a course.

    Content should be Ed XML format, e.g.:
    <document version="2.0"><paragraph>Hello world</paragraph></document>

    Args:
        course_id: The course ID.
        title: Thread title.
        content: Thread body in Ed XML format.
        type: Thread type — "post", "question", or "announcement".
        category: Category name.
        subcategory: Subcategory name.
        is_private: Whether the thread is private (visible to staff only).
        is_anonymous: Whether the thread is anonymous.
    """
    try:
        result = await _get_client().create_thread(
            course_id,
            title=title,
            content=content,
            type=type,
            category=category,
            subcategory=subcategory,
            is_private=is_private,
            is_anonymous=is_anonymous,
        )
        t = result.get("thread", result)
        return _json({"id": t.get("id"), "number": t.get("number"), "title": t.get("title")})
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def edit_thread(
    thread_id: int,
    title: str | None = None,
    content: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
) -> str:
    """Edit an existing thread.  Only provided fields are updated.

    Args:
        thread_id: The global thread ID.
        title: New title (optional).
        content: New body in Ed XML format (optional).
        category: New category (optional).
        subcategory: New subcategory (optional).
    """
    try:
        result = await _get_client().edit_thread(
            thread_id,
            title=title,
            content=content,
            category=category,
            subcategory=subcategory,
        )
        t = result.get("thread", result)
        return _json({"id": t.get("id"), "number": t.get("number"), "title": t.get("title")})
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def delete_thread(thread_id: int) -> str:
    """Delete a thread.

    Args:
        thread_id: The global thread ID.
    """
    try:
        await _get_client().delete_thread(thread_id)
        return "Thread deleted."
    except EdAPIError as e:
        return f"Error: {e.message}"


# ======================================================================
# Moderation
# ======================================================================


@mcp.tool()
async def lock_thread(thread_id: int) -> str:
    """Lock a thread (prevent new comments).

    Args:
        thread_id: The global thread ID.
    """
    try:
        await _get_client().lock_thread(thread_id)
        return "Thread locked."
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def unlock_thread(thread_id: int) -> str:
    """Unlock a thread.

    Args:
        thread_id: The global thread ID.
    """
    try:
        await _get_client().unlock_thread(thread_id)
        return "Thread unlocked."
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def pin_thread(thread_id: int) -> str:
    """Pin a thread to the top of the course feed.

    Args:
        thread_id: The global thread ID.
    """
    try:
        await _get_client().pin_thread(thread_id)
        return "Thread pinned."
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def unpin_thread(thread_id: int) -> str:
    """Unpin a thread.

    Args:
        thread_id: The global thread ID.
    """
    try:
        await _get_client().unpin_thread(thread_id)
        return "Thread unpinned."
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def endorse_thread(thread_id: int) -> str:
    """Endorse a thread (add instructor badge).

    Args:
        thread_id: The global thread ID.
    """
    try:
        await _get_client().endorse_thread(thread_id)
        return "Thread endorsed."
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def unendorse_thread(thread_id: int) -> str:
    """Remove endorsement from a thread.

    Args:
        thread_id: The global thread ID.
    """
    try:
        await _get_client().unendorse_thread(thread_id)
        return "Endorsement removed."
    except EdAPIError as e:
        return f"Error: {e.message}"


# ======================================================================
# Users & Analytics
# ======================================================================


@mcp.tool()
async def list_users(course_id: int) -> str:
    """List users enrolled in a course (compact: id, name, role).

    Args:
        course_id: The course ID.
    """
    try:
        result = await _get_client().list_users(course_id)
        users = [
            {
                "id": u.get("id"),
                "name": u.get("name"),
                "course_role": u.get("course_role", u.get("role", "")),
            }
            for u in result.get("users", [])
        ]
        return _json(users)
    except EdAPIError as e:
        return f"Error: {e.message}"


_ACTIVITY_THREAD_KEYS = {"id", "number", "type", "title", "category", "created_at"}
_ACTIVITY_COMMENT_KEYS = {"id", "type", "thread_id", "created_at"}


@mcp.tool()
async def get_user_activity(
    user_id: int,
    course_id: int,
    limit: int = 30,
    offset: int = 0,
    filter: str | None = None,
) -> str:
    """Get a user's activity in a course (compact thread/comment summaries).

    Args:
        user_id: The user ID.
        course_id: The course ID.
        limit: Max entries to return (default 30).
        offset: Pagination offset.
        filter: Optional filter — "all", "thread", "answer", "comment".
    """
    try:
        result = await _get_client().get_user_activity(
            user_id, course_id, limit=limit, offset=offset, filter=filter
        )
        items = []
        for entry in result.get("activity", []):
            if "thread" in entry:
                t = entry["thread"]
                items.append({
                    "kind": "thread",
                    **{k: t[k] for k in _ACTIVITY_THREAD_KEYS if k in t},
                })
            elif "comment" in entry:
                c = entry["comment"]
                items.append({
                    "kind": "comment",
                    **{k: c[k] for k in _ACTIVITY_COMMENT_KEYS if k in c},
                })
        return _json(items)
    except EdAPIError as e:
        return f"Error: {e.message}"


# ======================================================================
# Files
# ======================================================================


@mcp.tool()
async def upload_file(file_path: str) -> str:
    """Upload a local file to Ed and return its URL.

    Args:
        file_path: Absolute path to the file on disk.
    """
    try:
        p = Path(file_path)
        if not p.exists():
            return f"Error: File not found: {file_path}"
        result = await _get_client().upload_file(p)
        return _json(result)
    except EdAPIError as e:
        return f"Error: {e.message}"


# ======================================================================
# Comments / Replies
# ======================================================================


@mcp.tool()
async def reply_to_thread(
    thread_id: int,
    content: str,
    type: str = "comment",
    is_private: bool = False,
    is_anonymous: bool = False,
    parent_id: int | None = None,
) -> str:
    """Post a comment or answer on a thread.

    Content should be Ed XML format, e.g.:
    <document version="2.0"><paragraph>Great question!</paragraph></document>

    Args:
        thread_id: The global thread ID.
        content: Reply body in Ed XML format.
        type: "comment" or "answer".
        is_private: Whether the reply is private (visible to staff only).
        is_anonymous: Whether the reply is anonymous.
        parent_id: ID of an existing comment to nest this reply under (optional).
    """
    try:
        result = await _get_client().reply_to_thread(
            thread_id,
            content=content,
            type=type,
            is_private=is_private,
            is_anonymous=is_anonymous,
            parent_id=parent_id,
        )
        c = result.get("comment", result)
        return _json({"id": c.get("id"), "thread_id": c.get("thread_id"), "type": c.get("type")})
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def edit_comment(comment_id: int, content: str) -> str:
    """Edit an existing comment's content.

    Content should be Ed XML format.

    Args:
        comment_id: The comment ID.
        content: New body in Ed XML format.
    """
    try:
        result = await _get_client().edit_comment(comment_id, content=content)
        c = result.get("comment", result)
        return _json({"id": c.get("id"), "thread_id": c.get("thread_id")})
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def delete_comment(comment_id: int) -> str:
    """Delete a comment.

    Args:
        comment_id: The comment ID.
    """
    try:
        await _get_client().delete_comment(comment_id)
        return "Comment deleted."
    except EdAPIError as e:
        return f"Error: {e.message}"


# ======================================================================
# Entry point
# ======================================================================

if __name__ == "__main__":
    mcp.run()
