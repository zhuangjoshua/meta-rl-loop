# Meta Ads Policy — v0 (seed, identical for every sim world)

GOAL this policy serves: maximize settled ROAS (revenue / spend) for a $29/mo
product ("Formflow"); purchases are the goal event.

This policy is organized as three ownership sections — Media Buying, Ad Copy,
Video Creative — plus shared Judgment. Each section states what it Owns and
what state it Reads; a decision belongs to exactly one section.

Blocks marked `[PROD-ONLY — DORMANT IN SIM]` are outside the simulator's
action interface. Do not execute them, do not count their axes in coverage or
experimentation, and do not rewrite them; they ride along frozen so this same
policy can operate real Meta later without translation.

## 1. Media Buying

Owns: campaign objective, budget mode and split, audience posture, campaign
count, which ads are eligible in which campaign (`ad_ids`), and the next
buying action after evidence arrives.

Reads: the goal, landing page, platform audiences and CPMs, prior receipts,
and the current creative slate. Never hidden-market data.

### Campaign slate

The executable surface is: objectives `clicks`, `pageviews`, `leads`, `sales`;
audiences `broad`, `interest_biztools`, `interest_niche`; modes `fixed` (one
audience) and `auto` (platform allocates across at least two audiences);
budgets summing to exactly $200 per batch, every ad funded somewhere.

Initial slate (this is the batch-1 portfolio, and the default until evidence
says otherwise):

1. `pageviews_broad` — cheap qualified attention from the widest pool.
2. `leads_broad` — tests whether optimizing for signups finds different people
   than optimizing for attention.
3. `sales_broad` — tests whether purchase optimization works at all on broad.
4. `sales_biztools` — purchase optimization on the business-tools interest.
5. `sales_niche` — purchase optimization on niche professional communities.

`auto` mode is a declared budget-allocation family with no seat in the initial
slate; the coverage plan owes it a funded taste once a fixed-audience
incumbent exists to compare it against.

Buying rules: keep destination, offer, and observation window aligned across
the slate unless one of them is the explicit test axis; each challenger
changes one axis only; a campaign profile that cannot be expressed in the
executable surface is blocked, not approximated.

[PROD-ONLY — DORMANT IN SIM]
Execution boundary: MCP lane for account discovery, campaigns, ad sets,
creatives, ads, previews, insights, activation, pause, updates; Graph lane for
/advideos, image upload, custom conversions, dataset/pixel edges, event
submission. Production slate shapes: traffic-link-clicks-abo, traffic-lpv-abo,
leads-website-abo (requires a real measurable lead event),
sales-website-abo (requires a verified sales event; value optimization only
with a trustworthy value signal), matched-primary-cbo (CBO with at least two
non-duplicate ad sets, mirrored in the matched ABO family; blocked until a
defensible second audience exists). Global settings: Advantage+ placements;
lowest cost without cap; default billing event; one consistent attribution
setting; create paused unless live intent; encode type/objective/goal/budget
mode/audience in object names; approved exclusions only. Measurement gate:
traffic may run without a pixel (traffic metrics only); leads/sales/value
require a working pixel or dataset and exact promoted-object references,
otherwise block with the exact missing requirement; video requires uploaded,
processing-complete assets; unqueryable objects block with the provider
reason. Evaluation fallbacks when no business target exists: Link CTR good
> 1.5%, CPC good < $2, CPM good < $15, CVR good > 1.5%, 7-day frequency good
1.0-2.5, CPA good <= target, ROAS good >= 2.0; learning-phase spend concerns
are `wait`, frequency > 3.0 with CTR decline is fatigue, Meta-versus-server
attribution gaps are measurement problems first. Verdict to action: good ->
scale gradually; watch -> wait or narrow one hypothesis; bad CTR/CPC after
learning -> refresh creative; bad CPA/ROAS after learning -> pause; learning
-> wait; attribution_gap -> fix measurement before structure.
[END PROD-ONLY]

## 2. Ad Copy

Owns: headline, primary message, CTA, destination match, claims posture, and
copy variants.

Reads: verified product and offer truth from the landing page, prior copy
artifacts and their receipts, and the creative route. Unsupported proof,
results, testimonials, and numbers remain unavailable — every claim must be
supported by the landing page.

Declared copy-angle vocabulary (the coverage sweep works through these; an
angle never funded is unknown, not bad):

1. `direct-benefit-copy` — the clearest truthful statement of the offer, main
   benefit, and next step. Use when one strong payoff is easy to understand.
2. `problem-copy` — a specific pain or friction-led opening that pivots
   quickly to the product as the way out. Sharp and recognizable, never
   melodramatic.
3. `proof-copy` — proof-first framing inside approved evidence: concrete
   number, artifact, quote, or visible result, translated into what it means
   for the buyer.
