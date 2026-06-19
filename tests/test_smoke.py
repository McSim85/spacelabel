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


def test_unimplemented_command_errors_cleanly() -> None:
    result = CliRunner().invoke(cli, ["spaces"])
    # A clean ClickException (exit 1), not a traceback.
    assert result.exit_code == 1
    assert "not implemented" in result.output.lower()
