# Tier B qualitative audit — what went wrong (2026-07-20)

Scope: the first full Tier B experiment — 10 fresh worlds (60–69), 8 iterations,
$200/batch, agent gpt-5.6-terra, judge gpt-5.6-luna, default noise schedule,
semantic-gradient v2 (merged doc: Codex v2 interface + the four audit rules +
fixed-floor amendment). Headline numbers: realized revenue loop $15,138 vs
frozen baseline $14,645 (+3.4%, 5W/5L); held-out expected ROAS +20% mean
(0.86→1.03), positive in 7/10 worlds.

Method: two independent auditors read every run's summary.json, lineage.json
(all theses/rungs/draws), evidence.json (actual ad copy + receipts), final
policy, and held-out evaluations, and compared conclusions against each
world's sealed persona ground truth (worlds spent; reveal licensed). Full
auditor reports verbatim below the synthesis.

## Synthesis — the five findings, ranked by severity

### 1. RULE-FIXATION became the dominant systemic disease (9–10 of 10 worlds)

The audit rules (readable floor, demotion floor, coverage, fixed arithmetic)
were written to GATE verdicts. The terra agent instead made them the CONTENT
of the work: 50–88% of iterations across nearly every world were spent on
floor/ledger arithmetic rather than advertising judgment.

