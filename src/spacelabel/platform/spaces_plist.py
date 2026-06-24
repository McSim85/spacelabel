"""Fallback parser for ``~/Library/Preferences/com.apple.spaces.plist`` (DESIGN.md §11).

A **topology/UUID-enumeration fallback only** -- used when the CGS read path raises
:class:`spacelabel.platform.cgs.CGSUnavailableError`. It is cached by ``cfprefsd`` and
flushed only on Space create/delete, so it is **stale for the current Space**;
always read the live current Space via CGS (DECISIONS.md 3.4). Accordingly every
parsed :class:`~spacelabel.model.Space` has ``is_current=False``.

The parser is pure (stdlib only): :func:`parse_spaces_plist` takes an already-loaded
mapping so it is unit-testable without a real plist, and :func:`read_spaces` performs
the file I/O via ``plistlib``.
"""

from __future__ import annotations

import logging
import plistlib
import uuid as uuid_module
from collections.abc import Mapping
from pathlib import Path

from spacelabel.labeling import canonical_uuid
from spacelabel.model import Space

__all__ = ["parse_spaces_plist", "plist_path", "read_spaces"]

log = logging.getLogger(__name__)


def plist_path() -> Path:
    """Return the path to the Spaces preferences plist."""
    return Path.home() / "Library" / "Preferences" / "com.apple.spaces.plist"


def _is_real_uuid(value: object) -> bool:
    """Return True when ``value`` parses as a real UUID string (DECISIONS.md 1.6)."""
    if not value:
        return False
    try:
        uuid_module.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def parse_spaces_plist(
    data: Mapping[str, object],
    *,
    main_display_uuid: str | None = None,
    include_unlabelable: bool = False,
) -> list[Space]:
    """Parse a loaded Spaces plist mapping into Spaces (PURE, DESIGN.md §11).

    Navigates ``data["SpacesDisplayConfiguration"]["Management Data"]["Monitors"]``
    (a list of per-monitor dicts); for each monitor reads ``"Display Identifier"``
    and its ``"Spaces"`` list. Applies the same filter as
    :func:`spacelabel.platform.cgs.parse_spaces`: special Spaces (``type != 0`` or a
    ``TileLayoutManager`` key) are always skipped, and by default only Spaces with a
    real ``uuid`` are kept. With ``include_unlabelable=True`` an ordinary Space whose
    ``uuid`` is missing/non-UUID is also returned (``uuid=""``), so the ordinal count
    matches the live :func:`spacelabel.platform.cgs.parse_spaces` on the CGS-fallback
    path and "Desktop N" stays consistent with the live read (item V). ``is_current``
    is always ``False`` because the plist lags the live current Space (DECISIONS.md 3.4).

    A non-UUID ``"Display Identifier"`` (the literal ``"Main"`` when 'Displays have
    separate Spaces' is off) is remapped to ``main_display_uuid`` when provided, the
    same normalization :func:`spacelabel.platform.cgs.parse_spaces` applies, so the
    fallback path joins against the live NSScreen<->UUID topology (DESIGN.md §3.5).

    Every key access is guarded; a malformed shape (wrong type at any level) is
    logged and yields an empty result rather than raising. Monitor records that
    carry no ``"Spaces"`` key at all (disconnected/historical displays, observed
    on the reference machine) are skipped quietly -- that is a normal shape, not a
    malformed one.
    """
    config = data.get("SpacesDisplayConfiguration")
    if not isinstance(config, Mapping):
        log.warning("spaces plist: SpacesDisplayConfiguration missing or not a mapping")
        return []
    management = config.get("Management Data")
    if not isinstance(management, Mapping):
        log.warning("spaces plist: 'Management Data' missing or not a mapping")
        return []
    monitors = management.get("Monitors")
    if not isinstance(monitors, list):
        log.warning("spaces plist: 'Monitors' missing or not a list")
        return []

    spaces: list[Space] = []
    for monitor in monitors:
        if not isinstance(monitor, Mapping):
            log.warning("spaces plist: monitor entry is not a mapping; skipping")
            continue
        display_identifier = monitor.get("Display Identifier")
        raw_display = str(display_identifier) if display_identifier is not None else ""
        # Canonicalize a real-UUID identifier, and remap the "Main" sentinel / any
        # non-UUID identifier to the primary UUID, so the fallback joins against the
        # live (canonical) topology -- mirrors cgs._normalize_display_identifier
        # (DESIGN.md §3.5).
        if _is_real_uuid(raw_display):
            display_uuid = canonical_uuid(raw_display)
        elif main_display_uuid is not None:
            display_uuid = main_display_uuid
        else:
            display_uuid = raw_display
        if "Spaces" not in monitor:
            # Disconnected/historical monitor records legitimately carry no
            # "Spaces" key (only e.g. "Collapsed Space"); skip them quietly.
            log.debug("spaces plist: no 'Spaces' for display %r; skipping monitor", display_uuid)
            continue
        monitor_spaces = monitor.get("Spaces")
        if not isinstance(monitor_spaces, list):
            log.warning("spaces plist: 'Spaces' is not a list for display %r", display_uuid)
            continue
        for space in monitor_spaces:
            if not isinstance(space, Mapping):
                log.warning("spaces plist: space entry is not a mapping; skipping")
                continue
            raw_uuid = space.get("uuid")
            labelable = _is_real_uuid(raw_uuid)
            if not labelable and not include_unlabelable:
                continue
            # A malformed/changed entry (non-numeric type or id) must be SKIPPED, not
            # crash the best-effort fallback (read_spaces only guards plistlib.load).
            try:
                space_type = int(space.get("type", 0) or 0)
                id64 = int(space.get("id64", space.get("ManagedSpaceID", 0)) or 0)
            except (ValueError, TypeError) as exc:
                log.warning(
                    "spaces plist: skipping malformed space %s on display %r: %s",
                    raw_uuid,
                    display_uuid,
                    exc,
                )
                continue
            # Special Spaces (fullscreen/tiled/system) are never labelable; skip always,
            # mirroring cgs.parse_spaces so the fallback yields the identical Space set.
            if space_type != 0 or "TileLayoutManager" in space:
                continue
            spaces.append(
                Space(
                    uuid=canonical_uuid(str(raw_uuid)) if labelable else "",
                    display_uuid=display_uuid,
                    is_current=False,
                    id64=id64,
                    space_type=space_type,
                    is_fullscreen=False,
                )
            )
    return spaces


