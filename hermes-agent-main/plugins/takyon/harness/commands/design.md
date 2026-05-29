---
description: Create or apply a business-owned product/web/app design brief
requires-business: true
priority-band: p2_growth
allowed-tools: [read, workspace, memory, agent]
---

Design or improve the webpage, app, or product surface for `business:$BUSINESS`.

Operator arguments:

`$ARGUMENTS`

Use explicit business evidence: goal, audience, offer, product state, conversion evidence, support/conversation state, and any existing product or website files. If evidence is missing, write the smallest useful design brief first.

For focused edits inside the business workspace, prefer the `takyon:claude-agent-sdk` skill and `business_claude_agent_task`; it provides path containment, budget allocation, and an audit record. For non-trivial `product/site` design work, pass `guidance_skills: ["claude-design"]`. Do not run daemons, mutate global repo files, or write outside this business workspace.

Design work should leave durable business context behind: brief, rationale, assets or source changes, QA notes, and next open questions.
