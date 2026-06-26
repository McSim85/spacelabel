"""Import-smoke tests for the AppKit/PyObjC modules (macOS-only).

These confirm every agent module imports cleanly and registers its Objective-C
subclasses without building any UI (UI construction lives in ``__init__``/``show``).
They need PyObjC but no WindowServer; on a non-macOS box PyObjC is absent, so the
whole module is skipped rather than failed.
"""

from __future__ import annotations

import contextlib
import importlib
import logging

import pytest

pytest.importorskip("AppKit", reason="PyObjC/AppKit not available (non-macOS)")


@contextlib.contextmanager
def _capture_spacelabel_logs(level: int = logging.INFO):
    """Collect ``spacelabel`` log records via a direct handler (test_logging.py pattern).

    The package logger sets ``propagate=False`` once ``setup_logging`` has run, so
    ``caplog`` (which captures via the root logger) can miss records depending on test
    order. Attaching a handler straight to the ``spacelabel`` logger and raising its
    level captures the INFO lifecycle lines deterministically regardless of ordering.
    """
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logger = logging.getLogger("spacelabel")
    prev_level = logger.level
    logger.setLevel(level)
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)


AGENT_MODULES = [
    "spacelabel.agent.app",
    "spacelabel.agent.menubar",
    "spacelabel.agent.hud",
    "spacelabel.agent.overlay",
    "spacelabel.agent.prefs",
    "spacelabel.platform.notifications",
    "spacelabel.platform.switching",
]


@pytest.mark.parametrize("module_name", AGENT_MODULES)
def test_agent_module_imports(module_name):
    module = importlib.import_module(module_name)
    assert module is not None


def test_prefs_datasource_uses_correct_view_based_selector():
    # A view-based NSOutlineView requires EXACTLY this selector; the wrong name
    # ('...byItem:') silently falls back to cell-based mode and renders blank rows.
    from spacelabel.agent.prefs import PrefsDataSource

    data_source = PrefsDataSource.alloc().init()
    assert data_source.respondsToSelector_("outlineView:viewForTableColumn:item:")
    assert not data_source.respondsToSelector_("outlineView:viewForTableColumn:byItem:")


def test_click_to_switch_warning_item_is_visible_and_actionable():
    # The "click-to-switch off" row must be ENABLED (not a grayed/disabled line that
    # is easy to miss) with a colored emoji; when a Settings deep-link is known it
    # opens it on click.
    from spacelabel.agent.app import _SETTINGS_URL_KEYBOARD, AppDelegate

    delegate = AppDelegate.alloc().initWithConfigPath_(None)

    actionable = delegate._click_to_switch_warning_item(
        "shortcut not enabled", _SETTINGS_URL_KEYBOARD
    )
    assert actionable.isEnabled()  # never gray -- the whole point of the fix
    assert "⚠️" in str(actionable.title())  # plain title (no font/attributed AppKit calls)
    assert actionable.action() == "openClickToSwitchSettings:"
    assert str(actionable.representedObject()) == _SETTINGS_URL_KEYBOARD

    # No deep-link known (rare CGS/post failure): still enabled + colored, no action.
    informational = delegate._click_to_switch_warning_item("could not read live Spaces", None)
    assert informational.isEnabled()
    assert informational.action() is None


def test_click_to_switch_reopt_in_clears_stale_disable_before_menu():
    # After a failed click disables the feature, toggling menubar.click_to_switch
    # off->on must clear the disable + reason in _sync_click_to_switch_state(), which
    # _refresh() runs BEFORE _rebuild_menu() -- so the ⚠️ "off" row does not linger for
    # an extra refresh after re-enabling (review P2).
    from spacelabel.agent.app import AppDelegate
    from spacelabel.model import Config

    delegate = AppDelegate.alloc().initWithConfigPath_(None)
    delegate._config = Config()
    delegate._config.menubar.click_to_switch = True
    delegate._click_to_switch_on = True  # feature was already on

    # A failed click left it disabled with a surfaced reason.
    delegate._click_to_switch_available = False
    delegate._click_to_switch_reason = "shortcut not enabled"

    # Toggling OFF must not reset (no off->on edge yet).
    delegate._config.menubar.click_to_switch = False
    delegate._sync_click_to_switch_state()
    assert delegate._click_to_switch_available is False

    # Toggling back ON (the re-opt-in edge) clears the stale disable + reason.
    delegate._config.menubar.click_to_switch = True
    delegate._sync_click_to_switch_state()
    assert delegate._click_to_switch_available is True
    assert delegate._click_to_switch_reason is None


