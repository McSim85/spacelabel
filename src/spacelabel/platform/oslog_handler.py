"""Optional ``os_log`` mirror for the agent (DESIGN.md §8.2) — feature-detected.

Not load-bearing: its PyObjC import path on Tahoe is unverified, so it is
feature-detected and silently skipped when unavailable. The ``os_log`` subsystem
string is :data:`spacelabel.BUNDLE_ID`. The agent's primary sink remains the
``RotatingFileHandler`` under ``~/Library/Logs/spacelabel/``.
"""

from __future__ import annotations

import logging
from typing import Any

from spacelabel import BUNDLE_ID

__all__ = ["make_oslog_handler"]

log = logging.getLogger(__name__)

#: Map stdlib levels onto ``os_log`` types (lazy import of OSLogType happens in
#: the handler; the names here drive the lookup at emit time).
_OSLOG_TYPE_BY_LEVEL = {
    logging.CRITICAL: "OSLogTypeFault",
    logging.ERROR: "OSLogTypeError",
    logging.WARNING: "OSLogTypeDefault",
    logging.INFO: "OSLogTypeInfo",
    logging.DEBUG: "OSLogTypeDebug",
}


def make_oslog_handler() -> logging.Handler | None:
    """Return an ``os_log``-backed handler, or ``None`` if the API is unavailable.

    The ``pyobjc-framework-OSLog`` binding is **not** a project dependency, so
    ``None`` is the normal, expected result on most machines. A returned handler
    mirrors records into the unified logging system under the subsystem
    :data:`spacelabel.BUNDLE_ID`. Any ``ImportError``/``AttributeError`` from the
    feature probe is logged at ``DEBUG`` and degrades to ``None`` (not an error).

    Returns:
        A configured :class:`logging.Handler`, or ``None`` when no usable
        ``os_log`` PyObjC binding is present.
    """
    try:
        import OSLog  # lazy: optional, not a project dependency

        # Feature-detect the symbols we actually need; a partial binding is no
        # better than a missing one, so probe each access point up front.
        logger_factory = OSLog.OSLog.alloc
        log_type_cls = OSLog.OSLogType
    except (ImportError, AttributeError) as exc:
        log.debug("os_log mirror unavailable, skipping (%s)", exc)
        return None

    return _OSLogHandler(logger_factory, log_type_cls)


class _OSLogHandler(logging.Handler):
    """A :class:`logging.Handler` that forwards records to ``os_log``.

    Built only when :func:`make_oslog_handler` confirms a usable binding, so the
    PyObjC symbols are captured at construction and reused per record.
    """

    def __init__(self, logger_factory: Any, log_type_cls: Any) -> None:
        """Store the probed PyObjC factories and build the subsystem logger.

        Args:
            logger_factory: ``OSLog.OSLog.alloc`` bound method, used to build a
                logger for the :data:`spacelabel.BUNDLE_ID` subsystem.
            log_type_cls: The ``OSLogType`` enum class, used to map stdlib levels
                onto unified-logging severities at emit time.
        """
        super().__init__()
        self._log_type_cls = log_type_cls
        # The "default" category groups all records under one subsystem; per-logger
        # categories are a Phase-6 refinement.
        self._os_logger = logger_factory().initWithSubsystem_category_(BUNDLE_ID, "default")

    def _os_log_type(self, levelno: int) -> Any:
        """Return the ``OSLogType`` value matching a stdlib level number.

        Args:
            levelno: The record's numeric level (e.g. ``logging.WARNING``).

        Returns:
            The matching ``OSLogType`` enum member; for sub-``DEBUG`` levels it
            floors to the lowest mapped severity (``Debug``).
        """
        thresholds = sorted(_OSLOG_TYPE_BY_LEVEL, reverse=True)
        for threshold in thresholds:
            if levelno >= threshold:
                return getattr(self._log_type_cls, _OSLOG_TYPE_BY_LEVEL[threshold])
        # Below the lowest mapped level (DEBUG): floor to that lowest entry.
        return getattr(self._log_type_cls, _OSLOG_TYPE_BY_LEVEL[thresholds[-1]])

    def emit(self, record: logging.LogRecord) -> None:
        """Forward one record to ``os_log``; never raise out of logging.

        Args:
            record: The log record to mirror into unified logging.
        """
        try:
            message = self.format(record)
            log_type = self._os_log_type(record.levelno)
            self._os_logger.logWithType_message_(log_type, message)
        except (AttributeError, TypeError, ValueError) as exc:
            # A misbehaving binding must not take the process down; record the
            # fault via the stdlib hook and keep going (the file handler still has it).
            log.debug("os_log emit failed, dropping record (%s)", exc)
            self.handleError(record)
