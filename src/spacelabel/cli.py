"""Command-line interface — the single ``spacelabel`` console entry point.

A :mod:`click` group dispatches every subcommand (DESIGN.md §8.1). The long-lived
menu-bar agent is the ``agent`` subcommand (what the LaunchAgent runs); every
other subcommand is a one-shot action sharing the read/store layers. ``main()``
is the ``console_scripts`` entry point declared in ``pyproject.toml``.

Two invariants make the surface scriptable (DESIGN §8.1, DECISIONS 9.1/9.2):
stdout carries machine-readable data only (TSV by default, ``--json`` opt-in)
while every diagnostic, header, and progress line goes to stderr; and exit codes
are stable (``0`` ok, ``1`` runtime error, ``2`` usage error, ``3`` =
``status`` "agent not running"). Heavy imports (PyObjC via the platform/install
layers) stay lazy inside command bodies so ``--help``/``--version`` and dispatch
never pull AppKit.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import click

from spacelabel import __version__, completion, labeling, model, store
from spacelabel.logging_setup import LogMode, setup_logging

log = logging.getLogger(__name__)

_MODE_NAMES = ["menubar", "hud", "overlay", "wallpaper"]

#: LaunchAgent reverse-DNS label, reused in ``status`` output (DECISIONS 6.7).
_AGENT_LABEL = "dev.mcsim.spacelabel"


@dataclass(slots=True)
class AppContext:
    """Shared state passed from the root group to each subcommand."""

    config_path: Path | None
    verbose: bool
    debug: bool


def _paths(ctx: AppContext) -> store.StorePaths:
    """Resolve the on-disk store paths for the active ``--config`` selection."""
    return store.StorePaths.resolve(ctx.config_path)


def _diag(message: str) -> None:
    """Write a human diagnostic line to stderr (never the data channel)."""
    click.echo(message, err=True)


def _align_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """Return aligned, space-padded table lines (header line first, then rows).

    Each column is left-justified to its widest cell; the final column is not
    padded (no trailing whitespace). This is the human-facing format for
    ``spaces``/``label list``; machine consumers use ``--json`` (DECISIONS 9.2).
    """
    widths = [len(head) for head in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines: list[str] = []
    for cells in (headers, *rows):
        padded = [cells[i].ljust(widths[i]) for i in range(len(cells) - 1)]
        padded.append(cells[-1])
        lines.append("  ".join(padded).rstrip())
    return lines


def _echo_table(headers: list[str], rows: list[list[str]], *, color_current: bool = False) -> None:
    """Print an aligned table to stdout: bold header, optional green current rows.

    Colors use ``click.style``; ``click.echo`` strips ANSI automatically when
    stdout is not a terminal (piped/redirected), keeping the data channel clean.
    """
    lines = _align_table(headers, rows)
    if not lines:
        return
    click.echo(click.style(lines[0], bold=True))
    for line in lines[1:]:
        if color_current and line.startswith("*"):
            click.echo(click.style(line, fg="green", bold=True))
        else:
            click.echo(line)


def _config_keys_epilog() -> str:
    r"""Build the ``config``-help epilog listing every valid dotted key + its type.

    Sourced from ``store.CONFIG_SCHEMA`` so the help can never drift from the
    validator. A ``\b`` line tells click not to rewrap the aligned block.
    """
    lines = ["Valid keys (key -> type/constraint):", "", "\b"]
    for key in sorted(store.CONFIG_SCHEMA):
        lines.append(f"  {key:<28} {store.CONFIG_SCHEMA[key].description}")
    return "\n".join(lines)


_CONFIG_KEYS_EPILOG = _config_keys_epilog()


class _Command(click.Command):
    """A command that also accepts ``--verbose``/``--debug`` in the trailing position.

    The flags are global on the root group (``spacelabel --debug spaces``); this
    class adds them to every subcommand too (``spacelabel spaces --debug``), merges
    them into the shared :class:`AppContext`, re-raises the stderr log level, and
    pops them before the callback runs (so command bodies never see them).
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Append the verbosity flags unless the command already declares them."""
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        existing = {param.name for param in self.params}
        if "verbose" not in existing:
            self.params.append(
                click.Option(["--verbose"], is_flag=True, help="INFO-level logging on stderr.")
            )
        if "debug" not in existing:
            self.params.append(
                click.Option(["--debug"], is_flag=True, help="DEBUG-level logging on stderr.")
            )

    def invoke(self, ctx: click.Context) -> object:
        """Merge the trailing verbosity flags into the context, then invoke normally."""
        verbose = bool(ctx.params.pop("verbose", False))
        debug = bool(ctx.params.pop("debug", False))
        app = ctx.find_object(AppContext)
        if app is not None and (verbose or debug):
            app.verbose = app.verbose or verbose
            app.debug = app.debug or debug
            setup_logging(LogMode.CLI, verbose=app.verbose, debug=app.debug)
        return super().invoke(ctx)


class _Group(click.Group):
    """Group whose (sub)commands and nested groups use :class:`_Command`."""

    command_class = _Command


# Nested groups (label, config) inherit the same command behavior.
_Group.group_class = _Group


@click.group(cls=_Group, context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--config",
    "config_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to an alternate config.json.",
)
@click.option("--verbose", is_flag=True, help="Enable INFO-level logging on stderr.")
@click.option("--debug", is_flag=True, help="Enable DEBUG-level logging on stderr.")
@click.version_option(__version__, "-V", "--version", prog_name="spacelabel")
@click.pass_context
def cli(ctx: click.Context, config_path: Path | None, verbose: bool, debug: bool) -> None:
    """Label macOS Spaces (virtual desktops) by their stable UUID - reorder-proof."""
    setup_logging(LogMode.CLI, verbose=verbose, debug=debug)
    ctx.obj = AppContext(config_path=config_path, verbose=verbose, debug=debug)


@cli.command()
@click.pass_obj
def agent(ctx: AppContext) -> None:
    """Run the menu-bar agent in the foreground (what the LaunchAgent runs).

    ``--verbose``/``--debug`` work both before (``spacelabel --debug agent``) and
    after (``spacelabel agent --debug``) the subcommand; either raises the
    foreground log level for a dev run (merged into the context by ``_Command``).
    """
    from spacelabel.agent.app import run_agent

    run_agent(config_path=ctx.config_path, verbose=ctx.verbose, debug=ctx.debug)


@cli.command()
@click.option(
    "--no-load",
    "no_load",
    is_flag=True,
    help="Write/refresh the plist but do not load it now (loads at next login).",
)
def install(no_load: bool) -> None:
    """Install and load the login LaunchAgent."""
    from spacelabel import install as install_mod

    try:
        install_mod.install_agent(load=not no_load)
    except install_mod.InstallError as exc:
        log.error("install failed: %s", exc)
        raise click.ClickException(str(exc)) from exc
    if no_load:
        _diag(f"Installed {_AGENT_LABEL} (not loaded; loads at next login).")
    else:
        _diag(f"Installed and loaded {_AGENT_LABEL}.")


@cli.command()
@click.option(
    "--keep-labels",
    "keep_labels",
    is_flag=True,
    help="Reserved for a future destructive variant; labels are always kept today.",
)
def uninstall(keep_labels: bool) -> None:
    """Unload and remove the login LaunchAgent."""
    from spacelabel import install as install_mod

    # --keep-labels is the documented default behavior today (DESIGN/CLI.md 3.2):
    # labels.json and config.json are never deleted, so the flag is a no-op.
    del keep_labels
    try:
        install_mod.uninstall_agent()
    except install_mod.InstallError as exc:
        log.error("uninstall failed: %s", exc)
        raise click.ClickException(str(exc)) from exc
    _diag(f"Removed {_AGENT_LABEL} (labels and config kept).")


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON status object to stdout.")
@click.pass_context
def status(ctx: click.Context, as_json: bool) -> None:
    """Report whether the agent / LaunchAgent is running."""
    from spacelabel import install as install_mod

    try:
        running, pid = install_mod.agent_status()
    except install_mod.InstallError as exc:
        log.error("status query failed: %s", exc)
        raise click.ClickException(str(exc)) from exc

    if as_json:
        payload: dict[str, object] = {
            "running": running,
            "pid": pid,
            "label": _AGENT_LABEL,
        }
        click.echo(json.dumps(payload))
    elif running:
        pid_part = f"pid={pid}" if pid is not None else "pid=?"
        click.echo(f"running  {pid_part}  label={_AGENT_LABEL}")
    else:
        click.echo("not running")

    if not running:
        ctx.exit(3)


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON array to stdout.")
@click.option(
    "--active-display/--all-displays",
    "active_only",
    default=False,
    help="Restrict to the menu-bar-owning display (default: all displays).",
)
@click.pass_obj
def spaces(ctx: AppContext, as_json: bool, active_only: bool) -> None:
    """List current Spaces and their UUIDs, marking the active one.

    Includes Spaces macOS has not yet assigned a persistent UUID (a display's
    single default Space): these show a blank UUID and ``(no UUID)`` and cannot be
    labeled until macOS assigns one (e.g. after adding a Space on that display).
    """
    live = _read_spaces_with_fallback(include_unlabelable=True)

    if active_only:
        import spacelabel.platform.cgs as cgs_mod

        try:
            active_display = cgs_mod.active_display_uuid()
        except (cgs_mod.CGSUnavailableError, ImportError) as exc:  # ImportError == PyObjC absent
            log.error("could not resolve the active display: %s", exc)
            raise click.ClickException(
                "could not restrict to --active-display: the active display is unavailable."
            ) from exc
        if not active_display:
            # Never widen to all displays when the caller asked for just the active
            # one (would mislead --json scripts); fail instead.
            raise click.ClickException(
                "could not restrict to --active-display: no active display found."
            )
        live = [s for s in live if s.display_uuid == active_display]

    paths = _paths(ctx)
    labels = store.load_labels(paths)
    display_names = _display_names(paths)

    if as_json:
        records: list[dict[str, object]] = []
        for space in live:
            label = labels.get(space.uuid) if space.uuid else None
            # A notes-only entry (Label.text == "") is NOT a label — report it as
            # unlabeled (null), so `note add` on an unlabeled Space never makes it
            # look labeled (DECISIONS.md 9.10).
            label_text = label.text if (label is not None and label.text) else None
            records.append(
                {
                    "uuid": space.uuid or None,
                    "display_uuid": space.display_uuid,
                    "display_name": display_names.get(space.display_uuid),
                    "label": label_text,
                    # Task-queue size, so a notes-only Space is discoverable here
                    # rather than only via `note list <uuid>` (DECISIONS.md 9.10).
                    "notes": len(label.notes) if label is not None else 0,
                    "current": space.is_current,
                    "labelable": bool(space.uuid),
                }
            )
        click.echo(json.dumps(records))
        return

    rows: list[list[str]] = []
    for space in live:
        notes_count = 0
        if space.uuid:
            label = labels.get(space.uuid)
            uuid_cell = space.uuid
            # Empty text == notes-only == unlabeled (DECISIONS.md 9.10), not a blank label.
            label_cell = label.text if (label is not None and label.text) else "(unlabeled)"
            notes_count = len(label.notes) if label is not None else 0
        else:
            uuid_cell = "(none)"
            label_cell = "(no UUID)"
        rows.append(
            [
                "*" if space.is_current else "",
                display_names.get(space.display_uuid) or space.display_uuid,
                uuid_cell,
                label_cell,
                str(notes_count) if notes_count else "",  # blank for the common no-notes case
            ]
        )
    _echo_table(["CURRENT", "DISPLAY", "SPACE_UUID", "LABEL", "NOTES"], rows, color_current=True)


@cli.command()
@click.argument("name", type=click.Choice(_MODE_NAMES))
@click.option("--on/--off", "enabled", default=None, help="Enable or disable the mode.")
@click.pass_obj
def mode(ctx: AppContext, name: str, enabled: bool | None) -> None:
    """Show or toggle a display mode (menubar, hud, overlay, wallpaper)."""
    paths = _paths(ctx)
    if enabled is None:
        config = store.load_config(paths)
        state = bool(config.modes.get(name, False))
        click.echo(f"{name}: {'on' if state else 'off'}")
        return

    try:
        stored = store.set_config_value(paths, f"modes.{name}", str(enabled))
    except (store.ConfigKeyError, store.ConfigValueError) as exc:
        log.error("could not set mode %s: %s", name, exc)
        raise click.ClickException(str(exc)) from exc
    on = bool(stored)
    if name == "wallpaper" and on:
        _diag("WARNING: wallpaper mode is experimental and cosmetic; it may revert.")
    click.echo(f"{name}: {'on' if on else 'off'}")


@cli.group()
def label() -> None:
    """Create, list, and remove Space labels."""


@label.command("set")
@click.argument("target", shell_complete=completion.complete_space_target)
@click.argument("text")
@click.pass_obj
def label_set(ctx: AppContext, target: str, text: str) -> None:
    """Set the label for a Space UUID (or 'current')."""
    if not text.strip():
        raise click.BadParameter(
            "label text must not be empty (use 'label clear').", param_hint="TEXT"
        )

    uuid, last_display = _resolve_target(target, validate=True)
    paths = _paths(ctx)
    try:
        store.set_label(paths, uuid, text, last_display=last_display)
    except store.StoreError as exc:
        log.error("could not store label for %s: %s", uuid, exc)
        raise click.ClickException(str(exc)) from exc
    _diag(f"Labeled {uuid}: {text}")


@label.command("list")
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON array to stdout.")
@click.pass_obj
def label_list(ctx: AppContext, as_json: bool) -> None:
    """List all stored labels (machine-readable, to stdout).

    Notes-only entries (a task queue on an unlabeled Space, DECISIONS.md 9.10) are
    omitted here — they carry no label; surface them via ``note list``.
    """
    paths = _paths(ctx)
    labels = {uuid: entry for uuid, entry in store.load_labels(paths).items() if entry.text}
    if as_json:
        records = [
            {
                "uuid": uuid,
                "label": entry.text,
                "color": entry.color,
                "last_display": entry.last_display,
            }
            for uuid, entry in sorted(labels.items())
        ]
        click.echo(json.dumps(records))
        return
    rows = [[uuid, entry.text] for uuid, entry in sorted(labels.items())]
    _echo_table(["SPACE_UUID", "LABEL"], rows)


@label.command("clear")
@click.argument("target", shell_complete=completion.complete_label_clear_target)
@click.pass_obj
def label_clear(ctx: AppContext, target: str) -> None:
    """Clear the label for a Space UUID (or 'current')."""
    uuid, _ = _resolve_target(target)
    paths = _paths(ctx)
    try:
        existed = store.clear_label(paths, uuid)
    except store.StoreError as exc:
        log.error("could not clear label for %s: %s", uuid, exc)
        raise click.ClickException(str(exc)) from exc
    if existed:
        _diag(f"Cleared label for {uuid}.")
    else:
        # Idempotent clear: note to stderr, still exit 0 (CLI.md 3.6).
        _diag(f"No label stored for {uuid}; nothing to clear.")


@label.command("prune")
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="List labels that would be removed without changing anything.",
)
@click.pass_obj
def label_prune(ctx: AppContext, dry_run: bool) -> None:
    """Drop labels for Spaces that no longer exist."""
    live = _read_spaces_with_fallback()
    live_uuids = {space.uuid for space in live}
    paths = _paths(ctx)
    labels = store.load_labels(paths)

    # DATA SAFETY: an empty live set almost always means the read failed (a real
    # session always has >=1 Space), not that every Space vanished. Pruning against
    # it would delete EVERY label, so refuse and surface the failure (exit 1).
    if not live_uuids:
        raise click.ClickException(
            "refusing to prune: no live Spaces could be read "
            "(CGS path and plist fallback both yielded nothing)."
        )

    if dry_run:
        orphans = labeling.find_orphans(labels, live_uuids)
        for uuid in orphans:
            click.echo(uuid)
        _diag(f"Would remove {len(orphans)} orphaned label(s).")
        return

    try:
        removed = store.prune_labels(paths, live_uuids)
    except store.StoreError as exc:
        log.error("could not prune labels: %s", exc)
        raise click.ClickException(str(exc)) from exc
    for uuid in removed:
        click.echo(uuid)
    _diag(f"Removed {len(removed)} orphaned label(s).")


@cli.group()
def note() -> None:
    """Manage a Space's task queue (notes), keyed by Space UUID like labels."""


