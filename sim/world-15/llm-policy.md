# Meta Ads Policy — v8

GOAL this policy serves: maximize settled ROAS (revenue / spend) for a $29/mo
product ("Formflow"); purchases are the goal event.

## Coverage ledger (drives batch composition)
Declared axes and values:
- Angle family: benefit (priced), outcome (priced), story (priced), count (priced).
- Demo variant: off (priced), on (priced).
- Audience: broad (priced), interest_biztools (priced), interest_niche (priced).
- Objective: pageviews (priced), leads (priced), sales (priced).
- Budget mode: fixed (priced), auto (funded repeatedly, unreadable every time —
  no-trust windows only; treated as attempted-unreadable).
An unpriced value is UNKNOWN, not neutral. No axis may be declared settled and
no value retired while sibling values remain unpriced.

## Creative
Run three video ads per batch. Declared angle families: benefit, outcome,
count (adoption numbers), story (named customer — set named_story). Slot rule:
every batch runs TWO story ads, each a distinct named customer and setting
(never reuse a prior batch's named customer), and their matched-cell
per-dollar comparison is logged each batch so the story tilt self-audits. The
third slot defaults to a THIRD distinct story execution; the benefit ad with
the demo variant (screen-recording of the product working) returns every third
batch as a scored challenger, and any challenger family (benefit-demo,
outcome, or count) owed a decision test near its cumulative spend bar takes
the third slot that batch as a refreshed execution — in every case the third
slot's per-dollar signups and purchases are compared against the weaker story
slot within matched cells, logged in the evidence file. No family is demoted from the declared vocabulary
until it has sustained underproduction across roughly $200 of cumulative
funded spend.

## Campaigns — fixed-mode only
$200 total per batch. Only fixed-mode cells may be funded. Auto-budget mode may
return only through an explicit future policy revision that includes a concrete
readability plan (how its results would settle into trusted windows); until
then no batch includes an auto cell, and its former budget goes to fixed sales
cells. Default budget split: pageviews (broad) $10, leads (broad) $30, and
$160 across sales campaigns spanning the audiences as sales-broad $75,
sales-biztools $40, sales-niche $45 — sales-broad, the observed per-dollar
purchase leader, takes the largest sales share. Audiences: broad, interest_biztools, interest_niche.
Pageviews stays funded at this probe level rather than being cut: no objective
is demoted until its cumulative funded spend would expect at least three
purchases at the portfolio's observed purchase rate.

## Judgment
Judge only settled results, per dollar, within matched cells (same objective,
same audience). An ad/cell with no measurement is unknown, not bad. Never
repeat an approach that measurably failed. Unreadable cells (starved,
short-window, or no-trust auto windows) are evidence of nothing. Refinement
among priced values waits until the coverage ledger for that axis is empty.
Purchases settle sparsely, so goal-layer verdicts (crediting or blaming a
creative family, cell, or budget shape for purchases) require pooled
matched-cell spend large enough to expect at least three purchases at the
portfolio's observed purchase rate, pooled across eras as needed;
signup-layer reads may still steer slot allocation between such verdicts.

## Process
After each batch, the semantic gradient reads all receipts and revises this
policy; a noise schedule picks the revision dose. Every batch's full receipts
accumulate as evidence.
