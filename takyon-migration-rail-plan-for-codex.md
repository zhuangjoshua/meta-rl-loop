# Migration-Rail Hardening — Plan for Codex

**Status: READY FOR CODEX. Tracked workstream of [takyon-modularization-plan.md](takyon-modularization-plan.md) (DB-authority rail; feeds Stage 3's dev-DB provisioner). Progress is tracked in [takyon-modularization-progress.md](takyon-modularization-progress.md) under "Migration rail".**
Date: 2026-07-02. Author: Claude (modularization goal session), for parallel execution by Codex while Stages 2/5 land.

## 0. Context — what happened today (read this first, it is the whole motivation)

Applying migration `0059_worker_pools_and_job_reservations.sql` (modularization Stage 2) to prod exposed that **the prod database was never built by the tracked migration runner**. It was built piecemeal through the Supabase SQL editor as the `postgres` role (memories record 0001–0035 applied there). Consequences discovered live:

1. `takyon_migration` (the DSN `TAKYON_MIGRATION_DATABASE_URL` on the operator VPS, `/opt/takyon/secrets/.env`) could not `ALTER` the early tables — they were owned by `postgres`.
2. The runner (`plugins/takyon/db/runner.py::run_migrations`) has **no version table** — it replays *all* files every run, relying on per-file idempotence. So the first honest replay as `takyon_migration` tripped, file by file, on every privileged statement written in the editor era: `SET ROLE takyon_app` (0031), membership grants (0038), membership revokes (0046), object ownership (functions/types).
3. The local PG rig only runs all migrations clean because the Stage-0 work gave the rig's `takyon_migration` the **`WITH ADMIN OPTION` memberships the migrations require**. Prod lacked them.

**One-time surgery applied to prod on 2026-07-02 (via Supabase SQL editor as `postgres`, operator-confirmed, in `grant takyon_migration to postgres; … ; revoke` sandwiches):**

- `ALTER … OWNER TO takyon_migration` for ALL `public`-schema objects previously owned by `postgres`: ~50 tables (indexes + owned sequences followed), then routines (via `alter routine` with `oid::regprocedure` signatures), standalone sequences, views, and types (e.g. `safebox_usage_gate_result`).
- `grant takyon_app to takyon_migration with inherit false, set true;` (0031's requirement)
- `grant takyon_app to takyon_runtime with inherit false, set true;` (0038's requirement)
- `grant takyon_app / takyon_app_runtime / takyon_operator_runtime / takyon_safebox_authority / takyon_runtime to takyon_migration with admin option;` (the durable fix — lets the runner's own role-surgery statements, e.g. 0046's revokes, execute as `takyon_migration` on every replay)

Coordination note: another agent session drove the editor + VPS runner reruns; verify `run_migrations` reports **all 59 files applied** (last = `0059_worker_pools_and_job_reservations.sql`) before starting this plan's work, and do not run concurrent role surgery.

**The lesson this plan encodes:** the schema-authority topology (who owns what, which memberships the migration role holds) was *implicit* — reconstructable only by archaeology. It must become **code with one source of truth**, asserted loudly, consumed by prod, the local rig, and the Stage-3 dev provisioner alike.

## 1. Deliverables (each independently shippable, in order)

### D1 — `db/topology.sql`: the canonical role/ownership topology, as code

One idempotent SQL file under `hermes-agent-main/plugins/takyon/db/` (NOT in `migrations/` — it is a topology bootstrap, not a schema migration) that, run by a sufficiently privileged role, converges a database to the required authority topology:

- Creates the five roles if missing (`takyon_migration`, `takyon_operator_runtime`, `takyon_app_runtime`, `takyon_safebox_authority`, `takyon_app`, plus `takyon_runtime` if the rig uses it) with NOLOGIN as appropriate — passwords/logins are provisioner concerns, not topology.
- Grants `takyon_migration` the `WITH ADMIN OPTION` memberships listed in §0.
- Grants the two `with inherit false, set true` memberships listed in §0.
- Transfers ownership of every `public`-schema table/routine/sequence/view/type not owned by `takyon_migration` to it (the §0 sweeps, generalized, scoped to `schema public` ONLY — never `REASSIGN OWNED`, which on Supabase would touch auth/storage/extension internals).

Source-of-truth rule: the local PG rig's conftest role bootstrap and the future Stage-3 dev provisioner must CONSUME this file instead of duplicating the topology. Find the rig's current role-creation code in `tests/plugins/conftest.py` (the Stage-0 "faithful login-roles" work) and refactor it to execute `topology.sql`, keeping only rig-specific bits (LOGIN, passwords, throwaway DB names) local. One topology, three consumers (rig, dev, prod).

### D2 — Topology assertion in the runner (fail loud, never mid-deploy)

Extend `plugins/takyon/db/runner.py` with `assert_migration_topology(conn)`:

- Read-only checks: current role can administer the app-role memberships it needs (query `pg_auth_members.admin_option`), and no `public` object is owned by a role other than `takyon_migration` (tables, routines, sequences, views, types — the same five classes).
- Called at the TOP of `run_migrations` when connected as `takyon_migration` (skip when the caller is a rig superuser/hermetic conn — key off `current_user`), raising one exception that lists EVERY missing grant/ownership with the exact `ALTER/GRANT` statement to fix it — so the next drift is a 5-second read, not an afternoon of whack-a-mole.
- A unit test on the rig: break the topology in a throwaway DB (revoke one admin option), assert the error names the exact missing grant.

### D3 — `takyon migrate` — one tracked entrypoint replacing ad-hoc heredocs

- New CLI subcommand (takyon_cli/main.py, registered per the COMMAND_REGISTRY conventions in `hermes-agent-main/CLAUDE.md`) that: resolves the migration-plane DSN (`resolve_database_url(plane='migration')`), asserts the `migration` PG role (`assert_takyon_pg_role`), runs `assert_migration_topology`, runs `run_migrations`, prints the applied list + a post-run schema fingerprint (hash of sorted (table, column, type) — cheap drift evidence).
- The VPS deploy step becomes: `ssh … runuser -u takyon -- env … takyon-cli migrate` — update the deployment notes in the workspace `CLAUDE.md` (the agents.md; the operator's rule: deploy-rail changes must be tracked there so every future push+deploy follows them). Replace the "deploy heredoc imports plugins.takyon and runs run_migrations" sentence with the command.
- Guard rails: refuses to run when `TAKYON_HOST_ROLE` is unset/subuser; `--dry-run` prints the file list without executing (the runner has no version table, so "pending" = all files — say so honestly in the output).

### D4 — CI idempotence + privilege-contract check (closes plan Q3's gap for migrations)

- A CI job (extend `.github/workflows/tests.yml`) with a Postgres service container that: executes `topology.sql`, then `run_migrations` **twice** (idempotence), connected as a `takyon_migration` login role — NOT superuser — so any future migration file containing a statement `takyon_migration` cannot execute (the editor-era failure mode) goes red in CI instead of red on prod.
- Keep it scoped: this job runs only the runner + topology assertion, not the PG test suites (fast, <2 min).

### D5 — Documentation (small, in the same PRs)

- `db/README.md` (new, short): the topology model, why no version table, the replay-idempotence contract for AUTHORING migrations ("must run as takyon_migration; role surgery only on roles it administers; `create … if not exists` / `on conflict` everywhere; never assume postgres").
- Workspace `CLAUDE.md` deployment notes: point at `takyon migrate` (D3) and the topology file; record the 2026-07-02 one-time prod surgery in one sentence so future agents don't re-derive it.

## 2. Acceptance (proof before checkbox, per the goal's rules)

1. **Rig:** fresh throwaway DB → `topology.sql` → `run_migrations` × 2 as a `takyon_migration` LOGIN role → all files apply, second run no-ops clean; the conftest consumes topology.sql (grep: no duplicated grant lists).
2. **CI:** the D4 job green on a PR that adds a deliberately-privileged bad migration (test the tripwire, then remove the bad file).
3. **Prod:** `takyon migrate` (D3) over SSH reports all files applied + topology assertion passing — run it once for real; that run IS the acceptance.
4. **No security drift:** `takyon_app_runtime`'s privileges byte-identical before/after (dump `information_schema.role_table_grants` for it and diff); subuser plane untouched.

## 3. Boundaries / gotchas

- **Never `REASSIGN OWNED BY postgres`** on Supabase (platform internals live outside `public`).
- Do not add a migration version table — replay-idempotence is the established contract (changing it is out of scope and would fork the rig/test fixtures).
- Don't touch `migrations/*.sql` history files except where a file is PROVEN non-replayable as `takyon_migration` on the rig; fix those minimally (guards, not rewrites) and note each in the PR.
- The operator-prod local rail and Stage-2/5 worktrees (`takyon-stage2-claimscope`, `takyon-stage5-monthly`) are in flight in other sessions — this plan touches `db/runner.py`, `takyon_cli/main.py` (new subcommand), conftest, CI, docs; NO overlap with `jobs.py`/`worker*.py`/`app_entitlements.py`/`core.py`. Keep it that way; if you need a shared file, coordinate via the progress file.
- Shared-tree discipline: isolated worktree off latest `origin/main`, never `git stash`, land via fast-forward-able merges; deploy per the CLAUDE.md rail (green GH run ≠ VPS deployed — rsync is the activation path).
