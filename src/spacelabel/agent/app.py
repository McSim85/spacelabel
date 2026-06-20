"""NSApplication accessory app, AppDelegate, and run loop (DESIGN.md §6).

Runs under ``NSApplicationActivationPolicyAccessory`` (set in code -- no Dock icon,
no ``LSUIElement`` plist needed for the pipx path) via
``PyObjCTools.AppHelper.runEventLoop()``. The delegate wires the space/display
observer (debounced) to the enabled display modes, all reading the same
UUID->label store.

Single instance only (DECISIONS.md 6.5): an ``fcntl.flock`` on ``agent.lock``
guards against a second agent that would aggravate the Tahoe ControlCenter
visibility loop. Config/labels live-reload via a 1.0s mtime-poll ``NSTimer``
(a robust simplification vs kqueue), so a CLI ``label set`` is reflected live.
"""

from __future__ import annotations

import fcntl
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSControlStateValueOff,
    NSControlStateValueOn,
    NSMenuItem,
    NSObject,
    NSScreen,
)
from Foundation import NSTimer
from PyObjCTools import AppHelper

from spacelabel import labeling, store
from spacelabel.agent import geometry
from spacelabel.logging_setup import LogMode, setup_logging

if TYPE_CHECKING:
    from spacelabel.agent.hud import Hud
    from spacelabel.agent.menubar import MenuBarItem
    from spacelabel.agent.overlay import Overlay
    from spacelabel.agent.prefs import PreferencesWindow
    from spacelabel.agent.wallpaper import WallpaperRenderer
    from spacelabel.model import Config, Display, Label, Space
    from spacelabel.platform.notifications import SpaceObserver

__all__ = ["AppDelegate", "run_agent"]

log = logging.getLogger(__name__)

#: Live-reload poll interval (seconds) for config.json / labels.json mtimes.
_RELOAD_INTERVAL = 1.0

#: Menu checkmark states (NSControlStateValue*).
_STATE_ON = NSControlStateValueOn
_STATE_OFF = NSControlStateValueOff

#: Mode toggle rows in the dropdown (config key -> menu title), docs/UI.md §2.3.
_MODE_MENU_TITLES = (
    ("menubar", "Menu-bar title"),
    ("hud", "On-switch HUD"),
    ("overlay", "Corner overlay"),
    ("wallpaper", "Wallpaper  [experimental]"),
)

#: System Settings deep-links offered on the click-to-switch "off" row (best-effort:
#: the Keyboard link lands on the pane; the user navigates to Keyboard Shortcuts →
#: Mission Control from there). Opened via NSWorkspace when the row is clicked (9.5).
_SETTINGS_URL_ACCESSIBILITY = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
)
_SETTINGS_URL_KEYBOARD = "x-apple.systempreferences:com.apple.Keyboard-Settings.extension"


def run_agent(
    config_path: Path | None = None, *, verbose: bool = False, debug: bool = False
) -> None:
    """Start the menu-bar agent in the foreground (blocks on the AppKit run loop).

    Args:
        config_path: Optional alternate ``config.json`` path; ``None`` uses the
            default under Application Support.
        verbose: Foreground dev logging at ``INFO`` (CLI sink instead of file).
        debug: Foreground dev logging at ``DEBUG``.

    Raises:
        SystemExit: If another agent instance already holds the lock (exit 1).
    """
    if verbose or debug:
        setup_logging(LogMode.CLI, verbose=verbose, debug=debug)
    else:
        # Honor config.log_level for the agent's file sink (takes effect at start).
        config = store.load_config(store.StorePaths.resolve(config_path))
        agent_level = getattr(logging, config.log_level, logging.WARNING)
        setup_logging(LogMode.AGENT, agent_level=agent_level)

    lock_handle = _acquire_single_instance_lock(config_path)

    app = NSApplication.sharedApplication()
    delegate = AppDelegate.alloc().initWithConfigPath_(config_path)
    # Keep the lock file handle alive for the process lifetime via the delegate.
    delegate.retainLockHandle_(lock_handle)
    app.setDelegate_(delegate)
    log.info("starting spacelabel agent run loop")
    # installInterrupt=True wires a SIGINT handler so Ctrl-C stops the AppKit run
    # loop during a foreground/dev run (the LaunchAgent stops via the menu Quit).
    AppHelper.runEventLoop(installInterrupt=True)


