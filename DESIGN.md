# spacelabel — Technical Design

> **Status:** Locked at end of Phase 1 (deep research + adversarial verification).
> **Target:** macOS 26 "Tahoe" and forward; Apple Silicon + Intel; SIP enabled; no SIP disable.
> **Authoritative companion:** see [`DECISIONS.md`](./DECISIONS.md) for every decision with rationale, confidence, and open questions. This file is the *how*; `DECISIONS.md` is the *why + how-sure*.

Phase-1 research was conducted with a 7-track parallel workflow plus two adversarial verifiers and a completeness critic; the riskiest two claims (Python→CGS reads on Tahoe, and a pipx menu-bar agent under launchd) were **empirically confirmed on the reference machine** (macOS 26.5.1, arm64, SIP enabled). Where a claim is design-only / inferred it is marked and pushed to the Phase-6 probe.

---

## 1. What spacelabel is

An open-source (MIT) macOS menu-bar + CLI tool that **labels virtual desktops (Spaces)**, keyed by **Space UUID**. Because the label is bound to the Space's UUID — not its position — the label follows the desktop through any reorder. This is the core differentiator over WhichSpace, which keys by position and so shifts every label when you reorder Spaces.

Four display modes, all reading the same UUID→label store:

| Mode | What it does | Durable? |
|---|---|---|
| **menu-bar item** (primary) | Shows the active Space's label as an `NSStatusItem` title | yes |
| **on-switch HUD** | Brief centered banner on each Space change | yes |
| **persistent corner overlay** | Always-on-top label pinned to a screen corner | yes |

---

## 2. Architecture overview

A single Python package, `spacelabel`, exposes **one** console entry point (`spacelabel`, a `click` group). The long-lived menu-bar agent is the `spacelabel agent` subcommand; every other subcommand is a one-shot CLI action sharing the same read/store layers.

```text
spacelabel/
  cli.py              # click group; `main()` entry point; dispatches subcommands
  logging_setup.py    # setup_logging(mode=...) — the ONE place handlers are attached
  platform/
    cgs.py            # CGS read path: connection, managed-display-spaces, current-space, active display
    displays.py       # NSScreen <-> CGS display-id mapping; topology discovery
    spaces_plist.py   # ~/Library/Preferences/com.apple.spaces.plist fallback parser
    notifications.py  # activeSpaceDidChange (workspace center) + didChangeScreenParameters (default center) + debounce
    oslog_handler.py  # OPTIONAL os_log mirror (feature-detected)
  store.py            # labels.json + config.json: atomic read/modify/write, watch/reload
  agent/
    app.py            # NSApplication accessory app + AppDelegate + run loop
    menubar.py        # NSStatusItem surface
    hud.py            # transient HUD NSPanel
    overlay.py        # persistent corner NSPanel
    prefs.py          # NSTableView preferences window
  install.py          # LaunchAgent plist install/uninstall via launchctl
  model.py            # dataclasses: Space, Display, Label, Config
```

> Phase 2 owns the final repo layout (`pyproject.toml`, packaging, CI). The tree above is the design intent, not a binding directory contract.

**Data flow (agent):**

```text
NSWorkspace activeSpaceDidChange ─┐
                                   ├─► debounce (~200ms, trailing edge) ─► cgs.read_active_space_uuid()
NSApp didChangeScreenParameters ──┘                                          │
        │                                                                    ▼
        └─► displays.refresh() (rebuild NSScreen<->UUID map)        store.label_for(uuid)
                                                                             │
                                          ┌──────────────┬──────┴───────┐
                                          ▼              ▼              ▼
                                     menubar.set    hud.show      overlay.set
```

The CGS reads run off the AppKit main thread (they are pure WindowServer IPC); UI updates are marshalled back to the main thread.

---

## 3. The CGS read path (the core, highest risk)

### 3.1 What we read and from where

Four private CoreGraphics-Services (CGS) functions, all read-only, all working **SIP-on**:

| Function | Returns | Ownership |
|---|---|---|
| `CGSMainConnectionID()` | `uint32` process-wide connection id | scalar — no release |
| `CGSCopyManagedDisplaySpaces(cid)` | `CFArrayRef` of per-display dicts | **Copy → +1, release once** |
| `CGSManagedDisplayGetCurrentSpace(cid, CFStringRef displayUUID)` | `uint64` current Space id for a display | scalar — no release |
| `CGSCopyActiveMenuBarDisplayIdentifier(cid)` | `CFStringRef` UUID of the menu-bar-owning (active) display | **Copy → +1, release once** |

### 3.2 ⚠️ Binding target — corrected by on-device verification

