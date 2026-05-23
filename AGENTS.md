# Takyon Workspace Instructions

## Source Of Truth

This workspace has one runnable Takyon trunk:

```text
/Users/Zygote/Downloads/takyon/takyon
  -> /Users/Zygote/Downloads/takyon/hermes-agent-main
```

`./takyon` at the workspace root is the canonical operator entrypoint. It launches the Hermes/Takyon runtime with `TAKYON_HOME` rooted at `/Users/Zygote/Downloads/takyon/.takyon`.

`hermes-agent-main` is the active runtime, CEO, skill, cron, and plugin trunk.

The old `polsia3` app tree was removed. Archived source material salvaged from it lives under `hermes-agent-main/plugins/takyon/references/polsia3-skills/`.

Do not recreate `polsia3`, do not restore `polsia3/takyon`, and do not describe `polsia3` as "the active Next/Takyon app", "the main trunk", or "one of two trunks".

## User Terms

Do not conflate Takyon users with product subusers.

- A Takyon user is the top-level operator/account that owns a Takyon agent and may create businesses.
- A product subuser/app customer is a customer of a business/product created by that Takyon user.
- Top-level user-scoped APIs, credentials, budgets, and identity are different from business/product customer auth, entitlements, usage, billing, and scoped API keys.

## Product App Surfaces

The shared Hermes app runtime owns backend rails only: auth/session protocol, payment/webhook reconciliation, entitlement policy, app usage budget accounting, state mirrors, and safety gates.

Do not hardcode the final product's look, layout, copy, theme, or information architecture in the runtime. Store that per business with the `business_upsert_app_surface_contract` tool, mirrored at `app/surface.md`, and point it at the business design brief/source path that the CEO and skills should inspect.

Safety constraints must not erase the operator's specified business domain. Preserve the operator's intent in direct, accurate, non-sensational language while obeying claims, safety, credential, and provider guardrails. Physical subject matter does not imply physical fulfillment: unless the operator explicitly asks this business to sell, ship, prescribe, perform, or guarantee a physical thing, express the business as a lawful software-native product around the real-world subject.

## Operating Model

Takyon should be a skill-based Hermes CEO system, not a fixed workflow cockpit.

Practice parsimony and no slop. Parsimony means the system works through the smallest Hermes-native surface that genuinely handles the job for every business and every relevant mode. Do not create a second path for test mode, a one-off shell workaround, a special-case business bootstrap, or a fake/demo product behavior when the same skill, business-scoped tool, receipt, or runtime rail can express the work with the right gates. Mode differences should be explicit guardrails on the same path: in test mode suppress or stub external side effects with receipts; in live mode require the real provider, budget, permission, and receipt. Prefer one clear operator affordance with flags over multiple overlapping commands, aliases, or workflow shortcuts. If a shell action is part of creating a business, put it on `/create` as an explicit flag such as `--test`, `--schedule`, or `--no-auto`; do not add separate slash commands that duplicate the creation path.

Use canonical sources of truth wherever they exist. Runtime config belongs in `$TAKYON_HOME/config.yaml`; tool and skill metadata belong in `plugins/takyon/registry.py`; shell/harness command metadata belongs in `plugins/takyon/harness/settings.json` and `plugins/takyon/harness/commands/*.md`; business facts belong in `state.sqlite3` and the per-business filesystem. Do not duplicate those facts in prompts or UI code when they can be read from the canonical source.

Do not answer diagnosis with artifact edits. If the operator asks why something happened, whether behavior is correct, or how to improve it, inspect the relevant source of truth and answer. Do not patch a generated business website, outreach file, app copy, or local artifact unless the operator explicitly asks to change that artifact. If the fix is systemic, make the smallest Hermes-native change in the relevant skill, tool, registry, harness metadata, or AGENTS instruction.

