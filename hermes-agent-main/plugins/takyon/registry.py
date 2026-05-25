"""Skill and tool registry metadata for the Takyon plugin."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


TAKYON_PRIORITY_BANDS: dict[str, dict[str, str]] = {
    "p0_control": {
        "label": "Control and safety",
        "description": "Operator commands, kill switches, scope isolation, credential failures, budget violations, and cleanup decisions that protect the system.",
    },
    "p1_ceo": {
        "label": "CEO direction",
        "description": "Manual CEO commands, scheduled wakeups, strategic choices, plan changes, and recovery decisions that steer a business.",
    },
    "p2_growth": {
        "label": "Growth and revenue",
        "description": "Product, distribution, pricing, conversion, checkout, and revenue work with direct business impact.",
    },
    "p3_learning": {
        "label": "Learning and assets",
        "description": "Research, creative drafts, outreach assets, evidence capture, and durable business memory.",
    },
    "p4_maintenance": {
        "label": "Maintenance",
        "description": "Low-urgency status review, conservative garbage collection, organization, and archival.",
    },
}


TAKYON_CATEGORIES: dict[str, dict[str, str]] = {
    "registry": {"label": "Registry", "description": "Discover Takyon skills, tools, categories, and priority bands."},
    "ceo": {"label": "CEO", "description": "Top-level business direction and orchestration."},
    "read": {"label": "Read", "description": "Inspect businesses, workspaces, files, and current state."},
    "workspace": {"label": "Workspace", "description": "Create isolated business, campaign, product, sales, or research workspaces."},
    "files": {"label": "Files", "description": "Write or patch business-scoped files."},
    "memory": {"label": "Memory", "description": "Improve durable per-business strategy and learning."},
    "pulse": {"label": "Pulse", "description": "Calculate compact business metrics, deltas, and current strategic pulse."},
    "budget": {"label": "Budget", "description": "Allocate or reserve money under business caps."},
    "app": {"label": "App runtime", "description": "Product customer auth, sessions, entitlements, checkout, subscriptions, revenue, usage budgets, and surface contracts."},
    "queue": {"label": "Queue", "description": "Record guarded requests for external side effects."},
    "agent": {"label": "Agent", "description": "Run bounded business-scoped agent workers with durable receipts."},
    "conversation": {"label": "Conversation", "description": "Track business-owned outreach, forum, support, and customer replies."},
    "audit": {"label": "Audit", "description": "Record events, evidence, receipts, and agent runs."},
    "control": {"label": "Control", "description": "Pause, resume, or kill global, business, workspace, job, and agent scopes."},
    "cron": {"label": "Cron", "description": "Schedule CEO wake/sleep loops."},
    "maintenance": {"label": "Maintenance", "description": "Clean old ephemeral rows without deleting protected state."},
    "product": {"label": "Product", "description": "Shape product, offer, checkout, and build plans."},
    "research": {"label": "Research", "description": "Gather market, customer, competitor, channel, and pricing evidence."},
    "pricing": {"label": "Pricing", "description": "Improve packaging, offer, checkout, revenue, and margin."},
    "distribution": {"label": "Distribution", "description": "Run traffic, launch, channel, ads, content, and social campaigns."},
    "creative": {"label": "Creative", "description": "Draft ad angles, copy, hooks, and creative specs."},
    "outreach": {"label": "Outreach", "description": "Create lead, outbound, partner, and sales motions."},
    "conversion": {"label": "Conversion", "description": "Improve funnels when traffic does not convert or monetize."},
    "recovery": {"label": "Recovery", "description": "Recover failed, stale, blocked, or contradictory work."},
}


TAKYON_TOOL_REGISTRY: list[dict[str, Any]] = [
    {
        "name": "business_registry",
        "category": "registry",
        "priority_bands": ["p0_control", "p1_ceo", "p2_growth", "p3_learning", "p4_maintenance"],
        "effect": "read_only",
        "purpose": "Inspect the canonical Takyon skill/tool category and priority-band map plus runtime capability snapshots such as video_generation openai/sora availability.",
    },
    {
        "name": "business_list_businesses",
        "category": "read",
        "priority_bands": ["p1_ceo", "p4_maintenance"],
        "effect": "read_only",
        "purpose": "List businesses and global control states.",
    },
    {
        "name": "business_read_business",
        "category": "read",
        "priority_bands": ["p1_ceo", "p2_growth", "p3_learning", "p4_maintenance"],
        "effect": "read_only",
        "purpose": "Inspect one business summary, brain index, workspaces, controls, ledger, jobs, and events.",
    },
    {
        "name": "business_read_file",
        "category": "read",
        "priority_bands": ["p1_ceo", "p2_growth", "p3_learning", "p4_maintenance"],
        "effect": "read_only",
        "purpose": "Read a business-scoped file.",
    },
    {
        "name": "business_calculate_pulse",
        "category": "pulse",
        "priority_bands": ["p1_ceo", "p2_growth", "p3_learning", "p4_maintenance"],
        "effect": "read_only",
        "purpose": "Calculate bounded deterministic business metrics and deltas from canonical state without mutating anything.",
    },
    {
        "name": "business_check_runtime_capabilities",
        "category": "control",
        "priority_bands": ["p0_control", "p1_ceo", "p2_growth", "p4_maintenance"],
        "effect": "guarded_local_effect",
        "purpose": "Inspect local runtimes, package managers, and command capabilities; optionally provision supported local runtimes through guarded setup before product or worker tasks.",
    },
    {
        "name": "business_list_files",
        "category": "read",
        "priority_bands": ["p1_ceo", "p3_learning", "p4_maintenance"],
        "effect": "read_only",
        "purpose": "List files and directories inside a business scope.",
    },
    {
        "name": "business_upsert_business",
        "category": "workspace",
        "priority_bands": ["p1_ceo", "p2_growth"],
        "effect": "durable_write",
        "purpose": "Create or update a business, goal, metadata, and optional budget cap.",
    },
    {
        "name": "business_delete_business",
        "category": "control",
        "priority_bands": ["p0_control", "p4_maintenance"],
        "effect": "external_side_effect",
        "purpose": "Dry-run or permanently delete one business, including filesystem, CEO cron jobs, and its Vercel/fourmanifold.com subdomain.",
    },
    {
        "name": "business_set_mode",
        "category": "control",
        "priority_bands": ["p0_control", "p1_ceo"],
        "effect": "guarded_write",
        "purpose": "Set a business to live or test mode; test mode keeps product/website work and cron active while suppressing outreach, spend, and money movement.",
    },
    {
        "name": "business_set_work_focus",
        "category": "control",
        "priority_bands": ["p0_control", "p1_ceo"],
        "effect": "guarded_write",
        "purpose": "Set a business work focus to all, marketing-only, or product-only so CEO turns and cron wakes stay in that lane.",
    },
    {
        "name": "business_create_workspace",
        "category": "workspace",
        "priority_bands": ["p1_ceo", "p2_growth", "p3_learning"],
        "effect": "durable_write",
        "purpose": "Create isolated campaign, product, sales, research, or arbitrary business workspaces.",
    },
    {
        "name": "business_write_file",
        "category": "files",
        "priority_bands": ["p2_growth", "p3_learning", "p4_maintenance"],
        "effect": "durable_write",
        "purpose": "Write or append a file inside a business workspace.",
    },
    {
        "name": "business_patch_file",
        "category": "files",
        "priority_bands": ["p2_growth", "p3_learning", "p4_maintenance"],
        "effect": "durable_write",
        "purpose": "Patch a business-scoped file by replacing one text fragment.",
    },
    {
        "name": "business_record_memory",
        "category": "memory",
        "priority_bands": ["p1_ceo", "p2_growth", "p3_learning"],
        "effect": "durable_write",
        "purpose": "Write flexible per-business brain memory under brain/.",
    },
    {
        "name": "business_allocate_budget",
        "category": "budget",
        "priority_bands": ["p0_control", "p2_growth"],
        "effect": "guarded_write",
        "purpose": "Allocate or reserve spend under a business budget cap.",
    },
    {
        "name": "business_configure_app_budget",
        "category": "app",
        "priority_bands": ["p0_control", "p2_growth"],
        "effect": "guarded_write",
        "purpose": "Set the business product app's overall usage budget cap.",
    },
    {
        "name": "business_upsert_app_surface_contract",
        "category": "app",
        "priority_bands": ["p2_growth"],
        "effect": "durable_write",
        "purpose": "Record the business-owned app surface contract so UI look, routes, and source path come from per-business context.",
    },
    {
        "name": "business_verify_product_surface",
        "category": "product",
        "priority_bands": ["p1_ceo", "p2_growth", "p4_maintenance"],
        "effect": "durable_write",
        "purpose": "Verify a product/website source path and record a receipt or blocker without deciding strategy.",
    },
    {
        "name": "business_upsert_app_plan",
        "category": "app",
        "priority_bands": ["p2_growth"],
        "effect": "durable_write",
        "purpose": "Create or update product app pricing/entitlement plan policy.",
    },
    {
        "name": "business_upsert_app_customer",
        "category": "app",
        "priority_bands": ["p2_growth", "p3_learning"],
        "effect": "durable_write",
        "purpose": "Create or update a product subuser/customer for one business app.",
    },
    {
        "name": "business_grant_app_entitlement",
        "category": "app",
        "priority_bands": ["p2_growth"],
        "effect": "guarded_write",
        "purpose": "Grant or update product customer entitlements; paid billing state must come from Stripe/webhook evidence, not manual fiction.",
    },
    {
        "name": "business_request_app_magic_link",
        "category": "app",
        "priority_bands": ["p2_growth"],
        "effect": "guarded_write",
        "purpose": "Create a one-use magic-link token for product customer sign-in and optionally send it via Postmark.",
    },
    {
        "name": "business_verify_app_magic_link",
        "category": "app",
        "priority_bands": ["p2_growth"],
        "effect": "guarded_write",
        "purpose": "Consume a product customer magic-link token and create an app session.",
    },
    {
        "name": "business_read_app_account",
        "category": "app",
        "priority_bands": ["p2_growth", "p4_maintenance"],
        "effect": "read_only",
        "purpose": "Read a product customer account, entitlements, revenue, and usage.",
    },
    {
        "name": "business_create_app_checkout",
        "category": "app",
        "priority_bands": ["p2_growth"],
        "effect": "external_side_effect",
        "purpose": "Create a Stripe Checkout session for a business product app plan and record the checkout intent.",
    },
    {
        "name": "business_record_stripe_webhook",
        "category": "app",
        "priority_bands": ["p0_control", "p2_growth"],
        "effect": "guarded_write",
        "purpose": "Verify and reconcile Stripe webhook events into checkout sessions, entitlements, subscriptions, and revenue.",
    },
    {
        "name": "business_record_app_usage",
        "category": "app",
        "priority_bands": ["p0_control", "p2_growth"],
        "effect": "guarded_write",
        "purpose": "Record product app usage under the business app budget cap.",
    },
    {
        "name": "business_enqueue_job",
        "category": "queue",
        "priority_bands": ["p1_ceo", "p2_growth", "p3_learning"],
        "effect": "guarded_write",
        "purpose": "Record guarded requests for vendor calls, posting, builds, deploys, or receipts.",
    },
    {
        "name": "business_publish_outreach",
        "category": "outreach",
        "priority_bands": ["p2_growth", "p3_learning"],
        "effect": "mode_aware_side_effect",
        "purpose": "Publish outreach through one intent: test mode writes local suppressed artifacts and conversation mirrors; live mode records a gated provider publish job.",
    },
    {
        "name": "business_publish_test_outreach",
        "category": "outreach",
        "priority_bands": ["p2_growth", "p3_learning"],
        "effect": "durable_write",
        "purpose": "Publish test outreach locally, create a suppressed-side-effect receipt, and mirror it into conversations without sending externally.",
    },
    {
        "name": "business_generate_creative_asset",
        "category": "creative",
        "priority_bands": ["p2_growth", "p3_learning"],
        "effect": "guarded_local_effect",
        "purpose": "Generate a provider-backed image or video creative as a local business asset with budget, credential, receipt, and no posting/spend side effect.",
        "keywords": ["UGC video", "Sora", "creative asset", "local generated asset", "Meta UGC", "image creative", "video creative"],
        "capabilities": ["local_generated_asset", "ugc_video", "sora_video_via_video_generate", "receipt_backed_generation"],
        "provider_examples": ["openai/sora-2", "openai/sora-2-pro"],
        "capability_discovery": "Call business_registry and read runtime_capabilities.video_generation before assuming video generation is unavailable.",
    },
    {
        "name": "business_upgrade_businesses",
        "category": "maintenance",
        "priority_bands": ["p4_maintenance"],
        "effect": "guarded_write",
        "purpose": "Dry-run or apply idempotent business compatibility migrations, including schema/capability versions, old distribution mappings, and legacy product surface detection without inventing generated assets.",
    },
    {
        "name": "business_claude_agent_task",
        "category": "agent",
        "priority_bands": ["p1_ceo", "p2_growth", "p3_learning"],
        "effect": "agentic_write",
        "purpose": "Run a general Claude Agent SDK worker inside a business workspace with path, credential, budget, and audit guardrails.",
    },
    {
        "name": "business_conversation_agent_task",
        "category": "conversation",
        "priority_bands": ["p1_ceo", "p2_growth", "p3_learning"],
        "effect": "agentic_write",
        "purpose": "Delegate bounded conversation response work to a scoped worker that triages, drafts, learns, and optionally applies capped local conversation actions through guarded tools.",
    },
    {
        "name": "business_upsert_conversation_thread",
        "category": "conversation",
        "priority_bands": ["p1_ceo", "p2_growth", "p3_learning"],
        "effect": "durable_write",
        "purpose": "Create or update a business-owned conversation thread and Markdown mirror.",
    },
    {
        "name": "business_record_conversation_message",
        "category": "conversation",
        "priority_bands": ["p0_control", "p1_ceo", "p2_growth", "p3_learning"],
        "effect": "durable_write",
        "purpose": "Record inbound, outbound, or internal messages; unresolved inbound replies become CEO-visible business evidence.",
    },
    {
        "name": "business_update_conversation_message_status",
        "category": "conversation",
        "priority_bands": ["p1_ceo", "p2_growth", "p3_learning"],
        "effect": "durable_write",
        "purpose": "Update one business conversation message status, such as responded, ignored, archived, or needs_response.",
    },
    {
        "name": "business_record_event",
        "category": "audit",
        "priority_bands": ["p0_control", "p1_ceo", "p2_growth", "p3_learning", "p4_maintenance"],
        "effect": "durable_write",
        "purpose": "Record evidence, decisions, observations, or receipt-like events.",
    },
    {
        "name": "business_record_agent",
        "category": "audit",
        "priority_bands": ["p1_ceo", "p3_learning", "p4_maintenance"],
        "effect": "durable_write",
        "purpose": "Record CEO or delegated subagent runs in the business audit trail.",
    },
    {
        "name": "business_set_control",
        "category": "control",
        "priority_bands": ["p0_control"],
        "effect": "guarded_write",
        "purpose": "Set pause, resume, or kill states at global, business, workspace, job, or agent scope.",
    },
    {
        "name": "business_schedule_ceo_wakeup",
        "category": "cron",
        "priority_bands": ["p1_ceo"],
        "effect": "guarded_write",
        "purpose": "Create or update the cron job that wakes the CEO for one business.",
    },
    {
        "name": "business_gc",
        "category": "maintenance",
        "priority_bands": ["p0_control", "p4_maintenance"],
        "effect": "guarded_write",
        "purpose": "Run conservative cleanup for old ephemeral events, jobs, and agent runs.",
    },
]


TAKYON_SKILL_REGISTRY: list[dict[str, Any]] = [
    {
        "name": "ceo",
        "skill": "takyon:ceo",
        "category": "ceo",
        "priority_bands": ["p0_control", "p1_ceo", "p2_growth", "p3_learning", "p4_maintenance"],
        "purpose": "Top-level CEO router for business-isolated autonomous work.",
    },
    {
        "name": "business-learning",
        "skill": "takyon:business-learning",
        "category": "memory",
        "priority_bands": ["p1_ceo", "p2_growth", "p3_learning"],
        "purpose": "Improve flexible per-business strategy, pricing, product, distribution, and learning memory.",
    },
    {
        "name": "business-pulse",
        "skill": "takyon:business-pulse",
        "category": "pulse",
        "priority_bands": ["p1_ceo", "p2_growth", "p3_learning"],
        "purpose": "Interpret deterministic pulse metrics against the business model, update pulse memory, and surface changed signals.",
    },
    {
        "name": "build-product",
        "skill": "takyon:build-product",
        "category": "product",
        "priority_bands": ["p2_growth"],
        "purpose": "Shape or improve product and offer when the business lacks a usable product surface.",
    },
    {
        "name": "market-research",
        "skill": "takyon:market-research",
        "category": "research",
        "priority_bands": ["p2_growth", "p3_learning"],
        "purpose": "Gather customer, competitor, channel, pricing, and demand evidence.",
    },
    {
        "name": "pricing-strategy",
        "skill": "takyon:pricing-strategy",
        "category": "pricing",
        "priority_bands": ["p2_growth"],
        "purpose": "Improve pricing, packaging, checkout, margin, and revenue strategy.",
    },
    {
        "name": "distribution-campaign",
        "skill": "takyon:distribution-campaign",
        "category": "distribution",
        "priority_bands": ["p2_growth", "p3_learning"],
        "purpose": "Create and operate isolated traffic, launch, ad, content, social, or channel campaign workspaces.",
    },
    {
        "name": "ad-creative",
        "skill": "takyon:ad-creative",
        "category": "creative",
        "priority_bands": ["p2_growth", "p3_learning"],
        "purpose": "Draft ad angles, copy, landing hooks, UGC image/video creative assets, and posting requests.",
        "use_when": "Use for paid-social creative, UGC scripts, shot lists, local generated image/video assets, and ad creative iteration; keep posting/spend as separate gated jobs.",
        "keywords": ["UGC video", "Sora", "creative asset", "local generated asset", "Meta UGC", "ad video", "paid social"],
        "capabilities": ["routes_to_business_generate_creative_asset", "local_generated_asset", "ugc_video"],
    },
    {
        "name": "outreach",
        "skill": "takyon:outreach",
        "category": "outreach",
        "priority_bands": ["p2_growth", "p3_learning"],
        "purpose": "Build lead hypotheses, outbound lists, partner pitches, and sales follow-up sequences.",
    },
    {
        "name": "conversion-review",
        "skill": "takyon:conversion-review",
        "category": "conversion",
        "priority_bands": ["p2_growth"],
        "purpose": "Improve offer, site, onboarding, and funnel when traffic does not convert.",
    },
    {
        "name": "failure-recovery",
        "skill": "takyon:failure-recovery",
        "category": "recovery",
        "priority_bands": ["p0_control", "p1_ceo", "p4_maintenance"],
        "purpose": "Recover from failed, stale, blocked, or contradictory business work and turn it into learning.",
    },
    {
        "name": "claude-agent-sdk",
        "skill": "takyon:claude-agent-sdk",
        "category": "agent",
        "priority_bands": ["p1_ceo", "p2_growth", "p3_learning"],
        "purpose": "Use a bounded Claude Agent SDK worker for general business-scoped tasks, not just websites.",
    },
    {
        "name": "conversation-response",
        "skill": "takyon:conversation-response",
        "category": "conversation",
        "priority_bands": ["p1_ceo", "p2_growth", "p3_learning"],
        "purpose": "Delegate high-volume replies, comments, outreach results, and support conversations to a scoped response agent under CEO objectives and guardrails.",
    },
    {
        "name": "app-runtime",
        "skill": "takyon:app-runtime",
        "category": "app",
        "priority_bands": ["p0_control", "p2_growth"],
        "purpose": "Configure business product app auth, customers, entitlements, checkout, subscriptions, usage budgets, and surface contracts.",
    },
]


TAKYON_REGISTRY: dict[str, Any] = {
    "version": 1,
    "priority_bands": TAKYON_PRIORITY_BANDS,
    "categories": TAKYON_CATEGORIES,
    "tools": TAKYON_TOOL_REGISTRY,
    "skills": TAKYON_SKILL_REGISTRY,
}


def business_registry_snapshot(
    *,
    kind: str | None = None,
    category: str | None = None,
    priority_band: str | None = None,
) -> dict[str, Any]:
    """Return a filtered copy of the registry."""
    kind = str(kind or "all").strip().lower()
    category = str(category or "").strip()
    priority_band = str(priority_band or "").strip()

    data = deepcopy(TAKYON_REGISTRY)
    if kind not in {"all", "tools", "skills"}:
        raise ValueError("registry kind must be all, tools, or skills")
    if category and category not in TAKYON_CATEGORIES:
        raise ValueError(f"unknown registry category: {category}")
    if priority_band and priority_band not in TAKYON_PRIORITY_BANDS:
        raise ValueError(f"unknown priority band: {priority_band}")

    def include(item: dict[str, Any]) -> bool:
        if category and item.get("category") != category:
            return False
        if priority_band and priority_band not in item.get("priority_bands", []):
            return False
        return True

    if kind in {"all", "tools"}:
        data["tools"] = [item for item in data["tools"] if include(item)]
    else:
        data.pop("tools", None)
    if kind in {"all", "skills"}:
        data["skills"] = [item for item in data["skills"] if include(item)]
    else:
        data.pop("skills", None)
    return data
