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
    text = Path(takyon_core.__file__).read_text(encoding="utf-8")
    assert text.count("_auto_verify_product_surface_for_source_change(") == 1
