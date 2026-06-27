# /goal spec - Subuser authority split: no mixed sessions anywhere

**You are Codex.** This is the goal. Do not treat the current `SET ROLE takyon_app`
model as acceptable just because the happy-path E2E passed. The goal is to remove
the mixed operator/app database authority pattern, not to paper over it with more
grants or broader bypasses.

This is scoped to product sub-user security. Operator security is in scope only
where a product sub-user, product-host bug, or compromised sub-user plane could
reach or reuse operator authority.

## Goal Condition

TRUE GREEN = operator/dashboard traffic and product-app/sub-user traffic use
physically separate database authority end to end:

- separate DSNs
- separate Postgres login roles
- separate connection pools
- separate service/process surfaces where production routing permits it
- no reversible `SET ROLE` bridge from app/customer code back to operator authority
- no app/customer role can turn on an RLS bypass by setting a GUC
- all money/provider side effects still go through Safebox-owned authority gates
- no product sub-user path can use OpenMeter, env vending, shared tokens, SSRF, or
  VPC origin trust to grant access, spend money, fetch secrets, or reach operator

Not done until the code, migrations, deployed service config, and live probes prove
these invariants on the operator VPS, sub-user VPS, and Safebox.

## Simplicity Rule

The fix should delete mixing, not add more cleverness.

- one app DB login for product sub-user traffic
- one operator DB login for operator/dashboard traffic
- one Safebox authority login for money/secrets/provider actions
- one migration login for deploy/migrations
- no generic production `DATABASE_URL`
- no runtime request path that changes plane by `SET ROLE`
- no "mirror" system that can become authority by flag or metadata
- no legacy auth/billing/provider branch left reachable "just in case"

## Why This Goal Exists

Live failure observed on the operator dashboard:

```text
dashboard websocket -> prompt.submit
  -> _takyon_require_business_access(...)
  -> _takyon_businesses_for_session(...)
  -> active_store.read(query="list_businesses")
  -> SELECT * FROM businesses
  -> permission denied for table businesses
```

That means an operator/dashboard request hit Postgres while the effective role did
not have operator privileges. The dangerous shape is:

```text
session_user = takyon_runtime
current_user = takyon_app
```

Fresh runtime DB connections on the operator host resolve correctly as
`session_user = current_user = takyon_runtime`, so the stored runtime DSN is not
globally wrong. The defect is the architecture: the code can temporarily demote an
operator-capable DB session into `takyon_app` for app/customer work, and that state
can leak, be reused, or be reachable in places where it should not exist.

This was exposed by the least-privilege cutover work. The security direction was
right, but the mixed-session implementation is too subtle and must be replaced.

## Subuser Threat Boundary

A product sub-user may:

- sign in to their product app
- read and mutate only their own product-app data within one business
- start Stripe checkout for their own entitlement
- consume only their own metered AI/action budget

A product sub-user must never:

- read or mutate another product sub-user
- fake payment, entitlement, revenue, or usage
- read `businesses` or any operator control-plane table
- reach operator dashboard/tool APIs
- mint or reuse operator capabilities
- fetch raw provider keys or Safebox authority secrets
- make Safebox call arbitrary URLs
- depend on OpenMeter or Umami for access authority

## Prime Invariant

Never run product-app/customer work on an operator-capable database session.

Never run operator/business-tool work on an app/customer database session.

No code path should need to "become app" from an operator login. App traffic starts
as app-only and stays app-only. Operator traffic starts as operator-only and stays
operator-only. Safebox authority traffic starts as safebox-only and stays
safebox-only.

## Required Target Roles

Create and use distinct login roles:

| Role | Login? | Plane | Must be able to | Must NOT be able to |
|---|---:|---|---|---|
| `takyon_operator_runtime` | yes | operator/dashboard/tools | read/write non-money operator runtime state, enqueue work, read business ownership | directly mint/spend money ledgers; become app/safebox authority |
| `takyon_app_runtime` | yes | product app/sub-user APIs | read/write only scoped app-customer state allowed by RLS/functions | read `businesses`; read cross-tenant app data; set role to operator/safebox; enable bypass |
| `takyon_safebox_authority` | yes | Safebox only | process signed webhooks, reserve/settle/release, provider calls, guarded grants | be exposed to operator/sub-user runtime env |
| `takyon_migration` | yes | migration/deploy only | run migrations | be used by live runtime services |

The current pattern `takyon_runtime` login plus `SET ROLE takyon_app` is temporary
legacy and must be removed from production request paths.

## Required DSNs

Stop vending one generic runtime `DATABASE_URL` to every plane. Replace with:

- `TAKYON_OPERATOR_DATABASE_URL`
- `TAKYON_APP_DATABASE_URL`
- `TAKYON_SAFEBOX_DATABASE_URL`
- `TAKYON_MIGRATION_DATABASE_URL`

Safebox may resolve and vend only the DSN appropriate for the calling plane. The
operator service must not be able to ask for the app DSN as a fallback; the app
service must not be able to ask for the operator DSN.

## Immediate Containment

Before larger refactors:

1. Restart operator and sub-user services to clear contaminated local pools.
2. Add role assertions at request boundaries:
   - operator/dashboard: fail closed unless `current_user` is the operator role.
   - product app: fail closed unless `current_user` is the app role.
   - Safebox: fail closed unless `current_user` is the safebox role.
