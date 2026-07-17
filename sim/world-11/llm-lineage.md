# Iteration 1 (policy v0 -> batch llm-it1.json, seed 1101)

THESIS (pair, signup-layer): Outcome-led copy beats benefit-led and count-led at the
sign-up layer — in every trusted matched cell (same campaign, ads differing only in
angle) the outcome ad produced the most sign-ups (8 vs 2 vs 1) and the best CPL
($4.44 sales-broad). Falsified if a future matched cell shows outcome at parity or
worse on sign-ups per dollar. Goal layer (purchases) is 0-for-$200 — no purchase
evidence exists, so no angle is settled at the goal layer, and story/demo/
interest_niche remain unpriced coverage debts.

Doses:
1. Add an evidence note: outcome-led copy earned the most sign-ups in it1; writers may weigh it.
2. Lean: when the writer is uncertain, one of the three slots defaults to outcome-led copy.
3. Default: every batch guarantees exactly one outcome-led ad; other slots free/sweep.
4. Rule: two of three ads lead with outcome framing; the third slot is reserved for unswept values (story/demo).
5. Outcome becomes the anchor family: all non-sweep slots outcome-led; count demoted to sweep-only status.
6. Rewrite Creative around outcome-first copy: every ad leads with a concrete outcome; benefit/count allowed only as sub-lines; one slot per batch may be a coverage sweep.

DRAW: dose 5. ADOPTED: v0 -> v1. Outcome is now the anchor angle family (all
non-sweep slots outcome-led); count demoted to sweep-only; coverage debts (story,
demo, interest_niche) recorded in policy text; matched-cell/per-dollar judgment and
signup-vs-goal-layer separation added to Judgment.

# Iteration 2 (policy v1 -> batch llm-it2.json, seed 1102)

THESIS (design, replicated 2 eras): The auto-budget sales cell has been unreadable
(NO-TRUST auto-window) in both eras run — $70 of spend has bought zero trusted
evidence. The experiment should stop funding unreadable cells and put every dollar
into fixed-mode cells that produce trusted receipts. Falsified if a future auto
cell clears its window and yields trusted evidence at competitive cost. Side
findings this era, logged not adopted: demo vs no-demo is a wash at the signup
layer (4 vs 5 sign-ups in matched cells); story and interest_niche are now priced;
purchases remain 0-for-$400, so the goal layer stays open.

Doses:
1. Add a note: auto cells have twice returned no trusted evidence; treat auto spend as at-risk.
2. Cap the auto cell at $20 per batch.
3. Auto default-off: include it only with an explicit readability plan (a budget sized to clear the trust window).
4. Remove auto from the standard portfolio; the portfolio is fixed-mode only.
5. Remove auto and standing-reallocate its budget into fixed sales-objective cells to hunt purchase evidence.
6. Rewrite Campaigns: fixed-mode only; budget concentrated in sales cells across audiences with pageviews/leads cut to minimal probes — every dollar must buy a trusted receipt.

DRAW: dose 6. ADOPTED: v1 -> v2. Campaigns section rewritten: fixed-mode only (auto
removed), budget concentrated on sales-objective cells across the three audiences,
pageviews/leads reduced to minimal probes (~$15 cap) that may be dropped for a
readable sales cell.

# Iteration 3 (policy v2 -> batch llm-it3.json, seed 1103)

THESIS (pair, signup-layer): Within the outcome anchor family, rendering the
outcome as lived time/work relief (hours-to-minutes, submissions not lost) beats
rendering it as money/ROI arithmetic: in six matched cells the money-math ad took
0 sign-ups on $66.67 with collapsed CTR while the time-relief twin took 5 —
the anchor's rendering, not just its family, drives response. Falsified if a
future matched cell shows a money-math rendering at parity per dollar. Side
findings logged: demo remains a wash (5 vs 5); clicks objective priced (0 on $15);
purchases still 0 for $600 — goal layer open, no axis demoted on it.

Doses:
1. Add a note: money/ROI-math outcome renderings underperformed badly; writers cautioned.
2. Lean: prefer lived, experienced-outcome renderings; a money-math rendering needs a stated reason.
3. Default: anchor slots render outcomes as concrete experienced changes; money-math allowed only as an explicit variant test.
4. Rule: money/ROI-math renderings are retired from anchor slots as measurably failed; anchors must show a lived before/after.
5. Rule plus spec: anchors must name a specific experienced outcome (time reclaimed, submissions never lost) with visible product context; money-math retired entirely.
6. Rewrite Creative: the outcome anchor is defined as lived before/after copy — every ad opens on the customer's felt change; money framing banned; canonical hook patterns enumerated in the policy.

