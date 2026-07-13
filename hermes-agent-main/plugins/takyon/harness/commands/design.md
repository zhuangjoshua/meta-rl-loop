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

For focused edits inside the business workspace, use the owning Takyon skill and let it delegate `business_claude_agent_task` when needed; this keeps path containment, budget allocation, and audit truth on the owner rail instead of a generic helper. Use the full pinned `taste-frontend` skill explicitly and alone only for an initial public landing: one continuous call with `effort: medium`, `max_turns: 60`, and one total `timeout_ms: 900000` deadline from Design Read through final preflight. Persist the one-line Design Read, three exact dial values, selected foundation, and durable asset decisions in `product/site/DESIGN.md`; after build/typecheck, the worker must call `business_render_landing_preflight` and read its real 1440x900 and 390x844 screenshots, fixing, rebuilding, and using its one remaining call if either first viewport is incomplete. It must not start Vite, Chromium, or agent-browser itself. For a dashboard, data table, multi-step product UI, or other product continuation, pass `guidance_skills: []` and inherit `product/site/DESIGN.md`, the established landing tokens, and assets instead of choosing another direction. A generic `product/site` call with omitted guidance adds no design template. Never rerun Taste or add a design preset during later product work. The App Kit route, auth, checkout, entitlement, account, and action contracts remain immutable. Do not run daemons, mutate global repo files, or write outside this business workspace.

Design work should leave durable business context behind in the canonical surface/source path: rationale, assets or source changes, QA notes, and next open questions.
