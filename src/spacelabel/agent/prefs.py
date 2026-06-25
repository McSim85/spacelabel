"""Preferences window -- a two-level ``NSOutlineView`` (DESIGN.md §6 / DECISIONS.md 2.4, 9.6).

Each physical **display** is a parent row; its **Spaces** are children, so two
displays' Spaces never conflate (docs/UI.md §3). Columns: an inline-editable label
column (commits on Return AND focus-loss, Esc cancels -- DECISIONS.md 9.6), an
``NSColorWell`` color column, and a "now" marker for the current Space on its
display. A "Prune orphans..." button drops labels for absent Spaces.

This window is one of the two label writers (the CLI is the other), so edits go
through the same locked store path (:func:`spacelabel.store.set_label`). The UI is
built inside :meth:`PreferencesWindow.show` (and ``__init__`` only stores state)
so importing this module never constructs AppKit objects -- enabling an import
smoke test.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, cast

import objc
from AppKit import (
    NSBackingStoreBuffered,
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSColorWell,
    NSControlStateValueOff,
    NSControlStateValueOn,
    NSFont,
    NSMakeRect,
    NSObject,
    NSOutlineView,
    NSPopUpButton,
    NSScrollView,
    NSTableCellView,
    NSTableColumn,
    NSTextField,
    NSViewWidthSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
)

from spacelabel import labeling, store
from spacelabel.agent.geometry import ANCHORS

if TYPE_CHECKING:
    from collections.abc import Callable

    from spacelabel.model import Display, Label, Space

__all__ = ["PreferencesWindow"]

log = logging.getLogger(__name__)


def _center_on_main_screen(window: object) -> None:
    """Position ``window`` at the centre of the menu-bar-owning screen (item T).

    ``NSWindow.center()`` centres on *the window's current screen*, which may differ
    from the active (menu-bar-owning) screen after a monitor layout change or a
    multi-display move. This helper always targets ``NSScreen.mainScreen()``.
    """
    from AppKit import NSScreen

    screen = NSScreen.mainScreen()
    if screen is None:
        window.center()
        return
    sf = screen.visibleFrame()
    wf = window.frame()
    x = float(sf.origin.x) + (float(sf.size.width) - float(wf.size.width)) / 2.0
    y = float(sf.origin.y) + (float(sf.size.height) - float(wf.size.height)) / 2.0
    window.setFrameOrigin_((x, y))


#: Column identifiers.
_COL_LABEL = "label"
_COL_UUID = "uuid"
_COL_COLOR = "color"
_COL_NOW = "now"
_COL_OVERLAY = "overlay"

#: The nine anchors in reading order for the position/corner popups.
_ANCHOR_ORDER = (
    "top-left",
    "top-center",
    "top-right",
    "center-left",
    "center",
    "center-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
)
# Keep the popup order in sync with the geometry source of truth.
assert set(_ANCHOR_ORDER) == ANCHORS

#: Settings-strip mode checkboxes: (tag, dotted config key, label).
_MODE_CHECKBOXES = (
    (1, "modes.menubar", "Menu-bar title"),
    (2, "modes.hud", "On-switch HUD"),
    (3, "modes.overlay", "Corner overlay"),
    (4, "modes.wallpaper", "Wallpaper (exp)"),
    (5, "menubar.show_buttons_row", "Buttons row"),
    (6, "overlay.hide_on_unlabeled", "Hide overlay on unlabeled"),
)


def _state(on: bool) -> int:
    """Map a bool to an ``NSControlStateValue`` for a checkbox."""
    return NSControlStateValueOn if on else NSControlStateValueOff


def _color_from_hex(hex_color: str) -> object | None:
    """Parse ``#rrggbb`` (or ``rrggbb``) into an ``NSColor``; None if malformed."""
    text = hex_color.lstrip("#")
    if len(text) != 6:
        return None
    try:
        red = int(text[0:2], 16) / 255.0
        green = int(text[2:4], 16) / 255.0
        blue = int(text[4:6], 16) / 255.0
    except ValueError:
        return None
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(red, green, blue, 1.0)


def _hex_from_color(color: object) -> str | None:
    """Convert an ``NSColor`` to ``#rrggbb`` (calibrated RGB), or None if unconvertible."""
    rgb = color.colorUsingColorSpaceName_("NSCalibratedRGBColorSpace")
    if rgb is None:
        return None
    red = round(rgb.redComponent() * 255)
    green = round(rgb.greenComponent() * 255)
    blue = round(rgb.blueComponent() * 255)
    return f"#{red:02x}{green:02x}{blue:02x}"


