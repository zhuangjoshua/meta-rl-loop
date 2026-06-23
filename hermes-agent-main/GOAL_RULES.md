# GOAL_RULES

Canonical money-gate + secret-boundary rules for the Takyon runtime. Code comments cite these by
section (`GOAL_RULES §N`). The overriding rule: **the safebox is the only place a provider key exists
or a paid call is authorized; every paid provider call is money-gated AUTHORITATIVELY inside the
safebox before any key resolves; there is no gating outside the safebox and no ungated path.**

## §1 — Authoritative gating on the safebox (the secret boundary)

- Provider keys (model/image/video/search/social) live ONLY on the safebox host. A runtime plane
  (operator / sub-user / worker) must never resolve a raw provider key into its own memory. On the
  VPS the service units `UnsetEnvironment=` every provider key, and clients call the safebox.
- The safebox holds the key, makes the provider call, and returns a KEY-FREE result. The key is
  injected only into the outbound provider request header; it never appears in any response, log,
  error, or LLM message/context. (`ai_provider.call_anthropic` sends `x-api-key` as a header, never in
  `messages` — so no prompt can make a model print its key.)
- The safebox exposes only data routes (`/healthz`, `/v1/env/*` (legacy, being retired), `/v1/providers/*`,
  `/v1/creative/*`, `/v1/operator/*`, `/v1/token/mint`, `/v1/user-api-keys/*`, `/v1/creative-credits/*`,
  `/v1/stripe/*`). There is NO eval/exec/shell/file-write route — an LLM cannot run code on the safebox.
- Every paid route enforces the money gate on the safebox's OWN DB connection, keyed on a VERIFIED
  capability scope (server-derived, never client-asserted; HMAC signing key is safebox-only;
  single-use nonce / TTL / audience / cost ceiling enforced). No client-side gate survives.

## §2 — The three money rails (each keyed on a CANONICAL account)

The only two account types are the Takyon **user** (operator / business owner) and the product
**sub-user** (app customer). The gate keys on the real one — NEVER a synthetic "platform" account.

| Rail | Adapter / ledger | Keyed on | Routes |
|---|---|---|---|
| product sub-user AI (consumption) | `_UsageLedgerAdapter` → `app_usage` reserve→settle | `{business, app_user}` (single-use product capability) | `/v1/providers/{anthropic/messages,tavily/search}` (engaged by `TAKYON_PROVIDER_BROKER=1`) |
| creative logo/UGC/static-ad (fixed price) | `_CreditLedgerAdapter` → `business_credits` reserve→commit→release | business (operator-owned, `authorize_operator_call`) | `/v1/creative/{reserve,commit,release}` + gated `/v1/providers/{gemini/logo,openai/images,fal/{path}}` |
| operator/worker/web AI (control-plane) | `_OperatorBudgetAdapter` → `billing.py` reserve→settle→refund | the REAL operator `takyon_user_id` (business owner), via a reusable `operator.session` capability (`/v1/operator/session-token`, ownership-proven) | `/v1/messages`, `/v1/proxy/{anthropic/messages,tavily/{op}}` (streaming settles actual from the SSE usage event) |

There is NO ungated `/v1/proxy/{gemini,openai,fal}` — those were deleted. Pricing is resolved
server-side fail-closed from the canonical tables (`agent/usage_pricing.py`,
`core._creative_credit_total_cost`); an unpriced action is refused, never run free.

## §3 — Per-user budget, no flat pool

There is NO flat per-business pool cap. The per-user AI allowance is derived from the active PAID
subscription's `included_ai_budget_microusd`, pro-rated to the weekly window (× 7/30); no plan ⇒ 0 ⇒
the reserve refuses (402). No free-tier floor. Operator AI is gated by the operator's own
`billing_accounts` allowance.

## §4 — Buy, don't build; fail-safe ledgers

Prefer the existing canonical rail over a second money store (operator AI reuses `billing.py`, the same
primitives `web_spend.py` uses — no new control-plane ledger). Audit/usage mirroring is
fire-and-forget and fail-safe; the authoritative reserve→settle is synchronous and fail-closed.

