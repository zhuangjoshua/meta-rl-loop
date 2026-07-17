# Meta Ads Policy

Policy version: `meta-policy-v6`. Parent: `meta-policy-v5`.

This file owns what to do in Meta: campaign choices, exploration, budget pacing, measurement,
creative/copy routing, and evidence-based follow-up. `SKILL.md` and `TOOLS.md` separately own how to
execute those decisions.

## Policy precedence

Apply constraints in this order:

1. law, Meta requirements, truth, privacy, rights, and restricted-category rules;
2. explicit operator/business intent and authorized channel credits;
3. live MCP capability/schema evidence and account eligibility;
4. verified business goal, unit economics, product, destination, and attribution facts;
5. compatible receipt-backed results;
6. this policy's priors;
7. bounded exploration.

Exploration never overrides the first four layers.

## Read state before acting

Read:

- current business mode and channel credit allocation;
- live MCP capability receipt when provider fields matter;
- existing Meta plans, receipts, actions, syncs, and evaluations;
- `metrics/roas/meta.md`;
- copy artifacts, creative manifests, and post-render output reviews;
- pixel/attribution records;
- destination and offer truth;
- current campaign slot and spend-policy status.

Distinguish missing evidence from negative evidence. An unavailable metric is not zero.

## Compose policy layers

The Meta policy may invoke the specialist policy/execution pairs.

### Copy route

```text
skill_read_resource(skill="meta-copy-policy", path="POLICY.md")
skill_read_resource(skill="ad-copy", path="SKILL.md")
skill_read_resource(skill="ad-copy", path="TOOLS.md")
```

Use whenever exact launch copy or a new copy treatment is missing.

### UGC route

```text
skill_read_resource(skill="meta-ugc-policy", path="POLICY.md")
skill_read_resource(skill="ugc-video-ad", path="SKILL.md")
skill_read_resource(skill="ugc-video-ad", path="TOOLS.md")
```

Use whenever a selected video treatment lacks a finished canonical UGC asset.

The selected copy and UGC policies must share one experiment/variant lineage. For static images, use
the same copy artifact and Meta metadata with `static-ad-creative-generator`.

A policy-composed video UGC launch is complete only when it has all three reviewed handoffs:

- `copy_artifact_path`: the reviewed immutable copy treatment;
- `variant_manifest_path`: the canonical render lineage, preflight rights/policy decision, and media
  paths;
- `ugc_review_path`: the approved post-render QA decision for the actual video and audio.

The manifest's preflight decision cannot approve output that did not exist when the brief was
decided. After rendering, an authorized reviewer must inspect the actual publication and record
`product/ugc-ads/<variant-id>/qa.json` using `takyon.ugc-output-review.v1`. Launch is blocked unless
the review names the exact manifest, has `status: "approved"`, identifies the reviewer and review
time, and passes video decoding, continuity, anatomy/motion, lip-sync/audio, script/claim match,
CTA/disclosure match, and rights/policy checks.

## Parallel cold-start exploration, then external handoff

The cold-start objective is to expose every eligible campaign configuration to the market at the
same time under one bounded budget and observation window. This policy does not choose a winner or
run an exploitation phase after metrics arrive. It records the batch and hands observations to the
separate RL/semantic-gradient system.

### Phase 0: capability and measurement exploration

Before paid delivery:

- snapshot live MCP tools and schemas;
- identify account, Page, Instagram, currency/timezone, and eligibility facts available through
  guarded surfaces;
- verify the destination works;
- verify the exact event/custom-conversion boundary required by the objective;
- inspect a preview when a guarded preview wrapper exists;
- use `business_meta_capabilities` for provider schemas and test-mode launch receipts for suppressed
  local-plan rehearsal without claiming provider creation or delivery.

Do not create paid campaigns merely to discover fields that `tools/list` can reveal.
Do not use a paused single-hierarchy launch as a schema probe: it creates real provider objects,
charges setup credit, and reserves media authority. The production batch accepts only one confirmed
live request; its internal paused staging and readback are safety gates before coordinated activation,
not a no-spend mode.

### Phase 1: five-profile parallel landing-page batch

When no compatible Meta history identifies the best acquisition configuration, create one cold-start
portfolio with exactly the five profiles below. Every profile sends traffic to the same canonical
company landing page. A profile may append approved tracking parameters, but it must not swap the
destination for an instant form, message thread, app store, video-only experience, or another page.

`Traffic` is a campaign objective. `Link clicks` and `landing page views` are performance goals.
`ABO` means the budget is controlled at ad-set level. `CBO` is the former name for what Meta now calls
Advantage+ campaign budget, where one campaign budget is distributed across ad sets.

| Position | Profile key | Objective / performance goal / budget location | What it explores | Eligibility gate |
| --- | --- | --- | --- | --- |
| 1 | `abo-traffic-link-clicks` | Traffic / link clicks / ABO | Whether delivery can find people who click the landing-page link | Working landing page, link measurement, complete live schema, and explicit spend envelope |
| 2 | `abo-traffic-landing-page-views` | Traffic / landing page views / ABO | Whether optimizing for a loaded page improves visit quality over raw clicks | Working landing page, pixel/dataset page-view evidence, complete live schema, and explicit spend envelope |
| 3 | `abo-leads-website` | Leads / verified website lead event / ABO | Whether delivery can find landing-page visitors who complete the real lead action | Real website lead action, consent/privacy, qualification/follow-up definition, event attribution, promoted object, and complete live schema |
| 4 | `abo-sales-website` | Sales / deepest verified website value event / ABO | Whether delivery can find landing-page visitors who register, start a paid-relevant trial, or purchase | Real value event, checkout or activation path, event attribution, promoted object, unit economics, and complete live schema |
| 5 | `cbo-matched-primary-objective` | Declared primary eligible objective and performance goal / Advantage+ campaign budget / at least two meaningful ad sets | Whether Meta's cross-ad-set budget allocation behaves differently from matching ABO during the same batch | Matching ABO profile in this batch, two non-duplicate ad sets shared with that ABO profile, compatible budget/bid/delivery settings, complete CBO schema, and explicit campaign spend envelope |

