"""Tests for MCP server tools — verify response trimming and output shape."""

from __future__ import annotations

import json

from edstem_mcp.client import EdAPIError
from edstem_mcp.server import (
    _pii_enabled,
    _scrub_emails,
    _strip_user_pii,
    _summarise_threads,
    _trim_comment,
    _trim_thread_detail,
    accept_answer,
    bulk_recategorise,
    create_thread,
    delete_comment,
    delete_thread,
    edit_comment,
    edit_thread,
    endorse_thread,
    get_attendance_session,
    get_course_stats,
    get_course_thread,
    get_thread,
    get_thread_by_url,
    get_user,
    get_user_activity,
    list_categories,
    list_courses,
    list_threads,
    list_users,
    lock_thread,
    mark_duplicate,
    pin_thread,
    reply_to_thread,
    search_threads,
    unlock_thread,
    unmark_duplicate,
    unpin_thread,
    unendorse_thread,
)


def _parse(result: str) -> dict | list:
    """Parse a compact JSON tool response."""
    return json.loads(result)


# ------------------------------------------------------------------
# Helper unit tests
# ------------------------------------------------------------------


def test_summarise_threads_keeps_only_summary_keys():
    threads = [{
        "id": 1, "number": 42, "type": "post", "title": "Hello",
        "category": "General", "subcategory": "",
        "created_at": "2025-01-01", "is_pinned": False, "is_private": False,
        "is_endorsed": False, "is_answered": False, "is_locked": False,
        "reply_count": 3, "vote_count": 1, "view_count": 50, "unresolved_count": 0,
        "user": {"name": "Alice"},
        # Extra keys that should be stripped
        "content": "<doc>long content</doc>",
        "editor_id": 999,
        "deleted_at": None,
    }]
    result = _summarise_threads(threads, course_id=1)
    assert len(result) == 1
    assert result[0]["user"] == "Alice"
    assert "content" not in result[0]
    assert "editor_id" not in result[0]


def test_summarise_threads_no_url():
    """URLs are reconstructible from id + course_id — don't waste tokens."""
    threads = [{
        "id": 123, "number": 42, "type": "post", "title": "Hello",
        "category": "General", "subcategory": "",
        "created_at": "2025-01-01", "is_pinned": False, "is_private": False,
        "is_endorsed": False, "is_answered": False, "is_locked": False,
        "reply_count": 0, "vote_count": 0, "view_count": 0, "unresolved_count": 0,
        "user": {"name": "Alice"},
    }]
    result = _summarise_threads(threads, course_id=1)
    assert "url" not in result[0]
    # But id and number are still present for the LLM to reconstruct if needed
    assert result[0]["id"] == 123
    assert result[0]["number"] == 42


def test_summarise_threads_omits_false_booleans():
    """False booleans are the default — only include when true to save tokens."""
    threads = [{
        "id": 1, "number": 1, "type": "post", "title": "Normal",
        "category": "General", "subcategory": "",
        "created_at": "2025-01-01", "is_pinned": False, "is_private": False,
        "is_endorsed": False, "is_answered": False, "is_locked": False,
        "reply_count": 0, "vote_count": 0, "view_count": 0, "unresolved_count": 0,
        "user": {"name": "Alice"},
    }, {
        "id": 2, "number": 2, "type": "question", "title": "Pinned Q",
        "category": "General", "subcategory": "",
        "created_at": "2025-01-01", "is_pinned": True, "is_private": False,
        "is_endorsed": True, "is_answered": False, "is_locked": False,
        "reply_count": 5, "vote_count": 3, "view_count": 100, "unresolved_count": 0,
        "user": {"name": "Bob"},
    }]
    result = _summarise_threads(threads, course_id=1)
    # Thread with all-false booleans: none of the is_* keys present
    assert "is_pinned" not in result[0]
    assert "is_private" not in result[0]
    assert "is_endorsed" not in result[0]
    assert "is_answered" not in result[0]
    assert "is_locked" not in result[0]
    # Thread with some true booleans: only true ones present
    assert result[1]["is_pinned"] is True
    assert result[1]["is_endorsed"] is True
    assert "is_private" not in result[1]
    assert "is_answered" not in result[1]
    assert "is_locked" not in result[1]


