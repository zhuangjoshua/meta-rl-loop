-- 0072: backfill the gateway-required ai_generate baseline into seeded plan features.
--
-- The AI gateway's default feature for a product ctx.generate call is 'ai_generate'
-- (ai_gateway._feature_allowed, strict against metadata.features). Plan seeding was
-- CEO-free-form: the 2026-07-04 batch wrote product-domain feature names only
-- ({deck_generate, prompt_scoring, investor_search, ...}), so PAYING users 403'd
-- feature_not_in_plan on every AI action (proven on magicslides /actions/generate-deck,
-- peekaboo-intake, angelmatch-2). Forward writes are fixed at the one plan-write funnel
-- (app_entitlements.upsert_plan_policy -> _ensure_baseline_gateway_features); this
-- backfills rows seeded before that existed.
--
-- Idempotent. An EXPLICIT "ai_generate": false is preserved (key exists -> untouched):
-- that is a deliberate no-AI tier, not the strand.

-- metadata entirely NULL -> seed the baseline features object.
UPDATE app_plan_policies
SET metadata = '{"features": {"ai_generate": true}}'::jsonb,
    updated_at = now()
WHERE metadata IS NULL;

-- features absent, or an object WITHOUT the ai_generate key -> merge the baseline key.
UPDATE app_plan_policies
SET metadata = jsonb_set(
        metadata,
        '{features}',
        COALESCE(
            CASE
                WHEN jsonb_typeof(metadata -> 'features') = 'object' THEN metadata -> 'features'
                ELSE '{}'::jsonb
            END,
            '{}'::jsonb
        ) || '{"ai_generate": true}'::jsonb,
        true
    ),
    updated_at = now()
WHERE metadata IS NOT NULL
  AND (metadata -> 'features' IS NULL OR jsonb_typeof(metadata -> 'features') <> 'array')
  AND NOT COALESCE(metadata -> 'features' ? 'ai_generate', false);

-- features as a legacy ARRAY of names without 'ai_generate' -> append the name.
UPDATE app_plan_policies
SET metadata = jsonb_set(
        metadata,
        '{features}',
        (metadata -> 'features') || '["ai_generate"]'::jsonb,
        true
    ),
    updated_at = now()
WHERE metadata IS NOT NULL
  AND jsonb_typeof(metadata -> 'features') = 'array'
  AND NOT (metadata -> 'features' @> '["ai_generate"]'::jsonb);
