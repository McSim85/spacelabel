"""LaunchAgent install / uninstall via ``launchctl`` (DESIGN.md §9.2).

The agent runs at login in the per-user Aqua GUI domain (``NSStatusItem`` needs
a window-server session). The reverse-DNS id :data:`spacelabel.BUNDLE_ID` is used
verbatim as the LaunchAgent ``Label`` and the plist filename.

The plist is BUILT in code (:func:`build_launch_agent`) as the single source of
truth at runtime: the packaging template ``packaging/dev.mcsim.spacelabel.plist``
is not shipped inside the pipx wheel, so it stays a human reference only and a
test asserts the two stay in sync.
"""

from __future__ import annotations

import fcntl
import logging
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from xml.parsers.expat import ExpatError

from spacelabel import APP_NAME, BUNDLE_ID, store

__all__ = [
    "LAUNCH_AGENT_LABEL",
    "AgentStatus",
    "InstallError",
    "agent_status_detail",
    "build_launch_agent",
    "caches_dir",
    "install_agent",
    "is_installed",
    "logs_dir",
    "plist_path",
    "purge_targets",
    "purge_user_data",
    "render_plist",
    "uninstall_agent",
]

log = logging.getLogger(__name__)

#: launchd Label == plist basename == BUNDLE_ID (single source of truth).
LAUNCH_AGENT_LABEL = BUNDLE_ID

#: Matches launchctl's ``pid = 4213`` line inside ``launchctl print`` output.
_PID_RE = re.compile(r"^\s*pid\s*=\s*(\d+)", re.MULTILINE)


class InstallError(RuntimeError):
    """Raised when a ``launchctl`` invocation fails in a way that matters."""


def plist_path() -> Path:
    """Return the per-user LaunchAgents plist path for this agent."""
    return Path.home() / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"


def is_installed() -> bool:
    """Return whether the LaunchAgent plist exists on disk."""
    return plist_path().exists()


def logs_dir(home: Path | None = None) -> Path:
    """Return the agent log directory ``<home>/Library/Logs/spacelabel``.

    :param home: Home directory to base the path on; defaults to ``Path.home()``.
    """
    base = home if home is not None else Path.home()
    return base / "Library" / "Logs" / "spacelabel"


def caches_dir(home: Path | None = None) -> Path:
    """Return the agent cache directory ``<home>/Library/Caches/spacelabel``.

    :param home: Home directory to base the path on; defaults to ``Path.home()``.
    """
    base = home if home is not None else Path.home()
    return base / "Library" / "Caches" / "spacelabel"


def _canonical_shim() -> Path:
    """Return the legacy pipx shim path ``~/.local/bin/spacelabel`` (DESIGN §9.1)."""
    return Path.home() / ".local" / "bin" / "spacelabel"


def _enclosing_app_exe() -> Path | None:
    """Return ``<…>.app/Contents/MacOS/spacelabel`` when running from the cask bundle.

    The Homebrew cask exposes the bundle's main executable on PATH (its ``binary``
    stanza), so a user invoking ``spacelabel install`` is running *inside*
    ``spacelabel.app``. The LaunchAgent must exec that bundle executable so the agent
    process **is** the bundle and macOS attributes Accessibility (TCC) to
    ``dev.mcsim.spacelabel`` -- the whole point of the distribution pivot
    (DECISIONS.md §6 / §2.7, todo/phase-6-blockers.md Tier 1 step 5). Detected by
    walking up from the running executable / this module's file to the enclosing
    ``.app``; returns ``None`` when not bundled (a dev or legacy-pipx install).

    Paths are only ``abspath``-normalized, **not** symlink-resolved: the cask moves
    the app to a STABLE location (e.g. ``~/Applications/spacelabel.app``) that
    ``brew upgrade`` rewrites in place, so we must record that stable path in the
    LaunchAgent. Fully resolving symlinks could instead yield a versioned
    ``…/Caskroom/spacelabel/<version>/…`` path that the next upgrade deletes, breaking
    auto-start.
    """
    for raw in (sys.executable, sys.argv[0] if sys.argv else "", __file__):
        if not raw:
            continue
        try:
            # abspath normalizes (absolute + collapses ``..``) WITHOUT following
            # symlinks -> keeps the stable appdir path, not a Caskroom-versioned one.
            # (Path.resolve() -- what PTH100 suggests -- WOULD follow symlinks; that is
            # exactly the behavior we must avoid here.)
            normalized = Path(os.path.abspath(raw))  # noqa: PTH100
        except (OSError, ValueError) as exc:
            log.debug("could not normalize candidate path %r: %s", raw, exc)
            continue
        for ancestor in normalized.parents:
            if ancestor.suffix == ".app":
                exe = ancestor / "Contents" / "MacOS" / APP_NAME
                if exe.exists():
                    return exe
                # This .app has no spacelabel exe -- it may be py2app's embedded
                # Python.app helper under Contents/Frameworks, not spacelabel.app. Keep
                # scanning outer ancestors / the remaining candidates rather than giving up.
    return None


