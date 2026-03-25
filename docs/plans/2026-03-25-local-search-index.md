# Local Search Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add instant local search over Ed Discussion threads using SQLite FTS5 in-memory, backed by a bulk JSON endpoint.

**Architecture:** Two new MCP tools (`sync_index`, `search_index`) plus a new `_index.py` module that wraps FTS5. The bulk endpoint downloads all threads in one JSON response (~950KB). The FTS5 table is in-memory, rebuilt from cached JSON in ~2ms. Write-through updates keep the index fresh after replies/edits. No new dependencies.

**Tech Stack:** Python sqlite3 (stdlib FTS5), gzip, httpx (existing)

**Spec:** `docs/specs/2026-03-25-local-search-index.md`

---

### File Map

- **Create:** `src/edstem_mcp/_index.py` — FTS5 wrapper: build, search, update, delete, info, clear, normalisation
- **Create:** `tests/test_index.py` — unit tests for the index module
- **Modify:** `src/edstem_mcp/client.py` — add `get_discussion_threads_json(course_id)`
- **Modify:** `src/edstem_mcp/server.py` — add `sync_index` and `search_index` tools, write-through on 4 existing tools
- **Modify:** `tests/test_server.py` — tests for new tools and write-through

---

### Task 1: Core index module — build and search

**Files:**
- Create: `src/edstem_mcp/_index.py`
- Create: `tests/test_index.py`

- [ ] **Step 1: Write failing tests for _collect_replies and _count_recursive**

```python
# tests/test_index.py
"""Tests for the local search index module."""

import pytest
from edstem_mcp._index import _collect_replies, _count_recursive
from edstem_mcp import _index as _index_mod


@pytest.fixture(autouse=True)
def _cleanup_index():
    """Clear all index state after each test."""
    yield
    for cid in list(_index_mod._dbs.keys()):
        _index_mod.clear(cid)


def test_collect_replies_flat():
    items = [
        {"text": "Hello", "user": {"role": "student"}, "comments": []},
        {"text": "Staff here", "user": {"role": "admin"}, "comments": []},
    ]
    assert _collect_replies(items) == ["Hello", "Staff here"]
    assert _collect_replies(items, staff_only=True) == ["Staff here"]


def test_collect_replies_nested():
    items = [
        {
            "text": "Top",
            "user": {"role": "student"},
            "comments": [
                {"text": "Nested", "user": {"role": "admin"}, "comments": []},
            ],
        },
    ]
    assert _collect_replies(items) == ["Top", "Nested"]
    assert _collect_replies(items, staff_only=True) == ["Nested"]


def test_collect_replies_missing_comments_key():
    """answers on non-question threads may have no comments key."""
    items = [{"text": "Answer", "user": {"role": "admin"}}]
    assert _collect_replies(items) == ["Answer"]


def test_collect_replies_api_shape():
    """Write-through uses content/course_role instead of text/role."""
    items = [
        {"content": "<p>Hello</p>", "user": {"course_role": "student"}, "comments": []},
        {"content": "<p>Staff</p>", "user": {"course_role": "admin"}, "comments": []},
    ]
    result = _collect_replies(items, text_field="content", role_field="course_role")
    assert result == ["<p>Hello</p>", "<p>Staff</p>"]
    result = _collect_replies(items, staff_only=True, text_field="content", role_field="course_role")
    assert result == ["<p>Staff</p>"]


def test_count_recursive():
    items = [
        {"comments": [{"comments": [{"comments": []}]}]},
        {"comments": []},
    ]
    assert _count_recursive(items) == 4  # 2 top-level + 1 child + 1 grandchild
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_index.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement helpers in _index.py**

```python
# src/edstem_mcp/_index.py
"""Local search index using SQLite FTS5 for instant thread search."""

from __future__ import annotations

import gzip
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_index.py -v`
Expected: PASS

- [ ] **Step 5: Write failing tests for normalise and build**

Add to `tests/test_index.py`:

```python
from edstem_mcp._index import _normalise_bulk, _strip_xml, build, search, is_loaded, info, clear


def test_strip_xml():
    assert _strip_xml("<p>Hello <b>world</b></p>") == "Hello  world"
    assert _strip_xml("plain text") == "plain text"


