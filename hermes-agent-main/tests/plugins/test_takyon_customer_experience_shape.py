from __future__ import annotations

from pathlib import Path
from urllib.error import URLError

import pytest

from plugins.takyon import core as takyon_core


def test_surface_customer_experience_shape_defaults_strategy_source():
    shape = takyon_core._surface_customer_experience_shape(  # type: ignore[attr-defined]
        {
            "metadata": {},
        }
    )

    assert shape["research_sources"] == ["research/strategy.md"]
    assert shape["surface_goal"] == ""
    assert shape["required_sections"] == []


def test_subuser_surface_context_omits_burned_customer_experience_payload():
    metadata = takyon_core._merge_customer_experience_metadata(  # type: ignore[attr-defined]
        {
            "customer_experience": {
                "required_sections": ["hero"],
            }
        },
        surface_goal="Parents trust the app enough to sign up and start planning",
        conversion_model="self_serve_signup",
        required_routes=["/", "/pricing", "/app", "/pricing"],
        required_sections=["hero", "sample plan", "pricing"],
        required_app_tabs=["Planner", "Progress"],
        research_sources=["research/strategy.md", "research/market.md"],
    )
    payload = takyon_core._subuser_surface_context_payload(  # type: ignore[attr-defined]
        {
            "metadata": metadata,
            "runtime_features": ["auth", "generate"],
        },
        slug="plannerly",
    )

    assert "customerExperience" not in payload
    assert payload["runtimeFeatures"] == ["auth", "account", "generate"]


def test_subuser_surface_context_includes_supabase_public_auth_config(monkeypatch):
    def fake_public_env_value(*names: str) -> str:
        if "SUPABASE_URL" in names:
            return "https://project.supabase.co"
        if "SUPABASE_PUBLISHABLE_KEY" in names:
            return "sb_publishable_test"
        return ""

    monkeypatch.setattr(takyon_core, "_subuser_public_env_value", fake_public_env_value)

    payload = takyon_core._subuser_surface_context_payload(  # type: ignore[attr-defined]
        {"runtime_features": ["auth"]},
        slug="plannerly",
    )

    assert payload["auth"] == {
        "provider": "supabase",
        "configured": True,
        "url": "https://project.supabase.co",
        "publishableKey": "sb_publishable_test",
        "googleProvider": "google",
        "redirectPath": "/app",
    }


def test_subuser_surface_context_marks_supabase_auth_unconfigured_when_public_values_are_missing(monkeypatch):
    monkeypatch.setattr(takyon_core, "_subuser_public_env_value", lambda *names: "")

    payload = takyon_core._subuser_surface_context_payload(  # type: ignore[attr-defined]
        {"runtime_features": ["auth"]},
        slug="plannerly",
    )

    assert payload["auth"] == {
        "provider": "supabase",
        "configured": False,
        "url": "",
        "publishableKey": "",
        "googleProvider": "google",
        "redirectPath": "/app",
    }


def test_merge_subuser_app_metadata_preserves_existing_rail_truth_when_declaring_new_rail():
    metadata = takyon_core._merge_subuser_app_metadata(  # type: ignore[attr-defined]
        {
            "subuser_app": {
                "rail_state": {
                    "auth": "live",
                    "account": "live",
                },
            }
        },
        runtime_features=["auth", "account", "records"],
        previous_runtime_features=["auth", "account"],
    )
    assert metadata["subuser_app"]["rail_state"] == {
        "auth": "live",
        "account": "live",
        "records": "declared",
    }


def test_product_workflow_validator_is_noop_for_legacy_scope_rules():
    assert (
        takyon_core._validate_product_workflow_contract(  # type: ignore[attr-defined]
            surface={
                "runtime_features": ["directory"],
                "metadata": {
                    "product_workflow": {
                        "scope_rules": {
                            "no_sharing": True,
                        }
                    }
                },
            },
            runtime_features=["auth", "account", "profile", "directory"],
        )
        is None
    )


def test_materialized_subuser_kit_writes_js_context_only(tmp_path: Path):
    workspace_root = tmp_path / "product" / "site"
    workspace_root.mkdir(parents=True)

    takyon_core._materialize_subuser_app_kit(  # type: ignore[attr-defined]
        workspace_root,
        slug="plannerly",
        surface={"runtime_features": ["auth", "account"], "routes": [{"path": "/"}, {"path": "/app"}]},
    )

    kit_root = workspace_root / takyon_core.SUBUSER_KIT_DIRNAME
    assert (kit_root / "surface-context.js").exists()
    assert not (kit_root / "surface-context.md").exists()