The Leads and Sales profiles are not generic aliases. Make each `inapplicable` when the landing page
does not contain the corresponding real action. Do not optimize Leads for a purchase or Sales for an
unqualified email capture merely to fill the portfolio.

Choose the CBO objective before delivery from declared business facts, not performance: match the ABO
Sales profile when the declared primary outcome is a verified Sales event; otherwise match ABO Leads
when a verified website lead is the primary outcome; otherwise match ABO Traffic / landing-page views;
otherwise match ABO Traffic / link clicks. The matched ABO and CBO profiles must use the same
objective, performance goal, at least two defensible ad-set definitions, landing page, targeting
constraints, attribution, treatment matrix, and total observation envelope. Only budget location may
differ. CBO may distribute its share unevenly inside the campaign; that is part of the configuration
being observed, not permission to change the portfolio allocation.

Create all five cards on the first policy pass and keep business eligibility separate from runtime
execution state.

Assign one `eligibility_status`:

- `eligible`: destination, business prerequisites, measurement shape, and an allocatable observation
  envelope exist;
- `blocked`: a required truth, right, event, destination field, provider schema field, or safe spend
  input is missing;
- `inapplicable`: the company landing page does not offer the action the profile would optimize.

Assign a separate `execution_status`: `pending_creative`, `launch_ready`, `blocked_runtime`,
`staged`, `active`, `interrupted`, `paused_at_boundary`, or `partial_failed`. A missing runtime
capability must not erase an otherwise eligible candidate.

Never silently drop a card, change its landing page, turn a blocked conversion profile into Traffic,
or replace parallel execution with a sequence. Record the exact blocker. Freeze the initial candidate
set from every card marked `eligible`, even when the batch later becomes `blocked_runtime`; every
candidate must eventually launch in the same batch. Blocked and inapplicable cards remain visible but
are not claimed as tested. Production v1 requires all five: if one is blocked/inapplicable, the whole
batch remains blocked until all five are eligible.

#### Initial artifact admission gate

Do not admit a copy or creative row to the cold-start candidate set from an agent's self-reported
"verified" result alone. Require the runtime write/read boundary or a dedicated validator to prove
the exact stored bytes satisfy the published schema and joins. For every initial copy arm:

- `parent_variant_id` is `null`, because all three rows begin in the same initial cohort;
- `changed_dimensions` is a non-empty list of schema-valid fields; the control records the dimensions
  chosen for its initial hypothesis, and the production-only row may name only its row-bound script
  path in the copy artifact;
- destination, Meta CTA enum, feed copy, and spoken CTA agree with the paid landing-page action; a
  button-driven landing-page ad must not tell the viewer to use a profile bio link;
- every exposed claim, promise, objection answer, number, or time-saving statement is no stronger
  than its joined evidence; and
- copy-control equality and production-control equality are checked from stored artifacts, not from
  intended values in a prompt.

When the caller already supplies exact business, experiment, destination, policy, capability, and
artifact bindings, use those bound values and narrowly scoped reads; loading a full capability
receipt or unrelated plan state adds no eligibility evidence. A schema-invalid, misleading, or
otherwise unlaunchable draft is not a treatment and never enters the matrix. Preserve it as failed
lineage evidence, choose a new variant ID, and validate the replacement; do not overwrite an
immutable or previously reported arm.

#### Shared creative exploration matrix

Planning the five cards does not create copy or media. Persist the eligibility/budget configuration
draft first with an empty treatment set; then build and freeze the reviewed matrix in the same plan
before staging the batch:

Resolve `creative_kind` once for the whole cohort in this order:

1. honor an explicit reviewed operator route that has complete assets/rights;
2. otherwise use `existing_asset` only when a reviewed asset already matches the landing page,
   proposition, audience, CTA, placements, and initial experiment intent;
3. otherwise prefer `ugc_video` for a digital product when truthful explanation or demonstration is
   feasible and the authorized creative budget covers all three reviewed renders;
4. otherwise use `static_image` when a truthful image treatment can be produced and reviewed;
5. block creative preparation when none is feasible.

This pre-spend routing decision is not inferred from campaign metrics. Use the chosen kind everywhere
in the initial cohort.

Current production constraint: the only public coordinated five-profile launcher,
`business_meta_ad_batch_stage_activate` v1, supports the three-row `ugc_video` matrix. A
`static_image` or `existing_asset` decision still skips UGC, may be preserved in a configuration or
test-rehearsal plan, and must remain `blocked_runtime` for live parallel execution until a guarded
two-row wrapper exists. Never fabricate UGC or fall back to five single-ad launches to bypass this
constraint.

1. resolve one `creative_kind` from `ugc_video`, `static_image`, or `existing_asset`; an eligible
   profile cannot become `launch_ready` while it remains `undecided`;
2. verify the complete guarded parallel batch surface before paid creative generation; if it is
   absent, preserve the configuration draft as `blocked_runtime` and stop before rendering;
