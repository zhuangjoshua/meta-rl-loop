# Takyon UC1–4 — Progress Checklist

Living status for the modularization build. Plan: [takyon-modularization-plan.md](takyon-modularization-plan.md).
Legend: ⬜ not started · 🟡 in progress · ✅ done+proven · �las = needs your Chrome sign-on · ⛔ = blocked on a decision.

_Last updated: 2026-07-02, by Claude. This file is updated as stages move._

---

## Where we are right now

**Stage 0 (safety net) — ✅ net in place, re-verified 2026-07-02 10:20.** All three nets green in one run: `scripts/run_tests.sh` on the three characterization files → **29 passed** (15 host-role + 9 import-guard + 5 claim-affinity on the live PG rig at 54331 + tokenless safebox at 8377, both still up from the rig build). The earlier `billing_accounts` "permission denied on SELECT" puzzle is RESOLVED — it was `SELECT … FOR UPDATE`, which requires UPDATE privilege; migration 0044 deliberately revoked UPDATE on `billing_accounts` from `takyon_operator_runtime`, so the billing path correctly requires the `takyon_safebox_authority` role. Not a rig bug — it's the prod money-privilege model working as designed. This role topology IS the Stage-3 dev-DB foundation ("dev with prod role names"), so it's reused, not throwaway.

**Stage 1 — LANDED + DEPLOYED 2026-07-02 (one gate left: fresh-business browser E2E, awaiting operator Auth0 sign-in).** Built in the isolated worktree, merged fast-forward to main (`797dc7a5..cfe7270c`, plus docs `69e300fc` and the deploy-rail fix `3a4a9a1b`), pushed to origin, and **activated on both VPS hosts** (operator + sub-user: full-tree rsync, `py_compile` OK, services restarted and `is-active`, sub-user healthz 200). Live CLI E2E ran the WHOLE create path on Stage-1 code against prod: fresh business `stage1pool0702` bootstrapped by the new `WorkerPool` (claim identity `mac-operator-…-94997-1`, PYTHONPATH-verified worktree code) through landing → publish → Claude-worker build → **site live, HTTP 200** at `stage1pool0702.coscale.app`.

**Deploy-rail truthfulness fix (agents.md clause):** observed directly on run 28617202057 that the GitHub workflow's VPS legs skip (`Operator VPS SSH is not reachable from this GitHub runner; skipping remote deploy`) — a green run proves build only. CLAUDE.md's fast path said the opposite; it now records the verified rail (check the run log for skip warnings → full-tree rsync + restart + is-active on BOTH hosts), committed `3a4a9a1b` and pushed, so every future main push follows the truthful deploy path.

---

## Decisions still needed from you (⛔ = gates real work)

- ✅ **Q1 — RESOLVED in practice (2026-07-02).** Stage 1 was built in an isolated worktree off origin/main while the other session kept committing to the same hot files; fast-forward landed with zero conflicts. Same discipline (worktree + failure-set-diff + partial-hunk commits on shared files) applies to every later stage that touches `worker.py`/`jobs.py`/`core.py`.
- **Q13/Q14 — monthly-only sign-off.** Ship the monthly-only + fail-loud-cap slice standalone now (chip ready)? Confirm the grandfather contract (refuse new non-month, keep serving frozen non-month rows). **Prod check DONE (2026-07-02, read-only via operator DSN): 6 non-month rows exist — all `one_time` credit-packs on `roomier`(3)/`roomremix`(3) — and ALL have 0 active/trialing entitlements; 133/139 plan rows are already monthly. The grandfather window is EMPTY: no live non-month subscriber exists, so read-side year/one_time handling can be deleted outright when the slice ships.**
- **Q11/Q12 — UC4 margin policy owner + money-shape source.** Who sets `margin_floor` (recommend platform default + per-archetype override); does the shape gate key on the archetypes registry or a minimal UC4-owned record (recommend the latter first)?
- **Q16 — UC2 rollout.** Ship Stage 4a now (the 180s inline AI call is a live risk; chip ready)? DO load balancer vs round-robin DNS for 4b (recommend DO LB)?
- **Q15 — promote the §6b channel/provider registries** into stages, gating the next channel/provider add on the seam?

---

## Sign-on moments (the only Chrome help needed) — 🔑

