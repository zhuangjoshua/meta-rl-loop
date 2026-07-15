---
title: "Taste Imagegen Web"
sidebar_label: "Taste Imagegen Web"
description: "Art-direct generated imagery and visual references for a web surface from its business and design brief"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Taste Imagegen Web

Art-direct generated imagery and visual references for a web surface from its business and design brief. Use when an interface genuinely needs page-role-specific original imagery. Do not use for generic filler, direct provider calls, logos, ad creative, or image generation unrelated to a web surface.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/creative/taste-imagegen-web` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Takyon loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Taste Imagegen for Takyon Web

Use this to formulate prompts for `business_generate_site_image`. The tool owns generation, Safebox
authorization, credits, receipts, and publication. Never request or handle an API key.

## Brief Read

Identify the business, audience, offer, page role, visual thesis, palette, crop, focal subject, and
implementation constraints. Existing brand assets and the selected `taste-frontend` direction win.

## Prompt Contract

Every prompt must specify:

- the asset's exact job, such as hero background, product detail, editorial evidence, or texture;
- subject and environment grounded in the real business;
- composition and safe regions for adjacent HTML text;
- palette, lighting, material, and photographic or illustrative treatment;
- requested aspect ratio and intended responsive crop;
- `no baked-in text, UI labels, logos, watermarks, browser chrome, or fake product controls`.

Generate one asset for one real role. Do not generate a whole page screenshot unless the operator
explicitly requests a visual reference comp. UI text belongs in accessible HTML/CSS.

## Art Direction

- Vary composition by page role; do not reflexively put the subject on the right of every image.
- Prefer a decisive visual concept over generic mood art.
- Keep implementation clarity high: clean silhouette, controllable crop, coherent palette, and enough
  negative space for the layout.
- Match the business, not its category stereotype. AI is not automatically purple glow; premium is
  not automatically beige serif; developer tooling is not automatically terminal cosplay.
- Avoid generic stock teams, floating abstract blobs, fake dashboards, meaningless data, and repeated
  assets masquerading as different evidence.

## Output

Call `business_generate_site_image` with a stable slug, the composed prompt, the correct size, a short
purpose, and a fresh idempotency key. Use its returned `public_path` in the current primary SDK
session. Reuse the asset on later iterations unless the visual role or direction actually changed.

## Provenance

Adapted from Taste Skill's `imagegen-frontend-web` at commit
`b17742737e796305d829b3ad39eda3add0d79060`:
https://github.com/Leonxlnx/taste-skill/blob/b17742737e796305d829b3ad39eda3add0d79060/skills/imagegen-frontend-web/SKILL.md
