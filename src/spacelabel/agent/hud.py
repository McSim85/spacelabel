"""On-switch HUD -- a transient non-activating ``NSPanel``.

A borderless ``NSWindowStyleMaskNonactivatingPanel`` at ``NSScreenSaverWindowLevel``
with ``CanJoinAllSpaces | Stationary | FullScreenAuxiliary``, click-through, and
``canBecomeKeyWindow``/``canBecomeMainWindow`` -> ``False``. Shown with
``orderFrontRegardless`` (never ``makeKeyAndOrderFront``), faded via the animator,
auto-dismissed by a timer. A single panel instance is reused across switches.

The panel is placed by the shared nine-anchor helper
:func:`spacelabel.agent.geometry.anchor_origin` on the target screen's
``visibleFrame``, so position is fully configurable.
"""

from __future__ import annotations

import logging

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSColor,
    NSFont,
    NSMakeRect,
    NSPanel,
    NSScreen,
    NSScreenSaverWindowLevel,
    NSTextField,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSTimer

from spacelabel.agent import geometry

__all__ = ["Hud", "NonActivatingPanel", "make_non_activating_panel"]

log = logging.getLogger(__name__)

#: Collection behavior shared by the HUD and overlay (float across all Spaces).
_ALL_SPACES_BEHAVIOR = (
    NSWindowCollectionBehaviorCanJoinAllSpaces
    | NSWindowCollectionBehaviorStationary
    | NSWindowCollectionBehaviorFullScreenAuxiliary
)

#: Fade timing (seconds) -- UX taste, tuned live in Phase 6.
_FADE_IN = 0.12
_FADE_OUT = 0.35

#: Padding around the HUD text (points).
_HUD_PAD_X = 28.0
_HUD_PAD_Y = 16.0


class NonActivatingPanel(NSPanel):
    """Borderless click-through panel that never becomes key or main.

    Shared base for the HUD and the corner overlay: both must
    float across every Space and never steal focus from the active app.
    """

    def canBecomeKeyWindow(self) -> bool:  # noqa: N802
        """Never become the key window (no focus theft)."""
        return False

    def canBecomeMainWindow(self) -> bool:  # noqa: N802
        """Never become the main window (no focus theft)."""
        return False


def make_non_activating_panel(width: float, height: float, level: int) -> NonActivatingPanel:
    """Build a configured borderless, click-through, all-Spaces panel.

    Args:
        width: Initial content width in points.
        height: Initial content height in points.
        level: Window level (``NSScreenSaverWindowLevel`` for the HUD,
            ``NSStatusWindowLevel`` for the overlay).

    Returns:
        A hidden :class:`NonActivatingPanel` ready to position and order front.
    """
    rect = NSMakeRect(0.0, 0.0, width, height)
    panel = NonActivatingPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        rect,
        NSWindowStyleMaskNonactivatingPanel,
        NSBackingStoreBuffered,
        False,
    )
    panel.setLevel_(level)
    panel.setCollectionBehavior_(_ALL_SPACES_BEHAVIOR)
    panel.setIgnoresMouseEvents_(True)
    panel.setOpaque_(False)
    panel.setHasShadow_(True)
    panel.setBackgroundColor_(NSColor.clearColor())
    panel.setReleasedWhenClosed_(False)
    panel.setHidesOnDeactivate_(False)
    panel.setAlphaValue_(0.0)
    return panel


def _build_label_field(font_size: float) -> NSTextField:
    """Build a rounded, translucent label field used as the panel content."""
    field = NSTextField.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 10.0, 10.0))
    field.setBezeled_(False)
    field.setEditable_(False)
    field.setSelectable_(False)
    field.setDrawsBackground_(True)
    field.setBackgroundColor_(NSColor.windowBackgroundColor().colorWithAlphaComponent_(0.85))
    field.setTextColor_(NSColor.labelColor())
    field.setAlignment_(1)  # NSTextAlignmentCenter
    field.setFont_(NSFont.boldSystemFontOfSize_(font_size))
    field.setWantsLayer_(True)
    layer = field.layer()
    if layer is not None:
        layer.setCornerRadius_(12.0)
        layer.setMasksToBounds_(True)
    return field


