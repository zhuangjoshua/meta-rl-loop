"""Neutral turn-runtime helpers shared by the interactive shell and the worker plane.

Extracted verbatim from ``cli.py`` (modularization plan, Stage 1) so the backend job
handlers in ``worker.py`` no longer import the interactive CLI module (worker->UI layering
inversion). ``cli.py`` re-exports every name here, so shell callers and tests are unchanged.

The fourth compute lane — the dashboard operator turn spawned as a detached subprocess
(``tui_gateway.isolated_turn_worker``) — also consumes these helpers via the ``cli`` shim;
it is session-owned compute and its remodel onto ``WorkerPool`` is out of Stage-1 scope.
"""

import contextlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from .core import (
    TakyonError,
    TakyonStore,
    _company_base_domain,
    _slugify,
    load_takyon_env,
)


_CEO_PROMPT_PATH = Path(__file__).parent / "prompts" / "ceo.md"


_DEFAULT_BOOTSTRAP_MAX_TURNS = 30


_WORKFLOW_BOOTSTRAP_MAX_TURNS = 60


_BOOTSTRAP_PRODUCT_BASE_DOMAIN = "coscale.app"


_BOOTSTRAP_WORKFLOW_REQUEST_RE = re.compile(
    r"\b("
    r"real\s+(?:customer\s+)?ai\s+workflow|"
    r"customer\s+ai\s+workflow|"
    r"in-?app\s+workflow|"
    r"product\s+workflow|"
    r"app\s+action|"
    r"backend\s+action|"
    r"signed-?in\s+(?:subscriber|user|customer)\s+enters?|"
    r"receives?\s+(?:five|[0-9]+)\s+"
    r")\b",
    re.IGNORECASE,
)


_BOOTSTRAP_PRODUCT_SHAPE_RE = re.compile(
    r"\b("
    r"saas|software|app|application|platform|tool|assistant|copilot|service|portal|dashboard|"
    r"generator|tracker|planner|coach"
    r")\b",
    re.IGNORECASE,
)


_BOOTSTRAP_CUSTOMER_ACTION_RE = re.compile(
    r"\b("
    r"helps?|lets?|allows?|enables?|"
    r"customers?\s+can|users?\s+can|subscribers?\s+can|"
    r"enter(?:s|ing)?|upload(?:s|ing)?|draft(?:s|ing)?|generate(?:s|d|ing)?|"
    r"track(?:s|ing)?|plan(?:s|ning)?|calculate(?:s|ing)?|analy[sz]e(?:s|d|ing)?|"
    r"compare(?:s|d|ing)?|organize(?:s|d|ing)?|summari[sz]e(?:s|d|ing)?|"
    r"rewrite(?:s|d|ing)?|convert(?:s|ed|ing)?|automate(?:s|d|ing)?|"
    r"dispute(?:s|d|ing)?|quote(?:s|d|ing)?|schedule(?:s|d|ing)?|"
    r"transcribe(?:s|d|ing)?|classif(?:y|ies|ied|ying)|extract(?:s|ed|ing)?"
    r")\b",
    re.IGNORECASE,
)


_BOOTSTRAP_FEATURE_NOUN_RE = re.compile(
    r"\b("
    r"automation|generator|planner|tracker|calculator|assistant|copilot|workflow|"
    r"report(?:s)?|letter(?:s)?|evidence|deadline(?:s)?|timeline|analysis|"
    r"summary|summaries|quiz(?:zes)?|flashcards?|invoice(?:s)?|proposal(?:s)?|"
    r"resume(?:s)?|forecast(?:s)?"
    r")\b",
    re.IGNORECASE,
)


def _bootstrap_goal_requests_product_workflow(goal: str) -> bool:
    text = " ".join(str(goal or "").split())
    if not text:
        return False
    if _BOOTSTRAP_WORKFLOW_REQUEST_RE.search(text):
        return True
    if not _BOOTSTRAP_PRODUCT_SHAPE_RE.search(text):
        return False
    if _BOOTSTRAP_CUSTOMER_ACTION_RE.search(text):
        return True
    return " with " in text.lower() and bool(_BOOTSTRAP_FEATURE_NOUN_RE.search(text))


# Extra bootstrap turns a mobile_app business needs on top of the web cap: the iOS app-source
# build pass + the store-signed publish + one triage round of the repair loop.
_MOBILE_BOOTSTRAP_EXTRA_TURNS = 14


def _bootstrap_turn_cap_for_goal(goal: str, archetype: str = "") -> int:
    cap = (
        _WORKFLOW_BOOTSTRAP_MAX_TURNS
        if _bootstrap_goal_requests_product_workflow(goal)
        else _DEFAULT_BOOTSTRAP_MAX_TURNS
    )
    if str(archetype or "").strip().lower() == "mobile_app":
        cap += _MOBILE_BOOTSTRAP_EXTRA_TURNS
    return cap


def _bootstrap_public_site_url(slug: str) -> str:
    base = str(_company_base_domain() or "").strip().lower()
    if not base or base == "fourmanifold.com":
        base = _BOOTSTRAP_PRODUCT_BASE_DOMAIN
    return f"https://{_slugify(slug)}.{base}/"


