---
name: takyon-static-ad-creative-generator
description: 'Generate business-scoped static IMAGE AD creative bundles for one Takyon business: strict per-ad JSON specs (angle, hook, audience, awareness, visual template, layout, copy, QA), compiled art-directed image prompts, and optional OpenAI gpt-image-2 renders published under product/static-ads/<slug>/. Use for performance ad creative — NOT generic image generation.'
version: 1.2.0
author: Sai Alisetty
license: MIT
platforms: [linux, macos]

metadata:
  hermes:
    category: takyon
    tags: [takyon, ads, performance-marketing, creative, static-ad, image-generation, meta, instagram, facebook, gpt-image]
    related_skills: [takyon-meta-ads, takyon-reddit-ads, takyon-distribution, ugc-video-ad]
    requires_toolsets: [takyon]
    requires_tools: [business_read_business, business_read_file, business_list_files, business_static_ad_generate]
    routing:
      owns: Per-business static performance-ad image creative generation from brief to spec, prompt, QA bundle, and optional image render.
      when_to_use:
        - A business needs static image ad variants for Meta (Facebook/Instagram).
        - The operator wants multiple ad angles as reviewable specs before or alongside paid distribution work.
        - The business needs a batch creative test matrix with dry-run placeholders or optional OpenAI renders.
      do_not_use_for:
        - Generic or artistic image generation, logo/brand-identity design, or video ad creative.
        - Long-form landing-page or email copywriting (route to a copywriting skill).
        - Launching, managing, or pulling performance metrics from live ad accounts.
  takyon:
    scope: business
    allowed_roots: [product]
    output_root: product
    publication:
      - product/static-ads/<slug>
      - product/static-ads/<slug>/manifest.json

required_environment_variables: []
required_credential_files: []
---

# Static Ad Creative Generator

## Overview

This skill turns a product/company input into **static performance-ad creative** for Meta
(Facebook + Instagram). It is opinionated about *advertising*, not just imagery:
marketing strategy is decided **first**, as a strict JSON **ad spec**, and only then is an
image model asked to render it. The image backend never invents the angle, hook, audience,
or proof.

Inside Takyon, the canonical durable output is a business-scoped asset bundle under
`product/static-ads/<slug>/`. The skill itself stays **self-contained and backend-agnostic**.
The default image backend is **OpenAI
`gpt-image-2`** (configurable), and the strategy/spec/prompt/QA stages are decoupled so a
different backend can be dropped in without touching them. Its only external side effect is
the image-generation API call (real spend); every other stage is local and offline.

```
1 Intake     product, audience, offer, proof, brand, platform   -> creative brief
2 Strategy   pick angles from the taxonomy by awareness + proof  -> chosen angles
3 Ad Spec    strict JSON per creative (schema-validated)         -> *.spec.json
4 Compile    spec -> art-directed image-model prompt (no strategy invention)
5 Generate   call the image backend (gpt-image-2 by default)     -> *.png + bundle
6 QA         9-check rubric incl. a hard policy gate             -> *.qa.json
7 Output     image + spec + prompt + copy + QA + next iteration  -> *.output.json
```

## When to Use

- Turning a product URL / description / brief into **static image ad variants** for Meta.
- Generating multiple **ad angles** from one product as reviewable specs.
- Producing a **strict, reviewable ad spec** before spending money on generation.
- **Batch** producing a test matrix of creatives across angles, placements, and aspect ratios.

**Do not use for:** generic/artistic image generation, video ad creative, logo/brand-identity
design, long-form landing-page copy, or operating live ad accounts. This skill is specifically
a *static performance-ad creative spec generator*.

> This section is kept aligned with `metadata.hermes.routing` (owns / when_to_use /
> do_not_use_for). If they ever disagree, fix the routing metadata in the same change.

## Quick Reference

- **Primary root:** `product/`
- **Publication path:** `product/static-ads/<slug>/`
- **Schema (source of truth):** `templates/ad-spec.schema.json`
- **Angles:** `references/angle-taxonomy.md` · **Hooks:** `references/hook-strategy.md` · **Visuals:** `references/visual-templates.md`
- **Platform limits:** `references/platform-specs.md` · **Policy:** `references/policy-checks.md`
- **Prompt rules:** `references/prompt-compiler-rules.md` · **QA:** `references/qa-rubric.md`
- **Tool names used by this skill:** `business_read_business`, `business_read_file`, `business_list_files`, `business_static_ad_generate`
- **Scripts dir:** `scripts/` (referenced as `${HERMES_SKILL_DIR}/scripts/...` when bundled in an agent)
- **Validate:** `python scripts/validate_spec.py <spec|batch|dir>`
- **Compile prompt:** `python scripts/compile_prompt.py <spec.json>`
- **Live canonical path:** call `business_static_ad_generate` so creative credits and receipt recording are enforced
- **Multi-size fan-out:** add `--aspect-ratio 1:1,9:16,1.91:1` to render one creative at every size a placement needs.
- **Batch helper script:** `python ${HERMES_SKILL_DIR}/scripts/batch_generate.py <batch.json> -o product/static-ads/<slug>/`
- **Aspect ratios:** free-form `W:H`, any ratio from 1:3 to 3:1 (sized automatically; `--crop` for exact).
- **Test mode (no key, no spend):** add `--dry-run` (mock backend writes a labeled placeholder).
- **Default backend/model:** OpenAI `gpt-image-2` (override with `OPENAI_IMAGE_MODEL`).

