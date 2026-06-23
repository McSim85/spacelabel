"""LaunchAgent plist generation + packaging-template sync (DESIGN.md §9.2, DECISIONS.md 6.4)."""

from __future__ import annotations

import fcntl
import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from spacelabel import BUNDLE_ID, install
from spacelabel.install import build_launch_agent, render_plist


def _hold_lock(path: Path, content: str):
    """Open ``path``, write ``content``, and hold an exclusive flock on a separate fd.

    Models a running agent for the flock-detection probe: held-ness comes from the flock
    (a distinct open file description, so the probe's own fd is correctly denied), and the
    written ``<pid>``/``<config>`` lines are what the probe reads back.
    """
    handle = path.open("w")
    handle.write(content)
    handle.flush()
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle


HOME = Path("/Users/alex")
SHIM = HOME / ".local" / "bin" / "spacelabel"
# Representative cask-installed bundle exe (the runtime resolves the real one).
APP_EXE = Path("/Applications/spacelabel.app/Contents/MacOS/spacelabel")
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
    # what install.py generates in code (the runtime source of truth). The program path
    # is the __APP_EXE__ token (the cask bundle exe), resolved at install time.
    text = (
        PACKAGING_PLIST.read_text(encoding="utf-8")
        .replace("__HOME__", str(HOME))
        .replace("__APP_EXE__", str(APP_EXE))
    )
    parsed = plistlib.loads(text.encode("utf-8"))
    assert parsed == build_launch_agent(HOME, APP_EXE)


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


def test_resolve_install_shim_prefers_app_bundle(monkeypatch):
    # Running from the cask bundle -> point the LaunchAgent at the bundle exe so the
    # agent process IS the bundle (named Accessibility identity, DECISIONS.md §6).
    monkeypatch.setattr(install, "_enclosing_app_exe", lambda: APP_EXE)
    assert install._resolve_install_shim() == APP_EXE


def test_resolve_install_shim_falls_back_to_pipx_shim(monkeypatch):
    canonical = Path.home() / ".local" / "bin" / "spacelabel"
    monkeypatch.setattr(install, "_enclosing_app_exe", lambda: None)  # not bundled
    monkeypatch.setattr(install.Path, "exists", lambda self: self == canonical)
    assert install._resolve_install_shim() == canonical


def test_resolve_install_shim_refuses_when_unresolved(monkeypatch):
    # No bundle and no pipx shim -> REFUSE (never persist a transient PATH/venv path).
    monkeypatch.setattr(install, "_enclosing_app_exe", lambda: None)
    monkeypatch.setattr(install.Path, "exists", lambda _self: False)
    with pytest.raises(install.InstallError, match="cask"):
        install._resolve_install_shim()


def test_resolve_install_shim_uses_source_venv_shim(tmp_path, monkeypatch):
    # F1: a DURABLE uv/.venv source install (no bundle, no pipx shim) uses the console script
    # beside the interpreter (.venv/bin/spacelabel next to .venv/bin/python) -- an absolute,
    # durable path launchd can exec -- so contributors can run `spacelabel install` locally.
    # (pytest's tmp_path is under $TMPDIR, so stub the ephemerality check to model a durable
    # venv; the real ephemeral detection is covered by the two tests below.)
    monkeypatch.setattr(install, "_enclosing_app_exe", lambda: None)
    monkeypatch.setattr(install, "_canonical_shim", lambda: tmp_path / "no-pipx" / "spacelabel")
    monkeypatch.setattr(install, "_is_ephemeral_path", lambda _p: False)
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("")
    shim = venv_bin / "spacelabel"
    shim.write_text("#!/bin/sh\n")
    monkeypatch.setattr(install.sys, "executable", str(venv_bin / "python"))
    # The resolved durable target is persisted (canonicalizes a temp/cache symlink to its
    # real venv path); for a real file it's just the resolved path.
    assert install._resolve_install_shim() == shim.resolve()


