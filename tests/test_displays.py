"""Pure display-name helpers (friendly/describe/resolved). No PyObjC needed."""

from __future__ import annotations

from spacelabel.model import Display
from spacelabel.platform.displays import describe, friendly_name, resolved_name

UUID = "874A623F-F8F5-43C1-B11C-4AAC3E383C0F"


def _display(*, name=None, uuid=UUID):
    return Display(
        uuid=uuid,
        cg_display_id=1,
        size_pt=(2160.0, 3840.0),
        orientation="portrait",
        name=name,
    )


def test_friendly_name_prefers_localized_else_uuid_prefix():
    assert friendly_name(_display(name="LG UltraFine")) == "LG UltraFine"
    assert friendly_name(_display(name=None)) == f"Display {UUID[:8]}"


def test_describe_is_ascii_only():
    text = describe(_display(name="LG UltraFine"))
    assert text == "LG UltraFine - portrait - 2160x3840"
    assert chr(0x00D7) not in text  # the MULTIPLICATION SIGN, not plain ascii 'x'


def test_resolved_name_prefers_user_override():
    display = _display(name="LG UltraFine")
    assert resolved_name(display, {UUID: "Main monitor"}) == "Main monitor"
    assert resolved_name(display, {}) == "LG UltraFine"
    assert resolved_name(display, {UUID: ""}) == "LG UltraFine"  # empty override ignored
