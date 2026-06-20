"""Display topology discovery and the NSScreen <-> CGS display-id mapping (DESIGN.md §4).

Everything is discovered at runtime via ``NSScreen.screens()`` -- never hardcode
count, resolution, orientation, scale, or arrangement (portability requirement).
The join key across NSScreen <-> the Spaces array <-> the active-menubar display is
the display CFUUID:
``NSScreenNumber -> CGDirectDisplayID -> CGDisplayCreateUUIDFromDisplayID ->
CFUUIDCreateString``. On Tahoe ``CGDisplayCreateUUIDFromDisplayID`` is NOT exposed
by PyObjC's ``Quartz`` and is NOT exported by CoreGraphics; it lives in
``ColorSync.framework`` (and ``ApplicationServices``). We bind it with
``objc.loadBundleFunctions`` (ColorSync, then ApplicationServices), the same
loader pattern as the CGS read path, with ``already_cfretained`` so PyObjC
balances the Create-rule +1 (verified live; DECISIONS.md §3.2).

All PyObjC imports are lazy inside the live functions so pure helpers
(:func:`friendly_name`, :func:`describe`) and the package ``--help`` path never
pull in AppKit/Quartz.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from spacelabel.labeling import canonical_uuid
from spacelabel.model import Display

__all__ = [
    "describe",
    "discover_topology",
    "display_uuid",
    "friendly_name",
    "localized_name",
    "primary_display_uuid",
    "resolved_name",
]

log = logging.getLogger(__name__)

# Resolved once and memoized; None means "tried and unavailable" (do not retry).
_CG_DISPLAY_UUID_FN: Callable[[int], Any] | None = None
_CG_DISPLAY_UUID_LOADED = False


def _resolve_display_uuid_fn() -> Callable[[int], Any] | None:
    """Bind ``CGDisplayCreateUUIDFromDisplayID`` once (ColorSync, then ApplicationServices).

    On Tahoe this symbol is exported by ColorSync.framework (and the
    ApplicationServices umbrella) but NOT by CoreGraphics, and PyObjC's ``Quartz``
    does not expose it -- so it is bound via ``objc.loadBundleFunctions`` like the
    CGS reads. ``already_cfretained`` hands the Create-rule +1 to PyObjC so the
    CFUUID is never manually released. Returns ``None`` (logged) if neither
    framework yields the symbol; the result is cached either way.
    """
    global _CG_DISPLAY_UUID_FN, _CG_DISPLAY_UUID_LOADED
    if _CG_DISPLAY_UUID_LOADED:
        return _CG_DISPLAY_UUID_FN

    import objc

    resolved: dict[str, Any] = {}
    spec = ("CGDisplayCreateUUIDFromDisplayID", b"@I", "", {"retval": {"already_cfretained": True}})
    for framework, bundle_id in (
        ("ColorSync", "com.apple.ColorSync"),
        ("ApplicationServices", "com.apple.ApplicationServices"),
    ):
        try:
            bundle = objc.loadBundle(framework, {}, bundle_identifier=bundle_id)
        except (ImportError, ValueError, RuntimeError) as exc:
            log.debug("could not load %s bundle for display UUID: %s", framework, exc)
            continue
        objc.loadBundleFunctions(bundle, resolved, [spec])
        if "CGDisplayCreateUUIDFromDisplayID" in resolved:
            break

    _CG_DISPLAY_UUID_FN = resolved.get("CGDisplayCreateUUIDFromDisplayID")
    _CG_DISPLAY_UUID_LOADED = True
    if _CG_DISPLAY_UUID_FN is None:
        log.warning(
            "CGDisplayCreateUUIDFromDisplayID unavailable from ColorSync/ApplicationServices; "
            "display UUIDs cannot be resolved"
        )
    return _CG_DISPLAY_UUID_FN


def display_uuid(cg_display_id: int) -> str | None:
    """Return the CFUUID string for a ``CGDirectDisplayID``, or ``None`` if unavailable.

    Resolves ``CGDisplayCreateUUIDFromDisplayID`` (bound from ColorSync, see
    :func:`_resolve_display_uuid_fn`) then ``CoreFoundation.CFUUIDCreateString``.
    Returns ``None`` (logged) when the symbol or the display id has no resolvable
    UUID -- callers must tolerate a missing identity rather than crash (DESIGN.md §4).
    """
    import CoreFoundation

    fn = _resolve_display_uuid_fn()
    if fn is None:
        return None
    cf_uuid = fn(int(cg_display_id))
    if cf_uuid is None:
        log.debug("no CFUUID for CGDirectDisplayID %s", cg_display_id)
        return None
    cf_string = CoreFoundation.CFUUIDCreateString(None, cf_uuid)
    if cf_string is None:
        log.debug("CFUUIDCreateString returned None for CGDirectDisplayID %s", cg_display_id)
        return None
    return canonical_uuid(str(cf_string))


def localized_name(cg_display_id: int) -> str | None:
    """Return the best-effort ``NSScreen.localizedName`` for a display id, else ``None``.

    Scans ``NSScreen.screens()`` for the screen whose ``NSScreenNumber`` matches
    ``cg_display_id`` and returns its ``localizedName``. Returns ``None`` when no
    screen matches or the name is empty (DECISIONS.md 9 open question -- friendly
    name is best-effort).
    """
    from AppKit import NSScreen

    target = int(cg_display_id)
    for screen in NSScreen.screens():
        description = screen.deviceDescription()
        raw_number = description.get("NSScreenNumber") if description is not None else None
        if raw_number is None:
            continue
        if int(raw_number) != target:
            continue
        name = screen.localizedName()
        if not name:
            return None
        return str(name)
    return None


def discover_topology() -> list[Display]:
    """Enumerate connected displays and their CGS identities (DESIGN.md §4).

    Iterates ``NSScreen.screens()``; per screen reads ``NSScreenNumber`` (guarded
    -- never assume the key is present), ``frame`` (origin/size, orientation is
    ``"portrait"`` when height > width), and ``backingScaleFactor``. The display
    UUID and ``localizedName`` are best-effort; a screen with no resolvable
    ``NSScreenNumber`` is skipped with a logged warning. Nothing is cached.
    """
    from AppKit import NSScreen

    displays: list[Display] = []
    for screen in NSScreen.screens():
        description = screen.deviceDescription()
        raw_number = description.get("NSScreenNumber") if description is not None else None
        if raw_number is None:
            log.warning("NSScreen missing NSScreenNumber; skipping screen %r", screen)
            continue
        cg_id = int(raw_number)
        frame = screen.frame()
        width = float(frame.size.width)
        height = float(frame.size.height)
        orientation = "portrait" if height > width else "landscape"
        uuid = display_uuid(cg_id)
        if uuid is None:
            log.warning("no display UUID for CGDirectDisplayID %s; skipping screen", cg_id)
            continue
        name = screen.localizedName()
        displays.append(
            Display(
                uuid=str(uuid),
                cg_display_id=cg_id,
                origin=(float(frame.origin.x), float(frame.origin.y)),
                size_pt=(width, height),
                scale=float(screen.backingScaleFactor()),
                orientation=orientation,
                name=str(name) if name else None,
            )
        )
    return displays


def primary_display_uuid() -> str | None:
    """Return the UUID of the primary display (the screen at origin ``(0, 0)``).

    The primary display is the one whose ``frame.origin`` is the AppKit global
    origin; this is the remap target for the CGS ``"Main"`` sentinel (DESIGN.md
    §3.5). If no screen sits exactly at the origin (e.g. a fractional arrangement),
    fall back to ``NSScreen.screens()[0]`` -- the menu-bar-owning *primary* screen
    per AppKit, NOT ``mainScreen()`` (which is the *active/key* screen and may be a
    secondary display). Returns ``None`` (logged) when no UUID can be resolved.
    """
    from AppKit import NSScreen

    first_cg_id: int | None = None
    for screen in NSScreen.screens():
        description = screen.deviceDescription()
        raw_number = description.get("NSScreenNumber") if description is not None else None
        if raw_number is None:
            continue
        cg_id = int(raw_number)
        if first_cg_id is None:
            first_cg_id = cg_id  # screens()[0] == AppKit primary (menu-bar) display
        frame = screen.frame()
        if float(frame.origin.x) == 0.0 and float(frame.origin.y) == 0.0:
            return display_uuid(cg_id)

    # No screen exactly at the origin: the primary is screens()[0], not the
    # active/key mainScreen() (DESIGN.md §3.5 -- the remap target is the primary).
    if first_cg_id is not None:
        log.info("no display at origin (0,0); using NSScreen.screens()[0] as primary")
        return display_uuid(first_cg_id)
    log.warning("no primary display UUID could be resolved")
    return None


def resolved_name(display: Display, overrides: Mapping[str, str]) -> str:
    """Return a user-assigned display name if set, else :func:`friendly_name` (PURE).

    ``overrides`` is the display-UUID -> custom-name map from
    :func:`spacelabel.store.load_display_labels`.
    """
    custom = overrides.get(display.uuid)
    if custom:
        return custom
    return friendly_name(display)


def friendly_name(display: Display) -> str:
    """Return a human-facing name for a display (PURE).

    Uses ``display.name`` when present, else a short ``Display <8-char-prefix>``
    derived from the UUID (DECISIONS.md 9 -- fall back to a UUID prefix when no
    model name is available).
    """
    if display.name:
        return display.name
    return f"Display {display.uuid[:8]}"


def describe(display: Display) -> str:
    """Return a one-line human description of a display (PURE).

    Format: ``"<friendly name> - <orientation> - <width>x<height>"`` using a
    plain ASCII ``x`` between the point dimensions (no fancy glyph), with the
    dimensions truncated to whole points.
    """
    width = int(display.size_pt[0])
    height = int(display.size_pt[1])
    return f"{friendly_name(display)} - {display.orientation} - {width}x{height}"
