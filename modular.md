# Adding features modularly

How to extend Takyon without touching load-bearing code. Every seam below is a **registry
or descriptor**: you add one entry (and, where it needs a real side effect, one keyed
handler or tool), and the generic machinery — dispatchers, auth tiers, scanners, gates,
receipts — derives the rest. Line numbers are anchors, not contracts; the code is truth.

**Golden rule (from AGENTS.md):** find the canonical seam and add to it. Do **not** add a
second dispatcher, a parallel path, a per-feature if/then, or a deterministic router. If you
catch yourself copying an existing branch, you're at the wrong altitude — add a registry
entry instead.

---

## 1. New subuser API rail (a product-app backend capability)

**Seam:** `RUNTIME_RAILS` registry + `RuntimeRail`/`RailRoute` — `plugins/takyon/core.py:642` (class), `:775` (registry).

A "rail" is a backend capability a product app can select (auth/session, checkout, records,
media, generate, search, …). The subuser HTTP dispatcher, the three auth tiers, and the
source-scanner all **derive** from the registry — proven by a demo rail added and removed
with zero dispatcher edits.

**Steps:**
1. Add a `RuntimeRail(name=…, routes=(RailRoute(method, pattern, handler_key, auth_tier), …), client_methods=…, metadata=…)` to `RUNTIME_RAILS`.
   - `auth_tier` is one of the `APP_AUTH_*` constants (`PUBLIC_OPTIONAL` / `PUBLIC_NO_TOKEN` / `SESSION_REQUIRED`). `__post_init__` refuses an unknown tier.
   - `pattern` is a tuple of path parts; `<name>` parts bind as path params.
2. Add the keyed handler body under `_APP_{GET,POST,DELETE}_HANDLERS[handler_key]` in `takyon_cli/web_server.py` — the only imperative code you write.
3. `client_methods` auto-produce the source-scanner regexes (no scanner edit).
4. Select the rail per business via `runtime_features` on the surface contract — it does **not** turn on for every app automatically.

**Security note (priority one):** the role allowlist (`_http_path_allowed_for_host_role` /
`_APP_PLANE_*`) is the boundary — never widen it to add a rail. A new rail lives *inside* the
existing subuser allowlist; operator-plane paths stay 404 on the subuser box.

---

## 2. New outreach channel (X, Reddit, … the next one)

**Seam:** `CHANNEL_REGISTRY` + `ChannelPublisher` — `plugins/takyon/channel_registry.py:82` (class), `:711` (registry).

X and Reddit collapsed from ~750 duplicated lines to 3-line dispatchers. Everything a channel
needs is fields on the descriptor: `slug`, `credit_action`, `toolkit_slug`, `budget_bucket`,
`credit_cost_default`/`credit_cost_env`, `publication_root`, `env_alias_names`, `job_kind`,
plus five callables (`publish`, `reservation_metadata`, `commit_metadata`,
`partial_failed_metadata`, `release_metadata`).

**Steps:** add one `ChannelPublisher(...)` entry to `CHANNEL_REGISTRY`. Credits, env-alias
resolution (safebox), publication roots, and the reserve→publish→commit/release receipt
envelope all derive. The generic `worker.channel_publish_outreach_handler` runs it.

---

## 3. New paid creative capability (ad image/video, logo, UGC, …)

**Seam:** `CreativeProviderSpec` — `plugins/takyon/creative_provider_registry.py:127`.

**The money gate is a type invariant, not a review rule:** `money_gate` is a REQUIRED field;
a spec without it raises `MissingMoneyGate` in `__post_init__` — the object is
*unconstructable* ungated. "No ungated paid capability" is enforced by the compiler, not a
checklist.

**Steps:** construct a `CreativeProviderSpec(canonical_id, capability, provider, model,
pricing_key, key_aliases, safebox_route, money_gate=…)`.
- `pricing_key` resolves exact cost from `agent/usage_pricing.py` (unpriced ⇒ refused).
- `key_aliases` + `safebox_route` resolve the provider key **only** via the safebox authority route — never `os.environ` in a business tool.
- Register the alias in `core._API_ENV_ALIASES` and declare `requires_api=[...]` on the tool.

---

## 4. New pricing input (a cost that should flow into a plan's price)

