# AGENTS.md

## Required Prompt Restatement

Before implementing anything, explicitly restate the user's request in your own words.

The restatement must include:
- what will be changed
- what will not be changed
- the backend/runtime/deployment target for the change
- which `run/` documents will be updated

Do not start implementation until that restatement has been given.

## Run Directory Is Source Of Truth

All future implementation changes must update the readable `run/` directory.

`run/` is the agent-owned truth ledger. It should describe what is actually implemented, verified, decided, blocked, or failed.

Do not update `run/` first as a speculative promise. Implement and verify first, then update `run/` so it stays true.

`prompt/` is the operator-editable sync folder. It is a rough prompt/spec/input folder the user can edit. It is not automatically true.

The source-of-truth chain is always:

```text
verified reality -> run/ -> prompt/
```

`verified reality` means implemented code, observed connection checks, passing tests, failed tests, actual blockers, or explicit operator decisions. `run/` records that reality. `prompt/` is then refreshed from `run/` so the editable folder starts from truth instead of drift.

For every code change:
- update the relevant `run/*` design/runbook/acceptance file
- record why the change exists
- record the acceptance checks that prove it works
- keep docs and code consistent

Architecture changes that exist only in code are not allowed.

## Per-Business Isolation And One Source Of Truth

Everything is per business unless explicitly stated otherwise.

Business-scoped data, jobs, campaigns, conversations, touchpoints, monitors, budgets, memory, documents, and vendor receipts must carry and preserve the owning `business_id`. Do not blend state, conclusions, reply queues, campaign evidence, or automation decisions across businesses unless the operator explicitly asks for cross-business behavior.

There must be one source of truth for each operational fact. Prefer one canonical table or ledger row with stable IDs over parallel channel-specific truth stores. Derived views, documents, memories, and prompts must point back to that canonical evidence rather than becoming competing truth.

For outbound distribution and reply monitoring, the canonical chain must be:

```text
business-owned outbound receipt -> business-owned monitored touchpoint -> business-owned conversation message -> business-owned customer/campaign signal
```

Cron or worker loops may be shared infrastructure, but every claimed unit of work must remain business-scoped, bounded, idempotent, and traceable to the canonical row that caused it.

`businesses.mode` is the source of truth for live/test behavior. In `test` mode, workflows may plan, build, draft, record receipts, run per-business cron/watch loops, and observe internal state exactly as live mode does, but outbound outreach/distribution adapters must not create external posts, DMs, comments, emails, ads, or spend. They must write a business-owned receipt that says the external side effect was suppressed. Missing outbound-provider keys may be bypassed only when the adapter can produce a truthful suppressed-side-effect receipt; missing model, research, build, payment, or database capabilities must be recorded as blocked capability evidence, not replaced with fake success.

Operator changes to live/test mode must go through the source-of-truth business setting, including the Takyon shell `/test on|off|status` command. Do not add parallel per-channel test flags.

## No Part Left Unused

Business-owned artifacts must not be silently ignored. If a file, receipt, document, memory, job, conversation, campaign row, or generated artifact exists for a business, it must either be reachable from the business's canonical evidence map when relevant or be flagged as unreadable, orphaned, omitted, or blocked.

The per-business workspace filesystem is a first-class business artifact. CEO/runtime context should start with a cheap top-level map and boot files, then read deeper only when relevant. If prompt budget, tool failure, path policy, or missing runtime support means the CEO cannot or will not read a relevant path, surface that limitation to the operator instead of treating the path as absent.

## Sync Folder Workflow

When the user says "my prompt is in the sync folder", "I edited the sync folder", "I edited `prompt/`", or similar:

1. Treat `prompt/` as rough user intent, not as truth.
2. Read the changed/mentioned `prompt/` files.
3. Restate the requested change explicitly.
4. Clean up the implementation plan mentally or in a temporary note, but do not make `run/` claim completion before code exists.
5. Implement the corresponding code change.
6. Verify it with the relevant acceptance checks.
7. Update `run/` to match the verified implementation, blockers, or failures.
8. Sync `prompt/` from `run/` so the editable folder is refreshed from truth.
9. Report any ambiguity, missing secret, missing backend, or policy conflict as `blocked` before code pretends it works.

Sync direction after implementation is always:

```text
run/ -> prompt/
```

Never sync rough `prompt/` text into `run/` as if it were true before implementation and verification.

Never let `prompt/` outrank `run/`. If `prompt/` and `run/` disagree, treat `prompt/` as a requested change, not as current truth. After handling the request, update `run/` to the actual outcome and overwrite `prompt/` from `run/`.

The sync folder is allowed to contain rough operator notes. The agent's job is to turn those notes into working code and then update `run/` with the truth.

## No Silent Fallbacks Or Fake Success

Do not add fallbacks, stubs, placeholder success, repair shells, degraded success, fake metrics, fake users, fake vendor actions, fake deployments, fake AI, fake payments, or fake side effects unless the exact behavior has first been documented in `run/` and explicitly called out to the user.

Completion means the promised thing actually happened and evidence exists.

Use:
- `blocked` for missing secrets, permissions, budget, policy, approval, or operator input
- `failed` for code errors, vendor errors, timeouts, invalid responses, or broken builds
- `completed` only when the work truly completed

## External Posting And Spam Safety

Never spam, poll aggressively, DM, comment, post, create accounts, or bypass policies on Reddit, X/Twitter, Meta/Facebook/Instagram, LinkedIn, TikTok, ProductHunt, IndieHackers, YouTube, or similar platforms.

X posting may be automatic only when:
- the X add-on is configured
- the company policy mode allows automatic publishing
- the DB rate limit allows it
- the deterministic X API call returns a real receipt

