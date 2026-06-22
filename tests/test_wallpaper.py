"""Wallpaper mode: restart-robust originals + TTL cache eviction (DECISIONS.md §7).

GUI/NSImage compositing stays import-smoke + manual (docs/TESTING.md); these tests
drive the pure path/persistence/eviction logic with the two PyObjC-touching seams
(``_display_key`` / ``_current_desktop_path`` / ``_connected_composite_keys``) monkeypatched.
The cache dir is a subdir so source wallpaper files live *outside* it (else
``_is_ours`` would treat them as our own composites).
"""

from __future__ import annotations

import fcntl
import json
import os
import time

import pytest

from spacelabel.agent.wallpaper import WallpaperRenderer, _is_managed_png, _select_evictions

# Canonical (uppercase 8-4-4-4-12 hex) display UUIDs, the shape `displays.display_uuid`
# emits and `_is_managed_png` accepts for `original-<uuid>.png`.
_UUID_A = "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"
_UUID_C = "CCCCCCCC-CCCC-CCCC-CCCC-CCCCCCCCCCCC"
_UUID_X = "12345678-90AB-CDEF-1234-567890ABCDEF"
_UUID_Z = "DEADBEEF-DEAD-BEEF-DEAD-BEEFDEADBEEF"


@pytest.fixture
def cache(tmp_path):
    """Return a cache dir nested under tmp_path (so sources can sit outside it)."""
    path = tmp_path / "cache"
    path.mkdir()
    return path


@pytest.fixture
def screen():
    """Return an opaque stand-in -- the patched seams ignore the real NSScreen."""
    return object()


def _patch_seams(monkeypatch, *, key="UUID-A", current=None):
    """Patch the display-key / screen-key / current-desktop-path seams.

    ``key`` is the UUID returned by ``_display_key`` (``None`` to simulate an
    unresolvable UUID); the in-memory session key then derives from ``_screen_key``,
    stubbed here to a stable ``display-1`` so a bare ``object()`` screen works.
    """
    holder = {"key": key, "current": current}
    monkeypatch.setattr(WallpaperRenderer, "_display_key", lambda self, s: holder["key"])
    monkeypatch.setattr(WallpaperRenderer, "_screen_key", lambda self, s: "display-1")
    monkeypatch.setattr(
        WallpaperRenderer, "_current_desktop_path", lambda self, s: holder["current"]
    )
    return holder


# --- capture + user-change detection ---------------------------------------


def test_load_originals_missing_is_empty(cache):
    r = WallpaperRenderer(cache_dir=cache)
    assert r._persisted == {}
    assert r._originals == {}


def test_corrupt_originals_json_recovers_to_empty(cache):
    (cache / "originals.json").write_text("{not json")
    assert WallpaperRenderer(cache_dir=cache)._persisted == {}


def test_is_ours_uses_path_components_not_string_prefix(tmp_path):
    # A sibling dir sharing the textual prefix must NOT count as our cache.
    cache = tmp_path / "wallpaper"
    cache.mkdir()
    r = WallpaperRenderer(cache_dir=cache)
    assert r._is_ours(str(cache / "display-1.png"))
    assert not r._is_ours(str(tmp_path / "wallpaper-old" / "foo.jpg"))
    assert not r._is_ours("/Users/me/Pictures/wallpaper.jpg")


def test_capture_real_original_persists_path_and_copy(tmp_path, cache, monkeypatch, screen):
    src = tmp_path / "wall.jpg"  # outside the cache
    src.write_bytes(b"original-bytes")
    _patch_seams(monkeypatch, current=str(src))
    r = WallpaperRenderer(cache_dir=cache)

    base = r._base_image_path(screen)

    assert base == str(src)
    # in-memory remembered under BOTH the display id and the UUID (survives churn of
    # either identifier); the disk map is keyed by the stable UUID only.
    assert r._originals == {"display-1": str(src), "UUID-A": str(src)}
    assert r._persisted == {"UUID-A": str(src)}
    assert json.loads((cache / "originals.json").read_text()) == {"UUID-A": str(src)}
    assert (cache / "original-UUID-A.png").read_bytes() == b"original-bytes"


