# spacelabel — Decisions Log

> Every locked decision with **rationale**, **confidence**, and **open questions**. Companion to [`DESIGN.md`](./DESIGN.md). Phase 1 (deep research + adversarial verification) produced these; later phases append/revise and must update the "Affected phases" notes at the bottom.
>
> Confidence scale: **high** = verified on the reference machine or against ≥2 independent authoritative sources; **medium** = single strong source or consistent inference; **low** = inferred, unverified, deferred.
>
> Method: 7-track parallel research workflow → 2 adversarial verifiers (skeptic-framed, with live on-device recon) → completeness critic. Reference machine: macOS 26.5.1 (build 25F80), arm64, SIP **enabled**, Homebrew Python 3.14.4, pipx 1.11.1, uv 0.11.8.

---

## 0. ⚠️ Baseline correction (load-bearing — read first)

The Shared Baseline says *"ctypes `dlopen` of SkyLight for the private CGS reads."* On-device verification proves this is **imprecise on macOS 26** and it must be read as corrected:

- SkyLight has **no on-disk Mach-O** on Tahoe (shared-cache only; `nm` returns nothing) and exports the four symbols **only under `SLS*`** names — **zero `CGS*` exports**.
- **`CoreGraphics.framework` re-exports** them under the legacy `CGS*` names; that is the framework to bind against.
- A binding written as `CDLL(SkyLight).CGSMainConnectionID` works **today only by accident** of process-global symbol resolution (CoreGraphics is already loaded), and is one point-release away from a silent `AttributeError` if the `CGS` alias is ever dropped.

**Corrected decision:** bind against **CoreGraphics**, resolve each symbol **`CGS`-name-then-`SLS`-name**, raise a logged `CGSUnavailableError` on miss → fall back to the spaces plist. **Confidence: high** (empirically dlopened + symbol-resolved live on the reference machine; `dladdr` confirmed `CGS*`→`SLS*` ownership by SkyLight).
**Affects:** the baseline text carried into Phases 2–6; the Phase-4 `cgs.py` implementation; the Phase-6 probe.

---

## 1. CGS read path (highest risk)

