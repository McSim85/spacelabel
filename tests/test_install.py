"""LaunchAgent plist generation + packaging-template sync (DESIGN.md §9.2, DECISIONS.md 6.4)."""

from __future__ import annotations

import fcntl
import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from spacelabel import BUNDLE_ID, install
from spacelabel.install import build_launch_agent, render_plist

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


def test_enclosing_app_exe_detects_bundle(tmp_path, monkeypatch):
    # A spacelabel.app whose Resources host this package -> resolve Contents/MacOS exe.
    app = tmp_path / "spacelabel.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    exe = macos / "spacelabel"
    exe.write_text("#!/bin/sh\n")
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


# ---- status: agent.lock probe + install/run-state combination --------------


def test_probe_agent_lock_held_released_and_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(
        install.store.StorePaths, "resolve", lambda _cfg=None: SimpleNamespace(directory=tmp_path)
    )
    # No lock file yet -> the agent has never run for this config.
    assert install._probe_agent_lock(None) == (False, None)

    # Simulate a running agent: write its pid and hold an exclusive flock (a separate
    # open file description, so the probe's own fd is correctly denied — flock conflicts
    # across descriptions even within one process).
    holder = (tmp_path / "agent.lock").open("w")
    holder.write("12345")
    holder.flush()
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert install._probe_agent_lock(None) == (True, 12345)
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()

    # Lock released (agent gone), but the file (with its stale pid) remains -> not held.
    assert install._probe_agent_lock(None) == (False, None)


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
    monkeypatch.setattr(install, "_probe_lock_path", lambda _p: lock)
    monkeypatch.setattr(install, "is_installed", lambda: installed)
    st = install.agent_status_detail()  # default config -> folds in launchctl
    assert (st.installed, st.loaded, st.running, st.pid, st.managed) == expected


def test_agent_status_detail_custom_config_ignores_launchctl(monkeypatch, tmp_path):
    # The managed LaunchAgent only runs the DEFAULT config, so a --config query must NOT
    # report the default agent's managed state (it would be a false positive).
    monkeypatch.setattr(
        install, "_launchctl_service_state", lambda: (True, 4213)
    )  # default IS running
    monkeypatch.setattr(install, "_probe_lock_path", lambda _p: (False, None))  # this config's not
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
    monkeypatch.setattr(install, "_probe_lock_path", lambda _p: (True, 4213))
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
    # --config is a symlink to the default config.json -> status must probe the CANONICAL
    # data_dir/agent.lock (not the symlink's parent) so a live default agent is seen.
    default_dir = tmp_path / "AppSupport" / "spacelabel"
    default_dir.mkdir(parents=True)
    monkeypatch.setattr(install.store, "data_dir", lambda: default_dir)
    monkeypatch.setattr(install, "_launchctl_service_state", lambda: (False, None))
    monkeypatch.setattr(install, "is_installed", lambda: False)
    (default_dir / "config.json").write_text("{}")
    holder = (default_dir / "agent.lock").open("w")  # a live agent holds the canonical lock
    holder.write("4242")
    holder.flush()
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        link = elsewhere / "link.json"
        link.symlink_to(default_dir / "config.json")
        st = install.agent_status_detail(link)
        assert st.running is True and st.pid == 4242  # saw the live default agent
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


def test_agent_status_detail_alt_config_ignores_shared_default_lock(monkeypatch, tmp_path):
    # An alt config inside the default dir shares data_dir/agent.lock; that lock belongs to
    # the default agent, not this config -> report it not-running/unmanaged (no false agent).
    default_dir = tmp_path / "AppSupport" / "spacelabel"
    default_dir.mkdir(parents=True)
    monkeypatch.setattr(install.store, "data_dir", lambda: default_dir)
    monkeypatch.setattr(install, "_launchctl_service_state", lambda: (True, 4213))
    monkeypatch.setattr(install, "is_installed", lambda: True)
    holder = (default_dir / "agent.lock").open("w")  # the default agent holds the shared lock
    holder.write("4213")
    holder.flush()
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        st = install.agent_status_detail(default_dir / "alt.json")
        assert (st.running, st.managed, st.pid) == (False, False, None)
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


# ---- uninstall --purge: target resolution + deletion -----------------------


def test_caches_dir():
    home = Path("/Users/alex")
    assert install.caches_dir(home) == home / "Library/Caches/spacelabel"


