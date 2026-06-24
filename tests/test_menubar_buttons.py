"""Buttons-row layout, hit-testing, and click-to-switch dispatch (DECISIONS.md 9.4/9.5).

The layout/hit-test helpers are pure (no WindowServer); the ``ButtonsRowView`` tests
need PyObjC but no window server (an ``NSView`` allocates headless) and are skipped
where AppKit is absent (DESIGN.md §12 testing reality).
"""

from __future__ import annotations

import pytest

from spacelabel.agent.menubar import (
    PillModel,
    _pill_at_x,
    _pill_layout,
    _preferred_width,
)

_ROW_H = 22.0


def _pill(text, uuid="", *, current=False, id64=0):
    return PillModel(text, is_current=current, color=None, uuid=uuid, id64=id64)


# -- pure layout + hit-testing ------------------------------------------------


def test_pill_layout_orders_pills_and_inserts_dividers_between_displays():
    groups = [[_pill("E", "U1"), _pill("C", "U2")], [_pill("T", "U3")]]
    pills, cy, divider_xs = _pill_layout(groups, _ROW_H)

    assert [p.uuid for _, _, p in pills] == ["U1", "U2", "U3"]
    # x positions strictly increase in draw order.
    xs = [x for x, _, _ in pills]
    assert xs == sorted(xs)
    # One divider between the two displays, sitting between groups (after U2, before U3).
    assert len(divider_xs) == 1
    u2_x, u2_w, _ = pills[1]
    u3_x, _, _ = pills[2]
    assert u2_x + u2_w <= divider_xs[0] <= u3_x
    # Pills are vertically centered within the row.
    assert cy == (_ROW_H - 16.0) / 2.0  # _PILL_HEIGHT == 16.0


def test_pill_at_x_resolves_the_pill_under_the_point():
    groups = [[_pill("E", "U1"), _pill("C", "U2")]]
    pills, _, _ = _pill_layout(groups, _ROW_H)
    (x1, w1, _), (x2, w2, _) = pills

    assert _pill_at_x(pills, x1 + w1 / 2).uuid == "U1"
    assert _pill_at_x(pills, x2 + w2 / 2).uuid == "U2"
    # Left margin (before the first pill) is not a hit.
    assert _pill_at_x(pills, 0.0) is None
    # Far right (past the last pill) is not a hit.
    assert _pill_at_x(pills, x2 + w2 + 50.0) is None


def test_pill_at_x_picks_the_right_display_group():
    # A click lands on the correct Space even across a display divider.
    groups = [[_pill("E", "U1")], [_pill("T", "U2")]]
    pills, _, _ = _pill_layout(groups, _ROW_H)
    assert _pill_at_x(pills, pills[1][0] + 1.0).uuid == "U2"


def test_preferred_width_grows_with_content():
    one = _preferred_width([[_pill("E", "U1")]])
    many = _preferred_width([[_pill("Email", "U1"), _pill("Code", "U2")], [_pill("T", "U3")]])
    assert many > one
    assert _preferred_width([]) > 0  # never zero-width


# -- ButtonsRowView (headless: NSView allocates without a window server) -------

pytest.importorskip("AppKit", reason="PyObjC/AppKit not available (non-macOS)")


def _row_view(groups):
    from AppKit import NSMakeRect

    from spacelabel.agent.menubar import ButtonsRowView

    view = ButtonsRowView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 200.0, _ROW_H))
    view.set_groups(groups)
    return view


def test_disabled_row_falls_clicks_through_to_the_status_button():
    # click_to_switch off (default): hitTest_ returns nil so the click reaches the
    # status button and opens the menu -- the NSView click-through equivalent of the
    # old "ignoresMouseEvents == True" smoke check (DECISIONS.md 9.5).
    from AppKit import NSMakePoint

    view = _row_view([[_pill("E", "U1")]])
    view.set_click_enabled(False)
    assert view.hitTest_(NSMakePoint(10.0, 10.0)) is None


def test_enabled_row_is_the_hit_target():
    from AppKit import NSMakePoint

    view = _row_view([[_pill("E", "U1")]])
    view.set_click_enabled(True)
    assert view.hitTest_(NSMakePoint(10.0, 10.0)) is view


def test_click_on_pill_invokes_switch_handler_with_uuid():
    switched: list[str] = []
    opened_menu: list[bool] = []
    view = _row_view([[_pill("E", "U1"), _pill("C", "U2")]])
    view.set_handlers(lambda u, i: switched.append((u, i)), lambda: opened_menu.append(True))

    pills, _, _ = _pill_layout([[_pill("E", "U1"), _pill("C", "U2")]], _ROW_H)
    x2 = pills[1][0] + pills[1][1] / 2
    view._handle_click_at_x(x2)

    assert switched == [("U2", 0)]
    assert opened_menu == []


def test_click_off_a_pill_opens_the_menu():
    switched: list[tuple[str, int]] = []
    opened_menu: list[bool] = []
    view = _row_view([[_pill("E", "U1")]])
    view.set_handlers(lambda u, i: switched.append((u, i)), lambda: opened_menu.append(True))

    view._handle_click_at_x(0.0)  # left margin, before any pill

    assert switched == []
    assert opened_menu == [True]


def test_click_on_pill_with_no_identity_opens_menu_not_dead_click():
    # A pill with NEITHER a uuid NOR a session id64 has no Space identity to resolve, so
    # a click opens the menu rather than a dead click (review P2). (The default
    # unlabelable Space normally DOES carry an id64 -> see the switch test below.)
    switched: list[tuple[str, int]] = []
    opened_menu: list[bool] = []
    groups = [[_pill("1", "")], [_pill("E", "U1")]]  # uuid="" and id64=0 -> no identity
    view = _row_view(groups)
    view.set_handlers(lambda u, i: switched.append((u, i)), lambda: opened_menu.append(True))

    pills, _, _ = _pill_layout(groups, _ROW_H)
    no_identity_x = pills[0][0] + pills[0][1] / 2
    view._handle_click_at_x(no_identity_x)

    assert switched == []  # no identity -> no switch attempt
    assert opened_menu == [True]  # opens the menu instead of a dead click

    # The labelable pill in the same row still switches normally.
    labelable_x = pills[1][0] + pills[1][1] / 2
    view._handle_click_at_x(labelable_x)
    assert switched == [("U1", 0)]


def test_click_on_default_space_pill_switches_by_id64():
    # The default unlabelable Space (uuid="") carries a session id64 and IS a switch
    # target (DECISIONS 9.5 update): clicking its pill invokes the switch handler with
    # (uuid="", id64), not the menu. A labelable pill still switches by uuid.
    switched: list[tuple[str, int]] = []
    opened_menu: list[bool] = []
    groups = [[_pill("1", "", id64=1)], [_pill("E", "U1", id64=99)]]
    view = _row_view(groups)
    view.set_handlers(lambda u, i: switched.append((u, i)), lambda: opened_menu.append(True))

    pills, _, _ = _pill_layout(groups, _ROW_H)
    default_x = pills[0][0] + pills[0][1] / 2
    view._handle_click_at_x(default_x)
    assert switched == [("", 1)]  # the default Space switches by its id64
    assert opened_menu == []

    labelable_x = pills[1][0] + pills[1][1] / 2
    view._handle_click_at_x(labelable_x)
    assert switched == [("", 1), ("U1", 99)]
