---
name: takyon-example-skill
description: Replace this with a concise routing description that tells Hermes when this skill is worth loading.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]

metadata:
  hermes:
    category: takyon
    tags: [takyon, example]
    related_skills: []
    requires_toolsets: []
    requires_tools: []
  takyon:
    scope: business
    allowed_roots: [product]
    output_root: product
    publication:
      - product/example.md

required_environment_variables: []
required_credential_files: []
---

# Takyon Example Skill

## Overview

Use this skill for one bounded Takyon business method. Keep the intro short and make the routing value obvious.

## When to Use

- State concrete trigger conditions.
- Prefer real business situations over abstract advice.
- Add a "Do not use for" bullet when that would prevent confusion.

## Quick Reference

- Primary root: `product/`
- Publication paths: `product/example.md`
- Tool names used by this skill: `business_example_write`

## Prerequisites

- Name required providers, credentials, local setup, or tool availability.
- If this skill depends on a Hermes tool or Takyon tool existing, gate that in frontmatter with `metadata.hermes.requires_toolsets` and `metadata.hermes.requires_tools`.

## References

- Optional: `references/example.md`

## Templates

- Optional: `templates/example.md`

## Scripts

- Optional: `scripts/example.sh`

## How to Run

- Put the common path first.
- Mention the exact `business_*` tools or helper scripts used by this skill.
- If the skill uses bundled helpers, reference them with `${HERMES_SKILL_DIR}/scripts/...`.
- If this skill claims a durable change, require one last read-back of the exact file, artifact, or receipt before saying `done`, `wired`, `published`, or `completed`.

## Procedure

1. Read the current business state that matters.
2. Do the smallest honest amount of work that resolves the problem.
3. Publish the durable output to the exact destination path.
4. Use business tools for durable state changes or external side effects.
5. Before claiming success, re-read the exact durable file, artifact, or receipt you just changed and report from that read-back; if it does not match, say `attempted`, `blocked`, or `not verified` instead of `done`.

## Output Format

- Say what each publication path should look like.
- Separate prose artifacts from structured machine-readable ones.

## Publication

- This section is required in every Takyon skill.
- In Takyon, `publication` means the durable destination path for this skill's work.
- That includes writing a new file, updating an existing file, or maintaining a canonical directory inside `product/`, `distribution/`, `research/`, or `metrics/`.
- Name the exact canonical file or directory where this skill publishes its durable outputs, even when that publication is only local filesystem state.
- Keep every publication path inside `product/`, `distribution/`, `research/`, or `metrics/`.
- If the skill can also claim live external state, name the truth source for that claim as well.

## Common Pitfalls

- List the failure modes most likely to create fake state, sprawl, or confusion.

## Verification Checklist

- [ ] Outputs are truthful, current, and in the right place
- [ ] Any claimed side effect is backed by tool truth or receipts
- [ ] Any claimed completion is backed by a read-back of the exact changed file, artifact, or receipt
- [ ] No parallel state was created outside the canonical roots

## Rules

1. Keep work business-scoped.
2. Do not fake side effects, provider state, deploy state, auth, billing, or metrics.
3. Use canonical tools and files instead of parallel state.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Example blocker | Record the blocker and keep the publication paths honest |