def _load_notes(paths: store.StorePaths, uuid: str) -> list[model.Note]:
    """Return the stored notes for ``uuid`` (canonicalized), or ``[]`` if none.

    Used by the read-only ``note list``; the mutating commands validate the index
    inside the store's locked read-modify-write (see :class:`store.NoteIndexError`)
    rather than pre-reading here, so a corrupt store or a concurrent edit can't
    misclassify the exit code (DECISIONS.md 9.1/9.10).
    """
    entry = store.load_labels(paths).get(labeling.canonical_uuid(uuid))
    return entry.notes if entry is not None else []


@note.command("add")
@click.argument("target", shell_complete=completion.complete_note_target)
@click.argument("text")
@click.pass_obj
def note_add(ctx: AppContext, target: str, text: str) -> None:
    """Append a task to a Space's queue (UUID or 'current')."""
    if not text.strip():
        raise click.BadParameter("note text must not be empty.", param_hint="TEXT")
    uuid, _ = _resolve_target(target, validate=True)
    paths = _paths(ctx)
    try:
        label = store.add_note(paths, uuid, text)
    except store.StoreError as exc:
        log.error("could not add note for %s: %s", uuid, exc)
        raise click.ClickException(str(exc)) from exc
    _diag(f"Added task #{len(label.notes)} to {uuid}: {text}")


