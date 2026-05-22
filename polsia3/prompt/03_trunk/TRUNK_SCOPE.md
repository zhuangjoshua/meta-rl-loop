# Trunk Scope

The trunk is the stable core. It should stay boring.

The trunk owns:
- auth
- profiles
- companies
- memberships
- dashboard shell
- tasks
- workflow jobs
- agent runs
- run steps
- events
- documents/reports
- approvals/action policies
- prompt registry
- cron scheduler
- add-on registry
- AI gateway
- project AI wallets
- generated-app economics tables
- audit logs

The trunk should not know vendor implementation details beyond add-on interfaces.

## Verified Implementation Status - 2026-05-19 PT

Implemented and verified:
- `/dashboard` redirects into the v2-style Takyon operator surface at `/dashboard/takyon`.
- `/dashboard/takyon` renders the exact v2 Takyon onboarding prompt when the current profile has no companies, and the exact v2 Takyon company index/intake UI when companies exist.
- `/new/takyon` renders the exact v2 `TakyonOnboarding` prompt UI and posts to the v3 Build Company route.
- `/dashboard/companies/[companyId]` renders the v2 Takyon workspace board/chat UI backed by v3 documents, tasks, workflow jobs, events, payments, social/community/media rows, and team membership.
- Browser E2E created `SignalBridge E2E 20260519` through the visible Takyon intake and landed on its workspace.
- Browser E2E created `SignalBridge Browser E2E 20260519` through the visible in-app browser prompt on `app.fourmanifold.com/new/takyon` after the exact v2 onboarding UI was deployed.

Verification receipt:
- Company id: `f46a2969-4d94-46f8-8c25-e48a89c980f4`
- E2E result: 12 queued workflow jobs, 2 seeded placeholder documents, 1 build task, and no `agent_runs`.
- Visible workspace showed Performance, website/product preview tiles, queued task, seeded Mission/Market Research docs, growth lanes, team, and CEO chat rail.
- New company id: `bdffff4e-074f-4d3a-ab67-e924e19b9797`
- New browser E2E result: foundation completed with real `agent` Mission/Market Research docs and 12 evidence items; website deployed and health-checked at `https://signalbridge-browser-e2e-20260519.fourmanifold.com`; product backend completed; generated-app users/entitlements, Stripe setup, AI gateway setup, community research, and outreach copy completed.
- Honest non-completions: product UI failed with `Claude Code returned an error result: Reached maximum number of turns (12)`; X publish remains blocked because the daily platform limit was reached.
- Fixed after audit: generated-app auth/session routes are implemented and the targeted worker pass completed `generated_app_auth`; X now creates a real `business_social_posts` row with status `ready` before publish gating; the media lane now submits OpenAI Sora instead of Atlas/Seedance and saved a real `video_...` provider job id.
- The visible build task now syncs from workflow job outcomes and uses `blocked` for mixed completed/blocked/failed child lanes instead of making the whole company look dead when independent lanes fail.
- The Takyon workspace dashboard now presents X, Sora creative, Leads, and Community as automatic operating lanes with small refresh controls rather than duplicate primary buttons.
- The CEO chat rail now answers immediately from scoped business state and can enqueue bounded workflow jobs; it does not directly perform vendor side effects or unrestricted code edits.
- App shutdown exists for a company: archive business, mark site offline, cancel queued/running jobs/tasks/agent runs/build/deployment rows, and attempt Vercel alias/deployment removal.
- Dashboard preview tiles now remain in their normal v2 board positions and expose explicit `Open website` and `Open product` controls. Browser verification on `app.fourmanifold.com` confirmed both tile anchors point to the generated app URLs and the website tile opens in the current browser tab.

Important correction:
- This E2E verified route/auth/UI/database wiring only.
- It did not verify the autonomous build actually ran.
- The visible Mission/Market Research documents were `source: system` with `metadata.seeded: true`; they are not real company outputs and must be removed from the "completed-looking" document surface or replaced by real workflow results before this flow can be called usable.
- Superseding correction: the newer `SignalBridge Browser E2E 20260519` run did verify the local worker advancing the browser-created company beyond queued state, and seeded placeholder documents are filtered/replaced by real foundation documents. The remaining failed/blocked lanes above are still not complete and must not be claimed as complete.
- 2026-05-20 local verification: `npm run typecheck` and `npm run build` passed after chat/router/shutdown/dashboard-lane changes. In-app browser verified the new lane labels and persisted CEO chat output on local company `19687d0b-e1d4-4e78-a45c-2d11aa2a2161`.