def test_materialized_subuser_kit_derives_actions_rail_from_workspace_files(tmp_path: Path):
    workspace_root = tmp_path / "product" / "site"
    (workspace_root / "actions").mkdir(parents=True)
    (workspace_root / "actions" / "coach-chat.ts").write_text(
        "export default async () => ({ ok: true });\n",
        encoding="utf-8",
    )

    takyon_core._materialize_subuser_app_kit(  # type: ignore[attr-defined]
        workspace_root,
        slug="plannerly",
        surface={"runtime_features": ["auth", "account"], "routes": [{"path": "/"}, {"path": "/app"}]},
    )

    surface_context = (workspace_root / takyon_core.SUBUSER_KIT_DIRNAME / "surface-context.js").read_text(
        encoding="utf-8"
    )
    assert '"actions"' in surface_context
def test_materialized_subuser_kit_seeds_monthly_app_starter_for_app_shells(tmp_path: Path):
    workspace_root = tmp_path / "product" / "site"
    workspace_root.mkdir(parents=True)

    takyon_core._materialize_subuser_app_kit(  # type: ignore[attr-defined]
        workspace_root,
        slug="plannerly",
        surface={
            "runtime_features": ["auth", "checkout"],
            "routes": [{"path": "/"}, {"path": "/app"}],
            "metadata": {
                "subuser_app": {
                    "app_mode": "standard_saas",
                    "subscription_style": "monthly",
                },
                "customer_experience": {
                    "required_routes": ["/", "/app"],
                    "required_app_tabs": ["Planner", "Account"],
                },
            },
        },
        plans=[
            {
                "plan_key": "monthly",
                "tier": "paid",
                "price_cents": 1900,
                "currency": "usd",
                "billing_interval": "month",
                "included_ai_budget_microusd": 5_000_000,
                "included_action_quota": 0,
            }
        ],
    )

    assert (workspace_root / "package.json").exists()
    assert (workspace_root / "vite.config.ts").exists()
    assert (workspace_root / "package-lock.json").exists()
    assert (workspace_root / "src" / "screens" / "landing.tsx").exists()
    assert (workspace_root / "src" / "screens" / "support.tsx").exists()
    assert (workspace_root / "src" / "tokens.css").exists()
    assert (workspace_root / "public" / "robots.txt").exists()
    assert not (workspace_root / "next.config.js").exists()
    assert not (workspace_root / "node_modules").exists()
    assert not (workspace_root / "dist").exists()
    assert not (workspace_root / "_takyon" / "scaffold").exists()
    assert (workspace_root / "_takyon" / "runtime-client.js").exists()
    assert "Plannerly" in (workspace_root / "index.html").read_text(encoding="utf-8")
    surface_context = (workspace_root / takyon_core.SUBUSER_KIT_DIRNAME / "surface-context.js").read_text()
    assert "export const surfaceContext =" in surface_context
    assert "export const subuserSurfaceContext = surfaceContext;" in surface_context
    assert "export default surfaceContext;" in surface_context
    assert '"priceCents": 1900' in surface_context
    assert '"includedAiBudgetMicrousd": 5000000' in surface_context
    assert '"auth": "declared"' in surface_context
    assert '"checkout": "declared"' in surface_context
    hooks = (workspace_root / "src" / "lib" / "hooks.ts").read_text(encoding="utf-8")
    takyon_lib = (workspace_root / "src" / "lib" / "takyon.ts").read_text(encoding="utf-8")
    support_source = (workspace_root / "src" / "screens" / "support.tsx").read_text(encoding="utf-8")
    assert "useViewerAccess" in hooks
    assert "resolveViewerCta" in hooks
    assert "export function defaultPlanPriceLabel" in takyon_lib
    assert "Frequently asked questions" in support_source
    assert "Privacy policy" in support_source
    assert 'aria-hidden="true"' not in support_source

def test_appkit_contract_block_preserves_canonical_rail_helpers():
    block = takyon_core._subuser_app_kit_contract_block(  # type: ignore[attr-defined]
        {
            "runtime_features": ["auth", "account", "checkout", "generate"],
            "routes": ["/", "/app"],
            "metadata": {
                "subuser_app": {
                    "app_mode": "standard_saas",
                    "subscription_style": "monthly",
                }
            },
        }
    )

    assert "AppKit-owned rail helpers are canonical behavior, not inspiration." in block
    assert "src/lib/takyon.ts" in block and "src/lib/hooks.ts" in block
    assert "shared client/hooks" in block
    assert "useViewerAccess()" in block
    assert "resolveViewerCta()" in block
    assert "Landing and pricing CTAs must derive from real runtime session/account state" in block
    assert "do not spend bootstrap/design time" in block.lower()
    assert "createActionRunner" not in block
    assert "useActionRunner" not in block