def test_summarise_threads_date_only_timestamp():
    """Summaries truncate timestamps to date-only. Detail views keep full precision."""
    threads = [{
        "id": 1, "number": 1, "type": "post", "title": "T",
        "category": "", "subcategory": "",
        "created_at": "2026-03-21T09:15:32.123456+11:00",
        "is_pinned": False, "is_private": False,
        "is_endorsed": False, "is_answered": False, "is_locked": False,
        "reply_count": 0, "vote_count": 0, "view_count": 0, "unresolved_count": 0,
        "user": {"name": "Alice"},
    }]
    result = _summarise_threads(threads, course_id=1)
    assert result[0]["created_at"] == "2026-03-21"


def test_summarise_threads_omits_empty_values():
    """Null and empty-string values waste tokens — omit them."""
    threads = [{
        "id": 1, "number": 1, "type": "post", "title": "T",
        "category": "General", "subcategory": "",
        "created_at": "2026-03-21", "is_pinned": False, "is_private": False,
        "is_endorsed": False, "is_answered": False, "is_locked": False,
        "reply_count": 0, "vote_count": 0, "view_count": 0, "unresolved_count": 0,
        "user": {"name": "Alice"},
    }]
    result = _summarise_threads(threads, course_id=1)
    # Empty subcategory should be omitted
    assert "subcategory" not in result[0]
    # But zero counts should be kept (0 is meaningful: "no replies")
    assert result[0]["reply_count"] == 0
    # Non-empty category should be kept
    assert result[0]["category"] == "General"


def test_trim_comment_strips_bloat():
    comment = {
        "id": 1, "user_id": 2, "parent_id": None, "type": "comment",
        "content": "<doc/>", "is_endorsed": False, "is_private": False,
        "is_resolved": False, "is_anonymous": False, "vote_count": 0,
        "created_at": "2025-01-01",
        "user": {"id": 1, "name": "Alice", "course_role": "student", "avatar": "url"},
        # Bloat
        "updated_at": "2025-01-02", "flag_count": 0,
        "comments": [{
            "id": 2, "user_id": 3, "parent_id": 1, "type": "comment",
            "content": "<reply/>", "is_endorsed": False, "is_private": False,
            "is_resolved": False, "is_anonymous": False, "vote_count": 0,
            "created_at": "2025-01-01",
        }],
    }
    result = _trim_comment(comment)
    assert "updated_at" not in result
    assert "flag_count" not in result
    assert "avatar" not in result["user"]
    assert len(result["comments"]) == 1


def test_trim_thread_detail_strips_bloat():
    data = {
        "thread": {
            "id": 1, "number": 42, "type": "post", "title": "Hello",
            "content": "<doc/>", "category": "General", "subcategory": "",
            "course_id": 10, "user_id": 5, "accepted_id": None, "duplicate_id": None,
            "created_at": "2025-01-01", "is_pinned": False, "is_private": False,
            "is_endorsed": False, "is_answered": False, "is_locked": False,
            "is_anonymous": False, "reply_count": 1, "vote_count": 0,
            "unresolved_count": 0,
            "user": {"id": 5, "name": "Alice", "course_role": "admin"},
            "answers": [],
            "comments": [{
                "id": 10, "user_id": 3, "parent_id": None, "type": "comment",
                "content": "<c/>", "is_endorsed": False, "is_private": False,
                "is_resolved": False, "is_anonymous": False, "vote_count": 0,
                "created_at": "2025-01-01",
            }],
            # Bloat
            "editor_id": 99, "deleted_at": None, "document": "text",
            "flag_count": 0, "star_count": 0,
        },
        "users": [
            {"id": 5, "name": "Alice", "course_role": "admin", "avatar": "url"},
        ],
    }
    result = _trim_thread_detail(data)
    assert "editor_id" not in result
    assert "document" not in result
    # Participants list is redundant with per-comment user info
    assert "users" not in result
    assert len(result["comments"]) == 1
    # Null values should be omitted
    assert "accepted_id" not in result
    assert "duplicate_id" not in result
    # Empty subcategory should be omitted
    assert "subcategory" not in result


