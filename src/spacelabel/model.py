"""Domain data model — plain dataclasses shared across layers.

These are the in-memory shapes for a Space, a Display, a stored Label, and the
runtime Config (DESIGN.md §7 defines the on-disk JSON schema). No I/O lives here;
(de)serialization is :mod:`spacelabel.store`'s job.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["Config", "Display", "Label", "Space"]


@dataclass(frozen=True, slots=True)
class Space:
    """A single macOS Space (virtual desktop).

    ``uuid`` is the stable per-Space key we label on (DECISIONS.md 1.4); never
    ``id64``/``ManagedSpaceID``, which are session-scoped and reassignable.
    """

    uuid: str
    display_uuid: str
    is_current: bool = False
    # TODO(phase-4): id64, type, is_fullscreen (TileLayoutManager) — see DESIGN.md §3.4.


@dataclass(frozen=True, slots=True)
class Display:
    """A connected display and its CGS identity (DESIGN.md §4)."""

    uuid: str
    cg_display_id: int
    # TODO(phase-4): origin, size_pt, scale, orientation — see displays.discover_topology.


@dataclass(slots=True)
class Label:
    """A user-assigned label bound to a Space UUID (DESIGN.md §7.1)."""

    text: str
    last_display: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(slots=True)
class Config:
    """Runtime configuration mirroring ``config.json`` (DESIGN.md §7.2)."""

    schema_version: int = 1
    modes: dict[str, bool] = field(default_factory=dict)
    # TODO(phase-4): per-mode settings, debounce_ms, log_level — see DESIGN.md §7.2.