def _patch_purge_dirs(monkeypatch, tmp_path, *, data_dir):
    monkeypatch.setattr(install.store, "data_dir", lambda: data_dir)
    monkeypatch.setattr(install, "caches_dir", lambda: tmp_path / "Caches/spacelabel")
    monkeypatch.setattr(install, "logs_dir", lambda: tmp_path / "Logs/spacelabel")
    monkeypatch.setattr("spacelabel.completion.installed_completion_files", lambda: [])


def test_purge_targets_default_config_removes_whole_dir(monkeypatch, tmp_path):
    default_dir = tmp_path / "AppSupport" / "spacelabel"
    _patch_purge_dirs(monkeypatch, tmp_path, data_dir=default_dir)
    monkeypatch.setattr(
        "spacelabel.completion.installed_completion_files", lambda: [tmp_path / "x.fish"]
    )
    paths = install.store.StorePaths.resolve(None)
    targets = install.purge_targets(paths, remove_completion=True)
    assert default_dir in targets  # the whole owned dir
    assert paths.config_file not in targets  # individual JSONs not listed separately
    assert (tmp_path / "Caches/spacelabel") in targets
    assert (tmp_path / "Logs/spacelabel") in targets
    assert (tmp_path / "x.fish") in targets  # completion script included


def test_purge_targets_custom_config_touches_nothing_in_its_dir(monkeypatch, tmp_path):
    # Conservative: a custom --config dir is not exclusively ours, so NO file there is a
    # target (not the config, not the derived labels/displays, not the locks) -- only the
    # globally-exclusive caches/logs apply.
    _patch_purge_dirs(monkeypatch, tmp_path, data_dir=tmp_path / "default" / "spacelabel")
    cfg = tmp_path / "shared" / "myconfig.json"
    paths = install.store.StorePaths.resolve(cfg)
    targets = install.purge_targets(paths, remove_completion=True)
    assert not any(t.parent == paths.directory for t in targets)  # nothing in the --config dir
    assert (tmp_path / "Caches/spacelabel") in targets
    assert (tmp_path / "Logs/spacelabel") in targets


def test_purge_targets_custom_config_never_deletes_unrelated_file(monkeypatch, tmp_path):
    # The footgun: `--config ~/.ssh/config` must never schedule ~/.ssh/config (or a
    # sibling labels.json that could be another app's) for deletion.
    _patch_purge_dirs(monkeypatch, tmp_path, data_dir=tmp_path / "default" / "spacelabel")
    ssh_config = tmp_path / ".ssh" / "config"
    paths = install.store.StorePaths.resolve(ssh_config)
    targets = install.purge_targets(paths, remove_completion=True)
    assert not any(t.parent == ssh_config.parent for t in targets)  # nothing in ~/.ssh


def test_purge_targets_alt_config_inside_default_dir_spares_default_store(monkeypatch, tmp_path):
    # An ALTERNATE config kept inside the default dir must NOT delete the whole dir NOR the
    # default store's shared files (labels.json/displays.json/agent.lock all belong to the
    # default install) -- only the global caches/logs apply to it.
    default_dir = tmp_path / "AppSupport" / "spacelabel"
    _patch_purge_dirs(monkeypatch, tmp_path, data_dir=default_dir)
    paths = install.store.StorePaths.resolve(default_dir / "alt.json")
    targets = install.purge_targets(paths, remove_completion=True)
    assert default_dir not in targets  # never nuke the whole (shared) dir
    assert (default_dir / "alt.json") not in targets  # the alt config file is spared
    assert (default_dir / "labels.json") not in targets  # default store's data is spared
    assert (default_dir / "agent.lock") not in targets
    assert (tmp_path / "Caches/spacelabel") in targets  # only the global dirs apply

    # ...but the canonical default config.json (explicit path) DOES purge the whole dir.
    default_paths = install.store.StorePaths.resolve(default_dir / "config.json")
    assert default_dir in install.purge_targets(default_paths, remove_completion=True)


def test_purge_targets_symlinked_default_config_targets_canonical_dir(monkeypatch, tmp_path):
    # --config is a symlink resolving to the default config.json -> purge the CANONICAL
    # data dir, never the symlink's parent (e.g. /tmp), which would be catastrophic.
    default_dir = tmp_path / "AppSupport" / "spacelabel"
    _patch_purge_dirs(monkeypatch, tmp_path, data_dir=default_dir)
    default_dir.mkdir(parents=True)
    (default_dir / "config.json").write_text("{}")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = elsewhere / "link.json"
    link.symlink_to(default_dir / "config.json")
    targets = install.purge_targets(install.store.StorePaths.resolve(link), remove_completion=True)
    assert default_dir in targets  # the canonical store dir
    assert elsewhere not in targets  # NOT the symlink's parent


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