## Prerequisites

- **Python 3.9+.**
- **Live mode (real render, real spend)** needs the `openai` package plus an API key, supplied
  either via `OPENAI_API_KEY` **or** `--api-key-file PATH` (passed straight to the client, never
  exported to the environment). `pip install -r scripts/requirements.txt`.
- **Test mode** needs neither a key nor `openai`: `--dry-run` exercises intake → strategy →
  spec → prompt → QA and writes a **labeled mock placeholder** (an honest "not a real render"
  artifact), never a fake success.
- Optional: `Pillow` (exact `--crop` to 1:1/4:5/9:16 + nicer placeholders) and `jsonschema`
  (richer validation; a zero-dependency fallback runs without it).
- Optional env: `OPENAI_IMAGE_MODEL` (default `gpt-image-2`), `IMAGE_BACKEND` (default `openai`).
- No third-party creative platform, CLI, or paid service is required or used.
- Start with `business_read_business`, then inspect existing `product/static-ads/`,
  relevant product files, and any prior ad assets with `business_list_files` and
  `business_read_file` before creating new creative bundles.

## References

- `references/angle-taxonomy.md` — the 14 ad angles, how to pick by awareness + proof, variant waves.
- `references/hook-strategy.md` — hook tactics, provocative-but-defensible patterns, the creative stack, and hook QA.
- `references/visual-templates.md` — composition templates, the SSCLP art-direction framework, anti-artifact rules.
- `references/platform-specs.md` — Meta placements, safe zones, copy limits, the `aspect_ratio → model size` map.
- `references/policy-checks.md` — advertising-policy red flags and the machine-checkable lint signals.
- `references/prompt-compiler-rules.md` — the exact contract the prompt compiler implements.
- `references/qa-rubric.md` — the 9-check QA rubric and scoring.

## Templates

- `templates/ad-spec.schema.json` — JSON Schema for one ad spec (the contract every spec must satisfy).
- `templates/ad-spec.template.json` — a blank spec skeleton with field hints.
- `templates/creative-brief.template.md` — the intake brief to fill before writing specs.

## Scripts

- `scripts/validate_spec.py` — schema validation + performance/policy lint (errors vs. warnings).
- `scripts/compile_prompt.py` — deterministic spec → art-directed image prompt.
- `scripts/backends.py` — swappable backend interface; `OpenAIImageBackend` (default), `MockImageBackend`; size map, crop, `--api-key-file` reader.
- `scripts/generate_image.py` — single-creative pipeline (validate → compile → **generate** → QA → bundle).
- `scripts/batch_generate.py` — batch over an array/dir of specs; writes `manifest.json`.
- `scripts/qa_check.py` — scaffold the QA report from a spec.

## How to Run

**Read first:** the product input / creative brief, then the relevant references
(`angle-taxonomy.md` for angle choice, `hook-strategy.md` for the scroll-stopping frame,
`platform-specs.md` for placement + size, `policy-checks.md` before writing copy). Specs are
authored from `templates/ad-spec.template.json`.

Choose a canonical publication directory first, for example:

```bash
PUBLICATION_DIR=product/static-ads/<slug>
```

Common path (one creative, live render):

```bash
pip install -r ${HERMES_SKILL_DIR}/scripts/requirements.txt
python ${HERMES_SKILL_DIR}/scripts/validate_spec.py examples/example-spec.json        # must be 0 errors
python ${HERMES_SKILL_DIR}/scripts/generate_image.py examples/example-spec.json -o "$PUBLICATION_DIR" --crop \
  --api-key-file ~/.openai_key                                      # or export OPENAI_API_KEY
```

One creative at every size a placement needs (multi-size fan-out):

```bash
python ${HERMES_SKILL_DIR}/scripts/generate_image.py examples/example-spec.json -o "$PUBLICATION_DIR" --crop \
  --aspect-ratio 1:1,4:5,1.91:1,9:16 --api-key-file ~/.openai_key
```

Batch a test matrix:

```bash
python ${HERMES_SKILL_DIR}/scripts/batch_generate.py examples/example-batch.json -o "$PUBLICATION_DIR" --crop --api-key-file ~/.openai_key
```

