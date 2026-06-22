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

import logging
import os
import plistlib
import re
import subprocess
from pathlib import Path

from spacelabel import BUNDLE_ID

__all__ = [
    "LAUNCH_AGENT_LABEL",
    "InstallError",
    "agent_status",
    "build_launch_agent",
    "install_agent",
    "is_installed",
    "logs_dir",
    "plist_path",
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


def _canonical_shim() -> Path:
    """Return the canonical pipx shim path ``~/.local/bin/spacelabel`` (DESIGN §9.1)."""
    return Path.home() / ".local" / "bin" / "spacelabel"


def _resolve_install_shim() -> Path:
    """Return the absolute shim the LaunchAgent must exec; REQUIRE the pipx path.

    launchd starts the login agent without the shell environment, so a transient
    PATH-derived executable (a dev shell, a ``uv``/venv path) would make the login
    item fragile and break the moment that path disappears. Rather than persist such
    a path, this REQUIRES the canonical pipx location ``~/.local/bin/spacelabel``
    (DESIGN §9.1) and refuses otherwise -- the agent must be installed via pipx first.

    Raises:
        InstallError: If the canonical pipx shim does not exist.
    """
    canonical = _canonical_shim()
    if not canonical.exists():
        raise InstallError(
            f"{canonical} not found: install spacelabel via pipx (`pipx install spacelabel`) "
            "before `spacelabel install`, so the login agent points at a durable path "
            "rather than a transient shell/venv executable."
        )
    return canonical


def build_launch_agent(home: Path, shim: Path) -> dict[str, object]:
    """Build the LaunchAgent property-list dictionary (PURE; DESIGN §9.2).

    The returned dict matches ``packaging/dev.mcsim.spacelabel.plist`` after the
    ``__HOME__`` token is replaced with ``home`` and the program path is
    ``home/".local/bin/spacelabel"``.

    :param home: Absolute home directory templated into the log paths.
    :param shim: Absolute path to the ``spacelabel`` console-script shim.
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
        target_plist.write_bytes(render_plist(home, shim))
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


def agent_status() -> tuple[bool, int | None]:
    """Return ``(running, pid)`` for the LaunchAgent service.

    Runs ``launchctl print gui/$UID/<label>`` and parses the ``pid = N`` line.
    A non-zero exit means the service is not loaded, reported as
    ``(False, None)``. A present-but-pid-less service (loaded, not currently
    spawned) is also ``(False, None)``.

    :raises InstallError: Only if ``launchctl`` itself is missing or fails to
        execute (a genuine query failure, distinct from "not loaded").
    """
    result = _launchctl(["print", _service_target()], check=False)
    if result.returncode != 0:
        log.debug(
            "launchctl print %s exited %d (treated as not loaded)",
            _service_target(),
            result.returncode,
        )
        return (False, None)
    match = _PID_RE.search(result.stdout)
    if match is None:
        log.debug("service %s is loaded but has no pid (not running)", _service_target())
        return (False, None)
    return (True, int(match.group(1)))
