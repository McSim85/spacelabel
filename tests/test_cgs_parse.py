"""Pure CGS structure parsing — the testable core of the read path (DESIGN.md §3.4).

Exercises ``parse_spaces`` with mocked ``CGSCopyManagedDisplaySpaces`` output (plain
dicts), so it runs with no WindowServer (DESIGN.md §12 testing reality).
"""

from __future__ import annotations

import pytest

from spacelabel.platform import cgs
from spacelabel.platform.cgs import parse_spaces

# Real-format CFUUID strings (the stable per-Space key, DECISIONS.md 1.4).
U_EMAIL = "6622AC87-2FD2-48E8-934D-F6EB303AC9BA"
U_CODE = "1A0F5C2E-7B3D-4C8A-9E1F-2D4B6A8C0E12"
U_TERM = "9C44E7B1-3E2A-4F5C-8B6D-1A2B3C4D5E6F"
U_FULL = "ABCDEF01-2345-6789-ABCD-EF0123456789"
DISP_A = "874A623F-1111-2222-3333-444455556666"
DISP_B = "6FBB92D9-84CE-8D20-C114-3B1052DD9529"


def test_returns_only_labelable_spaces():
    managed = [
        {
            "Display Identifier": DISP_A,
            "Spaces": [
                {"uuid": U_EMAIL, "id64": 101, "type": 0},
                {"uuid": U_CODE, "id64": 102, "ManagedSpaceID": 102, "type": 0},
                {"uuid": "", "id64": 0, "type": 0},  # header row -> skip
                {"uuid": U_FULL, "id64": 103, "type": 0, "TileLayoutManager": {}},  # fullscreen
                {"uuid": "dashboard", "id64": 104, "type": 4},  # special non-uuid
            ],
        }
    ]
    spaces = parse_spaces(managed)
    assert [s.uuid for s in spaces] == [U_EMAIL, U_CODE]
    assert all(s.display_uuid == DISP_A for s in spaces)


def test_marks_current_via_id64():
    managed = [
        {
            "Display Identifier": DISP_A,
            "Spaces": [
                {"uuid": U_EMAIL, "id64": 101, "type": 0},
                {"uuid": U_CODE, "id64": 102, "type": 0},
            ],
        }
    ]
    spaces = parse_spaces(managed, current_ids={102})
    by_uuid = {s.uuid: s for s in spaces}
    assert by_uuid[U_CODE].is_current is True
    assert by_uuid[U_EMAIL].is_current is False


def test_main_sentinel_remapped_to_primary_uuid():
    managed = [
        {
            "Display Identifier": "Main",
            "Spaces": [{"uuid": U_EMAIL, "id64": 1, "type": 0}],
        }
    ]
    spaces = parse_spaces(managed, main_display_uuid=DISP_A)
    assert spaces[0].display_uuid == DISP_A


def test_parse_spaces_canonicalizes_uuids_from_windowserver():
    # If the WindowServer ever emits a lowercase/braced UUID, parse_spaces must yield
    # the canonical uppercase spelling so the join against canonical stored keys holds.
    managed = [
        {
            "Display Identifier": DISP_A.lower(),
            "Spaces": [{"uuid": U_EMAIL.lower(), "id64": 1, "type": 0}],
        }
    ]
    spaces = parse_spaces(managed)
    assert spaces[0].uuid == U_EMAIL
    assert spaces[0].display_uuid == DISP_A


def test_multi_display_separate_spaces():
    managed = [
        {
            "Display Identifier": DISP_A,
            "Spaces": [
                {"uuid": U_EMAIL, "id64": 1, "type": 0},
                {"uuid": U_CODE, "id64": 2, "type": 0},
            ],
        },
        {
            "Display Identifier": DISP_B,
            "Spaces": [{"uuid": U_TERM, "id64": 3, "type": 0}],
        },
    ]
    spaces = parse_spaces(managed, current_ids={2, 3})
    assert {s.uuid for s in spaces} == {U_EMAIL, U_CODE, U_TERM}
    a = [s for s in spaces if s.display_uuid == DISP_A]
    b = [s for s in spaces if s.display_uuid == DISP_B]
    assert len(a) == 2
    assert len(b) == 1
    assert b[0].is_current is True  # each display has its own current


def test_empty_input():
    assert parse_spaces([]) == []
    assert parse_spaces([{"Display Identifier": DISP_A, "Spaces": []}]) == []


def test_missing_spaces_key_does_not_crash():
    assert parse_spaces([{"Display Identifier": DISP_A}]) == []


def test_enumerate_spaces_raises_on_nil_managed_result():
    # A nil/garbage CGSCopyManagedDisplaySpaces result is a FAILED read (raise), not
    # an empty topology -- so callers engage the plist fallback / exit-1 path.
    cgs._NS.clear()
    cgs._NS.update(
        {
            "CGSMainConnectionID": lambda: 1,
            "CGSCopyManagedDisplaySpaces": lambda _conn: None,  # nil result
            "CGSManagedDisplayGetCurrentSpace": lambda _conn, _ident: 0,
            "CGSCopyActiveMenuBarDisplayIdentifier": lambda _conn: "",
        }
    )
    try:
        with pytest.raises(cgs.CGSUnavailableError):
            cgs.enumerate_spaces()
    finally:
        cgs._NS.clear()


def test_include_unlabelable_surfaces_empty_uuid_spaces():
    # A display's single default Space has uuid='' (no macOS-assigned UUID) plus a
    # 'wsid' key. Default: skipped. With include_unlabelable: returned with uuid=''.
    managed = [
        {
            "Display Identifier": DISP_B,
            "Spaces": [{"uuid": "", "id64": 1, "type": 0, "wsid": 7}],
        }
    ]
    assert parse_spaces(managed) == []  # default skips it
    surfaced = parse_spaces(managed, current_ids={1}, include_unlabelable=True)
    assert len(surfaced) == 1
    assert surfaced[0].uuid == ""
    assert surfaced[0].display_uuid == DISP_B
    assert surfaced[0].is_current is True  # current marked via id64 even without a uuid


def test_include_unlabelable_still_skips_special_spaces():
    managed = [
        {
            "Display Identifier": DISP_A,
            "Spaces": [
                {"uuid": "", "id64": 2, "type": 4},  # special type -> skip even unlabelable
                {"uuid": U_FULL, "id64": 3, "type": 0, "TileLayoutManager": {}},  # fullscreen
            ],
        }
    ]
    assert parse_spaces(managed, include_unlabelable=True) == []


def test_malformed_space_element_is_skipped_not_fatal():
    # A non-mapping element and a malformed-type element must be skipped (logged),
    # never abort the whole enumeration (no-silent-except, DESIGN §8.2).
    managed = [
        {
            "Display Identifier": DISP_A,
            "Spaces": [
                "not-a-dict",  # bridged scalar -> skip
                {"uuid": U_EMAIL, "id64": 1, "type": 0},  # valid
                {"uuid": U_CODE, "id64": "not-an-int", "type": 0},  # bad id -> skip
            ],
        }
    ]
    spaces = parse_spaces(managed)
    assert [s.uuid for s in spaces] == [U_EMAIL]
