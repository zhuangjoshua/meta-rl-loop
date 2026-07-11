---
name: claude-design
description: Distilled implementation method for high-quality product/site frontend work. Layer beneath taste-frontend and above at most one shared claude-design-* visual system.
license: Apache-2.0
---

# Claude Design

Use this skill for outward-facing `product/site` work beneath `taste-frontend`. Taste interprets the
brief and controls art direction; this skill provides the implementation method. Add at most one
shared visual system beneath both:

- `claude-design-openai`
- `claude-design-stripe`
- `claude-design-superhuman`
- `claude-design-doodle`
- `claude-design-brutalist`

Do not mix multiple style skills in the same worker run. Do not override Taste's brief-specific art
direction with a preset's house aesthetic.

## When To Use

- landing pages
- product marketing pages
- dashboards
- app shells
- UI refreshes
- customer-facing HTML/CSS/JS surfaces

## Shared Style Selection

Choose one coherent visual direction from the available style packs before building:

- `claude-design-openai`: calm serious for AI tools, prosumer software, research/productivity surfaces
- `claude-design-stripe`: premium commercial, fintech, infra, or polished B2B marketing
- `claude-design-superhuman`: premium productivity, speed, focus, executive-feeling software
- `claude-design-doodle`: whimsical playful consumer, pets, kids, casual social, deliberately lighthearted products
- `claude-design-brutalist`: raw industrial/terminal precision for dev tools, security/infra, telemetry dashboards, experimental technical brands

## Workflow

1. Follow Taste's reading of the brief, audience, core job, and emotional tone before choosing the look.
2. Choose one style pack and stay inside its typography, spacing, color, and component posture.
3. Build from a coherent page rhythm, not isolated pretty sections.
4. Keep the interface honest: real controls, real labels, real states, no poster-only hero fakery.
5. Use one visual thesis and carry it through typography, spacing, chrome, and motion.

## Layout and Width

- On desktop, customer-facing landing pages should occupy most of the canvas, not sit as a tiny island in empty space.
- Prefer a broad container in the rough `1320px` to `1440px` range with generous side gutters, or a deliberate split/full-bleed composition.
- On very large desktop viewports, treat that as a floor rather than a ceiling; if the page still looks boxed in, widen toward about `1600px` to `1720px` or roughly `90vw`.
- A reliable large-screen pattern is roughly `min(92vw, 1680px)` with restrained side padding rather than a centered `1400px` frame with big gutters.
- The masthead or top navigation lane can be a touch wider than the main content lane when that helps the logo, links, and primary CTA breathe.
- A dependable pattern is a header shell around `min(94vw, 1760px)` while the main hero/body shell sits around `min(92vw, 1680px)`. Keep the difference small and intentional.
- Do not leave 40% to 60% of the hero visually empty unless that space is doing clear work with a real image, product visual, proof block, or strong atmospheric gesture.
- If the headline sits in a narrow column, pair it with an equally intentional second column or widen the composition; avoid accidental center-column layouts that feel unfinished.
- Make the first screen feel composed at laptop width, not just technically responsive.
- If you choose a side-by-side hero, the proof rail should feel like a real half of the composition, not a small decorative card parked off to the side.
- As a starting point, a split hero should usually bias toward about `55/45` rather than a timid perfectly-equal split when that helps the proof rail feel substantial.
- Let desktop display headlines get big enough to carry the page; if the whole first screen feels miniature, scale the composition up before adding more copy.
- Do not let a widened container get neutralized by capping both hero columns around the same mid-`500px` width. One side should push wider so the composition actually spans the page.
- On wide monitors, outer gutters should not dominate the composition. If they do, widen the layout or reduce side padding before touching the copy.
- For very wide screens, slightly asymmetrical hero splits such as `58/42` or `60/40` often feel better than a perfect `50/50` when the proof rail is otherwise reading too polite.

## Layout Discipline

Failing any of these is shipping broken work:

- The hero must fit the initial viewport: headline max 2 lines on desktop, subtext max 20 words, CTAs visible without scrolling. If the copy overflows, cut copy or reduce scale; never let the hero force a scroll to find the CTA.
- Plan headline size and hero asset together. A 4-line hero headline is a font-size error, not a copy-length error. Reserve the biggest display scales for 3-5 word headlines.
- Hero top padding caps around `6rem` desktop; content floating halfway down the viewport reads as a layout bug, not intentional space.
- Hero stack max 4 text elements: at most one eyebrow or brand strip, headline, subtext, CTAs (1 primary + max 1 secondary). Trust micro-strips, pricing teasers, feature bullets, and avatar rows move to their own sections below.
- Logo walls ("Trusted by") live under the hero, never inside it.
- Navigation renders on one line at desktop, max ~80px tall. A two-line desktop nav is broken design.
- Eyebrow restraint: max 1 eyebrow (small uppercase tracking label) per 3 sections, hero included. If a section has one, the next two do not. Usually drop it; the headline alone is enough.
- Once a layout family is used for a section (3-column cards, full-width quote, split text+image), it appears at most once more on the page. Max 2 consecutive image/text zigzag splits; the 3rd consecutive split fails review.
- A bento/feature grid has exactly as many cells as there is content — never a blank filler tile — and needs background variety: at least 2-3 cells with a real image, tint, or pattern, not uniform white-on-white cards.
- No split-header pattern (big left headline + small floating right paragraph) by default; stack headline over body at ~65ch instead.
- Content density: per section, short headline (≤8 words) + short support (≤25 words) + one visual or CTA. Lists over 5 items need a different component (grouped chunks, card grid, tabs, carousel), not a longer list with a hairline under every row.
- Quotes: max 3 lines, attribution is name + role, real typographic quotes or none.
- One theme per page: sections never flip between light and dark mid-scroll. Background tints within the same family are fine; a cream section inside a dark page is broken.
- Declare the mobile collapse for every multi-column layout explicitly; never assume the framework handles it.