@note.command("list")
@click.argument("target", required=False, shell_complete=completion.complete_note_target)
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON array to stdout.")
@click.pass_obj
def note_list(ctx: AppContext, target: str | None, as_json: bool) -> None:
    """List a Space's task queue, or with no TARGET every Space that has notes.

    With no TARGET this enumerates all note-bearing entries (UUID + task count) so a
    notes-only queue stays discoverable and recoverable even when its Space is not
    live (``spaces`` shows only live Spaces; DECISIONS.md 9.10). stdout = data.
    """
    paths = _paths(ctx)
    if target is None:
        entries = {u: e for u, e in store.load_labels(paths).items() if e.notes}
        if as_json:
            summary = [{"uuid": u, "notes": len(e.notes)} for u, e in sorted(entries.items())]
            click.echo(json.dumps(summary))
            return
        rows = [[u, str(len(e.notes))] for u, e in sorted(entries.items())]
        _echo_table(["SPACE_UUID", "NOTES"], rows)
        return
    uuid, _ = _resolve_target(target)
    notes = _load_notes(paths, uuid)
    if as_json:
        records = [
            {"index": index, "text": item.text, "done": item.done}
            for index, item in enumerate(notes, start=1)
        ]
        click.echo(json.dumps(records))
        return
    rows = [
        [str(index), "[x]" if item.done else "[ ]", item.text]
        for index, item in enumerate(notes, start=1)
    ]
    _echo_table(["#", "DONE", "TASK"], rows)


