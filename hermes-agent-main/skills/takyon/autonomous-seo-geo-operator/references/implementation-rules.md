# SEO/GEO Implementation Rules

## Contents

- Page creation rules
- Optimization rules
- Quality gates
- Output and publication semantics
- Examples and pitfalls
- Verification, rules, and troubleshooting

## Page Creation Rules

Page creation is **opt-in only**. By default this skill should not add any new pages.
Only enter this section when the operator explicitly asks for new pages or explicitly
approves page creation for the current run.

Fill `templates/page-brief.md` before writing any page. One page = one clear search
intent, one H1, descriptive H2s, an above-the-fold value prop, an FAQ where useful,
internal links in and out, a clear CTA, and schema only where it matches visible
content. Match the page **type** to the dominant SERP format for the query when you
can see it.

| Type | URL pattern | Target query | Create when | Must include |
| --- | --- | --- | --- | --- |
| **Use-case** | `/use-cases/[use-case]` | "[category] for [job/ICP/problem]" | A segment has its own journey + demand; BOFU driver for small sites | Clear problem, who it's for, how the product helps, practical workflow, FAQ, internal links, schema where apt |
| **Alternative** | `/alternatives/[competitor]` | "alternatives to [Competitor]" | Competitor is explicitly present in repo/site/user input or obvious from category | Answer-first verdict, comparison table, **honest** positioning/tradeoffs/selection criteria, CTA |
| **Comparison** | `/compare/[x]-vs-[y]` | "[X] vs [Y]" | Decision-stage demand and (if visible) SERP shows comparison results | Real analysis, decision table with criteria, clear verdict, neutral language where facts are uncertain — **never invent feature gaps** |
| **Problem/solution** | `/solutions/[problem]` | "how to solve [problem]", "[problem] software" | Informational/BOFU demand around a problem the product solves | Direct answer up top, actionable steps, product-relevant resolution, internal link to the commercial page |
| **Glossary** | `/glossary/[term]` | "what is [term]" | The category has terms users genuinely search | Tight standalone 25-60 word definition + 200+ words context; internally linked; no dictionary spam |
| **Blog** | `/blog/[slug]` | informational long-tail | Only with a clear reason; prefer BOFU/use-case first | Credentialed author, dates, Article schema, original value, FAQ, cluster links |

Only create **Alternative/Comparison** pages for competitors that are real and present.
Only create **Integration** pages for integrations that actually exist. Frame
comparisons around positioning, use cases, tradeoffs, and selection criteria — never
fabricated wins or feature gaps. If a fact is uncertain, use neutral language.

## Optimization Rules

Concise here; the full checklists live in `references/seo-checklist.md`.

**On-page** — one clear intent per page; one H1 then clean H2/H3; title ≤ ~60 chars
(homepage brand-first, inner pages keyword-first); meta description ≤ ~155 chars with
keyword + value prop + CTA; titles must honestly match the page (no clickbait);
primary keyword placed naturally in title/first ~100 words/an H2/URL — never stuffed;
strong above-the-fold value prop; descriptive internal links in and out; clear CTA; no
duplicate or thin content; natural keyword variants. In existing-page-only mode,
preserve the current section structure and navigation; rewrite within existing sections
instead of appending new sections or nav items. Do not significantly increase the word
count on existing pages unless absolutely necessary.

**Technical** — confirm key pages are indexable (not accidentally noindex/blocked);
robots.txt doesn't block important paths, CSS, or JS; canonical present where the
framework supports it; OG/Twitter present; a real XML sitemap enumerates every canonical
indexable route from `extract-routes.ts` (never leave the seeded single-URL scaffold stub)
and is referenced from robots.txt; no broken internal links or redirect chains introduced;
changed pages build; generated schema is valid JSON-LD. Regenerating the sitemap to match
the route map is technical hygiene and does **not** count against the 1-5 change budget.
Don't bump `dateModified` without a real change.

