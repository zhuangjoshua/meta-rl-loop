# Angle Taxonomy

An **angle** is a distinct *reason to stop scrolling and click* — the motivation, not the
layout. One product yields many angles. Pick the angle from the audience's **awareness
level** and **strongest available proof**, then choose a visual template to express it.

> Strategy rule: the angle, hook, promise, objection, and proof are decided in the **ad
> spec** (`strategy` block). The visual template and the image prompt only *express* an
> angle that has already been chosen. The image model never selects the angle.

> Angle is not enough. After picking an angle, pick a **hook tactic**, **creative mechanic**,
> and **claim support** from `hook-strategy.md`. The angle says *what lane you're in*; the
> hook tactic says *why someone stops*.

## How to choose an angle

1. **Match awareness.** Unaware/problem-aware audiences need the *problem* dramatized
   (pain agitation, before/after, contrarian) and usually respond best to visible
   *outcomes* or *transformations*. Product-aware/most-aware audiences need *proof and
   offer* (product proof, social proof, comparison, urgency).
2. **Lead with your strongest real proof.** If you have a demo → product proof / fake-UI.
   Real reviews → social proof / testimonial. A founder story → founder confession. A hard
   number → stat. No proof yet → visual metaphor or pain agitation. But on cold traffic,
   prefer proof of the **after-state** over a pure feature walkthrough when possible.
3. **Be native to the surface.** Meta feed rewards UGC, before/after, product proof, and
   community-style social cards; Story/Reels reward full-bleed vertical with one big idea.
4. **One angle per creative.** Mixing angles dilutes the hook. Produce *variants* across
   angles instead of stacking them.

## Outcome-first by awareness

- **Unaware / problem-aware:** lead with pain, contrast, relief, transformation, identity, or
  status change. UI is rarely the first thing that earns the click.
- **Solution-aware:** outcome-first still works, but you can start mixing in more explicit
  proof, comparison, or mechanism.
- **Product-aware / most-aware:** this is where feature-proof, offer, UI, objection handling,
  and detailed comparisons become much more appropriate.

## Angle -> hook tactic fit

Common pairings that keep statics clicky instead of generic:

- **pain_agitation:** warning, relatability, shocking_statement
- **before_after / transformation:** contrast, curiosity, statistic
- **fake_ui / product_proof:** contrarian, comparison, reasons_why, statistic
- **founder_confession:** confession, direct_address
- **community_native / imessage:** overheard-conversation style curiosity, confession, social_proof
- **social_proof:** social_proof, statistic, confession-from-reluctance
- **comparison:** contrast, contrarian, warning, price_anchor
- **contrarian:** contrarian, myth_busting, shocking_statement
- **objection_handling:** warning, reasons_why, confession
- **urgency_offer:** urgency, offer_only, price_anchor

If the current hook still sounds like a category description, the angle is probably right but the
hook tactic is wrong or under-specified.

## The 14 core angles

Each entry: **id** (use in `strategy.angle`) · best awareness · default `proof_type` ·
suggested visual templates · platform fit · example hook · notes.

### 1. pain_agitation
- **Awareness:** unaware → problem_aware
- **Proof:** visual_metaphor / demo
- **Templates:** pain_metaphor, before_after_split, hero_product
- **Platforms:** Meta feed/story
- **Hook:** "Still doing `<painful manual task>` by hand?"
- **Notes:** Dramatize the *cost* of the status quo, then relieve it. Keep the relief on-brand. **Policy:** never target a personal attribute — agitate the *situation*, not the person ("Tired of messy spreadsheets" ✅, not "Struggling with debt?" ❌). See `policy-checks.md`.

### 2. before_after
- **Awareness:** problem_aware → solution_aware
- **Proof:** comparison / demo
- **Templates:** before_after_split, transformation_grid
- **Platforms:** Meta feed (1:1, 4:5)
- **Hook:** "Before vs. after `<product>`."
- **Notes:** Powerful but the most policy-sensitive angle for health, weight, finance, and cosmetics. Show *product state* (cluttered → clean dashboard) rather than implied bodily/financial outcomes unless the result is real, typical, and disclosed.

### 3. fake_ui  *(self-product UI mock — not third-party impersonation)*
- **Awareness:** solution_aware → product_aware
- **Proof:** demo
- **Templates:** ui_screenshot, device_in_hand
- **Platforms:** Meta feed/story
- **Hook:** "This is the screen that found me `<result>`."
- **Notes:** Render **your own** product UI as the hero. The word "fake" here means *art-directed mock of your real interface*, never a forged screenshot of a third party, a fabricated news article, or a non-functional control implying interactivity the static image cannot deliver. Numbers shown must be representative; mark non-real numbers as illustrative. Best when the audience is already somewhat bought into the category or when the interface itself is the proof. See `policy-checks.md` → misleading UI.

### 4. founder_confession
- **Awareness:** problem_aware → solution_aware
- **Proof:** founder
- **Templates:** founder_selfie, handwritten_note, native_social_post
- **Platforms:** Meta feed/story
- **Hook:** "I built `<product>` because I was sick of `<pain>`."
- **Notes:** Candid, first-person, imperfect. Trust comes from specificity and humility, not polish. The person must be the real founder or an explicitly fictional/representative persona — never a fabricated named individual implying a real endorsement.

### 5. community_native
- **Awareness:** problem_aware → product_aware
- **Proof:** social_proof / testimonial
- **Templates:** native_social_post (community post card)
- **Platforms:** Meta feed/story
- **Hook:** "The post I'd send any friend who keeps paying for `<pain>`"
- **Notes:** Mimic a plainspoken community post structure: label, title, short body, believable cadence. Content must read like a real human wrote it. **Do not fabricate quotes attributed to real people or communities as if they were authentic UGC** — use clearly representative copy or real, sourced UGC with rights. See `policy-checks.md`.

