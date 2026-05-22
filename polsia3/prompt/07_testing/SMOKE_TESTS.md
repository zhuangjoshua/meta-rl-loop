# Smoke Tests

Minimum platform smoke tests:
- homepage/dashboard renders
- create company
- dashboard loads company
- enqueue workflow job
- cron dispatch authorization fails without secret
- cron dispatch works with secret
- AI gateway rejects missing/invalid project key

Minimum generated app smoke tests:
- public homepage renders
- product route renders
- session endpoint responds
- checkout endpoint responds or blocks with exact missing Stripe reason
- AI route goes through gateway or blocks with exact budget/config reason
- no generated app provider key is exposed

## Verified Browser E2E - 2026-05-19 PT

Platform/operator E2E:
- Deployed build `argon-site-1dpfxp6n7-tejdivs-projects.vercel.app`.
- Aliased `app.fourmanifold.com` to that build.
- Opened `https://app.fourmanifold.com/dashboard/takyon` in the in-app browser.
- Created company `SignalBridge E2E 20260519` through the visible Takyon intake form.
- Landed on `https://app.fourmanifold.com/dashboard/companies/f46a2969-4d94-46f8-8c25-e48a89c980f4`.
- Verified the v2-style Takyon workspace rendered with queued build task, website/product preview tiles, Mission/Market Research docs, growth lanes, team, and CEO chat rail.

Database receipt:
- `workflow_jobs`: 12
- `business_documents`: 2
- `tasks`: 1

Correction after audit:
- All 12 workflow jobs were still `queued`.
- `agent_runs`: 0.
- The two documents were seeded placeholders, not real generated reports.
- This E2E did not prove the autonomous build, Hermes, research, website deploy, product backend, X, Stripe, Meta, community, or outreach lanes executed.
- Do not cite this E2E as a completed Build Company run.

Auth boundary smoke:
- No-cookie `https://fourmanifold.com/dashboard/takyon` redirects to `https://app.fourmanifold.com/dashboard/takyon`.
- No-cookie `https://app.fourmanifold.com/dashboard/takyon` redirects to `/auth/login`.

## Verified Browser E2E - 2026-05-19 PT, Exact V2 UI Slice

Platform/operator E2E:
- Deployed build `argon-site-6wyxzmphg-tejdivs-projects.vercel.app`.
- Aliased `app.fourmanifold.com` to that build.
- Opened `https://app.fourmanifold.com/new/takyon` in the in-app browser.
- Verified the exact v2 `TakyonOnboarding` screen: Takyon wordmark, `What do you want to build?`, prompt placeholder, and arrow Start button.
- Typed the company prompt using visible browser keystrokes because the in-app browser clipboard fill path was unavailable.
- Created company `SignalBridge Browser E2E 20260519`.
- Landed on `https://app.fourmanifold.com/dashboard/companies/bdffff4e-074f-4d3a-ab67-e924e19b9797?auto=pipeline`.
- Ran the local worker against that exact company until no queued jobs remained.

Receipts:
- Website URL: `https://signalbridge-browser-e2e-20260519.fourmanifold.com`
- Website health: `200`
- Product route health: `https://signalbridge-browser-e2e-20260519.fourmanifold.com/product` returned `200`
- Real docs visible in browser: Mission, Market Research, Community Launch Targets, Outreach Assets.
- Market Research browser modal contained real sections and cited sources, not `Blocked until research workflow collects evidence`.
- Browser workspace showed website completed, product lane published, and build task failed because one child lane failed.

Lane outcomes:
- `completed`: foundation, website, product backend, generated-app users/entitlements, Stripe setup, AI gateway setup, community research, outreach copy.
- `blocked`: generated-app auth, X/social.
- Original `failed`: product UI, Meta/Seedance.

This E2E proves the prompt-to-workspace-to-worker path executes real work. It does not prove the failed/blocked lanes are complete.