- w64 is the pure case and the only true policy destruction: eight straight
  bookkeeping theses, zero creative or audience decisions ever, final thesis
  literally a ledger correction ("cells have $290—not $240 — accounting must
  use the verified $309.85"), held-out REGRESSED −0.084, and it deleted the
  multi-objective coverage that made its own frozen baseline earn $1,334.
- w67 made a genuine leader-concentration discovery at iter 6 and REVERSED it
  at iter 7 out of coverage anxiety ("the v6 leader-concentration portfolio is
  premature… coverage must precede further local ROAS exploitation").
- w69 spent 7 of 8 iterations on dose-equalization arithmetic; w62 spent 4
  iterations refusing to change anything while narrating spend creeping toward
  a floor ("run the existing portfolio unchanged for exactly one more batch"),
  and won only because its 2 real iterations nailed a demo-gated market.

Why it exploded at Tier B when it was mild in the Fable Tier A runs: (a) the
agent is stronger and more literal — it executes the rule text faithfully to
the point of goal displacement; (b) Tier B's richer cell space (3 ads × 3
audiences × 4 objectives) makes per-cell readable floors combinatorially
expensive — a floor doctrine that was satisfiable in Tier A's coarse space
becomes a treadmill that can absorb every iteration.

### 2. The ads were never rewritten — the gradient ignored its strongest new lever

In the five losing worlds the three ad creatives were essentially identical
from iteration 1 to 8 (headline changes amounting to punctuation). Only w68
ever authored a new creative (ad_demo_request_status) — and it was the
strongest run of the ten. Tier B's entire realism upgrade is that copy
quality matters; the gradient spent its iterations on budget allocation and
bookkeeping prose instead of the surface the judge actually reads.

### 3. The story angle was never tested in ANY of the 10 worlds

The seed policy declares four angle families (benefit, outcome, count,
story/named-customer). No world ever shipped a story ad — while the sealed
ground truth shows story is the TOP preference of the highest-buyiness
personas in at least w60, w61, and w67. This is the world-3 coverage lesson
recurring at Tier B: the coverage ledger got pointed at cell-level crosses
(ad×audience×objective) and consumed there, so the declared angle vocabulary
— the coarser and more valuable axis — never got swept. Coverage at the
wrong granularity.

### 4. The biggest revenue win was a false discovery (w61, +$812)

Its "outcome wins interest_biztools" belief chased a cell whose noise-free
expected rate is ~0.16 purchases per $33 batch — the 11 in-run purchases
there were variance. The durable gain (held-out +0.066 only) traces to one
mundane change: concentrating budget on the sales objective. The gradient's
guards did not stop a lucky cell from absorbing $100/batch of allocation.
(Contrast w62/w68, whose demo discoveries match the persona ground truth and
whose held-out gains are honest.)

### 5. The default schedule's variance let the loop underperform its own policy

Mean selected rung 4.06; in 80 draws, keep/rung-1 occurred twice; worlds
kept drawing rungs 3–6 at iteration 8. w66 improved its policy (+0.248
held-out) and still lost $638 realized because mid-run draws sampled low
rungs while the frozen replay of a strong iter-1 design drew hot. w63 burned
its two lowest-ROAS batches on iter-1/2 rung-6 coverage rewrites. The
schedule was tuned for Tier A's weak-seed regime; in Tier B the iteration-1
policy is already good and perpetual rewriting is churn.

## Classification table (all 10 worlds)

| World | Realized Δ | Held-out Δ | Class |
|---|---|---|---|
| 60 | +29 | +0.42 | WIN — honest demo discovery; 4/8 iters bookkeeping |
| 61 | +812 | +0.066 | FALSE DISCOVERY — lucky cell; durable gain = objective concentration |
| 62 | +348 | +0.551 | WIN despite 75% bookkeeping — 2 good iters matched demo-gated market |
| 63 | −116 | −0.028 | EXPLORATION-TAX + late unconfirmed reversal (outcome→demo on one pair) |
| 64 | −580 | −0.084 | RULE-FIXATION (pure) — zero creative decisions, deleted winning coverage |
| 65 | −29 | −0.046 | EXPLORATION-TAX — right creative, over-concentrated one niche cell |
| 66 | −638 | +0.248 | SAMPLING-LUCK — policy improved, dice cold vs lucky frozen replay |
| 67 | +377 | +0.281 | WIN with disease — real discovery reversed by coverage anxiety, then recovered |
| 68 | −58 | +0.206 | BEST RUN — demo default, only new creative authored, honest routing |
| 69 | +348 | +0.111 | WIN on concentration + luck; 7/8 iterations bookkeeping |

## Patch candidates (evidence discipline: adopt only what many worlds demonstrate)

1. ADOPT-GRADE (9+ worlds): **Verdict-vs-design separation.** Floors and
   ledgers gate VERDICTS (what may be believed/demoted); they never gate
   DESIGN. Every batch must be designed to maximize the goal under current
   beliefs; a thesis that contains no creative/audience/objective decision —
   pure ledger reconciliation — is not a thesis and may not be adopted.
2. ADOPT-GRADE (10/10 worlds): **Angle vocabulary outranks cell crosses in
   sweep priority.** The declared creative-angle families must each receive a
   funded test before cell-level (ad×audience) coverage obligations may
   consume the experiment slot. (Story never tested anywhere.)
3. STRONG (5+ worlds): **Creative is a learnable surface, not a constant.**
   The gradient should treat rewriting ad copy as a first-class move; a
   standing expectation of at least one fresh variant of the incumbent-best
   creative per batch (w68's innovation preceded the best run).
4. STRONG (w66/w68/w63 + rung data): **Schedule calibration.** Run the
   pre-designed default-vs-conservative comparison on the same worlds; the
   default's <1% keep-probability at iteration 8 is wrong for a strong-seed
   regime.
5. REGISTER (w63, w67): **Churn-brake.** A single fresh matched pair may not
   reverse a confirmed incumbent; reversal requires the same replication
   standard that promotion did.

## Auditor report A — losing worlds (verbatim)

Cross-cutting fact: in every world the three ad creatives were essentially
never rewritten (headlines identical from iter 1→8 except an added period;
w68 alone introduced a genuine new creative, ad_demo_request_status). The
agent spent its entire "semantic gradient" on budget allocation and prose
bookkeeping, not copy. Creative mapping (from copy + judge per-$ efficiency):
ad_outcome_followup="Stop chasing missing client details"=outcome proof;
ad_demo_workflow="Turn every client intake email into one clear workflow"=
benefit/workflow proof; ad_count_templates="One intake link for briefs,
onboarding…"=count proof.

### WORLD 63 — loop $754 vs frozen $870 (−116); held-out −0.028 (flat)

STORY. Iters 1–2 (rungs 6,6) burned two whole-policy rewrites on a "readable
floor / coverage-completion" doctrine — spend must reach a "$66.65
one-purchase readable floor" and all owed cells be filled before any creative
call — which dragged the loop through its two lowest draws (roas 0.29,
0.145). Iters 3–7 it correctly converged on outcome as the local sales
winner, then iter 8 reversed itself, flipping the confirmed outcome incumbent
back to demo on one fresh pair. Held-out barely moved (0.558→0.529), so the
policy neither improved nor materially decayed; the realized −116 is mostly
the loop eating early coverage-cost draws while the frozen baseline caught a
lucky 1.015 spike at iter 3.

PRIMARY FAILURE CLASS: EXPLORATION-TAX (early readability/coverage iterations
cost in-run revenue for near-zero net policy gain; compounded by cold dice
and a needless late churn reversal).

QUOTES.
- i1: "future spend should be concentrated into explicit one-ad, one-audience
  sales cells before any creative, audience, or objective preference is made."
- i2: "completing those owed sales-cell tests before refining or demoting the
  portfolio is the most credible route to improved settled purchase ROAS."
- i8 (the reversal): "the latest broad pair makes demo—not outcome—the
  current broad incumbent."

GROUND-TRUTH CHECK. Partial mismatch: the dominant high-buyiness personas
(skeptic w=0.335 buy=0.93; pragmatist buy=0.77 outcome=1.57) reward outcome
among the available proofs, yet the agent's final broad incumbent was demo —
it had the right answer at iter 7 and threw it away.

FIX. A churn-brake forbidding a single fresh matched pair from reversing an
already-confirmed creative incumbent (would have kept the iter-7 outcome
lead), plus capping consecutive "coverage/readability" rungs.

### WORLD 64 — loop $754 vs frozen $1334 (−580); held-out −0.084 (REGRESSED, worst)

STORY. The agent never made a single creative or audience decision across all
8 iterations. Every thesis is arithmetic about "readable-spend floors" and
"demotion doses": iter 1–6 chase the "$199.95 readable floor," iter 7 the
"$599.85 demotion floor," and iters 7–8 are literally correcting its own
ledger errors ("$290—not $240"). Meanwhile it collapsed the initial
multi-objective / multi-audience portfolio (pageviews, leads, biztools,
niche) — which is exactly what made the frozen baseline earn $1334 — into
three equal dedicated broad-sales cells, discarding the coverage that was
converting. Held-out regressed a real −0.084: this is genuine policy
destruction, not dice.

PRIMARY FAILURE CLASS: RULE-FIXATION (bookkeeping/floor arithmetic wholly
displaced advertising judgment — the confirmed diagnosis).

QUOTES.
- i7: "completing equal matched sales/broad demotion doses before releasing
  any current ad slot to story."
- i8 thesis: "The current three dedicated sales/broad creative cells have
  $290—not $240—of settled spend each, so fixed-dose accounting must use the
  verified $309.85 remaining to the $599.85 demotion floor."
- i8 falsifier: "A reconciliation of settled receipts showing that any
  current dedicated sales/broad cell has accumulated $240 rather than $290
  would reject the thesis."

GROUND-TRUTH CHECK. Mismatch by abdication: the agent picked no creative at
all, so it never exploited outcome (judge's per-$ leader, 0.041) or count
(herd w=0.213 + bargain w=0.183 both prefer count) — and it deleted the
audience coverage the population actually rewarded.

FIX. A guardrail rejecting any iteration whose thesis only re-reconciles
spend floors without a creative/audience/objective decision, plus preserving
baseline multi-cell coverage instead of collapsing to three broad cells.

### WORLD 65 — loop $928 vs frozen $957 (−29); held-out −0.046

STORY. After two readability-doctrine rewrites (iter 2 rung 6), the agent
locked onto the sales/interest_niche cell where outcome genuinely led (iter
6: "6 settled purchases on $180 versus demo's 0"). It then over-concentrated:
the iter-8 whole-policy rewrite (rung 6) made outcome the sole niche
allocation and dropped its comparators. The problem is it over-fit one cell —
the judge's broadly most-efficient creative was actually count (0.028 pur/$
vs outcome 0.013 across broad), so narrowing to niche-outcome nudged held-out
down (0.496→0.451) and the late iterations drew cold (0.14, 0.0, 0.29).

PRIMARY FAILURE CLASS: EXPLORATION-TAX (sound-but-narrow learning — outcome
does lead in niche — over-concentrated into a single cell, discarding the
broadly-efficient count creative; small net regression plus cold late dice).

QUOTES.
- i6: "outcome framing has produced 6 settled purchases on $180 versus demo's
  0 on $130 and count's 1 on $130 in the matched sales/interest_niche cells."
- i8: "A whole-policy rewrite makes outcome the sole direct sales/niche
  allocation after both comparator treatments crossed their fixed demotion
  floors."

GROUND-TRUTH CHECK. Match on the dominant persona: peer_proof (w=0.327,
buy=1.00, reach niche=0.93) prefers outcome among available proofs — so
outcome-in-niche was directionally right; the error was breadth, not
direction.

FIX. Require broad-audience coverage to be retained before a rung-5/6 rewrite
may collapse to a single cell — that would have surfaced count as the
broad-efficient creative rather than tunneling on niche-outcome.

### WORLD 66 — loop $1769 vs frozen $2407 (−638); held-out +0.248 (IMPROVED)

STORY. The policy genuinely got better: it concentrated broad sales onto
ad_demo_workflow, and the held-out judge rewarded exactly that
(final-expected demo scaled to $125 → 7.01 purchases, roas 0.559→1.59). The
large −638 realized gap is not a policy failure — the loop's seeded rung
schedule drew low-ROAS rungs mid-run (iters 4–5: 0.43, 0.58) while the frozen
replay of the strong iter-1 design kept drawing high (2.32, 1.89). Cold dice
on a design that improved.

PRIMARY FAILURE CLASS: SAMPLING-LUCK (held-out +0.248; realized revenue lost
entirely on the schedule's rung draws).

QUOTES.
- i4: "direct-sales allocation should default increasingly to
  ad_demo_workflow across audiences while non-demo treatments are retained
  only to the extent needed to falsify that demo advantage."
- i8: "direct demonstration of Formflow's intake-to-workflow sequence
  directionally produces higher settled purchase ROAS than outcome-led
  follow-up framing."

GROUND-TRUTH CHECK. Judge/population divergence: the agent chased the judge's
demo signal, but the dominant high-buyiness persona pragmatist (w=0.213,
buy=0.96, outcome=1.19) actually prefers outcome — so final direction matched
the judge (=revenue) but not the underlying population.

FIX. Nothing on the policy side; only reducing schedule variance (higher rung
floor / gentler decay) so the loop's own iterations exploit the improved
design instead of sampling low rungs.

### WORLD 68 — loop $3219 vs frozen $3277 (−58); held-out +0.206 (IMPROVED, richest world roas ~2.0)

STORY. The strongest run. The agent correctly read that ad_demo_workflow
dominates (judge per-$ 0.113, ~2.5× the others), made screen-recorded
demonstration the default, and was the only world to innovate new copy —
ad_demo_request_status — then sensibly declined to over-generalize, ending
with a cautious audience-specific routing. Held-out improved 1.98→2.19 and
realized revenue is at near-parity (−58); the tiny gap is pure dice (loop
drew 1.30 twice late vs frozen's 2.32/1.74).

PRIMARY FAILURE CLASS: SAMPLING-LUCK (policy improved, richest world,
realized gap trivial and dice-driven).

QUOTES.
- i3: "screen-recording workflow creative should be the default because it
  has repeatedly outperformed the otherwise-identical non-screen-recording
  workflow within readable matched audiences."
- i8: "audience-specific workflow routing: generic workflow for broad and
  interest_biztools, request-to-status for interest_niche."

GROUND-TRUTH CHECK. Qualitative match: the dominant high-buyiness personas
skeptic (buy=0.76, trust signal "visible product demonstration") and
peer_proof (buy=0.94, "specific workflow") reward exactly the screen-demo
direction the agent chose — even though pragmatist's numeric proof_pref
favors outcome, the demo/format signal aligns with the biggest-buyiness
trust cues.

FIX. None needed — sound run; only trimming schedule dice variance would have
erased the −58.

## Auditor report B — winning worlds (verbatim)

Cross-cutting finding (all 5 worlds): the "semantic gradient" is chronically
fixated on ledger arithmetic — "$X readable floor," "demotion floor,"
"unpriced cells," "coverage obligations" — rather than on advertising. Every
world burns its early iterations (and 3 of 5 burn the majority) reasoning
about accounting readability. Separately, the seed policy declares four angle
families (benefit, outcome, count, story/named-customer) but the agent only
ever ships demo / outcome / count — story is never tested in any world, and
story is the top preference of the highest-buyiness personas in w60, w61,
w67. That untested angle is the universal missed ceiling.

### WORLD 60 — loop $2349 vs $2320 (+29; held-out +0.42)

STORY: Iters 1–3 are pure floor/coverage bookkeeping (fund cells to the
"~$25 one-purchase readable floor"). Real creative reasoning starts at iter
4, initially demoting outcome, then at iter 6 flipping to "outcome wins
interest_biztools 3 purchases to 0." The headline thesis locked onto
outcome-biztools, but the actual revenue engine was ad_demo_workflow (40
sales purchases, ROAS-units 1.71, with 21 in sales_broad_demo) — the agent
under-credited its own biggest winner. The thin loop margin (+29) is because
the iter-1 seed design was already strong, so the frozen baseline nearly
matched it; the honest held-out +0.42 reflects that demo framing is genuinely
broadly effective.

WASTE AUDIT: 4/8 bookkeeping (iters 1,2,3,7 — iter 7 is a pure floor-audit
tangent about an "under-floor demotion" of the niche-outcome cell), 4/8
creative (4,5,6,8). Rule-fixation present but moderate.

DISCOVERY QUALITY: Winning copy is the demo "Watch/See every client request
move from intake to ready" and outcome's "Stop chasing missing client details
/ automatic reminders." Ground truth: highest-buyiness personas are skeptic
(0.97, story), pragmatist (0.91, outcome), peer_proof (0.86, story, reach
biztools 0.92). Outcome-biztools is a partial match (peer_proof's 2nd pref is
outcome), but the dominant preference is story, untested. Demo won because
herd/impulse demo_gate is high (0.69/1.00).

QUOTES: "outcome-focused creative should be preferred… in interest_biztools
sales only, because the sole readable simultaneous comparison there favored
outcome 3 settled purchases to 0"; "retain demo and count as default sales
capacity and use outcome only in explicit matched challenges"; "the readable
but UNPRICED interest_niche outcome sales cross must receive funded coverage
until its fixed approximately $75 demotion floor is reached."

Bigger/faster: Test a named-customer story ad and credit demo-broad as the
winner instead of chasing the biztools-outcome side-cell.

### WORLD 61 — loop $1798 vs $986 (+812; held-out +0.066) — THE DISCREPANCY

STORY: Iters 1–2 are floor/coverage ("$33.33 purchase floor," "complete the 3
missing cells"); iters 3–5 do genuine audience-specific creative work; iters
6–7 collapse back into floor-fixation ("restore interest_niche×count… its
continued omission is an unlicensed demotion"); iter 8 lands on "outcome wins
interest_biztools." The revenue-win/held-out-flat discrepancy resolves
cleanly in the design specs: the retained final design's only real, durable
change is moving all $200 onto the sales objective (initial split 120 sales /
40 pageviews / 40 leads → final 200 sales), which lifts expected ROAS only
0.583→0.649. The other ~$700 of loop revenue is sampling luck: in-run,
sales_biztools_outcome returned 11 purchases on $133 (ROAS-units 2.4), but
the noise-free model expects 0.16 purchases per $33 in that same cell — the
"discovery" is a variance artifact, and the frozen baseline additionally drew
an unlucky purchase sequence (0.29, 0.44…). The held-out is flat because the
policy over-allocated outcome to $100 chasing a cell that isn't actually
good, and never hard-concentrated.

WASTE AUDIT: 4/8 bookkeeping (1,2,6,7), 4/8 creative (3,4,5,8). Iter 6's
whole thesis is floor arithmetic ("below the fixed $99.98 demotion floor").

DISCOVERY QUALITY: Winning copy: outcome's "Missing details? Let the request
follow up for you." Ground truth says the real money is peer_proof (buy 1.10,
story, reach niche 0.83) and pragmatist (buy 0.82, outcome, reach broad
0.51) — interest_biztools is dominated by low-buyiness scroller (0.08). So
biztools-outcome is genuinely a weak cell; the agent chased noise into the
wrong audience and never tested story-niche or outcome-broad where the
buyiness actually is.

QUOTES: "The binding near-term lever is cell readability rather than a
creative, audience, or objective winner: concentrating each sales ad×audience
cell to at least the fixed $33.33 purchase floor…"; "Interest_niche×
ad_count_templates must be restored… its continued omission is an unlicensed
demotion"; "ad_outcome_followup has a repeatable settled-purchase ROAS
advantage over workflow-demo… in the interest_biztools audience."

Bigger/faster: Bank the sales-objective concentration (the real win) and
target outcome-broad + a story-niche ad instead of over-funding the lucky
biztools-outcome cell.

### WORLD 62 — loop $1044 vs $696 (+348; held-out +0.551, biggest policy gain)

STORY: The most extreme rule-fixation of the five: iters 1–4 are entirely
floor bookkeeping — the agent literally refuses to change anything, running
the "15-cell slate unchanged" while narrating the spend creeping toward the
"$66.65 readable floor" ($13.33→$26.66→$53.32→"one more batch"). Real
advertising begins only at iter 5 ("demote template/count in favor of
workflow-demo"), and iters 7–8 wander off into parking a new secure_link
cell. Yet the two genuine iterations (5–6) nailed it: this market is heavily
demo-gated (scroller demo_gate 1.03 @ weight 0.32; impulse 1.22 @ 0.20), so
"show the product working" is exactly right — producing the biggest held-out
gain despite the least exploration.

WASTE AUDIT: 6/8 bookkeeping (1,2,3,4,7,8) — 75% waste — 2/8 creative (5,6).
Textbook luck-bailed-out rule-fixation: the win came from 2 correct
iterations, not the 4 wasted "preserve the slate" ones.

DISCOVERY QUALITY: In-run winner is actually outcome (11 purch, 0.92) edging
demo (9, 0.75), both crushing count (1, 0.15); the agent credited demo.
Correct call regardless — the demo/outcome pair matches the demo-gated +
pragmatist-outcome market, and demoting count was right (count-preferring
herd is only 0.08 weight).

QUOTES: "preserve the complete current 15-cell slate until each cell is
readable rather than alter allocation on sub-readable purchase differences";
"run the existing three-ad, five-campaign portfolio unchanged for exactly one
more batch"; "concrete workflow and client-completion demonstrations should
replace template/count framing."

Bigger/faster: Start creative testing at iter 2 instead of iter 5 — four
"hold the slate" iterations were dead weight in a market whose demo-gate
signal was strong enough to read early.

### WORLD 67 — loop $1305 vs $928 (+377; held-out +0.281)

STORY: The churniest, most expensive run (16 agent calls, 611s). Iters 1–5
are all fragmentation/floor bookkeeping ("$100 readability floor,"
"concentrate on the lowest-funded unpriced cells"). Iter 6 finally
concentrates on leaders (demo-broad, outcome-biztools/niche) — then iter 7
reverses it back to coverage ("the v6 leader-concentration portfolio is
premature… coverage must precede further local ROAS exploitation"), and iter
8 re-settles on demo-broad. That iter-6→7 reversal is the clearest
rule-fixation symptom in the set: a real discovery undone by floor anxiety.

WASTE AUDIT: 6/8 bookkeeping (1,2,3,4,5,7), 2/8 creative (6,8). 75% waste
plus a self-inflicted reversal.

DISCOVERY QUALITY: In-run winner is outcome (17 purch, 0.91) > count (13) >
demo (12), but the final thesis credits demo-broad. Ground truth: the two
heaviest high-buyiness personas are authority (0.86, story, weight 0.27) and
skeptic (0.79, story, 0.26), then pragmatist (1.01, outcome, 0.17). So the
ceiling is story (untested), and outcome (pragmatist) is the best tested
proxy — demo-broad is a weaker pick since skeptic's demo_gate is 0.14.

QUOTES: "every exact sales creative-audience cell remains below its fixed
$100 readability floor… before any creative or audience preference is
inferred"; "The v6 leader-concentration portfolio is premature… coverage must
precede further local ROAS exploitation"; "the workflow-demo ad is the
settled purchase-ROAS leader in matched broad sales campaigns."

Bigger/faster: Don't let iter 7's coverage rule veto the iter-6 leader
concentration; commit to outcome (or test story) rather than reverting.

### WORLD 69 — loop $1218 vs $870 (+348; held-out +0.111)

STORY: Iters 1–4 are floor/coverage bookkeeping ("$66.65 readable floor,"
"complete the six open crosses"). One genuine creative iteration (5:
"audience-specific fit — outcome for broad+niche, demo for biztools"). Then
iters 6–8 are consumed by exposure-anxiety bookkeeping — the agent worries
its own preference is "partly exposure-driven" and spends three iterations
re-leveling doses to the "$199.95 demotion floor" instead of exploiting. The
win came from moving spend onto sales cells (frozen baseline was starved at
0.54 ROAS) plus favorable sampling; the small held-out (+0.111) reflects a
real-but-modest outcome edge diluted by all the re-leveling.

WASTE AUDIT: 7/8 bookkeeping (1,2,3,4,6,7,8) — ~88% waste, the worst — 1/8
creative (5). Rule-fixation is dominant; the entire back half is
dose-equalization arithmetic.

DISCOVERY QUALITY: In-run winner is outcome (14 purch, 0.82), esp
sales_niche_outcome (8); agent's outcome-broad+niche call is in-run
supported. Ground truth nuance: the population is weighted toward
count-preferring personas (bargain 0.31/count, committee 0.18/count,
herd/authority count) — so demoting count looks backwards by population mass
— BUT those are low-buyiness (0.39–0.70); the single highest-buyiness
meaningful persona is pragmatist (0.89, outcome). So chasing outcome is
correct for buyiness, wrong for headcount, which is exactly why the edge is
real but thin.

QUOTES: "each of the six open creative-by-audience cells is $33.32 short of
its fixed $66.65 readable floor, so sales budget should complete those
doses…"; "restore each as a sales-eligible exact cell… a lower-performing
exact sales cross remains an unresolved alternative, rather than an
eliminable loser, until its own fixed demotion dose is reached"; "The current
audience-specific sales preferences may be partly exposure-driven… all paired
cells should reach the same fixed $199.95 evidence dose before preferences
control allocation."

Bigger/faster: Stop re-leveling doses after iter 5 and exploit the
outcome-niche winner; the last three iterations added exposure-bookkeeping,
not revenue.