**Schema (JSON-LD, server-rendered, matches visible content)** — add only what fits:
`Organization`, `WebSite`, `SoftwareApplication`, `Product` (only with real product
info), `FAQPage` (only when the FAQ is visible on the page and Google still supports
it for the context), `Article` (articles/blog only), `BreadcrumbList` (if breadcrumbs
exist or are added), `WebPage`. Use `sameAs` to authoritative profiles for entity
clarity. **Never** add fake `aggregateRating`, `review`, `price`, or `offers`. Verify a
type is still active (e.g. `HowTo` rich results are deprecated) before using it.
Validate with `validate-jsonld.ts`.

**Internal linking** — link each new page from 1-3 relevant existing pages (natural,
descriptive anchors), and to the homepage/product page and related pages. Build
pillar↔cluster structure; fix orphans; avoid sitewide nav changes unless the page is
important enough; don't over-link.

**GEO/AEO** — answer-first openings; standalone 25-60 word definitions; question-style
headings mirroring real prompts; high factual density (specific, dated, attributed);
server-rendered content + schema; consistent entity naming + `Organization`/`Person`
`sameAs`; create/update `/llms.txt`. In existing-page-only mode, apply these levers by
rewriting current copy and headings rather than adding new FAQ blocks, comparison
tables, or other new sections. Present GEO patterns as directionally sound, never as
guarantees.

**Truthfulness (non-negotiable)** — never fabricate ratings, reviews, prices, offers,
rankings, volumes, difficulty, CTR, testimonials, customer quotes, case-study
outcomes, logos, awards, certifications, or integrations; never make unsupported
"best/#1/leading/fastest" claims; keep schema, on-page text, and claims mutually
consistent; if a value is unknown, write "unknown" rather than guessing.

## Quality Gates

Before finishing, run what the repo provides and confirm the rest:

- run the **formatter** if available (e.g. `prettier`, `biome`, `gofmt`)
- run **lint** if available (`eslint`, `biome`, `astro check`)
- run **typecheck** if available (`tsc --noEmit`, `astro check`, `svelte-check`)
- run **build** if available (`npm run build` / framework build) — changed pages must build
- **validate JSON-LD** with `scripts/validate-jsonld.ts` (no `invalid` blocks)
- **re-run `check-metadata.ts`**: every changed/created page has title, meta, and one H1
- **re-run `check-internal-links.ts`**: no new broken links; intended pages aren't orphans
- confirm **`sitemap.xml` matches the actual canonical route map** (not just `/`)
- confirm **no unsupported factual claims** and **no page is pure fluff**
- confirm the **diff is small enough to review** (1-5 changes)

Prefer the package.json scripts the repo already defines. If a gate's tool isn't
present, say so in the report rather than inventing a result.

## Output Format

Produce a concise report from `templates/seo-report.md`, covering: summary; data
sources used; unavailable data sources; site understanding; opportunities found;
changes made; pages created; pages optimized; internal links added; schema added;
llms.txt changes; changed files; validation results; assumptions; skipped
opportunities; next actions. Save it to
`metrics/seo/seo-geo-report-<YYYY-MM-DD>.md`. The canonical work product is that report
plus the bounded `product/site/` changes it describes. If the operator explicitly
wants git review, you may also prepare a short branch/PR summary, but that is optional
and secondary.

## Publication & Optional Git Review

- Publish the visibility report to `metrics/seo/seo-geo-report-<YYYY-MM-DD>.md`.
- Publish any site edits under the canonical product source path, usually `product/site/`.
- If a changed source surface needs verification or honest publication state, call
  `business_refresh_product_surface` and reflect the result or blocker truthfully.
- If the operator explicitly asks for git review, a branch/PR may be created as a
  review artifact. It is never the canonical Takyon publication record.
- Never claim deploy state, merged state, or ranking improvement without evidence.

## Examples

- **"Improve this site's SEO and create missing BOFU pages."** → understand business →
  audit → on a new site, optimize homepage metadata + create 2 use-case pages + 1
  alternative page (competitor named in README) + Organization/WebSite/SoftwareApplication
  + FAQ schema + llms.txt + internal links → gates → report + truthful surface state.
  (≤5 changes.)