## §7 — Paid-provider key resolution (for any NEW paid tool)

Never read a provider key from `os.environ` in a business tool. Resolve it ONLY inside a safebox
authority route via `safebox.first_env_backed_value(*ALIASES)` (register the alias in
`core._API_ENV_ALIASES`), pass it as an explicit arg to the provider client (never `os.environ`), and
fail closed (`*_unconfigured` / 503) before any reserve or call. A new paid capability MUST be gated
(usage or credits) in the same route before it ships — no ungated paid path.

## Deploy (per `AGENTS.md` rails — safebox routes run ONLY on the safebox host)

1. Stage only the intended files in the OUTER repo, commit, `git push origin main` (fetch-before-push;
   Joshua is a concurrent pusher); verify the GitHub Actions run.
2. `rsync` the changed `plugins/takyon/safebox_*.py` (+ `creative_gateway.py`, `core.py`,
   `operator_gateway.py`, subprocess scripts) to ALL THREE hosts (safebox `67.205.158.170`, operator
   `137.184.75.57`, sub-user `134.209.123.8`) under `/opt/takyon/hermes-agent-main/` with
   `~/.ssh/takyon_argon_alpha14` (`-ptz`, no `-E/-X`, no AppleDouble); `py_compile` + import-smoke the
   touched files BEFORE restart.
3. `systemctl restart takyon-safebox.service` on the safebox; restart `takyon-dashboard.service` +
   `takyon-worker.service` (operator) and `takyon-subuser.service` (sub-user) when client code changed.
   Verify `is-active`, `/healthz`, `/openapi.json` (gated routes present, ungated `/v1/proxy/{gemini,
   openai,fal}` are 404), and that no raw `ANTHROPIC_API_KEY` is in the dashboard/worker process env.
4. E2E over the private IP `http://10.116.0.2:8000`: product broker call settles ONE usage event (no
   double-charge); a creative call reserves→commits credits key-free; an operator call (session
   capability) increments the REAL owner's `allowance_used_cents`; the safebox journal shows
   `POST /v1/operator/session-token 200` → `POST /v1/messages 200` from real CEO/worker traffic. No
   provider key in any response.
5. Migrations apply on the VPS via the CLI (creds resolve from the safebox there), not a local shell.
6. Runtime env contract: set `TAKYON_SAFEBOX_URL` + `TAKYON_SAFEBOX_TOKEN` (+ `TAKYON_PROVIDER_BROKER=1`
   in the TRACKED unit for the product broker); set NO raw `ANTHROPIC_*`/`OPENAI_*`/`TAVILY_*`/`FAL_*`
   on any runtime plane.

## Remaining cutover (ordered; the safebox is the sole money/key authority once done)

1. ✅ all three money rails authoritative on the safebox (product / creative / operator) — LIVE.
2. ✅ CEO loop + docker + non-docker worker key-hiding (mint `operator.session` for the real owner,
   `ANTHROPIC_BASE_URL`=safebox root, delete the raw-key branches) — LIVE.
3. ✅ `TAKYON_PROVIDER_BROKER=1` in the tracked units.
4. ⏳ stop the agent credential-pool init from probing `/v1/env` for a default provider key, then
   delete the `/v1/env/{key,first,snapshot}` PROVIDER-key vending (denylist provider-key names; KEEP
   infra secrets DB/Stripe/Auth0). This makes "no raw key on any plane" fully true.
5. ⏳ drop the `bypass=True` (rls_bypass) gateway conn for usage-ledger writes (`runtime_app.py:231`);
   run those writes as `takyon_app` so migration 0037's REVOKE binds the runtime.
6. ⏳ LAST (operator-sequenced): `git rm secrets/.env` + `.gitignore` + history scrub (filter-repo) +
   ROTATE every key. `secrets/.env` is git-tracked with the master `TAKYON_SAFEBOX_TOKEN` — the
   highest-severity remaining exfil, independent of the money gate.
