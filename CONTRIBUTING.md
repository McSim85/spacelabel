# Contributing to spacelabel

Thanks for your interest! This is a small, focused macOS tool. Please read
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) before touching the load-bearing
invariants — a change that contradicts them needs a deliberate decision, not a quiet
override.

## Development setup

Requires macOS, [`uv`](https://docs.astral.sh/uv/), and Python 3.11+ (the
reference interpreter is Homebrew `python@3.14`).

```sh
git clone https://github.com/McSim85/spacelabel
cd spacelabel
uv venv
uv pip install -e '.[dev]'
pre-commit install            # run the lint/type gates on every commit
```

Run the tool from the dev environment:

```sh
uv run spacelabel --help
uv run spacelabel agent --debug
```

> `uv` is the dev environment. Distribution is the Homebrew cask.

## Quality gates (must pass before a PR)

These are enforced by both `pre-commit` and CI:

```sh
uv run ruff check .            # lint
uv run ruff format --check .   # format
uv run mypy src                # type-check
uv run pytest                  # tests
```

`ruff format` is the autoformatter — run `uv run ruff format .` to fix style.

## Engineering standards

- **PEP 8** style, **PEP 257** docstrings, **PEP 484** type hints — enforced by
  ruff + mypy (`strict`).
- **No silent exception handling.** Never `except: pass` / `continue`. Catch
  specific exceptions, log with context (`logging.getLogger(__name__)`), then
  recover or re-raise.
- **Prefer the standard library.** Add a third-party dependency only when it
  clearly earns its keep. The current set is PyObjC (unavoidable) + `click`.
- Library/module code never configures logging — only `setup_logging()` does.

## Testing

Mocked unit tests run in CI (macOS only — PyObjC wheels don't install on Linux).
Live CGS reads, Spaces, GUI, and LaunchAgent behavior are **local-only on a real
Mac** — a hosted runner has no window server, no displays, and cannot load a
LaunchAgent.

**Mock boundary:** keep pure logic behind functions that take plain Python data.
The live calls (CGS IPC, `NSScreen`, `NSPanel`, launchctl) are thin shells around
that logic and are not unit-tested.

| Test file | Covers (pure) | Mock boundary |
|---|---|---|
| `test_labeling.py` | title / pill / ordinal / orphan resolution | none — pure |
| `test_geometry.py` | HUD/overlay font math + nine-anchor placement | none — pure arithmetic |
| `test_store.py` | atomic locked read-modify-write, label CRUD, prune, config schema | real `tmp_path` files; no GUI |
| `test_cgs_parse.py` | `parse_spaces` — labelable filter, current-marking, "Main" remap, multi-display | mocked `CGSCopyManagedDisplaySpaces` dicts |
| `test_spaces_plist.py` | `parse_spaces_plist` — UUID extraction | mocked plist mapping |
| `test_install.py` | `build_launch_agent`/`render_plist` | pure dict/plist; no launchctl |
| `test_cli.py` | every command, exit codes 0/1/2/3, stdout=data/stderr=diagnostics | monkeypatched `cgs.*`, `displays.*`, `install.agent_status` |
| `test_smoke.py` | package import, `--help`/`--version`, locked command tree | click only |

**Local-only verification** (live behavior — cannot run in CI):

```sh
uv run spacelabel spaces                 # live CGS reads across displays
uv run spacelabel label set current "X"  # exercises read_active_space_uuid end-to-end
uv run spacelabel agent --debug          # menu-bar agent (needs a GUI session)
spacelabel install && spacelabel status  # LaunchAgent lifecycle
```

Keep new logic testable without a GUI: put pure work behind a function that takes
plain data (the way `parse_spaces` sits behind `enumerate_spaces`), and unit-test that.

## Commits & pull requests

- Use [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:` …).
- Keep PRs focused; describe what changed and why.
- Fill in the pull-request template.

## Reporting issues

Use the bug-report or feature-request templates. For bugs, include your macOS
version, whether "Displays have separate Spaces" is on/off, your display setup,
and any output from `spacelabel spaces --debug`.