- **"Optimize this site for AI search."** → ensure server-rendered content/schema →
  rewrite top pages answer-first with definitions + FAQ + question headings →
  add/refresh llms.txt → entity schema with `sameAs` → report + refresh if needed.
  Label AI-visibility claims as probabilistic.
- **"Find and fix simple SEO issues."** → run the three scripts → fix missing
  titles/meta/H1, a bad canonical, a broken internal link, and an absent sitemap
  reference in robots.txt → report + honest local publication. No new pages if none are
  justified.

## Common Pitfalls

- Requiring GSC or any paid API — they are always optional; degrade to repo +
  heuristics and label confidence.
- Treating GEO/AEO as separate from SEO, or overselling llms.txt as a ranking signal.
- Adding `FAQPage`/`HowTo` schema everywhere (HowTo rich results are deprecated; FAQ is
  restricted) or shipping schema that doesn't match visible content.
- Chasing word-count targets, keyword stuffing, or generating near-duplicate
  programmatic pages (doorway-page risk).
- Recommending a new page for a "gap" query an existing page already serves (that's
  cannibalization, not a gap).
- Comparing a partial recent GSC period against a complete prior one (manufactures
  fake "drops").
- Writing files were changed but no report or verification state was recorded — the
  work is incomplete until `metrics/seo/` and `product/site/` tell the truth.
- Making more than 5 changes, or a diff too large to review.

## Verification Checklist

- [ ] Business understanding is grounded in real site copy; assumptions are labeled.
- [ ] At most 1-5 changes; the diff is small and reviewable.
- [ ] Every changed/created page has a title, meta description, and exactly one H1.
- [ ] Internal links added both ways; no new broken links; intended pages not orphaned.
- [ ] All JSON-LD is valid, server-rendered, matches visible content, and contains no
      fabricated ratings/reviews/prices/offers.
- [ ] No fabricated rankings, volumes, reviews, testimonials, logos, integrations, or
      "best/#1" claims; unknowns are labeled "unknown".
- [ ] Data sources used and unavailable are listed; findings labeled data-backed vs inferred.
- [ ] Quality gates run (formatter/lint/typecheck/build/validate) or their absence noted.
- [ ] Report written from the template and saved to `metrics/seo/`.
- [ ] If `product/site/` changed, the current surface state is honest and any
      verification result or blocker is recorded.

## Rules

1. Make **real product/site changes** that improve SEO/GEO, or leave a truthful report
   explaining exactly what blocked them.
2. **Never fabricate** data or claims. If unknown, say "unknown". Truthfulness outranks
   completeness.
3. Cap each run at **1-5 high-leverage changes**; fix indexing/penalty blockers first.
4. External SEO data (GSC/OpenSEO/DataForSEO/SERP) is **optional**; never require it,
   never fail without it, always label confidence.
5. Server-render critical content and schema; keep schema consistent with visible text.
6. **Never** claim deploy state, merged state, or ranking wins without real evidence or
   receipts. Git review is optional and secondary.
7. Match the repo's framework conventions and keep diffs idiomatic and small.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `node` won't run a `.ts` script | Use `npx tsx scripts/<name>.ts`, or upgrade to Node ≥ 22.6. |
| Framework not detected | Pass the repo root explicitly; check `package.json` deps; treat as static HTML and scan `*.html`. |
| Routes look wrong (dynamic/i18n/route groups) | The mapper is heuristic — verify against the framework's routing and the live nav before editing. |
| Schema is built at runtime (`JSON.stringify`/`dangerouslySetInnerHTML`) | `validate-jsonld.ts` reports it as `runtime`; validate against rendered HTML instead of source. |
| No GSC / no keyword data | Proceed with inferred strategy from product/ICP/use cases; label all keyword choices "inferred". |
| Competitor not actually present | Don't create alternative/comparison pages; record it as a skipped opportunity. |
| Operator wants git review | Prepare a branch/PR only as an additional review artifact; keep `metrics/seo/` and `product/site/` as the canonical truth. |
| Build fails after changes | Revert the offending change, keep the rest, and note the failure in the report. |
