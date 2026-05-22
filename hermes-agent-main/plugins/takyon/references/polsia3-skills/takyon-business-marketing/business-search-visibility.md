# Takyon Business Skill: Search Visibility

Source material copied and adapted from OpenClaw SEO skills and GEO SEO Claude scorecards: SEO audit, AI SEO, schema markup, site architecture, programmatic SEO, content strategy, citability, crawlers, llms.txt, brand mentions, platforms, technical checks, and report structure.

Purpose: produce an inspectable SEO/GEO backlog for the current business without changing the website.

Rules:
- Use only visible Takyon state, existing documents, deployment URLs, workspace files, and provided search/crawl evidence.
- Do not claim a crawl, ranking, schema validation, crawler access, or AI citation unless the evidence is present.
- Do not run upstream installers or update scripts.
- Do not write website files directly. Recommend bounded Takyon workflows when implementation is needed.
- Treat Meta, email, and social platforms as out of scope for this audit unless visible evidence connects them to search visibility.

Return Markdown with these sections:
- `# Business Search Visibility`
- `## GEO Scorecard`: score each visible area from 0 to 5 for citability, brand authority, content E-E-A-T, technical accessibility, schema, and platform presence. Mark unknown when no evidence exists.
- `## Search Intent Map`: buyer problems, high-intent queries, comparison queries, and programmatic page opportunities.
- `## Citability Gaps`: missing answer blocks, definitions, stats, proof, sourceable claims, and self-contained passages.
- `## Technical And Crawler Gaps`: only evidence-backed issues around robots, sitemap, SSR/readability, headers, speed hints, links, and llms.txt.
- `## Schema And Structure`: recommended schema types, page architecture, internal links, and snippets.
- `## Content Backlog`: prioritized pages/posts/tools with why each could matter.
- `## Implementation Candidates`: Takyon workflows to queue, such as `website_build_deploy`, `product_ui`, or `business_content_engine`.
- `## Unknowns`: checks that require a real crawl, search API, or operator-provided evidence.
