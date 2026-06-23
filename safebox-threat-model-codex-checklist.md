# Safebox threat-model audit — what Codex must check & fix

**Date:** 2026-06-23 · **Method:** 39-agent source-grounded red-team (44 attacks, 25 adversarially
verified, 17 confirmed exploitable) + live probes on the deployed hosts.

## Threat model

A maximally-capable adversary has **root on every plane except the safebox host**
(`67.205.158.170` / private `10.116.0.2:8000`). The attacker holds the shared `TAKYON_SAFEBOX_TOKEN`,
the runtime `DATABASE_URL`, and all R2/Stripe/Auth0/Umami infra creds (all of which sit on the runtime
planes), and can run arbitrary code on the operator/worker/sub-user planes. The **only** trusted
component is the safebox host (it alone holds the raw provider keys and the HMAC signing key
`TAKYON_CAP_SIGNING_KEY`). Evil **sub-users** (app customers) separately attack the product runtime.

Two invariants under test:
- **I1 — secret confidentiality:** attacker never obtains a raw provider key **or the HMAC signing key**.
- **I2 — money integrity:** attacker never causes paid spend beyond what the safebox gate authorizes —
  cannot forge an uncapped capability, cannot make the ledger lie, cannot charge a different account.

## Headline verdict (deployed code, 2026-06-23)

| | Status | Why |
|---|---|---|
| Provider keys **at rest / over HTTP** | ✅ **HOLDS (live-verified)** | 0 provider-key lines in `/opt/takyon/secrets/.env` + `/opt/takyon/.takyon/.env` on operator & subuser; keys only on the safebox (6 lines). `/v1/env/{ANTHROPIC,OPENAI,GEMINI,GOOGLE,TAVILY,FAL}_*` → 404. The proxy injects keys outbound-only and sanitizes errors. This part of the cutover genuinely works. |
| **I1 overall** | ⛔ **BROKEN** | **G1**: the safebox vends its **own** `TAKYON_CAP_SIGNING_KEY` and `TAKYON_SAFEBOX_TOKEN` over `/v1/env` (live: HTTP 200). |
| **I2 overall** | ⛔ **BROKEN** | **G1** (forge any cap), **G3** (money ledgers + ownership are owner-writable in the DB), **G2** (uncapped transitional proxy path). |

The capability **crypto** (HMAC + `compare_digest`, server-derived scope, reserve-before-nonce,
single-use nonce, audience separation) is **sound** — but I2 does not rest on it; it is bypassed, not beaten.

---

## The three gaps (fix in this order)

### G1 — [CRITICAL, LIVE-CONFIRMED] safebox vends its own authority secrets over `/v1/env`
- **Live proof:** `GET http://10.116.0.2:8000/v1/env/TAKYON_CAP_SIGNING_KEY` → **HTTP 200** (value, 76 bytes);
  `GET …/TAKYON_SAFEBOX_TOKEN` → **HTTP 200**. Any holder of the shared token (every runtime plane) reads them.
- **Root cause:** the `/v1/env` egress filter is a **denylist of paid-provider aliases**
  (`core.provider_key_denylist()`, `core.py:1210-1227`, built from `_API_ENV_ALIASES` minus infra). The
  signing key and master token are sensitive but are **not provider aliases**, so they fall through.
  `read_env_value` / `/v1/env/first` / `/v1/env/{key}` (`safebox_app.py:1150-1172`) hand them over.
- **Impact:** read the signing key → forge **any** capability offline (arbitrary `max_cost_microusd`,
  arbitrary `takyon_user_id`, arbitrary audience) → total I1 + I2 collapse. **No DB write needed.**
- **Also leaks** `META_APP_SECRET` / `X_CLIENT_SECRET` (same reason; lower severity).
- **Write path:** code review says `POST /v1/env/{key}` (`safebox_app.py:1168-1172`) has no protected-key
  guard and `save_env_value` mutates `os.environ` live (`config.py:4926`), which `_cap_signing_key` re-reads
  per call (`safebox_app.py:119`) → overwrite = persistent forge-your-own-key. (A live POST probe returned
  500 — confirm the exact write contract; the **read** leak alone is already game-over.)
- **Fix:** invert the policy for the safebox's **own** authority secrets. Add a hard **never-vend / never-write
  set** — `TAKYON_CAP_SIGNING_KEY`, `TAKYON_SAFEBOX_TOKEN` (and ideally make `/v1/env` an **infra-secret
  allowlist** rather than a provider denylist) — enforced on **read (`{key}`, `/first`, `/snapshot`, `GET /v1/env`)
  AND write (`POST`/`DELETE /v1/env/{key}`)**. Source the signing key so it is not HTTP-writable.

