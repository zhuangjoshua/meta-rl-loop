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
