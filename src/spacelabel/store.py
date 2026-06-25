"""Persistent store -- ``labels.json`` and ``config.json`` under Application Support.

Both files live in ``~/Library/Application Support/spacelabel/``.
All writes are atomic (temp file -> ``fsync`` -> ``os.replace``) and serialized
with an advisory ``fcntl.flock``; the agent watches both files and reloads on
change so a CLI edit is reflected live without a restart.

This module is pure stdlib plus the in-memory model (no PyObjC), which makes it
the prime unit-test target: paths are injectable via :class:`StorePaths`, the
config schema is introspectable via :data:`CONFIG_SCHEMA`, and timestamps accept
an injected value so writes are deterministic in tests.
"""

from __future__ import annotations

import contextlib
import fcntl
import functools
import json
import logging
import os
import tempfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from spacelabel.agent.geometry import ANCHORS
from spacelabel.labeling import canonical_uuid, find_orphans
from spacelabel.model import (
    AgentState,
    Config,
    HudConfig,
    Label,
    MenubarConfig,
    Note,
    OverlayConfig,
    default_modes,
)

__all__ = [
    "CONFIG_SCHEMA",
    "SCHEMA_VERSION",
    "AgentState",
    "ConfigKeyError",
    "ConfigValueError",
    "NoteIndexError",
    "StoreError",
    "StorePaths",
    "add_note",
    "clear_label",
    "clear_note",
    "config_from_dict",
    "config_path",
    "config_to_dict",
    "data_dir",
    "format_scalar",
    "get_config_value",
    "labels_path",
    "load_agent_state",
    "load_config",
    "load_display_labels",
    "load_display_overlay_disabled",
    "load_labels",
    "prune_labels",
    "save_agent_state",
    "save_config",
    "set_config_value",
    "set_display_label",
    "set_display_overlay_enabled",
    "set_label",
    "set_note_done",
]

log = logging.getLogger(__name__)

#: Bumped when the on-disk JSON shape changes; gates migrations.
SCHEMA_VERSION = 1

#: ISO-8601 UTC timestamp format used for ``created_at``/``updated_at``.
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

#: Strings (case-insensitive) accepted as booleans for ``config set`` (CONTRACT).
_BOOL_TRUE = frozenset({"true", "1", "on", "yes"})
_BOOL_FALSE = frozenset({"false", "0", "off", "no"})

#: Valid ``log_level`` values (Config.log_level enum).
_LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")

#: Valid ``menubar.buttons_scope`` values.
_BUTTONS_SCOPES = ("all_displays", "active_display")


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StorePaths:
    """Resolved filesystem locations for the store (injectable for tests).

    ``config_lock``/``labels_lock`` are the advisory-lock files (``<file>.lock``)
    that serialize read-modify-write cycles; ``directory`` is created lazily on
    first write. ``state_file`` holds agent-written runtime state (``AgentState``),
    not user config — it is not exposed to the CLI/prefs nor the live-reload poll.
    """

    directory: Path
    config_file: Path
    labels_file: Path
    displays_file: Path
    state_file: Path
    config_lock: Path
    labels_lock: Path
    displays_lock: Path
    state_lock: Path

    @classmethod
    def default(cls) -> StorePaths:
        """Return the per-user default paths under Application Support."""
        directory = data_dir()
        config_file = directory / "config.json"
        labels_file = directory / "labels.json"
        displays_file = directory / "displays.json"
        state_file = directory / "state.json"
        return cls(
            directory=directory,
            config_file=config_file,
            labels_file=labels_file,
            displays_file=displays_file,
            state_file=state_file,
            config_lock=_lock_for(config_file),
            labels_lock=_lock_for(labels_file),
            displays_lock=_lock_for(displays_file),
            state_lock=_lock_for(state_file),
        )

    @classmethod
    def resolve(cls, config_path: Path | None) -> StorePaths:
        """Resolve paths, honoring a ``--config`` override.

        When ``config_path`` is given the config file is that path, the directory
        is its parent, and ``labels.json`` is derived from the same directory; the
        labels store always lives beside the config (CLI.md note 1). When it is
        ``None`` the platform :meth:`default` paths are used.
        """
        if config_path is None:
            return cls.default()
        config_file = Path(config_path)
        directory = config_file.parent
        labels_file = directory / "labels.json"
        displays_file = directory / "displays.json"
        state_file = directory / "state.json"
        return cls(
            directory=directory,
            config_file=config_file,
            labels_file=labels_file,
            displays_file=displays_file,
            state_file=state_file,
            config_lock=_lock_for(config_file),
            labels_lock=_lock_for(labels_file),
            displays_lock=_lock_for(displays_file),
            state_lock=_lock_for(state_file),
        )


def _lock_for(path: Path) -> Path:
    """Return the advisory-lock path for ``path`` (``<path>.lock``)."""
    return path.with_name(path.name + ".lock")


def data_dir() -> Path:
    """Return the per-user data directory (created lazily on first write)."""
    return Path.home() / "Library" / "Application Support" / "spacelabel"


def labels_path() -> Path:
    """Return the path to ``labels.json`` in the default data directory."""
    return data_dir() / "labels.json"


def config_path() -> Path:
    """Return the path to ``config.json`` in the default data directory."""
    return data_dir() / "config.json"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class StoreError(Exception):
    """Base error for store-layer failures (config validation, I/O contracts)."""


class ConfigKeyError(StoreError):
    """An unknown dotted config key was supplied (CLI maps this to exit 1)."""


class ConfigValueError(StoreError):
    """A config value failed type/enum/range validation (CLI maps this to exit 1)."""