### G2 — [HIGH] transitional internal-token operator proxy spends uncapped
- `_authorize_operator_proxy` branch (b) accepts the bare shared token and returns
  `enforce_ceiling=False, ceiling_microusd=0` (`safebox_provider_proxy.py:208-232`), metered against
  `TAKYON_PLATFORM_OPERATOR_USER_ID`. Any token holder drives Anthropic/Tavily spend with **no per-call
  ceiling and no capability**, bounded only by the platform operator's **cumulative** allowance
  (`billing.reserve` still fails closed at exhaustion — so it drains, doesn't infinitely exceed, *until* G3
  lets the attacker raise the allowance).
- **Fix:** remove the internal-token spend path once clients mint session capabilities; until then it is an
  open uncapped channel keyed on a token the attacker holds.

### G3 — [CRITICAL] runtime `DATABASE_URL` = BYPASSRLS owner; two of three money ledgers + ownership have no DB-side boundary
- Runtime login role is the Supabase **owner / BYPASSRLS** (`core.py:13288-13293`; `0030` header);
  `_leaf_conn` does **no role drop** (`core.py:13231-13246`). The DSN is on the runtime planes (and vendable
  as infra over `/v1/env`).
- **No DB protection on:** `billing_accounts` / `billing_entries` (`0002` — no RLS/REVOKE/definer),
  `business_creative_credit_accounts` / `_entries` (`0012` — same), `businesses.owner_user_id` (no migration
  locks it). `app_usage_events` (`0037`) REVOKEs write **only from `takyon_app`**; the owner login keeps
  implicit DML and owns the SECURITY DEFINER funcs, so the 0037 boundary is voluntary for the owner.
- **Impact (no token forgery needed):**
  - `UPDATE billing_accounts SET allowance_used_cents=0 / allowance_included_cents=huge` → mint operator allowance.
  - `UPDATE business_creative_credit_accounts SET balance_credits=…` → unlimited creative credits.
  - `UPDATE businesses SET owner_user_id=<victim>` → then `/v1/operator/session-token` mints a **validly
    signed** `operator.session` cap charging the **victim's** account (`safebox_authz.py:79-103` reads the
    rewritten row). I1 untouched; I2 broken cleanly.
- **Fix:** give the runtime a **NOBYPASSRLS non-owner** `DATABASE_URL`; mirror `0037` (RLS + SECURITY
  DEFINER reserve/settle, EXECUTE-only) onto `billing_accounts`/`billing_entries` and the creative-credit
  ledgers; lock `businesses.owner_user_id` writes behind a definer fn. The **safebox** should use a separate
  privileged role the runtime does not have.

### Secondary (note, don't block on)
- **operator.session replay** (`safebox_provider_proxy.py:188-206`): reusable caps have no per-token nonce →
  a captured token replays until `exp` (≤6h), each call charging the victim's allowance (capped per-call;
  can't exceed allowance). Fix: per-token cumulative cap or short rotation. (Med.)
- **`/v1/env/snapshot` route-shadowing:** `GET /v1/env/{key}` registered before `…/snapshot` → snapshot 500s,
  its denylist-strip never runs. Latent footgun.
- **Shared static `TAKYON_SAFEBOX_TOKEN`:** it authenticates "some plane," not "a trustworthy caller," so the
  safebox must derive **all** authority from the per-call capability + tamper-proof DB state — never the token.
  G1 and G3 are exactly where that principle is violated.

---

## What HOLDS — do not regress these while fixing

- Provider model/search/social keys: not on runtime planes (disk=0, env unset), `/v1/env` 404, outbound-only
  injection, error sanitization (`_sanitize_upstream_error` `:101-106`).
- Capability unforgeability: `verify_capability` HMAC+`compare_digest` (`safebox_capability.py:120-122`), empty-sig
  guard, no `alg` confusion; server-derived scope (`safebox_app.py:1481-1488` ignores `body.audience`);
  reserve-before-nonce + reserve-before-key (`safebox_broker.py:94-109`); single-use nonce
  `ON CONFLICT DO NOTHING` (`safebox_nonce.py:44-49`).
- Product-usage rail: `app_usage_events` write REVOKED from `takyon_app`, routed through `safebox_*_usage`
  SECURITY DEFINER funcs (`0037:419-435`) — a real wall **against `takyon_app` and evil sub-users**.
