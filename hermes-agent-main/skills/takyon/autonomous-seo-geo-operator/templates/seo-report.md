# SEO / GEO Report — <SITE NAME>

<!--
Fill every section. Keep it concise. Delete bracketed guidance as you go.
Label every finding/keyword as DATA-BACKED or INFERRED. Never fabricate.
Save as: seo/seo-geo-report-<YYYY-MM-DD>.md and paste the Summary into the PR body.
-->

- **Date:** <YYYY-MM-DD>
- **Repo / site:** <repo path or URL>
- **Framework:** <Next.js | Astro | Remix | SvelteKit | Nuxt | Vite | static | other>
- **Business type:** <SaaS | local | e-commerce | publisher | agency> · **Maturity:** <new | established | migrating | recovering>
- **Branch:** <seo/geo-pass-YYYY-MM-DD> · **PR:** <link or "commands printed below">
- **Changes this run:** <N> / 5

## Summary

<2-4 sentences: what you changed, why it's the highest-leverage move for this site
right now, and the single most important next action. Plain language — no internal
jargon.>

## Data Sources Used

- [ ] Repo inspection + inferred strategy (always)
- [ ] GSC — <live MCP | pasted CSV> (window: <e.g. 28d ending YYYY-MM-DD, confirmed data>)
- [ ] OpenSEO / DataForSEO / SERP — <which>
- [ ] Live crawl of <N> pages via <browser/preview/WebFetch>
- [ ] Other: <...>

## Unavailable Data Sources

<List what was NOT available and the impact on confidence. e.g. "No GSC → keyword
targets are inferred from product/ICP, not measured; striking-distance analysis
skipped." Absence lowers confidence; it never blocks the run.>

## Site Understanding

- **Company / product:** <...>
- **Category:** <...>
- **ICP:** <...>
- **User pain points:** <...>
- **Main use cases:** <...>
- **Competitors / alternatives (only if explicitly present):** <... or "none found in repo/site/input">
- **Core conversion goal:** <signup | trial | demo | purchase | contact>

## Opportunities Found

<Prioritized list. For each: the issue, the affected page(s), impact tier
(Critical/High/Medium/Low), and whether it's data-backed or inferred.>

| # | Opportunity | Page(s) | Tier | Evidence |
| --- | --- | --- | --- | --- |
| 1 | <...> | <...> | <Critical/High/Medium/Low> | <data-backed / inferred> |

## Changes Made

<The 1-5 changes actually implemented this run, each tied to an opportunity above.
For each, note the falsifiability check + leading indicator to watch.>

1. **<change>** — <what + why>. *How we'd know it failed:* <...>. *Leading indicator:* <...>.

### Pages Created

| URL | Type | Target query (intent) | Brief |
| --- | --- | --- | --- |
| `/use-cases/<x>` | use-case | "<query>" (commercial) | <link to page-brief or "n/a"> |

### Pages Optimized

| URL | What changed (title / meta / H1 / H2s / content / canonical / OG) |
| --- | --- |
| `/` | <...> |

### Internal Links Added

| From | To | Anchor text |
| --- | --- | --- |
| `/<from>` | `/<to>` | "<descriptive anchor>" |

### Schema Added / Changed

| Page | Type(s) | Notes (server-rendered? matches visible content?) |
| --- | --- | --- |
| `/` | Organization, WebSite | <...> |

### llms.txt Changes

<Created / updated / unchanged. Summarize what it now contains (company/product/
category summary + key URLs). Note it's optionality, not a promised ranking lever.>

## Changed Files

```
<git diff --stat or a bullet list of every file touched>
```

## Validation Results

| Gate | Result |
| --- | --- |
| Formatter | <pass / not present / n/a> |
| Lint | <pass / fail / not present> |
| Typecheck | <pass / fail / not present> |
| Build | <pass / fail / not present> |
| `validate-jsonld.ts` | <valid blocks / invalid / runtime-only> |
| `check-metadata.ts` (changed pages) | <title/meta/H1 present?> |
| `check-internal-links.ts` | <no new broken links? orphans?> |

## Assumptions

<Every uncertain inference, clearly marked. e.g. "Assumed primary ICP is X based on
homepage hero; not confirmed." Keyword choices labeled inferred.>

## Skipped Opportunities

<Deliberately not done this run, and why (out of budget, needs real data, competitor
not actually present, would risk a fake claim, etc.). This is the backlog.>

## Next Actions

<The ranked next 3-5 moves for a future run, highest-leverage first. Plain language.
Include any off-page recommendations (entity/brand presence) the skill can't execute.>
