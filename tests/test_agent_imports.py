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


def test_prefs_color_well_disabled_for_notes_only_space(tmp_path):
    # A notes-only Space (notes but no label, Label.text == "") is unlabeled: the
    # color well must stay disabled — color is a per-label attribute (DECISIONS 9.10).
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
    assert data_source._color_cell(None, space).isEnabled() is False


def test_wallpaper_is_ours_distinguishes_cache_from_real(tmp_path):
    # _is_ours distinguishes our cache composites/copies from the user's real
    # wallpaper file -- the guard behind the label-on-label / recovery logic. The
    # full TTL-eviction + restart-recovery behavior is covered in test_wallpaper.py.
    from spacelabel.agent.wallpaper import WallpaperRenderer

    renderer = WallpaperRenderer(cache_dir=tmp_path)
    assert renderer._is_ours(str(tmp_path / "display-1.png"))
    assert renderer._is_ours(str(tmp_path / "original-UUID-A.png"))
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


def test_overlay_exposes_set_content_and_distinct_glyphs():
    # Overlay rendering needs a WindowServer (the panel creates its window device),
    # so it is import-smoke only (docs/TESTING.md). Verify the notes-render entry
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
    from spacelabel.agent.wallpaper import WallpaperRenderer
    from spacelabel.platform.notifications import SpaceObserver

    assert callable(run_agent)
    for cls in (MenuBarItem, Hud, Overlay, WallpaperRenderer, PreferencesWindow, SpaceObserver):
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


def test_topology_signature_detects_reorder_create_delete_current():
    from spacelabel.agent.app import _topology_signature
    from spacelabel.model import Space

    d = "DISP-UUID"
    a = Space(uuid="A", display_uuid=d, is_current=True)
    b = Space(uuid="B", display_uuid=d)
    c = Space(uuid="C", display_uuid=d)

    base = _topology_signature([a, b, c])
    # Same topology, same objects/order -> equal signature (no refresh).
    assert _topology_signature([a, b, c]) == base
    # Reorder (same set of UUIDs, different order) -> different signature.
    assert _topology_signature([b, a, c]) != base
    # Create / delete (membership change) -> different signature.
    assert _topology_signature([a, b]) != base
    assert _topology_signature([a, b, c, Space(uuid="D", display_uuid=d)]) != base
    # Current-Space change only -> different signature.
    moved_current = [
        Space(uuid="A", display_uuid=d),
        Space(uuid="B", display_uuid=d, is_current=True),
        Space(uuid="C", display_uuid=d),
    ]
    assert _topology_signature(moved_current) != base


def test_is_interactive_tolerates_closed_stream(monkeypatch):
    import sys as _sys

    from spacelabel.agent.app import _is_interactive

    class _Closed:
        def isatty(self):
            raise ValueError("I/O operation on closed file")

    # A closed stdio stream must not crash the probe; absence of a TTY -> False.
    monkeypatch.setattr(_sys, "stdin", _Closed())
    assert _is_interactive() is False
