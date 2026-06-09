---
name: takyon-claude-agent-sdk
description: Use a bounded Claude Agent SDK worker for focused business-scoped file work inside canonical Takyon roots.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, claude-agent-sdk, worker, bounded-edits]
    related_skills: [takyon-build-product, takyon-distribution]
    requires_toolsets: [takyon]
    requires_tools: [business_read_business, business_claude_agent_task]
    routing:
      owns: bounded business-scoped worker edits inside canonical roots when a separate implementation lane is helpful
      when_to_use:
        - a task needs focused multi-file edits or synthesis inside `product/`, `distribution/`, `research/`, or `metrics/`
        - non-trivial `product/site/` work under `takyon-build-product` needs a worker lane
      do_not_use_for:
        - cases where normal business tools are already enough
  takyon:
    scope: business
    allowed_roots: [product, distribution, research, metrics]
    output_root: product
    publication:
      - product/
      - distribution/
      - research/
      - metrics/
required_environment_variables: []
required_credential_files: []
---

# Takyon Claude Agent SDK

## Overview

Use this skill when a focused file-editing worker is actually helpful for one business workspace and the work fits inside the canonical business roots.

## When to Use

- Use for bounded workspace edits, synthesis, or source changes inside one business.
- Use when the task benefits from a separate scoped worker with path containment.
- Under `takyon-build-product`, use this as the default implementation lane for non-trivial `product/site/` work while leaving product ownership with `takyon-build-product`.
- Do not use when normal business tools are enough.

## Quick Reference

- Primary root: `product/` by default, but bounded work may land in any canonical Takyon root
- Allowed roots: `product/`, `distribution/`, `research/`, `metrics/`
- Best call points: narrow file work, bounded synthesis, contained source edits
- Optional guidance lane: pass `guidance_skills: ["claude-design", "claude-design-openai"]` by default for design-heavy `product/site/` work, or swap the second skill for `claude-design-stripe`, `claude-design-superhuman`, `claude-design-vibrant`, or `claude-design-doodle`
- Publication location: the exact target path named in the delegated task, inside a canonical Takyon root
- Tool names used by this skill: `business_read_business`, `business_claude_agent_task`, `business_refresh_product_surface`
- Customer-facing product rule: for `product/site/` work, keep copy capability-first; do not leak stale vendor/model labels into the generated UI

## Prerequisites

- The Takyon toolset must be available.
- Start with `business_read_business` so the worker gets the right current business context.
- The delegated workspace must stay inside one of the canonical roots.
- For `product/*` workspaces, plan to use `business_refresh_product_surface` after the worker run when the changed source should be published or refreshed into `product/surface.md`.
- For substantial `product/site/` work, the worker is expected to finish the source/build loop itself. Do not default to CEO source inspection, local hand-patching, or a second worker pass in the same turn unless the worker explicitly returns `BLOCKED:`.

## How to Run

- Call `business_read_business` first and decide whether the task is narrow enough to delegate.
- Call `business_claude_agent_task` with one bounded workspace, a clear instruction, and an explicit desired output path.
- For `product/site/`, assume the worker will receive a prepared shared subuser app kit under `_takyon/` plus a generated `surface-context.js`; extend the business-specific UI around that substrate instead of re-explaining the app plane in the task body. If the surface is app-like, keep a real `/app` route in the generated source and make sure the surface contract explicitly lists `/app`; only collapse to landing-only when the owning surface is intentionally marked `landing_page_only`.
- The seeded AppKit source is behavior scaffolding, not a house design system. Preserve the route intentions, auth/paywall/account boundaries, and helper calls by default, but replace the seeded page layout and presentation freely when the business calls for it.
- For a first monthly bootstrap, `/app` may stop at sign-in, subscribe, and account management. Do not invent product tabs, generators, or extra in-app workflow unless the contract explicitly asks for them.
- Do not ship customer-facing copy that frames the surface as a stub, demo, placeholder, scaffold, or developer preview.
- Keep paid product routes inside the gated `src/app/app/(product)/` route group unless the task explicitly calls for a route to stay outside the entitlement wall.
- When the task is design-heavy product/UI source work, include `guidance_skills: ["claude-design", "<style-skill>"]` so the worker receives both the shared frontend method and one shared design system.
- For `product/*` workspaces, follow the worker run with `business_refresh_product_surface` when the changed source should be published or refreshed into `product/surface.md`.
- If the result implies a send, deploy, runtime mutation, or other external effect, switch back to the appropriate Takyon skill or `business_*` tool path after the worker completes.
- If `business_claude_agent_task` explicitly returns `BLOCKED:`, treat that as the end of the delegated source pass. Return control to the owning skill to record the blocker or choose a later follow-up, not to default to same-turn CEO source repair.

