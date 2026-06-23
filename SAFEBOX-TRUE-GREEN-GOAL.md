# /goal spec — Safebox secret-boundary + money-gate: drive to TRUE GREEN

**You are Codex.** This is your goal. Execute it to completion; verify everything against the tree and the live
hosts — do **not** trust prose summaries (a prior agent fabricated an "in flight" status; always grep/probe).
Supersedes `SAFEBOX-TRUE-GREEN-HANDOFF.md` where they differ (that doc wrongly said the mint split was in
flight — it is NOT done; see §B).

## GOAL CONDITION (when you are done)

**TRUE GREEN = (1) no authority-equivalent secret vends over `/v1/env`, AND (2) no exploitable I1/I2 finding** —
proven by a final multi-agent adversarial audit that returns zero `still_exploitable`, PLUS a brand-new business
created end-to-end through the dashboard browser and used as a real user without breakage. Not done until all of
§C lands, deploys, and that audit + E2E are clean.

## What this is

Make the **safebox** (`67.205.158.170`, private `10.116.0.2:8000`) the *sole* secret + money authority so an
attacker with **root on every other plane** (operator `137.184.75.57`, sub-user `134.209.123.8`, all code, the
shared `TAKYON_SAFEBOX_TOKEN`, the runtime `DATABASE_URL`) **plus malicious product sub-users** still cannot
(**I1**) obtain a raw provider/authority key, nor (**I2**) spend or **mint** money beyond what the safebox
authorizes.

## The spine — read these MDs FIRST, in order

1. **`hermes-agent-main/GOAL_RULES.md`** — canonical rules; **§0 is the authority principle** (below); code cites `GOAL_RULES §N`.
2. **`AGENTS.md`** (outer root) — "Safebox authoritative gating" section: the §0 principle + the 3-host deploy rails.
3. **`safebox-threat-model-codex-checklist.md`** (outer root) — the **VERIFY-ONLY runbook**. Use its probes to confirm any gate. (It says "Codex: verify only" — for THIS goal you also IMPLEMENT, but still use its probes as the pass/fail oracle.)
4. **`hermes-agent-main/CLAUDE.md`** + outer `CLAUDE.md` — dev guide: `scripts/run_tests.sh` (never bare pytest), the no-paid-key-from-os.environ pattern, **no change-detector tests** (when logic moves into SQL, assert the guard in the SQL func body, not a Python source string).
5. **Memory** (`/Users/Zygote/.claude/projects/-Users-Zygote-Downloads-takyon/memory/`): `project_safebox_threat_model_gaps.md` (the 3 gaps + cutover viability), `project_vps_deploy_access.md` (hosts/key/rsync), `feedback_deploy_push_main_too.md`, `feedback_no_git_stash_concurrent_repo.md`, `feedback_new_business_e2e_per_fix.md`, `feedback_zero_shot_dashboard_bootstrap.md`.

### Authority principle (GOAL_RULES §0) — internalize
> **Authority is a capability the safebox MINTS and VERIFIES — never inferred from possession of a shared
> secret, and never read from state the caller can write.** A correct surface asks only "is this a valid
> capability the safebox minted for exactly this action/account/cost?" — never "which plane is calling / what
> token does it hold." Corollaries: (1) `TAKYON_SAFEBOX_TOKEN` = transport reachability, not authority; (2) the
> safebox never egresses or accepts a write to its own authority secrets — `/v1/env` is an infra **allowlist**;
> (3) money/identity state is writable only by the safebox's own DB role.

---

## §A. DONE (verify before trusting; deploy state matters)

| Item | Commit | Deployed? |
|---|---|---|
| **G1** `/v1/env` → exact-name infra allowlist; never-vend/never-write the safebox's own authority secrets (`TAKYON_CAP_SIGNING_KEY`, `TAKYON_SAFEBOX_TOKEN`, `STRIPE_BILLING_WEBHOOK_SECRET`, `SUPABASE_SERVICE_ROLE_KEY`); symmetric write gate (sensitive key → 403, case-folded) | `eb7c53ea`+`b486c2a3` | **YES — live-proven** on the safebox host |
| **G2** operator proxy is capability-only; bare-token spend path removed | `eb7c53ea` | **YES — live-proven** (`/v1/messages` bare-token→401) |
| Residual **`SUPABASE_JWT_SECRET`** alg-confusion fix (`verify_supabase_jwt` no longer verifies HS* with the ambient symmetric secret; validates server-side via Supabase Auth / JWKS); dropped from allowlist | `90aa186d` | **NO** — changes sub-user login; deploy WITH the E2E |
| Residual **`STRIPE_WEBHOOK_SECRET`** → sub-user app-webhook verified on the safebox (`/v1/stripe/app-webhook/verify`); dropped from allowlist | `d2f0d76b` | **NO** |
| **G3 FOUNDATION** migration `0038_runtime_least_privilege.sql` (INERT, NOLOGIN `takyon_runtime`): grant-all-revoke-dangerous on money tables + column-revoke `businesses.owner_user_id`; SECURITY DEFINER billing/credits/custody funcs; `billing.py`/`business_credits.py`/`custody.py` routed through them | `3374ca2c` | **NO** (inert until cutover) |

