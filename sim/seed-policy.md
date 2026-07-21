# Meta Ads Policy — v0 (seed, identical for every sim world)

GOAL this policy serves: maximize settled ROAS (revenue / spend) for a $29/mo
product ("Formflow"); purchases are the goal event.

## Creative
Run three video ads per batch. Declared angle families: benefit, outcome,
count (adoption numbers), story (named customer). A demo variant (screen-
recording of the product working) is an available production option.

Starting slate — these three ads are the batch-1 incumbents. They are
starting incumbents ONLY: replaceable through the standard
champion/challenger process, no protected status.

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

Challenger copy rules: new copy enters as challenger variants and must
lead with the viewer's pain in line one; show the product doing the thing
rather than claiming it; carry one idea per ad; use concrete artifacts and
numbers over adjectives; state the low-commitment CTA. Every claim must be
supported by the landing page. Count remains a declared family with no
seat — the coverage sweep owes it a funded taste in an early batch.

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

## Campaigns
Standard cold-start portfolio per batch, $200 total: a pageviews campaign
(broad), a leads campaign (broad), sales campaigns, and optionally an
auto-budget sales campaign (platform allocates between audiences).
Audiences available: broad, interest_biztools, interest_niche.

## Judgment
Judge only settled results. An ad/cell with no measurement is unknown, not
bad. Never repeat an approach that measurably failed. Unreadable cells
(starved/short-window) are evidence of nothing.

## Process
After each batch, the semantic gradient reads all receipts and revises this
policy; a noise schedule picks the revision dose. Every batch's full receipts
accumulate as evidence.
