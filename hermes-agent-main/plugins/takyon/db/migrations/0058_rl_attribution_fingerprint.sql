-- 0058_rl_attribution_fingerprint.sql
-- RL rails R2 (attribution_json) + R3 (card_fingerprint / payment_method).
--
-- ===========================================================================================
-- MEMBRANE FILE — touches subuser/money tables. VOID-by-default PENDING HUMAN SIGN-OFF (floor 5).
-- Do NOT apply to the shared control plane without operator review + a green subuser-security gate.
-- ===========================================================================================
--
-- SUBUSER-SECURITY INVARIANT ("do not make subuser any less secure"):
-- These are ADDITIVE, NULLABLE columns. They add NO grant to the subuser role
-- (takyon_app_runtime), and they cannot widen it, because after the existing migrations the
-- subuser role already has NO direct write path to any of these tables:
--   * app_revenue_events  — 0041 revokes insert/update/delete, 0047 revokes select (fully denied).
--   * app_users / app_sessions — 0045 revokes direct select; all writes go through the
--     SECURITY DEFINER identity ports (which run as their privileged owner, not as takyon_app).
-- New columns inherit that denial. They are POPULATED by the existing privileged ports — the
-- money-write definer function (fingerprint/attribution on app_revenue_events) and the identity
-- ports (attribution on app_users/app_sessions) — which are extended in a SEPARATE signed-off
-- step, not here. This migration only adds the storage. The subuser-security gate
-- (tests/plugins/test_takyon_rl_rails.py) asserts takyon_app_runtime still cannot write them.

begin;

-- R3: reward anchor must dedupe revenue by card fingerprint + survive refunds (floor 4).
-- Until the webhook populates these via the money port, live A(b) stays fail-closed
-- (VOID:anchor-uninstrumented) — never degrade to email/customer_id distinctness.
alter table public.app_revenue_events add column if not exists card_fingerprint text;
alter table public.app_revenue_events add column if not exists payment_method text;
alter table public.app_revenue_events add column if not exists attribution_json jsonb;

-- R2: attribution capture sink. Written only via the privileged identity ports at /api signup;
-- the subuser runtime never writes these directly.
alter table public.app_users    add column if not exists attribution_json jsonb;
alter table public.app_sessions  add column if not exists attribution_json jsonb;

commit;