def _normalize_progress_text(value: Any, *, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text or text in {"(empty)", "_thinking"}:
        return ""
    if limit is not None and limit > 0 and len(text) > limit:
        return text[: max(1, limit - 3)].rstrip() + "..."
    return text


def _takyon_reasoning_config(effort: str | None = None) -> dict[str, Any]:
    level = str(effort or "").strip().lower()
    if level in {"off", "none", "disable", "disabled"}:
        return {"enabled": False}
    if level not in {"minimal", "low", "medium", "high", "max", "xhigh"}:
        level = "medium"
    return {"enabled": True, "effort": level}


def _reasoning_progress_callback(progress: Any) -> Callable[[str], None] | None:
    # Product/operator progress is a public status rail, not a chain-of-thought rail. The model may
    # use reasoning internally, but raw deltas must never be persisted to events or replayed by the
    # CLI/dashboard. Curated business_post_operator_update milestones are the sole planning surface.
    return None


def _business_root(slug: str) -> Path:
    return TakyonStore()._business_root(slug).resolve()


def _business_artifact_path(slug: str, path: str) -> Path:
    return (_business_root(slug) / str(path or "").lstrip("/")).resolve()


@contextlib.contextmanager
def _business_workspace_execution_context(
    slug: str,
    *,
    operator_user_id: str | None = None,
    sync_on_exception: bool = False,
):
    from .core import TakyonStore, _mounted_canonical_business_workspace

    load_takyon_env()
    store = TakyonStore(operator_user_id=operator_user_id)
    with _mounted_canonical_business_workspace(
        store,
        slug,
        owner_label=str(operator_user_id or slug),
    ) as (workspace_home, _backend, _base_revision):
        yield workspace_home


def _business_bootstrap_instruction(
    slug: str,
    goal: str,
    active_mode: str,
    *,
    business_name: str = "",
    animations: bool = False,
    archetype: str = "",
) -> str:
    goal_text = goal or "Use current business state and evidence to define the business goal."
    workflow_requested = _bootstrap_goal_requests_product_workflow(goal_text)
    effective_mode = "live" if str(active_mode or "").strip().lower() != "live" else "live"
    # Archetype-aware bootstrap: a mobile_app business gains step 3 — the iOS app build + first store-signed TestFlight-lane
    # release, routed through the takyon-mobile-app skill.
    mobile_app = str(archetype or "").strip().lower() == "mobile_app"
    lines = [
        f"Bootstrap business:{slug} now.",
        "",
        "This is an operational create/build request. Execute immediately.",
        "Do not respond with instructions, checklists, or 'want me to start?'.",
        "",
        *(
            [
                "MANDATORY FOR THIS BUSINESS: it is a mobile_app (iOS) business. Its PRIMARY deliverable "
                "is a real, store-signed iOS app, not the website. You MUST run every step through step 3 "
                "(the iOS app build + first store-signed build) and you MUST NOT declare the bootstrap "
                "complete after only the landing/website. Publishing the landing is an early milestone, "
                "NOT the finish line. The only acceptable end states are: (a) step 3 produced a real "
                "build_id, or (b) step 3 hit a concrete, recorded blocker (compliance gate, credits, "
                "eas_builder_unconfigured) after a genuine attempt. Concluding without reaching step 3 is a "
                "failed bootstrap.",
                "",
            ]
            if mobile_app
            else []
        ),
        f"Canonical business name: {business_name or slug}",
        f"Business goal: {goal_text}",
        f"Mode: {effective_mode}",
        f"Explicit product workflow requested: {'yes' if workflow_requested else 'no'}",
        "",
        "## Execution rules",
        "",
        "Fresh create. Business state is empty.",
        "- Do NOT call business_read_business, business_read_file, or business_list_files before acting.",
        "- Do NOT call todo or update task lists at any point.",
        "- Do NOT call skills_list.",
        (
            "- Load a skill only at the step that uses it, with one skill_view right before use: "
            "takyon-brand-logo at 2b"
            + (", takyon-mobile-app at step 3" if mobile_app else "")
            + ". Do not preload skills up front; do not load takyon-market-research, takyon-x, or "
            "takyon-distribution during bootstrap, "
            "and do not load any other skill."
        ),
        "- After completing each step, move to the next immediately.",
        "- Treat the canonical business name above as the owner/account name. If it is an internal slug (digits, test suffixes, or machine separators), choose ONE short human product display name in step 1 from the idea, record it in the surface contract, and use it consistently. Never expose or title-case the routing slug as public branding. Do not invent a second competing brand.",
        "- Consumer voice: this bootstrap turn is shown live to the customer on the build screen and product chat. Write every visible sentence as a warm, high-level, business-focused update describing the BUSINESS work (defining the offer, designing the product, and putting it online) — never the runtime plumbing.",
        "- Curated update channel: the customer sees ONLY the curated update you post with business_post_operator_update, never your raw assistant reasoning. Keep ALL planning, deliberation, tool choreography, and chain-of-thought internal. At the very start of this turn, call business_post_operator_update with a warm headline, a 1-2 sentence summary, and a milestones plan covering the steps below — e.g. {title: \"Define the offer\", category: RESEARCH, status: running}, {title: \"Design and build the product\", category: PRODUCT, status: queued}. Re-post the update (flipping each milestone's status) as you complete the brief, the product build, and when anything blocks. The milestones become the customer's Tasks cards; do not narrate low-level tool calls yourself.",
        "- Never surface raw internal platform/tool/runtime strings in the visible reply. Do not quote TAKYON_* flags, docker path diagnostics, workspace-mode errors, or similar internals; summarize blockers in normal operator language instead.",
        "- Forbidden in any customer-visible sentence: \"bootstrap\", \"site worker\", \"scaffold\"/\"scaffolding\", \"upsert\"/\"upserted\", \"provision\"/\"provisioned\", \"app account\", \"workspace exists\"/\"workspace ready\" (say \"your company space\" instead), \"runtime\", \"surface contract\", \"app shell\", \"kit\", any tool name (business_upsert_*, business_claude_agent_task, etc.), and verbatim tool/web-access limitations like \"publicly cached\".",
        "- If a web or tool capability is limited, say it plainly to the customer, e.g. \"I'm working from the sources I can reach right now\", without naming the mechanism.",
        "- These forbidden terms apply only to the VISIBLE reply shown to the customer. The internal directives in this instruction (which deliberately use words like bootstrap, surface contract, scaffold, upsert, and tool names to steer you) are not customer-visible and stay as written.",
        "",
        "## Steps",
        "",
        "### 1. Minimal landing brief (from the idea alone — NO web research yet)",
        "Goal: get the customer a real, branded landing page live FAST. Derive the landing brief from the BUSINESS IDEA ALONE. The complete pinned Taste implementation skill owns the first public landing from Design Read through implementation and preflight in one continuous worker call — do NOT do any web research before the first landing publishes.",
        "Do NOT load takyon-market-research in this step, and do NOT call web_search, web_extract, web_tools, business_web_search, Tavily, or any other live-evidence/market-research tool during bootstrap. Research and distribution run later through the existing scheduled CEO wake rail.",
        "From the idea (and the canonical business name and goal above), reason out and pin down: ONE short human product display name (never the routing slug), a one-line tagline, the core value proposition, who the customer is (ICP / audience), the core problem the product solves, the offer, the brand tone, and one positioning angle. This is straightforward derivation from the idea, not research — no sources are required for a truthful, branded landing.",
        "If the goal names subscription cancellation timing or refund policy, treat those as AppKit/backend constraints only. Do not turn them into strategy, landing, pricing, or profile copy; the canonical account control may describe them only from the typed backend policy or the exact cancellation action result.",
        "Write that into research/strategy.md as the idea-only landing brief. A later scheduled CEO wake may deepen and source-back it.",
        "Keep the landing TRUTHFUL: a landing built from the idea alone is fine, but do NOT fabricate statistics, customer counts, testimonials, named partners, awards, or evidence-backed claims you have not verified. Stick to the product's own value proposition and offer.",
        "Stop as soon as you have enough of the brief for truthful, branded landing copy, then move straight to step 2.",
        "",
        "### 2. Product surface + landing build (publish the landing FIRST)",
        "Call business_upsert_app_surface_contract with:",
        "- display_name: the ONE human product display name chosen in step 1 (never the business slug)",
        "- source_path: product/site",
        "- runtime_features: auth, account, profile, checkout",
        "- routes: / (landing page), /app (sign-in + subscription gate), and /app/profile (account page)",
        "- If `Explicit product workflow requested: yes`, do NOT try to declare `generate` directly here. The product worker must implement the workflow as real `product/site/actions/<name>.ts` files and UI calls; the refresh pass derives the actions/generate rails from the source.",
        "",
        "This seeds the COMPLETE app kit up front (landing, the /app access shell, the /app/profile account page, support, and the shared auth/checkout/account rails). The two build passes below only change WHEN each screen is customized and published; they never change the final fileset. The end state must be the same complete app kit as a single-pass build.",
        "",
        "If the app shell is monthly paid, call business_upsert_app_plan for the canonical `monthly` plan before the site worker runs so the existing checkout rail has a real plan object to use.",
        "- Use an explicitly requested monthly price when one is already known.",
        "- Set `included_ai_budget_microusd` together with `price_cents`.",
        "- If pricing is not settled yet, keep the canonical starter monthly plan instead of leaving checkout planless.",
        "",
        "#### 2a. Build and publish the landing page (the customer's first paint)",
        "This pass is the customer's FIRST paint — nothing is live before it publishes, so start it immediately after the surface contract (and plan, if paid) are pinned. Build the full, polished, custom landing so the site looks bespoke, not templated. Call business_claude_agent_task with:",
        "- workspace: product/site",
        "- The same Taste worker session owns image art direction and implementation. It has a capped business_generate_site_image tool: after the Design Read, call it for exactly TWO distinct, page-role-specific images before finishing the landing. Use one as the real hero visual and one as supporting visual evidence; use every returned /generated/... public_path in the landing with data-takyon-landing-asset=\"hero\" or \"supporting\" on the corresponding <img>. Never use the logo as either image, generate filler, hotlink stock, or replace either image with a div/SVG fake screenshot. This spends four starter credits and preserves the separately required post-landing logo + X allowance.",
        "- instruction: Use the pinned Vite scaffold as the runtime base. Keep shared wiring through `src/lib/takyon.ts` and `src/lib/hooks.ts`. Apply the COMPLETE pinned Taste skill in this same worker session: read the runtime-injected canonical business brief before making any design or asset decision; emit its one-line Design Read with the exact product, ICP, real-world setting, core task, and tone from that brief; set exact `DESIGN_VARIANCE`, `MOTION_INTENSITY`, and `VISUAL_DENSITY` values; choose the appropriate foundation; then persist that one-line Design Read, all three dial values, the selected foundation, and durable asset decisions in `DESIGN.md` at the product/site workspace root BEFORE editing the landing. Treat `product/site/DESIGN.md` as the canonical design source for every later product pass. Implement the landing, run build/typecheck, and complete Taste's full preflight. Then call the same-session `business_render_landing_preflight` tool exactly once and READ its actual screenshots at 1440x900 and 390x844; do not start Vite, Chromium, or agent-browser yourself. The header and hero must form a deliberate complete first viewport with the primary CTA visible and no accidental early next-section intrusion. A render failure is a precise blocker, not permission to repeat the same preflight or start a second Taste worker. Do not split Taste into a planner call and a later coding call. The route graph and required public/auth behavior are immutable; the section structure is not.",
        '- guidance_skills: pass exactly ONE — "taste-frontend". Do not stack "claude-design" or any "claude-design-*" preset into this landing call: the full upstream Taste skill selects the appropriate design-system or aesthetic foundation from the brief itself, and one continuous skill context must own design through implementation and preflight.',
        "- Scope this pass to the landing route `/` plus the brand theme: customize `src/screens/landing.tsx` so it is a truthful, branded landing page, AND theme `src/tokens.css` with the chosen direction's real palette/typography tokens — the publish gate refuses to publish while `src/tokens.css` is still byte-identical to the scaffold placeholder theme, so the themed tokens are REQUIRED for this first publish, not optional polish. Do NOT edit `src/screens/app-layout.tsx`, `src/screens/app-home.tsx`, or `src/screens/profile.tsx` in this pass — those are customized in 2b.",
        "- Preserve and render the canonical `PublicSiteHeader` from `src/components/site-navigation.tsx`; do not replace it with a page-specific nav. The signed-out banner must stay visibly separated from the page and show distinct Log in and Sign up actions, never Subscribe/Open app or a price-first CTA. Signed-in visitors are redirected to `/app` by the shared landing behavior.",
        "- Never author subscription cancellation timing, renewal/end dates, grace-period, or refund/no-refund copy in the landing or pricing content, even when the business goal names that policy. Keep it as a backend constraint and preserve the canonical AppKit account control, which derives customer-visible truth from backend policy/action results.",
        "- Make the real product and conversion path understandable without prescribing a section count or layout family. Use real product representations when available; never fabricate UI, statistics, testimonials, logos, or outcome numbers.",
        "- Keep the shared Vite route skeleton and the seeded `/app`, `/app/profile`, and support routes intact; do not delete or stub any seeded screen. They stay as the seeded app kit until 2b refines them.",
        *(
            [
                "- Landing hero animation (operator opted in via --animation): this explicit perpetual-motion opt-in requires `MOTION_INTENSITY: 6` or higher in `DESIGN.md`; never declare a lower Taste band while shipping continuous motion. Complement the required generated hero image with a CUSTOM, CONTINUOUSLY-ANIMATED inline SVG accent in `src/screens/landing.tsx` — not a one-time load-in reveal and never a replacement for either required image. Author an inline `<svg>` visual that is RELEVANT to this specific product (e.g. an analytics product gets a rising chart; a scheduling product gets a timeline/calendar motif; a notes product gets flowing document/graph lines) and animate its paths with framer-motion: `motion.path` with `pathLength` draw-on, plus a looping animation (`animate` with `transition: { repeat: Infinity }`) so the graphic keeps moving subtly and continuously (drawing lines, pulsing/rising nodes, drifting accents). Keep it tasteful, on-brand with the chosen palette, and performant — animate ONLY `pathLength`, `opacity`, and `transform`; no layout thrash, no scroll-jacking, no autoplaying media. Provide a STATIC SVG fallback gated behind `useReducedMotion()` from framer-motion (reduced-motion visitors see the still graphic with an `aria-label`). framer-motion is already pinned in the scaffold; import it, do not add a dependency; keep it within this pass's budget and do not let it delay the publish.",
            ]
            if animations
            else []
        ),
        "- refresh_surface: true",
        "- max_turns: 60 — this is one complete Taste implementation pass, including the Design Read, implementation, verification, and full preflight; do not truncate it into a shallow art-direction summary.",
        "- effort: medium — Taste must reason from the brief and then carry that exact direction through code and verification in the same session.",
        "- timeout_ms: 900000 — one 15-minute total deadline for the complete landing call. Do not start a fresh retry or a second Taste worker when it expires; return the exact blocker. Reattaching with the same idempotency_key only observes this same durable call and is not a new attempt.",
        "- Do not pass a model. The coding-worker model is deployment-pinned for the entire run, and the runtime refuses per-call model changes.",
        "",
        "This 2a pass with `refresh_surface: true` PUBLISHES AND SERVES the landing immediately on its own: the worker's `surface_refresh.publish.status` should come back `published` and the live site at the customer host serves the new landing right away, with the still-seeded real `/app` access shell shipping behind sign-in until 2b refines it. The landing does NOT wait for 2b to be served — confirm `surface_refresh.publish.status == \"published\"` and a real `public_url` in this pass's structured result before continuing.",
        "",
        "Inspect the structured result from this first business_claude_agent_task. Trust only its exact success/blocker and surface_refresh publish status. If the landing build or publish is blocked, record that exact blocker in research/strategy.md and stop bootstrap there; do not continue to Search Console, the logo, or the rest of the app kit.",
        "A `detached: true` result (status `queued` or `running`, with a re-attach note) is NOT a blocker and NOT a failure — the build is simply still running on the worker plane. Do NOT record it as a blocker and do NOT stop the bootstrap. Re-call business_claude_agent_task with the SAME workspace, instruction, and idempotency_key to re-attach and collect the published result; repeat until it returns either `surface_refresh.publish.status == \"published\"` (continue) or a real blocker (then stop). Only an explicit blocker/error stops the landing.",
        "",
        "#### 2a.1. Register Search Console (immediately after the landing publishes)",
        "As soon as 2a reports `surface_refresh.publish.status == \"published\"` for the landing, register the live site with Google Search Console — do this BEFORE 2b so the single fast idempotent call is front-loaded onto the already-live landing instead of being pushed past the budget by the heavier 2b pass.",
        f'Call business_register_search_console with the business, site_url "{_bootstrap_public_site_url(slug)}", and a fresh idempotency_key. Do not rely on inferred public_url here. It injects the google-site-verification META tag onto BOTH the live published landing and the source template (so Google can verify it now AND the 2b appkit publish carries the tag forward), then registers the URL-prefix property.',
        "This is live-only, key-behind-TK, and fails closed on its own: if it returns blocked_search_console_unconfigured (the verification key is not provisioned) or any other blocker, record that exact blocker in research/strategy.md and continue to 2b — do not fabricate a verification and do not stop the whole build for it.",
        "",
        "#### 2b. Add the real logo, then finish the /app access shell + profile",
        "Once the landing page has published in 2a:",
        "",
        "First — BEFORE any other creative-credit spend (ads, UGC) — generate the real brand logo. This step is REQUIRED: do NOT spend a creative credit on anything else until the logo is generated. The fresh business's starter creative credits are reserved for the logo first. Load takyon-brand-logo (skill_view) and follow its procedure: assemble `business_context` ({name, category, tone}) from the research you wrote in research/strategy.md (do not invent brand voice), then call business_generate_logo with the business, a fresh idempotency_key, that business_context, and `republish: false`. The tool publishes /brand-logo.png plus a real PNG favicon into the workspace and live asset path; `republish: false` skips the tool's own chained site rebuild because the 2b app-shell build below publishes the whole site minutes later and carries the favicon/header forward — one publish instead of two. business_generate_logo is live-only and creative-credit gated: ONLY if it returns an explicit insufficient-credits or unconfigured-provider blocker do you record that exact blocker in research/strategy.md, leave the seeded monogram placeholder, and continue with the rest of 2b — in every other case you MUST generate the logo here before proceeding. Do not fabricate a logo and do not stop the whole build for it.",
        "",
        "Then finish the access shell and account page in a SECOND business_claude_agent_task with:",
        "- Immediately BEFORE this second task, call business_upsert_app_surface_contract again with the same display_name, source_path, runtime_features, and routes, plus bootstrap_final_product_pass: true. The runtime snapshots the current landing build; bootstrap cannot complete until this second task publishes a different final build. Do not set this flag during 2a.",
        "- If `Explicit product workflow requested: yes`, include workflow_completion_required: true in that same upsert. This also makes the publish gate refuse an unchanged access starter or a workflow without real action/records/generate wiring.",
        "- workspace: product/site",
        "- instruction: Use the same pinned Vite scaffold and inherit the completed landing's brand rather than designing again: FIRST read the canonical `DESIGN.md` written by 2a, then inspect `src/tokens.css`, the landing source, and existing assets. Preserve the exact Design Read, three dial values, selected foundation, asset decisions, typography, palette, radius, spacing, and motion language; do not rewrite `DESIGN.md` to justify a second direction. Keep the shared runtime wiring through `src/lib/takyon.ts` and `src/lib/hooks.ts`.",
        "- guidance_skills: pass exactly `[]`. Do NOT pass Taste again and do not use any Open Design template. Read the Taste-authored DESIGN.md and established landing source/tokens/assets as the fixed brand source of truth, then build the dense product UI from that durable direction without selecting or importing another visual system.",
        "- instruction addendum: for `/app` and `/app/profile`, keep subscription/account truth on the shared AppKit hooks in `src/lib/hooks.ts`. Treat the account rail as `user` plus `entitlements[]`, and do not hand-roll gates from legacy fields like `has_active_subscription`, nested `subscription.status`, or ad hoc `client.account()` parsing.",
        "- Scope this pass to the access shell and account page on the EXISTING seeded auth + checkout rails:",
        "  - Keep the canonical `src/screens/app-layout.tsx` unchanged: it already owns loading stability, auth, checkout, full-width layout, and direct entitled access. Build the real product directly in `src/screens/app-home.tsx`; never add another welcome/enter/Open app screen.",
        "  - Make `/app/profile` the truthful account/subscription page in `src/screens/profile.tsx` on the existing account + profile rails.",
        "  - Preserve the starter-owned `SubscriptionCancellation` control that `src/main.tsx` renders on `/app/profile`; never tell a customer to contact support to cancel or change billing. Every active paid subscription must remain cancellable in-app. The control must derive all timing/refund text from `account.product_runtime_contract.subscription.cancellation` and the exact cancellation action result; do not duplicate timing/refund claims in worker-authored screen copy.",
        "- Do not edit `src/screens/landing.tsx` again unless a small correction is required to keep it consistent with the brand; 2a already published it.",
        "- Do not spend bootstrap time editing `src/screens/support.tsx` unless explicitly asked.",
        "- Keep the shared Vite route skeleton intact unless a small route-level correction is required for correctness.",
        "- If `Explicit product workflow requested: no`, stop once `/`, `/app`, and `/app/profile` are truthful and publishable.",
        "- If `Explicit product workflow requested: yes`, do NOT stop at the access shell. In this SAME second business_claude_agent_task, extend `/app` into the requested real signed-in subscribed customer workflow while keeping the landing, checkout, and profile/account rails intact.",
        "- For that workflow-required path, implement the backend behavior as one or more real `product/site/actions/<name>.ts` files that default-export async `(payload, ctx) => result` and call `ctx.generate(...)` for AI output.",
        "- For that workflow-required path, call the action from `/app` through the shared `useDecodedActionRunner(name, taggedDecoder)` hook. Do not call legacy `useActionRunner`, `invokeAction`, or `createActionRunner` directly, use provider SDKs, provider env vars, direct provider URLs, mock outputs, fixtures, static canned AI responses, browser-only fake generation, localStorage as authority, or unsupported server routes.",
        "- Define one explicit JSON result schema for every action consumed by `/app`. Use identical field types in the generation prompt, action-boundary validator/normalizer, TypeScript decoder, and renderer. Either pass `value => decodeActionResult(value, valueDecoder)`, where valueDecoder returns the normalized value or null/throws, or pass a named `DecodedActionResult<T>` decoder that validates and returns both `ok: false` and `ok: true` outcomes. The tagged decoder is unambiguous, including for boolean values. AppKit always renders a global `invalid_result` alert; also render the runner error contextually. Never silently discard a successful action payload.",
        "- Persist every created/generated customer artifact with `saveRecord(...)`, load it with `listRecords(...)` or `useRecords(...)`, and render persisted records after route/tab changes. Never keep the only copy of completed work in component state or an action result.",
        "- Use the full available `/app` viewport for the workflow. Do not reduce the product to one narrow centered card or a marketing-page column.",
        "- For that workflow-required path, render honest loading, success, and error states; on budget/entitlement errors, show the runtime-provided path to subscribe/upgrade instead of retrying or faking success.",
        "",
        "This must NOT look like a generic starter kit, membership template, or placeholder SaaS shell.",
        'Do not leave generic copy such as "membership pricing", "what is included", "simple pricing", "offer", or similar starter text anywhere customer-visible.',
        "Keep Hermes/Takyon runtime rails for auth, account, profile, and checkout intact.",
        "But replace generic starter copy, generic starter sections, and generic starter-shell presentation with product-specific content and UI on the first pass.",
        "Keep /app present and wired through the existing Hermes app kit runtime rails for sign-in, subscription, account, and profile access.",
        "If `Explicit product workflow requested: no`, do NOT build a bespoke product application, custom backend workflow, domain-specific dashboard, fake coach/product tabs, sample domain data, charts, or invented in-app flows in this pass.",
        "If `Explicit product workflow requested: yes`, the requested signed-in workflow is REQUIRED in this pass, but do not expand past that one real workflow into extra tabs, speculative dashboards, unsupported backend capabilities, or fake data.",
        "",
        "For /:",
        "- Write ICP-specific copy immediately.",
        "- The hero, problem, features, pricing, and CTA must reflect the idea brief's customer and pain.",
        "- The landing page should be bold, visually opinionated, and unmistakably product-specific from the first pass, not timid, generic, or scaffold-like.",
        "",
        "For /app:",
        "- Keep the existing AppKit auth, checkout/subscription, account, and profile flows.",
        "- Make the existing sign-in, subscription, account, and profile surfaces polished, branded, and customer-specific instead of generic starter UI.",
        "- You may restyle and refine those surfaces so they match the landing page brand.",
        "- Keep access decisions on the shared `src/lib/hooks.ts` helpers; prefer `useViewerAccess()` and `resolveViewerCta()` over screen-local subscription parsing.",
        "- Treat runtime account truth as `user` plus `entitlements[]`; do not gate from legacy fields like `has_active_subscription`, nested `subscription.status`, or bespoke `client.account()` adapters in the screens.",
        "- If `Explicit product workflow requested: no`, do not invent product-specific tabs, custom product workflows, domain objects, or unsupported backend capabilities.",
        "- If `Explicit product workflow requested: yes`, the requested workflow is not invented extra scope; it is required, but it still must use the shared product action/generate rails and real receipts.",
        "- Do not fake persistence, fake synced records, fake AI results, or fake customer data. Customer work must be saved to the records rail before success is shown.",
        "",
        "Implementation bias:",
        "- Edit the seeded thin access/account surfaces in place first.",
        "- Preserve `_takyon/*`, `src/lib/takyon.ts`, `src/lib/hooks.ts`, and the existing runtime rail behavior.",
        "- Prefer upgrading the existing auth/account/profile shell over creating a new app architecture.",
        "",
        "Constraints:",
        "- Keep auth, account, profile, and checkout wired to Hermes/Takyon rails.",
        "- Do not expose runtime-internal wording to customers.",
        "- Do not invent unsupported backend capabilities.",
        "- The result should be publishable and product-specific on the first pass.",
        "- Always pass `refresh_surface: true`.",
        "- If `Explicit product workflow requested: no`, also pass `max_turns: 30`, `effort: low`, and `timeout_ms: 600000` — this pass restyles the two seeded access/account screens on EXISTING rails (no new architecture, no new routes), so the tight budget is sufficient and materially faster. Any internal recovery remains inside this call's one absolute deadline and must not create an overlapping worker.",
        "- Do NOT pass `wait_ms` — run this build to completion here. A fired/deferred build pays a full workspace re-materialize + cold sandbox start + a separate publish pass.",
        "- If `Explicit product workflow requested: yes`, this SAME second pass owns the real workflow build, so also pass `effort: high`, `max_turns: 90`, `budget_usd: 25.0`, and `timeout_ms: 1800000`. These are the established product-workflow limits and are intentionally separate from the Taste landing's medium/60/900 bounds. For `model`: PASS NOTHING — the deployment-pinned coding-worker model must remain unchanged for the whole run.",
        "",
        "Inspect the structured result from business_claude_agent_task, trusting only its exact success/blocker and surface_refresh publish status. If the product build or publish is blocked, record that exact blocker in research/strategy.md and stop bootstrap there. Do not paraphrase a different platform diagnosis. A `detached: true` result is NOT a blocker — re-call with the SAME arguments and idempotency_key only to observe and collect this same durable call; it must not create another attempt.",
        "",
        "#### 2c. Workflow verification gate, when explicitly requested",
        "If `Explicit product workflow requested: no`, skip this step.",
        "If `Explicit product workflow requested: yes`, do NOT start a third product build pass here. This step is only the verification gate for the workflow you just built in 2b.",
        "Use the business goal as the source of truth for the customer workflow. Example shape: a signed-in subscribed customer enters the requested input, triggers a real product action, and receives the requested AI output.",
        "Verify that `/app` is wired to at least one real non-underscore HTTP action file under `product/site/actions/`, and that the named action exists as customer-facing UI code rather than as an orphan backend file.",
        "Do NOT call business_invoke_app_action during ceo_bootstrap; app action execution requires a real signed-in subscribed product-user session and is verified immediately after bootstrap completes.",
        "If the action file is missing, schedule-only, placeholder-only, or the UI never calls it, record the exact blocker in research/strategy.md and stop bootstrap there. Do not declare final completion with a published access shell and no real workflow.",
        "",
        "Bootstrap ends once the complete required product is durably published (and the mobile release step below is terminal for mobile businesses). Do not perform deep market research, revise the published landing from later research, publish to X, run pulse work, or start distribution here. Those are bounded tasks for the existing scheduled CEO wake rail after /create completes.",
        *(
            [
                "",
                "### 3. iOS app build + first store-signed build (this business is archetype mobile_app)",
                "This business's deliverable includes a REAL iOS app, not only the web surface. Load takyon-mobile-app (skill_view) right before this step and follow its Procedure.",
                "Build the app source first: call business_claude_agent_task with workspace product/app. The platform seeds the pinned Expo scaffold into that workspace automatically and injects the mobile worker contract + build gates by code — instruct the worker to turn the seeded scaffold into this business's real app (screens, flows, copy, theme) from research/strategy.md and the offer, and to finish only on its injected green gates (npm ci + tsc + expo config).",
                "Then cut the first store-signed build: call business_publish_mobile_release with lane preview and a FRESH idempotency_key. The result's build_id + logs_url are the receipt; record them in metrics/ and reflect the milestone in the curated operator update.",
                "If the publish result is a blocker (compliance gate, credits, eas_builder_unconfigured), record the exact gate token in research/strategy.md and stop this step honestly. If the build triggers but later reports errored (check with business_read_store_status passing the build_id), run the takyon-mobile-app skill's Build-Failure Triage loop — maximum 3 total build attempts, each with a fresh idempotency_key.",
                "Customer-visible milestone: include an 'iOS app build' milestone (category PRODUCT) in the business_post_operator_update plan; describe it as building and packaging their mobile app — never name EAS, TestFlight internals, or tool names in customer-visible sentences.",
            ]
            if mobile_app
            else []
        ),
        "",
        "## Constraints",
        "Never fake auth, sessions, users, entitlements, checkout, subscriptions, outreach sends, deploys, revenue, metrics, or provider results.",
        "If a product feature is not wired to Hermes/Takyon rails, keep the customer surface normal and unavailable.",
        "Do not invent product workflow, extra tabs, or speculative routes unless the operator explicitly asked. If the operator explicitly asked for one, build and verify it in step 2c before completion.",
        "Missing credentials, budget authority, or provider gates are blockers; hard-fail instead of creating fake receipts.",
        "If any business_* tool says the business does not exist, stop immediately and report a platform provisioning failure.",
        "Do not retry business_write_file, and do not call business_create_workspace to paper over a missing business row.",
        "",
        "## Final response",
        "Concise status only: business filesystem root, what was created, what is blocked or missing.",
        "If a blocker has a clear next unblocked move, name that one re-run or follow-up explicitly.",
        *(
            [
                "This is a mobile_app business: the final response is only valid if you actually reached step 3. "
                "It MUST state the iOS build outcome — the build_id of the store-signed build, or the exact "
                "step-3 blocker. A final response that stops at the website (no step-3 build_id and no step-3 "
                "blocker) is a FAILED bootstrap, not a completion — keep going and do step 3 instead of closing.",
            ]
            if mobile_app
            else []
        ),
        "If `Explicit product workflow requested: yes`, the final response must include the action name and the workflow-verification result or the exact blocker. Never say the real workflow is \"coming soon\" after marking bootstrap complete; note that live action execution is the post-bootstrap signed-in subscriber verification step.",
        "If `Explicit product workflow requested: no` and only the landing + access shell were required, close by naming the next business move in warm, customer-facing language: the live site is a real starting point, and the next step is to build out the post-sign-in product experience. If `Explicit product workflow requested: yes`, close on the verified live workflow instead. Do NOT name any internal skill or tool; describe the work, not the runtime.",
    ]
    return "\n".join(lines)


def _ceo_bootstrap_turn_config(
    slug: str,
    goal: str,
    active_mode: str,
    *,
    business_name: str = "",
    animations: bool = False,
    archetype: str = "",
) -> dict[str, Any]:
    return {
        "user_prompt": _business_bootstrap_instruction(
            slug,
            goal,
            active_mode,
            business_name=business_name,
            animations=animations,
            archetype=archetype,
        ),
        "ephemeral_system_prompt": _ceo_prompt_for_bootstrap(),
        # Same CEO toolset as the interactive/cron turns: ``takyon-authority`` carries the spendful
        # business methods this first-business turn legitimately drives (e.g. app-plan/access-shell
        # provisioning). They stay quarantined in their own toolset (never folded into ``takyon``) so
        # they cannot leak into generic Hermes/sub-agent/product-runtime contexts; the tools are
        # fail-closed money gates and worker-only operations self-guard against session-bound calls.
        "enabled_toolsets": ["takyon", "takyon-authority", "skills"],
        "disabled_toolsets": [
            "cronjob",
            "messaging",
            "clarify",
            "memory",
            "session_search",
            "terminal",
            "file",
            "browser",
            "code_execution",
        ],
        "load_soul_identity": False,
        "skip_memory": True,
        "skip_context_files": True,
        "max_turns": _bootstrap_turn_cap_for_goal(goal, archetype=archetype),
    }


def _harness_root() -> Path:
    return Path(os.getenv("TAKYON_HARNESS_ROOT") or Path(__file__).parent / "harness").resolve()


def _load_harness_settings() -> dict[str, Any]:
    path = _harness_root() / "settings.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid Takyon harness settings {path}: {exc}") from exc


