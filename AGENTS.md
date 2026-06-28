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

Takyon now has three VPS classes in production `NYC1 / default-nyc1`:

- Operator VPS: `137.184.75.57` (`argon-alpha-14`) — current public operator/runtime host
- Safebox VPS: `67.205.158.170` (`takyon-safebox`) — private secret/funding authority host
- Sub-user VPS: `134.209.123.8` (`takyon-subuser`) — public app/runtime host for product subusers

Use the local Codex deploy key at `~/.ssh/takyon_argon_alpha14` for root SSH when deployment work needs direct VPS access. That key is the current tracked default for operator, Safebox, and sub-user deploy scripts unless the operator explicitly swaps to host-specific keys. Do not assume the same unit files, open ports, or public role on the Safebox or sub-user hosts.

Sub-user hosts must never contain operator authority. `TAKYON_OPERATOR_DATABASE_URL` and `TAKYON_SAFEBOX_OPERATOR_TOKEN` are forbidden on the sub-user VPS in every form: not in `/opt/takyon/.takyon/.env`, not in `/opt/takyon/secrets/.env`, not in systemd unit environment, not in process environment, not in deploy-generated files, and not in any mirrored secret bundle. `UnsetEnvironment=` is defense-in-depth only; it is not permission to colocate those secrets on the sub-user plane. If either key is present on `takyon-subuser`, stop and remove the secret from the host/config/deploy source before testing or claiming sub-user isolation.

The public operator hostname is `app.fourmanifold.com`. DNS is managed outside this repo and should currently resolve to `137.184.75.57`; if it does not, treat that as DNS drift and fix the record outside this repo rather than papering over it in code. Product hosts such as `slug.coscale.app` are the sub-user plane; today the tracked operator edge terminates those shared hosts and proxies eligible traffic over the private VPC to the sub-user VPS, so do not paper over product-host issues with ad hoc per-business Caddy blocks or direct app-host hacks.

Production Postgres is a VPS-only rail. Do not run local Mac dashboard/worker/runtime processes against the production Takyon control-plane DSN, and do not leave a production `DATABASE_URL` wired into a local Takyon process "just for convenience". The tracked VPS services must set an explicit `TAKYON_HOST_ROLE`, and the dashboard embedded worker is opt-in only; the canonical production queue drain is the VPS `takyon-worker.service`, not a local dashboard thread. If an intentional non-VPS Postgres session is truly required for testing or one-off maintenance, make that override explicit and temporary rather than the default developer posture.

For `app.fourmanifold.com`, the intended operator experience is the embedded Takyon business/chat UI, not the plain dashboard Sessions shell. On the VPS, make sure `takyon-dashboard.service` starts the dashboard with `takyon dashboard --tui` or sets `TAKYON_DASHBOARD_TUI=1`. If the host shows `/sessions`, `Sessions`, `Models`, or `Logs` as the main landing view, treat that as a dashboard startup-mode misconfiguration on the VPS rather than a frontend deploy failure.

Firewall state is now part of the deployment contract:

- `argon-alpha` protects the operator VPS and should currently allow only:
  - `SSH 22` from `73.63.144.229/32`
  - `HTTP 80` from `All IPv4` and `All IPv6`
  - `HTTPS 443` from `All IPv4` and `All IPv6`
- `takyon-safebox-fw` protects the Safebox VPS and should currently allow only:
  - `SSH 22` from `73.63.144.229/32`
  - `TCP 8000` from private VPC CIDR `10.116.0.0/20`
- `takyon-subuser-fw` protects the sub-user VPS and should currently allow only:
  - `SSH 22` from `73.63.144.229/32`
  - `HTTP 80` from `All IPv4` and `All IPv6`
  - `HTTPS 443` from `All IPv4` and `All IPv6`
- Safebox must not expose a public app surface.
- Operator and sub-user hosts must not expose Docker APIs or worker-control ports publicly.
- If a firewall rule conflicts with the topology in `SAFETY_PLAN.md`, fix the firewall instead of weakening the code boundary to match the old rule.

When the operator asks to push or deploy Takyon, keep the rails distinct:

1. Git push uses the outer workspace repo at `/Users/Zygote/Downloads/takyon`, not the nested `hermes-agent-main` git metadata. Stage only the intended hunks, commit in the outer repo, and push `origin main` unless the operator asked for a branch.
2. Operator deploy updates the active operator runtime on `137.184.75.57`. The operator runtime is `/opt/takyon/hermes-agent-main` and may not be a git checkout, so use the tracked operator rails under `deploy/argon-alpha-14/`: bootstrap the host with `deploy/argon-alpha-14/bootstrap-host.sh` when needed, then deploy with `deploy/argon-alpha-14/deploy-runtime.sh`. The tracked operator contract now includes Docker because `business_claude_agent_task` defaults `product/site` work onto the isolated Docker rail; deploy must fail fast if Docker or the tracked Claude worker image is unavailable. Verify with `systemctl is-active takyon-dashboard.service`, `systemctl is-active takyon-worker.service`, `docker version`, and source checks on the operator VPS.
   The root-only `/usr/local/bin/takyon-op` launcher is an operator-VPS convenience installed only by this operator deploy rail. It wraps the existing operator CLI with the live operator env and scoped logs. Never install, rsync, document as available, or recreate `takyon-op` on the sub-user host; product subusers must not gain any operator CLI affordance.
