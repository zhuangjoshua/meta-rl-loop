# World-7 LLM lineage

## Iteration 1 (policy v0, seed 701)

THESIS (design/coverage class — licensed by the coverage rule; evidence is the coverage ledger itself): Four policy-owned option values have never been funded — count angle, demo variant, interest_niche audience, clicks objective — so they are unknown, not neutral, and batch design must price them before any angle refinement; the only outcome signal so far (outcome-angle ads produced both demos and top matched-cell CTRs at 0 purchases / $200) is too thin to act on.

Doses:
1. Add one sentence to Creative noting count and demo are unpriced and the next batch should include a count-angle ad.
2. Add a rule: every batch, at least one ad slot prices a previously unpriced creative option (count first, then demo), at cheapest viable budget.
3. Rule: the next batch must fund BOTH a count-angle ad and a demo variant; policy carries a coverage ledger listing priced vs unpriced creative options.
4. Dose 3 plus audiences/objectives: interest_niche gets a funded sales cell and clicks gets one cheap funded cell next batch; coverage ledger covers all axes (angle, demo, audience, objective, budget mode).
5. Rewrite Creative and Campaigns as sweep-first: until every declared value of every axis is priced, batch design allocates slots to unpriced values at cheapest viable dose; refinement among priced values is deferred.
6. Policy core becomes the coverage ledger: batches are mechanically composed to price all unpriced axis values (count, demo, interest_niche, clicks) immediately, no earned creative biases may be written until the sweep completes, and every future axis addition enters the ledger unpriced.

DRAW: dose 4. ADOPTED: policy v1 — coverage ledger added across all axes (angle, demo, audience, objective, budget mode); next batch must fund count-angle ad, demo variant, an interest_niche sales cell, and a cheap clicks cell.

## Iteration 2 (policy v1, seed 702)

THESIS (pair class — one-difference comparison, ~$47/side across 5 matched cells): Within the outcome angle, the demo variant outperforms the plain variant at the signup layer (4 signups + 2 site-demos vs 2 + 1 at equal matched spend, best CPL $6.67 on sales-niche) — favor demo-variant production. Evidence is signup-layer only (0 purchases anywhere), so this earns a lean-to-default, not a creative identity; falsified if matched demo cells stop out-signing plain cells per dollar.

Doses:
1. Add a lean: demo variant showed cheaper matched-cell signups; include a demo ad each batch when practical.
2. Rule: every batch must include at least one demo-variant ad.
3. Demo becomes the default for the majority of ads (2 of 3); each batch keeps one no-demo control.
4. All ads run as demo variants except one no-demo control every other batch.
5. Demo is the mandatory production standard; no-demo retired from rotation, revisited only if demo's settled per-dollar signup advantage disappears across two batches.
6. Rewrite Creative around demonstration: every ad is a screen-recording demo, angle copy subordinated to showing the product working; no-demo retired entirely.

