"""
Takyon Meta Ads v2 — business_meta_* tool handlers + definitions.

Authored per skills/takyon/HANDOFF (Tool Template). Drop the handlers + TAKYON_TOOL_DEFINITIONS
entries into hermes-agent-main/plugins/takyon/core.py; tests in TOOLS/test_takyon_meta_ads_v2.py go to
tests/plugins/test_takyon_plugin.py. NEVER push to the read-only repo — hand-apply.

The five tools keep their v1 names/signatures. Internals change Composio -> Meta MCP (see
implementation-notes.md). business_meta_ad_evaluate is new in v2.

Reuses existing core.py helpers: _commit_tool, _schema, _BUSINESS_PROP, _IDEMPOTENCY_PROP, _REASON_PROP,
_ACTOR_PROP, _business_scope, _call_creative_runtime_gateway, _store(), business_credits, and
safebox.first_env_backed_value. Secrets resolve only in the authority route, never os.environ.
"""

from typing import Any

# Pinned default campaign shape (Quick Reference in SKILL.md): Traffic -> Website clicks, CBO.
_META_DEFAULTS = {
    "objective": "OUTCOME_TRAFFIC",
    "optimization_goal": "LINK_CLICKS",
    "billing_event": "IMPRESSIONS",
    "destination_type": "WEBSITE",
    "budget_mode": "CBO",
    "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
    "activate": False,  # staged paused unless launch intent is live
}


# --- business_meta_ad_launch -------------------------------------------------
def handle_business_meta_ad_launch(args: dict, **_: Any) -> str:
    """Preflight + bounded launch (or manual-handoff packaging).

    Defaults unfilled options from _META_DEFAULTS, then routes to the creative-gateway authority, which
    (live mode) reserves the channel credit, resolves the asset to an R2 public URL, builds the creative
    (image_url | /advideos->video_id), creates campaign->adset->ad via the Meta MCP, activates if live,
    settles credit, and writes plan.json/receipt.json/actions/<id>.json. Test mode suppresses to a local
    receipt. Body mirrors v1; only the gateway internals swap Composio->MCP.

    Preflight also runs business_meta_pixel_verify: conversion objectives BLOCK without a functional pixel,
    and for conversion campaigns the gateway injects the custom_conversion_id (from surface
    metadata.meta_pixel) into promoted_object. Traffic (the default) proceeds but warns if no pixel.
    """
    plan = {**_META_DEFAULTS, **{k: v for k, v in args.items() if v is not None}}
    return _call_creative_runtime_gateway(  # noqa: F821 (defined in core.py)
        "meta-launch",
        {
            "business": args.get("business"),
            "scope": args.get("scope") or _business_scope(args),  # noqa: F821
            "preflight": bool(args.get("preflight")),
            "mode": args.get("mode"),
            "idempotency_key": args.get("idempotency_key"),
            "plan": plan,  # asset_kind, ad_image_path|ad_video_path, copy{message,headline,
                           # description,call_to_action_type}, objective, optimization_goal,
                           # billing_event, budget_mode/budget_kind/budget_amount_cents, targeting,
                           # destination_type, link, page_id, instagram_user_id, schedule, activate
        },
    )


# --- business_meta_ad_control ------------------------------------------------
def handle_business_meta_ad_control(args: dict, **_: Any) -> str:
    """activate | pause | set_budget on an existing object.

    set_budget reads the object's budget_mode from plan.json/receipt.json (or queries the entity) to
    pick campaign_daily_budget (CBO) vs daily_budget (ABO) — never inferred from level. No delete: a
    delete request returns a truthful 'paused; delete in Ads Manager UI' result.
    """
    return _call_creative_runtime_gateway(  # noqa: F821
        "meta-control",
        {
            "business": args.get("business"),
            "scope": args.get("scope") or _business_scope(args),  # noqa: F821
            "action": args.get("action"),          # activate | pause | set_budget
            "level": args.get("level"),            # campaign | adset | ad
            "object_id": args.get("object_id"),
            "value": args.get("value"),            # int, account minor units (cents) for set_budget
            "idempotency_key": args.get("idempotency_key"),
        },
    )


