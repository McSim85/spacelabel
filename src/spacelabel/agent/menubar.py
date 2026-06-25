"""Menu-bar item (primary display mode) -- raw ``NSStatusItem`` (DESIGN.md §6.1).

``rumps`` is rejected (DECISIONS.md 2.1): it wraps these same ~15 calls, would
seize ``NSApplication``/run-loop ownership, and is poorly packaged (sdist-only,
no PyObjC deps). Tahoe
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
    NSMakePoint,
    NSMakeRect,
    NSMenu,
    NSStatusBar,
    NSVariableStatusItemLength,
    NSView,
)

from spacelabel import labeling

if TYPE_CHECKING:
    from collections.abc import Callable

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
_ROW_HEIGHT = 22.0
_ALPHA_CURRENT = 1.0
_ALPHA_INACTIVE = 0.4


class PillModel:
    """One pill in the buttons row: its text, current-ness, color, and Space identity.

    A plain value object (no PyObjC) so the row's per-display layout is easy to
    assemble in :meth:`MenuBarItem.set_buttons_row` and unit-test in isolation. The
    clicked-pill -> Space identity used by click-to-switch hit-testing (DECISIONS.md 9.5)
    is the ``uuid`` for a labelable Space, or ``(display_uuid, id64)`` for the default
    unlabelable Space (``uuid=""``), switched by ordinal via its stable session id (9.5
    update). ``display_uuid`` disambiguates default Spaces across displays, whose ``id64``
    can collide (a low/reused default id). A pill with no identity (``uuid=""`` and
    ``id64=0``) is not a switch target and a click on it opens the menu.
    """

    __slots__ = ("color", "display_uuid", "id64", "is_current", "text", "uuid")

    def __init__(
        self,
        text: str,
        *,
        is_current: bool,
        color: str | None,
        uuid: str = "",
        display_uuid: str = "",
        id64: int = 0,
    ) -> None:
        """Store the pill text, its current marker, an optional hex color, and identity."""
        self.text = text
        self.is_current = is_current
        self.color = color
        self.uuid = uuid
        self.display_uuid = display_uuid
        self.id64 = id64


def _pill_width(pill: PillModel) -> float:
    """Estimate a pill's width from its text (monospaced-ish heuristic). Pure."""
    # 7.5 pt/char is a safe upper bound for the small system font used here.
    text_width = max(1, len(pill.text)) * 7.5
    return max(_PILL_MIN_WIDTH, text_width + 2 * _PILL_PAD_X)


def _pill_layout(
    groups: Sequence[Sequence[PillModel]], height: float
) -> tuple[list[tuple[float, float, PillModel]], float, list[float]]:
    """Walk the row once, returning the pill frames, the pill ``y``, and divider ``x``s.

    Returns ``(pills, cy, divider_xs)`` where ``pills`` is ``[(x, width, pill), ...]``
    in draw order and ``cy`` is the shared vertical origin. ONE walk feeds both
    drawing (:meth:`ButtonsRowView._draw_pills`) and hit-testing
    (:func:`_pill_at_x`), so a clicked pixel resolves to the pill that was drawn
    there -- a pill must never look clickable and switch to the wrong Space
    (DECISIONS.md 9.5). Pure arithmetic over the layout constants: unit-testable
    without a WindowServer.
    """
    cy = (height - _PILL_HEIGHT) / 2.0
    pills: list[tuple[float, float, PillModel]] = []
    divider_xs: list[float] = []
    x = _ROW_MARGIN_X
    for index, group in enumerate(groups):
        if index > 0:
            x += _DIVIDER_GAP
            divider_xs.append(x)
            x += _DIVIDER_WIDTH + _DIVIDER_GAP
        for pill_index, pill in enumerate(group):
            if pill_index > 0:
                x += _PILL_GAP
            width = _pill_width(pill)
            pills.append((x, width, pill))
            x += width
    return pills, cy, divider_xs


def _pill_at_x(pills: Sequence[tuple[float, float, PillModel]], x: float) -> PillModel | None:
    """Return the pill whose horizontal span contains ``x`` (else ``None``). Pure.

    Hit-tests on the x-range only: the row is thin and pills are its sole content,
    so a click in the small vertical padding still selects the pill beneath it.
    """
    for px, width, pill in pills:
        if px <= x <= px + width:
            return pill
    return None


def _preferred_width(groups: Sequence[Sequence[PillModel]]) -> float:
    """Total width the row needs (right edge of the last pill + margin). Pure."""
    pills, _, _ = _pill_layout(groups, _ROW_HEIGHT)
    if not pills:
        return _PILL_MIN_WIDTH + _ROW_MARGIN_X * 2
    last_x, last_width, _ = pills[-1]
    return float(last_x + last_width + _ROW_MARGIN_X)


