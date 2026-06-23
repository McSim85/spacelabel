# CLAUDE.md — spacelabel

Standing brief for every Claude Code session and contributor. Kept tight on
purpose; the authoritative depth lives in **[`DESIGN.md`](DESIGN.md)** (the *how*)
and **[`DECISIONS.md`](DECISIONS.md)** (the *why* + confidence). **Read both before
changing anything** — especially `DECISIONS.md` §0 and "Cross-phase impact". A
change that contradicts a locked decision needs a deliberate decision update, not
a quiet override.

## What this is

`spacelabel` is an open-source (MIT) macOS menu-bar + CLI tool that labels Spaces
(virtual desktops).

**Core invariant — labels are keyed by Space `uuid`, never by index/position.**
That is the whole point versus WhichSpace (which keys by position, so a reorder
shifts every label). Anywhere you key, store, or look up a label, use the
per-Space `uuid` string. `id64`/`ManagedSpaceID`/index are session-scoped and must
never become the label key (DECISIONS §1.4).

## Architecture map

`src/` layout; one package, one `click` entry point (`spacelabel = spacelabel.cli:main`).
The agent is the `spacelabel agent` subcommand; every other subcommand is a
one-shot CLI action over the same read/store layers. See `DESIGN.md` §2 for the
authoritative design.

```text
src/spacelabel/
  cli.py              # click group + main(); dispatches subcommands (heavy imports stay lazy)
  logging_setup.py    # setup_logging(mode=...) — the ONE place handlers are attached
  model.py            # dataclasses: Space, Display, Label, Config (+ Menubar/Hud/Overlay/WallpaperConfig)
  labeling.py         # PURE: title_for / pill_text / assign_ordinals / find_orphans (no I/O, no PyObjC)
  store.py            # StorePaths + labels.json/config.json: atomic flock read-modify-write, prune, config schema
  install.py          # LaunchAgent plist (built in code) install/uninstall/status via launchctl
  platform/
    cgs.py            # CGS read path + PURE parse_spaces (connection, spaces, current-space, active display)
    displays.py       # NSScreen <-> CGS display-id mapping (CGDisplayCreateUUIDFromDisplayID via ColorSync); topology
    spaces_plist.py   # com.apple.spaces.plist fallback parser (topology/UUID only; PURE parse_spaces_plist)
    notifications.py  # activeSpaceDidChange + didChangeScreenParameters + debounce
    oslog_handler.py  # OPTIONAL os_log mirror (feature-detected)
  agent/
    app.py            # NSApplication accessory app + AppDelegate + run loop (single-instance flock; 1s mtime-poll reload)
    geometry.py       # PURE: HUD/overlay font math + the nine-anchor placement grid (DESIGN §9.9)
    menubar.py menubar item    hud.py transient HUD    overlay.py corner overlay (one per display)
    wallpaper.py experimental render+set    prefs.py NSOutlineView prefs window
```

Data store: two JSON files under `~/Library/Application Support/spacelabel/`
(`labels.json`, `config.json`), atomic writes (`fcntl.flock` → temp → `os.replace`),
agent watches and reloads live. See `DESIGN.md` §7 / DECISIONS §5.

## Hard gotchas (load-bearing — get these exact)

- **No SIP disable, ever.** All CGS reads work SIP-on; the project premise dies if
  anything needs SIP off.
- **CGS binds via CoreGraphics with a per-symbol CGS→SLS fallback — NOT a SkyLight
  `dlopen`** (DECISIONS §0/§1). On Tahoe, SkyLight has no on-disk Mach-O and exports
  only `SLS*`; CoreGraphics re-exports the `CGS*` aliases. Resolve each symbol
  `CGS`-name-then-`SLS`-name; on miss raise the logged **`CGSUnavailableError`** and
  fall back to the spaces-plist parser. Never `CFRelease` a `Copy` result that
  PyObjC already owns (`already_retained`).
- **Display UUID comes from `CGDisplayCreateUUIDFromDisplayID`, bound from
  ColorSync — NOT Quartz/CoreGraphics** (Phase-4 finding, DECISIONS §3.6). On Tahoe
  PyObjC's `Quartz` does not expose it and CoreGraphics does not export it; it lives
  in `ColorSync.framework` (and ApplicationServices). `displays.display_uuid` binds
  it via `objc.loadBundleFunctions` with `already_cfretained`. This CFUUID is the
  join key across NSScreen ↔ the Spaces array ↔ the active-menubar display, so
  `active_display_uuid()` normalizes the `"Main"` sentinel through the **same**
  primary-UUID remap as `parse_spaces` (else the active-Space lookup misses).
- **Notification-center footgun:** observe `activeSpaceDidChange` on the
  **workspace** notification center (`NSWorkspace.sharedWorkspace().notificationCenter()`),
  **not** the default center; the event carries no Space identity, so **re-read the
  UUID every fire**; debounce ~200ms trailing-edge. `didChangeScreenParameters` is
  the **default** center. (DESIGN §5 / DECISIONS §4)
