# Meta Ads Policy — v8

GOAL this policy serves: maximize settled ROAS (revenue / spend) for a $29/mo
product ("Formflow"); purchases are the goal event.

## Creative
Run three video ads per batch. Benefit is the default angle family: each
batch runs two benefit-family variants (the two differing in a single
element, e.g. demo vs non-demo or hook wording). The third slot runs a
non-benefit family (outcome, count, or story) justified as a probe of a
parked family, accumulating funded spend toward that family's demotion
threshold. Declared angle families: benefit, outcome, count (adoption
numbers), story (named customer). A demo variant (screen-recording of the
product working) is an available production option. Never relaunch an
execution that measurably failed.

## Campaigns
Standard cold-start portfolio per batch, $200 total: traffic-layer campaigns
(broad), fixed-budget sales campaigns, and optionally an auto-budget sales
campaign (platform allocates between audiences). Audiences available: broad,
interest_biztools, interest_niche. Traffic layer: the pageviews campaign
absorbs most traffic-layer budget (~$65); the leads campaign runs at a
minimum-readable $15 probe until it produces a first purchase. Sales
allocation: the three sales cells (interest_niche, broad, interest_biztools)
are funded equally at ~$40 each. Sales cells are never unfunded until their
cumulative funded spend would expect at least 3 purchases at the portfolio's
observed purchase rate; only then may an audience be demoted. The budget
shape freezes across each two-batch window: allocations may change only at
window boundaries. The portfolio always contains a ~$25 coverage slot that
funds the cheapest-viable test of an unpriced axis value; with no unpriced
values remaining, the slot is dormant and its budget returns to the general
pool.

## Judgment
Judge only settled results. An ad/cell with no measurement is unknown, not
bad. Never repeat an approach that measurably failed. Unreadable cells
(starved/short-window) are evidence of nothing. Axis values that have never
had a funded test are UNKNOWN, not neutral; no axis may be declared settled
while values of it remain unpriced. All declared axis values currently have
funded tests. Judge cells per-dollar, never on raw counts, and never compare
conversion rates across differently-targeted cells. An angle family may be
demoted only once its cumulative funded spend would expect at least 3
purchases at the portfolio's observed rate. No sales audience currently
holds leading-hypothesis status. At the portfolio's observed purchase rate a
single batch expects fewer than one purchase, so single-batch purchase
counts are below readability: no allocation change may cite a single batch's
purchase difference — goal-layer comparisons require an accumulated window
of at least two batches.

## Process
After each batch, the semantic gradient reads all receipts and revises this
policy; a noise schedule picks the revision dose. Every batch's full receipts
accumulate as evidence.
