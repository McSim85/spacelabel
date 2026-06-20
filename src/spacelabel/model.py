"""Domain data model — plain dataclasses shared across layers.

These are the in-memory shapes for a Space, a Display, a stored Label, and the
runtime Config (DESIGN.md §7 defines the on-disk JSON schema). No I/O lives here;
(de)serialization is :mod:`spacelabel.store`'s job.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "Config",
    "Display",
    "HudConfig",
    "Label",
    "MenubarConfig",
    "OverlayConfig",
    "Space",
    "WallpaperConfig",
    "default_modes",
]


@dataclass(frozen=True, slots=True)
class Space:
    """A single macOS Space (virtual desktop).

    ``uuid`` is the stable per-Space key we label on (DECISIONS.md 1.4); never
    ``id64``/``ManagedSpaceID``, which are session-scoped and reassignable.
    """

    uuid: str
    display_uuid: str
    is_current: bool = False
    #: Session-scoped managed id (``id64``/``ManagedSpaceID``) — used only to
    #: match the live current Space, never as the label key (DECISIONS.md 1.4/1.5).
    id64: int = 0
    #: CGS ``"type"`` — 0 is an ordinary user desktop; non-zero is special.
    space_type: int = 0
    #: Whether the dict carried a ``TileLayoutManager`` key (fullscreen/tiled).
    is_fullscreen: bool = False


@dataclass(frozen=True, slots=True)
class Display:
    """A connected display and its CGS identity (DESIGN.md §4)."""

    uuid: str
    cg_display_id: int
    origin: tuple[float, float] = (0.0, 0.0)
    size_pt: tuple[float, float] = (0.0, 0.0)
    scale: float = 1.0
    orientation: str = "landscape"  # "portrait" | "landscape"
    #: Best-effort human name (e.g. NSScreen ``localizedName``); ``None`` if absent.
    name: str | None = None


@dataclass(slots=True)
class Label:
    """A user-assigned label bound to a Space UUID (DESIGN.md §7.1, DECISIONS.md 9.8).

    Only ``text`` is required; the rest are informational/forward-compatible and
    are never part of the key (the Space ``uuid`` is the sole key).
    """

    text: str
    color: str | None = None
    last_display: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(slots=True)
class MenubarConfig:
    """``config.json`` ``menubar`` block (DESIGN.md §7.2, DECISIONS.md 9.7)."""

    max_length: int = 24
    show_buttons_row: bool = False
    buttons_scope: str = "all_displays"  # "all_displays" | "active_display"
    pill_label_chars: int = 1  # 1..2
    click_to_switch: bool = False


@dataclass(slots=True)
class HudConfig:
    """``config.json`` ``hud`` block (DESIGN.md §7.2 / §9.9, DECISIONS.md 9.7/9.9).

    ``font_size`` is an int point size or the literal ``"auto"`` (compute from the
    display's short side per DESIGN §9.9); ``position`` is one of the nine anchors.
    """

    duration_ms: int = 1100
    font_size: int | str = "auto"
    position: str = "center"
    margin: int = 24


@dataclass(slots=True)
class OverlayConfig:
    """``config.json`` ``overlay`` block (DESIGN.md §7.2 / §9.9, DECISIONS.md 9.7)."""

    corner: str = "top-right"  # one of the nine anchors
    margin: int = 12
    font_size: int | str = 15  # int point size or "auto"
    bold: bool = True  # draw the overlay label (title) bold


@dataclass(slots=True)
class WallpaperConfig:
    """``config.json`` ``wallpaper`` block (cosmetic/best-effort; DESIGN.md §6.4).

    The mode is toggled solely by ``modes.wallpaper``; this block only holds the
    label placement (one of the nine anchors), composited onto the real desktop
    image rather than a blank background.
    """

    position: str = "center"  # one of the nine anchors


def default_modes() -> dict[str, bool]:
    """Return the default per-mode enable map (DESIGN.md §7.2)."""
    return {"menubar": True, "hud": True, "overlay": False, "wallpaper": False}


@dataclass(slots=True)
class Config:
    """Runtime configuration mirroring ``config.json`` (DESIGN.md §7.2)."""

    schema_version: int = 1
    modes: dict[str, bool] = field(default_factory=default_modes)
    menubar: MenubarConfig = field(default_factory=MenubarConfig)
    hud: HudConfig = field(default_factory=HudConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    wallpaper: WallpaperConfig = field(default_factory=WallpaperConfig)
    debounce_ms: int = 200
    log_level: str = "WARNING"