class _LabelColorWell(NSColorWell):
    """An ``NSColorWell`` that remembers the Space UUID it edits.

    ``NSColorWell`` (an ``NSView``) has no settable ``tag``, so the row's UUID is
    carried as a Python attribute and read back in the change handler.
    """

    def initWithFrame_(self, frame: object) -> _LabelColorWell | None:  # noqa: N802
        """Initialize with an empty Space UUID."""
        self = objc.super(_LabelColorWell, self).initWithFrame_(frame)
        if self is None:
            return None
        self._space_uuid = ""
        return self

    def activate_(self, exclusive: bool) -> None:
        """Center the shared NSColorPanel on the active screen before showing it (item T)."""
        from AppKit import NSColorPanel

        _center_on_main_screen(NSColorPanel.sharedColorPanel())
        objc.super(_LabelColorWell, self).activate_(exclusive)

    @objc.python_method
    def set_space_uuid(self, uuid: str) -> None:
        """Bind the Space UUID this well edits."""
        self._space_uuid = uuid

    @objc.python_method
    def space_uuid(self) -> str:
        """Return the bound Space UUID (empty when unset)."""
        return self._space_uuid


class _DisplayNode:
    """Outline parent row: one physical display and its Spaces."""

    __slots__ = ("display", "spaces", "title")

    def __init__(self, display: Display | None, title: str, spaces: list[Space]) -> None:
        """Store the display (None for the orphan group), its title, and Spaces."""
        self.display = display
        self.title = title
        self.spaces = spaces

    @property
    def display_uuid(self) -> str | None:
        """Return the display UUID from the topology entry or from the first Space.

        Synthetic rows (created when topology discovery fails) have ``display=None``
        but their Spaces carry the real ``display_uuid`` — so the per-display overlay
        toggle works even when CGS/NSScreen topology is temporarily unavailable.
        """
        if self.display is not None:
            return self.display.uuid
        return self.spaces[0].display_uuid if self.spaces else None


