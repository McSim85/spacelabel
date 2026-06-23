# spacelabel — Remove pipx entirely (brew cask is the sole supported distribution)

**Recommended model:** Sonnet 4.6 · **effort:** medium (mostly mechanical; the only judgement is the `install.py` fallback policy). Set `/model` and `/effort` before running.
**Run in a fresh session.**

---

## Shared Baseline
- **Project:** `spacelabel` — open-source (MIT) macOS menu-bar + CLI tool that labels Spaces by **Space UUID** (reorder-proof vs WhichSpace).
- **Stack:** Python; PyObjC (AppKit); CGS reads via CoreGraphics. No SIP. `click`. ruff + mypy `--strict` + pytest + pre-commit. Conventional Commits. CI macOS-only.
- **Distribution (current):** signed `.app` via **Homebrew cask** (DECISIONS §6.8, reversed pipx-only #30); `brew install --cask spacelabel`. The agent process **is** the bundle (`dev.mcsim.spacelabel` TCC identity). pipx is currently kept only as a **deprecated** fallback in `install.py`.
- **Hand-off rule:** read `DESIGN.md` + `DECISIONS.md` (§6 install/runtime, §6.8 distribution) + `CLAUDE.md` first; update `DECISIONS.md` at the end.

## Context & goal
PR #32 made the Homebrew cask / signed `.app` the main install path and **demoted pipx to a legacy fallback**. Max's call (2026-06-23): **remove pipx entirely** — it is no longer a supported install path, and leaving it in the code/docs invites the exact TCC-identity confusion that motivated the pivot (the shared ad-hoc `python3.x` Accessibility mess — `todo/improvements.md` item E). The cask `binary` stanza already puts the CLI on PATH from the bundle, so pipx buys us nothing.

**Keep, do NOT remove:** the **`uv`-based dev workflow** (`uv venv` + `uv pip install -e '.[dev]'`, `uv run …`). `uv` ≠ pipx — it stays as the development/source path. A non-pipx **source/dev fallback** in `_resolve_install_shim` may stay (running the agent from a `uv` checkout for development); only the **pipx-specific** branch + the `~/.local/bin/spacelabel` shim assumptions go.

## Scope map (from a 2026-06-23 grep — re-grep before editing, counts will drift)
`grep -rIn -e 'pipx' -e '\.local/bin/spacelabel' -e 'legacy_shim' .`

- **Code (~23 hits):**
  - `src/spacelabel/install.py` (~18) — **the main work.** Remove `legacy_shim()` (the `~/.local/bin/spacelabel` path) and the pipx branch in `_resolve_install_shim`; keep only (1) the cask bundle exe and (2) an optional **source/dev** fallback. Rewrite the `InstallError`/not-found messages to point at the **cask** (`brew install --cask spacelabel`) and the dev path — drop the `pipx install` suggestion. Re-check `uninstall`/`purge` text that references the pipx venv/shim.
  - `src/spacelabel/agent/app.py` (~3), `src/spacelabel/cli.py` (~1), `src/spacelabel/agent/menubar.py` (~1) — pipx mentions in log/help/comment strings → bundle/cask wording.
- **Tests (~7):** `tests/test_install.py` — replace the pipx-shim-path tests with bundle-exe-path tests (and a dev-fallback test if that path is kept). Ensure `_resolve_install_shim` coverage matches the new policy.
- **Docs (~63):** `README.md` (install section → cask only), `docs/CLI.md`, `docs/UI.md`, `CONTRIBUTING.md`, `.github/ISSUE_TEMPLATE/bug_report.md`, `packaging/README.md` — drop pipx install instructions; show `brew install --cask spacelabel` + the `tools/build_app.sh` local build. `DESIGN.md` (~10, esp. §9.1 the pipx shim) — rewrite to the bundle path. `DECISIONS.md` (~20) — **keep the decision history** (#30 pipx-only → §6.8 pivot) but update any text that still implies pipx is a *current supported* path; add a line that pipx support was **removed** (not just deprecated) with this change.
- **Packaging / CI (~10):** `packaging/py2app/setup.py`, `packaging/py2app/launcher.py`, `packaging/dev.mcsim.spacelabel.plist`, `.github/workflows/publish.yml` — drop pipx references; the plist `ProgramArguments` must be the bundle exe (verify).
- **todo (~26):** `todo/README.md`, `todo/improvements.md` (item E workaround references the pipx venv path — mark historical), `todo/critical-release-automation.md`, `todo/uninstall-purge.md` (the `--purge`/cask `zap` targets never touched the pipx venv anyway — confirm wording), `todo/critical-click-to-switch.md`, `todo/phase-6-blockers.md` (done). Scrub stale "pipx-only / pipx fallback" language; keep genuinely historical references clearly marked as history.

## Decisions to make (record in DECISIONS.md)
1. **Keep a source/dev fallback in `_resolve_install_shim`?** Recommended **yes** (so `spacelabel install` works from a `uv` checkout during development) — just not the pipx one. State this in §6.8.
2. **`legacy_shim()` removal vs keep-as-private-helper?** Remove it; nothing should target `~/.local/bin/spacelabel` anymore.
3. Update `CLAUDE.md` "Distribute as a signed .app via a Homebrew cask" note to drop the "pipx … is the deprecated legacy path, kept as a dev/source convenience" clause (the dev convenience is `uv`, not pipx).

## Before committing
Gates (`uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest`) + the **codex review loop** until no critical findings (per `CLAUDE.md`). Conventional Commit (`refactor(install): remove the deprecated pipx path; cask-only`). Update `DECISIONS.md` §6.8 + `CLAUDE.md`; mark this item `done` in `todo/README.md`. Re-grep `pipx` afterward — only intentional history references should remain.
