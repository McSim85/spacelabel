"""Scaffold smoke tests: the package imports and the CLI entry point loads.

These deliberately avoid invoking command bodies (which raise until Phase 4) and
exercise only ``--help``/``--version``, which click handles eagerly.
"""

from __future__ import annotations

from click.testing import CliRunner

import spacelabel
from spacelabel import BUNDLE_ID
from spacelabel.cli import cli


def test_version_is_set() -> None:
    assert spacelabel.__version__


def test_bundle_id_constant() -> None:
    assert BUNDLE_ID == "dev.mcsim.spacelabel"


def test_cli_help_lists_agent() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "agent" in result.output


def test_cli_version_matches() -> None:
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert spacelabel.__version__ in result.output


def test_cli_exposes_the_locked_command_tree() -> None:
    # The command surface is locked (docs/CLI.md); --help must list every command.
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    for command in ("agent", "install", "uninstall", "status", "spaces", "mode", "label", "config"):
        assert command in result.output


def test_label_group_lists_subcommands() -> None:
    result = CliRunner().invoke(cli, ["label", "--help"])
    assert result.exit_code == 0
    for sub in ("set", "list", "clear", "prune"):
        assert sub in result.output