def test_appkit_contract_block_uses_vite_scaffold_surface_when_frontend_stack_is_pinned():
    block = takyon_core._subuser_app_kit_contract_block(  # type: ignore[attr-defined]
        {
            "runtime_features": ["auth", "account", "checkout", "generate"],
            "routes": ["/", "/app"],
            "metadata": {
                "subuser_app": {
                    "app_mode": "standard_saas",
                    "subscription_style": "monthly",
                    "frontend_stack": "vite_react_ts",
                }
            },
        }
    )

    assert "src/lib/hooks.ts" in block
    assert "src/screens/support.tsx" in block
    assert "src/screens/app-layout.tsx" in block
    assert "starter-context.js" not in block
    assert "src/app/app/(product)/root.js" not in block
    assert "createActionRunner" not in block
    assert "useActionRunner" not in block


def test_worker_contract_block_states_positive_obligation_and_facts():
    block = takyon_core._subuser_app_worker_contract_block(  # type: ignore[attr-defined]
        {
            "runtime_features": ["auth", "account", "checkout"],
            "routes": ["/", "/app"],
            "metadata": {
                "subuser_app": {},
                "customer_experience": {
                    "required_routes": ["/", "/app"],
                },
            },
        },
        plans_configured=True,
    )

    # The minimized worker contract is a short positive obligation, not a fear wall.
    assert "Your overriding obligation is that the product's primary job works for real." in block
    assert "Use the shared runtime client and the declared shared rails already present in this workspace." in block
    assert "fail truthfully with the exact blocker" in block
    # Factual contract context is still injected.
    assert "Declared runtime-backed features for this app: auth, account, checkout" in block
    assert "src/screens/" in block
    assert "Support-route screens live in `src/screens/support.tsx`" in block
    assert "createActionRunner(name)" in block
    assert "product/site/actions/<name>.ts" in block
    assert "default-export async `(payload, ctx) => result`" in block
    assert "ctx IS the runtime client" in block
    assert "Never fake or simulate an action result client-side" in block
    # The deleted app-shape taxonomy and the old per-rail fear prose are gone.
    assert "App mode:" not in block
    assert "Subscription style:" not in block
    assert "do not fake browser-only sessions" not in block
    assert "Do not use localStorage" not in block


def test_worker_contract_block_uses_vite_lane_guidance():
    block = takyon_core._subuser_app_worker_contract_block(  # type: ignore[attr-defined]
        {
            "runtime_features": ["auth", "account", "checkout"],
            "routes": ["/", "/app"],
            "metadata": {
                "subuser_app": {},
                "customer_experience": {
                    "required_routes": ["/", "/app"],
                },
            },
        },
        plans_configured=True,
    )

    assert "src/screens/" in block
    assert "src/screens/app-layout.tsx" in block
    assert "src/screens/support.tsx" in block
    assert "next.config.js" not in block
    assert "src/app/app/(product)/" not in block
    assert "createActionRunner(name)" in block
    assert "product/site/actions/<name>.ts" in block
    assert "default-export async `(payload, ctx) => result`" in block


def test_default_surface_contract_omits_design_brief(tmp_path: Path):
    store = takyon_core.TakyonStore(tmp_path)

    class _FakeCursor:
        def fetchone(self):
            return None

    class _FakeConn:
        def execute(self, *_args, **_kwargs):
            return _FakeCursor()

    surface = store._stored_app_surface_contract(_FakeConn(), "plannerly")  # type: ignore[attr-defined]

    assert "design_brief_path" not in surface


