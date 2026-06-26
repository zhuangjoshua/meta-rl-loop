"""
Takyon Meta Pixel — business_meta_pixel_* handlers + definitions.

Per skills/takyon/HANDOFF (Tool Template). Drop handlers + TAKYON_TOOL_DEFINITIONS into
hermes-agent-main/plugins/takyon/core.py; tests into tests/plugins/test_takyon_plugin.py. NEVER push to
the read-only repo.

Clones the Umami `analytics` rail: ONE shared pixel_id (runtime config analytics.meta_pixel.*), snippet
baked into the build <head> (favicon-injector pattern so it reaches the R2 edge), per-business attribution
via a custom conversion keyed on <slug>.coscale.app. Secrets via Safebox, never os.environ.

Reuses existing helpers: _commit_tool, _schema, _BUSINESS_PROP, _IDEMPOTENCY_PROP, _REASON_PROP,
_ACTOR_PROP, _business_scope, _product_publish_target, _business_upsert_app_surface_contract path,
_meta_graph (Graph fallback), safebox.first_env_backed_value, and the new helpers in
implementation-notes.md (_meta_pixel_snippet, _inject_meta_pixel, _meta_pixel_ensure_custom_conversion,
_meta_pixel_probe_installed, _meta_pixel_dataset_health).
"""

from typing import Any


# --- business_meta_pixel_ensure ---------------------------------------------
def handle_business_meta_pixel_ensure(args: dict, **_: Any) -> str:
    """Lazily make this business's site carry the shared pixel + a per-business custom conversion.

    Idempotent. Steps (live): (1) require analytics.meta_pixel.pixel_id is configured, else BLOCK (the
    shared pixel + domain verification is one-time manual setup — do not fake). (2) set
    metadata.meta_pixel.enabled=true on the surface contract → the build-time injector bakes
    _meta_pixel_snippet into index.html <head> (reaches both VPS + R2), then republish. (3) ensure the
    per-business custom conversion (MCP write if present, else Graph POST /customconversions with a URL
    rule on <slug>.coscale.app/<conversion_path>); store custom_conversion_id in surface metadata.
    (4) verify (see business_meta_pixel_verify) and write metrics/meta-pixel/<slug>/ensure.json. Fail
    truthfully if any proof is missing.
    """
    return _call_creative_runtime_gateway(  # noqa: F821  (authority route holds the pixel_id/CAPI secret)
        "meta-pixel-ensure",
        {
            "business": args.get("business"),
            "scope": args.get("scope") or _business_scope(args),  # noqa: F821
            "conversion_path": args.get("conversion_path") or "/",   # URL-rule path for the custom conversion
            "custom_event_type": args.get("custom_event_type") or "LEAD",
            "idempotency_key": args.get("idempotency_key"),
        },
    )


# --- business_meta_pixel_verify ---------------------------------------------
def handle_business_meta_pixel_verify(args: dict, **_: Any) -> str:
    """Read-only health check used by the meta-ads preflight.

    Two proofs: (A) fetch https://<slug>.coscale.app/ and assert the pixel snippet is in the served HTML
    <head> on BOTH the VPS and R2 paths; (B) read dataset health via the MCP (ads_get_datasets,
    ads_get_dataset_stats/_quality) and confirm this business's custom conversion exists
    (ads_get_customconversions). Returns {installed_vps, installed_r2, dataset_ok, custom_conversion_ok,
    dataset_id, custom_conversion_id, emq, last_event, ok}; ok only when both proofs pass. Writes
    metrics/meta-pixel/<slug>/preflight.json. Never claims functional from source alone.
    """
    return _call_creative_runtime_gateway(  # noqa: F821
        "meta-pixel-verify",
        {
            "business": args.get("business"),
            "scope": args.get("scope") or _business_scope(args),  # noqa: F821
            "idempotency_key": args.get("idempotency_key"),
        },
    )


# --- business_read_meta_pixel -----------------------------------------------
def handle_business_read_meta_pixel(args: dict, **_: Any) -> str:
    """Report current pixel status from surface metadata + the latest verify receipt (no external calls)."""
    return _call_creative_runtime_gateway(  # noqa: F821
        "meta-pixel-read",
        {
            "business": args.get("business"),
            "scope": args.get("scope") or _business_scope(args),  # noqa: F821
        },
    )


# --- TAKYON_TOOL_DEFINITIONS entries (append to core.py) ---------------------
TAKYON_META_PIXEL_DEFINITIONS = [
    {
        "name": "business_meta_pixel_ensure",
        "description": "Ensure this business's site carries the shared Meta pixel + a per-business custom conversion (lazy; run before the first Meta ad).",
        "handler": handle_business_meta_pixel_ensure,
        "schema": _schema(  # noqa: F821
            "business_meta_pixel_ensure",
            "Install the shared pixel on the site and create the per-business custom conversion.",
            {
                "business": _BUSINESS_PROP,  # noqa: F821
                "conversion_path": {"type": "string"},     # URL-rule path, e.g. "/thank-you"
                "custom_event_type": {"type": "string"},   # LEAD | PURCHASE | ...
                "idempotency_key": _IDEMPOTENCY_PROP,  # noqa: F821
                "reason": _REASON_PROP,  # noqa: F821
                "actor": _ACTOR_PROP,  # noqa: F821
            },
            ["business", "idempotency_key"],
        ),
    },
    {
        "name": "business_meta_pixel_verify",
        "description": "Verify a business's pixel is installed (both serving paths) and the dataset is receiving events; confirm its custom conversion exists.",
        "handler": handle_business_meta_pixel_verify,
        "schema": _schema(  # noqa: F821
            "business_meta_pixel_verify",
            "Health-check a business's Meta pixel and custom conversion.",
            {
                "business": _BUSINESS_PROP,  # noqa: F821
                "idempotency_key": _IDEMPOTENCY_PROP,  # noqa: F821
                "reason": _REASON_PROP,  # noqa: F821
                "actor": _ACTOR_PROP,  # noqa: F821
            },
            ["business", "idempotency_key"],
        ),
    },
    {
        "name": "business_read_meta_pixel",
        "description": "Report a business's current Meta pixel status from surface metadata and the latest verify receipt.",
        "handler": handle_business_read_meta_pixel,
        "schema": _schema(  # noqa: F821
            "business_read_meta_pixel",
            "Read a business's Meta pixel status.",
            {"business": _BUSINESS_PROP},  # noqa: F821
            ["business"],
        ),
    },
]