For v0, Meta Ads must generate and display Seedance creative only. Do not create, upload, launch, pause, or spend through Meta Ads APIs in v0.

For cached Latexflow builds (`template = latexflow-v1`), Sora rows are delayed. A fresh cached Latexflow company should queue the Sora lane for about 3 minutes later, and the worker should write the company-owned Sora row only when that delayed job runs. Do not interpret this as releasing or mutating an old existing row.

Community/Reddit surfaces should look like finished product UI, but backend policy remains no-post: show real discovered targets and real generated copy, never claim posting happened.

## Secrets

Do not print, summarize, or expose secret values.

When secrets are copied from v2, copy values exactly without echoing them. Generate only the secrets that are explicitly designated generated, such as `CRON_SECRET`.

## Backend Decision For V0

The v0 backend split is:
- Vercel hosts the Takyon platform UI and API
- Postgres is the source of truth
- a local Mac worker runs long jobs, generated-app builds, deploys, and optional Hermes/local runner workflows
- GitHub private repo stores source and history

Do not use Vercel Sandbox as the generated-app builder.

## Generated-App Rails Versus Customer Surface

Do not use deterministic templates as the customer-facing website or product UI.

Templates may create only deterministic rails: auth, account, checkout, billing, API routes, platform client wiring, entitlements, user/session plumbing, project AI proxy plumbing, and minimal compiling placeholders when required. The public website, offer, product workflow UI, visual system, copy, and conversion surface must be produced by the existing generated-app surface builders, Claude Agent SDK paths, OpenLovable integration when explicitly configured, and Takyon skills already present in the repo.

Before changing generated-app behavior, first trace why the main trunk did not route through the correct existing skill or builder. Do not make one-off local generated-app patches, duplicate builders, duplicate skills, or duplicate tool registries to compensate for a missed trunk connection. Fix the canonical route, capability gate, or source-of-truth handoff that caused the bypass.

## Anti-Sycophancy And Brittle Tooling Warnings

Do not agree with the operator just because they suggested a tool, runtime, architecture, or integration.

Before adding or depending on a tool/interface that introduces extra moving parts, explicitly warn the operator when it looks brittle, overengineered, unnecessary, or likely to create hidden failure modes. This includes separate localhost services, streaming/SSE parsers, browser automation layers, generated-file extraction protocols, conversation-reset state, repair loops, proxy tunnels, sandbox runtimes, and vendor SDKs used as broad automation boundaries.

The warning must say:
- what overhead the interface adds
- what failure modes it creates
- what the simpler direct alternative is
- whether the added interface is actually necessary for the requested outcome

If a simpler direct approach is enough, say so clearly and prefer it after the operator confirms. Push back early, before implementation creates a mess.

## TODO - Generated-App Economics, Auth, And API-Key Funding

Deferred by operator for the current terminal/queued-CEO operations slice. Keep these as explicit later work, not hidden assumptions.

These items are not complete. Do not describe them as done until they are implemented and verified:

- User-supplied API keys as a selectable funding source for generated-app AI.
- Cross-app allocation of an owner/operator API key or wallet budget across all apps created by that user.
- Full paid-user reserve and free-user leftover budget enforcement from the v2 generated-app economics model.
- Correct generated-app browser auth/session wiring from generated app domains, including same-origin or CORS-safe magic-link request, verify, session read, cookie scope, and credentials behavior.
- Stripe webhook entitlement E2E, proving a real checkout/session event updates generated-app paid entitlements.

Guardrail that remains non-negotiable:
- Generated apps must receive only project-scoped proxy keys or same-origin platform routes. They must never receive raw OpenAI, Anthropic, Stripe, X, Meta, or other provider keys.

## TODO - Engagement Learning Loop

The post-launch observation loop is intentionally stubbed in v0.

Implemented now:
- After real X publishing or completed Sora creative sync, Takyon can queue an `observe_campaign_results` workflow job so terminal/queued CEO has a visible follow-up row after launch.
- The queued observation job is a visible placeholder for the future CEO learning loop.

Not implemented yet:
- Fetching X engagement metrics.
- Reading Sora/video creative performance.
- Comparing post/ad variants.
- Automatically deciding the next campaign from engagement evidence.
- Waking on a durable schedule to observe results, decide, run the next lane, and sleep again.

Do not claim engagement learning is complete until vendor metrics are fetched, persisted, analyzed, and used to enqueue a real next action with receipts.

User-facing CEO/chat language must say `background runner` or `queued runner` for ordinary operator chat. Do not leak `local Mac worker` or local machine details unless the operator explicitly asks about runtime architecture/debugging.

## Hermes Scope

Hermes/Takyon runtime is the canonical CEO runtime. CEO wakeups and CEO reasoning must not fall back to a plain local model call that cannot use Hermes skills, files, and runtime tools.

Hermes owns non-deterministic business judgment: CEO inspection, skill selection, research synthesis, market planning, social/content/support/outreach copy, lead finding, campaign review, and activity review.

Deterministic side effects, generated-app builds, cron dispatch, payments, AI metering, deployment, and vendor mutations stay app-owned/local-runner-owned. Hermes may request or queue those actions, but the runner must validate capabilities, execute bounded work, and record receipts.

Business creation must not enqueue a fixed startup plan. A new business creates the canonical business row, business workspace/filesystem, per-business cron/watch rows, and one Hermes CEO wakeup with the operator brief. Hermes then reads the context and chooses the first skill or bounded runner job. Do not add a hardcoded foundation/startup lane, hidden workflow dependency, or local-model startup fallback.