| # | Decision | Rationale | Confidence |
|---|---|---|---|
| 1.1 | **Bind CGS against CoreGraphics, not SkyLight directly; CGS→SLS getattr/loadBundle fallback per symbol.** | See §0. CoreGraphics holds the `CGS*` re-exports directly; SkyLight only has `SLS*`; ApplicationServices exports almost nothing directly on Tahoe (works only via transitive CoreGraphics re-export). | high |
| 1.2 | **Committed read pattern = PyObjC `objc.loadBundleFunctions` of CoreGraphics**, with `{'retval': {'already_retained': True}}` on the two `Copy` functions; auto-bridged `NSArray`/`NSString`. | Only precedent (`css` 2024, `JARVIS` 2025) that *provably* balances the `+1` retain for a long-lived poller; auto-bridge removes manual CoreFoundation plumbing (fewer silent-bug surfaces). PyObjC already locked → free. Critique explicitly required committing to **one** ownership pattern, not a menu. | medium–high |
| 1.3 | **Fallback read pattern = `ctypes.CDLL(CoreGraphics)` + `objc.objc_object(c_void_p=…)` + deep-convert then exactly one `CFRelease`.** Pin `CGSConnectionID=c_uint32`, `CGSSpaceID=c_uint64`; `restype=c_void_p` for Copy results, `c_uint64` for `CGSManagedDisplayGetCurrentSpace`. | Documented contingency for a PyObjC build where the annotation misbehaves, and for debugging. Widths are AltTab-proven on arm64 (the #1 ABI hazard — connection width diverges across sources: AltTab `UInt32` vs Phoenix `NSUInteger` vs CGSInternal `int`; pin to AltTab). | high (widths) / medium (path) |
| 1.4 | **Label key = per-Space `uuid` string, not `id64`/`ManagedSpaceID`.** | `uuid` is what macOS persists to `com.apple.spaces.plist`, so it is the durable cross-reorder/cross-reboot key; `id64`/`ManagedSpaceID` are session-scoped and reassignable (AltTab uses `id64` only because it needs *within-session* identity, not persistence). | medium |
| 1.5 | **Live current-Space via `CGSManagedDisplayGetCurrentSpace`; map id→uuid through the parsed Spaces list.** Do not trust the dict's `"Current Space"` for liveness. | The dict `"Current Space"` can lag right after a switch; the plist definitely lags (cfprefsd cache). The live call is authoritative. | high |
| 1.6 | **Labelable-Space test = `type == 0` AND no `"TileLayoutManager"` key.** Never hardcode the fullscreen `type` constant. | Sources disagree on the fullscreen value (CGSInternal `1`, Phoenix `4`); WhichSpace detects special Spaces by the **presence** of `TileLayoutManager`. Treat any non-zero `type` / that key as skip. | medium |
| 1.7 | **Handle the `"Display Identifier" == "Main"` sentinel** (remap to primary display UUID); defensively remap any non-UUID-parseable identifier. | When "Displays have separate Spaces" is off, the key is the literal `"Main"`; AltTab/WhichSpace both special-case it. | high |
| 1.8 | **Active display = `CGSCopyActiveMenuBarDisplayIdentifier`; fallback = `NSScreen.mainScreen()` CFUUID.** | That symbol is single-sourced (AltTab only). The fallback covers a future rename/removal so active-display logic never silently targets the wrong screen. | medium |

**Open questions (→ Phase 6):**
- 🔑 Is the per-Space `uuid` **stable across reboot**? This is the #1 project assumption. **Upgraded to ~high confidence without a reboot** (follow-up research, 2026-06-19): the uuid is a persisted CFUUID in the `cfprefsd` `com.apple.spaces` domain (read back at login), macOS keys its own reboot-surviving features on it (`app-bindings` "Assign to Desktop", pre-Sonoma per-Space wallpaper), and `dado3212/spaces-renamer` keys names solely on the uuid string with the author stating they "persist between reboots and … re-ordered or recreated." Decisively, **all** known "scrambled on reboot" reports trace to the volatile integer `id64`/`ManagedSpaceID`/index, never the uuid string. Source code alone can't prove it (WindowServer/SkyLight is closed; reference repos only consume the uuid). Confirm cheaply without a full reboot: (1) read-only — match live `CGSCopyManagedDisplaySpaces` uuid to the on-disk plist `Spaces[].uuid` (not the stale `"Current Space"`); (2) near-definitive — logout/login or `killall -HUP WindowServer` (same teardown/reload path as boot) and diff uuid strings. Only a real reboot additionally covers new-display / NVRAM / OS-upgrade edge cases. Phase 6 still does the literal reboot capture as the final gate.
- Does `already_retained:True` (vs `already_cfretained:True`) correctly balance the `+1` for this PyObjC build? Confirm by RSS-watch in a tight loop (flat RSS = correct; rising = leak; crash = over-release).
- The PyObjC↔CFArray bridge round-trip was **not** exercised on 26.5.1 (PyObjC absent from the base interpreter during recon); only `ctypes` symbol resolution was confirmed. The bridge is the actual Phase-6 smoke test.
- `id64` vs `ManagedSpaceID` equivalence; `TileLayoutManager` fullscreen marker — design-only, confirm on hardware.

---

## 2. Module & dependency choices

| # | Decision | Rationale | Confidence |
|---|---|---|---|
| 2.1 | **Menu bar: raw `NSStatusItem`; reject `rumps`.** | rumps is a thin wrapper over the same ~15 calls, adds no capability, would seize `NSApplication`/run-loop ownership (collides with our delegate + workspace observer), and is a pipx trap (sdist-only, `requires_dist` null → wouldn't even pull PyObjC). Baseline said "rumps only if it clearly wins" — it does not. | high |
| 2.2 | **HUD + overlay: borderless non-activating `NSPanel`**, `canBecomeKeyWindow/Main → False`, `ignoresMouseEvents`, `CanJoinAllSpaces\|Stationary\|FullScreenAuxiliary`, `orderFrontRegardless`. HUD level `NSScreenSaverWindowLevel` (~101); overlay `NSStatusWindowLevel` (25). | Must float across all Spaces and never steal focus (opposite of AltTab, which captures keys). Verified level ordering and collection-behavior semantics. | high |
| 2.3 | **Wallpaper text via `NSBitmapImageRep` + Core Text; reject Pillow.** | PyObjC is already locked → zero new dep, correct system fonts + Retina scaling. Pillow adds a dep for trivial text-over-image. | high |
| 2.4 | **Preferences: view-based `NSTableView`** (dataSource+delegate, editable label column); reject WebView; NSStackView only as a tiny-list fallback. | Data is naturally tabular/editable (UUID, label, display); native columns/selection/resize scale to N Spaces. | high |
| 2.5 | **CLI: `click` (≥8.1); reject `argparse`.** | The surface is a real nested command tree; click cites argparse's "does not allow proper nesting of commands by design"; click earns its third-party slot (declarative options, `Choice`/`Path`, auto-help, `Context`) under stdlib-first. | high |
| 2.6 | **Logging: stdlib `logging` only.** Library uses `getLogger(__name__)` + `NullHandler`; one `setup_logging(mode=…)` at entry; CLI→stderr (WARNING/INFO/DEBUG), agent→`RotatingFileHandler` under `~/Library/Logs/spacelabel/`; optional feature-detected `os_log` mirror. | Matches the logging HOWTO and the agent-quiet/CLI-verbose requirement; `~/Library/Logs` is Apple-conventional, needs no privileges/SIP change. | high |
| 2.7 | **Activation policy: `NSApplicationActivationPolicyAccessory` set in code** (not `LSUIElement`). | Gives a no-Dock-icon agent that can still show windows; runtime-settable since 10.9; no bundled `.app`/Info.plist needed for the pipx path. | high |
| 2.8 | **Run loop: `PyObjCTools.AppHelper.runEventLoop()`.** | Installs PyObjC's autorelease-pool + exception handling around the AppKit loop; the documented idiomatic GUI loop (vs `runConsoleEventLoop` for headless). | high |

**Open questions:**
- os_log PyObjC import path on Tahoe unverified → keep optional/feature-detected (low; not load-bearing).
- HUD level 101 not covering system alerts on Tahoe — verify on hardware (medium).
- Label-edit commit semantics (Enter vs focus-loss) — UX detail for Phase 3/4.

---

## 3. Display topology & generality

| # | Decision | Rationale | Confidence |
|---|---|---|---|
| 3.1 | **Discover topology at runtime via `NSScreen.screens()`; never hardcode count/resolution/orientation/scale/arrangement.** | Portability requirement; orientation inferred from `frame` (portrait if h>w), scale from `backingScaleFactor`, arrangement from `frame.origin`. | high |
| 3.2 | **NSScreen↔CGS display id: `NSScreenNumber → CGDirectDisplayID → CGDisplayCreateUUIDFromDisplayID → CFUUIDCreateString` == CGS `"Display Identifier"`.** Reach `CGDisplayCreateUUIDFromDisplayID` via PyObjC `Quartz` (it lives in ColorSync since 10.13, re-exported through the CoreGraphics umbrella). | Verified identical to AltTab `Screens.swift`. This CFString is the join key across NSScreen ↔ Spaces array ↔ active-menubar display. A bare path-dlopen of only CoreGraphics could miss the ColorSync symbol. | high (mapping) / medium (link path) |
| 3.3 | **React to attach/detach via `NSApplicationDidChangeScreenParametersNotification` (default center); fully re-discover; never cache UUIDs/counts.** | `CGDisplayRegisterReconfigurationCallback` silently stopped firing on Tahoe 26.1 — the AppKit notification is the reliable trigger. | high |
| 3.4 | **Plist (`com.apple.spaces.plist`) is a topology/UUID-enumeration fallback only — never a live current-Space source.** | cfprefsd flushes it only on Space create/delete, so `"Current Space"` lags ordinary switches; WhichSpace watches the file's delete event and re-queries CGS live. | high |
| 3.5 | **Treat Apple's documented "up to 16 spaces" as sanity/UI context, not a hardcoded cap.** | Officially documented (Tahoe help page mh14112) but a *soft* Mission Control limit; per-display-vs-total unspecified; subject to change; the portability rule forbids hardcoding counts. Useful only to confirm the prefs table needs no virtualization and as a parse-sanity bound. | high (documented) / medium (semantics) |

**Open questions:**
- `activeSpaceDidChangeNotification` remains identity-less and non-deprecated on every future 26.x — inferred from secondary sources (primary Apple doc was an unreadable JS SPA) (medium).
- That `"Main"` is the *only* non-UUID `"Display Identifier"` value on Tahoe — taken from AltTab/WhichSpace; handle any non-UUID defensively (medium).

---

## 4. Space-change observation & debounce

| # | Decision | Rationale | Confidence |
|---|---|---|---|
| 4.1 | **Observe `NSWorkspaceActiveSpaceDidChangeNotification` on `NSWorkspace.sharedWorkspace().notificationCenter()`** (not the default center); re-read the UUID every fire. | The workspace notification posts only on the workspace center and carries no Space identity. | high |
| 4.2 | **Trailing-edge debounce ~200ms; the debounced callback does the off-main CGS read, then marshals the UI update to the main thread.** | Rapid switching is common; without coalescing the agent thrashes the CGS path and UI. CGS reads are pure IPC and run off-main (AltTab does this); UI is main-only. | high (need) / medium (exact timing) |

---

## 5. Data model (specified in Phase 1; was the critique's top gap)

| # | Decision | Rationale | Confidence |
|---|---|---|---|
| 5.1 | **Two JSON files under `~/Library/Application Support/spacelabel/`: `labels.json`, `config.json`, each with a top-level `schema_version`.** JSON (not TOML) per the task spec. | Named Phase-1 deliverable; `schema_version` enables forward-compatible migrations; Application Support is the conventional user-data location. | high (design choice) |
| 5.2 | **`labels.json`: `{ schema_version, labels: { <space_uuid>: { label, last_display?, created_at?, updated_at? } } }`.** Only `label` required. | Nested per-UUID object leaves room for metadata without a schema break; keyed by the stable `uuid` (decision 1.4). | high |
| 5.3 | **`config.json`: `{ schema_version, modes:{menubar,hud,overlay,wallpaper}, per-mode settings, debounce_ms, log_level }`.** | Mode toggles are the spec's stated config; per-mode blocks keep settings discoverable. | high |
| 5.4 | **Atomic writes: temp file in same dir → `fsync` → `os.replace`.** Readers never see a partial file. | `os.replace` is atomic on one filesystem; this is the standard safe-write idiom. | high |
| 5.5 | **Writers (CLI + prefs window) do read-modify-write under an `fcntl.flock` advisory lock, then atomic replace; the agent watches files and reloads on change.** | Two human-paced writers exist (CLI and the agent's prefs UI); advisory lock + atomic replace prevents lost updates and partial reads; file-watch gives live reload without restart (WhichSpace-style delete-event watch). | high (design choice) |
| 5.6 | **Orphaned UUIDs: retain by default; explicit `label prune` + `label clear` for removal.** | Reorders never orphan; if a deleted Space is recreated with the same UUID the label re-binds; and since cross-reboot UUID stability is unverified we must not auto-delete. | high (policy) / depends on 1.4 open question |

---

## 6. Install & runtime (confirmed end-to-end on the reference machine)

| # | Decision | Rationale | Confidence |
|---|---|---|---|
| 6.1 | **One package, one `console_scripts` entry point; agent started by `spacelabel agent` (no separate `spacelabeld`).** | Shares the CGS/store layers; pipx exposes the shim at `~/.local/bin/spacelabel`. | high |
| 6.2 | **Deps: pyobjc-core, -Cocoa, -Quartz, -CoreText, click. PyObjC ships `cp314 universal2` wheels → pipx installs with no compiler. SkyLight/CGS is never a PyPI dep.** | Verified live: `pyobjc-core 12.2.1` universal2 wheel downloads; CGS is dlopened at runtime. | high |
| 6.3 | **No `disable-library-validation` entitlement, no codesigning, no SIP-off.** | Apple-signed system frameworks are out of LV scope, and the Homebrew interpreter is adhoc/non-hardened (verified `flags=0x2`), so LV isn't enforced; `ctypes.CDLL` of a private framework succeeded live under SIP. | high |
| 6.4 | **LaunchAgent: `LimitLoadToSessionType=Aqua`, `RunAtLoad`, `KeepAlive={SuccessfulExit:false}`, `ProcessType=Interactive`, absolute `$HOME`-templated paths, logs under `~/Library/Logs/spacelabel/` (mkdir before load). Load via `launchctl bootstrap gui/$UID`.** | Verified `launchctl print gui/$UID` → `session = Aqua` (NSStatusItem needs the GUI session; a daemon has none). `KeepAlive` crashed-only so a menu Quit stays stopped. launchd doesn't expand `~`/`$PATH`. launchctl 2.0, not deprecated `load -w`. | high |
| 6.5 | **Run exactly one agent instance at login (only `RunAtLoad`, no second auto-start).** | macOS 26 has a documented `NSStatusItemChangeVisibilityAction` negotiation loop with ControlCenter (BetterDisplay #5314) that two instances aggravate. | high |
| 6.6 | **uv for dev (`uv venv`, `uv pip install -e '.[dev]'`, `uv run`), pipx for distribution; never share an environment.** | Both stdlib-venv from the same interpreter; pipx uses `venv --without-pip`; no conflict. | high |
| 6.7 | **Reverse-DNS id = `dev.mcsim.spacelabel` (personal namespace), kept as a single source-of-truth constant** reused as the LaunchAgent `Label`, the plist filename, and the `os_log` subsystem. | Repo starts under github.com/McSim85; one constant makes the later move to a Quicknode namespace (`io.quiknode.spacelabel`) a one-line change. Aligns Phase 1 with the Phase-2 task's `dev.mcsim.spacelabel` (resolves the prior naming conflict). | high |

**Open questions / residual risks:**
- "Process alive" ≠ "icon visible" on Tahoe: System Settings → Menu Bar visibility toggle and the ControlCenter loop can hide the icon. Surface a fallback + document the Settings check (high-impact, design-mitigated).
- Interpreter pinning: `brew upgrade python` minor bump orphans the pipx venv → `pipx reinstall` (and recreate uv `.venv`).
- macOS-26 PyObjC forward-compat is empirical-only (works on 26.5.1; no vendor cert). Keep CGS reads behind try/except + plist fallback.
- A hardened+notarized py2app bundle would re-engage library validation (out of scope for the pipx/personal path).

---

## 7. Wallpaper mode (experimental)

| # | Decision | Rationale | Confidence |
|---|---|---|---|
| 7.1 | **Ship wallpaper mode disabled-by-default, labeled experimental, documented cosmetic/may-revert.** | No per-Space wallpaper API exists; `setDesktopImageURL:forScreen:` is per-NSScreen; on Sonoma+/Tahoe WallpaperAgent owns state, self-reverts, and silently flips "Show on all spaces" off on repeated sets. | high |
| 7.2 | **Render via `NSBitmapImageRep`+Core Text at `frame.size × backingScaleFactor`; write to a stable per-display temp PNG overwritten in place; set per active screen.** | Retina-crisp, no Pillow; WallpaperAgent reads the file async so don't delete-too-early (blank desktop). | high (mechanics) / low (durability — can't be made durable) |
| 7.3 | **Do NOT edit the WallpaperAgent config store / container plists directly.** | Undocumented, fragile across point releases, risks corrupting the user's wallpaper config. | high |

---

## 8. Repository layout & packaging (Phase 2)

| # | Decision | Rationale | Confidence |
|---|---|---|---|
| 8.1 | **src-layout: package lives in `src/spacelabel/`** with the DESIGN §2 tree (`platform/` + `agent/` subpackages). | PyPA best practice: disambiguates the repo dir from the package, prevents accidental imports of the in-tree source, and works with both editable installs and pipx. Phase 2 owns final layout (DESIGN §2 is "intent, not a binding contract"). | high |
| 8.2 | **Repo name `spacelabel`; created **private** under github.com/McSim85; MIT © Max Kramarenko.** | Max's call — personal account for now, with a possible later move to a Quicknode namespace (a one-constant change, see 6.7). Private is reversible to public at any time. | high |
| 8.3 | **One console entry point confirmed end-to-end:** `spacelabel = "spacelabel.cli:main"`, plus a `python -m spacelabel` alias; `spacelabel agent` lazily imports and calls `agent.app.run_agent`. | Verified locally: editable install, `spacelabel --help`/`--version`, and `agent` dispatch all work (the §6.1 design, now exercised). Heavy imports (PyObjC, store, agent) stay lazy inside command bodies so the CLI never pulls AppKit for `--help`. | high |
| 8.4 | **Tooling = ruff (lint + format) + mypy `strict` + pytest; pre-commit mirrors the CI gates; CI runs on `macos-latest`.** | Engineering-standards requirement (PEP 8/257/484). The PyObjC framework wheels are macOS-only, so a Linux runner cannot even install the package — CI must be macOS. All four gates are green on the scaffold. | high |
| 8.5 | **Exception renamed `CGSUnavailable` → `CGSUnavailableError`** across code + DESIGN.md + DECISIONS.md. | PEP 8 exception naming (ruff `N818`); keeps the locked design and the code in sync. | high |
| 8.6 | **The LaunchAgent invokes `spacelabel agent`** (locked design §8.1/§9.2/6.1), **not** the Phase-2 task's loose wording "`run`". The plist template `packaging/dev.mcsim.spacelabel.plist` uses `__HOME__` tokens that `install.py` substitutes. | Reconciles the task↔DESIGN wording conflict in favour of the locked design (there is no `run` subcommand). | high |

**Notes / residual:**
- `logging_setup` is implemented for **CLI** mode (stderr at WARNING/INFO/DEBUG); the **agent** file sink (`RotatingFileHandler` + optional `os_log` mirror) is a `TODO(phase-4)` and currently falls back to stderr.
- mypy PyObjC per-module overrides are present but "unused" until Phase 4 imports PyObjC; `warn_unused_configs` is intentionally left off until then.
- pre-commit revs pinned via `pre-commit autoupdate` (pre-commit-hooks v6.0.0, ruff v0.15.18, mirrors-mypy v2.1.0); contributors re-run autoupdate as needed.
- `uv.lock` is gitignored (deps are pinned in `pyproject.toml`; pipx is the distribution path). Revisit if reproducible dev pinning is wanted.
- The pre-commit git hook is **installed locally** (`pre-commit install`). **Branch protection on `main` and GitHub Discussions are intentionally deferred until the repo goes public** (they add solo-dev friction and need an audience / a green CI run first); the how-to is recorded in `.github/SETTINGS.md` as the go-public checklist.

---

## Cross-phase impact (hand-off)

- **Phase 2 (repo layout): ✅ DONE** — see §8. Repo scaffolded (src-layout, stub modules, `pyproject.toml` with the 4 PyObjC parts + click, `spacelabel = "spacelabel.cli:main"`, ruff/mypy-strict/pytest, pre-commit, packaging, GitHub templates + macOS CI). Initial commit pushed to **private** github.com/McSim85/spacelabel. All four gates green.
- **Phase 3 (CLI/UI concepts):** the CLI surface (§8.1) and the four display-mode designs (§6) are the concept inputs; the Tahoe menu-bar transparency/visibility caveats (decision 6.5, §6.1) and the experimental-wallpaper framing (§7) should shape the UI copy. The command tree is now scaffolded in `src/spacelabel/cli.py` (stubs) and rendered in the README — use **`agent`** (not `run`) and the exact subcommand names there as the canonical surface.
- **Phase 4 (code + tests):** fill the stubs under `src/spacelabel/` (every module has a docstring + `TODO(phase-4)` markers). Implement `cgs.py` per the **committed** loader (1.1–1.3) — not the Phase-1 sketches that bound SkyLight directly — raising the renamed **`CGSUnavailableError`** (8.5). Implement the data model (§5 / §7-decisions): atomic writes, `fcntl.flock`, watch/reload, prune. Honor the no-silent-except policy at every CGS/plist site. The notification-center + debounce footgun (§4) is an explicit adversarial-review target. Scaffold realities to build on: src-layout (import from the installed package); the console entry point + `agent` dispatch are confirmed working (8.3); finish `logging_setup`'s **agent** branch (RotatingFileHandler + feature-detected `os_log`); `install.py` must substitute `__HOME__` in `packaging/dev.mcsim.spacelabel.plist`, `mkdir ~/Library/Logs/spacelabel` before load, and ensure one instance; once PyObjC is actually imported, the mypy overrides become "used" (consider re-enabling `warn_unused_configs`).
- **Phase 5 (backlog):** open questions in §1/§2/§3/§6 are backlog candidates; the wallpaper durability ceiling (§7) bounds what to promise.
- **Phase 6 (verification):** run the §12 checklist of `DESIGN.md`. **Gate the whole project on item 1 (uuid reboot-stability) and item 3 (RSS-flat memory).** If `uuid` is not reboot-stable, revisit decision 1.4 (the entire UUID-keying premise).

## Completeness gate (Phase 1)
Critic verdict: **ready-with-open-questions** — design may be locked with the §0 correction baked in (done) and the data model specified (done, §5). No unresolved blockers remain for Phase 2; the load-bearing empirical assumptions (uuid stability, CF memory ownership, the live bridge round-trip) are explicitly deferred to the Phase-6 probe rather than left implicit.