## Targeted Fix Verification - 2026-05-19 PT

Commands/checks:
- `npm run typecheck`
- `npm run migrate`
- `npm run build`
- Targeted local worker pass on company `bdffff4e-074f-4d3a-ab67-e924e19b9797`

Verified:
- Generated-app auth routes are present in the production build route table:
  - `/api/generated-apps/[slug]/auth/request`
  - `/api/generated-apps/[slug]/auth/verify`
  - `/api/generated-apps/[slug]/session`
- `generated_app_auth` completed in the targeted worker pass.
- X lane created a real `business_social_posts` row with status `ready` and did not publish because the X daily platform limit was reached.
- Sora lane completed a real OpenAI Sora job with model `sora-2`; DB status is `completed` and `output_url` points at the authenticated media proxy.
- Parent build task status for the browser-created company is now `blocked`, not `failed`, for mixed child-lane outcomes.
- Browser verification after deploy showed the v2-style board with the visible X post row, `Publish post`, `Generate Sora UGC`, and Sora media card. A stale-lane bug was found and fixed so the in-progress panel now uses only the latest job per workflow id.
- Browser verification after deploy `argon-site-dr6nssirz-tejdivs-projects.vercel.app` and explicit alias to `app.fourmanifold.com` confirmed the normal dashboard preview tiles show `Open website` and `Open product`.
- The verified tile anchors were `https://signalbridge-browser-e2e-20260519.fourmanifold.com` and `https://signalbridge-browser-e2e-20260519.fourmanifold.com/product`, both with same-tab navigation.
- A visible browser click on the website tile opened `https://signalbridge-browser-e2e-20260519.fourmanifold.com/`; the dashboard was then restored.
- `npm run typecheck` and `npm run build` passed before the dashboard preview-tile deployment.
- The direct Claude Agent SDK generated-app surface builder replaced the OpenLovable adapter on the active `website_build_deploy` and `product_ui` paths.
- The content validator was disabled for now by operator request after rejecting honest copy. Generated app build still passed `npm run build`.
- `buildAndDeployExistingGeneratedApp` deployed the inspected generated workspace with build id `8022bdf8-bd2f-46f4-8307-1b2781c6b281`, deployment URL `https://argon-site-ii9fyi8u9-tejdivs-projects.vercel.app`, alias `https://signalbridge-browser-e2e-20260519.fourmanifold.com`, and health `200`.
- Browser verification of the live alias confirmed `/`, `/product`, and `/signup` load with expected H1s.

Still not complete:
- A replacement content/visual quality gate for generated-app surfaces is still needed. The old validator is intentionally off for now.
- Manual browser playback of the authenticated Sora content route still needs a screenshot/visual receipt because the last screenshot capture timed out.

## Local Browser/Router Verification - 2026-05-20 PT

Target:
- Local Takyon dashboard on `http://localhost:3000`.
- Company `19687d0b-e1d4-4e78-a45c-2d11aa2a2161`.

Verified:
- In-app browser loaded `/dashboard/takyon` and opened the company dashboard.
- Dashboard showed X, Sora creative, Leads, and Community as automatic operating lanes with small refresh controls.
- `npm run typecheck` passed.
- `npm run build` passed.
- Direct server-side CEO chat router invocation with `Give me a daily digest and refresh leads.` queued:
  - `community_research`
  - `outreach_copy`
  - `ceo_wakeup`
- Browser reload of the same company showed the persisted operator message, CEO answer, and `Daily Report 2026-05-20`.
- A second CEO router check correctly answered that `agent_runner` cron is only a pulse and queued jobs require the local Mac worker.

Limit:
- The in-app browser automation failed to type into the chat textarea reliably. The chat action was therefore verified by direct router invocation plus browser reload of persisted output, not by a full typed-form browser submit.

## Latexflow Browser E2E Restart - 2026-05-20 PT