class NoteIndexError(StoreError):
    """A note index was out of range, or the Space has no notes (CLI maps to exit 2).

    Carries ``count`` (the number of stored notes at the time of the locked check)
    so the CLI can render a precise 1-based range in its usage error without an
    unlocked pre-read that could race a concurrent writer or misread a corrupt file.
    """

    def __init__(self, count: int) -> None:
        """Store ``count`` (notes present) and build the human-facing range message."""
        self.count = count
        message = (
            "this Space has no tasks"
            if count == 0
            else f"task index out of range (expected 1..{count})"
        )
        super().__init__(message)


# --------------------------------------------------------------------------- #
# Time + low-level write/lock primitives
# --------------------------------------------------------------------------- #


def _utcnow_iso(timestamp: str | None = None) -> str:
    """Return an ISO-8601 UTC timestamp, honoring an injected ``timestamp``.

    Tests pass a fixed ``timestamp`` for determinism; otherwise the current UTC
    time is formatted as ``YYYY-MM-DDTHH:MM:SSZ``.
    """
    if timestamp is not None:
        return timestamp
    return datetime.now(UTC).strftime(_TIMESTAMP_FORMAT)


@contextlib.contextmanager
def _file_lock(lock_path: Path) -> Iterator[None]:
    """Hold an exclusive advisory ``flock`` on ``lock_path`` for the block.

    The parent directory is created first; the lock file is created if absent.
    The lock is released (and the descriptor closed) on exit, even on error.
    """
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        # Surface as StoreError so callers (CLI) handle it cleanly, not as a leaked
        # OSError traceback (no-silent-except: logged + re-raised as our type).
        log.error("could not acquire lock %s: %s", lock_path, exc)
        raise StoreError(f"could not acquire lock {lock_path}: {exc}") from exc
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _atomic_write_json(target: Path, payload: Mapping[str, object]) -> None:
    """Atomically write ``payload`` as JSON to ``target``.

    Writes to a sibling temp file in the same directory, flushes and ``fsync``s
    it, then ``os.replace``s it over ``target`` so a reader never observes a
    partial file.
    """
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    tmp_path: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
        )
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        # Path.replace wraps os.replace -- atomic rename on the same filesystem.
        tmp_path.replace(target)
    except OSError as exc:
        log.exception("atomic write failed for %s; removing temp file", target)
        if tmp_path is not None:
            with contextlib.suppress(FileNotFoundError):
                tmp_path.unlink()
        # Wrap so the CLI's `except store.StoreError` handles it (clean exit 1),
        # rather than a leaked OSError traceback (mkdir/mkstemp/write/replace).
        raise StoreError(f"could not write {target}: {exc}") from exc


def _read_json(path: Path) -> object | None:
    """Read and parse JSON from ``path``.

    Returns the parsed object, or ``None`` when the file is missing or its
    contents are not valid JSON (logged). Never raises on a corrupt/absent file
    so callers can recover with a sane default.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        log.warning("could not read %s; using defaults", path, exc_info=True)
        return None
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError:
        log.warning("malformed JSON in %s; using defaults", path, exc_info=True)
        return None
    return parsed


def _guard_before_rewrite(path: Path) -> None:
    """Protect a present-but-corrupt file from being silently clobbered by a write.

    A write is a read-modify-write: ``load`` recovers a corrupt/unreadable file to
    an EMPTY default, so without this guard the next write would persist that empty
    state and discard every other entry. Here, before the load:

    * a transient read error (non-``FileNotFoundError`` ``OSError``) raises
      :class:`StoreError` so the write aborts and the file is left intact;
    * a present-but-corrupt JSON file is renamed to ``<file>.corrupt`` (preserving
      the data for recovery) and the write then proceeds from a fresh store.

    A missing or valid file is a no-op.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    except OSError as exc:
        raise StoreError(f"could not read {path} before update: {exc}") from exc
    try:
        json.loads(text)
    except json.JSONDecodeError:
        backup = path.with_name(path.name + ".corrupt")
        log.warning("corrupt JSON in %s; backing up to %s before rewriting", path, backup)
        try:
            path.replace(backup)
        except OSError as exc:
            # Could not preserve it -> abort rather than silently overwrite/lose data.
            raise StoreError(f"could not back up corrupt {path}: {exc}") from exc


# --------------------------------------------------------------------------- #
# Labels
# --------------------------------------------------------------------------- #


