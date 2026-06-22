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


def _stale_plist_bytes(home: Path, shim: Path) -> bytes:
    """Return a pre-migration plist (old split log paths) for ``shim``."""
    d = build_launch_agent(home, shim)
    d["StandardOutPath"] = str(home / "Library/Logs/spacelabel/agent.log")
    d["StandardErrorPath"] = str(home / "Library/Logs/spacelabel/agent.err.log")
    return plistlib.dumps(d)


def test_refresh_plist_noop_when_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "plist_path", lambda: tmp_path / "absent.plist")
    assert install.refresh_plist_if_stale() is False


def test_refresh_plist_rewrites_stale_preserving_shim(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))  # Path.home() -> tmp_path
    home = Path(tmp_path)
    shim = home / ".local" / "bin" / "spacelabel"
    plist = tmp_path / "agent.plist"
    plist.write_bytes(_stale_plist_bytes(home, shim))
    monkeypatch.setattr(install, "plist_path", lambda: plist)

    assert install.refresh_plist_if_stale() is True
    refreshed = plistlib.loads(plist.read_bytes())
    assert refreshed == build_launch_agent(home, shim)  # migrated to agent.boot.log
    assert refreshed["ProgramArguments"][0] == str(shim)  # shim preserved, not repointed
    assert not list(tmp_path.glob(".agent.plist.*.tmp"))  # atomic write left no temp
    assert install.refresh_plist_if_stale() is False  # idempotent


def test_refresh_plist_preserves_extra_program_arguments(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = Path(tmp_path)
    shim = home / ".local" / "bin" / "spacelabel"
    plist = tmp_path / "agent.plist"
    d = build_launch_agent(home, shim)
    # A hand-edited plist with extra ProgramArguments + the old (pre-migration) paths.
    d["ProgramArguments"] = [str(shim), "agent", "--config", "/etc/spacelabel.json"]
    d["StandardOutPath"] = str(home / "Library/Logs/spacelabel/agent.log")
    d["StandardErrorPath"] = str(home / "Library/Logs/spacelabel/agent.err.log")
    plist.write_bytes(plistlib.dumps(d))
    monkeypatch.setattr(install, "plist_path", lambda: plist)

    assert install.refresh_plist_if_stale() is True
    refreshed = plistlib.loads(plist.read_bytes())
    # Extra args preserved; only the std-stream paths migrated.
    assert refreshed["ProgramArguments"] == [str(shim), "agent", "--config", "/etc/spacelabel.json"]
    boot = str(home / "Library/Logs/spacelabel/agent.boot.log")
    assert refreshed["StandardOutPath"] == boot
    assert refreshed["StandardErrorPath"] == boot


def test_refresh_plist_noop_when_current(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = Path(tmp_path)
    shim = home / ".local" / "bin" / "spacelabel"
    plist = tmp_path / "agent.plist"
    plist.write_bytes(render_plist(home, shim))  # already current
    monkeypatch.setattr(install, "plist_path", lambda: plist)
    assert install.refresh_plist_if_stale() is False


def test_refresh_plist_handles_unparseable_plist(tmp_path, monkeypatch):
    plist = tmp_path / "agent.plist"
    plist.write_bytes(b"not a plist")  # no format detected -> InvalidFileException
    monkeypatch.setattr(install, "plist_path", lambda: plist)
    assert install.refresh_plist_if_stale() is False  # logged, not raised
    assert plist.read_bytes() == b"not a plist"  # left intact


def test_refresh_plist_handles_truncated_xml_plist(tmp_path, monkeypatch):
    # Detected-as-XML but syntactically broken -> expat ExpatError (not
    # InvalidFileException); must still be best-effort, not a startup crash.
    plist = tmp_path / "agent.plist"
    plist.write_bytes(b'<?xml version="1.0"?>\n<plist version="1.0"><dict><key>Label')
    monkeypatch.setattr(install, "plist_path", lambda: plist)
    assert install.refresh_plist_if_stale() is False


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
