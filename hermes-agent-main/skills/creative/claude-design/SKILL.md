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

## Layout and Width

- On desktop, customer-facing landing pages should occupy most of the canvas, not sit as a tiny island in empty space.
- Prefer a broad container in the rough `1320px` to `1440px` range with generous side gutters, or a deliberate split/full-bleed composition.
- On very large desktop viewports, treat that as a floor rather than a ceiling; if the page still looks boxed in, widen toward about `1600px` to `1720px` or roughly `90vw`.
- A reliable large-screen pattern is roughly `min(92vw, 1680px)` with restrained side padding rather than a centered `1400px` frame with big gutters.
- The masthead or top navigation lane can be a touch wider than the main content lane when that helps the logo, links, and primary CTA breathe.
- A dependable pattern is a header shell around `min(94vw, 1760px)` while the main hero/body shell sits around `min(92vw, 1680px)`. Keep the difference small and intentional.
- Do not leave 40% to 60% of the hero visually empty unless that space is doing clear work with a real image, product visual, proof block, or strong atmospheric gesture.
- If the headline sits in a narrow column, pair it with an equally intentional second column or widen the composition; avoid accidental center-column layouts that feel unfinished.
- Make the first screen feel composed at laptop width, not just technically responsive.
- If you choose a side-by-side hero, the proof rail should feel like a real half of the composition, not a small decorative card parked off to the side.
- As a starting point, a split hero should usually bias toward about `55/45` rather than a timid perfectly-equal split when that helps the proof rail feel substantial.
- Let desktop display headlines get big enough to carry the page; if the whole first screen feels miniature, scale the composition up before adding more copy.
- Do not let a widened container get neutralized by capping both hero columns around the same mid-`500px` width. One side should push wider so the composition actually spans the page.
- On wide monitors, outer gutters should not dominate the composition. If they do, widen the layout or reduce side padding before touching the copy.
- For very wide screens, slightly asymmetrical hero splits such as `58/42` or `60/40` often feel better than a perfect `50/50` when the proof rail is otherwise reading too polite.

## Marketing Surfaces

- Lead with one clear hero idea, then features/proof/pricing/CTA in a deliberate rhythm.
- Use concrete product language instead of generic startup filler.
- Do not invent metrics or customer logos.
- Avoid default AI-startup tropes unless the brief explicitly wants them.
- Hero support copy should usually stay within 2 sentences.
- Do not follow the hero with another long editorial paragraph unless the brief truly needs it; prefer one sharp supporting sentence or concise proof instead.
- If a split hero uses a product mock, screenshot, or proof card, that visual should feel weighty enough to balance the headline on desktop.
- When the page starts feeling wordy, cut copy and strengthen composition before adding more sections or shrinking the hero.

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
- Do not let customer-facing landing pages turn into essay blocks; cut copy before shrinking the layout.

## Local Sources

This skill was adapted from the copied Open Design files under:

- `references/open-design/web-prototype/`
- `references/open-design/saas-landing/`
- `references/open-design/dashboard/`
- `references/open-design/critique/`
