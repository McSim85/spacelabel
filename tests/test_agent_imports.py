"""Import-smoke tests for the AppKit/PyObjC modules (macOS-only).

These confirm every agent module imports cleanly and registers its Objective-C
subclasses without building any UI (UI construction lives in ``__init__``/``show``).
They need PyObjC but no WindowServer; on a non-macOS box PyObjC is absent, so the
whole module is skipped rather than failed (DESIGN.md §12 testing reality).
"""

from __future__ import annotations

import importlib

import pytest

pytest.importorskip("AppKit", reason="PyObjC/AppKit not available (non-macOS)")

AGENT_MODULES = [
    "spacelabel.agent.app",
    "spacelabel.agent.menubar",
    "spacelabel.agent.hud",
    "spacelabel.agent.overlay",
    "spacelabel.agent.wallpaper",
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
    # opens it on click (DECISIONS.md 9.5).
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

    # An unlabeled Space's well is disabled (color is a per-label attribute).
    unlabeled = Space(uuid="1A0F5C2E-7B3D-4C8A-9E1F-2D4B6A8C0E12", display_uuid="D1")
    assert data_source._color_cell(None, unlabeled).isEnabled() is False


def test_wallpaper_purge_keeps_only_current_outputs(tmp_path):
    # The cache purge must delete only stale per-display PNGs we wrote -- never the
    # current outputs, and never non-PNG/other files (originals live elsewhere).
    from spacelabel.agent.wallpaper import WallpaperRenderer

    renderer = WallpaperRenderer()
    renderer._cache_dir = tmp_path
    keep = tmp_path / "display-1.png"
    keep.write_bytes(b"x")
    stale = tmp_path / "display-2.png"
    stale.write_bytes(b"x")
    other = tmp_path / "notes.txt"
    other.write_bytes(b"x")
    renderer._outputs = {"display-1": keep}

    renderer._purge()

    assert keep.exists()  # current output kept
    assert not stale.exists()  # stale output removed
    assert other.exists()  # non-PNG untouched
    # _is_ours distinguishes our composites from the user's real wallpaper file.
    assert renderer._is_ours(str(keep))
    assert not renderer._is_ours("/Users/me/Pictures/wallpaper.jpg")


def test_wallpaper_skips_when_original_unknown(monkeypatch):
    # When the real wallpaper can't be recovered (e.g. after restart, current image
    # is our own composite), skip -- never paint black over / replace the wallpaper.
    from pathlib import Path

    from spacelabel.agent.wallpaper import WallpaperRenderer

    renderer = WallpaperRenderer()
    calls: list[str] = []
    monkeypatch.setattr(renderer, "_base_image_path", lambda _screen: None)
    monkeypatch.setattr(
        renderer, "_render_png", lambda *a, **k: calls.append("render") or Path("x")
    )
    monkeypatch.setattr(renderer, "_set_wallpaper", lambda *a, **k: calls.append("set"))
    monkeypatch.setattr(renderer, "_screen_key", lambda _screen: "display-1")

    renderer.render_and_set("Email", screen=object())
    assert calls == []  # neither rendered nor set -> the real wallpaper is untouched


def test_expected_public_classes_exist():
    from spacelabel.agent.app import run_agent
    from spacelabel.agent.hud import Hud
    from spacelabel.agent.menubar import MenuBarItem
    from spacelabel.agent.overlay import Overlay
    from spacelabel.agent.prefs import PreferencesWindow
    from spacelabel.agent.wallpaper import WallpaperRenderer
    from spacelabel.platform.notifications import SpaceObserver

    assert callable(run_agent)
    for cls in (MenuBarItem, Hud, Overlay, WallpaperRenderer, PreferencesWindow, SpaceObserver):
        assert isinstance(cls, type)
