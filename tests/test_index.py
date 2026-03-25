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
