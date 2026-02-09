# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## 0.1.0 (2026-02-09)

### Feat

- MCP server exposing Ed Discussion API as tools
- Async HTTP client with Bearer token auth
- Compact thread summaries for context efficiency

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