def _set_note_done(ctx: AppContext, target: str, index: int, done: bool) -> None:
    """Shared body for ``note done``/``note undone`` (mark task ``index`` done state).

    ``index`` is the human 1-based value; the store validates it (0-based) inside the
    lock. A bad/out-of-range index → usage error (exit 2); a store failure → exit 1.
    """
    uuid, _ = _resolve_target(target)
    paths = _paths(ctx)
    try:
        item = store.set_note_done(paths, uuid, index - 1, done)
    except store.NoteIndexError as exc:
        raise click.BadParameter(str(exc), param_hint="INDEX") from exc
    except store.StoreError as exc:
        log.error("could not update note %d for %s: %s", index, uuid, exc)
        raise click.ClickException(f"could not update task #{index} for {uuid}.") from exc
    _diag(f"Task #{index} for {uuid} marked {'done' if item.done else 'not done'}: {item.text}")


@note.command("done")
@click.argument("target", shell_complete=completion.complete_note_target)
@click.argument("index", type=int)
@click.pass_obj
def note_done(ctx: AppContext, target: str, index: int) -> None:
    """Mark task INDEX (1-based) done."""
    _set_note_done(ctx, target, index, True)


@note.command("undone")
@click.argument("target", shell_complete=completion.complete_note_target)
@click.argument("index", type=int)
@click.pass_obj
def note_undone(ctx: AppContext, target: str, index: int) -> None:
    """Mark task INDEX (1-based) not done."""
    _set_note_done(ctx, target, index, False)


