# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## v0.10.1 (2026-04-23)

### Fix

- include url in list_threads and search_threads summaries
- extract file URL from Ed upload response

## v0.10.0 (2026-04-09)

### Feat

- index from document field, extract media URLs
- auto-resync index when stale (>30 minutes)
- add write-through index updates for reply, edit, create, delete
- add sync_index and search_index MCP tools
- add bulk discussion threads endpoint to client
- add FTS5 index module with build and search

### Fix

- address PR review feedback (connection leak, cache invalidation, encapsulation)
- use POST for bulk analytics endpoint
- use region prefix for analytics bulk endpoint

### Refactor

- deduplicate image/file regex patterns in server.py
- remove disk cache from search index
- eliminate double lookup in bulk_recategorise cache invalidation
- simplify code after review
- update search_threads docstring to reference search_index
- inline _compact() into dict comprehensions

## v0.9.0 (2026-03-25)

### Feat

- add upload_file_url tool for uploading files from URLs
- use lightweight enrollment endpoint for headcounts
- add new_replies count to get_course_stats and document all thread filters

### Fix

- correct enrollment count total and update docstring
- resolve user names on comments and answers in get_thread

### Refactor

- improve MCP tool docstrings for LLM discoverability
- tune HTTP client connection pooling and timeouts

## v0.8.0 (2026-03-21)

### Refactor

- drop URLs from thread summaries (reconstructible from id + course_id)
- drop full document body from user activity listings
- drop participants list from thread detail (redundant with per-comment users)
- trim admin-only fields from attendance session detail
- omit false booleans from thread summaries (only include when true)
- truncate timestamps to date-only in thread summaries
- omit null and empty-string values from all trimmed responses

### Fix

- update outdated test assertions to match current implementation

## v0.7.0 (2026-03-20)

### Feat

- add category filter, search post-filters, and dry-run mode

### Fix

- improve 403 error message with enrollment context
- correct version and repo URL in plugin.json

## v0.6.0 (2026-03-01)

### Feat

- add 4 Claude Code skills for CLI mode
- add Claude Code plugin manifest and MCP config
- add moderation, attendance, and remaining CLI commands
- add course and thread CLI commands
- add CLI scaffold with typer, config management, and usage command

### Fix

- correct plan errors found during review

### Refactor

- extract shared helpers into _helpers.py for CLI reuse

## v0.5.0 (2026-03-01)

### Feat

- add attendance check-in and analytics tools
- add attendance session management tools
- add attendance client methods to EdClient

### Refactor

- add attendance key-set constants

## v0.4.0 (2026-02-11)

### Feat

- add url field to write tool responses
- add status, year, and code filters to list_courses
- add url field to thread responses
- add get_enrollment_counts tool and paginate list_users
- strip PII from responses by default

### Fix

- trim upload_file response to url and filename
- repair get_user_activity response parsing and field keys
- use thread ID instead of number in get_thread_by_url
- correct changelog extraction in release workflow

### Refactor

- remove redundant user_id from thread/comment key sets

## v0.3.1 (2026-02-10)

### Fix

- remove US-specific locale from documentation URLs

### Refactor

- improve tool descriptions for natural language discovery

## v0.3.0 (2026-02-10)

### Feat

- add bulk_recategorise tool
- add get_course_stats tool
- add list_categories tool
- add mark_duplicate and unmark_duplicate tools

## v0.2.0 (2026-02-10)

### Feat

- add edit_comment and delete_comment tools
- add accept_answer tool for question threads
- add get_thread_by_url tool to parse Ed Discussion URLs
- support nested replies via parent_id in reply_to_thread

### Refactor

- return minimal confirmations from write operations
- trim get_user_activity to compact summaries
- trim list_users to compact summaries
- trim get_thread and get_course_thread responses
- trim get_user response to essential profile fields
- extract _THREAD_SUMMARY_KEYS and _summarise_threads helper
- switch JSON output to compact format

## v0.1.0 (2026-02-09)