def test_user_change_refreshes_path_and_copy(tmp_path, cache, monkeypatch, screen):
    first = tmp_path / "old.jpg"
    first.write_bytes(b"old")
    second = tmp_path / "new.jpg"
    second.write_bytes(b"new")
    holder = _patch_seams(monkeypatch, current=str(first))
    r = WallpaperRenderer(cache_dir=cache)
    r._base_image_path(screen)

    holder["current"] = str(second)  # user picked a new wallpaper
    base = r._base_image_path(screen)

    assert base == str(second)
    assert json.loads((cache / "originals.json").read_text()) == {"UUID-A": str(second)}
    assert (cache / "original-UUID-A.png").read_bytes() == b"new"


def test_degraded_session_recovers_in_memory_but_persists_nothing(tmp_path, cache, monkeypatch):
    # No resolvable display UUID (_display_key -> None): the live image is remembered
    # IN MEMORY under the session key, so updates keep working all session (no
    # one-switch-only failure), but nothing is written to disk under a transient key.
    src = tmp_path / "wall.jpg"
    src.write_bytes(b"bytes")
    holder = _patch_seams(monkeypatch, key=None, current=str(src))
    r = WallpaperRenderer(cache_dir=cache)

    assert r._base_image_path(object()) == str(src)  # captured live
    assert r._originals == {"display-1": str(src)}  # remembered in-memory (session key)
    assert not (cache / "originals.json").exists()  # nothing persisted to disk
    assert not list(cache.glob("original-*.png"))

    holder["current"] = str(cache / "display-1.png")  # our composite is now current
    assert r._base_image_path(object()) == str(src)  # still recovers within the session


def test_in_memory_recovery_survives_uuid_flip(tmp_path, cache, monkeypatch):
    # UUID resolution flips None -> real mid-session. The in-memory entry was keyed
    # by the session-stable display id, so it must still recover (no orphaning).
    src = tmp_path / "wall.jpg"
    src.write_bytes(b"bytes")
    holder = {"uuid": None, "current": str(src)}
    monkeypatch.setattr(WallpaperRenderer, "_display_key", lambda self, s: holder["uuid"])
    monkeypatch.setattr(WallpaperRenderer, "_screen_key", lambda self, s: "display-1")
    monkeypatch.setattr(
        WallpaperRenderer, "_current_desktop_path", lambda self, s: holder["current"]
    )
    r = WallpaperRenderer(cache_dir=cache)
    assert r._base_image_path(object()) == str(src)  # captured while UUID unresolved

    holder["uuid"] = "UUID-A"  # UUID resolution comes online
    holder["current"] = str(cache / "display-1.png")  # our composite is now current
    assert r._base_image_path(object()) == str(src)  # cgid-keyed memory still recovers


def test_recovery_survives_cgid_churn_when_persist_failed(tmp_path, cache, monkeypatch):
    # Persist fails at first capture AND the display's CGDirectDisplayID then churns
    # (UUID stable). The in-memory original was remembered under the UUID too, so
    # recovery still finds it via the UUID key (not lost to the cgid change).
    src = tmp_path / "wall.jpg"
    src.write_bytes(b"bytes")
    holder = {"cgid": "display-1", "current": str(src)}
    monkeypatch.setattr(WallpaperRenderer, "_display_key", lambda self, s: "UUID-A")
    monkeypatch.setattr(WallpaperRenderer, "_screen_key", lambda self, s: holder["cgid"])
    monkeypatch.setattr(
        WallpaperRenderer, "_current_desktop_path", lambda self, s: holder["current"]
    )
    # persist fails (json write returns False)
    monkeypatch.setattr(WallpaperRenderer, "_save_originals", lambda self, payload: False)
    r = WallpaperRenderer(cache_dir=cache)
    assert r._base_image_path(object()) == str(src)  # captured under display-1 + UUID-A
    assert r._persisted == {}  # persist failed

    holder["cgid"] = "display-2"  # display re-identified, UUID unchanged
    holder["current"] = str(cache / "display-2.png")  # our composite is current
    assert r._base_image_path(object()) == str(src)  # recovered via the UUID mem key