Platform/operator E2E:
- Deployed `app.fourmanifold.com` was used as the intake surface.
- Old Latexflow company `ebd90c03-cb99-433b-a39a-affdab61e326` was archived/cancelled before restarting.
- Created fresh company `Latexflow Powered Micro Saas` through the visible in-app browser intake.
- Fresh company id: `2e81017a-f6b8-4857-bd5b-d9f8f6d8662e`.
- Landed on `https://app.fourmanifold.com/dashboard/companies/2e81017a-f6b8-4857-bd5b-d9f8f6d8662e?auto=pipeline`.
- Browser dashboard showed separated automatic operating lanes: X, Sora creative, Leads, and Community.

Verified so far:
- `foundation` completed through the local worker.
- Real agent documents were created: `Mission` and `Market Research`.
- Website and downstream lanes were queued automatically by the platform.
- Manual worker execution exposed the operational truth: queued jobs do not execute unless the local Mac worker is running.
- An interrupted manual worker left `website_build_deploy` stuck as `running`.
- `npm run worker:recover` recovered that stale website job back to `queued`.

Still pending for this E2E:
- Run `npm run worker:local` continuously and verify the website deploy from the browser.
- Verify the generated public website and product routes in the in-app browser.
- Record final lane outcomes after the worker drains the queue.

## Latexflow Fresh Powered Verification - 2026-05-20 PT

Target:
- Company `648df60b-4588-47b6-85f4-89f620063bea`
- Dashboard `https://app.fourmanifold.com/dashboard/companies/648df60b-4588-47b6-85f4-89f620063bea?auto=pipeline`
- Generated app alias `https://latexflow-e2e-fresh-powered.fourmanifold.com`

Verified:
- First website build failed on invalid generated JSX text (`=>` inside a `<code>` node).
- Generated-app builder now sanitizes plain text inside `<code>` nodes before typecheck/deploy.
- Retry website build completed and browser verification showed `/` and `/product` on the generated alias.
- Community research produced 6 real community targets.
- Lead discovery is now persisted: 6 existing community targets were backfilled into 6 real `candidate` leads with source URLs and no fake emails.
- Production browser verification showed `6 leads` in metrics and `6 candidates` in the Leads lane.
- The dashboard no longer counts community targets as lead fallbacks; the visible Leads lane is backed by `leads` rows.
- X lane published a real post with provider id `2057026819547975740`.
- Sora creative lane submitted a real OpenAI Sora job.
- `npm run typecheck` passed.
- `npm run build` passed.
- `npm run migrate` applied `0008_lead_candidates_and_surface_cache.sql` and `0009_lead_candidate_status_check.sql` on the local connected database.
- Cached Latexflow surface smoke copied 5 surface files and generated workspace typecheck passed.
- Production deploy `argon-site-1gfltyfi1-tejdivs-projects.vercel.app` was aliased to `https://app.fourmanifold.com`.
- Browser verification opened `https://app.fourmanifold.com/new/latexflow` and showed the Takyon intake UI.

Still pending:
- Product/backend improvement lane for the generated Latexflow app remains separate from the fast cached website/product-preview deploy.
- Old CEO chat messages on the Latexflow dashboard still contain stale text from before the latest lead/website fixes; the current dashboard metrics and lanes reflect the fixed DB state.

## Platform UI/Operations Patch - 2026-05-20 PT

Verified:
- `npm run typecheck` passed.
- `npm run build` passed.
- Production deploy `argon-site-lr2tnjy29-tejdivs-projects.vercel.app` completed and was aliased to `https://app.fourmanifold.com`.
- `/new/latexflow` now uses the `Four Manifold` wordmark while preserving the same Latexflow template/intake behavior.
- X cards render as links when the social post row has a real `provider_url`; rows without provider URLs remain non-clickable.
- CEO chat pending state renders the operator message normally and shows a minimal `Thinking` indicator.
- Community is mapped as prepared community post copy rather than a duplicated Leads target list.
- Sora media rows for the two latest Latexflow companies are now `completed` with output URLs after sync.
- Dashboard rows that newly complete get a brief green completion glow, and lead/community rows use the clearer `emailed` chip where applicable.

