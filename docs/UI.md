# spacelabel — UI specification

> **Status:** finalized in Phase 3 (design-only — no implementation). This is the
> visual + interaction contract Phase 4 builds the AppKit surfaces against.
> Companion: [`DESIGN.md`](../DESIGN.md) §6 (display modes), §7 (data model),
> [`DECISIONS.md`](../DECISIONS.md) §2 (UI dep choices), [`CLI.md`](./CLI.md).

## Visual mockup

A rendered, to-spec mockup of every surface below — menu bar + buttons row, the
dropdown, the preferences window, and HUD/overlay placement with the geometry
math — lives as an interactive page:

- **Hosted artifact:** <https://claude.ai/code/artifact/00bfae33-3e11-4be1-a557-59a5441b444e>
- **In-repo source (open locally):** [`ui-mockup.html`](./ui-mockup.html)

> The mockup renders the macOS chrome faithfully (Tahoe Liquid-Glass
> translucency, system fonts, the pill buttons row) on a desktop scene; a
> mint-accent layer carries the spec annotations. It is a reference, not shipping
> code. ASCII wireframes below are the durable fallback if the render is
> unavailable.

---

## 1. Principles carried from Phase 1–2

- **One status item.** The buttons row is **one** `NSStatusItem` hosting a custom
  CG-drawn view — never N status items (DECISIONS 2.1, 6.5; worsens Tahoe's
  Control Center hiding + notch overflow).
- **Current = alpha, not color.** The active Space is drawn at full opacity, the
  rest at ~40%. Color is reserved for the user's own per-label tag.
- **Everything keyed by Space UUID.** The display UUID is stored alongside for
  grouping/labels only — never part of the key (DECISIONS 1.4, 5.x).
- **Geometry computed at runtime.** Font sizes, positions, fades derive from the
  current display's points + `backingScaleFactor`; nothing is hardcoded
  (portability requirement; §4 here).
- **Visibility is fragile on Tahoe.** Run one instance; never loop-toggle
  `isVisible` (DECISIONS 6.5); copy points users to the Settings check.

---

## 2. Menu-bar UI

### 2.1 Status title (primary mode)

The active Space's label is the `NSStatusItem` button title, updated from the
debounced space-change callback (DESIGN §6.1). Title-only is the quiet default;
the buttons row is opt-in.

```
menu bar (right cluster):                         … ▢ Email  [E][C][S]│[T]  🔋 ▦ 9:41
                                                       └ title ┘ └ buttons row ┘
```

- Truncate to `menubar.max_length` (default 24) with an ellipsis.
- If a Space has no label, the title falls back to `Desktop N` (its current
  ordinal) so the item is never blank.
- Don't hardcode contrast for a solid bar — Tahoe's menu bar is translucent
  (DESIGN §6.1).

### 2.2 Buttons row (optional, config-toggled)

One drawn view, a row of pills. Each pill = the **first letter(s)** of the
label, falling back to the **Space number** when unlabeled.

```
 ┌──────────────── one NSStatusItem, one custom view ────────────────┐
 │   [E] [C] [S] [D] [5] [6]  │  [T] [Do] [3]                         │
 │    ▲                    ▲   │   ▲   ▲                               │
 │  current             inactive│ divider                             │
 │ (alpha 1.0)        (alpha .4)│ between physical displays           │
 └────────────────────────────────────────────────────────────────────┘
   group = LG UltraFine (6 Spaces)   │  group = DELL 4K (3 Spaces)
```

- **Current marker:** alpha `1.0` for the current Space, `~0.4` for the rest —
  **never** color (one current *per display*). Color, if the user sets one, only
  tints the pill fill; it never signals "current".
- **Pill text:** 1–2 leading letters of the label (`menubar.pill_label_chars`,
  default 1, max 2; "Docs" → `Do` when 2). Unlabeled → the Space number.
