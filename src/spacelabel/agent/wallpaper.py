"""Wallpaper mode (experimental) -- composite the label onto the real desktop image.

There is **no per-Space wallpaper API**: ``setDesktopImageURL:forScreen:`` is
per-``NSScreen``, and on Sonoma+/Tahoe ``WallpaperAgent`` owns wallpaper state,
self-reverts programmatic sets, and silently flips "Show on all spaces" off on
repeated sets (DESIGN.md §6.4, DECISIONS.md §7). So this is cosmetic/best-effort.

We never modify the user's wallpaper file. We capture the current desktop image as
a per-display *base*, composite the label onto a COPY at a configurable anchor, and
write it to a managed cache (``~/Library/Caches/spacelabel/wallpaper/``, one PNG per
display, overwritten in place, stale files swept). To avoid compositing a label onto
our own previous output (label-on-label), a base whose path is inside our cache is
ignored in favour of the remembered original.

To survive an agent restart -- when the system reports *our own* composite as the
current wallpaper and the live original is no longer discoverable -- the captured
original is persisted per display: its path in ``originals.json`` plus a byte copy
in ``original-<display>.png`` as the fallback when that path is gone. Cache files
are swept on a TTL (our own files only; never anything outside the cache).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import re
import shutil
import tempfile
import time
from collections.abc import Iterator
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
from spacelabel.platform import displays

__all__ = ["WallpaperRenderer"]

log = logging.getLogger(__name__)

#: Name of the persisted display-UUID -> original-wallpaper-path map (in the cache).
_ORIGINALS_FILE = "originals.json"
#: Cache files (composites + original copies) not refreshed within this window are
#: swept by :meth:`WallpaperRenderer._purge`. Documented small constant; composites
#: are rewritten on every Space change, so anything older is genuinely abandoned.
_CACHE_TTL_DAYS = 14
#: Absolute floor (pixels) for the auto-sized wallpaper label, preserving the prior
#: renderer's ``max(48, ...)`` so labels stay legible on small / 1x displays.
_WALLPAPER_AUTO_FLOOR_PX = 48.0

#: Exact filenames this renderer creates: ``main.png``, ``display-<CGDirectDisplayID>.png``
#: (``_screen_key``: ``main`` or ``display-<int>``) and ``original-<CFUUID>.png``
#: (the canonical uppercase 8-4-4-4-12 UUID from ``displays.display_uuid``). Matching
#: the exact shape -- not just a ``display-``/``original-`` prefix -- so a foreign file
#: like ``display-reference.png`` or ``original-photo.png`` is never an eviction target.
_MANAGED_PNG = re.compile(
    r"^(?:main"
    r"|display-\d+"
    r"|original-[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})"
    r"\.png$"
)


def _is_managed_png(name: str) -> bool:
    """Return whether ``name`` is exactly one of the filenames this renderer creates (PURE).

    Anything else under the cache dir is foreign and must never be swept by
    :meth:`WallpaperRenderer._purge` (see :data:`_MANAGED_PNG`).
    """
    return bool(_MANAGED_PNG.match(name))


def _select_evictions(
    files: list[tuple[Path, float]],
    keep: set[Path],
    now: float,
    ttl_seconds: float,
) -> list[Path]:
    """Return cache PNGs to evict: not in ``keep`` and older than the TTL (PURE).

    ``files`` is ``(path, mtime)`` pairs. Only ``*.png`` are considered (the
    ``originals.json`` map and any non-cache file are never selected); a file in
    ``keep`` (a current composite output or an active original copy) is never
    evicted regardless of age.
    """
    evictable: list[Path] = []
    for path, mtime in files:
        if path in keep or path.suffix != ".png":
            continue
        if now - mtime >= ttl_seconds:
            evictable.append(path)
    return evictable


class WallpaperRenderer:
    """Best-effort renderer that composites the label onto the screen's wallpaper.

    Cosmetic only: ``WallpaperAgent`` may revert the set. Never a source of truth,
    never edits the WallpaperAgent store, never modifies the user's wallpaper file
    (DECISIONS.md 7.2/7.3) -- it only writes composites and original copies into its
    own cache.
    """

    def __init__(self, *, cache_dir: Path | None = None) -> None:
        """Set up the cache dir + per-display original/output bookkeeping.

        ``cache_dir`` is injectable for tests; production uses the per-user cache.
        Any originals persisted by a prior run are loaded so the mode keeps working
        across an agent restart (DECISIONS.md §7).
        """
        self._cache_dir = cache_dir or (
            Path.home() / "Library" / "Caches" / spacelabel.APP_NAME / "wallpaper"
        )
        # display key -> our output PNG path (stable, overwritten in place).
        self._outputs: dict[str, Path] = {}
        # Two maps with different keys for their different jobs:
        #  - `_persisted` (display UUID -> path) mirrors originals.json + the on-disk
        #    `original-<uuid>.png` copies; it drives CROSS-RESTART recovery and is
        #    keyed by the stable UUID. Loaded from disk.
        #  - `_originals` (CGDirectDisplayID key -> path) is the freshest live path per
        #    display for WITHIN-SESSION recovery; keyed by the session-stable display
        #    id so UUID-resolution flips never orphan it. Starts empty each session.
        self._persisted: dict[str, str] = self._load_originals()
        self._originals: dict[str, str] = {}
        # display UUID -> the path whose bytes `original-<uuid>.png` currently holds,
        # set only on a successful copy. Distinguishes "copy is up to date for this
        # path" from a stale copy left by an earlier copy failure (so the retry isn't
        # skipped by an mtime coincidence). Empty at startup -- the first capture
        # re-copies, which is correct.
        self._copied: dict[str, str] = {}
        self._warned = False

    def render_and_set(
        self,
        text: str,
        *,
        screen: object | None = None,
        position: str = "center",
        font_size: int | str = "auto",
    ) -> None:
        """Composite ``text`` onto ``screen``'s wallpaper at ``position`` (best-effort).

        Args:
            text: The label to draw.
            screen: Target ``NSScreen``; defaults to the main screen.
            position: One of the nine :data:`~spacelabel.agent.geometry.ANCHORS`.
            font_size: Point size for the label, or ``"auto"`` to scale from the
                display's short side (:func:`~spacelabel.agent.geometry.wallpaper_font_size`).
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
            # PNG and neither the persisted original path nor its copy survives. Skip
            # rather than paint a black backdrop over (and SET) it, which would
            # visually replace their wallpaper (DECISIONS.md §7 fifth round).
            log.warning(
                "skipping wallpaper render for %s: original wallpaper unknown (would "
                "otherwise replace it); switch to that Space's real wallpaper to recapture",
                self._screen_key(target),
            )
            return
        anchor = position if position in geometry.ANCHORS else "center"
        try:
            png_path = self._render_png(text, target, base_path, anchor, font_size)
        except (ValueError, OSError) as exc:
            log.warning("wallpaper render failed for text %r: %s", text, exc)
            return
        self._set_wallpaper(png_path, target)
        self._purge()

    @objc.python_method
    def _screen_key(self, screen: object) -> str:
        """Derive a stable composite-filename key from the screen's CGDirectDisplayID.

        Falls back to ``"main"`` when the description or ``NSScreenNumber`` is absent
        (guarded like :meth:`_display_key`, since this is now also reached from the
        purge / connected-keys paths -- a missing description must not raise there).
        """
        description = screen.deviceDescription()
        number = description.get("NSScreenNumber") if description is not None else None
        if number is None:
            return "main"
        return f"display-{int(number)}"

    @objc.python_method
    def _display_key(self, screen: object) -> str | None:
        """Return the stable per-display key for persisted originals, or ``None``.

        The composite filename keys off the session-scoped ``CGDirectDisplayID``
        (:meth:`_screen_key`), but persisted originals must round-trip across a
        restart, so they key off the **stable display UUID**
        (:func:`spacelabel.platform.displays.display_uuid`). There is deliberately no
        ``CGDirectDisplayID`` fallback: a transient-id key would orphan the capture
        once the UUID resolves on a later run. ``None`` means "no stable key this
        run" -- the caller still composites onto the live image but does not persist.
        """
        description = screen.deviceDescription()
        number = description.get("NSScreenNumber") if description is not None else None
        if number is None:
            return None
        return displays.display_uuid(int(number))

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
    def _original_copy_path(self, key: str) -> Path:
        """Return the per-display original-wallpaper copy path (fallback base)."""
        return self._cache_dir / f"original-{key}.png"

    @objc.python_method
    def _is_ours(self, path: str) -> bool:
        """Return whether ``path`` lives inside our cache dir (not a real wallpaper).

        Component-wise containment (``is_relative_to``), not a string prefix, so a
        sibling like ``.../wallpaper-old/foo.jpg`` that merely shares the textual
        prefix of ``.../wallpaper`` is correctly treated as the user's real image.
        """
        try:
            return Path(path).is_relative_to(self._cache_dir)
        except (TypeError, ValueError):
            return False

    @objc.python_method
    def _current_desktop_path(self, screen: object) -> str | None:
        """Return the screen's current desktop-image path, or ``None`` if unset."""
        url = NSWorkspace.sharedWorkspace().desktopImageURLForScreen_(screen)
        return url.path() if url is not None else None

    @objc.python_method
    def _base_image_path(self, screen: object) -> str | None:
        """Return the original wallpaper path to composite onto (or ``None``).

        Resolution order (DECISIONS.md §7): (a) if the live current image is real
        (outside our cache), use it, remember it in memory under **every** stable-ish
        key for this display (the session display id *and* the UUID when resolvable),
        and -- when the UUID resolves -- persist it (path + copy) under that **stable
        UUID**; (b) else the in-memory remembered path under any of those keys if it
        exists; (c) else the cross-restart persisted path (keyed by UUID) -- and on a hit,
        heal the fallback copy (in case a prior run wrote json but not the copy) and
        seed it into session memory; (d) else the persisted ``original-<uuid>.png``
        copy (also seeded into memory); (e) else ``None`` (caller skips, never paints
        black). Remembering under both keys means within-session recovery survives
        either identifier changing (a UUID-resolution flip *or* a ``CGDirectDisplayID``
        churn) -- only both changing at once is unrecoverable; seeding (c)/(d) into
        memory likewise keeps a restart-recovered base usable through a *transient*
        UUID-resolution loss. Cross-restart recovery (c/d) still needs the UUID.
        """
        cgid = self._screen_key(screen)  # session-stable display key (never UUID-dependent)
        uuid = self._display_key(screen)  # stable display UUID, or None if unresolvable
        mem_keys = [cgid] + ([uuid] if uuid is not None else [])
        current = self._current_desktop_path(screen)
        if current and not self._is_ours(current) and Path(current).exists():
            # (a) live real image, and it actually exists -- a current path that's
            # gone (unmounted volume, iCloud placeholder, renamed file) is NOT a
            # usable base, so fall through to the persisted/copy fallback instead of
            # returning a dead path that _render_png can't load (which would skip).
            for key in mem_keys:
                self._originals[key] = str(current)  # remember live under all keys
            if uuid is not None:
                self._persist_original(uuid, str(current))  # persist to disk: UUID only
            return str(current)
        for key in mem_keys:  # (b) within-session memory, under any stable-ish key
            remembered = self._originals.get(key)
            if remembered and Path(remembered).exists():
                # Retry persistence here too: once our composite is the current image,
                # branch (a) never fires again, so a persist skipped/failed at first
                # capture (lock busy, write error) would otherwise never catch up and
                # restart recovery would stay broken all session. _persist_original
                # no-ops once already persisted. Skip for an ``_is_ours`` remembered
                # value (a seeded copy path from branch (d)) -- that's not a real
                # original to persist.
                if uuid is not None and not self._is_ours(remembered):
                    self._persist_original(uuid, remembered)
                return remembered
        if uuid is not None:
            persisted = self._persisted.get(uuid)  # (c) cross-restart persisted path
            if persisted is None or not Path(persisted).exists():
                # Our cached entry is missing or now dead -- a peer instance may have
                # written or *repaired* originals.json since we loaded it. Re-read,
                # adopt entries we lack (ours win on clash), and take a live disk path
                # for this UUID if our cached one is dead.
                disk = self._load_originals()
                self._persisted = {**disk, **self._persisted}
                disk_path = disk.get(uuid)
                if disk_path and Path(disk_path).exists():
                    self._persisted[uuid] = disk_path
                persisted = self._persisted.get(uuid)
            if persisted and Path(persisted).exists():
                # Heal the copy if a prior run wrote json but not the copy, and seed
                # session memory so a later transient UUID loss still recovers (b).
                self._persist_original(uuid, persisted)
                for key in mem_keys:
                    self._originals[key] = persisted
                return persisted
            copy = self._original_copy_path(uuid)  # (d) persisted byte copy
            if copy.exists():
                copy_path = str(copy)
                for key in mem_keys:  # seed memory: survive transient UUID loss this session
                    self._originals[key] = copy_path
                return copy_path
        return None  # (e) unrecoverable -> skip

    @objc.python_method
    def _persist_original(self, uuid: str, path: str) -> None:
        """Persist a captured original to disk under its stable ``uuid`` (copy + json).

        Skips when already fully persisted (path in json, the copy holds *this* path's
        current bytes by provenance + size/mtime). Otherwise, in **one critical
        section** (a ``flock``, re-reading inside the lock so a peer's entry is never
        dropped):

        1. **copy first** -- this captures the bytes *and* proves the source is
           readable right now, inside the lock (TOCTOU-free vs an early ``exists()``
           check: a source that vanished after the caller's check makes the copy fail
           here). If the copy fails (source gone/unreadable, disk full), bail and
           leave ``originals.json``/``_persisted`` at the last known-good -- we never
           commit a path we cannot actually recover from;
        2. then write the merged path map to ``originals.json``.

        Copy-before-json means ``originals.json`` only ever names a path whose copy we
        successfully captured. A failed json write changes nothing (retried next
        render); a copy that succeeded but whose json write then failed is healed on
        the next render via the in-memory ``_copied`` provenance (no needless re-copy),
        and within-session recovery meanwhile uses the in-memory ``_originals`` entry.
        """
        copy = self._original_copy_path(uuid)
        src = Path(path)
        # ``_copied`` proves the copy's provenance (which path's bytes it holds), so a
        # copy left stale by an earlier failure is always retried, never skipped by a
        # size/mtime coincidence across two different sources.
        copy_holds_path = self._copied.get(uuid) == path and self._copy_is_current(copy, src)
        if self._persisted.get(uuid) == path and copy_holds_path:
            return
        with self._originals_lock() as locked:
            if not locked:
                return  # couldn't lock; leave disk as-is, retry next render
            if not copy_holds_path:
                if not self._copy_original(src, copy):
                    return  # source unreadable/gone, or disk full -> keep last known-good
                self._copied[uuid] = path  # copy now holds this path's bytes
            merged = {**self._load_originals(), uuid: path}  # re-read inside the lock
            if self._save_originals(merged):
                self._persisted = merged

    @contextlib.contextmanager
    def _originals_lock(self) -> Iterator[bool]:
        """Hold a **non-blocking** exclusive advisory lock on ``originals.json``.

        Yields ``True`` while held, ``False`` if the lock is busy or couldn't be
        acquired -- the caller then leaves disk untouched and retries on the next
        render. Non-blocking (``LOCK_NB``) on purpose: this runs on the Space-change
        render path, so it must never stall the agent waiting on a peer that's
        mid-copy. Lighter than the store's flock wrapper, same idea.
        """
        lock_path = self._cache_dir / (_ORIGINALS_FILE + ".lock")
        fd: int | None = None
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            log.debug("originals lock %s busy/unavailable, will retry: %s", lock_path, exc)
            if fd is not None:
                os.close(fd)
            yield False
            return
        try:
            yield True
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    @objc.python_method
    def _copy_is_current(self, copy: Path, src: Path) -> bool:
        """Return whether ``copy`` holds ``src``'s current bytes (size + mtime check).

        :meth:`_copy_original` preserves the source's size and mtime on the copy, so
        for an unchanged source both match exactly -- a skewed/future-dated source
        mtime (common after a restore/sync) won't make this perpetually false and
        rewrite every render. An in-place rewrite at the same path is caught by a size
        change (the common case) or a newer mtime; only a same-size, non-newer-mtime
        overwrite slips through (the documented mtime-heuristic limit). Missing copy
        or unreadable source -> not current (re-copy / skip).
        """
        try:
            copy_stat = copy.stat()
            src_stat = src.stat()
        except OSError:
            return False
        return copy_stat.st_size == src_stat.st_size and copy_stat.st_mtime >= src_stat.st_mtime

    @objc.python_method
    def _load_originals(self) -> dict[str, str]:
        """Load the persisted display-UUID -> original-path map (``{}`` if absent/corrupt)."""
        path = self._cache_dir / _ORIGINALS_FILE
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            log.warning("could not read %s; starting with no remembered originals: %s", path, exc)
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.warning("malformed JSON in %s; ignoring remembered originals", path)
            return {}
        if not isinstance(data, dict):
            log.warning("%s is not a JSON object; ignoring remembered originals", path)
            return {}
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}

    @objc.python_method
    def _save_originals(self, payload: dict[str, str]) -> bool:
        """Atomically write ``payload`` to originals.json; return whether it succeeded.

        The caller commits ``payload`` to ``_persisted`` only on ``True`` -- so the
        in-memory disk-consistent map never claims a write that didn't land, and a
        transient failure is retried on the next render. Lighter touch than the
        flock'd store (this is cache), but still atomic so a concurrent read never
        sees a partial file (DECISIONS.md §7).
        """
        path = self._cache_dir / _ORIGINALS_FILE
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        tmp_path: Path | None = None
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
            )
            tmp_path = Path(tmp_name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            tmp_path.replace(path)
            return True
        except OSError as exc:
            log.warning("could not persist %s: %s", path, exc)
            if tmp_path is not None:
                with contextlib.suppress(OSError):
                    tmp_path.unlink()
            return False

    @objc.python_method
    def _copy_original(self, src: Path, dst: Path) -> bool:
        """Byte-copy the original wallpaper to ``dst``; return whether it succeeded.

        The copy keeps the original's bytes under a fixed ``.png`` name; ``NSImage``
        reads it by content, not extension, so a HEIC/JPEG original still loads.
        Written to a temp file then atomically replaced, so a mid-copy failure (disk
        full, I/O error) leaves any existing copy intact rather than truncating the
        last recoverable fallback. The source mtime is preserved (``copy2``) so
        :meth:`_copy_is_current` reads ``copy.mtime == src.mtime`` for an unchanged
        source. A failure is logged and returns ``False`` (the caller then leaves the
        map unchanged) -- not raised, the mode is best-effort.
        """
        tmp_path: Path | None = None
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=dst.name + ".", suffix=".tmp", dir=str(dst.parent)
            )
            os.close(fd)
            tmp_path = Path(tmp_name)
            shutil.copy2(src, tmp_path)  # content + mtime (see _copy_is_current)
            tmp_path.replace(dst)
            return True
        except OSError as exc:
            log.warning("could not cache original wallpaper copy %s -> %s: %s", src, dst, exc)
            if tmp_path is not None:
                with contextlib.suppress(OSError):
                    tmp_path.unlink()
            return False

    @objc.python_method
    def _render_png(
        self, text: str, screen: object, base_path: str, anchor: str, font_size: int | str
    ) -> Path:
        """Composite the label onto ``base_path``; write and return the PNG path.

        Raises:
            ValueError: If a pixel-backed bitmap could not be allocated/encoded, or
                the base image could not be drawn (so the caller skips the set
                rather than replacing the wallpaper with an empty backdrop).
            OSError: If the PNG could not be written.
        """
        frame = screen.frame()
        scale = float(screen.backingScaleFactor())
        px_w = round(float(frame.size.width) * scale)
        px_h = round(float(frame.size.height) * scale)
        if px_w <= 0 or px_h <= 0:
            raise ValueError(f"non-positive pixel size {px_w}x{px_h}")

        font_pt = geometry.wallpaper_font_size(
            (float(frame.size.width), float(frame.size.height)), font_size
        )
        font_px = float(font_pt) * scale
        if font_size == "auto":
            # Preserve the historical absolute readability floor (a pixel floor, so it
            # holds on 1x displays/projectors too); explicit int sizes are honored.
            font_px = max(_WALLPAPER_AUTO_FLOOR_PX, font_px)

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
            self._draw_composite(text, base_path, px_w, px_h, anchor, font_px)
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
        self, text: str, base_path: str, px_w: int, px_h: int, anchor: str, font_px: float
    ) -> None:
        """Draw the base wallpaper, then the anchored label.

        Raises:
            ValueError: if the base image can't be loaded -- we never paint a black
                backdrop and set it over the user's wallpaper (DECISIONS.md §7).
        """
        from AppKit import NSFontAttributeName, NSForegroundColorAttributeName

        full = NSMakeRect(0.0, 0.0, float(px_w), float(px_h))
        image = NSImage.alloc().initWithContentsOfFile_(base_path)
        if image is None:
            raise ValueError(f"could not load base wallpaper image {base_path!r}")
        image.drawInRect_fromRect_operation_fraction_(
            full, NSZeroRect, NSCompositingOperationCopy, 1.0
        )

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
    def _connected_composite_keys(self) -> set[str]:
        """Return the composite filename keys (``_screen_key``) of connected screens."""
        return {self._screen_key(screen) for screen in NSScreen.screens() or []}

    @objc.python_method
    def _live_cache_wallpapers(self) -> set[Path]:
        """Return cache PNGs currently applied as a connected screen's wallpaper.

        Protects the live-applied composite from the TTL sweep even when its
        filename keys off a now-stale ``CGDirectDisplayID`` (id churn across a
        restart/hot-plug): WallpaperAgent may still point at last session's
        ``display-<old id>.png`` for a display we have not revisited.
        """
        live: set[Path] = set()
        for screen in NSScreen.screens() or []:
            current = self._current_desktop_path(screen)
            if current and self._is_ours(current):
                live.add(Path(current))
        return live

    @objc.python_method
    def _purge(self) -> None:
        """Sweep our own stale cache files (TTL/LRU); never touch anything else.

        Keeps: composites for currently-connected displays (covers the one we just
        wrote) and any cache PNG still applied as a screen's live wallpaper (even
        under a stale display id); **every** ``original-<uuid>.png`` still referenced
        by ``originals.json`` (a known backup -- never evict the only base for a
        display that may reconnect, even after months unplugged; the map is tiny).
        Among the rest -- composites of long-gone displays and *unreferenced* orphan
        copies -- evicts only our own ``*.png`` (never a foreign file) not touched
        within :data:`_CACHE_TTL_DAYS`. ``originals.json`` and any non-``.png`` file
        are left intact. Logged at DEBUG.
        """
        try:
            composite_keys = self._connected_composite_keys()
            live = self._live_cache_wallpapers()
        except (ValueError, RuntimeError) as exc:
            log.debug("wallpaper cache purge skipped (no display topology): %s", exc)
            return
        keep: set[Path] = set(live)
        keep |= {self._cache_dir / f"{key}.png" for key in composite_keys}
        # Keep every referenced original (connected or not) -- it's the backup that
        # makes reconnect-after-restart recoverable; only truly orphaned copies (no
        # json entry) are eviction candidates. This also means UUID-resolution health
        # never gates original eviction. Read the referenced set fresh from disk
        # (union our own map) so a peer instance's entry written after we started is
        # honored, not evicted from a stale snapshot.
        referenced = set(self._persisted) | set(self._load_originals())
        keep |= {self._original_copy_path(uuid) for uuid in referenced}
        # Also keep any cache copy currently in active use as a recovered base (seeded
        # into `_originals` by branch (c)/(d)), so a copy recovered while originals.json
        # was missing/corrupt isn't deleted as an orphan right after we relied on it.
        keep |= {Path(v) for v in self._originals.values() if self._is_ours(v)}
        try:
            files = [(p, p.stat().st_mtime) for p in self._cache_dir.iterdir() if p.is_file()]
        except OSError as exc:
            log.debug("wallpaper cache purge skipped: %s", exc)
            return
        # Only our own naming (display-*/main/original-*.png) is ever a candidate, so
        # a foreign PNG dropped in the cache dir is never deleted.
        candidates = [(p, m) for (p, m) in files if _is_managed_png(p.name)]
        for path in _select_evictions(candidates, keep, time.time(), _CACHE_TTL_DAYS * 86400.0):
            try:
                path.unlink()
                log.debug("evicted stale wallpaper cache file %s", path)
            except OSError as exc:
                log.debug("could not evict %s: %s", path, exc)

    @objc.python_method
    def _set_wallpaper(self, path: Path, screen: object) -> None:
        """Set ``path`` as ``screen``'s desktop image (best-effort, logged on failure)."""
        url = NSURL.fileURLWithPath_(str(path))
        workspace = NSWorkspace.sharedWorkspace()
        ok, error = workspace.setDesktopImageURL_forScreen_options_error_(url, screen, {}, None)
        if not ok:
            log.warning("setDesktopImageURL failed for %s: %s", path, error)