- RLS on `app_*` customer tables (`0027`) under `NOBYPASSRLS takyon_app` — real wall vs evil sub-users.
- Fail-closed network posture: every spendful/secret client path → `504`/`SafeboxAuthorityUnavailable`, no
  local-key or local-ledger fallback.

---

## Codex checklist

### A. Code audit (these currently FAIL the desired posture)
1. **G1:** confirm `TAKYON_CAP_SIGNING_KEY` & `TAKYON_SAFEBOX_TOKEN` ∉ `core.provider_key_denylist()`
   (`core.py:1210-1227`) and ∉ the fail-closed fallback (`safebox_app.py:612-628`); confirm `read_env_value`,
   `/v1/env/first`, `/v1/env/snapshot`, `GET /v1/env`, and `POST/DELETE /v1/env/{key}` (`safebox_app.py:1150-1204`)
   apply no protected-key guard. **After fix:** a never-vend/never-write set refuses both names on read+write.
2. **G2:** `safebox_provider_proxy.py:208-232` — confirm branch (b) returns `enforce_ceiling=False`. After fix: path removed/ceiling-enforced.
3. **G3 ledgers:** `grep -rn "ROW LEVEL SECURITY\|REVOKE\|SECURITY DEFINER" .../db/migrations/0002_ledgers.sql .../0012_business_creative_credits.sql` → expect none. Confirm no migration locks `businesses.owner_user_id`. Confirm `_leaf_conn` does no role drop (`core.py:13231-13246`).
4. **G3 role:** confirm runtime login is BYPASSRLS owner (`core.py:13288-13293`; `0030`) and `0037` REVOKE targets only `takyon_app` (`0037:419-435`).
5. **Don't regress the sound core:** `verify_capability` (`safebox_capability.py:120-122`), broker reserve-before-nonce (`safebox_broker.py:94-109`), nonce (`safebox_nonce.py:44-49`), mint ignores `body.audience` (`safebox_app.py:1481-1488`).

### B. Live probes (safebox `67.205.158.170`, key `~/.ssh/takyon_argon_alpha14`; use the runtime's own token; **never print secret values — status/size only**)
6. **G1 (was 200 on 2026-06-23 → must become 404):**
   `curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TAKYON_SAFEBOX_TOKEN" http://10.116.0.2:8000/v1/env/TAKYON_CAP_SIGNING_KEY` → **want 404**; repeat for `TAKYON_SAFEBOX_TOKEN`, and `POST /v1/env/first {"keys":["TAKYON_CAP_SIGNING_KEY"]}`. Then `POST /v1/env/__probe__ {"value":"x"}` + `DELETE` → want 401/403 (write gated).
7. **Provider-key denial still holds:** `/v1/env/{ANTHROPIC_API_KEY,OPENAI_API_KEY,GEMINI_API_KEY,TAVILY_API_KEY,FAL_KEY}` → **404** each (regression check).
8. **On-disk boundary holds:** `grep -cE '^(ANTHROPIC_API_KEY|OPENAI_API_KEY|FAL_KEY|TAVILY_API_KEY|GEMINI_API_KEY|COMPOSIO_API_KEY)=.+' /opt/takyon/secrets/.env /opt/takyon/.takyon/.env` on operator+subuser → **0** (was 0 on 2026-06-23); safebox → nonzero.
9. **G3 role:** as the runtime DSN, `psql -c "SELECT current_user, rolbypassrls FROM pg_roles WHERE rolname=current_user;"` → **want bypassrls=f after fix** (was `t`). Reversible writability probe (maintenance window): `BEGIN; UPDATE business_creative_credit_accounts SET balance_credits=balance_credits WHERE false; ROLLBACK;` → want a **permission error** after fix. Same on `billing_accounts`, `businesses`.
10. **G2:** with only the master token (no capability), the operator proxy must **refuse** (401/402), not spend.
11. **Log hygiene (standing):** `journalctl -u takyon-safebox.service --since "1h ago" | grep -iE 'sk-ant|sk-proj|tvly-|x-api-key|CAP_SIGNING'` → **no matches**.

**Priority:** G1 first (one route-policy change closes the worst I1+I2 collapse), then G3 (DB role separation +
RLS/SECURITY DEFINER on `0002`/`0012` + lock `owner_user_id`), then G2 (delete the internal-token spend path).
Re-run probes 6, 9, 10 to confirm each closes; 7, 8, 11 are standing regression checks. **Rotate
`TAKYON_CAP_SIGNING_KEY` and `TAKYON_SAFEBOX_TOKEN` after G1 lands** (they were vendable, so treat as exposed).