DRAW: dose 6. ADOPTED: v2 -> v3. Creative section rewritten: anchor family defined
as lived before/after outcome copy with three canonical hook patterns (time
reclaimed, loss ended, drudgery removed); money/ROI-math framing banned as
measurably failed; count stays sweep-only; demo noted as neutral-so-far production
option; sweep-slot mechanism retained.

# Iteration 4 (policy v3 -> batch llm-it4.json, seed 1104)

THESIS (pattern, middle doses max): Across all four eras the portfolio converts
sign-ups steadily (44 sign-ups, $800) but has produced zero purchases — the goal
layer is unreadable, and until purchase volume exists no ROAS learning is
possible; the loop should reshape spend to maximize sign-ups per dollar in sales
cells and instrument a purchase-drought tripwire. Confirming experiment: if the
drought persists past ~60 cumulative sign-ups, purchase rate < ~5% is confirmed
and a destination/offer design thesis is licensed; a single purchase falsifies
the drought reading. Direction only — no causal claim about the destination yet.
Side findings logged: hook comparison mixed (time 5 / loss 4 / drudge 2 sign-ups;
time led broad, loss led biztools) — no hook retired; probes ($60 over 2 eras, 1
sign-up) near-dead.

Doses:
1. Standing item: log the 44-signup/0-purchase drought with a tripwire at 60 cumulative sign-ups.
2. Accounting rule: policy tracks sign-ups-since-last-purchase; drought past 60 licenses a destination/offer design thesis.
3. Lean: move ~$15 from the weakest probe to the best per-dollar sales cell; drought ledger added.
4. Default: drop pageviews/leads probes; all $200 to the three sales cells weighted by trailing signups-per-dollar; drought ledger added.
5. Dose 4 plus an explicit design objective: each batch maximizes expected sign-ups per dollar (trailing CPL weighting) to force the purchase layer readable; tripwire codified.
6. Restructure budget prose around purchase-readability: budget concentrated in the cheapest-CPL sales cells, probes retired until a purchase exists, drought ledger and 60-signup tripwire codified as policy.

DRAW: dose 5. ADOPTED: v3 -> v4. Probes dropped; all $200 to the three sales cells
weighted by trailing sign-ups per dollar; batch design objective set to maximize
expected sign-ups per dollar while the drought holds; purchase-drought ledger with
60-signup tripwire added as a policy section.

# Iteration 5 (policy v4 -> batch llm-it5.json, seed 1105)

THESIS (pattern, small — modest doses): interest_niche is the portfolio's
consistent per-dollar sign-up laggard — in three of its four readable eras it
trailed both other audiences (trailing 9 sign-ups/$195 = 0.046/$ vs broad 0.058,
biztools 0.065) and it has never led an era — so the budget should slightly
downweight niche while keeping it readable. Confirming experiment: keep niche
funded at a readable floor; an era at or above portfolio-average sign-ups per
dollar falsifies the thesis. Side findings logged: broad vs biztools is a
per-dollar wash cumulatively (biztools weighting did not pay this era); demo
pair still a wash (cumulative 8 vs 7); drought at 54 cumulative sign-ups, 0
purchases — tripwire (60) not yet reached, no destination thesis licensed yet.

Doses:
1. Add a note: niche trails per dollar in 3 of 4 readable eras; weight it accordingly.
2. Lean: niche funded at the minimum readable level (~$40); surplus goes to the per-dollar leaders.
3. Default: niche floor $35 per batch; remainder split evenly between broad and biztools.
4. Rule: niche capped at 20% of the batch until it posts an era at or above portfolio-average sign-ups per dollar.
5. Rule: niche capped at 15% of the batch pending a beating-average era; broad/biztools split by trailing per-dollar.
6. Niche demoted to probe status (~$25) pending a beating-average era; budget prose rewritten around broad and biztools as the two working cells.

DRAW: dose 4. ADOPTED: v4 -> v5. interest_niche capped at 20% of each batch until
it posts an era at or above portfolio-average sign-ups per dollar; broad/biztools
continue to split the remainder by trailing per-dollar performance.

# Iteration 6 (policy v5 -> batch llm-it6.json, seed 1106)

THESIS (design, licensed by the tripwire, replicated 6 eras): The purchase drought
tripwire has fired — 65 cumulative sign-ups, 13 site demos, zero purchases on
$1,200 — so the destination/offer, not the ads, is the binding constraint on
ROAS, and ad-side sign-up-efficiency optimization is now chasing noise against a
dead goal layer. The experiment should stop reweighting on marginal sign-up
deltas, hold a stable replicable portfolio as a purchase detector, and carry the
destination/offer fix as the loop's standing escalation. Falsified by any settled
purchase, or by a destination-side change producing purchases at current traffic.
Side findings logged: loss hook led this era (6 sign-ups vs time 2); niche era at
0.05/$ vs portfolio 0.055/$ — below average, cap stays.