4. `mechanism-copy` — how-it-works framing: the key difference, a short easy
   sequence, tied to a useful result; legible, never documentation.
5. `offer-copy` — the low-risk entry made obvious early: trial terms, easy
   first action, truthful friction-removal; no fake urgency or scarcity.

Copy rules for every treatment: the first line does the work — lead with the
viewer's pain or payoff; one main idea per ad; concrete artifacts and numbers
over adjectives; show the product doing the thing rather than claiming it;
headline, message, and CTA read as one unit; state the low-commitment CTA;
angles must be materially different, never synonym-only variants; if a seeded
angle is unsupportable by the landing page, replace only that angle with the
strongest truthful alternative and record why.

## 3. Video Creative

Owns: video concepts, opening hooks, scene sequences, demonstrations, pacing,
overlays, CTA presentation, and format variants. In the sim an ad's video is
its written scene timeline (duration and second-by-second scene content);
consumers respond only to content plausibly seen before abandoning, so the
first seconds must earn attention truthfully.

Reads: approved copy, offer, proof, and prior creative observations. Separate
observed performance from explanations inferred after the fact.

Declared video-format vocabulary (same coverage discipline as copy angles):

1. `ugc-video` — creator/customer-style: opens on a human moment or strong
   first line, native to feed, product early, captions carry the story.
2. `demo-video` — product on screen in the first shot; input -> action ->
   result; the transformed state visually obvious.
3. `founder-video` — an operator talking directly: strong point of view,
   proof or product cutaways, what is different, clear invitation.
4. `social-proof-video` — strongest quote or before/after first, anchored in
   a real person or artifact, each shot adding confidence; shows what
   improved.
5. `explainer-video` — problem first, short visual logic chain, one concept
   per beat, closes on value and next step.

Format rules: the seed set must not be only one format; keep copy, offer,
destination, and every non-tested dimension fixed when format is the test
axis; one clear opening, one main body idea, a simple close; keep scenes
moving — never make the viewer wait for the product, the proof, or the point;
show the claim whenever possible instead of stating it.

[PROD-ONLY — DORMANT IN SIM]
Rendering and review: render approved specifications through the Render Video
skill; review actual rendered output for technical integrity, script and
claim match, CTA and disclosure match, and rights before launch; require a
finished video artifact and thumbnail before Meta handoff; supply reviewed
artifacts and variant metadata to the Operate Meta Ads MCP API skill.
[END PROD-ONLY]

## Coverage plan (control-first, three seats per batch)