def _acquire_single_instance_lock(config_path: Path | None) -> object:
    """Acquire an exclusive non-blocking lock on ``agent.lock`` (DECISIONS.md 6.5).

    Returns:
        The open lock-file handle (kept open for the process lifetime).

    Raises:
        SystemExit: If the lock is already held by another agent (exit 1).
    """
    paths = store.StorePaths.resolve(config_path)
    paths.directory.mkdir(parents=True, exist_ok=True)
    lock_path = paths.directory / "agent.lock"
    handle = lock_path.open("w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        log.error("another spacelabel agent is already running (%s): %s", lock_path, exc)
        raise SystemExit(1) from exc
    log.debug("acquired single-instance lock %s", lock_path)
    return handle


class AppDelegate(NSObject):
    """Application delegate: builds the surfaces and drives refreshes.

    Constructed via :meth:`initWithConfigPath_`; the surfaces and observer are
    built in :meth:`applicationDidFinishLaunching_` so the activation policy and
    run loop are in place first.
    """

    def initWithConfigPath_(self, config_path: Path | None) -> AppDelegate | None:  # noqa: N802
        """Initialize with the optional config path (no AppKit work yet)."""
        self = objc.super(AppDelegate, self).init()
        if self is None:
            return None
        self._config_path: Path | None = config_path
        self._paths = store.StorePaths.resolve(config_path)
        self._config: Config | None = None
        self._labels: dict[str, Label] = {}
        self._menubar: MenuBarItem | None = None
        self._hud: Hud | None = None
        self._overlays: dict[str, Overlay] = {}
        self._wallpaper: WallpaperRenderer | None = None
        self._prefs: PreferencesWindow | None = None
        self._observer: SpaceObserver | None = None
        self._reload_timer: object | None = None
        self._lock_handle: object | None = None
        self._config_mtime: float = 0.0
        self._labels_mtime: float = 0.0
        self._displays_mtime: float = 0.0
        # Click-to-switch runtime state (DECISIONS.md 9.5): availability is verified
        # reactively (on a pill click) and on a fresh opt-in; a failure disables
        # capture with a surfaced reason rather than a silent no-op.
        self._click_to_switch_available: bool = True
        self._click_to_switch_reason: str | None = None
        self._click_to_switch_settings_url: str | None = None
        self._click_to_switch_on: bool = False
        self._ax_prompted: bool = False
        return self

    def retainLockHandle_(self, handle: object) -> None:  # noqa: N802
        """Keep the single-instance lock handle alive for the process lifetime."""
        self._lock_handle = handle

    def applicationDidFinishLaunching_(self, _notification: object) -> None:  # noqa: N802
        """Set accessory policy, build surfaces, wire the observer, start polling."""
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory
        )
        self._config = self._load_config()
        self._labels = self._load_labels()
        self._build_surfaces()
        self._start_observer()
        self._start_reload_timer()
        self._refresh()

    # -- construction -----------------------------------------------------------

    @objc.python_method
    def _build_surfaces(self) -> None:
        """Build the menu-bar item and any enabled HUD/overlay/wallpaper surfaces."""
        from spacelabel.agent.menubar import MenuBarItem

        config = self._require_config()
        self._menubar = MenuBarItem(show_buttons_row=config.menubar.show_buttons_row)
        # Wire pill click-to-switch (no-op until menubar.click_to_switch is enabled
        # and verified; DECISIONS.md 9.5). The menu-open handler is the item's own.
        self._menubar.set_pill_switch_handler(self._on_pill_clicked)
        # The menu is (re)built per refresh from the live Spaces (see _rebuild_menu).
        if config.modes.get("hud"):
            from spacelabel.agent.hud import Hud

            self._hud = Hud()
        if config.modes.get("wallpaper"):
            from spacelabel.agent.wallpaper import WallpaperRenderer

            self._wallpaper = WallpaperRenderer()
        # Overlays are built lazily per display in _refresh (one panel per display).

    @objc.python_method
    def _rebuild_menu(self, spaces: list[Space], ordinals: dict[int, int]) -> None:
        """Build the dropdown: rename, per-display Space list, mode toggles, Prefs, Quit.

        Mirrors docs/UI.md §2.3. Spaces are grouped under a disabled per-display
        header; the current Space on each display is checkmarked; an unlabeled Space
        shows its "Desktop N" number; a Space with no UUID is shown disabled. Clicking
        a labelable Space row renames it; the top item renames the current Space.
        """
        if self._menubar is None:
            return
        from spacelabel.platform import displays

        config = self._require_config()
        display_labels = self._load_display_labels()
        items: list[object] = []

        rename = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Rename this Space…", "renameCurrent:", ""
        )
        rename.setTarget_(self)
        items.append(rename)
        items.append(NSMenuItem.separatorItem())

        for display, group in self._group_by_display(spaces):
            header = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                displays.resolved_name(display, display_labels), "", ""
            )
            header.setEnabled_(False)
            items.append(header)
            for space in group:
                ordinal = ordinals.get(id(space), 0)
                if space.uuid:
                    title = labeling.title_for(
                        space, self._labels, ordinal, max_length=config.menubar.max_length
                    )
                    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                        f"  {title}", "renameSpace:", ""
                    )
                    item.setTarget_(self)
                    item.setRepresentedObject_(space.uuid)
                    label = self._labels.get(space.uuid)
                    swatch = _color_swatch(label.color if label is not None else None)
                    if swatch is not None:
                        item.setImage_(swatch)  # color tag visible in the dropdown
                else:
                    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                        f"  Desktop {ordinal} — (no UUID)", "", ""
                    )
                    item.setEnabled_(False)
                if space.is_current:
                    item.setState_(_STATE_ON)
                items.append(item)

        items.append(NSMenuItem.separatorItem())
        for mode_name, mode_title in _MODE_MENU_TITLES:
            entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                mode_title, "toggleMode:", ""
            )
            entry.setTarget_(self)
            entry.setRepresentedObject_(mode_name)
            entry.setState_(_STATE_ON if config.modes.get(mode_name) else _STATE_OFF)
            items.append(entry)

        # Surface WHY click-to-switch is off when it is enabled but unusable, so the
        # opt-in never looks active yet silently no-ops (DECISIONS.md 9.5).
        if (
            config.modes.get("menubar")
            and config.menubar.show_buttons_row
            and config.menubar.click_to_switch
            and not self._click_to_switch_available
        ):
            reason = self._click_to_switch_reason or "click-to-switch is unavailable"
            items.append(
                self._click_to_switch_warning_item(reason, self._click_to_switch_settings_url)
            )

        items.append(NSMenuItem.separatorItem())
        prefs_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Preferences…", "openPreferences:", ","
        )
        prefs_item.setTarget_(self)
        items.append(prefs_item)
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit spacelabel", "quit:", "q"
        )
        quit_item.setTarget_(self)
        items.append(quit_item)
        self._menubar.set_menu_items(items)

    @objc.python_method
    def _start_observer(self) -> None:
        """Start the debounced space/display-change observer (DESIGN.md §5)."""
        from spacelabel.platform.notifications import SpaceObserver

        config = self._require_config()
        observer = SpaceObserver(
            self._on_space_change,
            self._on_display_change,
            debounce_ms=config.debounce_ms,
        )
        observer.start()
        self._observer = observer

    @objc.python_method
    def _restart_observer(self) -> None:
        """Tear down and re-create the observer so a new debounce_ms takes effect."""
        if self._observer is not None:
            self._observer.stop()
            self._observer = None
        self._start_observer()

    @objc.python_method
    def _start_reload_timer(self) -> None:
        """Start the 1.0s mtime-poll timer for live config/labels reload."""
        self._config_mtime = _mtime(self._paths.config_file)
        self._labels_mtime = _mtime(self._paths.labels_file)
        self._displays_mtime = _mtime(self._paths.displays_file)
        self._reload_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            _RELOAD_INTERVAL,
            True,
            self._poll_reload,
        )

    # -- callbacks --------------------------------------------------------------

    @objc.python_method
    def _on_space_change(self) -> None:
        """Debounced space-change callback -> refresh the surfaces."""
        self._refresh()

    @objc.python_method
    def _on_display_change(self) -> None:
        """Display-change callback -> drop cached overlays and refresh."""
        for overlay in self._overlays.values():
            overlay.order_out()
        self._overlays.clear()
        self._refresh()

    @objc.python_method
    def _poll_reload(self, _timer: object) -> None:
        """Reload config/labels if either file's mtime changed (live reload)."""
        config_mtime = _mtime(self._paths.config_file)
        labels_mtime = _mtime(self._paths.labels_file)
        displays_mtime = _mtime(self._paths.displays_file)
        changed = False
        if config_mtime != self._config_mtime:
            old_debounce = self._config.debounce_ms if self._config is not None else None
            self._config_mtime = config_mtime
            self._config = self._load_config()
            changed = True
            # The SpaceObserver captured debounce_ms at construction; restart it so a
            # `config set debounce_ms ...` actually takes effect live.
            if self._observer is not None and self._config.debounce_ms != old_debounce:
                self._restart_observer()
        if labels_mtime != self._labels_mtime:
            self._labels_mtime = labels_mtime
            self._labels = self._load_labels()
            changed = True
        if displays_mtime != self._displays_mtime:
            # Display names are reloaded fresh in _rebuild_menu; just trigger a refresh.
            self._displays_mtime = displays_mtime
            changed = True
        if changed:
            log.debug("config/labels changed on disk; refreshing surfaces")
            self._refresh()

    # -- the hot path -----------------------------------------------------------

    @objc.python_method
    def _refresh(self) -> None:
        """Read the active Space, resolve its title, and update every surface."""
        # Include unlabelable Spaces so the menu/overlays surface every display.
        spaces = self._read_spaces(include_unlabelable=True)
        ordinals = labeling.assign_ordinals(spaces)
        active_uuid = self._read_active_space_uuid()
        active_space = self._find_space(spaces, active_uuid)
        title = self._title_for_active(active_space, ordinals)
        if self._menubar is not None:
            # Reconcile click-to-switch availability with config BEFORE rebuilding the
            # menu, so a fresh opt-in clears the stale ⚠️ "off" row on the SAME refresh
            # (capture itself is re-applied later in _update_buttons_row, once the row
            # view exists). (P2 review fix.)
            self._sync_click_to_switch_state()
            self._rebuild_menu(spaces, ordinals)
            config = self._require_config()
            if not config.modes.get("menubar"):
                # Menu-bar mode off: stop reflecting the Space label; show a neutral
                # icon so the menu (Preferences/Quit) is still reachable. (P2 fix.)
                self._menubar.set_inactive()
            elif config.menubar.show_buttons_row:
                # The buttons row (pills for all Spaces) and the title (current
                # Space's full label) are mutually exclusive: show one, clear the other.
                self._menubar.set_show_buttons_row(True)
                self._menubar.set_title("")
                self._update_buttons_row(spaces, ordinals)
            else:
                self._menubar.set_show_buttons_row(False)
                self._menubar.set_title(title)
        self._update_hud(title)
        self._update_overlays(spaces, ordinals)
        self._update_wallpaper(title)

    @objc.python_method
    def _title_for_active(self, active_space: Space | None, ordinals: dict[int, int]) -> str:
        """Resolve the menu-bar/HUD title for the active Space (or a neutral default)."""
        config = self._require_config()
        if active_space is None:
            return "spacelabel"
        ordinal = ordinals.get(id(active_space), 0)
        return labeling.title_for(
            active_space, self._labels, ordinal, max_length=config.menubar.max_length
        )

    @objc.python_method
    def _update_buttons_row(self, spaces: list[Space], ordinals: dict[int, int]) -> None:
        """Populate the optional buttons row grouped by display."""
        config = self._require_config()
        if not config.menubar.show_buttons_row or self._menubar is None:
            return
        # Apply capture AFTER set_show_buttons_row (in _refresh) created the row view;
        # the availability state was already reconciled before the menu rebuild.
        self._apply_click_to_switch_capture()
        groups = self._group_by_display(spaces)
        if config.menubar.buttons_scope == "active_display":
            active_display = self._active_display_uuid()
            groups = [(d, s) for (d, s) in groups if d.uuid == active_display]
        self._menubar.set_buttons_row(
            groups, self._labels, ordinals, pill_chars=config.menubar.pill_label_chars
        )

    @objc.python_method
    def _sync_click_to_switch_state(self) -> None:
        """Reconcile click-to-switch availability with config (run BEFORE the menu rebuild).

        A fresh opt-in (off->on) clears any stale disable + reason so the dropdown stops
        showing the ⚠️ "off" row the moment the user re-enables the feature (rather than
        for one extra refresh). Availability is otherwise verified reactively on a click
        (so the agent never proactively prompts for Accessibility on start); once a click
        fails, capture stays off -- with the reason surfaced -- until the next re-opt-in.
        Pill click *capture* is applied separately in :meth:`_apply_click_to_switch_capture`,
        which must run after the row view exists.
        """
        config = self._require_config()
        on = config.menubar.click_to_switch
        if on and not self._click_to_switch_on:
            self._click_to_switch_available = True
            self._click_to_switch_reason = None
            self._click_to_switch_settings_url = None
            self._ax_prompted = False
        self._click_to_switch_on = on

    @objc.python_method
    def _apply_click_to_switch_capture(self) -> None:
        """Toggle pill click capture to match config + availability (DECISIONS.md 9.5).

        Called from :meth:`_update_buttons_row` so the row view is guaranteed to exist;
        the availability state it reads was already reconciled by
        :meth:`_sync_click_to_switch_state` before the menu was rebuilt.
        """
        if self._menubar is None:
            return
        config = self._require_config()
        self._menubar.set_pills_clickable(
            config.menubar.click_to_switch and self._click_to_switch_available
        )

    @objc.python_method
    def _on_pill_clicked(self, uuid: str) -> None:
        """Switch to the clicked pill's Space via the Mission Control shortcut.

        Rebuilds the UUID->ordinal map from a LIVE CGS read at click time (ordinals
        shift on reorder -- never cached, DECISIONS.md 9.5), resolves the target's
        enabled "Switch to Desktop N" shortcut, and posts it. Any failure disables
        capture with a specific, surfaced reason (prompting for Accessibility on the
        first miss) rather than a silent no-op.
        """
        from spacelabel.platform import cgs, switching

        if not uuid:
            # Defensive: the buttons row already routes empty-UUID (unlabelable) pills
            # to the menu (ButtonsRowView._handle_click_at_x), so a click never reaches
            # here with an empty UUID.
            log.debug("ignoring click-to-switch for a Space with no persistent UUID")
            return
        try:
            spaces = cgs.enumerate_spaces(include_unlabelable=True)
        except cgs.CGSUnavailableError as exc:
            self._disable_click_to_switch(f"could not read live Spaces: {exc}")
            return
        ordinal = labeling.ordinal_for_uuid(spaces, uuid)
        if ordinal is None:
            # Space disappeared between last refresh and click (reorder race).
            # Rebuild the row immediately so the stale pill is no longer clickable.
            log.warning("clicked Space %s is no longer present; refreshing row", uuid)
            self._refresh()
            return
        if not switching.accessibility_trusted(prompt=not self._ax_prompted):
            self._ax_prompted = True
            self._disable_click_to_switch(
                "Accessibility permission is required — enable the entry in System "
                "Settings → Privacy & Security → Accessibility (on a pipx install it "
                "appears under your Python interpreter, e.g. “python3.x”, not "
                "“spacelabel”), then re-enable click-to-switch.",
                settings_url=_SETTINGS_URL_ACCESSIBILITY,
            )
            return
        try:
            hotkeys = switching.load_symbolic_hotkeys()
        except switching.HotkeyReadError as exc:
            self._disable_click_to_switch(f"could not read keyboard shortcuts: {exc!s}")
            return
        binding = switching.parse_desktop_binding(hotkeys, ordinal)
        if binding is None:
            self._disable_click_to_switch(
                f"the “Switch to Desktop {ordinal}” shortcut is not enabled — turn it on in "
                "System Settings → Keyboard → Keyboard Shortcuts → Mission Control.",
                settings_url=_SETTINGS_URL_KEYBOARD,
            )
            return
        if switching.post_switch(binding):
            log.info("posted “Switch to Desktop %d” for Space %s", ordinal, uuid)
        else:
            self._disable_click_to_switch(
                "could not post the switch key event (CGEventPost unavailable)."
            )

    @objc.python_method
    def _disable_click_to_switch(self, reason: str, *, settings_url: str | None = None) -> None:
        """Disable pill switching with a visible, logged reason (never silent; 9.5).

        ``settings_url`` (when known) makes the dropdown's attention row open the
        System Settings pane that fixes the blocker on click.
        """
        self._click_to_switch_available = False
        self._click_to_switch_reason = reason
        self._click_to_switch_settings_url = settings_url
        log.warning("click-to-switch disabled: %s", reason)
        if self._menubar is not None:
            # Stop capturing clicks so the menu (Preferences/Quit) is reachable again.
            self._menubar.set_pills_clickable(False)
            self._rebuild_menu_safely()

    @objc.python_method
    def _rebuild_menu_safely(self) -> None:
        """Rebuild the dropdown from the live Spaces so the disable reason appears."""
        spaces = self._read_spaces(include_unlabelable=True)
        ordinals = labeling.assign_ordinals(spaces)
        self._rebuild_menu(spaces, ordinals)

    @objc.python_method
    def _click_to_switch_warning_item(self, reason: str, settings_url: str | None) -> object:
        """Build the attention 'click-to-switch off' dropdown row (DECISIONS.md 9.5).

        ENABLED (so AppKit does NOT gray it out) with a colored ⚠️ emoji, so the reason
        is noticeable rather than the easy-to-miss dim disabled line it was. When a
        Settings deep-link is known the row opens it on click, turning the notice into
        a one-click fix. A plain (non-attributed) title is used deliberately: an enabled
        item already renders the emoji in color and the text at full contrast, and it
        avoids font/attributed-string AppKit calls (e.g. ``NSFont.menuFontOfSize_``) that
        can abort before ``NSApplication`` is fully up -- in unit tests / headless CI,
        and as an early call in general (review P1).
        """
        from AppKit import NSMenuItem

        # The reason already names the System Settings destination; an enabled,
        # colored, highlight-on-hover row signals it is clickable (opens Settings).
        title = f"⚠️ Click-to-switch off — {reason}"
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "", "")
        if settings_url:
            item.setTarget_(self)
            item.setAction_("openClickToSwitchSettings:")
            item.setRepresentedObject_(settings_url)
        item.setEnabled_(True)  # never gray -- visibility is the whole point
        return item

    @objc.python_method
    def _update_hud(self, title: str) -> None:
        """Show the on-switch HUD on the active (switched-to) screen, if enabled."""
        config = self._require_config()
        if not config.modes.get("hud"):
            return
        if self._hud is None:  # lazily built so a runtime mode-toggle takes effect
            from spacelabel.agent.hud import Hud

            self._hud = Hud()
        # Show on the display the switch landed on, not whatever holds the key window
        # (DESIGN.md §4.3). Fall back to mainScreen only if the active screen is unknown.
        active_uuid = self._active_display_uuid()
        screen = None
        if active_uuid is not None:
            screen = self._screens_by_uuid().get(active_uuid)
        if screen is None:
            screen = NSScreen.mainScreen()
        # Size the banner to the active display's short side (DESIGN.md §9.9); an
        # explicit int config.hud.font_size overrides the formula.
        configured = config.hud.font_size
        if isinstance(configured, int):
            font_size = configured
        else:
            frame = screen.frame()
            font_size = geometry.hud_font_size((float(frame.size.width), float(frame.size.height)))
        self._hud.show(
            title,
            duration_ms=config.hud.duration_ms,
            screen=screen,
            position=config.hud.position,
            margin=config.hud.margin,
            font_size=font_size,
        )

    @objc.python_method
    def _update_overlays(self, spaces: list[Space], ordinals: dict[int, int]) -> None:
        """Update one overlay panel per display with that display's current label."""
        config = self._require_config()
        if not config.modes.get("overlay"):
            for panel in self._overlays.values():
                panel.order_out()
            return
        from spacelabel.agent.overlay import Overlay

        screens_by_uuid = self._screens_by_uuid()
        for display, display_spaces in self._group_by_display(spaces):
            current = next((s for s in display_spaces if s.is_current), None)
            if current is None:
                continue
            ordinal = ordinals.get(id(current), 0)
            text = labeling.title_for(
                current, self._labels, ordinal, max_length=config.menubar.max_length
            )
            font = geometry.overlay_font_size(display.size_pt, config.overlay.font_size)
            overlay: Overlay | None = self._overlays.get(display.uuid)
            if overlay is None:
                overlay = Overlay(font_size=font, bold=config.overlay.bold)
                self._overlays[display.uuid] = overlay
            else:
                # Re-apply font on a live config reload (no-op when unchanged).
                overlay.set_font(font, config.overlay.bold)
            screen = screens_by_uuid.get(display.uuid) or NSScreen.mainScreen()
            overlay.reposition(screen, config.overlay.corner, config.overlay.margin)
            overlay.set_text(text)

    @objc.python_method
    def _update_wallpaper(self, title: str) -> None:
        """Render the label onto the active screen's wallpaper (best-effort)."""
        config = self._require_config()
        if not config.modes.get("wallpaper"):
            return
        if self._wallpaper is None:  # lazily built so a runtime mode-toggle takes effect
            from spacelabel.agent.wallpaper import WallpaperRenderer

            self._wallpaper = WallpaperRenderer()
        # Write to the display the active Space is on, not whatever holds the key
        # window (the title is the active Space's label) -- same mapping as the HUD.
        active_uuid = self._active_display_uuid()
        screen = self._screens_by_uuid().get(active_uuid) if active_uuid is not None else None
        if screen is None:
            screen = NSScreen.mainScreen()
        self._wallpaper.render_and_set(title, screen=screen, position=config.wallpaper.position)

    # -- menu actions -----------------------------------------------------------

    def openPreferences_(self, _sender: object) -> None:  # noqa: N802
        """Open (building on first use) the preferences window."""
        from spacelabel.agent.prefs import PreferencesWindow

        if self._prefs is None:
            self._prefs = PreferencesWindow(self._config_path)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._prefs.show()

    def openClickToSwitchSettings_(self, sender: object) -> None:  # noqa: N802
        """Open the System Settings pane that fixes the click-to-switch blocker (9.5)."""
        url = sender.representedObject()
        if not url:
            return
        from AppKit import NSWorkspace
        from Foundation import NSURL

        ns_url = NSURL.URLWithString_(str(url))
        if ns_url is None:
            log.warning("could not build Settings URL from %r", url)
            return
        if not NSWorkspace.sharedWorkspace().openURL_(ns_url):
            log.warning("could not open Settings URL %s", url)

    def quit_(self, _sender: object) -> None:
        """Stop observers and terminate the application (stays stopped, 6.4)."""
        if self._observer is not None:
            self._observer.stop()
        if self._reload_timer is not None:
            self._reload_timer.invalidate()
        NSApplication.sharedApplication().terminate_(None)

    def renameCurrent_(self, _sender: object) -> None:  # noqa: N802
        """Rename the active Space (prompt prefilled with its current label)."""
        uuid = self._read_active_space_uuid()
        if not uuid:
            log.warning("cannot rename: no active Space resolved")
            return
        self._rename_space(uuid)

    def renameSpace_(self, sender: object) -> None:  # noqa: N802
        """Rename the Space whose UUID is the clicked menu item's represented object."""
        uuid = sender.representedObject()
        if not uuid:
            return
        self._rename_space(str(uuid))

    def toggleMode_(self, sender: object) -> None:  # noqa: N802
        """Toggle a display mode from the dropdown, persist it, and refresh live."""
        mode_name = sender.representedObject()
        if not mode_name:
            return
        config = self._require_config()
        new_value = not config.modes.get(str(mode_name), False)
        try:
            store.set_config_value(self._paths, f"modes.{mode_name}", str(new_value))
        except (OSError, store.StoreError) as exc:
            log.error("could not toggle mode %s: %s", mode_name, exc)
            return
        self._config = self._load_config()
        self._config_mtime = _mtime(self._paths.config_file)
        self._refresh()

    @objc.python_method
    def _rename_space(self, uuid: str) -> None:
        """Prompt for a label and commit it (empty clears), then refresh."""
        existing = self._labels.get(uuid)
        prefill = existing.text if existing is not None else ""
        text = self._prompt_label(prefill)
        if text is None:  # cancelled
            return
        try:
            if text:
                store.set_label(self._paths, uuid, text)
            else:
                store.clear_label(self._paths, uuid)
        except (OSError, store.StoreError) as exc:
            log.error("could not save label for %s: %s", uuid, exc)
            return
        self._labels = self._load_labels()
        self._labels_mtime = _mtime(self._paths.labels_file)
        self._refresh()

    @objc.python_method
    def _prompt_label(self, prefill: str) -> str | None:
        """Show a modal text prompt; return the entered text, or None if cancelled."""
        from AppKit import NSAlert, NSMakeRect, NSTextField

        alert = NSAlert.alloc().init()
        alert.setMessageText_("Rename Space")
        alert.setInformativeText_("Enter a label for this Space (leave empty to clear).")
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Cancel")
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 240.0, 24.0))
        field.setStringValue_(prefill)
        alert.setAccessoryView_(field)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        # NSAlertFirstButtonReturn (Save) == 1000.
        if int(alert.runModal()) == 1000:
            return str(field.stringValue()).strip()
        return None

    # -- reads (recover on failure; never crash the agent) ----------------------

    @objc.python_method
    def _read_spaces(self, *, include_unlabelable: bool = False) -> list[Space]:
        """Read Spaces via CGS, falling back to the plist, then empty.

        ``include_unlabelable`` surfaces Spaces with no assigned UUID so the menu /
        overlays show every display (the second-display case).
        """
        from spacelabel.platform import cgs, spaces_plist

        try:
            return cgs.enumerate_spaces(include_unlabelable=include_unlabelable)
        except cgs.CGSUnavailableError as exc:
            log.warning("CGS unavailable; falling back to plist: %s", exc)
        try:
            return spaces_plist.read_spaces()
        except (OSError, ValueError) as exc:
            log.warning("plist fallback failed: %s", exc)
            return []

    @objc.python_method
    def _read_active_space_uuid(self) -> str | None:
        """Read the active display's current Space UUID; None on any failure."""
        from spacelabel.platform import cgs

        try:
            return cgs.read_active_space_uuid()
        except cgs.CGSUnavailableError as exc:
            log.warning("could not read active space uuid: %s", exc)
            return None

    @objc.python_method
    def _active_display_uuid(self) -> str | None:
        """Return the active display UUID (best-effort), else None."""
        from spacelabel.platform import cgs

        try:
            return str(cgs.active_display_uuid())
        except cgs.CGSUnavailableError as exc:
            log.debug("active display uuid unavailable: %s", exc)
            return None

    @objc.python_method
    def _load_config(self) -> Config:
        """Load config, recovering with defaults on any store error."""
        try:
            return store.load_config(self._paths)
        except (OSError, store.StoreError) as exc:
            from spacelabel.model import Config as ConfigModel

            log.warning("config load failed; using defaults: %s", exc)
            return ConfigModel()

    @objc.python_method
    def _load_labels(self) -> dict[str, Label]:
        """Load labels, recovering with an empty mapping on any store error."""
        try:
            return store.load_labels(self._paths)
        except (OSError, store.StoreError) as exc:
            log.warning("labels load failed; using empty store: %s", exc)
            return {}

    @objc.python_method
    def _load_display_labels(self) -> dict[str, str]:
        """Load custom display names, recovering with an empty mapping on error."""
        try:
            return store.load_display_labels(self._paths)
        except (OSError, store.StoreError) as exc:
            log.warning("display-labels load failed; using none: %s", exc)
            return {}

    # -- small helpers ----------------------------------------------------------

    @objc.python_method
    def _require_config(self) -> Config:
        """Return the loaded config, loading defaults if not yet present."""
        if self._config is None:
            self._config = self._load_config()
        return self._config

    @objc.python_method
    def _find_space(self, spaces: list[Space], uuid: str | None) -> Space | None:
        """Return the Space matching ``uuid`` (or the current one), else None."""
        if uuid is not None:
            for space in spaces:
                if space.uuid == uuid:
                    return space
        return next((s for s in spaces if s.is_current), None)

    @objc.python_method
    def _group_by_display(self, spaces: list[Space]) -> list[tuple[Display, list[Space]]]:
        """Group Spaces under their display in topology order (best-effort)."""
        from spacelabel.platform import displays

        try:
            topology = displays.discover_topology()
        except (RuntimeError, OSError) as exc:
            log.debug("topology discovery failed: %s", exc)
            topology = []
        by_uuid: dict[str, list[Space]] = {}
        for space in spaces:
            by_uuid.setdefault(space.display_uuid, []).append(space)
        groups: list[tuple[Display, list[Space]]] = []
        seen: set[str] = set()
        for display in topology:
            seen.add(display.uuid)
            groups.append((display, by_uuid.get(display.uuid, [])))
        for display_uuid, group in by_uuid.items():
            if display_uuid not in seen:
                groups.append((_synthetic_display(display_uuid), group))
        return groups

    @objc.python_method
    def _screens_by_uuid(self) -> dict[str, object]:
        """Map display UUID -> NSScreen for overlay placement (best-effort)."""
        from spacelabel.platform import displays

        result: dict[str, object] = {}
        for screen in NSScreen.screens():
            description = screen.deviceDescription()
            number = description.get("NSScreenNumber")
            if number is None:
                continue
            uuid = displays.display_uuid(int(number))
            if uuid is not None:
                result[str(uuid)] = screen
        return result


