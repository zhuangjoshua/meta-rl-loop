# World-16 LLM lineage

## Iteration 1 (policy v0 -> draw)
THESIS (coverage/design class, replicated-across-eras not required because it is a coverage obligation, dose cap: middle): The declared angle vocabulary is not fully priced — `count` has never had a funded test (and demo has only one funded crossing) — so before refining among angles the experiment slot must service unswept values; the only directional creative signal (story ads lead signup-per-dollar in matched sales cells: $3.33 and $4.44 CPL vs benefit/outcome mostly $13+ or unread) is signup-layer only, with zero purchases portfolio-wide, and cannot displace goal-layer judgment. Falsifier: a funded count ad pricing in-line or better than story in a matched sales cell.
DOSES:
1. Add one sentence: the next batch must include one count-family ad in a sales cell.
2. Rule: any declared-but-unpriced angle family gets one funded ad before any family is repeated twice.
3. Dose 2 + the policy keeps an explicit coverage ledger (angle x demo) and marks count and demo-crossings unpriced.
4. Each batch reserves one of the three ad slots as a coverage slot (unpriced angle or demo crossing) until the ledger is fully priced; remaining slots lean story on signup evidence.
5. Creative section restructured around the coverage ledger: enumerate angle x demo cells, forbid refinement/retirement of any angle while a cell is unpriced; story is interim lead ad.
6. Creative selection is fully ledger-driven: batches are composed to minimize unpriced cells first, refinement forbidden until every angle and demo crossing is priced; story interim lead.
DRAW: dose 6 (seed 16001). ADOPTED: policy v1 — creative selection fully coverage-ledger driven (angle x demo ledger in policy text; unpriced cells funded first; refinement/retirement forbidden while any angle unpriced; story interim lead, signup-layer only); matched-cell per-dollar judgment codified.

