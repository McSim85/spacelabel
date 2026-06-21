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
    NSAttributedString,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSLineBreakByWordWrapping,
    NSMakeRect,
    NSMutableAttributedString,
    NSMutableParagraphStyle,
    NSParagraphStyleAttributeName,
    NSScreen,
    NSStatusWindowLevel,
    NSTextAlignmentLeft,
    NSTextField,
)

from spacelabel.agent import geometry
from spacelabel.agent.hud import make_non_activating_panel
from spacelabel.model import Note

__all__ = ["Overlay"]

log = logging.getLogger(__name__)

#: Padding around the overlay text (points).
_OVERLAY_PAD_X = 14.0
_OVERLAY_PAD_Y = 8.0
#: Glyphs for a note's checkbox state on the overlay — display-only, never an
#: interactive control (the panel is click-through, DESIGN.md §6.3 / DECISIONS 9.10).
_GLYPH_DONE = "☑"  # ☑
_GLYPH_TODO = "☐"  # ☐
#: Vertical spacing between overlay lines (points), used when notes are shown.
_OVERLAY_LINE_SPACING = 2.0


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
        self._field.setAlignment_(1)  # NSTextAlignmentCenter (title-only path)
        # Allow the multi-line title+notes render (set_content); the title-only
        # set_text path still draws a single centered line. Lines break only on the
        # explicit newlines we insert (sizeThatFits is queried with a huge width).
        self._field.setUsesSingleLineMode_(False)
        # Word-wrap long lines (a long note, or a long title) so the panel can be
        # width-clamped to the screen in _fit_and_show instead of overflowing it.
        self._field.setLineBreakMode_(NSLineBreakByWordWrapping)
        cell = self._field.cell()
        if cell is not None:
            cell.setWraps_(True)
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
        """Update the overlay to a single centered title line and resize to fit.

        The quiet default (no notes); :meth:`set_content` delegates here when a
        Space has no tasks, preserving the original single-line behavior.
        """
        self._field.setStringValue_(str(text))
        self._fit_and_show()

    @objc.python_method
    def set_content(
        self, title: str, notes: list[Note], *, note_font_size: int | None = None
    ) -> None:
        """Render a bold title plus the per-Space task list, resizing to fit.

        With no ``notes`` this is exactly :meth:`set_text` (single centered title).
        With notes, the title is drawn in the configured title font/weight and each
        task on its own left-aligned line in the normal-weight system font at
        ``note_font_size`` (defaults to a step below the title), prefixed by a glyph
        reflecting ``done`` (``☑``/``☐``). The glyph is **display-only** — toggling is
        done via ``spacelabel note done`` and reflected on the next refresh; the panel
        stays click-through and never captures the click (DESIGN.md §6.3, DECISIONS 9.10).
        """
        if not notes:
            self.set_text(title)
            return
        note_size = note_font_size if note_font_size is not None else max(9, self._font_size - 2)
        self._field.setAttributedStringValue_(self._build_content(title, notes, int(note_size)))
        self._fit_and_show()

    @objc.python_method
    def _build_content(self, title: str, notes: list[Note], note_size: int) -> object:
        """Build the title+notes attributed string (bold title, normal-weight tasks)."""
        para = NSMutableParagraphStyle.alloc().init()
        para.setAlignment_(NSTextAlignmentLeft)
        para.setLineSpacing_(_OVERLAY_LINE_SPACING)
        para.setLineBreakMode_(NSLineBreakByWordWrapping)  # wrap long task lines
        title_size = float(self._font_size)
        title_font = (
            NSFont.boldSystemFontOfSize_(title_size)
            if self._bold
            else NSFont.systemFontOfSize_(title_size)
        )
        note_font = NSFont.systemFontOfSize_(float(note_size))
        color = NSColor.labelColor()
        attributed = NSMutableAttributedString.alloc().init()
        attributed.appendAttributedString_(
            NSAttributedString.alloc().initWithString_attributes_(
                str(title),
                {
                    NSFontAttributeName: title_font,
                    NSForegroundColorAttributeName: color,
                    NSParagraphStyleAttributeName: para,
                },
            )
        )
        note_attrs = {
            NSFontAttributeName: note_font,
            NSForegroundColorAttributeName: color,
            NSParagraphStyleAttributeName: para,
        }
        for item in notes:
            glyph = _GLYPH_DONE if item.done else _GLYPH_TODO
            line = f"\n{glyph} {item.text}"
            attributed.appendAttributedString_(
                NSAttributedString.alloc().initWithString_attributes_(line, note_attrs)
            )
        return attributed

    @objc.python_method
    def _max_content_size(self) -> tuple[float, float]:
        """Max text (width, height) in points so the panel fits the target screen.

        Derived from the overlay's screen ``visibleFrame`` (the one it is/will be
        pinned to). Returns a large unconstrained pair when no screen is known yet
        (the agent always repositions before set_content, so a real screen is set in
        practice).
        """
        # Typed object|None (like _place's param) so the None guard narrows to a
        # plain object — visibleFrame() is then a (relaxed) attr access, not union-attr.
        screen: object | None = self._screen or NSScreen.mainScreen()
        if screen is None:
            return (10_000.0, 10_000.0)
        size = screen.visibleFrame().size
        margin = float(self._margin)
        max_w = geometry.overlay_max_content_extent(float(size.width), margin, _OVERLAY_PAD_X)
        max_h = geometry.overlay_max_content_extent(float(size.height), margin, _OVERLAY_PAD_Y)
        return (max_w, max_h)

    @objc.python_method
    def _fit_and_show(self) -> None:
        """Resize the panel to fit the field, clamped to the screen, and re-pin.

        Long content wraps within the max width (the field word-wraps) and the panel
        is clamped to the max height too, so neither a long note line nor a long task
        list can push the anchored panel off-screen (DECISIONS.md 9.10). When the list
        exceeds the height bound the overflow simply clips (the corner overlay is a
        glance summary; ``note list`` shows the full queue).
        """
        max_w, max_h = self._max_content_size()
        fitting = self._field.sizeThatFits_((max_w, 10_000.0))
        width = min(float(fitting.width), max_w) + _OVERLAY_PAD_X * 2
        height = min(float(fitting.height), max_h) + _OVERLAY_PAD_Y * 2
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
