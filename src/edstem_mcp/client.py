"""Async HTTP client for the Ed Discussion API."""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Any

import httpx


class EdAPIError(Exception):
    """Base exception for Ed API errors."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"Ed API error {status_code}: {message}")


class EdAuthError(EdAPIError):
    """Authentication/authorisation error (401/403)."""


class EdNotFoundError(EdAPIError):
    """Resource not found (404)."""


_ERROR_MAP: dict[int, type[EdAPIError]] = {
    401: EdAuthError,
    403: EdAuthError,
    404: EdNotFoundError,
}


class EdClient:
    """Async client for the Ed Discussion API.

    Reads ``ED_API_TOKEN`` (required) and ``ED_BASE_URL`` (optional) from the
    environment.  All methods are async and return parsed JSON dicts.
    """

    def __init__(self) -> None:
        token = os.environ.get("ED_API_TOKEN")
        if not token:
            raise EdAuthError(401, "ED_API_TOKEN environment variable is not set")

        self.base_url = os.environ.get("ED_BASE_URL", "https://edstem.org/api").rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a request and return parsed JSON, raising on errors."""
        response = await self._client.request(
            method,
            path,
            json=json,
            params=params,
            files=files,
        )
        if not response.is_success:
            try:
                body = response.json()
                message = body.get("message", response.text)
            except Exception:
                message = response.text
            exc_cls = _ERROR_MAP.get(response.status_code, EdAPIError)
            raise exc_cls(response.status_code, message)

        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        cleaned = {k: v for k, v in params.items() if v is not None}
        return await self._request("GET", path, params=cleaned or None)

    async def _post(self, path: str, json: Any | None = None) -> dict[str, Any]:
        return await self._request("POST", path, json=json)

    async def _put(self, path: str, json: Any | None = None) -> dict[str, Any]:
        return await self._request("PUT", path, json=json)

    async def _delete(self, path: str) -> dict[str, Any]:
        return await self._request("DELETE", path)

    # ------------------------------------------------------------------
    # User & courses
    # ------------------------------------------------------------------

    async def get_user(self) -> dict[str, Any]:
        """Get the authenticated user's info and enrolled courses."""
        return await self._get("/user")

    # ------------------------------------------------------------------
    # Threads
    # ------------------------------------------------------------------

    async def list_threads(
        self,
        course_id: int,
        *,
        limit: int = 30,
        offset: int = 0,
        sort: str | None = None,
        filter: str | None = None,
    ) -> dict[str, Any]:
        """List threads in a course with pagination."""
        return await self._get(
            f"/courses/{course_id}/threads",
            limit=limit,
            offset=offset,
            sort=sort,
            filter=filter,
        )

    async def get_thread(self, thread_id: int) -> dict[str, Any]:
        """Get a thread by its global ID, including comments."""
        return await self._get(f"/threads/{thread_id}")

    async def get_course_thread(self, course_id: int, number: int) -> dict[str, Any]:
        """Get a thread by its course-relative number (e.g. #42)."""
        return await self._get(f"/courses/{course_id}/threads/{number}")

    async def search_threads(
        self,
        course_id: int,
        query: str,
        *,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search threads in a course by keyword."""
        return await self._get(
            f"/courses/{course_id}/threads",
            limit=limit,
            sort="new",
            filter=f"search:{query}",
        )

    async def create_thread(
        self,
        course_id: int,
        *,
        title: str,
        content: str,
        type: str = "post",
        category: str = "",
        subcategory: str = "",
        is_private: bool = False,
        is_anonymous: bool = False,
    ) -> dict[str, Any]:
        """Create a new thread in a course.

        ``content`` should be Ed XML, e.g.
        ``<document version="2.0"><paragraph>Hello</paragraph></document>``
        """
        return await self._post(
            f"/courses/{course_id}/threads",
            json={
                "thread": {
                    "type": type,
                    "title": title,
                    "category": category,
                    "subcategory": subcategory,
                    "subsubcategory": "",
                    "content": content,
                    "is_pinned": False,
                    "is_private": is_private,
                    "is_anonymous": is_anonymous,
                    "is_megathread": False,
                    "anonymous_comments": False,
                }
            },
        )

    async def edit_thread(
        self,
        thread_id: int,
        *,
        title: str | None = None,
        content: str | None = None,
        category: str | None = None,
        subcategory: str | None = None,
    ) -> dict[str, Any]:
        """Edit an existing thread.  Only provided fields are updated."""
        # Fetch current state so we can merge changes
        current = (await self.get_thread(thread_id))["thread"]
        updates: dict[str, Any] = {}
        if title is not None:
            updates["title"] = title
        if content is not None:
            updates["content"] = content
        if category is not None:
            updates["category"] = category
        if subcategory is not None:
            updates["subcategory"] = subcategory

        thread_body = {
            "type": current["type"],
            "title": current["title"],
            "category": current.get("category", ""),
            "subcategory": current.get("subcategory", ""),
            "subsubcategory": current.get("subsubcategory", ""),
            "content": current["content"],
            "is_pinned": current.get("is_pinned", False),
            "is_private": current.get("is_private", False),
            "is_anonymous": current.get("is_anonymous", False),
            "is_megathread": current.get("is_megathread", False),
            "anonymous_comments": current.get("anonymous_comments", False),
        }
        thread_body.update(updates)
        return await self._put(f"/threads/{thread_id}", json={"thread": thread_body})

    async def delete_thread(self, thread_id: int) -> dict[str, Any]:
        """Delete a thread."""
        return await self._delete(f"/threads/{thread_id}")

    # ------------------------------------------------------------------
    # Moderation
    # ------------------------------------------------------------------

    async def lock_thread(self, thread_id: int) -> dict[str, Any]:
        return await self._post(f"/threads/{thread_id}/lock")

    async def unlock_thread(self, thread_id: int) -> dict[str, Any]:
        return await self._post(f"/threads/{thread_id}/unlock")

    async def pin_thread(self, thread_id: int) -> dict[str, Any]:
        return await self._post(f"/threads/{thread_id}/pin")

    async def unpin_thread(self, thread_id: int) -> dict[str, Any]:
        return await self._post(f"/threads/{thread_id}/unpin")

    async def endorse_thread(self, thread_id: int) -> dict[str, Any]:
        return await self._post(f"/threads/{thread_id}/endorse")

    async def unendorse_thread(self, thread_id: int) -> dict[str, Any]:
        return await self._post(f"/threads/{thread_id}/unendorse")

    # ------------------------------------------------------------------
    # Users & analytics
    # ------------------------------------------------------------------

    async def list_users(self, course_id: int) -> dict[str, Any]:
        """List users enrolled in a course."""
        return await self._get(f"/courses/{course_id}/analytics/users")

    async def get_user_activity(
        self,
        user_id: int,
        course_id: int,
        *,
        limit: int = 30,
        offset: int = 0,
        filter: str | None = None,
    ) -> dict[str, Any]:
        """Get a user's activity in a course."""
        return await self._get(
            f"/users/{user_id}/profile/activity",
            courseID=course_id,
            limit=limit,
            offset=offset,
            filter=filter,
        )

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    async def upload_file(
        self,
        filepath: str | Path,
    ) -> dict[str, Any]:
        """Upload a local file and return its metadata (including URL)."""
        path = Path(filepath)
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        return await self._request(
            "POST",
            "/files",
            files={"attachment": (path.name, data, content_type)},
        )

    # ------------------------------------------------------------------
    # Comments / replies
    # ------------------------------------------------------------------

    async def reply_to_thread(
        self,
        thread_id: int,
        *,
        content: str,
        type: str = "comment",
        is_private: bool = False,
        is_anonymous: bool = False,
    ) -> dict[str, Any]:
        """Post a comment or answer on a thread.

        ``type`` should be ``"comment"`` or ``"answer"``.
        ``content`` should be Ed XML.
        """
        return await self._post(
            f"/threads/{thread_id}/comments",
            json={
                "comment": {
                    "type": type,
                    "content": content,
                    "is_private": is_private,
                    "is_anonymous": is_anonymous,
                }
            },
        )
