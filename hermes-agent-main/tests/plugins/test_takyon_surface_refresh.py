from __future__ import annotations

from pathlib import Path

from plugins.takyon import core as takyon_core
from plugins.takyon.core import TakyonStore


def test_surface_refresh_rewrites_without_auto_verify():
    store = object.__new__(TakyonStore)
    calls: list[tuple[str, str]] = []

    store._stored_app_surface_contract = lambda conn, slug: {"source_path": "product/site"}  # type: ignore[attr-defined]
    store._rewrite_app_files = lambda conn, slug: calls.append(("rewrite", slug))  # type: ignore[attr-defined]
    store._auto_verify_product_surface_for_source_change = (  # type: ignore[attr-defined]
        lambda conn, slug, changed_rel: calls.append(("verify", slug))
    )

    TakyonStore._refresh_surface_projection_files_for_path(
        store,
        conn=object(),
        slug="demo",
        rel_path="product/site/index.html",
    )

    assert calls == [("rewrite", "demo")]


def test_core_has_no_eager_surface_auto_verify_callers():
    # The eager auto-verify-on-source-change path was retired (it re-verified the product on every
    # source write and was a build-thrash contributor); the rewrite path now only rewrites and lets
    # the explicit refresh/publish gate verify. Guard against the eager symbol being reintroduced.
    text = Path(takyon_core.__file__).read_text(encoding="utf-8")
    assert text.count("_auto_verify_product_surface_for_source_change(") == 0


def test_surface_refresh_installs_when_node_modules_absent():
    # A freshly-materialized readback/cache workspace is deps-free by design (node_modules is
    # never synced into canonical storage). The refresh build path must therefore install even
    # when a caller passes install=False, or the build false-fails with a misleading
    # "vite: not found". Guard the install gate against regressing back to a bare `if install:`.
    text = Path(takyon_core.__file__).read_text(encoding="utf-8")
    assert "if install or not _node_modules_present(root):" in text
