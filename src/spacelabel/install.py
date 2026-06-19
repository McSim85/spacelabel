"""LaunchAgent install / uninstall via ``launchctl`` (DESIGN.md §9.2).

The agent runs at login in the per-user Aqua GUI domain (``NSStatusItem`` needs
a window-server session). The reverse-DNS id :data:`spacelabel.BUNDLE_ID` is used
verbatim as the LaunchAgent ``Label`` and the plist filename; the canonical
template is ``packaging/dev.mcsim.spacelabel.plist``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from spacelabel import BUNDLE_ID

__all__ = ["LAUNCH_AGENT_LABEL", "install_agent", "is_installed", "plist_path", "uninstall_agent"]

log = logging.getLogger(__name__)

#: launchd Label == plist basename == BUNDLE_ID (single source of truth).
LAUNCH_AGENT_LABEL = BUNDLE_ID


def plist_path() -> Path:
    """Return the per-user LaunchAgents plist path for this agent."""
    return Path.home() / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"


def is_installed() -> bool:
    """Return whether the LaunchAgent plist exists on disk."""
    return plist_path().exists()


def install_agent() -> None:
    """Render the plist with absolute paths and bootstrap it into ``gui/$UID``."""
    # TODO(phase-4): resolve the absolute ~/.local/bin/spacelabel shim, template
    # $HOME into the plist, mkdir -p ~/Library/Logs/spacelabel BEFORE load, then
    # `launchctl bootstrap gui/$(id -u) <plist>`. Ensure exactly one instance.
    raise NotImplementedError


def uninstall_agent() -> None:
    """Bootout the agent from ``gui/$UID`` and remove its plist."""
    # TODO(phase-4): `launchctl bootout gui/$(id -u)/<label>` then unlink the plist.
    raise NotImplementedError
