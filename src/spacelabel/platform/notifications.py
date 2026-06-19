"""Space-change and display-change observation with debounce (DESIGN.md §5).

Observe ``NSWorkspaceActiveSpaceDidChangeNotification`` on
``NSWorkspace.sharedWorkspace().notificationCenter()`` (NOT the default center) —
it carries no Space identity, so re-read the UUID on every fire. Observe
``NSApplicationDidChangeScreenParametersNotification`` on the default center to
re-discover topology. Coalesce bursts with a trailing-edge ~200ms debounce; the
debounced callback does the off-main CGS read, then marshals the UI update back
to the main thread.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

__all__ = ["SpaceObserver"]

log = logging.getLogger(__name__)


class SpaceObserver:
    """Subscribe to space/display changes and invoke debounced callbacks."""

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

    def start(self) -> None:
        """Register the two notification observers on their respective centers."""
        # TODO(phase-4): add observers; the workspace one on the workspace center,
        # the screen-parameters one on the default center (DECISIONS.md 4.1 / 3.3).
        raise NotImplementedError

    def stop(self) -> None:
        """Remove all observers and cancel any pending debounce timer."""
        # TODO(phase-4): removeObserver and invalidate the debounce timer.
        raise NotImplementedError
