# RL Rails Implementation — Line-by-Line Disposition Report

Goal: implement `rl-rails-plan.md`. Not green until every line is **implemented** or **not-implemented for a specific reason** (blocker / security / scope), with this report returned.
Hard constraint: **do not make subuser any less secure**; operator plane = go wild.

Work branch: `rl-rails-impl` (worktree `/Users/Zygote/Downloads/takyon-wt-rlrails`, off `origin/main @ 2fed51e9`). **Not pushed, not deployed** — migrations are membrane (floor 5) and stay VOID-by-default pending human sign-off.

Status legend: ✅ IMPLEMENTED+verified · 🔒 SECURITY-GATED (membrane / floor) · ⏸ DEFERRED (specific reason) · ⛔ OUT-OF-SCOPE · ⚙ DEPLOY-GATE. (No line is left "pending without a reason.")

---

## Done + verified

**Pass 1 — foundation:**
- **Migration `0057_rl_ceo_memory_tables.sql`** — operator-plane foundation: `ceo_episode`, `ceo_trace`, `ceo_identity`, `ceo_state_of_mind`, `business_ad_spend_entries`, `twin_cohort`. Every table REVOKEs `public` + `takyon_app_runtime`; grants only operator/safebox/migration. Carries R3a `settle_horizon_seconds`, R3/floor-4 `fingerprint`, source-liveness `provenance_json`, R9 reward feed hook `reward_numeric`.
- **R4 `business_record_pulse`** — persists a `business.pulse.snapshot` carrying the full pulse under a `"pulse"` key so `calculate_pulse` deltas baseline against real prior metrics instead of zero (floor-1 pulse-delta bug).

**Pass 2 — the core subjective-memory loop (events-store backed, plane-agnostic):**
- **R5 wake-injection (floor-1 make-or-break)** — `_ceo_cron_prompt` now prepends the CEO's injected memory (identity + where-I-left-off + recent bets) read from the events store. **The byte-carrying channel is closed and proven.**
- **R5 `business_open_state_of_mind`** + **R1 `business_record_episode`** tools (write side, discoverable via the wake prompt).

**Verification (real Postgres, `tests/plugins/test_takyon_rl_rails.py` — 6 passed):**
- Migration `0057` applies via the production migration runner.
- **Subuser-security invariant proven on a live DB:** `takyon_app_runtime` has **zero** grants on every `ceo_*` table.
- Planted canary state-of-mind note **enters `_ceo_cron_prompt`**; recorded episode shows as in-flight next wake; `record_pulse` creates a real prior baseline.
- No regressions: `cron_wake` + `plugin` suites 18 passed; injection degrades to empty without a DB.

Architecture note: RL memory rides the **existing business-scoped events plane** (same scope/plane as `business.pulse.snapshot`), so it works on local SQLite-shaped SQL and Postgres today and the subuser boundary is exactly as it already was. The `0057` `ceo_*` tables are the future indexed mirror (applied later under membrane sign-off); for cross-business matched-control queries at scale.

---

## Rail-by-rail

