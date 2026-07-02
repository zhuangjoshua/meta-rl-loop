# Takyon Modularization Plan — OO Compute Plane + Environment

**Status: PLAN ONLY — no implementation. Awaiting operator go.**
Date: 2026-07-01. Produced from a multi-agent evidence pass (8 subsystem maps, 3 use-case traces, 2 competing architecture designs, adversarial judge + completeness critic; all counts below verified against the tree, not estimated).

## 0. The ask

Three changes are near-impossible today and define what "modular" must mean:

- **UC1 — worker affinity**: "when I SSH in, my workers must not be given to anybody else." A shell session's local worker pool should be *owned*, not shared.
- **UC2 — autoscaling backend (CORRECTED 2026-07-02: this means the SUBUSER plane)**: more capacity for the **subuser VPS** (134.209.123.8) — the plane that serves real product-app customer traffic (auth, records, actions, AI calls). Target: N replicas of the subuser runtime behind a load balancer, elastic with demand. **Not** the operator worker plane: operator burst capacity is already solved today by local Macs joining over SSH tunnels + safebox (the lane UC1's ClaimScope formalizes). §2.5's earlier queue-drainer design is being rewritten against a serving-plane investigation.
- **UC3 — dev environment (dev/prod split)**: a second, **prod-shaped** instance — own `TAKYON_HOME`, own Postgres control plane, its own **browsable dashboard**, and **real connections to the actual providers** (Stripe in test mode; real model/media/search keys under hard per-environment budget caps) — so the whole experience can be tested end-to-end as a user without touching prod data, prod ledgers, or prod customers. "Exact copy of prod" as a *config value*, not a hand-rolled ritual. A fully-stubbed **hermetic** profile remains as a secondary flavor for CI/fast tests only.
- **UC4 — compositional subscription pricing**: adding a feature to a product (Shopify, App Store presence, more AI credits, a new package) must recalculate the subscription price **authoritatively and modularly** — the price *derived* from priced components under a margin policy, not two freehand numbers the CEO types; and a hallucinated money change (price, budget, or money *shape*) must be refused semantically on **every** path, not only on autonomous wakes.

The refactor is judged by one test: **each UC becomes a small diff against a named abstraction.** Readability-only extractions are deferred to a separate track (§6) so they never block the UCs.

## 1. Diagnosis — what "not modular" concretely is

The domain logic is mostly fine; the missing things are **objects for compute, ownership, and environment**. Verified facts:

1. **No WorkerPool.** A "worker" is simultaneously a process (`run_worker_loop`, [worker.py:2808](hermes-agent-main/plugins/takyon/worker.py:2808)), N daemon threads sized by a raw env read (`TAKYON_WORKER_CONCURRENCY`, worker.py:2843), and per-job single-thread executors nested inside handlers. Pool topology is encoded by thread index (`dispatcher = index == 0`, worker.py:2934). Worker identity is a formatted string `worker-{host}-{pid}` (worker.py:2839) that grows `-N` suffixes per thread (worker.py:2930) — the string-format collision that forced band-aid commit 6bc61762.
2. **Four divergent compute launch paths**, no shared constructor:
   - `takyon worker` → `cmd_worker` ([takyon_cli/main.py:10364](hermes-agent-main/takyon_cli/main.py:10364));
   - the dashboard's embedded daemon thread ([takyon_cli/web_server.py:1956](hermes-agent-main/takyon_cli/web_server.py:1956), held in module globals);
   - inline execution in the interactive shell process (`_run_pg_ceo_wake_once`, [plugins/takyon/cli.py:2168](hermes-agent-main/plugins/takyon/cli.py:2168));
   - the dashboard operator turn, which **always** runs in a detached subprocess (`python -m tui_gateway.isolated_turn_worker`, spawned at [tui_gateway/server.py:2381](hermes-agent-main/tui_gateway/server.py:2381)) — a lane both earlier designs missed.
3. **"Affinity" is a stringly-typed triangle, not ownership.** A bash-formatted prefix + sidecar file + pgrep (`scripts/takyon-operator-prod.sh:80,83-121,719`), re-read from env in leaf CLI code (`_bootstrap_preferred_worker_claim_payload`, cli.py:2217-2264), stapled onto job payload JSON, and matched by SQL `LIKE` with a ~120s grace window ([jobs.py:85-93](hermes-agent-main/plugins/takyon/jobs.py:85), :297-320). It only covers fresh bootstraps (ordinary wakes carry no hint), and the only durable tenancy filter is the *business owner*, not the session (jobs.py:290-296) — so two SSH sessions of the same operator can never be isolated. All five recent affinity commits (6bc61762, f899da41, 81fb2844, 6da4ed79, 3bf5b1b6) patch *when* a claim leaks, never *who owns it*.
4. **No Environment object.** "Which environment am I" is reconstructed from **213** `os.getenv`/`os.environ` reads across 25 files in `plugins/takyon/` (81 in core.py alone). Prod IPs are hardcoded fallbacks (`137.184.75.57` core.py:12196, `134.209.123.8` core.py:12139, safebox proxy `10.116.0.2` core.py:7314/7400). An isolated dev instance today is a fragile conjunction of ~8 escape-hatch flags (`TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS`, `TAKYON_ALLOW_LEGACY_DB_ROLES`, `TAKYON_SAFEBOX_ALLOW_TOKENLESS`, …) that nothing validates as a whole. `_normalized_host_role` has **four divergent runtime copies** (runtime_app.py:123, app_actions.py:264, safebox.py:179, core.py:175) plus two more in test conftests, and they disagree on alias mappings.
5. **God objects and inverted layers.** [core.py](hermes-agent-main/plugins/takyon/core.py) is 34,802 lines fusing tool registration, path containment, rails registry, deploy topology, docker lane, and the `TakyonStore` whose `commit()` hides the only job-enqueue path (dual-writing `work_requests` + `jobs`, core.py:20039-20092). The backend job handler `_run_ceo_turn` imports from the interactive CLI module (worker.py:1108-1117) — a worker→UI layering inversion. Handlers live in a module-singleton dict `HANDLERS` (worker.py:2670). Process-global caches (`_MANAGED_SECRET_CACHE` keyed by name only, `_POSTGRES_POOLS`, the DSN memo at runtime_app.py:322) make in-process environment isolation impossible — and are a **security** hazard for UC3 (a stub env could read prod's cached key).
6. **Scheduler and executor are fused.** `jobs.run_one` claims *and* executes inline on the same thread (jobs.py:520,538,545). There is no node concept, no registry, no heartbeat; capacity is exactly "restart with a bigger env var".

**What is already the target shape (verified — extend, don't rewrite):** `jobs.py` (connection-injected, `FOR UPDATE SKIP LOCKED`, real-PG-tested), `wakes.py`, the money rails (`billing.py`/`app_usage.py`/`business_credits.py`/`ledger_gate.py` import only stdlib + ledger_gate/runtime_app — never core.py), the `StorageBackend` Protocol ([storage.py:634](hermes-agent-main/plugins/takyon/storage.py:634)) which already makes workspaces node-agnostic via R2/Supabase CAS, the docker-broker RPC boundary, the DI'd idempotent migration runner, and `gateway/session_context.py`'s task-local ContextVars.

## 2. Target architecture — five abstractions

**Placement: everything under `plugins/takyon/`** (packaged as `plugins.*` in pyproject; a top-level `takyon/` package would collide with the `./takyon` launcher and is not in the setuptools allowlist). No new language, no microservices, no message broker: in-process objects behind a composition root, shipped by the existing rsync+systemd rail.

### 2.1 `RuntimeContext` (the Environment) — `plugins/takyon/environment.py`

One frozen value object naming the instance, built **once** at process boot, carried via a ContextVar that **composes with** (does not replace) `gateway/session_context.py`:

```python
@dataclass(frozen=True)
class RuntimeContext:
    name: str                # 'prod' | 'dev' | 'hermetic'  (TAKYON_ENV)
    home: Path               # TAKYON_HOME
    host_role: HostRole      # ONE enum, collapsing the 4 runtime copies
    db: DatabaseProfile      # dsn_for(plane) + AccessPolicy(allow_local/allow_remote/allow_macos/required_roles|None)
    secrets: SecretAuthority # 'remote' (url+token) | 'local' (in-proc) | 'stub' — dev uses 'remote' against the DEV safebox
    providers: SpendPolicy   # 'live' | 'test' | 'stub' — dev runs the REAL gates (test-mode keys, capped budgets); 'stub' is hermetic-only
    hosts: HostMap           # operator/subuser/safebox targets — kills the IP literals as fallbacks
    domains: DomainProfile   # company base domain, dashboard URL
    infra: InfraPolicy       # systemd / R2 / Auth0 / DNS on|off — dev keeps them ON, pointed at dev twins; hermetic flips them off
    worker: WorkerProfile    # concurrency, poll cadence — was loose env

@classmethod
def from_env(cls, overrides=None) -> "RuntimeContext": ...   # the ONLY place env is read
def current_context() -> RuntimeContext: ...                  # ContextVar, set at boot
```

dev = `RuntimeContext(name='dev', db=dev PG, secrets=remote→dev safebox, providers=live-with-caps, hosts=dev hosts, domains=dev base domain, infra=on)` — an exact copy of prod as a value, differing **only in what the slices point at**, never in which code runs. Critically, the dev Postgres is provisioned with the **same role names as prod** (`takyon_operator_runtime` etc.), so the fail-closed role gate stays fully enforced in dev — no bypass flags, no relaxed paths. That is the strongest parsimony outcome: dev exercises byte-identical gates against twin infrastructure.

**Dev covers BOTH planes — it is an instance of the whole system, not one plane.** The operator/subuser split is a *deployment topology*, not a code fork: both VPSes run the same service under different `TAKYON_HOST_ROLE` values, and `combined` (the existing default role) serves both planes from one process. Dev's topology is therefore a profile field: **local dev = one combined-role process** (dashboard + product-customer API + local dev safebox in one instance — create a business in the browser *and* hit its product API as a customer); **split-role dev droplets** when the split itself is under test (Stage 4a subuser load tests with the real systemd/deno action sandbox — macOS runs actions un-sandboxed — and Stage 4b's two-replica LB proof, which runs on dev-with-split before prod).

hermetic = `RuntimeContext(name='hermetic', db=loopback+required_roles=None, secrets=stub, providers=stub, hosts=local, domains=localtest, infra=all_off)` — the fully-stubbed flavor for CI and fast local tests (the descendant of today's local PG rig). It is the *only* profile that relaxes a gate, and it never serves as the dev experience.

The fail-closed prod gates (`_enforce_database_url_policy`, `assert_takyon_pg_role`, `safebox._authority_mode`) become policy questions on one validated object instead of independent escape-hatch flags.

### 2.2 `ControlPlane` / `DatabaseGateway` — connection provider

```python
class ControlPlane(Protocol):
    def operator_conn(self) -> ContextManager[Conn]: ...
    def app_conn(self) -> ContextManager[Conn]: ...
    def safebox_conn(self) -> ContextManager[Conn]: ...
```

Backed by `RuntimeContext.db`; generalizes the DI seams that already exist (`runtime_app` connect helpers, `worker.py:2863` heartbeat conn factory). Removes the process-static DSN memo as the isolation blocker.

### 2.3 `ClaimScope` — durable worker ownership (UC1 keystone) — `plugins/takyon/claim_scope.py`

```python
@dataclass(frozen=True)
class ClaimScope:
    id: str
    owner_user_id: str
    session_key: str          # sourced from gateway.session_context when present — no 3rd identity mechanism
    host: str
    exclusive: bool           # True = "my workers are not given to anybody else"
    kind_filter: tuple[str, ...]
    lease_expires_at: datetime

open_scope(conn, *, owner_user_id, session_key, host, exclusive, kind_filter) -> ClaimScope
heartbeat_scope(conn, scope_id); close_scope(conn, scope_id)
```

**DB shape (refined by the contracts pass, Appendix A):** the reservation lives in **indexed columns on `jobs`** — `reserved_pool_id`, `reservation_policy`, `reservation_expires_at` — so the claim predicate becomes an indexed equality instead of a `payload->>` regex, plus **one pool-registry table** with a heartbeated lease (created in Stage 2 for scopes, extended in Stage 4 into the node registry — one table, not two). `jobs.claim_one` gains `claim_scope: ClaimScope | None`; when set, one policy check **replaces** the four `_PREFERRED_WORKER_*` SQL fragments. `enqueue` gains a scope so the shell binds bootstraps *and wakes* to its pool at creation.

Reservation semantics via an explicit `FallbackPolicy`:
- `any` — no reservation (today's default for non-affinity jobs);
- `after_lease` — target pool first, spillable after expiry (reproduces today's grace-window behavior as a *config value*, so nothing regresses at cutover);
- `strict` — never claimable by another pool **while the owning scope's lease is alive**; if the SSH session dies, the scope's heartbeat lapses and its jobs spill rather than strand.

`exclusive=True` cuts both ways: the scope's workers claim only its jobs, and its jobs are claimable only by it. One mechanism — the payload-hint triangle is deleted, not paralleled.

### 2.4 `WorkerPool` — the one worker constructor — `plugins/takyon/worker_pool.py`

```python
class WorkerPool:
    def __init__(self, *, scope: ClaimScope | None, size: int, dispatcher: bool,
                 control_plane: ControlPlane, handlers: HandlerRegistry,
                 kinds=None, owner_user_id=None): ...
    def tick(self) -> dict: ...   # delegates to drain_tick / jobs.run_one, passing scope
    def run(self) -> int: ...     # the process shell, body lifted verbatim from run_worker_loop
```

`size`, `dispatcher`, and identity become constructor args (killing the env-int read, the hostname+pid string, and the thread-index dispatcher trick). All four launch paths converge here: `cmd_worker`, the dashboard embedded thread, the inline shell runner, and (minimally — see Stage 1) the isolated-turn subprocess. `HANDLERS` is promoted to an injectable `HandlerRegistry` so a hermetic/test instance can register stub handlers without monkeypatching a module global (dev uses the real handler set).

### 2.5 Subuser serving-plane scale-out — the UC2 keystone (CORRECTED: this plane, not the operator queue)

**What UC2 actually is (operator correction, 2026-07-02):** capacity for the **subuser VPS** — the plane serving product-app customers (auth, records, actions, AI calls). Investigation verdict, all file:line-verified: **the subuser runtime is already fundamentally stateless**, the fastest wins are process-model fixes on the one box, and true scale-out is textbook horizontal replicas behind a VPC load balancer. No queue, no drainers, no new coordinator — shared Postgres/R2/safebox already *are* the state.

**Verified topology (corrects an older memory):** the Cloudflare product worker already sends `<slug>.coscale.app/api/*` **directly** to `subuser-origin.coscale.app` via `resolveOverride` ([worker.js:145-215](deploy/cloudflare/product-worker/worker.js), `SUBUSER_API_HOSTS="*"`) — the operator hop is already out of the product data path. The only remaining operator-Caddy relay is the Stripe webhook (`app.fourmanifold.com/api/webhooks/stripe` → hardcoded `10.116.0.3:80`).

**Already replica-safe (nothing to build):** sessions (PG token-hash reads, no memory cache), rate limits (DB-atomic epoch-aligned upsert — *not* N-multiplied), usage reserve→settle→release (budget row locks + UNIQUE reservation_key), webhooks (HMAC + global event-id dedup + idempotent grants), entitlements (per-request SQL, no plan cache), static (R2 edge), and **no worker/cron runs on the box at all** (`TAKYON_DASHBOARD_EMBEDDED_WORKER=0`; sweepers run on the safebox-authority worker).

**"Why is /api the only thing this plane serves — doesn't that fight modularity?"** It's a *plane boundary*, not a feature ceiling, and every future feature lands in one of two lanes automatically:
- **Static/UI features** (new pages, new product UI) ship inside the built bundle → served from **R2 edge**, replicas untouched. That lane scales infinitely for free.
- **Dynamic features** (anything a customer *does*: a new rail like comments or POD orders from the archetypes plan, new webhooks — Shopify's would land beside Stripe's — new AI capabilities, and future **mobile clients**, which would hit this same app-plane API) become new routes on this plane and appear on **every replica automatically**, because replicas are copies of one service.
What the role allowlist *refuses* — dashboard, `/api/ws`, `/api/pty`, `/v1`, builds, agent turns — is refused deliberately: the customer-facing plane must not expose operator control surfaces. That's the isolation rail working, not a modularity failure. The genuine modularity gap is *inside* the plane: each new rail today needs a hand-written `if/elif` branch in the web_server dispatcher. The `RuntimeRail` registry fixes exactly that and is **Stage 6 of this plan** (promoted from the deferred track — it is load-bearing for UC2's "add features" story, so it cannot be deferred): "add a feature to the subuser plane" becomes one registry entry whose routes materialize on all replicas behind the same role gate.

**The safebox is deliberately NOT replicated.** It is the secret authority — a singleton root of trust; replicating it would multiply key custody by the replica count, which is a security decision, not a capacity one. Honest consequences: it is a single point of failure (paid calls fail *closed* when it's down — by design) and, since every replica's `/generate`//`search` brokers through it, at high traffic it becomes the next throughput chokepoint after Stage 4b. This is planned, not deferred: **Stage 4a applies the same process-model fixes to the safebox app in the same pass** (it is also a single-process uvicorn service brokering long provider calls), and **Stage 4c load-tests the safebox to a stated headroom target** — if it can't hold the target, 4c ships the broker-replica path (stateless replicas behind a VPC LB; durable state is PG + its env file) with per-replica key enrollment and revocation as the custody control. The chokepoint gets measured and either cleared or scaled *inside this plan*, not "when that day comes."

**The real ceilings at N=1 (fix first — Stage 4a):**
- **Head-of-line blocking — the tightest ceiling and a live incident waiting:** `/generate` and `/search` make a *blocking* safebox→provider call **inline on the single event loop** with a 180s timeout (web_server.py:3020/3031 → safebox.py:275). One slow AI call stalls every product customer on the box. App reads also block the loop on their DB round-trips (~4 RLS GUC round-trips ≈120ms each, core.py:14856).
- **Single uvicorn process, `workers=` never set** (web_server.py:10921) — and the subuser hardening plan already provisioned the Supavisor pooler role *for exactly this* (subuser-hardening-plan-for-codex.md C1); the flag was simply never applied.
- **Connection hygiene:** 8-conn in-process pool (20s wait → TimeoutError) plus **3+ unpooled `psycopg.connect` per logical request** (rate-limit, generate, search, DI) — a connection storm against the shared pooler as load grows.

**Then replicas (Stage 4b) — operational, not architectural:** a DO LB (or RR DNS) **inside the 10.116.0.0/20 VPC** (already inside the `fourmanifold_edge_only` allowlist, so no origin-lock change) → repoint the single DNS name `subuser-origin.coscale.app` at it (zero worker-code change) → repoint the webhook relay upstream → bootstrap each replica identically via the Stage-3b provisioner (deno + `loginctl enable-linger` + the `/run/user/<uid>` BindPaths drop-in, per `deploy/takyon-subuser/bootstrap-host.sh`; identical env pinning).

Replica blockers to close (all small, none architectural):
- **Force the shared storage backend on every replica** so the `LocalStorageBackend` media fallback can never be selected (app_media.py:107, storage.py:201-207) — else an upload lands on one box and 404s on the rest.
- **Action-source distribution:** actions read `product/site/actions/*.ts` from the local mirror (`sync=False`, app_actions.py:1328), shipped by a deploy-time tar from the operator host — fan that out per replica or hydrate read-through gated on the DB head revision (content-addressed per head_revision, so consistency holds; the risk is staleness between syncs).
- **Replay UX:** the action idempotent-replay fast path reads a *local* receipt; on a different replica it falls through to the DB reservation and returns a replay-conflict instead of the cached success — make the replay read authoritative from PG. (Correctness already holds: the DB reservation prevents double-execution.)
- The `_active_business_runs` module-global single-flight lock (app_actions.py:216) is per-replica; the DB reservation key is the real guard — accept best-effort, or `pg_advisory_xact_lock` if strict single-flight matters.
- The action hairpin (`ctx.generate` re-entering the business's own origin) must resolve to the LB, not a specific box.

**Where the compute-plane abstractions still apply:** the Stage-2 pool registry doubles as the **replica registry** (enrollment, heartbeat, drain — and the per-node credential requirement stands for replicas exactly as written below). Elastic operator-plane drainer nodes remain possible later through the same `WorkerPool` seam, but they are **not UC2**: operator burst capacity is served today by local Macs over SSH tunnels + safebox — the very lane UC1's ClaimScope formalizes.

**Parsimony:** Stage 4a is three code-hygiene fixes on the existing box — no new infrastructure at all. Stage 4b is standard horizontal scaling of an already-stateless HTTP server; the LB is the only new moving part, and "autoscaling," if ever wanted, is a droplet count on the LB pool — decoupled from every Takyon abstraction.


### 2.6 `EnvironmentProvisioner` — the backend half of UC3 (`takyon env create`)

`RuntimeContext` solves the *code* half of UC3 (one object, no scattered reads). The *infrastructure* half — standing up the twins — must not remain a console ritual. Key fact: **almost no provider needs a new account.** Nearly every dependency supports an environment twin *inside* the accounts that already exist:

| Dependency | Dev twin | New account? |
|---|---|---|
| Postgres | new database + prod-named roles on an existing server (or local/one managed instance), migrations applied | no |
| Safebox | second instance of our own code (process/container) with its own token + `.env` key set | no |
| Stripe | **test mode is a built-in parallel universe** — test keys + test webhooks in the same account | no |
| Auth0 | new *application* (client id/secret, callback URLs) in the same tenant, via the Management API | no |
| Cloudflare | `*.dev.` DNS records + a second R2 bucket + worker route, same account, via API | no |
| Compute | local Mac, a compose stack on an existing host, or one small droplet via the existing DO API token | no |
| Model/media/search (Gemini/FAL/Tavily) | additional keys under the same accounts, with per-env budget caps | no |
| Social posting (X/Composio) | a dedicated dev account **if** you want to test live posting without posting as the real identity | **yes — the one genuine exception** |

So the plan adds an **environment provisioner**: a declarative manifest per environment (`environments/dev.yaml` — names, domains, plans, caps) and an idempotent `takyon env create|status|destroy <name>` command that creates the API-provisionable twins above, applies migrations, seeds the dev safebox with its key aliases, registers Stripe test webhooks and Auth0 callbacks, and writes the resulting pointers into the environment's config — emitting receipts for every resource it creates. It runs as an operator-authority action resolving the platform admin tokens (DO/Cloudflare/Auth0-mgmt/Stripe) via the safebox, per the existing secret rails. This is deterministic infra-rail code, squarely inside the "durable rails" exception: idempotent, receipted, destroy-safe.

**Autonomous by default; the human surface is one irreducible act, not a standing chore** (verified against the current safebox, not assumed): the safebox *already* holds and uses **Cloudflare** (`CLOUDFLARE_API_TOKEN` — it creates product edge routes today via `/v1/cloudflare/product-edge-route`), **Vercel** (`VERCEL_TOKEN`), and **Stripe** (`STRIPE_SECRET_KEY`). So DNS/route, Vercel, and Stripe-test twins are provisionable autonomously *right now* — no human step. The only admin credentials the safebox does **not** yet hold are **DigitalOcean** (no droplet token or provisioning code exists) and the **Auth0 Management API** (it has login-side Auth0 only, not app-creation authority). The irreducible human act is therefore narrow and one-time-ever: for each *new* provider capability the safebox doesn't already hold, a human deposits that one root credential once (you cannot autonomously mint a DigitalOcean account from nothing — something must anchor the root of trust). After the deposit, every downstream call — create droplet, create Auth0 application, create DNS, register Stripe webhook — is autonomous, and every subsequent environment (dev2, a per-branch instance) is one command with zero human involvement. The only genuinely recurring human gate is signing up a brand-new dev *social* account (captcha/phone is an external-world gate, not a Takyon design choice) — and that is off the critical path, because dev runs in test-mode without live posting until you want it.

**No new VPS is required either.** Dev compute starts local; and once Stage 4 lands, a dev droplet is just cloud-init running the node bootstrap with `TAKYON_ENV=dev` — it registers itself into the dev environment's pool registry like any other node. Environments own *pointers*; nodes attach elastically.

### 2.7 `PlanComposition` — authoritative subscription pricing (UC4 keystone)

**Investigated state (all verified, file:line).** A subuser plan is a flat 6-scalar row (`app_plan_policies`: plan_key, tier, `price_cents`, interval, `included_ai_budget_microusd`, quota + loose metadata). The defects:

- **D1 — prices are freehand.** `business_upsert_app_plan` ([core.py:22234](hermes-agent-main/plugins/takyon/core.py:22234), schema :33841) is a thin passthrough: the CEO types raw `price_cents` and raw `included_ai_budget_microusd`. The bootstrap prompt literally says "Set `included_ai_budget_microusd` together with `price_cents`" ([cli.py:1993](hermes-agent-main/plugins/takyon/cli.py:1993)); the bootstrap seed is a flat hardcoded $5 allowance (core.py:615) regardless of COGS. No cost model exists behind either number.
- **D2 — the one price↔cost link is the blunt clamp you suspected.** `_monthly_plan_price_cap_microusd` = **100% of the monthly price** (zero margin floor), it **silently clamps** instead of refusing ([app_entitlements.py:293-296](hermes-agent-main/plugins/takyon/app_entitlements.py:293)), it exists in **two duplicated copies** (also core.py:11803-11824), applies **only to `month`** — year/one_time budgets are uncapped — and the clamp never appears in any receipt (the upsert receipt emits only plan_key/price/stripe_price_id, core.py:18920). Your instinct that "the hardcoded thing" is not correct is right on all four counts.
- **D3 — features carry no cost anywhere.** `PRODUCT_RUNTIME_RAILS` entries are purely functional (owner_skill/tools/endpoints/worker_contract — no cost key, core.py:300-502); `plan.metadata.features`/`model_allowlist` are boolean 403 gates ([ai_gateway.py:325-344](hermes-agent-main/plugins/takyon/ai_gateway.py:325)); and cost lives in **three disjoint namespaces** with no mapping: usage_pricing µUSD (AI COGS), creative-credit opaque units (core.py:28845), and plan `price_cents` (retail). Rolling a feature's true cost into a retail price is structurally impossible today.
- **D4 — external fees have no home, and intervals are mishandled.** A Shopify per-store fee or the App Store $99/yr has no column, table, or ledger anywhere (grep confirms). Separately, the runtime treats `included_ai_budget_microusd` as a *monthly* figure regardless of `billing_interval` (weekly pro-ration ×7/30 with no interval check, ai_gateway.py:364-381) — combined with the uncapped year/one_time budget this is a live unbounded-COGS exposure. `included_action_quota` is a phantom component: stored, mirrored, enforced by nothing.
- **D5 — semantic validation is advisory and path-dependent.** `plan_validation_warnings` "gates nothing" by its own docstring (app_entitlements.py:172-193). The structural wake ban (`_refuse_on_autonomous_wake`, core.py:8064 — built after the Roomier credit-packs incident) fires **only** when `task_kind == 'ceo_wake'`; operator-chat and bootstrap turns write plans with zero money-shape validation. A chat turn can still mint a `one_time` "credit-pack" plan for a subscription business — the incident's shape, one path over.
- **D6 — price and Stripe can drift.** `stripe_price_id` is authoritative at checkout (core.py:24911); `price_cents` mints the Stripe price only once (`_ensure_stripe_price` returns early if already set, core.py:21412), so a changed `price_cents` on an unfrozen plan never reaches Stripe.

**The two money levels are disjoint by construction** — L1 revenue (`app_revenue_events`) and L2 AI COGS (`app_usage_events`) join only in the read-only, incomplete `calculate_pulse.margin` (no payment fees, no external fees, estimates when actuals are missing; core.py:16405). The payout split already *withholds* the retail included-budget from owner payout at checkout ([app_payments.py:193-212](hermes-agent-main/plugins/takyon/app_payments.py:193)) but never trues it up against real spend.

**Operator decision (2026-07-02): subuser plans are MONTHLY-ONLY, and the legacy machinery goes.** This *shrinks* UC4 — the whole interval axis collapses, so the year/one_time cap extension and the ÷12 pro-ration fix die by construction. Scoped precisely (audit-verified):

- **Boundary:** this touches the *subuser* plan path (`app_plan_policies`) only. The operator control-plane rail — `control_api.py:259` reads a live Stripe subscription item's interval to pro-rate the *operator's* weekly agent allowance (`TAKYON_OPERATOR_PLANS_JSON` territory) — is a different rail and stays untouched.
- **The interval axis collapses (mostly a DELETE diff):** both byte-identical alias maps (app_entitlements.py:42-54 and core.py:11787-11799), both validation sets, both duplicated cap copies (keep one, always applied), the checkout `mode='payment'` one_time branch (core.py:24842 → always subscription), the Stripe `recurring[interval]` conditional (:21432 → always month), the pulse-MRR year-CASE (:16479), the OpenMeter one_time skip (:21124) and P1Y cadence branch (openmeter_backend.py:105). `_owner_payout_split`'s month-guard becomes unconditional (no behavior change).
- **Legacy inventory (removed with it):** the dead SQLite plan-write branches — `_db_backend()` returns `'postgres'` unconditionally (core.py:13717-13730), so the raw SQLite upsert (core.py:18857-18918, which still writes columns migration 0006 dropped), the bootstrap SQLite INSERT (:18481-18514), and the SQLite DDL (:14363-14380) are unreachable; the phantom `included_action_quota` (stored, frozen, mirrored, enforced by nothing); the zero-valued `_DEFAULT_USER_MONTHLY_BUDGET_MICROUSD` shim (ai_gateway.py:352); and — once composition lands — the freehand `price_cents`/budget input path itself.
- **New-write gate contract:** refuse `interval != 'month'` **unless** the write is an idempotent re-pass of an existing frozen non-month row's identical terms — verified compatible with `GrandfatheredPlanFrozen` (it compares full term sets; identical terms → no raise), so grandfathered year subscribers keep serviceable rows (metadata/Stripe-linkage edits still work) while no new non-month plan can be minted.
- **Migration hazard (must check before deploy):** query prod for `billing_interval != 'month'` rows and their active entitlements; keep read-side year handling (MRR, OpenMeter cadence) only for the lapse-out window of any such subscribers, then delete it.

**What is already right (preserve, build on):** the runtime enforcement is genuinely solid — reserve→settle→release fail-closed at 402, plan-derived-or-0 with one centralized resolver both spend paths share, priced fail-closed from `usage_pricing`. `GrandfatheredPlanFrozen` is a non-bypassable invariant (repricing mints a new immutable plan_key version). `FakeBillingRejected` blocks evidence-less grants; credit mint is webhook-authority-only. And the archetypes plan already designed the right shape for physical goods: `list_price ≥ landed_cost × (1 + margin_floor)` with the cost basis stored on the row — UC4 generalizes exactly that gate to subscription composition.

**Target abstraction — plans become compositions; economics become derived:**

```python
@dataclass(frozen=True)
class PricedComponent:
    kind: str            # 'ai_allowance' | 'external_fee' | 'feature_rail' | 'credit_grant' | ...
    key: str             # 'ai_allowance', 'shopify_store', 'app_store_presence', ...
    cost_basis: CostBasis
    #   metered(ceiling_microusd_month)      — AI allowance; ceiling priced via usage_pricing (the ONLY cost SSOT)
    #   fixed(fee_microusd_month)            — external per-seat/recurring fee, normalized from any interval
    #   per_unit(unit_cost, included_units)  — quota-shaped components (replaces the dead action quota)
    grants: dict         # what it turns on: metadata.features / model_allowlist entries, rail, credits

@dataclass(frozen=True)
class PlanComposition:
    components: tuple[PricedComponent, ...]
    margin_policy: MarginPolicy      # margin_floor + price-point rounding rule

def compose_plan(composition) -> ComposedPlan:
    """price_cents, included_ai_budget_microusd, features, model_allowlist — ALL DERIVED, never typed.
    Monthly-only (operator decision): no interval axis, everything is µUSD/month by construction.
    Margin invariant (generalizing the archetype gate, replacing the 100% clamp):
        sum(monthly COGS ceilings) <= price × (1 − margin_floor).
    Violations REFUSE with the exact figures — no silent clamp, ever. Receipts carry the full derivation."""
```

Plus a **money-shape gate** at the same choke point as the wake ban (top of the plan tool, so it covers chat and bootstrap too): every plan write validates against the business's declared money shape (subscription / credit-packs / COGS pass-through — the archetype registry's axis). Changing *shape* requires changing the declared record first, an explicit operator-approval affordance — a hallucinated "switch to credit packs" refuses on **every** path.

Repricing keeps the grandfather rail: `compose_plan` never mutates a live plan_key — it mints the next version (`plan_key-v2`) with a receipt showing the component-level delta ("`+ shopify_store` (fixed $9.00/mo COGS) → price $19 → $29"), and routes new checkout to it. The CEO's authority moves **up a level**: it chooses components and (within policy) a margin — it never touches the numbers.

**"Two levels, fluid":** L2 stays exactly the metered rails that exist (usage rail, priced by `usage_pricing`; plus a small new external-fee COGS record so Shopify/App-Store fees exist somewhere); L1 becomes the derived output of the composer over those L2 bases. `calculate_pulse.margin` then reports realized margin against the *same* component model the price was derived from — closing the loop the payout-withholding skeleton already hints at.

**Authority over `takyon-business-archetypes-plan.md` (operator ruling, 2026-07-02): THIS plan is authoritative.** The archetypes plan is subordinate design material — useful for its archetype registry, approval-rail spec, mobile/commerce research, and provisioning ledger — but wherever the two touch, the rulings below govern, and the archetypes plan must be revised against this plan before any of it is implemented. It does not constrain this plan.

*What UC4 provides that the archetypes plan builds on (it includes, not duplicates):*
- **The pricing engine every archetype assumes.** The archetypes provisioning ledger is full of fixed recurring platform costs with no pricing home — Apple $99/yr, EAS $19/mo, RevenueCat 1%, Shopify $39/mo/store. `PricedComponent(external_fee)` is exactly where the per-business ones compose into a subscription price; a `mobile_app` business's plan composes `ai_allowance + store costs` the same way the UC4 Shopify test composes the store fee. Without UC4, every archetype re-invents freehand pricing.
- **One `MarginPolicy`, two money shapes.** UC4's margin invariant and the archetypes' catalog gate (`list_price ≥ landed_cost × (1+floor)`) are the same policy object applied to monthly vs per-order shapes. UC4 builds `MarginPolicy` once; `business_upsert_catalog_product` (archetypes P1) consumes it with per-archetype floors — which is precisely Q11's per-archetype-override recommendation.
- **The money-shape gate's eventual SSOT.** UC4 ships the minimal per-business `money_shape` record (Q12); the archetype registry (archetypes §1.1) later subsumes it — each archetype declares its money shape(s), and `money_shape` becomes a derived attribute of `businesses.archetype`. The shape-change **approval affordance is built once to the archetypes §1.5 `operator_approvals` spec** (idempotent on payload digest, TTL, single-consume, receipted) — whichever plan lands first builds it; the other consumes it.
- **A front-run slice of archetypes P3.** UC4's real-Shopify test ships `business_connect_shopify` v0 (Composio connection + plan-fee read + `shop/update` webhook). Archetypes P3 *extends the same tool* with catalog sync — one tool, never two.

*Where the archetypes plan conflicted (this plan's rulings govern):*
1. **One-time payments vs monthly-only (the material one).** The archetypes plan prices digital products and physical checkout as "existing Stripe one-time" — which today means the `one_time` plan interval and the `mode='payment'` checkout branch that **monthly-only deletes**. Resolution: monthly-only stands *for plans* — plans are recurring subscriptions, full stop; one-time purchases (digital delivery, physical orders) ride the **order money shape** (the archetypes' own `app_orders.py` direction), never `app_plan_policies` rows. The archetypes plan's digital/commerce checkout must be specced against order-shaped one-time PaymentIntents, not one_time plans, when that project starts.
2. **Shopify connection mechanism.** Archetypes specced a per-store Admin API token → safebox alias `shopify`; UC4's investigation confirmed the Composio Shopify toolkit and chose it (zero new runtime credential, reuses the brokered forward). Resolution: **Composio is canonical**; archetypes P3 reuses the same connected account for catalog sync, and the direct-token alias is added only if sync volume/latency later demands it.
3. **Per-transaction % (store cuts, processing fees).** Both plans decline to model it as a component — archetypes handles Apple/Google cuts as a provider-aware *payout basis* with labeled receipts (§2.6); UC4 flags % as a third cost shape with no `CostBasis` kind yet. Consistent, but when IAP lands, `compose_plan` may need a percentage-aware basis — recorded as a future extension, not built by either plan now.

## 3. What each use case becomes

- **UC1** = one migration (pool registry + three reservation columns on `jobs`) + one WHERE-clause change in `claim_one` + the shell opening a scope and binding its enqueues + deleting the prefix/env/sidecar triangle. Provable: two concurrent SSH sessions of the same operator cannot drain each other's jobs.
- **UC2** = first, three process-model fixes on the existing subuser box (unblock the event loop, `uvicorn workers=N`, pool the connections) — most of the capacity win with zero new infrastructure; then N stateless replicas behind a VPC load balancer, reached by repointing one DNS name (`subuser-origin.coscale.app`). Capacity = workers × replicas; every piece of durable state already lives in shared PG/R2/safebox.
- **UC3** = `TAKYON_ENV=dev ./takyon`. `from_env()` builds the dev profile; the DSN/role/safebox/host/spend gates read from the context; the dashboard boots against the dev control plane and the dev safebox vends the dev key set — so you open the dashboard in a browser, create a business, and exercise real provider calls (Stripe test checkout, real model/media generation on capped budgets) end to end. Boot assertion fails loudly if any resolved host/DSN/key is a prod literal (`137.184.75.57` / `134.209.123.8` / `67.205.158.170` / `10.116.0.2`) while `name != 'prod'`.
- **UC4** = adding a feature is adding a `PricedComponent`. The composer derives the new price/budget/gates from the component's cost basis under the margin policy, mints the next plan_key version (grandfathering intact), routes new checkout to it, and emits a receipt with the component-level price delta. A hallucinated price is impossible (numbers are derived, not typed); a hallucinated money-shape change refuses at the tool choke point on every path.

## 4. Migration stages (each independently shippable + provable)

**Stage 0 — Pin behavior; zero production change (~300-500 LOC of tests)**
- Characterization tests on the local PG rig for: current `claim_one` affinity semantics (prefix + window + owner gate); the `job.enqueue` dual-write (`work_requests` + `jobs` mirror, core.py:20039-20092); the `run_one` reserve→settle→release ordering (jobs.py:554-579).
- Import-graph guard test: `ledger_gate`/`billing`/`app_usage`/`business_credits` import only stdlib + ledger_gate/runtime_app — forever.
- Table-driven `HostRole` test asserting **every** alias mapping of all six `_normalized_host_role` truth tables (4 runtime + 2 test-conftest) before any collapse.
- *Recommended hardening:* add a Postgres service job to CI. Today `tests.yml` provisions no PG and every PG-gated suite silently skips — the whole plan's per-stage proof otherwise rests on manually running the local Mac rig.

**Stage 1 — Extract `WorkerPool`; converge the launch paths (behavior-preserving, ~250 LOC new / ~150 moved)**
- Wrap the existing `drain_tick`/`run_one` — bodies lifted verbatim. `cmd_worker`, the dashboard embedded thread, and the inline shell runner construct a `WorkerPool` instead of open-coding loops. `scope=None` everywhere (no semantics change). The `TAKYON_WORKER_PROCESS` env write stays for now.
- Register the **fourth lane**: the isolated-turn subprocess is documented as session-owned compute; its full remodel is out of scope, but Stage 3 must thread the environment into its spawn payload/env (see risk R4).
- Fix the worker→CLI layering inversion while moving: the model-config/progress helpers `_run_ceo_turn` imports from `cli.py` move into a neutral module.
- Prove: Stage-0 tests green; fresh-business browser E2E (bootstrap streams, tasks complete).

**Stage 2 — `ClaimScope` + real reservation; UC1 ships (~400 LOC + 1 migration)**
- Additive-only, idempotent-by-construction migration per `db/runner.py` convention (lexical apply, no version table): the pool-registry table + three nullable reservation columns on `jobs` (`reserved_pool_id`, `reservation_policy` default `'any'`, `reservation_expires_at`) with a partial index on queued rows (Appendix A §3). **Mixed-version protocol:** migrate DB first (old code ignores the columns), then code; rollback = revert code, leave schema; `NULL`/`'any'` = exactly today's behavior, so dashboard/VPS workers are unaffected until they opt in. Cut over the existing grace-window jobs to `after_lease` (behavior-identical) before enabling `strict` for shell scopes.
- `claim_one(claim_scope=…)` replaces the `_PREFERRED_WORKER_*` fragments; delete `_bootstrap_preferred_worker_claim_payload`, the two env vars, and the bash prefix/sidecar/pgrep machinery.
- **Money-safety invariant (new, from the adversarial review):** lease-expiry reclaim must first finalize the dead attempt (`fail_if_still_owned`) and release its budget reservation before the job is re-claimable. The billing/usage sweepers reconcile by *reservation age*, not worker identity — an unordered reclaim could double-reserve. Lease TTL and the sweeper windows must be explicitly ordered, with a PG-rig test for "reclaim while reservation open".
- **Escape hatch (hard rule):** the reserve→settle→release block moves byte-for-byte or not at all. If claim/execute can't be split without editing it, don't split — executor nodes call `run_one` directly.
- Prove: PG-rig test "two scopes of the same owner never steal each other's jobs"; live E2E with two concurrent SSH sessions; fresh-business E2E.

**Stage 3 — `RuntimeContext`; UC3 ships (~500 LOC + ~200 edited call sites + dev-twin provisioning)**
- `from_env()` built once in the launcher/`cli.main`. Route **only** the load-bearing reads through it: DSN/plane resolution, PG role set, safebox authority mode, VPS/host targets, base domains, spend gates, worker profile. The ~180 cosmetic env reads stay put — parsimony over purity.
- Collapse the four `_normalized_host_role` copies into the `HostRole` enum (guarded by the Stage-0 table test).
- **Env-scope the process-global caches** (security-critical): `_MANAGED_SECRET_CACHE` (name-only keyed today), `_POSTGRES_POOLS`, the DSN memo, `_loaded_env_paths` — else a dev instance silently reads prod's cached key or DSN.
- Thread the context into `TakyonStore` via its *existing* constructor args (root/database_url/database_plane); the 20+ bare `TakyonStore()` sites pull `current_context()`.
- Thread the context into the isolated-turn subprocess spawn (explicit payload/env), so a dev dashboard turn provably runs against dev DB/safebox/budgets (and hermetic's `SpendPolicy=stub` provably reaches it).
- Spend gates stay **real** in dev: the same reserve→settle→release rails against the dev ledger tables, Stripe **test-mode** keys, hard per-environment budget caps. `SpendPolicy.stub` (receipted no-op before key resolution) exists for the hermetic profile only. Per-business test mode is untouched — distinct guardrails on one path, not a second path.
- **Dev-twin provisioning:** Stage 3 itself may bring the twins up manually once (a bounded checklist: dev Postgres with prod role names + migrations; dev safebox instance with the dev key set — Stripe TEST secret + webhook secret, capped model/media/search keys; dev Auth0 application; dev domains/R2 or local serving) — but the ritual is then immediately automated by Stage 3b. See §2.6 and open questions Q7–Q10.
- Prove — hermetic: full PG suite green **on macOS** (today hard-blocked). Prove — dev: boot it, open the dashboard in a browser, create a fresh business end-to-end with real provider calls (Stripe test checkout, real generation on capped budgets), verify zero writes to the prod control plane, prod-literal boot assertion holds.

**Stage 3b — `EnvironmentProvisioner`; UC3's backend half ships (~600 LOC + `environments/*.yaml`)**
- Build `takyon env create|status|destroy <name>` per §2.6: idempotent, receipted twin-creation over the provider APIs (PG database + roles + migrations, safebox instance + seeded aliases, Cloudflare DNS/R2/worker route, Auth0 application, Stripe test-mode webhooks, optional droplet). Admin tokens resolve via safebox — never `os.environ`.
- Destroy must be conservative: refuse while the environment has live nodes/pools registered or non-empty ledgers unless forced; receipts for every deletion.
- Prove: from a clean slate, `takyon env create dev` → `TAKYON_ENV=dev ./takyon` → dashboard in a browser → fresh business E2E with Stripe test checkout; re-running `create` is a no-op; `destroy` removes exactly what the receipts say it created.

**Stage 4a — Subuser box unblocked; most of UC2's value ships with zero new infra (~150 LOC)**
- Wrap the blocking broker calls (`/generate`, `/search`) and the inline read handlers in `asyncio.to_thread`, matching the pattern the action path already uses (web_server.py:3093) — removes the 180s head-of-line stall, the single tightest ceiling.
- Enable `uvicorn workers=N` in `start_server` + the subuser ExecStart — the Supavisor pooler role was already provisioned for exactly this and never applied.
- Collapse the 3+ per-request fresh `psycopg.connect`s onto the pool; size `TAKYON_PG_POOL_SIZE` × workers against the shared pooler budget.
- **Same pass, safebox app:** apply the identical fixes to the safebox service (workers, non-blocking proxying of the long provider calls) — it sits on every `/generate`//`search` path and must not become the ceiling the moment the subuser box stops being one.
- Prove: load test on the dev environment — concurrent `/generate` calls no longer stall unrelated reads; repeat off-peak on prod; fresh-business E2E unchanged.

**Stage 4b — N subuser replicas behind a VPC LB; UC2 ships (operational + ~200 LOC)**
- LB (or RR DNS) inside the `10.116.0.0/20` VPC (already inside the `fourmanifold_edge_only` allowlist); repoint `subuser-origin.coscale.app` at it — zero Cloudflare-worker code change; repoint the operator-Caddy webhook relay upstream from the hardcoded `10.116.0.3:80` to the LB.
- Per-replica bootstrap through the Stage-3b provisioner: deno, `loginctl enable-linger`, the `/run/user/<uid>` BindPaths drop-in, identical env pinning (`TAKYON_HOST_ROLE=subuser`, base domain, app DSN, safebox URL).
- Close the replica blockers from §2.5: force the shared storage backend everywhere (kill the `LocalStorageBackend` media fallback path); fan out or read-through-hydrate the action-source cache gated on the DB head revision; make the action replay receipt PG-authoritative; LB-resolve the `ctx.generate` hairpin.
- **Per-replica credentials (security requirement, unchanged from the earlier draft):** enrollment via the Stage-2 pool registry issues each replica its own scoped, revocable safebox token and DB credential; decommission/lease-expiry revokes them. Never bake a shared token into the replica image — N boxes with one token multiplies the known "one token" blast radius by N. Replicas are VPC-private.
- Prove: two replicas serving live product traffic; kill one mid-traffic — the LB drains it with zero failed customer requests beyond in-flight; a replayed action on the surviving replica returns the idempotent success (not a conflict); fresh-business E2E through the LB.

**Cost + robustness ledger for 4b (honest accounting):** the DO token is free; what it creates is not — a managed LB (~$12/mo per node) + each replica droplet (~$12–50/mo by size), so 4b is the first stage with recurring infra spend, opt-in on measured saturation (Q16); 4a costs $0 and delivers most of the capacity win. Robustness after 4b: customer-facing `/api` survives replica loss (health-check eviction + draining, proven by the kill-one test) and deploys become rolling. **Known residuals, accepted deliberately:** the safebox stays a fail-closed singleton until 4c says otherwise; Stripe webhook *ingress* still enters via the operator host (`app.fourmanifold.com`) — mitigated by Stripe's multi-day retries + idempotent dedup (delayed, never lost), with re-registering the delivery URL to the LB as the optional full fix; everything is single-region by design; the operator box stays single (its outage affects the operator, not product customers).

**Stage 4c — Safebox headroom proven or scaled (measurement + conditional build)**
- Load-test the safebox at the Stage-4b target (replicas × workers × concurrent AI calls) with a stated headroom margin (recommend: 3× current peak). If it holds: record the ceiling and the alarm threshold, done.
- If it doesn't hold: ship broker replicas — stateless safebox instances behind a VPC LB (durable state = PG + env file), **per-replica key enrollment and revocation** (the custody control that makes replication acceptable), fail-closed semantics preserved (any replica down ≠ silent degradation; all down = paid calls refuse, as today).
- Prove: sustained target-RPS run with the subuser plane at full Stage-4b capacity, safebox p95 flat; kill a broker replica mid-run — zero dropped provider calls beyond in-flight; revoked replica's credentials verifiably dead.

**Stage 5 — `PlanComposition` + money-shape gate; UC4 ships (~700 LOC + 1 migration; independent of Stages 1–4)**
- This stage touches a different file cluster (app_entitlements.py, the core.py plan path, ai_gateway budget resolver) and shares nothing with the compute-plane stages — it can be sequenced independently or in parallel with them.
- **Monthly-only enforcement + fail-loud cap ride in front** (shippable alone, before the composition work): refuse non-month intervals on new subuser plan writes (idempotent re-pass of frozen non-month rows still passes — the §2.7 gate contract); replace the silent clamp with an explicit refusal carrying the figures; collapse the duplicated cap copies; surface refusals/warnings in the upsert receipt. This closes the live unbounded-COGS exposure (D2+D4) with a mostly-DELETE diff — the year/one_time defenses the earlier draft planned are unnecessary once the intervals don't exist.
- **Legacy removal in the same file cluster:** the dead SQLite plan branches + DDL, both interval alias maps, the phantom `included_action_quota` plumbing, the zero-valued budget shim (full inventory in §2.7). Prod check for existing non-month rows precedes deploy.
- Build `PricedComponent`/`PlanComposition`/`compose_plan` per §2.7; components stored on the plan row (new column or structured metadata — additive migration); `business_upsert_app_plan` gains the composition input and derives economics; raw `price_cents`/`included_ai_budget_microusd` inputs are accepted only as a transitional path that the composer validates against the same margin invariant (one path, no silent second rail), then removed as the last legacy item.
- Money-shape gate at the tool choke point (beside `_refuse_on_autonomous_wake`, so chat and bootstrap are covered): plan writes validate against the business's declared money-shape record; shape changes require the explicit operator-approval affordance first.
- **Shopify slices (the real acceptance, per the operator's ruling — scoped to the plan-fee read + webhook, NOT orders/fulfillment):** `business_connect_shopify` (store the shop domain + Composio connected-account ref; `requires_api=['composio']`); the Admin-GraphQL plan read through the existing Composio safebox broker; the plan-name→fee map; `shopify_util.py` HMAC verifier + safebox-side `verify/process_shopify_app_webhook` (mirroring the Stripe boundary — the secret never reaches the runtime plane); the `/api/webhooks/shopify` route + the two web_server allowlist entries (`_APP_PLANE_EXACT_PATHS`, `_is_public_api_path`); a `_process_shopify_shop_update` dispatch branch updating the component cost_basis. One-time human setup (a sign-on moment): Shopify Partner account + dev store + the app OAuth credentials Composio requires.
- Preserve untouched: `GrandfatheredPlanFrozen`, `FakeBillingRejected`, credit-mint authority, reserve→settle→release, checkout/webhook reconciliation. The composer **reads** `usage_pricing` and the new external-fee record — it must never become a second pricing table.
- Prove: PG-rig tests (derivation determinism; margin refusal with figures; interval normalization; two-scope grandfather mint; shape-gate refusing a sub→credit-pack write on a chat-shaped turn). Fresh-business browser E2E: bootstrap seeds the default plan **via composition**; then the acceptance scenario is literally UC4 — add a fixed-fee component to the live business, watch the new plan_key version mint with the derived price, and complete a Stripe test checkout on it.

**Stage 6 — `RuntimeRail` registry + `BuildStep` pipeline (promoted from §6b — load-bearing for UC2's feature story)**
- One fat `RuntimeRail` object per rail owning: routes (method/pattern/handler/auth-tier), client_methods (the same strings the source scanner derives its regexes from — drift impossible by construction), build_derived flag, dependencies. The web_server app-plane dispatcher becomes a generic loop over the registry — the hand-written per-rail `if/elif` branches (web_server.py:2648-3150) are deleted, and `PRODUCT_RUNTIME_RAILS.endpoints` stops being documentation that lies.
- A `BuildStep` protocol with declared phases (`pre_build`, `post_build`, `pre_digest`, `post_publish`) replacing the two inline build/publish sequences — "must run before the build_id digest or it never reaches R2" becomes a declared property, not a comment.
- Sequencing constraint (not a deferral): the `BuildStep` half touches the publish path the canonicalization spine also owns — land the spine first or coordinate the hunks; the `RuntimeRail` routing/scanner half is independent and can ship any time after Stage 4a.
- Prove: add a trivial demo rail as **one registry literal + one handler** — its route serves on every subuser replica with zero dispatcher edits, the scanner recognizes it in built source without a regex edit, and the worker contract renders it; then delete it just as cheaply. Add a sitemap `BuildStep(post_build)` as the pipeline's proof — it appears content-addressed in the R2 artifact.

## 4p. Proof the plan delivers UC1–4 — the acceptance matrix

Each row is executable at its stage boundary; together they are the definition of "the plan works." Every capability proof runs on **dev first, then prod**; every modularity proof is the operator's own metric — *what does adding the next X cost after the refactor.*

**UC1 — session-owned workers (Stage 2)**
- *Capability:* two concurrent SSH sessions, same operator, each creates a business — cross-claim never occurs (PG test: two exclusive scopes of the same owner cannot steal each other's jobs; live E2E: watch both bootstraps drain only on their own pools). Kill session A mid-job — its scope lease lapses and the job spills instead of stranding.
- *Modularity:* the prefix/env/sidecar affinity triangle is **deleted** (negative diff, verified by grep); "exclusive workers for this session" is demonstrably one constructor argument.

**UC2 — subuser capacity (Stages 4a → 4b → 4c → 6)**
- *Capability 4a:* load test — a stubbed slow `/generate` (held 30s+) no longer moves p95 latency of concurrent reads on the same box (today it stalls them all); repeat off-peak on prod.
- *Capability 4b:* two replicas behind the LB, kill one mid-traffic — zero failed customer requests beyond in-flight; replayed action on the survivor returns the cached success; fresh-business E2E through the LB.
- *Capability 4c:* sustained target-RPS with the plane at full capacity — safebox p95 flat at 3× headroom, or broker replicas pass the same kill-one test.
- *Modularity (Stage 6):* the demo-rail test — a new customer-facing feature is one registry literal, live on all replicas, no dispatcher edit.

**UC3 — dev environment (Stages 3 + 3b)**
- *Capability:* from a clean slate, `takyon env create dev` → `TAKYON_ENV=dev ./takyon dashboard` → open `localhost:9119` in a browser → create a fresh business → real Stripe **test** checkout + real generation on capped keys — with a **DB assertion that zero prod-plane rows changed** during the run, and the prod-literal boot assertion armed throughout.
- *Modularity:* `takyon env create dev2` succeeds with **zero human steps** (post token-deposit); hermetic profile runs the full PG suite green on macOS and in CI.

**UC4 — compositional pricing (Stage 5) — acceptance is a REAL Shopify integration (operator ruling, 2026-07-02)**
- *The cost shape under test (confirmed by investigation):* Shopify's **fixed monthly per-store platform fee** — the one Shopify money surface that belongs in monthly subscription composition. Per-order/fulfillment pass-through (`app_orders.py`, catalog margin gates, Printful reconcilers) is a *different money shape* owned by the archetypes project and is the explicit **stop line**: not built for UC4. Per-transaction % is a third shape with no home in either plan — flagged, not built.
- *Capability leg 1 — compose on a cost READ from a real store:* a Shopify Partner **dev store** ($0, real Admin API) is connected via the **existing Composio broker** (Shopify is a Composio toolkit; token custody/refresh in Composio, calls through the already-brokered `/v1/providers/composio/forward` — zero new runtime credential, same pattern as X/Reddit/Meta). `business_connect_shopify` (scoped to plan-fee read) fetches `shop { plan { displayName } }` via Admin GraphQL through the safebox; a name→fee map (basic→$39, shopify→$105, advanced→$399, `partnerDevelopment`→configured test fee) sets the `shopify_store` component's `cost_basis` — **fetched, never typed**. `compose_plan` recomputes the $19/mo plan under the margin policy → mints `plan_key-v2` (~$29) with the derivation receipt `base + shopify_store ($X read from store) → $29` → a new signup checks out at the derived price (Stripe test on dev) → the existing subscriber's row is byte-identical (grandfather intact).
- *Capability leg 2 — webhook-driven recompose (the part only Shopify exercises):* a `shop/update` plan-change webhook hits the new `/api/webhooks/shopify` on the subuser plane → HMAC verified **safebox-side** (`X-Shopify-Hmac-Sha256`, base64 HMAC over raw body — a new `shopify_util` verifier; Stripe's `t=/v1=` format is not reusable) → rides the *existing* provider-parameterized dedup rail (`record_webhook_and_process(provider='shopify')` — the `webhook_events(provider, provider_event_id)` table needs **no migration**) → `_process_shopify_shop_update` updates the component's cost_basis → recompose mints `plan_key-v3` automatically, receipted. Upstream cost changed → subscriber price recomposed, end to end, push-driven.
- *Guard proofs (each a named test):* a chat-turn freehand price write → refused with figures; a sub→credit-pack shape switch on a chat turn → refused (the Roomier hole, closed on every path); a non-month interval on a new plan → refused; a budget above the margin invariant → refused loudly (silent clamp is structurally impossible).

If any row cannot pass at its stage boundary, the stage is not done — there is no "ship now, prove later" path in this plan.

## 5. Verification & deploy protocol (every stage)

- Money/jobs/claims proof = local Mac PG rig (`TAKYON_TEST_PG_DSN`) — CI has no Postgres today (Stage-0 recommendation fixes this). A green GitHub run does **not** prove prod updated: the VPS steps are SSH-reachability-gated and skip silently. Each stage deploys via the manual rsync recipe + `systemctl` verify on both hosts, **and** commits+pushes `main` in the outer repo.
- Migrations are applied on the VPS out-of-band of rsync (the deploy heredoc imports `plugins.takyon` and runs `run_migrations`) — schema before code, per the Stage-2 protocol.
- Fresh-business browser E2E is the acceptance gate for every product-touching stage (1, 2, 4), on both operator and subuser hosts; existing-business checks are exploration only.
- Shared-tree discipline: isolate each stage's hunks in a worktree off `origin/main`; never `git stash` in this repo; keep re-export shims until every caller is proven moved; one stage per deploy, never batched.
- **Rollback protocol (every stage):** rollback = revert the stage's commit, rsync, restart — schema stays (additive/nullable; old code ignores it; no down-migrations exist by design; never rename or repurpose). Rehearse the revert once on the dev environment after Stage 3 lands so it is a practiced motion. Known-acceptable rollback artifacts: jobs enqueued during a Stage-2 window lose the new reservation (claimable by anyone, none stranded); plan-key versions minted in Stage 5 stay minted (grandfather invariant — composed plans remain valid rows under reverted code).

## 6. Deferred readability track (after UC1-3; each its own shippable change)

Explicitly **not** on the critical path — these buy readability, not use cases:
1. **`primitives.py` lift** — move `_PGConn/_now/_json_dumps/_hash_token/_require_app_database_plane_for_pg/_atomic_write_text/_resolve_sqlite_app_user` out of core.py, severing the 52 `app_*`→core lazy-import sites. Caveat from review: these symbols have consumers beyond the app trio (`_now` in ~14 files), so shims persist long.
2. **`LedgerRail` protocol** over billing/usage/credits — the strongest latent seam, pure move, zero UC payoff.
3. **Scaffold literals → template files** (~3,500 lines of subuser-app strings, core.py:2769-6336) and `PRODUCT_RUNTIME_RAILS` to its own module.
4. **Fold file-based cron into the PG wake queue** (unblocks true multi-node wake dispatch).
5. **Server-plane merge** (web_server vs the production-dead `build_runtime_app`, tui_gateway duplication) — a separate project; this plan reuses only the connection seam.

## 6b. Modularity audit — what else needs seams (ranked)

A parallel audit graded every remaining subsystem by the operator's own test: *"what does adding the next X cost today?"* Four hotspots scored **D** (copy-paste smear, no seam), none covered by UC1–4, the deferred track, or the other plan files. Ranked:

### 1. Distribution channels → `ChannelPublisher` + `CHANNEL_REGISTRY` (grade D, priority 2)

**Add-LinkedIn test today: ~9 files, ~12 edit sites, across TWO deploy hosts.** X and Reddit are structurally identical (~490-line and ~265-line worker handlers duplicating the reserve→publish→commit/release skeleton verbatim, [worker.py:1758](hermes-agent-main/plugins/takyon/worker.py:1758)/:2247), yet share zero code. Channel knowledge is smeared across: **four parallel per-action dicts** in core.py:28845-28886 (credit cost/env/bucket/audience), the `_API_ENV_ALIASES` rows repeating the same Composio tuple per name-spelling, hand-written name predicates (`_is_x_provider_name`), per-channel composio resolver/wrapper clones, per-channel receipt-writer clones that hardcode publication paths **also** declared in the skill frontmatter (two sources of truth, hand-synced), and a per-channel entry in the safebox authority host's audience map — a *separate deploy target*. The X-acquisition-link prod bug is the failure mode: publish behavior is inline handler code, so no fix is inherited by the next channel.
**Seam:** a `ChannelPublisher` protocol (slug, aliases, credit_action, toolkit_slug, publication_root, `publish/test_stub/extract_ref`) + one registry. Worker gets ONE generic `channel_publish_outreach_handler`; the four credit dicts, alias rows, predicates, and receipt writers derive from the descriptor. Adding a channel = one descriptor + one provider adapter + a SKILL.md. Pure extraction on unchanged money rails; feeds directly into Stage 1's `HandlerRegistry`.

### 2. Creative/media providers → `CreativeProviderSpec` registry (grade D, priority 2)

**Add-Ideogram test today: ~7 lockstep surfaces in 4 incompatible vocabularies** — and the Takyon plugin **bypasses the clean Hermes provider registries that already exist** (`agent/image_gen_registry.py`, `video_gen_registry.py` — zero imports from plugins/takyon), while web-search proves the registry pattern works (one entry + one billing row). Worse, there are *four* parallel provider-integration styles: creative_gateway hardcoding, the static-ad skill's own private `ImageBackend` ABC, ai_provider.py's hand-written triplets, and the Hermes registries nobody uses. **Live truthfulness bug found:** the logo receipt stamps `gemini-2.5-flash-image` (core.py:25967) while the render actually uses `gemini-3.1-flash-image` (creative_gateway.py:39) — prices coincidentally match today, so it's silent, but the receipt is factually wrong and will mis-cost on the next price change.
**Seam — with the money gate made structural (operator's caveat: the Hermes registries are NOT credit-gated, and that must never change here):** the Hermes registries are the *operator agent's* tool lane; the business lane hardcodes providers today precisely because its calls must run inside the Takyon money envelope (reserve → safebox authority route → commit/release). The seam therefore reuses the Hermes *shape* (adapter ABC, catalog dispatch — the same split web search already proves safe: registry dispatches, `web_spend.py` injects the Takyon meter into the seam), **never the Hermes call path**. Concretely: one `CreativeProviderSpec` per (capability, provider) binding canonical id + model + pricing_key + key_aliases + safebox route + **`money_gate`** (a required field: `credit_action` for fixed-price creative work, or the usage-rail meter for consumption-priced calls). The spec exposes no raw `generate()` — the *only* invocation path in the business lane is a shared `gated_creative_call(spec, …)` envelope that reserves against `spec.money_gate`, calls `spec.safebox_route` (key resolved server-side), and commits/releases. A spec without a money gate cannot register — fail-closed at the type level. That is strictly *stronger* than today, where every new provider integration must hand-reimplement the reserve (a forgettable step); the registry turns CLAUDE.md's "no ungated paid capability" prose rule into a structural invariant. Receipt-model and priced-model can no longer diverge; `_API_ENV_ALIASES` (and its good denylist auto-derivation) build FROM the spec. `usage_pricing` stays the only price SSOT (the spec holds the lookup key, never a price). Credit action costs stay action-keyed (correct as-is).

### 3. Product rails + build steps → `RuntimeRail` fat registry + `BuildStep` pipeline (grade D — **PROMOTED to Stage 6**, no longer deferred)

**Add-a-rail test today: 6–8 disjoint surfaces that nothing forces to agree.** `PRODUCT_RUNTIME_RAILS.endpoints` is documentation-only — the *real* routing is a hand-written per-rail if/elif dispatcher in web_server.py (:2648-3150) that never reads the registry; rail declaration is derived from built source by a Python **regex scanner whose patterns must textually match the JS kit's method names** (an unenforced cross-file naming contract — drift = silent `rail_unavailable`). Build/publish is a linear inline sequence in two functions where step ordering (e.g. "inject before the build_id digest or it never reaches R2") exists only as comments. The actions-rail plan's own §2 anchor map *documents* this 7-place fan-out as a checklist — institutionalizing the duplication.
**Seam:** (a) a fat `RuntimeRail` object owning routes + client_methods + build_derived + dependencies, from which the serve dispatcher and scanner regexes are *derived*; (b) a `BuildStep` protocol with declared phases (`post_build`, `pre_digest`, …) replacing the inline sequences. **Sequence after the canonicalization spine lands** (it owns the artifact/pointer plane these sit on).

### 4. Bootstrap recipe + cockpit panels (grade D, priority 3)

**Add-a-bootstrap-step test today: hand-insert prose into a ~190-line `list[str]` monolith** ([cli.py:1935-2126](hermes-agent-main/plugins/takyon/cli.py:1935)) with per-step caps embedded in the prose and seeds scattered wherever their author put them (plan seed inline in the surface-contract handler; landing in its own module; favicon in core.py) — nothing ties prose steps to implemented seeds, so they drift silently. **Add-a-panel test: edit a ~600-line hand-assembled snapshot function** (tui_gateway/server.py:6286) *plus* its forked REST twin (web_server.py:3972) *plus* view-helper forks that have **already observably drifted** ("Published post" vs "Local published outreach"; the web twin dropped the `distribution/voice/` branch; the job-kind→label map exists in triplicate).
**Seam:** `BOOTSTRAP_SEED_STEPS` (step = key/title/tool/caps/policy/body; the instruction builder becomes a renderer; a test pins step.tool ↔ registered tools) + a `HOME_PANELS` registry both server planes consume + collapsing the forked view helpers into one shared module (the concrete first slice of the deferred server-plane merge — its drift is a correctness smell, not aesthetics). The seed recipe waits for UC4's plan-seed to settle so it lands once.

**Explicitly re-graded as fine or already covered:** the subuser interval axis (C — dies by construction with monthly-only); money ledgers, StorageBackend, docker broker, wakes (A–B, the role models); the publish/serve artifact plane (owned by the canonicalization spine, awaiting go); `_apply_operation`, AIAgent/run_conversation (non-goals, unchanged).

## 7. Non-goals (hard)

- No refactor of the money ledgers, `SECURITY DEFINER` calls, or the safebox authority gate — verified cleanest code in the repo; touching them risks real dollars for zero UC gain.
- No `_apply_operation` OperationRegistry rewrite (~30 live write-path branches; dropped on judge review), no `run_conversation`/`AIAgent` decomposition (cache-sensitive, orthogonal — the `auxiliary_client` `_RUNTIME_MAIN_*` process-global is a real >1-turn-per-process bug but is a density optimization, not node-count scaling).
- No second affinity mechanism, no second test-mode path, no deterministic business-action routing, no new config store (`RuntimeContext` *reads* the canonical sources; it does not duplicate them).
- Not converting all 213 env reads — only the load-bearing ones (§ Stage 3).
- UC4 does not touch the reserve→settle→release rails, credit-mint authority, or checkout/webhook reconciliation, and does not migrate existing subscribers to new pricing (that remains the deferred OpenMeter-owned billing migration). The composer reads `usage_pricing` — it never becomes a second pricing table.

## 8. Top risks

- **R1 — money × lease timing** (Stage 2): reclaim-before-release double-reserve window. Mitigated by the ordering invariant + dedicated PG test; escape hatch keeps `run_one` fused if needed.
- **R2 — host-role collapse mis-resolves a plane** (Stage 3): the four runtime truth tables genuinely disagree. Mitigated by the Stage-0 exhaustive alias test written *before* any deletion.
- **R3 — missed process-global cache** (Stage 3): a dev instance silently reads a prod cached key or DSN — a security regression, and a data-corruption one (a "dev" turn writing prod rows). Mitigated by the cache audit + prod-literal boot assertion.
- **R4 — isolated-turn subprocess bypasses the context** (Stage 3): dashboard turns spawn with inherited env; if the context isn't threaded through the spawn payload, a dev dashboard turn silently lands on prod's DB/safebox/budgets (and hermetic's stub never engages). Explicit Stage-3 item.
- **R5 — concurrent sessions on the shared tree + live schema changes**: two sessions deploying different stages against one prod PG. Mitigated by additive-only schema, one-stage-per-deploy, and serializing against the active MVP bootstrap goal (open question Q1).

## 9. Open questions for the operator

1. **Sequencing vs the ACTIVE MVP bootstrap goal** — it edits `worker.py`/`jobs.py`/`core.py` on the same tree and restarts services. Serialize this migration after it, pause it, or interleave carefully? (Recommend: serialize; Stages 0-1 are safe to start now since they're test-only/behavior-preserving.)
2. **Live-PG schema sign-off** — comfortable adding `claim_scopes`, `worker_nodes`, and nullable `jobs.claimed_scope_id` via the run-every-boot idempotent runner, with the additive-only/mixed-version protocol above (no down-migrations exist)?
3. **CI Postgres** — invest in a PG service job in CI at Stage 0 (recommended), or commit to running the local Mac PG rig manually for every stage?
4. **Isolated-turn lane scope** — accept the minimal treatment (context threaded through spawn; ownership attributed to the session's scope) and defer remodeling it onto `WorkerPool`? (Recommend: yes.)
5. **UC1 default posture** — scopes are opt-in (SSH shells open exclusive scopes; dashboard/VPS workers keep scope-less claiming). Should the dashboard/VPS pools eventually get non-exclusive scopes too, for observability? (Recommend: later, via the same mechanism.)
6. **Cron fold timing** — keep file-based cron pinned to the dispatcher node through UC2 and fold into the PG queue in the deferred track? (Recommend: yes.)
7. **Dev placement** — start local on the Mac (dashboard + loopback PG with prod role names + a local dev-safebox process; fastest loop, no systemd/Caddy), adding a dedicated dev droplet later for the systemd/Caddy/R2-serving paths — or go straight to a dev droplet mirroring the operator+subuser pair? (Recommend: local first; droplet when the serving paths need testing.)
8. ~~Provider key policy for dev~~ — **RESOLVED (operator, 2026-07-02): social posting stays on the same accounts for dev.** No dedicated dev social accounts; the last recurring human step disappears — dev provisioning is fully autonomous after the one-time DO/Auth0-management token deposits. Consequence accepted: a dev business flipped to live mode posts as the real identity (dev businesses default to test mode, so this is an explicit act). Gemini/FAL/Tavily: shared keys under per-environment budget caps.
9. **Dev data + domains** — empty schema via the existing migrations, or a scrubbed periodic prod snapshot? And does dev get a real public twin (`*.dev.fourmanifold.com`: DNS + Auth0 callbacks + an R2 worker route twin), or local-only serving to start? (Recommend: empty schema + migrations, local serving first; snapshot-restore and the public twin as later utilities.)
10. **Provisioner scope (Stage 3b)** — in scope for this migration (recommended: the manual ritual is exactly the complaint), or a fast-follow after UC1–3? And which platform admin tokens (DO, Cloudflare, Auth0 management, Stripe) already live in the safebox realm vs need to be granted once?
11. **Margin policy ownership (UC4)** — who sets `margin_floor` and the price-point rounding rule: one platform default, per-archetype defaults (subscription SaaS vs COGS pass-through), or per-business operator override? And what is the floor's starting value? (Recommend: platform default + per-archetype override, operator-changeable; the current implicit floor is 0%.)
12. **Money-shape gate coupling (UC4)** — should the shape gate key on the business-archetypes registry from takyon-business-archetypes-plan.md (couples two plan-only efforts; cleanest single source of shape truth), or start as a minimal per-business `money_shape` record UC4 owns and the archetype registry later subsumes? (Recommend: the minimal record first — UC4 must not block on the archetypes plan.)
13. **Ship the monthly-only slice now?** — non-month refusal on new plan writes + fail-loud clamp + duplicate-cap collapse is a standalone, mostly-DELETE change that closes the live unbounded-COGS exposure (a task chip is ready). Ship it ahead of the plan? (Recommend: yes.)
14. **Monthly-only grandfather contract** — confirm: new writes refuse non-month; existing frozen non-month rows keep serving (idempotent re-pass allowed) until their subscribers lapse; read-side year handling (MRR, OpenMeter cadence) is deleted only after that. Requires one prod check first: are there any `billing_interval != 'month'` rows with active entitlements?
15. **Modularity backlog promotion (§6b)** — the channel registry and creative-provider spec (both priority 2) are pure extractions on unchanged money rails. Promote them into this plan as stages (recommend: schedule the channel registry before the next channel add, the provider spec before the next provider add — i.e., gate future adds on the seam), or leave them as deferred-track items?
16. **UC2 rollout (corrected scope)** — ship Stage 4a soon regardless of the rest of the plan (the 180s inline provider call on the single event loop is a live incident waiting; a chip is ready), and hold Stage 4b until measured saturation? For 4b: DO load balancer in the VPC vs round-robin A records? (Recommend: 4a soon; DO LB — health checks and connection draining matter more than the small cost.)

---

## Appendix A — Port contracts (grounded sketches)

Adapter-compatible with the existing code: `conn` stays caller-owned (jobs.py is deliberately a connection-borrowing leaf), the PG `FOR UPDATE SKIP LOCKED` engine, the budget reserve→settle→release contract (jobs.py:520-559), and the idempotent `on conflict do nothing` enqueue (jobs.py:232) are untouched. The current `jobs` table has **no** ownership columns (`db/migrations/0010_jobs_and_wakes.sql:90-108`); affinity is payload-only today.

### A.1 `Environment` (worker-facing slice of `RuntimeContext`)

```python
@dataclass(frozen=True, slots=True)
class Environment:
    host_role: HostRole                 # was TAKYON_HOST_ROLE (jobs.py:103, runtime_app.py:67)
    profile: Literal["prod","dev","hermetic"]
    database_url: str                   # resolved once (was resolve_database_url at worker.py:2835)
    is_worker_process: bool             # REPLACES the os.environ["TAKYON_WORKER_PROCESS"]="1" write (worker.py:2834)
    worker_concurrency: int = 2         # was TAKYON_WORKER_CONCURRENCY (worker.py:2843)
    poll_seconds: float = 15.0          # was TAKYON_WORKER_POLL_SECONDS (worker.py:2841)
    stale_seconds: int = 900            # was TAKYON_WORKER_STALE_SECONDS (worker.py:2736)
    min_queue_age_seconds: float = 0.0  # was TAKYON_WORKER_MIN_QUEUE_AGE_SECONDS (jobs.py:283)

    @classmethod
    def load(cls, *, profile="prod", **overrides) -> "Environment": ...  # the ONLY place env is read

_ACTIVE: ContextVar[Environment]        # bound once per process/thread start
def active_env() -> Environment: ...
```

Deep leaves (e.g. `jobs._refresh_job_lifecycle_session`) read `active_env()` instead of `os.getenv` — no threading through 50 signatures. `is_worker_process` replaces the cross-module env-write control flow at worker.py:2834 (this lands in Stage 3, superseding the "keep the env write for now" note in Stage 1). A hermetic instance is `Environment.load(profile="hermetic", ...overrides)` — no env mutation; `dev` loads from its own env file/`config.yaml` exactly the way prod does, differing only in what the values point at.

### A.2 `JobQueue`

Protocol over the existing functions verbatim: `enqueue` jobs.py:212, `claim` (claim_one) jobs.py:261, `heartbeat` :374, `complete` :390, `block` :406, `fail` :424, `requeue_stale` :493, `claim_and_run` (run_one) :520. Two signature changes only: `scope: ClaimScope | None` threaded through enqueue/claim, and the loose `owner_user_id` kwarg folds into `ClaimScope.owner_user_id` — one targeting object instead of parallel kwargs.

### A.3 `ClaimScope` + reservation columns

```python
FallbackPolicy = Literal["strict", "after_lease", "any"]

@dataclass(frozen=True, slots=True)
class ClaimScope:
    owner_user_id: str | None = None   # absorbs claim_one's owner filter (jobs.py:284,290-296)
    pool_id: str | None = None         # target WorkerPool.pool_id (was preferred_worker_id_prefix)
    fallback: FallbackPolicy = "any"
    lease_seconds: float = 0.0         # was preferred_worker_claim_seconds
```

```sql
-- additive, idempotent-by-construction (db/runner.py convention); 00NN numbering per current head
alter table jobs add column if not exists reserved_pool_id       text;
alter table jobs add column if not exists reservation_policy     text not null default 'any'
    check (reservation_policy in ('strict','after_lease','any'));
alter table jobs add column if not exists reservation_expires_at timestamptz;
create index if not exists jobs_reserved_pool_idx on jobs (reserved_pool_id) where status = 'queued';
```

`claim_one` predicate (replaces jobs.py:85-93 + :297-320):

```sql
and (
     j.reserved_pool_id is null                                   -- 'any' / unreserved
  or j.reserved_pool_id = %(worker_pool_id)s                      -- this pool owns the reservation
  or (j.reservation_policy = 'after_lease'
      and j.reservation_expires_at <= now())                      -- lease expired → spillable
  or (j.reservation_policy = 'strict'
      and not exists (select 1 from worker_pools p               -- owning scope dead → spill, don't strand
                      where p.pool_id = j.reserved_pool_id
                        and p.lease_expires_at > now()))
)
```

`reservation_expires_at` stamps at enqueue and renews off `updated_at` on requeue — preserving the f899da41 renew-on-retry behavior by construction.

### A.4 `WorkerPool` — four constructors == the four compute paths

```python
class WorkerPoolFactory:
    @staticmethod
    def local_threads(env, queue, handlers) -> WorkerPool: ...  # == run_worker_loop (worker.py:2808)
    @staticmethod
    def embedded(env, queue, handlers) -> WorkerPool: ...       # == dashboard thread (web_server.py:1956)
    @staticmethod
    def inline(env, queue, handlers) -> WorkerPool: ...         # == shell run_inline (cli.py:2168), size=1, dispatch=False
    @staticmethod
    def remote_node(env, queue, handlers, node) -> WorkerPool: ...  # optional future remote worker node (NOT UC2 — see §2.5); registry lifecycle
```

Lifecycle: `start / drain (finish in-flight, claim nothing new) / stop`; `drain_once` wraps `drain_tick` (worker.py:2700). `handlers` is constructor-injected; the module `HANDLERS` dict (worker.py:2670) becomes merely the default map the composition root passes in — no second registry.

### A.5 Pool/node registry (Stage 2, extended Stage 4)

```sql
create table if not exists worker_pools (
    pool_id          text primary key,
    owner_user_id    text,
    session_key      text,             -- from gateway.session_context when present
    hostname         text not null,
    exclusive        boolean not null default false,
    concurrency      int not null default 1,
    status           text not null default 'active'
        check (status in ('joining','active','draining','decommissioned','lost')),
    capabilities     jsonb not null default '{}'::jsonb,   -- Stage 4: docker / vpc_safebox / deno_systemd / kinds
    lease_expires_at timestamptz not null,
    registered_at    timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);
```

`register` on `WorkerPool.start`; `heartbeat` per drain tick; `begin_drain`/`decommission` for scale-in; `reap_lost` flips lapsed leases to `lost`. Node liveness and job leases are orthogonal rails — `requeue_stale` independently recovers a lost node's in-flight jobs.

### A.6 Call-site rewiring table

| Call site | Today | Becomes |
|---|---|---|
| `worker.py:2808` `run_worker_loop` | env-int threads, invented worker_id, module `HANDLERS` | `WorkerPoolFactory.local_threads(env, queue, HANDLERS).start(...)` |
| `worker.py:2700` `drain_tick` | free function, `handlers=HANDLERS` default | `WorkerPool.drain_once(...)`, handlers injected |
| `worker.py:2834` env write `TAKYON_WORKER_PROCESS=1` | cross-module control flow | deleted (Stage 3); `Environment(is_worker_process=True)` bound at pool start |
| `web_server.py:1956` dashboard thread | open-coded `run_worker_loop` thread in module globals | `WorkerPoolFactory.embedded(...).start()` |
| `cli.py:2168` `_run_pg_ceo_wake_once` | inline enqueue + bounded `run_one` loop | `queue.enqueue(...)` + `WorkerPoolFactory.inline(...).drain_once(kinds=["ceo_wake"], dispatch=False)` |
| `cli.py:2217` `_bootstrap_preferred_worker_claim_payload` | env re-read → payload hint | deleted; shell passes `ClaimScope(pool_id=…, fallback="strict")` on enqueue |
| `jobs.py:85-93, 297-320` affinity SQL | payload-regex grace window | indexed reservation predicate (A.3) |
| `jobs.py:102-107, 283` leaf env reads | `os.getenv` in queue leaf | `active_env()` fields |
| `core.py:20039` `TakyonStore.commit` `job.enqueue` branch | `worker_jobs.enqueue(...)` mirror | `queue.enqueue(..., scope=…)` — same dual-write, one enqueue API |
| `core.py:31572` `_run_operator_task_on_worker` | commit + poll over two row kinds | unchanged orchestration; its enqueue carries the operator's local `ClaimScope` (UC1) |
| `scripts/takyon-operator-prod.sh:80,367-369,719` | bash prefix + sidecar file + pgrep + env export | deleted; the shell opens/heartbeats a `worker_pools` row |