def test_degraded_session_cannot_recover_after_restart(tmp_path, cache, monkeypatch):
    # The inherent limit: a fresh renderer (restart) with no UUID and our composite
    # already applied has no in-memory entry and nothing persisted -> skip, not paint.
    holder = _patch_seams(monkeypatch, key=None, current=str(cache / "display-1.png"))
    r = WallpaperRenderer(cache_dir=cache)
    assert r._base_image_path(object()) is None
    assert holder  # keep ref


def test_in_place_rewrite_refreshes_copy(tmp_path, cache, monkeypatch, screen):
    # User rewrites the SAME wallpaper path with new bytes -> the fallback copy must
    # refresh (path unchanged but mtime newer), else recovery serves stale bytes.
    src = tmp_path / "wall.jpg"
    src.write_bytes(b"A")
    _patch_seams(monkeypatch, current=str(src))
    r = WallpaperRenderer(cache_dir=cache)
    r._base_image_path(screen)
    copy = cache / "original-UUID-A.png"
    assert copy.read_bytes() == b"A"

    src.write_bytes(b"B")  # in-place rewrite, same path
    future = time.time() + 1000
    os.utime(src, (future, future))
    r._base_image_path(screen)

    assert copy.read_bytes() == b"B"


def test_in_place_rewrite_refreshes_via_size(tmp_path, cache, monkeypatch, screen):
    # An in-place rewrite that changes the file SIZE refreshes the fallback copy even
    # when the new mtime is not newer (e.g. restored from an older backup via cp -p).
    src = tmp_path / "wall.jpg"
    src.write_bytes(b"AAAA")  # 4 bytes
    _patch_seams(monkeypatch, current=str(src))
    r = WallpaperRenderer(cache_dir=cache)
    r._base_image_path(screen)
    copy = cache / "original-UUID-A.png"
    assert copy.read_bytes() == b"AAAA"

    src.write_bytes(b"B")  # different size (1 byte), older mtime
    past = time.time() - 1000
    os.utime(src, (past, past))
    r._base_image_path(screen)

    assert copy.read_bytes() == b"B"  # refreshed via the size mismatch


def test_copy_failure_keeps_last_known_good(tmp_path, cache, monkeypatch, screen):
    # copy-before-json: if the copy of the new wallpaper fails, originals.json is NOT
    # advanced to a path whose copy we don't have -- the last known-good (first) stays
    # on disk. The live image is still used in-session, and within-session recovery
    # comes from the in-memory map.
    first = tmp_path / "a.jpg"
    first.write_bytes(b"A")
    second = tmp_path / "b.jpg"
    second.write_bytes(b"B")
    holder = _patch_seams(monkeypatch, current=str(first))
    r = WallpaperRenderer(cache_dir=cache)
    r._base_image_path(screen)  # captures A successfully (json + copy = A)

    monkeypatch.setattr(WallpaperRenderer, "_copy_original", lambda self, src, dst: False)
    holder["current"] = str(second)
    assert r._base_image_path(screen) == str(second)  # in-session uses the live image

    assert r._persisted == {"UUID-A": str(first)}  # NOT advanced -- copy never landed
    assert json.loads((cache / "originals.json").read_text()) == {"UUID-A": str(first)}
    assert (cache / "original-UUID-A.png").read_bytes() == b"A"  # last known-good copy

    # our composite becomes current -> within-session recovery uses in-memory (second)
    holder["current"] = str(cache / "display-1.png")
    assert r._base_image_path(screen) == str(second)