**Seam:** `PricedComponent` / `CostBasis` → `compose_plan` → `upsert_plan_from_composition` — `plugins/takyon/plan_composition.py`.

Prices are **derived, never typed**. A `CostBasis` is one of three kinds
(`plan_composition.py:59`): `metered` (an AI allowance priced through the `usage_pricing`
SSOT), `fixed` (an external recurring/per-seat fee as-is — this is the Shopify per-store fee
shape), or `per_unit` (a quota). `compose_plan` sums components, enforces the **margin
invariant** (fail-loud), and `upsert_plan_from_composition` mints the next `plan_key` version
— grandfathering (old subscribers untouched) and monthly-only enforcement come free.

**Steps:** produce a `CostBasis`/`PricedComponent` for the new cost and feed it to
`compose_plan`. Don't add a second pricing table; extend `agent/usage_pricing.py` if a new
model/provider needs a price.

---

## 5. The money-shape gate (whenever you touch plans/credits)

**Seam:** `money_shape.assert_write_matches_shape` — `plugins/takyon/money_shape.py:172`.

Every path that mints a subuser-facing plan/credit price passes the gate: the write's shape
must match the business's **declared** shape (`subscription` / `credit_packs` /
`cogs_passthrough`), on *every* task kind (chat, bootstrap, wake). A mismatch is refused
(`MoneyShapeViolation`). Changing the shape itself requires an approved, single-consume
operator approval — never a silent flip. This is already wired at the plan choke point
(`upsert_plan_policy`); a new pricing path just needs to route through that choke point.

---

## 6. New environment twin (dev, dev2, staging, …)

**Seam:** a manifest block + the `EnvironmentProvisioner` — `environments/*.yaml` + `plugins/takyon/env_provisioner.py`; the runtime seam is `resolve_database_url` (`runtime_app.py:421`) reading `DEV_DATABASE_PLANE_ENV` (`environment.py:123`).

A twin is its **own** Supabase project (never dev tables in prod), provisioned prod-shaped via
`topology.sql` + migrations. `TAKYON_ENV=<name>` resolves that env's `*_DATABASE_URL` aliases
and **fails closed** if one is missing — plus a prod-literal guard refuses any non-prod target
that resolves a prod host.

**Steps:** copy `environments/dev.yaml` to a new manifest, deposit its aliases in that env's
safebox, `takyon env create <name>`. `status` / `destroy` / `restart` (zero-loss rolling) all
work off the same provisioner.

---

## 7. New business method (a CEO-choosable operating mode)

**Seam:** a Takyon skill + optional `business_*` tool. Full authoring path in
`AGENTS.md` → "When the operator asks to add a normal new Takyon feature or skill".

**Steps (parsimony path):**
1. `hermes-agent-main/skills/takyon/<name>/SKILL.md` from `SKILL-TEMPLATE.md` (keep the section order; frontmatter = valid YAML with `metadata.hermes.*` + `metadata.takyon.*`).
2. Add a `business_*` tool **only** if the method needs a new guarded side effect / provider call / receipt / budget gate (register in `core.py` + `plugin.yaml`).
3. Relaunch `./takyon` so `skills_sync` copies it into `$TAKYON_HOME/skills/`, then **verify** it appears in the skills index (an already-synced skill needs `takyon skills reset <name> --restore`).
4. Do **not** add a deterministic router — the CEO discovers the method through the skills index, tool schemas, and gate errors.

---

## What is *not* a descriptor (on purpose)

The **product app itself** — its site, copy, layout, information architecture — is generated
per business and stored per business (surface contract → `product/surface.md`), **not** a
registry entry. The runtime owns backend rails only; it must never hardcode a product's look.
So "add a new app" = a bootstrap run, not a code change. What *is* descriptor-easy is an app's
**backend capabilities** — the rails it selects via `runtime_features` (see §1).

---

## Where the boundary sits (deterministic code is allowed only here)

Hardcode only durable safety/control rails: business isolation, path containment, idempotency,
credential gates, budget caps, audit receipts, pause/kill/wake controls, auth/session/webhook
protocol, and UI rendering mechanics. Strategy, prioritization, product direction, outreach
motion, and pricing *judgment* live in per-business state + skills, never in a fixed workflow.
