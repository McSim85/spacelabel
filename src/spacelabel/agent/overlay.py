"""Persistent corner overlay -- an always-on-top ``NSPanel`` (DESIGN.md §6.3).

Same non-activating, click-through, all-Spaces configuration as the HUD, but at
``NSStatusWindowLevel`` (the polite always-on-top tier). Pinned to a corner of
the active screen's ``visibleFrame()`` (avoids menu bar / Dock) and repositioned
on ``didChangeScreenParameters``. Corner and margin are config-driven and placed
by the shared nine-anchor helper (DESIGN.md §9.9).
"""

from __future__ import annotations

import logging

import objc
from AppKit import (
    NSColor,
    NSFont,
    NSMakeRect,
    NSScreen,
    NSStatusWindowLevel,
    NSTextField,
)

from spacelabel.agent import geometry
from spacelabel.agent.hud import make_non_activating_panel

__all__ = ["Overlay"]

log = logging.getLogger(__name__)

#: Padding around the overlay text (points).
_OVERLAY_PAD_X = 14.0
_OVERLAY_PAD_Y = 8.0


class Overlay:
    """An always-visible label pinned to a screen corner (one panel per display)."""

    def __init__(self, *, font_size: int = 15, bold: bool = True) -> None:
        """Build the persistent non-activating panel at ``NSStatusWindowLevel``.

        Args:
            font_size: Point size for the overlay text (resolved by the agent via
                :func:`~spacelabel.agent.geometry.overlay_font_size`).
            bold: Draw the label (the overlay title) in the bold system font.
        """
        self._panel = make_non_activating_panel(120.0, 36.0, NSStatusWindowLevel)
        self._corner = "top-right"
        self._margin = 12
        # The display this overlay belongs to (one panel per display); retained so a
        # set_text() refresh re-pins to the SAME screen, never collapsing onto main.
        self._screen: object | None = None
        self._field = NSTextField.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 120.0, 36.0))
        self._field.setBezeled_(False)
        self._field.setEditable_(False)
        self._field.setSelectable_(False)
        self._field.setDrawsBackground_(True)
        self._field.setBackgroundColor_(
            NSColor.windowBackgroundColor().colorWithAlphaComponent_(0.8)
        )
        self._field.setTextColor_(NSColor.labelColor())
        self._field.setAlignment_(1)  # NSTextAlignmentCenter
        # Cached so set_font() can no-op when overlay.font_size/bold are unchanged on
        # a live config reload (the common case) and only re-apply when they differ.
        self._font_size = int(font_size)
        self._bold = bool(bold)
        self._apply_font()
        self._field.setWantsLayer_(True)
        layer = self._field.layer()
        if layer is not None:
            layer.setCornerRadius_(8.0)
            layer.setMasksToBounds_(True)
        self._panel.setContentView_(self._field)
        self._panel.setAlphaValue_(1.0)

    @objc.python_method
    def _apply_font(self) -> None:
        """Set the field font from the cached ``_font_size`` / ``_bold``."""
        size = float(self._font_size)
        self._field.setFont_(
            NSFont.boldSystemFontOfSize_(size) if self._bold else NSFont.systemFontOfSize_(size)
        )

    @objc.python_method
    def set_font(self, font_size: int, bold: bool) -> None:
        """Re-apply the overlay font on a live ``config set`` (no-op when unchanged).

        The agent calls this every refresh so ``overlay.font_size`` / ``overlay.bold``
        edits take effect without restarting (they were previously captured only in
        :meth:`__init__`). When the values are unchanged this returns immediately; the
        following :meth:`set_text` re-fits the panel to the new font.
        """
        if int(font_size) == self._font_size and bool(bold) == self._bold:
            return
        self._font_size = int(font_size)
        self._bold = bool(bold)
        self._apply_font()

    def set_text(self, text: str) -> None:
        """Update the overlay label text and resize the panel to fit it."""
        self._field.setStringValue_(str(text))
        fitting = self._field.sizeThatFits_((10_000.0, 10_000.0))
        width = float(fitting.width) + _OVERLAY_PAD_X * 2
        height = float(fitting.height) + _OVERLAY_PAD_Y * 2
        self._panel.setContentSize_((width, height))
        self._field.setFrame_(NSMakeRect(0.0, 0.0, width, height))
        self.reposition()
        self._panel.orderFrontRegardless()

    def reposition(
        self,
        screen: object | None = None,
        corner: str | None = None,
        margin: int | None = None,
    ) -> None:
        """Re-pin to the configured corner of a screen's visible frame.

        Args:
            screen: Target ``NSScreen``; defaults to the main screen.
            corner: One of the nine :data:`~spacelabel.agent.geometry.ANCHORS`;
                ``None`` keeps the current corner.
            margin: Edge inset in points; ``None`` keeps the current margin.
        """
        if corner is not None:
            self._corner = corner if corner in geometry.ANCHORS else "top-right"
        if margin is not None:
            self._margin = margin
        # Keep this overlay on its own display: prefer an explicit screen, else the
        # retained one, and only fall back to main as a last resort.
        target = screen if screen is not None else (self._screen or NSScreen.mainScreen())
        if target is None:
            log.warning("no screen available; overlay not repositioned")
            return
        self._screen = target
        self._place(target)

    @objc.python_method
    def _place(self, screen: object) -> None:
        """Compute and set the panel origin via the shared anchor helper."""
        vf = screen.visibleFrame()
        frame = self._panel.frame()
        x, y = geometry.anchor_origin(
            (float(vf.origin.x), float(vf.origin.y), float(vf.size.width), float(vf.size.height)),
            float(frame.size.width),
            float(frame.size.height),
            self._corner,
            float(self._margin),
        )
        self._panel.setFrameOrigin_((x, y))

    def order_out(self) -> None:
        """Hide the overlay panel (mode disabled)."""
        self._panel.orderOut_(None)
