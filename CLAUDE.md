# Takyon Workspace Instructions

## Source Of Truth

This workspace has one runnable Takyon trunk:

```text
/Users/Zygote/Downloads/takyon/takyon
  -> /Users/Zygote/Downloads/takyon/hermes-agent-main
```

`./takyon` at the workspace root is the canonical operator entrypoint. It launches the Hermes/Takyon runtime with `TAKYON_HOME` rooted at `/Users/Zygote/Downloads/takyon/.takyon`.

`hermes-agent-main` is the active runtime, CEO, skill, cron, and plugin trunk.

The old `polsia3` app tree was removed. Archived source material salvaged from it lives under `hermes-agent-main/plugins/takyon/references/polsia3-skills/`.

Do not recreate `polsia3`, do not restore `polsia3/takyon`, and do not describe `polsia3` as "the active Next/Takyon app", "the main trunk", or "one of two trunks".

## Deployment Notes

The current Takyon VPS target is `137.184.75.57` (`argon-alpha-14`). Use the local Codex deploy key at `~/.ssh/takyon_argon_alpha14` for root SSH when deployment work needs direct VPS access.

The public dashboard hostname is `app.fourmanifold.com`. DNS is managed outside this repo and should currently resolve to `137.184.75.57`; if it does not, treat that as DNS drift and fix the record outside this repo rather than papering over it in code.

For `app.fourmanifold.com`, the intended operator experience is the embedded Takyon business/chat UI, not the plain dashboard Sessions shell. On the VPS, make sure `takyon-dashboard.service` starts the dashboard with `takyon dashboard --tui` or sets `TAKYON_DASHBOARD_TUI=1`. If the host shows `/sessions`, `Sessions`, `Models`, or `Logs` as the main landing view, treat that as a dashboard startup-mode misconfiguration on the VPS rather than a frontend deploy failure.

