"""Pure switch-mechanics helpers (DECISIONS.md 9.5, docs/UI.md §2.4).

These cover the WindowServer-free core of :mod:`spacelabel.platform.switching`: the
``com.apple.symbolichotkeys`` parsing that decides whether a "Switch to Desktop N"
chord is usable. The CGEventPost / Accessibility paths are GUI/permission-gated and
exercised live (Phase 6), not here. Fixtures mirror the real reference-machine layout
(macOS 26.5.1): id 118 == Desktop 1, params ``[asciiChar, keyCode, modifierFlags]``.
"""

from __future__ import annotations

from spacelabel.platform.switching import (
    KeyBinding,
    parse_desktop_binding,
    symbolic_hotkey_id,
)

#: The default-but-disabled Ctrl+1 binding macOS ships for "Switch to Desktop 1".
_CTRL_1_PARAMS = [65535, 18, 262144]  # asciiChar(none), kVK_ANSI_1, Control


def _entry(params, *, enabled):
    return {"enabled": enabled, "value": {"type": "standard", "parameters": params}}


def test_symbolic_hotkey_id_is_contiguous_from_118():
    # Verified live: ids 118/119/120 == Switch to Desktop 1/2/3.
    assert symbolic_hotkey_id(1) == 118
    assert symbolic_hotkey_id(2) == 119
    assert symbolic_hotkey_id(13) == 130


def test_parse_enabled_binding_reads_keycode_and_modifiers():
    hotkeys = {"118": _entry(_CTRL_1_PARAMS, enabled=True)}
    assert parse_desktop_binding(hotkeys, 1) == KeyBinding(key_code=18, modifier_flags=262144)


def test_parse_disabled_binding_is_none():
    # The reference-machine default: the slot exists but is disabled -> not usable,
    # so the caller disables the action with a visible reason (no silent no-op).
    hotkeys = {"118": _entry(_CTRL_1_PARAMS, enabled=False)}
    assert parse_desktop_binding(hotkeys, 1) is None


def test_parse_missing_entry_is_none():
    # Desktops past the configured set have no entry at all (ids 121+ on the ref box).
    assert parse_desktop_binding({}, 4) is None
    assert parse_desktop_binding({"118": _entry(_CTRL_1_PARAMS, enabled=True)}, 9) is None


def test_parse_resolves_the_right_ordinal():
    hotkeys = {
        "118": _entry([65535, 18, 262144], enabled=True),  # Ctrl+1 -> Desktop 1
        "120": _entry([65535, 20, 262144], enabled=True),  # Ctrl+3 -> Desktop 3
    }
    assert parse_desktop_binding(hotkeys, 1) == KeyBinding(18, 262144)
    assert parse_desktop_binding(hotkeys, 3) == KeyBinding(20, 262144)
    assert parse_desktop_binding(hotkeys, 2) is None  # id 119 absent


def test_parse_preserves_all_modifier_bits():
    # All modifier bits are passed verbatim to CGEventSetFlags so non-standard
    # chords (e.g. Fn+Ctrl) post the exact flags the user configured.
    hotkeys = {"118": _entry([65535, 18, 0x40000 | 0x80000 | 0x800000], enabled=True)}
    binding = parse_desktop_binding(hotkeys, 1)
    assert binding == KeyBinding(key_code=18, modifier_flags=0x40000 | 0x80000 | 0x800000)


def test_parse_invalid_ordinal_is_none():
    hotkeys = {"118": _entry(_CTRL_1_PARAMS, enabled=True)}
    assert parse_desktop_binding(hotkeys, 0) is None
    assert parse_desktop_binding(hotkeys, -1) is None


def test_parse_malformed_entries_are_none_not_crash():
    # Each malformed shape is tolerated (log + None), never an exception.
    assert parse_desktop_binding({"118": "not-a-dict"}, 1) is None
    assert parse_desktop_binding({"118": _entry(None, enabled=True)}, 1) is None
    assert parse_desktop_binding({"118": _entry([65535], enabled=True)}, 1) is None
    assert parse_desktop_binding({"118": {"enabled": True}}, 1) is None  # no value
    assert parse_desktop_binding({"118": _entry([65535, "x", "y"], enabled=True)}, 1) is None