def _shell_progress_config() -> dict[str, Any]:
    settings = _load_harness_settings()
    ui = settings.get("ui") if isinstance(settings.get("ui"), dict) else {}
    progress = ui.get("progress") if isinstance(ui.get("progress"), dict) else {}
    try:
        max_lines = int(progress.get("maxLinesPerTool", 6))
    except (TypeError, ValueError):
        max_lines = 6
    return {
        "enabled": _config_bool(progress.get("enabled"), default=True),
        "show_business_root": _config_bool(progress.get("showBusinessRoot"), default=True),
        "show_durable_writes": _config_bool(progress.get("showDurableWrites"), default=True),
        "max_lines": max(1, min(max_lines, 8)),
    }


def _parse_tool_json_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    if not isinstance(result, str):
        return {}
    try:
        loaded = json.loads(result)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _shell_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _shell_money_cents(value: Any) -> str:
    cents = _shell_int(value)
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100}.{cents % 100:02d}"


def _shell_metric_value(stats: dict[str, Any], *names: str) -> int | None:
    for name in names:
        if name not in stats:
            continue
        value = stats.get(name)
        if isinstance(value, dict):
            for key in ("value", "count", "total"):
                if key in value:
                    return _shell_int(value.get(key))
            continue
        return _shell_int(value)
    return None


