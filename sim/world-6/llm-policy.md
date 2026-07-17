# Meta Ads Policy — v8

GOAL this policy serves: maximize settled ROAS (revenue / spend) for a $29/mo
product ("Formflow"); purchases are the goal event.

## Creative
The creative identity is the named customer story: every ad is built around a
named customer, told in their words with their results. Run three video ads
per batch, all named-story-led. Every ad carries the demo layer by default —
the customer's story runs over a screen-recording of the product working.
Every other batch, exactly one slot runs as a plain (no-demo) story control,
judged per-dollar against the demo slots in matched cells. Other declared angle families — benefit,
outcome, count (adoption numbers) — run only as explicitly funded coverage
tests of values that have never been priced; a coverage test may replace one
story slot in a batch. A demo variant (screen-recording of the product
working) is an available production option and may be layered onto any ad.
Coverage ledger: all declared angle families (benefit, outcome, count, story)
and the demo treatment have received funded tests.

## Campaigns
Every dollar sits in a fixed-budget campaign whose receipts are per-cell
trusted; auto-budget mode is retired — the platform never allocates budget on
this policy's behalf, because unreadable spend buys no evidence. The budget
shape is broad-first: the broad audience carries the portfolio — the
pageviews campaign, the leads campaign, and the large majority of sales
spend all run broad. Interest audiences (interest_biztools, interest_niche)
run only as small probes of $10 each per batch, and a probe graduates to
real budget only when it shows a purchase-layer signal (a buy, or a demo
rate matching broad's at equal spend). interest_biztools has shown that
signal and holds graduated budget; interest_niche remains a probe.
Portfolio per batch, $200 total across fixed campaigns only, split:
sales-broad $150 (floor), sales-biztools $15, leads-broad $15,
pageviews-broad $10, and the $10 interest_niche probe. $150 is the highest
budget at which sales-broad's per-dollar buy rate has held; a higher floor
measurably degraded it.

## Judgment
Judge only settled results. An ad/cell with no measurement is unknown, not
bad. Never repeat an approach that measurably failed. Unreadable cells
(starved/short-window) are evidence of nothing. Judge cells per-dollar and
compare only matched cells (same objective, same audience). Sign-up-layer
wins do not demote purchase-layer hypotheses.

## Process
After each batch, the semantic gradient reads all receipts and revises this
policy; a noise schedule picks the revision dose. Every batch's full receipts
accumulate as evidence.
