# Local search index for instant thread search

## Problem

The Ed API's search is basic keyword matching with no relevance ranking, fuzzy matching, or stemming. Queries like "summarise this week's Project 1 posts with staff answers" require dozens of API calls and still miss threads with different wording ("assessment 1", "first assignment"). There is no way to filter for threads with staff replies without reading every thread.

## Solution

Add a local SQLite FTS5 in-memory search index. Two new MCP tools (`sync_index`, `search_index`) provide bulk-download-and-index plus instant local search. All existing tools remain unchanged. No new dependencies — sqlite3 is stdlib.

## Bulk endpoint

`POST /courses/{course_id}/analytics/discussion_threads.json` returns all threads for a course as a bare JSON list (~950KB for 276 threads). Each thread includes:

```json
{
  "url": "https://edstem.org/au/courses/31798/discussion/3124785",
  "type": "question",
  "number": 1,
  "title": "Welcome!",
  "category": "General",
  "subcategory": "",
  "votes": 55,
  "views": 1246,
  "unique_views": 377,
  "private": false,
  "anonymous": false,
  "endorsed": false,
  "created_at": "2026-02-16T09:01:25.316633+11:00",
  "user": {"name": "Liana Pozza", "email": "...", "role": "admin"},
  "text": "Hi everyone, ...",
  "document": "<document version=\"2.0\">...</document>",
  "answers": [
    {
      "url": "...", "text": "...", "document": "...",
      "user": {"name": "...", "email": "...", "role": "admin"},
      "endorsed": true, "anonymous": false, "staff_only": false,
      "votes": 0, "created_at": "...",
      "comments": [...]
    }
  ],
  "comments": [
    {
      "url": "...", "text": "...", "document": "...",
      "user": {"name": "...", "email": "...", "role": "student"},
      "endorsed": false, "anonymous": false, "staff_only": false,
      "votes": 0, "created_at": "...",
      "comments": [...]
    }
  ]
}
```

Key structural notes:
- The response is a **bare list** `[{...}, {...}]`, not `{"threads": [...]}`.
- `answers` key is **conditionally absent** on non-question threads (not an empty list — the key is missing entirely). Always use `thread.get('answers') or []`.
- Both `answers` and `comments` have identical structure and can nest arbitrarily deep (up to 17 levels observed). A recursive walker is required.
- `answers` contain the majority of reply content: 294 answers vs 37 top-level comments in a 276-thread course. 246 of those answers are from staff.
- `text` is already plain text. `document` is Ed XML. Use `text` for indexing — no XML stripping needed on the bulk path.
- `user.email` is included but must not be indexed when PII stripping is enabled.
- Field names differ from the `get_thread` API (e.g., `private` not `is_private`, `text` not `content`).

## Architecture

### Files

| File | Purpose |
|---|---|
| `_index.py` | FTS5 wrapper: build, search, update, info, clear. Field mapping and normalisation. |
| `server.py` | Two new tools: `sync_index`, `search_index`. Write-through on existing write tools. |
| `client.py` | One new method: `get_discussion_threads_json(course_id)` returning `list[dict]`. This method must bypass `_request`/`_get` (which return `dict[str, Any]`) and call `self._client.get()` directly, since the bulk endpoint returns a bare JSON list. Uses a per-request timeout of 120s: `self._client.get(url, timeout=httpx.Timeout(120.0, connect=10.0))`. |

No new dependencies. sqlite3 is stdlib. FTS5 has been in SQLite since 3.9.0 (2015) and is compiled into every Python 3.9+ installation.

### In-memory index

The FTS5 table lives in a `:memory:` SQLite database. It is rebuilt from cached JSON on the first `search_index` call per course per session (~2ms for 276 threads, ~4ms for 750). No file locking, no atomic swaps, no corruption recovery.

### JSON cache on disk

```
~/.cache/edstem-mcp/
  31798.json.gz       <- gzipped bulk JSON (~178KB for 276 threads)
  31798.meta.json     <- {"last_synced": "2026-03-25T10:00:00", "thread_count": 276}
```

Override location with `ED_INDEX_PATH` environment variable. The index path must not be on a network/synced filesystem.

On `sync_index`: download bulk JSON, gzip to disk, rebuild in-memory FTS5.
On `search_index` with no in-memory table: read gzipped JSON from disk, rebuild FTS5 (~2ms). If no cache file exists, auto-trigger sync.
On corrupted cache file: delete it and re-download.