## Procedure

1. Call `business_read_business` and decide whether this task really needs a worker. If normal Takyon tools are enough, do not delegate.
2. Choose one narrow workspace inside `product/`, `distribution/`, `research/`, or `metrics/`. Name the exact target files or directory in the instruction.
3. Call `business_claude_agent_task` with a bounded instruction that says what to change, what to leave alone, and what proof or output is expected. For design-heavy `product/site/` source work, pass `guidance_skills: ["claude-design", "<style-skill>"]`. Do not restate low-level app-plane auth/billing/API semantics when the prepared `_takyon/` kit and surface contract already cover them. If the surface is app-like, make sure the contract explicitly requires `/, /app` and that the source really ships `/app`. Preserve the seeded auth/paywall/account helpers and route boundaries, but treat the seeded page layouts as disposable bootstrap scaffolding rather than the target design.
4. Review the changed files. Keep only durable changes that stay inside the requested business scope and canonical roots.
5. If the worker changed `product/site/` or another product source path that should be published, call `business_refresh_product_surface`.
6. If the worker output implies a real publish, send, checkout, auth, or billing effect, route that next step back through the appropriate Takyon skill and `business_*` tool instead of pretending the worker already did it.
7. For customer-facing product copy, default to capability language. Only mention model families when the operator explicitly wants that positioning, and never leak stale names like `GPT-4o-mini` into a Claude-backed product surface.
8. If the worker lane is blocked by missing provider/runtime access or exits without usable source, hand control back to the owning skill to record the blocker or choose a later follow-up. Do not default to same-turn CEO source repair for `product/site/`.

### Shared style skills

Choose exactly one style skill when pairing with `claude-design`:

- `claude-design-openai`: calm serious default for AI tools, prosumer software, research/productivity
- `claude-design-stripe`: premium commercial, infra, fintech, polished B2B
- `claude-design-superhuman`: premium productivity and speed-focused software
- `claude-design-vibrant`: fun colorful consumer or lively prosumer
- `claude-design-doodle`: whimsical playful consumer, pet, kid, or intentionally silly products

Default to `claude-design-openai` when no stronger style signal exists.

## Output Format

- The output should be the exact file or directory change requested in the delegated workspace.
- Worker results should stay narrow and reviewable rather than spraying edits across multiple roots.

## Publication

- Publish changes back into the exact target file or directory named in the delegated task.
- The target must stay inside `product/`, `distribution/`, `research/`, or `metrics/`.
- Any real publish, send, deploy, or runtime side effect must still route through the appropriate Takyon skill and tool path.

## Common Pitfalls

- Delegating vague, multi-root work that should have stayed in the main CEO loop
- Letting the worker substitute for canonical business tools
- Accepting source changes that smuggle in fake backend behavior
- Treating the worker like a mini-CEO that should run authority tools, publication, or same-turn repair choreography

## Verification Checklist

- [ ] The work stayed inside the requested business and canonical roots
- [ ] The result is narrower and clearer than doing the same thing inline
- [ ] Any product-source change that needed publication was checked with `business_refresh_product_surface`
- [ ] No claimed runtime or distribution side effect exists without tool-backed truth

## Rules

1. The worker may not invent backend behavior.
2. The worker may not escape the business workspace.
3. Product surface refresh blockers are blockers, not success.
4. Customer-facing product/UI copy should not expose stale or accidental foundation-model labels.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| The delegated task is too broad | Narrow the workspace and re-run with one explicit target |
| The worker result implies a real side effect | Route the next step through the appropriate Takyon skill and `business_*` tool |