Still pending:
- Browser playback of the Sora media proxy should be visually checked in the authenticated browser session.
- Existing stored CEO messages are not rewritten; stale Markdown/runtime wording remains only in historical inbox rows.
- The `observe_campaign_results` job is a visible TODO placeholder and does not yet fetch engagement metrics.

## Corrected Cached Latexflow Sora Delay - 2026-05-20 PT

Verified:
- `npm run typecheck` passed after changing the cached Latexflow Sora path.
- `npm run build` passed after changing the cached Latexflow Sora path.
- Production deploy `argon-site-2n19zqjh5-tejdivs-projects.vercel.app` completed and was aliased to `https://app.fourmanifold.com`.
- In-app browser verification opened `https://app.fourmanifold.com/new/latexflow` after the deploy and showed the Four Manifold cached Latexflow intake screen.
- The implemented path is for future cached Latexflow projects only: Build Company queues `meta_seedance` with a roughly 3 minute `run_after`, and the worker writes the company-owned cached Sora media row when that delayed job runs.

Still pending:
- Browser E2E of a fresh `/new/latexflow` company after deploy to prove the delayed row appears about 3 minutes later in the dashboard.

## Backend Preview And Cached Sora Patch - 2026-05-20 PT

Verified:
- Patched only backend/template/data paths while preserving active Claude frontend edits in `src/components/takyon/TakyonBusinessWorkspace.tsx` and `src/app/globals.css`.
- Cached Latexflow Sora row `2d49d8bf-5188-45c0-80cd-6862a1543cb1` now stores a row-scoped signed media URL.
- Local route verification against the stored signed URL returned `200` with `Content-Type: video/mp4`.
- Generated app template now emits frame-ancestor CSP headers allowing dashboard previews from `app.fourmanifold.com`.

Still pending:
- Production deploy/alias of this backend patch.
- Browser verification of the dashboard video modal after production deploy.

## Local Dashboard Style And Latexflow CEO Cache - 2026-05-20 PT

Verified:
- `npm run typecheck` passed after dashboard styling and CEO router changes.
- `npm run build` passed after dashboard styling and CEO router changes.
- The downloaded Takyon reference uses `Space Grotesk`; the local dashboard root now resolves to `"Space Grotesk", ui-sans-serif, system-ui, sans-serif`.
- In-app browser verification on `http://localhost:3000/dashboard/companies/19687d0b-e1d4-4e78-a45c-2d11aa2a2161` confirmed the performance chart has one unfilled SVG line with `stroke-width="2.25"`, the Meta generating card is white with a subtler spinner opacity, and the Reddit lane title renders as dark `Reddit` text with an orange icon badge.
- Direct CEO router invocation against Latexflow company `552d6401-632b-4f12-851c-dcf7127867ad` with `describe your current outreach campaign` returned the normal numbered campaign answer, queued no jobs, and did not expose the word `cached` in the operator-visible response.

Still pending:
- No production deploy/alias was run for this local styling/router patch.

## Latexflow 529 Retry Patch - 2026-05-20 PT

Verified:
- `npm run typecheck` passed after adding retryable worker error handling and the local dashboard UI patch.
- `latexflow-5` and `latexflow-6` foundation jobs were observed blocked on Anthropic `529 overloaded`; both were reset to `queued` with `attempts = 1 / max_attempts = 2`.
- The local dashboard UI patch centers the zero chart line, adds a bottom-left `+ Add member` team control, labels X posts as Four Manifold with a blue verification badge, and labels queued downstream workflow jobs as waiting on unmet dependencies instead of `due now`.

Still pending:
- Restart the local worker so it loads the retry patch.
- Push/deploy this patch after verification.
