---
name: takyon-x
description: Create, review, and continue honest X posts, replies, and thread handling for one Takyon business — now powered by an embedded viral-copy engine (hooks, edge dial, anti-AI/de-Claude humanization, adversarial refinement). Use for X posts, replies, threads, and durable X voice for one business.
version: 2.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, x, twitter, distribution, replies, copywriting, hooks, threads, viral, voice]
    related_skills: [takyon-distribution, takyon-business-metrics, takyon-conversation-followup, takyon-market-research]
    requires_toolsets: [takyon]
    requires_tools:
      [
        business_read_business,
        business_calculate_pulse,
        business_publish_outreach,
        business_record_conversation_message,
        business_update_conversation_message_status,
      ]
    routing:
      owns: X posts, replies, thread handling, and durable X voice
      when_to_use:
        - the business needs X-native drafting, posting, or reply handling
        - a wake or campaign turn is clearly X-shaped
      do_not_use_for:
        - broad cross-channel campaign coordination better handled by `takyon-distribution`
  takyon:
    scope: business
    allowed_roots: [distribution, metrics, research]
    output_root: distribution
    publication:
      - distribution/voice/x.md
      - distribution/campaign
      - distribution/local-published
      - metrics/conversations
required_environment_variables: []
required_credential_files: []
---

# Takyon X

## Overview

Use this skill for X-specific execution for one Takyon business: posts, replies, thread handling, and durable X voice. It runs on the embedded **viral-copy engine** in `references/` — hooks, the edge dial, anti-AI/de-Claude humanization, and adversarial refinement — so the copy actually lands instead of reading like generic positioning. The Takyon machinery (real business state, real publish path, truthful conversation state) is unchanged; only the way copy is *crafted* is upgraded.

## When to Use

- When the operator asks for an X post, X reply, thread response, or X-native participation.
- On `/wake` when unresolved inbound is clearly X-shaped and can be handled without broad inbox compression.
- When a campaign needs X-ready copy or thread follow-through, not just broad planning.
- When a post, reply, or thread needs to actually stop the scroll, earn replies and bookmarks, or stop sounding like AI.
- Do not use this skill for cross-channel campaign strategy or ICP research.

## Voice Context (read first)

This skill writes for **one business** with a durable voice in `distribution/voice/x.md`. The copy engine in `references/` was written brand-agnostic (for a page that posts for many brands), so read it through this lens:

- Wherever the engine says "brand-neutral" or "no founder voice / many brands," **match this business's voice** in `distribution/voice/x.md` instead.
- Identity-led hooks (a real story, a real result, a named product) are **available** here because the business owns its story — but still default to idea-led when the idea is strong enough to carry alone.
- Everything else in the engine — hooks, structure, the edge dial, AI-tell/de-Claude removal, refinement — applies exactly as written.

