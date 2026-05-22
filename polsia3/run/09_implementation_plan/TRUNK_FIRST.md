# Trunk First Implementation Plan

Legend:
- `verified`: implemented and checked.
- `partial`: some implementation exists, but required gates remain.
- `planned`: not implemented yet.
- `blocked`: implemented path cannot complete without a real prerequisite.

1. `verified`: Create Next.js app foundation with Next.js 16.2.6.
2. `verified`: Add `AGENTS.md` and `run/` rules.
3. `verified`: Add migrations and DB client. Node migration runner, `postgres` DB client, migrations `0001_v3_trunk.sql` and `0002_generated_app_runtime.sql`.
4. `verified`: Add auth/profile/company/membership. Auth0 client path plus local-dev bypass, profile upsert, company/membership creation using v2-compatible `businesses` tables.
5. `verified`: Add Takyon company creation and dashboard shell. `/dashboard`, `POST /api/companies`, company workspace page.
6. `partial`: Add tasks/events/documents, including v2-style inbox/chat, mission, research reports, daily reports, task reports, and document library. Browser E2E produced real mission/research docs; inbox/document APIs exist; daily report reasoning waits on Hermes/LLM runner.
7. `verified`: Add workflow jobs and agent runs. Durable jobs, explicit dependencies, agent runs/steps.
8. `verified`: Add prompt registry. Required prompt seeds are inserted by migration script.
9. `verified`: Add cron dispatcher. `GET /api/cron/dispatch` with `CRON_SECRET`; DB-configurable `cron_jobs`.
10. `verified`: Add local worker. Local worker can target a browser-created company and advance all queued jobs to completed/blocked/failed receipts.
11. `partial`: Add AI gateway/project wallet. Project wallet/proxy key/model policy are created; AI gateway route records blocked attempts until provider execution is implemented.
12. `partial`: Add generated app template/build/deploy. Browser E2E verified generated app deploy/alias/health and product backend; product UI failed at Claude maximum turns.
13. `partial`: Add Stripe generated-app checkout/entitlements. Payment link setup completed; webhook/session E2E remains pending.
14. `partial`: Add X/Meta/community/outreach add-ons in priority order. Community/outreach completed; X creates a ready row but publish is blocked by daily limit; Meta/Sora completed a real OpenAI video job and saved a proxied output URL.

## Verification Snapshot - 2026-05-19

- `npm run check:env`: pass.
- `npm run typecheck`: pass.
- `npm run migrate`: pass.
- `npm run smoke`: pass.
- `npm run build`: pass.
- Browser check of `/dashboard`: pass.
- Browser Build Company E2E: pass for visible v2 onboarding prompt, company creation, workspace route, local worker execution, real docs, website deploy/alias/health, product backend, Stripe payment link setup, AI gateway setup, community targets, and outreach assets.
- Browser Build Company E2E initially recorded blockers for product UI max turns, generated-app auth route absence, X daily limit, and missing Atlas key.
- Targeted fix verification: generated-app auth route absence is fixed; X now creates a ready post row before publish gating; media now uses Sora/OpenAI instead of Atlas and completed a real provider job with a proxied output URL. Product UI max turns and X daily limit remain current blockers.
