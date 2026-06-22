"""Unit tests for shell completion: dynamic completers + the script installer.

The live readers (CGS, display topology, the JSON store) and the zsh ``$fpath``
probe are monkeypatched, so these run on a hosted CI runner with no WindowServer
and without spawning a real shell. Completers must degrade to ``[]`` on a read
failure rather than raise (a traceback would break the user's tab key).
"""

from __future__ import annotations

import logging
import shlex
from pathlib import Path

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


def _run(completer, ctx, incomplete=""):
    """Call a completer with a None param (tests don't need a real Parameter)."""
    return completer(ctx, None, incomplete)  # type: ignore[arg-type]


def _spaces() -> list[Space]:
    return [
        Space(uuid=U1, display_uuid=DISP_A, is_current=True),
        Space(uuid=U2, display_uuid=DISP_B),
    ]


# ---- dynamic completers ----------------------------------------------------


def test_complete_space_target_offers_current_and_live(ctx, monkeypatch) -> None:
    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", lambda **_: _spaces())
    assert _run(completion.complete_space_target, ctx) == ["current", U1, U2]


def test_complete_space_target_prefix_filter_case_insensitive(ctx, monkeypatch) -> None:
    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", lambda **_: _spaces())
    assert _run(completion.complete_space_target, ctx, "6622ac") == [U1]
    assert _run(completion.complete_space_target, ctx, "cur") == ["current"]


def test_complete_space_target_swallows_read_failure(ctx, monkeypatch) -> None:
    import spacelabel.platform.cgs as cgs_mod

    def boom(**_):
        raise cgs_mod.CGSUnavailableError("no window server")

    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", boom)
    monkeypatch.setattr("spacelabel.platform.spaces_plist.read_spaces", lambda: [])
    assert _run(completion.complete_space_target, ctx) == ["current"]


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
    assert _run(completion.complete_space_target, ctx) == ["current"]


def test_complete_label_clear_includes_stored_labeled(ctx, monkeypatch) -> None:
    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", lambda **_: _spaces())
    labels = {
        U1: Label(text="Email"),
        U3: Label(text="Offline space"),  # labeled but not live
        U2: Label(text="", notes=[Note(text="todo")]),  # notes-only -> not a label
    }
    monkeypatch.setattr(store, "load_labels", lambda _paths: labels)
    out = _run(completion.complete_label_clear_target, ctx)
    assert "current" in out and U1 in out and U2 in out  # live
    assert U3 in out  # stored-but-offline labeled
    assert out.count(U1) == 1  # de-duped (live + stored)


def test_complete_note_target_includes_labeled_and_noted(ctx, monkeypatch) -> None:
    monkeypatch.setattr("spacelabel.platform.cgs.enumerate_spaces", lambda **_: _spaces())
    labels = {
        U3: Label(text="", notes=[Note(text="buy milk")]),  # notes-only, offline
        # labeled-but-offline (no notes yet): `note add` must still complete it.
        "OFFLINE-LABELED-0000-0000-000000000000": Label(text="Reading"),
    }
    monkeypatch.setattr(store, "load_labels", lambda _paths: labels)
    out = _run(completion.complete_note_target, ctx)
    assert U3 in out  # note-bearing offline space
    assert "OFFLINE-LABELED-0000-0000-000000000000" in out  # labeled offline space
    assert U1 in out  # live


def test_complete_display_target(ctx, monkeypatch) -> None:
    topo = [Display(uuid=DISP_A, cg_display_id=1), Display(uuid=DISP_B, cg_display_id=2)]
    monkeypatch.setattr("spacelabel.platform.displays.discover_topology", lambda: topo)
    monkeypatch.setattr(store, "load_display_labels", lambda _paths: {})
    assert _run(completion.complete_display_target, ctx) == ["current", DISP_A, DISP_B]


def test_complete_config_key_from_schema(ctx) -> None:
    out = _run(completion.complete_config_key, ctx, "menubar.")
    assert out
    assert all(key.startswith("menubar.") for key in out)


