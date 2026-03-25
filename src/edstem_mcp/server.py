"""MCP server for Ed Discussion (edstem.org)."""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

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
from edstem_mcp import _index
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


def _cache_dir() -> Path:
    """Return the cache directory for index data."""
    base = Path(os.environ.get("ED_INDEX_PATH", "~/.cache/edstem-mcp")).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    return base


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
async def list_courses(
    status: str | None = None,
    year: str | None = None,
    code: str | None = None,
) -> str:
    """List courses the user is enrolled in. Call this first to find a course_id before using other tools. Returns id, code, name, year, session, and status.

    Args:
        status: Filter by status — "active", "archived", or "inactive". Omit for all.
        year: Filter by year (e.g. "2025"). Omit for all years.
        code: Filter by course code substring, case-insensitive (e.g. "ENVX"). Omit for all codes.
    """
    try:
        result = await _get_client().get_user()
        code_upper = code.upper() if code else None
        courses = []
        for c in result.get("courses", []):
            course = c["course"]
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
        return _json(courses)
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def get_course_stats(course_id: int) -> str:
    """Get a quick course overview for daily review. Returns enrollment count, number of unanswered questions, number of unresolved threads, number of threads with new follow-up replies, and top categories by volume.

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
        new_replies_task = _count_filtered("new_replies")
        recent_task = client.list_threads(course_id, limit=100, offset=0)

        stats, unanswered, unresolved, new_replies, recent = await asyncio.gather(
            stats_task, unanswered_task, unresolved_task, new_replies_task, recent_task
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
            "new_replies": new_replies,
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
    limit: int = 50,
    offset: int = 0,
    sort: str = "new",
    filter: str | None = None,
    category: str | None = None,
) -> str:
    """Browse threads in a course. Returns compact summaries (no full content). Use get_thread or get_course_thread to read a specific thread's content. For keyword searches, prefer search_threads instead.

    Args:
        course_id: The course ID (use list_courses to find it).
        limit: Max threads to return (default 50, max 100).
        offset: Pagination offset (use with limit to page through results).
        sort: Sort order — "new" for most recent, "top" for most voted, or "trending" for currently active.
        filter: Narrow results. Triage filters: "unanswered", "unresolved", "new_replies". Personal filters: "unread", "starred", "watching", "mine", "following". Visibility filters: "private", "public", "staff", "endorsed". Invalid values silently return empty results.
        category: Filter by category name (case-insensitive). Only returns threads in this category.
    """
    try:
        fetch_limit = min(limit * 2, 100) if category else min(limit, 100)
        result = await _get_client().list_threads(
            course_id, limit=fetch_limit, offset=offset, sort=sort, filter=filter
        )
        threads = result.get("threads", [])
        if category:
            cat_lower = category.lower()
            threads = [t for t in threads if (t.get("category", "") or "").lower() == cat_lower]
            threads = threads[:limit]
        return _json({"threads": _summarise_threads(threads, course_id)})
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def get_thread(thread_id: int) -> str:
    """Read a thread's full content, comments, and answers. Use this when you have a thread ID (a large number like 2785693) from list_threads or search_threads results. For looking up a thread by its UI number (e.g. #42), use get_course_thread instead.

    Args:
        thread_id: The global thread ID (a large number from list_threads or search_threads, not the #number shown in the UI).
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
        url: Full Ed Discussion thread URL (e.g. https://edstem.org/au/courses/20849/discussion/2785693).
    """
    m = _ED_URL_RE.search(url)
    if not m:
        return "Error: Could not parse Ed thread URL. Expected format: https://edstem.org/.../courses/{course_id}/discussion/{thread_id}"
    thread_id = int(m.group(2))
    try:
        result = await _get_client().get_thread(thread_id)
        return _json(_trim_thread_detail(result))
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def accept_answer(thread_id: int, comment_id: int) -> str:
    """Mark a comment as the accepted answer on a question thread. Use get_thread first to find the comment_id of the correct answer.

    Args:
        thread_id: The global thread ID (from list_threads or search_threads).
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
        thread_id: The global ID of the duplicate thread (from list_threads or search_threads).
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
        thread_id: The global ID of the thread to unmark (from list_threads or search_threads).
    """
    try:
        await _get_client().unmark_duplicate(thread_id)
        return _json({"id": thread_id, "duplicate_id": None})
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def search_threads(
    course_id: int,
    query: str,
    limit: int = 20,
    type: str | None = None,
    exclude_pinned: bool = False,
) -> str:
    """Search threads in a course by keyword (searches titles and body content). Returns compact summaries. Use get_thread to read the full content of a result. Prefer this over list_threads when looking for specific topics.

    Args:
        course_id: The course ID (use list_courses to find it).
        query: Search keywords (e.g. "peer review", "exam", "deadline").
        limit: Max results (default 20).
        type: Filter by thread type — "question", "post", or "announcement". Omit for all types.
        exclude_pinned: If true, exclude pinned threads from results (useful to skip announcements). Default false.
    """
    try:
        # Fetch extra results to account for post-filtering
        fetch_limit = min(limit * 3, 100) if (type or exclude_pinned) else min(limit, 100)
        result = await _get_client().search_threads(course_id, query, limit=fetch_limit)
        threads = result.get("threads", [])
        if exclude_pinned:
            threads = [t for t in threads if not t.get("is_pinned")]
        if type:
            threads = [t for t in threads if t.get("type") == type]
        threads = threads[:limit]
        return _json({"threads": _summarise_threads(threads, course_id)})
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
        return _json({
            "id": t.get("id"),
            "number": t.get("number"),
            "title": t.get("title"),
            "url": _thread_url(course_id, t["id"]),
        })
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
        thread_id: The global thread ID (from list_threads or search_threads).
        title: New title (omit to keep current).
        content: New body in Ed XML format (omit to keep current).
        category: New category name (omit to keep current; use list_categories for valid names).
        subcategory: New subcategory name (omit to keep current).
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
        resp: dict = {"id": t.get("id"), "number": t.get("number"), "title": t.get("title")}
        if t.get("course_id") and t.get("id"):
            resp["url"] = _thread_url(t["course_id"], t["id"])
        return _json(resp)
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def bulk_recategorise(
    thread_ids: list[int],
    category: str,
    subcategory: str = "",
    dry_run: bool = False,
) -> str:
    """Move multiple threads to a new category at once. Use search_threads or list_threads to find thread IDs, and list_categories for valid category names. Set dry_run=true to preview changes without applying them.

    Args:
        thread_ids: List of global thread IDs to move.
        category: Target category name (use list_categories to see options).
        subcategory: Target subcategory name (optional).
        dry_run: If true, show what would change without applying. Default false.
    """
    client = _get_client()

    if dry_run:
        async def _preview(tid: int) -> dict:
            try:
                result = await client.get_thread(tid)
                t = result.get("thread", result)
                return {
                    "id": tid,
                    "number": t.get("number"),
                    "title": t.get("title"),
                    "from": t.get("category", "") + (
                        f" > {t['subcategory']}" if t.get("subcategory") else ""
                    ),
                    "to": category + (f" > {subcategory}" if subcategory else ""),
                }
            except EdAPIError as e:
                return {"id": tid, "error": e.message}

        changes = await asyncio.gather(*[_preview(tid) for tid in thread_ids])
        return _json({"dry_run": True, "would_update": len(changes), "changes": changes})

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
        thread_id: The global thread ID (from list_threads or search_threads).
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
        thread_id: The global thread ID (from list_threads or search_threads).
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
        thread_id: The global thread ID (from list_threads or search_threads).
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
        thread_id: The global thread ID (from list_threads or search_threads).
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
        thread_id: The global thread ID (from list_threads or search_threads).
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
        thread_id: The global thread ID (from list_threads or search_threads).
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
        thread_id: The global thread ID (from list_threads or search_threads).
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
    """Get a quick headcount for a course. Returns student count and total enrolled. Use this to answer "how many students" questions without loading the full user list. For per-role breakdowns (staff, admin), use list_users with a role filter.

    Args:
        course_id: The course ID (use list_courses to find it).
    """
    try:
        client = _get_client()
        # Try lightweight enrollment endpoint first (no individual user data)
        try:
            result = await client.get_enrollment_stats(course_id)
            if result.get("total_users") is not None:
                return _json({
                    "students": result.get("total_students", 0),
                    "total": result.get("total_users", 0),
                })
        except EdAPIError:
            pass  # Fall back to full user list
        # Fallback: fetch all users and count by role
        result = await client.list_users(course_id)
        counts = {}
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


@mcp.tool()
async def get_user_activity(
    user_id: int,
    course_id: int,
    limit: int = 30,
    offset: int = 0,
    filter: str = "all",
) -> str:
    """See what a specific user has been posting and commenting in a course. Use list_users first to find the user_id.

    Args:
        user_id: The user ID (use list_users to find it).
        course_id: The course ID (use list_courses to find it).
        limit: Max entries to return (default 30).
        offset: Pagination offset (use with limit to page through results).
        filter: Narrow results — "thread" for threads only, "answer" for answers only, "comment" for comments only, or "all" for everything (default).
    """
    try:
        result = await _get_client().get_user_activity(
            user_id, course_id, limit=limit, offset=offset, filter=filter
        )
        items = []
        for entry in result.get("items", []):
            kind = entry.get("type", "")
            value = entry.get("value", {})
            if kind == "thread":
                items.append({
                    "kind": "thread",
                    **{k: value[k] for k in _ACTIVITY_THREAD_KEYS if k in value},
                })
            elif kind in ("comment", "answer"):
                items.append({
                    "kind": kind,
                    **{k: value[k] for k in _ACTIVITY_COMMENT_KEYS if k in value},
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
        return _json({k: result[k] for k in _UPLOAD_KEYS if k in result})
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def upload_file_url(url: str) -> str:
    """Upload a file from a URL directly to Ed Discussion without downloading it locally first. Returns the Ed CDN URL you can use in thread content or comments.

    Args:
        url: Public URL of the file to upload (e.g. an image or document URL from the web).
    """
    try:
        result = await _get_client().upload_file_url(url)
        return _json({k: result[k] for k in _UPLOAD_KEYS if k in result})
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def download_file(url: str) -> str:
    """Download a file from an Ed Discussion CDN URL to a temporary local path. Returns the local file path so you can read or view it.

    Args:
        url: The Ed CDN URL (e.g. https://static.au.edusercontent.com/files/...).
    """
    if "edusercontent.com" not in url and "edstem.org" not in url:
        return "Error: URL must be from edusercontent.com or edstem.org"
    try:
        import tempfile
        tmp = Path(tempfile.mktemp(prefix="ed_"))
        dest, filename = await _get_client().download_file(url, tmp)
        return _json({"path": str(dest), "filename": filename, "size": dest.stat().st_size})
    except EdAPIError as e:
        return f"Error: {e.message}"


_IMAGE_SRC_RE = re.compile(r'<image\s[^>]*src="([^"]+)"')
_FILE_URL_RE = re.compile(r'<file\s[^>]*url="([^"]+)"')


@mcp.tool()
async def download_thread_files(thread_id: int) -> str:
    """Download all images and file attachments from a thread's content, answers, and comments to temporary local files. Returns local file paths so you can read or view them.

    Args:
        thread_id: The global thread ID (from list_threads or search_threads results).
    """
    try:
        import tempfile
        result = await _get_client().get_thread(thread_id)
        t = result.get("thread", result)

        # Collect all content from thread + comments + answers
        all_content = [t.get("content", "")]
        for key in ("answers", "comments"):
            for c in t.get(key, []):
                all_content.append(c.get("content", ""))
                for nested in c.get("comments", []):
                    all_content.append(nested.get("content", ""))

        urls = []
        for text in all_content:
            urls.extend(("image", u) for u in _IMAGE_SRC_RE.findall(text))
            urls.extend(("file", u) for u in _FILE_URL_RE.findall(text))

        if not urls:
            return _json({"files": [], "message": "No images or files found in this thread."})

        # Download all concurrently
        client = _get_client()
        tmpdir = Path(tempfile.mkdtemp(prefix="ed_files_"))

        async def _dl(i: int, kind: str, url: str) -> dict:
            try:
                placeholder = tmpdir / f"{kind}_{i}"
                dest, filename = await client.download_file(url, placeholder)
                return {"path": str(dest), "filename": filename, "type": kind, "size": dest.stat().st_size}
            except EdAPIError as e:
                return {"url": url, "type": kind, "error": e.message}

        files = await asyncio.gather(*[_dl(i, k, u) for i, (k, u) in enumerate(urls)])
        return _json({"files": files, "total": len(files)})
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
        thread_id: The global thread ID (from list_threads or search_threads).
        content: Reply body in Ed XML format.
        type: "comment" for a general reply, or "answer" for a direct answer (only works on "question" type threads).
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
        resp: dict = {"id": c.get("id"), "thread_id": c.get("thread_id"), "type": c.get("type")}
        if c.get("course_id"):
            resp["url"] = _thread_url(c["course_id"], c.get("thread_id", thread_id))
        return _json(resp)
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
# Attendance
# ======================================================================


@mcp.tool()
async def list_attendance_sessions(course_id: int) -> str:
    """List attendance sessions in a course. Returns compact summaries. Use get_attendance_session to see full details.

    Args:
        course_id: The course ID (use list_courses to find it).
    """
    try:
        result = await _get_client().list_attendance_sessions(course_id)
        sessions = [
            {k: e[k] for k in _EVENT_SUMMARY_KEYS if k in e}
            for e in result.get("events", [])
        ]
        return _json({"sessions": sessions})
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def get_attendance_session(event_id: int) -> str:
    """Get full details of an attendance session, including its check-ins.

    Args:
        event_id: The session ID (from list_attendance_sessions results).
    """
    try:
        client = _get_client()
        event_data, checkins_data = await asyncio.gather(
            client.get_attendance_session(event_id),
            client.list_check_ins(event_id=event_id),
        )
        ev = event_data.get("event", event_data)
        session = {k: ev[k] for k in _EVENT_DETAIL_KEYS if k in ev}
        session["check_ins"] = [
            {k: ci[k] for k in _CHECK_IN_KEYS if k in ci}
            for ci in checkins_data.get("check_ins", [])
        ]
        return _json(session)
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def create_attendance_session(
    course_id: int,
    title: str,
    start: str | None = None,
    is_hidden: bool = False,
) -> str:
    """Create a new attendance session in a course.

    Args:
        course_id: The course ID (use list_courses to find it).
        title: Session title (e.g. "Week 3 Tutorial").
        start: Optional start time in ISO 8601 format (e.g. "2026-03-01T09:00:00+11:00"). Defaults to now.
        is_hidden: If true, the session is hidden from students.
    """
    try:
        result = await _get_client().create_attendance_session(
            course_id, title=title, start=start, is_hidden=is_hidden,
        )
        ev = result.get("event", result)
        return _json({"id": ev.get("id"), "title": ev.get("title")})
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def update_attendance_session(
    event_id: int,
    title: str | None = None,
    is_closed: bool | None = None,
    is_hidden: bool | None = None,
) -> str:
    """Update an attendance session. Use this to close/reopen, hide/unhide, or rename a session.

    Args:
        event_id: The session ID (from list_attendance_sessions results).
        title: New title (omit to keep current).
        is_closed: Set true to close the session, false to reopen.
        is_hidden: Set true to hide from students, false to show.
    """
    try:
        fields: dict = {}
        if title is not None:
            fields["title"] = title
        if is_closed is not None:
            fields["is_closed"] = is_closed
        if is_hidden is not None:
            fields["is_hidden"] = is_hidden
        if not fields:
            return "Error: No fields to update. Provide at least one of: title, is_closed, is_hidden."
        result = await _get_client().update_attendance_session(event_id, **fields)
        ev = result.get("event", result)
        return _json({
            "id": ev.get("id"),
            "title": ev.get("title"),
            "is_closed": ev.get("is_closed"),
        })
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def delete_attendance_session(event_id: int) -> str:
    """Permanently delete an attendance session and all its check-ins. This cannot be undone.

    Args:
        event_id: The session ID (from list_attendance_sessions results).
    """
    try:
        await _get_client().delete_attendance_session(event_id)
        return "Attendance session deleted."
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def list_check_ins(
    course_id: int | None = None,
    event_id: int | None = None,
) -> str:
    """List attendance check-ins. Provide either course_id for all check-ins across sessions, or event_id for a single session.

    Args:
        course_id: The course ID — returns check-ins across all sessions.
        event_id: A specific session ID — returns only that session's check-ins.
    """
    if course_id is None and event_id is None:
        return "Error: Provide either course_id or event_id."
    try:
        result = await _get_client().list_check_ins(
            course_id=course_id, event_id=event_id,
        )
        check_ins = [
            {k: ci[k] for k in _CHECK_IN_KEYS if k in ci}
            for ci in result.get("check_ins", [])
        ]
        return _json({"check_ins": check_ins})
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def manual_check_in(
    event_id: int,
    user_ids: list[int],
    kind: str = "present",
) -> str:
    """Manually mark students' attendance for a session. Use list_users to find user IDs.

    Args:
        event_id: The session ID (from list_attendance_sessions results).
        user_ids: List of user IDs to check in.
        kind: Attendance status — "present", "late", "excused", or "absent".
    """
    valid_kinds = {"present", "late", "excused", "absent"}
    if kind not in valid_kinds:
        return f"Error: kind must be one of: {', '.join(sorted(valid_kinds))}"
    try:
        result = await _get_client().manual_check_in(
            event_id, user_ids=user_ids, kind=kind,
        )
        check_ins = [
            {k: ci[k] for k in _CHECK_IN_KEYS if k in ci}
            for ci in result.get("check_ins", [])
        ]
        return _json({"checked_in": len(check_ins), "check_ins": check_ins})
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def undo_check_in(
    event_id: int,
    user_ids: list[int],
) -> str:
    """Remove check-in records for specific users in a session. This undoes both manual and self check-ins.

    Args:
        event_id: The session ID.
        user_ids: List of user IDs whose check-ins to remove.
    """
    try:
        await _get_client().undo_check_in(event_id, user_ids=user_ids)
        return _json({"event_id": event_id, "removed": len(user_ids)})
    except EdAPIError as e:
        return f"Error: {e.message}"


@mcp.tool()
async def get_attendance_analytics(course_id: int) -> str:
    """Get a combined attendance report for a course — all sessions with all check-ins. Useful for generating attendance summaries.

    Args:
        course_id: The course ID (use list_courses to find it).
    """
    try:
        result = await _get_client().get_attendance_analytics(course_id)
        sessions = [
            {k: e[k] for k in _EVENT_SUMMARY_KEYS if k in e}
            for e in result.get("events", [])
        ]
        check_ins = [
            {k: ci[k] for k in _CHECK_IN_KEYS if k in ci}
            for ci in result.get("check_ins", [])
        ]
        return _json({"sessions": sessions, "check_ins": check_ins})
    except EdAPIError as e:
        return f"Error: {e.message}"


# ======================================================================
# Local Search Index
# ======================================================================


@mcp.tool()
async def sync_index(course_id: int) -> str:
    """Sync the local search index for a course. Downloads all threads and builds an in-memory search index for fast local search. Takes ~2-3 seconds. Call this before search_index, or to refresh stale data.

    Args:
        course_id: The course ID (use list_courses to find it).
    """
    import time
    start = time.monotonic()
    try:
        threads = await _get_client().get_discussion_threads_json(course_id)
    except EdAPIError as e:
        if e.status_code == 403:
            return "Error: Index sync failed. This endpoint may require staff or admin access."
        return f"Error: {e.message}"
    except Exception as e:
        return f"Error: Failed to download thread data. {e}"

    # Cache to disk
    cache = _cache_dir()
    cache_path = cache / f"{course_id}.json.gz"
    with gzip.open(cache_path, "wt", encoding="utf-8") as f:
        json.dump(threads, f)

    # Build in-memory index
    count = _index.build(course_id, threads)

    now = datetime.now(timezone.utc).isoformat()
    meta = {"last_synced": now, "thread_count": count}
    (cache / f"{course_id}.meta.json").write_text(json.dumps(meta))

    elapsed = round(time.monotonic() - start, 2)
    return _json({"course_id": course_id, "threads_indexed": count,
                   "elapsed_seconds": elapsed, "last_synced": now})


@mcp.tool()
async def search_index(
    course_id: int,
    query: str,
    limit: int = 20,
    category: str | None = None,
    type: str | None = None,
    has_staff_reply: bool | None = None,
    is_answered: bool | None = None,
) -> str:
    """Search the local index for a course. Returns BM25-ranked results with full content for top results. If no index exists, rebuilds from cache or triggers a sync.

    Args:
        course_id: The course ID (use list_courses to find it).
        query: Search query. Supports phrases ("peer review"), prefix (assign*), boolean (AND/OR/NOT), and column-specific (title:exam, staff_replies:deadline). Implicit AND between terms.
        limit: Max results (default 20).
        category: Filter by category name (e.g. "Assignments").
        type: Filter by thread type — "question", "post", or "announcement".
        has_staff_reply: If true, only threads with staff/admin replies.
        is_answered: If true, only answered threads.
    """
    # Auto-load from cache if not in memory
    if not _index.is_loaded(course_id):
        cache = _cache_dir()
        cache_path = cache / f"{course_id}.json.gz"
        meta_path = cache / f"{course_id}.meta.json"
        if cache_path.exists():
            try:
                with gzip.open(cache_path, "rt", encoding="utf-8") as f:
                    threads = json.load(f)
                _index.build(course_id, threads)
            except (json.JSONDecodeError, OSError):
                cache_path.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)

    # Auto-sync if still not loaded
    if not _index.is_loaded(course_id):
        sync_result = await sync_index(course_id)
        if sync_result.startswith("Error"):
            return sync_result

    result = _index.search(
        course_id, query, limit=limit,
        category=category, type=type,
        has_staff_reply=has_staff_reply,
        is_answered=is_answered,
    )

    # Add last_synced from meta
    cache = _cache_dir()
    meta_path = cache / f"{course_id}.meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            result["last_synced"] = meta.get("last_synced")
        except (json.JSONDecodeError, OSError):
            pass

    return _json(result)


# ======================================================================
# Entry point
# ======================================================================

if __name__ == "__main__":
    mcp.run()
