"""LaunchAgent plist generation + packaging-template sync (DESIGN.md §9.2, DECISIONS.md 6.4)."""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from spacelabel import BUNDLE_ID, install
from spacelabel.install import build_launch_agent, render_plist

HOME = Path("/Users/alex")
SHIM = HOME / ".local" / "bin" / "spacelabel"
PACKAGING_PLIST = Path(__file__).resolve().parents[1] / "packaging" / "dev.mcsim.spacelabel.plist"


def test_build_launch_agent_shape():
    d = build_launch_agent(HOME, SHIM)
    assert d["Label"] == BUNDLE_ID
    assert d["ProgramArguments"] == [str(SHIM), "agent"]
    assert d["LimitLoadToSessionType"] == "Aqua"
    assert d["RunAtLoad"] is True
    assert d["KeepAlive"] == {"SuccessfulExit": False}
    assert d["ProcessType"] == "Interactive"
    # Both streams -> the single boot-catch file (NOT agent.log, which the
    # RotatingFileHandler owns alone — no launchd double-writer).
    assert d["StandardOutPath"] == str(HOME / "Library/Logs/spacelabel/agent.boot.log")
    assert d["StandardErrorPath"] == str(HOME / "Library/Logs/spacelabel/agent.boot.log")


def test_render_plist_roundtrips():
    rendered = render_plist(HOME, SHIM)
    assert plistlib.loads(rendered) == build_launch_agent(HOME, SHIM)


def test_packaging_template_stays_in_sync():
    # The committed packaging reference (human-facing) must agree, key-for-key, with
    # what install.py generates in code (the runtime source of truth).
    text = PACKAGING_PLIST.read_text(encoding="utf-8").replace("__HOME__", str(HOME))
    parsed = plistlib.loads(text.encode("utf-8"))
    assert parsed == build_launch_agent(HOME, SHIM)


def test_resolve_install_shim_requires_canonical_pipx_path(monkeypatch):
    canonical = Path.home() / ".local" / "bin" / "spacelabel"
    # Canonical pipx shim exists -> use it.
    monkeypatch.setattr(install.Path, "exists", lambda self: self == canonical)
    assert install._resolve_install_shim() == canonical
    # Canonical absent -> REFUSE (never persist a transient PATH/venv path into the
    # login agent), with a clear "install via pipx first" error.
    monkeypatch.setattr(install.Path, "exists", lambda _self: False)
    with pytest.raises(install.InstallError, match="pipx"):
        install._resolve_install_shim()
