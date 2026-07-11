---
name: taste-imagegen-web
description: Art-direct generated imagery and visual references for Takyon product/site work before calling business_generate_site_image. Use only when a brief-derived web surface genuinely needs imagery; produce page-role-specific prompts and local published assets, never generic filler or direct provider calls.
license: MIT
---

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
purpose, and a fresh idempotency key. Give its returned `public_path` to the coding worker. Reuse the
asset on later iterations unless the visual role or direction actually changed.

## Provenance

Adapted from Taste Skill's `imagegen-frontend-web` at commit
`b17742737e796305d829b3ad39eda3add0d79060`:
https://github.com/Leonxlnx/taste-skill/blob/b17742737e796305d829b3ad39eda3add0d79060/skills/imagegen-frontend-web/SKILL.md