**Test mode (no key, no spend)** — rehearse the whole pipeline and write honest placeholders:

```bash
python ${HERMES_SKILL_DIR}/scripts/batch_generate.py examples/example-batch.json -o "$PUBLICATION_DIR" --dry-run --crop
```

- The **only step with an external effect / real spend** is generation
  (`generate_image.py` / `batch_generate.py` calling the image API). Everything before it is
  local. `--dry-run` switches that step to the mock backend.
- Swap model/backend: `OPENAI_IMAGE_MODEL=gpt-image-1 ...` (uses the gpt-image-1 size map) or
  `IMAGE_BACKEND=mock ...`.

## Procedure

1. **Read business state.** Call `business_read_business` first, then inspect existing
   `product/static-ads/` assets and any relevant product brief/source with
   `business_list_files` and `business_read_file`.
2. **Choose the publication dir.** Pick or confirm a canonical path
   `product/static-ads/<slug>/`. Reuse an existing slug when iterating on the same
   creative family; do not scatter one business's assets across random output dirs.
3. **Intake.** Fill `templates/creative-brief.template.md` from the product input. **Do not
   invent facts, metrics, testimonials, or endorsements.** Mark anything fictional
   `[FICTIONAL PLACEHOLDER]`.
4. **Strategy / angles.** From `references/angle-taxonomy.md`, choose 3–5 angles by
   audience **awareness level** and **strongest *real* proof**. Then use
   `references/hook-strategy.md` to choose the **hook tactic**, **creative mechanic**,
   **boldness level**, and **claim support** that make each angle worth clicking. One angle
   per creative. For first-pass cold traffic, include at least one **medium** and one **hard**
   boldness concept instead of producing only tasteful "safe" ads.
5. **Write the ad spec(s).** For each creative, author JSON conforming to
   `templates/ad-spec.schema.json`. Decide all strategy in `strategy`/`audience`; this
   includes the **boldness level**, **disruption target**, **hook tactic**,
   **psychological trigger**, **creative mechanic**, and the concrete **claim support** that
   makes the hook fair. Pick a `visual.template` that reinforces the same idea; keep
   `copy.overlay_text` short and sharp; set `layout` safe zones per
   `references/platform-specs.md`; fill `product.must_not_show`; pre-fill `qa` honestly.
6. **Validate.** `python ${HERMES_SKILL_DIR}/scripts/validate_spec.py <spec|batch|dir>`.
   Fix every ERROR; resolve each WARN.
7. **Generate into the canonical directory.** Run `generate_image.py` /
   `batch_generate.py` with `-o product/static-ads/<slug>/`. Use a key for a live render,
   or `--dry-run` for a labeled placeholder. In test/dry-run mode, leave the honest mock
   artifact — never claim a real render happened.
8. **QA.** Finish the `review` checks in each `*.qa.json` against the rendered image using
   `references/qa-rubric.md`. The **policy gate is hard** — a fail blocks the creative.
   Read every baked-in word for spelling/garble artifacts.
9. **Output.** Deliver the bundle per creative in `product/static-ads/<slug>/` (see
   Output Format); recommend the next iteration.

## Output Format

For **each** creative, inside `product/static-ads/<slug>/` next to the image(s):

- `<creative_id>.png` + `<creative_id>.prompt.txt` — the rendered creative and its compiled
  prompt. When rendering multiple ratios, files are suffixed per ratio, e.g.
  `<creative_id>__1x1.png`, `<creative_id>__9x16.png`, `<creative_id>__1.91x1.png` (each with
  its own `.prompt.txt`, since FORMAT/size differ per ratio). Mock placeholder under `--dry-run`.
- `<creative_id>.spec.json` — the exact, schema-valid ad spec used.
- `<creative_id>.qa.json` — the 9-check QA report (verdict: ship / iterate / block).
- `<creative_id>.output.json` — delivery record: backend/model, platform/placement,
  `aspect_ratios` + a `renders` array (`{aspect_ratio, size, images, prompt_file}` per ratio),
  suggested headline + primary text + CTA, QA notes, recommended next iteration. The `backend`
  (`openai` vs `mock`) and `dry_run` flag make a real render distinguishable from a placeholder.

Batch runs also write `manifest.json` summarizing every creative and any failures.

## Publication

This skill publishes a business-scoped creative bundle under
`product/static-ads/<slug>/`. Its durable outputs are the per-creative files in that
directory plus a batch `manifest.json`.

- Always pass `-o product/static-ads/<slug>/` when running the bundled scripts. Their
  generic `output/` default is not the canonical Takyon publication path.

- **Proof of a real render:** the saved `<creative_id>.png` plus `<creative_id>.output.json`
  with `"backend": "openai"` and `"dry_run": false`. A dry-run leaves `"backend": "mock"` so a
  placeholder can never be mistaken for a live render.
