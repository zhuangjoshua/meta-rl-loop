# Takyon Business Plugin

Takyon is a terminal-first CEO operator built on the Claude Agent SDK, one stable policy compiler, one approved native skill plugin, and guarded `business_*` tools for durable state changes.

## Commands

```bash
takyon shell
takyon create latexflow "Build a LaTeX workflow product for students"
takyon create --live --schedule "every 6h" latexflow "Build this business end to end."
takyon read latexflow research/index.md
takyon show latexflow
takyon files latexflow
takyon wake latexflow "every 6h"
takyon delete latexflow
takyon delete latexflow --confirm
takyon "for latexflow, improve the pricing strategy and create the next distribution campaign"
```

In the interactive shell:

```text
/create --live --schedule "every 6h" latexflow Build this business end to end.
/wake
/status
/files
/read research/index.md
```

Plain text in the shell goes to the scoped CEO by default. `/ceo` is only a focus/status affordance.

## Storage

Takyon control/business authority lives in Supabase Postgres. The canonical durable per-business file
store is the configured object-storage backend; on the tracked operator runtime this is Supabase
Storage over the S3-compatible API. The local host copy is cache/scratch, not the durable source of
truth.

```text
$TAKYON_HOME/
  cache/
    businesses/
      <business>/
        product/
        distribution/
        research/
        metrics/
  businesses/        # only for local-backend/dev or explicit scratch homes
    <business>/
      product/
      distribution/
      research/
      metrics/
```

These are the only canonical top-level business output roots.

Legacy paths such as `brain/`, `app/`, `sales/`, `conversations/`, and `receipts/` are read through compatibility aliases, but new work should land only in the four canonical roots above.

## CEO Prompt

The CEO is no longer a skill. The stable runtime prompt lives at:

`plugins/takyon/prompts/ceo.md`

`/create`, plain operator turns, and `/wake` add small invocation overlays around that stable prompt instead of swapping in different CEO skill variants.

Takyon runs the CEO with:

- `load_soul_identity=False`
- `skip_memory=True`
- context-file loading disabled for the CEO turn
- memory/skill self-improvement nudges disabled

So Takyon does not rely on `SOUL.md`, curator routing, or self-improvement prompt churn for CEO behavior.

## Skills

Takyon leaf workflows are approved native Agent SDK skills:

```text
skills/takyon/takyon-market-research/
skills/takyon/takyon-product/
skills/takyon/takyon-app-runtime/
skills/takyon/takyon-distribution/
skills/takyon/takyon-x/
skills/takyon/takyon-business-metrics/
```

Each release skill is published into the single read-only Agent SDK plugin declared by `skills/release-skills.yaml`; its native `SKILL.md` description supplies autonomous "when to use" routing, while tools, paths, authority, and publication bindings live separately under `skills/HANDOFF/`.

Takyon skill frontmatter is strict YAML. Invalid or malformed frontmatter fails release-manifest compilation and runtime discovery; it never degrades into ad hoc parsing.

Skills are compiled and published at release time, not installed or synchronized per turn, business, user profile, or runtime startup.

## Canonical Skill Shape

Takyon skills keep portable routing and method guidance in `SKILL.md`; runtime bindings belong in HANDOFF:

```md
---
name: takyon-market-research
description: Gather customer, competitor, channel, pricing, and demand evidence for one business.
---

# Market Research

## When to use

Use for current customer, competitor, pricing, channel, or demand evidence; do not use for product implementation or publication.
```

Useful optional subdirectories:

- `references/`
- `templates/`
- `scripts/`
- `assets/`

Not every skill needs all of them.

For new Takyon skills, start from:

- `skills/takyon/SKILL-TEMPLATE.md`
- `skills/takyon/BUILDING-SKILLS-AND-TOOLS.md`

Each Takyon skill lives in its own folder:

```text
skills/takyon/<skill-name>/
  SKILL.md
  references/
  templates/
  scripts/
  assets/
```

Only `SKILL.md` is required. Add the optional subdirectories only when the skill really needs them.

Tool availability, scoped paths, publication targets, authority, and receipts belong in `skills/HANDOFF/`; portable skill bodies describe domain method and verification without embedding deployment bindings. If a skill needs a durable action and no canonical `business_*` tool exists, add or modify the owning backend tool and bind its capability in HANDOFF.

## Tools

Takyon business tools are the durable state and side-effect layer. They handle:

- business creation and deletion
- filesystem writes
- budgets and controls
- app-runtime rails
- conversation state
- outreach publication
- wake scheduling
- receipts and events

The CEO and skills should use `business_*` tools for durable changes rather than inventing parallel state.

## Product Runtime

The shared runtime owns backend rails only: auth/session protocol, entitlements, checkout, Stripe reconciliation, usage accounting, and safety gates.

The business-specific surface contract lives in:

- `product/surface.md`

Related product mirrors and app-runtime outputs live in `product/`.

The runtime should not hardcode final product design, layout, or copy.

## Business Mode

`businesses.mode` in the Postgres control plane is the source of truth.

Takyon now runs businesses in live mode only.

- missing credentials, provider access, budget authority, or spend gates are blockers
- provider-backed sends, posts, checkout, and ad actions hard-fail instead of falling back to suppressed test receipts

## Wakes and Metrics

Takyon still computes a business metrics snapshot with `business_calculate_pulse`, but the durable files now live under `metrics/`:

- `metrics/summary.md`
- `metrics/wake-history.md`
- `metrics/conversations/...`
- `metrics/receipts/...`

`pulse` is legacy tool naming; the filesystem contract is metrics-first.