3. call `meta-copy-policy` once to choose two semantic copy treatments, BOTH anchored in
   verifiable proof: the biased control is
   the most SPECIFIC verifiable customer proof available — a named customer story with
   concrete numbers; aggregate adoption counts may not lead and serve only as supporting
   lines or challengers; the challenger explores a meaningfully
   different proof form (numbers-in-use, named-customer story, category-standard
   framing); non-proof angles (outcome, benefit, curiosity) may enter a cohort only as
   an explicitly declared hypothesis row with recorded rationale;
4. assign the composite row IDs and run `ad-copy` per row, duplicating the exact control message for
   the UGC-production-challenger row;
5. when `creative_kind` is `ugc_video`, call `meta-ugc-policy` from the reviewed copy rows to choose a
   production control and one meaningfully different production challenger, then use `ugc-video-ad`
   to render and review all three rows;
6. when `creative_kind` is `static_image` or `existing_asset`, skip UGC and reuse the exact same
   reviewed visual with the two copy treatments;
7. require every copy artifact's `ugc_handoff.requires_ugc` to equal
   `creative_kind == "ugc_video"`;
8. keep component IDs in the Meta plan/metadata rather than the strict copy/UGC schemas, freeze the
   completed matrix, and replicate it across every candidate and each comparable ad set;
9. stage no Meta provider object until every candidate has the complete treatment set, measurement
   boundary, equal profile budget share, reservation cap, and guarded capability shape.

For the initial UGC route, five profiles times three treatments create 15 logical
profile-treatment cells. Because the matched ABO and CBO profiles each use two matched ad sets, the
provider topology contains seven ad sets and 21 physical ads. Keep both counts explicit: the 15 cells
are policy hypotheses, while the 21 ads are their physical replication across ad sets.

For UGC video, the initial matrix is exactly:

| Treatment | Copy | UGC production | Changed policy axis |
| --- | --- | --- | --- |
| `control` | copy control | production control | none; shared reference |
| `copy-challenger` | copy challenger | production control | copy only |
| `ugc-production-challenger` | copy control | production challenger | UGC production only |

The copy-challenger video must be a separate render because its spoken words differ, but it must reuse
the control production labels and directions. Do not add a fourth copy-challenger plus
production-challenger treatment in the initial batch; it would confound both policy axes and consume
more delivery, and the three-row design does not estimate copy-by-production interaction. For a
static or existing visual, use only `control` and `copy-challenger` with the same visual.

Keep semantic component identity separate from composite artifact identity. Record
`copy_treatment_id` and `production_treatment_id` on every row, then give the row a unique composite
`variant_id`. Materialize every strict copy/UGC artifact under that composite ID. This may duplicate
the exact control message or production directions under another composite path; field fingerprints
must prove that the supposedly held-fixed component is actually identical.

Record the comparative treatment delta separately as `isolated_policy_axis` and
`isolated_axis_delta_fields`. Initial strict artifact `changed_dimensions` identify the fields that
define each root component; they do not override the matrix's explicit comparative delta.

Every selected treatment is exploratory and launches with the cohort. If a required challenger is
unsafe, unsupported, materially indistinguishable, unreviewed, or unaffordable, mark the batch
`blocked_creative_exploration`; do not silently collapse it to one treatment. The control and
challengers are biased starting hypotheses, not predicted winners.

Campaign management, capability discovery, status, budget control, insights, and evaluation do not
call copy or UGC merely because the portfolio exists.

#### Campaigns versus ad sets

Objective is selected at campaign level; different objectives require different campaigns. For the
initial batch, represent link-click and landing-page-view profiles as separately budgeted provider
units so their equal portfolio shares and metrics remain isolated. Do not collapse them into one CBO
campaign or let one profile spend another profile's share.

The CBO candidate is one campaign with at least two meaningful ad sets, matched to an ABO candidate
with the same ad sets. It is a budget-location configuration candidate, not another offer or
destination. Do not manufacture broad, interest, and lookalike ad sets merely to populate CBO; use
two non-duplicate hypotheses supported by business/audience facts or mark CBO blocked.

#### Parallel activation barrier

Parallel means one cohort, not merely several campaigns that eventually overlap:

1. calculate and reserve every candidate's explicit share before media upload;
2. create every candidate and all of its replicated treatment ads paused with distinct idempotency
   keys;
3. verify all required provider IDs and requested paused states through guarded reads;
4. activate the complete cohort inside one recorded activation window;
5. if staging or activation partially fails, pause every created sibling and mark the batch
   `partial_failed`; do not leave a smaller accidental experiment running;
6. record one `batch_started_at`, one planned `observation_end_at`, and each actual activation time.

Provider calls may be serialized mechanically, but activation and measurement belong to one cohort.
Use `business_meta_ad_batch_stage_activate`; never approximate this batch with repeated
`business_meta_ad_launch` calls.

#### Shared controls and interpretation

Hold the canonical landing-page URL, geography, required age/language constraints, cold-prospecting
posture, customer exclusions, placement posture, bid posture, complete treatment matrix, total spend
envelope, and observation timing stable wherever the objective permits.
Use profile-specific tracking parameters for attribution without changing the underlying page. Record
every unavoidable CTA, event, audience, copy, or asset difference as a confound.

A treatment matrix is compatible across profiles only when all of these remain identical:

- normalized landing-page origin/path after removing approved tracking parameters;
- audience stage plus required geography/language/age constraints;
- treatment IDs, axis labels, offer, terms, CTA enum, claims, proof sources, and exact copy strings;
- `creative_kind` and every exact asset or copy/UGC manifest/review identity;
- rights, disclosure, and compliance decision.

