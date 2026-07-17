# Dialogue + Action Framework (SCRIPT LAYER ONLY)

Adapted from Rob Palmer's ad-copy framework
(`robpalmer.com/blog/claude-code-ad-copy-skill`).

> **Scope boundary — read this first.**
> This framework governs **only two things**: the **words** the person says and the
> **action / visual beat paired with each line**. That is the entire job of this layer.
>
> It does **NOT** decide how the person looks, skin/lighting realism, the reference
> image, the video model, camera motion, `cfg_scale`, clip length, stitching, or post.
> All of that is the **production layer** — see
> [realism-framework.md](realism-framework.md) and
> [editing-and-stitching.md](editing-and-stitching.md). Do not let copy decisions leak
> into production settings, and do not let production concerns rewrite the copy.
>
> **Output of this layer = a `script.json`** (`templates/script.json`): an ordered list
> of `beats`, each `{ "dialogue": "...", "action": "..." }`. Nothing else.

---

## Step 1 — Classify the brief

Before writing a word, pin down four things. They steer tone and structure (not look):

1. **Funnel position** — `cold` (never heard of us) / `warm` (knows the category) /
   `hot` (knows us, needs a nudge).
2. **Awareness level** — `unaware` → `problem-aware` → `solution-aware` →
   `product-aware` → `most-aware`. Colder/less-aware ⇒ lead with the problem and earn
   the claim; warmer/more-aware ⇒ you can open closer to the offer.
3. **Strategy** — the single core promise/angle this one ad sells. One ad = one promise.
4. **Format** — `short-ugc` (15–60s talking head), `story` (narrative arc), or
   `adhd` (fast stacked pattern-interrupts). Default to `short-ugc`.

Record these in the script's header fields so the production layer and the reviewer
have context — but they do not change any production setting.

## Step 2 — WHY / WHAT / HOW

Draft the spine in three plain sentences before shaping beats:

- **WHY** — why the viewer should care *right now* (the felt problem / desire).
- **WHAT** — what the product actually is, in one honest sentence.
- **HOW** — how it delivers the promise (the mechanism, concretely).

If you can't say WHAT in one sentence, the ad isn't ready.

## Step 3 — Pick a hook (first 0–2 seconds)

The hook is the whole ballgame, and it has to **hit hard and fast**. You have ~1.5
seconds before the thumb keeps scrolling, so the hook must be **bold, almost out of
pocket** — provocative, blunt, a little cheeky, slightly more than you'd normally say
out loud. Make it impossible to ignore.

**Hook doctrine:**
- **First 3–5 words carry it.** Front-load the most arresting words; no "hey guys", no
  throat-clearing, no warm-up. Open *in the middle of the energy*, not building toward it.
- **Be bold/blunt.** "Stop." "You're being lied to." "Delete it." A confident, slightly
  provocative claim out-pulls a polite one. Out of pocket > safe.
- **Make a promise or a provocation** the rest of the ad must pay off — open a loop, call
  someone out, or state a result so good it sounds fake (then prove it).
- **Say it fast and punchy** — short, hard-hitting words, delivered with energy (the
  production layer pushes a brisk, upbeat read; write copy that *wants* to be said fast).

Choose **one** of the 8 hook types and write the first line to match. Vary the hook
across script variations.

| # | Hook type | What it does | Out-of-pocket opening shape |
|---|-----------|--------------|----------------|
| 1 | **Curiosity** | Opens a loop the viewer must close | "Okay I need you to stop scrolling for one second." |
| 2 | **Contrarian** | Challenges a held belief, bluntly | "Stop running your business on spreadsheets. You're losing money." |
| 3 | **Social Proof** | Borrows the crowd's trust | "Everyone's been gatekeeping this and honestly it's not fair." |
| 4 | **Story** | Drops mid-scene, mid-chaos | "It's Monday, 9am, six tabs open, and I'm already losing it —" |
| 5 | **Demographic** | Calls out the exact viewer, hard | "If you own a business and still do this by hand — this is for you." |
| 6 | **Result** | Leads with a result that sounds fake | "I did a full week of admin in the time it takes to make coffee." |
| 7 | **Pattern Interrupt** | Breaks the scroll rhythm | A blunt, odd, or unexpected line that does not fit the feed. |
| 8 | **Fascinating Fact** | Surprising true nugget, stated big | "You're spending a full DAY every week on this. A whole day." |

Keep it on-brand and never offensive — "out of pocket" means *bold and attention-
grabbing*, not crude or off-putting for the business.

## Step 4 — Lay out the beats

Each beat = **one spoken line + the action performed while saying it**. Keep lines
short and spoken-aloud natural (contractions, no jargon, no list-reading). The
`[action]` is a small real human movement or glance, **not** a camera instruction.

