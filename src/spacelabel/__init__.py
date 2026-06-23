"""spacelabel — label macOS Spaces (virtual desktops) by their stable UUID.

The label is bound to the Space's **UUID**, not its position, so it follows the
desktop through any reorder (the core differentiator over WhichSpace). See
``DESIGN.md`` for the architecture and ``DECISIONS.md`` for the rationale behind
every locked choice.

The package exposes a single console entry point, ``spacelabel`` (see
:mod:`spacelabel.cli`); the long-lived menu-bar agent is the ``spacelabel agent``
subcommand. Every other subcommand is a one-shot CLI action sharing the same
read/store layers.
"""

from __future__ import annotations

import logging
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

__all__ = ["APP_NAME", "BUNDLE_ID", "__version__"]

#: Human-facing application / package name; also the on-disk data + log dir name.
APP_NAME = "spacelabel"

#: Reverse-DNS identifier — the single source of truth, reused verbatim as the
#: LaunchAgent ``Label``, the plist filename, and the ``os_log`` subsystem.
BUNDLE_ID = "dev.mcsim.spacelabel"


def _version_from_app_bundle() -> str | None:
    """Return the version from the enclosing ``.app`` Info.plist, or ``None``.

    Inside the frozen py2app ``spacelabel.app`` bundle the installed package metadata
    is absent (py2app does not bundle ``*.dist-info``), so
    :func:`importlib.metadata.version` raises. The bundle's ``Info.plist`` carries
    ``CFBundleShortVersionString``, stamped at build time from this same
    ``pyproject.toml`` value, so it is the authoritative version there — no second
    hardcoded source. Returns ``None`` when not running from such a bundle.
    """
    import plistlib
    from pathlib import Path
    from xml.parsers.expat import ExpatError

    # …/spacelabel.app/Contents/MacOS/<exe> -> …/spacelabel.app/Contents/Info.plist
    info = Path(sys.executable).resolve().parent.parent / "Info.plist"
    try:
        with info.open("rb") as handle:
            plist = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ExpatError, ValueError):
        # ExpatError: a detected-as-XML but broken Info.plist must not crash package import
        # (this runs at import time via the __version__ fallback below).
        return None
    if not isinstance(plist, dict) or plist.get("CFBundleIdentifier") != BUNDLE_ID:
        # Only trust OUR bundle's version. A source checkout run under some *other*
        # app-bundled interpreter would otherwise borrow that host app's version.
        return None
    value = plist.get("CFBundleShortVersionString")
    return value if isinstance(value, str) and value else None


#: Package version — read from installed metadata so pyproject.toml is the
#: single source of truth and release-please bumps exactly one place. Inside the
#: frozen .app bundle (no metadata) fall back to the Info.plist version.
try:
    __version__ = _pkg_version(__name__)
except PackageNotFoundError:  # not installed: a source clone, or the frozen .app bundle
    __version__ = _version_from_app_bundle() or "0.0.0.dev0"

# Per DESIGN.md §8.2 and the stdlib logging HOWTO: a library never configures
# logging. Attach one NullHandler at import so library log records are dropped
# until the entry point calls spacelabel.logging_setup.setup_logging().
logging.getLogger(__name__).addHandler(logging.NullHandler())