def test_find_active_space_resolves_default_unlabelable_desktop(monkeypatch):
    # Item AA: when the focused display sits on its default unlabelable Space (uuid=""),
    # _find_active_space returns THAT Space (the is_current Space on the active display),
    # so the title shows its "Desktop N" -- not the first current Space on another
    # display (which the old first-current fallback would wrongly pick).
    from spacelabel.agent.app import AppDelegate
    from spacelabel.model import Space
    from spacelabel.platform import cgs

    delegate = AppDelegate.alloc().initWithConfigPath_(None)
    active = "899EDEF9-1840-4DE5-A049-D7FFA8ECEB7A"
    other = "874A623F-F8F5-43C1-B11C-4AAC3E383C0F"
    current_on_other = Space(
        uuid="6622AC87-2FD2-48E8-934D-F6EB303AC9BA", display_uuid=other, is_current=True, id64=9
    )
    default_on_active = Space(uuid="", display_uuid=active, is_current=True, id64=1)
    # 'other' is first in the list, so the old first-current fallback would mispick it.
    spaces = [current_on_other, default_on_active]
    monkeypatch.setattr(cgs, "active_display_uuid", lambda: active)

    assert delegate._find_active_space(spaces) is default_on_active
    # Active display KNOWN but its current Space is filtered out (a fullscreen/tiled
    # Space is not in `spaces`): no is_current Space on it -> neutral (None), never
    # another display's Space (item Z's neutral case).
    monkeypatch.setattr(cgs, "active_display_uuid", lambda: "CCCCFFFF-no-current-here")
    assert delegate._find_active_space(spaces) is None
    # Only an UNRESOLVABLE active display falls back to the first current Space.
    monkeypatch.setattr(cgs, "active_display_uuid", lambda: "")
    assert delegate._find_active_space(spaces) is current_on_other


def test_prefs_color_well_persists_to_store(tmp_path):
    # Picking a color in the prefs color well must write it onto the Space's label.
    from AppKit import NSColor

    from spacelabel import labeling, store
    from spacelabel.agent.prefs import PrefsDataSource, _DisplayNode
    from spacelabel.model import Display, Space

    paths = store.StorePaths.resolve(tmp_path / "config.json")
    uuid = "6622AC87-2FD2-48E8-934D-F6EB303AC9BA"
    store.set_label(paths, uuid, "Email")

    data_source = PrefsDataSource.alloc().init()
    space = Space(uuid=uuid, display_uuid="D1")
    node = _DisplayNode(Display(uuid="D1", cg_display_id=1), "D1", [space])
    data_source.set_nodes(
        [node], store.load_labels(paths), labeling.assign_ordinals([space]), paths
    )

    well = data_source._color_cell(None, space)
    assert well.space_uuid() == uuid
    assert well.isEnabled()
    well.setColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.5, 0.0, 1.0))
    data_source.colorChanged_(well)  # simulate the color-well action
    assert store.load_labels(paths)[uuid].color == "#ff8000"

    # An unlabeled Space: well stays ENABLED (so activate_ fires and shows the
    # "Set a label first" sheet), but has no space_uuid bound (no target/action).
    unlabeled = Space(uuid="1A0F5C2E-7B3D-4C8A-9E1F-2D4B6A8C0E12", display_uuid="D1")
    unlabeled_well = data_source._color_cell(None, unlabeled)
    assert unlabeled_well.isEnabled()  # enabled so AppKit dispatches activate_
    assert unlabeled_well.space_uuid() == ""  # no UUID -> explanation sheet, not picker


def test_prefs_color_well_enabled_for_notes_only_space(tmp_path):
    # A notes-only Space (notes but no label, Label.text == "") is unlabeled: the
    # color well must stay ENABLED so activate_ fires and shows the "Set a label first"
    # sheet — setEnabled_(False) would prevent AppKit from dispatching activate_ at all,
    # leaving clicks silently ignored (DECISIONS 9.10 / T-4 finding).
    from spacelabel import labeling, store
    from spacelabel.agent.prefs import PrefsDataSource, _DisplayNode
    from spacelabel.model import Display, Space

    paths = store.StorePaths.resolve(tmp_path / "config.json")
    uuid = "6622AC87-2FD2-48E8-934D-F6EB303AC9BA"
    store.add_note(paths, uuid, "a task")  # notes-only entry (no label)

    data_source = PrefsDataSource.alloc().init()
    space = Space(uuid=uuid, display_uuid="D1")
    node = _DisplayNode(Display(uuid="D1", cg_display_id=1), "D1", [space])
    data_source.set_nodes(
        [node], store.load_labels(paths), labeling.assign_ordinals([space]), paths
    )
    well = data_source._color_cell(None, space)
    assert well.isEnabled()  # enabled so AppKit dispatches activate_
    assert well.space_uuid() == ""  # no UUID bound -> explanation sheet, not picker