The Shared-Baseline phrase *"ctypes dlopen of SkyLight"* is **factually imprecise on Tahoe** and must be read as corrected below (see `DECISIONS.md` → "Baseline correction"):

- On macOS 26.5.1 the **SkyLight binary has no on-disk Mach-O** (it lives only in the dyld shared cache; the framework file is a broken symlink, `nm` returns nothing).
- SkyLight exports these symbols **only under the `SLS*` prefix** (`SLSMainConnectionID`, `SLSCopyManagedDisplaySpaces`, …) — **zero `CGS*` exports**.
- **`CoreGraphics.framework` re-exports** all four under the legacy `CGS*` names (verified: `[re-export] _CGSMainConnectionID (_SLSMainConnectionID from SkyLight)`).
- A `ctypes.CDLL` from arm64 Homebrew Python 3.14, SIP enabled, **no entitlement**, successfully dlopened CoreGraphics / ApplicationServices / SkyLight and resolved all four `CGS*` names live (addresses confirmed via `dladdr` to be SkyLight-owned `SLS*` implementations). Library validation is **out of scope for Apple-signed system frameworks**, and the Homebrew interpreter is adhoc/non-hardened anyway.

**Therefore: bind against `CoreGraphics` (it holds the `CGS*` re-exports directly), and resolve each symbol with a `CGS`-then-`SLS` fallback so the binding survives a future point release that drops the `CGS` alias while keeping `SLS`.** If both are missing, raise a specific logged `CGSUnavailableError` and fall back to the `com.apple.spaces.plist` parser.

### 3.3 Committed read pattern — PyObjC `loadBundleFunctions`

We commit to **one** pattern (not a menu): PyObjC's `objc.loadBundleFunctions` against the CoreGraphics bundle, annotating the two Copy functions as ownership-transferring so PyObjC balances the `+1` retain automatically. This is the only precedent (`cameron-simpson/css` 2024, `drussell23/JARVIS` 2025) that *provably* manages CF ownership for a long-lived poller, and it auto-bridges the `CFArray`→`NSArray` so there is no manual CoreFoundation plumbing (fewer silent-bug surfaces, per the no-silent-except standard). PyObjC is already a locked dependency, so this adds nothing.

```python
# platform/cgs.py  — committed primary loader (design sketch; finalized + RSS-validated in Phase 6)
import logging
import objc

log = logging.getLogger(__name__)


class CGSUnavailableError(RuntimeError):
    """A required CGS/SLS symbol could not be resolved (e.g. renamed on a new macOS)."""


# ObjC type encodings: i = int (CGSConnectionID, 32-bit), Q = unsigned long long
# (CGSSpaceID, 64-bit), @ = object (CFArray/CFString auto-bridged to NS*).
# already_retained:True tells PyObjC the Copy() result is +1-owned, so it releases it.
_FUNCS = [
    # (cgs_name, sls_name, signature, metadata)
    ("CGSMainConnectionID", "SLSMainConnectionID", b"i", None),
    ("CGSCopyManagedDisplaySpaces", "SLSCopyManagedDisplaySpaces",
     b"@i", {"retval": {"already_retained": True}}),
    ("CGSManagedDisplayGetCurrentSpace", "SLSManagedDisplayGetCurrentSpace",
     b"Qi@", None),
    ("CGSCopyActiveMenuBarDisplayIdentifier", "SLSCopyActiveMenuBarDisplayIdentifier",
     b"@i", {"retval": {"already_retained": True}}),
]

_NS: dict = {}


def _load() -> dict:
    bundle = objc.loadBundle("CoreGraphics", {}, bundle_identifier="com.apple.CoreGraphics")
    resolved: dict = {}
    for cgs_name, sls_name, sig, meta in _FUNCS:
        for name in (cgs_name, sls_name):  # CGS alias first, SLS implementation as fallback
            spec = (name, sig) if meta is None else (name, sig, "", meta)
            missing = objc.loadBundleFunctions(bundle, resolved, [spec])
            if not missing:        # resolved[name] now callable
                resolved[cgs_name] = resolved.get(name)   # normalise to the CGS key
                break
        else:
            raise CGSUnavailableError(f"neither {cgs_name} nor {sls_name} resolved from CoreGraphics")
    return resolved


def connection() -> int:
    return _NS["CGSMainConnectionID"]()
```

**Memory contract:** `CGSCopyManagedDisplaySpaces` and `CGSCopyActiveMenuBarDisplayIdentifier` follow the CF Create/Copy rule (returned object is `+1`, caller releases exactly once). The `already_retained:True` annotation hands that `+1` to PyObjC, which releases on GC — never also manually `CFRelease`. The scalar functions own nothing. *(Phase-6 RSS-watch must confirm flat memory across thousands of switches, and confirm `already_retained` vs `already_cfretained` is the correct key for this PyObjC build — this is the one unverified piece of the read path.)*

