# Qualitative audit — 20-world experiment vs revealed ground truth (2026-07-17)

Method: for each world 6–25, an independent auditor ran `python3 sim/score.py <w>`
(the oracle reveal: hidden archetype table + best-config expected cost per
purchase) and read that world's `llm-lineage.md`, `llm-policy.md`, and
`llm-evidence.md`. Each world classified by primary outcome mode, with
axis-by-axis match (proof angle / demo / objective / audience) against the
oracle-best config and verbatim lineage quotes as evidence.

Classification vocabulary (glossed):
- FOUND-AND-HELD — matched the oracle on most axes and exploited it.
- FOUND-LATE — matched, but only in the last 2–3 iterations.
- NEVER-TESTED — the oracle-best config was never funded (coverage gap).
- TESTED-BUT-MISREAD — funded the oracle config, drew unlucky receipts, moved away.
- FALSE-BELIEF — adopted and kept a thesis contradicting ground truth.
- DECLARED-DEAD — concluded the market/offer broken (checked against oracle).

The four auditor reports are preserved verbatim below (worlds 6–10, 11–15,
16–20, 21–25), followed by nothing — synthesis lives in the session record and
can be rebuilt from these reports.

---

# Ad-Optimization Audit: Worlds 6–10

Config axes reported as **proof / demo / objective / audience**. Agent achieved-ROAS = total revenue / $1600 (8 × $200).

---

## WORLD 6 — loop $406 (win)
- **ORACLE:** story / demo=True / sales / broad — **$129/buy** (0.154 buys/$20). Top-5 all story+demo+sales.
- **FINAL-POLICY:** every ad named-story + demo layer; sales-broad $150 floor (~75% of budget), broad carries pv/leads too. → story / demo=True / sales / broad.
- **AXIS-MATCH:** proof HIT · demo HIT · objective HIT · audience HIT — **4/4**.
- **EVER-FUNDED-ORACLE-CONFIG:** YES, continuously. story-demo in sales-broad funded from it4 on; produced the recurring buys (it5 2 buys, it6 3 buys, it7 3 buys). Locked direction early: story it1, demo it4, sales-broad concentration it5 (~half the run exploring, then held).
- **CLASSIFICATION:** **FOUND-AND-HELD.**
- **QUOTES:** it1 "lean creative toward named-story." · it6 "Concentrating budget into sales-broad keeps paying at rising per-dollar buy rates as it scales."
- **$-left:** Oracle $129/buy = 0.225 ROAS; agent hit 406/1600 = **0.254**, i.e. at/above the single-config oracle rate — essentially nothing left on the table (over-concentration at $165 in it8 briefly hurt, correctly walked back).

---

## WORLD 7 — loop $87 (win, but structural loss-market)
- **ORACLE:** outcome / demo=True / sales / interest_niche — **$331/buy** (0.060 buys/$20). Even perfect play = ROAS 0.088, below $29 breakeven → world is near-unwinnable.
- **FINAL-POLICY:** outcome favored (2/3 slots), demo mandatory, sales ~70% interest, interest_niche ~half of TOTAL sales budget. → outcome / demo=True / sales / interest_niche.
- **AXIS-MATCH:** proof HIT · demo HIT · objective HIT · audience HIT — **4/4**.
- **EVER-FUNDED-ORACLE-CONFIG:** YES. outcome-demo in sales-niche funded every batch from it2; returned the run's first buy it3 ($29). Core (demo it2, sales, interest-tilt it4) held from mid-run; niche-half (it7) and outcome-favoring (it8) only crystallized in the last 2 iters — but the cell was funded throughout.
- **CLASSIFICATION:** **FOUND-AND-HELD** (angle-favoring formalized late, but oracle cell exploited continuously).
- **QUOTES:** it7 "interest_niche sales is the portfolio's best per-dollar goal-proxy cell." · it8 "outcome has now won the core niche matched cell per dollar in two consecutive batches... outcome earns favored status."
- **$-left:** Oracle ceiling ~$140 (0.088 ROAS × $1600); agent got $87 (0.054). ~$50 gap, but the ceiling itself is below breakeven — the shortfall is the world, not the policy.

---