- **Per-physical-display split:** displays drawn **left-to-right** in
  `NSScreen.screens()` order, separated by a thin vertical divider. Optionally
  tint/underline the **active display's** group (the one owning the menu bar).
- **Scope toggle** (`menubar.buttons_scope`): `all_displays` (default) or
  `active_display` (only the menu-bar-owning display's Spaces).
- **Width adapts** to N Spaces; the row is a single variable-width item so the
  notch/overflow behavior is the system's normal status-item handling, not N
  items competing.

### 2.3 Dropdown (the everyday editing surface)

Click the status item → `NSMenu`. WhichSpace parity, minus the index bug.

```
┌────────────────────────────────┐
│  Rename this Space…             │   → text dialog → `label set current`
├────────────────────────────────┤
│  LG ULTRAFINE                   │   ← per-display section header (friendly name
│  ✓ ● Email                      │      resolved from display UUID); current ✓
│    ● Code                       │      ● = the user's color tag (optional)
│    ● Slack                      │
│  DELL 4K                        │
│    ● Terminal                   │
│  ✓ ● Docs                       │   ← one current checkmark per display
├────────────────────────────────┤
│  ✓ Menu-bar title               │   ← per-mode toggles (mirror `mode` CLI)
│  ✓ On-switch HUD                │
│    Corner overlay               │
│    Wallpaper            [exp]   │   ← experimental badge (cosmetic, may revert)
├────────────────────────────────┤
│  Preferences…             ⌘,    │
│  Quit spacelabel          ⌘Q    │
└────────────────────────────────┘
```

- **Rename this Space…** opens a small text dialog prefilled with the current
  label → commits via the same path as `label set current`.
- **All-Spaces list grouped under per-display section headers** (disabled header
  rows; friendly name resolved from the display UUID so per-display Spaces never
  conflate). Current Space on each display is checkmarked.
- **Per-mode toggles** for menubar/hud/overlay/wallpaper write `config.json`
  exactly like `spacelabel mode <name> --on/--off`; a running agent reloads live.
- **Quit** stays stopped (LaunchAgent `KeepAlive` is crash-only — DECISIONS 6.4).
- **Never loop-toggle `isVisible`** to force the icon back (Tahoe Control Center
  regression, DECISIONS 6.5). If the icon is hidden, surface the Settings check
  via a notification, not a visibility war.

### 2.4 Click-to-switch (opt-in, **NOT** default v1)

Pills are **display-only by default.** Space *switching* is the one operation
behind the SIP/Dock wall, so it ships as an explicit opt-in
(`menubar.click_to_switch`, default `false`) that walks the user through two
one-time steps:

1. **Enable the Mission Control shortcuts** — *System Settings → Keyboard →
   Keyboard Shortcuts → Mission Control → "Switch to Desktop 1…N"* (ships **off**;
   covers only existing Spaces, max 16).
2. **Grant Accessibility** to the spacelabel agent. *On a pipx install the agent
   runs as the Python interpreter, so the Accessibility entry appears under
   **"python3.x"**, not "spacelabel" — enable that entry. A code-signed `.app`
   bundle (v1.0 packaging) is what makes it read "spacelabel"; see DECISIONS §6.*

Then a left-click on a pill maps the clicked Space's **UUID → its current ordinal**
at click time (ordinals shift on reorder, so resolve live, never cache) and posts
the **bound** "Switch to Desktop N" chord via `CGEventPost`. The chord is read live
from `com.apple.symbolichotkeys` — whatever the user actually set (the default macOS
suggestion is **Ctrl + the desktop number**, e.g. Ctrl+1), not a hardcoded key. While
click-to-switch is on the row captures left-clicks, so the dropdown menu
(Preferences/Quit) is reached by **right-click** or a click off a pill.

> **Failure is visible, never silent.** If Accessibility is not granted, or the
> "Switch to Desktop N" shortcut for the target isn't enabled, the action is
> **disabled with a shown reason** — a disabled `⚠︎ Click-to-switch off — …` row in
> the dropdown plus a WARNING log naming the fix (grant Accessibility / enable the
> shortcut in Keyboard Shortcuts → Mission Control) — and the row stops capturing
> clicks so the menu stays reachable, per the no-silent-except policy. It never looks
> clickable and then silently no-ops. Re-enable by toggling `menubar.click_to_switch`
> off→on after fixing the cause. (Implementation: `platform/switching.py`; the macOS
> "Switch to Desktop N" shortcuts ship **disabled**, so this is the out-of-the-box
> state until the user enables them.)

---

## 3. Preferences window

A **two-level `NSOutlineView`** (DECISIONS 2.4): each **physical display** is a
parent row; its **Spaces** are children. Per-display nesting means two displays'
Spaces never conflate.

```
┌─ ● ● ●  spacelabel — Preferences ───────────────────────────────────────┐
│                                                                          │
│        SPACE / LABEL                              COLOR        NOW       │
│  ▾ 🖥 LG UltraFine — portrait · 2160×3840                                │
│        [ Email|             ] (editing)           [▆]          ●         │
│        Code                                       [▆]          ·         │
│        Slack                                      [▆]          ·         │
│  ▾ 🖥 DELL 4K — landscape · 3840×2160                                    │
│        Terminal                                   [▆]          ·         │
│        Docs                                       [▆]          ●         │
│  ▸ Orphaned labels — not on any current display (2)                      │
│                                                                          │
├──────────────────────────────────────────────────────────────────────┤
│  Labels keyed by Space UUID · orphans retained until pruned             │
│                                          [ Prune orphans… ]  [ Done ]   │
└──────────────────────────────────────────────────────────────────────┘
```

- **Columns:** label (inline-editable text), color (`NSColorWell`/swatch),
  and a "now" marker for the current Space on its display.
- **Inline edit:** double-click a label cell to edit; commit on **Return or
  focus-loss** (resolves the DECISIONS 2.x open question — both commit; Esc
  cancels). Writes go through the atomic + `fcntl.flock` store path (DESIGN §7.3).
- **Color** is a per-label user attribute (new `color` field, §6 below); it tints
  the pill/overlay/HUD but is independent of the current-marker (which is alpha).
- **Friendly display names** resolved from the display UUID (model/orientation/
  resolution where available), recomputed on `didChangeScreenParameters`.
- **Orphaned UUIDs:** a Space deleted/recreated gets a **new** UUID, so old
  labels collapse under an **"Orphaned labels"** group. **Retained by default**
  (DECISIONS 5.6) — never auto-deleted, because cross-reboot UUID stability is
  still being verified (§12 of DESIGN). **Prune orphans…** drops them on demand
  (same effect as `label prune`); a confirm sheet lists what will go.
- **No row virtualization needed** — Spaces stay well under the soft 16-per-
  display ceiling (DECISIONS 3.5).

---

## 4. HUD & overlay appearance — runtime geometry

Both are borderless **non-activating `NSPanel`s** (DECISIONS 2.2): click-through,
all-Spaces, never key/main. **Every dimension is computed from the current
display's geometry — nothing is hardcoded** (portability requirement).

### 4.1 The one formula

```
# S = the display's SHORT side, in points — handles portrait & landscape alike.
S = min(frame.width_pt, frame.height_pt)        # points, not pixels
hud_font     = clamp(round(S * 0.05),  18, 64) pt
overlay_font = overlay.font_size (default 15 pt) | auto: clamp(round(S * 0.018), 12, 28)

# ONE shared anchor helper places BOTH panels inside visibleFrame
# (AppKit bottom-left origin; vf = (vx,vy,vw,vh), panel = (w,h)):
def anchor_origin(vf, w, h, position, m):
    x = {"left":   vx + m,
         "center": vx + (vw - w) / 2,        # margin ignored on a centered axis
         "right":  vx + vw - w - m}[position.horizontal]
    y = {"top":    vy + vh - h - m,
         "middle": vy + (vh - h) / 2,
         "bottom": vy + m}[position.vertical]
    return (x, y)

hud:     position = hud.position     (default "center"),    m = hud.margin     (default 24 pt)
overlay: position = overlay.corner   (default "top-right"), m = overlay.margin (default 12 pt)
hud_fade = fade-in 120 ms → hold hud.duration_ms (default 1100) → fade-out 350 ms
           (one reused NSPanel; animator().setAlphaValue_)
```

**HUD position is configurable** — any of the **nine anchors** (a 3×3 grid):

```
   top-left      top-center      top-right
 center-left      CENTER ←def   center-right
 bottom-left    bottom-center   bottom-right
```

`hud.position` defaults to `center`; the overlay uses the same nine via
`overlay.corner` (default `top-right` — corners read best for a *persistent*
label, but all nine are valid). Edge/corner anchors are inset by the relevant
`margin`; centered axes ignore it. This replaces the earlier fixed
"upper-center / 22%" rule — one helper, both panels, fully positionable.

- **Retina crispness is free:** AppKit draws text in points; `backingScaleFactor`
  handles the pixel backing. We never multiply font sizes by scale.
- **`visibleFrame`** (not `frame`) keeps the HUD/overlay clear of the menu bar,
  notch, and Dock.
- **Reposition on `NSApplicationDidChangeScreenParameters`** — recompute every
  panel's origin; re-discover topology; never cache (DESIGN §4).
- **Multiple displays:** the HUD shows on the **active** display (where the
  switch landed); the overlay is **one panel per display**, each showing that
  display's current Space label.

**Overlay notes list (per-Space task queue, DECISIONS 9.10).** When a Space has
notes, the corner overlay grows from a single line into a **bold title** (the label,
or `Desktop N` when unlabeled) above one line per task — each prefixed by a glyph
reflecting its `done` state:

```
┌─────────────────────────────┐
│  Email            ← bold title (the Space label, or "Desktop N")
│  ☐ reply to Jane             │
│  ☐ invoice #4012             │
│  ☑ ping ops about deploy     │   ☑ = done, ☐ = open
└─────────────────────────────┘
```

The panel **auto-resizes** to fit all lines and stays pinned to `overlay.corner`.
Body text uses **`overlay.note_font_size`** (int, or `"auto"` = a step below the
title); **`overlay.show_notes`** (default `true`) hides the list when off, leaving
the title-only overlay. The checkboxes are **display-only glyphs, never interactive
controls**: the overlay panel is click-through (`ignoresMouseEvents`, DECISIONS 2.2)
and must never steal focus or capture a click, so toggling is done via `spacelabel
note done` (CLI) and reflected on the agent's next refresh (the 1 s file-watch). An
interactive checkbox would require giving the panel mouse focus — explicitly **out of
scope**; the Preferences window (a normal activating window) is the right home for an
editable surface if ever wanted.

### 4.2 Verification on the reference + a laptop

| Display | Pixels | Scale | Points (W×H) | S | HUD font | Overlay (auto) |
|---|---|---|---|---|---|---|
| LG UltraFine (portrait) | 2160×3840 | 2× | 1080×1920 | **1080** | `0.05·1080 = 54 pt` | `0.018·1080 ≈ 19 pt` |
| DELL 4K (landscape, scaled) | 3840×2160 | 2× | 1920×1080 | **1080** | `54 pt` | `19 pt` |
| 13" laptop (built-in, scaled) | ~2940×1912 | 2× | ~1470×956 | **956** | `0.05·956 ≈ 48 pt` | `0.018·956 ≈ 17 pt` |

- Portrait and landscape 4K land on the **same** S (1080) → identical HUD size,
  because S keys off the **short** side; a portrait panel doesn't get a giant
  HUD just because it's tall. ✔
- A native-resolution 4K (`@1×`, points 3840×2160 → S 2160 → `0.05·2160 = 108`)
  is caught by the **clamp at 64 pt**, so the banner never dominates. ✔
- **Single-display laptop:** same formulas, one screen — HUD centers on it, the
  overlay sits in its configured corner. No multi-display branch needed. ✔

### 4.3 Placement summary

```
   ┌──────────── active display ────────────┐
   │ ▔▔▔▔▔▔▔ menu bar ▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔ │
   │                                  ┌────┐ │  overlay: any of 9 anchors
   │                                  │Mail│ │            (default top-right),
   │            ┌──────────┐          └────┘ │            persistent, click-through
   │            │  Email   │  ← HUD                       │
   │            └──────────┘  (default center;  │  HUD: any of 9 anchors via
   │                          here shown a bit  │       hud.position; transient,
   │                          high), fades ~1.1s│       one reused panel
   └─────────────────────────────────────────┘
```

---

## 5. Copy / tone

- Plain verbs, sentence case; name things by what the user controls
  ("Rename this Space", "Corner overlay"), not by implementation.
- The experimental wallpaper mode always reads as cosmetic/may-revert wherever
  it appears (`[exp]` badge in menu; caveat line on enable).
- Failure states are directive, never silent: a disabled click-to-switch pill
  says *why* and *how to enable it*; a missing menu-bar icon points to the
  Settings check.

---

## 6. Phase-4 hand-off (what this spec adds to the data/UI APIs)

These are **new in Phase 3** and recorded in [`DECISIONS.md`](../DECISIONS.md) §9:

- **`labels.json`** — optional **`color`** per entry (hex string). UUID stays the
  sole key; `color` is informational/forward-compatible like the existing
  `last_display`. → `model.Label` gains `color: str | None = None`.
- **`config.json` `menubar` block** — new keys: `show_buttons_row` (bool, default
  `false`), `buttons_scope` (`"all_displays"` | `"active_display"`, default
  `all_displays`), `pill_label_chars` (int 1–2, default 1), `click_to_switch`
  (bool, default `false`). → `model.Config` per-mode settings must cover these.
- **`config.json` `hud` block** — new keys: `position` (one of the nine anchors,
  default `center`) and `margin` (int pt, default 24) join the existing
  `duration_ms`/`font_size`.
- **`config.json` `overlay` block** — `font_size` may be the literal `"auto"`
  (→ `clamp(round(S*0.018),12,28)`) in addition to an int; `corner` accepts any of
  the **nine** anchors (default `top-right`); `margin` int pt.
- **Per-Space notes (DECISIONS 9.10, added after Phase 3)** — `labels.json` entries
  gain an optional **`notes: [{text, done}]`** array (the Space UUID stays the sole
  key; a notes-only entry with an empty/absent `label` is valid). The `overlay`
  block gains **`show_notes`** (bool, default `true`) and **`note_font_size`**
  (int | `"auto"`). → `model.Note` + `Label.notes`; the `note` CLI group
  (§3.7 of [`CLI.md`](./CLI.md)) is the edit surface; overlay checkboxes are
  display-only glyphs (click-through panel).
- **Shared anchor helper** — `anchor_origin(visibleFrame, w, h, position, margin)`
  (the nine-position grid) backs both HUD and overlay placement; the natural home
  is alongside `metrics_for(display)`.
- **`menubar.py`** must draw the buttons row as a single custom view (sizing,
  per-display dividers, alpha-marking, hit-testing for opt-in switch) — not N
  items. **`prefs.py`** is a two-level `NSOutlineView` (display→Spaces), color
  well column, prune button. **`hud.py`/`overlay.py`** consume the §4.1 geometry
  helper (a shared `metrics_for(display)` is the natural place — new in Phase 4).
- **Friendly display names** need a resolver (UUID → name); a small addition to
  `displays.py`.
