"""Local search index using SQLite FTS5 for instant thread search."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from edstem_mcp._helpers import _pii_enabled, _scrub_emails, _thread_url

# ------------------------------------------------------------------
# XML stripping (for write-through path)
# ------------------------------------------------------------------

_XML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_xml(xml: str) -> str:
    return _XML_TAG_RE.sub(" ", xml).strip()


_IMAGE_RE = re.compile(r'<image\s[^>]*src="([^"]+)"')
_FILE_RE = re.compile(r'<file\s[^>]*url="([^"]+)"')
_VIDEO_RE = re.compile(r'<video\s[^>]*url="([^"]+)"')


def _extract_media(xml: str) -> list[str]:
    """Extract image, file, and video URLs from Ed XML."""
    urls: list[str] = []
    urls.extend(_IMAGE_RE.findall(xml))
    urls.extend(_FILE_RE.findall(xml))
    urls.extend(_VIDEO_RE.findall(xml))
    return urls


def _collect_media(items: list[dict], xml_field: str = "document") -> list[str]:
    """Recursively extract media URLs from items and their nested comments."""
    urls: list[str] = []
    for item in items:
        urls.extend(_extract_media(item.get(xml_field, "")))
        urls.extend(_collect_media(item.get("comments") or [], xml_field=xml_field))
    return urls


# ------------------------------------------------------------------
# Recursive comment/answer walkers
# ------------------------------------------------------------------


def _collect_replies(
    items: list[dict],
    staff_only: bool = False,
    text_field: str = "text",
    role_field: str = "role",
) -> list[str]:
    """Recursively collect text from answers/comments."""
    texts = []
    for item in items:
        is_staff = item.get("user", {}).get(role_field, "student") != "student"
        if not staff_only or is_staff:
            text = item.get(text_field, "")
            if text:
                texts.append(text)
        nested = item.get("comments") or []
        texts.extend(
            _collect_replies(nested, staff_only=staff_only,
                             text_field=text_field, role_field=role_field)
        )
    return texts


def _count_recursive(items: list[dict]) -> int:
    """Count all items recursively."""
    total = len(items)
    for item in items:
        total += _count_recursive(item.get("comments") or [])
    return total


# ------------------------------------------------------------------
# FTS5 schema and weights
# ------------------------------------------------------------------

_CREATE_TABLE = '''
    CREATE VIRTUAL TABLE threads USING fts5(
        thread_id UNINDEXED,
        number UNINDEXED,
        course_id UNINDEXED,
        url UNINDEXED,
        title,
        body,
        replies,
        staff_replies,
        category UNINDEXED,
        subcategory UNINDEXED,
        type UNINDEXED,
        user_name UNINDEXED,
        user_role UNINDEXED,
        has_staff_reply UNINDEXED,
        is_answered UNINDEXED,
        endorsed UNINDEXED,
        comment_count UNINDEXED,
        votes UNINDEXED,
        views UNINDEXED,
        unique_views UNINDEXED,
        created_at UNINDEXED,
        images UNINDEXED,
        tokenize='porter'
    )
'''

_BM25_WEIGHTS = (
    0, 0, 0, 0,
    5.0, 1.0, 0.5, 2.0,
    0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
    0,
)

_BM25_EXPR = f"bm25(threads, {', '.join(str(w) for w in _BM25_WEIGHTS)})"

# Column names in CREATE TABLE order (for building result dicts)
_COLUMNS = (
    "thread_id", "number", "course_id", "url",
    "title", "body", "replies", "staff_replies",
    "category", "subcategory", "type", "user_name", "user_role",
    "has_staff_reply", "is_answered", "endorsed",
    "comment_count", "votes", "views", "unique_views", "created_at",
    "images",
)

_SUMMARY_RESULT_KEYS = {
    "thread_id", "number", "title", "url",
    "category", "type", "user_name",
    "has_staff_reply", "is_answered", "comment_count", "created_at",
}

# ------------------------------------------------------------------
# Module state
# ------------------------------------------------------------------

_dbs: dict[int, sqlite3.Connection] = {}
_rowid_maps: dict[int, dict[str, int]] = {}
_course_map: dict[str, int] = {}


# ------------------------------------------------------------------
# Normalisation
# ------------------------------------------------------------------


def _normalise_bulk(thread: dict, course_id: int) -> tuple:
    """Convert a bulk JSON thread dict to a row tuple."""
    url = thread.get("url", "")
    thread_id = url.rstrip("/").split("/")[-1].split("?")[0]

    doc = thread.get("document", "")
    body = _strip_xml(doc)

    all_items = (thread.get("answers") or []) + (thread.get("comments") or [])
    replies = "\n".join(_collect_replies(all_items))
    staff_replies = "\n".join(_collect_replies(all_items, staff_only=True))

    if _pii_enabled():
        body = _scrub_emails(body)
        replies = _scrub_emails(replies)
        staff_replies = _scrub_emails(staff_replies)

    media_urls = _extract_media(doc)
    media_urls.extend(_collect_media(all_items))

    has_staff_reply = bool(staff_replies)
    is_answered = any(a.get("endorsed") for a in (thread.get("answers") or [])) or (
        thread.get("type") == "question" and has_staff_reply
    )

    return (
        thread_id,
        str(thread.get("number", "")),
        str(course_id),
        url,
        thread.get("title", ""),
        body,
        replies,
        staff_replies,
        thread.get("category", ""),
        thread.get("subcategory", ""),
        thread.get("type", ""),
        thread.get("user", {}).get("name", ""),
        thread.get("user", {}).get("role", ""),
        str(has_staff_reply).lower(),
        str(is_answered).lower(),
        str(thread.get("endorsed", False)).lower(),
        str(_count_recursive(all_items)),
        str(thread.get("votes", 0)),
        str(thread.get("views", 0)),
        str(thread.get("unique_views", 0)),
        thread.get("created_at", ""),
        json.dumps(media_urls) if media_urls else "",
    )


def _normalise_api(raw: dict) -> tuple:
    """Convert a raw get_thread API response to a row tuple."""
    t = raw.get("thread", raw)
    thread_id = str(t.get("id", ""))
    course_id = str(t.get("course_id", ""))
    url = _thread_url(int(course_id), int(thread_id)) if course_id and thread_id else ""

    content = t.get("content", "")
    all_items = (t.get("answers") or []) + (t.get("comments") or [])
    replies_parts = _collect_replies(all_items, text_field="content", role_field="course_role")
    staff_parts = _collect_replies(all_items, staff_only=True, text_field="content", role_field="course_role")
    replies = "\n".join(_strip_xml(r) for r in replies_parts)
    staff_replies = "\n".join(_strip_xml(r) for r in staff_parts)

    body = _strip_xml(content)
    if _pii_enabled():
        body = _scrub_emails(body)
        replies = _scrub_emails(replies)
        staff_replies = _scrub_emails(staff_replies)

    media_urls = _extract_media(content)
    media_urls.extend(_collect_media(all_items, xml_field="content"))

    has_staff_reply = bool(staff_replies)
    is_answered = t.get("is_answered", False)

    return (
        thread_id,
        str(t.get("number", "")),
        course_id,
        url,
        t.get("title", ""),
        body,
        replies,
        staff_replies,
        t.get("category", ""),
        t.get("subcategory", ""),
        t.get("type", ""),
        t.get("user", {}).get("name", ""),
        t.get("user", {}).get("course_role", ""),
        str(has_staff_reply).lower(),
        str(is_answered).lower(),
        str(t.get("is_endorsed", False)).lower(),
        str(t.get("reply_count", 0)),
        str(t.get("vote_count", 0)),
        str(t.get("view_count", 0)),
        "0",
        t.get("created_at", ""),
        json.dumps(media_urls) if media_urls else "",
    )


# ------------------------------------------------------------------
# Build / search / update / info / clear
# ------------------------------------------------------------------


def is_loaded(course_id: int) -> bool:
    return course_id in _dbs


def get_course_for_thread(thread_id: str) -> int | None:
    """Look up which course a thread belongs to."""
    return _course_map.get(thread_id)


def build(course_id: int, threads: list[dict]) -> int:
    """Build in-memory FTS5 index from bulk JSON threads."""
    # Close old connection if rebuilding
    old_conn = _dbs.pop(course_id, None)
    if old_conn is not None:
        old_conn.close()
    # Clean up stale _course_map entries from the old index
    old_rowid_map = _rowid_maps.pop(course_id, None)
    if old_rowid_map:
        for tid in old_rowid_map:
            _course_map.pop(tid, None)

    conn = sqlite3.connect(":memory:")
    conn.execute(_CREATE_TABLE)
    placeholders = ", ".join("?" * len(_COLUMNS))
    rowid_map: dict[str, int] = {}

    for thread in threads:
        row = _normalise_bulk(thread, course_id)
        cursor = conn.execute(f"INSERT INTO threads VALUES ({placeholders})", row)
        thread_id = row[0]
        rowid_map[thread_id] = cursor.lastrowid
        _course_map[thread_id] = course_id

    conn.commit()
    _dbs[course_id] = conn
    _rowid_maps[course_id] = rowid_map
    return len(threads)


def search(
    course_id: int,
    query: str,
    limit: int = 20,
    category: str | None = None,
    type: str | None = None,
    has_staff_reply: bool | None = None,
    is_answered: bool | None = None,
) -> dict:
    """Search the in-memory index. Returns results + metadata."""
    conn = _dbs.get(course_id)
    if conn is None:
        return {"results": [], "error": "Index not loaded for this course."}

    # Build WHERE clauses for UNINDEXED filters
    match_all = query.strip() in ("*", "")
    where_parts: list[str] = [] if match_all else ["threads MATCH ?"]
    params: list = [] if match_all else [query]

    if category is not None:
        where_parts.append("category = ?")
        params.append(category)
    if type is not None:
        where_parts.append("type = ?")
        params.append(type)
    if has_staff_reply is not None:
        where_parts.append("has_staff_reply = ?")
        params.append(str(has_staff_reply).lower())
    if is_answered is not None:
        where_parts.append("is_answered = ?")
        params.append(str(is_answered).lower())

    where = " AND ".join(where_parts) if where_parts else "1=1"
    col_list = ", ".join(_COLUMNS)
    if match_all:
        sql = f"""
            SELECT {col_list}, 0.0 AS score, '' AS snippet
            FROM threads
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT ?
        """
    else:
        sql = f"""
            SELECT {col_list}, {_BM25_EXPR} AS score,
                   snippet(threads, -1, '<b>', '</b>', '...', 30) AS snippet
            FROM threads
            WHERE {where}
            ORDER BY score
            LIMIT ?
        """
    params.append(limit)

    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        # Malformed FTS5 query — fall back to quoted literal
        escaped = query.replace('"', '""')
        params[0] = f'"{escaped}"'
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return {"results": [], "error": f"Search failed for query: {query}"}

    results = []
    for i, row in enumerate(rows):
        d = dict(zip(_COLUMNS, row[: len(_COLUMNS)]))
        d["score"] = round(-row[len(_COLUMNS)], 4)  # negate BM25 (higher = better)
        d["snippet"] = row[len(_COLUMNS) + 1]
        # Top 5 get full content, rest get summary
        if i >= 5:
            d = {k: v for k, v in d.items() if k in _SUMMARY_RESULT_KEYS or k in ("score", "snippet")}
        results.append(d)

    return {"results": results, "total": len(results)}


def update_thread(course_id: int, thread_id: str, raw_api_response: dict) -> None:
    """Update a single thread in the index (write-through)."""
    conn = _dbs.get(course_id)
    if conn is None:
        return
    rowid_map = _rowid_maps.get(course_id, {})

    # Delete old row if exists
    old_rowid = rowid_map.get(thread_id)
    if old_rowid is not None:
        conn.execute("DELETE FROM threads WHERE rowid = ?", (old_rowid,))

    # Insert new row
    row = _normalise_api(raw_api_response)
    placeholders = ", ".join("?" * len(_COLUMNS))
    cursor = conn.execute(f"INSERT INTO threads VALUES ({placeholders})", row)
    rowid_map[thread_id] = cursor.lastrowid
    _course_map[thread_id] = course_id
    conn.commit()


def delete_thread(course_id: int, thread_id: str) -> None:
    """Remove a thread from the index."""
    conn = _dbs.get(course_id)
    if conn is None:
        return
    rowid_map = _rowid_maps.get(course_id, {})
    old_rowid = rowid_map.pop(thread_id, None)
    if old_rowid is not None:
        conn.execute("DELETE FROM threads WHERE rowid = ?", (old_rowid,))
        conn.commit()
    _course_map.pop(thread_id, None)


def info(course_id: int) -> dict | None:
    """Return index metadata or None if not loaded."""
    conn = _dbs.get(course_id)
    if conn is None:
        return None
    count = conn.execute("SELECT count(*) FROM threads").fetchone()[0]
    return {"thread_count": count}


def clear(course_id: int) -> None:
    """Drop the in-memory index for a course."""
    conn = _dbs.pop(course_id, None)
    if conn is not None:
        conn.close()
    rowid_map = _rowid_maps.pop(course_id, None)
    if rowid_map:
        for tid in rowid_map:
            _course_map.pop(tid, None)
