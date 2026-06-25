"""Pure HUD/overlay geometry — fonts + the nine-anchor grid (DESIGN.md §9.9 / §4.2)."""

from __future__ import annotations

import pytest

from spacelabel.agent.geometry import (
    ANCHORS,
    anchor_origin,
    clamp,
    hud_font_size,
    overlay_font_size,
    overlay_max_content_extent,
    overlay_note_font_size,
    parse_anchor,
    short_side,
)


def test_overlay_note_font_size():
    # "auto" sits a step (2pt) below the title with a 9pt floor; an int is verbatim.
    assert overlay_note_font_size(15, "auto") == 13
    assert overlay_note_font_size(10, "auto") == 9  # floor (10-2=8 -> 9)
    assert overlay_note_font_size(9, "auto") == 9
    assert overlay_note_font_size(20, 16) == 16


def test_overlay_max_content_extent():
    # content = available - 2*margin - 2*pad, so the panel (content + 2*pad) is inset
    # by the margin on both sides and never exceeds the screen for ANY anchor.
    assert overlay_max_content_extent(1000.0, 12.0, 14.0) == 1000.0 - 24.0 - 28.0
    assert overlay_max_content_extent(200.0, 12.0, 14.0) == 200.0 - 24.0 - 28.0
    # Degenerate (narrower than margins+padding): clamped to 0, never negative.
    assert overlay_max_content_extent(30.0, 12.0, 14.0) == 0.0
    # Invariant: an edge anchor subtracts one margin from the panel position, so the
    # padded panel must fit within (available - margin) on every real display.
    margin, pad = 12.0, 14.0
    for available in (320.0, 768.0, 1080.0, 1920.0):
        content = overlay_max_content_extent(available, margin, pad)
        assert content + 2 * pad <= available - margin


def test_clamp_bounds():
    assert clamp(5, 0, 10) == 5
    assert clamp(-1, 0, 10) == 0
    assert clamp(99, 0, 10) == 10


def test_short_side_orientation_agnostic():
    assert short_side((1080, 1920)) == 1080
    assert short_side((1920, 1080)) == 1080


@pytest.mark.parametrize(
    ("size_pt", "expected"),
    [
        ((1080, 1920), 54),  # LG UltraFine portrait, S=1080
        ((1920, 1080), 54),  # DELL 4K landscape scaled, S=1080
        ((1470, 956), 48),  # 13" laptop, S=956
        ((3840, 2160), 64),  # native 4K @1x, S=2160 -> clamp at 64
        ((400, 300), 18),  # tiny -> clamp at 18
    ],
)
def test_hud_font_matches_design_table(size_pt, expected):
    assert hud_font_size(size_pt) == expected


@pytest.mark.parametrize(
    ("size_pt", "expected"),
    [
        ((1080, 1920), 19),
        ((1470, 956), 17),
        ((3840, 2160), 28),  # clamp at 28
        ((200, 200), 12),  # clamp at 12
    ],
)
def test_overlay_font_auto(size_pt, expected):
    assert overlay_font_size(size_pt, "auto") == expected


def test_overlay_font_int_passthrough():
    assert overlay_font_size((1080, 1920), 15) == 15
    assert overlay_font_size((3840, 2160), 40) == 40


def test_nine_anchors_present():
    assert len(ANCHORS) == 9
    for name in (
        "top-left",
        "top-center",
        "top-right",
        "center-left",
        "center",
        "center-right",
        "bottom-left",
        "bottom-center",
        "bottom-right",
    ):
        assert name in ANCHORS


def test_parse_anchor_axes():
    assert parse_anchor("center") == ("center", "middle")
    assert parse_anchor("top-left") == ("left", "top")
    assert parse_anchor("bottom-right") == ("right", "bottom")
    assert parse_anchor("center-left") == ("left", "middle")
    assert parse_anchor("top-center") == ("center", "top")


def test_parse_anchor_rejects_unknown():
    with pytest.raises(ValueError, match="unknown anchor"):
        parse_anchor("middle-middle")


def test_anchor_origin_center_ignores_margin():
    # vf (vx,vy,vw,vh) = (0,0,1000,800); panel 200x100
    assert anchor_origin((0, 0, 1000, 800), 200, 100, "center", 24) == (400.0, 350.0)


def test_anchor_origin_corners():
    vf = (0, 0, 1000, 800)
    assert anchor_origin(vf, 200, 100, "top-right", 12) == (1000 - 200 - 12, 800 - 100 - 12)
    assert anchor_origin(vf, 200, 100, "bottom-left", 12) == (12, 12)
    assert anchor_origin(vf, 200, 100, "top-left", 12) == (12, 800 - 100 - 12)
    assert anchor_origin(vf, 200, 100, "bottom-right", 12) == (1000 - 200 - 12, 12)


def test_anchor_origin_respects_visible_frame_offset():
    # A non-zero visibleFrame origin (menu bar / Dock inset) shifts placement.
    vf = (100, 50, 1000, 800)
    x, y = anchor_origin(vf, 200, 100, "top-left", 10)
    assert x == 110
    assert y == 50 + 800 - 100 - 10