def read_spaces(*, include_unlabelable: bool = False) -> list[Space]:
    """Enumerate Spaces (UUID + display) from the plist; never current-Space liveness.

    Loads the plist at :func:`plist_path` and delegates to :func:`parse_spaces_plist`.
    A missing file (``FileNotFoundError``) or a corrupt plist
    (``plistlib.InvalidFileException`` / ``OSError`` / ``ValueError``) is logged at
    WARNING and recovered as an empty list -- this is a best-effort fallback path
    (DECISIONS.md 3.4), never a hard error. ``include_unlabelable`` is forwarded so the
    CGS-fallback ordinal count matches the live read (item V).
    """
    path = plist_path()
    try:
        with path.open("rb") as handle:
            data = plistlib.load(handle)
    except FileNotFoundError:
        log.warning("spaces plist not found at %s; returning no Spaces", path)
        return []
    except (plistlib.InvalidFileException, OSError, ValueError) as exc:
        log.warning("failed to read spaces plist at %s: %s", path, exc)
        return []
    if not isinstance(data, Mapping):
        log.warning("spaces plist at %s did not parse to a mapping", path)
        return []

    # Resolve the primary display UUID to remap the "Main" sentinel; best-effort,
    # since display discovery itself may be unavailable in the fallback scenario.
    main_display_uuid: str | None = None
    try:
        from spacelabel.platform import displays

        main_display_uuid = displays.primary_display_uuid()
    except (ImportError, OSError) as exc:
        log.debug("could not resolve primary display UUID for plist remap: %s", exc)
    return parse_spaces_plist(
        data, main_display_uuid=main_display_uuid, include_unlabelable=include_unlabelable
    )