class PrefsDataSource(NSObject):
    """``NSOutlineView`` data source + delegate backing the prefs tree.

    Lives at module scope (NSObject subclass) but builds nothing until the
    controller calls :meth:`set_nodes`; importing the module stays UI-free.
    """

    def init(self) -> PrefsDataSource | None:
        """Initialize with an empty tree and no store paths."""
        self = objc.super(PrefsDataSource, self).init()
        if self is None:
            return None
        self._nodes: list[_DisplayNode] = []
        self._labels: dict[str, Label] = {}
        self._ordinals: dict[int, int] = {}
        self._paths: store.StorePaths | None = None
        # Per-display overlay-disabled set (P feature).
        self._overlay_disabled: set[str] = set()
        # tag -> (kind, uuid) so an edited field maps back to a Space or a display.
        self._fields: dict[int, tuple[str, str]] = {}
        self._tag_counter: dict[str, int] = {}
        # The text each editable cell was shown with, so we commit ONLY on a real
        # change (view-based fields can fire controlTextDidEndEditing on teardown/
        # reload, which would otherwise persist the "Desktop N" placeholder).
        self._original: dict[int, str] = {}
        # Called (deferred, to avoid re-entrancy in field-editor teardown) after a
        # successful commit so the outline live-reverts cleared labels to "Desktop N"
        # instead of showing the stale text until the window is reopened (item U).
        self._on_commit: Callable[[], None] | None = None
        return self

    @objc.python_method
    def set_on_commit(self, callback: Callable[[], None]) -> None:
        """Wire the post-commit callback that refreshes the outline (item U)."""
        self._on_commit = callback

    @objc.python_method
    def set_nodes(
        self,
        nodes: list[_DisplayNode],
        labels: dict[str, Label],
        ordinals: dict[int, int],
        paths: store.StorePaths,
        overlay_disabled: set[str] | None = None,
    ) -> None:
        """Replace the tree contents and the store paths used for edits."""
        self._nodes = nodes
        self._labels = labels
        self._ordinals = ordinals
        self._paths = paths
        self._overlay_disabled = overlay_disabled if overlay_disabled is not None else set()
        self._fields.clear()
        self._tag_counter.clear()
        self._original.clear()

    # -- NSOutlineViewDataSource ------------------------------------------------

    def outlineView_numberOfChildrenOfItem_(self, _view: object, item: object) -> int:  # noqa: N802
        """Return the child count for the root (item is None) or a display node."""
        if item is None:
            return len(self._nodes)
        if isinstance(item, _DisplayNode):
            return len(item.spaces)
        return 0

    def outlineView_isItemExpandable_(self, _view: object, item: object) -> bool:  # noqa: N802
        """Display nodes are expandable; Space rows are leaves."""
        return isinstance(item, _DisplayNode)

    def outlineView_child_ofItem_(self, _view: object, index: int, item: object) -> object:  # noqa: N802
        """Return the index-th child of the root or a display node."""
        if item is None:
            return self._nodes[index]
        if isinstance(item, _DisplayNode):
            return item.spaces[index]
        return None

    # -- NSOutlineViewDelegate (view-based) -------------------------------------

    def outlineView_viewForTableColumn_item_(  # noqa: N802
        self, outline: object, column: object, item: object
    ) -> object:
        """Provide the cell view for a column/item pair (view-based outline).

        The selector MUST be ``outlineView:viewForTableColumn:item:`` (NOT
        ``byItem:``) or AppKit never enters view-based mode and every row renders
        blank. Each text cell is an ``NSTableCellView`` wrapping a text field (the
        canonical reusable pattern).
        """
        column_id = str(column.identifier())
        if isinstance(item, _DisplayNode):
            return self._display_view(outline, column_id, item)
        space = cast("Space", item)
        if column_id == _COL_UUID:
            return self._text_cell(
                outline,
                column_id,
                space.uuid or "(no UUID)",
                editable=False,
                commit=None,
                bold=False,
            )
        if column_id == _COL_COLOR:
            return self._color_cell(outline, space)
        if column_id == _COL_LABEL:
            ordinal = self._ordinals.get(id(space), 0)
            text = labeling.title_for(space, self._labels, ordinal, max_length=64)
            commit = ("space", space.uuid)
            return self._text_cell(
                outline, column_id, text, editable=True, commit=commit, bold=False
            )
        # Overlay column: only display rows carry a toggle; Space rows show nothing.
        if column_id == _COL_OVERLAY:
            return self._text_cell(outline, column_id, "", editable=False, commit=None, bold=False)
        now_text = "now" if space.is_current else ""
        return self._text_cell(
            outline, column_id, now_text, editable=False, commit=None, bold=False
        )

    @objc.python_method
    def _display_view(self, outline: object, column_id: str, node: _DisplayNode) -> object:
        """Cell for a display (parent) row: editable name + the display's UUID."""
        if column_id == _COL_LABEL:
            # A real display is renamable (commit -> store.set_display_label); the
            # orphan group (display is None) is a static, non-editable header.
            commit = ("display", node.display.uuid) if node.display is not None else None
            return self._text_cell(
                outline,
                column_id,
                node.title,
                editable=commit is not None,
                commit=commit,
                bold=True,
            )
        if column_id == _COL_UUID:
            uuid = node.display.uuid if node.display is not None else ""
            return self._text_cell(
                outline, column_id, uuid, editable=False, commit=None, bold=False
            )
        if column_id == _COL_OVERLAY:
            return self._overlay_cell(node)
        return self._text_cell(outline, column_id, "", editable=False, commit=None, bold=False)

    @objc.python_method
    def _overlay_cell(self, node: _DisplayNode) -> object:
        """Return a checkbox cell that toggles per-display overlay on/off (P feature).

        Disabled only when no display UUID is resolvable at all (extremely rare:
        a synthetic fallback group with no Spaces at all). Synthetic rows created
        when topology discovery is temporarily unavailable still expose a UUID via
        ``node.display_uuid`` (from the first Space's ``display_uuid``), so the
        toggle works even when NSScreen / CGS topology is unavailable.
        """
        from AppKit import NSButton, NSButtonTypeSwitch

        box = NSButton.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 70.0, 18.0))
        box.setButtonType_(NSButtonTypeSwitch)
        box.setTitle_("Overlay")
        uuid = node.display_uuid
        if uuid is None:
            box.setState_(NSControlStateValueOff)
            box.setEnabled_(False)
        else:
            enabled = uuid not in self._overlay_disabled
            box.setState_(_state(enabled))
            box.setTag_(self._tag_for(uuid))
            box.setTarget_(self)
            box.setAction_("toggleOverlay:")
        return box

    def toggleOverlay_(self, sender: object) -> None:  # noqa: N802
        """Persist an overlay on/off toggle for a display (P feature)."""
        if self._paths is None:
            log.warning("prefs has no store paths; overlay toggle dropped")
            return
        tag = int(sender.tag())
        # Recover the display UUID from the tag_counter (same tag registry as text fields)
        uuid = next((u for u, t in self._tag_counter.items() if t == tag), None)
        if uuid is None:
            return
        from AppKit import NSControlStateValueOff, NSControlStateValueOn

        enabled = sender.state() == NSControlStateValueOn
        try:
            store.set_display_overlay_enabled(self._paths, uuid, enabled)
            if enabled:
                self._overlay_disabled.discard(uuid)
            else:
                self._overlay_disabled.add(uuid)
        except (OSError, store.StoreError) as exc:
            log.warning("failed to set overlay for display %s: %s", uuid, exc)
            # Store write failed: revert the checkbox so the UI matches the
            # (unchanged) store state — never leave them diverged.
            sender.setState_(NSControlStateValueOff if enabled else NSControlStateValueOn)

    @objc.python_method
    def _text_cell(
        self,
        outline: object,
        column_id: str,
        text: str,
        *,
        editable: bool,
        commit: tuple[str, str] | None,
        bold: bool,
    ) -> object:
        """Return a reusable ``NSTableCellView`` text cell configured for this row.

        ``commit`` is ``(kind, uuid)`` -- ``("space"|"display", uuid)`` -- recorded so
        an edit maps back to the right store write; ``None`` for read-only cells.
        """
        cell = outline.makeViewWithIdentifier_owner_(column_id, self)
        if cell is None:
            cell = NSTableCellView.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 200.0, 20.0))
            cell.setIdentifier_(column_id)
            field = NSTextField.alloc().initWithFrame_(NSMakeRect(2.0, 1.0, 196.0, 18.0))
            field.setBordered_(False)
            field.setDrawsBackground_(False)
            field.setAutoresizingMask_(NSViewWidthSizable)
            cell.addSubview_(field)
            cell.setTextField_(field)
        field = cell.textField()
        field.setStringValue_(text)
        field.setEditable_(editable)
        field.setSelectable_(editable)
        field.setFont_(
            NSFont.boldSystemFontOfSize_(13.0) if bold else NSFont.systemFontOfSize_(13.0)
        )
        if editable and commit is not None:
            field.setDelegate_(self)
            tag = self._tag_for(commit[1])
            field.setTag_(tag)
            self._fields[tag] = commit
            self._original[tag] = text  # remember the shown text to detect real edits
        else:
            field.setDelegate_(None)
        return cell

    @objc.python_method
    def _color_cell(self, _outline: object, space: Space) -> object:
        """Return a color-well cell that writes the Space's color tag on change.

        A fresh :class:`_LabelColorWell` per row carries the Space UUID, so the
        change handler knows which Space to update. Color is a per-label attribute
        (DECISIONS 9.8), so the well is enabled only for an already-labeled Space.
        """
        well = _LabelColorWell.alloc().initWithFrame_(NSMakeRect(0.0, 0.0, 44.0, 18.0))
        label = self._labels.get(space.uuid)
        # A notes-only entry (Label.text == "") is unlabeled — color is a per-label
        # attribute (DECISIONS 9.8/9.10), so treat empty text the same as no entry.
        is_labeled = label is not None and bool(label.text)
        color = label.color if (label is not None and is_labeled) else None
        parsed = _color_from_hex(color) if color else None
        well.setColor_(parsed if parsed is not None else NSColor.controlBackgroundColor())
        if is_labeled:
            well.set_space_uuid(space.uuid)
            well.setTarget_(self)
            well.setAction_("colorChanged:")
        else:
            # Unlabeled (incl. notes-only): no label to attach a color to -> disabled.
            well.setEnabled_(False)
        return well

    def colorChanged_(self, sender: object) -> None:  # noqa: N802
        """Persist a newly picked color for the well's Space (per-label tag)."""
        uuid = sender.space_uuid()
        if not uuid:
            return
        self._commit_color(str(uuid), _hex_from_color(sender.color()))

    @objc.python_method
    def _commit_color(self, uuid: str, hex_color: str | None) -> None:
        """Save ``hex_color`` onto the Space's existing label via the locked store."""
        if self._paths is None or hex_color is None:
            return
        existing = self._labels.get(uuid)
        if existing is None:  # color is a label attribute; nothing to attach it to
            log.debug("color picked for unlabeled space %s ignored", uuid)
            return
        try:
            self._labels[uuid] = store.set_label(self._paths, uuid, existing.text, color=hex_color)
        except (OSError, store.StoreError) as exc:
            log.warning("failed to save color for space %s: %s", uuid, exc)

    @objc.python_method
    def _tag_for(self, uuid: str) -> int:
        """Return a stable small int tag for a UUID (maps a field back to a Space)."""
        return self._tag_counter.setdefault(uuid, len(self._tag_counter) + 1)

    # -- NSTextFieldDelegate ----------------------------------------------------

    def controlTextDidEndEditing_(self, notification: object) -> None:  # noqa: N802
        """Commit a label edit on Return or focus-loss (Esc cancels natively).

        Commits ONLY when the text actually changed from what the cell was shown
        with -- so a teardown/reload that re-ends editing never persists the
        unchanged value (e.g. the "Desktop N" placeholder) for rows just viewed.
        """
        field = notification.object()
        tag = int(field.tag())
        commit = self._fields.get(tag)
        if commit is None:
            return
        new_text = str(field.stringValue()).strip()
        if new_text == self._original.get(tag, new_text):
            return  # unchanged -> no write
        kind, uuid = commit
        self._commit(kind, uuid, new_text)

    @objc.python_method
    def _commit(self, kind: str, uuid: str, text: str) -> None:
        """Persist an edited Space label or display name through the locked store."""
        if self._paths is None:
            log.warning("prefs has no store paths; edit dropped")
            return
        try:
            if kind == "display":
                store.set_display_label(self._paths, uuid, text)
            elif text:
                store.set_label(self._paths, uuid, text)
            else:
                store.clear_label(self._paths, uuid)
        except (OSError, store.StoreError) as exc:
            log.warning("failed to commit %s edit for %s: %s", kind, uuid, exc)
            return
        # Defer the outline refresh to the next run-loop cycle: calling reloadData()
        # directly from inside controlTextDidEndEditing_ (while the field editor is
        # still tearing down) is an AppKit re-entrancy hazard (item U).
        # NSOperationQueue.mainQueue() guarantees the block runs after the current
        # call stack unwinds — a harder guarantee than NSTimer(0) which can fire in
        # the same run-loop turn on some paths.
        if self._on_commit is not None:
            cb = self._on_commit
            from Foundation import NSOperationQueue

            NSOperationQueue.mainQueue().addOperationWithBlock_(lambda: cb())


