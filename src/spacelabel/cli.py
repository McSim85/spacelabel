"""Command-line interface — the single ``spacelabel`` console entry point.

A :mod:`click` group dispatches every subcommand (DESIGN.md §8.1). The long-lived
menu-bar agent is the ``agent`` subcommand (what the LaunchAgent runs); every
other subcommand is a one-shot action sharing the read/store layers. ``main()``
is the ``console_scripts`` entry point declared in ``pyproject.toml``.

Subcommands are scaffolded here (Phase 2) and implemented in Phase 4; until then
each one-shot command exits cleanly with a "not implemented" message rather than
a traceback. Heavy imports (PyObjC, store, agent) stay lazy inside command bodies
so ``--help``/``--version`` and dispatch never pull AppKit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import click

from spacelabel import __version__
from spacelabel.logging_setup import LogMode, setup_logging

log = logging.getLogger(__name__)

_MODE_NAMES = ["menubar", "hud", "overlay", "wallpaper"]


@dataclass(slots=True)
class AppContext:
    """Shared state passed from the root group to each subcommand."""

    config_path: Path | None
    verbose: bool
    debug: bool


def _todo(feature: str) -> NoReturn:
    """Raise a clean user-facing error for a not-yet-implemented command."""
    raise click.ClickException(
        f"`{feature}` is scaffolded but not implemented yet (built in Phase 4)."
    )


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
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
    """Label macOS Spaces (virtual desktops) by their stable UUID — reorder-proof."""
    setup_logging(LogMode.CLI, verbose=verbose, debug=debug)
    ctx.obj = AppContext(config_path=config_path, verbose=verbose, debug=debug)


@cli.command()
@click.pass_obj
def agent(ctx: AppContext) -> None:
    """Run the menu-bar agent in the foreground (what the LaunchAgent runs)."""
    from spacelabel.agent.app import run_agent

    run_agent(config_path=ctx.config_path)


@cli.command()
def install() -> None:
    """Install and load the login LaunchAgent."""
    _todo("install")


@cli.command()
def uninstall() -> None:
    """Unload and remove the login LaunchAgent."""
    _todo("uninstall")


@cli.command()
def status() -> None:
    """Report whether the agent / LaunchAgent is running."""
    _todo("status")


@cli.command()
def spaces() -> None:
    """List current Spaces and their UUIDs, marking the active one."""
    _todo("spaces")


@cli.command()
@click.argument("name", type=click.Choice(_MODE_NAMES))
@click.option("--on/--off", "enabled", default=None, help="Enable or disable the mode.")
def mode(name: str, enabled: bool | None) -> None:
    """Show or toggle a display mode (menubar, hud, overlay, wallpaper)."""
    _todo("mode")


@cli.group()
def label() -> None:
    """Create, list, and remove Space labels."""


@label.command("set")
@click.argument("target")
@click.argument("text")
def label_set(target: str, text: str) -> None:
    """Set the label for a Space UUID (or 'current')."""
    _todo("label set")


@label.command("list")
def label_list() -> None:
    """List all stored labels (machine-readable, to stdout)."""
    _todo("label list")


@label.command("clear")
@click.argument("target")
def label_clear(target: str) -> None:
    """Clear the label for a Space UUID (or 'current')."""
    _todo("label clear")


@label.command("prune")
def label_prune() -> None:
    """Drop labels for Spaces that no longer exist."""
    _todo("label prune")


@cli.group()
def config() -> None:
    """Read and write configuration values."""


@config.command("get")
@click.argument("key")
def config_get(key: str) -> None:
    """Print a single configuration value."""
    _todo("config get")


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """Set a single configuration value."""
    _todo("config set")


def main() -> None:
    """Console entry point — dispatch the ``spacelabel`` command group."""
    cli()


if __name__ == "__main__":
    main()