def _resolve_install_shim() -> Path:
    """Return the absolute executable the LaunchAgent must exec.

    Prefers the cask-installed ``spacelabel.app`` main executable so the agent process
    **is** the bundle -- a stable, *named* Accessibility identity (the point of the
    distribution pivot, DECISIONS.md §6). Falls back to the legacy pipx shim
    ``~/.local/bin/spacelabel`` (deprecated) for dev/transition installs. launchd starts
    the login agent without a shell environment, so a transient PATH/venv path would make
    the login item fragile; when neither a bundle nor the pipx shim resolves, this refuses
    rather than persist such a path.

    Raises:
        InstallError: If neither the app bundle nor the pipx shim can be resolved.
    """
    bundle_exe = _enclosing_app_exe()
    if bundle_exe is not None:
        return bundle_exe
    canonical = _canonical_shim()
    if canonical.exists():
        log.warning(
            "not running from the spacelabel.app bundle; the LaunchAgent will exec the "
            "legacy pipx shim %s. Prefer the Homebrew cask (`brew install --cask spacelabel`) "
            "so the agent gets its own stable Accessibility identity (DECISIONS.md §6).",
            canonical,
        )
        return canonical
    raise InstallError(
        "could not resolve the agent executable: install spacelabel via the Homebrew cask "
        "(`brew install --cask spacelabel`) -- or the legacy `pipx install spacelabel` -- "
        "before `spacelabel install`, so the login agent points at a durable path rather "
        "than a transient shell/venv executable."
    )


def build_launch_agent(home: Path, shim: Path) -> dict[str, object]:
    """Build the LaunchAgent property-list dictionary (PURE; DESIGN §9.2).

    The returned dict matches ``packaging/dev.mcsim.spacelabel.plist`` after the
    ``__HOME__`` token is replaced with ``home`` and ``__APP_EXE__`` with ``shim``
    (the resolved agent executable -- the cask bundle exe, or the legacy pipx shim).

    :param home: Absolute home directory templated into the log paths.
    :param shim: Absolute path to the agent executable (the ``spacelabel.app`` bundle
        exe under the cask; the pipx console-script shim on the legacy path).
    """
    log_root = home / "Library" / "Logs" / "spacelabel"
    return {
        "Label": BUNDLE_ID,
        "ProgramArguments": [str(shim), "agent"],
        "LimitLoadToSessionType": "Aqua",
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Interactive",
        # Both streams go to a single boot-catch file (NOT agent.log): agent.log is
        # owned solely by the RotatingFileHandler, so launchd never double-writes the
        # file the handler rotates. agent.boot.log only catches catastrophic output
        # before logging is up; run_agent caps it (DECISIONS 2.6 / DESIGN §9.2).
        "StandardOutPath": str(log_root / "agent.boot.log"),
        "StandardErrorPath": str(log_root / "agent.boot.log"),
    }