def test_resolve_install_shim_rejects_ephemeral_runner_shim(tmp_path, monkeypatch):
    # F1 follow-on: a DISPOSABLE runner venv (uvx / pipx run -> a .cache path) must NOT be
    # persisted into the LaunchAgent -> refuse rather than point at a path that may vanish.
    monkeypatch.setattr(install, "_enclosing_app_exe", lambda: None)
    monkeypatch.setattr(install, "_canonical_shim", lambda: tmp_path / "no-pipx" / "spacelabel")
    cache_bin = tmp_path / ".cache" / "uv" / "venv" / "bin"  # ".cache" component -> ephemeral
    cache_bin.mkdir(parents=True)
    (cache_bin / "python").write_text("")
    (cache_bin / "spacelabel").write_text("#!/bin/sh\n")
    monkeypatch.setattr(install.sys, "executable", str(cache_bin / "python"))
    with pytest.raises(install.InstallError, match="cask"):
        install._resolve_install_shim()


def test_is_ephemeral_path_flags_cache_and_temp_not_project_venv():
    home = Path.home()
    assert install._is_ephemeral_path(home / ".cache/uv/x/bin/spacelabel") is True  # uvx
    assert install._is_ephemeral_path(home / ".local/pipx/.cache/x/bin/spacelabel") is True
    assert install._is_ephemeral_path(home / "Library/Caches/x/bin/spacelabel") is True
    assert install._is_ephemeral_path(home / "code/proj/.venv/bin/spacelabel") is False


def test_enclosing_app_exe_detects_bundle(tmp_path, monkeypatch):
    # A spacelabel.app whose Resources host this package -> resolve Contents/MacOS exe.
    app = tmp_path / "spacelabel.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    exe = macos / "spacelabel"
    exe.write_text("#!/bin/sh\n")
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": BUNDLE_ID}))
    pkg_file = app / "Contents" / "Resources" / "lib" / "python3.14" / "spacelabel" / "install.py"
    pkg_file.parent.mkdir(parents=True)
    pkg_file.write_text("")
    monkeypatch.setattr(install, "__file__", str(pkg_file))
    monkeypatch.setattr(install.sys, "executable", str(tmp_path / "unrelated" / "python"))
    monkeypatch.setattr(install.sys, "argv", [str(pkg_file)])
    assert install._enclosing_app_exe() == exe


def test_enclosing_app_exe_none_when_not_bundled(monkeypatch, tmp_path):
    plain = tmp_path / "bin" / "python"
    plain.parent.mkdir(parents=True)
    plain.write_text("")
    monkeypatch.setattr(
        install, "__file__", str(tmp_path / "site-packages" / "spacelabel" / "install.py")
    )
    monkeypatch.setattr(install.sys, "executable", str(plain))
    monkeypatch.setattr(install.sys, "argv", [str(plain)])
    assert install._enclosing_app_exe() is None


def test_enclosing_app_exe_skips_inner_helper_app(tmp_path, monkeypatch):
    # sys.executable points at py2app's embedded Python.app helper (an inner .app with no
    # spacelabel exe) -> must KEEP scanning out to the real spacelabel.app, not give up.
    app = tmp_path / "spacelabel.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    exe = macos / "spacelabel"
    exe.write_text("#!/bin/sh\n")
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": BUNDLE_ID}))
    helper = (
        app
        / "Contents"
        / "Frameworks"
        / "Python.framework"
        / "Versions"
        / "3.14"
        / "Resources"
        / "Python.app"
        / "Contents"
        / "MacOS"
    )
    helper.mkdir(parents=True)
    (helper / "Python").write_text("")
    monkeypatch.setattr(install.sys, "executable", str(helper / "Python"))
    monkeypatch.setattr(install.sys, "argv", [str(helper / "Python")])
    monkeypatch.setattr(install, "__file__", str(app / "Contents" / "Resources" / "x.py"))
    assert install._enclosing_app_exe() == exe  # found the OUTER spacelabel.app


