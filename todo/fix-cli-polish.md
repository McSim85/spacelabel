# Fix — CLI help/output polish  (items K + M + N + Y)

**Model:** Sonnet 4.6 · **effort:** low (one batched session). **Fresh session + fresh branch off latest `main`.** Part of the Phase-6 fix set — see [`fix-sessions-overview.md`](fix-sessions-overview.md). **Track B — independent of `app.py`, safe to run fully in parallel with the other sessions.**

## Items (full diagnosis in `improvements.md`)
- **K** — `--help` shows `Usage: launcher.py …` instead of `spacelabel` under the cask (launcher invokes the click group without `prog_name`).
- **M** — `status --help` leaks raw RST/markdown markup + internal refs (`**…**`, `` ``…`` ``, `*…*`, `DECISIONS.md §9`, `per review F3`). Rewrite the docstring to clean plain text; move internal refs to a code comment; **audit every command docstring**.
- **N** — colorize `status` **output** (running→green, not-running→yellow) via `click.style`, TTY-gated + `NO_COLOR`-aware (the `spaces`/`display list` pattern). **Do NOT** colorize `--help` (would need `rich-click` — rejected per DECISIONS §2).
- **Y** — `NO_COLOR=1 spacelabel spaces` still colors on a TTY; the table color helper (`cli.py:~80–89`) gates on `isatty` but not `NO_COLOR`. Honor `NO_COLOR` (the logging sink already does, `logging_setup.py:36`).

## Read first
`packaging/py2app/launcher.py` (K), `src/spacelabel/cli.py` (`status` docstring + the bold/green color helper `:~80`, M/N/Y), `improvements.md` items K/M/N/Y, the CLI contract DECISIONS §9 / plan A4/A11/A18.

## Acceptance
`spacelabel --help` usage line says `spacelabel` (both cask + dev); no command's `--help` contains `**`/double-backtick/internal-ref strings (add the **parametrized acceptance test** Max asked for in `tests/test_cli.py`); `status` output is colored on a TTY and **plain when piped or `NO_COLOR=1`** (test both); `*` current marker always present.

## Parallelization
Touches only `cli.py` + `packaging/py2app/launcher.py` (+ maybe a test) — **no `app.py`**. Fully parallel-safe.

## Before committing
Gates + **codex review loop** until clean. Conventional Commit (`fix(cli): prog_name, status help, color + NO_COLOR`). Ask before commit/push. Mark K/M/N/Y done in `improvements.md`, tick the overview, update `docs/VERIFICATION.md` (A4/A18/§D).