def test_peer_display_save_preserves_other_entry(tmp_path, cache, monkeypatch):
    # Multi-display: each display persists its own path; a save for one display
    # reload-merges so a peer's entry is never dropped (no last-writer-wins).
    ax, bx = tmp_path / "ax.jpg", tmp_path / "bx.jpg"
    ay = tmp_path / "ay.jpg"
    for path, data in ((ax, b"AX"), (bx, b"BX"), (ay, b"AY")):
        path.write_bytes(data)
    sx, sy = object(), object()
    keys = {id(sx): "UUID-X", id(sy): "UUID-Y"}
    cgids = {id(sx): "display-x", id(sy): "display-y"}
    current = {id(sx): str(ax), id(sy): str(ay)}
    monkeypatch.setattr(WallpaperRenderer, "_display_key", lambda self, s: keys[id(s)])
    monkeypatch.setattr(WallpaperRenderer, "_screen_key", lambda self, s: cgids[id(s)])
    monkeypatch.setattr(WallpaperRenderer, "_current_desktop_path", lambda self, s: current[id(s)])
    r = WallpaperRenderer(cache_dir=cache)
    r._base_image_path(sx)  # X persists AX
    r._base_image_path(sy)  # Y persists AY

    current[id(sx)] = str(bx)
    r._base_image_path(sx)  # X updates to BX -> must keep Y's entry (reload-merge)

    persisted = json.loads((cache / "originals.json").read_text())
    assert persisted == {"UUID-X": str(bx), "UUID-Y": str(ay)}
    assert r._persisted == {"UUID-X": str(bx), "UUID-Y": str(ay)}


def test_dead_live_path_falls_through_to_recovery(tmp_path, cache, monkeypatch, screen):
    # If the live wallpaper path is gone (unmounted drive / deleted / renamed), it's
    # not a usable base -- fall through to the recovered original instead of returning
    # a dead path that _render_png can't load (codex P1). Persist stays unchanged.
    real = tmp_path / "real.jpg"
    real.write_bytes(b"R")
    holder = _patch_seams(monkeypatch, current=str(real))
    r = WallpaperRenderer(cache_dir=cache)
    r._base_image_path(screen)  # persists the real path
    assert r._persisted == {"UUID-A": str(real)}

    holder["current"] = str(tmp_path / "gone.jpg")  # never created -> unreadable
    assert r._base_image_path(screen) == str(real)  # recovers the real base, not the dead path
    assert r._persisted == {"UUID-A": str(real)}  # last known-good kept
    assert json.loads((cache / "originals.json").read_text()) == {"UUID-A": str(real)}