### 6. imessage  *(text-conversation format)*
- **Awareness:** problem_aware → product_aware
- **Proof:** social_proof
- **Templates:** text_conversation
- **Platforms:** Meta story/reels/feed
- **Hook:** A two-bubble exchange: "bro how did you `<result>`?" / "`<product>`. took 5 min."
- **Notes:** Generic message UI (not a forged iOS screenshot of a named person). Bubbles are short, lowercase, real-feeling. Represented conversation must be marked representative, not passed off as a real intercepted chat.

### 7. product_proof
- **Awareness:** solution_aware → product_aware
- **Proof:** demo / stat
- **Templates:** hero_product, ui_screenshot, result_closeup
- **Platforms:** Meta feed/story
- **Hook:** "Here's exactly what `<product>` does."
- **Notes:** Show the product doing the job and the concrete output. Best when the product *is* visually compelling or the audience already understands why the mechanism matters. For colder audiences, test a transformation or outcome-led variant alongside this; the UI should support the result, not replace it.

### 8. social_proof
- **Awareness:** solution_aware → most_aware
- **Proof:** social_proof / testimonial
- **Templates:** testimonial_card, rating_strip, logo_wall, review_screenshot
- **Platforms:** Meta feed/story
- **Hook:** "Why 12,000 `<persona>` switched to `<product>`."
- **Notes:** Reviews, ratings, counts, named customers/logos — **all must be real and rights-cleared.** A fictional sample is allowed only when visibly labeled illustrative. Never invent testimonials.

### 9. comparison
- **Awareness:** solution_aware → product_aware
- **Proof:** comparison
- **Templates:** comparison_table, this_vs_that, before_after_split
- **Platforms:** Meta feed/story
- **Hook:** "`<old way>` vs. `<product>`."
- **Notes:** "Us vs. the old way" is safer and stronger than naming a competitor. If naming a competitor, claims must be true, current, and substantiable; do not use competitor logos you lack rights to.

### 10. contrarian
- **Awareness:** unaware → solution_aware
- **Proof:** stat / visual_metaphor
- **Templates:** big_statement_card, native_social_post
- **Platforms:** Meta feed/story
- **Hook:** "`<Common advice>` is why you still `<pain>`."
- **Notes:** Challenge a belief the audience holds, then redirect to your mechanism. Earns attention; back the claim with a real reason so it doesn't read as clickbait.

### 11. meme_native
- **Awareness:** problem_aware
- **Proof:** visual_metaphor
- **Templates:** meme_format, big_statement_card
- **Platforms:** Meta feed/story
- **Hook:** Relatable two-panel "expectation vs. reality" of the pain.
- **Notes:** Humor must map to a real product truth or it converts poorly. Keep it tasteful and on-brand; avoid borrowed-format meme templates you can't license.

### 12. objection_handling
- **Awareness:** product_aware → most_aware
- **Proof:** demo / stat / testimonial
- **Templates:** myth_vs_fact, faq_card, before_after_split
- **Platforms:** Meta feed/story
- **Hook:** "'`<the objection>`?' — here's the 10-second answer."
- **Notes:** Name the #1 doubt verbatim and dismantle it with proof. Maps 1:1 to `strategy.objection_addressed`.

### 13. urgency_offer
- **Awareness:** product_aware → most_aware
- **Proof:** stat
- **Templates:** offer_card, hero_product_with_badge
- **Platforms:** Meta feed/story
- **Hook:** "`<Offer>` ends `<date>`."
- **Notes:** Only use *real* deadlines/scarcity. Fake countdowns and false scarcity are both a conversion killer and a policy risk. Make the offer the focal point.

### 14. transformation
- **Awareness:** problem_aware → solution_aware
- **Proof:** demo / testimonial
- **Templates:** transformation_grid, before_after_split, journey_strip
- **Platforms:** Meta feed/story
- **Hook:** "From `<bad state>` to `<good state>` in `<timeframe>`."
- **Notes:** Show the arc, not just the endpoint. This is often stronger than UI-led creative for cold traffic because it sells the after-state people actually want. Same health/finance/cosmetic policy caution as before_after.

## Angle → spec field mapping

| Spec field | What the angle decides |
| --- | --- |
| `strategy.angle` | The angle id above |
| `strategy.hook` | The angle's hook, rewritten in voice-of-customer |
| `strategy.proof_type` | The angle's default proof (override if you have stronger real proof) |
| `strategy.objection_addressed` | The doubt this angle is best positioned to kill |
| `visual.template` | A template from `visual-templates.md` compatible with the angle |
| `audience.awareness_level` | Should fall inside the angle's awareness band |

## Variant strategy (batch production)

Produce in waves so a test isolates the angle, then the execution:
- **Wave 1 — core angles:** 3–5 different angles, one creative each.
- **Wave 2 — tactic variants:** take the 1–2 winning angles, vary the hook tactic or creative
  mechanic (for example `contrarian` vs. `warning`, or `reframe` vs.
  `contrast_without_comment`) while keeping the core promise fixed.
- **Wave 3 — execution variants:** keep the winning angle+tactic, then vary hook wording,
  visual template, overlay placement, and proof emphasis.
- **Wave 4 — wildcards:** a bold new angle, a more aggressive tactic, or a category-specific
  pattern discovered from competitor or ad-library research.

Log which angle each `creative_id` tests so winners and losers are attributable.
