---
name: ugc-video-ad
description: Generate a per-business UGC video ad (vertical talking-head selfie) from a brief. Writes the dialogue+action script, makes a photoreal reference image (gpt-image-2), animates it with Kling image-to-video, splits anything over ~10s into stitched <=10s clips with continuity, and applies a realism post pass. Use when a business needs a short social video ad.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]

metadata:
  hermes:
    category: takyon
    tags: [ugc, video, ad, kling, marketing]
    related_skills: []
    requires_toolsets: [takyon]
    requires_tools: [business_ugc_ad_generate]
    routing:
      owns: per-business UGC video ad asset generation from brief to finished mp4, script, and reference image
      when_to_use:
        - the business needs a short social talking-head video ad
        - the asset should be published under `product/ugc-ads/` as a business-scoped creative deliverable
      do_not_use_for:
        - screen-recording or UI-demo videos
        - non-business or multi-business assets
  takyon:
    scope: business
    allowed_roots: [product]
    output_root: product
    publication:
      - product/ugc-ads/<slug>/ad.mp4
      - product/ugc-ads/<slug>/script.json
      - product/ugc-ads/<slug>/reference.png

required_environment_variables: [OPENAI_API_KEY, FAL_KEY]
required_credential_files: []
---

# UGC Video Ad

## Overview

Produce a short, realistic **user-generated-content (UGC) video ad** — a vertical 9:16
talking-head selfie of one person recommending a business's product — from a structured
brief. The skill keeps **two layers strictly separate**:

- **Script layer** — the *words* and the *action paired with each line*, authored with
  the Rob Palmer ad-copy framework. See
  [references/dialogue-action-framework.md](references/dialogue-action-framework.md).
- **Production layer** — *how it looks and moves*: a photoreal reference image, Kling
  image-to-video, >10s splitting + continuity stitching, and a realism post pass. See
  [references/realism-framework.md](references/realism-framework.md) and
  [references/editing-and-stitching.md](references/editing-and-stitching.md).

The copy never sets production knobs and production never rewrites the copy.

## When to Use

- A business needs a short (15–60s) social video ad with a person speaking to camera.
- You have (or can write) a product brief: who the product is for, what it does, the look
  of the spokesperson, and a CTA.
- You want the ad longer than one ~10s clip — the skill splits and stitches automatically.

**Do not use for:** screen-recording / UI-demo videos (this skill is a person talking, and
deliberately never cuts to fullscreen UI — see the realism framework); non-business or
multi-business assets (this skill is business-scoped).

## Quick Reference

- Primary root: `product/`
- Publication paths: `product/ugc-ads/<slug>/ad.mp4`, `.../script.json`, `.../reference.png`
- Tool used by this skill: **`business_ugc_ad_generate`** for the live path (`business_ugc_ad_write` is committed internally by that tool)
- Main entrypoint: `${HERMES_SKILL_DIR}/scripts/build_ad.py`
- Free planning: `build_ad.py --brief <brief> --dry-run` (no API calls, no spend)

## Prerequisites

- **`OPENAI_API_KEY`** — gpt-image-2 reference image (env or a local `.env`; never hardcode).
- **`FAL_KEY`** — Kling image-to-video via fal.ai (env or `.env`).
- **`ffmpeg` + `ffprobe`** on `PATH` — stitching and post.
- Python deps for the live path: `httpx`, `fal-client`. (`--dry-run` needs neither.)
- The **`business_ugc_ad_generate`** tool must be registered (gated in frontmatter
  `metadata.hermes.requires_tools`). Use it for any live spendful generation so creative
  credits and the canonical receipt path stay truthful.

## References

- [references/dialogue-action-framework.md](references/dialogue-action-framework.md) — SCRIPT layer (robpalmer): classify → WHY/WHAT/HOW → bold out-of-pocket hooks → dense/fast beats → variations → checklist.
- [references/realism-framework.md](references/realism-framework.md) — PRODUCTION: image anti-sheen, energy-in-delivery (grounded body, not flailing), camera-last @ cfg 0.3, one voice, product in-scene.
- [references/editing-and-stitching.md](references/editing-and-stitching.md) — PRODUCTION: >10s split + continuity stitch, motivated jump cuts (no zoom ramp), never upscale, grain pass.

## Templates

- [templates/brief.json](templates/brief.json) — production inputs (business, product, classification, subject/wardrobe/setting, persona, cta).
- [templates/script.json](templates/script.json) — SCRIPT-layer output: ordered `dialogue_action` beats.
- [assets/example-brief.json](assets/example-brief.json) — a filled brief (script embedded) for the dry-run and smoke test.

## Scripts

- `${HERMES_SKILL_DIR}/scripts/build_ad.py` — orchestrator (planning + full build).
- `${HERMES_SKILL_DIR}/scripts/pipeline.py` — vendored primitives (image, Kling, ffmpeg).
- `${HERMES_SKILL_DIR}/scripts/postpass.sh` — grain pass (default) + optional `--jumpcuts`.

## How to Run

Common path:

```bash
# 1) FREE: plan only — clip split + every compiled prompt, zero API calls/spend.
python ${HERMES_SKILL_DIR}/scripts/build_ad.py \
  --brief assets/example-brief.json --dry-run

# 2) LIVE / CANONICAL: call business_ugc_ad_generate so credits + receipt are enforced.
```

