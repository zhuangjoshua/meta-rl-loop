-- 0060_safebox_shopify_plan_recompose_grants.sql
--
-- UC4 Shopify shop/update rail (modularization plan §2.7 Stage 5): the Safebox verifies the
-- Shopify webhook HMAC (the shared secret lives only there) and, on a verified plan change,
-- re-derives each affected composed plan and mints the NEXT plan_key version through
-- app_entitlements.upsert_plan_from_composition — on the Safebox's own DB role, in the same
-- signed-event path (mirroring how 0054 gave the Safebox the Stripe reconciliation writes it
-- performs). webhook_events write authority already exists (0044); the plan-catalog write is the
-- one missing grant.
--
-- GRANTS ONLY — no new tables, columns, or dedup stores. Additive and idempotent (grants are
-- repeatable). App roles keep their existing read-only posture on the plan catalog; the
-- grandfather invariant is enforced in code (upsert_plan_policy), which this role now reaches.

grant insert, update on app_plan_policies
    to takyon_safebox_authority;