def test_prefs_load_tree_counts_default_desktop(tmp_path, monkeypatch):
    # Item V: Preferences must number "Desktop N" over the FULL enumeration (incl. a
    # display's default uuid="" Space) so it matches the menu-bar pill + switch path.
    # The default Space is counted but NOT shown as a row (it can't be labeled). A
    # regression to the labelable-only enumeration would number on_4k as Desktop 1.
    from spacelabel.agent.prefs import PreferencesWindow
    from spacelabel.model import Display, Space
    from spacelabel.platform import cgs, displays

    default = Space(uuid="", display_uuid="D1")  # the 4K display's default desktop
    on_4k = Space(uuid="6622AC87-2FD2-48E8-934D-F6EB303AC9BA", display_uuid="D1")
    on_portrait = Space(uuid="1A0F5C2E-7B3D-4C8A-9E1F-2D4B6A8C0E12", display_uuid="D2")

    def fake_enumerate(*, include_unlabelable=False):
        spaces = [default, on_4k, on_portrait]
        return spaces if include_unlabelable else [s for s in spaces if s.uuid]

    monkeypatch.setattr(cgs, "enumerate_spaces", fake_enumerate)
    monkeypatch.setattr(
        displays,
        "discover_topology",
        lambda: [Display(uuid="D1", cg_display_id=1), Display(uuid="D2", cg_display_id=2)],
    )

    window = PreferencesWindow(config_path=tmp_path / "config.json")
    nodes, _labels, ordinals, _paths, _overlay_disabled = window._load_tree()

    # The default desktop is counted, so the 4K's labelable Space is Desktop 2 (matches
    # the pill), and the portrait's first is Desktop 3.
    assert ordinals[id(on_4k)] == 2
    assert ordinals[id(on_portrait)] == 3
    # ...but the unlabelable default Space is never shown as a tree row.
    shown = [space for node in nodes for space in node.spaces]
    assert default not in shown
    assert on_4k in shown
    assert on_portrait in shown


def test_update_overlays_orders_out_when_current_is_none(monkeypatch, tmp_path):
    # Z: when the current Space on a display is a fullscreen/tiled app (filtered out
    # by enumerate_spaces, so current=None), the display's overlay must be ordered out
    # rather than left stale from the previous render.
    from spacelabel.agent.app import AppDelegate
    from spacelabel.model import Display, Space
    from spacelabel.platform import cgs, displays

    delegate = AppDelegate.alloc().initWithConfigPath_(tmp_path / "config.json")
    # Enable overlay mode so _update_overlays actually runs.
    delegate._config = delegate._load_config()
    delegate._config.modes["overlay"] = True

    disp_uuid = "874A623F-F8F5-43C1-B11C-4AAC3E383C0F"
    # The display has a space but no current (fullscreen swallowed it).
    spaces = [Space(uuid="6622AC87-2FD2-48E8-934D-F6EB303AC9BA", display_uuid=disp_uuid)]

    # A pre-existing overlay for the display (simulating a previous render).
    ordered_out: list[str] = []

    class _FakeOverlay:
        def order_out(self) -> None:
            ordered_out.append(disp_uuid)

    delegate._overlays[disp_uuid] = _FakeOverlay()  # type: ignore[assignment]

    monkeypatch.setattr(
        displays, "discover_topology", lambda: [Display(uuid=disp_uuid, cg_display_id=1)]
    )
    monkeypatch.setattr(cgs, "enumerate_spaces", lambda **kw: spaces)

    ordinals = {id(spaces[0]): 1}
    delegate._update_overlays(spaces, ordinals)

    assert disp_uuid in ordered_out, "order_out must be called when current is None"


def test_update_overlays_orders_out_for_per_display_disabled(monkeypatch, tmp_path):
    # P: when a display's overlay is toggled off in displays.json, _update_overlays
    # must order-out that display's panel and not render a new one.
    from spacelabel import store
    from spacelabel.agent.app import AppDelegate
    from spacelabel.model import Display, Space
    from spacelabel.platform import cgs, displays

    delegate = AppDelegate.alloc().initWithConfigPath_(tmp_path / "config.json")
    delegate._config = delegate._load_config()
    delegate._config.modes["overlay"] = True

    disp_uuid = "874A623F-F8F5-43C1-B11C-4AAC3E383C0F"
    sp = Space(
        uuid="6622AC87-2FD2-48E8-934D-F6EB303AC9BA",
        display_uuid=disp_uuid,
        is_current=True,
    )
    spaces = [sp]

    # Persist the display as overlay-disabled in the store.
    paths = store.StorePaths.resolve(tmp_path / "config.json")
    store.set_display_overlay_enabled(paths, disp_uuid, False)

    ordered_out: list[str] = []

    class _FakeOverlay:
        def order_out(self) -> None:
            ordered_out.append(disp_uuid)

    delegate._overlays[disp_uuid] = _FakeOverlay()  # type: ignore[assignment]

    monkeypatch.setattr(
        displays, "discover_topology", lambda: [Display(uuid=disp_uuid, cg_display_id=1)]
    )
    monkeypatch.setattr(cgs, "enumerate_spaces", lambda **kw: spaces)

    ordinals = {id(sp): 1}
    delegate._update_overlays(spaces, ordinals)

    assert disp_uuid in ordered_out, "order_out must be called when per-display overlay is off"


