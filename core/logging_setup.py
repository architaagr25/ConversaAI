"""
Logging setup. Console stays short for watching a call live; the file is JSON
lines so a run can be measured afterwards without parsing prose.

Call setup_logging() once at start, then getLogger(__name__) as normal.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from core.config import PROJECT_ROOT, settings

# Fields the logging library sets itself. Anything else came from a caller's
# extra={} and belongs in the output.
_STANDARD_FIELDS = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "module", "msecs",
    "message", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "taskName", "thread", "threadName",
}


class JsonLineFormatter(logging.Formatter):
    """One JSON object per line, keeping any extra fields passed by the caller."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_FIELDS and not key.startswith("_"):
                try:
                    json.dumps(value)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = repr(value)

        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Short lines for a person watching in real time."""

    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        name = record.name.split(".")[-1]
        return f"{stamp}  {record.levelname:<7} {name:<16} {record.getMessage()}"


def setup_logging(
    level: str | None = None,
    log_file: Path | str | None = None,
    quiet_console: bool = False,
) -> None:
    """Configure logging for the whole program.

    Safe to call twice - handlers are replaced, not stacked.
    """
    resolved = (level or settings.log_level).upper()
    root = logging.getLogger()
    root.setLevel(resolved)
    root.handlers.clear()

    if not quiet_console:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(ConsoleFormatter())
        console.setLevel(resolved)
        root.addHandler(console)

    target = Path(log_file) if log_file else PROJECT_ROOT / "logs" / "run.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(target, encoding="utf-8")
    file_handler.setFormatter(JsonLineFormatter())
    file_handler.setLevel(logging.DEBUG)
    root.addHandler(file_handler)

    # These log every HTTP request at INFO, which buries our own output during a
    # call. Warnings from them are still wanted.
    for noisy in ("httpx", "httpcore", "urllib3", "websockets", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