3. Normalize or discard pooled connections on release:
   - `ROLLBACK`
   - `RESET ROLE`
   - clear app/customer GUCs
   - restore only the plane-appropriate baseline
4. Treat normalization failure as connection poison: close it; never return it to
   the pool.

This containment is not the final fix. It only reduces blast radius while the role
split lands.

## Obvious Escape Hatches To Close

These are not separate projects. They are part of the same sub-user boundary fix.

1. Safebox env vending and shared-token authority.
   - Delete product/operator runtime reliance on `GET /v1/env/{key}`,
     `POST /v1/env/first`, `/v1/env/snapshot`, and `/v1/env`.
   - The shared `TAKYON_SAFEBOX_TOKEN` may be temporary transport reachability
     only; it must not authorize spend, secret egress, operator session minting,
     Stripe mutation, or provider forwarding.
   - Every spendful/provider/secret action must require a scoped Safebox
     capability for exactly that action/account/cost.
   - Product sub-user capability scope can contain only `{business, app_user,
     action, max_cost, nonce, ttl}`. It cannot mint operator/session authority.

2. OpenMeter authority residue.
   - Remove `metadata.openmeter_authority` and any code path where OpenMeter can
     be treated as access authority.
   - `source='openmeter'` rows must not confer product access. Either move them
     to mirror-only tables/metadata or exclude them from `get_active_entitlement`
     and tier resolution.
   - Remove metadata like `billing_authority: "openmeter"` from checkout and
     entitlement flows. Stripe + local ledgers are authority; OpenMeter is a
     reporting projection only.

3. Safebox provider forwarding SSRF.
   - `composio_forward` must accept relative Composio API paths only.
   - Safebox provider routes must enforce fixed upstream host allowlists.
   - Absolute URLs, private IPs, localhost, metadata IPs, and caller-chosen hosts
     must fail before any outbound socket opens and before any provider key is
     attached.
   - Product sub-user routes must not expose generic provider forwarding.

4. Product-origin and VPC trust.
   - Sub-user origin services should be reachable publicly only through
     Cloudflare/Caddy product hosts.
   - Do not trust the whole `10.116.0.0/20` VPC as a product-origin bypass. Use
     host firewall rules and exact private service allowlists instead.
   - A compromised sub-user host must not get a cheap path to operator-only
     `:9119`, Safebox admin routes, Docker, or worker-control ports.

5. Raw secrets on runtime planes.
   - Sub-user and operator services must not carry paid-provider keys in process
     env.
   - Local ignored `.env` files and temp deploy copies are not production
     authority and should be cleaned after cutover/rotation.
   - Doppler can store secrets; Safebox enforces authority. Neither replaces the
     DB and capability boundaries above.

## Main Work

1. Add migrations for the new login roles and grants.
   - App role gets no membership in operator/safebox roles.
   - Operator role gets no need to `SET ROLE takyon_app`.
   - Safebox role owns or can execute only the authority functions it needs.

2. Split database URL resolution.
   - `runtime_app.resolve_database_url()` must become plane-aware.
   - operator code resolves operator DSN.
   - sub-user/product app code resolves app DSN.
   - Safebox code resolves safebox DSN.
   - migrations resolve migration DSN.

3. Remove operator-capable app demotion.
   - Retire `_pg_app_scope` for production app/customer request paths.
   - Replace it with "open/use app-plane connection from app pool."
   - Retire `app_usage._ledger_gate_scope` session-level `SET ROLE` for production.
   - App usage reserve/settle/release still goes through SECURITY DEFINER gates.

4. Remove GUC-only bypass authority.
   - Best end state: app-plane policies do not rely on `takyon.rls_bypass`.
   - If a temporary bypass remains, `takyon_rls_bypass()` must require an allowed
     operator/safebox `current_user`; a settable GUC alone is never authority.
   - Prove `takyon_app_runtime` setting `takyon.rls_bypass=1` does not expand access.

5. Split or gate service surfaces.
   - Operator service must not serve public product app customer APIs.
   - Sub-user service must not serve dashboard/operator websocket/tool APIs.
   - Shared Python modules are acceptable; shared live DB authority is not.

6. Preserve money gates.
   - App role cannot directly write `app_usage_events`, `app_entitlements`, or
     `app_revenue_events`.
   - Operator role cannot directly mint billing, custody, or creative-credit money.
   - Safebox remains the authority for signed Stripe webhooks, provider keys,
     reserve/settle/release, and guarded grants.

7. Remove OpenMeter from entitlement authority.
   - Delete or hard-disable `openmeter_authority`.
   - Make `get_active_entitlement` and tier sync ignore OpenMeter mirror rows.
   - Keep OpenMeter only as async/fail-soft reporting.

8. Remove shared-token provider/admin authority.
   - Replace shared-token acceptance on sensitive Safebox routes with scoped
     capabilities or route-specific service credentials.
   - Delete `/v1/env/*` once all runtime consumers use broker/capability routes.

9. Lock provider forwarding to fixed upstreams.
   - Patch Composio forwarding to relative-only paths.
   - Add tests that absolute URLs and private IPs do not open sockets.

