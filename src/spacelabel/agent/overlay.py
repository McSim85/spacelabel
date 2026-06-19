"""Persistent corner overlay — an always-on-top ``NSPanel`` (DESIGN.md §6.3).

Same non-activating, click-through, all-Spaces configuration as the HUD, but at
``NSStatusWindowLevel`` (the polite always-on-top tier). Pinned to a corner of
the active screen's ``visibleFrame()`` (avoids menu bar / Dock) and repositioned
on ``didChangeScreenParameters``. Corner and margin are config-driven.
"""

from __future__ import annotations

import logging

__all__ = ["Overlay"]

log = logging.getLogger(__name__)


class Overlay:
    """An always-visible label pinned to a screen corner."""

    def __init__(self) -> None:
        """Build the persistent non-activating panel."""
        # TODO(phase-4): construct the borderless non-activating NSPanel at
        # NSStatusWindowLevel.
        raise NotImplementedError

    def set_text(self, text: str) -> None:
        """Update the overlay label text."""
        # TODO(phase-4): update the panel's text field.
        raise NotImplementedError

    def reposition(self) -> None:
        """Re-pin to the configured corner of the active screen's visible frame."""
        # TODO(phase-4): recompute origin from visibleFrame + corner + margin.
        raise NotImplementedError
