# Takyon Business Archetypes Plan

**Extending the platform from web micro-SaaS to mobile apps (autonomous App Store publishing), physical-product commerce (POD/Shopify), and a general archetype system.**

Status: PLAN ONLY — nothing implemented. Grounded in a full inventory of the active trunk (`hermes-agent-main/`) taken 2026-07-01; all file:line refs below were verified against the current tree.

> **SUBORDINATE PLAN (operator ruling, 2026-07-02):** `takyon-modularization-plan.md` is the **authoritative** plan; this document is subordinate design material and must be revised against it before implementation. Where they touch, the modularization plan's rulings govern — specifically: (1) **subuser plans are monthly-only** — the "existing Stripe one-time" checkout referenced for digital products (§4) and physical commerce (§3.3) must ride the **order money shape** (`app_orders.py` direction), not `one_time` plan rows, whose checkout branch is being deleted; (2) the **Shopify connection is Composio-canonical** (the modularization plan's UC4 ships `business_connect_shopify` v0 — connection + plan-fee read + `shop/update` webhook; P3 extends that same tool; the direct `shopify` safebox alias in §3.5/§8 is a fallback only); (3) pricing composes on UC4's `PricedComponent`/`MarginPolicy`/`money_shape` engine — the archetype registry (§1.1) subsumes `money_shape` when it lands, and the shape-change approval affordance is built once to §1.5's `operator_approvals` spec, under the modularization plan's Stage 5.

---

## 0. Where the repo actually is today (verified findings)

The platform is one hardcoded archetype, end to end:

| Surface | Current state | Ref |
|---|---|---|
| Business "kind" | **Does not exist.** `businesses` table = slug/name/goal/status/mode/work_focus/budget/metadata — no type column. Surface contract schema has no platform field. | `core.py:14231-14243`, `core.py:33787-33809` |
| Closest seam | `_surface_contract_kind()` derives `{landing_only, app_like}` heuristically; `_surface_allows_landing_only` is hardcoded `return False` — a dead product-kind branch | `core.py:10411`, `core.py:10366` |
| Build pipeline | Hard-pinned: pinned Vite scaffold only; Next/AppKit and raw static explicitly rejected; vite build → tsc gate → runtime-authority scanner | `core.py:11508-11777` |
| Publish | Immutable `build_id = sha256(...)[:32]` → R2 `<slug>/<build_id>/` → `<slug>/current` pointer last → `live_build_id` on contract; `publish_mode: pointer_static` | `core.py:13400-13564`, `storage.py:1099-1136` |
| Serving | CF worker: static from R2 (no origin fallback), `/api/*` → subuser edge `:9119` | `deploy/cloudflare/product-worker/worker.js:94-194` |
| Rails | 16 rails in `PRODUCT_RUNTIME_RAILS`, all web-product, all owned by `takyon-app-runtime`/`takyon-app-analytics`; no archetype dimension | `core.py:300-502` |
| Money | Three shapes: operator control-plane (control_api.py), per-business usage reserve→settle→release (app_usage.py:512/586/697), creative credits reserve→commit→release (business_credits.py:248/274/315); pricing fail-closed (usage_pricing.py) | verified |
| Subuser payments | Stripe only; `record_webhook_and_process(provider="stripe")` already provider-parameterized — the one embryonic multi-provider seam | `app_payments.py:368` |
| Approval | **No operator-approval primitive exists.** No `/approve` control, no pending-approval table; ceo.md treats "waiting for approval" as an anti-pattern. Posture: allocated credits ARE the approval | settings.json controls, ceo.md:16,32 |
| Bootstrap | Web-shaped step ladder lives in `_business_bootstrap_instruction()` (`cli.py:1918-2109`), not in ceo.md; ceo.md defers method choice to the skills index | verified |
| Filesystem contract | Four roots only (`product/ distribution/ research/ metrics/`); ceo.md line 26 forbids new top-level roots | ceo.md:19-26 |
| Mobile/commerce concepts | Zero. No hits for expo/capacitor/fastlane/printful/fulfillment/inventory; Shopify exists only as an optional non-active curl skill doc + a cosmetic idea string | grepped |

Two structural conclusions fall out:

1. **The subuser plane is already portable.** Auth/session, records, actions, checkout, entitlements, usage, generate are plain HTTP rails terminating at subuser `:9119`. A mobile client or a commerce storefront can consume the *same* rails — no second backend plane is needed. That is the single biggest parsimony win available.
2. **Everything else archetype-shaped is concentrated in five choke points**: the surface contract schema, `_refresh_product_surface_path` (build gates), `_publish_product_surface_path` (publish adapter), `PRODUCT_RUNTIME_RAILS` (rail registry), and the bootstrap ladder in `cli.py`. The archetype system is a dispatch dimension threaded through exactly those five places — not a parallel trunk.

---

## 1. Phase 0 — The archetype spine (prerequisite for everything)

One canonical registry, one stored field, one dispatch dimension. No second path per archetype.

### 1.1 Canonical archetype registry

New leaf `hermes-agent-main/plugins/takyon/archetypes.py` (imported by core.py; same pattern as `PRODUCT_RUNTIME_RAILS`):

