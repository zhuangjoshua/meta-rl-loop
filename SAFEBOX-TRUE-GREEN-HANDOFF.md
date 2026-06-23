# Safebox secret-boundary / money-gate hardening — continuation handoff

> **➤ SUPERSEDED BY `SAFEBOX-TRUE-GREEN-GOAL.md`** — that file is the canonical `/goal` spec (corrected current
> state + the mint/spend-split authorization design + ordered remaining work). Use it. This doc remains for
> extended background only; where they differ, the GOAL file wins (notably: the mint split is **NOT** in flight,
> it is the immediate next step).

**For:** the next Claude Code instance picking this up.
**Date:** 2026-06-23. **Repo:** `tejdiv/takyon-workspace` (outer). **Branch:** `main`. **HEAD at handoff:** `d2f0d76b`.

---

## 0. What this work is (one paragraph)

Make the **safebox** (`67.205.158.170`, private `10.116.0.2:8000`) the *sole* secret + money authority so that an
adversary who has **root on every other plane** (operator dashboard/worker `137.184.75.57`, sub-user runtime
`134.209.123.8`, all deployed code, all infra secrets incl. the shared `TAKYON_SAFEBOX_TOKEN` and the runtime
`DATABASE_URL`) — plus malicious product sub-users — still **cannot** (I1) obtain a raw provider key or any
authority secret, nor (I2) cause paid spend / mint money beyond what the safebox authorizes. A 39-agent
red-team found the boundary BROKEN in three ways (G1/G2/G3); we are closing them + all residuals to **"true
green" = no authority-equivalent secret vends over `/v1/env` AND no exploitable I1/I2 finding**.

## 1. The driving "spec" — read these FIRST, in order

There is **no formal `goal.md` LFD harness** for this security work. It is driven by a conversational `/goal`
Stop-hook whose condition is *"finish this for true green"* (G3 cutover + every residual moved server-side +
deploy + fresh-business E2E + a final clean 10-agent audit). The canonical written spec the code descends from is:

1. **`hermes-agent-main/GOAL_RULES.md`** — THE canonical rules. **§0 is the authority principle** (the spine,
   below); §1 secret boundary; §2 the three money rails; §3 per-user budget; §7 paid-key resolution; plus the
   deploy runbook + ordered remaining steps. Code comments cite these as `GOAL_RULES §N`.
2. **`AGENTS.md`** (outer repo root) — the "Safebox authoritative gating — architecture + deploy runbook"
   section now opens with the §0 authority principle, and has the 3-host deploy rails.
3. **`safebox-threat-model-codex-checklist.md`** (outer root) — the **verification runbook** (rewritten to be
   *verify-only*: Codex runs probes + reports pass/fail; it does NOT fix or rotate). Use this to confirm any gate.
4. **`CLAUDE.md`** (outer root = workspace rules) and **`hermes-agent-main/CLAUDE.md`** (the agent dev guide:
   how to add tools/skills, the paid-key pattern, the test wrapper `scripts/run_tests.sh`, the no-change-detector-
   tests rule). **Deploy rails, parsimony, "find the upstream cause" are mandatory.**
5. **Memory notes** (`/Users/Zygote/.claude/projects/-Users-Zygote-Downloads-takyon/memory/`):
   - `project_safebox_threat_model_gaps.md` — the 3 gaps + status + the G3 cutover-viability (pooler) finding.
   - `project_safebox_vending_not_broker.md` — the original red-team that started this.
   - `project_vps_deploy_access.md` — the 3 hosts + the ONE ssh key + the rsync recipe.
   - `feedback_deploy_push_main_too.md`, `feedback_no_git_stash_concurrent_repo.md`,
     `feedback_parallel_agents_opus.md`, `feedback_new_business_e2e_per_fix.md`,
     `feedback_zero_shot_dashboard_bootstrap.md` — deploy + process rules.

## 2. The authority principle (GOAL_RULES §0 — internalize this)

> **Authority is a capability the safebox MINTS and VERIFIES — never inferred from possession of a shared
> secret, and never read from state the caller can write.** A correct surface asks only "is this a valid
> capability the safebox minted for exactly this action/account/cost?" — never "which plane is calling / what
> token does it hold."

Corollaries (each is a gap): (1) the shared `TAKYON_SAFEBOX_TOKEN` is *transport reachability*, not authority;
(2) the safebox never egresses or accepts a write to its own authority secrets — `/v1/env` is an infra
**allowlist**, not a denylist; (3) ownership/allowance/balance state is writable only by the safebox's own DB
role (runtime gets a NOBYPASSRLS non-owner role; money/identity writes go through SECURITY DEFINER funcs).

