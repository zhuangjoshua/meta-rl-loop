# Iteration 1 (policy v0 -> ?)

THESIS (coverage/design, small — evidence thin): The batch produced zero purchases, so nothing at the goal layer is settled; the only readable signal is signup-layer and it is mixed across matched cells (outcome best in leads-broad, count best in sales-broad, benefit weakest everywhere). The policy's declared axes still hold unpriced values — story (named), the demo production variant, and the interest_niche audience — so the evidence best supports committing the experiment slot to pricing those unswept values before any angle preference is earned. Falsifier: if funded tests of story/demo/niche settle and none changes portfolio economics, the sweep-first commitment was wasted spend.

DOSES:
1. Add one sentence to Judgment: story, demo, and interest_niche have never been funded; silence about them is absence of data, never evidence against them.
2. Add a coverage ledger to the policy (swept: benefit/outcome/count angles, broad/biztools audiences, fixed/auto modes; unswept: story, demo, interest_niche) and a lean to service it.
3. Rule: every batch must fund at least one unswept axis value at cheapest viable dose until the ledger is clear; story, demo, niche are next.
4. Mandate batch 2 explicitly: run story(named) and a demo variant among the three ads, and add an interest_niche sales cell at cheapest viable budget.
5. As dose 4, plus: no angle-family bias may enter the policy until all four families and demo have settled funded tests; reserve ~25% of each batch's budget as the sweep slot until then.
6. Rewrite Creative+Campaigns around a sweep-first program: batches 2-3 are dedicated coverage batches (story, demo, interest_niche, remaining objective values); refinement among already-priced values is forbidden until every declared axis value has a funded, settled test.
DRAW: dose 4. ADOPTED: v0 -> v1. Creative now mandates a named-story ad and a demo variant while unpriced; Campaigns now mandates an interest_niche sales cell at cheapest viable budget while unpriced; Judgment gains the unknown-not-neutral coverage sentence.

# Iteration 2 (policy v1 -> ?)

THESIS (design, replicated 2 eras): The auto-budget sales campaign has produced only NO-TRUST short-window receipts in both eras (~$70 total spend, zero readable evidence), while every fixed cell settled; the portfolio should stop funding auto mode and put that budget into fixed cells sized to be readable. Falsifier: a future auto cell that settles trusted receipts at competitive economics would prove the cut wrong. (Runner-up, not adopted: story out-signed outcome in 3 matched cells — signup-layer lean only, revisit next era.)

DOSES:
1. Add a sentence: auto-budget cells have twice returned only unreadable short-window receipts; lean away from funding auto.
2. Cap the auto campaign at $20 per batch until an auto cell settles a trusted receipt.
3. Drop auto from the next batch's portfolio; reallocate its budget to fixed sales cells.
4. Remove auto from the standard portfolio; it may return only through an explicit single-cell re-pricing test.
5. Remove auto entirely and add a readability rule: every campaign cell must be sized so each ad in it can spend at least $10.
6. Rewrite Campaigns around readability as an eligibility rule: fixed-only portfolio, per-ad-per-cell spend floor of $10, auto mode retired; no future campaign type may be funded at a size that cannot settle.
DRAW: dose 5. ADOPTED: v1 -> v2. Auto-budget mode removed from the portfolio entirely; new readability rule: every cell sized so each ad can spend at least $10. Coverage mandates from v1 remain (story/demo/niche now priced, so their conditional clauses are satisfied).

# Iteration 3 (policy v2 -> ?)

