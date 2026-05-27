---
name: takyon-distribution-campaign
description: Create and operate isolated traffic, launch, ad, content, social, or channel campaign workspaces.
---

# Takyon Distribution Campaign

Use this skill when the business needs traffic, launch pressure, ad tests, content, social, partnerships, affiliates, or other distribution work.

Campaign workspaces are arbitrary. Create the structure that fits the campaign:

```text
campaigns/<name>/
  brief.md
  budget.md
  hypotheses.md
  research/
  creatives/
  copy/
  posts/
  outreach/
  receipts/
  learnings.md
  status.md
```

This is a useful default, not a schema.

## Practice

- Keep campaigns isolated by business and workspace.
- Create only the files that help the actual campaign.
- Use `business_allocate_budget` before spend, under the business cap.
- Use `business_enqueue_job` for guarded requests around posting, ad launches, vendor calls, build/deploy work, or other external side effects.
- Include `requires_api` or `requires_env` for external posting or paid actions.
- When the campaign's purpose is initial launch, find-users work, or bootstrap distribution, run or continue `campaigns/phase-1-outreach/` as a batch: normally at least 3 evidence-backed lanes and 6 total publish intents, each through `business_publish_outreach`. In test mode this is local/suppressed; in live mode it is gated or an exact blocker if even mock outreach is unsafe.
- Test mode changes side effects, not distribution judgment. Build the chosen distribution asset; if the chosen tactic is outreach or posting, use `business_publish_outreach` for local suppressed publication rather than skipping because provider keys are missing.
- Test-mode local outreach is proven only by `outreach/local-published/`, `receipts/outreach/`, and conversation mirror artifacts. Drafts and queued future live jobs are not publication.
- Record results and promote reusable lessons to the business brain.

Do not default to the cheapest move. Choose the highest expected-impact move under budget, evidence, risk, and current business strategy. Do not fake posts, sends, spend, campaign metrics, or provider results.
