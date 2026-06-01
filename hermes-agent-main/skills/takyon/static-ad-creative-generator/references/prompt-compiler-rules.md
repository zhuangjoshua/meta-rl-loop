# Prompt Compiler Rules

The prompt compiler (`scripts/compile_prompt.py`) **translates** an already-approved ad spec
into an art-directed brief for the image model. It is a deterministic function of the spec.

> **The compiler never invents strategy.** It does not choose the angle, hook, audience, or
> proof. Those are fixed in `strategy` / `audience`. The compiler only renders the spec's
> visual + copy + layout + constraint fields into image-model instructions. If a strategic
> field is missing, that is a spec bug to fix upstream — the compiler must not paper over it.

## Required output sections (fixed order)

The compiled prompt is a labeled brief, not a comma-soup prompt. It always contains these
sections, in this order, so every required component is present:

1. **OBJECTIVE** — from `goal`, `audience.persona`, `strategy.angle`, `strategy.hook`,
   `strategy.promise`. States what the ad must accomplish, then: *"render this strategy, do
   not change it."*
2. **PLATFORM & PLACEMENT** — from `platform`, `placement`; instruct "look native to this surface."
3. **FORMAT** — from `aspect_ratio` and the resolved model size (e.g. `4:5 (1024x1280)`).
4. **VISUAL STYLE** — from `visual.style`.
5. **SCENE & SUBJECT** — the art-directed core. If `prompting.final_image_prompt` is
   non-empty, use it verbatim as the scene description; otherwise synthesize from
   `visual.scene`, `visual.subject`, `visual.focal_point`. Always name the focal point.
6. **COMPOSITION** — from `visual.composition`; append the negative-space instruction for
   `layout.overlay_position`, and the safe-zone note from `layout.safe_zones`.
7. **LIGHTING & BACKGROUND** — from `visual.lighting`, `visual.background`, `visual.props`
   (props rendered as an explicit small count).
8. **PRODUCT** — from `product.representation` and `product.must_show`.
9. **OVERLAY TEXT** — render the exact words of `copy.overlay_text` in quotes, with "spell
   correctly, keep short, legible at thumbnail size." If `overlay_text` is empty, instruct
   "no baked-in text."
10. **TYPOGRAPHY & LAYOUT** — from `layout.typography`, `layout.overlay_position`,
    `layout.logo_position`.
11. **BRAND CONSTRAINTS** — from `product.must_show` (and any brand colors named there).
12. **REALISM** — fixed anti-artifact line: photographic real-world rendering, exact
    quantities, no garbled/misspelled text, no extra fingers/limbs.
13. **AVOID** — the union of `product.must_not_show` and `prompting.negative_constraints`.

## Translation rules per field

- **Exact text in quotes.** Any words that must appear in the image (`copy.overlay_text`) are
  quoted so the model renders them literally. Never paraphrase overlay copy.
- **Positive phrasing in the body; exclusions in AVOID.** Per the anti-artifact rules,
  describe what to include in sections 1–12 and route "don't" items to **AVOID** only.
- **Explicit space + safe zones.** Composition must say where text negative-space lives
  (driven by `layout.overlay_position`) and what to keep clear (`layout.safe_zones`).
- **Entity limits.** Props are listed with an explicit small count; if `visual.props` is long,
  the compiler keeps the first few and notes "minimal props."
- **Reference images.** If `prompting.reference_images` is non-empty, the compiler appends a
  note that brand/reference images are provided, and the generator routes to the image
  **edits** endpoint so the model conditions on them.
- **No strategy words leak into pixels.** The OBJECTIVE section is guidance to the model's
  art direction, not text to render. Only `copy.overlay_text` is meant to appear as pixels.

## Determinism & idempotency

- Same spec in ⇒ same prompt out. No randomness, no time, no external calls.
- The compiler may be run standalone (`python scripts/compile_prompt.py spec.json`) to print
  the prompt, or imported by the generator. Both paths produce identical text.

## Worked example (abridged)

Spec (excerpt): angle `product_proof`, 4:5 Meta feed, overlay "Found $312/mo I forgot about",
template `ui_screenshot`, overlay bottom.

Compiled (excerpt):

```
ART-DIRECTION BRIEF — STATIC AD CREATIVE

OBJECTIVE: A clicks-focused ad for "busy professionals who feel money leaks out of their
account." Express the "product_proof" angle. Hook: "See exactly where your money leaks."
Promise: "find forgotten subscriptions in 60 seconds." Render this strategy faithfully; do
not change the marketing idea.

PLATFORM & PLACEMENT: meta / feed. Make it look native to that surface, not like a polished
stock ad.

FORMAT: 4:5 aspect ratio (1024x1280), vertical.

VISUAL STYLE: clean editorial product UI, single accent color, soft shadows.

SCENE & SUBJECT: A realistic mobile app dashboard for the product, one highlighted figure
"$312/mo" on screen. Focal point: the highlighted figure.

COMPOSITION: app UI centered with breathing room; reserve clean negative space in the bottom
third for overlay text; keep key elements out of the bottom ~20% safe zone.

... LIGHTING & BACKGROUND ... PRODUCT ...

OVERLAY TEXT: render exactly the words "Found $312/mo I forgot about" in the bottom third.
Spell correctly, keep it short, legible at thumbnail size.

TYPOGRAPHY & LAYOUT: bold condensed sans, high contrast; overlay at bottom; logo bottom-left.

BRAND CONSTRAINTS: show the product logo and brand accent color.

REALISM: photographic, real-world rendering, exact quantities, no garbled or misspelled text.

AVOID: fabricated third-party logos, invented endorsements, competitor marks, cluttered
background, extra fingers, garbled text.
```

Keep this section list synchronized with `scripts/compile_prompt.py` (`SECTION_ORDER`).