def test_trim_thread_detail_resolves_comment_users():
    """Comments get user names from the response-level users array."""
    data = {
        "thread": {
            "id": 1, "number": 42, "type": "question", "title": "Help",
            "content": "<doc/>", "category": "General",
            "course_id": 10, "created_at": "2025-01-01",
            "is_pinned": False, "is_private": False,
            "is_endorsed": False, "is_answered": True, "is_locked": False,
            "is_anonymous": False, "reply_count": 2, "vote_count": 0,
            "unresolved_count": 0,
            "user": {"id": 5, "name": "Alice", "course_role": "admin"},
            "answers": [{
                "id": 20, "user_id": 6, "parent_id": None, "type": "answer",
                "content": "<a/>", "is_endorsed": True, "is_private": False,
                "is_resolved": False, "is_anonymous": False, "vote_count": 1,
                "created_at": "2025-01-01",
            }],
            "comments": [{
                "id": 10, "user_id": 7, "parent_id": None, "type": "comment",
                "content": "<c/>", "is_endorsed": False, "is_private": False,
                "is_resolved": False, "is_anonymous": False, "vote_count": 0,
                "created_at": "2025-01-01",
                "comments": [{
                    "id": 11, "user_id": 5, "parent_id": 10, "type": "comment",
                    "content": "<r/>", "is_endorsed": False, "is_private": False,
                    "is_resolved": False, "is_anonymous": False, "vote_count": 0,
                    "created_at": "2025-01-02",
                }],
            }],
        },
        "users": [
            {"id": 5, "name": "Alice", "course_role": "admin"},
            {"id": 6, "name": "Bob", "course_role": "staff"},
            {"id": 7, "name": "Carol", "course_role": "student"},
        ],
    }
    result = _trim_thread_detail(data)
    # Answer should have user resolved from users array
    assert result["answers"][0]["user"]["name"] == "Bob"
    # Comment should have user resolved
    assert result["comments"][0]["user"]["name"] == "Carol"
    # Nested reply should also have user resolved
    assert result["comments"][0]["comments"][0]["user"]["name"] == "Alice"
    # Top-level users array should not be in output
    assert "users" not in result


def test_trim_thread_detail_anonymous_comment_has_no_user():
    """Anonymous comments should not have user info even if user_id is present."""
    data = {
        "thread": {
            "id": 1, "number": 42, "type": "question", "title": "Help",
            "content": "<doc/>", "category": "General",
            "course_id": 10, "created_at": "2025-01-01",
            "is_pinned": False, "is_private": False,
            "is_endorsed": False, "is_answered": False, "is_locked": False,
            "is_anonymous": False, "reply_count": 1, "vote_count": 0,
            "unresolved_count": 0,
            "user": {"id": 5, "name": "Alice", "course_role": "admin"},
            "answers": [],
            "comments": [{
                "id": 10, "user_id": 7, "parent_id": None, "type": "comment",
                "content": "<c/>", "is_endorsed": False, "is_private": False,
                "is_resolved": False, "is_anonymous": True, "vote_count": 0,
                "created_at": "2025-01-01",
            }],
        },
        "users": [
            {"id": 5, "name": "Alice", "course_role": "admin"},
            {"id": 7, "name": "Carol", "course_role": "student"},
        ],
    }
    result = _trim_thread_detail(data)
    # Anonymous comment should not have user info
    assert "user" not in result["comments"][0]


# ------------------------------------------------------------------
# User & courses tools
# ------------------------------------------------------------------


