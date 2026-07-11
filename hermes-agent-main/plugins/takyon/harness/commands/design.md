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

For focused edits inside the business workspace, use the owning Takyon skill and let it delegate `business_claude_agent_task` when needed; this keeps path containment, budget allocation, and audit truth on the owner rail instead of a generic helper. For every `product/site` design pass, layer `taste-frontend` above `claude-design` and at most one brief-fitting `claude-design-*` visual system. Taste adapts the system to the business; it does not replace it. The App Kit route, auth, checkout, entitlement, account, and action contracts remain immutable. Do not run daemons, mutate global repo files, or write outside this business workspace.

Design work should leave durable business context behind in the canonical surface/source path: rationale, assets or source changes, QA notes, and next open questions.
