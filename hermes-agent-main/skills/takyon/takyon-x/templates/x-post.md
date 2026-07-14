# X Post Template

## Input

- topic:
- concrete payload (the one hard thing: real number, tradeoff, product contrast, or direct observation):
- desired posture: observational | sharp | explanatory
- edge dial (0–10, default 3–4):

## Output Contract

Return exactly one send-ready post (no variants unless the operator explicitly asks). For a thread, return one send-ready thread (each tweet standalone, no "1/" numbering in the posted text).

## Craft (use the engine)

- Hook — `references/hook-library.md` (lead with tension, not intro)
- Build + algorithm levers — `references/x-playbook.md`
- Edge dial + voice — `references/voice-and-edge.md`
- Strip AI tells + de-Claude pass — `references/ai-tells.md`
- Attack before shipping — `references/refinement-protocol.md`

## Constraints

- One post only; no variants by default
- One hard thing (real number, tradeoff, product contrast, or direct observation)
- No links in the tweet body (any link goes in a reply)
- No hashtags unless explicitly requested; no emojis; no borrowed internet slang
- Match the business voice in `distribution/voice/x.md`

## Reject If

- It sounds like generic positioning copy, or could fit any company with two nouns swapped
- It ends with a soft summary instead of a real point
- It carries AI tells or Claudeisms (rule-of-three, "not X, it's Y" mirror, fortune-cookie closer)