```python
BUSINESS_ARCHETYPES = {
  "web_saas": {          # today's behavior, byte-for-byte
    "scaffold": "subuser_app_kit/scaffold",
    "build_kind": "node_build",            # vite + tsc + scanner (unchanged)
    "publish_adapter": "pointer_static",   # R2 + current pointer (unchanged)
    "rails": <current 16>,
    "verify_gate": "browser_e2e",
    "requires_api": [],                    # beyond existing
    "approval_gates": [],                  # fully autonomous, as today
  },
  "mobile_app": { ... },                   # §2
  "physical_commerce": { ... },            # §3
  "digital_product": { ... },              # §4 (near-free win)
  "browser_extension": { ... },            # §4 (pipeline prover)
}
```

Each entry declares: scaffold path, build-gate chain, publish adapter name, applicable rail keys, verification gate, required safebox aliases, money-gate bindings, and which guarded actions require operator approval. **The registry is the only place archetype capability is enumerated** — skills, tools, bootstrap, and the CEO discover it from here (mirrored into tool schemas and skill frontmatter readiness, never duplicated in prompts).

### 1.2 Stored kind

- `businesses.archetype TEXT NOT NULL DEFAULT 'web_saas'` — migration backfills every existing row; zero behavior change.
- Surface contract gains `archetype` (validated against the registry), **anchored on first publish exactly like `source_path`** (`core.py:33820` precedent) so a business cannot silently mutate shape.
- `/create` gains `--kind <archetype>` (default `web_saas`) — a flag on the one creation command, per the parsimony rule; no new slash commands. `_parse_business_start_args` (`cli.py:1398`) is the insertion point.
- The dead `_surface_contract_kind` heuristic gets subsumed: `app_like`/`landing_only` become derived attributes *within* `web_saas`, and `_surface_allows_landing_only` stays false.

### 1.3 Rails become archetype-scoped

Extend each `PRODUCT_RUNTIME_RAILS` entry with `archetypes: (...)` (absent = `("web_saas",)` … actually default to *all* for genuinely shared rails: `auth`, `account`, `profile`, `records`, `actions`, `media`, `email`, `checkout`, `entitlements`, `usage`, `generate`, `search`, `analytics` are shared; `directory`/`connections` stay web+mobile). New rails (`store_iap`, `catalog`, `cart`, `orders`, `fulfillment`, `push`, `deep_links`, `digital_delivery`) enter the **same registry** with owner skills — never a second per-archetype list (CLAUDE.md rail rule). `_canonical_runtime_features_for_surface_shape` (`core.py:1736`) filters by the business archetype; declaring a rail outside your archetype is a validation error with an exact gate message.

### 1.4 Publish adapters

`_publish_product_surface_path` (`core.py:13400`) becomes a dispatch on the registry's `publish_adapter`:

- `pointer_static` — today's R2 flow, untouched.
- `store_release` — mobile: artifact = signed .ipa/.aab + store metadata bundle; "live pointer" = store version + OTA channel state (§2.4).
- `commerce_catalog` — physical: publish = storefront static (reuses `pointer_static`!) **plus** catalog sync receipt (§3.4).

Invariants preserved for every adapter: immutable content-addressed build id, pointer-written-last, receipt path on the contract, `live_probe_status`. The publish *semantics* generalize; the *integrity model* does not fork.

### 1.5 The operator approval rail — the "one human click" as a first-class primitive

This is the most important net-new platform primitive, and it must be built once, generically — not as an App-Store-special:

- **Control plane:** new table `operator_approvals` (business_slug, action_kind, payload_digest, status `pending|approved|denied|expired`, requested_at, decided_at, actor, expiry, receipt_path). Idempotent on (business, action_kind, payload_digest).
- **Tools:** `business_request_operator_approval` (creates/returns pending record + notifies) and an internal `require_approval(action_kind, payload)` check that guarded tools call **before** the side effect — approved receipt present → proceed and consume it; absent → fail closed with the exact gate error (`approval_required:<action_kind>`), which is itself the CEO's discovery surface.
- **Operator affordances:** `/approve` + `/deny` p0_control commands in `harness/settings.json` (source-of-truth driven, per the slash-command rule); an Approve button in the dashboard (`web/src/litebulb/product/CompanyTab.tsx` status-pill area) and a TUI prompt (`ui-tui/src/components/prompts.tsx` already has the approval-prompt component family); push notification so the click actually happens promptly.
- **Consumers (declared per-archetype in the registry):** App Store production submission (§2.5), first physical sample order and first live customer order of a new SKU (§3.5), and later anything the operator wants gated (e.g. ad spend above a threshold). Web SaaS keeps zero approval gates — no regression to today's autonomy.
- **Semantics:** approval is scoped to one payload digest (approving *this* release, not "releases"), TTL-bounded, single-consume, receipted. This keeps ceo.md's anti-stall posture intact: the CEO never "waits around" — it requests approval, records the blocker truthfully, and continues other work; the wake loop resumes the gated action when the receipt exists.

### 1.6 Bootstrap ladder parameterization

`_business_bootstrap_instruction()` (`cli.py:1918-2109`) becomes one ladder with archetype-injected steps, not N copies: steps 1 (brief), 3 (research), 4 (X seed) are archetype-invariant; step 2 (surface contract + first build) pulls scaffold, routes, runtime_features, and worker guidance skills from the registry. ceo.md needs at most one new line ("the business archetype is on the business record; skills declare which archetypes they serve") — routing stays index-driven, per its existing rule 2.

