# Plan: make dev a true pre-prod CODE gate (not just isolated state)

## The problem (operator, 2026-07-03)

Dev isolates **state** (own Supabase, own secrets/money rails, own hosts) but runs the **same
git revision as prod** — both are `main`. So dev answers "does this *side effect* work?" but
**cannot** answer "will this *code change* break prod?" A real pre-prod environment must run a
**different, pinned code revision** from prod, prove it on prod-shaped infra with isolated
state, then **promote** that exact revision to prod. Until dev can run code prod doesn't have,
it's a rehearsal env, not a code gate.

Two axes of isolation — dev has one, needs both:

| Axis | Prod | Dev today | Dev target |
|---|---|---|---|
| **State** (DB, secrets, money, hosts) | prod | ✅ isolated (four-manifold-dev, Stripe TEST, dev droplets) | ✅ keep |
| **Code revision** | `main` | ❌ **same `main`** | ✅ **independently pinned, ahead of prod** |

## The fix — 4 parts, keystone first

### 1. Per-environment pinned code revision (THE KEYSTONE)
- Each environment config carries a `code_revision` (a git SHA or branch ref).
  - **prod** pins `main` (or a `prod` tag) — what is live.
  - **dev** pins the `dev` branch (or an explicit SHA) — **ahead of** prod.
- The deploy rail deploys **that revision** to **that env's hosts** — not the Mac working tree.
- Net: dev hosts run code that prod does not have. That is the entire point.
- Store it on `environments/dev.yaml` (`code_revision:`) and read it in `env_provisioner` /
  the deploy rails. `takyon env status dev` reports dev's pinned rev vs prod's, so drift is visible.

### 2. Branch + promotion flow (how code moves dev → prod)
- `main` = live-in-prod. `dev` = integration branch, ahead of `main`.
- Flow: change → land on `dev` → dev hosts deploy `dev` → **test E2E on dev** (real providers,
  Stripe TEST, isolated state, prod-shaped topology) → **promote**.
- **Promotion is one tracked command**: `takyon env promote dev` — fast-forwards `main` to the
  exact dev-tested SHA and triggers the prod deploy rail. Prod only ever receives a revision that
  already ran green on dev. This is the code gate.
- Migrations ride the same promotion (already idempotent via `takyon migrate`), applied on dev
  first (dev is where a bad migration is caught), then prod at promote time.

### 3. Dev on dedicated hosts, at the dev revision (finish the topology)
Prod = 3 persistent systemd services (operator dashboard+worker, subuser, safebox) + optional
Mac worker. Dev must match so it can *hold* its own pinned code persistently:
- **Dev safebox** → `takyon-safebox.service` on the existing `takyon-dev-safebox` droplet
  (in progress). Stop using the Mac `uvicorn` safebox.
- **Dev operator** → `takyon-dashboard.service` + `takyon-worker.service` on a dev operator
  host (reuse a dev droplet), deployed at dev's `code_revision`.
- **Dev subuser** → the 4b LB split (`takyon-dev-subuser-1/2` + LB) — exists; deploy at dev rev.
- **`takyon-operator-dev.sh` demotes to a thin CLIENT** — SSH-tunnel + optional Mac worker,
  exactly like `takyon-operator-prod.sh`. It stops *hosting* dev, so "dev's code" is the hosts'
  deployed revision, **not** the Mac's arbitrary working tree. (This also kills the Mac
  zombie-accumulation / concurrent-operator-thrash problem at the root.)

### 4. Revision-aware deploy (the mechanical change)
- `deploy/*/deploy-runtime.sh` deploys a **specific git SHA** (`git archive <rev>` into a temp
  tree, then rsync) instead of the live working tree. Add an optional `--rev <sha>` arg.
- `takyon env deploy dev [--rev <sha>]` sets/records the pin and deploys it to the dev hosts.
- `takyon env restart dev` (exists — the zero-loss drain rail) activates without traffic loss.

## Result
Dev = prod-shaped infra + isolated state + **independently pinned code**, promotable to prod by
a single command that only ever moves a dev-green revision forward. That is a real "will this
break prod?" gate — the thing dev is *for*.

## Sequencing / coordination
- Keystone (#1) + promotion (#2) are the highest-value, do them first — they're what makes dev a
  gate. #4 is the mechanical enabler. #3 (dedicated hosts) is being started (Codex: dev safebox →
  droplet service); it's necessary for dev to *persistently host* its own revision but is not the
  gate by itself.
- Do NOT regress the existing isolation guarantees: prod-literal boot guard, separate Supabase,
  Stripe TEST, transaction-pooler (:6543) runtime DSNs, subuser-zero-operator-authority.

## Open questions for the operator
1. Branch model: a long-lived `dev` branch ahead of `main`, or per-env pinned SHAs with no
   standing branch? (Branch is simpler to reason about; SHA-pin is more explicit.)
2. Auto-deploy dev on push to `dev`, or explicit `takyon env deploy dev`?
3. Is a dedicated **dev operator droplet** approved (~$12-24/mo on top of the existing dev
   subuser/safebox droplets), or reuse one of the existing dev droplets for the operator role?
