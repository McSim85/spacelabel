# spacelabel — Phase 6 Verification Results

User-acceptance verification of the shipped build, per `spacelabel-plan/phase-6-verification.md`
(mirrors DESIGN.md §12 probe checklist; gate = DECISIONS.md §1 uuid reboot-stability).

## Environment

| | |
|---|---|
| Date | 2026-06-22 |
| macOS | 26.5.1 "Tahoe" (build 25F80) |
| Hardware | Apple M3 Pro, arm64, SIP enabled |
| Displays | LG UltraFine 4K **3840×2160** (horizontal) + LG UltraFine **2160×3840** (portrait, rotation 270°); separate Spaces per display |
| Python (pipx venv) | 3.14.4 |
| spacelabel | **0.6.1** (installed via `pipx install git+https://github.com/McSim85/spacelabel`, built from `main` @ 39a8d2c) |
| Verification branch | `docs/phase-6-verification` (off `main` @ 39a8d2c = v0.6.1 + pipx-only backlog #30) |

### Notes / drift from the plan text
- Plan says repo is at **v0.6.0**; repo is actually **v0.6.1** with an open release-please PR for **0.6.2**. Version-string checks (1A.1, A4) are scored as "matches installed metadata", not the literal `0.6.0`.
- The overview's branch-reconciliation warning (`feat/live-refresh-reorder`) is **stale**: that branch no longer exists (its feature shipped in v0.6.0 #25), and the `docs/backlog-pipx-only` work already merged to main (#30). No reconciliation was needed.
- **Resumed 2026-06-23, post-PR #32** (`feat(dist): signed .app via Homebrew cask + Phase-6 follow-ups`, merged to `main` @ `df12dde`): distribution pivoted to the **signed `.app` via Homebrew cask** (DECISIONS §6.8); the CGS→SLS (item H) and `status` (item I) fixes landed. Verification continues on branch **`docs/phase-6-reverify-brew`** (off `df12dde`). The env table above reflects the **pipx** snapshot at the original 2026-06-22 run; install acceptance is now the brew matrix in Part 1. pipx is being removed entirely (`todo/remove-pipx.md`).

Legend: ✅ pass · ⚠️ pass-with-note · ❌ fail · 🟡 N/A (by design / feature not shipped) · ⏳ deferred (hardware/UI, pending Max)

---

## Verdict & summary (2026-06-23)

**PASS — no blocking defects.** Every make-or-break behavior is verified on the reference Mac (macOS 26.5.1, M3 Pro, dual display). The distribution pivot to a signed `.app` via Homebrew cask shipped (PR #32) and is verified end-to-end. All gaps found are **non-blocking backlog**; the experimental wallpaper mode is deferred for a redesign; three checks are deferred to a natural restart/hardware window.

**Verified ✅**
- **Install/distribution:** `brew install --cask` of the signed 0.7.0 `.app` (identity `dev.mcsim.spacelabel`, cdhash matches the CI release asset); agent runs **as the bundle**; CLI on PATH; uninstall keeps data.
- **CGS gate (DESIGN §12):** symbols resolve, PyObjC↔CFArray bridge round-trips, flat RSS over 4000 reads, forced-nil → plist fallback identical; **every live uuid is persisted in the on-disk plist** → uuid reboot-stability at **~high confidence** (literal reboot still pending — see below).
- **Core invariant:** labels follow a Space's **UUID through a Mission Control reorder** (vs WhichSpace).
- **Click-to-switch** on the **primary** display (incl. high-ordinal pills past Desktop 3; revoke-AX → reason popup, no silent no-op) — fixed by the named-bundle TCC identity.
- **CLI matrix** Part 3 §A: 68/69 + 7/7 real-store + 28/28 second pass (the 1 "fail" was a stale plan expectation, not a defect); real store restored byte-identical.
- **UI:** §B menu-bar (pills/dropdown/rename), §C HUD (incl. rotated 2160×3840) + corner overlay + notes, §D Preferences (layout/color/toggles/popups), richer `status`.

**Backlog opened this phase (all non-blocking; in `todo/improvements.md` unless noted):**
- **Most significant — functional:** **O** click-to-switch fails on a **secondary** display (ordinal↔Desktop-N mismatch; near-silent) · **V** "Desktop N" numbering mismatch Prefs-vs-pill (same family as O) · **Z** overlay/HUD stale on a fullscreen Space.
- **Accessibility/TCC:** **L** detect a *stale* grant (ad-hoc cdhash rotates per release) before telling the user to "enable"; durable cure = Developer-ID notarization (item E). — **✅ FIXED (2026-06-24, `fix/stale-accessibility-grant`):** the agent reads its own cdhash via the Security framework (`code_signature_hash`; verified live on 26.5.1 — `b4955ea0…`, matching `codesign`'s CDHash), persists a `{last_cdhash, ax_was_trusted}` checkpoint to a new `state.json` on each successful AX check, and `is_grant_stale` + `_accessibility_reason` branch the ⚠️ row to **REMOVE-and-re-add** for a stale grant vs **enable** for a never-granted one. Classifier + cdhash read verified live; the literal cross-upgrade staleness trigger is observable only over a real cask upgrade (a natural-upgrade-window confirm, like the reboot gate). Durable cure stays item E.
- **Wallpaper (redesign, experimental/off-by-default):** **R** Dynamic/Shuffle guard (static-only) · **S** can't capture the per-Space base (`desktopImageURL` returns the default). Left UNVERIFIED by design.
- **UX/polish:** **T** Prefs/color-picker window placement + re-surface · **U** Prefs inline-edit (Cmd+V dead, no live-revert on clear) · **W** menu-bar OFF shows empty quadrant not `square.dashed` · **P/Q** per-display overlay on/off + suppress on unlabeled · **K** CLI prog_name `launcher.py` · **M** `status --help` markup leak (+test) · **N** colorize `status` output · **Y** `NO_COLOR` not honored by table color.
- **Already-tracked tasks:** `todo/remove-pipx.md` (strip the deprecated pipx path), `todo/uninstall-purge.md` (the `--purge` feature + its 1B.5–1B.9 acceptance rows).

**Deferred (need a restart / hardware window) — the only unrun checks:**
- **Reboot §5** — the uuid gate's *literal* final confirm (proxy already ~high). Capture snippet in this doc.
- **H16** — "Displays have separate Spaces" OFF (`"Main"` sentinel / F3) — requires a logout.
- **Part 2 §7** — detach-4K / re-orient generality (add/remove-Space already exercised).

**Follow-ups:** prioritize **O/V/Z** (functional) for the next fix session; refresh `CLAUDE.md` with verified gotchas (Phase-7). `DECISIONS.md` needed no new decision beyond #32's pivot (§6.8) + SLS-fallback fix (§1.1); §1 gate note updated below.

---

## Part 1 — Install & distribution acceptance

> **Distribution is now the signed `.app` via a Homebrew cask** (PR #32, DECISIONS §6.8 — reverses pipx-only #30). The **authoritative install-acceptance matrix is [Part 1 (brew) below](#part-1--install-acceptance-signed-app-via-homebrew-cask-current-path).** The pipx matrix that follows (1A/1B/1C) is **SUPERSEDED — kept only as a historical record** of the pre-pivot build's behavior. pipx is being removed from the repo entirely (`todo/remove-pipx.md`); **do not test pipx going forward.**

### 1A. pipx install matrix  *(SUPERSEDED — historical; pipx being removed)*

| # | Result | Evidence |
|---|---|---|
| 1A.1 | ✅ | Fresh `pipx install git+https://github.com/McSim85/spacelabel` succeeded with **no credentials** (public repo confirmed), built in ~16s, isolated venv, **5 deps resolved** (`click` 8.4.1, `pyobjc-core` 12.2.1, `pyobjc-framework-{Cocoa,CoreText,Quartz}` 12.2.1), shim linked at `~/.local/bin/spacelabel`. `--version` → `spacelabel, version 0.6.1` (metadata-derived; plan's literal "0.6.0" is stale). |
| 1A.2 | ✅ | `which spacelabel` → `/Users/mc-sim/.local/bin/spacelabel` — canonical pipx shim (the path `install_agent()` expects). |
| 1A.3 | ✅ | Isolated `uv venv` + `uv pip install -e '.[dev]'` succeeded; dev extras resolve (`ruff` 0.15.18, `mypy`, `pre-commit`, `pytest`); `--version` → 0.6.1 from the dev venv. Editable = dev-only, not the LaunchAgent target. (`pipx install .` lands the same shim — same mechanism as 1A.1 with a local path; not re-run to avoid churning the live shim.) |
| 1A.4 | ✅ | `git+...@v0.6.1` (isolated venv) resolved to the exact tag commit `bbcaf64`, version 0.6.1, same shim outcome. |
| 1A.5 | 🟡 | `pypi.org/pypi/spacelabel/json` → **HTTP 404**: package genuinely unpublished. N/A by design (PyPI deferred until stable; re-enable once OIDC trusted-publishing ships). Not a defect. |
| 1A.6 | ✅ | `pipx list` shows `spacelabel 0.6.1`; `spacelabel --help` renders the full 11-command tree (agent, completion, config, display, install, label, mode, note, spaces, status, uninstall) **instantly — no PyObjC import cost on `--help`**. Exit 0. |
| 1A.7 (extra — update path) | ⚠️ | `pipx upgrade spacelabel` → *"already at latest version 0.6.1"* (no-op). **Gotcha:** for `git+…` installs, `pipx upgrade` compares the package's static version metadata, which only bumps at release time, so it **won't pull new `main` commits** between releases. The real "get newest main" path for git installs is **`pipx install --force git+…`** or **`pipx reinstall spacelabel`**. Once versioned PyPI publishing ships, `pipx upgrade` works normally. Not a defect — documented for the README/install docs. |

### 1B. Footprint & clean uninstall

**1B-today (v0.6.0/0.6.1 as shipped):**

| # | Result | Evidence |
|---|---|---|
| 1B.1 | ✅ | `spacelabel uninstall` → `Removed dev.mcsim.spacelabel (labels and config kept).` exit 0. (No LaunchAgent plist was installed, so the bootout/unlink were no-ops; the full plist-removal path is exercised in Part 3E E7→E13.) |
| 1B.2 | ✅ | After uninstall, `~/Library/Application Support/spacelabel/` still has `labels.json`, `config.json`, `displays.json` (+ `.lock` siblings + `agent.lock`) — user data untouched. |
| 1B.3 | ✅ | `spacelabel uninstall --keep-labels` → identical output, exit 0. `--help` documents it: *"Reserved for a future destructive variant; labels are always kept today."* (documented no-op). |
| 1B.4 | ✅ | `launchctl print gui/$UID/dev.mcsim.spacelabel` → service not found. |

**1B-after (`uninstall --purge`) — 🟡 feature not shipped:** `spacelabel uninstall --purge` → `Error: No such option '--purge'.` exit **2** (both `--dry-run` and `--yes` forms). Rows 1B.5–1B.9 are **deferred-pending-feature** and have been wired into `todo/uninstall-purge.md` as that feature's acceptance matrix (definition-of-done) — re-run and mark ✅ here once `feat(cli): add uninstall --purge` lands.

**1B advisory (auto-cleanup reality):** accepted as documented design — neither `pipx uninstall` nor `brew uninstall` can auto-run the LaunchAgent cleanup (no uninstall hooks in pip/wheel/pipx; no `pre/post_uninstall` in brew formulae). Mitigations documented: run `spacelabel uninstall` *before* `pipx uninstall`; runtime breadcrumb (lands with `--purge`); cask `zap` if a cask is ever added. No code action this run.

### 1C. Homebrew — 🟡 deferred (out of scope)
No `Formula/` in the repo; brew not tested this run, matching the pipx-only decision (`todo/critical-release-automation.md`). N/A.

**Part 1 (pipx, legacy) verdict: PASS (historical).** All scriptable pipx rows passed pre-pivot; PyPI-by-name and `--purge`/Homebrew were N/A then. Retained only as a record — **superseded by the brew matrix below.**

### Part 1 — Install acceptance: signed `.app` via Homebrew cask (current path)

Distribution = signed `.app` via Homebrew cask (PR #32, DECISIONS §6.8). Build tool `tools/build_app.sh`; cask `Casks/spacelabel.rb`; agent-path resolution `install.py` `_resolve_install_shim`/`_enclosing_app_exe`. **Status: ⏳ to be run live** — building/installing the `.app` replaces the running agent, so it needs Max's go-ahead.

Verified 2026-06-23 against the **CI-signed v0.7.0 release artifact** `spacelabel-0.7.0.zip` (downloaded from the GitHub release; ~18 MB; this is exactly what the cask installs):

| # | Test | Expected | Result |
|---|---|---|---|
| P1.1 build | release pipeline `build-app` (py2app) | Produces a self-contained `spacelabel.app` (embedded `Python.framework`); py2app build-time only. | ✅ CI `build-app` job green; 18 MB bundle attached to v0.7.0. |
| P1.2 sign | `codesign -dvvv` + `codesign --verify --deep --strict` | `Identifier=dev.mcsim.spacelabel`, `Signature=adhoc`; inside-out; verify passes. | ✅ `Identifier=dev.mcsim.spacelabel`, adhoc, cdhash `4ac198d5…`; verify → *valid on disk; satisfies its Designated Requirement* (validated the embedded Python.framework). |
| P1.3 self-contained | `…/Contents/MacOS/spacelabel --version` (no build venv) | Runs from the embedded interpreter. | ✅ → `spacelabel, version 0.7.0`. |
| P1.4 CLI on PATH | `…/Contents/Resources/spacelabel --help` (cask `binary` shim) | Full command tree, no PyObjC cost; shim execs the stub by absolute path. | ⚠️ Full tree renders (incl. the new richer `status` help). **Minor:** usage line shows `launcher.py`, not `spacelabel` (prog_name not set on the shim path) — A4 expects `prog_name="spacelabel"`. See finding below. |
| P1.5 icon/accessory | `spacelabel.icns` present; `CFBundleIconFile`; `LSUIElement=true` | Named icon; accessory. | ✅ `CFBundleIdentifier=dev.mcsim.spacelabel`, `CFBundleName=spacelabel`, `LSUIElement=true`, `CFBundleIconFile=spacelabel.icns` (present). |
| P1.6 install→bundle | install the app, then `spacelabel install` | LaunchAgent `ProgramArguments` = the **bundle exe**; `_resolve_install_shim` resolves the bundle. | ✅ `spacelabel install` → "Installed and loaded dev.mcsim.spacelabel."; plist `ProgramArguments` = **`/Applications/spacelabel.app/Contents/MacOS/spacelabel agent`** (bundle exe, not the pipx shim). Cask installs to **`/Applications`** (not `~/Applications`). |
| **P1.7 named TCC identity** | running agent's code identity | Agent process **is** the bundle → Accessibility shows **"spacelabel"** (not `python3.x`); grant → `AXIsProcessTrusted()` True → pills switch. | ✅ **VERIFIED LIVE (Max, 2026-06-23).** Agent runs as the bundle (cdhash `4ac198d5…`); Accessibility entry is the named **"spacelabel"** (python3.x collision gone). Once a **stale** prior-cdhash entry was removed + re-added (see below / item L), `AXIsProcessTrusted()` → True and **clicking a non-current pill switches the Space** end-to-end (B18 path). The bundle pivot achieved its goal. Caveat stands: ad-hoc cdhash rotates per build → grant must be re-approved after each upgrade until Developer-ID/notarization (item E follow-on). The "had to remove a stale entry" friction → backlog item **L**. |
| P1.8 footprint/uninstall | `spacelabel uninstall`; cask `zap` | uninstall keeps user data; cask `zap trash:` matches `--purge` paths. | ⏳ pending (after the click-to-switch test). |
| P1.9 remote cask | `brew install --cask <tap>/spacelabel` | Installs v0.7.0 from the signed release asset. | ✅ PR #33 merged (cask `0.7.0`, real sha256). `brew tap mcsim85/spacelabel <repo>` + `brew install --cask mcsim85/spacelabel/spacelabel` → 0.7.0; app→`/Applications`, CLI→`/opt/homebrew/bin/spacelabel`; caveats correctly name **"spacelabel"** for Accessibility. Installed app cdhash `4ac198d5…` == the verified release asset. |

**Finding (minor, P1.4 — confirmed on installed cask):** through the cask CLI shim, `--help`/usage prints `launcher.py` instead of `spacelabel` (the bundle's `Contents/Resources/launcher.py` invokes the click group without `prog_name="spacelabel"`). Cosmetic; affects the usage line + any `prog_name`-derived text (A4). → logged as `todo/improvements.md` item **K**.

**Finding (minor — brew quarantine):** `brew install --cask` set `com.apple.quarantine` on `/Applications/spacelabel.app` (ad-hoc, no notarization). The LaunchAgent/first-launch needs it cleared — the cask **caveat documents this exactly** (`xattr -dr com.apple.quarantine …` / right-click→Open). I cleared it and the agent launched. Working-as-documented; the durable fix is Developer-ID notarization (deferred follow-on).

**Item I (richer `status`) — ✅ verified:** with the cask agent loaded, `spacelabel status` → `running (managed)  pid=11916  label=dev.mcsim.spacelabel` (exit 0); `--json` → `{"installed":true,"loaded":true,"running":true,"pid":11916,"managed":true,"label":"dev.mcsim.spacelabel"}`. (The foreground/unmanaged branch — `running (unmanaged)` — still to be spot-checked.)

---

## Part 2 — Technical probe (hardware-gated) — IN PROGRESS

Agent running as **foreground** `spacelabel agent --debug` (PID 50803, started by Max; holds `agent.lock` → single-instance flock confirmed). `spacelabel status` reports "not running" because it tracks only the *LaunchAgent* — see backlog item I (`todo/improvements.md`).

### Step 1 — CGS read smoke-test (the project gate) — ✅ PASS

Read-only probe (`scratchpad/cgs_probe.py`) exercising the **shipped** `spacelabel.platform.cgs`/`spaces_plist` against DESIGN §12 priority items. All hard checks passed:

| DESIGN §12 | Check | Result |
|---|---|---|
| §12.2 | All four CGS symbols resolve from CoreGraphics (`CGSMainConnectionID`, `CGSCopyManagedDisplaySpaces`, `CGSManagedDisplayGetCurrentSpace`, `CGSCopyActiveMenuBarDisplayIdentifier`); connection id non-zero | ✅ conn=2716715 |
| §12.4 | PyObjC↔CFArray bridge round-trips: `CGSCopyManagedDisplaySpaces` → native `list[dict]` (2 displays); `enumerate_spaces` → Space objects (**15 total, 14 labelable**, 1 unlabelable default Space) | ✅ |
| (step 1) | `read_active_space_uuid()` → a current UUID present in the live set (`3A9B361D-…`); active display = `874A623F-…` (the portrait LG UltraFine — matches the reference rig) | ✅ |
| §12.5 | Dict keys: `id64` present on all 15, `ManagedSpaceID` present on all 15, 15 user (`type==0`), 0 `TileLayoutManager`, 0 special. (Fullscreen/tiled `type!=0` not present now — exercised in Part 4 H1–H3 by Max.) | ✅ |
| **§12.1 GATE** | **uuid reboot-stability proxy:** every one of the 14 live CGS labelable uuids is present in the **on-disk** `~/Library/Preferences/com.apple.spaces.plist` (21 persisted uuids; independent `plistlib` read). `defaults read com.apple.spaces` corroborates. → live ⊆ persisted | ✅ **subset=True, none missing** |
| (step 1) | **Forced CGS-nil → plist fallback:** monkeypatching `CGSCopyManagedDisplaySpaces`→None raises `CGSUnavailableError` (not a false "0 Spaces"); the plist fallback then yields the **identical** 14 labelable uuids, all `is_current=False` (plist lags live) | ✅ exact set match |
| §12.6 | Active-display fallback: forcing `CGSCopyActiveMenuBarDisplayIdentifier`→nil → `NSScreen.mainScreen()` yields a real display UUID (`899EDEF9-…`, the 4K) | ✅ |
| §12.3 | Memory/ownership: 4000 tight `enumerate_spaces` reads, RSS did **not** grow like a per-read CF leak (net −42 MB; no crash) → `already_retained` annotation correct (no leak, no over-release) | ✅ |

**Finding (logged, minor):** the per-symbol **CGS→SLS fallback is a no-op on Tahoe 26.5.1**. `cgs._load()` resolves `SLS*` names against the *CoreGraphics* bundle, but verified: CoreGraphics exports `CGS*` only, SkyLight exports `SLS*` only. No functional impact (CGS resolves; plist parser is the real safety net) but it contradicts DECISIONS §1.1. → `todo/improvements.md` item **H**; flag DECISIONS §1.1 for an accuracy update.

**§12.9 "Main" sentinel:** not exercised — "Displays have separate Spaces" is **ON**, so both display identifiers are real UUIDs (`899EDEF9-…`, `874A623F-…`). The `"Main"` remap is unit-tested; live coverage needs the setting toggled OFF (Part 3 F3 / Part 4 H16) — pending Max.

**Gate status:** uuid reboot-stability is at **~high confidence** via the read-only proxy (every live uuid is persisted in the login-reload source). The **literal reboot** (DESIGN §12 item 1 final gate) is **deferred** to Max's next natural restart — capture snippet below.

### Finding — click-to-switch Accessibility on pipx (relates to B22, H4–H6) — ✅ **RESOLVED & VERIFIED LIVE** (signed `.app` via cask)
> **Resolved (2026-06-22):** the signed `.app` so TCC keys on `dev.mcsim.spacelabel` is built, installed via the Homebrew cask, and **verified end-to-end on the reference machine**: the Accessibility entry shows a **named "spacelabel"** (no python collision); granting it makes the agent trusted; **clicking a menu-bar pill switches the Space** (Max, after re-arming `menubar.click_to_switch` off→on). Caveat confirmed live too: the **ad-hoc cdhash changes on each rebuild**, so the grant must be re-approved after an upgrade/rebuild (Developer-ID would make it durable — deferred, §6.9). See the **Phase-6 blockers session → Tier 1** section below.
Max enabled "python3.14" in Settings → Accessibility but click-to-switch stayed disabled. **Root cause confirmed empirically:** the agent runs as the ad-hoc-signed framework app-stub `Python.app/Contents/MacOS/Python` (id `org.python.python`, cdhash `b4955ea0…`); `AXIsProcessTrusted()` returns **False from a fresh process of that exact binary**, so the enabled "python3.14" entry isn't bound to the agent's identity, and **relaunch won't fix it**. The pipx CLI stub `bin/python3.14` is a *different* binary (id `python3-5555…`, cdhash `f740…`), so multiple "python3.14" identities collide in the Accessibility list. This is exactly the TCC-identity risk in `todo/improvements.md` item **E** (signed `.app`) — now reproduced live; item E updated with the cdhashes, the "relaunch won't help" proof, and an interim workaround. Not a code defect in spacelabel's switching logic (it correctly disables with a visible reason rather than silently no-op'ing, per DECISIONS §9.5). **Deferred to item E** (signed bundle is the durable fix). Workaround to attempt this session is in item E.

### Step 2 — WhichSpace reorder demo (the core invariant) — ✅ PASS (Max, 2026-06-23)
Moving/reordering Spaces in Mission Control keeps each label bound to its **Space UUID**, not its position — labels follow their desktops through a reorder (the `Desktop N` ordinal shifts, the label does not migrate to a different Space). This is the project's reason-for-being vs WhichSpace (position-keyed). Confirmed live by Max. Covers plan row **A1** + Part-2 step 2.

### Steps 3–7 — ⏳ pending Max (UI / hardware)
Still need your hands/eyes: (3) display modes (menu-bar / HUD / overlay) on the rotated 2160×3840 + 4K, (4) menu-bar/prefs rename live-reload, (5) reboot persistence [deferred to a natural restart — snippet below], (6) experimental wallpaper revert, (7) generality spot-check (detach 4K / change res-orientation / add-remove Space).

#### Reboot-capture snippet (run once, around your next natural restart — no live session needed)
```sh
# BEFORE reboot: label the current Space and record its uuid
spacelabel label set current "REBOOT-TEST"
spacelabel spaces --json > ~/spacelabel-prereboot.json
/usr/libexec/PlistBuddy -c 'Print' ~/Library/Preferences/com.apple.spaces.plist >/dev/null 2>&1; \
  python3 -c "import plistlib,pathlib;print('prereboot current uuid recorded')"
# ... reboot ...
# AFTER reboot: confirm the SAME uuid returned and the label re-bound
spacelabel spaces --json > ~/spacelabel-postreboot.json
spacelabel label list | grep REBOOT-TEST   # label survived, keyed to the same uuid
diff <(python3 -c "import json;print('\n'.join(sorted(s['uuid'] for s in json.load(open('$HOME/spacelabel-prereboot.json')) if s['uuid'])))") \
     <(python3 -c "import json;print('\n'.join(sorted(s['uuid'] for s in json.load(open('$HOME/spacelabel-postreboot.json')) if s['uuid'])))")
# Empty diff = uuids identical across reboot = GATE CONFIRMED. Then: spacelabel label clear <that uuid>
```

---

## Phase-6 blockers session (2026-06-22, branch `feat/signed-app-cask`)

Cleared `todo/phase-6-blockers.md`. Distribution pivot: **signed `spacelabel.app` via a Homebrew cask replaces pipx** (DECISIONS §6.8/§6.9, reverses #30). Everything below was verified **headless on the reference machine**; the only outstanding items are the **on-hardware Accessibility grant + click-to-switch** (Max).

### Tier 2a — CGS→SLS fallback now resolves from SkyLight — ✅ PASS (live)
The old loader tried the `SLS*` name against the **CoreGraphics** bundle (which exports only `CGS*`), so the per-symbol fallback was a no-op. Fixed (`cgs._load` now loads a separate SkyLight bundle, cached). **Live proof:** forcing the `CGS*` names absent, all four symbols resolved via their `SLS*` names from SkyLight and `CGSMainConnectionID()` returned a real non-zero connection (`1357667`). Unit tests cover CGS-miss→SLS, CGS-present→SkyLight-not-loaded, total-miss→`CGSUnavailableError`. → resolves the Step-1 "Finding (logged, minor)" above; DECISIONS §1.1 updated.

### Tier 2b — richer `status` (install + run state, incl. a foreground agent) — ✅ PASS (live)
`status` now reports `{installed, loaded, running, pid, managed}` and detects a foreground `spacelabel agent` via a non-blocking `flock` on `agent.lock` (the agent records its pid there). **Live proof:** a foreground `spacelabel agent --debug` (pid 4723) →
`status --json` = `{"installed":false,"loaded":false,"running":true,"pid":4723,"managed":false,…}` exit 0 (`running (foreground)`); after kill → `not running (not installed)` exit 3. Exit-code contract unchanged (0 running / 3 not). → **flips the Part-2 note** "status reports not running because it tracks only the LaunchAgent" (line ~66): a foreground agent is now correctly reported. Rows **E16–E20** expectations updated to the `{installed,loaded,running,pid,managed}` model (DECISIONS §9.1).

### Tier 3 — `uninstall --purge` — ✅ PASS (live, isolated)
`apt remove` (default, keeps data + breadcrumb) vs `apt purge` (`--purge` deletes only spacelabel-owned paths). **Live proofs:**
- **1B.6** `uninstall --purge --dry-run` → printed the resolved paths to **stdout** (`~/Library/Application Support|Caches|Logs/spacelabel`), deleted nothing, exit 0; real data dir intact. ✅
- **1B.7** non-TTY `--purge` without `--yes` → refuses, exit 2 (unit-verified via the `_isatty` seam). ✅
- **1B.8/1B.9** real `uninstall --purge --yes` run under a **throwaway `$HOME`**: all three fake spacelabel dirs deleted, exit 0, and **the real `~/Library/.../spacelabel` was untouched** (proving the "named targets only" guarantee). ✅ The `.zshrc` fpath line is left for manual removal (printed). Default `uninstall` now appends the breadcrumb (**1B.5**). ✅
- `--keep-labels` is a hidden, deprecated no-op (stderr deprecation). The cask `zap trash:` lists the same four paths (kept in sync). DECISIONS §9.3.

### Tier 1 — signed `.app` via Homebrew cask — ✅ COMPLETE (built, installed, granted, click-to-switch verified live)
**Built + signed (`tools/build_app.sh --sign`):**
- py2app builds a **self-contained** `spacelabel.app` (embeds `Python.framework` + PyObjC + click); `Info.plist` = `CFBundleName/Executable=spacelabel`, `CFBundleIdentifier=dev.mcsim.spacelabel`, `LSUIElement=true`. ✅
- `codesign -dvvv` → `Identifier=dev.mcsim.spacelabel`, `Signature=adhoc` (inside-out via `tools/codesign_app.sh`; `--verify --deep --strict` passes). ✅
- icon: committed master `packaging/icon/spacelabel-1024.png` → `.icns` embedded (`CFBundleIconFile`). ✅

**Installed live on the reference machine (2026-06-22):**
- `brew install --cask` (local self-tap, `--appdir=~/Applications`) → **moved** `spacelabel.app` to `~/Applications` (a **real dir**, stable across `brew upgrade` — NOT a Caskroom-versioned symlink) + symlinked the CLI to `/opt/homebrew/bin/spacelabel`. ✅
- **CLI on PATH works**: `spacelabel --version` → `0.6.1`, `spaces --json` reads **live CGS**. ✅
- **`spacelabel install` points the LaunchAgent at the stable bundle exe** `~/Applications/spacelabel.app/Contents/MacOS/spacelabel` (verified: no Caskroom path) and loads it. ✅
- **The agent runs AS THE BUNDLE**: `launchctl` → `state=running`, `program=~/Applications/spacelabel.app/Contents/MacOS/spacelabel`; `spacelabel status` → `running (managed) pid=…`. The process **is** `dev.mcsim.spacelabel` — the whole point of the pivot. ✅

**Live-discovered fixes (all landed + codex-clean):**
- **CLI-via-symlink** broke (py2app stub computes `@executable_path` from the symlink dir): added a symlink-resolving shim at `Contents/Resources/spacelabel`, pointed the cask `binary` at it, + a build self-test. ✅
- `build_app.sh` `--clear` (re-runnable); cask `uninstall trash:` not `delete:` (avoids a `sudo` prompt on a user-owned plist); `_enclosing_app_exe` keeps scanning past an inner `Python.app` helper + `abspath`-normalizes (stable path).
- **Agent logging was silently lost under launchd**: the `RotatingFileHandler` had no `encoding`, so with no locale (LANG unset) it defaulted to ASCII and the agent's non-ASCII WARNING lines (curly quotes / “→”) raised on write — `agent.log` stayed empty and tracebacks spilled to `agent.boot.log`. Fixed: `encoding="utf-8"` (+ a unit test). Surfaced live on the cask install (the old foreground dev runs inherited the shell's UTF-8 locale, hiding it). ✅
- **The click-to-switch dropdown message** said "(on a pipx install it appears under python3.x…)" — corrected to name **"spacelabel"** (it's the cask now). ✅
- `Casks/spacelabel.rb` passes `brew style`; `publish.yml` gained `build-app` + `update-cask` (untested until the first real release — no push this session).

**✅ Hardware verification (2026-06-22, Max — Phase-6 rows B18–B26 + H4–H6):**
1. *System Settings → Privacy & Security → Accessibility* showed a **named "spacelabel"** entry (not python3.x). The pre-rebuild grant was stale (ad-hoc cdhash changed on rebuild — the documented caveat), so the row was removed and **re-granted** for the current build. ✅
2. With the "Switch to Desktop N" shortcuts enabled and `menubar.click_to_switch` re-armed (off→on), **clicking a menu-bar pill switches the Space** — confirming `AXIsProcessTrusted()` is True for the granted bundle and the bound chord posts. ✅
**→ Tier 1 verified end-to-end: a Homebrew-cask-installed signed `.app`, running as `dev.mcsim.spacelabel`, gets a durable named Accessibility grant and click-to-switch works.** The only residual is the ad-hoc re-grant-on-upgrade caveat (§6.9; Developer-ID is the deferred durable fix). Part-1 install rows are covered above (cask install, CLI on PATH, agent runs as the bundle).

### Post-PR codex follow-up (PR #32, 2026-06-22) — ✅ all addressed, gates + codex clean
A codex pass over the open PR raised five items; all fixed, full gate suite green (ruff/format/mypy/`347 passed`), each commit pre-commit-clean:
- **[P2] single-instance lock truncated the winner's PID** — `agent/app.py` opened `agent.lock` with mode `"w"`, truncating before `flock`; a losing second instance wiped the running agent's recorded pid. Now `"a+"` + only the winner `truncate(0)`+writes its pid, so `status`/`_probe_agent_lock` can always read a live pid.
- **[P2] plain build shipped an invalid signature** — writing the CLI shim invalidates py2app's seal, so `build_app.sh` without `--sign` produced a bundle that fails `codesign --verify` + Gatekeeper. Signing is now **unconditional** (`--sign` accepted+ignored); `publish.yml` drops the flag. **Verified:** a plain `tools/build_app.sh` → `codesign --verify --deep --strict` = "valid on disk / satisfies its Designated Requirement", CLI-via-symlink self-test passes.
- **[P3] version fallback trusted any enclosing bundle** — `_version_from_app_bundle()` would borrow a host app's `CFBundleShortVersionString` when a source checkout ran under some other `.app`'s interpreter. Now gated on `CFBundleIdentifier == BUNDLE_ID`; new `test_version_from_app_bundle_only_trusts_our_bundle` proves foreign→`None`, ours→the version.
- **[P1, by design] cask placeholder sha256 / pipx→cask migration** — the all-zero sha256 is filled by `update-cask` on the first release; the pipx LaunchAgent can't be auto-repointed (chicken-egg), so upgrading is an explicit `spacelabel install` re-run — now documented in `refresh_plist_if_stale()` + README.
- **[P2, found this pass — premise corrected] cask-bump asset download** — `update-cask` hashed the release zip over a hardcoded `curl` URL. The finding claimed this 404s on a private repo, but **the repo is public** (Max, 2026-06-23), so anonymous fetch works and there was no 404 bug. Kept the switch to `gh release download` (uses `GH_TOKEN`, resolves the asset via the release API) anyway as a robustness improvement → `sha256sum`. The release workflow itself stays untested until the first real release.

#### Second codex round (same PR) — 2 false positives rejected with proof, 3 fixed
A further pass raised five more; **two were verified false on macOS and rejected** (a fix would have been a regression), three were addressed. Gates green (`356 passed`), codex re-review clean.
- **[P1 — REJECTED, false on macOS] "flock(LOCK_EX) needs a writable fd"** — claimed `_probe_lock_path` opening `agent.lock` `"r"` raises `EBADF`. **Empirically false:** `flock(LOCK_EX|LOCK_NB)` on an `O_RDONLY` fd succeeds on Tahoe — BSD `flock(2)` attaches to the open file *description*, not the access mode (that constraint is for POSIX `fcntl`/`lockf` *write* locks). Read-only is deliberate: a writable open (`"a+"`) would *create* the lock file as a side effect of a status probe. Added a docstring note; no behavior change.
- **[P1 — REJECTED, false on macOS] "`sort -z` is GNU-only"** — macOS `/usr/bin/sort` is `2.3-Apple (199)` and **supports `-z`** (proven; it's why the builds passed this session). Still **removed** the `sort` from `codesign_app.sh` for runner portability — step-1 entries are order-independent leaf Mach-O, so the inside-out invariant is held by step order, not the sort. Rebuilt: `codesign --verify --deep --strict` valid, CLI-via-symlink self-test passes.
- **[P1 — FIXED] opening the `.app` from Finder did nothing useful** — `launcher.py` dispatched the CLI with LaunchServices' argv, so a Finder/right-click→Open launch printed `--help` into the void (LSUIElement, no window). Now starts the **agent** on a GUI launch, detected by `XPC_SERVICE_NAME == application.<BUNDLE_ID>.*` (empirically: a Finder Open of *our* bundle sets exactly that; a plain shell carries `0`). Tightened after codex noted XPC is inherited: requiring **our own** bundle id means a shell under another GUI app (`application.com.apple.Terminal.*`) — or any bare `spacelabel` CLI run — still prints `--help`. New `tests/test_launcher.py` pins it (incl. the inheritance + `spacelabel2` prefix-collision guards). The single-instance lock makes a duplicate launch bow out.
- **[P1 — REJECTED, false premise] "cask points at a private-repo release URL"** — claimed `brew`'s anonymous fetch 404s because the repo is private. **The repo is public** (Max, 2026-06-23), so anonymous fetch works and the cask `url` is correct. Reverted the speculative "must be public" notes I had added to the cask + README, and fixed the stale "private" claims elsewhere (`CLAUDE.md`, `todo/critical-release-automation.md`, the `publish.yml` comment).
- **[P2 — DOC] cask not installable until the bump PR merges** — the sha256 can only be computed after upload, so `update-cask` is a follow-up PR; the default branch carries the placeholder until it merges. Documented in `publish.yml` + README (merge it promptly, or have CI push the bump straight to main). **→ Max's call.**

#### Third codex round — custom-`--config` purge/status safety — ✅ all fixed (8 findings over 3 sub-passes)
A deep pass on the multi-`--config` install model found real data-safety/status regressions. All fixed; gates green (`362 passed`), codex-clean. The model is now: **`uninstall --purge` deletes only what the *selected* install exclusively owns.**
- **[P1] default purge wiped foreign files** — it `rmtree`'d `~/Library/Application Support/spacelabel` wholesale, destroying e.g. a user's alternate `--config` `alt.json` kept there. Now `purge_targets` lists the **owned files** (config/labels/displays + their `.lock` + `agent.lock`) and `remove_default_store_dir_if_empty()` removes the dir only if nothing foreign remains.
- **[P2] custom purge deleted the default install's shared dirs** — a custom `--config` purge removed the **global** `~/Library/Caches|Logs/spacelabel` (which the agent uses regardless of `--config`). Now a custom `--config` purges **nothing** (it owns nothing exclusively safe — its dir isn't ours, the caches/logs/completions are global); the CLI says to remove the store manually + run the default purge.
- **[P2] purge could delete shared dirs under a live agent** — the guard now runs only for the default purge (custom deletes nothing, so it must not false-block on a running default agent). The residual (a default purge can't enumerate *another* custom config's foreground agent) is documented and bounded to regenerable caches/logs.
- **[P2] status false-negative for an alt config in the default dir** — an alt config sharing the default `agent.lock` was forced `running=false`, also blinding the purge guard. Now it probes the **canonical** lock so a running agent on that shared store is reported.
- **[P2, follow-on] alt config falsely "installed"** — the first fix over-corrected: an idle alt config inherited the default LaunchAgent's `installed`/`loaded`. Now `installed/loaded/managed` stay **False** for any non-`config.json` selection (launchd manages only `config.json`); only `running`/`pid` come from the shared lock.
- **[P2, follow-on] incomplete purge after a crash** — listing fixed filenames missed leaked atomic-write temps (`<json>.<rand>.tmp`), leaving the dir behind. The owned-files list now globs those temps too (a foreign name like `notes.txt` is still preserved).
- **[P2, follow-on] custom purge demanded `--yes`** — the non-interactive `--yes` gate fired even when there was nothing to delete. Now the confirm/`--yes` gate is skipped when there are no targets, so a scripted custom-config uninstall doesn't fail.
- **[P3] cask `zap` vs CLI completion cleanup** — `zap` now removes the well-known default fish/bash completion paths (best effort); the comment is honest that zsh/`$fpath`/XDG-custom locations can't be statically enumerated, so `spacelabel uninstall --purge` (resolving them at runtime) is authoritative.

#### Fourth codex round — `publish.yml` release-workflow safety — ✅ all fixed (8 findings over 2 sub-passes)
A pass on the release automation found rerun/backfill hazards (all on the `workflow_dispatch`/retry paths; the normal `release: published` path was already correct). YAML re-validated, guard logic simulated locally, codex-clean. Untested end-to-end until the first real release (no tag pushed this session).
- **[P1] dispatch built the wrong commit** — `actions/checkout` defaulted to `github.ref` (the dispatch branch), so a manual `tag=vX` rebuilt the branch tip and published those bytes under `vX`. Both build jobs now pin `ref` to the tag's commit (`inputs.tag && refs/tags/<tag> || github.ref`).
- **[P1] reruns overwrote the published `.app` zip** — `--clobber` replaced the zip with a new ad-hoc cdhash (different bytes), invalidating a merged cask bump's sha256. Tagged assets are now **immutable**: the upload skips an asset that already exists.
- **[P2] cask bump forked from the dispatch branch** — `update-cask` now checks out `ref: main`, so the bump PR contains only the checksum change, never feature-branch commits.
- **[P2] cask bump not idempotent** — reruns failed on the existing branch/PR. Now: no-op if main already carries the bump (`git diff --quiet` after the edit → exits before touching branches, covering the merged-branch case); reuse the branch via `git ls-remote`; create the PR only if none exists in **any** state (`gh pr list --state all`).
- **[P2] wheel/sdist reruns overwrote published artifacts** — same `--clobber` hazard (build env not version-locked → different hashes). Now immutable per-file (skip if already attached).
- **[P2 ×2, follow-on] concurrent reruns raced** the check-then-act guards (asset upload + branch/PR). Added a top-level `concurrency: group: release-<tag>, cancel-in-progress: false` so runs for the same tag serialize (a rerun queues behind the in-flight build) — the guards are now single-writer per tag.

#### Fifth codex round — cask cleanup + release/install plumbing — ✅ all fixed (6 findings)
Gates green (`364 passed`), `brew style` clean, codex-clean. (The pytest `ModuleNotFoundError` codex hit was its own sandbox lacking the editable install — local gates pass.)
- **[P1] cask `zap` nuked the whole data dir** — `brew uninstall --zap` trashed `~/Library/Application Support/spacelabel` wholesale, reintroducing the foreign-file data loss `uninstall --purge` had just fixed. Now `zap` mirrors the CLI: `trash` only the OWNED files (config/labels/displays + `.lock` + `agent.lock` + `<json>.*.tmp`) + dedicated caches/logs + default completions + plist, then `rmdir` the data dir (Homebrew `rmdir` is empty-only, so a stashed `alt.json` survives).
- **[P2] cask `zap` trashed the store under a live agent** — no equivalent to the CLI's flock guard. `zap` now stops the agent first (`launchctl` + `quit` + `signal: [TERM, dev.mcsim.spacelabel]`) before trashing, so a running instance's `agent.lock` isn't unlinked out from under it.
- **[P1] hardcoded Homebrew prefix in build-app** — `SPACELABEL_PY_VERSION=/opt/homebrew/...` failed on a `/usr/local` prefix. Now `export SPACELABEL_PY_VERSION="$(brew --prefix python@3.14)/bin/python3.14"`.
- **[P2] closed cask-bump PR was terminal** — `--state all` meant a mistakenly-closed bump PR could never be recreated, leaving the cask stale. Now: reuse an OPEN PR, **reopen** a closed-but-unmerged one (`jq` filters `state=="CLOSED"`, ignoring MERGED), else create. The merged case is handled earlier by the `git diff --quiet` no-op.
- **[P3] `_enclosing_app_exe` trusted any `.app`** — it accepted any enclosing bundle containing a `Contents/MacOS/spacelabel`, so `spacelabel install` from inside a *foreign* app could point the LaunchAgent at the wrong exe. Now it requires `CFBundleIdentifier == BUNDLE_ID` (new `_bundle_identifier`), mirroring `_version_from_app_bundle`.
- **[P2, follow-on] broken-XML Info.plist crashed the probe** — the new identity check (and, latently, the import-time version fallback) didn't catch `xml.parsers.expat.ExpatError`. Both now catch it and skip the bundle instead of crashing `install`/import.

#### Sixth codex round — LaunchAgent install/status + purge/cask cleanup — ✅ all fixed (6 findings)
Gates green (`368 passed`), `brew style` clean, codex-clean.
- **[P2] source/venv installs couldn't set up the LaunchAgent** — `_resolve_install_shim` hard-failed unless the cask bundle or `~/.local/bin/spacelabel` resolved, so `uv run spacelabel install` / an editable `.venv` install always raised. Added a 3rd fallback: the console script beside the interpreter (`<bindir>/spacelabel`), an absolute durable path. Guarded by `_is_ephemeral_path` so a **disposable** runner venv (`uvx`/`pipx run` → a `.cache` path, or `$TMPDIR`) is refused, not persisted; and it persists the **resolved** target so a temp/cache *symlink* to a durable venv records its real path, never the ephemeral symlink.
- **[P1] purge could delete shared logs under a live custom-config agent** — the default `uninstall --purge` clears the global `logs_dir`, which a foreground `--config X agent` also wrote to. **Root fix:** the agent now routes a genuinely-custom `--config`'s file log to **its own store dir** (`_agent_log_dir`); the shared `logs_dir` is now exclusively the default agent's, fully covered by the existing default-lock guard. (Verified: nothing writes `caches_dir`; `truncate_boot_log`/plist-refresh are already gated to managed runs — logs were the only shared state.) This supersedes the prior round's "agent logs to the global logs_dir regardless of `--config`."
- **[P2] `status` false-negative for a custom-config agent** — clarified that `status` reports the **selected** store; a foreground agent under a *different* `--config` is a separate store/lock (now its own logs too) — check it with `status --config <file>`. `status` can't enumerate arbitrary config paths (doc fix; no feasible code change).
- **[P3] confirm prompt leaked to stdout** — `click.confirm("Delete these?")` defaulted to stdout, mixing into the machine-readable channel. Now `err=True` (stderr).
- **[P3] cask `zap` missed the zsh completion** — `zap` trashed only fish/bash defaults; zsh's #1 default is `~/.zfunc/_spacelabel` (per `completion._zsh_completion_path`). Added it to the best-effort completion cleanup.

#### Seventh codex round — config-scoped LaunchAgent / status / purge (the recurring root) — ✅ all fixed (6 findings)
The recurring `--config`/LaunchAgent P1s traced to a muddled model. Settled it explicitly: **the LaunchAgent is a single GLOBAL login item tied to the default store (`install` ignores `--config`); `--config` selects a store dir for one-shot ops + run-state; run-state is per-config, disambiguated by recording the config in the lock.** Gates green (`372 passed`), `brew style` clean, codex-clean.
- **[P1] custom `--config uninstall` tore down the global LaunchAgent** — it called `_uninstall_agent_or_die()` unconditionally, so `spacelabel --config /tmp/alt.json uninstall` disabled the default install. Now both the plain and `--purge` paths remove the LaunchAgent **only for the default store** (`is_default`); a custom config leaves it in place and says so.
- **[P2] alt-config status flip-flopped** (round 5 demanded running=true, round 6 demanded running=false for the same shared-lock case). **Root fix:** the agent records `<pid>\n<resolved-config>` in `agent.lock`; `agent_status_detail` reports running iff the store lock is held **and** the recorded config matches the selected one. So config.json's agent holding the shared lock no longer marks `alt.json` running (no false positive), while an `alt.json` agent does (no false negative). Default store probes the canonical lock (symlinked-default safe); a legacy pid-only lock → config None → not-this-config.
- **[P2] wallpaper cache shared across configs** — `caches_dir` *is* used (`WallpaperRenderer` → `~/Library/Caches/spacelabel/wallpaper`); my round-6 "nothing writes caches_dir" was wrong. A custom-config agent now caches per-store (`_wallpaper_cache_dir` → its store dir), mirroring the per-store logs, so a default purge can't delete a live custom agent's wallpaper cache.
- **[P1, introduced by the F2 fix] purge guard went config-aware and missed a shared-store holder** — making status config-aware meant the guard (which used it) no longer blocked when an alt-config agent held the **shared** default lock, yet purge deletes the shared `labels.json`/`displays.json` it uses. New `unmanaged_default_lock_holder()` makes the guard **lock-level**: it blocks the default purge while *any* unmanaged process holds the canonical lock (the managed launchd agent — `lock pid == launchctl pid` — is exempt, uninstall stops it). Status stays config-aware; the guard is lock-aware — distinct questions.
- **[P2 — DOC, maintainer decision] cask placeholder sha256** — not code-fixable (the sha can only come from a built+released asset). The cask + README state it isn't installable until the first release + the `update-cask` bump PR merges; building locally is the interim path. **→ Needs Max to cut the first release.**
- **[P3] cask `zap` completion parity overclaim** — the comment now says completions are **best-effort** (default paths only), NOT full parity; `$fpath`/`$XDG`/`$BASH_COMPLETION_USER_DIR` locations need `spacelabel uninstall --purge` run **before** `brew uninstall` (the CLI is gone after).

#### Eighth codex round — install/uninstall/completion edge cases — ✅ all fixed (5 findings, no P1s)
Gates green (`377 passed`), codex-clean.
- **[P2] `install` crashed on a blocked dir** — `~/Library/LaunchAgents` or `~/Library/Logs/spacelabel` `mkdir()` raised a raw `OSError` (missing-and-unwritable, or a file in the way) before the function's `InstallError` handling. Both mkdirs are now wrapped → clean `InstallError`, no traceback.
- **[P2] `uninstall` could falsely report success** — `launchctl bootout` exits nonzero for both "not loaded" (fine) and a real failure, and `check=False` ignored both, so a genuine unload failure deleted the plist while the agent kept running until logout. Now it checks the **outcome** (`_launchctl_service_state`): if still loaded after bootout → raise `InstallError` and **leave the plist** (so a retry/manual bootout still has it).
- **[P2] bash version check could falsely reject** — `_bash_too_old` probed only `bash` on `PATH` (maybe `/bin/bash` 3.2 even when a newer Homebrew bash is installed). Now it probes `[bash, /opt/homebrew/bin/bash, /usr/local/bin/bash]` and judges by the **newest** — a usable bash anywhere means completion isn't rejected.
- **[P3] completion purge was env-dependent** — `installed_completion_files()` recomputed each target from the current env, missing a script left at the default location after `$XDG_*`/`$BASH_COMPLETION_USER_DIR`/`$fpath` changed since install. It now checks **both** the current-env target and the env-independent default per shell. Residual (a custom env-var dir changed between install and purge) is documented — `completion install` printed the path it wrote.

#### Ninth codex round — lock-probe race + lock-format/purge/release edges — ✅ all fixed (6 findings)
Gates green (`379 passed`), codex-clean. The headline was a self-introduced lock race that took two passes to get right.
- **[P1] status/purge probe raced agent startup** — `_probe_lock_path` took a brief exclusive `flock` just to answer a query; if it ran during a *starting* agent's non-blocking acquisition it could win and make the real process exit 1. **First attempt** (read the lock + signal-0 pid liveness, no flock) traded it for a **worse** race: a probe reading the lock during the agent's `truncate→write` window sees it empty and reports not-held, so `uninstall --purge` could delete the store under a just-started agent. **Final design (race-free both ways):** the probe detects held via `flock` (held is reliable through the agent's *entire* lifetime, incl. the write window), and `_acquire_single_instance_lock` now **retries** (10×30 ms) so the probe's microsecond hold can't make a starting agent spuriously exit. Single-instance correctness unchanged (the agent's flock is still authoritative).
- **[P2] legacy pid-only lock hid a running agent** — after upgrading while a foreground agent (old lock format, pid only) is alive, the config-match made status report not-running. A `None` recorded config is now attributed to the **default** config, so a live default agent isn't hidden (it rewrites the lock with its config on restart).
- **[P2] `.corrupt` recovery backups survived purge** — `store._guard_before_rewrite` renames malformed JSON to `<json>.corrupt`; `_default_store_owned_files()` now globs those too, so a default `uninstall --purge` is complete (the data dir empties and is removed).
- **[P2] cask bump could downgrade on rerun** — a manual rerun for an *older* tag after main advanced would PR a version/sha **regression**. `update-cask` now parses the cask's current version and exits 42 (job no-ops, no PR) when the requested tag is older-or-equal.
- **[P3] bash probe too narrow** — `_bash_too_old` only checked PATH + Homebrew. Now it also probes MacPorts (`/opt/local`), Nix (system + `~/.nix-profile`), and `$SHELL` when it is bash, judging by the newest, so a compatible non-Homebrew bash isn't falsely rejected.

---

## Part 3 — User-acceptance matrix · §A CLI — ✅ PASS (2026-06-23, automated)

Run via `scratchpad/run_matrix.py` against the installed **cask CLI** (0.7.0). Per Max's request, **both store paths exercised**: the bulk against an **isolated `--config` scratch store**, plus a **mutation subset on the real default store** bracketed by backup → test → restore (real `labels.json` verified **byte-identical** afterward — user data untouched).

**Result: 68/69 deterministic scratch rows pass + 7/7 real-store subset.** Groups covered: root/dispatch (A2–A8), spaces (A15/A17/A18), mode (A24–A29), label set/list/clear/prune (A31–A49, A108), note (A54–A71, H7), config get/set (A74–A92), display (A96–A104, F7), empty-store JSON `[]` (H10), completion `--dry-run` (A14.1). Exit-code + stdout/stderr-channel + error-message contracts all matched (DECISIONS §9 / A12/A13).

**Findings (neither a code defect):**
- **A29 — plan expectation corrected.** `mode hud --on --off` is **last-wins → `hud: off`, exit 0** (standard click `--on/--off` boolean), *not* the usage-error exit 2 the plan predicted. Optional: make it mutually-exclusive-strict (low value). The plan row's "exit 2" is wrong.
- **A90 — PASS, with a `--` nuance.** `config set debounce_ms -- -1` → exit **1**, `… must be >= 0; got -1` (as the plan says). Without `--`, click reads `-1` as an option → exit 2 `No such option '-1'` (also correct; leading-dash values need `--`, same as H7's `note done -5`).
- **A38** lowercase UUID accepted + canonicalized; **A14.1** completion script → stdout / target → stderr.

**Real-store subset (default `StorePaths.resolve`):** `label set/list/clear`, `note add/list/clear`, `display set/clear` (all on a **sentinel UUID** `6622AC87…`, never a live Space), and `status --json` (`managed:true, running:true`) — all pass; the real store was restored byte-for-byte (`labels.json` pre == post). Confirms the default path works identically to `--config`.

**Not CLI-runnable this pass (CGS-failure-injection rows — no CLI flag to force CGS-unavailable):** A19, A21, A22, A34, A35, A46, A50, A57, A62, A98, A99, A102, A106 and G1–G3/G11/H15. These error/fallback paths were **verified at the library level in Part 2 §1** (forced `CGSCopyManagedDisplaySpaces`→nil → `CGSUnavailableError` → plist fallback / clean error). Marked lib-covered.

### §B Menu-bar (pills + dropdown) — ✅ PASS (Max, 2026-06-23)
Observed on the reference rig (buttons-row + menubar mode on):
- **Pills row (B7–B16):** pills render (not a text title), **grouped per display** (4K group + portrait group) with a **thin vertical divider** between (B12). **Current pill full-opacity, others dimmed; color never marks current** (B11). **Labeled** Spaces show a leading letter (A/s/V, `pill_label_chars=1`, B9); **unlabeled** show the Space **number** (B8). ✅
- **Switch tracking (B2/B15):** Ctrl+→ moves the bright pill to the new current Space and the row resizes, within ~1s. ✅
- **Dropdown (B29–B33/B37):** clicking the status item opens the menu — Rename this Space…, per-display Space sections with a **✓ on each display's current** Space (B33), the four mode toggles (B37), Preferences ⌘,, Quit ⌘Q. ✅
- **Rename dialog (B30):** "Rename this Space…" opens an alert **prefilled** with the current label. ✅
- **Click-to-switch (B18):** verified earlier (P1.7) — a non-current pill click switches the Space.

Not individually exercised (note, not failures): B4 (title-mode truncation — buttons row was on), B6 (menubar-off `square.dashed` icon), B17/B19–B28 (click-to-switch display-only + the various ⚠️ reason rows — need specific permission/hotkey failure setups; item L covers the stale-grant reason), B34 (color swatch), B38/B39 (Preferences/Quit actions), B40 (live display-name reload).

### §C HUD / overlay / notes / wallpaper — ◑ partial (Max, 2026-06-23)
- **HUD (C1–C3) + Part 2 §3 — ✅:** switching Spaces shows the transient banner on the active display; **renders correctly on both the rotated 2160×3840 portrait and the 4K**; no focus theft / click-through holds (C3). ✅
- **Corner overlay (C9–C12) + Part 2 §3 — ✅:** one always-on-top panel per display showing that display's current Space label; legible + correctly placed on the rotated portrait and sane on the 4K. ✅
- **✅ FIXED & VERIFIED LIVE 2026-06-24 (item O, PR #46) — was: click-to-switch fails on the secondary (4K/left) display.** After adding a 2nd Space to the left display, clicking its pill didn't switch (worked on the portrait). **The original diagnosis (ordinal mismatch) was wrong.** Re-pinned empirically on the rig: a manual `Ctrl+1..N` probe **with the 4K focused** showed macOS's "Switch to Desktop N" numbering **matches** spacelabel's CGS enumeration exactly (Ctrl+1→4K-1, Ctrl+2→4K-2, Ctrl+3→portrait-1; both CGS order and the spaces-plist global order agree; Desktops 11–15 use distinct `Ctrl+Option+1..5` — no chord collision). **The real limitation is focus:** macOS only reliably switches the **active/focused display's** Space; cross-display chords are near-silent no-ops, and the agent posts while the portrait holds the menu bar → the 4K pill no-ops. **Fix:** gate click-to-switch to the active display (`switching.is_switchable_target`); an off-display pill shows a visible "only works on the focused display" HUD notice instead of failing silently, feature stays armed (DECISIONS §9.5, items O+V). Code + unit tests landed (`test_switching`, `test_labeling`, `test_agent_imports`). **✅ Retest PASSED live (release 0.8.2, dual-display, 2026-06-24):** a cross-display pill click shows the HUD notice and does **not** switch (4K stayed on Desktop 1, probe-verified); same-display + focus-then-switch work.
- **Improvements requested → items P/Q:** per-display overlay on/off (P); suppress overlay on displays with a single/unlabeled Space (Q).
- **Notes-in-overlay (C13/C14) — ✅:** added 2 notes to the current (unlabeled) Space + marked one done; overlay grew to show the `Desktop N` title (C14 fallback) + `☑ reply to Jane` / `☐ invoice 4012` lines, panel auto-resized (C16). Test notes restored from baseline afterward. ✅
- **`current` resolution — ✅ verified correct (A30/A57 path):** 3/3 back-to-back trials, `note add current` resolved to the live **active-display** current Space. (An initial apparent mismatch was a test artifact — Max was switching Spaces between sub-second calls.)
- **Wallpaper cache finding (pre-existing):** the desktop is currently a spacelabel composite `…/Caches/spacelabel/wallpaper/display-3.png` from **2026-06-19** (pre-v0.3.0 #19), with **no `originals.json`** — so spacelabel can't restore the real wallpaper for that display (the originals-persistence feature post-dates these PNGs). Max may want to set a fresh real wallpaper. Not a regression in 0.7.0.
- **C19–C26 wallpaper + Part 2 §6 — ⏭️ UNVERIFIED / DEFERRED (two foundational issues found).** The live composite test was **not run** — two real problems make the current wallpaper mode unsafe/unreliable on this setup:
  - **item R (Dynamic/Shuffle):** the active wallpaper was Dynamic; compositing would capture a single frame + set a static image → irreversibly clobber the Dynamic wallpaper. Must detect + skip/confirm (static-only).
  - **item S (per-Space base capture):** macOS has **per-Space wallpapers**; the portrait's current Space shows a "Dubai Skyline" photo but `NSWorkspace.desktopImageURLForScreen_` returns `DefaultDesktop.heic` (the system default) — so the composite base is **wrong**. The capture mechanism doesn't reflect per-Space wallpapers.
  - The C22 "skip if base is our cache / original unknown" guard was observed indirectly (the desktop was stuck on an old June-19 composite with no `originals.json`).
  - **Verdict:** wallpaper mode needs a **detection redesign** (items R + S) before it can be verified. It's experimental/off-by-default, so this doesn't block the release.

**§C net:** HUD ✅, overlay ✅, notes/Desktop-N ✅, `current` ✅; **click-to-switch-on-secondary-display ✅ VERIFIED LIVE 2026-06-24 (item O — gate on the active display; cross-display click = visible notice, no switch)**; wallpaper deferred (items R+S).

### §D Preferences window — ◑ partial PASS (Max, 2026-06-23)
- **D2/D6 — ✅:** window "spacelabel — Preferences", two-level outline (displays = expandable parents, Spaces = children, auto-expanded), columns Space/Label · UUID · Color · Now; current Space's Now = `now`, UUID shown. ✅
- **D4/D5 — ✅:** color picker sets a color on a labeled Space (persists, pill/swatch tints); color well disabled for unlabeled Spaces. ✅
- **D9 toggles (HUD/overlay on-off) + D10 popups (HUD position / Overlay corner) — ✅:** agent reacts live; panels move. ✅
- **◑ D1 inline edit — (c) ✅ FIXED 2026-06-24 (item V); (a)/(b) ⏳ FIXED in code (item U, PR fix/prefs-menubar-ux):** (a) minimal Edit menu added to the app so Cmd+V/C/X/Z dispatch to the field editor; (b) label clear now schedules a deferred outline reload (NSTimer interval=0) so the row live-reverts to `Desktop N`; (c) ✅ the Space shown **"Desktop 3" in Prefs appearing as "4" in the pill** — **fixed (item V):** Preferences now numbers over the same full enumeration as the pills/switch path (counts each display's default desktop), so "Desktop N" is identical everywhere (DECISIONS §9.5). **✅ Verified live (0.8.2, 2026-06-24): Preferences == pills == Mission Control, default counted (the 4K's first labelable Space reads "Desktop 2").** Hardware-pending: confirm Cmd+V works and outline reverts on clear (GUI-only).
- **⏳ D9 menu-bar OFF — fix in code (item W, PR fix/prefs-menubar-ux):** `set_inactive` now sets `setTemplate_(True)`, `setImagePosition_(1)` (NSImageOnly), and logs when the SF Symbol is unavailable. `set_title` resets `setImagePosition_(0)` (NSNoImage) so the title reappears on re-enable. Hardware-pending: confirm square.dashed icon renders correctly.
- **⏳ UX (item T, PR fix/prefs-menubar-ux):** `_build` calls `window.center()` on first open; `show()` calls `orderFrontRegardless()` to re-surface hidden windows; `_LabelColorWell.activate_` centers the shared NSColorPanel before it appears. Hardware-pending: confirm window and color picker open centered and hidden window re-surfaces.
- **⏳ J — click-to-switch toggle (PR fix/prefs-menubar-ux):** "Switch to Space on click" toggle added to the dropdown (visible when buttons row is on); "Click to switch" checkbox added as Row 3 in Preferences. `toggleClickToSwitch_` writes `menubar.click_to_switch`. Hardware-pending: confirm toggle works end-to-end.
- Wallpaper checkbox not exercised (items R+S).

### Acceptance run — multi-display click-to-switch & Desktop-N (PR #46, release 0.8.2, 2026-06-24)
Live on the dual-display rig (4K primary + rotated portrait, "separate Spaces" ON, a default desktop, Switch-to-Desktop shortcuts enabled), driven step-by-step; every click outcome probe-verified against the live current Space (`tools/acceptance_probe.py`).
- **O — ✅:** same-display pill switches; **cross-display click → HUD "only works on the focused display" notice + no switch** (4K stayed on Desktop 1, probe-confirmed); the same pill switches once its display is focused.
- **X — ✅:** the default unlabelable desktop's pill **switches to it** when its display is focused (resolved by `(display, id64)`).
- **AA — ✅:** active display on its default desktop → title/HUD/overlay show **"Desktop 1"** (probe expected-title confirmed) — not the generic name or another display's label.
- **V — ✅:** the default is **counted**; Preferences == menu-bar pills == Mission Control once the agent re-enumerates (the 4K's first labelable Space reads "Desktop 2").
- **(display, id64) hardening — ✅:** exactly one current desktop marked per display; numbering counts the default with no fabricated rows.
- **Observed — item F (not a regression here):** the agent/Preferences don't re-enumerate on a Space **create/delete** (only on a switch / display change), so their numbering trails the live system until the next refresh. Reinforces item F's priority.

### §A CLI — second scriptable pass — ✅ 28/28 (2026-06-23, `run_matrix2.py`, isolated `--config`)
Banked the rows the first pass skipped + scriptable Part-4:
- **Logging/channel:** A9/A10 (`--verbose`/`--debug` accepted before **and** after the subcommand), A11 (data on stdout **identical** across log levels; verbosity only on stderr), A23 (no ANSI escapes in piped stdout even with `--debug`).
- **Store semantics:** A39 (`created_at` preserved, `updated_at` bumped across two `label set`), A47/A107 (clear/inspect a **legacy non-UUID key** — `validate=False` paths).
- **Config validation depth:** A85 (`overlay.bold` accepts on/yes/1/off/no/0), A87 (`buttons_scope` enum), A89 (`overlay.font_size` int/auto/0/abc), A93 (unknown-key checked before value), A94 (**invalid value → exit 1 + `config.json` byte-unchanged**, validate-before-write), A105 (clear legacy display key).

## Part 4 — Additional scenarios — ◑ (scriptable ✅; UI/hardware pending)
**Scriptable rows — ✅ (in the passes above):** H7 (`note done 0`/`-5` → exit 2), H8 (`hud.font_size` fresh=`auto`; `overlay.note_font_size 6` ok / `0` exit1; `hud.margin -1` exit1), H9 (**anchors case-sensitive** post-strip reject vs **bools case-insensitive** accept — asymmetry confirmed), H10 (empty-store `--json` → `[]`), H14 (`schema_version: 2` read **best-effort**), H17 (leading-space + emoji labels stored **verbatim**).
**UI/hardware rows (Max, 2026-06-23):**
- **H1/H2 — ✅:** a fullscreen Chrome Space is **not** listed in `spaces` and gets **no pill** (type!=0/TileLayoutManager skipped). ✅
- **✅ FIXED 2026-06-24 (item Z, PR overlay-behavior) — was: H2/H3 overlay/HUD stale on fullscreen.** `_update_overlays` now calls `order_out()` when `current is None` (fullscreen Space filtered out) or `is_labelable(current)` is False; `_update_hud` is suppressed when `active_space is None` or not labelable. Hardware confirm (live fullscreen test) still pending — code path covered by unit tests in `test_agent_imports.py` (Z gate). Additionally: per-display overlay toggle (P) and `overlay.hide_on_unlabeled` suppression flag (Q) added; `display overlay-on/off` CLI commands + Preferences overlay checkbox per-display row; `displays.json` extended with `overlay_disabled` list (backward-compatible). Tests: `test_store` (overlay-disabled round-trip + clobber prevention + config schema), `test_labeling` (`is_labelable`), `test_agent_imports` (Z/P/Q gate assertions + HUD suppression). All gates green.
- **H4 — ✅:** revoking Accessibility mid-run → next pill click re-checks and shows the ⚠️ reason popup (no silent no-op); re-enabling restores it. ✅
- **H6 — ✅ (flagged risk cleared):** clicking a high-ordinal pill (~Desktop 10–14) on the portrait switches correctly — contiguity past Desktop 3 confirmed (ids 118–132 all bound). ✅
- **❌ H18 — bug → item Y:** `NO_COLOR=1 spacelabel spaces` does **not** drop the bold/green coloring on a TTY (`NO_COLOR` not honored by the table color helper). Minor.
- **H5** (AX binding absent) — not forceable on this machine → lib-note.
- **Deferred:** H11 (re-install while loaded — low-risk, scriptable later), **H16** ("separate Spaces" OFF — requires logout), **reboot §5** + **Part 2 §7 detach-4K generality** — to a natural restart/hardware window.
- H15 (CGS zero-displays) = lib-covered (Part 2 §1).