Verified facts: RLS bypass is via the `takyon.rls_bypass` **GUC**, not the role's BYPASSRLS attribute (so
NOBYPASSRLS `takyon_runtime` is safe; `GRANT takyon_app TO takyon_runtime` keeps `_pg_app_scope` working). The
runtime connects as Supabase superuser `postgres` via the legacy pgbouncer `db.<ref>:6543` (auth_query) — a
custom `takyon_runtime` LOGIN role with a password **can** authenticate through it (direct `:5432` is fallback).

## §B. NOT DONE — the immediate next step: the MINT/SPEND split (gates the cutover)

`0038` still **GRANTs the MINT funcs to `takyon_runtime`** with **no `revoke`** (verified: billing 424‑425,
credits 701‑702, custody 841‑843; no `revoke execute` anywhere; no `/v1/billing/*` or `/v1/custody/*` route —
only `/v1/creative-credits/*`). So after the DSN cutover an attacker as `takyon_runtime` could **mint** by
calling `safebox_billing_grant_allowance` / `safebox_credits_grant` / `safebox_custody_accrue` /
`safebox_custody_payout`. **A partial, BROKEN attempt exists in another session's inner-repo worktree
`hermes-agent-main/.claude/worktrees/agent-a13cb40652e491190`** (it has the `0038` revoke lines at 444‑445/
728‑729/875‑877 and rewired `billing.py` call-sites, but the safebox routes + `safebox.py` wrappers + custody
routing DON'T exist there → it would crash; and it's a divergent lineage missing the SUPABASE_JWT fix). Use it
as a **read-only reference for the revoke lines only**; do NOT adopt its incomplete state. Build cleanly on main.

**THE HARD PART IS AUTHORIZATION, not relocation.** A mint route a `TAKYON_SAFEBOX_TOKEN` holder can call with
arbitrary `{account, amount}` is NOT a fix (the attacker holds that token). Design:
- Fence (revoke from `takyon_runtime`) the MINT funcs: billing `grant_allowance`/`open_account`; credits
  `grant`/`open_account`; custody `accrue`/`open_account`/`payout`. KEEP the SPEND funcs granted (billing
  reserve/settle/refund; credits reserve/commit/release) — they're balance-bounded and can't mint.
- First **investigate** how the existing `/v1/creative-credits/grant` + `/accounts/open` are authorized (gated
  by `authorize_operator_call`/business-ownership, or a blank mint a token-holder can call?). If it's itself an
  unauthorized mint hole, **flag it and fix it**, don't propagate it.
- Classify every legit mint caller by trigger and authorize accordingly:
  - **Webhook-driven** (subscription/credit-pack checkout → `grant_allowance`/`grant`; Stripe app-webhook →
    `custody.accrue`): the safebox already verifies these (`/v1/stripe/{billing,app}-webhook/verify`). The grant
    must happen on the safebox (owner) **atomically tied to the verified event** (fold the grant into the
    verify+process, or require the verified event id) so a bare token can't mint without a genuine signed event.
    See `control_api.py` `/billing/webhook` + `core.py handle_business_record_stripe_webhook`.
  - **Provisioning** (first-login starter allowance/credits via `control_plane.py
    provision_user_on_first_login`/`_ensure_starter_allowance` + business-creation starter credits): a gated
    safebox route, one-time-per-user / policy-bounded so it can't be replayed to mint repeatedly.
  - **Payout** (`custody.payout`): operator-initiated → operator capability.
- Implement: gated safebox routes for billing+custody mints (+ `safebox.py` wrappers; route `billing.py`/
  `custody.py` MINT calls through them on remote planes; keep SPEND on the local definer-func path); credits
  mints already route via `/v1/creative-credits/*` (rely on it if gated, just add the revoke); add the `revoke
  execute … from takyon_runtime` lines to `0038`. Update inv4/inv5 source tests if they assert the old path.
- **Boundary test (required):** (a) `set role takyon_runtime` + calling a mint func → InsufficientPrivilege;
  (b) an UNAUTHORIZED mint route call (bare token, no verified event/capability) → refused; (c) legit
  webhook-driven grant + provisioning grant + reserve/settle still succeed. Validate on a throwaway PG rig.

## §C. REMAINING WORK to true green (ordered; §B is step 1)

