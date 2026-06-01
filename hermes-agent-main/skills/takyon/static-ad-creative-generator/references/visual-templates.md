# Visual Templates

A **template** is a reusable composition that expresses an angle. The template id goes in
`visual.template`. Templates define *layout, focal point, and where text lives* — they do
not decide strategy (that is already fixed in `strategy`).

## Art-direction framework (SSCLP)

Every `visual` block should answer five things, in this order of importance:

1. **Scene** — what is literally happening (`visual.scene`).
2. **Subject** — the one hero element the eye lands on (`visual.subject` + `visual.focal_point`).
3. **Composition** — shot type, framing, and the *negative space reserved for overlay text* (`visual.composition`).
4. **Light** — direction, quality, mood (`visual.lighting`).
5. **Polish** — `visual.style` (aesthetic) + `product.representation` (how the product shows up).

## Realism / anti-artifact rules (apply to every prompt)

These keep image-model output clean and on-brief:

- **Spatial explicitness.** State *where* each element sits ("packshot lower-third, text upper-third").
- **Entity limit.** Few props. Crowded scenes garble. Prefer ≤ 3 distinct objects.
- **Prefer positives.** Describe what you *want*; route exclusions to `prompting.negative_constraints`.
- **Real-world anchor.** Reference real photography/render styles ("shot on 50mm, soft window light").
- **Specified quantities.** Exact counts ("one phone, one hand"), never "some" or "several".
- **Minimize baked text.** Image models misspell. Keep `copy.overlay_text` short, in quotes,
  and few words; plan to add critical legal/branding text in post if legibility is essential.

## Template library

Each entry: **id** (use in `visual.template`) · default aspect ratio · focal point ·
overlay placement · composition note · prompt skeleton · pairs-with angles.

### hero_product
- **AR:** 1:1 / 4:5 · **Focal:** the product · **Overlay:** bottom or top third
- Clean studio or contextual surface, product centered or rule-of-thirds, generous negative space for text.
- *Skeleton:* "`<product>` as hero on `<surface>`, `<lighting>`, centered with clean negative space in the `<top/bottom>` third for overlay text, `<style>`."
- Pairs: product_proof, urgency_offer, pain_agitation.

### before_after_split
- **AR:** 1:1 / 4:5 · **Focal:** the contrast line · **Overlay:** label each half
- Split frame (vertical or diagonal), clearly labeled "before"/"after", consistent framing both sides so only the meaningful thing changes.
- *Skeleton:* "Split-frame before/after. Left: `<bad state>`. Right: `<good state>`. Identical camera and lighting both halves, clear center divider, small labels."
- Pairs: before_after, transformation, comparison.

### transformation_grid
- **AR:** 1:1 / 4:5 · **Focal:** progression · **Overlay:** stage captions
- 2–4 panel grid showing an arc over time/steps.
- Pairs: transformation, product_proof.

### ui_screenshot  *(self-product UI)*
- **AR:** 4:5 / 9:16 · **Focal:** the key number/result on screen · **Overlay:** one callout
- Render the product's own interface, realistic device chrome optional, one metric highlighted. Numbers must be representative; label illustrative if not real.
- *Skeleton:* "Clean mobile app UI for `<product>`, one highlighted result '`<metric>`', realistic but uncluttered, single accent color `<brand hex>`, soft drop shadow on a plain background."
- Pairs: fake_ui, product_proof, objection_handling.

### device_in_hand
- **AR:** 4:5 / 9:16 · **Focal:** screen · **Overlay:** top
- One hand holding one phone showing the UI, candid, real-world background blurred.
- Pairs: fake_ui, product_proof.

### native_social_post
- **AR:** 4:5 / 9:16 · **Focal:** the post text · **Overlay:** is the post itself
- A platform-native card (Reddit thread, forum post, social caption). Structure mimics the platform; copy reads like a real human, clearly representative.
- *Skeleton:* "A Reddit-style post card: subreddit 'r/`<topic>`', upvote arrow, bold title '`<hook>`', two lines of body text, neutral UI, no real usernames."
- Pairs: reddit_native, founder_confession, contrarian.

