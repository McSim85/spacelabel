"""Display topology discovery and the NSScreen ↔ CGS display-id mapping (DESIGN.md §4).

Everything is discovered at runtime via ``NSScreen.screens()`` — never hardcode
count, resolution, orientation, scale, or arrangement (portability requirement).
The join key across NSScreen ↔ the Spaces array ↔ the active-menubar display is
the display CFUUID:
``NSScreenNumber → CGDirectDisplayID → CGDisplayCreateUUIDFromDisplayID →
CFUUIDCreateString``. Reach ``CGDisplayCreateUUIDFromDisplayID`` via PyObjC
``Quartz`` (it lives in ColorSync since 10.13, re-exported through the umbrella).
"""

from __future__ import annotations

import logging

from spacelabel.model import Display

__all__ = ["discover_topology", "display_uuid"]

log = logging.getLogger(__name__)


def display_uuid(cg_display_id: int) -> str | None:
    """Return the CFUUID string for a CGDirectDisplayID, or None if unavailable."""
    # TODO(phase-4): Quartz.CGDisplayCreateUUIDFromDisplayID + CFUUIDCreateString.
    raise NotImplementedError


def discover_topology() -> list[Display]:
    """Enumerate connected displays and their CGS identities."""
    # TODO(phase-4): iterate NSScreen.screens(); per screen read NSScreenNumber,
    # frame (origin/size/orientation) and backingScaleFactor; build Display objects.
    # Guard every lookup — never assume a key is present.
    raise NotImplementedError