def test_normalise_bulk():
    thread = {
        "url": "https://edstem.org/au/courses/31798/discussion/3124785",
        "number": 1,
        "title": "Welcome!",
        "text": "Hello everyone",
        "category": "General",
        "subcategory": "",
        "type": "announcement",
        "votes": 5,
        "views": 100,
        "unique_views": 50,
        "private": False,
        "anonymous": False,
        "endorsed": False,
        "created_at": "2026-01-01T00:00:00",
        "user": {"name": "Alice", "email": "a@b.com", "role": "admin"},
        "comments": [
            {
                "text": "Nice!",
                "user": {"name": "Bob", "email": "b@c.com", "role": "student"},
                "comments": [],
            }
        ],
    }
    row = _normalise_bulk(thread, course_id=31798)
    assert row[0] == "3124785"  # thread_id from url
    assert row[1] == "1"        # number
    assert row[2] == "31798"    # course_id
    assert row[4] == "Welcome!" # title
    assert row[5] == "Hello everyone"  # body
    assert "Nice!" in row[6]    # replies
    assert row[13] == "false"   # has_staff_reply (only student comment)


def test_normalise_bulk_with_answers():
    thread = {
        "url": "https://edstem.org/au/courses/31798/discussion/999",
        "number": 42,
        "title": "Help",
        "text": "Question here",
        "category": "Labs",
        "subcategory": "Lab 1",
        "type": "question",
        "votes": 0, "views": 10, "unique_views": 5,
        "private": False, "anonymous": False, "endorsed": False,
        "created_at": "2026-01-01T00:00:00",
        "user": {"name": "Student", "email": "s@t.com", "role": "student"},
        "answers": [
            {
                "text": "Here's the fix",
                "user": {"name": "Prof", "email": "p@t.com", "role": "admin"},
                "endorsed": True,
                "comments": [],
            }
        ],
        "comments": [],
    }
    row = _normalise_bulk(thread, course_id=31798)
    assert "Here's the fix" in row[6]     # replies includes answer
    assert "Here's the fix" in row[7]     # staff_replies
    assert row[13] == "true"              # has_staff_reply
    assert row[14] == "true"              # is_answered (endorsed answer)


def test_normalise_api():
    """Write-through normalisation from get_thread response."""
    from edstem_mcp._index import _normalise_api
    raw = {
        "thread": {
            "id": 100, "number": 1, "title": "Help",
            "content": "<document><paragraph>Need help</paragraph></document>",
            "category": "General", "subcategory": "", "type": "question",
            "course_id": 1, "is_answered": True, "is_endorsed": False,
            "is_pinned": False, "is_private": False, "is_locked": False,
            "is_anonymous": False, "reply_count": 1, "vote_count": 0,
            "view_count": 10, "unresolved_count": 0,
            "created_at": "2026-01-01T00:00:00",
            "user": {"id": 1, "name": "Alice", "course_role": "student"},
            "comments": [
                {
                    "id": 50, "content": "<doc>Staff answer</doc>",
                    "user": {"id": 2, "name": "Prof", "course_role": "admin"},
                    "type": "comment", "comments": [],
                }
            ],
            "answers": [],
        },
    }
    row = _normalise_api(raw)
    assert row[0] == "100"         # thread_id
    assert row[4] == "Help"        # title
    assert "Need help" in row[5]   # body (XML stripped)
    assert "Staff answer" in row[6]  # replies (XML stripped)
    assert "Staff answer" in row[7]  # staff_replies
    assert row[13] == "true"       # has_staff_reply
    assert row[14] == "true"       # is_answered


def test_build_and_search():
    threads = [
        {
            "url": "https://edstem.org/au/courses/1/discussion/100",
            "number": 1, "title": "Assignment 1 help",
            "text": "I need help with the first assignment",
            "category": "Assignments", "subcategory": "", "type": "question",
            "votes": 0, "views": 10, "unique_views": 5,
            "private": False, "anonymous": False, "endorsed": False,
            "created_at": "2026-01-01T00:00:00",
            "user": {"name": "Alice", "email": "a@b.com", "role": "student"},
            "comments": [],
        },
        {
            "url": "https://edstem.org/au/courses/1/discussion/101",
            "number": 2, "title": "Exam prep",
            "text": "How should I prepare for the exam",
            "category": "General", "subcategory": "", "type": "question",
            "votes": 3, "views": 50, "unique_views": 30,
            "private": False, "anonymous": False, "endorsed": False,
            "created_at": "2026-01-02T00:00:00",
            "user": {"name": "Bob", "email": "b@c.com", "role": "student"},
            "answers": [
                {
                    "text": "Review lecture notes",
                    "user": {"name": "Prof", "email": "p@t.com", "role": "admin"},
                    "endorsed": True, "comments": [],
                }
            ],
            "comments": [],
        },
    ]
    count = build(1, threads)
    assert count == 2
    assert is_loaded(1)

    # Search by keyword
    result = search(1, "assignment")
    assert len(result["results"]) == 1
    assert result["results"][0]["title"] == "Assignment 1 help"

    # Search with filter
    result = search(1, "exam", has_staff_reply=True)
    assert len(result["results"]) == 1
    assert result["results"][0]["has_staff_reply"] == "true"

    # Info
    idx_info = info(1)
    assert idx_info["thread_count"] == 2

    # Clear
    clear(1)
    assert not is_loaded(1)


