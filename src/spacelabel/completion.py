"""Shell tab-completion: dynamic completers + the ``completion install`` helper.

Two kinds of completion live here, kept out of :mod:`spacelabel.cli` so the
command module stays lean:

* **Dynamic completers** for the UUID-bearing positional arguments (``label``,
  ``note``, ``display`` targets) and the ``config`` key. Click invokes these only
  while completing (the ``_SPACELABEL_COMPLETE`` env var is set by the generated
  completion script), so the live CGS / display / store reads stay lazy — normal
  dispatch never pulls AppKit. A completer must never crash the user's shell, so
  every read degrades to ``[]`` on a *specific*, logged failure (never a bare
  ``except``). Library log records are already swallowed by the package-level
  ``NullHandler`` (``spacelabel/__init__.py``), so a malformed store does not print
  warnings to stderr on TAB.
* **The installer** writes click's *generated completion script* (obtained
  in-process via :func:`click.shell_completion.get_completion_class`) into each
  shell's auto-load directory — fish/bash need no rc edit, and zsh drops a
  ``#compdef`` function into a writable directory on the live ``$fpath``. Writing
  the generated script (not click's ``VAR=val cmd | source`` bootstrap) is what
  makes fish work and avoids re-running spacelabel on every shell startup.
"""

from __future__ import annotations

import fcntl
import logging
import os
import re
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import click

from spacelabel import store

log = logging.getLogger(__name__)

#: Console program name (matches the ``console_scripts`` entry point).
_PROG = "spacelabel"
#: Env var click reads to emit completion (derived from ``_PROG`` per click's rule).
_COMPLETE_VAR = "_SPACELABEL_COMPLETE"
#: Marker comment written above the snippet so a human can find/remove it.
_HEADER = "# spacelabel shell completion"

#: Shells click can generate completion for (and we can install).
SHELLS = ("zsh", "bash", "fish")


class UnknownShellError(Exception):
    """Raised when the shell cannot be detected or is not supported."""


# --- candidate readers (best-effort; never raise into the shell) -------------


def _live_space_uuids() -> list[str]:
    """Live Space UUIDs via CGS, falling back to the spaces plist; ``[]`` on failure.

    Mirrors :func:`cli._read_spaces_with_fallback` but completion-safe: a failed
    read yields no candidates instead of raising (a traceback during completion
    would break the user's tab key).
    """
    import spacelabel.platform.cgs as cgs_mod

    try:
        spaces = cgs_mod.enumerate_spaces()
    except (cgs_mod.CGSUnavailableError, ImportError) as exc:
        # ImportError == PyObjC absent; both paths recover to a plist read.
        log.debug("completion: CGS read failed, trying plist fallback: %s", exc)
        from spacelabel.platform import spaces_plist

        try:
            spaces = spaces_plist.read_spaces()  # recovers its own file errors to []
        except (OSError, RuntimeError) as plist_exc:  # RuntimeError == Path.home() failure
            log.debug("completion: plist fallback failed: %s", plist_exc)
            return []
    return [space.uuid for space in spaces if space.uuid]


def _current_space_uuid() -> str | None:
    """Live current-Space UUID via CGS; ``None`` on any failure (never raises)."""
    import spacelabel.platform.cgs as cgs_mod

    try:
        return cgs_mod.read_active_space_uuid() or None
    except (cgs_mod.CGSUnavailableError, ImportError) as exc:  # ImportError == PyObjC absent
        log.debug("completion: current Space read failed: %s", exc)
        return None


def _live_display_uuids() -> list[str]:
    """Live display UUIDs via topology discovery; ``[]`` on any failure."""
    from spacelabel.platform import displays as displays_mod

    try:
        topology = displays_mod.discover_topology()
    except (OSError, ImportError) as exc:  # ImportError == PyObjC absent
        log.debug("completion: display discovery failed: %s", exc)
        return []
    return [disp.uuid for disp in topology]


def _paths_from_ctx(ctx: click.Context) -> store.StorePaths:
    """Resolve the store paths honoring a root ``--config`` if one was typed.

    During completion click builds the context chain and parses params but does
    not run the group callback, so ``ctx.obj`` is unset; read ``--config`` off the
    root context's parsed params instead.
    """
    config_path = ctx.find_root().params.get("config_path")
    if not isinstance(config_path, Path):
        config_path = None
    return store.StorePaths.resolve(config_path)