async def test_get_user(mock_client):
    mock_client.get_user.return_value = {
        "user": {"id": 1, "name": "Alice", "email": "a@b.com", "role": "admin"},
        "courses": [{"course": {"id": 10}}],
    }
    result = _parse(await get_user())
    assert result["name"] == "Alice"
    assert result["course_count"] == 1
    # PII stripped by default — no id or email
    assert set(result.keys()) == {"name", "role", "course_count"}


async def test_list_courses(mock_client):
    mock_client.get_user.return_value = {
        "user": {"id": 1},
        "courses": [
            {"course": {"id": 10, "code": "CS101", "name": "Intro", "year": 2025, "session": "S1", "status": "active"}},
        ],
    }
    result = _parse(await list_courses())
    assert len(result) == 1
    assert result[0]["code"] == "CS101"


async def test_get_course_stats(mock_client):
    mock_client.get_course_stats.return_value = {
        "stats": {"student_enrollment_count": 200},
    }
    mock_client.list_threads.return_value = {
        "threads": [
            {"id": 1, "category": "General"},
            {"id": 2, "category": "General"},
            {"id": 3, "category": "Labs"},
        ],
    }
    result = _parse(await get_course_stats(1))
    assert result["enrollment"] == 200
    assert "unanswered" in result
    assert "unresolved" in result
    assert result["top_categories"][0]["name"] == "General"
    assert result["top_categories"][0]["count"] == 2


# ------------------------------------------------------------------
# Thread tools
# ------------------------------------------------------------------


async def test_list_categories(mock_client):
    mock_client.get_course.return_value = {
        "course": {
            "settings": {
                "discussion": {
                    "categories": [
                        {"name": "General", "subcategories": []},
                        {"name": "Assignments", "subcategories": [
                            {"name": "P1", "subcategories": []},
                            {"name": "P2", "subcategories": []},
                        ]},
                    ],
                },
            },
        },
    }
    result = _parse(await list_categories(1))
    assert len(result) == 2
    assert result[0]["name"] == "General"
    assert "subcategories" not in result[0]  # empty list omitted
    assert result[1]["subcategories"] == ["P1", "P2"]


async def test_list_threads(mock_client):
    mock_client.list_threads.return_value = {
        "threads": [{
            "id": 1, "number": 1, "type": "post", "title": "Hi",
            "category": "General", "subcategory": "",
            "created_at": "2025-01-01", "is_pinned": False, "is_private": False,
            "is_endorsed": False, "is_answered": False, "is_locked": False,
            "reply_count": 0, "vote_count": 0, "view_count": 10,
            "unresolved_count": 0,
            "user": {"name": "Bob"},
            "content": "should be stripped",
        }],
    }
    result = _parse(await list_threads(1))
    assert "content" not in result["threads"][0]
    assert result["threads"][0]["user"] == "Bob"


async def test_get_thread(mock_client):
    mock_client.get_thread.return_value = {
        "thread": {
            "id": 1, "number": 1, "type": "post", "title": "Hi",
            "content": "<doc/>", "category": "General", "subcategory": "",
            "course_id": 10, "user_id": 5, "accepted_id": None, "duplicate_id": None,
            "created_at": "2025-01-01", "is_pinned": False, "is_private": False,
            "is_endorsed": False, "is_answered": False, "is_locked": False,
            "is_anonymous": False, "reply_count": 0, "vote_count": 0,
            "unresolved_count": 0,
            "answers": [], "comments": [],
            "editor_id": 99,  # bloat
        },
        "users": [],
    }
    result = _parse(await get_thread(1))
    assert result["content"] == "<doc/>"
    assert "editor_id" not in result


async def test_get_course_thread(mock_client):
    mock_client.get_course_thread.return_value = {
        "thread": {
            "id": 1, "number": 42, "type": "question", "title": "Q42",
            "content": "<q/>", "category": "", "subcategory": "",
            "course_id": 10, "user_id": 5, "accepted_id": None, "duplicate_id": None,
            "created_at": "2025-01-01", "is_pinned": False, "is_private": False,
            "is_endorsed": False, "is_answered": False, "is_locked": False,
            "is_anonymous": False, "reply_count": 0, "vote_count": 0,
            "unresolved_count": 0,
            "answers": [], "comments": [],
        },
        "users": [],
    }
    result = _parse(await get_course_thread(10, 42))
    assert result["number"] == 42


