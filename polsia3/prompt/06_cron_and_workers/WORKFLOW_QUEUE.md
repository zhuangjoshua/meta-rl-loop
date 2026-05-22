# Workflow Queue

Workflow jobs are durable DB rows.

Statuses:
- queued
- running
- completed
- blocked
- failed
- cancelled

Workers claim jobs with row locks and leases.

Every job records:
- workflow id
- company id
- task id
- payload
- attempts
- max attempts
- run_after
- locked_by
- locked_at
- result/error

Long jobs run in the local Mac worker for v0.

## Parallel Lane Rules

The queue must allow independent jobs for:
- website generation/deploy
- product backend/API implementation
- product UI implementation
- generated-app auth/session wiring
- generated-app users/entitlements
- Stripe checkout/webhooks
- AI gateway/project wallet setup
- X/social add-ons
- Meta/Sora creative
- community/lead/outreach add-ons

Jobs declare dependencies explicitly.

A blocked/failed product job must not block:
- healthy website deployment
- X/social jobs
- Meta/Sora creative jobs
- community/lead/outreach jobs
- auth/users/Stripe/AI setup jobs that can proceed independently
- any other job whose declared dependencies are still satisfied

A completed website job must not mark product/auth/Stripe/AI/add-on jobs complete.

A completed product job must not mark X/social/Meta/community/lead/outreach jobs complete.

## Verified Implementation Status - 2026-05-19