10. Tighten product-origin network boundaries.
    - Add tracked host firewall rules for operator, sub-user, and Safebox.
    - Remove broad VPC trust from product-origin bypass rules.
    - Keep Safebox private, with only exact service paths reachable from exact
      runtime hosts.

11. Delete stale product-auth and billing branches.
    - Supabase is the only product sub-user auth path.
    - Stripe webhooks/checkouts plus local ledgers are the only billing authority.
    - Legacy magic-link, fake/test entitlement, and fallback checkout/access paths
      must remain removed or unreachable.

## Forbidden Fixes

Do not "fix" this by:

- granting `takyon_app` access to `businesses`
- letting app/customer code use operator DSNs
- leaving app/customer code on an operator login and relying on `SET ROLE`
- treating `takyon.rls_bypass=1` as authority by itself
- adding dashboard-side retries around permission errors
- claiming OpenMeter, Cloudflare AI Gateway, or Doppler solves DB authority
- leaving `source='openmeter'` active entitlements as access-conferring rows
- using the shared Safebox token as authority for provider forwarding, spend, or
  secret reads
- allowing Safebox forwarding routes to accept absolute URLs
- relying on broad VPC trust as the product-origin security boundary
- presenting a local-only proof as production proof

## Required Negative Tests

Automated and live probes must prove:

- app role cannot `SELECT businesses`
- app role cannot `SET ROLE` to operator/runtime/safebox
- app role cannot enable RLS bypass by setting `takyon.rls_bypass=1`
- app role cannot read another business's app users, sessions, records, entitlements,
  or usage
- app role cannot directly insert/update/delete `app_usage_events`
- app role cannot directly insert/update/delete `app_entitlements`
- app role cannot directly insert/update/delete `app_revenue_events`
- operator role cannot directly mutate money ledgers
- OpenMeter mirror rows cannot grant product access
- setting `metadata.openmeter_authority=true` cannot make OpenMeter authoritative
- product sub-user traffic cannot call operator/dashboard APIs
- product sub-user traffic cannot mint operator/session capabilities
- shared Safebox token alone cannot spend, fetch provider keys, or call generic
  provider forwarding
- shared Safebox token alone cannot mutate Stripe catalog state
- shared Safebox token alone cannot call operator-only Safebox authority routes
  from any host; those routes require the separate operator Safebox token and
  the exact operator client allowlist
- Safebox Composio/provider forwarding rejects absolute/private/localhost URLs
  before network I/O
- sub-user origin rejects non-Cloudflare public traffic and does not trust the
  whole VPC as an operator/Safebox bypass
- operator dashboard prompt submit works with `current_user = takyon_operator_runtime`
- product app account/action routes work with `current_user = takyon_app_runtime`
- Safebox webhook/provider/ledger operations work with `current_user =
  takyon_safebox_authority`

## Implementation Checkpoint - 2026-06-26

First containment landed in code, but this is not TRUE GREEN yet.

Done:

- `runtime_app.resolve_database_url()` is plane-aware:
  - operator -> `TAKYON_OPERATOR_DATABASE_URL`
  - product/subuser app -> `TAKYON_APP_DATABASE_URL`
  - Safebox -> `TAKYON_SAFEBOX_DATABASE_URL`
  - migration/deploy -> `TAKYON_MIGRATION_DATABASE_URL` / `MIGRATION_DATABASE_URL`
- Production host roles no longer accept generic `DATABASE_URL` as a fallback for
  operator/app/Safebox DB authority.
- DB URLs are read from the process' own local env/Takyon env file, not from
  Safebox `/v1/env/first`; shared Safebox transport reachability is not DB
  authority.
- Safebox `/v1/env/*` now denies DB authority names (`DATABASE_URL`,
  `POSTGRES_URL`, `TAKYON_APP_DATABASE_URL`, etc.) for read/snapshot/first and
  refuses writes/deletes.
- Safebox `/v1/env/*` is now public-config-only. It does not vend bearer tokens,
  service-account JSON, dashboard session tokens, storage access keys, analytics
  API keys, OpenMeter API tokens, or other credential values. Those values stay
  local to Safebox or move behind explicit Safebox action routes.
- `build_runtime_app()` and dashboard-mounted Postgres routers now bind control
  routes to operator DB authority and app gateway routes to app DB authority.
- `build_runtime_app()` is host-role aware:
  - operator hosts mount only operator/control + creative routes
  - sub-user hosts mount only app/AI-gateway routes
  - combined/local mode remains test/dev only
- Dashboard `_mount_postgres_runtime_routes()` is host-role aware too:
  - operator service does not resolve the app DSN
  - sub-user service does not resolve the operator DSN
  - each mounted dependency asserts the matching Postgres role
- HTTP host-role gating is stricter:
  - operator Python returns 404 for product-host traffic if edge routing ever misses
  - sub-user Python serves app APIs only for product hosts
  - product hosts cannot reach `/v1`, `/internal`, `/auth`, or `/billing` prefixes
- Product app helpers for rate limiting, generate, and search resolve the app
  plane explicitly.