### text_conversation
- **AR:** 9:16 / 4:5 · **Focal:** the punchline bubble · **Overlay:** none (text is in-scene)
- Generic messaging UI, 2–4 short bubbles, lowercase casual tone, last bubble lands the benefit.
- *Skeleton:* "Generic phone messaging screen, 3 short chat bubbles, casual tone, last incoming bubble reads '`<benefit line>`', plain UI, no real contact name."
- Pairs: imessage, social_proof.

### testimonial_card  *(real or clearly-illustrative)*
- **AR:** 1:1 / 4:5 · **Focal:** the quote · **Overlay:** quote + attribution
- Quote-forward card, 5-star strip optional, small attribution. **Quote and attribution must be real and rights-cleared, or visibly labeled illustrative.**
- Pairs: social_proof, objection_handling.

### rating_strip / logo_wall
- **AR:** 1:1 / 4:5 · **Focal:** the count or stars · **Overlay:** the number
- Star rating + count, or a wall of *real, licensed* customer logos.
- Pairs: social_proof.

### review_screenshot
- **AR:** 4:5 · **Focal:** the review text · **Overlay:** light highlight
- App-store / marketplace review styling. Real review text only, or labeled illustrative.
- Pairs: social_proof, objection_handling.

### comparison_table / this_vs_that
- **AR:** 1:1 / 4:5 · **Focal:** the winning column · **Overlay:** column headers + checks
- Two columns ("old way" vs `<product>`), check/cross marks, your column visually favored.
- Pairs: comparison, objection_handling.

### myth_vs_fact / faq_card
- **AR:** 1:1 / 4:5 · **Focal:** the correction · **Overlay:** "Myth/Fact" labels
- Pairs: objection_handling, contrarian.

### stat_card / big_statement_card
- **AR:** 1:1 / 4:5 · **Focal:** the number or claim · **Overlay:** the stat itself
- Bold typographic card, one number or one sentence dominates, minimal imagery.
- *Skeleton:* "Bold typographic poster, single huge figure '`<stat>`', high contrast, one accent color, lots of negative space."
- Pairs: contrarian, social_proof, product_proof.

### founder_selfie / handwritten_note
- **AR:** 4:5 / 9:16 · **Focal:** the face or the note · **Overlay:** caption bottom
- Candid first-person photo or a handwritten-style note. Imperfect = trustworthy.
- Pairs: founder_confession.

### offer_card / hero_product_with_badge
- **AR:** 1:1 / 4:5 · **Focal:** the offer · **Overlay:** offer + deadline
- Product with a clear offer badge; deadline must be real.
- Pairs: urgency_offer.

### result_closeup
- **AR:** 1:1 / 4:5 · **Focal:** the tangible result · **Overlay:** one label
- Macro on the outcome the product produces.
- Pairs: product_proof, transformation.

### pain_metaphor
- **AR:** 1:1 / 4:5 · **Focal:** the visual metaphor of the pain · **Overlay:** the agitation line
- A single strong metaphor image for the problem (tangled cables = chaos). Agitate the situation, not the person.
- Pairs: pain_agitation, contrarian.

### meme_format
- **AR:** 1:1 / 4:5 · **Focal:** the relatable beat · **Overlay:** top/bottom caption
- Two-panel or captioned-image humor mapped to a real product truth. Use original art, not licensed meme stills.
- Pairs: meme_native.

## Template → aspect ratio defaults

| Placement | Default AR | Templates that shine |
| --- | --- | --- |
| Meta feed | 1:1 or 4:5 | hero_product, before_after_split, comparison_table, stat_card |
| Meta story/reels | 9:16 | device_in_hand, text_conversation, founder_selfie, native_social_post |
| Reddit feed | 1:1 or 4:5 | native_social_post, stat_card, comparison_table |
| Reddit comments | 1:1 | stat_card, review_screenshot |

Always reserve clear negative space where `layout.overlay_position` says the text goes, and
keep it inside the placement's safe zone (see `platform-specs.md`).
