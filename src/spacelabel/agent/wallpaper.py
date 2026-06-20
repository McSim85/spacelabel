"""Wallpaper mode (experimental) -- composite the label onto the real desktop image.

There is **no per-Space wallpaper API**: ``setDesktopImageURL:forScreen:`` is
per-``NSScreen``, and on Sonoma+/Tahoe ``WallpaperAgent`` owns wallpaper state,
self-reverts programmatic sets, and silently flips "Show on all spaces" off on
repeated sets (DESIGN.md §6.4, DECISIONS.md §7). So this is cosmetic/best-effort.

We never modify the user's wallpaper file. We capture the current desktop image as
a per-display *base*, composite the label onto a COPY at a configurable anchor, and
write it to a managed cache (``~/Library/Caches/spacelabel/wallpaper/``, one PNG per
display, overwritten in place, stale files purged). To avoid compositing a label
onto our own previous output (label-on-label), a base whose path is inside our cache
is ignored in favour of the remembered original.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

import objc
from AppKit import (
    NSBezierPath,
    NSBitmapImageFileTypePNG,
    NSBitmapImageRep,
    NSColor,
    NSCompositingOperationCopy,
    NSDeviceRGBColorSpace,
    NSFont,
    NSGraphicsContext,
    NSImage,
    NSMakeRect,
    NSScreen,
    NSString,
    NSWorkspace,
    NSZeroRect,
)
from Foundation import NSURL

import spacelabel
from spacelabel.agent import geometry

__all__ = ["WallpaperRenderer"]

log = logging.getLogger(__name__)


class WallpaperRenderer:
    """Best-effort renderer that composites the label onto the screen's wallpaper.

    Cosmetic only: ``WallpaperAgent`` may revert the set. Never a source of truth,
    never edits the WallpaperAgent store, never modifies the user's wallpaper file
    (DECISIONS.md 7.2/7.3) -- it only writes composites into its own cache.
    """

    def __init__(self) -> None:
        """Set up the cache dir + per-display original/output bookkeeping."""
        self._cache_dir = Path.home() / "Library" / "Caches" / spacelabel.APP_NAME / "wallpaper"
        # display key -> our output PNG path (stable, overwritten in place).
        self._outputs: dict[str, Path] = {}
        # display key -> the user's real wallpaper path to composite onto.
        self._originals: dict[str, str] = {}
        self._warned = False

    def render_and_set(
        self, text: str, *, screen: object | None = None, position: str = "center"
    ) -> None:
        """Composite ``text`` onto ``screen``'s wallpaper at ``position`` (best-effort).

        Args:
            text: The label to draw.
            screen: Target ``NSScreen``; defaults to the main screen.
            position: One of the nine :data:`~spacelabel.agent.geometry.ANCHORS`.
        """
        if not self._warned:
            log.warning(
                "wallpaper mode is experimental and cosmetic: the system WallpaperAgent "
                "owns wallpaper state and may revert or flicker these sets; it is never a "
                "source of truth and the original wallpaper file is never modified "
                "(DESIGN.md §6.4)"
            )
            self._warned = True
        target = screen if screen is not None else NSScreen.mainScreen()
        if target is None:
            log.warning("no screen available; wallpaper not set")
            return
        base_path = self._base_image_path(target)
        if base_path is None:
            # We could not recover the user's real wallpaper to composite onto -- e.g.
            # after a restart, when the current desktop image is still our own cached
            # PNG and no original was remembered. Skip rather than paint a black
            # backdrop over (and SET) it, which would visually replace their wallpaper.
            log.warning(
                "skipping wallpaper render for %s: original wallpaper unknown (would "
                "otherwise replace it); switch to that Space's real wallpaper to recapture",
                self._screen_key(target),
            )
            return
        anchor = position if position in geometry.ANCHORS else "center"
        try:
            png_path = self._render_png(text, target, base_path, anchor)
        except (ValueError, OSError) as exc:
            log.warning("wallpaper render failed for text %r: %s", text, exc)
            return
        self._set_wallpaper(png_path, target)
        self._purge()

    @objc.python_method
    def _screen_key(self, screen: object) -> str:
        """Derive a stable filename key from the screen's CGDirectDisplayID."""
        description = screen.deviceDescription()
        number = description.get("NSScreenNumber")
        if number is None:
            return "main"
        return f"display-{int(number)}"

    @objc.python_method
    def _output_path_for(self, screen: object) -> Path:
        """Return (and remember) the stable per-display output PNG path."""
        key = self._screen_key(screen)
        path = self._outputs.get(key)
        if path is None:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            path = self._cache_dir / f"{key}.png"
            self._outputs[key] = path
        return path

    @objc.python_method
    def _is_ours(self, path: str) -> bool:
        """Return whether ``path`` is one of our cache outputs (not a real wallpaper)."""
        return str(path).startswith(str(self._cache_dir))

    @objc.python_method
    def _base_image_path(self, screen: object) -> str | None:
        """Return the original wallpaper path to composite onto (or None).

        Captures the user's current wallpaper as the per-display base; if the
        current image is already one of our composites, keep the remembered original
        so we never stack a label on a label.
        """
        key = self._screen_key(screen)
        url = NSWorkspace.sharedWorkspace().desktopImageURLForScreen_(screen)
        current = url.path() if url is not None else None
        if current and not self._is_ours(current):
            self._originals[key] = str(current)
        return self._originals.get(key)

    @objc.python_method
    def _render_png(self, text: str, screen: object, base_path: str, anchor: str) -> Path:
        """Composite the label onto ``base_path``; write and return the PNG path.

        Raises:
            ValueError: If a pixel-backed bitmap could not be allocated/encoded.
            OSError: If the PNG could not be written.
        """
        frame = screen.frame()
        scale = float(screen.backingScaleFactor())
        px_w = round(float(frame.size.width) * scale)
        px_h = round(float(frame.size.height) * scale)
        if px_w <= 0 or px_h <= 0:
            raise ValueError(f"non-positive pixel size {px_w}x{px_h}")

        rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(  # noqa: E501
            None, px_w, px_h, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0
        )
        if rep is None:
            raise ValueError("could not allocate NSBitmapImageRep")

        context = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
        if context is None:
            raise ValueError("could not create graphics context for bitmap")
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.setCurrentContext_(context)
        try:
            self._draw_composite(text, base_path, px_w, px_h, anchor)
        finally:
            NSGraphicsContext.restoreGraphicsState()

        png_data = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
        if png_data is None:
            raise ValueError("PNG encoding returned no data")
        path = self._output_path_for(screen)
        if not png_data.writeToFile_atomically_(str(path), True):
            raise OSError(f"failed to write wallpaper PNG to {path}")
        return path

    @objc.python_method
    def _draw_composite(
        self, text: str, base_path: str | None, px_w: int, px_h: int, anchor: str
    ) -> None:
        """Draw the base wallpaper (or a black fallback), then the anchored label."""
        from AppKit import (
            NSFontAttributeName,
            NSForegroundColorAttributeName,
            NSRectFill,
        )

        full = NSMakeRect(0.0, 0.0, float(px_w), float(px_h))
        drew_base = False
        if base_path is not None:
            image = NSImage.alloc().initWithContentsOfFile_(base_path)
            if image is not None:
                image.drawInRect_fromRect_operation_fraction_(
                    full, NSZeroRect, NSCompositingOperationCopy, 1.0
                )
                drew_base = True
        if not drew_base:
            # No recoverable original (e.g. just after a restart): neutral backdrop.
            NSColor.blackColor().setFill()
            NSRectFill(full)

        font_px = max(48.0, min(float(px_w), float(px_h)) * 0.12)
        attrs = {
            NSFontAttributeName: NSFont.boldSystemFontOfSize_(font_px),
            NSForegroundColorAttributeName: NSColor.whiteColor(),
        }
        ns_text = NSString.stringWithString_(str(text))
        size = ns_text.sizeWithAttributes_(attrs)
        margin = round(min(float(px_w), float(px_h)) * 0.04)
        x, y = geometry.anchor_origin(
            (0.0, 0.0, float(px_w), float(px_h)),
            float(size.width),
            float(size.height),
            anchor,
            float(margin),
        )
        # A translucent rounded backdrop keeps white text legible over any wallpaper.
        pad = font_px * 0.25
        backdrop = NSMakeRect(
            x - pad, y - pad, float(size.width) + 2 * pad, float(size.height) + 2 * pad
        )
        NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.45).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(backdrop, pad, pad).fill()
        ns_text.drawAtPoint_withAttributes_((x, y), attrs)

    @objc.python_method
    def _purge(self) -> None:
        """Remove cache PNGs that are not a current per-display output (space safety)."""
        keep = {str(path) for path in self._outputs.values()}
        try:
            stale = [png for png in self._cache_dir.glob("*.png") if str(png) not in keep]
        except OSError as exc:
            log.debug("wallpaper cache purge skipped: %s", exc)
            return
        for png in stale:
            with contextlib.suppress(OSError):
                png.unlink()

    @objc.python_method
    def _set_wallpaper(self, path: Path, screen: object) -> None:
        """Set ``path`` as ``screen``'s desktop image (best-effort, logged on failure)."""
        url = NSURL.fileURLWithPath_(str(path))
        workspace = NSWorkspace.sharedWorkspace()
        ok, error = workspace.setDesktopImageURL_forScreen_options_error_(url, screen, {}, None)
        if not ok:
            log.warning("setDesktopImageURL failed for %s: %s", path, error)
