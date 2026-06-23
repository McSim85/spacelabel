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


def test_version_from_app_bundle_only_trusts_our_bundle(tmp_path, monkeypatch) -> None:
    # The frozen-bundle version fallback must read the version only from OUR bundle's
    # Info.plist — never borrow a host app's version when run under a foreign bundle.
    import plistlib

    contents = tmp_path / "Host.app" / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    exe = contents / "MacOS" / "exe"
    exe.write_text("")
    monkeypatch.setattr(spacelabel.sys, "executable", str(exe))

    (contents / "Info.plist").write_bytes(
        plistlib.dumps(
            {"CFBundleIdentifier": "com.other.app", "CFBundleShortVersionString": "9.9.9"}
        )
    )
    assert spacelabel._version_from_app_bundle() is None  # foreign bundle -> not trusted

    (contents / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": BUNDLE_ID, "CFBundleShortVersionString": "1.2.3"})
    )
    assert spacelabel._version_from_app_bundle() == "1.2.3"  # our bundle -> use it


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
