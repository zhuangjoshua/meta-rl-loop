---
description: Create or apply a business-owned product/web/app design direction
requires-business: true
priority-band: p2_growth
allowed-tools: [read, workspace, memory, agent]
---

Design or improve the webpage, app, or product surface for `business:$BUSINESS`.

Operator arguments:

`$ARGUMENTS`

Use explicit business evidence: goal, audience, offer, product state, conversion evidence, support/conversation state, and any existing product or website files. Prefer updating the canonical product surface and source directly instead of seeding a separate design brief artifact.

For focused edits inside the business workspace, use the owning Takyon skill and let it delegate `business_claude_agent_task` when needed; this keeps path containment, budget allocation, and audit truth on the owner rail instead of a generic helper. The worker runtime installs the pinned `design-taste-frontend` once as a native Claude Code skill and includes it in every Agent SDK session. Never pass `guidance_skills`, paste or summarize the skill into an instruction, install it per business, or add another design preset. For an initial landing, invoke the native skill and let it own brief inference, Design Read, dials, foundation, generated-image decisions, implementation, rendered inspection, and the complete pre-flight. Persist its durable direction and image plan in `product/site/DESIGN.md`; missing native-use receipts or failed 1440x900 and 390x844 publication evidence block publish. For a dashboard, data table, multi-step product UI, or later product pass, invoke the installed skill, honor its own scope boundary, and preserve the existing `DESIGN.md`, landing, tokens, and assets instead of replacing the established direction. The App Kit route, auth, checkout, entitlement, account, and action contracts remain immutable. Do not run daemons, mutate global repo files, or write outside this business workspace.

Design work should leave durable business context behind in the canonical surface/source path: rationale, assets or source changes, QA notes, and next open questions.
