---
name: takyon-conversation-response
description: Delegate high-volume replies, comments, outreach results, and support conversations to a scoped response agent under CEO objectives and guardrails.
---

# Takyon Conversation Response

Use this skill when a business has replies, comments, support messages, outreach results, or other conversations that are too large or too operational for the CEO to handle directly.

## Practice

- Treat conversation data as business evidence, not an interrupt policy.
- Preserve all raw conversation history in the business corpus; operate from summaries, samples, and slices when volume is high.
- The CEO chooses the objective: convert interested leads, defuse risk, learn objections, identify high-value threads, draft batches, or answer selectively.
- Prefer `business_conversation_agent_task` for bounded response work. Give it a clear objective, volume limit, channel/status filters, action cap, and whether it may apply local actions.
- The response agent may triage, cluster, sample, ignore, escalate, draft, record local outbound messages, mark statuses, propose queued external send/post jobs, and extract learnings.
- Do not assume every reply deserves a response. For large volume, batch and prioritize by business impact, recency, risk, and operator direction.
- External sending, posting, vendor calls, spend, and channel mutation still require guarded tools, credentials, budget gates, and concrete receipts. In test mode, use suppressed local receipts rather than claiming live delivery.
- Promote reusable objections, customer language, lead patterns, and failures into the business brain only when they should guide future CEO decisions.

Do not build a deterministic inbox workflow or "reply to all" lane. This is a specialist operating method the CEO invokes when the evidence calls for it.