1. **Mint/spend split + authorization** (§B). Then it's safe to cut over.
2. **G3 CUTOVER** (the one prod-risky live step — it's the DSN swap, not the migration):
   - Apply `0038` on prod (safe while the runtime is still `postgres` — owner ignores its own REVOKEs). Apply
     0038 **before** rsyncing the routed money modules (they call the funcs).
   - Provision `takyon_runtime` **LOGIN + password** (psql as owner — a NEW internal credential, NOT a key
     rotation). Put the new DSN `postgres://takyon_runtime:<pw>@db.<ref>.supabase.co:6543/postgres` in the
     safebox-served `DATABASE_URL` for the **runtime planes only**; the safebox keeps the `postgres` DSN. Restart.
   - **Re-probe (checklist §D):** runtime `current_user` `rolbypassrls=f`; `BEGIN; UPDATE billing_accounts /
     business_creative_credit_accounts … ; ROLLBACK;` → permission denied; mint func as `takyon_runtime` →
     InsufficientPrivilege; legit reserve/settle/grant-via-safebox still work.
3. **AUTH0 residual** (highest remaining severity — operator impersonation): `AUTH0_CLIENT_SECRET`/`AUTH0_SECRET`
   are fetched by `web_server.py:_auth0_dashboard_config` for server-side OAuth code-exchange + session-cookie
   signing. Move BOTH onto the safebox (a `/v1/auth0/*` exchange+sign route, webhook-pattern); drop from the
   allowlist; **validate operator login E2E.**
4. **Remaining residuals** (same "move use server-side then drop from `_INFRA_ENV_ALLOW_EXACT`"):
   `SUPABASE_S3_SECRET_ACCESS_KEY` + `R2_S3_SECRET_ACCESS_KEY` (object store), `CLOUDFLARE_API_TOKEN`,
   `VERCEL_TOKEN`, `POSTMARK_SERVER_TOKEN` (email), `STRIPE_SECRET_KEY` (broker Stripe API). Each: `env_egress_
   allowed(name)` must become False AND the runtime path still works.
5. **Deploy + fresh-business E2E** — apply everything, then **create a BRAND-NEW business via
   `app.fourmanifold.com` in the browser and use it as a real user** (the mandatory acceptance gate). It
   exercises the three undeployed fixes at once: sub-user Google login (JWT), the app-webhook (STRIPE_WEBHOOK),
   and money under the demoted role (G3).
6. **Final multi-agent adversarial audit → green** (zero still_exploitable + zero authority-equivalent secret on
   the allowlist). Same shape as the two prior audits in the transcript: ~8-10 parallel adversaries (one per
   lens) → adversarial verify → synthesize; ground every claim in real code; feed it the prior findings.
7. **OPERATOR-ONLY (not Codex):** rotate `TAKYON_CAP_SIGNING_KEY`, `TAKYON_SAFEBOX_TOKEN`,
   `STRIPE_BILLING_WEBHOOK_SECRET` (were vendable). **Codex does NOT rotate keys.**

## §D. Deploy + verify mechanics

- Hosts: safebox `67.205.158.170`, operator `137.184.75.57`, sub-user `134.209.123.8`. ONE key
  `~/.ssh/takyon_argon_alpha14`. Runtime `/opt/takyon/hermes-agent-main`; secrets `/opt/takyon/secrets/.env`.
- `/v1/env` + the proxy run ONLY on the safebox host → safebox-app changes deploy there; `core.py` to all 3 for
  parity (the env gate only executes on the safebox). `app_supabase_auth.py` runs on the sub-user plane.
- **git push does NOT auto-deploy to the VPS** — deploy is manual `rsync -ptz -e "ssh -i
  ~/.ssh/takyon_argon_alpha14 …"`, then `py_compile` + `systemctl restart` the relevant unit
  (`takyon-safebox` / `takyon-dashboard`+`takyon-worker` / `takyon-subuser`). Migrations apply via the CLI on
  the VPS (creds resolve from the safebox there).
- **Live probe** (status/size only, NEVER print a secret value): on the safebox host
  `set -a; . /opt/takyon/secrets/.env; set +a; TOK=$TAKYON_SAFEBOX_TOKEN; B=http://10.116.0.2:8000`, then
  `curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TOK" "$B/v1/env/<name>"`.

## §E. Gotchas (will bite you)

- **Verify, don't trust summaries.** A prior agent claimed work was "in flight" that was never started. Grep the
  tree / probe the host before believing any status — including this doc's §A deploy claims (re-probe G1/G2).
- **Git history was rewritten** (`secrets/.env` scrub force-pushed `main` to a new root). `origin/main` is
  authoritative; **always `git fetch` before push**; **concurrent pushers (Joshua + other agent worktrees)** —
  rebase the single commit if origin moved. **Never `git stash`** here. `secrets/.env` is gitignored.
- **Many concurrent agent worktrees exist** under `.claude/worktrees/` (outer AND inner repo) — they are other
  sessions' WIP; do not adopt them blindly (e.g. `a13cb`, §B).
- **`0038` is inert until cutover** — applying it / committing routed `billing.py` does NOT break the
  still-`postgres` runtime (owner ignores REVOKEs). The DSN swap is the cutover. Apply 0038 BEFORE rsyncing the
  routed money modules.
- **Don't deploy the JWT fix without the fresh-business E2E** — it changes sub-user login verification.
- `scripts/run_tests.sh` (CI-parity). PG-gated suites SKIP without a local PG rig; validate money changes on a
  throwaway rig. **No change-detector tests** — assert the guard in the SQL func body when logic moves to SQL.
- Worktree integration: copy the worktree's changed files into main only after confirming its base has the
  latest main; run the suite; commit; `git worktree remove --force`.