- Safebox DB helpers resolve the Safebox plane explicitly.
- Worker loop resolves the operator plane explicitly.
- Deploy scripts now use explicit operator/migration DSNs and assert the matching
  role before idle checks or migrations.
- CLI operator-budget DB work uses a single operator connection helper with
  operator-plane role assertion.
- Business bootstrap creative-credit seeding no longer opens an operator DB
  connection; it calls the Safebox bootstrap authority path and lets Safebox open
  its own authority connection.
- Diagnostic scripts now use `resolve_database_url(plane="operator")` and assert
  the operator role instead of inheriting an unnamed `DATABASE_URL`.
- `TakyonStore` pools are keyed by plane as well as DSN, and pooled connections
  are scrubbed on release with `ROLLBACK`, `RESET ROLE`, and cleared app/RLS GUCs.
- App-plane `TakyonStore` connections now initialize with RLS bypass off. Operator
  and Safebox authority paths may request bypass where their roles are permitted;
  app/customer DB sessions do not ask for it at connection setup.
- Tests now lock:
  - old generic `DATABASE_URL` cannot satisfy an operator host role
  - subuser host role resolves the app DSN
  - Safebox env routes do not vend DB authority
  - dashboard helper asks for the operator plane
  - runtime/router mounting does not resolve or mount the other host role's plane
  - host-role middleware rejects operator/product and subuser/dashboard crossover
- OpenMeter is hard-disabled as access authority:
  - `metadata.openmeter_authority` is ignored
  - `source='openmeter'` rows are excluded from active entitlement and cached-tier
    resolution
  - checkout metadata no longer says `billing_authority: "openmeter"`
  - `/v1/env` may expose OpenMeter endpoint URLs, but never the OpenMeter bearer
    token; OpenMeter remains a fail-soft projection/mirror, not an authority rail
  - OpenMeter projection metadata uses `projection: "openmeter"` rather than
    `authority: "openmeter"`
- `takyon_rls_bypass()` has a hardening migration so the GUC is not authority by
  itself; app roles are not in the allowed `current_user` list.
- Composio forwarding accepts only relative API paths before broker/socket access;
  absolute, scheme-relative, local/private-target style inputs fail before any
  outbound request.
- Request/connection boundary role assertions now check both `session_user` and
  `current_user`:
  - operator connections reject app-current-user leakage
  - app connections reject demoted operator sessions (`takyon_runtime` ->
    `takyon_app`)
  - Safebox helpers assert Safebox authority before money/provider DB work
- Runtime DB role assertions no longer accept legacy cutover roles by default.
  Production assertions expect `takyon_operator_runtime`, `takyon_app_runtime`,
  `takyon_safebox_authority`, or `takyon_migration`; accepting `takyon_runtime`,
  `takyon_app`, or `postgres` now requires the explicit temporary
  `TAKYON_ALLOW_LEGACY_DB_ROLES=1` override.
- Shared money-ledger gates no longer demote runtime connections with
  `SET ROLE takyon_runtime`. The legacy opt-in was removed: `billing.py`,
  `business_credits.py`, and `custody.py` can call their SECURITY DEFINER money
  functions only on a Safebox authority DB session.
- `0046_revoke_legacy_cross_plane_role_memberships.sql` removes the old
  `takyon_app` -> `takyon_runtime` membership bridge and defensive cross-plane
  memberships among the split live roles. New code no longer calls `SET ROLE`,
  and the DB should no longer permit the old operator/app role-switch bridge
  after migrations run.
- The old PG least-privilege integration test was rewritten around the split
  roles: `takyon_operator_runtime` is denied direct money-ledger writes, while
  `takyon_safebox_authority` is the role that exercises the billing/credit gates.
- `0044_authority_split_login_roles.sql` creates the target login roles and
  explicit grants without embedding passwords or adding cross-plane role
  membership.
- `_pg_app_scope` now requires a direct app-plane database login. It binds only
  request-local app RLS GUCs and no longer runs a role-changing command from an
  operator-capable session.
- `app_usage._ledger_gate_scope` now takes an explicit authority plane. App
  session usage calls run on the app-plane login through app-specific
  session-bound ports; generic `safebox_*_usage` primitives require the Safebox
  authority login. Product usage still writes the ledger only through SECURITY
  DEFINER gate functions, but the app role no longer gets a generic
  "pick `{business, app_user}` by argument" money verb.
- `_pg_app_scope()` and the app usage session ports reuse shared
  `assert_takyon_pg_role(...)` boundaries. The legacy direct `takyon_app` login
  is rejected by default and works only under the temporary
  `TAKYON_ALLOW_LEGACY_DB_ROLES=1` cutover flag; neither scope carries its own
  broader app-role allowlist.
- Public product app API entrypoints now enter `app_runtime_database_plane()`
  before dispatching shared handlers, so `_store()` constructs an app-plane store
  for customer app requests.
- On `TAKYON_HOST_ROLE=subuser`, a plain `_store()` now fails closed instead of
  defaulting to an operator-plane store; app handlers must explicitly enter the
  app runtime context.