def _synthetic_display(display_uuid: str) -> Display:
    """Build a minimal :class:`~spacelabel.model.Display` for an unmapped UUID."""
    from spacelabel.model import Display as DisplayModel

    return DisplayModel(uuid=display_uuid, cg_display_id=0)


def _mtime(path: Path) -> float:
    """Return ``path``'s mtime, or 0.0 if it does not exist yet."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _color_swatch(hex_color: str | None) -> object | None:
    """Return a small rounded NSImage filled with ``#rrggbb``, or None if unset/bad.

    Used as a menu-item image so a label's color tag is visible in the dropdown
    (the color also tints the buttons-row pills, DECISIONS.md 9.4/9.8).
    """
    if not hex_color:
        return None
    text = hex_color.lstrip("#")
    if len(text) != 6:
        return None
    try:
        red, green, blue = (int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return None
    from AppKit import NSBezierPath, NSColor, NSImage, NSMakeRect, NSMakeSize

    image = NSImage.alloc().initWithSize_(NSMakeSize(12.0, 12.0))
    image.lockFocus()
    NSColor.colorWithCalibratedRed_green_blue_alpha_(red, green, blue, 1.0).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(1.0, 1.0, 10.0, 10.0), 2.0, 2.0
    ).fill()
    image.unlockFocus()
    return image