def _shell_analytics_line(analytics: dict[str, Any], *, prefix: str = "traffic") -> str:
    if not analytics:
        return f"{prefix} -> unavailable"
    if not analytics.get("configured"):
        reason = str(analytics.get("reason") or "not configured").strip()
        return f"{prefix} -> not configured ({reason})"
    if analytics.get("ok") is False:
        reason = str(analytics.get("reason") or "provider unavailable").strip()
        return f"{prefix} -> unavailable ({reason})"
    stats = analytics.get("stats") if isinstance(analytics.get("stats"), dict) else {}
    visitors = _shell_metric_value(stats, "visitors", "uniques", "unique_visitors")
    visits = _shell_metric_value(stats, "visits", "sessions")
    pageviews = _shell_metric_value(stats, "pageviews", "views")
    days = _shell_int(analytics.get("window_days") or analytics.get("days") or 0)
    window = f"{days}d " if days else ""
    parts = []
    if visitors is not None:
        parts.append(f"visitors={visitors}")
    if visits is not None:
        parts.append(f"visits={visits}")
    if pageviews is not None:
        parts.append(f"pageviews={pageviews}")
    if not parts:
        return f"{prefix} -> {window}configured, no counted stats"
    return f"{prefix} -> {window}{' '.join(parts)}"


