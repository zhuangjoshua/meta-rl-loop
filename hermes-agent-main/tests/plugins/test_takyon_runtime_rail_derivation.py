"""Default-on free rails and source-derived paid-provider rail declaration."""
from __future__ import annotations

from plugins.takyon import app_actions, core as takyon_core


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_scanner_detects_app_rail_usage_and_skips_the_kit(tmp_path):
    site = tmp_path / "product" / "site"
    # App source genuinely calls the records + media rails through the runtime client.
    _write(
        site / "src" / "App.tsx",
        "import { client } from './lib/runtime';\n"
        "export const Plants = () => client.listRecords({ type: 'plant' });\n"
        "export const Upload = (f) => client.uploadMedia(f);\n",
    )
    # The kit DEFINES every rail method but lives under `_takyon`; it must be skipped so the
    # method definitions are not mistaken for app usage.
    _write(
        site / "_takyon" / "runtime-client.js",
        "async listRecords(){ ensureRail('records'); }\n"
        "async generate(){ ensureRail('generate'); }\n"
        "async search(){ ensureRail('search'); }\n"
        "async listConnections(){ ensureRail('connections'); }\n",
    )
    assert app_actions.referenced_runtime_rails_in_source(site) == {"records", "media"}


def test_scanner_returns_empty_when_no_rails_used(tmp_path):
    site = tmp_path / "product" / "site"
    _write(site / "src" / "App.tsx", "export const App = () => 'hello';\n")
    assert app_actions.referenced_runtime_rails_in_source(site) == set()


def test_scanner_detects_action_internal_generate(tmp_path):
    site = tmp_path / "product" / "site"
    # AI flows run inside action files via ctx.generate — the generate rail must be derived.
    _write(
        site / "actions" / "draft-reply.ts",
        "export default async (payload, ctx) => ctx.generate({ prompt: payload.text });\n",
    )
    assert "generate" in app_actions.referenced_runtime_rails_in_source(site)


def test_reconcile_defaults_non_spendful_rails(tmp_path):
    workspace = tmp_path / "product" / "site"
    _write(
        workspace / "src" / "App.tsx",
        "import { client } from './lib/runtime';\n"
        "export const Plants = () => client.listRecords({ type: 'plant' });\n",
    )
    surface = {
        "runtime_features": ["auth", "account", "profile", "checkout"],  # seed: no records
        "metadata": {"subuser_app": {}},
        "routes": ["/", "/app"],
    }
    reconciled, receipt = takyon_core._reconcile_product_runtime_features_from_source(
        workspace,
        surface=surface,
        receipt_path="metrics/receipts/product-surface/run.json",
    )
    assert reconciled == list(takyon_core.DEFAULT_BOOTSTRAP_ACCESS_SHELL_RUNTIME_FEATURES)
    assert receipt["added"] == ["records", "actions", "media", "entitlements", "usage"]
    assert receipt["receipt_path"] == "metrics/receipts/runtime-rails/run.json"


def test_reconcile_adds_used_generate_and_drops_unused_search(tmp_path):
    workspace = tmp_path / "product" / "site"
    _write(workspace / "actions" / "draft.ts", "export default (p, ctx) => ctx.generate(p);\n")
    surface = {
        "runtime_features": ["auth", "account", "search"],
        "metadata": {"subuser_app": {}},
        "routes": ["/", "/app"],
    }
    reconciled, receipt = takyon_core._reconcile_product_runtime_features_from_source(
        workspace,
        surface=surface,
        receipt_path="metrics/receipts/product-surface/run.json",
    )
    assert "generate" in reconciled
    assert "search" not in reconciled
    assert receipt["spendful_rails_used"] == ["generate"]
    assert receipt["removed"] == ["search"]


def test_only_paid_provider_rails_are_build_derived():
    assert takyon_core._BUILD_DERIVED_RAILS == {"generate", "search"}


def test_refresh_operations_write_runtime_rail_receipt():
    operations = takyon_core._product_surface_refresh_operations(
        business="demo",
        surface_refresh={
            "status": "passed",
            "kind": "vite_react_ts",
            "source_path": "product/site",
            "receipt_path": "metrics/receipts/product-surface/run.json",
            "runtime_features": list(takyon_core.DEFAULT_BOOTSTRAP_ACCESS_SHELL_RUNTIME_FEATURES),
            "runtime_rail_reconciliation": {
                "status": "applied",
                "receipt_path": "metrics/receipts/runtime-rails/run.json",
                "added": ["media"],
                "removed": [],
            },
            "publish": {"status": "published", "database_build_activated": True},
        },
        surface={"runtime_features": []},
        publish_target="https://demo.coscale.app/",
        publish_policy="static",
        requested_publish_policy="static",
        activate_on_success=True,
    )
    writes = [op for op in operations if op["action"] == "artifact.write"]
    assert any(op["path"] == "metrics/receipts/runtime-rails/run.json" for op in writes)
    events = [op for op in operations if op["action"] == "event.record"]
    assert any(op["event_type"] == "product.runtime_rails.reconciled" for op in events)
