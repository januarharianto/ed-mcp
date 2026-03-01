# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## 0.1.0 (2026-02-09)

### Feat

- MCP server exposing Ed Discussion API as tools
- Async HTTP client with Bearer token auth
- Compact thread summaries for context efficiency

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