**Documented fallback** (debugging, or a PyObjC build where the annotation misbehaves): `ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")` with `restype=c_uint32` (connection), `c_void_p` (Copy results), `c_uint64` (`CGSManagedDisplayGetCurrentSpace`); bridge a Copy result with `objc.objc_object(c_void_p=...)`, **deep-convert to native Python, then `CFRelease` the original pointer exactly once.** Pin widths to the AltTab-proven arm64 values (`CGSConnectionID=uint32`, `CGSSpaceID=uint64`).

### 3.4 Returned structure (verified 4 ways)

`CGSCopyManagedDisplaySpaces` → `CFArray` of one dict per managed display:

```text
display dict:
  "Display Identifier"  -> CFString display UUID  (or the literal "Main", see §3.5)
  "Current Space"       -> space dict             (topology-time; for LIVE current use §3.6)
  "Spaces"              -> CFArray of space dicts
space dict:
  "uuid"            -> CFString   ← the STABLE per-Space key spacelabel labels on
  "id64"            -> uint64     session-scoped managed id (AltTab keys on this)
  "ManagedSpaceID"  -> uint64     same value, alternate key (Phoenix keys on this)
  "type"            -> int        0 == ordinary user desktop
  "TileLayoutManager" (key present) -> the Space is a fullscreen/tiled app Space
```