def test_probe_product_public_url_retries_transient_tls_bootstrap(monkeypatch):
    sleeps: list[int] = []
    attempts = {"count": 0}

    class _Response:
        status = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size: int = -1):
            return b"<!doctype html><html><body>ok</body></html>"

    def _fake_urlopen(_request, timeout=0):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise URLError("[SSL: TLSV1_ALERT_INTERNAL_ERROR] tlsv1 alert internal error (_ssl.c:1000)")
        return _Response()

    monkeypatch.setattr(takyon_core, "_product_deploy_dry_run", lambda: False)
    monkeypatch.setattr(takyon_core, "_product_public_probe_enabled", lambda: True)
    monkeypatch.setattr(takyon_core.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(takyon_core.time, "sleep", sleeps.append)

    ok, blocker = takyon_core._probe_product_public_url("https://example.fourmanifold.com/")  # type: ignore[attr-defined]

    assert ok is True
    assert blocker == ""
    assert attempts["count"] == 3
    assert sleeps == [2, 4]


def test_probe_product_public_url_blocks_raw_source_dev_entry(monkeypatch):
    class _Response:
        status = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size: int = -1):
            return (
                b"<!doctype html><html><body><div id='root'></div>"
                b"<script type='module' src='/src/main.tsx'></script></body></html>"
            )

    monkeypatch.setattr(takyon_core, "_product_deploy_dry_run", lambda: False)
    monkeypatch.setattr(takyon_core, "_product_public_probe_enabled", lambda: True)
    monkeypatch.setattr(takyon_core.urllib.request, "urlopen", lambda _request, timeout=0: _Response())
    monkeypatch.setattr(takyon_core.time, "sleep", lambda _seconds: None)

    ok, blocker = takyon_core._probe_product_public_url("https://example.fourmanifold.com/")  # type: ignore[attr-defined]

    assert ok is False
    assert "raw source entry" in blocker


