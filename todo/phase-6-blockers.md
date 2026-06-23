# spacelabel — Phase-6 blockers & follow-ups (clear these, then finish Phase 6)

**Recommended model:** Opus 4.8 · **effort:** high (Tier 1, the signed-app work, is the gnarly part; Tier 2 fixes are Sonnet/medium-sized). Set `/model` and `/effort` before running.
**Run in a fresh session.** These items surfaced during Phase-6 verification (results in `docs/VERIFICATION.md`, on branch `docs/phase-6-verification`) and block finishing it. Do **Tier 1 first** (it's the live blocker), then Tier 2 (quick), then Tier 3 (optional/deferred). After each item, **record the result back in `docs/VERIFICATION.md`** so Phase 6 can be completed in the resumed session.

---

## Shared Baseline
- **Project:** `spacelabel` — open-source (MIT) macOS menu-bar + CLI tool that labels Spaces by **Space UUID** (reorder-proof vs WhichSpace). pipx distribution; **PyPI deferred**.
- **Stack:** Python; PyObjC (AppKit); CGS reads bound via CoreGraphics (CGS→SLS fallback). No SIP. `click`. ruff + mypy `--strict` + pytest + pre-commit. Conventional Commits. CI macOS-only.
- **Portability:** discover displays/Spaces at runtime; never hardcode topology.
- **Identity:** `dev.mcsim.spacelabel` is the single reverse-DNS constant (LaunchAgent Label, plist filename, os_log subsystem) — reuse it, don't fork it.
- **Hand-off rule:** read `DESIGN.md` + `DECISIONS.md` (esp. §0, §1, §2.7, §6, §9) + `CLAUDE.md` before acting; update `DECISIONS.md` at the end; mark each item `done` in `todo/README.md`.

---

## Tier 1 — Distribute as a signed `.app` via a Homebrew cask (replaces pipx; fixes click-to-switch)  *(THE live blocker)*

**Decision (Max, 2026-06-22):** **pivot distribution from pipx-only to a signed `.app` shipped via a Homebrew cask.** This both (a) fixes click-to-switch by giving the agent its own stable, *named* TCC identity, and (b) replaces pipx as the install path. **This reverses the pipx-only decision (#30)** — make the `DECISIONS.md` update deliberately (see step 8). Start **ad-hoc** signed (no Apple Developer account needed) for this machine; Developer-ID + notarization is a follow-on for public-tap distribution.

**Why brew alone is NOT the fix (read this):** the click-to-switch failure is a TCC *identity* problem, not a packaging one. On pipx the agent runs as the **shared, ad-hoc-signed Homebrew Python app-stub** `…/Python.app/Contents/MacOS/Python` (id `org.python.python`, cdhash `b4955ea0…`); verified `AXIsProcessTrusted()` returns **False from a fresh process of that exact binary** even with "python3.14" enabled — so the grant never binds to the agent and **relaunch doesn't help** (multiple "python3.14" identities collide; the CLI stub `bin/python3.14` is a *different* binary, id `python3-5555…`, cdhash `f740…`). A brew **formula** that builds from source would run the agent as that same homebrew python → same broken identity. **Only a signed `.app` bundle (its own cdhash + `CFBundleIdentifier`) fixes it; the cask is just how we ship that `.app`.** spacelabel's own logic is correct (disables with a visible reason, never a silent no-op — DECISIONS §9.5). Full repro + cdhashes + interim workaround: `todo/improvements.md` item E.

**Goal:** the agent process *is* a `spacelabel.app` bundle (`CFBundleIdentifier=dev.mcsim.spacelabel`), installed via `brew install --cask spacelabel`, so that (a) Accessibility shows a named **"spacelabel"** entry (no python collision), (b) granting it once makes `AXIsProcessTrusted()` return **True**, (c) click-to-switch posts the chord, and (d) the `spacelabel` CLI is still on PATH (exposed from the bundle).

**Read first:** `todo/improvements.md` item E; `todo/critical-release-automation.md` (release pipeline — was pipx-only, now extend to build/sign/attach the `.app` + publish the cask); `DECISIONS.md` §2.7 (accessory, no bundle today), §6 / §6.3 (install/runtime, no codesign today, residual TCC risk), §9.5 (click-to-switch); `src/spacelabel/install.py` (LaunchAgent `ProgramArguments`/plist builder + the **`_resolve_install_shim`** path — must resolve the cask-installed app exe, not the hardcoded pipx shim); `src/spacelabel/agent/app.py` + `src/spacelabel/platform/switching.py` (the AX path).

**Steps:**
1. **Uninstall the pipx install first** (avoid two competing agents/identities): `spacelabel uninstall` (unload the LaunchAgent), then `pipx uninstall spacelabel`. Confirm `~/.local/bin/spacelabel` is gone. (User data under `~/Library/Application Support/spacelabel/` is kept.)
2. **Build `spacelabel.app`** — py2app, or a hand-rolled bundle whose `Contents/MacOS/<exe>` bootstraps the interpreter so the **process is the bundle**. `Info.plist`: `CFBundleName=spacelabel`, `CFBundleIdentifier=dev.mcsim.spacelabel` (reuse the constant), `LSUIElement=true` (accessory, no Dock — preserve current behavior). Build via `uv`; add **no** new runtime deps (py2app is build-time only). Expose the **CLI** from inside the bundle (e.g. `Contents/MacOS/spacelabel`) so the cask `binary` stanza can symlink it onto PATH — one bundle, one identity for both agent and CLI.
   - **App icon (generate at build time, macOS built-ins only):** from a **1024×1024 master PNG** produce `Contents/Resources/spacelabel.icns` — emit the iconset sizes with `sips` (16/32/128/256/512 @1x+@2x) into a `spacelabel.iconset/`, then `iconutil -c icns spacelabel.iconset -o spacelabel.icns`; reference it via `CFBundleIconFile` in `Info.plist` and include it in the bundle (py2app's `iconfile=` if using py2app). This icon shows in Finder/the cask **and as the named "spacelabel" icon in the Accessibility list** (a real upgrade over the generic python rocket). The menu-bar status-item glyph stays separate (the `square.dashed` SF Symbol / pills — DESIGN §6). If no master art exists yet, generate a simple placeholder at build time (e.g. a rounded-rect badge with the app initial via Core Graphics/`sips`) and check in the master so real artwork can replace it later with no code change. Keep the icon-build script in the repo (`tools/` or a `make`/`uv run` task) and wire it into the release pipeline (step 6).
3. **Ad-hoc codesign:** `codesign --force --deep --sign - --identifier dev.mcsim.spacelabel spacelabel.app`; confirm `codesign -dvvv` shows `Identifier=dev.mcsim.spacelabel`, `Signature=adhoc`.
4. **Homebrew cask (in-repo self-tap):** add `Casks/spacelabel.rb` — `url` → a GitHub-release asset (zipped/dmg'd `spacelabel.app`), `app "spacelabel.app"`, a `binary` stanza exposing the CLI on PATH, and a **`zap trash:`** stanza listing the four `~/Library/.../spacelabel` paths (so `brew uninstall --zap` matches `uninstall --purge` — the one place a package manager *can* clean up; ties into `todo/uninstall-purge.md`).
5. **Repoint the LaunchAgent** `ProgramArguments` at the cask-installed `spacelabel.app/Contents/MacOS/<exe>` (keep the `dev.mcsim.spacelabel` Label + plist invariants: `RunAtLoad`, `KeepAlive{SuccessfulExit:False}`, `LimitLoadToSessionType=Aqua`). Fix `_resolve_install_shim` to resolve the bundle exe, not the pipx shim.
6. **Release automation (GitHub Actions, macOS runner — CI can do all of this):**
   - **Ad-hoc signing is fully automatic in CI** — no Apple account, no secrets, no cost (free `macos-latest`/arm64 runner on the public repo). The job runs `codesign --force --sign - …` then builds the icon (step 2), zips the `.app`, attaches it to the GitHub Release, computes `sha256`, and bumps `Casks/spacelabel.rb` (`version`/`url`/`sha256`) — mirror the quiknode-labs optic/ssh-mcp update-formula job, driven by release-please.
   - **Sign inside-out**, not `--deep`: a py2app bundle embeds `Python.framework` + dylibs; sign every nested binary first, then the outer bundle. (`--deep` is tolerable for ad-hoc but wrong for notarization.)
   - **Developer-ID + notarization = deferred follow-on** (friction-free public installs + durable grant). It's automatable in CI but needs the paid **Apple Developer Program ($99/yr)** + secrets (Developer-ID Application `.p12`+password imported into a temp keychain via e.g. `apple-actions/import-codesign-certs`, plus an app-specific password / ASC API key), then `codesign --options runtime --timestamp` → `xcrun notarytool submit` → `xcrun stapler staple`. Note it, don't block on it.
   - **Ad-hoc caveats to document:** downloaders hit Gatekeeper quarantine (right-click→Open / `xattr -dr com.apple.quarantine`), and the cdhash changes each build so the Accessibility grant drops on upgrade until re-granted.
7. **Verify on hardware:** `brew install --cask <tap>/spacelabel` → `spacelabel install` → relaunch agent → the Accessibility prompt/list now reads **"spacelabel"** → grant → `AXIsProcessTrusted()` returns True (verify with the one-liner in item E) → clicking a pill switches Spaces. Confirm the `spacelabel` CLI still works from PATH.
8. **DECISIONS update (deliberate — reverses #30):** record the pivot in `DECISIONS.md` §6 (distribution = signed `.app` via Homebrew cask; pipx deprecated), §2.7/§6.3 (bundle + ad-hoc codesign shipped; Developer-ID/notarization deferred), and reconcile `todo/critical-release-automation.md` (no longer "pipx-only / brew deferred").
9. **Document the caveat:** an ad-hoc cdhash changes on every rebuild → the Accessibility grant **drops on reinstall/upgrade** until re-granted (Developer-ID would make it durable; deferred). README / `docs/UI.md`.
10. **Then re-run Phase-6 rows B18–B26 + H4–H6** and Part-1 install rows under the cask, and mark them in `docs/VERIFICATION.md`.

Commit(s): `feat(packaging): ship signed .app bundle`, `feat(dist): Homebrew cask + release pipeline`, `fix(install): resolve agent path from the app bundle`.

---

## Tier 2 — Quick correctness / UX fixes

### 2a. CGS→SLS fallback loads the wrong framework bundle  *(improvements.md item H)*
**Essence (verified):** `cgs._load()` resolves `SLS*` names against the **CoreGraphics** bundle, but on Tahoe 26.5.1 CoreGraphics exports only `CGS*` and SkyLight exports only `SLS*` — so the documented per-symbol fallback is a **no-op**. No functional impact today (CGS resolves; plist parser is the real safety net) but it contradicts `DECISIONS.md` §1.1.
**Fix:** when the `CGS` name misses against CoreGraphics, try the `SLS` name against a separately-loaded **SkyLight** bundle (`objc.loadBundle("SkyLight", …)`); cache both bundles. **Or** rewrite DECISIONS §1.1 to state the plist is the real fallback and drop the dead SLS attempt. Add a unit test (CGS forced absent → SLS resolves from SkyLight; total miss → `CGSUnavailableError`).
Commit: `fix(cgs): resolve the SLS fallback from SkyLight, not CoreGraphics`.

### 2b. `status` should report install + running (incl. a foreground agent)  *(improvements.md item I + Max)*
**Essence:** `spacelabel status` only parses `launchctl`, so a foreground `spacelabel agent` (dev/debug) reads `not running` even while it holds `agent.lock`. Max wants status to show **install state** (plist present/loaded) **and** **running state** (any agent, managed or foreground).
**Fix:** detect a running agent via a non-blocking `flock` on `agent.lock` (the same lock `app.py` uses); report `{installed, loaded, running, pid, managed}` in `--json`; define + document the exit-code contract for the new states (`DECISIONS.md` §9, `docs/CLI.md`); update Phase-6 rows **E16–E20** expectations to match. Tests for: managed-running, foreground-running, installed-not-loaded, not-installed.
Commit: `feat(cli): richer status — install + running state, incl. unmanaged agent`.

---

## Tier 3 — Deferred features (optional this session; already fully specced)

### 3a. `uninstall --purge` (apt remove vs purge)
Full spec already written in **`todo/uninstall-purge.md`** — including the Phase-6 acceptance matrix (rows **1B.5–1B.9**) to run when it lands. Implement it here if you want to clear those rows; otherwise it stays deferred. **Note:** the cask `zap` stanza (Tier 1 step 4) is the package-manager equivalent and should target the **same** four paths — keep them in sync.

*(The former "Homebrew deferred" item is now the headline — see Tier 1. The `_resolve_install_shim` fix it depended on is folded into Tier 1 step 5.)*

---

## After completing each item
1. Gates: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest`.
2. **codex review loop** until no critical findings (stage files first, pass the prompt positionally — `--uncommitted` conflicts with it; per `CLAUDE.md`).
3. Conventional-Commit; update `DECISIONS.md` if a decision changed; mark the item `done` in `todo/README.md`.
4. **Record the outcome in `docs/VERIFICATION.md`** (flip the deferred rows) so the resumed Phase-6 session can finish.
