# spacelabel

**Label your macOS Spaces (virtual desktops) so you always know which desktop is which — and have the label _follow_ the desktop when you reorder.**

`spacelabel` is an open-source (MIT) macOS menu-bar + CLI tool that names your
Spaces. Crucially, it keys each label by the Space's **UUID**, not its position.

> [!NOTE]
> **Status: in active development.** The core is implemented and tested — UUID-keyed
> labels, the menu-bar item + pill row, on-switch HUD, corner overlay, the CLI,
> the login LaunchAgent, and opt-in click-to-switch (verified on the reference
> machine). Broader hardware verification (Phase 6) and `.app` packaging precede a
> tagged release. See [`DESIGN.md`](DESIGN.md) and [`DECISIONS.md`](DECISIONS.md).

## Why — the reorder-proof difference

[WhichSpace](https://github.com/gechr/WhichSpace) and similar tools key labels by
a Space's **position** (Desktop 1, Desktop 2, …). The moment you reorder Spaces in
Mission Control, every label shifts to the wrong desktop.

`spacelabel` binds each label to the Space's stable **UUID**, so:

- Reorder Spaces freely — each label stays with its desktop.
- Delete and recreate a Space with the same UUID — its label re-binds.

## Features

### Display modes

All modes read the same UUID→label store; enable any combination with
`spacelabel mode <name> --on/--off`.

| Mode | What it does | Default | Durable? |
| --- | --- | --- | --- |
| **Menu-bar item** (primary) | Shows the active Space's label in the menu bar | on | yes |
| **On-switch HUD** | Brief centered banner on each Space change | on | yes |
| **Persistent corner overlay** | Always-on-top label pinned to a screen corner | off | yes |

### Menu-bar pills (buttons row)

Instead of just the active label, the menu-bar item can show a compact **pill per
Space**, grouped left-to-right by physical display (a thin divider between
displays). The current Space on each display is marked by **full opacity** (the
rest are dimmed) — never by color, so a per-label color stays free as your own tag.

```sh
spacelabel config set menubar.show_buttons_row true     # show pills instead of the title
spacelabel config set menubar.pill_label_chars 2        # 1 or 2 leading letters per pill
spacelabel config set menubar.buttons_scope active_display   # or all_displays (default)
```

### Click-to-switch (opt-in)

With the pills shown, you can **click a pill to switch to that Space**. It's
**off by default** and requires a one-time macOS setup (Accessibility + the
Mission Control "Switch to Desktop N" shortcuts — see [System Settings](#system-settings-to-grant--enable) below).

```sh
spacelabel config set menubar.click_to_switch true
```

- Because labels are **UUID-keyed**, a click resolves the Space's *live* position
  at click time — so it switches to the right desktop even after you reorder.
- **Failure is never silent.** If Accessibility isn't granted or the target's
  shortcut isn't enabled, the dropdown shows a ⚠️ "Click-to-switch off — …" row
  (click it to open the relevant System Settings pane) and the pills stop capturing
  clicks until you fix the cause and re-toggle the setting.
- **Multi-display:** a pill switches only when its Space is on the display that
  currently has focus (the menu bar). macOS only reliably switches the **focused**
  display's Space, so clicking a pill on another display shows a brief *"Click-to-switch
  only works on the focused display"* notice instead of failing silently — focus that
  display first, then click. Single-display setups are unaffected. (See [Caveats](#caveats).)
- While click-to-switch is on, the pills capture left-clicks, so open the dropdown
  menu (Preferences / Quit) with a **right-click** or a click off a pill.

## Requirements

- macOS 26 "Tahoe" or newer. The signed cask bundle is **Apple Silicon (arm64)** for
  now (a universal2 build is a follow-on); Intel users can run from source.
- **No SIP disable required.** `spacelabel` only *reads* private window-server
  state; switching Spaces uses the standard, user-enabled Mission Control shortcut.
- Python 3.11+ for a source/dev install only — the cask bundles its own `python@3.14`.

## Install

**Homebrew cask (recommended)** — ships a signed `spacelabel.app` bundle so the agent
has its own stable, named Accessibility identity (this is what makes click-to-switch's
grant work — see [`DECISIONS.md`](DECISIONS.md) §6.8):

```sh
brew tap McSim85/spacelabel https://github.com/McSim85/spacelabel
brew install --cask spacelabel
```

> Available **once the first signed `.app` release is published** and the release
> pipeline's **cask-bump PR is merged** — that PR fills the cask's `sha256`; until it
> lands, the default branch carries an all-zero placeholder and the cask won't install.
> Until then, build + install locally: `tools/build_app.sh` then copy
> `dist/spacelabel.app` to `/Applications`.

This installs `spacelabel.app` and puts the `spacelabel` CLI on your PATH (the cask
symlinks the bundle's executable). Start the menu-bar agent at login:

```sh
spacelabel install     # installs + loads the LaunchAgent (pointed at the app bundle)
spacelabel status      # check it's running
```

> **Ad-hoc signing caveat.** The bundle is ad-hoc-signed (no Apple Developer account
> yet), so on first launch Gatekeeper may block it — right-click → **Open** once, or
> `xattr -dr com.apple.quarantine /Applications/spacelabel.app`. An ad-hoc cdhash
> changes each release, so the Accessibility grant (below) must be **re-approved after
> `brew upgrade --cask spacelabel`**. Developer-ID + notarization would make both
> durable (deferred — [`DECISIONS.md`](DECISIONS.md) §6.9).

**From source (dev)** — a pure-Python wheel for hacking on it
(`uv pip install -e '.[dev]'`). The cask is the only supported distribution path;
a source install is dev-only and won't get a reliable Accessibility grant (§6.8).

## System Settings to grant / enable

Most features need nothing. Two macOS settings matter:

### For click-to-switch (only if you enable it)

1. **Accessibility** — *System Settings → Privacy & Security → Accessibility* →
   enable the **"spacelabel"** entry. The first pill click prompts for this.
   > With the **cask** (signed `.app`) the entry reads **"spacelabel"** and one grant
   > sticks — that's the whole point of the bundle (§6.8). It must be **re-granted after
   > a cask upgrade** (ad-hoc cdhash rotates, §6.9).

2. **Mission Control shortcuts** — *System Settings → Keyboard → Keyboard Shortcuts →
   Mission Control* → enable **"Switch to Desktop 1", "Switch to Desktop 2", …**.
   These ship **off**. You can switch to **as many desktops as you enable shortcuts
   for** (the default chord is `⌃` + the desktop number; verified working for all 14
   desktops on the reference machine).

### Menu-bar icon visibility (Tahoe)

"Process alive" ≠ "icon visible". If the menu-bar item doesn't appear, check
*System Settings → Control Center / Menu Bar* — macOS 26's Menu Bar controls (and
Control Center) can hide status items.

## Usage (CLI)

```text
spacelabel [--config PATH] [--verbose] [--debug] [--version]

  agent                         run the menu-bar agent in the foreground
  install                       install + load the login LaunchAgent
  uninstall [--purge]           remove the LaunchAgent; --purge also deletes data
  status                        install + run state (managed or foreground agent)
  spaces                        list current Spaces + UUIDs, mark the active one
  mode <menubar|hud|overlay> [--on/--off]
  label set <uuid|current> <text>
  label list
  label clear <uuid|current>
  label prune                   drop labels for Spaces that no longer exist
  display set <uuid|current> <name> | display list | display clear <uuid|current>
  config get <key> | config set <key> <value>
  completion install [--shell auto|zsh|bash|fish] [--dry-run]   # shell tab-completion
```

Example — label the current Space and list them all:

```sh
spacelabel label set current "Email"
spacelabel label list
```

Machine-readable output (`spaces`, `label list`) goes to **stdout** (add `--json`
for scripts); all diagnostics go to logging (stderr or a file), so stdout stays
clean for piping.

## Configuration

Settings live in `config.json` under
`~/Library/Application Support/spacelabel/`. Read/write them with
`spacelabel config get <key>` / `config set <key> <value>` (the agent live-reloads
within ~1 s — no restart needed). `config set --help` lists every key with its
constraint.

| Key | Type / values | Default | Notes |
| --- | --- | --- | --- |
| `modes.{menubar,hud,overlay}` | bool | `true,true,false` | Prefer `spacelabel mode <name> --on/--off` |
| `menubar.max_length` | int ≥ 1 | `24` | Truncate the menu-bar title |
| `menubar.show_buttons_row` | bool | `false` | Show per-Space pills instead of the title |
| `menubar.buttons_scope` | `all_displays` \| `active_display` | `all_displays` | Which displays' pills to show |
| `menubar.pill_label_chars` | 1–2 | `1` | Leading letters per pill |
| `menubar.click_to_switch` | bool | `false` | Opt-in pill-click switching (see above) |
| `hud.duration_ms` | int | `1100` | HUD banner lifetime |
| `hud.font_size` | int \| `auto` | `auto` | `auto` scales to the display's short side |
| `hud.position` | one of the nine anchors | `center` | e.g. `top-right`, `bottom`, `center` |
| `hud.margin` | int (pt) | `24` | Inset from the screen edge |
| `overlay.corner` | one of the nine anchors | `top-right` | Where the corner overlay pins |
| `overlay.margin` | int (pt) | `12` | Inset from the screen edge |
| `overlay.font_size` | int \| `auto` | `15` | |
| `overlay.bold` | bool | `true` | Draw the overlay label bold |
| `debounce_ms` | int | `200` | Coalesce rapid Space switches |
| `log_level` | `DEBUG`…`CRITICAL` | `WARNING` | Agent file-log level |

The **nine anchors** are `top-left`, `top`, `top-right`, `left`, `center`,
`right`, `bottom-left`, `bottom`, `bottom-right`.

## Caveats

- **Ad-hoc signed → grant re-prompts on upgrade.** The cask bundle is ad-hoc-signed,
  so its code-signing hash changes every release: the one-time Accessibility grant must
  be re-approved after `brew upgrade --cask spacelabel`, and first launch may need
  right-click → Open (Gatekeeper). Developer-ID signing + notarization (deferred) would
  make both durable. The bundle is **arm64-only** for now.
- **Stale-grant detection is best-effort (a heuristic).** Because of the above, after an
  upgrade an already-enabled "spacelabel" Accessibility entry is bound to the *previous*
  signature and silently stops working. `spacelabel` notices this and tells you to
  **remove and re-add** the entry (rather than the misleading "just enable it"). It's a
  heuristic: macOS won't let an app read which signature a grant is tied to (the TCC
  database is protected), so it infers from its own remembered code-signing hash. Two
  honest limits — the **first release** that shipped this feature (and a truly first-ever
  grant) can't yet tell a stale grant from a never-granted one, so it shows the plain
  "enable" message; and a grant you removed by hand may also read as "stale". In every
  case the fix is identical: remove any existing "spacelabel" entry, then re-add it.
  Developer-ID + notarization (above) would remove the need for this entirely.
- **Click-to-switch only targets the focused display.** With "Displays have separate
  Spaces" on, macOS's "Switch to Desktop N" shortcut only reliably switches the Space on
  the display that currently has the menu bar / keyboard focus; the same chord for another
  display's Space is a near-silent no-op (a macOS limitation, not a numbering bug — the
  desktop numbers do match). So spacelabel switches a pill only when its Space is on the
  active display and shows a visible "only works on the focused display" notice otherwise —
  focus that display first, then click its pill.
- **Pills don't redraw on a pure reorder (yet).** The agent refreshes on Space
  *switch* and display change, not on a Mission Control drag — so after reordering,
  the pill row looks stale until your next switch. Clicking is unaffected (it's
  UUID-keyed and resolves live), it's only the visual that lags.
- **Menu-bar visibility on Tahoe** — see [System Settings](#menu-bar-icon-visibility-tahoe) above.
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
