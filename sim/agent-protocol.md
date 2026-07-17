# Sim-world gradient agent protocol

You are the SEMANTIC GRADIENT for one simulated ad market ("world W" — given in
your task). You run the full RL loop for 8 iterations. Work from
/Users/joshuazhuang/Documents/fourmanifold/meta-rl-loop.

## Sealed-world integrity (absolute)
NEVER read: sim/world-*/subpops-hidden.json, sim/worldgen.py, sim/score.py,
sim/driver.py, sim/market.py, any other world's directory or files. These
contain or imply the hidden ground truth; reading any of them voids the run.
Your ONLY information about the world is the receipts your own batches produce.

## Files you own (create in sim/world-W/)
- llm-policy.md      — current policy full text (start: copy sim/seed-policy.md)
- llm-lineage.md     — per-iteration log (thesis, 6 doses one line each, draw, adoption)
- llm-evidence.md    — accumulated receipts (append each batch's market output)
- llm-results.jsonl  — one line per iteration: {"iteration":i,"spend":s,"revenue":r,"roas":x,"dose":d,"thesis":"one line"}
- specs: llm-it1.json … llm-it8.json

## Batch spec format (what the market accepts)
{"iteration":1,"policy":"v0","ads":[{"id":"any-name","proof":"benefit|outcome|count|story",
 "named_story":true|false,"demo":true|false,"prompt":"full ad description"} x3],
 "campaigns":[{"id":"name","objective":"clicks|pageviews|leads|sales",
 "audience":"broad|interest_biztools|interest_niche","budget":40,"mode":"fixed"}
 ... and optionally ONE {"id":"x","objective":"sales","audiences":["broad","interest_biztools"],
 "budget":40,"mode":"auto"}]}
Budgets must total ~$200. proof="story" should set named_story=true to count as
a real named story. CPMs differ by audience (platform fact: broad $8,
interest_biztools $14, interest_niche $12).

## Per-iteration protocol (i = 1..8) — follow EXACTLY, in this order
1. Design the batch spec from the CURRENT llm-policy.md. Write llm-itI.json.
2. Run the market:  python3 sim/market.py W $((W*100+I)) sim/world-W/llm-itI.json
   Append its full stdout to llm-evidence.md.
3. Read the receipts. Following ad-creative-stack/semantic-gradient.md TO THE
   LETTER (one falsifiable thesis; thesis classes and dose caps; coverage rule —
   sweep unpriced options early; composition rule — compare only matched cells;
   evidence sized honestly), write into llm-lineage.md: the thesis (2-3
   sentences) and SIX one-line doses, smallest to boldest.
4. THE DRAW — run it BEFORE writing any adoption text:
   python3 -c "import math,random;t0,dc,fl,wd=1.0,.92,.05,.18;t=(max(fl,t0*dc**I)-fl)/(t0-fl);w=[math.exp(-((j/6)-t)**2/(2*wd**2)) for j in range(7)];z=sum(w);r=random.Random(W*1000+I);p=r.random()*z;a=0
for j,x in enumerate(w):
 a+=x
 if p<=a: print('keep' if j==0 else f'dose {j}'); break"
   (replace W and I with the real numbers; keep = incumbent unchanged)
5. Adopt: apply the drawn dose's edits to llm-policy.md (standalone rules, no
   references to iteration numbers as justification inside the policy text);
   log the draw + adoption in llm-lineage.md.
6. Append the results line to llm-results.jsonl (spend/revenue/roas come from
   the market's @@SUMMARY line).

## Discipline reminders (from the gradient doc — they bind you)
- One thesis per iteration; a wash/tie is a valid finding; "no change" is a
  legitimate draw outcome.
- Sweep before refining: unpriced angle families / demo / audiences get funded
  tests in the first 2-3 iterations.
- Goal-layer (purchase) hypotheses are not displaced by signup-layer evidence;
  do not demote an axis until its funded spend would expect >=3 purchases at
  your portfolio's observed rate.
- Judge cells per-dollar, never raw counts; never compare conversion rates
  across differently-targeted cells.
- Auto-mode cells unreadable 2 iterations running -> a design thesis to cut
  them is licensed.

## When finished
Reply with ONLY the 8 result lines (the jsonl content) plus one sentence naming
your final policy's three most load-bearing rules. No other prose.
