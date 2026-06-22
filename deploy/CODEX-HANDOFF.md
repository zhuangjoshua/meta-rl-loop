# Codex handoff — finish the safebox-broker cutover + deploy

You are finishing a security re-architecture. A red-team broke BOTH red lines on the deployed code
(full detail + the original plan: `deploy/SAFEBOX-BROKER-REMEDIATION-PLAN.md`, STEP A–G):
- **EXFIL:** the safebox VENDS raw provider keys over HTTP (`/v1/env/*`); clients pull keys and call
  providers in-process; one shared `TAKYON_SAFEBOX_TOKEN` co-located on every plane unlocks all of it;
  `secrets/.env` (64 secrets) is in git history.
- **NO-FORGE/NO-UNGATED-SPEND:** `takyon_app` has direct DML on `app_usage_events`; the gateway conn is
  `bypass=True`; credit routes trust a body `business_slug`. Cross-tenant isolation (user↔user via
  `businesses.owner_user_id`, sub-user↔sub-user via session+entitlement) is not enforced at the gate.

The fix makes the safebox an **auth-injecting broker**: it holds all keys + signs short-TTL, single-use,
scope+cost-bound capability tokens; it reserves budget + calls the provider itself; clients never hold a
key. Token scope `{takyon_user_id, business_slug, app_user_id, action, max_cost, nonce, ttl, audience}`,
every field validated not trusted.

## Already on `main` (review, don't rebuild)
- `6769941e` — broker core (unit-tested): `plugins/takyon/safebox_capability.py` (signed scope → no
  cross-tenant swap), `safebox_authz.py` (two-tier validation → authoritative scope), `safebox_nonce.py`
  (single-use), `safebox_broker.py` (verify→reserve-before-call→key-used-only-inside→key-free→release).
- `684e3e3e` — STEP A code (`0037_safebox_ledger_boundary.sql` SECURITY DEFINER `safebox_reserve/settle/
  release_usage` + REVOKE direct DML from `takyon_app` + nonce table + reconciliation; `app_usage.py`
  refactored to call them, behavior preserved), STEP B routes (`safebox_app.py`: `/v1/providers/*` +
  `/v1/token/mint`, additive — `/v1/env/*` left intact), STEP D worker lockdown (flag-gated
  `TAKYON_CLAUDE_AGENT_BROKER`; shared-master-token fallback already deleted in `docker_broker{,_app}.py`).
- `0037` is already rsynced onto the operator at `/opt/takyon/hermes-agent-main/plugins/takyon/db/migrations/`.
- ~180 tests green; `tests/plugins/test_safebox_*` are the cross-tenant + broker proofs.

## You must still WRITE
- **STEP C — client cutover** (deferred on purpose: it can't exist before the broker route is live, or it
  404s every paid call). Point `ai_gateway.py`/`creative_gateway.py`/`stripe_util.py` at the broker route;
  DELETE the in-client raw-key resolution + direct provider calls; drop the `bypass=True` gateway conn
  (`runtime_app.py:~245`) so it runs as `takyon_app`. Preserve public return shapes (PG gateway suite green).
- **STEP F harness** — a runnable EXFIL sweep + the cross-tenant negative tests.

## You must PROVISION (safebox-only secrets / infra)
- `TAKYON_CAP_SIGNING_KEY` (32+ random bytes) on the **safebox host only**.
- `TAKYON_DOCKER_BROKER_TOKEN` (dedicated, replaces the shared-token reuse) on the worker + docker-broker.
- A least-privilege DB conn for the safebox (for the ledger adapter to call the STEP-A functions).
- A confined docker network whose only egress is the safebox (`TAKYON_CLAUDE_AGENT_BROKER_NETWORK`).

## DO THIS — gate by gate, STOP on any red gate, push every applied change to `main`
**A. Ledger boundary.**
```
cd /opt/takyon/hermes-agent-main && .venv/bin/takyon claw migrate --yes   # applies 0037 (auto-backup)
# rsync the new app_usage.py to operator + subuser; restart takyon-worker takyon-dashboard + subuser runtime
```
GATE: as `takyon_app`, `insert into app_usage_events …` → permission denied; `safebox_reserve_usage` works;
metered `/generate`+`/search` bill identically (`test_takyon_app_usage_pg.py`, `test_takyon_ai_gateway_pg.py`).

**B. Broker routes live.** Provision `TAKYON_CAP_SIGNING_KEY` + the safebox DB conn; deploy `safebox_app.py`
to the safebox host; restart it. GATE: `POST /v1/providers/anthropic/messages` (with a minted token) returns
a **key-free** result; `tcpdump`/logs show the provider request leaves the **safebox** host; `ps`/`/proc/<pid>/environ`
on subuser/operator show **no provider key**.

**C. Client cutover** (write per above, deploy). GATE: grep → zero provider-key fetches in client code; PG
gateway suite green; product `/generate`+`/search` work through the broker.

**D. Worker.** Create the confined network; provision `TAKYON_DOCKER_BROKER_TOKEN`; set
`TAKYON_CLAUDE_AGENT_BROKER=1`. GATE: `ps` on worker host → no key; container has no default-bridge egress;
a real `business_claude_agent_task` still runs. Then delete the flag's OFF branch.

**E. Delete unsafe.** Remove `GET /v1/env/{key}`, `/v1/env/first`, `/v1/env/snapshot`, `/v1/env` from
`safebox_app.py`; replace shared-bearer auth on remaining admin routes with capability/role checks. GATE:
safebox boots; `curl …/v1/env/snapshot` → 404; all product/worker AI still works (only via broker).

**F. Verify.** Run `tests/plugins/test_safebox_*`; re-run the design red-team; **EXFIL sweep = 0** across every
host's `ps`/env/logs/receipts/git/transcripts; the three cross-tenant rejections (cross-user / cross-business /
cross-sub-user) MUST fail.

**G. Cleanup.** `git filter-repo` `secrets/.env` (+ `polsia3/.env.local`) from history (coordinate the
force-push with Joshua); delete the secret `.env` from every host. Then HAND BACK to the operator for key
rotation (do NOT rotate before F is green).

## Ping the operator when gates A, then C, then F are green.
After **C** (broker live + clients cut over), the operator's agent runs the **dashboard E2E**: a fresh
business + a real metered AI call routing through the broker (key-free) + the EXFIL sweep. That + rotation = done.
