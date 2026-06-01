---
name: takyon-autonomous-seo-geo-operator
description: >-
  Improve one Takyon business's search visibility by auditing its current
  product/site source, understanding the business from real copy, then making 1-5
  honest SEO/GEO changes: metadata, schema/JSON-LD, internal links, sitemap,
  robots.txt, llms.txt, and a few strategic BOFU pages. Publishes bounded
  product/site updates plus a canonical visibility report. Runs with NO paid APIs
  and NO hosted services; optionally sharpens prioritization with Google Search
  Console, OpenSEO, DataForSEO, or SERP MCPs IF they are already connected. Never
  fabricates rankings, volumes, reviews, prices, logos, testimonials,
  integrations, or "best/#1" claims.
version: 1.0.0
author: saialisetty1
license: Proprietary
platforms: [linux, macos]

metadata:
  hermes:
    category: takyon
    tags: [takyon, seo, geo, aeo, ai-search, schema, json-ld, metadata, internal-linking, llms-txt, sitemap, robots, content, bofu]
    related_skills: [takyon-build-product, takyon-business-metrics, takyon-market-research]
    requires_toolsets: [takyon]
    requires_tools:
      [
        business_read_business,
        business_read_file,
        business_list_files,
        business_write_file,
        business_patch_file,
        business_claude_agent_task,
        business_verify_product_surface,
      ]
    routing:
      owns: >-
        Per-business search-visibility audits and bounded SEO/GEO improvements
        across product-site source, plus a canonical visibility report, with no paid APIs.
      when_to_use:
        - A business product/site needs improved search or AI-answer visibility through honest metadata, schema, internal-linking, sitemap, robots.txt, llms.txt, or a few BOFU pages.
        - The operator wants a small, reviewable SEO/GEO pass grounded in existing product truth and site source.
        - Optional GSC, OpenSEO, DataForSEO, or SERP context is available and should sharpen prioritization without blocking the run.
      do_not_use_for:
        - Off-page work needing real external action (backlink outreach, buying links, posting to social/forums) — out of scope.
        - Fabricating keyword volumes, rankings, reviews, testimonials, or competitor claims — explicitly forbidden, not a capability.
        - Claiming live ranking wins, deploy state, or merged publication without real evidence or receipts.
  takyon:
    scope: business
    allowed_roots: [product, metrics]
    output_root: product
    publication:
      - product/site
      - metrics/seo/seo-geo-report-<YYYY-MM-DD>.md

required_environment_variables: []
required_credential_files: []
---

# Autonomous SEO/GEO Operator

## Overview

Make a website rank and get cited — in classic search and in AI answer engines — by
making small, real, reviewable changes to its source repo. In Takyon, the durable
truth is bounded `product/site` updates plus a visibility report under `metrics/seo/`,
not a branch or PR by itself. This skill reads the repo
to understand the business, audits the pages with bundled zero-dependency scripts,
chooses the **1-5 highest-leverage changes** for this run, implements them
(metadata, schema, internal links, technical hygiene, GEO basics, and a few strategic
BOFU pages), runs quality gates, and writes a concise visibility report.

It is a **skill, not a platform**. It requires no SaaS, no database, no workers, no
hosting, and **no paid APIs**. Google Search Console, OpenSEO, DataForSEO, and SERP
tools are treated as *optional accelerators*: if one is already connected as an MCP
it sharpens prioritization, but its absence never blocks a run and never licenses
inventing numbers.

GEO/AEO here is **SEO applied to AI surfaces**, not a separate discipline: ~92% of AI
Overview citations come from top-10 ranking pages, so ranking fundamentals come first
and the AI-specific levers (answer-first passages, citable definitions, entity/schema
clarity) sit on top.

## When to Use

- "Run SEO/GEO optimization on this site."
- "Improve this site's SEO and create missing BOFU pages."
- "Optimize this site for AI search / GEO / AEO."
- "Find and fix simple SEO issues."
- "Create a few strategic SEO pages for this product."
- "Improve metadata, schema, internal links, sitemap, robots, and llms.txt."

### When NOT to Use

- The work is off-page (link outreach, social posting, press) — this skill only
  changes the repo.
- The user wants hundreds of programmatic pages, or any fabricated data (volumes,
  rankings, reviews, logos, testimonials, integrations, "#1" claims). Decline those
  parts; do the honest subset.
- The user wants a deployed production result, a merged review artifact, or guaranteed
  ranking outcomes. This skill stops at truthful local publication plus optional
  product-surface verification.
- It is purely an audit with no permission to change files — you *can* still run it
  read-only, but its value is the real changes; say so.

## Quick Reference