### FTS5 table

```python
conn.execute('''
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
''')
```

Four indexed (searchable) columns: `title`, `body`, `replies`, `staff_replies`. All others are UNINDEXED (stored and retrievable but not searchable via MATCH). Filtering on UNINDEXED columns uses SQL WHERE clauses, not FTS5 query syntax.

BM25 column weights (module-level constant):

```python
# Weights for all 21 columns. 0.0 for UNINDEXED, meaningful weights for indexed.
# Order matches CREATE TABLE column order.
_BM25_WEIGHTS = (
    0, 0, 0, 0,        # thread_id, number, course_id, url
    5.0, 1.0, 0.5, 2.0, # title, body, replies, staff_replies
    0, 0, 0, 0, 0,     # category, subcategory, type, user_name, user_role
    0, 0, 0, 0, 0, 0, 0, 0  # has_staff_reply ... created_at
)
```

### Field mapping

| Schema column | Bulk JSON source | get_thread API source (write-through) |
|---|---|---|
| `thread_id` | extract last path segment from `url` | `id` (cast to str) |
| `number` | `number` (cast to str) | `number` (cast to str) |
| `course_id` | passed as parameter to sync | `course_id` (cast to str) |
| `url` | `url` | construct via `_thread_url(course_id, id)` |
| `title` | `title` | `title` |
| `body` | `text` (already plain text) | strip XML from `content` |
| `replies` | recursive concatenation of `answers[].text` + `comments[].text` | recursive concatenation of `answers[].content` + `comments[].content` (strip XML) |
| `staff_replies` | recursive concat where `user.role != "student"` | recursive concat where `user.course_role != "student"` (strip XML) |
| `category` | `category` | `category` |
| `subcategory` | `subcategory` | `subcategory` |
| `type` | `type` | `type` |
| `user_name` | `user.name` | `user.name` |
| `user_role` | `user.role` | `user.course_role` |
| `has_staff_reply` | "true"/"false": any answer/comment has `user.role != "student"` | "true"/"false": any has `user.course_role != "student"` |
| `is_answered` | "true"/"false": any endorsed answer, or type=question with staff reply | `is_answered` (convert to "true"/"false") |
| `endorsed` | `endorsed` (convert to "true"/"false") | `is_endorsed` (convert to "true"/"false") |
| `comment_count` | recursive count of all answers + comments | `reply_count` (cast to str) |
| `votes` | `votes` (cast to str) | `vote_count` (cast to str) |
| `views` | `views` (cast to str) | `view_count` (cast to str) |
| `unique_views` | `unique_views` (cast to str) | "0" (not available) |
| `created_at` | `created_at` | `created_at` |

All FTS5 values are strings. Booleans stored as "true"/"false". Numbers cast to str. "Staff" is defined as `user.role != "student"` (covers admin, staff, tutor).

PII handling: if `ED_STRIP_PII` is enabled (default), apply `_scrub_emails()` to `body` and `replies` before inserting. Do not index `user.email`.

XML stripping function (needed for write-through path only):

```python
import re
_XML_TAG_RE = re.compile(r'<[^>]+>')
def _strip_xml(xml: str) -> str:
    return _XML_TAG_RE.sub(' ', xml).strip()
```

### Recursive comment/answer walker

```python
def _collect_replies(
    items: list[dict],
    staff_only: bool = False,
    text_field: str = "text",
    role_field: str = "role",
) -> list[str]:
    """Recursively collect text from answers/comments.

    Parameterised for both data shapes:
    - Bulk JSON: text_field="text", role_field="role"
    - get_thread API: text_field="content", role_field="course_role"
      (content values need XML stripping via _strip_xml before use)
    """
    texts = []
    for item in items:
        is_staff = item.get("user", {}).get(role_field, "student") != "student"
        if not staff_only or is_staff:
            text = item.get(text_field, "")
            if text:
                texts.append(text)
        # Recurse into nested comments
        nested = item.get("comments") or []
        texts.extend(_collect_replies(nested, staff_only=staff_only,
                                      text_field=text_field, role_field=role_field))
    return texts
```

Called for each thread:

```python
all_answers = thread.get("answers") or []
all_comments = thread.get("comments") or []
all_items = all_answers + all_comments

replies = "\n".join(_collect_replies(all_items))
staff_replies = "\n".join(_collect_replies(all_items, staff_only=True))
has_staff_reply = bool(staff_replies)
is_answered = any(a.get("endorsed") for a in all_answers) or (
    thread.get("type") == "question" and has_staff_reply
)
comment_count = _count_recursive(all_items)  # simple recursive len()
```

