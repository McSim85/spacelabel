"""CLI integration tests via CliRunner with mocked CGS (no WindowServer needed).

Verifies the locked command surface, exit-code contract (0/1/2/3), and the
stdout=data / stderr=diagnostics parsing contract (DESIGN.md §8.1, DECISIONS.md
9.1/9.2, docs/CLI.md). The live CGS reads are monkeypatched, so these run on a
hosted CI runner with no displays or Spaces session.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from spacelabel.cli import cli
from spacelabel.install import AgentStatus
from spacelabel.model import Display, Space

U1 = "6622AC87-2FD2-48E8-934D-F6EB303AC9BA"
U2 = "1A0F5C2E-7B3D-4C8A-9E1F-2D4B6A8C0E12"
DISP_A = "874A623F-1111-2222-3333-444455556666"
DISP_B = "6FBB92D9-84CE-8D20-C114-3B1052DD9529"


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def cfg(tmp_path):
    return str(tmp_path / "config.json")


def _base(cfg, *args):
    return ["--config", cfg, *args]


# ---- agent flag parsing (run loop mocked) ----------------------------------


def test_agent_accepts_local_and_global_debug(runner, monkeypatch):
    seen = {}

    def fake_run_agent(*, config_path=None, verbose=False, debug=False):
        seen["verbose"], seen["debug"] = verbose, debug

    monkeypatch.setattr("spacelabel.agent.app.run_agent", fake_run_agent)

    assert runner.invoke(cli, ["agent", "--debug"]).exit_code == 0  # local form
    assert seen == {"verbose": False, "debug": True}

    assert runner.invoke(cli, ["--verbose", "agent"]).exit_code == 0  # global form
    assert seen == {"verbose": True, "debug": False}


# ---- label set / list / clear (no CGS needed for literal UUID) -------------


def test_label_set_then_list(runner, cfg):
    r = runner.invoke(cli, _base(cfg, "label", "set", U1, "Email"))
    assert r.exit_code == 0
    r = runner.invoke(cli, _base(cfg, "label", "list"))
    assert r.exit_code == 0
    # Aligned table: header + the labelled row (DECISIONS 9.2 revised).
    assert "SPACE_UUID" in r.stdout
    assert U1 in r.stdout
    assert "Email" in r.stdout


def test_label_list_json(runner, cfg):
    runner.invoke(cli, _base(cfg, "label", "set", U1, "Email"))
    r = runner.invoke(cli, _base(cfg, "label", "list", "--json"))
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data == [{"uuid": U1, "label": "Email", "color": None, "last_display": None}]


def test_label_set_empty_text_is_usage_error(runner, cfg):
    r = runner.invoke(cli, _base(cfg, "label", "set", U1, "   "))
    assert r.exit_code == 2  # click.BadParameter


def test_label_clear_is_idempotent(runner, cfg):
    runner.invoke(cli, _base(cfg, "label", "set", U1, "Email"))
    r1 = runner.invoke(cli, _base(cfg, "label", "clear", U1))
    assert r1.exit_code == 0
    r2 = runner.invoke(cli, _base(cfg, "label", "clear", U1))
    assert r2.exit_code == 0  # still 0 when nothing to clear
    assert r2.stdout == ""  # the note goes to stderr
    assert "nothing to clear" in r2.stderr.lower()


def test_label_set_current_uses_live_read(runner, cfg, monkeypatch):
    monkeypatch.setattr("spacelabel.platform.cgs.read_active_space_uuid", lambda: U2)
    monkeypatch.setattr("spacelabel.platform.cgs.active_display_uuid", lambda: DISP_A)
    r = runner.invoke(cli, _base(cfg, "label", "set", "current", "Code"))
    assert r.exit_code == 0
    r = runner.invoke(cli, _base(cfg, "label", "list"))
    assert U2 in r.stdout
    assert "Code" in r.stdout


def test_label_set_current_fails_when_no_active_space(runner, cfg, monkeypatch):
    monkeypatch.setattr("spacelabel.platform.cgs.read_active_space_uuid", lambda: None)
    r = runner.invoke(cli, _base(cfg, "label", "set", "current", "Code"))
    assert r.exit_code == 1
    assert "current" in r.stderr.lower()


def test_label_list_omits_notes_only_entries(runner, cfg):
    # A notes-only Space (no label) must not appear as a blank row in `label list`.
    runner.invoke(cli, _base(cfg, "note", "add", U1, "a"))
    runner.invoke(cli, _base(cfg, "label", "set", U2, "Email"))
    r = runner.invoke(cli, _base(cfg, "label", "list", "--json"))
    assert [entry["uuid"] for entry in json.loads(r.stdout)] == [U2]


# ---- note command group ----------------------------------------------------


def test_note_add_list_done_json(runner, cfg):
    assert runner.invoke(cli, _base(cfg, "note", "add", U1, "task A")).exit_code == 0
    assert runner.invoke(cli, _base(cfg, "note", "add", U1, "task B")).exit_code == 0
    assert runner.invoke(cli, _base(cfg, "note", "done", U1, "2")).exit_code == 0
    r = runner.invoke(cli, _base(cfg, "note", "list", U1, "--json"))
    assert r.exit_code == 0
    assert json.loads(r.stdout) == [
        {"index": 1, "text": "task A", "done": False},
        {"index": 2, "text": "task B", "done": True},
    ]


def test_note_list_table_is_on_stdout(runner, cfg):
    runner.invoke(cli, _base(cfg, "note", "add", U1, "task A"))
    r = runner.invoke(cli, _base(cfg, "note", "list", U1))
    assert r.exit_code == 0
    assert "TASK" in r.stdout and "task A" in r.stdout  # data on stdout


def test_note_add_empty_text_is_usage_error(runner, cfg):
    r = runner.invoke(cli, _base(cfg, "note", "add", U1, "   "))
    assert r.exit_code == 2


def test_note_undone(runner, cfg):
    runner.invoke(cli, _base(cfg, "note", "add", U1, "t"))
    runner.invoke(cli, _base(cfg, "note", "done", U1, "1"))
    assert runner.invoke(cli, _base(cfg, "note", "undone", U1, "1")).exit_code == 0
    r = runner.invoke(cli, _base(cfg, "note", "list", U1, "--json"))
    assert json.loads(r.stdout)[0]["done"] is False


def test_note_bad_index_is_usage_error(runner, cfg):
    runner.invoke(cli, _base(cfg, "note", "add", U1, "t"))
    assert runner.invoke(cli, _base(cfg, "note", "done", U1, "9")).exit_code == 2
    assert runner.invoke(cli, _base(cfg, "note", "clear", U1, "9")).exit_code == 2


def test_note_op_on_empty_queue_is_usage_error(runner, cfg):
    # Any index against an empty queue is a usage error (exit 2), not a crash.
    assert runner.invoke(cli, _base(cfg, "note", "done", U1, "1")).exit_code == 2


def test_note_clear_one_and_all(runner, cfg):
    runner.invoke(cli, _base(cfg, "note", "add", U1, "a"))
    runner.invoke(cli, _base(cfg, "note", "add", U1, "b"))
    assert runner.invoke(cli, _base(cfg, "note", "clear", U1, "1")).exit_code == 0
    r = runner.invoke(cli, _base(cfg, "note", "list", U1, "--json"))
    assert [n["text"] for n in json.loads(r.stdout)] == ["b"]
    assert runner.invoke(cli, _base(cfg, "note", "clear", U1)).exit_code == 0
    r = runner.invoke(cli, _base(cfg, "note", "list", U1, "--json"))
    assert json.loads(r.stdout) == []


def test_note_clear_empty_is_idempotent(runner, cfg):
    # Clearing all on a Space with no tasks is a no-op success (mirrors label clear).
    assert runner.invoke(cli, _base(cfg, "note", "clear", U1)).exit_code == 0


def test_note_add_current_uses_live_read(runner, cfg, monkeypatch):
    monkeypatch.setattr("spacelabel.platform.cgs.read_active_space_uuid", lambda: U2)
    monkeypatch.setattr("spacelabel.platform.cgs.active_display_uuid", lambda: DISP_A)
    assert runner.invoke(cli, _base(cfg, "note", "add", "current", "task")).exit_code == 0
    r = runner.invoke(cli, _base(cfg, "note", "list", U2, "--json"))
    assert [n["text"] for n in json.loads(r.stdout)] == ["task"]


def test_note_add_rejects_non_uuid_target(runner, cfg):
    # A transposed TARGET/TEXT (`note add list current`) must be a usage error (exit 2),
    # not a silent entry on a Space "list" that can't exist.
    r = runner.invoke(cli, _base(cfg, "note", "add", "list", "current"))
    assert r.exit_code == 2
    assert "not a Space UUID" in r.stderr
    # ...and nothing was written.
    assert runner.invoke(cli, _base(cfg, "note", "list", U1, "--json")).stdout.strip() == "[]"


def test_label_set_rejects_non_uuid_target(runner, cfg):
    # Same guard on the label group (shared target resolver).
    r = runner.invoke(cli, _base(cfg, "label", "set", "notauuid", "Email"))
    assert r.exit_code == 2


def test_clear_can_remove_legacy_non_uuid_key(runner, cfg):
    # The create guard (set/add) must NOT block clearing a PRE-EXISTING non-UUID key
    # (a legacy typo/orphan); otherwise such entries become un-removable (codex review).
    from pathlib import Path

    (Path(cfg).parent / "labels.json").write_text(
        json.dumps(
            {"schema_version": 1, "labels": {"list": {"notes": [{"text": "x", "done": False}]}}}
        ),
        encoding="utf-8",
    )
    # inspect + remove the legacy key from the CLI
    listed = runner.invoke(cli, _base(cfg, "note", "list", "list", "--json"))
    assert [n["text"] for n in json.loads(listed.stdout)] == ["x"]
    assert runner.invoke(cli, _base(cfg, "note", "clear", "list")).exit_code == 0
    emptied = runner.invoke(cli, _base(cfg, "note", "list", "list", "--json"))
    assert json.loads(emptied.stdout) == []
    # ...but creating a NEW non-UUID entry is still rejected (exit 2)
    assert runner.invoke(cli, _base(cfg, "note", "add", "list", "x")).exit_code == 2


def test_note_list_no_target_enumerates_all_queues(runner, cfg):
    # `note list` with no target lists every note-bearing entry, so a notes-only queue
    # stays discoverable/recoverable even when its Space isn't live (codex review).
    runner.invoke(cli, _base(cfg, "note", "add", U1, "a"))
    runner.invoke(cli, _base(cfg, "note", "add", U1, "b"))
    runner.invoke(cli, _base(cfg, "label", "set", U2, "Email"))  # labeled, no notes -> excluded
    r = runner.invoke(cli, _base(cfg, "note", "list", "--json"))
    assert r.exit_code == 0
    assert json.loads(r.stdout) == [{"uuid": U1, "notes": 2}]


# ---- config get / set ------------------------------------------------------


def test_config_get_scalar_and_full(runner, cfg):
    r = runner.invoke(cli, _base(cfg, "config", "get", "hud.position"))
    assert r.exit_code == 0
    assert r.stdout.strip() == "center"

    r = runner.invoke(cli, _base(cfg, "config", "get"))
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert data["schema_version"] == 1
    assert data["modes"]["menubar"] is True


def test_config_get_unknown_key_exit_1(runner, cfg):
    r = runner.invoke(cli, _base(cfg, "config", "get", "hud.bogus"))
    assert r.exit_code == 1


def test_verbosity_flag_works_in_either_position(runner, cfg):
    # Trailing --debug on a leaf command must parse (and keep stdout pure data).
    r = runner.invoke(cli, _base(cfg, "config", "get", "hud.position", "--debug"))
    assert r.exit_code == 0
    assert r.stdout.strip() == "center"
    # Leading form is equivalent.
    r = runner.invoke(cli, ["--debug", "--config", cfg, "config", "get", "hud.position"])
    assert r.exit_code == 0
    assert r.stdout.strip() == "center"


def test_config_set_valid_and_invalid(runner, cfg):
    r = runner.invoke(cli, _base(cfg, "config", "set", "hud.position", "top-left"))
    assert r.exit_code == 0
    assert r.stdout.strip() == "top-left"
    # persisted
    r = runner.invoke(cli, _base(cfg, "config", "get", "hud.position"))
    assert r.stdout.strip() == "top-left"
    # invalid enum -> exit 1
    r = runner.invoke(cli, _base(cfg, "config", "set", "hud.position", "middle-middle"))
    assert r.exit_code == 1


# ---- mode ------------------------------------------------------------------


def test_mode_show_and_toggle(runner, cfg):
    r = runner.invoke(cli, _base(cfg, "mode", "hud"))
    assert r.exit_code == 0
    assert r.stdout.strip() == "hud: on"  # default on
    r = runner.invoke(cli, _base(cfg, "mode", "hud", "--off"))
    assert r.exit_code == 0
    assert r.stdout.strip() == "hud: off"


def test_mode_invalid_name_is_usage_error(runner, cfg):
    r = runner.invoke(cli, _base(cfg, "mode", "nonsense"))
    assert r.exit_code == 2  # click.Choice


def test_mode_wallpaper_enable_warns_on_stderr(runner, cfg):
    r = runner.invoke(cli, _base(cfg, "mode", "wallpaper", "--on"))
    assert r.exit_code == 0
    assert r.stdout.strip() == "wallpaper: on"
    assert "experimental" in r.stderr.lower()


# ---- spaces (mocked CGS) ---------------------------------------------------


def _fake_spaces(*, include_unlabelable=False):
    return [
        Space(uuid=U1, display_uuid=DISP_A, is_current=True, id64=1),
        Space(uuid=U2, display_uuid=DISP_B, is_current=False, id64=2),
    ]


def _fake_topology():
    return [
        Display(
            uuid=DISP_A,
            cg_display_id=1,
            size_pt=(1080, 1920),
            orientation="portrait",
            name="LG UltraFine",
        ),
        Display(uuid=DISP_B, cg_display_id=2, size_pt=(1920, 1080), name="DELL 4K"),
    ]


def test_spaces_table_marks_current_with_header_on_stdout(runner, cfg, monkeypatch):
    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", _fake_spaces)
    monkeypatch.setattr("spacelabel.platform.displays.discover_topology", _fake_topology)
    r = runner.invoke(cli, _base(cfg, "spaces"))
    assert r.exit_code == 0
    lines = r.stdout.strip().splitlines()
    assert lines[0].startswith("CURRENT")  # aligned table header on stdout
    assert any(line.startswith("*") for line in lines[1:])  # current marked
    assert U1 in r.stdout


def test_spaces_surfaces_unlabelable_space(runner, cfg, monkeypatch):
    # A Space macOS hasn't assigned a UUID (uuid="") must still appear, marked
    # unlabelable, so the display is visible (the second-display case).
    def _with_unlabelable(*, include_unlabelable=False):
        spaces = _fake_spaces()
        if include_unlabelable:
            spaces.append(Space(uuid="", display_uuid=DISP_B, is_current=True, id64=1))
        return spaces

    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", _with_unlabelable)
    monkeypatch.setattr("spacelabel.platform.displays.discover_topology", _fake_topology)
    r = runner.invoke(cli, _base(cfg, "spaces"))
    assert r.exit_code == 0
    assert "(no UUID)" in r.stdout
    # JSON marks it labelable=false with a null uuid.
    rj = runner.invoke(cli, _base(cfg, "spaces", "--json"))
    data = json.loads(rj.stdout)
    unlabelable = [d for d in data if not d["labelable"]]
    assert len(unlabelable) == 1
    assert unlabelable[0]["uuid"] is None


def test_spaces_json(runner, cfg, monkeypatch):
    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", _fake_spaces)
    monkeypatch.setattr("spacelabel.platform.displays.discover_topology", _fake_topology)
    r = runner.invoke(cli, _base(cfg, "spaces", "--json"))
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert len(data) == 2
    assert data[0]["current"] is True
    assert data[0]["display_name"] == "LG UltraFine"


def test_spaces_reports_notes_only_space_as_unlabeled(runner, cfg, monkeypatch):
    # `note add` on an unlabeled Space creates a notes-only entry (Label.text == "");
    # `spaces` must still report it as UNLABELED, not as a blank label (DECISIONS 9.10).
    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", _fake_spaces)
    monkeypatch.setattr("spacelabel.platform.displays.discover_topology", _fake_topology)
    runner.invoke(cli, _base(cfg, "note", "add", U1, "a task"))
    data = json.loads(runner.invoke(cli, _base(cfg, "spaces", "--json")).stdout)
    u1 = next(d for d in data if d["uuid"] == U1)
    assert u1["label"] is None  # notes-only -> null, NOT ""
    assert u1["labelable"] is True
    assert u1["notes"] == 1  # but the task queue is still discoverable here
    # the table shows "(unlabeled)" + a NOTES column, never a blank LABEL cell
    table = runner.invoke(cli, _base(cfg, "spaces")).stdout
    assert "(unlabeled)" in table
    assert "NOTES" in table


def test_spaces_active_display_filter(runner, cfg, monkeypatch):
    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", _fake_spaces)
    monkeypatch.setattr("spacelabel.platform.cgs.active_display_uuid", lambda: DISP_A)
    monkeypatch.setattr("spacelabel.platform.displays.discover_topology", _fake_topology)
    r = runner.invoke(cli, _base(cfg, "spaces", "--active-display", "--json"))
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert [d["uuid"] for d in data] == [U1]  # only the active display's Space


def test_spaces_active_display_failure_does_not_widen(runner, cfg, monkeypatch):
    # --active-display must never fall back to ALL displays on lookup failure; it
    # fails (exit 1) rather than returning a broader result than requested.
    import spacelabel.platform.cgs as cgs_mod

    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", _fake_spaces)
    monkeypatch.setattr("spacelabel.platform.displays.discover_topology", _fake_topology)

    def _boom():
        raise cgs_mod.CGSUnavailableError("active display gone")

    monkeypatch.setattr("spacelabel.platform.cgs.active_display_uuid", _boom)
    assert runner.invoke(cli, _base(cfg, "spaces", "--active-display")).exit_code == 1

    monkeypatch.setattr("spacelabel.platform.cgs.active_display_uuid", lambda: "")
    r = runner.invoke(cli, _base(cfg, "spaces", "--active-display"))
    assert r.exit_code == 1
    assert r.stdout == ""  # no widened data leaked to the channel


def test_spaces_both_paths_failing_exits_1(runner, cfg, monkeypatch):
    # CGS unavailable AND an empty plist fallback == both read paths failed -> exit 1
    # (not an empty-success table). read_spaces recovers its own errors to [].
    import spacelabel.platform.cgs as cgs_mod

    def _boom(**_kw):
        raise cgs_mod.CGSUnavailableError("symbols gone")

    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", _boom)
    monkeypatch.setattr("spacelabel.platform.spaces_plist.read_spaces", lambda **_kw: [])
    r = runner.invoke(cli, _base(cfg, "spaces"))
    assert r.exit_code == 1
    assert r.stdout == ""  # nothing on the data channel


def test_wallpaper_is_a_single_mode_toggle(runner, cfg):
    # The redundant wallpaper.enabled_experimental key is gone; modes.wallpaper alone
    # toggles the mode (uniform with the other three modes).
    r = runner.invoke(cli, _base(cfg, "config", "set", "wallpaper.enabled_experimental", "true"))
    assert r.exit_code == 1  # unknown key now
    r = runner.invoke(cli, _base(cfg, "config", "get"))
    assert "enabled_experimental" not in r.stdout  # not in the serialized config
    assert runner.invoke(cli, _base(cfg, "mode", "wallpaper", "--on")).exit_code == 0


def test_spaces_falls_back_to_plist_on_cgs_unavailable(runner, cfg, monkeypatch):
    import spacelabel.platform.cgs as cgs_mod

    def _boom(*, include_unlabelable=False):
        raise cgs_mod.CGSUnavailableError("symbols gone")

    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", _boom)
    monkeypatch.setattr("spacelabel.platform.spaces_plist.read_spaces", _fake_spaces)
    monkeypatch.setattr("spacelabel.platform.displays.discover_topology", lambda: [])
    r = runner.invoke(cli, _base(cfg, "spaces"))
    assert r.exit_code == 0
    assert U1 in r.stdout


def test_spaces_falls_back_to_plist_when_pyobjc_absent(runner, cfg, monkeypatch):
    # PyObjC absent: enumerate_spaces raises ImportError (lazy `import objc`). The
    # pure-stdlib plist fallback must still run, not abort before it.
    def _no_pyobjc(*, include_unlabelable=False):
        raise ModuleNotFoundError("No module named 'objc'")

    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", _no_pyobjc)
    monkeypatch.setattr("spacelabel.platform.spaces_plist.read_spaces", _fake_spaces)
    monkeypatch.setattr("spacelabel.platform.displays.discover_topology", _no_pyobjc)
    r = runner.invoke(cli, _base(cfg, "spaces"))
    assert r.exit_code == 0  # fell back to plist AND names degraded, no crash
    assert U1 in r.stdout


# ---- display labels --------------------------------------------------------


def test_display_list_shows_overlay_status(runner, cfg, monkeypatch):
    # display list --json must include "overlay" key so users can see per-display state.
    monkeypatch.setattr("spacelabel.platform.displays.discover_topology", lambda: [])
    monkeypatch.setattr("spacelabel.platform.cgs.active_display_uuid", lambda: "")
    runner.invoke(cli, _base(cfg, "display", "set", DISP_A, "Main"))
    # Default: overlay on.
    r = runner.invoke(cli, _base(cfg, "display", "list", "--json"))
    assert json.loads(r.stdout)[0]["overlay"] == "on"
    # After overlay-off: overlay shows "off".
    runner.invoke(cli, _base(cfg, "display", "overlay-off", DISP_A))
    r = runner.invoke(cli, _base(cfg, "display", "list", "--json"))
    assert json.loads(r.stdout)[0]["overlay"] == "off"
    # After overlay-on: back to "on".
    runner.invoke(cli, _base(cfg, "display", "overlay-on", DISP_A))
    r = runner.invoke(cli, _base(cfg, "display", "list", "--json"))
    assert json.loads(r.stdout)[0]["overlay"] == "on"


def test_display_list_fallback_includes_overlay_only_uuids(runner, cfg, monkeypatch):
    # A display with only overlay-off state (no custom name) must still appear in the
    # no-topology fallback — previously it was invisible because the fallback iterated
    # overrides.items() only, missing UUIDs that only existed in overlay_disabled.
    monkeypatch.setattr("spacelabel.platform.displays.discover_topology", lambda: [])
    monkeypatch.setattr("spacelabel.platform.cgs.active_display_uuid", lambda: "")
    # DISP_A: overlay-off only (no custom name).
    runner.invoke(cli, _base(cfg, "display", "overlay-off", DISP_A))
    r = runner.invoke(cli, _base(cfg, "display", "list", "--json"))
    records = json.loads(r.stdout)
    uuids = [rec["uuid"] for rec in records]
    assert DISP_A in uuids, "overlay-only UUID must appear in the fallback output"
    rec = next(r for r in records if r["uuid"] == DISP_A)
    assert rec["overlay"] == "off"
    assert rec["name"] == ""  # no custom name stored
    assert rec["custom"] is False


def test_display_set_list_clear(runner, cfg, monkeypatch):
    # No live displays in the test -> list falls back to stored overrides.
    monkeypatch.setattr("spacelabel.platform.displays.discover_topology", lambda: [])
    monkeypatch.setattr("spacelabel.platform.cgs.active_display_uuid", lambda: "")

    assert runner.invoke(cli, _base(cfg, "display", "set", DISP_A, "Main")).exit_code == 0
    r = runner.invoke(cli, _base(cfg, "display", "list", "--json"))
    assert r.exit_code == 0
    assert json.loads(r.stdout) == [
        {"uuid": DISP_A, "name": "Main", "custom": True, "active": False, "overlay": "on"}
    ]
    assert runner.invoke(cli, _base(cfg, "display", "clear", DISP_A)).exit_code == 0
    r = runner.invoke(cli, _base(cfg, "display", "list", "--json"))
    assert json.loads(r.stdout) == []


def test_display_list_degrades_when_pyobjc_absent(runner, cfg, monkeypatch):
    # On a PyObjC-less host BOTH legs raise ImportError: the cosmetic active marker
    # (active_display_uuid) AND topology discovery (discover_topology imports AppKit
    # lazily). `display list` must still print stored names, not abort the command.
    def _no_pyobjc(*_a, **_k):
        raise ModuleNotFoundError("No module named 'AppKit'")

    monkeypatch.setattr("spacelabel.platform.displays.discover_topology", _no_pyobjc)
    monkeypatch.setattr("spacelabel.platform.cgs.active_display_uuid", _no_pyobjc)
    assert runner.invoke(cli, _base(cfg, "display", "set", DISP_A, "Main")).exit_code == 0
    r = runner.invoke(cli, _base(cfg, "display", "list", "--json"))
    assert r.exit_code == 0
    assert json.loads(r.stdout) == [
        {"uuid": DISP_A, "name": "Main", "custom": True, "active": False, "overlay": "on"}
    ]


def test_display_clear_lowercase_uuid_reports_cleared(runner, cfg, monkeypatch):
    # `display clear` with a non-canonical (lowercase) literal must canonicalize before
    # the existed pre-check, so it reports "Cleared", not "nothing to clear" (the stored
    # key is canonical). The destructive clear itself already worked either way.
    monkeypatch.setattr("spacelabel.platform.displays.discover_topology", lambda: [])
    monkeypatch.setattr("spacelabel.platform.cgs.active_display_uuid", lambda: "")
    assert runner.invoke(cli, _base(cfg, "display", "set", DISP_A, "Main")).exit_code == 0
    r = runner.invoke(cli, _base(cfg, "display", "clear", DISP_A.lower()))
    assert r.exit_code == 0
    assert "Cleared" in r.stderr
    assert "nothing to clear" not in r.stderr


def test_display_set_empty_name_is_usage_error(runner, cfg):
    r = runner.invoke(cli, _base(cfg, "display", "set", DISP_A, "  "))
    assert r.exit_code == 2  # click.BadParameter


def test_display_set_current_uses_active_display(runner, cfg, monkeypatch):
    monkeypatch.setattr("spacelabel.platform.cgs.active_display_uuid", lambda: DISP_B)
    monkeypatch.setattr("spacelabel.platform.displays.discover_topology", lambda: [])
    assert runner.invoke(cli, _base(cfg, "display", "set", "current", "Side")).exit_code == 0
    r = runner.invoke(cli, _base(cfg, "display", "list", "--json"))
    assert json.loads(r.stdout)[0]["uuid"] == DISP_B


def test_spaces_stdout_is_ansi_free_when_piped(runner, cfg, monkeypatch):
    # CliRunner is not a TTY, so click must strip the styling from the data channel.
    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", _fake_spaces)
    monkeypatch.setattr("spacelabel.platform.displays.discover_topology", _fake_topology)
    r = runner.invoke(cli, _base(cfg, "spaces"))
    assert "\x1b[" not in r.stdout


# ---- status (mocked launchctl + agent.lock probe) --------------------------


def _patch_status(monkeypatch, st: AgentStatus) -> None:
    # agent_status_detail is called with the active --config path; accept + ignore it.
    monkeypatch.setattr("spacelabel.install.agent_status_detail", lambda _cfg=None: st)


def test_status_running_managed_exit_0(runner, monkeypatch):
    _patch_status(
        monkeypatch,
        AgentStatus(installed=True, loaded=True, running=True, pid=4213, managed=True),
    )
    r = runner.invoke(cli, ["status"])
    assert r.exit_code == 0
    assert "running (managed)" in r.stdout
    assert "4213" in r.stdout


def test_status_running_foreground_exit_0(runner, monkeypatch):
    # A foreground `spacelabel agent` holds agent.lock but launchd does not manage it.
    _patch_status(
        monkeypatch,
        AgentStatus(installed=False, loaded=False, running=True, pid=50803, managed=False),
    )
    r = runner.invoke(cli, ["status"])
    assert r.exit_code == 0
    assert "running (foreground)" in r.stdout
    assert "50803" in r.stdout


def test_status_installed_not_running_exit_3(runner, monkeypatch):
    _patch_status(
        monkeypatch,
        AgentStatus(installed=True, loaded=True, running=False, pid=None, managed=False),
    )
    r = runner.invoke(cli, ["status"])
    assert r.exit_code == 3
    assert "not running" in r.stdout
    assert "installed" in r.stdout


def test_status_not_installed_exit_3(runner, monkeypatch):
    _patch_status(
        monkeypatch,
        AgentStatus(installed=False, loaded=False, running=False, pid=None, managed=False),
    )
    r = runner.invoke(cli, ["status"])
    assert r.exit_code == 3
    assert "not installed" in r.stdout


def test_status_json(runner, monkeypatch):
    _patch_status(
        monkeypatch,
        AgentStatus(installed=True, loaded=True, running=True, pid=99, managed=True),
    )
    r = runner.invoke(cli, ["status", "--json"])
    assert r.exit_code == 0
    payload = json.loads(r.stdout)
    assert payload == {
        "installed": True,
        "loaded": True,
        "running": True,
        "pid": 99,
        "managed": True,
        "label": "dev.mcsim.spacelabel",
    }


# ---- uninstall / uninstall --purge -----------------------------------------


def _agent_not_running(monkeypatch):
    # The real --purge flow checks the default store lock for a live foreground agent before
    # deleting; stub it to "not held" so the deletion path runs (a dedicated test covers the
    # refusal). Also stub agent_status_detail for any status-path callers.
    monkeypatch.setattr("spacelabel.install.unmanaged_default_lock_holder", lambda: (False, None))
    monkeypatch.setattr(
        "spacelabel.install.agent_status_detail",
        lambda _cfg=None: AgentStatus(
            installed=False, loaded=False, running=False, pid=None, managed=False
        ),
    )


def test_uninstall_default_keeps_data_with_breadcrumb(runner, monkeypatch):
    calls = []
    monkeypatch.setattr("spacelabel.install.uninstall_agent", lambda: calls.append("agent"))
    monkeypatch.setattr("spacelabel.install.purge_user_data", lambda t: calls.append("purge") or [])
    r = runner.invoke(cli, ["uninstall"])
    assert r.exit_code == 0
    assert calls == ["agent"]  # purge never runs without --purge
    assert "labels and config kept" in r.stderr
    assert "--purge" in r.stderr  # breadcrumb nudges toward the deep clean


def test_uninstall_keep_labels_is_deprecated_noop(runner, monkeypatch):
    monkeypatch.setattr("spacelabel.install.uninstall_agent", lambda: None)
    r = runner.invoke(cli, ["uninstall", "--keep-labels"])
    assert r.exit_code == 0
    assert "no-op" in r.stderr and "--purge" in r.stderr


def test_uninstall_purge_dry_run_lists_paths_on_stdout(runner, monkeypatch):
    targets = [Path("/x/Application Support/spacelabel"), Path("/x/Caches/spacelabel")]
    monkeypatch.setattr(
        "spacelabel.install.purge_targets", lambda paths, remove_completion: targets
    )
    called = []
    monkeypatch.setattr("spacelabel.install.uninstall_agent", lambda: called.append("agent"))
    monkeypatch.setattr(
        "spacelabel.install.purge_user_data", lambda t: called.append("purge") or []
    )
    r = runner.invoke(cli, ["uninstall", "--purge", "--dry-run"])
    assert r.exit_code == 0
    assert called == []  # dry run removes nothing and does not touch the agent
    for target in targets:
        assert str(target) in r.stdout  # resolved paths on the data channel


def test_uninstall_purge_non_tty_without_yes_refuses(runner, monkeypatch):
    _agent_not_running(monkeypatch)
    monkeypatch.setattr("spacelabel.cli._isatty", lambda: False)
    # Non-empty targets (the default purge has data to delete) -> the --yes gate must fire.
    monkeypatch.setattr(
        "spacelabel.install.purge_targets", lambda paths, remove_completion: [Path("/x/data")]
    )
    deleted = []
    monkeypatch.setattr("spacelabel.install.purge_user_data", lambda t: deleted.append(t) or [])
    monkeypatch.setattr("spacelabel.install.uninstall_agent", lambda: None)
    r = runner.invoke(cli, ["uninstall", "--purge"])
    assert r.exit_code == 2  # UsageError -> never deletes non-interactively
    assert deleted == []


def test_uninstall_purge_no_targets_skips_yes_requirement(runner, monkeypatch):
    # A custom --config purges nothing (empty targets), so it needs neither --yes nor a TTY;
    # a harmless scripted uninstall that deletes no data must not fail. (real purge_targets
    # returns [] for a custom config -> not mocked here.)
    _agent_not_running(monkeypatch)
    monkeypatch.setattr("spacelabel.cli._isatty", lambda: False)
    monkeypatch.setattr("spacelabel.install.uninstall_agent", lambda: None)
    received: list = []
    monkeypatch.setattr("spacelabel.install.purge_user_data", lambda t: received.extend(t) or [])
    r = runner.invoke(cli, ["--config", "/tmp/mycfg.json", "uninstall", "--purge"])  # no --yes/tty
    assert r.exit_code == 0  # nothing to delete -> no --yes required
    assert received == []  # and nothing was deleted


def test_uninstall_purge_yes_removes_agent_then_purges(runner, monkeypatch):
    _agent_not_running(monkeypatch)
    targets = [Path("/x/data")]
    order = []
    monkeypatch.setattr(
        "spacelabel.install.purge_targets", lambda paths, remove_completion: targets
    )
    monkeypatch.setattr("spacelabel.install.uninstall_agent", lambda: order.append("agent"))
    monkeypatch.setattr(
        "spacelabel.install.purge_user_data", lambda t: order.append(("purge", t)) or []
    )
    r = runner.invoke(cli, ["uninstall", "--purge", "--yes"])
    assert r.exit_code == 0
    assert order == ["agent", ("purge", targets)]  # agent removed BEFORE data purged
    assert "purged spacelabel data" in r.stderr


def test_uninstall_purge_partial_failure_exit_1(runner, monkeypatch):
    _agent_not_running(monkeypatch)
    monkeypatch.setattr(
        "spacelabel.install.purge_targets",
        lambda paths, remove_completion: [Path("/x/data"), Path("/x/logs")],
    )
    monkeypatch.setattr("spacelabel.install.uninstall_agent", lambda: None)
    monkeypatch.setattr("spacelabel.install.purge_user_data", lambda t: [Path("/x/logs")])
    r = runner.invoke(cli, ["uninstall", "--purge", "--yes"])
    assert r.exit_code == 1
    assert "could not remove" in r.stderr
    assert "/x/logs" in r.stderr


def test_uninstall_purge_interactive_confirm(runner, monkeypatch):
    _agent_not_running(monkeypatch)
    monkeypatch.setattr("spacelabel.cli._isatty", lambda: True)
    monkeypatch.setattr(
        "spacelabel.install.purge_targets", lambda paths, remove_completion: [Path("/x/data")]
    )
    order = []
    monkeypatch.setattr("spacelabel.install.uninstall_agent", lambda: order.append("agent"))
    monkeypatch.setattr("spacelabel.install.purge_user_data", lambda t: order.append("purge") or [])
    # "y" proceeds...
    assert runner.invoke(cli, ["uninstall", "--purge"], input="y\n").exit_code == 0
    assert order == ["agent", "purge"]
    # ...and "n" aborts without removing anything.
    order.clear()
    r = runner.invoke(cli, ["uninstall", "--purge"], input="n\n")
    assert r.exit_code != 0  # click.confirm(abort=True) -> Abort
    assert order == []


def test_uninstall_purge_refuses_while_foreground_agent_running(runner, monkeypatch):
    # Any unmanaged holder of the default store lock -> refuse (lock-level guard, so it also
    # catches an alt config in the default dir, which a config-aware status check would miss).
    monkeypatch.setattr("spacelabel.install.unmanaged_default_lock_holder", lambda: (True, 50803))
    monkeypatch.setattr(
        "spacelabel.install.purge_targets", lambda paths, remove_completion: [Path("/x/data")]
    )
    deleted = []
    monkeypatch.setattr("spacelabel.install.uninstall_agent", lambda: deleted.append("agent"))
    monkeypatch.setattr(
        "spacelabel.install.purge_user_data", lambda t: deleted.append("purge") or []
    )
    r = runner.invoke(cli, ["uninstall", "--purge", "--yes"])
    assert r.exit_code == 1  # ClickException
    assert "foreground" in r.stderr and "50803" in r.stderr
    assert deleted == []  # neither the agent nor the data was touched


def test_uninstall_purge_custom_config_purges_nothing_and_notes_manual_removal(
    runner, monkeypatch, tmp_path
):
    # F2: a custom --config owns nothing safe to auto-delete (its dir isn't ours; caches/logs
    # are the default install's), so NOTHING is purged and the CLI says to remove it manually.
    _agent_not_running(monkeypatch)
    monkeypatch.setattr("spacelabel.install.uninstall_agent", lambda: None)
    deleted: list = []
    monkeypatch.setattr("spacelabel.install.purge_user_data", lambda t: deleted.extend(t) or [])
    r = runner.invoke(
        cli, ["--config", str(tmp_path / "mycfg.json"), "uninstall", "--purge", "--yes"]
    )
    assert r.exit_code == 0
    assert deleted == []  # real purge_targets returns [] for a custom config -> nothing deleted
    assert "NOT auto-purged" in r.stderr and "remove it manually" in r.stderr


def test_uninstall_purge_custom_config_not_blocked_by_running_default_agent(
    runner, monkeypatch, tmp_path
):
    # F3: a custom --config purge deletes nothing, so it must NOT be refused just because the
    # default agent is running (the guard only applies to the default purge now).
    monkeypatch.setattr(
        "spacelabel.install.agent_status_detail",
        lambda _cfg=None: AgentStatus(
            installed=True, loaded=True, running=True, pid=42, managed=False
        ),
    )
    monkeypatch.setattr("spacelabel.install.uninstall_agent", lambda: None)
    monkeypatch.setattr("spacelabel.install.purge_user_data", lambda t: [])
    r = runner.invoke(
        cli, ["--config", str(tmp_path / "mycfg.json"), "uninstall", "--purge", "--yes"]
    )
    assert r.exit_code == 0  # not blocked
    assert "still running" not in r.stderr


def test_isatty_handles_closed_stdin(monkeypatch):
    # A closed/detached stdin must read as non-interactive, not crash (so --purge refuses).
    from spacelabel import cli as cli_mod

    class _ClosedStdin:
        def isatty(self):
            raise ValueError("I/O operation on closed file")

    monkeypatch.setattr(cli_mod.sys, "stdin", _ClosedStdin())
    assert cli_mod._isatty() is False


# ---- label prune (mocked CGS) ----------------------------------------------


def test_label_prune_dry_run_changes_nothing(runner, cfg, monkeypatch):
    runner.invoke(cli, _base(cfg, "label", "set", U1, "live"))
    runner.invoke(cli, _base(cfg, "label", "set", U2, "gone"))  # U2 not live -> orphan
    monkeypatch.setattr(
        "spacelabel.platform.cgs.enumerate_spaces",
        lambda **_kw: [Space(uuid=U1, display_uuid=DISP_A, is_current=True)],
    )
    r = runner.invoke(cli, _base(cfg, "label", "prune", "--dry-run"))
    assert r.exit_code == 0
    assert U2 in r.stdout
    # nothing removed
    r = runner.invoke(cli, _base(cfg, "label", "list"))
    assert U2 in r.stdout


def test_label_prune_refuses_on_empty_live_read(runner, cfg, monkeypatch):
    # DATA SAFETY: an empty live set means the read failed, not "delete everything".
    runner.invoke(cli, _base(cfg, "label", "set", U1, "keep"))
    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", lambda **_kw: [])
    monkeypatch.setattr("spacelabel.platform.spaces_plist.read_spaces", lambda **_kw: [])
    r = runner.invoke(cli, _base(cfg, "label", "prune"))
    assert r.exit_code == 1
    assert "refusing to prune" in r.stderr.lower()
    # the label must survive
    r = runner.invoke(cli, _base(cfg, "label", "list"))
    assert U1 in r.stdout
    # dry-run is guarded the same way (no misleading "would remove everything")
    r = runner.invoke(cli, _base(cfg, "label", "prune", "--dry-run"))
    assert r.exit_code == 1


def test_label_prune_removes_orphans(runner, cfg, monkeypatch):
    runner.invoke(cli, _base(cfg, "label", "set", U1, "live"))
    runner.invoke(cli, _base(cfg, "label", "set", U2, "gone"))  # U2 not live -> orphan
    monkeypatch.setattr(
        "spacelabel.platform.cgs.enumerate_spaces",
        lambda **_kw: [Space(uuid=U1, display_uuid=DISP_A, is_current=True)],
    )
    r = runner.invoke(cli, _base(cfg, "label", "prune"))
    assert r.exit_code == 0
    assert U2 in r.stdout
    r = runner.invoke(cli, _base(cfg, "label", "list"))
    assert U2 not in r.stdout
    assert U1 in r.stdout
