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
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
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
            # Add context for common errors
            if response.status_code == 403:
                message = (
                    f"Forbidden — the API token does not have access to "
                    f"{path}. Check that the course ID is correct and that "
                    f"you are enrolled (use list_courses to verify)."
                )
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

    async def accept_answer(self, thread_id: int, comment_id: int) -> dict[str, Any]:
        """Mark a comment as the accepted answer on a question thread."""
        current = (await self.get_thread(thread_id))["thread"]
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
            "accepted_id": comment_id,
        }
        return await self._put(f"/threads/{thread_id}", json={"thread": thread_body})

    async def mark_duplicate(self, thread_id: int, original_thread_id: int) -> dict[str, Any]:
        """Mark a thread as a duplicate of another thread."""
        return await self._post(
            f"/threads/{thread_id}/mark_duplicate",
            json={"duplicate_id": original_thread_id},
        )

    async def unmark_duplicate(self, thread_id: int) -> dict[str, Any]:
        """Remove the duplicate mark from a thread."""
        return await self._post(
            f"/threads/{thread_id}/mark_duplicate",
            json={"duplicate_id": None},
        )

    async def get_course(self, course_id: int) -> dict[str, Any]:
        """Get course details including settings."""
        return await self._get(f"/courses/{course_id}")

    async def get_course_stats(self, course_id: int) -> dict[str, Any]:
        """Get basic course stats (enrollment counts)."""
        return await self._get(f"/courses/{course_id}/stats")

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

    async def get_enrollment_stats(self, course_id: int) -> dict[str, Any]:
        """Get lightweight enrollment counts (no individual user data)."""
        return await self._get(f"/courses/{course_id}/analytics/enrollments")

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
        filter: str = "all",
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

    async def upload_file_url(self, url: str) -> dict[str, Any]:
        """Upload a file from a URL (no local download needed)."""
        return await self._post("/files/url", json={"url": url})

    async def get_discussion_threads_json(self, course_id: int) -> list[dict[str, Any]]:
        """Download all threads for a course via the bulk analytics endpoint.

        Returns a bare list (not wrapped in a dict). Uses a longer timeout
        since this endpoint returns all threads at once.

        Note: this endpoint requires the region prefix (e.g. /au/api/ instead
        of /api/), unlike the regular API endpoints.
        """
        region = os.environ.get("ED_REGION", "us")
        resp = await self._client.get(
            f"https://edstem.org/{region}/api/courses/{course_id}/analytics/discussion_threads.json",
            timeout=httpx.Timeout(120.0, connect=10.0),
        )
        if not resp.is_success:
            error_cls = _ERROR_MAP.get(resp.status_code, EdAPIError)
            raise error_cls(resp.status_code, resp.text[:200])
        return resp.json()

    async def download_file(self, url: str, dest: Path) -> tuple[Path, str]:
        """Download a file from an Ed CDN URL. Returns (path, filename)."""
        async with httpx.AsyncClient(timeout=60.0) as http:
            resp = await http.get(url)
            if not resp.is_success:
                raise EdAPIError(resp.status_code, f"Failed to download {url}")
            # Extract filename from Content-Disposition header
            cd = resp.headers.get("content-disposition", "")
            filename = ""
            if "filename=" in cd:
                for part in cd.split(";"):
                    part = part.strip()
                    if part.startswith("filename="):
                        filename = part.split("=", 1)[1].strip('" ')
                        break
            if filename:
                dest = dest.parent / filename
            dest.write_bytes(resp.content)
        return dest, filename

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
        parent_id: int | None = None,
    ) -> dict[str, Any]:
        """Post a comment or answer on a thread.

        ``type`` should be ``"comment"`` or ``"answer"``.
        ``content`` should be Ed XML.
        ``parent_id`` nests the reply under an existing comment.
        """
        body: dict[str, Any] = {
            "type": type,
            "content": content,
            "is_private": is_private,
            "is_anonymous": is_anonymous,
        }
        if parent_id is not None:
            body["parent_id"] = parent_id
        return await self._post(
            f"/threads/{thread_id}/comments",
            json={"comment": body},
        )

    async def edit_comment(self, comment_id: int, *, content: str) -> dict[str, Any]:
        """Edit an existing comment's content."""
        return await self._put(
            f"/comments/{comment_id}",
            json={"comment": {"content": content}},
        )

    async def delete_comment(self, comment_id: int) -> dict[str, Any]:
        """Delete a comment."""
        return await self._delete(f"/comments/{comment_id}")

    # ------------------------------------------------------------------
    # Attendance
    # ------------------------------------------------------------------

    async def list_attendance_sessions(self, course_id: int) -> dict[str, Any]:
        """List attendance sessions (events) for a course."""
        return await self._get(f"/courses/{course_id}/events")

    async def get_attendance_session(self, event_id: int) -> dict[str, Any]:
        """Get a single attendance session."""
        return await self._get(f"/events/{event_id}")

    async def create_attendance_session(
        self,
        course_id: int,
        *,
        title: str,
        start: str | None = None,
        content: str = '<document version="2.0"><paragraph/></document>',
        is_hidden: bool = False,
        password: str | None = None,
    ) -> dict[str, Any]:
        """Create a new attendance session."""
        return await self._post(
            f"/courses/{course_id}/events",
            json={
                "title": title,
                "content": content,
                "is_hidden": is_hidden,
                "start": start,
                "password": password,
            },
        )

    async def update_attendance_session(
        self,
        event_id: int,
        **fields: Any,
    ) -> dict[str, Any]:
        """Update an attendance session. Pass only the fields to change."""
        return await self._put(f"/events/{event_id}", json=fields)

    async def delete_attendance_session(self, event_id: int) -> dict[str, Any]:
        """Delete an attendance session."""
        return await self._delete(f"/events/{event_id}")

    async def list_check_ins(
        self,
        *,
        course_id: int | None = None,
        event_id: int | None = None,
    ) -> dict[str, Any]:
        """List check-ins for a course or a specific session."""
        if event_id is not None:
            return await self._get(f"/events/{event_id}/check_ins")
        if course_id is not None:
            return await self._get(f"/courses/{course_id}/check_ins")
        raise ValueError("Either course_id or event_id is required")

    async def manual_check_in(
        self,
        event_id: int,
        *,
        user_ids: list[int],
        kind: str = "present",
    ) -> dict[str, Any]:
        """Manually check in users with a status (present/late/excused/absent)."""
        return await self._post(
            f"/events/{event_id}/check_in",
            json={"target_user_ids": user_ids, "kind": kind},
        )

    async def undo_check_in(
        self,
        event_id: int,
        *,
        user_ids: list[int],
    ) -> dict[str, Any]:
        """Remove manual check-ins for users."""
        return await self._request(
            "DELETE",
            f"/events/{event_id}/check_in",
            json={"user_ids": user_ids},
        )

    async def get_attendance_analytics(self, course_id: int) -> dict[str, Any]:
        """Get combined attendance analytics (all events + all check-ins)."""
        return await self._get(f"/courses/{course_id}/analytics/sessions")