THESIS (pair, replicated): Plain named-customer story is causally the strongest creative: it won or tied every matched cell against outcome (prior era), count, and its own demo overlay (this era: 8 signups / 4 demos / the run's only purchase vs 2/1/0 for story+demo and 1/0/0 for count), across two eras and ~10 one-difference comparisons. Bias creative toward plain named-story; the demo overlay measurably drags the story chassis. Falsifier: a future matched cell where a non-story angle or a story+demo ad out-converts plain story per dollar.

DOSES:
1. Add a lean: story with a real named customer has out-signed comparators two eras running; favor it when in doubt.
2. Make story the default lead angle: at least one named-story ad per batch.
3. Two of three ads per batch are distinct named-story ads; the third slot rotates a comparator; do not pair the demo overlay with story.
4. Story-first rule: every ad leads with a named customer story unless a slot is explicitly a comparator test; drop the demo overlay (lost to plain story in five matched cells).
5. As dose 4, plus tilt budget toward where story converts: sales-broad becomes the largest cell.
6. Rewrite Creative as a named-story portfolio: three distinct named-customer stories per batch, no demo overlays, comparator angles only via an explicit re-pricing slot, sales-broad anchored primary.
DRAW: dose 5. ADOPTED: v2 -> v3. Creative is story-first (named-story every ad unless an explicit comparator slot), demo overlay retired as measurably failed, and sales-broad anchored as the largest budget cell. The satisfied v1 sweep clauses are dropped (story/demo/niche all priced).

# Iteration 4 (policy v3 -> ?)

THESIS (pattern, cross-era, direction only): Every settled purchase of the run (3 buys, $87 revenue) has come from the sales-broad cell across two eras and two different story ads (~1 purchase per $43 there), while ~$270 of era-3/4 spend in all other cells produced zero; the evidence supports tilting budget share toward sales-broad, without retiring any cell (no other cell's funded spend yet reaches the >=3-expected-purchase demotion threshold at the portfolio rate of ~1/$133). Confirming experiment: an enlarged sales-broad share holds roughly one purchase per ~$45; if purchases-per-dollar decays at the larger size or appear elsewhere, the tilt is wrong.

DOSES:
1. Add a lean: settled purchases have so far come only from sales-broad; prefer broad when splitting sales budget.
2. Default: sales-broad receives at least $80 of the $200 batch.
3. sales-broad receives at least $90; interest audiences hold at readable minimums.
4. sales-broad receives at least $100 (half the batch); all other cells sized at or near the $30 readable floor.
5. sales-broad receives at least $110; pageviews and leads trimmed to the readable floor.
6. Allocation rule: sales-broad receives at least $120 (60%) and every other cell sits at its $30 readable floor; the tilt stands only while sales-broad sustains ~1 purchase per ~$45 of spend, and reverts if that rate decays or purchases settle elsewhere.
DRAW: dose 4. ADOPTED: v3 -> v4. Campaigns now allocate at least $100 of the $200 batch to sales-broad, with all other cells at or near the $30 readable floor; no cell retired (demotion threshold not reached).

# Iteration 5 (policy v4 -> ?)

THESIS (pattern, era-aggregate + the tilt's own confirmation metric; middle doses max): The enlarged sales-broad cell underdelivered its predicted purchase rate (1 buy on $110 vs ~2.4 expected at the prior ~1/$43 rate), and the era aggregate regressed (0.14 vs 0.29), so the evidence supports holding the sales-broad tilt at its current size rather than escalating — purchases do not scale linearly with the tilt. Confirming experiment: a sales-broad era at $100-110 that settles 2+ purchases would re-license escalation; another sub-rate era would license softening. Falsifier: >=2 purchases at the current size next era.

DOSES:
1. Add a sentence: at $110 sales-broad came in under its predicted purchase rate; treat further escalation as unsupported.
2. Cap sales-broad at $110 per batch until it settles an era with two or more purchases.
3. Cap sales-broad at $110 and track purchases-per-dollar each era in evidence as the tilt's standing confirmation metric.
4. Hold sales-broad at $100-110, and adopt the rule that any cell's allocation may be increased only if that cell held its predicted purchase rate in the most recent era.
5. Soften: sales-broad runs at exactly the $100 minimum, the freed budget returns to an interest sales cell, and escalation is forbidden until a $100 era settles two or more purchases.
6. Growth-by-proof rule: no cell's budget may grow until it has held its per-dollar purchase rate at its current size for a full era; sales-broad freezes at $100, every other cell at its floor.
DRAW: dose 5. ADOPTED: v4 -> v5. sales-broad softened to exactly $100 with escalation forbidden until a $100 era settles 2+ purchases; the freed budget returns to an interest-audience sales cell; floors unchanged.

# Iteration 6 (policy v5 -> ?)

THESIS (pattern, portfolio-wide across 6 eras; middle doses max): Signup and demo production has never predicted purchases — 81 signups and 32 demos across $1200 have yielded 4 buys, era purchase counts (0,0,1,2,1,0) do not track top-funnel output (this era: 15 signups/7 demos/0 buys; the 8-signup/6-demo Maya cell bought nothing), and allocation moves sized on signup signals did not move revenue. The bottleneck is after the click, and per-era reallocation on top-funnel signals is fitting noise. Confirming experiment (named): hold portfolio and creative constant for two consecutive eras; if purchase counts swing widely under identical structure, the noise diagnosis is confirmed and signup-driven reallocation was over-fitting. Falsifier: a cell whose signup/demo rate forecasts its next-era purchases.

DOSES:
1. Add a sentence: signup and demo counts have not predicted purchases; treat them as diagnostic only, never allocative.
2. Rule: allocation and keep/cut judgments may cite only settled purchases and revenue; signup/demo metrics are diagnostic only.
3. As dose 2, plus hold the current portfolio shape unchanged for the next era to measure purchase noise under identical structure.
4. As dose 2, plus freeze both portfolio shape and the creative slate for a two-era accumulation window; mid-window reallocation is forbidden.
5. Stability rule: allocation may change only on evidence pooled over at least two consecutive eras of identical structure; signup/demo metrics carry no allocative force; the current shape freezes meanwhile.
6. As dose 5, plus the policy states the diagnosis outright — buyers are scarce at the destination and top-funnel abundance is not progress — and forbids any future revision from citing signup/CPL improvements as success.
DRAW: dose 4. ADOPTED: v5 -> v6. Judgment now restricts allocative evidence to settled purchases/revenue (signups/demos diagnostic only), and a new Stability window section freezes portfolio shape and creative slate for a two-batch accumulation window.

# Iteration 7 (policy v6 -> ?)

THESIS (pattern, cross-era; middle doses max; all effects post-window): Story creatives decay with repeated exposure — the Maya ad, run five consecutive eras, fell from 9 signups (eras 4-5) to 4 (era 6) to 1 (era 7) with collapsed CTR, while the younger Sam and Priya ads held their signup production in the same frozen cells; the slate needs age-based rotation once the stability window closes. Confirming experiment: the window's second identical batch — if Maya stays collapsed while Sam/Priya hold again, fatigue is confirmed; if Maya rebounds under identical structure, the decay was noise. (Diagnostic only, not allocative: purchases remain the sole allocative evidence.)

DOSES:
1. Add a sentence: creatives reused across many eras have decayed; note ad age as diagnostic context.
2. Post-window lean: refresh at least one aged creative per batch with a new named story.
3. Post-window rule: no creative runs more than three consecutive eras; replace expiring ads with new named stories.
4. Post-window rotation: at most one carryover ad older than two eras per batch; new named stories fill the remaining slots.
5. As dose 4, plus track per-ad age in evidence and retire immediately (at window close) any ad whose signups fall below half its peak era.
6. As dose 5, plus ad age becomes a standing judgment input: an aged ad must re-earn its slot through a fresh matched comparison before running again.
DRAW: dose 4. ADOPTED: v6 -> v7. Creative gains a post-window age-rotation rule: at most one carryover ad older than two eras per batch, new named stories fill the rest. The stability window itself is untouched and still binds the next batch.

# Iteration 8 (policy v7 -> ?)

THESIS (pattern-refutation via the named confirming experiment; middle doses max): The fatigue diagnosis behind the age-rotation rule failed its own pre-registered test — under the window's identical second batch the five-era-old Maya ad rebounded from 1 signup to 6 while structure, slate, and budgets were frozen — so the era-7 collapse was noise, not decay, and the hard rotation rule should be softened to match what the receipts actually carry. Falsifier: an aged ad showing pooled below-half-peak production across two consecutive eras would re-license rotation as a rule. (Window verdict, diagnostic: purchases under identical structure were 0 then 1 — single-era purchase differences at this budget are noise, as the stability rule assumed.)

DOSES:
1. Add a sentence: the aged ad rebounded under identical structure; ad-age decay is unconfirmed.
2. Soften rotation to a lean: prefer fresh named stories when producing new ads, but an aged ad may keep its slot.
3. Replace the carryover cap: refresh a creative only after it declines for two consecutive eras, judged against its peak.
4. Remove the carryover cap entirely; ad age is diagnostic context only, and retiring a creative requires a pooled two-era decline.
5. As dose 4, plus creative retirement is held to the same pooled-two-era evidence bar the policy already applies to allocation.
6. As dose 5, plus a standing correction rule: any policy rule whose named confirming experiment fails is softened or struck at the next revision.
DRAW: dose 2. ADOPTED: v7 -> v8. The hard age-rotation rule is softened to a lean (prefer fresh named stories; age alone does not retire an ad) after its named confirming experiment failed. Stability-window and purchases-only judgment rules unchanged.

