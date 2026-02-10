# An EdStem MCP server

Talk to [Ed Discussion](https://edstem.org) boards using plain English. This tool connects LLMs that can use MCPs (e.g. Claude) to Ed so you can browse threads, reply to students, check what needs attention and more.

## What can I do with this?

Once set up, you can ask Claude things like:

> "What questions haven't been answered yet in ENVX2001?"

Claude will look up your course, find unanswered questions, and show you a summary. Behind the scenes it uses tools like `list_courses` and `list_threads` from the MCP.

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

Hopefully a clear description will give you the response that you want.

## Setup

### 1. Install prerequisites

You will need two things installed on your computer:

- **Python 3.11 or newer** -- check with `python3 --version` in your terminal. If you don't have it, download it from [python.org](https://www.python.org/downloads/).
- **uv** (a Python package manager) -- install it by running this in your terminal:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
  On Windows, use: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

### 2. Get your Ed API token

Go to your [Ed settings page](https://edstem.org/settings/api-tokens), create a new token, and copy it.

### 3. Integrate with Claude Desktop

The following instructions are specific to Claude Desktop. If you're using a different MCP-compatible client, refer to its documentation.

Open your Claude Desktop config file:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

Add this inside the `"mcpServers"` section (create the file if it doesn't exist).

> **Important:** Claude Desktop does not inherit your shell's `PATH`, so you must use the full path to `uv`. Find it by running `which uv` (macOS/Linux) or `where uv` (Windows) in your terminal.

#### Option A: Direct from GitHub (no clone needed)

```json
{
  "mcpServers": {
    "edstem": {
      "command": "/full/path/to/uv",
      "args": [
        "run", "--with", "git+https://github.com/januarharianto/ed-mcp.git",
        "python", "-m", "edstem_mcp.server"
      ],
      "env": {
        "ED_API_TOKEN": "your_token_here"
      }
    }
  }
}
```

#### Option B: Clone first (useful if you want to modify the code)

```bash
git clone https://github.com/januarharianto/ed-mcp.git
cd ed-mcp
uv sync
```

Then add to your config:

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

Replace `/path/to/ed-mcp` with the actual folder path.

Restart Claude Desktop after saving the file.

### 5. Test it

Ask Claude: "What courses am I enrolled in on Ed?" If everything is set up correctly, you'll see a list of your courses.

---

## Tool reference

For developers and anyone curious about what's available under the hood:

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

### Files

- **`upload_file`** -- Upload a file to Ed and get its URL.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `ED_API_TOKEN` | Yes | Your Ed Discussion API token |
| `ED_BASE_URL` | No | API base URL (defaults to `https://edstem.org/api`) |
| `ED_STRIP_PII` | No | Strip emails, user IDs, avatars from responses (defaults to `true`; set to `false` to include all fields) |
| `ED_REGION` | No | Region prefix for Ed URLs in responses — e.g. `au`, `us` (defaults to `us`) |

## License

MIT
