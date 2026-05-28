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

Use this skill for one bounded Takyon business method. Keep the intro short and make the routing value obvious.

## Quick Reference

- Primary root: `product/`
- Publication paths: `product/example.md`
- Best call points: name the concrete moments when this skill should be loaded
- Publication location: name the exact canonical file or directory where this skill publishes its durable outputs

## References

- Optional: `references/example.md`

## Templates

- Optional: `templates/example.md`

## Scripts

- Optional: `scripts/example.sh`

## When to Use

- State the trigger conditions.
- Prefer concrete business situations over abstract advice.
- Keep this section load-bearing; Hermes first sees the frontmatter, but this section should still sharpen the method once loaded.

## Procedure

1. Read the current business state that matters.
2. Do the smallest honest amount of work that resolves the problem.
3. Publish the durable output to the exact destination path.
4. Use business tools for durable state changes or external side effects.

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

## Pitfalls

- List the failure modes most likely to create fake state, sprawl, or confusion.

## Verification

- Say how to verify the outputs are truthful, current, and in the right place.
- Prefer checks that an operator or agent can do quickly.

## Rules

1. Keep work business-scoped.
2. Do not fake side effects, provider state, deploy state, auth, billing, or metrics.
3. Use canonical tools and files instead of parallel state.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Example blocker | Record the blocker and keep the publication paths honest |
