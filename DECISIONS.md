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
- Label-edit commit semantics (Enter vs focus-loss) — **resolved in Phase 3 (9.6): commit on both, Esc cancels.**

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

## 9. User-facing surfaces — CLI & UI (Phase 3)

> Design-only phase. Deliverables: [`docs/CLI.md`](./docs/CLI.md),
> [`docs/UI.md`](./docs/UI.md) + a rendered mockup
> ([`docs/ui-mockup.html`](./docs/ui-mockup.html); hosted artifact linked in
> UI.md). The scaffolded `cli.py` command tree (§8.3) is treated as canonical —
> **no commands renamed**; everything below is additive.

| # | Decision | Rationale | Confidence |
|---|---|---|---|
| 9.1 | **Exit-code contract: `0` success, `1` runtime/application error, `2` usage error (click's own codes), `3` reserved for `status` = "agent not running".** | Verified against the scaffold (`ClickException`→1, `UsageError`→2). `3` (LSB-style) lets `spacelabel status` compose in shell conditionals without conflating "down" with "errored". | high |
| 9.2 | **stdout = machine-readable data only; stderr = all diagnostics/logs/headers.** Default `spaces`/`label list` output is **tab-separated, stable column order, header line to stderr** (parseable with `cut`/`awk`); structured consumers use `--json`. | DESIGN §8.1 promises "scripts can parse stdout cleanly". Aligned-column tables aren't cleanly parseable, so the default machine format is TSV, not a padded table; logging never writes stdout (enforced by `setup_logging`). | high |
| 9.3 | **Additive CLI flags only:** `--json` (`spaces`, `label list`, `status`, bare `config get`), `--dry-run` (`label prune`), `--active-display/--all-displays` (`spaces`), `--no-load` (`install`), `--keep-labels` reserved (`uninstall`). | Cover the spec's machine-output + safety needs without touching the locked command tree. Each maps to a Phase-4 option on an existing command. | high (design) |
| 9.4 | **Buttons row = ONE `NSStatusItem` + custom CG view.** Pills show first letter(s) of the label, else the Space number; **current marked by alpha (1.0/~0.4), never color**; physical displays split by a thin vertical divider, drawn L→R; scope toggle all-vs-active display. | Re-states 2.1/6.5 for the UI: N items worsen Tahoe ControlCenter hiding + notch overflow. Alpha (not color) keeps per-label color free as a user tag and reads "current" unambiguously with one-per-display. | high |
| 9.5 | **Click-to-switch is opt-in, OFF in v1; display-only by default.** On enable, guide the two one-time steps (Mission Control "Switch to Desktop 1…N" shortcut + Accessibility), then post synthetic Ctrl+N via `CGEventPost` with a **live** UUID→ordinal map. If the shortcut can't be confirmed, **disable the action with a visible reason — never silently no-op.** | Space *switching* is the one operation behind the SIP/Dock wall; making it explicit keeps the default safe and honors the no-silent-except policy at the UI layer. Ordinals shift on reorder → resolve at click time, never cache. | high (policy) / medium (CGEventPost path, Phase-6 verify) |
| 9.6 | **Preferences inline-edit commits on BOTH Return and focus-loss; Esc cancels.** Two-level `NSOutlineView` (display→Spaces) per 2.4; color column via `NSColorWell`. | Resolves the §2 open question ("Enter vs focus-loss"). Matches native table-edit expectations; both commit so a user never loses an edit by clicking away. | high |
| 9.7 | **New `config.json` keys** (additive, schema_version stays 1): `menubar.show_buttons_row` (bool, def false), `menubar.buttons_scope` (`all_displays`\|`active_display`, def all), `menubar.pill_label_chars` (1–2, def 1), `menubar.click_to_switch` (bool, def false); `hud.position` (one of the 9 anchors, def `center`), `hud.margin` (int pt, def 24); `overlay.font_size` accepts `"auto"` as well as int; `overlay.corner` accepts any of the **9 anchors** (def `top-right`). | The UI exposes these as menu/prefs toggles and the `mode`/`config` CLI writes them. Defaults keep the quiet title-only behavior; **HUD/overlay placement is user-configurable** (asked for in Phase 3 review); all are forward-compatible additions. | high (design) |
| 9.8 | **New `labels.json` field `color`** (optional hex string) per entry; **UUID remains the sole key.** | Color is a per-label user tag surfaced in pills/overlay/HUD/prefs; informational/forward-compatible like `last_display` (5.2), never part of the key (1.4). | high (design) |
| 9.9 | **HUD/overlay geometry from one runtime formula keyed on the display's SHORT side** `S = min(width_pt, height_pt)`: `hud_font = clamp(round(S·0.05), 18, 64) pt`; `overlay_font` default 15 pt or `auto = clamp(round(S·0.018), 12, 28)`. **A single shared `anchor_origin(visibleFrame, w, h, position, margin)` helper (the 9-position grid) places BOTH panels** — HUD via `hud.position`/`hud.margin`, overlay via `overlay.corner`/`overlay.margin`. Reposition all panels on `didChangeScreenParameters`; AppKit points → Retina is free. | Portability requirement — nothing hardcoded. Keying on the short side gives identical HUD size on the portrait 2160×3840 and the scaled 4K (both S=1080→54 pt) and a sane 48 pt on a 13" laptop; the clamp catches a native-res 4K (S=2160). One anchor helper makes HUD position configurable (Phase-3 review) and keeps HUD/overlay placement consistent. `visibleFrame` clears menu bar/notch/Dock. | high (design) / medium (exact constants — tune in Phase 6) |

**Open questions / Phase-4 & 6 follow-ups:**
- `CGEventPost` Ctrl+N actually switching Spaces under SIP-on + Accessibility — verify on hardware (Phase 6); the disable-with-reason path is the safe fallback if it doesn't.
- Friendly display-name resolution (display UUID → human name/model) — best-effort; fall back to a short UUID prefix if unavailable.
- Exact HUD `duration_ms`/fade constants and `S·0.05` coefficient are UX taste — tune live in Phase 6.
- HUD `NSScreenSaverWindowLevel` (~101) vs system alerts on Tahoe remains the §2.2 open question (Phase 6).

---

## Cross-phase impact (hand-off)

- **Phase 2 (repo layout): ✅ DONE** — see §8. Repo scaffolded (src-layout, stub modules, `pyproject.toml` with the 4 PyObjC parts + click, `spacelabel = "spacelabel.cli:main"`, ruff/mypy-strict/pytest, pre-commit, packaging, GitHub templates + macOS CI). Initial commit pushed to **private** github.com/McSim85/spacelabel. All four gates green.
- **Phase 3 (CLI/UI concepts): ✅ DONE** — see §9. Deliverables written: `docs/CLI.md` (full command spec — exit codes, stdout/stderr contract, flags, examples), `docs/UI.md` (menu-bar/dropdown/prefs/HUD-overlay spec + runtime geometry) and `docs/ui-mockup.html` (to-spec rendered mockup; hosted artifact linked in UI.md). Command tree treated as canonical (no renames; `agent`, not `run`). Tahoe visibility caveats (6.5/§6.1) and experimental-wallpaper framing (§7) baked into the copy.
- **Phase 7 (repo `CLAUDE.md`): ✅ DONE (initial)** — `CLAUDE.md` exists at the repo root: the standing brief loaded each session (core UUID invariant, architecture map, the load-bearing gotchas — §0 CGS-via-CoreGraphics, the notification-center footgun §4, wallpaper §7, switch-wall §9.5, no-hardcoded-topology §3 — conventions, `uv`/pipx commands, testing reality, identity §6.7). It is intentionally tight and links `DESIGN.md`/`DECISIONS.md` as authoritative. **Living doc — must be refreshed after Phase 4** (final commands/modules) **and Phase 6** (verified gotchas).
- **Phase 4 (code + tests):** fill the stubs under `src/spacelabel/` (every module has a docstring + `TODO(phase-4)` markers). Implement `cgs.py` per the **committed** loader (1.1–1.3) — not the Phase-1 sketches that bound SkyLight directly — raising the renamed **`CGSUnavailableError`** (8.5). Implement the data model (§5 / §7-decisions): atomic writes, `fcntl.flock`, watch/reload, prune. Honor the no-silent-except policy at every CGS/plist site. The notification-center + debounce footgun (§4) is an explicit adversarial-review target. Scaffold realities to build on: src-layout (import from the installed package); the console entry point + `agent` dispatch are confirmed working (8.3); finish `logging_setup`'s **agent** branch (RotatingFileHandler + feature-detected `os_log`); `install.py` must substitute `__HOME__` in `packaging/dev.mcsim.spacelabel.plist`, `mkdir ~/Library/Logs/spacelabel` before load, and ensure one instance; once PyObjC is actually imported, the mypy overrides become "used" (consider re-enabling `warn_unused_configs`).
  - **From Phase 3 (§9) — wire these into the implementation:** ① CLI: exit codes `0/1/2/+3-for-status` (9.1), stdout-data/stderr-diagnostics with TSV default + `--json` (9.2), and the additive flags in 9.3 (`--json`, `--dry-run`, `--active-display`, `--no-load`). ② `model.Label` gains `color: str | None = None` (9.8); `model.Config` per-mode settings must carry the new `menubar.*` keys + `overlay.font_size="auto"`/`corner`/`margin` (9.7) — `store.py` (de)serializes them, `config get/set` validates them. ③ `menubar.py` draws the buttons row as **one** custom view (alpha-marking, per-display dividers, hit-testing for the opt-in switch — 9.4/9.5). ④ `prefs.py` = two-level `NSOutlineView` with a color-well column + prune button, inline edit committing on Return **and** focus-loss (9.6). ⑤ `hud.py`/`overlay.py` consume a shared `metrics_for(display)` + `anchor_origin(visibleFrame, w, h, position, margin)` helper (9.9) — HUD position/overlay corner read from config (`hud.position`/`overlay.corner`, the 9-anchor grid); `displays.py` gains a UUID→friendly-name resolver.
  - **Refresh `CLAUDE.md` (Phase 7) at the end of this phase** — reconcile its architecture map and command list with the modules/commands as actually implemented.
- **Phase 5 (backlog):** open questions in §1/§2/§3/§6 are backlog candidates; the wallpaper durability ceiling (§7) bounds what to promise.
- **Phase 6 (verification):** run the §12 checklist of `DESIGN.md`. **Gate the whole project on item 1 (uuid reboot-stability) and item 3 (RSS-flat memory).** If `uuid` is not reboot-stable, revisit decision 1.4 (the entire UUID-keying premise). **Refresh `CLAUDE.md` (Phase 7)** with any verified gotchas / corrected assumptions from the probe.

## Completeness gate (Phase 1)
Critic verdict: **ready-with-open-questions** — design may be locked with the §0 correction baked in (done) and the data model specified (done, §5). No unresolved blockers remain for Phase 2; the load-bearing empirical assumptions (uuid stability, CF memory ownership, the live bridge round-trip) are explicitly deferred to the Phase-6 probe rather than left implicit.