Three, all one-time-ever:
- 🔑 **Auth0 Management API token** — at Stage 3b, so the provisioner can create dev Auth0 applications. Deposited once into the safebox.
- 🔑 **DigitalOcean API token** — at Stage 4b, so the provisioner can create the load balancer + replica droplets. Deposited once into the safebox. _Note: the token is free; what it creates recurs (~$12/mo LB + ~$12–50/mo per replica). Stage 4a costs $0 and comes first._
- 🔑 **Shopify Partner setup** — at Stage 5, for the real-Shopify UC4 test: create a Partner account + $0 dev store + the app OAuth credentials Composio's Shopify toolkit requires. Token custody then lives in Composio (existing key) — no new runtime credential.

Everything else (Cloudflare, Vercel, Stripe) is already in the safebox — no sign-on. Dev social posting reuses your existing accounts (your ruling), so no dev social signup either.

---

## The stages

### Stage 0 — characterization tests + guards · 🟢 net in place
_Test-only, zero production change, doesn't touch hot files. Safe under the live goal. The three artifacts that GATE the actual refactors (Stage-3 enum collapse, money isolation, Stage-2 ClaimScope) are done + proven; the one gap (local run_one/billing rig) is documented and is Stage-3 dev-DB work._
- ✅ **Table-driven `HostRole` characterization** over all 7 `_normalized_host_role`/`_host_role` copies.
  **Proof wired in:** `hermes-agent-main/tests/plugins/test_takyon_host_role_characterization.py` — 15 tests, green under `scripts/run_tests.sh` (CI-parity, 4 workers, hermetic). Pins the 3 real divergence classes (web_server folds unknown/safebox/""→combined; app_actions+safebox do NO normalization so dashboard/app/product pass through un-normalized; core/runtime_app+2 conftests are canonical) and fails if a 7th copy appears. Gates the Stage-3 enum collapse.
- ✅ **Money-rail import guard** (AST-based, catches lazy imports too).
  **Proof wired in:** `hermes-agent-main/tests/plugins/test_takyon_money_rail_import_guard.py` — 9 tests, green under `scripts/run_tests.sh`. HARD RULE: billing/app_usage/business_credits/ledger_gate never import `core` (verified none do). ALLOWLIST: each rail's first-party deps pinned (billing→{ledger_gate,safebox}, app_usage→{runtime_app,app_identity,openmeter_backend}, business_credits→{ledger_gate,safebox}, ledger_gate→{runtime_app}); new coupling fails until consciously added.
- ✅ **`claim_one` affinity characterization** — the single most Stage-2-relevant runtime pin (Stage 2 replaces exactly this SQL with `ClaimScope`).
  **Proof wired in:** `hermes-agent-main/tests/plugins/test_takyon_claim_affinity_characterization.py` — 5 tests green on the PG rig, **skips cleanly (CI-safe) with no DSN**. Pins: matching-prefix worker claims in-window; non-matching worker BLOCKED in-window (returns None); non-matching worker claims AFTER the window elapses (back-dated `created_at`/`updated_at`, no sleep); base-prefix exact match (commit 6bc61762) treated as matching; un-hinted job claimable by anyone. Runs via the plain `pg_conn` fixture with direct row-insert provisioning — **no safebox needed**.
- 📋 **Rig + prod authority topology fully mapped** (observed directly from migrations + a live `status` run, not assumed): faithful login-role topology reproduced (all 58 migrations clean), and the exact operator-plane money-privilege model confirmed — `takyon_operator_runtime` has SELECT but NOT UPDATE on `billing_accounts` (0044 revoked it by design), so `SELECT … FOR UPDATE` in the billing path requires the `takyon_safebox_authority` role. This is the Stage-3 dev-DB foundation, reused there.
- ⚠️ **Documented harness gap (NOT my regression — pre-existing):** the billing-dependent slice of the jobs PG suite (`run_one` reserve→settle→release, `requeue_stale`) can't run on the *local* rig because `provision_user_on_first_login` mints an API key through the safebox authority, which needs either `host_role=safebox` (the PG conftest **blocks** it) or a shared-DB remote safebox (incompatible with per-test throwaway DBs). CI also has no Postgres, so this suite has plausibly never executed anywhere. `run_one`'s contract IS characterized by the existing suite's assertions; making it *runnable* is Stage-3 dev-DB work (a real safebox authority against a shared dev database). Flagged, not papered over.
- ➖ `TakyonStore.commit` job.enqueue dual-write pin folded into Stage 1's characterization (it's the enqueue path Stage 1 touches; pinning it there keeps the pin next to the change).

