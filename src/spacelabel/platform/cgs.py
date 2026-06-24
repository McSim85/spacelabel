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
import uuid as uuid_module
from collections.abc import Mapping, Sequence
from typing import Any

from spacelabel.labeling import canonical_uuid
from spacelabel.model import Space

__all__ = [
    "CGSUnavailableError",
    "active_display_uuid",
    "connection",
    "current_space_id",
    "enumerate_spaces",
    "list_spaces",
    "parse_spaces",
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

#: Memoized map of resolved callables, keyed by the canonical ``CGS*`` name.
_NS: dict[str, Any] = {}

#: Memoized framework bundles. CoreGraphics holds the ``CGS*`` re-exports (§0); SkyLight
#: holds the ``SLS*`` implementations and is loaded lazily, only if a ``CGS*`` alias misses
#: (on Tahoe CoreGraphics exports only ``CGS*`` and SkyLight only ``SLS*``, so the SLS
#: fallback MUST target SkyLight, not CoreGraphics -- DECISIONS.md §1.1).
_BUNDLES: dict[str, Any] = {}


class CGSUnavailableError(RuntimeError):
    """A required CGS/SLS symbol could not be resolved (e.g. renamed on a new macOS)."""


def _is_real_uuid(value: object) -> bool:
    """Return True if ``value`` parses as a canonical UUID string.

    The ``"Main"``/header rows carry an empty ``uuid`` and special Spaces can use
    literal non-UUID strings (e.g. ``dashboard``); both must be rejected so only
    labelable user Spaces survive (DESIGN.md §3.4).
    """
    text = str(value).strip()
    if not text:
        return False
    try:
        uuid_module.UUID(text)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _normalize_display_identifier(raw: object, main_display_uuid: str | None) -> str:
    """Normalize a ``"Display Identifier"`` value to a usable display UUID string.

    When *Displays have separate Spaces* is off the identifier is the literal
    ``"Main"`` (and other non-UUID sentinels are possible); remap any
    non-UUID-parseable identifier to ``main_display_uuid`` when known, else fall
    back to the raw string so the value is never lost (DESIGN.md §3.5,
    DECISIONS.md 1.7).
    """
    text = str(raw) if raw is not None else ""
    if _is_real_uuid(text):
        return canonical_uuid(text)  # canonical so the display join is casing-proof
    if main_display_uuid is not None:
        return main_display_uuid
    return text


def parse_spaces(
    managed: Sequence[Mapping[str, object]],
    *,
    current_by_display: Mapping[str, int] | None = None,
    main_display_uuid: str | None = None,
    include_unlabelable: bool = False,
) -> list[Space]:
    """Parse the managed-display-spaces structure into :class:`Space` objects.

    This is the PURE core of the read path (the prime unit-test target). It accepts
    a plain Python list of per-display dicts in tests and the bridged ``NSArray`` of
    ``NSDictionary`` in production; both are accessed via ``.get(...)`` and coerced
    with ``str()``/``int()``.

    Special Spaces (non-zero ``type`` or a ``TileLayoutManager`` key) are ALWAYS
    skipped. By default only **labelable** Spaces are returned (DESIGN.md §3.4 /
    DECISIONS.md 1.6) -- those with a real UUID. With ``include_unlabelable=True``
    an ordinary (``type == 0``) Space whose ``uuid`` is empty/non-UUID is also
    returned, with ``uuid=""`` (macOS has not yet assigned that Space a persistent
    UUID -- e.g. a display's single default Space, marked by a ``wsid`` key) -- BUT only
    when it carries a real session ``id64`` (``!= 0``); a ``uuid=""`` ``id64=0`` row is a
    header/placeholder, not a real desktop, so it is never surfaced (it would otherwise
    fabricate an extra ``Desktop N`` and shift later ordinals).

    ``is_current`` is keyed by **(display, id64)**, not a bare id64: a Space is current
    only when ``current_by_display[its display] == its id64``. Session ``id64`` is
    globally unique for labelable Spaces, but a display's default Space can report a
    low/reused id (``1``), so a bare-id64 set could mark the wrong display's default
    current. (DECISIONS.md 1.5/9.5.)

    Args:
        managed: Per-display dicts as returned by ``CGSCopyManagedDisplaySpaces``.
        current_by_display: Map of display UUID -> that display's live current-Space
            ``id64``; a Space is marked current iff its display's entry equals its id64.
        main_display_uuid: Primary display UUID used to remap the ``"Main"`` sentinel
            and any other non-UUID display identifier (DESIGN.md §3.5).
        include_unlabelable: Also return ordinary Spaces with a real id64 but no real
            UUID (``uuid=""``).

    Returns:
        Spaces in display-then-Space order (labelable only unless
        ``include_unlabelable``).
    """
    current = current_by_display or {}
    spaces: list[Space] = []
    for display in managed:
        raw_identifier = display.get("Display Identifier")
        display_uuid = _normalize_display_identifier(raw_identifier, main_display_uuid)

        raw_spaces = display.get("Spaces") or []
        # Accept a native list/tuple OR a bridged NSArray (Sequence), but never a
        # bare string; bridged NSDictionary/NSArray satisfy the ABC protocols.
        if isinstance(raw_spaces, (str, bytes)) or not isinstance(raw_spaces, Sequence):
            log.warning(
                "Spaces entry for display %s is not a sequence: %r; skipping",
                display_uuid,
                type(raw_spaces).__name__,
            )
            continue

        for space in raw_spaces:
            # A malformed element (non-dict, or non-numeric type/id) must not abort
            # the whole enumeration: log with context and skip just that Space
            # (no-silent-except, DESIGN.md §8.2).
            if not isinstance(space, Mapping):
                log.warning(
                    "skipping non-mapping Space entry on display %s: %r",
                    display_uuid,
                    type(space).__name__,
                )
                continue

            try:
                space_type = int(space.get("type", 0) or 0)
                raw_id64 = space.get("id64")
                if raw_id64 is None:
                    raw_id64 = space.get("ManagedSpaceID", 0)
                id64 = int(raw_id64 or 0)
            except (ValueError, TypeError) as exc:
                log.warning("skipping malformed Space on display %s: %s", display_uuid, exc)
                continue

            # Special Spaces (fullscreen/tiled/system) are never labelable; skip always.
            if space_type != 0 or "TileLayoutManager" in space:
                continue

            raw_uuid = space.get("uuid")
            labelable = _is_real_uuid(raw_uuid)
            # Surface an unlabelable Space only with include_unlabelable AND a real
            # session id64: a uuid="" id64=0 row is a header/placeholder, not a desktop,
            # and counting it would fabricate an extra Desktop N (shifting later ordinals).
            if not labelable and (not include_unlabelable or id64 == 0):
                continue

            spaces.append(
                Space(
                    uuid=canonical_uuid(str(raw_uuid)) if labelable else "",
                    display_uuid=display_uuid,
                    is_current=current.get(display_uuid) == id64,
                    id64=id64,
                    space_type=space_type,
                    is_fullscreen=False,
                )
            )
    return spaces


def _load() -> dict[str, Any]:
    """Resolve the four CGS functions, memoizing into ``_NS``.

    Each symbol is resolved ``CGS``-name-against-**CoreGraphics**, then (on a miss) the
    ``SLS`` name against a **separately-loaded SkyLight** bundle. This is the real
    per-symbol fallback: on Tahoe CoreGraphics exports only the ``CGS*`` re-exports and
    SkyLight only the ``SLS*`` implementations, so trying the ``SLS`` name against
    CoreGraphics (the old code) was a no-op (DECISIONS.md §1.1, improvements.md item H).
    The two ``Copy`` functions carry ``already_retained`` so PyObjC balances the ``+1``
    retain (never also ``CFRelease``). Both bundles are cached in ``_BUNDLES``; SkyLight
    is loaded lazily, only if some ``CGS*`` alias is missing.

    Returns:
        The memoized map of resolved callables, keyed by the canonical ``CGS*`` name.

    Raises:
        CGSUnavailableError: If neither the ``CGS`` name (CoreGraphics) nor the ``SLS``
            name (SkyLight) resolves for any required symbol (e.g. a future-macOS rename).
    """
    if _NS:
        return _NS

    import objc

    resolved: dict[str, Any] = {}

    def _resolve(
        bundle: Any, name: str, canonical: str, sig: bytes, meta: dict[str, object] | None
    ) -> bool:
        """Bind ``name`` from ``bundle`` into ``resolved[canonical]``; True on success.

        ``loadBundleFunctions`` returns ``None`` even when it skips a missing symbol, so
        success is decided by whether ``name`` was actually bound to a real callable --
        not by the return value (else a removed ``CGS`` alias stores ``None`` and the
        ``SLS`` fallback is skipped, failing later as NoneType-not-callable).
        """
        spec = (name, sig) if meta is None else (name, sig, "", meta)
        objc.loadBundleFunctions(bundle, resolved, [spec])
        func = resolved.get(name)
        if func is None:
            return False
        resolved[canonical] = func
        return True

    if "CoreGraphics" not in _BUNDLES:
        try:
            _BUNDLES["CoreGraphics"] = objc.loadBundle(
                "CoreGraphics", {}, bundle_identifier="com.apple.CoreGraphics"
            )
        except Exception as exc:
            # A bundle-load failure must degrade to the plist parser (callers catch
            # CGSUnavailableError), not crash out of the read path.
            raise CGSUnavailableError(f"could not load the CoreGraphics bundle: {exc}") from exc
    core_graphics = _BUNDLES["CoreGraphics"]

    for cgs_name, sls_name, sig, meta in _FUNCS:
        # 1. The CGS* alias against CoreGraphics (the re-export — §0 / DECISIONS 1.1).
        if _resolve(core_graphics, cgs_name, cgs_name, sig, meta):
            continue
        # 2. Fall back to the SLS* implementation against a SEPARATELY-loaded SkyLight
        #    bundle (CoreGraphics has no SLS* exports on Tahoe — see _BUNDLES).
        if "SkyLight" not in _BUNDLES:
            try:
                _BUNDLES["SkyLight"] = objc.loadBundle(
                    "SkyLight", {}, bundle_identifier="com.apple.SkyLight"
                )
            except Exception as exc:
                # SkyLight unavailable too -> the symbol is unresolvable; fall back to
                # the plist parser via CGSUnavailableError rather than letting the raw
                # load error escape the read path.
                raise CGSUnavailableError(
                    f"{cgs_name} missing from CoreGraphics and SkyLight could not be loaded: {exc}"
                ) from exc
        if _resolve(_BUNDLES["SkyLight"], sls_name, cgs_name, sig, meta):
            log.warning("resolved %s via the SLS fallback %s from SkyLight", cgs_name, sls_name)
            continue
        raise CGSUnavailableError(
            f"neither {cgs_name} (CoreGraphics) nor {sls_name} (SkyLight) could be resolved"
        )

    _NS.update(resolved)
    return _NS


def _to_native(value: object) -> object:
    """Deep-convert a bridged NSArray/NSDictionary (or scalar) to native Python.

    Builds plain ``list``/``dict``/scalar so callers never hand a bridged object to
    JSON or to mypy (avoids ``warn_return_any``). Bridged collections are NOT
    Python ``list``/``dict`` (they are ``__NSCFArray``/``__NSDictionaryI``), so
    detection is by the ``Mapping``/``Sequence`` ABCs they satisfy; strings/bytes
    are scalars even though they are Sequences.
    """
    if isinstance(value, Mapping):
        return {str(k): _to_native(v) for k, v in value.items()}
    if isinstance(value, (str, bytes)):
        return value
    if isinstance(value, Sequence):
        return [_to_native(v) for v in value]
    return value


def connection() -> int:
    """Return the process-wide CGS connection id.

    Raises:
        CGSUnavailableError: If the connection symbol cannot be resolved.
    """
    funcs = _load()
    return int(funcs["CGSMainConnectionID"]())


def current_space_id(display_uuid: str) -> int:
    """Return the live current Space id for the given display UUID.

    Uses the live ``CGSManagedDisplayGetCurrentSpace`` call (never the dict's
    ``"Current Space"``, which lags right after a switch -- DECISIONS.md 1.5).

    Args:
        display_uuid: The CFUUID string of the display to query.

    Returns:
        The session-scoped current Space id (``id64``) for that display.

    Raises:
        CGSUnavailableError: If the symbol cannot be resolved.
    """
    funcs = _load()
    conn = int(funcs["CGSMainConnectionID"]())
    return int(funcs["CGSManagedDisplayGetCurrentSpace"](conn, display_uuid))


def active_display_uuid() -> str:
    """Return the UUID of the menu-bar-owning (active) display.

    Prefers ``CGSCopyActiveMenuBarDisplayIdentifier``; on a falsy/missing result
    falls back to ``NSScreen.mainScreen()``'s CFUUID via
    :func:`spacelabel.platform.displays.display_uuid` (DECISIONS.md 1.8).

    Returns:
        The active display's CFUUID string (empty string only if every source is
        unavailable, which is logged).

    Raises:
        CGSUnavailableError: If the CGS symbols cannot be resolved.
    """
    funcs = _load()
    conn = int(funcs["CGSMainConnectionID"]())
    active = funcs["CGSCopyActiveMenuBarDisplayIdentifier"](conn)
    if active:
        active_str = str(active)
        if _is_real_uuid(active_str):
            return canonical_uuid(active_str)
        # Non-UUID identifier (e.g. the literal "Main" when 'Displays have separate
        # Spaces' is off): remap to the primary display UUID through the SAME path as
        # enumerate_spaces so the active-display join still matches (DESIGN.md §3.5,
        # DECISIONS.md 1.7) -- otherwise read_active_space_uuid never finds the
        # current Space and the agent shows no label.
        from spacelabel.platform import displays

        return _normalize_display_identifier(active_str, displays.primary_display_uuid())

    log.info("CGSCopyActiveMenuBarDisplayIdentifier returned falsy; using NSScreen.mainScreen")
    from AppKit import NSScreen

    from spacelabel.platform import displays

    main_screen = NSScreen.mainScreen()
    if main_screen is None:
        log.warning("NSScreen.mainScreen() is None; cannot resolve active display UUID")
        return ""

    cg_id = main_screen.deviceDescription().get("NSScreenNumber")
    if cg_id is None:
        log.warning("mainScreen has no NSScreenNumber; cannot resolve active display UUID")
        return ""

    fallback = displays.display_uuid(int(cg_id))
    if fallback is None:
        log.warning("display_uuid(%s) returned None; cannot resolve active display UUID", cg_id)
        return ""
    return fallback


def list_spaces() -> list[dict[str, object]]:
    """Return the raw managed-display-spaces structure as native Python (debugging).

    Deep-converts the bridged ``NSArray``/``NSDictionary`` from
    ``CGSCopyManagedDisplaySpaces`` so the result is JSON-friendly and free of
    bridged objects (DESIGN.md §3.4).

    Returns:
        One native ``dict`` per managed display.

    Raises:
        CGSUnavailableError: If the symbol cannot be resolved.
    """
    funcs = _load()
    conn = int(funcs["CGSMainConnectionID"]())
    managed = funcs["CGSCopyManagedDisplaySpaces"](conn)
    native = _to_native(managed)
    if not isinstance(native, list):
        # A nil/garbage result is a failed read, not an empty topology (same as
        # enumerate_spaces) -- raise rather than report success with no displays.
        raise CGSUnavailableError(
            f"CGSCopyManagedDisplaySpaces returned no usable data ({type(native).__name__})"
        )
    return [entry for entry in native if isinstance(entry, dict)]


def enumerate_spaces(*, include_unlabelable: bool = False) -> list[Space]:
    """Enumerate Spaces across all displays, marking each display's current one.

    Reads ``CGSCopyManagedDisplaySpaces``, computes each display's live current
    Space id via ``CGSManagedDisplayGetCurrentSpace`` (wrapped per display in
    specific try/except -> log + skip, never silent), then delegates the labelable
    filter to :func:`parse_spaces`. The ``"Main"`` sentinel is remapped via
    :func:`spacelabel.platform.displays.primary_display_uuid` (DESIGN.md §3.4-3.6).

    Args:
        include_unlabelable: When True, also return ordinary Spaces with no
            macOS-assigned UUID (``uuid=""``) so the diagnostic ``spaces`` command
            can surface every display; label/prune/agent paths keep the default
            (labelable only).

    Returns:
        Spaces with ``is_current`` set for the live current Space of each display.

    Raises:
        CGSUnavailableError: If the CGS symbols cannot be resolved.
    """
    from spacelabel.platform import displays

    funcs = _load()
    conn = int(funcs["CGSMainConnectionID"]())

    managed = funcs["CGSCopyManagedDisplaySpaces"](conn)
    native = _to_native(managed)
    if not isinstance(native, list):
        # A nil/garbage result is a FAILED read, not an empty topology -- raise so
        # callers engage the plist fallback / error path instead of reporting "0
        # Spaces" with exit 0 (DESIGN.md §3.4 / CLI.md §3.4).
        raise CGSUnavailableError(
            f"CGSCopyManagedDisplaySpaces returned no usable data ({type(native).__name__})"
        )
    display_dicts: list[dict[str, object]] = [d for d in native if isinstance(d, dict)]

    main_uuid = displays.primary_display_uuid()

    current_by_display: dict[str, int] = {}
    for display in display_dicts:
        identifier = _normalize_display_identifier(display.get("Display Identifier"), main_uuid)
        if not identifier:
            log.debug("skipping current-space read for a display with no identifier")
            continue
        try:
            sid = int(funcs["CGSManagedDisplayGetCurrentSpace"](conn, identifier))
        except (ValueError, TypeError, RuntimeError) as exc:
            log.warning("current-space read failed for display %s: %s; skipping", identifier, exc)
            continue
        if sid:
            # Key per display, not a flat id64 set: a display's default Space can report
            # a low/reused id (e.g. 1), which a flat set would mark current on the wrong
            # display (DECISIONS.md 1.5).
            current_by_display[identifier] = sid

    return parse_spaces(
        display_dicts,
        current_by_display=current_by_display,
        main_display_uuid=main_uuid,
        include_unlabelable=include_unlabelable,
    )


def read_active_space_uuid() -> str | None:
    """Resolve the active display's current Space UUID (the agent's hot path).

    Resolves the active display, reads its live current Space id, then maps that id
    to a Space ``uuid`` via :func:`enumerate_spaces` (DESIGN.md §3.6). Returns the
    UUID string, or ``None`` if no labelable Space on the active display matches the
    live current id.

    Returns:
        The active Space's UUID string, or ``None`` if no match is found.

    Raises:
        CGSUnavailableError: If the CGS symbols cannot be resolved.
    """
    active_uuid = active_display_uuid()
    if not active_uuid:
        log.warning("no active display UUID; cannot resolve active Space UUID")
        return None

    try:
        sid = current_space_id(active_uuid)
    except (ValueError, TypeError, RuntimeError) as exc:
        log.warning("current-space read failed for active display %s: %s", active_uuid, exc)
        return None
    if not sid:
        # A 0 current-space id means "no current Space" for this display; never
        # match it against a Space whose id64 defaulted to 0 (parse_spaces guards
        # the same way before marking is_current).
        log.info("active display %s reports no current Space (id 0)", active_uuid)
        return None

    for space in enumerate_spaces():
        if space.display_uuid == active_uuid and space.id64 == sid:
            return space.uuid

    log.info("no labelable Space on display %s matched live current id %s", active_uuid, sid)
    return None