Objective, performance goal, and ABO/CBO budget location are the intended profile differences and do
not break matrix compatibility. Any content/asset mismatch above requires a new variant, complete
`changed_dimensions`, and an explicit confound; never silently call it the same treatment.

This is a policy-guided configuration scan, not an official Meta A/B test. Record these signal layers
for every profile without ranking them:

1. revenue, gross profit, paid-customer CAC, or ROAS;
2. qualified lead, activated trial, or another verified value-bearing event and its CPA;
3. the shared primary landing-page conversion and its CPA/CVR;
4. landing-page views, landing-page-view rate, and cost per landing-page view;
5. link CTR and cost per link click.

The objective-native event explains delivery; it is not a winner label. Missing or conversion-lagged
downstream evidence remains missing or immature. Each profile records its native signal, shared
primary business signal, observation maturity, business targets, spend, and measurement quality for
the external learner.

#### Production execution boundary

`business_meta_ad_batch_rehearse` can validate the five-card configuration draft in either business
mode and, when complete reviewed or synthetic zero-cost fixtures already exist, the frozen copy/UGC
artifact graph in a test-mode business. The rehearsal must bind an existing same-business capability snapshot and reject
any plan-to-artifact policy-version drift. The frozen test plan keeps the batch and profiles
`blocked_runtime`; rehearsal does not make them live-ready. Its local receipt is engineering evidence
only: it records no delivery, observation, reward, winner, or RL input and must never be treated as
market performance.

`business_meta_ad_batch_stage_activate` is the production authority for this exact cold-start policy.
It requires one immutable `frozen_prelaunch` plan with all five profiles and three reviewed UGC
treatments, a fresh same-business capability snapshot, authority-side exact artifact hashing, a future common
schedule, five `$4` media reservations under one `$20` hard total cap, and explicit live-spend
confirmation. It stages the full cohort paused, reads every entity back through MCP, then activates
across one barrier; any partial failure invokes cohort rollback. The single-hierarchy
`business_meta_ad_launch` tool is not a substitute.

Calling the production wrapper is warranted only after every policy choice is frozen and the operator
has explicitly requested live delivery. Its existence does not prove any account is eligible or that
a batch has run. If the tool, credit authority, artifacts, provider minimums, or required MCP schemas
are unavailable, keep the plan `blocked_runtime` and create no provider objects.

### Phase 2: collect one cohort window

Keep campaign configuration, treatment matrix, destination, and allocated shares unchanged until
the declared observation window matures, a hard safety stop fires, an operator intervenes, or the
batch spend cap is reached. Synchronize every candidate over the same non-overlapping timestamps and
record missing metrics as missing.

Do not rank, promote, reject, reallocate, mutate copy/UGC, or open a successor from these observations.
Set the batch to `metrics_ready` only after every non-interrupted candidate/treatment cell has a
synchronized window and the observation bundle includes spend, delivery, native events, landing-page
signals, business events, attribution health, conversion lag, provider status, and all
artifact/policy joins.

### Phase 3: external RL/semantic-gradient handoff

Write the immutable observation bundle and set `handoff_status: "awaiting_external_policy_update"`.
From that point, this policy waits. The separate RL/semantic-gradient system owns interpretation,
policy revision, winner selection, reallocation, performance pauses, successors, and future creative
exploration. This bundle neither implements that system nor guesses its decision.

## Biased Meta priors

Until business evidence or live capability constraints contradict them, prefer:

- one clear business objective matching the real goal;
- a simple campaign/ad-set structure that avoids learning fragmentation;
- broad or minimally constrained audiences rather than invented interests;
- automated/broad placements when the live schema and asset support them;
- diverse, meaningfully different creative treatments;
- lead copy biased toward NAMED-customer specific proof; aggregate counts are support,
  not leads;
- copy controls biased toward verifiable adoption-proof claims;
- lowest-cost automated bidding without arbitrary caps;
- enough stable time/budget for learning;
- minimal material edits during learning;
- a real conversion objective only when its event and promoted object are ready;
- exact variant lineage and receipt-backed measurement.

These are priors, not rigid defaults. Explore targeted audiences, manual placements, bid controls,
other structures, and other objectives when verified evidence makes them credible and the live MCP
schema supports them.

## Example campaign settings for learning

Examples are starting hypotheses, not universal prescriptions. Validate every field against the live
MCP schema and the selected guarded wrapper.

### Executable-path rule

Every launch decision must explicitly supply `objective`, `targeting`, `daily_budget_usd`, and
`mode`; mechanical handler defaults are not policy. For a one-hierarchy launch, the narrow verified
baseline is `OUTCOME_TRAFFIC` with ABO, impressions billing, and link-click optimization. For the
five-profile production batch, every profile's complete objective, optimization, destination,
promoted-object, attribution, schedule, and budget shape must validate against the fresh frozen MCP
schemas before any mutation. A missing field blocks the whole batch; never squeeze another objective
into the single-launch Traffic shape.

### Example A: executable traffic baseline

Use when the business needs initial creative/link evidence and no valid conversion optimization object
exists.

```json
{
  "objective": "OUTCOME_TRAFFIC",
  "billing_event": "IMPRESSIONS",
  "optimization_goal": "LINK_CLICKS",
  "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
  "budget_mode": "ABO",
  "daily_budget_usd": 5,
  "targeting": {
    "geo_locations": {"countries": ["US"]},
    "publisher_platforms": ["facebook"],
    "facebook_positions": ["feed"]
  },
  "mode": "paused"
}
```

This matches the current narrow launcher except that billing/optimization/bidding are fixed rather
than public inputs. The `$5` value is illustrative; replace it with the policy-selected authorized
pace. Paused staging requests paused creation and skips activation but still creates provider objects
and reservations; it is not an effective-status readback. Use live only when operator intent and
credits authorize delivery. Traffic results can update click/creative beliefs, not purchase
economics.

