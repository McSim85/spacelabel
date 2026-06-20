"""Pure label-resolution helpers shared by the CLI and the agent UI.

No I/O and no PyObjC — these turn ``Space`` objects plus the UUID→``Label`` store
into the strings every surface displays (menu-bar title, pill text, "Desktop N"
fallback) and identify orphaned labels. Kept pure so they are unit-testable
without a WindowServer (DESIGN.md §12 testing reality).
"""

from __future__ import annotations

import uuid as uuid_module
from collections.abc import Iterable, Mapping

from spacelabel.model import Label, Space

__all__ = [
    "assign_ordinals",
    "canonical_uuid",
    "find_orphans",
    "pill_text",
    "title_for",
    "truncate",
]


def canonical_uuid(value: str) -> str:
    """Normalize a UUID string to the canonical CFUUID spelling (uppercase, hyphenated).

    macOS/CGS keys Spaces and displays by the uppercase CFUUID string. Applying this
    at EVERY boundary -- both where a UUID is stored (labels/display-name writes) and
    where it enters from the live system (the CGS/plist read path, display discovery)
    and is loaded back -- makes ``stored == lookup`` hold in code regardless of what
    spelling the WindowServer emits or what a legacy file holds, so a label/name never
    misses its Space and ``prune`` never mis-orphans it. A value that is not a UUID
    (e.g. the ``"Main"`` sentinel, or an arbitrary key) is returned unchanged.
    """
    try:
        return str(uuid_module.UUID(value)).upper()
    except (ValueError, AttributeError, TypeError):
        return value


def assign_ordinals(spaces: Iterable[Space]) -> dict[int, int]:
    """Map each Space's object identity (``id()``) to its 1-based enumeration position.

    The ordinal is the "Desktop N" number shown when a Space has no label and the
    pill fallback; it mirrors the Mission Control / "Switch to Desktop N" ordering
    (DESIGN.md §6.1, DECISIONS.md 9.5). Ordinals shift on reorder, so callers resolve
    them live and never persist them.

    Keyed by ``id(space)`` (not ``uuid``): with ``include_unlabelable=True`` several
    Spaces share ``uuid=""`` (and some may share a default ``id64``), so a UUID-keyed
    map would collapse them onto one ordinal. Callers look up ``ordinals[id(space)]``
    on the SAME Space objects they enumerated.
    """
    return {id(space): index for index, space in enumerate(spaces, start=1)}


def truncate(text: str, max_length: int) -> str:
    """Truncate ``text`` to ``max_length`` characters with a trailing ellipsis."""
    if max_length <= 0 or len(text) <= max_length:
        return text
    if max_length == 1:
        return "…"
    return text[: max_length - 1] + "…"


def title_for(
    space: Space,
    labels: Mapping[str, Label],
    ordinal: int,
    *,
    max_length: int = 24,
) -> str:
    """Return the menu-bar/HUD title for a Space.

    The stored label (truncated to ``max_length``) if present and non-empty, else
    the ``Desktop {ordinal}`` fallback so the surface is never blank (DESIGN.md §6.1).
    """
    label = labels.get(space.uuid)
    if label is not None and label.text.strip():
        return truncate(label.text, max_length)
    return f"Desktop {ordinal}"


def pill_text(
    space: Space,
    labels: Mapping[str, Label],
    ordinal: int,
    *,
    chars: int = 1,
) -> str:
    """Return the buttons-row pill text: leading letter(s) of the label, else the number.

    ``chars`` (1..2) leading non-space characters of the label when labelled
    (DESIGN.md §6.1 / DECISIONS.md 9.4); the Space ``ordinal`` when unlabelled.
    """
    label = labels.get(space.uuid)
    if label is not None and label.text.strip():
        compact = label.text.strip()
        return compact[: max(1, chars)]
    return str(ordinal)


def find_orphans(labels: Mapping[str, Label], live_uuids: Iterable[str]) -> list[str]:
    """Return stored label UUIDs absent from ``live_uuids``, preserving store order.

    These are the orphans ``label prune`` removes; retained by default until then
    (DECISIONS.md 5.6).
    """
    live = set(live_uuids)
    return [uuid for uuid in labels if uuid not in live]
