# Meta Ads Policy — v8

GOAL this policy serves: maximize settled ROAS (revenue / spend) for a $29/mo
product ("Formflow"); purchases are the goal event.

## Coverage ledger (binding)
Declared axes and values, with pricing status:
- Angle family: benefit PRICED, outcome PRICED (parked), story(named) PRICED, count PRICED.
- Demo variant: demo=true PRICED, demo=false PRICED.
- Audience: broad PRICED, interest_biztools PRICED, interest_niche PRICED (parked).
- Objective: pageviews PRICED, leads PRICED, sales PRICED, clicks PRICED.
- Budget mode: fixed PRICED; auto BANNED (funded twice, unreadable both times).
An unpriced value is UNKNOWN, not neutral. No axis value may be refined among,
retired, or declared settled while any value of that axis remains unpriced.
Update this ledger every batch.

## Funnel ledger (binding)
Each era, record per cell: spend, sign-ups, demo requests, settled purchases.
Judgment about the post-sign-up step uses this ledger only. The purchase-first
budget rule below stands while purchases per dollar improve or hold; if
purchases stay flat while sign-up volume falls across funded eras, the
purchase-first tilt is falsified and must be revisited.

## Creative — count-demo identity
The portfolio's creative core is the count+demo family: adoption-numbers copy
told over a screen-recording demo of the product working, closing on a direct
buy-now CTA for the $29/mo offer. Every slate slot is a count-demo variant
with its own distinct hook (different opening line, number framing, or shown
workflow), EXCEPT the protected story slot: one non-demo story(named) ad stays
in the slate for as long as story holds a settled purchase and its funded
spend has not reached the demotion threshold below. Any non-count challenger
creative may enter only through a recorded probe rationale in the batch spec.
Every ad's closing CTA sells the $29/mo purchase directly, not a bare sign-up.

## Campaigns
$200 total per batch, fixed-budget cells only. Purchase-first allocation:
sales-objective cells hold at least $120; exactly one leads (broad) cell is
kept as a sign-up control. Within the sales pool, the split is roughly 30/70
broad/biztools, with broad holding a floor of at least $30 so it stays priced. Auto-budget mode is
banned absent explicit operator override (funded twice, unreadable both
times).

## Standing items
- interest_niche audience: PARKED (eras 2/4/5 — $120 for 1 sign-up, 0 demo
  requests, 0 purchases at matched sales cells). Revival: surplus-era probe of
  about $20, or a redesigned targeting thesis. Waited: 3 eras.
- outcome angle: REVIVAL CONDITION FIRED (count-demo variants settled no
  purchase in the latest funded era) — outcome-demo is again eligible for a
  slate slot under the challenger-probe rule. Waited: 1 era before firing.

## Judgment
Judge only settled results. An ad/cell with no measurement is unknown, not
bad. Never repeat an approach that measurably failed. Unreadable cells
(starved/short-window) are evidence of nothing. Judge cells per-dollar, never
raw counts; never compare conversion rates across differently-targeted cells.
Sign-up-layer evidence never demotes a purchase-layer finding: do not demote
an axis value holding settled purchases until its funded spend would expect at
least 3 purchases at the portfolio's observed purchase rate. Parked values are
revivable and are never retired on sign-up evidence alone.

## Process
After each batch, the semantic gradient reads all receipts and revises this
policy; a noise schedule picks the revision dose. Every batch's full receipts
accumulate as evidence.
