# Wallpaper mode — REMOVED (items R + S) ✅ *(2026-06-25)*

**Outcome:** the experimental wallpaper mode was **removed entirely**, not redesigned.
This file is kept as the resolution record for items **R + S** (see
[`fix-sessions-overview.md`](fix-sessions-overview.md) and [`improvements.md`](improvements.md)).

## Why removal (not redesign)
The mode's premise — *capture the live desktop image → composite the label → set it* —
is unfixable on real setups:
- **R:** it grabs one frame and sets a static composite, **irreversibly clobbering**
  macOS Dynamic (time-of-day `.heic`) / Shuffle wallpapers.
- **S:** `NSWorkspace.desktopImageURLForScreen_` returns the *system default*, not the
  true per-Space image, so the composite base is wrong.

The considered redesign (composite onto **known/user-supplied** base images, sidestepping
R+S) was researched and dropped: **macOS has no public API to set a specific non-active
Space's wallpaper** — only the private `WallpaperExtensionKit` (entitlement-gated, breaks
across releases); the public `setDesktopImageURL:forScreen:` only sets the *current active*
Space. High implement+maintain cost for small gain, and **HUD + corner overlay already
cover the "which Space am I on" need**. Decision recorded in **DECISIONS §7.5**.

## What was removed
- `agent/wallpaper.py` (the whole `WallpaperRenderer` + originals.json / byte-copy /
  flock / TTL-purge / restart-recovery machinery) and `tests/test_wallpaper.py`.
- `geometry.wallpaper_font_size` + its constants; the `wallpaper` mode in `model`/`store`/
  `cli` (`modes.wallpaper`, `wallpaper.position`, `wallpaper.font_size`); the agent wiring
  in `app.py`; the prefs checkbox.
- Docs: DESIGN §6.4 (tombstone), DECISIONS §7 (rewritten as the removal record), CLAUDE.md,
  README, docs/CLI.md, docs/UI.md, docs/TESTING.md, docs/VERIFICATION.md.

## Back-compat
No migration needed: an existing `config.json` with a stale `wallpaper` block /
`modes.wallpaper` is ignored on load and dropped on next save (the loader reads only known
keys; the writer rebuilds from the in-memory `Config`). `mode wallpaper` → usage error
(exit 2); `config set wallpaper.*` → `ConfigKeyError` (exit 1). Regression test added in
`tests/test_store.py`.
