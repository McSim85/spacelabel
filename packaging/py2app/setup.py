"""py2app build configuration for ``spacelabel.app`` (build-time only).

Invoked by ``tools/build_app.sh`` from this directory::

    python setup.py py2app --dist-dir <dist> --bdist-base <build>

py2app is a *build-time* dependency only -- it is never added to the project's
runtime dependencies. The
resulting bundle is self-contained (it embeds ``Python.framework`` + PyObjC + click),
so the Homebrew cask can ship it to machines without the dev environment.

The version is read from the *installed* package metadata (the build venv installs
the project), keeping ``pyproject.toml`` the single source of truth. The icon path is
passed via ``SPACELABEL_ICNS`` (``tools/make_icon.py`` builds it); absent -> no icon.
"""

from __future__ import annotations

import os
from importlib.metadata import version

from setuptools import setup

VERSION = version("spacelabel")
ICNS = os.environ.get("SPACELABEL_ICNS") or None

PLIST = {
    "CFBundleName": "spacelabel",
    "CFBundleDisplayName": "spacelabel",
    "CFBundleExecutable": "spacelabel",
    "CFBundleIdentifier": "dev.mcsim.spacelabel",
    "CFBundleShortVersionString": VERSION,
    "CFBundleVersion": VERSION,
    # Accessory (menu-bar) app: no Dock icon, no app menu -- preserves the
    # NSApplicationActivationPolicyAccessory behavior the agent sets in code for dev
    # runs, now declared in the bundle.
    "LSUIElement": True,
    "NSHumanReadableCopyright": "MIT © Max Kramarenko",
}

OPTIONS: dict[str, object] = {
    "argv_emulation": False,  # real argv so the same exe works as a CLI on PATH
    "plist": PLIST,
    "packages": ["spacelabel", "click"],
    # spacelabel imports the PyObjC frameworks lazily inside functions, so py2app's
    # static modulegraph misses them -- list them explicitly (verified Phase-6 probe).
    "includes": [
        "objc",
        "Foundation",
        "AppKit",
        "Quartz",
        "CoreText",
        "CoreFoundation",
        "PyObjCTools",
        "PyObjCTools.AppHelper",
    ],
}
if ICNS:
    OPTIONS["iconfile"] = ICNS

setup(
    app=["launcher.py"],
    name="spacelabel",
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