- `0045_app_runtime_identity_ports.sql` adds narrow app-runtime SECURITY DEFINER
  ports:
  - `takyon_app_runtime_business()` exposes only active runtime business fields,
    not raw `businesses` access.
  - `takyon_app_control_blocker()` exposes only kill/pause candidates needed by
    app request gating, not raw `control_states` access.
  - `takyon_app_bind_supabase_session()` binds a server-verified Supabase user,
    recomputes tier from local Stripe-backed entitlements while ignoring
    OpenMeter mirror rows, and mints one hashed app session.
  - `takyon_app_validate_session()` validates a presented hashed app session
    without requiring app runtime to read `app_sessions` directly.
  - `takyon_app_revoke_session()` revokes only the presented hashed session.
  - `takyon_app_service_email_recipient()` and
    `takyon_app_service_email_sends_today()` let a service app session resolve a
    same-business non-service recipient and enforce the daily email cap without
    making the email route depend on raw cross-user reads.
  - `takyon_app_visible_directory_entries()` and
    `takyon_app_visible_directory_entry()` expose only the authenticated viewer's
    consented, non-blocked directory projection instead of giving product app
    routes raw `app_users` visibility for directory browsing.
  - `takyon_app_record_event()` records only `app.*` events under
    `business:{slug}/app`, so app-plane audit breadcrumbs do not require direct
    `events` table access.
  - `takyon_app_media_usage()` exposes only bounded media quota totals needed to
    enforce per-user/per-business upload limits without raw cross-user media
    reads.
- `0045_app_runtime_identity_ports.sql` also replaces the app media write RLS
  policy so `takyon_app_runtime` can insert/delete only the bound app user's own
  media rows; bypass remains unavailable to app roles.
- App runtime gets execute on those ports plus SELECT on app-owned runtime
  metadata (`app_surface_contracts`, `app_plan_policies`), but no
  `SELECT ON businesses`, no direct `SELECT ON app_users`, and no broad DML on
  app identity/session/money tables.
- App runtime no longer gets direct `SELECT` on `app_sessions`; session
  validation, revocation, and effective-user resolution go through app authority
  functions instead. `0045` defensively revokes the old session-table grant from
  `takyon_app_runtime`/`takyon_app`.
- The internal `takyon_app_resolve_tier()` helper is no longer directly granted
  to app runtime roles. App runtime reaches tier recomputation only through the
  verified Supabase session-bind port, so a direct app-role SQL call cannot
  recompute another `{business, app_user}` tier by argument alone.
- Fresh app-plane helper connections for Supabase login/session revoke, service
  email recipient/cap checks, and media usage reads now enter `_pg_app_scope()`
  before calling their SECURITY DEFINER ports.
- Product `/account` usage display no longer opens/mutates `app_budgets` on the
  app plane; budget rows are opened/rolled by the authoritative reserve path.
- App runtime no longer gets direct `SELECT` on `app_budgets`. That table has no
  app-customer RLS policy and is owned by the authoritative reserve path, so it
  must not be a raw cross-business budget side channel for the app DB role.
- Product `/account` reads no longer run checkout reconciliation on the app
  plane; Stripe/webhook/Safebox authority owns payment reconciliation, not a
  customer read.
- `0047_app_runtime_money_read_ports.sql` adds session-bound app account/action
  read ports for product money/access display:
  - `takyon_app_account_entitlements()`
  - `takyon_app_account_usage_summary()`
  - `takyon_app_account_revenue_summary()`
  - `takyon_app_action_usage_limit()`
  These validate the presented app session hash and derive the app user inside
  SECURITY DEFINER code before reading `app_entitlements`, `app_usage_events`,
  or `app_revenue_events`.
- `0047_app_runtime_money_read_ports.sql` revokes direct app-role `SELECT` on
  `app_entitlements`, `app_usage_events`, and `app_revenue_events`; the product
  app role can no longer read those ledgers by setting app RLS GUCs.
- `0050_ignore_app_user_id_guc_for_app_roles.sql` makes
  `takyon_rls_bound_app_user_id()` ignore `takyon.rls_app_user_id` entirely when
  `current_user` is `takyon_app` or `takyon_app_runtime`. Product app roles may
  bind a session hash; they cannot self-select another customer just by setting
  a custom GUC. Non-app internal authority can still use `app_user_id` as a
  scoped helper while its own role gates remain in force.
- Product `/account` on the app plane now resolves the caller only through
  `takyon_app_validate_session()` and skips the legacy `app_users` reselect, so
  account display works without direct app-role access to `app_sessions` or raw
  user rows.
- Product customer writes for profile, directory, connections, and records now
  take direct app-plane leaf branches before the generic `TakyonStore.commit()`
  path. These branches require the presented app session token, bind RLS by that
  token, write only the requested app-customer table, and record at most one
  bounded `app.*` event through `takyon_app_record_event()`.
- Product self-reported `/usage` no longer writes the usage ledger on the app
  plane. Positive spend is rejected and must flow through the metered
  server/Safebox brokers; zero-cost self-reporting returns a no-op success.
- Product media upload/delete now binds app RLS scope before touching
  `app_media`, and quota checks use `takyon_app_media_usage()` instead of
  broad app-role reads.
- Product media upload now carries the session-validated app user email/tier
  into the media leaf, so the app-plane upload path does not reopen `app_users`
  just to price/check the uploader.