- **Primary roots:** `product/`, `metrics/`
- **Publication paths:** `product/site/`, `metrics/seo/seo-geo-report-<YYYY-MM-DD>.md`
- **Default data source:** the repo itself (always available). External SEO data is optional.
- **Budget per run:** 1-5 meaningful changes. Keep the diff reviewable.
- **Tool names used by this skill:** `business_read_business`, `business_read_file`, `business_list_files`, `business_write_file`, `business_patch_file`, `business_claude_agent_task`, `business_verify_product_surface`
- **Bundled scripts** (zero deps, read-only, run against the target repo):
  - `scripts/extract-routes.ts` — detect framework + enumerate routes
  - `scripts/check-metadata.ts` — title/meta/H1/canonical/OG/schema/thinness lint
  - `scripts/check-internal-links.ts` — broken-link + orphan-page detection
  - `scripts/validate-jsonld.ts` — JSON-LD parse + truthfulness flags
- **Templates:** `templates/seo-report.md`, `templates/page-brief.md`
- **Reference:** `references/seo-checklist.md` (the full checklists)
- **Output:** a saved visibility report plus bounded `product/site` updates; git review is optional and never the Takyon truth surface.
- **Hard rules:** never fabricate; never claim deploy/ranking wins without evidence; never treat a branch/PR as the canonical publication.

## Prerequisites

### Required Context

- **The target website's source repo**, available as the working directory. This is
  the only hard requirement.
- **Node.js** (any modern version) to run the bundled scripts. Node ≥ 22.6 runs the
  `.ts` files directly via type-stripping; otherwise use `npx tsx <script>`. If Node
  is unavailable, do the same checks by reading files manually.
- Start with canonical Takyon business state: call `business_read_business`, then
  inspect `product/surface.md`, the current site source under `product/site/`, and any
  prior `metrics/seo/` outputs with `business_read_file` and `business_list_files`.
- For non-trivial source edits under `product/site/`, prefer `business_claude_agent_task`
  over large inline rewrites.

### Optional Context

- **A live URL.** If provided AND a browser/fetch tool is available (Claude-in-Chrome,
  the preview MCP, or `WebFetch`), crawl a *small* sample to compare rendered output
  vs. source. Otherwise rely on local route inspection.
- **Google Search Console** (e.g. an `mcp-gsc` / `google-search-console-mcp` server),
  or a **pasted GSC Performance CSV export**. Unlocks striking-distance (positions
  11-20), position-aware CTR gaps, opportunity scoring, cannibalization, and decline
  alerts.
- **OpenSEO / DataForSEO / SERP MCP** for keyword/SERP/competitor validation.
- **Discover optional tools, do not assume them:** run a `ToolSearch` for keywords
  like `search console`, `gsc`, `openseo`, `dataforseo`, `serp`, `keyword`. If a
  matching tool exists, use it and label findings *data-backed*. If not, proceed with
  *inferred* strategy and label it as such. **Never fail because an optional source is
  missing.**

## Data Source Priority

Use the highest-confidence source available; degrade gracefully; never fabricate.

1. **DEFAULT — repo inspection + inferred strategy (always on).** Render mode, HTML,
   existing schema, headings, internal links, robots.txt, sitemaps, content depth,
   detected business type. Every workflow must reach a useful result from this alone.
2. **Optional — pasted GSC CSV export.** Real demand data without any API.
3. **Optional — live GSC connection (MCP/API).** Use a stable 28-day window ending
   ~3 days ago with confirmed/final data; never compare a partial recent period
   against a complete prior one.
4. **Optional — free fallbacks.** Bing Webmaster Tools, a `site:` search for
   indexation, pure on-page heuristics.
5. **Optional — live SERP / OpenSEO / DataForSEO.** Difficulty/intent/competitor
   proxy (who ranks, SERP features, ranking page formats). When unavailable, infer
   difficulty and intent directly from the live SERP if you can read one; if not, use
   qualitative on-page judgment.

**Hard rule:** a missing source lowers confidence and is stated as such — it never
causes failure and never licenses inventing volumes, rankings, difficulty, or CTR.
Label every finding **data-backed** or **inferred/heuristic**.

## References

- `references/seo-checklist.md` — the full technical, on-page, content-quality,
  GEO/AEO, schema, internal-linking, truthfulness, and repo-safety checklists. Read it
  before the audit and use it as the gate before finishing.

## Templates

- `templates/page-brief.md` — fill ONE before creating any page (forces a single
  intent, real claims, and a claims-to-avoid list).
- `templates/seo-report.md` — the required output report structure.

## Scripts