@note.command("clear")
@click.argument("target", shell_complete=completion.complete_note_target)
@click.argument("index", type=int, required=False)
@click.pass_obj
def note_clear(ctx: AppContext, target: str, index: int | None) -> None:
    """Remove one task (INDEX, 1-based) or, with no INDEX, the whole queue."""
    uuid, _ = _resolve_target(target)
    paths = _paths(ctx)
    if index is None:
        # Clear-all is idempotent (decided + counted inside the store lock).
        try:
            removed = store.clear_note(paths, uuid)
        except store.StoreError as exc:
            log.error("could not clear notes for %s: %s", uuid, exc)
            raise click.ClickException(str(exc)) from exc
        if removed:
            _diag(f"Cleared {removed} task(s) for {uuid}.")
        else:
            _diag(f"No tasks stored for {uuid}; nothing to clear.")
        return
    try:
        store.clear_note(paths, uuid, index - 1)
    except store.NoteIndexError as exc:
        raise click.BadParameter(str(exc), param_hint="INDEX") from exc
    except store.StoreError as exc:
        log.error("could not remove note %d for %s: %s", index, uuid, exc)
        raise click.ClickException(f"could not remove task #{index} for {uuid}.") from exc
    _diag(f"Removed task #{index} for {uuid}.")


@cli.group()
def config() -> None:
    """Read and write configuration values."""


