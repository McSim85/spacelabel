"""Logging configuration — the one place handlers are attached.

Per DESIGN.md §8.2 and the stdlib logging HOWTO: library/module code only ever
calls ``logging.getLogger(__name__)`` and never adds handlers or calls
``basicConfig``. This module exposes :func:`setup_logging`, the single
configurator, called exactly once at the entry point (CLI or agent).
"""

from __future__ import annotations

import enum
import logging
import sys

__all__ = ["LogMode", "setup_logging"]

log = logging.getLogger(__name__)

#: The package-root logger; all module loggers propagate up to it.
_ROOT_LOGGER_NAME = "spacelabel"


class LogMode(enum.Enum):
    """Which entry point is configuring logging."""

    CLI = "cli"
    AGENT = "agent"


def setup_logging(mode: LogMode, *, verbose: bool = False, debug: bool = False) -> None:
    """Configure the package-root logger once for the given entry point.

    CLI mode logs to stderr at ``WARNING`` (``--verbose`` → ``INFO``, ``--debug``
    → ``DEBUG``). Agent mode is quiet (``WARNING`` and above).

    Args:
        mode: The active entry point.
        verbose: Raise the CLI level to ``INFO``.
        debug: Raise the CLI level to ``DEBUG`` (takes precedence over ``verbose``).
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
        stream.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        root.addHandler(stream)
        return

    # AGENT mode: quiet by default.
    # TODO(phase-4): a RotatingFileHandler under ~/Library/Logs/spacelabel/ plus
    # an optional, feature-detected os_log mirror (see oslog_handler). For the
    # scaffold, fall back to stderr so the agent process is never silent.
    root.setLevel(logging.WARNING)
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(stream)