The production launch matrix (five copy angles on one control video, then
four remaining formats on one control copy) cannot run in parallel here: the
simulator executes exactly three ads per batch. Run the same matrix as a
sequence. Batch 1 is the fixed anchor slate below, which already covers:
direct-benefit-copy + demo-video (`ad_demo_workflow`), problem-copy +
explainer-video (`ad_outcome_followup`), and proof-copy + social-proof-video
(`ad_story_maya`; the old seed's `count` family — supported adoption
numbers — is folded into proof-copy, not dropped). The anchor ads each vary
both axes at once (a known batch-1 confound, accepted to keep the control
slate identical across epochs); single-axis discipline begins at batch 2.
From batch 2 onward, hold the incumbent-best execution unchanged as one seat
and use the remaining seats to work through unpriced vocabulary values,
starting with the pivot cell direct-benefit-copy + ugc-video (which is both
sweeps' shared reference AND the owed taste of the mandatory ugc format),
then the remaining owed copy angles on the control format (mechanism-copy,
offer-copy), then the remaining owed format on the control copy
(founder-video) — before refining among already-priced values. New copy or
video enters as a challenger; the incumbent's exact text is never rewritten
in the same batch as its challenger.

## Judgment

Judge only settled results. An ad or cell with no measurement is unknown, not
bad. Never repeat an approach that measurably failed. Unreadable cells
(starved or short-window) are evidence of nothing. Do not infer that a
creative element caused performance unless the comparison isolated it; write
a new challenger only when the last comparison was interpretable; give every
challenger a fair delivery window before verdict. Keep the better value on a
tested axis and replace only the weak axis. Treat landing-page mismatch and
unsupported claims as validity failures, not experiments.

## Process

After each batch, the semantic gradient reads all receipts and revises this
policy; a noise schedule picks the revision dose. Every batch's full receipts
accumulate as evidence.

## Starting slate — batch-1 incumbents

These three ads are the batch-1 incumbents. They are starting incumbents
ONLY: replaceable through the standard process, no protected status. They are
byte-identical to the prior seed's slate so every epoch shares one control
instrument.

1. ad_demo_workflow (angle: benefit, demo: true).
   Headline: "Turn every client intake email into one clear workflow."
   Message: Client requests shouldn't live across inbox threads and
   half-finished attachments. Formflow gives you a branded intake page with
   conditional questions, turns each response into an organized client
   record, and reminds clients about missing answers automatically. 14-day
   trial, $29/month after, cancel anytime.
   Visual: 30-second vertical screen recording: a cluttered inbox ->
   template selected -> conditional question added -> branded link
   published -> a request arrives complete -> the status board updates to
   Ready.
   CTA: Start your 14-day trial.

2. ad_outcome_followup (angle: outcome, demo: false).
   Headline: "Stop chasing missing client details."
   Message: When a brief arrives half-finished, the project becomes
   reminder emails. Formflow collects every detail and attachment in one
   structured request, follows up on missing answers for you, and shows
   every request as New, Waiting on Client, Ready, or Complete. Clients
   use a secure link — no account needed.
   Visual: 25-second motion piece: a vague email -> a missing-field
   reminder firing -> the client completing via secure link -> the status
   board flipping to Ready.
   CTA: Start your 14-day trial.

3. ad_story_maya (angle: story, named_story: true, demo: true).
   Headline (verbatim, including the interior quotation marks):
   "Requests used to die in my inbox." — Maya Reyes, agency founder
   Message: Maya runs Fieldnote Studio, a 6-person design agency. Briefs
   arrived scattered across threads and attachments, and follow-ups ate
   her week. Now every client uses one Formflow link: answers arrive
   complete, reminders go out automatically, and her board shows what's
   Ready. "We stopped chasing."
   Visual: 30-second vertical: Maya's quote on screen -> screen recording
   of her intake link collecting a brief -> the reminder firing -> the
   status board -> closing quote card.
   CTA: Start your 14-day trial.

### Batch-1 spec (executable — the runner loads this block verbatim; keep
### it byte-consistent with the prose slate above)

```json
{
  "ads": [
    {
      "id": "ad_demo_workflow",
      "headline": "Turn every client intake email into one clear workflow.",
      "message": "Client requests shouldn't live across inbox threads and half-finished attachments. Formflow gives you a branded intake page with conditional questions, turns each response into an organized client record, and reminds clients about missing answers automatically. 14-day trial, $29/month after, cancel anytime.",
      "visual": "30-second vertical screen recording: a cluttered inbox -> template selected -> conditional question added -> branded link published -> a request arrives complete -> the status board updates to Ready.",
      "call_to_action": "Start your 14-day trial",
      "proof": "benefit",
      "named_story": false,
      "demo": true
    },
    {
      "id": "ad_outcome_followup",
      "headline": "Stop chasing missing client details.",
      "message": "When a brief arrives half-finished, the project becomes reminder emails. Formflow collects every detail and attachment in one structured request, follows up on missing answers for you, and shows every request as New, Waiting on Client, Ready, or Complete. Clients use a secure link — no account needed.",
      "visual": "25-second motion piece: a vague email -> a missing-field reminder firing -> the client completing via secure link -> the status board flipping to Ready.",
      "call_to_action": "Start your 14-day trial",
      "proof": "outcome",
      "named_story": false,
      "demo": false
    },
    {
      "id": "ad_story_maya",
      "headline": "\"Requests used to die in my inbox.\" — Maya Reyes, agency founder",
      "message": "Maya runs Fieldnote Studio, a 6-person design agency. Briefs arrived scattered across threads and attachments, and follow-ups ate her week. Now every client uses one Formflow link: answers arrive complete, reminders go out automatically, and her board shows what's Ready. \"We stopped chasing.\"",
      "visual": "30-second vertical: Maya's quote on screen -> screen recording of her intake link collecting a brief -> the reminder firing -> the status board -> closing quote card.",
      "call_to_action": "Start your 14-day trial",
      "proof": "story",
      "named_story": true,
      "demo": true
    }
  ],
  "campaigns": [
    {"id": "pageviews_broad", "objective": "pageviews", "audience": "broad", "mode": "fixed", "budget": 40.0, "ad_ids": ["ad_demo_workflow", "ad_outcome_followup", "ad_story_maya"]},
    {"id": "leads_broad", "objective": "leads", "audience": "broad", "mode": "fixed", "budget": 40.0, "ad_ids": ["ad_demo_workflow", "ad_outcome_followup", "ad_story_maya"]},
    {"id": "sales_broad", "objective": "sales", "audience": "broad", "mode": "fixed", "budget": 40.0, "ad_ids": ["ad_demo_workflow", "ad_outcome_followup", "ad_story_maya"]},
    {"id": "sales_biztools", "objective": "sales", "audience": "interest_biztools", "mode": "fixed", "budget": 40.0, "ad_ids": ["ad_demo_workflow", "ad_outcome_followup", "ad_story_maya"]},
    {"id": "sales_niche", "objective": "sales", "audience": "interest_niche", "mode": "fixed", "budget": 40.0, "ad_ids": ["ad_demo_workflow", "ad_outcome_followup", "ad_story_maya"]}
  ]
}
```
