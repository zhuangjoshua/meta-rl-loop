---
name: claude-design
description: Distilled Open Design method for high-quality product/site frontend work. Pair with exactly one shared style skill such as claude-design-openai or claude-design-doodle.
version: 1.0.0
author: Four Manifold
license: Apache-2.0
platforms: [linux, macos]
tags: [design, html, ui, ux, frontend, product, landing, dashboard]
---

# Claude Design

Use this skill for outward-facing `product/site` work when visual quality matters. This skill provides the method. Pair it with exactly one shared style skill:

- `claude-design-openai`
- `claude-design-stripe`
- `claude-design-superhuman`
- `claude-design-vibrant`
- `claude-design-doodle`

Do not mix multiple style skills in the same worker run.

## When To Use

- landing pages
- product marketing pages
- dashboards
- app shells
- UI refreshes
- customer-facing HTML/CSS/JS surfaces

## Shared Style Selection

Choose one style skill before building:

- `claude-design-openai`: calm serious default for AI tools, prosumer software, research/productivity surfaces
- `claude-design-stripe`: premium commercial, fintech, infra, or polished B2B marketing
- `claude-design-superhuman`: premium productivity, speed, focus, executive-feeling software
- `claude-design-vibrant`: fun consumer, colorful prosumer, energetic but still clean
- `claude-design-doodle`: whimsical playful consumer, pets, kids, casual social, deliberately lighthearted products

If no style is strongly implied, default to `claude-design-openai`.

## Workflow

1. Read the brief and identify the audience, core job, and emotional tone before choosing the look.
2. Read the paired style skill first and stay inside its typography, spacing, color, and component posture.
3. Build from a coherent page rhythm, not isolated pretty sections.
4. Keep the interface honest: real controls, real labels, real states, no poster-only hero fakery.
5. Use one visual thesis and carry it through typography, spacing, chrome, and motion.

## Marketing Surfaces

- Lead with one clear hero idea, then features/proof/pricing/CTA in a deliberate rhythm.
- Use concrete product language instead of generic startup filler.
- Do not invent metrics or customer logos.
- Avoid default AI-startup tropes unless the brief explicitly wants them.

## Product Surfaces

- Build the real interface, not just the shell.
- Include expected controls, empty states, loading states, and useful information density.
- Dashboards should be scannable and operational, not decorative card collections.
- Product claims must match what the surface can actually do now.

## Self Review Loop

Before finishing, check:

- Is there one obvious visual thesis?
- Did the chosen style stay coherent?
- Did we avoid generic purple-gradient SaaS slop?
- Does the page have one memorable quality?
- Are states, spacing, and hierarchy resolved enough to feel intentionally designed?

## Hard Rules

- Pair this skill with exactly one style skill.
- Do not mix visual systems.
- Do not flood the page with accent color.
- Do not use filler copy, fake numbers, or fake backend behavior.
- Do not expose stale model/vendor names in customer-facing copy.
- Prefer a strong restrained system over five disconnected flourishes.

## Local Sources

This skill was adapted from the copied Open Design files under:

- `references/open-design/web-prototype/`
- `references/open-design/saas-landing/`
- `references/open-design/dashboard/`
- `references/open-design/critique/`
