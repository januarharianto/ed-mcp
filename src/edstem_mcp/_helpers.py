"""Shared helpers for response trimming and formatting.

These are used by both the MCP server and the CLI, so they must not
import anything from FastMCP.
"""

from __future__ import annotations

import json
import os
import re

# ------------------------------------------------------------------
# Serialisation
# ------------------------------------------------------------------


def _json(data: dict) -> str:
    """Compact JSON serialisation for tool responses."""
    return json.dumps(data, separators=(",", ":"), default=str)


def _compact(d: dict) -> dict:
    """Drop keys with None or empty-string values. Keep 0 and False."""
    return {k: v for k, v in d.items() if v is not None and v != ""}


def _thread_url(course_id: int, thread_id: int) -> str:
    """Build an Ed Discussion URL for a thread."""
    region = os.environ.get("ED_REGION", "us")
    return f"https://edstem.org/{region}/courses/{course_id}/discussion/{thread_id}"


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


# ------------------------------------------------------------------
# Key-set constants
# ------------------------------------------------------------------

# Keys kept in thread summaries (list/search). Full content via get_thread.
_THREAD_SUMMARY_KEYS = {
    "id", "number", "type", "title", "category", "subcategory",
    "created_at", "reply_count", "vote_count",
    "view_count", "unresolved_count",
}

# Boolean keys only included when True (false is the default).
_THREAD_SUMMARY_BOOL_KEYS = {
    "is_pinned", "is_private", "is_endorsed", "is_answered", "is_locked",
}

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

_UPLOAD_KEYS = {"url", "filename"}

# Keys kept in attendance session summaries (list_attendance_sessions).
_EVENT_SUMMARY_KEYS = {
    "id", "title", "is_closed", "is_hidden", "start", "created_at",
}

# Keys kept in full attendance session detail.
_EVENT_DETAIL_KEYS = {
    "id", "course_id", "title", "content", "is_closed", "is_hidden",
    "start", "created_at",
}

# Keys kept in check-in records.
_CHECK_IN_KEYS = {
    "event_id", "user_id", "checked_in_by", "kind", "method", "created_at",
}

# Keys kept in user activity responses.
_ACTIVITY_THREAD_KEYS = {
    "id", "type", "course_id", "title", "category", "subcategory",
    "created_at",
}
_ACTIVITY_COMMENT_KEYS = {
    "id", "type", "thread_id", "thread_title", "thread_category",
    "created_at",
}


# ------------------------------------------------------------------
# Trimming functions
# ------------------------------------------------------------------


def _summarise_threads(threads: list[dict], course_id: int) -> list[dict]:
    """Extract compact summaries from a list of thread dicts."""
    result = []
    for t in threads:
        summary = _compact({k: t[k] for k in _THREAD_SUMMARY_KEYS if k in t})
        # Truncate timestamp to date-only in summaries
        if "created_at" in summary:
            summary["created_at"] = str(summary["created_at"])[:10]
        for k in _THREAD_SUMMARY_BOOL_KEYS:
            if t.get(k):
                summary[k] = True
        summary["user"] = t.get("user", {}).get("name", "")
        result.append(summary)
    return result


def _trim_comment(c: dict) -> dict:
    """Strip a comment/answer to essential fields, recursing into replies."""
    trimmed = _compact({k: c[k] for k in _COMMENT_KEYS if k in c})
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
    trimmed = _compact({k: t[k] for k in _THREAD_DETAIL_KEYS if k in t})
    if "id" in trimmed and "course_id" in trimmed:
        trimmed["url"] = _thread_url(trimmed["course_id"], trimmed["id"])
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
    return trimmed
