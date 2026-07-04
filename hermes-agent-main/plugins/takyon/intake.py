"""Brief intake — translate a detailed external product doc into a Takyon build plan expressed in
the capabilities the system ACTUALLY has.

The point (operator ask 2026-07-04): you hand in a real spec (a competitor teardown, a PRD, a
brief like ~/Downloads/briefs/*.md) and the system converts it to *what can be built on our
rails* — mapping each requirement to the equivalent capability we already have, and explicitly
naming what we refuse or omit. It does NOT try to build the literal spec; it converts to the
nearest thing our rails deliver. Grounded in the LIVE rail registry (PRODUCT_RUNTIME_RAILS) so
the catalog is truthful about what is on, not aspirational.

Runs as a one-shot, keyless operator model call (mint an operator session -> safebox anthropic
proxy). No raw key on this plane; fails closed if the safebox/operator auth is unavailable.
"""

from __future__ import annotations

import os
from typing import Any

# Capabilities the CEO can build a product from, keyed to the real rails. The rail NAMES are
# validated against the live registry at runtime; the descriptions are the equivalence hints the
# translator maps requirements onto. Add a line here when a new rail ships.
_CAPABILITY_HINTS: dict[str, str] = {
    "auth": "customer accounts + sessions (Supabase-backed, business-scoped)",
    "account": "per-customer account state + entitlement/plan status",
    "profile": "per-customer profile",
    "directory": "cross-customer directory / social like-pass-block (matching)",
    "records": "GENERIC per-business/per-customer datasets + bounded faceted queries — this is how "
               "you store 'a database of X' (investors, snapshots, leads); no per-business schema needed",
    "actions": "business-authored server code in a sandbox (the product's backend logic)",
    "media": "file/image storage with quotas + signed URLs",
    "email": "transactional + (consent-ledgered) broadcast email to customers",
    "generate": "AI generation/scoring/summarization, metered (drafts, briefs, relevance scoring, "
                "classification — use THIS instead of scraping an AI provider's UI)",
    "search": "metered web search / URL extraction",
    "connections": "per-CUSTOMER third-party OAuth connections via Composio (act as the customer on "
                   "Reddit/X/Google/a mailbox — send-as-customer, read-their-account)",
    "egress": "keyless outbound calls to ANY approved third-party API (ctx.egress) — the credential "
              "stays in the safebox; use for Stripe/CRM/calendar/data APIs and any authenticated integration",
    "checkout": "Stripe checkout + webhook reconciliation (customer payments)",
    "entitlements": "plan policy + per-customer entitlements/tiers",
    "usage": "per-customer usage metering + budgets (data-points/month quotas, metered_quota tiers)",
    "analytics": "customer-facing product analytics (auto-injected)",
}

# Scheduled/background work + billing shapes are cross-cutting (not single rails) — name them so the
# translator knows they exist.
_CROSSCUTTING = [
    "scheduled actions + background jobs — daily/periodic ingestion, scans, polls, enrichment "
    "(per-customer attributed or business-shared)",
    "billing shapes — monthly subscription, credit top-ups (metered_credits), one-time order, "
    "per-unit/quota (metered_quota); prices derived + margin-checked",
    "credit grants — purchased non-expiring balances that extend a customer's monthly allowance",
]

# Refused by policy — a general platform is defined as much by what it will not do. The translator
# must map any brief requirement hitting these to 'REFUSED', with the compliant substitute.
_REFUSED = [
    "inauthentic engagement — purchased upvotes/reviews, pooled aged/sockpuppet accounts, "
    "astroturfing, ban-evasion, any paid action to manipulate a third platform (compliant "
    "substitute: post from the customer's OWN connected account, no bought engagement)",
    "bot-evading scrape — scraping a third party's UI/results that has no API and deploys bot "
    "protection or forbids it in ToS (compliant substitute: use that provider's API, or the "
    "brokered LLM APIs instead of scraping an AI answer engine's UI)",
    "regulated money custody (wallets/stored value/escrow), gambling/wagering, and regulated "
    "verticals (health/lending/minors) without a posture pack",
    "unfunded value — perpetual free plans / comp grants (substitute: business-funded trials + "
    "public lead magnets)",
    "unlicensed data resale — selling scraped data without provenance/licensing",
]


def _available_rails() -> list[str]:
    """The rail names that are actually registered right now (truthful, not aspirational)."""
    try:
        from . import core
    except ImportError:  # pragma: no cover - alternate load path
        from plugins.takyon import core  # type: ignore
    reg = getattr(core, "PRODUCT_RUNTIME_RAILS", {}) or {}
    return [name for name in reg.keys()]


def capability_catalog() -> str:
    """A compact, TRUTHFUL catalog of what a product can be built from — the live rails (with
    equivalence hints), the cross-cutting capabilities, and the refuse list."""
    live = set(_available_rails())
    lines = ["AVAILABLE PRODUCT CAPABILITIES (map each requirement to one of these):"]
    for name in _available_rails():
        hint = _CAPABILITY_HINTS.get(name, "(rail — see runtime contract)")
        lines.append(f"- {name}: {hint}")
    # Any curated hint whose rail isn't live is intentionally omitted (stay truthful).
    lines.append("")
    lines.append("CROSS-CUTTING:")
    lines += [f"- {c}" for c in _CROSSCUTTING]
    lines.append("")
    lines.append("REFUSED BY POLICY (map matching requirements to REFUSED + the compliant substitute):")
    lines += [f"- {r}" for r in _REFUSED]
    return "\n".join(lines)