Implemented:
- `workflow_jobs` has `lane` and `dependencies`.
- Build Company queues independent jobs for website, product backend, product UI, generated-app auth/users, Stripe, AI gateway, X/social, Meta/Sora, community, and outreach.
- Dependency checks in `claimWorkflowJobs` require only declared dependencies to be completed.
- Generated-app source-dependent product lanes now declare `website_build_deploy` explicitly: `product_backend`, then `product_ui`.
- Generated-app auth/users, Stripe setup, and AI gateway setup depend on `foundation` only, so they do not wait for the website/product lane when their deterministic setup can run independently.
- Foundation-only add-on lanes remain independent of product readiness: `x_social`, `meta_seedance`, and `community_research`.
- `outreach_copy` depends on `community_research`, because it uses real community targets as input; it is still independent of website/product readiness.
- `enqueueWorkflowJob` now stores dependency arrays as explicit Postgres `text[]` literals after verification exposed that the previous helper call could insert malformed arrays.
- Local worker claim uses row locking with `FOR UPDATE SKIP LOCKED`.
- Foundation lane was claimed and completed in smoke and browser-created E2E runs.
- Local worker can be targeted with `WORKER_BUSINESS_ID` for a browser-created company.
- Worker completion now syncs the parent task status from the real child workflow statuses so the UI does not leave a completed/failed build stuck as `queued`.
- Worker task sync uses `blocked` for mixed child-lane outcomes after all lanes leave the queue, so a healthy website plus isolated failed/blocked add-ons does not make the whole company look dead.
- Takyon dashboard in-progress lanes use the latest workflow job per `workflow_id`, so stale blocked/failed retries do not keep appearing after a newer lane run supersedes them.
- The right-side CEO chat router can enqueue bounded workflow jobs from operator requests: `website_build_deploy`, `x_social`, `meta_seedance`, `community_research`, `outreach_copy`, and `ceo_wakeup`.
- The CEO chat router now supports `/goal get_first_customer`. The command creates an active `business_campaigns` row with `kind = goal`, creates a visible `goal` task, and queues `goal_get_first_customer` in the new `goal` lane.
- `goal_get_first_customer` is a persistent orchestrator tick, not a side-effect executor. Each tick reads product, checkout, revenue, leads, community targets, social posts, workflow jobs, and capabilities; writes `goals/get_first_customer` memory plus a `Get First Customer Goal` task report; queues the next bounded workflow actions; and schedules another goal tick until a positive Stripe-backed `company_revenue_events` receipt exists or a real missing capability blocks the goal.
- Follow-up `goal_get_first_customer` ticks keep the original visible `goal` task id, so the dashboard task stays active across iterations instead of completing after the first planning pass.
- The first supported goal success condition is strict: one positive paid/completed/succeeded `company_revenue_events` row. Checkout visits, generated users, draft posts, lead candidates, and strategy text do not count as a paying customer.
- The goal loop uses the `argon-company-factory/get-first-customer` skill for strategy shaping, but deterministic Takyon workflow jobs still own deployment, checkout, research, X, outreach assets, and receipts. Hermes/LLM output can recommend next actions; it cannot claim external side effects.
- Chat-enqueued website improvement jobs carry `payload.operator_instruction`; the local worker forwards that instruction into the Claude Agent SDK surface builder.
- Worker claim now joins `businesses` and only claims queued jobs for `active` businesses.
- Company shutdown cancels queued/running workflow jobs, tasks, agent runs, and generated-app build/deployment rows; it also archives the business and marks its site offline.
- `completeWorkflowJob` and `finishAgentRun` do not overwrite rows already marked `cancelled`, so a late-returning worker cannot silently resurrect a cancelled job/run.
- Local worker startup now runs stale-lock recovery before claiming jobs.
- `npm run worker:local` starts the v0 always-on local worker loop.
- `npm run worker:recover` forces stale `running` workflow jobs back to `queued` or `failed` if max attempts were reached.
- `npm run worker:recover` was verified on the fresh Latexflow E2E company after an interrupted website lane; it recovered one stuck `website_build_deploy` job from `running` back to `queued`.
- `npm run worker:local` claims up to 6 jobs per loop by default. This restores the faster v0 local runner after stale Latexflow workflow rows were cleaned; dependency checks still run before claim, so the higher claim limit does not override lane dependencies.
- Community research now creates real `leads` rows with `status = candidate`, nullable email, and source URL. This makes lead discovery automatic instead of only visually inferred from community targets.
- CEO chat actions now create a visible parent task and attach the queued workflow jobs to it. New operator-requested work should appear in the normal In progress panel.
- Dashboard workflow job rows now carry future `run_after` values into the UI, so future queued work can show labels such as `queued in 3m` instead of only `queued`.
- The company `In progress` panel also includes the global `ceo_wakeup` cron schedule as a `Next CEO Wakeup` row, so the operator can see when the CEO is expected to wake again.
- `meta_seedance` now distinguishes submit from render readiness. If OpenAI Sora returns a submitted/processing job without output, the worker queues a follow-up `meta_seedance` sync job instead of treating the playable media as ready.
- After real X publishing or completed Sora media sync, the worker can queue an `observe_campaign_results` job in the CEO lane. This is intentionally a visible v0 placeholder for the later engagement-learning loop, not a completed analytics system.
- Future cached Latexflow builds (`template = latexflow-v1`) queue `meta_seedance` with `run_after` about 3 minutes after the company build plan is created. The media row is written by the delayed worker job, not immediately at company creation.
- The delayed cached Latexflow Sora job carries `payload.use_cached_latexflow_sora = true` and writes a company-owned cached Sora media row from a previously completed Latexflow source. It must not mutate old company rows or pretend a Meta campaign was launched.
- Retryable vendor/runtime overloads now requeue instead of permanently freezing the company while attempts remain. The worker treats transient HTTP-style failures including 408, 409, 425, 429, 500, 502, 503, 504, 529, and `overloaded` messages as retryable.
- The local foundation lane uses the original provider path again: Anthropic is used when `ANTHROPIC_API_KEY` is configured, otherwise OpenAI is used only when no Anthropic key exists. The operator kept the rotated Anthropic key and restored the Anthropic foundation path after briefly testing direct OpenAI foundation generation.
- Cached Latexflow builds (`template = latexflow-v1` or `latexflow%` company slugs) now try a verified-doc foundation cache before any model call. If a prior Latexflow business has real agent Mission and Market Research documents generated by the foundation lane, the new company copies those documents with `provider = cached-foundation`; if no verified source exists, the normal foundation provider path runs.
- Cleanup receipt: the operator asked to clear former Latexflow process rows before a fresh E2E. On 2026-05-20, stale Latexflow process rows were deleted without deleting companies, documents, deployments, leads, social posts, or media rows: 171 `workflow_jobs`, 65 `agent_runs`, 51 `agent_run_steps`, and 49 `tasks`. Follow-up count verified 0 remaining Latexflow workflow jobs, agent runs, or tasks, while 22 cached-source foundation documents remained available.

