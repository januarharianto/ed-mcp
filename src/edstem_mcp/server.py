"""MCP server for Ed Discussion (edstem.org)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
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


# ======================================================================
# User & Courses
# ======================================================================


@mcp.tool()
async def get_user() -> str:
    """Get the authenticated user's profile (name, role). Call list_courses instead to see enrolled courses."""
    try:
        result = await _get_client().get_user()
        u = result.get("user", result)
        profile: dict = {
            "name": u.get("name"),
            "role": u.get("role"),
            "course_count": len(result.get("courses", [])),
        }
        if not _pii_enabled():
            profile["id"] = u.get("id")
            profile["email"] = u.get("email")
        return _json(profile)
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def list_courses() -> str:
    """List all courses the user is enrolled in. Call this first to find a course_id before using other tools. Returns id, code, name, year, session, and status."""
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


@mcp.tool()
async def get_course_stats(course_id: int) -> str:
    """Get a quick course overview for daily review. Returns enrollment count, number of unanswered questions, number of unresolved threads, and top categories by volume.

    Args:
        course_id: The course ID (use list_courses to find it).
    """
    try:
        client = _get_client()

        async def _count_filtered(filt: str) -> int:
            """Page through a filter and return the total count."""
            total = 0
            offset = 0
            while True:
                r = await client.list_threads(
                    course_id, limit=100, offset=offset, filter=filt
                )
                batch = r.get("threads", [])
                total += len(batch)
                if len(batch) < 100:
                    break
                offset += 100
            return total

        stats_task = client.get_course_stats(course_id)
        unanswered_task = _count_filtered("unanswered")
        unresolved_task = _count_filtered("unresolved")
        recent_task = client.list_threads(course_id, limit=100, offset=0)

        stats, unanswered, unresolved, recent = await asyncio.gather(
            stats_task, unanswered_task, unresolved_task, recent_task
        )

        # Category distribution from recent threads
        cat_counts: dict[str, int] = {}
        for t in recent.get("threads", []):
            cat = t.get("category", "") or "Uncategorised"
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
        top_categories = sorted(cat_counts.items(), key=lambda x: -x[1])

        enrollment = stats.get("stats", {}).get("student_enrollment_count", 0)

        return _json({
            "enrollment": enrollment,
            "unanswered": unanswered,
            "unresolved": unresolved,
            "top_categories": [
                {"name": name, "count": count} for name, count in top_categories
            ],
        })
    except EdAPIError as e:
        return f"Error: {e.message}"


# ======================================================================
# Threads
# ======================================================================


