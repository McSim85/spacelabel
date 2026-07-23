"""Pure switch-mechanics helpers.

These cover the WindowServer-free core of :mod:`spacelabel.platform.switching`: the
``com.apple.symbolichotkeys`` parsing that decides whether a "Switch to Desktop N"
chord is usable. The CGEventPost / Accessibility paths are GUI/permission-gated and
exercised live (Phase 6), not here. Fixtures mirror the real reference-machine layout
(macOS 26.5.1): id 118 == Desktop 1, params ``[asciiChar, keyCode, modifierFlags]``.
"""

from __future__ import annotations

from spacelabel.platform.switching import (
    KeyBinding,
    SwitchOutcome,
    classify_switch,
    is_grant_stale,
    is_switchable_target,
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


def test_is_switchable_target_only_on_active_display():
    # macOS reliably switches only the focused display's Space; a chord for a Space on
    # another display is a near-silent no-op (item O, verified dual-display 2026-06-24).
    active = "899EDEF9-1840-4DE5-A049-D7FFA8ECEB7A"
    other = "874A623F-F8F5-43C1-B11C-4AAC3E383C0F"
    assert is_switchable_target(active, active) is True
    assert is_switchable_target(other, active) is False


def test_is_switchable_target_refuses_when_active_unknown():
    # An unresolvable active display can't confirm the target is focused, so refuse --
    # never silently post a possibly cross-display chord (item O / DECISIONS 9.5). In
    # practice the active display resolves (CGS + NSScreen fallback), so single-display
    # setups are unaffected; refusing here is the conservative, per-click, visible choice.
    some = "899EDEF9-1840-4DE5-A049-D7FFA8ECEB7A"
    assert is_switchable_target(some, None) is False
    assert is_switchable_target(some, "") is False


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


# ---- stale-vs-missing Accessibility grant classification (item L) ----------
#
# is_grant_stale is the PURE decision behind the agent's branched guidance: a False
# AXIsProcessTrusted is a STALE grant (guide REMOVE-and-re-add) when the ad-hoc cdhash
# rotated since we were last trusted (an app update) OR Accessibility was ever granted;
# otherwise it was never granted (guide plain "enable").


def test_grant_never_granted_is_not_stale():
    # First-ever run: cdhash readable, no recorded "trusted", nothing to compare -> NOT
    # stale (show the plain "enable Accessibility" guidance, not remove-and-re-add).
    assert is_grant_stale(current_cdhash="abc", last_cdhash=None, ax_was_trusted=False) is False


def test_grant_stale_when_cdhash_changed_since_trusted():
    # App updated: the ad-hoc cdhash rotated since the checkpoint -> stale, even before
    # the ax_was_trusted flag is consulted (the headline upgrade scenario).
    assert is_grant_stale(current_cdhash="new", last_cdhash="old", ax_was_trusted=False) is True


def test_grant_stale_when_was_trusted_even_if_cdhash_unknown():
    # Signature unreadable (Security bind failed) but we were trusted before -> fall back
    # to the ax_was_trusted signal alone -> stale.
    assert is_grant_stale(current_cdhash=None, last_cdhash=None, ax_was_trusted=True) is True
    assert is_grant_stale(current_cdhash=None, last_cdhash="old", ax_was_trusted=True) is True


def test_grant_not_stale_when_cdhash_unchanged_and_never_trusted():
    # Same cdhash, never confirmed trusted -> the entry was never granted -> not stale.
    assert is_grant_stale(current_cdhash="same", last_cdhash="same", ax_was_trusted=False) is False


def test_grant_cdhash_change_needs_both_hashes():
    # A change can only be claimed when BOTH hashes are known; a missing current or last
    # hash (never-trusted) must not be reported stale.
    assert is_grant_stale(current_cdhash="x", last_cdhash=None, ax_was_trusted=False) is False
    assert is_grant_stale(current_cdhash=None, last_cdhash="y", ax_was_trusted=False) is False


def test_grant_was_trusted_dominates_even_with_equal_cdhash():
    # ax_was_trusted=True is stale regardless of cdhash equality (a manual revoke is
    # reported stale too -- a benign false positive; the remove-and-re-add cure still works).
    assert is_grant_stale(current_cdhash="same", last_cdhash="same", ax_was_trusted=True) is True


# ---- posted-switch read-back classification (orphaned-desktop honesty fix) --
#
# classify_switch maps the SETTLED current Space + read-back EVIDENCE to an outcome. The
# orphan bounce is a sequence (flash target -> land on Desktop 1), so a bare
# observed==home is not enough: REVERTED requires evidence a bounce occurred -- the target
# was seen (saw_target) OR the click started elsewhere (origin != home). home with neither
# is a no-op -> WRONG_DESKTOP, not an orphan. Only REVERTED earns the "reconnect the
# display" wording. The success decision (which read is the settled one) is the agent's.
#
# Fixtures use the real reference layout: home (Desktop 1) = the no-UUID default,
# id64 == 1; Kids/Valeriia == 650; 503s-upgrade == 927; Spacefix == 2541.


def _classify(**kw):
    # Defaults: origin unknown, target not seen — so each test states only its evidence.
    kw.setdefault("origin_id", None)
    kw.setdefault("saw_target", False)
    return classify_switch(**kw)


def test_classify_switch_confirmed_when_observed_is_target():
    assert _classify(target_id=697, observed_id=697, home_id=1) is SwitchOutcome.CONFIRMED


def test_classify_switch_reverted_on_home_bounce_from_other_space():
    # THE REAL REPRO: click an orphaned pill (target 650) from a non-Desktop-1 Space
    # (origin 2541); macOS flashes it and bounces to Desktop 1 (home 1). Evidence =
    # started elsewhere -> REVERTED, so the "reconnect the display" notice fires.
    outcome = _classify(target_id=650, observed_id=1, home_id=1, origin_id=2541)
    assert outcome is SwitchOutcome.REVERTED


def test_classify_switch_reverted_on_home_bounce_via_flash_evidence():
    # Clicked from Desktop 1 itself (origin == home), but the target was seen to flash
    # before the bounce -> still an evidenced orphan bounce.
    outcome = _classify(target_id=650, observed_id=1, home_id=1, origin_id=1, saw_target=True)
    assert outcome is SwitchOutcome.REVERTED


def test_classify_switch_home_noop_without_evidence_is_wrong_desktop_not_orphan():
    # Started on Desktop 1 and the event had NO effect (never saw the target): settled on
    # home but with no bounce evidence -> generic, NOT the orphan diagnosis (#3/#4).
    outcome = _classify(target_id=650, observed_id=1, home_id=1, origin_id=1, saw_target=False)
    assert outcome is SwitchOutcome.WRONG_DESKTOP


def test_classify_switch_noop_at_nonhome_origin_is_wrong_desktop():
    # No effect, stayed at a non-home origin (2541): not target, not home -> generic.
    outcome = _classify(target_id=650, observed_id=2541, home_id=1, origin_id=2541)
    assert outcome is SwitchOutcome.WRONG_DESKTOP


def test_classify_switch_other_desktop_is_wrong_desktop():
    # Landed on a genuinely different desktop (not target, not home) -> generic notice,
    # even with a flash seen (it did not settle on home, so it is not a home bounce).
    outcome = _classify(target_id=650, observed_id=927, home_id=1, origin_id=2541, saw_target=True)
    assert outcome is SwitchOutcome.WRONG_DESKTOP


def test_classify_switch_confirmed_takes_precedence_over_bounce_evidence():
    # Clicking Desktop 1 itself and landing there: observed == target == home -> CONFIRMED
    # wins over the reverted check regardless of evidence, so no spurious failure notice.
    outcome = _classify(target_id=1, observed_id=1, home_id=1, origin_id=2541, saw_target=True)
    assert outcome is SwitchOutcome.CONFIRMED