If `distribution/voice/x.md` is missing, stale, or weak and X is a repeated lane, refresh it first (using the engine's voice and anti-AI guidance) before drafting more output.

## Quick Reference

**Operational (Takyon):**

- Primary root: `distribution/`
- Publication paths: `distribution/voice/x.md`, `distribution/campaign/`, `distribution/local-published/`, `metrics/conversations/`
- Best call points: X posting, X replies, X thread handling, X voice maintenance
- Tools: `business_read_business`, `business_calculate_pulse`, `business_read_file`, `business_list_files`, `business_list_conversation_messages`, `business_read_conversation_thread`, `business_write_file`, `business_patch_file`, `business_publish_outreach`, `business_publish_test_outreach`, `business_record_conversation_message`, `business_update_conversation_message_status`, `business_enqueue_job`

**Copy engine (how to write):**

| Need | Reference |
|------|-----------|
| Hooks: patterns, formulas, scoring | `references/hook-library.md` |
| Post / thread / QT / reply construction + algorithm levers | `references/x-playbook.md` |
| AI-tell + **de-Claude** removal, humanization, voice | `references/ai-tells.md` |
| Adversarial refinement passes + stopping criteria | `references/refinement-protocol.md` |
| Engagement voice + the 0–10 edge dial (default 3–4) | `references/voice-and-edge.md` |

## The copy loop (non-negotiables)

Every post or reply runs the loop, then ships **one send-ready** piece by default:

1. The hook earns the second line (or the reply's one move lands). See `hook-library.md`.
2. Idea-led first; identity-led only when the business genuinely owns the story.
3. **No links in the tweet body** — any link goes in a reply.
4. **Zero AI tells, including structural Claudeisms** — no rule-of-three, no "not X, it's Y" mirror, no fortune-cookie closer, minimal "just/quietly." Run the de-Claude pass (`ai-tells.md`).
5. One hard thing per post: a real number, tradeoff, product contrast, or direct observation. Every claim specific.
6. Edge dial set for the business (`voice-and-edge.md`, default 3–4): spicy-but-true, punch up at practices not people. Never ragebait.
7. End before the point goes soft — a command or a line worth screenshotting, not "what do you think?"

Generate options internally (many hooks, score, pick the best), but **output one send-ready post or reply** unless the operator asks for variants.

## Prerequisites

- The Takyon toolset must be available.
- Start with `business_read_business` and usually `business_calculate_pulse` so the move begins from real conversation pressure and real campaign state.
- If thread volume is too noisy to inspect directly, load `takyon-conversation-followup` first and use its compact triage before deciding what to post or answer.
- If the business uses X repeatedly and `distribution/voice/x.md` is missing, stale, or weak, refresh it before drafting more output.

## References

The embedded copy engine (this replaces takyon-x's former style guide, `x-style-guide.md`, which has been removed):

- `references/hook-library.md`
- `references/x-playbook.md`
- `references/ai-tells.md`
- `references/refinement-protocol.md`
- `references/voice-and-edge.md`

## Templates

- `templates/x-post.md`
- `templates/x-reply.md`

## Scripts

None. Copy is crafted from the references and published through the Takyon business tools.

## How to Run

- Call `business_read_business` first to inspect current campaign state, unresolved replies, and existing X artifacts.
- Call `business_calculate_pulse` when reply pressure or recent activity should affect whether you post, reply, or hold.
- Use `business_list_conversation_messages` and `business_read_conversation_thread` to inspect the actual thread state before replying.
- Use `business_write_file` or `business_patch_file` to create or refresh `distribution/voice/x.md` when repeated X work needs durable channel guidance.
- Treat `distribution/campaign/` as the canonical campaign workspace. If only legacy X drafts live under `distribution/phase-1-outreach/`, move or merge that visible state forward before drafting new work.
- Keep in-progress X drafts visible under `distribution/campaign/` when the turn is not publishing immediately.
- Draft with the copy engine (hooks → build → refine → de-Claude/humanize → set edge dial), then ship one send-ready piece.
- Prefer `business_publish_outreach` as the main publish path. It will use test-mode behavior when the business is in test mode. Use `business_publish_test_outreach` directly only when you intentionally want a local suppressed artifact without taking the normal publish path.
- Use `business_record_conversation_message` and `business_update_conversation_message_status` so the thread state stays truthful after a draft, suppressed publication, or real publish outcome.

## Procedure

1. Call `business_read_business` and, when useful, `business_calculate_pulse`. If unresolved X replies exist, handle them before drafting new top-level posts unless the operator explicitly prioritizes a post.
2. Inspect `distribution/voice/x.md`, the current campaign workspace, and the relevant conversation mirrors. If X voice guidance is missing, stale, or too generic, refresh `distribution/voice/x.md` first (using the copy engine).
3. **If this is a reply:** read the target thread first. Draft exactly one send-ready reply by default — one move, usually 1–2 sentences — using `references/x-playbook.md` (Replies) and the edge dial in `references/voice-and-edge.md`. Answer the real thing (the false tradeoff, the smug premise, the outdated assumption), not always the literal claim. No variants unless the operator asks.
4. **If this is a top-level post or thread:** draft one send-ready piece with one concrete payload. Generate hooks (`references/hook-library.md`), build with `references/x-playbook.md`, then run the refinement and de-Claude passes (`references/refinement-protocol.md`, `references/ai-tells.md`). Prefer a real number, product contrast, tradeoff, or direct observation over generic positioning.
5. If the turn is still in draft mode, keep the draft visible under `distribution/campaign/` rather than pretending it was published.
6. If the business is in test mode or the publish path should remain local, call `business_publish_outreach` and expect a suppressed local artifact under `distribution/local-published/` plus a conversation mirror or receipt. If you explicitly need the local-only path, call `business_publish_test_outreach` directly.
7. If the business is in live mode and the publish path is provider-backed, call `business_publish_outreach` and inspect the resulting receipt, job, or blocker. If the channel requires deferred action rather than immediate publication, record that next step with `business_enqueue_job`.
8. After the draft or publish step, keep `metrics/conversations/` truthful with the conversation tools. Do not mark a thread resolved just because a draft exists.

## Output Format

- Durable X voice guidance lives in `distribution/voice/x.md`.
- Send-ready drafts stay compact and visible in `distribution/campaign/` when they are not yet published (one send-ready piece by default; variants only on request).
- Local suppressed publication belongs under `distribution/local-published/`.
- Conversation truth belongs in `metrics/conversations/`, not in a narrative summary.

## Publication

- Publish durable X voice guidance to `distribution/voice/x.md`.
- Publish X drafts and surrounding channel artifacts to `distribution/campaign/`.
- Publish local suppressed X outputs to `distribution/local-published/`.
- Publish thread and reply state to `metrics/conversations/`.
- Live external publication belongs to canonical business tools and their receipts, not hand-written success claims.

## Common Pitfalls

- Letting X replies turn into polished rebuttal essays — one move, end a beat early.
- Drafting variants when one sharp send-ready piece is enough.
- Treating a draft as if it was already posted.
- Losing thread truth by clearing message state too early.
- Weak hook or soft ending — if the first line does not stop the scroll, nothing else matters.
- Links in the tweet body (move them to a reply).
- AI tells or Claudeisms surviving into the draft — run the de-Claude pass.
- Edge with no point (ragebait) — if stripping the attitude leaves nothing, rewrite around a true insight.

## Verification Checklist

- [ ] `distribution/voice/x.md` exists and matches the current business voice when X is a repeated lane.
- [ ] Any claimed post or reply has a corresponding tool result, local artifact, queued job, or receipt.
- [ ] `distribution/local-published/` contains the expected suppressed artifact in test-mode publication paths.
- [ ] `metrics/conversations/` reflects unresolved X thread state truthfully.
- [ ] Hook stops the scroll; one concrete payload; ends sharp (no soft summary).
- [ ] Zero AI tells; de-Claude'd (no rule-of-three, ≤1 "not X/it's Y" flip, no fortune-cookie closer); passes the read-aloud bar test.
- [ ] If edgy: spicy-but-true, punches up not down, and the insight survives without the attitude.
- [ ] One send-ready piece by default; no links in the tweet body.

## Rules

1. Default to one send-ready post or reply, not variants, unless the operator explicitly asks for alternatives.
2. Do not claim a live X send or reply without a tool-backed receipt or result.
3. Read the thread before answering it.
4. Keep durable voice guidance in `distribution/voice/x.md`, not inside one-off drafts.
5. **No fabrication.** Do not invent numbers, results, personas, or testimonials. No astroturfing, manufactured consensus, or misinformation with viral mechanics.
6. **Edge with care.** Spicy-but-true only; punch up at practices, never down at people or identity; keep the "unhinged" dial low by default (3–4 of 10). See `references/voice-and-edge.md`.
7. **Craft discipline.** Run the refinement loop, the humanization pass, and the de-Claude pass before shipping. Specific beats clever; concrete before clever.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Reply sounds generic or over-explained | Refresh `distribution/voice/x.md`, then redraft with one move and one concrete payload (`x-playbook.md`, `voice-and-edge.md`). |
| Draft "sounds like AI" or too neat | Run the de-Claude pass in `references/ai-tells.md`; break the symmetry, kill the rule-of-three and the fortune-cookie closer. |
| Too many X replies to inspect cheaply | Load `takyon-conversation-followup` first, then come back with the compact triage. |
| Publish provider is blocked | Use local publication if allowed, otherwise record the blocker or queue the next step with `business_enqueue_job`. |
| Hook feels flat | Generate 10+ more from different families in `hook-library.md`; score for Curiosity + Specificity + Emotion; ship 7+. |
