# World-8 LLM lineage

## Iteration 1 (policy v0, seed 801)
THESIS (design-class, coverage rule): The batch produced zero purchases and only
thin signup counts, so no creative or audience refinement is licensed; meanwhile
four axis values the policy owns have never been funded (proof=count, demo=true,
audience=interest_niche, objective=clicks) and are UNKNOWN, not neutral. The
next batches must price these unswept values at cheapest viable dose before any
thesis may refine among the already-swept ones. Falsifier: funded tests of these
values produce receipts; if the sweep is funded and they underperform matched
cells per-dollar, they may then be demoted.

Doses (smallest to boldest):
1. Add one sentence: the next batch's third ad slot goes to the unpriced count angle.
2. Next batch funds count angle AND one demo=true variant in its three ad slots.
3. Lean: batch design prioritizes unswept values — count, demo, and an interest_niche sales cell get funded before refining swept values.
4. Default: creative slots rotate to unpriced families (count + demo) and campaigns carry an interest_niche sales cell ($30+) each batch until every axis value is priced.
5. Rule: policy keeps an explicit coverage ledger; no angle/audience/objective may be judged or retired until count, demo, interest_niche, and clicks each have a funded test; next batch dedicates roughly half its budget to unpriced values.
6. Rewrite the experiment structure around coverage: batches are sweeps until the ledger is complete — ads cover count/demo variants, portfolio restructured to price interest_niche and clicks alongside sales, and the coverage ledger governs all batch design.

DRAW (it1): dose 5. ADOPTED: v0 -> v1 — added a binding coverage-ledger section
(explicit axis vocabulary; unfunded = UNKNOWN; no judging/retiring until count,
demo, interest_niche, clicks are each funded; next batch dedicates ~half budget
to unpriced values) and strengthened Judgment with per-dollar / matched-cell
comparison rules.

## Iteration 2 (policy v1, seed 802)
THESIS (design-class, replicated 2 eras): Auto-budget sales cells have come back
NO-TRUST (auto-window, unreadable) in both funded batches, absorbing ~$70 that
produced evidence of nothing; the portfolio should stop funding auto mode and
reallocate that budget to readable fixed cells. Falsifier: a trusted, readable
auto-cell receipt would break the premise. (Coverage note: both budget-mode
values have now been priced; the sweep of count/demo/niche/clicks completed and
returns signup-layer receipts only — no purchase anywhere, so no angle/audience
refinement is graded above this structural waste.)

Doses (smallest to boldest):
1. Add one sentence: the auto-budget cell defaults to skipped next batch; its budget goes to fixed cells.
2. Next batches omit the auto cell; auto may return only with an explicit plan for reading it.
3. Lean: portfolio drops auto mode; the budget-mode axis is marked settled-against-auto pending a new readability mechanism.
4. Default: remove auto from the standard portfolio; fixed-only budgeting is the default and auto needs a fresh design thesis to reinstate.
5. Rule: budget mode is fixed-only; auto is retired as unreadable twice-funded; its $30-40 reallocates to readable fixed sales cells each batch.
6. Rewrite Campaigns around fixed-only readable cells: every cell sized for a readable dose, auto deleted from the axis vocabulary entirely.

DRAW (it2): dose 5. ADOPTED: v1 -> v2 — Campaigns section now fixed-only:
auto-budget mode retired (funded twice, unreadable both times), its $30-40
share reallocates to readable fixed sales cells; reinstatement requires a new
design thesis with a readability mechanism.

## Iteration 3 (policy v2, seed 803)
THESIS (pattern-class, small): The portfolio's only settled purchase in three
eras came from a sales-objective cell, while pageviews/leads/clicks cells
($200 cumulative) have produced signups but never a goal event — a slight lean
of budget share toward sales cells is supported, and nothing bolder: at the
observed portfolio rate (1 purchase / $600) no signup-layer objective has the
funded spend that would expect >=3 purchases, so none may be demoted or
retired. Confirming experiment: raise the sales share while keeping pv/leads
at readable floors; the lean is falsified if purchases stop arriving
preferentially in sales cells as their spend share grows.

Doses (smallest to boldest, capped at pattern-class magnitude — no retirement,
no core rewrite; every dose keeps the >=3-expected-purchase demotion guard):
1. Shift $10 from pageviews to the sales cells; nothing else changes.
2. Sales cells take at least 60% of batch budget; pv and leads each keep a $25 readable floor.
3. Sales cells take ~70% of budget; pv and leads each keep a $30 floor -> reduce to $30 combined floors of $30 each only if budget allows; guard unchanged.
4. Sales cells take ~75% of budget; pv and leads each keep a $25 floor.
5. Sales-heavy default: sales cells take ~80%; only ONE signup-layer cell runs per batch (alternating pageviews/leads), $40, as instrumentation.
6. Sales-first portfolio: sales cells take ~85%; a single $30 signup-layer instrumentation cell remains per batch; signup-layer objectives stay eligible and guarded, never retired.

