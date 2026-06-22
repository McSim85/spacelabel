"""Unit tests for shell completion: dynamic completers + the install helper.

The live readers (CGS, display topology, the JSON store) are monkeypatched, so
these run on a hosted CI runner with no WindowServer. Completers must degrade to
``[]`` on a read failure rather than raise (a traceback would break the shell).
"""

from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

from spacelabel import completion, store
from spacelabel.cli import cli
from spacelabel.model import Display, Label, Note, Space

U1 = "6622AC87-2FD2-48E8-934D-F6EB303AC9BA"
U2 = "1A0F5C2E-7B3D-4C8A-9E1F-2D4B6A8C0E12"
U3 = "9F8E7D6C-5B4A-3210-FEDC-BA9876543210"
DISP_A = "874A623F-1111-2222-3333-444455556666"
DISP_B = "6FBB92D9-84CE-8D20-C114-3B1052DD9529"


@pytest.fixture
def ctx() -> click.Context:
    """Return a bare context; completers read --config off root params (here: none)."""
    return click.Context(click.Command("spacelabel"))


# ---- snippet / path / detect helpers (pure) --------------------------------


def test_completion_snippet_per_shell() -> None:
    assert completion.completion_snippet("zsh") == (
        'eval "$(_SPACELABEL_COMPLETE=zsh_source spacelabel)"'
    )
    assert completion.completion_snippet("bash") == (
        'eval "$(_SPACELABEL_COMPLETE=bash_source spacelabel)"'
    )
    assert completion.completion_snippet("fish") == (
        "_SPACELABEL_COMPLETE=fish_source spacelabel | source"
    )


def test_completion_rc_path_per_shell(tmp_path) -> None:
    assert completion.completion_rc_path("zsh", tmp_path) == tmp_path / ".zshrc"
    assert completion.completion_rc_path("fish", tmp_path) == (
        tmp_path / ".config" / "fish" / "completions" / "spacelabel.fish"
    )
    # bash on macOS is a login shell: default to .bash_profile when nothing exists.
    assert completion.completion_rc_path("bash", tmp_path) == tmp_path / ".bash_profile"


def test_completion_rc_path_bash_prefers_login_files(tmp_path) -> None:
    # A login shell won't source ~/.bashrc, so a bashrc-only home still targets the
    # login rc (.bash_profile) — never .bashrc (would report success but not activate).
    (tmp_path / ".bashrc").write_text("# bashrc\n", encoding="utf-8")
    assert completion.completion_rc_path("bash", tmp_path) == tmp_path / ".bash_profile"
    # An existing ~/.profile (a login rc) is respected.
    (tmp_path / ".profile").write_text("# profile\n", encoding="utf-8")
    assert completion.completion_rc_path("bash", tmp_path) == tmp_path / ".profile"


@pytest.mark.parametrize("shell_env", ["/bin/zsh", "/usr/local/bin/bash", "/opt/homebrew/bin/fish"])
def test_detect_shell_supported(shell_env) -> None:
    assert completion.detect_shell(shell_env) in completion.SHELLS


@pytest.mark.parametrize("shell_env", ["", "/bin/tcsh", "/bin/sh"])
def test_detect_shell_unsupported_raises(shell_env) -> None:
    with pytest.raises(completion.UnknownShellError):
        completion.detect_shell(shell_env)


# ---- ensure_line (idempotent append) ---------------------------------------


def test_ensure_line_writes_then_is_idempotent(tmp_path) -> None:
    rc = tmp_path / ".zshrc"
    rc.write_text("export FOO=1", encoding="utf-8")  # no trailing newline
    line = completion.completion_snippet("zsh")

    assert completion.ensure_line(rc, line) is True
    body = rc.read_text(encoding="utf-8")
    assert "export FOO=1\n" in body  # a newline was inserted before our block
    assert completion._HEADER in body
    assert body.endswith(line + "\n")

    # Second call must not duplicate the line.
    assert completion.ensure_line(rc, line) is False
    assert rc.read_text(encoding="utf-8").count(line) == 1


