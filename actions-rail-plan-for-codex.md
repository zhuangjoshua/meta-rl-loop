# Implementation Plan: `actions` Product Runtime Rail

**For: Codex (implementing agent). Author: Fable 5 (planning only). Date: 2026-06-12.**

Goal: give generated products governed server-side compute — per-product TypeScript
functions executed in a bounded Deno subprocess, invocable over HTTP by a signed-in app
customer or by a schedule, metered through the existing usage ledger, with an explicit
outbound-network allowlist. This breaks the "textarea → one-shot generate → save → history"
skeleton: products gain "input → backend action → result → return later."

Out of scope (do NOT build): wasmtime/Spin, flows/LangGraph rail, gateway tool-calling,
LiteLLM, inbound webhooks, customer email, object storage, records-v2 queries, acceptance
test gate. If you find yourself adding any of these, stop.

---

## 0. Ground rules (non-negotiable)

1. Read `/Users/Zygote/Downloads/takyon/CLAUDE.md` first and obey its Operating Model.
   Parsimony: one path, no test-mode forks, no skill-local duplicates of anything below.
2. **All line numbers in this plan are anchors, not gospel.** Before editing any symbol,
   re-locate it with grep. If a named symbol does not exist or looks materially different
   from its description here, STOP on that item and report the discrepancy instead of
   improvising a parallel structure.
3. Tests run via `scripts/run_tests.sh` only — never bare pytest. Behavioral coverage MUST
   live in suites that execute without Postgres (see §10); PG-gated tests are additive
   verification only. Do not report a feature as verified on the strength of a test that
   skipped.
4. Both DB backends: every new table = PG migration in
   `hermes-agent-main/plugins/takyon/db/migrations/` (shape-guard header, §6) AND a SQLite
   `CREATE TABLE IF NOT EXISTS` in `core.py::_init_db()`.
5. Receipts and audit events for every state change and every invocation. Truthful errors
   everywhere; never fabricate an action result, never silently skip a missing runtime.
6. Commit in the OUTER repo (`/Users/Zygote/Downloads/takyon`), not nested git metadata.
   Follow the CLAUDE.md fast path (focused local checks → push → `gh run watch`).
7. Final report format: per plan section, `done` / `attempted` / `blocked` with file paths,
   receipts, and the exact commands you ran. No "done" without read-back evidence.

---

## 1. Decision record (already decided — do not relitigate)

| # | Decision | Value |
|---|----------|-------|
| D1 | Runtime | Deno subprocess per invocation. No Docker, no wasm, no worker-pool daemon. |
| D2 | Deno flags | `deno run --quiet --no-prompt --no-remote --deny-write --allow-read=<runner_dir>,<actions_dir> --allow-net=<rails_host:port>[,<outbound_hosts…>]`. **Never** `--allow-run`, `--allow-ffi`, `--allow-env`, `--allow-write`, `-A`. `--no-remote` forbids remote imports (an allowlist bypass vector). v1 actions are self-contained TS files: std-lib + `fetch` only, no npm. |
| D3 | Resource caps | On Linux with `systemd-run` present: wrap as `systemd-run --scope --quiet -p CPUQuota=50% -p MemoryMax=256M -p TasksMax=32 -- deno …`. Else (macOS dev): plain subprocess. Both paths: hard wall deadline via process-group kill (`start_new_session=True`, `os.killpg` on timeout). Record which isolation ran in metadata (`"systemd-scope"` / `"subprocess"`). |
| D4 | Timeouts | http-trigger default 60s; schedule-trigger 120s. Config-overridable, hard max 120s. |
| D5 | Pricing | Flat per-invocation platform price, default **2000 microUSD** ($0.002), config key (§8). `agent/usage_pricing.py` is strictly model/token pricing — do NOT add an action entry there. AI calls made *by* an action meter separately through the generate rail as today (no double-charge: the invocation price covers compute only). |
| D6 | Metering | Reserve-then-settle through the existing usage rails: PG via `app_usage.reserve_usage/settle_usage/release_usage` with `purpose="action_invoke"`, `route="actions"`; SQLite via the existing direct-insert pattern in `core.py` (~15637). `reservation_key` = the invocation idempotency key. Budget exceeded → HTTP 402. |
| D7 | HTTP identity | The action receives the **invoking caller's own session token** pass-through. No minting on the HTTP path. Owner tokens (`Bearer tk_`) stay rejected on the app plane. |
| D8 | Schedule identity | A per-business **service principal**: an `app_users` row with email `scheduler@service.<slug>.takyon.invalid`, `metadata_json` containing `{"service": "action_scheduler"}`, tier `"service"`. Created idempotently by the dispatcher. It can never log in (magic-link guard, §7.4), never appears in directory (no profile row, so `directory_enabled` never set). Schedule runs mint a 15-minute session for it and revoke in `finally`. Receipts record `principal: "service"`. This is a documented system principal, not a fake customer. |
| D9 | Schedules substrate | Extend the existing wakes/jobs substrate (`plugins/takyon/wakes.py`, `db/migrations/0010_jobs_and_wakes.sql`, `worker.py` handler dict). One new job kind `"product_action"`. Due-computation uses **croniter 6.0.0 — already a pinned dependency**. No second scheduler. |
| D10 | Schedule floor / misfire | Cron expressions must fire no more often than every 15 minutes (validator-enforced). Misfire policy mirrors wakes: `next_run_at = greatest(now, next_run_at)` advanced by croniter — one catch-up fire, never a backfill burst. |
| D11 | Contract fields | `product_workflow.actions: [{name, trigger: "http"\|"schedule", schedule?, description?}]` and `product_workflow.outbound_hosts: [host[:port]]`. Caps: ≤ 10 actions, ≤ 8 hosts. |
| D12 | Action files | `product/site/actions/<name>.ts`, default-exporting `async (payload, ctx) => result`. |
| D13 | Rate limit | Mirror the directory limiter: in-memory, per `(business, session_token)`, **20 invocations / 60s**, HTTP 429. Plus max 1 concurrent invocation per business (in the runner, truthful 429 `"action_already_running"` on collision). |
| D14 | Payload caps | Request body ≤ 64 KB (HTTP 413). Action stdout ≤ 256 KB (truthful error if exceeded). stderr captured, truncated to 16 KB into metadata. |
| D15 | New leaf module | `hermes-agent-main/plugins/takyon/app_actions.py` owns: command construction, harness protocol, subprocess execution, schedule due-computation, contract→schedule-state reconciliation. DB writes ride existing stores; no SQL inside the leaf beyond the schedule-state table helpers. |
| D16 | Tool name | `business_invoke_app_action` (handler `handle_business_invoke_app_action`), matching the `business_*_app_*` convention. One tool only. Run history is readable through usage events, audit events, and receipts — no listing tool. |
| D17 | Bootstrap | `DEFAULT_BOOTSTRAP_ACCESS_SHELL_RUNTIME_FEATURES` stays `("auth", "account", "profile", "checkout")`. Pin with a test. |
| D18 | No session minting in the invoke tool | `business_invoke_app_action` never mints, synthesizes, or impersonates a session — it requires a real `session_token`. Operator verification flows through the existing auth rails: in test mode, create a test customer, obtain a token via `business_request_app_magic_link` + `business_verify_app_magic_link`, then invoke. The ONLY session minting anywhere in this feature is the schedule dispatcher's 15-minute service-principal session (D8). |
| D19 | Rails base resolution | One canonical resolver `app_actions.resolve_rails_base()`: (1) config key `plugins.takyon.app_actions.rails_base_url` when set; (2) on the HTTP-trigger path, the serving process's own bound host:port; (3) otherwise a truthful error naming the config key. Never a guessed port, never the operator/dashboard plane (see G4 — routing is split across operator and sub-user planes). `ctx.base_url` and the rails entry in `--allow-net` both derive from this one resolver. |
| D20 | `included_action_quota` stays inert | The legacy entitlements field `included_action_quota` (`plugins/takyon/app_entitlements.py` ~17) remains metadata-only. The actions rail neither reads nor enforces it; v1 has no per-plan included-action allowance. Any future per-plan quota must be an explicit, documented entitlements gate — do not silently wire this field into the new rail, and say so in the skill text so two "action budget" concepts never coexist. |

---

## 2. Verified anchor map (re-grep each before editing)

All paths relative to `hermes-agent-main/` unless noted.

| Surface | File | Symbol (≈line) |
|---|---|---|
| Rails registry | `plugins/takyon/core.py` | `PRODUCT_RUNTIME_RAILS` (~325); `records` entry (~378–397) is the pattern to copy |
| Rail deps / order / bootstrap | `core.py` | `_RUNTIME_FEATURE_DEPENDENCIES` (~466), `_RUNTIME_FEATURE_ORDER` (~476), `DEFAULT_BOOTSTRAP_ACCESS_SHELL_RUNTIME_FEATURES` (~497) |
| Worker-contract injection | `core.py` | `_subuser_app_worker_contract_block` (~5650); rails loop appends `spec["worker_contract"]` lines (~5625–5638) |
| Contract shape normalize | `core.py` | `_surface_product_workflow_shape` (~1212–1315), `_normalize_surface_string_list` (~1070) |
| Contract validator | `core.py` | `_validate_product_workflow_contract` (~1371–1431); raises `TakyonError` with exact prose (examples in §5) |
| Contract persistence | `core.py` | SQLite `app_surface_contracts` (~11474); PG via `leaves["identity"]`; mirror written in `_rewrite_app_files` (~12288), surface.md sections (~12297–12503) |
| SQLite schema | `core.py` | `_init_db` (~11332); `app_records` table (~11528) is the model; `_migrate_db` (~11707) |
| App users / sessions | `core.py` | `app_users` (~11498, has `metadata_json`), `app_sessions` (~11570); mint pattern `_random_token`/`_hash_token` (~17762); validate join (~17792) |
| Usage metering (PG) | `plugins/takyon/app_usage.py` | `reserve_usage` (323), `settle_usage` (398), `release_usage` (456); `AppBudgetExceeded` (63), `AppUserBudgetExceeded` (80) |
| Usage metering (SQLite) | `core.py` | direct INSERT into `app_usage_events` (~15637–15666) |
| Wakes/jobs | `plugins/takyon/wakes.py`, `worker.py`, `plugins/takyon/jobs.py`, `db/migrations/0010_jobs_and_wakes.sql` | `upsert_wake_schedule`, `dispatch_due_wakes` (wakes.py 137); idempotency window key `wake:<slug>:<YYYYMMDDHH24MI>`; `FOR UPDATE SKIP LOCKED`; worker handler dict in `worker.py` / `jobs.run_one` |
| App HTTP plane | `takyon_cli/web_server.py` | `_takyon_app_post` (~2494), `_takyon_app_get` (~2367); session cookie helper `_takyon_app_session_token` (~2084); owner-token reject `_takyon_owner_token_on_app_plane` (~2368); `_takyon_app_tool` envelope (~2068); `_takyon_app_read_json` (~2241); `_PRODUCT_APP_RAIL_ROUTES` (~2327); `_normalize_product_rail_route` (~2342); directory limiter `_takyon_app_rate_limit_directory_lookup` (~2088); records POST dispatch (~2592–2606) is the pattern to copy |
| Capabilities | `core.py` | `business_check_runtime_capabilities` handler (~16458–16516); default probe list (~16473); `_runtime_capabilities` |
| Publish gate | `core.py` | `business_refresh_product_surface` → `_finalize_product_surface_refresh` (~16790); `_surface_refresh_exact_blocker` |
| No-pretend scan | `core.py` | `_PRETEND_PRODUCT_PATTERNS` (~7515), `_RUNTIME_BACKED_PATTERNS` (~7544), `_scan_for_pretend_product_state` (~7629) |
| AppKit client | `plugins/takyon/subuser_app_kit/runtime-client.js` | `ensureRail` (128–134), `jsonRequest` (77–98), `routeUrl` (136–143), `saveRecord` (~250) is the method pattern |
| Migrations | `plugins/takyon/db/migrations/` | latest is `0022_*.sql`; copy guard header style from `0019_app_records.sql` |
| Owner skill | `skills/takyon/takyon-app-runtime/SKILL.md` | section list + frontmatter per the repo template |
| Tests (hermetic) | `tests/takyon_cli/test_web_server.py` | monkeypatch handler + `TestClient` pattern (~2126–2253) |
| Tests (validator) | `tests/plugins/test_takyon_customer_experience_shape.py` | direct unit calls on `takyon_core._validate_product_workflow_contract` |