@config.command("get", epilog=_CONFIG_KEYS_EPILOG)
@click.argument("key", required=False, shell_complete=completion.complete_config_key)
@click.pass_obj
def config_get(ctx: AppContext, key: str | None) -> None:
    """Print a single configuration value, or the whole config as JSON if no key."""
    paths = _paths(ctx)
    loaded = store.load_config(paths)
    if key is None:
        click.echo(json.dumps(store.config_to_dict(loaded), indent=2))
        return
    try:
        value = store.get_config_value(loaded, key)
    except store.ConfigKeyError as exc:
        log.error("unknown config key %r: %s", key, exc)
        raise click.ClickException(str(exc)) from exc
    click.echo(store.format_scalar(value))


@config.command("set", epilog=_CONFIG_KEYS_EPILOG)
@click.argument("key", shell_complete=completion.complete_config_key)
@click.argument("value")
@click.pass_obj
def config_set(ctx: AppContext, key: str, value: str) -> None:
    """Set a single configuration value."""
    paths = _paths(ctx)
    try:
        stored = store.set_config_value(paths, key, value)
    except (store.ConfigKeyError, store.ConfigValueError) as exc:
        log.error("could not set %r: %s", key, exc)
        raise click.ClickException(str(exc)) from exc
    click.echo(store.format_scalar(stored))


@cli.group()
def display() -> None:
    """Rename displays (custom names shown in the menu, prefs, and ``spaces``)."""


@display.command("set")
@click.argument("target", shell_complete=completion.complete_display_target)
@click.argument("name")
@click.pass_obj
def display_set(ctx: AppContext, target: str, name: str) -> None:
    """Set a custom name for a display UUID (or 'current')."""
    if not name.strip():
        raise click.BadParameter(
            "display name must not be empty (use 'display clear').", param_hint="NAME"
        )
    uuid = _resolve_display_target(target, validate=True)
    paths = _paths(ctx)
    try:
        store.set_display_label(paths, uuid, name)
    except store.StoreError as exc:
        log.error("could not name display %s: %s", uuid, exc)
        raise click.ClickException(str(exc)) from exc
    _diag(f"Named display {uuid}: {name}")


@display.command("list")
@click.option("--json", "as_json", is_flag=True, help="Emit a JSON array to stdout.")
@click.pass_obj
def display_list(ctx: AppContext, as_json: bool) -> None:
    """List connected displays and their custom or system names."""
    from spacelabel.platform import displays as displays_mod

    paths = _paths(ctx)
    overrides = store.load_display_labels(paths)
    try:
        topology = displays_mod.discover_topology()
    except (OSError, ImportError) as exc:  # ImportError == PyObjC absent; show stored names only
        log.warning("display discovery failed; showing stored names only: %s", exc)
        topology = []
    active = _active_display_uuid_or_none()

    if as_json:
        if topology:
            records = [
                {
                    "uuid": disp.uuid,
                    "name": displays_mod.resolved_name(disp, overrides),
                    "custom": disp.uuid in overrides,
                    "active": disp.uuid == active,
                }
                for disp in topology
            ]
        else:
            records = [
                {"uuid": uuid, "name": name, "custom": True, "active": uuid == active}
                for uuid, name in sorted(overrides.items())
            ]
        click.echo(json.dumps(records))
        return

    if topology:
        rows = [
            [
                "*" if disp.uuid == active else "",
                disp.uuid,
                displays_mod.resolved_name(disp, overrides),
                "custom" if disp.uuid in overrides else "system",
            ]
            for disp in topology
        ]
    else:
        rows = [["", uuid, name, "custom"] for uuid, name in sorted(overrides.items())]
    _echo_table(["CURRENT", "DISPLAY_UUID", "NAME", "SOURCE"], rows, color_current=True)


@display.command("clear")
@click.argument("target", shell_complete=completion.complete_display_target)
@click.pass_obj
def display_clear(ctx: AppContext, target: str) -> None:
    """Clear a display's custom name (revert to the system name)."""
    uuid = _resolve_display_target(target)
    paths = _paths(ctx)
    existed = uuid in store.load_display_labels(paths)
    try:
        store.set_display_label(paths, uuid, "")
    except store.StoreError as exc:
        log.error("could not clear display name for %s: %s", uuid, exc)
        raise click.ClickException(str(exc)) from exc
    if existed:
        _diag(f"Cleared custom name for display {uuid}.")
    else:
        _diag(f"No custom name stored for display {uuid}; nothing to clear.")


