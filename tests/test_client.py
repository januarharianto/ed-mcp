"""Tests for EdClient — all HTTP calls mocked via respx."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from edstem_mcp.client import EdAuthError, EdClient, EdNotFoundError


BASE = "https://edstem.org/api"


# ------------------------------------------------------------------
# Construction
# ------------------------------------------------------------------


def test_missing_token(monkeypatch):
    monkeypatch.delenv("ED_API_TOKEN", raising=False)
    with pytest.raises(EdAuthError, match="ED_API_TOKEN"):
        EdClient()


def test_custom_base_url(monkeypatch):
    monkeypatch.setenv("ED_BASE_URL", "https://custom.example.com/api/")
    client = EdClient()
    assert client.base_url == "https://custom.example.com/api"


# ------------------------------------------------------------------
# Error mapping
# ------------------------------------------------------------------


@respx.mock
async def test_401_raises_auth_error():
    respx.get(f"{BASE}/user").mock(return_value=Response(401, json={"message": "bad token"}))
    client = EdClient()
    with pytest.raises(EdAuthError):
        await client.get_user()
    await client.close()


@respx.mock
async def test_404_raises_not_found():
    respx.get(f"{BASE}/threads/999").mock(return_value=Response(404, json={"message": "Not Found"}))
    client = EdClient()
    with pytest.raises(EdNotFoundError):
        await client.get_thread(999)
    await client.close()


# ------------------------------------------------------------------
# User & courses
# ------------------------------------------------------------------


@respx.mock
async def test_get_user():
    payload = {"user": {"id": 1, "name": "Alice"}, "courses": []}
    respx.get(f"{BASE}/user").mock(return_value=Response(200, json=payload))
    client = EdClient()
    result = await client.get_user()
    assert result["user"]["name"] == "Alice"
    await client.close()


# ------------------------------------------------------------------
# Threads
# ------------------------------------------------------------------


@respx.mock
async def test_list_threads():
    payload = {"threads": [{"id": 10, "title": "Hello"}]}
    respx.get(f"{BASE}/courses/1/threads").mock(return_value=Response(200, json=payload))
    client = EdClient()
    result = await client.list_threads(1)
    assert len(result["threads"]) == 1
    assert result["threads"][0]["id"] == 10
    await client.close()


@respx.mock
async def test_get_thread():
    payload = {"thread": {"id": 10, "title": "Hello", "type": "post"}}
    respx.get(f"{BASE}/threads/10").mock(return_value=Response(200, json=payload))
    client = EdClient()
    result = await client.get_thread(10)
    assert result["thread"]["title"] == "Hello"
    await client.close()


@respx.mock
async def test_get_course_thread():
    payload = {"thread": {"id": 10, "number": 42, "title": "Q42"}}
    respx.get(f"{BASE}/courses/1/threads/42").mock(return_value=Response(200, json=payload))
    client = EdClient()
    result = await client.get_course_thread(1, 42)
    assert result["thread"]["number"] == 42
    await client.close()


@respx.mock
async def test_search_threads():
    payload = {"threads": [{"id": 10, "title": "Match"}]}
    respx.get(f"{BASE}/courses/1/threads").mock(return_value=Response(200, json=payload))
    client = EdClient()
    result = await client.search_threads(1, "keyword")
    assert len(result["threads"]) == 1
    await client.close()


@respx.mock
async def test_create_thread():
    payload = {"thread": {"id": 99, "number": 1, "title": "New"}}
    respx.post(f"{BASE}/courses/1/threads").mock(return_value=Response(200, json=payload))
    client = EdClient()
    result = await client.create_thread(1, title="New", content="<document/>")
    assert result["thread"]["id"] == 99
    await client.close()


@respx.mock
async def test_edit_thread():
    # edit_thread fetches current state first
    current = {"thread": {
        "id": 10, "type": "post", "title": "Old", "content": "<doc/>",
        "category": "General", "subcategory": "", "subsubcategory": "",
        "is_pinned": False, "is_private": False, "is_anonymous": False,
        "is_megathread": False, "anonymous_comments": False,
    }}
    updated = {"thread": {"id": 10, "type": "post", "title": "New Title"}}
    respx.get(f"{BASE}/threads/10").mock(return_value=Response(200, json=current))
    respx.put(f"{BASE}/threads/10").mock(return_value=Response(200, json=updated))
    client = EdClient()
    result = await client.edit_thread(10, title="New Title")
    assert result["thread"]["title"] == "New Title"
    # Verify PUT was called with merged body
    put_call = respx.calls[-1]
    body = put_call.request.content
    assert b"New Title" in body
    await client.close()


@respx.mock
async def test_accept_answer():
    current = {"thread": {
        "id": 10, "type": "question", "title": "Q", "content": "<doc/>",
        "category": "", "subcategory": "", "subsubcategory": "",
        "is_pinned": False, "is_private": False, "is_anonymous": False,
        "is_megathread": False, "anonymous_comments": False,
    }}
    accepted = {"thread": {"id": 10, "accepted_id": 55}}
    respx.get(f"{BASE}/threads/10").mock(return_value=Response(200, json=current))
    respx.put(f"{BASE}/threads/10").mock(return_value=Response(200, json=accepted))
    client = EdClient()
    result = await client.accept_answer(10, 55)
    assert result["thread"]["accepted_id"] == 55
    await client.close()


@respx.mock
async def test_mark_duplicate():
    respx.post(f"{BASE}/threads/10/mark_duplicate").mock(return_value=Response(204))
    client = EdClient()
    result = await client.mark_duplicate(10, 20)
    assert result == {}
    await client.close()


@respx.mock
async def test_unmark_duplicate():
    respx.post(f"{BASE}/threads/10/mark_duplicate").mock(return_value=Response(204))
    client = EdClient()
    result = await client.unmark_duplicate(10)
    assert result == {}
    await client.close()


@respx.mock
async def test_get_course():
    payload = {"course": {"id": 1, "name": "Test Course", "settings": {}}}
    respx.get(f"{BASE}/courses/1").mock(return_value=Response(200, json=payload))
    client = EdClient()
    result = await client.get_course(1)
    assert result["course"]["name"] == "Test Course"
    await client.close()


@respx.mock
async def test_get_course_stats():
    payload = {"stats": {"course_id": 1, "student_enrollment_count": 100}}
    respx.get(f"{BASE}/courses/1/stats").mock(return_value=Response(200, json=payload))
    client = EdClient()
    result = await client.get_course_stats(1)
    assert result["stats"]["student_enrollment_count"] == 100
    await client.close()


@respx.mock
async def test_delete_thread():
    respx.delete(f"{BASE}/threads/10").mock(return_value=Response(204))
    client = EdClient()
    result = await client.delete_thread(10)
    assert result == {}
    await client.close()


# ------------------------------------------------------------------
# Moderation
# ------------------------------------------------------------------


@respx.mock
@pytest.mark.parametrize("method,path_suffix", [
    ("lock_thread", "lock"),
    ("unlock_thread", "unlock"),
    ("pin_thread", "pin"),
    ("unpin_thread", "unpin"),
    ("endorse_thread", "endorse"),
    ("unendorse_thread", "unendorse"),
])
async def test_moderation_actions(method, path_suffix):
    respx.post(f"{BASE}/threads/10/{path_suffix}").mock(return_value=Response(204))
    client = EdClient()
    result = await getattr(client, method)(10)
    assert result == {}
    await client.close()


# ------------------------------------------------------------------
# Users & analytics
# ------------------------------------------------------------------


@respx.mock
async def test_list_users():
    payload = {"users": [{"id": 1, "name": "Alice", "course_role": "student"}]}
    respx.get(f"{BASE}/courses/1/analytics/users").mock(return_value=Response(200, json=payload))
    client = EdClient()
    result = await client.list_users(1)
    assert len(result["users"]) == 1
    await client.close()


@respx.mock
async def test_get_user_activity():
    payload = {"activity": [{"thread": {"id": 5, "title": "Q"}}]}
    respx.get(f"{BASE}/users/1/profile/activity").mock(return_value=Response(200, json=payload))
    client = EdClient()
    result = await client.get_user_activity(1, 1)
    assert len(result["activity"]) == 1
    await client.close()


# ------------------------------------------------------------------
# Comments
# ------------------------------------------------------------------


@respx.mock
async def test_reply_to_thread():
    payload = {"comment": {"id": 77, "thread_id": 10, "type": "comment"}}
    respx.post(f"{BASE}/threads/10/comments").mock(return_value=Response(200, json=payload))
    client = EdClient()
    result = await client.reply_to_thread(10, content="<doc/>")
    assert result["comment"]["id"] == 77
    await client.close()


@respx.mock
async def test_reply_with_parent_id():
    payload = {"comment": {"id": 78, "thread_id": 10, "type": "comment", "parent_id": 77}}
    respx.post(f"{BASE}/threads/10/comments").mock(return_value=Response(200, json=payload))
    client = EdClient()
    result = await client.reply_to_thread(10, content="<doc/>", parent_id=77)
    assert result["comment"]["parent_id"] == 77
    # Verify parent_id was sent in the request body
    body = respx.calls[-1].request.content
    assert b"parent_id" in body
    await client.close()


@respx.mock
async def test_edit_comment():
    payload = {"comment": {"id": 77, "thread_id": 10, "content": "<new/>"}}
    respx.put(f"{BASE}/comments/77").mock(return_value=Response(200, json=payload))
    client = EdClient()
    result = await client.edit_comment(77, content="<new/>")
    assert result["comment"]["id"] == 77
    await client.close()


@respx.mock
async def test_delete_comment():
    respx.delete(f"{BASE}/comments/77").mock(return_value=Response(204))
    client = EdClient()
    result = await client.delete_comment(77)
    assert result == {}
    await client.close()
