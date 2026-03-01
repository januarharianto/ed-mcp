---
name: ed-admin
description: >
  Use when the user asks about Ed Discussion course details — listing courses,
  checking enrollment, viewing student activity, reviewing course statistics,
  or managing categories. Requires the `ed` CLI.
allowed-tools:
  - Bash
---

# Ed Discussion Course Administration

## Setup
```
export ED_API_TOKEN=your_token
ed config set-course <course_id>    # save default course
```

## Commands

**List courses:**
```
ed courses list [--status active|archived] [--year 2026] [--code ENVX]
```

**Course overview:**
```
ed courses stats [--course ID]
```
Returns: enrollment count, unanswered questions, unresolved threads, top categories.

**List enrolled users:**
```
ed courses users [--course ID] [--role student|staff|admin] [--limit N] [--offset N]
```

**View user activity:**
```
ed users activity <user_id> [--course ID] [--filter thread|answer|comment|all] [--limit N]
```

**List categories:**
```
ed courses categories [--course ID]
```

**Upload a file:**
```
ed files upload /path/to/file.pdf
```
Returns: `{"url": "...", "filename": "..."}` — use the URL in thread content.

## Quick Reference
```
ed usage    # print compact CLI reference
```
