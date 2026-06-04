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

## Workhorse formats for fast testing

If you need sharp click-through tests instead of "nice brand art", bias toward workhorse static
formats that map cleanly to high-volume paid-social patterns:

- **Headline + lifestyle outcome:** the message sells the life/result, not the interface.
- **Transformation / before-after:** especially strong when the end state is visible.
- **Offer / headline-led banners:** one dominant claim or offer, obvious hierarchy, little ornament.
- **Demo / UI proof:** show the product doing the job, not merely posing.
- **Testimonial / review cards:** best when the proof is real and the quote is short.
- **Before/after or old way/new way:** lets the viewer infer the conclusion quickly.
- **Listicle / myth-vs-fact / reasons-why:** scannable, useful, and naturally specific.
- **Comment / criticism reply:** ideal for skeptical audiences because it feels like a real objection being answered.

For cold traffic, **start with outcome, relief, or transformation** unless the UI itself is
the proof. UI-led statics are a lane, not the default.

## Boldness levers

If the concept needs more stop-scroll force, increase one or two of these:

- **type dominance:** one huge line, not three medium ones
- **contrast:** make the old way visibly worse and the new way visibly cleaner, stronger, or more desirable
- **asymmetry:** let one side overwhelm or one object dominate
- **accusation / warning framing:** visuals that support "stop doing this" or "this is the problem"
- **cost visibility:** make wasted time, clutter, stress, or confusion obvious at a glance

Do **not** turn up everything at once. One forceful contrast usually beats chaotic intensity.

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

### outcome_lifestyle
- **AR:** 4:5 / 1:1 · **Focal:** the person living in the after-state · **Overlay:** one big outcome line
- Show the improved life-state the product creates: calmer, faster, lighter, stronger, more put-together, more confident. The product may be implied or subtle, but the *result* is unmistakable.
- *Skeleton:* "One person or team in the desired after-state of `<outcome>`, natural or editorial lifestyle photography, clear emotional signal, clean space for one dominant headline, no interface required."
- Pairs: transformation, pain_agitation, founder_confession, community_native.

### chaos_to_calm_split
- **AR:** 4:5 / 1:1 · **Focal:** the contrast line between messy and resolved · **Overlay:** a short transformation statement
- Split one world into before vs. after: clutter vs. clarity, stress vs. control, scattered vs. submit-ready. Works well for abstract B2B or workflow outcomes that are easier to *feel* than to explain.
- *Skeleton:* "Left side shows `<chaos state>`, right side shows `<resolved state>`, same setting transformed, clean center divider or visual transition, strong figure-ground contrast, little or no product UI."
- Pairs: before_after, transformation, comparison, pain_agitation.

### ui_screenshot  *(self-product UI)*
- **AR:** 4:5 / 9:16 · **Focal:** the key number/result on screen · **Overlay:** one callout
- Render the product's own interface, realistic device chrome optional, one metric highlighted. Numbers must be representative; label illustrative if not real.
- *Skeleton:* "Clean mobile app UI for `<product>`, one highlighted result '`<metric>`', realistic but uncluttered, single accent color `<brand hex>`, soft drop shadow on a plain background."
- Pairs: fake_ui, product_proof, objection_handling.
- Best for: warmer traffic, proof-led creative, or products where the interface itself is genuinely the "wow."
- Avoid as the default first concept when the ad really needs to sell a transformation, lifestyle upgrade, or emotional payoff.

### device_in_hand
- **AR:** 4:5 / 9:16 · **Focal:** screen · **Overlay:** top
- One hand holding one phone showing the UI, candid, real-world background blurred.
- Pairs: fake_ui, product_proof.

### native_social_post
- **AR:** 4:5 / 9:16 · **Focal:** the post text · **Overlay:** is the post itself
- A platform-native card (community post, forum-style note, social caption). Structure mimics the surface; copy reads like a real human, clearly representative.
- *Skeleton:* "A clean community-post card: generic community label, bold title '`<hook>`', two lines of body text, neutral UI, no real usernames."
- Pairs: community_native, founder_confession, contrarian.

### comment_reply_card
- **AR:** 4:5 / 9:16 · **Focal:** the objection and its answer · **Overlay:** the reply itself
- Simulated comment or criticism at top, concise brand or creator reply below, with one proof element highlighted.
- *Skeleton:* "Reply-to-comment card. Top: skeptical comment '`<objection>`'. Bottom: concise answer showing `<proof>` with a calm, matter-of-fact tone. Generic UI, no real usernames unless rights-cleared."
- Pairs: confession, warning, social_proof, objection_handling, contrarian.

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

### listicle_card
- **AR:** 1:1 / 4:5 · **Focal:** the headline + three scannable bullets · **Overlay:** the list itself
- A mini-landing-page static: one bold claim on top, 3 short bullets beneath, and the product or proof anchor at the bottom.
- *Skeleton:* "Big headline '`<hook>`' at top, exactly three short bullets naming mistakes, reasons, or benefits, clean product/proof anchor at bottom, strong hierarchy, mobile-first sizing."
- Pairs: warning, reasons_why, product_proof, objection_handling.

### myth_vs_fact / faq_card
- **AR:** 1:1 / 4:5 · **Focal:** the correction · **Overlay:** "Myth/Fact" labels
- Pairs: objection_handling, contrarian.

### stat_card / big_statement_card
- **AR:** 1:1 / 4:5 · **Focal:** the number or claim · **Overlay:** the stat itself
- Bold typographic card, one number or one sentence dominates, minimal imagery.
- *Skeleton:* "Bold typographic poster, single huge figure '`<stat>`', high contrast, one accent color, lots of negative space."
- Pairs: contrarian, social_proof, product_proof.

### offer_first_banner
- **AR:** 1:1 / 4:5 · **Focal:** the offer or economic claim · **Overlay:** the offer itself
- A direct-response banner where the first thing read is the offer, price anchor, or deadline; product is secondary but visible.
- *Skeleton:* "Dominant offer banner '`<offer>`' or price anchor '`<price contrast>`', one secondary proof chip, clean product or UI anchor, high contrast, almost no decorative clutter."
- Pairs: urgency_offer, price_anchor, comparison, product_proof.

### opinion_poster
- **AR:** 1:1 / 4:5 · **Focal:** the statement itself · **Overlay:** the statement is the ad
- A confrontational typographic static with one dominant claim and minimal supporting imagery. Best when the ad needs to hit with a bang.
- *Skeleton:* "Huge bold statement '`<hook>`' dominating the frame, one supporting visual metaphor or tiny proof chip, extreme hierarchy, high contrast, unmistakable point of view."
- Pairs: contrarian, warning, shocking_statement, comparison.

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

Always reserve clear negative space where `layout.overlay_position` says the text goes, and
keep it inside the placement's safe zone (see `platform-specs.md`).
