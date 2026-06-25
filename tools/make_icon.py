#!/usr/bin/env python3
r"""Generate ``spacelabel.icns`` from a 1024x1024 master PNG (macOS built-ins only).

Build-time only (not a runtime dependency). Emits the standard iconset sizes from the
master with ``sips`` and packs them with ``iconutil``. When the master is absent a
simple on-brand placeholder is drawn
(the menu-bar "pills" motif -- three rounded pills, the middle one at full alpha to
echo how the agent marks the current Space) and written to the master path so real
artwork can replace it later with no code change.

Usage::

    python tools/make_icon.py --master packaging/icon/spacelabel-1024.png \\
        --icns packaging/icon/spacelabel.icns

The drawing uses the same offscreen ``NSBitmapImageRep`` pattern as
``agent/overlay.py`` (PyObjC is present in the build venv); no window server needed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

#: Standard macOS iconset point sizes; each emitted at @1x and @2x.
_ICONSET_SIZES = (16, 32, 128, 256, 512)
_MASTER_SIZE = 1024


def draw_placeholder(size: int = _MASTER_SIZE) -> bytes:
    """Render a placeholder app icon and return PNG bytes.

    A rounded-rect badge with three pills (middle at full alpha) -- the spacelabel
    menu-bar motif. Drawn offscreen via ``NSBitmapImageRep``; raises if AppKit is
    unavailable (this is a macOS build tool).
    """
    from AppKit import (
        NSBezierPath,
        NSBitmapImageFileTypePNG,
        NSBitmapImageRep,
        NSColor,
        NSDeviceRGBColorSpace,
        NSGraphicsContext,
    )
    from Foundation import NSMakeRect

    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(  # noqa: E501
        None, size, size, 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0
    )
    if rep is None:
        raise RuntimeError("could not allocate NSBitmapImageRep for the icon")

    context = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    if context is None:
        raise RuntimeError("could not create a graphics context for the icon")
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(context)
    try:
        inset = size * 0.10
        badge = NSMakeRect(inset, inset, size - 2 * inset, size - 2 * inset)
        corner = size * 0.225  # ~ macOS squircle proportion
        NSColor.colorWithDeviceRed_green_blue_alpha_(0.15, 0.17, 0.24, 1.0).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(badge, corner, corner).fill()

        pill_w = size * 0.17
        pill_h = size * 0.30
        gap = size * 0.06
        total_w = 3 * pill_w + 2 * gap
        x0 = (size - total_w) / 2.0
        y = (size - pill_h) / 2.0
        pill_corner = pill_w * 0.38
        for index in range(3):
            x = x0 + index * (pill_w + gap)
            rect = NSMakeRect(x, y, pill_w, pill_h)
            alpha = 1.0 if index == 1 else 0.4  # middle pill = "current" (DESIGN §9.4)
            NSColor.colorWithDeviceRed_green_blue_alpha_(1.0, 1.0, 1.0, alpha).set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                rect, pill_corner, pill_corner
            ).fill()
    finally:
        NSGraphicsContext.restoreGraphicsState()

    png = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
    if png is None:
        raise RuntimeError("could not encode the icon as PNG")
    return bytes(png)


def ensure_master(master: Path) -> None:
    """Write a placeholder master PNG when one is not already checked in."""
    if master.exists():
        print(f"using existing master {master}")
        return
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_bytes(draw_placeholder(_MASTER_SIZE))
    print(f"generated placeholder master {master} ({_MASTER_SIZE}x{_MASTER_SIZE})")


def _sips_resize(master: Path, size: int, dest: Path) -> None:
    """Resize ``master`` to ``size``x``size`` into ``dest`` via ``sips``."""
    subprocess.run(
        ["sips", "-z", str(size), str(size), str(master), "--out", str(dest)],
        check=True,
        capture_output=True,
        text=True,
    )


def build_icns(master: Path, icns: Path) -> None:
    """Build ``icns`` from ``master`` via an ``.iconset`` (``sips`` + ``iconutil``)."""
    iconset = icns.with_suffix(".iconset")
    if iconset.exists():
        for stale in iconset.iterdir():
            stale.unlink()
    else:
        iconset.mkdir(parents=True)
    for size in _ICONSET_SIZES:
        _sips_resize(master, size, iconset / f"icon_{size}x{size}.png")
        _sips_resize(master, size * 2, iconset / f"icon_{size}x{size}@2x.png")
    icns.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(icns)],
        check=True,
        capture_output=True,
        text=True,
    )
    print(f"wrote {icns}")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ensure the master exists, then build the icns."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--master",
        type=Path,
        default=Path("packaging/icon/spacelabel-1024.png"),
        help="path to the 1024x1024 master PNG (generated if absent)",
    )
    parser.add_argument(
        "--icns",
        type=Path,
        default=Path("packaging/icon/spacelabel.icns"),
        help="output .icns path",
    )
    parser.add_argument(
        "--placeholder-only",
        action="store_true",
        help="only (re)generate the master placeholder; do not build the icns",
    )
    args = parser.parse_args(argv)

    try:
        ensure_master(args.master)
        if not args.placeholder_only:
            build_icns(args.master, args.icns)
    except (subprocess.CalledProcessError, OSError, RuntimeError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        print(f"icon build failed: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