def test_update_overlays_orders_out_for_unlabeled_when_flag_set(monkeypatch, tmp_path):
    # Q: when overlay.hide_on_unlabeled is True, a current Space with no user label
    # must cause the overlay to be ordered out rather than show "Desktop N".
    from spacelabel.agent.app import AppDelegate
    from spacelabel.model import Display, Space
    from spacelabel.platform import cgs, displays

    delegate = AppDelegate.alloc().initWithConfigPath_(tmp_path / "config.json")
    delegate._config = delegate._load_config()
    delegate._config.modes["overlay"] = True
    delegate._config.overlay.hide_on_unlabeled = True

    disp_uuid = "874A623F-F8F5-43C1-B11C-4AAC3E383C0F"
    sp = Space(
        uuid="6622AC87-2FD2-48E8-934D-F6EB303AC9BA",
        display_uuid=disp_uuid,
        is_current=True,
    )
    spaces = [sp]
    # No label for this space -> "Desktop 1" placeholder.

    ordered_out: list[str] = []

    class _FakeOverlay:
        def order_out(self) -> None:
            ordered_out.append(disp_uuid)

    delegate._overlays[disp_uuid] = _FakeOverlay()  # type: ignore[assignment]

    monkeypatch.setattr(
        displays, "discover_topology", lambda: [Display(uuid=disp_uuid, cg_display_id=1)]
    )
    monkeypatch.setattr(cgs, "enumerate_spaces", lambda **kw: spaces)

    ordinals = {id(sp): 1}
    delegate._update_overlays(spaces, ordinals)

    assert disp_uuid in ordered_out, "order_out must be called when hide_on_unlabeled + no label"


def test_update_overlays_does_not_hide_notes_only_when_flag_set(monkeypatch, tmp_path):
    # Q exception (DECISIONS 9.10): a Space with notes but no label is still user
    # content; hide_on_unlabeled must NOT suppress its overlay.
    from spacelabel.agent.app import AppDelegate
    from spacelabel.model import Display, Label, Note, Space
    from spacelabel.platform import cgs, displays

    delegate = AppDelegate.alloc().initWithConfigPath_(tmp_path / "config.json")
    delegate._config = delegate._load_config()
    delegate._config.modes["overlay"] = True
    delegate._config.overlay.hide_on_unlabeled = True

    disp_uuid = "874A623F-F8F5-43C1-B11C-4AAC3E383C0F"
    sp_uuid = "6622AC87-2FD2-48E8-934D-F6EB303AC9BA"
    sp = Space(uuid=sp_uuid, display_uuid=disp_uuid, is_current=True)
    spaces = [sp]
    # Notes-only: no label text, but has tasks.
    delegate._labels = {sp_uuid: Label(text="", notes=[Note(text="buy milk")])}

    ordered_out: list[str] = []

    class _FakeOverlay:
        def order_out(self) -> None:
            ordered_out.append(disp_uuid)

        def set_font(self, *a, **kw):
            pass

        def reposition(self, *a, **kw):
            pass

        def set_content(self, *a, **kw):
            pass

    delegate._overlays[disp_uuid] = _FakeOverlay()  # type: ignore[assignment]

    monkeypatch.setattr(
        displays, "discover_topology", lambda: [Display(uuid=disp_uuid, cg_display_id=1)]
    )
    monkeypatch.setattr(cgs, "enumerate_spaces", lambda **kw: spaces)

    ordinals = {id(sp): 1}
    delegate._update_overlays(spaces, ordinals)

    assert disp_uuid not in ordered_out, "notes-only overlay must not be suppressed"


def test_update_hud_suppressed_when_no_active_space(tmp_path):
    # Z: _update_hud must not show the HUD when active_space is None (fullscreen).
    from spacelabel.agent.app import AppDelegate

    delegate = AppDelegate.alloc().initWithConfigPath_(tmp_path / "config.json")
    delegate._config = delegate._load_config()
    delegate._config.modes["hud"] = True

    shown: list[str] = []

    class _FakeHud:
        def show(self, text, **_kw):
            shown.append(text)

    delegate._hud = _FakeHud()  # type: ignore[assignment]

    delegate._update_hud("irrelevant-title", active_space=None)
    assert not shown, "_update_hud must not show when active_space is None"


def test_update_hud_suppressed_when_no_uuid_space(tmp_path):
    # Z: _update_hud must not show the HUD for the default no-UUID Space (uuid=""),
    # because is_labelable returns False for it.
    from spacelabel.agent.app import AppDelegate
    from spacelabel.model import Space

    delegate = AppDelegate.alloc().initWithConfigPath_(tmp_path / "config.json")
    delegate._config = delegate._load_config()
    delegate._config.modes["hud"] = True

    shown: list[str] = []

    class _FakeHud:
        def show(self, text, **_kw):
            shown.append(text)

    delegate._hud = _FakeHud()  # type: ignore[assignment]

    no_uuid_space = Space(uuid="", display_uuid="D1", is_current=True)
    delegate._update_hud("Desktop 1", active_space=no_uuid_space)
    assert not shown, "_update_hud must not show for the default no-UUID Space"


def test_overlay_exposes_set_content_and_distinct_glyphs():
    # Overlay rendering needs a WindowServer (the panel creates its window device),
    # so it is import-smoke only. Verify the notes-render entry
    # point exists and the two checkbox glyphs differ, without building the panel.
    from spacelabel.agent import overlay as overlay_mod

    assert callable(overlay_mod.Overlay.set_content)
    assert overlay_mod._GLYPH_DONE != overlay_mod._GLYPH_TODO


def test_expected_public_classes_exist():
    from spacelabel.agent.app import run_agent
    from spacelabel.agent.hud import Hud
    from spacelabel.agent.menubar import MenuBarItem
    from spacelabel.agent.overlay import Overlay
    from spacelabel.agent.prefs import PreferencesWindow
    from spacelabel.platform.notifications import SpaceObserver

    assert callable(run_agent)
    for cls in (MenuBarItem, Hud, Overlay, PreferencesWindow, SpaceObserver):
        assert isinstance(cls, type)


