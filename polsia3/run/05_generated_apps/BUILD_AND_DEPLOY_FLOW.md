# Generated App Build And Deploy Flow

When the operator clicks Build Company, the system should make the first useful website available quickly while the deeper product/backend work continues in parallel lanes.

## Phase 1: Foundation

1. Create company.
2. Save raw idea.
3. Run initial foundation/research.
4. Create mission/market docs.
5. Create default generated-app plan policy and project AI wallet/key.
6. Select template family, homepage variant, and product module.

## Phase 2: Website-First Lane

This lane should not wait for product/auth/Stripe/X add-ons.

1. Generate structured homepage config.
2. Build the customer-facing website surface with Claude Agent SDK / Claude 4.7, using OpenLovable-style polish as design guidance rather than the OpenLovable localhost app as the automation boundary. A verified cached surface may be used only when explicitly selected by a template key.
3. Run homepage install/typecheck/build/smoke checks.
4. Deploy website with Vercel CLI/API.
5. Health check website URL.
6. Save public website URL only if healthy.
7. Save `*.fourmanifold` alias/proxy only if healthy.

## Phase 3: Parallel Product And Platform Lanes

After the website lane is completed, generated-app source-dependent product lanes may run as durable jobs:

- product module backend/API implementation
- product workflow UI implementation
- product persistence tables/routes, when the module needs them

Deterministic setup lanes may run after foundation without waiting for the website:

- generated-app subuser auth/session wiring
- generated-app users/entitlements setup
- Stripe checkout setup and webhook wiring
- project AI gateway and per-subuser limit wiring

Foundation-only add-on lanes may run independently of product readiness:

- X/social growth setup and posting jobs, if configured and policy-allowed
- Meta/Sora creative generation, v0 display-only
- community/lead add-ons, with no posting unless explicitly permitted by that channel policy
- outreach copy after community research has produced real targets

Each lane has independent status. Product/backend failure must not erase or block a healthy website deployment, X/social, Meta/Sora, community, lead, outreach, or any other independent lane. A healthy website also must not mark the product lane complete.

## Phase 4: Product Readiness

Before the generated product is marked ready:

1. Product code install succeeds.
2. Typecheck succeeds.
3. Build succeeds.
4. Smoke tests cover homepage, product workflow, and backend routes.
5. Auth/session/entitlement checks pass or record explicit blocked state.
6. AI gateway path passes or records explicit blocked state.
7. Stripe checkout route exists when paid plans are enabled.
8. Deployed product URL health check passes.

If product core fails but homepage succeeds:
- website deployment can remain live
- independent add-on lanes can continue if their own dependencies are satisfied
- X/social jobs can continue if configured, policy-allowed, and rate-limit-allowed
- Meta/Sora creative generation can continue in v0 display-only mode if configured
- community/lead/outreach jobs can continue under their channel policies
- product lane is not marked ready
- next product action is recorded as blocked/failed, not success

See [PRODUCT_BACKEND_ROBUSTNESS.md](./PRODUCT_BACKEND_ROBUSTNESS.md) for the generated product backend gates.

## Builder Interface Decision - 2026-05-20

Decision:
- Do not be sycophantic about builder interfaces. Warn the operator before adopting brittle tooling boundaries.
- The current v0 implementation uses a direct Claude Agent SDK surface builder for generated-app customer-facing files.
- The OpenLovable localhost server/SSE stream/parser is no longer on the active generated-app build path.

Reason:
- The attempted OpenLovable localhost integration added brittle overhead: local server health, conversation resets, SSE parsing, XML/raw-code extraction, partial-file recovery, and CSS/JSX drift repair.
- That overhead made a simple website build harder and allowed a bad deploy to pass earlier health gates.
- A direct Claude Agent SDK / Claude 4.7 generation step can own the same customer-facing surface with fewer moving parts while deterministic Takyon rails still own auth, payments, sessions, AI gateway, limits, persistence, deploy, and secrets.