def test_completion_logging_is_silenced_by_package_nullhandler() -> None:
    # The package attaches a NullHandler so a malformed store logged inside a
    # completer never reaches the last-resort handler (no stderr noise on TAB).
    handlers = logging.getLogger("spacelabel").handlers
    assert any(isinstance(handler, logging.NullHandler) for handler in handlers)


# ---- detect_shell ----------------------------------------------------------


@pytest.mark.parametrize("shell_env", ["/bin/zsh", "/usr/local/bin/bash", "/opt/homebrew/bin/fish"])
def test_detect_shell_supported(shell_env) -> None:
    assert completion.detect_shell(shell_env) in completion.SHELLS


@pytest.mark.parametrize("shell_env", ["", "/bin/tcsh", "/bin/sh"])
def test_detect_shell_unsupported_raises(shell_env) -> None:
    with pytest.raises(completion.UnknownShellError):
        completion.detect_shell(shell_env)


# ---- generated script ------------------------------------------------------


def test_generate_script_zsh_is_compdef() -> None:
    src = completion.generate_script("zsh")
    assert src.startswith("#compdef spacelabel")
    assert "_spacelabel_completion" in src


def test_generate_script_fish_is_valid_fish_not_posix_bootstrap() -> None:
    src = completion.generate_script("fish")
    assert "complete --no-files --command spacelabel" in src
    assert "function _spacelabel_completion" in src
    # The broken POSIX bootstrap must NOT be what we write for fish.
    assert "fish_source spacelabel | source" not in src


def test_generate_script_bash_registers_complete() -> None:
    src = completion.generate_script("bash")
    assert "_spacelabel_completion" in src
    assert "complete -o nosort -F _spacelabel_completion spacelabel" in src


# ---- target path resolution ------------------------------------------------


def test_fish_path_honors_xdg_then_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    expected = tmp_path / "cfg" / "fish" / "completions" / "spacelabel.fish"
    assert completion._fish_completion_path() == expected
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    expected = tmp_path / ".config" / "fish" / "completions" / "spacelabel.fish"
    assert completion._fish_completion_path() == expected


