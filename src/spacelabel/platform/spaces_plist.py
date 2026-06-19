"""Fallback parser for ``~/Library/Preferences/com.apple.spaces.plist`` (DESIGN.md §11).

A **topology/UUID-enumeration fallback only** — used when the CGS read path raises
:class:`spacelabel.platform.cgs.CGSUnavailableError`. It is cached by ``cfprefsd`` and
flushed only on Space create/delete, so it is **stale for the current Space**;
always read the live current Space via CGS (DECISIONS.md 3.4).
"""

from __future__ import annotations

import logging
from pathlib import Path

from spacelabel.model import Space

__all__ = ["plist_path", "read_spaces"]

log = logging.getLogger(__name__)


def plist_path() -> Path:
    """Return the path to the Spaces preferences plist."""
    return Path.home() / "Library" / "Preferences" / "com.apple.spaces.plist"


def read_spaces() -> list[Space]:
    """Enumerate Spaces (UUID + display) from the plist; never current-Space liveness."""
    # TODO(phase-4): plistlib.load SpacesDisplayConfiguration -> Management Data ->
    # Monitors -> Spaces[].uuid; log + recover on a malformed/absent file.
    raise NotImplementedError
