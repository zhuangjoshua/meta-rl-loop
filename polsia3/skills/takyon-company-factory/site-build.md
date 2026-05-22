# Takyon Company Skill: Site Build

You are running a product-owned Takyon company skill. This skill is scoped to the current business and must not choose another skill.

Core job: build or improve the generated business web surface for the provided `business_id`.

Rules:
- Treat the generated business web surface as customer-facing web software, not the operator dashboard.
- Build two joined layers: the public website and the actual product core inside that website.
- The actual product core must be implemented in the generated Next app through Claude Code / Claude Agent SDK, especially when the product naturally includes AI.
- Open Lovable may be used for website shell/polish, but it must not be the canonical product runtime.
- Keep operator/auth/admin surfaces out of generated customer pages.
- Generate the site autonomously from the company foundation; do not ask the founder to write copy, choose sections, or manually provide assets.
- Aim above the observed Takyon generated sites: the output should have specific positioning, a polished visual system, and a useful first product workflow.
- The page must not be only a landing page. Include a concrete product workflow shell, calculator, intake flow, scanner, dashboard, planner, or similar interface that matches the business.
- If the workflow needs AI, include a server route that calls the Claude Agent SDK and a setup-required state when credentials are absent. Do not fake AI output.
- Do not create fake integrations, fake payments, fake auth, fake database state, or demo-only behavior.
- Browser-local state is acceptable for unsigned workflow state when clearly local to the visitor.
- If a required dependency is absent, return an explicit missing-dependency result.
- Use Vercel deployment when deployment credentials are available through the runtime/tooling.
- If Open Lovable or equivalent code-generation infrastructure is available, it may be used as the implementation engine; still return the concrete file/deployment result.

Return JSON with:
- `status`: `completed`, `needs_dependency`, or `failed`.
- `changed_files`: array of paths if code changed.
- `deployment`: deployment URL or null.
- `public_routes`: array of generated public routes.
- `protected_routes`: array of generated protected customer routes.
- `missing_dependencies`: array.
- `operator_review_notes`: short notes for the dashboard.
