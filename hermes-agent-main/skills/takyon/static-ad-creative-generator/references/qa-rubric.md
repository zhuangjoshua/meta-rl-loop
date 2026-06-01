# QA Rubric

Run QA **twice**: once on the spec **before** generation (catches strategy/policy problems
cheaply) and once on the rendered image **after** generation (catches execution problems).
`scripts/qa_check.py` scaffolds a QA report JSON from a spec; a human or agent fills the
post-generation visual verdicts.

## The 9 checks

Each check returns `pass` / `warn` / `fail` plus a one-line note.

1. **Hook readable in feed?** Is the core idea legible at thumbnail size, at a glance,
   without zooming? (overlay text short, high contrast, focal point obvious)
2. **Communicates product/category in < 2s?** Could a stranger say what this is and what
   it's for within two seconds?
3. **Native to the platform?** Does it look like content on that surface, or like a stocky
   banner ad? (UGC/native > corporate)
4. **Overlay text short enough?** Within the platform's readable limit; one idea, not a
   paragraph baked into the image.
5. **Not too corporate/generic?** Avoids stock clichés (handshakes, faceless suits, generic
   gradients). Has a specific, ownable idea.
6. **No fake claims / personal-attribute / misleading-UI / invented-proof violations?**
   Clears the pre-flight checklist in `policy-checks.md`. This is a **hard gate**: a `fail`
   here blocks the creative regardless of other scores.
7. **Plausibly click-worthy?** Would the target persona actually stop and tap, given their
   awareness level?
8. **Matches the chosen angle?** Does the execution actually express `strategy.angle`
   (a `before_after` spec must show a real before/after, etc.)?
9. **Matches the ad spec?** Does the rendered image reflect `visual`, `copy.overlay_text`
   (spelled correctly), `layout`, and `product` faithfully?

## Scoring

- **Ship:** checks 1–9 all `pass`, check 6 clear, with at most minor `warn`s noted.
- **Iterate:** any `warn` on 1–5/7/8/9 → fix and regenerate; record what to change in
  `qa.iteration_notes`.
- **Block:** any `fail` on check 6 (policy) → do not ship; either fix the spec to use real /
  labeled proof or switch to an angle that needs no fabricated proof.

## Mapping to the spec `qa` block

| Rubric check | Spec field |
| --- | --- |
| 1, 4 | `qa.readability_check` |
| 3, 5 | `qa.native_platform_check` |
| 6 | `qa.policy_risks` (empty only if truly clear) |
| 2, 7, 8, 9 | `qa.iteration_notes` (verdict + next step) |

## Post-generation visual QA (only doable after the image exists)

- Read every word baked into the image — **is any text misspelled or garbled?** (most common
  image-model failure). If yes → regenerate or fix overlay in post.
- Count hands/fingers/limbs and product units — any anatomical or quantity artifacts?
- Is the reserved negative space actually clear for the platform's overlaid UI?
- Does the focal point survive a 150px-wide thumbnail squint test?

## Output of QA

`qa_check.py` emits `<creative_id>.qa.json`:

```json
{
  "creative_id": "...",
  "stage": "pre_generation | post_generation",
  "checks": [{"id": 1, "name": "hook_readable", "result": "pass|warn|fail", "note": "..."}],
  "policy_gate": "clear | blocked",
  "verdict": "ship | iterate | block",
  "recommended_next_iteration": "..."
}
```
