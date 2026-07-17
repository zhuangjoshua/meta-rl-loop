# Iteration 1 (policy v0 -> ?)

THESIS (coverage/design class, middle doses at most): The batch left three axis
values unpriced — the count angle family, the demo variant, and the
interest_niche audience — so nothing about angles or audiences may be settled
yet; the policy should oblige funded tests of unpriced values before refining
among swept ones. Falsifier: if a sweep batch prices these values and they
match or beat swept values per-dollar, silence-as-neutral would have been wrong;
if the ledger empties with no change in decisions, the coverage rule cost was
pure overhead.

DOSES:
1. Add one sentence: the next batch must include one funded test of an unpriced axis value (count angle is noted as unpriced).
2. Add a coverage ledger section listing unpriced values (count angle, demo variant, interest_niche) and direct one funded test per batch from it.
3. Direct the next batch to price count and demo in ad slots and give interest_niche a funded sales cell.
4. Default: every batch reserves one ad slot and one campaign cell for the cheapest-viable unpriced axis value until the ledger is empty.
5. Rule: no refinement among swept values while any axis value is unpriced; batch composition is driven by the coverage ledger first.
6. Rewrite Creative and Campaigns around a sweep schedule: batches are coverage-first until every declared axis value (all four angles, demo on/off, all three audiences, all objectives, auto mode) has a funded readable test.

DRAW: dose 6 -> ADOPTED as v1. Creative and Campaigns rewritten coverage-first
with an explicit coverage ledger; refinement deferred until axes are fully
priced; matched-cell per-dollar judgment made explicit.

# Iteration 2 (policy v1 -> ?)

THESIS (design class, replicated 2 eras): Auto-budget sales cells have been
funded in both batches (~$75 total) and returned only no-trust windows both
times — they consume budget while producing evidence of nothing; the portfolio
should drop auto mode and put that budget into fixed, readable cells.
Falsifier: a future readable auto cell that settles usable results, or fixed
reallocation producing less readable evidence per dollar than auto did.
(Parked for next round: benefit+demo carried 2 of 3 purchases and sales-niche
was the best signup/demo cell per dollar — promising, not yet a settled pair.)

DOSES:
1. Add one sentence: the auto cell is lowest priority and runs only after every fixed cell is fully funded.
2. Auto becomes default-off for the next batch; its budget moves to fixed sales cells.
3. Auto is off until a future policy change; its budget goes to the fixed sales cell with the best per-dollar signups.
4. Remove auto from the standard portfolio and mark it in the ledger as attempted-unreadable-retired.
5. Rule: only fixed-mode cells may be funded; auto may return only through an explicit future thesis that includes a readability plan.
6. Rewrite Campaigns fixed-only: auto deleted from the mode vocabulary; budget concentrated in sales cells across all three audiences with minimal pageviews/leads.

DRAW: dose 5 -> ADOPTED as v2. Fixed-only funding rule; auto mode may return
only via an explicit future thesis carrying a readability plan; ledger updated
(all angles/audiences/demo now priced; auto marked attempted-unreadable).

# Iteration 3 (policy v2 -> ?)

