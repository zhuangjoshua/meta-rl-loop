# Acceptance Checklist

Before calling the rebuild usable:

Legend:
- `verified`: done and checked.
- `partial`: implemented only partly; do not count as complete.
- `planned`: required but not implemented yet.
- `blocked`: waiting on real prerequisite.

- `partial`: Feature ledger has no unhandled items. All known items are preserved, but many are still `planned`.
- `verified`: `AGENTS.md` rules are followed for prompt restatement, run updates, and sync direction.
- `partial`: `run/` docs match code. Current docs now distinguish aspirational vs complete status.
- `verified`: Private GitHub repo is connected. `tejdiv/polsia3` exists and is private.
- `verified`: Correct Vercel project is connected. Local project is linked to `argon-site`.
- `partial`: Required secrets are copied or blocked explicitly. Local Platform V0/generated-app commerce secrets are present; optional vendor add-on secrets remain config-required.
- `verified`: `CRON_SECRET` exists locally and in Vercel production, preview, and development.
- `partial`: Connection audit blockers are resolved or intentionally deferred. See [CONNECTION_AUDIT_2026-05-19.md](../08_secrets/CONNECTION_AUDIT_2026-05-19.md).
- `verified`: Blocker fixes are tracked in [BLOCKER_FIX_RUNBOOK.md](../08_secrets/BLOCKER_FIX_RUNBOOK.md).
- `verified`: Cron dispatch is DB-configurable.
- `partial`: Local worker can claim and run a browser-created company through all queued lanes. The latest E2E verified real foundation docs, website deploy, product backend, Stripe setup, AI gateway setup, community, and outreach. Targeted fix verification completed generated-app auth, created a ready X post row, and completed a Sora media job. Product UI and X publish receipt remain pending/blocked.
- `partial`: Workflow queue creates independent website/product/auth/Stripe/AI/X/add-on lanes with explicit dependencies and the latest browser E2E verified execution, not only queue creation. Failed/blocked lanes stayed isolated from the healthy website deployment.
- `partial`: V2-style Takyon operator UI, exact v2 onboarding, inbox/chat, mission, research reports, task reports, and document library are present. Latest browser E2E verified real Mission/Market Research docs; CEO daily report reasoning still waits on Hermes/LLM runner.
- `partial`: `/goal get_first_customer` exists as a persistent goal loop. `npm run typecheck` and `npm run build` verification show the slash command can create a durable goal campaign/task and queue `goal_get_first_customer` worker ticks, but browser typed-form verification and real paid-customer Stripe webhook E2E remain pending.
- `verified`: Generated app template builds locally.
- `verified`: Generated app deployment health check gates URL save. Browser E2E deployed `https://signalbridge-browser-e2e-20260519.fourmanifold.com` and saved it only after health `200`.
- `partial`: Generated app auth/payments/AI limits work. Platform magic-link/session routes, free user entitlement, and product-run persistence exist; generated template auth UI, paid entitlements, Stripe webhooks, and provider metering are pending.
- `partial`: Generated app product backend gates pass or are explicitly blocked. Browser E2E product backend completed and `/product` returned `200`; product UI failed at Claude Agent SDK maximum turns and is not complete.
- `verified`: Website readiness and product readiness are tracked separately in workflow lanes/status.
- `verified`: Business marketing skills are wired as Takyon-owned no-side-effect workflows. They write inspectable business documents, business memory, and workspace files; CEO chat, daily CEO wakeup, goal ticks, worker dispatch, and optional Hermes/Argon skill sync can see them. `npm run typecheck`, `npm run build`, and skill sync passed on 2026-05-22.
- `partial`: X can publish automatically only when configured/rate-limited and a real X receipt is returned. Targeted verification now creates a real visible `ready` post row before publish gating; publish remained blocked by daily platform limit.
- `verified`: Meta v0 only generates/displays Sora creative. Targeted verification completed a real Sora job and saved a proxied output URL; no Meta posting/spend occurs.
- `partial`: Community UI shows real targets/copy and does not post. Records are separated from leads/outreach/X, but task volume/polish still needs another pass.
- `partial`: Fake-success prevention improved. Seeded placeholder documents are no longer shown as finished deliverables and the latest browser E2E showed real foundation docs. X publish blocked honestly after creating a ready post row; Sora shows real completed media metadata without Meta fake-success.