3. Safebox deploy updates the dedicated Safebox service host. Do not piggyback secret authority changes onto the operator runtime and pretend the boundary exists if the Safebox host was not updated too.
4. Sub-user deploy updates the public app/runtime host. Do not claim product-app routing, `tkg_` isolation, or app-plane code changed in production unless that host was updated too.
5. VPS routing is now split across tracked deploy directories:
   - operator plane: `deploy/argon-alpha-14/Caddyfile` + `deploy/argon-alpha-14/takyon-dashboard.service`
   - sub-user plane: `deploy/takyon-subuser/Caddyfile` + `deploy/takyon-subuser/takyon-subuser.service`
   - Safebox plane: `deploy/takyon-safebox/takyon-safebox.service`
   Apply Caddy with the matching `apply-caddyfile.sh` on the target host. Do not hand-add new per-business Caddy blocks for normal businesses.
   The current sub-user runtime still serves existing static product hosts from `$TAKYON_HOME/product-sites`, so the tracked sub-user bootstrap/deploy path must keep syncing `product-sites` from the current operator source host until that surface moves to a different canonical backend.
6. Vercel deploy is the `app` project frontdoor only. It is not the canonical Takyon runtime and successful Vercel deploys do not prove prompt, skill, registry, or backend changes reached any VPS. Do not run `vercel deploy` from the workspace root; that uploads the wrong artifact. Use `vercel redeploy` against the current known-good `app` frontdoor production deployment, or deploy an equivalent tiny frontdoor artifact, then verify `vercel inspect app.fourmanifold.com` is Ready and aliased. Treat Vercel alias state separately from DNS: `app.fourmanifold.com` may still resolve to the VPS and return Caddy/uvicorn headers even when Vercel has a Ready alias.

Default deployment rule: if the change is code, tracked config, routing, UI, service units, or any other repo-owned artifact, push it through the outer git repo and the tracked deploy workflow first. Do not treat direct VPS edits, ad hoc `rsync`, hand-written host files, or any other untracked-on-host mutation as the normal deployment path. The narrow exceptions are secrets/env/provider-console state that intentionally lives outside git, plus explicit emergency rollback/hotfix requests from the operator; even then, backport the tracked change into the repo immediately and describe the out-of-band step plainly.

### Safebox authoritative gating — architecture + deploy runbook

**Authority principle (GOAL_RULES §0 — the one rule the boundary descends from):** authority is a
capability the safebox MINTS and VERIFIES — never inferred from possession of a shared secret, never
read from state the caller can write. A correct surface asks only "is this a valid capability the
safebox minted for exactly this action/account/cost?" — never "which plane is calling / what token does
it hold". Corollaries: (1) the shared `TAKYON_SAFEBOX_TOKEN` is transport REACHABILITY, not authority —
no spend or secret-egress may rest on it (every plane holds it); (2) the safebox never egresses or
accepts a write to its own authority secrets (`TAKYON_CAP_SIGNING_KEY`, `TAKYON_SAFEBOX_TOKEN`), and
`/v1/env` is an infra **allowlist** (`core.env_egress_allowed`, deny-by-default), not a denylist; (3)
ownership/allowance/balance state is writable only by the safebox's own DB role — the runtime gets a
NOBYPASSRLS non-owner role and money/identity writes go through SECURITY DEFINER funcs. When you touch
any safebox surface, re-derive nothing — apply §0. A red-team that finds an authority decision resting
on the token, a self-secret leaking over `/v1/env`, or a runtime-writable ledger is a §0 violation.

Invariant (GOAL_RULES, operator-mandated): **every paid provider call is money-gated AUTHORITATIVELY inside the safebox before any key resolves; there is no gating outside the safebox and no ungated path.** The safebox holds the keys, makes the provider call, and reserves→settles the right rail keyed on a VERIFIED capability scope. The gate always keys on one of the two CANONICAL accounts — the Takyon **user** (operator) or the product **sub-user** — never a synthetic "platform" account.

