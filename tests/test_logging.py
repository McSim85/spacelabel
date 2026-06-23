"""Colorized CLI log formatting (tty + NO_COLOR aware)."""

from __future__ import annotations

import io
import logging
import sys

from spacelabel.logging_setup import (
    _BOOT_LOG_MAX_BYTES,
    _ColorFormatter,
    _use_color,
    install_logging_excepthook,
    truncate_boot_log,
)


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


def test_setup_logging_agent_file_handler_is_utf8(tmp_path):
    # Under the LaunchAgent there is no locale, so the agent.log handler must force UTF-8;
    # otherwise the agent's non-ASCII log lines (curly quotes / “→”) raise on write and
    # every record is lost to agent.boot.log (caught live on the cask install, 2026-06-22).
    import logging.handlers

    from spacelabel.logging_setup import LogMode, setup_logging

    root = logging.getLogger("spacelabel")
    saved_level, saved_handlers = root.level, root.handlers[:]
    try:
        setup_logging(LogMode.AGENT, agent_level=logging.WARNING, log_dir=tmp_path)
        file_handlers = [
            h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert file_handlers, "agent mode must attach a rotating file handler"
        assert file_handlers[0].encoding == "utf-8"
        # Behavioral: a non-ASCII record round-trips through agent.log without raising.
        logging.getLogger("spacelabel.test").warning("Switch to Desktop “1” → ok")
        file_handlers[0].flush()
        assert "→ ok" in (tmp_path / "agent.log").read_text(encoding="utf-8")
    finally:
        for handler in root.handlers:
            if isinstance(handler, logging.handlers.RotatingFileHandler):
                handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_use_color_requires_tty_and_no_no_color(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert _use_color(_Tty()) is True
    assert _use_color(io.StringIO()) is False  # not a tty
    monkeypatch.setenv("NO_COLOR", "1")
    assert _use_color(_Tty()) is False  # honors NO_COLOR


# ---- agent.boot.log truncation (launchd boot-catch file) -------------------


def test_truncate_boot_log_noop_when_small(tmp_path):
    boot = tmp_path / "agent.boot.log"
    boot.write_text("ok", encoding="utf-8")
    truncate_boot_log(tmp_path)
    assert boot.read_text(encoding="utf-8") == "ok"  # under cap -> untouched


def test_truncate_boot_log_noop_when_absent(tmp_path):
    truncate_boot_log(tmp_path)  # no file present -> no error, nothing created
    assert not (tmp_path / "agent.boot.log").exists()


def test_truncate_boot_log_truncates_when_large(tmp_path):
    boot = tmp_path / "agent.boot.log"
    boot.write_bytes(b"x" * (_BOOT_LOG_MAX_BYTES + 1))
    truncate_boot_log(tmp_path)
    assert boot.exists() and boot.stat().st_size == 0  # truncated in place


def test_truncate_boot_log_inplace_keeps_open_fd(tmp_path):
    # launchd holds the boot file open as fd 1/2 (O_APPEND); an in-place truncate
    # means that fd keeps writing to the SAME inode, now reset.
    boot = tmp_path / "agent.boot.log"
    boot.write_bytes(b"x" * (_BOOT_LOG_MAX_BYTES + 1))
    with boot.open("a", encoding="utf-8") as held:
        truncate_boot_log(tmp_path)
        held.write("after\n")
        held.flush()
    assert boot.read_text(encoding="utf-8") == "after\n"


def test_truncate_boot_log_also_caps_legacy_err_log(tmp_path):
    # Upgrade path: an old plist still feeds agent.err.log until it is refreshed, so
    # truncate_boot_log must cap it too (keeps the original unbounded bug fixed).
    boot = tmp_path / "agent.boot.log"
    boot.write_bytes(b"x" * (_BOOT_LOG_MAX_BYTES + 1))
    legacy = tmp_path / "agent.err.log"
    legacy.write_bytes(b"y" * (_BOOT_LOG_MAX_BYTES + 1))
    truncate_boot_log(tmp_path)
    assert boot.stat().st_size == 0
    assert legacy.stat().st_size == 0


def test_truncate_boot_log_swallows_errors(tmp_path):
    # A non-existent log dir must not raise (best-effort housekeeping).
    truncate_boot_log(tmp_path / "does-not-exist")


# ---- uncaught-exception routing into the logger ----------------------------


def test_install_excepthook_routes_uncaught_to_logger():
    saved = sys.excepthook
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logger = logging.getLogger("spacelabel")
    logger.addHandler(handler)
    try:
        install_logging_excepthook()
        try:
            raise ValueError("boom")
        except ValueError:
            sys.excepthook(*sys.exc_info())  # type: ignore[misc]
        assert any(r.levelno == logging.CRITICAL and r.exc_info for r in records)
    finally:
        logger.removeHandler(handler)
        sys.excepthook = saved


def test_install_excepthook_passes_through_keyboardinterrupt(monkeypatch):
    saved = sys.excepthook
    seen: dict[str, bool] = {}
    monkeypatch.setattr(sys, "__excepthook__", lambda *a: seen.setdefault("default", True))
    try:
        install_logging_excepthook()
        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
        assert seen.get("default")  # delegated to the default hook, not logged
    finally:
        sys.excepthook = saved
