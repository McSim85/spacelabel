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
| Click-to-switch pills — implement the opt-in `CGEventPost` switch path | **Critical** | [critical-click-to-switch.md](critical-click-to-switch.md) | v0.2/v0.3 | 1 session · Opus 4.8 · high | done (Phase-6 verifies end-to-end switch) |
| Release automation — release-please + PyPI OIDC + Homebrew tap + Renovate | **Critical** | [critical-release-automation.md](critical-release-automation.md) | v0.2+ | 1 session · Sonnet 4.6 · medium | done |
| Per-Space notes/task queue *(superseded the per-display overlay-note design)* | Non-critical | [improvements.md](improvements.md) | v0.2 | bundled | done (v0.2.0, #17); only the optional prefs notes editor (item A.4) still open |
| Wallpaper: persist captured original across restarts | Non-critical | [improvements.md](improvements.md) | v0.3 | bundled | done (v0.3.0, #19) |
| Wallpaper: per-display font sizing | Non-critical | [improvements.md](improvements.md) | v0.3 | bundled | done (v0.3.0, #19) |
| Wallpaper: detect user wallpaper changes to refresh cached original | Non-critical | [improvements.md](improvements.md) | v0.3 | bundled | open |
| Dependency automation — Renovate for PyObjC / click / GHA / pre-commit revs | Non-critical | [improvements.md](improvements.md) | v0.2+ | bundled | done (#5, #11/#12) |
| CLI shell autocomplete — zsh/bash/fish via click's built-in completion | Non-critical | [improvements.md](improvements.md) | v0.2 | bundled | done |
| Single rotated agent log — merge stderr, one rotated file, fix double-writer | Non-critical | [improvements.md](improvements.md) | v0.2 | bundled | done |
| `install --no-run-at-load` — opt out of auto-start at login | Non-critical | [improvements.md](improvements.md) | v0.2 | bundled | open |
| Signed `.app` bundle for Accessibility/TCC identity (`python3.x` → `spacelabel`) | Non-critical | [improvements.md](improvements.md) | v1.0 | bundled (own session — relaxes 2.7/6.3) | open |
| Live pill/overlay refresh on Space reorder & create/delete | Non-critical | [improvements.md](improvements.md) | v0.2 | bundled | done |

---

## Notes

- **Phase 6 (verification)** runs the `DESIGN.md §12` hardware checklist and is a
  separate phase, not a backlog item. It gates the whole project on uuid reboot-stability
  (item 1) and flat-RSS memory (item 3).
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
