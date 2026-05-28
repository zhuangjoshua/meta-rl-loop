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

Use this skill when a focused file-editing worker is actually helpful for one business workspace and the work fits inside the canonical business roots.

## Quick Reference

- Primary root: `product/` by default, but bounded work may land in any canonical Takyon root
- Allowed roots: `product/`, `distribution/`, `research/`, `metrics/`
- Best call points: narrow file work, bounded synthesis, contained source edits
- Publication location: the exact target path named in the delegated task, inside a canonical Takyon root

## When to Use

- Use for bounded workspace edits, synthesis, or source changes inside one business.
- Use when the task benefits from a separate scoped worker with path containment.
- Do not use when normal business tools are enough.

## Procedure

1. Read the business first.
2. Choose one narrow workspace.
3. Delegate bounded work with a clear instruction.
4. Review the result and keep only truthful, durable changes.

## Publication

- Publish changes back into the exact target file or directory named in the delegated task.
- The target must stay inside `product/`, `distribution/`, `research/`, or `metrics/`.
- Any real publish, send, deploy, or runtime side effect must still route through the appropriate Takyon skill and tool path.

## Pitfalls

- Delegating vague, multi-root work that should have stayed in the main CEO loop
- Letting the worker substitute for canonical business tools
- Accepting source changes that smuggle in fake backend behavior

## Verification

- The work stayed inside the requested business and canonical roots
- The result is narrower and clearer than doing the same thing inline
- No claimed runtime or distribution side effect exists without tool-backed truth

## Rules

1. The worker may not invent backend behavior.
2. The worker may not escape the business workspace.
3. Product verification failures are blockers, not success.