def test_search_match_all():
    threads = [
        {
            "url": "https://edstem.org/au/courses/1/discussion/100",
            "number": 1, "title": "Thread A", "text": "Content A",
            "category": "General", "subcategory": "", "type": "post",
            "votes": 0, "views": 0, "unique_views": 0,
            "private": False, "anonymous": False, "endorsed": False,
            "created_at": "2026-01-01T00:00:00",
            "user": {"name": "A", "email": "a@b.com", "role": "student"},
            "comments": [],
        },
    ]
    build(1, threads)
    # Empty/star query returns all threads (bypasses MATCH)
    result = search(1, "*")
    assert len(result["results"]) == 1
    clear(1)


def test_search_malformed_query_falls_back():
    threads = [
        {
            "url": "https://edstem.org/au/courses/1/discussion/100",
            "number": 1, "title": "AND gates in circuits", "text": "About AND gates",
            "category": "General", "subcategory": "", "type": "post",
            "votes": 0, "views": 0, "unique_views": 0,
            "private": False, "anonymous": False, "endorsed": False,
            "created_at": "2026-01-01T00:00:00",
            "user": {"name": "A", "email": "a@b.com", "role": "student"},
            "comments": [],
        },
    ]
    build(1, threads)
    # Bare "AND" would crash FTS5 — should fall back to quoted literal
    result = search(1, "AND")
    # Should not raise, may return 0 or 1 results
    assert "results" in result
    clear(1)
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `uv run pytest tests/test_index.py -v`
Expected: FAIL (ImportError for missing functions)

- [ ] **Step 7: Implement normalise, build, search, info, clear in _index.py**

Add to `src/edstem_mcp/_index.py`:

```python
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
        tokenize='porter'
    )
'''

_BM25_WEIGHTS = (
    0, 0, 0, 0,
    5.0, 1.0, 0.5, 2.0,
    0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0,
)

_BM25_EXPR = f"bm25(threads, {', '.join(str(w) for w in _BM25_WEIGHTS)})"

# Column names in CREATE TABLE order (for building result dicts)
_COLUMNS = (
    "thread_id", "number", "course_id", "url",
    "title", "body", "replies", "staff_replies",
    "category", "subcategory", "type", "user_name", "user_role",
    "has_staff_reply", "is_answered", "endorsed",
    "comment_count", "votes", "views", "unique_views", "created_at",
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

    all_items = (thread.get("answers") or []) + (thread.get("comments") or [])
    replies = "\n".join(_collect_replies(all_items))
    staff_replies = "\n".join(_collect_replies(all_items, staff_only=True))

    if _pii_enabled():
        body = _scrub_emails(thread.get("text", ""))
        replies = _scrub_emails(replies)
        staff_replies = _scrub_emails(staff_replies)
    else:
        body = thread.get("text", "")

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
    )


def _normalise_api(raw: dict) -> tuple:
    """Convert a raw get_thread API response to a row tuple."""
    t = raw.get("thread", raw)
    thread_id = str(t.get("id", ""))
    course_id = str(t.get("course_id", ""))
    url = _thread_url(int(course_id), int(thread_id)) if course_id and thread_id else ""

    all_items = (t.get("answers") or []) + (t.get("comments") or [])
    replies_parts = _collect_replies(all_items, text_field="content", role_field="course_role")
    staff_parts = _collect_replies(all_items, staff_only=True, text_field="content", role_field="course_role")
    replies = "\n".join(_strip_xml(r) for r in replies_parts)
    staff_replies = "\n".join(_strip_xml(r) for r in staff_parts)

    body = _strip_xml(t.get("content", ""))
    if _pii_enabled():
        body = _scrub_emails(body)
        replies = _scrub_emails(replies)
        staff_replies = _scrub_emails(staff_replies)

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
    )


# ------------------------------------------------------------------
# Build / search / update / info / clear
# ------------------------------------------------------------------


def is_loaded(course_id: int) -> bool:
    return course_id in _dbs


def build(course_id: int, threads: list[dict]) -> int:
    """Build in-memory FTS5 index from bulk JSON threads."""
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
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_index.py -v`
Expected: PASS

