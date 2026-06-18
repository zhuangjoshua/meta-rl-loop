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



def test_worker_process_is_authority_surface_for_worker_only_ops(monkeypatch):
    # Worker-only authority ops (product-surface refresh / runtime-capability provisioning) refuse a
    # session-bound INTERACTIVE/cron/wake CEO turn so it routes through the worker plane, but the
    # durable worker process (TAKYON_WORKER_PROCESS=1) IS that authority surface and must be let
    # through even though _operator_tool_task_handler binds business_slug into the session for
    # workspace scoping. Guard against the regression where the session-bound guard refused the
    # legitimate worker run too (then product-surface/runtime-capability work blocked entirely).
    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: "acme")

    monkeypatch.delenv("TAKYON_WORKER_PROCESS", raising=False)
    assert takyon_core._is_worker_process() is False
    # Session-bound, not a worker → still quarantined (keeps deferring).
    assert takyon_core._blocks_session_bound_authority_op() is True

    monkeypatch.setenv("TAKYON_WORKER_PROCESS", "1")
    assert takyon_core._is_worker_process() is True
    # Session-bound BUT on the worker process → permitted (the authority surface).
    assert takyon_core._blocks_session_bound_authority_op() is False

    # No session binding at all (pure authority surface) is permitted regardless of worker flag.
    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: "")
    monkeypatch.delenv("TAKYON_WORKER_PROCESS", raising=False)
    assert takyon_core._blocks_session_bound_authority_op() is False


def test_worker_only_authority_handlers_use_worker_exempt_guard():
    # The two worker-only authority handlers must gate on _blocks_session_bound_authority_op (which
    # exempts the worker process), NOT on a bare _session_business_slug() check that would also
    # refuse the legitimate worker run. The deferral ROUTER may still use the bare session check
    # because it is inert on the worker process, so we only assert the two handlers here.
    text = Path(takyon_core.__file__).read_text(encoding="utf-8")
    for marker in (
        'raise TakyonError("trusted product surface refresh is available only on the authority tool surface")',
        'raise TakyonError("runtime capability provisioning is available only on the authority tool surface")',
    ):
        idx = text.index(marker)
        preceding = text[:idx].rsplit("\n", 3)[-3:]
        guard_line = "\n".join(preceding)
        assert "_blocks_session_bound_authority_op()" in guard_line, (
            f"worker-only authority handler for {marker!r} must use the worker-exempt guard"
        )
