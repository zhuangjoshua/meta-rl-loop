---
name: takyon-build-product
description: Create or improve the smallest credible product, site, or offer surface for one Takyon business and write it into canonical product files.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, product, website, app, offer]
    related_skills: [takyon-market-research, takyon-app-runtime, takyon-distribution]
  takyon:
    scope: business
    allowed_roots: [product, metrics]
    output_root: product
    publication:
      - product/design-brief.md
      - product/surface.md
      - product/site
required_environment_variables: []
required_credential_files: []
---

# Takyon Build Product

Use this skill to create or materially improve the business-owned product surface: the offer, the website, the app route, the source path, and the honest public claims around what works now.

## Quick Reference

- Primary root: `product/`
- Publication paths: `product/design-brief.md`, `product/surface.md`, `product/site/`
- Best call points: post-research product creation, surface repair, honest publication work
- Publication lane: publish from `product/site/`; reflect verified state in `product/surface.md`

## References

- `references/surface-rules.md`

## Templates

- `templates/design-brief.md`
- `templates/surface.md`

## When to Use

- Use after research when the business needs a real product or site surface.
- Use when the current source is too weak, misleading, unpublished, or mismatched to the offer.
- Use when the operator asks to build, publish, or repair the business product surface.

## Procedure

1. Read current business evidence before building.
2. Define the smallest credible product or offer that can create real evidence.
3. Update the design brief and surface contract.
4. Create or repair the actual source files.
5. Use the canonical product publication path; do not invent a fake deployed state.

## Output Format

- `product/design-brief.md` should describe audience, offer, routes, and constraints.
- `product/surface.md` should describe what is actually wired and what is blocked.
- `product/site/` should contain real source, not placeholder notes pretending to be source.

## Publication

- The canonical local source path is `product/site/`.
- Publish the design brief to `product/design-brief.md`.
- Publish the surface contract and current state to `product/surface.md`.
- Public or local publication state must be reflected honestly in `product/surface.md`.
- Do not claim a deployed URL, working route, or finished publish unless the runtime or tool receipts actually support it.

## Pitfalls

- Writing polished copy while leaving the real source or route missing
- Claiming runtime-backed product behavior that only exists in prose
- Splitting the actual product surface across random directories

## Verification

- `product/design-brief.md` and `product/surface.md` agree on the offer and current state
- `product/site/` contains usable source, not only notes
- Any claimed publication state is backed by visible runtime state or receipts

## Rules

1. Do not fake auth, billing, sessions, checkout, usage, or provider-backed features.
2. Use real source files when the operator asked for a built surface.
3. Keep the surface business-owned and honest.
4. Use app-runtime rails when the product needs shared backend behavior.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Publish is blocked | Record the exact blocker and keep the local product surface honest |
| The product needs backend behavior | Route through `takyon-app-runtime` before inventing custom state |