Current truth:
- The code still contains the local OpenLovable adapter from the interrupted implementation attempt, but `website_build_deploy` and `product_ui` no longer call it.
- `website_build_deploy` now writes deterministic platform rails, runs the direct Claude Agent SDK surface builder over the generated app workspace, then uses install/typecheck/build/deploy/health gates.
- `product_ui` now uses the same direct Claude Agent SDK surface builder instead of the OpenLovable adapter.
- The previous content/truthfulness validator is disabled for now by operator request because it rejected honest copy such as "your team owns implementation." This disablement applies to the active SDK surface builder and the inactive OpenLovable adapter. Typecheck, Next build, Vercel deploy, alias, and health checks remain active.

## Verified Implementation Status - 2026-05-19

Implemented:
- Build Company creates the company/workspace, task, and 12 workflow lane jobs. Mission/research docs are created by the foundation worker, not seeded as fake-complete documents.
- Foundation lane initializes generated-app plan policies, project AI wallet, model policy, and a project AI proxy key.
- Website lane has a local-worker builder that writes generated Next.js app source to `.takyon/generated/{companyId}`.
- Generated source includes a polished homepage, product page, and real generated-app API route that calls the platform runtime product-run endpoint.
- Platform runtime product-run endpoint verifies the project key, creates/updates a generated app user, grants/checks free entitlement state, persists the product run, and returns a real DB receipt id.
- Local generated-app install/typecheck/build gates passed in the smoke run.
- Vercel deploy/alias/health completed in browser E2E for `https://signalbridge-browser-e2e-20260519.fourmanifold.com` with health `200`.
- Product backend lane completed in browser E2E and `/product` returned `200`.
- Parent build task now syncs from child workflow outcomes and uses `blocked` for mixed completed/blocked/failed child lanes instead of collapsing the whole company into a failed-looking state.
- Generated-app auth/session routes are implemented: `/api/generated-apps/[slug]/auth/request`, `/api/generated-apps/[slug]/auth/verify`, and `/api/generated-apps/[slug]/session`.
- The targeted worker pass completed the `generated_app_auth` lane for company `bdffff4e-074f-4d3a-ab67-e924e19b9797`.
- X/social now creates a visible `ready` post row before publish gating. The targeted worker pass created a real X row and then blocked publish because the X daily platform limit was reached.
- Meta/Sora now submits and syncs a real OpenAI Sora video job in display-only mode. The targeted worker pass saved a real `video_...` provider job id and proxied media output URL.
- Build Company lane dependencies now explicitly require `website_build_deploy` before product source-dependent lanes (`product_backend`, then `product_ui`). Generated-app auth/users, Stripe setup, and AI gateway setup depend on `foundation` only. X/social, Meta/Sora, and community remain independent of product completion; outreach waits for community targets but not for website/product.
- Cached Latexflow runs now reuse prior verified foundation documents before any model call. This is a Latexflow-only acceleration path for repeat browser E2E runs; it copies agent-authored Mission and Market Research documents from an earlier successful Latexflow company and records `provider = cached-foundation`, rather than inventing placeholder docs.
- Dashboard generated-app previews now request live iframes eagerly and layer the iframe above the fallback card. The generated app still must allow framing; current fourmanifold-generated apps send `frame-ancestors` for `app.fourmanifold.com`, `fourmanifold.com`, `localhost:3000`, and `127.0.0.1:3000`.
- Dashboard generated-app previews keep the same browser-frame fallback visible until the iframe fires `load`. This prevents a deployed URL from showing a blank white tile during iframe paint, and uses the same pulsing status treatment for website and product previews.
- Cached Latexflow product backend now completes deterministically from the generated app’s existing platform-owned `/api/product/run` route and product module. It redeploys the existing generated app and records `productStatus = backend_published`; Claude Agent SDK remains for later product UI/customization, not for proving the cached backend route exists.
- The dashboard product preview is considered openable once the generated app deploy has a reachable `/product` route, even if later product polish is still queued.
- The main In progress panel suppresses failed workflow rows so the operator sees current/runnable work instead of stale red failures. Failure receipts remain in workflow rows/events for debugging.
- The Leads panel now prefers non-transactional outbound email rows, showing recipient email addresses and `sent email` chips. URL-only lead candidates no longer fill the primary Leads panel.
- The X lane suppresses frontend rows that do not have a real `provider_url` receipt. Non-receipted X failures/queued rows stay in the database for debugging, but they are not rendered as launch content on the customer-facing dashboard.
- The Meta creative modal now opens as video-only playback, without showing the generation prompt/body copy. Cached Latexflow Sora rows now carry a public media token in `output_url`, and the latest cached row was backfilled so the video route returns `200 video/mp4`.
- Verified for `latexflow-13`: the cached Meta/Sora row exists with a tokenized `/api/media-generation/.../content` URL, and both local and production media proxy URLs return `200 video/mp4`. If the dashboard still shows the creative lane as pending immediately after the delayed job runs, that is a stale render/timing issue, not missing media.
- Product UI/customer-surface generation has been moved off the OpenLovable localhost adapter and onto a direct Claude Agent SDK surface builder for the generated app workspace.
- The generated `Signalbridge Browser E2E 20260519` workspace built successfully after the content validator was disabled.
- Deployment completed with build id `8022bdf8-bd2f-46f4-8307-1b2781c6b281`, health `200`, deployment URL `https://argon-site-ii9fyi8u9-tejdivs-projects.vercel.app`, and alias `https://signalbridge-browser-e2e-20260519.fourmanifold.com`.
- Browser verification opened the live alias and confirmed `/`, `/product`, and `/signup` render with titles `Plan the E2E audits that keep your login, checkout, and consent flows trustworthy.`, `Compliance audit planner`, and `Create your Signalbridge account`.
- The Takyon dashboard now shows explicit `Open website` and `Open product` actions in the normal v2 board preview tiles. Browser verification confirmed the anchors on `app.fourmanifold.com` point to `https://signalbridge-browser-e2e-20260519.fourmanifold.com` and `/product`; a real click on the website tile navigated to the generated app.