# --- business_meta_ad_insights_sync ------------------------------------------
def handle_business_meta_ad_insights_sync(args: dict, **_: Any) -> str:
    """Pull Meta delivery metrics and append an attributed row to insights.jsonl.

    Writes metrics/meta-ads/<slug>/{insights.jsonl, syncs/<id>.json}. Append-only; the aggregator dedups
    by (level, object_id, date_start, date_stop), latest created_at wins. Records ad-platform metrics
    only — no invented attribution. See implementation-notes.md sect. 3 for the totals shape/fields.
    """
    return _call_creative_runtime_gateway(  # noqa: F821
        "meta-insights-sync",
        {
            "business": args.get("business"),
            "scope": args.get("scope") or _business_scope(args),  # noqa: F821
            "level": args.get("level"),                # campaign | adset | ad
            "object_id": args.get("object_id"),
            "date_preset": args.get("date_preset"),    # XOR time_range
            "time_range": args.get("time_range"),
            "breakdowns": args.get("breakdowns"),
            "idempotency_key": args.get("idempotency_key"),
        },
    )


# --- business_meta_ad_evaluate (NEW) -----------------------------------------
def handle_business_meta_ad_evaluate(args: dict, **_: Any) -> str:
    """Judge good/bad/neutral with a recommended action.

    Orchestration only — every threshold, the learning/fatigue/attribution rules, and the verdict->action
    map live in references/benchmarks.md (single source). Flow: get window metrics (reuse a <24h matching
    sync, else trigger insights_sync) + MCP signals (trend/anomaly/benchmark/opportunity); learning guard;
    score vs benchmarks (business targets override the CPA/ROAS baseline); fatigue (WoW, skip if prior
    week missing); attribution sanity; consolidate scope = parent campaign's ad sets. Writes
    metrics/meta-ads/<slug>/evaluations/<id>.json. Recommends only; any action runs through
    business_meta_ad_control under the spend gate.
    """
    return _call_creative_runtime_gateway(  # noqa: F821
        "meta-evaluate",
        {
            "business": args.get("business"),
            "scope": args.get("scope") or _business_scope(args),  # noqa: F821
            "level": args.get("level"),
            "object_id": args.get("object_id"),
            "window": args.get("window") or "last_7d",
            "targets": args.get("targets"),  # optional {cpa_usd, roas} override
            "idempotency_key": args.get("idempotency_key"),
        },
    )


# --- business_meta_ad_bind_manual_launch -------------------------------------
def handle_business_meta_ad_bind_manual_launch(args: dict, **_: Any) -> str:
    """Bind real Meta ids back into Takyon after a manual launch. MCP-agnostic; commit-only."""
    operation = {
        "action": "meta.bind_manual_launch",
        "business": args.get("business"),
        "scope": args.get("scope") or _business_scope(args),  # noqa: F821
        "ids": {
            "campaign_id": args.get("campaign_id"),
            "adset_id": args.get("adset_id"),
            "ad_id": args.get("ad_id"),
        },
    }
    return _commit_tool(args, operation, scope=operation["scope"])  # noqa: F821