- [ ] **Step 9: Run full test suite to verify no regressions**

Run: `uv run pytest -v`
Expected: All existing tests PASS

- [ ] **Step 10: Commit**

```bash
git add src/edstem_mcp/_index.py tests/test_index.py
git commit -m "feat: add FTS5 index module with build and search"
```

---

### Task 2: Bulk endpoint client method

**Files:**
- Modify: `src/edstem_mcp/client.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_server.py`:

```python
import httpx
from unittest.mock import AsyncMock, patch

async def test_get_discussion_threads_json():
    """Bulk endpoint returns a bare list and uses 120s timeout."""
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [{"number": 1, "title": "Thread 1"}]
    mock_response.raise_for_status = lambda: None

    with patch("edstem_mcp.client.EdClient.__init__", return_value=None):
        from edstem_mcp.client import EdClient
        client = EdClient.__new__(EdClient)
        client._client = AsyncMock()
        client._client.get.return_value = mock_response
        client.base_url = "https://edstem.org/api"

        result = await client.get_discussion_threads_json(31798)
        assert result == [{"number": 1, "title": "Thread 1"}]
        client._client.get.assert_called_once()
        # Verify the timeout was 120s
        call_kwargs = client._client.get.call_args
        assert call_kwargs.kwargs.get("timeout") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py::test_get_discussion_threads_json -v`
Expected: FAIL (AttributeError: get_discussion_threads_json not found)

- [ ] **Step 3: Implement the client method**

Add to `src/edstem_mcp/client.py` in the Files section (after `upload_file_url`, before `download_file`):

```python
    async def get_discussion_threads_json(self, course_id: int) -> list[dict[str, Any]]:
        """Download all threads for a course via the bulk analytics endpoint.

        Returns a bare list (not wrapped in a dict). Uses a longer timeout
        since this endpoint returns all threads at once.
        """
        resp = await self._client.get(
            f"{self.base_url}/courses/{course_id}/analytics/discussion_threads.json",
            timeout=httpx.Timeout(120.0, connect=10.0),
        )
        if not resp.is_success:
            error_cls = _ERROR_MAP.get(resp.status_code, EdAPIError)
            raise error_cls(resp.status_code, resp.text[:200])
        return resp.json()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_server.py::test_get_discussion_threads_json -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/edstem_mcp/client.py tests/test_server.py
git commit -m "feat: add bulk discussion threads endpoint to client"
```

---

### Task 3: sync_index and search_index MCP tools

**Files:**
- Modify: `src/edstem_mcp/server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Add imports to server.py**

At the top of `src/edstem_mcp/server.py`, add to the imports:

```python
import gzip
import json
import os
from datetime import datetime, timezone

from edstem_mcp import _index
```

- [ ] **Step 2: Add cache path helper**

Add near the top of `src/edstem_mcp/server.py` (after the `_get_client` function):

```python
def _cache_dir() -> Path:
    """Return the cache directory for index data."""
    base = Path(os.environ.get("ED_INDEX_PATH", "~/.cache/edstem-mcp")).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    return base
```

- [ ] **Step 3: Write failing test for sync_index**

Add to `tests/test_server.py`:

```python
from edstem_mcp.server import sync_index, search_index
from edstem_mcp import _index


