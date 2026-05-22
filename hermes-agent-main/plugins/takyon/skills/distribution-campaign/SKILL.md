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
- Use `business_enqueue_job` for posting, ad launches, vendor calls, build/deploy work, or any deterministic side effect.
- Include `requires_api` or `requires_env` for external posting or paid actions.
- Record results and promote reusable lessons to the business brain.

Do not default to the cheapest move. Choose the highest expected-impact move under budget, evidence, risk, and current business strategy.