- The media leaf itself now carries the validated session token into uploader
  resolution, quota reads, row insert/delete, and usage reserve/settle/release.
  A helper call that has a session token no longer reverts to app-user-id-only
  RLS binding below the route handler.
- `0051_session_bound_app_media_usage.sql` replaces the app-granted media quota
  port's argument-authority read with session authority. `takyon_app_media_usage`
  now treats its second text argument as a hashed app session, joins
  `app_sessions`/`app_users`, derives the app user inside SECURITY DEFINER code,
  and raises `app_session_required` if the session is absent/revoked/expired.
- Product checkout creation now has an app-plane branch before the legacy
  operator path. It requires the presented app session, derives the customer
  email from that session, refuses unconfigured Stripe plans in live mode, derives
  canonical return URLs server-side, writes only the caller's own checkout intent,
  and asks Safebox to create the Stripe Checkout Session.
- `0049_revoke_app_checkout_session_direct_access.sql` revokes app-role direct
  access to `app_checkout_sessions`. Product app code may create/read its own
  `app_checkout_intents`, but settled Stripe Checkout Session rows are
  Safebox/webhook payment evidence, not product-runtime state.
- Product checkout recovery/reconciliation through Safebox now requires expected
  product context (`business` plus `app_user_id` or customer email) before any
  Stripe session fetch. A shared Safebox transport token plus a raw `cs_...`
  session id is not enough to query or reconcile checkout state.
- Test-mode checkout no longer grants fake entitlements or rewrites product
  receipts on the app plane; it records a bounded suppressed-side-effect intent
  only.
- Product subscription cancellation on the app plane now validates the caller's
  app session, passes that session token to Safebox, and calls Safebox's
  app-subscription cancel authority route instead of importing Stripe in the
  product runtime.
- The generic Safebox `/v1/stripe/request` tunnel no longer permits
  `POST subscriptions/{id}` mutation. Product subscription cancellation must use
  `/v1/stripe/app-subscription/cancel`, which derives the cancelable subscription
  from the Safebox DB by `{business, app_user}` and requires the same app
  session to validate as that `app_user` before touching Stripe.
- The generic Safebox `/v1/stripe/request` tunnel also no longer permits
  shared-token reads of `checkout/sessions/{id}` or `subscriptions/{id}`. Checkout
  recovery and subscription inspection happen only inside dedicated Safebox
  authority routes that bind the request to product context first.
- Stripe catalog mutations (`POST /products`, `POST /prices`) through Safebox's
  generic Stripe tunnel now require the operator route token plus exact operator
  client allowlist. Product checkout creation remains available only through
  recorded app checkout intents and matching plan price authority.
- Service email on the app plane now requires a service app session, validates it
  inside the app RLS scope, resolves recipients/daily caps through service-session
  authority ports, and sends live mail through the Safebox product-provider
  broker (`postmark.send`). The product runtime sends only
  `recipient_app_user_id`; Safebox revalidates the service session, resolves the
  same-business non-service recipient email itself, enforces the daily email cap,
  and reserves/settles `email_send` usage before using the Postmark key. A
  caller-supplied `to_email` is not authority. Live service-session email now
  fails closed if the Safebox provider broker is disabled; it does not fall back
  to local reserve plus the legacy Postmark helper. The legacy
  `/v1/postmark/send` magic-link route is operator-route-token gated and is not a
  product shared-token tunnel.
- Custom app action invocation now requires the app session, binds app scope
  before session validation, checks the app kill/work-focus gate, then runs the
  already-metered action path.
- App action billing no longer re-reads `app_users` to infer tier during reserve;
  it uses the session-validated tier already carried by the caller and the
  authoritative active-entitlement lookup for the spend gate.
- `0048_app_runtime_session_usage_ports.sql` adds session-bound usage write
  ports:
  - `takyon_app_session_plan()`
  - `takyon_app_reserve_usage()`
  - `takyon_app_settle_usage()`
  - `takyon_app_release_usage()`
  These validate the app session hash, derive the app user inside the DB, verify
  settle/release reservations belong to that session user, and then call the
  generic Safebox-owned gate.
- `0048_app_runtime_session_usage_ports.sql` revokes app-role execute on
  `safebox_reserve_usage()`, `safebox_settle_usage()`, and
  `safebox_release_usage()`, plus the cross-user
  `safebox_reconcile_held_usage()` sweeper. The generic usage gate remains a
  Safebox authority primitive; product app code gets only session-bound app
  ports.
- Product action billing, media upload billing, and AI gateway fallback billing
  now pass the validated app session token into the usage ledger lifecycle, so
  reserve/settle/release are all bound to the same product app session instead
  of trusting caller-supplied `{business, app_user}` arguments.
- The AI gateway no longer needs raw app-role entitlement reads for plan gating
  on the app plane; it uses `takyon_app_session_plan()` to resolve the caller's
  active entitlement and plan metadata from the session hash.
- Product directory list/read on direct app-runtime sessions now uses the
  visible-directory SECURITY DEFINER ports, including target lookup by email, so
  customer directory browsing does not require raw app-user table reads.
- Product connection list/write on direct app-runtime sessions now reuses the
  visible-directory projection for target visibility instead of selecting target
  `app_users` rows. Like/pass require a visible active target; block/unblock use
  only the session-bound actor plus the target id and let the DB relationship
  constraint enforce existence.
