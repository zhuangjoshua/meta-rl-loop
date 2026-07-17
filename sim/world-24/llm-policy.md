# Meta Ads Policy — v8

GOAL this policy serves: maximize settled ROAS (revenue / spend) for a $29/mo
product ("Formflow"); purchases are the goal event.

## Creative
Run three video ads per batch. Every ad leads with a real named customer
story (named_story) unless a slot is explicitly declared a comparator test
against another angle family. Declared angle families: benefit, outcome,
count (adoption numbers), story (named customer). Do not produce the demo
(screen-recording) overlay: it measurably underperformed the plain story
treatment in matched cells and a measurably failed approach is not repeated.
Prefer fresh named-customer stories when producing new ads, but an aged ad
may keep its slot: a one-era collapse of an old creative rebounded under
identical structure, so ad-age decay is unconfirmed and age alone does not
retire an ad.

## Campaigns
Standard portfolio per batch, $200 total, fixed budgets only: a pageviews
campaign (broad), a leads campaign (broad), and sales campaigns across
audiences. Audiences available: broad, interest_biztools, interest_niche.
The sales-broad cell is the portfolio's primary cell: it runs at exactly
$100 of the $200 batch — purchases did not scale linearly when it was
enlarged past this size — and the budget above the floors returns to an
interest-audience sales cell. Raising sales-broad above $100 is forbidden
until a $100 era settles two or more purchases. Every other cell is sized
at or near the $30 readable floor. Settled purchases have so far come only
from sales-broad; no other cell is retired, because no other cell's funded
spend has yet reached the three-expected-purchase threshold at the
portfolio's observed purchase rate. Auto-budget mode is not funded — it has
repeatedly returned only unreadable short-window receipts. Every campaign
cell must be sized so each ad in it can spend at least $10; do not fund a
cell too small to settle.

## Judgment
Judge only settled results. An ad/cell with no measurement is unknown, not
bad. Never repeat an approach that measurably failed. Unreadable cells
(starved/short-window) are evidence of nothing. An axis value that has never
been funded is unknown, not neutral; silence about it is absence of data,
never evidence against it. Allocation and keep/cut judgments may cite only
settled purchases and revenue; signup and demo counts are diagnostic
context, never allocative evidence — they have not predicted purchases.

## Stability window
The portfolio shape and the creative slate are frozen for a two-batch
accumulation window: run the same campaign cells at the same budgets with
the same ads for two consecutive batches, and change neither mid-window.
Purchase events are rare, so evidence about them is read over the pooled
window, not a single batch.

## Process
After each batch, the semantic gradient reads all receipts and revises this
policy; a noise schedule picks the revision dose. Every batch's full receipts
accumulate as evidence.
