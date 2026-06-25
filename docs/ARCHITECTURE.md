# spacelabel — Architecture

Contributor essentials distilled from the design history. A change touching any of
these eight points needs careful thought; one that contradicts them needs a deliberate
decision, not a quiet override.

---

## 1. Core invariant — UUID-keyed labels

Labels are keyed by the Space's **`uuid`** string — never by index, position,
`id64`, or `ManagedSpaceID`. The volatile integers are session-scoped and can be
reassigned; only `uuid` is stable across reorders and reboots. This is the entire
point versus WhichSpace, which keys by position and shifts every label on reorder.

**Every code path that stores, reads, or compares a label must use the `uuid` string.**

## 2. CGS read path

Four private `CoreGraphics` functions, all read-only and SIP-on:

| Function | Returns |
|---|---|
| `CGSMainConnectionID()` | process-wide connection id (scalar, no release) |
| `CGSCopyManagedDisplaySpaces(cid)` | per-display Space dicts (`+1` owned) |
| `CGSManagedDisplayGetCurrentSpace(cid, displayUUID)` | live current Space id (scalar) |
| `CGSCopyActiveMenuBarDisplayIdentifier(cid)` | active display UUID (`+1` owned) |

**Binding target:** `objc.loadBundleFunctions` against `CoreGraphics`. That framework
holds the `CGS*` re-exports; on a miss fall back to the `SLS*` name from a lazily-loaded
SkyLight bundle — SkyLight exports only `SLS*` on Tahoe and has no on-disk Mach-O, but
the fallback keeps reads alive if Apple ever drops a `CGS*` alias.

**Ownership:** the two `Copy` functions are `+1` retain. Pass `{'retval': {'already_retained': True}}`
to `loadBundleFunctions` so PyObjC owns and releases the retain. **Never also `CFRelease`
a result PyObjC owns.**

**On any symbol miss:** raise `CGSUnavailableError` → fall back to
`platform/spaces_plist.py` (topology/UUID enumeration only — never a live current-Space
source, since `cfprefsd` caches it and lags ordinary switches).

**Display UUID:** `CGDisplayCreateUUIDFromDisplayID` lives in **ColorSync**, not
CoreGraphics or Quartz. Bind it via `objc.loadBundleFunctions` from `ColorSync.framework`
(ApplicationServices as a fallback) with `already_cfretained`. This CFUUID is the join
key across `NSScreen` device ids, the CGS `"Display Identifier"` strings, and
`CGSCopyActiveMenuBarDisplayIdentifier`.

## 3. Notification-center footgun

Observe `NSWorkspaceActiveSpaceDidChangeNotification` on
**`NSWorkspace.sharedWorkspace().notificationCenter()`** — **not** the default center.
The notification carries no Space identity; re-read the UUID on every fire. Debounce
~200 ms trailing-edge to coalesce rapid switches.

`NSApplicationDidChangeScreenParametersNotification` (display attach/detach) goes on the
**default** `NSNotificationCenter`. React by fully re-discovering topology; never cache
UUIDs across the event.

## 4. Signed `.app` + Accessibility/TCC

The agent must run **as the signed bundle** (`dev.mcsim.spacelabel`) for Accessibility
grants to bind. Under a shared-interpreter identity (`org.python.python`, `python3.x`),
the TCC entry never applies to the agent.

The bundle is **ad-hoc-signed**: the cdhash rotates on every release build, so the
Accessibility grant goes stale after a `brew upgrade --cask spacelabel`. The agent detects
this via `SecCodeCopySelf` + `SecCodeCopySigningInformation` (Security framework) and
tells the user to **remove and re-add** the entry — not just re-toggle the stale one.

**Click-to-switch is gated to the active (focused) display.** macOS's "Switch to Desktop N"
shortcut only reliably switches the Space on the display that currently has focus. An
off-display pill shows a visible HUD notice instead of failing silently.

## 5. Three display modes

| Mode | What it does | Default |
|---|---|---|
| **Menu-bar item** | Active Space label as `NSStatusItem` title, or a per-Space pill row | on |
| **On-switch HUD** | Transient centered banner on each Space change | on |
| **Corner overlay** | Always-on-top label pinned to a screen corner | off |

All three read the same UUID→label store. Toggle with `spacelabel mode <name> --on/--off`.

The pill row and HUD/overlay are **non-activating `NSPanel`s** — click-through, never
steal focus, float across all Spaces (`CanJoinAllSpaces | Stationary | FullScreenAuxiliary`).

Wallpaper mode was removed: it irreversibly clobbers Dynamic/Shuffle wallpapers and there
is no public API to set a non-active Space's wallpaper.

## 6. Data store

Two JSON files under `~/Library/Application Support/spacelabel/`:

- **`labels.json`** — `{ schema_version, labels: { <uuid>: { label, color?, notes?, ... } } }`
- **`config.json`** — `{ schema_version, modes, per-mode settings, debounce_ms, log_level }`

**Atomic writes:** same-directory temp file → `fcntl.flock` → `fsync` → `os.replace`
(atomic on one filesystem). The agent watches both files and reloads on change via a 1 s
poll + file-event.

Two writers exist (CLI + the prefs window); the `flock` prevents lost updates.

## 7. No SIP, never hardcode topology

All CGS reads work SIP-on — the project premise dies if anything requires SIP disabled.
Never hardcode display counts, resolutions, UUIDs, orientations, or Space counts; discover
everything at runtime via `NSScreen.screens()` and `CGSCopyManagedDisplaySpaces`.

React to display changes via `NSApplicationDidChangeScreenParametersNotification` (the
default center). `CGDisplayRegisterReconfigurationCallback` silently stopped firing on
Tahoe 26.1 — do not use it.

## 8. Distribution

**Homebrew cask** is the only supported distribution path:

```sh
brew tap McSim85/spacelabel https://github.com/McSim85/spacelabel
brew install --cask spacelabel
```

`tools/build_app.sh` builds and ad-hoc-signs a self-contained `spacelabel.app` (py2app;
embeds Python.framework + PyObjC + click). `packaging/py2app/` holds the build config.
The cask ships the prebuilt `.app` from a GitHub release asset.

`uv pip install -e '.[dev]'` is the **dev-only** path — no Accessibility grant, no signed
bundle identity. pipx is not supported.
