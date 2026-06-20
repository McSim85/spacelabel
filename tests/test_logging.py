"""Colorized CLI log formatting (tty + NO_COLOR aware)."""

from __future__ import annotations

import io
import logging

from spacelabel.logging_setup import _ColorFormatter, _use_color


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _record(level: int) -> logging.LogRecord:
    return logging.LogRecord("spacelabel", level, "f.py", 1, "hello", None, None)


def test_color_formatter_wraps_in_ansi_by_level():
    out = _ColorFormatter("%(levelname)s: %(message)s").format(_record(logging.WARNING))
    assert out.startswith("\033[")  # opened with an SGR color
    assert out.endswith("\033[0m")  # and reset
    assert "WARNING: hello" in out


def test_plain_formatter_has_no_ansi():
    out = logging.Formatter("%(levelname)s: %(message)s").format(_record(logging.ERROR))
    assert "\033[" not in out


def test_setup_logging_agent_honors_config_level(tmp_path):
    # config.log_level must actually set the agent's level (was inert before).
    from spacelabel.logging_setup import LogMode, setup_logging

    root = logging.getLogger("spacelabel")
    saved_level, saved_handlers = root.level, root.handlers[:]
    try:
        setup_logging(LogMode.AGENT, agent_level=logging.DEBUG, log_dir=tmp_path)
        assert root.level == logging.DEBUG
        setup_logging(LogMode.AGENT, agent_level=logging.ERROR, log_dir=tmp_path)
        assert root.level == logging.ERROR
        # Default (no agent_level) stays WARNING.
        setup_logging(LogMode.AGENT, log_dir=tmp_path)
        assert root.level == logging.WARNING
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_use_color_requires_tty_and_no_no_color(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert _use_color(_Tty()) is True
    assert _use_color(io.StringIO()) is False  # not a tty
    monkeypatch.setenv("NO_COLOR", "1")
    assert _use_color(_Tty()) is False  # honors NO_COLOR