All scripts are dependency-light (Node built-ins only), read-only, and print JSON.
The paths below are relative to **this skill's own directory** (the folder containing
this `SKILL.md`); the first argument is the **target site repo** (omit it to scan the
current directory). Use the skill dir's absolute path when the cwd is the target repo,
e.g. `node /path/to/skill/scripts/extract-routes.ts .`.

```bash
node scripts/extract-routes.ts <repo>           # framework + route map
node scripts/check-metadata.ts <repo-or-paths>  # on-page metadata lint
node scripts/check-internal-links.ts <repo>     # broken links + orphans
node scripts/validate-jsonld.ts <repo-or-paths> # JSON-LD validity + truth flags
# If `node` rejects .ts files: npx tsx scripts/<name>.ts ...
```

They are heuristic helpers, not oracles — confirm framework-source findings by reading
the file. `validate-jsonld.ts` emits `truthFlags` for ratings/reviews/prices/offers;
those fields must reflect REAL data or be removed.

## How to Run

The common path:

1. Call `business_read_business` first. Confirm the business, current product surface,
   and whether `product/site/` already contains the source this skill should edit.
2. Use `business_read_file` and `business_list_files` to inspect `product/surface.md`,
   the current site tree, and any existing `metrics/seo/` reports before changing
   anything.
3. Run `extract-routes.ts` to detect the framework and map routes, then run
   `check-metadata.ts`, `check-internal-links.ts`, and `validate-jsonld.ts` against the
   business source path.
4. Read the homepage + key pages and README/docs/package metadata to understand the
   business (see Procedure step 1). If optional GSC/OpenSEO/SERP tools are available,
   use them to sharpen prioritization without making them required.
5. Pick the **1-5** highest-leverage changes (see Decision Principles in Procedure
   step 5). For each new page, fill `templates/page-brief.md` first.
6. Use `business_write_file` / `business_patch_file` for small edits and
   `business_claude_agent_task` for substantial `product/site/` implementation work.
7. Run Quality Gates. Re-run the helper scripts on the changed scope and fix anything
   they surface.
8. Write the report from `templates/seo-report.md` to
   `metrics/seo/seo-geo-report-<YYYY-MM-DD>.md`.
9. If the operator asked for surface verification or publication, call
   `business_verify_product_surface`. If the operator also explicitly wants git review,
   a branch/PR can be prepared as an additional workflow, never as the source of truth.

## Procedure

This is the end-to-end SEO + GEO workflow. Keep each run small and finish with truthful
local outputs.

### 1. Understand the business (no invention)

Call `business_read_business` first. Then read homepage copy, product/pricing pages,
README, docs, package metadata, and any existing marketing pages. Identify, **only
from real evidence**:

