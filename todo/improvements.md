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

**Status + why this is the durable cure for the item-L *stale-grant* class (2026-06-24):**
The signed `.app` cask **shipped** (DECISIONS §6.8), but **ad-hoc** (§6.9), so the
remaining residual is that the cdhash **rotates every release** → an already-enabled
"spacelabel" Accessibility entry goes **stale** on `brew upgrade --cask` (the grant is
keyed to the old cdhash). Item **L** detects that and guides remove-and-re-add, but it
is necessarily **heuristic** — *no unprivileged API exposes the identity the existing
grant is bound to*:
- `AXIsProcessTrusted` / `…WithOptions` returns only a **Boolean** (trusted or not) —
  never "trusted as whom", nor "an entry exists but doesn't match you".
- The authoritative binding lives in **TCC.db** (`access.csreq`; for ad-hoc code the
  requirement pins `cdhash H"…"`), but TCC.db is **SIP-protected** — reading it needs
  the process to hold **Full Disk Access** + parsing a `SecRequirement` (asking for FDA
  to diagnose an *Accessibility* problem is worse than the disease), and a
  `com.apple.private.tcc.*` read entitlement is **Apple-private** (unavailable even with
  notarization). This matches the live finding above: "TCC.db is SIP-locked
  (`authorization denied` even read-only)".

So item L can read only its **own** cdhash (`SecCodeCopySelf`/`kSecCodeInfoUnique`) and
must **remember** it across runs (the `state.json` checkpoint) — there is no live source
for "what cdhash was I last trusted under". Consequence (intrinsic, not a bug): the
**first** item-L release can't retro-detect a *pre*-item-L stale grant — no checkpoint
existed, so it shows the plain "enable" copy and seeds the checkpoint on the first
successful grant; staleness detection then works from the **next** upgrade onward.

**Developer-ID + notarization dissolves the whole class:** a **stable** cdhash across
releases means the grant survives `brew upgrade --cask`, so there is *nothing stale to
detect* — item L's heuristic and `state.json` become unnecessary (they remain a harmless
no-op while the bundle stays ad-hoc). Item L is the best the ad-hoc bundle can do; **E**
removes the need for it.

**Context (surfaced live by click-to-switch, DECISIONS §9.5 / §6 residual risk):**
On the pipx path the agent process is the ad-hoc-signed Homebrew `python3.x`
(`Signature=adhoc`, `flags=0x2`, no TeamIdentifier — confirmed on the reference
machine). macOS TCC attributes and **displays** Accessibility by the code-signed
binary identity, so the Accessibility prompt/list shows **"python3.x"**, not
"spacelabel", and `brew upgrade python` changes the binary cdhash → the grant
**silently drops** until re-granted. `argv[0]`/process-title tricks do **not** change
this (TCC ignores them). The only fix is shipping a real, code-signed `.app` bundle.

**Phase-6 LIVE REPRO (2026-06-22) — Max enabled "python3.14" but click-to-switch stayed disabled:**
Verified empirically (`AXIsProcessTrusted()` called from the exact binaries, fresh processes):
- The agent runs as the framework **app stub** `…/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python` — signing id **`org.python.python`**, **adhoc**, no team, cdhash **`b4955ea0…`**.
- The pipx CLI stub `bin/python3.14` is a **different** Mach-O — signing id **`python3-5555…`**, **adhoc**, cdhash **`f740ae36…`**.
- `AXIsProcessTrusted()` returns **False from a *fresh* process of BOTH** despite "python3.14" showing enabled in Settings. ⇒ the enabled entry's identity matches **neither** binary, and **relaunching the agent will not help** (a fresh process is already untrusted — the grant isn't bound to the agent's identity). The Accessibility list collides multiple "python3.14" entries (every pipx PyObjC tool + stale grants from the old 0.1.0 cdhash), so the user almost certainly toggled a different "python3.14" than the agent's app-stub.
- TCC.db is SIP-locked (`authorization denied` even read-only), so the enabled identity can't be enumerated from the CLI — the `False` result is the proof.

**Interim workaround (fragile, document until the signed bundle lands):** remove ALL "python3.14"/"Python" rows from Settings → Accessibility → quit the agent → click a pill to trigger a **fresh** prompt → enable the newly-added entry → **quit and relaunch the agent** so it starts trusted. Verify with:
`PYTHONPATH=~/.local/pipx/venvs/spacelabel/lib/python3.14/site-packages "$(ps -o comm= -p "$(pgrep -f 'spacelabel agent')")" -c "from spacelabel.platform import switching;print(switching.accessibility_trusted())"` → expect `True`.
This still breaks on `brew upgrade python` (cdhash rotates). The durable fix is the signed `.app` below.

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

