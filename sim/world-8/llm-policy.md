# Meta Ads Policy — v8

GOAL this policy serves: maximize settled ROAS (revenue / spend) for a $29/mo
product ("Formflow"); purchases are the goal event.

## Creative
Run two video ads per batch (concentration: fewer, larger ad-cells so the
purchase layer is readable). The default pair is complementary and
audience-matched: an outcome-family ad matched to the broad sales cell and
a named-story ad matched to the interest_biztools sales cell. Each angle is
judged only in its matched sales cell; failing its off-matched audience
never cuts an ad. A replacement ad enters only by beating the incumbent in
the incumbent's OWN matched cell on per-dollar settled results.
An incumbent's winning prompt text is a versioned, frozen asset: it re-runs
VERBATIM. Any wording change, however light, is a new challenger ad and must
enter through the matched-cell replacement gate; wording is never free
variation. The words may lead with any angle the writer
judges best (no earned bias yet). Declared angle families: benefit, outcome,
count (adoption numbers), story (named customer). A demo variant (screen-
recording of the product working) is an available production option, untested.

## Campaigns
Portfolio per batch, $200 total. Sales-objective cells take roughly 75% of
the budget (~$150) spread across exactly TWO sales cells (broad and interest_biztools, the
matched cells of the default pair). The split between them is recomputed
each batch in proportion to cumulative per-dollar settled buys in each
matched cell, bounded at 75/25 at most, and the minority matched cell's
ad-cell spend never drops below $25 — both matched cells always stay
funded; a pageviews campaign (broad) and a
leads campaign (broad) each keep a $25 readable floor. Signup-layer objectives (pageviews,
leads, clicks) remain eligible and may not be demoted or retired until
their cumulative funded spend would expect at least 3 purchases at the
portfolio's observed purchase rate.
Audiences available: broad, interest_biztools, interest_niche.
Budget mode is fixed-only. Auto-budget mode is retired: funded twice, it
returned only unreadable (no-trust) cells both times, so its $30-40 share
reallocates to readable fixed sales cells each batch. Auto may be
reinstated only by a new design thesis that includes a mechanism for
reading its cells.

## Coverage ledger (binding)
The policy maintains an explicit coverage ledger over every axis it owns:
angle family (benefit, outcome, count, story), demo variant (on/off),
objective (pageviews, leads, sales, clicks), audience (broad,
interest_biztools, interest_niche), budget mode (fixed, auto). An axis value
with no funded test is UNKNOWN, not neutral. No angle, audience, or objective
may be judged, refined among, or retired until count, demo, interest_niche,
and clicks each have a funded test. The next batch dedicates roughly half its
budget to still-unpriced values, at the cheapest viable dose each.

## Stability (binding while the purchase layer is dark)
Until two settled purchases have accumulated under the current portfolio
shape, the campaign/budget shape is frozen and each batch repeats the
incumbent ad pair (same angle families, same demo settings, verbatim prompt
text — wording changes go through the challenger gate). Signup-layer volume
alone never licenses a redirection of shape or creative.

## Judgment
Judge only settled results. An ad/cell with no measurement is unknown, not
bad. Never repeat an approach that measurably failed. Unreadable cells
(starved/short-window) are evidence of nothing. Judge cells per-dollar, never
raw counts; never compare conversion rates across differently-targeted cells.

## Process
After each batch, the semantic gradient reads all receipts and revises this
policy; a noise schedule picks the revision dose. Every batch's full receipts
accumulate as evidence.