def _read_business_progress_lines(data: dict[str, Any], args: dict[str, Any]) -> list[str]:
    business = data.get("business") if isinstance(data.get("business"), dict) else {}
    app = data.get("app") if isinstance(data.get("app"), dict) else {}
    slug = str(
        business.get("slug")
        or data.get("business")
        or args.get("business")
        or ""
    ).strip()
    name = str(business.get("name") or slug or "business").strip()
    lines: list[str] = []
    if slug or name:
        lines.append(f"state -> {name}{f' ({slug})' if slug and slug != name else ''}")

    product = app.get("product_surface") if isinstance(app.get("product_surface"), dict) else {}
    surface = app.get("surface_contract") if isinstance(app.get("surface_contract"), dict) else {}
    publish_status = str(product.get("publish_status") or surface.get("publish_status") or "").strip()
    public_url = str(product.get("public_url") or surface.get("publish_target") or "").strip()
    if publish_status or public_url:
        details = " ".join(part for part in (publish_status, public_url) if part)
        lines.append(f"product -> {details}")

    users = app.get("customers") if isinstance(app.get("customers"), list) else []
    entitlements = app.get("entitlements") if isinstance(app.get("entitlements"), list) else []
    paid = sum(
        1
        for item in entitlements
        if isinstance(item, dict)
        and str(item.get("status") or "").lower() in {"active", "trialing"}
        and str(item.get("tier") or "").lower() in {"paid", "pro", "team", "owner"}
    )
    revenue = app.get("revenue") if isinstance(app.get("revenue"), dict) else {}
    usage = app.get("usage_this_period") if isinstance(app.get("usage_this_period"), dict) else {}
    if users or entitlements or revenue or usage:
        lines.append(
            "app -> "
            f"users={len(users)} paid={paid} "
            f"revenue={_shell_money_cents(revenue.get('amount_paid_cents'))} "
            f"usage_events={_shell_int(usage.get('events'))}"
        )

    jobs = data.get("jobs") if isinstance(data.get("jobs"), list) else []
    if jobs:
        queued = sum(1 for item in jobs if isinstance(item, dict) and str(item.get("status") or "").lower() == "queued")
        latest = next((item for item in jobs if isinstance(item, dict)), {})
        latest_kind = str(latest.get("kind") or latest.get("job_type") or latest.get("type") or "job").strip()
        latest_status = str(latest.get("status") or "unknown").strip()
        lines.append(f"jobs -> queued={queued} latest={latest_kind}:{latest_status}")

    controls = data.get("controls") if isinstance(data.get("controls"), list) else []
    wake_state = next(
        (
            str(item.get("state") or item.get("status") or "").strip()
            for item in controls
            if isinstance(item, dict) and str(item.get("scope") or "").startswith(f"business:{slug}")
        ),
        "",
    )
    if wake_state:
        lines.append(f"controls -> {wake_state}")
    return lines


