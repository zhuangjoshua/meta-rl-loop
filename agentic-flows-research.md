# Agentic Flows for Generated Products — Research Report (2026-06-12)

Question: can the `product_workflow` skill produce agent-products, and if not, what should Takyon adopt
(orchestrator, cost metering, bounded tools) to get "text → MVP-complete product" with metered agentic flows?

Method: repo source inspection + deep-research workflow (25 sources fetched, 124 claims extracted,
25 adversarially verified 3-vote: 23 confirmed / 2 refuted) + 3 targeted primary-source follow-up agents
for the categories the verification budget didn't reach. Verified-claim citations marked ✓; follow-up
agent findings cite primary docs but were not adversarially verified.

---

## 1. Why `product_workflow` cannot ship agentic flows today (verified in source)

The doctrine intends an MVP-complete closed loop (input → action → result → save_record → return later)
with `ai_flows: 0–2`. Three hard walls block agent-products:

1. **One-shot AI primitive.** The only model access a generated product has is `POST /generate` →
   `broker_message_for_business` (plugins/takyon/ai_gateway.py:178): session check → plan/feature/model
   gate → reserve estimated cost → exactly ONE Anthropic `messages` call → settle. No tool calling, no
   loops, no streaming, no run state, Anthropic-only. Worker contract forbids direct provider calls;
   the no-pretend scan blocks faked agent UX.
2. **Per-call metering, no flow envelope.** `app_usage.py` reserve-then-settle is strong (micro-USD,
   per-business + per-end-user monthly caps, 402 semantics) but reserves for one call. An agent run is
   N calls of unknown N: no run-level budget, no mid-run kill on runaway loops.
3. **Capability surface is deliberately bounded** (5–10 backend actions, rails-only side effects, no
   code execution in the product plane). Correct posture — matches "decently complicated but not
   all-powerful, no file writes" — but there is no bounded orchestration rail to express multi-step
   flows declaratively.

Conclusion: the substrate produces CRUD-plus-a-generate-button MVPs. Agent-products need a new runtime
rail (durable runs + flow-level budget), which should be adopted, not invented.

---

## 2. Cost metering / LLM gateways (adversarially verified — 23 confirmed claims)

**Winner: self-hosted LiteLLM Proxy (MIT).** The only option satisfying all three requirements
(per-end-user budget, blocked at cap, per-call cost) as free self-hostable OSS: ✓
- Budget objects: `POST /budget/new` (max_budget + tpm/rpm) attached via `POST /customer/new`, or
  global `max_end_user_budget`; over-budget users get explicit rejection errors. (docs.litellm.ai/docs/proxy/customers, /users)
- Attribution per call via OpenAI `user` field or `x-litellm-end-user-id` header — so ANY orchestrator
  above the proxy meters a whole multi-step run into one end-user budget without per-user keys. ✓
