# Redesign — wallpaper mode: reliable wallpaper detection (Dynamic/Shuffle + per-Space)  (items R + S)

**Model:** Opus 4.8 · **effort:** high (a design session — settle the approach before coding). **Fresh session + fresh branch off latest `main`.** Part of the Phase-6 fix set — see [`fix-sessions-overview.md`](fix-sessions-overview.md). **Track B — touches only `wallpaper.py`, safe to run fully in parallel.**

## Why (Phase-6 findings — full diagnosis in `improvements.md` R + S)
The experimental wallpaper mode's premise (*capture the current wallpaper → composite a label → set it*) is **unreliable on real setups**:
- **R (Dynamic/Shuffle):** it grabs one frame + sets a static composite → **irreversibly clobbers** a macOS Dynamic (time-of-day `.heic`) or Shuffle wallpaper, and persists only a frame as the "original".
- **S (per-Space):** macOS has **per-Space wallpapers**; `NSWorkspace.desktopImageURLForScreen_` returned `/System/Library/CoreServices/DefaultDesktop.heic` (the system default) for a Space actually showing a "Dubai Skyline" photo → the composite base is **wrong**.

Wallpaper mode is **experimental / off-by-default**, so this is non-blocking — but it's effectively non-functional + unsafe on multi-Space/multi-display until the detection is redesigned.

## Decide (design first), then implement
1. **Detect wallpaper type/source** (read-only — **never edit** the WallpaperAgent store, DECISIONS §7):
   - **Dynamic:** the image is a `.heic` carrying dynamic-desktop metadata (solar / `h24` keys / multiple embedded reps) — inspect via `CGImageSource` properties.
   - **Shuffle / per-Space:** read the WallpaperAgent store **read-only** — Tahoe `~/Library/Application Support/com.apple.wallpaper/Store/Index.plist` (older: `~/Library/Application Support/Dock/desktoppicture.db`) — fragile/private; OR
   - **Capture the rendered desktop pixels** (`CGWindowListCreateImage` of the desktop window layer) as the base — sidesteps "which file" but loses dynamic + must exclude icons/widgets.
2. **Policy:** composite **only** a plain static image; on Dynamic/Shuffle/per-Space-that-can't-be-resolved, **skip with a clear notice** (preferred for the non-interactive agent) and/or require explicit per-display opt-in — **never silently overwrite**.
3. Keep originals-persistence correct for the static path (capture + restore the real image).

## Read first
`agent/wallpaper.py` (`render_and_set`, `_base_image_path`, `_record_original`, the cache/skip guard), `DECISIONS.md` §7, `DESIGN.md` §6.4, `improvements.md` items R + S.

## Acceptance
On a **Dynamic** or **Shuffle** desktop, wallpaper mode does **not** alter it (skips with a logged/visible reason); on a **per-Space** static image it composites onto the **correct** current image + persists the real original; restore on `--off` works. Tests for the type-detection branches. Then re-run the deferred Phase-6 composite test (C19–C26 / Part 2 §6) on a **static** wallpaper and record in `docs/VERIFICATION.md`. Update `DECISIONS.md` §7 with the chosen detection approach.

## Before committing
Gates + **codex review loop** until clean. Conventional Commit (`feat(wallpaper): detect dynamic/shuffle/per-Space; static-only compositing`). Ask before commit/push. Mark R+S done in `improvements.md`, tick the overview.
