---
name: takyon-brand-logo
description: 'Generate a business-scoped brand LOGO icon for one Takyon business with Nano Banana (Gemini gemini-2.5-flash-image): a flat-vector, transparent-background, icon-only mark (no text), creative-credit gated and rendered through the guarded logo authority route. Use for brand identity / logo creation — NOT performance ad creative, generic image generation, or video.'
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]

metadata:
  hermes:
    category: takyon
    tags: [takyon, brand, logo, identity, creative, image-generation, gemini, nano-banana]
    related_skills: [takyon-static-ad-creative-generator, takyon-build-product]
    requires_toolsets: [takyon, takyon-authority]
    requires_tools: [business_read_business, business_read_file, business_list_files, business_generate_logo]
    routing:
      owns: Per-business brand logo icon generation (flat vector, transparent background, icon-only) through the credit-gated logo authority route.
      when_to_use:
        - A business needs a brand logo / icon mark for its product site, app, or social profile.
        - The CEO is building or refreshing a business's brand identity and wants a real transparent-background logo asset.
      do_not_use_for:
        - Performance ad creative (route to takyon-static-ad-creative-generator).
        - Generic or artistic image generation, photo editing, or video creative.
        - Inventing brand voice, naming, or copy — that is operator-owned.
  takyon:
    scope: business
    allowed_roots: [product]
    output_root: product
    publication:
      - product/brand/logos/<slug>/logo.png
      - product/brand/logos/<slug>/receipt.json

required_environment_variables: [TAKYON_GEMINI_API_KEY]
required_credential_files: []
---

# Brand Logo

## Overview

This skill produces one **brand logo icon** for a Takyon business: a flat-vector,
transparent-background, **icon-only** mark with **no text**. The render is done by Nano Banana
(Gemini `gemini-2.5-flash-image`) through the guarded `business_generate_logo` tool, which
reserves a creative credit, renders the image in the logo authority route, and writes the asset
plus a cost receipt under `product/brand/logos/<slug>/`.

The brand brief is operator-owned and fixed: **flat vector, transparent background, icon-only,
no text.** The business name, category, and tone steer the icon concept; this skill never invents
brand voice, naming, or copy.

## When to Use

- A business needs a brand **logo / icon mark** for its product site, app, or social profile.
- The CEO is establishing or refreshing a business's brand identity and wants a real
  transparent-background logo asset (not a placeholder).

**Do not use for:** performance ad creative (use `takyon-static-ad-creative-generator`), generic
or artistic image generation, video creative, or inventing brand voice/naming/copy.

> This section is kept aligned with `metadata.hermes.routing` (owns / when_to_use /
> do_not_use_for). If they ever disagree, fix the routing metadata in the same change.

## Quick Reference

- **Primary root:** `product/`
- **Publication paths:** `product/brand/logos/<slug>/logo.png`, `product/brand/logos/<slug>/receipt.json`
- **Tool names used by this skill:** `business_read_business`, `business_read_file`, `business_list_files`, `business_generate_logo`
- **Provider / model:** Google Gemini `gemini-2.5-flash-image` (Nano Banana), ~$0.039/image
- **Spend gate:** creative credits (`logo_generate`, brand-level — no channel bucket), reserve→commit/release
- **Live-only:** logo generation spends real money; it is not stubbed in test mode

## Prerequisites

- **Live business** (`mode=live`). In test mode `business_generate_logo` refuses — logo generation
  spends real provider money and is not stubbed.
- **Creative credits.** The action is creative-credit gated; with zero credits the tool fails
  closed and never calls the provider.
- **Gemini key.** `TAKYON_GEMINI_API_KEY` (aliases `GEMINI_API_KEY` / `GOOGLE_API_KEY`) must be
  provisioned in Safebox; until then the authority route returns `503 gemini_image_unconfigured`
  and no credit is consumed.
- Read business state first with `business_read_business` (and product/research state with
  `business_read_file` / `business_list_files`) so the icon concept reflects the real
  name / category / tone.

## How to Run

1. Read the business state with `business_read_business`; inspect any existing brand assets under
   `product/brand/` with `business_list_files` / `business_read_file`.
2. Assemble `business_context` from real state: `{name, category, tone}`. Do not invent tone or
   naming — pull category/tone from research/product state; if absent, omit them.
