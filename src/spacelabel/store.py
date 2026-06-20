"""Persistent store -- ``labels.json`` and ``config.json`` under Application Support.

Both files live in ``~/Library/Application Support/spacelabel/`` (DESIGN.md §7).
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
    Config,
    HudConfig,
    Label,
    MenubarConfig,
    OverlayConfig,
    WallpaperConfig,
    default_modes,
)

__all__ = [
    "CONFIG_SCHEMA",
    "SCHEMA_VERSION",
    "ConfigKeyError",
    "ConfigValueError",
    "StoreError",
    "StorePaths",
    "clear_label",
    "config_from_dict",
    "config_path",
    "config_to_dict",
    "data_dir",
    "format_scalar",
    "get_config_value",
    "labels_path",
    "load_config",
    "load_display_labels",
    "load_labels",
    "prune_labels",
    "save_config",
    "set_config_value",
    "set_display_label",
    "set_label",
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
    first write.
    """

    directory: Path
    config_file: Path
    labels_file: Path
    displays_file: Path
    config_lock: Path
    labels_lock: Path
    displays_lock: Path

    @classmethod
    def default(cls) -> StorePaths:
        """Return the per-user default paths under Application Support."""
        directory = data_dir()
        config_file = directory / "config.json"
        labels_file = directory / "labels.json"
        displays_file = directory / "displays.json"
        return cls(
            directory=directory,
            config_file=config_file,
            labels_file=labels_file,
            displays_file=displays_file,
            config_lock=_lock_for(config_file),
            labels_lock=_lock_for(labels_file),
            displays_lock=_lock_for(displays_file),
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
        return cls(
            directory=directory,
            config_file=config_file,
            labels_file=labels_file,
            displays_file=displays_file,
            config_lock=_lock_for(config_file),
            labels_lock=_lock_for(labels_file),
            displays_lock=_lock_for(displays_file),
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
    partial file (DESIGN.md §7.3).
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


def _label_from_entry(uuid: str, entry: object) -> Label | None:
    """Build a :class:`Label` from one ``labels.json`` entry, or ``None`` if bad.

    The on-disk key is ``label`` (DESIGN.md §7.1); ``color``/``last_display`` and
    the timestamps are optional and forward-compatible. A non-dict entry or a
    missing/empty ``label`` text is skipped (logged), never crashes the load.
    """
    if not isinstance(entry, Mapping):
        log.warning("skipping label entry for %s: not an object", uuid)
        return None
    text = entry.get("label")
    if not isinstance(text, str) or not text:
        log.warning("skipping label entry for %s: missing/invalid 'label'", uuid)
        return None
    return Label(
        text=text,
        color=_opt_str(entry.get("color")),
        last_display=_opt_str(entry.get("last_display")),
        created_at=_opt_str(entry.get("created_at")),
        updated_at=_opt_str(entry.get("updated_at")),
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

    Optional fields (``color``/``last_display``/timestamps) are omitted when
    ``None`` so the file stays minimal (DESIGN.md §7.1).
    """
    entries: dict[str, object] = {}
    for uuid, label in labels.items():
        entry: dict[str, object] = {"label": label.text}
        if label.color is not None:
            entry["color"] = label.color
        if label.last_display is not None:
            entry["last_display"] = label.last_display
        if label.created_at is not None:
            entry["created_at"] = label.created_at
        if label.updated_at is not None:
            entry["updated_at"] = label.updated_at
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
        )
        labels[uuid] = label
        _atomic_write_json(paths.labels_file, _labels_to_payload(labels))
    return label


def clear_label(paths: StorePaths, uuid: str) -> bool:
    """Remove the label for ``uuid`` via locked RMW; return ``True`` if it existed."""
    uuid = canonical_uuid(uuid)
    with _file_lock(paths.labels_lock):
        _guard_before_rewrite(paths.labels_file)
        labels = load_labels(paths)
        if uuid not in labels:
            return False
        del labels[uuid]
        _atomic_write_json(paths.labels_file, _labels_to_payload(labels))
    return True


def prune_labels(paths: StorePaths, live_uuids: set[str]) -> list[str]:
    """Remove orphan labels (UUIDs absent from ``live_uuids``); return removed UUIDs.

    Performs a locked read-modify-write and returns the removed UUIDs in store
    order (DECISIONS.md 5.6 -- retain by default, explicit prune for removal).
    """
    with _file_lock(paths.labels_lock):
        _guard_before_rewrite(paths.labels_file)
        labels = load_labels(paths)
        orphans = find_orphans(labels, live_uuids)
        if not orphans:
            return []
        for uuid in orphans:
            del labels[uuid]
        _atomic_write_json(paths.labels_file, _labels_to_payload(labels))
    return orphans


# --------------------------------------------------------------------------- #
# Display labels (custom per-display names, keyed by display UUID)
# --------------------------------------------------------------------------- #


def load_display_labels(paths: StorePaths) -> dict[str, str]:
    """Load custom display names (display UUID -> name); ``{}`` if absent/corrupt.

    Stored separately from labels in ``displays.json`` so the labels read-modify-
    write never has to preserve it. Missing/corrupt files recover as ``{}``.
    """
    data = _read_json(paths.displays_file)
    if data is None:
        return {}
    if not isinstance(data, Mapping):
        log.warning("displays file %s is not a JSON object; ignoring", paths.displays_file)
        return {}
    raw = data.get("displays")
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, str] = {}
    for uuid, name in raw.items():
        if isinstance(uuid, str) and isinstance(name, str) and name:
            result[canonical_uuid(uuid)] = name  # canonical on read too (see load_labels)
    return result


def set_display_label(paths: StorePaths, display_uuid: str, name: str) -> None:
    """Set (or, when ``name`` is empty, clear) a custom name for a display UUID.

    Locked read-modify-write + atomic replace, mirroring the label store.
    """
    display_uuid = canonical_uuid(display_uuid)
    with _file_lock(paths.displays_lock):
        _guard_before_rewrite(paths.displays_file)
        names = load_display_labels(paths)
        if name:
            names[display_uuid] = name
        else:
            names.pop(display_uuid, None)
        _atomic_write_json(
            paths.displays_file, {"schema_version": SCHEMA_VERSION, "displays": names}
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

    for mode in ("menubar", "hud", "overlay", "wallpaper"):
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
        "wallpaper.position",
        lambda raw: _parse_anchor("wallpaper.position", raw),
        lambda c: c.wallpaper.position,
        lambda c, v: setattr(c.wallpaper, "position", v),
        "one of the nine anchors",
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
        },
        "wallpaper": {
            "position": config.wallpaper.position,
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


def _coerce_int_or_auto(value: object, default: int | str) -> int | str:
    """Coerce a value to ``int`` or the literal ``"auto"``, else ``default``."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value == "auto":
        return "auto"
    return default


def config_from_dict(data: Mapping[str, object]) -> Config:
    """Build a :class:`Config` from a (possibly partial) payload, filling defaults.

    Tolerant of missing/extra keys and wrong-typed values: each field falls back
    to its model default rather than raising, so a hand-edited or partial file
    still loads (DESIGN.md §8.2 -- recover, do not crash).
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
    )

    raw_wallpaper = _as_mapping(data.get("wallpaper"))
    wallpaper = WallpaperConfig(
        position=_coerce_str(raw_wallpaper.get("position"), defaults.wallpaper.position),
    )

    return Config(
        schema_version=_coerce_int(data.get("schema_version"), defaults.schema_version),
        modes=modes,
        menubar=menubar,
        hud=hud,
        overlay=overlay,
        wallpaper=wallpaper,
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
