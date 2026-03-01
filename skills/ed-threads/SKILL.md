---
name: ed-threads
description: >
  Use when the user asks about Ed Discussion threads — browsing, searching,
  creating posts/questions/announcements, replying, or editing thread content.
  Requires the `ed` CLI (install: uv pip install edstem-mcp).
allowed-tools:
  - Bash
---

# Ed Discussion Thread Management

## Setup
Ensure `ED_API_TOKEN` is set. Set a default course: `ed config set-course <id>`

## Commands

**Browse threads:**
```
ed threads list [--course ID] [--sort new|top|trending] [--filter unanswered|unresolved] [--limit N]
```

**Read a thread** (accepts thread ID, #number, or URL):
```
ed threads get <thread_id>
ed threads get '#42' --course 31545
ed threads get 'https://edstem.org/au/courses/31545/discussion/123'
```

**Search:**
```
ed threads search "exam deadline" [--course ID] [--limit N]
```

**Create a thread:**
```
ed threads create --title "Week 5 Reminder" --content '<document version="2.0"><paragraph>Content here</paragraph></document>' [--type post|question|announcement] [--category "General"]
```

**Edit:**
```
ed threads edit <id> [--title "New Title"] [--content '<xml>'] [--category "Labs"]
```

**Reply:**
```
ed threads reply <thread_id> --content '<document version="2.0"><paragraph>Reply text</paragraph></document>' [--type comment|answer]
```

**Delete:**
```
ed threads delete <thread_id>
```

## Content Format
Ed uses XML: `<document version="2.0"><paragraph>Your text</paragraph></document>`

Cross-reference threads: `<link href="https://edstem.org/au/courses/{course_id}/discussion/{number}">#N</link>`

## Output
All commands return compact JSON. Errors: `{"error": "message"}`.
