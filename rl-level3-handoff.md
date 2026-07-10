# RL Level-3 Handoff — Real-CEO ROAS Feedback Acceptance

> **2026-07-09 FINAL: Level 3 PASSED.** Real-model acceptance ran with `gpt-5.1`
> (`--provider openai`, direct transport) — both shuffle directions, `late_share_on_best 1.0`:
> seed 41 (image wins): `[image ×5]`; seed 77 (video wins): `[image, video, video]` — the
> course-correction run: blind image default → measured 0.91 ROAS → switched to video (4.26,
> "reuses the proven video creative") and stayed. Running it surfaced a third rig bug, fixed:
> the plugin loader imports plugins/takyon a SECOND time as `takyon_plugins.takyon.*`, so all
> business_* tool handlers dispatch through TWIN module objects — the rig now patches both
> identities (plus an OpenAI direct-transport lane and a provider-registry bypass).
> Slices 1–2 pushed earlier (`c47109eb`, `ea8c853a`) and deployed to the operator VPS;
> slices 3–4 (production half + rig) pushed after the pass. Remaining: VPS deploy of the
> production half, shared-pixel custom-conversion hardening, sub-user host unreachable.

**Original state (historical): built and plumbing-verified, UNCOMMITTED; blocked on a
model API key.** This doc is self-contained — a fresh session can finish the job from here.

## The idea (operator's process)

Per business, one skill at a time: run the skill (meta ads / seo) → next wake compute ROAS →
append the ENTIRE process + metrics to the skill's per-business run history → the skill reads
it before its next run → ROAS improves. The test ladder proving it:

- **Level 1** — scripted world+policy, real store/distiller rails (`plugins/takyon/rl_sim.py`).
  ✅ MERGED to main (`3746b304..c58cc711`, 2026-07-08).
- **Level 2** — a real Claude reading the same feedback, blinded. ✅ demonstrated in-session.
- **Level 3 (this handoff)** — the REAL CEO wake executing the REAL meta-ads skill, providers
  stubbed, ROAS injected from a sealed truth. Scored: do its real creative choices converge
  on the secret winner?

## What was built (all uncommitted, in the working tree)

### Production half (ships on deploy, independent of the rig)
- **Assembler** — `TakyonStore.assemble_roas_run_history` + `_compose_roas_history_entry`
  (`plugins/takyon/core.py`): for every NEW insights-sync receipt on a meta campaign, append
  one truthful entry (launch-plan process: kind/headline/copy/CTA/budget + receipt results:
  purchases/attributed revenue/spend/ROAS) to `metrics/roas/meta.md`. Idempotent per sync
  (entries embed `sync <campaign>/<file>`), best-effort, plan-less degrade, `ROAS n/a` when
  no attributed revenue.
- **Worker hook** — `plugins/takyon/worker.py` pre-wake: insights refresh → distill →
  **assemble_roas_run_history** → build prompt.
- **Skill instruction** — `skills/takyon/takyon-meta-ads-v2/SKILL.md` Procedure step 1: read
  `metrics/roas/meta.md`, favor the highest-ROAS approaches.
- **Tests** — `tests/plugins/test_takyon_roas_history.py` (6, green).

### The rig — `plugins/takyon/rl_wake_rig.py`
Per wake: rig allocates channel budget (real ledger) → REAL `worker.ceo_wake_handler` fires →
real agent turn loads the real skill → writes a real ad, chooses **video vs image** → real
launch tool (credits reserve/commit, plan.json, receipts, ad-spend policy registry) → the
Meta call hits a recording stub (`RigSafebox`, no network/money) → rig injects a sync receipt
with ROAS from the **sealed** truth (`RigWorld`: one kind ~4.6×, other ~0.8×, seed-shuffled) →
real assembler writes the history entry the next wake reads. Score at end:
`kinds_in_order`, `sealed_truth` (revealed), `late_share_on_best` (fraction of late wakes on
the true winner; 1.0 = loop steers behavior, ~0.5 = history ignored), + `history_file` path.

Rig environment details (all restored on exit):
- temp `TAKYON_HOME` with repo `skills/` copied in + model config;
  `TAKYON_ALLOW_LEGACY_DB_ROLES=1`; local storage backend
- `core.TakyonStore` subclassed: DSN defaulted to the rig DB; **`_sync_business_workspace_cache`
  no-op'd** (head-revision re-materialization clobbers the rig's out-of-band writes — observed)
- safebox: `_local_authority_enabled -> True` (provisioning/billing run on the rig DB) +
  `RigSafebox` patched over the meta_* functions meta_ads_v2 calls
- **workspace ctx override**: `turn_runtime._business_workspace_execution_context` yields the
  rig's TAKYON_HOME — the real mount materializes from the DB head revision and would give the
  CEO an EMPTY tree (verified by dress rehearsal, then fixed)
