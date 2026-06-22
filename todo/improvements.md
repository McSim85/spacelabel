# spacelabel — Non-critical Improvements (batched session)

**Recommended model:** Sonnet 4.6 · **effort:** medium. Set `/model` and `/effort`
before running.
**Run in a fresh session.** Work through the items in priority order within the
session; it is fine to ship a subset and leave the rest for a follow-up.

---

## Shared Baseline

- **Project:** `spacelabel` — open-source (MIT) macOS menu-bar + CLI tool that labels
  Spaces, keyed by Space UUID (reorder-proof vs WhichSpace). pipx install.
- **Locked stack:** Python; PyObjC (AppKit); `objc.loadBundleFunctions` of CoreGraphics
  for CGS reads. No SIP disable. CLI + UI. Four display modes (menu-bar, HUD,
  overlay, experimental wallpaper = cosmetic/best-effort).
- **Engineering standards:** PEP 8 / 257 / 484, ruff + mypy `--strict`; no silent
  exception handling; stdlib `logging`; stdlib-first (only `click` beyond PyObjC).
  Conventional Commits.
- **Portability:** discover displays/Spaces at runtime; never hardcode topology.
- **Hand-off rule:** read `DESIGN.md` + `DECISIONS.md` (esp. §0, §9, "Cross-phase
  impact") before acting; update `DECISIONS.md` at the end with any new decisions.

---

## Items

Work through these in order. Each is independent; skip any that turn out to be
more complex than expected and note it at the end for a future session.

---

### A. Per-screen overlay note/TODO body  *(v0.2)*

> **⚠ Superseded — do NOT build the per-display design below.** This shipped instead
> as a per-**Space** notes/task queue (#17, v0.2.0; `DECISIONS.md` §9.10) — keyed by
> Space UUID, with the `note` CLI group and `☐/☑` overlay glyphs. The only live remnant
> of this section is the optional **A.4** (a Preferences notes editor), still unbuilt and
> tracked in `DECISIONS.md` §9.10. The rest of A is historical.

**Context (from Phase 5 plan):**
The corner overlay currently shows the Space label (bold title, configurable size).
Add a per-display free-text note body below the title — a small notes/TODO field
visible at a glance on each display's corner overlay.

**Read first:**
- `DESIGN.md` §6.3 (persistent corner overlay spec) and §7 (data model)
- `DECISIONS.md` §9.6 (preferences inline-edit), §9.7 (config keys), §9.8 (labels.json fields)
- `src/spacelabel/agent/overlay.py` — current `Overlay` implementation
- `src/spacelabel/store.py` — `displays.json` pattern (`load_display_labels` /
  `set_display_label`); mirror this for notes

**Implementation:**
1. **Note store:** add a `displays_notes.json` (display UUID → note string) under
   `~/Library/Application Support/spacelabel/`, following the same atomic
   flock read-modify-write pattern as `displays.json`. Add `displays_notes_file` /
   `displays_notes_lock` to `StorePaths`. Or extend `displays.json` to hold a `note`
   field per display (simpler — one file, same pattern). **Pick the simpler approach**
   (extend `displays.json`; a separate file is not worth the complexity for a string).
2. **CLI:** `display note set <uuid|current> <text>` / `display note clear <uuid|current>`
   (additive to the existing `display` group in `cli.py`)
3. **Overlay panel:** extend `Overlay.set_text` (or add `set_note`) to accept a
   note string; render it below the bold title in the normal-weight system font at
   `overlay.font_size` (or a new configurable `overlay.note_font_size`); the panel
   auto-resizes to fit both title and note. Reuse `geometry.anchor_origin` for
   placement. Reposition on `didChangeScreenParameters`.
4. **Preferences window:** editable multi-line `NSTextView` (or a single-line
   `NSTextField` if multi-line is complex) per display row in the preferences
   `NSOutlineView`. Commit on Return/focus-loss, cancel on Esc (DECISIONS §9.6).
5. **Agent reload:** the 1 s mtime-poll already watches `displays.json`; extend it
   to include `displays_notes.json` (or it's free if we extended `displays.json`).
6. **Tests:** unit tests for the note store read/write; smoke test for the overlay
   panel receiving a note string.

---

### B. Wallpaper: persist captured original across restarts  *(v0.3)*

**Context:**
`WallpaperRenderer` captures the current desktop image as a per-display base on
first render. After an agent restart, the current image may already be our own
cached composite — meaning the captured "original" is a labeled image, not the
user's real wallpaper. Phase 4 handles this at runtime (skips render if the base is
our cache), but the original is not persisted to disk, so after a restart with a
labeled wallpaper already set the agent can't recover the original.

**Read first:**
- `src/spacelabel/agent/wallpaper.py` — `WallpaperRenderer`, the `_base_*` cache
  logic, and the "skip if base is our cache" guard
- `DECISIONS.md` §7 (wallpaper policy: cosmetic/best-effort, never edit WallpaperAgent
  store)

**Implementation:**
1. On first successful capture of a real (non-cache) base image per display, write
   it to a persistent "originals" sidecar path, e.g.
   `~/Library/Caches/spacelabel/wallpaper/original-<display-uuid>.png` (separate
   from the labeled composite cache).
2. On agent start, if the current wallpaper for a display is inside our composite
   cache AND an originals sidecar exists, load the sidecar as the base instead of
   skipping. If the sidecar doesn't exist either, keep the existing "skip" behavior.
3. **Detect user wallpaper changes:** if the current wallpaper path changes to
   something outside our cache (the user set a new wallpaper), update the sidecar
   and re-render. Poll: the 1 s mtime-poll is sufficient (or watch the preferences
   plist — but polling the current path via `NSWorkspace.desktopImageURL(for:)` is
   simpler). On change: capture the new real wallpaper → overwrite the sidecar →
   re-render the composite.
4. **Per-display font sizing:** `WallpaperRenderer` currently uses a fixed font size.
   Add `wallpaper.font_size` (int|`"auto"`, default `"auto"`). `"auto"` computes a
   size proportional to the display's short side (mirror the HUD/overlay formula
   from `geometry.py`); an int override is honored.
5. **Purge stale originals:** `_purge_stale_cache` (which already cleans composite
   PNGs) should also remove originals for displays that are no longer connected.

---

### C. CLI shell autocomplete  *(v0.2)*

**Context (from Phase 5 plan):**
click ships tab-completion built-in (zsh/bash/fish). Max uses zsh. The whole
completion system is essentially free; the work is adding **dynamic completers**
for UUID-bearing arguments and shipping a `spacelabel completion install` helper.

**Read first:**
- `src/spacelabel/cli.py` — the full command tree
- `docs/CLI.md` — command spec; check if completion is already mentioned
- click docs on shell completion (the `shell_complete` parameter + `Context.info_name`)

**Implementation:**
1. **Dynamic completer for `<uuid|current>`:** `label set/clear`, `display set/clear`,
   `display note set/clear` all take a UUID or `current`. Provide a completer that
   returns `["current"]` + live Space UUIDs from `cgs.enumerate_spaces` (falling back
   to `spaces_plist.read_spaces` if CGS is unavailable). For `label clear`: also
   include UUIDs from `store.load_labels` (labeled Spaces only).
2. **Choice/enum completion** is automatic for `click.Choice` params (already used
   for `mode`, `config get/set` — verify it works out of the box).
3. **`spacelabel completion install`** subcommand:
   - Detects the user's shell (`$SHELL`) and emits the appropriate activation snippet
   - For zsh: adds `eval "$(_SPACELABEL_COMPLETE=zsh_source spacelabel)"` to
     `~/.zshrc` (or prints the line with instructions if `--dry-run`)
   - For bash: adds the equivalent to `~/.bashrc`
   - For fish: writes to `~/.config/fish/completions/spacelabel.fish`
   - Idempotent (checks if the line is already present before appending)
4. **Document:** add a "Tab completion" section to `docs/CLI.md`.
5. **Tests:** unit tests for the completers (mock `enumerate_spaces`/`load_labels`);
   smoke test that `spacelabel completion install --dry-run` prints the right snippet
   for zsh.

---

### D. Rotate `agent.err.log` (launchd stderr)  *(v0.2)*

**Context:**
`agent.log` is already rotated by `RotatingFileHandler` (1 MB × 4 = 4 MB max,
`logging_setup.py` lines 153–157). The launchd `StandardErrorPath` file
`agent.err.log` is **not** — it captures raw Python tracebacks and any pre-logging
stderr output. Under normal operation it stays near-empty, but a crash loop will
grow it without bound.

**Read first:**
- `src/spacelabel/logging_setup.py` — existing `RotatingFileHandler` setup
- `src/spacelabel/install.py` — `build_launch_agent` (the plist builder);
  `StandardErrorPath` is set here
- `DESIGN.md` §9.2 (LaunchAgent plist spec)

**Options (pick one):**

*Option A — rotate in Python at agent startup (simplest, no new dep):*
On `run_agent` startup, before `setup_logging`, check `agent.err.log` size and
rotate manually (rename to `.1`–`.3`, drop the oldest) if it exceeds 1 MB.
One small helper function; no external tool needed.

*Option B — `newsyslog` config (macOS-native, zero code in the agent):*
Drop a config file at `/etc/newsyslog.d/spacelabel.conf` or
`~/Library/newsyslog.d/spacelabel.conf`:
```
~/Library/Logs/spacelabel/agent.err.log  640  3  1024  *  J
```
`newsyslog` runs daily via `com.apple.newsyslog` and rotates at 1 MB (1024 KB),
keeping 3 backups, sending `SIGHUP` to reopen (`J` = HUP signal — launchd-managed
processes tolerate this). The user-level path `~/Library/newsyslog.d/` exists on
macOS 12+ (Monterey and later); Tahoe is covered.

**Recommendation:** Option B — zero agent code, zero runtime overhead, and rotation
happens even if the agent is not running. If `~/Library/newsyslog.d/` turns out to
be absent on Tahoe, fall back to Option A.

**Implementation (Option B first, A as fallback):**
1. Verify `~/Library/newsyslog.d/` is supported on macOS 26.5.1 (check
   `/etc/newsyslog.conf` includes pattern or read `man newsyslog.conf`).
2. `install.py`: `_install_newsyslog_conf()` — write the one-line conf to
   `~/.config/newsyslog.d/` or `~/Library/newsyslog.d/` (whichever exists);
   `uninstall` removes it.
3. `spacelabel install` calls `_install_newsyslog_conf()`; add a note to the
   install confirmation output.
4. If Option B is not viable, implement Option A as a `_rotate_err_log(path, max_bytes, backup_count)` helper called at the top of `run_agent`.
5. **Tests:** unit test for the rotation helper (Option A) or a smoke test that
   the conf file is written/removed correctly (Option B).

---

### E. Signed `.app` bundle for proper Accessibility/TCC identity  *(v1.0 packaging)*

**⚠ Larger than the other items — likely its own session, and it relaxes locked
decisions 2.7 / 6.3, so it needs a deliberate `DECISIONS.md` update (not a quiet
override). Pairs with `critical-release-automation.md` (the other distribution work).**

**Context (surfaced live by click-to-switch, DECISIONS §9.5 / §6 residual risk):**
On the pipx path the agent process is the ad-hoc-signed Homebrew `python3.x`
(`Signature=adhoc`, `flags=0x2`, no TeamIdentifier — confirmed on the reference
machine). macOS TCC attributes and **displays** Accessibility by the code-signed
binary identity, so the Accessibility prompt/list shows **"python3.x"**, not
"spacelabel", and `brew upgrade python` changes the binary cdhash → the grant
**silently drops** until re-granted. `argv[0]`/process-title tricks do **not** change
this (TCC ignores them). The only fix is shipping a real, code-signed `.app` bundle.

**Read first:**
- `DECISIONS.md` §2.7 (accessory policy, no LSUIElement/bundle today), §6.3 (no
  codesigning/LV today), §6 residual risks (the TCC note), §9.5 (click-to-switch)
- `src/spacelabel/install.py` — current LaunchAgent program path (the pipx shim)
- `DESIGN.md` §6 (display modes), §9 (install/runtime)

**Implementation (sketch — confirm approach in the DECISIONS update first):**
1. Build a `spacelabel.app` (py2app, or a hand-rolled bundle whose `Contents/MacOS`
   main executable is the interpreter/bootstrap so the process *is* the bundle) with
   `Info.plist`: `CFBundleName=spacelabel`, `CFBundleIdentifier=dev.mcsim.spacelabel`
   (reuse the existing reverse-DNS constant), `LSUIElement`/accessory as appropriate.
2. **Code-sign** the bundle — ad-hoc is enough to get a *stable* TCC identity across
   rebuilds *only if* the cdhash is stable; a Developer ID signature (+ notarization)
   is what makes the grant durable across `brew upgrade` and gatekeeper-clean for
   distribution. Decide ad-hoc vs Developer ID in the DECISIONS update.
3. Point the LaunchAgent at the bundle's executable (`spacelabel.app/Contents/MacOS/…`)
   instead of the pipx shim, or keep both paths and document which gives the named
   TCC entry. Keep `dev.mcsim.spacelabel` as the single identity (DECISIONS 6.7).
4. **Verify on hardware:** the Accessibility prompt now reads "spacelabel"; the grant
   survives a `brew upgrade python`; click-to-switch still posts the chord.
5. **Docs:** update `docs/UI.md` §2.4 + `DECISIONS.md` §2.7/§6.3/§6 to reflect the
   shipped bundle; remove the "appears under python3.x" caveat once it no longer applies.

---

### F. Live pill/overlay refresh on Space reorder & create/delete  *(v0.2)*

**Context (surfaced by Max while testing click-to-switch):**
The agent refreshes on `activeSpaceDidChange` (workspace center) +
`didChangeScreenParameters` (default center) — see `platform/notifications.py`.
**Reordering** Spaces in Mission Control fires neither (the active Space is
unchanged), so the menu-bar pill row, the dropdown, and the overlays stay visually
stale until the next Space switch. Click-to-switch correctness is **unaffected**
(pills are UUID-keyed and `labeling.ordinal_for_uuid` resolves live), so this is a
display-freshness gap, not a correctness bug.

**Read first:**
- `src/spacelabel/platform/notifications.py` — `SpaceObserver` (what it observes)
- `src/spacelabel/agent/app.py` — `_poll_reload` (the existing 1 s mtime-poll
  `NSTimer`), `_refresh`
- `DECISIONS.md` §4 (observation/debounce — the event-driven model), §3.4 (the
  spaces plist lags / flushes only on create/delete)

**Implementation (recommended — periodic live diff, reuses the 1 s poll):**
1. In `_poll_reload`, also read a cheap **live topology signature** — e.g. the
   ordered tuple of `(display_uuid, space.uuid, is_current)` from
   `cgs.enumerate_spaces(include_unlabelable=True)` — and `_refresh()` when it
   changes since the last tick. This catches reorder, create, and delete uniformly,
   because the CGS read reflects the live order immediately (unlike the plist).
2. Guard cost: it adds one CGS read/second (pure IPC, microseconds); only re-render
   on an actual change. If profiling shows any jank, move the read off-main per
   DECISIONS 4.2 (currently deferred).
3. **Rejected alternative:** watching `com.apple.spaces.plist` (WhichSpace's
   approach) reliably catches create/delete but **not** reorder (cfprefsd flushes
   it on create/delete only, DECISIONS 3.4) — so it wouldn't fix the reported case.
4. This nudges the design from purely event-driven (DECISIONS §4) toward a hybrid
   (events + a 1 s liveness poll) — note the change in `DECISIONS.md` §4.
5. **Tests:** unit-test the signature/diff helper (mock `enumerate_spaces`: same
   topology → no refresh; reordered/added/removed → refresh).

---

### G. `spacelabel install --no-run-at-load` (opt-out of auto-start)  *(v0.2)*

**Context:**
`spacelabel install` always writes `RunAtLoad: true` and
`KeepAlive: {SuccessfulExit: false}` into the LaunchAgent plist. There is no way
to install the LaunchAgent without auto-starting at login — the only option is
`spacelabel uninstall`, which removes the plist entirely. A user who wants the
LaunchAgent present but prefers to start the agent manually has no option.

**Read first:**
- `src/spacelabel/install.py` — `build_launch_agent`, `install_agent`, `render_plist`
- `DESIGN.md` §9.2 (LaunchAgent plist spec)
- `src/spacelabel/cli.py` — the `install` command and its existing `--no-load` flag

**Implementation:**
1. Add `run_at_load: bool = True` parameter to `build_launch_agent`. When `False`,
   omit `RunAtLoad` from the plist dict (launchd defaults to `false` when the key
   is absent) and also omit `KeepAlive` (no point auto-restarting a service that
   wasn't meant to start automatically).
2. Thread through `render_plist(home, shim, *, run_at_load=True)` and
   `install_agent(*, load=True, run_at_load=True)`.
3. In `cli.py`, add `--no-run-at-load` flag to the `install` command:
   ```
   spacelabel install [--no-load] [--no-run-at-load]
   ```
   `--no-load` = don't start now (existing); `--no-run-at-load` = don't start at
   future logins. The two are orthogonal and can be combined.
4. Update `docs/CLI.md` — add `--no-run-at-load` to the `install` synopsis.
5. **Tests:** unit test that `build_launch_agent(..., run_at_load=False)` omits both
   `RunAtLoad` and `KeepAlive`; the existing test that the default plist matches the
   packaging template must still pass (default `run_at_load=True`).

---

## After completing each item

1. Run all gates:
   ```sh
   uv run ruff check .
   uv run ruff format --check .
   uv run mypy src
   uv run pytest
   ```
2. Run **codex review** in a loop until no critical findings remain:
   ```sh
   git add <changed files>
   codex review "<focused prompt covering the changed files; flag only: crash risks,
     logic errors, missing system-boundary handling, thread-safety issues; skip style,
     naming, missing features>"
   # fix findings → re-run gates → re-stage → repeat
   ```
   Note: `--uncommitted` conflicts with a positional prompt; stage first, then pass
   the prompt as a positional argument.
3. Commit with a Conventional Commits message (e.g. `feat(overlay): add per-display note body`).
4. Update `DECISIONS.md` if any item forced a new design decision.
5. Mark the item `done` in `todo/README.md`.