async def test_get_thread_by_url_valid(mock_client):
    mock_client.get_thread.return_value = {
        "thread": {
            "id": 220, "number": 220, "type": "post", "title": "URL thread",
            "content": "<doc/>", "category": "", "subcategory": "",
            "course_id": 12345, "user_id": 5, "accepted_id": None,
            "duplicate_id": None,
            "created_at": "2025-01-01", "is_pinned": False, "is_private": False,
            "is_endorsed": False, "is_answered": False, "is_locked": False,
            "is_anonymous": False, "reply_count": 0, "vote_count": 0,
            "unresolved_count": 0,
            "answers": [], "comments": [],
        },
        "users": [],
    }
    result = _parse(await get_thread_by_url("https://edstem.org/au/courses/12345/discussion/220"))
    assert result["number"] == 220
    mock_client.get_thread.assert_called_with(220)


async def test_get_thread_by_url_invalid():
    result = await get_thread_by_url("https://example.com/not-ed")
    assert result.startswith("Error:")


async def test_accept_answer(mock_client):
    mock_client.accept_answer.return_value = {
        "thread": {"id": 1, "accepted_id": 55},
    }
    result = _parse(await accept_answer(1, 55))
    assert result == {"id": 1, "accepted_id": 55}


async def test_mark_duplicate(mock_client):
    mock_client.mark_duplicate.return_value = {}
    result = _parse(await mark_duplicate(1, 2))
    assert result == {"id": 1, "duplicate_id": 2}


async def test_unmark_duplicate(mock_client):
    mock_client.unmark_duplicate.return_value = {}
    result = _parse(await unmark_duplicate(1))
    assert result == {"id": 1, "duplicate_id": None}


async def test_search_threads(mock_client):
    mock_client.search_threads.return_value = {
        "threads": [{
            "id": 5, "number": 5, "type": "post", "title": "Match",
            "category": "", "subcategory": "",
            "created_at": "2025-01-01", "is_pinned": False, "is_private": False,
            "is_endorsed": False, "is_answered": False, "is_locked": False,
            "reply_count": 0, "vote_count": 0, "view_count": 0,
            "unresolved_count": 0,
            "user": {"name": "Eve"},
        }],
    }
    result = _parse(await search_threads(1, "keyword"))
    assert len(result["threads"]) == 1


async def test_create_thread(mock_client):
    mock_client.create_thread.return_value = {
        "thread": {"id": 99, "number": 1, "title": "New"},
    }
    result = _parse(await create_thread(1, "New", "<doc/>"))
    assert set(result.keys()) == {"id", "number", "title", "url"}


async def test_edit_thread(mock_client):
    mock_client.edit_thread.return_value = {
        "thread": {"id": 10, "number": 42, "title": "Updated"},
    }
    result = _parse(await edit_thread(10, title="Updated"))
    assert result["title"] == "Updated"


async def test_bulk_recategorise(mock_client):
    mock_client.edit_thread.return_value = {"thread": {"id": 1}}
    result = _parse(await bulk_recategorise([1, 2, 3], "General"))
    assert result["updated"] == 3
    assert result["total"] == 3
    assert "failed" not in result


async def test_bulk_recategorise_partial_failure(mock_client):
    async def _side_effect(tid, **kwargs):
        if tid == 2:
            raise EdAPIError(404, "Not Found")
        return {"thread": {"id": tid}}

    mock_client.edit_thread.side_effect = _side_effect
    result = _parse(await bulk_recategorise([1, 2, 3], "General"))
    assert result["updated"] == 2
    assert result["total"] == 3
    assert len(result["failed"]) == 1
    assert result["failed"][0]["id"] == 2