_SYSTEM = (
    "You are Takyon's intake translator. You are given a detailed external product brief and the "
    "EXACT set of capabilities the Takyon platform can build a product from. Translate the brief "
    "into a build plan expressed ONLY in those capabilities. Do NOT invent capabilities, and do NOT "
    "try to reproduce the literal spec — map each requirement to the capability that does the "
    "EQUIVALENT job effectively. If a requirement can only be met by something in the REFUSED list, "
    "mark it REFUSED and give the compliant substitute.\n\n"
    "Output GitHub-flavored markdown with exactly these sections:\n"
    "## Requirement -> Capability\n"
    "A table: | Brief requirement | Built with | Notes |. One row per meaningful requirement.\n"
    "## Refused / Omitted\n"
    "Bullets: what we will not build and why (with the compliant substitute).\n"
    "## Build Goal\n"
    "One tight paragraph (<= 900 chars) telling the Takyon CEO exactly what product to build, "
    "naming the specific rails to use (e.g. records, generate, egress, connections, scheduled "
    "jobs, the billing shape). This paragraph becomes the bootstrap goal, so it must be concrete "
    "and buildable on the listed capabilities alone."
)


class IntakeError(RuntimeError):
    """Intake conversion could not run (fail closed — no raw key, no fabrication)."""


def _extract_text(anthropic_result: dict[str, Any]) -> str:
    content = (anthropic_result or {}).get("content") or []
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "".join(parts).strip()


def _extract_build_goal(markdown: str) -> str:
    """Pull the 'Build Goal' paragraph out of the converted markdown (the bootstrap goal)."""
    lower = markdown.lower()
    marker = "## build goal"
    idx = lower.rfind(marker)
    if idx == -1:
        # No section found — fall back to the whole thing, trimmed.
        return markdown.strip()[:1200]
    tail = markdown[idx + len(marker):].strip()
    # up to the next heading, if any
    for stop in ("\n## ", "\n# "):
        cut = tail.find(stop)
        if cut != -1:
            tail = tail[:cut]
    return tail.strip()[:1200]


def convert_brief(
    brief_text: str,
    *,
    operator_user_id: str,
    business: str | None = None,
    model: str | None = None,
    # 16k, not 8k: adaptive-thinking models (fable-5/sonnet-5) spend output budget on thinking
    # blocks BEFORE the text block; a hard brief can burn all 8k on thinking and return zero text
    # ("returned no plan"). 16k leaves room for both.
    max_tokens: int = 16000,
) -> dict[str, str]:
    """Translate a brief into a Takyon build plan. Returns {markdown, goal}.

    One-shot keyless operator model call: mint an operator session capability, then POST the
    anthropic proxy on the safebox. Fails closed (IntakeError) if the safebox/operator auth is
    unavailable — never fabricates a plan and never touches a raw provider key.

    Model precedence mirrors the coding worker (explicit arg, then TAKYON_CLAUDE_AGENT_MODEL,
    then the Sonnet default) so one env flip moves the whole create pipeline together."""
    model = str(
        model or os.environ.get("TAKYON_CLAUDE_AGENT_MODEL") or "claude-sonnet-5"
    ).strip()
    text = str(brief_text or "").strip()
    if not text:
        raise IntakeError("empty brief")
    owner = str(operator_user_id or "").strip()
    if not owner:
        raise IntakeError("intake requires an operator_user_id")

    try:
        from . import safebox
    except ImportError:  # pragma: no cover
        from plugins.takyon import safebox  # type: ignore

    try:
        # Per-call ceiling sized for a top-tier model: max_tokens=16000 output at Fable's $50/M is
        # ~$0.80 plus prompt input, so the old Sonnet-sized $0.20 ceiling tripped the proxy's
        # estimate gate (estimate_exceeds_ceiling). $2.00 still hard-bounds a single intake call.
        token = safebox.mint_operator_session_token(business or "", owner, max_cost_microusd=2_000_000)
    except Exception as exc:  # noqa: BLE001 — any auth failure is fail-closed
        raise IntakeError(f"intake needs operator model access (safebox): {exc}") from exc

    catalog = capability_catalog()
    user = (
        f"{catalog}\n\n"
        f"=== PRODUCT BRIEF TO TRANSLATE ===\n{text[:24000]}\n"
        "=== END BRIEF ===\n\n"
        "Translate it now, using only the capabilities above."
    )
    payload = {
        "model": model,
        "max_tokens": int(max_tokens),
        "system": _SYSTEM,
        "messages": [{"role": "user", "content": user}],
    }
    try:
        result = safebox.proxy_request("anthropic", "messages", payload, token=token)
    except Exception as exc:  # noqa: BLE001 — fail closed
        raise IntakeError(f"intake model call failed: {exc}") from exc

    markdown = _extract_text(result)
    if not markdown:
        # Name WHY there is no text so a truncated-by-thinking response (stop_reason=max_tokens,
        # only thinking blocks) is diagnosable from the CLI error alone.
        stop_reason = str((result or {}).get("stop_reason") or "?")
        block_types = [
            str(b.get("type") or "?") for b in ((result or {}).get("content") or []) if isinstance(b, dict)
        ]
        raise IntakeError(
            f"intake model returned no plan (stop_reason={stop_reason}, content_blocks={block_types or 'none'})"
        )
    return {"markdown": markdown, "goal": _extract_build_goal(markdown)}
