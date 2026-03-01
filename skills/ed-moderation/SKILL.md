---
name: ed-moderation
description: >
  Use when the user asks to moderate Ed Discussion threads — locking, pinning,
  endorsing, marking duplicates, accepting answers, or recategorising threads.
  Requires the `ed` CLI.
allowed-tools:
  - Bash
---

# Ed Discussion Thread Moderation

## Setup
Ensure `ED_API_TOKEN` is set.

## Moderate a Thread

All moderation actions use one command with flags:

```
ed threads mod <thread_id> [FLAGS]
```

**Flags:**
- `--lock` / `--unlock` — prevent/allow new comments
- `--pin` / `--unpin` — stick to top of feed
- `--endorse` / `--unendorse` — instructor badge
- `--duplicate-of <original_id>` — mark as duplicate
- `--unmark-duplicate` — remove duplicate mark
- `--accept <comment_id>` — accept answer on question thread

**Combine flags:**
```
ed threads mod 12345 --lock --pin
ed threads mod 12345 --duplicate-of 11111
ed threads mod 12345 --accept 67890
```

## Recategorise Threads

Move multiple threads to a new category:
```
ed threads recategorise 111 222 333 --category "Labs" [--subcategory "Lab 1"]
```

## Edit/Delete Comments
```
ed comments edit <comment_id> --content '<document version="2.0"><paragraph>Updated</paragraph></document>'
ed comments delete <comment_id>
```

## Find Valid Categories
```
ed courses categories [--course ID]
```