Known gaps you must resolve by reading source (do this in step V0):
- **G1**: whether `wake_schedules` PK is `business_slug` alone or `(business_slug, kind)`. If
  business-slug-alone, do NOT alter it — the actions dispatcher uses its own state table
  (§7.2) and only the *jobs* table + worker handler dict are shared.
- **G2**: how the `generate` route in `_takyon_app_post` maps `AppBudgetExceeded` to an HTTP
  status. Mirror it exactly for actions (if it maps to 402, do 402; if the precedent is an
  error envelope with 400, match it and note that in your report — do not invent a third shape).
- **G3**: whether per-business rail *selection* is enforced in `web_server.py` or in the
  core handler for existing rails. Enforce actions-rail selection in the **core handler**
  (canonical) regardless; the web layer only knows the route exists.
- **G4**: routing is split across TWO planes with two tracked Caddyfiles at the workspace
  root (NOT hermes-agent-main): `deploy/argon-alpha-14/Caddyfile` (operator plane, ~25) and
  `deploy/takyon-subuser/Caddyfile` (sub-user/product plane, ~16). Read both. If product-app
  rail paths are explicitly matched, add `actions` on the sub-user plane only; if proxied
  wholesale, no change. Apply only via the tracked apply script. The D19 resolver must
  target the sub-user/app plane — a scheduled action must never be pointed at the operator
  dashboard plane by accident.
- **G5**: how `/cron tick` works in SQLite mode (no PG jobs table). Wire the SQLite path of
  the schedule sweep into that same tick code path (§7.3), not a new loop.
- **G6 (sequencing — the skill layer is LIVE AHEAD of this runtime)**: the operator landed
  the actions routing policy in `takyon-product-workflow` and `takyon-build-product`
  SKILL.md on 2026-06-12, repo and installed copies matching. Until §3–§5 land, a CEO
  selecting `actions` in runtime_features is rejected truthfully by
  `_normalize_runtime_features(strict=True)` (~919, enforced at upsert ~14292 — safe), but
  `product_workflow.actions` is silently DROPPED by `_surface_product_workflow_shape`
  (unknown keys do not survive normalization — a pretend-behavior window). Therefore land
  §3 (registry), §4 (shape), §5 (validator) FIRST, before any runner work, and verify the
  drop is closed with a shape round-trip test.

---

## 3. Registry + ordering (core.py)

3.1 Add to `PRODUCT_RUNTIME_RAILS`, copying the `records` entry shape exactly:

```python
"actions": {
    "owner_skill": "takyon-app-runtime",
    "tools": ["business_invoke_app_action"],
    "endpoints": [
        ("POST", "actions/<name>"),
    ],
    "worker_contract": [
        "Backend actions are per-product TypeScript files under product/site/actions/<name>.ts, default-exporting `async (payload, ctx) => result`; declare each one on the surface contract under product_workflow.actions before referencing it from UI.",
        "Inside an action: call the product's own rails over HTTP using ctx.base_url + ctx.session_token (the invoking customer's own session), and fetch only hosts declared in product_workflow.outbound_hosts; there is no filesystem write, no shell, no env access, and no npm/remote imports.",
        "Invoke actions from the UI through the shared runtime client's invokeAction(name, payload); never fake an action result, never simulate one client-side, and surface the runtime's truthful 402/429/timeout errors to the customer.",
    ],
},
```

3.2 `_RUNTIME_FEATURE_DEPENDENCIES`: add `"actions": ("auth", "account"),`.

3.3 `_RUNTIME_FEATURE_ORDER`: append `"actions"` at the end of the tuple.

3.4 Do NOT touch `DEFAULT_BOOTSTRAP_ACCESS_SHELL_RUNTIME_FEATURES`.

3.5 Re-read how `## Runtime Rails` in surface.md and the `_takyon` surface-context payload
are generated from the registry; confirm the new entry flows there with zero extra code.
If any per-rail hardcoded list exists elsewhere (grep `"generate"` co-occurring with
`"records"` in list literals across `core.py`, `web_server.py`, `subuser_app_kit/`), update
it and flag it in your report as a registry-bypass smell.

---

## 4. Contract shape (core.py `_surface_product_workflow_shape`)

