---
name: takyon-lightreel-seedance-fal-ugc
description: >-
  Turn business facts into a single-person creator-format research brief, a Seedance-safe prompt,
  and a fal-compatible render payload. Use when a UGC concept needs evidence-informed creator
  direction before video generation. Do not use for secret handling, product UI work, screen
  recordings, product-shot videos, or multi-actor ads.
---

# Lightreel Seedance fal UGC Skill

## Overview

Use this skill to turn runtime company input into a durable creative workflow for single-person UGC video generation. The skill covers four stages:

1. Normalize company truth into a compact product brief.
2. Ask Lightreel to discover the strongest proven creator format and hook family.
3. Convert the returned creative angle into a Seedance-safe prompt with rendering constraints.
4. Prepare the fal reference-to-video payload, including a reference image URL from a real object-storage or publish-target URL.

This skill ships with helper scripts so an agent can run the full path from runtime input to Lightreel prompt to Seedance prompt to fal payload. It does not require or expose provider secrets.

> **Money gate — live Lightreel discovery is DISABLED.** A live call to `api.lightreel.ai` is a billable provider request charged against `LIGHTREEL_API_KEY`. Per the Takyon hardline rule ("No ungated paid capability"), no path may spend real money without a money gate (reserve before the call, exact price, settle on success, release on failure; unpriced = refused). The gate for Lightreel does not exist yet and Lightreel has no resolved price in `agent/usage_pricing.py`, so `scripts/query_lightreel.js` **fails closed**: it refuses the billable call and writes a `blocked_missing_money_gate` receipt instead of spending unmetered money. The non-paid prompt-build / Seedancify / fal-payload-build steps still work. See `## Money Gate (Live Discovery Disabled)` below for what must ship to re-enable it.

## When to Use

- Runtime already knows the company, product category, differentiators, and creative constraints, but the team needs a reliable prompt framework.
- The target asset is a single-person selfie-style UGC video rendered with Seedance through fal.
- The workflow must stay compatible with direct HTTP calls to Lightreel and fal.
- Do not use for screen-demo ads, product-cutaway ads, or workflows that depend on visible UI.

## Quick Reference

- Primary root: `product/`
- Publication paths: `product/lightreel-seedance-fal-ugc-workflow.md`
- Tool names used by this skill: none required

## Prerequisites

- Company runtime must provide a truthful product brief or enough input to derive one.
- A Lightreel API key and fal API key must exist in the Safebox-backed runtime environment, but this skill must never hardcode or publish them. The helper scripts read `LIGHTREEL_API_KEY` and `FAL_KEY`; `FAL_API_KEY` is also accepted as an alias for local runs.
- A reference image must already exist in object storage or on a business publish target that resolves to a real public URL.
- The reference image should be represented as a URL such as `https://example-bucket.s3.amazonaws.com/reference-images/<company>/<image>.png`.
- Seedance should consume that image as a reference input through `image_urls`, not as a starting frame through `image_url`.

## References

- `templates/payloads.md`

## Templates

- `templates/payloads.md`
- `templates/runtime-input.example.json`

## Scripts

- `scripts/build_lightreel_prompt.js`
- `scripts/query_lightreel.js`
- `scripts/seedancify.js`
- `scripts/build_fal_payload.js`
- `scripts/env.js`
- `scripts/run_workflow.js`

## How to Run

- Inspect `templates/runtime-input.example.json` and shape runtime input to match it.
- Build the discovery prompt with `scripts/build_lightreel_prompt.js`, resolved relative to this
  native skill directory.
- Query Lightreel with `scripts/query_lightreel.js`. This step is **money-gated and currently disabled**: it fails closed (exit 2) and writes a `blocked_missing_money_gate` receipt instead of making the billable provider call. Treat the returned refusal as expected until the creative-credit gate ships; do not work around it by calling `api.lightreel.ai` directly.
- For local Takyon runs, the helper scripts also check `TAKYON_ENV_FILE`, `$TAKYON_HOME/secrets/.env`, and `$TAKYON_HOME/.env` before failing on missing credentials.
- Convert the returned conversation into a Seedance-safe prompt with `scripts/seedancify.js`.
- Build the fal payload with `scripts/build_fal_payload.js`.
- Run the full workflow with `scripts/run_workflow.js`.
- Publish the durable workflow spec to `product/lightreel-seedance-fal-ugc-workflow.md`.