DRAW: dose 5. ADOPTED: policy v2 — demo variant is the mandatory production standard, no-demo retired (revisit trigger: demo's matched-cell per-dollar signup advantage gone two consecutive batches). Ledger bookkeeping: count/demo/interest_niche/clicks marked PRICED; satisfied one-shot sweep mandates removed.

## Iteration 3 (policy v2, seed 703)

THESIS (design class — replicated across two eras, explicitly licensed): Auto-mode sales cells have consumed $80 across two consecutive funded batches and produced zero trusted measurements ([NO-TRUST auto-window] on every row, both iterations) — spend that buys no evidence; the portfolio should drop auto mode and put all budget in fixed, readable cells. Falsified if a future auto cell run under the same window produces trusted rows. (Runner-up finding, logged not adopted: the run's only purchase came from outcome-demo in sales-niche — one goal event, too thin to outrank a replicated design finding.)

Doses:
1. Add a sentence: auto has yielded no trusted measurements in two funded batches; fund auto only after every fixed cell is funded.
2. Auto is removed from the standard portfolio; it may return only with a written readability plan.
3. Auto retired; the full $200 goes to fixed cells, split as before.
4. Auto retired and marked UNREADABLE-RETIRED in the coverage ledger; every funded cell must be fixed-mode.
5. Fixed-only portfolio plus a starvation floor: no cell may be funded below a minimum readable budget; auto banned.
6. Rewrite Campaigns around measurement yield: fixed-only, minimum readable budget per cell, and any cell or mode that yields no trusted measurement in two consecutive funded batches is automatically retired.

DRAW: dose 5. ADOPTED: policy v3 — fixed-only portfolio (auto banned as buying no trusted measurements, ledger marks auto BANNED) plus a $25 starvation floor per cell; fund fewer cells rather than starve one.

## Iteration 4 (policy v3, seed 704)

THESIS (pattern class — replicated across all four batches, middle doses max): Sales spend in interest audiences outperforms broad sales per dollar at both layers (interest: $310 -> 2 purchases, 19 signups, CPL ~$14-19; broad sales: $150 -> 0 purchases, 5 signups, CPL ~$30) — lean the sales budget toward interest_biztools and interest_niche while keeping broad sales funded at the floor as a goal-layer accumulator. Confirming experiment: hold broad sales at $25/batch until its cumulative sales spend reaches $300, then judge its purchase yield against interest cells. Falsified if broad sales matches interest per-dollar yield as spend accumulates. (Runner-up, blocked: benefit angle is per-dollar worst in matched cells three batches running, but its ~$190 funded spend expects <1 purchase at the portfolio rate of ~1 per $400 — the demotion cap forbids retiring it yet.)

Doses:
1. Add a lean: prefer interest audiences when splitting sales budget; broad sales keeps at least floor funding.
2. Default split: interest cells get ~60% of sales budget; broad sales at least $25.
3. Interest cells get ~70% of sales budget by default; broad sales held at the $25 floor until accumulated spend licenses a goal-layer verdict.
4. Rule: sales budget defaults to 75% interest (split between biztools and niche); broad sales at floor; pageviews/leads unchanged.
5. Interest sales cells are the portfolio core (>=75% of sales budget); broad sales runs at floor purely as a goal-layer accumulator; policy names the confirming test (judge broad when its cumulative sales spend reaches $300).
6. Interest-first portfolio: biztools and niche each get dedicated sales cells every batch totaling >=80% of sales budget; broad sales exactly $25/batch as accumulating control, verdict deferred until $300 cumulative.

DRAW: dose 3. ADOPTED: policy v4 — interest audiences get ~70% of sales budget by default; broad sales stays funded at the $25 floor as a goal-layer accumulator, not judged until its spend expects >=3 purchases at the observed rate.

## Iteration 5 (policy v4, seed 705)

THESIS (pattern class — wash/tie finding, replicated across the three demo-era batches, middle doses max): Once every ad is a demo, angle family is not an active lever: each demo batch had a different matched-cell winner (outcome it3, story it4, count it5) and the three purchases came from three different angles in three different cells — so rotate angle families evenly instead of chasing last batch's winner, and grant no angle favored status without replication. Confirming experiment: keep running three different angles as demos in matched cells; the thesis is falsified if one angle wins matched cells per dollar in two consecutive batches. (Noted, not adopted: interest_niche went 0-signup on $65 this era after leading it2-4 — one era, directional only.)

Doses:
1. Add a sentence: no angle hierarchy has survived replication post-demo; writers should not chase last batch's winning angle.
2. Rule: rotate angle families evenly across batches; no angle may be favored or dropped on single-batch results.
3. Angle-neutrality default: each batch runs three different angle families (all as demos), cycling through all four families across consecutive batches; single-batch winners earn no extra slots.
4. Dose 3 plus a verdict standard: an angle earns favored status only by winning matched cells per dollar in two consecutive batches.
5. Angle declared noise until proven otherwise: demo is the only earned creative lever; angles assigned by strict rotation; any future angle thesis requires two-batch replication including goal-layer evidence.
6. Dose 5 plus standing instrumentation: the coverage ledger records each batch's per-angle matched-cell per-dollar wins, so the two-batch replication record is directly readable.

DRAW: dose 4. ADOPTED: policy v5 — angle-neutrality default (three different families per batch, cycling all four across batches; no single-batch favorites) plus the verdict standard: favored status requires winning matched cells per dollar in two consecutive batches.

## Iteration 6 (policy v5, seed 706)

THESIS (pattern class — portfolio-wide across all six eras, middle doses max): The bottleneck is after the click — 45 signups have become only 3 purchases (~7%, ~1 purchase per $400) — so signup counts/CPL are a false summit and cell judgment should anchor on per-dollar goal-layer proxies (site demos and purchases), with signups demoted to tiebreaker. Confirming experiment: track per-cell demo+purchase yield over the next two batches; falsified if signup-rich cells convert to purchases at parity with demo-rich cells. (Blocked runner-ups: benefit has never won a matched cell in four funded batches but its ~$256 spend expects <1 purchase, so the demotion cap holds; within matched sales cells the interest tilt still edges broad per dollar, so no walk-back is licensed despite two flat eras.)

Doses:
1. Add a sentence to Judgment: signup/CPL wins are weak evidence; prefer per-dollar demos and purchases where available.
2. Rule: no budget or angle decision may cite signups alone when any matched cell has demo or purchase data.
3. Judgment re-anchored: cell quality is judged on per-dollar demos plus purchases; signups are a tiebreaker only.
4. Dose 3 plus a budget consequence: each batch tilts sales budget toward the cells with the best per-dollar demo+purchase yield over the trailing two batches, floors intact.
5. Goal-proxy regime: demos and purchases are the only decision currency; the trailing-two-batch demo+purchase yield drives the whole sales split, floors intact.
6. Dose 5 plus standing instrumentation: the ledger records each cell's per-dollar demo+purchase yield every batch, and any surface with zero yield across three consecutive funded batches is flagged for a future design thesis.

DRAW: dose 3. ADOPTED: policy v6 — cell quality judged on per-dollar site demos plus purchases; signups/CPL demoted to tiebreaker and may not alone justify any verdict.

## Iteration 7 (policy v6, seed 707)

THESIS (pattern class — multi-era, judged in v6's goal-proxy currency, middle doses max): interest_niche sales is the portfolio's best per-dollar goal-proxy cell (roughly 9 of the 18 site demos on ~$355 — about 1 demo/$39, plus 1 of 3 purchases; demos in 5 of its 6 funded eras, vs ~1/$83 for biztools and 0 ever for leads) — concentrate the sales budget further into niche while floors keep the other cells measurable. Confirming experiment: two more funded batches; falsified if niche's per-dollar demo+purchase yield falls to or below biztools/broad over those batches. (Blocked runner-up: leads-broad has zero demos and zero purchases across seven funded eras ($193), but expects only ~0.4 purchases at the portfolio rate of ~1/$467, so the demotion cap forbids cutting it.)

Doses:
1. Add a sentence: niche is the best per-dollar demo producer; prefer it at the margin within the interest split.
2. Default: niche takes the larger share (~60%) of the interest sales budget.
3. Rule: niche receives about half of the total sales budget; biztools and broad keep floor-level funding plus remainder.
4. Niche is the core sales cell at ~60% of sales budget; biztools slightly above floor; broad at floor.
5. Niche takes ALL sales budget above the $25 floors held by broad and biztools.
6. Dose 5 plus standing instrumentation: the ledger logs each cell's per-dollar demo+purchase yield every batch, with the two-batch falsification test written into the policy.

DRAW: dose 3. ADOPTED: policy v7 — interest_niche receives about half of the total sales budget (best per-dollar goal-proxy cell); biztools and broad keep floors plus remainder; broad's accumulator status unchanged.

## Iteration 8 (policy v7, seed 708)

THESIS (pair class — matched-cell, one-difference on angle, judged in the policy's goal-proxy currency; evidence honest-sized to small-middle doses since goal-proxy events are ~1/batch): The outcome angle has now won the core niche matched cell per dollar in two consecutive batches (only site-demo producer in both it7 and it8, plus the it8 signup tiebreaks: CPL $5.56 biztools, $6.25 niche) — by the policy's own two-consecutive-batch verdict standard, outcome earns favored status among angles. Falsified if outcome stops winning matched cells on demo+purchase per dollar over the next two batches.

Doses:
1. Add a sentence: outcome has met the two-batch matched-cell standard; writers lean outcome at the margin.
2. Outcome earns favored status: one guaranteed slot every batch; the other two slots keep rotating.
3. Outcome favored: a guaranteed slot every batch and outcome anchors the core (niche) cell's creative; other slots rotate.
4. Outcome becomes the default lead: two of three slots run outcome-led variants; one slot rotates the other families.
5. Outcome is the portfolio's lead angle: all slots outcome-led except one rotating exploration slot every other batch.
6. Creative core rewritten around outcome-led demos; other angle families run only as periodic probes.

DRAW: dose 4. ADOPTED: policy v8 — outcome is the default lead angle (two of three slots outcome-led); the third slot rotates the other families; favored status still gained/lost only via two consecutive matched-cell per-dollar verdicts.
