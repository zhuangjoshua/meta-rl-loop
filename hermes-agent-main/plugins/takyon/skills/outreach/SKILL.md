---
name: takyon-outreach
description: Build lead hypotheses, outbound lists, partner pitches, and sales follow-up sequences.
---

# Takyon Outreach

Use this skill for leads, outbound, partner pitches, sales copy, customer discovery, and follow-up.

## Practice

- Keep lead hypotheses and outreach assets inside a business or campaign workspace.
- Use web research when available; label unverifiable guesses as hypotheses.
- Draft concise messages with clear offer, reason for contact, and next step.
- Publish outreach with `business_publish_outreach`. Include the exact `destination_url` or composer endpoint when the intended surface is known, such as a submit page, subreddit submit URL, social composer, email list, or partner inbox. Use the business app surface `public_url` or `publish_target` for product links in the message body; do not invent a custom product domain. The tool owns the mode bright line: test mode creates local suppressed artifacts and conversation mirrors; live mode requires provider gates or records a gated publish job.
- If the intended channel, provider, or public product URL is unavailable, do not stop at a draft. Make the closest truthful outreach attempt through `business_publish_outreach`: in test mode this is a local suppressed/mock publish, and in live mode this is a gated provider job or exact blocker.
- For Phase 1 outreach, work in a batch rather than a token single post: normally cover at least 3 evidence-backed lanes and 6 total publish intents. Keep the batch assets under the campaign workspace and use `business_publish_outreach` for every touch.
- Record enrichment requests only when APIs and approvals are explicit.
- Test-mode local publication must create a file under `outreach/local-published/`, a receipt under `receipts/outreach/`, and a conversation mirror. A draft file or queued future send/post job is not local publication.
- Record replies, objections, failures, and useful language in the business brain.

Do not claim contact, enrichment, or external sending happened unless a concrete Takyon receipt proves it. Test-mode local publish receipts prove only local publication with external side effects suppressed.