## Iteration 2 (policy v1 -> draw)
THESIS (design class; replicated: auto windows unreadable in BOTH eras run so far, ~$75 total spend returning zero trusted evidence): The auto-budget sales cell is structurally unreadable and should be removed from the standard portfolio, with its budget reallocated to fixed readable cells. Falsifier: a future auto cell producing a trusted window at comparable budget. (Noted, not adopted as thesis: benefit-demo produced the portfolio's only purchase, in sales-niche on $11.67 — one goal event, a lean at most; story x demo and count x demo remain the last unpriced ledger cells.)
DOSES:
1. Add a sentence: auto has never produced a trusted window; cap auto at $20 per batch.
2. Auto capped at $20 and must produce one trusted window before any budget increase.
3. Auto dropped from the standard portfolio; re-addable only as an explicitly declared experiment cell.
4. Auto removed entirely; its budget reallocated evenly across the fixed sales cells.
5. Auto removed; portfolio restructured sales-weighted: pageviews and leads capped at $25 each, remainder split across fixed sales cells on all three audiences.
6. Portfolio rewritten fixed-mode-only: sales-only fixed cells across the three audiences plus one small leads probe; auto banned pending a future policy revision.
DRAW: dose 4 (seed 16002). ADOPTED: policy v2 — auto-budget cell removed entirely, budget reallocated evenly across fixed sales cells; portfolio is fixed-mode only. Ledger bookkeeping updated: count x no-demo priced; audiences all priced; remaining unpriced cells count x demo, story x demo.

## Iteration 3 (policy v2 -> draw)
THESIS (pair class — one-difference angle comparison at demo=true within matched cells; may claim causation, any dose): Benefit-angle demo creative causes more purchases per dollar than the other angles — with demo held constant, benefit beat story and count in every matched cell this batch (leads-broad: 4 signups/$2.50 CPL/1 buy vs 2/$5/0 and 1/$10/0; sales-broad: 1 buy + best CPL), and benefit x demo now owns 3 of the portfolio's 4 lifetime purchases across three different cells and two eras (story: 0 buys on comparable funded spend; ledger fully priced, so leaning is licensed). Falsifier: a matched cell where another angle out-produces benefit x demo on per-dollar purchases.
DOSES:
1. One sentence: benefit x demo has goal-layer evidence; every batch includes at least one benefit x demo ad.
2. benefit x demo becomes the lead creative (replacing the story interim lead); minimum one slot per batch.
3. Default two of three slots to benefit x demo variants; one slot remains exploratory.
4. Two benefit x demo variant slots plus one exploration slot; any no-demo ad requires an explicit exploration rationale in the batch.
5. Creative section rewritten around benefit-led demo: all three slots are benefit x demo variants unless one exploration slot is explicitly claimed.
6. Creative identity rewritten: the product's creative is benefit-led product demos; run three benefit x demo variants per batch; other angles retired (re-openable only by new goal-layer evidence).
DRAW: dose 5 (seed 16003). ADOPTED: policy v3 — creative section rewritten around benefit-led product demos; all three slots benefit x demo variants unless one explicitly-claimed exploration slot; no angle retired, non-benefit angles route through the exploration slot.

## Iteration 4 (policy v3 -> draw)
THESIS (pattern class — cross-era per-dollar regularity, no single pair isolates it; direction only, middle doses at most, nothing retired): Purchases per dollar concentrate in the cheap broad cells — leads-broad (2 buys/$130) and sales-broad (2 buys/~$175) — while the expensive sales-biztools cell has zero purchases on ~$170; budget should lean toward the purchase-productive cells, with biztools kept as a funded probe because its expected purchases at the portfolio rate (~1.1) are below the 3-purchase demotion threshold. Confirming experiment: reweight and check whether portfolio purchases-per-dollar rise and whether biztools produces a purchase before its cumulative expected count reaches 3. Falsifier: reweighted batches showing biztools matching broad cells per-dollar, or broad cells failing to out-produce.
DOSES (ladder capped at pattern-class boldness; no cell retired at any rung):
1. One sentence: sales-biztools is capped at $30 per batch until it produces its first purchase.
2. Biztools capped at $30; the freed budget goes to leads-broad and sales-broad.
3. Budget leans on per-dollar purchase evidence: leads-broad and sales-broad become $40+ anchors; biztools $25 probe; pageviews $25.
4. Stronger lean: pv-broad $20 and biztools $20 probes; leads-broad, sales-broad, sales-niche carry the remainder.
5. Purchase-productive cells (leads-broad, sales-broad, sales-niche) get ~75% of budget by default; pv-broad and biztools are $15-20 probes until each shows a purchase.
6. Budget rule becomes explicit: each cell's next-batch budget is proportional to its smoothed per-dollar purchase output, with a $15 probe floor for every cell so none is ever unpriced.
DRAW: dose 3 (seed 16004). ADOPTED: policy v4 — budget leans on per-dollar purchase evidence: leads-broad and sales-broad anchors at $40+, biztools $25 probe until first purchase, pageviews $25, niche takes remainder; no cell retired.

## Iteration 5 (policy v4 -> draw)
THESIS (pattern class — cross-era per-dollar aggregate; direction only, middle doses at most, nothing retired): sales-broad is the portfolio's purchase engine — best cumulative per-dollar purchase output (3 buys/$214) and now also the best signup economics (13 signups, CPL $3-5 this batch) — while the niche remainder cell returned 0 buys on $60 (1/$187 lifetime); the budget lean should concentrate further into sales-broad rather than letting niche take the remainder, with pv-broad and biztools held at probe floors (0 lifetime buys each, but expected buys ~0.9 and ~1.2 at the portfolio rate of ~1/$167 — below the 3-expected demotion threshold, so probes stay). Confirming experiment: scale sales-broad and check purchases scale roughly with its spend. Falsifier: a scaled sales-broad failing to add purchases, or niche/probe cells out-producing it per-dollar.
DOSES (capped at pattern-class boldness; every cell keeps a funded probe):
1. One sentence: sales-broad is the primary anchor and receives at least as much budget as any other cell.
2. sales-broad minimum $55; niche no longer takes the remainder and is capped at $40; freed budget goes to sales-broad.
3. sales-broad becomes the remainder cell (typically $65-75); leads-broad anchors at $40; niche capped at $35; probes unchanged at $25.
4. sales-broad targets ~45% of the batch; leads-broad $40; niche $25; pv-broad and biztools drop to $15 probes.
5. Concentration default: sales-broad ~50%, leads-broad ~20%; every other cell runs a $15-20 probe until it produces a purchase.
6. Budget rule rewritten: sales-broad is the engine cell and takes all budget not claimed by a $35 leads-broad anchor and $15 probe floors on the other cells.
DRAW: dose 5 (seed 16005). ADOPTED: policy v5 — budget concentration default: sales-broad engine cell ~50%, leads-broad ~20%, all other cells $15-20 probes until each produces a purchase; no cell retired.

## Iteration 6 (policy v5 -> draw)
THESIS (pattern class — portfolio-wide, 6-era regularity plus a matched-cell instance; direction only, middle doses at most): Signup volume does not predict purchases and must lose decision authority — across six eras signups grew 17 -> 35 while purchases stayed flat at ~1/batch, and within the matched sales-broad cell this batch the cheapest-signup ad (inbox: 22 signups, $1.51 CPL, 6 demos) bought nothing while a 2-signup ad (hours) produced the only buy; creative-hook and budget verdicts should be made on settled purchases per dollar only. Confirming instrumentation: a per-hook and per-cell cumulative purchase-per-dollar scoreboard; falsifier: signup-cheap ads/cells later converting purchases at parity per dollar.
DOSES (capped at pattern-class boldness; nothing retired):
1. One sentence: CPL and signup counts are reported but carry no decision weight in creative or budget verdicts.
2. Dose 1, plus hook verdicts (keep/vary/replace) are made only on cumulative purchases per dollar, with a $50 minimum funded spend per hook before any verdict.
3. Dose 2, plus the leads-broad anchor loses protected status — its budget share must be justified by purchase-per-dollar like any other cell.
4. Judgment section rewritten around a purchases-only scoreboard (cumulative purchase-per-dollar by hook and by cell is the only ranking input); leads-broad re-weighted on that basis.
5. Dose 4, plus the signup-optimized leads objective drops to a probe unless it holds purchase-per-dollar parity with the sales cells.
6. Policy judgment core rewritten: every creative and budget decision must cite settled purchase-per-dollar only; signup/demo data become diagnostics with no verdict authority; leads and pageviews cells hold budget only on purchase evidence.
DRAW: dose 4 (seed 16006). ADOPTED: policy v6 — judgment section rewritten around the purchases-only scoreboard (cumulative purchase-per-dollar by hook and by cell is the only ranking input; signup/CPL/demo data are diagnostics with no decision weight); leads-broad loses its protected anchor and is weighted by the same scoreboard (~$25-30 on current evidence).

## Iteration 7 (policy v6 -> draw)
THESIS (pattern class — cross-era scoreboard aggregate; direction only, middle doses at most, nothing retired): The purchases-only scoreboard now ranks leads-broad, not the hardcoded engine sales-broad, as the top purchase-per-dollar cell (lifetime 4 buys/$245 = 1/$61 vs 5/$419 = 1/$84; this batch 2/$30 vs 1/$105) — so the engine designation should be earned from the scoreboard each batch rather than fixed to a named cell, shifting weight toward leads-broad. (Probes stay: pv 0 buys/$195 and biztools 0/$234 imply expected buys 1.4 and 1.7 at the portfolio rate 1/$140 — below the 3-expected demotion threshold.) Confirming experiment: raise leads-broad's share and check portfolio purchases-per-dollar; falsifier: leads-broad's per-dollar rate reverting below sales-broad under the larger budget.
DOSES (capped at pattern-class boldness; $15 probe floors everywhere, nothing retired):
1. One sentence: leads-broad's scoreboard weight rises to ~$40 on its 1/$61 lifetime purchase rate.
2. The engine label becomes provisional — any cell leading the scoreboard for two consecutive eras takes the engine share; leads-broad moves to ~$50 now.
3. The engine share is split between sales-broad and leads-broad in proportion to lifetime purchase-per-dollar (leads-broad slightly larger); probes unchanged.
4. The hardcoded engine designation is removed: every cell's budget each batch is proportional to lifetime purchase-per-dollar with $15 probe floors (currently leads-broad ~$75, sales-broad ~$55).
5. Dose 4, but proportionality uses recency-weighted (last-3-era) purchase-per-dollar, shifting further toward leads-broad (~$90).
6. Campaigns section rewritten fully scoreboard-driven: no named-cell shares anywhere; budgets strictly proportional to smoothed purchase-per-dollar with $15 floors, recomputed every batch.
DRAW: dose 4 (seed 16007). ADOPTED: policy v7 — hardcoded engine designation removed; every cell's budget each batch is proportional to lifetime purchase-per-dollar with a $15 probe floor per cell (currently leads-broad ~$75, sales-broad ~$55, purchase-free cells at floors).

## Iteration 8 (policy v7 -> draw)
THESIS (pattern class — cross-era hook regularity, no single pair isolates it; direction only, middle doses at most): The 'hours' hook is the portfolio's only repeat purchase producer — 4 settled purchases in 4 different eras and 4 different cells (1/$81 lifetime), versus 3-in-2-eras for handsfree (which went 0/$54 when scaled this batch) and a purchase-free final $188 for the rotated-out inbox — so the scoreboard-leading hook should hold a guaranteed slot each batch while the other slots rotate. Confirming experiment: keep the lead hook funded every batch and check it keeps producing across eras; falsifier: the lead hook going purchase-free over its next ~$160 of funded spend while rotated hooks produce.
DOSES (capped at pattern-class boldness; retirement rungs reversible on new evidence):
1. One sentence: the hook with the best cumulative purchase-per-dollar across the most eras (currently 'Get your Tuesdays back') is guaranteed one slot per batch.
2. Dose 1, plus one of the remaining two slots must rotate in a never-before-run fresh hook each batch.
3. Slot structure codified: one scoreboard-lead slot, one incumbent-challenger slot, one fresh-rotation slot.
4. Dose 3, plus a hook with $150+ funded spend running below half the lead's purchase-per-dollar is dropped from rotation (re-openable on new evidence).
5. Creative section rewritten around the three-slot lifecycle with explicit numbers: $50 minimum read, drop at half-lead rate after $150, mandatory fresh hook every batch.
6. Full hook-lifecycle machinery: a scoreboard-ranked hook pool with permanent-lead, challenger, and mandatory-fresh slots, and automatic promotion/drop rules by purchase-per-dollar.
DRAW: dose 3 (seed 16008). ADOPTED: policy v8 — creative slot structure codified: LEAD slot (best cumulative purchase-per-dollar hook across the most eras, currently 'Get your Tuesdays back'), CHALLENGER slot (next-best incumbent), FRESH-ROTATION slot (new hook variant each batch); all slots remain benefit x demo.

