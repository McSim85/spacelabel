"""Menu-bar item (primary display mode) -- raw ``NSStatusItem`` (DESIGN.md §6.1).

``rumps`` is rejected (DECISIONS.md 2.1): it wraps these same ~15 calls, would
seize ``NSApplication``/run-loop ownership, and is a pipx packaging trap. Tahoe
caveat: "process alive" is not "icon visible" -- run exactly one instance, don't
hardcode contrast against the transparent Liquid-Glass bar, and surface a
non-menu-bar fallback.

The buttons row (DECISIONS.md 9.4 / docs/UI.md §2.2) is ONE status item hosting
one custom CG-drawn view: pills show ``labeling.pill_text`` (leading letter(s)
of the label, else the Space number), the current Space per display is marked by
alpha (1.0 vs ~0.4) -- never color -- and physical displays are split L-to-R by a
thin vertical divider. The title-only path is the functional minimum; the row is
opt-in and drawn here best-effort.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

import objc
from AppKit import (
    NSColor,
    NSFont,
    NSMakeRect,
    NSMenu,
    NSStatusBar,
    NSVariableStatusItemLength,
    NSView,
)

from spacelabel import labeling

if TYPE_CHECKING:
    from spacelabel.model import Display, Label, Space

__all__ = ["ButtonsRowView", "MenuBarItem", "PillModel"]

log = logging.getLogger(__name__)

#: Layout constants for the optional buttons row (points). Sizes are deliberate,
#: not display-keyed -- the menu bar is a fixed-height surface (DESIGN.md §6.1).
_PILL_HEIGHT = 16.0
_PILL_MIN_WIDTH = 18.0
_PILL_PAD_X = 6.0
_PILL_GAP = 3.0
_DIVIDER_WIDTH = 1.0
_DIVIDER_GAP = 6.0
_ROW_MARGIN_X = 4.0
_ALPHA_CURRENT = 1.0
_ALPHA_INACTIVE = 0.4


class PillModel:
    """One pill in the buttons row: its text, current-ness, and optional color.

    A plain value object (no PyObjC) so the row's per-display layout is easy to
    assemble in :meth:`MenuBarItem.set_buttons_row` and unit-test in isolation.
    """

    __slots__ = ("color", "is_current", "text")

    def __init__(self, text: str, *, is_current: bool, color: str | None) -> None:
        """Store the pill text, its current marker, and an optional hex color."""
        self.text = text
        self.is_current = is_current
        self.color = color


class ButtonsRowView(NSView):
    """Custom flat view drawing the per-display pill row (DECISIONS.md 9.4).

    One view for the whole row (never N status items): pills are laid out
    left-to-right grouped by physical display, displays separated by a thin
    vertical divider, the current Space marked by full alpha and the rest dimmed.
    """

    def initWithFrame_(self, frame: object) -> ButtonsRowView | None:  # noqa: N802
        """Initialize with an empty pill layout."""
        self = objc.super(ButtonsRowView, self).initWithFrame_(frame)
        if self is None:
            return None
        # list of display groups; each group is a list[PillModel]
        self._groups: list[list[PillModel]] = []
        return self

    @objc.python_method
    def set_groups(self, groups: Sequence[Sequence[PillModel]]) -> None:
        """Replace the pill groups (one inner sequence per physical display)."""
        self._groups = [list(group) for group in groups]
        self.setNeedsDisplay_(True)

    @objc.python_method
    def _pill_width(self, pill: PillModel) -> float:
        """Estimate a pill's width from its text (monospaced-ish heuristic)."""
        # 7.5 pt/char is a safe upper bound for the small system font used here.
        text_width = max(1, len(pill.text)) * 7.5
        return max(_PILL_MIN_WIDTH, text_width + 2 * _PILL_PAD_X)

    @objc.python_method
    def preferred_width(self) -> float:
        """Total width this row needs, including gaps and per-display dividers."""
        total = _ROW_MARGIN_X * 2
        for index, group in enumerate(self._groups):
            if index > 0:
                total += _DIVIDER_GAP * 2 + _DIVIDER_WIDTH
            for pill_index, pill in enumerate(group):
                if pill_index > 0:
                    total += _PILL_GAP
                total += self._pill_width(pill)
        return float(max(total, _PILL_MIN_WIDTH + _ROW_MARGIN_X * 2))

    def isFlipped(self) -> bool:  # noqa: N802
        """Use a top-left origin so the small row math reads naturally."""
        return False

    def drawRect_(self, rect: object) -> None:  # noqa: N802
        """Draw the pills and dividers (called by AppKit on the main thread)."""
        try:
            self._draw_pills()
        except (ValueError, TypeError) as exc:
            # Drawing must never crash the agent; log and leave the row blank.
            log.warning("buttons-row draw failed: %s", exc)

    @objc.python_method
    def _draw_pills(self) -> None:
        """Lay out and fill each pill; dim non-current ones via alpha."""
        bounds = self.bounds()
        height = float(bounds.size.height)
        cy = (height - _PILL_HEIGHT) / 2.0
        font = NSFont.menuBarFontOfSize_(0) or NSFont.systemFontOfSize_(11.0)
        x = _ROW_MARGIN_X
        for index, group in enumerate(self._groups):
            if index > 0:
                x += _DIVIDER_GAP
                NSColor.tertiaryLabelColor().setFill()
                _fill_rect(NSMakeRect(x, cy, _DIVIDER_WIDTH, _PILL_HEIGHT))
                x += _DIVIDER_WIDTH + _DIVIDER_GAP
            for pill_index, pill in enumerate(group):
                if pill_index > 0:
                    x += _PILL_GAP
                width = self._pill_width(pill)
                self._draw_one_pill(pill, x, cy, width, font)
                x += width

    @objc.python_method
    def _draw_one_pill(
        self, pill: PillModel, x: float, y: float, width: float, font: object
    ) -> None:
        """Draw a single pill background + centered text at the given origin."""
        from AppKit import (
            NSFontAttributeName,
            NSForegroundColorAttributeName,
            NSString,
        )

        alpha = _ALPHA_CURRENT if pill.is_current else _ALPHA_INACTIVE
        fill = _pill_fill_color(pill.color).colorWithAlphaComponent_(alpha)
        fill.setFill()
        rect = NSMakeRect(x, y, width, _PILL_HEIGHT)
        _fill_rounded_rect(rect, 4.0)
        text_color = NSColor.labelColor().colorWithAlphaComponent_(alpha)
        attrs = {
            NSFontAttributeName: font,
            NSForegroundColorAttributeName: text_color,
        }
        ns_text = NSString.stringWithString_(pill.text)
        text_size = ns_text.sizeWithAttributes_(attrs)
        tx = x + (width - float(text_size.width)) / 2.0
        ty = y + (_PILL_HEIGHT - float(text_size.height)) / 2.0
        ns_text.drawAtPoint_withAttributes_((tx, ty), attrs)