async def test_delete_thread(mock_client):
    mock_client.delete_thread.return_value = {}
    result = await delete_thread(1)
    assert result == "Thread deleted."


# ------------------------------------------------------------------
# Moderation tools
# ------------------------------------------------------------------


async def test_lock_thread(mock_client):
    mock_client.lock_thread.return_value = {}
    assert await lock_thread(1) == "Thread locked."


async def test_unlock_thread(mock_client):
    mock_client.unlock_thread.return_value = {}
    assert await unlock_thread(1) == "Thread unlocked."


async def test_pin_thread(mock_client):
    mock_client.pin_thread.return_value = {}
    assert await pin_thread(1) == "Thread pinned."


async def test_unpin_thread(mock_client):
    mock_client.unpin_thread.return_value = {}
    assert await unpin_thread(1) == "Thread unpinned."


async def test_endorse_thread(mock_client):
    mock_client.endorse_thread.return_value = {}
    assert await endorse_thread(1) == "Thread endorsed."


async def test_unendorse_thread(mock_client):
    mock_client.unendorse_thread.return_value = {}
    assert await unendorse_thread(1) == "Endorsement removed."


# ------------------------------------------------------------------
# Users & analytics tools
# ------------------------------------------------------------------


async def test_list_users(mock_client):
    mock_client.list_users.return_value = {
        "users": [
            {"id": 1, "name": "Alice", "course_role": "student", "avatar": "url"},
        ],
    }
    result = _parse(await list_users(1))
    assert result["total"] == 1
    assert set(result["users"][0].keys()) == {"id", "name", "course_role"}


async def test_get_user_activity(mock_client):
    mock_client.get_user_activity.return_value = {
        "items": [
            {"type": "thread", "value": {
                "id": 1, "type": "post", "course_id": 10, "title": "T",
                "category": "General", "subcategory": "",
                "document": "<doc>content</doc>", "created_at": "2025-01-01"}},
            {"type": "comment", "value": {
                "id": 2, "type": "comment", "thread_id": 1, "thread_title": "T",
                "thread_category": "General",
                "document": "<doc>reply</doc>", "created_at": "2025-01-01"}},
        ],
    }
    result = _parse(await get_user_activity(1, 1))
    assert len(result) == 2
    assert result[0]["kind"] == "thread"
    assert result[1]["kind"] == "comment"
    # document (full body text) is stripped from activity listings to save tokens
    assert "document" not in result[0]
    assert "document" not in result[1]


# ------------------------------------------------------------------
# Attendance tools
# ------------------------------------------------------------------


async def test_get_attendance_session_strips_admin_fields(mock_client):
    """Admin-only fields waste tokens — keep only operationally useful keys."""
    mock_client.get_attendance_session.return_value = {
        "event": {
            "id": 1, "course_id": 10, "title": "Week 3 Tutorial",
            "content": '<document version="2.0"><paragraph/></document>',
            "is_closed": False, "is_hidden": False,
            "start": "2026-03-01T09:00:00+11:00", "created_at": "2026-02-28T10:00:00+11:00",
            # These admin fields should be stripped
            "no_screen": False, "qr_expiry": 300, "index": 2,
            "grade_passback_scoring_mode": "points",
            "grade_passback_scale_to": 100,
        },
    }
    mock_client.list_check_ins.return_value = {"check_ins": []}
    result = _parse(await get_attendance_session(1))
    assert result["title"] == "Week 3 Tutorial"
    assert "no_screen" not in result
    assert "qr_expiry" not in result
    assert "index" not in result
    assert "grade_passback_scoring_mode" not in result
    assert "grade_passback_scale_to" not in result


# ------------------------------------------------------------------
# Comment tools
# ------------------------------------------------------------------


async def test_reply_to_thread(mock_client):
    mock_client.reply_to_thread.return_value = {
        "comment": {"id": 77, "thread_id": 10, "type": "comment"},
    }
    result = _parse(await reply_to_thread(10, "<doc/>"))
    assert set(result.keys()) == {"id", "thread_id", "type"}