def render_plist(home: Path, shim: Path) -> bytes:
    """Serialize :func:`build_launch_agent` to XML plist bytes.

    :param home: Absolute home directory templated into the log paths.
    :param shim: Absolute path to the ``spacelabel`` console-script shim.
    """
    return plistlib.dumps(build_launch_agent(home, shim))


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (unique temp in the same dir -> replace).

    A direct ``write_bytes`` can leave a truncated plist if interrupted mid-write
    (power loss, kill, ENOSPC), which would make the login item fail to load. The
    temp-then-rename keeps the installed plist either fully old or fully new. A
    **unique** temp (``mkstemp``) avoids a collision when two writers race — e.g. a
    manual ``spacelabel install`` and the agent's ``refresh_plist_if_stale`` after an
    upgrade — which a shared temp name would corrupt.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
        _fsync_dir(path.parent)  # persist the rename itself, not just the file bytes
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _fsync_dir(directory: Path) -> None:
    """Best-effort ``fsync`` of ``directory`` so a just-completed rename survives a crash.

    Some filesystems reject directory fsync (``EINVAL``); since the rename already
    succeeded, such a failure is logged at ``DEBUG`` and ignored rather than failing
    the write.
    """
    try:
        dir_fd = os.open(directory, os.O_RDONLY)
    except OSError as exc:
        log.debug("could not open %s to fsync: %s", directory, exc)
        return
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        log.debug("could not fsync directory %s: %s", directory, exc)
    finally:
        os.close(dir_fd)


def refresh_plist_if_stale() -> bool:
    """Rewrite an installed LaunchAgent plist when it differs from the template.

    Lets a package upgrade roll out plist changes (e.g. the log-path / single-writer
    migration) **without** the user re-running ``spacelabel install``: the agent
    calls this at startup, and a stale on-disk plist is rewritten so the corrected
    config applies on the next login or ``launchctl kickstart``. Only the keys this
    migration changes (the std-stream paths) are patched — ``ProgramArguments``
    (including any ``--config``/extra args) and every other key are preserved, so it
    never repoints the agent or drops customizations. No-op when not installed or
    already current.

    This deliberately does **not** migrate a legacy pipx ``ProgramArguments[0]``
    (``~/.local/bin/spacelabel``) to the cask bundle exe: it cannot (the pipx plist runs
    the pipx agent, not the bundle one, so the bundle's startup never sees that plist),
    and silently repointing someone's program path would be surprising. The pipx→cask
    migration is instead an explicit, documented step — re-run ``spacelabel install``
    (which resolves and writes the bundle exe via ``_resolve_install_shim``).

    Best-effort: a read/parse/write failure is logged and returns ``False`` rather
    than raising — a logging-housekeeping refresh must never block agent startup.

    Returns:
        ``True`` if the plist was rewritten, else ``False``.
    """
    path = plist_path()
    if not path.exists():
        return False
    try:
        current = plistlib.loads(path.read_bytes())
    except (OSError, plistlib.InvalidFileException, ExpatError) as exc:
        # ExpatError: a detected-as-XML plist that is syntactically broken (e.g. a
        # truncated hand edit) — plistlib lets expat's error propagate. Best-effort.
        log.warning("could not read installed plist %s to refresh it: %s", path, exc)
        return False
    program = current.get("ProgramArguments") if isinstance(current, dict) else None
    if not (isinstance(program, list) and program and isinstance(program[0], str)):
        log.warning("installed plist %s has no usable ProgramArguments; not refreshing", path)
        return False
    # Patch ONLY the std-stream paths; keep ProgramArguments + any other keys as-is.
    expected = build_launch_agent(Path.home(), Path(program[0]))
    updated = dict(current)
    updated["StandardOutPath"] = expected["StandardOutPath"]
    updated["StandardErrorPath"] = expected["StandardErrorPath"]
    if updated == current:
        return False  # std paths already current
    try:
        _atomic_write_bytes(path, plistlib.dumps(updated))
    except OSError as exc:
        log.warning("could not refresh stale plist %s: %s", path, exc)
        return False
    # The on-disk file is now correct; launchd keeps the already-loaded (old) config
    # for this session, so the migration applies on the next login/reload. We do NOT
    # bootout/bootstrap here — self-reloading the running login agent is fragile; the
    # legacy log files stay capped meanwhile (see logging_setup.truncate_boot_log).
    log.info("refreshed stale LaunchAgent plist %s; applies on next login or reload", path)
    return True


