# spacelabel — Testing

> **Status:** written in Phase 4 (code + tests). Defines exactly what the
> automated suite covers, the **mock boundary**, and — loudly — what **cannot**
> run in CI and must be verified locally on a real Mac (the Phase-6 probe).
> Companions: [`DESIGN.md`](../DESIGN.md) §12 (verification checklist),
> [`DECISIONS.md`](../DECISIONS.md) §1/§3 (open questions).

## TL;DR

- **Unit tests run anywhere PyObjC installs (i.e. macOS), with no WindowServer,
  no displays, and no Spaces session.** They exercise the *pure* logic by mocking
  CGS output. This is what CI gates on.
- **Live CGS / Spaces / GUI / launchd behavior cannot run on a hosted CI runner**
  (GitHub's `macos-latest` has no interactive WindowServer or Spaces session).
  Those are **local-only on a real Mac** and are the Phase-6 probe.

## The four CI gates (`.github/workflows/ci.yml`, `macos-latest`)

CI must run on **macOS** — the PyObjC framework wheels are macOS-only, so a Linux
runner cannot even install the package (DECISIONS §8.4). All four gates also run
locally via `uv` and on every commit via pre-commit:

```sh
uv run ruff check .            # lint (pycodestyle, pyflakes, isort, naming, pydocstyle, annotations, …)
uv run ruff format --check .   # format
uv run mypy src                # type-check (strict; PyObjC glue scope-relaxed — see pyproject)
uv run pytest                  # unit tests (mocked; no GUI)
```

## What the unit tests cover (the mock boundary)

Everything testable **without a WindowServer** lives behind a pure function that
takes plain Python data. The live calls (CGS IPC, `NSScreen`, `NSPanel`, launchctl)
are the thin shell *around* that logic and are **not** unit-tested — they are the
Phase-6 probe.

| Test file | Covers (pure) | Mock boundary |
|---|---|---|
| `test_geometry.py` | HUD/overlay font math + nine-anchor placement (DESIGN §4.2 table) | none — pure arithmetic |
| `test_labeling.py` | title / pill / ordinal / orphan resolution | none — pure |
| `test_store.py` | atomic locked read-modify-write, label CRUD, prune, **full config schema validation** | real `tmp_path` files; no network/GUI |
| `test_cgs_parse.py` | `cgs.parse_spaces` — labelable filter, current-marking, "Main" remap, multi-display | **mocked `CGSCopyManagedDisplaySpaces` dicts** (plain Python) |
| `test_spaces_plist.py` | `spaces_plist.parse_spaces_plist` — UUID extraction, stale `is_current=False` | mocked plist mapping |
| `test_install.py` | `build_launch_agent`/`render_plist` + **packaging-template sync** | pure dict/plist; no launchctl |
| `test_cli.py` | every command, exit codes 0/1/2/3, **stdout=data / stderr=diagnostics** | **monkeypatched** `cgs.*`, `displays.*`, `install.agent_status` |
| `test_agent_imports.py` | AppKit modules import + register ObjC subclasses with **no UI built** | needs PyObjC; `importorskip` off-macOS |
| `test_smoke.py` | package import, `--help`/`--version`, locked command tree | click only |

**Why the boundary is drawn here:** the CGS read path returns a bridged
`CFArray`/`CFDictionary`; the moment it is converted to native Python (in
`cgs.enumerate_spaces`), all further logic — filtering special Spaces, remapping
the `"Main"` sentinel, marking the current Space, resolving labels — is pure and
unit-tested via `parse_spaces`. The same split applies to the plist parser, the
config schema, the LaunchAgent plist builder, and the CLI (which is tested with
the live readers monkeypatched).

## What CANNOT run in CI — local-only on a real Mac (Phase-6 probe)

A hosted runner has **no interactive WindowServer, no displays, no Spaces, no GUI
login session, and cannot load a LaunchAgent**. The following are verified by
running on Max's Mac (macOS 26.5.1, arm64, SIP on) and are the substance of the
Phase-6 probe (DESIGN §12):

```sh
# Live CGS reads (read-only; safe). Confirms symbol resolution, the PyObjC<->CFArray
# bridge, dict-key correctness, the "Main" sentinel, and the NSScreen<->UUID join key:
uv run spacelabel spaces                 # lists live Spaces across displays, marks current
uv run spacelabel spaces --json
uv run spacelabel label set current "X"  # exercises read_active_space_uuid end-to-end
uv run spacelabel label prune --dry-run

# The agent (needs a GUI session — menu bar, HUD, overlay, prefs window):
uv run spacelabel agent --debug          # Ctrl-C / menu Quit to stop

# LaunchAgent lifecycle (needs gui/$UID, writes ~/Library/LaunchAgents):
spacelabel install
spacelabel status                        # 0 running / 3 not running
spacelabel uninstall
```

**The load-bearing things only a real Mac can confirm** (gate the project on the
first two — DECISIONS "Cross-phase impact / Phase 6"):

1. 🔑 **`uuid` reboot-stability** — label a Space, **reboot**, confirm the same
   `uuid` returns and the label re-binds (cheap proxies first: match live CGS uuid
   to the on-disk plist; `killall -HUP WindowServer`). If false, the entire
   UUID-keying premise (DECISIONS 1.4) must be revisited.
2. **Flat RSS / CF ownership** — run the CGS read in a tight loop and watch RSS;
   flat == the `already_retained`/`already_cfretained` Copy-result annotation is
   correct (no leak, no over-release crash). DESIGN §12 item 3.
3. Symbol resolution under the `CGS` names (and the `SLS` fallback) on 26.5.1;
   the `NSScreen.mainScreen()` active-display fallback when
   `CGSCopyActiveMenuBarDisplayIdentifier` is forced absent.
4. The **notification-center** behavior: `activeSpaceDidChange` actually fires on
   the workspace center and the ~200 ms debounce coalesces real rapid switches;
   `didChangeScreenParameters` fires on display attach/detach.
5. Menu-bar **icon visibility** on Tahoe (Settings → Menu Bar; no ControlCenter
   negotiation loop; single instance); HUD level vs system alerts; panels never
   steal focus and float across all Spaces.
6. **Wallpaper** revert/flicker timing (experimental; observational only).

> **Status (Phase 4, on the reference machine):** the live read path was already
> exercised during implementation — `spacelabel spaces` returns 14 real Space
> UUIDs across the two displays with the current one marked, `label set current`
> resolves and stores the live active Space, and the PyObjC↔CFArray bridge
> round-trips. The reboot-stability and flat-RSS gates remain for Phase 6.

## Notes for contributors

- Keep new logic **testable without a GUI**: put pure work behind a function that
  takes plain data (the way `parse_spaces` sits behind `enumerate_spaces`), and
  unit-test that. Reserve the thin live shell for the Phase-6 probe.
- `test_agent_imports.py` is the cheap regression that the AppKit modules still
  import and register their selectors; it builds **no** UI.
- The CLI parsing contract (stdout = data, stderr = diagnostics) is asserted with
  `result.stdout` vs `result.stderr` in `test_cli.py` — preserve it for any new
  command (a header or note must never land on stdout).