DRAW (it3): dose 4. ADOPTED: v2 -> v3 — Campaigns now sales-heavy: sales cells
~75% of budget (~$150), pageviews and leads each keep a $25 readable floor,
with an explicit guard that signup-layer objectives cannot be demoted/retired
until their funded spend would expect >=3 purchases at the observed portfolio
rate.

## Iteration 4 (policy v3, seed 804)
THESIS (design-class, replicated 4 eras): Four eras and $800 have produced one
settled purchase because the batch shape dilutes spend into 15 ad-cells of
$8-20 each — below goal-event readability — so the portfolio should
concentrate into fewer, larger cells until the purchase layer is readable.
Falsifier: if concentrated cells at 2-3x per-ad-cell spend still return ~zero
purchases over the coming eras, dilution was not the blocker. (Runner-up
finding, logged as a wash: across 10 matched cell-pairs in two eras the demo
variant raises CTR in 8/10 but ties on signups (4 v 4) and buys (0 v 1) —
no goal-layer edge; demo is neutral, not preferred. No audience/objective may
be demoted: at 1 purchase/$800, no axis value's funded spend expects >=3.)

Doses (smallest to boldest):
1. Add one sentence: sales spend narrows from three audience cells to two per batch, raising per-cell dose.
2. Batches run 2 ads instead of 3, raising every per-ad-cell dose by half.
3. Lean: 2 ads and at most 4 cells per batch; no ad-cell below $20.
4. Default: portfolio is 2 ads across 2 sales cells plus the pv/leads floors; every sales ad-cell gets at least $30.
5. Rule: hard readability floor — no batch creates an ad-cell under $25; met with 2 ads, at most 4 cells, sales ad-cells at $35+.
6. Concentration rewrite: 2 ads; one alternating $25 signup-layer instrumentation cell (pv and leads alternate eras, both staying funded and eligible); the remaining ~$175 in two sales cells so every sales ad-cell gets $40+.

DRAW (it4): dose 4. ADOPTED: v3 -> v4 — concentration default: two ads per
batch, sales ~$150 in exactly two sales cells (every sales ad-cell >=$30),
pv/leads $25 floors unchanged, demotion guard unchanged.

## Iteration 5 (policy v4, seed 805)
THESIS (pattern-class): Five eras, $1000, every angle/audience/objective mix
tried: signups arrive steadily (40) but the purchase layer stays dark (1 buy),
so at the observed rate NO creative or audience refinement is readable and
further per-era redirection just destroys comparability; the direction the
evidence supports is stabilizing the concentrated shape until a readable
purchase base accumulates. Confirming experiment: hold the current shape for
consecutive eras; if stable $37+ sales ad-cells still show <=1 purchase per
$400, the constraint is the destination/product, not the mix, and goal-layer
expectations must be re-priced. Falsifier: a shape-change era that outbuys the
stable ones per-dollar.

Doses (smallest to boldest, capped at pattern-class magnitude):
1. Add one sentence: the portfolio shape holds next era unless a settled purchase arrives.
2. Shape may change only every second era while cumulative purchases < 3.
3. Lean: freeze shape AND repeat the incumbent ad pair until 2 settled purchases accumulate; only prompt wording may vary lightly.
4. Default: no budget-shape or audience change until $400 stable-shape spend or 2 purchases accumulate; at most one creative slot varies per era.
5. Rule: goal-layer verdicts require $600 stable-shape spend or 3 purchases; until then batches are repeats with at most one varied slot.
6. Stability charter: portfolio locked (2 ads; broad + biztools sales cells; pv/leads floors) for at least the next two eras; one creative slot may rotate; every refinement thesis is deferred until 3 purchases or $600 stable spend.

DRAW (it5): dose 3. ADOPTED: v4 -> v5 — added a binding Stability section:
shape frozen and incumbent ad pair repeated (light prompt variation only)
until two settled purchases accumulate under the current shape; signup-layer
volume never licenses redirection.

## Iteration 6 (policy v5, seed 806)
THESIS (pair-class): The two angles are audience-complementary — in matched
sales cells the outcome angle carried broad (5 signups, 2 buys vs story's
1/0) while the named story carried interest_biztools (5 signups, 1 buy vs
outcome's 0/0) — so the policy should keep the pair as a matched structure,
judge each angle only in its matched cell, and weight budget by matched-cell
per-dollar buys. One-difference comparisons within two matched cells, both
purchase-bearing, $37.50 per arm. Falsifier: a future era where the pairing's
matched cells fail to outbuy the off-matched combination per-dollar, or the
asymmetry reverses. (Stability note: 3 settled purchases accumulated under the
frozen shape — the unfreeze condition is met.)

Doses (smallest to boldest):
1. Add one sentence: each angle is judged only in its matched sales cell (outcome<->broad, story<->biztools); off-audience failure never cuts an ad.
2. Also keep the incumbent outcome+story pair as the complementary default for the two sales cells.
3. Lean: creative slots are written angle-by-audience — one outcome-family ad aimed at broad, one story-family ad aimed at biztools, prompts tuned to their audience.
4. Default: complementary portfolio — outcome-for-broad and story-for-biztools; sales budget stays 50/50; a replacement ad must beat the incumbent in its OWN matched cell.
5. Rule: sales budget follows matched-cell per-dollar buys (lean broad ~60/40 now), both matched cells always funded; angle swaps happen only via matched-cell wins.
6. Core rewrite: creative+campaign core becomes the matched-pair engine — broad×outcome $90 and biztools×story $60 ad-cell pairs, pv/leads floors intact, all creative work is within-family prompt refinement judged per matched cell.

DRAW (it6): dose 4. ADOPTED: v5 -> v6 — Creative section now the complementary
matched-pair default (outcome<->broad sales, story<->biztools sales), each
angle judged only in its matched cell, replacements must beat the incumbent in
its own matched cell; sales budget pinned 50/50 across the two matched cells.

## Iteration 7 (policy v6, seed 807)
THESIS (pattern-class, small, cross-era so directional): The only purchase-
bearing era ran specific winning prompt texts; the very next era reworded both
winners ("light prompt variation") and both matched cells collapsed (outcome
in sales-broad 5 signups/2 buys -> 0/0; story in biztools 5/1 -> 1/0), so the
policy should stop treating wording as free variation — incumbent winners
re-run VERBATIM, and any wording change is a challenger that must pass the
matched-cell replacement gate. Confirming experiment: re-run the exact winning
prompt texts; recovery toward the winning era's levels confirms wording drift
as the regression source. Falsifier: verbatim re-runs performing no better
than reworded ones. (Era confound acknowledged: different week, one era each
way; hence a small thesis, middle doses at most.)

Doses (smallest to boldest, capped at pattern-class magnitude):
1. Add one sentence: incumbent winners re-run with their exact winning prompt text, not lightly reworded.
2. Also strike the "light prompt variation" allowance from the Stability section.
3. Lean: winning prompt text is a versioned frozen asset; any wording change is a challenger entering through the matched-cell replacement gate.
4. Default: incumbents re-run verbatim every batch; a challenger may take one slot at most every second batch and is judged only in its matched cell.
5. Rule: verbatim-incumbent regime with challenger cadence (one slot, every second batch); wording drift outside a declared challenger is a policy violation.
6. Policy carries the exact incumbent prompt texts inline as versioned assets; all creative change flows through the challenger gate at that cadence.

DRAW (it7): dose 3. ADOPTED: v6 -> v7 — winning prompt text is now a
versioned, frozen asset re-run verbatim; any wording change is a challenger
that must pass the matched-cell replacement gate; the Stability section's
light-variation allowance replaced accordingly.

## Iteration 8 (policy v7, seed 808)
THESIS (pattern-class): With the matched-pair structure now replicated across
two purchase-bearing eras (broad-outcome 8 signups/1 buy vs story 0 this era,
5/2 vs 1/0 before; biztools-story 4 vs 1 and 5/1 vs 0/0), the broad-outcome
matched cell is the portfolio's per-dollar engine (~$37.5/buy cumulative vs
~$112.5/buy for biztools-story), so the sales split should lean toward broad
while keeping the biztools matched cell funded. Middle doses only: two eras,
four total sales-cell buys, and era comparisons are directional. Confirming
experiment: run the leaned split; falsified if biztools-story matches or beats
broad-outcome per-dollar on settled buys at the new weights. (The verbatim
re-run also confirmed the wording-freeze thesis: matched cells recovered
0->8 and 1->4 signups when exact winning texts returned.)

Doses (smallest to boldest, capped at pattern-class magnitude — both matched
cells stay funded, nothing retired):
1. Sales split leans 55/45 toward the broad matched cell.
2. Sales split 60/40 toward broad.
3. Sales split 65/35 toward broad; the biztools ad-cell never drops below $25.
4. Sales split follows cumulative per-dollar settled buys, recomputed each era, bounded at 70/30 with a $25 minority ad-cell floor.
5. Same recomputed split bounded at 75/25, $25 floor.
6. Same recomputed split bounded at 80/20, $20 floor.

DRAW (it8): dose 5. ADOPTED: v7 -> v8 — the sales split between the two
matched cells is now recomputed each batch from cumulative per-dollar settled
buys, bounded at 75/25, with a $25 minority ad-cell floor; both matched cells
always funded.
