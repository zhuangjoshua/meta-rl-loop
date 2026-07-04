# General Apps Plan — the Business Capability Manifest

**Goal:** the agent can build ANY real product — not just the three studied briefs, and not
fixed rails + a static SPA. **Method:** the capability space was mined exhaustively (three
briefs in full ≈ 125 capabilities, the landed Takyon rails, all existing plans, a 25+
archetype sweep, a first-principles CS sweep ≈ 100 dimension-values — 383 entries total),
the five-axis draft was adversarially attacked, a general basis was synthesized, then two
breakers tried to find unmappable capabilities. This doc is the corrected result. Verdict on
the previous draft: **restructure, not replace** — the reduction (declaration over
branching) survives; the ontology was wrong. Money detail: `subuser-billing-plan.md`
(amended below). Line anchors are pointers; the code is truth.

## 1. The formalism

**One open-keyed typed manifest per business** — the surface contract generalized
(`runtime_features`, `money_shape`, `archetype` are already its first three keys). Every
value is a reference into a **per-concern registry** (RUNTIME_RAILS, CHANNEL_REGISTRY,
CreativeProviderSpec, job kinds, connection kinds, …). **An archetype is a named, versioned
manifest preset** in the same grammar — never a type, never a code path. The "namespaces"
below are documentation chapters over the open key set; arity is not ontology.

**One composition checker** validates the whole manifest at a single choke point (where
`money_shape.assert_write_matches_shape` validates plan writes today), enforcing three
constraint families that are already load-bearing in code:

1. **Dependency closure** — the rails DAG (`core.py:510`) generalized to all registries.
2. **Obligation discharge** — declaring a capability that carries an obligation without its
   gate refuses composition (UGC ⇒ moderation, broadcast ⇒ consent ledger, order ⇒ tax
   posture, cold outreach ⇒ outreach posture, regulated provider ⇒ posture pack).
3. **Money soundness** — the checker **calls** `compose_plan` and `money_shape`; it never
   performs money arithmetic itself. The business-shared cost plane lands **inside**
   `compose_plan` (as a priced input), so margin stays one computation in one SSOT.

Enforcement is over each registry's **native** fields — no uniform row-schema retrofit.
`cost_gate` is REQUIRED iff the entry names a priced operation (generalizing
`CreativeProviderSpec.MissingMoneyGate` so "no ungated paid capability" is unconstructable
platform-wide); principal scope derives from `auth_tier` (extend the `APP_AUTH_*` enum with
org/api_key/service tiers — no parallel `subject` field).

**Gates are rows IN the per-concern registries**, not a parallel gate registry: a value
whose implementation is `gate:<name>` is a registered deferral/refusal. One lookup answers
declared / deferred / refused; the refused list derives from registry rows. An unknown
key/value fails closed naming the nearest registry — silent drops are structurally
impossible, and modular.md's golden rule becomes the type system.

**Single-declaration property:** every capability has exactly one authoritative manifest
key. Derived things are cross-references, not peer values (interfaces are bindings over
actions; a CLI is a Distribution artifact wrapping the api binding).

## 2. The namespaces

### 2.1 Principals (identity & access) — the audit's #1 hole
Values: individual subuser (default) · anonymous/device principal (merge-on-signup,
guest-order claim) · org/team (membership, RBAC roles) · API principals (`tk_`/`tkg_`) ·
service principal · `auth_assurance` (password/OAuth/magic-link default; `mfa` via Supabase
native, gate `mfa_unconfigured`) · gates: `sso_unconfigured`, `impersonation_unsupported`,
`sandbox_keys_unsupported` (test-mode gateway keys — deferred until an API-product hits it).
- EXISTS: Supabase JWT → hashed business-scoped sessions (`app_identity.py`, migration
  0026); gateway keys (`app_gateway_keys.py`, 0009); service sessions
  (`app_actions.py::execute_scheduled_action`); per-principal rate limiter (0055).
- DELTA: `app_orgs` + `org_members(role)` + entitlements attachable to org + role on
  RailRoute; anonymous/device principal row + SECURITY DEFINER merge.
- Quantity billing is NOT hardwired to member-seats: see Money `quantity_source`.

