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
- Optional guidance lane: pass `guidance_skills: ["claude-design"]` for design-heavy `product/site/` work
- Publication location: the exact target path named in the delegated task, inside a canonical Takyon root
- Tool names used by this skill: `business_read_business`, `business_claude_agent_task`, `business_verify_product_surface`

## Prerequisites

- The Takyon toolset must be available.
- Start with `business_read_business` so the worker gets the right current business context.
- The delegated workspace must stay inside one of the canonical roots.
- If the worker touches product source that needs publish verification, plan to use `business_verify_product_surface` after the worker run.

## How to Run

- Call `business_read_business` first and decide whether the task is narrow enough to delegate.
- Call `business_claude_agent_task` with one bounded workspace, a clear instruction, and an explicit desired output path.
- When the task is design-heavy product/UI source work, include `guidance_skills: ["claude-design"]` so the worker receives the distilled design guidance.
- If the worker edits product source that should be verified or published, follow with `business_verify_product_surface`.
- If the result implies a send, deploy, runtime mutation, or other external effect, switch back to the appropriate Takyon skill or `business_*` tool path after the worker completes.

## Procedure

1. Call `business_read_business` and decide whether this task really needs a worker. If normal Takyon tools are enough, do not delegate.
2. Choose one narrow workspace inside `product/`, `distribution/`, `research/`, or `metrics/`. Name the exact target files or directory in the instruction.
3. Call `business_claude_agent_task` with a bounded instruction that says what to change, what to leave alone, and what proof or output is expected. For design-heavy `product/site/` source work, pass `guidance_skills: ["claude-design"]`.
4. Review the changed files. Keep only durable changes that stay inside the requested business scope and canonical roots.
5. If the worker changed `product/site/` or another product source path that should be validated, call `business_verify_product_surface` before claiming product success.
6. If the worker output implies a real publish, send, checkout, auth, or billing effect, route that next step back through the appropriate Takyon skill and `business_*` tool instead of pretending the worker already did it.

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

## Verification Checklist

- [ ] The work stayed inside the requested business and canonical roots
- [ ] The result is narrower and clearer than doing the same thing inline
- [ ] Any product-source change that needed verification was checked with `business_verify_product_surface`
- [ ] No claimed runtime or distribution side effect exists without tool-backed truth

## Rules

1. The worker may not invent backend behavior.
2. The worker may not escape the business workspace.
3. Product verification failures are blockers, not success.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| The delegated task is too broad | Narrow the workspace and re-run with one explicit target |
| The worker result implies a real side effect | Route the next step through the appropriate Takyon skill and `business_*` tool |