Doses:
1. Ledger note: tripwire fired at 65 sign-ups; destination flagged as suspect.
2. Ledger updated plus standing item: destination/offer is the presumptive binding constraint; ad-side conclusions are frozen at the sign-up layer.
3. Default: batches hold the current best-known portfolio unchanged (no reweighting on sign-up deltas) while the constraint stands; purchase watch continues.
4. Rule: sign-up-efficiency reweighting suspended as noise-chasing; portfolio locked to a replicable baseline; any settled purchase reopens optimization.
5. Dose 4 plus: site demos (the deepest observed funnel event) become the interim per-dollar judgment metric for creative slots; destination escalation codified as the ledger's standing item.
6. Rewrite Judgment and Process around the constraint: ad-side ROAS declared unlearnable until the destination converts; demo-per-dollar becomes the interim optimization target; purchase watch and destination escalation codified.

DRAW: dose 4. ADOPTED: v5 -> v6. Sign-up-efficiency reweighting suspended as
noise-chasing; portfolio locked to the replicable baseline (broad $75 / biztools
$85 / niche $40, fixed) as a clean purchase detector; drought ledger updated to
FIRED state with destination/offer named the presumptive binding constraint and
standing escalation; any settled purchase unlocks the portfolio.

# Iteration 7 (policy v6 -> batch llm-it7.json, seed 1107)

THESIS (pattern, small-middle doses): Hook-level differences inside the lived
before/after family are not stable across eras — time went 5/2/3 sign-ups, loss
4/6/2 (its broad cell went 4 then 0 on identical creative), and no hook has led
twice running — so at $200 a batch the loop cannot rank hooks and should treat
the canonical hooks as an interchangeable rotation with fresh renderings each
batch, not chase last era's winner or reuse exact creative. Confirming
experiment: continue rotating fresh renderings; a hook leading three consecutive
readable eras falsifies interchangeability. Side findings logged: drought
re-confirmed by the locked detector (72 cumulative sign-ups, 0 purchases,
$1,400); era sign-up dip (11 to 7) is single-era noise, no budget conclusion.

Doses:
1. Note: hook rankings are unstable era to era; do not chase last era's winner.
2. Lean: prefer fresh renderings over exact creative reuse; hook choice stays free.
3. Default: each batch rotates at least two canonical hooks with fresh renderings; exact reuse discouraged.
4. Rule: no exact creative reuse in consecutive batches; each batch covers all three canonical hooks once, freshly rendered.
5. Dose 4 plus: hook performance memory is excluded from slot allocation until a hook leads three consecutive readable eras.
6. Creative section rewritten: hooks declared interchangeable; a batch is three fresh renderings, one per hook; winner-chasing and creative reuse banned; three-consecutive-era leadership is the only path to a hook preference.

DRAW: dose 3. ADOPTED: v6 -> v7. Creative default added: each batch rotates at
least two canonical hooks with fresh renderings; exact creative reuse discouraged;
one-era winners earn no extra slots. Portfolio lock and drought ledger unchanged.

# Iteration 8 (policy v7 -> batch llm-it8.json, seed 1108)

THESIS (design, replicated across all 8 eras): The purchase drought has now
replicated through every era the loop has run — 81 cumulative sign-ups, 15 site
demos, $1,600, zero purchases — tightening the confirmed purchase rate below
roughly 4%, so sign-up-layer receipts have stopped carrying new policy
information and the loop should harden into detector-only mode: no further
creative or audience rule changes adopted from sign-up evidence alone while the
drought holds, destination/offer escalation elevated to the loop's precondition
for any ROAS learning. Falsified by any settled purchase, or by a sign-up-layer
signal strong enough to survive the three-consecutive-era test. Side findings
logged: hook instability confirmed again under fresh renderings (time led broad
after trailing twice; drudge revived in niche) — rotation retained.

Doses:
1. Ledger note: bound tightened — 81 sign-ups, zero purchases; rate below ~4%.
2. Ledger tightened and the hook-instability confirmation appended; rotation retained.
3. Ledger updated; sign-up-layer creative/audience conclusions marked provisional and frozen; detector continues unchanged.
4. Rule: destination escalation upgraded from standing item to precondition — no creative/audience rule changes adopted from sign-up evidence alone while the drought holds.
5. Dose 4 plus the freeze codified section by section: Creative and Campaigns declared stable detector fixtures, only the ledger may change.
6. Judgment and Process rewritten around detector-only mode: the loop suspends all ad-side learning until a purchase settles; every batch exists to detect that purchase.

DRAW: dose 3. ADOPTED: v7 -> v8. Drought ledger tightened (past 80 sign-ups, zero
purchases, rate < ~4%); all sign-up-layer creative/audience conclusions declared
provisional and frozen — held as written, not extended, while the drought holds;
locked detector portfolio and destination escalation unchanged.