### 2.2 Data (state)
Values: none · per-customer records · per-business relational schema · shared ingested
dataset (an explicitly FUNDED build step, never a free byproduct) · blob/files (declared
MIME allowlist, signed expiring URLs) · vector index + embeddings · FTS/facets/timeseries
(plain Postgres — documented, no new store) · append-only ledgers · gate:
`event_store_unsupported` (pixel-scale ingestion, graduates at a named write-volume
threshold).
- EXISTS: `app_records.py` bounded-query store; `app_media.py` image quotas + StorageBackend.
- DELTA: per-business PG schema + schema-scoped role (the `takyon_migration` privilege split
  per business) + `business_db_migrate` (additive-only, receipted) + `ctx.db`; blob/MIME
  generalization + signed download URLs on the same meter; cluster-wide pgvector +
  embeddings priced in `usage_pricing.py`; **`pii_manifest` required on every rail** (feeds
  the DSR rail). Neon-per-business = later graduation behind the same `ctx.db`.

### 2.3 Compute
Values: request-response action · scheduled per-customer (attributed on-behalf) · scheduled
business-wide (business-shared funded) · queued job · webhook-triggered · cadence
daily|hourly (minute = gate) · confirm-spend quote + bounded autopilot envelope ·
**render_own_content worker** (PDF reports, screenshots of the business's own pages —
policy-cold, near-term) · realtime: SSE v0 on generate/action routes now, pub/sub v1 = gate
· gates: `third_party_scrape` (see Governance ruling), `customer_code_unsupported`,
heavy-native, `workflow_orchestration_unsupported`.
- EXISTS: Deno-sandboxed actions with SSRF guards + source scanner, metered per invoke;
  scheduled actions with idempotent windows; `jobs.py` at-least-once lanes.
- DELTA: trigger declaration gains `webhook|queue`; one ingestion job kind
  (provenance-receipted, business-shared attributed); quote path `{cost, balance_after,
  quote_id}` consume-once + declared autopilot envelope (auto-confirm below N + pace cap) —
  Bazzly's confirm-spend and Autopilot as money-plane declarations, not UI copy.
- **Workflows v1 = sanctioned composition**: records-backed state machine advanced by
  queued/webhook-triggered actions (the POD order reconciler is the proof). The
  checkpoint/compensation/replay tier is the named gate.

