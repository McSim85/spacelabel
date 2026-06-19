"""NSApplication accessory app, AppDelegate, and run loop (DESIGN.md §6).

Runs under ``NSApplicationActivationPolicyAccessory`` (set in code — no Dock icon,
no ``LSUIElement`` plist needed for the pipx path) via
``PyObjCTools.AppHelper.runEventLoop()``. The delegate wires the space/display
observer (debounced) to the enabled display modes, all reading the same
UUID→label store.
"""

from __future__ import annotations

import logging
from pathlib import Path

__all__ = ["run_agent"]

log = logging.getLogger(__name__)


def run_agent(config_path: Path | None = None) -> None:
    """Start the menu-bar agent in the foreground (blocks on the AppKit run loop).

    Args:
        config_path: Optional alternate ``config.json`` path; ``None`` uses the
            default under Application Support.
    """
    # TODO(phase-4): configure agent logging, set Accessory activation policy,
    # build the AppDelegate (status item + enabled modes + SpaceObserver), then
    # AppHelper.runEventLoop(). One instance only (DECISIONS.md 6.5).
    raise NotImplementedError