def test_bash_path_honors_env_precedence(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BASH_COMPLETION_USER_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert completion._bash_completion_path() == (
        tmp_path / ".local" / "share" / "bash-completion" / "completions" / "spacelabel"
    )
    monkeypatch.setenv("BASH_COMPLETION_USER_DIR", str(tmp_path / "bc"))
    assert completion._bash_completion_path() == tmp_path / "bc" / "completions" / "spacelabel"


def test_zsh_path_prefers_zfunc_on_fpath_even_if_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    zfunc = tmp_path / ".zfunc"  # on fpath but does NOT exist on disk yet
    plugin = tmp_path / ".oh-my-zsh" / "plugins" / "git"
    plugin.mkdir(parents=True)
    monkeypatch.setattr(completion, "_live_zsh_fpath", lambda: [plugin, zfunc])
    target, needs_rc = completion._zsh_completion_path()
    assert target == zfunc / "_spacelabel"  # ~/.zfunc wins over a plugin dir
    assert needs_rc is False


def test_zsh_path_skips_plugin_dirs(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    plugin = tmp_path / ".oh-my-zsh" / "plugins" / "vscode"
    plugin.mkdir(parents=True)
    monkeypatch.setattr(completion, "_live_zsh_fpath", lambda: [plugin])
    target, needs_rc = completion._zsh_completion_path()
    # A framework plugin dir is never a drop target -> fall back to ~/.zfunc + rc.
    assert target == tmp_path / ".zfunc" / "_spacelabel"
    assert needs_rc is True


def test_zsh_path_uses_dedicated_completions_dir_not_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    comp_dir = tmp_path / ".oh-my-zsh" / "completions"
    comp_dir.mkdir(parents=True)
    cache_dir = tmp_path / ".oh-my-zsh" / "cache" / "completions"
    cache_dir.mkdir(parents=True)
    # The cache/completions dir appears first but must be skipped.
    monkeypatch.setattr(completion, "_live_zsh_fpath", lambda: [cache_dir, comp_dir])
    target, needs_rc = completion._zsh_completion_path()
    assert target == comp_dir / "_spacelabel"
    assert needs_rc is False


def test_zsh_path_uses_site_functions(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    site = tmp_path / "brew" / "share" / "zsh" / "site-functions"
    site.mkdir(parents=True)
    monkeypatch.setattr(completion, "_live_zsh_fpath", lambda: [site])
    target, needs_rc = completion._zsh_completion_path()
    assert target == site / "_spacelabel"
    assert needs_rc is False


def test_zsh_path_skips_zfunc_if_not_a_writable_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    zfunc = tmp_path / ".zfunc"
    zfunc.write_text("not a dir", encoding="utf-8")  # on fpath but not a writable dir
    site = tmp_path / "brew" / "site-functions"
    site.mkdir(parents=True)
    monkeypatch.setattr(completion, "_live_zsh_fpath", lambda: [zfunc, site])
    target, needs_rc = completion._zsh_completion_path()
    assert target == site / "_spacelabel"  # falls through to a writable site-functions
    assert needs_rc is False


def test_zsh_path_fallback_when_nothing_suitable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(completion, "_live_zsh_fpath", lambda: [])
    target, needs_rc = completion._zsh_completion_path()
    assert target == tmp_path / ".zfunc" / "_spacelabel"
    assert needs_rc is True


# ---- atomic write + rc edit ------------------------------------------------


def test_write_if_changed_idempotent(tmp_path) -> None:
    target = tmp_path / "sub" / "_spacelabel"
    assert completion._write_if_changed(target, "abc") is True
    assert target.read_text(encoding="utf-8") == "abc\n"
    assert completion._write_if_changed(target, "abc") is False  # identical -> no write
    assert completion._write_if_changed(target, "different") is True
    assert not (tmp_path / "sub" / "._spacelabel.tmp").exists()  # no temp left behind


def test_write_if_changed_overwrites_non_utf8(tmp_path) -> None:
    target = tmp_path / "_spacelabel"
    target.write_bytes(b"\xff\xfe not utf8")  # existing file is not our UTF-8 script
    assert completion._write_if_changed(target, "new content") is True
    assert target.read_text(encoding="utf-8") == "new content\n"


def test_write_if_changed_bad_parent_is_completion_error(tmp_path) -> None:
    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    target = blocker / "sub" / "_spacelabel"  # a path component is a regular file
    with pytest.raises(completion.CompletionError):
        completion._write_if_changed(target, "data")


def test_ensure_zsh_fpath_idempotent(tmp_path) -> None:
    rc = tmp_path / ".zshrc"
    rc.write_text("export FOO=1", encoding="utf-8")  # no trailing newline
    directory = tmp_path / ".zfunc"
    assert completion.ensure_zsh_fpath(rc, directory) is True
    body = rc.read_text(encoding="utf-8")
    assert "export FOO=1\n" in body
    assert f"fpath=({directory} $fpath)" in body
    assert "autoload -Uz compinit && compinit" in body
    assert completion.ensure_zsh_fpath(rc, directory) is False  # marker present -> no-op
    assert body.count(completion._HEADER) == 1


def test_ensure_zsh_fpath_quotes_paths_with_spaces(tmp_path) -> None:
    rc = tmp_path / ".zshrc"
    directory = tmp_path / "Home Dir" / ".zfunc"  # space in the path
    assert completion.ensure_zsh_fpath(rc, directory) is True
    body = rc.read_text(encoding="utf-8")
    assert f"fpath=({shlex.quote(str(directory))} $fpath)" in body
    assert "Home Dir" in body and "fpath=(/" not in body  # the raw unquoted form is absent


# ---- install_completion (library) ------------------------------------------


def test_install_completion_fish_writes_generated_script(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    result = completion.install_completion("fish")
    assert result.changed is True
    assert result.path == tmp_path / ".config" / "fish" / "completions" / "spacelabel.fish"
    assert "complete --no-files --command spacelabel" in result.path.read_text(encoding="utf-8")
    # Second call is idempotent (script unchanged).
    assert completion.install_completion("fish").changed is False


def test_install_completion_zsh_into_fpath_dir_no_rc_edit(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    zfunc = tmp_path / ".zfunc"
    zfunc.mkdir()
    monkeypatch.setattr(completion, "_live_zsh_fpath", lambda: [zfunc])
    result = completion.install_completion("zsh")
    assert result.path == zfunc / "_spacelabel"
    assert (zfunc / "_spacelabel").read_text(encoding="utf-8").startswith("#compdef spacelabel")
    assert not (tmp_path / ".zshrc").exists()  # pure file-drop, no rc edit


def test_install_completion_zsh_fallback_wires_fpath_in_rc(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(completion, "_live_zsh_fpath", lambda: [])
    result = completion.install_completion("zsh")
    assert result.path == tmp_path / ".zfunc" / "_spacelabel"
    assert (tmp_path / ".zfunc" / "_spacelabel").exists()
    rc = (tmp_path / ".zshrc").read_text(encoding="utf-8")
    assert f"fpath=({tmp_path / '.zfunc'} $fpath)" in rc


def test_install_zsh_fallback_leaves_rc_untouched_on_write_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".zfunc").write_text("not a dir", encoding="utf-8")  # write target unusable
    monkeypatch.setattr(completion, "_live_zsh_fpath", lambda: [])
    with pytest.raises(completion.CompletionError):
        completion.install_completion("zsh")
    assert not (tmp_path / ".zshrc").exists()  # .zshrc not modified when the write fails


def test_install_completion_homeless_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    def home_boom():
        raise RuntimeError("no home")

    monkeypatch.setattr(Path, "home", home_boom)
    with pytest.raises(completion.CompletionError):
        completion.install_completion("fish")


# ---- `completion install` command ------------------------------------------


def test_completion_install_dry_run_prints_script(monkeypatch) -> None:
    monkeypatch.setattr(completion, "_live_zsh_fpath", lambda: [])  # avoid spawning zsh
    runner = CliRunner()
    r = runner.invoke(cli, ["completion", "install", "--shell", "zsh", "--dry-run"])
    assert r.exit_code == 0
    assert r.stdout.startswith("#compdef spacelabel")  # generated script on stdout


def test_completion_install_auto_detects_shell(monkeypatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(completion, "_live_zsh_fpath", lambda: [])
    runner = CliRunner()
    r = runner.invoke(cli, ["completion", "install", "--dry-run"])
    assert r.exit_code == 0
    assert "#compdef spacelabel" in r.stdout


def test_completion_install_unknown_shell_is_error(monkeypatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/tcsh")
    runner = CliRunner()
    r = runner.invoke(cli, ["completion", "install", "--dry-run"])
    assert r.exit_code != 0  # ClickException -> exit 1


def test_completion_install_dry_run_works_without_home(monkeypatch) -> None:
    # --dry-run prints the script even when $HOME can't be resolved (P3).
    def home_boom():
        raise RuntimeError("no home")

    monkeypatch.setattr(Path, "home", home_boom)
    runner = CliRunner()
    r = runner.invoke(cli, ["completion", "install", "--shell", "fish", "--dry-run"])
    assert r.exit_code == 0
    assert "complete --no-files --command spacelabel" in r.stdout


def test_completion_install_writes_file_end_to_end(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()
    r = runner.invoke(cli, ["completion", "install", "--shell", "fish"])
    assert r.exit_code == 0
    target = tmp_path / ".config" / "fish" / "completions" / "spacelabel.fish"
    assert target.exists()
    assert "Wrote" in r.stderr
    # Re-running reports up-to-date.
    r2 = runner.invoke(cli, ["completion", "install", "--shell", "fish"])
    assert r2.exit_code == 0
    assert "up to date" in r2.stderr


def test_completion_install_homeless_install_is_clean_error(monkeypatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    def home_boom():
        raise RuntimeError("no home")

    monkeypatch.setattr(Path, "home", home_boom)
    runner = CliRunner()
    r = runner.invoke(cli, ["completion", "install", "--shell", "fish"])
    assert r.exit_code != 0  # clean ClickException, not a raw traceback
    assert "home directory" in r.stderr
