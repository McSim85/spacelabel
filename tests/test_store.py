"""Persistent store: labels + config, atomic locked writes (DESIGN.md §7, DECISIONS.md §5)."""

from __future__ import annotations

import json

import pytest

from spacelabel import store
from spacelabel.model import Config
from spacelabel.store import (
    ConfigKeyError,
    ConfigValueError,
    StorePaths,
)


@pytest.fixture
def paths(tmp_path):
    return StorePaths.resolve(tmp_path / "config.json")


def test_resolve_derives_sibling_paths(tmp_path):
    p = StorePaths.resolve(tmp_path / "config.json")
    assert p.directory == tmp_path
    assert p.config_file == tmp_path / "config.json"
    assert p.labels_file == tmp_path / "labels.json"
    assert p.config_lock != p.config_file
    assert p.labels_lock != p.labels_file


# ---- labels ----------------------------------------------------------------


def test_load_labels_missing_is_empty(paths):
    assert store.load_labels(paths) == {}


def test_set_label_roundtrip(paths):
    store.set_label(paths, "uuid-1", "Email", last_display="disp-1")
    labels = store.load_labels(paths)
    assert labels["uuid-1"].text == "Email"
    assert labels["uuid-1"].last_display == "disp-1"
    assert labels["uuid-1"].created_at is not None
    assert labels["uuid-1"].updated_at is not None
    assert paths.labels_file.exists()


def test_set_label_preserves_created_at_and_unset_fields(paths):
    first = store.set_label(paths, "u", "Email", color="#ff0000", timestamp="2026-01-01T00:00:00Z")
    second = store.set_label(paths, "u", "Email v2", timestamp="2026-02-02T00:00:00Z")
    assert second.created_at == first.created_at  # preserved
    assert second.updated_at == "2026-02-02T00:00:00Z"  # bumped
    assert second.color == "#ff0000"  # not wiped when arg omitted


def test_clear_label_is_idempotent(paths):
    store.set_label(paths, "u", "x")
    assert store.clear_label(paths, "u") is True
    assert store.clear_label(paths, "u") is False  # already gone, no error
    assert store.load_labels(paths) == {}


def test_prune_removes_orphans_only(paths):
    store.set_label(paths, "live-1", "a")
    store.set_label(paths, "orphan", "b")
    store.set_label(paths, "live-2", "c")
    removed = store.prune_labels(paths, {"live-1", "live-2"})
    assert removed == ["orphan"]
    assert set(store.load_labels(paths)) == {"live-1", "live-2"}


def test_load_labels_recovers_from_corrupt_file(paths, caplog):
    paths.directory.mkdir(parents=True, exist_ok=True)
    paths.labels_file.write_text("{ not valid json", encoding="utf-8")
    # Recover (return empty), do not raise; and it must have logged something.
    assert store.load_labels(paths) == {}


# ---- config ----------------------------------------------------------------


def test_load_config_default_when_missing(paths):
    cfg = store.load_config(paths)
    assert isinstance(cfg, Config)
    assert cfg.schema_version == store.SCHEMA_VERSION
    assert cfg.modes["menubar"] is True


def test_save_then_load_config_roundtrip(paths):
    cfg = store.load_config(paths)
    cfg.modes["overlay"] = True
    cfg.hud.position = "top-left"
    store.save_config(paths, cfg)
    again = store.load_config(paths)
    assert again.modes["overlay"] is True
    assert again.hud.position == "top-left"


def test_config_to_from_dict_roundtrip():
    cfg = Config()
    cfg.menubar.show_buttons_row = True
    cfg.overlay.font_size = "auto"
    data = store.config_to_dict(cfg)
    assert data["schema_version"] == 1
    again = store.config_from_dict(data)
    assert again.menubar.show_buttons_row is True
    assert again.overlay.font_size == "auto"


def test_config_from_dict_tolerates_missing_keys():
    cfg = store.config_from_dict({"schema_version": 1})
    assert cfg.debounce_ms == 200  # default filled in


def test_get_config_value_dotted(paths):
    cfg = store.load_config(paths)
    assert store.get_config_value(cfg, "hud.position") == "center"
    assert store.get_config_value(cfg, "modes.menubar") is True
    assert store.get_config_value(cfg, "debounce_ms") == 200


def test_get_config_value_unknown_key_raises(paths):
    cfg = store.load_config(paths)
    with pytest.raises(ConfigKeyError):
        store.get_config_value(cfg, "hud.nonsense")
    with pytest.raises(ConfigKeyError):
        store.get_config_value(cfg, "totally.bogus")


def test_set_config_value_bool_parsing(paths):
    assert store.set_config_value(paths, "modes.hud", "off") is False
    assert store.set_config_value(paths, "modes.hud", "on") is True
    assert store.set_config_value(paths, "menubar.show_buttons_row", "true") is True
    assert store.load_config(paths).menubar.show_buttons_row is True


def test_set_config_value_enum_and_anchor(paths):
    assert store.set_config_value(paths, "hud.position", "top-left") == "top-left"
    with pytest.raises(ConfigValueError):
        store.set_config_value(paths, "hud.position", "middle-middle")
    assert store.set_config_value(paths, "log_level", "DEBUG") == "DEBUG"
    with pytest.raises(ConfigValueError):
        store.set_config_value(paths, "log_level", "LOUD")


def test_set_config_value_int_and_auto(paths):
    assert store.set_config_value(paths, "hud.duration_ms", "900") == 900
    assert store.set_config_value(paths, "overlay.font_size", "auto") == "auto"
    assert store.set_config_value(paths, "overlay.font_size", "20") == 20
    with pytest.raises(ConfigValueError):
        store.set_config_value(paths, "hud.duration_ms", "not-a-number")