## 3. Threat model (the lens for every change + the audit)

Attacker has root on every plane EXCEPT the safebox host; holds `TAKYON_SAFEBOX_TOKEN` + runtime `DATABASE_URL`
+ all infra creds; crafts arbitrary HTTP to the safebox. Separately, evil sub-users hit the product runtime.
Invariants: **I1** secret confidentiality (no raw provider key or authority secret), **I2** money integrity
(no spend/mint beyond the safebox gate; no forged capability; no charge to another account).

## 4. What is DONE (committed; deploy state noted)

| Gap / residual | Commit | Deployed to prod? |
|---|---|---|
| **G1** `/v1/env` → exact-name infra allowlist; never-vend/never-write the safebox's own authority secrets (`TAKYON_CAP_SIGNING_KEY`, `TAKYON_SAFEBOX_TOKEN`, `STRIPE_BILLING_WEBHOOK_SECRET`, `SUPABASE_SERVICE_ROLE_KEY`); symmetric write gate (any sensitive key → 403, case-folded) | `eb7c53ea` (v1) + `b486c2a3` (v2) | **YES — live-proven on the safebox host** (signing-key read 200→404; write clobber→403) |
| **G2** operator proxy is capability-only; removed the bare-token `enforce_ceiling=False` spend path | `eb7c53ea` | **YES — live-proven** (bare-token `/v1/messages`→401) |
| Residual **`SUPABASE_JWT_SECRET`** alg-confusion: `verify_supabase_jwt` no longer verifies HS* with the ambient symmetric secret (validates server-side via Supabase Auth / JWKS); dropped from allowlist | `90aa186d` | **NO** — changes sub-user login; deploy WITH the E2E |
| Residual **`STRIPE_WEBHOOK_SECRET`**: sub-user app-webhook verifies on the safebox (`POST /v1/stripe/app-webhook/verify`); dropped from allowlist | `d2f0d76b` | **NO** |
| **G3 FOUNDATION** migration `0038_runtime_least_privilege.sql` (INERT, NOLOGIN role): `takyon_runtime` (NOSUPERUSER/NOBYPASSRLS), grant-all-revoke-dangerous on the money tables + column-revoke `businesses.owner_user_id`; SECURITY DEFINER billing/credits/custody funcs; `billing.py`/`business_credits.py`/`custody.py`/`ledger_gate.py` route through them | `3374ca2c` | **NO** (inert until cutover) |

**Verified facts you can rely on:** RLS bypass is via the `takyon.rls_bypass` GUC, NOT the role's BYPASSRLS
attribute (so NOBYPASSRLS `takyon_runtime` is safe — `GRANT takyon_app TO takyon_runtime` keeps `_pg_app_scope`
working). The runtime connects as Supabase superuser `postgres` via the legacy pgbouncer `db.<ref>:6543`
(auth_query) — a custom `takyon_runtime` LOGIN role with a password **can** authenticate through it (direct
`:5432` is the fallback). 205 authoritative+safebox tests green; 47 money pg-tests pass through the routed path.

## 5. NOT STARTED — the immediate next step (correction)

**G3 mint-to-safebox refactor** (the cutover-gating fix) is **NOT done and NOT in flight** — an earlier version of
this handoff wrongly described it as an in-flight background agent; no such agent was launched. Verified in the
committed tree (`3374ca2c`): `0038` has **no `revoke execute`** anywhere and still **grants the mint funcs to
`takyon_runtime`** (billing 424-425, credits 701-702, custody 841-843), and `safebox_app.py` has **only
`/v1/creative-credits/*`** routes (no `/v1/billing/*` or `/v1/custody/*`). So an attacker as `takyon_runtime`
could still mint by CALLING `safebox_billing_grant_allowance` / `safebox_credits_grant` / `safebox_custody_accrue`
/ `safebox_custody_payout`.

**Do this as a focused, PG-validated change (it gates the cutover):** add safebox `/v1/billing/*` + `/v1/custody/*`
**mint** routes mirroring `/v1/creative-credits/*`; route the *mint* calls in `billing.py`/`custody.py` through them
(creative-credits already route this way); add `revoke execute on <mint_fn> from takyon_runtime` to `0038` so the
runtime keeps only the **spend** funcs (reserve/settle/refund/commit/release, balance-bounded); update the
`inv4`/`inv5` source-inspection tests to assert the guard lives in the SQL func. Validate on a throwaway PG rig.