def test_ensure_line_ignores_commented_or_quoted_snippet(tmp_path) -> None:
    rc = tmp_path / ".zshrc"
    line = completion.completion_snippet("zsh")
    # The snippet appears only as an inert comment / quoted example — not enabled.
    rc.write_text(f"# example: {line}\necho '{line}'\n", encoding="utf-8")
    assert completion.ensure_line(rc, line) is True  # must still install a real line
    # A real, uncommented line is now present -> a second call no-ops.
    assert completion.ensure_line(rc, line) is False
    real_lines = [ln.strip() for ln in rc.read_text(encoding="utf-8").splitlines()]
    assert real_lines.count(line) == 1


def test_ensure_line_tolerates_non_utf8_rc(tmp_path) -> None:
    rc = tmp_path / ".zshrc"
    rc.write_bytes(b"export NAME=caf\xe9\n")  # latin-1 byte, invalid UTF-8
    line = completion.completion_snippet("zsh")
    assert completion.ensure_line(rc, line) is True  # does not raise UnicodeDecodeError
    assert line in rc.read_text(encoding="utf-8", errors="surrogateescape")


def test_ensure_line_creates_missing_parents(tmp_path) -> None:
    target = tmp_path / ".config" / "fish" / "completions" / "spacelabel.fish"
    line = completion.completion_snippet("fish")
    assert completion.ensure_line(target, line) is True
    assert target.exists()
    assert line in target.read_text(encoding="utf-8")


# ---- dynamic completers ----------------------------------------------------


def _spaces() -> list[Space]:
    return [
        Space(uuid=U1, display_uuid=DISP_A, is_current=True),
        Space(uuid=U2, display_uuid=DISP_B),
    ]


def test_complete_space_target_offers_current_and_live(ctx, monkeypatch) -> None:
    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", lambda **_: _spaces())
    out = completion.complete_space_target(ctx, None, "")  # type: ignore[arg-type]
    assert out == ["current", U1, U2]


def test_complete_space_target_prefix_filter_case_insensitive(ctx, monkeypatch) -> None:
    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", lambda **_: _spaces())
    # Lowercase prefix still matches the canonical uppercase UUID.
    assert completion.complete_space_target(ctx, None, "6622ac") == [U1]  # type: ignore[arg-type]
    assert completion.complete_space_target(ctx, None, "cur") == ["current"]  # type: ignore[arg-type]


def test_complete_space_target_swallows_read_failure(ctx, monkeypatch) -> None:
    import spacelabel.platform.cgs as cgs_mod

    def boom(**_):
        raise cgs_mod.CGSUnavailableError("no window server")

    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", boom)
    monkeypatch.setattr("spacelabel.platform.spaces_plist.read_spaces", lambda: [])
    # current is always offered; the live read failure degrades to no UUIDs.
    assert completion.complete_space_target(ctx, None, "") == ["current"]  # type: ignore[arg-type]


def test_complete_space_target_swallows_homeless_plist_fallback(ctx, monkeypatch) -> None:
    import spacelabel.platform.cgs as cgs_mod

    def cgs_boom(**_):
        raise cgs_mod.CGSUnavailableError("no window server")

    def home_boom():
        raise RuntimeError("could not determine home directory")

    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", cgs_boom)
    # The plist fallback resolves Path.home() internally; a home-less context must
    # still degrade to just 'current', never raise into the shell.
    monkeypatch.setattr("spacelabel.platform.spaces_plist.read_spaces", home_boom)
    assert completion.complete_space_target(ctx, None, "") == ["current"]  # type: ignore[arg-type]


