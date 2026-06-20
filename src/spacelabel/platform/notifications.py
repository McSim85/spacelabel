"""Space-change and display-change observation with debounce (DESIGN.md §5).

Observe ``NSWorkspaceActiveSpaceDidChangeNotification`` on
``NSWorkspace.sharedWorkspace().notificationCenter()`` (NOT the default center) --
it carries no Space identity, so re-read the UUID on every fire. Observe
``NSApplicationDidChangeScreenParametersNotification`` on the default center to
re-discover topology. Coalesce bursts with a trailing-edge ~200ms debounce; the
debounced callback does the off-main CGS read, then marshals the UI update back
to the main thread.

Notification-center footgun (DECISIONS.md 4.1 / 3.3) -- the center choice is
load-bearing and easy to get wrong:

- ``NSWorkspaceActiveSpaceDidChangeNotification`` is posted ONLY on the per-process
  workspace notification center returned by
  ``NSWorkspace.sharedWorkspace().notificationCenter()``. Registering it on the
  default ``NSNotificationCenter`` silently yields zero events -- the agent would
  look alive yet never update on a Space switch.
- ``NSApplicationDidChangeScreenParametersNotification`` is an app-level event
  posted on the DEFAULT ``NSNotificationCenter``. Registering it on the workspace
  center would, likewise, never fire.

This module subclasses ``NSObject`` at module scope to bridge AppKit notification
selectors and the debounce ``NSTimer`` back to plain Python callables; per the
project rule, importing the framework at module top is allowed here (the module is
only imported by the agent command).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import objc
from AppKit import (
    NSApplicationDidChangeScreenParametersNotification,
    NSWorkspace,
    NSWorkspaceActiveSpaceDidChangeNotification,
)
from Foundation import (
    NSNotificationCenter,
    NSObject,
    NSRunLoop,
    NSRunLoopCommonModes,
    NSTimer,
)

__all__ = ["SpaceObserver"]

log = logging.getLogger(__name__)


class _ObserverBridge(NSObject):
    """ObjC bridge that routes AppKit notifications and the debounce timer.

    AppKit can only target an ``NSObject`` with an ObjC selector, so this thin
    subclass holds the Python callables and the trailing-edge timer. The methods
    invoked as selectors (``spaceDidChange:``, ``displayDidChange:``,
    ``debounceFired:``) keep their bridged signatures; pure-Python helpers are
    marked ``@objc.python_method`` so PyObjC does not expose them as selectors.
    """

    @objc.python_method
    def initialize_bridge(
        self,
        on_space_change: Callable[[], None],
        on_display_change: Callable[[], None],
        debounce_ms: int,
    ) -> _ObserverBridge:
        """Attach the Python callbacks and debounce interval to a fresh bridge.

        Called immediately after ``alloc().init()``. Returns ``self`` so callers
        can chain construction. ``debounce_ms`` is clamped to a non-negative value
        and stored as seconds for ``NSTimer``.
        """
        self._on_space_change = on_space_change
        self._on_display_change = on_display_change
        self._debounce_s = max(0, int(debounce_ms)) / 1000.0
        self._timer: object | None = None
        return self

    def spaceDidChange_(self, _notification: object) -> None:  # noqa: N802
        """Handle ``activeSpaceDidChange`` by (re)arming the trailing-edge timer.

        The notification carries no Space identity, so we never read it here; each
        fire only cancels any pending timer and schedules a new one, coalescing a
        burst of rapid switches into a single read after quiescence.
        """
        self._reschedule_timer()

    def displayDidChange_(self, _notification: object) -> None:  # noqa: N802
        """Handle ``didChangeScreenParameters`` by re-discovering topology promptly.

        Display attach/detach is rare and the consumer rebuilds the screen map, so
        this fires immediately rather than through the debounce timer.
        """
        self._invoke_display_change()

    def debounceFired_(self, _timer: object) -> None:  # noqa: N802
        """Trailing-edge timer callback: clear the timer and run the space read."""
        self._timer = None
        self._invoke_space_change()

    @objc.python_method
    def _reschedule_timer(self) -> None:
        """Invalidate any pending timer and schedule a fresh trailing-edge one.

        The timer is added to the main run loop in the common modes so it still
        fires while a menu is tracking or another modal event loop is running.
        """
        self._cancel_timer()
        timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            self._debounce_s,
            self,
            b"debounceFired:",
            None,
            False,
        )
        NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
        self._timer = timer

    @objc.python_method
    def _cancel_timer(self) -> None:
        """Invalidate and drop the pending debounce timer, if any."""
        timer = self._timer
        if timer is not None:
            timer.invalidate()
            self._timer = None

    @objc.python_method
    def _invoke_space_change(self) -> None:
        """Run the space-change callback, logging and swallowing its exceptions.

        Recovery, not re-raise: this runs on the AppKit main run loop, where an
        unhandled exception would tear down the event loop and kill the agent. A
        failed read must not stop future notifications, so we log with context and
        continue.
        """
        try:
            self._on_space_change()
        except Exception:
            log.exception("on_space_change callback failed; ignoring this fire")

    @objc.python_method
    def _invoke_display_change(self) -> None:
        """Run the display-change callback, logging and swallowing its exceptions.

        Same recovery rationale as ``_invoke_space_change``: keep the run loop and
        future notifications alive across a callback failure.
        """
        try:
            self._on_display_change()
        except Exception:
            log.exception("on_display_change callback failed; ignoring this fire")


class SpaceObserver:
    """Subscribe to space/display changes and invoke debounced callbacks.

    Registers ``NSWorkspaceActiveSpaceDidChangeNotification`` on the workspace
    notification center (debounced, trailing edge) and
    ``NSApplicationDidChangeScreenParametersNotification`` on the default center
    (prompt). The callbacks run on the AppKit main thread; the CGS read happens
    inline in the debounced callback (the ~200ms debounce already prevents thrash --
    an off-main read is a Phase-6 optimization, DESIGN.md §5 / contract).
    """

    def __init__(
        self,
        on_space_change: Callable[[], None],
        on_display_change: Callable[[], None],
        *,
        debounce_ms: int = 200,
    ) -> None:
        """Store the callbacks and the trailing-edge debounce interval."""
        self._on_space_change = on_space_change
        self._on_display_change = on_display_change
        self._debounce_ms = debounce_ms
        self._bridge: _ObserverBridge | None = None

    def start(self) -> None:
        """Register the two notification observers on their respective centers.

        Idempotent: calling ``start`` while already running is a no-op (logged).
        ``activeSpaceDidChange`` goes on the WORKSPACE center, ``didChangeScreen
        Parameters`` on the DEFAULT center -- see the module note on why the center
        choice matters.
        """
        if self._bridge is not None:
            log.debug("SpaceObserver.start called while already running; ignoring")
            return

        bridge = _ObserverBridge.alloc().init()
        bridge = bridge.initialize_bridge(
            self._on_space_change,
            self._on_display_change,
            self._debounce_ms,
        )

        workspace_center = NSWorkspace.sharedWorkspace().notificationCenter()
        workspace_center.addObserver_selector_name_object_(
            bridge,
            b"spaceDidChange:",
            NSWorkspaceActiveSpaceDidChangeNotification,
            None,
        )

        default_center = NSNotificationCenter.defaultCenter()
        default_center.addObserver_selector_name_object_(
            bridge,
            b"displayDidChange:",
            NSApplicationDidChangeScreenParametersNotification,
            None,
        )

        self._bridge = bridge
        log.debug(
            "SpaceObserver started (debounce=%dms; workspace+default centers)",
            self._debounce_ms,
        )

    def stop(self) -> None:
        """Remove all observers and cancel any pending debounce timer.

        Idempotent: safe to call when not running. Removes the bridge from BOTH the
        workspace and default centers and invalidates the trailing-edge timer.
        """
        bridge = self._bridge
        if bridge is None:
            log.debug("SpaceObserver.stop called while not running; ignoring")
            return

        bridge._cancel_timer()
        NSWorkspace.sharedWorkspace().notificationCenter().removeObserver_(bridge)
        NSNotificationCenter.defaultCenter().removeObserver_(bridge)

        self._bridge = None
        log.debug("SpaceObserver stopped (observers removed, timer invalidated)")