## New tools

### `sync_index(course_id: int) -> str`

Sync the local search index for a course. Downloads all threads and builds an in-memory search index. Takes ~2-3 seconds (network-bound). Call this before `search_index` or to refresh stale data.

Flow:
1. Download bulk JSON via `get_discussion_threads_json(course_id)` with 120s timeout.
2. Gzip and write to `~/.cache/edstem-mcp/{course_id}.json.gz`.
3. Write `{course_id}.meta.json` with `last_synced` and `thread_count`.
4. Build in-memory FTS5 table (~2ms).
5. Populate rowid map for write-through.

Returns: `{"course_id": 31798, "threads_indexed": 276, "elapsed_seconds": 2.1, "last_synced": "2026-03-25T10:00:00"}`

Errors:
- 403 from bulk endpoint: "Index sync failed. This endpoint may require staff or admin access."
- Network error: "Failed to download thread data. Check your connection and ED_API_TOKEN."

### `search_index(course_id: int, query: str, limit: int = 20) -> str`

Search the local index for a course. Returns BM25-ranked results. If no in-memory index exists, rebuilds from cached JSON (~2ms). If no cache exists, auto-triggers sync first (~2-3s on first call).

Query features (FTS5 syntax):
- Natural language: `project 1 deadline` (implicit AND across title, body, replies, staff_replies)
- Phrase: `"peer review"`
- Prefix: `assign*` (matches assignment, assignments, assigned)
- Column-specific: `title:exam`, `body:ggplot`, `staff_replies:extension`
- Boolean: `assignment AND error`, `project OR assessment`, `exam NOT practice`
- Proximity: `NEAR(project deadline, 5)`
- Stemmed: `assignment` matches `assignments` (Porter stemmer)

Note: column-specific search only works on indexed columns (title, body, replies, staff_replies). Filtering by category, type, user etc. is done via parameters, not query syntax. Queries on UNINDEXED columns (e.g., `category:Assignments`) silently return zero results.

Error handling: FTS5 raises `sqlite3.OperationalError` on malformed queries (unclosed quotes, bare `AND`, invalid syntax). The search function must catch this and fall back to quoting the entire query as a literal phrase (`'"' + query + '"'`). This handles cases like "tell me about AND gates" which would otherwise crash.

Additional parameters (translated to SQL WHERE clauses):
- `category: str | None` — filter by category name
- `type: str | None` — filter by thread type ("question", "post", "announcement")
- `has_staff_reply: bool | None` — filter for threads with staff responses
- `is_answered: bool | None` — filter for answered threads

Returns:
- Top 5 results: full content (number, title, body, replies, snippet, url, category, subcategory, type, user_name, user_role, has_staff_reply, is_answered, endorsed, comment_count, votes, views, created_at, score)
- Remaining results: summary (number, title, snippet, url, category, type, user_name, has_staff_reply, is_answered, comment_count, created_at, score)
- `snippet` is a ~30-token FTS5 excerpt around matching terms

Response includes `last_synced` timestamp. Always returns results even if stale — never refuses to search.

Errors:
- No cache and network error: "No index available for this course. Call sync_index first."
- Corrupted cache: delete and re-download automatically.

## Write-through updates

After these existing tools succeed, update the single affected thread in the in-memory FTS5 table (if one exists for that course):

- `reply_to_thread` — re-fetch thread via `get_thread(thread_id)`, normalise using get_thread column in field mapping, delete old row by rowid, insert new row.
- `edit_thread` — same.
- `create_thread` — fetch new thread, normalise, insert. Add to rowid map.
- `delete_thread` — look up `course_id` from `_course_map[thread_id]`, then delete from FTS5 by rowid. Remove from rowid map and course map. No API call needed.

Rowid management: maintain a `dict[str, int]` mapping `thread_id → rowid` per course, populated at build time. Delete uses `DELETE FROM threads WHERE rowid = ?`. Insert returns the new rowid via `cursor.lastrowid`.

Write-through uses the **raw** `get_thread` API response (before trimming), since the normaliser needs fields like `view_count` and `user.course_role` that the trimming code strips. Extract role data before PII stripping.

If write-through fails for any reason (e.g., concurrent access), silently skip. The next sync catches it.