def test_complete_label_clear_includes_stored_labeled(ctx, monkeypatch) -> None:
    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", lambda **_: _spaces())
    labels = {
        U1: Label(text="Email"),
        U3: Label(text="Offline space"),  # labeled but not live
        U2: Label(text="", notes=[Note(text="todo")]),  # notes-only -> not a label
    }
    monkeypatch.setattr(store, "load_labels", lambda _paths: labels)
    out = completion.complete_label_clear_target(ctx, None, "")  # type: ignore[arg-type]
    assert "current" in out and U1 in out and U2 in out  # live
    assert U3 in out  # stored-but-offline labeled
    assert out.count(U1) == 1  # de-duped (live + stored)


def test_complete_note_target_includes_note_bearing(ctx, monkeypatch) -> None:
    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", lambda **_: _spaces())
    labels = {
        U3: Label(text="", notes=[Note(text="buy milk")]),  # notes-only, offline
        U1: Label(text="Email"),  # labeled, no notes
    }
    monkeypatch.setattr(store, "load_labels", lambda _paths: labels)
    out = completion.complete_note_target(ctx, None, "")  # type: ignore[arg-type]
    assert U3 in out  # note-bearing offline space is completable
    assert U1 in out  # still live


def test_complete_display_target(ctx, monkeypatch) -> None:
    topo = [Display(uuid=DISP_A, cg_display_id=1), Display(uuid=DISP_B, cg_display_id=2)]
    monkeypatch.setattr("spacelabel.platform.displays.discover_topology", lambda: topo)
    monkeypatch.setattr(store, "load_display_labels", lambda _paths: {})
    out = completion.complete_display_target(ctx, None, "")  # type: ignore[arg-type]
    assert out == ["current", DISP_A, DISP_B]


def test_complete_config_key_from_schema(ctx) -> None:
    out = completion.complete_config_key(ctx, None, "menubar.")  # type: ignore[arg-type]
    assert out  # menubar.* keys exist in the schema
    assert all(key.startswith("menubar.") for key in out)


# ---- `completion install` command ------------------------------------------


def test_completion_install_dry_run_prints_snippet() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["completion", "install", "--shell", "zsh", "--dry-run"])
    assert r.exit_code == 0
    # Snippet on stdout (the data channel); guidance on stderr.
    assert r.stdout.strip() == 'eval "$(_SPACELABEL_COMPLETE=zsh_source spacelabel)"'


def test_completion_install_auto_detects_shell(monkeypatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/zsh")
    runner = CliRunner()
    r = runner.invoke(cli, ["completion", "install", "--dry-run"])
    assert r.exit_code == 0
    assert "_SPACELABEL_COMPLETE=zsh_source spacelabel" in r.stdout


def test_completion_install_unknown_shell_is_error(monkeypatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/tcsh")
    runner = CliRunner()
    r = runner.invoke(cli, ["completion", "install", "--dry-run"])
    assert r.exit_code != 0  # ClickException -> exit 1


def test_completion_install_homeless_is_clean_error(monkeypatch) -> None:
    from pathlib import Path

    def home_boom():
        raise RuntimeError("could not determine home directory")

    monkeypatch.setattr(Path, "home", home_boom)
    runner = CliRunner()
    r = runner.invoke(cli, ["completion", "install", "--shell", "zsh"])
    assert r.exit_code != 0  # clean ClickException, not a raw traceback
    assert "home directory" in r.stderr


def test_completion_install_writes_rc_idempotently(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))  # Path.home() honors $HOME on POSIX
    runner = CliRunner()
    r = runner.invoke(cli, ["completion", "install", "--shell", "zsh"])
    assert r.exit_code == 0
    rc = tmp_path / ".zshrc"
    assert rc.exists()
    assert "_SPACELABEL_COMPLETE=zsh_source spacelabel" in rc.read_text(encoding="utf-8")

    # Re-running is a no-op (already enabled).
    r2 = runner.invoke(cli, ["completion", "install", "--shell", "zsh"])
    assert r2.exit_code == 0
    assert "already enabled" in r2.stderr
    assert rc.read_text(encoding="utf-8").count("_SPACELABEL_COMPLETE=zsh_source") == 1