Parse and normalize two new `product_workflow` fields (follow the existing normalize
helpers' style; keep deterministic ordering and dedupe):

- `actions`: list of dicts. Normalize each to
  `{"name": str, "trigger": "http"|"schedule", "schedule": str|None, "description": str|None}`.
  Accept string entries as `{"name": s, "trigger": "http"}` for ergonomic parity with
  `_normalize_surface_string_list`, but emit the dict form.
- `outbound_hosts`: list of strings via `_normalize_surface_string_list`, lowercased.

Persist them inside the same `product_workflow` JSON blob (no new columns — confirm the
blob is what's stored today; it is part of the contract metadata/shape persisted to
`app_surface_contracts`). Surface both in the `## Product Workflow` section of
`product/surface.md` (actions as `name (trigger[: schedule])` lines; hosts as a list) and in
`product/runtime.md` "Rails By Owner" via the registry flow from §3.5.

---

## 5. Validator (core.py `_validate_product_workflow_contract`)

Add rules, matching existing `TakyonError` prose style exactly (lowercase field paths,
actionable phrasing). Required behaviors:

1. `actions` entry name must match `^[a-z][a-z0-9_-]{1,63}$` and be unique →
   `product_workflow.actions names must be unique lowercase slugs (a-z, 0-9, -, _), got: <name>`
2. `trigger` ∈ {`http`, `schedule`} → name the bad value.
3. `trigger == "schedule"` requires `schedule`; `trigger == "http"` forbids it.
4. Schedule syntax: `croniter.is_valid(expr)` (import croniter — already pinned at 6.0.0).
   Then enforce the 15-minute floor: compute the first three fire times from a fixed base
   with croniter; if any consecutive delta < 900s →
   `product_workflow.actions schedule for <name> fires more often than every 15 minutes; slow it down or make it an http action`
5. `len(actions) > 10` → cap error naming the count. `len(outbound_hosts) > 8` → same.
6. Each outbound host must match `^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?$`, must not
   contain a scheme, slash, or `*`, and must not be `localhost`, a loopback (`127.`),
   link-local (`169.254.`), or `0.0.0.0` literal →
   `product_workflow.outbound_hosts entries must be bare public hostnames (host or host:port), got: <value>`
7. Cross-rule: any `actions` declared ⇒ `"actions"` must be in selected runtime_features
   (mirror the `persistence_rail` error style). Converse: `"actions"` selected with zero
   declared actions →
   `runtime_features includes actions but product_workflow.actions declares none; declare the actions or drop the rail`
8. File-existence rule: when the surface has a real product `source_path` AND actions are
   declared, each `product/site/actions/<name>.ts` must exist. Wire this where source-path
   reality is already known — if the validator has no filesystem context (check call sites
   ~14248, ~14289), put the existence check in `business_refresh_product_surface`'s blocker
   assembly instead, as a truthful refresh blocker:
   `declared action <name> has no file at product/site/actions/<name>.ts`. Report which home
   you used and why.

---

## 6. Storage: schedule state table (the only new table)

No invocations table — invocation records are usage events (purpose `action_invoke`),
audit events, and receipt files.

**PG migration** `plugins/takyon/db/migrations/0023_app_action_schedules.sql` (use the next
free number; copy the `0019_app_records.sql` shape-guard header, keyed on a distinguishing
column, e.g. `action_name`):

```sql
create table if not exists app_action_schedules (
  business_slug text not null references businesses(slug) on delete cascade,
  action_name   text not null,
  schedule      text not null,
  enabled       boolean not null default true,
  next_run_at   timestamptz not null,
  last_run_at   timestamptz,
  last_status   text,
  last_error    text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  primary key (business_slug, action_name)
);
```

**SQLite** mirror in `_init_db()` next to `app_records`, same columns (TEXT timestamps,
matching local conventions), FK to `businesses(slug) ON DELETE CASCADE`.

Reconciliation (in `app_actions.py`, called from the surface-contract upsert path in core):
on every contract upsert, upsert rows for declared schedule-trigger actions (seed
`next_run_at` = croniter next from now), disable rows for actions no longer declared
(`enabled = false`, never delete — audit trail). Idempotent; receipts not needed for
reconcile (the contract upsert already has one), but include the reconciled action names in
the contract tool's result payload.

---

## 7. The leaf: `plugins/takyon/app_actions.py` + execution

### 7.1 Runner

Public surface (keep signatures in this spirit; adapt to local conventions):

```python
class ActionInvocationError(Exception): ...   # truthful, user-displayable message

def invoke_action(
    *, store, business_slug: str, action_name: str, payload: dict,
    principal: dict,          # {"kind": "session", "session_token": str, "user": {...}}
                              #  | {"kind": "service", "user": {...}, "session_token": str}
    trigger: str,             # "http" | "schedule"
    idempotency_key: str,
    config: ActionRuntimeConfig,
) -> dict:                    # {"ok": True, "result": ..., "run": {...metadata...}}
```

Execution sequence (each step's failure is a truthful error + `release_usage` +
receipt + audit event — never a silent skip):

1. Load contract; require `"actions"` selected, action declared, trigger matches the
   declaration (an http call to a schedule-only action → generic `not found` shape at the
   route layer; the handler returns `{"success": False, "error": "not found"}` to avoid
   existence leaks — match G3 findings).
2. Resolve the action file: `<business_root>/product/site/actions/<name>.ts`. Path
   containment: name already validator-constrained, but re-verify
   `resolved.is_relative_to(actions_dir)` after `resolve()` anyway, and that it's a regular
   file. Missing file → `declared action <name> has no file at product/site/actions/<name>.ts`.
3. Capability gate: `shutil.which("deno")` once per invocation (cheap). Missing →
   `ActionInvocationError("actions rail requires the deno runtime on this host; install deno and re-run business_check_runtime_capabilities")`.
4. Concurrency gate: in-process per-business lock (module-level dict of `threading.Lock`,
   non-blocking acquire). Busy → error `"action_already_running"` (route maps to 429).
5. Reserve: flat price from config (D5/D6). PG: `app_usage.reserve_usage(...,
   purpose="action_invoke", route="actions", reservation_key=idempotency_key,
   metadata={"action": name, "trigger": trigger})` — service principal passes
   `user_monthly_limit_microusd=None`. SQLite: mirror the core.py direct-insert reserve
   shape. `AppBudgetExceeded`/`AppUserBudgetExceeded` → map per G2 (target 402).
6. Build command (D2/D3). `--allow-net` = the host:port from `resolve_rails_base()` (D19)
   + declared `outbound_hosts` verbatim. Subprocess env is scrubbed: `{"DENO_DIR": <per-run tmp>, "NO_COLOR": "1",
   "PATH": minimal}` — never the parent env.
7. Harness: static file `plugins/takyon/subuser_app_kit/action_runner.ts` (ships with the
   repo; `--allow-read` covers its directory). It reads one JSON request from stdin:
   `{"payload": ..., "ctx": {"base_url", "session_token", "business", "trigger",
   "user": {"id", "email", "tier"}, "mode"}}`, dynamic-imports the action module from
   `Deno.args[0]`, requires a default-export function, calls it with `(payload, ctx)`,
   writes exactly one JSON line to stdout: `{"ok": true, "result": ...}` or
   `{"ok": false, "error": "<message>"}` (catches everything, including permission errors,
   into truthful `error` strings). `ctx.base_url` =
   `<resolve_rails_base()>/api/takyon/apps/<slug>` (D19 — same resolver as `--allow-net`;
   a schedule run with no resolvable base fails truthfully naming the config key).
8. Run with wall deadline (D4): `Popen(start_new_session=True)`,
   `communicate(timeout=...)`, on `TimeoutExpired` → `os.killpg(SIGKILL)` → error
   `action <name> exceeded its <N>s wall deadline and was killed`. Enforce stdout/stderr
   caps (D14).
9. Settle on success (`actual = estimated = flat price`, metadata: `wall_ms`, `exit_code`,
   `isolation`, `stdout_bytes`, `trigger`, `action`); release on any failure (metadata:
   truncated stderr + error).
10. Receipt file `metrics/receipts/app-actions/<idempotency_key>.json` (follow the
    magic-link receipt helper pattern): request summary (no payload bodies > 2 KB),
    principal kind, timing, cost, outcome. Audit event via the existing `_record_event`
    pattern with `event_type="app.action.invoke"`.
11. Mode semantics: same path in test and live. Test-mode businesses still execute real
    local Deno (it's local compute, not an external side effect); the receipt records
    `mode`. Outbound fetches in test mode are NOT stubbed by us in v1 — the allowlist is the
    guardrail; note this explicitly in the skill text.

### 7.2 Schedule state + due computation

In `app_actions.py`: `compute_next_run(schedule, after) -> datetime` (croniter),
`reconcile_action_schedules(conn, business_slug, actions)` (§6), and
`due_action_schedules(conn, now) -> list[row]` (PG: `FOR UPDATE SKIP LOCKED`; SQLite: plain
select — single-process). Advance `next_run_at = compute_next_run(schedule,
max(now, next_run_at))` — mirrors the wakes catch-up bound (one fire, no backfill).

### 7.3 Dispatch

PG path: beside the existing `dispatch_due_wakes` tick (find its caller in `worker.py`),
enqueue one job per due schedule into the existing jobs table, kind `"product_action"`,
idempotency key `action-sched:<slug>:<name>:<YYYYMMDDHHMM>` (mirror the wake window-key
collapse), payload `{"business": slug, "action": name}`. Register a `"product_action"`
handler in the worker handler dict that builds the service principal (D8: get-or-create
user via metadata marker, mint 15-min session with the existing `_random_token`/
`_hash_token` insert pattern, revoke in `finally`) and calls `invoke_action(...,
trigger="schedule", idempotency_key=<the window key>)`.

SQLite path (G5): wire the same sweep into the existing `/cron tick` code path.

Parsimony constraint (this is the least parsimonious seam in the plan — keep it bounded):
a queue dispatcher and a per-job executor are different moments on PG, so the leaf owns
exactly TWO canonical functions — not one, not three:

- `dispatch_due_action_schedules(store, now, enqueue)` — due query (`FOR UPDATE SKIP
  LOCKED` on PG), `next_run_at` advance, window idempotency-key construction; calls
  `enqueue(item)` for each due schedule and never executes anything itself.
- `execute_scheduled_action(store, business_slug, action_name, window_key)` — service
  principal get-or-create, 15-minute session mint/revoke, `invoke_action(...,
  trigger="schedule")`, receipts, `last_status`/`last_error` write-back.

PG adapters: the dispatcher tick passes `enqueue` = insert a `product_action` job (so the
jobs substrate keeps its SKIP LOCKED distribution and window-key collapse), and the worker
handler is a ≤5-line call into `execute_scheduled_action`. SQLite adapter: `/cron tick`
passes `enqueue` = call `execute_scheduled_action` inline. No third function, no logic in
any adapter; if an adapter starts growing logic of its own, stop and refactor it into the
two leaf functions.

### 7.4 Service-principal containment (every materialization path — five guards)

The reserved-identity predicate is the email domain: any address ending in
`.takyon.invalid` is a service identity. Centralize it as
`app_actions.is_service_email(email)` and use that one predicate at every guard below —
do not re-derive from `metadata_json` at call sites (metadata marks the row; the email
predicate also covers paths that run before any row exists).

1. **Creation**: ONLY via a dedicated helper
   `app_actions.get_or_create_service_principal(store, business_slug)` called by the
   schedule dispatcher. It inserts the `app_users` row directly (service marker in
   `metadata_json`, tier `"service"`), never through the generic customer-upsert path,
   and never creates a profile row.
2. **Magic-link REQUEST** (~17658): service email → return the normal generic success
   WITHOUT creating a link or sending anything (no enumeration leak, no login path).
3. **Magic-link VERIFY** (~17676): if the link's target user is a service identity, fail
   with the same generic invalid-link response used for expired/unknown tokens. Defense in
   depth — today's verify path mints sessions AND grants free-tier entitlements for any
   active user; a service principal must never collect either through a login flow.
4. **Generic customer/profile creation** (`business_upsert_app_customer` ensure paths
   ~14726/~14761, and the profile upsert path): reject service emails with a truthful
   error (`service identities cannot be managed as app customers`). These paths auto-ensure
   profiles/entitlements, which would make the service principal directory-eligible and
   plan-bearing — exactly what D8 forbids.
5. **Profile auto-materialization on read/mutate.** The runtime ensures profile rows in
   places beyond upsert: `business_read_app_profile` ensures a row on READ (~17983,
   ~18017), and directory upsert/disable can ensure a row and toggle `directory_enabled`
   by `app_user_id`/`session_token` (~14936). This vector is live, not theoretical:
   during a schedule run the ACTION CODE holds a valid service session and can call these
   rails itself over HTTP. First look for a shared profile-ensure helper — if one exists,
   a single `is_service_email` guard there covers all sites; if the ensure logic is
   per-site, guard each named site and say so in your report. Behavior: profile read for
   a service identity → truthful `service identities have no profile` error, no row
   created; directory upsert/disable targeting a service identity → truthful rejection,
   no row, no flag toggle.

Tests pin all five (§10).

---

## 8. Config

Follow the existing config pattern (verify: `takyon_cli/config.py` `DEFAULT_CONFIG`; if
plugin config genuinely doesn't flow there, fall back to module constants overridable via
env `TAKYON_APP_ACTIONS_*`, and say which you did):

```yaml
plugins:
  takyon:
    app_actions:
      rails_base_url: ""          # D19. Origin ONLY: scheme://host[:port], e.g. "http://127.0.0.1:9119".
                                  # Empty = unset: HTTP-trigger falls back to the serving process's own
                                  # bound address; schedule-trigger fails truthfully naming this key.
      invoke_price_microusd: 2000
      http_timeout_seconds: 60
      schedule_timeout_seconds: 120
      cpu_quota_percent: 50
      memory_max_mb: 256
```

`rails_base_url` normalization — one parser in `app_actions.py`, used by BOTH consumers,
because they need different projections of the same value: accept only
`scheme://host[:port]`; strip at most one trailing slash; reject any value carrying a
path, query, fragment, or userinfo with a truthful config error naming the key.
`ctx.base_url` = `<origin>/api/takyon/apps/<slug>`. The `--allow-net` rails entry =
`host:port` extracted from the same parsed origin (port inferred from scheme when absent:
80/443). Never derive one projection from the other by string surgery.

No secrets involved — do not route these through the safebox sensitive-key seam.

---

## 9. HTTP route, capabilities, AppKit, no-pretend, skills

### 9.1 web_server.py

- Add `"actions"` handling to `_normalize_product_rail_route` (`startswith("actions/")`,
  like records) and add the entry to `_PRODUCT_APP_RAIL_ROUTES`.
- In `_takyon_app_post`, add the dispatch branch (copy the records POST branch shape):
  `parts[0] == "actions" and len(parts) == 2` →
  401 `missing app session` without cookie; body via `_takyon_app_read_json` but FIRST
  enforce the 64 KB raw-body cap (413, `{"success": false, "error": "payload too large"}`);
  rate limiter `_takyon_app_rate_limit_action_invoke` (copy the directory limiter, 20/60s);
  then `_takyon_app_tool(handle_business_invoke_app_action({"business": business,
  "session_token": token, "action": parts[1], "payload": body.get("payload") or {},
  "idempotency_key": body.get("idempotency_key") or body.get("idempotencyKey") or
  f"action:{business}:{parts[1]}:{uuid.uuid4().hex}"}))`.
- Status mapping: per G2 for 402; `"action_already_running"` and rate-limit → 429; unknown
  action/rail → the generic `not found` 404 shape.
- GET on `actions/...` → generic 404 (no enumeration surface).

### 9.2 Core handler + tool

`handle_business_invoke_app_action` in core.py following `handle_business_upsert_app_record`'s
shape: resolve session → app user (existing session-validation join), then call
`app_actions.invoke_action(...)` with `principal={"kind": "session", ...}`. Register the
tool with schema (business, action, payload, idempotency_key, session_token — REQUIRED).
Two hard rules, each pinned by a test (§10):
- The tool NEVER mints, synthesizes, or impersonates a session (D18). Missing/invalid
  `session_token` → truthful error, no fallback. There is no `app_user_id` impersonation
  parameter at all.
- The handler enforces rail selection ITSELF: if `"actions"` is not in the business's
  runtime_features, return a truthful error before any subprocess work. Do not rely on
  the HTTP layer — the analogous record handlers do not enforce rail selection on the
  direct tool path, so this handler must.
CEO verification recipe (document in the skill, §9.6): in test mode, create a test
customer, obtain a session token via `business_request_app_magic_link` +
`business_verify_app_magic_link`, then invoke with that token. Schedule-trigger actions
are verified via `/cron tick`, never via impersonation. Tool result:
`{"success": True, "action", "result", "run": {...}}`.

### 9.3 Capabilities + publish blocker

- Add `"deno"` to the default probe list in `business_check_runtime_capabilities` and an
  ecosystem alias `"actions"` → probe `("deno",)` + `systemd-run` presence (informational).
- In `business_refresh_product_surface` blocker assembly: actions selected + declared ⇒
  (a) deno present, else blocker `actions rail requires the deno runtime on this host`;
  (b) every declared action file exists (§5.8 if homed here). Truthful blockers feed the
  existing worker repair retry — do not add new choreography.

### 9.4 AppKit (`subuser_app_kit/runtime-client.js`)

Add, following `saveRecord`:

```javascript
async invokeAction(name, payload = {}, options = {}) {
  ensureRail("actions");
  const actionName = String(name || "").trim();
  if (!actionName) throw new Error("action name is required");
  return jsonRequest(routeUrl(`actions/${encodeRoutePart(actionName)}`), {
    method: "POST",
    body: JSON.stringify({
      payload,
      idempotency_key: options.idempotency_key || options.idempotencyKey || undefined,
    }),
  });
}
```

Confirm how the built copy reaches products (`_takyon/runtime-client.js` materialization on
surface refresh) — no extra delivery step should be needed; verify one refreshed business
actually carries the new method.

### 9.5 No-pretend + inventory

- Add to `_RUNTIME_BACKED_PATTERNS`: `re.compile(r"\binvokeAction\s*\(")` and
  `re.compile(r"/actions/")` so honest action calls are never flagged.
- Extend the runtime-integration detection (`_PRODUCT_RUNTIME_INTEGRATION_PATTERNS`) so the
  inventory records action usage. Do NOT add speculative "fake action" regexes in v1.

### 9.6 Skills (after code is green)

- `skills/takyon/takyon-app-runtime/SKILL.md`: add `business_invoke_app_action` to
  frontmatter `requires_tools` and Quick Reference; add a Procedure subsection "Declaring
  and verifying backend actions" naming: contract fields (D11), file convention (D12),
  ctx semantics (D7/D8), price + budget behavior (D5), the exact verification steps
  (invoke tool → receipt path → usage event), and the deno capability gate. Include the
  D18 verification recipe (test customer + magic-link tools → session token → invoke) and
  an explicit note that the legacy entitlements field `included_action_quota` is inert
  metadata, not an action allowance (D20). Add Verification Checklist + Common Pitfalls
  entries (e.g., "schedule action with no service-principal receipt = dispatcher never
  ran; check /cron tick"). **This bullet is expanded into the binding work order in §14 —
  implement from §14, not from this summary.**
- `takyon-product-workflow` and `takyon-build-product` SKILL.md: **already landed by the
  operator (2026-06-12)** — actions-as-default-backend-leaf in product-workflow (When to
  Use, Procedure 5–7, Pitfalls, Checklist, Rules, Troubleshooting) and no-preseeding in
  build-product. VERIFY content against the shipped field shape rather than rewriting; the
  only permitted addition is one cross-reference from product-workflow to
  takyon-app-runtime for the exact `product_workflow.actions` field schema (the schema
  lives with the rail owner, not the doctrine skill). If the landed text contradicts the
  shipped schema (D11/D12), fix the schema reference in the skill and flag it in your
  report.
- Sync dance (mandatory): relaunch `./takyon`; expect "user-modified, skipping" for
  already-synced skills; run `takyon skills reset <name> --restore` for each edited skill;
  relaunch; verify `$TAKYON_HOME/skills/takyon/<name>/SKILL.md` matches the repo and the
  skills index lists it. Do not report skills live without this read-back.
- `plugins/takyon/API_REQUIREMENTS.md`: add the Deno section — local dev needs `deno` on
  PATH; VPS needs a one-time `deno` install (document `curl -fsSL https://deno.land/install.sh`
  or platform package, install location, and that `systemd-run` enables resource caps);
  absence degrades to a truthful rail blocker, never a silent skip. No new API keys.

---

## 10. Tests (the contract for "verified")

Hermetic — these MUST run in plain `scripts/run_tests.sh` with no Postgres and no deno:

1. **`tests/plugins/test_takyon_app_actions.py`** (new; unit, SQLite/no-DB where possible):
   - Command construction snapshot: exact flag list; assert `--allow-run`, `--allow-env`,
     `--allow-ffi`, `--allow-write`, `-A` are ABSENT; `--no-remote` present; allow-net is
     exactly rails host + declared hosts.
   - Path containment: traversal/absolute names rejected pre-subprocess.
   - Harness protocol: request encode / response decode; garbage stdout → truthful error.
   - Wall-deadline kill: monkeypatch the command to `[sys.executable, "-c", "import time; time.sleep(30)"]`
     with a 1s deadline → killed, error mentions the deadline, reservation released
     (assert via the store/monkeypatched usage seam).
   - Reserve/settle/release ordering: success settles once; each failure class releases.
   - Schedule math: croniter floor, due computation, `greatest(now, next)` no-backfill.
   - Service-principal containment (§7.4, all five guards): idempotent get-or-create via
     the dedicated helper only; magic-link REQUEST returns generic success with no link
     row; magic-link VERIFY of a link targeting a service identity returns the generic
     invalid response, mints no session, grants no entitlement; customer/profile upsert
     paths reject service emails with the truthful error; profile READ for a service
     identity creates no row and returns the truthful error; directory upsert/disable
     targeting a service identity (including via its own live session token, as an action
     would call it) is rejected with no row created and no `directory_enabled` toggle.
   - Handler hard rules (§9.2): `business_invoke_app_action` with `"actions"` absent from
     runtime_features → truthful error, no subprocess spawned (assert via monkeypatched
     runner seam); missing `session_token` → truthful error, no minting fallback.
   - Reconcile: contract upsert seeds/disables schedule rows idempotently (SQLite store).
2. **`tests/plugins/test_takyon_customer_experience_shape.py`** additions: validator matrix
   for §5 rules 1–7 (good + each failure, asserting exact message prefixes); registry
   invariants: `"actions"` in registry/order/deps, deps ⊆ registry, bootstrap tuple pinned
   without `"actions"`.
3. **`tests/takyon_cli/test_web_server.py`** additions (monkeypatched handler, TestClient):
   no session → 401; `Bearer tk_` → 403; happy path → 200 + handler args captured
   (idempotency fallback `action:<biz>:<name>:<hex>` asserted); oversize body → 413;
   21st call in window → 429; handler error envelope → mapped status per G2.
4. **`tests/plugins/test_takyon_app_actions_deno.py`** — integration, gated
   `@pytest.mark.skipif(shutil.which("deno") is None, reason="deno not installed")`:
   - echo action round-trips payload + ctx fields;
   - `Deno.writeTextFile` → fails, error names the denial;
   - `new Deno.Command("sh")` → fails;
   - `Deno.env.get` → fails;
   - `fetch` to a non-allowlisted host → fails; to an allowlisted local `127.0.0.1:<port>`
     test server → succeeds (allowlist includes it for this test only);
   - infinite loop killed at deadline.
   Each sandbox denial test pins the truthful error surfacing. Deno is currently NOT on
   this machine's PATH — installing it (e.g. `brew install deno`) is part of the
   implementation pass. If you land without it, the rail must be truthfully blocked
   end-to-end and the report must mark every sandbox-proof item
   `blocked: deno not installed locally` — never `done`. Report this suite's status
   separately from the hermetic suite either way.
5. PG: apply migration 0023 on the throwaway local rig (see memory: createdb + DSN env →
   `scripts/run_tests.sh tests/plugins/test_takyon_store_pg.py` pattern) and add a
   schedule-state CRUD case to an existing PG suite IF one fits naturally; this is
   additive, not the verification of record.

## 11. E2E through the real shell (manual, scripted in your report)

Temp `TAKYON_HOME`, `./takyon shell`: `/create` a test business (stay in test mode); have
the worker produce a product declaring one `http` action and one `schedule` action (or
hand-write the contract + files via the canonical tools if the full worker loop is too
heavy — say which you did); confirm: surface.md shows both + outbound_hosts; refresh
blocks truthfully with deno absent (rename the binary out of PATH to prove it, restore
after) and passes with it present; `business_invoke_app_action` succeeds and the receipt +
usage event exist; `/cron tick` fires the schedule once, with a service-principal receipt,
and does not double-fire in the same window; AppKit `invokeAction` works from the served
product page. Verify `/status`, `/files`, `/read` show the receipts.

## 12. Deploy notes

Code rides the normal fast path (outer-repo push → `gh run watch`). One-time ops the
workflow does NOT cover: install `deno` on the VPS (`argon-alpha-14`) and confirm
`systemd-run` is available; then run `business_check_runtime_capabilities` against a real
business and attach the output to your report. Caddy only if G4 demands it, via the
tracked apply script.

## 13. Named follow-ups (record, do not build)

Gateway tool-calling + run-level budget envelope ("flows"), inbound webhooks → action
trigger, customer email rail, object storage, records-v2 queries, wasmtime/Spin migration,
npm support in actions. These stay in `agentic-flows-research.md` §6–7.

---

## 14. Work order: close the verification-habit gap

**Status context (2026-06-12):** the runtime shipped (`app_actions.py`, registry entry,
validator, tool at core.py ~26827, usage purpose `action_invoke` at app_actions.py ~753),
and the routing skills landed. The remaining gap is behavioral: nothing teaches the CEO to
invoke an action before publish, and nothing makes "declared but never executed" visible.
Diagnosis: two implicit assumptions — (1) the CEO will invent a verification habit, (2)
worker-written action code is right first try — and both are currently caught by nothing.
This work order discharges them. It is three parts: TEACH (A), EVIDENCE (B), VERIFY (C).
Deliberately NOT in scope: any deterministic publish blocker on invocation status — that
would be the descoped acceptance gate by another name. Evidence + skill checklist is the
Hermes-native mechanism; if post-ship behavior shows CEOs still publishing never-invoked
actions, the escalation is the executable-acceptance-spec follow-up (§13), not more prose.

### 14A. `skills/takyon/takyon-app-runtime/SKILL.md` — the operational half

Frontmatter:
- `metadata.hermes.requires_tools`: add `business_invoke_app_action` (and
  `business_check_runtime_capabilities` if not already listed).
- `metadata.hermes.routing.owns`: add an entry for product backend actions
  (declare/operate/verify/receipts).
- `routing.when_to_use`: add "verifying or operating declared product actions
  (http or schedule) before reporting a product published".

Body — follow the skill's existing section order; add to each section, do not invent a
new layout:

1. **Quick Reference**: tool `business_invoke_app_action`; file convention
   `product/site/actions/<name>.ts` default-exporting `async (payload, ctx) => result`;
   contract fields `product_workflow.actions` (`{name, trigger: http|schedule,
   schedule?, description?}`) and `product_workflow.outbound_hosts`; receipt root
   `metrics/receipts/app-actions/`; usage purpose `action_invoke`; config keys under
   `plugins.takyon.app_actions.*` (`rails_base_url` required for scheduled actions).
2. **Procedure** — new subsection "Operating and verifying product actions", numbered:
   1. Read the surface contract (`business_read_app_surface_contract` / surface.md);
      confirm the `actions` rail is selected, each action is declared, and outbound hosts
      are the ones the action code actually fetches.
   2. Capability gate: `business_check_runtime_capabilities` with the actions/deno probe.
      Deno missing → record the exact blocker on the business and STOP; never simulate an
      invocation.
   3. HTTP-trigger recipe (D18 — no minting, ever): in test mode create a test customer
      with a normal email (NOT `*.takyon.invalid` — the runtime rejects service-domain
      identities on customer paths), then `business_request_app_magic_link` →
      `business_verify_app_magic_link` → session token → `business_invoke_app_action
      {business, action, payload, session_token}`. Expect
      `{"success": true, "result", "run"}`.
   4. Read-back (mandatory): open the receipt at
      `metrics/receipts/app-actions/<key>.json` (via `/files` + `/read` or
      `business_read_file`) and confirm the usage event (`purpose=action_invoke`) exists.
      An invoke without read-back does not count as verification.
   5. Schedule-trigger recipe: `/cron tick` (or the wake dispatcher), then confirm a
      receipt whose principal is `service`, the window idempotency key, and the schedule
      row's `last_status`/`last_run_at` advanced. Confirm the service identity acquired
      NO customer profile and NO directory presence.
   6. Failure handling: 402 → budget genuinely exhausted (configure or accept; never
      bypass); 429 → rate limit or `action_already_running`; timeout/sandbox-denial
      errors are truthful — route the fix to the action file via the delegated worker,
      then re-run this recipe from step 3.
3. **Verification Checklist** (this is the load-bearing fix — add as hard checklist
   items):
   - [ ] Every declared action invoked at least once in the current mode, with a success
         receipt read back.
   - [ ] The latest `business_refresh_product_surface` result shows no declared action
         with `never` invocation status (§14B evidence).
   - [ ] Every schedule action has a service-principal receipt from a real tick.
   - [ ] Deno capability confirmed, or the exact blocker is recorded instead of a publish
         claim.
   - [ ] No service identity appears in customers or directory.
4. **Common Pitfalls** rows: publishing with declared-but-never-invoked actions; treating
   `included_action_quota` as an action allowance (it is inert metadata — D20); using an
   owner token on the app plane; expecting npm/remote imports inside an action; using a
   `*.takyon.invalid` address as a test customer; claiming a schedule ran without a
   service-principal receipt.
5. **Rules**: never report an action working without invoke + receipt read-back; actions
   meter flat per invocation while their AI calls meter through generate — never
   double-represent cost; spend authority stays in rails, never in skill prose.
6. **Troubleshooting** rows: `rails_base_url is required for scheduled actions` → set
   `plugins.takyon.app_actions.rails_base_url` (origin only); deno missing → install +
   re-check capabilities; `declared action <name> has no file` → the worker never wrote
   it, re-delegate; 402 → inspect app budgets before touching anything else.

Cross-reference (the one permitted edit outside this skill): one line in
`takyon-product-workflow/SKILL.md` pointing to `takyon-app-runtime` for the exact action
field schema and the verification recipe. No other duplication — schema and operations
live with the rail owner only.

### 14B. Invocation evidence on the canonical report (small code change)

Goal: "declared but never proven" must be visible on the surfaces the CEO already reads —
evidence, NOT a blocker.

1. New helper in `app_actions.py`:
   `summarize_action_invocations(conn, business_slug, actions) -> list[dict]` returning,
   per declared action: `{name, trigger, last_status: "ok"|"failed"|"never",
   last_invoked_at, last_principal, invocations_30d}` derived from usage events with
   `purpose="action_invoke"` (PG and SQLite paths — query the same tables the metering
   writes). If invocation metadata does not already record the action name, mode, and
   principal kind (verify ~753), extend the metadata written at reserve/settle so the
   summary needs no joins beyond usage events.
2. Wire the summary into the existing product inventory / refresh assembly (where
   `pretend_findings` is attached, `_bounded_product_inventory` ~7824) and into the
   `business_refresh_product_surface` result payload, and render it in `surface.md`
   (Runtime Rails or Product Inventory section — match existing formatting).
3. Explicitly NOT a publish blocker: refresh/publish status logic is unchanged. The skill
   checklist (14A) is what makes `never` a do-not-publish signal.
4. Hermetic tests: seed usage events on the SQLite path → assert summary statuses
   (`never` / `ok` / `failed`), assert the refresh result and surface.md carry the block,
   assert publish status is NOT affected by `never` (pins the no-blocker decision).

### 14C. Sync + verification of this work order

1. Tests via `scripts/run_tests.sh` (hermetic suites; same rules as §10).
2. Skill sync: relaunch `./takyon`. `takyon-app-runtime` was never hand-edited so the
   repo edit should sync cleanly; if "user-modified, skipping" appears, run
   `takyon skills reset takyon-app-runtime --restore` and relaunch. Verify: on-disk copy
   under `$TAKYON_HOME/skills/takyon/takyon-app-runtime/` matches the repo, the skills
   index lists it, no skip warning. Same verification for the one-line product-workflow
   cross-reference edit.
3. E2E (extends §11): walk the full 14A recipe against a test business — capability
   check, test customer, magic link, invoke, receipt read-back, `/cron tick`, service
   receipt, then a refresh showing all declared actions with non-`never` status. Capture
   the receipt paths and the refresh excerpt in the report.
4. Report per ground rule 7: `done`/`attempted`/`blocked` per item above, with file
   paths, receipt paths, and commands. The sandbox-proof deno suite status carries over
   from §10 unchanged.

---

## 15. Work order: enforce the provider/namespace contract (the latexflow finding)

**Evidence (2026-06-12 lab run, latexflow):** with updated skills synced, the product
worker generated `product/site/src/app/api/takyon/apps/latexflow/generate/route.js` — a
self-hosted handler at the platform-reserved rail path that session-checks via rails, then
calls `https://api.openai.com/v1/chat/completions` directly with invented env keys
(`TAKYON_OPENAI_BASE_URL` / `TAKYON_OPENAI_API_KEY` / `OPENAI_API_KEY`; none exist in the
repo). The injected worker contract ALREADY forbids this (core.py ~5849: "Do not call
providers directly or invent output/spend state") — but no scanner or blocker checks
product source for it, and `_RUNTIME_BACKED_PATTERNS` counts the `/api/takyon/apps/`
string in that very file as evidence of honest rail usage. Build passed, nothing flagged.
Upstream causes: (1) contract rule with no gate; (2) reserved-namespace shadowing is
possible and invisible; (3) the raw-Next local preview has no platform rails, so honest
products look broken in it and faking is the path of least resistance.

This is spend/credential integrity — the class where CLAUDE.md sanctions deterministic
enforcement. Do NOT fix the latexflow artifact by hand; land the gates and let the
existing repair retry force the worker to fix it.

### 15A. Forbidden-pattern scan → hard refresh blockers

1. New pattern family beside `_PRETEND_PRODUCT_PATTERNS` (separate class, e.g.
   `_FORBIDDEN_PRODUCT_PATTERNS`, because these BLOCK rather than advise):
   - Direct provider hosts in product source: `api.openai.com`, `api.anthropic.com`,
     `generativelanguage.googleapis.com`, `api.mistral.ai`, `api.deepseek.com`,
     `openrouter.ai`, `api.groq.com`, `api.together.xyz` (one tuple, extendable).
   - Provider key env reads: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`,
     `GEMINI_API_KEY`, `MISTRAL_API_KEY`, `DEEPSEEK_API_KEY`, `GROQ_API_KEY`,
     `OPENROUTER_API_KEY`, plus the invented-prefix heuristic
     `TAKYON_[A-Z_]*API_KEY|TAKYON_[A-Z_]*BASE_URL` in product source.
   - Provider SDK imports: `from openai`, `new OpenAI(`, `@anthropic-ai/sdk`,
     `openai.OpenAI(`.
   Same walk/skip rules as the pretend scan (`_PRODUCT_SOURCE_EXTENSIONS`,
   `_PRODUCT_SOURCE_SKIP_DIRS`).
2. Surface findings as exact, truthful BLOCKERS in `business_refresh_product_surface`
   (same blocker assembly as build failures, feeding the existing one-retry repair loop):
   `product source calls an AI provider directly at <path>:<line>; runtime AI must go
   through the generate rail (or a declared action)`. One blocker line per finding, capped
   sensibly (first 5 + count).

### 15B. Reserved-namespace containment blocker

1. Detection is file-path based, not content based: any product-source route handler
   under the reserved namespace — `**/api/takyon/apps/**/route.(js|jsx|ts|tsx)`,
   `**/pages/api/takyon/apps/**`, and the `generated-apps` alias — is a blocker:
   `product source defines its own handler under the platform-reserved path
   /api/takyon/apps/... at <path>; platform rails are served by the Takyon runtime —
   remove the handler and call the rail from the client or a declared action`.
2. Fix the evidence inversion: a file that itself defines a reserved-namespace handler
   must NOT have its `/api/takyon/apps/` strings counted by `_RUNTIME_BACKED_PATTERNS`
   as runtime-backed evidence.
3. Verify production precedence while you are in there (report, do not assume): confirm
   the platform middleware intercepts rail paths before any product self-route could
   serve on product hosts — i.e. the shim was dead code in prod. Either way the blocker
   stands; this determines whether the failure mode was "unmetered spend" (route serves)
   or "works-in-preview, dead-in-prod" (route shadowed). Name which in your report.

### 15C. Worker-contract sharpening (two lines, generic block — not per-rail)

Add to the generic worker contract block (next to the ~5849 rule):
- "The path namespace `/api/takyon/apps/` (and `generated-apps`) is platform-reserved:
  never define your own server route handlers under it; the runtime serves those rails."
- "Never read or reference provider API keys or provider base URLs (OPENAI_*,
  ANTHROPIC_*, TAKYON_*_API_KEY, etc.) in product source; there are no such env vars on
  product hosts, and the refresh gate blocks them."
Cheap and honest — the gate is the enforcement; these lines tell the worker the gate
exists so the repair retry converges in one pass.

### 15D. Tests + acceptance rerun

1. Hermetic tests: fixture files distilled from the latexflow route (provider host call,
   env key read, SDK import, reserved-namespace handler) → each fires its exact blocker
   text; an honest fixture using `createSubuserRuntimeClient` / `invokeAction` /
   plain `fetch` to rails fires NOTHING (pins false-positive safety); the
   runtime-backed-evidence inversion (15B.2) is pinned.
2. Acceptance = re-run the latexflow lab flow: refresh must produce the 15A + 15B
   blockers on the current artifact; the repair retry should delete the route and wire
   the runtime client; the rerun then passes with rails-only source. THEN re-judge the
   actions question cleanly: with the fake option closed, the worker/CEO must either go
   rails-only client-side (honest for a one-call tool) or declare an action for the
   server-side orchestration — both are acceptable outcomes; report which one happened.
3. Preview note (G7, report-only this pass): the raw-Next preview (e.g. `npm run start`
   on 4017) has no platform rails, which is the pretend pressure source. Verify what the
   canonical platform-served preview path is and confirm the operator-facing
   verification flow uses it; if there is no canonical served-preview affordance, record
   that as a named follow-up — do not build one in this work order.

---

## 16. Work order: frontend action runner (one helper, closes the budget→upgrade loop)

`invokeAction` is landed (runtime-client.js ~330). What products lack is the canonical
way to USE it: in-flight state, retry-safe idempotency, and — the business-critical part —
typed error handling so a 402 becomes an upgrade CTA instead of a swallowed exception.
One addition to `subuser_app_kit/runtime-client.js`, framework-agnostic, no DOM, no
styling (product look stays with the worker/design brief per doctrine):

1. **`createActionRunner(name, options)`** returning `{ run(payload), state() }`:
   - In-flight guard: a second `run()` while pending rejects locally with
     `{kind: "already_running"}` (client complement to the server guard — no
     double-submits, no double-charges).
   - Idempotency: generate one key per logical submit; REUSE it when `run()` retries
     after a network failure (safe replay), regenerate on a new submit.
   - Typed error classification on the rejection:
     `{kind: "budget"|"rate_limited"|"already_running"|"timeout"|"action_error"|"unavailable",
     message, checkoutUrl?}` — mapped from HTTP status (402 → `budget`, 429 →
     `rate_limited`/`already_running` via the error string, 404/rail state →
     `unavailable`) and the truthful server error text as `message`, never rewritten.
   - The budget bridge: when `kind === "budget"` and the `checkout` rail is live,
     attach `checkoutUrl` via the existing `defaultCheckoutUrl("upgrade", location)`
     helper (~107). This is the monetization loop: action exhausts plan budget → user
     sees a truthful limit message with an upgrade path → checkout rail → revenue.
2. **Worker-contract line** (generic block): "Drive actions through
   `createActionRunner`: disable the trigger while pending, render the truthful error
   message by kind, and on `budget` errors show the upgrade path via the provided
   checkoutUrl — never retry-loop a 402 and never hide it."
3. **Schedule-results convention — prose, not code**: schedule-triggered actions persist
   their output via the records rail (the action calls records over rails as the service
   principal or writes per-user records as designed); the UI shows "what happened since
   you left" through existing `listRecords`. Add one line to the takyon-app-runtime skill
   (14A Quick Reference) and one worker-contract line; build NO polling helper, NO
   activity-feed component.
4. **Explicitly NOT building** (each would be fake or doctrine-violating): styled action
   buttons/components (product look is per-business), progress/streaming UI (v1 actions
   are one round trip — progress bars would be invented state), client-side schedule
   polling (records convention covers it), any state-management framework.
5. Tests: kit is plain JS — add hermetic node-side tests if the repo has a js test lane;
   otherwise pin behavior through a web_server route test asserting the 402/429 envelope
   shapes the runner depends on (status codes + error strings are the contract), and note
   the kit-level coverage gap honestly in the report.

---

## 17. Work order: pinned zero-shot frontend substrate (scaffold, not template)

**STATUS 2026-06-12 (implemented by Fable, verify with `verify-actions-stack.sh`):**
DONE — scaffold at `plugins/takyon/subuser_app_kit/scaffold/` (pinned: vite 5.4.21,
react 18.3.1, react-router-dom 6.30.4, typescript 5.6.3, tailwindcss 3.4.19; lockfile
shipped; `npm ci && npm run build && tsc --noEmit` green; `_takyon/` re-exports the real
kit; placeholder tokens with SCAFFOLD-PLACEHOLDER marker); `frontend_stack` contract
field end-to-end (choices constant, merge, shape default `legacy`, surface-context
`frontendStack`, both surface.md renders, tool schema); F6 static-only blockers
(`_scan_for_pinned_stack_server_entrypoints`, stack-gated in inventory, formatter
suffix); F7 placeholder-token advisory (`_scaffold_placeholder_tokens_marker`,
byte-compare vs shipped scaffold tokens, advisory only); all three skill edits
(lane-aware, synced to both TAKYON_HOMEs); hermetic tests for all of it.
ALSO DONE (2026-06-12, second pass — closes the Codex P1/P1/P2 findings): F4 machinery
auto-seeding — `_materialize_subuser_app_starter` branches on the contract lane and
seeds the scaffold (excluding `_takyon/`, `node_modules/`, `dist/`, `.git`) with
business name/description substituted via `__STARTER_SITE_NAME__`/`__STARTER_SITE_DESCRIPTION__`
tokens; kit materialization excludes `scaffold/` and build artifacts (no more 82MB
`_takyon/` payloads); creation default flipped — NEW contracts get
`frontend_stack: vite_react_ts` via `_frontend_stack_for_contract_upsert` while the
shape-read default stays `legacy` (no retro-gating; legacy Next starter retained ONLY
for legacy-lane re-seeds — capability preservation, not a new-product path); scaffold
gained support routes (/faq /privacy /terms /articles), robots.txt, and the seed
tokens, rebuilt green. All pinned by tests in test_takyon_product_enforcement.py.
REMAINING (Codex): §17.2.2 full platform-served E2E walk of the scaffold against a test
business (needs the container runtime); landing prerender/sitemap SEO parity with the
Next starter's dynamic robots/sitemap/og-image files (static lane ships robots.txt only
— accepted degradation, revisit with prerender); legacy migration. F1 note: Tailwind 3.4 was pinned on
the conservative training-mass rationale without the worker-model smoke test (docker was
unavailable); revisit only if scaffold-lane products show Tailwind-syntax failures.

**Goal:** raise zero-shot MVP-complete reliability by pinning the product frontend to
Vite + React + TypeScript + Tailwind + vendored shadcn-style components, seeded from a
canonical scaffold with all rails pre-wired. **The scaffold ships mechanics, never
design** — the operator's hard requirement is that products must NOT look templated, so
everything visible stays generated per product from the design brief; everything
invisible (wiring, a11y, interaction mechanics) is reused and pre-verified.

Sequencing: after §15 (enforcement) and §16 (action runner — the scaffold uses it).

### 17.1 Decisions (do not relitigate)

| # | Decision | Value |
|---|----------|-------|
| F1 | Stack | Vite + React + TypeScript + Tailwind + react-router-dom, EXACT versions pinned in the scaffold `package.json` + lockfile. Selection criterion for majors (esp. Tailwind 3 vs 4): whichever the current worker model demonstrably generates correctly — run one smoke generation against each candidate before pinning, record the result. |
| F2 | Output | Static SPA only: `vite build` → `dist/`, published as static assets. No SSR, no server entrypoints of any kind in product source. Landing-page prerender/SEO is a named follow-up, not v1. |
| F3 | Scaffold home | `plugins/takyon/subuser_app_kit/scaffold/` — versioned with the kit. |
| F4 | Seeding | Deterministic creation mechanics (sanctioned class): extend the same machinery that materializes `_takyon/` so that when the contract's `frontend_stack` is the pinned default AND `product/site/` is empty/absent, the scaffold is copied in ONCE. Never overwrite existing source; seeding is idempotent and receipted. |
| F5 | Contract field | `frontend_stack` on the surface contract. Default `"vite-react-ts"` for NEW contracts; existing contracts get `"legacy"` (grandfathered — no new gates fire on them). Validator accepts only known values. |
| F6 | Static-only gate | Extends §15B, gated on `frontend_stack == "vite-react-ts"`: any server entrypoint in product source is a truthful refresh blocker — `pages/api/**`, `app/**/route.*`, `next.config.*`, imports of express/fastify/hono/koa. Legacy products: untouched. |
| F7 | Anti-template gate | The scaffold ships deliberately-unusable placeholder tokens (obvious placeholder palette + marker comments). Refresh computes a hash of `tokens.css` (and the Tailwind theme block) against the scaffold's shipped hash: unchanged ⇒ ADVISORY (not blocker) "product ships scaffold placeholder tokens; theme from the design brief before publish" + a skill checklist item. Same evidence-not-blocker mechanism as §14B. |
| F8 | Components | Curated vendored set (~15: button, input, textarea, select, dialog, dropdown-menu, card, table, tabs, badge, label/form, toast, skeleton, avatar, sheet) copied from shadcn/ui (MIT, attribution preserved), themed exclusively through `tokens.css` CSS variables mapped into the Tailwind config. Editable per product by design. |
| F9 | Hooks | `useSession`, `useRecords`, `useActionRunner`, `useCheckout` live IN the scaffold as editable source, wrapping the canonical kit client. The kit itself stays framework-agnostic; one client, no fork. |
| F10 | Placeholder pages | Scaffold screens (landing, /app gate, profile, records example, action example) are MECHANICS demos: minimal structure, marker comments (`{/* SCAFFOLD-PLACEHOLDER: replace all presentation */}`), placeholder tokens. The worker contract requires replacing all placeholder presentation; the F7 gate catches the laziest version (unchanged tokens). |

### 17.2 Build the scaffold

1. `scaffold/` app: pinned package.json + lockfile; Vite config; Tailwind config mapping
   `tokens.css` variables; router with `/`, `/app` (session-gated via magic-link
   request/verify flow), `/app/profile`, checkout success/cancel handling; one records
   list example and one action invocation example using the §16 runner; the F8 component
   set under `src/components/ui/`; hooks under `src/lib/`; `_takyon/` consumed exactly as
   today (the kit is materialized by refresh, not vendored into the scaffold).
2. Pre-verify it ONCE, fully: `npm ci && npm run build` green; serve `dist/` through the
   platform (G8) against a test business; walk auth → session → records → action →
   checkout-redirect happy paths; fix until clean. This pre-verification is the entire
   point — record it as a receipt in the work-order report.
3. Known gaps to resolve while in there (verify in source, report which):
   - **G8**: how built product output is published/served today (publish_source_path →
     static serving vs node server) — wire `dist/` publishing into the existing build
     shapes (`_ensure_repo_node_dependencies` family), no new publish rail.
   - **G9**: where initial `product/site/` seeding happens today (worker-created vs
     machinery), to place F4 in the canonical spot.

### 17.3 Contract, gates, machinery

1. `frontend_stack` field: shape normalization + validator (F5), persisted with the
   contract, surfaced in surface.md `## Contract`.
2. F4 seeding in the kit-materialization path, receipted (`metrics/receipts/` family),
   never overwriting.
3. F6 static-only blockers + F7 token-hash advisory in the refresh assembly (blocker and
   advisory text verbatim from the decisions table; §15's cap-and-count pattern).
4. Worker contract lines (generic block, gated on the pinned stack): the stack pin; no
   server code anywhere in product source; theme exclusively via `tokens.css` +
   Tailwind theme from the design brief; replace ALL scaffold placeholder presentation —
   placeholder tokens at publish are a visible finding.

### 17.4 Skills (after code is green; sync dance per §9.6/§14C)

1. `takyon-build-product`: bootstrap seeds/expects the scaffold (name the path); never
   invent a stack; static-only rule; mandatory theming step from the design brief
   (tokens first, then screens); verification = build green + no placeholder-token
   advisory. Don't-preseed-actions rule unchanged.
2. `takyon-product-workflow`: update worker-edit path references from the Next-era
   `src/app/app/(product)/**` to the scaffold's screen structure; design-pack/
   `claude-design` flow unchanged; actions doctrine unchanged.
3. `takyon-app-runtime`: Quick Reference note only (scaffold hooks wrap the kit client;
   the client remains canonical).

### 17.5 Tests + acceptance

1. Hermetic: validator accepts/rejects `frontend_stack` values; seed-once logic (empty
   dir seeds, non-empty never overwrites); F6 blockers fire on fixture server entrypoints
   ONLY when stack is pinned (legacy fixture untouched — pins the grandfather rule); F7
   advisory fires on unchanged tokens, clears on themed tokens, and does NOT affect
   publish status.
2. E2E acceptance (the operator's bar, run it exactly): `/create` a fresh test business →
   bootstrap → scaffold seeded → product-workflow pass with a real design brief → build
   green → refresh shows NO placeholder-token advisory, NO server code, rails calls live
   → the product visibly carries the brief's identity, not the scaffold's. Then rerun the
   latexflow-class probe on a second fresh business and report which honest shape the
   worker chose (rails-only client vs declared action).
3. Report per ground rule 7, including the F1 version-pin smoke-test result and the G8/G9
   findings.

### 17.6 Out of scope (named follow-ups)

Prerender/SEO for landing pages; visual/vision QA loop; migrating legacy products onto
the scaffold; any second stack option; component-set expansion beyond the curated F8 set.

---

## 18. Work order: records-v2 — bounded queries (pure build; research-verified)

Goal: feeds/browse/match screens. Research verdict: every buy (PostgREST/Hasura/Directus/
Supabase) is a second API or identity plane; the gap is a bounded DSL in code we own.

1. **DSL** (one compiler, both dialects): request shape
   `{filters: [{field, op, value}] (≤5), sort: [{field, dir}] (≤2), cursor, limit (≤100, default 25)}`.
   Ops whitelist: `eq neq gt gte lt lte in ilike exists`. Field resolution: real columns
   (`record_type`, `title`, `created_at`, `updated_at`) or `data.<key>` where key matches
   `^[a-z0-9_]{1,64}$` — compiled to `data->>'key'` (PG, with `::numeric`/`::timestamptz`
   casts for typed ops) / `json_extract(data_json, '$.key')` (SQLite). Values ALWAYS bound
   parameters; field names NEVER interpolated raw (whitelist + fixed expression assembly).
   Keyset pagination over `(updated_at, id)`; opaque cursor; offset fallback capped.
2. **Surfaces**: extend `business_list_app_records` tool args (NO new tool); extend the PG
   leaf list path in `app_records.py` and the SQLite list path in core.py; new app-plane
   route `POST records/query` (read-only — session-gated, NO idempotency required,
   directory-limiter-pattern rate limit); kit `listRecords(options)` uses it when filters
   present. Add a records worker-contract line stating the query contract.
3. **Tests** (hermetic): compiler unit tests both dialects; injection pins (hostile field
   names/ops rejected, values never interpolated); pagination ordering + cursor round-trip;
   route gating; cap enforcement. Named follow-up (record, don't build): declared indexed
   fields on the contract → expression indexes when volume demands.

## 19. Work order: media rail (hybrid; research-verified — engine deferred, R2 later)

Goal: photo uploads/avatars. Engine verdict: MinIO is dead (archived 2026-04); VPS disk
now behind the `StorageBackend` seam that `plugins/takyon/storage.py` ALREADY has
(LocalStorageBackend; boto3/S3 driver proven by the Supabase backend) — R2 when disk
pressure demands, never a self-hosted daemon at current scale.

1. **Leaf** `app_media.py`: upload/get/delete over StorageBackend; keys
   `media/<business>/<media_id>` with `_safe_relpath` containment; `app_media` metadata
   table (id, business_slug, app_user_id, size_bytes, mime, created_at) — PG migration
   (next number, shape-guard header) + SQLite CREATE in `_init_db`.
2. **Registry** `media` entry (owner takyon-app-runtime; deps auth+account; tool
   `business_list_app_media` — list + quota stats, no other new tools); worker-contract
   lines: upload via the kit only, never base64-into-records, session-gated serving.
3. **Routes**: `POST media` (multipart via starlette form parsing; size cap default 5MB,
   MIME allowlist image/jpeg,png,webp,gif; per-user + per-business byte quotas — business
   default 1GB, user default 50MB, env-overridable); `GET media/<id>` (session-gated
   stream with content-type); `DELETE media/<id>` (uploader-only). Flat per-upload price
   (default 200 µUSD) metered purpose="media_store"; quota breach = truthful 4xx, never
   silent truncation. Rail-routes + normalize entries.
4. **Kit**: `uploadMedia(file)`, `mediaUrl(id)`, `deleteMedia(id)` behind
   `ensureRail("media")` (FormData; do NOT set Content-Type manually). Scaffold hook
   `useMediaUpload` rides the next scaffold pass.
5. **Tests**: size/MIME rejection, quota enforcement both scopes, id-traversal containment,
   session gating, uploader-only delete, SQLite table presence, kit method route shape.
   Named follow-ups: R2 driver cutover, public-read flag for landing images, image resize.

## 20. Codex checklist: email rail wiring + §18/§19 + skills/tools parsimony audit

**A. Email rail (implemented 2026-06-12 — verify, don't rebuild):**
1. `bash verify-actions-stack.sh` — all green including the email checks.
2. Leaf engine: `plugins/takyon/app_email.py` + `tests/plugins/test_takyon_app_email.py`
   exist and pass via scripts/run_tests.sh (built by a background agent — if absent, that
   agent failed; check its spec in the session transcript and implement to the same
   signature `send_app_email(store, *, business_slug, recipient_app_user_id, subject,
   text_body, html_body, purpose, idempotency_key, test_mode, principal)`).
3. Wiring greps: registry `"email"` entry with 3 worker-contract lines; deps
   `("auth","account")`; `"email"` in `_RUNTIME_FEATURE_ORDER`;
   `business_send_app_email` handler + schema registration;
   `email/send` in web_server rail routes; `_takyon_email_status_payload` maps
   service-only→403, budget→402, daily-cap→429, missing-rail→generic 404.
4. Behavior: tool send in test mode on a lab business → suppressed receipt under
   `metrics/receipts/app-email/` + `email_send` usage event; customer-session POST to
   email/send → 403; bootstrap defaults exclude `email`.
5. E2E (with container runtime): schedule action sends a real test-mode email via its
   service session; receipt shows principal service.

**B. Implement §18 then §19** (each: code → both backends → routes → kit → worker-contract
lines → hermetic tests → skill prose LAST, per the G6 sequencing law).

**C. Skills/tools parsimony audit (prose check — do this explicitly and report):**
For each of the three rails (email, records-v2, media), verify ALL of:
1. **No new skill was invented.** The owning skill is `takyon-app-runtime` for all three;
   `takyon-product-workflow` carries only routing pointers (when to declare the rail), and
   `takyon-build-product` carries nothing new (bootstrap must NOT preseed email/media —
   mirror the actions rule). If you find a new skill directory for any of these, that is a
   parsimony violation: fold it into app-runtime and delete it.
2. **Tools exist in code and are named in skill prose.** Frontmatter `requires_tools`
   lists exactly the canonical tools (`business_send_app_email`, extended
   `business_list_app_records`, `business_list_app_media`); Quick Reference names each
   tool with its key args; the Procedure/verification text tells the CEO the receipt path
   and usage-event purpose to read back. A tool named in prose but missing from code, or
   registered in code but absent from the owning skill's prose, fails this check.
3. **Skills teach use, not just existence**: each rail's skill text must answer — when to
   declare it, which tool to call with which arguments, what test mode does, what the
   truthful failure modes are (402/403/429/404), and what artifact proves success. Quote
   the exact lines in your report.
4. **Sync dance** for every edited skill: relaunch, `takyon skills reset <name> --restore`
   where flagged, verify both TAKYON_HOMEs match the repo AND the skills index lists them.
5. **Worker-contract truthfulness**: every registry `worker_contract` line must describe
   behavior that exists in code TODAY (the G6 law). If §18/§19 are not yet implemented,
   their registry entries and skill prose must not land before the runtime does.
Report per ground rule 7: done/attempted/blocked per item with paths and receipts.

## 21. Bootstrap vs workflow ownership: cut the semantic bloat

Problem: `takyon-build-product` has accumulated product-taxonomy and workflow-authoring
responsibility it should not own. That creates fake early declarations (`app_mode`,
guessed verbs, workflow doctrine, conversion semantics) and encourages scaffold theater.

Decision:
1. `takyon-build-product` is bootstrap-only. It owns:
   - creating `product/site`
   - seeding the pinned Vite scaffold
   - wiring the real shared app-runtime basics needed for the first shell to be honest:
     auth/session, account, checkout/subscription, and canonical billing/plan setup
   - recording source-path / scaffold-lane truth needed for the platform to prepare the
     kit correctly
2. `takyon-build-product` does **not** own product semantics. It should not decide or
   record:
   - `app_mode`
   - `api_mode`
   - `subscription_style`
   - `runtime_features`
   - customer-experience taxonomy such as generic-vs-AI shape
   - product workflow doctrine
   - guessed backend verbs
   - fake route/tab/section/product-loop structure
3. If the real customer product is not built yet, the truthful state is
   `workflow_pending` / blocked, not a generic placeholder product and not a taxonomy-rich
   fake shell.
4. `takyon-product-workflow` is the first place allowed to define what the product
   actually is.

Implementation direction:
- Remove bootstrap rules that coerce a fresh business into a semantic product shape.
- Keep bootstrap focused on real shared rails and real scaffold preparation.
- Delete bootstrap logic whose only purpose is to preserve a speculative future handoff.
- Delete bootstrap-time declaration of runtime rails; the shell may be physically wired for
  auth/account/checkout/billing, but the contract should not pretend the product semantics
  are final before workflow owns them.
- Never show a generic page merely because the real product is not built yet; keep the
  product blocked/pending instead.

## 22. Action declaration timing: declare verbs when they are real

Problem: requiring `product_workflow.actions` too early makes the contract lie. The
worker often does not know the real backend verbs until it has reasoned through the actual
product flow. Forcing declaration up front encourages fake guessed verbs, contract drift,
or degrading the business into a generic SaaS shell just to satisfy validation.

Decision:
1. `takyon-build-product` should not be forced to declare product actions at all.
2. `takyon-product-workflow` or the delegated worker may declare actions whenever the real
   product verbs become clear during the workflow build.
3. Action declaration is required by the time the workflow claims the backend behavior is
   real, not earlier.
4. Do not require guessed `product_workflow.actions` merely to let bootstrap proceed.
   The correct intermediate state is truthful `workflow_pending`, not fake action specs.
5. By refresh/publish time, the action identity must be fully consistent:
   declared spec ⇔ UI caller name ⇔ `product/site/actions/<name>.ts`.

Implementation direction:
- Validation should distinguish:
  - allowed bootstrap state with no product verbs yet
  - claimed real workflow state with concrete verbs
- Missing actions should block only when the workflow claims real backend behavior, not
  during pure scaffold/bootstrap.

## 23. Keep only runtime_features as the shared-rail contract

Problem: most of the current surface shape fields are speculative taxonomy, not real
enabling truth. They make the contract noisier while adding very little beyond what the
actual workflow, routes, actions, and runtime receipts already say.

Decision:
1. Delete these contract/tooling fields unless a later change proves they are truly
   load-bearing:
   - `app_mode`
   - `subscription_style`
   - `api_mode`
   - `frontend_stack` as a normal editable product field
2. Keep `runtime_features` because it is genuinely load-bearing today:
   - runtime tool handlers gate on it for rails such as media, email, and actions
   - action loading/validation gates on it
   - worker/runtime guidance currently uses it as the explicit shared-rail selector
3. `runtime_features` should be declared and edited during `takyon-product-workflow`, not
   during `takyon-build-product`.
4. Bootstrap may still seed/wire the honest shared shell, but it should not author the
   product's declared rail contract before the real workflow step.

Implementation direction:
- Treat `runtime_features` as the one remaining explicit shared-rail contract until a
  better authority surface replaces it.
- Move responsibility for declaring/updating `runtime_features` to
  `takyon-product-workflow`.
- Remove bootstrap rules and tool-schema language that require or encourage
  `runtime_features` authoring before the workflow step.

## 24. Adjacent structural problems: not just actions timing

Three adjacent problems are now in scope because they create the same failure pattern:
the system can claim "MVP-complete" while still steering the worker toward a weak or fake
artifact.

### 24.1 Fake MVP-complete semantics

Problem: `product_workflow` currently overstates substance. Many of the fields that claim
"closed loop", persistence, first-run strategy, or complexity bounds can collapse to empty
strings / false booleans while the product still presents itself as a real completed MVP.

Direction:
- `MVP-complete` must mean **scoped but real**:
  - one primary user
  - one primary job
  - one genuinely useful loop
  - no placeholder/theater
- A workflow that leaves the core loop empty, the persistence model empty, or the action
  story incoherent must not read as complete just because the schema shape exists.

### 24.2 Prompt imbalance: too much blue, too little positive obligation

Problem: the worker contract currently over-emphasizes prohibitions and under-emphasizes
the one thing that matters most: the product's primary job must actually work for real.
That drives the worker toward safe but useless artifacts.

Direction:
- keep security/integrity in tools and validators, not in prompt nagging
- give the worker a short positive contract:
  - make the primary job work for real
  - stay in the assigned source lane
  - use the declared backend path
  - do not edit out-of-scope routes unless asked
- do not add more prompt security; delete most of it instead

### 24.3 Verification bloat

Problem: verification is currently carrying too much of the quality burden. That is
distracting, prohibitive, and creates a culture where the system tries to "prove" weak
artifacts instead of generating better ones zero-shot.

Direction:
- verification should become a thin integrity backstop, not the main quality system
- keep only hard blockers such as:
  - placeholder/scaffold tokens
  - action/spec/file mismatch
  - forbidden direct provider path
  - missing required route/source
  - invalid action/contract shape
- the target is strong zero-shot generation, with publish checks catching structural lies
  rather than acting as the primary creator of quality

### 24.4 Schema/taxonomy bloat

Problem: fields such as `app_mode`, `subscription_style`, and `api_mode` are currently
doing more speculative classification than real enabling work, especially on the bootstrap
path. They make the contract feel complete while adding little truthful value.

Direction:
- `subscription_style` should be deleted while only one supported style exists.
- `api_mode` should exist only if it drives real runtime/scaffold composition that cannot
  be inferred from the workflow and routes.
- `app_mode` must earn its keep. If it does not control a real shared-runtime or scaffold
  composition difference that cannot be derived from richer product/workflow truth, delete
  it everywhere.
- `frontend_stack` should disappear as a normal editable product field while only one
  supported lane exists; keep it only as hidden internal platform state if the seeder
  still needs a branch key.
- Prefer direct truthful facts over taxonomy labels.

### 24.5 Claude hookup checklist for §§21–24

Claude should not claim §§21–24 are hooked up unless all of the following are true:
1. `takyon-build-product` no longer writes or teaches `app_mode`, `subscription_style`,
   `api_mode`, or bootstrap-time `runtime_features`.
2. `takyon-build-product` still bootstraps the honest shared shell:
   scaffold, auth/session, account, checkout/subscription, and canonical billing/plan
   setup.
3. `takyon-product-workflow` is now the first owner of:
   - `runtime_features`
   - `product_workflow.actions`
   - real product semantics / core loop definition
4. `business_upsert_app_surface_contract` schema/tooling no longer exposes the deleted
   taxonomy fields as normal required/encouraged app-shape knobs.
5. Any internal `frontend_stack` branching is hidden platform plumbing, not customer/worker
   taxonomy.
6. Validation distinguishes:
   - bootstrap shell with no declared product rails yet
   - workflow state with declared rails/actions
7. Refresh/publish still blocks structural lies:
   placeholder scaffold, action mismatch, forbidden provider path, missing required routes.
8. Worker/prompt guidance has been simplified to the positive contract from §25.1–§25.3,
   with security pushed into tools/rails instead of blue prompt sprawl.
9. A fresh run proves the new ownership split:
   bootstrap does not author product semantics, workflow does.

## 25. Prompt minimization + prod holes

### 25.1 Prompt rewrite goal

Security should live in tools/rails/validators/isolation, not in the worker prompt.
After the actions/Vite architecture is truly live, the prompt should contain almost no
security prose beyond minimal lane/backend ownership rules.

Claude should treat this as an edit recipe, not as philosophy:
1. Shorten the worker contract.
2. Delete repeated negative/security warning prose that code now enforces better.
3. Replace it with one short positive obligation block.
4. If a protection is still needed, move it into code/tools/validators first and only then
   mention it briefly in prompt if necessary.

### 25.2 Exact prompt block to keep/replace with

Edit the shared worker-facing prompt surfaces so they converge on a block no longer than
this in spirit:

- Your overriding obligation is that the product's primary job works for real.
- Build only inside the assigned source lane and workspace.
- Use the declared shared rails and named actions for backend behavior.
- Do not edit out-of-scope routes or surfaces unless the instruction asks for it.
- If a required capability is not actually available through the declared rails, fail
  truthfully with the exact blocker instead of simulating it.

The key point is that this is the **whole** worker contract shape, not one paragraph added
on top of the current blue wall.

### 25.3 Exact prompt-edit checklist

Claude should make these prompt/prose edits explicitly:
1. In the shared worker contract generator, remove repeated warnings whose real authority
   should live in code:
   - "do not fake"
   - "do not invent"
   - "do not call providers directly"
   - "do not use localStorage"
   - repeated auth/account/billing/usage fear wording
2. In `takyon-build-product`, remove prompt/prose that tries to define product meaning,
   product taxonomy, or product backend semantics during bootstrap.
3. In `takyon-product-workflow`, keep only the product-quality and workflow-owning
   instructions:
   - define the real core loop
   - declare the real actions/rails when known
   - make the primary job genuinely work
4. In `takyon-app-runtime`, keep only the runtime ownership and exact rail/action recipe,
   not broad behavioral nagging.
5. In `plugins/takyon/prompts/ceo.md`, keep routing/policy only; do not restate a second
   giant security wall there.

The touched prompt surfaces should be called out explicitly in Claude's report:
- `plugins/takyon/prompts/ceo.md`
- shared worker contract text in `plugins/takyon/core.py`
- `skills/takyon/takyon-build-product/SKILL.md`
- `skills/takyon/takyon-product-workflow/SKILL.md`
- `skills/takyon/takyon-app-runtime/SKILL.md`

### 25.4 Exact prompt content to remove

- repeated "do not fake / do not invent / do not call providers / do not use localStorage"
  style warnings
- repeated auth/account/billing/usage fear wording
- any security constraint that is supposed to be enforced by code anyway
- repeated fallback/scaffold/self-justifying warning prose that teaches the worker to ship
  the safest non-product artifact

### 25.5 Prod go/no-go checks before landing the prompt cuts

Do not ship the simplified prompt on prod until these checks are explicitly done:
1. **Deploy truth** — confirm prod is actually running the same Vite/actions codepath as
   the repo being edited.
2. **Legacy path removal** — confirm there is no surviving direct-client generate path or
   old lane escape the worker can still fall back into.
3. **Outbound host check** — confirm `outbound_hosts` is narrow enough that actions only
   reach intended external hosts.
4. **Internal rail auth check** — confirm actions calling internal rails through scoped
   session/service context cannot escape business/user scope.
5. **Runtime isolation check** — confirm prod is on the real Linux/systemd/Deno action
   path, not a weaker fallback.

If any check fails, the answer is not "put the warning back into the prompt"; the answer
is to fix the code/tool/runtime rail first.

Clean rule:
- no security in prompt
- keep security in tools/rails
- do not add more prompt security
- do not trust the simplification in prod until the above go/no-go checks pass on the live
  deployed stack