class Hud:
    """A brief banner shown on each Space change (one reused panel)."""

    def __init__(self) -> None:
        """Build the reusable non-activating panel (hidden until shown)."""
        self._panel = make_non_activating_panel(240.0, 80.0, NSScreenSaverWindowLevel)
        self._field = _build_label_field(42.0)
        self._panel.setContentView_(self._field)
        self._dismiss_timer: object | None = None
        #: Text currently shown (None when hidden) — lets a caller clear a specific stale
        #: banner (e.g. a switch-failure notice a later confirmed switch supersedes) without
        #: tearing down an unrelated ambient label.
        self._current_text: str | None = None

    @property
    def current_text(self) -> str | None:
        """The text currently displayed, or ``None`` when the panel is hidden/faded."""
        return self._current_text

    @objc.python_method
    def dismiss(self) -> None:
        """Fade the panel out now (cancels any pending auto-dismiss); no-op if hidden."""
        if self._current_text is None:
            return
        self._cancel_dismiss()
        self._current_text = None
        self._fade_to(0.0, _FADE_OUT)
        self._dismiss_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            _FADE_OUT,
            False,
            lambda _t: self._panel.orderOut_(None),
        )

    def show(
        self,
        text: str,
        *,
        duration_ms: int,
        screen: object | None = None,
        position: str = "center",
        margin: int = 24,
        font_size: int | None = None,
    ) -> None:
        """Display ``text`` briefly, then fade out after ``duration_ms``.

        Args:
            text: The label to show.
            duration_ms: Hold time at full opacity before the fade-out.
            screen: Target ``NSScreen``; defaults to the main screen.
            position: One of the nine :data:`~spacelabel.agent.geometry.ANCHORS`.
            margin: Edge inset in points (ignored on a centered axis).
            font_size: Point size for the banner; ``None`` keeps the current font.
                The agent passes the per-display size from
                :func:`~spacelabel.agent.geometry.hud_font_size`.
        """
        target = screen if screen is not None else NSScreen.mainScreen()
        if target is None:
            log.warning("no screen available; HUD not shown")
            return
        self._cancel_dismiss()
        self._current_text = str(text)
        if font_size is not None:
            self._field.setFont_(NSFont.boldSystemFontOfSize_(float(font_size)))
        self._size_to_text(text)
        self._place(target, position, margin)
        self._panel.setAlphaValue_(0.0)
        self._panel.orderFrontRegardless()
        self._fade_to(1.0, _FADE_IN)
        self._schedule_dismiss(max(0, duration_ms) / 1000.0)

    @objc.python_method
    def _size_to_text(self, text: str) -> None:
        """Resize the field + panel to fit ``text`` plus padding."""
        self._field.setStringValue_(str(text))
        fitting = self._field.sizeThatFits_((10_000.0, 10_000.0))
        width = float(fitting.width) + _HUD_PAD_X * 2
        height = float(fitting.height) + _HUD_PAD_Y * 2
        self._panel.setContentSize_((width, height))
        self._field.setFrame_(NSMakeRect(0.0, 0.0, width, height))

    @objc.python_method
    def _place(self, screen: object, position: str, margin: int) -> None:
        """Position the panel via the shared anchor helper on the visible frame."""
        vf = screen.visibleFrame()
        frame = self._panel.frame()
        anchor = position if position in geometry.ANCHORS else "center"
        x, y = geometry.anchor_origin(
            (float(vf.origin.x), float(vf.origin.y), float(vf.size.width), float(vf.size.height)),
            float(frame.size.width),
            float(frame.size.height),
            anchor,
            float(margin),
        )
        self._panel.setFrameOrigin_((x, y))

    @objc.python_method
    def _fade_to(self, alpha: float, duration: float) -> None:
        """Animate the panel's alpha to ``alpha`` over ``duration`` seconds."""
        from AppKit import NSAnimationContext

        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(duration)
        self._panel.animator().setAlphaValue_(alpha)
        NSAnimationContext.endGrouping()

    @objc.python_method
    def _schedule_dismiss(self, hold_seconds: float) -> None:
        """Schedule the fade-out + order-out after the hold time."""
        delay = _FADE_IN + hold_seconds
        self._dismiss_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            delay,
            False,
            self._dismiss_block,
        )

    @objc.python_method
    def _dismiss_block(self, _timer: object) -> None:
        """Fade out and order the panel out after the fade completes."""
        self._current_text = None
        self._fade_to(0.0, _FADE_OUT)
        self._dismiss_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            _FADE_OUT,
            False,
            lambda _t: self._panel.orderOut_(None),
        )

    @objc.python_method
    def _cancel_dismiss(self) -> None:
        """Invalidate any pending dismiss timer (rapid re-show)."""
        if self._dismiss_timer is not None:
            self._dismiss_timer.invalidate()
            self._dismiss_timer = None