## Money Gate (Live Discovery Disabled)

Live Lightreel discovery spends real money and has no money gate, so it is disabled. `scripts/query_lightreel.js` fails closed and `scripts/run_workflow.js` stops with a blocked receipt rather than calling `api.lightreel.ai`. A present `LIGHTREEL_API_KEY` is not authorization to spend; the gate is. Do not bypass this by calling the provider from prose, a one-off script, or a direct HTTP request.

Lightreel discovery is a fixed business-scoped creative/research action, so its canonical money rail is the creative-credit rail (the same rail behind `business_ugc_ad_generate` / `business_static_ad_generate`), not the product app usage rail. To re-enable live discovery, ship the gate the Hermes way:

1. `hermes-agent-main/plugins/takyon/core.py` — add a `lightreel_discover` action to `_CREATIVE_CREDIT_COST_DEFAULTS` and `_CREATIVE_CREDIT_COST_ENVS` (a fixed operator-facing credit price).
2. `hermes-agent-main/agent/usage_pricing.py` — add the exact Lightreel per-request provider cost (e.g. `("lightreel", "discover")` with `request_cost`). Without a resolved price the action stays refused; do not add a second pricing table in this skill.
3. `hermes-agent-main/plugins/takyon/creative_gateway.py` — add a `/internal/creative-gateway/lightreel-render` authority route that reserves credits with `_reserve_creative_credits`, makes the live call server-side with the Safebox-backed key, commits on success (`_commit_creative_credits`) and releases on failure (`_release_creative_credits`) — mirroring `/ugc-render` and `/logo-render`.
4. Add a `business_*` authority tool that calls that route, name it in this skill, and route discovery through the tool instead of `query_lightreel.js`.

Until all four exist, the only correct behavior is the fail-closed refusal.

## Procedure

1. Start from company truth.
   Required minimum fields:
   - company name
   - product category
   - target audience
   - core pain
   - mechanism
   - differentiators
   - allowed and disallowed creative constraints
   - preferred duration
   - desired CTA or business goal

2. Build the normalized product brief.
   Use a compact shape like:
   - `product`
   - `category`
   - `audience`
   - `core_pain`
   - `mechanism`
   - `differentiators`
   - `proof`
   - `cta_goal`
   - `render_constraints`

3. Send only the product brief and hard production constraints to Lightreel.
   Do not hand Lightreel a specific UGC format when the goal is discovery.
   Do:
   - ask Lightreel to discover the strongest proven viral single-person UGC format for the product
   - require direct-to-camera creator speech
   - state whether funny, irreverent, chaotic, confessional, or lightly skitty tones are allowed
   - state what is forbidden: UI, product shots, cutaways, overlays, second character

4. Ask Lightreel for four outputs.
   The framework should request:
   - the chosen UGC format
   - the hook family that makes that format work
   - one company-specific spoken script
   - one Seedance-ready rendering prompt

5. Inspect the Lightreel output for the real creative payload.
   The useful parts are:
   - format family
   - hook logic
   - tonal posture
   - spoken script
   - scene and performance notes

6. Seedancify the result without flattening it.
   Preserve:
   - the format family
   - the emotional angle
   - the hook energy
   - the spoken script
   Add:
   - reference-image identity lock
   - camera framing
   - motion rules
   - timing guidance
   - hard negatives such as no text, no UI, no cutaways, no face drift
   - an explicit instruction that `@Image1` is reference-only, not a literal first frame

7. Prepare the fal payload.
   Use:
   - `prompt` = final Seedance-safe prompt
   - `image_urls` = reference image URL list from object storage
   - `duration`
   - `resolution`
   - `aspect_ratio`
   - `generate_audio`

