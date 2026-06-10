# Static Ad Creative Generator

A self-contained, backend-agnostic skill that turns a product/company input into
**high-performance static image ad creative** for **Meta (Facebook + Instagram)**, then
optionally renders variants with **OpenAI `gpt-image-2`**.

It is built for **performance advertising**, not generic image generation. Marketing
strategy is decided first as a strict JSON **ad spec**; the image model only *renders* an
already-approved strategy. It never invents the angle, hook, audience, or proof.

> **Honesty rule:** this skill does **not** generate fake testimonials, fake reviews/ratings,
> fake "as seen in" logos, fake third-party screenshots, or fake endorsements — unless they
> are explicitly marked as fictional placeholder creative. See
> [`references/policy-checks.md`](references/policy-checks.md).

## Pipeline

```
1 Intake    →  2 Strategy/Angles  →  3 Ad Spec (JSON, validated)  →  4 Prompt Compiler
                                                                            ↓
7 Output bundle  ←  6 QA (9 checks + policy gate)  ←  5 Image Generation (gpt-image-2)
```

Each stage is a separate artifact. Strategy, prompt, layout, and generation are decoupled so
the backend is swappable and the strategy is reviewable before you spend on generation.

## Install

```bash
pip install -r scripts/requirements.txt      # openai (required for real gen), Pillow + jsonschema (optional)
export OPENAI_API_KEY=sk-...                  # never hardcode  — OR pass --api-key-file ~/.openai_key
```

The key can be supplied two ways: `OPENAI_API_KEY` in the environment, or `--api-key-file PATH`
(read at runtime and handed straight to the client, never exported to an env var).

- **Python 3.9+.**
- `openai` is needed for generation. `Pillow` enables exact `--crop`. `jsonschema` improves
  validation (a zero-dependency fallback runs without it).

## Quickstart

```bash
# 1) validate an ad spec
python scripts/validate_spec.py examples/example-spec.json

# 2) preview the compiled image prompt
python scripts/compile_prompt.py examples/example-spec.json

# 3) generate one creative
python scripts/generate_image.py examples/example-spec.json -o output/ --crop

# 4) batch a test matrix of angles/placements
python scripts/batch_generate.py examples/example-batch.json -o output/ --crop
```

Each creative produces a full bundle: `*.png`, `*.spec.json`, `*.prompt.txt`, `*.qa.json`,
`*.output.json`. Batches also write `manifest.json`. Those generated output directories are
local artifacts and are intentionally gitignored.

## Authoring your own ads

1. Fill [`templates/creative-brief.template.md`](templates/creative-brief.template.md) from
   the product input. Don't invent facts/proof.
2. Pick 3–5 angles from [`references/angle-taxonomy.md`](references/angle-taxonomy.md) by
   audience awareness + strongest real proof.
3. For each angle, write a spec from
   [`templates/ad-spec.template.json`](templates/ad-spec.template.json), conforming to
   [`templates/ad-spec.schema.json`](templates/ad-spec.schema.json).
4. `validate_spec.py` until **0 errors**; resolve warnings.
5. `generate_image.py` / `batch_generate.py`, then finish QA against
   [`references/qa-rubric.md`](references/qa-rubric.md).

## Aspect ratios & sizes

`aspect_ratio` is a free-form `W:H` — **any ratio from 1:3 to 3:1** is supported and sized
automatically (no fixed list). Sizes are computed in [`scripts/backends.py`](scripts/backends.py)
(`resolve_size`): the short edge is fixed to 1024px and the long edge scales to the ratio,
rounded to a multiple of 16. `--crop` (needs Pillow) trims to the exact frame.

| `aspect_ratio` | Use | `gpt-image-2` size | Exact via `--crop` |
| --- | --- | --- | --- |
| `1:1` | feed | `1024x1024` | already exact |
| `4:5` | feed (best mobile) | `1024x1280` | already exact |
| `1.91:1` | feed / link / desktop | `1952x1024` (~1.906) | crops to 1.91 |
| `9:16` | story / reels | `1024x1824` (~0.561) | crops to 0.5625 |

Render one creative at every size a placement needs in a single command:

```bash
python scripts/generate_image.py examples/example-spec.json -o output/ --crop \
  --aspect-ratio 1:1,4:5,1.91:1,9:16 --api-key-file ~/.openai_key
```

The deprecated `gpt-image-1` snaps any ratio to its nearest of three fixed sizes (then `--crop`).

## Image backend

The only place that talks to a generation API is `scripts/backends.py`.

- **Change model:** `export OPENAI_IMAGE_MODEL=gpt-image-1` (or any future model id).
- **New backend:** subclass `ImageBackend`, implement `generate(...)`, and register it in
  `get_backend(...)`. Nothing upstream (intake → strategy → spec → prompt → QA) changes.

## Layout

```
static-ad-creative-generator/
├── SKILL.md                      # skill entry point (workflow, rules, troubleshooting)
├── README.md                     # this file
├── references/
│   ├── angle-taxonomy.md         # 14 ad angles + how to choose + variant waves
│   ├── visual-templates.md       # composition templates + art-direction + anti-artifact rules
│   ├── platform-specs.md         # Meta placements, safe zones, copy limits, size map
│   ├── policy-checks.md          # honesty rules + platform policy + lint signals
│   ├── prompt-compiler-rules.md  # the compiler contract
│   └── qa-rubric.md              # the 9-check QA rubric
├── templates/
│   ├── ad-spec.schema.json       # JSON Schema for one ad spec (source of truth)
│   ├── ad-spec.template.json     # blank spec skeleton
│   └── creative-brief.template.md# intake brief
├── scripts/
│   ├── validate_spec.py          # schema + performance/policy lint
│   ├── compile_prompt.py         # spec → art-directed prompt (deterministic)
│   ├── backends.py               # backend adapter (OpenAI default), size map, crop
│   ├── generate_image.py         # single-creative pipeline
│   ├── batch_generate.py         # batch + manifest
│   ├── qa_check.py               # QA report scaffolder
│   └── requirements.txt
└── examples/
    ├── example-input.json        # a filled creative brief (fictional product "Tally")
    ├── example-spec.json         # one complete ad spec
    └── example-batch.json        # 2 specs across angles
```

## Notes

- **Self-contained:** no dependency on Creatify, Luma, Higgsfield, Canva, Midjourney, Runway,
  or any third-party creative platform/API. The reference repos that inspired the methodology
  were used as patterns only.
- **Not legal advice:** platform policies and exact spec numbers change — verify in
  [`references/platform-specs.md`](references/platform-specs.md) and the platforms' current
  policy pages before a paid launch.
- **License:** MIT.