### Example B: broad placement traffic hypothesis

Use only when a future/expanded wrapper validates the live ad-set schema and every asset supports the
selected surfaces.

```json
{
  "objective": "OUTCOME_TRAFFIC",
  "optimization_goal": "LINK_CLICKS",
  "daily_budget_usd": 5,
  "targeting": {"geo_locations": {"countries": ["US"]}},
  "placements": "automatic_or_omitted_per_live_schema",
  "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
  "mode": "paused"
}
```

Omitting manual placements may enable Advantage+ behavior in a live schema, but the current
single-launch wrapper does not do this—it explicitly falls back to Facebook Feed. Do not claim
Advantage+ from omission in the current plan.

### Example C: Sales/conversion hypothesis

Use only when business-specific purchase attribution, pixel/dataset health, conversion domain, and
promoted object are verified and the guarded wrapper exposes them.

```json
{
  "objective": "OUTCOME_SALES",
  "optimization_goal": "OFFSITE_CONVERSIONS",
  "billing_event": "IMPRESSIONS",
  "promoted_object": {
    "pixel_id": "<verified-shared-pixel>",
    "custom_conversion_id": "<verified-business-conversion>"
  },
  "bid_strategy": "LOWEST_COST_WITHOUT_CAP"
}
```

The current launcher cannot execute this complete shape. Passing `OUTCOME_SALES` alone while keeping
link-click optimization is not a valid conversion campaign.

### Example D: Lead-generation hypothesis

Use only when the live schema, Page eligibility, lead terms, destination/form, and attribution are
verified.

```json
{
  "objective": "OUTCOME_LEADS",
  "optimization_goal": "LEAD_GENERATION_OR_VERIFIED_OFFSITE_CONVERSION",
  "billing_event": "IMPRESSIONS",
  "destination_type": "<verified-live-value>",
  "promoted_object": "<verified-live-shape>"
}
```

Do not guess the optimization/destination combination from a static enum.

### Example E: externally requested creative successor

Keep campaign settings and destination fixed, then change one treatment dimension:

```json
{
  "parent_variant_id": "proof-demo-001",
  "variant_id": "proof-demo-002",
  "phase": "explore",
  "hypothesis": "A calmer creator delivery improves qualified engagement without changing copy.",
  "changed_dimensions": ["ugc.performance_energy"]
}
```

Do not create this successor from the static cold-start policy. Use it only when the external
RL/semantic-gradient system or an authorized operator returns an explicit next action under a pinned
new policy/context. It is not a substitute for missing objective profiles.

## Objective policy

Choose the objective that matches the business outcome and available optimization plumbing.

| Goal | Candidate objective | Required proof |
| --- | --- | --- |
| Website click learning | `OUTCOME_TRAFFIC` | Functional destination and click measurement |
| Purchase/value | `OUTCOME_SALES` | Business-specific conversion, dataset health, promoted object |
| Lead | `OUTCOME_LEADS` | Valid form/offsite event, eligibility, accepted terms |
| Video engagement | `OUTCOME_ENGAGEMENT` | Supported optimization goal and meaningful video metric |
| Reach | `OUTCOME_AWARENESS` | Reach/frequency goal and measurement |
| App installs | `OUTCOME_APP_PROMOTION` | App and promoted-object configuration |

Never choose a conversion objective solely because it sounds closer to revenue. The event must be
specific, available, and optimizable.

Only the Traffic row maps to the narrow single-launch shape. The production batch may use the
policy-defined Traffic, Leads, and Sales profiles only when every corresponding field binds to the
discovered live schemas and the business supplies the real event/promoted-object prerequisites.

## Campaign structure

The target cold-start unit is one coordinated batch of every eligible profile, with independent profile
reservations and the same frozen creative-treatment matrix replicated everywhere. It is concurrent
observational exploration, not an official Meta experiment. Execute it only through
`business_meta_ad_batch_stage_activate`, never by looping the single-hierarchy wrapper.

General policy:

- combine materially similar ad sets rather than fragmenting learning;
- create only enough structure to express a real hypothesis;
- keep requested paused creation distinct from independently verified provider status or delivery;
- all campaign, ad-set, and ad objects must be active to deliver;
- stage every batch member before coordinated activation;
- pause the cohort together at its planned observation boundary;
- do not hard-delete through the agent; use pause.

## Activation policy

- Inspection, preparation, or review intent → use local plans or the local configuration rehearsal;
  use frozen rehearsal only in test mode. Do not call
  the production batch wrapper.
- Explicit production batch intent plus complete inputs, sufficient credits, and safety checks →
  call `business_meta_ad_batch_stage_activate` with `mode: "live"` and
  `confirm_live_spend: true`. Its paused stage is internal and must either cross the barrier or roll
  back; there is no paused-only production batch mode.
- A separate one-hierarchy operation may explicitly request `mode: "paused"`; that does not implement
  the parallel batch and does not imply a provider-status readback.
- Channel-credit allocation is spend authorization; do not invent a redundant generic approval gate.
- Unspecified intent does not authorize delivery.
- Batch `activated_pending_review` means activation calls and entity readback completed while Meta
  still reports review/processing/scheduled state; it is not proof of delivery.
- A rollback is complete only when MCP readback verifies every created sibling non-delivering.

## Budget and pacing

- One Meta channel credit is one cent of total media-spend authority.
- Stay inside the registered total and daily authority.
- The wrapper's legacy `$5` daily default is mechanical, not a recommendation and not an executable
  policy decision; supply the selected pace explicitly.