### Stage 1 — extract WorkerPool, converge launch paths · ✅ landed + deployed + live-proven (browser E2E = last box below)
_Commits on main: f2e4e64a (Stage-0 nets) · 198529e9 (Stage-1) · cfe7270c (review fix) · pushed `797dc7a5..69e300fc..3a4a9a1b`. Deployed + active on both hosts._
- ✅ `worker_pool.py` — `WorkerPool` with lane factories `local_threads`/`embedded`/`inline`; size/dispatcher-role/identity are constructor args; handlers injected (`worker.HANDLERS` = default map only). `run()` is `run_worker_loop`'s body verbatim; every tick still goes through `drain_tick`/`jobs.run_one` (reserve→settle→release untouched, escape-hatch rule respected). ClaimScope deliberately NOT a param yet (Stage 2) — nothing lands unwired.
  **Proof wired in:** live prod bootstrap `stage1pool0702` claimed + executed by this exact class (job locked_by `mac-operator-Anuradhas-MacBook-Pro-94997-1` = `WorkerPool.thread_worker_id(0)` of the size-4 pool; worker process PYTHONPATH pinned to the Stage-1 tree, verified on the live pid) through landing publish + Claude-worker build → site HTTP 200. Deployed file present on both VPS hosts (15053 bytes, compile OK), services active.
