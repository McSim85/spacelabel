"""CGS symbol loader: the CGS->SLS per-symbol fallback (macOS-only).

``objc.loadBundleFunctions`` returns ``None`` even when it skips a missing symbol,
so the loader must verify the symbol was actually bound (not trust the return) or a
removed ``CGS*`` alias would store ``None`` and skip the ``SLS*`` fallback.
"""

from __future__ import annotations

import pytest

objc = pytest.importorskip("objc")  # PyObjC only on macOS

from spacelabel.platform import cgs  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_loader_cache():
    # _load memoizes into _NS; clear before and after so we don't poison the live env.
    cgs._NS.clear()
    yield
    cgs._NS.clear()


def test_load_falls_back_to_sls_when_cgs_alias_missing(monkeypatch):
    monkeypatch.setattr(objc, "loadBundle", lambda *a, **k: object())

    def fake_load_bundle_functions(_bundle, into, specs):
        name = specs[0][0]
        if name.startswith("SLS"):  # only the SLS implementation resolves
            into[name] = lambda *a, **k: 0
        return None  # mimics the real API: None even when a symbol is skipped

    monkeypatch.setattr(objc, "loadBundleFunctions", fake_load_bundle_functions)

    funcs = cgs._load()
    for cgs_name, *_rest in cgs._FUNCS:
        assert funcs[cgs_name] is not None  # never stored a None from a skipped CGS name
        assert callable(funcs[cgs_name])


def test_load_raises_when_neither_name_resolves(monkeypatch):
    monkeypatch.setattr(objc, "loadBundle", lambda *a, **k: object())
    monkeypatch.setattr(objc, "loadBundleFunctions", lambda *a, **k: None)  # binds nothing
    with pytest.raises(cgs.CGSUnavailableError):
        cgs._load()