- Authority deployment defaults are `$1/day` minimum and `$50/day` maximum; use actual runtime values.
- Reserve each candidate's explicit share independently before staging any provider object.
- Keep the portfolio allocation fixed throughout the first observation window. CBO may redistribute
  only inside its own campaign share.
- Do not sequentialize, prune an eligible candidate, or reallocate from early performance.
- Account for setup/creative costs separately from media reservations.
- Use total profile and portfolio caps because a Meta daily budget is an average, not a hard per-day
  ceiling.

Every live profile card must predeclare:

- `daily_budget_usd`, `reservation_cap_usd`, and `total_spend_cap_usd`;
- `review_after_spend_usd` and `minimum_runtime_hours`;
- `conversion_lag_hours` and `minimum_primary_events`;
- the shared `primary_business_event` and the profile-native event;
- nullable `target_cpa_usd` and `target_roas` when the business has supplied them;
- the evidence source for each number.

The batch also predeclares nullable `minimum_treatment_spend_usd` and
`minimum_treatment_impressions` with their source. These are arm-comparability flags for the
observation bundle, not per-ad budgets or automatic pause/rotation rules.

Targets are observation context for the external learner, not static-policy pause rules. Do not derive
a target CPA from the daily budget or invent missing economics.

### Equal initial allocation

Let `N` be the number of profiles marked `eligible` when the batch is frozen. Allocate the daily media
budget equally across all `N` candidates before launch:

```text
base_share_cents = floor(daily_portfolio_budget_cents / N)
remainder_cents = daily_portfolio_budget_cents mod N
```

Assign one remainder cent to each candidate in fixed profile-position order until the remainder is
zero. Apply the same deterministic method to the total batch spend cap. Record the exact resulting
share on every card. This cent-level rounding is bookkeeping, not a performance bias.

For `business_meta_ad_batch_stage_activate` v1, `N` must equal five and the hard total cap is split by
the same formula; the generic calculation does not authorize a smaller production cohort.

If any share falls below the live runtime/platform minimum or the declared minimum meaningful
observation envelope, mark the entire batch `blocked_budget`. Do not solve that by dropping a
candidate or changing to sequential delivery; increase the portfolio authority or explicitly define
a different future batch.

### `$20` total cold-start example

For this initial production contract, set both the declared daily portfolio pace and the independent
hard total portfolio media cap to `$20`. The total cap, not Meta's daily average, is the spend
authority. With all five profiles eligible, reserve exactly `$4` total for each profile and launch all
five in the same cohort once every profile and treatment is `launch_ready`.
The CBO profile receives one `$4` campaign lifetime budget total; Meta distributes that `$4` across
its matched ad sets. It does not receive `$4` per ad set. The matched ABO profile divides its `$4`
total across the same two ad sets (`$2` each for the equal example). The other three ABO profiles
receive one `$4` ad-set lifetime budget each.

Each `$4` is also the complete profile cap, not `$4` per creative treatment. Replicate every initial
treatment as an active ad inside each comparable ad set. Meta may distribute a profile's delivery
unevenly among those ads; the policy must record actual treatment-level impressions, spend, and
missing exposure rather than claim an equal creative split. If guaranteed equal treatment spend is
required, use a separately authorized, guarded cell-budget or official experiment design and
include every cell in the allocation math. The current target batch does not pretend multi-ad
delivery is equal allocation.

Five launches also require five separate launch/setup credits under the current cost model, so the
initial batch needs 2,005 Meta credits: 2,000 media credits plus five setup credits. Any separate
creative-generation cost is additional. Record the provider start/end window and hard cap explicitly;
never rely on a daily budget alone to enforce the `$20` total.
Every distinct rendered UGC treatment also consumes its own authorized creative-generation cost; do
not hide those costs inside media spend.

If only four profiles are genuinely eligible, keep the fifth card visibly blocked/inapplicable and
do not call the production v1 batch wrapper. Its contract requires all five profiles eligible and
launch-ready; changing to a four-profile portfolio is a different future contract and policy choice.

Meta's general guidance suggests enough budget over at least seven days for delivery learning. It also
describes a daily budget as an average that may spend above the daily amount on some days while staying
inside the weekly multiple. Apply those as delivery priors only when business authority and expected
event volume make them sensible; the local credit and total-spend rails remain hard limits.

## Observation window and pause boundary

This policy collects metrics; it does not make a post-observation performance decision.

### Maturity gate

The observation bundle distinguishes mature from immature evidence using:

- spend reached `review_after_spend_usd`;
- runtime reached `minimum_runtime_hours`;
- the declared conversion-lag window elapsed;
- attribution and the primary event remain healthy;
- the declared `minimum_primary_events` exists when a rate/CPA estimate is reported.
- the predeclared treatment spend/impression floor was met for a treatment-level comparison.

Immature evidence is still handed off with its maturity flags. The static policy does not cut, scale,
or reallocate from CTR, CPC, CPM, clicks, landing-page views, conversions, CPA, CAC, or ROAS.

### Immediate hard pause

Pause immediately, without waiting for maturity, when any of these is verified:

- the landing page is unavailable, redirects to the wrong destination, or its signup/checkout path is
  broken;
- the event, pixel/dataset, deduplication, domain, objective, promoted object, or attribution boundary
  is materially wrong;
- copy, creative, claim, rights, disclosure, audience, geography, or restricted-category state is
  noncompliant or materially misconfigured;
- the authorized daily/total spend cap is reached, provider billing is anomalous, or local/provider
  ownership state cannot be reconciled;