def test_is_managed_run_only_default_config_nondev_noninteractive():
    from pathlib import Path

    from spacelabel.agent.app import _is_managed_run

    base = {"verbose": False, "debug": False, "interactive": False}
    # Production login agent: default config, no dev flags, no TTY (launchd).
    assert _is_managed_run(None, **base) is True
    # A manual run from a terminal (TTY) is NOT the managed agent.
    assert _is_managed_run(None, verbose=False, debug=False, interactive=True) is False
    # Dev flags are never managed (no plist mutation / boot-log truncation / hard-exit).
    assert _is_managed_run(None, verbose=True, debug=False, interactive=False) is False
    assert _is_managed_run(None, verbose=False, debug=True, interactive=False) is False
    # A --config run targets a throwaway store and must never touch shared artifacts.
    assert _is_managed_run(Path("/tmp/x.json"), **base) is False


def test_acquire_single_instance_lock_loser_exits_zero(tmp_path):
    # Item AB: a second agent that loses the single-instance lock-race must exit 0 (a
    # duplicate is not a crash), so launchd KeepAlive (SuccessfulExit:false) does NOT
    # relaunch it into a restart loop after a `brew upgrade` double-start. The winner's
    # recorded pid/config in agent.lock must survive the loser's failed attempt.
    import os

    import pytest as _pytest

    from spacelabel.agent.app import _acquire_single_instance_lock

    config_path = tmp_path / "config.json"
    winner = _acquire_single_instance_lock(config_path)  # holds the lock + records our pid
    try:
        recorded_before = (tmp_path / "agent.lock").read_text()
        assert recorded_before.startswith(f"{os.getpid()}\n")  # winner recorded its pid

        # A second acquire (a distinct open file description) contends and, after the
        # brief retry window, must raise SystemExit(0) -- not 1.
        with _pytest.raises(SystemExit) as excinfo:
            _acquire_single_instance_lock(config_path)
        assert excinfo.value.code == 0

        # The loser opened agent.lock with "a+" and never truncated, so the winner's
        # recorded pid/config line is left intact for `spacelabel status`.
        assert (tmp_path / "agent.lock").read_text() == recorded_before
    finally:
        winner.close()  # release the advisory lock


def test_topology_signature_detects_reorder_create_delete():
    from spacelabel.agent.app import _topology_signature
    from spacelabel.model import Space

    d = "DISP-UUID"
    a = Space(uuid="A", display_uuid=d, id64=1, is_current=True)
    b = Space(uuid="B", display_uuid=d, id64=2)
    c = Space(uuid="C", display_uuid=d, id64=3)

    base = _topology_signature([a, b, c])
    assert _topology_signature([a, b, c]) == base  # stable -> no refresh
    assert _topology_signature([b, a, c]) != base  # reorder
    assert _topology_signature([a, b]) != base  # delete
    assert _topology_signature([a, b, c, Space(uuid="D", display_uuid=d, id64=4)]) != base  # create


def test_topology_signature_ignores_current_space_change():
    # A pure active-Space change must NOT change the signature: space switches are the
    # observer's (activeSpaceDidChange) job, and is_current is unreliable per-tick.
    from spacelabel.agent.app import _topology_signature
    from spacelabel.model import Space

    d = "DISP-UUID"
    before = [
        Space(uuid="A", display_uuid=d, id64=1, is_current=True),
        Space(uuid="B", display_uuid=d, id64=2),
    ]
    after = [
        Space(uuid="A", display_uuid=d, id64=1),
        Space(uuid="B", display_uuid=d, id64=2, is_current=True),
    ]
    assert _topology_signature(after) == _topology_signature(before)


def test_topology_signature_distinguishes_unlabelable_by_id64():
    # Two no-UUID (uuid == "") desktops on one display stay distinct via id64, so
    # reordering them is still detected (uuid alone would collapse them).
    from spacelabel.agent.app import _topology_signature
    from spacelabel.model import Space

    d = "DISP-UUID"
    u1 = Space(uuid="", display_uuid=d, id64=10)
    u2 = Space(uuid="", display_uuid=d, id64=11)
    assert _topology_signature([u1, u2]) != _topology_signature([u2, u1])


def test_agent_log_dir_global_for_default_store_per_store_for_custom(tmp_path):
    # F3: the default store logs to the shared logs_dir (None -> setup_logging's default); a
    # genuinely custom --config logs to its OWN store dir, so a default `uninstall --purge`
    # can never pull a live custom-config agent's logs out from under it.
    from spacelabel import store
    from spacelabel.agent.app import _agent_log_dir

    assert _agent_log_dir(store.StorePaths.resolve(None)) is None  # default -> shared logs_dir
    custom = store.StorePaths.resolve(tmp_path / "other.json")
    assert _agent_log_dir(custom) == custom.directory  # custom -> its own store dir


