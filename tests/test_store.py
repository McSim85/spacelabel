"""Persistent store: labels + config, atomic locked writes (DESIGN.md §7, DECISIONS.md §5)."""

from __future__ import annotations

import json

import pytest

from spacelabel import store
from spacelabel.model import AgentState, Config
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


def test_wallpaper_font_size_config(paths):
    assert store.set_config_value(paths, "wallpaper.font_size", "auto") == "auto"
    assert store.load_config(paths).wallpaper.font_size == "auto"
    assert store.set_config_value(paths, "wallpaper.font_size", "96") == 96
    assert store.load_config(paths).wallpaper.font_size == 96
    with pytest.raises(ConfigValueError):
        store.set_config_value(paths, "wallpaper.font_size", "0")


def test_font_size_load_rejects_below_minimum():
    # A hand-edited config.json with font_size < 1 must fall back to the default on
    # load (same lower bound `config set` enforces), not pass a value through that
    # would render an invisible/broken label. Applies to all int|auto font fields.
    cfg = store.config_from_dict(
        {
            "schema_version": 1,
            "wallpaper": {"font_size": 0},
            "hud": {"font_size": -1},
            "overlay": {"font_size": 0},
        }
    )
    assert cfg.wallpaper.font_size == "auto"  # WallpaperConfig default
    assert cfg.hud.font_size == "auto"  # HudConfig default
    assert cfg.overlay.font_size == 15  # OverlayConfig default
    # a valid value still loads verbatim
    assert store.config_from_dict({"wallpaper": {"font_size": 96}}).wallpaper.font_size == 96


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


# ---- agent runtime state (state.json) --------------------------------------


def test_agent_state_missing_is_default(paths):
    state = store.load_agent_state(paths)
    assert state.last_cdhash is None
    assert state.ax_was_trusted is False


def test_agent_state_roundtrip(paths):
    store.save_agent_state(paths, AgentState(last_cdhash="b4955ea0", ax_was_trusted=True))
    state = store.load_agent_state(paths)
    assert state == AgentState(last_cdhash="b4955ea0", ax_was_trusted=True)
    # Stored beside the config in its own file, separate from labels/displays/config.
    assert paths.state_file == paths.directory / "state.json"
    assert paths.state_file.exists()


def test_agent_state_overwrite_replaces(paths):
    store.save_agent_state(paths, AgentState(last_cdhash="old", ax_was_trusted=True))
    store.save_agent_state(paths, AgentState(last_cdhash="new", ax_was_trusted=True))
    assert store.load_agent_state(paths).last_cdhash == "new"


def test_agent_state_null_cdhash_persists(paths):
    # The signature can be unreadable yet we still record that AX was granted: the
    # ax_was_trusted signal must survive a None cdhash (drives is_grant_stale alone).
    store.save_agent_state(paths, AgentState(last_cdhash=None, ax_was_trusted=True))
    assert store.load_agent_state(paths) == AgentState(last_cdhash=None, ax_was_trusted=True)


def test_agent_state_corrupt_recovers_to_default(paths):
    paths.state_file.parent.mkdir(parents=True, exist_ok=True)
    paths.state_file.write_text("{ not json")
    # A malformed state file is only a regenerable checkpoint -> recover, never raise.
    assert store.load_agent_state(paths) == AgentState()


def test_agent_state_wrong_field_types_drop_to_defaults(paths):
    paths.state_file.parent.mkdir(parents=True, exist_ok=True)
    paths.state_file.write_text(
        json.dumps({"schema_version": 1, "last_cdhash": 123, "ax_was_trusted": "yes"})
    )
    state = store.load_agent_state(paths)
    assert state.last_cdhash is None  # non-str cdhash dropped
    assert state.ax_was_trusted is False  # non-bool flag dropped


def test_agent_state_independent_of_labels(paths):
    # state.json is its own file: a label write must not touch it, and vice versa.
    store.save_agent_state(paths, AgentState(last_cdhash="X", ax_was_trusted=True))
    store.set_label(paths, "space-1", "Email")
    assert store.load_agent_state(paths) == AgentState(last_cdhash="X", ax_was_trusted=True)
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


# ---- notes (per-Space task queue, keyed by Space UUID — DECISIONS.md 9.10) -


def test_add_note_creates_notes_only_entry(paths):
    label = store.add_note(paths, "u", "task A")
    assert label.text == ""  # no label yet -> notes-only entry
    assert [(n.text, n.done) for n in label.notes] == [("task A", False)]
    reloaded = store.load_labels(paths)["u"]
    assert reloaded.text == ""
    assert [(n.text, n.done) for n in reloaded.notes] == [("task A", False)]


