# Meta Ads Policy — compressed v4 (test incumbent for the gradient loop)

Compressed from workspace-root `POLICY.md` (`meta-policy-v4`); decision content
preserved, execution detail trimmed. Version: `meta-policy-v4c`.

## Precedence

Apply in order: (1) law, Meta rules, truth, privacy, restricted categories;
(2) operator/business intent and authorized channel credits; (3) live MCP
capability/schema evidence and account eligibility; (4) verified goal, unit
economics, destination, and attribution facts; (5) receipt-backed results;
(6) this policy's priors; (7) bounded exploration. Exploration never overrides
layers 1–4.

## Read state before acting

Business mode, credit allocation, existing plans/receipts/syncs/evaluations,
`metrics/roas/meta.md`, copy/creative artifacts and reviews, pixel/attribution
records, destination truth, slot status. Missing evidence is not negative
evidence; an unavailable metric is not zero.

## Cold start: five-profile parallel batch

Run one coordinated batch, every profile sending traffic to the same canonical
landing page: (1) `abo-traffic-link-clicks`; (2) `abo-traffic-landing-page-views`;
(3) `abo-leads-website` — only with a real lead action; (4) `abo-sales-website` —
only with a real value event; (5) `cbo-matched-primary-objective` — matched to its
ABO twin with the same two ad sets; budget location is the only difference.
All five must be eligible or the batch stays blocked. Never drop or swap a card,
never sequentialize, never turn a blocked conversion profile into Traffic.

## Creative treatment matrix

Resolve ONE `creative_kind` for the whole cohort (reviewed operator route >
existing reviewed asset > `ugc_video` > `static_image` > blocked). Three rows,
replicated identically across every profile: `control`, `copy-challenger`
(copy axis only), `ugc-production-challenger` (production axis only). Each
challenger isolates exactly one axis. Treatments are biased hypotheses, never
predicted winners. Admission gate: artifacts validated from stored bytes against
schema; every claim no stronger than its joined evidence; invalid drafts are
failed lineage, never treatments.

## Activation

Reserve each profile's explicit share before any provider object ($4 each under
one $20 hard total cap in the current contract). Stage the whole cohort paused
with distinct idempotency keys, verify readback, activate everything inside one
recorded window; partial failure pauses every sibling. Only the guarded batch
tool; never approximate with looped single launches.

## Observation

Hold configuration, matrix, destination, and allocation fixed for one declared
window. Record all signal layers without ranking: ROAS/CAC; verified value
events and CPA; shared landing-page conversion; landing-page views, LPV rate,
cost per LPV; link CTR/CPC. Maturity gates: spend floor, runtime floor,
conversion-lag window, minimum event counts. Hard pause only for broken
destination/measurement, compliance failure, spend cap, billing anomaly, or
operator request. Never cut, scale, or reallocate from performance.

## Handoff

At window end or cap: pause the cohort together, settle spend, release unused
reservations, write the immutable observation bundle, and wait for the external
RL/semantic-gradient system. This policy never revises itself from its own
observations; a run pins its policy version from planning through settlement.

## Priors (revisable)

One clear objective matching the real goal; simple structure over fragmentation;
broad audiences over invented interests; automated placements when assets
support them; meaningfully different creative treatments; lowest-cost automated
bidding; stable time/budget for learning; conversion objectives only with
verified events; exact variant lineage everywhere.

## Compliance (fixed)

Real assets, claims, destinations, and results only; never fabricate delivery or
attribution; pause, never hard-delete; tokens stay in Safebox; every action
business-scoped, idempotent, credit-bounded, receipt-backed.