### 2.4 Connections (integrations, both directions)
Values: outbound metered egress (business-scoped) · per-CUSTOMER OAuth connections
(subject-scoped token vault) · inbound provider webhooks · per-business inbound hooks ·
outbound webhooks to customer URLs (`webhook_out`) · fulfillment providers (Shopify via
Composio, Printful POD) · gate: `bank_data_unsupported` (requires posture pack). Mailbox
connectors (Nylas-class send-as-customer) are a **provider profile** on per-customer
connections + egress, not a separate value — and carry the cold-outreach obligation.
- EXISTS: Composio connections; webhook_events FOR-UPDATE dedup; safebox broker routes.
- DELTA: ONE generic metered `POST /v1/egress` + `ctx.egress` (reserve→attach→call→settle,
  credential attached only for the connection's own host, internal-address refusal);
  **NEW `provider_connections` leaf** — `app_connections.py` is the social like/pass/block
  rail and must NOT carry credentials; "one table" means one per CONCERN; per-customer
  consent/callback rail flow + refresh job + revocation (business-level consent stays
  `business_request_credential` on operator_approvals); per-business inbound hooks
  (safebox-held per-source secret, dedup, dispatch to a webhook-trigger action);
  `webhook_out` = subscriptions + signed deliveries as queue jobs reusing the egress
  internal-address refusal, metered per delivery.

### 2.5 Bindings (interfaces — DERIVED)
**Theorem: one action surface, N bindings; a binding without a declared action is
unconstructable.** Values: web UI · public REST `api/<name>` + scoped keys · MCP tools ·
embeddable widget (origin-scoped key kind + declared CORS; the widget.js artifact itself is
a Distribution build target) · build-derived agent manifests (`openapi.json`, `llms.txt`) ·
**feeds** (RSS/Atom as build-derivation targets; premium feeds = tokenized-URL bindings) ·
gate: `oauth_provider_unsupported` (the business as IdP for its own API). CLI/SDK =
cross-reference to Distribution (package artifact wrapping REST).
- EXISTS: RUNTIME_RAILS + RailRoute + dispatcher + three auth tiers + role allowlist (never
  widened); gateway keys.
- DELTA: api/MCP RailRoute entries whose handlers are the business's own actions, selected
  via `runtime_features`; derivation targets emitted at build (new agent protocols = new
  derivation targets, never a second action surface).

### 2.6 Communication (channels)
Values: transactional email (EXISTS: `app_email.py`, Postmark, test-mode suppression with
receipts) · broadcast/marketing email (consent ledger: double opt-in, suppression,
one-click unsubscribe — attached as a mandatory obligation) · push (one rail, two
transports: Expo + web-push VAPID, metered per send) · gates: `sms_unconfigured` (consent +
quiet hours), voice/IVR (refused until separately gated), u2u messaging (hard-depends on
moderation + realtime gates). In-app inbox and alert fan-out are **compositions, no
primitive** (records + scheduled/webhook actions over the channel rails; each channel
discharges its own cost gate; throttle via `rate_limit.py`).

### 2.7 Distribution (publish surfaces)
Values: pointer_static web (EXISTS: immutable build id + pointer-last publish + R2 edge —
the integrity spine never forks) · `ssg_indexable` build (prerender, sitemaps, schema.org,
**programmatic page factory** reading the per-business schema data→pages, GSC
index-coverage probe — the single biggest brief gap: AngelMatch's 150K-page moat) · `pwa`
flag on the web value (build-derived manifest + service worker, riding web-push) · OTA
channel · store_release family (Chrome extension FIRST as the cheap prover, then iOS via
Expo/EAS per readmodular, desktop/packages per archetypes) · package registries ·
third-party marketplace/directory listings · gates: `custom_domain_unconfigured` (residual
spec'd — Cloudflare-for-SaaS + hostname map in the R2 worker — but pulled from near-term
ordering; no live business needs it before white-label), `white_label_unsupported` (names
its prerequisites: org principal + custom domain + reseller billing attribution),
local-first/CRDT sync.
- DELTA: **BuildStep protocol + `publish_adapter` dispatch** keyed from the manifest
  (mod-plan Stage 6 seam) — the one seam every distribution value enters through.

### 2.8 Acquisition & growth (declared in-scope — where all three briefs actually win)
Values: organic channel publishers — the manifest key's registry **is** the existing
CHANNEL_REGISTRY (X/Reddit, credit-gated, receipted) · paid ads (meta_ads_v2 class;
cost_gate = `business_ad_spend` policy + creative-credit staging; live spend stays separate
USD budget authority per CLAUDE.md) · lead magnets (public bindings + business-funded
acquisition budget + rate rail) · programmatic SEO (= `ssg_indexable`, cross-ref) ·
affiliate programs run ON third-party platforms (= provider connection + egress —
expressible today); NATIVE payout-bearing affiliate/referral programs route to the payouts
gate. Trials/referral grants: see Money (business-funded acquisition grants).

### 2.9 Money (direction × shape × mechanics)
`subuser-billing-plan.md` remains the detail doc, amended by this audit:
- IN shapes: subscription (monthly-only) · credit_packs (persistent grants) · **order**
  (one-time; capture policy immediate|manual≤7d|deposit+balance; returns/dispute state
  machine; NEVER an `app_plan_policies` row; **mandatory tax-posture obligation** — Stripe
  Tax computation + collected-vs-MoR stance; compose refuses order-shape and cross-border
  composition without it) · cogs_passthrough · store_iap (provider-labeled cut) ·
  **quantity billing with a declared `quantity_source`** (org members | projects | brands |
  client workspaces — Bazzly bills per project, Peekaboo per brand; same `per_unit` compose
  path, synced to Stripe subscription_items, proration previewed via the quote path).
- Allowances: period grant · persistent grant · declared units · exhaustion
  (402|upgrade|topup|auto_topup) · plan-declared rpm/day rate tiers — **effective limit =
  min(plan-entitled rate, platform abuse policy)**; one `rate_limit.py` enforcement point.
- **Acquisition mechanics (ALL business-funded)**: time-boxed trials · margin-checked
  discounts (plan_key-v2 mint) · referral grants · lead-magnet budgets — a SECOND
  SECURITY-DEFINER mint source drawing on the business's own settled funding ledger,
  declared + bounded `{days, card_required, allowance}`. **"Nothing free" holds as "nothing
  UNFUNDED": every unit of value traces to a settled payment — the customer's or the
  business's. The payer is generalized, never faked.** (All three briefs run trials the
  previous chain refused — this was the audit's most urgent correction.)
- **Gifts are funded**: payer ≠ beneficiary attribution on the order shape and grant mint;
  the unfunded_value refusal narrows to UNfunded comps only.
- External platform fees (Shopify/Apple/EAS) compose as `PricedComponent(fixed)`;
  per-transaction % flagged as the future third CostBasis kind.
- OUT: refunds (EXISTS) · gates: `payouts_unsupported` (Connect Express + payout ledger +
  KYC/tax; take-rate and processor-held escrow ride it), `external_revenue_unsupported`
  (ad/affiliate postback reconcilers + CMP/disclosure obligations),
  `invoicing_unsupported` (net-terms/custom-quote), `offline_license_unsupported` (signed
  entitlement tokens + activation ledger; online phone-home licensing on existing
  sessions/keys is the sanctioned v1 so the extension prover isn't blocked),
  `interval_unsupported` (annual/lifetime — monthly-only stands).

### 2.10 Policy & Governance (refusals and obligations AS gates, never prose)
- EXISTS: money-shape gate; operator_approvals (single-consume, TTL); rate limiter; SSRF
  guards + source scanner; role boundary; `businesses.mode` test mode (same path, gated
  side effects with receipts).
- DELTA (legal-before-scale): **DSR rail** (GDPR/CCPA export + receipted erasure keyed
  `app_user_id`, driven by `pii_manifest`, always-on — legally mandatory for businesses the
  rails ship TODAY); **UGC trust rail** (scan-on-ingest, report/takedown queue, subuser
  suspend/ban with entitlement freeze; composition edge `moderation_required:<surface>` on
  UGC-bearing rails — ships before social/marketplace archetypes); **brief-time capability
  screen** — one deterministic deny rail carrying the refused list (§3) as live gate
  errors; **provenance ledger** on ingestion (source/ToS/license/robots + freshness
  receipts; dataset-sale and SEO factories REQUIRE it); **cold-outreach posture pack**
  (sender identification + working opt-out + suppression ledger + declared pacing + dedup)
  mandatory on mailbox-connector sends and client-runner deliveries — consent ledgers can't
  exist for cold outreach, so this obligation replaces them; disclosure packs as build
  gates (FTC affiliate, unsubscribe footer, not-financial-advice, CMP on ad-monetized
  surfaces); regulated posture packs (encryption/retention/BAA) that bank/health/minors
  connections require; gate `resource_sharing_unsupported` (per-record ACL/ReBAC — named
  shape, so collaboration briefs fail closed instead of building leaky sharing).
- **Platform obligations (non-declarable):** DSR, CSAM scanning, abuse velocity control,
  tax/MoR posture maintenance, single-region residency (residency guarantees = gate).

### 2.11 Observation
- EXISTS: receipts convention; always-on Umami rail; append-only usage events;
  pulse/status; showcase metrics flagged in metadata.
- DELTA: two-plane cost report (customer cost ≤ paid × (1−margin) AND shared cost ≤
  business funding) emitted from `compose_plan`'s own arithmetic; **declared third-party
  script allowlist lives as metadata ON the analytics rail row, and that row's
  worker_contract text derives from it** (one row owns both the permission and the
  prohibition — resolves the landed "no other trackers" contract conflict; widened from
  analytics tags to declared third-party scripts, which is also where support widgets
  live); org audit logs (append-only records, once org lands); account-health rail per
  gatekeeper provider (Apple/CWS/POD/affiliate standing → pulse + CEO wake).
- Customer-facing analytics = documented **composition** (ingested schema + scheduled
  compute + bindings) — explicitly not Umami, no new rail. In-product support = documented
  composition (records ticket queue + email + generate rail + conversation-followup;
  support-tier SLAs are plan feature gates).

## 3. Refused (registered as live gates at the brief screen — a general platform is defined
as much by what it refuses)

`inauthentic_engagement` (purchased upvotes/reviews, pooled aged/sockpuppet accounts,
astroturfing, ban-evasion — Bazzly's high-karma network and smart upvotes; mechanically
expressible on planned rails, which is exactly why the refusal must be a live gate at BOTH
the brief screen and channel/egress deny) · `bot_evading_scrape` — **the ruling that splits
the browser worker**: render-own-content is allowed; robots/ToS-permitted third-party
capture is gate-openable WITH provenance receipts; bot-protected/ToS-prohibited capture
(Google AI-surface scraping — Peekaboo's stated moat) is **refused-as-such, not deferred**;
a Peekaboo-class clone is buildable minus that moat and the gate error says exactly that ·
`regulated_money_custody` (wallets, stored value, platform escrow; processor-held routes to
the payouts gate) · `gambling_wagering` · `csam_and_categorical` (+ POD regulated
categories, trademark/IP infringement) · `regulated_verticals_without_posture`
(health/minors/banking until the named posture pack exists) · `unfunded_value` (perpetual
free plans and unfunded comps — the two funded substitutes are named in the gate error:
business-funded bounded trials; business-funded lead magnets + rate rail) ·
`unlicensed_data_resale` (no provenance = no dataset sale) ·
`inventory_commerce_unsupported` (stock-holding/dropship/3PL — gate points at the POD
provider-held substitute) · `physical_ops_and_realtime_atoms` (IoT fleets, gig dispatch,
realtime multiplayer/AR) · voice/IVR (until separately gated) · annual/lifetime intervals.

## 4. Ordered deltas

1. **Formalism**: manifest over the surface contract; ONE composition checker (calls
   compose_plan/money_shape — no arithmetic of its own); gates as registry rows; brief-time
   capability screen carrying §3.
2. **Constraint retrofit at the checker**: cost_gate-iff-priced generalized from
   MissingMoneyGate to channels + spendful job kinds/routes; obligation edges; auth_tier
   enum extension (org/api_key/service).
3. **Money A** — subuser-billing WS1–4 exactly as written (unit + monthly window on the one
   gate; persistent grant funded-only + re-rate; plans as declarations; attribution).
4. **Money B** — quote/autopilot envelope; **acquisition grants (trials/referral/lead-magnet
   — the most urgent correction)**; gift beneficiary attribution; order shape + capture
   policy + returns machine + **tax obligation**; `quantity_source` billing; external-fee
   COGS; rate tiers = min(plan, platform).
5. **Governance minimums (legal-before-scale)**: `pii_manifest` + DSR rail; UGC trust rail
   + moderation edge; provenance ledger skeleton; cold-outreach posture pack.
6. **Connections**: generic egress + `ctx.egress`; NEW `provider_connections` leaf +
   business consent; inbound hooks; then per-customer OAuth vault + refresh; then
   `webhook_out`.
7. **Data + compute**: per-business schema + `ctx.db` + `business_db_migrate` (+ pgvector +
   embedding pricing); blob/MIME + signed URLs; ingestion job kind; webhook|queue triggers;
   render-own-content worker.
8. **Principals**: org/RBAC + quantity-source sync; anonymous/device + merge;
   auth_assurance/MFA.
9. **Bindings**: api/<name> + MCP entries; SSE v0; build-derived openapi/llms.txt/feeds;
   embed artifact + origin-scoped keys.
10. **Distribution**: BuildStep + publish_adapter seam; `ssg_indexable` + page factory +
    PWA flag; then Chrome-extension adapter as the store_release prover; mobile per its own
    plan. (Custom domain stays gated.)
11. **Communication + acquisition**: broadcast lane + consent ledger; push (two
    transports); acquisition manifest key over CHANNEL_REGISTRY + ads.
12. **Gate-opened residuals, only when hit, in dependency order**: third-party-scrape
    worker (provenance-ruled) · realtime pub/sub · payouts/Connect · external-revenue
    reconcilers · SMS · event store · posture packs (then bank_data) · white-label (after
    org + domains + reseller attribution) · delegated client-runner (act-as-customer on
    their own device, platform stays secretless) · invoicing · SSO/SCIM · resource sharing
    · offline licensing · sandbox keys · customer-code plane · workflow-orchestration tier.

## 5. Merge rules (blocking, all phases)

1. One gate, one pricing SSOT, one action runtime, one dispatcher, one credential-connections
   table (`provider_connections`), one publish spine. The checker never re-implements money
   arithmetic.
2. New capability = registry/manifest entry + keyed handler; a copied branch is the wrong
   altitude. Every capability has exactly ONE authoritative manifest key.
3. No spendable value without a settled payment (customer's or business's — generalized
   payer, never faked); no ungated paid capability; obligations discharge at compose or the
   composition refuses.
4. Every refusal/deferral in this doc EXISTS as a runtime gate error the CEO can discover —
   a policy that lives only in a planning doc does not exist.
5. Migrations numbered at ship time, additive/nullable, via `takyon migrate` before restart.
   Nothing lands before its phase's doc corrections are folded.

## 6. Acceptance

Fresh business, browser, zero-shot, both hosts deployed, per phase — and for the whole
plan: a white-hat Peekaboo-class clone declared as a manifest (per-customer OAuth
connection, funded daily scan ingesting into the per-business schema with provenance
receipts, metered-quota tiers with a business-funded trial, scoped API keys + MCP, SSG
public pages) where a trial customer converts, a paying customer exhausts quota → clean 402
with the declared CTA → continues, a scheduled on-behalf run debits the right customer, a
DSR export/erase round-trips, and the two-plane cost report proves customer cost ≤ paid ×
(1−margin) AND shared cost ≤ business funding. Existing-business poking is exploration, not
acceptance.