def test_notes_only_entry_omits_label_key_on_disk(paths):
    store.add_note(paths, "u", "a")
    entry = json.loads(paths.labels_file.read_text(encoding="utf-8"))["labels"]["u"]
    assert "label" not in entry  # notes-only -> no 'label' key written
    assert entry["notes"] == [{"text": "a", "done": False}]


def test_set_note_done_roundtrip(paths):
    store.add_note(paths, "u", "a")
    store.add_note(paths, "u", "b")
    store.set_note_done(paths, "u", 1, True)
    notes = store.load_labels(paths)["u"].notes
    assert [(n.text, n.done) for n in notes] == [("a", False), ("b", True)]
    store.set_note_done(paths, "u", 1, False)  # undone
    assert store.load_labels(paths)["u"].notes[1].done is False


def test_set_label_preserves_notes(paths):
    store.add_note(paths, "u", "a")
    store.set_label(paths, "u", "Email")
    label = store.load_labels(paths)["u"]
    assert label.text == "Email"
    assert [n.text for n in label.notes] == ["a"]  # notes survive (re)labeling


def test_clear_label_keeps_notes_as_notes_only(paths):
    store.set_label(paths, "u", "Email")
    store.add_note(paths, "u", "a")
    assert store.clear_label(paths, "u") is True
    label = store.load_labels(paths)["u"]  # entry survives as notes-only
    assert label.text == ""
    assert [n.text for n in label.notes] == ["a"]


def test_clear_label_removes_entry_when_no_notes(paths):
    store.set_label(paths, "u", "Email")
    assert store.clear_label(paths, "u") is True
    assert store.load_labels(paths) == {}  # no notes -> entry removed (legacy behavior)


def test_clear_label_demotion_drops_color_and_bumps_updated_at(paths):
    # Demoting a colored, noted label to notes-only must drop the color (per-label
    # tag; the entry is now unlabeled) and refresh updated_at (codex review).
    store.set_label(paths, "u", "Email", color="#3b82f6", timestamp="2026-01-01T00:00:00Z")
    store.add_note(paths, "u", "a", timestamp="2026-01-02T00:00:00Z")
    store.clear_label(paths, "u", timestamp="2026-03-03T00:00:00Z")
    demoted = store.load_labels(paths)["u"]
    assert demoted.text == ""
    assert demoted.color is None  # stale color must not survive onto a notes-only entry
    assert demoted.updated_at == "2026-03-03T00:00:00Z"  # bumped
    assert [n.text for n in demoted.notes] == ["a"]  # queue preserved
    # A later re-label must NOT inherit the old color.
    relabeled = store.set_label(paths, "u", "Inbox", timestamp="2026-04-04T00:00:00Z")
    assert relabeled.color is None


def test_clear_note_one_then_all_removes_notes_only_entry(paths):
    store.add_note(paths, "u", "a")
    store.add_note(paths, "u", "b")
    assert store.clear_note(paths, "u", 0) == 1
    assert [n.text for n in store.load_labels(paths)["u"].notes] == ["b"]
    assert store.clear_note(paths, "u") == 1  # clear remaining
    assert store.load_labels(paths) == {}  # notes-only entry with no notes is dropped


def test_clear_all_notes_keeps_labeled_entry(paths):
    store.set_label(paths, "u", "Email")
    store.add_note(paths, "u", "a")
    store.clear_note(paths, "u")
    label = store.load_labels(paths)["u"]  # labeled entry kept, notes emptied
    assert label.text == "Email"
    assert label.notes == []


def test_note_ops_validate_index_and_missing_entry(paths):
    store.add_note(paths, "u", "a")
    # Out-of-range index and a missing entry both raise NoteIndexError (CLI -> exit 2),
    # validated INSIDE the lock so a corrupt store/concurrent edit can't misclassify it.
    with pytest.raises(store.NoteIndexError):
        store.set_note_done(paths, "u", 5, True)
    with pytest.raises(store.NoteIndexError):
        store.clear_note(paths, "u", 5)
    with pytest.raises(store.NoteIndexError):
        store.set_note_done(paths, "missing", 0, True)
    with pytest.raises(store.NoteIndexError):
        store.clear_note(paths, "missing", 0)


def test_clear_all_notes_is_idempotent_on_missing_entry(paths):
    # Clear-all never raises on a missing/empty queue (idempotent, returns 0) so a
    # concurrent clear can't turn `note clear` into an error (codex review).
    assert store.clear_note(paths, "missing") == 0
    store.set_label(paths, "u", "Email")  # labeled, no notes
    assert store.clear_note(paths, "u") == 0
    assert store.load_labels(paths)["u"].text == "Email"  # entry untouched


