-- App AI gateway allowlists are now default-deny in code: a paid entitlement is necessary but not
-- sufficient unless the plan explicitly allows the feature/model. Existing plans predate that rule
-- and previously received implicit access, so make that historical access explicit without
-- overwriting any custom plan metadata already present.

update app_plan_policies
set metadata =
    metadata
    || case
        when metadata ? 'features' then '{}'::jsonb
        else '{"features":{"ai_generate":true,"web_search":true}}'::jsonb
       end
    || case
        when metadata ? 'model_allowlist' or metadata ? 'models' then '{}'::jsonb
        else '{"model_allowlist":["claude-sonnet-4-6"]}'::jsonb
       end,
    updated_at = now()
where not (
    metadata ? 'features'
    and (metadata ? 'model_allowlist' or metadata ? 'models')
);
