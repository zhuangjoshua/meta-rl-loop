# Safebox boundary — VERIFICATION runbook (Codex: verify only)

**Scope (read first):** Codex's job here is to **independently verify** that the safebox secret/money
boundary is closed, and to **report pass/fail** with the failing probe. Codex does **NOT**:
- modify code (the fixes are owned by the implementer),
- apply migrations,
- rotate keys (the operator does that).

Run with the runtime's own `TAKYON_SAFEBOX_TOKEN`. **Never print a secret value** — assert on HTTP
status / row counts / `rolbypassrls`, never on the body. Report each item as PASS / FAIL / (RED = known
open, see status column).

**Hosts:** safebox `67.205.158.170` (private `10.116.0.2:8000`), operator `137.184.75.57`, sub-user
`134.209.123.8`; key `~/.ssh/takyon_argon_alpha14`. Prod runtime at `/opt/takyon/hermes-agent-main`;
token in `/opt/takyon/secrets/.env`. Code-audit lines refer to `plugins/takyon/`.

Helper on the safebox host:
```bash
set -a; . /opt/takyon/secrets/.env; set +a; TOK="$TAKYON_SAFEBOX_TOKEN"; B="http://10.116.0.2:8000"
g(){ curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $TOK" "$B/v1/env/$1"; }
pw(){ curl -s -o /dev/null -w "%{http_code}" -X POST -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' -d '{"value":"x"}' "$B/v1/env/$1"; }
```

## A. G1 — `/v1/env` READ allowlist  (expected: GREEN today)
| # | Probe | Expect |
|---|---|---|
| A1 | `g TAKYON_CAP_SIGNING_KEY`, `g TAKYON_SAFEBOX_TOKEN`, `g STRIPE_BILLING_WEBHOOK_SECRET`, `g SUPABASE_SERVICE_ROLE_KEY` | **404** each |
| A2 | `g ANTHROPIC_API_KEY` `g OPENAI_API_KEY` `g GEMINI_API_KEY` `g TAVILY_API_KEY` `g FAL_KEY` | **404** each |
| A3 | `g DATABASE_URL` `g STRIPE_SECRET_KEY` `g UMAMI_API_KEY` | **200** each |
| A4 | `POST /v1/env/first {"keys":["TAKYON_OPENMETER_URL","OPENMETER_URL","OPENMETER_API_URL"]}`; same for token aliases; `g TAKYON_DASHBOARD_SESSION_TOKEN` | **200** (regressions un-done) |
| A5 | code: `core.env_egress_allowed` uses `_INFRA_ENV_ALLOW_EXACT` only (no `_INFRA_ENV_ALLOW_PREFIXES`); the 4 never-vend names in `_SAFEBOX_SELF_AUTHORITY_SECRETS` | exact-name allowlist; deny-first |

## B. G1 — `/v1/env` WRITE/DELETE  (expected: GREEN today)
| # | Probe | Expect |
|---|---|---|
| B1 | `pw DATABASE_URL` `pw ANTHROPIC_API_KEY` `pw STRIPE_SECRET_KEY` `pw TAKYON_CAP_SIGNING_KEY` `pw takyon_cap_signing_key` `pw database_url` | **403** each (no 500) |
| B2 | `DELETE /v1/env/DATABASE_URL`, `DELETE /v1/env/TAKYON_SAFEBOX_TOKEN` | **403** each |
| B3 | after a refused write, `g DATABASE_URL` value unchanged (read still 200) | unchanged |

## C. G2 — operator proxy is capability-only  (expected: GREEN today)
| # | Probe | Expect |
|---|---|---|
| C1 | bare-token `POST /v1/messages`, `POST /v1/proxy/anthropic/messages`, `POST /v1/proxy/tavily/search` (no capability) | **401** each (no spend) |
| C2 | code: `_authorize_operator_proxy` has no bare-token branch; it is the first statement of all 3 routes | capability-only |

## D. G3 — runtime DB privilege  (verify AFTER it lands; RED until then)
| # | Probe | Expect (post-fix) | Today |
|---|---|---|---|
| D1 | as the runtime `DATABASE_URL`: `SELECT current_user, rolbypassrls FROM pg_roles WHERE rolname=current_user;` | `rolbypassrls = f` | `t` (RED) |
| D2 | `BEGIN; UPDATE billing_accounts SET allowance_used_cents=allowance_used_cents WHERE false; ROLLBACK;` — same on `business_creative_credit_accounts`, and `UPDATE businesses SET owner_user_id=owner_user_id WHERE false` | **permission denied** (owner_user_id: column-denied) | succeeds (RED) |
| D3 | a legit reserve/settle through the `SECURITY DEFINER` fn succeeds; the app still provisions billing on first login, grants starter credits, and creates a business | works (no regression) | n/a |

## E. Residuals — confirm the vending set is EXACTLY the documented one
| # | Check | Expect |
|---|---|---|
| E1 | enumerate every name still vending over `/v1/env` read that is sensitive; the only authority/identity/data-plane ones are the documented residuals (`STRIPE_WEBHOOK_SECRET`, `AUTH0_CLIENT_SECRET`, `AUTH0_SECRET`, `SUPABASE_S3_SECRET_ACCESS_KEY`, `R2_S3_SECRET_ACCESS_KEY`, `CLOUDFLARE_API_TOKEN`, `STRIPE_SECRET_KEY`, `POSTMARK_SERVER_TOKEN`) | nothing worse than the documented set |
| E2 | `SUPABASE_JWT_SECRET` (after hardening): `g SUPABASE_JWT_SECRET` → **404**, AND `verify_supabase_jwt` rejects an attacker `alg=HS*` token (pinned to the asymmetric issuer) | post-fix: 404 + HS rejected. Today: vends (RED) |

## Operator (NOT Codex)
Rotate `TAKYON_CAP_SIGNING_KEY`, `TAKYON_SAFEBOX_TOKEN`, and `STRIPE_BILLING_WEBHOOK_SECRET` — they were
vendable historically. (Independent of the code fixes; do it whenever.)

## Reporting
For each section return PASS/FAIL + the first failing probe and its actual value. Do not attempt a fix —
hand the failure back. A section marked RED above is a known-open gap being worked, not a Codex action.
