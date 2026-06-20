"""CLI integration tests via CliRunner with mocked CGS (no WindowServer needed).

Verifies the locked command surface, exit-code contract (0/1/2/3), and the
stdout=data / stderr=diagnostics parsing contract (DESIGN.md §8.1, DECISIONS.md
9.1/9.2, docs/CLI.md). The live CGS reads are monkeypatched, so these run on a
hosted CI runner with no displays or Spaces session.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from spacelabel.cli import cli
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
    monkeypatch.setattr("spacelabel.platform.spaces_plist.read_spaces", lambda: [])
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


def test_display_set_list_clear(runner, cfg, monkeypatch):
    # No live displays in the test -> list falls back to stored overrides.
    monkeypatch.setattr("spacelabel.platform.displays.discover_topology", lambda: [])
    monkeypatch.setattr("spacelabel.platform.cgs.active_display_uuid", lambda: "")

    assert runner.invoke(cli, _base(cfg, "display", "set", DISP_A, "Main")).exit_code == 0
    r = runner.invoke(cli, _base(cfg, "display", "list", "--json"))
    assert r.exit_code == 0
    assert json.loads(r.stdout) == [
        {"uuid": DISP_A, "name": "Main", "custom": True, "active": False}
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
        {"uuid": DISP_A, "name": "Main", "custom": True, "active": False}
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


# ---- status (mocked launchctl) ---------------------------------------------


def test_status_running_exit_0(runner, monkeypatch):
    monkeypatch.setattr("spacelabel.install.agent_status", lambda: (True, 4213))
    r = runner.invoke(cli, ["status"])
    assert r.exit_code == 0
    assert "running" in r.stdout
    assert "4213" in r.stdout


def test_status_not_running_exit_3(runner, monkeypatch):
    monkeypatch.setattr("spacelabel.install.agent_status", lambda: (False, None))
    r = runner.invoke(cli, ["status"])
    assert r.exit_code == 3
    assert "not running" in r.stdout


def test_status_json(runner, monkeypatch):
    monkeypatch.setattr("spacelabel.install.agent_status", lambda: (True, 99))
    r = runner.invoke(cli, ["status", "--json"])
    assert r.exit_code == 0
    payload = json.loads(r.stdout)
    assert payload == {"running": True, "pid": 99, "label": "dev.mcsim.spacelabel"}


# ---- label prune (mocked CGS) ----------------------------------------------


def test_label_prune_dry_run_changes_nothing(runner, cfg, monkeypatch):
    runner.invoke(cli, _base(cfg, "label", "set", U1, "live"))
    runner.invoke(cli, _base(cfg, "label", "set", "ORPHAN-UUID", "gone"))
    monkeypatch.setattr(
        "spacelabel.platform.cgs.enumerate_spaces",
        lambda **_kw: [Space(uuid=U1, display_uuid=DISP_A, is_current=True)],
    )
    r = runner.invoke(cli, _base(cfg, "label", "prune", "--dry-run"))
    assert r.exit_code == 0
    assert "ORPHAN-UUID" in r.stdout
    # nothing removed
    r = runner.invoke(cli, _base(cfg, "label", "list"))
    assert "ORPHAN-UUID" in r.stdout


def test_label_prune_refuses_on_empty_live_read(runner, cfg, monkeypatch):
    # DATA SAFETY: an empty live set means the read failed, not "delete everything".
    runner.invoke(cli, _base(cfg, "label", "set", U1, "keep"))
    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", lambda **_kw: [])
    monkeypatch.setattr("spacelabel.platform.spaces_plist.read_spaces", lambda: [])
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
    runner.invoke(cli, _base(cfg, "label", "set", "ORPHAN-UUID", "gone"))
    monkeypatch.setattr(
        "spacelabel.platform.cgs.enumerate_spaces",
        lambda **_kw: [Space(uuid=U1, display_uuid=DISP_A, is_current=True)],
    )
    r = runner.invoke(cli, _base(cfg, "label", "prune"))
    assert r.exit_code == 0
    assert "ORPHAN-UUID" in r.stdout
    r = runner.invoke(cli, _base(cfg, "label", "list"))
    assert "ORPHAN-UUID" not in r.stdout
    assert U1 in r.stdout