def _fill_rect(rect: object) -> None:
    """Fill a plain rectangle with the current fill color."""
    from AppKit import NSRectFill

    NSRectFill(rect)


def _fill_rounded_rect(rect: object, radius: float) -> None:
    """Fill a rounded rectangle with the current fill color."""
    from AppKit import NSBezierPath

    path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, radius, radius)
    path.fill()


def _pill_fill_color(hex_color: str | None) -> object:
    """Resolve a pill fill color from an optional ``#rrggbb`` string.

    Falls back to a neutral control-background color when no/invalid color is set;
    color only tints the fill and never signals "current" (DECISIONS.md 9.4).
    """
    if hex_color:
        parsed = _color_from_hex(hex_color)
        if parsed is not None:
            return parsed
    return NSColor.controlAccentColor()


def _color_from_hex(hex_color: str) -> object | None:
    """Parse ``#rrggbb`` (or ``rrggbb``) into an ``NSColor``; None if malformed."""
    text = hex_color.lstrip("#")
    if len(text) != 6:
        log.debug("ignoring malformed pill color %r", hex_color)
        return None
    try:
        red = int(text[0:2], 16) / 255.0
        green = int(text[2:4], 16) / 255.0
        blue = int(text[4:6], 16) / 255.0
    except ValueError:
        log.debug("ignoring non-hex pill color %r", hex_color)
        return None
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(red, green, blue, 1.0)