def _pulse_progress_lines(data: dict[str, Any]) -> list[str]:
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    state = data.get("current_state") if isinstance(data.get("current_state"), dict) else {}
    product = state.get("product_surface") if isinstance(state.get("product_surface"), dict) else {}
    lines = [
        "pulse -> "
        f"users={_shell_int(summary.get('users'))} "
        f"paid={_shell_int(summary.get('paid_customers'))} "
        f"mrr={_shell_money_cents(summary.get('mrr_cents'))}/mo "
        f"revenue={_shell_money_cents(summary.get('revenue_cents'))} "
        f"usage_events={_shell_int(summary.get('usage_events'))} "
        f"queued_jobs={_shell_int(summary.get('queued_jobs'))} "
        f"unresolved={_shell_int(summary.get('unresolved_inbound'))}"
    ]
    publish_status = str(product.get("publish_status") or product.get("status") or "").strip()
    public_url = str(product.get("public_url") or "").strip()
    blocker = str(product.get("publish_blocker") or "").strip()
    if publish_status or public_url:
        lines.append(f"product -> {' '.join(part for part in (publish_status, public_url) if part)}")
    if blocker and blocker.lower() not in {"none", "null"}:
        lines.append(f"blocker -> {blocker}")
    analytics = data.get("web_analytics") if isinstance(data.get("web_analytics"), dict) else {}
    if analytics:
        lines.append(_shell_analytics_line(analytics))
    return lines


