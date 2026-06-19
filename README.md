# spacelabel

**Label your macOS Spaces (virtual desktops) so you always know which desktop is which — and have the label _follow_ the desktop when you reorder.**

`spacelabel` is an open-source (MIT) macOS menu-bar + CLI tool that names your
Spaces. Crucially, it keys each label by the Space's **UUID**, not its position.

> [!NOTE]
> **Status: pre-alpha scaffold.** The repository structure, packaging, and CLI
> surface are in place; the application logic is implemented in later phases. See
> [`DESIGN.md`](DESIGN.md) and [`DECISIONS.md`](DECISIONS.md).

## Why — the reorder-proof difference

[WhichSpace](https://github.com/gechr/WhichSpace) and similar tools key labels by
a Space's **position** (Desktop 1, Desktop 2, …). The moment you reorder Spaces in
Mission Control, every label shifts to the wrong desktop.

`spacelabel` binds each label to the Space's stable **UUID**, so:

- Reorder Spaces freely — each label stays with its desktop.
- Delete and recreate a Space with the same UUID — its label re-binds.

## Display modes

All modes read the same UUID→label store; enable any combination.

| Mode | What it does | Durable? |
| --- | --- | --- |
| **Menu-bar item** (primary) | Shows the active Space's label in the menu bar | yes |
| **On-switch HUD** | Brief centered banner on each Space change | yes |
| **Persistent corner overlay** | Always-on-top label pinned to a screen corner | yes |
| **Wallpaper** (experimental) | Renders the label onto the desktop image | **no — best-effort** |

## Requirements

- macOS 26 "Tahoe" or newer (Apple Silicon or Intel).
- **No SIP disable required.** `spacelabel` only *reads* private window-server
  state; it changes nothing that needs elevated privileges.
- Python 3.11+ (a Homebrew `python@3.14` is the reference interpreter).

## Install (via pipx)

```sh
pipx install .
# or, once published:  pipx install spacelabel
```

This installs a single `spacelabel` command at `~/.local/bin/spacelabel`.

Start the menu-bar agent at login:

```sh
spacelabel install     # installs + loads the LaunchAgent
spacelabel status      # check it's running
```

## Usage

```text
spacelabel [--config PATH] [--verbose] [--debug] [--version]

  agent                         run the menu-bar agent in the foreground
  install | uninstall           manage the login LaunchAgent
  status                        is the agent / LaunchAgent running?
  spaces                        list current Spaces + UUIDs, mark the active one
  mode <menubar|hud|overlay|wallpaper> [--on/--off]
  label set <uuid|current> <text>
  label list
  label clear <uuid|current>
  label prune                   drop labels for Spaces that no longer exist
  config get <key> | config set <key> <value>
```

Example — label the current Space and list them all:

```sh
spacelabel label set current "Email"
spacelabel label list
```

Machine-readable output (`spaces`, `label list`) goes to **stdout**; all
diagnostics go to logging (stderr or a file), so scripts can parse stdout cleanly.

## Caveats

- **Wallpaper mode is experimental and best-effort.** macOS exposes no per-Space
  wallpaper API, and a system `WallpaperAgent` may revert or flicker programmatic
  changes. It ships disabled by default.
- **Menu-bar visibility on Tahoe:** "process alive" ≠ "icon visible". macOS 26's
  Menu Bar settings (and Control Center) can hide status items. If the icon is
  missing, check **System Settings → Control Center / Menu Bar**.
- The tool relies on private CoreGraphics-Services (CGS) reads that Apple may
  change between point releases; `spacelabel` resolves symbols defensively and
  falls back to parsing the Spaces preferences when needed.

## Development

See [`CONTRIBUTING.md`](CONTRIBUTING.md). In short:

```sh
uv venv
uv pip install -e '.[dev]'
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
```

## License

MIT © Max Kramarenko. See [`LICENSE`](LICENSE).
