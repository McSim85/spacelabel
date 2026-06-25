"""Pure HUD/overlay geometry — fonts and the nine-anchor placement grid.

No PyObjC: the panel modules pass in the display's ``visibleFrame`` and point size
and consume these results, so the math is unit-testable without a WindowServer.
Every dimension derives from the current display's geometry — nothing is hardcoded
(portability requirement). AppKit draws text in points, so ``backingScaleFactor``
is never multiplied in here (Retina crispness is free).
"""

from __future__ import annotations

__all__ = [
    "ANCHORS",
    "anchor_origin",
    "clamp",
    "hud_font_size",
    "overlay_font_size",
    "overlay_max_content_extent",
    "overlay_note_font_size",
    "parse_anchor",
    "short_side",
]

#: Floor for the auto-computed overlay notes-body font (points).
_NOTE_FONT_MIN = 9
#: How far below the title the auto notes-body font sits (points).
_NOTE_FONT_STEP = 2

#: The nine valid anchor names (a 3x3 grid) for ``hud.position``/``overlay.corner``.
ANCHORS: frozenset[str] = frozenset(
    {
        "top-left",
        "top-center",
        "top-right",
        "center-left",
        "center",
        "center-right",
        "bottom-left",
        "bottom-center",
        "bottom-right",
    }
)

# anchor name -> (horizontal in {left,center,right}, vertical in {top,middle,bottom})
_ANCHOR_AXES: dict[str, tuple[str, str]] = {
    "top-left": ("left", "top"),
    "top-center": ("center", "top"),
    "top-right": ("right", "top"),
    "center-left": ("left", "middle"),
    "center": ("center", "middle"),
    "center-right": ("right", "middle"),
    "bottom-left": ("left", "bottom"),
    "bottom-center": ("center", "bottom"),
    "bottom-right": ("right", "bottom"),
}


def clamp(value: float, low: float, high: float) -> float:
    """Clamp ``value`` to the inclusive ``[low, high]`` range."""
    return max(low, min(high, value))


def short_side(size_pt: tuple[float, float]) -> float:
    """Return the display's short side in points — keys font size to size, not orientation."""
    return min(size_pt[0], size_pt[1])


def hud_font_size(size_pt: tuple[float, float]) -> int:
    """HUD font in points: ``clamp(round(S*0.05), 18, 64)``."""
    return int(clamp(round(short_side(size_pt) * 0.05), 18, 64))


def overlay_font_size(size_pt: tuple[float, float], configured: int | str) -> int:
    """Overlay font in points.

    A configured int is used verbatim; the literal ``"auto"`` computes
    ``clamp(round(S*0.018), 12, 28)``.
    """
    if isinstance(configured, int):
        return configured
    return int(clamp(round(short_side(size_pt) * 0.018), 12, 28))


def overlay_max_content_extent(available: float, margin: float, pad: float) -> float:
    """Max overlay text extent (points) along one axis so the anchored panel fits.

    Used for BOTH width (``pad`` = horizontal padding) and height (``pad`` = vertical).
    The panel is the content extent plus ``pad`` on each side and is placed up to
    ``margin`` from the anchored edge (:func:`anchor_origin`). Reserving the margin on
    **both** sides (``available - 2*margin - 2*pad``) keeps the panel within
    ``available`` for every anchor — an edge anchor leaves a full margin on the far
    side, a centered one has even more room. Clamped at ``0``: a
    display narrower than the panel's own padding+margin can't be honored, but no real
    display is that small (the padding alone is a couple dozen points).
    """
    return max(0.0, available - 2.0 * margin - 2.0 * pad)


def overlay_note_font_size(title_font: int, configured: int | str) -> int:
    """Overlay notes-body font in points.

    A configured int is used verbatim; the literal ``"auto"`` sits one step
    (``_NOTE_FONT_STEP`` pt) below the resolved ``title_font`` so the task list
    reads as smaller than the bold title, with a small floor.
    """
    if isinstance(configured, int):
        return configured
    return max(_NOTE_FONT_MIN, title_font - _NOTE_FONT_STEP)


def parse_anchor(position: str) -> tuple[str, str]:
    """Split a nine-grid anchor name into ``(horizontal, vertical)`` axes.

    Raises:
        ValueError: if ``position`` is not one of :data:`ANCHORS`.
    """
    try:
        return _ANCHOR_AXES[position]
    except KeyError as exc:
        raise ValueError(f"unknown anchor {position!r}; expected one of {sorted(ANCHORS)}") from exc


def anchor_origin(
    visible_frame: tuple[float, float, float, float],
    width: float,
    height: float,
    position: str,
    margin: float,
) -> tuple[float, float]:
    """Return the bottom-left origin for a ``width`` x ``height`` panel inside ``visible_frame``.

    ``visible_frame`` is ``(vx, vy, vw, vh)`` in AppKit's bottom-left coordinate
    space (use ``NSScreen.visibleFrame`` so the panel clears menu bar/notch/Dock).
    A centered axis ignores ``margin``; edge axes are inset by it.
    """
    vx, vy, vw, vh = visible_frame
    horizontal, vertical = parse_anchor(position)
    x = {
        "left": vx + margin,
        "center": vx + (vw - width) / 2,
        "right": vx + vw - width - margin,
    }[horizontal]
    y = {
        "top": vy + vh - height - margin,
        "middle": vy + (vh - height) / 2,
        "bottom": vy + margin,
    }[vertical]
    return (x, y)