- `scripts/probe_db_authority_split.py` is the tracked live DB-role probe. Run it
  on the relevant VPS with that service's env loaded to record
  `session_user/current_user`, app-role denial of raw `businesses`/`app_users`/
  `app_sessions` reads, app-role money-ledger DML denial, app-role GUC-bypass
  denial, operator money-ledger DML denial, and cross-plane `SET ROLE` denial.
  It prints JSON and never prints DSNs.
- Static security tests now pin that product app customer write handlers cannot
  drift back to generic commit first, and that app-plane account reads do not
  mutate checkout/payment state.
- Stale operator-plane product-customer session fallbacks have been removed or
  made fail-closed locally:
  - customer-session profile, directory, connection, record, media, email, and
    action paths now require the app DB plane before binding app RLS scope
  - product subscription cancellation has no runtime Stripe fallback
  - shared Safebox transport token alone cannot cancel product subscriptions;
    the Safebox cancel route requires an app session that matches the target
    `{business, app_user}`
  - product checkout creation has no operator-plane/runtime Stripe fallback
  - Safebox will not create a Stripe Checkout Session from shared-token metadata
    alone; it now requires a matching `app_checkout_intents` row and
    `app_plan_policies.stripe_price_id` before calling Stripe, and success/
    cancel redirects must stay on the expected `{business}.coscale.app` app
    surface
  - Safebox will not reconcile a product Checkout Session from shared-token
    `session_id` alone; recovery requires expected app context before Stripe I/O
  - shared Safebox transport token alone cannot read arbitrary Stripe Checkout
    Session or Subscription objects through the generic Stripe tunnel
  - shared Safebox transport token alone cannot create Stripe products or prices;
    catalog mutation requires the operator Safebox route credential
  - shared Safebox transport token alone cannot send Postmark email through the
    legacy route; product email uses the product capability/usage broker and the
    broker resolves recipient email from `{business, service_session,
    recipient_app_user_id}` rather than trusting caller-supplied email. If that
    broker is disabled, live service-session email fails closed.
  - gated creative FAL provider calls are no longer a wildcard FAL path proxy.
    The Safebox exposes only the explicit UGC route
    `/v1/providers/fal/kling-image-to-video`, forwards to the fixed
    `fal-ai/kling-video/v3/pro/image-to-video` queue endpoint, and will only
    follow queue callback/result URLs on the fixed `https://queue.fal.run` host.
    Absolute/private/localhost callback URLs are rejected before any follow-up
    socket opens and before the FAL key is sent to another host.
  - legacy Meta Graph broker calls no longer accept a caller-chosen upstream
    host. The route and client both pin Graph forwarding to
    `https://graph.facebook.com`; stale callers that pass another host fail
    before the Meta system-user token is attached or any outbound request opens.
  - Tavily provider calls are no longer a caller-chosen endpoint path. The
    provider leaf and Safebox broker accept only the fixed Tavily `search` and
    `extract` wire endpoints; `search_advanced` is treated as a priced search
    operation, not a new URL path. Unsupported endpoints fail before reserve,
    key resolution, or socket open.
  - the generic Tavily web plugin no longer reads `TAVILY_API_KEY` directly from
    process env. On remote runtime planes it is unavailable/fail-closed because
    it has no caller-bound `operator.session` capability; on local/Safebox
    authority it resolves through the Safebox config helper.
  - static-ad image generation no longer reads `OPENAI_API_KEY` from process env
    inside the business skill. Live renders go through `business_static_ad_generate`
    and Safebox; direct local SDK dev is explicit `--api-key-file` only.
  - UGC video generation no longer reads `OPENAI_API_KEY`, `FAL_KEY`, or
    `FAL_API_KEY` from ambient env/local `.env` inside the business skill. Live
    renders go through `business_ugc_ad_generate` and Safebox; direct local
    provider debugging is explicit `--openai-api-key-file` / `--fal-key-file`
    only.
  - product Supabase auth no longer has an ambient `SUPABASE_JWT_SECRET` helper.
    HS256 Supabase tokens without an explicitly supplied test secret are verified
    server-side by Supabase Auth; a runtime-held symmetric secret cannot become a
    product-user forgery path.
  - operator provider proxy clients now require an explicit signed operator
    capability before opening a request; `safebox.proxy_request()` sends that
    capability as `x-api-key` and refuses locally if a caller only has the shared
    Safebox transport token. The generic Tavily web plugin fails closed on remote
    runtime planes because it has no business-owner identity to mint an
    `operator.session` capability; product sub-user search continues through the
    app AI gateway broker.
  - `/v1/env` HTTP mutation is disabled outright. The shared Safebox transport
    token cannot write or delete even public config, and remote
    `save_env_backed_value()` / `remove_env_backed_value()` fail before opening a
    socket. Safebox secrets/config are provisioned locally on the authority host
    or through the chosen out-of-band secret manager, not through a runtime plane.
  - operator-only Safebox authority routes now require
    `TAKYON_SAFEBOX_OPERATOR_TOKEN` plus an exact operator-client allowlist in
    addition to the shared transport token. This covers Auth0 dashboard session
    authority, top-level user API keys, operator session capability minting,
    operator billing/payout routes, creative-credit authority routes, storage
    routes, product-edge/Vercel infrastructure routes, and Meta/Composio
    provider forwarders.
  - the tracked Safebox systemd unit sets
    `TAKYON_SAFEBOX_OPERATOR_CLIENTS=10.116.0.18` (the operator VPS private
    address). It deliberately does not allow the whole `10.116.0.0/20` VPC; the
    sub-user VPS is `10.116.0.3`.
  - the tracked sub-user systemd unit unsets `TAKYON_SAFEBOX_OPERATOR_TOKEN`, so
    a mistakenly shared env file does not hand product runtime the operator
    Safebox route credential.
  - tracked operator dashboard, operator worker, operator Docker broker, and
    sub-user runtime units now unset `TAKYON_CAP_SIGNING_KEY`, so shared env-file
    drift cannot hand token-minting authority to non-Safebox processes. The
    Docker broker also unsets `TAKYON_SAFEBOX_OPERATOR_TOKEN` because it holds
    Docker authority only; it does not need operator Safebox route authority.
  - `TAKYON_SAFEBOX_OPERATOR_TOKEN` is now in the canonical Safebox
    self-authority secret set alongside `TAKYON_CAP_SIGNING_KEY` and
    `TAKYON_SAFEBOX_TOKEN`, so `/v1/env` cannot vend, advertise, overwrite, or
    delete it even if a future broad token allowlist appears.
  - the shared secret provision helper treats `TAKYON_SAFEBOX_OPERATOR_TOKEN` as
    an operator+Safebox authority secret: it skips local workspace mirrors,
    writes operator host env files, and writes Safebox host env files.
  - operator and Safebox deploy scripts now fail fast if
    `TAKYON_SAFEBOX_OPERATOR_TOKEN` is missing from both tracked remote env
    files, so a rollout cannot silently restart into broken/half-open operator
    Safebox authority.
  - checkout reconciliation no longer falls back to direct Stripe in core; it
    requires Safebox recovery or returns a fail-closed recovery error
  - `app_media` enforces the same app-plane requirement inside the module, so
    route-only callers cannot bypass the handler guard

