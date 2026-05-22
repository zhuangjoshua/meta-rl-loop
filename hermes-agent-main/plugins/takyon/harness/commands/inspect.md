---
description: Inspect visible state, unknowns, blockers, and business filesystem evidence
requires-business: true
priority-band: p1_ceo
allowed-tools: [read, workspace, memory, control]
---

Inspect `business:$BUSINESS` and report only what is visible in Takyon state, business files, conversations, ledgers, controls, events, and recorded agent runs.

Operator arguments:

`$ARGUMENTS`

Return:

- what is known
- what is unknown
- blockers and missing capabilities
- unresolved inbound conversations that should stop outward distribution
- which files or receipts should exist next
- whether product/value delivery and demand creation both have live evidence

Do not invent hidden state or infer a completed action from a plan.
