# Fix — overlay behavior: clear on fullscreen + per-display on/off + suppress on unlabeled  (items Z + P + Q)

**Model:** Sonnet 4.6 · **effort:** medium. **Fresh session + fresh branch off latest `main`.** Part of the Phase-6 fix set — see [`fix-sessions-overview.md`](fix-sessions-overview.md).

## Items (full diagnosis in `improvements.md`)
- **Z** — entering a fullscreen Space leaves the corner overlay + HUD showing the **previous** Space's label (stale); they should clear / go neutral (plan H2/H3). *(Functional-ish — do this one first.)*
- **P** — let the corner overlay be enabled/disabled **per display** (not just global `modes.overlay`).
- **Q** — option to **suppress** the overlay on displays whose current Space is unlabeled / the single default no-UUID Space (don't show the `Desktop N` placeholder).

## Do this
- **Z:** in `_update_overlays`/`_update_hud` (`app.py`), when the active display's current Space resolves to **no labelable Space** (fullscreen/tiled / no match), **order-out that display's overlay** and suppress the HUD — don't leave the last render up. Check whether a fullscreen transition even fires `activeSpaceDidChange` (`platform/notifications.py`); if not, the 1 s poll's live-topology diff should catch it.
- **P:** persist a per-display overlay flag (extend `displays.json` like the custom-name pattern); expose in the `display` CLI + the Preferences per-display rows; `_update_overlays` skips displays toggled off.
- **Q:** add a config flag (default TBD) to skip the overlay when the current Space has no real label; reuse the `title_for` labelable check.

## Read first
`agent/overlay.py`, `agent/app.py` (`_update_overlays`/`_update_hud`/`read_active_space_uuid`), `platform/notifications.py` (fullscreen transition?), `store.py` (displays.json pattern), `agent/prefs.py` (per-display rows), `DECISIONS.md` §6.3, `improvements.md` items Z/P/Q.

## Acceptance
On a fullscreen Space, that display gets **no** overlay and no HUD (no stale label); overlays can be toggled per display; the overlay is suppressed on unlabeled/default Spaces per the new flag. Tests for the labelable-gate + per-display skip.

## Parallelization
Touches `app.py` (+ `overlay.py`, `store.py`, `prefs.py`) — **Track A** (shares `app.py` with O+V/L/T+U+W; `store.py` with L; `prefs.py` with O+V/T+U+W). Rebase on the earlier Track-A units.

## Before committing
Gates + **codex review loop** until clean. Conventional Commit (`fix(overlay): clear on fullscreen; add per-display + unlabeled suppression`). Ask before commit/push. Mark Z/P/Q done in `improvements.md`, tick the overview, update `docs/VERIFICATION.md` (§C / item Z).
