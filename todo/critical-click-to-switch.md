# spacelabel — Critical: Implement Click-to-Switch Pills

**Recommended model:** Opus 4.8 · **effort:** high (xhigh if CGEventPost hits a hard
wall under SIP-on). Set `/model` and `/effort` before running.
**Run in a fresh session.**

---

## Shared Baseline

- **Project:** `spacelabel` — open-source (MIT) macOS menu-bar + CLI tool that labels
  Spaces, **keyed by Space UUID** (reorder-proof — the whole point vs WhichSpace).
- **Locked stack:** Python; PyObjC (AppKit); `objc.loadBundleFunctions` of CoreGraphics
  for CGS reads. No SIP disable. pipx install. CLI + UI. Four display modes (menu-bar,
  HUD, overlay, experimental wallpaper = cosmetic/best-effort).
- **Engineering standards:** PEP 8 / 257 / 484 enforced via ruff + mypy `--strict`;
  no silent exception handling (no bare `except: pass`/`continue`); stdlib `logging`;
  stdlib-first (only `click` beyond PyObjC). Conventional Commits.
- **Portability:** never hardcode display/Space topology; discover at runtime.
- **Hand-off rule:** read `DESIGN.md` + `DECISIONS.md` (esp. §0, §9.5, "Cross-phase
  impact") before acting; update `DECISIONS.md` at the end.

---

## Background

The menu-bar buttons row (compact per-Space pills, one per Space across all
displays, drawn in a custom Core Graphics view) is fully implemented and ships
display-only. The `menubar.click_to_switch` config key exists (DECISIONS §9.5).
When a user enables it, the agent logs a visible one-time warning:

```
menubar.click_to_switch is enabled but not implemented yet: pills are display-only
```

This is the correct no-silent-no-op policy (DECISIONS §9.5), but the feature
now needs a real implementation.

Relevant code:
- `src/spacelabel/agent/menubar.py` — `ButtonsRowView` (custom CG view with pills)
  and `MenuBarItem`
- `src/spacelabel/agent/app.py` — `AppDelegate._refresh` (where the warning fires,
  around line 395–403)
- `DECISIONS.md §9.5` — full policy: opt-in, guide the two one-time setup steps,
  `CGEventPost` Ctrl+N, live UUID→ordinal map, disable-with-reason fallback

---

## Your task this session

Implement the `menubar.click_to_switch` pill-click Space-switching path end-to-end.

### 1. Read first
- `DESIGN.md` §6 (display modes) and §8.1 (CLI surface)
- `DECISIONS.md` §9.4 (buttons-row design), §9.5 (click-to-switch policy), §9.7
  (config keys)
- `src/spacelabel/agent/menubar.py` — understand `ButtonsRowView` and how pills
  are drawn and laid out
- `src/spacelabel/agent/app.py` — `AppDelegate` and the `_refresh` / `_on_space_change`
  flow

### 2. Implementation requirements (from DECISIONS §9.5)

**a. Per-pill hit-testing in `ButtonsRowView`**
- Track each pill's bounding rect in the CG view's coordinate space during `drawRect_`
  (or a separate layout pass)
- Override `mouseDown_` (or use an `NSClickGestureRecognizer`) to map the click
  point to a pill → a Space UUID
- `ignoresMouseEvents` must be `False` when `click_to_switch` is enabled; `True`
  when disabled — toggle this at runtime when the config is reloaded

**b. Live UUID→ordinal map at click time**
- Ordinals (the N in "Switch to Desktop N") shift when Spaces are reordered
- **Never cache the map** — rebuild it from the live CGS read at every click
- `labeling.assign_ordinals` already provides ordinals keyed by `id(space)`
- At click time: re-enumerate via `cgs.enumerate_spaces`, build the map, look up the
  clicked Space's UUID → ordinal N
- If the live read fails (CGSUnavailableError), disable with a visible reason (see §d)

**c. Synthetic Ctrl+N via `CGEventPost`**
- Use `Quartz.CGEventCreateKeyboardEvent(None, kVK_ANSI_N, True/False)` +
  `Quartz.CGEventSetFlags(event, kCGEventFlagMaskControl)` + `Quartz.CGEventPost`
- `kVK_ANSI_N` is `0x2D`; or use `CoreGraphics.CGEventCreateKeyboardEvent` via
  PyObjC
- Post a key-down then key-up pair

**d. One-time Accessibility + Mission Control setup guide**
- On first click (when `click_to_switch` just became enabled or the first pill is
  clicked), verify:
  1. The "Switch to Desktop N" Mission Control shortcut is set in System Settings →
     Keyboard → Keyboard Shortcuts → Mission Control. The standard mapping is
     Ctrl+1…Ctrl+N (0-based index of the shortcut, verified at runtime by checking
     the `com.apple.symbolichotkeys` plist or by attempting a test-post and observing).
  2. Accessibility permission: `AXIsProcessTrusted()` (AppKit `NSAccessibility`) or
     `AXIsProcessTrustedWithOptions` prompting the user
- If either check fails or the shortcut can't be confirmed, **disable** the feature
  in the running agent (set `_click_to_switch_available = False`), log a WARNING
  with the specific reason (not a generic failure), and update the menu-bar / prefs
  to surface why it's disabled — **never silently no-op** (DECISIONS §9.5)

**e. Fallback behavior**
- If `CGEventPost` fires but the Space doesn't switch (observable by re-reading
  the live UUID ~200 ms later — or simply not checking and accepting best-effort),
  that is acceptable: the feature is opt-in and documented as needing the setup steps
- If the whole path is unavailable (permissions denied, shortcut absent), disable
  with a visible reason in the UI

### 3. Phase-6 hardware note
DECISIONS §9.5 flags `CGEventPost` Ctrl+N under SIP-on + Accessibility as a
**Phase-6 must-verify** item. Implement the full path here; if you hit a SIP/Sandbox
wall that prevents verification without hardware, document it clearly and write the
code so Phase 6 can verify by running `spacelabel agent --debug` on the reference
machine and clicking a pill.

### 4. Tests
- Unit tests for the hit-testing logic (mock the pill layout, verify UUID resolution)
- Unit tests for the live UUID→ordinal map building (mock `enumerate_spaces`)
- Smoke test: when `click_to_switch=False`, `ignoresMouseEvents` must be `True`
- All existing tests must still pass: `uv run pytest`

### 5. Lint / type-check
```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```
All gates must be green before committing.

---

## Deliverables

1. **Working implementation** in `src/spacelabel/agent/menubar.py` (and `app.py` for
   the warning removal / wiring)
2. **Tests** in `tests/` covering the new code
3. **`DECISIONS.md` update** — resolve the `CGEventPost` open question in §9.5 with
   your findings (confirmed working, or: confirmed blocked + why + documented fallback)
4. Remove the "not implemented yet" warning from `app.py` (it is replaced by the
   real disable-with-reason path)
