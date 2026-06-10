---
name: takyon-lightreel-seedance-fal-ugc
description: Build runnable single-person UGC prompt workflows from company inputs by discovering winning creator formats in Lightreel, converting them into Seedance-safe prompts, and preparing fal submission payloads.
version: 1.0.0
author: OpenAI
license: Proprietary
platforms: [linux, macos]

metadata:
  hermes:
    category: takyon
    tags: [takyon, lightreel, seedance, fal, ugc, video]
    related_skills: [ugc-video-ad, takyon-reddit-ads, takyon-static-ad-creative-generator]
    requires_toolsets: [takyon]
    requires_tools: []
    routing:
      owns: The company-input to Lightreel-discovery to Seedance-prompt to fal-payload workflow for single-person UGC video generation.
      when_to_use:
        - When runtime provides company facts and the goal is to discover a high-performing creator format before generating a Seedance prompt.
        - When a team needs a repeatable framework for turning business context into Lightreel research prompts and fal submission payloads.
        - When single-person selfie-style UGC constraints must be preserved across ideation and rendering.
      do_not_use_for:
        - Do not use for final UI implementation, API key storage, or provider-specific secret management.
        - Do not use for product-shot, screen-recording, or multi-actor ad workflows.
  takyon:
    scope: business
    allowed_roots: [product]
    output_root: product
    publication:
      - product/lightreel-seedance-fal-ugc-workflow.md

required_environment_variables: [LIGHTREEL_API_KEY, FAL_KEY]
required_credential_files: []
---

# Lightreel Seedance fal UGC Skill

## Overview

Use this skill to turn runtime company input into a durable creative workflow for single-person UGC video generation. The skill covers four stages:

1. Normalize company truth into a compact product brief.
2. Ask Lightreel to discover the strongest proven creator format and hook family.
3. Convert the returned creative angle into a Seedance-safe prompt with rendering constraints.
4. Prepare the fal reference-to-video payload, including a reference image URL from a real object-storage or publish-target URL.

This skill ships with helper scripts so an agent can run the full path from runtime input to Lightreel prompt to Seedance prompt to fal payload. It does not require or expose provider secrets.

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
- Build the discovery prompt with `${HERMES_SKILL_DIR}/scripts/build_lightreel_prompt.js`.
- Query Lightreel with `${HERMES_SKILL_DIR}/scripts/query_lightreel.js`.
- For local Takyon runs, the helper scripts also check `TAKYON_ENV_FILE`, `$TAKYON_HOME/secrets/.env`, and `$TAKYON_HOME/.env` before failing on missing credentials.
- Convert the returned conversation into a Seedance-safe prompt with `${HERMES_SKILL_DIR}/scripts/seedancify.js`.
- Build the fal payload with `${HERMES_SKILL_DIR}/scripts/build_fal_payload.js`.
- Run the full workflow with `${HERMES_SKILL_DIR}/scripts/run_workflow.js`.
- Publish the durable workflow spec to `product/lightreel-seedance-fal-ugc-workflow.md`.

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
- Running the wrapper script without `LIGHTREEL_API_KEY` when live discovery is expected.

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
2. Treat Lightreel as the format-discovery and hook-discovery layer.
3. Treat Seedance conversion as a renderer-safety and identity-preservation layer.
4. Preserve the creative angle from Lightreel instead of rewriting it into generic ad copy.
5. Keep the workflow single-person if the constraints require single-person.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Lightreel keeps returning demo ads | Tighten the prompt around single-person talking-head UGC and forbid UI, product shots, and cutaways |
| Lightreel output feels too safe | Allow funny, irreverent, chaotic, or lightly skitty creator tone in the prompt |
| Seedance prompt loses the original hook energy | Reinsert the exact Lightreel script and only add render constraints around it |
| Render includes text overlays | Add explicit negatives: no on-screen text, no captions, no overlay graphics |
| Video drifts visually | Add stronger identity-lock and camera-stability constraints tied to the reference image |