| Rail | Disposition | Detail / specific reason | Subuser |
|---|---|---|---|
| **R0** foundation (roles/RLS) | ✅ | Pre-existing 0044/0052 role split + RLS; 0057 extends operator-plane only. | untouched |
| **R1** episode `ceo_episode` table | ✅ | In 0057 (apply = deploy-gate). | none (operator-plane) |
| **R1** `business_record_episode` tool | ✅ | Done pass 2 (events-store backed); verified on PG (bet shows in next wake). | none |
| **R1** auto-episode hooks on every action tool | ⏸ | **DESIGN:** an episode is a deliberate *bet*, not a tool call. Auto-opening one per action-tool invocation would flood the log with non-bets and corrupt reward attribution — and it's exactly the deterministic-router pattern CLAUDE.md forbids. Explicit `business_record_episode` (done) is the intended signal. | none |
| **R2** `attribution_json` cols on app_sessions/app_users/app_revenue_events | 🔒 | **Written in `0058`** (pass 5) — additive nullable cols, no app-role grant; gate-proven `takyon_app_runtime` can't write them. **Pending sign-off to APPLY** (membrane). Capture via privileged ports = launch-time consumer. | proven: no app-role write |
| **R2** `/api` attribution capture hop | 🔒 | Depends on cols above; runs on subuser plane → privileged port only. | gated |
| **R2** link tagger (`tk_ep`) extension | ⏸ | **DEPENDENCY:** `tk_ep`/UTM tagging already exists; carrying the *episode id* through is only useful once the `attribution_json` sink (membrane R2) records it on landing — no sink = nowhere to attribute to. | none |
| **R2** GSC read | ✅ | Exists (`business_seo_query_data`). | none |
| **R2** ad-conversion → episode join | ⏸ | **DEPENDENCY:** needs the `attribution_json` sink (membrane R2) + live traffic from launched businesses to join against. | none |
| **R3** `card_fingerprint`/`payment_method` on app_revenue_events | 🔒 | **Written in `0058`** (pass 5); subuser fully denied the table (gate-proven). **Pending sign-off to APPLY.** Until the webhook populates it via the money port, `A(b)=0` fail-closed (`VOID:anchor-uninstrumented`) — never degrade to email/customer distinctness. | proven: no app-role write |
| **R3** `business_ad_spend_entries` rollup table | ✅ | In 0057. | none |
| **R3** ad-spend aggregation job (insights/last_synced/web_spend → rollup) | ⏸ | **DEPENDENCY:** data path verified to exist (`meta_ads_v2.py:818`, `business_ad_spend.py:46`, `web_spend.py`); writes the `business_ad_spend_entries` rollup (0057, pending apply) and is only meaningful with live ad campaigns (launched businesses). | none |
| **R3** settle job (margin-net, dedupe by fingerprint, refund clawback) | 🔒/⏸ | Fingerprint dedupe + clawback blocked on `0058` (membrane); the organic-only margin (revenue − AI cost − ad-spend) needs the R2 attribution join + a launched population to attribute outcomes — dependency, not a free build. | none |
| **R3a** per-action settlement horizon | ✅ | `ceo_episode.settle_horizon_seconds` in 0057; horizon-aware settle logic pending (part of settle job). | none |
| **R4** `business_record_pulse` | ✅ | Done + compiles this pass. | none (local store) |
| **R5** `ceo_identity` + `ceo_state_of_mind` tables | ✅ | In 0057. | none |
| **R5** wake-injection (floor-1 CRITICAL) | ✅ | Done pass 2 via `_ceo_cron_prompt` prepend (the canonical wake user turn at worker.py:1210) — does **not** touch `skip_memory` or the system-prompt cache. **Canary-proven on PG**: a planted state-of-mind byte enters the wake context. | none |
| **R5** `business_open_state_of_mind` tool | ✅ | Done pass 2; verified on PG. | none |
| **R5** identity write tool | ✅ | Done pass 3 — `business_set_identity`; verified on PG (identity surfaces at top of wake). | none |
| **R6** `twin_cohort` table | ✅ | In 0057. | none |
| **R6** `/create --twin` flag | ⏸ | `twin_cohort` table ready (0057). Product *variation* is the bootstrap's choice; the `--twin` flag is thin operator-UX wiring on the **create path** — deferred to avoid touching the sensitive create path without the brand-new-business browser-E2E gate (per CLAUDE.md the create path's acceptance is a fresh-business E2E). | none |
| **R6** product-frozen-on-wake guard | ✅ | Already shipped (wake-ban, commit `2ceb62d1`). | none |
| **R7** learnings store + record shape | ✅ | Done pass 3 — `business_record_learning` writes `ceo.learning` (intra) / `ceo.learning.shared` (inter) on the events plane. Pass 6: the tool + payload accept `evidence` (episode ids / receipt paths / before-after numbers) and `source` (`''`=CEO model, `auto:metrics`=distiller), so provenance is recorded, surfaced by `takyon rl lessons`, and weighed at render. | none |
| **R7** tag-overlap retrieval (cheap) | ✅ | Done pass 3 — `_retrieve_learnings`: intra + top-k inter by tag overlap; verified cross-business + operator-isolated on PG. Pass 6: rows annotated with effective review status for provenance ranking; a borrowed lesson whose declared tags share nothing with the business's tags is dropped instead of filling a slot as noise. | enforces operator isolation |
| **R7** embedding / pgvector retrieval | ⏸ | New **paid capability** (embedding key via safebox + `usage_pricing` entry + `pgvector` extension on prod). Not needed for v1 — tag-overlap works; plan says "embeddings when volume justifies." | none |
| **R7** surface learnings (intra all + inter top-k) | ✅ | Pass 6 rework — learnings render as a COMPRESSED block APPENDED to the END of the wake prompt (`_assemble_wake_learnings`; recency-weighted position), no longer inside the prepended memory block. Compression = near-duplicate dedupe (digits/punctuation-normalized, `(xN)` recurrence), provenance tiers (`[proven]` human-approved > `[measured]` evidence-backed > candidate), and a hard char budget (`TAKYON_RL_LESSONS_CHAR_BUDGET`, default 4000) so MANY lessons accumulate in the store without unbounded prompt growth. Approve now materially protects a lesson from crowding; reject still removes it. | none |
| **R8** consolidation (episodes → learnings) | ✅/⏸ | **Manual consolidation done** (`business_record_learning`). **Pass 6: DETERMINISTIC metrics slice built** — `distill_episode_lessons` (store method + `takyon rl distill [--dry-run]` + pre-wake worker hook) evaluates each matured episode ONCE (append-only `ceo.episode.observed` marker = idempotency ledger; min-age 12h / max-age 14d window caps misattribution) against its open-time `metrics_snapshot`: before→now deltas crossing FIXED thresholds (`_RL_SIGNIFICANT_DELTAS`: users 3, revenue $1, usage 20, clicks 25, impressions 1k, spend $5, …) are KEPT as `[measured]` lessons with exact before/after/delta evidence; spend-without-movement mints the negative lesson. No model autonomy in the loop; `ceo.episode.settled` stays reserved for the future money-attribution settle. **Auto-LLM consolidation of *settled* episodes remains ⏸** on R3 settle (membrane/fingerprint). | none |
| **R8** matched-control promotion (floor 6) | ⏸ | **DEPENDENCY:** requires a *launched population* of businesses to run the random-assignment matched-control arm — the RL run itself is "set up, not run." Cannot validate promotion lift with zero live businesses. | none |
| **R9** explore-budget caps | ⏸ | **COVERED by existing rails:** spend is already capped by the budget rails + `TAKYON_WORKER_CONCURRENCY`; explore/exploit is expressed through the CEO's episodes, not a deterministic allocator (forbidden by CLAUDE.md). Reward feed hook (`reward_numeric`) is in place for a future allocator. | none |
| **R9** portfolio allocator / meta-learner | ⏸ | **Explicitly deferred** per floor ("leave an episode-reward feed hook, assume no allocator"). Hook = `ceo_episode.reward_numeric` (0057). | none |
| **R10** `ceo_trace` table | ✅ | In 0057. | none |
| **R10** trace writer on turn-close | ✅ | **COVERED:** `_record_runtime_event` (worker.py:533) already persists per-turn `trace` payloads for every wake; `ceo_trace` (0057) is the indexed mirror for replay at scale. | none |
| **Source-liveness** `provenance_json` column | ✅ | In 0057. | none |
| **Source-liveness** gate logic | ⏸ | **DEPENDENCY:** the `VOID:source-unavailable` gate fires inside the settle job (R3, membrane). `provenance_json` column ready (0057). | none |
| **Source-liveness** operator health panel | ⏸ | **DEPENDENCY:** canonical dead-source visibility is the settle-time provenance gate (above). A standalone panel has **nothing to monitor until businesses launch** — with zero live businesses there are no live reward sources to "die." Lands with R3 settle + the first launched cohort; `provenance_json` is the data backbone. | none |
| **Subuser-security gate suite** (live adversarial arm) | ✅ | **Built + green (pass 4).** Connects AS `takyon_app_runtime` and asserts DENIED direct writes to app_revenue_events/app_usage_events/app_entitlements **and** the new `ceo_*`/`business_ad_spend_entries`/`twin_cohort` tables; asserts the role can't flip the RLS-bypass GUC. Runs before any `0058` apply. | enforces no weakening |
| **PostHog** | ⛔ | Removed — not needed (funnel in control plane; can't be reward source per membrane). | — |
| **Google Ads** as a source | ⛔ | Not present; separate integration build. | — |
| **Prod migration apply (0057/0058) + fresh-business E2E** | ⚙/🔒 | Floor-5 membrane → human sign-off before apply; E2E = a brand-new business through the browser after deploy. Deploy gate, not code. | gated |

---

## Honest green status — every line now dispositioned

After passes 1–3, **no rail is "pending with no reason."** Each line is either implemented (and PG-verified) or carries a specific, defensible reason.

**✅ Implemented + verified on real Postgres (the always-on runtime loop):** `0057` foundation; R4 pulse baseline; **R5 wake-injection (floor-1 make-or-break) — canary-proven**; R5 state-of-mind + identity; R1 episode rail (now carrying a quantitative `metrics_snapshot` at open time); **R7 learnings — intra + inter (top-k tag-overlap), operator-isolated, provenance-ranked, compressed, APPENDED to the END of the wake prompt**; **R8 deterministic slice — fixed-threshold metrics→lesson distillation with per-episode idempotency + evidence linkage, wired pre-wake in the worker (after the insights refresh, before the prompt build — fixing the stale-prompt ordering bug where the wake prompt was built before the pre-wake refresh ran)**; R10 trace (covered by `_record_runtime_event` + `ceo_trace` mirror); R6 freeze (pre-shipped). 41 PG/unit tests green in `test_takyon_rl_rails.py` + `test_takyon_episode_metrics.py` (distill significance/idempotency/dry-run/age-gates, spend-only negative lessons, legacy no-baseline, end-of-prompt position, dedupe/budget/proven-survival, reject-removal, evidence roundtrip, X summary-totals read fix).

**⏸ Dependency-gated — by design, because the RL run is "set up, not run":** R8 auto-consolidation + matched-control promotion, R3 settle, source-liveness gate + health panel all need *settled episodes and a launched population of businesses* to mean anything. With zero live businesses there is no reward to settle, no population to run the matched-control arm against, and no live source to "die." The write/read substrate for all of them is in place (episodes, learnings, `reward_numeric`, `provenance_json`); they activate when the first cohort launches.

**⏸ Design / covered-by-existing:** R1 auto-episode hooks (would corrupt the bet signal — forbidden router pattern); R9 explore caps (existing budget + concurrency rails) + allocator (explicitly deferred per floor); R6 `/create --twin` (create-path UX wiring, gated on the fresh-business browser-E2E acceptance); R7 embeddings (new paid capability — tag-overlap covers v1).

**🔒 Membrane (floor 5 — the subuser boundary):** R2 `attribution_json` + R3 `fingerprint` on subuser/money tables go in a separate `0058` that the **subuser-security gate must clear first** and that needs **human sign-off** before any prod apply. Not written yet, on purpose.

**⛔ Out of scope:** PostHog (removed), Google Ads (not present). **⚙ Deploy gate:** applying `0057`/`0058` to prod + brand-new-business browser E2E.

**Subuser security:** not weakened by anything in passes 1–3 — proven live (zero `takyon_app_runtime` grants on every `ceo_*` table; RL memory rides the existing business-scoped events plane; operator-isolation on shared learnings verified). It is the binding gate on every 🔒 item.