A prod-shaped, fully-isolated dev environment is stood up with `takyon env create dev` (the `EnvironmentProvisioner`, `hermes-agent-main/plugins/takyon/env_provisioner.py`, declared by `hermes-agent-main/environments/dev.yaml`). Dev is **its own Supabase project** as its control plane — never dev tables inside prod — provisioned with prod-named roles via `topology.sql` and the tracked migrations, plus a dev safebox, Stripe **TEST** webhook, and a dev Auth0 application. The command is idempotent, receipted, and fails closed: on a fresh workspace it names the exact aliases to deposit first (`TAKYON_DEV_MIGRATION_DATABASE_URL`, `TAKYON_DEV_SAFEBOX_URL`, the dev safebox's Stripe TEST / model / media / search keys, and the two one-time-ever admin tokens the safebox does not yet hold — `TAKYON_AUTH0_MGMT_TOKEN` and, for the optional dev droplet, `TAKYON_DO_API_TOKEN`). Admin tokens resolve only via the safebox authority route, never `os.environ`, and the prod-literal guard (`environment.assert_not_prod_leakage` / `PROD_LITERALS`) refuses any target that resolves a prod IP/host/DSN. After the one-time token deposits, `takyon env create dev` (and `create dev2`, per-branch instances) is autonomous; `takyon env status <name>` reports state without side effects and `takyon env destroy <name>` refuses while the env has live pools/ledgers unless `--force`. Restarting or redeploying the LIVE replica split goes through `takyon env restart <name>` (`EnvironmentProvisioner.rolling_restart`) — the drain-aware rolling restart that loses ZERO requests on planned restarts: per replica it removes the node from the LB, waits out in-flight requests, converges the tracked Caddy front (`deploy/takyon-dev-split/Caddyfile.dev`, which bakes the `X-Takyon-Node` identity header), restarts `takyon-subuser.service`, health-verifies locally, re-adds the node, and only proceeds once the LB provably routes to it again; it fails closed unless every other replica is a healthy LB member. Never hard-restart a serving replica directly — that black-holes ~4.5s of LB traffic (the health-check detection window); sync code first (`deploy/takyon-dev-split/bootstrap-dev-droplet.sh` on a drained node, or rsync), then activate with `takyon env restart`. This manifest + provisioner is tracked in the outer repo so future pushes carry it, and `hermes-agent-main/environments/hermetic.yaml` is the all-stub CI/fast profile.

When the operator asks to push or deploy Takyon, keep the three rails distinct:

1. Git push uses the outer workspace repo at `/Users/Zygote/Downloads/takyon`, not the nested `hermes-agent-main` git metadata. Stage only the intended hunks, commit in the outer repo, and push `origin main` unless the operator asked for a branch.
2. VPS deploy updates the active runtime. The VPS runtime is `/opt/takyon/hermes-agent-main` and may not be a git checkout, so deploy changed runtime files with `rsync`/`ssh` using `~/.ssh/takyon_argon_alpha14`, then compile touched Python files and restart `takyon-dashboard.service`. Verify with `systemctl is-active takyon-dashboard.service` and source checks on the VPS.
3. VPS Caddy config is tracked at `deploy/argon-alpha-14/Caddyfile`; apply it with `deploy/argon-alpha-14/apply-caddyfile.sh`. This is the repeatable source for `app.fourmanifold.com` and shared `slug.fourmanifold.com` product routing. Do not hand-add new per-business Caddy blocks for normal businesses.
4. Vercel deploy is the `app` project frontdoor only. It is not the canonical Takyon runtime and successful Vercel deploys do not prove prompt, skill, registry, or backend changes reached the VPS. Do not run `vercel deploy` from the workspace root; that uploads the wrong artifact. Use `vercel redeploy` against the current known-good `app` frontdoor production deployment, or deploy an equivalent tiny frontdoor artifact, then verify `vercel inspect app.fourmanifold.com` is Ready and aliased. Treat Vercel alias state separately from DNS: `app.fourmanifold.com` may still resolve to the VPS and return Caddy/uvicorn headers even when Vercel has a Ready alias.

The deployment workflow is tracked at `.github/workflows/deploy.yml`: pushing `main` should run the dashboard web build, compile Python, redeploy the current Vercel `app.fourmanifold.com` frontdoor when `VERCEL_TOKEN` is configured, rsync the active runtime to the VPS, restart `takyon-dashboard.service`, apply tracked Caddy only when `deploy/argon-alpha-14/Caddyfile` changed, and verify the public dashboard host. Required GitHub secrets are `TAKYON_VPS_HOST`, `TAKYON_VPS_USER`, `TAKYON_VPS_SSH_KEY`, and optional Vercel metadata secrets `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`. **GitHub Actions is OFF (account billing failure; push triggers disabled 2026-07-04) — do NOT run `gh run list`/`gh run watch` after pushing and do NOT report GHA state to the operator.** The workflows are `workflow_dispatch`-only until billing is restored; deploys are the tracked local scripts below.

Production Postgres migrations must run through the VPS migration DSN as `takyon_migration`; do not store a `postgres`/owner DSN in Safebox or on any host. The tracked command is `takyon migrate` from the operator VPS rail; it resolves `TAKYON_MIGRATION_DATABASE_URL`, asserts the `takyon_migration` role/topology, replays every idempotent migration file, and prints the schema fingerprint. The required Supabase topology is codified in `hermes-agent-main/plugins/takyon/db/topology.sql`: public Takyon-owned tables, routines, sequences, views, and non-array types are owned by `takyon_migration`, and `takyon_migration` holds `WITH ADMIN OPTION` membership on `takyon_app`, `takyon_app_runtime`, `takyon_operator_runtime`, `takyon_safebox_authority`, and `takyon_runtime`. This was repaired on 2026-07-02 because early prod objects had been created manually as `postgres`; future migrations should assume the tracked runner can replay all migration files autonomously under `takyon_migration`.

Fast path for ordinary code/docs/UI changes:

1. Run only the focused local checks needed for the touched surface, plus `git diff --check`.
2. Stage only intended files, commit in the outer repo, and `git push origin main`.
3. **Skip GitHub Actions entirely (billing-dead; push triggers disabled 2026-07-04 — see above). Never run or report `gh run` commands to the operator.**
4. Activate the runtime — full-tree rsync to BOTH hosts (operator `137.184.75.57`, sub-user `134.209.123.8`, key `~/.ssh/takyon_argon_alpha14`): `COPYFILE_DISABLE=1 rsync -rt --no-perms --no-owner --no-group --checksum --exclude='.git' --exclude='.venv' --exclude='venv' --exclude='node_modules' --exclude='__pycache__' --exclude='*.pyc' --exclude='._*' --exclude='.DS_Store' --exclude='.env' --exclude='secrets' --exclude='logs' --exclude='tmp' hermes-agent-main/ root@HOST:/opt/takyon/hermes-agent-main/`, then on each host delete `._*` sidecars, `py_compile` the touched files, restart the services (operator: `takyon-dashboard.service` + `takyon-worker.service`; sub-user: `takyon-subuser.service`), and verify `is-active` + a source-level check. Never restart a worker while it holds a running job (check `jobs` for rows locked by that host first).
5. **If the deploy includes new files under `hermes-agent-main/plugins/takyon/db/migrations/`, run `takyon migrate` once on the operator host (the tracked rail: resolves the migration DSN, asserts topology, replays every file idempotently as `takyon_migration`) BEFORE restarting services.** This is one autonomous command, never hand SQL. It is deliberately an explicit deploy step, not an automatic service-start side effect: runtime roles cannot DDL and the migration DSN lives only on the operator rail — that privilege boundary is the schema-change security model. Migrations are additive/nullable by convention, so running them before the code restart is always safe, and re-running is a no-op.
6. Report the VPS activation evidence (rsync + migrate-if-needed + restart + is-active) and direct smoke checks. Push main AND activate the runtime — one without the other is not a deploy.

## User Terms

Do not conflate Takyon users with product subusers.

- A Takyon user is the top-level operator/account that owns a Takyon agent and may create businesses.
- A product subuser/app customer is a customer of a business/product created by that Takyon user.
- Top-level user-scoped APIs, credentials, budgets, and identity are different from business/product customer auth, entitlements, usage, billing, and scoped API keys.

## Product App Surfaces

The shared Hermes app runtime owns backend rails only: auth/session protocol, payment/webhook reconciliation, entitlement policy, app usage budget accounting, state mirrors, and safety gates.

Do not hardcode the final product's look, layout, copy, theme, or information architecture in the runtime. Store that per business with the `business_upsert_app_surface_contract` tool, mirrored at `product/surface.md`, and point it at the business design brief/source path that the CEO and skills should inspect.

Backend rails for a product app should be declared once on the surface contract as `runtime_features`, not rediscovered separately in UI code or copied across skills. The canonical registry of known rails lives in `hermes-agent-main/plugins/takyon/core.py` (`PRODUCT_RUNTIME_RAILS`). Claude product-site work should receive the selected rails as injected worker contract guidance, and backend-owning skills should read the same selected rails from `product/runtime.md` under `Rails By Owner`. When adding a new rail, extend the registry, then select it through `runtime_features`; do not create a second per-skill rail list.

When implementing custom backend or own-runtime product capability later, update the authoritative Takyon discovery surfaces the CEO can actually see: the stable CEO prompt at `hermes-agent-main/plugins/takyon/prompts/ceo.md`, the owning Hermes skill under `hermes-agent-main/skills/takyon/`, and the guarded product verification/deploy tool behavior. Do not add a hidden backend path that the CEO cannot see through skill frontmatter/body, tool schemas/results, or business receipts.

## Creative Credits vs Usage Billing

Keep Takyon payment rails distinct. Do not merge them into one generic "budget":

- `hermes-agent-main/plugins/takyon/billing.py` and `hermes-agent-main/plugins/takyon/control_api.py` are the control-plane Takyon user -> platform money rail.
- `hermes-agent-main/plugins/takyon/app_usage.py` is the per-business product runtime usage rail for normal app/customer AI spend.
- Future fixed-price business creative/ad actions should use a separate business-scoped creative credit rail. The intended canonical home is a leaf like `hermes-agent-main/plugins/takyon/business_credits.py`, not a skill-local ledger and not product subuser entitlements.

When adding future skills/tools:

- Plug into creative credits when the action is a business-scoped paid creative/ad operation with a fixed operator-facing price, such as ad image generation, ad video generation, campaign staging/launch actions, or similar future channel-creative actions.
- Keep usage-based billing when the action is ordinary AI inference whose user-facing price should track real usage, especially product app AI/chat/generate actions and other runtime calls that meter through `app_usage.py`.
- Do not treat third-party media spend as creative credits. Credits may charge for the Takyon action that creates or stages a campaign, but live ad spend remains separate USD budget authority.

Creative-credit payment rules:

- Credit purchases should reuse the control-plane Stripe checkout/webhook pattern in `hermes-agent-main/plugins/takyon/control_api.py`, not the product app checkout rails.
- Skills may orchestrate, but payment authority must live in shared business tools/rails. Spendful tools should fail fast on missing credits, reserve before provider work, commit on success, and release on failure.
- User-facing credits may be fixed packs, but each credited action should still record exact provider/model cost metadata from `hermes-agent-main/agent/usage_pricing.py`.
- New billing-sensitive model/provider integrations should extend `hermes-agent-main/agent/usage_pricing.py`; do not add a second hardcoded pricing table in a skill or channel tool.
- Normal app AI runtime pricing should resolve exact model pricing from `hermes-agent-main/agent/usage_pricing.py`, not heuristic family matching.
- **No ungated paid capability — gate it behind usage or credits before it ships.** Never introduce a tool, skill, action, or runtime call that spends real money (provider/API, model inference, search/extract such as Tavily, media, or infra) without first metering it through a money gate. Route consumption-priced spend — model inference and per-request runtime tool calls — through the usage rail (`hermes-agent-main/plugins/takyon/app_usage.py`, reserve→settle→release, priced in `hermes-agent-main/agent/usage_pricing.py`; use `request_cost` for per-request providers); route fixed operator-priced creative/ad actions through the creative-credit rail (`hermes-agent-main/plugins/takyon/business_credits.py`). Spendful paths must fail closed: resolve exact cost from `hermes-agent-main/agent/usage_pricing.py` (unpriced = refused), reserve before the provider call, settle on success, release on failure. Do not paper over an ungated call with a downstream cost report or label — gate the call itself. If it cannot be gated yet, ship only the guarded/disabled path and record the missing gate; never expose the ungated paid path.

Paid-provider secret keys live behind TK (the safebox) — never read them from `os.environ` in a business tool or skill. Every secret that lets a business tool call a paid provider — model/image/video APIs (Gemini, FAL, OpenAI `gpt-image`), search/extract (Tavily), social/ads (Composio) — is held by the safebox (private secret-authority host `67.205.158.170`), not in the business runtime. On the VPS the key is not in the process env: `os.environ.get("GEMINI_API_KEY")` is empty there, so a tool that reads its key from `os.environ` both breaks the secret boundary and fails closed at runtime (observed: a logo rewrite that read `os.environ` raised `GEMINI_API_KEY required` on prod and never worked).

Canonical pattern (`GOAL_RULES` §7) — see `hermes-agent-main/plugins/takyon/creative_gateway.py::_resolve_gemini_image_key`:

- Resolve the key only in an authority route via `safebox.first_env_backed_value(*ALIASES)` (e.g. aliases `TAKYON_GEMINI_API_KEY` / `GEMINI_API_KEY` / `GOOGLE_API_KEY`). The business runtime never reads the raw key.
- Pass it as an explicit argument to the provider client (`genai.Client(api_key=...)`), never via `os.environ`.
- Fail closed: if the key is absent, return a clear `*_unconfigured` error / `503` before any credit reserve or provider call — never proceed or fabricate.
- Register the alias in `hermes-agent-main/plugins/takyon/core.py` (`_API_ENV_ALIASES`) and declare `requires_api=[...]` on the tool; gate the spend (creative-credit or usage rail) in the same authority route.

Any new paid-provider skill/tool resolves its key this way — same as logo (`business_generate_logo`), UGC, static-ad, and web-search. Reading a provider key from `os.environ` in a business tool is a bug: it breaks the secret boundary and fails on the VPS.

## Operating Model

Takyon should be a skill-based Claude Agent SDK CEO system, not a fixed workflow cockpit.

Practice parsimony and no slop. Use the smallest native SDK skill, scoped tool, and durable runtime surface that genuinely handles the job for every business and invocation mode; never use a fake/demo product path.

Skill-based does not mean every fix belongs in skill prose. If the real missing piece is tool registration, provider config, harness metadata, a guarded runtime rail, or a UI renderer, fix that canonical surface and let the existing skills use it; edit or add a skill only when the business-facing method, judgment, or routing behavior genuinely changes.

When behavior is wrong, identify the missing source of truth, skill contract, HANDOFF binding, tool schema, receipt, guardrail, or runtime rail, then fix that canonical surface.

Do not add deterministic business-action routers or forced artifact shortcuts to make the CEO pick a business move. Let it select from approved native skill descriptions and scoped tool schemas; deterministic code owns invocation modes, bootstrap/wake checkpoints, scope, idempotency, authority, spend, receipts, and done gates.

Use canonical sources: runtime config in `$TAKYON_HOME/config.yaml`; base policy in `plugins/takyon/prompts/ceo.md`; portable methods in `skills/*/SKILL.md` plus `contract.yaml`; exact tools, paths, modes, authority, publication, receipts, and validators in `skills/HANDOFF/`; approved inventory in `skills/release-skills.yaml`; and business facts in the control plane plus canonical workspace.

Before adding a new store, command, prompt rule, metric file, or workflow, check whether the active Takyon trunk already has a canonical source that does the job.

Do not answer diagnosis with artifact edits; a systemic fix belongs in the relevant skill, HANDOFF binding, tool, harness metadata, or AGENTS instruction.

`AGENTS.md` is not a CEO runtime prompt. Do not put business strategy, wake-loop policy, product judgment, outreach policy, or per-business operating instructions here. Runtime behavior belongs in the CEO prompt, Takyon skills, guarded business tools, harness command metadata, cron prompts, and per-business `research/`, `product/`, `distribution/`, and `metrics/` state.

Hardcode only durable rails and safety primitives: invocation modes/checkpoints, business isolation, path containment, idempotency, credential gates, budget caps, audit events, pause/resume/kill controls, conservative cleanup, and shared product-runtime APIs.

When changing CEO/runtime behavior, edit stable policy, approved skills, HANDOFF bindings, and the relevant tool/cron/harness source. In particular:

- `plugins/takyon/prompts/ceo.md` is stable base policy; bootstrap, wake, and interactive differences are code-owned mode policy plus fresh context.
- `scripts/takyon-claude-primary-runtime.mjs` is the only model-agent runtime; `takyon-worker.service` is a durable queue consumer.
- All approved skills are discoverable by default from one versioned read-only plugin and load on demand through the native `Skill` tool; SDK subagents are unavailable.
- Do not reintroduce Hermes delegation, `business_claude_agent_task`, ambient skill settings, or mutable `$TAKYON_HOME/skills` discovery.
- `takyon-app-runtime` and the canonical app tools own product customer auth, magic links, sessions, app customers/subusers, entitlements, plan policy, checkout, Stripe webhook reconciliation, revenue, and usage budgets.
- `takyon-business-metrics`, `takyon-market-research`, `takyon-build-product`, `takyon-distribution`, `takyon-x`, `takyon-reddit`, and `takyon-conversation-followup` own the remaining active business methods.

When the operator asks to add a normal new Takyon feature or skill, always use the parsimonious addition path:

1. Add `hermes-agent-main/skills/takyon/<new-feature>/SKILL.md` from `hermes-agent-main/skills/SKILL-TEMPLATE.md`.
2. Put `Use when` and `Do not use` routing in the native description; keep Inputs, Method, Verification, and Failure Conditions provider- and deployment-agnostic.
3. Add `contract.yaml` with semantic capabilities and outputs, then bind exact tools, roots, paths, modes, authority, publication, receipts, and validators in `skills/HANDOFF/bindings.yaml`.
4. Add only necessary resources, and add the reviewed exact `publish_files` allowlist to `skills/release-skills.yaml`.
5. Add a guarded `business_*` tool only when a new canonical side effect/state rail is required, then bind it through HANDOFF.
6. Run `python3 scripts/build_approved_skills_manifest.py` and then `--check`.
7. Deployment publishes the exact inventory into a content-addressed read-only plugin; prove native discovery there and never sync to mutable `$TAKYON_HOME/skills`.
8. Keep bootstrap/wake phase order, retries, spend settlement, and done gates in runtime code, not skills.

For every new feature, skill, or tool, verify the native description, `contract.yaml`, HANDOFF mode/capability bindings, generated approved manifest, invocation-mode policy, and canonical tools/runtime rails.

Takyon skills must keep rich portable operational detail without embedding Takyon tool names, paths, providers, authority, spend, or publication targets; those exact bindings belong in HANDOFF.

Before adding a feature, skill, tool, store, command, metric, or prompt rule, check redundancy and conflicts across bootstrap, wake, interactive, business isolation, app-runtime rails, cron, and shell policy; if valid, describe native discovery and exact HANDOFF/mode grants.

Before implementing a new feature, prove or ask whether it needs new provider access, API keys, OAuth apps, paid services, network scraping, external posting/sending, deploy credentials, model/video/image generation access, billing rails, or budget authority. Report the exact required environment variables, provider accounts, expected side effects, test-mode behavior, and live-mode gates. If the feature can work locally without new credentials, say that explicitly. If live functionality would be degraded or blocked without credentials, implement the local/guarded path only and record the missing provider gate instead of pretending it works.

External side-effect semantics belong in guarded business tools and the relevant skills. Do not duplicate them in `AGENTS.md`, shell prose, or one-off prompts.

Archived `polsia3` reference files may mention fixed workflow IDs or old workflow catalogs; treat them only as historical source material.

## Test Mode

Test-mode semantics belong in the Takyon tools, skills, and harness metadata, not in `AGENTS.md`. The canonical state is `businesses.mode` in the Postgres control plane, changed through `business_set_mode` or the shell command `/test on|off|status`. Do not add parallel per-channel test flags or duplicate test-mode behavior in prompts.

## Agent-SDK Takyon Work

Implement Takyon behavior through portable skills, reviewed HANDOFF bindings, scoped tools, and durable business state; do not add one-off model-worker stages.

When modifying artifact/reporting surfaces, keep business deliverables visible through tool results, shell progress, or concise reports. Paths should come from canonical tool results and filesystem roots, not invented shell text.

Default rule: everything business-facing should be an approved native skill discoverable in the immutable production plugin or a business-scoped tool visible through HANDOFF-approved schemas/results; safety and control rails remain enforced in code.

Slash-command UI must be source-of-truth driven. Shell palettes/help should derive from `hermes-agent-main/plugins/takyon/harness/settings.json` for controls, `hermes-agent-main/plugins/takyon/harness/commands/*.md` for harness skill commands, and Hermes skill discovery for Takyon skills. If a future command or skill is added, updating that canonical source must be enough for shell discovery; do not duplicate slash command names/descriptions in the UI.

## Takyon Shell Model

The Takyon shell is always in a scope. `global` is the top-level account/root scope; it is not the CEO. A business scope is `business:<slug>`.

The CEO is the scoped operator agent role. Plain text in the shell should route to the CEO for the current scope, except obvious shell self-help questions such as "how do I create a business", which should answer locally and tersely. The shell must preserve recent in-session turns so follow-ups can resolve against recent context; tune that through `harness/settings.json`, not hardcoded prompts. `/ceo` is only a focus/status affordance because the CEO is already the default interface.

Slash commands are narrow shell controls only: scope navigation, local status/inspection, setup/config, debug/doctor, server start/stop, and exit. Product-building behavior belongs in skills, tools, and per-business state.

Business creation belongs to one shell command: `/create`. Creation-time choices such as test mode, immediate CEO start, and wake cadence should be flags on `/create`, not separate slash commands.

When the CEO is working, the shell must leave a visible operator lane. Never put spinners, carriage-return redraws, explanatory status text, or progress output on the input row. The active-run indicator is an animated braille spinner (`⠋⠙⠹…`) with a `thinking…` label on its OWN dedicated line below the (already-submitted) input — it redraws in place via `\r` and is fully cleared (`\r\x1b[2K`) on completion. This is safe because the agent turn runs with stdio silenced (`_silence_process_stdio`), so the spinner never interleaves with agent output; the response prints after the spinner clears. Frames/cadence are source-of-truth in `harness/settings.json` (`ui.thinking.frames`). Let streaming tool progress stand alone when progress lines are visible. If true mid-turn injection is not available, document that in help/status surfaces rather than cluttering the active input area.

## E2E Testing

For operator experience, test through the real shell path. Direct commands such as `./takyon create ...` are useful unit/smoke checks, but they do not exercise the interactive shell parser, slash command handling, scoped CEO routing, shell history, visible progress, input-lane behavior, or operator follow-up flow.

For isolated E2E tests, use a temporary workspace-local `TAKYON_HOME`, copy only the config needed to run the model, and launch `./takyon shell`. Run `/create` and follow-up inspection commands from inside the shell. Do not test by writing directly into Postgres control-plane tables or by bypassing the shell when the bug is about shell UX, progress, slash commands, scope, or operator conversation.

Use `/status`, `/pulse`, `/files`, `/read`, `/cron list`, and `/cron tick` inside the shell to verify state, receipts, product surface, pulse, filesystem visibility, and scheduled wake behavior. Keep test businesses in test mode unless the operator explicitly wants live side effects.

When the operator wants live monitoring, run the shell in a terminal visible to Codex or ask them to paste output. Codex can inspect the shared terminal output, but cannot see an unrelated external Terminal window unless its output is provided.

### Browser E2E is the final acceptance gate — on a brand-new business, every time

For the dashboard/product experience (`app.fourmanifold.com` and the live `<slug>.fourmanifold.com` product site), the **final** acceptance check for ANY change set is a **brand-new business created end-to-end through the browser UI**, exercised as a real user across every change. Poking at an existing business — signing in, re-running a tool, reloading a page — is **debugging/exploration only**; it is NOT the acceptance gate, because existing businesses were built under older code and skip the current bootstrap path. The loop is: batch fixes → deploy to BOTH hosts (operator + subuser) → create ONE fresh business in the browser → verify all changes on it as a user → fix what failed → create ANOTHER fresh business → repeat until clean. Never declare a change done on existing-business checks alone.

### Parallel build agents inherit the session model — never a cheaper tier

When fanning out implementation work to parallel agents (the Workflow tool or the Agent tool), **omit the `model:` parameter** so every lane inherits the session's model. Do not pin lanes to a different model — pinning `model: 'opus'` on a Fable 5 session is a downgrade, and the operator has explicitly ruled it out (2026-07-02: "stay on fable"). The invariant is: no lane ever runs on a tier below the session model.

## Agent Behavior

Do not stop at analysis because the workspace is dirty. There may be existing user or previous-agent edits. Read the relevant files, preserve unrelated changes, and make the requested change in the canonical location.

Do not begin with a verbose prompt restatement. If the request is actionable, inspect and act. If context is ambiguous, ask one concise question or state the assumption you are using.

When discussing a repo, package, website, downloaded artifact, or generated output, only describe contents you actually inspected. If local files are not present or you have not opened the relevant source, say that plainly. Do not guess what is inside, do not imply direct inspection when you only inferred from names/docs/marketing copy, and do not gaslight the operator about what you actually verified.

When in doubt:

1. Use the root `./takyon`.
2. Treat Hermes as CEO/runtime/cron owner.
3. Use archived reference material only from `hermes-agent-main/plugins/takyon/references/polsia3-skills/`.
4. Keep business state in the canonical Takyon/Hermes store and per-business filesystem.