- seeding: `provision_user_on_first_login` (billing account needed by grant_allowance),
  `grant_credits` 500k, per-wake meta channel-budget top-up (`metrics/channel-credit-budgets.json`,
  40k×wake — one launch reserves the WHOLE remaining channel budget by design), dummy assets
  (`product/ugc-ads/shared/ad.mp4` + `thumbnail.png`, `product/static-ads/shared/hero.png`),
  goal text pinning the task to meta-ads traffic campaigns (`work_focus=marketing`)

### Two production bugs the rig found — ✅ FIXED AND PUSHED (2026-07-08)
1. `ea8c853a` — **`meta_ads_v2.py`**: live-mode launch crashed writing its receipt (raw
   datetimes from the real `_derive_ad_spend_schedule`; the test harness stub returned
   strings, hiding it). ISO at source.
2. `c47109eb` — **main clobber restored**: commit `8a0eeb2a` ("coding worker: honor the
   configured model") had deleted **437 lines of core.py** from a stale checkout — the pixel
   revenue aggregator (`purchase_*` fields, `_meta_first_action_metric`,
   `_META_PURCHASE_ACTION_TYPES`) and `_surface_enable_meta_pixel`. Restored verbatim from
   `8a0eeb2a^`; symbols verified present on the origin tip. (`_shopify_catalog_commit` also
   deleted, no callers — intentionally left out.) Third stale-base clobber recently (see
   80ef2969 / 4a1cc878) — the offending checkout needs a rebase before its next push.

## Verification status
- Fake-CEO mode (scripted actor + everything else real): **converged both shuffle directions,
  `late_share_on_best` 1.0** (seeds 41/77).
- Dress rehearsal (real `ceo_wake_handler` end to end, model call swapped for a probe):
  history file + assets **visible in the wake's workspace** after the ctx fix; toolsets correct.
  Note: the business goal is NOT in the wake prompt text — the CEO gets it via
  `business_read_business` during the turn (normal production path; a compliance question the
  real run answers).
- Sweep: **98 passed** (roas_history 6, rl_sim 13, rl_rails 41, episode_metrics,
  meta_insights_revenue 7 restored, meta_ads_v2 22, shopify rail 5). Known pre-existing: 2
  wake-handler workspace unit tests fail at origin/main (legacy sync model; tracked separately).

## TO FINISH

### 1. The real-model acceptance run (the only remaining step)
Blocked on a credential — this machine has NONE (verified: session transport 401s for child
processes, `~/.takyon/.env` empty, no workspace `secrets/.env`, no shell-profile keys, no
safebox lane configured). Operator: create a key at console.anthropic.com and either run:
```bash
docker start rlsim-pg   # postgres:16, port 55432, password postgres; db rlsim_demo (migrated)
cd ~/Documents/fourmanifold/takyon-workspace/hermes-agent-main && source .venv/bin/activate
export TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS=1
export TAKYON_TEST_PG_DSN="postgres://postgres:postgres@127.0.0.1:55432/rlsim_demo"
export ANTHROPIC_API_KEY="sk-ant-..."
python -m plugins.takyon.rl_wake_rig --wakes 5 --model claude-sonnet-5
```
…or drop `ANTHROPIC_API_KEY=...` into `~/.takyon/.env` and have the session source it and run.
Cost ~$1–3 (`--max-turns 24` caps each wake). Interpretation: `late_share_on_best` ≥ ~0.75 with
a sensible `kinds_in_order` = the loop steers real behavior (PASS). ~0.5 / history-ignoring =
a genuine finding: fix the skill instruction wording, not the plumbing. Read the printed
`history_file` to audit what the CEO saw before each choice. Watch for real-mode-only wrinkles:
the CEO not calling the launch tool at all (goal/prompt tuning), or `--transport gateway` vs
`direct` (direct is default; builds a plain client from the env key).

### 2. Commit + push (after the operator tests to their satisfaction)
Slices 1–2 (clobber restoration `c47109eb`, datetime fix `ea8c853a`) are PUSHED. Remaining:
3. `rl roas: assembler + pre-wake hook + skill read instruction + tests` (production half)
4. `rl rig: level-3 real-wake acceptance harness` (rl_wake_rig.py + handoff doc)
Then the standing follow-ups: per-business custom-conversion revenue (shared-pixel hardening),
eventual VPS deploy chain (push ≠ deploy; nothing is deployed).

## Prior context (for orientation)
- Level-1 env + RL loop rework + pixel→RL wiring: merged to main 2026-07-08.
- Memory file: `~/.claude/projects/.../memory/rl-sim-test-environment.md` (detailed log).
- Related reports in repo root: `rl-rails-impl-report.md`.
- Testing contract: operator names the ONE skill under test; the agent never selects skills
  in test environments. ROAS (harness definition) = profit ÷ (creation + spend); production
  receipt ROAS = attributed revenue ÷ spend (meta only).
