"""Pure plist fallback parsing (DESIGN.md §11, DECISIONS.md 3.4)."""

from __future__ import annotations

from spacelabel.platform.spaces_plist import parse_spaces_plist

U_A = "6622AC87-2FD2-48E8-934D-F6EB303AC9BA"
U_B = "1A0F5C2E-7B3D-4C8A-9E1F-2D4B6A8C0E12"
DISP = "874A623F-1111-2222-3333-444455556666"


def _plist(monitors):
    return {"SpacesDisplayConfiguration": {"Management Data": {"Monitors": monitors}}}


def test_extracts_labelable_uuids():
    data = _plist(
        [
            {
                "Display Identifier": DISP,
                "Spaces": [
                    {"uuid": U_A, "type": 0},
                    {"uuid": U_B, "type": 0},
                    {"uuid": "", "type": 0},  # skip header
                    {"uuid": "ABCDEF01-2345-6789-ABCD-EF0123456789", "type": 4},  # skip special
                ],
            }
        ]
    )
    spaces = parse_spaces_plist(data)
    assert [s.uuid for s in spaces] == [U_A, U_B]
    assert all(s.display_uuid == DISP for s in spaces)


def test_current_always_false_because_plist_is_stale():
    data = _plist([{"Display Identifier": DISP, "Spaces": [{"uuid": U_A, "type": 0}]}])
    spaces = parse_spaces_plist(data)
    assert spaces[0].is_current is False


def test_malformed_or_empty_does_not_crash():
    assert parse_spaces_plist({}) == []
    assert parse_spaces_plist({"SpacesDisplayConfiguration": {}}) == []
    assert parse_spaces_plist(_plist([])) == []
    assert parse_spaces_plist(_plist([{"Display Identifier": DISP}])) == []


def test_main_sentinel_remapped_to_primary_uuid():
    data = _plist([{"Display Identifier": "Main", "Spaces": [{"uuid": U_A, "type": 0}]}])
    spaces = parse_spaces_plist(data, main_display_uuid=DISP)
    assert spaces[0].display_uuid == DISP


def test_plist_canonicalizes_lowercase_uuid():
    # The plist fallback must yield the canonical uppercase UUID, same as the CGS path,
    # so both read sources join against canonical stored keys.
    data = _plist([{"Display Identifier": DISP, "Spaces": [{"uuid": U_A.lower(), "type": 0}]}])
    spaces = parse_spaces_plist(data)
    assert spaces[0].uuid == U_A


def test_plist_canonicalizes_real_uuid_display_identifier():
    # A real-UUID Display Identifier must canonicalize too (mirrors
    # cgs._normalize_display_identifier), so the fallback's display grouping joins
    # against discover_topology's canonical display.uuid.
    data = _plist([{"Display Identifier": DISP.lower(), "Spaces": [{"uuid": U_A, "type": 0}]}])
    spaces = parse_spaces_plist(data)
    assert spaces[0].display_uuid == DISP


def test_malformed_space_type_is_skipped_not_raised():
    # A non-numeric "type" must be skipped (best-effort), never crash read_spaces.
    data = _plist(
        [
            {
                "Display Identifier": DISP,
                "Spaces": [
                    {"uuid": U_A, "type": 0},
                    {"uuid": U_B, "type": "weird"},  # non-numeric -> skip
                ],
            }
        ]
    )
    spaces = parse_spaces_plist(data)
    assert [s.uuid for s in spaces] == [U_A]


def test_main_sentinel_kept_raw_when_no_primary():
    data = _plist([{"Display Identifier": "Main", "Spaces": [{"uuid": U_A, "type": 0}]}])
    spaces = parse_spaces_plist(data)  # no main_display_uuid
    assert spaces[0].display_uuid == "Main"