Pending:
- Product-specific AI/provider execution in `/api/ai-gateway/messages`.
- Replacement content/visual quality gate. The old validator is intentionally disabled for now; the remaining checks are build/deploy/health/browser route checks.
- Stripe webhook lane.
- X publish receipt when rate-limit allows it.
- Manual browser playback of the authenticated Sora media proxy after each deploy.

Compatibility note:
- The existing Supabase database already had v2 generated-app tables with v2 column names. V3 code now targets those real columns instead of creating a second incompatible schema.

## Verified Implementation Status - 2026-05-20

Implemented:
- `/new/latexflow` and `/dashboard/latex` render the same Takyon intake UI with hidden template key `latexflow-v1` and business name `Latexflow`.
- `latexflow-v1` is a repo-cached generated-app surface under `generated-app-cache/latexflow-v1`.
- The cached surface copies only customer-facing files over deterministic rails:
  - `src/app/page.tsx`
  - `src/app/product/page.tsx`
  - `src/app/signup/page.tsx`
  - `src/app/globals.css`
  - `src/product/module.ts`
- The deterministic generated-app rails still come from `writeGeneratedAppTemplate`: package, config, layout, platform client, product run route, and project AI key wiring.
- Cached surface use is limited to `website_build_deploy` with `template = latexflow-v1` and no operator edit instruction. Operator-requested edits still route to the Claude Agent SDK builder.
- For future cached Latexflow builds, Sora creative is delayed instead of created immediately. The build plan queues `meta_seedance` with a roughly 3 minute `run_after`, then the worker writes a new company-owned cached Sora media row when that delayed job runs.
- Generated app `next.config.ts` now sets `Content-Security-Policy: frame-ancestors 'self' https://app.fourmanifold.com https://fourmanifold.com http://localhost:3000 http://127.0.0.1:3000` so dashboard iframe previews can load generated sites when the frontend uses `sandbox="allow-scripts allow-same-origin"`.

Verified:
- Generated cache smoke copied 5 cached files into `.takyon/cache-test/latexflow-v1`.
- `npm install --prefix .takyon/cache-test/latexflow-v1` and `npm run --prefix .takyon/cache-test/latexflow-v1 typecheck` passed.
- Production deploy `argon-site-1gfltyfi1-tejdivs-projects.vercel.app` was explicitly aliased to `https://app.fourmanifold.com`.
- In-app browser verification opened `https://app.fourmanifold.com/new/latexflow` and showed the Takyon `What do you want to build?` intake screen.
