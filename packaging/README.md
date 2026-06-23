# Packaging — LaunchAgent

`dev.mcsim.spacelabel.plist` is the canonical LaunchAgent template for the
menu-bar agent. The reverse-DNS id `dev.mcsim.spacelabel` is the single source of
truth — it is the launchd `Label`, the plist filename, and the `os_log` subsystem.

The supported way to install is `brew install --cask spacelabel` then
`spacelabel install` — `install` renders the template with your absolute `$HOME`
**and the resolved `spacelabel.app` bundle executable** (so the agent process *is*
the bundle and Accessibility keys on `dev.mcsim.spacelabel`), creates
`~/Library/Logs/spacelabel/`, and bootstraps the agent. The commands below are the
manual equivalents for reference and debugging.

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

# Prepare log dir, then render the template (substitute __HOME__ and __APP_EXE__).
mkdir -p "$HOME/Library/Logs/spacelabel"
APP_EXE="/Applications/spacelabel.app/Contents/MacOS/spacelabel"  # cask-installed bundle exe
sed -e "s|__HOME__|$HOME|g" -e "s|__APP_EXE__|$APP_EXE|g" \
    packaging/dev.mcsim.spacelabel.plist > "$PLIST"

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

## Bundle is self-contained; ad-hoc signing caveat

The cask ships a self-contained `spacelabel.app` (it embeds its own
`Python.framework` + PyObjC + click), so a `brew upgrade python` no longer affects
the agent — there is no shared interpreter to re-pin (unlike the legacy pipx path).

The app is **ad-hoc code-signed** (`Identifier=dev.mcsim.spacelabel`, no Apple
Developer account). An ad-hoc cdhash changes on every rebuild, so the Accessibility
grant **drops on a cask upgrade/reinstall** until re-granted (System Settings →
Privacy & Security → Accessibility → re-enable "spacelabel"). Developer-ID signing +
notarization (a deferred follow-on) would make the grant durable. First-launch
downloads may also hit Gatekeeper quarantine — right-click → Open once, or
`xattr -dr com.apple.quarantine /Applications/spacelabel.app`.

> **Legacy pipx (deprecated):** the pipx/uv environments are pinned to the Homebrew
> interpreter; after a `brew upgrade python` minor bump, `pipx reinstall spacelabel`
> (and recreate the uv `.venv`). Prefer the cask.
