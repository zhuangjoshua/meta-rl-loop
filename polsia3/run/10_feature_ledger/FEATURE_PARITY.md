# Feature Ledger

This file prevents accidental feature loss. A rebuild task is not complete if it removes a listed feature without an explicit replacement or documented cut.

Status labels:
- `verified`: implemented and checked.
- `partial`: some implementation exists, but required gates remain.
- `planned`: preserved requirement, not implemented yet.
- `blocked`: waiting on config/secret/vendor/operator/policy.
- `forbidden`: intentionally disallowed in v0.

Primary source material is preserved in [MANIFEST.md](../00_source_material/polsia_v1/MANIFEST.md).

## Platform Core

- `verified`: Takyon onboarding prompt for new company creation.
- `verified`: Exact v2 `TakyonOnboarding` UI exists at `/new/takyon` and renders as the empty `/dashboard/takyon` state.
- `verified`: Takyon company dashboard.
- `verified`: Company/workspace records.
- `verified`: Memberships and roles.
- `verified`: Tasks with status, priority, category, routing, and blockers.
- `verified`: Events/activity feed.
- `partial`: Business documents/reports. Latest browser E2E produced real Mission/Market Research docs plus Community Launch Targets and Outreach Assets. Daily reports wait on Hermes/CEO runner.
- `partial`: Operator chat/inbox with CEO response path. Inbox persistence is verified; CEO response generation waits on Hermes/CEO runner.
- `partial`: V2-style persisted inbox/chat, mission, research reports, daily reports, task reports, and document library. See [CHAT_DOCUMENTS_REPORTS_PARITY.md](../03_trunk/CHAT_DOCUMENTS_REPORTS_PARITY.md).
- `verified`: Prompt registry and operator-editable prompt text.
- `verified`: Workflow queue with durable jobs.
- `verified`: Agent run and step logs.
- `partial`: Persistent `/goal` capability. `/goal get_first_customer` creates a durable goal campaign/task, queues repeatable `goal_get_first_customer` ticks, records strategy in memory/documents, and keeps queuing bounded product/checkout/targeting/distribution work until a verified Stripe revenue event or a real capability blocker. Generic arbitrary goals and paid-customer E2E proof remain pending.
- `partial`: Approvals/action policy. Tables/default policies exist; UI/toggles and all vendor enforcement paths are pending.
- `verified`: Configurable cron table and dispatch endpoint.
- `planned`: Integration health/config state UI.

## Generated Apps

- `verified`: Generated app source saved for local smoke build under `.takyon/generated/{companyId}`.
- `verified`: Build logs saved in `generated_app_builds` and `generated_app_build_steps`.
- `verified`: Typecheck/build required by generated-app local build gate.
- `verified`: Deployment URL saved only after health check. Browser E2E saved completed deployment only after health `200`.
- `verified`: `*.fourmanifold` public URL target. Browser E2E alias `https://signalbridge-browser-e2e-20260519.fourmanifold.com` returned `200`.
- `planned`: Platform proxy for generated app traffic.
- `partial`: Magic-link generated-app user auth. Platform request/verify routes are implemented; generated product template integration remains pending.
- `partial`: Generated-app sessions. Platform session route is implemented; generated product UI consumption remains pending.
- `partial`: Generated-app entitlements. Runtime product run creates/checks free entitlements using the v2-compatible table shape; paid/Stripe entitlements are pending.
- `partial`: Stripe checkout for generated app customers. Payment link setup completed in browser E2E; checkout route/webhook E2E remains pending.
- `planned`: Stripe webhook entitlement updates.
- `verified`: Generated-app plan policies created with existing v2 schema constraints.
- `verified`: Project AI wallet created with existing v2 schema constraints.
- `verified`: Project AI proxy keys generated and hashed; raw key is only passed to generated app build/deploy context.
- `partial`: Project AI usage reservations/events. Blocked AI attempts are recorded; provider execution/reservation accounting is pending.
- `planned`: Paid users reserve budget first.
- `planned`: Free/anonymous users use leftover budget only.
- `verified`: Generated apps never receive raw provider keys in the implemented template path.

## Agents And Workflows

- `partial`: CEO planning/wakeup. Queue/daily-report blocked path exists; Hermes/LLM reasoning is pending.
- `partial`: Goal-driven autonomy. The `argon-company-factory/get-first-customer` skill is wired for strategy shaping and the deterministic worker owns the `goal_get_first_customer` loop. Success is strict positive `company_revenue_events` revenue; draft posts, leads, generated users, and checkout attempts do not count. The loop still needs real Stripe checkout/webhook E2E and more direct prospect-person enrichment before it can honestly claim end-to-end first-customer acquisition.
- `partial`: Initial foundation/company planning. Foundation job can create generated-app economics, but the browser E2E company still had the foundation job queued and visible seeded docs. Full research/planning agent is pending.
- `verified`: Market research foundation report generated in browser E2E with 12 cited evidence items.
- `partial`: Generated app engineering/build. Website and product backend completed in browser E2E; product UI failed at Claude Agent SDK maximum turns.
- `planned`: Generated app improvement.
- `partial`: Social/X copy and posting. Targeted verification creates a visible `ready` X post row before publish gating; publish still waits on rate-limit availability and real X receipt.
- `verified`: Community discovery and launch copy completed for browser E2E with real targets/copy and no posting.
- `partial`: Lead finding. Community targets/leads appear in browser E2E, but dedicated email/contact enrichment remains pending.
- `verified`: Outreach copy completed for browser E2E with real assets and no fake sends.
- `planned`: Cold outreach policy/rate limits.
- `planned`: Support reply.
- `planned`: Content generation.
- `planned`: Activity review.
- `planned`: Data/operations report.
- `planned`: Operations monitor.
- `verified`: Meta/Sora display-only creative generation. Targeted verification completed a real OpenAI Sora job, saved the provider job id, and stored a proxied media output URL; no Meta upload/campaign/spend occurs in v0.
- `partial`: Stripe revenue setup and sync. Payment link setup completed; webhook revenue sync E2E remains pending.

## Vendor/External Add-Ons

- `verified`: Auth0 auth configuration and local-dev auth bypass.
- `verified`: Postgres/Supabase connection and migrations.
- `partial`: Stripe keys verified/copied; checkout/webhook implementation pending.
- `verified`: Postmark key copied/connection previously verified; transactional workflows pending.
- `partial`: X OAuth DB token verified; publish path pending and must require a real X receipt.
- `verified`: Tavily key copied/connection previously verified; research workflow pending.
- `planned`: Hunter optional email discovery.
- `planned`: Outbound email optional Smartlead/Instantly.
- `verified`: Sora creative generation. Local worker completed a real OpenAI `sora-2` job and saved a proxied output URL; no Meta upload/campaign/spend is allowed in v0.
- `verified`: Vercel deploy/alias. Browser E2E generated-app deploy/alias/health completed.
- `partial`: Hermes/Argon scoped local runtime adapter for the workflow categories v2 used. V2 vendored runtime/scripts/skill sync and adapter are copied; the local gateway run is not verified.

## Polsia v1 Parity Rules

- `partial`: Chat/orchestrator creates tasks and routes work.
- `partial`: Dispatch gates by capability, budget, policy, approvals, and credentials.
- `partial`: Agents coordinate through reports, documents, tasks, and database state.
- `planned`: Browser site tiers are enforced.
- `planned`: Cold outreach limits are enforced in DB.
- `partial`: Failures are recorded for implemented build/worker paths, but visible seeded documents currently create a fake-finished impression and must be removed or replaced by real generated reports.
- `partial`: Reports are required when a workflow's deliverable is a report.