async def test_edit_comment(mock_client):
    mock_client.edit_comment.return_value = {
        "comment": {"id": 77, "thread_id": 10, "content": "<new/>"},
    }
    result = _parse(await edit_comment(77, "<new/>"))
    assert set(result.keys()) == {"id", "thread_id"}


async def test_delete_comment(mock_client):
    mock_client.delete_comment.return_value = {}
    result = await delete_comment(77)
    assert result == "Comment deleted."


# ------------------------------------------------------------------
# Error handling
# ------------------------------------------------------------------


async def test_tool_returns_error_string(mock_client):
    mock_client.get_user.side_effect = EdAPIError(500, "Internal error")
    result = await get_user()
    assert result == "Error: Internal error"


# ------------------------------------------------------------------
# Compact JSON format
# ------------------------------------------------------------------


async def test_output_is_compact_json(mock_client):
    mock_client.get_user.return_value = {
        "user": {"id": 1, "name": "Alice", "email": "a@b.com", "role": "admin"},
        "courses": [],
    }
    result = await get_user()
    # Compact JSON has no spaces after separators
    assert " " not in result.replace("Alice", "X")


# ------------------------------------------------------------------
# PII stripping
# ------------------------------------------------------------------


def test_pii_enabled_default():
    """PII stripping is ON when ED_STRIP_PII is unset."""
    assert _pii_enabled() is True


def test_pii_enabled_explicit_false(monkeypatch):
    monkeypatch.setenv("ED_STRIP_PII", "false")
    assert _pii_enabled() is False


def test_pii_enabled_case_insensitive(monkeypatch):
    monkeypatch.setenv("ED_STRIP_PII", "False")
    assert _pii_enabled() is False


def test_pii_enabled_other_values(monkeypatch):
    for val in ("true", "1", "yes", ""):
        monkeypatch.setenv("ED_STRIP_PII", val)
        assert _pii_enabled() is True


def test_scrub_emails():
    assert _scrub_emails("Contact alice@uni.edu.au for help") == "Contact [email] for help"
    assert _scrub_emails("No emails here") == "No emails here"
    assert _scrub_emails("a@b.com and c@d.org") == "[email] and [email]"


def test_strip_user_pii():
    user = {"id": 1, "name": "Alice", "course_role": "student", "email": "a@b.com", "avatar": "url"}
    result = _strip_user_pii(user)
    assert result == {"name": "Alice", "course_role": "student"}


def test_strip_user_pii_minimal():
    """Works when only name is present."""
    assert _strip_user_pii({"name": "Bob"}) == {"name": "Bob"}


def test_trim_comment_pii_scrubs_email_from_content():
    comment = {
        "id": 1, "parent_id": None, "type": "comment",
        "content": "<doc>Email alice@uni.edu</doc>",
        "is_endorsed": False, "is_private": False,
        "is_resolved": False, "is_anonymous": False,
        "vote_count": 0, "created_at": "2025-01-01",
        "user": {"id": 1, "name": "Alice", "course_role": "student", "email": "a@b.com"},
    }
    result = _trim_comment(comment)
    assert "[email]" in result["content"]
    assert "alice@uni.edu" not in result["content"]
    assert "id" not in result["user"]
    assert "email" not in result["user"]
    assert result["user"]["name"] == "Alice"


def test_trim_comment_pii_disabled_keeps_user_id(monkeypatch):
    monkeypatch.setenv("ED_STRIP_PII", "false")
    comment = {
        "id": 1, "parent_id": None, "type": "comment",
        "content": "<doc>Email alice@uni.edu</doc>",
        "is_endorsed": False, "is_private": False,
        "is_resolved": False, "is_anonymous": False,
        "vote_count": 0, "created_at": "2025-01-01",
        "user": {"id": 1, "name": "Alice", "course_role": "student"},
    }
    result = _trim_comment(comment)
    # Email not scrubbed when PII disabled
    assert "alice@uni.edu" in result["content"]
    # User id preserved
    assert result["user"]["id"] == 1