8. Publish the workflow and payload shapes.
   The durable artifact must capture:
   - the normalized runtime input shape
   - the Lightreel prompt framework
   - the Seedance conversion rules
   - the fal request payload template
   - object-storage or publish-target URLs only, never secrets

9. If an agent should execute the workflow end to end, use the wrapper script.
   Expected artifacts:
   - `lightreel-prompt.txt`
   - `lightreel-conversation.json`
   - `seedancified.json`
   - `fal-payload.json`
   - `run-receipt.json`

## Output Format

- `product/lightreel-seedance-fal-ugc-workflow.md`
  - business-readable workflow spec
  - Lightreel prompt framework
  - Seedance conversion checklist
  - fal payload examples with placeholders
- `product/lightreel-seedance-fal-ugc-run/`
  - generated workflow artifacts from the helper scripts

## Publication

This skill publishes one durable workflow spec:

- `product/lightreel-seedance-fal-ugc-workflow.md`

This skill can also produce runnable artifacts under:

- `product/lightreel-seedance-fal-ugc-run/`

The publication is successful when that file contains:
- the runtime input schema
- the Lightreel discovery prompt template
- the Seedance-safe prompt structure
- the fal submission payload shape
- placeholder object-storage reference image URLs

Workflow execution is successful when `product/lightreel-seedance-fal-ugc-run/run-receipt.json` exists and points to the generated prompt, Lightreel conversation, Seedance prompt, and fal payload files.

## Common Pitfalls

- Preselecting the UGC format before Lightreel does discovery. That narrows the search space and weakens the result.
- Asking Lightreel for generic SaaS ads instead of creator-native single-person UGC.
- Flattening the returned creative angle into a bland feature list while Seedancifying.
- Letting the Seedance prompt imply UI, captions, overlays, or cutaways when those are forbidden.
- Forgetting the reference-image identity lock.
- Accidentally using Seedance image-to-video semantics that treat the image as a start frame instead of using reference-to-video semantics.
- Publishing provider keys, signed URLs, or internal storage details.
- Trying to "fix" the money-gate refusal by calling `api.lightreel.ai` directly, exporting the key, or restoring the old live `query_lightreel.js`. Live discovery stays disabled until the creative-credit gate ships (see `## Money Gate (Live Discovery Disabled)`).

## Verification Checklist

- [ ] Runtime company input is normalized into a compact product brief
- [ ] Lightreel is asked to discover the format instead of being handed one
- [ ] The returned hook family and tone are preserved in the Seedance prompt
- [ ] The Seedance prompt includes reference-image identity rules and hard negatives
- [ ] The fal payload includes a real or placeholder object-storage reference image URL and no secrets
- [ ] The published artifact lives in the declared `product/` path
- [ ] If the wrapper script ran, `run-receipt.json` truthfully points to the generated workflow files

## Rules

1. Never include API keys, signed URLs, or private bucket details in the skill.
2. Never make the billable Lightreel call from this skill's ungated path. Live discovery is money-gated and disabled; the only sanctioned re-enable path is the creative-credit gate described in `## Money Gate (Live Discovery Disabled)`.
3. Treat Lightreel as the format-discovery and hook-discovery layer.
4. Treat Seedance conversion as a renderer-safety and identity-preservation layer.
5. Preserve the creative angle from Lightreel instead of rewriting it into generic ad copy.
6. Keep the workflow single-person if the constraints require single-person.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Lightreel keeps returning demo ads | Tighten the prompt around single-person talking-head UGC and forbid UI, product shots, and cutaways |
| Lightreel output feels too safe | Allow funny, irreverent, chaotic, or lightly skitty creator tone in the prompt |
| Seedance prompt loses the original hook energy | Reinsert the exact Lightreel script and only add render constraints around it |
| Render includes text overlays | Add explicit negatives: no on-screen text, no captions, no overlay graphics |
| Video drifts visually | Add stronger identity-lock and camera-stability constraints tied to the reference image |
