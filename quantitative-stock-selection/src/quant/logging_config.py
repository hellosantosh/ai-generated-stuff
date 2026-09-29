"""Logging setup (REQUIREMENTS 40).

Console output stays terse so the CLI remains readable; the rotating file at
``logs/app.log`` keeps the full DEBUG-capable record.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

_CONSOLE_FORMAT = "%(levelname)-8s %(message)s"
_FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)-28s %(message)s"

_configured = False


def configure_logging(
    level: str = "INFO",
    log_file: str | os.PathLike[str] | None = "logs/app.log",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    force: bool = False,
) -> logging.Logger:
    """Install console and rotating-file handlers on the root logger.

    Repeat calls are no-ops unless ``force`` is set, so importing a module
    twice cannot duplicate every log line.
    """
    global _configured
    root = logging.getLogger()
    if _configured and not force:
        return root

    for handler in list(root.handlers):
        root.removeHandler(handler)

    level_name = os.environ.get("QUANT_LOG_LEVEL", level).upper()
    resolved = getattr(logging, level_name, None)
    if not isinstance(resolved, int):
        raise ValueError(f"unknown log level: {level_name!r}")
    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler(stream=sys.stderr)
    console.setLevel(resolved)
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    root.addHandler(console)

    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(_FILE_FORMAT))
        root.addHandler(file_handler)

    # These libraries are chatty at INFO and drown out our own records.
    for noisy in ("urllib3", "matplotlib", "peewee", "yfinance"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
