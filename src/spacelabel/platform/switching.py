"""Synthetic Space switching via the "Switch to Desktop N" Mission Control shortcut.

Space *switching* is the one operation behind the SIP/Dock wall (DECISIONS.md 9.5,
docs/UI.md §2.4): there is no public or SIP-free private API that sets the active
Space. The only supported path is the user-enabled per-desktop "Switch to Desktop
N" Mission Control keyboard shortcut, synthesized with ``CGEventPost`` once the
user grants Accessibility.

We do **not** assume the chord is Ctrl+N: we read the *actual* binding (virtual key
code + modifier flags) from ``com.apple.symbolichotkeys`` and post exactly that,
and we refuse -- with a specific, logged reason -- when the shortcut is
absent/disabled or Accessibility is denied (never a silent no-op).

Empirically grounded on the reference machine (macOS 26.5.1, build 25F80):
symbolic-hotkey id ``118`` is "Switch to Desktop 1" with the default-but-disabled
binding ``[asciiChar=65535, keyCode=18 (kVK_ANSI_1), modifiers=0x40000 (Control)]``
(i.e. Ctrl+1); ids ``119``/``120`` are Desktop 2/3, so ``id == 117 + ordinal``. The
symbolic-hotkey modifier bits equal the ``kCGEventFlagMask*`` bits (Control ==
0x40000 == ``kCGEventFlagMaskControl``), so they pass straight to ``CGEventSetFlags``.

The pure helpers (:func:`symbolic_hotkey_id`, :func:`parse_desktop_binding`) take
plain data and are unit-tested without a WindowServer; the PyObjC/Quartz calls are
feature-detected and bound lazily, exactly like the CGS and ColorSync read paths.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "HotkeyReadError",
    "KeyBinding",
    "accessibility_trusted",
    "load_symbolic_hotkeys",
    "parse_desktop_binding",
    "post_switch",
    "symbolic_hotkey_id",
]

log = logging.getLogger(__name__)


class HotkeyReadError(Exception):
    """Raised when the ``AppleSymbolicHotKeys`` preference cannot be read (system/bridge failure).

    Distinct from "no shortcuts configured" (which returns ``{}``) so callers can
    surface an accurate error message instead of a misleading "shortcut not enabled" hint.
    """


#: Symbolic-hotkey id of "Switch to Desktop 1"; "Switch to Desktop N" == base + N-1.
#: Verified live on macOS 26.5.1 (ids 118/119/120 == Desktop 1/2/3). Phase-6 must
#: confirm the contiguity holds past Desktop 3 (ids 121+ are absent until the user
#: enables the shortcuts, at which point macOS materializes the entries).
_DESKTOP_HOTKEY_BASE_ID = 118

#: ``com.apple.symbolichotkeys`` stores modifier flags as ``kCGEventFlagMask*`` bit
#: values (verified: Control == 0x40000 == ``kCGEventFlagMaskControl``), so they are
#: passed verbatim to ``CGEventSetFlags``. All bits are preserved to honor non-standard
#: chords a user may configure.

#: ``parameters`` layout in a symbolic-hotkey ``value`` dict: the synthetic event is
#: posted by key code, so ``asciiChar`` (index 0) is ignored.
_PARAM_KEY_CODE = 1
_PARAM_MODIFIERS = 2

#: CoreFoundation key for the Accessibility "prompt the user" option -- the literal
#: value of ``kAXTrustedCheckOptionPrompt`` (the constant symbol is not exported to
#: PyObjC on Tahoe, so the string is used directly).
_AX_PROMPT_OPTION = "AXTrustedCheckOptionPrompt"

#: ``com.apple.symbolichotkeys`` preference domain + the top-level key.
_SYMBOLIC_HOTKEYS_DOMAIN = "com.apple.symbolichotkeys"
_SYMBOLIC_HOTKEYS_KEY = "AppleSymbolicHotKeys"

#: HIServices holds the AX trust functions. On Tahoe the ApplicationServices/
#: HIServices bundle has no on-disk Mach-O and will not load by identifier, but
#: ``objc.loadBundle`` by the framework PATH resolves both functions (verified live).
_HISERVICES_PATHS = (
    "/System/Library/Frameworks/ApplicationServices.framework/Frameworks/HIServices.framework",
    "/System/Library/Frameworks/ApplicationServices.framework",
)

#: Memoized AX function map + a "tried to load" flag (cache a miss too, so a host
#: lacking the symbol is not probed on every click).
_AX_FUNCS: dict[str, Any] = {}
_AX_LOADED = False


@dataclass(frozen=True, slots=True)
class KeyBinding:
    """A keyboard chord: a virtual key code plus CGEvent modifier flags."""

    key_code: int
    modifier_flags: int


def symbolic_hotkey_id(ordinal: int) -> int:
    """Return the ``com.apple.symbolichotkeys`` id for "Switch to Desktop ``ordinal``".

    ``ordinal`` is the 1-based "Desktop N" number (Desktop 1 -> id 118). See the
    module docstring for the empirical grounding and the Phase-6 caveat past
    Desktop 3.
    """
    return _DESKTOP_HOTKEY_BASE_ID + ordinal - 1


def parse_desktop_binding(hotkeys: Mapping[str, object], ordinal: int) -> KeyBinding | None:
    """Resolve the *enabled* "Switch to Desktop ``ordinal``" chord, or ``None``.

    ``hotkeys`` is the ``AppleSymbolicHotKeys`` mapping (string id -> entry, as
    returned by :func:`load_symbolic_hotkeys`). A :class:`KeyBinding` is returned
    only when the entry exists AND is ``enabled`` AND carries a parseable
    ``value.parameters`` ``[asciiChar, keyCode, modifierFlags]`` array; otherwise
    ``None`` (the shortcut is unset/disabled, so the caller disables the action with
    a visible reason rather than silently no-op). The ``asciiChar`` element is
    ignored -- the synthetic event is posted by key code.

    Pure (no I/O / no PyObjC): the prime unit-test target.
    """
    if ordinal < 1:
        return None
    entry = hotkeys.get(str(symbolic_hotkey_id(ordinal)))
    if not isinstance(entry, Mapping):
        return None
    if not entry.get("enabled"):
        return None
    value = entry.get("value")
    if not isinstance(value, Mapping):
        return None
    params = value.get("parameters")
    if (
        not isinstance(params, Sequence)
        or isinstance(params, (str, bytes))
        or len(params) <= _PARAM_MODIFIERS
    ):
        log.warning("Switch to Desktop %d has no usable parameters: %r", ordinal, params)
        return None
    try:
        key_code = int(params[_PARAM_KEY_CODE])
        modifier_flags = int(params[_PARAM_MODIFIERS])
    except (TypeError, ValueError) as exc:
        log.warning("malformed parameters for Switch to Desktop %d: %s", ordinal, exc)
        return None
    return KeyBinding(key_code=key_code, modifier_flags=modifier_flags)


def load_symbolic_hotkeys() -> dict[str, object]:
    """Read the live ``AppleSymbolicHotKeys`` map via CFPreferences (string id -> entry).

    Reads through ``cfprefsd`` (CFPreferences) so a just-changed System Settings
    binding is seen without the stale-file lag the on-disk plist suffers (the same
    "prefer the live source" ethos as DECISIONS.md 3.4 for the spaces plist).
    Returns an empty dict when the domain/key is absent or not a mapping (logged) --
    no shortcuts are configured. Raises :exc:`HotkeyReadError` on genuine system
    failures (Foundation unavailable, bridge error) so callers can surface an accurate
    error message rather than a misleading "shortcut not enabled" hint.
    """
    try:
        from Foundation import CFPreferencesCopyAppValue
    except ImportError as exc:
        raise HotkeyReadError(f"CFPreferences unavailable: {exc}") from exc
    try:
        raw = CFPreferencesCopyAppValue(_SYMBOLIC_HOTKEYS_KEY, _SYMBOLIC_HOTKEYS_DOMAIN)
    except Exception as exc:
        raise HotkeyReadError(f"CFPreferences read failed: {exc}") from exc
    if raw is None:
        log.info("no %s set; no Switch-to-Desktop shortcuts configured", _SYMBOLIC_HOTKEYS_KEY)
        return {}
    if not isinstance(raw, Mapping):
        log.warning("%s is not a mapping (%s)", _SYMBOLIC_HOTKEYS_KEY, type(raw).__name__)
        return {}
    # Dict keys are the numeric ids as strings; normalize so the pure parser can
    # look them up by ``str(id)`` regardless of the bridged key type.
    return {str(key): value for key, value in raw.items()}


def accessibility_trusted(*, prompt: bool = False) -> bool:
    """Return whether this process is trusted for Accessibility (``AXIsProcessTrusted``).

    With ``prompt=True`` uses ``AXIsProcessTrustedWithOptions`` so macOS shows the
    one-time "grant Accessibility" dialog and registers the agent in the list; with
    ``prompt=False`` it is a silent check. The AX functions live in HIServices (not
    wrapped by PyObjC on Tahoe), so they are bound via ``objc.loadBundle`` by path --
    the same feature-detected loader pattern as the CGS / ColorSync reads. Returns
    ``False`` (logged) if the symbol cannot be bound.
    """
    funcs = _load_ax_functions()
    if prompt:
        with_options = funcs.get("AXIsProcessTrustedWithOptions")
        if with_options is not None:
            return bool(with_options({_AX_PROMPT_OPTION: True}))
        log.debug("AXIsProcessTrustedWithOptions unavailable; falling back to silent check")
    is_trusted = funcs.get("AXIsProcessTrusted")
    if is_trusted is None:
        log.warning("AXIsProcessTrusted unavailable; cannot confirm Accessibility permission")
        return False
    return bool(is_trusted())


def post_switch(binding: KeyBinding) -> bool:
    """Post the synthetic key-down/up pair for ``binding`` via ``CGEventPost``.

    Returns ``True`` once the events are posted. This is best-effort: a posted event
    may still be dropped (e.g. Accessibility revoked between the check and the post),
    which is acceptable for an opt-in, documented feature (DECISIONS.md 9.5 §e) --
    the caller has already verified the prerequisites. Posting requires Accessibility.
    Feature-detected: returns ``False`` (logged) if the Quartz CGEvent symbols are
    unavailable. Posted at the HID tap so the WindowServer's hotkey dispatch (which
    owns the Mission Control shortcut) sees it as genuine input.
    """
    try:
        import Quartz
    except ImportError as exc:
        log.warning("Quartz unavailable; cannot post switch event: %s", exc)
        return False
    try:
        key_down = Quartz.CGEventCreateKeyboardEvent(None, binding.key_code, True)
        key_up = Quartz.CGEventCreateKeyboardEvent(None, binding.key_code, False)
        if key_down is None or key_up is None:
            log.warning("CGEventCreateKeyboardEvent returned nil for key code %d", binding.key_code)
            return False
        Quartz.CGEventSetFlags(key_down, binding.modifier_flags)
        Quartz.CGEventSetFlags(key_up, binding.modifier_flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, key_down)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, key_up)
    except Exception as exc:
        log.warning("failed to post switch key event: %s", exc)
        return False
    log.debug("posted key code %d with flags 0x%x", binding.key_code, binding.modifier_flags)
    return True


def _load_ax_functions() -> dict[str, Any]:
    """Bind ``AXIsProcessTrusted[WithOptions]`` from HIServices once (feature-detected).

    On Tahoe the ApplicationServices/HIServices bundle is not loadable by identifier
    (no on-disk Mach-O), but ``objc.loadBundle`` by the HIServices framework PATH
    resolves both AX functions (verified live). Cached either way, including a miss.
    """
    global _AX_LOADED
    if _AX_LOADED:
        return _AX_FUNCS
    _AX_LOADED = True
    try:
        import objc
    except ImportError as exc:
        log.warning("objc unavailable; cannot bind Accessibility functions: %s", exc)
        return _AX_FUNCS
    # Boolean (b"Z") return; AXIsProcessTrustedWithOptions takes a CFDictionaryRef
    # (auto-bridged from a Python dict).
    specs = [("AXIsProcessTrusted", b"Z"), ("AXIsProcessTrustedWithOptions", b"Z@")]
    for path in _HISERVICES_PATHS:
        try:
            bundle = objc.loadBundle("HIServices", {}, bundle_path=path)
            objc.loadBundleFunctions(bundle, _AX_FUNCS, specs)
        except Exception as exc:
            log.debug("could not load HIServices bundle at %s: %s", path, exc)
            continue
        if "AXIsProcessTrusted" in _AX_FUNCS:
            break
    if "AXIsProcessTrusted" not in _AX_FUNCS:
        log.warning("AXIsProcessTrusted could not be bound from HIServices")
    return _AX_FUNCS