Useful flags: `--jumpcuts` (extra silence-drop reframe cuts in post), `--skip-post`,
`--transition-mode continuity|jumpcut` (continuity chains the last frame; jumpcut
re-anchors every clip from the original reference and varies framing), `--slug <name>`,
`--out-root product`, `--max-clip 10`, `--wps 3.0` (brisk pace; raise to pack more
content / speak faster), `--workdir <dir>`.

After a full build, `build_ad.py` prints a **`business_ugc_ad_write` payload** — the
agent then calls that tool to record the asset (the script does not).

## Procedure

1. **Read business state** — confirm the business id and that no current ad already
   covers this brief at `product/ugc-ads/<slug>/`.
2. **Script layer** — using
   [dialogue-action-framework.md](references/dialogue-action-framework.md), classify the
   brief and write the `dialogue_action` beats into a `script.json` (3–5 variations are
   ideal; pick one to produce). This sets **only** words + paired actions.
3. **Production layer** — fill `brief.json` (subject/wardrobe/setting/persona/cta) per
   [realism-framework.md](references/realism-framework.md).
4. **Plan (free)** — `build_ad.py --dry-run` to verify the ≤10s clip split and the
   compiled prompts before spending.
5. **Build** — run `build_ad.py` to generate the reference image, per-clip Kling i2v with
   either last-frame continuity or jumpcut re-anchoring, then stitch and post-process.
6. **Publish** — outputs are written under `product/ugc-ads/<slug>/`.
7. **Record** — call **`business_ugc_ad_write`** with the printed payload to commit the
   durable Takyon asset record (idempotent).

## Output Format

Published under `product/ugc-ads/<slug>/`:

- `ad.mp4` — the finished vertical 9:16 ad (machine/binary artifact).
- `script.json` — the exact dialogue+action beats used (structured, machine-readable).
- `reference.png` — the gpt-image-2 reference still the ad was animated from.

## Publication

- This skill publishes to the canonical directory **`product/ugc-ads/<slug>/`** inside the
  `product/` root, where `<slug>` derives from the business + product (override with
  `--slug`).
- The durable outputs are the three files above; that directory is the canonical home for
  this business's ad.
- The truth source for the *recorded asset* (that this ad exists for the business) is the
  **`business_ugc_ad_write`** tool commit, which references the same `ad.mp4` path.

## Common Pitfalls

- **Blurring the two layers** — letting copy dictate camera/realism, or letting production
  rewrite the script. Keep them separate.
- **Faking the record** — the scripts publish files but must **not** pretend to call
  `business_ugc_ad_write`; the agent commits that.
- **Flat/even/white lighting** in the reference — the #1 AI tell. Always directional/uneven.
- **Over-animating the body** — constant flailing/jitter reads as manic. Put energy in
  the *delivery* (brisk pace, punchy hook) and purposeful gestures, not nonstop motion.
- **Zoom-ramp cuts** or **upscaled punch-ins** — both read as fake/blurry. Hard cuts +
  native crops only.
- **One long clip > ~10s** — voice/lips drift. Let the skill split and stitch.
- **UI screenshot cutaways** — never; the product shows in-scene on a real device.

## Verification Checklist

- [ ] `--dry-run` shows beats grouped into clips each ≤10s, with continuity noted.
- [ ] Reference image reads as a real photo (skin texture, directional light); real ≠ ugly.
- [ ] One consistent voice/identity across all stitched clips.
- [ ] Cuts are motivated (stitch seams / silence-drop), never zoom ramps; tight shots sharp.
- [ ] Outputs exist at `product/ugc-ads/<slug>/{ad.mp4,script.json,reference.png}`.
- [ ] `business_ugc_ad_write` was called with the printed payload (asset record is truthful).
- [ ] No state was written outside `product/`.

## Rules

1. Keep work **business-scoped** (one business per run).
2. **Do not fake side effects** — provider calls, file outputs, or the asset record. The
   scripts only claim files they actually wrote; the agent records the asset via the tool.
3. Use the canonical tool (`business_ugc_ad_write`) and the canonical path
   (`product/ugc-ads/<slug>/`) — never parallel state.
4. Keep the **script** and **production** layers separate (see Overview).
5. Credentials come from env/`.env` only; never hardcode keys.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `OPENAI_API_KEY/FAL_KEY is not set` | Export it or add to a local `.env`; never hardcode. |
| `ffmpeg/ffprobe not found` | Install ffmpeg and ensure both are on `PATH`. |
| Clip flagged "speech > cap, clamped" | A single beat exceeds `--max-clip`; split that beat in the script. |
| Person/voice changes between clips | Ensure continuity ran (clip N from last frame of N-1); keep one persona. |
| Tight shots look blurry | You upscaled — deliver at the native-crop size or regen at higher res. |
| Cuts feel like pauses | Use `--jumpcuts` (drops inter-phrase silence so position pops). |
| `--jumpcuts` made no cuts | No phrase silences detected; tune `SILENCE_DB`/`SILENCE_DUR` env or rely on stitch-seam cuts. |
| Live deps missing | `--dry-run` needs none; the full build needs `httpx` + `fal-client`. |