def _launchctl(args: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    """Run ``launchctl`` with list args (no shell) and capture text output.

    :param args: ``launchctl`` subcommand and operands (without ``launchctl``).
    :param check: When True, raise :class:`InstallError` on a non-zero exit.
    :raises InstallError: If the binary is missing, or ``check`` and the exit is
        non-zero.
    """
    cmd = ["launchctl", *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        log.error("launchctl not found on PATH: %s", exc)
        raise InstallError("launchctl binary not found") from exc
    except OSError as exc:
        log.exception("launchctl invocation failed: %s", " ".join(cmd))
        raise InstallError(f"launchctl invocation failed: {exc}") from exc
    if check and result.returncode != 0:
        log.error(
            "launchctl %s exited %d: %s",
            " ".join(args),
            result.returncode,
            result.stderr.strip(),
        )
        raise InstallError(
            f"launchctl {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result


def _gui_domain() -> str:
    """Return the per-user GUI domain target ``gui/<uid>``."""
    return f"gui/{os.getuid()}"


def _service_target() -> str:
    """Return the per-user service target ``gui/<uid>/<label>``."""
    return f"{_gui_domain()}/{LAUNCH_AGENT_LABEL}"


def install_agent(*, load: bool = True) -> None:
    """Render the plist with absolute paths and (optionally) bootstrap it.

    Creates ``~/Library/LaunchAgents`` and the agent log directory before
    loading (launchd cannot open the log paths otherwise), writes the rendered
    plist, then, when ``load`` is True, boots out any stale copy (ignoring "not
    loaded") and bootstraps the freshly written plist into ``gui/$UID``.

    :param load: When True, load the agent now; when False, only write/refresh
        the plist (it loads at next login).
    :raises InstallError: If the plist cannot be written or ``launchctl``
        bootstrap fails.
    """
    home = Path.home()
    shim = _resolve_install_shim()
    target_plist = plist_path()

    target_plist.parent.mkdir(parents=True, exist_ok=True)
    logs_dir(home).mkdir(parents=True, exist_ok=True)

    try:
        _atomic_write_bytes(target_plist, render_plist(home, shim))
    except OSError as exc:
        log.exception("failed to write LaunchAgent plist: %s", target_plist)
        raise InstallError(f"could not write plist {target_plist}: {exc}") from exc

    if not load:
        log.info("wrote %s (not loaded; --no-load)", target_plist)
        return

    # Boot out any stale copy first; a missing service is fine here.
    _launchctl(["bootout", _service_target()], check=False)
    _launchctl(["bootstrap", _gui_domain(), str(target_plist)], check=True)
    log.info("loaded %s", LAUNCH_AGENT_LABEL)


def uninstall_agent() -> None:
    """Bootout the agent from ``gui/$UID`` and remove its plist.

    A "not loaded" bootout is ignored (already unloaded); the plist is unlinked
    with ``missing_ok=True``. Labels and config are never touched.

    :raises InstallError: If unlinking the plist fails for a reason other than
        the file being absent.
    """
    # Ignore failures here: the service may simply not be loaded.
    _launchctl(["bootout", _service_target()], check=False)
    try:
        plist_path().unlink(missing_ok=True)
    except OSError as exc:
        log.exception("failed to remove LaunchAgent plist: %s", plist_path())
        raise InstallError(f"could not remove plist {plist_path()}: {exc}") from exc
    log.info("unloaded and removed %s", LAUNCH_AGENT_LABEL)


def _is_default_store(config_file: Path) -> bool:
    """Whether ``config_file`` is the canonical default config (``config.json`` in data_dir).

    Compares fully-resolved paths (symlink/spelling-proof). Only the default config file
    is the managed store: the LaunchAgent runs *that* file, so this gates whether the
    launchctl state and the whole-dir ``--purge`` apply. An ALTERNATE config kept inside
    the data dir (e.g. ``alt.json``) is **not** the default store -- status reports it
    unmanaged and purge deletes only spacelabel-owned-named files, never the whole dir.
    """
    default_config = store.StorePaths.default().config_file
    try:
        return config_file.resolve() == default_config.resolve()
    except OSError:
        return config_file == default_config


def purge_targets(paths: store.StorePaths, *, remove_completion: bool) -> list[Path]:
    """Resolve the spacelabel-owned paths that ``uninstall --purge`` would delete.

    Deletes only paths spacelabel **exclusively owns**:

    - the **default store** ``store.data_dir()`` (``~/Library/Application Support/spacelabel``),
      our own dedicated directory, removed wholesale -- but only when ``paths`` is the
      default config (or a symlink/spelling of it). The canonical ``store.data_dir()`` is
      used, never ``paths.directory`` (a ``--config`` symlink's parent could be ``/tmp``);
    - the global ``~/Library/Caches/spacelabel`` and ``~/Library/Logs/spacelabel`` dirs;
    - when ``remove_completion``, the existing per-shell completion scripts.

    A **custom** ``--config`` lives in a directory spacelabel does not own, so its files
    are **never** deleted by guessed basename -- a sibling named ``labels.json`` could be
    another app's. The custom store's own files are left for manual removal (the CLI says
    so). **Never** touches the WallpaperAgent store, the pipx venv, or the
    ``~/.local/bin/spacelabel`` shim. The cask ``zap`` stanza mirrors these paths.

    :param paths: The resolved store paths for the active ``--config`` selection.
    :param remove_completion: Also include the per-shell completion scripts.
    """
    from spacelabel import completion

    targets: list[Path] = []
    if _is_default_store(paths.config_file):
        # Our own dedicated dir -> remove wholesale. The CANONICAL store.data_dir(), never
        # paths.directory: if --config is a symlink to the default config.json,
        # paths.directory is the symlink's parent (e.g. /tmp), which must not be deleted.
        targets.append(store.data_dir())
    # A custom --config is intentionally NOT expanded into per-file deletions here: its
    # directory is not exclusively ours, so deleting labels.json/etc by name there could
    # remove a foreign file. Only the globally-exclusive dirs below apply to it.
    targets.append(caches_dir())
    targets.append(logs_dir())
    if remove_completion:
        targets += completion.installed_completion_files()
    return targets


def purge_user_data(targets: list[Path]) -> list[Path]:
    """Delete each target (file or directory); return the ones that could not be removed.

    Each deletion is independent and best-effort, so one failure never aborts the rest
    (the caller reports the returned failures and exits 1). A missing target is a no-op
    (already gone). A symlink is unlinked (never followed) so a deletion can't escape the
    named target into a linked location.
    """
    failed: list[Path] = []
    for target in targets:
        try:
            if target.is_symlink():
                target.unlink(missing_ok=True)
            elif target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
        except OSError as exc:
            log.error("could not remove %s: %s", target, exc)
            failed.append(target)
    return failed


@dataclass(frozen=True, slots=True)
class AgentStatus:
    """Install + run state of the agent (DECISIONS.md §9 exit-code contract).

    ``installed``/``loaded`` describe the LaunchAgent plist (present on disk / loaded
    into ``gui/$UID``); ``running`` is True when *any* agent process -- the managed
    LaunchAgent **or** a foreground ``spacelabel agent`` -- holds ``agent.lock``, and
    ``managed`` distinguishes the two. ``spacelabel status`` exits 0 when ``running``
    else 3; ``installed``/``loaded`` are informational and never change the exit code.
    """

    installed: bool
    loaded: bool
    running: bool
    pid: int | None
    managed: bool


def _launchctl_service_state() -> tuple[bool, int | None]:
    """Return ``(loaded, pid)`` for the LaunchAgent service via ``launchctl print``.

    ``loaded`` is whether the service exists in ``gui/$UID`` (``launchctl print`` exits
    0); ``pid`` is the running instance's pid when launchd has spawned it, else ``None``
    (loaded-but-not-spawned).

    :raises InstallError: Only if ``launchctl`` itself is missing or fails to execute
        (a genuine query failure, distinct from "not loaded").
    """
    result = _launchctl(["print", _service_target()], check=False)
    if result.returncode != 0:
        log.debug(
            "launchctl print %s exited %d (service not loaded)",
            _service_target(),
            result.returncode,
        )
        return (False, None)
    match = _PID_RE.search(result.stdout)
    pid = int(match.group(1)) if match is not None else None
    return (True, pid)


def _probe_lock_path(lock_path: Path) -> tuple[bool, int | None]:
    """Return ``(held, pid)`` for a specific lock file -- the core single-instance probe.

    Opens the file read-only and attempts a non-blocking ``flock``: if it blocks
    (``BlockingIOError``/``EAGAIN``) an agent -- managed or foreground -- holds it; if it
    succeeds the lock is released immediately and no agent is running. The holder's pid is
    read from the file's contents (the agent records its pid after locking); a stale file
    from a crashed agent reads as not-held because the OS drops its ``flock``.
    """
    try:
        handle = lock_path.open("r")
    except OSError:
        return (False, None)  # no lock file -> the agent has never run here
    try:
        recorded = handle.read().strip()
        try:
            pid: int | None = int(recorded)
        except ValueError:
            pid = None
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return (True, pid)  # contended -> an agent holds the lock
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return (False, None)  # acquired freely -> not running
    finally:
        handle.close()


def _probe_agent_lock(config_path: Path | None) -> tuple[bool, int | None]:
    """Return ``(held, pid)`` for the ``agent.lock`` of the ``--config`` selection's store."""
    return _probe_lock_path(store.StorePaths.resolve(config_path).directory / "agent.lock")


def agent_status_detail(config_path: Path | None = None) -> AgentStatus:
    """Report install + run state, detecting a foreground agent too (improvements.md item I).

    Combines the LaunchAgent service state (``launchctl``) with a probe of ``agent.lock``,
    so a foreground ``spacelabel agent`` (dev/debug) is reported **running** even though
    launchd does not manage it. ``managed`` is True when the running instance is the
    launchd one (``launchctl`` reports its pid); ``pid`` prefers the launchd pid, else the
    pid recorded in the lock file.

    :param config_path: ``--config`` selection, so the probe targets the matching
        ``agent.lock`` (a custom-config agent has its own store/lock).
    :raises InstallError: Only if ``launchctl`` itself cannot be executed.
    """
    paths = store.StorePaths.resolve(config_path)
    canonical_lock = store.data_dir() / "agent.lock"
    # The LaunchAgent only ever runs the DEFAULT config file (one Label). Decide by the
    # *resolved config file* (not `config_path is None`, not the directory) so the default
    # config.json -- even via a symlink/relative spelling -- is recognized as managed.
    if _is_default_store(paths.config_file):
        # Probe the CANONICAL lock, so a symlinked-default --config still sees the live
        # default agent (not a lock in the symlink's parent dir), and fold in launchctl.
        lock_held, lock_pid = _probe_lock_path(canonical_lock)
        loaded, launchctl_pid = _launchctl_service_state()
        return AgentStatus(
            installed=is_installed(),
            loaded=loaded,
            running=lock_held or launchctl_pid is not None,
            pid=launchctl_pid if launchctl_pid is not None else lock_pid,
            managed=launchctl_pid is not None,
        )
    # A genuinely custom --config agent is necessarily unmanaged/foreground. Probe ITS
    # lock -- unless that lock IS the default store's shared lock (an alt config kept
    # inside the default dir), which we attribute to the default agent, not this config.
    # Inherent ambiguity: StorePaths places an alt config's agent.lock in the shared
    # default dir, so its agent and the default agent are indistinguishable via flock; we
    # report not-running here to avoid falsely attributing the default agent to this
    # config (a documented limit of the contrived "alt config inside the default store"
    # layout -- a normal --config lives in its own directory with its own lock).
    custom_lock = paths.directory / "agent.lock"
    try:
        shares_default_lock = custom_lock.resolve() == canonical_lock.resolve()
    except OSError:
        shares_default_lock = custom_lock == canonical_lock
    lock_held, lock_pid = (False, None) if shares_default_lock else _probe_lock_path(custom_lock)
    return AgentStatus(
        installed=False, loaded=False, running=lock_held, pid=lock_pid, managed=False
    )