def test_is_interactive_tolerates_closed_stream(monkeypatch):
    import sys as _sys

    from spacelabel.agent.app import _is_interactive

    class _Closed:
        def isatty(self):
            raise ValueError("I/O operation on closed file")

    # A closed stdio stream must not crash the probe; absence of a TTY -> False.
    monkeypatch.setattr(_sys, "stdin", _Closed())
    assert _is_interactive() is False


def test_accessibility_reason_branches_stale_vs_never_granted(tmp_path, monkeypatch):
    # Acceptance (item L): when AXIsProcessTrusted is False (the only time this runs),
    # a STALE grant -- cdhash changed since we were last trusted, OR ax_was_trusted was
    # recorded -- yields REMOVE-and-re-add guidance; a first-ever run yields the plain
    # "enable" message. Drives the real delegate decision with the persisted cdhash/ax
    # state seeded in a tmp store and the process cdhash mocked.
    from spacelabel import store
    from spacelabel.agent.app import AppDelegate
    from spacelabel.model import AgentState
    from spacelabel.platform import switching

    paths = store.StorePaths.resolve(tmp_path / "config.json")
    delegate = AppDelegate.alloc().initWithConfigPath_(None)
    delegate._paths = paths

    # (1) Never granted: no recorded state + a readable cdhash -> plain "enable".
    monkeypatch.setattr(switching, "code_signature_hash", lambda: "CDHASH-NEW")
    never = delegate._accessibility_reason()
    assert "permission is required" in never
    assert "REMOVE" not in never and "went stale" not in never

    # (2) Stale via cdhash change alone (ax flag still False): an app update rotated the
    # ad-hoc signature since the checkpoint -> remove-and-re-add.
    store.save_agent_state(paths, AgentState(last_cdhash="CDHASH-OLD", ax_was_trusted=False))
    cdhash_stale = delegate._accessibility_reason()
    assert "REMOVE" in cdhash_stale and "went stale" in cdhash_stale

    # (3) Stale via ax_was_trusted even when the signature can't be read (None cdhash).
    store.save_agent_state(paths, AgentState(last_cdhash=None, ax_was_trusted=True))
    monkeypatch.setattr(switching, "code_signature_hash", lambda: None)
    assert "REMOVE" in delegate._accessibility_reason()


def test_record_ax_trusted_persists_checkpoint(tmp_path, monkeypatch):
    # A successful AX check checkpoints (cdhash, ax_was_trusted=True) so a LATER failure
    # is classified stale; the write is best-effort and only-on-change.
    from spacelabel import store
    from spacelabel.agent.app import AppDelegate
    from spacelabel.model import AgentState
    from spacelabel.platform import switching

    paths = store.StorePaths.resolve(tmp_path / "config.json")
    delegate = AppDelegate.alloc().initWithConfigPath_(None)
    delegate._paths = paths

    monkeypatch.setattr(switching, "code_signature_hash", lambda: "CDHASH-CURRENT")
    delegate._record_ax_trusted()
    assert store.load_agent_state(paths) == AgentState(
        last_cdhash="CDHASH-CURRENT", ax_was_trusted=True
    )

    # Idempotent: re-recording the same checkpoint must not raise (write skipped).
    delegate._record_ax_trusted()
    assert store.load_agent_state(paths).last_cdhash == "CDHASH-CURRENT"


def test_install_edit_menu_creates_main_menu_with_paste():
    # Item U: _install_edit_menu appends an Edit submenu with standard key-equivalent
    # items so Cmd+V/C/X/Z dispatch to text-field editors. It must be ADDITIVE — it
    # must not replace any menu items that AppHelper or AppKit already installed.
    from AppKit import NSApplication, NSMenu, NSMenuItem

    from spacelabel.agent.app import AppDelegate

    # Pre-seed the main menu to prove additivity.
    app = NSApplication.sharedApplication()
    pre_menu = NSMenu.alloc().init()
    pre_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("AppMenu", "", "")
    pre_menu.addItem_(pre_item)
    app.setMainMenu_(pre_menu)

    delegate = AppDelegate.alloc().initWithConfigPath_(None)
    delegate._install_edit_menu()

    main_menu = app.mainMenu()
    assert main_menu is not None
    assert main_menu.itemWithTitle_("AppMenu") is not None  # pre-existing item preserved
    edit_item = main_menu.itemWithTitle_("Edit")
    assert edit_item is not None
    edit_menu = edit_item.submenu()
    assert edit_menu is not None
    assert edit_menu.itemWithTitle_("Paste") is not None
    assert edit_menu.itemWithTitle_("Copy") is not None
    assert edit_menu.itemWithTitle_("Cut") is not None
    assert edit_menu.itemWithTitle_("Undo") is not None


def test_toggle_click_to_switch_writes_config(tmp_path):
    # Item J: toggleClickToSwitch_ flips menubar.click_to_switch in the store
    # and reloads config (mirrors toggleMode_ for the non-mode setting).
    from spacelabel import store
    from spacelabel.agent.app import AppDelegate
    from spacelabel.model import Config

    paths = store.StorePaths.resolve(tmp_path / "config.json")
    delegate = AppDelegate.alloc().initWithConfigPath_(tmp_path / "config.json")
    delegate._paths = paths
    delegate._config = Config()
    assert delegate._config.menubar.click_to_switch is False

    # Stub _refresh so we can call the action without a full agent running.
    refreshed: list[bool] = []
    delegate._refresh = lambda: refreshed.append(True)

    delegate.toggleClickToSwitch_(None)

    loaded = store.load_config(paths)
    assert loaded.menubar.click_to_switch is True
    assert refreshed  # a refresh was triggered