- This skill makes **no external platform claim** — it never launches or uploads to an ad
  account. Generation stops at saved image files and their sidecar JSON.

## Common Pitfalls

- **Letting the image model decide strategy.** Decide angle/hook/audience/proof in the spec;
  the prompt compiler only translates.
- **Writing category copy instead of a hook.** "Better workflow", "all-in-one platform", and
  other generic category lines do not earn the click. Pick a real hook tactic and back it with
  claim support in `strategy.*`.
- **Being too polite.** If the first concept sounds useful but not urgent, interruptive, or
  opinionated, turn up `strategy.boldness`, choose a higher-voltage hook tactic, and name the
  exact old way or belief the ad is attacking.
- **Provocative with no proof.** Contrarian, warning, confession, and shocking-statement
  hooks only work when the ad quickly pays them off with a demo, stat, comparison, offer, or
  other real evidence.
- **Truth gap: a placeholder is not a render.** A `--dry-run` PNG is a gray mock, not a real
  ad. Confirm `backend: openai` / `dry_run: false` (and a realistic file size) before calling a
  creative "generated."
- **Fabricated proof.** Inventing testimonials, reviews, ratings, "as seen in" logos, or
  third-party screenshots is deceptive and against platform/FTC rules; if you must show sample
  proof, label it illustrative. See `references/policy-checks.md`.
- **Publishing to `output/` instead of `product/static-ads/<slug>/`.** The files may exist,
  but they are in the wrong Takyon truth surface.
- **Personal-attribute targeting.** Agitate the *situation/desire*, never label the *person*.
- **Overlong baked-in text / wrong aspect ratio for placement / trusting overlay spelling.**
- **Editing the compiled prompt by hand.** Fix the spec instead, so it stays reproducible.

## Verification Checklist

- [ ] Every spec passes `validate_spec.py` with **0 errors**; warnings resolved or justified.
- [ ] One clear angle per creative, matched to the audience's awareness level.
- [ ] All proof is real & rights-cleared **or** visibly labeled illustrative/representative.
- [ ] No personal-attribute targeting; no guaranteed/unrealistic claims; no misleading UI.
- [ ] Aspect ratio matches placement; copy within platform limits; safe zones respected.
- [ ] QA report's policy gate is **clear** and overlay text is spelled correctly.
- [ ] A claimed live render is backed by a saved PNG **and** an `output.json` receipt with
      `backend: openai`, `dry_run: false` — not a mock placeholder.
- [ ] The bundle lives under `product/static-ads/<slug>/`, not a generic temp/output dir.
- [ ] Output bundle is complete (image + spec + prompt + qa + output record).

## Rules

1. **Strategy before pixels.** Decide marketing strategy in the ad spec; never delegate it to
   the image model. The prompt compiler translates, it does not invent.
2. **Truthful test/live.** A dry-run writes a labeled mock and is never reported as a real
   render; a live render is proven by its saved files. No fake success claims.
3. **Truthful proof.** Do not fabricate testimonials, reviews, ratings, endorsements,
   third-party screenshots, or "as seen in" logos; sample proof must be labeled illustrative.
4. **Backend-agnostic, key-safe.** Default to `gpt-image-2`; keep the backend swappable; read
   the key from `OPENAI_API_KEY` or `--api-key-file` and never hardcode or persist it.
5. **Canonical publication.** Publish bundles under `product/static-ads/<slug>/`, not a
   generic `output/` directory.
6. **Self-contained.** No dependency on Creatify, Luma, Higgsfield, Canva, Midjourney, Runway,
   or any third-party creative platform/API. Everything here runs standalone.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `No API key available` | Pass `--api-key-file PATH`, `export OPENAI_API_KEY`, or add `--dry-run` for the mock backend. |
| `The 'openai' package is required` | `pip install -r scripts/requirements.txt`. |
| Spec rejected with ERRORs | Read each path in the message; fix against `templates/ad-spec.schema.json`. |
| Lint WARN about a proof angle | State in `qa.policy_risks` whether proof is real & rights-cleared or labeled illustrative. |
| Image isn't an exact 4:5 / 9:16 | Re-run with `--crop` (needs Pillow); `gpt-image-2` 9:16 is ~0.571 before cropping. |
| Got a gray placeholder, not an ad | That's a `--dry-run` mock — re-run with a key (no `--dry-run`) for a live render. |
| `model not found` / no access | Set `OPENAI_IMAGE_MODEL=gpt-image-1` (uses its 3-size map); verify your account's model access. |
| Garbled/misspelled overlay text | Shorten `copy.overlay_text`, regenerate, or add the text in post. |
| Need exact platform numbers | Verify current specs in `references/platform-specs.md` before a paid launch. |
| Files landed in `output/` | Re-run with `-o product/static-ads/<slug>/` so the assets land in the canonical Takyon publication path. |