**Pace & density (write for a fast, energetic read).** The ad is delivered briskly —
roughly **3 words/second** (the production layer plans clips at this pace; see
`--wps`). So:
- **No filler.** Cut "so", "like", "basically", "honestly", "I just wanted to", and
  every warm-up phrase. Each word should carry weight.
- **Front-load value.** Make the point, *then* support it. Never bury the payoff at the
  end of a slow ramp.
- **Pack content.** Because the read is fast, you can (and should) fit *more substance*
  per second — dense, punchy, high-information lines rather than slow, padded ones. Aim
  for ~12–15 spoken words per ~5s of clip.
- **Write lines that *want* to be said fast** — tight rhythm, hard consonants, short
  clauses. If a line only works said slowly, rewrite it.

**Sound human (a light touch of imperfection).** Real people don't talk in clean,
polished sentences — they false-start, self-correct, toss in an aside, trail off. A
script that's *too* tight reads like an announcer, which is its own AI tell. So season
the copy with **deliberate, sparing** imperfection:
- **One disfluency per ~10s, max.** A single false start ("Okay wait—"), a mid-sentence
  aside ("—not exaggerating—"), one genuine filler, or a trailing "…" on a soft line.
  That's the dose. Two in a beat reads as a stumble, not a real person.
- **It must be load-bearing, not padding.** This is the *opposite* of the warm-up filler
  the density rule cuts. A warm-up ("so, like, I just wanted to say…") delays the value
  and gets cut. A *deliberate* disfluency ("Okay wait— you're losing money.") lands the
  value with a human, caught-mid-thought texture. Keep the word count just as tight.
- **Keep the hook and CTA clean.** The first 3–5 words and the final ask stay crisp —
  imperfection lives in the *middle*, never on the lines that have to cut through.
- **Don't overindex.** If you can hear the disfluency "performing," it's too much. The
  read should still be fast and confident; the imperfection is seasoning, not the dish.

### `short-ugc` (default, 15–60s)
| Beat | Window | Job | Example action |
|------|--------|-----|----------------|
| HOOK | 0–3s | Stop the scroll | grin, lean toward the phone |
| PROBLEM | 3–10s | Name the pain they feel | tired half-eye-roll, glance away |
| MECHANISM | 10–25s | What it does / show it on a real device | tip head toward the open laptop |
| RESULT | 25–40s | The payoff, concretely | relaxed, relieved exhale + smile |
| CTA | 40–60s | One clear ask | hold the phone up, easy shrug |

Short ads (~15–20s) compress these — often HOOK, PROBLEM, MECHANISM, RESULT+CTA as 4
beats (see `assets/example-brief.json`).

### `story`
Beginning (status quo + inciting pain) → Middle (tried things, found the product) →
End (transformation + CTA). Actions track the emotional arc.

### `adhd`
6–12 very short beats, each a mini pattern-interrupt (claim, proof, objection, reframe,
proof, CTA). Actions change every beat to keep visual novelty high.

## Step 5 — Produce 3–5 variations

Write **3–5** full script variations that keep the same WHAT but vary the **hook** and
angle (e.g. Curiosity vs. Result vs. Story openings). This is the unit of A/B testing.
Each variation is its own `script.json`.

## Step 6 — Validate (8-point checklist)

- [ ] **Hook lands in ≤2s** and is **bold / out-of-pocket** — the first 3–5 words stop
      the scroll, and it matches a named hook type.
- [ ] **One promise** — the ad sells a single core idea.
- [ ] **Spoken-natural** — reads aloud like a real person, not marketing copy.
- [ ] **Dense & fast** — no filler words; ~12–15 spoken words per ~5s; the copy *wants*
      to be said briskly (not a slow, padded read).
- [ ] **Sounds human** — at most one *deliberate* disfluency per ~10s (false start,
      mid-sentence aside, single filler, trailing off); hook and CTA stay clean; no
      overindexing.
- [ ] **Specific** — concrete details/numbers beat vague adjectives.
- [ ] **WHAT is clear** — a stranger could say what the product is.
- [ ] **One clear CTA** — exactly one ask, unambiguous.
- [ ] **Every line earns its place** — cut anything that doesn't pull the viewer forward.
- [ ] **Each beat has a paired, human action** (and product shows in-scene where relevant —
      a real device, never a UI screenshot, per the production layer).

---

### Handoff to production
The finished `script.json` (the `beats`) is the **only** thing this layer hands to
`build_ad.py`. The look, lighting, camera, clip-splitting, and post are decided entirely
by the production layer. Keep the two separate.
