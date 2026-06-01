# SEO / GEO Checklist

The working checklists for `autonomous-seo-geo-operator`. Read the relevant sections
before the audit; use them as the gate before opening the PR. These are decision
criteria, not a script — apply judgment weighted by the site's business type
(SaaS / local / e-commerce / publisher / agency) and maturity (new / established /
migrating / recovering).

Two cross-cutting rules sit above everything:

- **Fix blockers before optimizations.** A page that can't be crawled or indexed earns
  zero, so indexing/crawl/penalty issues come before any micro-tweak.
- **Never fabricate.** If a value is unknown, write "unknown". See *Truthfulness*.

---

## Technical SEO

- [ ] **Indexable first.** Confirm key pages are actually indexed/indexable (not
      accidentally `noindex` or robots-blocked). Free checks: GSC URL Inspection if
      connected, else a `site:` search, Bing Webmaster Tools, or reading raw HTML.
- [ ] **Triage indexing visibility-weighted** (highest-impression/value URLs first) into
      Critical (was getting impressions, now blocked/not indexed) → High (canonical
      mismatch on a traffic page) → Medium (robots/fetch failure) → Low (soft
      exclusion). Fix Critical first.
- [ ] **Render mode.** Critical content, structured data, canonicals, and answers exist
      in server-rendered HTML (SSR/SSG/prerender) — AI crawlers and many bots don't run
      JS. Flag SPA frameworks where critical content/schema is client-injected.
- [ ] **robots.txt** is valid, doesn't block CSS/JS or important sections, and references
      the sitemap. AI-crawler directives (GPTBot, ClaudeBot, PerplexityBot,
      Google-Extended) reflect an **intentional** policy (allow citation-driving bots if
      AI visibility is a goal; block only deliberately). Note: blocking Google-Extended
      stops Gemini *training* use but does NOT remove the site from Google Search.
- [ ] **Canonicals** are present and correct where the framework supports them; no
      canonical conflicts; check raw HTML vs. JS-rendered for mismatched canonical/robots.
- [ ] **No duplicate/thin pages, index bloat, or pagination mishandling.**
- [ ] **HTTPS everywhere** with valid SSL, no mixed content; sensible security headers
      (HSTS, CSP, X-Content-Type-Options, X-Frame-Options, Referrer-Policy). No
      malware/security flags.
- [ ] **Core Web Vitals** at the 75th percentile with CURRENT thresholds: LCP < 2.5s,
      INP < 200ms (INP replaced FID in 2024 — never use FID), CLS < 0.1.
- [ ] **Mobile**: responsive, touch targets ≥ 48×48px, legible fonts, no horizontal scroll.
- [ ] **URL structure**: descriptive, logical hierarchy, consistent trailing-slash,
      reasonable length; no redirect chains/loops or soft 404s; non-200s aren't silently
      blocking rendering or wasting crawl budget.
- [ ] **Semantic HTML** (article/section/nav/proper headings, not div soup).
- [ ] **No broken internal/external links** and **no self-contradictory on-page data**
      (both are critical/veto failures). Run `check-internal-links.ts`.
- [ ] **XML sitemap**: canonical, indexable, 200-status URLs only (exclude noindex,
      redirected, non-canonical, HTTP-only). ≤ 50,000 URLs per file; split into a sitemap
      index above that. Realistic `<lastmod>`; omit `<priority>`/`<changefreq>` (ignored).
- [ ] **Honest `dateModified`** — never bump dates without a real, substantive change.

---

## On-Page SEO

- [ ] **One clear search intent per page**; assign each keyword cluster to exactly one
      page to avoid cannibalization.
