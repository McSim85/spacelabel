"""CGS read path — the core, highest-risk module (DESIGN.md §3, DECISIONS.md §1).

Reads Spaces and the active Space from the private CoreGraphics-Services API,
read-only and SIP-on. Per the §0 baseline correction we **bind against
CoreGraphics** (which re-exports the symbols under their legacy ``CGS*`` names)
and resolve each symbol ``CGS``-name-then-``SLS``-name, so the binding survives a
point release that drops the ``CGS`` alias. The committed loader is PyObjC's
``objc.loadBundleFunctions`` with ``already_retained`` on the two ``Copy``
functions (DECISIONS.md 1.1-1.3). On total failure raise :class:`CGSUnavailableError`
and let callers fall back to :mod:`spacelabel.platform.spaces_plist`.
"""

from __future__ import annotations

import logging

__all__ = [
    "CGSUnavailableError",
    "active_display_uuid",
    "connection",
    "current_space_id",
    "list_spaces",
    "read_active_space_uuid",
]

log = logging.getLogger(__name__)

# ObjC type encodings used by the committed loader (DESIGN.md §3.3):
#   i = CGSConnectionID (int32)   Q = CGSSpaceID (uint64)   @ = object (auto-bridged)
# already_retained=True hands the Copy() +1 to PyObjC so it releases on GC; never
# also CFRelease manually. Pinned to AltTab-proven arm64 widths (DECISIONS.md 1.3).
_FUNCS: tuple[tuple[str, str, bytes, dict[str, object] | None], ...] = (
    ("CGSMainConnectionID", "SLSMainConnectionID", b"i", None),
    (
        "CGSCopyManagedDisplaySpaces",
        "SLSCopyManagedDisplaySpaces",
        b"@i",
        {"retval": {"already_retained": True}},
    ),
    ("CGSManagedDisplayGetCurrentSpace", "SLSManagedDisplayGetCurrentSpace", b"Qi@", None),
    (
        "CGSCopyActiveMenuBarDisplayIdentifier",
        "SLSCopyActiveMenuBarDisplayIdentifier",
        b"@i",
        {"retval": {"already_retained": True}},
    ),
)


class CGSUnavailableError(RuntimeError):
    """A required CGS/SLS symbol could not be resolved (e.g. renamed on a new macOS)."""


def connection() -> int:
    """Return the process-wide CGS connection id."""
    # TODO(phase-4): lazily load CoreGraphics via objc.loadBundleFunctions (CGS->SLS
    # fallback per symbol; raise CGSUnavailableError on miss) and call CGSMainConnectionID.
    raise NotImplementedError


def list_spaces() -> list[dict[str, object]]:
    """Return the per-display managed-spaces structure (DESIGN.md §3.4)."""
    # TODO(phase-4): CGSCopyManagedDisplaySpaces; remap the "Main" sentinel; skip
    # special Spaces (type != 0 or TileLayoutManager present, or empty uuid).
    raise NotImplementedError


def current_space_id(display_uuid: str) -> int:
    """Return the live current Space id for the given display UUID."""
    # TODO(phase-4): CGSManagedDisplayGetCurrentSpace (live; never trust the dict's
    # "Current Space" for liveness — DECISIONS.md 1.5).
    raise NotImplementedError


def active_display_uuid() -> str:
    """Return the UUID of the menu-bar-owning (active) display."""
    # TODO(phase-4): CGSCopyActiveMenuBarDisplayIdentifier; fall back to
    # NSScreen.mainScreen() CFUUID if the symbol is ever absent (DECISIONS.md 1.8).
    raise NotImplementedError


def read_active_space_uuid() -> str:
    """Resolve the active display's current Space UUID (the agent's hot path)."""
    # TODO(phase-4): active_display_uuid -> current_space_id -> map id64/ManagedSpaceID
    # to uuid via the parsed Spaces list (DESIGN.md §3.6).
    raise NotImplementedError