def test_notes_only_entry_survives_partial_malformed_notes(paths):
    # A notes-only entry keeps its valid notes even when some items are malformed;
    # only unusable items are dropped, so a later write never erases a recoverable
    # task list (codex review — valid data is preserved across the rewrite).
    paths.directory.mkdir(parents=True, exist_ok=True)
    paths.labels_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "labels": {"u": {"notes": [{"text": "keep me"}, {"bad": 1}, "x"]}},
            }
        ),
        encoding="utf-8",
    )
    label = store.load_labels(paths)["u"]  # entry survives (notes-only)
    assert label.text == ""
    assert [n.text for n in label.notes] == ["keep me"]
    store.add_note(paths, "u", "added")  # a write round-trips the surviving note
    assert [n.text for n in store.load_labels(paths)["u"].notes] == ["keep me", "added"]


def test_prune_keeps_notes_only_entry(paths):
    store.add_note(paths, "notes-only-live", "a")  # notes-only but its Space is live
    store.set_label(paths, "orphan", "x")
    removed = store.prune_labels(paths, {"notes-only-live"})
    assert removed == ["orphan"]
    assert set(store.load_labels(paths)) == {"notes-only-live"}


def test_prune_never_deletes_task_queues(paths):
    # `label prune` prunes LABELS, not NOTES (codex review): an orphan that still has
    # tasks is demoted to notes-only (label+color dropped, updated_at bumped, tasks
    # kept), an orphan with no tasks is deleted, and a notes-only orphan is untouched.
    store.set_label(paths, "noted", "Email", color="#3b82f6", timestamp="2026-01-01T00:00:00Z")
    store.add_note(paths, "noted", "task", timestamp="2026-01-02T00:00:00Z")
    store.set_label(paths, "bare", "Bare")  # label, no notes -> deleted
    store.add_note(paths, "tasksonly", "keep me")  # notes-only orphan -> untouched
    store.set_label(paths, "live", "Live")  # not an orphan
    pruned = store.prune_labels(paths, {"live"}, timestamp="2026-03-03T00:00:00Z")
    assert set(pruned) == {"noted", "bare"}  # only entries whose LABEL was pruned
    labels = store.load_labels(paths)
    assert "bare" not in labels  # deleted (no tasks to keep)
    assert labels["noted"].text == ""  # demoted to notes-only
    assert labels["noted"].color is None  # per-label color dropped
    assert labels["noted"].updated_at == "2026-03-03T00:00:00Z"  # bumped
    assert [n.text for n in labels["noted"].notes] == ["task"]  # task queue SURVIVED prune
    assert [n.text for n in labels["tasksonly"].notes] == ["keep me"]  # notes-only orphan kept
    assert labels["live"].text == "Live"


def test_notes_canonical_uuid_matching(paths):
    lower = "6622ac87-2fd2-48e8-934d-f6eb303ac9ba"
    store.add_note(paths, lower, "a")  # write with a lowercase UUID
    labels = store.load_labels(paths)
    assert lower.upper() in labels  # read back under the canonical (uppercase) key
    assert [n.text for n in labels[lower.upper()].notes] == ["a"]


def test_load_skips_entry_with_no_label_and_no_notes(paths):
    paths.directory.mkdir(parents=True, exist_ok=True)
    paths.labels_file.write_text(
        json.dumps({"schema_version": 1, "labels": {"u": {"label": "", "notes": []}}}),
        encoding="utf-8",
    )
    assert store.load_labels(paths) == {}  # carries nothing -> skipped


def test_load_tolerates_malformed_notes(paths):
    paths.directory.mkdir(parents=True, exist_ok=True)
    paths.labels_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "labels": {
                    "u": {
                        "label": "Email",
                        "notes": [{"text": "ok"}, {"bad": 1}, "x", {"text": ""}],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    notes = store.load_labels(paths)["u"].notes
    assert [(n.text, n.done) for n in notes] == [("ok", False)]  # only the valid item


def test_overlay_notes_config_roundtrip(paths):
    store.set_config_value(paths, "overlay.show_notes", "false")
    assert store.set_config_value(paths, "overlay.note_font_size", "11") == 11
    cfg = store.load_config(paths)
    assert cfg.overlay.show_notes is False
    assert cfg.overlay.note_font_size == 11
    assert store.set_config_value(paths, "overlay.note_font_size", "auto") == "auto"
    assert store.load_config(paths).overlay.note_font_size == "auto"