@mcp.tool()
async def list_categories(course_id: int) -> str:
    """List the available thread categories and subcategories in a course. Use this to find valid category names before creating or recategorising threads.

    Args:
        course_id: The course ID (use list_courses to find it).
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
    """Browse threads in a course. Returns compact summaries (no full content). Use get_thread or get_course_thread to read a specific thread's content.

    Args:
        course_id: The course ID (use list_courses to find it).
        limit: Max threads to return (default 30).
        offset: Pagination offset (use with limit to page through results).
        sort: Sort order — "new" for most recent, "top" for most voted, or "trending" for currently active.
        filter: Narrow results — "unanswered" for questions needing a response, "unresolved" for threads with open follow-ups, "mine" for your own threads, "following" for threads you follow.
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
    """Read a thread's full content, comments, and answers. Use this when you have a thread ID from list_threads or search_threads results. For looking up a thread by its number (e.g. #42), use get_course_thread instead.

    Args:
        thread_id: The global thread ID (from list_threads or search_threads results).
    """
    try:
        result = await _get_client().get_thread(thread_id)
        return _json(_trim_thread_detail(result))
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def get_course_thread(course_id: int, thread_number: int) -> str:
    """Read a thread by its number as shown in the Ed UI (e.g. #42, #220). Use this when someone refers to a thread by number. Returns full content, comments, and answers.

    Args:
        course_id: The course ID (use list_courses to find it).
        thread_number: The thread number as shown in the UI (e.g. 42 for thread #42).
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
    """Read a thread by pasting its Ed Discussion URL. Use this when someone shares a link to a thread. Returns full content, comments, and answers.

    Args:
        url: Full Ed Discussion thread URL (e.g. https://edstem.org/au/courses/12345/discussion/220).
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
    """Mark a comment as the accepted answer on a question thread. Use get_thread first to find the comment_id of the correct answer.

    Args:
        thread_id: The global thread ID.
        comment_id: The ID of the comment to accept (from get_thread results).
    """
    try:
        result = await _get_client().accept_answer(thread_id, comment_id)
        t = result.get("thread", result)
        return _json({"id": t.get("id"), "accepted_id": t.get("accepted_id")})
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def mark_duplicate(thread_id: int, original_thread_id: int) -> str:
    """Mark a thread as a duplicate of another thread. Use this when a question has already been answered elsewhere to point students to the original.

    Args:
        thread_id: The global ID of the duplicate thread.
        original_thread_id: The global ID of the original thread it duplicates.
    """
    try:
        await _get_client().mark_duplicate(thread_id, original_thread_id)
        return _json({"id": thread_id, "duplicate_id": original_thread_id})
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def unmark_duplicate(thread_id: int) -> str:
    """Remove the duplicate mark from a thread, restoring it as a standalone thread.

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
    """Search threads in a course by keyword. Returns compact summaries. Use get_thread to read the full content of a result.

    Args:
        course_id: The course ID (use list_courses to find it).
        query: Search keywords (e.g. "peer review", "exam", "deadline").
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
    """Create a new thread in a course. Wrap content in Ed XML: <document version="2.0"><paragraph>Your text here</paragraph></document>. Use list_categories to find valid category names.

    Args:
        course_id: The course ID (use list_courses to find it).
        title: Thread title.
        content: Thread body in Ed XML format.
        type: "post" for a discussion, "question" for a question that can be answered, or "announcement" for a course-wide notice.
        category: Category name (use list_categories to see options).
        subcategory: Subcategory name (optional).
        is_private: If true, only staff can see the thread.
        is_anonymous: If true, the author's name is hidden.
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
    """Edit an existing thread's title, content, or category. Only provided fields are updated; omitted fields are left unchanged.

    Args:
        thread_id: The global thread ID.
        title: New title (leave empty to keep current).
        content: New body in Ed XML format (leave empty to keep current).
        category: New category name (leave empty to keep current; use list_categories for valid names).
        subcategory: New subcategory name (leave empty to keep current).
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
async def bulk_recategorise(
    thread_ids: list[int],
    category: str,
    subcategory: str = "",
) -> str:
    """Move multiple threads to a new category at once. Use search_threads or list_threads to find thread IDs, and list_categories for valid category names.

    Args:
        thread_ids: List of global thread IDs to move.
        category: Target category name (use list_categories to see options).
        subcategory: Target subcategory name (optional).
    """
    client = _get_client()

    async def _update(tid: int) -> dict:
        try:
            await client.edit_thread(tid, category=category, subcategory=subcategory)
            return {"id": tid, "ok": True}
        except EdAPIError as e:
            return {"id": tid, "ok": False, "error": e.message}

    results = await asyncio.gather(*[_update(tid) for tid in thread_ids])
    succeeded = sum(1 for r in results if r["ok"])
    failed = [r for r in results if not r["ok"]]
    summary: dict = {"updated": succeeded, "total": len(thread_ids)}
    if failed:
        summary["failed"] = failed
    return _json(summary)


@mcp.tool()
async def delete_thread(thread_id: int) -> str:
    """Permanently delete a thread and all its comments. This cannot be undone.

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
    """Lock a thread to prevent new comments. Useful after a question is resolved or a discussion is concluded.

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
    """Unlock a thread to allow new comments again.

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
    """Pin a thread so it stays at the top of the course feed. Good for important announcements or FAQs.

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
    """Unpin a thread from the top of the course feed.

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
    """Endorse a thread with an instructor badge to signal it contains good content or a correct answer.

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
    """Remove the instructor endorsement badge from a thread.

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
async def get_enrollment_counts(course_id: int) -> str:
    """Get a quick headcount of students, staff, and admins in a course. Use this to answer "how many students" questions without loading the full user list.

    Args:
        course_id: The course ID (use list_courses to find it).
    """
    try:
        result = await _get_client().list_users(course_id)
        counts: dict[str, int] = {}
        for u in result.get("users", []):
            role = u.get("course_role", u.get("role", "unknown"))
            counts[role] = counts.get(role, 0) + 1
        counts["total"] = sum(counts.values())
        return _json(counts)
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def list_users(
    course_id: int,
    role: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> str:
    """List enrolled users in a course. Use get_enrollment_counts for a quick headcount. Use this when you need names or user IDs (e.g. before calling get_user_activity).

    Args:
        course_id: The course ID (use list_courses to find it).
        role: Filter by role — "student", "staff", or "admin". Omit for all roles.
        limit: Max users to return (default 50).
        offset: Pagination offset (use with limit to page through results).
    """
    try:
        result = await _get_client().list_users(course_id)
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
        return _json({"users": page, "total": total, "offset": offset, "limit": limit})
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
    """See what a specific user has been posting and commenting in a course. Use list_users first to find the user_id.

    Args:
        user_id: The user ID (use list_users to find it).
        course_id: The course ID (use list_courses to find it).
        limit: Max entries to return (default 30).
        offset: Pagination offset (use with limit to page through results).
        filter: Narrow results — "thread" for threads only, "answer" for answers only, "comment" for comments only, or "all" for everything.
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
    """Upload a file from your computer to Ed Discussion and get a URL you can use in thread content or comments.

    Args:
        file_path: Absolute path to the file on your computer.
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
    """Reply to a thread with a comment or answer. Wrap content in Ed XML: <document version="2.0"><paragraph>Your text here</paragraph></document>

    Args:
        thread_id: The global thread ID.
        content: Reply body in Ed XML format.
        type: "comment" for a general reply, or "answer" for a direct answer to a question thread.
        is_private: If true, only staff can see this reply.
        is_anonymous: If true, the author's name is hidden.
        parent_id: To nest this reply under an existing comment, pass that comment's ID here.
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
    """Edit an existing comment's content. Replaces the entire body with the new content in Ed XML format.

    Args:
        comment_id: The comment ID (from get_thread results).
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
    """Permanently delete a comment. This cannot be undone.

    Args:
        comment_id: The comment ID (from get_thread results).
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