def _notes_from_entry(uuid: str, raw: object) -> list[Note]:
    """Parse the optional ``notes`` array of one ``labels.json`` entry (tolerant).

    Each item is a ``{text, done}`` object. A non-list value or
    a malformed item (non-object, missing/empty ``text``) is logged and skipped so a
    hand-edited file never crashes the load; ``done`` defaults to ``False`` when
    absent or non-bool. Order is preserved (the queue order).
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        log.warning("ignoring non-list 'notes' for %s", uuid)
        return []
    notes: list[Note] = []
    for item in raw:
        if not isinstance(item, Mapping):
            log.warning("skipping note for %s: not an object", uuid)
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text:
            log.warning("skipping note for %s: missing/invalid 'text'", uuid)
            continue
        done = item.get("done")
        notes.append(Note(text=text, done=done if isinstance(done, bool) else False))
    return notes


def _label_from_entry(uuid: str, entry: object) -> Label | None:
    """Build a :class:`Label` from one ``labels.json`` entry, or ``None`` if empty.

    The on-disk key is ``label``; ``color``/``last_display``, the
    timestamps and ``notes`` are optional and forward-compatible. A non-dict entry
    is skipped (logged). A **notes-only** entry (empty/absent ``label`` but with at
    least one note) is valid — a task list on an unlabeled Space;
    surfaces fall back to ``Desktop N``. An entry with neither a label nor any note
    carries nothing and is skipped, never crashing the load.
    """
    if not isinstance(entry, Mapping):
        log.warning("skipping label entry for %s: not an object", uuid)
        return None
    raw_text = entry.get("label")
    text = raw_text if isinstance(raw_text, str) else ""
    notes = _notes_from_entry(uuid, entry.get("notes"))
    if not text and not notes:
        # Recover-don't-crash (DESIGN §8.2): an entry that yields neither a usable
        # label nor any usable note carries nothing renderable, so it is skipped
        # (logged), exactly as a malformed `label` is. Valid notes are always kept
        # (see _notes_from_entry — only individually-unusable items are dropped),
        # so this never discards a recoverable task list, only unparseable data.
        log.warning("skipping entry for %s: no usable 'label' text and no usable 'notes'", uuid)
        return None
    return Label(
        text=text,
        color=_opt_str(entry.get("color")),
        last_display=_opt_str(entry.get("last_display")),
        created_at=_opt_str(entry.get("created_at")),
        updated_at=_opt_str(entry.get("updated_at")),
        notes=notes,
    )


def _opt_str(value: object) -> str | None:
    """Return ``value`` as a ``str`` if it is one, else ``None`` (tolerant read)."""
    return value if isinstance(value, str) else None


def load_labels(paths: StorePaths) -> dict[str, Label]:
    """Load the UUID->label store, returning an empty mapping if absent or corrupt.

    Reads ``labels_file``; a missing file yields ``{}``. A corrupt file or one
    with the wrong top-level shape is logged and recovered as ``{}`` (no raise).
    Each well-formed entry is mapped to a :class:`Label`; the insertion order of
    the JSON object is preserved.
    """
    data = _read_json(paths.labels_file)
    if data is None:
        return {}
    if not isinstance(data, Mapping):
        log.warning("labels file %s is not a JSON object; ignoring", paths.labels_file)
        return {}
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        log.warning(
            "labels file %s schema_version %r != %d; reading best-effort",
            paths.labels_file,
            version,
            SCHEMA_VERSION,
        )
    raw_labels = data.get("labels")
    if not isinstance(raw_labels, Mapping):
        log.warning("labels file %s has no 'labels' object; ignoring", paths.labels_file)
        return {}
    result: dict[str, Label] = {}
    for uuid, entry in raw_labels.items():
        if not isinstance(uuid, str):
            log.warning("skipping non-string label key %r", uuid)
            continue
        label = _label_from_entry(uuid, entry)
        if label is not None:
            # Canonicalize on read too, so a legacy non-canonical key still matches
            # the (canonical) live CGS uuid and is not mis-pruned (read==write==live).
            result[canonical_uuid(uuid)] = label
    return result


def _labels_to_payload(labels: Mapping[str, Label]) -> dict[str, object]:
    """Serialize the in-memory label map to the ``labels.json`` payload shape.

    Optional fields (``color``/``last_display``/timestamps/``notes``) are omitted
    when empty so the file stays minimal. ``label`` itself is
    omitted when the text is empty (a notes-only entry).
    """
    entries: dict[str, object] = {}
    for uuid, label in labels.items():
        entry: dict[str, object] = {}
        if label.text:
            entry["label"] = label.text
        if label.color is not None:
            entry["color"] = label.color
        if label.last_display is not None:
            entry["last_display"] = label.last_display
        if label.created_at is not None:
            entry["created_at"] = label.created_at
        if label.updated_at is not None:
            entry["updated_at"] = label.updated_at
        if label.notes:
            entry["notes"] = [{"text": note.text, "done": note.done} for note in label.notes]
        entries[uuid] = entry
    return {"schema_version": SCHEMA_VERSION, "labels": entries}


def set_label(
    paths: StorePaths,
    uuid: str,
    text: str,
    *,
    last_display: str | None = None,
    color: str | None = None,
    timestamp: str | None = None,
) -> Label:
    """Set or replace a single label via locked read-modify-write + atomic replace.

    Preserves an existing ``created_at`` (stamping it for a new label) and always
    refreshes ``updated_at`` to now. ``last_display``/``color`` override the stored
    values only when the argument is not ``None``; otherwise the existing value is
    kept. Returns the stored :class:`Label`.
    """
    uuid = canonical_uuid(uuid)
    now = _utcnow_iso(timestamp)
    with _file_lock(paths.labels_lock):
        _guard_before_rewrite(paths.labels_file)
        labels = load_labels(paths)
        existing = labels.get(uuid)
        created_at = existing.created_at if existing is not None else now
        if created_at is None:
            created_at = now
        new_color = color if color is not None else (existing.color if existing else None)
        new_last_display = (
            last_display
            if last_display is not None
            else (existing.last_display if existing else None)
        )
        label = Label(
            text=text,
            color=new_color,
            last_display=new_last_display,
            created_at=created_at,
            updated_at=now,
            notes=existing.notes if existing is not None else [],
        )
        labels[uuid] = label
        _atomic_write_json(paths.labels_file, _labels_to_payload(labels))
    return label


def clear_label(paths: StorePaths, uuid: str, *, timestamp: str | None = None) -> bool:
    """Clear the label text for ``uuid`` via locked RMW; return ``True`` if one existed.

    If the Space also has notes, the entry is **kept** as a notes-only entry — the
    task queue must survive clearing the label, so a `label clear`
    never silently discards a task list; the entry is removed entirely when it has no
    notes. On demotion the label-only attributes are dropped — ``color`` is cleared
    (it is per-label, 9.8, and a notes-only Space is unlabeled everywhere else, so a
    later `label set` must not inherit a stale color) and ``updated_at`` is refreshed.
    Idempotent: returns ``False`` when there was no label text to clear.
    """
    uuid = canonical_uuid(uuid)
    with _file_lock(paths.labels_lock):
        _guard_before_rewrite(paths.labels_file)
        labels = load_labels(paths)
        existing = labels.get(uuid)
        if existing is None or not existing.text:
            return False
        if existing.notes:
            existing.text = ""  # demote to notes-only, keep the queue
            existing.color = None  # color is a per-label tag; the entry is now unlabeled
            existing.updated_at = _utcnow_iso(timestamp)
        else:
            del labels[uuid]
        _atomic_write_json(paths.labels_file, _labels_to_payload(labels))
    return True


def prune_labels(
    paths: StorePaths, live_uuids: set[str], *, timestamp: str | None = None
) -> list[str]:
    """Prune orphan **labels** (UUIDs absent from ``live_uuids``); return the pruned UUIDs.

    Locked read-modify-write (retain by default, explicit prune
    for removal). ``label prune`` removes *labels*, not *notes*: an orphan that still
    carries a task queue is **demoted to a notes-only entry** (label text + color
    dropped, ``updated_at`` bumped) rather than deleted, so a maintenance prune never
    silently destroys a Space's tasks; an orphan with no notes is
    deleted entirely. A notes-only orphan (no label to prune) is left untouched and
    not reported. Returns the orphan UUIDs whose label was pruned, in store order.
    """
    with _file_lock(paths.labels_lock):
        _guard_before_rewrite(paths.labels_file)
        labels = load_labels(paths)
        orphans = find_orphans(labels, live_uuids)
        pruned: list[str] = []
        for uuid in orphans:
            entry = labels[uuid]
            if not entry.text:
                continue  # notes-only orphan: no label to prune, keep its tasks
            if entry.notes:
                entry.text = ""  # demote: drop the label, preserve the task queue
                entry.color = None
                entry.updated_at = _utcnow_iso(timestamp)
            else:
                del labels[uuid]
            pruned.append(uuid)
        if not pruned:
            return []
        _atomic_write_json(paths.labels_file, _labels_to_payload(labels))
    return pruned


# --------------------------------------------------------------------------- #
# Notes (per-Space task queue, stored on the label entry)
# --------------------------------------------------------------------------- #


def add_note(paths: StorePaths, uuid: str, text: str, *, timestamp: str | None = None) -> Label:
    """Append a task to ``uuid``'s note queue via locked RMW; return the stored Label.

    Creates a notes-only entry when the Space has no label yet.
    The existing label text/color/``created_at`` are preserved and ``updated_at`` is
    refreshed. The new task starts ``done=False``.
    """
    uuid = canonical_uuid(uuid)
    now = _utcnow_iso(timestamp)
    with _file_lock(paths.labels_lock):
        _guard_before_rewrite(paths.labels_file)
        labels = load_labels(paths)
        existing = labels.get(uuid)
        if existing is None:
            existing = Label(text="", created_at=now, updated_at=now)
            labels[uuid] = existing
        existing.notes.append(Note(text=text))
        existing.updated_at = now
        if existing.created_at is None:
            existing.created_at = now
        _atomic_write_json(paths.labels_file, _labels_to_payload(labels))
    return existing


def set_note_done(
    paths: StorePaths, uuid: str, index: int, done: bool, *, timestamp: str | None = None
) -> Note:
    """Set the ``done`` state of note ``index`` (0-based) via locked RMW; return it.

    The range check runs **inside the lock** against the authoritative on-disk count,
    so a concurrent edit can't make a validated index stale and a corrupt store
    surfaces as a write/read failure (``StoreError``), never a spurious bad-index.

    Raises:
        NoteIndexError: if ``uuid`` has no entry or ``index`` is out of range.
    """
    uuid = canonical_uuid(uuid)
    now = _utcnow_iso(timestamp)
    with _file_lock(paths.labels_lock):
        _guard_before_rewrite(paths.labels_file)
        labels = load_labels(paths)
        existing = labels.get(uuid)
        count = len(existing.notes) if existing is not None else 0
        if existing is None or not 0 <= index < count:
            raise NoteIndexError(count)
        existing.notes[index].done = done
        existing.updated_at = now
        _atomic_write_json(paths.labels_file, _labels_to_payload(labels))
        return existing.notes[index]


def clear_note(
    paths: StorePaths, uuid: str, index: int | None = None, *, timestamp: str | None = None
) -> int:
    """Remove one note (0-based ``index``) or all notes (``index=None``); return the count.

    Done via locked RMW. ``index=None`` (clear all) is **idempotent**: a missing
    entry or empty queue removes nothing and returns ``0`` (no raise), so a concurrent
    clear can't turn the command into an error. A specific ``index`` is validated
    inside the lock. When the entry is left with neither a label nor any note it is
    removed entirely (mirrors :func:`clear_label`); otherwise ``updated_at`` is bumped.

    Raises:
        NoteIndexError: if ``index`` is given and ``uuid`` has no entry / it is out of range.
    """
    uuid = canonical_uuid(uuid)
    now = _utcnow_iso(timestamp)
    with _file_lock(paths.labels_lock):
        _guard_before_rewrite(paths.labels_file)
        labels = load_labels(paths)
        existing = labels.get(uuid)
        if index is None:
            if existing is None or not existing.notes:
                return 0  # idempotent: nothing to clear
            removed = len(existing.notes)
            existing.notes = []
        else:
            count = len(existing.notes) if existing is not None else 0
            if existing is None or not 0 <= index < count:
                raise NoteIndexError(count)
            del existing.notes[index]
            removed = 1
        # Both surviving paths leave `existing` as a real entry.
        if not existing.text and not existing.notes:
            del labels[uuid]  # nothing left to store
        else:
            existing.updated_at = now
        _atomic_write_json(paths.labels_file, _labels_to_payload(labels))
    return removed


# --------------------------------------------------------------------------- #
# Display labels (custom per-display names, keyed by display UUID)
# --------------------------------------------------------------------------- #


def _load_displays_raw(paths: StorePaths) -> tuple[dict[str, str], set[str]]:
    """Load the full ``displays.json`` payload; recover silently on errors.

    Returns ``(names, overlay_disabled)`` where ``names`` maps display UUID to a
    custom name and ``overlay_disabled`` is the set of display UUIDs whose corner
    overlay is turned off (P feature). Missing keys recover to empty containers;
    both UUIDs are canonicalized on read so ``_update_overlays`` lookups match.
    """
    data = _read_json(paths.displays_file)
    if data is None:
        return {}, set()
    if not isinstance(data, Mapping):
        log.warning("displays file %s is not a JSON object; ignoring", paths.displays_file)
        return {}, set()
    # Names dict (existing field)
    names: dict[str, str] = {}
    raw_names = data.get("displays")
    if isinstance(raw_names, Mapping):
        for uuid, name in raw_names.items():
            if isinstance(uuid, str) and isinstance(name, str) and name:
                names[canonical_uuid(uuid)] = name
    # Per-display overlay-disabled set (new field; missing → all enabled)
    overlay_disabled: set[str] = set()
    raw_disabled = data.get("overlay_disabled")
    if isinstance(raw_disabled, list):
        for uuid in raw_disabled:
            if isinstance(uuid, str) and uuid:
                overlay_disabled.add(canonical_uuid(uuid))
    return names, overlay_disabled


def _write_displays(paths: StorePaths, names: dict[str, str], overlay_disabled: set[str]) -> None:
    """Atomically write the full ``displays.json`` payload (names + overlay_disabled).

    Both writers — :func:`set_display_label` and :func:`set_display_overlay_enabled`
    — call this so neither clobbers the other's slice of the file.
    """
    payload: dict[str, object] = {"schema_version": SCHEMA_VERSION, "displays": names}
    if overlay_disabled:  # omit the key when empty (backward-compatible reads)
        payload["overlay_disabled"] = sorted(overlay_disabled)
    _atomic_write_json(paths.displays_file, payload)


def load_display_labels(paths: StorePaths) -> dict[str, str]:
    """Load custom display names (display UUID -> name); ``{}`` if absent/corrupt.

    Stored separately from labels in ``displays.json`` so the labels read-modify-
    write never has to preserve it. Missing/corrupt files recover as ``{}``.
    """
    names, _ = _load_displays_raw(paths)
    return names


def load_display_overlay_disabled(paths: StorePaths) -> set[str]:
    """Load the set of display UUIDs whose corner overlay is disabled (P feature).

    Returns an empty set (all displays enabled) when the file is absent or the
    ``overlay_disabled`` key is missing — so the default is overlay-on everywhere.
    Never raises; missing/corrupt data recovers to the empty set.
    """
    _, overlay_disabled = _load_displays_raw(paths)
    return overlay_disabled


def set_display_label(paths: StorePaths, display_uuid: str, name: str) -> None:
    """Set (or, when ``name`` is empty, clear) a custom name for a display UUID.

    Locked read-modify-write + atomic replace, mirroring the label store.
    Preserves the ``overlay_disabled`` slice so the two writers don't clobber each other.
    """
    display_uuid = canonical_uuid(display_uuid)
    with _file_lock(paths.displays_lock):
        _guard_before_rewrite(paths.displays_file)
        names, overlay_disabled = _load_displays_raw(paths)
        if name:
            names[display_uuid] = name
        else:
            names.pop(display_uuid, None)
        _write_displays(paths, names, overlay_disabled)


def set_display_overlay_enabled(paths: StorePaths, display_uuid: str, enabled: bool) -> None:
    """Enable or disable the corner overlay for a specific display (P feature).

    Locked read-modify-write. When ``enabled`` is True the display UUID is removed
    from ``overlay_disabled`` (default state); when False it is added.
    """
    display_uuid = canonical_uuid(display_uuid)
    with _file_lock(paths.displays_lock):
        _guard_before_rewrite(paths.displays_file)
        names, overlay_disabled = _load_displays_raw(paths)
        if enabled:
            overlay_disabled.discard(display_uuid)
        else:
            overlay_disabled.add(display_uuid)
        _write_displays(paths, names, overlay_disabled)


# --------------------------------------------------------------------------- #
# Agent runtime state (state.json — agent-written, not user config)
# --------------------------------------------------------------------------- #


def load_agent_state(paths: StorePaths) -> AgentState:
    """Load persisted agent runtime state; defaults if absent/corrupt (never raises).

    Mirrors :func:`load_display_labels`: a missing or malformed ``state.json``
    recovers to a default :class:`~spacelabel.model.AgentState`, and any field with
    the wrong type is dropped to its default — the state is only a regenerable
    heuristic checkpoint (item L), so a bad file must never crash the agent.
    """
    data = _read_json(paths.state_file)
    if not isinstance(data, Mapping):
        return AgentState()
    last = data.get("last_cdhash")
    trusted = data.get("ax_was_trusted")
    return AgentState(
        last_cdhash=last if isinstance(last, str) else None,
        ax_was_trusted=trusted if isinstance(trusted, bool) else False,
    )


def save_agent_state(paths: StorePaths, state: AgentState) -> None:
    """Persist agent runtime state atomically under a flock.

    A full overwrite of the two regenerable fields (not a read-modify-write), so —
    unlike the label/display stores — there are no sibling keys to preserve and no
    ``_guard_before_rewrite`` is needed: a corrupt ``state.json`` simply recovers to
    defaults on the next :func:`load_agent_state` and is replaced cleanly here.
    """
    with _file_lock(paths.state_lock):
        _atomic_write_json(
            paths.state_file,
            {
                "schema_version": SCHEMA_VERSION,
                "last_cdhash": state.last_cdhash,
                "ax_was_trusted": state.ax_was_trusted,
            },
        )


# --------------------------------------------------------------------------- #
# Config schema
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ConfigField:
    """One validatable config key.

    ``parse`` turns a raw ``config set`` string into the typed value (raising
    :class:`ConfigValueError` on bad input); ``getter``/``setter`` read and write
    the value on a :class:`Config`. ``description`` documents the constraint.
    """

    key: str
    parse: Callable[[str], object]
    getter: Callable[[Config], object]
    setter: Callable[[Config, object], None]
    description: str


def _parse_bool(key: str, raw: str) -> bool:
    """Parse a boolean ``config set`` value (true/false/1/0/on/off/yes/no)."""
    lowered = raw.strip().lower()
    if lowered in _BOOL_TRUE:
        return True
    if lowered in _BOOL_FALSE:
        return False
    raise ConfigValueError(
        f"{key} must be a boolean (one of true,false,1,0,on,off,yes,no); got {raw!r}"
    )


def _parse_int(key: str, raw: str, *, minimum: int) -> int:
    """Parse an integer ``config set`` value with an inclusive lower bound."""
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ConfigValueError(f"{key} must be an integer; got {raw!r}") from exc
    if value < minimum:
        raise ConfigValueError(f"{key} must be >= {minimum}; got {value}")
    return value


def _parse_int_range(key: str, raw: str, *, low: int, high: int) -> int:
    """Parse an integer ``config set`` value within an inclusive ``[low, high]`` range."""
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ConfigValueError(f"{key} must be an integer; got {raw!r}") from exc
    if not low <= value <= high:
        raise ConfigValueError(f"{key} must be in {low}..{high}; got {value}")
    return value


def _parse_int_or_auto(key: str, raw: str, *, minimum: int) -> int | str:
    """Parse an integer (``>= minimum``) or the literal ``"auto"`` font-size value."""
    if raw.strip().lower() == "auto":
        return "auto"
    return _parse_int(key, raw, minimum=minimum)


def _parse_enum(key: str, raw: str, choices: tuple[str, ...]) -> str:
    """Parse a value constrained to a fixed set of ``choices`` (case-sensitive)."""
    value = raw.strip()
    if value not in choices:
        raise ConfigValueError(f"{key} must be one of {','.join(choices)}; got {raw!r}")
    return value


def _parse_anchor(key: str, raw: str) -> str:
    """Parse a nine-grid anchor value validated against :data:`geometry.ANCHORS`."""
    value = raw.strip()
    if value not in ANCHORS:
        choices = ",".join(sorted(ANCHORS))
        raise ConfigValueError(f"{key} must be one of {choices}; got {raw!r}")
    return value


def _mode_setter(name: str) -> Callable[[Config, object], None]:
    """Return a setter that writes ``config.modes[name]`` (coercing to bool)."""

    def setter(config: Config, value: object) -> None:
        config.modes[name] = bool(value)

    return setter


def _mode_getter(name: str) -> Callable[[Config], object]:
    """Return a getter for ``config.modes[name]`` (default ``False`` if absent)."""

    def getter(config: Config) -> object:
        return config.modes.get(name, False)

    return getter


def _build_config_schema() -> dict[str, ConfigField]:
    """Build the dotted-key -> :class:`ConfigField` validation table (CONTRACT)."""
    fields: list[ConfigField] = []

    for mode in ("menubar", "hud", "overlay"):
        key = f"modes.{mode}"
        fields.append(
            ConfigField(
                key=key,
                parse=functools.partial(_parse_bool, key),
                getter=_mode_getter(mode),
                setter=_mode_setter(mode),
                description="bool",
            )
        )

    def add(
        key: str,
        parse: Callable[[str], object],
        getter: Callable[[Config], object],
        setter: Callable[[Config, object], None],
        description: str,
    ) -> None:
        fields.append(
            ConfigField(key=key, parse=parse, getter=getter, setter=setter, description=description)
        )

    # ``parse`` already returns the correctly typed value, so setters assign it
    # verbatim; ``setattr`` accepts an ``object`` value cleanly under mypy strict.
    add(
        "menubar.max_length",
        lambda raw: _parse_int("menubar.max_length", raw, minimum=1),
        lambda c: c.menubar.max_length,
        lambda c, v: setattr(c.menubar, "max_length", v),
        "int >= 1",
    )
    add(
        "menubar.show_buttons_row",
        lambda raw: _parse_bool("menubar.show_buttons_row", raw),
        lambda c: c.menubar.show_buttons_row,
        lambda c, v: setattr(c.menubar, "show_buttons_row", v),
        "bool",
    )
    add(
        "menubar.buttons_scope",
        lambda raw: _parse_enum("menubar.buttons_scope", raw, _BUTTONS_SCOPES),
        lambda c: c.menubar.buttons_scope,
        lambda c, v: setattr(c.menubar, "buttons_scope", v),
        "enum {all_displays, active_display}",
    )
    add(
        "menubar.pill_label_chars",
        lambda raw: _parse_int_range("menubar.pill_label_chars", raw, low=1, high=2),
        lambda c: c.menubar.pill_label_chars,
        lambda c, v: setattr(c.menubar, "pill_label_chars", v),
        "int in 1..2",
    )
    add(
        "menubar.click_to_switch",
        lambda raw: _parse_bool("menubar.click_to_switch", raw),
        lambda c: c.menubar.click_to_switch,
        lambda c, v: setattr(c.menubar, "click_to_switch", v),
        "bool",
    )
    add(
        "hud.duration_ms",
        lambda raw: _parse_int("hud.duration_ms", raw, minimum=0),
        lambda c: c.hud.duration_ms,
        lambda c, v: setattr(c.hud, "duration_ms", v),
        "int >= 0",
    )
    add(
        "hud.font_size",
        lambda raw: _parse_int_or_auto("hud.font_size", raw, minimum=1),
        lambda c: c.hud.font_size,
        lambda c, v: setattr(c.hud, "font_size", v),
        'int >= 1 or "auto"',
    )
    add(
        "hud.position",
        lambda raw: _parse_anchor("hud.position", raw),
        lambda c: c.hud.position,
        lambda c, v: setattr(c.hud, "position", v),
        "one of the nine anchors",
    )
    add(
        "hud.margin",
        lambda raw: _parse_int("hud.margin", raw, minimum=0),
        lambda c: c.hud.margin,
        lambda c, v: setattr(c.hud, "margin", v),
        "int >= 0",
    )
    add(
        "overlay.corner",
        lambda raw: _parse_anchor("overlay.corner", raw),
        lambda c: c.overlay.corner,
        lambda c, v: setattr(c.overlay, "corner", v),
        "one of the nine anchors",
    )
    add(
        "overlay.margin",
        lambda raw: _parse_int("overlay.margin", raw, minimum=0),
        lambda c: c.overlay.margin,
        lambda c, v: setattr(c.overlay, "margin", v),
        "int >= 0",
    )
    add(
        "overlay.font_size",
        lambda raw: _parse_int_or_auto("overlay.font_size", raw, minimum=1),
        lambda c: c.overlay.font_size,
        lambda c, v: setattr(c.overlay, "font_size", v),
        'int >= 1 or "auto"',
    )
    add(
        "overlay.bold",
        lambda raw: _parse_bool("overlay.bold", raw),
        lambda c: c.overlay.bold,
        lambda c, v: setattr(c.overlay, "bold", v),
        "bool",
    )
    add(
        "overlay.show_notes",
        lambda raw: _parse_bool("overlay.show_notes", raw),
        lambda c: c.overlay.show_notes,
        lambda c, v: setattr(c.overlay, "show_notes", v),
        "bool",
    )
    add(
        "overlay.note_font_size",
        lambda raw: _parse_int_or_auto("overlay.note_font_size", raw, minimum=1),
        lambda c: c.overlay.note_font_size,
        lambda c, v: setattr(c.overlay, "note_font_size", v),
        'int >= 1 or "auto"',
    )
    add(
        "overlay.hide_on_unlabeled",
        lambda raw: _parse_bool("overlay.hide_on_unlabeled", raw),
        lambda c: c.overlay.hide_on_unlabeled,
        lambda c, v: setattr(c.overlay, "hide_on_unlabeled", v),
        "bool",
    )
    add(
        "debounce_ms",
        lambda raw: _parse_int("debounce_ms", raw, minimum=0),
        lambda c: c.debounce_ms,
        lambda c, v: setattr(c, "debounce_ms", v),
        "int >= 0",
    )
    add(
        "log_level",
        lambda raw: _parse_enum("log_level", raw, _LOG_LEVELS),
        lambda c: c.log_level,
        lambda c, v: setattr(c, "log_level", v),
        "enum {CRITICAL, ERROR, WARNING, INFO, DEBUG}",
    )

    return {field.key: field for field in fields}


#: Dotted-key -> validation/accessor table for ``config get/set`` (introspectable).
CONFIG_SCHEMA: dict[str, ConfigField] = _build_config_schema()


# --------------------------------------------------------------------------- #
# Config (de)serialization
# --------------------------------------------------------------------------- #


def config_to_dict(config: Config) -> dict[str, object]:
    """Serialize a :class:`Config` to the ``config.json`` payload shape (DESIGN §7.2)."""
    return {
        "schema_version": config.schema_version,
        "modes": dict(config.modes),
        "menubar": {
            "max_length": config.menubar.max_length,
            "show_buttons_row": config.menubar.show_buttons_row,
            "buttons_scope": config.menubar.buttons_scope,
            "pill_label_chars": config.menubar.pill_label_chars,
            "click_to_switch": config.menubar.click_to_switch,
        },
        "hud": {
            "duration_ms": config.hud.duration_ms,
            "font_size": config.hud.font_size,
            "position": config.hud.position,
            "margin": config.hud.margin,
        },
        "overlay": {
            "corner": config.overlay.corner,
            "margin": config.overlay.margin,
            "font_size": config.overlay.font_size,
            "bold": config.overlay.bold,
            "show_notes": config.overlay.show_notes,
            "note_font_size": config.overlay.note_font_size,
            "hide_on_unlabeled": config.overlay.hide_on_unlabeled,
        },
        "debounce_ms": config.debounce_ms,
        "log_level": config.log_level,
    }


def _as_mapping(value: object) -> Mapping[str, object]:
    """Return ``value`` if it is a mapping, else an empty mapping (tolerant read)."""
    return value if isinstance(value, Mapping) else {}


def _coerce_bool(value: object, default: bool) -> bool:
    """Coerce a JSON-loaded value to ``bool``, falling back to ``default``."""
    return value if isinstance(value, bool) else default


def _coerce_int(value: object, default: int) -> int:
    """Coerce a JSON-loaded value to ``int`` (excluding bool), else ``default``."""
    if isinstance(value, bool):
        return default
    return value if isinstance(value, int) else default


def _coerce_str(value: object, default: str) -> str:
    """Coerce a JSON-loaded value to ``str``, falling back to ``default``."""
    return value if isinstance(value, str) else default


def _coerce_int_or_auto(value: object, default: int | str, *, minimum: int = 1) -> int | str:
    """Coerce a value to ``int`` (>= ``minimum``) or the literal ``"auto"``, else ``default``.

    A loaded int below ``minimum`` falls back to ``default`` so a hand-edited
    ``config.json`` with e.g. ``font_size: 0`` doesn't render an invisible label --
    the same lower bound the ``config set`` parsers enforce (tolerant load, DESIGN §8.2).
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value if value >= minimum else default
    if isinstance(value, str) and value == "auto":
        return "auto"
    return default