Rails (all enforced in `plugins/takyon/safebox_app.py` / `safebox_provider_proxy.py` on the safebox's own DB conn):
- **product sub-user AI** (anthropic/tavily) → usage reserve→settle (`_UsageLedgerAdapter` → `app_usage`), keyed on `{business, app_user}` from the single-use product capability. Routes: `/v1/providers/{anthropic/messages,tavily/search}`. Engaged by `TAKYON_PROVIDER_BROKER=1` on the runtime planes.
- **creative** (logo/UGC/static-ad: gemini/openai/fal) → creative-credit reserve→commit→release (`_CreditLedgerAdapter` → `business_credits`), priced from `core._creative_credit_total_cost`, keyed on the business; operator-owned via `authorize_operator_call`. Routes: `/v1/creative/{reserve,commit,release}` + gated `/v1/providers/{gemini/logo,openai/images,fal/{path}}`. There is NO ungated `/v1/proxy/{gemini,openai,fal}` — those were deleted.
- **operator/platform AI** (CEO loop / coding worker / operator web) → control-plane budget reserve→settle (`_OperatorBudgetAdapter` → `billing.py`), keyed on the REAL operator `takyon_user_id` (the business owner) carried by a REUSABLE `operator.session` capability (minted at `/v1/operator/session-token`, ownership-proven). Routes: `/v1/messages`, `/v1/proxy/{anthropic/messages,tavily/{op}}`; streaming settles actual cost parsed from the SSE usage event. Do NOT introduce a synthetic shared operator account — each operator meters its own `billing_accounts` row.

Deploy procedure (per the rails above — safebox routes run ONLY on the safebox host):
1. Stage only the intended files in the outer repo, commit, `git push origin main` (fetch-before-push; Joshua is a concurrent pusher). Verify the GitHub Actions run.
2. `rsync` the changed `plugins/takyon/safebox_*.py` (+ `creative_gateway.py`, subprocess scripts) to ALL THREE hosts (safebox `67.205.158.170`, operator `137.184.75.57`, sub-user `134.209.123.8`) under `/opt/takyon/hermes-agent-main/` with `~/.ssh/takyon_argon_alpha14`; `-ptz` (no `-E/-X`, no AppleDouble). `py_compile` the touched files.
3. `systemctl restart takyon-safebox.service` on the safebox; verify `is-active`, `/healthz`, and `/openapi.json` shows the gated routes (and that ungated `/v1/proxy/{gemini,openai,fal}` are 404).
4. E2E from the operator over the private IP `http://10.116.0.2:8000`: a product broker call settles ONE usage event (no double-charge); a creative call reserves→commits credits and is key-free; an operator call (session capability) increments the REAL operator's `allowance_used_cents`. Confirm no provider key appears in any response.
5. Migration (0037 etc.): apply on the VPS via the CLI (creds resolve from the safebox there), not from a local manual shell.

Remaining cutover steps (in order) to reach "no raw key on any runtime plane": (2) force the CEO loop + non-docker worker (`core.py:32230`) onto the proxy with an `operator.session` token + `ANTHROPIC_BASE_URL` = safebox; (3) move `TAKYON_PROVIDER_BROKER=1` into the tracked unit; (4) delete `/v1/env/{key,first,snapshot}` provider-key vending once no consumer calls it; (5) drop the `bypass=True` ledger conn (`runtime_app.py:231`), run ledger writes as `takyon_app`; (6) LAST: `git rm secrets/.env` + history scrub + rotate every key. The `/v1/env` egress + tracked `secrets/.env` (with the master `TAKYON_SAFEBOX_TOKEN`) are the highest-severity remaining exfil surfaces — they are independent of the money gate and must be closed before the cutover is "done."

When the operator explicitly asks for the production workflow, a live Hermes/UI-agent path, or an end-to-end browser proof on `app.fourmanifold.com` / `slug.coscale.app`, do not substitute localhost demos, lab homes, manual scaffolds, or local-only publish loops as the main path. Use the tracked production/business workflow first: the real Takyon worker path, the real business refresh/publish rails, the real VPS deploy path for the touched planes, and the visible in-app browser against the live host. If a production rail is blocked, say so plainly before using any fallback, and do not present a local proof as if it satisfied the requested production workflow.

The deployment workflow is tracked at `.github/workflows/deploy.yml` and is now multi-host aware when the corresponding GitHub secrets are configured. It should:

- always build the dashboard bundle and compile Python
- optionally redeploy Vercel frontdoor when `VERCEL_TOKEN` exists
- bootstrap the operator plane when provisioning/drift repair is needed via `deploy/argon-alpha-14/bootstrap-host.sh`
- deploy the operator plane via `deploy/argon-alpha-14/deploy-runtime.sh`
- deploy the Safebox plane via `deploy/takyon-safebox/deploy-runtime.sh` when `TAKYON_SAFEBOX_VPS_HOST`, `TAKYON_SAFEBOX_VPS_USER`, and `TAKYON_SAFEBOX_VPS_SSH_KEY` are configured
- deploy the sub-user plane via `deploy/takyon-subuser/deploy-runtime.sh` when `TAKYON_SUBUSER_VPS_HOST`, `TAKYON_SUBUSER_VPS_USER`, and `TAKYON_SUBUSER_VPS_SSH_KEY` are configured

Required operator secrets remain `TAKYON_VPS_HOST`, `TAKYON_VPS_USER`, and `TAKYON_VPS_SSH_KEY`. Optional Vercel metadata secrets remain `VERCEL_TOKEN`, `VERCEL_ORG_ID`, and `VERCEL_PROJECT_ID`.

Current deploy reality under the tightened firewall:

- GitHub-hosted runners cannot SSH into VPSes when the firewall allows `22` only from `73.63.144.229/32`.
- The tracked workflow now treats that as an expected skip instead of a hard failure: it still builds/compiles, then skips any remote deploy step whose host is unreachable from the runner.
- Until there is a reachable deploy runner or a firewall change, a green Git push does not by itself prove the operator/sub-user/Safebox hosts were updated. In that state, do the remote deploy from the allowed local machine or another explicitly allowed runner.
- When a workflow run skips remote VPS deploys, the canonical follow-up is to run the tracked local deploy scripts from this Mac before testing production or claiming the change is live: `deploy/argon-alpha-14/deploy-runtime.sh`, `deploy/takyon-subuser/deploy-runtime.sh`, and `deploy/takyon-safebox/deploy-runtime.sh` as needed for the touched planes.
- Do not weaken the firewall just to preserve the old GitHub-hosted SSH pattern unless the operator explicitly asks for that tradeoff.
- The dedicated Safebox service app now runs live on `10.116.0.2:8000`, and the tracked operator/sub-user systemd units point at it with `TAKYON_SAFEBOX_URL=http://10.116.0.2:8000`.
- The sub-user VPS now runs the tracked shared product-host Caddyfile and serves product hosts through `takyon-subuser.service`; if a product host falls back to the default Caddy page, treat that as a sub-user Caddy drift or missing `product-sites` sync, not as a frontend build issue.
- During `app.fourmanifold.com` / `slug.coscale.app` / in-app-browser E2E waits, do not sit idle. Keep polling both the visible browser state and the live VPS/backend/job/workspace state so you can tell the difference between a slow bootstrap, a stale preview, and a real production failure.

Fast path for ordinary code/docs/UI changes:

1. Run only the focused local checks needed for the touched surface, plus `git diff --check`.
2. Stage only intended files, commit in the outer repo, and `git push origin main`.
3. Immediately run `gh run list --repo tejdiv/takyon-workspace --branch main --limit 5`, then `gh run watch <run-id> --repo tejdiv/takyon-workspace --exit-status`.
4. If the workflow passes, report the run id and any direct smoke checks. Do not also do manual VPS/Vercel deploys.
5. If the workflow fails, inspect `gh run view <run-id> --log-failed`, patch the failing tracked rail, push again, and watch the new run. Use manual SSH/rsync only for emergency rollback or when the operator explicitly asks.

## Local Dev Rail

When the operator wants a persistent local Takyon dev environment, use one stable local-only root outside the repo, not a repo-owned `TAKYON_HOME` clone and not ad hoc workspace scratch by default. The canonical local dev root is `~/.takyon-fourmanifold-local-dev/` unless the operator explicitly chooses a different outside-repo path.

Use `scripts/takyon-local-dev.sh` as the default bootstrap/launch entrypoint for that rail so the local operator home and local Safebox authority stay on one canonical path.

Mirror the production shape as closely as practical on that local rail:

- keep a separate local operator runtime home and local Safebox authority instead of collapsing secrets back into random shell exports
- use the normal `./takyon` shell/CEO/business-tool path for operator work
- allow local-only exceptions only for public DNS/auth surfaces the operator already called out, such as `slug.coscale.app` and production Auth0 login

Local dev state under that outside-repo root is operator-local and must never be staged, committed, pushed, deployed, rsynced to a VPS, or described as repo-owned state, even under broad requests such as "push everything locally", "commit all changes", or "sync the whole workspace". Treat that rule as stronger than generic bulk-stage/push instructions unless the operator explicitly names a specific local file and says to promote it into tracked repo state.

Do not use workspace-root `.takyon-*` or `.tmp-*` homes as the normal persistent dev environment. If a one-off isolated repro needs a workspace-local scratch home, keep it disposable, never promote it to the canonical local rail, and never stage or push it.

Do not point the local dev rail at production Postgres or production Safebox "just to make it work". If a local run truly needs an explicit DSN or authority override, keep it local-only, explicit, and temporary.

## User Terms

Do not conflate Takyon users with product subusers.

- A Takyon user is the top-level operator/account that owns a Takyon agent and may create businesses.
- A product subuser/app customer is a customer of a business/product created by that Takyon user.
- Top-level user-scoped APIs, credentials, budgets, and identity are different from business/product customer auth, entitlements, usage, billing, and scoped API keys.

## Product App Surfaces

The shared Hermes app runtime owns backend rails only: auth/session protocol, payment/webhook reconciliation, entitlement policy, app usage budget accounting, state mirrors, and safety gates.

Do not hardcode the final product's look, layout, copy, theme, or information architecture in the runtime. Store that per business with the `business_upsert_app_surface_contract` tool, mirrored at `product/surface.md`, and point it at the business design brief/source path that the CEO and skills should inspect.

Do not leak internal Takyon/runtime ontology into shipped product copy unless the operator explicitly asks for that language. Customer-facing sites and apps should not ship headings or labels such as `What is real`, `What is local`, `shared Takyon runtime`, raw rail names, `browser-local`, or similar internal-explainer phrasing just to satisfy the no-pretend contract. Keep those distinctions in worker guidance, verification, blockers, receipts, and operator-facing diagnostics; express customer-facing limitations in normal product language instead of surfacing framework internals.

**Do not ship, publish, or present a runtime starter shell, fallback shell, scaffold shell, placeholder app, or recovery UI as if it were the product. If the real customer surface is missing, incomplete, or failing build/publish, keep the product blocked and report the exact blocker instead of serving a fallback page on the public slug.**

Backend rails for a product app should be declared once on the surface contract as `runtime_features`, not rediscovered separately in UI code or copied across skills. The canonical registry of known rails lives in `hermes-agent-main/plugins/takyon/core.py` (`PRODUCT_RUNTIME_RAILS`). Claude product-site work should receive the selected rails as injected worker contract guidance, and backend-owning skills should read the same selected rails from `product/surface.md` under `Runtime Rails`. When adding a new rail, extend the registry, then select it through `runtime_features`; do not create a second per-skill rail list.

When implementing custom backend or own-runtime product capability later, update the authoritative Takyon discovery surfaces the CEO can actually see: the stable CEO prompt at `hermes-agent-main/plugins/takyon/prompts/ceo.md`, the owning Hermes skill under `hermes-agent-main/skills/takyon/`, and the guarded product surface refresh/publish tool behavior. Do not add a hidden backend path that the CEO cannot see through skill frontmatter/body, tool schemas/results, or business receipts.

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

## Operator Budget Rails

For the operator/dashboard path, the canonical spend gate is the top-level Takyon user billing rail, not a create-time business cap:

- Public dashboard/operator turns should resolve to one Postgres-backed Takyon user principal and reserve/settle against that user's control-plane budget.
- `/create`, `/wake`, and normal operator chat must use that same per-user budget gate and ownership boundary.
- Do not reintroduce legacy create-time bootstrap caps or `--budget` as a normal operator path. The old `business.budget_json` cap is legacy compatibility state, not the intended budgeting model.
- Do not expose user-editable wake cadence or legacy budget fields in the dashboard create UI. The operator UI may show read-only budget state and can rely on backend/default wake policy, but should not present those as normal editable setup knobs.
- For business/product runtime spend, use the real downstream rail instead: `app_usage.py` for product usage budgets, and the creative-credit rail for fixed-price creative/ad actions.
- The tracked operator services now run with `TERMINAL_ENV=docker`, `TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE=true`, and non-persistent containers so the existing terminal/file sandbox backend keys itself off the Takyon session and mounts the current business scratch workspace instead of reusing one process-global default container for scoped business work. Treat Docker on the operator host as a tracked prerequisite, not an optional convenience: if the host cannot run the tracked Claude worker image, operator deploy is broken and should fail loudly.

## Safebox Authority

Safebox is the canonical authority boundary for secrets, auth, funding, and spendful tool control.

- All secrets, API keys, OAuth tokens, session auth state, funding authority, billing authority, credit balances, paid-provider credentials, and tools/actions that can directly spend money or consume paid credits/budgets must live behind Safebox-owned rails.
- Paid-provider secret keys live behind TK (the safebox) — NEVER read from `os.environ` in a business tool or skill. Every secret that lets a business tool call a PAID provider — model/image/video APIs (Gemini, FAL, OpenAI gpt-image), search/extract (Tavily), social/ads (Composio) — is held by the safebox (private secret-authority host `67.205.158.170`), not in the business runtime. On the VPS the key is NOT in the process env: `os.environ.get("GEMINI_API_KEY")` is empty there, so a tool that reads its key from `os.environ` both breaks the secret boundary AND fails closed at runtime (observed: a logo rewrite that read `os.environ` raised `"GEMINI_API_KEY required"` on prod and never worked). Canonical pattern (`GOAL_RULES` §7) — see `plugins/takyon/creative_gateway.py::_resolve_gemini_image_key`:
  1. Resolve the key ONLY in an authority route via `safebox.first_env_backed_value(*ALIASES)` (e.g. aliases `TAKYON_GEMINI_API_KEY` / `GEMINI_API_KEY` / `GOOGLE_API_KEY`); the business runtime never reads the raw key.
  2. Pass it as an EXPLICIT argument to the provider client (`genai.Client(api_key=...)`), never via `os.environ`.
  3. Fail closed: if the key is absent, return a clear `*_unconfigured` error / `503` BEFORE any credit reserve or provider call — never proceed or fabricate.
  4. Register the alias in `core._API_ENV_ALIASES` and declare `requires_api=[...]` on the tool; gate the spend (creative-credit or usage rail) in the same authority route.
  Any NEW paid-provider skill/tool resolves its key this way — same as logo (`business_generate_logo`), UGC, static-ad, and web-search. Reading a provider key from `os.environ` in a business tool is a bug: it breaks the secret boundary and fails on the VPS.
- Non-Safebox tools, skills, prompts, UI code, workers, and business files may orchestrate around those rails, but they must not directly create, edit, mint, rotate, reveal, persist, or bypass that authority.
- Do not duplicate secret/auth/funding state into prompts, skill-local stores, business files, client-editable payloads, ad hoc env mirrors, or alternate mutable tables just because a higher-level flow needs to read or route on it.
- A path does not become acceptable merely because inference, routing, or a worker call passed through Safebox first; if the mutable target is secrets/auth/funding/spend authority, the canonical write and enforcement point must still remain inside Safebox and be uneditable by every other tool or skill.
- When adding a new provider, credentialed backend, or money-costing tool, put the credential gate, spend permission, reservation/settlement authority, and irreversible side-effect approval in Safebox first; then expose only the minimum guarded interface and receipts to the rest of Takyon.
- If an existing tool or skill can directly mutate those surfaces outside Safebox, treat that as a bug to remove, not an allowed second path.

## Operating Model

Takyon should be a skill-based Hermes CEO system, not a fixed workflow cockpit.

Practice parsimony and no slop. Parsimony means the system works through the smallest Hermes-native surface that genuinely handles the job for every business and every relevant mode. Do not create a second path for test mode, a one-off shell workaround, a special-case business bootstrap, or a fake/demo product behavior when the same skill, business-scoped tool, receipt, or runtime rail can express the work with the right gates. Mode differences should be explicit guardrails on the same path: in test mode suppress or stub external side effects with receipts; in live mode require the real provider, budget, permission, and receipt. Prefer one clear operator affordance with flags over multiple overlapping commands, aliases, or workflow shortcuts. If a shell action is part of creating a business, put it on `/create` as an explicit flag such as `--test`, `--schedule`, or `--no-auto`; do not add separate slash commands that duplicate the creation path.

Skill-based does not mean every fix belongs in skill prose. If the real missing piece is tool registration, provider config, harness metadata, a guarded runtime rail, or a UI renderer, fix that canonical surface and let the existing skills use it; edit or add a skill only when the business-facing method, judgment, or routing behavior genuinely changes.

When behavior is wrong, find the upstream cause before adding a downstream rule, label, report field, UI shim, or prompt reminder. A fix that merely records, hides, or explains a bad decision is a band-aid unless the canonical surface that enabled the bad decision has been corrected. First identify what source of truth, tool schema, receipt, capability read, guardrail, or runtime rail was missing, misleading, or unenforced; then make the smallest Hermes-native change at that surface. Add downstream visibility only after the upstream behavior is truthful.

When replacing a UI read path or first-paint renderer, do not preserve legacy dual-read, fallback hydration, or superseded render branches unless the operator explicitly asks for compatibility. Remove the old first-paint path in the same change so the shell has one authoritative bootstrap contract.

Do not add deterministic business-action routers, fixed if/then workflow funnels, or forced artifact shortcuts to make the CEO pick a business move. Encourage the CEO through the Hermes skills index, skill guidance, tool schemas, capability/read tools, exact gate errors, and regression tests that catch bad substitutions. Deterministic code is appropriate only for durable safety and integrity rails such as scope isolation, path containment, idempotency, credential checks, budget caps, audit receipts, kill/pause controls, and UI rendering mechanics.

Use canonical sources of truth wherever they exist. Runtime config belongs in `$TAKYON_HOME/config.yaml`; the CEO runtime prompt belongs in `plugins/takyon/prompts/ceo.md`; Takyon skill metadata belongs in Hermes `SKILL.md` frontmatter under `skills/takyon/`; shell/harness command metadata belongs in `plugins/takyon/harness/settings.json` and `plugins/takyon/harness/commands/*.md`; business facts belong in the Postgres control plane plus the canonical per-business object-store workspace. On the operator host, the local materialization is cache/scratch only: durable business files live in the configured storage backend, host cache lives under `$TAKYON_HOME/cache/businesses/`, and per-run disposable scratch lives under the isolated workspace mounts/`/tmp/takyon-workspaces`. Do not duplicate those facts in prompts or UI code when they can be read from the canonical source.

Before adding a new store, command, prompt rule, metric file, or workflow, check whether the active Takyon/Hermes trunk already has a canonical source that does the job. If it does, tell the operator and use or point to that source instead of creating a duplicate.

Do not answer diagnosis with artifact edits. If the operator asks why something happened, whether behavior is correct, or how to improve it, inspect the relevant source of truth and answer. Do not patch a generated business website, outreach file, app copy, or local artifact unless the operator explicitly asks to change that artifact. If the fix is systemic, make the smallest Hermes-native change in the relevant skill, tool, harness metadata, or AGENTS instruction.

When a bug shows up on a specific subuser app, default to fixing the canonical shared AppKit/runtime/publish path that future apps inherit. Do not spend normal diagnosis/fix time polishing or hand-repairing one existing subuser app surface unless the operator explicitly asks for a one-off rescue, validation requires a minimal business-surface adjustment, or the canonical path cannot express the needed fix.

`AGENTS.md` is not a CEO runtime prompt. Do not put business strategy, wake-loop policy, product judgment, outreach policy, or per-business operating instructions here. Runtime behavior belongs in the CEO prompt, Takyon skills, guarded business tools, harness command metadata, cron prompts, and per-business `research/`, `product/`, `distribution/`, and `metrics/` state.

Hardcode only durable rails and safety primitives: business isolation, path containment, idempotency, credential gates, budget caps, audit events, pause/resume/kill controls, conservative cleanup, and the shared Hermes app runtime APIs. Strategy, prioritization, product direction, outreach motion, design, and learning belong in per-business `research/`, `product/`, `distribution/`, and `metrics/` state plus active Takyon skills.

When changing CEO/runtime behavior, edit the stable CEO prompt at `hermes-agent-main/plugins/takyon/prompts/ceo.md`, the active Takyon skills under `hermes-agent-main/skills/takyon/`, and the relevant tool/cron/harness source. In particular:

- `plugins/takyon/prompts/ceo.md` is the stable state-aware router prompt.
- The bounded Claude Agent SDK worker lane for business-scoped workspace edits is the `business_claude_agent_task` tool (registered in `plugins/takyon/core.py` and `plugins/takyon/plugin.yaml`, running `hermes-agent-main/scripts/takyon-claude-agent-task.mjs` in a hardened docker sandbox). It is a coding worker, not a Takyon skill, so do not look for a `takyon-claude-agent-sdk` `SKILL.md` in the Hermes skills index.
- Do not reintroduce generic Hermes `delegate_task` into normal Takyon CEO runs; the coding worker lane above is the one special worker path.
- `takyon-app-runtime` and the canonical app tools own product customer auth, magic links, sessions, app customers/subusers, entitlements, plan policy, checkout, Stripe webhook reconciliation, revenue, and usage budgets.
- `takyon-business-metrics`, `takyon-market-research`, `takyon-build-product`, `takyon-distribution`, `takyon-x`, `takyon-reddit`, and `takyon-conversation-followup` own the remaining active business methods.

When the operator asks to add a normal new Takyon feature or skill, always use the parsimonious addition path:

1. Add `hermes-agent-main/skills/takyon/<new-feature>/SKILL.md`.
2. Start from `hermes-agent-main/skills/takyon/SKILL-TEMPLATE.md` and keep the canonical section order unless there is a strong reason not to.
   New skill folder shape:
   `hermes-agent-main/skills/takyon/<new-feature>/SKILL.md` plus optional `references/`, `templates/`, `scripts/`, and `assets/` inside that same skill directory.
   `hermes-agent-main/skills/takyon/SKILL-TEMPLATE.md` and `hermes-agent-main/skills/takyon/BUILDING-SKILLS-AND-TOOLS.md` are authoritative. If a future skill or tool needs a different authoring shape, update those documents in the same change before adding the divergent skill or tool.
3. Put canonical routing and readiness metadata in the skill frontmatter, including `metadata.hermes.*` plus any `metadata.hermes.requires_toolsets` / `requires_tools` gating, and `metadata.takyon.allowed_roots` / `output_root` / `publication`. Frontmatter must be valid YAML; there is no Takyon-specific fallback parser.
4. Add `references/`, `templates/`, `scripts/`, or `assets/` only when the skill truly needs them.
5. Add or modify a `business_*` tool only if the feature needs a new canonical state change, guarded side effect, provider call, receipt, budget gate, or durable runtime rail. The skill should name the tool in its body, but the tool must exist in code. If no existing tool fits, create the new tool in the same format documented in `hermes-agent-main/skills/takyon/BUILDING-SKILLS-AND-TOOLS.md`.
6. Add a harness command only if the operator needs `/new-feature` as a shell affordance; do not create slash commands for ordinary CEO-choosable business methods.
7. **Relaunch so the skill actually syncs — then verify the sync took effect; never assume it did.** Editing files under `hermes-agent-main/skills/takyon/` does not touch a running shell. On every `./takyon` startup, `tools/skills_sync.py::sync_skills()` copies the repo's bundled `skills/` into `$TAKYON_HOME/skills/` (here `/Users/Zygote/Downloads/takyon/.takyon/skills/`), and Hermes builds its skills index from that copied tree, not from the repo. So a fresh `./takyon` run (or shell relaunch) is required before any skill edit is live — that is the whole reason for this step.
   The copy is manifest-tracked (`$TAKYON_HOME/skills/.bundled_manifest`) and deliberately conservative, which is the part that trips people up:
   - A brand-new skill folder (never synced before) is copied in cleanly on the next relaunch.
   - A skill that already exists in `$TAKYON_HOME/skills/` and differs from its recorded baseline (you edited the repo copy after it was first synced, or it was hub-installed/custom) is SKIPPED to protect local changes: the sync keeps the on-disk copy and prints `Run \`takyon skills reset <name>\``. Relaunch alone will NOT propagate a repo edit to an already-synced skill.
   - Plain `takyon skills reset <name>` only re-baselines the manifest against the current on-disk copy (it clears the "user-modified, skipping" flag); it does NOT pull your repo version onto disk. To force the repo version, run `takyon skills reset <name> --restore` (this DELETES the on-disk copy and re-copies the bundled/repo version), then relaunch.
   **Always check the sync took effect before reporting the skill as working.** After relaunching, confirm: (a) `$TAKYON_HOME/skills/takyon/<name>/SKILL.md` exists and matches the repo edit, (b) the skill appears in the Hermes skills index / `takyon skills list`, and (c) no "kept local copy / user-modified, skipping" warning was printed for that skill during startup. If it was skipped, run `takyon skills reset <name> --restore`, relaunch, and re-verify. Do not claim a skill is live on the strength of the repo edit alone.
8. Do not edit the CEO prompt unless the CEO's general policy, routing rule, or safety contract changes.

For every new feature, skill, or tool, first understand when it should be used and verify that the existing Takyon piping will let it be used in those places without hardcoding a deterministic workflow. Check the relevant routing and discovery surfaces: the initial/bootstrap prompt, `plugins/takyon/prompts/ceo.md`, related skills' `SKILL.md` files, the runtime Hermes skills index, shell/harness metadata, and any canonical tools or runtime rails. Update only the surfaces that genuinely need to know about the feature; do not duplicate routing rules across prompts, skills, or UI code.

Takyon skills must keep rich normal-Hermes operational detail. Do not collapse `How to Run`, `Procedure`, or `Verification Checklist` into generic prompt prose. Those sections should explicitly name the exact tool names used, the files or state checked first, the expected outputs, the branch points for test/live or present/missing state, and the receipts or file paths that prove success. Converting a good Hermes skill into a Takyon skill means adding `metadata.takyon.*` and `## Publication`, not removing the concrete operational detail.

When a skill or tool claims to create, update, publish, launch, charge, or otherwise mutate business or provider state, every mutating path must leave canonical durable truth, including test-mode suppressed paths and update paths. That durable truth does not have to be a row in the `ledger` table specifically: it may be a shared-store commit such as `ledger.allocate` or `agent.record`, a guarded `business_*` tool receipt/event/state write, or an exact `business_*` tool payload that the agent must call in the same turn after a local script prepares files. Read-only, planning, and draft-only paths may remain advisory. But do not allow a mutating path to stop at provider readback, stdout, or ad hoc file output without a canonical Takyon receipt, event, or tool-backed commit.

Before adding a feature, skill, tool, store, command, metric, or prompt rule, perform a redundancy and conflict check against the active Takyon/Hermes trunk. Report to the operator whether the proposal is new, partially redundant, or better implemented by extending an existing skill/tool/rail; name the overlapping surfaces and explain the chosen canonical home. Also check whether the change conflicts with initial bootstrap, scheduled wakes, manual CEO turns, work focus, test/live mode, business isolation, app-runtime rails, cron behavior, or slash-command policy. If the feature is valid, describe the non-deterministic call points where the CEO or shell can discover and use it through the Hermes skills index, tool schemas, metrics evidence, business state, or related skill guidance.

Before implementing a new feature, prove or ask whether it needs new provider access, API keys, OAuth apps, paid services, network scraping, external posting/sending, deploy credentials, model/video/image generation access, billing rails, or budget authority. Report the exact required environment variables, provider accounts, expected side effects, test-mode behavior, and live-mode gates. If the feature can work locally without new credentials, say that explicitly. If live functionality would be degraded or blocked without credentials, implement the local/guarded path only and record the missing provider gate instead of pretending it works.

External side-effect semantics belong in guarded business tools and the relevant skills. Do not duplicate them in `AGENTS.md`, shell prose, or one-off prompts.

Archived `polsia3` reference files may mention fixed workflow IDs or old workflow catalogs. Treat those as historical source material only. Do not copy that architecture back into the active Takyon/Hermes system.

## Test Mode

Test-mode semantics belong in the Takyon tools, skills, and harness metadata, not in `AGENTS.md`. The canonical state is `businesses.mode` in the Postgres control plane, changed through `business_set_mode` or the shell command `/test on|off|status`. Do not add parallel per-channel test flags or duplicate test-mode behavior in prompts.

## Hermes-Style Takyon Work

Implement Takyon behavior the Hermes way: skills describe operating modes, tools provide guarded state changes/side effects, and the business filesystem records durable context. Do not add local deterministic business flows, one-off worker stages, hardcoded startup sequences, or UI-only command lists when a skill, harness command file, or business-scoped tool can express the behavior.

When modifying artifact/reporting surfaces, keep business deliverables visible through tool results, shell progress, or concise reports. Paths should come from canonical tool results and filesystem roots, not invented shell text.

Default rule: everything business-facing should be a Takyon skill discoverable through the Hermes skills index or a business-scoped tool visible through tool schemas/results. The clear exceptions are shared safety/control rails: path containment, idempotency, credential checks, budget gates, pause/kill controls, cron wake scheduling, auth/session/payment/webhook protocol, and UI rendering mechanics. Even those exceptions should publish their user-facing command metadata through a single source of truth rather than separate UI hardcoding.

Slash-command UI must be source-of-truth driven. Shell palettes/help should derive from `hermes-agent-main/plugins/takyon/harness/settings.json` for controls, `hermes-agent-main/plugins/takyon/harness/commands/*.md` for harness skill commands, and Hermes skill discovery for Takyon skills. If a future command or skill is added, updating that canonical source must be enough for shell discovery; do not duplicate slash command names/descriptions in the UI.

## Takyon Shell Model

The Takyon shell is always in a scope. `global` is the top-level account/root scope; it is not the CEO. A business scope is `business:<slug>`.

The CEO is the scoped operator agent role. Plain text in the shell should route to the CEO for the current scope, except obvious shell self-help questions such as "how do I create a business", which should answer locally and tersely. The shell must preserve recent in-session turns so follow-ups can resolve against recent context; tune that through `harness/settings.json`, not hardcoded prompts. `/ceo` is only a focus/status affordance because the CEO is already the default interface.

Slash commands are narrow shell controls only: scope navigation, local status/inspection, setup/config, debug/doctor, server start/stop, and exit. Product-building behavior belongs in skills, tools, and per-business state.

Business creation belongs to one shell command: `/create`. Creation-time choices such as test mode, immediate CEO start, and wake cadence should be flags on `/create`, not separate slash commands.

When the CEO is working, the shell must leave a visible operator lane. Do not use spinners, carriage-return redraws, explanatory status text, or progress output that occupies or clears the input row. The active-run indicator should be minimal and visual: use a blinking `*` only when it can be cleared on completion, and let streaming tool progress stand alone when progress lines are visible. If true mid-turn injection is not available, document that in help/status surfaces rather than cluttering the active input area.

## E2E Testing

For operator experience, test through the real shell path. Direct commands such as `./takyon create ...` are useful unit/smoke checks, but they do not exercise the interactive shell parser, slash command handling, scoped CEO routing, shell history, visible progress, input-lane behavior, or operator follow-up flow.

For ordinary local operator E2E, prefer the stable outside-repo local dev rail above. Use a temporary workspace-local `TAKYON_HOME` only for one-off isolated repros, keep it disposable, and never stage or push it. In both cases, launch `./takyon shell` and run `/create` plus follow-up inspection commands from inside the shell. Do not test by writing directly into Postgres control-plane tables or by bypassing the shell when the bug is about shell UX, progress, slash commands, scope, or operator conversation.

Use `/status`, `/pulse`, `/files`, `/read`, `/cron list`, and `/cron tick` inside the shell to verify state, receipts, product surface, pulse, filesystem visibility, and scheduled wake behavior. Keep test businesses in test mode unless the operator explicitly wants live side effects.

When the operator wants live monitoring, run the shell in a terminal visible to Codex or ask them to paste output. Codex can inspect the shared terminal output, but cannot see an unrelated external Terminal window unless its output is provided.

### Browser E2E is the final acceptance gate — on a brand-new business, every time

For the dashboard/product experience (`app.fourmanifold.com` and the live `<slug>.coscale.app` product site), the **final** acceptance check for ANY change set is a **brand-new business created end-to-end through the browser UI**, exercised as a real user across every change. Poking at an existing business — signing in, re-running a tool, reloading a page — is **debugging/exploration only**; it is NOT the acceptance gate, because existing businesses were built under older code and skip the current bootstrap path. The loop is: batch fixes → deploy to BOTH hosts (operator + subuser) → create ONE fresh business in the browser → verify all changes on it as a user → fix what failed → create ANOTHER fresh business → repeat until clean. Never declare a change done on existing-business checks alone.

### Parallel build agents run on Opus 4.8

When fanning out implementation work to parallel agents (the Workflow tool or the Agent tool), pin them to **Opus 4.8** (`model: 'opus'`). Do not let a parallel lane silently run on a cheaper tier.

## Agent Behavior

Do not stop at analysis because the workspace is dirty. There may be existing user or previous-agent edits. Read the relevant files, preserve unrelated changes, and make the requested change in the canonical location.

Do not begin with a verbose prompt restatement. If the request is actionable, inspect and act. If context is ambiguous, ask one concise question or state the assumption you are using.

When discussing a repo, package, website, downloaded artifact, or generated output, only describe contents you actually inspected. If local files are not present or you have not opened the relevant source, say that plainly. Do not guess what is inside, do not imply direct inspection when you only inferred from names/docs/marketing copy, and do not gaslight the operator about what you actually verified.

When in doubt:

1. Use the root `./takyon`.
2. Treat Hermes as CEO/runtime/cron owner.
3. Use archived reference material only from `hermes-agent-main/plugins/takyon/references/polsia3-skills/`.
4. Keep business state in the canonical Takyon/Hermes store and per-business filesystem.