## WORLD 8 — loop $145 (win)
- **ORACLE:** outcome / **demo=True** / sales / broad — **$129/buy** (0.155 buys/$20); demo_gate 0.54 → demo ~1.85× purchases.
- **FINAL-POLICY:** 2-ad matched pair — outcome→broad-sales AND story→interest_biztools-sales, ~75% budget on those two cells, split up to 75/25. Demo listed "available… untested," **not run**. Broad arm → outcome / demo=False / sales / broad.
- **AXIS-MATCH:** proof HIT · demo **MISS** · objective HIT · audience HIT — **3/4** (plus ~40% of sales budget on the off-oracle story-biztools cell).
- **EVER-FUNDED-ORACLE-CONFIG:** YES — outcome-demo in sales-broad funded it2/it3/it4, returned 0 buys each (thin $13–20, unlucky), so demo was declared a wash and dropped; the plain (non-demo) outcome-broad cell then caught buys (it3, it6 2 buys, it8) and became the incumbent.
- **CLASSIFICATION:** **TESTED-BUT-MISREAD** (oracle config funded, noisy zeros misread as "demo neutral"; agent still captured 3/4 and won).
- **QUOTES:** it4 "the demo variant raises CTR in 8/10 but ties on signups (4 v 4) and buys (0 v 1) — no goal-layer edge; demo is neutral, not preferred."
- **$-left:** Oracle 0.225 ROAS → ~$360 achievable; agent 145/1600 = **0.091**, ~$215 left — chiefly from dropping demo (the world's biggest lever) and spending ~40% of sales budget on the off-oracle biztools-story arm.

---

## WORLD 9 — loop $116 (LOSS vs base $261)
- **ORACLE:** story / **demo=True** / sales / **interest_niche** (and broad) — **$162/buy** (0.123 buys/$20). Top-5 all story+demo+sales; demo_gate 0.60, story strongest angle (0.781).
- **FINAL-POLICY:** ONE sales cell = entire $200, audience chosen by settled cost/purchase (ledger locked **interest_biztools** at $133; niche recorded "0 buys on $200"); creative merit-allocated (late leader = outcome-concrete), demo lean added only it8. → merit/outcome-leaning / demo=lean-True / sales / biztools.
- **AXIS-MATCH:** proof **MISS** (oracle=story) · demo HIT (late) · objective HIT · audience **MISS** (oracle=niche/broad; agent locked biztools) — **2/4**.
- **EVER-FUNDED-ORACLE-CONFIG:** **NO** at readable scale. story+demo+niche ran once thin (it2, $13.33, 0 buys, pre-dense). Niche got exactly one *dense* era (it7) but with non-demo story/outcome ads → settled 0 → the "one dense era then lock" rule permanently discarded the oracle-best audience. The winning story×demo×niche combination never co-occurred densely.
- **CLASSIFICATION:** **NEVER-TESTED** (coverage gap on the winning creative×audience combo; compounded by a brittle one-era audience lock that misread niche's single unlucky non-demo era).
- **QUOTES:** it7 standing item "audience ledger complete — biztools $100/settled purchase, broad $200, niche 0 buys on $200" (→ locked biztools). · it5 "price broad… select on settled cost per purchase" — the protocol that gave each audience only one dense shot.
- **$-left:** Oracle 0.179 ROAS → ~$286 achievable, base $261; agent got 116/1600 = **0.073**. ~$145 below base / ~$170 below oracle — spent it1–it4 (half the run) producing zero purchases while fragmented, then locked the wrong audience and priced demo too late to ever pair it with niche.

---

## WORLD 10 — loop $261 (win)
- **ORACLE:** story / demo=True / sales / broad — **$222/buy** (0.090 buys/$20); demo_gate 0.59.
- **FINAL-POLICY:** every ad named-story + demo footage; sales-broad single largest cell (~35%), sales-broad+leads-broad ~55%, ≥50% broad. → story / demo=True / sales / broad.
- **AXIS-MATCH:** proof HIT · demo HIT · objective HIT · audience HIT — **4/4**.
- **EVER-FUNDED-ORACLE-CONFIG:** YES, heavily. story-demo in sales-broad drove the wins: it5 marcus-demo 3 buys + ana-demo 1, it6 tom-demo 1, it7 ana-demo 1. Locked story it3, demo it5, broad-sales concentration it6–7 (~5 iters exploring, then held; it8's $0 is persona-fatigue noise, not a direction change).
- **CLASSIFICATION:** **FOUND-AND-HELD.**
- **QUOTES:** it5 "every one of the 5 settled purchases came from a story ad WITH demo footage while plain story ads hold 0 purchases." · it7 "All nine settled purchases across four consecutive eras sit in the two broad direct-response cells."
- **$-left:** Oracle single-config ceiling ~$209 (0.131 ROAS × $1600); agent got 261/1600 = **0.163**, *above* it by also harvesting broad-leads buys — near-optimal, essentially nothing left on the table.

---

### One-line pattern (worlds 6–10)
Wins (w6, w10; and w7 to the market's low ceiling) all reached the full **story-or-outcome + demo + sales + broad/niche** oracle config and exploited it. The two shortfalls both trace to the **demo axis + audience interaction under noise**: w8 funded the oracle config but misread unlucky demo zeros and dropped demo (TESTED-BUT-MISREAD, still 3/4 win); w9 (the only true loss) never funded the winning story×demo×niche combo densely and let a one-era audience lock kill its best audience (NEVER-TESTED, 2/4).

---

# Ad-Optimization Audit: Worlds 11–15

Config axes: **proof** (benefit/outcome/count/story) · **demo** (T/F) · **objective** (sales/leads/pageviews) · **audience** (broad/biztools/niche). Each world = 8 batches × ~$200 = ~$1,600 spend. Achieved $/buy = $1,600 ÷ (revenue/$29).

---

## WORLD 11 — revenue $0 (LOSS)

**ORACLE:** best = `story · demo=True · sales · broad` @ **$176/buy** (~1.1 expected buys per $200 batch). Buyer-weighted proof preference: **story 0.863 ≫ count 0.663 > outcome 0.597 > benefit 0.333**. demo_gate 0.64 (dropping demo cuts purchase rate ~36%). Top buyer archetypes (peer_proof 20%, authority 17%, skeptic, bargain) all prefer **story**.

**FINAL-POLICY (v8):** proof = **outcome** ("lived before/after" anchor family, count sweep-only, money-math banned); demo = neutral option (mostly outcome+demo); objective = **sales** only; audience = locked baseline broad $75 / biztools $85 / niche $40. Declared destination/offer the binding constraint; froze all conclusions ("detector-only mode").

**AXIS-MATCH vs oracle:** proof **MISS** (outcome, not story) · demo **MISS at the winning combo** (demo only ever ran on outcome ads) · objective **HIT** (sales) · audience **HIT** (broad funded). → 1/4, and the two creative axes that matter are both wrong.

**EVER-FUNDED-ORACLE-CONFIG:** **NO.** `story+demo` was never created in any batch of world 11. Story appeared **only in it2** (demo=False): `it2-story · sales-broad` got $13.33 → 0 signups, 0 buys, then story was dropped entirely from it3–it8. Every demo ad in the run was **outcome**-angle (it2/it3 outcome-demo, it5 time-demo, it6/it7 loss-demo, it8 time-fresh). So the $176/buy jackpot cell was never touched.

**Special-attention findings:**
- **Did it fund story+demo in a sales-broad cell? No — never, in any cell.** Story-with-demo was never even authored.
- **Configs it DID fund:** it1 benefit/outcome/count (demo=F); it2 outcome, outcome-demo, story(demo=F); **it3–it8 exclusively the outcome angle** (time/loss/drudge renderings), demo bolted only onto outcome, across sales broad/biztools/niche.
- **Bad luck, or wrong configs? Wrong configs — systematic, not variance.** The world's buyers down-weight outcome (0.597) and reject the config the agent anchored on from **iteration-1 sign-up data**, while the story+demo cell that buys at ~1.1/batch was never funded. Its $0 is a coverage/creative failure, not unlucky draws on good cells.
- **DECLARED-DEAD verdict:** ground truth **does NOT support it.** Oracle shows ~1.1 expected buys per $200 batch in story+demo/sales/broad; the market/offer is alive. The real binding constraint was the agent's own creative choice (outcome anchor + never crossing story×demo), which it misattributed to the destination.

**CLASSIFICATION:** **FALSE-BELIEF** (primary) — adopted "outcome is the anchor angle" at it1 from sign-up-layer evidence, a thesis that directly contradicts the story-preferring ground truth, and held it all 8 iterations. This produced a **NEVER-TESTED** coverage gap (story+demo unfunded) and terminated in a **DECLARED-DEAD** misdiagnosis that ground truth refutes.

**QUOTES:**
- it1: "Outcome-led copy beats benefit-led and count-led at the sign-up layer… Outcome is now the anchor angle family (all non-sweep slots outcome-led); count demoted to sweep-only."
- it6: "the destination/offer, not the ads, is the binding constraint on ROAS… ad-side ROAS declared unlearnable until the destination converts."

**Lock-on timing:** direction fixed at **it1** (outcome anchor); story abandoned after it2; it4 built the drought tripwire; it6–it8 froze. Effectively 0 iterations of genuine exploration of the winning axis.

**$-left-on-table:** achieved **∞ $/buy** (0 buys on $1,600) vs oracle $176/buy; had it funded story+demo/sales/broad it projects ~9 buys ≈ $264 revenue — the entire loss traces to spending the whole run on a proof angle the world rejects and never authoring story+demo.

---

## WORLD 12 — revenue $116 (win)

**ORACLE:** `story · demo=True · sales · broad` @ **$122/buy**. Proof pref: count 0.819 ≈ story 0.787 ≫ outcome 0.532; demo_gate 0.68.

**FINAL-POLICY (v8):** proof = **story** (2 story variants + 1 outcome default); demo = **words-led / no-demo** default (demo only with a stated argument); objective = **sales** core; audience = **broad** core (+ biztools/niche). Offer-qualification line default.

**AXIS-MATCH:** proof **HIT** · demo **MISS** (reverted demo to off) · objective **HIT** · audience **HIT**. → 3/4.

**EVER-FUNDED-ORACLE-CONFIG:** **YES but misread.** `story+demo/sales-broad` funded it3 ($23.33 → 4 signups, 2 demos, **0 buys**) and it4 ($23.33 → 1 signup, 0 buys). Unlucky 0-buys on a 0.16-buy/$20 cell led the agent to read four matched ±demo pairs as "no demo advantage" and revert to no-demo — a **TESTED-BUT-MISREAD on the demo axis**, leaving the demo_gate multiplier on the table. Story-nodemo/sales still bought (4 settled purchases).

**CLASSIFICATION:** **FOUND-AND-HELD** (story/sales/broad matched and exploited); demo axis TESTED-BUT-MISREAD.

**QUOTES:**
- it8: "Story is separating from outcome as the purchase engine — named-customer story ads now hold 3 of 4 settled purchases."
- it4: "the demo-as-default rule confers no advantage at any measured funnel layer and should be reverted toward neutral."

**Lock-on timing:** demo mis-neutralized it4–it5; **story promoted to primary only at it7–it8** (last 2 iterations) — the proof win landed late, though sales+broad held from it2.

**$-left-on-table:** achieved **$400/buy** vs oracle $122/buy (~3.3× worse); dropping demo off the story config forfeited ~half the purchase rate the demo_gate would have supplied.

---

## WORLD 13 — revenue $174 (win)

**ORACLE:** `count · demo=True · sales · broad` @ **$223/buy**; `story·demo·sales·broad` 4th @ $247 (near-tie). Proof pref: story 0.753 ≈ count 0.736; demo_gate 0.72.

**FINAL-POLICY (v8):** proof = **story-first default wrapper + count run every batch** (2 ads: story-demo & count-demo); demo = **mandatory** ("every ad built around screen-recording demo footage"); objective = **sales**; audience = **niche ≥40% + broad funded** (over-tilted to niche mid-run, partly unwound).

**AXIS-MATCH:** proof **PARTIAL/HIT** (defaults story but funds count+demo every batch = oracle #1 config is in-portfolio) · demo **HIT** · objective **HIT** · audience **PARTIAL** (broad funded but niche prioritized; oracle=broad).

**EVER-FUNDED-ORACLE-CONFIG:** **YES, repeatedly and productively.** `count+demo/sales-broad` funded it3–it8; peak **it6: $40 → 9 signups, $4.44 CPL, 5 demos, 1 buy** (the best subcell of the run). Also story+demo/sales-broad bought (it3).

**CLASSIFICATION:** **FOUND-AND-HELD** — locked demo+sales at it2, funded both top configs throughout; only inefficiency was an it5 over-tilt to niche (chasing 2 early niche buys) that it5→it6 partly reversed.

**QUOTES:**
- it2: "Adding demo footage to story creative improves response per dollar."
- it6 standing item: "count-demo x sales-broad was the best subcell ever (9 signups $4.44 CPL, 5 demos, 1 buy)."

**Lock-on timing:** demo locked **it2**, sales **it3**; niche detour it5, unwound it6; late iterations (it7–it8) spent on a creative-fatigue/freshness rule (real but minor axis).

**$-left-on-table:** achieved **$267/buy** vs oracle $223/buy (~1.2× — the closest-to-oracle world); leakage was the niche budget tilt (biztools/niche absorbed spend at ~4× worse per-dollar than broad).

---

## WORLD 14 — revenue $232 (win)

**ORACLE:** `story · demo=True · sales · broad` @ **$130/buy**. Proof pref: **story 0.926** (most story-dominant world); demo_gate 0.58 (large demo lift).

**FINAL-POLICY (v8):** proof = **story** (all slots named-story-led, founder/agency "vertical" default); demo = **per-ad writer's choice / measured-neutral**; objective = **sales** (+pv/leads on broad); audience = **broad-only** (interest cells cut to a probe every 3rd batch).

**AXIS-MATCH:** proof **HIT** · demo **MISS** (neutralized a real demo lift) · objective **HIT** · audience **HIT**. → 3/4.

**EVER-FUNDED-ORACLE-CONFIG:** **YES, many times, productively.** `story+demo/sales-broad`: it2 (3 signups, 1 buy), it5 priya-demo, **it8 aisha-demo ($16.67 → 3 signups, 2 buys)**. The winning cell was repeatedly funded and bought.

**CLASSIFICATION:** **FOUND-AND-HELD** — story locked it2–it3, broad reshaped by it8, exploited for 8 buys. Demo axis TESTED-BUT-MISREAD (±demo pairs read as wash, demo demoted). Also burned it5–it7 chasing a **story-subject "vertical" axis that does not exist in the world model** (founder vs healthcare/legal) — wasted motion inside the correct story angle, non-fatal.

**QUOTES:**
- it2: "The named-customer story angle beats the outcome angle… produced the batch's only purchase."
- it8: "broad-audience cells produced 7 of 8 lifetime purchases on ~$894 … reshape the standing portfolio toward broad-audience cells."

**Lock-on timing:** story by **it3**; audience→broad only at **it8** (last iteration); it5–it7 diverted onto the phantom vertical axis.

**$-left-on-table:** achieved **$200/buy** vs oracle $130/buy (~1.5×); losses = demo neutralization + several batches of interest-audience spend before the it8 broad reshape.

---

## WORLD 15 — revenue $261 (win, highest)

**ORACLE:** `story · demo=True · sales · broad` @ **$74/buy** (cheapest cell of all five worlds; ~2.7 expected buys per $200 batch). Proof pref: **story 0.825**; demo_gate 0.58 (large demo lift).

**FINAL-POLICY (v8):** proof = **story** (2–3 distinct story executions/batch; benefit-demo demoted to every-3rd-batch challenger); demo = **only ever attached to benefit/count, never to story** (all story ads demo=False); objective = **sales**; audience = **sales-broad largest ($75)**, biztools/niche smaller, pv/leads probes.

**AXIS-MATCH:** proof **HIT** · demo **MISS at the winning combo** (story ran demo=False; demo bolted to the weaker benefit angle) · objective **HIT** · audience **HIT**. → 3/4.

**EVER-FUNDED-ORACLE-CONFIG:** **NO.** `story+demo` was never funded in any cell — the agent froze "story" and "benefit+demo" as two **separate** incumbents from it3 and never crossed them. It concluded demo was neutral by comparing **benefit**-demo vs benefit-nodemo, and never discovered the story×demo interaction that is the $74/buy jackpot (0.269 buys/$20). It still won because story-nodemo/sales/broad buys strongly on its own.

**CLASSIFICATION:** **FOUND-AND-HELD** on story/sales/broad (top revenue, 9 buys), with the demo axis a **NEVER-TESTED interaction gap** — the single best config in the best world went untested, so even the winner left the largest multiplier of any world unclaimed.

**QUOTES:**
- it5: "The story family is the portfolio's strongest creative: 5 of 8 lifetime settled purchases."
- it3 (adopted): "story and benefit+demo hold two guaranteed slots" — demo permanently pinned to benefit, never to story.

**Lock-on timing:** story lead by **it3**, sales-broad concentration it4–it7; held throughout (found early).

**$-left-on-table:** achieved **$178/buy** vs oracle **$74/buy** (~2.4× worse); despite the highest revenue, never funding story+demo forfeited the biggest single upgrade available in any of the five worlds.

---

## Cross-world summary (11–15)

| World | Rev | Oracle-best | Oracle $/buy | Achieved $/buy | Axis hits | Oracle config funded? | Class |
|---|---|---|---|---|---|---|---|
| 11 | $0 | story+demo/sales/broad | $176 | ∞ | 1/4 | **No** | **FALSE-BELIEF** → never-tested → declared-dead (refuted) |
| 12 | $116 | story+demo/sales/broad | $122 | $400 | 3/4 | Yes, misread | FOUND-AND-HELD (+demo misread) |
| 13 | $174 | count+demo/sales/broad | $223 | $267 | ~4/4 | **Yes, exploited** | FOUND-AND-HELD |
| 14 | $232 | story+demo/sales/broad | $130 | $200 | 3/4 | Yes, exploited | FOUND-AND-HELD (+demo misread) |
| 15 | $261 | story+demo/sales/broad | $74 | $178 | 3/4 | **No** (story×demo never crossed) | FOUND-AND-HELD (demo interaction never-tested) |

**Recurring pattern:** every world's oracle-best config wants **demo=True**, and four of five want **story**. The winners (12–15) all found story+sales+broad; the systematic leak across them is the **demo axis** — either misread as a wash (12, 14) or never crossed with story at all (13-partial, 15). World 11 is the outlier: it anchored on the wrong proof angle (outcome) at iteration 1 from sign-up-layer data, never funded story+demo, and misdiagnosed a live market ($176/buy, ~1.1 buys/batch achievable) as a dead destination.

---

# Ad-Optimization Audit — Worlds 16–20

Convention: achieved $/buy = $1600 total spend / total settled buys in the 8-iteration run. Axis match is vs the single oracle-best config (#1); frontier proximity noted where relevant.

---

## WORLD 16 — "best comparison case"
- **ORACLE:** #1 `benefit / demo=True / pageviews / interest_niche` @ **$79/buy**. (#2 benefit-demo-leads-niche $82; #3 benefit-demo-**sales**-niche $88.) Buyer proof spread modest; benefit×demo is the dominant lever.
- **FINAL-POLICY:** Creative = all slots `benefit×demo` (LEAD/CHALLENGER/FRESH hooks). Budget = purchases-only scoreboard over a fixed 5-cell set {sales-broad, leads-broad, pv-broad, sales-biztools, sales-niche}, proportional to $/buy → **leads-broad ~$75, sales-broad ~$55**, all others at $15 floors. Effective config: benefit / demo=True / **leads(+sales)** / **broad**.
- **AXIS-MATCH:** proof **HIT** (benefit) · demo **HIT** (True) · objective **MISS** (leads/sales vs pageviews) · audience **MISS** (broad vs interest_niche). 2/4.
- **EVER-FUNDED-ORACLE-CONFIG:** Exact #1 (pageviews×interest_niche) **NO — never in the portfolio**. pageviews ran *only* on broad (pv-broad, every batch); interest_niche ran *only* on sales (sales-niche); the cross cell was never a design element. Near-oracle #3 (benefit-demo-**sales**-niche) *was* funded and **bought** (it2 1 buy $11.67; it8 1 buy), but sales-niche only ever ran at $7–15 floors so it accrued few buys while cheap broad cells accrued 10 of 12.
- **WHY COVERAGE DIDN'T PUSH IT:** the coverage ledger tracked **angle×demo only**, not objective×audience; once that ledger read "fully priced," refinement was unlocked and budget flowed to a purchases-only scoreboard that rewards the high-volume cheap **broad** cells. pageviews was pinned to broad and niche pinned to sales, so pv×niche was structurally unreachable.
- **CLASSIFICATION:** **NEVER-TESTED** (oracle-best objective×audience cell was a coverage gap) — but won anyway because it did FIND-AND-HOLD the dominant creative lever benefit×demo from it3 on.
- **QUOTES:** it1 — *"the policy keeps an explicit coverage ledger (angle x demo)"*; it7 — *"The purchases-only scoreboard now ranks leads-broad, not the hardcoded engine sales-broad, as the top purchase-per-dollar cell."*
- **DOLLARS-LEFT:** 12 buys → **$133/buy** vs oracle $79; it held the right creative but spent it in $133-geography instead of the $79 pv/niche cell it never built — the win ($348 vs $203) came from benefit×demo, not from finding the cost floor.

---

## WORLD 17
- **ORACLE:** #1 `story / demo=True / sales / interest_niche` @ **$244/buy** (#5 `outcome / demo=False / leads / broad` $272). Market effectively dead: best config still costs $244 to make one $29 sale.
- **FINAL-POLICY:** house angle = **outcome** (plain); **leads-broad $150 anchor**; sales = two ~$25 probes with rotating audiences; count de-weighted. Effective config: outcome / demo=False / leads / broad.
- **AXIS-MATCH vs #1:** proof **MISS** · demo **MISS** · objective **MISS** · audience **MISS** = 0/4 — **but this exactly equals oracle #5 (outcome/plain/leads/broad, $272), a top-5 frontier cell → 4/4 vs the frontier.**
- **EVER-FUNDED-ORACLE-CONFIG:** #1 story+demo+sales+niche **NO** (story ran in sales-niche only once, non-demo, it7, 0 buys). All 4 lifetime buys landed in **leads-broad** (it3/it7/it8 outcome, it5 benefit).
- **CLASSIFICATION:** **FOUND-AND-HELD** (locked outcome/leads/broad = oracle #5 by it3, held to v8) — in a near-dead market where ground truth *would* support DECLARED-DEAD ($244 ≫ $29) but the agent never declared it.
- **QUOTES:** it7 — *"all three purchases ever came from the leads-broad cell … concentrate the portfolio further on the leads-broad anchor"*; it4 — *"outcome owns all house slots every batch."*
- **DOLLARS-LEFT:** 4 buys → **$400/buy** vs oracle $244; frontier is flat and catastrophic, so almost nothing was recoverable — the $116 is roughly the best a rational agent could scrape.

---

## WORLD 18
- **ORACLE:** #1 `story / demo=True / sales / broad` @ **$115/buy**, dominant by a wide margin (0.175 buys vs 0.114 next). demo_gate 0.66 (running plain costs ~34% of purchase prob). Story is best proof (0.753).
- **FINAL-POLICY:** house angle = **benefit** (two benefit variants/batch); traffic layer **pageviews ~$65 primary**, leads $15 probe; three sales cells equal ~$40. Effective config: benefit / mixed-demo / pageviews+sales / broad+niche+biztools.
- **AXIS-MATCH vs #1:** proof **MISS** (benefit vs story) · demo partial→**MISS** (house not demo-locked) · objective **MISS** (pageviews-primary vs sales) · audience **HIT** (broad funded). ~1/4.
- **EVER-FUNDED-ORACLE-CONFIG:** exact #1 (story **+demo**) **NEVER** — story ran *only plain* and thinly (sales-broad it1/it4/it5/it6, all 0 buys; best was it4 2 signups on $13). The true winner was kneecapped by never pairing it with the demo the oracle requires. Agent's 4 buys came from benefit (pv-broad ×2, sales-niche ×1) + outcome-demo (sales-niche ×1), all in it2–it5; it6–it8 scored **zero**.
- **CLASSIFICATION:** **FALSE-BELIEF** — adopted a benefit-first house thesis (built on lucky pv/niche buys) that contradicts the story-dominant ground truth, and never gave story+demo+sales+broad a funded test.
- **QUOTES:** it5 — *"The benefit angle family is involved in 3 of 4 lifetime purchases … creative selection should lean benefit-led"*; it7 — *"pv-broad holds 2 of the 4 lifetime purchases … traffic-layer budget should lean from leads toward pageviews."*
- **DOLLARS-LEFT:** 4 buys → **$400/buy** vs oracle $115 — the largest avoidable gap of the set; the single dominant cell (story+demo+sales+broad) sat unexploited the entire run.

---

## WORLD 19 — "demo nearly irrelevant"
- **ORACLE:** #1 `benefit / demo=True / pageviews / broad` @ **$310/buy**; #2 identical but **demo=False $313** → demo axis is immaterial here.
- **FINAL-POLICY:** `pageviews-broad` runs a **plain (no-demo) benefit** ad as the engine (largest budget); sales-broad secondary runs outcome; fresh-copy-each-batch discipline. Effective config: benefit / demo=False / pageviews / broad.
- **AXIS-MATCH vs #1:** proof **HIT** · demo **HIT** (agent's demo=False = oracle #2, immaterial) · objective **HIT** · audience **HIT**. **4/4.**
- **EVER-FUNDED-ORACLE-CONFIG:** **YES, repeatedly.** pv-broad benefit produced the jackpot at it4 (3 buys on $16.67, in-cell ROAS 5.2) and again it8 (1 buy) — 4 of 6 lifetime buys.
- **DEMO-AXIS WASTE:** Minimal. The agent **correctly kept the engine plain**, noting at it4 the demo variant took 0 signups there. The one demo-axis iteration (it6, "attach demo to outcome") was confined to *sales* cells and was near-worthless given demo's irrelevance, but it did not compromise the pv-broad engine.
- **LOCK TIMING:** engine identified at **it4/8**, held and only refined (fatigue/freshness) through it8 → FOUND-AND-HELD, not late.
- **CLASSIFICATION:** **FOUND-AND-HELD.**
- **QUOTES:** it4 — *"Plain benefit-led creative in the pageviews-broad cell is the portfolio's engine"*; it4 — *"the demo variant of the same angle in the same cell took 0 sign-ups."*
- **DOLLARS-LEFT:** 6 buys → **$267/buy**, at/above oracle expectation of $310/buy → **~nothing left on table**; near-optimal exploitation.

---

## WORLD 20
- **ORACLE:** #1 `story / demo=True / sales / broad` @ **$535/buy** (all top configs broad; demo_gate 0.49 → demo strongly matters). Market unwinnable: $535 to make one $29 sale.
- **FINAL-POLICY:** creative identity = **count+demo** (every non-story slot a count-demo variant); sales pool **biztools-max (~30/70 broad/biztools)**, purchase-first sales ≥$120; one protected non-demo story slot. Effective config: count / demo=True / sales / interest_biztools.
- **AXIS-MATCH vs #1:** proof **MISS** (count vs story) · demo **HIT** (True; correctly demo-heavy) · objective **HIT** (sales) · audience **MISS** (biztools vs broad). 2/4.
- **EVER-FUNDED-ORACLE-CONFIG:** #1 story+demo+sales+broad **funded once** (it3 story-demo sales-broad, $13, 0 buys — invisible at 0.037 expected buys). The agent's 3 buys: it1 story-named **leads-broad**, it6 & it7 **count-demo sales-biztools** — the latter two (heavy positive noise in a $535/buy market) drove the entire final thesis.
- **CLASSIFICATION:** **FALSE-BELIEF** — from 2 lucky biztools purchases it declared count-demo "the portfolio's purchase-producing creative" and rebuilt its whole identity + audience concentration around it, contradicting the story/broad ground truth; it8 already caught it breaking. (Ground truth would have supported **DECLARED-DEAD** given $535/buy ≫ $29, but the agent did the opposite and over-fit noise.)
- **QUOTES:** it7 — *"count-demo is the portfolio's purchase-producing creative and the slate should concentrate on count-demo variants"*; it8 — *"The biztools-max concentration era halved per-dollar sign-up production … and settled no purchase."*
- **DOLLARS-LEFT:** 3 buys → **$533/buy** vs oracle $535 — market is unwinnable regardless, so ~zero recoverable; the $87 "win vs $0" is expected noise, not skill.

---

### Cross-world summary (16–20)
| W | Oracle #1 $/buy | Achieved $/buy | Axes hit | Oracle-#1 funded? | Class |
|---|---|---|---|---|---|
| 16 | $79 (benefit/demo/pv/niche) | $133 (12 buys) | 2/4 | No (pv×niche cross never built; #3 sales-niche bought) | NEVER-TESTED (held benefit×demo) |
| 17 | $244 (story/demo/sales/niche) | $400 (4 buys) | 0/4 vs#1, 4/4 vs#5 | No | FOUND-AND-HELD (frontier #5; near-dead mkt) |
| 18 | $115 (story/demo/sales/broad) | $400 (4 buys) | ~1/4 | No — story never run with demo | FALSE-BELIEF (benefit-first) |
| 19 | $310 (benefit/demo/pv/broad) | $267 (6 buys) | 4/4 | Yes (it4 jackpot) | FOUND-AND-HELD |
| 20 | $535 (story/demo/sales/broad) | $533 (3 buys) | 2/4 | Once, 0 buys | FALSE-BELIEF (count-demo noise; mkt dead) |

Note on markets: **w17 ($244) and w20 ($535)** are effectively dead vs the $29 price — a DECLARED-DEAD conclusion would have been ground-truth-supported in both; neither agent declared it. **w18 ($115)** was the biggest unforced error (dominant story+demo+sales+broad cell never funded). **w19** is the clean success. **w16** is the instructive case: right creative lever, wrong geography, won on the lever alone.

---

# Ad-Optimization Audit — Worlds 21–25

All worlds: 8 iterations × ~$200 = ~$1600 total spend. Product price $29. "Achieved $/buy" = $1600 ÷ total buys.

---

## WORLD 21 — LOSS (loop $29 vs base $87)
- **ORACLE:** story · demo=True · sales · broad — $151/buy (0.132 buys/$20). This world was decently buyable (~1.3 buys/$200 batch at oracle).
- **FINAL-POLICY:** proof=**story** (2 guaranteed story slots, anchor+challenger); demo=**false** (demo retired as "non-additive"); objective=**sales** (sales-majority, concentration onto sales-broad, biztools parked); audience=**broad**. Also kept a clicks-broad cell (chased its one purchase).
- **AXIS-MATCH:** proof HIT · demo **MISS** · objective HIT · audience HIT (3/4).
- **EVER-FUNDED-ORACLE-CONFIG:** **NO** for the exact config — story+demo=True was *never once run* (demo only ever paired with the outcome family). The demo=**false** twin (oracle rank #4, $228/buy) was funded repeatedly in sales-broad and returned ~0 buys; the run's single purchase (iter 3) came from story in **clicks**-broad, not sales.
- **CLASSIFICATION:** **FOUND-AND-HELD on 3/4 axes, but LOST to bad luck on the correctly-chosen config + a demo coverage-gap driven by a false belief.** It held story/sales/broad but (a) generalized "demo adds nothing" from an outcome-only deconfound and never tested story+demo=True, and (b) declared the whole (buyable) market structurally unreadable — a FALSE-BELIEF contradicting ground truth. Locked story ~iter 5, concentration iter 6; iters 1–5 spent sweeping/deconfounding before the direction settled.
- **QUOTES:** "demo footage is not what made outcome creative work; the angle was" (it4). "The purchase layer is structurally unreadable at the portfolio's granularity" (it6).
- **DOLLARS LEFT:** Achieved $1600/buy (1 buy) vs oracle $151/buy — a buyable ~1.3-buys/batch world was treated as noise and the demo=True lift never captured, so ~$280 of attainable revenue went unearned.

---

## WORLD 22 — LOSS/TIE (loop $0 vs base $0)
- **ORACLE:** **count** · demo=True · sales · broad — $253/buy (0.079 buys/$20). Hardest world here; even oracle ROAS ≈ 0.11 (29/253), so profit was ~impossible, but ~6 buys over the run were physically attainable — not truly hopeless.
- **FINAL-POLICY:** proof=**count** (count-first, 2 count slots + probe); demo=**false** (never used); objective=**sales** (≥2 of 3 cells sales); audience=**tilted to interest_niche** ($90 niche vs $44 broad).
- **AXIS-MATCH:** proof **HIT** (correctly identified count) · demo **MISS** · objective HIT · audience **MISS** (down-weighted broad, the oracle audience).
- **EVER-FUNDED-ORACLE-CONFIG:** **NO** — count+demo=True never run (demo only paired with outcome). count/sales/broad demo=false was funded and returned 0 buys everywhere (consistent with the low buyability + bad luck).
- **CLASSIFICATION:** **NEVER-TESTED (coverage gap) + audience mis-tilt** on a near-unbuyable world. It nailed the count angle but steered budget to interest_niche using a **demos-per-dollar proxy** (0 purchases ever, so it optimized a signup surrogate) and never funded the demo=True lever. Not a DECLARED-DEAD case — it kept chasing demos rather than concluding broken. Locked count/sales ~iter 4; niche tilt iter 6.
- **QUOTES:** "count-angle ads cause more funnel entry than sibling angles" (it4). "the sales budget should tilt toward interest_niche" (it6).
- **DOLLARS LEFT:** Achieved $0 vs oracle $253/buy; ~6 oracle buys (~$174) were attainable but the demo=True lever + broad concentration were skipped and the niche tilt bled the best audience — though the world was structurally unprofitable regardless.

---

## WORLD 23 — LOSS (loop $29 vs base $58)
- **ORACLE:** story · demo=True · sales · broad — $220/buy (0.091 buys/$20).
- **FINAL-POLICY:** proof=**story** (all 3 slots plain named-story); demo=**false** (removed from vocabulary); objective=**sales** ($190 sales-majority); audience=**split evenly across all three** (~$63 each broad/biztools/niche — not concentrated on broad). Back half consumed by a CTA-intent (trial vs purchase) sub-experiment.
- **AXIS-MATCH:** proof HIT · demo **MISS** · objective HIT · audience **MISS** (even split, never concentrated broad).
- **EVER-FUNDED-ORACLE-CONFIG:** **YES** — story+demo=True ran in **sales-broad** twice: it2-story-demo ($13.33 → 1 signup, 0 buys) and it3-story-lena-demo ($13.33 → 0 signups, 0 buys). Both thin (~$13, expected <0.07 buys) and unlucky-zero, after which demo was **retired**. The run's lone purchase came from story/demo=false in sales-**biztools** (it5).
- **CLASSIFICATION:** **TESTED-BUT-MISREAD.** It funded the exact oracle creative in the oracle cell, drew 0 buys on ~$13 of thin spend, and wrongly concluded the demo overlay hurts — then never revisited it. Story locked immediately (iter 1); iters 6–8 were wasted on a washed intent axis instead of concentrating on broad or reinstating demo.
- **QUOTES:** "the demo overlay measurably drags the story chassis" (it3). "trial-intent is the working default … while the comparison continues" (it8 — back-half spent on an irrelevant axis).
- **DOLLARS LEFT:** Achieved $1600/buy (1 buy) vs oracle $220/buy; even the demo=false twin (~$356/buy #5) beat what it realized. Killing demo on two $13 unlucky cells + spreading audience instead of pressing broad left ~$180 of attainable revenue and cost it the baseline.

---

## WORLD 24 — RELATIVE WIN (loop $145 total, 5 buys)
- **ORACLE:** story · demo=True · sales · broad — $116/buy (0.172 buys/$20; richest-but-25).
- **FINAL-POLICY:** proof=**story** (every ad named-story); demo=**false** (overlay retired); objective=**sales** (sales-broad the primary cell); audience=**broad** (sales-broad $100 anchor).
- **AXIS-MATCH:** proof HIT · demo **MISS** · objective HIT · audience HIT (3/4).
- **EVER-FUNDED-ORACLE-CONFIG:** demo=True funded once (it3-story-demo, sales-broad, $16.67 → 2 signups, 0 buys) then retired; the demo=**false** story/sales/broad twin was heavily funded and produced **all 5 purchases** (it3/it4/it5/it8, every buy in sales-broad).
- **CLASSIFICATION:** **FOUND-AND-HELD.** Matched proof/obj/aud by iter 3–4 and exploited sales-broad story for the rest of the run. Only leak: demo=True dropped early on one thin cell.
- **QUOTES:** "Every settled purchase of the run … has come from the sales-broad cell across two eras and two different story ads" (it4). "sales-broad receives at least $100 (half the batch)" (it4).
- **DOLLARS LEFT:** Achieved $320/buy (5 buys) vs oracle $116/buy — right cell, but demo=false and the $100 (not full) concentration left ~half the oracle's ~13.8 attainable buys on the table.

---

## WORLD 25 — TIE (loop $58 vs base $58, oracle $94/buy — richest world)
- **ORACLE:** story · demo=True · sales · broad — $94/buy (0.213 buys/$20); biztools/niche also buyable ($102/$127).
- **FINAL-POLICY:** proof=**story** (2 story-variant slots + rotating comparator); demo=**false** (overlay retired); objective=**sales** ($140 sales-weighted); audience=**frozen near-equal thirds** (broad $46 / biztools $47 / niche $47) under an "accumulation bar," plus $30 pageviews + $30 leads reference cells.
- **AXIS-MATCH:** proof HIT · demo **MISS** · objective HIT · audience **MISS** (spread thirds; actively moved *off* broad).
- **EVER-FUNDED-ORACLE-CONFIG:** **NO** — story+demo=True never run (demo only paired with outcome, then retired). Both purchases (iter 5) landed in sales-**biztools** and exp-sales-**niche**, never broad; the thin $46 broad story cell drew 0 buys.
- **CLASSIFICATION:** **TESTED-BUT-MISREAD (audience) + demo coverage-gap.** In a world with huge headroom it got proof/obj right but read 2 lucky non-broad purchases as evidence to re-weight *away* from broad, then froze an equal-thirds split so no cell (least of all broad) ever got concentration; the demo=True lever that ~doubles the buy rate was never funded. Story locked iter 3; audience whipsawed iters 4–6 (broad-first → interest-first → frozen thirds), diffusing $1600 across ~5 cells + reference probes.
- **QUOTES:** "the first two settled purchases both landed in premium interest audiences … re-weight sales budget toward them" (it5). "purchases are too rare to steer per-era budget; … hold a fixed, diversified split" (it6).
- **DOLLARS LEFT:** Achieved $800/buy (2 buys) vs oracle $94/buy — a world offering ~17 buys ($493) was diffused into thirds and reference cells; failing to concentrate on broad *and* skipping the demo=True lift left the loop tied with baseline despite the largest headroom of any world here.

---

### Cross-world pattern (21–25)
The single recurring killer is the **demo axis**: the oracle's best config is `demo=True` in every world, yet the agent retired demo in all five — either never testing it on the winning angle (w21/w22/w25 coverage gap) or funding it on one thin/unlucky cell and misreading it (w23/w24). Secondary killer is **audience diffusion/whipsaw** away from `broad` on tiny purchase counts (w22 niche tilt, w23 even split, w25 frozen thirds). w24 succeeded precisely because it did the one thing the others didn't: concentrate on sales-broad and hold.