def config_from_dict(data: Mapping[str, object]) -> Config:
    """Build a :class:`Config` from a (possibly partial) payload, filling defaults.

    Tolerant of missing/extra keys and wrong-typed values: each field falls back
    to its model default rather than raising, so a hand-edited or partial file
    still loads (recover, do not crash).
    """
    defaults = Config()

    modes = dict(default_modes())
    raw_modes = _as_mapping(data.get("modes"))
    for name in modes:
        modes[name] = _coerce_bool(raw_modes.get(name), modes[name])

    raw_menubar = _as_mapping(data.get("menubar"))
    menubar = MenubarConfig(
        max_length=_coerce_int(raw_menubar.get("max_length"), defaults.menubar.max_length),
        show_buttons_row=_coerce_bool(
            raw_menubar.get("show_buttons_row"), defaults.menubar.show_buttons_row
        ),
        buttons_scope=_coerce_str(raw_menubar.get("buttons_scope"), defaults.menubar.buttons_scope),
        pill_label_chars=_coerce_int(
            raw_menubar.get("pill_label_chars"), defaults.menubar.pill_label_chars
        ),
        click_to_switch=_coerce_bool(
            raw_menubar.get("click_to_switch"), defaults.menubar.click_to_switch
        ),
    )

    raw_hud = _as_mapping(data.get("hud"))
    hud = HudConfig(
        duration_ms=_coerce_int(raw_hud.get("duration_ms"), defaults.hud.duration_ms),
        font_size=_coerce_int_or_auto(raw_hud.get("font_size"), defaults.hud.font_size),
        position=_coerce_str(raw_hud.get("position"), defaults.hud.position),
        margin=_coerce_int(raw_hud.get("margin"), defaults.hud.margin),
    )

    raw_overlay = _as_mapping(data.get("overlay"))
    overlay = OverlayConfig(
        corner=_coerce_str(raw_overlay.get("corner"), defaults.overlay.corner),
        margin=_coerce_int(raw_overlay.get("margin"), defaults.overlay.margin),
        font_size=_coerce_int_or_auto(raw_overlay.get("font_size"), defaults.overlay.font_size),
        bold=_coerce_bool(raw_overlay.get("bold"), defaults.overlay.bold),
        show_notes=_coerce_bool(raw_overlay.get("show_notes"), defaults.overlay.show_notes),
        note_font_size=_coerce_int_or_auto(
            raw_overlay.get("note_font_size"), defaults.overlay.note_font_size
        ),
        hide_on_unlabeled=_coerce_bool(
            raw_overlay.get("hide_on_unlabeled"), defaults.overlay.hide_on_unlabeled
        ),
    )

    return Config(
        schema_version=_coerce_int(data.get("schema_version"), defaults.schema_version),
        modes=modes,
        menubar=menubar,
        hud=hud,
        overlay=overlay,
        debounce_ms=_coerce_int(data.get("debounce_ms"), defaults.debounce_ms),
        log_level=_coerce_str(data.get("log_level"), defaults.log_level),
    )


