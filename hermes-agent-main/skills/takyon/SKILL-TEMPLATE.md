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
    routing:
      owns: Replace this with the short ownership sentence that should appear in dynamic CEO/skills routing summaries.
      when_to_use:
        - Replace this with one concrete trigger condition.
      do_not_use_for:
        - Replace this with one nearby task that belongs to another skill.
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
- Keep this section aligned with `metadata.hermes.routing` so the dynamic ownership summary stays truthful.

## Quick Reference

- Primary root: `product/`
- Publication paths: `product/example.md`
- Tool names used by this skill: `business_example_write`

## Prerequisites

- Name required providers, credentials, local setup, or tool availability.
- If this skill depends on a Hermes tool or Takyon tool existing, gate that in frontmatter with `metadata.hermes.requires_toolsets` and `metadata.hermes.requires_tools`.
- If the skill has a real external-effect path, name the live-mode gates and any truthful blocked/test outcome.

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
- Say which canonical business state or files must be read first, usually via `business_read_business`, `business_read_file`, or `business_list_files`.
- If this skill changes real state, say which `business_*` tool makes that change real.
- If this skill claims a durable change, require one last read-back of the exact file, artifact, or receipt before saying `done`, `wired`, `published`, or `completed`.
- If this skill edits evidence that a cached projection summarizes, use the canonical `business_*` write/commit tools and let the core rails rewrite the coarse surface files automatically; only call out extra handling when this skill introduces a new projection/evidence pair or bypasses canonical write tools.
- Mention test/live only if it changes this skill's external-effect behavior.

## Procedure

1. Read the current business state that matters.
2. Do the smallest honest amount of work that resolves the problem.
3. Publish the durable output to the exact destination path.
4. If this skill changes canonical business or provider state, call an existing `business_*` tool or add a new one if none exists.
5. If test mode changes the real side effect, leave a truthful local artifact, blocker, queued job, or receipt instead of a fake success claim.
6. Before claiming success, re-read the exact durable file, artifact, or receipt you just changed and report from that read-back; if it does not match, say `attempted`, `blocked`, or `not verified` instead of `done`.
7. Do not add skill-local freshness rules or verification choreography. If the work changes evidence behind a coarse surface file, rely on canonical write tools and receipts so the core rails rewrite that surface automatically; only add new projection logic when the feature introduces a brand-new projection/evidence pair.

## Output Format

- Say what each publication path should look like.
- Separate prose artifacts from structured machine-readable ones.

## Publication

- This section is required in every Takyon skill.
- In Takyon, `publication` means the durable destination path for this skill's work.
- Name the exact canonical file or directory where this skill publishes its durable outputs.
- Keep every publication path inside `product/`, `distribution/`, `research/`, or `metrics/`.
- If the skill can also claim live external state, name the tool result or receipt that proves it.

## Common Pitfalls

- List the failure modes most likely to create fake state, sprawl, or confusion.
- Call out any easy-to-miss truth gap, such as "files were written but no tool committed the state change."

## Verification Checklist

- [ ] Outputs are truthful, current, and in the right place
- [ ] Any claimed side effect is backed by tool truth or receipts
- [ ] Any real state change is backed by an existing or newly added `business_*` tool
- [ ] Any claimed completion is backed by a read-back of the exact changed file, artifact, or receipt
- [ ] No parallel state was created outside the canonical roots

## Rules

1. Keep work business-scoped.
2. Do not fake side effects, provider state, deploy state, auth, billing, or metrics.
3. Use canonical tools and files instead of parallel state.
4. If a needed state change has no `business_*` tool yet, add the tool and tests in the same change.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Example blocker | Record the blocker and keep the publication paths honest |
