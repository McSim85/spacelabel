"""Preferences window — a view-based ``NSTableView`` (DESIGN.md §6 / DECISIONS.md 2.4).

The data is naturally tabular and editable (UUID, label, last display), so a
view-based ``NSTableView`` (dataSource + delegate, editable label column) scales
to N Spaces; WebView is rejected. This window is one of the two label writers
(the CLI is the other), so edits go through the same locked store path.
"""

from __future__ import annotations

import logging

__all__ = ["PreferencesWindow"]

log = logging.getLogger(__name__)


class PreferencesWindow:
    """The Spaces/labels editor and mode toggles."""

    def __init__(self) -> None:
        """Build the window and its table view (lazily; one shared instance)."""
        # TODO(phase-4): NSWindow + view-based NSTableView; mode-toggle controls.
        raise NotImplementedError

    def show(self) -> None:
        """Bring the preferences window to the front."""
        # TODO(phase-4): order front and focus.
        raise NotImplementedError