def test_enclosing_app_exe_rejects_foreign_bundle(tmp_path, monkeypatch):
    # F4: an OTHER app that merely contains a Contents/MacOS/spacelabel exe (wrong/absent
    # CFBundleIdentifier) must NOT be accepted -> never point the LaunchAgent at a
    # non-spacelabel bundle. Mirrors _version_from_app_bundle's identity gate.
    app = tmp_path / "Other.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / "spacelabel").write_text("#!/bin/sh\n")
    (app / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": "com.example.other"})
    )
    monkeypatch.setattr(install.sys, "executable", str(macos / "spacelabel"))
    monkeypatch.setattr(install.sys, "argv", [str(macos / "spacelabel")])
    monkeypatch.setattr(install, "__file__", str(app / "Contents" / "Resources" / "x.py"))
    assert install._enclosing_app_exe() is None


def test_enclosing_app_exe_handles_broken_xml_info_plist(tmp_path, monkeypatch):
    # A detected-as-XML but syntactically broken Info.plist raises ExpatError -> must be
    # skipped (return None), never crash `spacelabel install` from inside a corrupt wrapper.
    app = tmp_path / "spacelabel.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / "spacelabel").write_text("#!/bin/sh\n")
    (app / "Contents" / "Info.plist").write_bytes(b'<?xml version="1.0"?>\n<plist><dict><key>')
    monkeypatch.setattr(install.sys, "executable", str(macos / "spacelabel"))
    monkeypatch.setattr(install.sys, "argv", [str(macos / "spacelabel")])
    monkeypatch.setattr(install, "__file__", str(app / "Contents" / "Resources" / "x.py"))
    assert install._enclosing_app_exe() is None  # ExpatError swallowed, not raised


# ---- install / uninstall agent lifecycle -----------------------------------