The CEO's prime directive is to find users and become profitable. Product, ICP, distribution, pricing, conversations, and follow-up are subordinate to that directive. On initial CEO bootstrap and every scheduled CEO wake, the CEO must re-evaluate the current ICP, where that ICP concentrates, what promise/product they would pay for, how Takyon can reach them with current permissions, what evidence changed since the last run, what should change in product, ICP, pricing, or distribution, and the highest expected-profit move now. Treat those answers as revisable beliefs stored in the business brain, not permanent metadata. Distribution is required thinking, not forced outreach; choose, defer, or revise distribution tactics based on the business state and record the reasoning when it should guide future wakes.

When conversation, outreach, or user evidence is too large, noisy, or operational for the CEO to inspect cheaply, use the existing `business_conversation_agent_task` / `takyon:conversation-response` path to summarize responses, extract objections and lead patterns, identify ICP/product/pricing/distribution implications, and optionally draft replies. The CEO remains the decider; delegate outputs become evidence for brain updates and business actions, not a separate workflow.

Cron should wake the Hermes/Takyon CEO for a business. The CEO must inspect the current business state, honor the latest operator query, infer what matters now, and then choose the active Takyon skill or concrete `business_*` tool that fits. Do not encode brittle funnels, stage ladders, fixed next-workflow catalogs, or "if no X then always do Y" strategy in prompts, runtime code, cron, or AGENTS instructions.

Inbound replies, comments, support messages, and outreach results are business evidence, not a hardcoded interrupt policy. Store them durably per business and make them visible to the CEO, but do not add rules like "always handle every reply before outreach" or "always respond to all unresolved messages." The CEO should triage, batch, ignore, escalate, learn from, or answer messages based on business impact, volume, recency, risk, budget, operator direction, and current strategy.

Hardcode only durable rails and safety primitives: business isolation, path containment, idempotency, credential gates, budget caps, audit events, pause/resume/kill controls, conservative cleanup, and the shared Hermes app runtime APIs. Strategy, prioritization, product direction, outreach motion, design, and learning belong in per-business brain/workspace state plus active Takyon skills.

Use the active skills under `hermes-agent-main/plugins/takyon/skills/` as operating methods. In particular:

- Use `takyon:ceo` as the state-aware router.
- Use `takyon:claude-agent-sdk` / `business_claude_agent_task` when difficult coding, source inspection, or business-scoped workspace edits need a focused Claude Agent SDK worker with path, budget, credential, and audit guardrails.
- Use `takyon:app-runtime` and the canonical app tools for product customer auth, magic links, sessions, app customers/subusers, entitlements, plan policy, checkout, Stripe webhook reconciliation, revenue, and usage budgets.
- Use `takyon:outreach`, `takyon:conversation-response`, `takyon:distribution-campaign`, `takyon:ad-creative`, `takyon:market-research`, `takyon:pricing-strategy`, `takyon:conversion-review`, `takyon:business-learning`, and `takyon:failure-recovery` when the evidence and operator query call for them.

External side effects such as posting, sending, enrichment, deploys, vendor calls, paid spend, and media generation require explicit budget/API/env gates and concrete receipts. Queue or record them through guarded business tools; do not claim execution from a draft, plan, or model guess.

Archived `polsia3` reference files may mention fixed workflow IDs or old workflow catalogs. Treat those as historical source material only. Do not copy that architecture back into the active Takyon/Hermes system.

## Test Mode

Do not claim Takyon is running in test mode unless a specific business has `businesses.mode = 'test'`. Test mode is per business, not global. It still writes durable test-marked state to `.takyon/state.sqlite3` and `.takyon/businesses/<business>/`; it is not a separate `TAKYON_HOME`.

In test mode, Takyon should keep planning, product/website build and publication, receipts, conversations, app rails, cron wakeups, and follow-up review active. Product and website surfaces may be built, published locally, or deployed when the normal path, budget, credential, and receipt/job gates pass. Outbound acquisition and money-movement side effects such as outreach posting, sending, enrichment, ads, spend, live Stripe charging, and Postmark marketing/email delivery must be suppressed or stubbed locally with concrete receipts such as `external_side_effects=suppressed`. Missing outbound-provider keys should not block local execution of a chosen external distribution tactic. Missing core model/runtime capability is still a blocker.