Browser E2E receipt:
- Company `bdffff4e-074f-4d3a-ab67-e924e19b9797`.
- Completed lanes: `foundation`, `website_build_deploy`, `product_backend`, `generated_app_users_entitlements`, `stripe_setup`, `ai_gateway_setup`, `community_research`, `outreach_copy`.
- Original blocked lanes: `generated_app_auth` because magic-link/session routes were not implemented; `x_social` because the X daily platform limit was reached.
- Original failed lanes: `product_ui` because Claude reached maximum turns; `meta_seedance` because `ATLAS_API_KEY` was not configured.
- Targeted fix worker pass after the audit completed `generated_app_auth`.
- Targeted X pass created a real `business_social_posts` row with status `ready`, then blocked publish with `X daily platform limit reached: 9/5`.
- Targeted media pass completed a real OpenAI Sora job and saved a `media_generation_jobs` row with `provider = openai`, `model = sora-2`, `status = completed`, a real provider job id, and proxied output URL.
- Later targeted X pass completed with real X provider post id `2056971073997156497` after the platform daily limit was made configurable and set high enough for v0 testing.
- Later generated-app surface work moved `website_build_deploy` and `product_ui` off the local OpenLovable adapter and onto a direct Claude Agent SDK surface builder.
- The content/truthfulness validator is disabled for now by operator request after a false positive on honest copy, including in the inactive OpenLovable adapter. Build, deploy, alias, health, and browser route checks remain active.
- Existing generated workspace for company `bdffff4e-074f-4d3a-ab67-e924e19b9797` deployed successfully after this change: build id `8022bdf8-bd2f-46f4-8307-1b2781c6b281`, alias `https://signalbridge-browser-e2e-20260519.fourmanifold.com`, health `200`.
- In-app browser verification of local company `19687d0b-e1d4-4e78-a45c-2d11aa2a2161` showed X, Sora creative, Leads, and Community as automatic operating lanes with small refresh controls instead of duplicated primary buttons.
- Direct CEO chat router verification against that same company queued `community_research`, `outreach_copy`, and `ceo_wakeup`; browser reload showed the persisted operator message, CEO answer, and daily report.

Still pending:
- Replacement content/visual quality gate for generated-app surfaces.
- Manual browser playback of the authenticated Sora content route after each deploy.
- Stale lock recovery pass beyond the current claim predicate.
- Full typed-form chat submit via in-app browser automation; browser typing was flaky, so persisted output was browser-verified after direct router invocation.
- The `observe_campaign_results` workflow is queued as a future/placeholder task only. It does not yet fetch X metrics, read ad/video engagement, compare variants, or decide the next campaign from evidence.

## Verified Implementation Status - 2026-05-20

Latexflow company `648df60b-4588-47b6-85f4-89f620063bea`:
- Existing community targets were backfilled into real candidate lead rows: 6 targets -> 6 `candidate` leads.
- Production browser verification on `app.fourmanifold.com` showed the dashboard metrics as `6 leads` and the Leads lane as `6 candidates` with real source URLs.
- The dashboard Leads lane now reads stored `leads` rows only; it no longer treats community targets as visual-only lead fallbacks.
- `outreach_copy` is now ordered after `community_research` for new companies, so outreach copy does not race ahead of the evidence it needs.
- `npm run typecheck`, `npm run build`, generated cached-surface typecheck, and production deploy/alias completed.

Latest platform deploy verification - 2026-05-20:
- Production deploy `argon-site-lr2tnjy29-tejdivs-projects.vercel.app` was aliased to `https://app.fourmanifold.com`.
- `npm run typecheck` passed.
- `npm run build` passed.
- Sora media rows for the two latest Latexflow companies were synced after the initial submitted state; both now show `status = completed` with provider job ids and proxied output URLs.
- Corrected cached Latexflow Sora behavior was deployed in production deploy `argon-site-2n19zqjh5-tejdivs-projects.vercel.app` and aliased to `https://app.fourmanifold.com`.
- Fresh Latexflow runs `latexflow-5` and `latexflow-6` were blocked by Anthropic `529 overloaded` in the `foundation` lane. Those foundation jobs were reset to `queued` with their original error preserved and `attempts = 1 / max_attempts = 2`; the worker must be restarted after this code patch so future retryable overloads use the new requeue path.
