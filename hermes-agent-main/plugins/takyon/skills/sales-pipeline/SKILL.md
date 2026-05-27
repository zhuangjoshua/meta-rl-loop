---
name: takyon-sales-pipeline
description: Manage sales-led prospects, accounts, opportunities, touches, and follow-up.
---

# Takyon Sales Pipeline

Use this skill when sales-led growth is the chosen motion: named prospects, account research, partner targets, demos, follow-ups, opportunities, close-rate learning, or founder-led sales.

## Practice

- Keep terms clean. Sales prospects, contacts, accounts, and opportunities are not Takyon users and are not product app customers/subusers unless canonical app tools create those app records.
- Use `takyon:outreach` for outreach assets and `business_publish_outreach` for publish intent.
- Use existing conversation tools for replies, objections, support-style messages, and thread status. Do not duplicate raw messages into a separate sales store.
- If canonical sales tools exist in `business_registry`, use them for accounts, contacts, opportunities, and touches. Until then, keep compact sales records under `sales/` and record important stage changes with `business_record_event`.
- For enrichment, CRM sync, email sending, LinkedIn, Apollo, HubSpot, or other provider work, use guarded jobs/tools with explicit `requires_api` or `requires_env`.
- In test mode, create local drafts, suppressed receipts, and conversation mirrors; do not claim external sends or live CRM updates.
- Promote repeated objections, close reasons, pricing signals, and ICP changes into the business brain.

## Handoffs

- Use `takyon:market-research` when the prospect set is not yet clear.
- Use `takyon:conversation-response` when replies are too large or operational to inspect cheaply.
- Use `takyon:pricing-strategy` when sales evidence changes willingness to pay or packaging.

Sales pipeline work is evidence for the CEO, not a forced interruption policy.
