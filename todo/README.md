# spacelabel — Backlog (Phase 5 output)

Paste-ready prompt backlog. Each item below links to a self-contained prompt file
you can open as its own Claude Code session. Run Critical items first, in any
order (they are independent); fold Non-critical items into a single session using
`improvements.md`.

---

## Priority definitions

| Priority | Meaning |
|---|---|
| **Critical** | Correctness, robustness, or blocking: gates a shippable release. Ships before any user-visible release note promises the feature. |
| **Non-critical** | Polish, DX, or nice-to-have. Improves the product but does not block a release. |

---

## Index

| Item | Priority | Prompt file | Milestone | Rough effort | Status |
|---|---|---|---|---|---|
| **Phase-6 blockers & follow-ups** — signed `.app` via Homebrew cask (fixes click-to-switch + replaces pipx) + CGS→SLS fix + richer `status` | **Critical** | [phase-6-blockers.md](phase-6-blockers.md) | next | 1 session · Opus 4.8 · high | **implemented on `feat/signed-app-cask`** (2026-06-22): Tier 1 build + cask + release pipeline, Tier 2a/2b, Tier 3 — all gated green. **Only the on-hardware grant + click-to-switch test remains (Max).** |
| Click-to-switch pills — implement the opt-in `CGEventPost` switch path | **Critical** | [critical-click-to-switch.md](critical-click-to-switch.md) | v0.2/v0.3 | 1 session · Opus 4.8 · high | done — but Phase-6 found Accessibility broken on pipx (shared-python TCC identity); fix = signed `.app` (phase-6-blockers Tier 1) |
| Release automation — release-please + PyPI OIDC + **Homebrew cask** + Renovate | **Critical** | [critical-release-automation.md](critical-release-automation.md) | v0.2+ | 1 session · Sonnet 4.6 · medium | **done** — release-please + Renovate + the **cask** `build-app`/`update-cask` pipeline (build + ad-hoc sign + attach `.app`, PR the cask bump) all landed (2026-06-22 pivot, reverses #30; DECISIONS §10.3/§10.5). PyPI still deferred. **CI run untested until the first release (no push this session).** |
| Per-Space notes/task queue *(superseded the per-display overlay-note design)* | Non-critical | [improvements.md](improvements.md) | v0.2 | bundled | done (v0.2.0, #17); only the optional prefs notes editor (item A.4) still open |
| Wallpaper: persist captured original across restarts | Non-critical | [improvements.md](improvements.md) | v0.3 | bundled | done (v0.3.0, #19) |
| Wallpaper: per-display font sizing | Non-critical | [improvements.md](improvements.md) | v0.3 | bundled | done (v0.3.0, #19) |
| Wallpaper: detect user wallpaper changes to refresh cached original | Non-critical | [improvements.md](improvements.md) | v0.3 | bundled | open |
| Dependency automation — Renovate for PyObjC / click / GHA / pre-commit revs | Non-critical | [improvements.md](improvements.md) | v0.2+ | bundled | done (#5, #11/#12) |
| CLI shell autocomplete — zsh/bash/fish via click's built-in completion | Non-critical | [improvements.md](improvements.md) | v0.2 | bundled | done |
| Single rotated agent log — merge stderr, one rotated file, fix double-writer | Non-critical | [improvements.md](improvements.md) | v0.2 | bundled | done |
| `install --no-run-at-load` — opt out of auto-start at login | Non-critical | [improvements.md](improvements.md) | v0.2 | bundled | open |
| Signed `.app` bundle for Accessibility/TCC identity (`python3.x` → `spacelabel`) | **Critical** (was Non-crit) | [improvements.md](improvements.md) item E → [phase-6-blockers.md](phase-6-blockers.md) Tier 1 | next | bundled into phase-6-blockers (own session — relaxes 2.7/6.3) | **done (build)** (2026-06-22; py2app bundle builds self-contained + ad-hoc-signs as `dev.mcsim.spacelabel`, CLI+agent run from it; DECISIONS §6.8/§6.9) — **on-hardware grant + click-to-switch pending (Max)** |
| Live pill/overlay refresh on Space reorder & create/delete | Non-critical | [improvements.md](improvements.md) | v0.2 | bundled | done |
| `uninstall --purge` — keep config by default; opt-in deep clean; retire `--keep-labels` | Non-critical | [uninstall-purge.md](uninstall-purge.md) | v0.6+ | 1 session · Sonnet 4.6 · medium | **done** (2026-06-22; `--purge`/`--yes`/`--dry-run`, `--keep-labels` deprecated, cask `zap` in sync; DECISIONS §9.3) |
| CGS→SLS fallback loads wrong framework bundle (no-op on Tahoe) | Non-critical | [improvements.md](improvements.md) item H → [phase-6-blockers.md](phase-6-blockers.md) Tier 2a | next | bundled | **done** (2026-06-22; SLS fallback now resolves from a real SkyLight bundle — live-verified; DECISIONS §1.1) |
| `status` should report install + running incl. foreground agent | Non-critical | [improvements.md](improvements.md) item I → [phase-6-blockers.md](phase-6-blockers.md) Tier 2b | next | bundled | **done** (2026-06-22; `{installed,loaded,running,pid,managed}`, detects a foreground agent — live-verified; DECISIONS §9.1) |
| Menu-bar/Prefs toggle for click-to-switch | Non-critical | [improvements.md](improvements.md) item J | v0.7 | bundled | open (Max, 2026-06-22) |
| Homebrew agent-path fix — `_resolve_install_shim` resolves the **app-bundle** exe (not the pipx shim) | **Critical** | [critical-release-automation.md](critical-release-automation.md) → [phase-6-blockers.md](phase-6-blockers.md) Tier 1 step 5 | next | folded into phase-6-blockers | **done** (2026-06-22; `_enclosing_app_exe` resolves the bundle exe, pipx shim kept as deprecated fallback) |

---

## Notes

- **🔀 Distribution pivot (Max, 2026-06-22):** moving from **pipx-only** to a **signed `.app` shipped via a Homebrew cask** (reverses #30). Driven by the Phase-6 finding that click-to-switch can't get a reliable Accessibility grant under pipx's shared homebrew-python identity. Authoritative plan: **[phase-6-blockers.md](phase-6-blockers.md) Tier 1**. The formal `DECISIONS.md` update (§6/§2.7/§6.3) lands when that session ships.
- **Phase 6 (verification)** runs the `DESIGN.md §12` hardware checklist and is a
  separate phase, not a backlog item. It gates the whole project on uuid reboot-stability
  (item 1) and flat-RSS memory (item 3). Phases 1 (CGS gate) ✅ passed; the click-to-switch/distribution work is now front-loaded via [phase-6-blockers.md](phase-6-blockers.md).
- Mark a row `done` here once its session completes and the code lands on `main`.
- Update `DECISIONS.md` if any session forces a design decision change.

## Required: codex review loop before every commit

Every implementation session must run `codex review` in a loop until no critical
findings remain, **before** committing. See `CLAUDE.md` → "Pre-commit checklist"
for the exact steps. Short version:

```sh
git add <changed files>
codex review "<focused prompt: flag crash risks, logic errors, missing system-boundary
  handling, thread-safety; skip style/naming/missing features>"
# fix → re-test → re-stage → repeat until clean
```