## Anti-Slop Tells

Banned patterns unless the brief explicitly asks for one — these are the signatures of AI-generated design:

- Visual: no neon/outer glows, no pure `#000000`, no oversaturated accents, no gradient text on large headers, no glassmorphism-by-default, no custom cursors.
- Layout: no three identical feature cards in a row as the default feature section; use zigzag, asymmetric grid, or a different family.
- Content: no "John Doe"/generic names, no egg avatars, no fake-perfect numbers (`99.99%`, `50%`) — organic data reads real (`47.2%`); no startup-slop brand names ("Acme", "Nexus", "SmartFlow"); no filler verbs ("Elevate", "Seamless", "Unleash", "Next-Gen", "Revolutionize").
- Hero and labels: no version labels (`BETA`, `v2.0`, `EARLY ACCESS`) as default eyebrows; no section-number eyebrows (`01 / Capabilities`); no scroll cues ("Scroll to explore"); no decoration text strips (`DESIGN · BUILD · SHIP`) at the hero bottom; no locale/time/weather strips.
- Fake previews: never build fake product UI out of styled `<div>`s in the hero — use a real screenshot, real component, or nothing; no fake version footers inside mock screenshots.
- Copy: no "Quietly trusted by" social-proof headers; no mock-poetic section labels ("From the field", "On our desks") — plain functional labels; no micro-meta sentences under eyebrows; no generic step labels ("Step 1 / Step 2") — the verb is the label.
- Separators: middle-dot (`·`) rationed to max 1 per metadata line; no decorative colored status dots unless they convey real semantic state; no hairline borders on every row of a long list.
- Em-dash ban: zero em/en dashes (`—`, `–`) anywhere visible — headlines, body, quotes, captions, buttons. Use a period, comma, colon, or plain hyphen. One visible em-dash fails review.
- Copy self-audit before finishing: re-read every visible string; rewrite anything grammatically broken, unclear in referent, or that reads like an LLM trying to sound thoughtful. Plain copy beats cute copy.

## Marketing Surfaces

- Lead with one clear hero idea, then features/proof/pricing/CTA in a deliberate rhythm.
- Use concrete product language instead of generic startup filler.
- Do not invent metrics or customer logos.
- Avoid default AI-startup tropes unless the brief explicitly wants them.
- Hero support copy should usually stay within 2 sentences.
- Do not follow the hero with another long editorial paragraph unless the brief truly needs it; prefer one sharp supporting sentence or concise proof instead.
- If a split hero uses a product mock, screenshot, or proof card, that visual should feel weighty enough to balance the headline on desktop.
- When the page starts feeling wordy, cut copy and strengthen composition before adding more sections or shrinking the hero.

## Product Surfaces

- Build the real interface, not just the shell.
- Include expected controls, empty states, loading states, and useful information density.
- Dashboards should be scannable and operational, not decorative card collections.
- Product claims must match what the surface can actually do now.

## Self Review Loop

Before finishing, check:

- Is there one obvious visual thesis?
- Did the chosen style stay coherent?
- Did we avoid generic purple-gradient SaaS slop?
- Does the page have one memorable quality?
- Are states, spacing, and hierarchy resolved enough to feel intentionally designed?

Then run the mechanical pass — each of these is a countable check, not a vibe:

- Hero fits the first viewport with CTA visible; headline ≤2 lines; ≤4 hero text elements.
- Eyebrow count ≤ ceil(sections / 3); no section-number or version-label eyebrows.
- No 3 consecutive image/text split sections; ≥4 distinct layout families on a long page.
- Zero em/en dashes visible anywhere on the page.
- Zero fake div-built screenshots, fake-perfect numbers, or "John Doe" content.
- Every visible string re-read and rewritten if broken, unclear, or AI-cute.
- One page theme; no section flips between light and dark.

## Hard Rules

- Use this skill beneath Taste and with at most one shared style pack; commit to one coherent visual direction in the artifact.
- Do not mix visual systems.
- Do not flood the page with accent color.
- Do not use filler copy, fake numbers, or fake backend behavior.
- Do not expose stale model/vendor names in customer-facing copy.
- Prefer a strong restrained system over five disconnected flourishes.
- Do not let customer-facing landing pages turn into essay blocks; cut copy before shrinking the layout.

## Local Sources

This skill was adapted from the copied Open Design files under:

- `references/open-design/web-prototype/`
- `references/open-design/saas-landing/`
- `references/open-design/dashboard/`
- `references/open-design/critique/`

The `Layout Discipline` and `Anti-Slop Tells` sections are curated from taste-skill (https://github.com/Leonxlnx/taste-skill), MIT License, Copyright (c) 2026 Leonxlnx.