Still required:

- Apply the role migrations to production and put the named DSNs into service
  env/Doppler on operator, sub-user, Safebox, and deploy/migration rails. This
  goal does not require password rotation unless the operator separately asks
  for it.
- [HUMAN] Provision the existing or approved `TAKYON_SAFEBOX_OPERATOR_TOKEN` only
  on the operator and Safebox hosts using `deploy/shared/provision-safebox-secret.sh`,
  and keep it absent/scrubbed on the sub-user host.
- Deploy the Safebox operator-token/client allowlist and probe from the sub-user host
  that `/v1/operator/session-token`, `/v1/storage/get`, and operator provider
  forwarders return `401 operator_unauthorized` with only the shared
  `TAKYON_SAFEBOX_TOKEN`, and `403 operator_client_not_allowed` if a bad client
  ever presents the operator route token.
- Replace the operator-client allowlist containment with caller-bound operator
  capabilities where practical. The allowlist blocks the sub-user plane now; it
  is not a reason to let caller-supplied `operator_user_id` remain the long-term
  proof of operator identity.
- Keep auditing newly added product-customer routes against this rule: public
  product routes enter `app_runtime_database_plane()`; operator/admin flows use
  explicit operator or Safebox ports, not customer app RLS scopes.
- Run `scripts/probe_db_authority_split.py` on operator, sub-user, and Safebox
  hosts after DSN cutover and save the JSON output as live evidence.
- Run the live app-role path proof that session/account/profile/directory/
  connection/media/action routes still work through the bounded ports.
- Run the fresh-business security E2E after deploy.

## Required E2E

After deploy, run a fresh-business security E2E:

1. Create a brand-new business through `app.fourmanifold.com`.
2. Publish live product host.
3. Product sub-user login on `slug.coscale.app`.
4. Stripe test checkout grants entitlement.
5. One metered AI call settles in `app_usage_events`.
6. Stripe test refund revokes entitlement.
7. Same signed-in product user is denied after revocation.
8. Operator dashboard prompt submit succeeds before and after product app calls.
9. Meta Ads v2 preflight reads `businesses` as operator role and does not hit
   `takyon_app`.
10. Log/probe `session_user` and `current_user` at each plane boundary.
11. Attempt OpenMeter-only access projection; product action remains denied.
12. Attempt product-host calls to operator-only routes; they 404/401 without
    invoking operator code.
13. Attempt product subscription cancellation with only the shared Safebox
    transport token; it is rejected before any Stripe mutation.
14. Attempt Safebox forwarding with absolute/private URLs; it is rejected with no
    outbound request.

## Acceptance Summary

The goal is complete only when we can say, with live proof:

```text
Operator dashboard/tools never run on app DB authority.
Product app/sub-user APIs never run on operator DB authority.
Safebox is the only money/provider authority.
No app/customer path can climb to operator by SET ROLE or by setting a GUC.
OpenMeter and Umami are mirrors/analytics only; they cannot grant or revoke access.
Shared tokens and VPC reachability are transport, not authority.
```