## 6. What REMAINS for true green (ordered)

1. **Integrate the mint refactor** (above) → then **G3 CUTOVER** (the one prod-risky live step):
   - Apply `0038` on prod (inert; creates the role+funcs+revokes). Migrations apply via the CLI on the VPS (the
     runner resolves creds from the safebox). 0038 is safe to apply while the runtime is still `postgres`.
   - `rsync` the routed `billing.py`/`business_credits.py`/`custody.py`/`ledger_gate.py` + safebox routes to the
     planes (apply 0038 FIRST, else the routed code calls a missing func). Restart services.
   - **Provision `takyon_runtime` LOGIN + password** (psql as owner; the password is a NEW internal credential —
     not a key rotation): `ALTER ROLE takyon_runtime LOGIN PASSWORD '<gen>';` Put the new DSN
     (`postgres://takyon_runtime:<pw>@db.<ref>.supabase.co:6543/postgres`) in the safebox-served `DATABASE_URL`
     for the **runtime planes only** (the safebox keeps the `postgres` DSN). Swap → restart.
   - **Re-probe** (D-section of the checklist): runtime `current_user` `rolbypassrls=f`; `BEGIN; UPDATE
     billing_accounts/business_creative_credit_accounts ... ; ROLLBACK;` → permission denied; mint funcs under
     `takyon_runtime` → InsufficientPrivilege; legit reserve/settle/grant via safebox routing still work.
2. **AUTH0 residual** (highest remaining severity — operator impersonation): `AUTH0_CLIENT_SECRET`/`AUTH0_SECRET`
   are fetched by the dashboard (`web_server.py:_auth0_dashboard_config`) for server-side OAuth code-exchange +
   session-cookie signing. Move BOTH onto the safebox (a `/v1/auth0/*` exchange+sign route, like the webhook
   pattern) so the dashboard plane never holds them; drop from the allowlist. **Validate operator login E2E.**
3. **Remaining residuals** (lower severity; "broker their use" — same move-server-side pattern, drop each from
   `_INFRA_ENV_ALLOW_EXACT`): `SUPABASE_S3_SECRET_ACCESS_KEY` + `R2_S3_SECRET_ACCESS_KEY` (object store),
   `CLOUDFLARE_API_TOKEN`, `VERCEL_TOKEN` (deploy/edge), `POSTMARK_SERVER_TOKEN` (email), `STRIPE_SECRET_KEY`
   (broker Stripe API calls). Each: env_egress_allowed(name) must become False AND the runtime path still works.
4. **Deploy + fresh-business E2E** — apply everything, then **create a BRAND-NEW business via the dashboard
   (`app.fourmanifold.com`) and exercise it as a real user** (this is the mandatory acceptance gate — see
   `feedback_new_business_e2e_per_fix` + `feedback_zero_shot_dashboard_bootstrap`). It validates: sub-user
   Google login (the JWT fix), the app-webhook (STRIPE_WEBHOOK fix), and the money routing under the demoted
   role (G3). The earlier E2E pattern: navigate, sign in (operator session is usually already live), type an
   idea, "Start building", watch the build, then `slug.coscale.app` serves from R2. Drive the browser with the
   `mcp__Claude_in_Chrome__*` tools; the operator may need to do the Auth0 password step.
5. **Final 10-agent audit → green.** Re-run the audit-workflow pattern (below). True green = zero
   still-exploitable findings + zero authority-equivalent secret on the allowlist.
6. **OPERATOR-ONLY (not us):** rotate `TAKYON_CAP_SIGNING_KEY`, `TAKYON_SAFEBOX_TOKEN`,
   `STRIPE_BILLING_WEBHOOK_SECRET` (were vendable historically). We do NOT rotate keys.

## 7. Key code surfaces

- `plugins/takyon/core.py`: `env_egress_allowed` + `_INFRA_ENV_ALLOW_EXACT` + `_SAFEBOX_SELF_AUTHORITY_SECRETS`
  + `provider_key_denylist` (the G1 policy, ~line 1210-1320); the documented RESIDUAL comment block lists what
  still vends. `_API_ENV_ALIASES`/`_INFRA_API_ALIAS_PROVIDERS`. The runtime DB conn (`_connect_postgres`,
  `_leaf_conn`, `_pg_app_scope`, `configure_takyon_pg_session(bypass=True)`, ~13080-13320).
