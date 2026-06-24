# Fix — click-to-switch on a secondary display + Prefs/pill "Desktop N" consistency  (items O + V)

**Model:** Opus 4.8 · **effort:** high (escalate to xhigh on the macOS-mapping investigation). **Fresh session + fresh branch off latest `main`.** Part of the Phase-6 fix set — see [`fix-sessions-overview.md`](fix-sessions-overview.md).

## Items (full diagnosis in `improvements.md`)
- **O** — click-to-switch **fails on a secondary display**; works on the primary.
- **V** — a Space shown "Desktop 3" in Preferences appears as "4" in the pill (numbering mismatch). **Same root family as O** — fix together so all surfaces agree on one ordinal source.

## What's already known (Phase-6 investigation, don't re-derive)
- `labeling.assign_ordinals` (`labeling.py:72`) numbers Spaces by spacelabel's **CGS `CGSCopyManagedDisplaySpaces` enumeration order** (4K/left → ords 1–2, portrait/right → 3–14). Click-to-switch posts `Ctrl+ordinal` (hotkey id `117+ordinal`, `switching.py`).
- All "Switch to Desktop 1–15" hotkeys (ids 118–132) are **bound + enabled** → **not** a missing-hotkey/H6 issue.
- So macOS's "Switch to Desktop N" numbering **does not match** spacelabel's CGS enumeration order once Spaces span displays with "separate Spaces" ON. The posted chord lands on the wrong desktop for secondary-display Spaces. Near-silent failure (brushes the never-silently-no-op rule, DECISIONS §9.5).

## Do this
1. **Empirically pin macOS's mapping first (on hardware, dual display):** determine the real (display, Space) → "Desktop N" mapping the Ctrl+N shortcuts follow — e.g. manually press Ctrl+1..N and record which physical Space each activates, and compare to `com.apple.spaces.plist` global order vs `CGSCopyManagedDisplaySpaces` order. This tells you whether a corrected ordinal source exists.
2. **Then pick the fix:**
   - **(a)** if a reliable authoritative ordering exists → compute the click-to-switch ordinal from **that** (not raw CGS enumeration), so `Ctrl+N` targets the right Space on every display; **and** make `prefs.py` + `menubar.py` derive the displayed "Desktop N" from the **same** source (fixes V).
   - **(b)** if `Ctrl+N` genuinely cannot target a secondary display's Space (a macOS limitation) → **disable click-to-switch for those Spaces with a visible reason** ("click-to-switch is only reliable on the main display"), never a silent no-op.
3. Ensure **one ordinal source of truth** shared by pills, Preferences, and the switch path (kills V).

## Read first
`labeling.py` (`assign_ordinals`/`ordinal_for_uuid`/`title_for`/`pill_text`), `platform/switching.py` (`parse_desktop_binding` + the `CGEventPost` path), `agent/app.py` (`_on_pill_clicked`), `agent/prefs.py` + `agent/menubar.py` (how each numbers), `DECISIONS.md` §9.5 / §6.1, plan F2/F3, `improvements.md` items O & V.

## Acceptance
Clicking a pill on **each** display switches to the correct Space (or, where impossible, disables with a clear reason — no silent failure); the "Desktop N" a Space shows is **identical** in Preferences and in the pill. Add tests for the ordinal-source helper. Record the macOS-mapping finding in `DECISIONS.md`.

## Parallelization
Touches `app.py` (+ `prefs.py`, `menubar.py`, `switching.py`) — **Track A** (see overview). Run it as the *first* Track-A unit; others rebase on it. Conflicts with L / Z+P+Q / T+U+W on `app.py`.

## Before committing
Gates (`ruff` + `ruff format --check` + `mypy src` + `pytest`) + **codex review loop** until clean (CLAUDE.md). Conventional Commit (`fix(switching): correct multi-display ordinal / Desktop-N mapping`). Ask Max before committing/pushing. Mark O+V done in `improvements.md`, tick the overview, update `docs/VERIFICATION.md` (P1.7/§D/item O+V).
