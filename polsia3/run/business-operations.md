# Business Operations

## Web Dashboard Removed

Implemented behavior:
- The Next.js `/dashboard` route tree has been removed.
- The dashboard-only company list, company workspace, live refresher, dashboard actions, dashboard CEO chat router, and dashboard inbox API route have been removed.
- `/` no longer redirects into `/dashboard`; it shows a terminal-first operator page with Takyon shell commands.
- Web onboarding no longer redirects to a dashboard after queuing a company build; it returns to `/` with the queued business slug.
- X OAuth fallback return paths now point to `/`, not `/dashboard`.
- Auth proxy canonicalization no longer treats `/dashboard` as an operator route.
- Terminal and queued CEO remain the canonical operator surfaces.

Acceptance checks:
- `npm run typecheck` passes.
- `find src/app/dashboard -maxdepth 4 -type f` returns no route files.
- `rg "dashboard" src scripts AGENTS.md run/business-operations.md` has no live dashboard routes or imports; remaining matches are the terminal-root notice and this runbook entry.

## Per-Business Pause And Resume

Implemented behavior:
- Business operations can be paused or resumed from the Takyon shell.
- Pause updates only the selected business to `businesses.status = 'paused'`.
- Pause sets the business-scoped Takyon control state to `paused`.
- Pause updates only `cron_jobs` rows where `metadata->>'business_id'` matches the selected business.
- Resume updates the selected business to `businesses.status = 'active'`.
- Resume sets the business-scoped Takyon control state to `active`.
- Resume ensures that the selected business's CEO, conversation watch, and customer ops cron rows exist, then reactivates only that business's cron rows.

Cron reconciliation behavior:
- Active businesses continue to have per-business cron rows reconciled.
- Paused businesses keep their cron rows but reconciliation does not reactivate them.
- Archived or missing businesses have their per-business cron rows removed.
- The shared cron dispatcher remains shared infrastructure, but claimed work remains business-scoped.

Acceptance checks:
- `npm run typecheck` passes.

Known limits:
- This does not cancel already-running jobs.
- This does not expose arbitrary cron editing; it exposes per-business pause/resume only.

## Per-Business Test Mode

Implemented behavior:
- `businesses.mode` is the canonical source of truth for live/test behavior.
- Mode values are `live` and `test`; new businesses default to `live`.
- The Takyon shell can read or switch the current business with `/test on|off|status` after `/use <business-id-or-slug>`.
- The non-interactive CLI can read or switch a business with `./takyon test <business-id-or-slug> on|off|status`.
- Test mode is per business only.
- Test mode does not pause the business and does not change other businesses.
- Test mode keeps per-business cron reconciliation, conversation watch, customer ops watch, and campaign observation behavior on the same schedule as live mode.
- Test-mode X distribution records a ready `business_social_posts` receipt with `business_mode = 'test'` and `external_side_effects = 'suppressed'`.
- Test-mode X distribution does not call the X API and does not require X posting credentials.
- Test-mode X distribution can be queued by terminal/autopilot, worker, or first-customer goal even when X posting credentials are missing.
- Model-backed business skills still require their configured model API key. If the key is missing, capability preflight blocks the workflow instead of creating a fake skill report.

Acceptance checks:
- `npm run typecheck` passes.

Known limits:
- Test mode currently suppresses the implemented external outreach posting path: X publishing.
- Community and outreach lanes were already no-post/no-send draft or target workflows.
- Future outbound adapters must check `businesses.mode` before creating external side effects.

## Business Workspace Filesystem Visibility

Implemented behavior:
- The per-business workspace filesystem under `.takyon/businesses/<business-slug>/` is treated as a first-class business artifact.
- Non-dashboard CEO surfaces are the canonical operator surfaces for this check: queued CEO wakeups and terminal chat/shell.
- Queued CEO context includes a cheap top-level workspace map with file counts, bytes, timestamps, and example paths.
- Queued CEO context still includes boot-file excerpts first, then a bounded path list; if that path list is truncated, the prompt says exactly how many files are omitted and instructs the CEO/runtime to inspect deeper before treating paths as irrelevant.
- Terminal CEO chat receives the business workspace map through Takyon self-description and explicitly treats omitted/truncated/unreadable paths as unknown instead of absent.
- Terminal CEO chat also reads a small focused set of workspace files when the operator names a path or asks about an obvious root such as product, outreach, website, jobs, blockers, or memory.
- Business skill state packets include the same top-level map and omitted-file count.
- Takyon self-description exposes the business workspace top-level map and read strategy.
- Workspace directory read failures no longer look like empty folders; they throw a visible error.
- Unsupported non-file/non-directory workspace entries are rejected because the CEO cannot read them as normal business evidence.

