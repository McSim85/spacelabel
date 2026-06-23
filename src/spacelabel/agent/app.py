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

import contextlib
import fcntl
import logging
import os
import sys
import traceback
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
from spacelabel.logging_setup import (
    LogMode,
    install_logging_excepthook,
    setup_logging,
    truncate_boot_log,
)
from spacelabel.platform.cgs import CGSUnavailableError  # exception class only (no objc at import)

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

#: How often (in poll ticks ~= seconds) the managed agent re-caps the launchd boot
#: log, so recurring AppKit/PyObjC stderr can't grow it unbounded mid-session.
_BOOT_CAP_EVERY_TICKS = 60

#: How often (in poll ticks ~= seconds) the live CGS topology is read. Decoupled from
#: the 1 s mtime poll: the mtime checks are cheap stat()s (keep `config`/`label` edits
#: reflected within ~1 s), but the CGS read costs ~1 ms (measured; ~6 ms p99) on the
#: AppKit main thread, so it runs every ~3 s — reorder freshness stays imperceptible
#: while cutting main-thread CGS load ~3x (off-main read deferred — DECISIONS 4.2/4.3).
_TOPOLOGY_POLL_EVERY_TICKS = 3


def _topology_signature(spaces: list[Space]) -> tuple[tuple[str, str, int], ...]:
    """Ordered signature of the live Space topology for change detection (PURE).

    A ``(display_uuid, uuid, id64)`` tuple per Space, **in order**. Compared
    tick-to-tick by the poll so a Mission Control **reorder** — which fires neither
    ``activeSpaceDidChange`` nor ``didChangeScreenParameters`` — is detected,
    alongside create/delete (length changes). Order is preserved (no sorting): a
    reorder permutes the tuple even when the set is identical. (DECISIONS.md §4.3.)

    Field choices:
    * **No ``is_current``:** an active-Space change is the observer's job
      (``activeSpaceDidChange``), not the poll's, and ``is_current`` is unreliable
      per-tick — ``enumerate_spaces`` degrades to no-current-marked for a display
      whose ``CGSManagedDisplayGetCurrentSpace`` read fails, which would otherwise
      flip the signature and trigger a bogus "topology changed" every few seconds.
    * **``id64`` included:** with ``include_unlabelable=True`` several Spaces on a
      display can share ``uuid == ""`` (no persistent UUID yet); ``id64`` keeps them
      distinct so reordering two no-UUID desktops still changes the signature. This
      is an in-memory, tick-to-tick, never-persisted value — *not* a label key — so
      the "never key on id64" invariant (DECISIONS 1.4) does not apply.
    """
    return tuple((space.display_uuid, space.uuid, space.id64) for space in spaces)


