# Takyon Business Plugin

Takyon is a terminal-first CEO operator layer on top of Hermes. It keeps one stable CEO runtime prompt, a small set of real Hermes skills, and guarded `business_*` tools for durable state changes.

## Commands

```bash
takyon shell
takyon create latexflow "Build a LaTeX workflow product for students"
takyon create --test --schedule "every 6h" latexflow "Build this business end to end in test mode."
takyon read latexflow research/index.md
takyon show latexflow
takyon files latexflow
takyon wake latexflow "every 6h"
takyon skills-index
takyon budget set latexflow 100
takyon delete latexflow
takyon delete latexflow --confirm
takyon "for latexflow, improve the pricing strategy and create the next distribution campaign"
```

In the interactive shell:

```text
/create --test --schedule "every 6h" latexflow Build this business end to end in test mode.
/wake
/status
/files
/read research/index.md
/skills-index
```

Plain text in the shell goes to the scoped CEO by default. `/ceo` is only a focus/status affordance.

## Storage

Takyon state lives under `TAKYON_HOME`. In the parent workspace launcher this is `/Users/Zygote/Downloads/takyon/.takyon`.

```text
$TAKYON_HOME/
  state.sqlite3
  businesses/
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

Takyon leaf workflows are normal Hermes skills, not plugin-only Takyon skills:

```text
skills/takyon/takyon-market-research/
skills/takyon/takyon-build-product/
skills/takyon/takyon-app-runtime/
skills/takyon/takyon-distribution/
skills/takyon/takyon-business-metrics/
skills/takyon/takyon-claude-agent-sdk/
```

Each skill is discovered the Hermes way: `SKILL.md` frontmatter supplies name/description/platform metadata, Hermes builds a compact skills index, and the model loads a skill with `skill_view(...)` when it matches.

Takyon skill frontmatter is strict YAML. Invalid or malformed frontmatter should fail `./takyon skills-index`; it should not degrade into ad hoc parsing.

The canonical local rebuild step is:

```bash
./takyon skills-index
```

That command:

1. syncs bundled Takyon skills into the active Hermes skills directory
2. rebuilds the cached Hermes skills snapshot
3. primes the compact skills index used in future Takyon sessions

## Canonical Skill Shape

Takyon skills follow Hermes-style frontmatter plus a small Takyon block for output roots:

```md
---
name: takyon-market-research
description: Gather customer, competitor, channel, pricing, and demand evidence for one business.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]

metadata:
  hermes:
    category: research
    tags: [takyon, market-research]
    related_skills: [takyon-build-product]
    requires_toolsets: []
    requires_tools: []
  takyon:
    scope: business
    allowed_roots: [research, metrics]
    output_root: research
    publication:
      - research/market.md
      - research/sources.jsonl
      - metrics/research-summary.json

required_environment_variables: []
required_credential_files: []
---
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

Hermes-side tool availability gating belongs in `metadata.hermes.requires_toolsets` and `metadata.hermes.requires_tools`. The exact durable Takyon tools a skill expects to use should be named in the skill body itself. If a skill needs a durable action and no canonical `business_*` tool exists for it yet, the skill does not plug in by itself; add or modify the owning tool in code and then reference it from the skill.

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

## Test Mode

`businesses.mode` in `state.sqlite3` is the source of truth.

In test mode:

- local product work and local publication are allowed
- external sends, posts, ad spend, and money movement stay suppressed
- suppressed side effects still produce receipts under `metrics/receipts/`

## Wakes and Metrics

Takyon still computes a business metrics snapshot with `business_calculate_pulse`, but the durable files now live under `metrics/`:

- `metrics/summary.md`
- `metrics/wake-history.md`
- `metrics/conversations/...`
- `metrics/receipts/...`

`pulse` is legacy tool naming; the filesystem contract is metrics-first.