- [ ] **One H1**, then a clean H1 → H2 → H3 hierarchy that maps to the intent.
- [ ] **Title** ≤ ~60 chars, unique per page, matches intent, honest (no clickbait
      mismatch — that's a veto). Homepage **brand-first** (`Acme | AI Workflow`); inner
      pages **keyword-first** (`AI Workflow for Teams — Acme`).
- [ ] **Meta description** ≤ ~155 chars with target keyword + value prop + CTA; unique
      (no boilerplate templates across pages).
- [ ] **Primary keyword placed naturally** in title, first ~100 words, ≥ 1 H2, and the
      URL. **Never stuff** — match genuine intent over density.
- [ ] **Answer-first**: lead each major section with the direct answer in the first
      40-60 words; front-load actionable value in the intro.
- [ ] **Descriptive, query-aligned headings**; prefer question phrasing where users search
      in questions (mine real questions from GSC when available).
- [ ] **Scannable structure**: 2-4 sentence paragraphs; a TL;DR/key-takeaways box near the
      top of long pages; tables for comparisons; numbered/bulleted lists for processes.
- [ ] **Strong above-the-fold value proposition** and a **clear CTA**.
- [ ] **Internal links** in and out with descriptive anchors (see *Internal Linking*).
- [ ] **Match page TYPE to the dominant SERP format** for the query when you can see it
      (guide vs. comparison vs. tool vs. category vs. local) — don't fight the SERP.
- [ ] **Striking distance** (when GSC data exists): for pages at positions 11-20 with
      impressions > ~100 and below-curve CTR, rewrite title/meta/snippet and add schema —
      a packaging problem, not a ranking problem. Decide re-optimize vs. consolidate vs.
      internal-link before defaulting to new content.

---

## Content Quality

- [ ] **Who / How / Why gate**: a visible credentialed author (mandatory for YMYL), a
      disclosed creation process, and a genuine reader-first reason to exist.
- [ ] **Audit content vs. source separately (MECE)**: content body (clarity, organization,
      extractability, originality) vs. source credibility (author, organization, site).
      Strong writing must not mask a weak/anonymous source, nor vice versa.
- [ ] **Score E-E-A-T explicitly** — Experience (first-hand evidence, original data,
      photos, before/after), Expertise (credentials, depth), Authoritativeness (external
      citations, recognition), Trustworthiness (contact info, policies, dates,
      corrections, HTTPS).
- [ ] **Original value** competitors lack: proprietary data, a novel framework, first-hand
      testing, contrarian insight, or a tool/template. No original substance = fails the
      exclusivity bar.
- [ ] **Word counts are coverage floors, not targets** (homepage ~500, service ~800, blog
      ~1,500, product 300-400+, location 500-600). Match depth to SERP competitors and
      intent; judge by whether the page fully answers intent.
- [ ] **Thin-content flags**: < 100 retrievable words, generic phrasing, no original
      insight/attribution, repetitive templated structure, factual errors.
- [ ] **Evidence**: map every claim to dated, named sources (primary > secondary); add
      publication + last-updated dates; acknowledge limitations and edge cases.
- [ ] **Definitions + FAQ**: a standalone 25-50 word definition for each core term; cover
      obvious follow-up questions.
- [ ] **Freshness is conditional**: year/"updated" markers are positive only within
      [current_year − 2, current_year]; a stale year on an evergreen page is a flag.
- [ ] **Content-type weighting**: reviews weight first-hand Experience + Exclusivity;
      how-tos weight Clarity + Expertise. Don't penalize a page for signals irrelevant to
      its type.
- [ ] **AI-draft review**: reject generic, unattributed, no-first-party-data output;
      readability (Flesch ~60-70, 15-20 word sentences) is a quality signal, not a ranking
      factor.
- [ ] **Decay**: flag pages/queries with > 20% click decline period-over-period (when data
      exists) and refresh. Distinguish **gap** (no page) vs. **cannibalization** (too many
      pages) vs. **decay** (a slipping page) — each gets a different action.

---

## GEO / AEO (AI Search)

GEO/AEO is SEO applied to AI surfaces — fundamentals first, these levers on top. Present
all of this as directionally sound and **probabilistic, never guaranteed**.

- [ ] **Answer-first**: open every section/key page with the direct answer in the first
      40-60 words so extractive engines can lift a clean response.
- [ ] **Citable passages** (~130-170 words), each self-contained with a specific sourced
      fact/definition that reads correctly when quoted out of context.
- [ ] **Standalone definitions** (25-50 words) for core terms AI can lift verbatim.
- [ ] **Question-style H2/H3** mirroring how users phrase prompts.
- [ ] **High factual density** — specific numbers, dates, named sources, statistics. The
      mantra is "specific, dated, and authoritative."
- [ ] **Q&A / FAQ blocks, comparison tables, scannable lists** — formats AI parses and
      cites easily. Pair `FAQPage` schema with visible Q&A only where Google supports it.
- [ ] **Server-render content + schema** (AI crawlers generally don't run JS).
- [ ] **Entity clarity**: consistent naming + `Organization`/`Person` schema with `sameAs`
      to authoritative profiles. Off-site brand presence (Wikipedia, Reddit, YouTube,
      LinkedIn) correlates more with AI visibility than backlinks — recommend it, don't
      fake it.
- [ ] **Topical depth** across the niche's sub-topics (aim ~70%+) so the domain reads as a
      niche expert.
- [ ] **Multimodal assets** (images/video/infographics) where genuinely relevant.
- [ ] **`/llms.txt`** present and grounded (company/product/category summary + key URLs).
      Treat it as low-cost optionality, NOT a proven ranking lever — clean crawlable HTML
      and a permissive robots.txt matter far more.
- [ ] **Track AI citation/visibility** as a first-class KPI; treat stable-impressions-but-
      falling-clicks as the signature of an AI Overview / SERP feature capturing the click.

---

## Schema (JSON-LD)

- [ ] **JSON-LD**, embedded in **server-rendered HTML**; validate with
      `scripts/validate-jsonld.ts` (and Rich Results Test where rich results are the goal).
- [ ] **`@type` matches actual page content**; include all REQUIRED + recommended
      properties. Common mapping: `Article`/`BlogPosting` (posts), `Product`(+`Offer`,
      `Review`/`AggregateRating`) (products — real data only), `LocalBusiness` (local),
      `SoftwareApplication` (apps/SaaS), `Organization` + `WebSite` (site identity),
      `BreadcrumbList`, `WebPage`, `Event`/`Recipe`/`VideoObject`/`Course` as applicable.
- [ ] **Schema must equal visible content** — no markup-only claims. Use `FAQPage`/`QAPage`
      and step schema ONLY when the questions/steps actually appear on the page.
- [ ] **No deprecated/retired types.** Verify a type is still active before using it
      (`HowTo` rich results are deprecated; `FAQPage` rich results are restricted to
      authoritative gov/health contexts; avoid `SpecialAnnouncement`, `ClaimReview` unless
      eligible).
- [ ] **Honest dates** in `Article`/`BlogPosting` (`datePublished`, `dateModified`); add a
      credentialed `Person` author.
- [ ] **Entity linking**: `Organization`/`Person` with `sameAs` to authoritative profiles;
      `Dataset` on original-research/statistics pages.
- [ ] **One coherent graph** for nested/multi-type markup (`Product` + `Review` +
      `Breadcrumb`) — never conflicting duplicate blocks.
- [ ] **Never** ship fake `aggregateRating`, `review`, `ratingValue`, `price`,
      `priceCurrency`, or `offers`. `validate-jsonld.ts` flags these for review.

---

## Internal Linking

- [ ] **Hierarchy**: important pages within a few clicks of the homepage.
- [ ] **Pillar ↔ cluster**: every cluster page links up to its pillar and laterally to
      relevant siblings; relevance signals concentrate on the canonical page.
- [ ] **New page gets 1-3 inbound links** from relevant existing pages (natural,
      descriptive, query-aligned anchors — never "click here" or exact-match spam) and
      links out to the homepage/product page and related pages.
- [ ] **Funnel equity** from high-authority/high-traffic pages to striking-distance and
      priority BOFU/commercial targets.
- [ ] **Fix orphans** (especially any earning impressions with no inbound links) and
      eliminate redirect chains.
- [ ] **On consolidation**, 301/canonical losers to the survivor and repoint internal links.
- [ ] **`BreadcrumbList`** navigation + matching schema where it reinforces hierarchy.
- [ ] **Don't change sitewide nav** unless the page is important enough; don't over-link.
- [ ] **No new broken internal links** — re-run `check-internal-links.ts` after edits.

---

## Truthfulness (non-negotiable)

- [ ] **No fabricated `Review`/`AggregateRating`/star ratings** — only with real,
      verifiable first-party reviews.
- [ ] **No invented prices/availability/`Offer` details** — real current data, or omit.
- [ ] **No fabricated rankings, traffic, search volume, keyword difficulty, or CTR** — if
      unavailable, label "unknown" rather than guessing.
- [ ] **No fabricated testimonials, customer quotes, case-study outcomes, client logos,
      awards, certifications, or integrations/partnerships** that don't exist.
- [ ] **No unsupported superiority claims** ("best", "#1", "leading", "fastest") without
      verifiable, cited evidence — remove or qualify.
- [ ] **No dishonest dates** — don't bump `dateModified`/`datePublished`/year markers
      without a real change.
- [ ] **Label provenance**: live SEO/SERP data / web-search evidence / local file (GSC CSV)
      / heuristic judgment. Mark each finding data-backed vs. inferred.
- [ ] **Don't overstate the skill's capabilities** — it can't browse arbitrary pages,
      auto-discover contacts, or guarantee rankings/AI citations.
- [ ] **Consistency**: schema, on-page text, and claims must agree; refuse
      self-contradictory data.
- [ ] **Privacy**: confirm a GDPR/privacy basis before publishing structured profiles of
      named individuals.

---

## Repo Safety

- [ ] **Work on a new branch**; never edit, commit to, or push `main`/`master`.
- [ ] **Cap the change set at 1-5 items**; keep the diff small and reviewable.
- [ ] **Match framework conventions** (metadata API, routing, layout/head, content
      collections) and keep edits idiomatic to surrounding code.
- [ ] **Don't introduce broken links, broken redirects, or build failures** — changed
      pages must build.
- [ ] **Run the repo's own** formatter / lint / typecheck / build; note any missing tool
      rather than inventing a result.
- [ ] **Don't add heavy dependencies.** The bundled scripts use only Node built-ins; don't
      pull in crawlers/SEO libraries unless already present.
- [ ] **Don't delete or rewrite content you didn't create** without surfacing the conflict
      first; prefer additive changes.
- [ ] **Gate persistence behind the PR**: writing files isn't "shipped" until a branch + PR
      exist (or the exact commands are printed). Never auto-publish, auto-merge, or deploy.
