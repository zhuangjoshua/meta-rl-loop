# Takyon Backend Implementation Log

Append-only record of the Supabase-target backend build described in
`mediationplan.md`. Every increment lists exactly what changed and how to
revert it, so the whole effort can be unwound cleanly if we decide to.

Conventions:
- The canonical Python project root is `hermes-agent-main/` (holds `pyproject.toml`,
  `plugins/`, `tests/`). The workspace root holds the `./takyon` launcher,
  `.takyon/` runtime state, `mediationplan.md`, and this log.
- Tests run from `hermes-agent-main/` with `.venv/bin/python -m pytest`.
- New code is backend-agnostic where possible so it behaves identically on the
  current SQLite control plane and the Supabase (Postgres) target.

---

## Increment 1 — Opaque per-user API key core (P1 identity primitive)

**Date:** 2026-05-30

**What:** Added the pure-function security core for the single opaque per-user
Takyon API key (mint / structural-check / non-secret prefix / SHA-256 hash /
constant-time verify). No DB, framework, or network coupling, so it ports
unchanged from SQLite to Supabase.

**Why:** The API key is the entire per-user boundary (platform-minted, never
user-generated, never stored in clear). This is the smallest piece of P1 that is
fully testable today regardless of the Postgres infra decision, so it is built
and verified first. Follows the existing runtime convention
(`hashlib.sha256(...).hexdigest()`, `secrets.token_urlsafe(32)`,
`hmac.compare_digest`).

**Files created:**
- `hermes-agent-main/plugins/takyon/user_api_keys.py` — `generate_api_key`,
  `is_well_formed`, `key_prefix`, `hash_api_key`, `verify_api_key`.
- `hermes-agent-main/tests/plugins/test_takyon_user_api_keys.py` — 13 unit tests.

**Verification:** `.venv/bin/python -m pytest tests/plugins/test_takyon_user_api_keys.py -q`
→ **13 passed**.

**Revert:**
```sh
rm hermes-agent-main/plugins/takyon/user_api_keys.py
rm hermes-agent-main/tests/plugins/test_takyon_user_api_keys.py
```
Nothing else imports this module yet, so removal is self-contained.

**Topology correction (same increment):** the test was first written to a stray
`tests/plugins/` at the *workspace root* before I confirmed the real test tree
lives under `hermes-agent-main/tests/`. The stray root `tests/` dir (which
contained only that one file) was removed with `rm -rf` and the test rewritten to
the correct location. No other files were affected.

---

## Increment 2 — P1 identity-spine migration + Postgres verification

**Date:** 2026-05-30

**What:** Wrote the canonical first migration for the Postgres/Supabase control
plane (`users`, `user_api_keys`, `businesses` with enforced ownership) and an
integration test that runs it against a real local Postgres and asserts the
invariants. Stood up a throwaway local Postgres so this is actually verified, not
just written — nothing here touches production Supabase.

**Why:** Robustness-first: the schema is not "robust" until the one-active-key
partial unique index, citext case-insensitivity, and owner FK have run against
real Postgres semantics that SQLite can't express.

**Files created:**
- `hermes-agent-main/plugins/takyon/db/migrations/0001_identity_spine.sql` —
  canonical schema source of truth (idempotent, greenfield, no backfill).
- `hermes-agent-main/tests/plugins/test_takyon_identity_spine_pg.py` — 6
  integration tests, double-gated (skip unless `psycopg` importable AND
  `TAKYON_TEST_PG_DSN` set), per-worker throwaway DB for xdist safety.

**Local infra provisioned (reversible, dev-only):**
- `brew install postgresql@16` → binaries at `/opt/homebrew/opt/postgresql@16/bin`
  (keg-only; PostgreSQL 16.14).
- `.venv/bin/pip install 'psycopg[binary]'` → psycopg 3.3.4 in the project venv.
- Throwaway cluster: data dir `/.tmp-pg-identity` (matches the repo's `.tmp-*`
  ignore pattern), port `54329`, trust auth, DB `takyon_test`.
  DSN: `postgresql://postgres@127.0.0.1:54329/takyon_test`.

**Start the cluster (note the LC_ALL fix — macOS Postgres aborts startup with
"postmaster became multithreaded" if no locale is set):**
```sh
export LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8
PGBIN=/opt/homebrew/opt/postgresql@16/bin
PGDATA=/Users/Zygote/Downloads/takyon/.tmp-pg-identity
"$PGBIN/pg_ctl" -D "$PGDATA" -o "-p 54329 -k $PGDATA" -l "$PGDATA/server.log" -w start
```

**Verification:**
- With Postgres: `TAKYON_TEST_PG_DSN=postgresql://postgres@127.0.0.1:54329/takyon_test
  .venv/bin/python -m pytest tests/plugins/test_takyon_identity_spine_pg.py -q`
  → **6 passed**.
- Without the DSN: both new test files → **13 passed, 6 skipped** (integration
  test is a clean no-op where there is no Postgres).

**Revert:**
```sh
# stop + delete the throwaway cluster
/opt/homebrew/opt/postgresql@16/bin/pg_ctl -D /Users/Zygote/Downloads/takyon/.tmp-pg-identity stop -m immediate
rm -rf /Users/Zygote/Downloads/takyon/.tmp-pg-identity
# remove the code
rm hermes-agent-main/plugins/takyon/db/migrations/0001_identity_spine.sql
rm hermes-agent-main/tests/plugins/test_takyon_identity_spine_pg.py
# optional: uninstall dev tooling (only if nothing else needs them)
hermes-agent-main/.venv/bin/pip uninstall -y psycopg psycopg-binary
brew uninstall postgresql@16
```
The migration is not wired into any runtime path yet; removal is self-contained.

**Note on the production Supabase target:** this increment verifies the schema on
*local* Postgres only. Pointing the build at a real Supabase project (a fresh one,
distinct from the dead polsia2 era) is a separate, production-touching decision
left for when we cut over — it is intentionally NOT done here.

---

## Increment 3 — API key resolver + shared `pg_conn` fixture

**Date:** 2026-05-30

**What:** Built the server-side opaque-boundary resolver on top of the P1 schema:
given a raw API key (the only user-provided input), return a deliberately small
`ResolvedPrincipal` (identity + owned business slugs) or None — never secrets,
provider keys, or internals. Also added the JIT user-provisioning and
mint/rotate helpers the resolver needs, and a shared per-worker Postgres test
fixture that both Postgres suites now use.

**Why:** This is the actual per-user boundary from `mediationplan.md`. The schema
(Increment 2) is inert until something resolves a presented key into "who is this
and what do they own" while refusing revoked/unknown/suspended principals. Built
as plain functions taking a psycopg connection so it stays backend-agnostic
(identical on SQLite-era and Supabase) and composes with any
transaction/pool strategy.

**Files created:**
- `hermes-agent-main/plugins/takyon/control_plane.py` —
  `ResolvedPrincipal` (frozen dataclass: user_id, key_id, status,
  business_slugs), `get_or_create_user` (JIT by Auth0 `sub`, race-safe via
  `on conflict do nothing` + re-read), `mint_api_key` (the single active key;
  raises if one is already active), `rotate_api_key` (revoke-then-issue in one
  transaction, old row kept for audit), `resolve_api_key` (well-formed → hash →
  join keys/users where not revoked → reject non-active → load owned slugs →
  stamp `last_used_at` → return principal).
- `hermes-agent-main/tests/plugins/conftest.py` — shared module-scoped `pg_conn`
  fixture (per-worker throwaway DB, all migrations applied, `psycopg` imported
  lazily inside the fixture so the suite still collects without it).
- `hermes-agent-main/tests/plugins/test_takyon_control_plane_pg.py` — 9
  integration tests: JIT idempotency, mint→resolve round trip, resolve reflects
  ownership, rejects garbage/unknown, rejects revoked, mint-twice violates
  one-active, rotate revokes+issues, rejects non-active user, stamps
  `last_used_at`.

**Files modified:**
- `hermes-agent-main/tests/plugins/test_takyon_identity_spine_pg.py` — refactored
  to consume the shared `pg_conn` fixture instead of its own; kept `_MIGRATION`
  only for the idempotent-re-apply test. No assertion changes.

**Verification:**
- With Postgres: `TAKYON_TEST_PG_DSN=postgresql://postgres@127.0.0.1:54329/takyon_test
  .venv/bin/python -m pytest tests/plugins/test_takyon_control_plane_pg.py
  tests/plugins/test_takyon_identity_spine_pg.py tests/plugins/test_takyon_user_api_keys.py -q`
  → **28 passed** (9 control + 6 identity + 13 unit).
- Without the DSN: the two Postgres suites are a clean no-op (skipped); the unit
  suite still runs.

**Pre-existing-failure note (NOT caused by this work):** a full `tests/plugins/`
run shows 2 failures — `test_business_work_focus_persists_and_blocks_cross_lane_writes`
and `test_all_seven_plugins_present_in_registry` (extra `xai`). These come from
the `hermes-agent-main` repo already being mid-refactor before any P1 work began
(tracked `core.py` modified, `registry.py` and many `skills/*/SKILL.md` deleted,
all unrelated to identity). All seven P1 files are untracked (`??`) and purely
additive. Per CLAUDE.md these unrelated in-progress changes are preserved, not
reverted.

**Revert:**
```sh
rm hermes-agent-main/plugins/takyon/control_plane.py
rm hermes-agent-main/tests/plugins/conftest.py
rm hermes-agent-main/tests/plugins/test_takyon_control_plane_pg.py
# restore the identity-spine test to its self-contained fixture form, or just
# remove it too (Increment 2 revert):
rm hermes-agent-main/tests/plugins/test_takyon_identity_spine_pg.py
```
Nothing imports `control_plane.py` from a runtime path yet; removal is
self-contained.

---

## Increment 4 — Control API read path (opaque boundary) + JIT provisioning core

**Date:** 2026-05-30

**What:** Built the Phase-1 read path of the Control API — the opaque per-user HTTP
boundary — and routed it through the resolver, exactly as the plan's "Phase 1 STEP
1" acceptance requires: a request resolves to exactly one user + their businesses
before any privileged work; revoked/unknown keys are rejected; one tenant cannot
read another's businesses. Also added the first-login JIT provisioning core
(`provision_user_on_first_login`).