def test_probe_product_public_url_threads_publish_probe_token(monkeypatch):
    seen: list[str] = []

    class _Response:
        status = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size: int = -1):
            return b"<!doctype html><html><body>ok</body></html>"

    def _fake_urlopen(request, timeout=0):
        seen.append(str(getattr(request, "full_url", "")))
        return _Response()

    monkeypatch.setattr(takyon_core, "_product_deploy_dry_run", lambda: False)
    monkeypatch.setattr(takyon_core, "_product_public_probe_enabled", lambda: True)
    monkeypatch.setattr(takyon_core.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(takyon_core.time, "sleep", lambda _seconds: None)

    ok, blocker = takyon_core._probe_product_public_url_with_token(  # type: ignore[attr-defined]
        "https://example.fourmanifold.com/",
        publish_probe_token="probe-token-123",
    )

    assert ok is True
    assert blocker == ""
    assert seen == ["https://example.fourmanifold.com/?__takyon_publish_probe=probe-token-123"]


def test_merge_subuser_app_metadata_threads_frontend_stack():
    metadata = takyon_core._merge_subuser_app_metadata(  # type: ignore[attr-defined]
        {},
        runtime_features=["auth"],
        frontend_stack="vite-react-ts",
    )
    assert metadata["subuser_app"]["frontend_stack"] == "vite_react_ts"

    preserved = takyon_core._merge_subuser_app_metadata(  # type: ignore[attr-defined]
        metadata,
        runtime_features=["auth"],
    )
    assert preserved["subuser_app"]["frontend_stack"] == "vite_react_ts"

    invalid = takyon_core._merge_subuser_app_metadata(  # type: ignore[attr-defined]
        {},
        runtime_features=["auth"],
        frontend_stack="svelte",
    )
    assert "frontend_stack" not in invalid["subuser_app"]


def test_surface_shape_defaults_frontend_stack_to_vite():
    shape = takyon_core._surface_subuser_app_shape(  # type: ignore[attr-defined]
        {"metadata": {"subuser_app": {}}}
    )
    assert shape["frontend_stack"] == "vite_react_ts"


def test_bootstrap_default_runtime_features_stay_pinned():
    # The bootstrap access shell must stay pinned to the shared auth/account/profile/
    # checkout shell so removing the old taxonomy never silently changes it.
    assert takyon_core.DEFAULT_BOOTSTRAP_ACCESS_SHELL_RUNTIME_FEATURES == (
        "auth",
        "account",
        "profile",
        "checkout",
    )


def test_bootstrap_access_shell_is_effective_until_workflow_declares_real_rails():
    surface = {
        "routes": ["/", "/app", "/app/profile"],
        "metadata": {
            "customer_experience": {
                "required_routes": ["/", "/app"],
            }
        },
    }

    assert takyon_core._surface_runtime_features(surface) == []  # type: ignore[attr-defined]
    assert takyon_core._surface_effective_runtime_features(surface) == [  # type: ignore[attr-defined]
        "auth",
        "account",
        "profile",
        "checkout",
    ]

    payload = takyon_core._subuser_surface_context_payload(  # type: ignore[attr-defined]
        surface,
        slug="plannerly",
    )
    assert payload["runtimeFeatures"] == [
        "auth",
        "account",
        "profile",
        "checkout",
    ]

    block = takyon_core._runtime_ui_contract_block(surface)  # type: ignore[attr-defined]
    assert "Runtime-backed features available in this shell: auth, account, profile, checkout" in block


def test_product_workflow_actions_survive_shape_normalization():
    # product_workflow.actions must round-trip through the shape normalizer
    # independently of the deleted app_mode/subscription_style/api_mode taxonomy.
    surface = {
        "metadata": {
            "product_workflow": {
                "actions": [{"name": "sync-data", "trigger": "http"}],
                "primary_job": "Sync the user's data.",
            }
        }
    }
    workflow = takyon_core._surface_product_workflow_shape(surface)  # type: ignore[attr-defined]
    assert workflow["actions"] == [{"name": "sync-data", "trigger": "http"}]


def test_partial_product_workflow_no_longer_drives_worker_doctrine():
    workflow = {
        "primary_job": "Help the user save a plan.",
        "core_loop": {
            "input": "Enter a goal.",
            "action": "Generate a starter plan.",
            "result": "Show a saved plan draft.",
        },
    }
    surface = {
        "runtime_features": ["auth", "account", "records"],
        "routes": ["/", "/app"],
        "metadata": {
            "customer_experience": {
                "required_routes": ["/", "/app"],
            },
            "product_workflow": workflow,
        },
    }

    assert takyon_core._product_workflow_is_mvp_complete(workflow) is False  # type: ignore[attr-defined]

    block = takyon_core._subuser_app_worker_contract_block(  # type: ignore[attr-defined]
        surface,
        plans_configured=False,
    )
    assert "partial product workflow" not in block
    assert "workflow `workflow_pending`" not in block
    assert "MVP-complete product workflow for the gated app." not in block

    assert takyon_core._validate_product_workflow_contract(  # type: ignore[attr-defined]
        surface=surface,
        runtime_features=["auth", "account", "records"],
        product_workflow=workflow,
    ) is None


def test_app_shell_signal_derived_from_rails_not_taxonomy():
    # App-shell intent is derived from real declared signals (access/AI rails or an
    # explicit in-app workflow route), not from app-shape taxonomy.
    assert (
        takyon_core._surface_shape_requires_app_shell(  # type: ignore[attr-defined]
            runtime_features=["auth", "account", "actions"],
        )
        is True
    )
    assert (
        takyon_core._surface_shape_requires_app_shell(  # type: ignore[attr-defined]
            runtime_features=[],
            required_routes=["/"],
        )
        is False
    )
    assert (
        takyon_core._surface_shape_requires_app_shell(  # type: ignore[attr-defined]
            runtime_features=[],
            required_routes=["/", "/app"],
        )
        is True
    )


def test_bootstrap_access_shell_seed_forces_canonical_shell_for_real_app_surfaces():
    # On a fresh seed of a real app surface, the access shell normalizes to the pinned
    # auth/account/profile/checkout set — without any app-shape taxonomy.
    forced = takyon_core._canonical_bootstrap_access_runtime_features(  # type: ignore[attr-defined]
        ["actions"],
        bootstrap_seed=True,
        app_shell_required=True,
    )
    assert forced == ["auth", "account", "profile", "checkout"]

    # Not a fresh seed → declared rails are left untouched.
    assert takyon_core._canonical_bootstrap_access_runtime_features(  # type: ignore[attr-defined]
        ["records"],
        bootstrap_seed=False,
        app_shell_required=True,
    ) == ["records"]

    # No app-shell signal (landing-style) → nothing is forced even on a fresh seed.
    assert takyon_core._canonical_bootstrap_access_runtime_features(  # type: ignore[attr-defined]
        [],
        bootstrap_seed=True,
        app_shell_required=False,
    ) == []


def test_bootstrap_conversion_model_collapses_free_copy_to_monthly():
    assert (
        takyon_core._canonical_bootstrap_conversion_model(  # type: ignore[attr-defined]
            "free tier (5 docs) -> $9/mo paid plan",
            bootstrap_seed=True,
            app_shell_required=True,
        )
        == "monthly subscription"
    )
    # Real conversion copy is preserved.
    assert (
        takyon_core._canonical_bootstrap_conversion_model(  # type: ignore[attr-defined]
            "paid monthly plan",
            bootstrap_seed=True,
            app_shell_required=True,
        )
        == "paid monthly plan"
    )
