"""Pure label-resolution helpers (DESIGN.md §6.1, DECISIONS.md 9.4)."""

from __future__ import annotations

from spacelabel.labeling import (
    assign_ordinals,
    canonical_uuid,
    find_orphans,
    pill_text,
    title_for,
    truncate,
)
from spacelabel.model import Label, Space


def _space(uuid, display="D1", current=False):
    return Space(uuid=uuid, display_uuid=display, is_current=current)


def test_assign_ordinals_is_one_based_keyed_by_identity():
    a, b, c = _space("a"), _space("b"), _space("c")
    assert assign_ordinals([a, b, c]) == {id(a): 1, id(b): 2, id(c): 3}


def test_assign_ordinals_distinguishes_empty_uuid_spaces():
    # Multiple unlabelable Spaces (uuid="") must each get a distinct ordinal, not
    # collapse onto one (the multi-display no-UUID case).
    s1 = _space("", display="A")
    s2 = _space("", display="B")
    ordinals = assign_ordinals([s1, s2])
    assert ordinals[id(s1)] == 1
    assert ordinals[id(s2)] == 2
    assert len(ordinals) == 2


def test_truncate():
    assert truncate("Email", 24) == "Email"
    assert truncate("A very long label here", 8) == "A very …"
    assert truncate("abc", 1) == "…"
    assert truncate("abc", 0) == "abc"  # no limit


def test_title_for_uses_label_then_falls_back():
    labels = {"a": Label(text="Email")}
    assert title_for(_space("a"), labels, 1) == "Email"
    # Unlabeled -> Desktop N, never blank
    assert title_for(_space("b"), labels, 7) == "Desktop 7"


def test_title_for_truncates_to_max_length():
    labels = {"a": Label(text="A really long descriptive label")}
    assert title_for(_space("a"), labels, 1, max_length=10) == "A really …"


def test_title_for_blank_label_falls_back():
    labels = {"a": Label(text="   ")}
    assert title_for(_space("a"), labels, 3) == "Desktop 3"


def test_pill_text_leading_letters_or_number():
    labels = {"a": Label(text="Docs"), "b": Label(text="Email")}
    assert pill_text(_space("a"), labels, 1, chars=1) == "D"
    assert pill_text(_space("a"), labels, 1, chars=2) == "Do"
    assert pill_text(_space("b"), labels, 1, chars=1) == "E"
    # unlabeled -> the space number
    assert pill_text(_space("c"), labels, 9, chars=1) == "9"


def test_find_orphans_preserves_store_order():
    labels = {"keep": Label(text="x"), "gone": Label(text="y"), "keep2": Label(text="z")}
    live = {"keep", "keep2", "extra-live"}
    assert find_orphans(labels, live) == ["gone"]
    assert find_orphans(labels, set()) == ["keep", "gone", "keep2"]
    assert find_orphans({}, live) == []


def test_canonical_uuid_normalizes_case_and_braces():
    lower = "abcdef01-2345-6789-abcd-ef0123456789"
    assert canonical_uuid(lower) == lower.upper()
    # CGS emits the bare uppercase CFUUID spelling; braced/mixed-case inputs from a
    # legacy file canonicalize to the SAME key so a live lookup matches.
    assert canonical_uuid("{ABCDEF01-2345-6789-ABCD-EF0123456789}") == lower.upper()
    assert canonical_uuid(lower.upper()) == lower.upper()


def test_canonical_uuid_passes_non_uuid_through_unchanged():
    # The "Main" sentinel and arbitrary keys are returned verbatim, never mangled.
    assert canonical_uuid("Main") == "Main"
    assert canonical_uuid("") == ""
    assert canonical_uuid("not-a-uuid") == "not-a-uuid"
