"""Bundle entry point: run the spacelabel CLI/agent as the app's main executable.

py2app makes this the bundle's main executable, so the running process *is* the
``spacelabel.app`` bundle and macOS attributes TCC (Accessibility) to the bundle's
``CFBundleIdentifier`` (``dev.mcsim.spacelabel``) -- a stable, *named* identity. This
is the whole point of the bundle: without it the agent runs as the shared interpreter
so Accessibility shows "python3.x" rather than "spacelabel".
The same exe is both the agent (``… agent``, what the LaunchAgent runs) and the CLI
(the cask symlinks it onto PATH).
"""

from __future__ import annotations

import os
import sys

from spacelabel import BUNDLE_ID
from spacelabel.cli import main


def _opened_from_finder(argv: list[str]) -> bool:
    """Return True when LaunchServices ("Open"/double-click) launched the bundle, no subcommand.

    A Finder/``open`` launch starts the bundle with no CLI args and sets
    ``XPC_SERVICE_NAME`` to ``application.<our-bundle-id>.<n>`` (empirically verified on
    Tahoe; legacy macOS instead passes a ``-psn_<psn>`` argv token). The launchd
    LaunchAgent (``… agent``) and the PATH CLI shim (``… <subcommand>``) always pass a real
    argument, so they return early here.

    The XPC marker must contain **our own** ``BUNDLE_ID``: ``XPC_SERVICE_NAME`` is inherited
    by child processes, so a shell spawned by *another* GUI app carries that app's id (e.g.
    ``application.com.apple.Terminal.*``), and a plain shell carries ``0``. Matching only
    ``application.<BUNDLE_ID>`` means a bare ``spacelabel`` typed anywhere still prints
    ``--help`` -- the agent run loop can never hijack a no-arg CLI invocation.
    """
    has_subcommand = any(not a.startswith("-psn_") for a in argv[1:])
    if has_subcommand:
        return False
    if any(a.startswith("-psn_") for a in argv[1:]):
        return True  # legacy LaunchServices Process-Serial-Number token
    xpc = os.environ.get("XPC_SERVICE_NAME", "")
    return xpc == f"application.{BUNDLE_ID}" or xpc.startswith(f"application.{BUNDLE_ID}.")


if __name__ == "__main__":
    if _opened_from_finder(sys.argv):
        # Opened from Finder/Applications: the user means "run the menu-bar app", so start
        # the agent rather than dispatching the CLI (which would print --help into a void
        # under LSUIElement). The single-instance lock makes a duplicate launch bow out.
        sys.argv = [sys.argv[0], "agent"]
    else:
        # Drop any legacy -psn_ token so click never sees it as a bad argument.
        sys.argv = [sys.argv[0], *(a for a in sys.argv[1:] if not a.startswith("-psn_"))]
    main(prog_name="spacelabel")