def _stored_uuids(ctx: click.Context, *, want_text: bool, want_notes: bool) -> list[str]:
    """Return stored Space UUIDs matching ``want_text`` and/or ``want_notes``.

    Enriches the ``clear``/operate completers so an entry whose Space is not
    currently live is still completable. ``note`` commands accept any stored Space
    (labeled *or* note-bearing) as an offline target, so they pass both flags;
    ``label clear`` passes only ``want_text``. ``[]`` on any read failure.
    """
    # RuntimeError covers Path.home() failing to resolve a home dir (unusual launch
    # contexts) inside _paths_from_ctx — a completer must never raise into the shell.
    try:
        labels = store.load_labels(_paths_from_ctx(ctx))
    except (store.StoreError, OSError, RuntimeError) as exc:
        log.debug("completion: store read failed: %s", exc)
        return []
    return [
        uuid
        for uuid, entry in labels.items()
        if (want_text and entry.text) or (want_notes and entry.notes)
    ]


def _stored_display_uuids(ctx: click.Context) -> list[str]:
    """Display UUIDs that have a stored custom name; ``[]`` on any read failure."""
    try:
        return list(store.load_display_labels(_paths_from_ctx(ctx)))
    except (store.StoreError, OSError, RuntimeError) as exc:  # RuntimeError == Path.home() failure
        log.debug("completion: display-name store read failed: %s", exc)
        return []


def _filter(candidates: list[str], incomplete: str) -> list[str]:
    """De-dupe (order-preserving) and prefix-filter candidates, case-insensitively."""
    prefix = incomplete.upper()
    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen or not candidate.upper().startswith(prefix):
            continue
        seen.add(candidate)
        out.append(candidate)
    return out


# --- completers (click ``shell_complete`` callbacks) -------------------------


def complete_space_target(ctx: click.Context, param: click.Parameter, incomplete: str) -> list[str]:
    """Complete a Space TARGET: ``current`` + live Space UUIDs (create paths)."""
    del param
    return _filter(["current", *_live_space_uuids()], incomplete)