Acceptance checks:
- `npm run typecheck` passes.
- `npm run migrate` applied `0014_business_test_mode.sql`, which was required before workspace sync commands could read the business row after the `businesses.mode` change.
- `./takyon files contractclear --json` lists the current 36 workspace files.
- `./takyon workspace contractclear --json` exposes the 36-file top-level map and read strategy.
- Direct `businessWorkspaceContext` inspection reports 36 files, 14 top-level buckets, 9 boot files, and explicit truncation metadata on large boot excerpts.
- Direct `focusedBusinessWorkspaceExcerpts` inspection for an outreach/status question reads `outreach/outreach-pipeline.md` and relevant job files, with truncation metadata.
- Direct `buildTakyonSelfDescription` inspection for an outreach/status terminal message exposes the 36-file top-level map and focused excerpts for `outreach/outreach-pipeline.md` plus relevant job files.

Known limits:
- This does not force the CEO to read every file every run. It gives cheap top-level visibility and requires relevant deeper reads, matching the Hermes-style "overview first, inspect deeper when needed" pattern.
- The runtime files tool still depends on the Hermes/Takyon runtime being installed and reachable.

## Generated-App Rails Versus Customer Surface Guardrail

Implemented behavior:
- `AGENTS.md` now requires generated-app deterministic templates to be treated as rails scaffolding only.
- Customer-facing website, offer, product workflow UI, visual system, copy, and conversion surfaces must route through existing generated-app surface builders, Claude Agent SDK paths, OpenLovable integration when explicitly configured, and Takyon skills already present in the repo.
- Before generated-app behavior changes, agents must trace why the main trunk missed the existing skill or builder path instead of making one-off local generated-app patches or duplicate builders.
- `website_build_deploy` now requires the existing Claude Agent SDK surface builder capability in the central workflow registry.
- `website_build_deploy` now always calls the existing Claude Agent SDK surface builder after deterministic rails scaffolding. The deterministic template branch is no longer a valid published customer-surface path.
- The deterministic generated-app homepage template has been reduced to a compile-only rails placeholder. It is not intended to be the public/customer surface.
- The Takyon CEO path now always calls the Hermes/Takyon runtime client. The local plain-model CEO fallback and `TAKYON_REMOTE_RUNTIME` selector are removed from the CEO decision path.
- The Hermes/Takyon runtime client waits for the `/v1/runs` result and fails if the run fails, cancels, or completes without CEO output.
- `takyon_runtime` capability now checks the vendored runtime files, local runtime venv, model key, and local Hermes gateway reachability instead of reporting runtime success from model keys alone.
- `scripts/setup-argon-hermes-runtime.sh` now selects Python >= 3.11 and removes a wrong-version venv before installing the vendored Hermes runtime.
- The local Hermes runtime venv has been installed with Python 3.12 in this checkout, and Takyon skills have been synced into `.argon-hermes-home`.
- `./takyon vps` now starts the Hermes CEO runtime gateway together with cron dispatch and the local worker loop.

Acceptance checks:
- `npx tsc --noEmit` passes.
- `./takyon capabilities` reports `claude_agent_sdk` as available and reports `takyon_runtime` as blocked when the local Hermes gateway is not running.
- `./takyon runtimes` reports the missing gateway start command instead of claiming the CEO runtime is OK.
- Foreground `scripts/start-argon-hermes-runtime.sh` was verified against `http://127.0.0.1:8642/health` and returned `{"status":"ok","platform":"hermes-agent"}`.
- A bounded `./takyon vps` test started Hermes, returned the same health response, and was then stopped.

Known limits:
- The local Hermes gateway is not kept running by this Codex turn. Run `./takyon vps` for the normal local-Mac VPS loop, or `scripts/start-argon-hermes-runtime.sh` for just the Hermes gateway.

## Hermes-Decided Business Startup

Implemented behavior:
- Business creation now queues one `ceo_wakeup` job only.
- The old fixed startup plan expander is removed from creation routes; `/new/takyon/start` and `/api/companies` now call `enqueueBusinessStartup`.
- The central workflow registry no longer contains a foundation workflow and no workflow depends on a foundation lane.
- The local worker no longer has a local foundation/market-research branch.
- The local foundation workflow files were deleted.
- The CEO prompt tells Hermes to choose bounded runner work from business context, not from a fixed startup sequence.
- Database migration `0015_remove_foundation_startup_lane.sql` cancels active legacy foundation jobs, moves legacy foundation-lane rows to `ceo`, changes the default lane to `ceo`, and removes foundation from the lane check.
- `AGENTS.md` now states that new businesses create business context, workspace/filesystem, cron/watch rows, and a Hermes CEO wakeup only.

Acceptance checks:
- Source search under `src/` and `scripts/` finds no active `foundation` workflow id, dependency, local foundation fallback, startup expander, or foundation env/model setting.
- Outer `./takyon --help` launches Hermes Takyon, not the removed polsia3 launcher.
- Outer `./takyon --json cron list` succeeds and reads the outer `.takyon` state.

Known limits:
- This does not rename historical database migrations already applied in old databases; migration `0015` cleans live state forward.
- Generated-app rails still create plan, entitlement, wallet, and proxy-key records when a build or app runtime lane needs them. That is deterministic product plumbing, not business judgment or startup sequencing.