def _tool_progress_lines(name: str, args: dict[str, Any], result: Any) -> list[str]:
    if not str(name or "").startswith("business_"):
        return []
    config = _shell_progress_config()
    data = _parse_tool_json_result(result)
    if str(name or "") == "business_read_business":
        return _read_business_progress_lines(data, args)
    if str(name or "") == "business_calculate_pulse":
        return _pulse_progress_lines(data)
    if str(name or "") == "business_read_app_analytics":
        return [_shell_analytics_line(data)]
    results = data.get("results") if isinstance(data.get("results"), list) else []
    if not results and data.get("action"):
        results = [data]
    if not results and data.get("success") and str(name or "") == "business_create_app_checkout":
        business = str(data.get("business") or args.get("business") or "").strip()
        lines = []
        if business:
            lines.append(f"checkout intent created for business:{business}")
            if str(data.get("external_side_effects") or "") == "suppressed":
                checkout_id = str(data.get("checkout_intent_id") or "")
                if checkout_id:
                    lines.append(f"checkout receipt -> {_business_artifact_path(business, f'metrics/receipts/app-checkout/{checkout_id}.json')}")
        return lines
    if not results and str(name or "") == "business_claude_agent_task":
        business = str(data.get("business") or args.get("business") or "").strip()
        workspace = str(data.get("workspace") or args.get("workspace") or ".").strip() or "."
        lines = []
        if business:
            lines.append(f"agent workspace -> {_business_artifact_path(business, workspace)}")
            surface_refresh = data.get("surface_refresh") if isinstance(data.get("surface_refresh"), dict) else {}
            if surface_refresh:
                status = surface_refresh.get("status") or "unrefreshed"
                receipt = surface_refresh.get("receipt_path") or ""
                suffix = f" -> {_business_artifact_path(business, receipt)}" if receipt else ""
                lines.append(f"product publish check {status}{suffix}")
            agent_record = data.get("agent_record") if isinstance(data.get("agent_record"), dict) else {}
            for line in _tool_progress_lines("business_record_agent", {"business": business}, agent_record)[:1]:
                lines.append(line)
        return lines
    if not results and str(name or "") == "business_refresh_product_surface":
        business = str(data.get("business") or args.get("business") or "").strip()
        surface_refresh = data.get("surface_refresh") if isinstance(data.get("surface_refresh"), dict) else {}
        if business and surface_refresh:
            status = surface_refresh.get("status") or "unrefreshed"
            receipt = surface_refresh.get("receipt_path") or ""
            suffix = f" -> {_business_artifact_path(business, receipt)}" if receipt else ""
            return [f"product publish check {status}{suffix}"]
    lines: list[str] = []
    seen_root: set[str] = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "")
        business = str(item.get("business") or item.get("business_slug") or args.get("business") or "").strip()
        if config["show_business_root"] and business and business not in seen_root and action == "business.upsert":
            seen_root.add(business)
            lines.append(f"business:{business} filesystem -> {_business_root(business)}")
        if not config["show_durable_writes"]:
            continue
        if action in {"artifact.write", "artifact.patch", "memory.write"}:
            path = str(item.get("path") or "")
            if business and path:
                lines.append(f"file -> {_business_artifact_path(business, path)}")
        elif action == "workspace.upsert":
            workspace = str(item.get("workspace") or "")
            if business and workspace:
                lines.append(f"workspace -> {_business_artifact_path(business, workspace)}")
        elif action == "outreach.local_publish":
            artifact = str(item.get("artifact") or "")
            if business and artifact:
                lines.append(f"local outreach -> {_business_artifact_path(business, artifact)}")
            receipt = str(item.get("receipt") or "")
            if business and receipt:
                lines.append(f"receipt -> {_business_artifact_path(business, receipt)}")
        elif action == "app.surface.upsert":
            if business:
                lines.append(f"product surface -> {_business_artifact_path(business, 'product/surface.md')}")
        elif action == "app.surface.publish_result":
            if business:
                status = str(item.get("publish_status") or "not_published")
                url = str(item.get("public_url") or item.get("publish_target") or "")
                suffix = f" ({url})" if url else ""
                lines.append(f"app surface publish {status} for business:{business}{suffix}")
        elif action == "app.plan.upsert":
            if business:
                plan = str(item.get("plan_key") or "")
                suffix = f" ({plan})" if plan else ""
                lines.append(f"app plan policy updated for business:{business}{suffix}")
        elif action in {"app.customer.upsert", "app.entitlement.upsert"}:
            if business:
                lines.append(f"app customer/entitlement state updated for business:{business}")
        elif action == "app.usage.record":
            if business:
                lines.append(f"app usage recorded for business:{business}")
        elif action in {"conversation.thread.upsert", "conversation.message.record"}:
            path = str(item.get("file") or "")
            if business and path:
                lines.append(f"conversation -> {_business_artifact_path(business, path)}")
        elif action == "business.mode.set":
            if business:
                lines.append(f"business:{business} mode -> {item.get('mode')}")
        elif action == "business.focus.set":
            if business:
                lines.append(f"business:{business} work focus -> {item.get('work_focus') or 'all'}")
        elif action == "cron.ensure_ceo_wakeup":
            if business:
                state = "enabled" if item.get("enabled") else "paused"
                lines.append(f"wake schedule {state} -> business:{business} {item.get('schedule') or item.get('cron_job')}")
        elif action == "job.enqueue":
            if business:
                lines.append(f"job queued -> business:{business} {item.get('job') or item.get('id') or ''}".rstrip())
        elif action == "agent.record":
            if business:
                lines.append(f"agent record -> business:{business} {item.get('agent_run') or item.get('id') or ''}".rstrip())
    return lines