`edit_comment` and `delete_comment` are intentionally excluded from write-through. They change indexed content (replies/staff_replies), but the complexity of re-fetching the parent thread and re-indexing is not justified. The next `sync_index` call catches these changes.

Also update the cached JSON on disk: read, decompress, update the thread entry, recompress, write. This keeps the cache consistent for the next rebuild. If this fails, skip — the cache becomes slightly stale.

## `_index.py` module API

```python
# Module-level state
_dbs: dict[int, sqlite3.Connection] = {}       # course_id -> in-memory db
_rowid_maps: dict[int, dict[str, int]] = {}    # course_id -> {thread_id -> rowid}
_course_map: dict[str, int] = {}               # thread_id -> course_id (reverse lookup for delete_thread)

def is_loaded(course_id: int) -> bool
def build(course_id: int, threads: list[dict]) -> int       # returns thread count
def search(course_id: int, query: str, limit: int = 20,
           category: str | None = None, type: str | None = None,
           has_staff_reply: bool | None = None,
           is_answered: bool | None = None) -> dict          # returns results + metadata
def update_thread(course_id: int, thread_id: str, raw_api_response: dict) -> None
def delete_thread(course_id: int, thread_id: str) -> None
def info(course_id: int) -> dict | None          # {"thread_count": N, "last_synced": "..."}
def clear(course_id: int) -> None                # drop in-memory db for course

# Internal
def _normalise_bulk(thread: dict, course_id: int) -> tuple    # bulk JSON -> row tuple
def _normalise_api(raw: dict) -> tuple                         # get_thread response -> row tuple (extracts course_id from raw["thread"]["course_id"])
def _collect_replies(items: list[dict], staff_only: bool = False) -> list[str]
def _count_recursive(items: list[dict]) -> int
def _strip_xml(xml: str) -> str
```

## What does NOT change

All existing tools remain identical. `search_threads` still hits the Ed API. `list_threads` still hits the Ed API. `get_thread` still hits the Ed API. The index is purely additive — two new tools that provide a faster search path.

After implementation, update the `search_threads` docstring to mention `search_index` as an alternative: "For faster ranked search with stemming and filtering, use search_index (requires sync_index first)."

## Implementation notes

- BM25 scores from FTS5 are negative (more negative = better match). Negate them before returning so higher = better.
- All FTS5 column values are strings (including booleans as "true"/"false" and numbers as their string representation).
- The `_course_map` reverse lookup is populated during `build()` alongside `_rowid_maps`.
- Write-through normalisation must extract `user.course_role` from the raw API response **before** PII stripping, since `_strip_user_pii` removes the role field.
- Cache update on write-through requires iterating the decompressed JSON list to find the thread by `thread_id` (extracted from URL). This is O(n) but n is ~300-750, taking <1ms.

## Performance

Benchmarked on the actual ENVX1002 dataset (276 threads, 1.3MB):

| Operation | 276 threads | 750 threads (projected) |
|---|---|---|
| Bulk download | ~2s (network) | ~3s (network) |
| Build FTS5 from JSON | 2ms | 4ms |
| Search query | 30-150μs | 60-200μs |
| Write-through (single row) | <1ms | <1ms |
| Rebuild from cached JSON | 2ms | 4ms |
| Gzipped cache on disk | 178KB | ~480KB |

## Validation

1. Sync ENVX1002 (276 threads). Verify thread count matches.
2. Search `"project 1"` — verify phrase search works and returns relevant threads.
3. Search `assignment` — verify Porter stemming matches "assignments".
4. Search `ggplot2` — verify technical terms are not mangled by stemmer.
5. Search `assign*` — verify prefix search works.
6. Search `assignment AND error` — verify boolean AND.
7. Search `staff_replies:data` — verify column-specific search on staff replies.
8. Search with `has_staff_reply=True` — verify SQL WHERE filtering on UNINDEXED column.
9. Search with `category="Assignments"` — verify category filtering.
10. Reply to a thread — verify write-through updates the in-memory index.
11. Delete a thread — verify it's removed from the index.
12. Restart MCP server — verify index rebuilds from cached JSON on first search (~2ms).
13. Test with no cache — verify auto-sync on first search.
14. Test with `ED_STRIP_PII=true` — verify emails in content are scrubbed.
15. Verify `answers` array is processed — check a question thread with staff answers appears in `has_staff_reply` filter.
