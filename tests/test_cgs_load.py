"""CGS symbol loader: the CGS->SLS per-symbol fallback resolves from SkyLight.

On Tahoe CoreGraphics exports only the ``CGS*`` re-exports and SkyLight only the
``SLS*`` implementations, so the per-symbol fallback must try the ``SLS`` name against
a *separately-loaded SkyLight bundle* — trying it against CoreGraphics (the old code)
was a no-op. ``loadBundleFunctions`` also
returns ``None`` even when it skips a missing symbol, so the loader decides success by
whether the symbol was actually bound, not by the return value.
"""

from __future__ import annotations

import pytest

objc = pytest.importorskip("objc")  # PyObjC only on macOS

from spacelabel.platform import cgs  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_loader_cache():
    # _load memoizes into _NS + caches bundles in _BUNDLES; clear before and after so
    # we never poison (or read) the live environment.
    cgs._NS.clear()
    cgs._BUNDLES.clear()
    yield
    cgs._NS.clear()
    cgs._BUNDLES.clear()


class _Bundle:
    """A stand-in framework bundle that resolves a fixed set of symbol prefixes."""

    def __init__(self, name: str, *, resolves: tuple[str, ...]) -> None:
        self.name = name
        self.resolves = resolves


def _install_fake_objc(monkeypatch, *, cgs_resolves: bool, sls_resolves: bool):
    """Wire fake objc.loadBundle/loadBundleFunctions; return the list of loaded names."""
    loaded: list[str] = []

    def fake_load_bundle(name, *_a, **_k):
        loaded.append(name)
        if name == "CoreGraphics":
            return _Bundle(name, resolves=("CGS",) if cgs_resolves else ())
        if name == "SkyLight":
            return _Bundle(name, resolves=("SLS",) if sls_resolves else ())
        return _Bundle(name, resolves=())

    def fake_load_bundle_functions(bundle, into, specs):
        name = specs[0][0]
        # Resolve only when this specific bundle owns the symbol's prefix — so the SLS
        # name can ONLY come from SkyLight, never from CoreGraphics.
        if any(name.startswith(prefix) for prefix in bundle.resolves):
            into[name] = lambda *a, **k: 0
        return None  # mimics the real API: None even when a symbol is skipped

    monkeypatch.setattr(objc, "loadBundle", fake_load_bundle)
    monkeypatch.setattr(objc, "loadBundleFunctions", fake_load_bundle_functions)
    return loaded


def test_load_falls_back_to_sls_from_skylight(monkeypatch):
    # CGS aliases absent from CoreGraphics -> every symbol must resolve via its SLS name
    # from the separately-loaded SkyLight bundle.
    loaded = _install_fake_objc(monkeypatch, cgs_resolves=False, sls_resolves=True)

    funcs = cgs._load()

    for cgs_name, *_rest in cgs._FUNCS:
        assert callable(funcs[cgs_name])  # bound via the SLS fallback, under the CGS key
    assert "SkyLight" in loaded  # the fallback actually loaded a separate SkyLight bundle
    assert "SkyLight" in cgs._BUNDLES  # ...and cached it


def test_load_prefers_coregraphics_and_skips_skylight(monkeypatch):
    # The normal Tahoe path: CGS* resolves from CoreGraphics, so SkyLight is never loaded.
    loaded = _install_fake_objc(monkeypatch, cgs_resolves=True, sls_resolves=True)

    funcs = cgs._load()

    for cgs_name, *_rest in cgs._FUNCS:
        assert callable(funcs[cgs_name])
    assert "SkyLight" not in loaded  # lazy: no CGS miss -> SkyLight never touched
    assert loaded.count("CoreGraphics") == 1  # bundle loaded once, then cached


def test_load_raises_when_neither_name_resolves(monkeypatch):
    # Neither CoreGraphics (CGS) nor SkyLight (SLS) binds the symbol -> hard failure.
    _install_fake_objc(monkeypatch, cgs_resolves=False, sls_resolves=False)
    with pytest.raises(cgs.CGSUnavailableError, match="SkyLight"):
        cgs._load()
