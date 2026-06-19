"""Menu-bar item (primary display mode) — raw ``NSStatusItem`` (DESIGN.md §6.1).

``rumps`` is rejected (DECISIONS.md 2.1): it wraps these same ~15 calls, would
seize ``NSApplication``/run-loop ownership, and is a pipx packaging trap. Tahoe
caveat: "process alive" ≠ "icon visible" — run exactly one instance, don't
hardcode contrast against the transparent Liquid-Glass bar, and surface a
non-menu-bar fallback.
"""

from __future__ import annotations

import logging

__all__ = ["MenuBarItem"]

log = logging.getLogger(__name__)


class MenuBarItem:
    """The active Space's label shown as an ``NSStatusItem`` title."""

    def __init__(self) -> None:
        """Create the variable-length status item and attach its menu."""
        # TODO(phase-4): NSStatusBar.systemStatusBar().statusItemWithLength_(...);
        # attach an NSMenu (Preferences…, Quit).
        raise NotImplementedError

    def set_title(self, text: str) -> None:
        """Update the status-item title (call on the main thread)."""
        # TODO(phase-4): self._item.button().setTitle_(text).
        raise NotImplementedError
