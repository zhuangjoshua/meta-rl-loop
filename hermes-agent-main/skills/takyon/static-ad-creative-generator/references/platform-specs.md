# Platform Specs

Constraints for Meta (Facebook + Instagram) and Reddit static placements. Use these to set
`platform`, `placement`, `aspect_ratio`, `layout.safe_zones`, and to keep `copy` within
readable limits. These are practical creative limits, not a substitute for each platform's
current official spec sheet — verify before a paid launch.

## Aspect ratio → placement

`aspect_ratio` is a free-form `W:H` (decimals allowed). **Any ratio from 1:3 to 3:1 is
supported** and sized automatically; the table below lists the common ad ratios. Author one
spec per native ratio, or render one approved creative at several sizes with the generator's
`--aspect-ratio 1:1,9:16,1.91:1` flag.

| `aspect_ratio` | Shape | Best placements | Why |
| --- | --- | --- | --- |
| `1:1` | square | Meta feed, Reddit feed/comments | Safe everywhere, compact |
| `4:5` | vertical | Meta feed (IG/FB), Reddit feed | Max mobile feed real estate without story chrome |
| `1.91:1` | landscape | Meta feed / link / right-column, Reddit feed | Wide link-style and desktop placements |
| `9:16` | full vertical | Meta story/reels | Full-screen immersive |

Match shape to surface: vertical/square for mobile feed, `1.91:1` for landscape/link/desktop,
`9:16` for story/reels. The validator emits a soft warning when a ratio is unusual for the
chosen placement — it never blocks an intentional choice.

## aspect_ratio → image-model size

The backend **computes** a model resolution for any ratio (no fixed table). For **`gpt-image-2`**
it fixes the short edge to 1024px and scales the long edge to the requested ratio, rounded to a
multiple of 16 (gpt-image-2 accepts edges that are multiples of 16, each ≤ 3840, aspect 1:3..3:1).
Examples (`scripts/backends.py` → `resolve_size`):

| `aspect_ratio` | gpt-image-2 size | Note |
| --- | --- | --- |
| `1:1` | `1024x1024` | exact |
| `4:5` | `1024x1280` | exact |
| `1.91:1` | `1952x1024` | ≈1.906; `--crop` for exact 1.91 |
| `9:16` | `1024x1824` | ≈0.561; `--crop` for exact 0.5625 |
| `16:9` | `1824x1024` | ≈1.781; `--crop` for exact |

For the deprecated **`gpt-image-1`** (only `1024x1024`, `1536x1024`, `1024x1536`), the ratio is
snapped to the nearest of those three by orientation; use `--crop` for the exact frame.

With `--crop`, the generator center-crops the returned image to the exact target ratio (needs
Pillow). Ratios outside 1:3..3:1 are rejected with a clear error.

## Meta (Facebook + Instagram)

### Placements
- `feed` — IG & FB feed. AR `1:1`, `4:5` (preferred on mobile), or `1.91:1` (landscape/link/desktop).
- `story` — IG/FB Stories. AR `9:16`.
- `reels` — IG/FB Reels (static cover / image reel). AR `9:16`.

### Safe zones
- **Story / Reels (9:16):** keep text and logo **out of the top ~14% and bottom ~20–35%** —
  profile chrome sits up top, the caption/CTA/profile bar sits at the bottom. Center ~60% is safe.
- **Feed (4:5 / 1:1):** keep critical text ≥ ~5% from every edge; the bottom is overlaid by
  primary text + CTA in the UI, so don't put the key message flush to the bottom edge.

### Copy guidance (fields outside the image)
- **Primary text:** ~125 characters before "...more" truncation on mobile — front-load the hook.
- **Headline:** ~27–40 characters recommended.
- **Description:** ~30 characters, often not shown; don't rely on it.
- **CTA:** use a standard button label (Learn More, Shop Now, Sign Up, Get Offer).

### Baked-in overlay text
- The old "20% text" hard rule is retired, but **heavy text still suppresses reach and reads
  as an ad.** Keep `copy.overlay_text` to one short line / a few words. Legibility first.

### Native conventions
- UGC, candid phone photography, before/after, and real demos outperform polished stock.
- Avoid stocky "corporate handshake" energy; it screams ad and gets scrolled past.

## Reddit

### Placements
- `reddit_feed` — Free-form / image post in feed. AR `1:1` or `4:5`.
- `reddit_comments` — Conversation placement; compact, `1:1` works best.

### Safe zones
- Feed image gets a title above it and engagement bar below; keep ~6% edge margins. On the
  comments placement the image renders small — make the focal point huge and legible at thumbnail size.

### Copy guidance
- **Title** is the workhorse — it sits above the image and carries the hook. Plain, specific,
  community-voiced (not ad-speak) wins.
- Keep baked overlay text minimal; let the title do the talking.

### Native conventions
- Redditors punish obvious advertising. Native-post, contrarian, and genuine product-proof
  angles fit best. Match the subreddit's voice. Disclose sponsorship as required.
- Do **not** impersonate real users, real subreddits as authentic UGC, or fabricate
  upvote/award counts as if real. Representative mocks must read as illustrative.

## Quick consistency rules (enforced by `validate_spec.py`)

- `placement` must belong to `platform` (`feed/story/reels` → meta; `reddit_*` → reddit).
- `aspect_ratio` unusual for the placement ⇒ soft warning (feed: `1:1`/`4:5`/`1.91:1`;
  story/reels: `9:16`; reddit_feed: `1:1`/`4:5`/`1.91:1`; reddit_comments: `1:1`/`4:5`). Any
  `W:H` from 1:3 to 3:1 is allowed — the warning never blocks an intentional choice.
- `copy.overlay_text` longer than ~50 characters ⇒ readability warning.
- `copy.headline` over 40 chars / `copy.primary_text` over 125 chars ⇒ truncation warning.