def test_install_agent_mkdir_failure_raises_clean_install_error(tmp_path, monkeypatch):
    # A blocked ~/Library/LaunchAgents or Logs dir -> clean InstallError, not a raw OSError
    # traceback (the function's contract promises InstallError).
    monkeypatch.setattr(install, "_resolve_install_shim", lambda: tmp_path / "spacelabel")
    monkeypatch.setattr(install, "plist_path", lambda: tmp_path / "LaunchAgents" / "x.plist")

    def boom(_self, *a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr(install.Path, "mkdir", boom)
    with pytest.raises(install.InstallError, match="could not create"):
        install.install_agent()


def test_uninstall_agent_raises_when_still_loaded(tmp_path, monkeypatch):
    # bootout "succeeds" (check=False) but the service is STILL loaded -> a real unload
    # failure; surface it and do NOT delete the plist (so a retry / manual bootout has it).
    monkeypatch.setattr(
        install, "_launchctl", lambda *a, **k: SimpleNamespace(returncode=0, stdout="")
    )
    monkeypatch.setattr(install, "_launchctl_service_state", lambda: (True, 4242))  # still loaded
    plist = tmp_path / "x.plist"
    plist.write_text("x")
    monkeypatch.setattr(install, "plist_path", lambda: plist)
    with pytest.raises(install.InstallError, match="still loaded"):
        install.uninstall_agent()
    assert plist.exists()  # left in place for a retry, not a false-success deletion


def test_uninstall_agent_removes_plist_when_unloaded(tmp_path, monkeypatch):
    monkeypatch.setattr(
        install, "_launchctl", lambda *a, **k: SimpleNamespace(returncode=0, stdout="")
    )
    monkeypatch.setattr(install, "_launchctl_service_state", lambda: (False, None))  # unloaded
    plist = tmp_path / "x.plist"
    plist.write_text("x")
    monkeypatch.setattr(install, "plist_path", lambda: plist)
    install.uninstall_agent()  # no raise
    assert not plist.exists()  # removed once confirmed unloaded


# ---- status: agent.lock probe + install/run-state combination --------------


def test_probe_agent_lock_held_released_and_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(
        install.store.StorePaths, "resolve", lambda _cfg=None: SimpleNamespace(directory=tmp_path)
    )
    lock = tmp_path / "agent.lock"
    # No lock file yet -> the agent has never run for this config.
    assert install._probe_agent_lock(None) == (False, None, None)

    # Running agent: holds the flock and records "<pid>\n<config>". The probe detects held
    # via flock (never racing a starting agent, which retries its own acquisition).
    cfg = tmp_path / "config.json"
    holder = _hold_lock(lock, f"12345\n{cfg}\n")
    try:
        assert install._probe_agent_lock(None) == (True, 12345, cfg)  # pid + recorded config
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()

    # flock released (agent gone), but the file remains -> not held.
    assert install._probe_agent_lock(None) == (False, None, None)


def test_probe_lock_path_legacy_pid_only_lock_has_no_config(tmp_path):
    # A legacy pid-only lock (pre-config-recording): held (flock), pid parsed, no config line.
    lock = tmp_path / "agent.lock"
    holder = _hold_lock(lock, "777")
    try:
        assert install._probe_lock_path(lock) == (True, 777, None)
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


@pytest.mark.parametrize(
    ("launchctl", "lock", "installed", "expected"),
    [
        # managed-running: launchd reports a pid; the lock is held by it.
        ((True, 4213), (True, 4213), True, (True, True, True, 4213, True)),
        # foreground-running: lock held, but launchd does not manage it.
        ((False, None), (True, 50803), False, (False, False, True, 50803, False)),
        # installed-not-loaded (and not running).
        ((False, None), (False, None), True, (True, False, False, None, False)),
        # not installed, not running.
        ((False, None), (False, None), False, (False, False, False, None, False)),
    ],
)
def test_agent_status_detail_states(monkeypatch, launchctl, lock, installed, expected):
    monkeypatch.setattr(install, "_launchctl_service_state", lambda: launchctl)
    # A held lock for the DEFAULT query records the default config.json, so it matches.
    monkeypatch.setattr(
        install,
        "_probe_lock_path",
        lambda _p: (lock[0], lock[1], install.store.config_path() if lock[0] else None),
    )
    monkeypatch.setattr(install, "is_installed", lambda: installed)
    st = install.agent_status_detail()  # default config -> folds in launchctl
    assert (st.installed, st.loaded, st.running, st.pid, st.managed) == expected


def test_agent_status_detail_custom_config_ignores_launchctl(monkeypatch, tmp_path):
    # The managed LaunchAgent only runs the DEFAULT config, so a --config query must NOT
    # report the default agent's managed state (it would be a false positive).
    monkeypatch.setattr(
        install, "_launchctl_service_state", lambda: (True, 4213)
    )  # default running
    monkeypatch.setattr(
        install, "_probe_lock_path", lambda _p: (False, None, None)
    )  # this one's not
    monkeypatch.setattr(install, "is_installed", lambda: True)
    st = install.agent_status_detail(tmp_path / "other.json")
    assert (st.installed, st.loaded, st.running, st.pid, st.managed) == (
        False,
        False,
        False,
        None,
        False,
    )


def test_agent_status_detail_default_config_path_is_still_managed(monkeypatch, tmp_path):
    # Passing the canonical default config path explicitly must be recognized as the
    # managed store (compare the resolved dir to data_dir, not `config_path is None`).
    default_dir = tmp_path / "AppSupport" / "spacelabel"
    monkeypatch.setattr(install.store, "data_dir", lambda: default_dir)
    monkeypatch.setattr(install, "_launchctl_service_state", lambda: (True, 4213))
    monkeypatch.setattr(
        install, "_probe_lock_path", lambda _p: (True, 4213, default_dir / "config.json")
    )
    monkeypatch.setattr(install, "is_installed", lambda: True)
    st = install.agent_status_detail(default_dir / "config.json")
    assert (st.installed, st.loaded, st.running, st.pid, st.managed) == (
        True,
        True,
        True,
        4213,
        True,
    )


def test_agent_status_detail_symlinked_default_probes_canonical_lock(monkeypatch, tmp_path):
    # --config is a symlink to the default config.json -> status probes the CANONICAL
    # data_dir/agent.lock and the recorded config resolves to the same file, so a live
    # default agent is seen.
    default_dir = tmp_path / "AppSupport" / "spacelabel"
    default_dir.mkdir(parents=True)
    monkeypatch.setattr(install.store, "data_dir", lambda: default_dir)
    monkeypatch.setattr(install, "_launchctl_service_state", lambda: (False, None))
    monkeypatch.setattr(install, "is_installed", lambda: False)
    (default_dir / "config.json").write_text("{}")
    holder = _hold_lock(default_dir / "agent.lock", f"4242\n{default_dir / 'config.json'}\n")
    try:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        link = elsewhere / "link.json"
        link.symlink_to(default_dir / "config.json")
        st = install.agent_status_detail(link)
        assert st.running is True and st.pid == 4242  # live default agent (resolved match)
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


def test_agent_status_detail_alt_config_not_running_when_default_holds_lock(monkeypatch, tmp_path):
    # F2 (root fix): an alt config kept in the default dir shares data_dir/agent.lock with the
    # default. When the DEFAULT (config.json) agent holds it, `status --config alt.json` must
    # report NOT running -- alt.json was never started (the recorded config disambiguates, so
    # no false positive). installed/loaded/managed also stay False (not the managed config).
    default_dir = tmp_path / "AppSupport" / "spacelabel"
    default_dir.mkdir(parents=True)
    monkeypatch.setattr(install.store, "data_dir", lambda: default_dir)
    monkeypatch.setattr(install, "_launchctl_service_state", lambda: (True, 4213))
    monkeypatch.setattr(install, "is_installed", lambda: True)
    # held by the config.json agent (recorded config disambiguates from alt.json)
    holder = _hold_lock(default_dir / "agent.lock", f"4213\n{default_dir / 'config.json'}\n")
    try:
        st = install.agent_status_detail(default_dir / "alt.json")
        assert (st.installed, st.loaded, st.running, st.managed) == (False, False, False, False)
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


def test_agent_status_detail_alt_config_running_when_alt_holds_lock(monkeypatch, tmp_path):
    # The flip side (no false negative): when an agent STARTED with alt.json holds the shared
    # lock (recording alt.json), `status --config alt.json` reports running + unmanaged.
    default_dir = tmp_path / "AppSupport" / "spacelabel"
    default_dir.mkdir(parents=True)
    monkeypatch.setattr(install.store, "data_dir", lambda: default_dir)
    monkeypatch.setattr(install, "_launchctl_service_state", lambda: (False, None))
    monkeypatch.setattr(install, "is_installed", lambda: False)
    # held by the alt.json agent (it recorded alt.json)
    holder = _hold_lock(default_dir / "agent.lock", f"9001\n{default_dir / 'alt.json'}\n")
    try:
        st = install.agent_status_detail(default_dir / "alt.json")
        assert (st.running, st.managed, st.pid) == (True, False, 9001)
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


def test_agent_status_detail_idle_alt_config_not_marked_installed(monkeypatch, tmp_path):
    # An IDLE alt config in the default dir (no agent holds the lock) must not inherit the
    # default LaunchAgent's installed/loaded just because it shares the dir -> all False.
    default_dir = tmp_path / "AppSupport" / "spacelabel"
    default_dir.mkdir(parents=True)
    monkeypatch.setattr(install.store, "data_dir", lambda: default_dir)
    monkeypatch.setattr(install, "_launchctl_service_state", lambda: (True, None))  # loaded, idle
    monkeypatch.setattr(install, "is_installed", lambda: True)
    st = install.agent_status_detail(default_dir / "alt.json")
    assert (st.installed, st.loaded, st.running, st.managed) == (False, False, False, False)


def test_agent_status_detail_legacy_pid_only_lock_attributed_to_default(monkeypatch, tmp_path):
    # F2: a legacy pid-only lock (no config line, pre-config-recording) is attributed to the
    # DEFAULT config, so an upgrade doesn't hide a still-running default agent.
    default_dir = tmp_path / "AppSupport" / "spacelabel"
    default_dir.mkdir(parents=True)
    monkeypatch.setattr(install.store, "data_dir", lambda: default_dir)
    monkeypatch.setattr(install, "_launchctl_service_state", lambda: (False, None))  # foreground
    monkeypatch.setattr(install, "is_installed", lambda: False)
    holder = _hold_lock(default_dir / "agent.lock", "4242")  # pid only, no config line
    try:
        st = install.agent_status_detail(None)  # default config query
        assert st.running is True and st.pid == 4242  # not hidden across the upgrade
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


def test_unmanaged_default_lock_holder_blocks_any_foreground_holder(monkeypatch, tmp_path):
    # The purge guard is LOCK-level, not config-aware: a foreground agent holding the default
    # store lock blocks the default purge even if it serves an ALT config (it shares the
    # store's labels/displays) -- the data-safety hole the config-aware status check left.
    default_dir = tmp_path / "AppSupport" / "spacelabel"
    default_dir.mkdir(parents=True)
    monkeypatch.setattr(install.store, "data_dir", lambda: default_dir)
    monkeypatch.setattr(install, "_launchctl_service_state", lambda: (False, None))  # not managed
    # a foreground alt.json agent holds the shared default lock
    holder = _hold_lock(default_dir / "agent.lock", f"9001\n{default_dir / 'alt.json'}\n")
    try:
        # blocks despite the alt config (lock-level guard, not config-aware)
        assert install.unmanaged_default_lock_holder() == (True, 9001)
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


def test_unmanaged_default_lock_holder_allows_managed_agent(monkeypatch, tmp_path):
    # The managed launchd agent holding the lock does NOT block (uninstall stops it): its
    # recorded lock pid equals the launchctl pid.
    default_dir = tmp_path / "AppSupport" / "spacelabel"
    default_dir.mkdir(parents=True)
    monkeypatch.setattr(install.store, "data_dir", lambda: default_dir)
    # the managed agent's launchctl pid == the pid recorded in the lock
    monkeypatch.setattr(install, "_launchctl_service_state", lambda: (True, 4242))
    holder = _hold_lock(default_dir / "agent.lock", f"4242\n{default_dir / 'config.json'}\n")
    try:
        assert install.unmanaged_default_lock_holder() == (False, None)  # managed -> allowed
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


def test_unmanaged_default_lock_holder_none_when_lock_free(monkeypatch, tmp_path):
    default_dir = tmp_path / "AppSupport" / "spacelabel"
    default_dir.mkdir(parents=True)
    monkeypatch.setattr(install.store, "data_dir", lambda: default_dir)
    monkeypatch.setattr(install, "_launchctl_service_state", lambda: (False, None))
    assert install.unmanaged_default_lock_holder() == (False, None)  # no lock file -> not held


# ---- uninstall --purge: target resolution + deletion -----------------------


def test_caches_dir():
    home = Path("/Users/alex")
    assert install.caches_dir(home) == home / "Library/Caches/spacelabel"


def _patch_purge_dirs(monkeypatch, tmp_path, *, data_dir):
    monkeypatch.setattr(install.store, "data_dir", lambda: data_dir)
    monkeypatch.setattr(install, "caches_dir", lambda: tmp_path / "Caches/spacelabel")
    monkeypatch.setattr(install, "logs_dir", lambda: tmp_path / "Logs/spacelabel")
    monkeypatch.setattr("spacelabel.completion.installed_completion_files", lambda: [])


def test_purge_targets_default_config_removes_owned_files_not_whole_dir(monkeypatch, tmp_path):
    # F1: the default store deletes the files it OWNS by construction (so a foreign file kept
    # in the dir survives) -- NOT the whole dir wholesale.
    default_dir = tmp_path / "AppSupport" / "spacelabel"
    _patch_purge_dirs(monkeypatch, tmp_path, data_dir=default_dir)
    monkeypatch.setattr(
        "spacelabel.completion.installed_completion_files", lambda: [tmp_path / "x.fish"]
    )
    paths = install.store.StorePaths.resolve(None)
    targets = install.purge_targets(paths, remove_completion=True)
    assert default_dir not in targets  # NOT the whole dir
    for owned in ("config.json", "config.json.lock", "labels.json", "displays.json", "agent.lock"):
        assert (default_dir / owned) in targets  # owned files listed individually
    assert (tmp_path / "Caches/spacelabel") in targets
    assert (tmp_path / "Logs/spacelabel") in targets
    assert (tmp_path / "x.fish") in targets  # completion script included


def test_purge_targets_custom_config_returns_nothing(monkeypatch, tmp_path):
    # F2: a custom --config owns nothing exclusively safe to delete -> empty target list. Its
    # dir isn't ours, and the caches/logs/completions are GLOBAL (the default install's).
    _patch_purge_dirs(monkeypatch, tmp_path, data_dir=tmp_path / "default" / "spacelabel")
    cfg = tmp_path / "shared" / "myconfig.json"
    paths = install.store.StorePaths.resolve(cfg)
    targets = install.purge_targets(paths, remove_completion=True)
    assert targets == []  # nothing in the --config dir, and NOT the global caches/logs
    assert (tmp_path / "Caches/spacelabel") not in targets  # default install's, not this config's
    assert (tmp_path / "Logs/spacelabel") not in targets


def test_purge_targets_custom_config_never_deletes_unrelated_file(monkeypatch, tmp_path):
    # The footgun: `--config ~/.ssh/config` must never schedule ~/.ssh/config (or a
    # sibling labels.json that could be another app's) for deletion -- it purges nothing.
    _patch_purge_dirs(monkeypatch, tmp_path, data_dir=tmp_path / "default" / "spacelabel")
    ssh_config = tmp_path / ".ssh" / "config"
    paths = install.store.StorePaths.resolve(ssh_config)
    assert install.purge_targets(paths, remove_completion=True) == []


def test_purge_targets_alt_config_inside_default_dir_returns_nothing(monkeypatch, tmp_path):
    # An ALTERNATE config kept inside the default dir is not the default store (its config
    # file != config.json), so it purges NOTHING -- never the shared default files/dir.
    default_dir = tmp_path / "AppSupport" / "spacelabel"
    _patch_purge_dirs(monkeypatch, tmp_path, data_dir=default_dir)
    paths = install.store.StorePaths.resolve(default_dir / "alt.json")
    assert install.purge_targets(paths, remove_completion=True) == []

    # ...but the canonical default config.json (explicit path) purges the owned files.
    default_paths = install.store.StorePaths.resolve(default_dir / "config.json")
    targets = install.purge_targets(default_paths, remove_completion=True)
    assert (default_dir / "config.json") in targets
    assert default_dir not in targets  # owned files, not the whole dir


def test_purge_targets_symlinked_default_config_targets_canonical_owned_files(
    monkeypatch, tmp_path
):
    # --config is a symlink resolving to the default config.json -> purge the CANONICAL
    # store's owned files, never anything under the symlink's parent (e.g. /tmp).
    default_dir = tmp_path / "AppSupport" / "spacelabel"
    _patch_purge_dirs(monkeypatch, tmp_path, data_dir=default_dir)
    default_dir.mkdir(parents=True)
    (default_dir / "config.json").write_text("{}")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = elsewhere / "link.json"
    link.symlink_to(default_dir / "config.json")
    targets = install.purge_targets(install.store.StorePaths.resolve(link), remove_completion=True)
    assert (default_dir / "config.json") in targets  # canonical owned file
    assert not any(
        t == elsewhere or elsewhere in t.parents for t in targets
    )  # not symlink's parent


def test_remove_default_store_dir_if_empty_preserves_foreign_file(monkeypatch, tmp_path):
    # F1: after purging owned files, the data dir is removed only if empty; a foreign file
    # kept there (e.g. a user's alternate --config) survives.
    default_dir = tmp_path / "AppSupport" / "spacelabel"
    default_dir.mkdir(parents=True)
    monkeypatch.setattr(install.store, "data_dir", lambda: default_dir)
    foreign = default_dir / "alt.json"
    foreign.write_text("{}")
    install.remove_default_store_dir_if_empty()
    assert default_dir.exists() and foreign.exists()  # not removed: a foreign file remains
    foreign.unlink()
    install.remove_default_store_dir_if_empty()
    assert not default_dir.exists()  # now empty -> removed


def test_purge_targets_default_includes_leaked_atomic_write_temps(monkeypatch, tmp_path):
    # F-a: an interrupted atomic write leaves "<json>.<rand>.tmp" in the default dir; purge
    # must sweep them (else the dir survives the empty-check). A foreign name is NOT swept.
    default_dir = tmp_path / "AppSupport" / "spacelabel"
    default_dir.mkdir(parents=True)
    _patch_purge_dirs(monkeypatch, tmp_path, data_dir=default_dir)
    leaked = default_dir / "labels.json.ab12cd.tmp"
    leaked.write_text("partial")
    foreign = default_dir / "notes.txt"  # not a spacelabel temp -> preserved
    foreign.write_text("x")
    targets = install.purge_targets(install.store.StorePaths.resolve(None), remove_completion=True)
    assert leaked in targets
    assert foreign not in targets


def test_purge_targets_default_includes_corrupt_backups(monkeypatch, tmp_path):
    # F3: store._guard_before_rewrite backs a malformed file up to "<json>.corrupt"; purge must
    # sweep it, else remove_default_store_dir_if_empty leaves the (non-empty) dir behind.
    default_dir = tmp_path / "AppSupport" / "spacelabel"
    default_dir.mkdir(parents=True)
    _patch_purge_dirs(monkeypatch, tmp_path, data_dir=default_dir)
    corrupt = default_dir / "labels.json.corrupt"
    corrupt.write_text("{ broken")
    targets = install.purge_targets(install.store.StorePaths.resolve(None), remove_completion=True)
    assert corrupt in targets


def test_purge_user_data_deletes_files_dirs_and_skips_missing(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "labels.json").write_text("{}")
    comp = tmp_path / "x.fish"
    comp.write_text("x")
    missing = tmp_path / "gone"  # already absent -> no-op, not a failure
    failed = install.purge_user_data([data, comp, missing])
    assert failed == []
    assert not data.exists()
    assert not comp.exists()


def test_purge_user_data_partial_failure_is_independent(tmp_path, monkeypatch):
    comp = tmp_path / "x.fish"
    comp.write_text("x")
    bad = tmp_path / "data"
    bad.mkdir()
    real_rmtree = install.shutil.rmtree
    monkeypatch.setattr(
        install.shutil,
        "rmtree",
        lambda p, *a, **k: (
            (_ for _ in ()).throw(OSError("boom")) if Path(p) == bad else real_rmtree(p, *a, **k)
        ),
    )
    failed = install.purge_user_data([comp, bad])
    assert failed == [bad]  # the failure is reported
    assert not comp.exists()  # ...but the independent target was still deleted