def test_prefs_commit_callback_fires_after_clear(tmp_path):
    # Item U: after clearing a Space label the data source calls _on_commit so
    # the outline row live-reverts to "Desktop N" without reopening the window.
    from spacelabel import labeling, store
    from spacelabel.agent.prefs import PrefsDataSource, _DisplayNode
    from spacelabel.model import Display, Space

    paths = store.StorePaths.resolve(tmp_path / "config.json")
    uuid = "6622AC87-2FD2-48E8-934D-F6EB303AC9BA"
    store.set_label(paths, uuid, "Email")

    data_source = PrefsDataSource.alloc().init()
    space = Space(uuid=uuid, display_uuid="D1")
    node = _DisplayNode(Display(uuid="D1", cg_display_id=1), "D1", [space])
    data_source.set_nodes(
        [node], store.load_labels(paths), labeling.assign_ordinals([space]), paths
    )

    callbacks: list[int] = []
    data_source.set_on_commit(lambda: callbacks.append(1))

    data_source._commit("space", uuid, "")  # clear the label

    # The label must be gone from the store.
    assert store.load_labels(paths).get(uuid) is None

    # The NSTimer fires on the next run-loop cycle, so we can't poll it in a unit
    # test without a live run loop. Verify that the callback IS wired (not None)
    # so the deferred timer is scheduled, and that a direct call works correctly.
    assert data_source._on_commit is not None
    data_source._on_commit()  # simulate the timer firing
    assert len(callbacks) == 1


def test_install_edit_menu_idempotent():
    # Item U: calling _install_edit_menu twice must not produce duplicate Edit submenus.
    from AppKit import NSApplication, NSMenu, NSMenuItem

    from spacelabel.agent.app import AppDelegate

    app = NSApplication.sharedApplication()
    base = NSMenu.alloc().init()
    base.addItem_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("App", "", ""))
    app.setMainMenu_(base)

    delegate = AppDelegate.alloc().initWithConfigPath_(None)
    delegate._install_edit_menu()
    delegate._install_edit_menu()  # second call must be a no-op

    menu = app.mainMenu()
    assert menu is not None
    edit_count = sum(
        1 for i in range(menu.numberOfItems()) if str(menu.itemAtIndex_(i).title()) == "Edit"
    )
    assert edit_count == 1  # exactly one Edit menu, never two


def test_sync_cts_state_noop_before_build():
    # Item J P3: sync_cts_state must be a silent no-op when the window
    # has never been built (_cts_button is None) — called from toggleClickToSwitch_
    # before the user has ever opened Preferences.
    from spacelabel.agent.prefs import PreferencesWindow

    window = PreferencesWindow()
    window.sync_cts_state()  # must not raise even with _cts_button = None


def test_sync_cts_state_updates_button(tmp_path):
    # Item J P3: after a config write, sync_cts_state updates the checkbox state.
    from spacelabel import store
    from spacelabel.agent.prefs import PreferencesWindow

    paths = store.StorePaths.resolve(tmp_path / "config.json")
    window = PreferencesWindow(config_path=tmp_path / "config.json")

    # Simulate a built button: plant a mock with a recordable setState_.
    calls: list[int] = []

    class _MockBtn:
        def setState_(self, v: int) -> None:  # noqa: N802
            calls.append(v)

    window._cts_button = _MockBtn()

    # Start false, write true, sync.
    store.set_config_value(paths, "menubar.click_to_switch", "true")
    window.sync_cts_state()

    from AppKit import NSControlStateValueOn

    assert calls == [NSControlStateValueOn]


def test_application_will_terminate_logs_clean_stop():
    # Lifecycle tracking: a clean NSApplication termination (menu Quit / cask `quit:`)
    # logs an "agent stopping" INFO line. Paired with run_agent's "agent starting", a
    # start with no preceding stop in agent.log means killed/crashed, not quit.
    from spacelabel.agent.app import AppDelegate

    delegate = AppDelegate.alloc().initWithConfigPath_(None)
    with _capture_spacelabel_logs() as records:
        delegate.applicationWillTerminate_(None)
    assert any("agent stopping" in r.getMessage() for r in records)


