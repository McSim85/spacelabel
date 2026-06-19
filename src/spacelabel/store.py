"""Persistent store — ``labels.json`` and ``config.json`` under Application Support.

Both files live in ``~/Library/Application Support/spacelabel/`` (DESIGN.md §7).
All writes are atomic (temp file → ``fsync`` → ``os.replace``) and serialized
with an advisory ``fcntl.flock``; the agent watches both files and reloads on
change so a CLI edit is reflected live without a restart.
"""

from __future__ import annotations

import logging
from pathlib import Path

from spacelabel.model import Config, Label

__all__ = [
    "SCHEMA_VERSION",
    "config_path",
    "data_dir",
    "labels_path",
    "load_config",
    "load_labels",
    "prune_labels",
    "save_config",
    "save_label",
]

log = logging.getLogger(__name__)

#: Bumped when the on-disk JSON shape changes; gates migrations.
SCHEMA_VERSION = 1


def data_dir() -> Path:
    """Return the per-user data directory (created lazily on first write)."""
    return Path.home() / "Library" / "Application Support" / "spacelabel"


def labels_path() -> Path:
    """Return the path to ``labels.json``."""
    return data_dir() / "labels.json"


def config_path() -> Path:
    """Return the path to ``config.json``."""
    return data_dir() / "config.json"


def load_labels() -> dict[str, Label]:
    """Load the UUID→label store, returning an empty mapping if absent."""
    # TODO(phase-4): read labels.json under flock, validate schema_version, map
    # each entry to a Label; on a corrupt/partial file log and recover (no bare
    # except — see DESIGN.md §8.2).
    raise NotImplementedError


def save_label(uuid: str, label: Label) -> None:
    """Set or replace a single label via locked read-modify-write + atomic replace."""
    # TODO(phase-4): mkdir data_dir, flock labels.lock, read-modify-write, atomic replace.
    raise NotImplementedError


def prune_labels(live_uuids: set[str]) -> int:
    """Drop labels whose UUID is absent from ``live_uuids``; return the count removed."""
    # TODO(phase-4): retain by default elsewhere; this is the explicit `label prune` path.
    raise NotImplementedError


def load_config() -> Config:
    """Load ``config.json``, returning defaults if absent."""
    # TODO(phase-4): read under flock, validate schema_version, build a Config.
    raise NotImplementedError


def save_config(config: Config) -> None:
    """Persist ``config.json`` via locked read-modify-write + atomic replace."""
    # TODO(phase-4): flock config.lock, serialize, atomic replace.
    raise NotImplementedError
