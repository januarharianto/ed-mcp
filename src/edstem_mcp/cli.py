"""Typer CLI for Ed Discussion — LLM-friendly alternative to the MCP server."""

from __future__ import annotations

import asyncio
import json
import sys
from functools import wraps
from pathlib import Path
from typing import Any

import typer

from edstem_mcp.__about__ import __version__
from edstem_mcp._helpers import _json
from edstem_mcp.client import EdAPIError, EdClient

# ------------------------------------------------------------------
# Async helper
# ------------------------------------------------------------------

def _run_async(f):
    """Decorator: run an async Typer command via asyncio.run()."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper


# ------------------------------------------------------------------
# Config management
# ------------------------------------------------------------------

_CONFIG_DIR = Path.home() / ".config" / "ed"
_CONFIG_FILE = _CONFIG_DIR / "config.json"


def _get_config() -> dict[str, Any]:
    if _CONFIG_FILE.exists():
        return json.loads(_CONFIG_FILE.read_text())
    return {}


def _set_config(key: str, value: Any) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config = _get_config()
    config[key] = value
    _CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")


def _require_course(course: int | None) -> int:
    """Resolve course ID from --course flag or saved config."""
    if course is not None:
        return course
    saved = _get_config().get("course_id")
    if saved is not None:
        return int(saved)
    print('{"error":"No course ID. Use --course or run: ed config set-course <id>"}', file=sys.stderr)
    raise typer.Exit(1)


# ------------------------------------------------------------------
# Client helper
# ------------------------------------------------------------------

def _output(data: Any) -> None:
    """Print compact JSON to stdout."""
    if isinstance(data, str):
        print(data)
    else:
        print(_json(data))


async def _client() -> EdClient:
    """Create a fresh EdClient. Caller must close it."""
    return EdClient()


# ------------------------------------------------------------------
# App structure
# ------------------------------------------------------------------

app = typer.Typer(no_args_is_help=True, add_completion=False)
courses_app = typer.Typer(no_args_is_help=True, help="Course operations.")
threads_app = typer.Typer(no_args_is_help=True, help="Thread operations.")
attendance_app = typer.Typer(no_args_is_help=True, help="Attendance operations.")
comments_app = typer.Typer(no_args_is_help=True, help="Comment operations.")
users_app = typer.Typer(no_args_is_help=True, help="User operations.")
files_app = typer.Typer(no_args_is_help=True, help="File operations.")
config_app = typer.Typer(no_args_is_help=True, help="CLI configuration.")

app.add_typer(courses_app, name="courses")
app.add_typer(threads_app, name="threads")
app.add_typer(attendance_app, name="attendance")
app.add_typer(comments_app, name="comments")
app.add_typer(users_app, name="users")
app.add_typer(files_app, name="files")
app.add_typer(config_app, name="config")


# ------------------------------------------------------------------
# Top-level commands
# ------------------------------------------------------------------


@app.command()
def usage():
    """Print compact CLI reference for LLM consumption."""
    print(f"""Ed Discussion CLI v{__version__}

Config:
  ed config set-course <id>     Save default course

Courses:
  ed courses list [--status S] [--year Y] [--code C]
  ed courses stats [--course ID]
  ed courses users [--course ID] [--role R] [--limit N] [--offset N]
  ed courses categories [--course ID]

Threads:
  ed threads list [--course ID] [--sort S] [--filter F] [--limit N] [--offset N]
  ed threads get <ref>          ref = thread_id, #number, or URL
  ed threads search <query> [--course ID] [--limit N]
  ed threads create --title T --content XML [--course ID] [--type T] [--category C]
  ed threads edit <id> [--title T] [--content XML] [--category C]
  ed threads reply <id> --content XML [--type comment|answer]
  ed threads delete <id>
  ed threads mod <id> [--lock|--unlock] [--pin|--unpin] [--endorse|--unendorse] [--duplicate-of ID] [--unmark-duplicate] [--accept COMMENT_ID]
  ed threads recategorise <ids> --category C [--subcategory S]

Attendance:
  ed attendance list [--course ID]
  ed attendance get <event_id>
  ed attendance create --title T [--course ID] [--start ISO] [--hidden]
  ed attendance update <id> [--title T] [--closed|--no-closed] [--hidden|--no-hidden]
  ed attendance delete <id>
  ed attendance check-in <id> --users U1,U2 [--kind present|late|excused|absent]
  ed attendance undo <id> --users U1,U2
  ed attendance analytics [--course ID]

Other:
  ed comments edit <id> --content XML
  ed comments delete <id>
  ed users activity <user_id> [--course ID] [--filter F] [--limit N]
  ed files upload <path>

Output: JSON (compact). Content format: Ed XML.""")


# ------------------------------------------------------------------
# Config commands
# ------------------------------------------------------------------


@config_app.command("set-course")
def config_set_course(course_id: int = typer.Argument(..., help="Course ID to use as default.")):
    """Save a default course ID so you don't need --course on every command."""
    _set_config("course_id", course_id)
    _output({"course_id": course_id, "saved": True})
