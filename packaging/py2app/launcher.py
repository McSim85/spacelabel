"""Bundle entry point: run the spacelabel CLI/agent as the app's main executable.

py2app makes this the bundle's main executable, so the running process *is* the
``spacelabel.app`` bundle and macOS attributes TCC (Accessibility) to the bundle's
``CFBundleIdentifier`` (``dev.mcsim.spacelabel``) -- a stable, *named* identity. This
is the whole point of the bundle: under pipx the agent ran as the shared Homebrew
``python`` app-stub, so Accessibility showed "python3.x" and the grant never bound to
the agent (DECISIONS.md §6 / §9.5). The same exe is both the agent (``… agent``, what
the LaunchAgent runs) and the CLI (the cask symlinks it onto PATH).
"""

from __future__ import annotations

from spacelabel.cli import main

if __name__ == "__main__":
    main()
