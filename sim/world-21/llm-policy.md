# Meta Ads Policy — v8

GOAL this policy serves: maximize settled ROAS (revenue / spend) for a $29/mo
product ("Formflow"); purchases are the goal event.

## Axis vocabulary (declared, exhaustive)
- Angle family: benefit, outcome, count (adoption numbers), story (named customer).
- Demo variant: demo=true (screen-recording of product working), demo=false.
- Objective: clicks, pageviews, leads, sales.
- Audience: broad, interest_biztools, interest_niche.
- Budget mode: fixed, auto (auto currently retired — see Campaigns).
An axis value with no funded, settled test is UNKNOWN — never neutral, never
settled, never retired.

## Creative
Run three video ads per batch. The story angle (named customer) is the
steadiest per-dollar signup producer and holds two slots by default. A
matched-cell test showed a fresh named-story variant beating a long-running
incumbent in every scoring cell, so the family lead does not belong to any
single creative: the current anchor is the most recent matched-cell winner,
and the second story slot runs a NEW, different named-customer story each
batch. An anchor that loses its matched cells to the fresh variant is retired
from the anchor slot and returns only via a future matched-cell win. The
third slot rotates among the other families (benefit, outcome, count) so no
family goes unwatched.
A matched one-difference pair found the plain variant matched or beat the demo
variant in every tested cell, so all creative leans demo=false by default;
demo footage may still take a slot opportunistically, and a matched-pair win
by demo in trusted cells would restore it. Sweep-first still applies: any angle family or
demo variant that has never had a funded test takes a slot at the cheapest
viable dose before tested values are repeated.

## Campaigns
$200 total per batch, all in fixed-budget cells. Auto-budget mode is retired as
measurably unreadable: repeated funded auto cells settled only as untrusted
auto-window receipts, so auto spend buys no evidence. Its former budget share
is redistributed across the fixed cells. Auto may be re-admitted only by an
explicit future policy revision. Cells are chosen sweep-first: any objective or
audience value that has never had a funded test gets a cell at the cheapest
viable dose (~$25-40) before budget is re-spent on already-tested cells. Only
once every campaign axis value has been priced may the portfolio concentrate on
the best-reading cells.

The purchase layer is unreadable at thin per-cell spend, so the portfolio
leans toward concentration: the interest_biztools sales cell is paused —
parked as UNKNOWN in standing items, not judged — and its budget share moves
to the broad sales cell. A purchase-readable budget would un-park it.

## Judgment
Judge only settled results, per-dollar within matched cells (same objective,
same audience). An ad/cell with no measurement is unknown, not bad. Never
repeat an approach that measurably failed. Unreadable cells (starved/
short-window) are evidence of nothing.

## Process
Batches are coverage batches until every declared axis value has a funded,
settled test; refinement begins only after the sweep is complete. After each
batch, the semantic gradient reads all receipts and revises this policy; a
noise schedule picks the revision dose. Every batch's full receipts accumulate
as evidence, including a coverage ledger of which axis values have been priced.
