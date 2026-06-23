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

Legend: ✅ pass · ⚠️ pass-with-note · ❌ fail · 🟡 N/A (by design / feature not shipped) · ⏳ deferred (hardware/UI, pending Max)

---

## Part 1 — Install & distribution acceptance (pipx) — COMPLETE

### 1A. pipx install matrix

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

**Part 1 verdict: PASS.** All scriptable install/distribution rows pass; PyPI-by-name and `--purge`/Homebrew correctly N/A (deferred features). No defects.

> **⚠ Distribution pivot (Max, 2026-06-22):** during Part 2, click-to-switch testing surfaced that the pipx shared-python identity can't be granted Accessibility reliably (see the finding below). Decision: **move from pipx to a signed `.app` shipped via a Homebrew cask** (reverses #30). Part 1's pipx results remain a **valid record of the current build's behavior**, but the install/distribution path will be **re-verified under the cask** in the follow-up session (`todo/phase-6-blockers.md` Tier 1). The CLI/agent behavior tested in Parts 2–4 is distribution-agnostic and stands regardless.

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

### Steps 2–7 — ⏳ pending Max (UI / hardware)
Need the running agent + your hands/eyes: (2) WhichSpace reorder demo, (3) display modes on rotated 2160×3840 + 4K, (4) menu-bar/prefs rename live-reload, (5) reboot persistence [deferred], (6) experimental wallpaper revert, (7) generality spot-check (detach 4K / change res-orientation / add-remove Space). I'll drive each with exact steps and you report/screenshot.

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
