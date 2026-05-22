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

INSERT INTO addons (addon_key, title, status)
VALUES ('goals', 'Persistent Goals', 'available')
ON CONFLICT (addon_key) DO NOTHING;

INSERT INTO action_policies (policy_key, title, default_requires_approval, metadata)
VALUES
  (
    'goal.get_first_customer',
    'Run Get First Customer Goal',
    false,
    '{"success_receipt":"company_revenue_events","requires_real_payment":true}'::jsonb
  )
ON CONFLICT (policy_key) DO NOTHING;
