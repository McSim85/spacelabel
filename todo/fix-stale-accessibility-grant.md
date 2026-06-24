# Fix — detect a STALE Accessibility grant before telling the user to enable it  (item L)

**Model:** Opus 4.8 · **effort:** high. **Fresh session + fresh branch off latest `main`.** Part of the Phase-6 fix set — see [`fix-sessions-overview.md`](fix-sessions-overview.md).

## Item (full diagnosis in `improvements.md` item L)
With the ad-hoc-signed `.app`, the cdhash **rotates each release**, so an already-enabled "spacelabel" Accessibility entry goes **stale** after an upgrade — `AXIsProcessTrusted()` returns False even though the entry is checked, and re-triggering doesn't help (verified live). Today the guidance just says "enable Accessibility", which is misleading when an enabled-but-stale entry exists; the user must **remove + re-add** it.

## Do this (the app can detect stale-vs-missing — it can't read TCC.db, but it can read its own identity)
1. Read the running bundle's **cdhash** via the Security framework (`SecCodeCopySelf` → `SecCodeCopySigningInformation`, `kSecCodeInfoUnique`), bound feature-detected like the AX funcs in `switching.py`.
2. Persist `last_cdhash` + an `ax_was_trusted` flag in the store (small state file or config).
3. When `AXIsProcessTrusted()` is False **and** (`current_cdhash != last_cdhash` → app updated, OR `ax_was_trusted` was set) → treat as **stale**, not missing.
4. **Branch the guidance** in the click-to-switch availability path / ⚠️ reason row (`app.py`, `_sync_click_to_switch_state`; the B22 reason rows): **stale** → "Accessibility for 'spacelabel' went stale after an app update — REMOVE the existing entry (–) in Settings → Accessibility, then click a pill to re-add it" (+ open the Accessibility pane); **never-granted** → the existing "grant Accessibility" message.

## Read first
`platform/switching.py` (`accessibility_trusted`, the HIServices bind pattern), `agent/app.py` (`_sync_click_to_switch_state`, the AX reason rows / `_SETTINGS_URL_ACCESSIBILITY`), `store.py` (where to persist `last_cdhash`/`ax_was_trusted`), `improvements.md` items L + E, `docs/UI.md` Accessibility section. **Durable cure** (out of scope here) = Developer-ID + notarization (stable cdhash) — item E.

## Acceptance
On a stale grant (cdhash changed since last trusted) the agent shows the **remove-and-re-add** guidance, not "enable it"; on a first-ever run it shows the plain grant message. Tests mock `AXIsProcessTrusted` + the persisted cdhash for both branches.

## Parallelization
Touches `app.py` + `switching.py` + `store.py` — **Track A** (shares `app.py` with O+V / Z+P+Q / T+U+W; shares `switching.py` with O+V; `store.py` with Z+P+Q). Rebase on O+V if run after it.

## Before committing
Gates + **codex review loop** until clean. Conventional Commit (`feat(switching): detect stale Accessibility grant + targeted guidance`). Ask before commit/push. Mark L done in `improvements.md`, tick the overview, update `docs/VERIFICATION.md` (item L).
