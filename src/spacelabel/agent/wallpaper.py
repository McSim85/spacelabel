"""Wallpaper mode (experimental, disabled by default) — cosmetic/best-effort (DESIGN.md §6.4).

There is **no per-Space wallpaper API**: ``setDesktopImageURL:forScreen:`` is
per-``NSScreen``, and on Sonoma+/Tahoe ``WallpaperAgent`` owns wallpaper state,
self-reverts programmatic sets, and silently flips "Show on all spaces" off on
repeated sets. We render a label image (``NSBitmapImageRep`` + Core Text at
``frame.size x backingScaleFactor`` -- no Pillow), write a per-display PNG
overwritten in place, and set it for the active screen, accepting flicker/revert.
Never edit the WallpaperAgent config store directly (DECISIONS.md 7.3).
"""

from __future__ import annotations

import logging

__all__ = ["WallpaperRenderer"]

log = logging.getLogger(__name__)


class WallpaperRenderer:
    """Best-effort renderer that draws the label onto the active screen's wallpaper."""

    def __init__(self) -> None:
        """Initialize per-display temp-path bookkeeping."""
        # TODO(phase-4): track a stable per-display temp PNG path (overwrite in place).
        raise NotImplementedError

    def render_and_set(self, text: str) -> None:
        """Render ``text`` to a PNG and set it as the active screen's wallpaper."""
        # TODO(phase-4): NSBitmapImageRep + Core Text at Retina scale; write PNG;
        # NSWorkspace.setDesktopImageURL_forScreen_options_error_. Best-effort only.
        raise NotImplementedError
