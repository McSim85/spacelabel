"""Domain data model — plain dataclasses shared across layers.

These are the in-memory shapes for a Space, a Display, a stored Label, and the
runtime Config. No I/O lives here;
(de)serialization is :mod:`spacelabel.store`'s job.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "AgentState",
    "Config",
    "Display",
    "HudConfig",
    "Label",
    "MenubarConfig",
    "Note",
    "OverlayConfig",
    "Space",
    "default_modes",
]


@dataclass(frozen=True, slots=True)
class Space:
    """A single macOS Space (virtual desktop).

    ``uuid`` is the stable per-Space key we label on; never
    ``id64``/``ManagedSpaceID``, which are session-scoped and reassignable.
    """

    uuid: str
    display_uuid: str
    is_current: bool = False
    #: Session-scoped managed id (``id64``/``ManagedSpaceID``) — used only to
    #: match the live current Space, never as the label key.
    id64: int = 0
    #: CGS ``"type"`` — 0 is an ordinary user desktop; non-zero is special.
    space_type: int = 0
    #: Whether the dict carried a ``TileLayoutManager`` key (fullscreen/tiled).
    is_fullscreen: bool = False


@dataclass(frozen=True, slots=True)
class Display:
    """A connected display and its CGS identity."""

    uuid: str
    cg_display_id: int
    origin: tuple[float, float] = (0.0, 0.0)
    size_pt: tuple[float, float] = (0.0, 0.0)
    scale: float = 1.0
    orientation: str = "landscape"  # "portrait" | "landscape"
    #: Best-effort human name (e.g. NSScreen ``localizedName``); ``None`` if absent.
    name: str | None = None


@dataclass(slots=True)
class Note:
    """One task in a Space's note queue.

    ``text`` is the task line; ``done`` is the checkbox state. Modeled as
    ``{text, done}`` so the state is captured even if an overlay build only draws
    bullets — the overlay renders a glyph reflecting ``done`` (display-only, never
    an interactive control: the panel is click-through).
    """

    text: str
    done: bool = False


@dataclass(slots=True)
class Label:
    """A user-assigned label bound to a Space UUID.

    Only the per-Space ``uuid`` is the key; every field here is value data. ``text``
    may be empty when the entry holds only ``notes`` (a task list on an unlabeled
    Space) — surfaces then fall back to ``Desktop N``. ``color``,
    ``last_display`` and the timestamps are informational/forward-compatible.
    """

    text: str
    color: str | None = None
    last_display: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    #: Per-Space task queue; follows the Space through reorders like the label
    #: (keyed by ``uuid``, never display). Empty when the Space has no tasks.
    notes: list[Note] = field(default_factory=list)


@dataclass(slots=True)
class MenubarConfig:
    """``config.json`` ``menubar`` block."""

    max_length: int = 24
    show_buttons_row: bool = False
    buttons_scope: str = "all_displays"  # "all_displays" | "active_display"
    pill_label_chars: int = 1  # 1..2
    click_to_switch: bool = False


@dataclass(slots=True)
class HudConfig:
    """``config.json`` ``hud`` block.

    ``font_size`` is an int point size or the literal ``"auto"`` (compute from the
    display's short side); ``position`` is one of the nine anchors.
    """

    duration_ms: int = 1100
    font_size: int | str = "auto"
    position: str = "center"
    margin: int = 24


@dataclass(slots=True)
class OverlayConfig:
    """``config.json`` ``overlay`` block."""

    corner: str = "top-right"  # one of the nine anchors
    margin: int = 12
    font_size: int | str = 15  # int point size or "auto"
    bold: bool = True  # draw the overlay label (title) bold
    show_notes: bool = True  # render the per-Space notes list beneath the title
    #: Notes-body point size: an int, or ``"auto"`` = one step below the title
    #: (computed in :mod:`spacelabel.agent.geometry`) so the body reads as smaller.
    note_font_size: int | str = "auto"
    #: Q: when True, suppress the overlay on displays whose current Space has no
    #: user label (shows only a "Desktop N" placeholder without this flag). Default
    #: False so existing behaviour is unchanged on upgrade.
    hide_on_unlabeled: bool = False


def default_modes() -> dict[str, bool]:
    """Return the default per-mode enable map."""
    return {"menubar": True, "hud": True, "overlay": False}


@dataclass(slots=True)
class Config:
    """Runtime configuration mirroring ``config.json``."""

    schema_version: int = 1
    modes: dict[str, bool] = field(default_factory=default_modes)
    menubar: MenubarConfig = field(default_factory=MenubarConfig)
    hud: HudConfig = field(default_factory=HudConfig)
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    debounce_ms: int = 200
    log_level: str = "WARNING"


@dataclass(frozen=True, slots=True)
class AgentState:
    """Persisted agent runtime state for heuristics that span restarts (item L).

    NOT user config — written by the agent itself, never by the CLI/prefs, and not
    watched by the live-reload poll. Currently the Accessibility-grant staleness
    checkpoint: ``last_cdhash`` is the process code-signing hash observed the last
    time Accessibility was confirmed granted, and ``ax_was_trusted`` records that it
    was ever granted. A later failed ``AXIsProcessTrusted`` check uses these to tell
    a *stale* grant (cdhash rotated by an app update) from a
    never-granted one, so the agent can guide REMOVE-and-re-add vs plain "enable".
    """

    last_cdhash: str | None = None
    ax_was_trusted: bool = False