**Why:** This is the Phase-1 acceptance gate from `mediationplan.md` ("route `GET
/v1/me` + `/v1/businesses` through it") and the whole point of the identity spine:
prove the opaque boundary on a *real request path* (FastAPI + resolver + Postgres),
not in the abstract. Built additively and standalone — it does **not** touch or
re-wire the live SQLite dashboard, so it commits nothing to Postgres ahead of the
deferred Supabase cutover.

**Redundancy finding (Auth0, per CLAUDE.md anti-duplication):** task "Auth0 token
validation" is NOT net-new. The canonical runtime already validates Auth0 tokens in
`takyon_cli/web_server.py` — `_auth0_verify_id_token` fetches JWKS via PyJWT
`PyJWKClient` and requires `exp/iat/iss/aud/sub`, with `/auth/login`,
`/auth/callback`, `/auth/me` routes wired. So this increment did **not** rebuild
Auth0 validation. It added only the missing piece — the JIT provisioning the plan
calls for on first login — and left wiring it into `/auth/callback` as a deferred,
prod-touching step (lands with the SQLite→Postgres cutover).

**Files created:**
- `hermes-agent-main/plugins/takyon/control_api.py` — FastAPI `build_control_router()`
  (`/v1` prefix) with bearer-token dependency `_resolve_principal` (→ one
  undifferentiated 401 for missing/malformed/unknown/revoked/non-active, never
  revealing which) and three read endpoints: `GET /v1/me` (identity projection
  only — billing balance/allowance deferred, NOT fabricated), `GET /v1/businesses`
  (owner-scoped list), `GET /v1/businesses/{slug}` (owner-scoped detail).
  DB-agnostic via the overridable `get_control_conn` dependency (tests inject the
  throwaway-DB connection; production injects a pool).
- `hermes-agent-main/tests/plugins/test_takyon_control_api_pg.py` — 10 integration
  tests over the REAL FastAPI request path (TestClient) against Postgres:
  missing/garbage/unknown/revoked → 401; `/v1/me` returns the resolved identity;
  `/v1/businesses` lists only owned (a second tenant's business does not leak);
  cross-tenant `/v1/businesses/{slug}` → 404; JIT idempotent + mints exactly once;
  the read path stamps `last_used_at` (proves the resolver actually ran).

**Files modified:**
- `hermes-agent-main/plugins/takyon/control_plane.py` — added
  `provision_user_on_first_login(conn, auth0_sub, email)`: one transaction that
  `get_or_create_user` then mints the first key ONLY when newly created; returns
  `(user_id, created, raw_key)` with `raw_key` non-None exactly once. Idempotent and
  race-safe. (Phase-1 scope: billing/custody accounts join once those migrations
  land.)

**Deliberate deviation from the plan's wording:** the plan lists `403 not_owner`
for businesses, but `/v1/businesses/{slug}` returns **404** for a slug the caller
doesn't own. Returning 403 ("exists but not yours") would make the endpoint a
cross-tenant existence oracle; "other tenants are unreachable / opaque by
construction" (Robustness Contract) outranks the looser 403 wording. Documented at
the call site.

**Verification:**
- With Postgres: `TAKYON_TEST_PG_DSN=postgresql://postgres@127.0.0.1:54329/takyon_test
  .venv/bin/python -m pytest tests/plugins/test_takyon_control_api_pg.py -q`
  → **10 passed**. Full P1 stack (unit + identity + control-plane + control-api)
  → **38 passed**.
- Without the DSN: full P1 stack → **13 passed, 25 skipped** (every integration
  test is a clean no-op).
- Whole-directory CI-parity sweep (`scripts/run_tests.sh tests/plugins/ -q`) →
  **2 failed, 831 passed, 25 skipped**. The 2 failures
  (`test_all_seven_plugins_present_in_registry`,
  `test_business_work_focus_persists_and_blocks_cross_lane_writes`) are the SAME
  pre-existing failures from the repo's mid-refactor state (tracked `core.py`
  work_focus/lane/outreach edits — now prefixing outreach paths with
  `distribution/` — and a `registry.py` `xai` entry), unrelated to this purely
  additive identity work. Pass count unchanged at 831; skip count rose 15→25 (the
  10 new control-API tests skip without a DSN). Zero new failures introduced.

**Revert:**
```sh
rm hermes-agent-main/plugins/takyon/control_api.py
rm hermes-agent-main/tests/plugins/test_takyon_control_api_pg.py
# and drop the JIT helper added to control_plane.py:
#   remove provision_user_on_first_login(...) from
#   hermes-agent-main/plugins/takyon/control_plane.py
```
`control_api.py` is not mounted into any live app (no `include_router` call exists
in `web_server.py` yet), so removal is self-contained.

**Refinement (same increment):** `resolve_api_key`'s `last_used_at` stamp was made
**throttled** — it now only writes when the column is null or older than 60s
(`... where id = %s and (last_used_at is null or last_used_at < now() - interval
'60 seconds')`). Reason: the stamp ran on every authenticated read; under one hot
key that serialized every request on the row write-lock and inflated WAL. The
column is unindexed so the write was already a cheap HOT update, but throttling
collapses a hot key to ~1 write/min with no behavior cost (a coarse last_used_at is
fine). Verified: control-plane + control-API suites → **19 passed**. Revert = drop
the `and (...)` clause to restore the unconditional stamp.

---

## Increment — Governance rules added to mediationplan.md (inspect-before-build + surface-credential-needs); Ground Truth corrected

**Why:** Phases 1–2 were first cut greenfield against the SQLite trunk; only afterward
did the survey show the target Supabase (`DATABASE_URL`) still hosts polsia2's
*populated* control schema — `public.businesses` (~32 rows) and `public.billing_accounts`
(~15 rows, Stripe-subscription shape) already exist and would have been silently shadowed
by the new `create table public.<name>` migrations. Operator directive: encode a standing
rule that every remaining phase must FIRST check what already exists (repo + backend), and
a rule that any new secret/API-key/provider need is surfaced to the operator, never
bandaided around.

**Changed (docs only — no code, no schema, no DB connection):**
- `mediationplan.md` → **Ground Truth** corrected: retracted the false "no real data to
  migrate / trivial backfill" and "polsia2 … not a live dependency" claims. Recorded that
  the Supabase is NOT greenfield (populated polsia2 schema, profile-based identity), named
  the real collisions (`businesses`, `billing_accounts`), and listed the confirmed-FREE
  names (`users`, `user_api_keys`, `billing_entries`, `custody_accounts`, `custody_entries`).
- `mediationplan.md` → new **Build Discipline (applies to every phase)** section, two gates:
  Gate 1 inspect-what-exists (repo `TakyonStore`/migrations + backend `information_schema`)
  → decide **replace / extend / isolate**, never a parallel `public.<name>`; Gate 2 surface
  every new credential/provider need (record env var, provider, side effect, test/live gate)
  — never stub/fake/bandaid; missing-credential path is `blocked`, not fake-`completed`.
  Recorded "Outstanding new-credential needs: none for Phases 0–2."
- `mediationplan.md` → **Phased Rollout** prefaced: every phase begins by running both gates.

**Not done / honest state:** No live DB connection this turn (operator declined a read-only
introspection). The row counts above come from this session's earlier survey and are marked
approximate in the plan, pending a re-verified table-by-table inventory before any Phase 1–2
DDL. Phase 2 ledger tests remain RED and are intentionally NOT the next step — the gating
work is the replace/extend/isolate decision for the colliding tables (Gate 1), which must
precede reworking 0001/0002.

**Verification:** docs-only; no tests run/affected.

**Revert:**
```sh
git checkout -- mediationplan.md   # outer workspace repo; restores pre-rule version
# (this log entry is additive; trim it back to the previous increment if reverting)
```

---

## Increment — REPLACE decision recorded; Phase 2 ledger tests turned green (reconcile cast fix + per-test DB isolation)

**Why:** Two things resolved here. (1) The prior increment left the colliding-table
decision (Gate 1: replace / extend / isolate) open. The operator answered it on
2026-05-30 via AskUserQuestion → **REPLACE polsia2's `public` control tables**; that is
now recorded in `mediationplan.md` Ground Truth (line 11). (2) The Phase 2 ledger
integration tests were RED; diagnosis showed the **engines are correct** and the failures
were two non-engine defects — one query bug and one test-fixture isolation bug.

**Changed (code + docs):**
- `mediationplan.md` → Ground Truth gained the **REPLACE decision** bullet (2026-05-30,
  operator): takyon owns `public`; Phase 1–2 migrations deliberately drop/replace polsia2's
  overlapping objects (`businesses`, `billing_accounts`, and their FK-dependents) in
  dependency order; polsia2 live rows are **disposable**. Guardrail: design+verify the
  replace on **LOCAL Postgres first**; the live Supabase apply is a *separate* step gated on
  (a) a fresh Supabase backup/snapshot and (b) explicit operator go-ahead — `drop` is
  irreversible. Migrations stay idempotent/re-runnable. (Resolves the "Not done" gating item
  from the previous increment.)
- `plugins/takyon/billing.py` → `reconcile_billing`: fixed a psycopg3 **AmbiguousParameter**
  (`could not determine data type of parameter $1`). The named param `%(ps)s` (period start)
  appeared only inside `%(ps)s is null or created_at >= %(ps)s`, which gives the planner no
  inferable type. Added explicit `::timestamptz` casts on all three `filter (...)` clauses
  for the allowance reserve/settle/refund sums. No semantic change — same NULL-means-all-time
  behavior, now type-resolvable.
- `tests/plugins/conftest.py` → `pg_conn` fixture **`scope="module"` → function scope**.
  The ledger engines treat `billing_entries.idempotency_key` / `custody_entries.idempotency_key`
  as **globally UNIQUE** (a replayed key is one effect — correct). Module scope shared ONE DB
  across a file's tests, so tests reusing fixed literal keys (`"pay-1"`, `"r1"`, `"t"`) had the
  2nd+ test's op silently swallowed as a replay (e.g. `test_settle_*` saw `topup_balance == 0`
  because `topup(...,"t")` replayed an earlier test's `"t"`; `test_concurrent_accruals_*` got
  `15200 == 19×800` because one worker's `"pay-1"` was already consumed). Function scope gives
  every test a pristine uuid-named DB, so keys are independent. Per-worker name segment still
  prevents pytest-xdist collisions. Docstring updated to explain the why.

**Verification:** from `hermes-agent-main`, `TAKYON_TEST_PG_DSN=postgresql://postgres@127.0.0.1:54329/takyon_test`,
all five PG suites together (billing, custody, identity_spine, control_plane, control_api),
`-n 4` → **54 passed**. No regression in the three suites that share `pg_conn`.

**Not done / honest state:** No live DB this turn. The REPLACE is designed/verified on LOCAL
Postgres only; migrations 0001/0002 still need the explicit polsia2-retirement drop step
(dependency-ordered, since `businesses` roots the polsia2 schema) authored + local-tested
before any live apply. Live Supabase apply remains blocked on backup + explicit operator
go-ahead. Task #6 (JIT into Auth0 `/auth/callback`) still deferred to the Supabase cutover.

**Revert:**
```sh
git checkout -- hermes-agent-main/tests/plugins/conftest.py  # restores module scope (re-breaks ledger isolation)
git checkout -- mediationplan.md                             # drops the REPLACE decision bullet
# billing.py is untracked; to undo only the cast, remove the three `::timestamptz` casts
#   in reconcile_billing's allowance reserve/settle/refund `filter (...)` clauses.
```

---

## Increment — polsia2 REPLACE cutover: fail-loud forward guards + separate gated teardown, verified on local PG

**Why:** The previous increment closed the REPLACE *decision* but left the *mechanism* as
the next step. Reading the migrations confirmed the concrete trap: BOTH 0001 (`businesses`)
and 0002 (`billing_accounts`) use `create table if not exists` — on the live Supabase, where
polsia2's differently-shaped versions already exist, `if not exists` would silently bind
takyon to polsia2's incompatible table (or fail later with a cryptic FK/index error). The
destructive drop, meanwhile, must NOT ride along in the idempotent forward set the test
conftest sweeps. So the cutover is split into a loud guard (in the forward migrations) plus a
separate, gated teardown — designed and verified entirely on local PG, with the live apply
still gated on backup + operator go-ahead.

**Changed (code + tests + docs):**
- `db/migrations/0001_identity_spine.sql` → fail-fast **REPLACE guard** at the very top
  (before any object is created): if `public.businesses` exists but lacks `owner_user_id`,
  `raise exception … 'not the takyon shape … run retire_polsia2_public.sql first'`
  (errcode `feature_not_supported`). Trivial pass on a clean DB / re-run. Header's stale
  "Greenfield: no backfill" line rewritten to the REPLACE reality.
- `db/migrations/0002_ledgers.sql` → twin guard for `public.billing_accounts` (signature
  column `allowance_included_cents`). Header's "(greenfield … no backfill)" line corrected.
- `plugins/takyon/db/retire_polsia2_public.sql` → **NEW**, deliberately OUTSIDE `db/migrations/`
  so neither the test sweep nor a forward-migrate ever runs it. Idempotent named drops of the
  two takyon-colliding roots — `billing_accounts` then `businesses` — each `drop … cascade`
  (clears dependents' FK constraints, not their tables). Each drop is guarded by the INVERSE
  of the migration guards: it fires ONLY when a table of that name exists AND is not already
  the takyon shape, so on a clean DB it is a no-op and AFTER takyon owns the table it is ALSO a
  no-op — a re-run can never destroy takyon data. Heavy header: DESTRUCTIVE, run once on live
  only after backup + explicit go-ahead; documents that a FULL `public` wipe (profiles,
  agent_runs, orphaned dependents) is a separate gated step needing a live inventory + Supabase
  role/grant review.
- `tests/plugins/conftest.py` → factored the throwaway-DB lifecycle into `_throwaway_db(worker_id)`
  + `_apply_migrations(conn)`; `pg_conn` now composes them (unchanged semantics). Added
  **`pg_conn_raw`** (fresh DB, NO migrations) for tests that apply migration SQL by hand, and
  exported `RETIRE_POLSIA2_SQL`. No behavior change to existing PG tests.
- `tests/plugins/test_takyon_retire_polsia2_pg.py` → **NEW**, 5 tests proving the four cutover
  properties on real PG: (1) 0001/0002 RAISE on a simulated polsia2 shadow (and create nothing);
  (2) retire→migrate drops the colliders, leaves the dependent table (only its FK cascaded),
  and yields the takyon shape with a working user→business→billing_entry insert chain;
  (3) teardown is a no-op on a clean DB; (4) re-running teardown on a takyon-owned DB preserves
  all takyon rows.
- `mediationplan.md` → Ground Truth gained a **"Cutover mechanics (built + locally verified)"**
  bullet: the guard+teardown design, the live cutover ORDER (`retire_polsia2_public.sql` → 0001
  → 0002 → …), the local verification, and the still-gated full-`public` retirement (records it
  as an *authorization-to-connect* gap, not a new-credential need).

**Verification:** from `hermes-agent-main`, `TAKYON_TEST_PG_DSN=…@127.0.0.1:54329/takyon_test`,
all six PG control-plane suites together, `-n 4` → **59 passed** (54 prior + 5 new). The broader
`tests/plugins/` run shows 2 failures (`test_business_work_focus_…` expecting `outreach/test.md`
vs current `distribution/outreach/test.md`; web-search registry expecting 7 providers, `xai`
makes 8) — **confirmed pre-existing**: both fail identically with this turn's tracked edits
stashed, and neither touches the control plane, migrations, ledgers, or the PG fixtures.

**Not done / honest state:** No live DB this turn — the REPLACE is verified on LOCAL PG only.
Live apply (retire → 0001 → 0002) remains blocked on a fresh Supabase backup + explicit operator
go-ahead. Full polsia2 `public` retirement (beyond the two colliding roots) is a separate gated
step needing a live inventory + Supabase grant review. Task #6 (JIT into Auth0 `/auth/callback`)
still deferred to the Supabase cutover.

**Revert:**
```sh
git checkout -- hermes-agent-main/tests/plugins/conftest.py   # restores pre-refactor fixture
git checkout -- mediationplan.md                              # drops the cutover-mechanics bullet
rm hermes-agent-main/plugins/takyon/db/retire_polsia2_public.sql
rm hermes-agent-main/tests/plugins/test_takyon_retire_polsia2_pg.py
# 0001/0002 guards are additive DO-blocks at the top of each file (untracked); delete the
#   `do $$ begin if to_regclass(...) ... end $$;` block to remove a guard.
```

---

## Increment — Phase 3: Control API topup checkout + webhook (flow A) + per-user rate limiting

**Date:** 2026-05-30

**What:** Built the three remaining Phase-3 pieces on top of the Phase-1 opaque-key
read boundary: (1) a self-contained control-plane Stripe helper module; (2) the
flow-A **topup** path — `POST /v1/billing/topup/checkout` to create a Stripe Checkout
session that tops up the *caller's own* balance, and a dedicated `POST /v1/billing/webhook`
that credits the billing ledger when that payment completes; (3) a Postgres-backed
**per-user fixed-window rate limiter** gating the authenticated endpoints.

**Why:** The opaque API key is the entire per-user surface, so abuse control and
money-in both live at the user grain. Flow A (user→platform) is genuinely new vs. the
SQLite trunk's product/sub-user webhook (flow B), as the Gate-1 finding in
`mediationplan.md` established. The ledger primitive `billing.topup(...)` already
existed (idempotent on the Stripe event id); this increment is the HTTP + Stripe
plumbing that drives it, plus the rate limiter the plan called for (line 100: "Postgres
fixed-window now; Upstash Redis at scale").

**Decisions (resolve the plan's open questions):**
- **Webhook topology — SEPARATE control-plane endpoint** (not the shared SQLite product
  dispatcher), so flow A carries its OWN per-endpoint signing secret
  `STRIPE_BILLING_WEBHOOK_SECRET` and stays cleanly isolated from the product webhook.
  This resolves the "open decision" flagged in the Phase-3 gate finding.
- **Caller supplies `success_url`/`cancel_url`** on the checkout body, mirroring the
  existing trunk convention (`handle_business_create_app_checkout`) — the server never
  invents or open-redirects to a base URL it picked.
- **Stripe helpers are a second, self-contained copy** (`stripe_util.py`), NOT an import
  of core.py's: core's helpers sit inside the large SQLite trunk, raise `TakyonError`,
  and call `load_takyon_env()`; importing them would couple the Postgres control plane to
  that trunk and risk an import cycle. The wire format is byte-for-byte identical.
- **Rate limiter is one atomic SQL upsert** (fixed window, not a token bucket): the
  increment-and-return is a single statement, so concurrent requests for one user cannot
  race past the cap. The window is epoch-aligned in the DATABASE, so every stateless
  worker agrees on "which window now is" without a shared clock — the reason it must be
  PG-backed (workers are stateless), exactly as the plan noted (no credential needed).

**Files created:**
- `hermes-agent-main/plugins/takyon/stripe_util.py` — `stripe_request` (form-encoded REST
  POST, drops None params, `Bearer` auth, raises `StripeError` if `STRIPE_SECRET_KEY`
  absent — never fakes), `verify_stripe_signature` (`t=…,v1=…` HMAC-SHA256 over
  `"{ts}.{body}"`, 300s tolerance, `hmac.compare_digest`), `build_signature_header`
  (test/local signing only). Pure stdlib; reads config from `os.environ` like custody.py.
- `hermes-agent-main/plugins/takyon/rate_limit.py` — `RateLimitResult` dataclass +
  `check_rate_limit(conn, user_id, *, limit, window_seconds)` (atomic upsert-increment,
  429-shaped result with `retry_after_seconds`) + `prune_rate_limits(conn, *,
  older_than_seconds)`. Pure (no psycopg import), opens its own `conn.transaction()`.
- `hermes-agent-main/plugins/takyon/db/migrations/0003_rate_limits.sql` — `api_rate_limits`
  table (PK `(user_id, window_start)` → that key is both the lock and the dedupe;
  FK→users on delete cascade; index on `window_start` for prune). Net-new, so plain
  create-if-not-exists — but carries the same fail-loud REPLACE guard pattern as 0001/0002
  (fires only if a non-takyon `api_rate_limits` lacking `window_start` is ever present).
- `hermes-agent-main/tests/plugins/test_takyon_stripe_util.py` — **13** hermetic unit tests
  (no network/psycopg/live keys; signature tests round-trip via `build_signature_header`).
- `hermes-agent-main/tests/plugins/test_takyon_rate_limit_pg.py` — **7** PG integration tests
  (first request allowed; allows up to limit then blocks; users independent; an old window's
  count never bleeds into the current one; window rolls over with wall-clock time; rejects
  non-positive limit/window; prune removes only expired windows).

**Files changed:**
- `hermes-agent-main/plugins/takyon/control_api.py` — added the `TopupCheckoutRequest`
  model and the two billing endpoints; added `_positive_int_env`, `_rate_limit_config`
  (env `TAKYON_CONTROL_RATE_LIMIT` default 120, `TAKYON_CONTROL_RATE_WINDOW_SECONDS`
  default 60 — non-secret config with safe defaults), and a `_rate_limited_principal`
  dependency (429 + `Retry-After` over the cap) now applied to `/me`, `/businesses`,
  `/businesses/{slug}`, and the topup checkout. The webhook is deliberately EXEMPT
  (signature-authenticated, no bearer principal, Stripe retries must not be throttled).
- `hermes-agent-main/tests/plugins/test_takyon_control_api_pg.py` — added the flow-A topup
  tests (checkout requires bearer / rejects non-positive amount / blocked-without-key 503 /
  returns url + tags user; webhook blocked-without-secret 503 / bad-signature 400 / credits
  + idempotent replay / ignores non-topup / ignores unpaid) and 2 rate-limit tests
  (429 after cap with Retry-After; limit is per-user). Now **21** tests in this file.

**Verification:** from `hermes-agent-main`, `TAKYON_TEST_PG_DSN=…@127.0.0.1:54329/takyon_test`,
via `scripts/run_tests.sh` (`-n 4`, hermetic): the three Phase-3 suites together →
**41 passed** (13 stripe_util + 21 control_api + 7 rate_limit). Full `tests/plugins/` →
**920 passed, 3 failed** — all 3 are **confirmed pre-existing change-detectors** untouched by
this work: `test_business_work_focus_…` (path drift), web-search registry (expects 7 providers,
`xai` makes 8), and `test_bundled_takyon_skills_exist` (hardcoded skill-set assertion that omits
the already-present `takyon-meta-ads` skill). None touch the control plane, ledgers, migrations,
or PG fixtures; my additions are confined to `plugins/takyon/{stripe_util,rate_limit,control_api}`
and `tests/plugins/`.

**Not done / honest state:**
- The Control API router is still **NOT mounted** into the live dashboard app — `get_control_conn`
  is an unconfigured dependency seam the host overrides (tests override it with the throwaway-DB
  `pg_conn`). Mounting it is a separate, deliberate step.
- The webhook handler is `async` but calls the synchronous psycopg `billing.topup` inline.
  This is fine pre-mount; the **production connection-execution strategy** (sync-in-threadpool
  vs. an async pool) is **deliberately deferred** until the router is mounted and the prod
  connection provider is chosen.
- **`STRIPE_BILLING_WEBHOOK_SECRET` is still required for LIVE topup crediting** and remains
  unprovisioned (operator action — recorded in `mediationplan.md` Gate 2). Absent → the webhook
  returns 503 `billing_webhook_unconfigured` (Stripe retries); a missing `STRIPE_SECRET_KEY` →
  checkout returns 503 `topup_unconfigured`. Neither path ever fakes a credit or a URL
  (invariant #8). The rate-limit knobs are non-secret env config with safe defaults — no new
  credential.

**Revert:**
```sh
git checkout -- hermes-agent-main/plugins/takyon/control_api.py          # back to read-path only
git checkout -- hermes-agent-main/tests/plugins/test_takyon_control_api_pg.py
rm hermes-agent-main/plugins/takyon/stripe_util.py
rm hermes-agent-main/plugins/takyon/rate_limit.py
rm hermes-agent-main/plugins/takyon/db/migrations/0003_rate_limits.sql
rm hermes-agent-main/tests/plugins/test_takyon_stripe_util.py
rm hermes-agent-main/tests/plugins/test_takyon_rate_limit_pg.py
```

## Increment — Phase 4: Execution-policy engine (route the CEO's own compute under flow-A budget pressure)

**Date:** 2026-05-30

**What:** Built the Phase-4 execution-policy engine: a per-business `app_execution_policies`
table plus a pure leaf module `policy.py` whose `decide_execution(...)` recommends how a
unit of the CEO's *own* work should run — **inline**, downgraded to a **cheaper** model
tier, pushed to a background **job**, or **blocked** with a precise reason — from the
business's routing knobs, the owner's flow-A balances, and a caller-supplied cost estimate.

**Why:** The plan's Phase-4 acceptance is that features *degrade gracefully under budget
pressure instead of hard-failing* (mediationplan.md line 226). The two ledgers (Phase 2)
can already say "no money" by raising; Phase 4 adds the judgment *above* that gate — pick a
cheaper tier, defer to a job, or block cleanly — so a budget-constrained business keeps
running at reduced capability rather than erroring out.

**Gate-1 finding (extend / replace / isolate → ISOLATE, net-new):** the SQLite trunk
already has a per-business budget (`app_budgets` core.py:3026 + `app_plan_policies`
core.py:3036, enforced in the product `/generate` path app_api.py:379). **That is the
PRODUCT / sub-user budget — how much a business spends serving ITS customers, in
microUSD.** Phase-4's `app_execution_policies` governs a *different* thing: the per-business
ROUTING knobs for the CEO's own compute against the USER's flow-A budget (cents), plus an
OPTIONAL monthly sub-cap. So it is **ADD-on-top, not a replacement** — `app_budgets` is
ported as-is in Phase 5. New table + new module, distinct from both the ledgers and the
product budget. Full finding recorded in mediationplan.md ("Phase 4 gate finding").

**Gate-2 finding (credentials):** none. The engine moves no money and calls no provider.
One OPTIONAL non-secret tuning knob `TAKYON_EXECUTION_EXPENSIVE_THRESHOLD_CENTS` (default
100 = $1.00, clamps to ≥ 0). Recorded in mediationplan.md Gate 2.

**Decisions:**
- **ADVISORY, not a second money gate.** `decide_execution` only *reads* — it never reserves
  or settles. `billing.reserve` (the FOR UPDATE row lock) stays the one atomic money gate.
  A decision moving no money is an explicit, test-proven invariant
  (`test_decision_moves_no_money_and_inserts_no_policy`). This avoids a parallel spend path.
- **Owner resolved from the business, not passed in.** `decide_execution` looks up
  `businesses.owner_user_id` from `business_slug` (single source of truth) so a caller can't
  pass a user/business mismatch; an unknown business raises `NoBusiness` rather than guessing.
- **Per-business monthly sub-cap nets via the reservation_key set, NOT a business_slug filter.**
  billing's `settle`/`refund` write entries with `business_slug=NULL` (only `reserve` is
  tagged), so `Σreserve − Σrefund WHERE business_slug=X` would *miss every refund* and
  overcount. `_business_period_spend_cents` instead sums `Σreserve − Σrefund` over the set of
  reservation_keys the business reserved this period → outstanding holds + settled actuals.
  Pinned by three tests (cap-exhaust, refund-restores-headroom, settled-actual-counts).
- **Inline-vs-job is tier-independent.** The runtime/output ceilings describe the *work*, so
  the inline→job→blocked check applies to whichever tier was chosen (requested or a
  downgrade); `detail['downgraded']` records the downgrade either way.
- **Absent policy row → conservative documented defaults, never an auto-insert** (reading a
  policy is a pure read); defaults mirror the DDL exactly.
- **Bad inputs / broken preconditions raise; only budget/policy outcomes are decisions.**
  Negative estimate, unknown business, or missing billing account raise — they are NOT
  laundered into a budget `blocked` (which would mask a provisioning bug as "out of money").

**Files created:**
- `hermes-agent-main/plugins/takyon/db/migrations/0004_execution_policies.sql` —
  `app_execution_policies` (PK `business_slug` → FK businesses on delete cascade; CHECKs keep a
  misconfigured row from inverting a decision; `monthly_app_budget_cents` nullable = no sub-cap).
  Net-new, plain create-if-not-exists, carrying the same fail-loud REPLACE guard as 0001–0003
  (fires only if a non-takyon `app_execution_policies` lacking `preferred_model_tier` exists).
- `hermes-agent-main/plugins/takyon/policy.py` — pure leaf (takes a psycopg conn, imports no
  psycopg, reads config from `os.environ`; imports `billing` for balance reads only → no cycle):
  `ExecutionPolicy`/`PolicyDecision` dataclasses; `get_execution_policy` (defaults on miss, no
  insert); `upsert_execution_policy` (read-merge-write under a row lock, preserves unspecified
  fields, FK makes an unknown business fail loud); `decide_execution` (the four-outcome engine);
  `expensive_threshold_cents` (env knob, clamps like custody's `app_fee_bps`);
  `_business_period_spend_cents` (the reservation_key netting).
- `hermes-agent-main/tests/plugins/test_takyon_policy_pg.py` — **19** tests: env-knob clamp/default;
  policy storage (defaults-without-insert, partial-update preservation, unknown-field + bad-value
  rejection, unknown-business FK fail-loud); all four outcomes (inline, cheaper-downgrade-to-closest
  -affordable, job-on-runtime-overflow, blocked-insufficient); escalation-disabled → blocked;
  expensive-branch-disallowed → blocked; zero-estimate always inline; per-business cap blocks before
  flow-A; refund restores cap headroom; settled actual (not the reservation) counts toward the cap;
  unknown-business + negative-estimate raise; advisory no-write invariant.

**Files changed:** none — purely additive (new migration + new module + new test file). No
existing source touched, so nothing in Phases 1–3 can regress from this increment.

**Verification:** from `hermes-agent-main`, `TAKYON_TEST_PG_DSN=…@127.0.0.1:54329/takyon_test`,
via `scripts/run_tests.sh` (`-n 4`, hermetic): the Phase-4 suite → **19 passed**. Full
`tests/plugins/` → **945 passed, 2 failed** — both failures are **confirmed pre-existing
change-detectors**, a subset of the three the Phase-3 increment already documented:
`test_business_work_focus_…` (in `test_takyon_plugin.py`, which carries unrelated uncommitted
edits) and the web-search registry test (expects 7 providers; `xai`, committed in `63cd4b5`,
makes 8). The third Phase-3 failure (the skill-set assertion) now passes. Neither failure
imports `policy.py` or touches the PG migrations; my additions are confined to the three new
files above.

**Not done / honest state:**
- The engine is **NOT mounted / not called by anything live.** It is a standalone module like
  billing/custody/rate_limit. The actual call site — the internal **AI gateway** that resolves
  business → policy → `reserve` → `settle` (mediationplan.md line 116) — is **Phase 5** work.
- The **'job' outcome only recommends deferral**; there is no queue yet. The worker plane that
  drains jobs is **Phase 6**; until then a 'job' decision is advice the (future) caller acts on.
- `decide_execution` reads balances + period spend in separate statements (no enclosing
  transaction) — fine because it is advisory and `reserve` re-checks atomically; if a future
  caller needs a consistent snapshot it can wrap the call, but the money truth is always `reserve`.

**Revert:**
```sh
rm hermes-agent-main/plugins/takyon/policy.py
rm hermes-agent-main/plugins/takyon/db/migrations/0004_execution_policies.sql
rm hermes-agent-main/tests/plugins/test_takyon_policy_pg.py
# and revert the two mediationplan.md additions (Phase 4 Gate-2 entry + "Phase 4 gate finding"
#   paragraph); this log entry is additive — trim it back to the Phase-3 increment if reverting.
```

## Increment — Phase 5a: Product sub-user identity / magic-link auth / sessions (Postgres port)

**Date:** 2026-05-30

**What:** Ported the first slice of the SQLite product runtime to Postgres — the *product
sub-user* identity substrate (a business's CUSTOMERS, not the top-level Takyon operator):
migration `0005_app_identity.sql` (`app_users` / `app_magic_links` / `app_sessions`, all
FK'd to `businesses(slug)` and business-scoped) plus a pure leaf module `app_identity.py`
that mints magic links, redeems them for 30-day bearer sessions, and validates/revokes
those sessions. Magic-link-only auth; opaque tokens are SHA-256-hashed, never stored in
clear. This is increment **(a)** of Phase 5 — identity/auth/session only; entitlements,
checkout/webhook/revenue, usage/budget, owner→custody accrual, and the gateway-key boundary
are increments **b–e**.

**Why:** Phase-5 acceptance opens with *"all apps share rails"* (mediationplan.md line 233),
and the first shared rail every product needs is *who is this customer and is their session
valid*. The SQLite product path that owns this today (`core.py` app_users/app_magic_links/
app_sessions) is explicitly on death row — Phase 8 kills SQLite — so this is its **successor
authority on Postgres**, not a second parallel system (the PORT decision recorded in
mediationplan.md "Phase 5 gate finding"). Token hashing matches the SQLite `_hash_token`
byte-for-byte so a ported app's existing links/sessions keep verifying.

**Gate-1 finding (PORT, per the recorded Phase 5 finding):** the SQLite trunk's product
runtime is 11 owner-agnostic tables keyed by `business_slug` (core.py:3026-3243). This
increment ports the **identity three** (`app_users` UNIQUE(business_slug,email),
`app_magic_links` token_hash UNIQUE 15-min, `app_sessions` token_hash UNIQUE 30-day),
carrying the same fail-loud REPLACE-guard pattern as 0001–0004 (`app_users` is net-new to
Postgres, so the guard fires only if a differently-shaped non-takyon `app_users` lacking
`business_slug` already exists — the migration's anchor table, since links + sessions FK to
it). The remaining eight tables (entitlements, checkout intents/sessions, revenue, usage,
budgets, plan policies, webhook_events) land in increments b–e.

**Gate-2 finding (credentials):** none. The identity leaf mints and stores; it calls no
provider and moves no money. Email DELIVERY (and its `provider_message_id`) is a side effect
owned by the layer above — recorded in mediationplan.md "Phase 5 — no NEW external
credential."

**Decisions:**
- **Pure leaf owns the guarded STATE change; email DELIVERY is layered above** — exactly as
  Phase 3 split `billing.topup` (ledger state) from the Stripe call. The leaf mints the link
  and stores only its hash; `app_magic_links.provider_message_id` is left NULL by the leaf and
  populated by the (future) send layer, which also decides live-send vs. test-mode
  suppression. No email is sent from this module.
- **Single-use becomes ATOMIC, closing the SQLite TOCTOU.** The SQLite original read the link,
  checked `used_at IS NULL` in Python, then wrote — two simultaneous clicks could both pass the
  read and double-redeem. The port makes redemption one statement:
  `UPDATE app_magic_links SET used_at = now() WHERE business_slug=%s AND token_hash=%s AND
  used_at IS NULL AND expires_at > now() RETURNING app_user_id`. Under READ COMMITTED exactly
  one concurrent caller's UPDATE matches; everyone else sees `None` → `InvalidMagicLink`. Pinned
  by a 20-thread concurrency test (own connections, a `threading.Barrier` to maximize overlap):
  **exactly 1 "ok" + 19 "rejected", and exactly 1 `app_sessions` row** — no errors, no second
  redemption.
- **verify is atomic end-to-end.** The redemption, the active-status check, and the session
  insert all run inside one `conn.transaction()`. If the resolved sub-user is suspended/closed,
  `InactiveAppUser` is raised and the whole transaction ROLLS BACK — so the `used_at` stamp is
  undone and **the link survives** for a later (reactivated) attempt. Test-proven
  (`test_verify_inactive_user_rolls_back_so_link_survives`: suspend → InactiveAppUser → reactivate
  → the same raw token still redeems).
- **Everything is business-scoped in the WHERE clause, not just by convention.** A session token
  minted under business A returns `None` from `validate_session(conn, B, token)` — a token never
  crosses the business boundary even if the raw value were known. Test-proven
  (`test_session_is_business_scoped`).
- **citext + uuid PKs.** `email` is `citext` so `(business_slug, email)` uniqueness and all
  lookups are case-insensitive without the SQLite `lower()` dance; case variants collapse to one
  sub-user (test-proven). PKs are `uuid default gen_random_uuid()`.
- **Raw tokens are never persisted.** Only `sha256(raw)` hex is stored; a test asserts both
  `stored == hashlib.sha256(raw.encode()).hexdigest()` **and** `stored != raw`.
- **Typed errors on broken preconditions, sentinels only where "absent" is a normal answer.**
  `upsert`/`create`/`verify` raise (`InvalidEmail`, `InvalidMagicLink`, `InactiveAppUser`,
  `ValueError` on non-positive TTL); an unknown business fails loud through the FK
  (`ForeignKeyViolation`). `get_app_user`/`validate_session` return `None` for missing/garbage,
  and `revoke_session` returns a `bool` (idempotent: True once, False thereafter) — reads and
  revokes tolerate empty/garbage tokens without raising.
- **upsert is idempotent on (business_slug, email)** — a re-request reactivates a suspended row
  and keeps the existing name unless a new one is supplied (`coalesce(excluded.name,
  app_users.name)`); it never creates a second row for the same person in the same business, but
  the same email IS a distinct customer across two businesses (both test-proven).

**Files created:**
- `hermes-agent-main/plugins/takyon/db/migrations/0005_app_identity.sql` — REPLACE guard on
  `app_users`/`business_slug`; `create extension if not exists citext`; the three tables
  (statuses CHECK-constrained to active/suspended/closed; `tier` default 'free', business-defined;
  `provider_message_id` nullable for the email layer) + `app_magic_links_user_idx` /
  `app_sessions_user_idx`. Idempotent DDL.
- `hermes-agent-main/plugins/takyon/app_identity.py` — pure leaf (takes a psycopg conn, imports
  no psycopg, opens its own `conn.transaction()` per mutating op): `AppUser`/`MagicLink`/
  `AppSession` dataclasses; `upsert_app_user`, `get_app_user`, `create_magic_link` (mint only),
  `verify_magic_link` (atomic redeem → session), `validate_session`, `revoke_session`; helpers
  `_hash_token` (matches SQLite), `_random_token` (`secrets.token_urlsafe(32)`),
  `_normalize_email`.
- `hermes-agent-main/tests/plugins/test_takyon_app_identity_pg.py` — **19** tests on real
  Postgres (never mocks): upsert create/idempotent-reactivate/normalize/bad-email/unknown-business
  -fail-loud/distinct-per-business; create-link provisions-user + stores-only-the-hash /
  rejects-nonpositive-ttl; verify opens-validating-session / single-use / expired / unknown+empty /
  inactive-rolls-back-so-link-survives / **concurrent-redeems-exactly-once** (20 threads);
  validate rejects revoked + expired, is business-scoped; revoke idempotent; validate+revoke
  tolerate garbage.

**Files changed:** none — purely additive (new migration + new module + new test file). No
existing source touched, so Phases 1–4 cannot regress from this increment.

**Verification:** from `hermes-agent-main`, `TAKYON_TEST_PG_DSN=…@127.0.0.1:54329/takyon_test`,
via `scripts/run_tests.sh` (`-n 4`, hermetic): the Phase-5a suite → **19 passed**. Full
`tests/plugins/` → **964 passed, 2 failed** (= Phase 4's 945 + the 19 net-new tests). The 2
failures are the **same confirmed pre-existing change-detectors** the Phase-4 increment already
documented: `test_business_work_focus_…` (in `test_takyon_plugin.py`, which carries unrelated
uncommitted edits) and the web-search registry test (expects 7 providers; the committed `xai`
makes 8). Neither imports `app_identity`, the 0005 migration, or the PG fixtures; my additions
are confined to the three new files above. (Note: the *whole-repo* suite is far larger — ~24k
tests with ~115 unrelated pre-existing failures in google-oauth / mcp-sse / skills areas — so
regression is measured against `tests/plugins/`, as in every prior phase.)

**Not done / honest state:**
- **Identity slice only.** Entitlements + plan policies (5b), usage + the collapsed
  reserve-then-settle budget gate (5c), checkout + webhook + revenue + the NET-NEW owner→custody
  accrual (5d), and the project gateway-key boundary for `/generate` (5e) are the remaining
  Phase-5 increments. The eight other product tables are not yet ported.
- **NOT mounted into any live HTTP surface.** `app_identity.py` is a standalone module like
  billing/custody/policy. The product HTTP surface (`/api/takyon/apps/<business>/…` verify /
  session / account, currently SQLite-backed in `app_api.py`) is re-pointed at this leaf in a
  later increment; nothing live calls it yet.
- **Email is not sent here.** The leaf returns the raw token to its caller exactly once and
  stores only the hash; the send layer (live provider vs. test-mode suppression, recording
  `provider_message_id`) is owned above and is not built in this increment.
- **Live Supabase apply remains blocked** on the polsia2 teardown + a backup + explicit operator
  go-ahead (unchanged from earlier phases). 0005 has only been applied to the local throwaway
  test DBs.

**Revert:**
```sh
rm hermes-agent-main/plugins/takyon/app_identity.py
rm hermes-agent-main/plugins/takyon/db/migrations/0005_app_identity.sql
rm hermes-agent-main/tests/plugins/test_takyon_app_identity_pg.py
# this log entry is additive — trim it back to the Phase-4 increment if reverting.
```

## Increment — Phase 5b: Product plan catalog + sub-user entitlements (Postgres port)

**Date:** 2026-05-30

**What:** Ported the second product slice to Postgres — the per-business PLAN CATALOG and the
per-sub-user ENTITLEMENTS: migration `0006_app_entitlements.sql` (`app_plan_policies` +
`app_entitlements`) and a pure leaf `app_entitlements.py` that upserts plans, grants entitlements
(guarded), resolves a sub-user's effective tier, and lists both. This is increment **(b)** of
Phase 5; identity/auth/session was **(a)**. Usage/budget, checkout/webhook/revenue +
owner→custody accrual, and the gateway-key boundary are **(c)–(e)**.

**Why:** Phase-5 acceptance is *"all apps share rails"* (mediationplan.md line 233). After
"who is this customer" (5a) the next shared rail is *what is this customer entitled to* — the
access tier a paid plan unlocks. The plan catalog is the thing a Stripe checkout (5d) sells; the
entitlement is what that checkout grants. The SQLite product path owning this (core.py
app_plan_policies/app_entitlements) is on death row (Phase 8 kills SQLite), so this is its
**successor authority on Postgres**, not a parallel system.

**Gate-1 finding — and a CORRECTION to the recorded Phase-5 finding.** The Phase-5 gate note
(mediationplan.md) said to DROP four "dead" `app_plan_policies` fields:
`included_action_quota` / `allow_overage` / `stripe_payment_link_id` / `stripe_payment_link_url`.
At build time I verified each against source and **two of those were wrong to call dead**:
- `included_action_quota` and `allow_overage` ARE read — rendered into the per-business
  `product/plans.md` mirror (core.py:3884-3885) and fed into `_plan_validation_warnings`
  (core.py:1995). They are descriptive (not enforced), but they are read, so they are **PORTED**.
- only `stripe_payment_link_id` / `stripe_payment_link_url` are genuinely write-only (written by
  the SQLite upsert at core.py:5203/5217-5218/5237-5238, read NOWHERE) → **DROPPED** as cruft.

The mediationplan finding was edited in place with this correction (dated 2026-05-30). Net: the
PG plan table is the SQLite shape minus the two payment-link columns. This is the Gate-1
discipline working as intended — inspect before building, correct the premise rather than port a
mistake.

**Gate-2 finding (credentials):** none. The leaf calls no provider and moves no money; it stores
catalog + grant state. Stripe ids are stored as opaque references only.

**Decisions:**
- **The money-truth guard is ported verbatim and fires BEFORE any write.** A grant with a
  non-free tier, `source='manual'`, no Stripe evidence (customer/subscription/checkout id), and
  no explicit non-billing escape (`source ∈ {internal,owner,comp,test}` or
  `metadata.non_billing`) raises `FakeBillingRejected` — granting a paid tier with no payment
  proof would fake billing state (the exact check at core.py:5314, invariant #8). A test proves
  the rejection writes **zero** entitlement rows and leaves `app_users.tier='free'`.
- **Entitlements are append-a-row; the effective tier is resolved, not stored on one row.**
  `_sync_user_tier` (ported from core.py:3545) selects the highest-rank grant among
  `status ∈ (active, trialing)` — rank `owner(0) < paid=pro(1) < free(2) < unknown(5)`, verbatim
  from core.py:2742 — and caches it onto `app_users.tier` in the same transaction as the insert.
  A `cancelled` grant confers nothing (test-proven), and `resolve_user_tier(...)` recomputes the
  cache after an out-of-band status change (the seam the 5d webhook will use when a subscription
  lapses).
- **Plan upsert is idempotent on (business_slug, plan_key)**; on conflict every field overwrites
  EXCEPT `stripe_product_id`/`stripe_price_id`, which are **COALESCE-preserved** (a re-upsert that
  omits them keeps the prior linkage) — faithful to core.py:5207. Validation warnings are folded
  into stored `metadata.takyon_plan_validation` exactly as the SQLite path did (advisory only).
- **`billing_interval` and `plan_key` are normalized in the leaf** (interval alias map →
  {month,year,one_time}; `plan_key` slugified like `_file_slug`) so 'monthly'/'Pro Plan' collapse
  deterministically; a bad interval or negative amount raises `InvalidPlan`.
- **jsonb is written with `json.dumps(...)` bound through a `%s::jsonb` cast** — the leaf imports
  no psycopg (house style), and the existing leaves never wrote a non-default jsonb, so this is the
  first one to; the cast keeps it adapter-free. On read, psycopg returns jsonb as a dict directly.
- **email→sub-user resolution reuses `app_identity.upsert_app_user`** (cross-leaf import; no cycle
  — app_identity imports nothing from here). An unknown business fails loud through that FK; an
  unknown `app_user_id` raises `AppUserNotFound`.

**Files created:**
- `hermes-agent-main/plugins/takyon/db/migrations/0006_app_entitlements.sql` — `app_plan_policies`
  (minus the two dead payment-link columns; CHECKed non-negative amounts + canonical
  billing_interval; UNIQUE(business_slug, plan_key)) and `app_entitlements` (append-a-row, status
  free-text, index on (business_slug, app_user_id, status)). Two fail-loud REPLACE guards (one per
  table) matching 0001-0005. Idempotent DDL.
- `hermes-agent-main/plugins/takyon/app_entitlements.py` — pure leaf:
  `upsert_plan_policy`/`get_plan_policy`/`list_plan_policies`; `grant_entitlement` (guarded) /
  `resolve_user_tier` / `list_entitlements`; `plan_validation_warnings` (pure, ported);
  `PlanPolicy`/`Entitlement` dataclasses; `EntitlementError`/`InvalidPlan`/`AppUserNotFound`/
  `FakeBillingRejected`.
- `hermes-agent-main/tests/plugins/test_takyon_app_entitlements_pg.py` — **23** tests on real
  Postgres: plan defaults / idempotent upsert / COALESCE-preserve of Stripe ids / interval +
  slug normalization / bad-interval + negative-price → InvalidPlan / unknown-business fail-loud /
  validation-warning folding / cheapest-first ordering; entitlement provision-by-email + tier
  cache / paid-with-evidence (+ timestamptz round-trip) / **manual-paid-without-evidence rejected
  and writes nothing** / comp + metadata.non_billing escapes / highest-rank-wins / cancelled
  confers nothing / resolve-after-out-of-band-change / unknown-user + missing-args raise /
  business scoping / per-user listing.

**Files changed:** none in code (purely additive: new migration + new leaf + new test file, so
Phases 1–5a cannot regress). The one non-code edit is the **correction to mediationplan.md's
Phase-5 gate finding** described above (the dead-field set).

**Verification:** from `hermes-agent-main`, `TAKYON_TEST_PG_DSN=…@127.0.0.1:54329/takyon_test`,
via `scripts/run_tests.sh` (`-n 4`, hermetic): the Phase-5b suite → **23 passed**. Full
`tests/plugins/` → **987 passed, 2 failed** (= Phase 5a's 964 + the 23 net-new tests). The 2
failures are the **same confirmed pre-existing change-detectors** documented since Phase 4
(`test_business_work_focus_…` under unrelated uncommitted edits; the web-search registry test
expecting 7 providers when the committed `xai` makes 8). Neither imports `app_entitlements`, the
0006 migration, or the PG fixtures.

**Not done / honest state:**
- **NOT mounted into any live HTTP surface.** `app_entitlements.py` is a standalone leaf; the
  product surface (`/api/takyon/apps/<business>/…`) still runs on SQLite until a later increment
  re-points it.
- **Nothing here WRITES entitlements from Stripe yet.** `grant_entitlement` accepts Stripe
  evidence and clears the money-truth guard, but the checkout-session creation + the webhook that
  turns a `checkout.session.completed` / subscription event into a grant (and updates status on
  lapse, and accrues to owner custody) is **increment 5d**. `current_period_end` is a
  `timestamptz` the 5d webhook will populate from the parsed Stripe period.
- **The enforced AI budget is still increment 5c.** Plan `included_ai_budget_microusd` /
  `included_action_quota` are descriptive catalog metadata; the authoritative reserve-then-settle
  budget gate is not built here.
- **`app_users.tier` is a denormalized cache.** It is kept correct on every grant and via
  `resolve_user_tier`; it is not the source of truth (the entitlement rows are). This matches the
  SQLite behavior.
- **Live Supabase apply remains blocked** on the polsia2 teardown + backup + operator go-ahead.
  0006 has only been applied to local throwaway test DBs.

**Revert:**
```sh
rm hermes-agent-main/plugins/takyon/app_entitlements.py
rm hermes-agent-main/plugins/takyon/db/migrations/0006_app_entitlements.sql
rm hermes-agent-main/tests/plugins/test_takyon_app_entitlements_pg.py
# also revert the Phase-5 gate-finding correction in mediationplan.md (the dead-field note).
# this log entry is additive — trim it back to the Phase-5a increment if reverting.
```

---

## Increment — Phase 5c: Product AI-spend budget collapsed to ONE reserve-then-settle gate (Postgres port)

**Date:** 2026-05-30

**What:** Ported the product AI-spend budget cap + usage ledger to Postgres as a pure leaf
(`app_usage.py`) backed by migration `0007_app_usage_budget.sql` (`app_budgets` + `app_usage_events`),
and **collapsed the SQLite trunk's two uncoordinated enforcement paths into a single authoritative
reserve-then-settle gate** mirroring `billing.py` (Phase 3) on the product budget.

**Why:** This is the per-business COMPUTE budget — the cap on what a business's PRODUCT may spend on
AI on behalf of its sub-users (distinct from the Takyon operator's own money in `billing.py`/0002).
The SQLite trunk gated it on two paths and both are wrong under load (the whole reason for this
increment):
1. an **estimate PRE-CHECK** (`app_api.py:379`, `/generate`) that read a rendered budget mirror
   (`_app_budget_remaining_microusd`, `app_api.py:176`) and compared `estimate > remaining` but
   **reserved nothing** — pure read-then-act, so N concurrent calls all saw the same headroom and
   all proceeded (overspend); and
2. an **actuals RE-SUM at insert** (`core.py:5362`, the `app.usage.record` op) that summed
   `actual_cost_microusd` only and raised if it would exceed the cap — but it fires **after** the
   provider was already called and paid, so tripping it means **refusing to RECORD spend that
   already happened** (the ledger then under-counts real cost — a money-truth violation,
   mediationplan invariant #8).

**Gate-1 finding (inspect-before-build):** the budget tables (`app_budgets`, `app_usage_events`) and
the two-path gate are the canonical SQLite home (`core.py:3026-3034`, `3203-3224`, `3529-3543`,
`5349-5398`; `app_api.py:330-466`). There is **no** Postgres budget surface yet (0005 = identity,
0006 = entitlements). So this is the **successor**, not a second parallel authority — net-new
Postgres tables + leaf that REPLACE the SQLite path at Phase 8, exactly as 5a/5b did. No redundant
store created; the one canonical reserve/settle pattern already in the repo (`billing.py`) is reused
in shape, not duplicated in code (different table, different invariant).

**Gate-2 finding (credentials/providers):** **none.** This is pure ledger state on the existing
local Postgres; no new key, provider, or external call. (The Anthropic key that `/generate` needs to
actually spend is a *5e* concern — the gateway-key boundary — and the Stripe rails that fund the
budget are *5d*. This increment only meters and caps; it calls nothing external.)

**Decisions (and the deliberate divergences):**
- **ONE gate = `reserve_usage`.** It is the only thing that can refuse spend. Atomic under the
  `app_budgets` row lock (`select … for update`) — the same single-row-lock invariant `billing.py`
  rests on — so it computes committed spend over a stable view and parallel reserves can never
  oversell. **Committed = Σ(estimate of still-`reserved` rows) + Σ(actual of `completed` rows)**
  within the period; `failed`/`released` rows count zero. Refuses with `AppBudgetExceeded` (carrying
  hard_limit/committed/requested/remaining for a precise 402) or `AppBudgetInactive`, writing
  nothing.
- **`settle_usage` records the REAL provider spend and NEVER re-checks the cap.** Once money is
  spent, recording the truth is mandatory — this is the fix for path-2's integrity bug.
  **Deliberate divergence from `billing.py`:** `billing.settle` asserts `actual ≤ reserved` because
  it is custody of the user's real money; here the estimate is only a pre-flight gate and the
  provider's actual is the truth, so settle records `actual` even if it slightly exceeds the
  reserved estimate (capping it would reintroduce the very under-count this increment removes). The
  cap is enforced at reserve. Test `test_settle_records_true_actual_even_if_over_estimate` pins this.
- **`release_usage` frees the hold on the failure path** (reserved → `failed` when an error is
  given, else `released`); actual stays 0 so committed drops by the freed estimate. settle/release
  are idempotent (first finalizer wins; row-locked, so concurrent finalizers serialize).
- **`record_completed_usage`** is reserve+settle **fused** for the synchronous self-report path (the
  SQLite `/usage` route, `app_api.py:339`, where the cost is already known and there is no provider
  round-trip to straddle). It goes through the **same** committed-aggregate gate (so it is not a
  second gate — the check logic is the shared `_ensure_budget_locked` + `_committed_microusd`), then
  writes a `completed` row directly; gate amount is `max(estimate, actual)`.
- **`reservation_key` is the idempotency handle**, UNIQUE per business (mirrors `billing.py`'s
  `reservation_key`); a replay holds/charges once. `status` is a CHECKed lifecycle
  (`reserved`/`completed`/`failed`/`released`) — net-new vs SQLite's free-text status, and
  load-bearing for the gate.
- **`get_usage_summary`** is the authoritative pre-flight read (status/hard_limit/committed/
  remaining/period) meant to REPLACE the stale rendered-mirror read the broken pre-check used.
- **Period semantics ported faithfully** (calendar-month UTC, fixed at row creation) — see Not done.

**Files created:**
- `hermes-agent-main/plugins/takyon/db/migrations/0007_app_usage_budget.sql` — `app_budgets` (PK
  business_slug, status, `hard_limit_microusd` bigint default 5_000_000, period defaults via
  `date_trunc('month', now())`) and `app_usage_events` (uuid PK, `app_user_id` FK SET NULL, CHECKed
  `status` lifecycle, bigint cost columns, `reservation_key` UNIQUE(business_slug, reservation_key),
  jsonb metadata, index on (business_slug, created_at, status)). Two fail-loud REPLACE guards (one
  per table) matching 0001-0006. Idempotent DDL (verified run-twice on a scratch DB across the full
  0001→0007 chain).
- `hermes-agent-main/plugins/takyon/app_usage.py` — pure leaf: `ensure_app_budget`/`set_app_budget`/
  `get_app_budget`/`get_usage_summary`; `reserve_usage`/`settle_usage`/`release_usage`/
  `record_completed_usage`/`list_usage_events`; `AppBudget`/`UsageEvent` dataclasses;
  `AppUsageError`/`AppBudgetInactive`/`AppBudgetExceeded`/`UnknownReservation`/`AppUserNotFound`.
- `hermes-agent-main/tests/plugins/test_takyon_app_usage_pg.py` — **29** tests on real Postgres:
  budget open/default/idempotent + set cap/status + unknown-business fail-loud + negative-cap →
  ValueError; reserve-holds-then-settle-records-actual; **settle records true actual even over
  estimate**; release frees hold (failed vs released); reserve/settle idempotent + first-finalizer
  wins + release-after-settle no-op; settle/release unknown → UnknownReservation; reserve refused
  when inactive / over cap (carries figures, **writes nothing**); freed-headroom-lets-later-reserve-
  fit; unknown/cross-business app_user → AppUserNotFound; input validation; settle COALESCE-preserves
  provider/model + merges metadata; record_completed gates+writes one-shot + idempotent + max(est,
  actual); business-scoped reservation keys; event survives sub-user delete (SET NULL); list
  newest-first + per-user filter; **two real-concurrency tests** — `test_concurrent_reserves_never_
  overspend` (25 threads, cap fits exactly 10 → exactly 10 ok / 15 exceeded, committed never over
  cap — the test the SQLite read-then-act gate would FAIL) and `test_concurrent_identical_
  reservation_key_holds_once`.

**Files changed:** none in code (purely additive: new migration + new leaf + new test file, so
Phases 1–5b cannot regress).

**Verification:** from `hermes-agent-main`, `TAKYON_TEST_PG_DSN=…@127.0.0.1:54329/takyon_test`, via
`scripts/run_tests.sh` (`-n 4`, hermetic): the Phase-5c suite → **29 passed**. Full `tests/plugins/`
→ **1016 passed, 2 failed** (= Phase 5b's 987 + the 29 net-new tests). The 2 failures are
**pre-existing and unrelated**: `test_business_work_focus_persists_and_blocks_cross_lane_writes` and
the web-search `test_all_seven_plugins_present_in_registry`. Proven not-mine three ways — (a) both
target subsystems I did not touch (`core.py` artifact-path resolution → `distribution/outreach/…`;
the web-search provider registry expecting 7 when committed `xai` makes 8); (b) `git status` shows
`core.py` and `test_takyon_plugin.py` carry **pre-existing uncommitted edits** (+486/+123 lines, a
separate in-progress `distribution/meta-ads` feature) while my increment is only the 3 untracked
files; (c) neither failing test imports `app_usage`, the 0007 migration, or the Postgres `pg_conn`
fixture, so a Postgres-only addition cannot reach them. `py_compile` of the leaf is clean; 0007
re-applied to a scratch DB is idempotent (only benign "already exists, skipping" notices).

**Not done / honest state:**
- **NOT mounted into any live HTTP surface.** `app_usage.py` is a standalone leaf; `/generate` and
  `/usage` (`app_api.py`) still run the SQLite two-path gate until the product surface is re-pointed
  at a later increment. This increment proves the correct gate exists and is concurrency-safe; it
  does not yet replace the live path.
- **Monthly period does NOT auto-roll — ported faithfully from SQLite.** `current_period_start` is
  fixed at budget creation and the gate sums `created_at >= current_period_start`, so the cap is
  effectively a since-creation cap, not a resetting monthly one — identical to the SQLite trunk
  (`_ensure_app_budget`, `core.py:3529`, never advances the period). Rolling the period forward is a
  **system-wide** semantics decision (it affects SQLite too) and is deliberately out of scope for
  "collapse the double-charge gate." Flagged here so it can be addressed once, in the right place,
  rather than diverging the Postgres path from the live SQLite behavior now.
- **The budget is not yet FUNDED or linked to plans.** A plan's `included_ai_budget_microusd` (0006)
  is descriptive; wiring a paid plan/top-up into a business's `hard_limit_microusd`, and the Stripe
  rails that pay for it + owner→custody accrual, are **increment 5d**.
- **Live Supabase apply remains blocked** on the polsia2 teardown + backup + operator go-ahead. 0007
  has only been applied to local throwaway test DBs.

**Revert:**
```sh
rm hermes-agent-main/plugins/takyon/app_usage.py
rm hermes-agent-main/plugins/takyon/db/migrations/0007_app_usage_budget.sql
rm hermes-agent-main/tests/plugins/test_takyon_app_usage_pg.py
# this log entry is additive — trim it back to the Phase-5b increment if reverting.
```

## Increment — Phase 5d: Product checkout + Stripe webhook reconciliation + revenue ledger + **net-new owner→custody accrual** (Postgres port + flow B ADD)

**Date:** 2026-05-30

**What:** Ported the product CHECKOUT → Stripe WEBHOOK → REVENUE rail to Postgres as a pure leaf
(`app_payments.py`) backed by migration `0008_app_payments.sql` (four tables: `app_checkout_intents`,
`app_checkout_sessions`, `app_revenue_events`, and the global `webhook_events`), and **ADDED the
net-new owner→custody accrual (flow B) the SQLite product path never had** — on a paid revenue event,
resolve `business_slug → businesses.owner_user_id` and accrue the gross minus the platform app fee
into the OWNER's custody ledger via the existing `custody.accrue` (0002).

**Why (two things, one increment):**
1. **The accrual gap (the headline ADD, mediationplan Phase 5 (a)).** The SQLite product webhook
   (`core.py:6844` `_process_checkout_completed`) records business REVENUE on a paid checkout but
   performs **ZERO** owner accrual — `grep` finds no custody/accrual/app-fee reference anywhere in the
   product path. A business's sub-users pay on the shared platform Stripe, but the money never reaches
   the business OWNER's custody ledger (flow B in 0002). This increment closes that: on a paid revenue
   event we resolve the owner (the `businesses.owner_user_id` linkage 0001 added and SQLite lacks) and
   accrue gross − app fee (`STRIPE_CONNECT_APPLICATION_FEE_BPS`, default 2000 bps = 20%) into the
   owner's custody account, so "sub-user payment shows in owner custody" (the Phase 5 acceptance).
2. **A latent SQLite double-grant bug (robustness #1).** The SQLite handler INSERT-OR-IGNOREs the
   `webhook_events` dedup row but then processes **unconditionally**, and its entitlement insert
   (`core.py:6915`) is a plain `INSERT` with **no conflict target** — so a redelivered
   `checkout.session.completed` appends a DUPLICATE entitlement. The Postgres port closes that: the
   webhook gate locks the `webhook_events` row `for update` and SKIPS if `processed_at` is set, so each
   delivered event is processed to completion **at most once** even under concurrent redelivery.

**Gate-1 finding (inspect-before-build):** the four product payment tables and the webhook handlers
are the canonical SQLite home (`core.py:3141-3234` DDL; `6844` checkout, `6929` subscription, `6956`
`handle_business_record_stripe_webhook`). There is **no** Postgres payment surface yet (0005 =
identity, 0006 = entitlements, 0007 = usage budget). So the four tables + leaf are the **successor**,
not a second authority — net-new Postgres tables that REPLACE the SQLite path at Phase 8, exactly as
5a/5b/5c did. The owner accrual is genuinely **net-new (ADD)** and reuses the **existing**
`custody.accrue` (0002) — it does **not** create a second ledger. `app_payments` **orchestrates
sibling leaves** (`custody`, `app_entitlements`) — a precedent already in the repo
(`app_entitlements.py:34` imports `app_identity`), so no new architectural pattern was invented.

**Gate-2 finding (credentials/providers):** **none new.** Signature verification deliberately stays
**out** of this leaf — the caller verifies the Stripe signature (the existing product
`STRIPE_WEBHOOK_SECRET`, via `stripe_util`) and hands `record_webhook_and_process` an
already-verified event dict, so this increment introduces no key/provider/external call.
`STRIPE_CONNECT_APPLICATION_FEE_BPS` already exists (`custody.app_fee_bps()`, default 2000). The one
still-outstanding credential remains `STRIPE_BILLING_WEBHOOK_SECRET` (the Phase-3 control-plane topup
webhook — a *different* secret from this product webhook); it is unchanged by 5d and still tracked in
mediationplan Gate 2.

**Decisions (and the deliberate divergences):**
- **The webhook gate = `record_webhook_and_process`.** ONE outer `with conn.transaction():` →
  `INSERT webhook_events ON CONFLICT DO NOTHING` → `SELECT processed_at … FOR UPDATE` → if already
  set, return `{deduplicated: True}` and write nothing → else dispatch → `UPDATE processed_at = now()`.
  This is the at-most-once invariant that fixes the SQLite double-grant; it mirrors `billing.py`'s
  single-row-lock pattern. Because the whole dispatch is one transaction, a mid-failure rolls back the
  dedup row too, so the event is cleanly retryable.
- **Orchestrator opens one transaction; sibling leaves nest as savepoints.** `_process_checkout_completed`
  runs inside the caller's transaction and calls `app_entitlements.grant_entitlement`,
  `custody.open_custody_account`, and `custody.accrue` — each opens its own `conn.transaction()`,
  which psycopg turns into a SAVEPOINT under the outer BEGIN. The concurrency test proves the triple
  nesting commits/rolls back correctly under 8-way contention.
- **Owner accrual reuses `custody.accrue`, keyed deterministically** on
  `f"app_revenue:{business}:{event_id}:{session_id}"`, so a replayed paid event accrues **once**;
  `custody.open_custody_account` is called first (idempotent) so accrual never hits `NoCustodyAccount`.
  Accrual fires **only when a NEW `app_revenue_events` row is actually inserted** (the
  `INSERT … ON CONFLICT DO NOTHING RETURNING id` returns nothing on replay) **and** `amount_total > 0`
  — so a duplicate paid event neither double-records revenue nor double-accrues.
- **Connect payout is NOT part of this increment — and that is correct per the money model.** Accrual
  writes the OWED balance into custody (a ledger fact from day one); the actual Stripe Connect
  transfer to the owner is the deferred payout rail. This increment makes the owed balance *true*, not
  *paid*.
- **Entitlement grant delegated** to `app_entitlements.grant_entitlement` (auto-provisions the sub-user
  from `customer_email`, passes the fake-billing gate via Stripe evidence, `tier="paid"`,
  `source="stripe"`). Granted only when there's an email AND (a subscription id OR `payment_status ==
  'paid'`).
- **Subscription lifecycle delegated to a net-new `app_entitlements.set_subscription_status`** — added
  this increment to the **canonical entitlements home**, not buried in `app_payments`. It maps the
  Stripe status (`active`/`trialing` → active, `canceled`/`cancelled` → cancelled, else past_due —
  verbatim from `core.py:6026`) and resyncs tier. Stripe-status *interpretation* stays out of the
  entitlements leaf (the mapping lives in `app_payments._subscription_entitlement_status`); the leaf
  only applies a given status.
- **Checkout-intent idempotency:** `create_checkout_intent` is `INSERT … ON CONFLICT
  (client_reference_id) DO UPDATE SET updated_at = now() RETURNING`, so a replayed start returns the
  **original** intent and cannot fork a second checkout for the same logical upgrade.
- **Stripe epoch timestamps → tz-aware `datetime`** via `_epoch_to_dt` (psycopg adapts natively); jsonb
  via `json.dumps(..., sort_keys=True)` bound through `%s::jsonb`, matching 5a/5b/5c.

**Files created:**
- `hermes-agent-main/plugins/takyon/db/migrations/0008_app_payments.sql` — the four tables (three
  business-scoped: `app_checkout_intents`, `app_checkout_sessions`, `app_revenue_events`; one global:
  `webhook_events`), four fail-loud REPLACE guards (3 keyed on `business_slug`, `webhook_events` keyed
  on `provider_event_id`), and four indexes. Dedup keys: `app_revenue_events UNIQUE(business_slug,
  provider_event_id, stripe_object_id)`, `app_checkout_sessions.stripe_checkout_session_id UNIQUE`,
  `app_checkout_intents.client_reference_id UNIQUE`, `webhook_events UNIQUE(provider,
  provider_event_id)`. Idempotent DDL (verified run-twice on a scratch DB across the full 0001→0008
  chain).
- `hermes-agent-main/plugins/takyon/app_payments.py` — pure leaf orchestrating `custody` +
  `app_entitlements`: public `create_checkout_intent` / `attach_checkout_session` /
  `get_checkout_intent` / `record_webhook_and_process` / `list_revenue_events` / `get_revenue_summary`;
  internal `_process_checkout_completed` / `_process_subscription_event` / `_resolve_owner` /
  `_find_intent_row`; `CheckoutIntent` / `RevenueEvent` dataclasses; `AppPaymentError` /
  `InvalidWebhookEvent` / `CheckoutIntentNotFound` / `BusinessOwnerMissing`.
- `hermes-agent-main/tests/plugins/test_takyon_app_payments_pg.py` — **21** tests on real Postgres:
  checkout intent create / idempotent-on-ref / unknown-business → ForeignKeyViolation / required
  fields → ValueError; attach by id / by ref / unknown → CheckoutIntentNotFound; get by id / ref /
  None; **`test_paid_checkout_accrues_to_owner_custody`** (the acceptance: revenue summary = {1000, 1
  event}, owner owed = net-of-fee, reconcile ok, paid entitlement with Stripe evidence, tier = paid);
  owner-accrual-nets-exact-fee (asserts gross/fee/net custody entries for 5000); webhook-idempotent-
  on-replay (second deduplicated, one revenue / one entitlement, owed not doubled); paid-without-email
  (accrues, no entitlement); unpaid (session recorded, no revenue/accrual); zero-amount (revenue
  recorded, no accrual); subscription-cancel-drops-tier-to-free; subscription-unknown noop;
  ignored-event consumed; event-without-id → InvalidWebhookEvent; list-revenue newest-first; and
  **`test_concurrent_identical_webhook_processes_exactly_once`** (8 threads via barrier + fresh conns →
  exactly 1 processed / 7 deduped, 1 revenue event, 1 entitlement, owed accrued once, reconcile ok —
  proving the SQLite double-grant cannot happen here).

**Files changed:**
- `hermes-agent-main/plugins/takyon/app_entitlements.py` — appended the net-new public
  `set_subscription_status` (the subscription-lifecycle writer) in the canonical entitlements home.
  **Purely additive** — no existing function altered, so 5a/5b/5c cannot regress.

**Verification:** from `hermes-agent-main`, `TAKYON_TEST_PG_DSN=…@127.0.0.1:54329/takyon_test`, via
`scripts/run_tests.sh` (`-n 4`, hermetic): the Phase-5d suite → **21 passed**. Full `tests/plugins/`
→ **1037 passed, 2 failed** (= Phase 5c's 1016 + the 21 net-new tests). The 2 failures are the **same
pre-existing, unrelated** pair as 5c — `test_business_work_focus_persists_and_blocks_cross_lane_writes`
(core.py artifact-path, from the separate uncommitted `distribution/meta-ads` working-tree edits) and
the web-search `test_all_seven_plugins_present_in_registry` (expects 7, committed `xai` makes 8).
Proven not-mine three ways, identical to 5c: (a) both target subsystems I did not touch; (b) the edits
they trip on are pre-existing uncommitted `core.py`/`test_takyon_plugin.py` changes, not my untracked
files; (c) neither failing test imports `app_payments`, `app_entitlements`, the 0008 migration, or the
Postgres `pg_conn` fixture. `py_compile` of both Python files is clean; 0008 re-applied to a scratch DB
is idempotent (only benign "already exists, skipping" notices).

**Not done / honest state:**
- **NOT mounted into any live HTTP surface.** `app_payments.py` is a standalone leaf; the SQLite
  `handle_business_record_stripe_webhook` (`core.py:6956`) still runs the live product webhook until
  the product surface is re-pointed at a later increment. This increment proves the correct gate +
  accrual exist and are concurrency-safe; it does not yet replace the live path.
- **Signature verification is the caller's job, by design.** `record_webhook_and_process` takes an
  already-verified event dict. The eventual mount point must verify the Stripe signature (existing
  `STRIPE_WEBHOOK_SECRET` via `stripe_util`) before calling in. Keeping provider-secret handling out of
  the ledger leaf is deliberate.
- **Owner payout (Stripe Connect transfer) is deferred** per the account/money model — accrual records
  the *owed* balance in custody; moving that money to the owner is the later payout rail. The owed
  balance is a true ledger fact from day one; it is not yet *paid out*.
- **Period roll + plan funding unchanged** (carried from 5c): a plan's `included_ai_budget_microusd`
  is still descriptive and the monthly period still does not auto-roll — both are out of scope here.
- **Live Supabase apply remains blocked** on the polsia2 teardown + backup + operator go-ahead. 0008
  has only been applied to local throwaway test DBs.

**Revert:**
```sh
rm hermes-agent-main/plugins/takyon/app_payments.py
rm hermes-agent-main/plugins/takyon/db/migrations/0008_app_payments.sql
rm hermes-agent-main/tests/plugins/test_takyon_app_payments_pg.py
# also remove the appended set_subscription_status function from
# hermes-agent-main/plugins/takyon/app_entitlements.py (git checkout that file, or delete that one fn).
# this log entry is additive — trim it back to the Phase-5c increment if reverting.
```

## Increment — Phase 5e: project gateway-key boundary (the credential that fronts the platform provider key)

**Date:** 2026-05-30

**What:** Added the **project gateway-key boundary** as a net-new migration `0009_app_gateway_keys.sql`
(one table, `app_gateway_keys`) + a pure leaf `app_gateway_keys.py` (mint / resolve / revoke / list).
A business is minted an internally-generated `tkg_…` key; presenting it resolves to ONLY that
business (`business_slug` + `key_id`). This is the boundary that lets "generated app never holds
provider key" (the Phase 5 acceptance): the generated product app and the app runtime hold a
`tkg_…` gateway key, present it to the internal AI gateway, and the gateway — not the app — calls
the shared platform provider key server-side.

**Why (mediationplan Gate-1 gap (3), verified at source):** the SQLite product `/generate` path
(`app_api.py:395`) calls Anthropic with the **platform's shared key** (`_anthropic_key()`,
`app_api.py:66`) **directly** — there is **no per-business gateway-key boundary** in front of the
provider key. So any caller of the product AI route is one hop from the raw platform key. This
increment introduces the missing credential layer: a per-business, internally-minted capability that
the internal AI gateway resolves to a `business_slug` before it touches the provider key, so the app
side only ever holds its own scoped `tkg_…` key.

**Gate-1 finding (inspect-before-build):** `grep` confirms **no predecessor** — there is no
`app_gateway_keys` (or any per-business gateway-key concept) in the SQLite trunk (`core.py`/
`app_api.py`) or in 0001-0008; the only `ai_gateway` hits are an unrelated `ai_gateway_setup`
workflow id (`core.py:211`) and archived polsia3 reference docs. So this is **net-new (ADD)**, not a
port of an existing table. The canonical at-rest pattern to mirror already exists — `user_api_keys`
(0001:63) + `control_plane.resolve_api_key` (the opaque SHA-256-hash + prefix + by-hash resolve). The
gateway key is a **different scope** (per-BUSINESS, not per-USER) and a **different keyspace**, so it
does **not** belong inside `user_api_keys` (which is the entire per-user boundary) nor inside any
product table. *Decision:* **ISOLATE** — its own `app_gateway_keys` table + its own `app_gateway_keys`
leaf, **reusing** the prefix-agnostic `user_api_keys.hash_api_key` (the security-critical hashing is
not duplicated) while minting in a distinct namespace.

**Gate-2 finding (credentials/providers):** **none new.** The gateway key is **internally minted** —
`secrets.token_urlsafe(32)` in the `tkg_` namespace; minting a hash needs no external account, no
provider key, no operator action (mediationplan Phase 5 Gate 2: "the project gateway key is an
internally-minted per-business credential … minting a hash needs no external account"). The shared
platform Anthropic key it fronts (`ANTHROPIC_API_KEY` / CLI `get_anthropic_key()`) already exists and
is unchanged. The one still-outstanding credential remains `STRIPE_BILLING_WEBHOOK_SECRET` (Phase 3),
untouched here.

**Decisions (and the deliberate divergences):**
- **Distinct, disjoint keyspace `tkg_`** (vs the per-user `tk_`). This is the security crux: a
  `tk_…` user key never starts with `tkg_` (its 3rd char is `_`, not `g`) and a `tkg_…` gateway key
  never starts with `tk_`, so the two keyspaces are provably disjoint at the well-formedness check —
  a user key is **rejected before any DB lookup** as a gateway key and vice versa. Belt-and-suspenders
  on top of that: the two key types live in **separate tables** (`user_api_keys` vs
  `app_gateway_keys`) with separate resolvers, so even a hypothetical hash collision could not
  cross-resolve. Test `test_user_key_and_gateway_key_keyspaces_are_disjoint` pins this.
- **At-rest = hash + prefix only**, reusing `user_api_keys.hash_api_key` verbatim (SHA-256 hex). The
  raw key is returned **exactly once** at mint and is unrecoverable; only `key_hash` (UNIQUE, also the
  hot resolve index) and a non-secret `prefix` are stored — identical discipline to `user_api_keys`.
- **`resolve_gateway_key` returns the minimum** — a frozen `GatewayPrincipal(business_slug, key_id)`,
  the opaque handle the gateway needs to route (business → policy (0004) → product budget (0007) →
  shared provider key → settle). No provider key, no other tenant, no internal handle leaks (the same
  opaque-by-construction discipline as `ResolvedPrincipal`). None for malformed / unknown / revoked.
- **NO one-active-per-business constraint — deliberate divergence from `user_api_keys`.** A user has
  exactly one active key (the whole per-user boundary, enforced by the `user_api_keys_one_active`
  partial unique index). A business may legitimately hold **several** active gateway keys at once (the
  app runtime + the generated app, or an overlapping rotation where the old key keeps the deployed app
  working until cutover). So `mint_gateway_key` always INSERTs and rotation is mint-new + revoke-old
  as separate steps, not the atomic single-row swap `rotate_api_key` does. Tests
  `test_business_may_hold_multiple_active_keys` and `test_concurrent_mint_produces_unique_resolvable_keys`
  pin it (incl. 8-way concurrent mint with no collision).
- **Revocation is soft, idempotent, and scopable.** `revoke_gateway_key` sets `revoked_at` (keeps the
  row for audit), identified by `key_id` OR `raw_key`, optionally scoped to `business_slug` so one
  business cannot revoke another's key (the `(%s::uuid is null or …)` NULL-guard cast pattern from
  5d's `attach_checkout_session`). Returns True iff a row moved; an already-revoked / unknown /
  out-of-scope key returns False.
- **`business_slug` FK CASCADE** — a deleted business takes its gateway keys with it, so a resolvable
  key always points at a live business and the resolver needs **no existence join** (single-table
  lookup on the indexed `key_hash`). Test `test_business_delete_cascades_keys` pins it.

**Files created:**
- `hermes-agent-main/plugins/takyon/db/migrations/0009_app_gateway_keys.sql` — `app_gateway_keys`
  (uuid PK, `business_slug` FK CASCADE, `key_hash` text UNIQUE check len>0, `prefix` text check
  len>0, `revoked_at`, `created_at`), one fail-loud REPLACE guard (keyed on `business_slug`), index
  `app_gateway_keys_business_idx (business_slug, created_at desc)`. Idempotent DDL (verified run-twice
  on a scratch DB across the full 0001→0009 chain; table shape confirmed via `\d`).
- `hermes-agent-main/plugins/takyon/app_gateway_keys.py` — pure leaf: `generate_gateway_key` /
  `is_well_formed` / `gateway_key_prefix`; `mint_gateway_key` / `resolve_gateway_key` /
  `revoke_gateway_key` / `list_gateway_keys`; `GatewayKey` + `GatewayPrincipal` dataclasses;
  `AppGatewayKeyError`. Reuses `user_api_keys.hash_api_key`.
- `hermes-agent-main/tests/plugins/test_takyon_app_gateway_keys_pg.py` — **23** tests on real
  Postgres: mint returns a `tkg_` key + stores only hash/prefix (sha256, not raw); resolve returns
  business+key_id only (exactly two fields); resolve rejects malformed (incl. a `tk_` user key) /
  unknown / revoked; **disjoint keyspace** (user key ✗ as gateway key and vice versa); mint unknown
  business → ForeignKeyViolation; **multiple active keys per business**; revoke by raw / by id,
  idempotent, unknown → False, **scoped so it can't cross tenants**, no-identifier → AppGatewayKeyError;
  cross-business resolve isolation; list excludes revoked by default (include_revoked shows them);
  **business delete cascades keys**; and **`test_concurrent_mint_produces_unique_resolvable_keys`**
  (8 threads + barrier + fresh conns → 8 distinct keys, all resolve, list = 8).

**Files changed:** none in code (purely additive: new migration + new leaf + new test file, so
Phases 1–5d cannot regress).

**Verification:** from `hermes-agent-main`, `TAKYON_TEST_PG_DSN=…@127.0.0.1:54329/takyon_test`, via
`scripts/run_tests.sh` (`-n 4`, hermetic): the Phase-5e suite → **23 passed**. Full `tests/plugins/`
→ **1060 passed, 2 failed** (= Phase 5d's 1037 + the 23 net-new tests). The 2 failures are the **same
pre-existing, unrelated** pair as 5c/5d — `test_business_work_focus_persists_and_blocks_cross_lane_writes`
(core.py artifact-path, from the separate uncommitted `distribution/meta-ads` working-tree edits) and
the web-search `test_all_seven_plugins_present_in_registry` (expects 7, committed `xai` makes 8).
Proven not-mine: neither test imports `app_gateway_keys`, the 0009 migration, or the Postgres
`pg_conn` fixture; a Postgres-only addition cannot reach them. `py_compile` of the leaf is clean; 0009
re-applied to a scratch DB is idempotent (only benign "already exists, skipping" notices).

**Not done / honest state:**
- **NOT mounted into any live HTTP surface.** This increment delivers the gateway-key boundary
  *primitive* (mint/resolve/revoke). The live `/internal/ai-gateway/messages` endpoint that composes
  it — `resolve_gateway_key` → `policy.decide_execution` (0004/Phase 4) → `app_usage.reserve_usage`
  (0007/Phase 5c) → `_call_anthropic(<platform key>)` → `app_usage.settle_usage` — is the deferred
  live mount, exactly as 5a-5d deferred theirs. The SQLite `/generate` path (`app_api.py:395`) still
  calls the platform key directly until the product surface is re-pointed at the mount-up phase.
- **No key handed to a generated app yet** — minting a business's gateway key and injecting it into a
  generated product app's runtime config is wiring that belongs to the build-product surface, done
  when the live gateway endpoint exists. This increment proves the credential + its opaque resolve.
- **No `last_used_at`** on the key (the documented table spec omits it), so `resolve_gateway_key` is a
  pure read with no write-lock contention; audit of "which key was used" is the gateway's job via the
  returned `key_id`.
- **Live Supabase apply remains blocked** on the polsia2 teardown + backup + operator go-ahead. 0009
  has only been applied to local throwaway test DBs.

**Phase 5 complete (5a-5e).** The entire sub-user/product runtime is ported to Postgres as the
successor authority — identity/auth/session (5a, 0005), entitlements + plan catalog (5b, 0006), the
collapsed one-gate usage budget (5c, 0007), checkout + webhook + revenue + **owner→custody accrual**
(5d, 0008), and the **project gateway-key boundary** (5e, 0009) — plus the two net-new ADDs the
acceptance demanded (owner accrual so "sub-user payment shows in owner custody"; the gateway key so
"generated app never holds provider key"). All leaves are unmounted; mounting the live HTTP surfaces
(control router + product app runtime, currently SQLite-backed) and the live Supabase apply are the
remaining transitional steps before Phase 8 retires SQLite.

**Revert:**
```sh
rm hermes-agent-main/plugins/takyon/app_gateway_keys.py
rm hermes-agent-main/plugins/takyon/db/migrations/0009_app_gateway_keys.sql
rm hermes-agent-main/tests/plugins/test_takyon_app_gateway_keys_pg.py
# this log entry is additive — trim it back to the Phase-5d increment if reverting.
```

## Increment — Runtime Cutover connection layer (Phase 0 realized): migration runner + host app that actually serves on real Postgres

**Date:** 2026-05-30

**What:** Built the two modules the routers had deliberately omitted as "a separate, deliberate
step." (1) The migration runner `plugins/takyon/db/runner.py` — `run_migrations(conn)` applies every
`db/migrations/*.sql` in lexical (0001…0009) order; idempotent by construction; the SINGLE production
path that brings a database to current schema. (2) The host app `plugins/takyon/runtime_app.py` —
`build_runtime_app(database_url?)` opens a per-request autocommit psycopg connection and overrides
the existing control router's `get_control_conn` seam, so a presented bearer key resolves end-to-end
through the SAME seam production uses, against real Postgres. Plus the shared provider leaf
`plugins/takyon/ai_provider.py` (extracted from `app_api.py`) so the SQLite `/generate` route and the
next-increment Postgres gateway share ONE provider+cost implementation, and `conftest.py` now
delegates to `run_migrations` so the schema tests run against and the schema production runs against
come from ONE definition.

**Why (mediationplan Phase 0 — "DB layer: Postgres access layer + migration runner; *Accept:* runtime
reads/writes Postgres; migrations idempotent"):** Phases 1–5 built the leaves + the routers
(`control_api.build_control_router()` + a `get_control_conn` seam that "raises until a host overrides
it"), but nothing ever opened a Postgres connection and served — each router's docstring said
mounting was a deliberate later step, and this is that step. Separately, migration application existed
ONLY as a private inline loop inside `tests/plugins/conftest.py` — a second, drift-prone copy of
"apply the migrations" with no production counterpart.

**Gate-1 finding (inspect-before-build):**
- *Migration application:* `grep` confirmed the only "apply every `*.sql` in sorted order" lived in
  `conftest._apply_migrations` (`for sql_path in sorted(_MIGRATIONS_DIR.glob("*.sql")): conn.execute(…)`)
  — no production runner existed. *Decision:* **EXTRACT** the canonical `run_migrations` into
  `db/runner.py` and have conftest **DELEGATE** to it (not a third copy — the fixture now calls the
  exact code production will), so test/prod schema can never diverge and the suite validates the real
  runner for free. `migration_files()` is scoped to `migrations/` so the manually-gated
  `retire_polsia2_public.sql` teardown (kept deliberately OUTSIDE that dir) is never swept in.
- *Mounting:* the control router already existed and was never mounted. *Decision:* **ADD**
  `runtime_app.py` as the ONE module that knows the production connection strategy (per-request
  `psycopg.connect(url, autocommit=True)`), overriding the seam — routers stay strategy-free and
  identically testable.
- *Provider call + cost:* lived as private functions inside `app_api.py` (the SQLite surface); the
  coming gateway needs the same logic. *Decision:* **EXTRACT** to `ai_provider.py` (one
  implementation) and **REBIND** `app_api`'s private names to it (module body + its existing test
  unchanged) — not a second copy that can drift. When Phase 8 deletes the SQLite path, the leaf is the
  survivor.

**Gate-2 finding (credentials/providers):** **none new.** The host reads `DATABASE_URL` /
`POSTGRES_URL` / `POSTGRES_PRISMA_URL` — already the platform-managed aliases, kept identical to
`core.py`'s "database" provider aliases on purpose. `ai_provider.anthropic_key()` resolves the
already-present shared platform Anthropic key (takyon_cli auth helper, then `ANTHROPIC_API_KEY` /
`ANTHROPIC_TOKEN`). No external account, no operator action. **Invariant #8:** `build_runtime_app()`
with NO database URL configured raises `RuntimeNotConfigured` **loudly** — never a half-live server
that 500s every request, never a silent SQLite fallback. The one still-outstanding credential remains
`STRIPE_BILLING_WEBHOOK_SECRET` (Phase 3), untouched here.

**Decisions (and the deliberate divergences):**
- **Per-request connection, `autocommit=True`, yield, close.** Read paths need no transaction; each
  mutating leaf opens its own `with conn.transaction():`. FastAPI caches the dependency per-request
  (`use_cache`), so the SAME connection is reused across principal-resolution → endpoint within one
  request, then closed when the request ends. ONE connection factory serves both seams (control now,
  the gateway next increment) since both want the same per-request connection to the same DB.
- **`/healthz` is liveness only** — deliberately does NOT touch Postgres; a DB round-trip belongs in a
  separately-gateable readiness probe, not the hot liveness path.
- **`run_migrations` is a pure leaf** — takes a `conn`, never opens/closes one; the caller owns the
  mode. Idempotent (every migration is `create … if not exists` / guarded REPLACE), so it is the
  intended "bring DB to current" op; a genuinely wrong-shaped pre-existing table makes a guard RAISE
  loudly (robustness #1) rather than bind silently.
- **Building/serving this app does NOT retire SQLite.** Flipping the live runtime onto it is the
  separate, operator-gated Runtime Cutover step.

**Files created:**
- `hermes-agent-main/plugins/takyon/db/__init__.py` — db package marker.
- `hermes-agent-main/plugins/takyon/db/runner.py` — `run_migrations(conn)` + `migration_files()`; the
  single idempotent production path to current schema; pure leaf.
- `hermes-agent-main/plugins/takyon/runtime_app.py` — `build_runtime_app(database_url?)`,
  `resolve_database_url()`, `RuntimeNotConfigured`; per-request autocommit conn factory; mounts the
  control router; `/healthz`.
- `hermes-agent-main/plugins/takyon/ai_provider.py` — Anthropic provider leaf: `anthropic_key` /
  `anthropic_model` / `anthropic_payload` / `anthropic_text`, `anthropic_rates_microusd_per_token`,
  `microusd_cost`, `estimate_input_tokens`, `call_anthropic`. One implementation for SQLite + gateway.
- `hermes-agent-main/tests/plugins/test_takyon_db_runner_pg.py` — **3** tests (empty→current applies
  the full ordered set incl. 0001 spine + 0009; idempotent re-run; pure path-order check, no DB).
- `hermes-agent-main/tests/plugins/test_takyon_runtime_app_pg.py` — **6** tests (healthz; `/v1/me` +
  `/v1/businesses` resolve through the REAL per-request seam on real PG; missing bearer 401; unknown
  well-formed key 401; build-without-DB → `RuntimeNotConfigured` (invariant #8, runs without a DB)).

**Files changed:**
- `hermes-agent-main/plugins/takyon/app_api.py` — replaced the inline Anthropic/cost helpers with
  imports from `ai_provider` bound to the original private names (`_anthropic_key`,
  `_anthropic_payload`, `_microusd_cost`, …); module body + existing test unchanged. Net −148/+11
  (deletion of the now-shared logic).
- `hermes-agent-main/tests/plugins/conftest.py` — `_apply_migrations` now delegates to
  `plugins.takyon.db.runner.run_migrations` (one definition, no drifting copy; the suite validates the
  real runner for free). Imported lazily to keep conftest side-effect-free at collection.

**Verification:** from `hermes-agent-main`, `TAKYON_TEST_PG_DSN=…@127.0.0.1:54329/takyon_test`, via
`scripts/run_tests.sh` (`-n 4`, hermetic): the connection-layer suites (db_runner + runtime_app) →
**9 passed** (3 + 6). `py_compile` of all four Python files clean. Because conftest now routes through
`run_migrations`, the ENTIRE existing `tests/plugins/` PG suite already exercises the production
runner (no separate proof needed). CI (no `TAKYON_TEST_PG_DSN`) SKIPS the PG-gated tests; the one
no-DB test (`test_build_without_database_url_raises`) runs everywhere.

**Not done / honest state:**
- **NOT the live runtime.** `build_runtime_app` serves the control router against PG, but the
  operator-gated Runtime Cutover (flip serving + live Supabase apply) has NOT happened. The live
  product/CEO paths still run on SQLite.
- **Only the CONTROL router is mounted here;** the Internal AI Gateway mount is the next increment (it
  reuses this host + conn factory).
- **Live Supabase apply remains blocked** on polsia2 teardown + backup + operator go-ahead. The runner
  has only touched local throwaway test DBs.

**Revert:**
```sh
rm hermes-agent-main/plugins/takyon/runtime_app.py
rm hermes-agent-main/plugins/takyon/db/runner.py
rm hermes-agent-main/plugins/takyon/db/__init__.py
rm hermes-agent-main/plugins/takyon/ai_provider.py
rm hermes-agent-main/tests/plugins/test_takyon_db_runner_pg.py
rm hermes-agent-main/tests/plugins/test_takyon_runtime_app_pg.py
git checkout -- hermes-agent-main/plugins/takyon/app_api.py    # undo the ai_provider rebind
git checkout -- hermes-agent-main/tests/plugins/conftest.py    # restore the inline migration loop
# The AI-gateway increment below depends on ai_provider.py + runtime_app.py — revert THAT one first.
# this log entry is additive — trim it back to the Phase-5e increment if reverting.
```

## Increment — Internal AI Gateway mounted (Phase 5 `/internal/ai-gateway/messages`): the broker a generated app spends through without ever holding the provider key

**Date:** 2026-05-30

**What:** Built `plugins/takyon/ai_gateway.py` — the `/internal/ai-gateway` router (POST `/messages`)
— and mounted it into `runtime_app.py`. A generated app presents its OWN `tkg_` gateway key as
`Authorization: Bearer`; the gateway resolves it to a `business_slug` (and to **nothing else** —
never another tenant, never the provider key), meters the spend through THE ONE `app_usage`
reserve→settle/release gate, and only then calls the SHARED platform provider key server-side via a
closure seam. The provider key is resolved here and **never appears in any response**. Also registered
the `generate` rail's concrete wiring in `core.py` `PRODUCT_RUNTIME_RAILS` (its `worker_contract`) and
reflected it in the `takyon-app-runtime` SKILL.md — so Hermes builds products that correctly connect,
with **no hidden backend path** the CEO can't see. This is the deferred live mount that Phase 5e's
"Not done" section explicitly named.

**Why (mediationplan Phase 5 acceptance — "generated app never holds provider key" + Phase 5 / Runtime
Cutover — "Gateway resolves business → policy → reserves billing → calls the shared provider key →
settles"):** 5e built the `tkg_` boundary primitive (mint/resolve); 5c built the one usage gate; the
prior increment extracted the provider leaf. This **composes** them into the actual broker. It is the
Postgres successor to the SQLite `/generate` route (`app_api.py`).

**Gate-1 finding (inspect-before-build):** the SQLite `/generate` (`app_api.py:395`, calling the
platform key directly via `_anthropic_key()`) is the predecessor; the gateway-key boundary
(`app_gateway_keys.resolve_gateway_key`, 5e), the one usage gate (`app_usage.reserve/settle/release`,
5c), and the provider leaf (`ai_provider.py`, prior increment) all already exist. *Decision:*
**COMPOSE** them in a new strategy-free router (house style mirrors `control_api.py`: a
`build_*_router()` factory + `get_gateway_conn` / `get_provider_caller` seams the host overrides) —
**not** a new gate, **not** a second provider impl. Two deliberate hardenings over the SQLite path:
(1) spend is gated by the **atomic reserve-under-row-lock**, not the old read-then-act budget mirror
that N concurrent calls could all slip past; (2) the cost estimate the cap is checked against is
computed **SERVER-SIDE** from the request payload — a caller can no longer under-declare
`estimated_cost_microusd` to duck the cap. For the `generate` rail: **UPDATED the existing rail's
`worker_contract`** in the canonical `PRODUCT_RUNTIME_RAILS` registry rather than adding a parallel
`ai_gateway` rail (CLAUDE.md: one canonical rail registry; do not create a second per-skill list).

**Gate-2 finding (credentials/providers):** **none new.** The gateway calls the already-present SHARED
platform Anthropic key, resolved server-side by `ai_provider.anthropic_key()` and bound into a closure
— never an argument the app supplies, never returned. The app side holds only its internally-minted
`tkg_` key (5e, no external account). **Invariant #8:** when no provider key is configured
`get_provider_caller()` returns `None` → the endpoint BLOCKS with **503 `provider_unconfigured`** and
**nothing reserved**; it never calls keyless and never fabricates a completion. The one outstanding
credential remains `STRIPE_BILLING_WEBHOOK_SECRET` (Phase 3), untouched.

**Decisions (and the deliberate choices):**
- **THE gate, once.** `reserve_usage` holds the server-side estimate atomically under the budget row
  lock (the only refusal points: `AppBudgetInactive` → 402, `AppBudgetExceeded` → 402). A **fresh
  `uuid4` reservation_key per request** — an internal reserve↔settle correlation id, NOT a client
  retry key — so there is no replay path that calls the provider twice against one settle.
- **Failure releases, success settles at TRUE cost.** On ANY provider failure → `release_usage`
  (recorded `failed`, zero spend, committed drops back to 0) + **502**; on success → `settle_usage` at
  the true provider cost. Settle never re-checks the cap — the money is already spent and recording
  truth is mandatory (invariant #8).
- **Never-leak-key projection.** The response is built ONLY from the provider response + computed costs
  — exactly `{success, text, content, model, usage}` — so no key, no business slug, no internal id can
  appear.
- **Ordering of refusals.** 503 (provider unconfigured) is checked AFTER auth (an unauthenticated
  caller can't probe provider config) and BEFORE reserve (a config gap never churns the budget). Bad/
  empty body → 400 before reserve. Unknown `app_user_id` → 400 (`AppUserNotFound`). Malformed /
  unknown / revoked / wrong-keyspace key → one undifferentiated **401** (a per-user `tk_` key is in a
  disjoint keyspace and is rejected before any DB lookup).
- **`get_provider_caller` left at its production default** in `runtime_app.py` (resolves the real
  shared key); only tests override it. The control conn factory serves the gateway seam too.

**Files created:**
- `hermes-agent-main/plugins/takyon/ai_gateway.py` — `build_ai_gateway_router()` (POST
  `/internal/ai-gateway/messages`), the `get_gateway_conn` + `get_provider_caller` seams,
  `_gateway_principal` (Bearer → `resolve_gateway_key` → 401), `ProviderCaller` type.
- `hermes-agent-main/tests/plugins/test_takyon_ai_gateway_pg.py` — **12** tests on real PG through the
  SAME `build_runtime_app` mount production uses: resolves/reserves/settles (response keyset exact;
  ledger event `completed`, actual==600, `route==internal_ai_gateway`; committed==600); provider key
  never in response (secret-in-closure → `secret not in resp.text`); 503 blocks with nothing reserved
  + the default seam returns `None` keyless; 402 budget exceeded / inactive (paused); 502 provider
  error releases (event `failed`, committed back to 0); 400 bad body / unknown app_user; 401 missing
  bearer / unknown well-formed key / per-user `tk_` key rejected.

**Files changed:**
- `hermes-agent-main/plugins/takyon/runtime_app.py` — `include_router(build_ai_gateway_router())` +
  `dependency_overrides[get_gateway_conn] = control_conn`; `get_provider_caller` deliberately NOT
  overridden (production default). Docstring updated to note the gateway is now mounted.
- `hermes-agent-main/plugins/takyon/core.py` — the `generate` rail's `worker_contract` in
  `PRODUCT_RUNTIME_RAILS` now states the concrete wiring (POST `/internal/ai-gateway/messages`;
  `Authorization: Bearer tkg_…`; the app holds ONLY the gateway key, never the platform provider key;
  402 = out-of-credit, 503 = generation-not-configured, never fake a completion; treat the returned
  `{text, content, model, usage}` as the only source of truth for output + spend). **Surgical edit on
  the rail block only — `core.py` also carries the operator's unrelated uncommitted meta-ads hunks, so
  any commit must stage ONLY the generate-rail hunk via `git add -p`, never `git add core.py`.**
- `hermes-agent-main/skills/takyon/takyon-app-runtime/SKILL.md` — 4 targeted additions documenting the
  gateway-backed, budget-metered generation rail, the 402/503 semantics, and the never-hold-provider-
  key discipline; selected by including `generate` in the surface contract `runtime_features`.
  Frontmatter untouched (YAML still parses).

**Verification:** from `hermes-agent-main`, `TAKYON_TEST_PG_DSN=…@127.0.0.1:54329/takyon_test`, via
`scripts/run_tests.sh` (`-n 4`, hermetic): the gateway suite → **12 passed**; all three wiring suites
together (gateway + runtime_app + db_runner) → **21 passed**. Full `tests/plugins/` → **1081 passed,
2 failed**. The 2 failures are the **same pre-existing, unrelated** pair as 5c–5e —
`test_business_work_focus_persists_and_blocks_cross_lane_writes` (core.py artifact-path, from the
operator's separate uncommitted `distribution/meta-ads` working-tree edits) and the web-search
`test_all_seven_plugins_present_in_registry` (a change-detector snapshot expecting 7; committed `xai`
makes 8 — the failure literally prints "Left contains one more item: 'xai'"). Proven not-mine: neither
test imports `ai_gateway` / `runtime_app` / `ai_provider` or the PG `pg_conn` fixture.
`_runtime_ui_contract_block({"runtime_features": ["generate"], …})` renders the gateway lines
(confirmed output contains `/internal/ai-gateway/messages`, `tkg_`, "never returns it"). SKILL.md
frontmatter parses (takyon-app-runtime v1.0.0). `py_compile` of ai_gateway / runtime_app / core clean.

**Not done / honest state:**
- **NOT the live product path.** The gateway serves on `build_runtime_app`, but the operator-gated
  Runtime Cutover (flip serving + live Supabase apply) has NOT happened. The SQLite `/generate`
  (`app_api.py`) still serves live product AI until the product surface is re-pointed.
- **No `tkg_` key is injected into a generated app yet** — minting a business key and wiring it into a
  generated product app's runtime config belongs to the build-product surface, done at cutover. This
  increment proves the broker; the CEO-visible rail contract tells the product-site worker HOW to call
  it.
- **Per-user sub-budgets not enforced here.** `app_user_id` / `app_user_tier` are passed through to
  the reserve for per-user attribution, but the business-level budget is the cap; per-user limits are
  out of scope for this increment.
- **Live Supabase apply remains blocked** on polsia2 teardown + backup + operator go-ahead.

**Phase 5 live broker complete.** The credential boundary (5e) + the one usage gate (5c) + the shared
provider leaf now compose into a mounted `/internal/ai-gateway/messages` that a generated app reaches
with only its `tkg_` key — and the CEO sees exactly how to build against it (rail `worker_contract` +
the owning skill). The remaining transitional steps are the operator-gated serving flip + live
Supabase apply, before Phase 8 retires SQLite.

**Revert:**
```sh
rm hermes-agent-main/plugins/takyon/ai_gateway.py
rm hermes-agent-main/tests/plugins/test_takyon_ai_gateway_pg.py
# remove the gateway import + the 2 mount lines from runtime_app.py
#   (include_router(build_ai_gateway_router()) and dependency_overrides[get_gateway_conn] = control_conn);
#   this leaves the connection-layer increment (control router on PG) intact.
git checkout -p hermes-agent-main/plugins/takyon/core.py   # drop ONLY the generate-rail hunk (keep operator meta-ads hunks)
git checkout -- hermes-agent-main/skills/takyon/takyon-app-runtime/SKILL.md
# this log entry is additive — trim it back to the connection-layer increment if reverting.
```

## Increment — Worker plane + scheduled CEO wakes (Phase 6): an at-least-once, budget-gated job queue, and recurring wakes as due-rows enqueued into that SAME queue

**Date:** 2026-05-30

**What:** Built the Postgres-native **worker plane** (mediationplan.md > Worker Plane) in three coupled
pieces, all additive and inert until a caller is wired at cutover:
- **migration 0010** — `jobs` (the at-least-once queue: one job, one worker via `FOR UPDATE SKIP
  LOCKED`; `idempotency_key UNIQUE` dedup; a CHECKED `queued→running→completed|blocked|failed|cancelled`
  lifecycle; `reserved_billing_entry_id` as the flow-A reservation back-reference; bounded
  `attempts`/`max_attempts`), `wake_schedules` (one recurring CEO-wake row per business; `next_run_at`
  is the dispatcher's cursor), and the in-DB `dispatch_due_wakes()` function (enqueue-when-due **then**
  advance, atomically, in one statement over a `FOR UPDATE SKIP LOCKED` `due` set).
- **`plugins/takyon/jobs.py`** — the queue ops + the ONE budget-gated execution contract: `enqueue`
  (idempotent), `get_job`/`list_jobs`, `claim_one` (skip-locked → 'running', attempts++), `complete`/
  `block`/`fail` (atomic terminal transitions; the row is its own receipt), `requeue_stale` (crash
  recovery), and `run_one` (claim → handler-lookup → reserve on the OWNER's flow-A account → run →
  settle/complete | block | refund+fail).
- **`plugins/takyon/wakes.py`** — `wake_schedules` CRUD (`upsert_wake_schedule`, `get`, `list`,
  `set_enabled`) + a thin `dispatch_due_wakes(conn)` caller of the in-DB function.

Also **struck every Modal reference from `mediationplan.md`** (operator directive: "we weren't ever
going to use Modal") — the 4 "Modal (later)" mentions are replaced with provider-neutral framing: the
**runtime worker drains the queue under one job contract — the VPS now, scaled out to N stateless
workers later; there is no external job runner** (heavy/build jobs run on the same contract, just on a
worker with more headroom).

**Why (mediationplan Worker Plane, line 124 + acceptance, line 234):** heavy/recurring work must run as
durable, idempotent, budget-gated jobs, and a missing config/credential must **block with a reason**,
never fabricate a completion (invariant #8). Scheduled CEO wakes are **not** a second mechanism — they
are due-rows enqueued into the SAME `jobs` queue the worker already drains, so there is no systemd
timer, no `.takyon/cron/jobs.json`, no `.tick.lock`.

**Gate-1 finding (inspect-before-build — repo AND live backend):** `policy.py` already emits
`PolicyDecision(outcome="job", estimate_cents=…)` (`policy.py:328`) with **no consumer** — this queue
is that consumer (policy DECIDES; the worker RESERVES). `billing.py reserve(…, *, business_slug,
job_id)` already takes `job_id` (`billing.py:180`), so the worker reuses the flow-A
reserve→settle/refund engine **unchanged** — `jobs.reserved_billing_entry_id` is the back-reference;
**no new money path**. Read-only live Supabase catalog check (105 public tables): the exact names
`jobs` and `wake_schedules` are **ABSENT** (collision-free); the polsia2-era analogs (`cron_jobs` which
conflates schedule+lock+status, `business_ceo_wakeups`, `workflow_jobs`, `media_generation_jobs`) are
disposable/orphaned and are **NOT read or migrated** — this design SEPARATES schedule
(`wake_schedules`) from queue (`jobs`), the correct single-path REPLACE. The legacy FILE cron
(`cron/scheduler.py`, `cron/jobs.py`, `gateway/run.py::_start_cron_ticker`) is SQLite-era and is
retired in **Phase 8**, NOT here; 0010 installs the Postgres replacement ALONGSIDE it. *Decisions:*
`jobs`/`wake_schedules` + `jobs.py`/`wakes.py` are **NEW**; the budget gate **EXTENDS** `billing.py`;
the job decision **CONSUMES** `policy.py`'s existing `outcome="job"`; wakes **REPLACE** the legacy file
cron (coexisting until Phase 8). REPLACE guards mirror 0001–0009 (fail loud on a differently-shaped
pre-existing table, keyed on `jobs.reserved_billing_entry_id` and `wake_schedules.next_run_at`).

**Gate-2 finding (credentials/providers): NONE new.** Dispatch is gated by **`CRON_SECRET`** (already
provisioned). Heavy jobs run on the **runtime worker** (no external job runner — Modal struck per the
operator directive above). pg_cron is an in-DB Supabase extension enabled at cutover; equivalently a
`CRON_SECRET`-bearer endpoint runs the identical `select dispatch_due_wakes()` on an interval. No new
account, key, or paid service. The one outstanding credential remains **`STRIPE_BILLING_WEBHOOK_SECRET`**
(Phase 3), untouched.

**Decisions (and the deliberate choices):**
- **One job, one worker.** `claim_one` locks the oldest queued row with `FOR UPDATE SKIP LOCKED`, then
  flips it 'running' in the same transaction — two workers (or a pg_cron overlap) never pick the same
  row. The skip-locked guarantee is proven against a second live connection holding the lock.
- **At-least-once + idempotent.** `enqueue` is `on conflict (idempotency_key) do nothing`; a replay
  returns the EXISTING row unchanged (one effect, original payload preserved).
- **The worker RESERVES; policy only DECIDED.** `run_one` reserves `payload.estimate_cents` on the
  owner's flow-A account under a **per-attempt** key `job:<id>:<attempts>`. A reserve the buckets can't
  cover ⇒ `block('budget_exhausted')` and **nothing runs** (invariant #8). On handler success →
  `settle` at the TRUE cost (clamped ≤ reserved, remainder released) + `complete`; on handler raise →
  `refund` the whole hold + `fail`. **Stale-hold reconciliation:** before reserving, `run_one` refunds
  any prior attempt's outstanding hold (idempotent), so a crash-mid-job reservation can never leak
  across retries.
- **Retries are BOUNDED.** `fail` re-queues only while `attempts < max_attempts`, else terminal
  'failed' — an exhausted budget or a permanently-failing job stops, never loops. `requeue_stale`
  recovers a crashed worker's stranded 'running' job (or blocks it `'stalled_max_attempts'` at the
  bound).
- **The job row is its own receipt.** `status` + `result`/`error` are written ATOMICALLY with the
  terminal transition; `complete`/`block`/`fail` only act on a 'running' row (single-writer — the
  claimer holds it) and raise `JobNotRunning` rather than overwrite a terminal row.
- **The work is a SEAM.** `run_one(…, handlers: Mapping[str, Handler])` — the host wires real handlers
  (a `ceo_wake` handler that runs a CEO turn, a build handler, …); tests inject deterministic stubs.
  This mirrors the AI gateway's `get_provider_caller` seam: the engine (claim, budget, lifecycle) is
  real and tested on real Postgres; only the leaf side effect is injected.
- **The wake schedule lives in the table, advanced ONLY by dispatch.** `dispatch_due_wakes()` enqueues
  one job per due row keyed `wake:<slug>:<YYYYMMDDHH24MI>` of the *scheduled* time (window idempotency
  via the jobs unique key) and advances `next_run_at = greatest(now(), next_run_at) + interval` (the
  catch-up bound: a host down for N intervals fires ONE enqueue and realigns to now, never an N-deep
  backlog) — both effects in one statement so a crash can't split enqueue from advance. `upsert`
  preserves the cursor on update (same bound param coalesced to `now()` on INSERT, to the existing
  cursor on UPDATE) unless an explicit `next_run_at` is passed; `set_enabled` pauses/resumes without
  moving it.

**Files created:**
- `hermes-agent-main/plugins/takyon/db/migrations/0010_jobs_and_wakes.sql` — the two tables, three
  indexes (`jobs_queued_idx` partial on `status='queued'`, `jobs_business_idx`, `wake_schedules_due_idx`
  partial on `enabled`), two fail-loud REPLACE guards, and `dispatch_due_wakes()`.
- `hermes-agent-main/plugins/takyon/jobs.py` — pure-leaf queue + execution engine (`Job`/`JobRunResult`/
  `JobOutcome`, `enqueue`/`get_job`/`list_jobs`/`claim_one`/`complete`/`block`/`fail`/`requeue_stale`/
  `run_one`; `Handler` seam type).
- `hermes-agent-main/plugins/takyon/wakes.py` — pure-leaf `WakeSchedule` + `upsert_wake_schedule`/`get`/
  `list`/`set_enabled`/`dispatch_due_wakes`.
- `hermes-agent-main/tests/plugins/test_takyon_jobs_pg.py` — **14** tests on real PG: enqueue
  idempotent; claim_one FIFO + never-double-claim + kind filter + **the real SKIP-LOCKED proof** (a row
  locked by a second live connection is skipped, then claimable on release); run_one
  reserve→settle→complete (ledger moves: `allowance_used==actual`, `reserved==0`); budget-exhausted
  blocks with nothing run/held (invariant #8); unknown-kind blocks ('no_handler', nothing reserved);
  zero-estimate completes without touching billing; handler-error refunds + fails; **bounded** retry
  then terminal-failed with no leak (handler ran exactly max_attempts times, never re-claimed after);
  empty-queue → None; requeue_stale recovers a crashed job / blocks it at max attempts; **stale-hold
  released before the next reserve** (no cross-retry leak: `reserved==0`, only the true cost charged).
- `hermes-agent-main/tests/plugins/test_takyon_wakes_pg.py` — **9** tests on real PG: upsert creates
  due-now-by-default; update preserves the cursor unless explicit; rejects non-positive interval;
  set_enabled pauses dispatch + preserves cursor on resume; dispatch enqueues exactly one ceo_wake job
  (keyed on the scheduled minute) + advances the cursor + sets last_enqueued_at; window-idempotency
  replay collapses to zero new jobs; bounded one-shot catch-up after a multi-interval outage (realigned
  to ~now+interval, not a backlog); the dispatched wake drains through `run_one(kinds=['ceo_wake'])`;
  distinct minute-windows fire distinct jobs.

**Files changed:**
- `mediationplan.md` — **all 4 Modal references struck** (operator directive), replaced with
  provider-neutral "runtime worker drains the queue / scale to N stateless workers / no external job
  runner" framing at lines ~27, ~124, ~189, ~234. (Optional Temporal/Inngest noted only as an unused
  "if durable orchestration is ever actually needed" aside.)
- `IMPLEMENTATION_LOG.md` — this entry.

**Verification:** from `hermes-agent-main`, `TAKYON_TEST_PG_DSN=…@127.0.0.1:54329/takyon_test`, via
`scripts/run_tests.sh` (`-n 4`, hermetic): the jobs+wakes suites → **23 passed**. Full `tests/plugins/`
→ **1104 passed, 2 failed**. The 2 failures are the **same pre-existing, unrelated** pair as the Phase 5
increments — `test_business_work_focus_persists_and_blocks_cross_lane_writes` (core.py artifact-path,
from the operator's separate uncommitted `distribution/meta-ads` working-tree edits) and the web-search
`test_all_seven_plugins_present_in_registry` (a change-detector snapshot expecting 7; committed `xai`
makes 8 — the failure literally prints "Left contains one more item: 'xai'"). Proven not-mine: neither
imports `jobs`/`wakes` nor uses the `pg_conn` fixture. Because the conftest re-runs the FULL migration
chain (0001→0010) for every `pg_conn` test, all 1104 passing PG tests also prove 0010 applies cleanly
and breaks nothing downstream. `grep -ci modal mediationplan.md` → **0**.

**Not done / honest state:**
- **No caller is wired yet — the engine is inert.** Nothing in the serving path calls `run_one` or
  `dispatch_due_wakes`. The worker LOOP (a process calling `run_one` in a loop) and the dispatch
  TRIGGER (pg_cron, or a `CRON_SECRET` endpoint calling `select dispatch_due_wakes()`) are wired at the
  operator-gated cutover. This increment builds the queue + schedule + dispatch; it does not start a
  worker or schedule a tick.
- **No real handlers yet.** The `ceo_wake` handler (runs a CEO turn), build/deploy handlers, etc. are
  host wiring landed with the worker loop. Tests inject stubs through the SAME `handlers` seam
  production will use.
- **Legacy file cron still runs, untouched.** `cron/scheduler.py` + `jobs.json` + `.tick.lock` are
  SQLite-era; their retirement rides with the SQLite path in **Phase 8**. 0010 is the Postgres
  replacement installed alongside, not a retirement.
- **Live Supabase apply remains blocked** on polsia2 teardown + backup + operator go-ahead. 0010 is
  validated on local PG only. pg_cron is enabled at cutover; until then the `CRON_SECRET` endpoint runs
  the identical dispatch SQL.

**Phase 6 worker plane complete (engine).** The queue, the one budget-gated execution contract, and
the schedule+dispatch for recurring CEO wakes all exist and are proven on real Postgres — reusing the
flow-A billing engine and consuming policy's `outcome="job"` with no new money path and no new
credential. The remaining steps are the operator-gated worker loop + dispatch trigger at cutover, then
Phase 7 (externalize the filesystem) and Phase 8 (retire SQLite + the legacy file cron).

**Revert:**
```sh
rm hermes-agent-main/plugins/takyon/db/migrations/0010_jobs_and_wakes.sql
rm hermes-agent-main/plugins/takyon/jobs.py
rm hermes-agent-main/plugins/takyon/wakes.py
rm hermes-agent-main/tests/plugins/test_takyon_jobs_pg.py
rm hermes-agent-main/tests/plugins/test_takyon_wakes_pg.py
git checkout 07619804 -- mediationplan.md   # restore the 4 "Modal (later)" mentions struck in this commit
# this log entry is additive — trim it back to the AI-gateway increment if reverting.
```

---

## Increment — Externalize the per-business filesystem + the no-fleet proof (Phase 7): a stateless host that resumes a business from Postgres + an object store

**Date:** 2026-05-30

**What:** Built the Postgres-era **externalized filesystem** (mediationplan.md > Runtime Cutover step 4 +
Phase 7) as ONE pure leaf, additive and inert until a caller is wired at cutover:
- **`plugins/takyon/storage.py`** — the object-store leaf, shaped like `jobs`/`wakes` and seamed like the
  AI gateway's `get_provider_caller`:
  - `StorageBackend` (a put/get/delete/`list_digests` Protocol) + `get_storage_backend()` — ONE seam,
    selected by config: `LocalStorageBackend` (a real local-directory object store; the credential-free
    default + the CI tier + the literal "local disk = scratch" stand-in for the bucket) or
    `SupabaseS3StorageBackend` (Supabase Storage over its S3-compatible API, lazy `boto3`).
  - `sync_up`/`sync_down` — content-**digest incremental** (an unchanged file is skipped; only changed
    bytes move) and **integrity-checked** (a downloaded blob whose sha256 ≠ its recorded digest raises
    before it lands), reusing `core._safe_relpath`'s containment discipline so an object key can never
    escape the `<slug>/` prefix.
  - `with_business_workspace(...)` — the worker integration seam: sync-down on enter → yield scratch →
    sync-up on **clean** exit (default: mirror deletions); on an exception it does NOT sync up (crash
    discipline — a crashed run never clobbers the last good remote state).

**Why (mediationplan Runtime Cutover step 4/6/7, lines 166/168/169 + Phase 7 acceptance, line 236):** the
per-business workspace lives only on the local disk of whichever box ran the CEO, which makes the host
*stateful* — a second runtime can't resume a business, and the VPS can't be made disposable. Externalizing
the workspace to an object store turns the contract into sync-down → run → sync-up so a second host
resumes from **Postgres (identity/jobs/ledger/schedule) + the object store (files)** — the no-fleet proof.
A missing Storage credential must **block with a reason**, never silently fall back or fake a "synced"
result (invariant #8).

**Gate-1 finding (inspect-before-build — repo AND backend):** the per-business filesystem today is
**local disk only** — `TakyonStore.root` (`$TAKYON_HOME`) `/ "businesses" / <slug>` over four canonical
roots (`product`/`distribution`/`research`/`metrics` = `TAKYON_BUSINESS_ROOTS`, core.py:58), seeded at
`business.upsert` (core.py:4940), read/written at **~40+ direct call sites** via `_business_root` /
`_resolve_business_file` (containment) / `_atomic_write_text` / `_append_jsonl`, with canonical-relpath
aliasing in `_canonical_business_relpath` (core.py:2038). A grep for any Storage/S3/sync code
(`supabase storage`, `boto3`, `s3`, `put_object`, `sync_down/up`) finds **none** — the only `storage`
hits are doc comments + `tools/tool_result_storage.py` (`/tmp` tool-output cache, unrelated). PG
migrations 0001–0010 have **no `files`/storage table**. *Decisions:* **ADD** a new pure-leaf `storage.py`
(nothing to extend/dedupe); **REPLACE-on-top, not a third system** — it externalizes the SAME four-root
taxonomy core.py owns, and the SQLite local-disk path stays until Phase 8 (the transitional dual-write
must not settle into permanent coexistence). **No DB migration / no new table** — the bucket is the
source of truth for file bytes + their listing; Postgres stays the source of truth for
business/jobs/ledger/schedule. (A PG `business_files` manifest was considered and **rejected**: it
duplicates the store's own listing and adds a bucket↔table two-write drift hazard.)

**Gate-2 finding (credentials/providers): NEW credential for the LIVE backend — recorded, not bandaided.**
This **corrects** the plan's optimistic Providers note ("Supabase Storage … already provisioned, no
separate S3/'F3' needed"): the Supabase *project* is provisioned (we hold `DATABASE_URL` /
`MIGRATION_DATABASE_URL`, confirmed the only DB/storage names in `secrets/.env`), so no separate
object-store *vendor* is needed — **but `DATABASE_URL` is a Postgres SQL connection and CANNOT read/write
Storage blob bytes.** Live sync needs NEW Supabase Storage access keys, absent from `secrets/.env` today:
the **recommended S3-compatible quad** `SUPABASE_S3_ENDPOINT` + `SUPABASE_S3_REGION` +
`SUPABASE_S3_ACCESS_KEY_ID` + `SUPABASE_S3_SECRET_ACCESS_KEY` (+ our own `TAKYON_STORAGE_BUCKET`), or the
REST alternative `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` (+ bucket). The live S3 backend lazy-imports
`boto3` only when selected. **The mechanism is fully buildable + proven with NO new credential** (the
local backend + the no-fleet proof run offline); only the live wire-up is gated. Recorded in
mediationplan.md Gate 2 + a reminder to the operator below. (`STRIPE_BILLING_WEBHOOK_SECRET` from Phase 3
remains the other outstanding operator-pending key.)

**Decisions (and the deliberate choices):**
- **One seam, two impls — not a second code path.** `local` and `supabase_s3` satisfy the same
  `StorageBackend` contract; `get_storage_backend()` picks by `TAKYON_STORAGE_BACKEND` exactly like a
  provider selector. The local backend is a REAL object store (the credential-free + CI + scratch tier),
  not a fake/stub — so test mode and live differ only by which backend the seam returns, with the same
  gates.
- **Invariant #8 is the activation gate.** `supabase_s3` is an EXPLICIT opt-in; selected while
  unconfigured (creds or `boto3` missing) → `StorageUnconfigured` (a `blocked`-with-reason that names the
  missing creds), **never** a silent downgrade to local and never a fake "synced."
- **Incrementality + integrity are first-class (robustness #1).** Sync compares sha256 digests (one
  digest space across backends; the local backend computes by reading so the listing is exactly the bytes
  on disk, no drift-prone sidecar), moves only changed bytes, and **verifies every downloaded blob's
  sha256 before writing it** — a corrupt/tampered object raises rather than landing.
- **Mirror semantics are explicit + safe-by-default.** The raw `sync_up`/`sync_down` are additive
  (`delete_remote`/`delete_local` default False — a partial tree can't delete good state). The
  worker-facing `with_business_workspace` opts into mirror deletion on a CLEAN run only.
- **Crash discipline.** `with_business_workspace` syncs up only on normal exit; an exception propagates
  WITHOUT sync-up, so the requeued job (Phase 6) re-syncs the last good remote tree — the externalized
  filesystem and the at-least-once queue compose.
- **Containment is a durable hardcode.** `_safe_rel` (mirrors `core._safe_relpath`: no absolute, no
  empty/`.`/`..` segments, depth-capped) + the local backend's resolved-root check make a key escaping the
  business prefix impossible; a 256 MiB per-object cap bounds memory.

**Files created:**
- `hermes-agent-main/plugins/takyon/storage.py` — the pure-leaf object-store seam (`StorageBackend`,
  `LocalStorageBackend`, `SupabaseS3StorageBackend`, `get_storage_backend`, `sync_up`/`sync_down`,
  `with_business_workspace`, `digest_bytes`, `object_prefix`; `StorageError`/`ObjectNotFound`/`UnsafePath`/
  `StorageUnconfigured`). No import of the SQLite-coupled `core`; no psycopg; no global state.
- `hermes-agent-main/tests/plugins/test_takyon_storage_pg.py` — **22** tests: **the no-fleet proof**
  (a real PG business; host A writes the four-root workspace + a binary blob and syncs up; host B — a
  fresh EMPTY scratch dir that learns the slug only from `select slug from businesses …` — resumes
  BYTE-IDENTICAL from the store); two-business store isolation; digest incrementality (re-up skips all,
  one changed file moves alone, the revised bytes resume); a `_LyingBackend` proving sync_down refuses a
  digest-mismatched blob and lands nothing; mirror-delete vs. safe additive default; path containment
  (5 escape keys + 5 unsafe slugs rejected, case-normalization allowed); backend selection (local default;
  `supabase_s3` unconfigured → `StorageUnconfigured` naming the missing creds; unknown kind rejected);
  `with_business_workspace` syncs down→up on clean exit and does NOT sync up on an exception.

**Files changed:**
- `mediationplan.md` — added the **Phase 7 Gate 2 entry** (the new Supabase Storage credential need, with
  the S3 quad / REST alternative + the operator provisioning step + the corrected "already provisioned"
  claim) and the **Phase 7 gate finding** paragraph (Gate-1 inventory + decisions, in the same house style
  as Phases 3–5). Both written BEFORE any code, per the standing gate discipline.
- `IMPLEMENTATION_LOG.md` — this entry.

**Verification:** from `hermes-agent-main`, `TAKYON_TEST_PG_DSN=…@127.0.0.1:54329/takyon_test`, via
`scripts/run_tests.sh` (`-n 4`, hermetic, all credential env unset): the storage suite → **22 passed**.
Full `tests/plugins/` → **1126 passed, 2 failed** (= the prior 1104 baseline + these 22). The 2 failures
are the **same pre-existing, unrelated** pair tracked since Phase 5 — `test_business_work_focus_persists…`
(core.py artifact-path, from the operator's separate uncommitted `distribution/meta-ads` working-tree
edits) and the web-search `test_all_seven_plugins_present_in_registry` (a change-detector snapshot
expecting 7; committed `xai` makes 8 — it literally prints "Left contains one more item: 'xai'"). Proven
not-mine: neither imports `storage` nor the new test. The local backend + no-fleet proof run with NO new
credential and the live backend's invariant-#8 block is proven without `boto3` or live Supabase.

**Not done / honest state:**
- **No caller is wired yet — the leaf is inert.** Nothing in the serving path calls `sync_down`/`sync_up`
  or wraps the worker's per-job run in `with_business_workspace`. That integration (sync-down → run →
  sync-up around `jobs.run_one`) is the operator-gated cutover step, exactly as Phase 6 left the worker
  loop unmounted. core.py still reads/writes the workspace on local disk via `_business_root`.
- **The `supabase_s3` backend is wired but UNVERIFIED against live Supabase.** No live Storage creds
  exist in this environment, so its boto3 calls (`put_object`/`get_object`/`list_objects_v2`+`head_object`/
  `delete_object`) are cutover-ready code, NOT a tested path. The local backend is the verified one. The
  operator must provision the Gate-2 keys and a private bucket before live cutover; absent them the path
  blocks with a reason (proven), it does not fake.
- **Local disk still authoritative until cutover.** This increment externalizes the mechanism and proves
  resume; it does not flip core.py off local disk. The VPS is not yet demoted/disposable — that follows
  once the cutover wires the sync around the worker and selects the live backend.
- **Live Supabase apply/cutover remains blocked** on polsia2 teardown + backup + operator go-ahead
  (unchanged). No new migration was added by this phase.

**⚠️ Operator action — NEW credential to provide before LIVE filesystem sync (Phase 7):** create a
**private Supabase Storage bucket** (e.g. `business-workspaces`) and, under Storage → S3 Access Keys,
generate an access key; then add to `secrets/.env`: `SUPABASE_S3_ENDPOINT`
(`https://<ref>.storage.supabase.co/storage/v1/s3`), `SUPABASE_S3_REGION`, `SUPABASE_S3_ACCESS_KEY_ID`,
`SUPABASE_S3_SECRET_ACCESS_KEY`, and `TAKYON_STORAGE_BUCKET`. (Outstanding keys overall:
`STRIPE_BILLING_WEBHOOK_SECRET` from Phase 3 + these Storage keys.)

**Phase 7 mechanism complete (leaf + proof).** The externalized filesystem — one provider-neutral seam, a
real credential-free local backend, digest-incremental + integrity-checked sync, and a wired-but-unverified
Supabase S3 backend that blocks-with-reason when unprovisioned — exists and is proven on real Postgres:
a second host on an empty disk resumes a business byte-identically from Postgres + the store. The remaining
steps are the operator-gated cutover (wire the sync around the worker, select the live backend, provision
the Storage keys), then Phase 8 (retire SQLite + the legacy file cron + the local-disk-authoritative path).

**Revert:**
```sh
rm hermes-agent-main/plugins/takyon/storage.py
rm hermes-agent-main/tests/plugins/test_takyon_storage_pg.py
git checkout 5e535934 -- mediationplan.md   # restore the pre-Phase-7 plan (drops the Phase 7 Gate-2 entry + gate finding)
# this log entry is additive — trim it back to the Phase 6 increment if reverting.
```

---

## Increment — Phase 7 follow-up: Supabase Storage credential provisioned in-browser + live-verified (2026-05-31)

**What:** The operator signed in to Supabase; I then provisioned the Phase-7 Storage credential in their
browser and verified it against live Supabase Storage. Created a **private** bucket `business-workspaces`
on project four-manifold-prod (ref `ddftvmjpfghfrdxhavvp`, region `us-east-2`) + an S3 access key named
`takyon-business-workspaces`. Wrote the four `SUPABASE_S3_*` values + `TAKYON_STORAGE_BUCKET` into
`secrets/.env`, leaving the selector `TAKYON_STORAGE_BACKEND` **commented** (default `local`, so the leaf
stays inert).

**Why:** Closes the Phase-7 Gate-2 outstanding credential AND the "wired but unverified against live
Supabase" caveat the Phase-7 increment explicitly left open. Robustness #1: prove the operator-provided
creds work end-to-end through the real code path while the keys are fresh, rather than discovering a bad
key at cutover. The credential is present but stays inert until the operator-gated cutover flips the
selector — no silent activation.

**Verified (LIVE, not mocked):** a throwaway smoke test drove the REAL
`storage.py::SupabaseS3StorageBackend` (selected via `TAKYON_STORAGE_BACKEND=supabase_s3` for that one
process only, creds read straight from `secrets/.env`) through **put → get (sha256 integrity match) →
list_digests (digest round-tripped through object metadata) → delete → list-empty** against live Supabase
Storage. Result: `LIVE SMOKETEST PASS`; bucket left empty (no residue). `boto3 1.42.89` present in `.venv`.

**Files changed:**
- `secrets/.env` (TRACKED-but-never-committed): +4 `SUPABASE_S3_*` + `TAKYON_STORAGE_BUCKET` + a comment
  block; selector `TAKYON_STORAGE_BACKEND` left commented (inert). Values not printed anywhere.
- `mediationplan.md`: Phase-7 Gate-2 credential bullet marked **✅ PROVISIONED + LIVE-VERIFIED** with the
  bucket/ref/region/key facts + the 50 MB-vs-256 MiB config note.
- `plugins/takyon/storage.py`: `SupabaseS3StorageBackend` docstring corrected from "unverified" → live-
  verified 2026-05-31 (per-method `# pragma: no cover` markers kept; offline suite still doesn't hit net).

**Supersedes the Phase-7 increment's "Not done":** the "supabase_s3 backend is wired but UNVERIFIED" item
and the "⚠️ Operator action — Storage keys to provide" reminder are now **DONE**. Overall still
outstanding: `STRIPE_BILLING_WEBHOOK_SECRET` (Phase 3).

**Not done / honest state:**
- **Still inert** — no caller wired; `with_business_workspace` is not mounted around the worker; core.py
  still reads/writes the workspace on local disk. (unchanged from Phase 7)
- **Open config item, NOT yet actioned:** the bucket inherits Supabase's global **50 MB** upload limit
  while the client cap `MAX_OBJECT_BYTES` is **256 MiB**. Raise the global Storage upload limit to
  ≥256 MiB before live sync of large media, else objects between 50 MB and 256 MiB are rejected
  server-side. Left as an operator decision (it is a project-wide setting, not bucket-scoped).
- **Live apply / cutover still blocked** on polsia2 teardown + backup + operator go-ahead (unchanged).

**Revert:**
```sh
git checkout HEAD -- mediationplan.md hermes-agent-main/plugins/takyon/storage.py   # drop the 2026-05-31 truthfulness edits (back to 51d70d29)
# secrets/.env is never committed — manually remove the 5 SUPABASE_S3_*/TAKYON_STORAGE_BUCKET lines + their comment block.
# the live bucket `business-workspaces` + key `takyon-business-workspaces` persist in Supabase; delete the bucket / revoke the key in the dashboard to fully undo provisioning.
# this log entry is additive — trim it if reverting.
```

---

## Increment — Runtime Cutover step 1 (LIVE DDL): retired polsia2's colliding control tables + applied takyon migrations 0001–0010 to live Supabase (2026-05-31)

**What:** Executed the gated live schema cutover against the production Supabase project
**four-manifold-prod** (ref `ddftvmjpfghfrdxhavvp`, PostgreSQL **17.6**) over `MIGRATION_DATABASE_URL`
(direct 5432). Two documented steps, in order: (1) ran `plugins/takyon/db/retire_polsia2_public.sql`
in one transaction — it dropped polsia2's `public.billing_accounts` then `public.businesses` with
`cascade` (cleared **87** dependent FK *constraints*, left those ~88 dependent tables and their rows
intact); (2) applied migrations `0001`…`0010` via the canonical `db/runner.py::run_migrations` on an
autocommit psycopg connection (the SAME code path the test fixtures use — one definition of "the
schema"). Installed the takyon shape: identity spine (`users`, `user_api_keys`, takyon `businesses`),
both ledgers (flow A `billing_accounts`+`billing_entries`, flow B `custody_accounts`+`custody_entries`),
`api_rate_limits`, `app_execution_policies`, the full sub-user/app runtime (`app_users`,
`app_magic_links`, `app_sessions`, `app_entitlements`, `app_usage_events`, `app_budgets`,
`app_plan_policies`, `app_checkout_intents`, `app_checkout_sessions`, `app_revenue_events`,
`webhook_events`, `app_gateway_keys`), and `jobs` + `wake_schedules` + the in-DB `dispatch_due_wakes()`
function.

**Why:** The operator gave an explicit go-ahead for items 3+4 of the Phase-8 prerequisites and
**explicitly waived the Supabase backup** ("i dont want to back up live supabase i dont care about it" —
polsia2's rows are disposable per the Ground-Truth REPLACE decision). This is the "separate, gated live
apply" the Ground-Truth section left outstanding. Robustness #1: I still did the Gate-1 read-only
backend inventory FIRST and took a scoped local CSV of the only two tables being dropped as insurance,
rather than relying on the waiver alone.

**Verified (LIVE, against the production DB, not mocked):**
- **Pre-apply (read-only inventory):** `public.businesses` + `public.billing_accounts` present in
  **polsia2 shape** (no `owner_user_id` / no `allowance_included_cents`); all 10 takyon table names
  ABSENT; **105** public base tables; `profiles`/`agent_runs` present. So the 0001 guard would correctly
  RAISE until the teardown ran — confirming the retire-first ordering was required.
- **Post-apply:** all **23** takyon tables present, **every one 0 rows**, and takyon-shaped
  (`businesses.owner_user_id` ✓, `billing_accounts.allowance_included_cents` ✓,
  `webhook_events.provider_event_id` ✓); `dispatch_due_wakes()` exists; public base tables **105 → 125**.
- **No silent polsia2 shadow (the load-bearing safety check):** ALL 10 migrations carry a fail-loud
  REPLACE guard and ALL 10 applied with **no exception** → nothing bound to a pre-existing polsia2 table;
  and the empty-table census (0 rows on every takyon table) independently confirms no polsia2 data was
  shadowed in. A freshly-created table being empty is the definitive shadow test; every table passed.

**Files changed:** **none in the repo** — this increment is a **live-DB state change**, not a code edit
(the migrations + teardown + runner were already committed in earlier phases). Out-of-repo artifacts:
`/tmp/takyon_polsia2_predrop_backup/{businesses,billing_accounts}.csv` (32+15 = 47 rows, insurance for
the dropped tables; deliberately outside the repo, never committed). Doc updates accompanying this:
`mediationplan.md` Ground-Truth (live apply marked done); this log entry.

**Supersedes:** Ground-Truth's "the live Supabase apply is a *separate* step requiring (a) a fresh
Supabase backup/snapshot and (b) an explicit operator go-ahead" → **(b) given; (a) waived by the
operator** (insurance CSV taken instead). And the Cutover-mechanics "Still gated/outstanding … needs a
live `DATABASE_URL` read, which the operator has not yet authorized" → **authorized + executed** for the
two colliding roots + the 0001–0010 install. **Still outstanding (unchanged):** full retirement of the
*rest* of polsia2's `public` (the ~103 now-orphaned tables — `profiles`, `agent_runs`, the `business_id`
dependents) still needs a verified table inventory + a Supabase role/grant review; the teardown by design
retired ONLY the two takyon-colliding roots.

**Not done / honest state:**
- **This is Runtime Cutover step 1 (schema) ONLY.** The operator runtime — the `./takyon` shell + the
  dashboard — still instantiates `TakyonStore()` and serves **entirely from local SQLite**
  (`.takyon/state.sqlite3`). Nothing in the live shell reads these new Postgres tables yet. Item 4 ("flip
  serving") is NOT done; it is the full terminal build (rewire `core.py`'s ~40+ business-state/filesystem
  call sites onto the Postgres modules + Storage backend, wire the inert worker loop + `pg_cron`/
  `dispatch_due_wakes` + the Phase-7 sync-around-job, flip `TAKYON_STORAGE_BACKEND=supabase_s3`, full E2E
  through the real shell, the no-fleet proof, THEN delete the SQLite path).
- **No data migrated from polsia2** — its rows were disposable; the takyon tables start empty (fresh
  start, by the operator's REPLACE decision).
- `TAKYON_STORAGE_BACKEND` still commented (Storage backend inert; files still on local disk).
- `STRIPE_BILLING_WEBHOOK_SECRET` still outstanding (Phase 3) — flow-A billing webhook stays
  blocked-with-reason until provided.

**Revert:** the migrations are **forward-only** (no down-migration), so this live apply is effectively
irreversible without a snapshot — and the operator waived the snapshot. Partial undo only:
```sh
# polsia2's two dropped tables: data-only reconstruction from the insurance CSVs (the ~88 FK constraints are NOT auto-restored,
#   and the polsia2 table shape must be recreated first):
#   psql "$MIGRATION_DATABASE_URL" -c "\copy public.businesses from '/tmp/takyon_polsia2_predrop_backup/businesses.csv' csv header"
# the 23 takyon tables are empty; re-running retire_polsia2_public.sql will NOT drop them (guard refuses to nuke takyon-owned tables) — drop manually only if intentionally rolling back.
git checkout HEAD -- mediationplan.md   # drop the 2026-05-31 live-apply Ground-Truth marker
# this log entry is additive — trim it if reverting.
```

---

## Increment — Phase 8 step 1 (schema port, LOCAL-verified): 0011 operator-runtime migration + extended polsia2 teardown for the 3 operator-name collisions (2026-05-31)

**What:** Wrote `db/migrations/0011_operator_runtime.sql` — the *storage half* of the SQLite kill. It
(a) **ENRICHES `businesses`** to the operator shape (`goal`/`status`/`work_focus`/`budget_json`/
`metadata_json`/`updated_at`, a `businesses_work_focus_chk` CHECK on the closed enum `all|marketing|
product`, and two indexes) — same one slug-keyed 0001 row, purely additive; and (b) **PORTS the 10
SQLite-only operator tables** that `core.py:_init_db` owns but no prior migration covered: `workspaces`,
`agent_runs`, `ledger_entries`, `control_states`, `events`, `conversation_threads`,
`conversation_messages`, `idempotency_keys`, `app_surface_contracts`, and the operator work-request
record `business_work_requests` (the SQLite `jobs`, **ISOLATED** under a distinct name so it never
pollutes the 0010 worker-plane execution queue). Every ported table is preceded by a fail-loud REPLACE
guard keyed on a takyon-distinctive column (`businesses` needs none — 0001's identity guard already
protects it). Then **extended `db/retire_polsia2_public.sql`** with three more guarded drop blocks —
`agent_runs` (drop if no `scope`), `events` (drop if no `event_type`), `idempotency_keys` (drop if no
`operation_hash`) — for the polsia2 collisions live introspection found, so the teardown now retires
five colliding roots, not two.

**Why:** Phase 8 = kill SQLite. This increment gives the 10 operator tables a Postgres home so the *next*
increment (the Postgres-backed `TakyonStore` connection seam) has real tables to read/write; it is
ADDITIVE and does **not** flip serving or touch the worker plane. Design choices recorded in the
migration header and the mediationplan Phase-8 finding: **text timestamps / text JSON** for the 10 tables
(a 1:1 port of the SQLite store's ISO-string / `json.dumps` columns — the *only* reader is the ported
operator store, which speaks strings; `businesses` keeps timestamptz because its readers are the 0001
psycopg leaf modules), `ledger_entries.amount double precision` (= SQLite REAL), and **ISOLATE** rather
than reconcile `business_work_requests` (it is a work-*record*, never drained; the 0010 `jobs` is an
execution *queue*). The retire extension is required because live still carries polsia2's `agent_runs`
(70 rows), `events` (1274 rows), `idempotency_keys` (0 rows) among its ~103 orphaned tables — 0011's
guards would (correctly) RAISE on them, so the teardown must drop them FIRST at live-apply time.

**Verified (LOCAL Postgres 16.14 @ 127.0.0.1:54329, the real engine, not mocked):** ran through the
canonical CI-parity path `scripts/run_tests.sh` with `TAKYON_TEST_PG_DSN=postgresql://postgres@127.0.0.1:54329/postgres`,
which applies every migration via the SAME `db/runner.py::run_migrations` production uses (one definition
of "the schema").
- **`tests/plugins/test_takyon_operator_runtime_pg.py` (NEW, 5 tests):** after `run_migrations`, all 10
  operator tables exist each carrying its distinctive column; `businesses` carries all 6 enrich columns
  and still has `slug`/`owner_user_id` (not forked); the `work_focus` CHECK *accepts* all|marketing|
  product and *rejects* an out-of-enum value (`CheckViolation`); a `workspaces` insert takes its
  `kind`/`status` defaults and the `businesses` FK **cascade is real** (deleting the business deletes the
  workspace); and a polsia2-shaped `events` (has `kind`, no `event_type`) makes 0011 **FAIL LOUD**
  (`FeatureNotSupported`, "not the takyon shape") instead of `create-if-not-exists`-binding to it.
- **`tests/plugins/test_takyon_retire_polsia2_pg.py` (strengthened):** stands up all five polsia2-shaped
  roots + an innocent `workflow_runs` dependent; after the extended teardown, all five roots are dropped,
  `workflow_runs` SURVIVES with its FK cleared (cascade clears constraints, not dependent tables), and a
  full re-apply (0001/0002/**0011**) reinstalls the takyon shape incl. `agent_runs.scope`/
  `events.event_type`/`idempotency_keys.operation_hash`.
- **`tests/plugins/test_takyon_db_runner_pg.py`:** runner applies the full set (now incl. 0011) and is
  idempotent; the "reached latest migration" spot-check now also asserts an 0011 table (`control_states`).
- **Whole plugin suite:** `1131 passed`. The only 2 failures are **PRE-EXISTING and unrelated**, proven
  so: `web/test_web_search_provider_plugins.py` (registry now has an 8th provider `xai`; count assertion
  stale) and `test_takyon_plugin.py::test_business_work_focus_persists_and_blocks_cross_lane_writes` —
  which fails **identically with no PG DSN set** because it is a pure `TakyonStore(tmp_path)` *SQLite*
  test, and the mismatch is the operator's **uncommitted** `core.py:2034` route `("outreach/",
  "distribution/outreach/")` vs the uncommitted test still expecting un-prefixed `outreach/test.md`.
  Both live in the operator's in-flight workstream (`core.py`, `test_takyon_plugin.py` both show `M`);
  neither is touched here.

**Files changed:**
- `plugins/takyon/db/migrations/0011_operator_runtime.sql` (**NEW**) — the port migration above.
- `plugins/takyon/db/retire_polsia2_public.sql` — +3 guarded drop blocks (agent_runs/events/
  idempotency_keys); DESTRUCTIVE header + SCOPE note updated to five colliding roots.
- `tests/plugins/test_takyon_operator_runtime_pg.py` (**NEW**) — the 5 0011 tests above.
- `tests/plugins/test_takyon_retire_polsia2_pg.py` — retarget the "survives cascade" role to
  `workflow_runs`; add the 3 collisions as drop targets + their takyon-shape reinstall assertions.
- `tests/plugins/test_takyon_db_runner_pg.py` — "reached latest" now checks `control_states` (0011).
- `mediationplan.md` — the Phase-8 Gate-1 finding (written earlier this step).
- **NOT changed:** `core.py` (no seam yet — shell/dashboard still serve from SQLite), and **nothing
  applied to live**.

**Not done / honest state:**
- **Schema only; serving unchanged.** The `./takyon` shell + dashboard still instantiate
  `TakyonStore()` and serve entirely from local SQLite. 0011's tables have no reader yet — that is the
  next increment (the Postgres-backed store seam: `_connect`→psycopg, `?`→`%s`, dict_row, datetime↔ISO
  coercion, 2 `INSERT OR IGNORE`→`ON CONFLICT`, app_* divergence reconciliation).
- **Not applied to live (deliberate).** 0011 + the retire-of-3 are LOCAL-verified only. The gated live
  apply (run extended `retire_polsia2_public.sql` FIRST to drop polsia2's agent_runs/events/
  idempotency_keys, THEN apply 0011) is deferred until the seam is built, to avoid a half-state on live
  where the tables exist but nothing serves from them. Operator already gave the DDL go-ahead + waived
  the backup.
- `STRIPE_BILLING_WEBHOOK_SECRET` still outstanding (Phase 3). Storage 50 MB-vs-256 MiB upload-limit
  decision still open. Both unchanged.

**Revert (local only — nothing reached live):**
```sh
rm hermes-agent-main/plugins/takyon/db/migrations/0011_operator_runtime.sql
rm hermes-agent-main/tests/plugins/test_takyon_operator_runtime_pg.py
git checkout HEAD -- hermes-agent-main/plugins/takyon/db/retire_polsia2_public.sql \
                     hermes-agent-main/tests/plugins/test_takyon_retire_polsia2_pg.py \
                     hermes-agent-main/tests/plugins/test_takyon_db_runner_pg.py
# mediationplan.md also carries the Phase-8 Gate-1 finding from this step; `git checkout HEAD -- mediationplan.md` drops it too (additive doc).
# this log entry is additive — trim it if reverting.
```

---

## Increment — Phase 8 step 2a (LOCAL-verified): Postgres-backed `TakyonStore` connection seam — a translating `_PGConn` wrapper + `TAKYON_DB_BACKEND` switch (2026-05-31)

**What:** Gave the operator store a Postgres engine WITHOUT rewriting a single one of its ~150
`conn.execute` call sites. Three additive pieces in `core.py`, plus a test fixture and a test file:
- **`_db_backend()`** — reads `TAKYON_DB_BACKEND`, default `sqlite`, opt-in `postgres`. A separate,
  *explicit* flag (NOT auto-detected from `DATABASE_URL`) so a process that merely has `DATABASE_URL`
  set for the leaf modules does not silently flip the operator store. Parallels storage.py's
  `TAKYON_STORAGE_BACKEND`.
- **`class _PGConn`** — a thin psycopg adapter. The store speaks sqlite3: `conn.execute(sql, params)`
  with `?` placeholders, reads every row by column name. The wrapper translates `?` → `%s` (escaping
  any literal `%` → `%%` FIRST, and ONLY when params are bound — psycopg does no %-substitution on a
  paramless query), opens the connection `row_factory=dict_row`, and is a context manager exactly like
  sqlite3 so `with self._connect() as conn:` is one atomic transaction.
- **`_connect()` PG branch + `_connect_postgres()`** — `_connect` still `mkdir`s the per-business
  filesystem root (used on both backends), then forks: postgres → `_connect_postgres()`, else the
  unchanged sqlite block. `_connect_postgres` lazy-imports psycopg + the canonical
  `runtime_app.resolve_database_url` (so the default SQLite path stays dependency-free), connects
  `autocommit=False`, and does **NO schema bootstrap** — `db/runner.py` owns all DDL, so `_init_db`/
  `_migrate_db` are intentionally never called (their SQLite-only PRAGMA/ALTER wouldn't even parse on
  PG). `_PGConn.executescript` (only reachable from the skipped `_init_db`) RAISES if ever called —
  fail-loud rather than bootstrap a divergent schema (invariant #8).
- **`TakyonStore.__init__(..., *, database_url=None)`** — an explicit DSN the postgres path opens
  (tests point it at a throwaway DB); when `None`, `_connect_postgres` resolves `DATABASE_URL`/
  `POSTGRES_URL` via runtime_app. Unused on SQLite.
- **`tests/plugins/conftest.py`** — new `pg_store_dsn` fixture: yields a libpq conninfo STRING (via
  `make_conninfo`) for a fresh, migrated, per-test throwaway DB. The store opens its OWN connections
  from a URL, so the seam needs a string, not the live handle `pg_conn` hands back.
- **`tests/plugins/test_takyon_store_pg.py`** (NEW, 5 tests) — proves the SQLite-shaped SQL runs
  unchanged on real Postgres.

**Why:** Phase 8 = kill SQLite. Step 1 gave the 10 operator tables a Postgres home (0011); this step
gives them a *reader* — the smallest seam that lets the existing store serve from PG. A translating
wrapper, not a per-site `?`→`%s` edit, because the entire sqlite3 surface the store actually uses is
`execute` + one `executescript`/`row_factory` on the bootstrap path the PG backend skips: **zero**
positional row reads (so `dict_row` is a true drop-in for `sqlite3.Row`), **zero** literal `%` in any
store SQL (the one LIKE wildcard rides inside a bound parameter, which psycopg leaves untouched), no
`cursor`/`commit`/`rollback`/`executemany`/`create_function`/`lastrowid`. So one wrapper at the
connection boundary is faithful to every call site — parsimonious, and it keeps the SQLite path the
exact unchanged default.

**The one subtlety that drove the design — nested `with conn:` and psycopg-closes-on-exit.** The store
nests a transaction block (`with conn:` — `commit()` at core.py:4907, `upgrade_businesses` at 8158)
INSIDE the connection block (`with self._connect() as conn:`). sqlite3's `with conn:` only commits/
rolls back and never closes, so that nesting is harmless there. psycopg3's `__exit__` commits/rolls
back **AND CLOSES**. So `_PGConn` tracks `__enter__`/`__exit__` depth: only the OUTERMOST block drives
psycopg's real commit+close; inner blocks are no-ops that return falsy (so they don't suppress a
propagating exception — the outer block still sees it and rolls back). Safe because no code reads the
DB after an inner block exits (both nested sites just `return`), so collapsing both levels into the one
outer-managed transaction preserves the original atomicity (all writes commit together / roll back
together).

**Correction to the previous increment's prediction (honest):** the P8.1 "Not done" note predicted this
seam would also need "datetime↔ISO coercion, 2 `INSERT OR IGNORE`→`ON CONFLICT`, app_* divergence
reconciliation." That was an **OVER-PREDICTION** for the operator tables, now disproven by reading
source: (a) 0011 ports the 10 operator tables with `text` JSON + `text` timestamps as a deliberate 1:1
port of the store's ISO-string / `json.dumps` columns — so strings round-trip with **no coercion**;
(b) the store already emits `ON CONFLICT`, and 0011 carries the matching unique constraints/PKs
(`control_states(scope)` PK, `workspaces(business_slug,path)`, `conversation_threads`/`_messages
(business_slug,source,external_id)`, `idempotency_keys(key)` PK) — verified compatible, **no
`INSERT OR IGNORE` rewrite needed**. The app_* divergence is real but belongs to a DIFFERENT slice
(see Not done) — it is NOT part of this connection seam. Net: the operator-table seam is **pure
placeholder translation**, nothing more.

**Verified (LOCAL Postgres 16.14 @ 127.0.0.1:54329, the real engine, never mocked):** via the canonical
CI-parity `scripts/run_tests.sh` with `TAKYON_TEST_PG_DSN` set; each test runs against a fresh
throwaway DB migrated by the SAME `db/runner.py` production uses.
- **`tests/plugins/test_takyon_store_pg.py` — 5 passed:**
  - `test_default_backend_is_sqlite`: with the flag unset, `_connect()` returns a `sqlite3.Connection`
    — the switch is genuinely opt-in.
  - `test_backend_switch_returns_pg_wrapper_without_bootstrapping`: with the flag set, `_connect()`
    returns a `_PGConn`; a `?`-parametrized `dict_row` read works (row addressable by aliased name);
    `executescript` RAISES `RuntimeError("…must not run on the Postgres backend")`; and the guard table
    it tried to make is absent (`to_regclass('public.should_not_exist') is None`) — bootstrap really
    was skipped.
  - `test_multi_op_commit_round_trips_operator_tables`: ONE atomic `commit()` spanning six operator
    tables (workspace.upsert + ledger.allocate{amount 7, budget 25} + agent.record{completed} +
    conversation.message.record{exercises ON CONFLICT(business_slug,source,external_id), binds a NULL
    parent} + event.record); every row read straight back through raw psycopg (tuple rows, bypassing
    the store) confirms all writes are real AND committed by the outer transaction, including exactly
    one `idempotency_keys` row.
  - `test_global_control_set_round_trips`: a GLOBAL-scope `control.set`→`paused` round-trips
    `control_states` via `ON CONFLICT(scope)` (no business, so the CEO-cron sync path that imports
    `cron.jobs` is never entered); the store's own `read(scope=global)` returns the control row by name,
    proving reads round-trip through `dict_row` too.
  - `test_commit_idempotency_replay_is_one_effect`: re-committing the same `idempotency_key` returns the
    STORED original result verbatim (`second == first`) and writes NO new rows (one key row, one
    workspace row) — a genuine re-apply would PK-violate on the no-ON-CONFLICT `idempotency_keys`
    INSERT, so success on the second call IS the replay path. (Earlier this slice I'd mis-asserted on a
    `"idempotent": True` key; the replay path returns the stored `result_json`, which has no such key —
    fixed to `second == first` + DB counts. My assertion bug, not a seam bug.)
- **Whole plugin suite with the DSN set: `2 failed, 1136 passed`** (1131 + the 5 new). The 2 failures
  are **PRE-EXISTING and unrelated**, proven: `web/test_web_search_provider_plugins.py` (registry now
  has an 8th provider `xai`; stale count assertion — a change-detector test), and
  `test_takyon_plugin.py::test_business_work_focus_persists_and_blocks_cross_lane_writes` — which fails
  **identically with no DSN** because it is a pure `TakyonStore(tmp_path)` *SQLite* test, and the
  mismatch is the operator's **uncommitted** `core.py` work_focus path route vs the uncommitted test's
  expectation. Both live in the operator's in-flight workstream; neither is touched here.

**Files changed:**
- `plugins/takyon/core.py` — `_db_backend()` + `class _PGConn` (inserted before `class TakyonStore`),
  `__init__` gains the keyword-only `database_url`, `_connect()` gains the postgres fork, new
  `_connect_postgres()`. **My hunks only** — `core.py` also carries the operator's uncommitted
  meta-ads / work_focus changes, which are NOT mine and NOT part of this increment.
- `tests/plugins/conftest.py` — new `pg_store_dsn` fixture (additive; sits after `pg_conn_raw`).
- `tests/plugins/test_takyon_store_pg.py` (**NEW**) — the 5 seam tests above.
- **NOT changed:** 0011 (already landed step 1), the leaf modules, the shell/dashboard serving path,
  and **nothing applied to live**.

**Not done / honest state:**
- **Seam only; serving unchanged.** `_connect()` defaults to SQLite, and nothing sets
  `TAKYON_DB_BACKEND=postgres` outside this test file. The `./takyon` shell + dashboard still serve
  entirely from local SQLite. Flipping the runtime onto PG is the serving flip (P8.4).
- **`business.upsert` not exercised — deferred to P8.3 (owner wiring).** PG `businesses.owner_user_id`
  is `not null references users(id)` (0001 spine) and the store supplies no owner. The seam tests
  PRE-SEED a users row + an owned business by hand (raw psycopg) and drive the store's OTHER operator
  operations against it. Seeding/resolving the platform user + Auth0 JIT is P8.3.
- **`job.enqueue` / `app.*` not routed through the seam — deferred to P8.2b.** Their PG tables are owned
  by the Phase-6 (0010 jobs) and Phase-5 (0006/0007 app_*) leaf modules with `timestamptz`/`jsonb`
  shapes that diverge from the store's SQLite SQL. Delegating the store's app_*/jobs reads+writes to
  those leaves (in `_apply_operation`, `read`, `calculate_pulse`) is the next slice — this is the
  "app_* divergence" the P8.1 note flagged, correctly scoped OUT of the connection seam.
- **Not applied to live (deliberate).** The gated live apply (run the extended
  `retire_polsia2_public.sql` FIRST to drop polsia2's agent_runs/events/idempotency_keys, THEN apply
  0011) is still P8.5, after the serving flip, to avoid a live half-state.
- `STRIPE_BILLING_WEBHOOK_SECRET` still outstanding (Phase 3). Storage 50 MB-vs-256 MiB upload-limit
  decision still open. Both unchanged.

**Revert (local only — nothing reached live):**
```sh
rm hermes-agent-main/tests/plugins/test_takyon_store_pg.py
# core.py + conftest.py also carry the operator's unrelated uncommitted work, so do NOT `git checkout`
# them wholesale. Hand-remove only this increment's additions:
#   core.py: delete `def _db_backend()`, `class _PGConn`, the `_connect_postgres` method, the
#            `if _db_backend() == "postgres":` fork in `_connect`, and the `database_url` __init__ param.
#   conftest.py: delete the `pg_store_dsn` fixture (the block after `pg_conn_raw`).
# this log entry is additive — trim it if reverting.
```

---

## Increment — Phase 8 step 2b (LOCAL-verified): route the store's `job.enqueue` + `app.*` ops through the Postgres leaves — Stage A storage-retarget + Stage B leaf-delegation (2026-05-31)

**What:** The 2a seam translated placeholders, but two op families could not just ride the wrapper because their PG tables (0010/0006/0007/0008) deliberately diverge from the store's SQLite SQL. This step routes them — additively, with the SQLite path kept as the exact default — in two stages.

**Stage A — `job.enqueue` is a STORAGE RETARGET, not delegation.** The operator's `jobs` writes are a *work-request record* (enqueue / count / list / GC), never a worker-plane drain. New **`_work_requests_table()`** returns `"jobs"` on SQLite and `"business_work_requests"` on Postgres (0011's 1:1 text-column port of the SQLite operator `jobs`), interpolated into the ~7 operator-jobs `conn.execute` sites (enqueue, the queued-count, the two list reads, the GC SELECT/DELETE). This ISOLATES the operator record from the **0010 `jobs` execution queue** (a different uuid/jsonb/SKIP-LOCKED table the worker plane owns) — same physical name, opposite role; mixing them would let a maintenance GC prune live queued work. Pure name interpolation: the SQL text is otherwise unchanged, so it round-trips through the 2a wrapper.

**Stage B — `app.*` writes DELEGATE to the Phase-5 leaves.** On Postgres each of the five `app.*` write ops in `_apply_operation` forks `if _db_backend() == "postgres":` to the leaf that already owns the table, with the original SQLite block moved verbatim under `else:`:
- `app.budget.set` → `app_usage.set_app_budget` (status hoisted before the call so an omitted status is preserved, not reset).
- `app.plan.upsert` → `app_entitlements.upsert_plan_policy` (migration 0006 dropped the dead `stripe_payment_link_*` columns the SQLite INSERT still lists; the leaf also normalizes `plan_key` and folds plan-validation warnings — so the store passes RAW metadata to avoid double-folding, and re-reads `policy.plan_key` for the receipt).
- `app.customer.upsert` → `app_identity.upsert_app_user`.
- `app.entitlement.upsert` → `app_entitlements.grant_entitlement` (auto-provisions the sub-user from email — no recursive customer.upsert needed on PG — enforces the SAME anti-fake-billing rule, and resyncs `app_users.tier` atomically).
- `app.usage.record` → `app_usage.record_completed_usage` (**REQUIRED**, not just preferred: the PG table mandates a NOT-NULL `reservation_key` the SQLite INSERT never set, and the leaf row-locks the budget and re-checks the cap against committed spend atomically — invariant #8; so the store's non-atomic SUM pre-check is skipped on PG).

Three small seam helpers carry the delegation: **`_app_leaves()`** (lazy import of the three leaf modules, dual repo-root / package import), **`_leaf_conn()`** (a `@contextmanager` that swaps the live connection's `row_factory` from `dict_row` to `tuple_row` for the leaf call — the leaves read their rows BY POSITION — and restores `dict_row` after), and **`_app_user_metadata_select()`** (the sub-user metadata blob is jsonb `metadata` on PG vs text `metadata_json` on SQLite; only the **two** reads — `_rewrite_app_files`, `_app_summary` — that literally name the column needed it, because everywhere else `_row_to_dict` already passes a jsonb dict through untouched while decoding a `_json` text suffix). Plus a `_json_default` net on `_json_dumps` (datetime→isoformat, Decimal→int/float) for the values PG hands back into the app summary/budget dicts; SQLite never triggers it. New imports: `contextmanager`, `Decimal`.

**Why delegate (Stage B) but retarget (Stage A)?** Parsimony + invariant #8. The leaves are the canonical writers for these tables; re-porting their reserve→settle / anti-fake-billing / tier-resync logic into the store would be a SECOND writer of the same rows — exactly the "third parallel system" the plan forbids, and a place the usage cap could silently race. So where a leaf owns the table, the store calls it. Where there is no leaf (the operator job record has none — the 0010 queue is a different concern the store never drains), a name retarget is the smallest faithful change. Per-op judgment, recorded honestly: budget/plan/entitlement are faithful or a superset; usage is semantically required; `app.customer.upsert` is an ACCEPTED NARROWING — the identity leaf forces `status='active'` and does not persist a caller-supplied tier / metadata / custom id on the `app_users` row. Acceptable because effective tier is governed by entitlements (`_sync_user_tier`), `'active'` is already this op's default, and the `app_users` metadata blob is read by no downstream operator path.

**Transaction nesting (verified by reading `commit()`):** the leaf runs on the raw psycopg conn while the store's outer `with self._connect() as conn:` transaction is already open, so the leaf's own `with conn.transaction()` nests as a SAVEPOINT; on outer success the idempotency row + leaf write + `events` row + file mirror all commit together. A leaf error becomes `TakyonError` and PROPAGATES out of `commit()` (it is raised inside `with conn:` with no try/except), rolling the whole transaction back — so a rejected grant/cap leaves NO orphan rows.

**Verified (LOCAL Postgres 16.14 @ 127.0.0.1:54329, real engine, never mocked):** canonical CI-parity `scripts/run_tests.sh` with `TAKYON_TEST_PG_DSN` set; each test runs against a fresh throwaway DB migrated by the SAME `db/runner.py` production uses.
- **`tests/plugins/test_takyon_store_pg.py` — 15 passed** (5 from 2a + 2 Stage A + 8 new Stage B):
  - Stage A: `…job_enqueue_isolates_to_business_work_requests` (row lands in `business_work_requests` with the store's exact text shape; the 0010 `jobs` queue stays empty; `read(query="jobs")` returns it) and `…maintenance_gc_prunes_business_work_requests_not_jobs_queue` (GC prunes the completed record there, receipt keys the pruned set by the physical table, 0010 queue untouched).
  - Stage B: budget delegate + status-preservation; plan delegate proving the `stripe_payment_link_*` columns are ABSENT on PG (`information_schema`), the receipt is the leaf-normalized `pro-plan`, and the advisory warning is folded EXACTLY ONCE; customer delegate (receipt id == persisted id, email normalized, status `active`); entitlement email auto-provision + atomic tier resync to `pro`; anti-fake-billing rejection (manual paid, no evidence) raising `TakyonError` and leaving zero entitlement AND zero orphan user rows; usage delegate proving the receipt `usage_event` is the leaf-generated id (≠ the reservation_key the store passes) and that the LEAF's reservation_key idempotency collapses a duplicate under a DIFFERENT store key; the atomic budget cap refusing the over-cap second record and leaving exactly the accepted row; and a full read round-trip (`read(query="app")` + `calculate_pulse`) after all five delegated writes.
- **SQLite plugin suite unchanged: `1 failed, 77 passed`** (`scripts/run_tests.sh tests/plugins/test_takyon_plugin.py`, no DSN). The one failure is the SAME PRE-EXISTING, unrelated `test_business_work_focus_persists_and_blocks_cross_lane_writes` from the operator's uncommitted work_focus lane-routing (`distribution/outreach/…` prefix) — nothing to do with this slice; every SQLite `app.*` / jobs test stayed green, proving the `else:` branches are byte-for-byte the historical behavior.

**Files changed:**
- `plugins/takyon/core.py` — **my hunks only.** `contextmanager` + `Decimal` imports; `_json_default` + the `default=` on `_json_dumps`; `_work_requests_table()` + its interpolation at the ~7 operator-jobs sites (Stage A); `_app_user_metadata_select()` + the two app_users read SELECTs converted to f-strings; `_leaf_conn()`; `_app_leaves()`; the `if _db_backend() == "postgres":` fork in the five `app.*` ops with the SQLite path preserved under `else:` (Stage B). `core.py` also carries the operator's uncommitted meta-ads / work_focus changes — NOT mine, NOT part of this increment.
- `tests/plugins/test_takyon_store_pg.py` — header docstring updated (app.* moved from "NOT exercised" to a Stage B section); the 2 Stage A tests and the `_commit_one` helper + 8 Stage B tests appended.
- **NOT changed:** 0011 and the other migrations, the leaf modules themselves, the shell/dashboard serving path, and **nothing applied to live.**

**Not done / honest state:**
- **Still seam-only on the serving side.** Nothing sets `TAKYON_DB_BACKEND=postgres` outside the test file; the `./takyon` shell + dashboard still serve entirely from local SQLite. The runtime flip is P8.4.
- **`business.upsert` still deferred to P8.3 (owner wiring).** The tests still pre-seed a users row + owned business by hand because PG `businesses.owner_user_id` is NOT NULL and the store supplies no owner.
- **Accepted `app.customer.upsert` narrowing on PG** (status forced `active`; caller tier/metadata/custom-id not persisted on `app_users`) is deliberate, documented above, and not a bug — but it IS a behavior difference between backends worth remembering at the P8.6 E2E.
- **Not applied to live (deliberate).** Gated live apply (extended `retire_polsia2_public.sql` FIRST, then 0011) is still P8.5, after the serving flip.
- `STRIPE_BILLING_WEBHOOK_SECRET` still outstanding (Phase 3). Storage 50 MB-vs-256 MiB upload-limit decision still open. Both unchanged.

**Revert (local only — nothing reached live):**
```sh
# tests: keep the 5 P8.2a tests; remove ONLY this slice's additions.
#   test_takyon_store_pg.py: delete the "Stage B" section (the `_commit_one` helper + `test_app_*`),
#     the 2 "Stage A" tests (`test_operator_job_enqueue_*`, `test_maintenance_gc_*`), and restore the
#     header docstring's "NOT exercised … app.*" wording.
# core.py also carries the operator's unrelated uncommitted work, so do NOT `git checkout` it wholesale.
# Hand-remove only this increment's additions:
#   - delete `_work_requests_table`, `_app_user_metadata_select`, `_leaf_conn`, `_app_leaves`;
#   - re-inline the literal `jobs` at the work-request SQL sites; restore the two app_users SELECTs to
#     listing `metadata_json` (drop the f-string);
#   - in each of the 5 `app.*` ops delete the `if _db_backend() == "postgres":` branch and de-indent
#     the `else:` body back to the original SQLite block;
#   - revert `_json_dumps` to the plain call and delete `_json_default`; drop the `contextmanager`/
#     `Decimal` imports IF nothing else uses them.
# this log entry is additive — trim it if reverting.
```

---

## Increment — Phase 8 step 3 (LOCAL-verified): owner wiring — single config-keyed platform owner resolved into `business.upsert`, + Auth0 `/auth/callback` JIT (task #6) (2026-05-31)

**What:** Closed the last storage-seam gap before the serving flip: on Postgres, `businesses.owner_user_id`
is `NOT NULL references users(id)` (0001 spine), but the operator store's `business.upsert` supplied no
owner (it serves the local CEO/shell, which has no Auth0/login context). This step resolves a **single
config-keyed platform owner** into the create path — read-only, no secret through the commit — and wires
the deferred Auth0 first-login JIT (task #6) into the dashboard callback, both guarded to PG so the SQLite
era is untouched.

**Why:** Without an owner the PG create path fails the not-null; with a *fabricated* owner it would violate
invariant #8 (no fake state). The robust shape is one platform/operator user owning every shell-created
business, unified with the dashboard login when the operator points the env at their real Auth0 `sub`
(control_api's `/v1/businesses` scopes `where owner_user_id = principal.user_id`, so shell-created and
dashboard-seen businesses must resolve to the SAME `users` row). Full Gate-1/Gate-2 reasoning in
`mediationplan.md` ("Phase 8 owner-wiring finding").

**Decisions (all recorded in the plan finding):**
- **Single platform owner, config-keyed.** New `control_plane.platform_owner_sub()` reads
  **`TAKYON_PLATFORM_OWNER_SUB`** (default sentinel `takyon|platform-owner`; optional
  `TAKYON_PLATFORM_OWNER_EMAIL`). A NON-secret identifier with a working default → zero-friction local is
  unaffected; set it to your real Auth0 `sub` to unify shell↔dashboard ownership.
- **Minting stays OUT of the store (no key through a commit).** `business.upsert`'s PG branch resolves the
  owner **read-only** via new `control_plane.resolve_platform_owner_id(conn)` (a bare
  `select id from users where auth0_sub=%s`, run over the **raw** psycopg conn lent by the existing
  `_leaf_conn` so it speaks `%s`/positional like the other leaf calls). Unprovisioned → `TakyonError`
  **blocked with an actionable reason**, never a NULL/fake owner. The one-time raw API key is therefore
  never observable in a commit result, event payload, or file mirror — it is surfaced only by the explicit
  `control_plane.ensure_platform_owner(conn)` bootstrap (wraps `provision_user_on_first_login` →
  `(user_id, raw_key)`), which the serving flip (P8.4) calls at startup, and by the dashboard JIT.
- **Only the INSERT forks.** The PG INSERT adds the `owner_user_id` column+value; the SQLite INSERT is
  byte-identical (no such column); the UPDATE/existing-business path is unchanged on both. The `raise`
  precedes `self._business_root(slug)` + mkdir, so an unprovisioned-owner block creates **no** filesystem
  side effect and rolls back the transaction. `mode or "live"` stays within `_BUSINESS_MODES={"live","test"}`
  = 0001's `businesses_mode_chk`.
- **Auth0 JIT (#6).** `/auth/callback`, right after `_auth0_authorize_claims`, calls
  `_provision_dashboard_user_if_postgres(user)` → `provision_user_on_first_login(conn, sub, email)` **only
  when `_db_backend()=="postgres"`** (a guarded no-op in the SQLite era — there is no `users` table, and the
  dashboard still logs the operator in via its signed cookie exactly as today), on a per-request
  `autocommit=True` psycopg conn mirroring `runtime_app`'s `control_conn`. On a brand-new `sub` it surfaces
  the one-time key via a prominent server **log** (never a cookie). Wrapped so it can NEVER raise into the
  login flow (logs at error and continues).

**Files changed:**
- `plugins/takyon/control_plane.py` — added `import os`; added the platform-owner block before
  `mint_api_key`: `platform_owner_sub()`, `platform_owner_email()`, `resolve_platform_owner_id(conn)`
  (read-only resolver), `ensure_platform_owner(conn)` (idempotent bootstrap → `(user_id, raw_key)`).
- `plugins/takyon/core.py` — **my hunk only:** the `business.upsert` create path now forks
  `elif _db_backend() == "postgres":` (resolve owner read-only via `_leaf_conn` → `resolve_platform_owner_id`;
  block-with-reason if unprovisioned; INSERT with `owner_user_id`) vs `else:` (the byte-identical SQLite
  INSERT). The UPDATE branch is untouched. (`core.py` also carries the operator's unrelated uncommitted
  meta-ads / work_focus changes — NOT mine.)
- `takyon_cli/web_server.py` — added `_provision_dashboard_user_if_postgres(user)` (lazy-imports
  `_db_backend`/`provision_user_on_first_login`/`resolve_database_url`; no-op off PG; opens
  `psycopg.connect(url, autocommit=True)`; logs a minted one-time key at WARNING "shown once, store it
  securely"; wrapped in `try/except` so it never raises) and a call to it in `/auth/callback` right after
  the claims are authorized.
- `tests/plugins/test_takyon_store_pg.py` — appended **4** PG tests (header docstring updated):
  `test_ensure_platform_owner_idempotent_mints_key_once` (raw1 starts `tk_`, raw2 is None, exactly 1 active
  key + 1 billing + 1 custody account), `test_business_upsert_lands_owned_business_with_resolved_platform_owner`
  (owner_user_id == bootstrapped id; fields persisted; `resolve_api_key(...).business_slugs` contains the
  slug), `test_business_upsert_blocks_when_platform_owner_unprovisioned`
  (`pytest.raises(TakyonError, match="platform owner is not provisioned")` + 0 business rows),
  `test_business_upsert_update_path_preserves_owner_on_postgres` (owner untouched on update).
- `mediationplan.md` — appended the "Phase 8 owner-wiring finding (2026-05-31, step 3; task #6)" paragraph
  (Gate 1 + the four decisions + Gate 2 = one new NON-secret config identifier).

**Verified (LOCAL Postgres 16.14 @ 127.0.0.1:54329, real engine, never mocked):** the full PG store file
`tests/plugins/test_takyon_store_pg.py` → **19 passed** (15 from 2a/2b + 4 new owner-wiring), each against a
fresh throwaway DB migrated by the production `db/runner.py`. The 3 touched Python files byte-compile clean.
(Per the operator's batched-cadence request, the *whole-suite* CI-parity run is performed once at the end of
the P8.4–P8.6 batch, not per-slice.)

**Not done / honest state:**
- The **startup `ensure_platform_owner` seed** is built but not yet *called* by any serving entrypoint — that
  call rides with the serving flip (P8.4). Until then a PG `business.upsert` blocks-with-reason unless an
  owner is pre-provisioned (the tests bootstrap it explicitly).
- Nothing applied to live; the shell/dashboard still serve from SQLite (flip is P8.4; live 0011 apply is the
  gated P8.5).
- `STRIPE_BILLING_WEBHOOK_SECRET` (Phase 3) and the 50 MB→≥256 MiB Storage upload-limit decision still open.

**Revert (local only — nothing reached live):**
```sh
git checkout -- mediationplan.md   # drops the owner-wiring finding paragraph (outer repo)
# control_plane.py is untracked: delete the platform-owner block (platform_owner_sub/_email,
#   resolve_platform_owner_id, ensure_platform_owner) and the `import os` if nothing else uses it.
# core.py carries unrelated operator work — do NOT checkout wholesale. Hand-revert only my hunk:
#   collapse the `elif _db_backend()=="postgres":` create branch back into a single SQLite INSERT
#   (drop the owner resolve + block + owner_user_id column).
# web_server.py: delete `_provision_dashboard_user_if_postgres` and its call in /auth/callback.
# test_takyon_store_pg.py: delete the 4 owner-wiring tests and restore the header docstring.
# this log entry is additive — trim it if reverting.
```

---

## Increment — Phase 8 step 4 (LOCAL-verified): serving flip — shell + dashboard startup seed the platform owner on Postgres; pg_cron dispatch SQL as the inert wake-trigger seam; Storage backend stays pure-env (2026-05-31)

**What/why.** Step 3 built `ensure_platform_owner(conn)` but left it *uncalled*, so a PG `business.upsert`
blocked-with-reason until something provisioned the owner. This step wires that one startup call into BOTH
serving entrypoints, so flipping `TAKYON_DB_BACKEND=postgres` is sufficient to serve — there is no third
"provision the owner first" manual step. The flip itself is **env-driven and already seamed** (P8.2a/P7):
`_db_backend()` reads `TAKYON_DB_BACKEND` (default `sqlite`, opt-in `postgres`); `get_storage_backend()`
reads `TAKYON_STORAGE_BACKEND` (default `local`, opt-in `supabase_s3`). So the Storage half of the flip is
**pure env, zero code** this step. PG-sole serving authority is enforced by construction, NOT by deleting
the SQLite branches: selecting Postgres with no DSN **raises** `RuntimeNotConfigured`
(`runtime_app.resolve_database_url`, called directly by `_connect_postgres`) — invariant #8, never a silent
fall-back to SQLite. Keeping the SQLite path as the env-default-off branch is what lets the ~17k hermetic
SQLite suite keep running; the live runtime is Postgres because the VPS/dashboard service sets the env, not
because the code lost a branch. (Physical removal of the SQLite branches, if ever wanted, is a post-cutover
cleanup, not a serving requirement — held with the gated P8.5.)

- **One idempotent seed, two callers, one home.** New `TakyonStore.seed_platform_owner() -> (user_id|None,
  raw_key|None)` is a guarded **no-op off Postgres** (returns `(None, None)`); on PG it delegates to
  `control_plane.ensure_platform_owner` over the **raw** psycopg conn lent by the existing `_leaf_conn`,
  inside the store's own `_connect()` txn — reusing the P8.2b leaf seam rather than inventing a second
  connection strategy (parsimony). `control_plane.py` must stay connection-strategy-free and `runtime_app`
  imports FastAPI at top (so the shell can't depend on it) → the store is the correct shared home.
- **Shell caller** (`cli._seed_platform_owner_at_startup(store)`, called in `_interactive_shell` right after
  `store = TakyonStore()`): prints a freshly-minted one-time key to **stderr** ("shown ONCE — store it
  securely"); on an already-provisioned owner it stays silent; wrapped so a seed failure **never blocks the
  shell** (writes a skipped-notice to stderr and continues).
- **Dashboard caller** (`web_server._seed_platform_owner_if_postgres()`, called in `start_server` right after
  `_configure_local_product_publish`): same idempotent `TakyonStore.seed_platform_owner()`, logs a minted key
  at **WARNING** ("shown once, store it securely") / already-provisioned at DEBUG; wrapped so it **never
  raises into dashboard startup**. The shell-side and dashboard-side callers both funnel through the SAME
  store method — one minting path, no duplication.
- **pg_cron dispatch as an inert, operator-gated seam.** New `plugins/takyon/db/apply_pg_cron_dispatch.sql`
  (deliberately **NOT** under `db/migrations/` — the runner + conftest sweep `migrations/*.sql` on every run
  and pg_cron is Supabase-/superuser-only, absent in local/CI PG, so sweeping it in would fail every run). It
  schedules the already-migration-installed, already-Phase-6-tested `dispatch_due_wakes()` to run every
  minute via `cron.schedule('takyon-dispatch-wakes', '* * * * *', …)`. A leading `do $$…$$` block **raises
  loudly** (invariant #8) if `dispatch_due_wakes()` is not installed or `pg_cron` is not in `pg_extension`,
  and unschedules any prior job of that name first so re-applying is a clean single-job replace. Revert is one
  statement: `select cron.unschedule('takyon-dispatch-wakes');`.

**Files changed:**
- `plugins/takyon/core.py` — **my hunk only:** added `TakyonStore.seed_platform_owner()` immediately after
  `_connect_postgres` (no-op off PG; on PG `with self._connect() as conn: with self._leaf_conn(conn) as raw:
  return control_plane.ensure_platform_owner(raw)`; import-style-robust import of `control_plane`). (core.py
  also carries the operator's unrelated uncommitted meta-ads / work_focus changes — NOT mine.)
- `plugins/takyon/cli.py` — added `_seed_platform_owner_at_startup(store)` before `_interactive_shell` and a
  call to it inside `_interactive_shell` right after `store = TakyonStore()` (uses the already-imported `sys`
  + `TakyonStore`).
- `takyon_cli/web_server.py` — added `_seed_platform_owner_if_postgres()` after
  `_provision_dashboard_user_if_postgres` and a call to it in `start_server` after
  `_configure_local_product_publish(host, port)` (lazy-imports `TakyonStore`/`_db_backend`; uses the
  module `_log`; no-op off PG; never raises).
- `plugins/takyon/db/apply_pg_cron_dispatch.sql` — **new file** (untracked): the gated Supabase-only pg_cron
  apply that wires the interval trigger around `dispatch_due_wakes()`.
- `mediationplan.md` — appended the "Phase 8 serving-flip finding (2026-05-31, step 4)" paragraph
  (env-driven flip already seamed; owner gap = the one substantive wire; the store-as-home decision; pg_cron
  apply SQL; the **honest** enqueue-only / worker-drain-deferred caveat; Gate 2 = NONE new — two env toggles
  + optional Supabase pg_cron config).
- `tests/plugins/test_takyon_store_pg.py` — appended **2** P8.4 tests in a new "P8.4 serving flip" section:
  `test_seed_platform_owner_via_store_is_idempotent_and_enables_create` (first `store.seed_platform_owner()`
  returns a key starting `tk_`, second returns `None`; exactly one active key + one billing + one custody
  account; a subsequent `business.upsert` of "flipco" then succeeds owned by that owner — proving the seed
  unblocks create) and `test_seed_platform_owner_is_noop_off_postgres` (with `TAKYON_DB_BACKEND` unset,
  `TakyonStore(root=tmp_path).seed_platform_owner() == (None, None)`).

**Verified (LOCAL Postgres 16.14 @ 127.0.0.1:54329, real engine, never mocked):** the 2 P8.4 tests pass, and
the **whole PG store surface runs green together** — `test_takyon_store_pg.py` + `test_takyon_serving_flip_pg.py`
(P8.6, below) + `test_takyon_storage_pg.py` → **45 passed**, each against a fresh throwaway DB migrated by the
production `db/runner.py`. Re-confirmed at source (not memory) that `_connect_postgres` →
`resolve_database_url` raises `RuntimeNotConfigured` when no DSN is set (no silent SQLite fallback). Touched
Python files byte-compile clean.

**Not done / honest state:**
- **Worker drain stays UNMOUNTED — so pg_cron dispatch is ENQUEUE-ONLY.** `jobs.run_one` (the drain) has no
  caller; mounting it needs a CEO-turn handler registry that runs the *model*, which is the deferred Phase-6
  **worker-plane deployment**, deliberately OUT of this step's scope. `dispatch_due_wakes()` only INSERTs due
  wakes into the jobs queue (idempotent on the per-wake key); applying the pg_cron SQL on a runtime whose
  worker is not yet draining is therefore harmless — due wakes accumulate as queued jobs and drain once the
  worker is mounted. The SQL file documents this honestly in its header.
- pg_cron is unavailable locally (Supabase-only), so the apply SQL is **operator-gated**, applied once at the
  live cutover; it is not swept by the migration runner and has no local test (the function it schedules is
  already migration-installed + Phase-6-tested directly).
- The flip is opt-in: default backend is still `sqlite`; nothing applied to live (the live 0011 apply is the
  gated P8.5). `STRIPE_BILLING_WEBHOOK_SECRET` (Phase 3), the 50 MB→≥256 MiB Storage upload-limit decision,
  and the Supabase pg_cron enable toggle remain operator-pending.

**Revert (local only — nothing reached live):**
```sh
git checkout -- mediationplan.md   # drops the serving-flip finding paragraph (outer repo)
# core.py carries unrelated operator work — do NOT checkout wholesale. Hand-revert only my hunk:
#   delete the `TakyonStore.seed_platform_owner` method (the block right after `_connect_postgres`).
# cli.py: delete `_seed_platform_owner_at_startup` and its call in `_interactive_shell`.
# web_server.py: delete `_seed_platform_owner_if_postgres` and its call in `start_server`.
rm plugins/takyon/db/apply_pg_cron_dispatch.sql   # new file (untracked)
# test_takyon_store_pg.py: delete the 2 P8.4 tests (the "P8.4 serving flip" section).
# this log entry is additive — trim it if reverting.
```

---

## Increment — Phase 8 step 6 (LOCAL-verified): full E2E through the REAL shell parser + no-fleet stateless resume via the operator store, both on real Postgres; SQLite kept as the env-default-off branch (the "kill" is the env flip, not code deletion) (2026-05-31)

**What/why.** This is the acceptance for the serving flip: drive the runtime the way an operator actually
does and prove identical behavior on Postgres, plus prove a second empty-disk host resumes purely from
Postgres + Storage. New file `tests/plugins/test_takyon_serving_flip_pg.py` (2 tests), both against a real
migrated throwaway PG (never mocks). On "delete the SQLite path": the SQLite branches **stay in the tree** as
the env-default-off backend (deleting them would break the ~17k hermetic SQLite suite and local dev for zero
production gain). PG-sole serving authority is achieved by the env flip + invariant-#8 no-silent-fallback
(verified above), so the "kill" is operational (live service sets `TAKYON_DB_BACKEND=postgres`), not a code
excision. Any physical removal is an optional post-cutover cleanup held with the gated P8.5.

- **Real-shell operator lifecycle on Postgres** (`test_shell_operator_lifecycle_on_postgres`). Drives the
  ACTUAL per-line shell parser/router `cli._handle_shell_line` — the same function the interactive `./takyon`
  shell calls for every typed line — for the model-free operator commands with `TAKYON_DB_BACKEND=postgres`,
  `DATABASE_URL=<throwaway>`, `TAKYON_HOME=<tmp>`. The platform owner is seeded exactly as shell startup does
  (`cli._seed_platform_owner_at_startup`), so `business.upsert` resolves a real owner. It runs
  `/create --no-auto --test e2eco …` (`--no-auto` keeps it model-free — no CEO turn, no cron), then asserts
  on the **raw PG row** that the business landed owned by the seeded `auth0|e2e-operator` (NOT a NULL/fake
  owner) with the right goal/mode/status; then `/status`, `/pulse`, `/test status`, and `/show` read back
  through the shell on PG (read path, not just write path), including a workspace file reading back
  byte-faithfully through `/show`. This exercises slash-command parsing + scoped routing +
  `run_takyon_command` → `TakyonStore` end-to-end on PG — not just the store API the other PG tests call.
- **No-fleet stateless resume via the OPERATOR STORE on Postgres**
  (`test_no_fleet_resume_via_operator_store_on_postgres`). The Phase-7 no-fleet proof used the raw psycopg
  leaf; THIS ties it to the full `TakyonStore`. Host A's store creates the PG-authoritative business
  (`business.upsert` of "nfco") and writes a realistic four-root workspace (text + a binary receipt blob) to
  local scratch, then `sync_up`s (3 uploaded). Host B is a SECOND store on a **genuinely empty disk** sharing
  the same Postgres (empty-disk precondition asserted BEFORE host B acts). It resumes the business from PG
  alone via `store.read(scope="business:nfco", query="summary")` (slug/goal come back from PG with no local
  tree). The test then makes the **two state planes explicit and independent**: (1) the **Postgres plane** —
  reading the summary mirrors the PG-authoritative app surface contract to `product/surface.md` on disk, the
  ONLY file present before any Storage round-trip; (2) the **Storage plane** — `sync_down` reconstructs the
  workspace blobs byte-for-byte (default `delete_local=False`, so the PG-mirrored contract is left untouched
  alongside them), and the workspace subtree (excluding the PG mirror) is byte-identical to host A, binary
  blob included. That is the "host is disposable; state lives in Postgres + Storage" acceptance.

**Files changed:**
- `tests/plugins/test_takyon_serving_flip_pg.py` — **new file**, 2 E2E tests (above) + local
  `_seed_workspace`/`_tree` helpers mirroring the Phase-7 storage fixture so the byte-identity assertion is
  apples-to-apples. `psycopg = pytest.importorskip("psycopg")`; uses the conftest `pg_store_dsn` fixture.

**Verified (LOCAL Postgres 16.14 @ 127.0.0.1:54329, real engine, never mocked):** both E2E tests pass; the
full PG surface (store + serving-flip + storage) is **45 passed** together against fresh per-test throwaway
DBs. First run surfaced one real behavior I had mis-modeled — `read(summary)` mirrors `product/surface.md`
to disk as a PG→disk materialization, so the "empty disk" assertion had to move BEFORE the read and the
byte-identity check had to separate the PG plane from the Storage plane; the corrected test is a *stronger*
proof (it now demonstrates both planes reconstruct independently). Per the operator's batched-cadence
request, the **whole-suite** CI-parity run is performed once at the end of the P8.4–P8.6 batch.

**Not done / honest state:**
- The E2E exercises the **model-free** operator surface (`/create --no-auto`, `/status`, `/pulse`, `/test`,
  `/show`) — it deliberately does not invoke a CEO model turn (that needs provider creds and is non-hermetic);
  the model-routing path (harness commands, plain-text→CEO) is unchanged by the flip and covered elsewhere.
- SQLite branches remain in the tree (env-default-off) by design; nothing applied to live; worker drain still
  unmounted (see step 4). The live 0011 apply remains the gated P8.5.

**Revert (local only — nothing reached live):**
```sh
rm tests/plugins/test_takyon_serving_flip_pg.py   # new file — the entire P8.6 E2E
# this log entry is additive — trim it if reverting.
```

---

## Increment — P8.7–P8.8: VPS Postgres serving flip (psycopg provisioning + pgbouncer-safe connections + tracked-unit env flip) (2026-05-31)

**What.** Flipped the live dashboard host (`argon-alpha-14`, `137.184.75.57`, runtime `/opt/takyon/hermes-agent-main`) from the SQLite serving backend onto the Postgres operator store, via three changes:
1. **`pyproject.toml`** — added a `postgres = ["psycopg[binary]==3.3.4"]` optional-dependency extra (exact-pinned per the supply-chain policy; `[binary]` bundles libpq so no system `libpq-dev`). psycopg is lazily imported (`runtime_app.py`, `core._connect_postgres`) and the PG tests `importorskip` it, so it is backend-specific and belongs in an extra, not core. **Caught a real gap:** psycopg had only ever been pip-installed into the local dev venv during P8 — it was never declared, and the deploy excludes `.venv/` and never installs Python deps, so the VPS venv had **no psycopg**. A flip without it would have crashed the runtime on first PG connect. Installed `psycopg[binary]==3.3.4` (cp312 manylinux wheel, x86_64) into `/opt/takyon/hermes-agent-main/.venv` manually — the same way every other VPS dep got there.
2. **`plugins/takyon/runtime_app.py` + `plugins/takyon/core.py`** — set `prepare_threshold=None` on **both** psycopg connect sites (the per-request control-plane connection and the `TakyonStore` `_connect_postgres` seam). The live `DATABASE_URL` resolves to Supabase's **pgbouncer endpoint (port 6543)**; in transaction pooling a server backend is reassigned per transaction, so psycopg's default auto-prepare (threshold 5) can split a `PREPARE` and its `EXECUTE` across backends → `prepared statement does not exist`. Disabling auto-prepare removes that entire failure class with identical correctness (extended protocol either way) and negligible cost on a low-QPS control plane. This is the upstream connection-factory fix, not a downstream retry band-aid.
3. **`deploy/argon-alpha-14/takyon-dashboard.service`** — added `Environment=TAKYON_DB_BACKEND=postgres`. This is the canonical, version-controlled flip: the deploy SCPs this tracked unit over `/etc/systemd/system/takyon-dashboard.service` every run, so the flip survives redeploys (a systemd drop-in would survive but be untracked). The tracked unit was byte-verified to match the live unit before the edit, so the SCP changes only this one env line.

**Why.** The operator gave an explicit go-ahead for the full cutover ("you can do this … go. and the others"). Robustness is the #1 value, so before touching production I ran a read-only on-VPS probe that resolves `DATABASE_URL` exactly as the runtime does (`core.load_takyon_env` → `runtime_app.resolve_database_url`), reported the endpoint shape **without printing credentials**, and **forced psycopg past its prepare threshold** (12 repeated parametrized autocommit queries on one long-lived connection — a strictly harder case than the runtime's short-lived per-request/per-op connections) to detect pooler breakage before the flip rather than in production.

**Verified BEFORE the flip (read-only against LIVE Supabase + local PG, never mocked):**
- **Live schema (read-only verifier):** the P8.5 apply is in fact complete on live — `0001`–`0010` present + takyon-shaped (0 rows), the retire-of-3 targets (`agent_runs`/`events`/`idempotency_keys`) now takyon-shaped, all 7 of 0011's net-new tables present + takyon-shaped, all 6 `businesses` operator-enrich columns present, `profiles` still present (confirms the real prod DB). (Task #48 was stale-marked pending; corrected.)
- **On-VPS connectivity probe (read-only):** `DATABASE_URL` → scheme `postgresql`, **port 6543** (pgbouncer), db `postgres`; `select 1` ✓, param-bind ✓, `count(businesses)=0` (fresh — see below), `public_tables=132` (125 after 0001–0010 + 7 net-new from 0011 ✓), `operator_tables=10` ✓; **prepared-statement loop ×12 PASSED** even with default psycopg (so the flip was already prepared-statement-safe; `prepare_threshold=None` is belt-and-suspenders correctness for the pooler).
- **Local PG regression (Postgres 16.14 @ 127.0.0.1:54329):** the full PG-integration set — both connect sites, 23 files — is **318 passed** with `prepare_threshold=None`; the serving-flip E2E (`test_takyon_serving_flip_pg.py`) is green. Both touched files `py_compile`-clean.

**Platform-owner seed (designed one-time secret surface).** First PG serving startup mints the single platform owner (`control_plane.ensure_platform_owner`, keyed by `TAKYON_PLATFORM_OWNER_SUB`) and surfaces its one-time API key **exactly once** — for the dashboard via `web_server._seed_platform_owner_if_postgres` → `_log.warning` (→ journald). The seed is idempotent and is wrapped so a hiccup never blocks the dashboard from binding (invariant #8: a later `business.upsert` would block with its own reason rather than serving a NULL owner). `TAKYON_PLATFORM_OWNER_SUB` is currently **unset on the VPS → defaults to `takyon|platform-owner`**, which is acceptable for the flip because there are **0 businesses** (fresh start; switching the sub later is clean). The key is left in its designed one-time log surface; it is **not** echoed anywhere or persisted in clear.

**uv.lock (deliberately NOT regenerated).** The committed `uv.lock` pins `websockets 15.0.1` while `pyproject` pins core `websockets==16.0` — the lock is **already stale, predating the websockets bump**, and that (not the psycopg addition) is why `uv lock` fails: the optional `daytona==0.155.0` extra requires `websockets<16.0`, which is unsatisfiable against the core `==16.0` pin at `python_full_version >= '3.14'`. Because `websockets==16.0` is a **core** dep (not an extra), no `[tool.uv] conflicts` declaration can resolve it, and fixing it would mean altering the unrelated `daytona` extra or `requires-python` — out of scope for the PG flip. The **outer-repo `deploy.yml` does not run `uv lock`/`uv lock --check`**, so a stale lock does not affect this deploy. Left as-is; flagged here.

**Files changed:**
- `hermes-agent-main/pyproject.toml` — `postgres` extra (1 line + rationale comment).
- `hermes-agent-main/plugins/takyon/runtime_app.py` — `prepare_threshold=None` + comment on the per-request connect.
- `hermes-agent-main/plugins/takyon/core.py` — `prepare_threshold=None` + comment on `_connect_postgres`.
- `deploy/argon-alpha-14/takyon-dashboard.service` — `Environment=TAKYON_DB_BACKEND=postgres` + rationale comment.
- VPS-side (out of repo): `psycopg[binary]==3.3.4` installed into the runtime venv.

**Not done / honest state (at commit time):**
- **The live flip executes on the deploy restart triggered by this push** (the workflow SCPs the flipped unit + rsyncs the connection change + restarts). Post-restart live confirmation (service active, journald shows PG backend + owner seed, no `RuntimeNotConfigured`, dashboard 200/302, `/api/status` 200/401) is appended as a follow-up below after `gh run watch`.
- The VPS's 9 local-SQLite **test** businesses are **deliberately orphaned** by the flip (disposable test data; the PG operator store starts empty per the REPLACE decision). No data was migrated.
- `TAKYON_STORAGE_BACKEND` stays default (local disk) for the single-VPS host; `STRIPE_BILLING_WEBHOOK_SECRET` still outstanding (flow-A billing webhook stays blocked-with-reason); the worker-drain plane is built next and held **inert** (no `takyon-worker.service` enabled on the VPS yet); pg_cron is optional (the worker self-dispatches `dispatch_due_wakes`).

**Revert (the flip is env-reversible — no schema change here):**
```sh
# Remove the one env line and redeploy → dashboard restarts on SQLite (default backend):
git checkout HEAD -- deploy/argon-alpha-14/takyon-dashboard.service   # drops Environment=TAKYON_DB_BACKEND=postgres
# Emergency fast revert (no workflow wait): SCP a unit without that line to the VPS + `systemctl daemon-reload && systemctl restart takyon-dashboard.service`.
# The prepare_threshold=None + postgres extra are safe to keep regardless of backend; revert them only to fully undo:
git checkout HEAD -- hermes-agent-main/plugins/takyon/runtime_app.py hermes-agent-main/plugins/takyon/core.py hermes-agent-main/pyproject.toml
# this log entry is additive — trim it if reverting.
```

**LIVE VERIFIED (2026-05-31, post-deploy).** Pushed `88cfc108`; the "Deploy Takyon" workflow (run
`26725907695`) went green — build UI, compile, rsync runtime, SCP the flipped unit, restart, smoke
(dashboard 200/302 + `/api/status` 200/401) all ✓. On the VPS: `takyon-dashboard.service` **active**,
the live unit carries `TAKYON_DB_BACKEND=postgres`, **zero** error-priority journal entries since the
restart. The smoke test only proves the dashboard answers, so the load-bearing proof is the database:
live Supabase `users` went **0 → 1** (`auth0_sub = takyon|platform-owner`, `created_at = 22:10:58Z` =
the restart moment), and the owner came up fully provisioned — `billing_accounts = 1` **and**
`custody_accounts = 1` (both ledgers opened by JIT) + `user_api_keys = 1` (key minted). `businesses = 0`
(the VPS's 9 local-SQLite test businesses are deliberately orphaned). The live operator runtime is now
authoritative on Postgres. The one-time raw owner key was emitted to the dashboard's startup log surface
only (not captured to any clear-text store, by design); if external control-plane API access is needed
later, mint/rotate a fresh key rather than recovering this one. Follow-up (not blocking): set
`TAKYON_PLATFORM_OWNER_SUB` to the operator's real Auth0 sub before creating real businesses (clean now
at 0 businesses; a later switch just creates the real-sub owner and leaves the default vestigial).

---

## Increment — P8.9: worker-drain plane (built INERT; local-PG-tested; VPS activation held) (2026-05-31)

**What.** Built the Postgres-native **worker-drain plane** — the long-lived process that finally ties the
Phase-6 queue (`jobs.py`) and schedule (`wakes.py`) together and **replaces the legacy SQLite file-cron CEO
wakeups**. One tick (`worker.drain_tick`) does, in order: **self-dispatch** due wakes
(`wakes.dispatch_due_wakes` → enqueues a `ceo_wake` job carrying the schedule's payload, then advances
`next_run_at`), **reclaim** stale claims (`jobs.requeue_stale`, older-than 900s), then **drain** the queue
through the budget-gated `jobs.run_one` cycle until empty, routing each job kind to a handler and returning
counts `{dispatched, requeued, drained, completed, blocked, failed}`. Because the worker self-dispatches,
**pg_cron is optional** (pass `--no-dispatch` if pg_cron owns dispatch instead).

Three surfaces, one new module:
1. **`plugins/takyon/worker.py`** (new) — `drain_tick`, the `ceo_wake_handler`, the `HANDLERS` registry,
   `_run_ceo_turn`, and `run_worker_loop`. The handler reuses the **real sources of truth** rather than
   re-deriving anything: the wake prompt from `core._ceo_cron_prompt(slug)`, the wake toolsets from
   `core._ceo_cron_toolsets()` (`["takyon","web","skills","todo"]`), the stable system prompt from
   `cli._load_ceo_prompt()` (ceo.md), model resolution from `cli._read_model_config`/`_require_agent_model_config`,
   and the inactivity-timeout pattern from `cron/scheduler.py` (ThreadPoolExecutor + idle poll → `interrupt()`
   + `TimeoutError`). The only thing built fresh is the ~20-line `AIAgent` construction — deliberately **not**
   `cli._run_agent`, which discards the turn's cost and wraps the message in an interactive operator envelope
   that is wrong for a scheduled wake. The handler converts the turn's **true USD cost → integer cents**
   (`max(0, round(usd*100))`) and always reports it, so `run_one` settles correctly whenever an estimate was
   reserved.
2. **`takyon_cli/main.py`** — added the `takyon-cli worker` subcommand (`cmd_worker` + `worker` subparser with
   `--once`, `--no-dispatch`, `--poll-interval`, `--max-jobs`, `--worker-id`; registered in both `_SUBCOMMANDS`
   and `_BUILTIN_SUBCOMMANDS`). `cmd_worker` fails **loud** on any startup error and exits non-zero (invariant
   #8 — never a silent half-start).
3. **`deploy/argon-alpha-14/takyon-worker.service`** (new) — the canonical unit for the worker, **tracked but
   INERT**: `deploy-runtime.sh` (and `deploy.yml`) only manage `takyon-dashboard.service`, and the rsync ships
   `worker.py` + the `worker` CLI to the VPS, so the **code is present** and `takyon-cli worker --once` can be
   run by hand for a smoke check, but **no daemon starts**. Recurring wake EXECUTION stays on the legacy
   file-cron until activation — a separate, operator-gated step documented in the unit header (scp the unit +
   `systemctl enable --now`). Only ONE worker per deployment (jobs are `FOR UPDATE SKIP LOCKED`, so extras are
   safe but redundant); `TimeoutStopSec=120` so an in-flight CEO turn can finish on stop.

**Why.** This is the last piece of the Phase-6 plane and the operator's explicitly-authorized remaining work
("the others"), with the standing instruction to build it **inert + local-PG-tested and HOLD VPS activation**.
Robustness (#1 value) drove the loop design: `run_worker_loop` calls `load_takyon_env()` then
`resolve_database_url()` **before any loop or signal handler**, so a missing `DATABASE_URL` raises
`RuntimeNotConfigured` immediately (invariant #8). Each tick opens a **fresh** per-tick psycopg connection
(`autocommit=True`, `prepare_threshold=None` — the same pgbouncer-safe settings as `runtime_app`), so a dropped
connection costs one tick and reconnects next tick. `drain_tick` is exception-guarded (a tick failure logs and
the daemon survives). SIGTERM/SIGINT stop pulling NEW jobs between jobs and exit cleanly; a job **killed
mid-turn** is left `running` and reclaimed by the next worker's `requeue_stale`, **its reservation refunded** —
so an interrupted wake is safe, never double-billed, never a fake completion.

Wake billing is **opt-in per schedule**: `dispatch_due_wakes` copies `wake_schedules.payload` onto the job, so
a wake bills only if its payload carries `estimate_cents`. Per the `run_one` contract, when `estimate_cents`
is absent/0 nothing is reserved and the handler's reported cost is ignored; when present, the owner's flow-A
balance is reserved under `job:<id>:<attempts>`, the handler runs, and the ledger settles to
`max(0, min(actual, reserved))`. An owner who cannot cover the estimate is `blocked('budget_exhausted')` and
**the handler never runs** — proven by test.

**Verified (LOCAL Postgres 16.14 @ 127.0.0.1:54329, real engine — dispatch/claim/reserve/settle/lifecycle are
the real `jobs`/`wakes`/`billing` code; only the leaf CEO turn is stubbed, exactly as `jobs_pg` stubs the work
seam):**
- **`tests/plugins/test_takyon_worker_pg.py`** (new, **12 tests, all green**). PG end-to-end: a due wake is
  enqueued-then-drained in **one tick** (dispatched=1, completed=1, handler ran once); a **second** tick is a
  no-op because dispatch advanced the cursor past now() (the wake ran exactly once across both ticks); true cost
  **settles the ledger** (allowance 100000, estimate 500, true cost 300 → `allowance_used=300`, `reserved=0` —
  settled at true cost, remainder released); an exhausted budget is **blocked** and the handler never runs;
  `--no-dispatch` drains the queue **without** enqueuing the due wake (`last_enqueued_at` stays NULL); an empty
  queue is a clean all-zero no-op; and with no explicit handlers the tick consults `worker.HANDLERS`, proving
  `ceo_wake` is wired (run seam stubbed). Unit: the handler maps `$0.0734 → 7` cents, sources the canonical wake
  toolsets, honors `payload.max_turns`, reports 0 cents for a free turn; and `run_worker_loop(database_url=None)`
  with the env cleared raises `RuntimeNotConfigured` (invariant #8).
- **Engine intact:** worker + jobs + wakes PG suites together = **35 passed**.
- **CLI wiring:** `takyon-cli worker --help` renders the subcommand + all five flags (exit 0); `worker.py` and
  `takyon_cli/main.py` are `py_compile`-clean.
- **One real issue found and fixed during testing** (honest state): the invariant-#8 unit test initially DID NOT
  RAISE on this dev box, because `run_worker_loop` legitimately calls `load_takyon_env()` first (that is how it
  reads `DATABASE_URL` from `$TAKYON_HOME/.env` in production), which **repopulated** `DATABASE_URL` from the
  on-disk `.env` and masked the invariant. The fix is **test-isolation, not a worker behavior change**: the test
  now monkeypatches `core.load_takyon_env` to a no-op so the resolve seam is exercised with a genuinely empty
  env. The worker's load-then-resolve order is correct and unchanged.

**Files changed:**
- `hermes-agent-main/plugins/takyon/worker.py` — **new file**, the drain plane (drain_tick, ceo_wake_handler,
  HANDLERS, _run_ceo_turn, run_worker_loop).
- `hermes-agent-main/takyon_cli/main.py` — `cmd_worker` + `worker` subparser + `_SUBCOMMANDS`/`_BUILTIN_SUBCOMMANDS`
  entries.
- `hermes-agent-main/tests/plugins/test_takyon_worker_pg.py` — **new file**, 12 tests (above).
- `deploy/argon-alpha-14/takyon-worker.service` — **new tracked INERT unit** (ships code; starts no daemon).

**Not done / honest state:**
- **The worker daemon is NOT enabled on the VPS** — by design. This push ships `worker.py` + the `worker` CLI
  + the (inert) unit; recurring wake EXECUTION stays on the legacy file-cron until the operator runs the gated
  activation in the unit header. `takyon-cli worker --once` can be run by hand on the VPS as a smoke check
  without enabling the daemon.
- `pg_cron` remains optional (the worker self-dispatches); `plugins/takyon/db/apply_pg_cron_dispatch.sql` is
  available to apply on Supabase if pg_cron-owned dispatch is later preferred (then run the worker with
  `--no-dispatch`).
- `STRIPE_BILLING_WEBHOOK_SECRET` still outstanding; `TAKYON_PLATFORM_OWNER_SUB` still defaulting (both
  unchanged by this step).

**Revert (local only — nothing reached live; the unit is inert even after it ships):**
```sh
rm hermes-agent-main/plugins/takyon/worker.py
rm hermes-agent-main/tests/plugins/test_takyon_worker_pg.py
rm deploy/argon-alpha-14/takyon-worker.service
git checkout HEAD -- hermes-agent-main/takyon_cli/main.py   # drops cmd_worker + the worker subparser/registrations
# this log entry is additive — trim it if reverting.
```

---

## Flow-B fix: wire the live `business_record_stripe_webhook` tool to the Postgres accrual leaf

**Why (the hole this closes):** flow B's payment→accrual engine (`plugins/takyon/app_payments.py::record_webhook_and_process`) was built and unit-tested but had **no non-test caller in the serving path**. The live tool `business_record_stripe_webhook` → `core.handle_business_record_stripe_webhook` routed *unconditionally* through the legacy SQLite handler `core._process_checkout_completed`, which performs **zero owner custody accrual**. Net effect: a sub-user payment reconciled (revenue + entitlement) but the gross-minus-app-fee net **never reached the owner's custody balance** — exactly the Phase-5(d) acceptance ("sub-user payment shows in owner custody") silently regressed at the live entrypoint. The usage-metering half of flow B (ai_gateway → app_usage) was already correctly wired; only the payment→accrual half was dead.

**The fix (parsimonious — no new operation action, no new HTTP route):** on the Postgres backend the handler now delegates to the canonical leaf over the raw psycopg connection, using the **same store→leaf pattern already proven by `seed_platform_owner`** (`with store._connect() as conn: with store._leaf_conn(conn) as raw: app_payments.record_webhook_and_process(raw, event)`). The leaf already owns the `webhook_events` dedup, the checkout/subscription dispatch, AND the `custody.accrue(gross, fee)` net accrual in one transaction. The legacy SQLite branch is preserved verbatim for the SQLite backend (`_db_backend() != "postgres"`), so `test_takyon_app_api.py`'s SQLite webhook route is untouched.

- **Why NOT the `_commit_tool` seam** (my first proposed mechanism): `_normalize_operation` *requires a business scope* for every action (core.py:5172) and applies per-business work-focus + kill-switch gating. A Stripe webhook is **global** — it carries no business slug; the business is discovered from the checkout intent *inside* the leaf — and the leaf already does its own dedup. Forcing it through the business-scoped operator seam would mean a fake scope, double dedup, and a conceptual mismatch. The direct store→leaf delegation (mirroring `seed_platform_owner`) is the smaller, truthful fit.
- **Envelope flattening:** the leaf returns `{provider_event_id, type, deduplicated, processed}`; the tool flattens that to the SAME shape the SQLite path returned — top-level ids + `processed` = the inner reconciliation dict (`None` on a deduplicated replay) — so no caller/skill/UI reading `processed` changes.

**Discovery surface (so the CEO can see it):** `skills/takyon/takyon-app-runtime/SKILL.md` now states in How-to-Run + Procedure step 7 that paid reconciliation accrues gross minus the platform application fee (`STRIPE_CONNECT_APPLICATION_FEE_BPS`, default 2000 bps = 20%) into the business owner's custody balance (flow B, distinct from the top-level user billing ledger), and that payout of that custody balance is **deferred** (no Stripe Connect transfer yet) — report it as owed/accrued, never as paid out.

**Tests (real engine, real Postgres):** two new tests in `tests/plugins/test_takyon_app_payments_pg.py` drive the **tool** (not just the leaf) against Postgres:
- `test_record_stripe_webhook_tool_accrues_to_owner_custody` — a paid `checkout.session.completed` through the tool moves the owner's custody `owed_balance_cents` to `gross − app fee` and records revenue.
- `test_record_stripe_webhook_tool_dedups_on_replay` — a replayed event id accrues exactly once (`processed` is `None` the second time).
- Regression sweep green: `app_payments + store + custody + worker + app_api` = **72 passed** (store suite proves the `_app_leaves()` `payments` addition didn't disturb the existing identity/entitlements/usage delegations; app_api proves the SQLite path still works).

**Files changed:**
- `hermes-agent-main/plugins/takyon/core.py` — `handle_business_record_stripe_webhook` gains a Postgres branch that delegates to `app_payments.record_webhook_and_process`; `TakyonStore._app_leaves()` now also returns the `payments` leaf (docstring updated).
- `hermes-agent-main/skills/takyon/takyon-app-runtime/SKILL.md` — names the owner custody accrual, the app-fee env var, and the deferred payout.
- `hermes-agent-main/tests/plugins/test_takyon_app_payments_pg.py` — two new tool-level PG tests.

**Not done / honest state:**
- **Payout is still deferred** — accrual lands in `custody_accounts.owed_balance_cents`; no Stripe Connect transfer is performed. Unchanged by this step (per the account/money model: per-user Connect payout is deferred).
- The webhook tool verifies product webhooks with `STRIPE_WEBHOOK_SECRET` (separate from the flow-A `STRIPE_BILLING_WEBHOOK_SECRET`, still outstanding).
- No new HTTP webhook route was added on the Postgres serving surface; the tool is the reconciliation entrypoint. If/when a product webhook HTTP endpoint is mounted on the Postgres runtime, point it at this same handler rather than re-implementing accrual.

**Revert (local only — nothing irreversible; pure code+docs+tests):**
```sh
git checkout HEAD -- hermes-agent-main/plugins/takyon/core.py \
  hermes-agent-main/skills/takyon/takyon-app-runtime/SKILL.md \
  hermes-agent-main/tests/plugins/test_takyon_app_payments_pg.py
# this log entry is additive — trim it if reverting.
```
