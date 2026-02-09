# edstem-mcp

An [MCP](https://modelcontextprotocol.io/) server that connects LLM agents to [Ed Discussion](https://edstem.org). It wraps the Ed API so that Claude (and other MCP-compatible tools) can browse courses, read and reply to threads, manage moderation, and more -- all through natural language.

## Setup

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/januarharianto/ed-mcp.git
cd ed-mcp
uv sync
```

Then grab an API token from your [Ed settings page](https://edstem.org/us/settings/api-tokens).

## Configure with Claude Code

Add the server to your Claude Code MCP config (`.mcp.json` in your project root or `~/.claude/settings.json` for global access):

```json
{
  "mcpServers": {
    "edstem": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/ed-mcp", "python", "-m", "edstem_mcp.server"],
      "env": {
        "ED_API_TOKEN": "your_token_here"
      }
    }
  }
}
```

Replace `/path/to/ed-mcp` with the actual path to where you cloned the repo.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `ED_API_TOKEN` | Yes | Your Ed Discussion API bearer token |
| `ED_BASE_URL` | No | API base URL (defaults to `https://edstem.org/api`) |

## Available tools

### Courses and users

- **`get_user`** -- Get the authenticated user's profile.
- **`list_courses`** -- List all enrolled courses.
- **`get_course_stats`** -- Quick course overview: enrollment count, unanswered/unresolved threads, top categories.
- **`list_users`** -- List users enrolled in a course.
- **`get_user_activity`** -- Get a user's thread and comment activity in a course.

### Threads

- **`list_threads`** -- List threads in a course, with sorting and filtering.
- **`search_threads`** -- Search threads by keyword.
- **`get_thread`** -- Get a thread by its global ID, including all comments and answers.
- **`get_course_thread`** -- Get a thread by its course-relative number (e.g. #42).
- **`get_thread_by_url`** -- Get a thread by pasting its Ed Discussion URL.
- **`list_categories`** -- List all thread categories and subcategories in a course.
- **`create_thread`** -- Create a new thread (post, question, or announcement).
- **`edit_thread`** -- Edit an existing thread's title, content, or category.
- **`delete_thread`** -- Delete a thread.
- **`bulk_recategorise`** -- Move multiple threads to a new category at once.

### Comments

- **`reply_to_thread`** -- Post a comment or answer on a thread (supports nested replies).
- **`edit_comment`** -- Edit an existing comment.
- **`delete_comment`** -- Delete a comment.

### Moderation

- **`lock_thread`** / **`unlock_thread`** -- Prevent or allow new comments.
- **`pin_thread`** / **`unpin_thread`** -- Pin or unpin a thread in the course feed.
- **`endorse_thread`** / **`unendorse_thread`** -- Add or remove the instructor endorsement badge.
- **`accept_answer`** -- Mark a comment as the accepted answer on a question thread.
- **`mark_duplicate`** / **`unmark_duplicate`** -- Mark or unmark a thread as a duplicate.

### Files

- **`upload_file`** -- Upload a local file to Ed and get back its URL.

## Design notes

List and search tools return compact summaries (title, category, reply count, etc.) to keep LLM context usage low. Full thread content, comments, and answers are only returned when you fetch a specific thread. Write operations return minimal confirmations.

Thread content in Ed uses an XML format:

```xml
<document version="2.0"><paragraph>Hello world</paragraph></document>
```

Both `create_thread` and `reply_to_thread` expect content in this format.

## License

MIT