def _is_interactive() -> bool:
    """Whether any standard stream is a TTY (a manual terminal run, not launchd).

    A ``None``, closed, or detached stream counts as non-interactive: ``isatty()``
    raises ``ValueError`` on a closed file (and ``OSError`` on some detached ones),
    which must be treated as "no usable terminal", never crash startup.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            if stream is not None and stream.isatty():
                return True
        except (ValueError, OSError):
            continue
    return False


def _is_managed_run(
    config_path: Path | None, *, verbose: bool, debug: bool, interactive: bool
) -> bool:
    """Whether this is the launchd-managed production run (PURE given ``interactive``).

    Requires **all** of: default config (no ``--config``), no ``--verbose``/``--debug``,
    and **no controlling TTY**. A launchd LaunchAgent has its std streams wired to
    files (not a terminal), so ``interactive`` is False; a manual ``spacelabel agent``
    from a shell is a TTY and must NOT be treated as the managed agent (no plist
    rewrite / boot-log truncation / ``os._exit`` hard-exit). The TTY check is a
    **fail-safe** launchd proxy: a wrong env-var positive (e.g. ``XPC_SERVICE_NAME``)
    that the real agent must satisfy could silently disable the migration, whereas a
    LaunchAgent reliably has no TTY, so this heuristic never breaks the real agent.

    Residual: a *manual* non-TTY run (``nohup``/redirected/CI) also passes this and is
    treated as managed. The harm is bounded — these actions run only **after** the
    single-instance lock, so only the lock winner acts, and a non-TTY run that wins
    the lock is the de-facto agent (truncate/refresh are idempotent/beneficial,
    ``os._exit`` just exits non-zero with output already redirected). A *verified*
    launchd signal is deferred to Phase 6 (it can't be validated against real launchd
    here, and a wrong positive would be worse than this bounded misclassification).
    """
    return config_path is None and not (verbose or debug) and not interactive


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
    # Configure the real sink first so EVERY startup failure (incl. the
    # single-instance rejection below) lands in the inspectable log: the rotated
    # agent.log for the launchd agent, stderr for a foreground/dev run. load_config
    # never raises (recovers to defaults), so it can't crash before the lock.
    if verbose or debug:
        setup_logging(LogMode.CLI, verbose=verbose, debug=debug)
    else:
        config = store.load_config(store.StorePaths.resolve(config_path))
        agent_level = getattr(logging, config.log_level, logging.WARNING)
        setup_logging(LogMode.AGENT, agent_level=agent_level)

    # Route uncaught (top-level) exceptions into the configured sink, not raw stderr.
    install_logging_excepthook()

    # Single-instance lock: a rejected duplicate has logged the reason and exits here.
    lock_handle = _acquire_single_instance_lock(config_path)

    # Only the production login agent (default config, no dev flags) touches the
    # shared/installed artifacts: a `--config` or `--debug`/`--verbose` foreground run
    # must NOT zero the real agent's boot log or rewrite the user's installed plist.
    managed = _is_managed_run(
        config_path, verbose=verbose, debug=debug, interactive=_is_interactive()
    )
    if managed:
        # Cap the unrotated launchd capture file(s), and migrate a stale on-disk plist
        # so the log-path/single-writer fix rolls out on upgrade without a manual
        # `spacelabel install` (applies next login). Before the run loop so each
        # KeepAlive restart bounds the file even if startup later crashes.
        truncate_boot_log()
        from spacelabel import install as install_mod

        install_mod.refresh_plist_if_stale()

    app = NSApplication.sharedApplication()
    delegate = AppDelegate.alloc().initWithConfigPath_(config_path)
    # Keep the lock file handle alive for the process lifetime via the delegate.
    delegate.retainLockHandle_(lock_handle)
    delegate.set_managed_run(managed)
    app.setDelegate_(delegate)
    log.info("starting spacelabel agent run loop")
    # installInterrupt=True wires a SIGINT handler so Ctrl-C stops the AppKit run
    # loop during a foreground/dev run (the LaunchAgent stops via the menu Quit).
    AppHelper.runEventLoop(installInterrupt=True)

    # A non-managed startup failure stops the loop and stashes the error here (PyObjC
    # swallows a re-raise inside the callback). It was already logged; surface the
    # STANDARD traceback on stderr (the logging sys.excepthook would suppress it) and
    # exit non-zero via SystemExit (which bypasses the excepthook, no double-logging).
    if delegate._startup_error is not None:
        # stderr may be closed/detached; the failure was already logged at startup, so
        # a failed print here must not mask it.
        with contextlib.suppress(ValueError, OSError):
            traceback.print_exception(delegate._startup_error)
        raise SystemExit(1)


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
    # Open WITHOUT truncating ("a+", not "w"): a losing second instance must not clear the
    # current holder's recorded pid before its flock fails (else `status` reports pid=?).
    handle = lock_path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()  # the loser leaves the winner's recorded pid intact
        log.error("another spacelabel agent is already running (%s): %s", lock_path, exc)
        raise SystemExit(1) from exc
    log.debug("acquired single-instance lock %s", lock_path)
    # Won the lock: NOW record our pid (truncate the stale content first) so `spacelabel
    # status` can report it for a foreground agent (the flock itself is anonymous);
    # best-effort (DECISIONS.md §9).
    try:
        handle.truncate(0)
        handle.write(str(os.getpid()))
        handle.flush()
    except OSError as exc:
        log.debug("could not record agent pid in %s: %s", lock_path, exc)
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
        # Set by run_agent: True only for the launchd-managed production run (gates
        # the startup hard-exit and the periodic boot-log cap below).
        self._is_managed_run: bool = False
        self._boot_cap_ticks: int = 0
        # A non-managed startup failure is stashed here (PyObjC swallows a re-raise
        # from the callback); run_agent re-raises it after the loop stops.
        self._startup_error: BaseException | None = None
        # Last live Space-topology signature; the (3 s) CGS poll refreshes when it
        # changes — catches a Mission Control reorder, which fires no notification and
        # is invisible in the spaces plist (DECISIONS 4.3).
        self._topology_sig: tuple[tuple[str, str, int], ...] | None = None
        # Spaces-plist path + last mtime: a cheap stat() watched every 1 s tick for
        # create/delete (the plist flushes on those — §3.4), independent of CGS.
        from spacelabel.platform import spaces_plist

        self._spaces_plist_path = spaces_plist.plist_path()
        self._spaces_plist_mtime: float = 0.0
        # Tick counter so the EXPENSIVE CGS topology read runs every
        # _TOPOLOGY_POLL_EVERY_TICKS, not every 1 s tick (cheap mtime checks stay at 1 s).
        self._topology_poll_ticks: int = 0
        # One-shot guard so an UNEXPECTED topology-read error warns once, not per tick.
        self._topology_read_warned: bool = False
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

    @objc.python_method
    def set_managed_run(self, managed: bool) -> None:
        """Record whether this is the launchd-managed production run (see run_agent)."""
        self._is_managed_run = managed

    def applicationDidFinishLaunching_(self, _notification: object) -> None:  # noqa: N802
        """Set accessory policy, build surfaces, wire the observer, start polling.

        On an unexpected startup failure, log a traceback to the configured sink
        first (PyObjC would otherwise route a raw callback exception only to stderr /
        the boot file). Then, for the **launchd-managed** run, ``os._exit(1)`` so
        launchd (``KeepAlive``) restarts the agent and releases the single-instance
        lock — swallowing-and-continuing would wedge it (un-initialized, holding the
        lock, never restarted; codex P1). For a **foreground/dev or ``--config``
        run**, a plain ``raise`` would be *swallowed by PyObjC* at this callback
        boundary (same reason :meth:`_refresh` documents), leaving a wedged process;
        so the error is stashed and the run loop is stopped, and :func:`run_agent`
        re-raises it from a normal frame for a clean terminal traceback + non-zero
        exit (with normal Python/atexit cleanup, not an abrupt ``os._exit``).
        """
        try:
            NSApplication.sharedApplication().setActivationPolicy_(
                NSApplicationActivationPolicyAccessory
            )
            self._config = self._load_config()
            self._labels = self._load_labels()
            self._build_surfaces()
            self._start_observer()
            self._start_reload_timer()
            # Call the UNGUARDED impl: the first paint is part of startup, so a
            # failure here must reach this handler (the guarded _refresh would
            # swallow it and leave the agent wedged — codex P1).
            self._refresh_impl()  # also seeds self._topology_sig from what it rendered
        except Exception as exc:
            log.critical("unexpected error during agent startup", exc_info=True)
            if self._is_managed_run:
                os._exit(1)  # launchd-managed: fail fast for a clean KeepAlive restart
            # Foreground/dev or --config: PyObjC would swallow a re-raise here, so
            # stash it and stop the loop; run_agent re-raises it from a normal frame.
            self._startup_error = exc
            AppHelper.stopEventLoop()

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
        # Periodically re-cap the launchd boot log so recurring AppKit/PyObjC stderr
        # warnings can't grow it unbounded across a long-lived session (it is
        # otherwise capped only at startup). Managed run only — a dev/--config run
        # must not zero the real agent's boot log.
        if self._is_managed_run:
            self._boot_cap_ticks += 1
            if self._boot_cap_ticks >= _BOOT_CAP_EVERY_TICKS:
                self._boot_cap_ticks = 0
                truncate_boot_log()
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
        # Cheap create/delete signal (every 1 s tick, like config/labels/displays): the
        # spaces plist flushes on Space create/delete (§3.4), so its mtime is a stat()-
        # cost watch that works whether or not CGS is available. Reorder is invisible
        # there — that needs the CGS read below.
        # Detect only — do NOT commit the baseline here; _refresh_impl commits it on a
        # successful render, so a failed render retries on the next tick (codex P2),
        # consistent with the CGS topology baseline below.
        if _mtime(self._spaces_plist_path) != self._spaces_plist_mtime:
            changed = True
        # Live-topology diff for REORDER: a Mission Control reorder fires no
        # notification and is invisible in the plist, so compare a live CGS signature
        # (DECISIONS 4.3). The CGS read (~1 ms, ~6 ms p99) is the one EXPENSIVE part, so
        # it runs every ~3 s on the AppKit main thread (off-main deferred — 4.2);
        # reorder freshness ≤3 s is imperceptible. The baseline is committed by
        # _refresh_impl only on a successful render, so a failed render retries on the
        # next ~3 s tick (codex P1; the cadence bounds the retry — no tight loop).
        self._topology_poll_ticks += 1
        if self._topology_poll_ticks >= _TOPOLOGY_POLL_EVERY_TICKS:
            self._topology_poll_ticks = 0
            topology_sig = self._live_topology_signature()
            if topology_sig is not None and topology_sig != self._topology_sig:
                log.debug("space topology changed (reorder); refreshing")
                changed = True
        if changed:
            self._refresh()

    # -- the hot path -----------------------------------------------------------

    @objc.python_method
    def _refresh(self) -> None:
        """Refresh every surface, logging any unexpected error to ``agent.log``.

        Called on user/system events (space switch, display change, config/label
        edits) — not a fixed tick: the 1 s poll only refreshes on a store mtime
        change, so failures here are event-paced, not per-second. Expected failures
        are handled specifically in :meth:`_refresh_impl` (no-silent-except policy);
        an *unexpected* one is logged at CRITICAL and the previous surfaces are kept.

        It is deliberately **not** re-raised: PyObjC swallows exceptions at the
        callback boundary, so propagating would neither reach :func:`sys.excepthook`
        nor terminate the process — it would just vanish to raw stderr and leave the
        agent wedged *without a trace in ``agent.log``*. ``os._exit`` here would be
        worse (restarting the whole agent over one transient refresh glitch, with
        restart-loop risk). So: log it where operators look, keep the last-good
        surfaces, and let the next event retry. (Startup differs — see
        :meth:`applicationDidFinishLaunching_`, which fails fast for a launchd
        restart.) The rotated ``agent.log`` bounds the log even if it recurs.
        """
        try:
            self._refresh_impl()
        except Exception:
            # Last-resort guard at the AppKit callback boundary: log (never silent),
            # keep the previous surfaces, retry on the next event.
            log.critical("unexpected error refreshing surfaces", exc_info=True)

    @objc.python_method
    def _refresh_impl(self) -> None:
        """Read the active Space, resolve its title, and update every surface."""
        # Capture the spaces-plist mtime BEFORE reading topology, so the baseline we
        # commit corresponds to the data we read. If cfprefsd atomically replaces the
        # plist during this refresh, the on-disk mtime moves past this value and the
        # next poll re-detects it — committing a post-read mtime would instead swallow
        # that create/delete (codex P1 TOCTOU).
        plist_mtime_at_read = _mtime(self._spaces_plist_path)
        # Include unlabelable Spaces so the menu/overlays surface every display.
        spaces, from_cgs = self._read_spaces_with_source(include_unlabelable=True)
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
        # Resync the change-detection baselines AFTER a render, so the next poll doesn't
        # re-fire for what we just reflected (codex P2):
        #  - CGS topology signature: only from a LIVE CGS read (`from_cgs`). The plist
        #    fallback's order/current is stale and would spuriously diff vs the next CGS
        #    poll; `enumerate_spaces` raises on a nil/empty result, so `from_cgs` implies
        #    real data.
        #  - spaces-plist mtime: always. `read_spaces()` is self-recovering (returns []
        #    for a missing/corrupt plist, never raises), so an empty result is a STABLE
        #    "nothing parseable" state, not a transient one — committing the mtime
        #    consumes the signal and avoids a 1 Hz re-fire loop on a corrupt plist. A
        #    genuine mid-write is self-correcting: cfprefsd writes atomically and the
        #    write's completion bumps the mtime again, so it's re-detected next tick.
        if from_cgs:
            self._topology_sig = _topology_signature(spaces)
        self._spaces_plist_mtime = plist_mtime_at_read

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
            # Name the right Accessibility row: the signed cask bundle (frozen) appears as
            # "spacelabel"; a legacy pipx/dev run appears under the Python interpreter.
            if getattr(sys, "frozen", False):
                entry_hint = "enable “spacelabel”"
            else:
                entry_hint = (
                    "enable this agent's entry (a legacy pipx/dev install appears under your "
                    "Python interpreter, e.g. “python3.x”, not “spacelabel”)"
                )
            self._disable_click_to_switch(
                f"Accessibility permission is required — {entry_hint} in System Settings → "
                "Privacy & Security → Accessibility, then re-enable click-to-switch.",
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
            # Per-Space notes ride on the label entry, keyed by the Space UUID
            # (DECISIONS.md 9.10); show them under the title unless overlay.show_notes
            # is off. An unlabeled Space with notes shows "Desktop N" as the title.
            label = self._labels.get(current.uuid)
            notes = label.notes if (label is not None and config.overlay.show_notes) else []
            font = geometry.overlay_font_size(display.size_pt, config.overlay.font_size)
            note_font = geometry.overlay_note_font_size(font, config.overlay.note_font_size)
            overlay: Overlay | None = self._overlays.get(display.uuid)
            if overlay is None:
                overlay = Overlay(font_size=font, bold=config.overlay.bold)
                self._overlays[display.uuid] = overlay
            else:
                # Re-apply font on a live config reload (no-op when unchanged).
                overlay.set_font(font, config.overlay.bold)
            screen = screens_by_uuid.get(display.uuid) or NSScreen.mainScreen()
            overlay.reposition(screen, config.overlay.corner, config.overlay.margin)
            overlay.set_content(text, notes, note_font_size=note_font)

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
        self._wallpaper.render_and_set(
            title,
            screen=screen,
            position=config.wallpaper.position,
            font_size=config.wallpaper.font_size,
        )

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
        spaces, _from_cgs = self._read_spaces_with_source(include_unlabelable=include_unlabelable)
        return spaces

    @objc.python_method
    def _read_spaces_with_source(
        self, *, include_unlabelable: bool = False
    ) -> tuple[list[Space], bool]:
        """Read Spaces; return ``(spaces, from_cgs)``.

        ``from_cgs`` is True only when the live CGS read succeeded; False means the
        stale spaces-plist fallback (or empty). Callers that record a live-topology
        baseline use it to avoid seeding from the plist's intentionally-stale
        ordering/current-Space data (which would spuriously diff vs the next CGS poll).
        """
        from spacelabel.platform import cgs, spaces_plist

        try:
            return cgs.enumerate_spaces(include_unlabelable=include_unlabelable), True
        except cgs.CGSUnavailableError as exc:
            # Only the expected CGS-symbol absence falls back. An ImportError here is a
            # real import regression (the agent is macOS-only with PyObjC as a hard dep),
            # so let it propagate -> CRITICAL via _refresh's guard / startup fail-fast,
            # rather than silently switching to the stale plist (codex P2).
            log.warning("CGS unavailable; falling back to plist: %s", exc)
        try:
            return spaces_plist.read_spaces(), False
        except (OSError, ValueError) as exc:
            log.warning("plist fallback failed: %s", exc)
            return [], False

    @objc.python_method
    def _live_topology_signature(self) -> tuple[tuple[str, str, int], ...] | None:
        """Live CGS topology signature for the poll, or ``None`` if unreadable.

        Live CGS **only** (no plist fallback): a reorder is invisible in the stale
        spaces plist (cfprefsd flushes it on create/delete only — DECISIONS 3.4), so
        the plist can't drive this. A failed read returns ``None`` so the poll skips
        the tick rather than mistaking a transient CGS hiccup for a topology change
        (which would spuriously refresh). Logged at DEBUG only — not per-second WARNs.
        """
        try:
            from spacelabel.platform import cgs

            spaces = cgs.enumerate_spaces(include_unlabelable=True)
        except CGSUnavailableError as exc:  # module-level name (not the in-try import)
            # Expected degradation (CGS symbols unavailable): quiet.
            log.debug("topology poll: CGS unavailable: %s", exc)
            return None
        except Exception:
            # Anything else must NOT escape this NSTimer callback — including an
            # ImportError from the cgs import line above, or a real bug in the
            # CGS/display stack (the agent is macOS-only with PyObjC as a hard dep, so
            # these are regressions, not expected absences). Surface ONCE at WARNING:
            # reorder detection lives only here, so a silently-disabled feature must be
            # diagnosable, not a DEBUG black hole (codex P2/P3). Re-armed on recovery.
            if not self._topology_read_warned:
                self._topology_read_warned = True
                log.warning(
                    "topology poll: unexpected read error; reorder detection degraded",
                    exc_info=True,
                )
            return None
        self._topology_read_warned = False  # recovered -> re-arm the one-shot warning
        return _topology_signature(spaces)

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
