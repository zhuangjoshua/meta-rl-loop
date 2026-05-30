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