def test_trim_thread_detail_pii_strips_users():
    data = {
        "thread": {
            "id": 1, "number": 42, "type": "post", "title": "Hello",
            "content": "<doc>Contact bob@school.com</doc>",
            "category": "General", "subcategory": "",
            "course_id": 10, "accepted_id": None, "duplicate_id": None,
            "created_at": "2025-01-01", "is_pinned": False, "is_private": False,
            "is_endorsed": False, "is_answered": False, "is_locked": False,
            "is_anonymous": False, "reply_count": 0, "vote_count": 0,
            "unresolved_count": 0,
            "user": {"id": 5, "name": "Alice", "course_role": "admin", "email": "a@b.com"},
            "answers": [], "comments": [],
        },
        "users": [
            {"id": 5, "name": "Alice", "course_role": "admin", "avatar": "url", "email": "a@b.com"},
        ],
    }
    result = _trim_thread_detail(data)
    # Thread user stripped
    assert "id" not in result["user"]
    assert "email" not in result["user"]
    assert result["user"]["name"] == "Alice"
    # Participants list dropped entirely (redundant with per-comment users)
    assert "users" not in result
    # Email in content scrubbed
    assert "bob@school.com" not in result["content"]
    assert "[email]" in result["content"]


def test_trim_thread_detail_pii_disabled_preserves_all(monkeypatch):
    monkeypatch.setenv("ED_STRIP_PII", "false")
    data = {
        "thread": {
            "id": 1, "number": 42, "type": "post", "title": "Hello",
            "content": "<doc>Contact bob@school.com</doc>",
            "category": "General", "subcategory": "",
            "course_id": 10, "accepted_id": None, "duplicate_id": None,
            "created_at": "2025-01-01", "is_pinned": False, "is_private": False,
            "is_endorsed": False, "is_answered": False, "is_locked": False,
            "is_anonymous": False, "reply_count": 0, "vote_count": 0,
            "unresolved_count": 0,
            "user": {"id": 5, "name": "Alice", "course_role": "admin"},
            "answers": [], "comments": [],
        },
        "users": [
            {"id": 5, "name": "Alice", "course_role": "admin"},
        ],
    }
    result = _trim_thread_detail(data)
    assert result["user"]["id"] == 5
    # Participants list dropped regardless of PII setting
    assert "users" not in result
    assert "bob@school.com" in result["content"]


async def test_get_user_pii_disabled(mock_client, monkeypatch):
    monkeypatch.setenv("ED_STRIP_PII", "false")
    mock_client.get_user.return_value = {
        "user": {"id": 1, "name": "Alice", "email": "a@b.com", "role": "admin"},
        "courses": [{"course": {"id": 10}}],
    }
    result = _parse(await get_user())
    assert result["id"] == 1
    assert result["email"] == "a@b.com"
    assert set(result.keys()) == {"id", "name", "email", "role", "course_count"}


async def test_list_users_pii_maps_user_id(mock_client):
    """API returns user_id; server maps it to id for tool chaining."""
    mock_client.list_users.return_value = {
        "users": [
            {"user_id": 42, "name": "Alice", "course_role": "student", "email": "a@b.com"},
        ],
    }
    result = _parse(await list_users(1))
    assert result["users"][0]["id"] == 42
    assert "email" not in result["users"][0]
    assert set(result["users"][0].keys()) == {"id", "name", "course_role"}


async def test_list_users_pii_disabled(mock_client, monkeypatch):
    monkeypatch.setenv("ED_STRIP_PII", "false")
    mock_client.list_users.return_value = {
        "users": [
            {"user_id": 42, "name": "Alice", "course_role": "student", "email": "a@b.com"},
        ],
    }
    result = _parse(await list_users(1))
    assert result["users"][0]["id"] == 42
    assert result["users"][0]["email"] == "a@b.com"
    assert set(result["users"][0].keys()) == {"id", "name", "course_role", "email"}