### 1.7 Verification gates per archetype

The "fresh business E2E through the real UI" acceptance rule generalizes: `web_saas` → browser E2E (unchanged); `mobile_app` → simulator/emulator flow run (Maestro) + TestFlight install check; `physical_commerce` → sandbox order round-trip (test-card payment → provider draft order → webhook reconcile → receipts). Each gate is declared in the registry and enforced pre-publish by the same `_product_publish_readiness_blocker` seam (`core.py:11777`).

**Phase 0 effort: ~2-3 weeks.** It is almost entirely refactor-in-place with `web_saas` as the identity case; existing businesses must be bit-identical before/after (regression: re-publish an existing business, assert same build_id).

---

## 2. Mobile apps — autonomous App Store publishing, one human click

### 2.1 Tech decision: Expo/EAS default, tiered native escape hatches, explicit refuse-list

*(This section was adversarially re-reviewed on 2026-07-01 against the "did we just pick what fits our TS/React architecture?" challenge — four-perspective panel with web verification. The Expo default survived; several claims were corrected and the tier/refuse-list structure below was added as a result.)*

**What these apps actually are:** React Native renders real native views (`UIView`/Jetpack primitives) via the Fabric renderer — the New Architecture is mandatory since RN 0.82 (Oct 2025, bridge removed). These are the same class of app as Discord, Shopify, and Bluesky (RN is primary in ~12.6% of top-500 US apps), not webview shells. Capacitor **was** the webview option and stays rejected: wrapping the Vite SPA is exactly what Apple Guideline **4.2 (minimum functionality)** targets. RN is categorically not what 4.2 hits.

**Why Expo/EAS over generating native SwiftUI apps — the honest ranking:**

1. **OTA vs review-gated iteration (decisive, and structural — would hold greenfield).** A compiled Swift app has no hot-patch path: every iteration goes through App Store review (~1.5-day median in 2026). expo-updates ships JS/asset changes review-free. For a factory whose core loop is continuous product iteration, Swift-default is a 5-10× throughput cut — disqualifying.
2. **One codebase → both stores.** Pure Swift = zero Android; a second Kotlin codebase breaks the one-agent premise. Android-first rollout requires cross-platform.
3. **Agent codegen competence + compile-loop cost — this is the architecture-fit factor, named honestly.** Claude-class agents are strongest in TS/React; Swift is a lower-resource language for LLM codegen, and the compile-feedback loop is cloud-macOS-minutes vs local `tsc` seconds. Legitimate operational reason, but it is convenience, not an app-quality argument — and it did partially shape this choice.

Note what is *not* a reason: headless publishing (fastlane + ASC API makes Swift headless too — both stacks beat "human at a Mac"), and review outcomes (Apple does not penalize the RN framework; no such claim is load-bearing here).