def _resolve_display_target(target: str, *, validate: bool = False) -> str:
    """Resolve a ``display`` TARGET ('current' -> active display UUID, else literal).

    A literal UUID is canonicalized so it matches the canonical stored keys
    (``display clear``'s ``existed`` pre-check and ``display set``'s diagnostics
    compare against ``load_display_labels``, which canonicalizes on read).
    ``validate=True`` (``display set``) rejects a non-UUID literal; ``display clear``
    leaves it off so a legacy non-UUID display key can still be cleared.
    """
    if target != "current":
        if validate and not labeling.is_uuid(target):
            raise click.BadParameter(
                f"{target!r} is not a display UUID or 'current' "
                "(list display UUIDs with `spacelabel display list`).",
                param_hint="TARGET",
            )
        return labeling.canonical_uuid(target)
    import spacelabel.platform.cgs as cgs_mod

    try:
        uuid = cgs_mod.active_display_uuid()
    except (cgs_mod.CGSUnavailableError, ImportError) as exc:  # ImportError == PyObjC absent
        log.error("could not resolve 'current' display: %s", exc)
        raise click.ClickException(
            "could not resolve 'current': the active display is unavailable."
        ) from exc
    if not uuid:
        raise click.ClickException("could not resolve 'current': no active display found.")
    return uuid


def _active_display_uuid_or_none() -> str | None:
    """Best-effort active display UUID for marking; None if unavailable.

    The active marker is cosmetic, so every unavailability mode degrades to "no
    active display" rather than aborting the command: ``CGSUnavailableError`` when
    the CGS symbols don't resolve, and ``ImportError`` when PyObjC/AppKit is absent
    (``active_display_uuid`` imports AppKit lazily), so ``spaces`` / ``display list``
    still print stored metadata on a host without the framework.
    """
    import spacelabel.platform.cgs as cgs_mod

    try:
        return cgs_mod.active_display_uuid() or None
    except (cgs_mod.CGSUnavailableError, ImportError) as exc:
        log.debug("active display unavailable: %s", exc)
        return None


def _resolve_target(target: str, *, validate: bool = False) -> tuple[str, str | None]:
    """Resolve a ``label`` TARGET to a (uuid, active_display_uuid) pair.

    ``current`` resolves to the live active Space UUID via the CGS read (its
    display becomes ``last_display``); any other value is treated as a literal
    UUID. A failed/empty live read for ``current`` is a runtime error (exit 1).

    ``validate=True`` (the create paths — ``label set`` / ``note add``) rejects a
    literal that is not a well-formed UUID (e.g. a transposed ``note add list
    current``) as a usage error. The operate/read paths (``clear``/``done``/``list``)
    pass ``validate=False`` so a **pre-existing** non-UUID key (a legacy typo/orphan
    like ``list``) can still be inspected or removed from the CLI (DECISIONS.md 9.10).
    """
    import spacelabel.platform.cgs as cgs_mod

    if target != "current":
        if validate and not labeling.is_uuid(target):
            raise click.BadParameter(
                f"{target!r} is not a Space UUID or 'current' "
                "(list Space UUIDs with `spacelabel spaces`).",
                param_hint="TARGET",
            )
        return labeling.canonical_uuid(target), None

    try:
        uuid = cgs_mod.read_active_space_uuid()
    except (cgs_mod.CGSUnavailableError, ImportError) as exc:
        # ImportError == PyObjC absent; a literal 'current' genuinely needs the live
        # read, so fail cleanly (exit 1) rather than surfacing a raw traceback.
        log.error("could not resolve 'current': CGS unavailable: %s", exc)
        raise click.ClickException(
            "could not resolve 'current': the live Space read is unavailable."
        ) from exc
    if not uuid:
        raise click.ClickException("could not resolve 'current': no active Space found.")

    last_display: str | None = None
    try:
        last_display = cgs_mod.active_display_uuid() or None
    except (cgs_mod.CGSUnavailableError, ImportError) as exc:
        # Non-fatal: the label still stores; last_display is just grouping metadata.
        log.warning("active display unavailable for 'current'; omitting last_display: %s", exc)
    return uuid, last_display


