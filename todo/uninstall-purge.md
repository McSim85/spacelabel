# spacelabel — `uninstall --purge` (keep config by default; opt-in deep clean)

**Recommended model:** Sonnet 4.6 · **effort:** medium. Set `/model` and `/effort` before running.
**Run in a fresh session.**

---

## Shared Baseline
- **Project:** `spacelabel` — open-source (MIT) macOS menu-bar + CLI tool that labels Spaces by **Space UUID**. pipx distribution; **PyPI deferred** — teammates install via `pipx install git+https://github.com/McSim85/spacelabel`.
- **Stack:** Python; PyObjC; `click`. No SIP. ruff + mypy `--strict` + pytest + pre-commit. Conventional Commits. CI macOS-only.
- **Hand-off rule:** read `DESIGN.md` + `DECISIONS.md` (§9 CLI contract, §6 install/runtime) and `CLAUDE.md` before acting; update `DECISIONS.md` at the end.

## Context
`spacelabel uninstall` removes the LaunchAgent but **keeps user data (labels/config) by design**; `--keep-labels` is a documented **no-op** (keeping is unconditional). Max wants the **`apt remove` vs `apt purge`** model: default keeps data; `--purge` deletes it. Neither pipx nor brew can auto-run this (no uninstall hooks), so it is a manual step run **before** `pipx/brew uninstall` — make the default output nudge toward it.

## Task — add `spacelabel uninstall [--purge] [--yes] [--dry-run]`
- **Default (= `apt remove`):** unload + remove the LaunchAgent, keep all user data (unchanged). Append a breadcrumb to the success line: `…labels and config kept; run 'spacelabel uninstall --purge' to also delete them.`
- **`--purge` (= `apt purge`):** after agent removal, delete **only** spacelabel-owned, named targets:
  - `~/Library/Application Support/spacelabel/` (from `store.data_dir()`; under a custom `--config`, delete the three known JSONs + their `.lock` siblings, **not** a user-chosen shared dir),
  - `~/Library/Caches/spacelabel/`,
  - `~/Library/Logs/spacelabel/` (`install.logs_dir()`),
  - the per-shell completion file (`completion.completion_target(shell)`).
  **Never touch:** the WallpaperAgent store/container plists, the pipx venv, the `~/.local/bin/spacelabel` shim, or the `.zshrc` `fpath` line (mention it in output for manual removal).
- **Safety:** `--purge` prompts via `click.confirm` listing the exact paths; `--yes`/`-y` skips; **non-TTY without `--yes` refuses (exit 2)**. `--dry-run` prints resolved paths to stdout and exits 0 (mirror `label prune --dry-run`). Each delete is independent/best-effort; partial failure prints what failed and exits 1; agent-removal failure still exits 1 as today.
- **Retire `--keep-labels` gracefully:** keep it accepted but `hidden=True`, and emit a stderr deprecation: `--keep-labels is the default and now a no-op; use --purge to delete data.`

## Files
- `cli.py` — flags, confirm/TTY guard, breadcrumb, hide + deprecate `--keep-labels`.
- `install.py` — a `purge_user_data(paths, *, remove_completion)` helper reusing `logs_dir()` + a shared `~/Library/Caches/spacelabel` constant.
- `completion.py` — expose per-shell completion target paths.
- Docs: `docs/CLI.md` §3.2, `README.md` uninstall section, `DECISIONS.md` §9.3 (record `--purge` superseded the reserved `--keep-labels`).
- Tests: unit-test `purge_user_data` (mock paths: dry-run lists, real deletes, partial-failure → exit 1) and the `--yes`/non-TTY guard.

## Before committing
Run the gates (`uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest`) + the **codex review loop** until no critical findings (per `CLAUDE.md`). Conventional Commit (`feat(cli): add uninstall --purge`). Mark this item `done` in `todo/README.md` when it lands.