- `plugins/takyon/safebox_app.py`: all routes — `/v1/env/*` (allowlist + write gate), `/v1/providers/*`,
  `/v1/creative/*` + `/v1/creative-credits/*`, `/v1/operator/session-token`, `/v1/stripe/{billing,app}-webhook/verify`.
- `plugins/takyon/safebox_provider_proxy.py`: `_authorize_operator_proxy` (capability-only, G2).
- `plugins/takyon/safebox.py`: client wrappers + `_use_remote_authority()` split + `is_sensitive_env_key`.
- `plugins/takyon/app_supabase_auth.py`: `verify_supabase_jwt` (the JWT fix — server-side HS/JWKS).
- `plugins/takyon/db/migrations/0038_runtime_least_privilege.sql` (+ `0037_safebox_ledger_boundary.sql` template);
  `billing.py`/`business_credits.py`/`custody.py`/`ledger_gate.py` (definer-func routing).
- Tests: `tests/plugins/test_safebox_authority_boundary.py`, `test_takyon_safebox.py`,
  `test_safebox_provider_proxy.py`, `test_takyon_runtime_least_privilege_pg.py`, the money `*_pg.py` suites,
  `tests/authoritative/test_inv4_no_ledger_tampering.py` + `test_inv5_*` (source-inspection invariants — when
  you move logic into SQL funcs, update these to assert the guard in the SQL, mirroring the app_usage pattern).

## 8. Deploy + verify mechanics

- Hosts: safebox `67.205.158.170`, operator `137.184.75.57`, sub-user `134.209.123.8`. ONE key
  `~/.ssh/takyon_argon_alpha14`. Runtime at `/opt/takyon/hermes-agent-main`; secrets `/opt/takyon/secrets/.env`.
- `/v1/env` + the proxy run ONLY on the safebox host → safebox-app changes deploy to the safebox; `core.py` to
  all 3 for parity (the env gate only executes on the safebox). `app_supabase_auth.py` runs on the sub-user
  plane. **git push does NOT auto-deploy to the VPS** (`project_deploy_rail_runner_gap`) — VPS deploy is manual
  rsync (`rsync -ptz -e "ssh -i ~/.ssh/takyon_argon_alpha14 ..."`), then `py_compile` + restart
  (`takyon-safebox.service` / `takyon-dashboard.service` + `takyon-worker.service` / `takyon-subuser.service`).
- **Live probe pattern** (status/size only, NEVER print a secret): on the safebox host
  `set -a; . /opt/takyon/secrets/.env; set +a; TOK=$TAKYON_SAFEBOX_TOKEN; B=http://10.116.0.2:8000`, then
  `curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TOK" "$B/v1/env/<name>"`.
- **The audit workflow** (the verification gate): a `Workflow` with ~8-10 parallel Opus adversaries (one per
  attack lens) → adversarial verify → synthesize green/not-green. See the two prior runs in this session's
  transcript for the exact shape; pin `model:'opus'`, ground every claim in real code, and feed it the prior
  findings so it confirms each is closed.

## 9. Gotchas (will bite you)

- **Git history was rewritten** (the `secrets/.env` scrub force-pushed `main` to a new root). `origin/main` is
  authoritative; the local working repo may be on old history. **Always `git fetch` before push**; this Mac has
  **concurrent pushers (Joshua)** — fetch-before-push + rebase the single commit if origin moved. **Never `git
  stash`** here (`feedback_no_git_stash_concurrent_repo`). `secrets/.env` is gitignored.
- **Parallel build agents on Opus** (`model:'opus'`), **worktree isolation** when they mutate files. Integrate
  by copying the worktree's changed files into main (verify the worktree base has the latest main first), run
  tests, commit, then `git worktree remove --force`.
- **0038 is inert until cutover** — applying it / committing the routed `billing.py` does NOT break the
  still-`postgres` runtime (owner ignores the REVOKEs). The DSN swap is the actual cutover.
- **Migration order at deploy:** apply 0038 BEFORE rsyncing the routed money modules (they call the funcs).
- **Don't deploy the JWT fix without the E2E** — it changes sub-user login verification.
- Tests: `scripts/run_tests.sh` (CI-parity, NOT bare `pytest`). The local PG-gated suites SKIP without a PG rig;
  the build agents validate on throwaway rigs. **Don't write change-detector tests** (CLAUDE.md) — when logic
  moves to SQL, assert the guard in the SQL func body (see `_sql_function_body` in `test_inv4`).
- Tracking lives in the session task list: **#28** (G3), **#30** (audit + E2E), **#31** (residuals).