- Full budget hierarchy (proxy/team/user/key/customer) + virtual keys + SpendLogs per call; needs Postgres. ✓
- **Caveats (verified):** real enforcement bugs — budget-reset persistence for auto-created users
  (#25386, #22019, #24675), team-key bypass (#12905), v1.82.3 regression (#26672), pass-through bypasses;
  Anthropic-native `/v1/messages` budget bypass FIXED (PRs #24205/#26248) — pin a version above that floor.
  Treat LiteLLM as defense-in-depth backstop; Takyon's micro-USD ledger stays authoritative. ✓

**Runner-up / second cap: Cloudflare AI Gateway spend limits** (open beta 2026-06-05): dollar budgets,
20 rules/gateway, `split by value` on `metadata.user_id` = per-user buckets, 429 at cap. ✓ BUT cost is
best-effort estimation and enforcement eventually consistent — the claim that it records exact per-call
cost was REFUTED 0-3. Backstop only. ✓

**Others:** Portkey budget limits are real hard caps (auto-expiring keys) but Enterprise/select-Pro
gated ✓; OpenRouter has per-key USD limits + daily/weekly/monthly counters via Provisioning API
(key-per-end-user model) ✓; Helicone is segmentation/alerts-first, though `Helicone-RateLimit-Policy`
gives windowed per-user cents-denominated 429s ✓; LangSmith auto-costs every step of a LangGraph trace
tree from its pricing table (observability; enforcement unresolved, 1-2 split) ✓.

## 3. Orchestrators (follow-up agent, primary docs)

Cross-cutting finding: **none of the top candidates ships a native run-level USD budget cap** — which
validates gateway-level enforcement. Comparison of finalists:

| | License | Lang | Durability | Tenancy | Budget-ish primitive | Proxy-routable |
|---|---|---|---|---|---|---|
| **LangGraph (OSS lib)** | MIT | Python | Postgres checkpointer (`langgraph-checkpoint-postgres`) | none (lib) | `ModelCallLimitMiddleware` / `ToolCallLimitMiddleware` (count-based abort) | 100% — every call via `base_url` |
| **Hatchet** | MIT | Python | event-log checkpoints, Postgres-only | native tenants + fair queues | CEL dynamic rate limits per user | n/a (your code calls LLMs) |
| Temporal | MIT | Python | event-sourced (strongest) | Namespaces documented | none | via activities |
| Inngest (+AgentKit) | SSPL→Apache after 3y | TS (AgentKit) | step memoization | per-key fairness | flow control | `baseUrl` on model adapters |

Eliminated: Mastra/Trigger.dev/Vercel WDK (TS-only), Cloudflare Agents (no self-host), Restate (BUSL,
small), OpenAI Agents SDK (not durable alone; Temporal wraps it). LangGraph Platform self-host is
Enterprise-gated — use the MIT lib only.

**Recommendation: LangGraph OSS + Postgres checkpointer**, graphs compiled at runtime from per-tenant
JSON config (flows-as-data, no tenant code, no fs/shell — tools are an explicit allowlist). Hatchet
(MIT, Postgres, native multi-tenant queues) as the durable engine underneath if queueing/retries are
needed; Temporal if operational conservatism wins.

## 4. Bounded tool platforms — REVISED for self-host-only constraint (two follow-up sweeps, primary docs)

The initial pass recommended Composio/Arcade — wrong for this platform: both are SaaS-or-Enterprise.
Re-ranked with self-host MANDATORY, license must permit embedding in commercial multi-tenant SaaS,
per-tenant isolation + allowlist in the FREE core:

**Top pick (integrations-catalog shape): ACI.dev** (github.com/aipotheosis-labs/aci, **Apache-2.0,
whole repo, no ee/**). The only OSS candidate with all the Composio primitives in the free core —
per-end-user "linked accounts" OAuth isolation, per-agent API keys restricted to explicit app lists,
tenant-scoped MCP server (`aci-mcp` with `--linked-account-owner-id` + `--apps` allowlist), ~600
integrations. And it is literally Takyon's stack: FastAPI + Postgres/pgvector + Docker Compose,
Python 3.12. Risks: dev-portal auth assumes PropelAuth and secret encryption targets AWS KMS — budget
a small de-SaaS adaptation; ~5-person funded team, commit cadence slowing (last push 2026-05-28) —
Apache-2.0 makes a hard fork viable if it stalls.

**Top pick (MCP-gateway shape): IBM ContextForge** (github.com/IBM/mcp-context-forge, **Apache-2.0**,
v1.0.3 2026-06-10). Python FastAPI + Postgres again. First-class multi-tenancy (teams, JWT
claim scoping enforced at the DB query layer, RBAC), tool-level bounding via "virtual servers"
(explicit tool-ID bundles per tenant endpoint), per-user encrypted OAuth token storage with
auto-refresh, OTel. Risks: ops-heavy, ~928 open issues — pin versions, wrap behind own API.
**Enforcement-plane runner-up: agentgateway** (Apache-2.0, Linux Foundation, Rust): best-in-field
allowlisting — CEL rules per `tools/call` AND unauthorized tools filtered out of `tools/list` — but
deliberately no credential vault (platform injects per-tenant tokens).

**Fallback plumbing: Nango free self-host** (ELv2) covers exactly per-end-user OAuth flows + token
refresh + authenticated proxy (largest catalog, healthiest project, 10.4k★ daily commits); agent
allowlist + MCP exposure would be a thin Takyon layer over its connection API. ELv2 caveat: don't
expose Nango's own surfaces to customers; MCP/syncs/RBAC are Enterprise-gated.

**Disqualified and why:** Composio (catalog/auth plane is the hosted service; self-host secondary),
Arcade (engine closed), Klavis (Apache-2.0 code but OAuth/token plane phones home to api.klavis.ai
even self-hosted), Activepieces (MIT core, but multi-tenant embedding = the ee-licensed part), n8n
(Sustainable Use License explicitly disallows embedding in a customer-facing product), Pica (CE
abandoned), Windmill (AGPL + wrong shape), MCP Mesh/Lunar MCPX (non-OSI license terms), Docker MCP
Gateway/Director/Lasso (single-user shaped), Plugged.in (94★).

**Structural insight that reframes the category:** Composio's real moat is vendor-managed shared
OAuth apps — and that advantage *evaporates under self-hosting*. Whatever is chosen, the platform
registers its own OAuth app per provider (one per deployment) and the gateway runs the
authorization-code flow storing per-tenant tokens — exactly ContextForge's model. Plan an internal
OAuth-app registry (per-provider client IDs/secrets, redirect URIs) as a platform ops surface
regardless of tool choice. The "no shell/filesystem" rule is enforced by never registering such MCP
servers and pinning per-tenant tool allowlists at the gateway.

## 5. Text→MVP builders: how generated apps get runtime AI (follow-up agent, primary docs)

Two camps:
- **Platform-brokered (consumer/no-code):** Lovable AI (auto-provisioned `LOVABLE_API_KEY`, edge-function
  proxy, token passthrough billing, 402 at empty balance), Base44 (`invokeLLM` + workspace "integration
  credits"), Replit AI Integrations (Replit-provisioned credentials, 300+ models via OpenRouter, billed
  to builder account). **Same architecture as Takyon's generate rail.**
- **BYO-key (code-first/OSS):** Bolt.new (OPENAI_API_KEY secret in Supabase edge fn), Databutton
  (`db.secrets.get`), bolt.diy, Dyad, open-lovable — platform meters nothing at runtime.
- **v0/Vercel hybrid:** generated apps are AI-Gateway-native; per-API-key spend caps with hard rejection
  shipped 2026-06-09; per-end-user `user` attribution exists only as paid Custom Reporting (observability).

**Key competitive finding: no surveyed builder ships per-END-USER budget enforcement for generated
apps' runtime AI.** Takyon's `app_usage.py` per-customer budgets are already ahead of the entire
category on the metering axis. The gap is orchestration, not metering.

---

## 6. Recommended minimal stack for Takyon

1. **Keep the micro-USD ledger authoritative.** Put a pinned, self-hosted **LiteLLM proxy** between
   `ai_gateway.py` and providers: tag every call with the app_user id; gain multi-provider support and a
   hard per-end-user backstop; settle the canonical ledger from actual per-call cost (extend
   `agent/usage_pricing.py` resolution as required by workspace rules). Version floor above the
   Anthropic-passthrough budget fix (PRs #24205/#26248).
2. **Add one new runtime rail: `flows`** (name TBD) in `PRODUCT_RUNTIME_RAILS`: declarative per-business
   flow definitions (JSON on the surface contract / product files — steps over existing rails: generate,
   records, directory, connections; no fs/shell, explicit tool allowlist), executed server-side by a
   **LangGraph (MIT) interpreter** with the Postgres checkpointer for durable/resumable runs. Budget
   semantics: reserve a flow-level envelope up front, settle per step, kill the run at the envelope —
   composing the existing reserve-then-settle instead of replacing it. Count-based middleware as a
   second fuse. Hatchet only if/when queueing demands it.
3. **Tools via ACI.dev (Apache-2.0, self-hosted)** behind a guarded business tool: per-business
   linked accounts, per-agent app allowlists, tenant-scoped MCP endpoints — same FastAPI/Postgres
   stack, deployable next to the runtime on the VPS. If the gateway-over-MCP shape is preferred
   instead, IBM ContextForge (Apache-2.0, also FastAPI/Postgres) with virtual servers as the
   per-tenant allowlist, optionally agentgateway as the CEL-enforced data plane. Either way, Takyon
   owns an OAuth-app registry per provider (the vendor-shared-OAuth-app convenience does not exist
   self-hosted). Tool-call audit keyed by app_user id through the existing receipts rails.
4. **Optional second cap:** Cloudflare AI Gateway spend limits (split-by-value user_id) once out of beta —
   backstop only, never ledger of record (refuted 0-3 on exactness).
5. **Doctrine change:** `product_workflow.ai_flows` gains a declarative `flow` variant; the validator
   ties any flow to the `flows` rail in `runtime_features`, same pattern as `persistence_rail`.

Open questions (not adversarially verified): LangGraph-side budget enforcement beyond observability;
whether Cloudflare's eventually-consistent caps can ever satisfy strict reserve-then-settle; Composio
enterprise self-host pricing.

---

## 7. The missing core primitive: bounded per-product backends (2026-06-12 follow-up)

Gap analysis derived from the MVP-complete doctrine itself — to cover the space of one-loop products,
a generated product needs 8 capability classes; the rails provide ~1.5 (records = weak KV, generate =
one-shot). Missing entirely: **governed custom backend actions** (the doctrine demands 5–10 per
product), product-level schedules, allowlisted outbound HTTP, inbound webhooks, file/object storage,
customer email, real queries over entities. This — not design — is why every product degenerates into
a thin client over generic rails.

Two market shapes (both sweeps primary-source-cited; full agent reports in session transcript):

**Shape A — self-hostable BaaS bundle.**
Winner: **Appwrite** (BSD-3) — the only one with native multi-project in ONE self-hosted deployment,
all 8 classes (incl. multi-provider email + per-function cron), container-isolated functions in 16+
runtimes, and a per-project usage API that maps onto the metering rail. Costs: ~23 containers / 4GB+,
brings its own MariaDB+Redis, no per-project resource caps, and — decisive for Takyon — **its own
auth/teams/billing plane that would duplicate the canonical rails** (violates the one-source-of-truth
doctrine for auth/checkout/entitlements). Runner-up: **PocketBase-per-product** (MIT, one ~50–100MB
binary + SQLite each; PocketHost proves hundreds per machine) — but its goja JSVM exposes `$os` shell
with no off-switch (needs a custom Go build with reduced bindings, or cgroup/user confinement as the
sandbox). Eliminated: Supabase self-host (1 project per ~10-container stack, confirmed unchanged
June 2026 — Lovable Cloud rides Supabase's *managed* fleet, not the OSS compose), Convex (FSL
competing-use risk for exactly this embedding), Nhost (stack per product), n8n-class (license).

**Shape B — bounded compute runtime as a new rail.**
Winner: **wasmtime + Spin (+ StarlingMonkey/ComponentizeJS for JS/TS)** — all Apache-2.0; the ONLY
runtime whose vendor docs affirmatively endorse executing untrusted code; deny-by-default capabilities
(no fs/shell concept; network only via per-component `allowed_outbound_hosts`); and uniquely
**deterministic per-invocation CPU metering (wasmtime fuel) + unavoidable wall deadlines (epoch) +
per-Store memory caps** — fuel maps 1:1 onto reserve-then-settle. Cost: deploy-time
bundle/componentize step for LLM-written TS, partial npm coverage.
Pragmatic first step: **Deno subprocess per invocation** (MIT; the Val Town pattern via their OSS
`deno-http-worker`): `--deny-write --allow-net=<rails+approved hosts>`, no `--allow-run/--allow-ffi`,
under `systemd-run` CPUQuota/MemoryMax, metered from cgroup cpu.stat. Best LLM-output fidelity
(native TS + fetch). Deno's own docs say permission flags alone aren't sufficient for hostile code —
hence the cgroup/seccomp wrapper, and wasmtime as the destination.
Disqualified: workerd (own README: must run inside a VM for possibly-malicious code; CPU/memory
limits closed not-planned — issue #49), isolated-vm (maintenance mode, in-process blast radius),
Firecracker/E2B (needs KVM, writable guest fs breaks no-file-writes, heavy), goja (ES5, no metering),
WinterJS (deprecated).

**Recommendation for Takyon: Shape B as a new `actions` rail** — it composes with the existing
canonical rails instead of installing a second auth/billing universe (Appwrite's fatal flaw here).
Per product: worker ships 5–10 TS functions; the runtime executes them with capability grants =
session-scoped rail calls + per-business outbound-host allowlist; invocations meter through the
existing usage ledger (fuel/cgroup → micro-USD). Add the small missing rails alongside: product
schedules (extend the existing cron substrate), inbound webhooks (web_server route → action),
object storage, customer email (one transactional provider rail), and records-v2 queries
(filter/sort on indexed fields). Start Deno-subprocess (ship fast), migrate hot path to
wasmtime/Spin (hostile-grade tenancy + exact fuel metering). Combined with §6 (LiteLLM + flows +
ACI.dev), this is the full "powerful enough primitive" for the MVP-complete space.

Research cost: deep-research workflow 108 agents / ~3.2M subagent tokens; 3 follow-up agents ~194k.
Full verified-claim record: /private/tmp/claude-502/-Users-Zygote-Downloads-takyon/107b4e6b-d5a3-4b10-8e4b-094b550f327f/tasks/w7smfpbu8.output
