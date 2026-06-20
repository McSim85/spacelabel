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
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

__all__ = ["APP_NAME", "BUNDLE_ID", "__version__"]

#: Human-facing application / package name; also the on-disk data + log dir name.
APP_NAME = "spacelabel"

#: Reverse-DNS identifier — the single source of truth, reused verbatim as the
#: LaunchAgent ``Label``, the plist filename, and the ``os_log`` subsystem.
BUNDLE_ID = "dev.mcsim.spacelabel"

#: Package version — read from installed metadata so pyproject.toml is the
#: single source of truth and release-please bumps exactly one place.
try:
    __version__ = _pkg_version(__name__)
except PackageNotFoundError:  # running from a non-installed clone
    __version__ = "0.0.0.dev0"

# Per DESIGN.md §8.2 and the stdlib logging HOWTO: a library never configures
# logging. Attach one NullHandler at import so library log records are dropped
# until the entry point calls spacelabel.logging_setup.setup_logging().
logging.getLogger(__name__).addHandler(logging.NullHandler())