def test_poll_reload_logs_config_reload_reason(tmp_path, monkeypatch):
    # Config-reload tracking: when config.json's mtime changes, _poll_reload logs the
    # reason at INFO. mtimes are stubbed so exactly the config branch fires (others
    # stable; the every-3-tick CGS topology read is skipped on the first tick).
    from spacelabel.agent import app as app_mod
    from spacelabel.agent.app import AppDelegate
    from spacelabel.model import Config

    delegate = AppDelegate.alloc().initWithConfigPath_(tmp_path / "config.json")
    delegate._config = Config()
    delegate._refresh = lambda: None  # don't build AppKit surfaces in a unit test

    watched = {
        str(delegate._paths.config_file): 1.0,
        str(delegate._paths.labels_file): 1.0,
        str(delegate._paths.displays_file): 1.0,
        str(delegate._spaces_plist_path): 1.0,
    }
    monkeypatch.setattr(app_mod, "_mtime", lambda p: watched.get(str(p), 0.0))
    delegate._config_mtime = 1.0
    delegate._labels_mtime = 1.0
    delegate._displays_mtime = 1.0
    delegate._spaces_plist_mtime = 1.0

    watched[str(delegate._paths.config_file)] = 2.0  # only config.json changed
    with _capture_spacelabel_logs() as records:
        delegate._poll_reload(None)
    messages = [r.getMessage() for r in records]
    assert any("config reloaded: config.json changed" in m for m in messages)
    # The unchanged stores must NOT log a reload reason.
    assert not any("labels reloaded" in m for m in messages)


def test_poll_reload_logs_labels_reload_reason(tmp_path, monkeypatch):
    # Same harness, labels.json branch: logs the reason + the loaded entry count.
    from spacelabel.agent import app as app_mod
    from spacelabel.agent.app import AppDelegate
    from spacelabel.model import Config

    delegate = AppDelegate.alloc().initWithConfigPath_(tmp_path / "config.json")
    delegate._config = Config()
    delegate._refresh = lambda: None

    watched = {
        str(delegate._paths.config_file): 1.0,
        str(delegate._paths.labels_file): 1.0,
        str(delegate._paths.displays_file): 1.0,
        str(delegate._spaces_plist_path): 1.0,
    }
    monkeypatch.setattr(app_mod, "_mtime", lambda p: watched.get(str(p), 0.0))
    delegate._config_mtime = 1.0
    delegate._labels_mtime = 1.0
    delegate._displays_mtime = 1.0
    delegate._spaces_plist_mtime = 1.0

    watched[str(delegate._paths.labels_file)] = 2.0  # only labels.json changed
    with _capture_spacelabel_logs() as records:
        delegate._poll_reload(None)
    assert any("labels reloaded: labels.json changed" in r.getMessage() for r in records)


def test_poll_reload_does_not_spam_spaces_changed_on_repeated_ticks(tmp_path, monkeypatch):
    # codex P2: the spaces-plist branch commits its baseline only in _refresh_impl, so a
    # stuck/failed refresh would re-enter it every tick. The "Spaces changed" INFO must
    # log once per distinct mtime, not once per tick. Here _refresh is a no-op (the
    # baseline never advances), so two polls with the same changed mtime yield exactly
    # one log line — proving the "announced mtime" gate.
    from spacelabel.agent import app as app_mod
    from spacelabel.agent.app import AppDelegate
    from spacelabel.model import Config

    delegate = AppDelegate.alloc().initWithConfigPath_(tmp_path / "config.json")
    delegate._config = Config()
    delegate._refresh = lambda: None  # render that never commits the baseline

    watched = {
        str(delegate._paths.config_file): 1.0,
        str(delegate._paths.labels_file): 1.0,
        str(delegate._paths.displays_file): 1.0,
        str(delegate._spaces_plist_path): 1.0,
    }
    monkeypatch.setattr(app_mod, "_mtime", lambda p: watched.get(str(p), 0.0))
    delegate._config_mtime = 1.0
    delegate._labels_mtime = 1.0
    delegate._displays_mtime = 1.0
    delegate._spaces_plist_mtime = 1.0
    delegate._spaces_plist_announced_mtime = 1.0

    watched[str(delegate._spaces_plist_path)] = 2.0  # a create/delete bumps the plist
    with _capture_spacelabel_logs() as records:
        delegate._poll_reload(None)  # tick 1: detect + log once
        delegate._poll_reload(None)  # tick 2: baseline still stale -> must NOT re-log
    spaces_logs = [r for r in records if "Spaces changed" in r.getMessage()]
    assert len(spaces_logs) == 1, f"expected one 'Spaces changed' line, got {len(spaces_logs)}"


def test_rebuild_menu_appends_disabled_version_footer(tmp_path, monkeypatch):
    # Part 3: the dropdown ends with a dimmed, disabled "spacelabel v<version>" row so
    # the running build is visible at a glance. Uses a fake menubar to capture the items
    # without creating a real NSStatusItem.
    from spacelabel import __version__
    from spacelabel.agent.app import AppDelegate
    from spacelabel.model import Config
    from spacelabel.platform import displays

    delegate = AppDelegate.alloc().initWithConfigPath_(tmp_path / "config.json")
    delegate._config = Config()
    delegate._labels = {}

    captured: dict[str, list] = {}

    class _FakeMenubar:
        def set_menu_items(self, items):
            captured["items"] = items

    delegate._menubar = _FakeMenubar()  # type: ignore[assignment]
    monkeypatch.setattr(displays, "discover_topology", lambda: [])

    delegate._rebuild_menu([], {})

    items = captured["items"]
    footer = items[-1]
    assert str(footer.title()) == f"spacelabel v{__version__}"
    assert footer.isEnabled() is False