THESIS (pattern class — middle doses at most): Across three eras, story and
benefit+demo creatives consistently lead signups per dollar in matched cells
while outcome and count have produced almost none; the policy should favor
story and benefit+demo in slot allocation WITHOUT retiring outcome/count, whose
goal-layer funded spend (~$55 each) is far below the >=3-expected-purchase bar.
Confirming experiment (required for a pattern thesis): keep a rotating
challenger slot running refreshed outcome/count executions in matched cells
until each family's cumulative funded spend reaches ~$200; sustained
underproduction there licenses demotion, outperformance falsifies this thesis.
(Also noted: the it3 demo pair was a signup-layer wash, 2 cells demo, 2 no-demo,
1 tie, 0 buys each — demo's earlier purchase lean is not yet causal.)

DOSES:
1. Add one sentence: when otherwise indifferent, writers lean toward story and benefit+demo executions.
2. Default: two of three ad slots go to story and benefit+demo; the third rotates among other families.
3. As dose 2, plus demo becomes default-on for benefit-family ads.
4. Rule: every batch runs one story and one benefit+demo ad; outcome/count appear only through the rotating third slot with refreshed executions.
5. As dose 4, plus the challenger slot is scored: a challenger family must match the weaker incumbent per-dollar in matched cells to stay in rotation (log the running comparison in evidence).
6. Standing default: story and benefit+demo hold two guaranteed slots; the third slot is a monitored rotation with the demotion test written into the policy (a family is demoted only after ~$200 cumulative funded spend of sustained underproduction).

DRAW: dose 5 -> ADOPTED as v3. Creative slots fixed to story + benefit-demo
incumbents plus a scored challenger rotation (matched-cell per-dollar
comparison logged each batch; demotion only after ~$200 sustained
underproduction).

# Iteration 4 (policy v3 -> ?)

CHALLENGER LOG (v3 rule): outcome challenger 3 signups/$66.66, 1 demo, 0 buys
vs weaker incumbent (benefit-demo) 4 signups/$66.66, 0 buys — marginal
underperformance, roughly matching; outcome stays in rotation. Cumulative
outcome funded spend ~$120 of the ~$200 demotion bar.

THESIS (design/pattern class, replicated 4 eras — capped below demotion): All
four settled purchases across four eras came from sales or leads cells; the
pageviews campaign has $115 of funded spend over every era with zero purchases
and signups that never progress. Budget should shift toward sales cells
(sales-broad weighted highest — it holds 2 of 4 purchases on ~$170), while
pageviews stays funded at probe level because its spend is far below the
>=3-expected-purchase demotion bar (observed rate ~1 buy/$200 makes that bar
~$600). Falsifier: a pv-broad cell producing purchases at probe funding, or
sales-weighted batches yielding fewer settled purchases per dollar than the
balanced portfolio did.

DOSES:
1. Lean: shift $5 from pageviews to sales-broad (pv $20).
2. pv drops to $15; freed budget goes to sales-broad.
3. Default split: pv $10, leads $30, sales cells $160 with sales-broad largest.
4. Default split: pv $10, leads $25, sales cells $165; sales-broad gets the top share (~$65).
5. Rule: sales cells receive at least 80% of batch budget; pv+leads together at most $40 with pv a $10 probe.
6. Sales-first portfolio: sales cells across all three audiences carry ~$170 with broad weighted highest; pv and leads persist only as $10-15 probes, each carrying an explicit demotion test at the licensed spend bar.

DRAW: dose 3 -> ADOPTED as v4. Default budget split becomes pv $10 / leads $30 /
sales $160 with sales-broad largest; explicit no-demotion-below-spend-bar rule
added for objectives.

# Iteration 5 (policy v4 -> ?)

CHALLENGER LOG (v3/v4 rule): count challenger 4 signups/$66.66, 2 demos, 0 buys
vs weaker incumbent (benefit-demo) 5 signups, 1 buy — underperforms the weaker
incumbent per dollar; count cumulative funded spend ~$122 of the ~$200 bar.

THESIS (pattern class — middle doses at most): The story family is the
portfolio's strongest creative: 5 of 8 lifetime settled purchases and the top
per-dollar signups in most matched cells in every era, on ~$260 cumulative
funded spend. Creative capacity should tilt further toward story executions.
Confirming experiment (required): run two DISTINCT story executions in one
batch; if the second story slot fails to match the non-story incumbent
per-dollar in matched cells, the extra story allocation is falsified.

DOSES:
1. Add one sentence: story is the acknowledged lead family; production ties break toward story.
2. Lean: story may take a second slot in batches where no challenger test is owed.
3. Default: two of three slots run distinct story executions; the third alternates benefit-demo and the challenger rotation.
4. Rule: two story slots standing (distinct named customers each batch); benefit-demo holds the third except when a challenger test is owed at its spend bar.
5. Story-first slotting: two story + one benefit-demo every batch; challenger rotation suspended until a family's demotion decision is due at its ~$200 bar.
6. Standing default: batches run two-three distinct story executions with benefit-demo as the sole non-story incumbent, each batch logging the second story slot's matched-cell comparison (the named confirming experiment) so the tilt self-audits.

DRAW: dose 4 -> ADOPTED as v5. Two standing story slots (distinct named
customers, self-auditing matched-cell comparison); benefit-demo holds the
third slot except when a challenger is owed its spend-bar decision test.

# Iteration 6 (policy v5 -> ?)

CHALLENGER LOG (decision test at bar): count reached ~$189 cumulative funded
spend; this batch it produced 4 signups/$66.66 (2 demos, 0 buys) vs the weaker
story slot's 3 signups/$66.66 — count MATCHED the weaker incumbent in matched
cells, so demotion is not licensed; count stays in the vocabulary and the
third slot returns to benefit-demo. Outcome still owes ~$80 before its bar.
Story self-audit: story-a 3 signups, story-b 4 signups, both 0 buys — second
story slot roughly matched; the tilt survives, unspectacularly. Also noted:
pv-broad produced a purchase at probe level (a prior falsifier fired softly).

THESIS (pattern class — middle doses at most): Settled purchases arrive too
sparsely for single-era judgment — era buy counts run 0,3,0,1,4,0 at roughly
one purchase per $150, so any single era's goal-layer winners and losers are
mostly noise; goal-layer verdicts should require pooled multi-era matched-cell
evidence sized in expected purchases (>=3 expected at the observed rate)
before crediting or blaming any family, cell, or budget shape. Confirming
experiment (required): track era-to-era rank stability of purchase leaders in
the evidence file — if single-era leaders repeat as leaders next era at
better-than-chance rates, sparsity is refuted and faster judgment is safe.

DOSES:
1. Add one sentence: single-era purchase counts are treated as noisy; verdicts lean on cumulative evidence.
2. Goal-layer verdicts prefer two-era pooled windows over single-era reads.
3. Default: no goal-layer verdict is issued on fewer than two pooled eras of matched-cell spend.
4. Rule: goal-layer verdicts require pooled matched-cell spend expecting at least 3 purchases at the portfolio's observed rate; signup-layer reads may still steer slot allocation between verdicts.
5. As dose 4, plus era-level ROAS swings alone never trigger budget or portfolio changes.
6. Judgment re-defaulted: every verdict threshold is written in expected-purchase units (>=3 expected, pooled multi-era matched cells); single-era results can only park signals in the standing-items ledger, never decide.

DRAW: dose 4 -> ADOPTED as v6. Goal-layer verdicts now require pooled
matched-cell spend expecting >=3 purchases at the observed rate; signup-layer
evidence steers slots between verdicts. (Count retained after its wash
decision test; third slot back to benefit-demo; outcome still owed ~$80.)

# Iteration 7 (policy v6 -> ?)

CHALLENGER LOG (decision test at bar): outcome reached ~$187 cumulative; this
batch 4 signups/$66.66, 0 buys vs weaker story slot 1 signup/$66.66 — outcome
BEAT the weaker incumbent, demotion not licensed; outcome stays in the
vocabulary. Both challenger families have now survived their spend-bar tests.
Story self-audit: story-b 5 signups + the era's only buy; story-a 1 signup —
large execution variance inside the story family, second slot carried.
Rank-stability track (sparsity experiment): era-5 purchase leader (story)
repeated as era-7 leader.

THESIS (pattern class, sized below the verdict bar — small-to-middle doses):
Sales-broad is the portfolio's per-dollar purchase leader — 5 of 9 lifetime
purchases on ~$278 versus 1 each for sales-biztools (~$275) and sales-niche
(~$265), roughly 4x per dollar at matched objective — so the sales budget
should lean further toward broad; but pooled sales-broad spend expects only
~1.8 purchases at the observed rate, below the policy's own >=3-expected
verdict bar, so this is a lean with an explicit revert condition, not a
verdict. Falsifier: once pooled sales-broad spend reaches the verdict bar, a
per-dollar purchase rate no better than the other sales cells reverts the
concentration.

DOSES:
1. Add one sentence: sales-broad is the noted leading sales cell; budget ties break toward it.
2. Shift $10: sales-broad $75, sales-niche $45, biztools $40.
3. Default split: sales-broad $80, biztools $35, niche $45.
4. Default split: sales-broad $90, biztools $35, niche $35.
5. Default split: sales-broad $100, biztools $30, niche $30, with the concentration reviewed when pooled broad spend reaches the verdict bar.
6. Concentrate: sales-broad $110 with biztools and niche as $25 probes until pooled sales-broad spend reaches the ~$465 verdict bar, at which point a full goal-layer verdict either locks the concentration or reverts to the balanced split.

DRAW: dose 2 -> ADOPTED as v7. Sales split shifts $10 toward the per-dollar
purchase leader: sales-broad $75, sales-biztools $40, sales-niche $45.

# Iteration 8 (policy v7 -> ?)

NOTE: cell table shows 1 purchase (story-b, sales-broad) but settled revenue is
$0 — the purchase did not settle in-window; per policy an unsettled measurement
is evidence of nothing. Story self-audit: story-a 11 signups/$66.66, story-b 5;
benefit-demo 3 signups/$66.66, 0 settled buys.

THESIS (pattern class — small-to-middle doses): The benefit-demo incumbent is
decaying — its last ~$200 of funded spend produced zero settled purchases and
bottom-of-batch per-dollar signups in two of its last three batches, while
story executions keep leading; the third slot should tilt toward story, with
benefit-demo kept as a scored challenger rather than a standing incumbent.
Demotion is NOT licensed ($200 expects ~1.3 purchases at the observed rate,
below the >=3-expected bar), so this is a slot-allocation lean with the
verdict deferred to the bar. Falsifier: benefit-demo matching the weaker story
slot per-dollar in matched cells over its next pooled window.

DOSES:
1. Add one sentence: benefit-demo's recent underproduction is noted; third-slot ties break toward a third story execution.
2. Third slot alternates batches between benefit-demo and a third distinct story execution, with the matched-cell comparison logged.
3. Default: third slot runs a third story execution; benefit-demo returns every third batch as a scored challenger.
4. Rule: third slot is story by default; benefit-demo runs only as a scored challenger until its pooled challenger spend reaches the >=3-expected-purchase bar and earns a verdict.
5. As dose 4, plus the benefit-demo verdict is scheduled in the ledger with an explicit keep-or-demote outcome to be written when the bar is reached.
6. All three slots run distinct story executions; benefit-demo is parked as a standing-item challenger owed a bar-sized decision test, recorded in the ledger as a pending verdict.

DRAW: dose 3 -> ADOPTED as v8. Third slot defaults to a third distinct story
execution; benefit-demo demoted to a scored every-third-batch challenger
(spend-bar decision tests unchanged; no family demoted below the
>=3-expected-purchase bar).