class PreferencesWindow:
    """The Spaces/labels editor and prune action (one shared instance)."""

    def __init__(self, config_path: Path | None = None) -> None:
        """Record the config path; build no AppKit objects yet (import-safe).

        Args:
            config_path: Optional alternate ``config.json`` path passed to
                :meth:`spacelabel.store.StorePaths.resolve`.
        """
        self._config_path = config_path
        self._window: object | None = None
        self._outline: NSOutlineView | None = None
        self._data_source: PrefsDataSource | None = None
        self._target: _PruneTarget | None = None
        self._controls: _ControlsTarget | None = None
        self._cts_button: object | None = None  # NSButton for "Click to switch" state sync

    def show(self) -> None:
        """Build the window, refresh, center on the active screen, and order front (item T).

        Centers on every open (not just first build) so the window follows the
        menu-bar-owning screen after a monitor layout change. Activate immediately
        before ``makeKeyAndOrderFront_`` so the window is both key and front.
        """
        if self._window is None:
            self._build()
        self.refresh()
        if self._window is not None:
            _center_on_main_screen(self._window)  # follow the active screen (item T)
            # Re-centre a stranded color panel: if the panel is already open on another
            # display, reopening Preferences must move it to the current active screen.
            from AppKit import NSApplication, NSColorPanel

            panel = NSColorPanel.sharedColorPanel()
            if panel.isVisible():
                _center_on_main_screen(panel)
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            self._window.makeKeyAndOrderFront_(None)

    def _build(self) -> None:
        """Construct the window: settings strip (top), Spaces outline, prune button."""
        rect = NSMakeRect(0.0, 0.0, 720.0, 508.0)
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False
        )
        window.setTitle_("spacelabel - Preferences")
        window.setReleasedWhenClosed_(False)

        outline = NSOutlineView.alloc().init()
        outline.setAllowsColumnReordering_(False)
        outline.addTableColumn_(
            self._make_column(_COL_LABEL, "Space / Label", 220.0, editable=True)
        )
        outline.addTableColumn_(self._make_column(_COL_UUID, "UUID", 300.0, editable=False))
        outline.addTableColumn_(self._make_column(_COL_COLOR, "Color", 60.0, editable=False))
        outline.addTableColumn_(self._make_column(_COL_NOW, "Now", 50.0, editable=False))
        outline.addTableColumn_(self._make_column(_COL_OVERLAY, "Overlay", 80.0, editable=False))
        outline.setOutlineTableColumn_(outline.tableColumnWithIdentifier_(_COL_LABEL))

        data_source = PrefsDataSource.alloc().init()
        data_source.set_on_commit(self.refresh)  # live-revert cleared labels (item U)
        outline.setDataSource_(data_source)
        outline.setDelegate_(data_source)

        # Height 350 (was 380): 30 pt freed for the click-to-switch row 3 (item J).
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0.0, 40.0, 720.0, 350.0))
        scroll.setDocumentView_(outline)
        scroll.setHasVerticalScroller_(True)
        scroll.setAutoresizingMask_(18)  # width | height resizable

        prune = NSButton.alloc().initWithFrame_(NSMakeRect(560.0, 8.0, 150.0, 28.0))
        prune.setTitle_("Prune orphans...")
        prune.setBezelStyle_(1)  # NSBezelStyleRounded
        prune.setTarget_(self._button_target())
        prune.setAction_("pruneOrphans:")

        content = window.contentView()
        content.addSubview_(scroll)
        content.addSubview_(prune)
        self._build_settings(content)

        self._window = window
        self._outline = outline
        self._data_source = data_source

    def _build_settings(self, content: object) -> None:
        """Build the top settings strip: mode checkboxes + position/corner popups.

        Each control writes its config key on change via :meth:`write_config`; a
        running agent reloads live. Numeric params (durations/sizes/margins) stay
        CLI-only (see ``spacelabel config set --help``).
        """
        config = store.load_config(store.StorePaths.resolve(self._config_path))
        target = self._controls_target()

        # Row 1: mode checkboxes, laid left-to-right by their fitted widths.
        x = 16.0
        for tag, key, title in _MODE_CHECKBOXES:
            target.register_tag(tag, key)
            box = NSButton.alloc().initWithFrame_(NSMakeRect(x, 466.0, 130.0, 20.0))
            box.setButtonType_(NSButtonTypeSwitch)
            box.setTitle_(title)
            box.setState_(_state(bool(store.get_config_value(config, key))))
            box.setTag_(tag)
            box.setTarget_(target)
            box.setAction_("toggleCheckbox:")
            box.sizeToFit()
            box.setFrameOrigin_((x, 466.0))
            content.addSubview_(box)
            x += float(box.frame().size.width) + 14.0

        # Row 2: HUD position + overlay corner popups (the nine anchors).
        self._add_label(content, "HUD position:", 16.0, 432.0, 96.0)
        hud_pos = self._make_popup(
            116.0, str(store.get_config_value(config, "hud.position")), target, "changeHudPosition:"
        )
        content.addSubview_(hud_pos)
        self._add_label(content, "Overlay corner:", 300.0, 432.0, 104.0)
        ovl_corner = self._make_popup(
            408.0,
            str(store.get_config_value(config, "overlay.corner")),
            target,
            "changeOverlayCorner:",
        )
        content.addSubview_(ovl_corner)

        # Row 3: "Click to switch" toggle (item J; effective only when buttons row is on).
        # Placed at y=396, 6 pt above the scroll view top (390 = 40 + 350).
        # Tag 7: tags 1-6 are used by _MODE_CHECKBOXES (6 = overlay.hide_on_unlabeled).
        cts_tag = 7
        target.register_tag(cts_tag, "menubar.click_to_switch")
        cts = NSButton.alloc().initWithFrame_(NSMakeRect(16.0, 396.0, 130.0, 20.0))
        cts.setButtonType_(NSButtonTypeSwitch)
        cts.setTitle_("Click to switch")
        cts.setState_(_state(bool(store.get_config_value(config, "menubar.click_to_switch"))))
        cts.setTag_(cts_tag)
        cts.setTarget_(target)
        cts.setAction_("toggleCheckbox:")
        cts.sizeToFit()
        cts.setFrameOrigin_((16.0, 396.0))
        content.addSubview_(cts)
        self._cts_button = cts  # kept for state sync after dropdown toggles (item J)

    def _add_label(self, content: object, text: str, x: float, y: float, width: float) -> None:
        """Add a static text label to the settings strip."""
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, width, 20.0))
        field.setStringValue_(text)
        field.setBordered_(False)
        field.setEditable_(False)
        field.setSelectable_(False)
        field.setDrawsBackground_(False)
        content.addSubview_(field)

    def _make_popup(self, x: float, selected: str, target: object, action: str) -> object:
        """Build a nine-anchor popup, preselecting ``selected``, wired to ``action``."""
        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(x, 430.0, 150.0, 26.0), False
        )
        popup.addItemsWithTitles_(list(_ANCHOR_ORDER))
        if selected in _ANCHOR_ORDER:
            popup.selectItemWithTitle_(selected)
        popup.setTarget_(target)
        popup.setAction_(action)
        return popup

    def _controls_target(self) -> _ControlsTarget:
        """Return (building once) the settings-controls action bridge."""
        if self._controls is None:
            self._controls = _ControlsTarget.alloc().initWithController_(self)
        return self._controls

    def write_config(self, key: str, value: str) -> None:
        """Persist one config key from a settings control (logged, never raises)."""
        paths = store.StorePaths.resolve(self._config_path)
        try:
            store.set_config_value(paths, key, value)
        except (OSError, store.StoreError) as exc:
            log.warning("could not set %s=%s from prefs: %s", key, value, exc)

    def sync_cts_state(self) -> None:
        """Sync the click-to-switch checkbox to the current config (item J, P3).

        Called by the dropdown ``toggleClickToSwitch_`` action so an open Preferences
        window reflects the new state without requiring a close/reopen.
        """
        if self._cts_button is None:
            return
        paths = store.StorePaths.resolve(self._config_path)
        config = store.load_config(paths)
        self._cts_button.setState_(
            _state(bool(store.get_config_value(config, "menubar.click_to_switch")))
        )

    def _make_column(
        self, identifier: str, title: str, width: float, *, editable: bool
    ) -> NSTableColumn:
        """Build one outline column with a title and width."""
        column = NSTableColumn.alloc().initWithIdentifier_(identifier)
        column.setWidth_(width)
        header = column.headerCell()
        header.setStringValue_(title)
        column.setEditable_(editable)
        return column

    def _button_target(self) -> _PruneTarget:
        """Return the object handling the prune button action (a small bridge)."""
        if self._target is None:
            self._target = _PruneTarget.alloc().initWithController_(self)
        return self._target

    def refresh(self) -> None:
        """Reload Spaces, labels, and topology into the outline tree."""
        if self._outline is None or self._data_source is None:
            return
        nodes, labels, ordinals, paths, overlay_disabled = self._load_tree()
        self._data_source.set_nodes(nodes, labels, ordinals, paths, overlay_disabled)
        self._outline.reloadData()
        # expandItem_(None) is a no-op; expand each expandable row explicitly so the
        # display nodes show their Spaces. numberOfRows grows as rows expand.
        row = 0
        while row < self._outline.numberOfRows():
            item = self._outline.itemAtRow_(row)
            if item is not None and self._outline.isExpandable_(item):
                self._outline.expandItem_(item)
            row += 1

    @objc.python_method
    def _load_tree(
        self,
    ) -> tuple[list[_DisplayNode], dict[str, Label], dict[int, int], store.StorePaths, set[str]]:
        """Build the display->Spaces tree plus an orphan group from live reads."""
        paths = store.StorePaths.resolve(self._config_path)
        labels = store.load_labels(paths)
        display_labels = store.load_display_labels(paths)
        overlay_disabled = store.load_display_overlay_disabled(paths)
        # Number over the FULL enumeration (incl. each display's default unlabelable
        # Space) so the "Desktop N" shown here counts every desktop and matches the
        # menu-bar pill + the switch path -- the one ordinal source of truth (item V,
        # labeling.assign_ordinals). Rows/orphans still use labelable Spaces only: the
        # default Space can't be labeled, so it is counted but not shown as a row.
        spaces = self._read_spaces(include_unlabelable=True)
        ordinals = labeling.assign_ordinals(spaces)
        labelable = [space for space in spaces if space.uuid]
        topology = self._read_topology()
        nodes = self._group_by_display(labelable, topology, display_labels)
        orphan_uuids = labeling.find_orphans(labels, [s.uuid for s in labelable])
        if orphan_uuids:
            log.debug("%d orphaned labels present", len(orphan_uuids))
        return nodes, labels, ordinals, paths, overlay_disabled

    @objc.python_method
    def _read_spaces(self, *, include_unlabelable: bool = False) -> list[Space]:
        """Read Spaces, recovering with an empty list on failure.

        ``include_unlabelable=True`` also returns each display's default ``uuid==""``
        Space so the ordinal count matches macOS / the pills (item V); the default
        (labelable-only) is what prune uses for the live set.
        """
        from spacelabel.platform import cgs

        try:
            return cgs.enumerate_spaces(include_unlabelable=include_unlabelable)
        except cgs.CGSUnavailableError as exc:
            log.warning("CGS unavailable in prefs: %s", exc)
            return []

    @objc.python_method
    def _read_topology(self) -> list[Display]:
        """Discover displays, recovering with an empty list on failure."""
        from spacelabel.platform import displays

        try:
            return displays.discover_topology()
        except (RuntimeError, OSError) as exc:
            log.warning("topology discovery failed in prefs: %s", exc)
            return []

    @objc.python_method
    def _group_by_display(
        self, spaces: list[Space], topology: list[Display], display_labels: dict[str, str]
    ) -> list[_DisplayNode]:
        """Group Spaces under their display node, in topology order.

        Node titles use the user's custom display name when set (else the friendly
        name); the title is what the editable display row is prefilled with.
        """
        from spacelabel.platform import displays

        by_uuid: dict[str, list[Space]] = {}
        for space in spaces:
            by_uuid.setdefault(space.display_uuid, []).append(space)
        nodes: list[_DisplayNode] = []
        seen: set[str] = set()
        for display in topology:
            seen.add(display.uuid)
            title = displays.resolved_name(display, display_labels)
            nodes.append(_DisplayNode(display, title, by_uuid.get(display.uuid, [])))
        # Spaces on displays not in the topology (defensive) get a fallback group.
        for display_uuid, group in by_uuid.items():
            if display_uuid not in seen:
                nodes.append(_DisplayNode(None, f"Display {display_uuid[:8]}", group))
        return nodes

    @objc.python_method
    def prune_orphans(self) -> None:
        """Prune labels whose Space UUID is absent from the live set."""
        paths = store.StorePaths.resolve(self._config_path)
        spaces = self._read_spaces()
        live = {space.uuid for space in spaces}
        if not live:
            log.warning("no live Spaces read; refusing to prune")
            return
        try:
            removed = store.prune_labels(paths, live)
        except (OSError, store.StoreError) as exc:
            log.warning("prune failed: %s", exc)
            return
        log.info("pruned %d orphaned labels", len(removed))
        self.refresh()