- company name, product name, category
- ICP (who it's for), user pain points, main use cases
- competitors/alternatives **only if explicitly present** in repo/site/user input
- core conversion goal (signup, trial, demo, purchase, contact)
- business type (SaaS / local / e-commerce / publisher / agency) — this reweights
  what matters.

Record every uncertain item as a clearly marked **assumption** in the report. Do not
invent claims, customers, or differentiators.

### 2. Inspect the repo

Use `business_read_file` and `business_list_files` to locate the current site source,
`product/surface.md`, and any earlier visibility artifacts. Then detect the framework
(Next.js app/pages router, Astro, Remix, SvelteKit, Nuxt, Gatsby, Vite, static HTML,
or other) with `extract-routes.ts`. Then locate:

- page routes; metadata conventions (e.g. Next `metadata` export / `<Head>`, Astro
  frontmatter + layout, SvelteKit `<svelte:head>`, Nuxt `useHead`)
- sitemap generation; robots.txt; layout/`head` files; navigation components
- existing schema/JSON-LD; blog/content directories; markdown/MDX content
- existing use-case / comparison / alternative pages (don't duplicate them).

Note the **render mode**: critical content and schema must be server-rendered
(SSR/SSG/prerender) — AI crawlers and some bots don't execute JavaScript. Flag
client-only critical content or client-injected schema/canonicals.

### 3. Analyze pages (crawl or static)

Prefer local route inspection. If a live URL + browser/fetch tool is available, crawl
a small sample and compare rendered HTML to source. For important pages collect: path,
title, meta description, H1, H2s, canonical, robots/noindex, OG/Twitter, schema,
internal links, word count. Run the three audit scripts and flag the usual issues
(missing/duplicate/weak title; missing/weak meta; missing or multiple H1; missing
canonical/OG; missing sitemap; bad robots; missing schema; weak internal links;
orphan/thin pages; unclear intent; generic copy; pages targeting no obvious query).

**Fix blockers before optimizations:** anything that prevents crawl/index or risks a
penalty (Critical) comes before any micro-tweak — an unindexed page earns zero.

### 4. Optional data (sharpen, never require)

If GSC is available (live or pasted CSV): find pages with impressions but low CTR
(judge CTR against a *position-aware* benchmark, not a flat number), queries ranking
positions 11-20 (striking distance, impressions > ~100), pages losing clicks (>20%
period-over-period), queries with no matching page, and high-impression queries with
weak titles/meta. If OpenSEO/SERP is available: validate keyword variants, intent,
competitor page patterns, related questions, SERP features. **Otherwise infer** target
queries from product/category/ICP/use cases and mark them *inferred, not measured*.

### 5. Choose 1-5 high-leverage changes

Pick by **leverage = (impact × winnability) / effort**, capped at 1-5 per run:

- Resolve **veto/critical** issues first (broken links, self-contradictory data,
  fabricated/invalid schema, indexing blockers, clickbait title mismatch).
- Prioritize by **business fit and opportunity, not raw volume**: Opportunity ≈
  (Volume × Intent Value) / Difficulty, where intent weights informational = 1,
  commercial = 2, transactional = 3. With GSC data, rank by quantified upside
  (impressions × (target_CTR − current_CTR)) and favor striking-distance pages.
- **Small/new sites → BOFU-first:** optimize homepage metadata/H1/H2s; create 1-3
  use-case pages; add 1-2 comparison/alternative/problem pages if justified; add FAQ
  + Organization/WebSite/SoftwareApplication schema; add llms.txt; add internal links;
  sanity-check sitemap/robots.
- Prefer changes that **unblock or compound** (an indexing fix, a pillar that anchors
  a cluster) over isolated tweaks.

Every selected change must be **falsifiable and monitorable**: state the observation
it rests on, what it unblocks/depends on, how you'd know it failed, and a leading
indicator to watch. List everything you deliberately skipped (and why) in the report.

### 6. Make the changes

Implement using the framework's own conventions (see Page Creation Rules and
Optimization Rules). For each new page, fill `templates/page-brief.md` first. Add
internal links **both ways** (existing → new and new → relevant pages). Keep diffs
small and idiomatic to the surrounding code. Use `business_write_file` or
`business_patch_file` for narrow edits, and `business_claude_agent_task` when the site
work spans multiple files or needs a bounded implementation worker.

### 7. GEO / AEO pass

On the changed/created pages, apply the AI-search levers (details in *Optimization
Rules → GEO/AEO* and the checklist): answer-first opening (direct answer in the first
40-60 words), standalone 25-60 word definitions for core terms, question-style
headings, self-contained sourced/dated facts, Q&A/FAQ blocks, tables/lists, and
server-rendered content + schema. Create or update **`/llms.txt`** with concise,
grounded company/product/category summaries and key URLs (treat llms.txt as low-cost
optionality, not a promised ranking lever).

### 8. Quality gates, report, and verification

Run the Quality Gates. Then write the report from `templates/seo-report.md` to
`metrics/seo/seo-geo-report-<YYYY-MM-DD>.md`. If the changed source should be checked
for honest publication state, call `business_verify_product_surface` and record any
blocker truthfully in the report or `product/surface.md`. Never claim a deploy,
ranking win, merge, or publication state without receipts or evidence.

## Page Creation Rules

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
duplicate or thin content; natural keyword variants.

**Technical** — confirm key pages are indexable (not accidentally noindex/blocked);
robots.txt doesn't block important paths, CSS, or JS; canonical present where the
framework supports it; OG/Twitter present; valid XML sitemap of canonical 200 URLs
referenced from robots.txt; no broken internal links or redirect chains introduced;
changed pages build; generated schema is valid JSON-LD. Don't bump `dateModified`
without a real change.

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
Q&A/FAQ blocks; comparison tables and scannable lists; server-rendered content +
schema; consistent entity naming + `Organization`/`Person` `sameAs`; create/update
`/llms.txt`. Present GEO patterns as directionally sound, never as guarantees.

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
  `business_verify_product_surface` and reflect the result or blocker truthfully.
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
  add/refresh llms.txt → entity schema with `sameAs` → report + verify if needed.
  Label AI-visibility claims as probabilistic.
- **"Find and fix simple SEO issues."** → run the three scripts → fix missing
  titles/meta/H1, a bad canonical, a broken internal link, and an absent sitemap
  reference in robots.txt → report + honest local publication. No new pages if none are
  justified.

## Common Pitfalls

- Requiring GSC or any paid API — they are always optional; degrade to repo + CSV +
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