def _read_spaces_with_fallback(*, include_unlabelable: bool = False) -> list[model.Space]:
    """Read live Spaces via CGS, falling back to the spaces plist parser.

    A live CGS read is preferred; on :class:`CGSUnavailableError` the stale-but-
    durable ``com.apple.spaces.plist`` topology is parsed instead (DESIGN §3.4).
    A runtime error (exit 1) is raised only when both paths fail.

    Args:
        include_unlabelable: Pass through to the live read so the ``spaces`` listing
            can surface Spaces with no assigned UUID (the plist fallback only ever
            yields labelable Spaces).
    """
    import spacelabel.platform.cgs as cgs_mod

    try:
        return cgs_mod.enumerate_spaces(include_unlabelable=include_unlabelable)
    except (cgs_mod.CGSUnavailableError, ImportError) as exc:
        # ImportError == PyObjC absent (enumerate_spaces imports objc lazily); the
        # plist fallback is pure stdlib, so it is exactly the path that should run.
        log.warning("CGS read unavailable; falling back to spaces plist: %s", exc)

    from spacelabel.platform import spaces_plist

    # read_spaces() recovers its own file errors to [], so reaching here with an
    # empty result means the CGS path is gone AND the plist yielded nothing -- both
    # read paths failed, which the contract maps to exit 1 (not an empty success).
    fallback = spaces_plist.read_spaces()
    if not fallback:
        raise click.ClickException(
            "could not read Spaces: the CGS path is unavailable and the plist "
            "fallback yielded nothing."
        )
    return fallback


def _display_names(paths: store.StorePaths) -> dict[str, str]:
    """Best-effort map of display UUID to its resolved name (empty on any failure).

    Uses the user's custom display name when set (``displays.json``), else the
    friendly name. Names are decorative for the ``spaces`` output, so a discovery
    failure logs and degrades to an empty map rather than failing the command.
    """
    from spacelabel.platform import displays

    names: dict[str, str] = {}
    try:
        topology = displays.discover_topology()
    except (OSError, ImportError) as exc:  # ImportError == PyObjC absent; names are decorative
        log.warning("display discovery failed; names omitted: %s", exc)
        return names
    overrides = store.load_display_labels(paths)
    for disp in topology:
        names[disp.uuid] = displays.resolved_name(disp, overrides)
    return names


@cli.group("completion")
def completion_group() -> None:
    """Manage shell tab-completion (zsh/bash/fish)."""


@completion_group.command("install")
@click.option(
    "--shell",
    "shell",
    type=click.Choice(["auto", *completion.SHELLS]),
    default="auto",
    help="Target shell (default: auto-detect from $SHELL).",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    help="Print the generated completion script (to stdout) without writing any file.",
)
def completion_install(shell: str, dry_run: bool) -> None:
    """Install tab-completion by writing the generated script to your shell's dir.

    Writes click's generated completion script into the shell's auto-load directory
    (fish/bash need no rc edit; zsh drops ``_spacelabel`` onto ``$fpath``). The
    script is printed on stdout (the data channel) for ``--dry-run`` so it can be
    piped/redirected; all human guidance goes to stderr. Writing is idempotent.
    """
    if shell == "auto":
        try:
            shell = completion.detect_shell(os.environ.get("SHELL", ""))
        except completion.UnknownShellError as exc:
            raise click.ClickException(str(exc)) from exc

    try:
        script = completion.generate_script(shell)
    except completion.CompletionError as exc:
        log.error("could not generate completion script: %s", exc)
        raise click.ClickException(str(exc)) from exc

    if dry_run:
        # The script itself never needs $HOME; print it, then show the target
        # best-effort so a home-less context still emits the script (exit 0).
        click.echo(script)
        try:
            target = completion.completion_target(shell)
            _diag(f"Would write the above to {target} (run without --dry-run to apply).")
        except completion.CompletionError as exc:
            _diag(f"Would write the above to your {shell} completion dir (target n/a: {exc}).")
        return

    try:
        result = completion.install_completion(shell)
    except completion.CompletionError as exc:
        log.error("completion install failed: %s", exc)
        raise click.ClickException(str(exc)) from exc

    verb = "Wrote" if result.changed else "Already up to date —"
    _diag(f"{verb} {result.shell} completion at {result.path}. {result.hint}")


def main() -> None:
    """Console entry point - dispatch the ``spacelabel`` command group."""
    cli()


if __name__ == "__main__":
    main()
