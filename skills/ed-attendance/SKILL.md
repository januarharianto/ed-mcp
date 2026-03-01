---
name: ed-attendance
description: >
  Use when the user asks about Ed Discussion attendance — creating sessions,
  marking students present/late/excused/absent, viewing check-ins, or
  generating attendance reports. Requires the `ed` CLI.
allowed-tools:
  - Bash
---

# Ed Discussion Attendance Management

## Setup
Ensure `ED_API_TOKEN` is set. Set a default course: `ed config set-course <id>`

## Commands

**List sessions:**
```
ed attendance list [--course ID]
```

**Session details (includes check-ins):**
```
ed attendance get <event_id>
```

**Create session:**
```
ed attendance create --title "Week 3 Tutorial" [--course ID] [--start "2026-03-01T09:00:00+11:00"] [--hidden]
```

**Update session (close/reopen, hide/show, rename):**
```
ed attendance update <event_id> [--title "New Name"] [--closed] [--no-closed] [--hidden] [--no-hidden]
```

**Delete session:**
```
ed attendance delete <event_id>
```

**Manual check-in:**
```
ed attendance check-in <event_id> --users 12345,67890 [--kind present|late|excused|absent]
```
Find user IDs first: `ed courses users [--role student]`

**Undo check-in:**
```
ed attendance undo <event_id> --users 12345,67890
```

**Analytics (all sessions + all check-ins):**
```
ed attendance analytics [--course ID]
```

## Check-in Kinds
- `present` — attended (default)
- `late` — arrived late
- `excused` — excused absence
- `absent` — unexcused absence

## Workflow: Mark Attendance
1. `ed attendance list` — find the session ID
2. `ed courses users --role student` — find user IDs
3. `ed attendance check-in <event_id> --users <ids> --kind present`