- **Label key = `uuid`** (string), not `id64`/`ManagedSpaceID`. `uuid` is what macOS persists in `com.apple.spaces.plist`, making it the durable cross-reorder (and presumed cross-reboot) key. `id64` is session-scoped and can be reassigned. *(Reboot stability of `uuid` is the #1 Phase-6 must-verify — see §12.)*
- **Labelable Space test:** `type == 0`, no `"TileLayoutManager"` key, **and** a non-empty real-UUID `uuid`. Do **not** hardcode the fullscreen `type` constant (sources disagree: CGSInternal says `1`, Phoenix says `4`); treat any non-zero `type` or presence of `TileLayoutManager` as a special Space to skip. The `"Main"`/header rows carry an **empty `uuid`** (`''`) — confirmed on the reference machine — and special spaces can use literal non-UUID strings (e.g. `dashboard`); skip both.

### 3.5 The `"Main"` sentinel

When *System Settings → Desktop & Dock → "Displays have separate Spaces"* is **off**, `"Display Identifier"` is the literal string `"Main"` instead of a UUID. Remap it to the primary display's UUID (§4). Defensively treat **any** non-UUID-parseable identifier as needing remap, not just `"Main"`.

> ⚠️ Tahoe regression: with that setting **off**, early macOS 26 crashes WindowServer at login (Apple bug 151570422). We never toggle it; we only read whatever topology exists. On the reference machine it is on (separate Spaces per display).

### 3.6 Resolving the *active* Space

1. `active_display_uuid = CGSCopyActiveMenuBarDisplayIdentifier(cid)` → the display that owns the menu bar.
   - **Fallback** (symbol single-sourced from AltTab; if it's ever missing): use `NSScreen.mainScreen()`'s CFUUID (§4).
2. `sid = CGSManagedDisplayGetCurrentSpace(cid, active_display_uuid)` → the **live** current Space id (the dict's `"Current Space"` can lag right after a switch, and the plist definitely lags — always use the live call for "which Space now").
3. Map `sid` → `uuid` by matching `id64`/`ManagedSpaceID` in that display's `"Spaces"` list.

---

## 4. Display topology & generality

Nothing is keyed to a model, resolution, UUID, or Space count — all discovered at runtime, recomputed on every display change.

- **Enumerate** via `NSScreen.screens()`. Per screen: `deviceDescription()["NSScreenNumber"]` → `CGDirectDisplayID`; `frame` → arrangement (origin) + resolution + orientation (`portrait if h > w`); `backingScaleFactor()` → Retina scale.
- **NSScreen ↔ CGS display id (load-bearing correlation, verified):**
  `NSScreenNumber → CGDirectDisplayID → CGDisplayCreateUUIDFromDisplayID → CFUUIDCreateString` yields the **same** CFString that `CGSCopyManagedDisplaySpaces` uses as `"Display Identifier"` and that `CGSCopyActiveMenuBarDisplayIdentifier` returns.
  > Linking caveat: `CGDisplayCreateUUIDFromDisplayID` physically moved to **ColorSync.framework** in 10.13 (still re-exported via the CoreGraphics umbrella). Reach it through PyObjC's `Quartz` (umbrella), not a bare path-based dlopen of just CoreGraphics.
- **Attach/detach:** observe `NSApplicationDidChangeScreenParametersNotification` on the **default** `NSNotificationCenter` (this is an app-level notification), then **fully re-discover** topology and rebuild the screen↔UUID map. **Never cache** UUIDs/counts across the event.
  > Do **not** use `CGDisplayRegisterReconfigurationCallback` — it silently stopped firing on Tahoe 26.1.
- **Space count is discovered, never assumed.** Apple documents a soft cap — *"You can create up to 16 spaces"* ([mac-help mh14112](https://support.apple.com/guide/mac-help/mh14112/mac), current on the macOS 26 Tahoe page). It is a Mission Control UI limit, and Apple does **not** state whether 16 is per-display or total (the reference machine shows 14 on a single display, hinting per-display). Use it as **sanity/UI context only — never a hardcoded limit** (the portability rule stands; we always enumerate dynamically): it confirms the prefs `NSTableView` needs no row virtualization, and a parsed count wildly above it can be **logged as a parse-sanity warning**, not treated as an error or a ceiling.

```python
# platform/displays.py  (design sketch)
from AppKit import NSScreen
import Quartz
from CoreFoundation import CFUUIDCreateString

def display_uuid(cg_display_id: int) -> str | None:
    cf_uuid = Quartz.CGDisplayCreateUUIDFromDisplayID(cg_display_id)
    if cf_uuid is None:
        return None
    return CFUUIDCreateString(None, cf_uuid)

def discover_topology() -> list[dict]:
    out = []
    for s in NSScreen.screens():
        cg_id = s.deviceDescription().get("NSScreenNumber")
        if cg_id is None:
            continue                       # guard: never assume present
        f = s.frame()
        out.append({
            "cg_display_id": int(cg_id),
            "uuid": display_uuid(int(cg_id)),
            "origin": (f.origin.x, f.origin.y),
            "size_pt": (f.size.width, f.size.height),
            "scale": s.backingScaleFactor(),
            "orientation": "portrait" if f.size.height > f.size.width else "landscape",
        })
    return out
```

---

## 5. Space-change observation & debounce

- Observe `NSWorkspaceActiveSpaceDidChangeNotification` on `NSWorkspace.sharedWorkspace().notificationCenter()` — **not** the default center. The notification **carries no Space identity**; re-read the UUID every fire.
- **Debounce (trailing edge, ~200ms):** rapid Space switching is the common case. Coalesce a burst of notifications and re-read the CGS path **once** after quiescence. Mechanism: cancel-and-reschedule a single timer (`NSTimer` invalidated/rescheduled on each fire, or a trailing-edge dispatch). The debounced callback does the off-main CGS read, then marshals the UI update (menu-bar/HUD/overlay) back to the main thread.
- This is the "notification-center footgun" Phase 4 must get right: wrong center → no events; no debounce → thrash.
- **Hybrid: events + a 1 s liveness poll (DECISIONS §4.3).** A Mission Control **reorder** fires neither `activeSpaceDidChange` (active Space unchanged) nor `didChangeScreenParameters`, so the existing 1 s `_poll_reload` also reads a cheap live CGS **topology signature** (ordered `(display_uuid, uuid, is_current)` tuples) and refreshes when it differs from the last tick — catching reorder, create, and delete uniformly. Live CGS only (the plist can't show reorder, §3.4); an unreadable tick is skipped so a transient hiccup never spuriously refreshes.

---

## 6. Display modes

All agent windows run under `NSApplicationActivationPolicyAccessory` (set in code at `applicationDidFinishLaunching_`; no Dock icon, no app menu, no `LSUIElement` plist needed for the pipx path). HUD and overlay must **never steal focus**.

### 6.1 Menu-bar item (primary) — raw `NSStatusItem` (no `rumps`)
`NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)`; set `button().setTitle_(label)`; attach an `NSMenu` (Preferences…, Quit). Update the title from the debounced space-change callback. **`rumps` is rejected** — it is a thin wrapper over these same ~15 calls, would take over `NSApplication`/run-loop ownership (colliding with our delegate + workspace observer), and is a packaging trap under pipx (sdist-only, declares no PyObjC dep). See `DECISIONS.md`.

> **Tahoe menu-bar robustness:** "process alive" ≠ "icon visible". macOS 26 added *System Settings → Menu Bar* visibility controls that can hide the item, and there's a documented `NSStatusItemChangeVisibilityAction` negotiation loop with ControlCenter (BetterDisplay #5314, worsened by two instances). Mitigations: **run exactly one instance** (only `RunAtLoad`, no second auto-start), don't hardcode contrast against a solid bar (Tahoe's Liquid-Glass menu bar is transparent by default), surface a non-menu-bar fallback (log/notification), and document the Settings check.

### 6.2 On-switch HUD — transient non-activating `NSPanel`
Borderless `NSPanel` with `NSWindowStyleMaskNonactivatingPanel`; `level = NSScreenSaverWindowLevel` (≈101, wins for its ~1s lifetime); `collectionBehavior = CanJoinAllSpaces | Stationary | FullScreenAuxiliary`; `ignoresMouseEvents_(True)`; override `canBecomeKeyWindow`/`canBecomeMainWindow` → `False`; show with `orderFrontRegardless()` (never `makeKeyAndOrderFront`); fade via `animator().setAlphaValue_`; auto-dismiss timer. Reuse a single panel instance across switches.

### 6.3 Persistent corner overlay — always-on-top `NSPanel`
Same non-activating, click-through, all-Spaces config as the HUD, but `level = NSStatusWindowLevel` (25 — above apps, below menus/popups, the polite always-on-top tier). Pin to a corner of the active screen's `visibleFrame()` (avoids menu bar/Dock); reposition on `didChangeScreenParameters`. Corner + margin are config-driven.

### 6.4 Wallpaper mode — removed, see DECISIONS §7
The experimental wallpaper mode (capture the desktop image → composite the label →
set it) was **removed** (2026-06-25): unfixable on Dynamic/Shuffle/per-Space setups,
no public API for per-Space wallpaper, and HUD + overlay already cover the need. Full
rationale in DECISIONS §7.5.

---

## 7. Data model

> No research track designed this (it was the critique's top gap); it is specified here in full as a Phase-1 deliverable. Two JSON files under **`~/Library/Application Support/spacelabel/`** (`os.makedirs(..., exist_ok=True)` on first write). JSON per the task spec (the stray `config.toml` in one CLI sketch is superseded — both files are JSON).

### 7.1 `labels.json` — the UUID→label store (the project's whole value)

```json
{
  "schema_version": 1,
  "labels": {
    "6622AC87-2FD2-48E8-934D-F6EB303AC9BA": {
      "label": "Email",
      "last_display": "6FBB92D9-84CE-8D20-C114-3B1052DD9529",
      "created_at": "2026-06-19T12:00:00Z",
      "updated_at": "2026-06-19T12:00:00Z"
    }
  }
}
```
- Key = per-Space `uuid` string. Only `label` is required per entry; `last_display`/timestamps are informational and forward-compatible.
- `schema_version` gates migrations.

### 7.2 `config.json` — mode toggles + settings

```json
{
  "schema_version": 1,
  "modes": { "menubar": true, "hud": true, "overlay": false },
  "menubar":  { "max_length": 24 },
  "hud":      { "duration_ms": 1100, "font_size": 42 },
  "overlay":  { "corner": "top-right", "margin": 12, "font_size": 15, "bold": true },
  "debounce_ms": 200,
  "log_level": "WARNING"
}
```

> `overlay.bold` draws the corner label (its title) in the bold system font.
> (The `wallpaper` mode and its `wallpaper.*` config were removed — DECISIONS §7.)

### 7.3 Persistence mechanics
- **Atomic writes:** write to a sibling temp file in the same directory, `flush`+`os.fsync`, then `os.replace(tmp, target)` (atomic on the same filesystem). Readers therefore never observe a partial file.
- **Concurrency:** the **CLI** (`label set/clear`, `mode`, `config set`) and the **agent's preferences window** are both potential writers. Each write is a **read-modify-write under an advisory `fcntl.flock`** on `labels.lock` / `config.lock`, then atomic replace. Contention is human-paced, so this is ample.
- **Agent reload:** the agent **watches** `labels.json`/`config.json` (kqueue/`DispatchSource` on the file, re-arming on the atomic-replace delete event — the pattern WhichSpace uses for the spaces plist) and reloads on change, so a `spacelabel label set …` from the CLI is reflected live without a restart.
- **Orphaned UUIDs:** **retain by default** (a reorder never orphans; and if a Space is deleted and later recreated with the same UUID the label re-binds). Because cross-reboot UUID stability is unverified (§12), we never auto-delete. Provide an explicit `spacelabel label prune` to drop labels whose UUID is absent from the current Spaces set, and `label clear <uuid>` for a single removal.

---

## 8. CLI surface & logging

### 8.1 CLI — `click` (final call; rejected `argparse`)
The command surface is a genuine nested tree, exactly the case click cites argparse as failing. click earns its third-party slot under the stdlib-first rule by removing hand-rolled dispatch/validation, giving `Choice`/`Path` types, auto `--help`, and a parent→child `Context`. Surface:

```text
spacelabel [--config PATH] [--verbose] [--debug] [--version]
  agent                         start the menu-bar agent in the foreground (what the LaunchAgent runs)
  install | uninstall           manage the login LaunchAgent
  status                        is the agent / LaunchAgent running?
  spaces                        list current Spaces + UUIDs, mark the active one   (data → stdout)
  mode <menubar|hud|overlay> [--on/--off]
  label set <uuid|current> <text>
  label list
  label clear <uuid|current>
  label prune                   drop labels for Spaces that no longer exist
  config get <key> | config set <key> <value>
```
Machine-readable output (`spaces`, `label list`) goes to **stdout** via `click.echo`; all diagnostics go to logging (stderr/file), so scripts can parse stdout cleanly.

### 8.2 Logging — stdlib `logging` only
- Library/module code: `logging.getLogger(__name__)`, **never** adds handlers or calls `basicConfig`. The top package gets a single `NullHandler` at import.
- `setup_logging(mode=...)` is the **one** configurator, called once at the entry point:
  - **CLI:** `StreamHandler(stderr)` at `WARNING`, → `INFO` with `--verbose`, → `DEBUG` with `--debug`.
  - **agent:** quiet (`WARNING+`) `RotatingFileHandler` under `~/Library/Logs/spacelabel/` (Console.app-visible, no SIP/privilege needs), no stdout chatter; optional `os_log` mirror, **feature-detected** (not load-bearing — its PyObjC import path on Tahoe is unverified).
  - `propagate = False`; clear non-Null handlers on re-entry (cli→agent) to avoid double-logging.
- **No-silent-except policy (enforced):** `except <Specific> as exc: logger.exception("context… (uuid=%s)", uuid)` then **recover** (logged fallback) or **re-raise** — never bare `except: pass`/`continue`. CGS/plist read sites are the canonical application points.

---

## 9. Install & runtime model

### 9.1 pipx (distribution) — confirmed end-to-end on the reference machine
- One package, one `console_scripts` entry point `spacelabel = "spacelabel.cli:main"`; pipx puts the shim at `~/.local/bin/spacelabel` (a symlink into `~/.local/pipx/venvs/spacelabel`).
- **Dependencies:** `pyobjc-core`, `pyobjc-framework-Cocoa`, `pyobjc-framework-Quartz`, `pyobjc-framework-CoreText`, `click`. All PyObjC parts ship **`cp314 universal2` (arm64) wheels** (verified `pyobjc-core 12.2.1`), so pipx installs **without a compiler**. SkyLight/CGS is loaded at runtime — **never** a PyPI dep. `rumps` is **not** a dependency (sdist-only, declares no deps, stale).
- **Library validation:** the Homebrew interpreter is **adhoc-signed, non-hardened** (verified `flags=0x2(adhoc)`, no `0x10000`), so LV is not enforced and `ctypes`/PyObjC can dlopen Apple-signed private frameworks with **no entitlement and no SIP change**. (Holds only while the interpreter stays non-hardened; a hardened/notarized py2app bundle would re-engage LV.)

### 9.2 LaunchAgent (login) — confirmed: agent runs in the Aqua GUI session
`~/Library/LaunchAgents/dev.mcsim.spacelabel.plist`, loaded into the per-user GUI domain. **The reverse-DNS id `dev.mcsim.spacelabel` is the single source of truth** — reused verbatim as the LaunchAgent `Label`, the plist filename, and the `os_log` subsystem (§8.2) — so a future namespace rename is a one-constant change. Repo starts at **github.com/McSim85**.

```xml
<key>Label</key>                  <string>dev.mcsim.spacelabel</string>
<key>ProgramArguments</key>       <array>
  <string>/Users/<you>/.local/bin/spacelabel</string>   <!-- ABSOLUTE: launchd does not expand ~ or read $PATH -->
  <string>agent</string>
</array>
<key>LimitLoadToSessionType</key> <string>Aqua</string>   <!-- REQUIRED: window-server access for NSStatusItem -->
<key>RunAtLoad</key>              <true/>
<key>KeepAlive</key>              <dict><key>SuccessfulExit</key><false/></dict>  <!-- restart on crash only; a menu Quit stays stopped -->
<key>ProcessType</key>           <string>Interactive</string>
<key>StandardOutPath</key>        <string>/Users/<you>/Library/Logs/spacelabel/agent.boot.log</string>
<key>StandardErrorPath</key>      <string>/Users/<you>/Library/Logs/spacelabel/agent.boot.log</string>
```
- `launchctl print gui/$UID` on the reference machine reports `session = Aqua` — a daemon (system domain) would have **no** window-server access and the status item would never appear.
- Manage with **launchctl 2.0** (not the deprecated `load -w`): `launchctl bootstrap gui/$(id -u) "$PLIST"`; `launchctl kickstart -k gui/$(id -u)/dev.mcsim.spacelabel`; `launchctl bootout gui/$(id -u)/dev.mcsim.spacelabel` to unload (bootout → bootstrap to apply edits).
- **`install.py` must:** template the real `$HOME` into the absolute paths; `mkdir -p ~/Library/Logs/spacelabel` **before** first load (else launchd can't open the log paths); ensure exactly one instance.
- **Single rotated log + a bounded boot-catch file (one managed log; no double-writer):** `agent.log` is owned **solely** by the `RotatingFileHandler` (1 MB × 4) — launchd does **not** also write it. Both launchd streams (`StandardOutPath` *and* `StandardErrorPath`) go to a separate **`agent.boot.log`**, a near-empty safety net for catastrophic output that can't reach the logger (interpreter/import failure before `setup_logging`). `run_agent` (a) installs a `sys.excepthook` (`logging_setup.install_logging_excepthook()`) so uncaught tracebacks are logged at CRITICAL into the rotated `agent.log` rather than raw stderr, and (b) **right after winning the single-instance lock and before the crash-prone agent/config setup**, calls `logging_setup.truncate_boot_log()` which truncates `agent.boot.log` **in place** (launchd holds it open as fd 1 & 2, so never rename) once it exceeds 256 KB. Order matters: AGENT logging is configured first so the single-instance rejection lands in the rotated `agent.log`; then the lock (a rejected duplicate exits without truncating); then `truncate_boot_log()` runs before the AppKit loop so each `KeepAlive` restart bounds the file even if startup later crashes (the only inherent residual is a crash *before* `run_agent` runs, e.g. an import error, which no in-process code can catch). **Managed-run gating:** the boot-log truncation, the plist refresh, and the startup hard-exit happen **only for the production login agent** — `_is_managed_run` requires default config (no `--config`), no `--verbose`/`--debug`, **and no controlling TTY** (a LaunchAgent's std streams are files; a manual `spacelabel agent` from a shell is a TTY → not managed). The TTY check is a safe launchd proxy: unlike an env var, a wrong assumption can't silently disable the migration for the real agent (a LaunchAgent reliably has no TTY). A `--config` or foreground/dev run never zeroes the real agent's boot log, never rewrites the user's installed plist, and on a startup failure stashes the error + stops the run loop so `run_agent` re-raises it from a normal frame (a plain `raise` inside the callback would be swallowed by PyObjC) — a clean terminal traceback + non-zero exit with normal cleanup, not `os._exit`. **Upgrade path:** because an already-installed plist keeps its old paths until refreshed, (a) `truncate_boot_log()` also caps the legacy `agent.err.log` (and the agent re-caps the boot log every ~60 s of the poll, so recurring AppKit stderr can't grow it unbounded mid-session — not just at startup), and (b) `install.refresh_plist_if_stale()` (managed run only) rewrites a stale on-disk plist **atomically** (temp→`os.replace`), patching **only** the std-stream paths so `ProgramArguments` (incl. any `--config`/extra args) and other customizations are preserved, so the new paths/single-writer apply on the **next login** — it deliberately does **not** bootout/bootstrap the running job (self-reloading a live login agent is fragile); the legacy files stay capped meanwhile. The old `StandardOutPath=agent.log` on a stale plist is left to the `RotatingFileHandler` (which owns and size-bounds `agent.log`; the agent writes nothing to raw stdout) — truncating it would discard the handler's logs — and the double-writer ends once the refreshed plist loads. **Startup vs steady-state callback failures** (PyObjC swallows exceptions at the callback boundary, so they never reach `sys.excepthook`): **startup** (`applicationDidFinishLaunching_`) logs to `agent.log` then **fails fast** (`os._exit(1)`) so launchd restarts the agent and releases the lock — swallowing it would wedge the agent (un-initialized, holding the lock, launchd wouldn't restart). **Steady-state** (`_refresh`, which is event-driven — space switch / display change / store edit, not a fixed tick) is guarded to log an unexpected error at CRITICAL into `agent.log` and keep the last-good surfaces: PyObjC won't let it crash for a launchd restart anyway, and `os._exit` over one transient refresh glitch would be worse (restart-loop risk); the next event retries, and the rotated `agent.log` bounds any recurrence. Top-level (non-callback) crashes route to `agent.log` via `sys.excepthook`. PyObjC 12.2.1 exposes no global callback-exception hook, so any callback exception *outside* these guarded paths still lands in the bounded `agent.boot.log` (a documented limit, not silent). This supersedes the earlier split `agent.log`/`agent.err.log` (which had two writers on `agent.log` and an unbounded `agent.err.log`). (A `newsyslog` drop-in was rejected: `~/Library/newsyslog.d/` does not exist on Tahoe and `/etc/newsyslog.d/` needs root — see DECISIONS 2.6.)
- Interpreter pinning caveat: the pipx/uv venvs are pinned to Homebrew `python@3.14`; after a `brew upgrade python` minor bump, `pipx reinstall spacelabel` (and recreate the uv `.venv`).

### 9.3 uv (dev) — coexists with pipx, never shares an env
`uv venv` → `uv pip install -e '.[dev]'` (ruff, mypy) → `uv run ruff check .` / `uv run mypy spacelabel` / `uv run spacelabel agent --debug`. pipx uses stdlib `venv --without-pip`; uv uses its own `./.venv`; both from the same interpreter, zero conflict.

---

## 10. Module & dependency choices (stdlib-first scorecard)

| Concern | Choice | New dep? |
|---|---|---|
| AppKit / windows / notifications | **PyObjC** (Cocoa, Quartz, CoreText) | accepted (locked) |
| CGS reads | PyObjC `loadBundleFunctions` of CoreGraphics (re-exports SkyLight) | none |
| Menu bar | raw `NSStatusItem` | **no** (rumps rejected) |
| HUD / overlay | `NSPanel` (non-activating) | none |
| Overlay / HUD text | `NSBitmapImageRep` + Core Text | **no** (Pillow rejected) |
| Preferences | view-based `NSTableView` | none |
| CLI | **click** | yes — earns its keep (nested commands) |
| Logging | stdlib `logging` | none |
| Config/labels | stdlib `json` + `fcntl` + `os.replace` | none |

Net new third-party deps beyond the locked PyObjC: **just `click`.**

---

## 11. Tahoe specifics & forward-compat

- No new public Spaces API in macOS 26 (WWDC25 AppKit work was Liquid-Glass design only). The private CGS path remains the only option and is in active use by AltTab and the rewritten WhichSpace on Tahoe.
- `NSWorkspace.activeSpaceDidChangeNotification` still valid and still identity-less (inferred from secondary sources — primary Apple doc unreadable; medium confidence).
- The private surface **does** shift between point releases (`CGDisplayCreateImage` broke on 14.4; `CGSetDisplayTransferByTable` on 26.4; `CGDisplayRegisterReconfigurationCallback` dead on 26.1). Mitigation baked in: resolve every symbol at launch with the CGS→SLS fallback, wrap reads in specific-exception handling, and degrade to the plist parser.
- **Plist fallback** (`~/Library/Preferences/com.apple.spaces.plist`, `SpacesDisplayConfiguration → Management Data → Monitors`): good for **topology/UUID enumeration only**. It is cached by `cfprefsd` and flushed only on Space create/delete, so it is **stale for current-Space** — always read current Space live via CGS. If used reactively, watch the file's delete event and re-query.

---

## 12. Phase-6 verification checklist (probe is designed here, run there)

The Phase-6 read-only probe (designed, not run this phase): print the active-display current Space UUID + all Space UUIDs across all displays, with no-silent-except handling. Must verify, in priority order:

1. **🔑 `uuid` reboot-stability (the project's core assumption):** now ~high confidence from the persistence model + prior art (see `DECISIONS.md` §1 open questions) — but verify on hardware. Cheap proxies first (no full reboot): match live CGS uuid ↔ on-disk plist `Spaces[].uuid` (read-only); then logout/login or `killall -HUP WindowServer` and diff uuid strings. Final gate: label a Space, **reboot**, confirm the same `uuid` returns and the label re-binds (not merely that JSON reloads). A real reboot also covers new-display/NVRAM/OS-upgrade edge cases the proxies can't.
2. **Symbol resolution on 26.5.1:** all four functions resolve from CoreGraphics under the `CGS` names (and the `SLS` fallback resolves too).
3. **Memory:** run the read in a tight loop, watch RSS — flat == the `already_retained` ownership annotation is correct. Settles `already_retained` vs `already_cfretained`.
4. **PyObjC↔CFArray bridge** actually round-trips on this OS/interpreter (only `ctypes` symbol resolution was confirmed in Phase 1; the bridge is the real smoke test).
5. **Dict-key correctness on hardware:** `id64` vs `ManagedSpaceID` equivalence; `type==0` user Spaces; `TileLayoutManager` marks fullscreen.
6. **Active-display fallback** path (NSScreen.main UUID) works if `CGSCopyActiveMenuBarDisplayIdentifier` is forced absent.
7. **Menu-bar icon visibility** on Tahoe (Settings → Menu Bar, no ControlCenter negotiation loop, single instance).
8. **`"Main"` sentinel** handling when "Displays have separate Spaces" is on/off.
