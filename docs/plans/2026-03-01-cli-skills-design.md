# CLI + Skills Plugin Design

**Goal:** Add a Typer CLI (`ed`) alongside the existing MCP server, plus 4 Claude Code skills — packaged as a Claude Code plugin. Users pick MCP mode (auto-discovered tools) or CLI mode (skills teach Claude the `ed` commands via Bash).

**Audience:** Any Ed Discussion instructor using Claude Code or Claude Desktop.

## Architecture

The `EdClient` is the shared core. Two interfaces wrap it:

- **MCP server** (`server.py`, existing) — 38 auto-discovered tools, best for Claude Desktop or users who prefer MCP
- **CLI** (`cli.py`, new) — 25 grouped commands, JSON output by default, best for Claude Code with skills (fewer tokens per turn)

A Claude Code plugin bundles both. The user enables one mode:

- **MCP mode:** `.mcp.json` auto-starts the server. No skills needed — Claude auto-discovers MCP tools.
- **CLI mode:** User installs `ed` CLI via pip/uv. Skills teach Claude which Bash commands to run. ~200-400 tokens per skill vs ~3-4k tokens for 38 MCP tool schemas.

## Plugin Structure

```
edstem/                          # repo root IS the plugin root
├── .claude-plugin/
│   └── plugin.json              # plugin manifest
├── .mcp.json                    # MCP server config (for MCP mode)
├── skills/                      # skills teach Claude the CLI (for CLI mode)
│   ├── ed-threads/
│   │   └── SKILL.md
│   ├── ed-attendance/
│   │   └── SKILL.md
│   ├── ed-moderation/
│   │   └── SKILL.md
│   └── ed-admin/
│       └── SKILL.md
├── src/edstem_mcp/
│   ├── client.py                # shared EdClient (existing)
│   ├── server.py                # MCP server (existing)
│   └── cli.py                   # NEW — Typer CLI
├── pyproject.toml               # adds `ed` console script + typer dep
└── ...
```

## CLI Design

### Principles

- **JSON output by default** (LLM-first), `--pretty` flag for human use
- **Default course context** via `ed config set-course <id>` — no need to pass `course_id` every time
- **Smart get** — `ed threads get` auto-detects thread ID, `#number`, or URL
- **Consolidated moderation** — one `ed threads mod` command with flags instead of 8 separate commands
- **Compact `ed usage`** — outputs full command reference in ~500 tokens for LLM self-reference

### Commands (25 total)

```
ed usage                           # LLM-friendly compact reference
ed config set-course <id>          # save default course

# Courses
ed courses list                    # list enrolled courses
ed courses stats                   # overview of default course
ed courses users [--role ...]
ed courses categories

# Threads
ed threads list [--filter ...]     # list/filter threads
ed threads get <id|#N|url>         # smart get (auto-detects format)
ed threads search <query>
ed threads create --title --content [--type ...]
ed threads edit <id> [--title] [--content] [--category]
ed threads reply <id> --content [--type comment|answer]
ed threads delete <id>
ed threads mod <id> [--lock|--unlock|--pin|--unpin|--endorse|...]
ed threads recategorise <ids> --category "..."

# Attendance
ed attendance list                 # sessions in default course
ed attendance get <event_id>
ed attendance create --title [--start] [--hidden]
ed attendance update <id> [--title] [--closed] [--hidden]
ed attendance delete <id>
ed attendance check-in <id> --users 1,2,3 [--kind present|late|...]
ed attendance undo <id> --users 1,2,3
ed attendance analytics

# Other
ed comments edit <id> --content
ed comments delete <id>
ed users activity <user_id>
ed files upload <path>
```

### Output Format

- Default: compact JSON (single object or array)
- `--pretty`: human-readable tables
- Errors: JSON `{"error": "message"}` with non-zero exit code

### Response Trimming

CLI applies the same key-set trimming as MCP tools — list commands return compact summaries, detail commands return full content.

## Skills Design

Four skills for CLI mode. Each has a YAML frontmatter + instruction body (~200-400 tokens).

### `ed-threads`

**Triggers:** browsing, searching, creating, or replying to Ed Discussion threads

**Covers:** `ed threads list/get/search/create/edit/reply/delete`, Ed XML content format, thread cross-referencing with `<link>` tags

### `ed-attendance`

**Triggers:** attendance, check-ins, sessions, attendance analytics

**Covers:** `ed attendance list/get/create/update/delete/check-in/undo/analytics`, check-in kinds (present/late/excused/absent), finding user IDs via `ed courses users`

### `ed-moderation`

**Triggers:** locking, pinning, endorsing, marking duplicates, accepting answers, recategorising

**Covers:** `ed threads mod` flags, `ed threads recategorise`, `ed comments edit/delete`

### `ed-admin`

**Triggers:** course info, enrollment, student activity, course stats

**Covers:** `ed courses list/stats/users/categories`, `ed users activity`, `ed config set-course`, `ed files upload`

## Tech Stack

- **CLI framework:** Typer (type-hint driven, wraps Click)
- **Shared client:** existing `EdClient` from `client.py`
- **Plugin format:** Claude Code plugin spec (`.claude-plugin/plugin.json`)
- **No new runtime dependencies beyond:** `typer`
