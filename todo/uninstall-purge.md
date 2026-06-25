# spacelabel — `uninstall --purge` (keep config by default; opt-in deep clean)

**Recommended model:** Sonnet 4.6 · **effort:** medium. Set `/model` and `/effort` before running.
**Run in a fresh session.**

> **✅ IMPLEMENTED 2026-06-22 (branch `feat/signed-app-cask`), with one deliberate deviation.** Codex review flagged that deleting the "three known JSONs" in a custom `--config`'s directory by basename could remove a foreign file if `--config` points into a shared dir. Max chose the **conservative** path (DECISIONS §9.3): `--purge` deletes only paths spacelabel *exclusively owns* — the default store dir (wholesale, default config only), the global Caches/Logs dirs, and the completion scripts. A custom `--config` purges those global dirs but **never** touches files in the `--config`'s own directory; they're left for manual removal (the command says so). Purge also **refuses while a foreground agent holds `agent.lock`**. The acceptance rows below were verified (1B.6 dry-run + 1B.7 non-TTY live; 1B.8/1B.9 real delete verified in an isolated `$HOME`).

---

## Shared Baseline
- **Project:** `spacelabel` — open-source (MIT) macOS menu-bar + CLI tool that labels Spaces by **Space UUID**. Distribution via Homebrew cask; **PyPI deferred**.
- **Stack:** Python; PyObjC; `click`. No SIP. ruff + mypy `--strict` + pytest + pre-commit. Conventional Commits. CI macOS-only.
- **Hand-off rule:** read `DESIGN.md` + `DECISIONS.md` (§9 CLI contract, §6 install/runtime) and `CLAUDE.md` before acting; update `DECISIONS.md` at the end.

## Context
`spacelabel uninstall` removes the LaunchAgent but **keeps user data (labels/config) by design**; `--keep-labels` is a documented **no-op** (keeping is unconditional). Max wants the **`apt remove` vs `apt purge`** model: default keeps data; `--purge` deletes it. `brew uninstall --cask` has no uninstall hooks so this is a manual step run **before** `brew uninstall --cask spacelabel` — make the default output nudge toward it.

## Task — add `spacelabel uninstall [--purge] [--yes] [--dry-run]`
- **Default (= `apt remove`):** unload + remove the LaunchAgent, keep all user data (unchanged). Append a breadcrumb to the success line: `…labels and config kept; run 'spacelabel uninstall --purge' to also delete them.`
- **`--purge` (= `apt purge`):** after agent removal, delete **only** spacelabel-owned, named targets:
  - `~/Library/Application Support/spacelabel/` (from `store.data_dir()`; under a custom `--config`, delete the three known JSONs + their `.lock` siblings, **not** a user-chosen shared dir),
  - `~/Library/Caches/spacelabel/`,
  - `~/Library/Logs/spacelabel/` (`install.logs_dir()`),
  - the per-shell completion file (`completion.completion_target(shell)`).
  **Never touch:** files outside spacelabel-owned paths, or the `.zshrc` `fpath` line (mention it in output for manual removal).
- **Safety:** `--purge` prompts via `click.confirm` listing the exact paths; `--yes`/`-y` skips; **non-TTY without `--yes` refuses (exit 2)**. `--dry-run` prints resolved paths to stdout and exits 0 (mirror `label prune --dry-run`). Each delete is independent/best-effort; partial failure prints what failed and exits 1; agent-removal failure still exits 1 as today.
- **Retire `--keep-labels` gracefully:** keep it accepted but `hidden=True`, and emit a stderr deprecation: `--keep-labels is the default and now a no-op; use --purge to delete data.`

## Files
- `cli.py` — flags, confirm/TTY guard, breadcrumb, hide + deprecate `--keep-labels`.
- `install.py` — a `purge_user_data(paths, *, remove_completion)` helper reusing `logs_dir()` + a shared `~/Library/Caches/spacelabel` constant.
- `completion.py` — expose per-shell completion target paths.
- Docs: `docs/CLI.md` §3.2, `README.md` uninstall section, `DECISIONS.md` §9.3 (record `--purge` superseded the reserved `--keep-labels`).
- Tests: unit-test `purge_user_data` (mock paths: dry-run lists, real deletes, partial-failure → exit 1) and the `--yes`/non-TTY guard.

## Verification (Phase-6 acceptance — RUN THESE WHEN THE FEATURE LANDS)
Deferred from Phase 6 (2026-06-22): rows 1B.5–1B.9 of `phase-6-verification.md` could not run because `--purge` did not exist (`spacelabel uninstall --purge` → `Error: No such option '--purge'.` exit 2). Run them after implementing, then mark them ✅ in `docs/VERIFICATION.md`:
- **1B.5** — `spacelabel uninstall` (no flag): same as today + breadcrumb appended: `…labels and config kept; run 'spacelabel uninstall --purge' to also delete them.`
- **1B.6** — `spacelabel uninstall --purge --dry-run`: prints the exact resolved paths to **stdout**, deletes nothing, exit 0. Paths: `~/Library/Application Support/spacelabel/`, `~/Library/Caches/spacelabel/`, `~/Library/Logs/spacelabel/`, per-shell completion file.
- **1B.7** — `spacelabel uninstall --purge` on a **non-TTY without `--yes`**: refuses, exit **2** (never deletes non-interactively).
- **1B.8** — `spacelabel uninstall --purge --yes`: removes the agent, then deletes the four targets; each delete independent/best-effort; partial failure prints what couldn't be removed and exits 1. **Never** touches files outside spacelabel-owned paths.
- **1B.9** — after 1B.8: the three `~/Library/.../spacelabel/` dirs are gone; the `.zshrc` `fpath` line (if ever added) is left in place and mentioned in output for manual removal.
- Also verify the **`--keep-labels` deprecation**: still accepted but `hidden=True`, emits stderr `--keep-labels is the default and now a no-op; use --purge to delete data.`

## Before committing
Run the gates (`uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest`) + the **codex review loop** until no critical findings (per `CLAUDE.md`). Conventional Commit (`feat(cli): add uninstall --purge`). Mark this item `done` in `todo/README.md` when it lands.
