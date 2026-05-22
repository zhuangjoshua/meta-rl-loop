# Takyon Business Skill: Product Design

Source material copied and adapted from Open Design's local-first design-system, artifact, critique, and export workflow patterns into Takyon's bounded per-business skill layer.

Purpose: create a practical webpage and app design brief that can guide `website_build_deploy` and `product_ui` without installing or running Open Design.

Rules:
- Use visible Takyon evidence only: mission, research, marketing context, search visibility, conversion review, generated-app receipts, workspace files, product docs, pricing rows, jobs, memory, and operator instruction.
- Do not run Open Design, install dependencies, start daemons, call MCP servers, spawn coding-agent CLIs, write outside the business workspace, or edit generated-app source files directly.
- Do not invent screenshots, Figma files, exported assets, user tests, customers, testimonials, metrics, integrations, or production findings.
- Treat Open Design as a reference for disciplined design artifacts: `DESIGN.md`-style design system, screen briefs, artifact QA, and design critique. The output must remain Takyon-owned.
- Keep recommendations implementable by existing Takyon workflows. If a change should modify the generated app, route it to `website_build_deploy` or `product_ui`.
- Prefer domain-fit design over generic visual polish. SaaS, CRM, ops, and analytics products should be calm, dense, scannable, and work-focused; consumer or creative products may be more expressive when evidence supports it.

Return Markdown with these sections:
- `# Business Product Design`
- `## Source Boundary`: state that Open Design was used only as reference patterns, not as a runtime dependency, daemon, MCP server, or repo-writing agent.
- `## Product Surface Snapshot`: what pages/app screens visibly exist, what is unknown, and what evidence supports the design direction.
- `## User And Job`: primary user, their job, urgency, objections, trust needs, and the first successful session.
- `## Design System`: palette direction, typography scale, spacing, layout density, component tone, iconography, data display, states, and accessibility notes.
- `## Page Briefs`: homepage, signup/pricing, product workflow, support/FAQ, and any business-specific page worth building.
- `## App Workflow Brief`: first-run flow, input model, output model, empty/loading/error/success states, upgrade path, and retention hook.
- `## Copy And Conversion Fit`: headline direction, proof needs, CTAs, pricing language, risk reversal, and where marketing context should shape UI text.
- `## QA Checklist`: mobile/desktop fit, text overflow, non-overlap, real links, no fake metrics, no unsupported claims, no one-note palette, no nested cards, no hidden side effects.
- `## Implementation Candidates`: concrete next Takyon workflows such as `website_build_deploy`, `product_ui`, `business_conversion_review`, `business_content_engine`, or `business_measurement_plan`.
- `## Open Questions`: missing evidence that should be gathered before committing heavier design work.