def load_config(paths: StorePaths) -> Config:
    """Load ``config.json``, returning :class:`Config` defaults if absent or corrupt.

    A missing file yields ``Config()``; a corrupt file or a wrong top-level shape
    is logged and recovered as defaults (never raises). The ``schema_version`` is
    checked and a mismatch is logged before a best-effort read.
    """
    data = _read_json(paths.config_file)
    if data is None:
        return Config()
    if not isinstance(data, Mapping):
        log.warning("config file %s is not a JSON object; using defaults", paths.config_file)
        return Config()
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        log.warning(
            "config file %s schema_version %r != %d; reading best-effort",
            paths.config_file,
            version,
            SCHEMA_VERSION,
        )
    return config_from_dict(data)


def save_config(paths: StorePaths, config: Config) -> None:
    """Persist ``config.json`` via locked atomic write (temp -> fsync -> replace)."""
    with _file_lock(paths.config_lock):
        _atomic_write_json(paths.config_file, config_to_dict(config))


# --------------------------------------------------------------------------- #
# Config dotted read/write
# --------------------------------------------------------------------------- #


def get_config_value(config: Config, key: str) -> object:
    """Return the typed value at the dotted ``key`` (pure read).

    Raises:
        ConfigKeyError: if ``key`` is not a known config key.
    """
    field = CONFIG_SCHEMA.get(key)
    if field is None:
        raise ConfigKeyError(f"unknown config key {key!r}")
    return field.getter(config)


def set_config_value(paths: StorePaths, key: str, raw_value: str) -> object:
    """Validate and persist ``raw_value`` for the dotted ``key``; return stored value.

    Validates ``raw_value`` against the schema purely first (so an invalid value
    never touches disk), then does a locked load -> apply -> save and returns the
    stored, typed value.

    Raises:
        ConfigKeyError: if ``key`` is not a known config key.
        ConfigValueError: if ``raw_value`` fails type/enum/range validation.
    """
    field = CONFIG_SCHEMA.get(key)
    if field is None:
        raise ConfigKeyError(f"unknown config key {key!r}")
    typed = field.parse(raw_value)
    with _file_lock(paths.config_lock):
        _guard_before_rewrite(paths.config_file)
        config = load_config(paths)
        field.setter(config, typed)
        _atomic_write_json(paths.config_file, config_to_dict(config))
    return typed


def format_scalar(value: object) -> str:
    """Format a config scalar for ``config get`` (bool -> ``true``/``false``)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
