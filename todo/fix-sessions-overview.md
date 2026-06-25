# spacelabel — Phase-6 fix sessions (parallel work plan)

Phase-6 verification (`docs/VERIFICATION.md`, verdict **PASS**) opened backlog items **K–Z** (full diagnoses in [`improvements.md`](improvements.md)). This file splits them into **self-contained, parallelizable session prompts** so they can be worked in **separate Claude Code sessions at the same time**. Each `fix-*.md` is a paste-ready prompt; open one per session.

> **Convention:** the full diagnosis + fix options + acceptance for each item already live in `improvements.md` (items K–Z). The `fix-*.md` files are thin **session drivers** — they reference those items and add run/parallelization/acceptance scaffolding. Read the referenced `improvements.md` item(s) first.

## The units

| Prompt file | Items | Effort / model | Source files it touches |
|---|---|---|---|
| ✅ [fix-multidisplay-ordinal.md](fix-multidisplay-ordinal.md) | **O + V** *(done 2026-06-24)* | **high** · Opus 4.8 | `labeling.py` `switching.py` `app.py` `prefs.py` (not `menubar.py`) |
| ✅ [fix-stale-accessibility-grant.md](fix-stale-accessibility-grant.md) | **L** *(done 2026-06-24)* | **high** · Opus 4.8 | `switching.py` `app.py` `store.py` |
| ✅ [fix-overlay-behavior.md](fix-overlay-behavior.md) | **Z + P + Q** *(done 2026-06-24)* | medium · Sonnet 4.6 | `overlay.py` `app.py` `store.py` `prefs.py` |
| ✅ [fix-prefs-menubar-ux.md](fix-prefs-menubar-ux.md) | **T + U + W + J** *(done 2026-06-25)* | medium · Sonnet 4.6 | `prefs.py` `app.py` `menubar.py` |
| ✅ [fix-cli-polish.md](fix-cli-polish.md) | **K + M + N + Y** *(done 2026-06-25)* | low · Sonnet 4.6 | `cli.py` `packaging/py2app/launcher.py` |
| [wallpaper-redesign.md](wallpaper-redesign.md) | **R + S** | **high (design)** · Opus 4.8 | `wallpaper.py` |
| [remove-pipx.md](remove-pipx.md) | — | medium · Sonnet 4.6 | `install.py` tests docs |
| **[fix-brew-upgrade-race.md](fix-brew-upgrade-race.md)** | **AB** *(critical)* | low · Sonnet 4.6 | `app.py` `Casks/spacelabel.rb` |

## ⚠ Parallelization plan — `app.py` is the bottleneck

Four UI units (**O+V, L, Z+P+Q, T+U+W**) all edit **`app.py`** (different methods, but the same file) → running them at the exact same time will produce merge conflicts there.

- **Track B — run fully in parallel (no shared files):** **fix-cli-polish** (`cli.py`/launcher), **wallpaper-redesign** (`wallpaper.py`), **remove-pipx** (`install.py`). These don't touch `app.py`/`prefs.py`/`overlay.py`/`switching.py` — safe to run simultaneously with each other and with one Track-A unit.
- **Track A — `app.py`-heavy: sequence, or parallelize with rebase discipline.** Suggested order (functional first; each rebases on the prior so `app.py` conflicts stay trivial):
  1. ✅ **fix-multidisplay-ordinal** (O+V) — the headline functional bug. **(done 2026-06-24, branch `fix/multidisplay-ordinal`)**
  2. **fix-stale-accessibility-grant** (L) — rebase on O+V (the next Track-A unit).
  3. **fix-overlay-behavior** (Z+P+Q).
  4. **fix-prefs-menubar-ux** (T+U+W).
  If you do run Track-A units truly in parallel: keep each diff small/focused, **rebase on `main` before opening each PR**, and merge them one at a time (resolve the small `app.py` overlap on the trailing PRs).

**Practical recommendation:** start **Track B (3 sessions) + the first Track-A unit (O+V)** in parallel now — four sessions, zero `app.py` contention. Pick up the rest of Track A as those merge.

## Session prompt template (paste into each session; replace `<FIX_FILE>`)

```
Set /model and /effort to the values in the header of todo/<FIX_FILE>.

Execute todo/<FIX_FILE> step by step.

Before starting, read: DESIGN.md, DECISIONS.md, CLAUDE.md,
todo/fix-sessions-overview.md, and the improvements.md item(s) that prompt
references. Then fetch + merge origin/main and cut a fresh branch off main
use a git worktree. If this is a
Track-A unit (touches app.py), rebase on origin/main before opening the PR.

Keep the diff small and focused. Before every commit run the gates —
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
— then run the codex review loop until no critical findings remain.

Ask me before committing or pushing; use Conventional Commits. When it lands,
mark the item(s) done in todo/improvements.md, tick the row in
todo/fix-sessions-overview.md, and record the result in docs/VERIFICATION.md.
When you finish (PR opened) or get blocked needing my input, DM me on Slack
via curlbot (see the standing rules) — done/blocked only, not routine progress.

Ask me questions, if any.
```

Model/effort per prompt header: Opus 4.8/high for `fix-multidisplay-ordinal`, `fix-stale-accessibility-grant`, `wallpaper-redesign`; Sonnet 4.6/medium for the rest.

## How each session runs (standing rules — in every `fix-*.md` too)
- **Fresh session, fresh branch off latest `main`** — `git fetch origin && git checkout main && git merge --ff-only origin/main`, then branch (per [[feedback_pull_main_first]]). Consider a git **worktree** per parallel session so they don't share a checkout.
- Read `DESIGN.md` + `DECISIONS.md` + `CLAUDE.md` + the referenced `improvements.md` item(s) before coding.
- Gates: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest`.
- **codex review loop** until no critical findings before every commit (per `CLAUDE.md` / [[feedback_codex_review_loop]]).
- Conventional Commits; **ask Max before committing/pushing**; update `DECISIONS.md` if a decision changes.
- On landing: mark the item(s) **done** in `improvements.md`, tick the row here, and record the result in `docs/VERIFICATION.md` (it flips the corresponding ❌/⏳ rows to ✅).
- **Ping Max when you finish or get blocked** (lightweight "don't make me babysit" signal — scoped to these sessions, no global hook): when the task is done (PR opened) or you hit something that needs Max's input, send a Slack DM via **`curlbot -m "<prompt name>: <PR #NN ready / blocked on X>"`** — Max's Slack notifier (DMs `maksim@quiknode.io`; resolves to `/Users/mc-sim/.virtualenvs/curlbot/bin/python3 /usr/local/bin/curlbot` — use the full path if the alias isn't available non-interactively). Keep it to **done / blocked / needs-a-decision** — don't ping for routine progress.

## Not in these prompts (deferred — need a restart/hardware window, not parallel code work)
Literal **reboot** (uuid gate final confirm), **H16** ("Displays have separate Spaces" OFF → logout), **Part 2 §7** (detach-4K). Run during a natural restart; capture snippet in `docs/VERIFICATION.md`.