def test_set_config_value_range_checks(paths):
    with pytest.raises(ConfigValueError):
        store.set_config_value(paths, "menubar.pill_label_chars", "3")
    assert store.set_config_value(paths, "menubar.pill_label_chars", "2") == 2


def test_set_config_value_unknown_key_raises(paths):
    with pytest.raises(ConfigKeyError):
        store.set_config_value(paths, "hud.bogus", "1")


def test_set_config_value_persists(paths):
    store.set_config_value(paths, "debounce_ms", "350")
    assert store.load_config(paths).debounce_ms == 350
    # And the file is valid JSON with schema_version.
    data = json.loads(paths.config_file.read_text(encoding="utf-8"))
    assert data["schema_version"] == store.SCHEMA_VERSION


def test_format_scalar():
    assert store.format_scalar(True) == "true"
    assert store.format_scalar(False) == "false"
    assert store.format_scalar(200) == "200"
    assert store.format_scalar("center") == "center"


def test_display_labels_roundtrip(paths):
    assert store.load_display_labels(paths) == {}
    store.set_display_label(paths, "disp-1", "Main 4K")
    store.set_display_label(paths, "disp-2", "Portrait")
    assert store.load_display_labels(paths) == {"disp-1": "Main 4K", "disp-2": "Portrait"}
    # empty name clears just that display
    store.set_display_label(paths, "disp-1", "")
    assert store.load_display_labels(paths) == {"disp-2": "Portrait"}


def test_display_labels_independent_of_space_labels(paths):
    # Display names live in a separate file, so a label write must not wipe them.
    store.set_display_label(paths, "disp-1", "Main")
    store.set_label(paths, "space-1", "Email")
    assert store.load_display_labels(paths) == {"disp-1": "Main"}
    assert store.load_labels(paths)["space-1"].text == "Email"
    # ...and a display-name write must not wipe space labels.
    store.set_display_label(paths, "disp-2", "Side")
    assert store.load_labels(paths)["space-1"].text == "Email"


def test_set_label_canonicalizes_uuid_key(paths):
    # A label written with a lowercase/braced UUID must be stored under the canonical
    # CFUUID spelling so a later live lookup (CGS uppercase) matches it.
    lower = "6622ac87-2fd2-48e8-934d-f6eb303ac9ba"
    canonical = "6622AC87-2FD2-48E8-934D-F6EB303AC9BA"
    store.set_label(paths, lower, "Email")
    labels = store.load_labels(paths)
    assert canonical in labels
    assert lower not in labels
    # clearing with yet another spelling (braces) still matches the same Space.
    assert store.clear_label(paths, "{6622AC87-2FD2-48E8-934D-F6EB303AC9BA}") is True


def test_set_display_label_canonicalizes_uuid_key(paths):
    store.set_display_label(paths, "874a623f-f8f5-43c1-b11c-4aac3e383c0f", "Main")
    assert store.load_display_labels(paths) == {"874A623F-F8F5-43C1-B11C-4AAC3E383C0F": "Main"}


def test_load_labels_canonicalizes_legacy_lowercase_key(paths):
    # A labels.json written by an older build with a lowercase key must canonicalize
    # on READ so it still matches the (uppercase) live CGS uuid -- not just on write.
    lower = "6622ac87-2fd2-48e8-934d-f6eb303ac9ba"
    payload = {"schema_version": 1, "labels": {lower: {"label": "Email"}}}
    paths.labels_file.write_text(json.dumps(payload), encoding="utf-8")
    labels = store.load_labels(paths)
    assert "6622AC87-2FD2-48E8-934D-F6EB303AC9BA" in labels
    assert lower not in labels
    assert labels["6622AC87-2FD2-48E8-934D-F6EB303AC9BA"].text == "Email"


def test_load_display_labels_canonicalizes_legacy_lowercase_key(paths):
    payload = {"displays": {"874a623f-f8f5-43c1-b11c-4aac3e383c0f": "Main 4K"}}
    paths.displays_file.write_text(json.dumps(payload), encoding="utf-8")
    assert store.load_display_labels(paths) == {"874A623F-F8F5-43C1-B11C-4AAC3E383C0F": "Main 4K"}


def test_non_uuid_label_key_kept_as_is(paths):
    # Arbitrary (non-UUID) keys are never rejected/mangled.
    store.set_label(paths, "not-a-uuid", "x")
    assert "not-a-uuid" in store.load_labels(paths)


def test_corrupt_labels_file_is_backed_up_not_clobbered(paths):
    # DATA SAFETY: a corrupt file must not be silently overwritten (losing entries)
    # by the next single-key write; it is backed up to <file>.corrupt first.
    store.set_label(paths, "u1", "Email")
    store.set_label(paths, "u2", "Code")
    paths.labels_file.write_text("{ totally broken json", encoding="utf-8")

    store.set_label(paths, "u3", "Slack")  # write against a corrupt existing file

    backup = paths.labels_file.with_name(paths.labels_file.name + ".corrupt")
    assert backup.exists()  # the unparseable bytes are preserved, not discarded
    assert "totally broken json" in backup.read_text(encoding="utf-8")
    assert "u3" in store.load_labels(paths)  # the new write still succeeded


def test_write_oserror_surfaces_as_storeerror(paths, monkeypatch):
    # A write-path OSError must be wrapped as StoreError so the CLI handles it
    # cleanly (exit 1) instead of leaking a traceback.
    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(store.tempfile, "mkstemp", _boom)
    with pytest.raises(store.StoreError):
        store.set_label(paths, "u", "x")