**The tier structure (adopt as the archetype's internal shape):**

- **Tier 0 — Expo managed + OTA:** app core (screens, logic, AI via `/generate`, subscriptions). The common case; stays fully inside agent competence.
- **Tier 1 — Swift extension targets inside the Expo app (ROUTINE, not exceptional):** WidgetKit widgets, Live Activities, App Intents/Siri, share/keyboard extensions are buildable via `expo-apple-targets` config plugins — **but the extension code is hand-written Swift** the worker must generate and EAS must compile. Takyon's bread-and-butter archetypes (trackers, planners, health-lite) *want* widgets/Live Activities to be competitive, so this is budgeted as a first-class rail with its own E2E canary — autonomous mobile builds fail first exactly at prebuild/config-plugin correctness. This is the standing **"Swift-extension tax"**: premium native features are additive Swift work, never free.
- **Tier 2 — `native_swift` archetype variant (gated, rare):** full SwiftUI for a genuine flagship (animation-heavy, deep-OS). Feasible headlessly (Xcode Cloud/fastlane) but loses OTA — only on explicit operator request.
- **Refuse-list (hard gate in the registry, like the regulated-category rail):** animation-flagship/sub-frame-timing apps, standalone Apple Watch, CarPlay, real-time games/AR. RN cannot reach Swift parity here (RN New-Arch still has acknowledged animation regressions; SwiftUI gets each new iOS design language — e.g. Liquid Glass — on recompile while RN permanently lags via wrapper libraries). The factory declines these rather than shipping them half-native.

**OTA, stated narrowly (corrected from the first draft):** expo-updates covers JS/asset changes only. New native modules, permission/manifest changes, SDK bumps, and *substantive feature additions* still require a store release — and Apple's 2026 enforcement against runtime-self-modifying apps means an OTA-happy factory can itself trip §2.5.2. The `business_push_ota_update` tool therefore classifies each update **fix-vs-feature** (diff-scope gate: no new permissions, no new native deps, bounded surface delta) and escalates feature-class changes to the release lane instead of shipping them OTA.

Fallback noted: if EAS pricing/lock-in becomes a problem, the same pipeline runs on fastlane + a self-hosted Mac runner with `match` for signing; the tool contracts below don't change, only the executor.

### 2.2 Accounts, signing, and the honest constraint map

| Constraint | Reality | Plan |
|---|---|---|
| Apple Developer Program | $99/yr recurring, legal entity + D-U-N-S, human enrollment (3-5+ business days); **Paid Apps Agreement re-acceptance is Account-Holder-click-only and recurs whenever Apple updates terms — while stale it hard-blocks ALL new app/IAP creation account-wide**; banking/tax forms also human-only | One platform org account initially — legitimate while every business is owned by the same legal entity (the current model). **Publishing apps for *distinct* third-party businesses from one account violates Guideline 5.2**, so per-Takyon-user Apple accounts become mandatory (not just prudent) the day users legally own their businesses. Add an **account-health check** (agreements current, membership not lapsing) to pulse/doctor with push notification — a headless fleet otherwise stalls silently. |
| App Store Connect auth | ASC API key (.p8 + key id + issuer id) does app creation, TestFlight, metadata, submission — fully headless | Safebox alias `app_store_connect`; resolved only in the authority route, passed explicitly into the pinned EAS/fastlane subprocess env (GOAL_RULES §7 pattern — the CLI needs env vars, so the authority route constructs that env explicitly; the business runtime never sees the key). |
| Signing | Distribution cert + provisioning profiles | EAS-managed credentials (EAS holds them, scoped per app) — no cert material in our runtime at all. Safebox alias `expo` for the EAS token. |
| **Apple 4.3(a) spam / app-farm risk** | **The #1 strategic risk.** Many templated apps from one account → rejections, then account termination — blast radius is *every* business's app | Three mitigations, all rails: (a) per-account submission quota (registry policy, e.g. ≤2 production submissions/week initially); (b) a differentiation gate before submission — the app must diverge from the scaffold beyond threshold (screens, native modules, icon/brand), enforced like the runtime-authority scanner; (c) architecture keeps per-user accounts pluggable (account credentials are a per-business-resolvable safebox lookup, not a global). |
| Review latency | 24-48h typical, human reviewers, rejections happen | Review state poller (cron rail) → rejection reasons land as a receipt + CEO wake. Metadata-only rejections auto-fix and resubmit under the same approved release intent (bounded, e.g. 3 attempts); binary changes require a fresh approval. |
| Google Play | $25 one-time; org account (D-U-N-S + legal entity) is **exempt** from the 12-tester/14-day closed-testing gate that blocks personal accounts — must use org. **The Play Developer API has no `apps.create`**: the first app record + first AAB upload per app are Console-click-only; everything after (tracks, releases, rollouts, listings, IAP) is headless via the service-account JSON | Safebox alias `google_play`. **Android first** stays right, corrected claim: per app it's *one human Console session at first release*, then zero clicks forever (the Edits API + EAS Submit handle all subsequent releases). Same third-party caveat as Apple: Play ToS prohibit using the API to publish on behalf of distinct third parties — fine under single-entity ownership, forces per-user accounts later. |
| IAP mandate | Apple 3.1.1: digital goods must use IAP (15-30% cut); Stripe-in-app is not viable as the default plan | New `store_iap` rail (§2.6). US link-out entitlement exists post-Epic but treat as optimization, not architecture. |

### 2.3 Scaffold and rails reuse

New pinned scaffold `plugins/takyon/mobile_app_kit/scaffold/` — Expo + TypeScript, pinned versions, with a mobile runtime client that speaks to **the existing subuser rails** (`https://<slug>.coscale.app/api/*`): Supabase auth (magic link → universal links / app links — new `deep_links` rail carries the associated-domains config), records, actions, generate, usage, entitlements. The product backend does not fork. New mobile-only rails in the registry:

- `push` — APNs/FCM via Expo push service; server-side send goes through a guarded tool (metered request_cost), never raw tokens in product code.
- `deep_links` — universal-link association files served from the existing web publish (apple-app-site-association lands in the R2 static publish — the web and mobile archetypes compose).
- `store_iap` — §2.6.

Mobile source lives at `product/mobile/` — inside the existing `product/` root, so ceo.md's four-root contract (line 26) is untouched. A business may hold both `product/site` and `product/mobile` (the normal case: web landing + store listing + app).

### 2.4 Build → publish pipeline (`store_release` adapter)

Mirrors the web gate chain, same order, same fail-closed posture:

1. Containment + pinned-Expo-scaffold check (analog of `core.py:11608`).
2. Runtime-authority scanner over the RN source — same forbidden-provider/env-read scan; a mobile app must broker paid AI through `/generate` exactly like web.
3. `tsc --noEmit` gate.
4. **Local fast gate:** `expo prebuild --no-install` + bundle compile (catches most breakage in seconds, free).
5. **EAS Build** (profile per lane: `preview` for internal, `production` for release). Build id + git-digest hash = the immutable artifact identity; .ipa/.aab stored via the existing `storage.write_build_artifact` path under the same content-addressed prefix scheme.
6. **Simulator verification gate:** Maestro flows (boot, auth round-trip, core workflow, IAP sandbox purchase, no crash) on the built artifact. This is the mobile "browser E2E" and it gates submission.
7. Publish lanes, in escalating autonomy cost:
   - **OTA update (expo-updates channel per business):** JS/asset-only changes, passing the fix-vs-feature diff-scope gate (§2.1). Fully autonomous, no review, no click. This is the default iterate loop and maps 1:1 to today's `business_refresh_product_surface` semantics. Feature-class or native-touching changes are refused here and escalate to the release lane.
   - **TestFlight internal / Play internal track:** autonomous (no Apple review for internal TestFlight).
   - **Production store submission:** requires an `operator_approvals` receipt (§1.5). Post-approval, release is automatic on approval — approve is the *single* human action ("one button").

### 2.5 Tools and skill

- Tools (all in core.py + plugin.yaml, all fail-closed, all receipted): `business_publish_mobile_release` (build+verify+TestFlight; **creative-credit gated** — a release is a fixed operator-priced action; exact EAS cost recorded from the EAS API into the credit entry metadata per the credit rules), `business_push_ota_update` (usage-rail metered, cheap, autonomous), `business_submit_store_review` (approval-gated; ASC/Play submission + metadata + screenshots), `business_read_store_status` (review state, ratings, crash summary — the CEO's evidence surface).
- Pricing entries in `agent/usage_pricing.py`: `("eas","build_ios")`, `("eas","build_android")`, `("eas","update")`, `("expo","push")`, `("maestro","flow_run")` — unpriced = refused, per the existing per-request provider pattern (`usage_pricing.py:93-141`).
- Skill: `skills/takyon/takyon-mobile-product/SKILL.md` (from SKILL-TEMPLATE, full frontmatter + `requires_tools` gating on the four tools above). It owns the mobile *method*: when an idea warrants an app vs web-only, release-train discipline, OTA-vs-binary decision rule, store-listing craft, review-rejection playbook. `takyon-app-runtime` keeps ownership of `store_iap` (it's payments). Store metadata/screenshots generation reuses the Claude worker lane with a mobile contract block added to `build_worker_instruction` (`core.py:32316`) — a new `_mobile_app_worker_contract_block` alongside the existing subuser-app blocks.

### 2.6 IAP money rail (`store_iap`)

- Client: StoreKit 2 / Play Billing via `react-native-purchases` (RevenueCat) or expo-iap; server truth via **App Store Server Notifications V2 + Play RTDN** webhooks.
- Server: extend `app_payments.py` — `record_webhook_and_process(provider="apple"|"google")` (the dispatch at `app_payments.py:368` is already provider-parameterized; add `_process_apple_notification` / `_process_play_rtdn` mapping to the *same* checkout-intent → subscription → entitlement machinery, renewals via the invoice-paid analog, refunds via the reversal analog). One entitlement spine: a subuser authenticated by Supabase gets identical entitlements whether they paid via Stripe on web or IAP on mobile.
- Payout math: `_owner_payout_split` (`app_payments.py:193`) gains a provider-aware gross basis (Apple/Google already took 15-30% upstream; the platform fee applies to net-of-store proceeds, and revenue receipts must label the store cut explicitly — no silent margin illusions in metrics).

### 2.7 Mobile milestones

- **M0** (after Phase 0): Expo scaffold + local gates + Maestro sim verify; no store. ~2 wks.
- **M1**: Android end-to-end autonomous — org Play account, service account key in safebox, internal track publish. Zero human clicks *after* a one-time per-app Console bootstrap (create app record + upload first AAB — Play has no `apps.create` API). Proves the whole pipeline cheaply. ~2 wks.
- **M2**: iOS — ASC key, EAS credentials, TestFlight autonomous, approval rail wired, one-click production submit, review-state poller + rejection wake. ~2-3 wks.
- **M3**: `store_iap` rail + reconciliation + payout basis; OTA iterate loop as the CEO's default; push + deep-link rails. ~2-3 wks.

---

## 3. Physical products — POD-first commerce, Shopify as a variant

### 3.1 The autonomy ladder (honest physics)

1. **Print-on-demand (Printful primary; Printify/Gelato later)** — full API surface: catalog blueprints → create product from print file → mockups → order → provider prints & ships → tracking webhooks. Zero inventory, zero capital, no human in fulfillment. **The only genuinely autonomous physical tier — this is the v1 archetype.**
2. Dropshipping (CJ/Zendrop) — API-able but shipping-time/quality/chargeback risk is brand-toxic for autonomous operation. Deferred indefinitely.
3. Real inventory + 3PL — purchasing capital, MOQs, vendor relations: structurally human. If ever added it's an *operator-assisted* archetype, not autonomous. Out of scope.

**Regulated-category deny rail (about "dough.dough"):** if the reference is literal food (cookie dough), that is cottage-food/FDA/refrigerated-logistics territory — **not autonomously feasible and it must be refused, not attempted.** The catalog gate ships with a deny-list (food/beverage, cosmetics, supplements, children's-safety-regulated goods, weapons) plus an IP/trademark screen on designs (trademarked terms/logos in print files are the #1 POD account-ban cause). Both are build-time-style gates in the catalog authority route, same posture as the runtime-authority scanner.

### 3.2 Storefront decision: native spine first, Shopify as an opt-in variant

- **v1 — native storefront:** the storefront *is* the existing Vite SPA on the existing R2 edge. Physical commerce adds rails, not a new serving stack: `catalog`, `cart`, `orders` (+ `fulfillment` server-side). Checkout extends the existing Stripe rail to one-time PaymentIntents with shipping-address collection and Stripe Tax. No per-business $39/mo SaaS dependency, no OAuth-app sprawl, and the entire publish/verify/E2E machinery is reused unchanged. This is the parsimonious path and the default.
- **v2 — `physical_commerce:shopify` variant:** for businesses that need Shopify's ecosystem (Shop-app distribution, merchant apps, operator preference). Same rails semantics, different executor: catalog syncs via Admin API, checkout via Storefront API cart → Shopify checkout, our R2 site stays the storefront (headless). The variant is a registry flag + a `business_connect_shopify` tool (safebox alias `shopify`), not a fork of skills. Cost (per-store subscription) is surfaced as an explicit operator-approved fixed cost.

### 3.3 The third money shape: COGS pass-through (new, and it must be its own thing)

Physical orders introduce per-unit provider cost funded by the **customer's payment** — neither operator usage nor creative credits. Rules, encoded as rails:

- **Never place a provider fulfillment order before payment capture.** Order lifecycle is the reserve→settle pattern applied across two providers: Stripe capture → Printful order create (draft) → confirm → on provider failure, refund + release. Idempotent on order id at every step.
- **Margin gate at catalog time:** `list_price ≥ landed_cost(provider base + shipping estimate) × (1 + margin_floor)` enforced by `business_upsert_catalog_product` — negative-margin listings are impossible by construction, and price changes re-validate. Provider base costs come from the Printful catalog API at upsert time and are stored on the catalog row (repriced on provider cost-change webhooks).
- **New leaf `plugins/takyon/app_orders.py`** (sibling of app_payments.py, same discipline): orders, order_items, fulfillments, shipments, returns. Two webhook reconcilers: Stripe (payment truth) and Printful (fulfillment truth), converging on one order state machine — `paid → submitted → fulfilled → shipped → delivered | returned | refunded`. Chargeback handling extends the existing `_process_charge_reversal` (`app_payments.py:973`) to cancel unshipped fulfillments.
- **Operator-paid physical actions** (sample orders, product photography via the creative gateway) are **creative-credit** actions (fixed operator price, exact provider cost recorded) — consistent with the existing credit rules.
- Sales tax: Stripe Tax on the native path (automatic), Shopify Tax on the variant. Nexus registration is flagged to the operator as a compliance receipt, not silently assumed.

### 3.4 Pipeline (`commerce_catalog` adapter)

1. **Design:** existing creative gateway (credit-gated Gemini/gpt-image) → print files validated against provider blueprint specs (DPI/dimensions from the catalog API) → provider mockups (metered request_cost).
2. **Catalog:** `business_upsert_catalog_product` — deny-list + trademark screen + margin gate + blueprint validation; writes control-plane catalog rows + `product/catalog/` state files; syncs to provider (native: Printful sync product; shopify variant: Admin API).
3. **Storefront:** the Claude worker builds shop pages against a `catalog` rail contract block (product grid, PDP, cart) — same worker lane, one new contract block.
4. **Publish:** `pointer_static` for the site **plus** a catalog-sync receipt; the publish is not "passed" unless both site and catalog agree (drift = blocker, exact gate error).
5. **Verify (sandbox-order E2E):** test-mode Stripe payment → Printful **draft** order (drafts don't fulfill or charge) → both webhooks round-trip → order state machine reaches `submitted` → receipts. Runs on every fresh commerce business as the acceptance gate.
6. **Approval-rail consumers:** first sample order (physical goods to a real address) and, policy-optional, the first live customer-facing SKU activation per business.

### 3.5 Tools, skill, rails

- Rails added to the registry (archetype `physical_commerce`): `catalog`, `cart`, `orders` (customer order status/tracking pages), `fulfillment` (server-side webhook ingest; not customer-callable).
- Tools: `business_upsert_catalog_product`, `business_order_sample` (credit + approval gated), `business_read_orders`, `business_process_return` (refund + provider reorder/cancel), `business_connect_shopify` (v2).
- Safebox aliases: `printful` (v1), `printify`/`gelato`/`shopify` (later) — all registered in `_API_ENV_ALIASES`, all resolved only in authority routes, all `requires_api`-declared.
- Pricing entries: `("printful","mockup")`, `("printful","order")` (pass-through basis), sample-order credit price.
- Skill: `skills/takyon/takyon-commerce/SKILL.md` — owns product selection method (ties into takyon-market-research evidence), margin policy, SKU experimentation and kill rules (feeds the RL/lessons rails), returns/support policy. `takyon-app-runtime` keeps payments/orders rail ownership.

### 3.6 Commerce milestones

- **P1** (after Phase 0; parallelizable with mobile M1): native storefront rails + Stripe one-time checkout + Printful sandbox E2E on a test business. ~3 wks.
- **P2**: live path — margin gate, deny/trademark screens, sample approval, dual-webhook reconcile, returns/chargebacks, order-status rail. ~2-3 wks.
- **P3**: Shopify variant; second POD provider (Gelato for EU landed cost); CEO catalog-experimentation loop. ~2-3 wks.

---

## 4. The rest of the archetype space (feasibility matrix + sequencing insight)

| Archetype | Autonomy ceiling | New rails | Gatekeeper friction | Money shape | Lift |
|---|---|---|---|---|---|
| **Digital products** (templates, ebooks, prompts, assets) | Total | `digital_delivery` (signed download URLs off existing media rail) | None | Existing Stripe one-time | **Tiny — days.** Everything else already exists |
| **Browser extension** (Chrome Web Store) | Near-total (light review, $5 once, full publish API) | build variant of web scaffold | Low | License key via existing entitlements rail — already built | **Small — ~1-2 wks.** Ideal cheap prover of the store_release adapter before Apple |
| **API-as-a-product** | Total | public key surface + docs page | None | `app_gateway_keys.py` + usage metering **already exist** | Small |
| Mobile app | High (OTA) / gated (releases) | §2 | **Highest** (Apple) | IAP + credits | Large |
| Physical POD | High | §3 | Low (no review) but real-world risk | COGS pass-through | Large |
| Newsletter/content | High | subscription+archive on Postmark rail | None | Existing Stripe | Medium |
| Marketplace (two-sided) | Medium | payouts between subusers → per-subuser Stripe Connect | Money-transmission complexity | New | Heavy — later; note `directory`+`connections` rails are the embryo |
| Desktop app (Tauri) | High (notarytool is headless, reuses the ASC safebox alias!) | updater rail | Medium (notarization; MAS optional) | Existing | Medium — cheap add-on after mobile M2 |
| Services/local lead-gen | Low (humans/telephony) | Twilio etc. | n/a | n/a | Defer |

**Sequencing insight (recommendation, honoring the stated priorities):** ship **digital_product** and **browser_extension** inside Phase 0's wake — they cost days, validate the archetype registry/adapter seams with real second and third archetypes before any Apple dependency, and each is immediately revenue-capable. Then the priority tracks: **mobile M1 (Android) → commerce P1** can run in parallel (different rails, different providers), then M2/P2, then M3/P3.

---

## 5. Consolidated change map (file → change)

| Surface | Change |
|---|---|
| `plugins/takyon/archetypes.py` | NEW — canonical archetype registry (§1.1) |
| `plugins/takyon/core.py` | `businesses.archetype` column + migration; surface-contract `archetype` field (anchored); rail `archetypes` scoping + validation; publish-adapter dispatch; `operator_approvals` table + `require_approval` gate + tools; new rails (`store_iap push deep_links catalog cart orders fulfillment digital_delivery`); new worker contract blocks (mobile, catalog); new tool schemas; `_API_ENV_ALIASES` += `app_store_connect google_play expo printful shopify` |
| `plugins/takyon/cli.py` | `--kind` on `/create` (`_parse_business_start_args`); bootstrap ladder parameterized by registry (§1.6) |
| `plugins/takyon/app_payments.py` | `provider="apple"|"google"` processors; provider-aware payout basis |
| `plugins/takyon/app_orders.py` | NEW — order/fulfillment state machine + dual reconcilers (§3.3) |
| `agent/usage_pricing.py` | entries: eas/expo/maestro/printful (+ later shopify) — fail-closed as today |
| `plugins/takyon/mobile_app_kit/` | NEW — pinned Expo scaffold + mobile runtime client |
| `plugins/takyon/prompts/ceo.md` | one line: archetype comes from the business record; approval-blocked = record + continue (posture already compatible) |
| `skills/takyon/takyon-mobile-product/`, `takyon-commerce/` | NEW skills from SKILL-TEMPLATE with full frontmatter gating; sync + `--restore` verification per CLAUDE.md step 7 |
| `harness/settings.json` | `/approve`, `/deny` (p0_control); `/create` usage string gains `--kind` |
| `web/src/litebulb/product/CompanyTab.tsx`, `ui-tui/src/components/prompts.tsx` | approval affordance + per-archetype status pills (store review state, order counts) |
| Infra | Maestro runner (Mac runner or Maestro Cloud); EAS org; Play org account; ASC org account + API key; Printful account; all keys safebox-side only |

**Rollout safety:** default archetype `web_saas` everywhere; new archetypes flag-gated per operator until their fresh-business E2E gate passes clean twice consecutively (the standing acceptance rule). Existing businesses: zero behavior change, verified by re-publish producing identical build_ids.

---

## 6. Risks and open questions for the operator

**Risks (ranked):**
1. **Apple account blast radius** — one org account carries every business's app; a 4.3 termination kills all of them. Mitigations in §2.2 are policy rails, not guarantees. Per-user Apple accounts are the durable fix and should be designed-for now (per-business credential resolution), built later.
2. **Store review breaks the tight loop** — mitigated by OTA-first iteration; releases become deliberate, approval-gated events. OTA covers fixes, not features (§2.1) — the mitigation is partial by design, and over-using OTA risks Apple's §2.5.2 self-modification enforcement.
2b. **The Swift-extension tax + prebuild fragility** — widgets/Live Activities/App Intents require agent-generated Swift extension targets compiled through EAS; config-plugin/prebuild correctness is where autonomous mobile builds will fail first. Owned by a dedicated Tier-1 E2E canary (§2.1), not discovered in production.
3. **POD IP/trademark bans** — the design screen must be real (deny-list + trademark check against print-file text/marks), or Printful terminates the account.
4. **COGS correctness** — a bug that fulfills before capture, or misprices landed cost, loses real money per order. The dual-reconciler + margin-gate design is specifically shaped to fail closed here; it needs the same test rigor as app_payments.
5. **New infra cost centers** (EAS minutes, Mac/Maestro runners) — all metered through the money gates from day one; nothing ships ungated (standing rule).

**Open questions (answers change scope, not architecture):**
1. Confirm "dough.dough": example DTC brand (plan stands) or literal food product (blocked by the deny rail — needs a human-operated fulfillment story instead)?
2. One platform Apple/Play org account to start — acceptable blast radius for v1?
3. Android-first for the autonomous proof, with iOS as M2 — acceptable ordering?
4. Shopify: required for v1 commerce, or is the native-storefront-first / Shopify-as-variant plan acceptable?
5. Default margin floor for POD (suggest 35% over landed cost) and sample-order policy (suggest: required + approval-gated for the first SKU per business, optional after)?
6. Green-light the two cheap archetypes (digital products, browser extension) as Phase-0 validators?

---

## 7. Effort summary

| Track | Duration | Parallel? |
|---|---|---|
| Phase 0 spine (+ digital_product, browser_extension validators) | ~3 wks | — |
| Mobile M1 Android → M2 iOS → M3 IAP/OTA | ~6-8 wks | M-track ∥ P-track after Phase 0 |
| Commerce P1 sandbox → P2 live POD → P3 Shopify | ~7-9 wks | ∥ |
| Total wall-clock with parallel tracks | **~10-12 wks** to both headline archetypes live | |

---

## 8. Appendix — Integration provisioning ledger (verified 2026-07-01)

Answer to "is this zero-shottable?": **the code is; the accounts aren't.** Split by rail:

**Zero new integrations needed (buildable today on existing credentials):** Phase 0 spine (registry, archetype column, approval rail, publish adapters — pure code, locally E2E-testable), `digital_product` archetype (existing Stripe + R2 + media rail), commerce order state machine + Stripe test-mode sandbox E2E, all skills/tools/scaffolds.

**One-time human provisioning pass (platform-level, not per-business):**

| Provider | Cost | Human-only (one-time) | Human-only (RECURRING — fleet-stall risks) | Headless artifact → safebox alias | Needed at |
|---|---|---|---|---|---|
| Apple Developer (org) | $99/yr | D-U-N-S + legal-entity verification, binding-authority check, 2FA, 3-5+ biz days; Paid Apps Agreement + banking/tax before IAP | **Paid Apps Agreement re-acceptance on every Apple terms update (hard-blocks new apps/IAP account-wide)**; annual renewal; 2FA recovery | ASC API key `.p8` + Key ID + Issuer ID (downloadable ONCE) → `app_store_connect` | Mobile M2 |
| Google Play (org) | $25 once | D-U-N-S org verification (~1 wk); merchant profile + bank + W-9/W-8BEN for IAP; **per-app: create app + first AAB via Console (no `apps.create` API)** | Keep payments profile in sync with D&B (mismatch = suspension); periodic re-verification (2026 identity mandate) | GCP service-account JSON → `google_play` | Mobile M1 |
| Expo/EAS | Free tier 30 builds/mo (tight) → Starter $19/mo | Email signup only; iOS credential generation is headless given the Admin-level ASC key | None | `EXPO_TOKEN` robot token → `expo` | Mobile M0/M1 |
| FCM (Android push transport only — **not** a Firebase-stack dependency; Supabase stays the auth/identity spine) | $0 | Enable FCM on the **same GCP project** already created for the Play service account; one click to download the SA JSON. iOS push needs no Firebase (APNs via the Apple account, EAS-generated). Deferrable: `push` rail is per-business optional | None | FCM SA JSON → `fcm` | Mobile M3 |
| RevenueCat (recommended for `store_iap`) | Free < $2.5k/mo tracked per app; then 1% | Email signup; paste existing ASC/Play creds | None | API key → `revenuecat` | Mobile M3 |
| Maestro | Local runner free (no account); Cloud $250/device/mo — skip | None locally. **Infra note: iOS simulators need a macOS runner** (GH Actions macOS or a Mac mini); Android emulators run on Linux CI | None | n/a | Mobile M0 |
| Printful | $0 signup, $0 API | Email signup (minutes); **one card-on-file before first confirmed order** (draft orders need nothing — fail-closed by Printful itself) | None | Private API token → `printful` | Commerce P1 (sandbox: token only) |
| Stripe Tax | 0.5% of taxed volume | Toggle = one API param on existing Stripe key. **Tax REGISTRATION per jurisdiction is human/legal and recurs with nexus** — Stripe only calculates + alerts | Registrations, filings, remittance (or Tax Complete/partner) | existing `stripe` | Commerce P2 |
| Chrome Web Store | $5 once | 2FA, EEA trader declaration w/ SMS-verified **publicly listed** phone; one-time service-account linkage | **20-published-items cap per account** (increase = human request); repetitive-extension spam policy | GCP service-account JSON → `chrome_web_store` | Validator archetype |
| Shopify | Partner free; live store $39/mo | Dev stores: fully API-creatable, $0, test-only. Live store: plan + card + **Shopify Payments KYC (bank/tax) per store** + per-store app-install click | Periodic partner verification | per-store Admin API token → `shopify` | Commerce P3 only |

**Steady-state autonomy after provisioning:** web/digital — fully zero-shot (unchanged today). Commerce (POD) — fully zero-shot including fulfillment (card on file; drafts→confirm gated by payment capture). Android — zero-shot per release after a one-time per-app Console bootstrap. iOS — zero-shot to TestFlight; production = the one designed approval click. The recurring human surface is deliberately tiny and must be *monitored, not assumed*: plan adds a platform **account-health check** (Apple agreements current, Play profile in sync, memberships not lapsing, EAS quota) to pulse/doctor with push notification.

**Sequencing note:** Apple org enrollment (and D-U-N-S if missing) is the longest pole (up to ~2-4 wks worst case) — kick it off at Phase 0 start, in parallel with all code work, so it never blocks M2.