def complete_label_clear_target(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[str]:
    """Complete ``label clear`` TARGET: ``current`` + live + already-labeled UUIDs."""
    del param
    stored = _stored_uuids(ctx, want_text=True, want_notes=False)
    return _filter(["current", *_live_space_uuids(), *stored], incomplete)


def complete_note_target(ctx: click.Context, param: click.Parameter, incomplete: str) -> list[str]:
    """Complete a ``note add``/``note list`` TARGET: ``current`` + live + any stored UUID.

    ``note add`` on a labeled-but-offline Space is valid (it grows the first task),
    and ``note list`` works on any Space, so labeled UUIDs are offered too — not
    only note-bearing ones.
    """
    del param
    stored = _stored_uuids(ctx, want_text=True, want_notes=True)
    return _filter(["current", *_live_space_uuids(), *stored], incomplete)


def complete_noted_space_target(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[str]:
    """Complete a ``note done``/``undone``/``clear`` TARGET: ``current`` + live + note-bearing.

    These operate on an existing task, so any target without notes would raise
    ``NoteIndexError``. Only note-bearing stored UUIDs are offered (a note-bearing
    Space is in the store whether or not it is currently live), and ``current`` is
    offered only when the live current Space actually has notes.
    """
    del param
    noted = _stored_uuids(ctx, want_text=False, want_notes=True)
    candidates = list(noted)
    current = _current_space_uuid()
    if current is not None and any(current.upper() == uuid.upper() for uuid in noted):
        candidates.insert(0, "current")
    return _filter(candidates, incomplete)


def complete_display_target(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[str]:
    """Complete a ``display`` TARGET: ``current`` + live + custom-named display UUIDs."""
    del param
    return _filter(["current", *_live_display_uuids(), *_stored_display_uuids(ctx)], incomplete)


def complete_config_key(ctx: click.Context, param: click.Parameter, incomplete: str) -> list[str]:
    """Complete a ``config get``/``config set`` KEY from the live config schema."""
    del ctx, param
    return _filter(sorted(store.CONFIG_SCHEMA), incomplete)


# --- generated-script installer ----------------------------------------------


class CompletionError(Exception):
    """Raised when the completion script cannot be generated or installed."""


@dataclass(frozen=True)
class InstallResult:
    """Outcome of :func:`install_completion` for the CLI to report."""

    shell: str
    path: Path
    changed: bool  # wrote/updated the file (False == on-disk script already current)
    hint: str  # follow-up guidance (restart shell, compinit, bash version, …)


def detect_shell(shell_env: str) -> str:
    """Return the supported shell name from a ``$SHELL`` value, else raise.

    Args:
        shell_env: The raw ``$SHELL`` path (e.g. ``/bin/zsh``); ``""`` if unset.

    Raises:
        UnknownShellError: ``$SHELL`` is empty or not one of :data:`SHELLS`.
    """
    name = Path(shell_env).name
    if name in SHELLS:
        return name
    raise UnknownShellError(
        f"could not detect a supported shell from $SHELL={shell_env!r}; "
        f"pass --shell explicitly (one of: {', '.join(SHELLS)})."
    )


def generate_script(shell: str) -> str:
    """Return click's generated completion script for ``shell`` (in-process).

    Uses click's own completion class so the output always matches the installed
    click version. The script is self-contained: for fish it is valid fish code
    (not the POSIX ``VAR=val cmd | source`` bootstrap, which fish cannot parse);
    for zsh it is a ``#compdef`` function that works both autoloaded from ``$fpath``
    and when sourced.
    """
    from click.shell_completion import get_completion_class

    from spacelabel.cli import cli  # lazy: cli imports this module at top level

    completion_cls = get_completion_class(shell)
    if completion_cls is None:  # unknown shell — guarded by SHELLS, but be explicit
        raise CompletionError(f"click has no completion support for shell {shell!r}.")
    instance = completion_cls(cli, {}, _PROG, _COMPLETE_VAR)
    return instance.source()


def _home() -> Path:
    """Return the user's home dir, as a :class:`CompletionError` on failure."""
    try:
        return Path.home()
    except RuntimeError as exc:  # no resolvable home (unusual launch context)
        raise CompletionError(f"could not resolve your home directory: {exc}") from exc


def _fish_completion_path() -> Path:
    """Auto-loaded fish completion file (honors ``$XDG_CONFIG_HOME``)."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(_home() / ".config")
    return Path(base) / "fish" / "completions" / f"{_PROG}.fish"


def _bash_too_old() -> bool:
    """Return whether the ``bash`` on PATH is known to be older than 4.4.

    click cannot generate working completion for bash < 4.4 (macOS ships 3.2).
    Best-effort: if ``bash`` can't be found or its version can't be parsed, returns
    ``False`` so we don't block a user whose target bash lives elsewhere.
    """
    try:
        proc = subprocess.run(
            ["bash", "-c", "echo ${BASH_VERSINFO[0]} ${BASH_VERSINFO[1]}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("completion: could not determine bash version: %s", exc)
        return False
    parts = proc.stdout.split()
    if len(parts) < 2:
        return False
    try:
        version = (int(parts[0]), int(parts[1]))
    except ValueError:
        return False
    return version < (4, 4)


def _bash_completion_path() -> Path:
    """Auto-loaded bash-completion (v2) user completion file.

    Honors ``$BASH_COMPLETION_USER_DIR``, else ``$XDG_DATA_HOME``, else
    ``~/.local/share``. Requires bash >= 4.4 with bash-completion v2 sourced; the
    macOS system bash 3.2 cannot use click completion at all.
    """
    user_dir = os.environ.get("BASH_COMPLETION_USER_DIR")
    if user_dir:
        base = Path(user_dir)
    else:
        data = os.environ.get("XDG_DATA_HOME") or str(_home() / ".local" / "share")
        base = Path(data) / "bash-completion"
    return base / "completions" / _PROG


def _live_zsh_fpath() -> list[Path]:
    """Best-effort directories on the user's live zsh ``$fpath`` (``[]`` on failure).

    Spawns an interactive zsh so user / oh-my-zsh / Homebrew ``fpath`` additions
    are present, then parses ``$fpath``. Interactive zsh may emit terminal control
    sequences (e.g. iTerm shell integration), so those are stripped and only
    absolute-path lines are kept.
    """
    try:
        proc = subprocess.run(
            ["zsh", "-ic", "print -rl -- $fpath"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("completion: could not query zsh $fpath: %s", exc)
        return []
    control = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
    dirs: list[Path] = []
    for raw in proc.stdout.splitlines():
        cleaned = control.sub("", raw).strip()
        if cleaned.startswith("/"):
            dirs.append(Path(cleaned))
    return dirs


def _zsh_completion_path() -> tuple[Path, bool]:
    """Return ``(_spacelabel target path, needs_fpath_rc_edit)`` for zsh.

    Choose a *dedicated* completion directory on the live ``$fpath`` — never a
    framework plugin/cache dir (e.g. ``~/.oh-my-zsh/plugins/*``), which would be
    clobbered by updates. Preference order:

    1. ``~/.zfunc`` if it is on ``$fpath`` (the conventional user dir; created if
       missing — being on ``$fpath`` is what matters, not pre-existing on disk);
    2. a user-writable ``…/completions`` dir on ``$fpath`` that is not under a
       ``plugins``/``cache`` path;
    3. a writable ``…/site-functions`` dir (Homebrew/system);
    4. fall back to ``~/.zfunc`` and wire it onto ``fpath`` via a one-time
       ``.zshrc`` edit (``needs_fpath_rc_edit=True``).
    """
    home = _home()
    fpath = _live_zsh_fpath()
    name = f"_{_PROG}"

    zfunc = home / ".zfunc"
    if any(_same_path(directory, zfunc) for directory in fpath) and _can_write_dir(zfunc):
        return zfunc / name, False  # _write_if_changed creates the dir if missing

    for directory in fpath:
        if (
            directory.name == "completions"
            and "plugins" not in directory.parts
            and "cache" not in directory.parts
            and _is_within(directory, home)
            and directory.is_dir()
            and os.access(directory, os.W_OK)
        ):
            return directory / name, False

    for directory in fpath:
        is_site = directory.name == "site-functions"
        if is_site and directory.is_dir() and os.access(directory, os.W_OK):
            return directory / name, False

    return zfunc / name, True


def _same_path(a: Path, b: Path) -> bool:
    """Return whether two paths resolve to the same location (best-effort)."""
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _can_write_dir(directory: Path) -> bool:
    """Return whether ``directory`` is (or can be created as) a writable dir.

    True if it exists as a writable directory, or it does not exist yet but its
    parent is a writable directory (so ``mkdir`` would succeed). Lets the selector
    skip an on-``fpath`` ``~/.zfunc`` that is read-only (e.g. an immutable dotfiles
    checkout) and fall through to another candidate.
    """
    if directory.exists():
        return directory.is_dir() and os.access(directory, os.W_OK)
    parent = directory.parent
    return parent.is_dir() and os.access(parent, os.W_OK)


def _is_within(path: Path, base: Path) -> bool:
    """Return whether ``path`` is ``base`` or below it (best-effort, no raise)."""
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def completion_target(shell: str) -> Path:
    """Return where the generated script for ``shell`` will be written.

    Raises:
        CompletionError: the home directory can't be resolved (so the path is
            undeterminable). Used by ``--dry-run`` for an informational line only.
    """
    if shell == "fish":
        return _fish_completion_path()
    if shell == "bash":
        return _bash_completion_path()
    return _zsh_completion_path()[0]


def installed_completion_files() -> list[Path]:
    """Return per-shell completion scripts that currently exist (for ``uninstall --purge``).

    Resolves each supported shell's target best-effort and keeps only files that exist,
    so purge removes exactly what ``completion install`` would have written -- never a
    guessed path. Per-shell resolution failures are skipped (logged at DEBUG).
    """
    found: list[Path] = []
    for shell in SHELLS:
        try:
            target = completion_target(shell)
            exists = target.exists()
        except (CompletionError, UnknownShellError, OSError) as exc:
            log.debug("completion: could not resolve %s target for purge: %s", shell, exc)
            continue
        if exists:
            found.append(target)
    return found


def _write_if_changed(path: Path, content: str) -> bool:
    """Atomically write ``content`` to ``path`` only if it differs; return changed.

    Mirrors the repo's temp-file + :func:`os.replace` atomic-write convention so a
    concurrent reader never sees a half-written completion script.
    """
    if not content.endswith("\n"):
        content += "\n"
    try:
        if path.read_text(encoding="utf-8") == content:
            return False
    except FileNotFoundError:
        pass
    except UnicodeDecodeError:
        pass  # an existing non-UTF-8 file is never our script -> overwrite it
    except OSError as exc:
        raise CompletionError(f"could not read {path}: {exc}") from exc
    # A unique temp file (not a shared ".name.tmp") so two concurrent installs to the
    # same target never clobber each other's temp; the dest replace is atomic. mkdir +
    # mkstemp are in the same guard so a bad target dir is a clean CompletionError.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    except OSError as exc:
        raise CompletionError(f"could not prepare {path.parent}: {exc}") from exc
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)  # atomic rename within the same dir
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise CompletionError(f"could not write {path}: {exc}") from exc
    return True


def ensure_zsh_fpath(rc: Path, directory: Path) -> bool:
    """Idempotently put ``directory`` on ``fpath`` + run ``compinit`` via ``rc``.

    Used only when no writable directory was found on the live ``$fpath``. Appends
    a single block under an exclusive ``flock`` (flush+fsync so concurrent installs
    can't double-append). Returns ``True`` if it wrote, ``False`` if the wiring is
    already present. Raises :class:`CompletionError` if ``rc`` can't be opened or
    written (e.g. it is a directory or read-only) — never a raw ``OSError``.

    Idempotency keys on the **functional** ``fpath=(…)`` line, not the marker
    comment: a stray leftover comment from a partial edit must not make us skip the
    actual wiring (which would write ``_spacelabel`` yet never load it).
    """
    # Shell-quote the path so a home dir with spaces/glob chars doesn't break the
    # fpath assignment when .zshrc is sourced.
    quoted = shlex.quote(str(directory))
    fpath_line = f"fpath=({quoted} $fpath)"
    block = f"{_HEADER}\n{fpath_line}\nautoload -Uz compinit && compinit\n"
    try:
        rc.parent.mkdir(parents=True, exist_ok=True)
        # "a+" creates the file if absent; O_APPEND keeps writes at EOF after seek(0).
        with rc.open("a+", encoding="utf-8", errors="surrogateescape") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0)
                existing = handle.read()
                if any(line.strip() == fpath_line for line in existing.splitlines()):
                    return False
                if existing and not existing.endswith("\n"):
                    handle.write("\n")
                handle.write(block)
                handle.flush()
                os.fsync(handle.fileno())
                return True
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        raise CompletionError(f"could not update {rc}: {exc}") from exc


def install_completion(shell: str) -> InstallResult:
    """Write click's generated completion script into ``shell``'s auto-load dir.

    Idempotent: rewrites only when the on-disk script differs. For zsh with no
    writable ``$fpath`` directory, also wires ``~/.zfunc`` onto ``fpath`` in
    ``.zshrc``. Raises :class:`CompletionError` on an unwritable target or an
    unresolvable home (the CLI maps it to a clean error).
    """
    script = generate_script(shell)
    needs_rc = False
    if shell == "fish":
        target = _fish_completion_path()
        hint = "Restart your shell; fish auto-loads the file."
    elif shell == "bash":
        if _bash_too_old():
            raise CompletionError(
                "the `bash` on PATH is older than 4.4 (macOS ships 3.2), which cannot "
                "use click completion. Install Homebrew bash + bash-completion@2, then retry."
            )
        target = _bash_completion_path()
        hint = "Restart your shell. Requires bash-completion v2 to be sourced."
    else:  # zsh
        target, needs_rc = _zsh_completion_path()
        hint = (
            "Restart your shell (or run `autoload -Uz compinit && compinit`). "
            "If completion does not appear, run `rm -f ~/.zcompdump*` and restart."
        )

    # Write the completion file FIRST; only touch .zshrc once it succeeded, so a
    # failed write never leaves a dangling fpath entry behind.
    changed = _write_if_changed(target, script)
    rc_added = False
    if needs_rc:
        rc_added = ensure_zsh_fpath(_home() / ".zshrc", target.parent)
        if rc_added:
            hint = f"Added {target.parent} to fpath in ~/.zshrc. " + hint
    return InstallResult(shell=shell, path=target, changed=changed or rc_added, hint=hint)