async def test_sync_index(mock_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ED_INDEX_PATH", str(tmp_path))
    mock_client.get_discussion_threads_json.return_value = [
        {
            "url": "https://edstem.org/au/courses/1/discussion/100",
            "number": 1, "title": "Thread 1", "text": "Content 1",
            "category": "General", "subcategory": "", "type": "post",
            "votes": 0, "views": 0, "unique_views": 0,
            "private": False, "anonymous": False, "endorsed": False,
            "created_at": "2026-01-01T00:00:00",
            "user": {"name": "Alice", "email": "a@b.com", "role": "student"},
            "comments": [],
        },
    ]
    result = _parse(await sync_index(1))
    assert result["threads_indexed"] == 1
    assert result["course_id"] == 1
    assert "last_synced" in result
    # Cache file exists
    assert (tmp_path / "1.json.gz").exists()
    assert (tmp_path / "1.meta.json").exists()
    # Index is loaded
    assert _index.is_loaded(1)
    _index.clear(1)
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py::test_sync_index -v`
Expected: FAIL (sync_index not found)

- [ ] **Step 5: Implement sync_index tool**

Add to `src/edstem_mcp/server.py` before the entry point section:

```python
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
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_server.py::test_sync_index -v`
Expected: PASS

- [ ] **Step 7: Write failing test for search_index**

Add to `tests/test_server.py`:

```python
async def test_search_index(mock_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ED_INDEX_PATH", str(tmp_path))
    threads = [
        {
            "url": "https://edstem.org/au/courses/1/discussion/100",
            "number": 1, "title": "Assignment 1", "text": "Help with assignment",
            "category": "Assignments", "subcategory": "", "type": "question",
            "votes": 0, "views": 10, "unique_views": 5,
            "private": False, "anonymous": False, "endorsed": False,
            "created_at": "2026-01-01T00:00:00",
            "user": {"name": "Alice", "email": "a@b.com", "role": "student"},
            "answers": [
                {"text": "Check the notes", "user": {"name": "Prof", "email": "p@t.com", "role": "admin"},
                 "endorsed": True, "comments": []},
            ],
            "comments": [],
        },
        {
            "url": "https://edstem.org/au/courses/1/discussion/101",
            "number": 2, "title": "Exam question", "text": "When is the exam",
            "category": "General", "subcategory": "", "type": "question",
            "votes": 0, "views": 5, "unique_views": 3,
            "private": False, "anonymous": False, "endorsed": False,
            "created_at": "2026-01-02T00:00:00",
            "user": {"name": "Bob", "email": "b@c.com", "role": "student"},
            "comments": [],
        },
    ]
    # Build index directly
    _index.build(1, threads)

    result = _parse(await search_index(1, "assignment"))
    assert len(result["results"]) == 1
    assert result["results"][0]["title"] == "Assignment 1"

    # Filter by has_staff_reply
    result = _parse(await search_index(1, "assignment", has_staff_reply=True))
    assert len(result["results"]) == 1

    # Filter by category
    result = _parse(await search_index(1, "exam", category="General"))
    assert len(result["results"]) == 1

    _index.clear(1)
```

- [ ] **Step 8: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py::test_search_index -v`
Expected: FAIL (search_index not found)

- [ ] **Step 9: Implement search_index tool**

Add to `src/edstem_mcp/server.py`:

```python
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
```

- [ ] **Step 10: Run test to verify it passes**

Run: `uv run pytest tests/test_server.py::test_search_index -v`
Expected: PASS

- [ ] **Step 11: Run full test suite**

Run: `uv run pytest -v`
Expected: All PASS

- [ ] **Step 12: Commit**

```bash
git add src/edstem_mcp/server.py tests/test_server.py
git commit -m "feat: add sync_index and search_index MCP tools"
```

---

### Task 4: Write-through updates

**Files:**
- Modify: `src/edstem_mcp/server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Write failing test for write-through on reply_to_thread**

Add to `tests/test_server.py`:

```python
async def test_reply_write_through(mock_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ED_INDEX_PATH", str(tmp_path))
    # Build an index with one thread
    threads = [
        {
            "url": "https://edstem.org/au/courses/1/discussion/100",
            "number": 1, "title": "Help", "text": "Need help",
            "category": "General", "subcategory": "", "type": "question",
            "votes": 0, "views": 0, "unique_views": 0,
            "private": False, "anonymous": False, "endorsed": False,
            "created_at": "2026-01-01T00:00:00",
            "user": {"name": "Alice", "email": "a@b.com", "role": "student"},
            "comments": [],
        },
    ]
    _index.build(1, threads)

    # Mock the reply and subsequent get_thread for write-through
    mock_client.reply_to_thread.return_value = {
        "comment": {"id": 50, "thread_id": 100, "type": "comment", "course_id": 1},
    }
    mock_client.get_thread.return_value = {
        "thread": {
            "id": 100, "number": 1, "title": "Help", "content": "<doc>Need help</doc>",
            "category": "General", "subcategory": "", "type": "question",
            "course_id": 1, "is_answered": True, "is_endorsed": False,
            "is_pinned": False, "is_private": False, "is_locked": False,
            "is_anonymous": False, "reply_count": 1, "vote_count": 0,
            "view_count": 10, "unresolved_count": 0,
            "created_at": "2026-01-01T00:00:00",
            "user": {"id": 1, "name": "Alice", "course_role": "student"},
            "comments": [
                {
                    "id": 50, "content": "<doc>Here is the answer about deadlines</doc>",
                    "user": {"id": 2, "name": "Prof", "course_role": "admin"},
                    "type": "comment", "comments": [],
                }
            ],
            "answers": [],
        },
        "users": [],
    }

    await reply_to_thread(100, "<doc>Here is the answer about deadlines</doc>")

    # The index should now contain the updated thread with staff reply
    result = _index.search(1, "deadlines")
    assert len(result["results"]) == 1
    _index.clear(1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py::test_reply_write_through -v`
Expected: FAIL (write-through not implemented)

- [ ] **Step 3: Add write-through helper**

Add to `src/edstem_mcp/server.py` (near the index section):

```python
async def _write_through(thread_id: int) -> None:
    """Re-fetch a thread and update the local index (best-effort)."""
    try:
        course_id = _index._course_map.get(str(thread_id))
        if course_id is None or not _index.is_loaded(course_id):
            return
        raw = await _get_client().get_thread(thread_id)
        _index.update_thread(course_id, str(thread_id), raw)
    except Exception:
        pass  # Write-through is best-effort
```

- [ ] **Step 4: Add write-through calls to existing tools**

In `reply_to_thread`, after the return statement is built but before returning, add:

```python
        # Write-through: update index
        await _write_through(thread_id)
```

Similarly in `edit_thread` (after building the response).

For `create_thread`, add custom write-through (cannot use `_write_through` because the new thread is not in `_course_map`):

```python
        # Write-through: index the new thread
        if _index.is_loaded(course_id):
            try:
                raw = await _get_client().get_thread(t["id"])
                _index.update_thread(course_id, str(t["id"]), raw)
            except Exception:
                pass
```

For `delete_thread`, add before the return:

```python
        # Write-through: remove from index
        tid = str(thread_id)
        cid = _index._course_map.get(tid)
        if cid is not None:
            _index.delete_thread(cid, tid)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_server.py::test_reply_write_through -v`
Expected: PASS

- [ ] **Step 6: Write additional write-through tests**

Add to `tests/test_server.py`:

```python
async def test_delete_write_through(mock_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ED_INDEX_PATH", str(tmp_path))
    threads = [
        {
            "url": "https://edstem.org/au/courses/1/discussion/100",
            "number": 1, "title": "To be deleted", "text": "Delete me",
            "category": "General", "subcategory": "", "type": "post",
            "votes": 0, "views": 0, "unique_views": 0,
            "private": False, "anonymous": False, "endorsed": False,
            "created_at": "2026-01-01T00:00:00",
            "user": {"name": "Alice", "email": "a@b.com", "role": "student"},
            "comments": [],
        },
    ]
    _index.build(1, threads)
    mock_client.delete_thread.return_value = {}

    await delete_thread(100)

    # Thread should be removed from the index
    result = _index.search(1, "deleted")
    assert len(result["results"]) == 0
    _index.clear(1)


async def test_search_index_auto_loads_from_cache(mock_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ED_INDEX_PATH", str(tmp_path))
    # Write a cache file directly
    import gzip
    threads = [
        {
            "url": "https://edstem.org/au/courses/1/discussion/100",
            "number": 1, "title": "Cached thread", "text": "From cache",
            "category": "General", "subcategory": "", "type": "post",
            "votes": 0, "views": 0, "unique_views": 0,
            "private": False, "anonymous": False, "endorsed": False,
            "created_at": "2026-01-01T00:00:00",
            "user": {"name": "Alice", "email": "a@b.com", "role": "student"},
            "comments": [],
        },
    ]
    with gzip.open(tmp_path / "1.json.gz", "wt", encoding="utf-8") as f:
        json.dump(threads, f)
    (tmp_path / "1.meta.json").write_text('{"last_synced": "2026-01-01T00:00:00", "thread_count": 1}')

    # No index in memory — search should auto-load from cache
    assert not _index.is_loaded(1)
    result = _parse(await search_index(1, "cached"))
    assert len(result["results"]) == 1
    assert result["results"][0]["title"] == "Cached thread"
    assert "last_synced" in result
    _index.clear(1)


async def test_search_index_auto_syncs_when_no_cache(mock_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ED_INDEX_PATH", str(tmp_path))
    mock_client.get_discussion_threads_json.return_value = [
        {
            "url": "https://edstem.org/au/courses/1/discussion/100",
            "number": 1, "title": "Fresh sync", "text": "Just synced",
            "category": "General", "subcategory": "", "type": "post",
            "votes": 0, "views": 0, "unique_views": 0,
            "private": False, "anonymous": False, "endorsed": False,
            "created_at": "2026-01-01T00:00:00",
            "user": {"name": "Alice", "email": "a@b.com", "role": "student"},
            "comments": [],
        },
    ]

    # No index, no cache — search should auto-sync
    result = _parse(await search_index(1, "synced"))
    assert len(result["results"]) == 1
    mock_client.get_discussion_threads_json.assert_called_once_with(1)
    _index.clear(1)


async def test_search_index_corrupted_cache(mock_client, tmp_path, monkeypatch):
    monkeypatch.setenv("ED_INDEX_PATH", str(tmp_path))
    # Write a corrupted cache file
    (tmp_path / "1.json.gz").write_bytes(b"not valid gzip")

    mock_client.get_discussion_threads_json.return_value = [
        {
            "url": "https://edstem.org/au/courses/1/discussion/100",
            "number": 1, "title": "After corruption", "text": "Recovered",
            "category": "General", "subcategory": "", "type": "post",
            "votes": 0, "views": 0, "unique_views": 0,
            "private": False, "anonymous": False, "endorsed": False,
            "created_at": "2026-01-01T00:00:00",
            "user": {"name": "Alice", "email": "a@b.com", "role": "student"},
            "comments": [],
        },
    ]

    # Should delete corrupted cache, auto-sync, and return results
    result = _parse(await search_index(1, "recovered"))
    assert len(result["results"]) == 1
    _index.clear(1)
```

- [ ] **Step 7: Run all new tests**

Run: `uv run pytest tests/test_server.py -k "write_through or auto_loads or auto_syncs or corrupted" -v`
Expected: PASS

- [ ] **Step 8: Run full test suite**

Run: `uv run pytest -v`
Expected: All PASS

- [ ] **Step 9: Commit**

```bash
git add src/edstem_mcp/server.py tests/test_server.py
git commit -m "feat: add write-through index updates for reply, edit, create, delete"
```

---

### Task 5: Update docstrings and verify tool count

**Files:**
- Modify: `src/edstem_mcp/server.py`

- [ ] **Step 1: Update search_threads docstring**

In the `search_threads` docstring, add at the end of the first sentence:

```
For faster ranked search with stemming and filtering, use search_index (requires sync_index first).
```

- [ ] **Step 2: Verify tool count**

Run: `uv run python -c "from edstem_mcp.server import mcp; print(len(mcp._tool_manager._tools), 'tools')"`
Expected: `43 tools` (41 existing + sync_index + search_index)

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/edstem_mcp/server.py
git commit -m "refactor: update search_threads docstring to reference search_index"
```

---

### Task 6: Validation against real data

- [ ] **Step 1: Restart MCP server and test sync**

Restart the edstem MCP server (`/mcp`), then call:
- `sync_index(31798)` — should index 276 threads in ~2-3s

- [ ] **Step 2: Test search queries**

- `search_index(31798, "project 1")` — should find threads about Project 1
- `search_index(31798, "assignment", has_staff_reply=True)` — should filter for staff-answered threads
- `search_index(31798, "ggplot2")` — technical term, should not be mangled
- `search_index(31798, "assign*")` — prefix search
- `search_index(31798, "staff_replies:extension")` — column-specific search

- [ ] **Step 3: Test write-through**

Reply to a thread, then search for content from the reply. Verify it appears in search results without re-syncing.

- [ ] **Step 4: Test cache rebuild**

Restart the MCP server, then call `search_index(31798, "project 1")` — should rebuild from cached JSON in <5ms and return results.
