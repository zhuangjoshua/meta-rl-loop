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
    "budget": {"label": "Budget", "description": "Allocate or reserve money under business caps."},
    "queue": {"label": "Queue", "description": "Request deterministic runner-side jobs and external side effects."},
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
        "purpose": "Inspect the canonical Takyon skill/tool category and priority-band map.",
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
        "name": "business_enqueue_job",
        "category": "queue",
        "priority_bands": ["p1_ceo", "p2_growth", "p3_learning"],
        "effect": "guarded_write",
        "purpose": "Request deterministic runner-side jobs, vendor calls, posting, builds, deploys, or receipts.",
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
        "purpose": "Draft ad angles, copy, landing hooks, creative specs, and posting requests.",
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