Operator changes to live/test mode must go through `business_set_mode` or the registry-driven shell command `/test on|off|status`. Do not add parallel per-channel test flags.

## Hermes-Style Takyon Work

Implement Takyon behavior the Hermes way: the CEO reasons through skills, skills describe operating modes, tools provide guarded state changes/side effects, and the business filesystem records durable context. Do not add local deterministic business flows, one-off worker stages, hardcoded startup sequences, or UI-only command lists when a skill, registry entry, harness command file, or business-scoped tool can express the behavior.

When Takyon creates or updates business artifacts, the operator should be able to see where they landed. Surface the business filesystem root and paths for outreach, website/app, product, campaign, receipt, conversation, job, and wakeup deliverables through tool results, shell progress, or concise CEO reports.

Default rule: everything business-facing should be a Takyon skill or a business-scoped tool visible in `business_registry`. The clear exceptions are shared safety/control rails: path containment, idempotency, credential checks, budget gates, pause/kill controls, cron wake scheduling, auth/session/payment/webhook protocol, and UI rendering mechanics. Even those exceptions should publish their user-facing command metadata through a single source of truth rather than separate UI hardcoding.

Slash-command UI must be registry-driven. Shell palettes/help should derive from `hermes-agent-main/plugins/takyon/harness/settings.json` for controls, `hermes-agent-main/plugins/takyon/harness/commands/*.md` for harness skill commands, and `hermes-agent-main/plugins/takyon/registry.py` for Takyon skills/tools. If a future command or skill is added, updating that canonical source must be enough for shell discovery; do not duplicate slash command names/descriptions in the UI.

## Takyon Shell Model

The Takyon shell is always in a scope. `global` is the top-level account/root scope; it is not the CEO. A business scope is `business:<slug>`.

The CEO is the scoped operator agent role. Plain text in the shell should route to the CEO for the current scope, except obvious shell self-help questions such as "how do I create a business", which should answer locally and tersely. The shell must preserve recent in-session turns so follow-ups like "make #1" can resolve against what the CEO just said; tune that through `harness/settings.json`, not hardcoded prompts. `/ceo` is only a focus/status affordance because the CEO is already the default interface.

For actionable business requests, the shell/CEO posture is autonomous execution. Do not respond with "say X and I'll do it", a tool-call recipe, or a staged checklist when the operator has already named a business, supplied a goal, or chosen an idea. Use business tools and skills to make durable progress, then report what changed and where it landed.

Slash commands are narrow shell controls only: scope navigation, local status/inspection, setup/config, debug/doctor, server start/stop, and exit. Product-building behavior belongs in skills, tools, registry metadata, and per-business state.

Business creation belongs to one shell command: `/create`. Creation-time choices such as test mode, immediate CEO start, and wake cadence should be flags on `/create`, not separate slash commands.

When the CEO is working, the shell must leave a visible operator lane. Do not use spinners, carriage-return redraws, explanatory status text, or progress output that occupies or clears the input row. The active-run indicator should be minimal and visual: use a blinking `*` only when it can be cleared on completion, and let streaming tool progress stand alone when progress lines are visible. If true mid-turn injection is not available, document that in help/status surfaces rather than cluttering the active input area.

## Agent Behavior

Do not stop at analysis because the workspace is dirty. There may be existing user or previous-agent edits. Read the relevant files, preserve unrelated changes, and make the requested change in the canonical location.

Do not begin with a verbose prompt restatement. If the request is actionable, inspect and act. If context is ambiguous, ask one concise question or state the assumption you are using.

When in doubt:

1. Use the root `./takyon`.
2. Treat Hermes as CEO/runtime/cron owner.
3. Use archived reference material only from `hermes-agent-main/plugins/takyon/references/polsia3-skills/`.
4. Keep business state in the canonical Takyon/Hermes store and per-business filesystem.