# --- TAKYON_TOOL_DEFINITIONS entries (append to the list in core.py) ---------
TAKYON_META_ADS_V2_DEFINITIONS = [
    {
        "name": "business_meta_ad_launch",
        "description": "Preflight or launch/stage a bounded Meta campaign (Campaign->AdSet->Ad) from a chosen asset via the Meta MCP. Defaults to Traffic->Website clicks, CBO.",
        "handler": handle_business_meta_ad_launch,
        "schema": _schema(  # noqa: F821
            "business_meta_ad_launch",
            "Preflight or launch a bounded Meta ad from a chosen image/video asset.",
            {
                "business": _BUSINESS_PROP,  # noqa: F821
                "preflight": {"type": "boolean"},
                "mode": {"type": "string", "enum": ["test", "live"]},
                "asset_kind": {"type": "string", "enum": ["image", "video"]},
                "ad_image_path": {"type": "string"},
                "ad_video_path": {"type": "string"},
                "copy": {"type": "object"},  # {message, headline, description, call_to_action_type}
                "objective": {"type": "string"},
                "optimization_goal": {"type": "string"},
                "billing_event": {"type": "string"},
                "budget_mode": {"type": "string", "enum": ["CBO", "ABO"]},
                "budget_kind": {"type": "string", "enum": ["daily", "lifetime"]},
                "budget_amount_cents": {"type": "integer"},
                "targeting": {"type": "object"},
                "destination_type": {"type": "string"},
                "link": {"type": "string"},
                "page_id": {"type": "string"},
                "instagram_user_id": {"type": "string"},
                "schedule": {"type": "object"},  # {start_time, end_time}
                "activate": {"type": "boolean"},
                "idempotency_key": _IDEMPOTENCY_PROP,  # noqa: F821
                "reason": _REASON_PROP,  # noqa: F821
                "actor": _ACTOR_PROP,  # noqa: F821
            },
            ["business", "idempotency_key"],
        ),
    },
    {
        "name": "business_meta_ad_control",
        "description": "Activate, pause, or set/change the budget of an existing Meta object. No delete (MCP pauses; delete in Ads Manager UI).",
        "handler": handle_business_meta_ad_control,
        "schema": _schema(  # noqa: F821
            "business_meta_ad_control",
            "Activate, pause, or set budget on a Meta campaign/adset/ad.",
            {
                "business": _BUSINESS_PROP,  # noqa: F821
                "action": {"type": "string", "enum": ["activate", "pause", "set_budget"]},
                "level": {"type": "string", "enum": ["campaign", "adset", "ad"]},
                "object_id": {"type": "string"},
                "value": {"type": "integer"},  # cents, for set_budget
                "idempotency_key": _IDEMPOTENCY_PROP,  # noqa: F821
                "reason": _REASON_PROP,  # noqa: F821
                "actor": _ACTOR_PROP,  # noqa: F821
            },
            ["business", "action", "level", "object_id", "idempotency_key"],
        ),
    },
    {
        "name": "business_meta_ad_insights_sync",
        "description": "Sync Meta delivery metrics for a campaign/adset/ad into metrics/meta-ads/<slug>/insights.jsonl as an attributed time-series.",
        "handler": handle_business_meta_ad_insights_sync,
        "schema": _schema(  # noqa: F821
            "business_meta_ad_insights_sync",
            "Sync Meta delivery metrics into the business metrics folder.",
            {
                "business": _BUSINESS_PROP,  # noqa: F821
                "level": {"type": "string", "enum": ["campaign", "adset", "ad"]},
                "object_id": {"type": "string"},
                "date_preset": {"type": "string"},
                "time_range": {"type": "object"},  # {since, until}
                "breakdowns": {"type": "array", "items": {"type": "string"}},
                "idempotency_key": _IDEMPOTENCY_PROP,  # noqa: F821
                "reason": _REASON_PROP,  # noqa: F821
                "actor": _ACTOR_PROP,  # noqa: F821
            },
            ["business", "level", "object_id", "idempotency_key"],
        ),
    },
    {
        "name": "business_meta_ad_evaluate",
        "description": "Evaluate a Meta campaign/adset/ad as good/bad/neutral with a recommended action, using benchmarks + MCP insight signals.",
        "handler": handle_business_meta_ad_evaluate,
        "schema": _schema(  # noqa: F821
            "business_meta_ad_evaluate",
            "Judge Meta ad performance and recommend an action.",
            {
                "business": _BUSINESS_PROP,  # noqa: F821
                "level": {"type": "string", "enum": ["campaign", "adset", "ad"]},
                "object_id": {"type": "string"},
                "window": {"type": "string"},  # default last_7d
                "targets": {"type": "object"},  # {cpa_usd, roas}
                "idempotency_key": _IDEMPOTENCY_PROP,  # noqa: F821
                "reason": _REASON_PROP,  # noqa: F821
                "actor": _ACTOR_PROP,  # noqa: F821
            },
            ["business", "level", "object_id", "idempotency_key"],
        ),
    },
    {
        "name": "business_meta_ad_bind_manual_launch",
        "description": "Bind real Meta campaign/adset/ad ids into Takyon after a manual launch.",
        "handler": handle_business_meta_ad_bind_manual_launch,
        "schema": _schema(  # noqa: F821
            "business_meta_ad_bind_manual_launch",
            "Bind real Meta ids after a manual launch.",
            {
                "business": _BUSINESS_PROP,  # noqa: F821
                "campaign_id": {"type": "string"},
                "adset_id": {"type": "string"},
                "ad_id": {"type": "string"},
                "idempotency_key": _IDEMPOTENCY_PROP,  # noqa: F821
                "reason": _REASON_PROP,  # noqa: F821
                "actor": _ACTOR_PROP,  # noqa: F821
            },
            ["business", "campaign_id", "idempotency_key"],
        ),
    },
]
