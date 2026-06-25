"""Pure label-resolution helpers (DESIGN.md §6.1, DECISIONS.md 9.4)."""

from __future__ import annotations

from spacelabel.labeling import (
    assign_ordinals,
    canonical_uuid,
    find_orphans,
    is_labelable,
    is_uuid,
    ordinal_for_uuid,
    pill_text,
    title_for,
    truncate,
)
from spacelabel.model import Label, Space


def _space(uuid, display="D1", current=False):
    return Space(uuid=uuid, display_uuid=display, is_current=current)


def test_is_labelable_gates_on_uuid():
    # Spaces with a real UUID are labelable; the no-UUID default desktop is not.
    assert is_labelable(_space("6622AC87-2FD2-48E8-934D-F6EB303AC9BA"))
    assert not is_labelable(_space(""))  # the default no-UUID single-Space desktop


def test_is_uuid():
    assert is_uuid("6622AC87-2FD2-48E8-934D-F6EB303AC9BA")
    assert is_uuid("6622ac87-2fd2-48e8-934d-f6eb303ac9ba")  # any case
    assert not is_uuid("list")  # a transposed TARGET/TEXT, not a UUID
    assert not is_uuid("current")  # the sentinel is resolved separately, not a UUID
    assert not is_uuid("")


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


def test_assign_ordinals_counts_default_desktop():
    # macOS numbers a display's default unlabelable Space (uuid="") a Desktop too, so
    # the FULL enumeration is the single source of truth shared by pills/Prefs/switch
    # (item V, verified dual-display 2026-06-24). A labelable Space preceded by a
    # default Space is "Desktop 2", not 1 -- numbering only labelable Spaces (the old
    # Preferences path) drifted -1 from the pill/menu number.
    default = _space("", display="4K")  # the 4K display's default desktop
    first = _space("A", display="4K")  # the added labelable 2nd desktop on the 4K
    portrait = _space("B", display="P")  # the portrait's first desktop
    ordinals = assign_ordinals([default, first, portrait])
    assert ordinals[id(default)] == 1
    assert ordinals[id(first)] == 2  # counts the default desktop ahead of it
    assert ordinals[id(portrait)] == 3
    # A surface that HIDES the default Space (Preferences) still numbers by identity
    # over the full enumeration, so each shown Space keeps its true Desktop number.
    shown = [s for s in (default, first, portrait) if s.uuid]
    assert [ordinals[id(s)] for s in shown] == [2, 3]


def test_ordinal_for_uuid_resolves_live_position():
    # Click-to-switch maps the clicked Space's UUID to its current ordinal at click
    # time (DECISIONS.md 9.5); reordering shifts the answer, which is why it is never
    # cached.
    a, b, c = _space("a"), _space("b"), _space("c")
    assert ordinal_for_uuid([a, b, c], "b") == 2
    assert ordinal_for_uuid([b, a, c], "b") == 1  # reordered -> different ordinal


def test_ordinal_for_uuid_absent_or_empty_returns_none():
    a, b = _space("a"), _space("b")
    assert ordinal_for_uuid([a, b], "missing") is None
    # An empty UUID (unlabelable Space) is never a switch target, even if present.
    assert ordinal_for_uuid([_space(""), a], "") is None


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
