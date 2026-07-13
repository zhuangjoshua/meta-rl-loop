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

For focused edits inside the business workspace, use the owning Takyon skill and let it delegate `business_claude_agent_task` when needed; this keeps path containment and budget authority on the owner rail instead of a generic helper. The worker runtime installs the pinned `design-taste-frontend` once as a native Claude Code skill and includes it in every Agent SDK session. Never pass `guidance_skills`, paste or summarize the skill into an instruction, install it per business, or add another design preset. Invoke the native skill and let it own design decisions. Original imagery is optional and available through the Safebox-gated image tool when it improves the product; generated-asset counts, DESIGN.md, screenshots, audits, and aesthetic scores are not publication conditions. Preserve useful existing direction on later passes. The App Kit route, auth, checkout, entitlement, account, and action contracts remain immutable. Do not run daemons, mutate global repo files, or write outside this business workspace.

Design work should leave durable business context behind in the canonical surface/source path: rationale, assets or source changes, QA notes, and next open questions.
