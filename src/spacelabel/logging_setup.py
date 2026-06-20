"""Logging configuration — the one place handlers are attached.

Per DESIGN.md §8.2 and the stdlib logging HOWTO: library/module code only ever
calls ``logging.getLogger(__name__)`` and never adds handlers or calls
``basicConfig``. This module exposes :func:`setup_logging`, the single
configurator, called exactly once at the entry point (CLI or agent).
"""

from __future__ import annotations

import enum
import logging
import logging.handlers
import os
import sys
from pathlib import Path

__all__ = ["LogMode", "setup_logging"]

log = logging.getLogger(__name__)

#: The package-root logger; all module loggers propagate up to it.
_ROOT_LOGGER_NAME = "spacelabel"

#: ANSI SGR codes per level for the colorized CLI formatter (tty-only).
_RESET = "\033[0m"
_LEVEL_COLORS = {
    logging.DEBUG: "\033[2m",  # dim
    logging.INFO: "\033[36m",  # cyan
    logging.WARNING: "\033[33m",  # yellow
    logging.ERROR: "\033[31m",  # red
    logging.CRITICAL: "\033[1;31m",  # bold red
}


def _use_color(stream: object) -> bool:
    """Return whether to emit ANSI color on ``stream`` (tty + not ``NO_COLOR``).

    Honors the ``NO_COLOR`` convention (https://no-color.org) and only colors when
    the stream is an interactive terminal.
    """
    if os.environ.get("NO_COLOR"):
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


class _ColorFormatter(logging.Formatter):
    """Formatter that wraps each record in an ANSI color keyed by its level."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the record, then color the whole line by severity."""
        message = super().format(record)
        color = _LEVEL_COLORS.get(record.levelno)
        return f"{color}{message}{_RESET}" if color else message


#: Rotating-file sink limits for the agent (DESIGN.md §8.2 / INTERFACE contract).
_AGENT_LOG_MAX_BYTES = 1_000_000
_AGENT_LOG_BACKUP_COUNT = 3

#: Timestamped formatter shared by the agent's file/os_log/fallback sinks.
_TIMESTAMPED_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class LogMode(enum.Enum):
    """Which entry point is configuring logging."""

    CLI = "cli"
    AGENT = "agent"


def _default_agent_log_dir() -> Path:
    """Return the default agent log directory ``~/Library/Logs/spacelabel``.

    Returns:
        The per-user log directory the agent rotates its file sink into.
    """
    return Path.home() / "Library" / "Logs" / "spacelabel"


def _attach_stderr_fallback(root: logging.Logger) -> None:
    """Attach a stderr handler so the agent is never silent on file-sink failure.

    Args:
        root: The package-root logger to attach the fallback handler to.
    """
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(logging.Formatter(_TIMESTAMPED_FORMAT))
    root.addHandler(stream)


def setup_logging(
    mode: LogMode,
    *,
    verbose: bool = False,
    debug: bool = False,
    log_dir: Path | None = None,
    agent_level: int | None = None,
) -> None:
    """Configure the package-root logger once for the given entry point.

    CLI mode logs to stderr at ``WARNING`` (``--verbose`` -> ``INFO``, ``--debug``
    -> ``DEBUG``). Agent mode is quiet (``WARNING`` and above): it rotates a file
    sink under ``log_dir`` and, when the optional binding is present, mirrors to
    ``os_log``. If the log directory cannot be created the agent degrades to a
    stderr fallback rather than going silent.

    Args:
        mode: The active entry point.
        verbose: Raise the CLI level to ``INFO``.
        debug: Raise the CLI level to ``DEBUG`` (takes precedence over ``verbose``).
        log_dir: Override for the agent's log directory; defaults to
            ``~/Library/Logs/spacelabel``. Ignored in CLI mode.
        agent_level: Agent log level (from ``config.log_level``); defaults to
            ``WARNING``. Ignored in CLI mode (which uses ``verbose``/``debug``).
    """
    if debug:
        level = logging.DEBUG
    elif verbose:
        level = logging.INFO
    else:
        level = logging.WARNING

    root = logging.getLogger(_ROOT_LOGGER_NAME)
    # Clear any non-Null handlers on re-entry (e.g. cli -> agent) to avoid
    # double-logging; never strip the import-time NullHandler.
    for handler in list(root.handlers):
        if not isinstance(handler, logging.NullHandler):
            root.removeHandler(handler)
    root.propagate = False

    if mode is LogMode.CLI:
        root.setLevel(level)
        stream = logging.StreamHandler(sys.stderr)
        fmt = "%(levelname)s: %(message)s"
        formatter = _ColorFormatter(fmt) if _use_color(sys.stderr) else logging.Formatter(fmt)
        stream.setFormatter(formatter)
        root.addHandler(stream)
        return

    # AGENT mode: file-backed with an optional os_log mirror, at config.log_level
    # (default WARNING) so `config set log_level DEBUG` actually raises agent verbosity.
    agent = agent_level if agent_level is not None else logging.WARNING
    root.setLevel(agent)
    target_dir = log_dir if log_dir is not None else _default_agent_log_dir()
    formatter = logging.Formatter(_TIMESTAMPED_FORMAT)
    try:
        # mkdir AND opening the rotating file sink can each raise OSError (missing
        # dir, permissions, read-only volume); both must degrade to stderr, not
        # crash the agent (no-silent-except recovery, DESIGN.md §8.2).
        target_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            target_dir / "agent.log",
            maxBytes=_AGENT_LOG_MAX_BYTES,
            backupCount=_AGENT_LOG_BACKUP_COUNT,
        )
    except OSError as exc:
        # Cannot create the log dir or open the file sink; do not go silent. Fall
        # back to stderr and continue so the agent still surfaces warnings/errors.
        _attach_stderr_fallback(root)
        log.warning("cannot open agent log in %s, using stderr (%s)", target_dir, exc)
        return

    file_handler.setLevel(agent)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Optional unified-logging mirror; None is the normal result (no dependency).
    from spacelabel.platform import oslog_handler  # lazy: avoid import cycle

    oslog = oslog_handler.make_oslog_handler()
    if oslog is not None:
        oslog.setLevel(logging.WARNING)
        oslog.setFormatter(formatter)
        root.addHandler(oslog)
