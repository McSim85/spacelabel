"""Shell tab-completion: dynamic completers + the ``completion install`` helper.

Two kinds of completion live here, kept out of :mod:`spacelabel.cli` so the
command module stays lean:

* **Dynamic completers** for the UUID-bearing positional arguments (``label``,
  ``note``, ``display`` targets) and the ``config`` key. Click invokes these only
  while completing (the ``_SPACELABEL_COMPLETE`` env var is set by the shell
  snippet), so the live CGS / display / store reads stay lazy — normal dispatch
  never pulls AppKit. A completer must never crash the user's shell, so every read
  degrades to ``[]`` on a *specific*, logged failure (never a bare ``except``).
* **The activation snippet** click ships built-in (zsh/bash/fish) plus an
  idempotent installer that writes it into the right per-shell rc file.
"""

from __future__ import annotations

import fcntl
import logging
import os
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


def _stored_uuids(ctx: click.Context, *, with_notes: bool) -> list[str]:
    """Return stored Space UUIDs: labeled ones, or note-bearing when ``with_notes``.

    Used to enrich the ``clear``/operate completers so an existing entry whose
    Space is not currently live is still completable; ``[]`` on any read failure.
    """
    # RuntimeError covers Path.home() failing to resolve a home dir (unusual launch
    # contexts) inside _paths_from_ctx — a completer must never raise into the shell.
    try:
        labels = store.load_labels(_paths_from_ctx(ctx))
    except (store.StoreError, OSError, RuntimeError) as exc:
        log.debug("completion: store read failed: %s", exc)
        return []
    if with_notes:
        return [uuid for uuid, entry in labels.items() if entry.notes]
    return [uuid for uuid, entry in labels.items() if entry.text]


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
    candidates = ["current", *_live_space_uuids(), *_stored_uuids(ctx, with_notes=False)]
    return _filter(candidates, incomplete)


def complete_note_target(ctx: click.Context, param: click.Parameter, incomplete: str) -> list[str]:
    """Complete a ``note`` TARGET: ``current`` + live + note-bearing UUIDs."""
    del param
    candidates = ["current", *_live_space_uuids(), *_stored_uuids(ctx, with_notes=True)]
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


# --- activation snippet + idempotent installer -------------------------------


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


def completion_snippet(shell: str) -> str:
    """Return the one-line activation snippet click documents for ``shell``."""
    if shell == "fish":
        return f"{_COMPLETE_VAR}=fish_source {_PROG} | source"
    return f'eval "$({_COMPLETE_VAR}={shell}_source {_PROG})"'


def completion_rc_path(shell: str, home: Path) -> Path:
    """Return the rc file the snippet belongs in for ``shell`` (under ``home``).

    bash on macOS starts interactive Terminal/iTerm sessions as *login* shells,
    which read ``~/.bash_profile`` then ``~/.profile`` — **not** ``~/.bashrc``
    (which a login shell sources only if a login rc file chains to it). So target a
    login rc file: an existing ``~/.bash_profile``/``~/.profile``, else create
    ``~/.bash_profile``. Targeting ``~/.bashrc`` would report success yet never
    activate for the common macOS setup.
    """
    if shell == "fish":
        return home / ".config" / "fish" / "completions" / f"{_PROG}.fish"
    if shell == "zsh":
        return home / ".zshrc"
    for name in (".bash_profile", ".profile"):
        candidate = home / name
        if candidate.exists():
            return candidate
    return home / ".bash_profile"


def ensure_line(path: Path, line: str) -> bool:
    """Append ``line`` (with the header comment) to ``path`` unless already present.

    Idempotent: returns ``True`` if it wrote the snippet, ``False`` if the exact
    line was already in the file. Creates parent directories (the fish completions
    dir may not exist) and a final newline before appending so it never glues onto
    a partial last line. The whole check-then-append runs under an exclusive
    ``flock`` (the repo's standard atomic-write guard) so two concurrent installs
    can't both observe the line absent and double-append.

    Presence is an **exact full-line** match (after stripping surrounding
    whitespace), not a substring: the snippet appearing inside a comment, a quoted
    example, or a heredoc does not count as enabled — otherwise the install would
    no-op while the shell never actually runs the activation command.

    surrogateescape round-trips any stray non-UTF-8 bytes losslessly, so reading a
    rc file with odd bytes never raises ``UnicodeDecodeError``; the ASCII snippet
    still matches reliably for the idempotency check, and we only append ASCII.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # "a+" creates the file if absent; O_APPEND keeps writes at EOF even after the
    # seek(0) read, so concurrent appends never clobber each other's bytes.
    with path.open("a+", encoding="utf-8", errors="surrogateescape") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            existing = handle.read()
            if any(existing_line.strip() == line for existing_line in existing.splitlines()):
                return False
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write(f"{_HEADER}\n{line}\n")
            # Flush Python's buffer to the OS (and sync to disk) BEFORE releasing the
            # lock, so a racing installer that then acquires it reads our append, not
            # the stale contents — otherwise the flock wouldn't actually serialize.
            handle.flush()
            os.fsync(handle.fileno())
            return True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
