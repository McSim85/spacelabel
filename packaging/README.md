# Packaging — LaunchAgent

`dev.mcsim.spacelabel.plist` is the canonical LaunchAgent template for the
menu-bar agent. The reverse-DNS id `dev.mcsim.spacelabel` is the single source of
truth — it is the launchd `Label`, the plist filename, and the `os_log` subsystem.

The supported way to install is `spacelabel install` (it renders the template
with your absolute `$HOME`, creates `~/Library/Logs/spacelabel/`, and bootstraps
the agent). The commands below are the manual equivalents for reference and
debugging.

## Why these settings

- `LimitLoadToSessionType = Aqua` — the agent shows an `NSStatusItem`, which needs
  the per-user GUI (window-server) session. A system daemon would have none.
- `RunAtLoad` + `KeepAlive {SuccessfulExit: false}` — start at login and restart
  on crash, but a deliberate **Quit** from the menu stays stopped.
- Absolute paths only — launchd does **not** expand `~` or read `$PATH`.
- `~/Library/Logs/spacelabel/` must exist **before** first load, or launchd
  cannot open the `StandardOutPath` / `StandardErrorPath` files.

## Manual load / unload (launchctl 2.0)

`launchctl bootstrap`/`bootout` replace the deprecated `load -w`/`unload -w`.

```sh
PLIST="$HOME/Library/LaunchAgents/dev.mcsim.spacelabel.plist"

# Prepare log dir, then render the template (replace __HOME__ with your $HOME).
mkdir -p "$HOME/Library/Logs/spacelabel"
sed "s|__HOME__|$HOME|g" packaging/dev.mcsim.spacelabel.plist > "$PLIST"

# Load (start now + at every login):
launchctl bootstrap gui/$(id -u) "$PLIST"

# Restart (e.g. after upgrading):
launchctl kickstart -k gui/$(id -u)/dev.mcsim.spacelabel

# Unload:
launchctl bootout gui/$(id -u)/dev.mcsim.spacelabel

# Inspect:
launchctl print gui/$(id -u)/dev.mcsim.spacelabel
```

To apply edits to the plist, `bootout` then `bootstrap` again.

## Interpreter pinning

The pipx/uv environments are pinned to the Homebrew interpreter. After a
`brew upgrade python` minor bump, run `pipx reinstall spacelabel` (and recreate
the uv `.venv`) so the agent keeps launching.