class MenuBarItem:
    """The active Space's label shown as an ``NSStatusItem`` title.

    Title and menu are the functional minimum; the optional buttons row is drawn
    via :class:`ButtonsRowView` when enabled (DECISIONS.md 9.4).
    """

    def __init__(self, *, show_buttons_row: bool = False) -> None:
        """Create the variable-length status item and attach an empty menu.

        Args:
            show_buttons_row: When True, host a :class:`ButtonsRowView` in the
                status item's button instead of a plain title.
        """
        self._show_buttons_row = show_buttons_row
        status_bar = NSStatusBar.systemStatusBar()
        self._item = status_bar.statusItemWithLength_(NSVariableStatusItemLength)
        self._menu = NSMenu.alloc().init()
        self._menu.setAutoenablesItems_(False)
        self._item.setMenu_(self._menu)
        self._row_view: ButtonsRowView | None = None
        if show_buttons_row:
            self._install_row_view()

    def _install_row_view(self) -> None:
        """Embed a buttons-row view in the status item's button."""
        button = self._item.button()
        if button is None:
            log.warning("status item has no button; buttons row unavailable")
            return
        frame = NSMakeRect(0.0, 0.0, _PILL_MIN_WIDTH + _ROW_MARGIN_X * 2, 22.0)
        view = ButtonsRowView.alloc().initWithFrame_(frame)
        button.addSubview_(view)
        self._row_view = view

    def set_show_buttons_row(self, enabled: bool) -> None:
        """Install or remove the pill row at runtime (so a mode-toggle takes effect).

        When enabled, the row view becomes the indicator (the caller clears the
        title); when disabled, the row is removed and the title is shown again.
        """
        self._show_buttons_row = enabled
        if enabled and self._row_view is None:
            self._install_row_view()
        elif not enabled and self._row_view is not None:
            self._row_view.removeFromSuperview()
            self._row_view = None
            button = self._item.button()
            if button is not None:
                # Let the variable-length item resize back to its title width.
                button.setFrame_(NSMakeRect(0.0, 0.0, _PILL_MIN_WIDTH, 22.0))

    def set_title(self, text: str) -> None:
        """Update the status-item title (call on the main thread)."""
        button = self._item.button()
        if button is None:
            log.warning("status item has no button; cannot set title %r", text)
            return
        button.setImage_(None)  # drop any inactive-mode icon
        button.setTitle_(str(text))

    def set_inactive(self) -> None:
        """Show a neutral icon (menu-bar mode off): no Space label, menu still reachable.

        The status item also hosts the Preferences/Quit menu, so it must stay
        present even when the menu-bar *display* mode is disabled -- it just stops
        reflecting the active Space (DESIGN.md §6.1).
        """
        self.set_show_buttons_row(False)
        button = self._item.button()
        if button is None:
            return
        button.setTitle_("")
        from AppKit import NSImage

        icon = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "square.dashed", "spacelabel (menu-bar label off)"
        )
        if icon is not None:
            button.setImage_(icon)

    def set_buttons_row(
        self,
        spaces_by_display: Sequence[tuple[Display, Sequence[Space]]],
        labels: dict[str, Label],
        ordinals: dict[int, int],
        *,
        pill_chars: int = 1,
    ) -> None:
        """Populate the optional buttons row from per-display Space groups.

        Args:
            spaces_by_display: Physical displays in draw order, each paired with
                its labelable Spaces.
            labels: The UUID to :class:`~spacelabel.model.Label` store.
            ordinals: UUID to 1-based ordinal (the unlabeled-pill fallback).
            pill_chars: Leading label characters per pill (1..2).
        """
        if self._row_view is None:
            return
        groups: list[list[PillModel]] = []
        for _display, spaces in spaces_by_display:
            group: list[PillModel] = []
            for space in spaces:
                ordinal = ordinals.get(id(space), 0)
                text = labeling.pill_text(space, labels, ordinal, chars=pill_chars)
                label = labels.get(space.uuid)
                group.append(
                    PillModel(
                        text,
                        is_current=space.is_current,
                        color=label.color if label is not None else None,
                    )
                )
            groups.append(group)
        self._row_view.set_groups(groups)
        self._resize_row(self._row_view.preferred_width())

    def _resize_row(self, width: float) -> None:
        """Resize the status item / row view to fit the pills."""
        button = self._item.button()
        if button is None:
            return
        frame = NSMakeRect(0.0, 0.0, width, 22.0)
        button.setFrame_(frame)
        if self._row_view is not None:
            self._row_view.setFrame_(frame)

    def set_menu_items(self, items: Sequence[object]) -> None:
        """Replace the status item's menu with the given ``NSMenuItem`` objects.

        The agent uses this to populate per-display Space rows and the mode
        toggles / Preferences / Quit entries (docs/UI.md §2.3).
        """
        self._menu.removeAllItems()
        for item in items:
            self._menu.addItem_(item)

    @property
    def menu(self) -> object:
        """Return the backing ``NSMenu`` (for the delegate to build rows)."""
        return self._menu

    def remove(self) -> None:
        """Remove the status item from the menu bar (on agent quit)."""
        NSStatusBar.systemStatusBar().removeStatusItem_(self._item)