class ButtonsRowView(NSView):
    """Custom flat view drawing the per-display pill row (DECISIONS.md 9.4).

    One view for the whole row (never N status items): pills are laid out
    left-to-right grouped by physical display, displays separated by a thin
    vertical divider, the current Space marked by full alpha and the rest dimmed.

    When click-to-switch is enabled (DECISIONS.md 9.5) the view becomes the hit
    target (:meth:`hitTest_`) and a pill click resolves to its Space UUID via the
    shared layout; a click off a pill (or a right-click) opens the status menu so
    Preferences/Quit stay reachable while the row captures clicks. Display-only by
    default (the row falls clicks through to the status button).
    """

    def initWithFrame_(self, frame: object) -> ButtonsRowView | None:  # noqa: N802
        """Initialize with an empty pill layout and click-to-switch disabled."""
        self = objc.super(ButtonsRowView, self).initWithFrame_(frame)
        if self is None:
            return None
        # list of display groups; each group is a list[PillModel]
        self._groups: list[list[PillModel]] = []
        self._click_enabled: bool = False
        self._switch_handler: Callable[[str, str, int], None] | None = None
        self._menu_handler: Callable[[], None] | None = None
        return self

    @objc.python_method
    def set_groups(self, groups: Sequence[Sequence[PillModel]]) -> None:
        """Replace the pill groups (one inner sequence per physical display)."""
        self._groups = [list(group) for group in groups]
        self.setNeedsDisplay_(True)

    @objc.python_method
    def set_handlers(
        self,
        switch_handler: Callable[[str, str, int], None] | None,
        menu_handler: Callable[[], None] | None,
    ) -> None:
        """Wire the pill-click (uuid, display_uuid, id64) and menu-open callbacks (9.5)."""
        self._switch_handler = switch_handler
        self._menu_handler = menu_handler

    @objc.python_method
    def set_click_enabled(self, enabled: bool) -> None:
        """Capture clicks (enabled) or pass them through to the status button (disabled).

        Capture is gated in :meth:`hitTest_`: when disabled the view is transparent to
        the mouse, so a click falls through to the status item and opens the menu
        (display-only pills); when enabled the view is the hit target so a pill press
        reaches :meth:`mouseDown_` and can switch Spaces (DECISIONS.md 9.5).
        """
        self._click_enabled = enabled

    @objc.python_method
    def preferred_width(self) -> float:
        """Total width this row needs, including gaps and per-display dividers."""
        return _preferred_width(self._groups)

    def isFlipped(self) -> bool:  # noqa: N802
        """Use a bottom-left origin (standard Cocoa); pills are vertically centered."""
        return False

    def hitTest_(self, point: object) -> object:  # noqa: N802
        """Be the hit target only when click capture is enabled; else fall through.

        Returning ``nil`` while disabled makes the row transparent to the mouse so the
        click reaches the status button and opens the menu -- the NSView equivalent of
        the click-through used for display-only pills (DECISIONS.md 9.5). NSView has no
        ``ignoresMouseEvents`` (that is an NSWindow property), so hit-testing is the
        correct gate.
        """
        if not self._click_enabled:
            return None
        return objc.super(ButtonsRowView, self).hitTest_(point)

    def drawRect_(self, rect: object) -> None:  # noqa: N802
        """Draw the pills and dividers (called by AppKit on the main thread)."""
        try:
            self._draw_pills()
        except (ValueError, TypeError) as exc:
            # Drawing must never crash the agent; log and leave the row blank.
            log.warning("buttons-row draw failed: %s", exc)

    def mouseDown_(self, event: object) -> None:  # noqa: N802
        """Switch to the clicked pill's Space, or open the menu off a pill.

        Only reached when click-to-switch is enabled (otherwise the view ignores the
        mouse). A pill hit invokes the switch handler with its UUID; a click in the
        gap/margin opens the status menu so Preferences/Quit stay reachable
        (DECISIONS.md 9.5).
        """
        if not self._click_enabled:
            self._invoke_menu()
            return
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        self._handle_click_at_x(float(point.x))

    def rightMouseDown_(self, _event: object) -> None:  # noqa: N802
        """Open the status menu unconditionally (reachable while pills capture clicks)."""
        self._invoke_menu()

    @objc.python_method
    def _handle_click_at_x(self, x: float) -> None:
        """Route a click at view-x ``x``: a switchable pill switches; otherwise open the menu.

        Split out of :meth:`mouseDown_` (which only converts the event point) so the
        pill-resolution dispatch is unit-testable without synthesizing an NSEvent. A
        pill is a switch target when it carries a Space identity -- a ``uuid`` (labelable
        Space) or a session ``id64`` (the default unlabelable Space, switched by ordinal
        via its stable session id, DECISIONS.md 9.5). A pill with neither (``uuid=""`` and
        ``id64=0``) opens the menu like a click off any pill, never a dead click.
        """
        pills, _, _ = _pill_layout(self._groups, float(self.bounds().size.height))
        pill = _pill_at_x(pills, x)
        if pill is not None and (pill.uuid or pill.id64) and self._switch_handler is not None:
            self._switch_handler(pill.uuid, pill.display_uuid, pill.id64)
            return
        self._invoke_menu()

    @objc.python_method
    def _invoke_menu(self) -> None:
        """Invoke the menu-open callback if wired."""
        if self._menu_handler is not None:
            self._menu_handler()

    @objc.python_method
    def _draw_pills(self) -> None:
        """Lay out and fill each pill; dim non-current ones via alpha."""
        font = NSFont.menuBarFontOfSize_(0) or NSFont.systemFontOfSize_(11.0)
        pills, cy, divider_xs = _pill_layout(self._groups, float(self.bounds().size.height))
        for divider_x in divider_xs:
            NSColor.tertiaryLabelColor().setFill()
            _fill_rect(NSMakeRect(divider_x, cy, _DIVIDER_WIDTH, _PILL_HEIGHT))
        for x, width, pill in pills:
            self._draw_one_pill(pill, x, cy, width, font)

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
        #: Pill-click handler (Space UUID -> switch), wired by the delegate; the
        #: menu-open handler is the item's own :meth:`_pop_up_menu` (DECISIONS.md 9.5).
        self._pill_switch_handler: object | None = None
        if show_buttons_row:
            self._install_row_view()

    def _install_row_view(self) -> None:
        """Embed a buttons-row view in the status item's button (display-only until enabled)."""
        button = self._item.button()
        if button is None:
            log.warning("status item has no button; buttons row unavailable")
            return
        frame = NSMakeRect(0.0, 0.0, _PILL_MIN_WIDTH + _ROW_MARGIN_X * 2, _ROW_HEIGHT)
        view = ButtonsRowView.alloc().initWithFrame_(frame)
        # Pills are display-only until click-to-switch enables capture: the view
        # starts with _click_enabled False, so hitTest_ falls clicks through to the
        # status button (the menu) until set_click_enabled(True) (DECISIONS.md 9.5).
        button.addSubview_(view)
        self._row_view = view
        # Re-wire handlers if they were set before the row existed (runtime re-install).
        view.set_handlers(self._pill_switch_handler, self._pop_up_menu)

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

    def set_pill_switch_handler(self, handler: object | None) -> None:
        """Wire the pill-click handler (Space UUID -> switch); DECISIONS.md 9.5.

        The menu-open handler is always this item's own :meth:`_pop_up_menu`, so the
        delegate only supplies the switch action. Stored so a runtime row re-install
        re-wires it.
        """
        self._pill_switch_handler = handler
        if self._row_view is not None:
            self._row_view.set_handlers(handler, self._pop_up_menu)

    def set_pills_clickable(self, enabled: bool) -> None:
        """Enable/disable pill click-to-switch capture on the row view (DECISIONS.md 9.5).

        When disabled (the default, and whenever switching is unavailable) the row
        ignores the mouse so clicks open the menu; the delegate toggles this when
        ``menubar.click_to_switch`` changes or a switch attempt fails.
        """
        if self._row_view is not None:
            self._row_view.set_click_enabled(enabled)

    def _pop_up_menu(self) -> None:
        """Pop up the status-item menu (so it stays reachable while pills capture clicks).

        Used as the row view's menu-open handler. Exact drop position is GUI-only
        (Phase-6 tuning); anchoring just below the row is the sane default.
        """
        button = self._item.button()
        if button is None:
            return
        location = NSMakePoint(0.0, button.bounds().size.height + 4.0)
        self._menu.popUpMenuPositioningItem_atLocation_inView_(None, location, button)

    def set_title(self, text: str) -> None:
        """Update the status-item title (call on the main thread)."""
        button = self._item.button()
        if button is None:
            log.warning("status item has no button; cannot set title %r", text)
            return
        button.setImage_(None)  # drop any inactive-mode icon
        button.setImagePosition_(0)  # NSNoImage: reset from NSImageOnly so title shows
        button.setTitle_(str(text))

    def set_inactive(self) -> None:
        """Show a neutral icon (menu-bar mode off): no Space label, menu still reachable.

        The status item also hosts the Preferences/Quit menu, so it must stay
        present even when the menu-bar *display* mode is disabled -- it just stops
        reflecting the active Space (DESIGN.md §6.1 / item W).
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
            icon.setTemplate_(True)  # correct tint in light/dark menu bar
            button.setImage_(icon)
            button.setImagePosition_(1)  # NSImageOnly: icon without title padding
        else:
            log.warning("square.dashed SF Symbol unavailable; menu-bar-off item shows blank")

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
                        uuid=space.uuid,
                        display_uuid=space.display_uuid,
                        id64=space.id64,
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