- an authorized operator explicitly requests pause.

For a profile-specific hard stop, pause that profile, mark it `interrupted`, and keep the other cohort
members running unless the issue compromises shared measurement or safety. For a treatment-specific
creative/compliance failure, pause every replica of that treatment across all profiles and mark those
cells `interrupted`. For a shared landing-page, tracking, authority, or campaign-wide compliance
failure, pause the entire batch. Synchronize and settle as soon as safely possible.

At `observation_end_at` or the batch total-spend cap, pause the complete cohort together, verify status
when guarded reads exist, synchronize one final common window, settle actual spend, release unused
reservations, and write the observation bundle. This is a planned budget boundary, not a performance
verdict. If no external update arrives, the batch remains paused.

The current local evaluator's `good/watch/bad` receipt is diagnostic only. It lacks these maturity,
business-economics, conversion-lag, and attribution gates and cannot by itself authorize a budget
change, performance pause, creative mutation, or policy update.

## Audience policy

### Geography

Use business evidence or operator constraints and supply the result explicitly. The handler's US
fallback is legacy mechanical behavior, not a policy decision.

### Broad versus detailed

- Prefer broad geo-first targeting when no verified audience evidence exists.
- Use verified numeric interest/audience IDs only; never invent IDs from names.
- Meta's general guidance says delivery often benefits from broad audiences and cites roughly 2–10
  million people as a useful range, with interest targeting recommended only when the audience remains
  at least around 2 million. Treat this as a prior, not a universal requirement.
- Preserve strict business constraints such as geography, minimum age, language, and exclusions.
- Distinguish Advantage+ suggestions from hard controls using the live schema.

### Exploration

Do not change audience and copy/creative simultaneously when the question is which message worked.
Explore audience only after the creative/message treatment is stable enough or explicitly record the
confounding change.

## Placement policy

The handler's current fallback is Facebook Feed. It is legacy mechanical behavior, not an executable
policy decision or Advantage+ placements; supply the selected targeting/placement object explicitly.

For an expanded live schema:

- prefer automated/broad placements as an initial prior when assets support them;
- otherwise select a complete manual placement object;
- ensure Page/Instagram identity and asset ratios work across every surface;
- Meta's general guidance suggests Advantage+ placements or at least six manual placements, but do
  not force unsupported placements merely to hit a count;
- read back applied state through a guarded MCP surface before claiming placement truth.

## Copy and creative policy

Every treatment in the launch matrix should have:

- one immutable copy artifact;
- one immutable creative manifest;
- for video UGC, one approved post-render output review bound to that exact manifest;
- one primary promise;
- a real asset and video thumbnail;
- exact destination and CTA;
- one variant ID used across all artifacts and Meta metadata;
- claim evidence and compliance status;
- a phase, hypothesis, parent, and changed dimensions.

### Selection

- Use the same reviewed `control`/`copy-challenger`/`ugc-production-challenger` matrix across every
  initial campaign candidate when the route is UGC video.
- For static or existing visuals, use the same control/copy-challenger pair and exact visual across
  every candidate.
- Keep offer, destination, and every non-isolated dimension consistent; a copy challenger may change
  its angle/hook/promise only within verified truth, and a UGC challenger must preserve control copy.
- Copy controls lead with verifiable adoption proof; the challenger explores a
  different proof form; non-proof angles run only as declared hypothesis rows.
- Leads open on a named customer story; aggregate-count copy is retired from the lead
  slot (support-lines and challengers only); successors default to story-led.
- Route copy choices to `meta-copy-policy`.
- Route UGC production choices to `meta-ugc-policy`.
- Route execution to `ad-copy` and `ugc-video-ad`.
- Treat UGC manifest preflight review as production authorization, not final render approval. Inspect
  the actual output and require the separate approved `ugc_review_path` before Meta staging.
- Use `static-ad-creative-generator` for image rendering, not for silently inventing Meta copy.

### Format exploration

Do not compare UGC video and static image inside the initial campaign-configuration batch; choose one
shared `creative_kind` so format is not an additional axis. The UGC production challenger changes a
production treatment within UGC; it does not change `creative_kind`. Any later format
exploration requires an explicit external RL/semantic-gradient or operator decision and a new recorded
batch/variant.

## Pixel and attribution

### Isolation

- A shared pixel/dataset may serve multiple business sites only with business-specific isolation.
- Create/use a business-specific custom conversion for optimization/reporting.
- Never optimize/report against generic purchases pooled across businesses.
- Verify domain controls and dataset traffic permissions.
- Preserve a stable business discriminator in eligible server events.

For PURCHASE, require the canonical boundary:

- private deterministic CAPI-only event;
- exact authoritative business hostname;
- `/app` checkout-return path;
- current shared pixel;
- provider readback of the exact rule;
- verified live instrumentation.

If any part is missing/stale, purchase count, value, CAC, and ROAS are unavailable.

### Health

Pixel health needs independent site and provider proof.

Site proof:

- served page contains expected initialization;
- origin and edge/static paths are checked;
- browser+CAPI duplicates share event name and `event_id` where deduplication applies.

Provider proof:

- dataset exists and receives recent events;
- expected funnel events are present;
- volume has no unexplained cliff;
- business custom conversion is registered/firing;
- Event Match Quality, deduplication, and errors are inspected.

`business_meta_pixel_verify` proves only a minimum subset. Do not promote it to a full certificate.

### Attribution sanity

- Keep Meta delivery metrics separate from business attribution.
- Investigate material Meta-versus-server conversion divergence when valid join keys exist.
- Browser-only measurement is lossy; CAPI must deduplicate where duplicated.
- Never substitute shared raw purchases or `$0` when business attribution is unavailable.

