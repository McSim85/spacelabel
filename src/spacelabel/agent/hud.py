"""On-switch HUD — a transient non-activating ``NSPanel`` (DESIGN.md §6.2).

A borderless ``NSWindowStyleMaskNonactivatingPanel`` at ``NSScreenSaverWindowLevel``
with ``CanJoinAllSpaces | Stationary | FullScreenAuxiliary``, click-through, and
``canBecomeKeyWindow``/``canBecomeMainWindow`` → ``False``. Shown with
``orderFrontRegardless`` (never ``makeKeyAndOrderFront``), faded via the animator,
auto-dismissed by a timer. A single panel instance is reused across switches.
"""

from __future__ import annotations

import logging

__all__ = ["Hud"]

log = logging.getLogger(__name__)


class Hud:
    """A brief centered banner shown on each Space change."""

    def __init__(self) -> None:
        """Build the reusable non-activating panel (hidden until shown)."""
        # TODO(phase-4): construct the borderless non-activating NSPanel.
        raise NotImplementedError

    def show(self, text: str, *, duration_ms: int) -> None:
        """Display ``text`` briefly, then fade out after ``duration_ms``."""
        # TODO(phase-4): set text, orderFrontRegardless, animate alpha, schedule dismiss.
        raise NotImplementedError
