# Chrome Sign-On Runbook — one session, unblocks everything downstream

_Prepared 2026-07-02 while the operator is away. Each step is a one-time credential/account act. I (Claude) drive the browser and do everything AROUND each login; the operator only authenticates + confirms. I cannot create accounts or type tokens myself — those are the sign-on moments. After each token, I deposit it in the safebox and the downstream code (built + landed) runs autonomously._

Order is by leverage. Supabase first (unblocks the whole dev environment UC3).

---

## 1. Supabase — dev control plane (highest leverage; unblocks UC3 end-to-end)
**Why:** operator ruling — dev gets its OWN Supabase project (isolated from four-manifold-prod). `topology.sql` (Codex-shipped) + migrations make it prod-shaped autonomously once it exists.
**Operator does:** log in at supabase.com → create a new project named `four-manifold-dev` (same region as prod is fine) → set a DB password → when it's provisioned, copy the **connection string** (the pooler/6543 DSN) and the project ref.
**Then I do (no further login):** deposit the dev DSN as the dev-env `TAKYON_OPERATOR_DATABASE_URL` (+ migration DSN) into the DEV safebox / dev env file; run the ownership+admin-option topology bootstrap once (same `grant … to postgres` sandwich we used on prod, but as the dev project's postgres); `takyon env create dev` applies `topology.sql` + all migrations as `takyon_migration`; verify `worker_pools` + reservation columns exist on the dev DB. → **dev DB is live and prod-shaped.**

## 2. Auth0 — Management API token (dev dashboard login + provisioner)
**Why:** the provisioner creates dev Auth0 applications; and the dev dashboard needs an Auth0 app so you can log into `localhost:9119` as a real user.
**Operator does:** log into the Auth0 dashboard (same tenant as prod) → Applications → APIs → **Auth0 Management API** → Machine-to-Machine Applications → authorize a new M2M app (name `takyon-dev-provisioner`) with scopes `create:clients read:clients update:clients create:client_grants` → copy the **client_id + client_secret** (or a Management API token).
**Then I do:** deposit as `TAKYON_AUTH0_MGMT_*` in the safebox; the provisioner's Auth0 step (built, fail-closed until this lands) creates the dev dashboard application + callback URLs. → **dev dashboard is browsable.**

## 3. DigitalOcean — API token (Stage 4b later; free to hold)
**Why:** Stage 4b (subuser replicas + VPC load balancer) needs it. The token is free; what it *creates* recurs (~$12/mo LB + ~$12–50/mo/replica) and stays opt-in (Q16) — depositing the token now costs nothing and removes the future gate.
**Operator does:** DO dashboard → API → Generate New Token (name `takyon-provisioner`, read+write) → copy.
**Then I do:** deposit as the safebox DO admin token. Nothing is created until Stage 4b is explicitly run.

## 4. Shopify — Partner + $0 dev store + app OAuth (UC4 real acceptance)
**Why:** UC4's acceptance is a REAL Shopify integration (operator ruling) — compose a subscription price from a store's real plan fee, read via Composio, recompose on a `shop/update` webhook.
**Operator does:** create/log into a Shopify **Partner** account → create a **development store** ($0) → create an app (or connect via Composio's Shopify toolkit) and get the app OAuth credentials Composio needs → connect the dev store through Composio.
**Then I do:** the `business_connect_shopify` tool + `/api/webhooks/shopify` HMAC rail (to be built next) use the existing Composio broker — token custody stays in Composio, zero new runtime credential. → **UC4 real acceptance runnable.**

---

### What's already built and waiting on these (so the payoff is immediate)
- **Stage 3 RuntimeContext** (landed): `TAKYON_ENV=dev` builds the dev profile; prod-literal boot assertion armed.
- **Migration rail** (Codex, landed): `topology.sql` + `takyon migrate` — makes the dev DB prod-shaped autonomously.
- **Stage 3b provisioner** (building now): `takyon env create dev` — consumes 1+2, fail-closed on anything not yet deposited.
- **UC4 composition engine** (landed) + **money-shape gate/tool** (building now): derived pricing, ready for the Shopify component from step 4.