- **Wallpaper mode is cosmetic/best-effort** — `WallpaperAgent` self-reverts; it is
  never the source of truth, ships disabled-by-default, and you must **never edit the
  WallpaperAgent store/container plists**. (DESIGN §6.4 / DECISIONS §7)
- **Space *switching* is SIP/Dock-walled** → only via the opt-in Ctrl+N "Switch to
  Desktop N" shortcut + Accessibility, OFF by default; if it can't be confirmed,
  **disable with a visible reason, never silently no-op**. (DECISIONS §9.5 / DESIGN §6)
- **Never hardcode display/Space topology** — discover displays and Spaces at runtime;
  no hardcoded models, resolutions, UUIDs, scales, orientations, or counts. The
  reference machine is for testing only. (DESIGN §4 / DECISIONS §3)

## Conventions

- PEP 8 / 257 / 484, enforced by **ruff** + **mypy `--strict`**.
- **No silent exception handling** — never bare `except: pass`/`continue`; catch a
  specific exception, log with context, then recover or re-raise. CGS/plist read
  sites are the canonical application points. (DESIGN §8.2)
- **stdlib `logging`, never `print`.** Library code uses `getLogger(__name__)` +
  `NullHandler` and never configures handlers; only `setup_logging()` does.
- **Stdlib-first.** Only third-party deps beyond PyObjC: **`click`**. `rumps` and
  `Pillow` are rejected (DECISIONS §2) — don't reintroduce them.
- Conventional Commits (`feat:`, `fix:`, `docs:`, …). If a change revises a
  decision, update `DECISIONS.md` in the same PR.

## Commands

Dev via **`uv`** (never share an env with pipx):

```sh
uv venv
uv pip install -e '.[dev]'
pre-commit install                 # auto ruff + mypy on every commit (already set up)
uv run ruff check .                # lint
uv run ruff format --check .       # format check (use `ruff format .` to fix)
uv run mypy src                    # type-check (strict)
uv run pytest                      # tests
uv run spacelabel agent --debug    # run the agent in the foreground
```

- **pre-commit is installed** locally and mirrors the CI gates.
- **CI is macOS-only** (`macos-latest`) — PyObjC framework wheels don't install on
  Linux, so a Linux runner can't even build the package.
- **Distribute as a signed `.app` via a Homebrew cask** (DECISIONS §6.8, reverses
  pipx-only #30): `brew install --cask spacelabel`. Build the bundle with
  `tools/build_app.sh [--sign]` (py2app, build-time only — never a runtime dep); the
  agent process **is** the bundle so Accessibility keys on `dev.mcsim.spacelabel`. The
  cask `binary` stanza puts the same exe on PATH as the `spacelabel` CLI. pipx
  (`pipx install .`, `~/.local/bin/spacelabel`) is the **deprecated** legacy path,
  kept only as a dev/source convenience.

### Pre-commit checklist (required before every commit)

```sh
# 1. Run the full gate suite
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest

# 2. Run codex review in a loop until no critical findings remain
git add <changed files>
codex review "<focused prompt: changed files + what to flag: crash risks, logic errors,
  missing system-boundary handling, thread-safety; skip style/naming/missing features>"
# fix findings → re-run tests → re-stage → repeat until codex reports no critical findings

# 3. Commit
```

The `--uncommitted` flag on `codex review` conflicts with a positional prompt argument;
work around it by staging files first, then passing the prompt as a positional argument.
- CLI contract: stdout = machine-readable data (TSV default, `--json` opt-in),
  stderr = all diagnostics; exit codes `0` ok / `1` runtime / `2` usage / `3` =
  status "agent not running". (DECISIONS §9 / `docs/CLI.md`)

## Testing reality

Mocked unit tests run in CI (the suite lives in `tests/`; pure logic sits behind
functions that take plain data — `parse_spaces`, the config schema, the plist
parser, the LaunchAgent builder, the CLI with live readers monkeypatched). **Live
CGS / Spaces / GUI behavior is local-only** — it cannot run on a CI runner (no
window server, no real displays). The Phase-6 read-only probe verifies the
load-bearing empirical assumptions on hardware (uuid reboot-stability, flat RSS /
CF ownership, the PyObjC↔CFArray bridge). See **[`docs/TESTING.md`](docs/TESTING.md)**
for the mock boundary + the exact local-only commands, and `DESIGN.md` §12.

## Don'ts

- Don't relitigate locked decisions (re-read `DESIGN.md`/`DECISIONS.md` first; if you
  truly must change one, update `DECISIONS.md` deliberately).
- Don't add dependencies casually — stdlib-first; only `click` beyond PyObjC.
- Don't hardcode display/Space topology.
- Don't treat wallpaper output as durable, and don't edit the WallpaperAgent store.

## Identity

- **`dev.mcsim.spacelabel`** is the single reverse-DNS constant (LaunchAgent `Label`,
  plist filename, `os_log` subsystem) — one source of truth so a later move to a
  a future namespace rename is a one-line change. (DECISIONS §6.7)
- Repo: public at github.com/McSim85/spacelabel; MIT © Max Kramarenko.

---

> **This is a living doc.** Refresh it after **Phase 4** (final commands/modules)
> and after **Phase 6** (verified gotchas).