### H. CGS→SLS fallback loads the wrong framework bundle  *(Phase-6 finding, 2026-06-22)*

**Context (surfaced by the Phase-6 CGS probe):**
`cgs._load()` (`platform/cgs.py:195`) loads the **CoreGraphics** bundle once and resolves each
symbol by trying its `CGS*` name then its `SLS*` name **against that same CoreGraphics bundle**.
Verified on macOS 26.5.1 (Tahoe), via `objc.loadBundleFunctions`:
- CoreGraphics exports `CGS*` ✅ but **not** `SLS*`.
- SkyLight exports `SLS*` ✅ but **not** `CGS*`.

So the documented per-symbol **CGS→SLS fallback is a no-op** on this OS: the `SLS*` names can
never resolve from CoreGraphics. **No functional impact today** — the `CGS*` names resolve, and the
spaces-plist parser is the real safety net — but it contradicts `DECISIONS.md` §1.1 ("CGS→SLS
getattr/loadBundle fallback per symbol"). If Apple ever drops the `CGS` alias from CoreGraphics, the
fallback as written would fail too (straight to the plist parser).

**Read first:** `platform/cgs.py` `_load`/`_FUNCS`; `DECISIONS.md` §0, §1.1.

**Implementation (pick one):**
1. **Make the fallback real (recommended, cheap):** in `_load`, when the `CGS` name misses against
   CoreGraphics, try the `SLS` name against a **separately-loaded SkyLight bundle**
   (`objc.loadBundle("SkyLight", {}, bundle_identifier="com.apple.SkyLight")`). Cache both bundles.
2. **Or** update `DECISIONS.md` §1.1 to state the SLS fallback is aspirational and the **plist parser
   is the real fallback**, and drop the SLS name from the CoreGraphics-only loop to avoid implying a
   safety net that doesn't exist.
3. **Tests:** unit-test that with the `CGS` name forced absent, the loader resolves the `SLS` name
   from SkyLight (mock the two bundles), and that total failure still raises `CGSUnavailableError`.

Severity: **low** (future-proofing only). Surfaced because Phase 6 is the first run of the bridge on
this OS — exactly the assumption DESIGN §12 item 2 flags.

---

### I. `status` should detect an unmanaged (foreground) agent + report install state  *(Max, 2026-06-22)*

**Context:**
`spacelabel status` only parses `launchctl print gui/$UID/dev.mcsim.spacelabel` (`install.py:223`),
so it reports `not running` whenever the agent is alive as a **foreground** `spacelabel agent`
(dev/debug) rather than via the LaunchAgent — even though the process holds `agent.lock`. Today this
is technically by-design (status tracks the *managed* agent), but it is misleading. Max wants status
to: (a) detect **any** running agent (managed or foreground), and (b) report **install state**
(is the LaunchAgent plist present / loaded?) distinctly from run state.

**Read first:** `install.py` (`status`, the launchctl parse), `agent/app.py:111` (the single-instance
`flock` on `agent.lock`), `DECISIONS.md` §9 (exit-code contract), plan rows E16–E20.

**Implementation:**
1. Add a launchctl-independent **process check**: open `agent.lock` and attempt a non-blocking
   `flock(LOCK_EX|LOCK_NB)` — `BlockingIOError`/`EAGAIN` means an agent holds it → running. (Same lock
   `app.py` uses for single-instance; works for foreground and LaunchAgent alike.)
2. Report three facts: **install** (plist present? `launchctl print` finds the service?), **running**
   (lock held? pid if resolvable), **mode** (managed vs unmanaged/foreground).
3. Extend `--json` with `{installed, loaded, running, pid, managed}`. Define the exit-code contract for
   the new states (e.g. running-but-unmanaged → 0; installed-not-loaded → ?) and record it in
   `DECISIONS.md` §9 + `docs/CLI.md`. Update plan rows E16–E20 expectations to match.
4. **Tests:** mock the lock + launchctl to cover: managed-running, foreground-running, installed-not-
   loaded, not-installed.

Severity: **low–medium** (UX/observability; changes the documented status contract → DECISIONS update).

---

### J. Menu-bar dropdown + Preferences toggle for click-to-switch  *(Max, 2026-06-22)*

**Context:**
Click-to-switch is enable-only via the CLI (`config set menubar.click_to_switch true/false`). The
dropdown menu exposes the four **mode** toggles (Menu-bar / HUD / Overlay / Wallpaper) but **no**
click-to-switch toggle; the Preferences top strip has checkboxes for the modes + buttons-row but
**no** click-to-switch checkbox. Max wants an enable/disable toggle in **both** surfaces.

**Read first:** `agent/app.py` `_rebuild_menu`/`toggleMode_` (`:211`/`:731`), `_sync_click_to_switch_state`
(`:456`), the ⚠️-reason row logic (`:277`); `agent/prefs.py` `_build`/`toggleCheckbox_` (`:436`/`:480`);
`todo/critical-click-to-switch.md` (the shipped feature spec); `DECISIONS.md` §9.5.

**Implementation:**
1. **Dropdown:** add a "Switch to Space on click" toggle item near the mode toggles in `_rebuild_menu`;
   route through `toggleMode_`-style handler to flip `menubar.click_to_switch` via
   `store.set_config_value` + live reload. The existing off→on reset (`_sync_click_to_switch_state`)
   and the ⚠️ Accessibility/shortcut reason row already handle the not-available case.
2. **Preferences:** add a checkbox to the top strip in `prefs.py` writing `menubar.click_to_switch`
   (mirror `toggleCheckbox_`/`write_config`). Preselect from current config.
3. Consider only showing/enabling the toggle when the buttons-row (pills) is on, since click-to-switch
   acts on pills (`app.py:277` gating). Decide and document.
4. **Tests:** the config write happens and the agent reloads; checkbox reflects current value.

Severity: **low** (UX). Cross-refs `critical-click-to-switch.md` (shipped).

---

### K. CLI shows `launcher.py` as prog_name when run from the .app bundle  *(Phase-6 finding, 2026-06-23)*

**Context:** through the cask CLI shim (`spacelabel.app/Contents/Resources/spacelabel` → `launcher.py`), `spacelabel --help` prints `Usage: launcher.py [OPTIONS] …` instead of `Usage: spacelabel …`. The py2app launcher invokes the click group without `prog_name`, so click derives it from `sys.argv[0]` (`launcher.py`). Cosmetic, but it contradicts the CLI contract (plan row **A4** expects `prog_name="spacelabel"`) and leaks into any usage/error text.

**Read first:** `packaging/py2app/launcher.py`; `src/spacelabel/cli.py` (`main()` / the click group, which sets `prog_name="spacelabel"` on the normal entry point).

**Fix:** have the launcher call the group with `prog_name="spacelabel"` (e.g. `cli.main(prog_name="spacelabel")`) — matching the pyproject entry-point behavior — so the bundle CLI and the pipx/dev CLI present identically. Add/extend a launcher test (`tests/test_launcher.py`) asserting the usage line says `spacelabel`.

Severity: **low** (cosmetic / contract consistency).

---

### L. Detect a STALE Accessibility grant before telling the user to "enable" it  *(Max, 2026-06-23 — live finding)*

> **✅ DONE (2026-06-24, branch `fix/stale-accessibility-grant`).** `switching.code_signature_hash`
> reads the process cdhash (`SecCodeCopySelf`→`SecCodeCopySigningInformation`/`kSecCodeInfoUnique`,
> feature-detected from Security; verified live — matches `codesign`'s CDHash). A `{last_cdhash,
> ax_was_trusted}` checkpoint is persisted to a new agent-owned `state.json` on each successful AX
> check; the PURE `switching.is_grant_stale` classifies a later False check as **stale** (cdhash
> rotated since trusted, or ever-trusted) vs **never granted**, and `app._accessibility_reason`
> branches the ⚠️ reason row + the prompt path accordingly (REMOVE-and-re-add vs enable). Tests:
> `is_grant_stale` matrix (`test_switching`), `state.json` round-trip (`test_store`), purge ownership
> (`test_install`), and the delegate reason both-branches (`test_agent_imports`). Durable cure stays
> the item-E Developer-ID/notarization follow-on. DECISIONS §9.5 note + docs/UI.md §2.4 updated.

**Context (verified live):** with the signed cask 0.7.0 bundle (cdhash `4ac198d5…`), an **already-enabled "spacelabel" entry** existed in System Settings → Accessibility, yet `AXIsProcessTrusted()` stayed **False** and click-to-switch never armed; **re-triggering / toggling did not help.** Root cause: **ad-hoc signing rotates the cdhash every build/release**, and macOS TCC keys the grant to the cdhash — so the visible "spacelabel" entry is bound to an **old** cdhash and does not apply to the new process. Toggling the stale entry off/on often re-grants the *old* cdhash; the user must **remove it (−) and let a fresh prompt re-add it** (re-keying to the current cdhash). Today the guidance just says "enable Accessibility for spacelabel" — actively misleading when an enabled-but-stale entry is already present.

**Max's question — can the app detect this before suggesting enablement? YES (heuristically):** the app cannot read TCC.db (SIP), but it can read **its own** code identity and remember it:
1. Read the running bundle's **cdhash** via the Security framework (`SecCodeCopySelf` → `SecCodeCopySigningInformation`, `kSecCodeInfoUnique`) — reading your own signature is allowed; bind it feature-detected like the AX funcs in `switching.py`.
2. Persist `last_cdhash` + an `ax_was_trusted` flag in the store (small state file or config).
3. **Infer staleness:** when `AXIsProcessTrusted()` is False AND (`current_cdhash != last_cdhash` → app was updated, OR `ax_was_trusted` was set) → almost certainly a **stale** grant, not a missing one.

**Then branch the guidance** (in `app.py` click-to-switch availability / the ⚠️ reason row + the first-click prompt; `_sync_click_to_switch_state`):
- **stale** → *"Accessibility for ‘spacelabel’ went stale after an app update (its signature changed). In System Settings → Privacy & Security → Accessibility, REMOVE the existing ‘spacelabel’ entry (–), then click a pill again to re-add it."* + open the Accessibility pane.
- **never granted** → the existing "grant Accessibility" message.

**Read first:** `src/spacelabel/platform/switching.py` (`accessibility_trusted`, the HIServices bind pattern), `src/spacelabel/agent/app.py` (`_sync_click_to_switch_state`, the AX reason rows B22), item **E** (the signed-app/TCC story), `docs/UI.md` Accessibility section.

**Durable fix (the real cure):** **Developer-ID signing + notarization** gives a *stable* cdhash across releases, so the grant survives upgrades and this whole staleness class disappears — tracked as the item-E follow-on. Item L is the best we can do while ad-hoc. Tests: stale-vs-missing branch (mock `AXIsProcessTrusted` + persisted cdhash). Severity: **medium** (UX/correctness of the headline feature).

> **Interim workaround for Max right now:** Settings → Privacy & Security → Accessibility → select the existing **"spacelabel"** row → **−** to remove it → click a menu-bar pill → on the fresh prompt enable the newly-added "spacelabel" → click a pill again. (Re-keys the grant to cdhash `4ac198d5`.)

---

### M. `status --help` (and audit all) leaks raw RST/markdown markup + internal refs  *(Max, 2026-06-23)*

**Context:** `spacelabel status --help` prints the docstring verbatim, including `**selected store**`, `` ``--config`` ``, `*different*`, `` ``agent.lock`` ``, and internal references `(DECISIONS.md §9)`, `(per review F3)` — click renders none of this in plain-text help, so the raw markup/refs show (`src/spacelabel/cli.py` `status` docstring, ~line 382).

**Fix:** rewrite the `status` docstring as clean, concise plain text — no `**bold**`, no `` ``literals`` ``, no `*emphasis*` — and move the `DECISIONS §9` / `review F3` notes into a **code comment**, not user-facing help. **Audit every command docstring** for the same leak.

**Acceptance test (Max asked):** in `tests/test_cli.py`, parametrize over the CLI command tree and assert no command's `--help` contains `**`, `` `` `` (double backtick), or internal-ref substrings (`DECISIONS.md`, `review F`, `§`). Doubles as a regression guard. (The usage line also shows `launcher.py` not `spacelabel` under the cask — that's item **K**; the test should assert `spacelabel` once K lands.)

Severity: **low** (cosmetic / CLI contract A4).

---

### N. Colorize `status` output (dep-free); do NOT colorize help (needs a rejected dep)  *(Max, 2026-06-23)*

**`status` output — yes:** color `running (managed) …` green and `not running …` yellow via `click.style`, TTY-gated + `NO_COLOR`-aware — the existing pattern for `spaces`/`display list` (the bold/green helper near `cli.py:80`). No new dep; fits the CLI contract (DECISIONS §9 / A11–A18: color only on an interactive TTY, stripped when piped/`NO_COLOR`). Test: ANSI present on a faked TTY, absent when piped / `NO_COLOR=1`.

**`--help` colorization — no:** click has no native colored help; the only easy route is `rich-click`/`rich`, a **new dependency** contradicting the locked **stdlib-first / only-`click`** policy (DECISIONS §2; `rumps`/`Pillow` already rejected). Keep help plain unless that decision is deliberately relaxed.

Pairs with **K** + **M** (one `fix(cli): help/output polish` PR). Severity: **low**.

---

### O. Click-to-switch fails on a SECONDARY display (multi-display ordinal ↔ "Desktop N" mismatch)  *(Max, 2026-06-23 — live finding)*

**✅ DONE (2026-06-24, branch `fix/multidisplay-ordinal`).** Pinned empirically on the dual-display rig: the root cause was **not** an ordinal mismatch (a `Ctrl+1..N` probe confirmed macOS's Desktop-N numbering **matches** our CGS enumeration) but a **focus limitation** — macOS only reliably switches the **active/focused display's** Space; cross-display chords are near-silent no-ops. Fixed (option b, refined to *focused* display not "main"): click-to-switch is **gated to the active display** (`switching.is_switchable_target`), and an off-display pill shows a visible "only works on the focused display" HUD notice instead of failing silently — feature stays armed so the same pill works once its display is focused. See DECISIONS §9.5 (items O+V) + `docs/VERIFICATION.md`. *Live-agent retest of the notice on the rig pending.*

**Context (verified live):** with two displays + "Displays have separate Spaces" ON, click-to-switch **works on the portrait/right display but does nothing on the 4K/left display** (after adding a 2nd Space there).

**Diagnosis (Phase-6 investigation):**
- All "Switch to Desktop 1–15" symbolic hotkeys (ids 118–132) are **bound + enabled** — so this is **not** the missing-hotkey / contiguity risk (H6 is fine here).
- `labeling.assign_ordinals` (`labeling.py:72`) numbers Spaces by spacelabel's **CGS `CGSCopyManagedDisplaySpaces` enumeration order**: 4K/left → ordinals **1–2**, portrait/right → **3–14**. Click-to-switch posts `Ctrl+ordinal` (hotkey id `117+ordinal`, `switching.py`).
- **Root cause:** macOS's "Switch to Desktop N" numbering does **not** match spacelabel's CGS enumeration order once Spaces are split across displays. Posting `Ctrl+N` for a left-display Space lands on the wrong desktop / the other display, so the left display's Spaces aren't reachable by their spacelabel ordinal. (It happens to align for the right display, which is why that one works.)
- **It currently fails near-silently** (click does nothing visible) — which also brushes against the "never silently no-op" rule (DECISIONS §9.5).

**Read first:** `labeling.assign_ordinals`/`ordinal_for_uuid`, `switching.parse_desktop_binding` + the post path, `agent/app.py::_on_pill_clicked`, DECISIONS §9.5, plan F2/F3 (multi-display), H6.

**Fix options (resolve the mapping or fail loudly):**
1. Derive the ordinal from macOS's **authoritative** "Desktop N" ordering (e.g., the global `com.apple.spaces.plist` order, or whatever the Ctrl+N hotkeys actually follow) instead of CGS enumeration order — needs an empirical (display,Space)→Desktop-N mapping confirmed on hardware (a manual `Ctrl+N` probe per display).
2. If `Ctrl+N` cannot reliably target a **secondary** display's Space (a real macOS limitation under separate-Spaces), **disable click-to-switch for those Spaces with a visible reason** ("click-to-switch is only reliable on the main display") rather than a silent no-op.
3. (Stretch) a private per-display set-current-space path — SIP/risky, likely out of scope.

**Acceptance:** clicking a pill on **each** display switches to the correct Space; where it can't, it disables with a clear reason (no silent failure). Severity: **medium–high** (headline feature broken on a secondary display + near-silent).

---

### P. Per-display overlay on/off  *(Max, 2026-06-23)*
Let the corner overlay be enabled/disabled **per display** (not just the global `modes.overlay`). Store a per-display flag (extend `displays.json` like the custom-name pattern) + expose in `display` CLI and the Preferences per-display rows; `_update_overlays` (`app.py`) skips displays toggled off. Read first: `agent/overlay.py`, `_update_overlays`, `store.py` displays.json pattern, `agent/prefs.py`. Severity: **low** (UX).

### Q. Hide overlay on displays with only a single / unlabeled Space  *(Max, 2026-06-23)*
When a display's current Space is unlabeled (or it's the single default no-UUID Space), the overlay shows a `Desktop N` placeholder that adds noise. Option to **suppress the overlay** on such displays (config flag, default on/off TBD) — only show where there's a real label. Pairs with P. Read first: `_update_overlays`/`overlay.py` (`title_for` fallback), DECISIONS §6.3. Severity: **low** (UX).

---

### R. Wallpaper mode must not clobber Dynamic / Shuffle wallpapers (static-only, detect + skip/confirm)  *(Max, 2026-06-23 — important safety gap)*

**Context (Max):** macOS supports **Dynamic** wallpapers (time-of-day `.heic`) and **Shuffle** (rotating folder/album). spacelabel's experimental wallpaper mode grabs `NSWorkspace.desktopImageURL` (one image) and sets a **static composite** — so on a Dynamic wallpaper it captures **a single frame**, replaces the dynamic `.heic` (kills the time-of-day behavior), and persists only that frame as the "original" → **the dynamic wallpaper can never be restored**. Shuffle breaks the same way (rotation lost, only one image captured). This is worse than the static-image case and effectively clobbers the user's real wallpaper config irreversibly — counter to the "best-effort, never corrupt the source of truth" stance (DECISIONS §7).

**Required behavior:** wallpaper mode should operate on **static images only**. Detect Dynamic/Shuffle and **skip those displays with a clear notice** (preferred for the non-interactive agent) and/or require explicit per-display confirmation/opt-in. Never silently overwrite a Dynamic/Shuffle desktop.

**Detection (read-only — never edit the WallpaperAgent store, DECISIONS §7):**
- **Dynamic:** the `desktopImageURL` is a `.heic` carrying dynamic-desktop metadata (solar / `h24` keys / multiple embedded representations) — inspect via `CGImageSource` properties.
- **Shuffle:** configured in the WallpaperAgent store (`~/Library/Application Support/com.apple.wallpaper/…` on Tahoe) — read-only detection is fragile/private; a conservative fallback is to only composite when `desktopImageURL` resolves to a single static file and the source isn't a rotating/folder config.
- Conservative rule: **composite only** a plain static image; otherwise skip + log/notify.

**Read first:** `agent/wallpaper.py` (`render_and_set`, `_base_image_path`, `_record_original`), DECISIONS §7, DESIGN §6.4. **Acceptance:** on a Dynamic or Shuffle desktop, wallpaper mode does **not** alter it (skips with a logged/visible reason); on a static image it composites + persists the original as today. Add tests for the type-detection branch.

Severity: **medium** (data-safety in an experimental, off-by-default mode — but it irreversibly degrades a user's Dynamic/Shuffle wallpaper if enabled). **Phase-6: the live wallpaper-composite test (C19–C26 / Part 2 §6) was SKIPPED** on the reference machine precisely because the active wallpaper is Dynamic and the current build would clobber it.

---

### S. Wallpaper mode can't capture the real **per-Space** wallpaper base (`desktopImageURL` is per-screen, stale)  *(Max, 2026-06-23 — foundational finding)*

**Context (verified live):** macOS supports **per-Space wallpapers**. The active portrait display's current Space (3A9B361D) shows a "Dubai Skyline" static photo, but `NSWorkspace.desktopImageURLForScreen_(screen)` returned `/System/Library/CoreServices/DefaultDesktop.heic` (the system default applied to newly-connected displays) — **not** the actual per-Space image. So `wallpaper.py`'s base capture composites onto the **wrong / default** base, and the persisted "original" is wrong too. Together with item **R** (Dynamic/Shuffle), the wallpaper mode's entire premise — *capture the current wallpaper → composite a label → set it* — is **unreliable on real multi-Space / multi-display setups** (the public `desktopImageURL` API predates per-Space wallpapers and doesn't reflect them).

**Detection options (read first `agent/wallpaper.py`, DECISIONS §7 — never *edit* the WallpaperAgent store):**
1. **Read the WallpaperAgent store read-only** to resolve the real per-Space/per-display image + type — Tahoe: `~/Library/Application Support/com.apple.wallpaper/Store/Index.plist`; older: `~/Library/Application Support/Dock/desktoppicture.db`. Private/fragile, and **never write** it.
2. **Capture the rendered desktop pixels** (`CGWindowListCreateImage` of the desktop window layer / `screencapture`) as the base — sidesteps "which file is it" but grabs the rendered frame (loses dynamic; must exclude icons/widgets).
3. **Accept the limitation** — only support the simple single-wallpaper case and skip per-Space setups with a notice.

This is a **foundational redesign of wallpaper mode**, not a quick fix → its own design session (pairs with **R**). Severity: **medium** (experimental/off-by-default, but the feature is largely non-functional on per-Space setups). **Phase-6 outcome: wallpaper mode (C19–C26, Part 2 §6) left UNVERIFIED — deferred to a wallpaper-redesign session covering R + S.**

---

### T. Preferences / color-picker window placement + can't re-surface when hidden  *(Max, 2026-06-23 — §D)*
Two window-management UX gaps in the accessory app:
- **Placement:** the Preferences window (and the NSColorPanel from the Color column) open at the **bottom-left of the left display**. Should open **centered on the active (menu-bar-owning) display** by default (`center()` relative to the active `NSScreen`).
- **Re-surface:** because the agent is an `NSApplicationActivationPolicyAccessory` app (no Dock icon, no Cmd+Tab entry), a Preferences window **hidden behind other windows can't be found again**. Make re-selecting **Preferences…** (and the menu open) **bring the existing window to front** (`makeKeyAndOrderFront:` + `NSApp.activate(ignoringOtherApps:true)`), and consider a transient activation policy while a window is open so it appears in the app switcher.
Read first: `agent/prefs.py` (`show()`/window setup), `agent/app.py` (`openPreferences_`, accessory policy `:178`). Severity: **low–medium** (UX; the "can't find the window" one is genuinely confusing).

### U. Preferences inline-edit bugs — paste shortcut + no live revert on clear  *(Max, 2026-06-23 — §D / D1)*
- **Cmd+V/Cut/Copy don't work** in the Label edit field (right-click → Paste works). The accessory app has **no Edit menu**, so the standard Cmd-C/V/X key equivalents aren't wired to the field editor. Fix: add a minimal **Edit menu** (Undo/Cut/Copy/Paste/Select All with standard selectors+key equivalents) to the app, or install the standard responder-chain edit items so the field editor receives them.
- **Clearing a label doesn't live-revert the outline** to `Desktop N` — the row stays stale until Preferences is reopened (`refresh()` not triggered after an empty commit → `clear_label`). Fix: refresh the outline row after a commit/clear (the `controlTextDidEndEditing_`/`_commit` path, `prefs.py:376`).
Read first: `agent/prefs.py` (`_commit`, the outline editing), `agent/app.py` (menu construction). Severity: **low** (UX).

### V. "Desktop N" numbering mismatch: Preferences vs menu-bar pills  *(Max, 2026-06-23 — §D / D1)*

**✅ DONE (2026-06-24, branch `fix/multidisplay-ordinal`).** Root cause: Preferences numbered **labelable-only** while the pills/switch path numbered over `include_unlabelable=True` (counting each display's default `uuid=""` desktop, which macOS numbers too) → Prefs drifted **−1**. Unified on the single source (`labeling.assign_ordinals` over the full enumeration, per Max's "count every desktop" call): `prefs._load_tree` now builds ordinals over the same full list and filters only the *displayed rows* to labelable Spaces, so "Desktop N" is identical in Preferences, the pill, and the switch path. Tests added. See DECISIONS §9.5 (items O+V).

A Space shown as **"Desktop 3"** in the Preferences outline appears as **"4"** in the menu-bar pill. The two surfaces derive the fallback ordinal from different enumerations (one likely per-display / store-ordered, the other the global live `assign_ordinals`). They must agree. **Same root family as item O** (cross-display ordinal). Read first: `labeling.assign_ordinals`/`title_for`/`pill_text`, `agent/prefs.py` (how it numbers), `agent/menubar.py` (pill number), DECISIONS §6.1. Severity: **low–medium** (confusing inconsistency; also a clue for O).

### W. Menu-bar mode OFF shows an empty status item instead of the `square.dashed` icon  *(Max, 2026-06-23 — §D / B6)*
Turning **Menu-bar title OFF** leaves an **empty quadrant** in the menu bar rather than the documented neutral **`square.dashed` SF Symbol** + "menu-bar label off" accessibility label (plan B6 / `menubar.py:449` `set_inactive`). Investigate why `set_inactive` isn't rendering the symbol (possibly the buttons-row view still occupies the item, or the SF Symbol image isn't applied). Read first: `agent/menubar.py` `set_inactive`, `agent/app.py` menubar-mode toggle + buttons-row interaction. Severity: **low** (cosmetic, but looks broken).

### Y. CLI table coloring ignores `NO_COLOR` on a TTY  *(Max, 2026-06-23 — H18/A18)*
`NO_COLOR=1 spacelabel spaces` still shows the **bold header + green current-row** color on an interactive TTY — `NO_COLOR` is not honored. Plan A18/A12 + the CLI contract (DECISIONS §9) require color suppressed when `NO_COLOR` is set. The table color helper (`cli.py` ~`:80–89`, the bold/green `click.style` path) gates on `isatty` but **not** on `NO_COLOR`. Fix: suppress styling when `os.environ.get("NO_COLOR")` is set (or pass `color=False` to `click.echo`) — the logging sink already does this (`logging_setup.py:36`). Test: `NO_COLOR=1` → no ANSI even on a faked TTY; `*` marker still present. Severity: **low** (cosmetic / contract).

### Z. Overlay + HUD show a STALE Space when the active Space is fullscreen/tiled  *(Max, 2026-06-23 — H2/H3)*
Entering a fullscreen app (its own `type != 0` Space) correctly drops it from `spaces`/pills (H1 ✅), but the **corner overlay and HUD on that display keep showing the previous Space's label** instead of clearing/going neutral. Plan H2/H3 expect: no labelable current Space on that display → menu-bar title falls back to `spacelabel`, that display gets **no** overlay, HUD neutral/none. Either the fullscreen transition doesn't trigger a refresh or the refresh doesn't clear the panel when the active Space has no labelable match. Fix: in `_update_overlays`/`_update_hud` (`agent/app.py`), when the active display's current Space resolves to no labelable Space (fullscreen/tiled), **order-out that display's overlay** and suppress the HUD. Read first: `agent/app.py` `_update_overlays`/`_update_hud`/`read_active_space_uuid`, `platform/notifications.py` (does a fullscreen transition fire `activeSpaceDidChange`?). No crash. Severity: **low–medium** (stale display).

### X. Default unlabelable-Space pill is not a switch target (a click opens the menu)  *(Max, 2026-06-24 — from the O+V review)*
**✅ DONE (2026-06-24, same PR as O+V).** Chose option 2 (make it switchable): the default Space is now a switch target keyed by **`(display_uuid, id64)`** (the pill carries `display_uuid` + `id64`; `_handle_click_at_x` routes a pill with a `uuid` or an `id64` to the switch handler `(uuid, display_uuid, id64)`; `_on_pill_clicked` resolves by `uuid` (labelable) or `(display_uuid, id64)` (default)), still gated to the active display (item O). A pill with no identity (`uuid=""` and `id64==0`) still opens the menu. Also hardened `parse_spaces`: it skips `uuid="" id64==0` placeholder rows and keys `is_current` by `(display, id64)` (not a flat id64 set), so a reused default `id64` can't fabricate a desktop or mark the wrong display current. Recorded the §9.5 update in DECISIONS.md.

With `include_unlabelable=True`, the buttons row renders a display's **default Space** (`uuid=""`) as a numbered `Desktop N` pill, but clicking it **opens the status menu** (it routes like an unlabelable / click-off-pill per DECISIONS §9.5) instead of switching — so the pill looks switchable (it shows a number) yet isn't. Worth revisiting now: §9.5 made unlabelable pills non-switch-targets because they "had no stable key to resolve to a live ordinal," but the multi-display work (O/V) gives the default Space a **live ordinal** (its enumeration position) — so it *could* be switched by ordinal, just not UUID-stable across reorders (the original §9.5 safety rationale). Read first: `agent/menubar.py` `_handle_click_at_x` (empty-uuid → menu), `agent/app.py` `_on_pill_clicked` (requires a uuid; `ordinal_for_uuid` returns `None` for `""`), DECISIONS §9.5 note. **Fix options:** (1) **hide** the default-Space pill from the buttons row (cleanest — not labelable, not a reliable switch target); or (2) **make it switchable by its current ordinal** (best-effort, not reorder-stable, still gated to the active display per O). Severity: **low** (cosmetic/UX; not a dead no-op — it opens the menu).

### AA. Menu-bar / HUD show `spacelabel` (not `Desktop N`) when the active desktop is the default unlabelable Space  *(Max, 2026-06-24 — from the O+V review; related to Z)*
**✅ DONE (2026-06-24, same PR as O+V).** `agent/app.py` `_find_active_space` now resolves the active display's current Space within the full `include_unlabelable` enumeration (the `is_current` Space on the active display), so the focused default desktop is found and titles as `Desktop N` — consistent with the pill — instead of falling back to another display's current Space. When the active display is known but on a fullscreen/tiled Space (filtered out), the title goes **neutral** (a small item-Z-adjacent improvement); only an unresolvable active display falls back to first-current.

When the active display's current Space is the **default unlabelable** one (`uuid=""`, `type 0`), `cgs.read_active_space_uuid()` returns `None` (it only matches **labelable** Spaces), so `_title_for_active` falls back to the `spacelabel` title and the HUD/overlay go neutral — even though the multi-display numbering (item V) now counts that Space as `Desktop 1`. The shown title is then **inconsistent with the pill**, which reads `1`. Read first: `platform/cgs.py` `read_active_space_uuid` (labelable-only `enumerate_spaces()` match), `agent/app.py` `_title_for_active`/`_update_hud`/`_update_overlays`, `labeling.title_for` (`uuid="" `+ ordinal → `Desktop N`). **Related to Z** but a different trigger (the default no-UUID desktop, not a fullscreen/tiled Space). **Fix:** resolve the active Space against the `include_unlabelable=True` enumeration so the default desktop is found with its ordinal, then render `Desktop N` in the title/HUD/overlay — consistent with the pill; mind the ripple into HUD/overlay/wallpaper (all key off the active Space) and the §9.5/Z "no labelable Space → neutral" cases. Severity: **low–medium** (visible title-vs-pill inconsistency on the default desktop).

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