class _PruneTarget(NSObject):
    """Tiny selector bridge so the prune button can call back into Python."""

    def initWithController_(  # noqa: N802
        self, controller: PreferencesWindow
    ) -> _PruneTarget | None:
        """Retain the owning :class:`PreferencesWindow`."""
        self = objc.super(_PruneTarget, self).init()
        if self is None:
            return None
        self._controller: PreferencesWindow = controller
        return self

    def pruneOrphans_(self, _sender: object) -> None:  # noqa: N802
        """Forward the button click to the controller's prune handler."""
        self._controller.prune_orphans()


class _ControlsTarget(NSObject):
    """Selector bridge for the settings-strip controls (checkboxes + popups)."""

    def initWithController_(  # noqa: N802
        self, controller: PreferencesWindow
    ) -> _ControlsTarget | None:
        """Retain the owning window and an empty checkbox tag->key map."""
        self = objc.super(_ControlsTarget, self).init()
        if self is None:
            return None
        self._controller: PreferencesWindow = controller
        self._tag_keys: dict[int, str] = {}
        return self

    @objc.python_method
    def register_tag(self, tag: int, key: str) -> None:
        """Map a checkbox tag to its dotted config key."""
        self._tag_keys[tag] = key

    def toggleCheckbox_(self, sender: object) -> None:  # noqa: N802
        """Write the bool config key bound to the toggled checkbox's tag."""
        key = self._tag_keys.get(int(sender.tag()))
        if key is None:
            return
        on = sender.state() == NSControlStateValueOn
        self._controller.write_config(key, "true" if on else "false")

    def changeHudPosition_(self, sender: object) -> None:  # noqa: N802
        """Write ``hud.position`` from the popup selection."""
        title = sender.titleOfSelectedItem()
        if title:
            self._controller.write_config("hud.position", str(title))

    def changeOverlayCorner_(self, sender: object) -> None:  # noqa: N802
        """Write ``overlay.corner`` from the popup selection."""
        title = sender.titleOfSelectedItem()
        if title:
            self._controller.write_config("overlay.corner", str(title))
