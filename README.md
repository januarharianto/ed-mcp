# ed-mcp

![Lines of code](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/januarharianto/ed-mcp/main/.github/badges/loc.json)

An MCP server and CLI for [Ed Discussion](https://edstem.org). It gives LLMs (and you) access to threads, comments, attendance, moderation, file uploads, and instant local search through 43 tools. Works with Claude Desktop, Claude Code, or any MCP-compatible client.

## What can I do with this?

Once set up, you can ask Claude things like:

> "What questions haven't been answered yet in ENVX2001?"

Claude will look up your course, find unanswered questions, and show you a summary.

Here are some more examples:

| You say | What happens |
|---|---|
| "Give me a quick overview of my stats course" | Fetches enrollment, unanswered questions, unresolved threads, and top categories (`get_course_stats`) |
| "Show me thread #42 in ENVX2001" | Looks up the thread by its number and shows the full content and replies (`get_course_thread`) |
| "Search for posts about peer review in my course" | Searches threads by keyword and returns a summary list (`search_threads`) |
| "Reply to that thread saying the deadline has been extended" | Posts a comment on the thread (`reply_to_thread`) |
| "Pin the announcement about the exam" | Pins the thread to the top of the course feed (`pin_thread`) |
| "Mark that question as answered -- the first reply is correct" | Accepts the reply as the answer (`accept_answer`) |
| "Move all the project 2 threads into the Assignments category" | Recategorises multiple threads at once (`bulk_recategorise`) |
| "What has student Jane Smith been posting about?" | Looks up the student and shows their recent activity (`get_user_activity`) |
| "This question is a duplicate of #35, mark it" | Marks the thread as a duplicate and links to the original (`mark_duplicate`) |
| "Show me today's attendance session" | Lists attendance sessions and their check-ins (`list_attendance_sessions`) |
| "Mark students 12345 and 67890 as present for the Week 3 tutorial" | Manually checks in students by user ID (`manual_check_in`) |
| "Give me an attendance report for the whole course" | Returns all sessions and check-ins in one call (`get_attendance_analytics`) |
| "Summarise this week's posts with staff answers into a FAQ" | Syncs local index, searches with staff reply filter, returns full content (`sync_index` + `search_index`) |
| "Find answered threads similar to this question about R errors" | Searches local index with BM25 ranking, stemming, and fuzzy matching (`search_index`) |

## Setup

### 1. Prerequisites

You will need two things installed on your computer:

- **Python 3.11 or newer** -- check with `python3 --version` in your terminal. If you don't have it, download it from [python.org](https://www.python.org/downloads/).
- **uv** (a Python package manager) -- install it by running this in your terminal:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
  On Windows, use: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

### 2. Get your Ed API token

Go to your [Ed settings page](https://edstem.org/settings/api-tokens), create a new token, and copy it.

### 3. Download and install

Go to the [GitHub repository](https://github.com/januarharianto/ed-mcp) and click the green **Code** button, then **Download ZIP**. Extract it somewhere on your computer (e.g. your Desktop or Documents folder).

If you have [git](https://git-scm.com/downloads) installed, you can clone it instead:

```bash
git clone https://github.com/januarharianto/ed-mcp.git
```

Then open a terminal, navigate to the folder, and install dependencies:

```bash
cd /path/to/ed-mcp
uv sync
```

Replace `/path/to/ed-mcp` with the actual folder path.

### 4. Connect to Claude

#### Claude Desktop

Open your Claude Desktop config file:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

Add this inside the `"mcpServers"` section (create the file if it doesn't exist).

> **Important:** Claude Desktop does not inherit your shell's `PATH`, so you must use the full path to `uv`. Find it by running `which uv` (macOS/Linux) or `where uv` (Windows) in your terminal.

```json
{
  "mcpServers": {
    "edstem": {
      "command": "/full/path/to/uv",
      "args": ["run", "--directory", "/path/to/ed-mcp", "python", "-m", "edstem_mcp.server"],
      "env": {
        "ED_API_TOKEN": "your_token_here"
      }
    }
  }
}
```

Replace `/full/path/to/uv` with the output from `which uv` (macOS/Linux) or `where uv` (Windows), and `/path/to/ed-mcp` with the actual folder path.

Restart Claude Desktop after saving the file.

#### Claude Code

The repository includes an `.mcp.json` file, so Claude Code discovers the MCP server automatically when you open the project folder.

Create a `.env` file in the project root with your token:

```bash
ED_API_TOKEN=your_token_here
```

The repository also ships with four Claude Code skills in `skills/` that are auto-discovered. They provide structured workflows for common tasks like thread management, attendance, moderation, and course admin.

### 5. Test it

Ask Claude: "What courses am I enrolled in on Ed?" If everything is set up correctly, you'll see a list of your courses.

---

## CLI

The `ed` command gives you the same functionality from your terminal. Output is JSON, so it pipes well into `jq` or other tools.

```bash
uv run ed usage          # print the full command reference
```

| Command group | What it does |
|---|---|
| `ed courses` | List courses, get stats, list users, list categories |
| `ed threads` | List, search, read, create, edit, reply, delete, moderate, recategorise |
| `ed attendance` | List sessions, check in students, undo check-ins, get analytics |
| `ed comments` | Edit or delete comments |
| `ed users` | View a user's activity |
| `ed files` | Upload files |
| `ed config` | Save a default course ID |

Most commands that need a course ID accept `--course`. To avoid typing it every time:

```bash
uv run ed config set-course 12345
```

After that, commands like `ed threads list` and `ed attendance analytics` will use course 12345 by default.

---

## Tool reference

### Courses and users

- **`get_user`** -- Your profile info.
- **`list_courses`** -- All enrolled courses.
- **`get_course_stats`** -- Quick overview: enrollment, unanswered/unresolved counts, top categories.
- **`get_enrollment_counts`** -- Headcount by role (students, staff, admins).
- **`list_users`** -- Students and staff in a course (with role filtering and pagination).
- **`get_user_activity`** -- A user's thread and comment history.

### Threads

- **`list_threads`** -- Browse threads with sorting and filtering.
- **`search_threads`** -- Search by keyword.
- **`get_thread`** -- Full thread content by ID.
- **`get_course_thread`** -- Full thread content by number (e.g. #42).
- **`get_thread_by_url`** -- Full thread content from an Ed URL.
- **`list_categories`** -- All categories and subcategories.
- **`create_thread`** -- Create a post, question, or announcement.
- **`edit_thread`** -- Update title, content, or category.
- **`delete_thread`** -- Delete a thread.
- **`bulk_recategorise`** -- Move multiple threads to a new category.

### Comments

- **`reply_to_thread`** -- Post a comment or answer (supports nested replies).
- **`edit_comment`** -- Edit a comment.
- **`delete_comment`** -- Delete a comment.

### Moderation

- **`lock_thread`** / **`unlock_thread`** -- Control whether new comments are allowed.
- **`pin_thread`** / **`unpin_thread`** -- Pin or unpin from the course feed.
- **`endorse_thread`** / **`unendorse_thread`** -- Instructor endorsement badge.
- **`accept_answer`** -- Mark the accepted answer on a question.
- **`mark_duplicate`** / **`unmark_duplicate`** -- Flag duplicate threads.

### Attendance

- **`list_attendance_sessions`** -- List all sessions in a course.
- **`get_attendance_session`** -- Full session detail with check-ins.
- **`create_attendance_session`** -- Create a new session.
- **`update_attendance_session`** -- Rename, close/reopen, or hide/show a session.
- **`delete_attendance_session`** -- Delete a session and its check-ins.
- **`list_check_ins`** -- List check-ins by course or session.
- **`manual_check_in`** -- Mark students as present, late, excused, or absent.
- **`undo_check_in`** -- Remove check-in records.
- **`get_attendance_analytics`** -- Combined attendance report for a course.

### Files

- **`upload_file`** -- Upload a file to Ed and get its URL.
- **`upload_file_url`** -- Upload a file from a URL directly to Ed (no local download needed).
- **`download_file`** -- Download a file from an Ed CDN URL to a local path.
- **`download_thread_files`** -- Batch download all images and attachments from a thread.

### Local search

These tools provide instant, offline, full-text search across an entire course's threads. The search index is built from a bulk download of all threads and uses SQLite FTS5 with BM25 ranking and Porter stemming. No new dependencies -- `sqlite3` is part of Python's standard library.

- **`sync_index`** -- Download all threads for a course and build a local search index. Takes ~0.4 seconds. Call this once, then use `search_index` for instant results.
- **`search_index`** -- Search the local index with BM25 ranking. Supports phrases (`"peer review"`), prefix matching (`assign*`), boolean operators (`AND`/`OR`/`NOT`), and column-specific queries (`title:exam`, `staff_replies:deadline`). Filter by category, type, staff replies, or answered status. Returns full thread content for top results. Auto-syncs on first call and refreshes when stale (>30 minutes).

After you reply to, edit, create, or delete a thread, the local index is automatically updated (write-through).

## Response efficiency

All tool responses are trimmed for LLM context efficiency:

- **List/search tools** return compact summaries (key subset, no content body).
- **Detail tools** return full content but strip metadata bloat.
- **Write tools** return minimal confirmations (id, number, title).
- **Booleans** like `is_pinned` and `is_locked` are only included when `true`.
- **Timestamps** are date-only in summaries, full precision in detail views.
- **Null/empty values** are omitted from all responses.

Use `get_thread` or `get_course_thread` when you need full thread content.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `ED_API_TOKEN` | Yes | Your Ed Discussion API token |
| `ED_BASE_URL` | No | API base URL (defaults to `https://edstem.org/api`) |
| `ED_STRIP_PII` | No | Strip emails, user IDs, avatars from responses (defaults to `true`; set to `false` to include all fields) |
| `ED_REGION` | No | Region prefix for Ed URLs in responses -- e.g. `au`, `us` (defaults to `us`) |
| `ED_INDEX_PATH` | No | Directory for the local search index cache (defaults to `~/.cache/edstem-mcp`) |

## License

MIT