3. Call `business_generate_logo` with `business`, a fresh `idempotency_key`, an optional `slug`,
   and `business_context`. The tool runs the two-half flow: a preflight credit gate in the
   handler, then the real key-resolution + provider call + reserve/commit in the logo authority
   route.
4. On success, the tool returns `asset_path` (`product/brand/logos/<slug>/logo.png`),
   `provider_cost_usd`, `credits_charged`, and a `receipt`. Re-read the receipt before claiming
   the logo is generated.

The **only** step with an external effect / real spend is `business_generate_logo`; everything
before it (reading state, assembling context) is local.

## Procedure

1. Read the current business state that matters (name, category, tone, existing brand assets).
2. Build `business_context` from that state — never fabricate brand voice.
3. Call `business_generate_logo` (live, with `idempotency_key`). The credit gate and receipt are
   the approval rail; do not stop for a generic re-confirmation about bounded provider spend.
4. If the tool returns `blocked_insufficient_creative_credits`, report that the business needs
   more creative credits — do not retry against the provider.
5. If the tool returns `503 gemini_image_unconfigured` (surfaced as a blocked status), record the
   missing `TAKYON_GEMINI_API_KEY` credential gate instead of claiming a logo.
6. Re-read `product/brand/logos/<slug>/receipt.json` and confirm `success`, `asset_path`, and
   `provider_cost_usd` before saying the logo is done.

## Output Format

- `product/brand/logos/<slug>/logo.png` — the rendered transparent-background icon (alpha channel).
- `product/brand/logos/<slug>/receipt.json` — durable receipt: business, slug, provider/model,
  `provider_cost_usd`, `credits_charged`, the prompt used, and the success status.

## Publication

This skill publishes a business-scoped brand asset under `product/brand/logos/<slug>/`. Its
durable outputs are `logo.png` and `receipt.json` in that directory.

- **Proof of a real render:** the saved `logo.png` plus `receipt.json` with `"success": true`,
  `"provider": "google"`, `"model": "gemini-2.5-flash-image"`, and a non-null `provider_cost_usd`.
- This skill makes **no external platform claim** — it never publishes the logo to a third party.

## Common Pitfalls

- **Inventing brand voice/tone.** Pull tone and category from business state; never fabricate.
- **Claiming a logo without the receipt.** A live render is proven by the saved PNG and the
  receipt, not by the tool call alone.
- **Treating a 503 / insufficient-credit block as success.** Those are honest blocked outcomes;
  record the missing gate instead of faking an asset.
- **Adding text to the logo.** The brand brief is icon-only, no text — keep it that way.

## Verification Checklist

- [ ] `product/brand/logos/<slug>/logo.png` exists and is a transparent-background icon.
- [ ] `receipt.json` shows `success: true`, the Gemini provider/model, and `provider_cost_usd`.
- [ ] Exactly one `logo_generate` credit debit is reflected in the credit balance.
- [ ] No brand voice/naming was invented; `business_context` came from real business state.
- [ ] On block (insufficient credits / unconfigured key), the missing gate is recorded, not faked.

## Rules

1. **Operator-owned brand brief.** Flat vector, transparent background, icon-only, no text. Steer
   only from real business name/category/tone.
2. **Truthful renders only.** A claimed logo must come from a real saved render and its receipt.
3. **Fail closed.** Zero credits ⇒ no provider call; missing key ⇒ `503`, no credit consumed.
4. **Canonical publication.** Publish under `product/brand/logos/<slug>/`, not a generic temp dir.
5. **One canonical tool.** Logo generation goes through `business_generate_logo`; do not add a
   parallel render path or a second pricing/credit table.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `503 gemini_image_unconfigured` | Provision `TAKYON_GEMINI_API_KEY` (or `GEMINI_API_KEY`/`GOOGLE_API_KEY`) in Safebox; the credit is not consumed until the key exists. |
| `blocked_insufficient_creative_credits` | The business needs more creative credits; do not retry against the provider. |
| `business_generate_logo requires a live business` | Switch the business to live mode; logo generation is not stubbed in test mode. |
| `google-genai is not installed` | The authority route lazily installs it; if the install is disabled, enable lazy installs or add the `gemini` extra. |