def test_lock_contention_skips_persist_without_blocking(tmp_path, cache, monkeypatch, screen):
    # A held originals lock must not block the render path: persist is skipped
    # (non-blocking) and retried once the lock frees.
    src = tmp_path / "wall.jpg"
    src.write_bytes(b"bytes")
    _patch_seams(monkeypatch, current=str(src))
    r = WallpaperRenderer(cache_dir=cache)
    fd = os.open(cache / "originals.json.lock", os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert r._base_image_path(screen) == str(src)  # returns live, does not hang
        assert r._persisted == {}  # persist skipped while the lock is busy
        assert not (cache / "originals.json").exists()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    assert r._base_image_path(screen) == str(src)  # lock free -> persists now
    assert r._persisted == {"UUID-A": str(src)}


def test_persist_retried_via_remembered_branch(tmp_path, cache, monkeypatch, screen):
    # A persist skipped at first capture (lock busy) must still catch up later even
    # after our composite is applied (current becomes a cache path -> branch b), so
    # restart recovery isn't lost for the rest of the session.
    src = tmp_path / "wall.jpg"
    src.write_bytes(b"bytes")
    holder = _patch_seams(monkeypatch, current=str(src))
    r = WallpaperRenderer(cache_dir=cache)
    fd = os.open(cache / "originals.json.lock", os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        r._base_image_path(screen)  # capture; persist skipped (lock busy)
        assert r._persisted == {}
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    holder["current"] = str(cache / "display-1.png")  # our composite is now current
    assert r._base_image_path(screen) == str(src)  # recovered from in-memory (branch b)
    assert r._persisted == {"UUID-A": str(src)}  # ...and persistence caught up
    assert json.loads((cache / "originals.json").read_text()) == {"UUID-A": str(src)}


def test_steady_state_does_not_rewrite(tmp_path, cache, monkeypatch, screen):
    src = tmp_path / "wall.jpg"
    src.write_bytes(b"bytes")
    _patch_seams(monkeypatch, current=str(src))
    saves: list[int] = []

    def fake_save(self, payload):
        saves.append(1)
        return True

    monkeypatch.setattr(WallpaperRenderer, "_save_originals", fake_save)
    r = WallpaperRenderer(cache_dir=cache)

    r._base_image_path(screen)  # first capture -> records + copies
    r._base_image_path(screen)  # unchanged -> no rewrite

    assert len(saves) == 1


def test_future_dated_source_does_not_rewrite_each_render(tmp_path, cache, monkeypatch, screen):
    # A wallpaper file with a skewed/future mtime (common after restore/sync) must not
    # make every render re-persist: the copy preserves the source mtime, so steady
    # state stays a no-op (copy.mtime == src.mtime), not perpetually "stale".
    src = tmp_path / "wall.jpg"
    src.write_bytes(b"bytes")
    os.utime(src, (time.time() + 1_000_000, time.time() + 1_000_000))  # future-dated
    _patch_seams(monkeypatch, current=str(src))
    saves: list[int] = []

    def fake_save(self, payload):
        saves.append(1)
        return True

    monkeypatch.setattr(WallpaperRenderer, "_save_originals", fake_save)
    r = WallpaperRenderer(cache_dir=cache)

    r._base_image_path(screen)
    r._base_image_path(screen)

    assert len(saves) == 1  # not rewritten on the second render


def test_transient_save_failure_is_retried(tmp_path, cache, monkeypatch, screen):
    # If originals.json write fails once, _persisted must NOT advance (else the next
    # unchanged render short-circuits and never retries). A later render retries and,
    # on success, persists -- so recovery isn't stuck copy-only after a blip.
    src = tmp_path / "wall.jpg"
    src.write_bytes(b"bytes")
    _patch_seams(monkeypatch, current=str(src))
    outcomes = iter([False, True])  # first save fails, second succeeds
    monkeypatch.setattr(WallpaperRenderer, "_save_originals", lambda self, payload: next(outcomes))
    r = WallpaperRenderer(cache_dir=cache)

    r._base_image_path(screen)  # copy ok, save FAILS -> not committed
    assert r._persisted == {}

    r._base_image_path(screen)  # retries the save -> now committed
    assert r._persisted == {"UUID-A": str(src)}


# --- restart recovery order: (a) live, (b) path, (c) copy, (d) skip --------


def _ours(cache):
    """Return a cache path -- what the system reports as current after a restart."""
    return str(cache / "display-1.png")


def test_restart_recovers_from_originals_json(tmp_path, cache, monkeypatch, screen):
    real = tmp_path / "real.jpg"
    real.write_bytes(b"real")
    (cache / "originals.json").write_text(json.dumps({"UUID-A": str(real)}))
    _patch_seams(monkeypatch, current=_ours(cache))  # current image is OUR composite
    r = WallpaperRenderer(cache_dir=cache)

    assert r._base_image_path(screen) == str(real)


def test_restart_falls_back_to_copy_when_path_gone(tmp_path, cache, monkeypatch, screen):
    (cache / "originals.json").write_text(json.dumps({"UUID-A": str(tmp_path / "gone.jpg")}))
    copy = cache / "original-UUID-A.png"
    copy.write_bytes(b"copy")
    _patch_seams(monkeypatch, current=_ours(cache))
    r = WallpaperRenderer(cache_dir=cache)

    assert r._base_image_path(screen) == str(copy)


def test_restart_skips_when_unrecoverable(tmp_path, cache, monkeypatch, screen):
    (cache / "originals.json").write_text(json.dumps({"UUID-A": str(tmp_path / "gone.jpg")}))
    _patch_seams(monkeypatch, current=_ours(cache))
    r = WallpaperRenderer(cache_dir=cache)

    assert r._base_image_path(screen) is None


def test_restart_heals_missing_copy_from_persisted_path(tmp_path, cache, monkeypatch, screen):
    # A prior run wrote originals.json but not original-<uuid>.png. On restart,
    # recovering from the (still readable) persisted path must rebuild the copy, so a
    # later move/unmount of the source doesn't leave recovery with nothing.
    real = tmp_path / "real.jpg"
    real.write_bytes(b"R")
    (cache / "originals.json").write_text(json.dumps({"UUID-A": str(real)}))  # no copy on disk
    _patch_seams(monkeypatch, current=_ours(cache))
    r = WallpaperRenderer(cache_dir=cache)

    assert r._base_image_path(screen) == str(real)
    assert (cache / "original-UUID-A.png").read_bytes() == b"R"  # copy healed from source


def test_recovery_reloads_peer_repaired_dead_path(tmp_path, cache, monkeypatch):
    # We loaded {UUID: /dead} at startup; a peer then repaired originals.json to a live
    # path. Branch (c) must re-read disk on the dead path (not only on a missing one)
    # and adopt the peer's live path instead of falling through to skip (codex P2).
    dead = tmp_path / "old.jpg"  # never created -> dead
    live = tmp_path / "new.jpg"
    live.write_bytes(b"R")
    (cache / "originals.json").write_text(json.dumps({_UUID_A: str(dead)}))
    monkeypatch.setattr(WallpaperRenderer, "_display_key", lambda self, s: _UUID_A)
    monkeypatch.setattr(WallpaperRenderer, "_screen_key", lambda self, s: "display-1")
    monkeypatch.setattr(
        WallpaperRenderer, "_current_desktop_path", lambda self, s: str(cache / "display-1.png")
    )
    r = WallpaperRenderer(cache_dir=cache)  # _persisted = {UUID-A: dead}
    assert r._persisted == {_UUID_A: str(dead)}

    (cache / "originals.json").write_text(json.dumps({_UUID_A: str(live)}))  # peer repair
    assert r._base_image_path(object()) == str(live)  # re-read disk, adopt the live path


def test_restart_recovery_seeds_memory_for_transient_uuid_loss(tmp_path, cache, monkeypatch):
    # A base recovered from originals.json at restart is seeded into session memory,
    # so a later transient UUID-resolution loss still recovers via the display-id key.
    real = tmp_path / "real.jpg"
    real.write_bytes(b"R")
    (cache / "originals.json").write_text(json.dumps({"UUID-A": str(real)}))
    holder = {"uuid": "UUID-A", "current": str(cache / "display-1.png")}
    monkeypatch.setattr(WallpaperRenderer, "_display_key", lambda self, s: holder["uuid"])
    monkeypatch.setattr(WallpaperRenderer, "_screen_key", lambda self, s: "display-1")
    monkeypatch.setattr(
        WallpaperRenderer, "_current_desktop_path", lambda self, s: holder["current"]
    )
    r = WallpaperRenderer(cache_dir=cache)
    assert r._base_image_path(object()) == str(real)  # branch (c): recovered + seeded

    holder["uuid"] = None  # UUID resolution drops out transiently
    assert r._base_image_path(object()) == str(real)  # branch (b) via display-id still recovers


# --- eviction ---------------------------------------------------------------


def test_is_managed_png_matches_only_our_exact_filenames():
    # Our outputs are eviction candidates; foreign files that merely share a prefix
    # are not (codex P3 -- never delete a user's display-reference.png / original-photo.png).
    assert _is_managed_png("main.png")
    assert _is_managed_png("display-3.png")
    assert _is_managed_png(f"original-{_UUID_A}.png")
    assert _is_managed_png(f"original-{_UUID_A.lower()}.png")
    assert not _is_managed_png("display-reference.png")
    assert not _is_managed_png("original-photo.png")
    assert not _is_managed_png("display-.png")
    assert not _is_managed_png("notes.txt")
    assert not _is_managed_png("originals.json")


def test_select_evictions_pure(tmp_path):
    ttl = 10.0
    now = 1000.0
    keep = {tmp_path / "keep.png"}
    files = [
        (tmp_path / "keep.png", now),  # in keep -> never evicted, even if old
        (tmp_path / "stale.png", now - 100),  # old + not kept -> evict
        (tmp_path / "fresh.png", now - 1),  # recent -> kept (TTL not reached)
        (tmp_path / "originals.json", now - 100),  # not a .png -> never evicted
    ]
    assert _select_evictions(files, keep, now, ttl) == [tmp_path / "stale.png"]


def test_purge_evicts_stale_disconnected_keeps_active_and_outside(tmp_path, cache, monkeypatch):
    outside = tmp_path / "outside.txt"
    outside.write_text("untouched")

    files = {
        "display-1.png": cache / "display-1.png",  # connected composite -> keep
        f"original-{_UUID_C}.png": cache / f"original-{_UUID_C}.png",  # referenced -> keep
        "display-9.png": cache / "display-9.png",  # disconnected, stale -> evict
        f"original-{_UUID_X}.png": cache / f"original-{_UUID_X}.png",  # unreferenced -> evict
        "display-7.png": cache / "display-7.png",  # disconnected, fresh -> keep (TTL)
        "originals.json": cache / "originals.json",  # never a candidate
        "display-reference.png": cache / "display-reference.png",  # foreign -> keep
        "original-photo.png": cache / "original-photo.png",  # foreign -> keep
    }
    for path in files.values():
        path.write_bytes(b"x")
    old = time.time() - 100 * 86400
    # everything-but-the-connected/fresh is old, so the only thing keeping the
    # referenced original and the foreign PNGs is the keep-set + managed-name filter.
    for name in files:
        if name not in ("display-1.png", "display-7.png"):
            os.utime(files[name], (old, old))

    r = WallpaperRenderer(cache_dir=cache)
    r._persisted = {_UUID_C: "/Users/me/wall.jpg"}  # referenced -> its copy is kept
    monkeypatch.setattr(WallpaperRenderer, "_connected_composite_keys", lambda self: {"display-1"})
    monkeypatch.setattr(WallpaperRenderer, "_live_cache_wallpapers", lambda self: set())

    r._purge()

    assert files["display-1.png"].exists()
    assert files[f"original-{_UUID_C}.png"].exists()
    assert files["display-7.png"].exists()
    assert files["originals.json"].exists()
    assert not files["display-9.png"].exists()
    assert not files[f"original-{_UUID_X}.png"].exists()
    assert files["display-reference.png"].exists()  # foreign -> never swept
    assert files["original-photo.png"].exists()  # foreign -> never swept
    assert outside.exists()  # nothing outside the cache is ever touched


def test_purge_keeps_referenced_original_even_when_disconnected(tmp_path, cache, monkeypatch):
    # A display unplugged for a long time but still referenced by originals.json must
    # keep its backup copy -- never evict the only base for a display that may
    # reconnect (codex P2). An unreferenced orphan copy is still TTL-swept.
    referenced = cache / f"original-{_UUID_A}.png"  # still in originals.json
    orphan = cache / f"original-{_UUID_Z}.png"  # no json entry -> orphan
    for path in (referenced, orphan):
        path.write_bytes(b"x")
    old = time.time() - 100 * 86400
    os.utime(referenced, (old, old))
    os.utime(orphan, (old, old))

    r = WallpaperRenderer(cache_dir=cache)
    r._persisted = {_UUID_A: "/Users/me/wall.jpg"}
    monkeypatch.setattr(WallpaperRenderer, "_connected_composite_keys", lambda self: set())
    monkeypatch.setattr(WallpaperRenderer, "_live_cache_wallpapers", lambda self: set())

    r._purge()

    assert referenced.exists()  # referenced backup kept despite disconnect + old mtime
    assert not orphan.exists()  # unreferenced orphan still swept


def test_purge_keeps_referenced_original_from_peer_written_json(tmp_path, cache, monkeypatch):
    # A peer renderer wrote originals.json AFTER this instance loaded it. _purge must
    # read the referenced set fresh from disk, not a stale _persisted snapshot, or it
    # would delete the peer's still-referenced backup (codex P2).
    peer_copy = cache / f"original-{_UUID_A}.png"
    peer_copy.write_bytes(b"x")
    old = time.time() - 100 * 86400
    os.utime(peer_copy, (old, old))
    r = WallpaperRenderer(cache_dir=cache)  # _persisted loaded empty here
    (cache / "originals.json").write_text(json.dumps({_UUID_A: "/Users/me/wall.jpg"}))  # peer write
    monkeypatch.setattr(WallpaperRenderer, "_connected_composite_keys", lambda self: set())
    monkeypatch.setattr(WallpaperRenderer, "_live_cache_wallpapers", lambda self: set())

    r._purge()

    assert peer_copy.exists()  # kept because the on-disk map still references it


def test_purge_keeps_copy_recovered_with_missing_json(tmp_path, cache, monkeypatch):
    # originals.json missing/corrupt but a copy exists: once branch (d) recovers from
    # it (seeding _originals), the next purge must not delete the only fallback (P2).
    copy = cache / f"original-{_UUID_A}.png"
    copy.write_bytes(b"x")
    old = time.time() - 100 * 86400
    os.utime(copy, (old, old))
    monkeypatch.setattr(WallpaperRenderer, "_display_key", lambda self, s: _UUID_A)
    monkeypatch.setattr(WallpaperRenderer, "_screen_key", lambda self, s: "display-1")
    monkeypatch.setattr(
        WallpaperRenderer, "_current_desktop_path", lambda self, s: str(cache / "display-1.png")
    )
    monkeypatch.setattr(WallpaperRenderer, "_connected_composite_keys", lambda self: set())
    monkeypatch.setattr(WallpaperRenderer, "_live_cache_wallpapers", lambda self: set())
    r = WallpaperRenderer(cache_dir=cache)  # _persisted empty (no json)

    assert r._base_image_path(object()) == str(copy)  # branch (d) recovers from the copy
    r._purge()

    assert copy.exists()  # the recovered fallback is not evicted as an orphan


def test_purge_keeps_live_applied_composite_under_stale_id(tmp_path, cache, monkeypatch):
    # A display's CGDirectDisplayID changed; WallpaperAgent still points at last
    # session's `display-5.png`, which we have not revisited and which is stale by
    # mtime -- but it is the LIVE wallpaper, so it must survive the sweep (codex P2).
    live = cache / "display-5.png"
    gone = cache / "display-8.png"
    for path in (live, gone):
        path.write_bytes(b"x")
    old = time.time() - 100 * 86400
    os.utime(live, (old, old))
    os.utime(gone, (old, old))

    r = WallpaperRenderer(cache_dir=cache)
    monkeypatch.setattr(WallpaperRenderer, "_connected_composite_keys", lambda self: {"display-6"})
    monkeypatch.setattr(WallpaperRenderer, "_live_cache_wallpapers", lambda self: {live})

    r._purge()

    assert live.exists()  # live-applied wallpaper kept despite stale id + old mtime
    assert not gone.exists()  # genuinely abandoned stale composite evicted


def test_purge_evicts_stale_output_from_a_reid_display(tmp_path, cache, monkeypatch):
    # A composite we rendered this session lingers in _outputs after the display's id
    # changed / it unplugged. It is neither connected nor live, so it must still age
    # out -- _outputs must not pin it forever (codex P3).
    stale = cache / "display-5.png"
    stale.write_bytes(b"x")
    old = time.time() - 100 * 86400
    os.utime(stale, (old, old))

    r = WallpaperRenderer(cache_dir=cache)
    r._outputs = {"display-5": stale}  # remembered from earlier this session
    monkeypatch.setattr(WallpaperRenderer, "_connected_composite_keys", lambda self: {"display-6"})
    monkeypatch.setattr(WallpaperRenderer, "_live_cache_wallpapers", lambda self: set())

    r._purge()

    assert not stale.exists()  # no longer connected/live -> swept despite _outputs
