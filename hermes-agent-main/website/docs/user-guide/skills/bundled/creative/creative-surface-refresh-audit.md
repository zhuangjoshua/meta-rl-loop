---
title: "Surface Refresh Audit"
sidebar_label: "Surface Refresh Audit"
description: "Audit and improve an existing product surface while preserving its established design direction and functionality"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Surface Refresh Audit

Audit and improve an existing product surface while preserving its established design direction and functionality. Use when a shipped interface needs a focused refresh, redesign, or anti-slop pass. Do not use for a first build or for backend behavior changes.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/creative/surface-refresh-audit` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Takyon loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Claude Refresh Audit

Use this when the task is improving an EXISTING surface, not building a new one. It provides the audit method; the visual direction comes only from the business's Taste-authored `DESIGN.md`, tokens, and assets.

## When To Use

- UI refresh or redesign passes on an existing `product/site`
- "make it look better / more premium" requests against shipped pages
- de-slopping a surface that reads as generic AI output

Do not use for first builds; the full Taste landing pass owns those.

## Audit Method

1. Scan: read the existing source; identify the styling method and current design patterns before touching anything.
2. Diagnose: run the audit checklists below; list every generic pattern, weak point, and missing state found.
3. Fix: apply targeted upgrades inside the existing stack. Do not rewrite from scratch; improve what is there, in small reviewable changes.

## Design Audit

Typography:
- default fonts or Inter-everywhere; headlines without presence (increase display size, tighten tracking, reduce leading)
- body text wider than ~65ch; only 400/700 weights (introduce 500/600 hierarchy)
- proportional figures in data UI (use `font-variant-numeric: tabular-nums`)
- orphaned last-line words (fix with `text-wrap: balance`)

Color and surfaces:
- pure `#000000` backgrounds (use off-black/charcoal); oversaturated accents; more than one accent color (pick one, remove the rest)
- mixed warm and cool grays; purple/blue "AI gradient" aesthetic (the most common AI fingerprint)
- untinted generic box-shadows; inconsistent lighting direction across shadows
- a random dark section inside a light page (commit to one theme; contrast via same-family shades)
- flat empty sections with zero depth (add subtle texture, imagery, or ambient background)

Layout:
- everything centered and symmetrical (break with offsets, mixed ratios, left-aligned headers)
- three equal card columns as the feature row (replace with zigzag, asymmetric grid, or horizontal scroll)
- `height: 100vh` sections (use `min-height: 100dvh`); no max-width container; uniform border-radius everywhere
- CTAs at random heights in card groups (pin buttons to card bottoms); misaligned shared elements across side-by-side columns
- mathematically-centered icons/text that look optically off (nudge 1-2px)

Interactivity and states:
- missing hover/active/pressed feedback; zero-duration transitions (use 200-300ms)
- missing focus rings (accessibility requirement, not optional)
- generic spinners instead of skeleton loaders; missing empty states and inline error states; `window.alert()` anywhere
- dead `#` links; no active-page indication in nav; instant anchor jumps (add `scroll-behavior: smooth`)
- animations on `top/left/width/height` (switch to `transform`/`opacity`)

Content:
- "John Doe" names, fake-round numbers (`99.99%`), "Acme Corp" brands, Lorem Ipsum
- AI copy cliches ("Elevate", "Seamless", "Unleash", "Next-Gen", "Delve"); passive voice; "Oops!" errors; Title Case On Everything
- identical blog dates; reused avatars for different people

Components and icons:
- border+shadow+white default cards; always filled+ghost button pairs; pill badges everywhere; accordion FAQs; 3-tower pricing with height as the only emphasis; modals for simple actions; 4-column footer link farms
- Lucide/Feather-only icons (differentiate with Phosphor/Heroicons); cliche metaphors (rocketship=launch, shield=security); inconsistent stroke widths; missing favicon

Code quality:
- div soup (use semantic `<nav>/<main>/<article>/<section>`); inline styles mixed with the styling system; hardcoded pixel widths; missing alt text; `z-index: 9999`; commented-out dead code; imports that do not exist in the dependency file

## Fix Priority

1. Fonts and type scale (biggest visible lift per line changed)
2. Color discipline: one accent, one gray family, one theme
3. Layout breaks: kill the three-card row, fix container width, align card internals
4. States: hover, focus, loading, empty, error
5. Content pass: names, numbers, copy register
6. Texture and depth last; they polish, they do not rescue

## Rules

- Work with the existing stack; check the dependency file before using anything.
- Never break functionality for aesthetics; behavior-preserving diffs only.
- Keep changes small and reviewable; no wholesale rewrites.
- The existing `DESIGN.md`, tokens, fonts, assets, and posture override any suggestion here.

## Local Sources

Adapted from taste-skill `redesign-skill` (https://github.com/Leonxlnx/taste-skill), MIT License, Copyright (c) 2026 Leonxlnx. See `LICENSE` in this folder.