- ✅ Launch paths converged: `cmd_worker` (takyon_cli/main.py), dashboard embedded drain (web_server.py), shell inline wake (`_run_pg_ceo_wake_once` → `WorkerPool.inline().run_one_inline`) all construct the pool; `run_worker_loop` kept as back-compat delegate. 4th lane (isolated-turn subprocess) documented as session-owned; Stage-3 threads context through its spawn (plan R4).
  **Proof wired in:** the live E2E exercised `cmd_worker`→`local_threads` (the console's worker pool) and the enqueue/follow shell path end-to-end on prod; git shows no remaining non-test caller of the old open-coded loops (`grep run_worker_loop` = delegate + docs only).
- ✅ worker→cli inversion FIXED: the 37 helpers worker.py lazily imported from the interactive shell module moved verbatim (733 loc) to `turn_runtime.py`; cli.py re-exports (shim per §5). worker.py imports ZERO cli names.
  **Proof wired in:** AST layering guard `test_takyon_worker_layering_guard.py` (worker/worker_pool/turn_runtime must never import cli, lazy included + shim-identity assertions) green on main; the live E2E ran the bootstrap handler (which consumes `_ceo_bootstrap_turn_config`/`_business_workspace_execution_context` via turn_runtime) on prod successfully.
- **Test evidence (all three gates passed):**
  1. Worker PG suite failure set on the rig **byte-identical to untouched-main baseline** (21 pre-existing env failures, 0 new).
  2. **FULL suite (24k tests) failure-set diff**: every worktree-only failure reproduces **byte-identically in a pristine control worktree at the same base commit with zero Stage-1 changes** (kanban-dashboard asset tests, cmd_update git-worktree quirks, etc.) → worktree-env artifacts, not regressions. Main-only failures are main-tree dirty-state flakes.
  3. **Adversarial review workflow** (8 agents, 4 lenses — verbatim-drift/launch-parity/money-claims/import-shim — each finding differentially reproduced against the parent commit): **zero confirmed runtime behavior drift**. One confirmed test-plane defect (missed monkeypatch retarget in DSN-gated `test_takyon_plugin.py:8509`, masked locally, already-stale test) → FIXED in cfe7270c.
- **Found in passing (pre-existing, chip filed):** `cli.py` references `_plugin_skill_invocation_message` which is defined nowhere — latent NameError on that shell path.
- ✅ **Live CLI E2E on Stage-1 code (the operator's console command, real prod):** console opened on the worktree code, `/create stage1pool0702` → bootstrap claimed by the Stage-1 pool and executed through business row → plan seed → instant landing → product surface publish → Claude-worker build → GSC/logo phase; **site live HTTP 200**. Driver-window artifact: my 45-min watch window closed during the logo retry; the in-flight job spilled to another machine's pool exactly per the designed stale-reclaim rail (spill-not-strand observed live). The orphaned `nutrientdaily0702c` from the terminated session ended `blocked` (recorded, not stranded).
- ✅ **Deployed per the agents.md rail:** pushed `main`; observed run 28617202057's VPS legs SKIP (runner can't SSH) → full-tree rsync to operator `137.184.75.57` + sub-user `134.209.123.8`, `._*` sweep, `py_compile` OK on both, `takyon-dashboard`+`takyon-worker`+`takyon-subuser` restarted and `is-active`, sub-user healthz 200, `app.fourmanifold.com` 302→Auth0 (expected), product site 200. Sub-user security surface untouched by the diff (operator-plane files only; sub-user redeploy keeps trees coherent).
- ✅ **Acceptance CLOSED per operator ruling (2026-07-02): the proof rail for this goal is the CLI, not the dashboard browser E2E.** The CLI proof above (fresh business `stage1pool0702` created through the real console on Stage-1 code, bootstrapped end-to-end, site HTTP 200) is the Stage-1 acceptance. **Stage 1 is DONE.**

### Migration rail (DB-authority topology) · 🟡 handed to CODEX — plan at [takyon-migration-rail-plan-for-codex.md](takyon-migration-rail-plan-for-codex.md)
_Surfaced 2026-07-02 by 0059: prod schema was editor-built as `postgres`, so the tracked runner had never replayed as `takyon_migration`. One-time prod surgery DONE (operator-confirmed in the Supabase editor): all `public` tables/routines/sequences/views/types → owned by `takyon_migration`; `WITH ADMIN OPTION` memberships on the app roles (the rig's proven topology) + the two `set true` grants (0031/0038). Codex deliverables: `db/topology.sql` (one topology source consumed by rig+dev+prod), runner topology assertion (fail-loud with exact fixes), `takyon migrate` tracked entrypoint (replaces heredocs, recorded in CLAUDE.md deploy notes), CI idempotence+privilege job, authoring contract docs. Feeds Stage 3's dev-DB provisioner. No file overlap with Stages 2/5._

### Stage 2 — ClaimScope reservation · 🟡 LANDED on main (ab9e49f4) + DEPLOYED both hosts; live UC1 ownership check running → **UC1 ships**
_Deploy proof: pushed `cf862c99..ab9e49f4`; full-tree rsync to operator+subuser, compile OK, `takyon-dashboard`/`takyon-worker`/`takyon-subuser` all `active`, subuser healthz 200. **VPS worker registered its pool row on prod through the deployed code** (`worker-ubuntu-…-3643495`, lease renewing) — registry lifecycle live. Two REAL findings from the live E2E, both fixed + regression-pinned before landing:_
1. _**RLS gap:** prod enables RLS on new public tables; 0059 lacked the 0052 policy convention → pool INSERT denied (my degraded-path log caught it exactly). Fixed: 0059 now ships `takyon_rls_bypass()` policies for the four authority roles — **no policy for the subuser roles, deny-by-absence preserved** — and was replayed onto prod through the freshly repaired migration rail (`migrations_ok count=59`, zero browser steps: the rail fix's first dividend)._
2. _**Self-heal gap (mine):** the heartbeat/re-register thread only started after a SUCCESSFUL initial registration, so an initial failure disabled self-heal permanently. Fixed (loop always starts; it is the retry path) + pinned (`test_worker_pool_heartbeat_thread_starts_even_if_initial_registration_fails`)._
_**Mixed-version window (expected, closing):** the E2E's strict reservation was stamped correctly but claimed by a stale OLD-code worker on another machine (Joshua's) whose `claim_one` predates the predicate — reservations bind only new-code claimers. VPS lane now runs Stage-2; **operator: Joshua's Mac worker must be stopped/relaunched** to fully close the window (it also raced verifications in prior sessions). The stage2scope0702 bootstrap it stole is completing fine on old code = the no-regression proof for unreserved/legacy claiming._
- 🟡 Additive migration `0059_worker_pools_and_job_reservations.sql`: `worker_pools` registry (heartbeated lease, exclusive, capabilities for Stage-4) + 4 nullable/defaulted reservation columns + partial index; operator-plane grants only, `takyon_app_runtime`/PUBLIC revoked (0057 pattern). **Applied cleanly in every rig throwaway DB during the test run.**
- 🟡 `claim_one` indexed reservation predicate replaces the `_PREFERRED_WORKER_*` SQL (zero references remain); `enqueue` stamps reservations from `ClaimScope`; all 3 requeue paths renew after_lease windows (f899da41 parity); `run_one`/`drain_tick`/`WorkerPool` thread the claiming pool identity; `WorkerPool` registers/heartbeats/drains/decommissions its `worker_pools` row (daemon heartbeat on its own conn so long handlers never lapse the lease).
- 🟡 Shell session ownership: console mints the session pool id up front and runs it EXCLUSIVE by default (plan Q5 posture); bootstrap+wake enqueues bind to the session scope (strict when exclusive); commit-mirror sub-jobs bind after_lease NEVER strict (busy size-1 pool must not deadlock its own parent turn); prefix/env/sidecar/pgrep triangle DELETED from cli.py + script. Self-caught edge fixed: explicit worker identity beats env (a `worker --once` inside a console must not adopt+decommission the session pool row); inline shell claimer adopts the session identity without registering.
- 🟡 Money invariant: reserve→settle→release byte-identical; rig test proves requeues never touch `reserved_billing_entry_id`; the full R1 reclaim-while-hold-open cycle test needs the billing rail (known rig gap → runs on the Stage-3 dev DB).
- **Rig proof so far (17-test `test_takyon_claim_scope_pg.py`, all green):** two exclusive scopes of the same owner NEVER cross-claim; strict holds while the owner pool lease is alive (no time escape) and SPILLS when the pool lapses/decommissions/never registered; exclusive pools claim nothing unreserved; after_lease reproduces the old grace-window exactly; idempotent replay never restamps; renewal on both requeue paths; registry lifecycle incl. draining-still-owns. Wide-suite failure set byte-identical to main baseline.
- **Verification done (lean, per your feedback):** full-suite failure-set diff vs main = only known env noise on untouched surfaces; the (interrupted) review workflow's one live lead was REAL and is FIXED — the `run()` drain loop wasn't passing pool identity into `claim_one`, which would have let an exclusive console pool strand its own strict bootstrap; fixed + regression-pinned (`test_worker_pool_run_passes_pool_identity_to_drain`) + an all-gates param-alignment rig test. Amended commit `78f5f2a5`.
- 🔑 **BLOCKED on Supabase sign-in (Chrome tab open at the dashboard sign-in):** applying migration 0059 to prod needs table-owner privileges for `ALTER TABLE jobs` — `jobs` is owned by `postgres` and **no VPS holds an owner-capable DSN** (`takyon_migration` can CREATE tables but not ALTER `postgres`-owned ones; verified directly on the operator + safebox hosts). Historical ALTERs went through the Supabase SQL editor.
- ⛔ **Structural deploy-rail gap surfaced (your decision):** every future migration that ALTERs an early table hits this same wall. One-time fixes possible: (a) deposit an owner-capable migration DSN into the safebox, or (b) `ALTER TABLE ... OWNER TO takyon_migration` for the app tables (run once as postgres). Recommend (b) — keeps role-scoped DSNs, no new super-credential in custody.
- **Remaining for ✅:** run 0059 in the Supabase SQL editor (after your sign-in) → live CLI E2E on Stage-2 code (strict session reservation observed in the jobs row + kill-session spill observed live) → land on main → deploy both hosts → progress ✅ with proofs.

### Stage 3 + 3b — RuntimeContext + env provisioner · ⬜ 🔑Auth0 → **UC3 ships**
- ⬜ `environment.py` (RuntimeContext.from_env, 7 slices, HostRole enum)
- ⬜ Env-scope the process-global caches (security-critical) + prod-literal boot assertion
- ⬜ Thread context into the isolated-turn subprocess spawn
- ⬜ `takyon env create|status|destroy` over `environments/*.yaml` (idempotent, receipted)
- **Proof:** clean slate → `takyon env create dev` → browser dashboard at `localhost:9119` → fresh business with real Stripe test checkout → **DB assertion: zero prod-plane rows changed**; `dev2` with zero human steps.

### Stage 4a — subuser box unblocked · ⬜ (chip ready) → most of **UC2**
_No new infrastructure._
- ⬜ `asyncio.to_thread` the blocking `/generate`//search + inline read handlers
- ⬜ `uvicorn workers=N` (Supavisor pooler role already provisioned)
- ⬜ Pool the 3+ per-request fresh psycopg.connects
- ⬜ Same fixes on the safebox app
- **Proof:** a stubbed 30s AI call no longer moves p95 of concurrent reads on the box.

### Stage 4b — N subuser replicas behind VPC LB · ⬜ 🔑DO → **UC2 ships**
- ⬜ VPC LB + repoint `subuser-origin.coscale.app` DNS + webhook relay upstream
- ⬜ Per-replica bootstrap (deno + linger + BindPaths) via the provisioner
- ⬜ Close replica blockers: force shared storage backend; action-source cache fan-out; PG-authoritative replay receipt; LB-resolve the ctx.generate hairpin
- ⬜ Per-replica scoped/revocable credentials
- **Proof:** kill one replica mid-traffic → zero failed customer requests beyond in-flight; replayed action returns cached success.

### Stage 4c — safebox headroom proven or scaled · ⬜
- ⬜ Load-test safebox at full 4b capacity to 3× headroom
- ⬜ If insufficient: broker replicas with per-replica key enrollment/revocation
- **Proof:** sustained target-RPS, safebox p95 flat; or kill-one-broker → zero dropped calls.

### Stage 5 — compositional pricing + monthly-only · ⬜ (parallelizable) → **UC4 ships**
_Independent file cluster — can run alongside the compute stages._
- ⬜ Monthly-only enforcement + fail-loud cap + legacy purge (dead SQLite branches, phantom quota, shims)
- ⬜ `PricedComponent`/`PlanComposition`/`compose_plan` (derived price/budget/gates, margin invariant)
- ⬜ Money-shape gate at the tool choke point (covers chat + bootstrap paths)
- ⬜ **Real Shopify integration (operator ruling — acceptance is real, not a stand-in):** connect a $0 Partner dev store via the existing Composio broker (Shopify toolkit confirmed; zero new runtime credential) 🔑; read the store's real plan fee via Admin GraphQL through the safebox; `/api/webhooks/shopify` rail (new HMAC verifier, safebox-side verification, existing provider-keyed dedup — no migration)
- **Proof (two legs):** ① cost basis **read from the real store** → compose → `plan_key-v2` minted with derivation receipt → new signup pays derived price → existing subscriber byte-identical; ② `shop/update` plan-change **webhook** updates the cost basis → auto-recompose mints `plan_key-v3`, receipted. Plus named refusal tests for every hallucination shape. **Stop line:** per-order/fulfillment/catalog rails stay OUT (archetypes project — different money shape).

### Stage 6 — RuntimeRail registry + BuildStep pipeline · ⬜ → feature modularity
- ⬜ Fat `RuntimeRail` object; generic dispatcher loop replaces per-rail if/elif; scanner regexes derived from client_methods
- ⬜ `BuildStep` protocol with declared phases (coordinate hunks with the canonicalization spine)
- **Proof:** a demo rail = one registry literal, live on every subuser replica with zero dispatcher edits; a sitemap BuildStep appears content-addressed in the R2 artifact.

---

## Dev environment: subuser or operator?

**Both — dev is an instance of the whole system, not one plane.** operator vs subuser is a deployment topology (same service, different `TAKYON_HOST_ROLE`), and the existing `combined` role serves both from one process. So **local dev = one combined-role process** (you create a business in the browser *and* hit its product API as a customer, in the same instance). **Split-role dev droplets** appear only when the split itself is under test — Stage 4a subuser load tests (real deno/systemd action sandbox; macOS runs actions un-sandboxed) and Stage 4b's two-replica LB proof, which runs on dev-with-split before prod. This is stated in the plan at the RuntimeContext section (§2.1).

---

## Rollback posture (from plan §5)

Every stage: revert the commit, rsync, restart. Schema stays (additive/nullable; old code ignores it; no down-migrations). Rehearsed once on dev after Stage 3. Two things are one-way by design: minted plan-key versions with live subscribers (grandfather), and receipts. Two things rollback doesn't cover (guarded by invariants instead): lease-vs-sweeper money timing (R1) and concurrent-session schema deploys (R5 → Q1).
