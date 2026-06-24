# Fix — Preferences/menu-bar UX: window placement + re-surface, inline-edit, menu-bar-off icon  (items T + U + W)

**Model:** Sonnet 4.6 · **effort:** medium. **Fresh session + fresh branch off latest `main`.** Part of the Phase-6 fix set — see [`fix-sessions-overview.md`](fix-sessions-overview.md).

## Items (full diagnosis in `improvements.md`)
- **T** — Preferences window (and the NSColorPanel) open at the **left display's bottom-left**; should **center on the active screen**. And a Preferences window **hidden behind other windows can't be re-surfaced** (accessory app, no Cmd+Tab) — re-selecting Preferences should bring it to front.
- **U** — in the Label edit field, **Cmd+V/Cut/Copy don't work** (right-click→Paste does) — accessory app has no Edit menu; and **clearing a label doesn't live-revert** the outline to `Desktop N` (stale until reopen).
- **W** — turning **Menu-bar title OFF** shows an **empty quadrant** instead of the `square.dashed` neutral icon (plan B6).

## Do this
- **T:** center the Preferences window on the active `NSScreen` on `show()`; on `openPreferences_`, `makeKeyAndOrderFront:` + `NSApp.activate(ignoringOtherApps:true)` so a hidden window resurfaces (consider a transient activation policy while a window is open so it appears in Cmd+Tab).
- **U:** add a minimal **Edit menu** (Undo/Cut/Copy/Paste/Select-All with standard selectors + key equivalents) so the field editor gets Cmd-C/V/X; refresh the outline row after a commit/clear (`controlTextDidEndEditing_`/`_commit`, `prefs.py:376`) so clearing reverts to `Desktop N` live.
- **W:** fix `set_inactive` (`menubar.py:449`) so menu-bar-off renders the `square.dashed` SF Symbol + the "menu-bar label off" accessibility label (check the buttons-row view isn't still occupying the item / the symbol image is applied).

## Read first
`agent/prefs.py` (`show()`, `_commit`, color cell), `agent/app.py` (`openPreferences_`, accessory policy `:178`, menu construction), `agent/menubar.py` (`set_inactive`), `improvements.md` items T/U/W, plan B6 / §D.

## Acceptance
Prefs + color picker open centered on the active screen and can be re-surfaced when hidden; Cmd+V works in the Label field and clearing a label live-reverts to `Desktop N`; menu-bar-off shows the `square.dashed` icon (not empty).

## Parallelization
Touches `prefs.py` + `app.py` + `menubar.py` — **Track A** (shares `app.py` with O+V/L/Z; `prefs.py` with O+V/Z; `menubar.py` with O+V). Best run **last** of Track A (rebase on the others).

## Before committing
Gates + **codex review loop** until clean. Conventional Commit (`fix(prefs): center+resurface window, Edit-menu paste, live revert; fix(menubar): inactive icon`). Ask before commit/push. Mark T/U/W done in `improvements.md`, tick the overview, update `docs/VERIFICATION.md` (§D / B6).
