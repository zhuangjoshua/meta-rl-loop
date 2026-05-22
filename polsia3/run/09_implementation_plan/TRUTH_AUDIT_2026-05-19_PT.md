# Truth Audit - 2026-05-19 PT

## Trigger

The browser E2E for `SignalBridge E2E 20260519` showed a visible `Market Research` document containing queued/blocker placeholder text. That violated the no-stubs/no-fake-finished-output rule.

## Verified E2E Reality

Company:
- `f46a2969-4d94-46f8-8c25-e48a89c980f4`
- `SignalBridge E2E 20260519`

Database state:
- `workflow_jobs`: 12 rows, all `queued`
- `agent_runs`: 0 rows
- `tasks`: 1 queued build task
- `business_documents`: 2 rows, both seeded placeholders

Document state:
- `Mission`: `source = system`, `metadata.seeded = true`
- `Market Research`: `source = system`, `metadata.seeded = true`, `metadata.status = blocked_until_research_runs`

This means the E2E verified auth/route/UI/form/database queue wiring only. It did not verify that the company was built.

## Hermes Truth

V2 did use Hermes/Argon for non-deterministic skill workflows:
- CEO/planning
- business planning
- market research
- social/content/support/outreach copy
- lead finding
- activity review

V2 included:
- `vendor/argon-hermes-runtime`
- `.argon-hermes-home`
- `scripts/setup-argon-hermes-runtime.sh`
- `scripts/start-argon-hermes-runtime.sh`
- `scripts/sync-argon-hermes-skills.mjs`
- `src/lib/vendors/argon-runtime.ts`
- runtime session/reconciler logic

Current v3 reality:
- `vendor/argon-hermes-runtime`, v2 setup/start/sync scripts, `skills/argon-company-factory`, and `src/lib/vendors/argon-runtime.ts` have been copied into v3.
- `src/lib/hermes.ts` now uses the copied Argon runtime adapter for `/v1/runs`.
- Hermes-backed gateway execution has not been verified because `ARGON_RUNTIME_URL` is not explicitly configured/running for the worker.
- The browser E2E foundation lane transparently recorded Hermes as skipped and used the verified local-foundation provider path.
- `src/lib/ceo.ts` falls back to direct LLM execution when Hermes is not configured, which is not the same as preserving the v2 Hermes runtime.

Any previous statement that the Hermes code was already copied into v3 was overstated and wrong.

Update after exact-v2-UI/browser-worker slice:
- Exact v2 `TakyonOnboarding` was ported and deployed.
- Browser E2E company `bdffff4e-074f-4d3a-ab67-e924e19b9797` progressed beyond queued jobs.
- Real Mission/Market Research docs were created with `source = agent`, `metadata.seeded = false`, provider `local-foundation`, and `evidence_count = 12`.
- Website deploy/alias/health completed at `https://signalbridge-browser-e2e-20260519.fourmanifold.com`.
- Product UI remains not complete for the reasons recorded in `SMOKE_TESTS.md`.
- Generated-app auth is now partially complete: platform auth request/verify/session routes exist and the targeted worker pass completed the auth lane.
- X/social is partially complete: a real visible `ready` X row is created before publish gating; publish remains blocked by the daily platform limit.
- Meta/Sora display-only generation is complete for the targeted run: a real OpenAI Sora job completed, the provider receipt was saved, and a proxied output URL was stored. No Meta upload/campaign/spend occurred.

## Current False-Finished Surface

Visible seeded Mission/Market Research docs make the product look like it produced real business outputs when it did not.

Required fix:
- Do not show seeded placeholder docs as normal deliverables.
- Either hide them from the finished document surface until replaced, or replace them immediately with real workflow output before rendering as documents.
- A job can be `queued`, `running`, `blocked`, or `failed`, but it must not look completed without a real receipt.

Current fix status:
- Implemented for the latest browser E2E: seeded placeholder docs are filtered from the visible document model and real foundation output replaces them when the worker runs.
- Parent build task status is now synced from workflow job outcomes so it no longer remains stale `queued` after child lanes finish or fail.

## Not Complete Yet

- Verified local Hermes gateway run.
- Product UI generation from the Build Company flow.
- Generated app template UI consumption of the implemented magic-link/session auth routes.
- Stripe webhook entitlement E2E.
- X publish with a real receipt after rate-limit allows it.
- Sora display lane output URL after OpenAI completes the submitted render.
- CEO daily report reasoning through Hermes or an explicitly verified provider path.
- AI gateway provider execution with reservation/accounting beyond blocked attempts.

## Next Implementation Priority

1. Remove or quarantine visible seeded docs so the UI does not imply real output.
2. Port v2 Hermes runtime/scripts/session wrapper into v3 and verify a local `/v1/runs` receipt.
3. Implement the initial foundation worker lane to run real planning/research before creating Mission/Market Research docs.
4. Re-run browser E2E and keep the company visible while verifying jobs progress beyond `queued`.
5. Only then continue website/product/Stripe/X/Meta/community/outreach execution slices.
