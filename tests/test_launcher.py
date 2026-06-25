"""The py2app bundle entry point (packaging/py2app/launcher.py).

launcher.py is not on the package import path (py2app makes it the bundle's
``__main__``), so load it by file path. The load-bearing property: a Finder/``open``
launch starts the menu-bar agent, while every CLI path (the PATH shim, the launchd
LaunchAgent, a bare ``spacelabel`` typed in a shell) is left untouched -- so the agent
run loop can never hijack a no-arg CLI invocation.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from spacelabel import BUNDLE_ID

_LAUNCHER = Path(__file__).resolve().parent.parent / "packaging" / "py2app" / "launcher.py"


def _load_launcher() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_spacelabel_launcher", _LAUNCHER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("argv", "xpc", "expected"),
    [
        (["exe"], f"application.{BUNDLE_ID}.531.999", True),  # Finder "Open" of OUR bundle
        (["exe", "-psn_0_12345"], "", True),  # legacy LaunchServices PSN token
        (["exe"], "0", False),  # bare `spacelabel` in a plain shell -> --help
        (["exe"], "", False),  # XPC_SERVICE_NAME unset -> --help
        # Inheritance guard: a shell spawned by ANOTHER GUI app carries that app's id, so a
        # bare `spacelabel` there must still print --help (the regression codex flagged).
        (["exe"], "application.com.apple.Terminal.99.1", False),
        (["exe"], f"application.{BUNDLE_ID}2.1.2", False),  # prefix collision must not match
        (["exe", "agent"], f"application.{BUNDLE_ID}.1.2", False),  # launchd LaunchAgent
        (["exe", "spaces", "--json"], f"application.{BUNDLE_ID}.1.2", False),  # PATH CLI shim
        (["exe", "--version"], "0", False),  # the build self-test invocation
    ],
)
def test_opened_from_finder(argv: list[str], xpc: str, expected: bool, monkeypatch) -> None:
    monkeypatch.setenv("XPC_SERVICE_NAME", xpc)
    launcher = _load_launcher()
    assert launcher._opened_from_finder(argv) is expected


def test_launcher_main_passes_prog_name_to_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """launcher.py calls main(prog_name='spacelabel') so usage shows 'spacelabel' (item K)."""
    import spacelabel.cli as cli_mod

    received: list[str | None] = []

    def _capture(*_args: object, **kwargs: object) -> None:
        received.append(kwargs.get("prog_name"))

    monkeypatch.setattr(cli_mod, "cli", _capture)
    cli_mod.main(prog_name="spacelabel")

    assert received == ["spacelabel"]


def test_main_no_prog_name_forwards_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() without prog_name passes None so click derives the name from sys.argv[0]."""
    import spacelabel.cli as cli_mod

    received: list[str | None] = []

    def _capture(*_args: object, **kwargs: object) -> None:
        received.append(kwargs.get("prog_name"))

    monkeypatch.setattr(cli_mod, "cli", _capture)
    cli_mod.main()

    assert received == [None]
