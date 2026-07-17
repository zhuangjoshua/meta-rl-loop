# Meta Ads Policy — v8

GOAL this policy serves: maximize settled ROAS (revenue / spend) for a $29/mo
product ("Formflow"); purchases are the goal event.

## Creative
Run three video ads per batch. Declared angle families: benefit, outcome,
count (adoption numbers), story (named customer).

The outcome angle, having won matched cells per dollar on goal-layer proxies
in two consecutive batches, holds favored status: two of the three ad slots
run outcome-led variants by default. The third slot rotates through the other
families (benefit, count, story) so every family keeps getting funded; a
single batch's winner earns no extra slots. Any angle may earn or lose
favored status only by winning (or ceasing to win) matched cells per dollar
in two consecutive batches.

The demo variant (screen-recording of the product working) is the mandatory
production standard: every ad runs as a demo variant. No-demo production is
retired from rotation; it is revisited only if the demo variant's settled
per-dollar signup advantage in matched cells disappears across two
consecutive batches.

## Coverage ledger
The policy tracks, for every axis it owns, which values have had a funded test
and which have not. An unpriced value is UNKNOWN, not neutral; no axis may be
declared settled, and no value retired, while values of that axis remain
unpriced. Unpriced values take priority for test slots at the cheapest viable
budget.

- Angle family: benefit PRICED; outcome PRICED; story PRICED; count PRICED.
- Demo variant: no-demo PRICED; demo PRICED.
- Audience: broad PRICED; interest_biztools PRICED; interest_niche PRICED.
- Objective: pageviews PRICED; leads PRICED; sales PRICED; clicks PRICED.
- Budget mode: fixed PRICED; auto PRICED and BANNED (two consecutive funded
  batches yielded zero trusted measurements).

## Campaigns
Fixed-mode cells only, $200 total per batch: a pageviews campaign (broad), a
leads campaign (broad), and sales campaigns. Auto-budget mode is banned: it
buys no trusted measurements. Audiences available: broad, interest_biztools,
interest_niche.

Starvation floor: no cell may be funded below $25. Fund fewer cells rather
than starve any cell into unreadability.

Sales budget split: interest audiences (interest_biztools and interest_niche)
receive about 70% of the sales budget by default, and interest_niche — the
portfolio's best per-dollar producer of goal-layer proxies (site demos and
purchases) — receives about half of the TOTAL sales budget. Biztools and
broad keep floor-level funding plus the remainder. Broad sales stays funded,
held at the $25 floor, as a goal-layer accumulator: it is not to be judged or
demoted until its accumulated sales spend is large enough to expect at least
3 purchases at the portfolio's observed purchase rate.

## Judgment
Judge only settled results. An ad/cell with no measurement is unknown, not
bad. Never repeat an approach that measurably failed. Unreadable cells
(starved/short-window) are evidence of nothing. Judge cells per-dollar, never
raw counts; never compare conversion rates across differently-targeted cells.

Cell quality is judged on per-dollar goal-layer proxies: site demos plus
purchases. Sign-ups and CPL are a tiebreaker only — they may not by
themselves justify a budget, angle, or cell verdict, because sign-ups
convert to purchases too rarely to stand in for the goal.

## Process
After each batch, the semantic gradient reads all receipts and revises this
policy; a noise schedule picks the revision dose. Every batch's full receipts
accumulate as evidence.