## Observation policy

- Sync every candidate and treatment over the same explicit start/end timestamps.
- Keep one level, object, and non-overlapping window per row.
- Do not sum overlapping `last_7d` snapshots as independent periods.
- Account for current first-row dedup behavior.
- Prefer link-click metrics to generic clicks when available.
- Record the profile's actual optimization event; do not substitute generic clicks.
- Preserve provider diagnostics, learning status, attribution gaps, and missing actions as raw facts.

### Observation bundle

For every candidate/treatment cell, hand off:

- planned and actual activation timestamps plus common observation timestamps;
- allocated daily budget, reservation, actual spend, reach, impressions, frequency, CPM, and delivery
  status;
- link clicks, link CTR, CPC, landing-page views, landing-page-view rate, and cost per landing-page
  view when available;
- native optimization events and cost per native event;
- shared landing-page business events, CVR, CPA/CAC, value, and ROAS when attribution supports them;
- conversion-lag and maturity fields;
- pixel/dataset/attribution health and every missing-metric reason;
- exact campaign/ad-set/ad IDs, profile settings, `treatment_id`, isolated policy axis, copy/creative
  artifacts, and policy versions.

Also emit treatment aggregates across profiles and profile aggregates across treatments, preserving
the underlying rows and actual exposure. Do not manufacture a rate for a cell with no denominator or
pretend uneven ad delivery was an equal creative split.

Do not attach static `good`, `watch`, `poor`, `winner`, `loser`, `scale`, or `pause_performance`
labels. The current automated evaluator may still write its legacy diagnostic receipt, but that
receipt is not part of the policy decision and cannot mutate the batch.

## Experiment lineage for an external semantic gradient

This policy contains no RL learner, reward function, gradient, or update loop. It preserves and emits
the joins the external metrics-based semantic-gradient system consumes: business/product/offer,
objective and targeting, authorized budget and schedule, capability snapshot,
experiment/variant/parent identity, phase, hypothesis, changed dimensions, pinned policy versions,
cold-start `portfolio_id`/`batch_id`/`profile_id`/`profile_key`/position/`treatment_id`/isolated axis,
copy/creative/review paths, provider
object IDs, spend decisions, pause reasons, and truthful status receipts.

Keep delivery metrics, downstream attribution, creative generation cost, and media spend as separate
facts. Missing evidence remains missing rather than becoming zero. A run pins its policy versions from
planning through terminal settlement. It must not rewrite those policies from its own observations.
The separate system may use the immutable observation bundle to propose a new reviewed policy version
or live action. Implementing, scoring, or approving that system is outside this bundle. Until its
output arrives, the batch remains paused at its planned cap and this policy takes no next action.

## Run history

`metrics/roas/meta.md` joins the Meta plan with each sync result. The plan preserves asset path,
copy artifact, creative manifest, UGC output review, experiment, variant, parent, phase, and changed
dimensions.

Use the latest compatible entry for the current treatment. A historical operator threshold may appear
in the file; business targets and this fuller evidence policy take precedence. `ROAS n/a` is not a
hold/cut signal.

## Compliance and truth

- Use real assets, destinations, offers, claims, IDs, and results.
- Never fabricate delivery, clicks, conversions, attribution, or provider state.
- For policy-composed video UGC, require the reviewed copy artifact, canonical manifest, and approved
  post-render `ugc_review_path` together; a pre-render review cannot certify final pixels or audio.
- Review copy/creative/targeting/destination against current Meta requirements before live launch.
- Block special categories, regional/DSA obligations, or restricted products until the live wrapper
  exposes required fields and evidence.
- Do not infer Meta review approval, effective paused status, non-delivery, or delivery from object
  creation. Local UGC output approval proves only the enumerated post-render QA checks.
- Keep tokens in Safebox and every action business-scoped, idempotent, credit-bounded, and
  receipt-backed.
- Use pause; hard delete remains outside the public wrapper.

## Current official guidance

These primary Meta sources inform the priors above; live schemas and account facts still control
execution:

- [Meta Performance 5](https://www.facebook.com/business/ads/performance-marketing)
- [Meta campaign objectives](https://www.facebook.com/business/ads/ad-objectives)
- [Meta Sales objective](https://www.facebook.com/business/ads/ad-objectives/sales)
- [Meta Traffic objective](https://www.facebook.com/business/ads/ad-objectives/traffic)
- [Meta lead forms](https://www.facebook.com/business/ads/ad-objectives/lead-generation/lead-ads-with-forms)
- [Meta lead messaging](https://www.facebook.com/business/ads/ad-objectives/lead-generation/lead-ads-with-messaging)
- [Meta Advantage+ app campaigns](https://www.facebook.com/business/ads/meta-advantage-plus/app-campaigns)
- [Meta budget and schedule guidance](https://www.facebook.com/business/ads/pricing)
- [Meta audience targeting guidance](https://www.facebook.com/business/ads/ad-targeting)
- [Meta ad-set simplification](https://www.facebook.com/business/ads/ad-set-structure)
- [Meta ad creative guidance](https://www.facebook.com/business/ads/ad-creative)
- [Meta Advantage+ audience](https://www.facebook.com/business/ads/meta-advantage-plus/audience)
- [Meta Advantage+ campaign budget](https://www.facebook.com/business/ads/meta-advantage-plus/budget)
- [Official Meta Business SDK](https://github.com/facebook/facebook-python-business-sdk)
- [Official Meta Business SDK releases](https://github.com/facebook/facebook-python-business-sdk/releases)
