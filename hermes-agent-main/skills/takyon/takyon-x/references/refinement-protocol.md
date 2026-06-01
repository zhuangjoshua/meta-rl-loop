# Refinement Protocol

The difference between forgettable copy and viral copy is the number of adversarial passes it survived. Good posts aren't written — they're attacked until only the strong parts remain. Never ship pass one.

## The deliberative refinement loop

1. Generate the initial draft (hook + body + closer).
2. Attack it from the perspectives below.
3. Find the AI tells and weak points (`ai-tells.md`).
4. Refine with a sharper, more specific, more human version.
5. Repeat until it stops breaking.

---

## The five passes

Run in this order. The order matters: there's no point polishing copy that has no point.

### Pass 1 — The Skeptic
**Voice:** "Why should I care? What's actually new here?"
- Is this just restating the obvious? What's the genuine insight vs padding?
- Would someone who knows this space learn anything? Is there a "so what?"
- **Actions:** kill any line that fails "so what?"; cut what's true but not new; back claims with specifics.

### Pass 2 — The Expert
**Voice:** "Is this accurate? What would someone who knows this nitpick?"
- Does anything oversimplify to the point of being wrong? Stated more confidently than warranted?
- **Actions:** fix inaccuracies; add the one piece of nuance that prevents a "well, actually" pile-on in the replies; remove claims you can't defend; keep the conviction.

### Pass 3 — The Scroller
**Voice:** "Would I stop for this? Where's the hook?"
- Does the first line create curiosity? Is the value obvious in the first 5 seconds? Is there a payoff?
- **Actions:** rewrite the first line until it hooks; front-load the value; delete the warmup; make the payoff visible early. (See `hook-library.md`.)

### Pass 4 — The Competitor
**Voice:** "How is this different from the 10 similar posts in the feed?"
- What makes this one stand out? Is there a novel angle or framing?
- **Actions:** sharpen the unique angle; cut anything that sounds like every other take; make the differentiation explicit in the hook.

### Pass 5 — The Editor
**Voice:** "What can I cut without losing meaning?"
- Is every line earning its place? Any filler? Can it be tighter?
- **Actions:** cut every filler phrase; merge redundant points; shorten every sentence that allows it; aim to remove ~20%.

**Why this sequence:** Skeptic (value) → Expert (accuracy) → Scroller (hook) → Competitor (differentiation) → Editor (cut). Polishing precedes nothing; substance precedes polish.

---

## Execution depths

Match effort to stakes.

- **Quick (single tweet, reply, low stakes):** mental run-through of Passes 1, 3, 5. One revision.
- **Standard (most posts, important single tweets):** all five passes with notes; two revision cycles; humanization checklist (`ai-tells.md`).
- **Deep (flagship thread, teardown, big swing):** full written critique per pass; multiple revisions; the bar test with a real human if possible; a cooling-off reread before shipping.

---

## Common failure modes

| Symptom | Fix |
|---------|-----|
| Sounds good, says nothing | You skipped the Skeptic. Ask "what's the actual insight?" first. |
| Hedged into mush | Expert paralysis. Accuracy matters, but so does conviction — re-add the spine. |
| Great hook, dead body | Build the body before obsessing over the hook. A promise the body can't pay off loses the reader on tweet 2. |
| Novel angle, boring topic | Run Skeptic before Competitor — differentiation without value is still forgettable. |
| Sterile, voice gone | Over-edited. Preserve one distinctive line per revision; humanize delivery, not spine. |

---

## Stopping criteria

Ship when **all** are true:

- [ ] Survives the Skeptic's "so what?"
- [ ] The Expert can't find an inaccuracy worth a reply-dunk.
- [ ] The Scroller would stop for the hook.
- [ ] The Competitor sees clear differentiation from similar posts.
- [ ] The Editor can't cut more without losing meaning.
- [ ] Passes the `ai-tells.md` humanization checklist and the read-aloud bar test.
- [ ] Hook scores 7+ (`hook-library.md`).
- [ ] Idea-led: works with zero knowledge of who posted it; no reliance on profile or founder voice.

All boxes checked → ship. Perfectionism past this point has diminishing returns.

---

## Lean variant testing

When the user wants options or plans to test:

- Generate **2–3 distinct hook variants** for the same body (different families from `hook-library.md`), label each with its pattern, and recommend one.
- If comparing live, change **only the hook/first line**; keep the body identical so the variable is clean.
- Compare **engagement rate**, not raw counts. Replies and bookmarks are the signals that matter most (`x-playbook.md`), not likes.

**Variant log shape:**

```
Topic: [what the post is about]
Variant A — [pattern]: "[hook]"
Variant B — [pattern]: "[hook]"

Result (after [N] days):
- A: [reply / bookmark / repost rate]
- B: [reply / bookmark / repost rate]
Winner: [A/B] — [the pattern that won this topic, and the one-line lesson]
```

Over several tests, patterns emerge for each brand and topic type. Bank the winners as starting points — never as guarantees. A pattern that won one topic can die on the next; the idea always has to earn the structure.
