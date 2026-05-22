UPDATE workflow_jobs
SET status = 'cancelled',
    error = COALESCE(error, 'Legacy foundation startup lane removed; Hermes CEO must choose the next skill from business context.'),
    completed_at = COALESCE(completed_at, now()),
    updated_at = now()
WHERE workflow_id = 'foundation'
  AND status IN ('queued', 'running');

UPDATE workflow_jobs
SET lane = 'ceo',
    result = result || jsonb_build_object('legacy_lane', 'foundation'),
    updated_at = now()
WHERE lane = 'foundation';

ALTER TABLE workflow_jobs
  ALTER COLUMN lane SET DEFAULT 'ceo';

ALTER TABLE workflow_jobs DROP CONSTRAINT IF EXISTS workflow_jobs_lane_check;
ALTER TABLE workflow_jobs
  ADD CONSTRAINT workflow_jobs_lane_check
  CHECK (lane IN (
    'website',
    'product_backend',
    'product_ui',
    'generated_app_auth',
    'generated_app_users_entitlements',
    'stripe',
    'ai_gateway',
    'x_social',
    'meta_seedance',
    'community',
    'outreach',
    'ceo',
    'goal'
  ));
