"""Tests for MCP server tools — verify response trimming and output shape."""

from __future__ import annotations

import json

from edstem_mcp.client import EdAPIError
from edstem_mcp.server import (
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
    result = _summarise_threads(threads)
    assert len(result) == 1
    assert result[0]["user"] == "Alice"
    assert "content" not in result[0]
    assert "editor_id" not in result[0]


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
    assert "avatar" not in result["users"][0]
    assert len(result["comments"]) == 1


# ------------------------------------------------------------------
# User & courses tools
# ------------------------------------------------------------------


async def test_get_user(mock_client):
    mock_client.get_user.return_value = {
        "user": {"id": 1, "name": "Alice", "email": "a@b.com", "role": "admin"},
        "courses": [{"course": {"id": 10}}],
    }
    result = _parse(await get_user())
    assert result["id"] == 1
    assert result["course_count"] == 1
    assert set(result.keys()) == {"id", "name", "email", "role", "course_count"}


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
    mock_client.get_course_thread.return_value = {
        "thread": {
            "id": 1, "number": 220, "type": "post", "title": "URL thread",
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
    mock_client.get_course_thread.assert_called_with(12345, 220)


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
    assert set(result.keys()) == {"id", "number", "title"}


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
    assert len(result) == 1
    assert set(result[0].keys()) == {"id", "name", "course_role"}


async def test_get_user_activity(mock_client):
    mock_client.get_user_activity.return_value = {
        "activity": [
            {"thread": {"id": 1, "number": 10, "type": "post", "title": "T",
                         "category": "General", "created_at": "2025-01-01",
                         "content": "bloat"}},
            {"comment": {"id": 2, "type": "comment", "thread_id": 1,
                          "created_at": "2025-01-01", "content": "bloat"}},
        ],
    }
    result = _parse(await get_user_activity(1, 1))
    assert len(result) == 2
    assert result[0]["kind"] == "thread"
    assert "content" not in result[0]
    assert result[1]["kind"] == "comment"
    assert "content" not in result[1]


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
    assert " " not in result.replace("Alice", "X").replace("a@b.com", "X")