def _config_path(store: TakyonStore) -> Path:
    return store.root / "config.yaml"


def _read_model_config(store: TakyonStore) -> dict[str, str]:
    path = _config_path(store)
    provider = ""
    model = ""
    claude_agent_model = ""
    response_style = ""
    show_agent_activity = ""
    shell_enhanced_input = ""
    auto_schedule_ceo_on_create = ""
    default_ceo_schedule = ""
    if path.exists():
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            model_data = data.get("model") or {}
            provider = str(model_data.get("provider") or "")
            model = str(model_data.get("default") or model_data.get("model") or "")
            claude_agent_model = str(
                model_data.get("claude_agent_default")
                or model_data.get("deep_work_default")
                or ""
            )
            conversation_data = data.get("conversation") or {}
            if isinstance(conversation_data, dict):
                response_style = str(conversation_data.get("response_style") or "")
                show_agent_activity = str(conversation_data.get("show_agent_activity") or "")
            shell_data = data.get("shell") or {}
            if isinstance(shell_data, dict):
                shell_enhanced_input = str(shell_data.get("enhanced_input") or "")
            business_data = data.get("business") or {}
            if isinstance(business_data, dict):
                auto_schedule_ceo_on_create = str(business_data.get("auto_schedule_ceo_on_create") or "")
                default_ceo_schedule = str(business_data.get("default_ceo_schedule") or "")
        except Exception:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if stripped.startswith("provider:"):
                    provider = stripped.split(":", 1)[1].strip()
                if stripped.startswith("default:"):
                    model = stripped.split(":", 1)[1].strip()
                if stripped.startswith("claude_agent_default:"):
                    claude_agent_model = stripped.split(":", 1)[1].strip()
                if stripped.startswith("response_style:"):
                    response_style = stripped.split(":", 1)[1].strip()
                if stripped.startswith("show_agent_activity:"):
                    show_agent_activity = stripped.split(":", 1)[1].strip()
                if stripped.startswith("enhanced_input:"):
                    shell_enhanced_input = stripped.split(":", 1)[1].strip()
                if stripped.startswith("auto_schedule_ceo_on_create:"):
                    auto_schedule_ceo_on_create = stripped.split(":", 1)[1].strip()
                if stripped.startswith("default_ceo_schedule:"):
                    default_ceo_schedule = stripped.split(":", 1)[1].strip()
    return {
        "provider": provider,
        "model": model,
        "claude_agent_model": claude_agent_model,
        "response_style": response_style,
        "show_agent_activity": show_agent_activity,
        "shell_enhanced_input": shell_enhanced_input,
        "auto_schedule_ceo_on_create": auto_schedule_ceo_on_create,
        "default_ceo_schedule": default_ceo_schedule,
        "path": str(path),
    }


def _config_bool(value: Any, *, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _require_agent_model_config(config: dict[str, str], *, model_override: str | None = None) -> str:
    provider = config.get("provider", "")
    resolved_model = model_override or os.getenv("TAKYON_MODEL", "") or config.get("model", "")
    if provider and resolved_model:
        return resolved_model
    missing = []
    if not provider:
        missing.append("model.provider")
    if not resolved_model:
        missing.append("model.default")
    path = config.get("path") or str(_config_path(TakyonStore()))
    raise TakyonError(
        f"Takyon model config missing {', '.join(missing)} in {path}. "
        "Run `takyon model set <provider> <model>` or copy the workspace config into this TAKYON_HOME."
    )


def _load_ceo_prompt() -> str:
    return _CEO_PROMPT_PATH.read_text(encoding="utf-8")


def _strip_fenced_block(text: str, name: str) -> str:
    """Drop an HTML-comment-fenced span (``<!-- name:START -->`` … ``<!-- name:END -->``)
    from a prompt. Lets one rule in the shared ceo.md be scoped to a single turn kind in
    code without forking the file. No-op if the fence is absent."""
    start, end = f"<!-- {name}:START -->", f"<!-- {name}:END -->"
    i, j = text.find(start), text.find(end)
    if i == -1 or j == -1 or j < i:
        return text
    j += len(end)
    if j < len(text) and text[j] == "\n":
        j += 1
    return text[:i] + text[j:]


def _ceo_prompt_for_bootstrap() -> str:
    # Bootstrap runs the standard build sequence under its own instruction, not a single
    # operator request to inspect — so drop the per-request completion-discipline rule.
    return _strip_fenced_block(_load_ceo_prompt(), "COMPLETION-DISCIPLINE")
