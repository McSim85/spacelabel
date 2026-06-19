"""Optional ``os_log`` mirror for the agent (DESIGN.md §8.2) — feature-detected.

Not load-bearing: its PyObjC import path on Tahoe is unverified, so it is
feature-detected and silently skipped when unavailable. The ``os_log`` subsystem
string is :data:`spacelabel.BUNDLE_ID`. The agent's primary sink remains the
``RotatingFileHandler`` under ``~/Library/Logs/spacelabel/``.
"""

from __future__ import annotations

import logging

__all__ = ["make_oslog_handler"]

log = logging.getLogger(__name__)


def make_oslog_handler() -> logging.Handler | None:
    """Return an os_log-backed handler, or None if the API is unavailable."""
    # TODO(phase-4): feature-detect the OSLog PyObjC binding; return None (not an
    # error) when missing so logging setup degrades gracefully.
    raise NotImplementedError
